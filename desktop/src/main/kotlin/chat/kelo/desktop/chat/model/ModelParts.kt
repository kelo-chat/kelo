package chat.kelo.desktop.chat.model

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.ModelChoice
import chat.kelo.app.chat.ModelList
import chat.kelo.app.chat.ModelNames
import chat.kelo.app.chat.ProviderConsentRules
import chat.kelo.app.chat.ProviderInfo
import chat.kelo.app.chat.SignOutResult
import chat.kelo.app.chat.SignOutRules
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.chat.ModelCatalog
import chat.kelo.desktop.chat.SelectField
import chat.kelo.desktop.chat.SelectOption
import chat.kelo.desktop.chat.providerLabel
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * The parts the model chooser, the model settings panel and 마이 → 모델 share, as on the phone: the unofficial-provider question
 * (android/kelo .../chat/ProviderConsent.kt ConsentGate / ConsentDialog), the signed-in account with 로그아웃 (.../chat/SignOut.kt)
 * and the provider + model picker of the fallback (.../chat/ModelSheet.kt ModelPicker).
 */

/** What a provider list remembers about consent while it is open: what was agreed to here, and the question on screen. */
@Stable
class ConsentGate(private val scope: CoroutineScope, private val api: () -> KeloApi) {
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

    /** Asked already in this run about a saved provider (the phone keeps it in its preferences; the model page asks once). */
    fun alreadyAsked(p: ProviderInfo): Boolean = ProviderConsentRules.askedKey(p) in ASKED
    private fun markAsked(p: ProviderInfo) { ASKED += ProviderConsentRules.askedKey(p) }

    fun decline() {
        asking?.let(::markAsked)
        asking = null
        onNo()
    }

    fun agree() {
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

    private companion object {
        val ASKED: MutableSet<String> = java.util.concurrent.ConcurrentHashMap.newKeySet()
    }
}

@Composable
fun rememberConsentGate(sessions: Sessions): ConsentGate {
    val scope = rememberCoroutineScope()
    return remember(sessions) { ConsentGate(scope, { sessions.requireApi() }) }
}

/** The question itself; shown while [gate] asks. */
@Composable
fun ConsentDialog(gate: ConsentGate, providers: List<ProviderInfo>) {
    val p = gate.asking ?: return
    val c = Kelo.colors
    var checked by remember(p.name) { mutableStateOf(false) }
    val service = providerLabel(p.name, providers + p)
    val vendor = p.vendor.ifBlank { service }
    AlertDialog(
        onDismissRequest = { gate.decline() }, containerColor = c.card,
        title = { Text(stringResource(R.string.consent_title, service), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(stringResource(R.string.consent_body, service, vendor + ProviderConsentRules.subjectParticle(vendor), vendor), color = c.ink,
                    style = MaterialTheme.typography.bodyMedium)
                Row(Modifier.fillMaxWidth().clickable(role = Role.Checkbox) { checked = !checked }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = checked, onCheckedChange = null, colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent, uncheckedColor = c.muted))
                    Text(stringResource(R.string.consent_check), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 8.dp))
                }
                gate.error?.let { Text(it, color = c.danger, style = MaterialTheme.typography.bodySmall) }
            }
        },
        confirmButton = { TextButton(onClick = { gate.agree() }, enabled = checked) { Text(stringResource(R.string.consent_use), color = if (checked) c.accent else c.muted) } },
        dismissButton = { TextButton(onClick = { gate.decline() }) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/**
 * The signed-in account of a provider reached by signing in (Claude, agy, Codex, Qwen, MiniMax) and 로그아웃 with a confirm step
 * (DELETE /api/providers/{name}/login). [onResult] runs after the server answered; the caller shows it with [SignOutMessage] and,
 * when it is done, reloads the list (the row then shows it is not connected and this box goes away).
 */
@Composable
fun SignOutBox(
    sessions: Sessions,
    name: String,
    displayName: String,
    account: String?,
    mainProvider: String,
    /** For the saved main model: only the account line (the chat's settings), instead of the note why it cannot sign out */
    quietWhenMain: Boolean = false,
    onResult: (outcome: SignOutRules.Outcome, result: SignOutResult?, detail: String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var confirm by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    val main = SignOutRules.isMain(name, mainProvider)

    fun signOut() {
        busy = true
        scope.launch {
            var result: SignOutResult? = null
            var error: ApiError? = null
            try {
                result = sessions.requireApi().delete<SignOutResult>("/api/providers/${KeloApi.segment(name)}/login")
            } catch (e: ApiError) {
                error = e
            } finally {
                busy = false
            }
            onResult(SignOutRules.outcome(result, error), result, error?.takeIf { !it.isNetwork }?.detail.orEmpty())
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        MutedText(if (account.isNullOrBlank()) stringResource(R.string.model_signin_connected) else stringResource(R.string.model_signin_account, account), small = true)
        when {
            busy -> MutedText(stringResource(R.string.model_signout_busy), small = true)
            main && quietWhenMain -> {}
            main -> MutedText(stringResource(R.string.model_signout_main), small = true)
            else -> LinkButton(stringResource(R.string.model_signout), onClick = { confirm = true }, danger = true)
        }
    }
    if (confirm) {
        ConfirmDialog(
            text = stringResource(R.string.model_signout_confirm, displayName), yes = stringResource(R.string.model_signout),
            onYes = ::signOut, onDismiss = { confirm = false },
        )
    }
}

/** The line after an answer; the caller keeps it, since the box itself goes away once the provider is signed out. */
@Composable
fun SignOutMessage(outcome: SignOutRules.Outcome?, detail: String = "") {
    when (outcome) {
        null -> {}
        SignOutRules.Outcome.DONE -> MutedText(stringResource(R.string.model_signout_done), small = true)
        SignOutRules.Outcome.DONE_FALLBACK_CLEARED -> MutedText(stringResource(R.string.model_signout_fallback_cleared), small = true)
        SignOutRules.Outcome.DONE_ENV_LEFT -> ErrorText(stringResource(R.string.model_signout_env_left), small = true)
        SignOutRules.Outcome.MAIN_MODEL -> ErrorText(stringResource(R.string.model_signout_main), small = true)
        SignOutRules.Outcome.FAILED -> ErrorText(stringResource(R.string.model_signout_failed, detail).trim(), small = true)
    }
}

private const val CUSTOM = "__custom__"

/**
 * Provider + model in one block; the model list is whatever that provider really offers. Unofficial providers come last, under
 * their own divider; choosing one goes through [gate]. [exclude]: the main model's provider, shown but not choosable (the
 * fallback never shares a provider with the main model).
 */
@Composable
fun ModelPicker(
    sessions: Sessions,
    providers: List<ProviderInfo>,
    value: ModelChoice,
    onChange: (ModelChoice) -> Unit,
    noneLabel: String? = null,
    exclude: String? = null,
    gate: ConsentGate? = null,
) {
    // the provider's last good list (kept on this computer) shows at once and is refreshed in place; never the previous provider's
    var list by remember { mutableStateOf(ModelCatalog.keptList(sessions, value.provider)) }
    var loading by remember { mutableStateOf(value.provider.isNotEmpty()) }   // the fetch below starts right after the first frame
    var slow by remember { mutableStateOf(false) }   // the refresh takes over ModelCatalog.SLOW_MS: a small hint shows
    var custom by remember { mutableStateOf(list.let { it != null && value.model.isNotEmpty() && value.model !in it.models }) }
    val failed = stringResource(R.string.model_picker_list_failed)
    val info = providers.find { it.name == value.provider }

    LaunchedEffect(value.provider) {
        val kept = ModelCatalog.keptList(sessions, value.provider)
        list = kept
        custom = kept != null && value.model.isNotEmpty() && value.model !in kept.models
        if (value.provider.isEmpty()) { loading = false; return@LaunchedEffect }
        loading = true
        val hint = launch { kotlinx.coroutines.delay(ModelCatalog.SLOW_MS); slow = true }
        try {
            val m = ModelCatalog.shown(ModelCatalog.fetch(sessions, value.provider), kept)!!
            list = m
            custom = m.models.isEmpty() || (value.model.isNotEmpty() && value.model !in m.models)
        } catch (e: ApiError) {
            list = ModelCatalog.shown(ModelList(value.provider, emptyList(), error = failed), kept)
        } finally {
            hint.cancel()
            loading = false
            slow = false
        }
    }

    // ready ones first, then the rest by name; the unofficial ones last under their heading
    val sorted = providers.sortedWith(compareBy<ProviderInfo>({ ProviderConsentRules.group(it) }, { if (it.configured == true) 0 else 1 }, { it.displayName.lowercase() }))
    val providerOptions = buildList {
        add(SelectOption("", noneLabel ?: stringResource(R.string.model_picker_choose_provider)))
        sorted.forEach { p ->
            val taken = !exclude.isNullOrEmpty() && p.name == exclude
            var text = when (p.configured) {
                true -> stringResource(R.string.model_picker_ready, p.displayName)
                false -> stringResource(R.string.model_picker_needs_setup, p.displayName)
                null -> p.displayName
            }
            if (taken) text = stringResource(R.string.model_picker_taken, text)
            add(SelectOption(p.name, text, group = ProviderConsentRules.group(p), enabled = !taken))
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SelectField(providerOptions, value.provider, noneLabel ?: stringResource(R.string.model_picker_choose_provider),
            groupLabel = stringResource(R.string.model_picker_unofficial), onPick = { v ->
                val p = providers.firstOrNull { it.name == v }
                if (gate == null) onChange(ModelChoice(v, "")) else gate.request(p) { onChange(ModelChoice(v, "")) }
            })
        if (value.provider.isNotEmpty() && info?.configured == false) ErrorText(stringResource(R.string.model_picker_not_ready), small = true)
        if (value.provider.isNotEmpty()) {
            // nothing kept yet and still loading: the chosen model alone, by name (never the raw id in a text field)
            val l = list ?: if (loading) ModelCatalog.placeholder(value.provider, value.model) else null
            val showSelect = l != null && l.models.isNotEmpty()
            if (showSelect) {
                val names = ModelNames.displayAll(l!!.models, l.labels)
                val options = l.models.map { SelectOption(it, names.getValue(it)) } + SelectOption(CUSTOM, stringResource(R.string.model_picker_custom))
                SelectField(options, if (custom) CUSTOM else value.model, stringResource(if (slow) R.string.loading else R.string.model_picker_choose_model), onPick = { v ->
                    if (v == CUSTOM) custom = true else { custom = false; onChange(ModelChoice(value.provider, v)) }
                })
            }
            if (custom || (l != null && !showSelect)) {
                KeloTextField(value.model, { onChange(ModelChoice(value.provider, it)) }, stringResource(R.string.model_picker_custom_placeholder), maxLength = 200)
            }
            if (slow) MutedText(stringResource(R.string.loading), small = true)
            l?.error?.let { MutedText(it, small = true) }
            if (l?.stale == true) MutedText(stringResource(R.string.model_list_stale), small = true)
        }
    }
}
