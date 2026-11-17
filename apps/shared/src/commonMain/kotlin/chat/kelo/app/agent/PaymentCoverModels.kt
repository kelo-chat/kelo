package chat.kelo.app.agent

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

/** One thing in an order approved in advance. */
@Serializable
data class PreapprovalItem(val description: String = "", val qty: Int = 1)

/** One order the user approved in advance (GET /api/permissions/preapprovals): what, the all-in cap, until when, for which task or schedule. */
@Serializable
data class Preapproval(
    val id: String,
    @SerialName("owner_kind") val ownerKind: String = "",
    /** The schedule's name; "" for a task */
    @SerialName("owner_name") val ownerName: String = "",
    val intent: String = "",
    val items: List<PreapprovalItem> = emptyList(),
    val requirements: List<String> = emptyList(),
    val shipping: String = "",
    @SerialName("merchant_policy") val merchantPolicy: String = "",
    @SerialName("delegated_choices") val delegatedChoices: List<String> = emptyList(),
    @SerialName("max_total") val maxTotal: Double = 0.0,
    val currency: String = "",
    /** Unix seconds */
    @SerialName("expires_at") val expiresAt: Long = 0,
) {
    val forSchedule: Boolean get() = ownerKind == "schedule"
}

@Serializable
data class PreapprovalList(val enabled: Boolean = true, val items: List<Preapproval> = emptyList())

/** What an Allow on a scheduled task's payment card also covers: the same shop and the same payment method (GET /api/permissions/pay-allows). */
@Serializable
data class PayAllow(
    val id: String,
    @SerialName("schedule_name") val scheduleName: String = "",
    @SerialName("merchant_origin") val merchantOrigin: String = "",
    @SerialName("payment_label") val paymentLabel: String = "",
    val currency: String = "",
)

@Serializable
data class PayAllowList(val enabled: Boolean = true, val items: List<PayAllow> = emptyList())

/** The "Payments approved in advance" section: the switch and both lists. */
data class PaymentCover(val enabled: Boolean, val orders: List<Preapproval> = emptyList(), val allows: List<PayAllow> = emptyList()) {
    val empty: Boolean get() = orders.isEmpty() && allows.isEmpty()
}

/**
 * The approval card of `order_preapprove`: the server's extra field `preapproval` (the spec, the all-in cap and when it ends).
 * [maxTotalText] is the server's own amount text; the card falls back to the number and currency.
 */
data class PreapprovalAsk(
    val intent: String = "",
    val items: List<PreapprovalItem> = emptyList(),
    val requirements: List<String> = emptyList(),
    val shipping: String = "",
    val merchantPolicy: String = "",
    val delegatedChoices: List<String> = emptyList(),
    val maxTotal: Double = 0.0,
    val currency: String = "",
    val maxTotalText: String = "",
    val expiresAt: Long = 0,
    val ownerKind: String = "",
) {
    val forSchedule: Boolean get() = ownerKind == "schedule"

    companion object {
        /** Null when the card carries nothing a person could read (no what, no cap): it then shows as an ordinary card. */
        fun parse(e: JsonElement?): PreapprovalAsk? {
            val o = e as? JsonObject ?: return null
            fun s(k: String) = (o[k] as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim().orEmpty()
            fun lines(k: String) = (o[k] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content?.trim()?.ifEmpty { null } }
            val items = (o["items"] as? JsonArray).orEmpty().mapNotNull { i ->
                val it = i as? JsonObject ?: return@mapNotNull null
                val d = (it["description"] as? JsonPrimitive)?.takeIf { p -> p.isString }?.content?.trim().orEmpty()
                if (d.isEmpty()) null else PreapprovalItem(d, ((it["qty"] as? JsonPrimitive)?.longOrNull ?: 1L).toInt().coerceAtLeast(1))
            }
            val ask = PreapprovalAsk(
                s("intent"), items, lines("requirements"), s("shipping"), s("merchant_policy"), lines("delegated_choices"),
                (o["max_total"] as? JsonPrimitive)?.doubleOrNull ?: 0.0, s("currency").uppercase(), s("max_total_text"),
                (o["expires_at"] as? JsonPrimitive)?.longOrNull ?: 0L, s("owner_kind"),
            )
            return ask.takeIf { it.maxTotal > 0.0 && (it.intent.isNotEmpty() || it.items.isNotEmpty()) }
        }
    }
}

/** The `payment_auto` event's note that the payment was covered in advance: [kind] "preapproval" or "schedule_allow", [via] the way it paid. */
data class PreapprovalCover(val kind: String, val via: String) {
    val bySchedule: Boolean get() = kind == KIND_ALLOW

    companion object {
        const val KIND_ORDER = "preapproval"
        const val KIND_ALLOW = "schedule_allow"

        fun parse(e: JsonElement?): PreapprovalCover? {
            val o = e as? JsonObject ?: return null
            fun s(k: String) = (o[k] as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim().orEmpty()
            val kind = s("kind")
            return if (kind.isEmpty()) null else PreapprovalCover(kind, s("via"))
        }
    }
}

/** What the permissions section and the cards decide, away from the screens so it can be tested. */
object PaymentCoverRules {
    /** The order that ends first on top (it needs the owner's eye soonest). */
    fun ordered(items: List<Preapproval>): List<Preapproval> = items.sortedBy { if (it.expiresAt > 0) it.expiresAt else Long.MAX_VALUE }

    fun withoutOrder(c: PaymentCover, id: String): PaymentCover = c.copy(orders = c.orders.filter { it.id != id })

    fun withoutAllow(c: PaymentCover, id: String): PaymentCover = c.copy(allows = c.allows.filter { it.id != id })

    /** What the order is: its sentence, else its items ("2 × running shoes, size 42"). */
    fun what(intent: String, items: List<PreapprovalItem>): String =
        intent.trim().ifEmpty { items.joinToString(", ") { item(it) } }

    /** The card's "what": the order's sentence, then its items, one per line (the items alone when there is no sentence). */
    fun whatLines(p: PreapprovalAsk): List<String> = listOfNotNull(p.intent.takeIf { it.isNotBlank() }) + p.items.map { item(it) }

    fun item(i: PreapprovalItem): String = if (i.qty > 1) "${i.qty} × ${i.description}" else i.description

    /** A card's all-in cap as text: the server's own text, else the number and the currency code. */
    fun capText(ask: PreapprovalAsk): String = ask.maxTotalText.ifEmpty { amount(ask.maxTotal, ask.currency) }

    fun amount(v: Double, currency: String): String {
        val n = if (v == Math.floor(v) && v < 1e12) v.toLong().toString() else String.format(java.util.Locale.ROOT, "%.2f", v)
        return listOf(n, currency).filter { it.isNotEmpty() }.joinToString(" ")
    }
}

/** GET/PUT /api/permissions/payment-cover. */
@Serializable
data class PaymentCoverSwitch(val enabled: Boolean = true)
