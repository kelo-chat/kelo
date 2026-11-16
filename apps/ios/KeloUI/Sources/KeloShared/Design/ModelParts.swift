import SwiftUI
import KeloCore


/// A <select>: a field showing the chosen option. A short list opens as a menu; a long one (the ~40 providers) opens a sheet
/// with a search field, ready options first, the chosen one marked and options that cannot be chosen greyed out.
public struct SelectField: View {
    let label: String
    let options: [SelectOption]
    let selected: String
    var sorted = false
    var groupLabel: String?
    let onSelect: (String) -> Void
    @State private var sheet = false
    @Environment(\.kelo) private var c

    public init(title: String, options: [SelectOption], selected: String, sorted: Bool = false, groupLabel: String? = nil,
                onPick: @escaping (String) -> Void) {
        label = title; self.options = options; self.selected = selected; self.sorted = sorted; self.groupLabel = groupLabel; onSelect = onPick
    }

    public var body: some View {
        let shown = options.first { $0.value == selected }?.label ?? selected
        let field = HStack {
            Text(shown).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2).multilineTextAlignment(.leading)
            Spacer(minLength: 8)
            KeloIconView(.chevronRight, size: 16, tint: c.muted).rotationEffect(.degrees(90))
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        .contentShape(Rectangle())
        .accessibilityLabel(label)
        .accessibilityValue(shown)

        if SelectRules.useSheet(options.count) {
            Button { sheet = true } label: { field }
                .buttonStyle(.plain)
                .keloSheet(isPresented: $sheet, detents: [.large]) {
                    SelectSheet(label: label, options: options, selected: selected, sorted: sorted, groupLabel: groupLabel,
                                onClose: { sheet = false }, onSelect: { sheet = false; onSelect($0) })
                }
        } else {
            let arranged = SelectRules.arrange(options, query: "", sorted: sorted)
            Menu {
                ForEach(Array(arranged.enumerated()), id: \.element.id) { i, o in
                    if SelectRules.dividerBefore(arranged, i) {
                        Divider()
                        if let groupLabel { Text(groupLabel) }
                    }
                    Button { onSelect(o.value) } label: {
                        if o.value == selected { Label(o.label, systemImage: "checkmark") } else { Text(o.label) }
                    }
                    .disabled(!o.enabled)
                }
            } label: { field }
            .buttonStyle(.plain)
        }
    }
}

private struct SelectSheet: View {
    let label: String
    let options: [SelectOption]
    let selected: String
    let sorted: Bool
    let groupLabel: String?
    let onClose: () -> Void
    let onSelect: (String) -> Void
    @State private var query = ""
    @Environment(\.kelo) private var c

    var body: some View {
        let shown = SelectRules.arrange(options, query: query, sorted: sorted)
        VStack(alignment: .leading, spacing: 8) {
            SheetHeader(label, onClose: onClose).padding(.horizontal, 20)
            KeloTextField(L(R.string.select_search), text: $query, maxLength: 80).padding(.horizontal, 16)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(shown.enumerated()), id: \.element.id) { i, o in
                            if SelectRules.dividerBefore(shown, i) {
                                HStack {
                                    Hairline()
                                    if let groupLabel { Text(groupLabel).font(KeloFont.label).foregroundStyle(c.muted).fixedSize() }
                                    Hairline()
                                }
                                .padding(.horizontal, 16).padding(.vertical, 8)
                            }
                            Button { onSelect(o.value) } label: {
                                HStack {
                                    Text(o.label).font(o.value == selected ? KeloFont.button : KeloFont.bodyLarge)
                                        .foregroundStyle(!o.enabled ? c.muted.opacity(0.6) : o.value == selected ? c.accent : c.ink)
                                        .lineLimit(2).multilineTextAlignment(.leading)
                                    Spacer(minLength: 8)
                                    if o.value == selected { KeloIconView(.check, size: 20, tint: c.accent) }
                                }
                                .padding(.horizontal, 20).padding(.vertical, 12).frame(minHeight: 48)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .disabled(!o.enabled)
                            .id(o.id)
                            .accessibilityAddTraits(o.value == selected ? .isSelected : [])
                        }
                        if SelectRules.nothingFound(shown, query: query) {
                            Text(L(R.string.select_no_match)).font(KeloFont.body).foregroundStyle(c.muted)
                                .padding(.horizontal, 20).padding(.vertical, 14)
                        }
                    }
                }
                .onAppear { proxy.scrollTo(selected, anchor: .center) }
            }
        }
    }
}

/// The consent question for an unofficial provider, as a sheet while `gate` asks.
public struct ProviderConsentSheet: View {
    let gate: ConsentGate
    @Environment(\.kelo) private var c
    @State private var checked = false

    public init(gate: ConsentGate) { self.gate = gate }

    public var body: some View {
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
                    PrimaryButton(L(R.string.consent_use), enabled: checked) { gate.agree() }
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
    let provider: ModelProvider
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var phase
    @Environment(\.kelo) private var c
    @State private var model: SignInModel
    @State private var paste = ""
    @State private var copied = false

    init(provider: ModelProvider, api: KeloApi, gate: ConsentGate, onSignedIn: @escaping () -> Void) {
        self.provider = provider
        _model = State(initialValue: SignInModel(api: api, provider: provider, gate: gate, onSignedIn: onSignedIn))
    }

    var body: some View {
        let s = model.s, busy = model.busy, err = model.err
        let name = provider.displayName.isEmpty ? L(R.string.signin_this_service) : provider.displayName
        VStack(alignment: .leading, spacing: 8) {
            if let cur = s {
                if SignInRules.finished(cur) {
                    MutedText((cur.account ?? "").isEmpty ? L(R.string.signin_done) : L(R.string.signin_done_account, cur.account!), small: true)
                } else if SignInRules.ended(cur) {
                    ErrorText((cur.message ?? "").isEmpty ? L(R.string.signin_ended) : cur.message!, small: true)
                    PrimaryButton(L(R.string.signin_again), enabled: !busy) { paste = ""; copied = false; model.start() }
                } else {
                    let secs = max(0, Int(model.deadline.timeIntervalSince(model.now)))
                    Text(L(R.string.signin_step_open, name)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    PrimaryButton(L(R.string.signin_open_browser)) {
                        if let u = URL(string: cur.url) { openURL(u) } else { model.setError(L(R.string.connector_browser_failed)) }
                    }
                    if SignInRules.isDeviceCode(cur) {
                        if let code = cur.userCode, !code.isEmpty {
                            Text(L(R.string.signin_step_code)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                            CodeBlock(code: code)
                            SecondaryButton(copied ? L(R.string.device_copied) : L(R.string.device_copy), fill: true) { Clipboard.copy(code); copied = true }
                        }
                        MutedText(L(R.string.signin_device_wait, SignInRules.mmss(Int64(secs))), small: true)
                        LinkButton(L(R.string.signin_check_now)) { model.finish(code: paste) { paste = "" } }
                    } else {
                        Text(L(R.string.signin_step_paste)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        KeloTextField(L(R.string.signin_paste_placeholder), text: $paste, keyboard: .url, maxLength: 4096)
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 8) { pasteButtons }
                            VStack(spacing: 8) { pasteButtons }
                        }
                        MutedText(L(R.string.signin_paste_help, SignInRules.mmss(Int64(secs))), small: true)
                    }
                    LinkButton(L(R.string.cancel)) { paste = ""; model.cancel() }
                }
            } else {
                MutedText(L(R.string.signin_intro, name), small: true)
                PrimaryButton(busy ? L(R.string.signin_starting) : L(R.string.signin_button), enabled: !busy) { paste = ""; copied = false; model.begin() }
            }
            if !err.isEmpty { ErrorText(err, small: true) }
        }
        .task(id: s?.session) { await model.follow() }
        .onChange(of: phase) { _, p in if p == .active { model.appBecameActive() } }
    }

    @ViewBuilder private var pasteButtons: some View {
        SecondaryButton(L(R.string.signin_paste), fill: true) {
            if let t = Clipboard.text()?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { paste = t; model.clearError() }
        }
        PrimaryButton(model.busy ? L(R.string.signin_checking) : L(R.string.signin_finish), enabled: !model.busy && SignInRules.pasteReady(paste)) { model.finish(code: paste) { paste = "" } }
    }
}

/// The signed-in account of a provider and 로그아웃 with a confirm step (Android chat/SignOut.kt).
struct SignOutBox: View {
    let provider: ModelProvider
    let mainProvider: String
    let onResult: (SignOutRules.Outcome, SignOutResult?, String) -> Void
    @State private var model: SignOutModel
    @State private var confirm = false

    init(provider: ModelProvider, api: KeloApi, mainProvider: String, onResult: @escaping (SignOutRules.Outcome, SignOutResult?, String) -> Void) {
        self.provider = provider; self.mainProvider = mainProvider; self.onResult = onResult
        _model = State(initialValue: SignOutModel(api: api, provider: provider))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            MutedText((provider.account ?? "").isEmpty ? L(R.string.model_signin_connected) : L(R.string.model_signin_account, provider.account!), small: true)
            if model.busy { MutedText(L(R.string.model_signout_busy), small: true) }
            else if SignOutRules.isMain(provider.name, mainProvider: mainProvider) { MutedText(L(R.string.model_signout_main), small: true) }
            else { LinkButton(L(R.string.model_signout), danger: true) { confirm = true } }
        }
        .keloConfirm(L(R.string.model_signout_confirm, provider.displayName), isPresented: $confirm, yes: L(R.string.model_signout)) { model.signOut(onResult: onResult) }
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

/// A mono code in a tinted block (sign-in codes).
struct CodeBlock: View {
    let code: String
    @Environment(\.kelo) private var c
    var body: some View {
        Text(code).font(.system(size: 26, weight: .bold, design: .monospaced)).tracking(3).foregroundStyle(c.ink)
            .multilineTextAlignment(.center).textSelection(.enabled)
            .frame(maxWidth: .infinity).padding(.vertical, 14).padding(.horizontal, 8)
            .background(RoundedRectangle(cornerRadius: 14).fill(c.bubble))
            .accessibilityLabel(L(R.string.device_code_label) + ", " + code)
    }
}
