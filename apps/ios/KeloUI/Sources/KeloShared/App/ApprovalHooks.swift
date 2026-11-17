import Foundation
import KeloCore

/// What runs after the owner allowed an approval anywhere in the app (the chat, the work page): an approved calendar change or call
/// may now wait for this phone (마이 sets it; Android DeviceTasks.afterApproval).
@MainActor public enum ApprovalHooks {
    public static var afterAllow: () -> Void = {}
    /// A waiting item (an approval or request card, by its id) was answered here: its notification, if one is up, is taken down at once.
    public static var itemSettled: (String) -> Void = { _ in }
    /// This phone's approval key (the app sets the real one at start; none: payments are sent unsigned and the server decides).
    public static var signer: ApprovalSigner = NoApprovalSigner()
}

/// The Allow of a signed payment (Android ApprovalSigning.kt): the owner's check and the phone's signature come first; a cancelled or
/// refused one leaves the card open and says why in the words below.
@MainActor public enum ApprovalSignFlow {
    public static func sign(id: String, issuedAt: Int64) async -> ApprovalSignOutcome {
        await ApprovalSigning.signPaymentAllow(ApprovalHooks.signer, approvalId: id, issuedAt: issuedAt,
                                               title: L(R.string.approval_sign_title), subtitle: L(R.string.approval_sign_subtitle))
    }

    public static func failure(_ r: ApprovalSignOutcome.Reason) -> String {
        switch r {
        case .noLock: L(R.string.keyguard_no_lock_ios)
        case .keyLost: L(R.string.approval_sign_key_lost)
        case .failed: L(R.string.approval_sign_failed)
        }
    }

    /// What a refused payment Allow (403 signature_required / bad_signature) says: a phone shows the server's own message.
    public static func refusal(_ e: ApiError) -> String {
        if e.code == ApprovalSignRules.signatureRequired && !ApprovalHooks.signer.available { return L(R.string.approval_pay_on_phone) }
        return ApprovalSigning.serverMessage(e) ?? L(R.string.approval_sign_failed)
    }
}
