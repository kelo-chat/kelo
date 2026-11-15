package chat.kelo.desktop.pages.my.ops

import chat.kelo.app.core.KeloApi
import chat.kelo.app.wallet.PauseState
import chat.kelo.app.wallet.WalletCard
import chat.kelo.app.wallet.WalletInfo
import chat.kelo.app.wallet.WalletLimit
import chat.kelo.app.wallet.WalletLimits
import chat.kelo.app.wallet.WalletNotifications
import chat.kelo.app.wallet.WalletPayments
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// The wallet endpoints of android/kelo .../wallet/WalletCalls.kt that carry no card details (that file also sends cards with the
// phone's key, which a desktop does not have). Keep in step with it.

/** server.py WALLET API: reading the cards and payments, the pause and limits, renaming, auto-pay off, removing. */
internal class WalletCalls(private val api: KeloApi) {
    suspend fun info(): WalletInfo = api.get("/api/wallet")
    suspend fun limits(): WalletLimits = api.get("/api/wallet/limits")
    suspend fun payments(limit: Int = 100): WalletPayments = api.get("/api/wallet/payments?limit=$limit")
    suspend fun notifications(): WalletNotifications = api.get("/api/wallet/notifications")
    suspend fun markSeen(upTo: Long) { api.post<JsonElement>("/api/wallet/notifications/seen", buildJsonObject { put("up_to", upTo) }) }
    suspend fun pause(paused: Boolean): PauseState = api.put("/api/wallet/pause", buildJsonObject { put("paused", paused) })
    suspend fun setLimit(currency: String, perPayment: Double, monthly: Double): WalletLimit =
        api.put("/api/wallet/limits/${KeloApi.segment(currency)}", buildJsonObject { put("per_payment", perPayment); put("monthly", monthly) })
    suspend fun resetLimit(currency: String) { api.delete<JsonElement>("/api/wallet/limits/${KeloApi.segment(currency)}") }
    /** [newDefault]: the card that becomes the default when the default card is deleted with two or more others left. */
    suspend fun delete(id: String, newDefault: String? = null) {
        val q = newDefault?.let { "?new_default=${KeloApi.segment(it)}" }.orEmpty()
        api.delete<JsonElement>("/api/wallet/cards/${KeloApi.segment(id)}$q")
    }
    suspend fun makeDefault(id: String): WalletCard = patch(id, buildJsonObject { put("default", true) })

    /** The payments made with one card, newest first; [before] is the last page's next_before_id. */
    suspend fun cardPayments(id: String, before: Long? = null, limit: Int = 20): WalletPayments =
        api.get("/api/wallet/cards/${KeloApi.segment(id)}/payments?limit=$limit" + (before?.let { "&before_id=$it" } ?: ""))
    /** PATCH of fields that carry no card details (label, auto_pay false). */
    suspend fun patch(id: String, fields: JsonObject): WalletCard = api.patch("/api/wallet/cards/${KeloApi.segment(id)}", fields)
}
