package chat.kelo.app.my

import android.app.Activity
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import chat.kelo.app.wallet.WalletInfo
import chat.kelo.app.wallet.WalletNotifications
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.Caches
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.ui.components.StaleHint
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.DeviceList
import chat.kelo.app.core.Languages
import chat.kelo.app.core.Profile
import chat.kelo.app.shell.OnTabReselect
import chat.kelo.app.shell.Routes
import chat.kelo.app.shell.HideShellChrome
import chat.kelo.app.shell.LocalShellChrome
import chat.kelo.app.shell.bottomBarHeight
import chat.kelo.app.shell.headerHeight
import chat.kelo.app.ui.PushHost
import chat.kelo.app.ui.rememberVaultGate
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement

/** The My page's sub pages. */
private object MyView {
    const val HOME = "home"
    const val AGENT = "agent"
    const val PERMISSIONS = "permissions"
    const val RECORDS = "records"
    const val NOTIFY = "notify"
    const val PROFILE = "profile"
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
    const val SERVER_UPDATE = "server_update"
    const val KEYGUARD = "keyguard"
}

/**
 * The 마이 tab: one grouped settings list (account and devices, the agent, the model and its usage, connections, security, data,
 * the app, and for the server's owner its operator settings). It shows only what belongs to the person; the assistant's skills
 * and notes are not listed here. Sub pages cover the whole screen (the app's bars step aside); the back arrow or the phone's back
 * gesture steps out of them, and the list comes back scrolled where it was left. The vault opens only after the phone's own check
 * (fingerprint, face or screen lock). [onOpenTab] switches to another tab (연결 opens 커넥터; 권한's waiting questions open 작업).
 */
@Composable
fun MyScreen(profile: Profile, onProfile: (Profile) -> Unit, onSignedOut: () -> Unit, onOpenTab: (String) -> Unit = {}) {
    val container = LocalAppContainer.current
    val sessions = container.sessions
    val activity = LocalContext.current as? Activity
    val scope = rememberCoroutineScope()
    val chrome = LocalShellChrome.current
    var stack by rememberSaveable { mutableStateOf(listOf<String>()) }
    var vaultNoLock by rememberSaveable { mutableStateOf(false) }
    val view = stack.lastOrNull() ?: MyView.HOME
    // the header and tabs start stepping aside in the same frame as the page starts sliding
    fun push(v: String) { stack = MyStack.push(stack, v); chrome.hide(CHROME_KEY, MyStack.isSub(stack)) }
    val back: () -> Unit = { stack = MyStack.pop(stack); chrome.hide(CHROME_KEY, MyStack.isSub(stack)) }
    val openVault = rememberVaultGate { noLock -> vaultNoLock = noLock; push(MyView.VAULT) }
    fun go(v: String) = if (v == MyView.VAULT) openVault() else push(v)
    HideShellChrome(CHROME_KEY, MyStack.isSub(stack))   // also after the activity is recreated with a sub page open
    // the CLI a notification asked 마이 → 모델 to show (its provider), until the page is left
    var modelCli by rememberSaveable { mutableStateOf<String?>(null) }
    var keyguardCode by rememberSaveable { mutableStateOf(false) }   // the page opens at the recovery code (from the locked banner)
    LaunchedEffect(view) { if (view != MyView.MODEL) modelCli = null }
    // a payment notification asked for the wallet
    val pageRequest by container.myPageRequests.collectAsStateWithLifecycle()
    LaunchedEffect(pageRequest) {
        val p = pageRequest ?: return@LaunchedEffect
        container.myPageRequests.value = null
        if (p == chat.kelo.app.shell.MY_PAGE_WALLET && stack.lastOrNull() != MyView.WALLET) push(MyView.WALLET)
        if (p == chat.kelo.app.shell.MY_PAGE_WALLET_ADD) {   // the card form, behind the wallet's own lock
            chat.kelo.app.wallet.WalletPlaces.go(chat.kelo.app.wallet.WalletView.Add)
            if (stack.lastOrNull() != MyView.WALLET) push(MyView.WALLET)
        }
        if (p == chat.kelo.app.shell.MY_PAGE_AGENT && stack.lastOrNull() != MyView.AGENT) push(MyView.AGENT)
        if (p == chat.kelo.app.shell.MY_PAGE_MODEL || p.startsWith(chat.kelo.app.shell.MY_PAGE_MODEL + ":")) {
            modelCli = p.substringAfter(':', "").ifEmpty { null }   // a newer CLI notice: the page shows that CLI's box
            if (stack.lastOrNull() != MyView.MODEL) push(MyView.MODEL)
        }
        if (p == chat.kelo.app.shell.MY_PAGE_OPERATOR && stack.lastOrNull() != MyView.OPERATOR) push(MyView.OPERATOR)
        if (p == chat.kelo.app.shell.MY_PAGE_SERVER_UPDATE && stack.lastOrNull() != MyView.SERVER_UPDATE) push(MyView.SERVER_UPDATE)
        if ((p == chat.kelo.app.shell.MY_PAGE_KEYGUARD || p == chat.kelo.app.shell.MY_PAGE_KEYGUARD_CODE) && stack.lastOrNull() != MyView.KEYGUARD) {
            keyguardCode = p == chat.kelo.app.shell.MY_PAGE_KEYGUARD_CODE
            push(MyView.KEYGUARD)
        }
        // a storage warning: its tap opens the resources page, its 정리하기 action the cleanup screen (over the resources page)
        if (p == chat.kelo.app.shell.MY_PAGE_RESOURCES && stack.lastOrNull() != MyView.RESOURCES) push(MyView.RESOURCES)
        if (p == chat.kelo.app.shell.MY_PAGE_CLEANUP && stack.lastOrNull() != MyView.CLEANUP) {
            if (stack.lastOrNull() != MyView.RESOURCES) push(MyView.RESOURCES)
            push(MyView.CLEANUP)
        }
    }
    // the home page's scroll lives here, not in the home page, so it survives a visit to a sub page (and a rotation)
    val homeScroll = rememberSaveable(saver = ScrollState.Saver) { ScrollState(0) }
    OnTabReselect(Routes.MY) {
        if (MyStack.isSub(stack)) back() else homeScroll.animateScrollTo(0)
    }
    // The numbers kept from last time (on the phone, encrypted) show at once, and coming back to My shows the last ones instead of
    // an empty page that then grows (which made the page jump under the finger); they are refreshed in place.
    val holder: OverviewHolder = viewModel { OverviewHolder(runCatching { sessions.cached(Caches.MY) }.getOrNull()) }
    var o by holder.overview
    var offline by holder.offline
    // 서버 업데이트 (owner only; null = show nothing) and, while an update runs, whether the restarting server answers
    var server by holder.server
    var serverWaiting by holder.serverWaiting
    var serverGaveUp by holder.serverGaveUp
    val serverRunning = ServerUpdateRules.running(server)
    LaunchedEffect(serverRunning) {
        if (!serverRunning) return@LaunchedEffect
        val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@LaunchedEffect
        val start = server ?: return@LaunchedEffect
        ServerUpdateRules.follow(api, start) { f -> server = f.status; serverWaiting = f.waiting; serverGaveUp = f.gaveUp }
    }

    fun refreshUsage(refresh: Boolean = false) {
        scope.launch {
            runCatching { sessions.requireApi().get<Usage>("/api/usage${if (refresh) "?refresh=true" else ""}") }.onSuccess { u -> o = o.copy(usage = u) }
        }
    }

    // fresh numbers every time the home page is shown; each part replaces its old value when it arrives, and the whole is kept
    // for next time once all have answered
    LaunchedEffect(view) {
        if (view != MyView.HOME) return@LaunchedEffect
        val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@LaunchedEffect
        val parts = listOf(
            async { runCatching { api.get<Usage>("/api/usage") }.onSuccess { u -> o = o.copy(usage = u) } },
            async { runCatching { api.get<DeviceList>("/api/devices") }.onSuccess { d -> o = o.copy(devices = d.devices.size) } },
            async { runCatching { api.get<VaultState>("/api/vault") }.onSuccess { v -> o = o.copy(creds = v.items, locked = v.locked) } },
            async { runCatching { api.get<JsonArray>("/api/uploads") }.onSuccess { d -> o = o.copy(documents = d.size) } },
            async {
                runCatching { api.get<Resources>("/api/resources") }
                    .onSuccess { r -> o = o.copy(sizes = r.profile, storage = StorageRules.key(StorageRules.state(r.alert)), storagePercent = r.alert.percent) }
            },
            async { runCatching { api.get<About>("/api/about") }.onSuccess { a -> o = o.copy(version = a.version) } },
            // 서버 업데이트: an owner sign-in on a server whose updater is set up (403, 404 and "unavailable": nothing shows)
            async { runCatching { ServerUpdateRules.load(api) }.onSuccess { s -> if (!ServerUpdateRules.running(server)) server = s } },
            async { runCatching { api.get<WalletNotifications>("/api/wallet/notifications?limit=1") }.onSuccess { n -> o = o.copy(walletUnseen = n.unseen) } },
            async { runCatching { api.get<WalletInfo>("/api/wallet/cards") }.onSuccess { w -> o = o.copy(cards = w.cards.size) } },
            // the operator section: only for an owner sign-in (the access key, or a phone marked as the owner's) on the operator's own
            // server; a self-hosted copy says operator=false, any other phone gets 403. A server from before the call (404): the older rule
            async {
                runCatching { api.get<OperatorStatus>("/api/operator/status").operator }
                    .map { OperatorRules.operatorFrom(null, it) }
                    .recoverCatching { e ->
                        val status = (e as? ApiError)?.status?.takeIf { it == 403 || it == 404 } ?: throw e
                        val older = if (status == 404) runCatching { api.get<JsonElement>("/api/operator/firebase"); null }
                            .recoverCatching { f -> if (f is ApiError && f.status == 403) 403 else throw f }.getOrElse { -1 } else -1
                        OperatorRules.operatorFrom(status, null) { OperatorRules.ownerFrom(older) }
                    }
                    .onSuccess { shown -> shown?.let { o = o.copy(operator = it) } }
            },
        ).awaitAll()
        offline = MyOverview.offline(parts.map { it.exceptionOrNull() })
        if (parts.any { it.isSuccess }) sessions.keep(api, Caches.MY, o)
    }

    PushHost(
        state = stack,
        depth = { it.size },
        back = if (MyStack.isSub(stack)) MyStack.pop(stack) else null,
        onBack = back,
        modifier = Modifier.fillMaxSize(),
    ) { shown ->
        when (shown.lastOrNull() ?: MyView.HOME) {
            MyView.PROFILE -> ProfilePage(profile, onProfile, back)
            MyView.AGENT -> AgentPage(profile, onProfile, back)
            MyView.PERMISSIONS -> PermissionsPage(back, onOpenWork = { container.openRequests.value = chat.kelo.app.notify.OpenTarget.Activity })
            MyView.RECORDS -> RecordsPage(back)
            MyView.NOTIFY -> NotifyPage(back, onDevices = { push(MyView.DEVICES) })
            MyView.MODEL -> ModelPage(profile, onProfile, back, cliProvider = modelCli)
            MyView.USAGE -> UsagePage(o.usage, profile.model, onRefresh = { refreshUsage(true) }, onBack = back)
            MyView.DEVICES -> DevicesPage(back, onSignedOut)
            MyView.WALLET -> chat.kelo.app.wallet.WalletPage(back, onSeen = { o = o.copy(walletUnseen = 0) })
            MyView.VAULT -> VaultPage(back, onGoModel = { push(MyView.MODEL) }, noLock = vaultNoLock)
            MyView.RESOURCES -> ResourcesPage(back, onCleanup = { push(MyView.CLEANUP) })
            MyView.CLEANUP -> CleanupPage(back)
            MyView.DOCUMENTS -> DocumentsPage(back)
            MyView.PRIVACY -> PrivacyPage(back)
            MyView.OPERATOR -> OperatorPage(back)
            MyView.KEYGUARD -> chat.kelo.app.keyguard.KeyguardPage(back, startWithCode = keyguardCode)
            MyView.SERVER_UPDATE -> ServerUpdatePage(server, serverWaiting, serverGaveUp, onStatus = { s ->
                server = s
                if (s == null) back()   // this sign-in may no longer see it
            }, onBack = back)
            MyView.LANGUAGE -> LanguagePage(container.store.language ?: Languages.SYSTEM, onChange = { code ->
                chat.kelo.app.LocaleSupport.pick(activity, container, code)   // the whole app in the chosen language
            }, onBack = back)
            else -> Box(Modifier.fillMaxSize()) {
                Home(profile, o, server, homeScroll, ::go, onSignedOut, onOpenTab)
                StaleHint(offline, Modifier.align(Alignment.BottomCenter).padding(bottom = bottomBarHeight() + 12.dp))
            }
        }
    }
}

private const val CHROME_KEY = "my-sub"

private class OverviewHolder(kept: MyOverview?) : ViewModel() {
    val overview = mutableStateOf(kept ?: MyOverview())
    val offline = mutableStateOf(false)
    val server = mutableStateOf<ServerUpdateStatus?>(null)
    val serverWaiting = mutableStateOf(false)
    val serverGaveUp = mutableStateOf(false)
}

/** The My page's stack of sub pages (empty is the home page). */
internal object MyStack {
    fun push(stack: List<String>, v: String): List<String> = stack + v
    fun pop(stack: List<String>): List<String> = stack.dropLast(1)
    fun isSub(stack: List<String>): Boolean = stack.isNotEmpty()
}

@Composable
private fun Home(profile: Profile, o: MyOverview, server: ServerUpdateStatus?, scroll: ScrollState, go: (String) -> Unit, onSignedOut: () -> Unit, onOpenTab: (String) -> Unit) {
    val container = LocalAppContainer.current
    val sessions = container.sessions
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var confirmClear by remember { mutableStateOf(false) }
    var confirmLogout by remember { mutableStateOf(false) }
    val c = Kelo.colors
    val connected = o.creds.filter { it.hasSecret && (it.kind == VaultEntry.KIND_API || it.kind == VaultEntry.KIND_EMAIL) }
    val cleared = stringResource(R.string.my_clear_chat_done)

    val update by container.updater.state.collectAsStateWithLifecycle()
    Column(
        Modifier.fillMaxSize().background(c.bg).verticalScroll(scroll)
            .padding(start = 8.dp, end = 8.dp, top = headerHeight() + 4.dp, bottom = bottomBarHeight() + 16.dp),
    ) {
        // a newer build of this app is out: its download (or Play's), check and install start here
        if (update.offer != null) {
            chat.kelo.app.update.UpdateCard(
                update, onGet = { container.updater.get() }, onInstall = { container.updater.install() },
                modifier = Modifier.padding(start = 8.dp, end = 8.dp, top = 8.dp),
            )
        }
        // the owner's server: a newer release, an update running, or one that failed or was undone in the last day
        ServerUpdateRules.card(server, System.currentTimeMillis() / 1000.0)?.let { card ->
            ServerUpdateCard(card, onClick = { go(MyView.SERVER_UPDATE) }, modifier = Modifier.padding(start = 8.dp, end = 8.dp, top = 8.dp))
        }
        SectionLabel(stringResource(R.string.my_section_account))
        MenuGroup {
            row { MenuRow(KeloIcons.User, stringResource(R.string.profile_title), { go(MyView.PROFILE) }, value = profile.userName.ifEmpty { stringResource(R.string.my_name_missing) }) }
            row { MenuRow(KeloIcons.Phone, stringResource(R.string.devices_title), { go(MyView.DEVICES) }, value = o.devices?.let { stringResource(R.string.count_devices, it) }) }
            row {
                val sizes = o.sizes?.let { p -> profileText(p, long = false)?.let { stringResource(it) } ?: p }
                // a storage warning replaces the size profile, in red when something stops soon
                val storage = StorageRules.fromKey(o.storage)
                val warning = when (storage) {
                    StorageState.HOST_LOW -> stringResource(R.string.storage_row_host)
                    StorageState.CRITICAL -> stringResource(R.string.storage_row_critical)
                    StorageState.WARN -> stringResource(R.string.storage_row_warn, o.storagePercent ?: 80)
                    StorageState.OK -> null
                }
                MenuRow(
                    KeloIcons.Chip, stringResource(R.string.resources_title), { go(MyView.RESOURCES) }, value = warning ?: sizes,
                    valueColor = when { warning == null -> null; StorageRules.urgent(storage) -> c.danger; else -> c.warn },
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
            row { MenuRow(KeloIcons.Chat, stringResource(R.string.model_title), { go(MyView.MODEL) }, value = profile.model.ifEmpty { null }?.let { chat.kelo.app.chat.ModelNames.display(it, null) } ?: stringResource(if (profile.modelReady == false) R.string.model_not_chosen else R.string.model_sheet_server_default)) }
            row { MenuRow(KeloIcons.Pulse, stringResource(R.string.usage_title), { go(MyView.USAGE) }, value = usageSummary(o.usage, profile.model)) }
        }

        SectionLabel(stringResource(R.string.my_section_connections))
        MenuGroup {
            row {
                MenuRow(
                    KeloIcons.Plug, stringResource(R.string.my_services), { onOpenTab(Routes.CONNECTORS) },
                    value = pluralStringResource(R.plurals.my_services_count, connected.size, connected.size),
                )
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
                MenuRow(
                    KeloIcons.Card, stringResource(R.string.wallet_title), { go(MyView.WALLET) },
                    value = o.cards?.takeIf { it > 0 }?.let { pluralStringResource(R.plurals.wallet_card_count, it, it) },
                    badge = o.walletUnseen?.takeIf { it > 0 },
                )
            }
            row { MenuRow(KeloIcons.Shield, stringResource(R.string.agent_tab_permissions), { go(MyView.PERMISSIONS) }, value = stringResource(R.string.my_perm_value)) }
            row { MenuRow(KeloIcons.Lock, stringResource(R.string.my_privacy_title), { go(MyView.PRIVACY) }) }
            // the key of passwords, keys and cards on the owner's phones (Keyguard; owner only, the page says so otherwise)
            row {
                val locked by container.sessions.locked.collectAsStateWithLifecycle()
                MenuRow(KeloIcons.Shield, stringResource(R.string.keyguard_title), { go(MyView.KEYGUARD) },
                    value = if (locked) stringResource(R.string.keyguard_locked_title) else null, valueColor = if (locked) c.danger else null)
            }
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
                // notifications: the phone's current state; the page has the rest (and the agent writing first)
                val on = chat.kelo.app.notify.rememberNotificationsOn()
                // signed in with the access key: the server keeps push tokens only for paired phones (403), so say so here
                val base = sessions.api.value?.baseUrl
                val needsDevice = base != null && remember(base, o) { container.notifyPrefs.pushNeedsDevice(base) }
                MenuRow(
                    KeloIcons.Bell, stringResource(R.string.notify_my_row), { go(MyView.NOTIFY) },
                    value = stringResource(
                        when {
                            on && needsDevice -> R.string.notify_state_needs_device
                            on -> R.string.notify_state_on
                            else -> R.string.notify_state_off
                        },
                    ),
                    valueColor = if (on && !needsDevice) null else c.danger,
                )
            }
            row { MenuRow(KeloIcons.Globe, stringResource(R.string.my_language), { go(MyView.LANGUAGE) }, value = Languages.override(container.store.language)?.let(Languages::name) ?: stringResource(R.string.language_system)) }
            if (update.enabled) {
                // the running version, and a check right now (the Play build asks Google Play)
                row {
                    MenuRow(
                        KeloIcons.ArrowDown, stringResource(R.string.update_check), { container.updater.check(force = true) },
                        value = listOfNotNull(stringResource(R.string.my_version, update.running), chat.kelo.app.update.checkValue(update)).joinToString(" · "),
                        valueColor = if (update.offer != null) c.accent else null,
                    )
                }
            }
        }

        if (o.operator == true) {
            SectionLabel(stringResource(R.string.my_section_operator))
            MenuGroup {
                row { MenuRow(KeloIcons.Cloud, stringResource(R.string.operator_page_title), { go(MyView.OPERATOR) }) }
            }
        }

        Box(Modifier.height(16.dp))
        MenuGroup { row { MenuRow(KeloIcons.ArrowRight, stringResource(R.string.my_logout), { confirmLogout = true }, danger = true) } }
        val appVersion = remember { runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull().orEmpty() }
        val versionLine = listOfNotNull(
            appVersion.takeIf { it.isNotEmpty() }?.let { stringResource(R.string.my_version, it) },
            o.version?.takeIf { it.isNotEmpty() }?.let { stringResource(R.string.my_server_version, it) },
        ).joinToString("\n")
        if (versionLine.isNotEmpty()) {
            Text(
                versionLine, color = c.muted, style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(start = 16.dp, top = 12.dp),
            )
        }
    }

    if (confirmLogout) {
        ConfirmDialog(
            text = stringResource(R.string.my_logout_confirm), yes = stringResource(R.string.my_logout),
            onYes = onSignedOut, onDismiss = { confirmLogout = false },
        )
    }
    if (confirmClear) {
        ConfirmDialog(
            text = stringResource(R.string.my_clear_chat_confirm), yes = stringResource(R.string.delete),
            onDismiss = { confirmClear = false },
            onYes = {
                scope.launch {
                    try {
                        sessions.requireApi().delete<JsonElement>("/api/messages")   // the chat and what came of it (ClearHistory)
                        ClearHistory.forgetLocally(sessions) { container.notifier.cancelAll() }
                        Toast.makeText(context, cleared, Toast.LENGTH_SHORT).show()
                    } catch (e: ApiError) {
                        Toast.makeText(context, e.detail, Toast.LENGTH_SHORT).show()
                    }
                }
            },
        )
    }
}

