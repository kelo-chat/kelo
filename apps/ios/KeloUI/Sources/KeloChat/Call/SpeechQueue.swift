import Foundation
import KeloShared

/// Speaks reply sentences in order (brief §4.8; the same design as the Kotlin SpeechQueue of 2026-11-19).
/// One worker per burst: it starts up to [CallRules.prefetch] fetches ahead, waits for the next one, plays it, and repeats.
/// The worker ends (and `.idle` fires) only when nothing waits AND nothing is being fetched, checked under the same lock `add` takes,
/// so a piece is never left behind (a one-sentence reply whose fetch was in flight was never played before).
public final class SpeechQueue: @unchecked Sendable {
    public enum Event: Sendable {
        case firstPlay
        case firstAudio
        case idle
        case deviceSpoke
        /// Kelo's own voice was expected but its audio failed: the device voice spoke instead.
        case voiceFallback
        /// A piece could not be spoken at all (no device voice, or it failed). Fires once per call.
        case silent
    }

    private enum Playable: Sendable {
        case serverAudio(n: Int, pcm: Data, sampleRate: Int)
        case deviceEngine(n: Int, text: String, lang: String, fallback: Bool)
    }

    private let audio: CallAudio
    private let server: @Sendable (String, String) async -> Data?
    private let device: (any SpeechEngine)?
    private let serverLang: @Sendable (String) async -> String?
    private let appLang: @Sendable () -> String
    private let onLevel: @Sendable (Float) -> Void
    private let onEvent: @Sendable (Event) -> Void
    private let log: @Sendable (String) -> Void

    private let lock = NSLock()
    private var pendingTexts: [String] = []
    /// A worker owns the queue: set by add(), cleared by that worker when idle, or by stop() (under the lock).
    private var running = false
    /// Bumped by stop(): an old worker never touches a newer one's state.
    private var generation = 0
    private var pieceNo = 0
    private var workerTask: Task<Void, Never>?
    private var onceFired: Set<String> = []

    private var _playing = false
    public var playing: Bool { lock.withLock { _playing } }

    public init(
        audio: CallAudio,
        server: @escaping @Sendable (String, String) async -> Data?,
        device: (any SpeechEngine)?,
        serverLang: @escaping @Sendable (String) async -> String?,
        appLang: @escaping @Sendable () -> String,
        onLevel: @escaping @Sendable (Float) -> Void,
        onEvent: @escaping @Sendable (Event) -> Void,
        log: @escaping @Sendable (String) -> Void = { _ in }
    ) {
        self.audio = audio
        self.server = server
        self.device = device
        self.serverLang = serverLang
        self.appLang = appLang
        self.onLevel = onLevel
        self.onEvent = onEvent
        self.log = log
    }

    public func add(_ piece: String) {
        let trimmed = piece.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let start: Int? = lock.withLock { () -> Int? in
            pendingTexts.append(trimmed)
            _playing = true
            if running { return nil }
            running = true
            return generation
        }
        if let gen = start {
            let task = Task { [weak self] in
                guard let self else { return }
                await self.runWorker(gen)
            }
            lock.withLock { workerTask = task }
        }
    }

    public func stop() {
        let task = lock.withLock { () -> Task<Void, Never>? in
            pendingTexts.removeAll()
            _playing = false
            running = false
            generation += 1
            let t = workerTask
            workerTask = nil
            return t
        }
        task?.cancel()
        audio.stopPlayback()
        device?.stop()
    }

    /// True the first time it is asked for: the device-voice events fire once per call.
    private func fireOnce(_ key: String) -> Bool {
        lock.withLock { onceFired.insert(key).inserted }
    }

    private func runWorker(_ gen: Int) async {
        var ahead: [Task<Playable, Never>] = []
        var firstAudio = false
        var firstPlay = false
        var leftIdle = false
        defer {
            ahead.forEach { $0.cancel() }
            // An unexpected end (not idle, not stopped) frees the queue so the next add() starts a worker again.
            if !leftIdle {
                lock.withLock {
                    if gen == generation && running {
                        running = false
                        _playing = false
                    }
                }
            }
        }

        func fill() {
            while ahead.count < CallRules.prefetch {
                let taken: (Int, String)? = lock.withLock {
                    guard gen == generation, !pendingTexts.isEmpty else { return nil }
                    pieceNo += 1
                    return (pieceNo, pendingTexts.removeFirst())
                }
                guard let t = taken else { return }
                ahead.append(Task { await self.fetch(t.0, t.1) })
            }
        }

        while !Task.isCancelled {
            fill()
            guard !ahead.isEmpty else {
                // 0: stopped, 1: more text arrived, 2: idle (nothing waits, nothing loads)
                let outcome: Int = lock.withLock {
                    if gen != generation { return 0 }
                    if !pendingTexts.isEmpty { return 1 }
                    running = false
                    _playing = false
                    leftIdle = true
                    return 2
                }
                if outcome == 1 { continue }
                if outcome == 2 {
                    log("queue idle")
                    onEvent(.idle)
                }
                return
            }

            let first = ahead.removeFirst()
            let item = await withTaskCancellationHandler {
                await first.value
            } onCancel: {
                first.cancel()
            }
            if Task.isCancelled { return }

            if !firstAudio {
                firstAudio = true
                onEvent(.firstAudio)
            }
            fill()   // fetch ahead while this piece plays
            if !firstPlay {
                firstPlay = true
                onEvent(.firstPlay)
            }
            await play(item)
        }
    }

    private func fetch(_ n: Int, _ text: String) async -> Playable {
        let lang = SpeechLang.of(piece: text, appLang: appLang())
        guard let sLang = await serverLang(lang) else {
            log("piece \(n) voice=device reason=no_server_voice lang=\(lang)")
            return .deviceEngine(n: n, text: text, lang: lang, fallback: false)
        }
        let t0 = Date()
        let wav = await server(text, sLang)
        let decoded = wav.flatMap { Wav.decode($0) }
        guard let wav, let decoded else {
            if let wav {
                log("piece \(n) speak audio not playable bytes=\(wav.count)")
            } else {
                log("piece \(n) speak gave no audio")
            }
            return .deviceEngine(n: n, text: text, lang: lang, fallback: true)
        }
        let ms = Int(Date().timeIntervalSince(t0) * 1000)
        log("piece \(n) fetched voice=server lang=\(sLang) ms=\(ms) bytes=\(wav.count) rate=\(decoded.sampleRate)")
        return .serverAudio(n: n, pcm: decoded.pcm16le, sampleRate: decoded.sampleRate)
    }

    private func play(_ item: Playable) async {
        switch item {
        case .serverAudio(let n, let pcm, let sampleRate):
            log("piece \(n) play voice=server")
            await audio.play(pcm16le: pcm, sampleRate: sampleRate, onLevel: onLevel)

        case .deviceEngine(let n, let text, let lang, let fallback):
            if fallback && fireOnce("voiceFallback") { onEvent(.voiceFallback) }
            guard let dev = device else {
                log("piece \(n) silent: no device voice")
                silent()
                return
            }
            if fireOnce("deviceSpoke") { onEvent(.deviceSpoke) }
            onLevel(0)
            log("piece \(n) play voice=device")
            let spoke = await speakOnDevice(dev, text: text, lang: lang)
            if !spoke {
                log("piece \(n) silent: device voice failed")
                silent()
            }
        }
    }

    /// Waits until the device voice has spoken the text (true), or it was stopped or failed (false).
    private func speakOnDevice(_ dev: any SpeechEngine, text: String, lang: String) async -> Bool {
        await withTaskCancellationHandler {
            await withCheckedContinuation { (cont: CheckedContinuation<Bool, Never>) in
                let once = Once(cont)
                if Task.isCancelled {
                    once.resume(false)
                    return
                }
                dev.speakChecked(text, lang: lang) { ok in once.resume(ok) }
            }
        } onCancel: {
            dev.stop()
        }
    }

    private func silent() {
        if fireOnce("silent") { onEvent(.silent) }
    }
}

/// Resumes a continuation at most once (a device engine may call back late or twice).
final class Once: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false
    private let cont: CheckedContinuation<Bool, Never>

    init(_ cont: CheckedContinuation<Bool, Never>) {
        self.cont = cont
    }

    func resume(_ value: Bool) {
        let go = lock.withLock { () -> Bool in
            if done { return false }
            done = true
            return true
        }
        if go { cont.resume(returning: value) }
    }
}
