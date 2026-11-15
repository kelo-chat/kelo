package chat.kelo.app.wallet

// The enum of android/kelo .../wallet/WalletCalls.kt (that file also needs the phone's card lock). Keep in step with it.

/** Why sending an end-to-end card for an allowed payment did not work. */
enum class CardSendError {
    /** 404: nothing waits for it any more (used, denied, timed out) */
    NOT_WAITING,
    /** 422: the server turned the card down (another card's approval, or the number does not match) */
    MISMATCH,
    /** This phone cannot open the card's blob (made on another phone, or the key was lost) */
    NOT_ON_PHONE,
    /** The phone has no screen lock */
    NO_LOCK,
    /** The user backed out of the fingerprint / screen lock check */
    CANCELLED,
    NETWORK,
    OTHER,
}
