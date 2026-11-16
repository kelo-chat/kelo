import SwiftUI
import KeloCore
import KeloShared

// Keyguard on the iPhone (Android keyguard/KeyguardUi.kt): the page in 마이 → 보안, the locked banner over the app, the recovery code
// shown once, and the "Auto-pay needs Keyguard" question. The phone's key is KeloCore's `KeyguardKey` (Secure Enclave P-256 + HPKE,
// blob 0x02; the software key on the simulator).

/// What to say when an unlock or enrol did not happen (nil: the owner backed out, nothing to say).
@MainActor func keyguardProblemText(_ p: KeyguardFlow.Problem) -> String? {
    switch p {
    case .cancelled: nil
    case .noLock: L(R.string.keyguard_no_lock_ios)
    case .keyLost: L(R.string.keyguard_key_lost)
    case .notHere: L(R.string.keyguard_locked_other)
    case .server(let e):
        if e.code == KeyguardRules.codeEnvKey { L(R.string.keyguard_env_key) }
        else if let c = e.code, KeyguardRules.lastPhoneCodes.contains(c) { L(R.string.keyguard_last_phone) }
        else if e.status == 403 { L(R.string.keyguard_not_owner) }
        else if e.isNetwork || e.detail.isEmpty { L(R.string.keyguard_failed) }
        else { e.detail }
    }
}

/// "Kelo is locked" under the header while the server holds no key (423 answers, /api/agent/status "locked", the keyguard_locked
/// notification): Unlock with this phone's copy, or, on a phone without one, the recovery code in 마이 → 보안 → Keyguard.
public struct LockedBanner: View {
    let onRecover: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var model: KeyguardModel

    public init(app: AppModel, onRecover: @escaping () -> Void) {
        self.onRecover = onRecover
        _model = State(initialValue: KeyguardModel(host: app))
    }

    public var body: some View {
        let problem = model.problem, busy = model.busy
        let other = problem == .notHere
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                KeloIconView(.lock, size: 22, tint: c.accent)
                Text(L(R.string.keyguard_locked_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityAddTraits(.isHeader)
            }
            MutedText(L(other ? R.string.keyguard_locked_other : R.string.keyguard_locked_body), small: true)
            if let p = problem, !other, let text = keyguardProblemText(p) { ErrorText(text, small: true) }
            if !other {
                PrimaryButton(L(busy ? R.string.keyguard_unlocking : R.string.keyguard_unlock), enabled: !busy) { model.unlockFromBanner() }
                    .accessibilityIdentifier("kelo.keyguard.unlock")
            }
            SecondaryButton(L(R.string.keyguard_use_code), fill: true, action: onRecover)
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 14).fill(c.card).shadow(color: .black.opacity(0.12), radius: 8, y: 2))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(c.line, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("kelo.keyguard.locked")
        // opened from the keyguard_locked notification: the check starts by itself, once
        .task(id: app.router.unlockRequested) {
            if app.router.unlockRequested { app.router.unlockRequested = false; model.unlockFromBanner() }
        }
    }
}

/// 마이 → 보안 → Keyguard: turn it on, the phones that hold the key, unlock or recover, remove this phone's copy, turn it off.
/// `enableNow`: opened from "Auto-pay needs Keyguard" (the turn-on question at once; `onEnabled` once it is on).
struct KeyguardPage: View {
    var startWithCode = false
    var enableNow = false
    var onEnabled: () -> Void = {}
    var onClose: (() -> Void)? = nil
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: KeyguardModel
    @State private var wroteDown = false
    @State private var codeOpen = false
    @State private var code = ""
    @State private var confirm: Confirm?
    @State private var started = false

    init(app: AppModel, startWithCode: Bool = false, enableNow: Bool = false, onEnabled: @escaping () -> Void = {}, onClose: (() -> Void)? = nil) {
        self.startWithCode = startWithCode; self.enableNow = enableNow; self.onEnabled = onEnabled; self.onClose = onClose
        _model = State(initialValue: KeyguardModel(host: app))
    }

    enum Confirm: Identifiable { case on, remove, off; var id: Self { self } }

    private func back() { if let onClose { onClose() } else { dismiss() } }

    var body: some View {
        Group {
            if let rc = model.recovery { recoveryView(rc) } else { page }
        }
        .task {
            if !started { started = true; codeOpen = startWithCode; if enableNow { confirm = .on } }
            await model.load()
        }
        .onChange(of: model.enabledTick) { _, _ in onEnabled() }
        .alert(confirmText, isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } })) {
            switch confirm {
            case .on: Button(L(R.string.keyguard_turn_on)) { confirm = nil; wroteDown = false; model.enroll() }
            case .remove: Button(L(R.string.keyguard_remove_this_phone), role: .destructive) { confirm = nil; model.removeThisPhone() }
            case .off: Button(L(R.string.keyguard_turn_off), role: .destructive) { confirm = nil; model.turnOff() }
            case nil: EmptyView()
            }
            Button(L(R.string.cancel), role: .cancel) { if confirm == .on && enableNow { back() }; confirm = nil }
        }
    }

    private var confirmText: String {
        switch confirm {
        case .on: L(R.string.keyguard_turn_on_confirm)
        case .remove: L(R.string.keyguard_remove_confirm)
        case .off: L(R.string.keyguard_turn_off_confirm)
        case nil: ""
        }
    }

    private var page: some View {
        let busy = model.busy, me = model.me, key = model.key
        return SubPage(L(R.string.keyguard_title), onBack: back) {
            MyCard {
                MutedText(L(R.string.keyguard_intro), small: false)
                MutedText(L(R.string.keyguard_restart_note), small: true)
            }
            if model.notOwner {
                MyCard { MutedText(L(R.string.keyguard_not_owner), small: true) }
            } else if let v = model.view {
                if !v.on {
                    MyCard {
                        PrimaryButton(L(busy ? R.string.keyguard_turning_on : R.string.keyguard_turn_on), enabled: !busy && key != nil) {
                            confirm = .on
                        }
                        .accessibilityIdentifier("kelo.keyguard.turnOn")
                    }
                } else {
                    if v.locked {
                        MyCard {
                            Text(L(R.string.keyguard_locked_title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                            if v.holds(me) && key != nil {
                                PrimaryButton(L(busy ? R.string.keyguard_unlocking : R.string.keyguard_unlock), enabled: !busy) { model.unlock() }
                            } else {
                                MutedText(L(R.string.keyguard_locked_other), small: true)
                            }
                        }
                    }
                    MyCard {
                        Text(L(R.string.keyguard_phones_title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                        ForEach(v.phones, id: \.deviceId) { p in
                            Text(p.deviceId == me ? L(R.string.keyguard_this_phone, p.name) : p.name).font(KeloFont.body).foregroundStyle(c.ink)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if !v.locked && !v.holds(me) {
                            SecondaryButton(L(R.string.keyguard_add_this_phone), enabled: !busy && key != nil, fill: true) { wroteDown = false; model.enroll() }
                        }
                        if v.holds(me) {
                            SecondaryButton(L(R.string.keyguard_remove_this_phone), enabled: !busy, fill: true) { confirm = .remove }
                        }
                        if !v.locked {
                            SecondaryButton(L(R.string.keyguard_turn_off), enabled: !busy, fill: true) { confirm = .off }
                        }
                    }
                }
            }
            if model.view?.on == true || codeOpen {
                MyCard {
                    if !codeOpen {
                        SecondaryButton(L(R.string.keyguard_use_code), fill: true) { codeOpen = true }
                    } else {
                        Text(L(R.string.keyguard_use_code)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                        // an ASCII keyboard even when the phone types Korean or Japanese; shown in capitals as the code is
                        KeloTextField(L(R.string.keyguard_code_hint), text: $code, keyboard: .ascii, maxLength: 64)
                            .keloCapitals()
                            .onChange(of: code) { _, v in
                                // capitals as the code is written; letters, digits and the separators people type
                                let shown = String(v.uppercased().filter { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == " ") })
                                if shown != v { code = shown }
                            }
                            .accessibilityIdentifier("kelo.keyguard.code")
                        PrimaryButton(L(R.string.keyguard_unlock), enabled: !busy && KeyguardRules.codeReady(code)) { model.recover(code: code) { code = ""; codeOpen = false } }
                    }
                }
            }
            if !model.note.isEmpty { MutedText(model.note, small: true) }
            if !model.error.isEmpty { ErrorText(model.error, small: true) }
        }
    }

    /// The recovery code, once: nothing else on the page until "I wrote it down".
    private func recoveryView(_ rc: String) -> some View {
        SubPage(L(R.string.keyguard_recovery_title), onBack: {}) {
            MyCard {
                Text(L(R.string.keyguard_recovery_warning)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                Text(KeyguardRules.codeLines(rc)).font(.system(.title3, design: .monospaced).weight(.bold)).foregroundStyle(c.ink)
                    .lineSpacing(6)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true).padding(.vertical, 8)
                    .accessibilityIdentifier("kelo.keyguard.recoveryCode")
                // the whole row ticks the box, not only the box
                Button { wroteDown.toggle() } label: {
                    HStack(spacing: 10) {
                        Image(systemName: wroteDown ? "checkmark.square.fill" : "square").font(.system(size: 22))
                            .foregroundStyle(wroteDown ? c.accent : c.muted)
                        Text(L(R.string.keyguard_recovery_confirm)).font(KeloFont.body).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    .padding(.vertical, 8)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(wroteDown ? .isSelected : [])
                .accessibilityIdentifier("kelo.keyguard.wroteDown")
                PrimaryButton(L(R.string.keyguard_done), enabled: wroteDown) { model.recoveryWritten() }
            }
        }
        .keloSwipeBackBlocked()   // the code is shown once: the edge swipe must not leave before "I wrote it down"
    }
}

/// "Auto-pay needs Keyguard" (owner's decision): [Turn on Keyguard] opens its enable flow over the page and, once it is on,
/// `onEnabled` goes on with what was asked (turning auto-pay on); [Cancel] leaves everything as it was.
struct KeyguardNeeded: ViewModifier {
    @Binding var isPresented: Bool
    let onEnabled: () -> Void
    @Environment(AppModel.self) private var app
    @State private var enabling = false

    func body(content: Content) -> some View {
        content
            .alert(L(R.string.wallet_keyguard_needed_title), isPresented: $isPresented) {
                Button(L(R.string.keyguard_turn_on_named)) { isPresented = false; enabling = true }
                Button(L(R.string.cancel), role: .cancel) { isPresented = false }
            } message: {
                Text(L(R.string.wallet_keyguard_needed_body))
            }
            .keloFullScreen(isPresented: $enabling) {
                KeyguardPage(app: app, enableNow: true, onEnabled: { enabling = false; onEnabled() }, onClose: { enabling = false })
                    .keloTheme()
            }
    }
}

extension View {
    func keyguardNeeded(_ isPresented: Binding<Bool>, onEnabled: @escaping () -> Void) -> some View {
        modifier(KeyguardNeeded(isPresented: isPresented, onEnabled: onEnabled))
    }
}

private extension View {
    /// The keyboard starts in capitals (iOS).
    @ViewBuilder func keloCapitals() -> some View {
        #if os(iOS)
        textInputAutocapitalization(.characters)
        #else
        self
        #endif
    }
}
