import SwiftUI
import KeloCore
import KeloShared

/// A follow-up's buttons under the agent's message (sign in, allow, go on, ask later, stop asking); once one was used, one line
/// says what happened. 로그인하기 opens the sign-in sheet here.
struct MessageFollowups: View {
    let followup: Followup
    let model: ChatModel
    @State private var login: LoginName?

    struct LoginName: Identifiable { let name: String; var id: String { name } }

    var body: some View {
        if let outcome = ChatReducer.closedOutcome(followup, local: model.outcomes[followup.id]) {
            ResultLine(icon: outcome == .gone ? .clock : .check, text: ChatWords.outcome(outcome), muted: outcome == .gone)
        } else {
            FollowupButtons(buttons: FollowupRules.buttons(followup.actions, ownId: followup.id).map(\.step),
                            busy: model.acting.contains(followup.id)) { step in
                Haptics.tick()
                if case .openLogin(let name) = step { login = LoginName(name: name) } else { model.runAction(followup, step: step) }
            }
            .keloSheet(item: $login, detents: [.large]) { l in
                SignInSheet(name: l.name, onNotify: { text in model.actionSignedIn(followup); model.send(text) }, onClose: { login = nil })
            }
        }
    }
}

/// The buttons under an agent's message or on a card: they wrap onto more lines rather than squeeze. One filled button at most.
struct FollowupButtons: View {
    let buttons: [FollowupStep]
    let busy: Bool
    let onPress: (FollowupStep) -> Void
    @Environment(\.kelo) private var c

    private func primary(_ s: FollowupStep) -> Bool {
        switch s {
        case .openLogin, .retry, .resumeWorker: return true
        case .answer(_, let approve): return approve
        default: return false
        }
    }

    var body: some View {
        if buttons.isEmpty {
            EmptyView()
        } else if busy {
            ProgressView().controlSize(.small).padding(.vertical, 6)
        } else {
            let main = buttons.firstIndex(where: primary)
            FlowLayout(spacing: 8, lineSpacing: 8) {
                ForEach(Array(buttons.enumerated()), id: \.offset) { i, step in
                    Button { onPress(step) } label: {
                        Text(ChatWords.action(step)).font(KeloFont.button).multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 14).padding(.vertical, 8)
                            .foregroundStyle(i == main ? c.onAccent : c.ink)
                            .background(Capsule().fill(i == main ? c.accent : c.card))
                            .overlay(Capsule().stroke(i == main ? .clear : c.line, lineWidth: 1))
                    }
                    .buttonStyle(PressShrinkStyle())
                }
            }
        }
    }
}

/// The sign-in sheet for one Kelo keychain item, opened from a follow-up button: the same secure form the chat shows.
struct SignInSheet: View {
    let name: String
    let onNotify: (String) -> Void
    let onClose: () -> Void
    @Environment(\.keloApi) private var api
    @State private var lookup = SecureLookupModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.signin_sheet_title), onClose: onClose)
                switch lookup.state {
                case 0: ProgressView()
                case 1: SecureCard(name: name) { text in onNotify(text); onClose() }
                default: MutedText(L(R.string.signin_sheet_gone), small: true)
                }
            }
            .padding(.horizontal, 16).padding(.bottom, 24)
        }
        .task { await lookup.load(name, api: api) }
    }
}

/// The card that appears when the agent asks for a login or a key (Android vault/SecureEntry.kt SecureCard). `onNotify` sends the
/// line that tells the agent. The value goes to the server and is never shown again; the agent never sees it.
struct SecureCard: View {
    let name: String
    let onNotify: ((String) -> Void)?
    @Environment(\.keloApi) private var api
    @Environment(\.kelo) private var c
    @State private var lookup = SecureLookupModel()
    @State private var done: SecretLoginMode?

    var body: some View {
        let closed = lookup.closed
        Group {
            if let it = lookup.item {
                let what = L(it.isLogin ? R.string.vault_what_login : R.string.vault_what_key, it.label)
                CardBox {
                    if closed {
                        MutedText(L(R.string.vault_card_cancelled, what), small: true)
                    } else {
                        HStack(spacing: 8) {
                            KeloIconView(.shield, size: 20, tint: c.ink)
                            Text(L(R.string.vault_card_needed, what)).font(KeloFont.button).foregroundStyle(c.ink)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        MutedText(L(R.string.vault_card_hosts, it.hosts.joined(separator: ", ")), small: true)
                        if let d = done {
                            Text(L(d == .afterSuccess ? R.string.vault_card_done_after_success
                                   : d == .once ? R.string.vault_card_done_once : R.string.vault_card_done_now))
                                .font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                            if let onNotify {
                                let text = L(d == .now ? R.string.vault_card_notify_saved : R.string.vault_card_notify_entered, what)
                                PrimaryButton(L(R.string.notify_agent)) { onNotify(text) }
                            }
                        } else {
                            SecureEntryForm(item: it, defaultMode: it.isLogin ? .afterSuccess : .now) { done = $0 }
                            // nothing was entered: the request is forgotten, and nothing is left in the keychain
                            LinkButton(L(R.string.cancel)) { lookup.discard(name, api: api) }
                        }
                    }
                }
            } else {
                EmptyView()
            }
        }
        .task(id: name) { await lookup.load(name, api: api) }
    }
}

/// The form where a login or a key is typed. A login's fields are marked as username / password, so iOS offers the saved
/// passwords above the keyboard by itself (no extra button, no hint text); a service key is never offered for AutoFill.
struct SecureEntryForm: View {
    let item: ChatVaultItem
    let defaultMode: SecretLoginMode
    let onSaved: (SecretLoginMode) -> Void
    @Environment(\.keloApi) private var api
    @Environment(\.kelo) private var c
    @State private var user = ""
    @State private var pass = ""
    @State private var mode: SecretLoginMode?
    @State private var save = SecureEntrySaveModel()
    @FocusState private var focus: Field?

    enum Field { case user, pass }

    var body: some View {
        let login = item.isLogin
        let chosen = mode ?? defaultMode
        VStack(alignment: .leading, spacing: 10) {
            if login {
                FieldLabel(L(R.string.vault_username))
                field(TextField("", text: $user, prompt: Text(L(R.string.vault_username)).foregroundStyle(c.muted)), focused: .user)
                    .textContentType(.username)
                    .keloKeyboard(.email)
            }
            FieldLabel(L(login ? R.string.vault_password : R.string.vault_key))
            field(SecureField("", text: $pass, prompt: Text(L(login ? R.string.vault_password : R.string.vault_key)).foregroundStyle(c.muted)),
                  focused: .pass)
                .textContentType(login ? .password : nil)
                .keloKeyboard(.ascii)
            if login {
                FieldLabel(L(R.string.vault_mode))
                LoginModeMenu(mode: chosen) { mode = $0 }
            }
            let ready = !pass.isEmpty && (login ? !user.trimmingCharacters(in: .whitespaces).isEmpty : pass.trimmingCharacters(in: .whitespaces).count >= 8)
            PrimaryButton(L(save.busy ? R.string.vault_saving : R.string.vault_save), enabled: !save.busy && ready) { submit(mode: chosen) }
            if !save.msg.isEmpty { ErrorText(save.msg, small: true) }
            MutedText(L(R.string.vault_footer), small: true)
        }
        .keloSensitive()
        .onAppear { focus = login ? .user : .pass }
    }

    private func field<F: View>(_ f: F, focused: Field) -> some View {
        f.lineLimit(1).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
            .focused($focus, equals: focused)
            .padding(.horizontal, 14).padding(.vertical, 13)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(focus == focused ? c.accent : c.line, lineWidth: focus == focused ? 2 : 1))
    }

    private func submit(mode: SecretLoginMode) {
        save.save(item, user: user, pass: pass, mode: mode, api: api) {
            user = ""; pass = ""
            onSaved(mode)
        }
    }
}
