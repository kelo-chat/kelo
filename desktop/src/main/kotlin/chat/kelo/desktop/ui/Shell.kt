package chat.kelo.desktop.ui

import chat.kelo.app.core.AppVersion
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.AgentActivityRules
import chat.kelo.app.chat.AgentStatus
import chat.kelo.app.chat.BrowserTabsBrief
import chat.kelo.app.chat.TasksBrief
import chat.kelo.app.chat.WorkersBrief
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Profile
import chat.kelo.app.ui.character.KeloCharacter
import chat.kelo.app.ui.character.KeloMood
import chat.kelo.app.ui.character.centerShift
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.work.WorkBadge
import chat.kelo.desktop.chat.ChatModel
import chat.kelo.desktop.chat.ChatPane
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.LiveRequest
import chat.kelo.desktop.core.MY_PAGE_OPERATOR
import chat.kelo.desktop.core.MY_PAGE_SERVER_UPDATE
import chat.kelo.desktop.core.MY_PAGE_RESOURCES
import chat.kelo.desktop.core.MY_PAGE_CLEANUP
import chat.kelo.desktop.core.MY_PAGE_MODEL
import chat.kelo.desktop.core.MY_PAGE_AGENT
import chat.kelo.desktop.chat.AgentAvatar
import chat.kelo.desktop.core.MY_PAGE_WALLET
import chat.kelo.app.notify.OpenTarget
import chat.kelo.app.agent.ownTabId
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.ConnectorsPane
import chat.kelo.desktop.pages.GoalsPane
import chat.kelo.desktop.pages.LivePane
import chat.kelo.desktop.pages.MyPane
import chat.kelo.desktop.pages.WorkPane
import kotlinx.coroutines.delay

/** The signed-in app's pages, in the side rail's order. */
enum class Page(val label: Int, val icon: ImageVector) {
    CHAT(R.string.tab_chat, KeloIcons.Chat),
    GOALS(R.string.tab_goals, KeloIcons.Goal),
    WORK(R.string.tab_work, KeloIcons.Work),
    CONNECTORS(R.string.tab_connectors, KeloIcons.Plug),
    MY(R.string.tab_my, KeloIcons.User),
}

/** How far the floating header reaches down (the pages leave this much room at their top). */
val HeaderHeight: Dp = 104.dp
private val BADGE_CREAM = Color(0xFFFFF3D6)
private const val BADGE = 68

/**
 * The signed-in window: a side rail with the five pages (채팅, 다짐, 작업, 커넥터, 마이; 작업 carries a badge when something waits),
 * the page, and over it the floating header: Kelo's character (whose drawing follows what the agent is doing) with the agent's
 * name and, while it works, what it is doing. Clicking the character opens 작업.
 */
@Composable
fun Shell(app: AppContainer, chat: ChatModel, page: Page, onPage: (Page) -> Unit, onSignedOut: () -> Unit, connectorToOpen: String?, onConnectorOpened: () -> Unit) {
    val c = Kelo.colors
    val profile by app.profile.collectAsState()
    val p = profile ?: Profile()
    val status by app.activity.status.collectAsState()
    PollAgentStatus(app)
    // the agent's picture: the kept one at once, then in step with the profile's version (the status poll keeps it so after)
    LaunchedEffect(p.avatarVersion) {
        val avatar = AgentAvatar.of(app.sessions)
        avatar.warm()
        avatar.sync(p.avatarVersion)
    }
    // the app's own update check: at start, then once a day (checked every hour; Updater.checkIfDue)
    LaunchedEffect(app.updates) {
        while (true) {
            app.updates.checkIfDue()
            kotlinx.coroutines.delay(60 * 60 * 1000L)
        }
    }
    // the server's oldest supported desktop version (min_app_version.desktop): below it the required-update screen covers the window
    val minVersion = p.minAppVersion["desktop"]
    LaunchedEffect(minVersion) { app.updates.requireAtLeast(minVersion) }
    val updateRequired by app.updates.required.collectAsState()
    // 426 app_too_old from any call (its min_app is newer than this release): the same required-update screen
    val appTooOld by app.sessions.appTooOld.collectAsState()
    LaunchedEffect(appTooOld) { if (appTooOld) app.updates.requireNow() }
    // KE2E: the server's key changed (nothing more is sent until this device is connected again); the server is too old for
    // this app (no KE2E, or X-Kelo-Server below min_server: never plaintext, spec 5.18)
    val keyChanged by app.sessions.keyChanged.collectAsState()
    val serverOld by app.sessions.serverOld.collectAsState()
    val serverSeen by AppVersion.server.collectAsState()
    var myStart by remember { mutableStateOf<String?>(null) }
    // a notification, a card's button or the tray asked for a place (the phone's MainShell does the same with OpenTarget)
    val openRequest by app.openRequests.collectAsState()
    LaunchedEffect(openRequest) {
        val target = openRequest ?: return@LaunchedEffect
        app.openRequests.value = null
        when (target) {
            OpenTarget.Chat -> { app.liveTab.value = null; onPage(Page.CHAT) }
            // Kelo is locked (Keyguard): only an owner phone unlocks it; this computer shows the locked banner (spec 2.4: no key here)
            OpenTarget.Unlock -> { app.liveTab.value = null; app.sessions.setLocked(true); onPage(Page.CHAT) }
            is OpenTarget.ChatAt -> { app.liveTab.value = null; app.chatFocus.value = target.messageId; onPage(Page.CHAT) }
            is OpenTarget.Entry -> { app.liveTab.value = null; app.chatEntryFocus.value = target.requestId; onPage(Page.CHAT) }
            OpenTarget.Wallet -> { app.liveTab.value = null; app.myPageRequests.value = MY_PAGE_WALLET; onPage(Page.MY) }
            OpenTarget.Operator -> { app.liveTab.value = null; app.myPageRequests.value = MY_PAGE_OPERATOR; onPage(Page.MY) }
            OpenTarget.ServerUpdate -> { app.liveTab.value = null; app.myPageRequests.value = MY_PAGE_SERVER_UPDATE; onPage(Page.MY) }
            OpenTarget.Storage -> { app.liveTab.value = null; app.myPageRequests.value = MY_PAGE_RESOURCES; onPage(Page.MY) }
            OpenTarget.StorageCleanup -> { app.liveTab.value = null; app.myPageRequests.value = MY_PAGE_CLEANUP; onPage(Page.MY) }
            is OpenTarget.ModelSettings -> {   // a newer CLI notice: My → Model at that CLI's box ("model:<provider>")
                app.liveTab.value = null
                app.myPageRequests.value = if (target.provider.isBlank()) MY_PAGE_MODEL else "$MY_PAGE_MODEL:${target.provider}"
                onPage(Page.MY)
            }
            is OpenTarget.Tab -> app.liveTab.value = LiveRequest(target.id, target.control, target.picture)
            OpenTarget.OwnTab -> app.liveTab.value = LiveRequest(
                runCatching { ownTabId(app.sessions.requireApi()) }.getOrDefault("owner"),
            )
            is OpenTarget.Task, is OpenTarget.Schedule, OpenTarget.Approvals, OpenTarget.Activity, OpenTarget.Schedules, OpenTarget.Watchers -> {
                app.liveTab.value = null
                app.workRequests.value = target
                onPage(Page.WORK)
            }
        }
    }

    Box(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxSize().background(c.bg)) {
            Rail(page, WorkBadge.of(status), status, onPage)
            VerticalDivider(color = c.line)
            Box(Modifier.weight(1f).fillMaxHeight()) {
                when (page) {
                    Page.CHAT -> ChatPane(
                        chat, app.sessions, p, onProfile = app::setProfile,
                        onOpenConnector = { id -> app.openPage.value = "connector:$id" },
                        topInset = HeaderHeight,
                        onSolveCheck = { tab -> app.openRequests.value = OpenTarget.Tab(tab, control = true) },
                        onOpenTarget = { t -> app.openRequests.value = t },
                        focus = app.chatFocus,
                        entryFocus = app.chatEntryFocus,
                        onEntryClosed = { kind -> app.activity.entryClosed(kind) },
                        onOpenGoals = { onPage(Page.GOALS) },
                    )
                    Page.GOALS -> GoalsPane(app, topInset = HeaderHeight)
                    Page.WORK -> WorkPane(app, topInset = HeaderHeight)
                    Page.CONNECTORS -> ConnectorsPane(app, topInset = HeaderHeight, openId = connectorToOpen, onOpened = onConnectorOpened)
                    Page.MY -> MyPane(app, topInset = HeaderHeight, start = myStart, onStartUsed = { myStart = null }, onSignedOut = onSignedOut)
                }
                // as on the phone, the agent opens its own page (My → the agent: its name, picture and what it is doing)
                FloatingHeader(app, p, onAgent = { app.myPageRequests.value = MY_PAGE_AGENT; onPage(Page.MY) }, modifier = Modifier.align(Alignment.TopCenter))
                // a newer app version: a small pill once per version (My keeps the card and the download)
                UpdateBanner(app, onOpenMy = { onPage(Page.MY) }, modifier = Modifier.align(Alignment.BottomCenter))
                // Keyguard: the server holds no key ("Kelo is locked", 423 / agent status): unlocking happens on an owner phone only
                LockedBanner(app, modifier = Modifier.align(Alignment.TopCenter).padding(top = HeaderHeight + 8.dp, start = 12.dp, end = 12.dp))
                // a browser tab live over the page ("solve it" on a human check, from the chat or 작업)
                val liveTab by app.liveTab.collectAsState()
                liveTab?.let { r -> LivePane(app, r.id, takeControl = r.control, picture = r.picture, onClose = { app.liveTab.value = null }) }
            }
        }
        // the server needs a newer version (min_app_version.desktop): this covers everything until the app is updated
        if (updateRequired) UpdateRequiredScreen(app)
        else if (keyChanged) PairAgainScreen(onPairAgain = { app.sessions.pairAgain(); onSignedOut() })
        else if (serverOld || AppVersion.serverTooOld(serverSeen)) {
            ServerTooOldScreen(serverSeen, onRetry = { app.sessions.recheckServer() })
        }
    }
}

@Composable
private fun Rail(page: Page, badge: WorkBadge.Shown?, status: chat.kelo.app.chat.AgentStatus?, onPage: (Page) -> Unit) {
    val c = Kelo.colors
    Column(
        Modifier.width(92.dp).fillMaxHeight().background(c.bg).padding(top = 16.dp, bottom = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Page.entries.forEach { p ->
            val on = p == page
            val label = stringResource(p.label)
            val tabBadge = if (p == Page.WORK) badge else null
            val badgeWords = tabBadge?.let { waitingWords(status) }
            Column(
                Modifier.fillMaxWidth().padding(horizontal = 6.dp).clip(RoundedCornerShape(14.dp))
                    .clickable(role = Role.Tab) { onPage(p) }
                    .semantics { contentDescription = listOfNotNull(label, badgeWords).joinToString(", ") }
                    .padding(vertical = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Box {
                    Box(Modifier.width(56.dp).height(32.dp).clip(RoundedCornerShape(16.dp)).background(if (on) c.mine else Color.Transparent), contentAlignment = Alignment.Center) {
                        Icon(p.icon, contentDescription = null, tint = if (on) c.ink else c.muted, modifier = Modifier.size(22.dp))
                    }
                    // only while something waits for the owner (WorkBadge): how many, in a red pill
                    if (tabBadge != null) Text(
                        if (tabBadge.count > 99) stringResource(R.string.count_badge_max) else tabBadge.count.toString(),
                        color = c.onDanger, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold,
                        modifier = Modifier.align(Alignment.TopEnd).offset(x = 4.dp, y = (-4).dp).clip(RoundedCornerShape(99.dp)).background(c.danger).padding(horizontal = 5.dp),
                    )
                }
                // labels wrap (two lines at most) rather than being cut: "Connecteurs", "Конекторы"
                Text(label, color = if (on) c.ink else c.muted, style = MaterialTheme.typography.labelSmall, textAlign = TextAlign.Center,
                    fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal, maxLines = 2, overflow = TextOverflow.Ellipsis)
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

/** The agent at the top centre: the character (or its mood) on a cream disc, the name pill under it, a red dot while something waits for the owner. */
@Composable
private fun FloatingHeader(app: AppContainer, profile: Profile, onAgent: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val status by app.activity.status.collectAsState()
    val waiting = WorkBadge.of(status)
    val news = waiting != null
    val line = rememberAgentLine(app)
    val mood = rememberAgentMood(app)
    val name = profile.agentName.ifBlank { "Kelo" }
    val photo by AgentAvatar.of(app.sessions).image.collectAsState()
    val openLabel = stringResource(R.string.agent_open)
    val newsWords = waiting?.let { waitingWords(status) }
    val offlineWords = if (mood == KeloMood.Offline) stringResource(R.string.agent_offline) else null
    Column(modifier.padding(top = 6.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Column(
            Modifier.clip(RoundedCornerShape(20.dp)).clickable(role = Role.Button, onClickLabel = openLabel, onClick = onAgent)
                .semantics(mergeDescendants = true) { contentDescription = listOfNotNull(name, offlineWords, line, newsWords).joinToString(", ") }
                .padding(horizontal = 8.dp, vertical = 2.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Box(contentAlignment = Alignment.TopCenter) {
                Column(
                    Modifier.align(Alignment.BottomCenter).widthIn(max = 320.dp)
                        .shadow(3.dp, RoundedCornerShape(14.dp), ambientColor = c.scrim, spotColor = c.scrim)
                        .clip(RoundedCornerShape(14.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(14.dp))
                        .animateContentSize().padding(horizontal = 12.dp, vertical = 3.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(name, style = MaterialTheme.typography.titleSmall, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    val second = offlineWords ?: line
                    if (second != null) Text(second, style = MaterialTheme.typography.labelSmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Box(Modifier.padding(bottom = if (line != null || offlineWords != null) 36.dp else 21.dp)) {
                    // a photo gets a ring that turns green while the agent works; else Kelo's character on the cream disc
                    val pic = photo
                    val ring = if (pic != null && line != null) c.success else c.card
                    Box(
                        Modifier.size(BADGE.dp).shadow(6.dp, CircleShape, ambientColor = c.scrim, spotColor = c.scrim)
                            .clip(CircleShape).background(if (pic != null) c.card else BADGE_CREAM).border(2.dp, ring, CircleShape),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (pic != null) androidx.compose.foundation.Image(pic, contentDescription = null, contentScale = androidx.compose.ui.layout.ContentScale.Crop,
                            modifier = Modifier.size(BADGE.dp).clip(CircleShape))
                        else KeloCharacter(mood, Modifier.size((BADGE * 0.92f).dp).offset(x = (BADGE * 0.92f * mood.centerShift() / 120f).dp, y = 3.dp))
                    }
                    if (news) Box(Modifier.align(Alignment.TopEnd).offset(x = (-2).dp, y = 2.dp).size(12.dp).border(2.dp, c.bg, CircleShape).padding(2.dp).background(c.danger, CircleShape))
                }
            }
        }
    }
}

@Composable
private fun rememberAgentLine(app: AppContainer): String? {
    val following by app.activity.following.collectAsState()
    val turnLine by app.activity.turnLine.collectAsState()
    val polled by app.activity.polledLine.collectAsState()
    return AgentActivityRules.shown(following, turnLine, polled)
}

/** What the header's character shows (chat.kelo.app.chat.AgentActivityRules.mood, as on the phone). */
@Composable
fun rememberAgentMood(app: AppContainer): KeloMood {
    val a = app.activity
    val offline by a.offline.collectAsState()
    val status by a.status.collectAsState()
    val stuck by a.stuck.collectAsState()
    val listening by a.listening.collectAsState()
    val following by a.following.collectAsState()
    val turnKind by a.turnKind.collectAsState()
    val polledBrowsing by a.polledBrowsing.collectAsState()
    val doneAt by a.doneAt.collectAsState()
    var sinceDone by remember { mutableStateOf<Long?>(null) }
    LaunchedEffect(doneAt) {
        if (doneAt <= 0L) { sinceDone = null; return@LaunchedEffect }
        val since = System.nanoTime() / 1_000_000 - doneAt
        if (since >= AgentActivityRules.DONE_MS) { sinceDone = null; return@LaunchedEffect }
        sinceDone = since
        delay(AgentActivityRules.DONE_MS - since)
        sinceDone = null
    }
    // the window coming to the front, a reply, work, an approval or the microphone starting or ending counts as something happening
    val focused = androidx.compose.ui.platform.LocalWindowInfo.current.isWindowFocused
    val busy = status?.busy == true
    val asking = (status?.pendingApprovals ?: 0) > 0
    LaunchedEffect(focused, following, busy, asking, listening) { a.touch(System.nanoTime() / 1_000_000) }
    // at night the character falls asleep only after SLEEP_AFTER_IDLE_MS with nothing happening
    val activeAt by a.activeAt.collectAsState()
    var idle by remember { mutableStateOf<Long?>(null) }
    LaunchedEffect(activeAt) {
        val since = AgentActivityRules.idleMs(System.nanoTime() / 1_000_000, activeAt)
        idle = since
        if (since != null && since < AgentActivityRules.SLEEP_AFTER_IDLE_MS) {
            delay(AgentActivityRules.SLEEP_AFTER_IDLE_MS - since)
            idle = AgentActivityRules.SLEEP_AFTER_IDLE_MS
        }
    }
    // the night: outside the owner's contact hours when the server knows them, else 23:00–07:00
    val hours by a.sleepHours.collectAsState()
    var night by remember { mutableStateOf(false) }
    LaunchedEffect(hours) {
        while (true) {
            night = AgentActivityRules.nightAt(System.currentTimeMillis(), java.time.ZoneId.systemDefault(), hours)
            delay(60_000L)
        }
    }
    return AgentActivityRules.mood(AgentActivityRules.MoodInput(offline, status, stuck, listening, following, turnKind, polledBrowsing, sinceDone, night, idle))
}

/** GET /api/agent/status every few seconds (the header's line, its character and the 작업 badge read it), as on the phone. */
@Composable
private fun PollAgentStatus(app: AppContainer) {
    val activity = app.activity
    LaunchedEffect(Unit) {
        var tabsBefore = emptyMap<String, Double>()
        var before: AgentStatus? = null
        var failures = 0
        var round = 0
        while (true) {
            val api = app.sessions.api.value
            if (api != null) {
                try {
                    val s = api.get<AgentStatus>("/api/agent/status")
                    app.sessions.setLocked(s.locked)   // Keyguard: "Kelo is locked" comes and goes with the server's own word
                    failures = 0
                    activity.offline.value = false
                    activity.status.value = s
                    // work or a question waiting keeps the character up (it sleeps only after a quiet while)
                    if (s.busy || s.pendingApprovals > 0) activity.touch(System.nanoTime() / 1_000_000)
                    // the owner's contact hours decide the character's night; asked at start and about every five minutes
                    if (round % 100 == 0) runCatching { api.get<chat.kelo.app.chat.SleepHours>("/api/proactive") }.onSuccess { activity.sleepHours.value = it }
                    if (!activity.following.value) activity.polledLine.value = AgentActivityRules.line(s)
                    if (s.busy && !activity.following.value) {
                        runCatching { api.get<BrowserTabsBrief>("/api/browser/sessions?thumbnails=false") }.onSuccess { t ->
                            activity.polledBrowsing.value = AgentActivityRules.polledBrowsing(s, t.items, tabsBefore, System.currentTimeMillis() / 1000.0)
                            tabsBefore = t.items.mapNotNull { b -> b.lastActivity?.let { b.id to it } }.toMap()
                        }
                    } else {
                        activity.polledBrowsing.value = false
                        tabsBefore = emptyMap()
                    }
                    val ended = before?.busy == true && !s.busy
                    if (before == null || ended || before.unseenTasks != s.unseenTasks || round % 10 == 0) {
                        val newest = runCatching { api.get<TasksBrief>("/api/tasks?limit=1").items.firstOrNull() }.getOrNull()
                        val waiting = runCatching { api.get<WorkersBrief>("/api/workers?status=needs_input&limit=5").items }.getOrDefault(emptyList())
                        activity.stuck.value = AgentActivityRules.needsUser(newest, waiting, System.currentTimeMillis() / 1000.0)
                        if (AgentActivityRules.finished(before, s, newest) && !activity.following.value) activity.doneAt.value = System.nanoTime() / 1_000_000
                    }
                    before = s
                    round++
                    if (s.avatarVersion >= 0) runCatching { AgentAvatar.of(app.sessions).sync(s.avatarVersion) }
                    s.agentName?.takeIf { it.isNotBlank() && it != app.profile.value?.agentName }?.let { n ->
                        app.profile.value?.let { app.profile.value = it.copy(agentName = n) }
                    }
                    // the tone changed in the chat ("반말로 해 줘") or on another device: 마이 → 에이전트 shows it without a reload
                    app.profile.value?.let { p -> chat.kelo.app.my.ToneRules.synced(p, s.tone)?.let(app::setProfile) }
                } catch (e: ApiError) {
                    if (e.isNetwork) {
                        failures++
                        if (failures >= AgentActivityRules.OFFLINE_AFTER) activity.offline.value = true
                    }
                } catch (e: Exception) {
                    failures++
                    if (failures >= AgentActivityRules.OFFLINE_AFTER) activity.offline.value = true
                }
            }
            delay(AgentActivityRules.POLL_MS)
        }
    }
}
