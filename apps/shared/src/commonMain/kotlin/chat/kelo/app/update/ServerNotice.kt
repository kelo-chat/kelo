package chat.kelo.app.update

import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * When the "Kelo server update available" notice may show: the server offered a newer release (`X-Kelo-Server-Update`), the notice
 * was not shown in the last 24 hours ([UpdatePopupRules.due]), it is not within an hour of the app-update popup (two update popups in a
 * row feel like nagging), and nothing that covers the chat is showing ([covered]).
 */
object ServerNoticeRules {
    /** Not within an hour of the app-update popup. */
    const val AFTER_APP_POPUP_MS = 60L * 60 * 1000

    fun show(offered: String?, lastShownMs: Long?, appPopupShownMs: Long?, nowMs: Long, covered: Boolean): Boolean =
        !offered.isNullOrBlank() && !covered && UpdatePopupRules.due(lastShownMs, nowMs) &&
            (appPopupShownMs == null || nowMs < appPopupShownMs || nowMs - appPopupShownMs >= AFTER_APP_POPUP_MS)
}

/**
 * The server-update notice of this device. [load] / [save] keep the time it last showed in the app's settings storage (a storage that
 * fails never breaks the app: the notice then simply may show again). [open] is the version the notice shows now, or null.
 * Whatever the answer, [close] keeps it away for 24 hours from the opening.
 */
class ServerNotice(
    private val load: () -> String?,
    private val save: (String) -> Unit,
    private val now: () -> Long = { System.currentTimeMillis() },
) {
    private var lastShown: Long? = runCatching(load).getOrNull()?.trim()?.toLongOrNull()
    private val _open = MutableStateFlow<String?>(null)
    val open: StateFlow<String?> = _open.asStateFlow()

    /** Opens the notice when [ServerNoticeRules.show] says so, and saves the time. Returns whether it is open. */
    fun consider(offered: String?, appPopupShownMs: Long?, covered: Boolean): Boolean {
        val version = offered?.trim()?.takeIf { it.isNotEmpty() }
        if (_open.value != null) return true
        if (!ServerNoticeRules.show(version, lastShown, appPopupShownMs, now(), covered)) return false
        lastShown = now()
        runCatching { save(lastShown.toString()) }
        _open.value = version
        return true
    }

    /** See the update, Later, or dismissed: the notice goes away. */
    fun close() { _open.value = null }
}

/** The server-update notice: the title names the version, Open goes to My -> Server update, Later (or dismissed) closes it. */
@Composable
fun ServerNoticeDialog(version: String, onOpen: () -> Unit, onLater: () -> Unit) {
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = onLater,
        containerColor = c.card,
        title = { Text(string(Res.string.server_notice_title, version), color = c.ink, style = MaterialTheme.typography.titleMedium) },
        text = { Text(string(Res.string.server_notice_body), color = c.muted, style = MaterialTheme.typography.bodyMedium) },
        confirmButton = { TextButton(onClick = onOpen) { Text(string(Res.string.server_notice_open), color = c.accent) } },
        dismissButton = { TextButton(onClick = onLater) { Text(string(Res.string.update_popup_later), color = c.ink) } },
    )
}
