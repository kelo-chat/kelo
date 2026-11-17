package chat.kelo.app.update

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** A newer app the popup can announce: its version, the release line (blank = none) and where the action goes. */
data class UpdateCandidate(val version: String, val notes: String, val source: UpdatePopupRules.Source)

/**
 * The popup that tells, at the start of the app and when it comes back after an hour away, that a newer app is out: at most once a
 * day (owner decision 2026-11-16). Whatever the answer (Update without really updating, Later, closed), the next showing is no earlier
 * than 24 hours after this one, until the app runs the new version. Where the news comes from follows the build: a store build (Play,
 * App Store, Store) takes it from the server's `app_update` ([chat.kelo.app.core.AppVersion.update]) and its action opens the store
 * page; a file build (sideload, desktop installer) takes it from the signed latest.json offer ([UpdateState.offer]) and its action is
 * the app's own updater.
 */
object UpdatePopupRules {
    enum class Source { STORE, UPDATER }

    const val EVERY_MS = 24L * 60 * 60 * 1000
    const val AWAY_MS = 60L * 60 * 1000
    /** The release line is a short plain sentence: longer text is cut. */
    const val NOTES_MAX = 240

    fun candidate(state: UpdateState, serverVersion: String?, channel: String): UpdateCandidate? {
        val store = AppNoticeRules.action(channel) == AppNoticeRules.Action.STORE
        val running = state.running
        val v = serverVersion?.trim().orEmpty()
        val serverNewer = v.isNotEmpty() && (running.isBlank() || UpdateRules.newer(v, running))
        if (store) return if (serverNewer) UpdateCandidate(v, "", Source.STORE) else null
        state.offer?.let { r ->
            // the offer already passed UpdateRules.offer (newer than this build) and the release signature
            return UpdateCandidate(r.version, r.notes.trim().take(NOTES_MAX), Source.UPDATER)
        }
        return if (serverNewer) UpdateCandidate(v, "", Source.UPDATER) else null
    }

    /** Due: never shown here, or the last showing is 24 hours old (a clock set back counts as due). */
    fun due(lastShownMs: Long?, nowMs: Long): Boolean = lastShownMs == null || nowMs - lastShownMs >= EVERY_MS || nowMs < lastShownMs

    /** Back in front of the user after more than an hour away: the popup may come again (and the check is repeated). */
    fun awayLongEnough(leftAtMs: Long?, nowMs: Long): Boolean = leftAtMs != null && nowMs - leftAtMs > AWAY_MS
}

/**
 * When the popup last showed on this device ([load] / [save] go to the app's settings storage) and whether it is armed: at start, and
 * after more than an hour in the background. [open] is the popup showing now; the banners stay hidden while it does.
 */
class UpdatePopup(private val load: () -> String?, private val save: (String) -> Unit, private val now: () -> Long = { System.currentTimeMillis() }) {
    private var lastShown: Long? = runCatching(load).getOrNull()?.trim()?.toLongOrNull()
    private val _open = MutableStateFlow<UpdateCandidate?>(null)
    private val _armed = MutableStateFlow(true)
    private var leftAt: Long? = null
    val open: StateFlow<UpdateCandidate?> = _open.asStateFlow()

    /** Called whenever the news may have changed (and at start / return): opens the popup when one is due. Returns whether it is open. */
    fun consider(candidate: UpdateCandidate?): Boolean {
        if (candidate == null) { _open.value = null; return false }
        if (_open.value != null) return true
        if (!_armed.value || !UpdatePopupRules.due(lastShown, now())) return false
        lastShown = now()
        runCatching { save(lastShown.toString()) }
        _open.value = candidate
        return true
    }

    /** Whatever the answer: the popup goes away and stays away for 24 hours. */
    fun close() { _open.value = null; _armed.value = false }

    fun left() { leftAt = now() }

    /** The app is in front again; true when it was away long enough that the update check should be repeated. */
    fun returned(): Boolean {
        val again = UpdatePopupRules.awayLongEnough(leftAt, now())
        leftAt = null
        if (again) _armed.value = true
        return again
    }
}

/** The popup itself: title with the version, the release line when there is one, Update / Later. */
@Composable
fun UpdatePopupDialog(candidate: UpdateCandidate, onUpdate: () -> Unit, onLater: () -> Unit) {
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = onLater,
        containerColor = c.card,
        title = { Text(string(Res.string.update_popup_title, candidate.version), color = c.ink, style = MaterialTheme.typography.titleMedium) },
        text = if (candidate.notes.isBlank()) null else ({ Text(candidate.notes, color = c.muted, style = MaterialTheme.typography.bodyMedium) }),
        confirmButton = { TextButton(onClick = onUpdate) { Text(string(Res.string.update_popup_update), color = c.accent) } },
        dismissButton = { TextButton(onClick = onLater) { Text(string(Res.string.update_popup_later), color = c.ink) } },
    )
}
