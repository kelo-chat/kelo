import SwiftUI
import KeloCore
import KeloShared

/// Deleting the default card with two or more others left: which card Kelo pays with from now on, then delete.
struct WalletNewDefaultSheet: View {
    let card: WalletCard
    let choices: [WalletCard]
    let onCancel: () -> Void
    let onDelete: (String) -> Void
    @Environment(\.kelo) private var c
    @State private var picked: String?
    var body: some View {
        let chosen = picked ?? DefaultCardRules.suggested(choices)?.id
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.wallet_delete_default_title), onClose: onCancel)
                Text(L(R.string.wallet_delete_default_body, card.label)).font(KeloFont.body).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                VStack(spacing: 0) {
                    ForEach(choices, id: \.id) { other in
                        RadioRow(other.label, detail: walletCardSubtitle(other), selected: chosen == other.id) { picked = other.id }
                        if other.id != choices.last?.id { Hairline() }
                    }
                }
                PrimaryButton(L(R.string.delete), enabled: chosen != nil) { if let chosen { onDelete(chosen) } }
                SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
    }
}

/// A new name for a card; never one that holds card digits.
struct WalletRenameSheet: View {
    let current: String
    let onCancel: () -> Void
    let onSave: (String) -> Void
    @State private var name = ""
    var body: some View {
        let digits = WalletFormRules.nameHasDigits(name)
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.wallet_nickname), onClose: onCancel)
                KeloTextField(L(R.string.wallet_rename_hint), text: $name, maxLength: 60) { if !digits { onSave(name) } }
                MutedText(L(R.string.wallet_rename_note), small: true)
                if digits { ErrorText(L(R.string.wallet_rename_no_digits), small: true) }
                PrimaryButton(L(R.string.save), enabled: !digits) { onSave(name) }
                SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .onAppear { name = current }
    }
}
