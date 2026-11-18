package chat.kelo.app.agent

import android.content.Context
import android.graphics.Color
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.webkit.WebMessage
import android.webkit.WebMessagePort
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import java.io.ByteArrayInputStream
import java.io.IOException

/**
 * The VNC screen in a WebView: noVNC (assets/vnc, 1.5.0) draws the tab's X screen; the bytes go through the shared [VncRelay]
 * (the same socket as the live view, KE2E included). The page is served from our own origin out of the app's assets, and nothing
 * else is loaded (every other request is answered 404, navigation is refused).
 */
object AndroidVncScreenHost : VncScreenHost {
    @Composable
    override fun Screen(relay: VncRelay, holding: Boolean, onPaste: () -> Unit, modifier: Modifier) {
        val bridge = remember(relay) { VncWebBridge(relay) }
        DisposableEffect(bridge) {
            relay.attach(bridge)
            onDispose {
                relay.detach(bridge)
                bridge.release()
            }
        }
        LaunchedEffect(bridge, holding) { bridge.hold(holding) }
        AndroidView(
            modifier = modifier,
            factory = { context -> WebView(context).also { bridge.bind(it) } },
        )
    }
}

/** The page's origin and the asset rules (only files under assets/vnc, never a path that climbs out). */
internal object VncAssets {
    const val ORIGIN = "https://appassets.androidplatform.net"
    const val PAGE = "$ORIGIN/assets/vnc/view.html"
    private const val HOST = "appassets.androidplatform.net"

    fun respond(context: Context, uri: Uri): WebResourceResponse {
        val path = uri.path.orEmpty()
        if (uri.scheme != "https" || uri.host != HOST || !path.startsWith("/assets/vnc/") || path.contains("..")) return notFound()
        val name = path.removePrefix("/assets/")
        val stream = try {
            context.assets.open(name)
        } catch (e: IOException) {
            return notFound()
        }
        return WebResourceResponse(mimeOf(name), "utf-8", stream)
    }

    fun mimeOf(name: String): String = when (name.substringAfterLast('.', "")) {
        "html" -> "text/html"
        "js" -> "text/javascript"
        "css" -> "text/css"
        else -> "application/octet-stream"
    }

    private fun notFound() = WebResourceResponse("text/plain", "utf-8", 404, "Not Found", emptyMap(), ByteArrayInputStream(ByteArray(0)))
}

/**
 * One WebView's bridge: the page gets a WebMessagePort once it has loaded; it says "ready", then the bytes flow. Everything the page
 * sends is a string (see [VncBridge]). All of it runs on the main thread; bytes from the relay are posted there.
 */
private class VncWebBridge(private val relay: VncRelay) : VncSink {
    private val main = Handler(Looper.getMainLooper())
    private var web: WebView? = null
    private var port: WebMessagePort? = null
    private var ready = false
    private var holding = false
    /** Messages for the page before it is ready (bytes, hold) */
    private val pending = ArrayList<String>()

    fun bind(view: WebView) {
        web = view
        view.setBackgroundColor(Color.TRANSPARENT)
        view.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = false
            allowFileAccess = false
            allowContentAccess = false
            setGeolocationEnabled(false)
        }
        view.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse =
                VncAssets.respond(view.context, request.url)

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = true

            override fun onPageFinished(view: WebView, url: String?) = openPort(view)
        }
        view.loadUrl(VncAssets.PAGE)
    }

    private fun openPort(view: WebView) {
        if (port != null) return
        val ports = view.createWebMessageChannel()
        val ours = ports[0]
        ours.setWebMessageCallback(object : WebMessagePort.WebMessageCallback() {
            override fun onMessage(p: WebMessagePort, message: WebMessage) = onPageMessage(message.data.orEmpty())
        })
        port = ours
        view.postWebMessage(WebMessage("init", arrayOf(ports[1])), Uri.parse(VncAssets.ORIGIN))
    }

    private fun onPageMessage(text: String) {
        when (val m = VncBridge.fromPage(text)) {
            is VncBridge.FromPage.Bytes -> relay.send(m.bytes)
            is VncBridge.FromPage.Clipboard -> relay.offerClipboard(m.text)
            VncBridge.FromPage.Ready -> {
                ready = true
                post(VncBridge.open())
                post(VncBridge.hold(holding))
                pending.forEach { post(it) }
                pending.clear()
            }
            VncBridge.FromPage.Unknown -> Unit
        }
    }

    /** Server bytes, from the relay's thread. */
    override fun toPage(bytes: ByteArray) {
        val text = VncBridge.data(bytes)
        main.post { deliver(text) }
    }

    /** A new socket for a new screen size: the page starts a new session after the old bytes, in order with the new ones. */
    override fun reset() {
        main.post { deliver(VncBridge.reset()) }
    }

    fun hold(on: Boolean) {
        holding = on
        if (ready) post(VncBridge.hold(on))
    }

    private fun deliver(text: String) {
        if (web == null) return
        if (ready) post(text) else pending.add(text)
    }

    private fun post(text: String) {
        port?.postMessage(WebMessage(text))
    }

    fun release() {
        port?.close()
        port = null
        ready = false
        pending.clear()
        web?.apply {
            stopLoading()
            destroy()
        }
        web = null
    }
}
