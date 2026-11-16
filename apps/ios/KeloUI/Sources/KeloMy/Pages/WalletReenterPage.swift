import SwiftUI
import CryptoKit
import KeloCore
import KeloShared

/// The number and CVC of a saved card typed again: to make a new phone copy (a card this iPhone cannot open), or to turn auto-pay
/// on (the server keeps its own sealed copy then). The number must be the same card (same last 4 digits).
struct WalletReenterPage: View {
    let card: WalletCard
    let autoPay: Bool
    let onBack: () -> Void
    let onSaved: () -> Void
    @State private var model: ReenterModel
    @State private var draft: ReenterDraft
    @State private var needKeyguard = false

    init(api: KeloApi, card: WalletCard, autoPay: Bool, onBack: @escaping () -> Void, onSaved: @escaping () -> Void) {
        self.card = card; self.autoPay = autoPay; self.onBack = onBack; self.onSaved = onSaved
        _model = State(initialValue: ReenterModel(api: api, card: card, autoPay: autoPay))
        _draft = State(initialValue: MainActor.assumeIsolated { WalletPlaces.reenterDraft(card.id, autoPay: autoPay) } ?? ReenterDraft(cardId: card.id, autoPay: autoPay))
    }

    var body: some View {
        let brand = draft.number.isEmpty ? CardBrand.of(card.brand) : CardNumber.brand(draft.number)
        let ready = WalletFormRules.reenterReady(number: draft.number, cvc: draft.cvc, card: card)
        let busy = model.busy
        let problem = model.problem
        let name = card.label.isEmpty ? L(R.string.wallet_card_masked, card.last4) : card.label
        SubPage(L(autoPay ? R.string.wallet_reenter_auto_title : R.string.wallet_reenter_title), onBack: { if !busy { onBack() } }) {
            MutedText(L(autoPay ? R.string.wallet_reenter_auto_body : R.string.wallet_reenter_body, name), small: true)
            MyCard {
                WalletField(label: L(R.string.wallet_field_number), placeholder: L(R.string.wallet_field_number_ending, card.last4),
                            text: Binding(get: { CardNumber.group(draft.number) }, set: { v in
                                let d = CardNumber.digits(v)
                                draft.number = String(d.prefix(CardNumber.maxLength(CardNumber.brand(d))))
                            }), keyboard: .number, maxLength: 23, error: numberError(draft.number, mustEnd: card.last4))
                CardScanButton { scan in
                    draft.number = String(CardNumber.digits(scan.number).prefix(CardNumber.maxLength(CardNumber.brand(scan.number))))
                }
                WalletField(label: L(R.string.wallet_field_cvc), placeholder: L(brand == .amex ? R.string.wallet_field_cvc_hint4 : R.string.wallet_field_cvc_hint3),
                            text: Binding(get: { draft.cvc }, set: { draft.cvc = Cvc.clean($0, brand: brand) }), secure: true, keyboard: .number, maxLength: 4)
            }
            if !problem.isEmpty { ErrorText(problem, small: true) }
            PrimaryButton(busy ? L(R.string.wallet_saving) : L(autoPay ? R.string.wallet_auto_turn_on : R.string.wallet_save),
                          enabled: ready && !busy, busy: busy) { save() }
        }
        .keloSensitive()
        .onChange(of: draft) { _, d in WalletPlaces.saveReenter(d) }
        .onChange(of: model.needKeyguard) { _, on in if on { needKeyguard = true; model.keyguardAsked() } }
        .keyguardNeeded($needKeyguard) { save() }
    }

    private func save() {
        guard let secret = CardSecret.of(number: CardNumber.digits(draft.number), cvc: draft.cvc) else { return }
        model.save(secret: secret) {
            draft = ReenterDraft(cardId: card.id, autoPay: autoPay)
            WalletPlaces.saveReenter(draft)
            onSaved()
        }
    }
}
