import SwiftUI
import KeloCore
import KeloShared

/// How a login is kept (Android chat/vault/VaultModels.kt VaultMode).
enum VaultMode: String, CaseIterable { case afterSuccess = "after_success", now, once }

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

/// The vault's data, shared by its pages while it is open.
@MainActor @Observable final class VaultStore {
    static let shared = VaultStore()
    var info: VaultState?
    var providers: [MyProvider] = []

    func load(_ app: AppModel) async {
        if let v = try? await app.requireApi().vault() { info = v }
    }

    func loadProviders(_ app: AppModel) async {
        if let p = (try? await app.requireApi().request("GET", "/api/providers")).map(MyProvider.list) { providers = p }
    }
}

/// Every password and key the assistant may use, what each is for, and the lock. Values can be added or replaced, never read.
/// Opened only after the phone's own check; `noLock`: the phone has no passcode, so nothing could be checked and the page says so.
struct VaultPage: View {
    let noLock: Bool
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var store = VaultStore.shared

    private var nav: MyNav { MyNav(app: app) }

    var body: some View {
        SubPage(L(R.string.vault_title), onBack: { dismiss() }) {
            if let v = store.info { list(v) } else { MyCard { SkeletonRows(rows: 2) }; SkeletonRows(rows: 3) }
        }
        .keloSensitive()
        .task { await store.load(app); await store.loadProviders(app) }
    }

    @ViewBuilder private func list(_ v: VaultState) -> some View {
        let logins = v.items.filter { $0.kind == VaultEntry.kindLogin }
        let mailboxes = v.items.filter { $0.kind == VaultEntry.kindEmail }
        let keys = v.items.filter { $0.kind == VaultEntry.kindApi }
        let ai = store.providers.filter { $0.keySource == "app" }
        if noLock { MutedText(L(R.string.vault_gate_no_lock), small: true) }
        MyCard {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { lockTitle(v); Spacer(minLength: 8); lockButton(v) }
                VStack(alignment: .leading, spacing: 8) { lockTitle(v); lockButton(v) }
            }
            MutedText(v.locked ? L(R.string.vault_locked_body) : L(R.string.vault_safe_body), small: true)
        }
        // what the agent asked for and nobody has filled in yet, at the top
        WaitingEntries(vault: v) { Task { await store.load(app) } }
            .padding(.horizontal, -16)
        Group {
            SectionLabel(L(R.string.vault_logins))
            ForEach(logins, id: \.name) { i in itemRow(i, icon: .globe) }
            MenuRow(.plus, L(R.string.vault_add_login)) { nav.push(.vaultAddLogin) }
            // "Use codes from my mail automatically": a code the site mails during a sign-in Kelo started is typed without asking
            MyCard {
                ToggleRow(L(R.string.vault_mail_codes_title),
                          detail: v.mailCodesAuto ? L(R.string.vault_mail_codes_on) : L(R.string.vault_mail_codes_off),
                          isOn: Binding(get: { v.mailCodesAuto }, set: { setMailCodes($0) }))
            }
            .padding(.horizontal, 16)
            PaymentPinSection(locked: v.locked)
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
        if v.locked { PrimaryButton(L(R.string.vault_unlock)) { toggleLock(v) }.frame(maxWidth: 200) }
        else { SecondaryButton(L(R.string.vault_lock)) { toggleLock(v) } }
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

    private func toggleLock(_ v: VaultState) {
        Task {
            _ = try? await app.requireApi().request("PUT", "/api/vault/lock", body: ["locked": .bool(!v.locked)])
            await store.load(app)
        }
    }

    private func setMailCodes(_ on: Bool) {
        Task {
            _ = try? await app.requireApi().request("PUT", "/api/vault/settings", body: ["mail_codes_auto": .bool(on)])
            await store.load(app)
        }
    }
}

/// "Waiting for input": the secure entries the agent asked for that are still open (GET /api/secret-requests, newest first; a
/// vault item waiting for its value shows here too). [Fill in] opens the same entry the chat shows, right here; [Delete] cancels it
/// (the agent is told). Nothing shows when nothing waits.
struct WaitingEntries: View {
    let vault: VaultState
    let reload: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var list: [SecretRequest] = []
    @State private var open: String?

    var body: some View {
        let waiting = SecretRules.waiting(list)
        Group {
            if !waiting.isEmpty {
                SectionLabel(L(R.string.secret_waiting_title))
                MutedText(L(R.string.secret_waiting_help), small: true).padding(.horizontal, 16)
                ForEach(waiting) { r in
                    MyCard {
                        if open == r.id {
                            SecretEntryCard(request: r, framed: false,
                                            onSaved: { _, _ in open = nil; done(r) },
                                            onCancel: { open = nil; drop(r) },
                                            onGone: { open = nil; done(r) })
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
        .task(id: vault) { await load() }
        // a card added through the wallet's form for one of these: the list follows
        .onChange(of: app.secretCardWait.tick) { _, _ in Task { await load() } }
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
        PrimaryButton(L(R.string.secret_fill)) { Haptics.tick(); open = r.id }
            .accessibilityIdentifier("kelo.vault.waiting.fill")
    }

    private func deleteButton(_ r: SecretRequest) -> some View {
        SecondaryButton(L(R.string.delete)) { Haptics.tick(); drop(r) }
    }

    private func load() async {
        guard let api = app.api, let fresh = try? await SecretCalls(api: api).list() else { return }
        list = fresh
    }

    /// Answered (or gone): it leaves the list, and the Keychain loads again (a key or a login may now be listed).
    private func done(_ r: SecretRequest) {
        list.removeAll { $0.id == r.id }
        reload()
    }

    /// [Delete] / Cancel: the agent is told; an empty vault item made for it goes too.
    private func drop(_ r: SecretRequest) {
        list.removeAll { $0.id == r.id }
        Task {
            if let api = app.api { try? await SecretCalls(api: api).cancel(id: r.id) }
            reload()
        }
    }
}

/// One login or key: where it can be used, how it last went, how a login is allowed, and replacing or removing it.
struct VaultItemPage: View {
    let name: String
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var store = VaultStore.shared
    @State private var open: Bool?
    @State private var confirm = false
    @State private var msg = ""

    var body: some View {
        let v = store.info
        let item = v.flatMap { s in (s.items + s.pending).first { $0.name == name } }
        SubPage(item?.label ?? L(R.string.vault_title), onBack: leave) {
            if let item, let v { content(item, v) } else { SkeletonRows(rows: 3) }
        }
        .keloSensitive()
        .keloConfirm(L(R.string.vault_remove_confirm, item?.label ?? ""), isPresented: $confirm, yes: L(R.string.delete)) { remove() }
    }

    /// Leaving a login or key that was started but never filled in: nothing stays behind in the vault.
    private func leave() {
        if let s = store.info, let item = (s.items + s.pending).first(where: { $0.name == name }), !item.hasSecret {
            Task { _ = try? await app.requireApi().request("DELETE", "/api/vault/\(KeloApi.segment(name))/draft"); await store.load(app) }
        }
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
                LinkButton(L(R.string.vault_try_again)) { patch(["clear_result": true]) }
            }
        }
        if item.isLogin {
            MyCard {
                Text(L(R.string.vault_when_login)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                Segmented(options: [L(R.string.vault_ask_every_time), L(R.string.vault_without_asking)], selected: item.autoAllow ? 1 : 0) { i in
                    patch(["auto_allow": .bool(i == 1)])
                }
                MutedText(item.autoAllow ? L(R.string.vault_auto_allow_on) : L(R.string.vault_auto_allow_off), small: true)
            }
            if !item.scheduleGrants.isEmpty {
                // schedules allowed (once, at setup) to sign in with this login without asking; each can be revoked
                MyCard {
                    Text(L(R.string.grant_login_section)).font(KeloFont.titleSmall).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    ScheduleGrantRows(item.scheduleGrants, showSchedule: true) { g in revoke(g) }
                    MutedText(L(R.string.grant_section_note), small: true)
                }
            }
        }
        MyCard {
            if signedIn {
                MutedText((bad ? L(R.string.vault_disconnected) : "") + L(R.string.vault_reconnect_in_connectors), small: true)
            } else if open ?? (!item.hasSecret || bad) {
                SecureEntryForm(item: item) { _ in open = false; Task { await store.load(app) } }
            } else {
                SecondaryButton(item.isLogin ? L(R.string.vault_change_login) : L(R.string.vault_change_key), fill: true) { open = true }
            }
        }
        MyCard { LinkButton(L(R.string.vault_remove), danger: true) { confirm = true } }
        if !msg.isEmpty { ErrorText(msg, small: true) }
    }

    private func patch(_ fields: [String: JSON]) {
        Task {
            do {
                try await app.requireApi().request("PATCH", "/api/vault/\(KeloApi.segment(name))", body: .object(fields))
                await store.load(app)
            } catch { msg = app.message(for: error) }
        }
    }

    private func revoke(_ g: ScheduleGrant) {
        Task {
            do {
                try await app.requireApi().revokeScheduleGrant(g)
                await store.load(app)
            } catch { msg = app.message(for: error) }
        }
    }

    private func remove() {
        Task {
            do {
                try await app.requireApi().disconnect(item: name)
                await store.load(app)
                dismiss()
            } catch { msg = app.message(for: error) }
        }
    }
}

/// The form where a login or a key is typed (Android chat/vault/SecureEntry.kt). The value goes to the server and is never shown
/// again. The login fields carry iOS's username / password content types, so the phone's own password AutoFill can offer to fill
/// them; the page itself says nothing about password apps.
struct SecureEntryForm: View {
    let item: VaultEntry
    var defaultMode: VaultMode = .now
    let onSaved: (VaultMode) -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var user = ""
    @State private var pass = ""
    @State private var mode: VaultMode?
    @State private var msg = ""
    @State private var busy = false

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
                    ForEach(Array(VaultMode.allCases.enumerated()), id: \.offset) { i, m in
                        if i > 0 { Hairline() }
                        modeRow(m)
                    }
                }
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(c.line, lineWidth: 1))
            }
            PrimaryButton(busy ? L(R.string.vault_saving) : L(R.string.vault_save), enabled: !busy && ready) { submit() }
            if !msg.isEmpty { ErrorText(msg, small: true) }
            MutedText(L(R.string.vault_footer), small: true)
        }
        .keloSensitive()
        .onAppear { if mode == nil { mode = defaultMode } }
    }

    private func modeRow(_ m: VaultMode) -> some View {
        let on = (mode ?? defaultMode) == m
        let (title, help): (StringKey, StringKey) = switch m {
        case .afterSuccess: (R.string.vault_mode_after_success, R.string.vault_mode_after_success_help)
        case .now: (R.string.vault_mode_now, R.string.vault_mode_now_help)
        case .once: (R.string.vault_mode_once, R.string.vault_mode_once_help)
        }
        return Button { mode = m } label: {
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

    private func submit() {
        busy = true; msg = ""
        let m = mode ?? defaultMode
        Task {
            defer { busy = false }
            do {
                let api = try app.requireApi()
                if item.isLogin {
                    try await api.request("PUT", "/api/vault/\(KeloApi.segment(item.name))/login",
                                          body: ["username": .string(user.trimmingCharacters(in: .whitespaces)), "password": .string(pass), "mode": .string(m.rawValue)])
                } else {
                    try await api.request("PUT", "/api/credentials/\(KeloApi.segment(item.name))/secret",
                                          body: ["secret": .string(pass.trimmingCharacters(in: .whitespacesAndNewlines))])
                }
                user = ""; pass = ""
                onSaved(m)
            } catch {
                msg = app.message(for: error)
            }
        }
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
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var site = ""
    @State private var label = ""
    @State private var err = ""

    var body: some View {
        SubPage(L(R.string.vault_add_login), onBack: { dismiss() }) {
            MyCard {
                FieldLabel(L(R.string.vault_login_site))
                KeloTextField(L(R.string.vault_login_site_placeholder), text: $site, keyboard: .url)
                FieldLabel(L(R.string.vault_login_name))
                KeloTextField(L(R.string.vault_login_name_placeholder), text: $label, maxLength: 80)
                MutedText(L(R.string.vault_login_site_help), small: true)
                if !err.isEmpty { ErrorText(err, small: true) }
                PrimaryButton(L(R.string.onboarding_next), enabled: !site.trimmingCharacters(in: .whitespaces).isEmpty) { add() }
            }
        }
        .keloSensitive()
    }

    private func add() {
        err = ""
        Task {
            do {
                let r: NewLogin = try await app.requireApi().post("/api/vault/logins",
                    body: ["site": .string(site.trimmingCharacters(in: .whitespaces)), "label": .string(label.trimmingCharacters(in: .whitespaces))])
                await VaultStore.shared.load(app)
                var p = app.router.paths[.my] ?? NavigationPath()
                if !p.isEmpty { p.removeLast() }
                p.append(MyRoute.vaultItem(r.name))
                app.router.paths[.my] = p
            } catch {
                err = app.message(for: error)
            }
        }
    }
}

/// The key of an AI service: changing or removing it; choosing which model answers is on the model page.
struct AiKeyPage: View {
    let name: String
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var store = VaultStore.shared
    @State private var key = ""
    @State private var msg = ""
    @State private var confirm = false

    var body: some View {
        let p = store.providers.first { $0.name == name }
        SubPage(p?.displayName ?? name, onBack: { dismiss() }) {
            MyCard {
                Text(L(R.string.vault_ai_key_title)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                MutedText(p?.keyHint.map { L(R.string.vault_ai_key_saved_hint, $0) } ?? L(R.string.vault_ai_key_saved), small: true)
                MutedText(L(R.string.vault_ai_key_note), small: true)
            }
            MyCard {
                FieldLabel(L(R.string.model_key_replace))
                KeloTextField(L(R.string.model_key_paste), text: $key, secure: true, keyboard: .ascii, maxLength: 512)
                PrimaryButton(L(R.string.save), enabled: KeyRules.keyReady(key)) { save() }
                if !msg.isEmpty { MutedText(msg, small: true) }
            }
            MyCard { LinkButton(L(R.string.model_key_delete), danger: true) { confirm = true } }
            ViewThatFits(in: .horizontal) {
                HStack { MutedText(L(R.string.vault_ai_model_choice), small: true); Spacer(); goModel }
                VStack(alignment: .leading) { MutedText(L(R.string.vault_ai_model_choice), small: true); goModel }
            }
        }
        .keloSensitive()
        .keloConfirm(L(R.string.vault_ai_key_delete_confirm, p?.displayName ?? name), isPresented: $confirm, yes: L(R.string.delete)) { remove() }
    }

    private var goModel: some View { LinkButton(L(R.string.chat_model_settings)) { MyNav(app: app).push(.model) } }

    private var path: String { "/api/providers/\(KeloApi.segment(name))/key" }

    private func save() {
        msg = ""
        Task {
            do {
                try await app.requireApi().request("PUT", path, body: ["api_key": .string(key.trimmingCharacters(in: .whitespacesAndNewlines))])
                key = ""; msg = L(R.string.operator_saved)
                await store.loadProviders(app)
            } catch { msg = app.message(for: error) }
        }
    }

    private func remove() {
        Task {
            _ = try? await app.requireApi().request("DELETE", path)
            await store.loadProviders(app)
            dismiss()
        }
    }
}

/// What happened in the vault. The agent can neither read nor erase it.
struct VaultLogPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var rows: [VaultLogRow]?

    var body: some View {
        SubPage(L(R.string.vault_log), onBack: { dismiss() }) {
            MutedText(L(R.string.vault_log_help), small: true)
            if let list = rows {
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
        .task { rows = (try? await app.requireApi().get("/api/vault/log?limit=200", as: [VaultLogRow].self)) ?? [] }
    }
}
