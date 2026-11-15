import SwiftUI
import CryptoKit
import KeloCore
import KeloShared

/// The card form itself (Android wallet/CardForms.kt AddCardForm), used by 카드 등록 and, `inline`, by a card fill-in card.
/// `onAdded`: the server's answer for the saved card ({id, label, brand, last4, ...}).
struct AddCardForm: View {
    let model: AddCardModel
    @Binding var draft: AddCardDraft
    let inline: Bool
    let onAdded: (JSON) async -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let busy = model.busy
        let problem = model.problem
        let now = YearMonth.now()
        let brand = CardNumber.brand(draft.number)
        let prev = AddCardSteps.prev(draft.step)
        let (at, of) = AddCardSteps.position(draft.step)
        let canLeave = AddCardSteps.canLeave(draft.step, number: draft.number, expiry: draft.expiry, cvc: draft.cvc, agreed: draft.agreed, now: now)
        WalletCardFace(brand: brand, number: CardFaceRules.preview(draft.number), holder: draft.holder,
                       expiry: draft.expiry.isEmpty ? L(R.string.wallet_field_expiry_hint) : Expiry.format(draft.expiry))
            .padding(.horizontal, inline ? 32 : 8)
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(L(stepTitle(draft.step))).font(KeloFont.titleMedium).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            Text(L(R.string.wallet_step_of, at, of)).font(KeloFont.label).foregroundStyle(c.muted)
        }
        ProgressView(value: Double(at), total: Double(of)).tint(c.accent)
        switch draft.step {
        case .number:
            WalletField(label: L(R.string.wallet_field_number), placeholder: L(R.string.wallet_field_number_hint), text: numberBinding,
                        keyboard: .number, maxLength: 23, error: numberError(draft.number, mustEnd: nil), trailing: WalletBrand.name(brand))
            CardScanButton { scan in
                draft.number = String(CardNumber.digits(scan.number).prefix(CardNumber.maxLength(CardNumber.brand(scan.number))))
                if let e = scan.expiry { draft.expiry = Expiry.clean(e) }
            }
        case .dates:
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 10) { dateFields(brand, now: now) }
                VStack(alignment: .leading, spacing: 10) { dateFields(brand, now: now) }
            }
        case .owner:
            // the owner's name for the card: its title in the wallet, the approval cards and what Kelo calls it
            WalletField(label: L(R.string.wallet_nickname_field), placeholder: L(R.string.wallet_rename_hint), text: field(\.nickname, max: 60),
                        maxLength: 60, error: WalletFormRules.nameHasDigits(draft.nickname) ? L(R.string.wallet_rename_no_digits) : nil)
                .accessibilityIdentifier("kelo.wallet.nickname")
            WalletField(label: L(R.string.wallet_field_holder), placeholder: L(R.string.wallet_field_holder_hint), text: field(\.holder, max: 80))
            WalletField(label: L(R.string.wallet_field_postal), placeholder: L(R.string.wallet_field_postal_hint), text: field(\.postal, max: 16),
                        keyboard: .ascii, maxLength: 16)
        case .confirm:
            Text(L(R.string.wallet_card_notice)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            WalletAgreeRow(agreed: Binding(get: { draft.agreed }, set: { draft.agreed = $0 }))
        }
        if !problem.isEmpty { ErrorText(problem, small: true) }
        if draft.step == .confirm {
            PrimaryButton(busy ? L(R.string.wallet_saving) : L(R.string.wallet_save), enabled: canLeave && !busy, busy: busy) { save() }
        } else {
            PrimaryButton(L(R.string.wallet_step_next), enabled: canLeave) { draft.step = AddCardSteps.next(draft.step) }
                .accessibilityIdentifier("kelo.wallet.next")
        }
        if inline, let prev { LinkButton(L(R.string.back)) { if !busy { draft.step = prev } } }
    }

    @ViewBuilder private func dateFields(_ brand: CardBrand, now: YearMonth) -> some View {
        WalletField(label: L(R.string.wallet_field_expiry), placeholder: L(R.string.wallet_field_expiry_hint), text: expiryBinding,
                    keyboard: .number, maxLength: 5, error: expiryError(now: now))
        WalletField(label: L(R.string.wallet_field_cvc), placeholder: L(brand == .amex ? R.string.wallet_field_cvc_hint4 : R.string.wallet_field_cvc_hint3),
                    text: Binding(get: { draft.cvc }, set: { draft.cvc = Cvc.clean($0, brand: brand) }), secure: true, keyboard: .number, maxLength: 4)
    }

    private func stepTitle(_ s: AddStep) -> StringKey {
        switch s {
        case .number: R.string.wallet_step_number
        case .dates: R.string.wallet_step_dates
        case .owner: R.string.wallet_step_owner
        case .confirm: R.string.wallet_step_confirm
        }
    }

    private func expiryError(now: YearMonth) -> String? {
        switch Expiry.check(draft.expiry, now: now) {
        case .badMonth: L(R.string.wallet_expiry_bad_month)
        case .expired: L(R.string.wallet_expiry_expired)
        case .tooFar: L(R.string.wallet_expiry_too_far)
        default: nil
        }
    }

    /// The field shows the digits grouped ("4242 4242 …"); the draft keeps only the digits.
    private var numberBinding: Binding<String> {
        Binding(get: { CardNumber.group(draft.number) }, set: { v in
            let d = CardNumber.digits(v)
            draft.number = String(d.prefix(CardNumber.maxLength(CardNumber.brand(d))))
        })
    }

    private var expiryBinding: Binding<String> {
        Binding(get: { Expiry.format(draft.expiry) }, set: { draft.expiry = Expiry.clean($0) })
    }

    private func field(_ k: WritableKeyPath<AddCardDraft, String>, max: Int) -> Binding<String> {
        Binding(get: { draft[keyPath: k] }, set: { draft[keyPath: k] = String($0.prefix(max)) })
    }

    private func save() {
        let now = YearMonth.now()
        if let blocking = AddCardSteps.firstBlocking(number: draft.number, expiry: draft.expiry, cvc: draft.cvc, now: now) {
            draft.step = blocking; return
        }
        guard case .ok(let month, let year) = Expiry.check(draft.expiry, now: now),
              let secret = CardSecret.of(number: CardNumber.digits(draft.number), cvc: draft.cvc) else { return }
        model.save(draft, month: month, year: year, secret: secret, reset: { draft = AddCardDraft() }, added: onAdded)
    }
}

/// The card form inside a card fill-in card (the chat's, the Keychain's waiting list): its own draft, memory only, gone with the card.
struct InlineCardForm: View {
    let onSaved: (JSON) -> Void
    @State private var model: AddCardModel
    @State private var draft = AddCardDraft()

    init(api: KeloApi, onSaved: @escaping (JSON) -> Void) {
        self.onSaved = onSaved
        _model = State(initialValue: AddCardModel(api: api))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            AddCardForm(model: model, draft: $draft, inline: true) { added in onSaved(added) }
        }
        .keloSensitive()
        .onDisappear { draft = AddCardDraft() }
    }
}
