package chat.kelo.desktop.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.connectors.Connector
import chat.kelo.app.connectors.ConnectorCalls
import chat.kelo.app.connectors.ConnectorList
import chat.kelo.app.connectors.ConnectorRules
import chat.kelo.app.connectors.OAuthScope
import chat.kelo.app.connectors.Reconnect
import chat.kelo.app.core.ApiError
import chat.kelo.app.my.VaultEntry
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.connectors.ConfirmAsk
import chat.kelo.desktop.pages.connectors.ConnectPanel
import chat.kelo.desktop.pages.connectors.ConnectorTile
import chat.kelo.desktop.pages.connectors.ReconnectBadge
import chat.kelo.desktop.pages.connectors.ReloadOnReturn
import chat.kelo.desktop.pages.connectors.ReviewPill
import chat.kelo.desktop.pages.connectors.StatusBadge
import chat.kelo.desktop.ui.Card
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PageTitle
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch

/**
 * 커넥터, as on the phone (android/kelo .../connectors/ConnectorsScreen.kt): the user's own connections pinned on top (those to
 * connect again first), then the popular services; a search covers everything, and "show all" lists every service by group with
 * filter chips. A click opens the connector beside the list (or over it in a narrow window): what Kelo can do with it, how it is
 * connected and who is, a warning when it needs connecting again, and the connect panel for its way (a code, a sign-in on the
 * service's page, a mailbox, a key, a site login, Google). The list reloads whenever a Kelo window becomes active again, so a
 * sign-in finished in the browser shows up on return. (The phone's own features — its calendar, contacts, messages — are not here:
 * a computer has none of them to share.)
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ConnectorsPane(app: AppContainer, topInset: Dp, openId: String? = null, onOpened: () -> Unit = {}) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var list by remember { mutableStateOf<List<Connector>?>(null) }
    var creds by remember { mutableStateOf<List<VaultEntry>>(emptyList()) }
    var failed by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    var filter by remember { mutableStateOf<String?>(null) }
    var showAll by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<String?>(null) }

    suspend fun load() {
        try {
            val api = app.sessions.requireApi()
            coroutineScope {
                val cr = async { runCatching { api.get<List<VaultEntry>>("/api/credentials") }.getOrNull() }
                val l = ConnectorCalls.list(api)
                list = l
                cr.await()?.let { creds = it }
                failed = false
                if (selected != null && l.none { it.id == selected }) selected = null
            }
        } catch (e: ApiError) {
            failed = list.isNullOrEmpty()
            if (list == null) list = emptyList()
        }
    }
    ReloadOnReturn(Unit) { load() }
    LaunchedEffect(openId, list) {
        val id = openId ?: return@LaunchedEffect
        val l = list ?: return@LaunchedEffect
        selected = l.firstOrNull { it.id == id || it.name.equals(id, ignoreCase = true) || it.item == id }?.id ?: id
        onOpened()
    }
    val reload: () -> Unit = { scope.launch { load() } }

    BoxWithConstraints(Modifier.fillMaxSize().padding(top = topInset)) {
        val wide = maxWidth > 880.dp
        Row(Modifier.fillMaxSize()) {
            val sel = selected
            if (!wide && sel != null) {
                ConnectorDetail(app, list?.let { ConnectorRules.find(it, sel) }, creds, loading = list == null, onBack = { selected = null }, onChanged = reload,
                    modifier = Modifier.weight(1f))
                return@Row
            }
            Box(Modifier.weight(1f).fillMaxHeight(), contentAlignment = Alignment.TopCenter) {
                Column(
                    Modifier.widthIn(max = 720.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    PageTitle(stringResource(R.string.tab_connectors))
                    KeloTextField(query, { query = it }, stringResource(R.string.connectors_search))
                    val all = list ?: run { MutedText(stringResource(R.string.loading)); return@Column }
                    val shown = remember(all, query) { ConnectorList.search(all, query) }
                    val home = remember(all, creds) { ConnectorList.home(all, creds) }
                    val full = showAll || home == null
                    val searching = query.isNotBlank()
                    val sections = remember(shown, creds, filter, full, searching, home) {
                        when {
                            searching -> ConnectorList.sections(shown, creds)
                            !full -> home.orEmpty()
                            else -> ConnectorList.sections(shown, creds, filter)
                        }
                    }
                    val chips = remember(all) { ConnectorList.filters(all) }
                    val hidden = remember(all, creds) { ConnectorList.hiddenCount(all, creds) }
                    if (full && !searching && home != null) LinkButton(stringResource(R.string.connectors_back_to_popular), onClick = { showAll = false; filter = null })
                    // the group chips wrap onto more lines rather than scrolling sideways, so none is cut at the edge
                    if (full && !searching && chips.size > 1) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        ChoiceChip(stringResource(R.string.connectors_filter_all), selected = filter == null, onClick = { filter = null })
                        chips.forEach { ch ->
                            ChoiceChip(ch.label?.let { stringResource(it) } ?: ch.title, selected = filter == ch.key, onClick = { filter = if (filter == ch.key) null else ch.key })
                        }
                    }
                    sections.forEach { section ->
                        Text(
                            section.label?.let { stringResource(it) } ?: section.title, style = MaterialTheme.typography.titleSmall,
                            color = when (section.key) { "reconnect" -> c.danger; "connected", "popular" -> c.accent; else -> c.muted },
                            modifier = Modifier.padding(start = 4.dp, top = 6.dp),
                        )
                        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(12.dp))) {
                            section.items.forEachIndexed { i, x ->
                                if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                                ConnectorRow(x, x.id == selected, ConnectorList.reconnect(x, creds) != null) { selected = x.id }
                            }
                        }
                    }
                    if (!full && !searching && hidden > 0) {
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button) { showAll = true }.padding(vertical = 14.dp),
                            horizontalArrangement = Arrangement.Center,
                        ) { Text(pluralStringResource(R.plurals.connectors_show_all, hidden, hidden), color = c.muted, style = MaterialTheme.typography.bodyMedium) }
                    }
                    if (shown.isEmpty()) {
                        if (failed) {
                            MutedText(stringResource(R.string.connect_failed))
                            SecondaryButton(stringResource(R.string.retry), onClick = reload)
                        } else MutedText(stringResource(R.string.connectors_empty))
                    }
                }
            }
            if (wide && sel != null) {
                VerticalDivider(color = c.line)
                ConnectorDetail(app, list?.let { ConnectorRules.find(it, sel) }, creds, loading = list == null, onBack = { selected = null }, onChanged = reload,
                    modifier = Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun ConnectorRow(x: Connector, selected: Boolean, broken: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().background(if (selected) c.mine else androidx.compose.ui.graphics.Color.Transparent)
            .clickable(role = Role.Button, onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ConnectorTile(x, 40.dp)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(x.name, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (x.description.isNotBlank()) Text(x.description, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        Box(Modifier.widthIn(max = 140.dp)) {
            when {
                broken -> ReconnectBadge()
                ConnectorRules.pendingReview(x) -> ReviewPill()
                else -> StatusBadge(ConnectorRules.badge(x.status))
            }
        }
    }
}

/**
 * One connector: a large tile with its name and state, what Kelo can do with it, how it is connected and who is (with the
 * permissions a code-connected service asks for), a warning when it needs connecting again, then the connect panel.
 */
@Composable
private fun ConnectorDetail(
    app: AppContainer, x: Connector?, creds: List<VaultEntry>, loading: Boolean, onBack: () -> Unit, onChanged: () -> Unit, modifier: Modifier = Modifier,
) {
    val c = Kelo.colors
    Box(modifier.fillMaxHeight(), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.widthIn(max = 640.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) { Icon(KeloIcons.ArrowLeft, contentDescription = stringResource(R.string.back), tint = c.ink) }
                Text(x?.name.orEmpty(), style = MaterialTheme.typography.titleLarge, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            }
            if (x == null) {
                MutedText(stringResource(if (loading) R.string.loading else R.string.connector_gone))
                return@Column
            }
            val broken = ConnectorList.reconnect(x, creds)
            val account = ConnectorList.account(x, creds)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                ConnectorTile(x, 64.dp)
                Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.weight(1f)) {
                    Text(TokenText.fit(x.name), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
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
                        Icon(KeloIcons.Alert, contentDescription = null, tint = c.danger, modifier = Modifier.size(20.dp))
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(stringResource(R.string.connector_reconnect_title), fontWeight = FontWeight.SemiBold, color = c.ink, style = MaterialTheme.typography.bodyLarge)
                            Text(stringResource(broken.text), color = c.ink, style = MaterialTheme.typography.bodySmall)
                            val detail = cred?.lastResultDetail.orEmpty()
                            if (broken == Reconnect.REFUSED && detail.isNotBlank()) MutedText(stringResource(R.string.connector_reconnect_detail, detail), small = true)
                        }
                    }
                }
            }
            if (x.description.isNotBlank()) DetailSection(stringResource(R.string.connector_can_do)) {
                Text(x.description, color = c.ink, style = MaterialTheme.typography.bodyLarge)
            }
            val method = if (x.status == "connected") ConnectorList.methodOf(x) else ConnectorList.method(x.connect)
            DetailSection(stringResource(R.string.connector_facts)) {
                FactRow(stringResource(R.string.connector_method), method?.let { stringResource(it.label) } ?: stringResource(R.string.connector_method_none))
                if (account.isNotBlank()) FactRow(stringResource(R.string.connector_account), account)
                else if (x.status == "connected") FactRow(stringResource(R.string.connector_account), stringResource(R.string.connector_account_unknown))
                if (x.site.isNotBlank()) FactRow(stringResource(R.string.connector_site), ConnectorRules.host(x.site).ifBlank { x.site })
                if (x.connect == "device" && x.status == "connected") DeviceScopes(app, x.via.ifBlank { x.id }, x.account)
            }
            if (x.connect != null) {
                Card { ConnectPanel(app, if (broken != null) x.copy(status = "available") else x, onChanged = onChanged) }
                if (broken != null && x.item.isNotBlank()) BrokenDisconnect(app, x, onChanged)
            }
            if (x.status == "coming_soon") MutedText(stringResource(R.string.connector_coming_soon_note), small = true)
            if (x.status == "unsupported") MutedText(stringResource(R.string.connector_unsupported_note), small = true)
            Spacer(Modifier.height(12.dp))
        }
    }
}

@Composable
private fun BrokenDisconnect(app: AppContainer, x: Connector, onChanged: () -> Unit) {
    val scope = rememberCoroutineScope()
    var asking by remember(x.id) { mutableStateOf(false) }
    LinkButton(stringResource(R.string.connector_disconnect), onClick = { asking = true }, danger = true)
    if (asking) ConfirmAsk(
        stringResource(R.string.mcp_disconnect_confirm, x.name), stringResource(R.string.connector_disconnect),
        onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(app.sessions.requireApi(), x.item) }; onChanged() } },
        onDismiss = { asking = false },
    )
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
        Text(TokenText.fit(value, 24), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(0.65f))
    }
}

/**
 * The permission a connected code-connected service was given (GET /api/oauth/providers: its `granted_scope` among its `scopes`).
 * Nothing when that choice is not known; [account] refetches after a new connection.
 */
@Composable
private fun DeviceScopes(app: AppContainer, provider: String, account: String) {
    var granted by remember(provider) { mutableStateOf<OAuthScope?>(null) }
    LaunchedEffect(provider, account) {
        runCatching { ConnectorCalls.oauthProviders(app.sessions.requireApi()) }.onSuccess { l -> granted = ConnectorRules.grantedScope(l.firstOrNull { it.id == provider }) }
    }
    val s = granted ?: return
    val c = Kelo.colors
    Text(stringResource(R.string.connector_permissions), color = c.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 4.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(KeloIcons.Check, contentDescription = null, tint = c.accent, modifier = Modifier.size(16.dp).padding(top = 2.dp))
        Column(Modifier.weight(1f)) {
            Text(TokenText.fit(s.label.ifBlank { s.id }), color = c.ink, style = MaterialTheme.typography.bodyMedium)
            if (s.detail.isNotBlank()) Text(s.detail, color = c.muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** The mailbox services a mail connector offers (as the phone's ConnectorRules.mailChoices: keep in step). */
object MailRules {
    /** Only Gmail on the Gmail connector; the other mailbox connector offers the server's list in the server's order. */
    fun choices(connectorId: String, providers: List<chat.kelo.app.connectors.MailProvider>): List<chat.kelo.app.connectors.MailProvider> =
        ConnectorRules.mailChoices(connectorId, providers)

    /** The one picked first: Gmail on the Gmail connector, else the first the server lists. */
    fun first(connectorId: String, providers: List<chat.kelo.app.connectors.MailProvider>): String =
        choices(connectorId, providers).firstOrNull()?.id.orEmpty()
}
