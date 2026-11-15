package chat.kelo.app.connectors

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.foundation.layout.Box
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.shell.OnTabReselect
import chat.kelo.app.shell.Routes
import chat.kelo.app.shell.HideShellChrome
import chat.kelo.app.shell.LocalShellChrome
import chat.kelo.app.shell.bottomBarHeight
import chat.kelo.app.shell.headerHeight
import chat.kelo.app.ui.PushHost
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.components.BackTitleBar
import chat.kelo.app.ui.components.SkeletonBar
import chat.kelo.app.ui.components.StaleHint
import chat.kelo.app.ui.components.SkeletonLines
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.rememberSkeletonShown
import chat.kelo.app.ui.components.ChoiceChip
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.ripple
import chat.kelo.app.ui.components.Card
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch

/**
 * The 커넥터 tab: the services the agent can use, searchable and filterable by group; the connected ones pinned on top, the rest by
 * group, with a badge for each; tapping one opens its detail over the whole screen (the app's bars step aside) with the connect panel; the back
 * arrow or the phone's (predictive) back gesture returns to the list where it was. [openId] opens that connector straight away
 * (the chat's connect card). The list and the open panel are refreshed whenever the app comes back to the foreground, so a
 * sign-in finished in the browser shows up on return.
 */
@Composable
fun ConnectorsScreen(openId: String? = null) {
    val sessions = LocalAppContainer.current.sessions
    val vm: ConnectorsViewModel = viewModel { ConnectorsViewModel(sessions, openId) }
    val state by vm.state.collectAsStateWithLifecycle()
    val chrome = LocalShellChrome.current
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { vm.reload() }
    // kept here, so coming back from a detail finds the list where it was
    val listState = rememberLazyListState()
    val open: (String?) -> Unit = { id -> vm.open(id); chrome.hide(CHROME_KEY, id != null) }
    // tapping 커넥터 again: back to the list (from a detail), or to the top of the list
    OnTabReselect(Routes.CONNECTORS) {
        if (vm.state.value.openId != null) open(null) else listState.animateScrollToItem(0)
    }
    HideShellChrome(CHROME_KEY, state.openId != null)
    // Google Calendar's "휴대폰 캘린더로 연결하기": back to the list, down to 이 기기에서, and the calendar row's sheet opens
    var phoneCalendar by androidx.compose.runtime.saveable.rememberSaveable { mutableStateOf(false) }

    PushHost(
        state = state.openId.orEmpty(),
        depth = { if (it.isEmpty()) 0 else 1 },
        back = if (state.openId != null) "" else null,
        onBack = { open(null) },
        modifier = Modifier.fillMaxSize(),
    ) { id ->
        if (id.isEmpty()) {
            Box(Modifier.fillMaxSize()) {
                ConnectorList(state, vm, listState, headerHeight(), bottomBarHeight(), onOpen = { open(it) },
                    phoneCalendar = phoneCalendar, onPhoneCalendarShown = { phoneCalendar = false })
                // above the tab bar, where it covers no title or search field
                StaleHint(state.offline, Modifier.align(Alignment.BottomCenter).padding(bottom = bottomBarHeight() + 12.dp), onRetry = vm::reload)
            }
        } else {
            ConnectorDetail(ConnectorRules.find(state.list, id), state.creds, loading = !state.loaded, onBack = { open(null) }, onChanged = vm::reload,
                onPhoneCalendar = { vm.setQuery(""); vm.setFilter(null); open(null); phoneCalendar = true })
        }
    }
}

private const val CHROME_KEY = "connector-detail"

private val GroupShape = RoundedCornerShape(12.dp)
private val FirstShape = RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp)
private val LastShape = RoundedCornerShape(bottomStart = 12.dp, bottomEnd = 12.dp)

@Composable
private fun ConnectorList(
    state: ConnectorsState, vm: ConnectorsViewModel, listState: LazyListState, top: Dp, bottom: Dp, onOpen: (String) -> Unit,
    phoneCalendar: Boolean = false, onPhoneCalendarShown: () -> Unit = {},
) {
    val c = Kelo.colors
    // filtered and grouped only when the list, the search or the filter changes, not on every recomposition
    val shown = remember(state.list, state.query) { state.shown }
    // opening view: connected and popular only; a search covers everything; the full list (with group chips) on request
    val home = remember(state.list, state.creds) { ConnectorList.home(state.list, state.creds) }
    val full = state.showAll || home == null
    val searching = state.query.isNotBlank()
    val sections = remember(shown, state.creds, state.filter, full, searching, home) {
        when {
            searching -> ConnectorList.sections(shown, state.creds)
            !full -> home.orEmpty()
            else -> ConnectorList.sections(shown, state.creds, state.filter)
        }
    }
    val chips = remember(state.list) { ConnectorList.filters(state.list) }
    val hidden = remember(state.list, state.creds) { ConnectorList.hiddenCount(state.list, state.creds) }
    val skeleton = rememberSkeletonShown(!state.loaded)
    androidx.compose.runtime.LaunchedEffect(phoneCalendar, sections, skeleton) {
        // the "device" item comes after "top" and, per section, its head, its rows and its gap
        if (phoneCalendar && !skeleton) listState.animateScrollToItem(1 + sections.sumOf { it.items.size + 2 })
    }
    LazyColumn(
        Modifier.fillMaxSize().background(c.bg).imePadding(),
        state = listState,
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = top + 8.dp, bottom = bottom + 24.dp),
    ) {
        item(key = "top", contentType = "top") {
            KeloTextField(
                state.query, vm::setQuery, stringResource(R.string.connectors_search),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            )
            Spacer(Modifier.height(10.dp))
            if (full && !searching && home != null) {
                LinkButton(stringResource(R.string.connectors_back_to_popular), onClick = { vm.setShowAll(false) })
            }
            if (full && !searching && chips.size > 1) {
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ChoiceChip(stringResource(R.string.connectors_filter_all), selected = state.filter == null, onClick = { vm.setFilter(null) })
                    chips.forEach { ch ->
                        ChoiceChip(ch.label?.let { stringResource(it) } ?: ch.title, selected = state.filter == ch.key,
                            onClick = { vm.setFilter(if (state.filter == ch.key) null else ch.key) })
                    }
                }
            }
            Spacer(Modifier.height(14.dp))
        }
        if (skeleton) {
            item(key = "loading", contentType = "loading") {
                Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    SkeletonBar(width = 90.dp, height = 12.dp, modifier = Modifier.padding(start = 4.dp, top = 6.dp))
                    SkeletonRows(4)
                    SkeletonBar(width = 70.dp, height = 12.dp, modifier = Modifier.padding(start = 4.dp, top = 6.dp))
                    SkeletonRows(3)
                }
            }
            return@LazyColumn
        }
        sections.forEach { section ->
            val category = section.key
            val items = section.items
            item(key = "head:$category", contentType = "head") {
                Text(
                    section.label?.let { stringResource(it) } ?: section.title,
                    style = MaterialTheme.typography.titleSmall,
                    color = when (category) { "reconnect" -> c.danger; "connected", "popular" -> c.accent; else -> c.muted },
                    modifier = Modifier.padding(start = 4.dp, top = 6.dp, bottom = 8.dp),
                )
            }
            // one lazy item per connector (not one big item per group), so only the rows on screen are composed
            itemsIndexed(items, key = { _, x -> "row:${x.id}" }, contentType = { _, _ -> "row" }) { i, x ->
                val shape = when {
                    items.size == 1 -> GroupShape
                    i == 0 -> FirstShape
                    i == items.lastIndex -> LastShape
                    else -> RectangleShape
                }
                Column(Modifier.fillMaxWidth().background(c.card, shape)) {
                    if (i == 0) Spacer(Modifier.height(2.dp))
                    if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                    ConnectorRow(x, shape, ConnectorList.reconnect(x, state.creds) != null, onOpen)
                    if (i == items.lastIndex) Spacer(Modifier.height(2.dp))
                }
            }
            item(key = "gap:$category", contentType = "gap") { Spacer(Modifier.height(14.dp)) }
        }
        if (!searching && state.filter == null) {
            // what this phone itself can share (calendar, contacts, messages, dialer, notifications, call log, Health Connect): not
            // services to sign in to, one switch each; also found in 마이 → 권한
            item(key = "device", contentType = "device") {
                chat.kelo.app.device.DeviceSection(
                    request = if (phoneCalendar) chat.kelo.app.device.DeviceFeature.CALENDAR else null, onRequestHandled = onPhoneCalendarShown,
                )
                Spacer(Modifier.height(14.dp))
            }
        }
        if (!full && !searching && hidden > 0) {
            item(key = "all", contentType = "all") {
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button) { vm.setShowAll(true) }.padding(vertical = 14.dp),
                    horizontalArrangement = Arrangement.Center,
                ) {
                    Text(androidx.compose.ui.res.pluralStringResource(R.plurals.connectors_show_all, hidden, hidden), color = c.muted,
                        style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        if (state.loaded && shown.isEmpty()) {
            item(key = "empty") {
                if (state.failed) {
                    MutedText(stringResource(R.string.connect_failed))
                    Spacer(Modifier.height(10.dp))
                    SecondaryButton(stringResource(R.string.retry), onClick = vm::reload)
                } else {
                    MutedText(stringResource(R.string.connectors_empty))
                }
            }
        }
    }
}

@Composable
private fun ConnectorRow(x: Connector, shape: Shape, broken: Boolean, onOpen: (String) -> Unit) {
    val c = Kelo.colors
    val source = remember { MutableInteractionSource() }
    Row(
        Modifier
            .fillMaxWidth()
            
            .clip(shape)
            .clickable(interactionSource = source, indication = ripple()) { onOpen(x.id) }
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ConnectorTile(x, 40.dp)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(x.name, color = c.ink, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, maxLines = 1,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
            if (x.description.isNotBlank()) Text(
                x.description, color = c.muted, style = MaterialTheme.typography.bodySmall, maxLines = 2,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
            )
        }
        when {
            broken -> ReconnectBadge()
            ConnectorRules.pendingReview(x) -> ReviewPill()
            else -> StatusBadge(ConnectorRules.badge(x.status))
        }
    }
}

/**
 * One connector, full screen: a large tile with its name and state, what Kelo can do with it (the server's own words), how it is
 * connected, who is connected and with which permissions when that is known, a warning when it needs connecting again, then the
 * connect panel (connect, connect again, disconnect with a question first). [x] is null while the list is still loading.
 */
@Composable
private fun ConnectorDetail(
    x: Connector?, creds: List<chat.kelo.app.my.VaultEntry>, loading: Boolean, onBack: () -> Unit, onChanged: () -> Unit,
    onPhoneCalendar: () -> Unit = {},
) {
    val c = Kelo.colors
    Column(Modifier.fillMaxSize().background(c.bg).safeDrawingPadding()) {
        BackTitleBar(x?.name.orEmpty(), onBack)
        Column(
            Modifier
                .fillMaxWidth()
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(start = 20.dp, end = 20.dp, top = 10.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (x == null) {
                if (loading || rememberSkeletonShown(loading)) {
                    SkeletonBar(width = 64.dp, height = 64.dp)
                    SkeletonBar(fraction = 0.5f, height = 26.dp)
                    Card { SkeletonLines(4) }
                } else {
                    MutedText(stringResource(R.string.connector_gone))
                }
                return@Column
            }
            val broken = ConnectorList.reconnect(x, creds)
            val account = ConnectorList.account(x, creds)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                ConnectorTile(x, 64.dp)
                Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.weight(1f)) {
                    Text(chat.kelo.app.ui.components.TokenText.fit(x.name), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold,
                        color = c.ink, maxLines = 2, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                    when {
                        broken != null -> ReconnectBadge()
                        ConnectorRules.pendingReview(x) -> ReviewPill()
                        else -> StatusBadge(ConnectorRules.badge(x.status))
                    }
                }
            }
            if (broken != null) {
                val cred = creds.firstOrNull { it.name == x.item }
                Card {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        androidx.compose.material3.Icon(chat.kelo.app.ui.icons.KeloIcons.Alert, contentDescription = null, tint = c.danger, modifier = Modifier.size(20.dp))
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(stringResource(R.string.connector_reconnect_title), fontWeight = FontWeight.SemiBold, color = c.ink, style = MaterialTheme.typography.bodyLarge)
                            Text(stringResource(broken.text), color = c.ink, style = MaterialTheme.typography.bodySmall)
                            val detail = cred?.lastResultDetail.orEmpty()
                            if (broken == Reconnect.REFUSED && detail.isNotBlank()) MutedText(stringResource(R.string.connector_reconnect_detail, detail), small = true)
                        }
                    }
                }
            }
            if (x.description.isNotBlank()) {
                DetailSection(stringResource(R.string.connector_can_do)) {
                    Text(x.description, color = c.ink, style = MaterialTheme.typography.bodyLarge)
                }
            }
            val method = if (x.status == "connected") ConnectorList.methodOf(x) else ConnectorList.method(x.connect)
            DetailSection(stringResource(R.string.connector_facts)) {
                FactRow(stringResource(R.string.connector_method), method?.let { stringResource(it.label) } ?: stringResource(R.string.connector_method_none))
                if (account.isNotBlank()) FactRow(stringResource(R.string.connector_account), account)
                else if (x.status == "connected") FactRow(stringResource(R.string.connector_account), stringResource(R.string.connector_account_unknown))
                if (x.site.isNotBlank()) FactRow(stringResource(R.string.connector_site), ConnectorRules.host(x.site).ifBlank { x.site })
                if (x.connect == "device" && x.status == "connected") DeviceScopes(x.via.ifBlank { x.id }, x.account)
            }
            if (x.connect != null) {
                // needing a new connection: the panel offers connecting again (the stored one is replaced), and it can still go
                Card { ConnectPanel(x.id, connector = if (broken != null) x.copy(status = "available") else x, onChanged = onChanged, onPhoneCalendar = onPhoneCalendar) }
                if (broken != null && x.item.isNotBlank()) BrokenDisconnect(x, onChanged)
            }
            if (x.status == "coming_soon") MutedText(stringResource(R.string.connector_coming_soon_note), small = true)
            if (x.status == "unsupported") MutedText(stringResource(R.string.connector_unsupported_note), small = true)
        }
    }
}

/** 연결 끊기 for a connection that needs doing again (the panel then shows only how to connect). */
@Composable
private fun BrokenDisconnect(x: Connector, onChanged: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var asking by remember(x.id) { mutableStateOf(false) }
    chat.kelo.app.ui.components.LinkButton(stringResource(R.string.connector_disconnect), onClick = { asking = true }, danger = true)
    if (asking) {
        ConfirmDialog(
            stringResource(R.string.mcp_disconnect_confirm, x.name), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(sessions.requireApi(), x.item) }; onChanged() } },
            onDismiss = { asking = false },
        )
    }
}

@Composable
private fun DetailSection(title: String, content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall, color = Kelo.colors.muted, modifier = Modifier.padding(start = 2.dp))
        Card { content() }
    }
}

@Composable
private fun FactRow(label: String, value: String) {
    val c = Kelo.colors
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, color = c.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(0.35f))
        Text(chat.kelo.app.ui.components.TokenText.fit(value, 24), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(0.65f))
    }
}

/**
 * The permission a connected code-connected service was given (GET /api/oauth/providers: its `granted_scope` among its `scopes`).
 * Nothing when that choice is not known; [account] refetches after a new connection.
 */
@Composable
private fun DeviceScopes(provider: String, account: String) {
    val sessions = LocalAppContainer.current.sessions
    var granted by remember(provider) { mutableStateOf<OAuthScope?>(null) }
    androidx.compose.runtime.LaunchedEffect(provider, account) {
        runCatching { ConnectorCalls.oauthProviders(sessions.requireApi()) }.onSuccess { l -> granted = ConnectorRules.grantedScope(l.firstOrNull { it.id == provider }) }
    }
    val s = granted ?: return
    val c = Kelo.colors
    Text(stringResource(R.string.connector_permissions), color = c.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 4.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        androidx.compose.material3.Icon(chat.kelo.app.ui.icons.KeloIcons.Check, contentDescription = null, tint = c.accent, modifier = Modifier.size(16.dp).padding(top = 2.dp))
        Column(Modifier.weight(1f)) {
            Text(chat.kelo.app.ui.components.TokenText.fit(s.label.ifBlank { s.id }), color = c.ink, style = MaterialTheme.typography.bodyMedium)
            if (s.detail.isNotBlank()) Text(s.detail, color = c.muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}
