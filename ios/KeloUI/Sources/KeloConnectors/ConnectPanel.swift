import SwiftUI
import UniformTypeIdentifiers
import KeloCore
import KeloShared

/// The right way to connect this service, whichever it is (Android connectors/ConnectPanel.kt).
struct ConnectPanel: View {
    let connector: ConnectorInfo
    let onChanged: () -> Void

    var body: some View {
        switch PanelKind.of(connector) {
        case .device(let provider): DeviceConnectPanel(provider: provider, onChanged: onChanged)
        case .key: KeyConnectPanel(c: connector, onChanged: onChanged)
        case .browser: BrowserConnectPanel(c: connector)
        case .mcp: McpConnectPanel(c: connector, onChanged: onChanged)
        case .mail: MailConnectPanel(c: connector, onChanged: onChanged)
        case .caldav: CalDavConnectPanel(c: connector, onChanged: onChanged)
        case .google: GoogleConnectPanel(c: connector, onChanged: onChanged)
        case .none(let unsupported):
            MutedText(unsupported ? connector.description : L(R.string.connector_not_yet), small: true)
        }
    }
}

/// Sign in on the service's own page (hosted MCP servers), in the system browser. The service sends the user back to the server.
struct McpConnectPanel: View {
    let c: ConnectorInfo
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @State private var err = ""
    @State private var busy = false
    @State private var confirming = false
    @State private var other = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if c.status == "connected" {
                ShieldTitle(text: L(R.string.connector_connected_name, c.name))
                MutedText(L(R.string.mcp_connected_note), small: true)
                LinkButton(L(R.string.connector_disconnect), danger: true) { confirming = true }
            } else {
                BoldLine(text: L(R.string.mcp_title, c.name))
                MutedText(L(R.string.mcp_help, c.name), small: true)
                // the service may accept only apps it approved, which shows only after the login: said before the sign-in opens
                if !c.note.isEmpty { MutedText(c.note, small: true) }
                PrimaryButton(busy ? L(R.string.connector_opening) : L(R.string.mcp_sign_in, c.name), enabled: !busy) {
                    err = ""; busy = true
                    Task {
                        do {
                            let url = try await app.requireApi().mcpSignInURL(c.id)
                            if !(await SystemBrowser.signIn(url, openURL: openURL)) { err = L(R.string.connector_browser_failed) }
                            onChanged()
                        } catch { err = app.message(for: error) }
                        busy = false
                    }
                }
                if !err.isEmpty { ErrorText(err, small: true) }
                if let fb = c.fallback, fb.connect == "key", !fb.title.isEmpty {
                    if other {
                        BoldLine(text: fb.title)
                        KeyConnectPanel(c: c.with(connect: "key", via: fb.via, item: fb.item, description: fb.blurb), onChanged: onChanged)
                    } else {
                        LinkButton("\(fb.title) ›") { other = true }
                    }
                }
            }
        }
        .keloConfirm(L(R.string.mcp_disconnect_confirm, c.name), isPresented: $confirming, yes: L(R.string.connector_disconnect)) {
            Task { try? await app.requireApi().disconnect(item: c.item); onChanged() }
        }
    }
}

/// A mailbox with an app password (or the Google sign-in for Gmail). The server signs in once to check it; only a mailbox that lets
/// it in is saved.
struct MailConnectPanel: View {
    let c: ConnectorInfo
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @State private var data: MailProviders?
    @State private var provider = ""
    @State private var address = ""
    @State private var password = ""
    @State private var err = ""
    @State private var busy = false
    @State private var adding = false
    @State private var byPassword = false
    @State private var removing: MailAccount?

    var body: some View {
        Group {
            if let data { content(data) } else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(c.id) { await load(); busy = false }
        .alert(removing.map { L(R.string.mail_disconnect_confirm, $0.label) } ?? "", isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button(L(R.string.connector_disconnect), role: .destructive) {
                guard let a = removing else { return }
                Task { try? await app.requireApi().disconnect(item: a.name); await load(); onChanged() }
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    private func load() async {
        if let d = try? await app.requireApi().mailProviders() {
            data = d
            if provider.isEmpty { provider = MailPanelForm.defaultProvider(connectorId: c.id, providers: d.providers) }
        }
    }

    @ViewBuilder private func content(_ d: MailProviders) -> some View {
        let shown = ConnectorRules.mailAccounts(connectorId: c.id, d.accounts)
        let choices = ConnectorRules.mailChoices(connectorId: c.id, d.providers)
        let gmail = c.id == "gmail"
        VStack(alignment: .leading, spacing: 10) {
            ForEach(shown, id: \.name) { a in
                VStack(alignment: .leading, spacing: 4) {
                    ShieldTitle(text: a.label)
                    MutedText(L(R.string.mail_account_note), small: true)
                    LinkButton(L(R.string.connector_disconnect), danger: true) { removing = a }
                }
            }
            switch MailPanelForm.of(c, accounts: shown.count, adding: adding, byPassword: byPassword, googleWeb: d.googleWeb) {
            case .accountsOnly:
                if !gmail { LinkButton(L(R.string.mail_add_other)) { adding = true } }
            case .reviewNote:
                ReviewNote(c: c) { Task { await load() }; onChanged() }
                LinkButton(L(R.string.mail_by_password)) { byPassword = true }
            case .googleSignIn:
                PrimaryButton(busy ? L(R.string.connector_opening) : L(R.string.mail_google_sign_in), enabled: !busy) {
                    err = ""; busy = true
                    Task {
                        do {
                            let url = try await app.requireApi().googleWebStart()
                            if !(await SystemBrowser.signIn(url, openURL: openURL)) { err = L(R.string.connector_browser_failed) }
                            await load(); onChanged()
                        } catch { err = app.message(for: error) }
                        busy = false
                    }
                }
                MutedText(L(R.string.mail_google_help), small: true)
                LinkButton(L(R.string.mail_by_password)) { byPassword = true }
            case .passwordForm:
                if choices.count > 1 {
                    FieldLabel(L(R.string.mail_provider))
                    SelectField(label: L(R.string.mail_provider), options: choices.map { ($0.id, $0.name) }, value: $provider)
                }
                if let p = d.providers.first(where: { $0.id == provider }) { StepsView(lines: p.help) }
                KeloTextField(L(R.string.mail_address), text: $address, keyboard: .email, maxLength: 200)
                    .textContentType(.username)
                KeloTextField(L(R.string.mail_password), text: $password, secure: true, maxLength: 200)
                    .textContentType(.password)
                    .keloSensitive()
                PrimaryButton(L(busy ? R.string.mail_checking : R.string.check_and_connect),
                              enabled: !busy && ConnectorLogic.mailReady(address: address, password: password)) {
                    err = ""; busy = true
                    Task {
                        do {
                            try await app.requireApi().addMail(provider: provider, address: address.trimmingCharacters(in: .whitespacesAndNewlines),
                                                               password: password)
                            password = ""; address = ""; adding = false
                            await load(); onChanged()
                        } catch { err = app.message(for: error) }
                        busy = false
                    }
                }
                MutedText(L(R.string.mail_check_note), small: true)
                if adding { LinkButton(L(R.string.cancel)) { adding = false } }
            }
            if !err.isEmpty { ErrorText(err, small: true) }
            if gmail { OperatorSettings(provider: "google", onSaved: { Task { await load() } }) }
        }
    }
}

/// A calendar from any provider that offers CalDAV (Fastmail, Zoho, Nextcloud ...): the provider's CalDAV address, user name and an app
/// password, checked once by the server before anything is saved. Read only: the morning briefing and heads-ups read it (Android
/// connectors/CalDavConnect.kt).
struct CalDavConnectPanel: View {
    let c: ConnectorInfo
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @State private var accounts: [CalDavAccount]?
    @State private var url = ""
    @State private var user = ""
    @State private var password = ""
    @State private var err = ""
    @State private var busy = false
    @State private var adding = false
    @State private var removing: CalDavAccount?

    var body: some View {
        Group {
            if let shown = accounts { content(shown) } else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(c.id) { await load(); busy = false }
        .alert(removing.map { L(R.string.caldav_disconnect_confirm, $0.address) } ?? "", isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button(L(R.string.connector_disconnect), role: .destructive) {
                guard let a = removing else { return }
                Task { try? await app.requireApi().disconnect(item: a.name); await load(); onChanged() }
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    private func load() async {
        if let got = try? await app.requireApi().get("/api/caldav", as: CalDavAccounts.self) { accounts = got.accounts }
    }

    @ViewBuilder private func content(_ shown: [CalDavAccount]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(shown, id: \.name) { a in
                VStack(alignment: .leading, spacing: 4) {
                    ShieldTitle(text: a.address)
                    MutedText(a.url.hasPrefix("https://") ? String(a.url.dropFirst(8)) : a.url, small: true)
                    MutedText(L(R.string.caldav_account_note), small: true)
                    LinkButton(L(R.string.connector_disconnect), danger: true) { removing = a }
                }
            }
            if shown.isEmpty || adding {
                MutedText(L(R.string.caldav_help), small: true)
                FieldLabel(L(R.string.caldav_url))
                KeloTextField(L(R.string.caldav_url_hint), text: $url, keyboard: .url, maxLength: 300)
                KeloTextField(L(R.string.caldav_user), text: $user, keyboard: .email, maxLength: 200)
                    .textContentType(.username)
                KeloTextField(L(R.string.mail_password), text: $password, secure: true, maxLength: 200)
                    .textContentType(.password)
                    .keloSensitive()
                PrimaryButton(L(busy ? R.string.mail_checking : R.string.check_and_connect),
                              enabled: !busy && !url.isBlank && !user.isBlank && !password.isEmpty) {
                    err = ""; busy = true
                    Task {
                        do {
                            _ = try await app.requireApi().request("POST", "/api/caldav/accounts", body: [
                                "url": .string(url.trimmingCharacters(in: .whitespaces)), "username": .string(user.trimmingCharacters(in: .whitespaces)),
                                "password": .string(password)])
                            password = ""; user = ""; url = ""; adding = false
                            await load(); onChanged()
                        } catch { err = app.message(for: error) }
                        busy = false
                    }
                }
                MutedText(L(R.string.mail_check_note), small: true)
                if adding { LinkButton(L(R.string.cancel)) { adding = false } }
            } else {
                LinkButton(L(R.string.caldav_add_other)) { adding = true }
            }
            if !err.isEmpty { ErrorText(err, small: true) }
        }
    }
}

/// A key or token made on the service (or a Zoom app, or a Google service account). The server tries it once before saving it.
struct KeyConnectPanel: View {
    let c: ConnectorInfo
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var colors
    @State private var form: KeyForm?
    @State private var values: [String: String] = [:]
    @State private var err = ""
    @State private var busy = false
    @State private var confirming = false
    @State private var picking = false

    var body: some View {
        Group {
            if let form { content(form) } else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(c.via) { await load() }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.json, .plainText, .data]) { result in
            guard case .success(let url) = result else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            if let data = try? Data(contentsOf: url), let text = ConnectorLogic.keyFileText(data) {
                values["key"] = text
            } else {
                err = L(R.string.key_file_failed)
            }
        }
    }

    private func load() async {
        if let f = try? await app.requireApi().keyForm(c.via) { form = f }
    }

    private func binding(_ id: String) -> Binding<String> {
        Binding(get: { values[id] ?? "" }, set: { values[id] = $0 })
    }

    @ViewBuilder private func content(_ f: KeyForm) -> some View {
        let google = f.id == "google"
        VStack(alignment: .leading, spacing: 10) {
            if f.connected {
                ShieldTitle(text: L(R.string.connector_connected_name, f.label))
                let account = ConnectorRules.keyAccount(f.label)
                if google && !account.isEmpty {
                    SmallText(text: L(R.string.key_google_share))
                    Text(account).font(.system(.footnote, design: .monospaced)).foregroundStyle(colors.ink)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(RoundedRectangle(cornerRadius: 6).fill(colors.bubble))
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                MutedText(L(R.string.key_connected_note), small: true)
                LinkButton(L(R.string.connector_disconnect), danger: true) { confirming = true }
            } else {
                StepsView(lines: f.help)
                ForEach(f.fields, id: \.id) { x in
                    if google && x.id == "key" {
                        SecondaryButton(L(R.string.key_pick_json), icon: .file) { picking = true }
                        KeloTextField(L(R.string.key_paste_json), text: binding("key"), keyboard: .ascii, axis: .vertical, maxLength: 20_000)
                            .lineLimit(3...8)
                    } else {
                        KeloTextField(x.placeholder.isEmpty ? x.label : x.placeholder, text: binding(x.id), secure: x.secret,
                                      keyboard: .ascii)
                    }
                }
                PrimaryButton(L(busy ? R.string.key_checking : (f.checked ? R.string.check_and_connect : R.string.save)),
                              enabled: !busy && ConnectorRules.keyReady(f, values: values)) {
                    err = ""; busy = true
                    Task {
                        do {
                            try await app.requireApi().saveKey(f.id, values: values)
                            values = [:]
                            await load(); onChanged()
                        } catch { err = app.message(for: error) }
                        busy = false
                    }
                }
                MutedText(L(f.checked ? R.string.key_note_checked : R.string.key_note_unchecked), small: true)
                if !err.isEmpty { ErrorText(err, small: true) }
            }
        }
        .keloSensitive()
        .keloConfirm(L(R.string.key_disconnect_confirm, f.name), isPresented: $confirming, yes: L(R.string.connector_disconnect)) {
            Task { try? await app.requireApi().disconnect(item: f.item); await load(); onChanged() }
        }
    }
}

/// A site the agent uses with its own browser. A login, if the site needs one, is kept in the keychain and typed by the server.
struct BrowserConnectPanel: View {
    let c: ConnectorInfo
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    /// nil: loading; "": nothing kept.
    @State private var saved: String?
    /// The keychain item made for this site's login, while its form is open.
    @State private var asking: String?
    @State private var err = ""

    var body: some View {
        let host = ConnectorRules.host(c.site)
        VStack(alignment: .leading, spacing: 10) {
            if !c.site.isEmpty {
                LinkButton(L(R.string.browser_site_link, host.isEmpty ? c.site : host)) {
                    if !SystemBrowser.open(c.site, openURL: openURL) { err = L(R.string.connector_browser_failed) }
                }
            }
            if let s = saved, !s.isEmpty {
                ShieldTitle(text: L(R.string.browser_saved, s), small: true)
            } else if let name = asking {
                SiteLoginForm(name: name, site: host, onSaved: { Task { await load(host) } }, onCancel: {
                    Task { try? await app.requireApi().cancelLoginDraft(name) }
                    asking = nil
                })
            } else if saved == "" {
                SecondaryButton(L(R.string.browser_ask)) {
                    err = ""
                    Task {
                        do { asking = try await app.requireApi().addLogin(site: c.site, label: c.name) } catch { err = app.message(for: error) }
                    }
                }
            }
            MutedText(L(R.string.browser_note), small: true)
            if !err.isEmpty { ErrorText(err, small: true) }
        }
        .reloadOnReturn(host) { await load(host) }
    }

    private func load(_ host: String) async {
        do {
            let items = try await app.requireApi().vault().items
            saved = ConnectorLogic.savedLogin(host: host, items: items, defaultText: L(R.string.browser_saved_default)) ?? ""
            if saved?.isEmpty == false { asking = nil }
        } catch {
            saved = ""
        }
    }
}

/// The secure form for a site's login: user name, password and how it is kept. iOS offers saved passwords on the fields
/// itself (text content types); no app is named here.
struct SiteLoginForm: View {
    let name: String
    let site: String
    let onSaved: () -> Void
    let onCancel: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var user = ""
    @State private var pass = ""
    @State private var mode: VaultSaveMode = .afterSuccess
    @State private var msg = ""
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            FieldLabel(L(R.string.vault_username))
            KeloTextField(L(R.string.vault_username), text: $user, keyboard: .email, maxLength: 200)
                .textContentType(.username)
            FieldLabel(L(R.string.vault_password))
            KeloTextField(L(R.string.vault_password), text: $pass, secure: true, maxLength: 512)
                .textContentType(.password)
            FieldLabel(L(R.string.vault_mode))
            VStack(spacing: 0) {
                ForEach(VaultSaveMode.allCases, id: \.self) { m in
                    RadioRow(LS("vault_mode_\(m.rawValue)"), detail: LS("vault_mode_\(m.rawValue)_help"), selected: mode == m) { mode = m }
                }
            }
            PrimaryButton(L(busy ? R.string.vault_saving : R.string.vault_save),
                          enabled: !busy && ConnectorLogic.loginReady(user: user, password: pass)) {
                busy = true; msg = ""
                Task {
                    do {
                        try await app.requireApi().putLogin(name, username: user.trimmingCharacters(in: .whitespacesAndNewlines), password: pass, mode: mode)
                        user = ""; pass = ""
                        onSaved()
                    } catch { msg = app.message(for: error) }
                    busy = false
                }
            }
            LinkButton(L(R.string.cancel), action: onCancel)
            if !msg.isEmpty { ErrorText(msg, small: true) }
        }
        .keloSensitive()
    }
}
