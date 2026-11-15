import SwiftUI
import KeloCore
import KeloShared

/// Connect a service by typing a short code on its own page (GitHub, Microsoft: the OAuth device flow). The app shows the code, the
/// user types it on the service, and the token goes straight into the keychain. When the server offers a web sign-in (GitHub with
/// an OAuth app), the sign-in page opens in the system browser instead (Android connectors/DeviceConnect.kt).
struct DeviceConnectPanel: View {
    let provider: String
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @Environment(\.kelo) private var c
    @State private var p: OAuthProvider?
    @State private var flow: OAuthFlow?
    @State private var deadlineMs: Int64 = 0
    @State private var nowMs: Int64 = 0
    @State private var clientId = ""
    @State private var editId = false
    @State private var scopeId = ""
    @State private var err = ""
    @State private var busy = false
    @State private var copied = false
    @State private var byCode = false
    @State private var confirming = false

    private static func now() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }

    var body: some View {
        Group {
            if let p { content(p) } else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(provider) { await load() }
        // follow the attempt with short requests; it goes on at the server even if this screen is left
        .task(id: flow?.pending == true ? flow?.id : nil) { await follow() }
    }

    private func load() async {
        guard let x = try? await app.requireApi().oauthProviders().first(where: { $0.id == provider }) else { return }
        // a connection shows the choice it was made with; otherwise the first (the default) until the user picks one
        if scopeId.isEmpty || (x.connected && !x.grantedScope.isEmpty && p?.grantedScope != x.grantedScope) {
            scopeId = x.grantedScope.isEmpty ? (x.scopes.first?.id ?? "") : x.grantedScope
        }
        p = x
    }

    private func follow() async {
        guard let id = flow?.id, flow?.pending == true else { return }
        var clock = DeviceFlowClock()
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: DeviceFlowClock.tickSeconds * 1_000_000_000)
            if Task.isCancelled { return }
            nowMs = Self.now()
            guard clock.tick() else { continue }
            do {
                let f = try await app.requireApi().deviceFlow(id)
                flow = f
                switch DeviceFlowClock.after(f) {
                case .keepPolling: continue
                case .connected: await load(); onChanged(); return
                case .ended: await load(); return
                }
            } catch let e as ApiError {
                if e.status == 404 { flow = nil; return }
            } catch { return }
        }
    }

    private func act(_ block: @escaping () async throws -> Void) {
        err = ""; busy = true
        Task {
            do { try await block() } catch { err = app.message(for: error) }
            busy = false
        }
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
                PrimaryButton(busy ? L(R.string.connector_opening) : L(R.string.device_sign_in, name), enabled: !busy) {
                    act {
                        let url = try await app.requireApi().githubWebStart(scope: scopeId)
                        if !(await SystemBrowser.signIn(url, openURL: openURL)) { err = L(R.string.connector_browser_failed) }
                        await load(); onChanged()
                    }
                }
                MutedText(L(R.string.device_sign_in_help, name), small: true)
                LinkButton(L(R.string.device_by_code)) { byCode = true }
            case .register:
                endedLine
                BoldLine(text: L(R.string.device_register, name))
                StepsView(lines: provider.help)
                KeloTextField(L(R.string.client_id), text: $clientId, keyboard: .ascii, maxLength: 100)
                PrimaryButton(L(R.string.save), enabled: !busy && ConnectorLogic.clientIdReady(clientId)) {
                    act {
                        p = try await app.requireApi().saveClientId(self.provider, clientId: clientId.trimmingCharacters(in: .whitespacesAndNewlines))
                        clientId = ""; editId = false
                    }
                }
                if editId { LinkButton(L(R.string.cancel)) { editId = false } }
            case .startCode:
                endedLine
                scopePicker(provider)
                PrimaryButton(busy ? L(R.string.device_preparing) : L(R.string.device_connect, name), enabled: !busy) {
                    act {
                        let started = try await app.requireApi().startDeviceFlow(self.provider, scope: scopeId)
                        let now = Self.now()
                        deadlineMs = DeviceFlowClock.deadline(nowMs: now, expiresIn: started.expiresIn)
                        nowMs = now
                        copied = false
                        flow = started
                    }
                }
                LinkButton(L(R.string.device_change_client)) { editId = true }
                if provider.web { LinkButton(L(R.string.device_sign_in_link, name)) { byCode = false } }
            }
            if !err.isEmpty { ErrorText(err, small: true) }
            if provider.id == "github" { OperatorSettings(provider: "github", onSaved: { Task { await load() } }) }
        }
        .keloConfirm(L(R.string.device_disconnect_confirm, name), isPresented: $confirming, yes: L(R.string.connector_disconnect)) {
            Task {
                try? await app.requireApi().disconnect(item: provider.item)
                flow = nil
                await load(); onChanged()
            }
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
            MutedText(L(R.string.device_wait_help, name, DeviceFlowClock.countdown(deadlineMs: deadlineMs, nowMs: nowMs)), small: true)
            LinkButton(L(R.string.cancel)) {
                Task {
                    try? await app.requireApi().cancelDeviceFlow(f.id)
                    flow = nil
                }
            }
        }
    }

    private func copyButton(_ f: OAuthFlow, fill: Bool) -> some View {
        SecondaryButton(L(copied ? R.string.device_copied : R.string.device_copy), icon: .copy, fill: fill) {
            Clipboard.copy(f.userCode); copied = true; Haptics.success()
        }
    }

    private func openButton(_ f: OAuthFlow, _ name: String) -> some View {
        PrimaryButton(L(R.string.device_open, name)) {
            if !SystemBrowser.open(f.verificationUri, openURL: openURL) { err = L(R.string.connector_browser_failed) }
        }
    }

    @ViewBuilder private func scopePicker(_ provider: OAuthProvider) -> some View {
        if !provider.scopes.isEmpty {
            FieldLabel(L(R.string.device_scope))
            SelectField(label: L(R.string.device_scope), options: provider.scopes.map { ($0.id, $0.label.isEmpty ? $0.id : $0.label) }, value: $scopeId)
            if let detail = provider.scopes.first(where: { $0.id == scopeId })?.detail, !detail.isEmpty {
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
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var open = false
    @State private var appInfo: OperatorApp?
    @State private var cid = ""
    @State private var secret = ""
    @State private var msg = ""
    /// nil: not known yet; false: not the owner (403): nothing shown.
    @State private var owner: Bool?
    @State private var busy = false
    @State private var switchErr = ""

    var body: some View {
        Group {
            if owner == false {
                EmptyView()
            } else if !open {
                if owner == true { LinkButton(L(R.string.operator_open)) { open = true } }
            } else if let a = appInfo {
                card(a)
            } else {
                SkeletonRows(rows: 2)
            }
        }
        .task(id: provider) {
            do {
                let a = try await app.requireApi().operatorApp(provider)
                appInfo = a; cid = a.clientId; owner = true
            } catch let e as ApiError {
                owner = e.status == 403 ? false : nil
            } catch {}
        }
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
            KeloTextField(L(R.string.client_id), text: $cid, keyboard: .ascii, maxLength: 100)
            KeloTextField(a.hasSecret ? L(R.string.operator_secret_saved, secretHint) : secretHint, text: $secret, secure: true, maxLength: 200)
            PrimaryButton(L(R.string.save), enabled: !cid.trimmingCharacters(in: .whitespaces).isEmpty) {
                msg = ""
                Task {
                    do {
                        let saved = try await app.requireApi().saveOperatorApp(provider, clientId: cid.trimmingCharacters(in: .whitespacesAndNewlines),
                                                                               secret: secret.trimmingCharacters(in: .whitespacesAndNewlines))
                        secret = ""; appInfo = saved; cid = saved.clientId
                        msg = L(R.string.operator_saved)
                        onSaved()
                    } catch { msg = app.message(for: error) }
                }
            }
            MutedText(L(a.isBroker ? R.string.operator_broker_on : R.string.operator_broker_off), small: true)
            if !github {
                ToggleRow(L(R.string.google_review_open_switch), detail: L(R.string.google_review_open_warning), isOn: Binding(
                    get: { appInfo?.openToAll ?? false },
                    set: { v in
                        busy = true; switchErr = ""
                        Task {
                            do { appInfo = try await app.requireApi().setGoogleOpen(v) } catch { switchErr = app.message(for: error) }
                            busy = false
                        }
                    }), enabled: !busy)
                if !switchErr.isEmpty { ErrorText(switchErr, small: true) }
            }
            if !msg.isEmpty { SmallText(text: msg) }
        }
        .keloSensitive()
    }
}
