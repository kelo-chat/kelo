import Foundation
import KeloCore
import KeloShared

// The in-chat address form (shipping_form): answering, cancelling and running out of time.

extension ChatModel {
    private func shippingItem(_ key: String) -> ChatItem.Shipping? {
        for item in items { if case .shipping(let s) = item, s.key == key { return s } }
        return nil
    }

    /// "Use this address": POST /api/shipping-forms/{form_id}. A 200 folds the card into one line (and with `payment` "card" opens
    /// the wallet's card form); a 422 keeps the form open with the server's words under it; a 404 (the form is gone) shows it as
    /// expired.
    public func answerShipping(_ key: String, address: [String: JSON], save: Bool, label: String, payment: String?) {
        guard let s = shippingItem(key), s.state == .open, !s.sending, let api = api() else { return }
        items = ChatReducer.shipping(items, key) { $0.sending = true; $0.error = nil }
        let body = ShippingFormRules.body(address: address, save: save, label: label, payment: payment)
        Task { [weak self] in
            do {
                let a = try await api.answerShippingForm(s.formId, body)
                guard let self else { return }
                let one = a.profile.map(\.oneLine).flatMap { $0.isEmpty ? nil : $0 } ?? AddressRules.oneLine(a.formatted)
                let line = ShippingFormRules.doneLine(label: a.profile?.label ?? (save ? label : nil), oneLine: one)
                self.items = ChatReducer.shipping(self.items, key) { $0.state = .done; $0.sending = false; $0.doneLine = line }
                if self.busy { self.phase = Phase(.waiting, since: self.clock()) }
                if payment == ShippingFormRules.payCard { self.app.router.openMyPage(.walletAdd) }   // the existing secure card entry
            } catch let e as ApiError where e.status == 404 {
                self?.items = ChatReducer.shipping(self?.items ?? [], key) { $0.state = .expired; $0.sending = false }
            } catch let e as ApiError where !e.isNetwork {
                self?.items = ChatReducer.shipping(self?.items ?? [], key) { $0.sending = false; $0.error = e.detail.isEmpty ? nil : e.detail }
                self?.onEvent(.error)
            } catch {
                guard let self else { return }
                let message = ApiMessage.text(error)
                self.items = ChatReducer.shipping(self.items, key) { $0.sending = false; $0.error = message }
                self.onEvent(.error)
            }
        }
    }

    /// "Cancel": the agent is told there is no address (POST {"cancel": true}); the card folds at once.
    public func cancelShipping(_ key: String) {
        guard let s = shippingItem(key), s.state == .open, !s.sending else { return }
        items = ChatReducer.shipping(items, key) { $0.state = .cancelled }
        guard let api = api() else { return }
        Task { try? await api.cancelShippingForm(s.formId) }
    }

    /// The form's time ran out (`expires_at`): no more answers.
    public func expireShipping(_ key: String) {
        items = ChatReducer.shipping(items, key) { if $0.state == .open { $0.state = .expired; $0.sending = false } }
    }
}
