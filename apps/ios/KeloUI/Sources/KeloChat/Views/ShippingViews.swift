import SwiftUI
import KeloCore
import KeloShared

/// The agent needs to know where an order goes (shipping_form): the address form right in the chat, filled with what Kelo
/// remembers, for the user to check and send. "Use for next orders" (on by default) also saves it as a shipping address with a
/// nickname; when the agent asks, "How to pay" offers adding a card or paying on the site. After it is sent the card folds into one
/// line; a form past its time, or whose reply is over, shows as expired.
struct ShippingFormCard: View {
    let item: ChatItem.Shipping
    let model: ChatModel
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var editor: AddressEditor
    @State private var save: Bool
    @State private var label: String
    @State private var payment: String?

    init(item: ChatItem.Shipping, model: ChatModel) {
        self.item = item
        self.model = model
        _editor = State(initialValue: AddressEditor(draft: .prefilled(country: item.form.country, item.form.prefill)))
        _save = State(initialValue: item.form.save)
        _label = State(initialValue: String(item.form.label.prefix(AddressRules.maxLabel)))
    }

    var body: some View {
        CardBox {
            switch item.state {
            case .open:
                if ShippingFormRules.open(expiresAt: item.form.expiresAt, now: Date().timeIntervalSince1970) { form } else { expired }
            case .done:
                ResultLine(icon: .mapPin, text: L(R.string.shipping_ask_done, item.doneLine))
                    .accessibilityIdentifier("kelo.shipping.done")
            case .cancelled:
                ResultLine(icon: .close, text: L(R.string.shipping_ask_cancelled), muted: true)
            case .closed:
                ResultLine(icon: .clock, text: L(R.string.shipping_ask_closed), muted: true)
            case .expired:
                expired
            }
        }
        .onAppear { if label.trimmingCharacters(in: .whitespaces).isEmpty { label = L(R.string.shipping_home) } }
        .task(id: item.key) { if item.state == .open { await editor.start(app.api) } }
        .task(id: item.state == .open ? item.key : "") {
            // fold it when its time runs out while it is on screen
            guard item.state == .open, let at = item.form.expiresAt, at > 0 else { return }
            let left = at - Date().timeIntervalSince1970
            if left > 0 { try? await Task.sleep(nanoseconds: UInt64(min(left, 86_400) * 1_000_000_000)) }
            if !Task.isCancelled { model.expireShipping(item.key) }
        }
    }

    private var expired: some View {
        ResultLine(icon: .clock, text: L(R.string.shipping_ask_expired), muted: true)
    }

    @ViewBuilder private var form: some View {
        HStack(alignment: .top, spacing: 8) {
            KeloIconView(.mapPin, size: 20, tint: c.accent).padding(.top, 2)
                .accessibilityHidden(true)
            Text(L(R.string.shipping_ask_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
        }
        if !item.form.site.isEmpty || !item.form.what.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                if !item.form.site.isEmpty {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        KeloIconView(.globe, size: 14, tint: c.muted)
                        Text(TokenText.fit(item.form.site)).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(2)
                    }
                }
                if !item.form.what.isEmpty {
                    Text(item.form.what).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
            }
            .accessibilityElement(children: .combine)
        }
        AddressFields(editor: editor, api: app.api, onCard: true)
        if editor.form != nil {
            saveRow
            if save {
                AddressRow(editor.form?.labels.nickname.nonEmptyText ?? L(R.string.shipping_nickname)) {
                    KeloTextField(L(R.string.shipping_nickname_hint), text: $label, maxLength: AddressRules.maxLabel)
                        .accessibilityIdentifier("kelo.shipping.nickname")
                }
            }
            if item.form.askPayment {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L(R.string.shipping_ask_pay)).font(KeloFont.titleSmall).foregroundStyle(c.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    FlowLayout(spacing: 6, lineSpacing: 6) {
                        ChoiceChip(L(R.string.shipping_pay_card), selected: payment == ShippingFormRules.payCard) {
                            payment = payment == ShippingFormRules.payCard ? nil : ShippingFormRules.payCard
                        }
                        .accessibilityIdentifier("kelo.shipping.pay.card")
                        ChoiceChip(L(R.string.shipping_pay_site), selected: payment == ShippingFormRules.paySite) {
                            payment = payment == ShippingFormRules.paySite ? nil : ShippingFormRules.paySite
                        }
                        .accessibilityIdentifier("kelo.shipping.pay.site")
                    }
                }
            }
        }
        if let e = item.error { ErrorText(e, small: true).accessibilityIdentifier("kelo.shipping.error") }
        // one under the other: long words in any language fit at 320 pt
        send
        SecondaryButton(L(R.string.cancel), enabled: !item.sending, fill: true) { Haptics.warning(); model.cancelShipping(item.key) }
    }

    private var send: some View {
        PrimaryButton(L(item.sending ? R.string.shipping_ask_sending : R.string.shipping_ask_send), enabled: editor.form != nil && !item.sending) {
            guard editor.check(), let address = editor.body() else { Haptics.warning(); return }
            Haptics.tick()
            let nickname = AddressRules.startLabel(label, fallback: L(R.string.shipping_home))
            model.answerShipping(item.key, address: address, save: save, label: nickname, payment: item.form.askPayment ? payment : nil)
        }
        .accessibilityIdentifier("kelo.shipping.send")
    }

    /// "Use for next orders (save as a shipping address)" with a check box; the whole row toggles it.
    private var saveRow: some View {
        Button { save.toggle(); Haptics.tick() } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: save ? "checkmark.square.fill" : "square").font(.title3).foregroundStyle(save ? c.accent : c.muted)
                Text(L(R.string.shipping_ask_save)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, 4).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(save ? [.isSelected, .isButton] : .isButton)
        .accessibilityIdentifier("kelo.shipping.save")
    }
}

private extension String {
    var nonEmptyText: String? { trimmingCharacters(in: .whitespaces).isEmpty ? nil : self }
}
