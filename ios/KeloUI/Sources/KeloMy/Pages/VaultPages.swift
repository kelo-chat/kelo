import SwiftUI
import KeloCore
import KeloShared

/// The vault's log actions and last results in words.
@MainActor enum VaultWords {
    static func action(_ a: String) -> String {
        let keys: [String: StringKey] = [
            "used": R.string.vault_action_used, "denied": R.string.vault_action_denied, "requested": R.string.vault_action_requested,
            "added": R.string.vault_action_added, "saved": R.string.vault_action_saved, "replaced": R.string.vault_action_replaced,
            "entered-once": R.string.vault_action_entered_once, "deleted": R.string.vault_action_deleted, "setting": R.string.vault_action_setting,
            "locked": R.string.vault_action_locked, "unlocked": R.string.vault_action_unlocked, "result": R.string.vault_action_result,
            "failed": R.string.vault_action_failed, "rejected": R.string.vault_action_rejected, "cancelled": R.string.vault_action_cancelled,
            "code-typed": R.string.vault_action_code_typed,
        ]
        return keys[a].map { L($0) } ?? a
    }

    static func result(_ r: String) -> String {
        let keys: [String: StringKey] = [
            "ok": R.string.vault_result_ok, "failed": R.string.vault_result_failed, "rejected": R.string.vault_result_rejected,
            "code_needed": R.string.vault_result_code_needed, "blocked": R.string.vault_result_blocked, "unknown": R.string.vault_result_unknown,
        ]
        return keys[r].map { L($0) } ?? r
    }

    static func place(_ p: Placement) -> String {
        switch p.type {
        case "custom_header": L(R.string.vault_place_header_named, p.header ?? "")
        case "query_param": L(R.string.vault_place_query_named, p.param ?? "")
        case "bearer_header": L(R.string.vault_place_bearer)
        case "url_path_segment": L(R.string.vault_place_path)
        default: ""
        }
    }
}

/// Every password and key the assistant may use, what each is for, and the lock. Values can be added or replaced, never read.
/// Opened only after the phone's own check; `noLock`: the phone has no passcode, so nothing could be checked and the page says so.
struct VaultPage: View {
    let noLock: Bool
    let api: KeloApi
    let vault: VaultModel
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss

    private var nav: MyNav { MyNav(app: app) }

    var body: some View {
        SubPage(L(R.string.vault_title), onBack: { dismiss() }) {
            if let v = vault.info { list(v) } else { MyCard { SkeletonRows(rows: 2) }; SkeletonRows(rows: 3) }
        }
        .keloSensitive()
        .task { await vault.load(); await vault.loadProviders() }
    }

    @ViewBuilder private func list(_ v: VaultState) -> some View {
        let logins = v.items.filter { $0.kind == VaultEntry.kindLogin }
        let mailboxes = v.items.filter { $0.kind == VaultEntry.kindEmail }
        let keys = v.items.filter { $0.kind == VaultEntry.kindApi }
        let ai = vault.providers.filter { $0.keySource == "app" }
        if noLock { MutedText(L(R.string.vault_gate_no_lock), small: true) }
        MyCard {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { lockTitle(v); Spacer(minLength: 8); lockButton(v) }
                VStack(alignment: .leading, spacing: 8) { lockTitle(v); lockButton(v) }
            }
            MutedText(v.locked ? L(R.string.vault_locked_body) : L(R.string.vault_safe_body), small: true)
        }
        // what the agent asked for and nobody has filled in yet, at the top
        WaitingEntries(api: api, vault: v) { vault.reload() }
            .padding(.horizontal, -16)
        Group {
            SectionLabel(L(R.string.vault_logins))
            ForEach(logins, id: \.name) { i in itemRow(i, icon: .globe) }
            MenuRow(.plus, L(R.string.vault_add_login)) { nav.push(.vaultAddLogin) }
            // "Use codes from my mail automatically": a code the site mails during a sign-in Kelo started is typed without asking
            MyCard {
                ToggleRow(L(R.string.vault_mail_codes_title),
                          detail: v.mailCodesAuto ? L(R.string.vault_mail_codes_on) : L(R.string.vault_mail_codes_off),
                          isOn: Binding(get: { v.mailCodesAuto }, set: { vault.setMailCodes($0) }))
            }
            .padding(.horizontal, 16)
            PaymentPinSection(api: api, locked: v.locked)
            if !mailboxes.isEmpty {
                SectionLabel(L(R.string.vault_mailboxes))
                ForEach(mailboxes, id: \.name) { i in itemRow(i, icon: .key, label: i.label.components(separatedBy: " · ").first ?? i.label) }
            }
            if !keys.isEmpty {
                SectionLabel(L(R.string.vault_keys))
                MutedText(L(R.string.vault_keys_help), small: true).padding(.horizontal, 16)
                ForEach(keys, id: \.name) { i in itemRow(i, icon: .key) }
            }
            if !ai.isEmpty {
                SectionLabel(L(R.string.vault_ai_keys))
                ForEach(ai) { p in
                    MenuRow(.key, p.displayName, value: p.keyHint.map { L(R.string.vault_key_hint, $0) } ?? L(R.string.vault_stored)) { nav.push(.vaultAi(p.name)) }
                }
            }
            SectionLabel(L(R.string.vault_records))
            MenuRow(.shield, L(R.string.vault_log)) { nav.push(.vaultLog) }
        }
        .padding(.horizontal, -16)
    }

    private func lockTitle(_ v: VaultState) -> some View { IconTitle(.shield, v.locked ? L(R.string.vault_locked_title) : L(R.string.vault_safe_title)) }

    @ViewBuilder private func lockButton(_ v: VaultState) -> some View {
        if v.locked { PrimaryButton(L(R.string.vault_unlock)) { vault.setLocked(!v.locked) }.frame(maxWidth: 200) }
        else { SecondaryButton(L(R.string.vault_lock)) { vault.setLocked(!v.locked) } }
    }

    @ViewBuilder private func itemRow(_ i: VaultEntry, icon: KeloIcon, label: String? = nil) -> some View {
        let stored = i.usernameHint ?? L(R.string.vault_stored)
        if i.isBad {
            MenuRow(icon, label ?? i.label, value: i.isLogin ? L(R.string.vault_status_login_failed) : L(R.string.vault_status_key_rejected),
                    valueColor: Color(hex: 0xC62828)) { nav.push(.vaultItem(i.name)) }
        } else if i.lastResult == "ok" {
            MenuRow(icon, label ?? i.label, value: L(R.string.vault_status_ok, stored)) { nav.push(.vaultItem(i.name)) }
        } else {
            MenuRow(icon, label ?? i.label, value: stored) { nav.push(.vaultItem(i.name)) }
        }
    }
}

/// "Waiting for input": the secure entries the agent asked for that are still open (GET /api/secret-requests, newest first; a
/// vault item waiting for its value shows here too). [Fill in] opens the same entry the chat shows, right here; [Delete] cancels it
/// (the agent is told). Nothing shows when nothing waits.
struct WaitingEntries: View {
    let vault: VaultState
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var model: WaitingEntriesModel

    init(api: KeloApi, vault: VaultState, reload: @escaping () -> Void) {
        self.vault = vault
        _model = State(initialValue: WaitingEntriesModel(api: api, onChanged: reload))
    }

    var body: some View {
        let waiting = SecretRules.waiting(model.list)
        Group {
            if !waiting.isEmpty {
                SectionLabel(L(R.string.secret_waiting_title))
                MutedText(L(R.string.secret_waiting_help), small: true).padding(.horizontal, 16)
                ForEach(waiting) { r in
                    MyCard {
                        if model.open == r.id {
                            SecretEntryCard(request: r, framed: false,
                                            onSaved: { _, _ in model.done(r) },
                                            onCancel: { model.drop(r) },
                                            onGone: { model.done(r) })
                        } else {
                            row(r)
                        }
                    }
                    .padding(.horizontal, 16)
                }
            } else {
                // a view of no height, so the load below runs (an empty group never appears)
                Color.clear.frame(height: 0)
            }
        }
        .task(id: vault) { await model.load() }
        // a card added through the wallet's form for one of these: the list follows
        .onChange(of: app.secretCardWait.tick) { _, _ in model.reload() }
    }

    @ViewBuilder private func row(_ r: SecretRequest) -> some View {
        let (kind, hosts) = SecretEntryWords.kindLine(r)
        HStack(alignment: .top, spacing: 10) {
            KeloIconView(r.isCard ? .card : (r.isLogin ? .globe : .key), size: 20, tint: c.ink).padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(r.label.isEmpty ? (r.name.isEmpty ? L(kind) : r.name) : r.label).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(hosts.isEmpty ? L(kind) : "\(L(kind)) · \(hosts)").font(KeloFont.small).foregroundStyle(c.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .combine)
        // side by side while both fit, stacked otherwise (long words in some languages)
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { fillButton(r); deleteButton(r) }
            VStack(alignment: .leading, spacing: 8) { fillButton(r); deleteButton(r) }
        }
    }

    private func fillButton(_ r: SecretRequest) -> some View {
        PrimaryButton(L(R.string.secret_fill)) { Haptics.tick(); model.fill(r.id) }
            .accessibilityIdentifier("kelo.vault.waiting.fill")
    }

    private func deleteButton(_ r: SecretRequest) -> some View {
        SecondaryButton(L(R.string.delete)) { Haptics.tick(); model.drop(r) }
    }
}

/// One login or key: where it can be used, how it last went, how a login is allowed, and replacing or removing it.
struct VaultItemPage: View {
    let name: String
    let vault: VaultModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var open: Bool?
    @State private var confirm = false

    var body: some View {
        let v = vault.info
        let item = vault.item(name)
        SubPage(item?.label ?? L(R.string.vault_title), onBack: leave) {
            if let item, let v { content(item, v) } else { SkeletonRows(rows: 3) }
        }
        .keloSensitive()
        .onAppear { vault.clearMessage() }
        .keloConfirm(L(R.string.vault_remove_confirm, item?.label ?? ""), isPresented: $confirm, yes: L(R.string.delete)) {
            vault.remove(name) { dismiss() }
        }
    }

    /// Leaving a login or key that was started but never filled in: nothing stays behind in the vault.
    private func leave() {
        vault.dropUnfilled(name)
        dismiss()
    }

    @ViewBuilder private func content(_ item: VaultEntry, _ v: VaultState) -> some View {
        let bad = item.isBad
        let signedIn = item.signedIn
        let joiner = L(R.string.usage_group_joiner)
        MyCard {
            let kind = item.isLogin ? L(R.string.vault_kind_login) : (item.kind == VaultEntry.kindEmail ? L(R.string.vault_kind_email)
                : (signedIn ? L(R.string.vault_kind_signed_in) : L(R.string.vault_kind_key)))
            Text(emphasized(L(R.string.vault_kind_hosts, kind), item.hosts.joined(separator: ", "), code: true))
                .font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            let parts: [String] = {
                var p: [String] = []
                if item.hasSecret {
                    let s = bad ? L(R.string.vault_saved_plain) : L(R.string.vault_saved_ok)
                    p.append(item.usernameHint.map { L(R.string.vault_join, s, $0) } ?? s)
                } else { p.append(L(R.string.vault_not_entered)) }
                p.append(L(R.string.vault_last_used, MyFormat.whenText(item.lastUsed)))
                if item.kind == VaultEntry.kindApi && !signedIn { p.append(L(R.string.vault_place, VaultWords.place(item.placement))) }
                if !item.endpoint.isEmpty { p.append(L(R.string.vault_mcp, item.endpoint)) }
                return p
            }()
            MutedText(parts.joined(separator: joiner), small: true)
            if v.locked { ErrorText(L(R.string.vault_item_locked), small: true) }
            if v.verifying.contains(item.name) { Text(L(R.string.vault_verifying)).font(KeloFont.small).foregroundStyle(c.ink) }
            if let r = item.lastResult {
                let line = [VaultWords.result(r), MyFormat.whenText(item.lastResultAt)]
                    + ((item.kind == VaultEntry.kindApi && !item.lastResultDetail.isEmpty) ? [item.lastResultDetail] : [])
                if bad { ErrorText(line.joined(separator: joiner), small: true) } else { MutedText(line.joined(separator: joiner), small: true) }
            } else if item.hasSecret {
                MutedText(L(R.string.vault_not_used_yet), small: true)
            }
            if bad && !signedIn {
                ErrorText(item.isLogin ? L(R.string.vault_bad_login) : L(R.string.vault_bad_key), small: true)
                LinkButton(L(R.string.vault_try_again)) { vault.patch(name, ["clear_result": true]) }
            }
        }
        if item.isLogin {
            MyCard {
                Text(L(R.string.vault_when_login)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                Segmented(options: [L(R.string.vault_ask_every_time), L(R.string.vault_without_asking)], selected: item.autoAllow ? 1 : 0) { i in
                    vault.patch(name, ["auto_allow": .bool(i == 1)])
                }
                MutedText(item.autoAllow ? L(R.string.vault_auto_allow_on) : L(R.string.vault_auto_allow_off), small: true)
            }
            if !item.scheduleGrants.isEmpty {
                // schedules allowed (once, at setup) to sign in with this login without asking; each can be revoked
                MyCard {
                    Text(L(R.string.grant_login_section)).font(KeloFont.titleSmall).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    ScheduleGrantRows(item.scheduleGrants, showSchedule: true) { g in vault.revoke(g) }
                    MutedText(L(R.string.grant_section_note), small: true)
                }
            }
        }
        MyCard {
            if signedIn {
                MutedText((bad ? L(R.string.vault_disconnected) : "") + L(R.string.vault_reconnect_in_connectors), small: true)
            } else if open ?? (!item.hasSecret || bad) {
                SecureEntryForm(item: item, vault: vault) { _ in open = false; vault.reload() }
            } else {
                SecondaryButton(item.isLogin ? L(R.string.vault_change_login) : L(R.string.vault_change_key), fill: true) { open = true }
            }
        }
        MyCard { LinkButton(L(R.string.vault_remove), danger: true) { confirm = true } }
        if !vault.message.isEmpty { ErrorText(vault.message, small: true) }
    }
}

/// The form where a login or a key is typed (Android chat/vault/SecureEntry.kt). The value goes to the server and is never shown
/// again. The login fields carry iOS's username / password content types, so the phone's own password AutoFill can offer to fill
/// them; the page itself says nothing about password apps.
struct SecureEntryForm: View {
    let item: VaultEntry
    var defaultMode: SecretLoginMode = .now
    let onSaved: (SecretLoginMode) -> Void
    @Environment(\.kelo) private var c
    @State private var model: SecureEntryModel
    @State private var user = ""
    @State private var pass = ""
    @State private var mode: SecretLoginMode?

    init(item: VaultEntry, vault: VaultModel, defaultMode: SecretLoginMode = .now, onSaved: @escaping (SecretLoginMode) -> Void) {
        self.item = item; self.defaultMode = defaultMode; self.onSaved = onSaved
        _model = State(initialValue: SecureEntryModel(vault: vault))
    }

    var body: some View {
        let login = item.isLogin
        let ready = !pass.isEmpty && (login ? !user.trimmingCharacters(in: .whitespaces).isEmpty : KeyRules.keyReady(pass))
        VStack(alignment: .leading, spacing: 10) {
            if login {
                FieldLabel(L(R.string.vault_username))
                KeloTextField(L(R.string.vault_username), text: $user, keyboard: .email, maxLength: 200)
                    .modifier(ContentType(kind: .username))
            }
            FieldLabel(login ? L(R.string.vault_password) : L(R.string.vault_key))
            KeloTextField(login ? L(R.string.vault_password) : L(R.string.vault_key), text: $pass, secure: true, keyboard: .ascii, maxLength: 512)
                .modifier(ContentType(kind: login ? .password : .none))
            if login {
                FieldLabel(L(R.string.vault_mode))
                VStack(spacing: 0) {
                    ForEach(Array(SecretLoginMode.allCases.enumerated()), id: \.offset) { i, m in
                        if i > 0 { Hairline() }
                        LoginModeRow(mode: m, on: (mode ?? defaultMode) == m) { mode = m }
                    }
                }
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(c.line, lineWidth: 1))
            }
            PrimaryButton(model.busy ? L(R.string.vault_saving) : L(R.string.vault_save), enabled: !model.busy && ready) { submit() }
            if !model.message.isEmpty { ErrorText(model.message, small: true) }
            MutedText(L(R.string.vault_footer), small: true)
        }
        .keloSensitive()
        .onAppear { if mode == nil { mode = defaultMode } }
    }

    private func submit() {
        let m = mode ?? defaultMode
        model.save(item, user: user, pass: pass, mode: m) { user = ""; pass = ""; onSaved(m) }
    }
}

/// Marks a field for the phone's own AutoFill (iOS only).
struct ContentType: ViewModifier {
    enum Kind { case username, password, none }
    let kind: Kind
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

/// The sign-in page's address and a name; the next page takes the username and password.
struct AddLoginPage: View {
    let vault: VaultModel
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var site = ""
    @State private var label = ""

    var body: some View {
        SubPage(L(R.string.vault_add_login), onBack: { dismiss() }) {
            MyCard {
                FieldLabel(L(R.string.vault_login_site))
                KeloTextField(L(R.string.vault_login_site_placeholder), text: $site, keyboard: .url)
                FieldLabel(L(R.string.vault_login_name))
                KeloTextField(L(R.string.vault_login_name_placeholder), text: $label, maxLength: 80)
                MutedText(L(R.string.vault_login_site_help), small: true)
                if !vault.message.isEmpty { ErrorText(vault.message, small: true) }
                PrimaryButton(L(R.string.onboarding_next), enabled: !site.trimmingCharacters(in: .whitespaces).isEmpty) { add() }
            }
        }
        .keloSensitive()
        .onAppear { vault.clearMessage() }
    }

    /// The new login's page replaces this one in the stack.
    private func add() {
        vault.addLogin(site: site, label: label) { name in
            var p = app.router.paths[.my] ?? NavigationPath()
            if !p.isEmpty { p.removeLast() }
            p.append(MyRoute.vaultItem(name))
            app.router.paths[.my] = p
        }
    }
}

/// The key of an AI service: changing or removing it; choosing which model answers is on the model page.
struct AiKeyPage: View {
    let name: String
    let vault: VaultModel
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var key = ""
    @State private var confirm = false

    var body: some View {
        let p = vault.providers.first { $0.name == name }
        SubPage(p?.displayName ?? name, onBack: { dismiss() }) {
            MyCard {
                Text(L(R.string.vault_ai_key_title)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                MutedText(p?.keyHint.map { L(R.string.vault_ai_key_saved_hint, $0) } ?? L(R.string.vault_ai_key_saved), small: true)
                MutedText(L(R.string.vault_ai_key_note), small: true)
            }
            MyCard {
                FieldLabel(L(R.string.model_key_replace))
                KeloTextField(L(R.string.model_key_paste), text: $key, secure: true, keyboard: .ascii, maxLength: 512)
                PrimaryButton(L(R.string.save), enabled: KeyRules.keyReady(key)) { vault.saveAiKey(name, key: key) }
                if vault.aiSaved { MutedText(L(R.string.operator_saved), small: true) }
                else if !vault.message.isEmpty { MutedText(vault.message, small: true) }
            }
            MyCard { LinkButton(L(R.string.model_key_delete), danger: true) { confirm = true } }
            ViewThatFits(in: .horizontal) {
                HStack { MutedText(L(R.string.vault_ai_model_choice), small: true); Spacer(); goModel }
                VStack(alignment: .leading) { MutedText(L(R.string.vault_ai_model_choice), small: true); goModel }
            }
        }
        .keloSensitive()
        .keloConfirm(L(R.string.vault_ai_key_delete_confirm, p?.displayName ?? name), isPresented: $confirm, yes: L(R.string.delete)) {
            vault.removeAiKey(name) { dismiss() }
        }
        .onAppear { vault.clearMessage() }
        .onChange(of: vault.aiSaved) { _, ok in if ok { key = "" } }
    }

    private var goModel: some View { LinkButton(L(R.string.chat_model_settings)) { MyNav(app: app).push(.model) } }
}

/// What happened in the vault. The agent can neither read nor erase it.
struct VaultLogPage: View {
    let vault: VaultModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c

    var body: some View {
        SubPage(L(R.string.vault_log), onBack: { dismiss() }) {
            MutedText(L(R.string.vault_log_help), small: true)
            if let list = vault.log {
                if list.isEmpty { EmptyNote(L(R.string.vault_log_empty)) }
                ForEach(Array(list.enumerated()), id: \.offset) { _, r in
                    HStack(alignment: .top, spacing: 10) {
                        KeloBadge(VaultWords.action(r.action), denied: !r.ok)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(TokenText.fit(r.item)).font(KeloFont.button).foregroundStyle(c.ink).lineLimit(1)
                            if !r.detail.isEmpty { MutedText(r.detail, small: true) }
                            MutedText(MyFormat.whenText(r.ts), small: true)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.vertical, 8)
                }
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .keloSensitive()
        .task { await vault.loadLog() }
    }
}
