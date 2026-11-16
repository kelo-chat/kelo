import SwiftUI
import KeloCore

/// The wallet's own forms and its end-to-end key, for screens outside the wallet (the chat's fill-in cards and payment cards, the
/// Keychain's waiting list). Those modules cannot import KeloMy, so the shell sets this once (`AppModel.wallet`); nil in previews and
/// tests, where the forms simply do not show.
@MainActor public struct WalletHook {
    /// The payment PIN form (`site`, whether the site is fixed, the fill-in card it answers): `onSaved` gets the reply it started, if any.
    public let pinForm: (_ site: String, _ siteFixed: Bool, _ requestId: String?, _ onSaved: @escaping (String?) -> Void) -> AnyView
    /// The wallet's card form, inline (a card fill-in card): `onSaved` gets the card the server saved ({id, label, brand, last4}).
    public let cardForm: (_ onSaved: @escaping (JSON) -> Void) -> AnyView
    /// Releases a site's saved payment PIN once, for the payment `slotId` the owner approved (Face ID first). nil: it got there; else
    /// why not, in the chat's card words (`ChatCardError`).
    public let releasePin: (_ slotId: String, _ pinId: String) async -> WalletSendProblem?

    public init(pinForm: @escaping (String, Bool, String?, @escaping (String?) -> Void) -> AnyView,
                cardForm: @escaping (@escaping (JSON) -> Void) -> AnyView,
                releasePin: @escaping (String, String) async -> WalletSendProblem?) {
        self.pinForm = pinForm; self.cardForm = cardForm; self.releasePin = releasePin
    }
}

/// Why something sealed on this phone (a card, a PIN) could not be sent (Android CardSendError).
public enum WalletSendProblem: Equatable, Sendable { case notWaiting, mismatch, notOnPhone, noLock, cancelled, network, other }
