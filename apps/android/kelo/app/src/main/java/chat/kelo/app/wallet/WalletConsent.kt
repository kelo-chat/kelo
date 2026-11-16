package chat.kelo.app.wallet

import android.content.Context

/**
 * The risk notices the owner accepts: before a card is saved (shown with its checkbox every time) and before auto-pay is turned on
 * for a card. What was accepted is kept on this phone only, as the notice's version and the time: the server's wallet API has no
 * field for it (POST/PATCH /api/wallet/cards ignore unknown fields).
 */
class PrefsWalletConsent(context: Context) : WalletConsent {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** The card notice was accepted for a card that was then saved. */
    override fun cardSaved(cardId: String, at: Long) {
        prefs.edit().putString(ConsentRules.cardKey(cardId), ConsentRules.record(WalletConsent.CARD_NOTICE_VERSION, at)).apply()
    }

    /** The auto-pay notice was accepted for [cardId]. */
    override fun autoPayAccepted(cardId: String, at: Long) {
        prefs.edit().putString(ConsentRules.autoPayKey(cardId), ConsentRules.record(WalletConsent.AUTOPAY_NOTICE_VERSION, at)).apply()
    }

    override fun forget(cardId: String) {
        prefs.edit().remove(ConsentRules.cardKey(cardId)).remove(ConsentRules.autoPayKey(cardId)).apply()
    }

    private companion object {
        const val PREFS = "kelo.wallet.consent"
    }
}

