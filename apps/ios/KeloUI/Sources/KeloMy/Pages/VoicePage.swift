import SwiftUI
import AVFoundation
import KeloShared
import KeloCore

/// 마이 → 음성: the speech model on the Kelo server, the two models to pick from, and removing the model.
/// Plus the Kelo 목소리 (TTS) section (brief §5.4). No licence text here.
struct VoicePage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    let api: KeloApi
    @State private var view: VoiceView?
    @State private var problem: VoiceProblem?
    @State private var confirmRemove = false

    @State private var speechView: SpeechView?
    @State private var speechError: String?
    @State private var confirmRemoveSpeech = false
    @State private var playingVoiceId: Int?
    @State private var player: AVAudioPlayer?
    @State private var sliderSpeed: Double = 1.0

    var body: some View {
        SubPage(L(R.string.voice_page_title), onBack: { dismiss() }) {
            MutedText(L(R.string.voice_page_intro))
            if let v = view {
                MyCard {
                    if let model = v.model {
                        Text(model.name).font(KeloFont.small.weight(.semibold)).foregroundStyle(c.ink)
                        MutedText(sizeLine(model), small: true)
                        Text(stateLine(v)).font(KeloFont.small).foregroundStyle(v.state == "failed" ? c.dangerInk : c.muted)
                    } else {
                        Text(L(R.string.voice_state_off)).font(KeloFont.small).foregroundStyle(c.ink)
                        if let recommended = v.recommended {
                            PrimaryButton(L(R.string.voice_download_model), enabled: v.state != "downloading") { choose(recommended.id) }
                        }
                    }
                    if v.runtime == "missing" { MutedText(L(R.string.voice_runtime_missing), small: true) }
                    if v.state == "unsupported" { MutedText(L(R.string.voice_phone_note), small: true) }
                }
                if !(v.models ?? []).isEmpty {
                    MyCard {
                        Text(L(R.string.voice_change_model)).font(KeloFont.small.weight(.semibold)).foregroundStyle(c.ink)
                        ForEach(v.models ?? [], id: \.id) { m in
                            Button { choose(m.id) } label: {
                                HStack(spacing: 10) {
                                    Image(systemName: v.model?.id == m.id ? "circle.inset.filled" : "circle")
                                        .foregroundStyle(v.model?.id == m.id ? c.accent : c.muted)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(m.name).font(KeloFont.small).foregroundStyle(c.ink)
                                        MutedText(sizeLine(m), small: true)
                                    }
                                    Spacer(minLength: 0)
                                }
                            }
                            .buttonStyle(.plain)
                            .disabled(v.model?.id == m.id || v.state == "downloading")
                        }
                    }
                }
                if v.model != nil {
                    SecondaryButton(L(R.string.voice_remove_model)) { confirmRemove = true }
                }
            }
            if let problem { ErrorText(line(for: problem)) }

            // --- 2. Kelo's Voice (TTS) Section ---
            Spacer().frame(height: 20)
            Text(L(R.string.voice_speech_title))
                .font(KeloFont.small.weight(.semibold))
                .foregroundStyle(c.ink)

            if let sv = speechView {
                if sv.state == "offered" || sv.state == "off" {
                    MyCard {
                        MutedText(L(R.string.voice_speech_device_note))
                        let sizeStr = sv.model?.sizeBytes.map { bytes($0) } ?? "145 MB"
                        PrimaryButton(L(R.string.voice_speech_download, sizeStr)) {
                            chooseSpeech()
                        }
                    }
                } else if sv.state == "downloading" {
                    MyCard {
                        let done = sv.progress?.done ?? 0, total = sv.progress?.total ?? 0
                        let percent = total <= 0 ? 0 : Int(min(100, max(0, done * 100 / total)))
                        HStack(spacing: 12) {
                            ProgressView()
                            Text(L(R.string.voice_speech_downloading, percent))
                                .font(KeloFont.small)
                                .foregroundStyle(c.ink)
                        }
                        .padding(.vertical, 8)
                    }
                } else if sv.state == "failed" {
                    MyCard {
                        Text(sv.error ?? L(R.string.voice_server_download_failed))
                            .font(KeloFont.small)
                            .foregroundStyle(c.dangerInk)
                        PrimaryButton(L(R.string.voice_retry)) {
                            chooseSpeech()
                        }
                    }
                } else if sv.state == "ready" {
                    let appLang = appLanguageCode()
                    let langSubtag = appLang.split(separator: "-").first.map { String($0).lowercased() } ?? appLang.lowercased()
                    let isSupported = sv.languages.isEmpty || sv.languages.contains(langSubtag) || sv.languages.contains(appLang)
                    if !isSupported {
                        MyCard {
                            MutedText(L(R.string.voice_speech_unsupported_lang))
                        }
                    } else {
                        MyCard {
                            let voices = !sv.voices.isEmpty ? sv.voices : (
                                (0...4).map { SpeechVoice(id: $0, kind: "female", n: $0 + 1) } +
                                (5...9).map { SpeechVoice(id: $0, kind: "male", n: $0 - 4) }
                            )
                            ForEach(voices, id: \.id) { voiceItem in
                                let selected = sv.voice == voiceItem.id
                                let label = voiceItem.kind == "female" ?
                                    L(R.string.voice_speech_voice_female, voiceItem.n) :
                                    L(R.string.voice_speech_voice_male, voiceItem.n)
                                HStack(spacing: 10) {
                                    Button {
                                        selectVoice(voiceItem.id)
                                    } label: {
                                        HStack(spacing: 10) {
                                            Image(systemName: selected ? "circle.inset.filled" : "circle")
                                                .foregroundStyle(selected ? c.accent : c.muted)
                                            Text(label)
                                                .font(KeloFont.small)
                                                .foregroundStyle(c.ink)
                                            Spacer()
                                        }
                                    }
                                    .buttonStyle(.plain)

                                    Button(action: {
                                        togglePlay(voiceItem.id, speed: sv.speed)
                                    }) {
                                        Text(playingVoiceId == voiceItem.id ? L(R.string.voice_speech_stop) : L(R.string.voice_speech_listen))
                                            .font(KeloFont.small.weight(.semibold))
                                            .foregroundStyle(c.accent)
                                    }
                                    .buttonStyle(.plain)
                                }
                                .padding(.vertical, 4)
                            }

                            // Speed slider
                            Spacer().frame(height: 8)
                            HStack {
                                Text(L(R.string.voice_speech_speed))
                                    .font(KeloFont.small)
                                    .foregroundStyle(c.ink)
                                Spacer()
                                Text(L(R.string.voice_speech_speed_value, String(format: "%.1f", sliderSpeed)))
                                    .font(KeloFont.small)
                                    .foregroundStyle(c.muted)
                            }
                            Slider(
                                value: $sliderSpeed,
                                in: 0.7...1.5,
                                step: 0.1,
                                onEditingChanged: { editing in
                                    if !editing {
                                        updateSpeed(sliderSpeed)
                                    }
                                }
                            )
                        }
                    }

                    SecondaryButton(L(R.string.voice_speech_remove)) {
                        confirmRemoveSpeech = true
                    }
                }
            }
            if let speechError {
                ErrorText(speechError)
            }
        }
        .task {
            async let t1: Void = load()
            async let t2: Void = loadSpeech()
            _ = await (t1, t2)
        }
        .alert(L(R.string.voice_remove_model), isPresented: $confirmRemove) {
            Button(L(R.string.voice_remove_model), role: .destructive) { remove() }
            Button(L(R.string.cancel), role: .cancel) {}
        } message: { Text(L(R.string.voice_remove_confirm)) }
        .alert(L(R.string.voice_speech_remove), isPresented: $confirmRemoveSpeech) {
            Button(L(R.string.voice_speech_remove), role: .destructive) { removeSpeech() }
            Button(L(R.string.cancel), role: .cancel) {}
        } message: { Text(L(R.string.voice_speech_remove_confirm)) }
    }

    private func sizeLine(_ m: VoiceModelView) -> String {
        L(R.string.voice_model_size, bytes(m.sizeBytes ?? 0), bytes(m.memoryBytes ?? 0))
    }

    private func bytes(_ n: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: n, countStyle: .file)
    }

    private func stateLine(_ v: VoiceView) -> String {
        switch v.state {
        case "downloading":
            let done = v.progress?.done ?? 0, total = v.progress?.total ?? 0
            let percent = total <= 0 ? 0 : Int(min(100, max(0, done * 100 / total)))
            return L(R.string.voice_server_downloading, percent)
        case "ready": return L(R.string.voice_model_in_use)
        case "failed": return L(R.string.voice_server_download_failed)
        default: return L(R.string.voice_state_off)
        }
    }

    private func line(for problem: VoiceProblem) -> String {
        switch problem {
        case .nothingHeard: return L(R.string.voice_no_match)
        case .offline: return L(R.string.voice_server_offline)
        case .busy: return L(R.string.voice_server_busy)
        case .tooLong: return L(R.string.voice_server_too_long)
        case .notReady: return L(R.string.voice_server_download_failed)
        case .failed: return L(R.string.voice_dictation_failed)
        }
    }

    private func load() async {
        while !Task.isCancelled {
            if let fresh = try? await VoiceAPI.view(api, lang: appLanguageCode()) { view = fresh }
            guard view?.state == "downloading" else { return }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
    }

    private func loadSpeech() async {
        while !Task.isCancelled {
            if let fresh = try? await VoiceAPI.speechView(api) {
                speechView = fresh
                sliderSpeed = fresh.speed
            }
            guard speechView?.state == "downloading" else { return }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
    }

    private func choose(_ id: String) {
        Task { await answer { try await VoiceAPI.choose(api, id: id) } }
    }

    private func remove() {
        Task { await answer { try await VoiceAPI.remove(api) } }
    }

    private func chooseSpeech() {
        Task {
            do {
                speechView = try await VoiceAPI.speechChoose(api)
                speechError = nil
                await loadSpeech()
            } catch {
                speechError = L(R.string.voice_server_download_failed)
            }
        }
    }

    private func removeSpeech() {
        Task {
            do {
                speechView = try await VoiceAPI.speechRemove(api)
                speechError = nil
                await loadSpeech()
            } catch {
                speechError = L(R.string.voice_server_download_failed)
            }
        }
    }

    private func selectVoice(_ id: Int) {
        Task {
            if let fresh = try? await VoiceAPI.speechPrefs(api, voice: id) {
                speechView = fresh
            }
        }
    }

    private func updateSpeed(_ speed: Double) {
        Task {
            if let fresh = try? await VoiceAPI.speechPrefs(api, speed: speed) {
                speechView = fresh
            }
        }
    }

    private func togglePlay(_ voiceId: Int, speed: Double) {
        if playingVoiceId == voiceId {
            player?.stop()
            player = nil
            playingVoiceId = nil
            return
        }
        player?.stop()
        player = nil
        playingVoiceId = voiceId
        Task {
            do {
                let wavData = try await VoiceAPI.speak(
                    api,
                    text: L(R.string.voice_speech_sample),
                    lang: appLanguageCode(),
                    voice: voiceId,
                    speed: speed
                )
                await MainActor.run {
                    guard playingVoiceId == voiceId else { return }
                    if let p = try? AVAudioPlayer(data: wavData) {
                        self.player = p
                        p.play()
                    } else {
                        self.playingVoiceId = nil
                    }
                }
            } catch {
                await MainActor.run {
                    if self.playingVoiceId == voiceId {
                        self.playingVoiceId = nil
                    }
                }
            }
        }
    }

    private func answer(_ call: () async throws -> VoiceView) async {
        do {
            view = try await call()
            problem = nil
            await load()
        } catch let e as ApiError {
            problem = voiceProblem(httpStatus: e.status == ApiError.noResponse ? nil : e.status, code: e.code)
        } catch {
            problem = .failed
        }
    }
}
