import SwiftUI
import UniformTypeIdentifiers
import KeloCore
import KeloShared

/// The right way to connect this service, whichever it is (Android connectors/ConnectPanel.kt).
struct ConnectPanel: View {
    let connector: ConnectorInfo
    let api: KeloApi
    let onChanged: () -> Void

    var body: some View {
        switch PanelKind.of(connector) {
        case .device(let provider): DeviceConnectPanel(provider: provider, api: api, onChanged: onChanged)
        case .key: KeyConnectPanel(c: connector, api: api, onChanged: onChanged)
        case .browser: BrowserConnectPanel(c: connector, api: api)
        case .mcp: McpConnectPanel(c: connector, api: api, onChanged: onChanged)
        case .mail: MailConnectPanel(c: connector, api: api, onChanged: onChanged)
        case .caldav: CalDavConnectPanel(c: connector, api: api, onChanged: onChanged)
        case .google: GoogleConnectPanel(c: connector, api: api, onChanged: onChanged)
        case .none(let unsupported):
            MutedText(unsupported ? connector.description : L(R.string.connector_not_yet), small: true)
        }
    }
}

/// Sign in on the service's own page (hosted MCP servers), in the system browser. The service sends the user back to the server.
struct McpConnectPanel: View {
    let c: ConnectorInfo
    let api: KeloApi
    let onChanged: () -> Void
    @Environment(\.openURL) private var openURL
    @Environment(\.forgetReporter) private var forgetReporter
    @State private var model: McpModel
    @State private var confirming = false
    @State private var other = false

    init(c: ConnectorInfo, api: KeloApi, onChanged: @escaping () -> Void) {
        self.c = c; self.api = api; self.onChanged = onChanged
        _model = State(initialValue: McpModel(api: api, id: c.id))
    }

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
                PrimaryButton(model.busy ? L(R.string.connector_opening) : L(R.string.mcp_sign_in, c.name), enabled: !model.busy) {
                    model.signIn(browser: { await SystemBrowser.signIn($0, openURL: openURL) }, changed: onChanged)
                }
                if !model.err.isEmpty { ErrorText(model.err, small: true) }
                if let fb = c.fallback, fb.connect == "key", !fb.title.isEmpty {
                    if other {
                        BoldLine(text: fb.title)
                        KeyConnectPanel(c: c.with(connect: "key", via: fb.via, item: fb.item, description: fb.blurb), api: api, onChanged: onChanged)
                    } else {
                        LinkButton("\(fb.title) ›") { other = true }
                    }
                }
            }
        }
        .keloConfirm(L(R.string.mcp_disconnect_confirm, c.name), isPresented: $confirming, yes: L(R.string.connector_disconnect)) {
            model.disconnect(item: c.item, name: c.name, changed: onChanged, report: forgetReporter)
        }
    }
}

/// A mailbox with an app password (or the Google sign-in for Gmail). The server signs in once to check it; only a mailbox that lets
/// it in is saved.
struct MailConnectPanel: View {
    let c: ConnectorInfo
    let api: KeloApi
    let onChanged: () -> Void
    @Environment(\.openURL) private var openURL
    @Environment(\.forgetReporter) private var forgetReporter
    @State private var model: MailModel
    @State private var address = ""
    @State private var password = ""
    @State private var adding = false
    @State private var byPassword = false
    @State private var removing: MailAccount?

    init(c: ConnectorInfo, api: KeloApi, onChanged: @escaping () -> Void) {
        self.c = c; self.api = api; self.onChanged = onChanged
        _model = State(initialValue: MailModel(api: api, connectorId: c.id))
    }

    var body: some View {
        Group {
            if let data = model.data { content(data) } else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(c.id) { await model.returned() }
        .removeAlert($removing, title: { L(R.string.mail_disconnect_confirm, $0.label) }) { model.remove($0, changed: onChanged, report: forgetReporter) }
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
                ReviewNote(c: c, api: api) { Task { await model.load() }; onChanged() }
                LinkButton(L(R.string.mail_by_password)) { byPassword = true }
            case .googleSignIn:
                PrimaryButton(model.busy ? L(R.string.connector_opening) : L(R.string.mail_google_sign_in), enabled: !model.busy) {
                    model.googleSignIn(browser: { await SystemBrowser.signIn($0, openURL: openURL) }, changed: onChanged)
                }
                MutedText(L(R.string.mail_google_help), small: true)
                LinkButton(L(R.string.mail_by_password)) { byPassword = true }
            case .passwordForm:
                if choices.count > 1 {
                    FieldLabel(L(R.string.mail_provider))
                    SelectField(title: L(R.string.mail_provider), options: choices.map { SelectOption(value: $0.id, label: $0.name) },
                                selected: model.provider) { model.choose($0) }
                }
                if let p = d.providers.first(where: { $0.id == model.provider }) { StepsView(lines: p.help) }
                KeloTextField(L(R.string.mail_address), text: $address, keyboard: .email, maxLength: 200)
                    .textContentType(.username)
                KeloTextField(L(R.string.mail_password), text: $password, secure: true, maxLength: 200)
                    .textContentType(.password)
                    .keloSensitive()
                PrimaryButton(L(model.busy ? R.string.mail_checking : R.string.check_and_connect),
                              enabled: !model.busy && ConnectorLogic.mailReady(address: address, password: password)) {
                    model.add(address: address, password: password, added: { password = ""; address = ""; adding = false }, changed: onChanged)
                }
                MutedText(L(R.string.mail_check_note), small: true)
                if adding { LinkButton(L(R.string.cancel)) { adding = false } }
            }
            if !model.err.isEmpty { ErrorText(model.err, small: true) }
            if gmail { OperatorSettings(provider: "google", api: api, onSaved: { Task { await model.load() } }) }
        }
    }
}

/// A calendar from any provider that offers CalDAV (Fastmail, Zoho, Nextcloud ...): the provider's CalDAV address, user name and an app
/// password, checked once by the server before anything is saved. Read only: the morning briefing and heads-ups read it (Android
/// connectors/CalDavConnect.kt).
struct CalDavConnectPanel: View {
    let c: ConnectorInfo
    let onChanged: () -> Void
    @Environment(\.forgetReporter) private var forgetReporter
    @State private var model: CalDavModel
    @State private var url = ""
    @State private var user = ""
    @State private var password = ""
    @State private var adding = false
    @State private var removing: CalDavAccount?

    init(c: ConnectorInfo, api: KeloApi, onChanged: @escaping () -> Void) {
        self.c = c; self.onChanged = onChanged
        _model = State(initialValue: CalDavModel(api: api))
    }

    var body: some View {
        Group {
            if let shown = model.accounts { content(shown) } else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(c.id) { await model.returned() }
        .removeAlert($removing, title: { L(R.string.caldav_disconnect_confirm, $0.address) }) { model.remove($0, changed: onChanged, report: forgetReporter) }
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
                PrimaryButton(L(model.busy ? R.string.mail_checking : R.string.check_and_connect),
                              enabled: !model.busy && !url.isBlank && !user.isBlank && !password.isEmpty) {
                    model.add(url: url, user: user, password: password, added: { password = ""; user = ""; url = ""; adding = false },
                              changed: onChanged)
                }
                MutedText(L(R.string.mail_check_note), small: true)
                if adding { LinkButton(L(R.string.cancel)) { adding = false } }
            } else {
                LinkButton(L(R.string.caldav_add_other)) { adding = true }
            }
            if !model.err.isEmpty { ErrorText(model.err, small: true) }
        }
    }
}

/// A key or token made on the service (or a Zoom app, or a Google service account). The server tries it once before saving it.
struct KeyConnectPanel: View {
    let c: ConnectorInfo
    let onChanged: () -> Void
    @Environment(\.kelo) private var colors
    @Environment(\.forgetReporter) private var forgetReporter
    @State private var model: KeyModel
    @State private var values: [String: String] = [:]
    @State private var confirming = false
    @State private var picking = false

    init(c: ConnectorInfo, api: KeloApi, onChanged: @escaping () -> Void) {
        self.c = c; self.onChanged = onChanged
        _model = State(initialValue: KeyModel(api: api, via: c.via))
    }

    var body: some View {
        Group {
            if let form = model.form { content(form) } else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(c.via) { await model.load() }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.json, .plainText, .data]) { result in
            guard case .success(let url) = result else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            if let data = try? Data(contentsOf: url), let text = ConnectorLogic.keyFileText(data) {
                values["key"] = text
            } else {
                model.setError(L(R.string.key_file_failed))
            }
        }
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
                PrimaryButton(L(model.busy ? R.string.key_checking : (f.checked ? R.string.check_and_connect : R.string.save)),
                              enabled: !model.busy && ConnectorRules.keyReady(f, values: values)) {
                    model.save(f, values: values, saved: { values = [:] }, changed: onChanged)
                }
                MutedText(L(f.checked ? R.string.key_note_checked : R.string.key_note_unchecked), small: true)
                if !model.err.isEmpty { ErrorText(model.err, small: true) }
            }
        }
        .keloSensitive()
        .keloConfirm(L(R.string.key_disconnect_confirm, f.name), isPresented: $confirming, yes: L(R.string.connector_disconnect)) {
            model.disconnect(f, changed: onChanged, report: forgetReporter)
        }
    }
}

/// A site the agent uses with its own browser. A login, if the site needs one, is kept in the keychain and typed by the server.
struct BrowserConnectPanel: View {
    let c: ConnectorInfo
    let api: KeloApi
    @Environment(\.openURL) private var openURL
    @State private var model: BrowserConnectModel

    init(c: ConnectorInfo, api: KeloApi) {
        self.c = c; self.api = api
        _model = State(initialValue: BrowserConnectModel(api: api, site: c.site, name: c.name))
    }

    var body: some View {
        let host = ConnectorRules.host(c.site)
        VStack(alignment: .leading, spacing: 10) {
            if !c.site.isEmpty {
                LinkButton(L(R.string.browser_site_link, host.isEmpty ? c.site : host)) {
                    if !SystemBrowser.open(c.site, openURL: openURL) { model.setError(L(R.string.connector_browser_failed)) }
                }
            }
            if let s = model.saved, !s.isEmpty {
                ShieldTitle(text: L(R.string.browser_saved, s), small: true)
            } else if let name = model.asking {
                SiteLoginForm(name: name, api: api, onSaved: { Task { await model.load(host: host) } }, onCancel: { model.cancelAsk() })
            } else if model.saved == "" {
                SecondaryButton(L(R.string.browser_ask)) { model.ask() }
            }
            MutedText(L(R.string.browser_note), small: true)
            if !model.err.isEmpty { ErrorText(model.err, small: true) }
        }
        .reloadOnReturn(host) { await model.load(host: host) }
    }
}

/// The secure form for a site's login: user name, password and how it is kept. iOS offers saved passwords on the fields
/// itself (text content types); no app is named here.
struct SiteLoginForm: View {
    let onSaved: () -> Void
    let onCancel: () -> Void
    @State private var model: SiteLoginModel
    @State private var user = ""
    @State private var pass = ""
    @State private var mode: SecretLoginMode = .afterSuccess

    init(name: String, api: KeloApi, onSaved: @escaping () -> Void, onCancel: @escaping () -> Void) {
        self.onSaved = onSaved; self.onCancel = onCancel
        _model = State(initialValue: SiteLoginModel(api: api, name: name))
    }

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
                ForEach(SecretLoginMode.allCases, id: \.self) { m in
                    RadioRow(LS("vault_mode_\(m.rawValue)"), detail: LS("vault_mode_\(m.rawValue)_help"), selected: mode == m) { mode = m }
                }
            }
            PrimaryButton(L(model.busy ? R.string.vault_saving : R.string.vault_save),
                          enabled: !model.busy && ConnectorLogic.loginReady(user: user, password: pass)) {
                model.save(user: user, password: pass, mode: mode, saved: { user = ""; pass = ""; onSaved() })
            }
            LinkButton(L(R.string.cancel), action: onCancel)
            if !model.msg.isEmpty { ErrorText(model.msg, small: true) }
        }
        .keloSensitive()
    }
}
