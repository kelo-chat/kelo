package chat.kelo.app.my

import chat.kelo.app.R
import chat.kelo.app.core.KeloApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// From android/kelo .../my/OperatorPage.kt, OperatorReports.kt and OperatorBeta.kt (the models and rules; those files also hold the
// Android UI). Keep in step with them: the Android unit test my/OperatorRulesTest.kt runs here too.

/** GET /api/operator/firebase: the operator's push key, never the key itself. */
@Serializable
data class FirebaseConfig(
    val enabled: Boolean = false,
    @SerialName("project_id") val projectId: String = "",
    @SerialName("client_email") val clientEmail: String = "",
)

/** GET /api/operator/cloudflare: the free-address service's settings (the API token is only "stored or not"). */
@Serializable
data class CloudflareConfig(
    @SerialName("account_id") val accountId: String = "",
    @SerialName("zone_id") val zoneId: String = "",
    @SerialName("zone_name") val zoneName: String = "",
    @SerialName("has_token") val hasToken: Boolean = false,
    @SerialName("max_names") val maxNames: Int = 0,
    val used: Int = 0,
    val enabled: Boolean = false,
    @SerialName("idle_days") val idleDays: Int = 30,
)

@Serializable
data class Reclaimed(val released: List<String> = emptyList())

/** GET /api/operator/status (owner sign-ins only): whether this server is the operator's (the one other copies lean on). */
@Serializable
data class OperatorStatus(val owner: Boolean = false, val operator: Boolean = false)

/** What the Cloudflare form holds before it is sent. */
data class CloudflareForm(val token: String = "", val accountId: String = "", val zoneId: String = "", val zoneName: String = "", val maxNames: String = "")

/** The operator page's checks and request bodies, apart from the UI so they can be tested. Nothing here keeps a secret. */
object OperatorRules {
    private val HEX32 = Regex("^[0-9a-f]{32}$")
    private val DOMAIN = Regex("^([a-z0-9-]+\\.)+[a-z]{2,63}$")
    private val TOKEN = Regex("^[A-Za-z0-9_-]{20,200}$")
    const val MAX_KEY_FILE = 20_000

    /** The Cloudflare id fields: empty (left as they are) or the 32-character id the dashboard shows. */
    fun idOk(s: String): Boolean = s.isEmpty() || HEX32.matches(s.trim().lowercase())

    fun zoneOk(s: String): Boolean = s.isEmpty() || DOMAIN.matches(s.trim().lowercase())

    /** A new API token: empty keeps the stored one. */
    fun tokenOk(s: String): Boolean = s.isEmpty() || TOKEN.matches(s.trim())

    fun maxOk(s: String): Boolean = s.isBlank() || (s.trim().toIntOrNull() ?: 0) in 1..100_000

    /** Which field is wrong first (a string resource), or null when the form can be sent. */
    fun problem(f: CloudflareForm): Int? = when {
        !tokenOk(f.token) -> R.string.operator_cf_bad_token
        !idOk(f.accountId) -> R.string.operator_cf_bad_account
        !idOk(f.zoneId) -> R.string.operator_cf_bad_zone_id
        !zoneOk(f.zoneName) -> R.string.operator_cf_bad_zone
        !maxOk(f.maxNames) -> R.string.operator_cf_bad_max
        else -> null
    }

    /** PUT /api/operator/cloudflare: only what is filled in; an empty token field leaves the stored token alone. */
    fun cloudflareBody(f: CloudflareForm): JsonObject = buildJsonObject {
        if (f.token.isNotBlank()) put("api_token", f.token.trim())
        if (f.accountId.isNotBlank()) put("account_id", f.accountId.trim().lowercase())
        if (f.zoneId.isNotBlank()) put("zone_id", f.zoneId.trim().lowercase())
        if (f.zoneName.isNotBlank()) put("zone_name", f.zoneName.trim().lowercase())
        f.maxNames.trim().toIntOrNull()?.let { put("max_names", it) }
    }

    /** Turning the service off: the token is removed, everything else stays. */
    fun cloudflareOff(): JsonObject = buildJsonObject { put("api_token", "") }

    /** The form, filled from what the server has (never the token). */
    fun formOf(c: CloudflareConfig): CloudflareForm =
        CloudflareForm(accountId = c.accountId, zoneId = c.zoneId, zoneName = c.zoneName, maxNames = if (c.maxNames > 0) c.maxNames.toString() else "")

    /** A picked file that can be a service-account key: small, and a JSON object. Its content is never shown. */
    fun keyFileOk(bytes: ByteArray): Boolean {
        if (bytes.isEmpty() || bytes.size > MAX_KEY_FILE) return false
        val text = bytes.toString(Charsets.UTF_8)
        return runCatching { KeloApi.json.parseToJsonElement(text) is JsonObject }.getOrDefault(false)
    }

    /** The sign-in this app uses is the owner's own token: an operator GET answered (403 = a paired phone, not the owner). */
    fun ownerFrom(status: Int?): Boolean? = when (status) {
        null -> true
        403 -> false
        else -> null
    }

    /**
     * Whether 마이 shows the operator section, from GET /api/operator/status: only an owner sign-in ([status] null) on the operator's
     * server ([operator] true). Every self-hosted copy answers false, so its owner's own phone does not see it. 403 = not an owner
     * sign-in. 404 = a server from before this call: [fallback] (the older rule, [ownerFrom] on GET /api/operator/firebase).
     * Anything else: not known (null), the last value stays.
     */
    fun operatorFrom(status: Int?, operator: Boolean?, fallback: () -> Boolean? = { null }): Boolean? = when (status) {
        null -> operator == true
        403 -> false
        404 -> fallback()
        else -> null
    }
}

/** One report another copy (or this one) sent, as GET /api/operator/reports lists it. */
@Serializable
data class ReceivedReport(
    val id: Long = 0,
    @SerialName("created_at") val createdAt: Double = 0.0,
    val reason: String = "",
    val comment: String = "",
    val reply: String = "",
    val asked: String = "",
    val provider: String = "",
    val model: String = "",
    @SerialName("app_version") val appVersion: String = "",
    val handled: Boolean = false,
)

@Serializable
data class ReceivedReports(
    val reports: List<ReceivedReport> = emptyList(),
    val open: Int = 0,
    @SerialName("keep_days") val keepDays: Int = 365,
    val receiving: Boolean = false,
)

object OperatorReportRules {
    /** The list request: open reports only, or all of them. */
    fun path(showHandled: Boolean): String = if (showHandled) "/api/operator/reports" else "/api/operator/reports?handled=false"

    /** "provider / model", or just one of them, or null when neither is known. */
    fun modelName(r: ReceivedReport): String? = listOf(r.provider, r.model).filter { it.isNotBlank() }.joinToString(" / ").ifBlank { null }
}

/** One request to try Google sign-in, as GET /api/operator/beta lists it. */
@Serializable
data class BetaRequestRow(
    val id: Long = 0,
    @SerialName("created_at") val createdAt: Double = 0.0,
    val email: String = "",
    val products: List<String> = emptyList(),
    val status: String = "requested",
    /** the email to support when it came in: "sent", "no_mailbox", "failed" or "" (still going) */
    val notice: String = "",
)

@Serializable
data class BetaRequests(
    val requests: List<BetaRequestRow> = emptyList(),
    val open: Int = 0,
    @SerialName("mail_from") val mailFrom: String = "",
    val receiving: Boolean = false,
)

@Serializable
data class BetaAnswer(
    val mailed: Boolean = false,
    @SerialName("mail_error") val mailError: String = "",
)

object OperatorBetaRules {
    /** "Gmail, Google Calendar" (product names stay as they are in every language). */
    fun products(ids: List<String>): String = ids.mapNotNull { mapOf("gmail" to "Gmail", "calendar" to "Google Calendar")[it] }.joinToString(", ")

    /** Approved / Decline show only while a request waits; an approved one can still be taken back with Decline. */
    fun canApprove(r: BetaRequestRow): Boolean = r.status == "requested"
    fun canDecline(r: BetaRequestRow): Boolean = r.status != "declined"
}

