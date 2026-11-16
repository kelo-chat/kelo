import SwiftUI
import KeloCore
import KeloShared

/// Connect a service by typing a short code on its own page (GitHub, Microsoft: the OAuth device flow). The app shows the code, the
/// user types it on the service, and the token goes straight into the keychain. When the server offers a web sign-in (GitHub with
/// an OAuth app), the sign-in page opens in the system browser instead (Android connectors/DeviceConnect.kt).
struct DeviceConnectPanel: View {
    let provider: String
    let api: KeloApi
    let onChanged: () -> Void
    @Environment(\.openURL) private var openURL
    @Environment(\.kelo) private var c
    @Environment(\.forgetReporter) private var forgetReporter
    @State private var model: DeviceConnectModel
    @State private var clientId = ""
    @State private var editId = false
    @State private var copied = false
    @State private var byCode = false
    @State private var confirming = false

    init(provider: String, api: KeloApi, onChanged: @escaping () -> Void) {
        self.provider = provider; self.api = api; self.onChanged = onChanged
        _model = State(initialValue: DeviceConnectModel(api: api, provider: provider))
    }

    private var flow: OAuthFlow? { model.flow }

    var body: some View {
        Group {
            if let p = model.p { content(p) } else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(provider) { await model.returned() }
        // follow the attempt with short requests; it goes on at the server even if this screen is left
        .task(id: flow?.pending == true ? flow?.id : nil) { await model.follow(connected: onChanged) }
    }

    @ViewBuilder private func content(_ provider: OAuthProvider) -> some View {
        let name = provider.name
        VStack(alignment: .leading, spacing: 10) {
            switch DevicePanelStep.of(provider: provider, flow: flow, byCode: byCode, editingClientId: editId) {
            case .connected:
                ShieldTitle(text: L(R.string.connector_connected_name, name))
                MutedText(L(R.string.device_connected_note, provider.account.isEmpty ? name : provider.account), small: true)
                LinkButton(L(R.string.connector_disconnect), danger: true) { confirming = true }
                if !provider.settingsUrl.isEmpty {
                    LinkButton(L(R.string.device_manage, name)) { _ = SystemBrowser.open(provider.settingsUrl, openURL: openURL) }
                }
            case .waitingForCode:
                waiting(name)
            case .webSignIn:
                endedLine
                scopePicker(provider)
                PrimaryButton(model.busy ? L(R.string.connector_opening) : L(R.string.device_sign_in, name), enabled: !model.busy) {
                    model.webSignIn(browser: { await SystemBrowser.signIn($0, openURL: openURL) }, changed: onChanged)
                }
                MutedText(L(R.string.device_sign_in_help, name), small: true)
                LinkButton(L(R.string.device_by_code)) { byCode = true }
            case .register:
                endedLine
                BoldLine(text: L(R.string.device_register, name))
                StepsView(lines: provider.help)
                KeloTextField(L(R.string.client_id), text: $clientId, keyboard: .ascii, maxLength: 100)
                PrimaryButton(L(R.string.save), enabled: !model.busy && ConnectorLogic.clientIdReady(clientId)) {
                    model.saveClientId(clientId, saved: { clientId = ""; editId = false })
                }
                if editId { LinkButton(L(R.string.cancel)) { editId = false } }
            case .startCode:
                endedLine
                scopePicker(provider)
                PrimaryButton(model.busy ? L(R.string.device_preparing) : L(R.string.device_connect, name), enabled: !model.busy) {
                    copied = false
                    model.startCode()
                }
                LinkButton(L(R.string.device_change_client)) { editId = true }
                if provider.web { LinkButton(L(R.string.device_sign_in_link, name)) { byCode = false } }
            }
            if !model.err.isEmpty { ErrorText(model.err, small: true) }
            if provider.id == "github" { OperatorSettings(provider: "github", api: api, onSaved: { Task { await model.load() } }) }
        }
        .keloConfirm(L(R.string.device_disconnect_confirm, name), isPresented: $confirming, yes: L(R.string.connector_disconnect)) {
            model.disconnect(item: provider.item, name: name, changed: onChanged, report: forgetReporter)
        }
    }

    @ViewBuilder private var endedLine: some View {
        if let ended = ConnectorRules.ended(flow) {
            ErrorText(ended.message.isEmpty ? L(R.string.device_failed) : ended.message, small: true)
        }
    }

    @ViewBuilder private func waiting(_ name: String) -> some View {
        if let f = flow {
            BoldLine(text: L(R.string.device_enter_code, name))
            Text(f.userCode)
                .font(.system(size: 28, weight: .bold, design: .monospaced)).kerning(3)
                .foregroundStyle(c.ink).multilineTextAlignment(.center)
                .minimumScaleFactor(0.5).lineLimit(1)
                .frame(maxWidth: .infinity).padding(.horizontal, 8).padding(.vertical, 14)
                .background(RoundedRectangle(cornerRadius: 14).fill(c.bubble))
                .textSelection(.enabled)
                .accessibilityLabel(L(R.string.device_code_label))
                .accessibilityValue(f.userCode)
            // side by side when they fit, else one under the other
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { copyButton(f, fill: true); openButton(f, name) }
                VStack(spacing: 8) { copyButton(f, fill: true); openButton(f, name) }
            }
            MutedText(L(R.string.device_wait_help, name, DeviceFlowClock.countdown(deadlineMs: model.deadlineMs, nowMs: model.nowMs)), small: true)
            LinkButton(L(R.string.cancel)) { model.cancelFlow(f) }
        }
    }

    private func copyButton(_ f: OAuthFlow, fill: Bool) -> some View {
        SecondaryButton(L(copied ? R.string.device_copied : R.string.device_copy), icon: .copy, fill: fill) {
            Clipboard.copy(f.userCode); copied = true; Haptics.success()
        }
    }

    private func openButton(_ f: OAuthFlow, _ name: String) -> some View {
        PrimaryButton(L(R.string.device_open, name)) {
            if !SystemBrowser.open(f.verificationUri, openURL: openURL) { model.setError(L(R.string.connector_browser_failed)) }
        }
    }

    @ViewBuilder private func scopePicker(_ provider: OAuthProvider) -> some View {
        if !provider.scopes.isEmpty {
            FieldLabel(L(R.string.device_scope))
            SelectField(title: L(R.string.device_scope), options: provider.scopes.map { SelectOption(value: $0.id, label: $0.label.isEmpty ? $0.id : $0.label) },
                        selected: model.scopeId) { model.setScope($0) }
            if let detail = provider.scopes.first(where: { $0.id == model.scopeId })?.detail, !detail.isEmpty {
                MutedText(detail, small: true)
            }
        }
    }
}

/// For the person who runs Kelo for others: the OAuth app (GitHub or Google) everyone signs in with. The secret can be replaced,
/// never read back. A paired phone (not the owner) gets 403 and sees nothing.
struct OperatorSettings: View {
    let provider: String
    var onSaved: () -> Void = {}
    @Environment(\.kelo) private var c
    @State private var model: OperatorSettingsModel
    @State private var open = false
    @State private var secret = ""

    init(provider: String, api: KeloApi, onSaved: @escaping () -> Void = {}) {
        self.provider = provider; self.onSaved = onSaved
        _model = State(initialValue: OperatorSettingsModel(api: api, provider: provider))
    }

    var body: some View {
        Group {
            if model.owner == false {
                EmptyView()
            } else if !open {
                if model.owner == true { LinkButton(L(R.string.operator_open)) { open = true } }
            } else if let a = model.appInfo {
                card(a)
            } else {
                SkeletonRows(rows: 2)
            }
        }
        .task(id: provider) { await model.load() }
    }

    @ViewBuilder private func card(_ a: OperatorApp) -> some View {
        let github = provider == "github"
        let secretHint = L(github ? R.string.operator_secret_github : R.string.operator_secret_google)
        KeloCard {
            BoldLine(text: L(github ? R.string.operator_title_github : R.string.operator_title_google))
            MutedText(L(R.string.operator_note), small: true)
            if !a.callback.isEmpty {
                Text(emphasized(L(github ? R.string.operator_callback_github : R.string.operator_callback_google), a.callback, code: true))
                    .font(KeloFont.small).foregroundStyle(c.ink).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            KeloTextField(L(R.string.client_id), text: Binding(get: { model.cid }, set: { model.setClientId($0) }), keyboard: .ascii, maxLength: 100)
            KeloTextField(a.hasSecret ? L(R.string.operator_secret_saved, secretHint) : secretHint, text: $secret, secure: true, maxLength: 200)
            PrimaryButton(L(R.string.save), enabled: !model.cid.trimmingCharacters(in: .whitespaces).isEmpty) {
                model.save(secret: secret, saved: onSaved, cleared: { secret = "" })
            }
            MutedText(L(a.isBroker ? R.string.operator_broker_on : R.string.operator_broker_off), small: true)
            if !github {
                ToggleRow(L(R.string.google_review_open_switch), detail: L(R.string.google_review_open_warning), isOn: Binding(
                    get: { model.appInfo?.openToAll ?? false },
                    set: { model.setOpen($0) }), enabled: !model.busy)
                if !model.switchErr.isEmpty { ErrorText(model.switchErr, small: true) }
            }
            if !model.msg.isEmpty { SmallText(text: model.msg) }
        }
        .keloSensitive()
    }
}
