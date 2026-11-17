package chat.kelo.app.update

import chat.kelo.app.core.AppVersion
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * "A new Kelo app (x.y.z) works best with your server" (docs/version-notices.md, part 3). The server names the newest released app that
 * fits it for this app's platform and channel (`X-Kelo-App-Update`, [AppVersion.update]); this app shows a banner once per version and
 * remembers a dismissed version on this device. Every build shows it, store builds too: the action is the store page there
 * ([Action.STORE]), the app's own updater elsewhere ([Action.UPDATER]).
 */
object AppNoticeRules {
    enum class Action { STORE, UPDATER }

    /** Google Play's page of the Android app */
    const val PLAY_URL = "https://play.google.com/store/apps/details?id=chat.kelo.app"

    /** Whether the banner shows: a version is named, it was not dismissed here, and nothing newer than this build's own offer is competing. */
    fun show(offered: String?, dismissed: Set<String>): Boolean = !offered.isNullOrBlank() && offered !in dismissed

    /** The channels that update from a store page (the rest use the app's own updater). */
    fun action(channel: String): Action = if (channel == "play" || channel == "appstore" || channel == "msstore") Action.STORE else Action.UPDATER

    /** The remembered versions as one string (a version never contains a comma) and back. */
    fun encode(set: Set<String>): String = set.sorted().joinToString(",")
    fun decode(text: String?): Set<String> = text.orEmpty().split(',').map { it.trim() }.filter { it.isNotEmpty() }.toSet()
}

/** The dismissed versions of one device: [load] / [save] go to the app's own settings storage. */
class AppNotice(private val load: () -> String?, private val save: (String) -> Unit) {
    private val _dismissed = MutableStateFlow(AppNoticeRules.decode(runCatching(load).getOrNull()))
    val dismissed: StateFlow<Set<String>> = _dismissed.asStateFlow()

    /** The version to show now, or null. */
    fun visible(offered: String? = AppVersion.update.value): String? = offered?.takeIf { AppNoticeRules.show(it, _dismissed.value) }

    /** Closed or used: this version does not come back on this device. */
    fun dismiss(version: String) {
        val next = _dismissed.value + version
        _dismissed.value = next
        runCatching { save(AppNoticeRules.encode(next)) }
    }
}
