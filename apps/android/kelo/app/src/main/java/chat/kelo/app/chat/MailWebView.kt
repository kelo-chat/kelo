package chat.kelo.app.chat

import android.graphics.Color
import android.os.Build
import android.view.View
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import java.io.ByteArrayInputStream

/** What the mail preview's web view may do. Nothing is ever loaded; a tapped link goes to the owner's confirm, never followed by the view. */
object MailWebPolicy {
    /** Every load is refused, whatever its scheme or address. */
    fun blockAll(url: String?): Boolean = true

    /** The address of a link the owner tapped (a gesture), or null: a link the view reaches on its own is never followed. */
    fun tappedLink(url: String, hasGesture: Boolean): String? = if (hasGesture) url else null
}

/**
 * The mail preview's document in a web view with scripts, network and file access off (the same words the server sends, nothing
 * more). A tapped link comes back through [onLink]; the shared code asks the owner before it opens anywhere.
 */
object AndroidMailPreviewHost : MailPreviewHost {
    @Composable
    override fun Document(html: String, scrollable: Boolean, onLink: (String) -> Unit, modifier: Modifier, fallback: @Composable () -> Unit) {
        val context = LocalContext.current
        // the web view package can be missing or updating: then the blocks are drawn instead
        val usable = remember(context) { runCatching { WebView(context).destroy() }.isSuccess }
        if (!usable) {
            Box(modifier) { fallback() }
            return
        }
        val linkHandler by rememberUpdatedState(onLink)
        AndroidView(
            modifier = modifier,
            factory = { ctx ->
                WebView(ctx).apply {
                    configure(scrollable)
                    webViewClient = object : WebViewClient() {
                        override fun shouldInterceptRequest(view: WebView?, request: WebResourceRequest?): WebResourceResponse =
                            WebResourceResponse("text/plain", "utf-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))

                        override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                            val req = request ?: return true
                            MailWebPolicy.tappedLink(req.url.toString(), req.hasGesture())?.let(linkHandler)
                            return true
                        }

                        override fun onRenderProcessGone(view: WebView?, detail: RenderProcessGoneDetail?): Boolean {
                            view?.destroy()
                            return true   // the app keeps running; the card shows its failure text
                        }
                    }
                    webChromeClient = null
                }
            },
            update = { view ->
                if (view.tag != html) {   // loaded once per document
                    view.tag = html
                    view.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
                }
            },
            onRelease = { view ->
                view.stopLoading()
                view.destroy()
            },
        )
    }

    private fun WebView.configure(scrollable: Boolean) {
        settings.apply {
            javaScriptEnabled = false
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            allowFileAccess = false
            allowContentAccess = false
            @Suppress("DEPRECATION")
            blockNetworkLoads = true
            @Suppress("DEPRECATION")
            blockNetworkImage = true
            loadsImagesAutomatically = false
            domStorageEnabled = false
            databaseEnabled = false
            setGeolocationEnabled(false)
            cacheMode = WebSettings.LOAD_NO_CACHE
            mediaPlaybackRequiresUserGesture = true
            builtInZoomControls = scrollable
            displayZoomControls = false
            if (Build.VERSION.SDK_INT >= 33) isAlgorithmicDarkeningAllowed = false
        }
        setBackgroundColor(Color.WHITE)
        setOnLongClickListener { true }   // no link preview or text actions that could load anything
        isHapticFeedbackEnabled = false
        importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
        if (!scrollable) {
            isVerticalScrollBarEnabled = false
            isHorizontalScrollBarEnabled = false
            isFocusable = false
            setOnTouchListener { _, _ -> true }   // the card's overlay takes the tap
        }
    }
}
