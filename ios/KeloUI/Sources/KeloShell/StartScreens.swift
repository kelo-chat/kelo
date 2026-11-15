import SwiftUI
import Observation
import KeloCore
import KeloShared

// The first-run screens (Android start/*): connect (QR, link, or address + access key), onboarding. There is no language step:
// the app starts in the phone's language (English when the app lacks it); 마이 → 언어 changes it.

/// What the connect screen shows and does (Android start/ConnectViewModel.kt).
@MainActor @Observable
final class ConnectModel {
    /// A pairing link is being used: the whole screen says "connecting this device…".
    var pairing = false
    /// Why the last pairing link did not work, shown at the top.
    var notice: String?
    var linkError: String?
    var keyBusy = false
    var keyError: String?
    /// A valid pairing link waiting for "Connect to this server?": nothing is sent before the user sees which server it is.
    var confirm: PairingLink?
    @ObservationIgnored private let client: PairingClient
    @ObservationIgnored private let deviceName: () -> String
    @ObservationIgnored private let signIn: (Session) -> Void
    @ObservationIgnored let jobs = Jobs()

    /// `signIn`: the app opens the session once the server took this phone; `deviceName`: what this phone is called in 마이 → 내 기기.
    init(client: PairingClient = PairingClient(), deviceName: @escaping () -> String = { "iPhone" },
         signIn: @escaping (Session) -> Void = { _ in }) {
        self.client = client; self.deviceName = deviceName; self.signIn = signIn
    }

    /// A scanned or pasted link. False (and why) when it is not a Kelo pairing link.
    @discardableResult
    func useLink(_ raw: String) -> Bool {
        guard let link = PairingLinks.parse(raw.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            linkError = L(R.string.connect_link_invalid)
            return false
        }
        guard ServerAddress.isAllowed(link.serverUrl) else {
            linkError = L(R.string.connect_https_only)
            return false
        }
        if pairing { return true }
        confirm = link
        notice = nil
        linkError = nil
        return true
    }

    func confirmPair() {
        guard let link = confirm, !pairing else { return }
        confirm = nil
        pairing = true
        jobs.run { [self] in
            do {
                let session = try await client.pair(link, deviceName: deviceName())
                pairing = false
                signIn(session)
            } catch let e as ApiError {
                pairing = false
                notice = e.code == KE2ETransport.unsupportedCode ? L(R.string.e2e_server_old_body, KeloVersion.minServer)
                    : e.isNetwork || e.detail.isEmpty ? L(R.string.connect_failed) : e.detail
            } catch {
                pairing = false
                notice = L(R.string.connect_failed)
            }
        }
    }

    func connectWithKey(address: String, key: String) {
        guard let server = ServerAddress.normalize(address) else { keyError = L(R.string.connect_server_invalid); return }
        guard ServerAddress.isAllowed(server) else { keyError = L(R.string.connect_https_only); return }
        keyBusy = true
        keyError = nil
        jobs.run { [self] in
            do {
                let (session, _) = try await client.checkKey(serverUrl: server, key: key.trimmingCharacters(in: .whitespaces))
                keyBusy = false
                signIn(session)
            } catch let e as ApiError {
                // not an access key: it may be a sign-in code typed by hand (the server's code, e.g. an app store reviewer's from
                // the review notes), which signs this device in like a scanned link
                if e.isUnauthorized && PairingLinks.looksLikeCode(key),
                   let session = try? await client.pair(PairingLink(serverUrl: server, code: PairingLinks.codeOf(key)),
                                                        deviceName: deviceName()) {
                    keyBusy = false
                    signIn(session)
                    return
                }
                keyBusy = false
                keyError = e.code == KE2ETransport.unsupportedCode ? L(R.string.e2e_server_old_body, KeloVersion.minServer)
                    : e.isUnauthorized ? L(R.string.connect_key_wrong) : L(R.string.connect_failed)
            } catch {
                keyBusy = false
                keyError = L(R.string.connect_failed)
            }
        }
    }

    /// The host (and port) the confirm question names.
    static func host(of link: PairingLink) -> String {
        guard let u = URL(string: link.serverUrl), let h = u.host else { return link.serverUrl }
        return h + (u.port.map { ":\($0)" } ?? "")
    }
}

/// Signed out: the connect screen and its QR scanner.
struct ConnectFlow: View {
    @State private var model: ConnectModel
    @State private var scanning = false
    /// A link handed in from outside (opened from another app) waiting for the confirm question.
    let incoming: String?

    init(app: AppModel, incoming: String?) {
        self.incoming = incoming
        _model = State(initialValue: ConnectModel(deviceName: { [weak app] in app?.deviceName ?? "iPhone" },
                                                  signIn: { [weak app] in app?.signIn($0) }))
    }

    var body: some View {
        Group {
            if model.pairing {
                CenterMessage(L(R.string.connect_pairing))
            } else {
                ConnectScreen(model: model, onScan: { scanning = true })
            }
        }
        .alert(L(R.string.connect_confirm_title), isPresented: Binding(get: { model.confirm != nil }, set: { if !$0 { model.confirm = nil } })) {
            Button(L(R.string.connect_confirm_yes)) { model.confirmPair() }
            Button(L(R.string.cancel), role: .cancel) { model.confirm = nil }
        } message: {
            if let link = model.confirm {
                Text(ConnectModel.host(of: link) + "\n\n" + L(R.string.connect_confirm_body))
            }
        }
        .keloFullScreen(isPresented: $scanning) {
            QrScanScreen(accept: { PairingLinks.parse($0) != nil }, onScanned: { raw in
                scanning = false
                model.useLink(raw)
            }, onClose: { scanning = false })
            .keloTheme()
        }
        .onAppear { if let incoming { model.useLink(incoming) } }
    }
}

private struct ConnectScreen: View {
    @Bindable var model: ConnectModel
    let onScan: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var withLink = false
    @State private var link = ""
    @State private var withKey = false
    @State private var server = ""
    @State private var key = ""

    var body: some View {
        CenterCard {
            Text(L(R.string.connect_title)).font(KeloFont.headline).foregroundStyle(c.ink).accessibilityAddTraits(.isHeader)
            if let notice = model.notice { ErrorText(notice) }
            MutedText(L(R.string.connect_lead))
            VStack(alignment: .leading, spacing: 6) {
                step(1, Text(emphasized(L(R.string.connect_step1), L(R.string.connect_step1_path))))
                step(2, Text(L(R.string.connect_step2)))
            }
            Text(emphasized(L(R.string.connect_first_device), L(R.string.connect_first_device_cmd), code: true))
                .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)

            PrimaryButton(L(R.string.connect_scan), action: onScan)

            if withLink {
                KeloTextField(L(R.string.connect_link_placeholder), text: $link, keyboard: .url) {
                    if !link.isEmpty { model.useLink(link) }
                }
                .onChange(of: link) { _, _ in model.linkError = nil }
                if let e = model.linkError { ErrorText(e, small: true) }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) { pasteButton; submitLink }
                    VStack(spacing: 8) { pasteButton; submitLink }
                }
            } else {
                SecondaryButton(L(R.string.connect_paste_link), fill: true) { withLink = true }
            }

            if withKey {
                Text(L(R.string.connect_server_label)).font(KeloFont.small).foregroundStyle(c.muted)
                KeloTextField(L(R.string.connect_server_placeholder), text: $server, keyboard: .url)
                KeloTextField(L(R.string.connect_key_placeholder), text: $key, secure: true, keyboard: .ascii) { submitKey() }
                MutedText(L(R.string.connect_key_hint), small: true)
                if let e = model.keyError { ErrorText(e) }
                PrimaryButton(model.keyBusy ? L(R.string.connect_checking) : L(R.string.connect_submit),
                              enabled: !model.keyBusy && !key.isEmpty && !server.isEmpty, busy: model.keyBusy) { submitKey() }
            } else {
                LinkButton(L(R.string.connect_with_key)) { withKey = true }
            }
        }
        .onAppear { if server.isEmpty { server = app.prefs.lastServerUrl ?? "" } }
    }

    private var pasteButton: some View {
        SecondaryButton(L(R.string.connect_link_paste)) {
            if let t = Clipboard.text()?.trimmingCharacters(in: .whitespacesAndNewlines) { link = t; model.linkError = nil }
        }
    }

    private var submitLink: some View {
        PrimaryButton(L(R.string.connect_submit), enabled: !link.trimmingCharacters(in: .whitespaces).isEmpty) { model.useLink(link) }
    }

    private func submitKey() {
        guard !model.keyBusy, !key.isEmpty, !server.isEmpty else { return }
        model.connectWithKey(address: server, key: key)
    }

    private func step(_ n: Int, _ text: Text) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(L(R.string.step_number, n)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
            text.font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// First visit to a server whose profile has no agent name yet: the user's name and the agent's name ("Kelo" to start with).
struct OnboardingScreen: View {
    let initial: Profile
    @Environment(\.kelo) private var c
    @State private var model: OnboardingModel
    @State private var userName = ""
    @State private var agentName = ""

    init(initial: Profile, app: AppModel) {
        self.initial = initial
        _model = State(initialValue: OnboardingModel(api: { [weak app] in app?.api }, onSaved: { [weak app] in app?.setProfile($0) }))
    }

    private var busy: Bool { model.busy }
    private var error: String? { model.error }

    private var ready: Bool {
        !userName.trimmingCharacters(in: .whitespaces).isEmpty && !agentName.trimmingCharacters(in: .whitespaces).isEmpty && !busy
    }

    var body: some View {
        CenterCard {
            Text(L(R.string.onboarding_hello)).font(KeloFont.headline).foregroundStyle(c.ink).accessibilityAddTraits(.isHeader)
            MutedText(L(R.string.onboarding_intro))
            FieldLabel(L(R.string.onboarding_name_label))
            KeloTextField(L(R.string.onboarding_name_placeholder), text: $userName, maxLength: 60)
            FieldLabel(L(R.string.onboarding_agent_label))
            KeloTextField(L(R.string.onboarding_agent_placeholder), text: $agentName, maxLength: 60) { finish() }
            MutedText(L(R.string.onboarding_agent_question), small: true)
            if let error { ErrorText(error, small: true) }
            PrimaryButton(L(R.string.onboarding_start), enabled: ready, busy: busy) { finish() }
        }
        .onAppear {
            if userName.isEmpty { userName = initial.userName }
            if agentName.isEmpty { agentName = initial.agentName.isEmpty ? "Kelo" : initial.agentName }
        }
    }

    private func finish() {
        guard ready else { return }
        model.finish(user: userName, agent: agentName)
    }
}

/// The first visit's names: PUT /api/profile, then the app shows the profile it got back (Android Onboarding).
@MainActor @Observable
final class OnboardingModel {
    private(set) var busy = false
    private(set) var error: String?
    @ObservationIgnored private let api: () -> KeloApi?
    @ObservationIgnored private let onSaved: (Profile) -> Void
    @ObservationIgnored let jobs = Jobs()

    init(api: @escaping () -> KeloApi?, onSaved: @escaping (Profile) -> Void) { self.api = api; self.onSaved = onSaved }

    func finish(user: String, agent: String) {
        busy = true
        error = nil
        jobs.run { [self] in
            defer { busy = false }
            do {
                guard let api = api() else { throw ApiError(status: 401, detail: "signed out") }
                onSaved(try await api.updateProfile([
                    "user_name": user.trimmingCharacters(in: .whitespaces),
                    "agent_name": agent.trimmingCharacters(in: .whitespaces),
                ]))
            } catch {
                self.error = ApiMessage.text(error)
            }
        }
    }
}
