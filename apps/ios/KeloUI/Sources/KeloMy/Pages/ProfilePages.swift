import SwiftUI
import PhotosUI
import KeloCore
import KeloShared

/// My name and the assistant's name (PUT /api/profile).
struct ProfilePage: View {
    @Environment(\.dismiss) private var dismiss
    @State private var model: ProfileModel
    @State private var user = ""
    @State private var agent = ""
    @State private var loaded = false

    init(api: KeloApi, host: any ProfileHost) { _model = State(initialValue: ProfileModel(api: api, host: host)) }

    var body: some View {
        SubPage(L(R.string.profile_title), onBack: { dismiss() }) {
            MyCard {
                FieldLabel(L(R.string.profile_user_name))
                KeloTextField(L(R.string.profile_user_name), text: $user, maxLength: 60)
                FieldLabel(L(R.string.profile_agent_name))
                KeloTextField(L(R.string.profile_agent_name), text: $agent, maxLength: 60)
                PrimaryButton(model.saved ? L(R.string.saved_check) : L(R.string.save), enabled: !agent.trimmingCharacters(in: .whitespaces).isEmpty) {
                    model.save(user: user, agent: agent)
                }
                if !model.error.isEmpty { ErrorText(model.error, small: true) }
            }
        }
        .onAppear {
            guard !loaded, let p = model.profile else { return }
            loaded = true; user = p.userName; agent = p.agentName
        }
        .onChange(of: user) { _, _ in model.edited() }
        .onChange(of: agent) { _, _ in model.edited() }
    }
}

/// Plain-language answers to "what happens to my data". Everything here is something the app really does.
struct PrivacyPage: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        let titles = LA(R.array.privacy_titles)
        let bodies = LA(R.array.privacy_bodies)
        SubPage(L(R.string.privacy_title), onBack: { dismiss() }) {
            MyCard {
                ForEach(Array(zip(titles, bodies).enumerated()), id: \.offset) { i, pair in
                    KeyBox(first: i == 0) {
                        IconTitle(.shield, pair.0)
                        MutedText(pair.1, small: true)
                        if i == 0 {
                            MutedText(L(R.string.my_privacy_keyring), small: true)
                        }
                    }
                }
            }
        }
    }
}

/// 앱 → 언어: kept on this phone; the whole app changes to it at once.
struct LanguagePage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var value = Languages.system

    var body: some View {
        SubPage(L(R.string.my_language), onBack: { dismiss() }) {
            LanguageList(value: $value)
        }
        .onAppear { value = app.languageChoice ?? Languages.system }
        .onChange(of: value) { _, v in if v != (app.languageChoice ?? Languages.system) { app.chooseLanguage(v) } }
    }
}

/// The agent's picture limits (Android AgentAvatar.MAX_BYTES).
enum AvatarRules {
    static let maxBytes = 2_000_000
    /// The longest side a picked picture is scaled down to before upload.
    static let maxSide: CGFloat = 1024
}

/// 마이 → 에이전트: the agent's picture (pick one, or go back to Kelo's character), its name, its tone (친근 / 공손, each with an
/// example sentence; `ToneRules`) and what it is doing now (Android my/AgentPage.kt). The profile is read again each time the page
/// opens, so a tone the agent changed in the chat shows as chosen; the status poll (`AppModel.statusPolled`) also brings it while
/// the page is open.
struct AgentPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: AgentPageModel
    @State private var pick: PhotosPickerItem?
    @State private var rename = false
    @State private var newName = ""
    @State private var confirmRemove = false

    init(api: KeloApi, host: any ProfileHost) { _model = State(initialValue: AgentPageModel(api: api, host: host)) }

    var body: some View {
        let profile = model.profile
        let shownTone = model.shownTone
        let working = model.working
        let savingTone = model.savingTone
        SubPage(L(R.string.my_agent_title), onBack: { dismiss() }) {
            SectionLabel(L(R.string.my_agent_picture)).padding(.horizontal, -16)
            MyCard {
                HStack(spacing: 14) {
                    ZStack {
                        AgentPicture(image: app.avatar.image, size: 56)
                        if working { ProgressView().tint(c.accent) }
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        MutedText(app.avatar.image != nil ? L(R.string.my_agent_picture_hint) : L(R.string.my_agent_picture_default), small: true)
                        if app.avatar.image != nil { LinkButton(L(R.string.agent_avatar_remove), danger: true) { confirmRemove = true } }
                    }
                }
                let pickLabel = L(R.string.agent_avatar_pick)
                PhotosPicker(selection: $pick, matching: .images) {
                    Text(pickLabel).font(KeloFont.bodyLarge).foregroundStyle(working ? c.disabledInk : c.ink)
                        .frame(maxWidth: .infinity).padding(.vertical, 11)
                        .background(Capsule().fill(c.card)).overlay(Capsule().stroke(c.line, lineWidth: 1))
                }
                .disabled(working)
            }
            SectionLabel(L(R.string.my_agent_identity)).padding(.horizontal, -16)
            MenuRow(.edit, L(R.string.profile_agent_name), value: profile.agentName) { newName = profile.agentName; rename = true }
                .padding(.horizontal, -16)
            SectionLabel(L(R.string.my_agent_tone)).padding(.horizontal, -16)
            VStack(spacing: 0) {
                ToneRow(L(R.string.my_agent_tone_friendly), example: L(R.string.my_agent_tone_friendly_example),
                        selected: shownTone == ToneRules.friendly, enabled: savingTone == nil) { model.pickTone(ToneRules.friendly) }
                    .accessibilityIdentifier("kelo.tone.friendly")
                ToneRow(L(R.string.my_agent_tone_polite), example: L(R.string.my_agent_tone_polite_example),
                        selected: shownTone == ToneRules.polite, enabled: savingTone == nil) { model.pickTone(ToneRules.polite) }
                    .accessibilityIdentifier("kelo.tone.polite")
            }
            .padding(.horizontal, -16)
            .accessibilityElement(children: .contain)
            .accessibilityLabel(L(R.string.my_agent_tone))
            SectionLabel(L(R.string.my_agent_now)).padding(.horizontal, -16)
            MyCard {
                let line = app.activity.status.flatMap { AgentActivityRules.line($0) }
                Text(line ?? L(R.string.agent_idle)).font(KeloFont.body).foregroundStyle(line != nil ? c.ink : c.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        // the agent may have changed the tone in the chat ("반말로 해 줘"): the server's profile as it is now
        .task { await model.load() }
        .onChange(of: pick) { _, item in
            guard let item else { return }
            Task { @MainActor in   // reading the picked picture is the phone's, the upload is the model's
                let raw = try? await item.loadTransferable(type: Data.self)
                pick = nil
                model.upload(picked: raw)
            }
        }
        .alert(L(R.string.agent_rename_title), isPresented: $rename) {
            TextField(L(R.string.agent_rename_placeholder), text: $newName)
            Button(L(R.string.save)) { model.rename(newName) }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .keloConfirm(L(R.string.agent_avatar_remove_confirm), isPresented: $confirmRemove, yes: L(R.string.agent_avatar_remove_yes)) { model.removePicture() }
        .toast(model.notice, clear: model.clearNotice)
    }
}

/// One tone (Android AgentPage ToneRow): a radio mark, its name and, under it, an example sentence in that tone (wraps in any
/// language). Lined up with the settings rows above it: the mark sits in the icon column.
private struct ToneRow: View {
    let label: String
    let example: String
    let selected: Bool
    let enabled: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c

    init(_ label: String, example: String, selected: Bool, enabled: Bool, action: @escaping () -> Void) {
        self.label = label; self.example = example; self.selected = selected; self.enabled = enabled; self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 16) {
                ZStack {
                    Circle().stroke(selected ? c.accent : c.muted, lineWidth: 2).frame(width: 20, height: 20)
                    if selected { Circle().fill(c.accent).frame(width: 10, height: 10) }
                }
                .frame(width: 24, height: 24)
                VStack(alignment: .leading, spacing: 2) {
                    Text(label).font(KeloFont.bodyLarge).fontWeight(selected ? .medium : .regular).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(example).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .frame(minHeight: 56)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }
}

/// A picked picture scaled down and encoded as JPEG for the avatar upload.
enum AvatarImage {
    static func jpeg(_ img: PlatformImage) -> Data? {
        #if os(iOS)
        let size = img.size
        let scale = min(1, AvatarRules.maxSide / max(size.width, size.height))
        let target = CGSize(width: size.width * scale, height: size.height * scale)
        let r = UIGraphicsImageRenderer(size: target)
        let scaled = r.image { _ in img.draw(in: CGRect(origin: .zero, size: target)) }
        return scaled.jpegData(compressionQuality: 0.85)
        #else
        return img.keloJPEG()
        #endif
    }
}
