package chat.kelo.desktop.pages

import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.input.pointer.PointerEventType
import androidx.compose.ui.input.pointer.isMetaPressed
import androidx.compose.ui.input.pointer.isCtrlPressed
import androidx.compose.ui.input.pointer.onPointerEvent
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.FrameHeader
import chat.kelo.app.agent.FramePoint
import chat.kelo.app.agent.LiveEnd
import chat.kelo.app.agent.LiveGeometry
import chat.kelo.app.agent.LiveMachine
import chat.kelo.app.agent.LiveMessages
import chat.kelo.app.agent.LiveMode
import chat.kelo.app.agent.LiveQuality
import chat.kelo.app.agent.LiveRules
import chat.kelo.app.agent.LiveStatus
import chat.kelo.app.agent.LiveStep
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Response
import java.io.IOException
import java.net.URLDecoder
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.roundToInt

/** What the live view shows: the newest picture, who holds the tab, and how the connection is. */
data class LiveViewState(
    val frame: ImageBitmap? = null,
    val header: FrameHeader? = null,
    val status: LiveStatus = LiveStatus(),
    val mode: LiveMode = LiveMode.CONNECTING,
    val end: LiveEnd? = null,
    /** A control request is on its way */
    val controlBusy: Boolean = false,
    /** A short line for the user (a string resource): "another device is controlling", "the browser could not do that" */
    val notice: Int? = null,
)

/**
 * The live view of one browser tab (server.py "LIVE VIEW API") for the desktop app. It fetches the pictures one at a time
 * (GET …/frame?after=&wait=10, with the bearer token) rather than over the WebSocket, takes and hands back control
 * (POST …/control) and sends clicks, wheel scrolls, keys and typed text as POST …/input. The reconnect and end rules are the
 * phone's (LiveMachine). Typed text is never logged or kept.
 */
class LiveSession(private val api: KeloApi, tabId: String, private val scope: CoroutineScope, private val cleanup: CoroutineScope) {
    private val path = "/api/browser/sessions/${KeloApi.segment(tabId)}"
    private val machine = LiveMachine()
    private val quality = LiveQuality.WIFI
    private val inputLock = Mutex()
    private val _state = MutableStateFlow(LiveViewState())
    val state: StateFlow<LiveViewState> = _state.asStateFlow()
    private var job: Job? = null
    private var lastSeq = 0L

    fun start() {
        if (job != null) return
        job = scope.launch { pollLoop() }
    }

    /** Stops fetching; hands the tab back to the agent when this view held it. */
    fun close() {
        job?.cancel(); job = null
        val held = _state.value.status.youControl
        cleanup.launch {
            if (held) runCatching { api.post<JsonElement>("$path/control", LiveMessages.control(false)) }
            runCatching { api.delete<JsonElement>("$path/frame") }
        }
    }

    fun clearNotice() = _state.update { it.copy(notice = null) }

    private suspend fun pollLoop() {
        var failures = 0
        while (true) {
            val (code, headers, body) = try {
                fetch("$path/frame?after=$lastSeq&wait=10&${quality.query()}")
            } catch (e: IOException) {
                Triple(0, null, null)
            }
            failures = if (code == 200 || code == 204) 0 else failures + 1
            if (headers != null && code in 200..299) {
                val you = headers("X-You-Control") == "1"
                val controller = headers("X-Controller") ?: "agent"
                _state.update { it.copy(status = it.status.copy(youControl = you, controller = controller)) }
            }
            if (code == 200 && headers != null && body != null) {
                val h = FrameHeader(
                    seq = headers("X-Frame-Seq")?.toLongOrNull() ?: (lastSeq + 1),
                    w = headers("X-Frame-W")?.toIntOrNull() ?: 0,
                    h = headers("X-Frame-H")?.toIntOrNull() ?: 0,
                    url = decode(headers("X-Frame-Url")),
                    title = decode(headers("X-Frame-Title")),
                    bytes = body.size,
                    cssW = headers("X-Frame-Css-W")?.toIntOrNull() ?: 0,
                    cssH = headers("X-Frame-Css-H")?.toIntOrNull() ?: 0,
                )
                lastSeq = h.seq
                val bmp = withContext(Dispatchers.Default) {
                    runCatching { org.jetbrains.skia.Image.makeFromEncoded(body).toComposeImageBitmap() }.getOrNull()
                }
                if (bmp != null) _state.update { it.copy(frame = bmp, header = h) }
            }
            if (code == 401) return
            when (val next = machine.polled(code, failures)) {
                is LiveStep.End -> { _state.update { it.copy(mode = LiveMode.ENDED, end = next.reason) }; return }
                is LiveStep.Poll -> {
                    _state.update { it.copy(mode = if (it.frame != null) LiveMode.POLLING else it.mode) }
                    if (next.delayMs > 0) delay(next.delayMs)
                }
                else -> {}
            }
        }
    }

    enum class ControlResult { OK, HELD, GONE, FAILED }

    suspend fun control(take: Boolean): ControlResult {
        _state.update { it.copy(controlBusy = true) }
        return try {
            val o = api.post<JsonObject>("$path/control", LiveMessages.control(take))
            _state.update { it.copy(status = LiveMessages.controlAnswer(o, it.status)) }
            ControlResult.OK
        } catch (e: ApiError) {
            when (e.status) {
                409 -> ControlResult.HELD
                404 -> ControlResult.GONE
                else -> ControlResult.FAILED
            }
        } finally {
            _state.update { it.copy(controlBusy = false) }
        }
    }

    /** One input event (tap, scroll, key, text, back…), in order. */
    fun send(event: JsonObject) {
        scope.launch {
            inputLock.withLock {
                try {
                    api.post<JsonElement>("$path/input", event)
                } catch (e: ApiError) {
                    val notice = when {
                        e.status == 409 -> R.string.live_not_controller
                        e.status == 422 || e.status == 502 || e.isNetwork -> R.string.live_input_failed
                        else -> null
                    }
                    if (e.status == 409) _state.update { it.copy(status = it.status.copy(youControl = false)) }
                    if (notice != null) _state.update { it.copy(notice = notice) }
                }
            }
        }
    }

    fun notice(id: Int) = _state.update { it.copy(notice = id) }

    private suspend fun fetch(p: String): Triple<Int, ((String) -> String?)?, ByteArray?> = suspendCancellableCoroutine { cont ->
        val call = api.client.newCall(api.requestBuilder(p).get().build())
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) { cont.resumeWithException(e) }
            override fun onResponse(call: Call, response: Response) {
                try {
                    response.use { r ->
                        val bytes = if (r.code == 200) r.body?.bytes() else null
                        val hs = r.headers
                        cont.resume(Triple(r.code, { name: String -> hs[name] }, bytes))
                    }
                } catch (e: IOException) {
                    cont.resumeWithException(e)
                }
            }
        })
    }

    private fun decode(v: String?): String = runCatching { URLDecoder.decode(v.orEmpty(), "UTF-8") }.getOrDefault("")
}

/**
 * A browser tab live, over the page (opened by "solve it" on a human check, so it takes the tab over once, as soon as the first
 * picture is there and nobody else holds it). Click to tap, the mouse wheel scrolls, and the row under the picture types text and
 * sends keys. Closing it hands the tab back to the agent.
 */
@OptIn(ExperimentalComposeUiApi::class, ExperimentalLayoutApi::class)
@Composable
fun LivePane(app: AppContainer, tabId: String, takeControl: Boolean, onClose: () -> Unit, picture: String? = null) {
    val c = Kelo.colors
    val api = app.sessions.api.collectAsState().value ?: return
    val scope = rememberCoroutineScope()
    val session = remember(api, tabId) { LiveSession(api, tabId, scope, app.scope) }
    DisposableEffect(session) {
        session.start()
        onDispose { session.close() }
    }
    val state by session.state.collectAsState()
    val controlling = state.status.youControl
    var autoTaken by remember(session) { mutableStateOf(false) }
    LaunchedEffect(session, takeControl, state.frame != null, state.status.someoneElse, controlling) {
        if (!LiveRules.autoTake(takeControl, autoTaken, state.frame != null, controlling, state.status.someoneElse, state.controlBusy)) return@LaunchedEffect
        autoTaken = true
        when (session.control(true)) {
            LiveSession.ControlResult.HELD -> session.notice(R.string.live_held_elsewhere)
            LiveSession.ControlResult.GONE -> session.notice(R.string.live_tab_closed)
            LiveSession.ControlResult.FAILED -> session.notice(R.string.live_input_failed)
            LiveSession.ControlResult.OK -> {}
        }
    }
    LaunchedEffect(state.notice) { if (state.notice != null) { delay(4_000); session.clearNotice() } }
    var view by remember { mutableStateOf(IntSize.Zero) }
    var zoom by remember(session) { mutableStateOf(1f) }
    var pan by remember(session) { mutableStateOf(Offset.Zero) }
    val zoomInWords = stringResource(R.string.desktop_chat_zoom_in)
    val zoomResetWords = stringResource(R.string.desktop_chat_zoom_reset)
    var typed by remember { mutableStateOf("") }
    val header = state.header
    val title = header?.title?.ifBlank { null } ?: header?.url?.ifBlank { null } ?: stringResource(R.string.agent_tab_browser)

    Column(Modifier.fillMaxSize().background(c.bg).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            IconButton(onClick = onClose) { Icon(KeloIcons.Close, contentDescription = stringResource(R.string.close), tint = c.ink) }
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                val line = when {
                    state.mode == LiveMode.ENDED -> stringResource(R.string.live_tab_closed)
                    controlling -> stringResource(R.string.live_state_you)
                    state.status.someoneElse -> stringResource(R.string.live_state_other)
                    state.frame == null -> stringResource(R.string.live_connecting)
                    else -> null
                }
                if (line != null) MutedText(line, small = true)
            }
            if (state.mode != LiveMode.ENDED) {
                if (controlling) SecondaryButton(stringResource(R.string.live_give_back), enabled = !state.controlBusy, onClick = { scope.launch { session.control(false) } })
                else PrimaryButton(stringResource(R.string.live_take), enabled = !state.controlBusy && state.frame != null && !state.status.someoneElse, onClick = {
                    scope.launch {
                        when (session.control(true)) {
                            LiveSession.ControlResult.HELD -> session.notice(R.string.live_held_elsewhere)
                            LiveSession.ControlResult.GONE -> session.notice(R.string.live_tab_closed)
                            LiveSession.ControlResult.FAILED -> session.notice(R.string.live_input_failed)
                            LiveSession.ControlResult.OK -> {}
                        }
                    }
                })
            }
        }
        if (controlling) MutedText(stringResource(R.string.live_paused_banner), small = true)
        state.notice?.let { Text(stringResource(it), style = MaterialTheme.typography.bodySmall, color = c.warn) }

        // the picture: Ctrl (⌘ on a Mac) + wheel or the buttons zoom (1x to 5x, around the centre), a drag looks around while
        // watching, a double click zooms in or back out. Controlling: a click goes to that point of the page and the wheel scrolls
        // it; the zoom stays as it was and is accounted for (LiveGeometry, as on the phone).
        val mac = remember { System.getProperty("os.name").orEmpty().lowercase().contains("mac") }
        fun zoomTo(z: Float) {
            zoom = LiveGeometry.clampZoom(z)
            val (x, y) = LiveGeometry.clampPan(pan.x, pan.y, zoom, view.width.toFloat(), view.height.toFloat())
            pan = Offset(x, y)
        }
        Box(
            Modifier.weight(1f).fillMaxWidth().clipToBounds().background(c.bubble).onSizeChanged { view = it }
                .pointerInput(header?.w, header?.h, controlling) {
                    detectTapGestures(
                        onDoubleTap = { if (!controlling) { if (zoom > 1.01f) { zoom = 1f; pan = Offset.Zero } else zoomTo(2.5f) } },
                        onTap = { o ->
                            val h = header ?: return@detectTapGestures
                            if (!controlling) return@detectTapGestures
                            LiveGeometry.toFrame(o.x, o.y, view.width.toFloat(), view.height.toFloat(), h.w, h.h, zoom, pan.x, pan.y)?.let { p -> session.send(LiveMessages.tap(p, h.w, h.h)) }
                        },
                    )
                }
                .pointerInput(controlling) {
                    if (controlling) return@pointerInput
                    detectDragGestures { ch, d ->
                        ch.consume()
                        val (x, y) = LiveGeometry.clampPan(pan.x + d.x, pan.y + d.y, zoom, view.width.toFloat(), view.height.toFloat())
                        pan = Offset(x, y)
                    }
                }
                .onPointerEvent(PointerEventType.Scroll) { e ->
                    val ch = e.changes.firstOrNull() ?: return@onPointerEvent
                    val mods = e.keyboardModifiers
                    if (if (mac) mods.isMetaPressed else mods.isCtrlPressed) {
                        zoomTo(zoom * (if (ch.scrollDelta.y < 0) 1.15f else 1f / 1.15f))
                        return@onPointerEvent
                    }
                    val h = header ?: return@onPointerEvent
                    if (!controlling) {
                        if (zoom > 1.01f) {
                            val (x, y) = LiveGeometry.clampPan(pan.x - ch.scrollDelta.x * 60f, pan.y - ch.scrollDelta.y * 60f, zoom, view.width.toFloat(), view.height.toFloat())
                            pan = Offset(x, y)
                        }
                        return@onPointerEvent
                    }
                    // one wheel notch is about 100 CSS pixels of the page (sent in frame pixels); the page moves the way the wheel turns
                    val step = 100f * (if (h.cssW > 0) h.w.toFloat() / h.cssW else 1f) / zoom
                    val d = FramePoint((ch.scrollDelta.x * step).roundToInt(), (ch.scrollDelta.y * step).roundToInt())
                    val at = LiveGeometry.toFrame(ch.position.x, ch.position.y, view.width.toFloat(), view.height.toFloat(), h.w, h.h, zoom, pan.x, pan.y)
                    session.send(LiveMessages.scroll(d, at, h.w, h.h))
                },
            contentAlignment = Alignment.Center,
        ) {
            val f = state.frame
            // a tab that has closed (or not shown yet) keeps the last picture its opener had, when there is one
            val kept = chat.kelo.desktop.chat.rememberDataImage(picture)
            val shown = if (state.mode == LiveMode.ENDED || f == null) kept ?: f else f
            if (shown != null) Image(shown, contentDescription = title, contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize().graphicsLayer { scaleX = zoom; scaleY = zoom; translationX = pan.x; translationY = pan.y })
            else MutedText(stringResource(if (state.mode == LiveMode.ENDED) R.string.live_tab_closed else R.string.live_connecting))
            // zoom buttons in a corner
            Row(
                Modifier.align(Alignment.BottomEnd).padding(8.dp).clip(RoundedCornerShape(99.dp)).background(c.card.copy(alpha = 0.92f))
                    .border(1.dp, c.line, RoundedCornerShape(99.dp)).padding(horizontal = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = { zoomTo(zoom / 1.25f) }, enabled = zoom > 1.01f) { Text("−", color = c.ink, style = MaterialTheme.typography.titleLarge) }
                Text("${(zoom * 100).roundToInt()}%", color = c.ink, style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.clickable(enabled = zoom > 1.01f) { zoom = 1f; pan = Offset.Zero }.padding(horizontal = 4.dp)
                        .semantics { contentDescription = zoomResetWords })
                IconButton(onClick = { zoomTo(zoom * 1.25f) }, enabled = zoom < LiveGeometry.MAX_ZOOM - 0.01f) {
                    Text("+", color = c.ink, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { contentDescription = zoomInWords })
                }
            }
        }
        MutedText(stringResource(R.string.desktop_chat_zoom_hint, if (mac) "⌘" else "Ctrl"), small = true)

        if (controlling) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                ChoiceChip(stringResource(R.string.live_back), selected = false, onClick = { session.send(LiveMessages.nav("back")) })
                ChoiceChip(stringResource(R.string.live_forward), selected = false, onClick = { session.send(LiveMessages.nav("forward")) })
                ChoiceChip(stringResource(R.string.live_reload), selected = false, onClick = { session.send(LiveMessages.nav("reload")) })
                ChoiceChip(stringResource(R.string.live_key_enter), selected = false, onClick = { session.send(LiveMessages.key("Enter")) })
                ChoiceChip(stringResource(R.string.live_key_backspace), selected = false, onClick = { session.send(LiveMessages.key("Backspace")) })
                ChoiceChip(stringResource(R.string.live_key_tab), selected = false, onClick = { session.send(LiveMessages.key("Tab")) })
                ChoiceChip(stringResource(R.string.live_key_escape), selected = false, onClick = { session.send(LiveMessages.key("Escape")) })
                ChoiceChip(stringResource(R.string.live_key_left), selected = false, onClick = { session.send(LiveMessages.key("ArrowLeft")) })
                ChoiceChip(stringResource(R.string.live_key_up), selected = false, onClick = { session.send(LiveMessages.key("ArrowUp")) })
                ChoiceChip(stringResource(R.string.live_key_down), selected = false, onClick = { session.send(LiveMessages.key("ArrowDown")) })
                ChoiceChip(stringResource(R.string.live_key_right), selected = false, onClick = { session.send(LiveMessages.key("ArrowRight")) })
            }
            chat.kelo.desktop.ui.FieldWithAction(
                field = { m -> KeloTextField(typed, { typed = it }, stringResource(R.string.live_type_hint), modifier = m, maxLength = LiveMessages.MAX_TEXT * 4) },
                action = {
                    PrimaryButton(stringResource(R.string.live_type_send), enabled = typed.isNotEmpty(), onClick = {
                        LiveMessages.chunks(typed).forEach { session.send(LiveMessages.text(it)) }
                        typed = ""
                    })
                },
            )
        }
    }
}
