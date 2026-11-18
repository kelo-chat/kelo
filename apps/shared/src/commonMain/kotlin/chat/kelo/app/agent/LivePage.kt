package chat.kelo.app.agent

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*


import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.VectorConverter
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.input.pointer.PointerEventType
import androidx.compose.ui.input.pointer.PointerIcon
import androidx.compose.ui.input.pointer.isCtrlPressed
import androidx.compose.ui.input.pointer.isMetaPressed
import androidx.compose.ui.input.pointer.pointerHoverIcon
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.compose.runtime.CompositionLocalProvider
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalFormFactor
import kotlin.math.roundToInt
import org.jetbrains.compose.resources.StringResource
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Fill
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.ui.platform.LocalNetworkInfo
import chat.kelo.app.ui.platform.rememberPageViewModelStoreOwner
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.LocalHaptics
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.net.URI

/**
 * One browser tab, live, on the whole screen (FLAG_SECURE: a page may show a password). The picture follows the tab as the agent
 * works in it; pinch to zoom and drag to look around. "직접 조작" takes the tab over: the agent waits, taps and drags go to the
 * page (a drag scrolls it), and a text field, keys and back / forward / reload come up; "돌려주기" hands it back. Leaving the page
 * hands it back too.
 *
 * [fallback] is what shows when there is nothing live to show (a tab that has closed keeps its last picture there).
 */
@Composable
fun LiveTabPage(
    tabId: String,
    tab: BrowserTab?,
    onBack: () -> Unit,
    onCloseTab: (() -> Unit)?,
    takeControl: Boolean = false,
    onDone: (() -> Unit)? = null,
    fallback: @Composable () -> Unit,
) {
    // the page's view models (the live connection) live exactly as long as the page: they are cleared when it leaves
    val owner = rememberPageViewModelStoreOwner(tabId)
    CompositionLocalProvider(LocalViewModelStoreOwner provides owner) {
        LiveTabContent(tabId, tab, onBack, onCloseTab, takeControl, onDone, fallback)
    }
}

@Composable
private fun LiveTabContent(
    tabId: String,
    tab: BrowserTab?,
    onBack: () -> Unit,
    onCloseTab: (() -> Unit)?,
    takeControl: Boolean,
    onDone: (() -> Unit)?,
    fallback: @Composable () -> Unit,
) {
    val c = Kelo.colors
    val network = LocalNetworkInfo.current
    val pointer = LocalFormFactor.current == FormFactor.Desktop
    val api = LocalAppServices.current.sessions.api.collectAsStateWithLifecycle().value ?: return
    val scope = rememberCoroutineScope()
    val haptics = LocalHaptics.current
    val clipboard = LocalClipboardManager.current
    // the connection lives in the view model (one per tab): it closes when the page is gone for good
    val client: LiveViewModel = viewModel(key = "live-$tabId") { liveViewModel(api, tabId, network) }
    // the VNC screen (Android only: a host is provided): tried first once the page is live and knows its size; the JPEG picture is the
    // fallback. A screen that closed or failed is replaced when the page comes back on screen, so it is tried again.
    val vncHost = LocalVncScreenHost.current
    var vnc by remember(client) { mutableStateOf(vncRelay(api, tabId)) }
    // off screen (another app, the screen off): the connection closes; the tab stays held for a short while
    LifecycleStartEffect(client) {
        client.start()
        if (vnc.state.value != VncState.Idle) vnc = vncRelay(api, tabId)
        onStopOrDispose { client.stop(); vnc.close() }
    }
    val frame by client.frame.collectAsStateWithLifecycle()
    val state by client.state.collectAsStateWithLifecycle()
    val vncState by vnc.state.collectAsStateWithLifecycle()
    val density = LocalDensity.current.density
    var area by remember { mutableStateOf<ViewSize?>(null) }
    LaunchedEffect(vnc, vncHost != null, area, state.mode == LiveMode.ENDED) {
        val a = area
        if (VncRules.shouldConnect(vnc.state.value, vncHost != null, a, state.mode == LiveMode.ENDED)) vnc.connectOnce(a!!)
    }
    LaunchedEffect(vncState) { if (vncHost != null) client.setFrames(VncRules.framesOn(vncState)) }
    // a rotation or split screen: the /vnc socket is opened again at the new size (300 ms after the area settles, the last picture stays
    // meanwhile). The keyboard shortens the area too, but the screen stays and the keyboard covers it: no reconnect for that.
    val opened by vnc.openedWith.collectAsStateWithLifecycle()
    val imeVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
    LaunchedEffect(vnc, vncState, opened, area, imeVisible) {
        val a = area ?: return@LaunchedEffect
        if (vncHost != null && vncState is VncState.Showing) vnc.reconnectIfResized(a, imeVisible)
    }
    val activity = LocalAppServices.current.agentActivity
    // the tab is gone (closed, or the job behind it stopped): the lists that still show it as running load again now
    LaunchedEffect(state.mode, state.end) {
        if (state.mode == LiveMode.ENDED && state.end != LiveEnd.NO_BROWSER) activity.workChanged.update { it + 1 }
    }
    var notice by remember { mutableStateOf<StringResource?>(null) }
    LaunchedEffect(client) { client.notices.collect { notice = it } }
    LaunchedEffect(notice) { if (notice != null) { delay(3_500); notice = null } }
    // the owner's paste into the VNC screen (Ctrl/⌘+V there, or the paste key): the clipboard's text is typed into the page, as the paste key does
    val onPaste: () -> Unit = {
        ClipboardRules.pasteText(clipboard.getText()?.text)?.let { client.type(it); notice = Res.string.live_pasted }
    }

    val controlling = state.status.youControl
    // the live view is always capturable (owner decision): no secure window here, see LiveRules.guardScreen
    // opened to solve a human check: take the tab over once, as soon as the first picture is there and nobody else holds it
    var autoTaken by remember(client) { mutableStateOf(false) }
    LaunchedEffect(client, takeControl, frame != null, vncState, state.status.someoneElse, controlling) {
        if (!LiveRules.autoTake(takeControl, autoTaken, LiveRules.hasPicture(frame != null, vncState), controlling, state.status.someoneElse, state.controlBusy)) return@LaunchedEffect
        autoTaken = true
        when (client.control(true, handover = true)) {   // Kelo asked for this: a hand-over hold (leaving the page gives it back)
            LiveClient.ControlResult.Held -> notice = Res.string.live_held_elsewhere
            LiveClient.ControlResult.Gone -> notice = Res.string.live_tab_closed
            is LiveClient.ControlResult.Failed -> notice = Res.string.live_input_failed
            LiveClient.ControlResult.Ok -> haptics.confirm()
        }
    }
    // leaving the page with the owner's own button hold: say it stays on until they hand it back, then leave
    val leave: () -> Unit = {
        if (LiveRules.leaveNotice(state.status)) {
            notice = Res.string.live_still_yours
            scope.launch { delay(1_400); onBack() }
        } else onBack()
    }
    val header = frame?.header
    val meta by client.meta.collectAsStateWithLifecycle()
    val device = LiveKinds.isDevice(tabId)   // the Android device: a portrait screen, Back / Home / Recents, no tab to close
    val title = if (device) tab?.title?.ifBlank { null } ?: string(Res.string.chat_phone_title)   // the phone's own name when the server says it
        else LiveHeader.title(meta, header) ?: tab?.title?.ifBlank { null } ?: tab?.host?.ifBlank { null } ?: string(Res.string.agent_tab_browser)
    val host = if (device) "" else LiveHeader.url(meta, header)?.let(::hostOf)?.ifBlank { null } ?: tab?.host.orEmpty()

    Column(Modifier.fillMaxSize().background(c.bg).safeDrawingPadding()) {
        LiveTop(title, host, leave, onCloseTab.takeIf { !device && !controlling && state.mode != LiveMode.ENDED })
        if (state.mode != LiveMode.ENDED) LiveStrip(state, tab)
        if (controlling) ControlBanner(busy = state.controlBusy) {
            scope.launch { client.control(false) }
        }
        if (LiveRules.showWorkNote(state.status)) WorkNote()
        Box(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 8.dp)) {
          Box(Modifier.fillMaxSize().onSizeChanged { area = ViewSize(it.width, it.height, density) }) {
            when {
                state.mode == LiveMode.ENDED && state.end != LiveEnd.NO_BROWSER -> Column(
                    Modifier.fillMaxSize().padding(horizontal = 4.dp), verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    LiveNote(string(Res.string.live_tab_closed))
                    Box(Modifier.weight(1f)) { fallback() }
                }
                state.mode == LiveMode.ENDED -> fallback()
                else -> {
                    val host = vncHost
                    if (host != null && VncRules.showsVnc(vncState)) host.Screen(vnc, controlling, onPaste, Modifier.fillMaxSize())
                    else LiveCanvas(client, frame, controlling, device, onTapped = { haptics.tick() })
                }
            }
          }
            notice?.let { n ->
                Text(
                    string(n), color = c.bg, style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp).clip(RoundedCornerShape(12.dp)).background(c.ink)
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                )
            }
        }
        // the desktop's VNC screen: the keys go to the page while the owner holds it; composed text (Korean, Japanese, Chinese) goes in the line below
        if (pointer && controlling && vncHost != null && VncRules.showsVnc(vncState)) Text(
            string(Res.string.desktop_live_type_hint), style = MaterialTheme.typography.bodySmall, color = c.muted,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp),
        )
        if (pointer) {
            val key = if (System.getProperty("os.name").orEmpty().lowercase().contains("mac")) "⌘" else "Ctrl"
            Text(string(Res.string.desktop_chat_zoom_hint, key), style = MaterialTheme.typography.bodySmall, color = c.muted,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp))
        }
        // the phone was handed over to the owner (a card waits for "Done"): the same answer as the card's button, always in reach
        if (onDone != null) PrimaryButton(string(Res.string.live_done), modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp), onClick = { haptics.confirm(); onDone() })
        if (controlling) {
            // text the browser copied (VNC screen only): a chip above the keys; the owner taps it to take it, nothing is copied on its own
            val offered by vnc.clipboard.collectAsStateWithLifecycle()
            if (vncHost != null && VncRules.showsVnc(vncState)) offered?.let { text ->
                CopyChip(string(Res.string.live_copy_from_browser)) {
                    clipboard.setText(AnnotatedString(text))
                    vnc.clearClipboard()
                    notice = ClipboardRules.words(pointer).copied
                }
            }
            ControlBar(client, device, editing = LiveHeader.editing(meta, header), canPaste = vncHost != null, desktop = pointer, keysGoToScreen = pointer && VncRules.showsVnc(vncState), onNotice = { notice = it })
        }
        else if (state.mode != LiveMode.ENDED) {
            val other = state.status.someoneElse
            Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                // a tab another device holds is taken over only when that device has no live connection any more (the server moves it)
                PrimaryButton(
                    string(if (other && !state.status.holderAway) Res.string.live_held_elsewhere else Res.string.live_take),
                    enabled = (!other || state.status.holderAway) && !state.controlBusy && LiveRules.hasPicture(frame != null, vncState),
                    modifier = Modifier.weight(1f),
                    onClick = {
                        scope.launch {
                            when (client.control(true)) {
                                LiveClient.ControlResult.Held -> notice = Res.string.live_held_elsewhere
                                LiveClient.ControlResult.Gone -> notice = Res.string.live_tab_closed
                                is LiveClient.ControlResult.Failed -> notice = Res.string.live_input_failed
                                LiveClient.ControlResult.Ok -> haptics.confirm()
                            }
                        }
                    },
                )
            }
        }
    }
}

@Composable
private fun LiveTop(title: String, host: String, onBack: () -> Unit, onCloseTab: (() -> Unit)?) {
    val c = Kelo.colors
    val back = string(Res.string.back)
    Row(
        Modifier.fillMaxWidth().padding(start = 8.dp, end = 8.dp, top = 6.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onBack) { Icon(KeloIcons.ChevronLeft, contentDescription = back, tint = c.ink) }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (host.isNotBlank()) Text(host, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (onCloseTab != null) {
            IconButton(onClick = onCloseTab) { Icon(KeloIcons.Trash, contentDescription = string(Res.string.agent_tab_close), tint = c.muted) }
        }
    }
}

/** What the agent is doing, who is watching, who holds the tab. */
@Composable
private fun LiveStrip(state: LiveState, tab: BrowserTab?) {
    val c = Kelo.colors
    val s = state.status
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        val (word, color) = when {
            s.youControl -> string(Res.string.live_state_you) to c.accent
            s.someoneElse -> string(Res.string.live_state_other) to c.muted
            s.agent.waitingApproval -> string(Res.string.live_state_waiting) to c.accent
            s.agent.busy || tab?.status == "working" -> string(Res.string.agent_tab_working) to c.accent
            else -> string(Res.string.agent_tab_idle) to c.muted
        }
        if (s.inWorkBrowser) WorkBrowserPill()   // the work pill first; the state pill follows
        Pill(word, color)
        val line = when (state.mode) {
            LiveMode.CONNECTING -> string(Res.string.live_connecting)
            LiveMode.WAITING -> string(Res.string.live_reconnecting)
            else -> s.label.ifBlank { s.agent.status.orEmpty() }.ifBlank { tab?.statusText.orEmpty() }
        }
        if (line.isNotBlank()) Text(line, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.widthIn(max = 220.dp))
        if (state.mode == LiveMode.POLLING) Pill(string(Res.string.live_slow_mode), c.muted)
        if (s.viewers > 0) {
            val desc = plural(Res.plurals.live_viewers, s.viewers, s.viewers)
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.semantics(mergeDescendants = true) { contentDescription = desc }) {
                Icon(KeloIcons.Eye, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
                Text(" ${s.viewers}", style = MaterialTheme.typography.labelSmall, color = c.muted)
            }
        }
    }
}

@Composable
private fun Pill(text: String, color: androidx.compose.ui.graphics.Color) {
    Text(
        text, style = MaterialTheme.typography.labelSmall, color = color, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
        modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(color.copy(alpha = 0.12f)).padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

@Composable
private fun LiveNote(text: String) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(c.card).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(KeloIcons.Alert, contentDescription = null, tint = c.muted, modifier = Modifier.size(20.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f))
    }
}

/** While the user holds the tab: the agent is paused, and one tap gives it back. */
@Composable
private fun ControlBanner(busy: Boolean, onGiveBack: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp).clip(RoundedCornerShape(14.dp))
            .background(c.accent.copy(alpha = 0.14f)).padding(start = 14.dp, end = 6.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(KeloIcons.Hand, contentDescription = null, tint = c.accent, modifier = Modifier.size(20.dp))
        Text(string(Res.string.live_paused_banner), style = MaterialTheme.typography.bodySmall, color = c.ink, modifier = Modifier.weight(1f))
        Text(
            string(Res.string.live_give_back), color = c.bg, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(if (busy) c.muted else c.ink)
                .clickable(enabled = !busy, role = Role.Button, onClick = onGiveBack).padding(horizontal = 14.dp, vertical = 8.dp),
        )
    }
}

/** Under the paused banner, while the owner holds a tab of the work browser: where their sign-in goes. */
@Composable
private fun WorkNote() {
    val c = Kelo.colors
    Text(
        string(Res.string.live_work_browser_note), style = MaterialTheme.typography.bodySmall, color = c.muted,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 2.dp),
    )
}

/**
 * The picture. Watching: pinch to zoom (1x to 5x), drag to move, double tap to zoom in or out. Controlling: a tap or a long press
 * goes to that point of the page and a drag scrolls it (sent at most every 50 ms); the zoom stays as it was and is accounted for.
 * With a pointer (the desktop) the zoom also has buttons and the wheel with Ctrl (⌘ on a Mac) held, and the plain wheel scrolls the
 * page while controlling. The zoom and pan are the view model's.
 */
@Composable
fun LiveCanvas(
    client: LiveViewModel,
    frame: LiveFrame?,
    controlling: Boolean = false,
    device: Boolean = false,
    onTapped: () -> Unit = {},
) {
    val c = Kelo.colors
    val pointer = LocalFormFactor.current == FormFactor.Desktop
    var size by remember { mutableStateOf(IntSize.Zero) }
    val zoom by client.zoom.collectAsStateWithLifecycle()
    val pan by client.pan.collectAsStateWithLifecycle()
    val current by rememberUpdatedState(frame)
    val zoomNow by rememberUpdatedState(zoom)
    val panNow by rememberUpdatedState(pan)
    // drags add up here and leave as one scroll every 50 ms
    val pending = remember { floatArrayOf(0f, 0f, 0f, 0f) }   // dx, dy, at x, at y (view pixels)
    LaunchedEffect(controlling) {
        while (controlling) {
            delay(50)
            if (pending[0] == 0f && pending[1] == 0f) continue
            val w = size.width.toFloat(); val h = size.height.toFloat()
            val dx = pending[0]; val dy = pending[1]
            pending[0] = 0f; pending[1] = 0f
            client.scroll(dx, dy, pending[2], pending[3], w, h)
        }
    }
    // the live area's size goes to the server while controlling: when it starts, after a rotation, when the soft keyboard moves the
    // picture (a short wait so one keyboard animation is one message). Not controlling: nothing is wanted (the page gets its size back).
    val density = LocalDensity.current.density
    LaunchedEffect(controlling, size, density, frame?.header?.liveV) {
        if (!controlling || size.width <= 0 || size.height <= 0) { client.reportView(null); return@LaunchedEffect }
        delay(150)
        client.reportView(ViewSize(size.width, size.height, density))
    }
    val touch by client.touch.collectAsStateWithLifecycle()
    val markFade = remember { Animatable(1f) }
    LaunchedEffect(touch.mark?.id) {
        val m = touch.mark ?: return@LaunchedEffect
        markFade.snapTo(0f)
        markFade.animateTo(1f, tween(durationMillis = TouchMarkLook.fadeMs(m), easing = LinearEasing))
    }
    val desc = string(Res.string.agent_tab_picture)
    val zoomKeyIsMeta = remember { System.getProperty("os.name").orEmpty().lowercase().contains("mac") }

    val pointerState by client.pointer.collectAsStateWithLifecycle()
    var showPointer by remember { mutableStateOf(false) }
    LaunchedEffect(pointerState) {
        if (pointerState != null) {
            showPointer = true
            delay(10_000)
            showPointer = false
        } else {
            showPointer = false
        }
    }
    val pointerVisible = showPointer && !controlling

    val f = frame
    val targetOffset = if (pointerState != null && f != null && size.width > 0 && size.height > 0) {
        LiveGeometry.pointerToView(
            cssX = pointerState!!.x, cssY = pointerState!!.y,
            viewW = size.width.toFloat(), viewH = size.height.toFloat(),
            frameW = f.header.w, frameH = f.header.h,
            cssW = f.header.cssW, cssH = f.header.cssH,
            zoom = zoom, panX = pan.x, panY = pan.y,
        )
    } else null

    val posAnim = remember { Animatable(Offset.Zero, Offset.VectorConverter) }
    var hasPos by remember { mutableStateOf(false) }
    LaunchedEffect(targetOffset) {
        if (targetOffset != null) {
            if (!hasPos) {
                posAnim.snapTo(targetOffset)
                hasPos = true
            } else {
                posAnim.animateTo(targetOffset, tween(durationMillis = 80, easing = LinearEasing))
            }
        } else {
            hasPos = false
        }
    }

    val rippleAnim = remember { Animatable(0f) }
    LaunchedEffect(pointerState?.down) {
        if (pointerState?.down == true) {
            rippleAnim.snapTo(0f)
            rippleAnim.animateTo(1f, tween(durationMillis = 350, easing = FastOutSlowInEasing))
        }
    }

    val state by client.state.collectAsStateWithLifecycle()
    val isBusy = state.isPageBusy

    Box(
        Modifier.fillMaxSize().clip(RoundedCornerShape(12.dp)).background(c.bubble).onSizeChanged { size = it }
            .then(
                if (controlling) Modifier.pointerInput("control", device) {
                    // tap = at the DOWN point; long press; one finger drags (scrolls the page / swipes the phone); two fingers pinch
                    // and pan the picture. The finger's ring shows at once.
                    val machine = TouchMachine(TouchRules.SLOP_DP * density, longPress = !device)
                    val clock = kotlin.time.TimeSource.Monotonic.markNow()
                    fun now() = clock.elapsedNow().inWholeMilliseconds
                    fun apply(a: TouchAction?) {
                        val w = size.width.toFloat(); val h = size.height.toFloat()
                        when (a) {
                            is TouchAction.Tap -> if (client.tap(a.at, w, h)) onTapped()
                            is TouchAction.LongPress -> if (client.tap(a.at, w, h, longPress = true)) onTapped()
                            is TouchAction.Drag -> {
                                if (!device) { pending[0] += a.dx; pending[1] += a.dy; pending[2] = a.at.x; pending[3] = a.at.y }
                            }
                            is TouchAction.DragEnd -> if (device) client.swipe(a.from, a.to, a.ms, w, h)
                            null -> {}
                        }
                    }
                    awaitEachGesture {
                        val first = awaitFirstDown(requireUnconsumed = false)
                        machine.down(first.position, now())
                        client.fingerDown(first.position)
                        try {
                            do {
                                val wait = machine.longPressIn(now())
                                val ev = if (wait != null) withTimeoutOrNull(wait) { awaitPointerEvent() } else awaitPointerEvent()
                                if (ev == null) { apply(machine.tick(now())); continue }
                                val down = ev.changes.filter { it.pressed }
                                if (down.size >= 2) {
                                    if (!machine.multi) { machine.second(); client.fingerUp() }
                                    client.transform(ev.calculateZoom(), ev.calculatePan(), size.width.toFloat(), size.height.toFloat(), ev.calculateCentroid())
                                    ev.changes.forEach { it.consume() }
                                } else if (machine.oneFinger) {
                                    val ch = ev.changes.first()
                                    if (ch.pressed) {
                                        val act = machine.move(ch.position, now())
                                        if (act != null) { apply(act); ch.consume() }
                                    } else {
                                        client.fingerUp()
                                        apply(machine.up(ch.position, now()))
                                        ch.consume()
                                    }
                                }
                            } while (ev == null || ev.changes.any { it.pressed })
                        } finally {
                            machine.reset()
                            client.fingerUp()
                        }
                    }
                }
                else Modifier
                    .pointerInput("zoom") {
                        detectTransformGestures { _, panBy, zoomBy, _ ->
                            client.transform(zoomBy, panBy, size.width.toFloat(), size.height.toFloat())
                        }
                    }
                    .pointerInput("double") {
                        detectTapGestures(onDoubleTap = { client.toggleZoom(size.width.toFloat(), size.height.toFloat()) })
                    },
            )
            // the pointer's wheel: with the zoom key it zooms; else it moves a zoomed picture, or scrolls the page while controlling
            .then(if (pointer) Modifier.pointerInput("wheel", controlling) {
                awaitPointerEventScope {
                    while (true) {
                        val e = awaitPointerEvent()
                        if (e.type != PointerEventType.Scroll) continue
                        val ch = e.changes.firstOrNull() ?: continue
                        val mods = e.keyboardModifiers
                        val w = size.width.toFloat(); val h = size.height.toFloat()
                        if (if (zoomKeyIsMeta) mods.isMetaPressed else mods.isCtrlPressed) {
                            client.zoomTo(zoomNow * (if (ch.scrollDelta.y < 0) LiveViewModel.WHEEL_STEP else 1f / LiveViewModel.WHEEL_STEP), w, h)
                            ch.consume()
                            continue
                        }
                        val f = current ?: continue
                        if (controlling && device) continue   // the device is not scrolled by a wheel
                        if (!controlling) {
                            if (zoomNow > 1.01f) {
                                client.transform(1f, Offset(-ch.scrollDelta.x * 60f, -ch.scrollDelta.y * 60f), w, h)
                                ch.consume()
                            }
                            continue
                        }
                        // one wheel notch is about 100 CSS pixels of the page (sent in frame pixels); the page moves the way the wheel turns
                        val step = 100f * (if (f.header.cssW > 0) f.header.w.toFloat() / f.header.cssW else 1f) / zoomNow
                        val d = FramePoint((ch.scrollDelta.x * step).roundToInt(), (ch.scrollDelta.y * step).roundToInt())
                        val at = LiveGeometry.toFrame(ch.position.x, ch.position.y, w, h, f.header.w, f.header.h, zoomNow, panNow.x, panNow.y)
                        client.send(LiveMessages.scroll(d, at, f.header.w, f.header.h, LiveProtocol.seqFor(f.header)))
                        ch.consume()
                    }
                }
            } else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        if (f == null) {
            CircularProgressIndicator(color = c.muted)
        } else {
            Image(
                f.image, contentDescription = desc, contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize().graphicsLayer { scaleX = zoom; scaleY = zoom; translationX = pan.x; translationY = pan.y },
            )
        }
        if (pointerVisible && hasPos && targetOffset != null) {
            val density = LocalDensity.current
            Canvas(Modifier.fillMaxSize()) {
                val pos = posAnim.value
                val rVal = rippleAnim.value
                if (rVal > 0f && rVal < 1f) {
                    val rippleRadius = (6f + 16f * rVal) * density.density
                    val rippleAlpha = (1f - rVal) * 0.7f
                    drawCircle(
                        color = Color.Black.copy(alpha = rippleAlpha * 0.4f),
                        radius = rippleRadius + 1.5f * density.density,
                        center = pos,
                        style = Stroke(width = 2.5f * density.density),
                    )
                    drawCircle(
                        color = Color.White.copy(alpha = rippleAlpha),
                        radius = rippleRadius,
                        center = pos,
                        style = Stroke(width = 1.5f * density.density),
                    )
                }
                val scale = density.density
                val cursorPath = Path().apply {
                    moveTo(pos.x, pos.y)
                    lineTo(pos.x, pos.y + 16f * scale)
                    lineTo(pos.x + 4.5f * scale, pos.y + 12.5f * scale)
                    lineTo(pos.x + 7.5f * scale, pos.y + 18.5f * scale)
                    lineTo(pos.x + 10f * scale, pos.y + 17.5f * scale)
                    lineTo(pos.x + 7f * scale, pos.y + 11.5f * scale)
                    lineTo(pos.x + 12f * scale, pos.y + 11.5f * scale)
                    close()
                }
                drawPath(
                    cursorPath,
                    color = Color(0xFF1E1E1E),
                    style = Stroke(width = 2.5f * scale, join = StrokeJoin.Round, cap = StrokeCap.Round),
                )
                drawPath(
                    cursorPath,
                    color = Color.White,
                    style = Fill,
                )
            }
        }
        // the owner's own touch (only while controlling: the agent's pointer shows when not)
        if (controlling) TouchMarksLayer(touch.down, touch.mark, markFade.value, c.accent)
        if (isBusy) {
            Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.22f)))
            PageBusyBanner(
                canReload = !device,
                onReload = { client.send(LiveMessages.nav("reload")) },
                modifier = Modifier.align(Alignment.TopCenter).padding(top = 10.dp, start = 12.dp, end = 12.dp),
            )
        }
        if (pointer) LiveZoomButtons(zoom, Modifier.align(Alignment.BottomEnd),
            onOut = { client.zoomTo(zoom / LiveViewModel.BUTTON_STEP, size.width.toFloat(), size.height.toFloat()) },
            onIn = { client.zoomTo(zoom * LiveViewModel.BUTTON_STEP, size.width.toFloat(), size.height.toFloat()) },
            onReset = { client.resetZoom() })
    }
}

/** The pointer's zoom buttons in a corner of the picture: out, the level (a click fits the window), in; with the wheel's hint under. */
@Composable
fun LiveZoomButtons(zoom: Float, modifier: Modifier, onOut: () -> Unit, onIn: () -> Unit, onReset: () -> Unit) {
    val c = Kelo.colors
    val zoomInWords = string(Res.string.desktop_chat_zoom_in)
    val zoomResetWords = string(Res.string.desktop_chat_zoom_reset)
    Row(
        modifier.padding(8.dp).clip(RoundedCornerShape(99.dp)).background(c.card.copy(alpha = 0.92f))
            .border(1.dp, c.line, RoundedCornerShape(99.dp)).padding(horizontal = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onOut, enabled = zoom > 1.01f) { Text("−", color = c.ink, style = MaterialTheme.typography.titleLarge) }
        Text("${(zoom * 100).roundToInt()}%", color = c.ink, style = MaterialTheme.typography.labelMedium,
            modifier = Modifier.pointerHoverIcon(PointerIcon.Hand).clickable(enabled = zoom > 1.01f, onClick = onReset).padding(horizontal = 4.dp)
                .semantics { contentDescription = zoomResetWords })
        IconButton(onClick = onIn, enabled = zoom < LiveGeometry.MAX_ZOOM - 0.01f) {
            Text("+", color = c.ink, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { contentDescription = zoomInWords })
        }
    }
}

@Composable
private fun PageBusyBanner(canReload: Boolean, onReload: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(12.dp))
            .background(c.card.copy(alpha = 0.95f))
            .border(1.dp, c.line, RoundedCornerShape(12.dp))
            .padding(start = 12.dp, end = if (canReload) 4.dp else 12.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(KeloIcons.Alert, contentDescription = null, tint = c.accent, modifier = Modifier.size(16.dp))
        Text(
            string(Res.string.live_page_not_responding),
            style = MaterialTheme.typography.bodySmall,
            color = c.ink,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f, fill = false),
        )
        if (canReload) {
            IconButton(onClick = onReload, modifier = Modifier.size(28.dp)) {
                Icon(KeloIcons.Retry, contentDescription = string(Res.string.live_reload), tint = c.ink, modifier = Modifier.size(16.dp))
            }
        }
    }
}

/** Keys, back / forward / reload, and a line to type into the page (nothing typed is kept or logged). */
@Composable
private fun ControlBar(client: LiveViewModel, device: Boolean, editing: Boolean, canPaste: Boolean, desktop: Boolean, keysGoToScreen: Boolean, onNotice: (StringResource) -> Unit) {
    val c = Kelo.colors
    // a text field took focus on the page / phone (the server says so): the keyboard comes up, once per focus
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    var wasEditing by remember { mutableStateOf(false) }
    LaunchedEffect(editing) {
        if (EditingRule.focusTypeLine(wasEditing, editing, keysGoToScreen)) { runCatching { focus.requestFocus() }; keyboard?.show() }
        wasEditing = editing
    }
    var text by remember { mutableStateOf("") }
    var hidden by remember { mutableStateOf(false) }
    fun sendText() { if (text.isNotEmpty()) { client.type(text); text = "" } }
    // the phone's clipboard, typed into the page on a tap (never read on its own); the key is off while there is nothing on it
    val clipboard = LocalClipboardManager.current
    var clipHasText by remember { mutableStateOf(false) }
    if (canPaste) LaunchedEffect(Unit) { while (true) { clipHasText = clipboard.hasText(); delay(1_000) } }
    fun paste() {
        val clip = ClipboardRules.pasteText(clipboard.getText()?.text) ?: return
        client.type(clip)
        onNotice(Res.string.live_pasted)
    }
    Column(Modifier.fillMaxWidth().background(c.card).padding(horizontal = 10.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (device) PhoneKeys { client.send(LiveMessages.deviceKey(it)) }
        else Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            NavKey(KeloIcons.ArrowLeft, Res.string.live_back) { client.send(LiveMessages.nav("back")) }
            NavKey(KeloIcons.ArrowRight, Res.string.live_forward) { client.send(LiveMessages.nav("forward")) }
            NavKey(KeloIcons.Retry, Res.string.live_reload) { client.send(LiveMessages.nav("reload")) }
            KEY_LABELS.forEach { (key, label, desc) -> TextKey(string(label), string(desc)) { client.send(LiveMessages.key(key)) } }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            KeloTextField(
                text, { text = it }, string(if (device) Res.string.live_phone_type_hint else Res.string.live_type_hint), modifier = Modifier.weight(1f).focusRequester(focus), password = hidden,
                maxLength = LiveMessages.MAX_TEXT,
                keyboardOptions = KeyboardOptions(
                    autoCorrectEnabled = false, imeAction = ImeAction.Send,
                    keyboardType = if (hidden) KeyboardType.Password else KeyboardType.Text,
                ),
                keyboardActions = KeyboardActions(onSend = { sendText() }),
            )
            if (canPaste) IconButton(onClick = { paste() }, enabled = clipHasText, modifier = Modifier.size(40.dp)) {
                Icon(KeloIcons.Paste, contentDescription = string(ClipboardRules.words(desktop).paste), tint = if (clipHasText) c.ink else c.muted)
            }
            IconButton(onClick = { hidden = !hidden }) {
                Icon(if (hidden) KeloIcons.Lock else KeloIcons.Eye, contentDescription = string(if (hidden) Res.string.live_type_show else Res.string.live_type_hide), tint = c.muted)
            }
            IconButton(onClick = { sendText() }, enabled = text.isNotEmpty()) {
                Icon(KeloIcons.Send, contentDescription = string(Res.string.live_type_send), tint = if (text.isNotEmpty()) c.accent else c.muted)
            }
        }
    }
}

/** The browser's copied text, offered above the keys: wraps in any language, never wider than the screen. */
@Composable
private fun CopyChip(label: String, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().background(c.card).padding(horizontal = 10.dp, vertical = 6.dp)) {
        Row(
            Modifier.heightIn(min = 40.dp).clip(RoundedCornerShape(20.dp)).background(c.bubble)
                .clickable(role = Role.Button, onClickLabel = label, onClick = onClick).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(KeloIcons.Copy, contentDescription = null, tint = c.ink, modifier = Modifier.size(18.dp))
            Text(label, style = MaterialTheme.typography.labelLarge, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
        }
    }
}

private val KEY_LABELS = listOf(
    Triple("Enter", Res.string.live_key_enter, Res.string.live_key_enter_desc),
    Triple("Backspace", Res.string.live_key_backspace, Res.string.live_key_backspace_desc),
    Triple("Tab", Res.string.live_key_tab, Res.string.live_key_tab_desc),
    Triple("Escape", Res.string.live_key_escape, Res.string.live_key_escape_desc),
    Triple("ArrowLeft", Res.string.live_key_left, Res.string.live_key_left_desc),
    Triple("ArrowUp", Res.string.live_key_up, Res.string.live_key_up_desc),
    Triple("ArrowDown", Res.string.live_key_down, Res.string.live_key_down_desc),
    Triple("ArrowRight", Res.string.live_key_right, Res.string.live_key_right_desc),
)

/** The device's Back / Home / Recents under its screen: an icon and its word each (the word is also what a screen reader says). */
@Composable
private fun PhoneKeys(onKey: (String) -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf(
            Triple(LiveKinds.PHONE_KEYS[0], KeloIcons.ArrowLeft, Res.string.live_phone_back),
            Triple(LiveKinds.PHONE_KEYS[1], KeloIcons.Home, Res.string.live_phone_home),
            Triple(LiveKinds.PHONE_KEYS[2], KeloIcons.Recents, Res.string.live_phone_recents),
        ).forEach { (key, icon, label) ->
            val words = string(label)
            Row(
                Modifier.weight(1f).height(44.dp).clip(RoundedCornerShape(12.dp)).background(c.bubble)
                    .clickable(role = Role.Button, onClickLabel = words) { onKey(key) }.semantics { contentDescription = words },
                horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(icon, contentDescription = null, tint = c.ink, modifier = Modifier.size(20.dp))
                Text(" $words", style = MaterialTheme.typography.labelLarge, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
private fun NavKey(icon: ImageVector, desc: StringResource, onClick: () -> Unit) {
    val c = Kelo.colors
    Box(
        Modifier.size(40.dp).clip(RoundedCornerShape(10.dp)).background(c.bubble).clickable(role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Icon(icon, contentDescription = string(desc), tint = c.ink, modifier = Modifier.size(20.dp)) }
}

@Composable
private fun TextKey(label: String, desc: String, onClick: () -> Unit) {
    val c = Kelo.colors
    Box(
        Modifier.size(height = 40.dp, width = 52.dp).clip(RoundedCornerShape(10.dp)).background(c.bubble)
            .clickable(role = Role.Button, onClickLabel = desc, onClick = onClick).semantics { contentDescription = desc },
        contentAlignment = Alignment.Center,
    ) { Text(label, style = MaterialTheme.typography.labelLarge, color = c.ink, maxLines = 1) }
}

private fun hostOf(url: String): String = runCatching { URI(url).host.orEmpty().removePrefix("www.") }.getOrDefault("")
