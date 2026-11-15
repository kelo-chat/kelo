package chat.kelo.app.my

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.chat.ModelCatalog
import chat.kelo.app.chat.ModelChoice
import chat.kelo.app.chat.ModelList
import chat.kelo.app.chat.ModelPicker
import chat.kelo.app.chat.ModelRules
import chat.kelo.app.chat.SelectField
import chat.kelo.app.chat.SelectOption
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Profile
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.emphasized
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private const val CUSTOM = "__custom__"

/**
 * Which AI service and model answer, the fallback model, and the API key for the service. [cliProvider]: a newer CLI notice opened
 * the page for that provider's CLI; when it is not the selected service its box shows on its own card at the top (with 업데이트),
 * so the main and fallback choice stay as they are.
 */
@Composable
fun ModelPage(profile: Profile, onProfile: (Profile) -> Unit, onBack: () -> Unit, cliProvider: String? = null) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var provider by remember { mutableStateOf(profile.provider) }
    var model by remember { mutableStateOf(profile.model) }
    var effort by remember { mutableStateOf(profile.effort) }
    var effortTouched by remember { mutableStateOf(false) }
    var fb by remember { mutableStateOf(ModelRules.initialFallback(profile)) }
    var fbNote by remember { mutableStateOf(false) }
    var bg by remember { mutableStateOf(ModelChoice(profile.backgroundProvider, profile.backgroundModel)) }
    var bgState by remember { mutableStateOf<BackgroundModelState?>(null) }
    // the last provider list and model list (kept on the phone) show at once and are refreshed in place (chat.ModelCatalog)
    var providers by remember { mutableStateOf(ModelCatalog.keptProviders(sessions)) }
    val gate = chat.kelo.app.chat.rememberConsentGate()
    var saved by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    var models by remember { mutableStateOf(ModelCatalog.keptList(sessions, profile.provider)) }
    var loadingModels by remember { mutableStateOf(false) }
    var slowModels by remember { mutableStateOf(false) }   // a refresh over ModelCatalog.SLOW_MS: the 새로고침 link says it is loading
    var custom by remember { mutableStateOf(models.let { it != null && profile.model.isNotEmpty() && profile.model !in it.models }) }
    var keyInput by remember { mutableStateOf("") }
    var keyMsg by remember { mutableStateOf("") }
    var installing by remember { mutableStateOf(false) }
    var installMsg by remember { mutableStateOf("") }
    var updating by remember { mutableStateOf<String?>(null) }                              // the CLI provider being updated
    var updateOutcome by remember { mutableStateOf<Pair<String, CliUpdateRules.Outcome>?>(null) }   // provider name, how it went
    val asked = remember { mutableSetOf<String>() }
    var askInstall by remember { mutableStateOf<ProviderDetail?>(null) }
    var confirmRemoveKey by remember { mutableStateOf(false) }
    var signOut by remember { mutableStateOf<Pair<String, chat.kelo.app.chat.SignOutRules.Outcome>?>(null) }   // provider name, answer
    var signOutDetail by remember { mutableStateOf("") }
    // the server address of a provider that takes one (custom, lmstudio)
    var urlInput by remember { mutableStateOf("") }
    var editingUrl by remember { mutableStateOf(false) }
    var urlMsg by remember { mutableStateOf("") }
    var savingUrl by remember { mutableStateOf(false) }
    val urlProblems = mapOf(
        EndpointRules.Problem.EMPTY to stringResource(R.string.model_url_empty),
        EndpointRules.Problem.SPACES to stringResource(R.string.model_url_spaces),
        EndpointRules.Problem.SCHEME to stringResource(R.string.model_url_scheme),
        EndpointRules.Problem.HOST to stringResource(R.string.model_url_host),
    )
    val listFailed = stringResource(R.string.model_picker_list_failed)
    val installed = stringResource(R.string.model_cli_installed_msg)

    suspend fun loadProviders() {
        runCatching { ModelCatalog.fetchProviders(sessions) }.onSuccess { providers = it }
    }

    fun loadModels(p: String, refresh: Boolean = false) {
        if (p.isEmpty()) { models = null; return }
        val kept = ModelCatalog.keptList(sessions, p)
        if (models?.provider != p) models = kept      // another provider: its own last list at once, never the previous one's
        loadingModels = true
        scope.launch {
            val hint = launch { kotlinx.coroutines.delay(ModelCatalog.SLOW_MS); if (p == provider) slowModels = true }
            try {
                val m = ModelCatalog.fetch(sessions, p, refresh)
                if (p != provider) return@launch          // the user picked another provider meanwhile
                val shown = ModelCatalog.shown(m, kept) ?: m
                models = shown
                custom = shown.models.isEmpty() || (model.isNotEmpty() && model !in shown.models)
            } catch (_: ApiError) {
                if (p == provider) models = ModelCatalog.shown(ModelList(p, emptyList(), error = listFailed), kept)
            } finally {
                hint.cancel()
                if (p == provider) { loadingModels = false; slowModels = false }
            }
        }
    }

    LaunchedEffect(Unit) { loadProviders() }
    LaunchedEffect(provider) { loadModels(provider) }
    LaunchedEffect(Unit) {   // the background model's last failure, if any (an older server has no such page: nothing shows)
        runCatching { sessions.requireApi().get<UsageSummary>("/api/usage/summary?days=1") }.onSuccess { bgState = it.background }
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

    // 업데이트 in the CLI box (owner only): the sidecar installs the newer CLI into its volume; the old one keeps working on failure
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

    // Selecting a CLI provider that can be installed but is not: offer to install it (once per selection).
    LaunchedEffect(provider, providers) {
        val p = providers.find { it.name == provider }
        val cli = p?.cli
        if (cli != null && !cli.installed && cli.installable && p.name !in asked) {
            asked += p.name
            askInstall = p
        }
    }

    fun saveKey() {
        keyMsg = ""
        scope.launch {
            try {
                sessions.requireApi().put<JsonElement>("/api/providers/${KeloApi.segment(provider)}/key", buildJsonObject { put("api_key", keyInput.trim()) })
                keyInput = ""
                keyMsg = ""
                loadProviders()   // the box then shows the key as set (no field)
                loadModels(provider, refresh = true)
            } catch (e: ApiError) {
                keyMsg = e.detail
            }
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
                loadProviders()                     // the box then shows the address as set (no field)
                loadModels(p, refresh = true)       // the models come from that server
            } catch (e: ApiError) {
                urlMsg = e.detail
            } finally {
                savingUrl = false
            }
        }
    }

    fun removeKey() {
        scope.launch {
            runCatching { sessions.requireApi().delete<JsonElement>("/api/providers/${KeloApi.segment(provider)}/key") }
            keyMsg = ""
            loadProviders()
            loadModels(provider, refresh = true)
        }
    }

    val chooseModel = stringResource(R.string.model_picker_choose_model)
    fun save() {
        if (saving) return
        err = ""
        if (!ModelRules.complete(bg)) { err = chooseModel; return }   // a background provider needs its model
        saving = true
        scope.launch {
            try {
                onProfile(sessions.requireApi().put<Profile>("/api/profile", buildJsonObject {
                    put("provider", provider); put("model", model)
                    put("fallback_provider", fb.provider); put("fallback_model", fb.model)
                    put("background_provider", bg.provider); put("background_model", if (bg.provider.isEmpty()) "" else bg.model)
                    if (effortTouched) put("effort", effort)
                }))
                saved = true
                onBack()   // saved: back to 마이 (an error keeps the page open with the reason)
            } catch (e: ApiError) {
                // an unofficial provider without the user's agreement: ask, then save again
                val needed = chat.kelo.app.chat.ProviderConsentRules.requiredBy(e)?.let { n -> providers.firstOrNull { it.name == n } }
                if (needed != null) { saving = false; gate.request(needed.asInfo().copy(consent = null)) { save() } } else err = e.detail
            } finally {
                saving = false
            }
        }
    }

    // a saved choice that is unofficial and not agreed to (set up before the question existed): asked once, kept either way
    LaunchedEffect(providers) {
        val infos = providers.map { it.asInfo() }
        chat.kelo.app.chat.ProviderConsentRules.pending(profile.provider, profile.fallbackProvider, infos)
            .firstOrNull { !gate.alreadyAsked(it) }
            ?.let { p -> gate.request(p) {} }
    }

    val current = providers.find { it.name == provider }
    val c = Kelo.colors
    val unagreed = chat.kelo.app.chat.ProviderConsentRules.pending(provider, fb.provider, providers.map { it.asInfo() }).filter { gate.needs(it) }
    SubPage(stringResource(R.string.model_title), onBack) {
        if (unagreed.isNotEmpty() && gate.asking == null) {
            MutedText(stringResource(R.string.model_unofficial_warning, unagreed.joinToString(", ") { it.displayName }), small = true)
        }
        // the CLI a newer-CLI notice is about, when another service is selected below (the selected one shows its own box there)
        providers.find { it.name == CliUpdateRules.focus(cliProvider, provider, providers.filter { p -> p.cli != null }.map { p -> p.name }) }?.let { f ->
            MyCard {
                FieldLabel(f.displayName)
                CliBox(f.cli!!, installing, installMsg, onInstall = { install(f) },
                    updating = updating == f.name, updateOutcome = updateOutcome?.takeIf { it.first == f.name }?.second,
                    onUpdate = { updateCli(f) })
            }
        }
        MyCard {
            val options = buildList {
                add(SelectOption("", stringResource(if (profile.modelReady == false) R.string.model_picker_choose_provider else R.string.model_sheet_server_default), keep = true))
                providers.forEach { p ->
                    // ready ones first, then the rest by name
                    add(SelectOption(p.name, when (p.configured) {
                        true -> stringResource(R.string.model_picker_ready, p.displayName)
                        false -> stringResource(R.string.model_picker_needs_setup, p.displayName)
                        null -> p.displayName
                    }, rank = if (p.configured == true) 0 else 1, sortKey = p.displayName, group = if (p.unofficial) 1 else 0))
                }
            }
            FieldLabel(stringResource(R.string.model_picker_provider))
            SelectField(stringResource(R.string.model_picker_provider), options, provider, sorted = true,
                groupLabel = stringResource(R.string.model_picker_unofficial)) { picked -> gate.request(providers.firstOrNull { it.name == picked }?.asInfo()) {
                val v = picked
                saved = false
                // the main model and the fallback never come from the same provider
                val (next, cleared) = ModelRules.afterMainChange(ModelChoice(v, model), fb)
                fb = next
                fbNote = cleared
                if (v != provider) {
                    urlInput = ""; editingUrl = false; urlMsg = ""
                    // another provider offers other models: the old name must not stay behind
                    model = ""
                    custom = false
                    models = ModelCatalog.keptList(sessions, v)
                }
                provider = v
            } }
            chat.kelo.app.chat.ChatsGoLine(provider, current?.company)
            current?.cli?.let { cli ->
                CliBox(cli, installing, installMsg, onInstall = { install(current) }, signInHere = current.signin,
                    updating = updating == current.name, updateOutcome = updateOutcome?.takeIf { it.first == current.name }?.second,
                    onUpdate = { updateCli(current) })
            }
            if (current != null && chat.kelo.app.chat.SignInRules.offered(current.signin, current.signedIn)) {
                KeyBox(first = current.cli == null) {
                    chat.kelo.app.chat.SignInBox(current.asInfo(), gate) {
                        signOut = null
                        scope.launch { loadProviders(); loadModels(provider, refresh = true) }   // the box then shows the account
                    }
                }
            }
            if (current != null && chat.kelo.app.chat.SignOutRules.offered(current.signedIn)) {
                KeyBox(first = current.cli == null) {
                    chat.kelo.app.chat.SignOutBox(current.name, current.displayName, current.account, profile.provider) { outcome, result, detail ->
                        signOut = current.name to outcome
                        signOutDetail = detail
                        if (chat.kelo.app.chat.SignOutRules.done(outcome)) {
                            if (result?.fallbackCleared == true && fb.provider == current.name) fb = ModelChoice.NONE
                            scope.launch {
                                loadProviders()   // the box goes away and the row shows the sign-in is needed
                                runCatching { sessions.requireApi().get<Profile>("/api/profile") }.onSuccess {
                                    onProfile(it)
                                    bg = ModelChoice(it.backgroundProvider, it.backgroundModel)   // a background model on that sign-in is emptied
                                }
                            }
                        }
                    }
                }
            }
            signOut?.takeIf { it.first == current?.name }?.let { chat.kelo.app.chat.SignOutMessage(it.second, signOutDetail) }
            if (current?.takesUrl == true) {
                KeyBox(first = true) {
                    // an address already saved shows as one line with 변경; otherwise (or while changing it) the field
                    val saved = current.baseUrl.orEmpty()
                    if (saved.isNotEmpty() && !editingUrl) {
                        MutedText(stringResource(R.string.model_url_set, saved), small = true)
                        LinkButton(stringResource(R.string.model_url_change), onClick = { urlInput = saved; urlMsg = ""; editingUrl = true })
                    } else {
                        FieldLabel(stringResource(R.string.model_url_label))
                        chat.kelo.app.ui.components.FieldWithAction(
                            field = { m ->
                                KeloTextField(
                                    urlInput, { urlInput = it; urlMsg = "" }, stringResource(R.string.model_url_placeholder),
                                    maxLength = 300, modifier = m,
                                    keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Uri),
                                )
                            },
                            action = { PrimaryButton(stringResource(R.string.save), onClick = ::saveUrl, enabled = !savingUrl && urlInput.isNotBlank()) },
                        )
                        MutedText(stringResource(R.string.model_url_help), small = true)
                        if (saved.isNotEmpty()) LinkButton(stringResource(R.string.cancel), onClick = { editingUrl = false; urlInput = ""; urlMsg = "" })
                    }
                    if (urlMsg.isNotEmpty()) ErrorText(urlMsg, small = true)
                }
            }
            if (current != null && (current.takesKey || current.takesUrl)) {
                KeyBox(first = current.takesUrl != true) {
                    if (current.takesUrl) FieldLabel(stringResource(R.string.model_key_optional))
                    // a key kept in the app: only that it is set, and 삭제 (then the field comes back); a key from the server's .env:
                    // only that note; no key: the field to paste one
                    when (KeyRules.state(current.keySource)) {
                        KeyRules.State.IN_APP -> {
                            val hint = current.keyHint.orEmpty()
                            MutedText(
                                if (hint.isEmpty()) stringResource(R.string.model_key_set) else stringResource(R.string.model_key_in_app, hint),
                                small = true,
                            )
                            LinkButton(stringResource(R.string.model_key_delete), onClick = { confirmRemoveKey = true }, danger = true)
                        }
                        KeyRules.State.ENV -> MutedText(stringResource(R.string.model_key_env), small = true)
                        KeyRules.State.NONE -> {
                            MutedText(stringResource(R.string.model_key_none), small = true)
                            chat.kelo.app.ui.components.FieldWithAction(
                                field = { m ->
                                    KeloTextField(
                                        keyInput, { keyInput = it }, stringResource(R.string.model_key_paste),
                                        password = true, maxLength = 512, modifier = m,
                                        keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Password),
                                    )
                                },
                                action = { PrimaryButton(stringResource(R.string.save), onClick = ::saveKey, enabled = keyInput.trim().length >= 8) },
                            )
                        }
                    }
                    if (keyMsg.isNotEmpty()) MutedText(keyMsg, small = true)
                }
            }
            if (current != null && current.oauth && current.loggedIn != true && !current.signin) {   // an older server: no sign-in here
                Text(
                    emphasized(stringResource(R.string.model_oauth_login_needed), "python -m assistant.providers.oauth login ${current.name}", code = true),
                    color = c.danger, style = MaterialTheme.typography.bodySmall,
                )
            }
            if (provider.isNotEmpty()) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    FieldLabel(stringResource(R.string.model_picker_model))
                    Row(Modifier.weight(1f)) {}
                    LinkButton(
                        stringResource(if (slowModels) R.string.loading else R.string.refresh),
                        onClick = { if (!loadingModels) loadModels(provider, refresh = true) },
                    )
                }
                // nothing kept yet and still loading: the chosen model alone, by name (never the raw id in a text field)
                val l = models ?: if (loadingModels) ModelCatalog.placeholder(provider, model) else null
                val showSelect = l != null && l.models.isNotEmpty()
                if (showSelect) {
                    val mOptions = buildList {
                        if (model.isEmpty()) add(SelectOption("", stringResource(R.string.model_picker_choose_model), enabled = false, keep = true))
                        val names = chat.kelo.app.chat.ModelNames.displayAll(l!!.models, l.labels)
                        l.models.forEach { m ->
                            add(SelectOption(m, names.getValue(m)))
                        }
                        add(SelectOption(CUSTOM, stringResource(R.string.model_picker_custom), keep = true))
                    }
                    SelectField(stringResource(R.string.model_picker_model), mOptions, if (custom) CUSTOM else model) { v ->
                        saved = false
                        if (v == CUSTOM) custom = true else { custom = false; model = v }
                    }
                }
                if (custom || !showSelect) {
                    KeloTextField(model, { saved = false; model = it }, stringResource(R.string.model_picker_custom_placeholder), maxLength = 200)
                }
                l?.error?.let { ErrorText(it, small = true) }
                if (l?.stale == true) MutedText(stringResource(R.string.model_list_stale), small = true)
                // the thinking level, as in the chat's model sheet; a server that turned it away: its sentence, quietly
                val effortChoice = chat.kelo.app.chat.ModelChooserRules.effort(current?.asInfo(), l?.info?.get(model))
                if (effortChoice is chat.kelo.app.chat.EffortChoice.Levels) {
                    FieldLabel(stringResource(R.string.chat_effort))
                    SelectField(
                        stringResource(R.string.chat_effort),
                        effortChoice.levels.map { v -> SelectOption(v, chat.kelo.app.chat.effortLabel(v), keep = true) },
                        chat.kelo.app.chat.ModelChooserRules.shownLevel(effortChoice, effort),
                    ) { v -> saved = false; effort = v; effortTouched = true }
                    MutedText(stringResource(R.string.chooser_effort_hint), small = true)
                } else {
                    chat.kelo.app.chat.ModelChooserRules.effortNote(effortChoice)?.let { MutedText(it, small = true) }
                }
            } else {
                Text(stringResource(R.string.model_server_default_note), color = c.muted, style = MaterialTheme.typography.bodySmall)
            }
            Text(stringResource(R.string.model_sheet_fallback), style = MaterialTheme.typography.titleSmall, color = c.ink)
            MutedText(stringResource(R.string.model_fallback_help), small = true)
            ModelPicker(
                providers.map { it.asInfo() }, fb, noneLabel = stringResource(R.string.model_sheet_no_fallback), exclude = provider, gate = gate,
                onChange = { v -> saved = false; fbNote = false; fb = v },
            )
            if (fbNote) MutedText(stringResource(R.string.model_sheet_fallback_cleared), small = true)
            // background work (look-back, memory, journal, ...): any provider, the main one too; empty = the main model
            Text(stringResource(R.string.model_background_title), style = MaterialTheme.typography.titleSmall, color = c.ink)
            MutedText(stringResource(R.string.model_background_help), small = true)
            ModelPicker(
                providers.map { it.asInfo() }, bg, noneLabel = stringResource(R.string.model_background_same), gate = gate,
                onChange = { v -> saved = false; bg = v },
            )
            UsageLogic.fallbackShown(bgState, bg.provider, bg.model)?.let { f ->
                MutedText(stringResource(R.string.model_background_fell_back, shortDateTime(f.at, appLocale())), small = true)
            }
            PrimaryButton(stringResource(R.string.save), onClick = ::save, enabled = !saving)
            if (err.isNotEmpty()) ErrorText(err, small = true)
        }
    }

    chat.kelo.app.chat.ConsentDialog(gate)
    askInstall?.let { p ->
        ConfirmDialog(
            text = stringResource(R.string.model_cli_install_confirm, p.displayName),
            yes = stringResource(R.string.model_cli_install), danger = false,
            onYes = { install(p) }, onDismiss = { askInstall = null },
        )
    }
    if (confirmRemoveKey) {
        ConfirmDialog(
            text = stringResource(R.string.model_key_delete_confirm), yes = stringResource(R.string.delete),
            onYes = ::removeKey, onDismiss = { confirmRemoveKey = false },
        )
    }
}

/** Checks the address of an OpenAI-compatible server before it is sent (the server checks again). */
object EndpointRules {
    enum class Problem { EMPTY, SPACES, SCHEME, HOST }

    /** Null when [url] looks usable: http or https, a host, no spaces. */
    fun check(url: String): Problem? {
        val t = url.trim()
        if (t.isEmpty()) return Problem.EMPTY
        if (t.any { it.isWhitespace() }) return Problem.SPACES
        val scheme = t.substringBefore("://", "").lowercase()
        if (scheme != "http" && scheme != "https") return Problem.SCHEME
        val host = runCatching { java.net.URI(t).host }.getOrNull()
            ?: t.substringAfter("://").substringBefore('/').substringBefore('?').substringBefore('#').substringAfterLast('@').substringBefore(':')
        return if (host.isBlank()) Problem.HOST else null
    }
}

/** Which API key box a provider gets, from where its key comes (ProviderDetail.keySource). */
object KeyRules {
    enum class State { IN_APP, ENV, NONE }

    fun state(keySource: String?): State = when (keySource) {
        "app" -> State.IN_APP
        "env" -> State.ENV
        else -> State.NONE
    }
}

/** The CLI's state on the server: installed, signed in, its version, and an install button when it can be installed. */
@Composable
private fun CliBox(cli: CliStatus, installing: Boolean, installMsg: String, onInstall: () -> Unit, signInHere: Boolean = false,
                   updating: Boolean = false, updateOutcome: CliUpdateRules.Outcome? = null, onUpdate: () -> Unit = {}) {
    KeyBox {
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
        // the terminal hint only when the app cannot sign it in itself (the 로그인 box below does it)
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

/** "2026. 11. 9. 오후 3:12": a server time (epoch seconds) in the phone's zone and the app's language. */
private fun shortDateTime(epochSeconds: Double, locale: java.util.Locale): String =
    java.time.format.DateTimeFormatter.ofLocalizedDateTime(java.time.format.FormatStyle.SHORT).withLocale(locale)
        .format(java.time.Instant.ofEpochMilli((epochSeconds * 1000).toLong()).atZone(java.time.ZoneId.systemDefault()))
