import Foundation
import KeloCore
import KeloShared

// Payment steps that need the owner (a code, the page's PIN keypad) and releasing a saved payment PIN.

extension ChatModel {
    // ---- payment steps (server payment_steps.py): approve in another app, a one-time code, the page's PIN keypad in the card

    public func refreshPaymentSteps() {
        guard let api = api() else { return }
        Task { [weak self] in
            guard let fresh = try? await PaymentStepCalls(api: api).list() else { return }
            guard let self else { return }
            let next = PaymentStepRules.refresh(self.paymentSteps, fresh)
            if next != self.paymentSteps { self.paymentSteps = next }
        }
    }

    /// `answer`: approved | cancel | new_code | code | done. A step that is gone is folded by the next load.
    public func answerPaymentStep(_ stepId: String, _ answer: String, code: String? = nil) {
        guard let api = api() else { return }
        Task { [weak self] in
            do {
                try await PaymentStepCalls(api: api).answer(stepId, answer, code: code)
                if answer == "new_code", let self {
                    self.paymentSteps = self.paymentSteps.map { var s = $0; if s.stepId == stepId { s.state = "renewing" }; return s }
                }
            } catch {
                self?.refreshPaymentSteps()
            }
        }
    }

    /// The page's keypad region now (JPEG bytes), or nil.
    public func paymentPad(_ stepId: String) async -> Data? {
        guard let api = api() else { return nil }
        return try? await PaymentStepCalls(api: api).pad(stepId)
    }

    /// A tap at fractions of the keypad picture; the taps counted, or nil when it did not go through.
    public func paymentPadTap(_ stepId: String, x: Double, y: Double) async -> Int? {
        guard let api = api() else { return nil }
        return try? await PaymentStepCalls(api: api).tap(stepId, x: x, y: y)
    }

    public func paymentPadClear(_ stepId: String) async -> Int? {
        guard let api = api() else { return nil }
        return try? await PaymentStepCalls(api: api).clear(stepId)
    }

    /// Allow on a payment whose site has a saved PIN: its pin_needed row then asks for the owner's check at once.
    public func pinAllowed(_ approvalId: String) {
        if let row = pinNeeds.first(where: { $0.need.slotId == approvalId }), row.send == .idle { releasePin(approvalId) }
        else { pinOnAllow.insert(approvalId) }
    }

    /// "결제 비밀번호 보내기": the owner's check, then the PIN goes once to the payment's slot.
    public func releasePin(_ slotId: String) {
        guard let row = pinNeeds.first(where: { $0.need.slotId == slotId }), row.send != .working else { return }
        pinNeeds = PinNeedRow.set(pinNeeds, slotId, .working)
        Task { [weak self] in
            guard let self else { return }
            let err: CardSendError?
            if let wallet = self.app.wallet {
                err = (await wallet.releasePin(slotId, row.need.pinId)).map(CardSendError.init)
            } else {
                err = .notOnPhone
            }
            self.pinNeeds = PinNeedRow.set(self.pinNeeds, slotId, err.map { .failed($0) } ?? .sent)
            if err == nil { Haptics.success() } else { Haptics.warning() }
        }
    }
}
