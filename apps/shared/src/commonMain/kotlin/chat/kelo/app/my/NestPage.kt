package chat.kelo.app.my

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.connectors.DeviceConnect
import chat.kelo.app.notify.OpenTarget
import chat.kelo.app.platform.LocalToaster
import chat.kelo.app.res.*
import chat.kelo.app.shell.LocalNest
import chat.kelo.app.shell.NestEntry
import chat.kelo.app.shell.NestFullPage
import chat.kelo.app.shell.NestLive
import chat.kelo.app.shell.NestPlace
import chat.kelo.app.shell.NestViewModel
import chat.kelo.app.ui.components.Badge
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.EmptyNote
import chat.kelo.app.ui.components.KeloModal
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.MyCard
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SectionLabel
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.platform.keloViewModel
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo

private const val SCREEN = "screen:"
private const val SKILL = "skill:"
private const val TEXT = "text:"

/**
 * 마이 → Nest: what Kelo made by talking ("Made here": the drafts and the custom skills, each with Open, Share, Delete) and which
 * package tabs the bottom bar shows. The pages it opens push onto its own stack ("screen:<key>", "skill:<id>", "text:<id>").
 */
@Composable
fun NestPage(onBack: () -> Unit) {
    val services = LocalAppServices.current
    val sessions = services.sessions
    val vm = keloViewModel("nest-made") { MadeHereViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    val nest = LocalNest.current
    var at by rememberSaveable { mutableStateOf(listOf<String>()) }
    var delete by remember { mutableStateOf<MadeItem?>(null) }
    val toaster = LocalToaster.current
    val shareFailed = string(Res.string.nest_share_failed)

    LaunchedEffect(Unit) { vm.load() }
    LaunchedEffect(vm) {
        vm.effects.collect { fx ->
            when (fx) {
                is MadeEffect.OpenChat -> {   // Kelo asks where to put it: the chat follows that reply
                    services.chatFollowTurn.value = fx.turnId
                    services.openRequests.value = OpenTarget.Chat
                }
            }
        }
    }
    LaunchedEffect(ui.message) { ui.message?.let { toaster.show(it.ifBlank { shareFailed }); vm.clearMessage() } }

    val top = at.lastOrNull().orEmpty()
    fun pop() { if (at.isEmpty()) onBack() else at = at.dropLast(1) }
    fun entryOf(key: String): NestEntry? = nest.byPlace.values.flatten().firstOrNull { it.key == key }

    when {
        top.startsWith(SCREEN) -> {
            val entry = entryOf(top.removePrefix(SCREEN))
            if (entry != null) NestFullPage(entry, onBack = ::pop) else SubPage(string(Res.string.nest_title), ::pop) { MutedText(string(Res.string.nest_gone)) }
        }
        top.startsWith(SKILL) -> SkillPage(top.removePrefix(SKILL), back = ::pop, afterDelete = { vm.load(); pop() })
        top.startsWith(TEXT) -> DraftTextPage(top.removePrefix(TEXT), ui.items.firstOrNull { it.id == top.removePrefix(TEXT) }?.title.orEmpty(), ::pop)
        else -> SubPage(string(Res.string.nest_title), ::pop) {
            LinkSection()
            InstalledSection()
            SectionLabel(string(Res.string.nest_made_here))
            MutedText(string(Res.string.nest_made_hint), small = true)
            when {
                !ui.loaded -> SkeletonRows(2)
                ui.items.isEmpty() -> EmptyNote(string(Res.string.nest_made_empty))
                else -> ui.items.forEach { item ->
                    MadeCard(
                        item, busy = item.id in ui.busy,
                        onOpen = {
                            val screen = if (item.draft) nest.byPlace.values.flatten().filter { it.packageId == item.id && it.draft }
                                .minByOrNull { if (it.place == NestPlace.TAB) 0 else 1 } else null
                            at = at + when {
                                screen != null -> "$SCREEN${screen.key}"
                                item.draft -> "$TEXT${item.id}"
                                else -> "$SKILL${item.id}"
                            }
                        },
                        onShare = { vm.share(item) },
                        onDelete = { delete = item },
                        onRepo = { url -> services.browser.open(url) },
                    )
                }
            }
            TabsSection()
        }
    }

    delete?.let { item ->
        ConfirmDialog(string(Res.string.nest_delete_q, item.title), string(Res.string.nest_delete), onYes = { vm.delete(item) }, onDismiss = { delete = null })
    }
    ui.connectFor?.let { item -> ConnectSheet(item, onDone = vm::retryShare, onDismiss = vm::dismissConnect) }
}

/** One thing that was made: its name, kind, version, "Draft" and "Shared on GitHub" tags, and Open / Share / Delete. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun MadeCard(item: MadeItem, busy: Boolean, onOpen: () -> Unit, onShare: () -> Unit, onDelete: () -> Unit, onRepo: (String) -> Unit) {
    val c = Kelo.colors
    MyCard {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(item.title, color = c.ink, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f, fill = false))
            if (item.draft) Badge(string(Res.string.nest_draft))
        }
        val kind = string(when (item.kind) {
            MadeKind.SKILL -> Res.string.nest_kind_skill
            MadeKind.EXTENSION -> Res.string.nest_kind_extension
            MadeKind.CONNECTOR -> Res.string.nest_kind_connector
            MadeKind.CUSTOM_SKILL -> Res.string.nest_kind_custom_skill
        })
        MutedText(if (item.version.isBlank()) kind else "$kind · ${string(Res.string.nest_version, item.version)}", small = true)
        if (item.shared) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MutedText("${string(Res.string.nest_shared_on_github)} · ${item.repo}", small = true, modifier = Modifier.weight(1f, fill = false))
            }
            item.repoUrl?.let { url -> LinkButton(string(Res.string.nest_open_repo), onClick = { onRepo(url) }) }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            SecondaryButton(string(Res.string.nest_open), onClick = onOpen, enabled = !busy)
            SecondaryButton(string(Res.string.nest_share), onClick = onShare, enabled = !busy)
            LinkButton(string(Res.string.nest_delete), onClick = onDelete, danger = true)
            if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = c.muted)
        }
    }
}

/** A custom skill's text, read only, by the viewer of "Kelo의 파일" (changes go through the chat). */
@Composable
private fun SkillPage(id: String, back: () -> Unit, afterDelete: () -> Unit) {
    val sessions = LocalAppServices.current.sessions
    val vm = keloViewModel("my-kelo-files") { KeloFilesViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(id) { vm.openSkill(id) }
    SubPage(ui.skill?.takeIf { it.id == id }?.title.orEmpty(), back) { SkillView(id, ui, vm) { afterDelete() } }
}

/** A draft that has no screen: its SKILL.md (else its manifest), read only, with what the validator found. */
@Composable
private fun DraftTextPage(id: String, title: String, back: () -> Unit) {
    val sessions = LocalAppServices.current.sessions
    var details by remember(id) { mutableStateOf<NestDraftDetails?>(null) }
    var failed by remember(id) { mutableStateOf(false) }
    LaunchedEffect(id) {
        try {
            details = sessions.requireApi().get<NestDraftDetails>("/api/nest/drafts/${chat.kelo.app.core.KeloApi.segment(id)}")
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) { failed = true }
    }
    LaunchedEffect(Unit) { NestLive.changes.collect { ch -> if (ch == id) runCatching { details = sessions.requireApi().get("/api/nest/drafts/${chat.kelo.app.core.KeloApi.segment(id)}") } } }
    SubPage(title.ifBlank { id }, back) {
        Badge(string(Res.string.nest_draft))
        val d = details
        when {
            failed -> MutedText(string(Res.string.nest_load_failed))
            d == null -> SkeletonRows(3)
            else -> {
                d.findings.forEach { MutedText(it, small = true) }
                val text = d.files["SKILL.md"] ?: d.files["kelo-package.json"].orEmpty()
                val mono = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace)
                SelectionContainer { Text(text, style = mono, color = Kelo.colors.ink) }
            }
        }
    }
}

/** Sharing needs GitHub with write access: the existing connect panel (device code or web sign-in), then Share goes on by itself. */
@Composable
private fun ConnectSheet(item: MadeItem, onDone: () -> Unit, onDismiss: () -> Unit) {
    KeloModal(onDismiss) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(string(Res.string.nest_connect_title), color = Kelo.colors.ink, style = MaterialTheme.typography.titleMedium)
            MutedText(string(Res.string.nest_connect_hint), small = true)
            DeviceConnect("github", onChanged = onDone, onNotify = null)
            PrimaryButton(string(Res.string.nest_share_github), onClick = onDone)
            Text(item.title, color = Kelo.colors.muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** Which package tabs the bottom bar shows: every `tab` entry with a switch, at most as many as the server allows (PUT /api/nest/extensions/tabs). */
@Composable
private fun TabsSection() {
    val nest = LocalNest.current
    val tabs = nest.byPlace[NestPlace.TAB].orEmpty()
    if (tabs.isEmpty()) return
    val sessions = LocalAppServices.current.sessions
    val vm = keloViewModel("nest") { NestViewModel(sessions) }
    val toaster = LocalToaster.current
    val failed = string(Res.string.nest_tabs_failed)
    val shown = tabs.filter { it.shown }.map { it.key }
    SectionLabel(string(Res.string.nest_tabs_title))
    MutedText(string(Res.string.nest_tabs_hint, nest.maxTabs), small = true)
    tabs.forEach { e ->
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            androidx.compose.material3.Icon(chat.kelo.app.shell.NestIcons.of(e.icon), contentDescription = null, tint = Kelo.colors.muted, modifier = Modifier.size(24.dp))
            Column(Modifier.weight(1f)) {
                Text(e.title.ifBlank { e.packageName }, color = Kelo.colors.ink, style = MaterialTheme.typography.bodyLarge)
                MutedText(if (e.draft) "${e.packageName} · ${string(Res.string.nest_draft)}" else e.packageName, small = true)
            }
            Switch(
                checked = e.shown,
                // at most max_tabs at once: a switch that would go past it stays off until another is turned off
                enabled = e.shown || shown.size < nest.maxTabs,
                onCheckedChange = { on -> vm.setTabs(if (on) shown + e.key else shown - e.key) { toaster.show(it.ifBlank { failed }) } },
                colors = SwitchDefaults.colors(checkedTrackColor = Kelo.colors.accent, checkedThumbColor = Kelo.colors.onAccent),
            )
        }
    }
}

/** Install from a link: paste a GitHub link, Look, then the card says what it is and what it may do; Install puts it on. */
@Composable
private fun LinkSection() {
    val sessions = LocalAppServices.current.sessions
    val vm = keloViewModel("nest-links") { NestLinksViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.loadInstalled() }
    var contentsOpen by remember { mutableStateOf(false) }
    LaunchedEffect(ui.card?.token) { contentsOpen = false }   // a new preview or none: the contents sheet of the last one closes
    SectionLabel(string(Res.string.nest_link_title))
    MutedText(string(Res.string.nest_link_hint), small = true)
    MyCard {
        KeloTextField(ui.link, vm::setLink, "https://github.com/…", modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
            keyboardActions = KeyboardActions(onGo = { vm.check() }))
        ui.problem?.let { MutedText(string(problemText(it)), small = true) }
        if (ui.reason.isNotBlank()) MutedText(ui.reason, small = true)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PrimaryButton(string(Res.string.nest_link_check), onClick = vm::check, enabled = ui.link.isNotBlank() && !ui.checking)
            if (ui.checking) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = Kelo.colors.muted)
        }
    }
    ui.card?.let { card -> LinkCardSheet(card, ui.installing, onInstall = vm::install, onDismiss = vm::dismissCard, onContents = { contentsOpen = true }) }
    if (contentsOpen) ui.card?.let { card -> NestContentsSheet(card.contents, onDismiss = { contentsOpen = false }) }
    ui.look?.let { row -> UpdateLookSheet(row, onUpdate = { vm.update(row, accepted = true) }, onDismiss = vm::dismissLook) }
}

private fun problemText(p: NestLinkProblem) = when (p) {
    NestLinkProblem.NOT_A_LINK -> Res.string.nest_link_not_link
    NestLinkProblem.NOT_A_PACKAGE -> Res.string.nest_link_not_package
    NestLinkProblem.EXPIRED -> Res.string.nest_link_expired
    NestLinkProblem.CHANGED -> Res.string.nest_link_changed
    NestLinkProblem.ALREADY -> Res.string.nest_link_already
    NestLinkProblem.FAILED -> Res.string.nest_link_failed
}

/** What the link is and what it may do, then Install. Nobody reviewed it: the sheet says so. */
@Composable
private fun LinkCardSheet(card: NestLinkCard, installing: Boolean, onInstall: () -> Unit, onDismiss: () -> Unit, onContents: () -> Unit) {
    KeloModal(onDismiss) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(card.name.ifBlank { card.id }, color = Kelo.colors.ink, style = MaterialTheme.typography.titleMedium)
            MutedText(listOf(card.repo, string(Res.string.nest_version, card.version)).filter { it.isNotBlank() }.joinToString(" · "), small = true)
            if (card.description.isNotBlank()) Text(card.description, color = Kelo.colors.ink, style = MaterialTheme.typography.bodyMedium)
            Badge(string(Res.string.nest_unreviewed))
            SectionLabel(string(Res.string.nest_may_do))
            card.permissions.words.forEach { w -> WordLine(w) }
            if (NestContentsRules.hasContents(card.contents)) LinkButton(string(Res.string.nest_contents_open), onClick = onContents)
            card.installed?.let { MutedText(string(Res.string.nest_link_already_version, it), small = true) }
            PrimaryButton(string(Res.string.nest_install), onClick = onInstall, enabled = !installing && card.installed == null)
        }
    }
}

@Composable
private fun WordLine(w: NestWord) {
    Text("• ${w.text}", color = if (w.level == "caution") Kelo.colors.danger else Kelo.colors.ink, style = MaterialTheme.typography.bodyMedium)
}

/** An update that asks for more than before: the new list, then Update. */
@Composable
private fun UpdateLookSheet(row: NestInstalledRow, onUpdate: () -> Unit, onDismiss: () -> Unit) {
    KeloModal(onDismiss) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(string(Res.string.nest_update_asks_more, row.title), color = Kelo.colors.ink, style = MaterialTheme.typography.titleMedium)
            row.update?.words.orEmpty().forEach { WordLine(it) }
            PrimaryButton(string(Res.string.nest_update), onClick = onUpdate)
        }
    }
}

/** The installed packages: name, repository, version; Update when the repository has a newer commit; Remove. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun InstalledSection() {
    val sessions = LocalAppServices.current.sessions
    val vm = keloViewModel("nest-links") { NestLinksViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    var removing by remember { mutableStateOf<NestInstalledRow?>(null) }
    var changedOf by remember { mutableStateOf<NestInstalledRow?>(null) }
    if (ui.installed.isEmpty()) return
    SectionLabel(string(Res.string.nest_installed))
    ui.installed.forEach { row ->
        MyCard {
            Text(row.title, color = Kelo.colors.ink, style = MaterialTheme.typography.titleMedium)
            MutedText(listOf(row.repo, string(Res.string.nest_version, row.version)).filter { it.isNotBlank() }.joinToString(" · "), small = true)
            row.update?.let { u -> MutedText(string(Res.string.nest_update_ready, u.version), small = true) }
            val changed = row.update?.changed?.let { NestContentsRules.changedCount(it) } ?: 0
            if (changed > 0) MutedText(string(Res.string.nest_update_changed, changed), small = true)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (row.update != null && !row.pinned) SecondaryButton(string(Res.string.nest_update), onClick = { vm.update(row) }, enabled = row.id !in ui.busy)
                if (changed > 0) LinkButton(string(Res.string.nest_changed_open), onClick = { changedOf = row })
                LinkButton(string(Res.string.nest_remove), onClick = { removing = row }, danger = true)
                if (row.id in ui.busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = Kelo.colors.muted)
            }
        }
    }
    removing?.let { r -> ConfirmDialog(string(Res.string.nest_remove_pkg_q, r.title), string(Res.string.nest_remove), onYes = { vm.remove(r) }, onDismiss = { removing = null }) }
    changedOf?.let { r -> NestChangedSheet(r.title, r.update?.changed ?: NestChanged(), onDismiss = { changedOf = null }) }
}
