package chat.kelo.app.platform

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import chat.kelo.app.ui.ActivityHost

/** The phone's browser behind [ExternalBrowser]: the page opens from the screen on top, so back returns to the app. */
class AndroidExternalBrowser(private val context: Context, private val host: ActivityHost) : ExternalBrowser {
    override fun open(url: String): Boolean {
        val from = host.current ?: context
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE)
        if (from === context) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)   // no screen to start it from
        return try {
            from.startActivity(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        } catch (_: SecurityException) {
            false
        }
    }
}
