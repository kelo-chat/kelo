import Foundation
import KeloShared

/// Speaks reply sentences in order, prefetching up to [CallRules.prefetch] ahead (brief §4.8).
public final class SpeechQueue: @unchecked Sendable {
    public enum Event: Sendable {
        case firstPlay
        case firstAudio
        case idle
        case deviceSpoke
    }

    private enum Playable: Sendable {
        case serverAudio(text: String, pcm: Data, sampleRate: Int)
        case deviceEngine(text: String, lang: String)
    }

    private let audio: CallAudio
    private let server: @Sendable (String, String) async -> Data?
    private let device: @Sendable () -> (any SpeechEngine)?
    private let serverLang: @Sendable (String) async -> String?
    private let appLang: @Sendable () -> String
    private let onLevel: @Sendable (Float) -> Void
    private let onEvent: @Sendable (Event) -> Void

    private let lock = NSLock()
    private var pendingTexts: [String] = []
    private var readyQueue: [Playable] = []

    private var workerTask: Task<Void, Never>?
    private var prefetchTask: Task<Void, Never>?

    private var _playing = false
    public var playing: Bool { lock.withLock { _playing } }

    private var firstAudioFired = false
    private var firstPlayFired = false
    private var deviceSpokeFired = false

    public init(
        audio: CallAudio,
        server: @escaping @Sendable (String, String) async -> Data?,
        device: @escaping @Sendable () -> (any SpeechEngine)?,
        serverLang: @escaping @Sendable (String) async -> String?,
        appLang: @escaping @Sendable () -> String,
        onLevel: @escaping @Sendable (Float) -> Void,
        onEvent: @escaping @Sendable (Event) -> Void
    ) {
        self.audio = audio
        self.server = server
        self.device = device
        self.serverLang = serverLang
        self.appLang = appLang
        self.onLevel = onLevel
        self.onEvent = onEvent
    }

    public func add(_ piece: String) {
        let trimmed = piece.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        lock.lock()
        pendingTexts.append(trimmed)
        _playing = true
        lock.unlock()

        ensureTasks()
    }

    public func stop() {
        lock.lock()
        pendingTexts.removeAll()
        readyQueue.removeAll()
        _playing = false
        lock.unlock()

        prefetchTask?.cancel()
        prefetchTask = nil
        workerTask?.cancel()
        workerTask = nil

        audio.stopPlayback()
        device()?.stop()
    }

    private func ensureTasks() {
        lock.lock()
        defer { lock.unlock() }

        if prefetchTask == nil {
            prefetchTask = Task { [weak self] in
                await self?.runPrefetch()
            }
        }
        if workerTask == nil {
            workerTask = Task { [weak self] in
                await self?.runPlayback()
            }
        }
    }

    private func runPrefetch() async {
        while !Task.isCancelled {
            let nextText: String? = lock.withLock {
                if pendingTexts.isEmpty { return nil }
                return pendingTexts.removeFirst()
            }

            guard let text = nextText else { break }

            let lang = SpeechLang.of(piece: text, appLang: appLang())
            let sLang = await serverLang(lang)
            let playable: Playable

            if let sLang {
                let wav = await server(text, sLang)
                if let wav, let decoded = Wav.decode(wav) {
                    playable = .serverAudio(text: text, pcm: decoded.pcm16le, sampleRate: decoded.sampleRate)
                } else {
                    playable = .deviceEngine(text: text, lang: lang)
                }
            } else {
                playable = .deviceEngine(text: text, lang: lang)
            }

            let fireFirstAudio = lock.withLock { () -> Bool in
                let fire = !firstAudioFired
                if fire { firstAudioFired = true }
                readyQueue.append(playable)
                return fire
            }
            if fireFirstAudio {
                onEvent(.firstAudio)
            }
        }

        lock.withLock {
            prefetchTask = nil
        }
    }

    private func runPlayback() async {
        while !Task.isCancelled {
            let item: Playable? = lock.withLock {
                if readyQueue.isEmpty { return nil }
                return readyQueue.removeFirst()
            }

            guard let current = item else {
                let isDone = lock.withLock {
                    pendingTexts.isEmpty && readyQueue.isEmpty && prefetchTask == nil
                }
                if isDone {
                    lock.withLock {
                        _playing = false
                        firstAudioFired = false
                        firstPlayFired = false
                    }
                    onEvent(.idle)
                    break
                }
                try? await Task.sleep(nanoseconds: 20_000_000)
                continue
            }

            let fireFirstPlay = lock.withLock { () -> Bool in
                let fire = !firstPlayFired
                if fire { firstPlayFired = true }
                return fire
            }
            if fireFirstPlay {
                onEvent(.firstPlay)
            }

            switch current {
            case .serverAudio(_, let pcm, let sampleRate):
                await audio.play(pcm16le: pcm, sampleRate: sampleRate, onLevel: onLevel)

            case .deviceEngine(let text, let lang):
                let fireDeviceSpoke = lock.withLock { () -> Bool in
                    let fire = !deviceSpokeFired
                    if fire { deviceSpokeFired = true }
                    return fire
                }
                if fireDeviceSpoke {
                    onEvent(.deviceSpoke)
                }

                onLevel(0)
                await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                    guard let dev = device() else {
                        cont.resume()
                        return
                    }
                    dev.speak(text, lang: lang) {
                        cont.resume()
                    }
                }
            }
        }

        lock.withLock {
            workerTask = nil
        }
    }
}
