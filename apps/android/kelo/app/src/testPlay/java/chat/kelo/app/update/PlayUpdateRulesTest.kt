package chat.kelo.app.update

import chat.kelo.app.update.PlayUpdateRules.Action
import com.google.android.play.core.install.model.AppUpdateType
import com.google.android.play.core.install.model.InstallStatus
import com.google.android.play.core.install.model.UpdateAvailability
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class PlayUpdateRulesTest {
    private val available = UpdateAvailability.UPDATE_AVAILABLE
    private val none = UpdateAvailability.UPDATE_NOT_AVAILABLE
    private val inProgress = UpdateAvailability.DEVELOPER_TRIGGERED_UPDATE_IN_PROGRESS
    private val unknown = InstallStatus.UNKNOWN

    private fun decide(availability: Int, status: Int = unknown, flexible: Boolean = true, immediate: Boolean = true,
                       required: Boolean = false, startedFlexible: Boolean = false) =
        PlayUpdateRules.decide(availability, status, flexible, immediate, required, startedFlexible)

    @Test fun normallyAFlexibleOfferNeverAnImmediateUpdate() {
        assertEquals(Action.OFFER, decide(available))
        assertEquals(AppUpdateType.FLEXIBLE, PlayUpdateRules.flowType(available, flexibleAllowed = true, immediateAllowed = true, required = false))
        // Play allows only an immediate update: still just offered, started by the user's tap
        assertEquals(Action.OFFER, decide(available, flexible = false))
        assertEquals(AppUpdateType.IMMEDIATE, PlayUpdateRules.flowType(available, flexibleAllowed = false, immediateAllowed = true, required = false))
        assertEquals(Action.NONE, decide(available, flexible = false, immediate = false))
        assertEquals(Action.NONE, decide(none))
        assertNull(PlayUpdateRules.flowType(none, flexibleAllowed = true, immediateAllowed = true, required = false))
    }

    @Test fun immediateOnlyWhenTheServerRequiresANewerBuild() {
        assertEquals(Action.START_IMMEDIATE, decide(available, required = true))
        assertEquals(AppUpdateType.IMMEDIATE, PlayUpdateRules.flowType(available, flexibleAllowed = true, immediateAllowed = true, required = true))
        // Play does not allow an immediate update (or has none for this install): the required screen opens the Play Store
        assertEquals(Action.BLOCK, decide(available, immediate = false, required = true))
        assertNull(PlayUpdateRules.flowType(available, flexibleAllowed = true, immediateAllowed = false, required = true))
        assertEquals(Action.BLOCK, decide(none, required = true))
    }

    @Test fun aFinishedDownloadWaitsForRestartAndAnUpdateLeftHalfWayIsResumed() {
        assertEquals(Action.READY, decide(none, InstallStatus.DOWNLOADED))
        assertEquals(Action.READY, decide(inProgress, InstallStatus.DOWNLOADED, required = true))
        assertEquals(Action.RESUME_IMMEDIATE, decide(inProgress, InstallStatus.DOWNLOADING))
        assertEquals(Action.RESUME_IMMEDIATE, decide(inProgress, unknown))
        assertEquals(AppUpdateType.IMMEDIATE, PlayUpdateRules.flowType(inProgress, flexibleAllowed = false, immediateAllowed = false, required = false))
        // a flexible download this app started also reads "in progress": it is followed, never turned into an immediate update
        assertEquals(Action.DOWNLOADING, decide(inProgress, InstallStatus.DOWNLOADING, startedFlexible = true))
        assertEquals(Action.DOWNLOADING, decide(available, InstallStatus.PENDING, startedFlexible = true))
    }

    @Test fun asksPlayAtMostEveryFewHours() {
        assertTrue(PlayUpdateRules.due(null, 1_000))
        assertFalse(PlayUpdateRules.due(1_000, 1_000 + PlayUpdateRules.CHECK_EVERY_MS - 1))
        assertTrue(PlayUpdateRules.due(1_000, 1_000 + PlayUpdateRules.CHECK_EVERY_MS))
        assertTrue(PlayUpdateRules.due(5_000, 1_000))   // the clock went back
        assertTrue(PlayUpdateRules.CHECK_EVERY_MS in 1L * 60 * 60 * 1000..12L * 60 * 60 * 1000)
    }

    @Test fun playsInstallStatusAsTheCardsPhase() {
        assertEquals(UpdatePhase.DOWNLOADING, PlayUpdateRules.phase(InstallStatus.PENDING))
        assertEquals(UpdatePhase.DOWNLOADING, PlayUpdateRules.phase(InstallStatus.DOWNLOADING))
        assertEquals(UpdatePhase.READY, PlayUpdateRules.phase(InstallStatus.DOWNLOADED))
        assertEquals(UpdatePhase.FAILED, PlayUpdateRules.phase(InstallStatus.FAILED))
        assertEquals(UpdatePhase.IDLE, PlayUpdateRules.phase(InstallStatus.CANCELED))
    }

    /** The Play bundle never asks to install packages itself (Play installs the update). */
    @Test fun thePlayManifestStillRemovesRequestInstallPackages() {
        val play = listOf(File("src/play/AndroidManifest.xml"), File("app/src/play/AndroidManifest.xml")).first { it.exists() }.readText()
        assertTrue(Regex("""<uses-permission\s+android:name="android.permission.REQUEST_INSTALL_PACKAGES"\s+tools:node="remove"\s*/>""").containsMatchIn(play))
    }
}
