package chat.kelo.app.update

import com.google.android.play.core.install.model.AppUpdateType
import com.google.android.play.core.install.model.InstallStatus
import com.google.android.play.core.install.model.UpdateAvailability

/**
 * The Play build's update decisions, apart from Android and the Play service (PlayAppUpdates applies them):
 *  - normally a FLEXIBLE update: the banner / card offers it, Play downloads in the background, then "다시 시작" applies it;
 *  - IMMEDIATE only when the server says this build is too old (`min_app_version`), and only when Play allows it; otherwise the
 *    required screen sends the user to the Play Store;
 *  - an immediate update the app started and the user left half-way is resumed (Google's advice for
 *    DEVELOPER_TRIGGERED_UPDATE_IN_PROGRESS);
 *  - asking Play for a new version at most every [CHECK_EVERY_MS] (start, coming back to the app); a finished download and an
 *    update in progress are looked at on every return.
 */
object PlayUpdateRules {
    const val CHECK_EVERY_MS = 4L * 60 * 60 * 1000

    enum class Action {
        /** Nothing to offer (or no longer: installed elsewhere). */
        NONE,
        /** A new version: the banner and card (flexible; or immediate on tap when Play allows only that). */
        OFFER,
        /** A flexible download is under way. */
        DOWNLOADING,
        /** A flexible download is done: offer "다시 시작" (completeUpdate). */
        READY,
        /** The server requires a newer build and Play allows an immediate update: start it. */
        START_IMMEDIATE,
        /** An immediate update this app started was left half-way: resume it. */
        RESUME_IMMEDIATE,
        /** The server requires a newer build but Play offers none this app can start: the required screen opens the Play Store. */
        BLOCK,
    }

    /** Due for asking Play again: never asked in this run, the last ask is [CHECK_EVERY_MS] old, or the clock went back. */
    fun due(lastCheckMs: Long?, nowMs: Long): Boolean = lastCheckMs == null || nowMs - lastCheckMs >= CHECK_EVERY_MS || nowMs < lastCheckMs

    /**
     * What Play's answer means. [availability]: UpdateAvailability; [installStatus]: InstallStatus; [required]: below the server's
     * minimum; [startedFlexible]: the last flow this app started was flexible (Play reports a flexible download in progress as
     * DEVELOPER_TRIGGERED_UPDATE_IN_PROGRESS too, which must not be turned into an immediate update).
     */
    fun decide(
        availability: Int,
        installStatus: Int,
        flexibleAllowed: Boolean,
        immediateAllowed: Boolean,
        required: Boolean,
        startedFlexible: Boolean,
    ): Action {
        if (installStatus == InstallStatus.DOWNLOADED) return Action.READY
        if (availability == UpdateAvailability.DEVELOPER_TRIGGERED_UPDATE_IN_PROGRESS) {
            val downloading = installStatus == InstallStatus.DOWNLOADING || installStatus == InstallStatus.PENDING
            return if (startedFlexible && downloading && !required) Action.DOWNLOADING else Action.RESUME_IMMEDIATE
        }
        if (installStatus == InstallStatus.DOWNLOADING || installStatus == InstallStatus.PENDING) return Action.DOWNLOADING
        if (availability == UpdateAvailability.UPDATE_AVAILABLE) {
            if (required) return if (immediateAllowed) Action.START_IMMEDIATE else Action.BLOCK
            return if (flexibleAllowed || immediateAllowed) Action.OFFER else Action.NONE
        }
        return if (required) Action.BLOCK else Action.NONE
    }

    /**
     * The flow a tap starts (AppUpdateType), or null when Play offers none (the Play Store opens instead): required -> IMMEDIATE
     * only; otherwise FLEXIBLE, or IMMEDIATE when Play allows only that. An update in progress is resumed as IMMEDIATE.
     */
    fun flowType(availability: Int, flexibleAllowed: Boolean, immediateAllowed: Boolean, required: Boolean): Int? = when {
        availability == UpdateAvailability.DEVELOPER_TRIGGERED_UPDATE_IN_PROGRESS -> AppUpdateType.IMMEDIATE
        availability != UpdateAvailability.UPDATE_AVAILABLE -> null
        required -> if (immediateAllowed) AppUpdateType.IMMEDIATE else null
        flexibleAllowed -> AppUpdateType.FLEXIBLE
        immediateAllowed -> AppUpdateType.IMMEDIATE
        else -> null
    }

    /** Play's install status as the card's phase. */
    fun phase(installStatus: Int): UpdatePhase = when (installStatus) {
        InstallStatus.PENDING, InstallStatus.DOWNLOADING, InstallStatus.INSTALLING -> UpdatePhase.DOWNLOADING
        InstallStatus.DOWNLOADED -> UpdatePhase.READY
        InstallStatus.FAILED -> UpdatePhase.FAILED
        else -> UpdatePhase.IDLE
    }
}
