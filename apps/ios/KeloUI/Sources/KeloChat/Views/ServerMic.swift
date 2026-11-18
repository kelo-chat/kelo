import SwiftUI
import KeloShared
import KeloCore
#if os(iOS)

/// The server path of the mic (Android: voice/ServerMicController.kt): record in memory, send the WAV to the Kelo server when the owner
/// stops, and hand the text back at the caret. Nothing is sent by itself. The phase rules are the same as on Android: one recording at
/// a time, two minutes at most, a lost connection keeps the recording for "다시 하기".
@MainActor
@Observable
final class ServerDictationModel {
    enum Phase: Equatable { case idle, recording, sending }

    /// The audio thread's side: the levels and the byte count, read by the clock every 50 ms.
    final class Meter: @unchecked Sendable {
        private let lock = NSLock()
        private var levels: [Float] = []
        private var count: Int64 = 0
        func add(_ chunk: Data) {
            let level = SoundBars.rms(chunk, from: 0, to: chunk.count)
            lock.lock(); levels.append(level); count += Int64(chunk.count); lock.unlock()
        }
        func drain() -> (levels: [Float], bytes: Int64) {
            lock.lock(); defer { lock.unlock() }
            let out = (levels, count)
            levels = []
            return (out.0, out.1)
        }
        func reset() { lock.lock(); levels = []; count = 0; lock.unlock() }
    }

    private(set) var phase: Phase = .idle
    private(set) var bars: [Float] = []
    private(set) var elapsedMs: Int64 = 0
    /// The one line under the composer (nil = nothing to show).
    var problem: VoiceProblem?
    /// The mic permission was refused: the view asks to open the settings.
    var denied = false

    @ObservationIgnored private let recorder = ServerRecorder()
    @ObservationIgnored private let meter = Meter()
    @ObservationIgnored private var ticker: Task<Void, Never>?
    @ObservationIgnored private var sending: Task<Void, Never>?
    @ObservationIgnored private var pending: Data?
    @ObservationIgnored private let api: () -> KeloApi?
    /// Where the words go: called when a recording starts (the caret / the end of the field).
    @ObservationIgnored var onBegin: () -> Void = {}
    /// The recognised words for the field (trimmed, never empty).
    @ObservationIgnored var onText: (String) -> Void = { _ in }

    init(api: @escaping () -> KeloApi?) { self.api = api }

    var recording: Bool { phase == .recording }

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
        bars = []
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
                for level in drained.levels { self.bars = SoundBars.push(self.bars, level) }
                self.elapsedMs = drained.bytes * 1000 / 32_000
                if self.elapsedMs >= RecordingClock.maxMs { self.stop(); return }   // the two-minute cap
            }
        }
    }

    /// Tap again, or the hold is released: send what was said.
    func stop() {
        guard phase == .recording else { return }
        ticker?.cancel()
        ticker = nil
        let wav = Wav.encode(pcm16le: recorder.stop())
        phase = .sending
        send(wav)
    }

    /// ✕ or a slide off the button: drops the recording, or the reply that is on its way.
    func cancel() {
        if phase == .recording { recorder.cancel() }
        ticker?.cancel(); ticker = nil
        sending?.cancel(); sending = nil
        pending = nil
        bars = []
        elapsedMs = 0
        phase = .idle
    }

    /// The owner asks again after the server could not be reached: the same recording goes up once more.
    func retry() {
        guard let wav = pending else { return }
        pending = nil
        problem = nil
        phase = .sending
        send(wav)
    }

    func clearProblem() { problem = nil }

    private func send(_ wav: Data) {
        let client = api()
        sending = Task { @MainActor [weak self] in
            do {
                guard let client else { throw ApiError(status: ApiError.noResponse, detail: "not signed in") }
                let text = try await VoiceAPI.transcribe(client, wav: wav)
                guard !Task.isCancelled, let self else { return }
                if text.isEmpty { self.problem = .nothingHeard } else { self.onText(text) }
                self.phase = .idle
            } catch let e as ApiError {
                guard !Task.isCancelled, let self else { return }
                let lost = e.status == ApiError.noResponse
                self.pending = lost ? wav : nil
                self.problem = voiceProblem(httpStatus: lost ? nil : e.status, code: e.code)
                self.phase = .idle
            } catch {
                self?.phase = .idle
            }
            self?.sending = nil
        }
    }
}

/// The card above the mic while the owner speaks: ✕, the sound graph, the clock (a countdown from 1:50), and stop.
struct ServerRecordingCard: View {
    let model: ServerDictationModel
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 8) {
            Button { model.cancel() } label: {
                Text(L(R.string.voice_cancel_recording)).font(KeloFont.small).foregroundStyle(c.muted)
            }
            if model.phase == .sending {
                ProgressView().controlSize(.small)
                Text(L(R.string.voice_transcribing)).font(KeloFont.small).foregroundStyle(c.ink)
                Spacer(minLength: 0)
            } else {
                HStack(alignment: .center, spacing: 2) {
                    ForEach(Array(model.bars.enumerated()), id: \.offset) { _, level in
                        Capsule().fill(c.accent).frame(width: 3, height: 3 + 25 * CGFloat(level))
                    }
                }
                .frame(height: 28)
                .accessibilityHidden(true)
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(RecordingClock.clock(model.elapsedMs)).font(KeloFont.small).foregroundStyle(c.ink).monospacedDigit()
                    if model.elapsedMs >= RecordingClock.countdownFromMs {
                        Text(L(R.string.voice_ends_in, Int((RecordingClock.maxMs - model.elapsedMs) / 1000)))
                            .font(KeloFont.small).foregroundStyle(c.dangerInk)
                    }
                }
                Button { model.stop() } label: {
                    Text(L(R.string.voice_stop_recording)).font(KeloFont.small.weight(.semibold)).foregroundStyle(c.accent)
                }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(width: 300)
        .background(RoundedRectangle(cornerRadius: 16).fill(c.card))
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
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
struct ServerMicButton: View {
    let model: ServerDictationModel
    @Environment(\.kelo) private var c

    var body: some View {
        Button { model.toggle() } label: {
            ZStack {
                if model.recording {
                    Circle().stroke(c.accent.opacity(0.35), lineWidth: 3)
                }
                KeloIconView(model.recording ? .stop : .mic, size: 22, tint: model.recording ? c.accent : c.muted)
            }
            .frame(width: 40, height: 40)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(L(model.recording ? R.string.voice_stop : R.string.voice_start))
        .accessibilityValue(model.recording ? L(R.string.voice_listening) : "")
        .overlay(alignment: .topTrailing) {
            if model.phase != .idle {
                ServerRecordingCard(model: model)
                    .fixedSize()
                    .offset(y: -50)
            } else if let problem = model.problem {
                ServerProblemLine(model: model, problem: problem)
                    .fixedSize()
                    .offset(y: -50)
            }
        }
    }
}
#endif
