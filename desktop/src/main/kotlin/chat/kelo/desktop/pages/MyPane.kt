package chat.kelo.desktop.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.ModelNames
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.DeviceList
import chat.kelo.app.core.Languages
import chat.kelo.app.core.Profile
import chat.kelo.app.my.About
import chat.kelo.app.my.MyOverview
import chat.kelo.app.my.Resources
import chat.kelo.app.my.ServerUpdateRules
import chat.kelo.app.my.ServerUpdateStatus
import chat.kelo.app.my.Usage
import chat.kelo.app.my.VaultEntry
import chat.kelo.app.my.VaultState
import chat.kelo.app.notify.OpenTarget
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.wallet.WalletInfo
import chat.kelo.app.wallet.WalletNotifications
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.Autostart
import chat.kelo.desktop.core.DesktopCaches
import chat.kelo.desktop.core.MY_PAGE_AGENT
import chat.kelo.desktop.core.MY_PAGE_MODEL
import chat.kelo.desktop.core.MY_PAGE_OPERATOR
import chat.kelo.desktop.core.MY_PAGE_SERVER_UPDATE
import chat.kelo.desktop.core.MY_PAGE_WALLET
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.my.AgentPage
import chat.kelo.desktop.pages.my.DevicesPage
import chat.kelo.desktop.pages.my.DocumentsPage
import chat.kelo.desktop.pages.my.LanguagePage
import chat.kelo.desktop.pages.my.ModelPage
import chat.kelo.desktop.pages.my.NotifyPage
import chat.kelo.desktop.pages.my.OperatorPage
import chat.kelo.desktop.pages.my.PermissionsPage
import chat.kelo.desktop.pages.my.PrivacyPage
import chat.kelo.desktop.pages.my.ProfilePage
import chat.kelo.desktop.pages.my.RecordsPage
import chat.kelo.desktop.pages.my.ResourcesPage
import chat.kelo.desktop.pages.my.ServerUpdateCard
import chat.kelo.desktop.pages.my.ServerUpdatePage
import chat.kelo.desktop.pages.my.UsagePage
import chat.kelo.desktop.pages.my.VaultPage
import chat.kelo.desktop.pages.my.WalletPage
import chat.kelo.desktop.pages.my.profileText
import chat.kelo.desktop.pages.my.usageSummary
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.MenuGroup
import chat.kelo.desktop.ui.MenuRow
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.SUB_PAGE_WIDTH
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import chat.kelo.desktop.ui.SwitchRow
import chat.kelo.desktop.ui.UpdateCard
import chat.kelo.desktop.ui.updateRowValue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement

/** The My page's sub pages (android/kelo .../my/MyScreen.kt MyView, plus this device's own settings). */
object MyView {
    const val HOME = "home"
    const val PROFILE = "profile"
    const val AGENT = "agent"
    const val PERMISSIONS = "permissions"
    const val RECORDS = "records"
    const val NOTIFY = "notify"
    const val MODEL = "model"
    const val USAGE = "usage"
    const val DEVICES = "devices"
    const val VAULT = "vault"
    const val RESOURCES = "resources"
    const val CLEANUP = "cleanup"
    const val DOCUMENTS = "documents"
    const val PRIVACY = "privacy"
    const val LANGUAGE = "language"
    const val WALLET = "wallet"
    const val OPERATOR = "operator"
    /** 서버 업데이트 (owner only) */
    const val SERVER_UPDATE = "server_update"
    /** Desktop only: start at sign-in, the tray, motion and appearance */
    const val DESKTOP = "desktop"

    val ALL = setOf(PROFILE, AGENT, PERMISSIONS, RECORDS, NOTIFY, MODEL, USAGE, DEVICES, VAULT, RESOURCES, DOCUMENTS, PRIVACY, LANGUAGE, WALLET, OPERATOR, SERVER_UPDATE, DESKTOP)
}

/** MyPane's start value that opens the clear-history dialog. */
const val CLEAR_HISTORY = "clear-history"

/** The My page's stack of sub pages (empty is the home page). */
object MyStack {
    fun push(stack: List<String>, v: String): List<String> = if (stack.lastOrNull() == v) stack else stack + v
    fun pop(stack: List<String>): List<String> = stack.dropLast(1)
    fun isSub(stack: List<String>): Boolean = stack.isNotEmpty()
}

/**
 * 마이, as on the phone (android/kelo .../my/MyScreen.kt): one grouped settings list (account and devices, the agent, the model and
 * its usage, connections, security, data, the app, and for the server's owner its operator settings), each row opening a sub page
 * over the list (the back arrow or Escape steps out). It shows only what belongs to the person. The desktop adds "This device"
 * (start at sign-in, the tray, motion, appearance) under the app's group.
 */
@Composable
fun MyPane(app: AppContainer, topInset: Dp, start: String?, onStartUsed: () -> Unit, onSignedOut: () -> Unit) {
    val profile by app.profile.collectAsState()
    val p = profile ?: Profile()
    var stack by remember { mutableStateOf(listOf<String>()) }
    fun push(v: String) { stack = MyStack.push(stack, v) }
    val back: () -> Unit = { stack = MyStack.pop(stack) }
    var o by remember { mutableStateOf(runCatching { app.sessions.cached(MY_CACHE) }.getOrNull() ?: MyOverview()) }
    val homeScroll = rememberScrollState()
    val view = stack.lastOrNull() ?: MyView.HOME

    // a page to open first: "model" (the chat's model chip), any MyView name, or "clear-history" (the clear dialog on the home page)
    var askClear by remember { mutableStateOf(false) }
    LaunchedEffect(start) {
        when {
            start == null -> return@LaunchedEffect
            start == CLEAR_HISTORY -> { stack = emptyList(); askClear = true }
            start in MyView.ALL -> push(start)
        }
        onStartUsed()
    }
    // a notification or the shell asked for one sub page
    val pageRequest by app.myPageRequests.collectAsState()
    // the CLI a notification asked My → Model to show (its provider), until the page is left
    var modelCli by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(view) { if (view != MyView.MODEL) modelCli = null }
    LaunchedEffect(pageRequest) {
        val r = pageRequest ?: return@LaunchedEffect
        app.myPageRequests.value = null
        when (r.substringBefore(':')) {
            MY_PAGE_WALLET -> push(MyView.WALLET)
            MY_PAGE_AGENT -> push(MyView.AGENT)
            MY_PAGE_MODEL -> {
                modelCli = r.substringAfter(':', "").ifEmpty { null }
                if (stack.lastOrNull() != MyView.MODEL) push(MyView.MODEL)
            }
            MY_PAGE_OPERATOR -> push(MyView.OPERATOR)
            MY_PAGE_SERVER_UPDATE -> push(MyView.SERVER_UPDATE)
            // a storage warning: the resources page; its 정리하기: the cleanup screen over it
            chat.kelo.desktop.core.MY_PAGE_RESOURCES -> if (stack.lastOrNull() != MyView.RESOURCES) push(MyView.RESOURCES)
            chat.kelo.desktop.core.MY_PAGE_CLEANUP -> {
                if (stack.lastOrNull() != MyView.RESOURCES) push(MyView.RESOURCES)
                push(MyView.CLEANUP)
            }
        }
    }

    // 서버 업데이트 (owner only; null = show nothing) and, while an update runs, whether the restarting server answers
    var server by remember { mutableStateOf<ServerUpdateStatus?>(null) }
    var serverWaiting by remember { mutableStateOf(false) }
    var serverGaveUp by remember { mutableStateOf(false) }
    val serverRunning = ServerUpdateRules.running(server)
    LaunchedEffect(serverRunning) {
        if (!serverRunning) return@LaunchedEffect
        val api = runCatching { app.sessions.requireApi() }.getOrNull() ?: return@LaunchedEffect
        val begin = server ?: return@LaunchedEffect
        ServerUpdateRules.follow(api, begin) { f -> server = f.status; serverWaiting = f.waiting; serverGaveUp = f.gaveUp }
    }

    fun refreshUsage(refresh: Boolean = false) {
        app.scope.launch {
            runCatching { app.sessions.requireApi().get<Usage>("/api/usage${if (refresh) "?refresh=true" else ""}") }.onSuccess { u -> o = o.copy(usage = u) }
        }
    }
    // fresh numbers each time the home page shows; each part replaces its old value as it arrives
    LaunchedEffect(view) {
        if (view != MyView.HOME) return@LaunchedEffect
        val api = runCatching { app.sessions.requireApi() }.getOrNull() ?: return@LaunchedEffect
        runCatching { api.get<Profile>("/api/profile") }.onSuccess(app::setProfile)
        val parts = coroutineScope {
            listOf(
                async { runCatching { api.get<Usage>("/api/usage") }.onSuccess { u -> o = o.copy(usage = u) } },
                async { runCatching { api.get<DeviceList>("/api/devices") }.onSuccess { d -> o = o.copy(devices = d.devices.size) } },
                async { runCatching { api.get<VaultState>("/api/vault") }.onSuccess { v -> o = o.copy(creds = v.items, locked = v.locked) } },
                async { runCatching { api.get<JsonArray>("/api/uploads") }.onSuccess { d -> o = o.copy(documents = d.size) } },
                async {
                    runCatching { api.get<Resources>("/api/resources") }.onSuccess { r ->
                        o = o.copy(sizes = r.profile, storage = chat.kelo.app.my.StorageRules.key(chat.kelo.app.my.StorageRules.state(r.alert)),
                            storagePercent = r.alert.percent)
                    }
                },
                async { runCatching { api.get<About>("/api/about") }.onSuccess { a -> o = o.copy(version = a.version) } },
                // 서버 업데이트: an owner sign-in on a server whose updater is set up (403, 404 and "unavailable": nothing shows)
                async { runCatching { ServerUpdateRules.load(api) }.onSuccess { s -> if (!ServerUpdateRules.running(server)) server = s } },
                async { runCatching { api.get<WalletNotifications>("/api/wallet/notifications?limit=1") }.onSuccess { n -> o = o.copy(walletUnseen = n.unseen) } },
                async { runCatching { api.get<WalletInfo>("/api/wallet/cards") }.onSuccess { w -> o = o.copy(cards = w.cards.size) } },
                // the operator section: only for an owner sign-in on the operator's own server (GET /api/operator/status); a self-hosted
                // copy says operator=false, any other device gets 403; a server from before that call (404) falls back to the older rule
                async {
                    runCatching { api.get<OperatorStatusBrief>("/api/operator/status").operator }
                        .map { MyOperatorRules.operatorFrom(null, it) }
                        .recoverCatching { e ->
                            val status = (e as? ApiError)?.status?.takeIf { it == 403 || it == 404 } ?: throw e
                            val older = if (status == 404) runCatching { api.get<JsonElement>("/api/operator/firebase"); null }
                                .recoverCatching { f -> if (f is ApiError && f.status == 403) 403 else throw f }.getOrElse { -1 } else -1
                            MyOperatorRules.operatorFrom(status, null) { MyOperatorRules.ownerFrom(older) }
                        }
                        .onSuccess { shown -> shown?.let { o = o.copy(operator = it) } }
                },
            ).awaitAll()
        }
        if (parts.any { it.isSuccess }) runCatching { app.sessions.keep(api, MY_CACHE, o) }
    }

    Box(Modifier.fillMaxSize().background(Kelo.colors.bg)) {
        when (view) {
            MyView.PROFILE -> ProfilePage(app, p, back)
            MyView.AGENT -> AgentPage(app, p, back)
            MyView.PERMISSIONS -> PermissionsPage(app, onBack = back, onOpenWork = { app.openRequests.value = OpenTarget.Activity })
            MyView.RECORDS -> RecordsPage(app, back)
            MyView.NOTIFY -> NotifyPage(app, back)
            MyView.MODEL -> ModelPage(app, onBack = back, cliProvider = modelCli)
            MyView.USAGE -> UsagePage(app, o.usage, p.model, onRefresh = { refreshUsage(true) }, onBack = back)
            MyView.DEVICES -> DevicesPage(app, back, onSignedOut)
            MyView.WALLET -> WalletPage(app, onBack = back, onSeen = { o = o.copy(walletUnseen = 0) })
            MyView.VAULT -> VaultPage(app, onBack = back, onGoModel = { push(MyView.MODEL) })
            MyView.RESOURCES -> ResourcesPage(app, back, onCleanup = { push(MyView.CLEANUP) })
            MyView.CLEANUP -> chat.kelo.desktop.pages.my.CleanupPage(app, back)
            MyView.DOCUMENTS -> DocumentsPage(app, back)
            MyView.PRIVACY -> PrivacyPage(back)
            MyView.OPERATOR -> OperatorPage(app, onBack = back)
            MyView.SERVER_UPDATE -> ServerUpdatePage(app, server, serverWaiting, serverGaveUp, onStatus = { s ->
                server = s
                if (s == null) back()   // this sign-in may no longer see it
            }, onBack = back)
            MyView.LANGUAGE -> LanguagePage(app, back)
            MyView.DESKTOP -> DesktopPage(app, back)
            else -> Home(app, p, o, server, homeScroll, topInset, ::push, onSignedOut, askClear = askClear, onAskedClear = { askClear = false })
        }
    }
}

/** The My page's numbers kept from last time (the phone's Caches.MY: same name and shape), so the page shows them at once. */
private val MY_CACHE = chat.kelo.app.core.CacheSlot("my", MyOverview.serializer())

/** GET /api/operator/status (android .../my/OperatorPage.kt OperatorStatus). */
@kotlinx.serialization.Serializable
data class OperatorStatusBrief(val owner: Boolean = false, val operator: Boolean = false)

/** Whether 마이 shows the operator row: the Android rules themselves (hand copy in chat.kelo.app.my.OperatorModels). */
val MyOperatorRules = chat.kelo.app.my.OperatorRules

@Composable
private fun Home(
    app: AppContainer, profile: Profile, o: MyOverview, server: ServerUpdateStatus?, scroll: ScrollState, topInset: Dp, go: (String) -> Unit, onSignedOut: () -> Unit,
    askClear: Boolean = false, onAskedClear: () -> Unit = {},
) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var confirmClear by remember { mutableStateOf(false) }
    var confirmLogout by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf("") }
    LaunchedEffect(askClear) { if (askClear) { confirmClear = true; onAskedClear() } }
    val connected = o.creds.filter { it.hasSecret && (it.kind == VaultEntry.KIND_API || it.kind == VaultEntry.KIND_EMAIL) }

    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.widthIn(max = SUB_PAGE_WIDTH.dp).fillMaxWidth().verticalScroll(scroll)
                .padding(start = 8.dp, end = 8.dp, top = topInset + 4.dp, bottom = 24.dp),
        ) {
            Text(stringResource(R.string.tab_my), style = MaterialTheme.typography.headlineSmall, color = c.ink, modifier = Modifier.padding(start = 16.dp, top = 8.dp))
            if (note.isNotEmpty()) MutedText(note, small = true, modifier = Modifier.padding(start = 16.dp, top = 6.dp))
            UpdateCard(app)
            // the owner's server: a newer release, an update running, or one that failed or was undone in the last day
            ServerUpdateRules.card(server, System.currentTimeMillis() / 1000.0)?.let { card ->
                ServerUpdateCard(card, onClick = { go(MyView.SERVER_UPDATE) }, modifier = Modifier.padding(horizontal = 8.dp, vertical = 8.dp))
            }

            SectionLabel(stringResource(R.string.my_section_account))
            MenuGroup {
                row { MenuRow(KeloIcons.User, stringResource(R.string.profile_title), { go(MyView.PROFILE) }, value = profile.userName.ifEmpty { stringResource(R.string.my_name_missing) }) }
                row { MenuRow(KeloIcons.Phone, stringResource(R.string.devices_title), { go(MyView.DEVICES) }, value = o.devices?.let { stringResource(R.string.count_devices, it) }) }
                row {
                    val sizes = o.sizes?.let { s -> profileText(s, long = false)?.let { stringResource(it) } ?: s }
                    // a storage warning replaces the size profile, in red when something stops soon
                    val storage = chat.kelo.app.my.StorageRules.fromKey(o.storage)
                    val warning = when (storage) {
                        chat.kelo.app.my.StorageState.HOST_LOW -> stringResource(R.string.storage_row_host)
                        chat.kelo.app.my.StorageState.CRITICAL -> stringResource(R.string.storage_row_critical)
                        chat.kelo.app.my.StorageState.WARN -> stringResource(R.string.storage_row_warn, o.storagePercent ?: 80)
                        chat.kelo.app.my.StorageState.OK -> null
                    }
                    MenuRow(
                        KeloIcons.Chip, stringResource(R.string.resources_title), { go(MyView.RESOURCES) }, value = warning ?: sizes,
                        valueColor = when {
                            warning == null -> null
                            chat.kelo.app.my.StorageRules.urgent(storage) -> Kelo.colors.danger
                            else -> Kelo.colors.warn
                        },
                    )
                }
                if (ServerUpdateRules.shown(server)) row {
                    // the version the server runs; in the accent colour while a newer one waits
                    MenuRow(
                        KeloIcons.Cloud, stringResource(R.string.server_update_title), { go(MyView.SERVER_UPDATE) },
                        value = server?.current?.takeIf { it.isNotBlank() },
                        valueColor = if (ServerUpdateRules.canUpdate(server)) c.accent else null,
                    )
                }
            }

            SectionLabel(stringResource(R.string.my_section_agent))
            MenuGroup {
                row { MenuRow(KeloIcons.Spark, stringResource(R.string.my_agent_title), { go(MyView.AGENT) }, value = profile.agentName) }
            }

            SectionLabel(stringResource(R.string.my_section_model))
            MenuGroup {
                row {
                    MenuRow(KeloIcons.Chat, stringResource(R.string.model_title), { go(MyView.MODEL) },
                        value = profile.model.ifEmpty { null }?.let { ModelNames.display(it, null) }
                            ?: stringResource(if (profile.modelReady == false) R.string.model_not_chosen else R.string.model_sheet_server_default))
                }
                row { MenuRow(KeloIcons.Pulse, stringResource(R.string.usage_title), { go(MyView.USAGE) }, value = usageSummary(o.usage, profile.model)) }
            }

            SectionLabel(stringResource(R.string.my_section_connections))
            MenuGroup {
                row {
                    MenuRow(KeloIcons.Plug, stringResource(R.string.my_services), { app.openPage.value = "CONNECTORS" },
                        value = pluralStringResource(R.plurals.my_services_count, connected.size, connected.size))
                }
            }

            SectionLabel(stringResource(R.string.my_section_security))
            MenuGroup {
                row {
                    val bad = o.creds.any { it.isBad }
                    MenuRow(
                        KeloIcons.Key, stringResource(R.string.vault_title), { go(MyView.VAULT) },
                        value = when {
                            bad -> stringResource(R.string.my_vault_check)
                            o.locked -> stringResource(R.string.my_vault_locked)
                            o.creds.isNotEmpty() -> stringResource(R.string.count_items, o.creds.size)
                            else -> null
                        },
                        valueColor = if (bad) c.danger else null,
                    )
                }
                row {
                    MenuRow(KeloIcons.Card, stringResource(R.string.wallet_title), { go(MyView.WALLET) },
                        value = o.cards?.takeIf { it > 0 }?.let { pluralStringResource(R.plurals.wallet_card_count, it, it) }, badge = o.walletUnseen?.takeIf { it > 0 })
                }
                row { MenuRow(KeloIcons.Shield, stringResource(R.string.agent_tab_permissions), { go(MyView.PERMISSIONS) }, value = stringResource(R.string.my_perm_value)) }
                row { MenuRow(KeloIcons.Lock, stringResource(R.string.my_privacy_title), { go(MyView.PRIVACY) }) }
            }

            SectionLabel(stringResource(R.string.my_section_data))
            MenuGroup {
                row { MenuRow(KeloIcons.File, stringResource(R.string.docs_title), { go(MyView.DOCUMENTS) }, value = o.documents?.let { stringResource(R.string.count_items, it) }) }
                row { MenuRow(KeloIcons.Book, stringResource(R.string.my_records_title), { go(MyView.RECORDS) }, value = stringResource(R.string.my_records_value)) }
                row { MenuRow(KeloIcons.Trash, stringResource(R.string.my_clear_chat), { confirmClear = true }, danger = true) }
            }

            SectionLabel(stringResource(R.string.my_section_app))
            MenuGroup {
                row {
                    val on by app.settings.notifications.collectAsState()
                    MenuRow(KeloIcons.Bell, stringResource(R.string.notify_my_row), { go(MyView.NOTIFY) },
                        value = stringResource(if (on) R.string.notify_state_on else R.string.notify_state_off), valueColor = if (on) null else c.danger)
                }
                row {
                    val lang by app.settings.language.collectAsState()
                    MenuRow(KeloIcons.Globe, stringResource(R.string.my_language), { go(MyView.LANGUAGE) },
                        value = Languages.override(lang)?.let(Languages::name) ?: stringResource(R.string.language_system))
                }
                row {
                    val theme by app.settings.theme.collectAsState()
                    MenuRow(KeloIcons.Phone, stringResource(R.string.desktop_section), { go(MyView.DESKTOP) },
                        value = stringResource(R.string.desktop_theme) + " · " + stringResource(themeLabel(theme)))
                }
                if (app.updates.enabled) row {
                    val update by app.updates.state.collectAsState()
                    MenuRow(KeloIcons.Retry, stringResource(R.string.update_check), { scope.launch { app.updates.check() } }, value = updateRowValue(update))
                }
            }

            if (o.operator == true) {
                SectionLabel(stringResource(R.string.my_section_operator))
                MenuGroup { row { MenuRow(KeloIcons.Cloud, stringResource(R.string.operator_page_title), { go(MyView.OPERATOR) }) } }
            }

            Box(Modifier.height(16.dp))
            MenuGroup { row { MenuRow(KeloIcons.ArrowRight, stringResource(R.string.my_logout), { confirmLogout = true }, danger = true) } }
            val versionLine = listOfNotNull(
                stringResource(R.string.desktop_version, chat.kelo.desktop.core.ReleaseInfo.VERSION),
                o.version?.takeIf { it.isNotEmpty() }?.let { stringResource(R.string.my_server_version, it) },
            ).joinToString("\n")
            Text(versionLine, color = c.muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(start = 16.dp, top = 12.dp))
        }
    }

    if (confirmLogout) ConfirmDialog(
        text = stringResource(R.string.desktop_logout_confirm), yes = stringResource(R.string.my_logout),
        onDismiss = { confirmLogout = false },
        onYes = {
            scope.launch {
                // this device's own token is removed from the server's list too, so it is not left behind
                runCatching {
                    val api = app.sessions.requireApi()
                    val me = api.get<DeviceList>("/api/devices").current
                    if (me != null) api.delete<JsonElement>("/api/devices/$me")
                }
                app.sessions.signOut()
                onSignedOut()
            }
        },
    )
    if (confirmClear) ConfirmDialog(
        title = stringResource(R.string.my_clear_chat),
        text = stringResource(R.string.my_clear_chat_confirm), yes = stringResource(R.string.delete),
        onDismiss = { confirmClear = false },
        onYes = {
            scope.launch {
                note = try {
                    // the chat and what came of it (the task log, jobs, approvals, notifications, follow-ups, day summaries, step pictures)
                    app.sessions.requireApi().delete<JsonElement>("/api/messages")
                    withContext(Dispatchers.IO) { runCatching { app.sessions.keep(DesktopCaches.MESSAGES, emptyList()) } }
                    app.sessions.historyWasCleared()
                    strings.get(R.string.my_clear_chat_done)
                } catch (e: ApiError) { e.detail }
            }
        },
    )
}

private fun themeLabel(v: String): Int = when (v) {
    "light" -> R.string.desktop_theme_light
    "dark" -> R.string.desktop_theme_dark
    else -> R.string.desktop_theme_system
}

/** This device: start at sign-in, the tray, reduced motion and the appearance (desktop only; a phone has none of these). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DesktopPage(app: AppContainer, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    SubPage(stringResource(R.string.desktop_section), onBack) {
        val mode = Autostart.mode
        var autostart by remember { mutableStateOf(runCatching { Autostart.isOn() }.getOrDefault(false)) }
        SwitchRow(
            stringResource(R.string.desktop_start_at_login), autostart, enabled = mode == Autostart.Mode.SWITCH,
            subtitle = when (mode) {
                Autostart.Mode.SWITCH -> stringResource(R.string.desktop_start_at_login_hint)
                Autostart.Mode.SYSTEM -> stringResource(R.string.desktop_start_at_login_store)
                Autostart.Mode.UNAVAILABLE -> stringResource(R.string.desktop_start_at_login_unavailable)
            },
            onChange = { on -> scope.launch { autostart = withContext(Dispatchers.IO) { Autostart.set(on) } } },
        )
        val tray by app.settings.keepInTray.collectAsState()
        SwitchRow(stringResource(R.string.desktop_keep_in_tray), tray, app.settings::setKeepInTray)
        val motion by app.settings.reduceMotion.collectAsState()
        SwitchRow(stringResource(R.string.desktop_reduce_motion), motion, app.settings::setReduceMotion)
        SectionLabel(stringResource(R.string.desktop_theme))
        val theme by app.settings.theme.collectAsState()
        FlowRow(Modifier.padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("system", "light", "dark").forEach { v -> ChoiceChip(stringResource(themeLabel(v)), selected = theme == v, onClick = { app.settings.setTheme(v) }) }
        }
        MutedText(stringResource(R.string.desktop_version, chat.kelo.desktop.core.ReleaseInfo.VERSION), small = true)
    }
}
