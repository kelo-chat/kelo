import SwiftUI
import KeloCore
import KeloShared

/// 카드 등록, in steps over a live card face: 1 the number (its brand shows as it is typed; [카드 스캔] fills it and the expiry from the
/// camera), 2 expiry and CVC, 3 the nickname, the holder's name and postal code (both optional), then the notice to tick and Save. The
/// number and CVC are kept only in memory (`WalletPlaces` holds them through a lock for up to ten minutes), sealed with this iPhone's
/// key after Face ID, and cleared once sent.
struct WalletAddPage: View {
    let onBack: () -> Void
    let onSaved: () -> Void
    @Environment(AppModel.self) private var app
    @State private var model: AddCardModel
    @State private var draft = WalletPlaces.addDraft() ?? AddCardDraft()

    init(api: KeloApi, onBack: @escaping () -> Void, onSaved: @escaping () -> Void) {
        self.onBack = onBack; self.onSaved = onSaved
        _model = State(initialValue: AddCardModel(api: api))
    }

    var body: some View {
        let busy = model.busy
        let prev = AddCardSteps.prev(draft.step)
        SubPage(L(R.string.wallet_add_title), onBack: { if let prev, !busy { draft.step = prev } else if !busy { app.secretCardWait.stop(); onBack() } }) {
            AddCardForm(model: model, draft: $draft, inline: false) { added in
                WalletPlaces.clear()
                app.secretCardKept()   // the chat puts "(saved to Keychain: …)" in place of the number, if it came from there
                // a card entry the agent asked for waits for this card: it is answered with it (nickname and last four digits only)
                guard let api = app.api else { onSaved(); return }
                let back = await app.secretCardWait.cardAdded(SecretCardWait.card(from: added), api: api)
                onSaved()
                if let back {   // back where the entry was (the chat), the wallet left at its list
                    app.router.paths[.my] = NavigationPath()
                    app.router.tab = back
                }
            }
        }
        .keloSensitive()
        .onChange(of: draft) { _, d in WalletPlaces.saveAdd(d) }
        .onAppear {
            // a card number from a message the chat held back (SecretGuard): already filled in, taken out of the hand-off at once
            if let v = app.secretHandoff.peek() {
                app.secretHandoff.clear()
                if draft.number.isEmpty {
                    let d = CardNumber.digits(v)
                    draft.number = String(d.prefix(CardNumber.maxLength(CardNumber.brand(d))))
                }
            }
        }
    }
}
