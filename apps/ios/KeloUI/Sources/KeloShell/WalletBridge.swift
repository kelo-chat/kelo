import Foundation
import KeloCore
import KeloShared
import KeloChat
import KeloMy

/// Lets the chat's payment card send a card kept only on this phone: KeloChat cannot depend on KeloMy (the wallet and its
/// end-to-end key), so the shell joins them here.
@MainActor final class WalletCardSender: ChatCardSender {
    init() {}

    func deliver(api: KeloApi, approvalId: String, cardId: String) async -> KeloChat.CardSendError? {
        guard let err = await WalletDelivery.deliver(api: api, approvalId: approvalId, cardId: cardId) else { return nil }
        switch err {
        case .notWaiting: return .notWaiting
        case .mismatch: return .mismatch
        case .notOnPhone: return .notOnPhone
        case .noLock: return .noLock
        case .cancelled: return .cancelled
        case .network: return .network
        case .other, .keyguardRequired: return .other
        }
    }
}
