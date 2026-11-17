package chat.kelo.app.shell

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import chat.kelo.app.ui.text.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.isSpecified
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.notify.OpenTarget
import chat.kelo.app.platform.LocalToaster
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.Badge
import chat.kelo.app.ui.components.ChoiceChip
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.MyCard
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.LocalPageFrame
import chat.kelo.app.ui.platform.decodeImage
import chat.kelo.app.ui.platform.headerHeight
import chat.kelo.app.ui.platform.keloViewModel
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset

/*
 * The native renderer of Nest screens (docs/nest-ui.md Part 1): the server sends a plain tree of fixed components and this file lays
 * it out with the app's own colours, type and spacing. No web view, no HTML, no Markdown; every text is drawn as plain text; pictures
 * come only through the server's `/api/nest/...` paths; an address is opened only in the system browser.
 */

/** What the renderer needs from the app: a picture of a screen (with the auth header), the system browser, and the chat. */
interface NestHost {
    /** The bytes of the server path [path] (a screen's picture), or null when it cannot be had. */
    suspend fun image(path: String): ByteArray?
    /** Opens [url] in the system browser. */
    fun open(url: String)
    /** Opens the chat on the reply [turnId] (an action that asked Kelo for something, a share). */
    fun openChat(turnId: String)
}

val LocalNestHost = staticCompositionLocalOf<NestHost?> { null }

/** Re-reads GET /api/nest/extensions (a screen answered 403 / 404: it is gone from view). */
val LocalNestReload = staticCompositionLocalOf<() -> Unit> { {} }

/** How much of a tab's bottom the app's tab bar covers; the phone's shell provides it, a window has none. */
val LocalTabBottomInset = compositionLocalOf { 0.dp }

/** The renderer the app root provides ([ProvideNest]). */
object NestRenderer : NestSurfaceView {
    @Composable
    override fun Show(entry: NestEntry, modifier: Modifier) = NestSurface(entry, modifier)
}

/** What a rendered component needs to act: [busy] buttons, [run] an action, and how to fetch a picture. */
class NestUi(
    val busy: Set<String>,
    val run: (NestAction, form: kotlinx.serialization.json.JsonObject?) -> Unit,
    val image: suspend (String) -> ByteArray?,
)

/** What one open surface holds: its view model, the state read from it, the actions' plumbing, and the dialogs it may raise. */
class NestScreenHandle(val vm: NestScreenViewModel, val state: NestScreenState, val ui: NestUi, val dialogs: @Composable () -> Unit)

/** Loads the surface [entry], follows its live changes and effects (system browser, chat), and shows its messages. */
@Composable
fun rememberNestScreen(entry: NestEntry): NestScreenHandle {
    val services = LocalAppServices.current
    val host = LocalNestHost.current
    val reloadExtensions = LocalNestReload.current
    val toaster = LocalToaster.current
    val vm = keloViewModel("nest-screen-${entry.key}") { NestScreenViewModel(services.sessions, entry) }
    val st by vm.state.collectAsStateWithLifecycle()
    var started by remember { mutableStateOf<NestEffect.Started?>(null) }

    LaunchedEffect(entry.key) { vm.load() }
    LaunchedEffect(vm) {
        vm.effects.collect { fx ->
            when (fx) {
                is NestEffect.OpenUrl -> host?.open(fx.url) ?: services.browser.open(fx.url)
                is NestEffect.Started -> started = fx
            }
        }
    }
    LaunchedEffect(st.message) { st.message?.let { toaster.show(it); vm.clearMessage() } }
    LaunchedEffect(st.gone) { if (st.gone) reloadExtensions() }

    val ui = NestUi(st.busy, vm::run) { path -> host?.image(path) ?: runCatching { services.sessions.requireApi().download(path) }.getOrNull() }
    return NestScreenHandle(vm, st, ui) {
        st.confirm?.let { c -> NestConfirmDialog(c, onYes = vm::confirm, onDismiss = vm::dismissConfirm) }
        started?.let { s ->
            ConfirmDialog(
                text = s.message.ifBlank { string(Res.string.nest_started_chat) }, yes = string(Res.string.nest_open_chat),
                onYes = { host?.openChat(s.turnId) }, onDismiss = { started = null }, danger = false,
            )
        }
    }
}

/** One surface in its place: a tab page, a My section, a home card, or the body of the chat's sheet. */
@Composable
fun NestSurface(entry: NestEntry, modifier: Modifier = Modifier) {
    val h = rememberNestScreen(entry)
    val st = h.state
    val ui = h.ui
    var full by remember { mutableStateOf(false) }
    when (entry.place) {
        NestPlace.TAB -> NestTabPage(entry, st, ui, onRefresh = h.vm::load, modifier = modifier)
        NestPlace.HOME_CARD -> {
            MyCard(modifier.fillMaxWidth()) {
                NestHeader(entry, st.screen.title, onClick = { full = true })
                NestScreenBody(st, ui, entry)
            }
            if (full) Dialog(onDismissRequest = { full = false }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
                Box(Modifier.fillMaxSize().background(Kelo.colors.bg)) { NestFullPage(entry, onBack = { full = false }) }
            }
        }
        NestPlace.MY_SECTION -> Column(modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (entry.draft) Badge(string(Res.string.nest_draft))
            NestScreenBody(st, ui, entry)
        }
        else -> Column(modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {   // the chat's sheet
            NestHeader(entry, st.screen.title, onClick = null)
            NestScreenBody(st, ui, entry)
        }
    }
    h.dialogs()
}

/** A surface full screen under a back arrow (a home card's header, "Open" on a draft in My → Nest). */
@Composable
fun NestFullPage(entry: NestEntry, onBack: () -> Unit) {
    val h = rememberNestScreen(entry)
    SubPage(h.state.screen.title.ifBlank { entry.title.ifBlank { entry.packageName } }, onBack) {
        if (entry.draft) Badge(string(Res.string.nest_draft))
        NestScreenBody(h.state, h.ui, entry)
    }
    h.dialogs()
}

/** A tab: the screen scrolls under the app's header and over its tab bar, with pull-to-refresh. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NestTabPage(entry: NestEntry, st: NestScreenState, ui: NestUi, onRefresh: () -> Unit, modifier: Modifier) {
    val scroll = rememberScrollState()
    val isScrolled by remember(scroll) { derivedStateOf { scroll.canScrollBackward } }
    ReportTopBarScrolled("nest/${entry.key}", isScrolled)
    val frame = LocalPageFrame.current
    PullToRefreshBox(isRefreshing = st.refreshing, onRefresh = onRefresh, modifier = modifier.fillMaxSize()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            Column(
                Modifier.then(if (frame.maxWidth.isSpecified) Modifier.widthIn(max = frame.maxWidth) else Modifier).fillMaxSize()
                    .verticalScroll(scroll)
                    .padding(start = 16.dp, end = 16.dp, top = headerHeight() + frame.topInset + 4.dp, bottom = LocalTabBottomInset.current + 24.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                NestHeader(entry, st.screen.title, onClick = null)
                NestScreenBody(st, ui, entry)
            }
        }
    }
}

/** The title of a surface with the small "Draft" badge of a draft, and under it whose screen it is (the package's name and author). */
@Composable
internal fun NestHeader(entry: NestEntry, title: String, onClick: (() -> Unit)?) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().then(if (onClick != null) Modifier.clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClick = onClick) else Modifier),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                title.ifBlank { entry.title.ifBlank { entry.packageName } }, color = c.ink, style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f, fill = false).semantics { heading() },
            )
            if (entry.draft) Badge(string(Res.string.nest_draft))
        }
        val from = if (entry.author.isBlank()) entry.packageName else "${entry.packageName} · ${entry.author}"
        if (from.isNotBlank()) MutedText(string(Res.string.nest_from, from), small = true)
    }
}

/** The screen's body: the quiet notice of data that could not be fetched, then the components top to bottom. */
@Composable
fun NestScreenBody(st: NestScreenState, ui: NestUi, entry: NestEntry? = null) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        when {
            !st.loaded -> SkeletonRows(3)
            st.gone -> MutedText(string(Res.string.nest_gone))
            else -> {
                if (st.error != null && st.screen.components.isEmpty()) MutedText(st.error.ifBlank { string(Res.string.nest_load_failed) })
                st.screen.errors.forEach { e ->
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.Top) {
                        Icon(KeloIcons.Info, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp).padding(top = 2.dp))
                        Text(e, color = c.muted, style = MaterialTheme.typography.bodySmall)
                    }
                }
                NestComponents(st.screen.components, ui)
            }
        }
    }
}

/** A column of components. */
@Composable
fun NestComponents(components: List<NestComponent>, ui: NestUi) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        components.forEach { NestPart(it, ui) }
    }
}

@Composable
private fun NestPart(c: NestComponent, ui: NestUi) {
    when (c) {
        is NestComponent.Header -> NestHeaderPart(c)
        is NestComponent.Text -> NestTextPart(c)
        is NestComponent.Stat -> NestStatPart(c)
        is NestComponent.ListView -> NestListPart(c, ui)
        is NestComponent.Card -> NestCardPart(c, ui)
        is NestComponent.Chart -> NestChartPart(c)
        is NestComponent.Image -> NestImagePart(c, ui)
        is NestComponent.Button -> NestButtonPart(c.label, c.style, c.action, ui)
        is NestComponent.Form -> NestFormPart(c, ui)
        is NestComponent.Empty -> NestEmptyPart(c, ui)
        is NestComponent.Unsupported -> NestUnsupportedPart(c)
    }
}

@Composable
private fun NestHeaderPart(h: NestComponent.Header) {
    if (h.text.isBlank() && h.subtitle.isBlank()) return
    Column(Modifier.fillMaxWidth().padding(top = 6.dp)) {
        if (h.text.isNotBlank()) Text(h.text, color = Kelo.colors.ink, style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
        if (h.subtitle.isNotBlank()) MutedText(h.subtitle, small = true)
    }
}

@Composable
private fun NestTextPart(t: NestComponent.Text) {
    if (t.text.isBlank()) return
    val c = Kelo.colors
    when (t.style) {
        "title" -> Text(t.text, color = c.ink, style = MaterialTheme.typography.titleLarge)
        "caption" -> Text(t.text, color = c.muted, style = MaterialTheme.typography.bodySmall)
        "label" -> Text(t.text, color = c.ink, style = MaterialTheme.typography.labelLarge)
        else -> Text(t.text, color = c.ink, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun NestStatPart(s: NestComponent.Stat) {
    val c = Kelo.colors
    MyCard {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            if (s.icon.isNotBlank()) Icon(NestIcons.of(s.icon), contentDescription = null, tint = c.accent, modifier = Modifier.size(28.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                if (s.label.isNotBlank()) Text(s.label, color = c.muted, style = MaterialTheme.typography.bodySmall)
                Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(s.value, color = c.ink, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f, fill = false))
                    if (s.unit.isNotBlank()) Text(s.unit, color = c.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(bottom = 4.dp))
                }
                if (s.caption.isNotBlank()) Text(s.caption, color = c.muted, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun NestListPart(l: NestComponent.ListView, ui: NestUi) {
    if (l.rows.isEmpty()) {
        if (l.emptyText != null) NestEmptyBlock(l.emptyText, l.emptyIcon, null, ui)
        return
    }
    var removing by remember { mutableStateOf<NestAction?>(null) }
    Column(Modifier.fillMaxWidth()) {
        l.rows.forEachIndexed { i, row ->
            if (i > 0) HorizontalDivider(color = Kelo.colors.line)
            NestRowPart(row, ui, onRemove = { removing = it })
        }
    }
    removing?.let { a ->
        ConfirmDialog(
            string(Res.string.nest_remove_q), a.label.ifBlank { string(Res.string.nest_remove) },
            onYes = { ui.run(a, null) }, onDismiss = { removing = null },
        )
    }
}

@Composable
private fun NestRowPart(r: NestRow, ui: NestUi, onRemove: (NestAction) -> Unit) {
    val c = Kelo.colors
    val a = r.action
    val tappable = a != null && !a.removes
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp)
            .then(if (tappable && a != null) Modifier.clickable(enabled = a.key !in ui.busy, role = Role.Button) { ui.run(a, null) } else Modifier)
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // an image wins over an icon
        if (r.image.isNotBlank()) NestPicture(r.image, ui, Modifier.size(40.dp).clip(RoundedCornerShape(8.dp)), alt = r.title)
        else if (r.icon.isNotBlank()) Icon(NestIcons.of(r.icon), contentDescription = null, tint = c.muted, modifier = Modifier.size(24.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(r.title, color = c.ink, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (r.subtitle.isNotBlank()) Text(r.subtitle, color = c.muted, style = MaterialTheme.typography.bodySmall)
        }
        if (r.trailing.isNotBlank()) Text(r.trailing, color = c.muted, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.End, modifier = Modifier.widthIn(max = 120.dp))
        if (a != null && a.removes) {
            if (a.key in ui.busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = c.muted)
            else LinkButton(a.label.ifBlank { string(Res.string.nest_remove) }, onClick = { onRemove(a) }, danger = true)
        }
    }
}

@Composable
private fun NestCardPart(card: NestComponent.Card, ui: NestUi) {
    val a = card.action
    MyCard(onClick = if (a != null && a.key !in ui.busy) ({ ui.run(a, null) }) else null) {
        if (card.title.isNotBlank()) Text(card.title, color = Kelo.colors.ink, style = MaterialTheme.typography.titleMedium)
        if (card.subtitle.isNotBlank()) MutedText(card.subtitle, small = true)
        NestComponents(card.children, ui)
    }
}

@Composable
private fun NestChartPart(ch: NestComponent.Chart) {
    if (ch.points.isEmpty()) return
    val c = Kelo.colors
    val accent = c.accent
    val line = c.line
    val label = ch.label
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (label.isNotBlank()) Text(label, color = c.muted, style = MaterialTheme.typography.bodySmall)
        val lo = minOf(0.0, ch.points.minOf { it.y })
        val hi = ch.points.maxOf { it.y }.let { if (it <= lo) lo + 1 else it }
        val desc = ch.points.joinToString(", ") { "${it.x} ${it.y}" }
        Canvas(Modifier.fillMaxWidth().height(120.dp).semantics { contentDescription = desc }) {
            val w = size.width
            val h = size.height
            drawLine(line, Offset(0f, h), Offset(w, h), strokeWidth = 1.dp.toPx())
            val n = ch.points.size
            fun yOf(v: Double) = (h - ((v - lo) / (hi - lo)).toFloat() * (h - 4.dp.toPx())).coerceIn(0f, h)
            if (ch.kind == "bar") {
                val slot = w / n
                val bar = (slot * 0.6f).coerceAtMost(48.dp.toPx())
                ch.points.forEachIndexed { i, p ->
                    val top = yOf(p.y)
                    drawRect(accent, Offset(slot * i + (slot - bar) / 2, top), Size(bar, (h - top).coerceAtLeast(1f)))
                }
            } else {
                val step = if (n > 1) w / (n - 1) else 0f
                val path = Path()
                ch.points.forEachIndexed { i, p ->
                    val x = if (n > 1) step * i else w / 2
                    if (i == 0) path.moveTo(x, yOf(p.y)) else path.lineTo(x, yOf(p.y))
                }
                drawPath(path, accent, style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round))
                ch.points.forEachIndexed { i, p -> drawCircle(accent, 3.dp.toPx(), Offset(if (n > 1) step * i else w / 2, yOf(p.y))) }
            }
        }
        // the category labels in order; a long series shows its first, middle and last
        val shown = if (ch.points.size <= 7) ch.points else listOf(ch.points.first(), ch.points[ch.points.size / 2], ch.points.last())
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            shown.forEach { Text(it.x, color = c.muted, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false)) }
        }
    }
}

@Composable
private fun NestImagePart(img: NestComponent.Image, ui: NestUi) {
    NestPicture(img.src, ui, Modifier.fillMaxWidth().heightIn(max = img.height.dp), alt = img.alt)
}

/** A picture of a screen: fetched through the server (`/api/nest/...` only, with the auth header) and fitted inside its box. */
@Composable
private fun NestPicture(src: String, ui: NestUi, modifier: Modifier, alt: String) {
    val bitmap by produceState<androidx.compose.ui.graphics.ImageBitmap?>(null, src) {
        value = if (src.startsWith("/api/nest/")) ui.image(src)?.let { decodeImage(it) } else null
    }
    val b = bitmap
    if (b == null) Box(modifier.background(Kelo.colors.bubble).semantics { contentDescription = alt })
    else Image(b, contentDescription = alt.ifBlank { null }, modifier = modifier, contentScale = androidx.compose.ui.layout.ContentScale.Fit)
}

@Composable
private fun NestButtonPart(label: String, style: String, action: NestAction, ui: NestUi) {
    val c = Kelo.colors
    // hidden while its request runs, so it cannot be sent twice
    if (action.key in ui.busy) {
        Box(Modifier.fillMaxWidth().height(44.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = c.muted) }
        return
    }
    val text = label.ifBlank { action.label }.ifBlank { string(Res.string.ok) }
    when (style) {
        "secondary" -> SecondaryButton(text, onClick = { ui.run(action, null) }, modifier = Modifier.fillMaxWidth())
        "danger" -> Button(
            onClick = { ui.run(action, null) }, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(50),
            colors = ButtonDefaults.buttonColors(containerColor = c.danger, contentColor = c.onDanger),
        ) { Text(text, style = MaterialTheme.typography.labelLarge) }
        else -> PrimaryButton(text, onClick = { ui.run(action, null) })
    }
}

@Composable
private fun NestEmptyPart(e: NestComponent.Empty, ui: NestUi) {
    if (e.text.isBlank() && e.icon.isBlank()) return
    NestEmptyBlock(e.text, e.icon, e.action, ui)
}

@Composable
private fun NestEmptyBlock(text: String, icon: String, action: NestAction?, ui: NestUi) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth().padding(vertical = 16.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (icon.isNotBlank()) Icon(NestIcons.of(icon), contentDescription = null, tint = c.muted, modifier = Modifier.size(32.dp))
        if (text.isNotBlank()) Text(text, color = c.muted, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
        if (action != null) NestButtonPart(action.label, "secondary", action, ui)
    }
}

@Composable
private fun NestUnsupportedPart(u: NestComponent.Unsupported) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.bubble).padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(KeloIcons.Info, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp))
        Text(u.text.ifBlank { string(Res.string.nest_update_part) }, color = c.muted, style = MaterialTheme.typography.bodySmall)
    }
}

// ---- forms

@Composable
private fun NestFormPart(form: NestComponent.Form, ui: NestUi) {
    val values = remember(form) { mutableStateMapOf<String, String>().apply { putAll(NestFormRules.initial(form.fields)) } }
    var missing by remember(form) { mutableStateOf(emptySet<String>()) }
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        form.fields.forEach { f ->
            NestFieldPart(f, values[f.id].orEmpty(), error = f.id in missing) { v -> values[f.id] = v; missing = missing - f.id }
        }
        val a = form.submit
        if (a.key in ui.busy) {
            Box(Modifier.fillMaxWidth().height(44.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = Kelo.colors.muted) }
        } else {
            PrimaryButton(form.submitLabel.ifBlank { a.label }.ifBlank { string(Res.string.ok) }, onClick = {
                val bad = NestFormRules.missing(form.fields, values)
                if (bad.isNotEmpty()) { missing = bad.toSet(); return@PrimaryButton }
                ui.run(a, NestFormRules.payload(form.fields, values))
                if (a.type == "store_add") values.putAll(NestFormRules.initial(form.fields))   // ready for the next entry
            })
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NestFieldPart(f: NestField, value: String, error: Boolean, onChange: (String) -> Unit) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (f.type == "toggle") {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(f.label, color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                Switch(
                    checked = value == "true", onCheckedChange = { onChange(if (it) "true" else "false") },
                    colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
                )
            }
            return@Column
        }
        if (f.label.isNotBlank()) Text(f.label, color = c.muted, style = MaterialTheme.typography.labelLarge)
        when (f.type) {
            "number" -> KeloTextField(
                value, { onChange(it.filter { ch -> ch.isDigit() || ch == '.' || ch == ',' || ch == '-' }) }, f.placeholder,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            )
            "multiline" -> KeloTextField(
                value, onChange, f.placeholder, singleLine = false, maxLength = f.maxLength ?: NestLimits.MAX_TEXT,
                keyboardOptions = KeyboardOptions(capitalization = androidx.compose.ui.text.input.KeyboardCapitalization.Sentences),   // sentences, so it may wrap
            )
            "date" -> {
                var open by remember { mutableStateOf(false) }
                val date = runCatching { LocalDate.parse(value) }.getOrNull()
                ChoiceChip(date?.toString() ?: f.placeholder.ifBlank { string(Res.string.nest_pick_date) }, selected = date != null, onClick = { open = true })
                if (open) {
                    val st = rememberDatePickerState(initialSelectedDateMillis = (date ?: LocalDate.now()).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli())
                    DatePickerDialog(
                        onDismissRequest = { open = false },
                        confirmButton = {
                            TextButton(onClick = {
                                st.selectedDateMillis?.let { onChange(Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate().toString()) }
                                open = false
                            }) { Text(string(Res.string.ok), color = c.accent) }
                        },
                        dismissButton = { TextButton(onClick = { open = false }) { Text(string(Res.string.cancel), color = c.ink) } },
                    ) { DatePicker(st) }
                }
            }
            "time" -> {
                var open by remember { mutableStateOf(false) }
                val time = runCatching { LocalTime.parse(value) }.getOrNull()
                ChoiceChip(time?.toString() ?: f.placeholder.ifBlank { string(Res.string.nest_pick_time) }, selected = time != null, onClick = { open = true })
                if (open) {
                    val start = time ?: LocalTime.of(12, 0)
                    val st = rememberTimePickerState(start.hour, start.minute, is24Hour = true)
                    AlertDialog(
                        onDismissRequest = { open = false }, containerColor = c.card, text = { TimePicker(st) },
                        confirmButton = {
                            TextButton(onClick = { onChange("%02d:%02d".format(st.hour, st.minute)); open = false }) { Text(string(Res.string.ok), color = c.accent) }
                        },
                        dismissButton = { TextButton(onClick = { open = false }) { Text(string(Res.string.cancel), color = c.ink) } },
                    )
                }
            }
            "select" -> {
                var open by remember { mutableStateOf(false) }
                val chosen = f.options.firstOrNull { it.value == value }
                Box {
                    ChoiceChip(chosen?.label ?: f.placeholder.ifBlank { string(Res.string.nest_pick_option) }, selected = chosen != null, onClick = { open = true })
                    DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                        f.options.forEach { o -> DropdownMenuItem(text = { Text(o.label) }, onClick = { onChange(o.value); open = false }) }
                    }
                }
            }
            else -> KeloTextField(value, onChange, f.placeholder, maxLength = f.maxLength ?: NestLimits.MAX_TEXT)
        }
        if (error) Text(string(Res.string.nest_required), color = c.danger, style = MaterialTheme.typography.bodySmall)
    }
}

/** The owner confirms a change that goes to an outside service: the question, where it goes, and what is sent (plain lines). */
@Composable
fun NestConfirmDialog(c: NestConfirm, onYes: () -> Unit, onDismiss: () -> Unit) {
    val k = Kelo.colors
    AlertDialog(
        onDismissRequest = onDismiss, containerColor = k.card,
        title = { Text(c.result.title.ifBlank { string(Res.string.nest_confirm) }, color = k.ink) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (c.result.detail.isNotBlank()) Text(c.result.detail, color = k.ink, style = MaterialTheme.typography.bodyMedium)
                if (c.result.sends.isNotEmpty()) {
                    Text(string(Res.string.nest_sends), color = k.muted, style = MaterialTheme.typography.labelLarge)
                    c.result.sends.forEach { (key, v) -> Text("$key: $v", color = k.ink, style = MaterialTheme.typography.bodySmall) }
                }
            }
        },
        confirmButton = { TextButton(onClick = onYes) { Text(string(Res.string.nest_confirm), color = k.accent) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(string(Res.string.cancel), color = k.ink) } },
    )
}
