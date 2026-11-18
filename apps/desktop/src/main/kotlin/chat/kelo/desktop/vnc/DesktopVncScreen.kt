package chat.kelo.desktop.vnc

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isMetaPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.key.utf16CodePoint
import androidx.compose.ui.input.pointer.PointerEventType
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.input.pointer.PointerButton
import androidx.compose.ui.input.pointer.PointerIcon
import androidx.compose.ui.input.pointer.pointerHoverIcon
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import chat.kelo.app.agent.LiveGeometry
import chat.kelo.app.agent.LiveZoomButtons
import chat.kelo.app.agent.VncRelay
import chat.kelo.app.agent.VncScreenHost
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.delay
import androidx.compose.ui.layout.ContentScale
import org.jetbrains.skia.ColorAlphaType
import org.jetbrains.skia.ColorType
import org.jetbrains.skia.Image as SkiaImage
import org.jetbrains.skia.ImageInfo
import java.awt.Point
import java.awt.Toolkit
import java.awt.image.BufferedImage

/**
 * The desktop window's VNC screen: the tab's X screen drawn by [DesktopVncSession] (its own RFB client), shown in the live view in place
 * of the JPEG picture. Nothing here is a web view.
 */
object DesktopVncScreenHost : VncScreenHost {
    @Composable
    override fun Screen(relay: VncRelay, holding: Boolean, onPaste: () -> Unit, modifier: Modifier) {
        val scope = rememberCoroutineScope()
        val session = remember(relay) {
            DesktopVncSession(
                scope = scope,
                send = { relay.send(it) },
                offerClipboard = { relay.offerClipboard(it) },
                onFailed = { relay.close() },   // the live view shows its picture again (the relay is Closed)
                macOs = isMac(),
            )
        }
        DisposableEffect(session) {
            relay.attach(session)
            onDispose {
                relay.detach(session)
                session.close()
            }
        }
        LaunchedEffect(session, holding) { session.setHolding(holding) }
        // the pointer's held-back move goes out once its interval passed, even if the mouse stopped; a new socket's size is swapped in at the cap
        LaunchedEffect(session, holding) {
            while (true) {
                delay(16)
                if (holding) session.pointer.flush(System.currentTimeMillis())
                session.tick()
            }
        }
        VncSurface(session, holding, onPaste, modifier)
    }
}

internal fun isMac(): Boolean = System.getProperty("os.name").orEmpty().lowercase().contains("mac")

/**
 * The picture of [session] with its input: the owner's keys while holding, the pointer and the wheel (zoom with Ctrl or ⌘, pan while
 * watching a zoomed picture), and a click requests the focus so the keys go to the screen.
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
fun VncSurface(session: DesktopVncSession, holding: Boolean, onPaste: () -> Unit, modifier: Modifier) {
    val c = Kelo.colors
    val mac = remember { isMac() }
    val frame by session.frame.collectAsState()
    val cursorShape by session.cursor.collectAsState()
    val windowFocused = LocalWindowInfo.current.isWindowFocused
    val focus = remember { FocusRequester() }
    var box by remember { mutableStateOf(IntSize.Zero) }
    var zoom by remember { mutableFloatStateOf(1f) }
    var pan by remember { mutableStateOf(Offset.Zero) }
    // Ctrl and ⌘ as the keys last said (the wheel reads them: a pointer event does not carry them in this Compose)
    var ctrlHeld by remember { mutableStateOf(false) }
    var metaHeld by remember { mutableStateOf(false) }
    val ctrlNow by rememberUpdatedState(ctrlHeld)
    val metaNow by rememberUpdatedState(metaHeld)
    val holdingNow by rememberUpdatedState(holding)
    val pasteNow by rememberUpdatedState(onPaste)
    val frameNow by rememberUpdatedState(frame)
    val boxNow by rememberUpdatedState(box)
    val zoomNow by rememberUpdatedState(zoom)
    val panNow by rememberUpdatedState(pan)
    // the window lost focus: nothing stays down on the page
    LaunchedEffect(windowFocused) { if (!windowFocused) session.focusLost() }

    val picture = rememberPicture(frame)
    val icon = remember(cursorShape, holding) {
        val shape = cursorShape
        if (holding && shape != null) customCursor(shape) else PointerIcon.Default
    }

    fun zoomAt(factor: Float, cx: Float, cy: Float) {
        val f = frameNow ?: return
        val view = boxNow
        val newZoom = LiveGeometry.clampZoom(zoomNow * factor)
        val (px, py) = LiveGeometry.panForZoomAround(cx, cy, zoomNow, newZoom, panNow.x, panNow.y, view.width.toFloat(), view.height.toFloat())
        val (cpx, cpy) = LiveGeometry.clampPan(px, py, newZoom, view.width.toFloat(), view.height.toFloat(), f.width, f.height)
        zoom = newZoom
        pan = Offset(cpx, cpy)
    }

    fun zoomKey(ctrl: Boolean, meta: Boolean) = if (mac) meta else ctrl

    Box(
        modifier
            .testTag("vnc-surface")
            .fillMaxSize()
            .clip(RoundedCornerShape(12.dp))
            .background(c.bubble)
            .onSizeChanged { box = it }
            .focusRequester(focus)
            .focusable()
            .onPreviewKeyEvent { e ->
                ctrlHeld = e.isCtrlPressed
                metaHeld = e.isMetaPressed
                keyEvent(e, session, holdingNow, mac) { pasteNow() }
            }
            .pointerHoverIcon(icon)
            .pointerInput(session) {
                awaitPointerEventScope {
                    while (true) {
                        val event = awaitPointerEvent()
                        val change = event.changes.firstOrNull() ?: continue
                        val view = boxNow
                        val f = frameNow
                        val at = if (f == null) null else
                            RfbPointer.frameAt(change.position.x, change.position.y, view.width.toFloat(), view.height.toFloat(), f.width, f.height, zoomNow, panNow.x, panNow.y)
                        if (event.type == PointerEventType.Scroll) {
                            val delta = change.scrollDelta
                            if (zoomKey(ctrlNow, metaNow)) {
                                zoomAt(if (delta.y < 0) WHEEL_ZOOM_STEP else 1f / WHEEL_ZOOM_STEP, change.position.x, change.position.y)
                            } else if (holdingNow) {
                                session.pointer.wheel(up = -delta.y, right = delta.x, p = at)
                            } else if (zoomNow > 1.01f) {
                                val (px, py) = LiveGeometry.clampPan(panNow.x - delta.x * 60f, panNow.y - delta.y * 60f, zoomNow, view.width.toFloat(), view.height.toFloat(), f?.width ?: 0, f?.height ?: 0)
                                pan = Offset(px, py)
                            }
                            change.consume()
                            continue
                        }
                        if (event.type == PointerEventType.Press || event.type == PointerEventType.Release) {
                            val bit = buttonBit(event.button)
                            if (bit != null) {
                                session.pointer.button(bit, down = event.type == PointerEventType.Press, p = at)
                                if (event.type == PointerEventType.Press) runCatching { focus.requestFocus() }
                            }
                        } else {
                            session.pointer.move(at, System.currentTimeMillis())
                        }
                        change.consume()
                    }
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        val pic = picture
        if (pic == null) {
            CircularProgressIndicator(color = c.muted)
        } else {
            Image(
                pic, contentDescription = null, contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize().graphicsLayer { scaleX = zoom; scaleY = zoom; translationX = pan.x; translationY = pan.y },
            )
        }
        LiveZoomButtons(
            zoom, Modifier.align(Alignment.BottomEnd),
            onOut = { zoomAt(1f / WHEEL_ZOOM_STEP, box.width / 2f, box.height / 2f) },
            onIn = { zoomAt(WHEEL_ZOOM_STEP, box.width / 2f, box.height / 2f) },
            onReset = { zoom = 1f; pan = Offset.Zero },
        )
    }
}

private const val WHEEL_ZOOM_STEP = 1.1f

/** The mask bit of a mouse button on the page: left 1, middle 2, right 4; null for the buttons the page has none for. */
@OptIn(ExperimentalComposeUiApi::class)
private fun buttonBit(button: PointerButton?): Int? = when (button) {
    PointerButton.Primary -> RfbPointer.LEFT
    PointerButton.Secondary -> RfbPointer.RIGHT
    PointerButton.Tertiary -> RfbPointer.MIDDLE
    else -> null
}

/** The owner's key: while holding, every key goes to the page (Ctrl+V, ⌘V on macOS, types the phone's paste instead); else the window keeps it. */
private fun keyEvent(e: KeyEvent, session: DesktopVncSession, holding: Boolean, mac: Boolean, paste: () -> Unit): Boolean {
    if (!holding) return false
    val ctrl = e.isCtrlPressed || (mac && e.isMetaPressed)
    return when (e.type) {
        KeyEventType.KeyDown -> {
            if (ctrl && e.key == Key.V) paste() else session.keyDown(e.key.keyCode, e.utf16CodePoint, ctrl)
            true
        }
        KeyEventType.KeyUp -> {
            session.keyUp(e.key.keyCode)
            true
        }
        else -> true
    }
}

/**
 * The server's picture as an image: refreshed at most once per display frame, and only when the frame changed (its version). The
 * copy is made from the frame buffer under its lock, so a half-drawn update never shows.
 */
@Composable
private fun rememberPicture(frame: FrameBuffer?): ImageBitmap? {
    var picture by remember { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(frame) {
        val fb = frame ?: run { picture = null; return@LaunchedEffect }
        var buf = ByteArray(0)
        var seen = -1L
        while (true) {
            withFrameNanos { }
            val need = fb.width * fb.height * 4
            if (buf.size != need) buf = ByteArray(need)
            if (fb.version == seen || need == 0) continue
            val snap = fb.copyTo(buf) ?: continue
            seen = snap.version
            val info = ImageInfo(snap.width, snap.height, ColorType.BGRA_8888, ColorAlphaType.OPAQUE)
            picture = SkiaImage.makeRaster(info, buf, snap.width * 4).toComposeImageBitmap()
        }
    }
    return picture
}

/** The server's cursor as the system's cursor (only while the owner holds the tab). */
private fun customCursor(shape: RfbCursorImage): PointerIcon = runCatching {
    val img = BufferedImage(shape.width, shape.height, BufferedImage.TYPE_INT_ARGB)
    img.setRGB(0, 0, shape.width, shape.height, shape.argb, 0, shape.width)
    PointerIcon(Toolkit.getDefaultToolkit().createCustomCursor(img, Point(shape.hotX, shape.hotY), "kelo-vnc"))
}.getOrDefault(PointerIcon.Default)
