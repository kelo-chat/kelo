import SwiftUI
import KeloShared
import KeloCore
#if os(iOS)

/// The server path of the mic (Android: voice/ServerMicController.kt): record in memory, send the WAV to the Kelo server when the owner
/// ends it, and hand the text back. The text goes to `onText` with what the owner asked for: the field (■, the two-minute cap) or sent
/// at once (↑). A lost connection keeps the recording for "다시 하기", which puts the text in the field. The bar is the composer while
/// it records (ComposerVoice.swift); `bar` is what it draws.
@MainActor
@Observable
final class ServerDictationModel {
    enum Phase: Equatable { case idle, recording, sending }

    /// The audio thread's side: one level per 80 ms window (LevelMeter) and the byte count, drained by the clock every 50 ms.
    final class Meter: @unchecked Sendable {
        private let lock = NSLock()
        private let windows = LevelMeter()
        private var levels: [Float] = []
        private var count: Int64 = 0
        func add(_ chunk: Data) {
            lock.lock(); defer { lock.unlock() }
            levels += windows.add(chunk)
            count += Int64(chunk.count)
        }
        func drain() -> (levels: [Float], bytes: Int64) {
            lock.lock(); defer { lock.unlock() }
            let out = (levels, count)
            levels = []
            return (out.0, out.1)
        }
        func reset() { lock.lock(); windows.reset(); levels = []; count = 0; lock.unlock() }
    }

    private(set) var phase: Phase = .idle
    private(set) var elapsedMs: Int64 = 0
    /// The newest levels for the bar's waveform.
    let trail = LevelTrail()
    /// The one line under the composer (nil = nothing to show).
    var problem: VoiceProblem?
    /// The mic permission was refused: the view asks to open the settings.
    var denied = false

    @ObservationIgnored private let recorder = ServerRecorder()
    @ObservationIgnored private let meter = Meter()
    @ObservationIgnored private var ticker: Task<Void, Never>?
    @ObservationIgnored private var sending: Task<Void, Never>?
    @ObservationIgnored private var pending: Data?
    /// The signed-in server; nil while signed out (the recording then counts as offline).
    @ObservationIgnored var api: () -> KeloApi?
    /// The speech endpoint (a test replaces it).
    @ObservationIgnored private let transcribe: (KeloApi, Data) async throws -> String
    /// Called when a recording starts: remember where the words go.
    @ObservationIgnored var onBegin: () -> Void = {}
    /// The recognised words (trimmed, never empty) and what the owner asked for.
    @ObservationIgnored var onText: (String, AfterSpeech) -> Void = { _, _ in }

    init(api: @escaping () -> KeloApi? = { nil },
         transcribe: @escaping (KeloApi, Data) async throws -> String = { api, wav in try await VoiceAPI.transcribe(api, wav: wav) }) {
        self.api = api
        self.transcribe = transcribe
    }

    var recording: Bool { phase == .recording }

    /// What the composer draws while the server path is busy: nil when idle.
    var bar: RecordingBarState? {
        switch phase {
        case .idle: return nil
        case .recording: return RecordingBarState(stage: .listening, elapsedMs: elapsedMs, endsInSeconds: RecordingBarRules.endsIn(elapsedMs: elapsedMs), held: false)
        case .sending: return RecordingBarState(stage: .turningToText, elapsedMs: elapsedMs, endsInSeconds: nil, held: false)
        }
    }

    func toggle() {
        if phase == .recording { stop() } else { start() }
    }

    func start() {
        guard phase == .idle else { return }
        ServerRecorder.permitted { [weak self] granted in
            Task { @MainActor in self?.afterPermission(granted) }
        }
    }

    private func afterPermission(_ granted: Bool) {
        guard granted else { denied = true; return }
        guard phase == .idle else { return }
        problem = nil
        pending = nil
        onBegin()
        meter.reset()
        trail.clear()
        elapsedMs = 0
        do {
            let meter = self.meter
            try recorder.start { chunk in meter.add(chunk) }
        } catch {
            problem = .failed
            return
        }
        phase = .recording
        ticker = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 50_000_000)
                guard let self, self.phase == .recording else { return }
                let drained = self.meter.drain()
                let now = Int64(DispatchTime.now().uptimeNanoseconds)
                for level in drained.levels { self.trail.push(level, atNanos: now) }
                self.elapsedMs = drained.bytes * 1000 / 32_000
                if self.elapsedMs >= RecordingClock.maxMs { self.stop(); return }   // the two-minute cap: into the field, never sent
            }
        }
    }

    /// ■ (or the two-minute cap): the words go into the field.
    func stop() { finish(.field) }

    /// ↑ or ■: the recording goes to the server, and the reply goes to `onText` with `after`.
    func finish(_ after: AfterSpeech) {
        guard phase == .recording else { return }
        ticker?.cancel()
        ticker = nil
        let wav = Wav.encode(pcm16le: recorder.stop())
        phase = .sending
        send(wav, after: after)
    }

    /// ✕: drops the recording, or the reply that is on its way.
    func discard() {
        if phase == .recording { recorder.cancel() }
        ticker?.cancel(); ticker = nil
        sending?.cancel(); sending = nil
        pending = nil
        trail.clear()
        elapsedMs = 0
        phase = .idle
    }

    /// The owner asks again after the server could not be reached: the same recording goes up once more, into the field.
    func retry() {
        guard let wav = pending else { return }
        pending = nil
        problem = nil
        phase = .sending
        send(wav, after: .field)
    }

    func clearProblem() { problem = nil }

    private func send(_ wav: Data, after: AfterSpeech) {
        let client = api()
        let transcribe = self.transcribe
        sending = Task { @MainActor [weak self] in
            do {
                guard let client else { throw ApiError(status: ApiError.noResponse, detail: "not signed in") }
                let text = try await transcribe(client, wav)
                guard !Task.isCancelled, let self else { return }
                if text.isEmpty { self.problem = .nothingHeard } else { self.onText(text, after) }
                self.phase = .idle
                self.trail.clear()
            } catch let e as ApiError {
                guard !Task.isCancelled, let self else { return }
                let lost = e.status == ApiError.noResponse
                self.pending = lost ? wav : nil
                self.problem = voiceProblem(httpStatus: lost ? nil : e.status, code: e.code)
                self.phase = .idle
                self.trail.clear()
            } catch {
                self?.phase = .idle
            }
            self?.sending = nil
        }
    }
}

/// The one line for a recognition that gave no text; 다시 하기 when the server could not be reached (the recording is kept).
struct ServerProblemLine: View {
    let model: ServerDictationModel
    let problem: VoiceProblem
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 8) {
            Text(line).font(KeloFont.small).foregroundStyle(c.ink).frame(maxWidth: .infinity, alignment: .leading)
            if problem == .offline {
                Button { model.retry() } label: { Text(L(R.string.voice_retry)).font(KeloFont.small.weight(.semibold)).foregroundStyle(c.accent) }
            } else {
                Button { model.clearProblem() } label: { Text(L(R.string.ok)).font(KeloFont.small.weight(.semibold)).foregroundStyle(c.accent) }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .frame(width: 300)
        .background(RoundedRectangle(cornerRadius: 16).fill(c.card))
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
    }

    private var line: String {
        switch problem {
        case .nothingHeard: return L(R.string.voice_no_match)
        case .offline: return L(R.string.voice_server_offline)
        case .busy: return L(R.string.voice_server_busy)
        case .tooLong: return L(R.string.voice_server_too_long)
        case .notReady: return L(R.string.voice_server_download_failed)
        case .failed: return L(R.string.voice_dictation_failed)
        }
    }
}

/// The server mic button: a tap starts, a tap stops and sends. The card above shows while the owner speaks.
/// The download ring: a circle with the speech model's progress (a spinner when the server gives no percent). Replaces the mic.
struct VoiceDownloadRingView: View {
    let percent: Int?
    let label: String
    @Environment(\.kelo) private var c

    var body: some View {
        ZStack {
            if let percent {
                Circle().stroke(c.muted.opacity(0.25), lineWidth: 2.5)
                Circle().trim(from: 0, to: CGFloat(percent) / 100)
                    .stroke(c.accent, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            } else {
                ProgressView().controlSize(.small).tint(c.accent)
            }
        }
        .frame(width: 22, height: 22)
        .frame(width: 40, height: 40)
        .accessibilityLabel(label)
    }
}

/// The server mic: a tap starts; while it records the composer is the bar (ComposerVoice.swift), so only the problem line shows here.
struct ServerMicButton: View {
    let voice: ComposerVoiceModel
    @Environment(\.kelo) private var c

    var body: some View {
        Button { voice.server.toggle() } label: {
            KeloIconView(.mic, size: 22, tint: c.muted)
                .frame(width: 40, height: 40)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(L(R.string.voice_start))
        .overlay(alignment: .topTrailing) {
            if let problem = voice.server.problem {
                ServerProblemLine(model: voice.server, problem: problem)
                    .fixedSize()
                    .offset(y: -50)
            }
        }
    }
}
#endif
