package chat.kelo.app.chat

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * Unofficial providers: ones that sign in with a consumer account (a Claude or ChatGPT subscription, Antigravity, Qwen, MiniMax)
 * whose maker has not confirmed it allows this use. They are listed last, under their own divider, and choosing one asks the
 * user once (per provider and consent version, recorded on the server so other phones do not ask again).
 */

/** Which service name the consent text uses for a provider. */
enum class ConsentService { CLAUDE_SUBSCRIPTION, ANTIGRAVITY, CHATGPT_SUBSCRIPTION, QWEN, MINIMAX, OTHER }

object ProviderConsentRules {
    /** Unofficial and not (or not for this version) agreed to. */
    fun needs(p: ProviderInfo): Boolean = p.unofficial && (p.consent == null || p.consent.version < p.consentVersion)

    /** The divider group in a provider list: unofficial ones go last. */
    fun group(p: ProviderInfo): Int = if (p.unofficial) 1 else 0

    fun service(name: String): ConsentService = when {
        name.startsWith("claude-subscription") -> ConsentService.CLAUDE_SUBSCRIPTION
        name.startsWith("antigravity") -> ConsentService.ANTIGRAVITY
        name.startsWith("openai-codex") -> ConsentService.CHATGPT_SUBSCRIPTION
        name.startsWith("qwen") -> ConsentService.QWEN
        name.startsWith("minimax") -> ConsentService.MINIMAX
        else -> ConsentService.OTHER
    }

    /** The Korean subject particle after a vendor's name, as it is read aloud (Anthropic이, Google이, OpenAI가, Alibaba가, MiniMax가). */
    fun subjectParticle(vendor: String): String {
        KNOWN[vendor.trim().lowercase()]?.let { return it }
        val last = vendor.trim().lastOrNull() ?: return "가"
        if (last in '가'..'힣') return if ((last - '가') % 28 != 0) "이" else "가"
        return if (last.lowercaseChar() in "aeiouy") "가" else "이"
    }

    /**
     * [word] as it reads inside "Allow …?": in English a tool label is a capitalized "-ing" phrase ("Adding an event"), which reads
     * as "Allow adding an event?". Other languages and names (Gmail, GitHub) are kept as they are.
     */
    fun askWord(word: String, language: String): String {
        if (language != "en") return word
        val first = word.substringBefore(' ')
        if (first.length < 4 || !first.endsWith("ing") || !first[0].isUpperCase() || first.drop(1).any { it.isUpperCase() }) return word
        return word[0].lowercaseChar() + word.substring(1)
    }

    /** The Korean object particle after [word] (사이트 열기를, 로그인 정보 입력을); Latin endings are read as they sound in most names. */
    fun objectParticle(word: String): String {
        val last = word.trim().lastOrNull { it.isLetterOrDigit() } ?: return "를"
        if (last in '가'..'힣') return if ((last - '가') % 28 != 0) "을" else "를"
        if (last.isDigit()) return if (last in "013678") "을" else "를"
        return if (last.lowercaseChar() in "aeiouy") "를" else "을"
    }

    private val KNOWN = mapOf("anthropic" to "이", "google" to "이", "openai" to "가", "alibaba" to "가", "minimax" to "가")

    /** The providers among the chosen main and fallback that still need the user's agreement, main first. */
    fun pending(main: String, fallback: String, providers: List<ProviderInfo>): List<ProviderInfo> =
        listOf(main, fallback).filter { it.isNotEmpty() }.distinct().mapNotNull { n -> providers.firstOrNull { it.name == n } }.filter(::needs)

    /** "consent_required:<provider>" in a 428 from PUT /api/profile, or null. */
    fun requiredBy(e: ApiError): String? =
        if (e.status == 428) e.detail.substringAfter("consent_required:", "").trim().ifEmpty { null } else null

    /** The provider after this phone sent the agreement (so the list stops asking at once, before it is fetched again). */
    fun accepted(p: ProviderInfo, nowSeconds: Double): ProviderInfo = p.copy(consent = ProviderConsentRecord(nowSeconds, p.consentVersion))

    /** The key under which this phone remembers it already asked about a saved provider (the model page asks that only once). */
    fun askedKey(p: ProviderInfo): String = "asked." + p.name + "." + p.consentVersion
}

/** What a provider list remembers about consent while it is open: what was agreed to here, and the question on screen. */
@Stable
class ConsentGate(private val scope: CoroutineScope, private val api: () -> KeloApi, private val prefs: android.content.SharedPreferences) {
    var asking by mutableStateOf<ProviderInfo?>(null)
        private set
    var accepted by mutableStateOf(setOf<String>())
        private set
    private var onYes: () -> Unit = {}
    private var onNo: () -> Unit = {}
    var error by mutableStateOf<String?>(null)
        private set

    fun needs(p: ProviderInfo): Boolean = p.name !in accepted && ProviderConsentRules.needs(p)

    /** Runs [then] at once when [p] needs no agreement, else after the user agreed (and the server recorded it). */
    fun request(p: ProviderInfo?, onDecline: () -> Unit = {}, then: () -> Unit) {
        if (p == null || !needs(p)) { then(); return }
        onYes = then
        onNo = onDecline
        error = null
        asking = p
    }

    fun alreadyAsked(p: ProviderInfo): Boolean = prefs.getBoolean(ProviderConsentRules.askedKey(p), false)
    fun markAsked(p: ProviderInfo) = prefs.edit().putBoolean(ProviderConsentRules.askedKey(p), true).apply()

    internal fun decline() {
        asking?.let(::markAsked)
        asking = null
        onNo()
    }

    internal fun agree() {
        val p = asking ?: return
        scope.launch {
            try {
                api().put<JsonElement>("/api/providers/${KeloApi.segment(p.name)}/consent", buildJsonObject {
                    put("accepted", true)
                    put("version", p.consentVersion)
                })
                accepted = accepted + p.name
                markAsked(p)
                asking = null
                onYes()
            } catch (e: ApiError) {
                error = e.detail
            }
        }
    }
}

@Composable
fun rememberConsentGate(): ConsentGate {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    return remember(sessions) {
        ConsentGate(scope, { sessions.requireApi() }, context.getSharedPreferences("kelo_consent", Context.MODE_PRIVATE))
    }
}

/** The question itself; shown while [gate] asks. */
@Composable
fun ConsentDialog(gate: ConsentGate, displayName: (ProviderInfo) -> String = { it.displayName }) {
    val p = gate.asking ?: return
    val c = Kelo.colors
    var checked by remember(p.name) { mutableStateOf(false) }
    val service = when (ProviderConsentRules.service(p.name)) {
        ConsentService.CLAUDE_SUBSCRIPTION -> stringResource(R.string.consent_service_claude)
        ConsentService.CHATGPT_SUBSCRIPTION -> stringResource(R.string.consent_service_chatgpt)
        ConsentService.ANTIGRAVITY -> "Antigravity"
        ConsentService.QWEN -> "Qwen"
        ConsentService.MINIMAX -> "MiniMax"
        ConsentService.OTHER -> displayName(p)
    }
    val vendor = p.vendor.ifBlank { displayName(p) }
    AlertDialog(
        onDismissRequest = { gate.decline() }, containerColor = c.card,
        title = { Text(stringResource(R.string.consent_title, service), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    stringResource(R.string.consent_body, service, vendor + ProviderConsentRules.subjectParticle(vendor), vendor),
                    color = c.ink, style = MaterialTheme.typography.bodyMedium,
                )
                Row(
                    Modifier.fillMaxWidth().toggleable(value = checked, role = Role.Checkbox, onValueChange = { checked = it }).padding(vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = checked, onCheckedChange = null,
                        colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent, uncheckedColor = c.muted))
                    Text(stringResource(R.string.consent_check), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 8.dp))
                }
                gate.error?.let { Text(it, color = c.danger, style = MaterialTheme.typography.bodySmall) }
            }
        },
        confirmButton = {
            TextButton(onClick = { gate.agree() }, enabled = checked) { Text(stringResource(R.string.consent_use), color = if (checked) c.accent else c.muted) }
        },
        dismissButton = { TextButton(onClick = { gate.decline() }) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}
