import SwiftUI
import KeloCore

// A secure entry the agent asked for, inline in the chat and in the Keychain's "waiting for input" list (Android
// chat/vault/SecretEntryCard.kt): what it is for, where the value will be sent, the field(s), Save and Cancel. The value goes from
// here straight to the vault (a key or a login) or to the wallet (a card, through the wallet's own card form and its sealing) and is
// never put in the chat, the model's context, the logs or a notification. Saving is the consent; nothing else is asked first. An
// answered entry folds into one line.

/// The words a secure entry shows (pure; the views and the tests share it).
public enum SecretEntryWords {
    /// The heading of an open entry: the key's name, the site of a login, or "add a payment card".
    public static func title(_ r: SecretRequest) -> (StringKey, String?) {
        if r.isCard { return (R.string.secret_title_card, nil) }
        if r.isPin { return (R.string.pin_secret_title, r.hosts.first ?? r.name) }
        if r.isLogin { return (R.string.secret_title_login, r.label.isEmpty ? (r.hosts.first ?? r.name) : r.label) }
        return (R.string.secret_title_key, r.label.isEmpty ? r.name : r.label)
    }

    /// What the entry is for, under the heading (nil: nothing to add; a card's label already says it).
    public static func reason(_ r: SecretRequest) -> String? {
        let reason = r.reason.trimmingCharacters(in: .whitespacesAndNewlines)
        if !reason.isEmpty && reason != r.label { return reason }
        if r.isCard && !r.label.trimmingCharacters(in: .whitespaces).isEmpty { return r.label }
        return nil
    }

    /// Where the value goes: the hosts a key or a login is sent to, the site a card pays on, or the wallet.
    public static func destination(_ r: SecretRequest) -> (StringKey, String?) {
        let hosts = SecretRules.hostsText(r.hosts)
        if r.isPin { return (R.string.pin_secret_where, nil) }
        if r.isCard { return hosts.isEmpty ? (R.string.secret_card_wallet, nil) : (R.string.secret_card_site, hosts) }
        return (R.string.secret_hosts_only, hosts)
    }

    /// The one line an answered entry leaves, with its icon and whether it is muted.
    public static func folded(_ r: SecretRequest) -> (icon: KeloIcon, key: StringKey, arg: String?, muted: Bool) {
        switch r.state {
        case SecretRequest.stateSaved:
            let name = r.label.isEmpty ? r.name : r.label
            if r.isCard {
                let card = r.result?.card?.label ?? ""
                return (.shield, R.string.secret_done_card, card.isEmpty ? r.label : card, false)
            }
            if r.isPin { return (.shield, R.string.pin_secret_done, r.hosts.first ?? r.name, false) }
            if r.isLogin { return (.shield, R.string.secret_done_login, name, false) }
            if r.result?.checked == "ok" { return (.shield, R.string.secret_done_checked, name, false) }
            return (.shield, R.string.secret_done_key, name, false)
        case SecretRequest.stateCancelled: return (.close, R.string.secret_cancelled, nil, true)
        default: return (.clock, R.string.secret_gone, nil, true)
        }
    }

    /// The kind in a few words, for the Keychain's waiting list ("Service key · api.example.com").
    public static func kindLine(_ r: SecretRequest) -> (StringKey, String) {
        let kind = r.isCard ? R.string.secret_kind_card : (r.isLogin ? R.string.secret_kind_login : R.string.secret_kind_key)
        return (kind, SecretRules.hostsText(r.hosts))
    }

    /// The Save button's words: "Checking…" while a key is tried, "Saving…" otherwise.
    public static func saveLabel(busy: Bool, check: Bool) -> StringKey {
        busy ? (check ? R.string.secret_checking : R.string.vault_saving) : R.string.save
    }
}

@MainActor private func words(_ t: (StringKey, String?)) -> String {
    if let a = t.1 { return L(t.0, a) }
    return L(t.0)
}

/// The secure entry. `onSaved`: the server's answer (a reply it started to carry on, if any) and how it went; `onCancel`: Cancel was
/// pressed; `onGone`: the server no longer has it open (answered on another phone, or it ran out). `framed`: in its own card (the
/// chat); without, it sits in a card the page draws (the Keychain). `returnTo`: the tab a card entry comes back to after the wallet's
/// card form (the chat; nil stays on the wallet).
public struct SecretEntryCard: View {
    let request: SecretRequest
    var framed = true
    var returnTo: KeloTab?
    let onSaved: (SecretSaveAnswer, SecretResult?) -> Void
    let onCancel: () -> Void
    var onGone: () -> Void = {}
    @State private var showSheet = false

    public init(request: SecretRequest, framed: Bool = true, returnTo: KeloTab? = nil,
                onSaved: @escaping (SecretSaveAnswer, SecretResult?) -> Void, onCancel: @escaping () -> Void, onGone: @escaping () -> Void = {}) {
        self.request = request; self.framed = framed; self.returnTo = returnTo
        self.onSaved = onSaved; self.onCancel = onCancel; self.onGone = onGone
    }

    public var body: some View {
        Group {
            if !request.open {
                if framed {
                    CardBox { SecretFoldedLine(r: request) }.keloSensitive()
                } else {
                    SecretFoldedLine(r: request).keloSensitive()
                }
            } else if framed {
                CardBox {
                    SecretSummaryEntry(r: request, onOpen: { showSheet = true }, onCancel: onCancel)
                }
                .keloSensitive()
                .sheet(isPresented: $showSheet) {
                    SecretSheetView(
                        request: request,
                        returnTo: returnTo,
                        onSaved: { ans, res in
                            showSheet = false
                            onSaved(ans, res)
                        },
                        onCancel: {
                            showSheet = false
                            onCancel()
                        },
                        onGone: {
                            showSheet = false
                            onGone()
                        }
                    )
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
                }
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    SecretOpenEntry(r: request, returnTo: returnTo, onSaved: onSaved, onCancel: onCancel, onGone: onGone)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .keloSensitive()
            }
        }
    }
}

/// Compact summary shown in chat: site, what is asked, cancel, and primary "Enter" / "입력하기" button.
private struct SecretSummaryEntry: View {
    let r: SecretRequest
    let onOpen: () -> Void
    let onCancel: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                KeloIconView(r.isCard ? .card : .shield, size: 20, tint: c.ink)
                Text(words(SecretEntryWords.title(r)))
                    .font(KeloFont.titleMedium)
                    .foregroundStyle(c.ink)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                LinkButton(L(R.string.cancel), action: onCancel)
                    .accessibilityIdentifier("kelo.secretEntry.cancel")
            }
            if let reason = SecretEntryWords.reason(r) {
                Text(reason)
                    .font(KeloFont.body)
                    .foregroundStyle(c.ink)
                    .lineLimit(2)
                    .truncationMode(.tail)
            }
            MutedText(words(SecretEntryWords.destination(r)), small: true)
                .lineLimit(1)
            PrimaryButton(L(R.string.secret_enter), action: onOpen)
                .accessibilityIdentifier("kelo.secretEntry.open")
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
    }
}

/// Modal bottom sheet holding the full secret entry form.
private struct SecretSheetView: View {
    let request: SecretRequest
    let returnTo: KeloTab?
    let onSaved: (SecretSaveAnswer, SecretResult?) -> Void
    let onCancel: () -> Void
    let onGone: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    SecretOpenEntry(
                        r: request,
                        returnTo: returnTo,
                        onSaved: onSaved,
                        onCancel: onCancel,
                        onGone: onGone
                    )
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(c.bg.ignoresSafeArea())
        }
    }
}

/// .approval / .secure-card: a full-width card in the chat list.
public struct CardBox<Content: View>: View {
    let content: Content
    @Environment(\.kelo) private var c
    public init(@ViewBuilder content: () -> Content) { self.content = content() }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14).padding(.vertical, 12)
            .background(RoundedRectangle(cornerRadius: 16).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(c.line, lineWidth: 1))
    }
}

/// The one line an answered entry leaves.
private struct SecretFoldedLine: View {
    let r: SecretRequest
    @Environment(\.kelo) private var c

    var body: some View {
        let f = SecretEntryWords.folded(r)
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            KeloIconView(f.icon, size: 16, tint: f.muted ? c.muted : c.ink)
            Text(words((f.key, f.arg))).font(KeloFont.small).foregroundStyle(f.muted ? c.muted : c.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct SecretOpenEntry: View {
    let r: SecretRequest
    let returnTo: KeloTab?
    let onSaved: (SecretSaveAnswer, SecretResult?) -> Void
    let onCancel: () -> Void
    let onGone: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            KeloIconView(r.isCard ? .card : .shield, size: 20, tint: c.ink).padding(.top, 2)
            Text(words(SecretEntryWords.title(r))).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            LinkButton(L(R.string.cancel), action: onCancel)
                .accessibilityIdentifier("kelo.secretEntry.cancel")
        }
        if let reason = SecretEntryWords.reason(r) {
            Text(reason).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
        MutedText(words(SecretEntryWords.destination(r)), small: true)
        Group {
            if r.isCard {
                SecretCardEntry(r: r, returnTo: returnTo, onSaved: onSaved, onGone: onGone)
            } else if r.isPin {
                SecretPinEntry(r: r, onSaved: onSaved)
            } else if r.isLogin {
                SecretLoginEntry(r: r, onSaved: onSaved, onGone: onGone)
            } else {
                SecretKeyEntry(r: r, onSaved: onSaved, onGone: onGone)
            }
        }
        .id(r.id)
    }
}

/// A key or token: one masked field with Show. Tried once before it is saved when `check`.
private struct SecretKeyEntry: View {
    let r: SecretRequest
    let onSaved: (SecretSaveAnswer, SecretResult?) -> Void
    let onGone: () -> Void
    @Environment(\.keloApi) private var api
    // memory only (@State, never @SceneStorage/@AppStorage): the value goes when the entry goes
    @State private var value = ""
    @State private var answer = SecretAnswerModel()
    @FocusState private var focused: Bool
    private var busy: Bool { answer.busy }
    private var problem: String { answer.problem }

    var body: some View {
        SecretValueField(label: L(R.string.secret_field_key), value: $value, keyboard: .ascii, submitLabel: .done) {
            if !busy && SecretEntryRules.keyReady(value) { save() }
        }
        .focused($focused)
        .accessibilityIdentifier("kelo.secretEntry.key")
        if r.check { MutedText(L(R.string.secret_check_note), small: true) }
        if !problem.isEmpty { ErrorText(problem, small: true) }
        PrimaryButton(L(SecretEntryWords.saveLabel(busy: busy, check: r.check)), enabled: !busy && SecretEntryRules.keyReady(value), busy: busy) { save() }
            .accessibilityIdentifier("kelo.secretEntry.save")
        MutedText(L(R.string.secret_never_chat), small: true)
            .onDisappear { value = "" }
    }

    private func save() {
        let v = value.trimmingCharacters(in: .whitespacesAndNewlines)
        answer.saveKey(name: r.name, secret: v, api: api, saved: { got in
            value = ""
            onSaved(got, SecretResult(checked: got.checked ? "ok" : "unchecked"))
        }, onGone: onGone)
    }
}

/// A website login: user name and password (Show), and how it is kept (by default only once a sign-in with it works).
private struct SecretLoginEntry: View {
    enum Field: Hashable { case user, pass }
    let r: SecretRequest
    let onSaved: (SecretSaveAnswer, SecretResult?) -> Void
    let onGone: () -> Void
    @Environment(\.keloApi) private var api
    @State private var user = ""
    @State private var pass = ""
    @State private var mode: SecretLoginMode = .afterSuccess
    @State private var answer = SecretAnswerModel()
    @FocusState private var focus: Field?
    private var busy: Bool { answer.busy }
    private var problem: String { answer.problem }

    var body: some View {
        SecretValueField(label: L(R.string.vault_username), value: $user, keyboard: .email, masked: false, content: .username, submitLabel: .next) {
            focus = .pass
        }
        .focused($focus, equals: .user)
        .accessibilityIdentifier("kelo.secretEntry.user")
        SecretValueField(label: L(R.string.vault_password), value: $pass, keyboard: .ascii, content: .password, submitLabel: .done) {
            if !busy && SecretEntryRules.loginReady(user: user, pass: pass) { save() }
        }
        .focused($focus, equals: .pass)
        .accessibilityIdentifier("kelo.secretEntry.password")
        FieldLabel(L(R.string.vault_mode))
        LoginModeMenu(mode: mode) { mode = $0 }
        if !problem.isEmpty { ErrorText(problem, small: true) }
        PrimaryButton(L(SecretEntryWords.saveLabel(busy: busy, check: false)), enabled: !busy && SecretEntryRules.loginReady(user: user, pass: pass), busy: busy) { save() }
            .accessibilityIdentifier("kelo.secretEntry.save")
        MutedText(L(R.string.secret_never_chat), small: true)
            .onDisappear { user = ""; pass = "" }
    }

    private func save() {
        let u = user.trimmingCharacters(in: .whitespacesAndNewlines)
        answer.saveLogin(name: r.name, user: u, pass: pass, mode: mode, api: api, saved: { got in
            user = ""; pass = ""
            onSaved(got, SecretResult())
        }, onGone: onGone)
    }
}

/// A payment card: the wallet's own card form (number, expiry and CVC, nickname, the notice) behind its lock, sealed on this
/// iPhone; once the card is in the wallet the request is answered with it (its nickname and last four digits, `SecretCardWait`).
private struct SecretCardEntry: View {
    let r: SecretRequest
    let returnTo: KeloTab?
    let onSaved: (SecretSaveAnswer, SecretResult?) -> Void
    let onGone: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.keloApi) private var api
    @State private var answer = SecretAnswerModel()
    private var problem: String { answer.problem }

    var body: some View {
        if let wallet = app.wallet {
            // the wallet's own card form right here (Android CardEntry: AddCardForm inline), sealed on this iPhone
            wallet.cardForm { card in saved(card) }
            if !problem.isEmpty { ErrorText(problem, small: true) }
        } else {
            nextButton
        }
    }

    /// The card the form saved answers the entry (its nickname and last four digits only).
    private func saved(_ card: JSON) {
        let c = Lenient(card)
        // the card is in the wallet either way; an entry that is gone just folds
        answer.cardSaved(id: r.id, cardId: c.str("id"), api: api, saved: { got in
            onSaved(got, SecretResult(card: SecretCard(id: c.str("id"), label: c.str("label"))))
        }, onGone: onGone)
    }

    @ViewBuilder private var nextButton: some View {
        if !problem.isEmpty { ErrorText(problem, small: true) }
        PrimaryButton(L(R.string.secret_card_next)) {
            Haptics.tick()
            answer.clearProblem()
            app.secretCardWait.start(r.id, returnTo: returnTo)
            app.router.openMyPage(.walletAdd)
        }
        .accessibilityIdentifier("kelo.secretEntry.card")
        .onAppear { takeOutcome() }
        .onChange(of: app.secretCardWait.tick) { _, _ in takeOutcome() }
    }

    private func takeOutcome() {
        guard let o = app.secretCardWait.take(r.id) else { return }
        switch o.kind {
        case .saved: onSaved(SecretSaveAnswer(turnId: o.turnId), o.result)
        case .gone: onGone()   // the card is in the wallet either way; an entry that is gone just folds
        case .failed: answer.setProblem(o.detail.isEmpty ? L(R.string.secret_failed) : o.detail)
        }
    }
}

/// A site's payment PIN (request_payment_pin): the wallet's PIN form with the site fixed; the PIN is sealed with this iPhone's key
/// after Face ID and only that ciphertext goes to the server.
private struct SecretPinEntry: View {
    let r: SecretRequest
    let onSaved: (SecretSaveAnswer, SecretResult?) -> Void
    @Environment(AppModel.self) private var app

    var body: some View {
        if let wallet = app.wallet {
            wallet.pinForm(r.hosts.first ?? r.name, true, r.id) { turnId in onSaved(SecretSaveAnswer(turnId: turnId), SecretResult()) }
        }
    }
}

/// A labelled field; `masked` values show as dots until Show is pressed (the system's own paste works in it).
private struct SecretValueField: View {
    enum Content { case username, password, none }
    let label: String
    @Binding var value: String
    let keyboard: KeloKeyboard
    var masked = true
    var content: Content = .none
    var submitLabel: SubmitLabel? = nil
    var onSubmit: () -> Void = {}
    @Environment(\.kelo) private var c
    @State private var shown = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FieldLabel(label)
            HStack(spacing: 4) {
                // no placeholder: the label is right above, and the same words inside the empty field showed them twice ("키 또는 토큰")
                KeloTextField("", text: $value, secure: masked && !shown, keyboard: keyboard, maxLength: 4096, onSubmit: onSubmit)
                    .modifier(SecretContentType(kind: content))
                    .modifier(SecretSubmitLabel(label: submitLabel))
                    .accessibilityLabel(label)
                if masked {
                    Button { shown.toggle() } label: {
                        KeloIconView(shown ? .close : .eye, size: 20, tint: shown ? c.accent : c.muted).frame(width: 44, height: 44)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(L(shown ? R.string.secret_hide : R.string.secret_show))
                }
            }
        }
    }
}

private struct SecretSubmitLabel: ViewModifier {
    let label: SubmitLabel?
    func body(content: Content) -> some View {
        if let label {
            content.submitLabel(label)
        } else {
            content
        }
    }
}

/// Marks a field for the phone's own AutoFill (iOS only); a key field is marked as nothing so AutoFill stays away.
private struct SecretContentType: ViewModifier {
    let kind: SecretValueField.Content
    func body(content: Content) -> some View {
        #if os(iOS)
        switch kind {
        case .username: content.textContentType(.username)
        case .password: content.textContentType(.password)
        case .none: content.textContentType(nil)
        }
        #else
        content
        #endif
    }
}

/// How a login is kept, as one compact row: the chosen way's name (a tap lists the three ways, each with its one-line explanation),
/// and the chosen way's explanation under it.
public struct LoginModeMenu: View {
    let mode: SecretLoginMode
    let onPick: (SecretLoginMode) -> Void
    @Environment(\.kelo) private var c

    public init(mode: SecretLoginMode, onPick: @escaping (SecretLoginMode) -> Void) {
        self.mode = mode
        self.onPick = onPick
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Menu {
                ForEach(SecretLoginMode.allCases, id: \.self) { m in
                    Button(L(title(m))) { onPick(m) }
                }
            } label: {
                HStack {
                    Text(L(title(mode)))
                        .font(KeloFont.body)
                        .foregroundStyle(c.ink)
                    Spacer()
                    KeloIconView(.chevronRight, size: 16, tint: c.muted)
                        .rotationEffect(.degrees(90))
                }
                .padding(12)
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("kelo.secretEntry.mode")
            Text(L(help(mode)))
                .font(KeloFont.small)
                .foregroundStyle(c.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func title(_ m: SecretLoginMode) -> StringKey {
        switch m {
        case .afterSuccess: R.string.vault_mode_after_success
        case .now: R.string.vault_mode_now
        case .once: R.string.vault_mode_once
        }
    }

    private func help(_ m: SecretLoginMode) -> StringKey {
        switch m {
        case .afterSuccess: R.string.vault_mode_after_success_help
        case .now: R.string.vault_mode_now_help
        case .once: R.string.vault_mode_once_help
        }
    }
}

/// One way to keep a typed login (once a sign-in with it works / now / once): a radio row, the chosen row's explanation under its name.
public struct LoginModeRow: View {
    let mode: SecretLoginMode
    let on: Bool
    let pick: () -> Void
    @Environment(\.kelo) private var c

    public init(mode: SecretLoginMode, on: Bool, pick: @escaping () -> Void) { self.mode = mode; self.on = on; self.pick = pick }

    public var body: some View {
        let (title, help): (StringKey, StringKey) = switch mode {
        case .afterSuccess: (R.string.vault_mode_after_success, R.string.vault_mode_after_success_help)
        case .now: (R.string.vault_mode_now, R.string.vault_mode_now_help)
        case .once: (R.string.vault_mode_once, R.string.vault_mode_once_help)
        }
        Button(action: pick) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: on ? "largecircle.fill.circle" : "circle").foregroundStyle(on ? c.accent : c.muted).padding(.top, 2)
                VStack(alignment: .leading, spacing: 2) {
                    Text(L(title)).font(on ? KeloFont.titleSmall : KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    if on { Text(L(help)).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? [.isSelected, .isButton] : .isButton)
    }
}
