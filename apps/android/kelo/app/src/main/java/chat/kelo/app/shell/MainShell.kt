package chat.kelo.app.shell

import androidx.annotation.StringRes
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.border
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.agent.AgentPicture
import chat.kelo.app.chat.AgentActivityRules
import chat.kelo.app.chat.ChatScreen
import chat.kelo.app.chat.ChatSearchScreen
import chat.kelo.app.connectors.ConnectorsScreen
import chat.kelo.app.core.Profile
import chat.kelo.app.goals.GoalsScreen
import chat.kelo.app.ui.rememberTabPageFrame
import chat.kelo.app.my.MyScreen
import androidx.compose.runtime.rememberUpdatedState
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.filter
import chat.kelo.app.my.MyPage
import chat.kelo.app.my.MyScreenHooks
import chat.kelo.app.notify.OpenTarget
import chat.kelo.app.ui.character.KeloCharacter
import chat.kelo.app.ui.character.centerShift
import chat.kelo.app.ui.character.KeloMood
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.theme.Motion
import chat.kelo.app.work.WorkBadge
import chat.kelo.app.work.WorkScreen
import kotlinx.coroutines.launch

/**
 * The signed-in app: a top app bar (on 채팅 the agent's picture and name, with what it is doing as a second line while it works; on
 * the other tabs the tab's name and its page's actions), the page, and five tabs at the bottom. A page that wants the whole screen
 * (a sub page, a connector's detail) asks the bars to step aside ([HideShellChrome]). Tapping the tab you are already in takes
 * that tab back to its start. A browser tab's live view opens over everything (from a chat card or a notification).
 */
@Composable
fun MainShell(profile: Profile, onProfile: (Profile) -> Unit, onSignedOut: () -> Unit) {
    // Nest's extensions for this sign-in, drawn by the native Nest renderer
    ProvideNest(LocalAppContainer.current.sessions) { MainShellContent(profile, onProfile, onSignedOut) }
}

@Composable
private fun MainShellContent(profile: Profile, onProfile: (Profile) -> Unit, onSignedOut: () -> Unit) {
    val nav = rememberNavController()
    val c = Kelo.colors
    val reselect = remember { TabReselect() }
    val chrome = remember { ShellChrome() }
    val slots = remember { ShellBarSlots() }
    val topBarScroll = remember { TopBarScroll() }
    val density = LocalDensity.current
    val reduce = LocalReduceMotion.current
    var headerH by remember { mutableStateOf(0.dp) }
    var barH by remember { mutableStateOf(0.dp) }
    val statusTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    val navBottom = WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding()
    val header = if (headerH > 0.dp) headerH else statusTop + 96.dp
    val bar = if (barH > 0.dp) barH else navBottom + BAR_ESTIMATE
    val container = LocalAppContainer.current
    val shellVm = chat.kelo.app.ui.platform.keloViewModel("shell") {
        ShellViewModel(container.sessions, container.agentActivity, container.agentAvatar, chat.kelo.app.chat.AndroidChatPlatform, container.phoneLocation)
    }
    androidx.compose.runtime.SideEffect { shellVm.bind(profile, onProfile) }
    val shell by shellVm.state.collectAsStateWithLifecycle()

    fun go(route: String) = nav.navigate(route) {
        popUpTo(nav.graph.findStartDestination().id) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }

    // the tabs as data: the built-in ones in today's order (the phone opens 작업 from the header), Nest's after 커넥터
    val layout = TabLayout.Default
    val hidden = remember(layout) { hiddenTabs(builtInTabs(withWork = false) { { } }, layout) }   // only their names and icons are read: My lists them
    val builtIn = builtInTabs(withWork = false) { id ->
        when (id) {
            Routes.CHAT -> { { ChatScreen(profile = profile, onProfile = onProfile, onOpenConnector = { connector ->
                nav.navigate(Routes.connectorOpen(connector)) {
                    popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                    launchSingleTop = true
                }
            }, onOpenBrowser = { card -> container.openRequests.value = OpenTarget.Tab.ofCard(card) },
                onOpenCardForm = { container.myPageRequests.value = MyPage.WALLET_ADD; go(Routes.MY) },
                onOpenGoals = { go(Routes.GOALS) }) } }
            Routes.GOALS -> { { GoalsScreen(rememberTabPageFrame(Routes.GOALS)) } }
            Routes.CONNECTORS -> { { ConnectorsScreen(frame = rememberTabPageFrame(Routes.CONNECTORS)) } }
            else -> { {
                val myHooks = rememberMyHooks(hiddenTabs = hidden, openTab = { route -> go(route) })
                MyScreen(profile = profile, onProfile = onProfile, onSignedOut = onSignedOut, hooks = myHooks)
            } }
        }
    }
    val tabs = rememberTabs(builtIn, layout)

    // something shared from another app: the chat takes it
    val shared by container.shareInbox.pending.collectAsStateWithLifecycle()
    LaunchedEffect(shared != null) {
        if (shared != null && nav.currentDestination?.route != Routes.CHAT) go(Routes.CHAT)
    }

    // a browser tab live over everything (back returns to where it was opened from); in a wide window (two panes) the chat tab shows it beside the chat
    val shape = chat.kelo.app.ui.platform.LocalWindowShape.current
    val routeNow = nav.currentBackStackEntryAsState().value?.destination?.route
    var liveTab by rememberSaveable { mutableStateOf<String?>(null) }
    val side = chat.kelo.app.ui.platform.WindowRules.side(shape, onChatTab = routeNow == Routes.CHAT, liveOpen = liveTab != null)
    val sideShown = side != chat.kelo.app.ui.platform.WindowRules.Side.None
    var liveControl by rememberSaveable { mutableStateOf(false) }   // opened to solve a human check: take the tab over at once
    var livePicture by remember { mutableStateOf<String?>(null) }   // the chat card's last picture, for a tab that has closed
    HideShellChrome(LIVE_CHROME_KEY, liveTab != null && !sideShown)
    // the agent's work (what it does, asks, has planned), opened from the header over whatever tab is showing
    var workOpen by rememberSaveable { mutableStateOf(false) }
    HideShellChrome(WORK_CHROME_KEY, workOpen && !sideShown)
    // a page over the chat keeps the composer focused underneath it: put the keyboard away while it shows
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    LaunchedEffect(workOpen, liveTab != null) {
        if (OverlayKeyboard.shouldDismiss(workOpen, liveTab != null)) { keyboard?.hide(); focusManager.clearFocus(force = true) }
    }
    // the chat's search page, opened from the chat header over everything; a result opens the chat at that message
    var searchOpen by rememberSaveable { mutableStateOf(false) }
    HideShellChrome(SEARCH_CHROME_KEY, searchOpen && !sideShown)
    // a place in the 작업 tab a notification asked for, handed to it once it shows
    var workStart by remember { mutableStateOf<OpenTarget?>(null) }
    // the keyguard_locked notification was tapped: the locked banner starts the unlock check by itself
    var unlockNow by remember { mutableStateOf(false) }
    val openRequest by container.openRequests.collectAsStateWithLifecycle()
    LaunchedEffect(openRequest) {
        val target = openRequest ?: return@LaunchedEffect
        container.openRequests.value = null
        when (target) {
            OpenTarget.Chat -> { liveTab = null; go(Routes.CHAT) }
            is OpenTarget.ChatAt -> { liveTab = null; container.chatFocus.value = target.messageId; go(Routes.CHAT) }
            is OpenTarget.Entry -> { liveTab = null; workOpen = false; container.chatEntryFocus.value = target.requestId; go(Routes.CHAT) }
            OpenTarget.Wallet -> { liveTab = null; container.myPageRequests.value = MyPage.WALLET; go(Routes.MY) }
            OpenTarget.Keychain -> { liveTab = null; container.myPageRequests.value = MyPage.VAULT; go(Routes.MY) }
            OpenTarget.Operator -> { liveTab = null; container.myPageRequests.value = MyPage.OPERATOR; go(Routes.MY) }
            OpenTarget.ServerUpdate -> { liveTab = null; container.myPageRequests.value = MyPage.SERVER_UPDATE; go(Routes.MY) }
            OpenTarget.Unlock -> { liveTab = null; workOpen = false; container.sessions.setLocked(true); unlockNow = true; go(Routes.CHAT) }
            OpenTarget.Storage -> { liveTab = null; container.myPageRequests.value = MyPage.RESOURCES; go(Routes.MY) }
            OpenTarget.StorageCleanup -> { liveTab = null; container.myPageRequests.value = MyPage.CLEANUP; go(Routes.MY) }
            is OpenTarget.ModelSettings -> { liveTab = null; container.myPageRequests.value = MyPage.model(target.provider); go(Routes.MY) }
            is OpenTarget.Tab -> { liveControl = target.control; livePicture = target.picture; liveTab = target.id }
            OpenTarget.OwnTab -> { liveControl = false; livePicture = null; liveTab = runCatching { chat.kelo.app.agent.ownTabId(container.sessions.requireApi()) }.getOrDefault("owner") }
            else -> { liveTab = null; workStart = target; workOpen = true }
        }
    }
    // the agent's picture follows the profile's version until the status says otherwise
    LaunchedEffect(profile.avatarVersion) {
        container.agentAvatar.warm()
        container.agentAvatar.sync(profile.avatarVersion)
    }

    // Keyguard's enable flow asked from any page (auto-pay needs it): drawn over the whole app below
    val keyguardEnable = remember { chat.kelo.app.keyguard.KeyguardEnableHost() }
    Box(Modifier.fillMaxSize().background(c.bg)) {
        CompositionLocalProvider(
            LocalHeaderHeight provides header,
            LocalBottomBarHeight provides bar,
            chat.kelo.app.shell.LocalTabBottomInset provides bar,
            LocalTabReselect provides reselect,
            LocalShellChrome provides chrome,
            LocalShellBarSlots provides slots,
            LocalTopBarScroll provides topBarScroll,
            chat.kelo.app.keyguard.LocalKeyguardEnableHost provides keyguardEnable,
        ) {
            val splitPrefs = androidx.compose.ui.platform.LocalContext.current.getSharedPreferences("kelo.ui", android.content.Context.MODE_PRIVATE)
            val splitState = chat.kelo.app.ui.components.rememberSplitState(runCatching { splitPrefs.getFloat("split", chat.kelo.app.ui.platform.WindowRules.DEFAULT_SPLIT) }.getOrDefault(chat.kelo.app.ui.platform.WindowRules.DEFAULT_SPLIT))
            val workHooks = rememberAndroidWorkHooks()
            val dockedHooks = remember(workHooks) { DockedWorkHooks(workHooks) }
            chat.kelo.app.ui.components.TwoPane(
                first = {
                NavHost(
                    nav, startDestination = Routes.CHAT,
                    modifier = Modifier.fillMaxSize(),
                    enterTransition = { EnterTransition.None }, exitTransition = { ExitTransition.None },
                    popEnterTransition = { EnterTransition.None }, popExitTransition = { ExitTransition.None },
                ) {
                    // the tabs as data: each tab's page is its own content; a connector's detail is a sub route of 커넥터
                    tabs.forEach { tab -> composable(tab.id) { tab.content() } }
                    composable(Routes.CONNECTOR_OPEN) { entry -> ConnectorsScreen(openId = entry.arguments?.getString("id"), frame = rememberTabPageFrame(Routes.CONNECTORS)) }
                }
                },
                second = {
                    // below the header and above the bar, beside the chat
                    Box(Modifier.fillMaxSize().background(c.bg).padding(top = header, bottom = bar)) {
                        CompositionLocalProvider(LocalHeaderHeight provides 0.dp, LocalBottomBarHeight provides 0.dp) {
                            if (side == chat.kelo.app.ui.platform.WindowRules.Side.Live) {
                                androidx.activity.compose.BackHandler { liveTab = null }
                                liveTab?.let { id ->
                                    chat.kelo.app.work.LiveOverlay(id, takeControl = liveControl, picture = livePicture,
                                        onClose = { liveTab = null; liveControl = false; livePicture = null })
                                }
                            } else if (side == chat.kelo.app.ui.platform.WindowRules.Side.Work) {
                                WorkScreen(start = workStart, onStartUsed = { workStart = null }, onOpenLive = { liveControl = false; livePicture = null; liveTab = it }, onClose = null, hooks = dockedHooks)
                            }
                        }
                    }
                },
                split = splitState,
                showSecond = shape.twoPane && sideShown,
                onSplit = { share -> runCatching { splitPrefs.edit().putFloat("split", share).apply() } },
            )
        }
        val route = routeNow
        val isScrolled = topBarScroll.isScrolled(route)
        // the top area (status bar + header) tints on scroll (Material 3 surfaceContainer), content scrolls under it
        if (!chrome.headerHidden) HeaderScrim(statusTop, headerH, isScrolled, Modifier.align(Alignment.TopCenter))
        AnimatedVisibility(
            visible = !chrome.headerHidden, modifier = Modifier.align(Alignment.TopCenter),
            enter = fadeIn(Motion.small(reduce)), exit = fadeOut(Motion.small(reduce)),
        ) {
            val canCall by container.calls.available.collectAsStateWithLifecycle()
            FloatingHeader(
                profile, shell,
                onAgent = { workOpen = true },
                modifier = Modifier.onSizeChanged { headerH = with(density) { it.height.toDp() } },
                onSearch = if (route == Routes.CHAT && !sideShown) ({ searchOpen = true }) else null,
                onCall = if (route == Routes.CHAT && canCall) container.calls::ringFromButton else null,
            )
        }
        AnimatedVisibility(
            visible = !chrome.barHidden, modifier = Modifier.align(Alignment.BottomCenter),
            enter = fadeIn(Motion.small(reduce)) + slideInVertically(Motion.enter(reduce)) { it / 2 },
            exit = fadeOut(Motion.small(reduce)) + slideOutVertically(Motion.exit(reduce)) { it / 2 },
        ) {
            TabBar(nav, tabs, reselect, shell, Modifier.then(if (shape.centred) Modifier.widthIn(max = chat.kelo.app.ui.platform.WindowRules.TAB_BAR_MAX.dp) else Modifier).onSizeChanged { barH = with(density) { it.height.toDp() } })
        }
        // a new server has no model yet (the terminal setup does not ask for one): the chat leads to the model settings
        if (ModelGate.needsModel(profile) && route == Routes.CHAT && !workOpen && liveTab == null) {
            ChooseModelCard(
                onChoose = { container.myPageRequests.value = MyPage.MODEL; go(Routes.MY) },
                modifier = Modifier.align(Alignment.TopCenter).padding(top = header + 8.dp, start = 16.dp, end = 16.dp).widthIn(max = 560.dp),
            )
        }
        // a newer build of this app (the file builds' own check, or Google Play in the Play build; see update/AppUpdates.kt): a small
        // banner, once per version, under the header (clear of the chat's composer); 마이 has the card with the download, and 업데이트 확인
        val update by container.updater.state.collectAsStateWithLifecycle()
        val minVersion = profile.minAppVersion["android"]
        LaunchedEffect(minVersion) { container.updater.requireAtLeast(minVersion) }   // the server's oldest supported build
        LaunchedEffect(Unit) {
            while (true) {
                container.updater.check()   // at start, then whenever a day has passed (check() keeps the pace)
                kotlinx.coroutines.delay(60 * 60 * 1000L)
            }
        }
        // the popup (once per version; Later = 3 days) and its hold on the banners below
        val popupOffered by chat.kelo.app.core.AppVersion.update.collectAsStateWithLifecycle()
        val popupCandidate = chat.kelo.app.update.UpdatePopupRules.candidate(update, popupOffered, chat.kelo.app.core.AppVersion.channel)
        val popupOpen by container.updatePopup.open.collectAsStateWithLifecycle()
        androidx.compose.runtime.LaunchedEffect(popupCandidate, update.required) { container.updatePopup.consider(popupCandidate.takeIf { !update.required }) }
        val popupUp = popupOpen != null
        val popupNow by androidx.compose.runtime.rememberUpdatedState(popupCandidate.takeIf { !update.required })
        val popupLifecycle = androidx.lifecycle.compose.LocalLifecycleOwner.current
        androidx.compose.runtime.DisposableEffect(popupLifecycle) {
            val watcher = androidx.lifecycle.LifecycleEventObserver { _, event ->
                if (event == androidx.lifecycle.Lifecycle.Event.ON_STOP) container.updatePopup.left()
                if (event == androidx.lifecycle.Lifecycle.Event.ON_START && container.updatePopup.returned()) { container.updater.check(force = true); container.updatePopup.consider(popupNow) }
            }
            popupLifecycle.lifecycle.addObserver(watcher)
            onDispose { popupLifecycle.lifecycle.removeObserver(watcher) }
        }
        val popupUri = androidx.compose.ui.platform.LocalUriHandler.current
        popupOpen?.let { popupCandidate ->
            chat.kelo.app.update.UpdatePopupDialog(
                popupCandidate,
                onUpdate = {
                    container.updatePopup.close()
                    if (popupCandidate.source == chat.kelo.app.update.UpdatePopupRules.Source.STORE) popupUri.openUri(chat.kelo.app.update.AppNoticeRules.PLAY_URL)
                    else { container.updater.get(); go(Routes.MY) }
                },
                onLater = { container.updatePopup.close() },
            )
        }
        val offered = update.offer
        val modelCard = ModelGate.needsModel(profile) && route == Routes.CHAT
        if (!popupUp && offered != null && update.showBanner && route != Routes.MY && !workOpen && liveTab == null && !chrome.headerHidden && !modelCard) {
            chat.kelo.app.update.UpdateBanner(
                update,
                onGet = { container.updater.get(); go(Routes.MY) },
                onInstall = { container.updater.bannerSeen(); container.updater.install() },
                onClose = { container.updater.bannerSeen() },
                modifier = Modifier.align(Alignment.TopCenter).padding(top = header + 8.dp, start = 12.dp, end = 12.dp),
            )
        }
        // a newer released app that works with the server (X-Kelo-App-Update; store builds too): once per version, never over another banner
        val offeredApp by chat.kelo.app.core.AppVersion.update.collectAsStateWithLifecycle()
        val dismissedApps by container.appNotice.dismissed.collectAsStateWithLifecycle()
        // a store build hears of a newer app from its store (the popup above), never from this header alone
        val noticeVersion = offeredApp?.takeIf { chat.kelo.app.update.AppNoticeRules.action(chat.kelo.app.core.AppVersion.channel) == chat.kelo.app.update.AppNoticeRules.Action.UPDATER && chat.kelo.app.update.AppNoticeRules.show(it, dismissedApps) }
        val uriHandler = androidx.compose.ui.platform.LocalUriHandler.current
        val storeNotice = chat.kelo.app.update.AppNoticeRules.action(chat.kelo.app.core.AppVersion.channel) == chat.kelo.app.update.AppNoticeRules.Action.STORE
        if (!popupUp && noticeVersion != null && !(offered != null && update.showBanner) && route != Routes.MY && !workOpen && liveTab == null && !chrome.headerHidden && !modelCard) {
            chat.kelo.app.update.AppNoticeBanner(
                noticeVersion, store = storeNotice,
                onAction = {
                    container.appNotice.dismiss(noticeVersion)
                    if (storeNotice) uriHandler.openUri(chat.kelo.app.update.AppNoticeRules.PLAY_URL)
                    else { if (offered != null) container.updater.get(); go(Routes.MY) }
                },
                onClose = { container.appNotice.dismiss(noticeVersion) },
                modifier = Modifier.align(Alignment.TopCenter).padding(top = header + 8.dp, start = 12.dp, end = 12.dp),
            )
        }
        if (update.needsPermission) {
            chat.kelo.app.update.InstallPermissionDialog(onOpen = { container.updater.openInstallSetting() }, onDismiss = { container.updater.dismissPermission() })
        }
        if (workOpen && !sideShown) {
            androidx.activity.compose.BackHandler { workOpen = false }   // the work page's own sub pages step back first
            Box(Modifier.fillMaxSize().background(c.bg)) {
                CompositionLocalProvider(
                    LocalHeaderHeight provides 0.dp,
                    LocalBottomBarHeight provides WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding(),
                ) {
                    WorkScreen(start = workStart, onStartUsed = { workStart = null }, onOpenLive = { liveControl = false; livePicture = null; liveTab = it }, onClose = { workOpen = false }, hooks = rememberAndroidWorkHooks())
                }
            }
        }
        AnimatedVisibility(
            visible = liveTab != null && !sideShown,
            enter = fadeIn(Motion.enter(reduce)) + slideInHorizontally(Motion.enter(reduce)) { it / 10 },
            exit = fadeOut(Motion.exit(reduce)) + slideOutHorizontally(Motion.exit(reduce)) { it / 10 },
        ) {
            var shown by remember { mutableStateOf("") }
            liveTab?.let { shown = it }
            if (shown.isNotEmpty()) {
                androidx.activity.compose.BackHandler { liveTab = null }
                chat.kelo.app.work.LiveOverlay(shown, takeControl = liveControl, picture = livePicture,
                    onClose = { liveTab = null; liveControl = false; livePicture = null })
            }
        }
        if (searchOpen && !sideShown) {
            androidx.activity.compose.BackHandler { searchOpen = false }
            Box(Modifier.fillMaxSize().background(c.bg)) {
                CompositionLocalProvider(LocalHeaderHeight provides 0.dp, LocalBottomBarHeight provides 0.dp) {
                    ChatSearchScreen(
                        agentName = profile.agentName.ifBlank { "Kelo" },
                        onOpen = { id -> searchOpen = false; container.chatFocus.value = id },
                        onBack = { searchOpen = false },
                    )
                }
            }
        }
        chat.kelo.app.keyguard.KeyguardEnableOverlay(keyguardEnable)
        // a disconnected connector (or a deleted login) left data behind: ask whether to forget it too
        chat.kelo.app.connectors.ForgetDataHost()
        // 426 app_too_old from any call: the same update-required screen as min_app_version
        val tooOld by container.sessions.appTooOld.collectAsStateWithLifecycle()
        LaunchedEffect(tooOld) { if (tooOld) container.updater.requireAtLeast(chat.kelo.app.BuildConfig.VERSION_CODE.toLong() + 1) }
        // KE2E: the server's key changed: nothing more is sent until this phone is paired again
        val keyChanged by container.sessions.keyChanged.collectAsStateWithLifecycle()
        if (keyChanged) {
            PairAgainScreen(onPairAgain = { container.sessions.pairAgain(); onSignedOut() })
        }
        // Keyguard: the server holds no key ("Kelo is locked"): unlock with this phone, or the recovery code in 마이 → Security
        val locked by container.sessions.locked.collectAsStateWithLifecycle()
        if (locked && route != Routes.MY && !workOpen && liveTab == null) {
            chat.kelo.app.keyguard.LockedBanner(
                onRecover = { container.myPageRequests.value = MyPage.KEYGUARD_CODE; go(Routes.MY) },
                autoStart = unlockNow, onAutoStarted = { unlockNow = false },
                modifier = Modifier.align(Alignment.TopCenter).padding(top = header + 8.dp, start = 12.dp, end = 12.dp).widthIn(max = 560.dp),
            )
        }
        // the server is older than this build needs (X-Kelo-Server, or no KE2E at all): nothing is sent until it is updated (5.18)
        val serverSeen by chat.kelo.app.core.AppVersion.server.collectAsStateWithLifecycle()
        val serverOld by container.sessions.serverOld.collectAsStateWithLifecycle()
        if (serverOld || chat.kelo.app.core.AppVersion.serverTooOld(serverSeen)) {
            val oldScope = androidx.compose.runtime.rememberCoroutineScope()
            val oldUpdater = remember(container.sessions) {
                chat.kelo.app.shell.ServerOldUpdater(oldScope, { container.sessions.api.value }, onDone = { container.sessions.recheckServer() })
            }
            ServerTooOldScreen(serverSeen, onRetry = { container.sessions.recheckServer(); oldUpdater.probe() }, updater = oldUpdater)
        }
        // the server needs a newer build (min_app_version): this covers everything until the app is updated
        // the server has a newer release (X-Kelo-Server-Update, see update/ServerNotice.kt): once a day, after the app popup, never over
        // a full screen; "See the update" opens My -> Server update
        val serverOffered by chat.kelo.app.core.AppVersion.serverUpdate.collectAsStateWithLifecycle()
        val serverCovered = popupUp || update.required || serverOld || chat.kelo.app.core.AppVersion.serverTooOld(serverSeen) || keyChanged || locked
        val onChat = route == Routes.CHAT && !workOpen && liveTab == null
        androidx.compose.runtime.LaunchedEffect(serverOffered, serverCovered, onChat) {
            container.serverNotice.consider(serverOffered.takeIf { onChat }, container.updatePopup.lastShownMs(), serverCovered)
        }
        val serverOpen by container.serverNotice.open.collectAsStateWithLifecycle()
        serverOpen?.takeIf { onChat && !serverCovered }?.let { version ->
            chat.kelo.app.update.ServerNoticeDialog(
                version,
                onOpen = { container.serverNotice.close(); liveTab = null; container.myPageRequests.value = MyPage.SERVER_UPDATE; go(Routes.MY) },
                onLater = { container.serverNotice.close() },
            )
        }
        if (update.required) {
            val activity = androidx.compose.ui.platform.LocalContext.current as? android.app.Activity
            chat.kelo.app.update.UpdateRequiredScreen(
                update, onGet = { container.updater.get() }, onInstall = { container.updater.install() },
                onLeave = { activity?.moveTaskToBack(true) },
            )
        }
        val callSession by container.calls.session.collectAsStateWithLifecycle()
        val callMinimized by container.calls.isMinimized.collectAsStateWithLifecycle()
        if (callSession != null && !callMinimized) {
            val callUi by callSession!!.ui.collectAsStateWithLifecycle()
            androidx.activity.compose.BackHandler {
                if (callUi.phase == chat.kelo.app.call.CallSteps.Phase.RINGING) {
                    callSession?.decline()
                } else {
                    container.calls.minimized(true)
                }
            }
            val activity = androidx.compose.ui.platform.LocalContext.current as? android.app.Activity
            androidx.compose.runtime.DisposableEffect(activity) {
                activity?.window?.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                onDispose {
                    activity?.window?.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                }
            }
        }
        val agentPhoto by container.agentAvatar.image.collectAsStateWithLifecycle()
        chat.kelo.app.call.CallOverlay(
            center = container.calls,
            agentName = profile.agentName.ifBlank { "Kelo" },
            photo = agentPhoto,
        )
    }
}

private const val LIVE_CHROME_KEY = "live-view"
private const val WORK_CHROME_KEY = "work-page"
private const val SEARCH_CHROME_KEY = "chat-search"

/** Until the tab bar has been measured once. */
private val BAR_ESTIMATE = 80.dp
/** The header's round badge (photo or character) */
private const val HEADER_BADGE = 68
private val BADGE_CREAM = androidx.compose.ui.graphics.Color(0xFFFFF3D6)

/**
 * Only the strip under the clock is solid (the page's own colour), with a short fade below it. The rest of the header is see-through:
 * the floating agent badge and name tag sit on the page, and the chat shows around and up to them instead of under a solid band
 * (owner 2026-11-16: "위에 다 쳐 가리냐" after a solid header-high band replaced the fade). `isScrolled` keeps the strip's tint.
 */
@Composable
private fun HeaderScrim(
    statusTop: androidx.compose.ui.unit.Dp,
    headerH: androidx.compose.ui.unit.Dp,
    isScrolled: Boolean,
    modifier: Modifier = Modifier,
) {
    if (statusTop <= 0.dp && headerH <= 0.dp) return
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    val bg by animateColorAsState(if (isScrolled) c.card else c.bg, Motion.small(reduce), label = "headerAreaBg")
    val shape = headerScrimShape(statusTop, headerH)
    Column(modifier.fillMaxWidth()) {
        if (shape.solid > 0.dp) Box(Modifier.fillMaxWidth().height(shape.solid).background(bg))
        Box(Modifier.fillMaxWidth().height(shape.fade).background(Brush.verticalGradient(listOf(bg, bg.copy(alpha = 0f)))))
    }
}

private const val STATUS_FADE = 16

/** The scrim's two parts: the solid strip under the clock and the fade below it. Their sum must never reach the header's height. */
internal data class HeaderScrimShape(val solid: androidx.compose.ui.unit.Dp, val fade: androidx.compose.ui.unit.Dp)

/** Only the status-bar strip is solid; the header itself is see-through so the chat shows around the badge and name tag. */
@Suppress("UNUSED_PARAMETER")
internal fun headerScrimShape(statusTop: androidx.compose.ui.unit.Dp, headerH: androidx.compose.ui.unit.Dp) =
    HeaderScrimShape(solid = statusTop.coerceAtLeast(0.dp), fade = STATUS_FADE.dp)

/** Five destinations; the chosen one sits in a tinted pill (Material 3 navigation bar). 작업 carries a badge. */
@Composable
private fun TabBar(nav: NavHostController, tabs: List<TabSpec>, reselect: TabReselect, shell: ShellUiState, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val entry by nav.currentBackStackEntryAsState()
    val current = entry?.destination?.route
    val badge = shell.badge
    val reduce = LocalReduceMotion.current
    Column(modifier.background(c.bg).navigationBarsPadding()) {
        HorizontalDivider(color = c.line)
        Row(Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 10.dp)) {
            tabs.forEach { tab ->
                // a sub page (e.g. "connectors/open/{id}") keeps its tab lit
                val on = TabNav.isIn(current, tab.id)
                val label = tab.title.text()
                val tabBadge = if (tab.badge == TabBadgeKind.WORK) badge else null
                val badgeWords = tabBadge?.let { waitingWords(shell.status) }
                val pill by animateColorAsState(if (on) c.mine else c.mine.copy(alpha = 0f), Motion.small(reduce), label = "tabPill")
                Column(
                    Modifier
                        .weight(1f)
                        // no grey press box over the whole tab: the pill behind the icon is the only feedback
                        .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, role = Role.Tab) {
                            when (TabNav.onTap(current, tab.id)) {
                                TabNav.Tap.SWITCH -> nav.navigate(tab.id) {
                                    popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                                // the tab's own page under a sub route: drop the sub route, start fresh at the tab's root
                                TabNav.Tap.TO_ROOT -> nav.navigate(tab.id) {
                                    popUpTo(nav.graph.findStartDestination().id) { saveState = false }
                                    launchSingleTop = true
                                }
                                TabNav.Tap.RESET -> reselect.emit(tab.id)
                            }
                        }
                        .semantics { contentDescription = listOfNotNull(label, badgeWords).joinToString(", "); selected = on },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Box {
                        Box(Modifier.width(56.dp).height(30.dp).clip(RoundedCornerShape(15.dp)).background(pill), contentAlignment = Alignment.Center) {
                            Icon(tab.icon, contentDescription = null, tint = if (on) c.ink else c.muted, modifier = Modifier.size(22.dp))
                        }
                        if (tabBadge != null) TabBadge(tabBadge, Modifier.align(Alignment.TopEnd).offset(x = (-6).dp, y = (-2).dp))
                    }
                    Text(
                        label, color = if (on) c.ink else c.muted, style = MaterialTheme.typography.labelSmall.copy(fontSize = 12.sp),
                        fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.widthIn(max = 72.dp),
                    )
                }
            }
        }
    }
}

/** What the shared My screen takes from the phone's shell: the tab bar's re-select and height, hiding the bars, and the pages that still live in the app. */
@Composable
private fun rememberMyHooks(hiddenTabs: List<TabSpec>, openTab: (String) -> Unit): MyScreenHooks {
    val chrome = LocalShellChrome.current
    val taps = LocalTabReselect.current
    val container = LocalAppContainer.current
    val bottom = bottomBarHeight()
    val openTabNow by rememberUpdatedState(openTab)
    return remember(chrome, taps, bottom, hiddenTabs) {
        MyScreenHooks(
            reselect = taps.taps.filter { it == Routes.MY }.map { },
            bottomInset = { bottom },
            hideChrome = { key, hidden -> chrome.hide(key, hidden) },
            openConnectors = { openTabNow(Routes.CONNECTORS) },
            hiddenTabs = hiddenTabs,
            openTab = { id -> openTabNow(id) },
            keyguardPage = { onBack, startWithCode -> chat.kelo.app.keyguard.KeyguardPage(onBack, startWithCode = startWithCode) },
        )
    }
}

/** The work list in the side pane: its sub pages must not hide the bars (the chat stays beside it). */
private class DockedWorkHooks(private val base: chat.kelo.app.work.WorkScreenHooks) : chat.kelo.app.work.WorkScreenHooks by base {
    override val bottomInset: androidx.compose.ui.unit.Dp = 0.dp
    override fun hideChrome(key: String, hidden: Boolean) {}
}
