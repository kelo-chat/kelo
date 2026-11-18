import SwiftUI
import KeloShared
import KeloCore

/// 마이 → 음성 입력: the speech model on the Kelo server, the two models to pick from, and removing the model. No licence text here.
struct VoicePage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    let api: KeloApi
    @State private var view: VoiceView?
    @State private var problem: VoiceProblem?
    @State private var confirmRemove = false

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
        }
        .task { await load() }
        .alert(L(R.string.voice_remove_model), isPresented: $confirmRemove) {
            Button(L(R.string.voice_remove_model), role: .destructive) { remove() }
            Button(L(R.string.cancel), role: .cancel) {}
        } message: { Text(L(R.string.voice_remove_confirm)) }
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

    private func choose(_ id: String) {
        Task { await answer { try await VoiceAPI.choose(api, id: id) } }
    }

    private func remove() {
        Task { await answer { try await VoiceAPI.remove(api) } }
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
