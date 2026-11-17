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
                SignInSheet(name: l.name, onNotify: { text in model.actionSignedIn(followup); model.send(text) },
                            onAdded: { model.entryToast = $0 }, onClose: { login = nil })
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
    var onAdded: (String) -> Void = { _ in }
    let onClose: () -> Void
    @Environment(\.keloApi) private var api
    @State private var lookup = SecureLookupModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.signin_sheet_title), onClose: onClose)
                switch lookup.state {
                case 0: ProgressView()
                case 1: SecureCard(name: name, embedded: true, onClose: onClose, onAdded: onAdded, onNotify: { text in onNotify(text); onClose() })
                default: MutedText(L(R.string.signin_sheet_gone), small: true)
                }
            }
            .padding(.horizontal, 16).padding(.bottom, 24)
        }
        .task { await lookup.load(name, api: api) }
    }
}

/// The card that appears when the agent asks for a login or a key (Android vault/SecureEntry.kt SecureCard): a compact card whose
/// button opens a bottom sheet with the form (`embedded`: already inside a sheet, so the form is shown right there). `onNotify` sends
/// the line that tells the agent, once, right after a save; `onAdded` gets the short message to show. The value goes to the server and
/// is never shown again; the agent never sees it.
struct SecureCard: View {
    let name: String
    var embedded = false
    var onClose: () -> Void = {}
    var onAdded: (String) -> Void = { _ in }
    let onNotify: ((String) -> Void)?
    @Environment(\.keloApi) private var api
    @State private var lookup = SecureLookupModel()
    @State private var save = SecureEntrySaveModel()
    @State private var showSheet = false
    @State private var once = ContinueOnce()

    var body: some View {
        let closed = lookup.closed
        Group {
            if let it = lookup.item {
                let what = L(it.isLogin ? R.string.vault_what_login : R.string.vault_what_key, it.label)
                if embedded {
                    LoginSheetForm(login: it.isLogin, hosts: it.hosts, hint: it.usernameHint, busy: save.busy, problem: save.msg,
                                   onSave: { u, p, m in submit(it, what, u, p, m) }, onCancel: onClose)
                        .id(name)
                } else {
                    CardBox {
                        if closed {
                            MutedText(L(R.string.vault_card_cancelled, what), small: true)
                        } else {
                            // nothing was entered: "Not now" forgets the request, and nothing is left in the keychain
                            LoginCompactCard(login: it.isLogin, hosts: it.hosts, reason: nil, onOpen: { showSheet = true },
                                             onNotNow: { lookup.discard(name, api: api) })
                        }
                    }
                    .keloSensitive()
                    .sheet(isPresented: $showSheet) {
                        LoginSheetFrame {
                            LoginSheetForm(login: it.isLogin, hosts: it.hosts, hint: it.usernameHint, busy: save.busy, problem: save.msg,
                                           onSave: { u, p, m in submit(it, what, u, p, m) }, onCancel: { showSheet = false })
                        }
                    }
                }
            } else {
                EmptyView()
            }
        }
        .task(id: name) { await lookup.load(name, api: api) }
    }

    /// Saved: the sheet closes, a short message says so, and the agent carries on by itself (told once).
    private func submit(_ it: ChatVaultItem, _ what: String, _ user: String, _ pass: String, _ mode: SecretLoginMode) {
        save.save(it, user: user, pass: pass, mode: mode, api: api) {
            showSheet = false
            guard once.take() else { return }
            onAdded(L(LoginSheetRules.addedKey(login: it.isLogin)))
            onNotify?(L(R.string.vault_card_notify_entered, what))
        }
    }
}
