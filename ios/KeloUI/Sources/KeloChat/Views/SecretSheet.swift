import SwiftUI
import KeloCore
import KeloShared

/// The sheet for a message the server held back because it holds a key or password (`SecretGuard`; Android chat/SecretSheet.kt).
/// First it asks: Save in Keychain, Send as is (the same text with "secrets": "allow"), or Edit (back to the composer, text intact).
/// Save opens its second step in the same sheet: the value from the message already filled in (hidden, with Show), what it is for,
/// and a name or site guessed from the server's label; Save there goes straight from the app to the vault. A card number goes on to
/// the wallet's card form instead. The value lives only in this sheet's memory (`@State`, never stored) and goes with it.
struct SecretSheet: View {
    let ask: SecretAsk
    let onSaved: (_ value: String, _ reference: String) -> Void
    let onCard: () -> Void
    let onSendAsIs: () -> Void
    let onEdit: () -> Void
    /// Starts on the save step (screenshots of a real scene only).
    var startSaving = false
    @Environment(\.kelo) private var c
    @State private var saving = false
    @State private var busy = false

    var body: some View {
        let uses = SecretGuard.uses(ask.found)
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if !saving {
                    Text(L(R.string.secret_title)).font(KeloFont.titleLarge).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
                        .padding(.top, 20)
                    MutedText(L(R.string.secret_body), small: true)
                    MutedText(L(R.string.secret_body_save), small: true)
                    VStack(spacing: 10) {
                        PrimaryButton(L(R.string.secret_save)) { if uses == [.card] { onCard() } else { saving = true } }
                            .accessibilityIdentifier("kelo.secret.save")
                        SecondaryButton(L(R.string.secret_send_as_is), fill: true, action: onSendAsIs)
                            .accessibilityIdentifier("kelo.secret.sendAsIs")
                        SecondaryButton(L(R.string.secret_edit), fill: true, action: onEdit)
                            .accessibilityIdentifier("kelo.secret.edit")
                    }
                    .padding(.top, 6)
                } else {
                    SecretSaveStep(ask: ask, uses: uses, busy: $busy, onSaved: onSaved, onCard: onCard, onBack: { saving = false })
                        .padding(.top, 20)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20).padding(.bottom, 16)
        }
        .scrollBounceBehavior(.basedOnSize)
        .interactiveDismissDisabled(busy)
        .onAppear { if startSaving && uses != [.card] { saving = true } }
    }
}

/// The save step: what it is for, the fields that form needs, the value (hidden until Show), Save and Back.
private struct SecretSaveStep: View {
    let ask: SecretAsk
    let uses: [SecretUse]
    @Binding var busy: Bool
    let onSaved: (String, String) -> Void
    let onCard: () -> Void
    let onBack: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    // memory only (@State, never @SceneStorage/@AppStorage): the value goes when the sheet goes
    @State private var original = ""
    @State private var value = ""
    @State private var shown = false
    @State private var use: SecretUse = .serviceKey
    @State private var label = ""
    @State private var host = ""
    @State private var site = ""
    @State private var user = ""
    @State private var form: KeyForm?
    @State private var others: [String: String] = [:]
    @State private var err = ""
    @State private var ready = false

    private var found: SecretFound { ask.found }

    private var name: String {
        let t = { (s: String) in s.trimmingCharacters(in: .whitespacesAndNewlines) }
        switch use {
        case .aiKey: return t(found.label).isEmpty ? found.target.id : found.label
        case .connectorKey:
            if let n = form?.name, !t(n).isEmpty { return n }
            return t(found.label).isEmpty ? found.target.id : found.label
        case .login: return t(label).isEmpty ? t(site) : t(label)
        case .serviceKey: return t(label).isEmpty ? t(host) : t(label)
        case .card: return ""
        }
    }

    private var canSave: Bool {
        let blank = { (s: String) in s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard !busy, value.trimmingCharacters(in: .whitespacesAndNewlines).count >= (use == .login ? 1 : 8) else { return false }
        switch use {
        case .aiKey: return !found.target.id.isEmpty
        case .connectorKey:
            guard let form else { return false }
            return form.fields.filter { !$0.secret }.allSatisfy { !blank(others[$0.id] ?? "") }
        case .login: return !blank(site) && !blank(user)
        case .serviceKey: return !blank(host)
        case .card: return false
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L(R.string.secret_save)).font(KeloFont.titleLarge).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
            if uses.count > 1 {
                FieldLabel(L(R.string.secret_for))
                FlowLayout(spacing: 8, lineSpacing: 8) {
                    ForEach(uses, id: \.self) { u in
                        ChoiceChip(L(useText(u)), selected: use == u) { use = u; err = "" }
                    }
                }
            }
            if use == .card {
                MutedText(L(R.string.secret_card_help), small: true)
                PrimaryButton(L(R.string.secret_card_next), action: onCard)
                LinkButton(L(R.string.back), action: onBack)
            } else {
                fields
                if !err.isEmpty { ErrorText(err, small: true) }
                PrimaryButton(L(busy ? R.string.vault_saving : R.string.save), enabled: canSave, busy: busy) { save() }
                    .accessibilityIdentifier("kelo.secret.saveNow")
                    .padding(.top, 4)
                LinkButton(L(R.string.back)) { if !busy { onBack() } }
            }
        }
        .keloSensitive()
        .onAppear {
            guard !ready else { return }
            ready = true
            original = ask.values.first ?? ""
            value = original
            use = uses.first ?? .serviceKey
            label = found.label
            host = SecretGuard.hostGuess(found.label)
        }
        .onDisappear { value = ""; original = "" }
        .task(id: use) {
            guard use == .connectorKey, form == nil, let api = app.api else { return }
            form = try? await SecretSave.connectorForm(api, connector: found.target.id)
        }
    }

    @ViewBuilder private var fields: some View {
        if (use == .aiKey || use == .connectorKey) && !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            Text(L(R.string.secret_key_for, name)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
        switch use {
        case .connectorKey:
            if let form {
                ForEach(form.fields.filter { !$0.secret }, id: \.id) { f in
                    FieldLabel(f.label.isEmpty ? f.id : f.label)
                    KeloTextField(f.placeholder.isEmpty ? f.label : f.placeholder,
                                  text: Binding(get: { others[f.id] ?? "" }, set: { others[f.id] = $0 }), keyboard: .ascii)
                }
            } else {
                SkeletonRows(rows: 1)
            }
        case .login:
            FieldLabel(L(R.string.vault_login_site))
            KeloTextField(L(R.string.vault_login_site_placeholder), text: $site, keyboard: .url)
            FieldLabel(L(R.string.vault_username))
            KeloTextField(L(R.string.vault_username), text: $user, keyboard: .email, maxLength: 200)
            FieldLabel(L(R.string.secret_label))
            KeloTextField(L(R.string.vault_login_name_placeholder), text: $label, maxLength: 80)
        case .serviceKey:
            FieldLabel(L(R.string.secret_label))
            KeloTextField(L(R.string.secret_label), text: $label, maxLength: 80)
            FieldLabel(L(R.string.secret_host))
            KeloTextField(L(R.string.secret_host_hint), text: $host, keyboard: .url)
        default:
            EmptyView()
        }
        FieldLabel(L(use == .login ? R.string.vault_password : R.string.secret_value))
        HStack(spacing: 4) {
            KeloTextField(L(R.string.secret_value), text: $value, secure: !shown, keyboard: .ascii, maxLength: 4096)
                .accessibilityIdentifier("kelo.secret.value")
            Button { shown.toggle() } label: {
                KeloIconView(.eye, size: 20, tint: shown ? c.accent : c.muted).frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L(shown ? R.string.secret_hide : R.string.secret_show))
        }
    }

    private func save() {
        busy = true; err = ""
        let v = value, chosen = use, connector = form, extra = others
        let site = site, label = label, user = user, host = host, ref = name
        let target = found.target.id
        Task {
            defer { busy = false }
            do {
                let api = try app.requireApi()
                switch chosen {
                case .aiKey: try await SecretSave.aiKey(api, provider: target, value: v)
                case .connectorKey:
                    guard let connector else { return }
                    try await SecretSave.connectorKey(api, form: connector, value: v, others: extra)
                case .login: try await SecretSave.login(api, site: site, label: label, username: user, password: v)
                case .serviceKey: try await SecretSave.serviceKey(api, host: host, label: label, value: v)
                case .card: return
                }
                value = ""
                let shownName = ref.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? L(useText(chosen)) : ref
                Haptics.success()
                onSaved(original, L(R.string.secret_saved_ref, shownName))
            } catch let e as ApiError {
                // as Android: a network failure reads "Can't reach the server", never the transport's own text
                err = e.isNetwork ? L(R.string.agent_offline) : (e.detail.isEmpty ? app.message(for: e) : e.detail)
            } catch {
                err = app.message(for: error)
            }
        }
    }
}

private func useText(_ u: SecretUse) -> StringKey {
    switch u {
    case .aiKey: R.string.secret_use_ai
    case .connectorKey: R.string.secret_use_connector
    case .card: R.string.secret_use_card
    case .login: R.string.secret_use_login
    case .serviceKey: R.string.secret_use_service
    }
}
