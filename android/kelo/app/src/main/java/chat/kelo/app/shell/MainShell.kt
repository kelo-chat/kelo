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
import chat.kelo.app.connectors.ConnectorsScreen
import chat.kelo.app.core.Profile
import chat.kelo.app.goals.GoalsScreen
import chat.kelo.app.my.MyScreen
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

/** Routes inside the signed-in app. */
object Routes {
    const val CHAT = "chat"
    const val WORK = "work"
    const val GOALS = "goals"
    const val CONNECTORS = "connectors"
    const val MY = "my"

    /** The Connectors tab opened at one connector ("connectors/" keeps the tab lit). */
    const val CONNECTOR_OPEN = "connectors/open/{id}"
    fun connectorOpen(id: String): String = "connectors/open/" + android.net.Uri.encode(id)
}

private data class Tab(val route: String, @StringRes val label: Int, val icon: ImageVector)

private val TABS = listOf(
    Tab(Routes.CHAT, R.string.tab_chat, KeloIcons.Chat),
    Tab(Routes.GOALS, R.string.tab_goals, KeloIcons.Goal),
    Tab(Routes.CONNECTORS, R.string.tab_connectors, KeloIcons.Plug),
    Tab(Routes.MY, R.string.tab_my, KeloIcons.User),
)

/**
 * The signed-in app: a top app bar (on 채팅 the agent's picture and name, with what it is doing as a second line while it works; on
 * the other tabs the tab's name and its page's actions), the page, and five tabs at the bottom. A page that wants the whole screen
 * (a sub page, a connector's detail) asks the bars to step aside ([HideShellChrome]). Tapping the tab you are already in takes
 * that tab back to its start. A browser tab's live view opens over everything (from a chat card or a notification).
 */
@Composable
fun MainShell(profile: Profile, onProfile: (Profile) -> Unit, onSignedOut: () -> Unit) {
    val nav = rememberNavController()
    val c = Kelo.colors
    val reselect = remember { TabReselect() }
    val chrome = remember { ShellChrome() }
    val slots = remember { ShellBarSlots() }
    val density = LocalDensity.current
    val reduce = LocalReduceMotion.current
    var headerH by remember { mutableStateOf(0.dp) }
    var barH by remember { mutableStateOf(0.dp) }
    val statusTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    val navBottom = WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding()
    val header = if (headerH > 0.dp) headerH else statusTop + 96.dp
    val bar = if (barH > 0.dp) barH else navBottom + BAR_ESTIMATE
    val container = LocalAppContainer.current
    pollAgentStatus(profile, onProfile)

    fun go(route: String) = nav.navigate(route) {
        popUpTo(nav.graph.findStartDestination().id) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }

    // something shared from another app: the chat takes it
    val shared by container.shareInbox.pending.collectAsStateWithLifecycle()
    LaunchedEffect(shared != null) {
        if (shared != null && nav.currentDestination?.route != Routes.CHAT) go(Routes.CHAT)
    }

    // a browser tab live over everything (back returns to where it was opened from)
    var liveTab by rememberSaveable { mutableStateOf<String?>(null) }
    var liveControl by rememberSaveable { mutableStateOf(false) }   // opened to solve a human check: take the tab over at once
    var livePicture by remember { mutableStateOf<String?>(null) }   // the chat card's last picture, for a tab that has closed
    HideShellChrome(LIVE_CHROME_KEY, liveTab != null)
    // the agent's work (what it does, asks, has planned), opened from the header over whatever tab is showing
    var workOpen by rememberSaveable { mutableStateOf(false) }
    HideShellChrome(WORK_CHROME_KEY, workOpen)
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
            OpenTarget.Wallet -> { liveTab = null; container.myPageRequests.value = MY_PAGE_WALLET; go(Routes.MY) }
            OpenTarget.Operator -> { liveTab = null; container.myPageRequests.value = MY_PAGE_OPERATOR; go(Routes.MY) }
            OpenTarget.ServerUpdate -> { liveTab = null; container.myPageRequests.value = MY_PAGE_SERVER_UPDATE; go(Routes.MY) }
            OpenTarget.Unlock -> { liveTab = null; workOpen = false; container.sessions.setLocked(true); unlockNow = true; go(Routes.CHAT) }
            OpenTarget.Storage -> { liveTab = null; container.myPageRequests.value = MY_PAGE_RESOURCES; go(Routes.MY) }
            OpenTarget.StorageCleanup -> { liveTab = null; container.myPageRequests.value = MY_PAGE_CLEANUP; go(Routes.MY) }
            is OpenTarget.ModelSettings -> { liveTab = null; container.myPageRequests.value = myPageModel(target.provider); go(Routes.MY) }
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
            LocalTabReselect provides reselect,
            LocalShellChrome provides chrome,
            LocalShellBarSlots provides slots,
            chat.kelo.app.keyguard.LocalKeyguardEnableHost provides keyguardEnable,
        ) {
            NavHost(
                nav, startDestination = Routes.CHAT,
                modifier = Modifier.fillMaxSize(),
                enterTransition = { EnterTransition.None }, exitTransition = { ExitTransition.None },
                popEnterTransition = { EnterTransition.None }, popExitTransition = { ExitTransition.None },
            ) {
                composable(Routes.CHAT) {
                    ChatScreen(profile = profile, onProfile = onProfile, onOpenConnector = { id ->
                        nav.navigate(Routes.connectorOpen(id)) {
                            popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                            launchSingleTop = true
                        }
                    }, onOpenBrowser = { card -> container.openRequests.value = OpenTarget.Tab.ofCard(card) },
                        onOpenCardForm = { container.myPageRequests.value = MY_PAGE_WALLET_ADD; go(Routes.MY) },
                        onOpenGoals = { go(Routes.GOALS) })
                }
                composable(Routes.GOALS) { GoalsScreen() }
                composable(Routes.CONNECTORS) { ConnectorsScreen() }
                composable(Routes.CONNECTOR_OPEN) { entry -> ConnectorsScreen(openId = entry.arguments?.getString("id")) }
                composable(Routes.MY) {
                    MyScreen(profile = profile, onProfile = onProfile, onSignedOut = onSignedOut, onOpenTab = { route -> go(route) })
                }
            }
        }
        val entry by nav.currentBackStackEntryAsState()
        val route = entry?.destination?.route
        // the clock and the icons keep a strip of their own: the page scrolls under it and fades out before it, never behind the text
        if (!chrome.headerHidden) StatusBarScrim(statusTop, Modifier.align(Alignment.TopCenter))
        AnimatedVisibility(
            visible = !chrome.headerHidden, modifier = Modifier.align(Alignment.TopCenter),
            enter = fadeIn(Motion.small(reduce)), exit = fadeOut(Motion.small(reduce)),
        ) {
            FloatingHeader(
                profile,
                onAgent = { workOpen = true },
                modifier = Modifier.onSizeChanged { headerH = with(density) { it.height.toDp() } },
            )
        }
        AnimatedVisibility(
            visible = !chrome.barHidden, modifier = Modifier.align(Alignment.BottomCenter),
            enter = fadeIn(Motion.small(reduce)) + slideInVertically(Motion.enter(reduce)) { it / 2 },
            exit = fadeOut(Motion.small(reduce)) + slideOutVertically(Motion.exit(reduce)) { it / 2 },
        ) {
            TabBar(nav, reselect, Modifier.onSizeChanged { barH = with(density) { it.height.toDp() } })
        }
        // a new server has no model yet (the terminal setup does not ask for one): the chat leads to the model settings
        if (ModelGate.needsModel(profile) && route == Routes.CHAT && !workOpen && liveTab == null) {
            ChooseModelCard(
                onChoose = { container.myPageRequests.value = MY_PAGE_MODEL; go(Routes.MY) },
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
        val offered = update.offer
        val modelCard = ModelGate.needsModel(profile) && route == Routes.CHAT
        if (offered != null && update.showBanner && route != Routes.MY && !workOpen && liveTab == null && !chrome.headerHidden && !modelCard) {
            chat.kelo.app.update.UpdateBanner(
                update,
                onGet = { container.updater.get(); go(Routes.MY) },
                onInstall = { container.updater.bannerSeen(); container.updater.install() },
                onClose = { container.updater.bannerSeen() },
                modifier = Modifier.align(Alignment.TopCenter).padding(top = header + 8.dp, start = 12.dp, end = 12.dp),
            )
        }
        if (update.needsPermission) {
            chat.kelo.app.update.InstallPermissionDialog(onOpen = { container.updater.openInstallSetting() }, onDismiss = { container.updater.dismissPermission() })
        }
        if (workOpen) {
            androidx.activity.compose.BackHandler { workOpen = false }   // the work page's own sub pages step back first
            Box(Modifier.fillMaxSize().background(c.bg)) {
                CompositionLocalProvider(
                    LocalHeaderHeight provides 0.dp,
                    LocalBottomBarHeight provides WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding(),
                ) {
                    WorkScreen(start = workStart, onStartUsed = { workStart = null }, onOpenLive = { liveControl = false; livePicture = null; liveTab = it }, onClose = { workOpen = false })
                }
            }
        }
        AnimatedVisibility(
            visible = liveTab != null,
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
        chat.kelo.app.keyguard.KeyguardEnableOverlay(keyguardEnable)
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
                onRecover = { container.myPageRequests.value = MY_PAGE_KEYGUARD_CODE; go(Routes.MY) },
                autoStart = unlockNow, onAutoStarted = { unlockNow = false },
                modifier = Modifier.align(Alignment.TopCenter).padding(top = header + 8.dp, start = 12.dp, end = 12.dp).widthIn(max = 560.dp),
            )
        }
        // the server is older than this build needs (X-Kelo-Server, or no KE2E at all): nothing is sent until it is updated (5.18)
        val serverSeen by chat.kelo.app.core.AppVersion.server.collectAsStateWithLifecycle()
        val serverOld by container.sessions.serverOld.collectAsStateWithLifecycle()
        if (serverOld || chat.kelo.app.core.AppVersion.serverTooOld(serverSeen)) {
            ServerTooOldScreen(serverSeen, onRetry = { container.sessions.recheckServer() })
        }
        // the server needs a newer build (min_app_version): this covers everything until the app is updated
        if (update.required) {
            chat.kelo.app.update.UpdateRequiredScreen(update, onGet = { container.updater.get() }, onInstall = { container.updater.install() })
        }
    }
}

private const val LIVE_CHROME_KEY = "live-view"
private const val WORK_CHROME_KEY = "work-page"

/** What [AppContainer.myPageRequests] carries to open the wallet page. */
const val MY_PAGE_WALLET = "wallet"
/** ... at its add-card form (a card number the chat held back; chat/SecretGuard.kt) */
const val MY_PAGE_WALLET_ADD = "wallet:add"
/** 마이 → 운영자 (a beta request notification) */
const val MY_PAGE_OPERATOR = "operator"
/** ... and 서버 업데이트 (a server update notification; owner only) */
const val MY_PAGE_SERVER_UPDATE = "server-update"
/** ... and Security → the phone key (Keyguard); [MY_PAGE_KEYGUARD_CODE] opens it at the recovery code */
const val MY_PAGE_KEYGUARD = "keyguard"
const val MY_PAGE_KEYGUARD_CODE = "keyguard:code"
/** 마이 → Kelo 서버 자원 (a storage warning was tapped) */
const val MY_PAGE_RESOURCES = "resources"
/** 마이 → Kelo 서버 자원 → 정리하기 (the storage warning's 정리하기 action) */
const val MY_PAGE_CLEANUP = "cleanup"
/** ... and the agent's own page (its picture and name). */
const val MY_PAGE_AGENT = "agent"
/** ... and the model settings (the first-run card's button). */
const val MY_PAGE_MODEL = "model"

/** 마이 → 모델 opened at one CLI's box ("model:<provider>"; a newer CLI notice); a blank provider is just the page. */
fun myPageModel(provider: String): String = if (provider.isBlank()) MY_PAGE_MODEL else "$MY_PAGE_MODEL:$provider"

/** Until the tab bar has been measured once. */
private val BAR_ESTIMATE = 80.dp
/** The agent's picture in the floating header */
private const val HEADER_AVATAR = 44
/** The header's round badge (photo or character) */
private const val HEADER_BADGE = 68
private val BADGE_CREAM = androidx.compose.ui.graphics.Color(0xFFFFF3D6)
/** Kelo's character in the floating header (it has no circle around it, so it gets a little more room than a photo) */
private const val HEADER_CHARACTER = 56
/** How far the header's soft fade reaches below the name */
private val HEADER_FADE = 20.dp

/**
 * The floating header of the main tabs: the agent at the top centre and its name in a small pill below, with nothing behind them
 * (the page scrolls underneath). Without a photo the agent is Kelo's character, whose drawing follows what the agent is doing
 * ([AgentActivityRules.mood]: offline, asking, stuck, listening, browsing, reading, working, thinking, done, asleep or resting); a photo gets a ring that turns green while the agent works. What it is doing is on its page and
 * read out by TalkBack; a small red dot means something waits for the owner (WorkBadge). Tapping it opens the agent's page.
 */
@Composable
private fun FloatingHeader(profile: Profile, onAgent: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    val container = LocalAppContainer.current
    val image by container.agentAvatar.image.collectAsStateWithLifecycle()
    val status by container.agentActivity.status.collectAsStateWithLifecycle()
    val waiting = WorkBadge.of(status)
    val news = waiting != null
    val line = rememberAgentLine()
    val openLabel = stringResource(R.string.agent_open)
    val newsWords = waiting?.let { waitingWords(status) }
    val mood = rememberAgentMood()
    val offlineWords = if (mood == KeloMood.Offline) stringResource(R.string.agent_offline) else null
    // nothing behind the picture and the name: the page shows through everywhere else
    Box(modifier.fillMaxWidth()) {
        Column(
            Modifier.fillMaxWidth().statusBarsPadding().padding(top = 6.dp, bottom = 6.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Column(
                Modifier.clip(RoundedCornerShape(20.dp))
                    .clickable(role = Role.Button, onClickLabel = openLabel, onClick = onAgent)
                    .semantics(mergeDescendants = true) { contentDescription = listOfNotNull(profile.agentName, offlineWords, line, newsWords).joinToString(", ") }
                    .padding(horizontal = 8.dp, vertical = 2.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                // the name pill sits on the lower edge of the picture, so the two read as one
                val photo = image
                Box(contentAlignment = Alignment.TopCenter) {
                    Column(
                        Modifier.align(Alignment.BottomCenter).widthIn(max = 260.dp)
                            .shadow(3.dp, RoundedCornerShape(14.dp), ambientColor = c.scrim, spotColor = c.scrim)
                            .clip(RoundedCornerShape(14.dp))
                            .background(c.card).border(1.dp, c.line, RoundedCornerShape(14.dp))
                            .animateContentSize(Motion.small(reduce))
                            .padding(horizontal = 12.dp, vertical = 3.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(profile.agentName, style = MaterialTheme.typography.titleSmall, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    // one round badge for either a photo or the character (on a card-coloured disc), so both sit centred the same way
                    Box(Modifier.padding(bottom = 21.dp)) {   // the disc covers only the name tag's top edge
                        val ring = if (photo != null && line != null) c.success else c.card
                        Box(
                            Modifier.size(HEADER_BADGE.dp)
                                .shadow(6.dp, CircleShape, ambientColor = c.scrim, spotColor = c.scrim)
                                // the character sits on the launcher icon's cream (light in both themes, like a sticker); a photo fills it
                                .clip(CircleShape).background(if (photo != null) c.card else BADGE_CREAM).border(2.dp, ring, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            if (photo != null) AgentPicture(profile.agentName, photo, HEADER_BADGE)
                            else KeloCharacter(mood, Modifier.size((HEADER_BADGE * 0.92f).dp).offset(x = (HEADER_BADGE * 0.92f * mood.centerShift() / 120f).dp, y = 3.dp))
                        }
                        if (news) NewsDot(Modifier.align(Alignment.TopEnd).offset(x = (-2).dp, y = 2.dp))
                    }
                }
            }
        }
    }
}

/**
 * The page's own colour under the status bar, with a short fade below it: text that scrolls up goes out of sight before it reaches
 * the clock (the floating agent stays on top of it, light and dark alike).
 */
@Composable
private fun StatusBarScrim(height: androidx.compose.ui.unit.Dp, modifier: Modifier = Modifier) {
    if (height <= 0.dp) return
    val bg = Kelo.colors.bg
    Column(modifier.fillMaxWidth()) {
        Box(Modifier.fillMaxWidth().height(height).background(bg))
        Box(Modifier.fillMaxWidth().height(STATUS_FADE.dp).background(Brush.verticalGradient(listOf(bg, bg.copy(alpha = 0f)))))
    }
}

private const val STATUS_FADE = 10

/** The red dot on the header's agent: something waits for the owner ([WorkBadge]). */
@Composable
private fun NewsDot(modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Box(modifier.size(12.dp).border(2.dp, c.bg, CircleShape).padding(2.dp).background(c.danger, CircleShape))
}

/** GET /api/agent/status every few seconds while the app is in the foreground (the bar's line and the 작업 badge read it). */
@Composable
private fun pollAgentStatus(profile: Profile, onProfile: (Profile) -> Unit) {
    val container = LocalAppContainer.current
    val shownProfile by androidx.compose.runtime.rememberUpdatedState(profile)
    val setProfile by androidx.compose.runtime.rememberUpdatedState(onProfile)
    val activity = container.agentActivity
    val lifecycle = androidx.lifecycle.compose.LocalLifecycleOwner.current.lifecycle
    val appContext = androidx.compose.ui.platform.LocalContext.current.applicationContext
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(androidx.lifecycle.Lifecycle.State.STARTED) {
            // the owner opened the app: the character is awake
            activity.touch(android.os.SystemClock.elapsedRealtime())
            // the phone's location for the weather and "near me" questions: only when allowed, only now that the app is open
            container.sessions.api.value?.let { api ->
                launch { runCatching { chat.kelo.app.device.PhonePlace.maybeSend(appContext, api) } }
            }
            // each working tab's last action at the previous poll (whether it moved since)
            var tabsBefore = emptyMap<String, Double>()
            var before: chat.kelo.app.chat.AgentStatus? = null
            var failures = 0
            var round = 0
            while (true) {
                val api = container.sessions.api.value
                if (api != null) {
                    try {
                        val s = api.get<chat.kelo.app.chat.AgentStatus>("/api/agent/status")
                        container.sessions.setLocked(s.locked)   // Keyguard: "Kelo is locked" comes and goes with the server's own word
                        failures = 0
                        activity.offline.value = false
                        activity.status.value = s
                        // work or a question waiting keeps the character up (it sleeps only after a quiet while)
                        if (s.busy || s.pendingApprovals > 0) activity.touch(android.os.SystemClock.elapsedRealtime())
                        // the owner's contact hours decide the character's night; asked when the app comes up and every few minutes
                        if (round % HOURS_EVERY == 0) {
                            runCatching { api.get<chat.kelo.app.chat.SleepHours>("/api/proactive") }.onSuccess { activity.sleepHours.value = it }
                        }
                        if (!activity.following.value) activity.polledLine.value = AgentActivityRules.line(s)
                        // a run the chat is not following: is it in the browser? (asked only while the agent is busy)
                        if (s.busy && !activity.following.value) {
                            runCatching { api.get<chat.kelo.app.chat.BrowserTabsBrief>("/api/browser/sessions?thumbnails=false") }.onSuccess { t ->
                                activity.polledBrowsing.value =
                                    AgentActivityRules.polledBrowsing(s, t.items, tabsBefore, System.currentTimeMillis() / 1000.0)
                                tabsBefore = t.items.mapNotNull { b -> b.lastActivity?.let { b.id to it } }.toMap()
                            }
                        } else {
                            activity.polledBrowsing.value = false
                            tabsBefore = emptyMap()
                        }
                        // is a job stuck on the user, or did work just finish? Asked on the first poll, when a run ends, when
                        // unseen tasks change, and every half minute; two small requests.
                        val ended = before?.busy == true && !s.busy
                        if (before == null || ended || before.unseenTasks != s.unseenTasks || round % STUCK_EVERY == 0) {
                            val newest = runCatching { api.get<chat.kelo.app.chat.TasksBrief>("/api/tasks?limit=1").items.firstOrNull() }.getOrNull()
                            val waiting = runCatching { api.get<chat.kelo.app.chat.WorkersBrief>("/api/workers?status=needs_input&limit=5").items }
                                .getOrDefault(emptyList())
                            activity.stuck.value = AgentActivityRules.needsUser(newest, waiting, System.currentTimeMillis() / 1000.0)
                            if (AgentActivityRules.finished(before, s, newest) && !activity.following.value) {
                                activity.doneAt.value = android.os.SystemClock.elapsedRealtime()
                            }
                        }
                        before = s
                        round++
                        // the picture changed (on another phone, or here)
                        if (s.avatarVersion >= 0) container.agentAvatar.sync(s.avatarVersion)
                        // the tone changed in the chat ("반말로 해 줘") or on another device: 마이 → 에이전트 shows it without a reload
                        chat.kelo.app.my.ToneRules.synced(shownProfile, s.tone)?.let(setProfile)
                    } catch (e: chat.kelo.app.core.ApiError) {
                        if (e.isNetwork) {
                            failures++
                            if (failures >= AgentActivityRules.OFFLINE_AFTER) activity.offline.value = true
                        }
                        if (e.status == 404) { activity.polledLine.value = null; break }
                    }
                }
                kotlinx.coroutines.delay(AgentActivityRules.POLL_MS)
            }
        }
    }
}

/** How often (in polls) the stuck-job check runs on its own. */
private const val STUCK_EVERY = 10
/** How often (in polls, about five minutes) the contact hours are asked for again. */
private const val HOURS_EVERY = 100

/** The bar's second line: the followed reply's own while the chat follows one, else the polled one. */
@Composable
private fun rememberAgentLine(): String? {
    val activity = LocalAppContainer.current.agentActivity
    val following by activity.following.collectAsStateWithLifecycle()
    val turnLine by activity.turnLine.collectAsStateWithLifecycle()
    val polled by activity.polledLine.collectAsStateWithLifecycle()
    return AgentActivityRules.shown(following, turnLine, polled)
}

/** What the header's character shows ([AgentActivityRules.mood]). */
@Composable
private fun rememberAgentMood(): KeloMood {
    val activity = LocalAppContainer.current.agentActivity
    val offline by activity.offline.collectAsStateWithLifecycle()
    val status by activity.status.collectAsStateWithLifecycle()
    val stuck by activity.stuck.collectAsStateWithLifecycle()
    val listening by activity.listening.collectAsStateWithLifecycle()
    val following by activity.following.collectAsStateWithLifecycle()
    val turnKind by activity.turnKind.collectAsStateWithLifecycle()
    val polledBrowsing by activity.polledBrowsing.collectAsStateWithLifecycle()
    val doneAt by activity.doneAt.collectAsStateWithLifecycle()
    // the cheer ends on its own; the night is checked every minute
    var sinceDone by remember { mutableStateOf<Long?>(null) }
    LaunchedEffect(doneAt) {
        if (doneAt <= 0L) { sinceDone = null; return@LaunchedEffect }
        val since = android.os.SystemClock.elapsedRealtime() - doneAt
        if (since >= AgentActivityRules.DONE_MS) { sinceDone = null; return@LaunchedEffect }
        sinceDone = since
        kotlinx.coroutines.delay(AgentActivityRules.DONE_MS - since)
        sinceDone = null
    }
    // a reply, work, an approval or the microphone starting or ending counts as something happening (and so does showing up)
    val busy = status?.busy == true
    val asking = (status?.pendingApprovals ?: 0) > 0
    LaunchedEffect(following, busy, asking, listening) { activity.touch(android.os.SystemClock.elapsedRealtime()) }
    // at night the character falls asleep only after SLEEP_AFTER_IDLE_MS with nothing happening
    val activeAt by activity.activeAt.collectAsStateWithLifecycle()
    var idle by remember { mutableStateOf<Long?>(null) }
    LaunchedEffect(activeAt) {
        val since = AgentActivityRules.idleMs(android.os.SystemClock.elapsedRealtime(), activeAt)
        idle = since
        if (since != null && since < AgentActivityRules.SLEEP_AFTER_IDLE_MS) {
            kotlinx.coroutines.delay(AgentActivityRules.SLEEP_AFTER_IDLE_MS - since)
            idle = AgentActivityRules.SLEEP_AFTER_IDLE_MS
        }
    }
    // the night: outside the owner's contact hours when the server knows them, else 23:00–07:00
    val hours by activity.sleepHours.collectAsStateWithLifecycle()
    var night by remember { mutableStateOf(false) }
    LaunchedEffect(hours) {
        while (true) {
            night = AgentActivityRules.nightAt(System.currentTimeMillis(), java.time.ZoneId.systemDefault(), hours)
            kotlinx.coroutines.delay(60_000L)
        }
    }
    return AgentActivityRules.mood(
        AgentActivityRules.MoodInput(offline, status, stuck, listening, following, turnKind, polledBrowsing, sinceDone, night, idle),
    )
}

/** Five destinations; the chosen one sits in a tinted pill (Material 3 navigation bar). 작업 carries a badge. */
@Composable
private fun TabBar(nav: NavHostController, reselect: TabReselect, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val entry by nav.currentBackStackEntryAsState()
    val current = entry?.destination?.route
    val status by LocalAppContainer.current.agentActivity.status.collectAsStateWithLifecycle()
    val badge = WorkBadge.of(status)
    val reduce = LocalReduceMotion.current
    Column(modifier.background(c.bg).navigationBarsPadding()) {
        HorizontalDivider(color = c.line)
        Row(Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 10.dp)) {
            TABS.forEach { tab ->
                // a sub page (e.g. "connectors/open/{id}") keeps its tab lit
                val on = TabNav.isIn(current, tab.route)
                val label = stringResource(tab.label)
                val tabBadge = if (tab.route == Routes.WORK) badge else null
                val badgeWords = tabBadge?.let { waitingWords(status) }
                val pill by animateColorAsState(if (on) c.mine else c.mine.copy(alpha = 0f), Motion.small(reduce), label = "tabPill")
                Column(
                    Modifier
                        .weight(1f)
                        // no grey press box over the whole tab: the pill behind the icon is the only feedback
                        .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, role = Role.Tab) {
                            when (TabNav.onTap(current, tab.route)) {
                                TabNav.Tap.SWITCH -> nav.navigate(tab.route) {
                                    popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                                // the tab's own page under a sub route: drop the sub route, start fresh at the tab's root
                                TabNav.Tap.TO_ROOT -> nav.navigate(tab.route) {
                                    popUpTo(nav.graph.findStartDestination().id) { saveState = false }
                                    launchSingleTop = true
                                }
                                TabNav.Tap.RESET -> reselect.emit(tab.route)
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

/** What waits, for a screen reader: "입력을 기다리는 키 1개, 답을 기다리는 질문 2개" (by kind; [WorkBadge.parts]). */
@Composable
internal fun waitingWords(status: chat.kelo.app.chat.AgentStatus?): String =
    WorkBadge.parts(status).map { p ->
        val res = when (p.kind) {
            WorkBadge.APPROVALS -> R.plurals.work_badge_approvals
            WorkBadge.KEYS -> R.plurals.work_badge_keys
            WorkBadge.LOGINS -> R.plurals.work_badge_logins
            WorkBadge.CARDS -> R.plurals.work_badge_cards
            WorkBadge.PINS -> R.plurals.work_badge_pins
            else -> R.plurals.work_badge_waiting
        }
        pluralStringResource(res, p.count, p.count)
    }.joinToString(", ")

/** How many things wait for the owner, in a small red pill. */
@Composable
private fun TabBadge(b: WorkBadge.Shown, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Text(
        if (b.count > 99) stringResource(R.string.count_badge_max) else b.count.toString(),
        color = c.onDanger, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold,
        modifier = modifier.clip(RoundedCornerShape(99.dp)).background(c.danger).padding(horizontal = 5.dp, vertical = 0.dp),
    )
}
