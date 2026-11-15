import SwiftUI
import KeloCore
import KeloShared

/// Before auto-pay goes on: what it means, ticked, then 켜기 (this phone then opens its own copy of the card).
struct WalletAutoPaySheet: View {
    let onCancel: () -> Void
    let onConfirm: () -> Void
    @Environment(\.kelo) private var c
    @State private var agreed = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SheetHeader(L(R.string.wallet_auto_confirm_title), onClose: onCancel)
                Text(L(R.string.wallet_auto_notice)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                MutedText(L(R.string.wallet_auto_from_phone), small: true)
                WalletAgreeRow(agreed: $agreed)
                PrimaryButton(L(R.string.wallet_auto_confirm_yes), enabled: agreed, action: onConfirm)
                SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
    }
}

/// Turning auto-pay on for `asking` (Android WalletScreens.rememberAutoPayOn): the notice (ticked), then this phone's own end-to-end
/// copy opened with Face ID / Touch ID / the passcode and sent once to the auto-pay endpoint (never typed, shown or logged). A card
/// this phone holds no copy of (added on another device) says so, with typing the number as the last resort.
struct WalletAutoPayFlow: ViewModifier {
    @Binding var asking: WalletCard?
    let wallet: WalletModel
    let go: (WalletView) -> Void
    @State private var notHere: WalletCard?
    @State private var failed = false
    /// The card that passed the Keyguard gate: its notice sheet shows.
    @State private var cleared: WalletCard?
    /// Auto-pay needs Keyguard (`AutoPayGate`): the dialog, and the card it goes on with once Keyguard is on.
    @State private var needKeyguard = false
    @State private var waiting: WalletCard?

    /// Auto-pay needs Keyguard: with it off (or not known) the dialog comes first, and turning Keyguard on goes on from there.
    private func gate() {
        guard let card = asking else { return }
        asking = nil
        wallet.checkKeyguard { on in
            if AutoPayGate.mayTurnOn(on) { cleared = card } else { waiting = card; needKeyguard = true }
        }
    }

    func body(content: Content) -> some View {
        content
            .onChange(of: asking?.id) { _, id in if id != nil { gate() } }
            .keyguardNeeded($needKeyguard) {
                if let card = waiting { waiting = nil; cleared = card }
                wallet.reload()
            }
            .keloSheet(isPresented: Binding(get: { cleared != nil }, set: { if !$0 { cleared = nil } }), detents: [.medium, .large]) {
                WalletAutoPaySheet(onCancel: { cleared = nil }, onConfirm: {
                    guard let card = cleared else { return }
                    cleared = nil
                    wallet.turnAutoPayOn(card, delay: 450_000_000) { problem in   // the sheet goes first, so the system check is not behind it
                        if let problem {
                            switch problem {
                            case .notOnPhone: notHere = card
                            case .cancelled: break
                            case .keyguardRequired: waiting = card; needKeyguard = true   // the server says so: the same dialog
                            default: failed = true; Haptics.warning()
                            }
                        } else {
                            Haptics.success()
                            wallet.reload()
                        }
                    }
                })
            }
            .alert(L(R.string.wallet_auto_not_here_title), isPresented: Binding(get: { notHere != nil }, set: { if !$0 { notHere = nil } })) {
                Button(L(R.string.wallet_auto_enter_number)) {
                    if let card = notHere { notHere = nil; go(.reenter(card.id, autoPay: true)) }
                }
                Button(L(R.string.cancel), role: .cancel) { notHere = nil }
            } message: {
                Text(L(R.string.wallet_auto_not_here_body))
            }
            .alert(L(R.string.wallet_auto_on_failed), isPresented: $failed) {
                Button(L(R.string.ok), role: .cancel) {}
            }
    }
}
