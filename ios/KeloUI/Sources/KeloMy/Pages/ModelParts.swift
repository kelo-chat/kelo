import SwiftUI
import KeloCore
import KeloShared

/// One choice of a select field.
struct SelectOption: Identifiable, Equatable {
    let value: String
    let label: String
    var enabled = true
    /// 1 = after a divider headed by the field's group label (unofficial providers)
    var group = 0
    var id: String { value }
}

/// A field that shows the chosen label and opens a sheet of choices (Android chat/SelectField.kt). Labels wrap in any language.
struct SelectField: View {
    let title: String
    let options: [SelectOption]
    let selected: String
    var groupLabel: String?
    let onPick: (String) -> Void
    @State private var open = false
    @Environment(\.kelo) private var c

    var body: some View {
        Button { open = true } label: {
            HStack {
                Text(options.first { $0.value == selected }?.label ?? "—").font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                    .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                KeloIconView(.chevronRight, size: 16, tint: c.muted).rotationEffect(.degrees(90))
            }
            .padding(.horizontal, 14).padding(.vertical, 13)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityValue(options.first { $0.value == selected }?.label ?? "")
        .keloSheet(isPresented: $open, detents: [.medium, .large]) {
            VStack(alignment: .leading, spacing: 0) {
                SheetHeader(title) { open = false }.padding(.horizontal, 20)
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(options.enumerated()), id: \.element.id) { i, o in
                            if o.group == 1, i == 0 || options[i - 1].group != 1, let g = groupLabel {
                                Text(g).font(KeloFont.titleSmall).foregroundStyle(c.muted).padding(.top, 14).padding(.bottom, 4)
                            } else if i > 0 { Hairline() }
                            RadioRow(o.label, selected: o.value == selected) { onPick(o.value); open = false }
                                .disabled(!o.enabled)
                                .opacity(o.enabled ? 1 : 0.5)
                        }
                    }
                    .padding(.horizontal, 20).padding(.bottom, 24)
                }
            }
        }
    }
}

/// What a provider list remembers about consent while it is open (Android ConsentGate): what was agreed here and the question.
@MainActor @Observable final class ConsentGate {
    var asking: MyProvider?
    var accepted: Set<String> = []
    var error: String?
    @ObservationIgnored private var onYes: () -> Void = {}
    @ObservationIgnored private var onNo: () -> Void = {}

    func needs(_ p: MyProvider) -> Bool { !accepted.contains(p.name) && ProviderConsentRules.needs(p) }

    /// Runs `then` at once when `p` needs no agreement, else after the user agreed (and the server recorded it).
    func request(_ p: MyProvider?, onDecline: @escaping () -> Void = {}, then: @escaping () -> Void) {
        guard let p, needs(p) else { then(); return }
        onYes = then; onNo = onDecline; error = nil; asking = p
    }

    func alreadyAsked(_ p: MyProvider) -> Bool { Prefs.shared.bool(ProviderConsentRules.askedKey(p), default: false) }
    func markAsked(_ p: MyProvider) { Prefs.shared.set(ProviderConsentRules.askedKey(p), true) }

    func decline() {
        if let p = asking { markAsked(p) }
        asking = nil
        onNo()
    }

    func agree(app: AppModel) {
        guard let p = asking else { return }
        Task {
            do {
                try await app.requireApi().request("PUT", "/api/providers/\(KeloApi.segment(p.name))/consent",
                                                   body: ["accepted": true, "version": .number(Double(p.consentVersion))])
                accepted.insert(p.name)
                markAsked(p)
                asking = nil
                onYes()
            } catch {
                self.error = app.message(for: error)
            }
        }
    }
}

/// The consent question, as a sheet while `gate` asks.
struct ConsentSheet: View {
    let gate: ConsentGate
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var checked = false

    var body: some View {
        if let p = gate.asking {
            let service: String = switch ProviderConsentRules.service(p.name) {
            case .claudeSubscription: L(R.string.consent_service_claude)
            case .chatgptSubscription: L(R.string.consent_service_chatgpt)
            case .antigravity: "Antigravity"
            case .qwen: "Qwen"
            case .minimax: "MiniMax"
            case .other: p.displayName
            }
            let vendor = p.vendor.isEmpty ? p.displayName : p.vendor
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    SheetHeader(L(R.string.consent_title, service)) { gate.decline() }
                    Text(L(R.string.consent_body, service, vendor + ProviderConsentRules.subjectParticle(vendor), vendor))
                        .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    Toggle(isOn: $checked) {
                        Text(L(R.string.consent_check)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                    .toggleStyle(CheckboxStyle())
                    if let e = gate.error { ErrorText(e, small: true) }
                    PrimaryButton(L(R.string.consent_use), enabled: checked) { gate.agree(app: app) }
                    SecondaryButton(L(R.string.cancel), fill: true) { gate.decline() }
                }
                .padding(.horizontal, 20).padding(.bottom, 24)
            }
        }
    }
}

/// A checkbox (iOS has none built in).
struct CheckboxStyle: ToggleStyle {
    @Environment(\.kelo) private var c
    func makeBody(configuration: Configuration) -> some View {
        Button { configuration.isOn.toggle() } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: configuration.isOn ? "checkmark.square.fill" : "square")
                    .font(.system(size: 20)).foregroundStyle(configuration.isOn ? c.accent : c.muted)
                configuration.label
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(configuration.isOn ? .isSelected : [])
    }
}

/// 로그인 for a provider reached by signing in, all on the phone (Android chat/SignIn.kt): the consent first (when not given),
/// then the provider's page in the system browser and either a code to type there (the app notices by itself) or a field to paste
/// the code the page shows. Offered only when the server enables it for this provider (`signin`).
struct SignInBox: View {
    let provider: MyProvider
    let gate: ConsentGate
    let onSignedIn: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var phase
    @Environment(\.kelo) private var c
    @State private var s: SignInSession?
    @State private var busy = false
    @State private var err = ""
    @State private var paste = ""
    @State private var copied = false
    @State private var deadline = Date()
    @State private var now = Date()

    private var path: String { "/api/providers/\(KeloApi.segment(provider.name))/login" }

    var body: some View {
        let name = provider.displayName.isEmpty ? L(R.string.signin_this_service) : provider.displayName
        VStack(alignment: .leading, spacing: 8) {
            if let cur = s {
                if SignInRules.finished(cur) {
                    MutedText((cur.account ?? "").isEmpty ? L(R.string.signin_done) : L(R.string.signin_done_account, cur.account!), small: true)
                } else if SignInRules.ended(cur) {
                    ErrorText((cur.message ?? "").isEmpty ? L(R.string.signin_ended) : cur.message!, small: true)
                    PrimaryButton(L(R.string.signin_again), enabled: !busy) { start() }
                } else {
                    let secs = max(0, Int(deadline.timeIntervalSince(now)))
                    Text(L(R.string.signin_step_open, name)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    PrimaryButton(L(R.string.signin_open_browser)) {
                        if let u = URL(string: cur.url) { openURL(u) } else { err = L(R.string.connector_browser_failed) }
                    }
                    if SignInRules.isDeviceCode(cur) {
                        if let code = cur.userCode, !code.isEmpty {
                            Text(L(R.string.signin_step_code)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                            CodeBlock(code: code)
                            SecondaryButton(copied ? L(R.string.device_copied) : L(R.string.device_copy), fill: true) { Clipboard.copy(code); copied = true }
                        }
                        MutedText(L(R.string.signin_device_wait, SignInRules.mmss(secs)), small: true)
                        LinkButton(L(R.string.signin_check_now)) { finish() }
                    } else {
                        Text(L(R.string.signin_step_paste)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        KeloTextField(L(R.string.signin_paste_placeholder), text: $paste, keyboard: .url, maxLength: 4096)
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 8) { pasteButtons }
                            VStack(spacing: 8) { pasteButtons }
                        }
                        MutedText(L(R.string.signin_paste_help, SignInRules.mmss(secs)), small: true)
                    }
                    LinkButton(L(R.string.cancel)) { cancel() }
                }
            } else {
                MutedText(L(R.string.signin_intro, name), small: true)
                PrimaryButton(busy ? L(R.string.signin_starting) : L(R.string.signin_button), enabled: !busy) { gate.request(provider) { start() } }
            }
            if !err.isEmpty { ErrorText(err, small: true) }
        }
        .task(id: s?.session) {
            guard let cur = s, cur.state == "pending" else { return }
            var ticks = 0
            while !Task.isCancelled, s?.state == "pending" {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                now = Date()
                ticks += 1
                if ticks % 3 == 0 { await refresh() }
            }
        }
        .onChange(of: phase) { _, p in if p == .active { Task { await refresh() } } }
    }

    @ViewBuilder private var pasteButtons: some View {
        SecondaryButton(L(R.string.signin_paste), fill: true) {
            if let t = Clipboard.text()?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { paste = t; err = "" }
        }
        PrimaryButton(busy ? L(R.string.signin_checking) : L(R.string.signin_finish), enabled: !busy && SignInRules.pasteReady(paste)) { finish() }
    }

    private func take(_ next: SignInSession) {
        s = next
        if next.state == "pending" { deadline = Date().addingTimeInterval(Double(next.expiresIn)) }
        if SignInRules.finished(next) { onSignedIn() }
    }

    private func fail(_ e: Error) {
        guard let a = e as? ApiError else { err = L(R.string.signin_failed); return }
        if SignInRules.problem(a) == .already { err = L(R.string.signin_already); return }
        let r = SignInRules.readable(a)
        err = r.isEmpty ? L(R.string.signin_failed) : r
    }

    private func start() {
        guard !busy else { return }
        busy = true; err = ""; paste = ""; copied = false
        Task {
            defer { busy = false }
            do {
                take(try await app.requireApi().post("\(path)/start", body: .object([:])))
            } catch let e as ApiError {
                switch SignInRules.problem(e) {
                case .consent:
                    var again = provider
                    again.consent = nil
                    gate.request(again) { start() }
                    return
                case .already: onSignedIn()
                default: break
                }
                fail(e)
            } catch {
                fail(error)
            }
        }
    }

    private func finish() {
        guard let cur = s else { return }
        busy = true; err = ""
        Task {
            defer { busy = false }
            do {
                take(try await app.requireApi().post("\(path)/finish", body: ["session": .string(cur.session), "code": .string(paste.trimmingCharacters(in: .whitespacesAndNewlines))]))
                paste = ""
            } catch {
                if let a = error as? ApiError, a.status == 404 || a.status == 410 { var x = cur; x.state = "expired"; s = x }
                fail(error)
            }
        }
    }

    private func cancel() {
        guard let cur = s else { return }
        s = nil; err = ""; paste = ""
        Task { _ = try? await app.requireApi().request("POST", "\(path)/cancel", body: ["session": .string(cur.session)]) }
    }

    private func refresh() async {
        guard let cur = s, cur.state == "pending", SignInRules.isDeviceCode(cur) else { return }
        do {
            take(try await app.requireApi().get("\(path)/status?session=\(KeloApi.query(cur.session))"))
        } catch let e as ApiError {
            if e.status == 404 { var x = cur; x.state = "expired"; s = x }
        } catch {}
    }
}

/// The signed-in account of a provider and 로그아웃 with a confirm step (Android chat/SignOut.kt).
struct SignOutBox: View {
    let provider: MyProvider
    let mainProvider: String
    let onResult: (SignOutRules.Outcome, SignOutResult?, String) -> Void
    @Environment(AppModel.self) private var app
    @State private var busy = false
    @State private var confirm = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            MutedText((provider.account ?? "").isEmpty ? L(R.string.model_signin_connected) : L(R.string.model_signin_account, provider.account!), small: true)
            if busy { MutedText(L(R.string.model_signout_busy), small: true) }
            else if SignOutRules.isMain(provider.name, mainProvider: mainProvider) { MutedText(L(R.string.model_signout_main), small: true) }
            else { LinkButton(L(R.string.model_signout), danger: true) { confirm = true } }
        }
        .keloConfirm(L(R.string.model_signout_confirm, provider.displayName), isPresented: $confirm, yes: L(R.string.model_signout)) { signOut() }
    }

    private func signOut() {
        busy = true
        Task {
            defer { busy = false }
            do {
                let r: SignOutResult = try await app.requireApi().delete("/api/providers/\(KeloApi.segment(provider.name))/login")
                onResult(SignOutRules.outcome(r, nil), r, "")
            } catch let e as ApiError {
                onResult(SignOutRules.outcome(nil, e), nil, e.isNetwork ? "" : e.detail)
            } catch {
                onResult(.failed, nil, "")
            }
        }
    }
}

/// What a sign-out answer means, in words.
struct SignOutMessage: View {
    let outcome: SignOutRules.Outcome
    let detail: String
    var body: some View {
        switch outcome {
        case .done: MutedText(L(R.string.model_signout_done), small: true)
        case .doneFallbackCleared: MutedText(L(R.string.model_signout_fallback_cleared), small: true)
        case .doneEnvLeft: ErrorText(L(R.string.model_signout_env_left), small: true)
        case .mainModel: ErrorText(L(R.string.model_signout_main), small: true)
        case .failed: ErrorText(L(R.string.model_signout_failed, detail).trimmingCharacters(in: .whitespaces), small: true)
        }
    }
}
