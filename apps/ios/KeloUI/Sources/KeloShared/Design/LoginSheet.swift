import SwiftUI
import KeloCore

// The compact card and the bottom sheet for a login or a service key the agent asked for (one look for both ways it can ask: a secret
// request in the chat, and a keychain item behind a follow-up). The card: key icon, title, "for <site>", one button, and a small
// "Not now". The sheet: key icon, title, "for <site>", the user name already filled when the agent knew it (editable), the password
// with Show, one checkbox (checked = saved once a sign-in with it works, unchecked = used once), Save and a plain Cancel that only closes
// the sheet. A service key has the key field and no checkbox. The value never leaves the sheet except to the vault (SecretAnswerModel /
// SecureEntrySaveModel); nothing here keeps it.

/// What the card and the sheet decide (pure; the views and the tests share it).
public enum LoginSheetRules {
    /// The checkbox: checked keeps the login once a sign-in with it works, unchecked uses it once.
    public static func mode(saveInKeychain: Bool) -> SecretLoginMode { saveInKeychain ? .afterSuccess : .once }

    /// The box is checked from the start.
    public static let saveInKeychainByDefault = true

    /// Save is on only when every field is filled (a login: user name and password; a key: a key the server accepts the shape of).
    public static func ready(login: Bool, user: String, pass: String) -> Bool {
        login ? SecretEntryRules.loginReady(user: user, pass: pass) : SecretEntryRules.keyReady(pass)
    }

    public static func title(login: Bool) -> StringKey { login ? R.string.ios_login_add_password : R.string.ios_login_add_key }

    /// "for example.com"; nil when there is no site to name.
    public static func forSite(hosts: [String]) -> StringKey? { hosts.isEmpty ? nil : R.string.ios_login_for_site }

    /// The short message after a save.
    public static func addedKey(login: Bool) -> StringKey { login ? R.string.ios_login_added : R.string.ios_login_key_added }

    /// The user name shown first: what the agent knew (cleaned), else empty.
    public static func initialUser(_ hint: String) -> String { UsernameHint.clean(hint) }
}

/// "Tell the agent to continue" happens once per card, right after the first successful save (a second tap or a retry never repeats it).
public struct ContinueOnce: Equatable, Sendable {
    public private(set) var sent = false
    public init() {}
    /// True the first time only.
    public mutating func take() -> Bool {
        if sent { return false }
        sent = true
        return true
    }
}

/// The key icon over a title and "for <site>" (the card shows it left-aligned, the sheet centred).
private struct LoginHead: View {
    let login: Bool
    let hosts: [String]
    var centered = false
    @Environment(\.kelo) private var c

    var body: some View {
        let alignment: HorizontalAlignment = centered ? .center : .leading
        VStack(alignment: alignment, spacing: 6) {
            KeloIconView(.key, size: centered ? 32 : 22, tint: c.ink)
            Text(L(LoginSheetRules.title(login: login))).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                .multilineTextAlignment(centered ? .center : .leading)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            if let key = LoginSheetRules.forSite(hosts: hosts) {
                MutedText(L(key, SecretRules.hostsText(hosts)), small: true)
                    .multilineTextAlignment(centered ? .center : .leading)
            }
        }
        .frame(maxWidth: .infinity, alignment: centered ? .center : .leading)
    }
}

/// The compact card in the chat (the caller wraps it in a CardBox): what is asked, for which site, one button, "Not now".
public struct LoginCompactCard: View {
    let login: Bool
    let hosts: [String]
    let reason: String?
    let onOpen: () -> Void
    let onNotNow: () -> Void
    @Environment(\.kelo) private var c

    public init(login: Bool, hosts: [String], reason: String?, onOpen: @escaping () -> Void, onNotNow: @escaping () -> Void) {
        self.login = login; self.hosts = hosts; self.reason = reason; self.onOpen = onOpen; self.onNotNow = onNotNow
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LoginHead(login: login, hosts: hosts)
            if let reason {
                Text(reason).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            PrimaryButton(L(R.string.secret_enter), action: onOpen)
                .accessibilityIdentifier("kelo.secretEntry.open")
            HStack {
                Spacer(minLength: 0)
                LinkButton(L(R.string.ios_login_not_now), action: onNotNow).accessibilityIdentifier("kelo.secretEntry.cancel")
                Spacer(minLength: 0)
            }
        }
    }
}

/// One checkbox row; the words wrap, the row stays at least 44 points tall.
struct LoginCheckbox: View {
    let text: String
    @Binding var on: Bool
    @Environment(\.kelo) private var c

    var body: some View {
        Button { on.toggle() } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: on ? "checkmark.square.fill" : "square").font(.system(size: 22))
                    .foregroundStyle(on ? c.accent : c.muted).padding(.top, 1)
                Text(text).font(KeloFont.body).foregroundStyle(c.ink).multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? [.isButton, .isSelected] : .isButton)
    }
}

/// The sheet's content. `onSave(user, password, mode)` runs the one call; `busy` / `problem` come from the model that runs it. The fields
/// live in memory only and are cleared when the sheet goes.
public struct LoginSheetForm: View {
    enum Field: Hashable { case user, pass }
    let login: Bool
    let hosts: [String]
    let check: Bool
    let busy: Bool
    let problem: String
    let onSave: (String, String, SecretLoginMode) -> Void
    let onCancel: () -> Void
    @State private var user: String
    @State private var pass = ""
    @State private var keep = LoginSheetRules.saveInKeychainByDefault
    @FocusState private var focus: Field?

    public init(login: Bool, hosts: [String], hint: String = "", check: Bool = false, busy: Bool, problem: String,
                onSave: @escaping (String, String, SecretLoginMode) -> Void, onCancel: @escaping () -> Void) {
        self.login = login; self.hosts = hosts; self.check = check; self.busy = busy; self.problem = problem
        self.onSave = onSave; self.onCancel = onCancel
        _user = State(initialValue: LoginSheetRules.initialUser(hint))
    }

    private var ready: Bool { !busy && LoginSheetRules.ready(login: login, user: user, pass: pass) }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            LoginHead(login: login, hosts: hosts, centered: true)
            if login {
                SecretValueField(label: L(R.string.vault_username), value: $user, keyboard: .email, masked: false, content: .username, submitLabel: .next) {
                    focus = .pass
                }
                .focused($focus, equals: .user)
                .accessibilityIdentifier("kelo.secretEntry.user")
            }
            SecretValueField(label: L(login ? R.string.vault_password : R.string.secret_field_key), value: $pass, keyboard: .ascii,
                             content: login ? .password : .none, submitLabel: .done) {
                if ready { save() }
            }
            .focused($focus, equals: .pass)
            .accessibilityIdentifier(login ? "kelo.secretEntry.password" : "kelo.secretEntry.key")
            if login { LoginCheckbox(text: L(R.string.ios_login_save_keychain), on: $keep).accessibilityIdentifier("kelo.secretEntry.mode") }
            if !login && check { MutedText(L(R.string.secret_check_note), small: true) }
            if !problem.isEmpty { ErrorText(problem, small: true) }
            PrimaryButton(L(SecretEntryWords.saveLabel(busy: busy, check: !login && check)), enabled: ready, busy: busy) { save() }
                .accessibilityIdentifier("kelo.secretEntry.save")
            HStack {
                Spacer(minLength: 0)
                LinkButton(L(R.string.cancel), action: onCancel).accessibilityIdentifier("kelo.secretEntry.sheetCancel")
                Spacer(minLength: 0)
            }
        }
        .keloSensitive()
        // the keyboard opens by itself only where typing is certain (an empty user name, or a key); a filled-in user name leaves
        // the sheet at its fitted height until a field is tapped (the keyboard would stretch it to full height)
        .onAppear { focus = login ? (user.isEmpty ? .user : nil) : .pass }
        .onDisappear { user = ""; pass = "" }
    }

    private func save() {
        onSave(user.trimmingCharacters(in: .whitespacesAndNewlines), login ? pass : pass.trimmingCharacters(in: .whitespacesAndNewlines),
               LoginSheetRules.mode(saveInKeychain: keep))
    }
}

/// The bottom sheet around `LoginSheetForm`: scrolls with the keyboard, detents medium and large.
public struct LoginSheetFrame<Content: View>: View {
    let content: Content
    @Environment(\.kelo) private var c
    /// The measured height of the content (a first guess until measured; the selection follows it): the sheet opens just that tall, and can still be pulled to large.
    @State private var fitted: CGFloat = 420
    @State private var selected: PresentationDetent = .height(420)
    public init(@ViewBuilder content: () -> Content) { self.content = content() }

    public var body: some View {
        NavigationStack {
            ScrollView {
                // top room so the drag handle stands alone above the key icon
                content.padding(.horizontal, 20).padding(.top, 48).padding(.bottom, 24).frame(maxWidth: .infinity)
                    .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { h in
                        if h > 0 { fitted = h; selected = .height(h) }
                    }
            }
            .scrollDismissesKeyboard(.interactively)
            .background(c.bg.ignoresSafeArea())
        }
        .presentationDetents([.height(fitted), .large], selection: $selected)
        .presentationDragIndicator(.visible)
    }
}
