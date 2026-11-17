package chat.kelo.app.chat

/**
 * What the chat's state layer needs from the app around it, which only the app can do (each app provides one; the defaults suit tests).
 */
interface ChatPlatform {
    /** A steady clock in milliseconds (the phone's time since boot) the header's activity line counts quiet time with. */
    fun uptimeMs(): Long = System.nanoTime() / 1_000_000

    /** An approval was answered "allow": a text, call or calendar change may now wait for this phone (the phone starts that work). */
    fun afterApproval() {}

    /** A waiting item (an approval or request card, by its id) was answered here: its notification, if one is up, is taken down at once. */
    fun itemSettled(id: String) {}

    /** This phone's approval key for signed payment Allows ([chat.kelo.app.approval.ApprovalSigner.None]: this device has none). */
    val approvalSigner: chat.kelo.app.approval.ApprovalSigner get() = chat.kelo.app.approval.ApprovalSigner.None

    /** A line for the developer's log (Android: Log.w). */
    fun log(tag: String, message: String, error: Throwable? = null) {}

    companion object {
        val Default: ChatPlatform = object : ChatPlatform {}
    }
}
