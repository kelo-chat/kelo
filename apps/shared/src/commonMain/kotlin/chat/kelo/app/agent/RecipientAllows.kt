package chat.kelo.app.agent

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * One piece of the data trail's question on an approval card: data of one [kind] (mail, calendar...) would go to [recipient].
 * [titles] are the names of the items (never their content).
 */
data class TrailOffer(val recipient: String, val kind: String, val titles: List<String> = emptyList()) {
    /** The recipient as the user reads it: the server's internal prefixes dropped; null for the public link (the app words that). */
    val shown: String? get() = RecipientRules.shown(recipient)

    companion object {
        fun parseList(e: JsonElement?): List<TrailOffer> = (e as? JsonArray).orEmpty().mapNotNull { item ->
            val o = item as? JsonObject ?: return@mapNotNull null
            fun s(k: String) = (o[k] as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim().orEmpty()
            val recipient = s("recipient")
            val kind = s("kind")
            if (recipient.isEmpty() || kind.isEmpty()) return@mapNotNull null
            val titles = (o["titles"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content?.trim()?.ifEmpty { null } }
            TrailOffer(recipient, kind, titles.distinct())
        }
    }
}

/** A standing "always for this recipient" (GET /api/permissions/recipients). */
@Serializable
data class RecipientAllow(
    val id: Long,
    val recipient: String = "",
    val kind: String = "",
    @SerialName("created_at") val createdAt: Double = 0.0,
)

@Serializable
data class RecipientAllowList(val items: List<RecipientAllow> = emptyList())

/** What the data-trail card and the list page decide, away from the screens so it can be tested. */
object RecipientRules {
    /** The kinds the server names (data_trail.SOURCE_WORDS); any other kind is shown as plain "data". */
    val KINDS: List<String> = listOf(
        "mail", "file", "memory", "contacts", "calendar", "photos", "messages", "notes", "vault", "profile", "web", "channel", "device", "connector",
    )

    fun knownKind(kind: String): String = if (kind in KINDS) kind else "other"

    const val LINK = "link"

    /** The recipient without the server's internal prefixes; null for the public link and for an empty one. */
    fun shown(recipient: String): String? {
        val r = recipient.trim().removePrefix("site:").removePrefix("connector:").trim()
        return if (r.isEmpty() || r == LINK) null else r
    }

    /** The card offers "always for this recipient" only when the server named at least one recipient. */
    fun canAlways(offers: List<TrailOffer>): Boolean = offers.any { it.recipient.isNotBlank() && it.kind.isNotBlank() }

    /** The three answers of a data-trail card, as the body of POST /api/approvals/{id}: approve and the value for "remember". */
    enum class Answer(val approve: Boolean, val remember: String?) {
        ONCE(true, null), ALWAYS(true, "recipient"), DENY(false, null)
    }

    /** The list page shows the newest first. */
    fun ordered(items: List<RecipientAllow>): List<RecipientAllow> = items.sortedByDescending { it.id }

    fun without(items: List<RecipientAllow>, id: Long): List<RecipientAllow> = items.filter { it.id != id }
}
