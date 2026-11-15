package chat.kelo.desktop.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.chat.EffortChoice
import chat.kelo.app.chat.EndpointRules
import chat.kelo.app.chat.ModelChooserRules
import chat.kelo.app.chat.KeyRules
import chat.kelo.app.chat.ModelChoice
import chat.kelo.app.chat.ModelList
import chat.kelo.app.chat.ModelNames
import chat.kelo.app.chat.ModelRules
import chat.kelo.app.chat.ProviderConsentRules
import chat.kelo.app.chat.ProviderInfo
import chat.kelo.app.chat.SetupRules
import chat.kelo.app.chat.SignInRules
import chat.kelo.app.chat.SignInSession
import chat.kelo.app.chat.SignOutRules
import chat.kelo.desktop.chat.model.ConsentDialog
import chat.kelo.desktop.chat.model.ModelPicker
import chat.kelo.desktop.chat.model.SignOutBox
import chat.kelo.desktop.chat.model.SignOutMessage
import chat.kelo.desktop.chat.model.rememberConsentGate
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Profile
import chat.kelo.app.core.Sessions
import chat.kelo.app.my.CliStatus
import chat.kelo.app.my.CliUpdateRules
import chat.kelo.app.my.ProviderDetail
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.Browser
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private const val CUSTOM = "__custom__"

/** One choice in a [SelectField]. [group] 1 goes under the field's group heading (the unofficial providers). */
data class SelectOption(val value: String, val label: String, val group: Int = 0, val enabled: Boolean = true)

/** A field that opens a list to pick from (the provider, the model). The list scrolls; a long label wraps. */
@Composable
fun SelectField(
    options: List<SelectOption>,
    selected: String,
    placeholder: String,
    onPick: (String) -> Unit,
    modifier: Modifier = Modifier,
    groupLabel: String? = null,
) {
    val c = Kelo.colors
    var open by remember { mutableStateOf(false) }
    val shown = options.firstOrNull { it.value == selected }?.label
    Box(modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp).clip(RoundedCornerShape(12.dp)).border(1.dp, c.line, RoundedCornerShape(12.dp))
                .background(c.card).clickable(role = Role.DropdownList) { open = true }.padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(shown ?: placeholder, style = MaterialTheme.typography.bodyLarge, color = if (shown == null) c.muted else c.ink, modifier = Modifier.weight(1f))
            Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp).rotate(if (androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl) -90f else 90f))
        }
        DropdownMenu(open, onDismissRequest = { open = false }, modifier = Modifier.heightIn(max = 420.dp).widthIn(min = 280.dp, max = 520.dp).background(c.card)) {
            var lastGroup = 0
            options.forEach { o ->
                if (o.group != lastGroup && groupLabel != null) {
                    HorizontalDivider(color = c.line)
                    Text(groupLabel, style = MaterialTheme.typography.labelMedium, color = c.muted, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp))
                }
                lastGroup = o.group
                DropdownMenuItem(
                    text = { Text(o.label, color = if (o.enabled) c.ink else c.disabledInk, fontWeight = if (o.value == selected) FontWeight.SemiBold else FontWeight.Normal) },
                    enabled = o.enabled,
                    trailingIcon = if (o.value == selected) { { Icon(KeloIcons.Check, contentDescription = null, tint = c.accent, modifier = Modifier.size(18.dp)) } } else null,
                    onClick = { open = false; onPick(o.value) },
                )
            }
        }
    }
}

@Composable
private fun FieldLabel(text: String) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = Kelo.colors.ink)
}

/** A soft box under the provider field: its CLI, sign-in, address or key. */
@Composable
private fun SetupBox(content: @Composable () -> Unit) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(c.bubble).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) { content() }
}

/**
 * The model settings as a panel over the page (from the chat's model chooser: "자세한 설정", or a "needs setup" row): which AI
 * service answers and how it is reached — an API key, the address of an OpenAI-compatible server (LM Studio, Ollama, vLLM…), or a
 * sign-in in the browser, and 로그아웃 again —, the model, and the fallback. A brand-new server is set up entirely here.
 * [startProvider] opens on that service. The same settings are 마이 → 모델 ([ModelSettings] in a sub page).
 */
@Composable
fun ModelSetup(
    sessions: Sessions,
    profile: Profile,
    startProvider: String?,
    onProfile: (Profile) -> Unit,
    onProvidersChanged: (List<ProviderInfo>) -> Unit,
    onClose: () -> Unit,
) {
    val c = Kelo.colors
    Box(
        Modifier.fillMaxSize().background(c.scrim.copy(alpha = 0.32f))
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClose),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.widthIn(max = 560.dp).fillMaxWidth(0.92f).fillMaxHeight(0.88f).clip(RoundedCornerShape(20.dp)).background(c.card)
                .border(1.dp, c.line, RoundedCornerShape(20.dp))
                .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {},
        ) {
            Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.model_title), style = MaterialTheme.typography.titleLarge, color = c.ink, modifier = Modifier.weight(1f))
                IconButton(onClick = onClose) { Icon(KeloIcons.Close, contentDescription = stringResource(R.string.close), tint = c.muted) }
            }
            ModelSettings(
                sessions, profile, startProvider, onProfile, onProvidersChanged, onDone = onClose,
                body = { content -> Column(
                    Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) { content() } },
                footer = { content ->
                    HorizontalDivider(color = c.line)
                    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) { content() }
                },
            )
        }
    }
}

/**
 * The model settings themselves (the phone's My → Model page, android/kelo .../my/ModelPage.kt): [body] holds the fields, [footer]
 * the save button. [onDone] runs after saving (the panel closes, the sub page goes back). [cliProvider]: a newer CLI notice opened
 * the page for that provider's CLI; when another service is selected, that CLI's box (with 업데이트) shows first, on its own, so
 * the main and fallback choice stay as they are.
 */
@Composable
fun ModelSettings(
    sessions: Sessions,
    profile: Profile,
    startProvider: String?,
    onProfile: (Profile) -> Unit,
    onProvidersChanged: (List<ProviderInfo>) -> Unit,
    onDone: () -> Unit,
    body: @Composable (content: @Composable ColumnScope.() -> Unit) -> Unit,
    footer: @Composable (content: @Composable ColumnScope.() -> Unit) -> Unit,
    cliProvider: String? = null,
    /** The background work model row (My → Model only; the first-start panel leaves it out). */
    showBackground: Boolean = false,
) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var provider by remember { mutableStateOf(startProvider ?: profile.provider) }
    var bg by remember { mutableStateOf(ModelChoice(profile.backgroundProvider, profile.backgroundModel)) }
    var bgState by remember { mutableStateOf<chat.kelo.app.my.BackgroundModelState?>(null) }
    if (showBackground) LaunchedEffect(Unit) {   // the background model's last failure, if any (an older server: nothing shows)
        runCatching { sessions.requireApi().get<chat.kelo.app.my.UsageSummary>("/api/usage/summary?days=1") }.onSuccess { bgState = it.background }
    }
    var model by remember { mutableStateOf(if (startProvider == null || startProvider == profile.provider) profile.model else "") }
    var effort by remember { mutableStateOf(profile.effort) }
    var effortTouched by remember { mutableStateOf(false) }
    var fb by remember { mutableStateOf(ModelRules.afterMainChange(ModelChoice(provider, model), ModelRules.initialFallback(profile)).first) }
    var fbNote by remember { mutableStateOf(false) }
    // the last provider list and model list (kept on this computer) show at once and are refreshed in place (ModelCatalog)
    var providers by remember { mutableStateOf(ModelCatalog.keptProviders(sessions).ifEmpty { null }) }
    var providersSlow by remember { mutableStateOf(false) }   // nothing kept and the list takes over ModelCatalog.SLOW_MS: "loading"
    var models by remember { mutableStateOf(ModelCatalog.keptList(sessions, provider)) }
    var loadingModels by remember { mutableStateOf(provider.isNotEmpty()) }   // loadModels starts right after the first frame
    var slowModels by remember { mutableStateOf(false) }   // a refresh over ModelCatalog.SLOW_MS: the 새로고침 link says it is loading
    var custom by remember { mutableStateOf(models.let { it != null && model.isNotEmpty() && model !in it.models }) }
    var keyInput by remember { mutableStateOf("") }
    var keyMsg by remember { mutableStateOf("") }
    var urlInput by remember { mutableStateOf("") }
    var editingUrl by remember { mutableStateOf(false) }
    var urlMsg by remember { mutableStateOf("") }
    var savingUrl by remember { mutableStateOf(false) }
    var installing by remember { mutableStateOf(false) }
    var installMsg by remember { mutableStateOf("") }
    var updating by remember { mutableStateOf<String?>(null) }                              // the CLI provider being updated
    var updateOutcome by remember { mutableStateOf<Pair<String, CliUpdateRules.Outcome>?>(null) }   // provider name, how it went
    var askInstall by remember { mutableStateOf<ProviderDetail?>(null) }
    var confirmRemoveKey by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    var signOut by remember { mutableStateOf<Pair<String, SignOutRules.Outcome>?>(null) }   // provider name, answer
    var signOutDetail by remember { mutableStateOf("") }
    val gate = rememberConsentGate(sessions)
    val urlProblems = mapOf(
        EndpointRules.Problem.EMPTY to stringResource(R.string.model_url_empty),
        EndpointRules.Problem.SPACES to stringResource(R.string.model_url_spaces),
        EndpointRules.Problem.SCHEME to stringResource(R.string.model_url_scheme),
        EndpointRules.Problem.HOST to stringResource(R.string.model_url_host),
    )
    val listFailed = stringResource(R.string.model_picker_list_failed)
    val installed = stringResource(R.string.model_cli_installed_msg)

    suspend fun loadProviders() {
        runCatching { ModelCatalog.fetchProviders(sessions) }.onSuccess { list ->
            providers = list
            onProvidersChanged(list.map { it.asInfo() })
        }
    }

    fun loadModels(p: String, refresh: Boolean = false) {
        if (p.isEmpty()) { models = null; loadingModels = false; return }
        val kept = ModelCatalog.keptList(sessions, p)
        if (models?.provider != p) models = kept      // another provider: its own last list at once, never the previous one's
        loadingModels = true
        scope.launch {
            val hint = launch { delay(ModelCatalog.SLOW_MS); if (p == provider) slowModels = true }
            try {
                val m = ModelCatalog.fetch(sessions, p, refresh)
                if (p != provider) return@launch          // another provider was picked meanwhile
                val shown = ModelCatalog.shown(m, kept) ?: m
                models = shown
                custom = model.isNotEmpty() && model !in shown.models
            } catch (_: ApiError) {
                if (p == provider) models = ModelCatalog.shown(ModelList(p, emptyList(), error = listFailed), kept)
            } finally {
                hint.cancel()
                if (p == provider) { loadingModels = false; slowModels = false }
            }
        }
    }

    LaunchedEffect(Unit) {
        // with nothing kept, "loading" shows only when the list takes over ModelCatalog.SLOW_MS (and stays if it never comes)
        val hint = launch { delay(ModelCatalog.SLOW_MS); providersSlow = true }
        try { loadProviders() } finally { hint.cancel(); if (providers == null) providersSlow = true }
    }
    LaunchedEffect(provider) { loadModels(provider) }

    // opened on an unofficial service from the sheet: its notice first, as when it is picked in the list
    var startAsked by remember { mutableStateOf(false) }
    LaunchedEffect(providers) {
        val list = providers ?: return@LaunchedEffect
        if (!startAsked && startProvider != null) {
            startAsked = true
            list.firstOrNull { it.name == startProvider }?.asInfo()?.let { gate.request(it) {} }
            return@LaunchedEffect
        }
        // a saved choice that is unofficial and not agreed to (set up before the question existed): asked once, kept either way
        ProviderConsentRules.pending(profile.provider, profile.fallbackProvider, list.map { it.asInfo() })
            .firstOrNull { !gate.alreadyAsked(it) }
            ?.let { p -> gate.request(p) {} }
    }

    /** After a key, an address or a sign-in: the provider list again (it is ready now), and its models from the server. */
    fun refreshAfterSetup(p: String) {
        scope.launch { loadProviders(); loadModels(p, refresh = true) }
    }

    fun saveKey() {
        val p = provider
        keyMsg = ""
        scope.launch {
            try {
                sessions.requireApi().put<JsonElement>("/api/providers/${KeloApi.segment(p)}/key", buildJsonObject { put("api_key", keyInput.trim()) })
                keyInput = ""
                refreshAfterSetup(p)
            } catch (e: ApiError) {
                keyMsg = e.detail
            }
        }
    }

    fun removeKey() {
        val p = provider
        scope.launch {
            runCatching { sessions.requireApi().delete<JsonElement>("/api/providers/${KeloApi.segment(p)}/key") }
            keyMsg = ""
            refreshAfterSetup(p)
        }
    }

    fun saveUrl() {
        val problem = EndpointRules.check(urlInput)
        if (problem != null) { urlMsg = urlProblems[problem].orEmpty(); return }
        val p = provider
        urlMsg = ""
        savingUrl = true
        scope.launch {
            try {
                sessions.requireApi().put<JsonElement>("/api/providers/${KeloApi.segment(p)}/endpoint", buildJsonObject { put("base_url", urlInput.trim()) })
                urlInput = ""
                editingUrl = false
                refreshAfterSetup(p)
            } catch (e: ApiError) {
                urlMsg = e.detail
            } finally {
                savingUrl = false
            }
        }
    }

    // 업데이트 in the CLI box (owner only; android my/ModelPage.kt updateCli): the old version keeps working when it fails
    fun updateCli(p: ProviderDetail) {
        if (updating != null) return
        updating = p.name; updateOutcome = null
        scope.launch {
            try {
                val st = CliUpdateRules.run(sessions.requireApi(), p.name)
                updateOutcome = p.name to CliUpdateRules.outcome(st, p.displayName)
            } finally {
                updating = null
                loadProviders()
            }
        }
    }

    fun install(p: ProviderDetail) {
        installing = true; installMsg = ""
        scope.launch {
            try {
                sessions.requireApi().post<JsonElement>("/api/providers/${KeloApi.segment(p.name)}/install")
                installMsg = installed
            } catch (e: ApiError) {
                installMsg = e.detail
            } finally {
                installing = false
                loadProviders()
            }
        }
    }

    fun save() {
        if (saving) return
        err = ""
        saving = true
        scope.launch {
            try {
                onProfile(sessions.requireApi().put<Profile>("/api/profile", buildJsonObject {
                    put("provider", provider); put("model", model.trim())
                    put("fallback_provider", fb.provider); put("fallback_model", fb.model.trim())
                    if (showBackground) { put("background_provider", bg.provider); put("background_model", if (bg.provider.isEmpty()) "" else bg.model.trim()) }
                    if (effortTouched) put("effort", effort)
                }))
                onDone()
            } catch (e: ApiError) {
                // an unofficial provider without the user's agreement: ask, then save again
                val needed = ProviderConsentRules.requiredBy(e)?.let { n -> providers.orEmpty().firstOrNull { it.name == n } }
                if (needed != null) { saving = false; gate.request(needed.asInfo().copy(consent = null)) { save() } } else err = e.detail
            } finally {
                saving = false
            }
        }
    }

    fun pickProvider(v: String) {
        // the main model and the fallback never come from the same provider
        val (next, cleared) = ModelRules.afterMainChange(ModelChoice(v, model), fb)
        fb = next
        fbNote = cleared
        if (v != provider) {
            urlInput = ""; editingUrl = false; urlMsg = ""; keyInput = ""; keyMsg = ""; installMsg = ""
            // another provider offers other models: the old name must not stay behind
            model = ""; custom = false; models = ModelCatalog.keptList(sessions, v)
        }
        provider = v
        val p = providers.orEmpty().find { it.name == v }
        val cli = p?.cli
        if (cli != null && !cli.installed && cli.installable) askInstall = p
    }

    val all = providers.orEmpty()
    val infos = all.map { it.asInfo() }
    val current = all.find { it.name == provider }
    val serverDefault = profile.modelReady != false && profile.provider.isEmpty()
    val unagreed = ProviderConsentRules.pending(provider, fb.provider, infos).filter { gate.needs(it) }

    body {
        if (providers == null && providersSlow) MutedText(stringResource(R.string.loading), small = true)
        // the CLI a newer-CLI notice is about, when another service is selected below (the selected one shows its own box there)
        all.find { it.name == CliUpdateRules.focus(cliProvider, provider, all.filter { p -> p.cli != null }.map { p -> p.name }) }?.let { f ->
            FieldLabel(f.displayName)
            CliBox(f.cli!!, installing, installMsg, signInHere = false, onInstall = { askInstall = f },
                updating = updating == f.name, updateOutcome = updateOutcome?.takeIf { it.first == f.name }?.second,
                onUpdate = { updateCli(f) })
            HorizontalDivider(color = c.line)
        }
        if (unagreed.isNotEmpty() && gate.asking == null) {
            MutedText(stringResource(R.string.model_unofficial_warning, unagreed.joinToString(", ") { it.displayName }), small = true)
        }
        FieldLabel(stringResource(R.string.model_picker_provider))
        val options = buildList {
            // the server's own default only where the server has one (a brand-new server has none)
            if (serverDefault) add(SelectOption("", stringResource(R.string.model_sheet_server_default)))
            all.sortedWith(compareBy<ProviderDetail>({ if (it.unofficial) 1 else 0 }, { if (it.configured == true) 0 else 1 }, { it.displayName.lowercase() }))
                .forEach { p ->
                    add(SelectOption(p.name, when (p.configured) {
                        true -> stringResource(R.string.model_picker_ready, p.displayName)
                        false -> stringResource(R.string.model_picker_needs_setup, p.displayName)
                        null -> p.displayName
                    }, group = if (p.unofficial) 1 else 0))
                }
        }
        SelectField(
            options, provider, stringResource(R.string.model_picker_choose_provider),
            onPick = { picked -> gate.request(all.firstOrNull { it.name == picked }?.asInfo()) { pickProvider(picked) } },
            groupLabel = stringResource(R.string.model_picker_unofficial),
        )
        ChatsGoLine(provider, current?.company)

        current?.cli?.let { cli ->
            CliBox(cli, installing, installMsg, signInHere = current.signin, onInstall = { askInstall = current },
                updating = updating == current.name, updateOutcome = updateOutcome?.takeIf { it.first == current.name }?.second,
                onUpdate = { updateCli(current) })
        }
        if (current != null && SignInRules.offered(current.signin, current.signedIn)) {
            SetupBox {
                SignInBox(sessions, current.asInfo(), gate = { p, then -> gate.request(p, then = then) }) {
                    signOut = null
                    refreshAfterSetup(current.name)   // the box then shows the account and 로그아웃
                }
            }
        }
        if (current != null && SignOutRules.offered(current.signedIn)) {
            SetupBox {
                SignOutBox(sessions, current.name, current.displayName, current.account, profile.provider) { outcome, result, detail ->
                    signOut = current.name to outcome
                    signOutDetail = detail
                    if (SignOutRules.done(outcome)) {
                        if (result?.fallbackCleared == true && fb.provider == current.name) fb = ModelChoice.NONE
                        scope.launch {
                            loadProviders()   // the box goes away and the row shows the sign-in is needed
                            runCatching { sessions.requireApi().get<Profile>("/api/profile") }.onSuccess(onProfile)
                        }
                    }
                }
            }
        }
        signOut?.takeIf { it.first == current?.name }?.let { SignOutMessage(it.second, signOutDetail) }
        if (current?.takesUrl == true) {
            SetupBox {
                // an address already saved shows as one line with 변경; otherwise (or while changing it) the field
                val saved = current.baseUrl.orEmpty()
                if (saved.isNotEmpty() && !editingUrl) {
                    MutedText(stringResource(R.string.model_url_set, saved), small = true)
                    LinkButton(stringResource(R.string.model_url_change), onClick = { urlInput = saved; urlMsg = ""; editingUrl = true })
                } else {
                    FieldLabel(stringResource(R.string.model_url_label))
                    KeloTextField(urlInput, { urlInput = it; urlMsg = "" }, stringResource(R.string.model_url_placeholder), maxLength = 300)
                    MutedText(stringResource(R.string.model_url_help), small = true)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        PrimaryButton(stringResource(R.string.save), onClick = ::saveUrl, enabled = !savingUrl && urlInput.isNotBlank())
                        if (saved.isNotEmpty()) LinkButton(stringResource(R.string.cancel), onClick = { editingUrl = false; urlInput = ""; urlMsg = "" })
                    }
                }
                if (urlMsg.isNotEmpty()) ErrorText(urlMsg, small = true)
            }
        }
        if (current != null && (current.takesKey || current.takesUrl)) {
            SetupBox {
                if (current.takesUrl) FieldLabel(stringResource(R.string.model_key_optional))
                // a key kept in the app: only that it is set, and 삭제; a key from the server's .env: only that note; none: the field
                when (KeyRules.state(current.keySource)) {
                    KeyRules.State.IN_APP -> {
                        val hint = current.keyHint.orEmpty()
                        MutedText(if (hint.isEmpty()) stringResource(R.string.model_key_set) else stringResource(R.string.model_key_in_app, hint), small = true)
                        LinkButton(stringResource(R.string.model_key_delete), onClick = { confirmRemoveKey = true }, danger = true)
                    }
                    KeyRules.State.ENV -> MutedText(stringResource(R.string.model_key_env), small = true)
                    KeyRules.State.NONE -> {
                        MutedText(stringResource(R.string.model_key_none), small = true)
                        KeloTextField(keyInput, { keyInput = it; keyMsg = "" }, stringResource(R.string.model_key_paste), password = true, maxLength = 512)
                        PrimaryButton(stringResource(R.string.save), onClick = ::saveKey, enabled = KeyRules.ready(keyInput))
                    }
                }
                if (keyMsg.isNotEmpty()) ErrorText(keyMsg, small = true)
            }
        }
        if (current != null && current.oauth && current.loggedIn != true && !current.signin) {   // an older server: no sign-in here
            Text(
                emphasized(stringResource(R.string.model_oauth_login_needed), "python -m assistant.providers.oauth login ${current.name}"),
                color = c.danger, style = MaterialTheme.typography.bodySmall,
            )
        }

        if (provider.isNotEmpty()) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.weight(1f)) { FieldLabel(stringResource(R.string.model_picker_model)) }
                LinkButton(stringResource(if (slowModels) R.string.loading else R.string.refresh), onClick = { if (!loadingModels) loadModels(provider, refresh = true) })
            }
            // nothing kept yet and still loading: the chosen model alone, by name (never the raw id in a text field)
            val l = models ?: if (loadingModels) ModelCatalog.placeholder(provider, model) else null
            val showSelect = l != null && l.models.isNotEmpty()
            if (showSelect) {
                val names = ModelNames.displayAll(l!!.models, l.labels)
                val mOptions = l.models.map { SelectOption(it, names.getValue(it)) } + SelectOption(CUSTOM, stringResource(R.string.model_picker_custom))
                SelectField(mOptions, if (custom) CUSTOM else model, stringResource(R.string.model_picker_choose_model), onPick = { v ->
                    if (v == CUSTOM) custom = true else { custom = false; model = v }
                })
            }
            if (custom || (l != null && !showSelect)) {
                KeloTextField(model, { model = it }, stringResource(R.string.model_picker_custom_placeholder), maxLength = 200)
            }
            l?.error?.let { ErrorText(it, small = true) }
            if (l?.stale == true) MutedText(stringResource(R.string.model_list_stale), small = true)
            // the thinking level, as in the chat's model sheet (My → Model only); a server that turned it away: its sentence, quietly
            if (showBackground) {
                val effortChoice = ModelChooserRules.effort(current?.asInfo(), l?.info?.get(model))
                if (effortChoice is EffortChoice.Levels) {
                    FieldLabel(stringResource(R.string.chat_effort))
                    SelectField(
                        effortChoice.levels.map { v -> SelectOption(v, effortLabel(v)) },
                        ModelChooserRules.shownLevel(effortChoice, effort), stringResource(R.string.chat_effort_default),
                        onPick = { v -> effort = v; effortTouched = true },
                    )
                    MutedText(stringResource(R.string.chooser_effort_hint), small = true)
                } else {
                    ModelChooserRules.effortNote(effortChoice)?.let { MutedText(it, small = true) }
                }
            }
        } else if (serverDefault) {
            Text(emphasized(stringResource(R.string.model_server_default_note), ".env"), color = c.muted, style = MaterialTheme.typography.bodySmall)
        }

        HorizontalDivider(color = c.line)
        Text(stringResource(R.string.model_sheet_fallback), style = MaterialTheme.typography.titleSmall, color = c.ink)
        MutedText(stringResource(R.string.model_fallback_help), small = true)
        ModelPicker(
            sessions, infos, fb, noneLabel = stringResource(R.string.model_sheet_no_fallback), exclude = provider, gate = gate,
            onChange = { v -> fbNote = false; fb = v },
        )
        // the fallback's own account and 로그아웃, as the phone's model sheet shows under each picker
        all.find { it.name == fb.provider }?.takeIf { SignOutRules.offered(it.signedIn) }?.let { p ->
            SignOutBox(sessions, p.name, p.displayName, p.account, profile.provider, quietWhenMain = true) { outcome, result, detail ->
                signOut = p.name to outcome
                signOutDetail = detail
                if (SignOutRules.done(outcome)) {
                    if (result?.fallbackCleared == true && fb.provider == p.name) fb = ModelChoice.NONE
                    scope.launch {
                        loadProviders()
                        runCatching { sessions.requireApi().get<Profile>("/api/profile") }.onSuccess(onProfile)
                    }
                }
            }
        }
        signOut?.takeIf { it.first != current?.name }?.let { SignOutMessage(it.second, signOutDetail) }
        if (fbNote) MutedText(stringResource(R.string.model_sheet_fallback_cleared), small = true)
        if (showBackground) {
            // background work (look-back, memory, journal, ...): any provider, the main one too; empty = the main model
            HorizontalDivider(color = c.line)
            Text(stringResource(R.string.model_background_title), style = MaterialTheme.typography.titleSmall, color = c.ink)
            MutedText(stringResource(R.string.model_background_help), small = true)
            ModelPicker(sessions, infos, bg, noneLabel = stringResource(R.string.model_background_same), gate = gate, onChange = { v -> bg = v })
            chat.kelo.app.my.UsageLogic.fallbackShown(bgState, bg.provider, bg.model)?.let { f ->
                MutedText(stringResource(R.string.model_background_fell_back, backgroundFailedAt(f.at, chat.kelo.desktop.pages.my.appLocale())), small = true)
            }
        }
    }
    footer {
        if (err.isNotEmpty()) ErrorText(err, small = true)
        PrimaryButton(
            stringResource(R.string.save), onClick = ::save, modifier = Modifier.fillMaxWidth(),
            enabled = !saving && SetupRules.canSave(provider, model, serverDefault) && ModelRules.complete(fb) && ModelRules.complete(bg),
        )
    }

    ConsentDialog(gate, infos)
    askInstall?.let { p ->
        ConfirmDialog(
            text = stringResource(R.string.model_cli_install_confirm, p.displayName), yes = stringResource(R.string.model_cli_install), danger = false,
            onYes = { install(p) }, onDismiss = { askInstall = null },
        )
    }
    if (confirmRemoveKey) ConfirmDialog(
        text = stringResource(R.string.model_key_delete_confirm), yes = stringResource(R.string.delete),
        onYes = ::removeKey, onDismiss = { confirmRemoveKey = false },
    )
}

/** [template] with [arg] in a monospace span (a command or a file name inside a sentence). */
@Composable
private fun emphasized(template: String, arg: String): androidx.compose.ui.text.AnnotatedString {
    val i = template.indexOf("%1\$s").takeIf { it >= 0 } ?: template.indexOf("%s")
    val token = if (template.contains("%1\$s")) "%1\$s" else "%s"
    if (i < 0) return androidx.compose.ui.text.AnnotatedString(template)
    return androidx.compose.ui.text.buildAnnotatedString {
        append(template.substring(0, i))
        pushStyle(androidx.compose.ui.text.SpanStyle(fontFamily = FontFamily.Monospace))
        append(arg)
        pop()
        append(template.substring(i + token.length))
    }
}

/** The CLI's state on the server: installed, signed in, its version, and an install button when it can be installed. */
@Composable
private fun CliBox(cli: CliStatus, installing: Boolean, installMsg: String, signInHere: Boolean, onInstall: () -> Unit,
                   updating: Boolean = false, updateOutcome: CliUpdateRules.Outcome? = null, onUpdate: () -> Unit = {}) {
    SetupBox {
        MutedText(
            when {
                installing -> stringResource(R.string.model_cli_installing)
                !cli.installed -> stringResource(if (cli.remote) R.string.model_cli_unreachable else R.string.model_cli_not_installed)
                cli.loggedIn == false -> stringResource(R.string.model_cli_login_needed)
                cli.loggedIn == true -> stringResource(if (cli.remote) R.string.model_cli_ok_remote else R.string.model_cli_ok_local)
                else -> stringResource(R.string.model_cli_ok_unknown)
            },
            small = true,
        )
        if (!cli.installed && cli.installable && !installing) SecondaryButton(stringResource(R.string.model_cli_install), onClick = onInstall)
        // the terminal hint only when the app cannot sign it in itself (the sign-in box below does it)
        val signInBelow = signInHere && cli.installed && cli.loggedIn != true
        if ((!cli.installed || cli.loggedIn != true) && !installing && cli.hint.isNotEmpty() && !signInBelow) MutedText(cli.hint, small = true)
        if (cli.installed && (!cli.version.isNullOrEmpty() || !cli.updateHint.isNullOrEmpty())) {
            val parts = listOfNotNull(
                cli.version?.takeIf { it.isNotEmpty() }?.let { stringResource(R.string.model_cli_version, it) },
                cli.latestVersion?.takeIf { cli.updateAvailable && it.isNotEmpty() }?.let { stringResource(R.string.model_cli_update_available, it) },
                cli.updateHint?.takeIf { it.isNotEmpty() && !cli.updatable },   // the button below does it
            )
            MutedText(parts.joinToString(stringResource(R.string.usage_group_joiner)), small = true)
        }
        if (updating) MutedText(stringResource(R.string.model_cli_updating), small = true)
        else if (CliUpdateRules.showButton(cli, busy = installing)) SecondaryButton(stringResource(R.string.model_cli_update_button), onClick = onUpdate)
        when (updateOutcome) {
            is CliUpdateRules.Outcome.Updated -> MutedText(stringResource(R.string.model_cli_update_done, updateOutcome.product, updateOutcome.version), small = true)
            is CliUpdateRules.Outcome.Unchanged -> MutedText(stringResource(R.string.model_cli_update_unchanged, updateOutcome.product), small = true)
            is CliUpdateRules.Outcome.Failed -> ErrorText(stringResource(R.string.model_cli_update_failed, updateOutcome.error), small = true)
            else -> {}
        }
        if (installMsg.isNotEmpty()) MutedText(installMsg, small = true)
    }
}

/**
 * Signing a provider in from this app (Claude, Antigravity, ChatGPT, Qwen, MiniMax): the consent notice first when not given yet,
 * then the provider's page in the browser and either a code to type there (the app notices by itself) or a field to paste the
 * code the page shows. [onSignedIn] runs once it worked.
 */
@Composable
fun SignInBox(sessions: Sessions, provider: ProviderInfo, gate: (ProviderInfo, () -> Unit) -> Unit, onSignedIn: () -> Unit) {
    val scope = rememberCoroutineScope()
    val done by rememberUpdatedState(onSignedIn)
    val path = "/api/providers/${KeloApi.segment(provider.name)}/login"
    var s by remember(provider.name) { mutableStateOf<SignInSession?>(null) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    var paste by remember { mutableStateOf("") }
    var copied by remember { mutableStateOf(false) }
    var deadline by remember { mutableLongStateOf(0L) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val browserFailed = stringResource(R.string.connector_browser_failed)
    val failedText = stringResource(R.string.signin_failed)
    val alreadyText = stringResource(R.string.signin_already)

    fun take(next: SignInSession) {
        s = next
        if (next.state == "pending") deadline = System.currentTimeMillis() + next.expiresIn * 1000
        if (SignInRules.finished(next)) done()
    }

    fun fail(e: ApiError) {
        err = if (SignInRules.problem(e) == SignInRules.Problem.ALREADY) alreadyText else SignInRules.readable(e).ifEmpty { failedText }
    }

    fun start() {
        if (busy) return
        busy = true; err = ""; paste = ""; copied = false
        scope.launch {
            try {
                take(sessions.requireApi().post<SignInSession>("$path/start", buildJsonObject {}))
            } catch (e: ApiError) {
                if (SignInRules.problem(e) == SignInRules.Problem.CONSENT) {
                    busy = false
                    gate(provider.copy(consent = null)) { start() }
                    return@launch
                }
                if (SignInRules.problem(e) == SignInRules.Problem.ALREADY) done()
                fail(e)
            } finally {
                busy = false
            }
        }
    }

    fun finish() {
        val cur = s ?: return
        busy = true; err = ""
        scope.launch {
            try {
                take(sessions.requireApi().post<SignInSession>("$path/finish", buildJsonObject { put("session", cur.session); put("code", paste.trim()) }))
                paste = ""
            } catch (e: ApiError) {
                if (e.status == 404 || e.status == 410) s = cur.copy(state = "expired")
                fail(e)
            } finally {
                busy = false
            }
        }
    }

    fun cancel() {
        val cur = s ?: return
        s = null; err = ""; paste = ""
        scope.launch { runCatching { sessions.requireApi().post<JsonElement>("$path/cancel", buildJsonObject { put("session", cur.session) }) } }
    }

    val waiting = s?.state == "pending"
    LaunchedEffect(s?.session, waiting) {
        if (!waiting) return@LaunchedEffect
        var ticks = 0
        while (true) {
            delay(1000)
            now = System.currentTimeMillis()
            val cur = s ?: break
            if (cur.state != "pending") break
            if (++ticks % 3 == 0 && SignInRules.isDeviceCode(cur)) {
                try {
                    take(sessions.requireApi().get<SignInSession>("$path/status?session=${KeloApi.segment(cur.session)}"))
                } catch (e: ApiError) {
                    if (e.status == 404) s = cur.copy(state = "expired")
                }
            }
        }
    }

    val c = Kelo.colors
    val name = provider.displayName.ifBlank { stringResource(R.string.signin_this_service) }
    val cur = s
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        when {
            cur == null -> {
                MutedText(stringResource(R.string.signin_intro, name), small = true)
                PrimaryButton(stringResource(if (busy) R.string.signin_starting else R.string.signin_button), onClick = { gate(provider) { start() } }, enabled = !busy)
            }
            SignInRules.finished(cur) -> MutedText(
                if (cur.account.isNullOrBlank()) stringResource(R.string.signin_done) else stringResource(R.string.signin_done_account, cur.account),
                small = true,
            )
            SignInRules.ended(cur) -> {
                ErrorText(cur.message?.takeIf { it.isNotBlank() } ?: stringResource(R.string.signin_ended), small = true)
                PrimaryButton(stringResource(R.string.signin_again), onClick = ::start, enabled = !busy)
            }
            else -> {
                val secs = SignInRules.secondsLeft(deadline, now)
                Text(stringResource(R.string.signin_step_open, name), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                PrimaryButton(stringResource(R.string.signin_open_browser), onClick = { if (!Browser.open(cur.url)) err = browserFailed })
                if (SignInRules.isDeviceCode(cur)) {
                    val code = cur.userCode.orEmpty()
                    if (code.isNotEmpty()) {
                        Text(stringResource(R.string.signin_step_code), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                        SelectionContainer {
                            Text(
                                code, modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(c.card).padding(horizontal = 8.dp, vertical = 14.dp),
                                color = c.ink, textAlign = TextAlign.Center, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 26.sp, letterSpacing = 3.sp,
                            )
                        }
                        SecondaryButton(stringResource(if (copied) R.string.device_copied else R.string.device_copy), onClick = { Browser.copy(code); copied = true })
                    }
                    MutedText(stringResource(R.string.signin_device_wait, SignInRules.mmss(secs)), small = true)
                    LinkButton(stringResource(R.string.signin_check_now), onClick = ::finish)
                } else {
                    Text(stringResource(R.string.signin_step_paste), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                    KeloTextField(paste, { paste = it; err = "" }, stringResource(R.string.signin_paste_placeholder), maxLength = 4096)
                    PrimaryButton(
                        stringResource(if (busy) R.string.signin_checking else R.string.signin_finish), onClick = ::finish,
                        enabled = !busy && SignInRules.pasteReady(paste),
                    )
                    MutedText(stringResource(R.string.signin_paste_help, SignInRules.mmss(secs)), small = true)
                }
                LinkButton(stringResource(R.string.cancel), onClick = ::cancel)
            }
        }
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
}

/** When the background model last failed (epoch seconds), short, in this computer's zone and the app's language. */
private fun backgroundFailedAt(epochSeconds: Double, locale: java.util.Locale): String =
    java.time.format.DateTimeFormatter.ofLocalizedDateTime(java.time.format.FormatStyle.SHORT).withLocale(locale)
        .format(java.time.Instant.ofEpochMilli((epochSeconds * 1000).toLong()).atZone(java.time.ZoneId.systemDefault()))
