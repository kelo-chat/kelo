package chat.kelo.app.update

import android.content.ContextWrapper
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContract
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.ActivityOptionsCompat
import com.google.android.play.core.appupdate.AppUpdateInfo
import com.google.android.play.core.appupdate.AppUpdateOptions
import com.google.android.play.core.appupdate.testing.FakeAppUpdateManager
import com.google.android.play.core.install.model.AppUpdateType
import android.app.PendingIntent
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.MockedStatic
import org.mockito.Mockito

/** The Play build's updates against Play Core's own FakeAppUpdateManager (no Play service, no device). */
class PlayAppUpdatesTest {
    /** The app's own package (the fake names it in every install state). */
    private class App : ContextWrapper(null) {
        override fun getPackageName() = "chat.kelo.app"
        override fun getApplicationContext() = this
    }

    /**
     * Records the flows asked for ([asked]) and the ones the fake accepted and shows ([started]). The fake has no update types for
     * an update already in progress, so it refuses to resume one; the real Play resumes it — [asked] shows the app asked.
     */
    private class Fake : FakeAppUpdateManager(App()) {
        val started = mutableListOf<Int>()
        val asked = mutableListOf<Int>()
        override fun startUpdateFlowForResult(info: AppUpdateInfo, launcher: ActivityResultLauncher<IntentSenderRequest>, options: AppUpdateOptions): Boolean {
            asked += options.appUpdateType()
            val ok = super.startUpdateFlowForResult(info, launcher, options)
            if (ok) started += options.appUpdateType()
            return ok
        }
    }

    private object NoLauncher : ActivityResultLauncher<IntentSenderRequest>() {
        override fun launch(input: IntentSenderRequest, options: ActivityOptionsCompat?) {}
        override fun unregister() {}
        override val contract: ActivityResultContract<IntentSenderRequest, *> get() = ActivityResultContracts.StartIntentSenderForResult()
    }

    /**
     * The fake says an update type is allowed only when its PendingIntents exist; the plain JVM android.jar returns null from
     * PendingIntent.getBroadcast, so that static call returns a stand-in here (nothing else is mocked).
     */
    private lateinit var pending: MockedStatic<PendingIntent>

    @Before fun stubPendingIntents() {
        val stand = Mockito.mock(PendingIntent::class.java)
        pending = Mockito.mockStatic(PendingIntent::class.java) { inv -> if (inv.method.returnType == PendingIntent::class.java) stand else null }
    }

    @After fun release() = pending.close()

    private var now = 1_000_000L
    private fun updates(fake: Fake, versionCode: Long = 100) =
        PlayAppUpdates(App(), fake, versionCode, "0.1.$versionCode", { it.run() }, prefs = null, clock = { now })
            .apply { useLauncher(NoLauncher) }

    @Test fun flexibleUpdateFromOfferToRestart() {
        val fake = Fake()
        fake.setUpdateAvailable(101)
        val u = updates(fake)
        u.check()
        var s = u.state.value
        assertEquals("101", s.offer?.version)
        assertTrue(s.play && s.showBanner && !s.required)
        assertEquals(UpdatePhase.IDLE, s.phase)

        u.get()   // 업데이트: Play's flexible confirmation
        assertEquals(listOf(AppUpdateType.FLEXIBLE), fake.started)
        assertTrue(fake.isConfirmationDialogVisible)
        assertFalse(fake.isImmediateFlowVisible)
        assertFalse(u.state.value.showBanner)   // used: not shown again for this version

        fake.userAcceptsUpdate()
        fake.downloadStarts()
        fake.setTotalBytesToDownload(1_000)
        fake.setBytesDownloaded(400)
        s = u.state.value
        assertEquals(UpdatePhase.DOWNLOADING, s.phase)
        assertEquals(40, s.progress)

        fake.setBytesDownloaded(1_000)
        fake.downloadCompletes()
        s = u.state.value
        assertEquals(UpdatePhase.READY, s.phase)
        assertTrue(s.showBanner)   // the banner comes back once with 다시 시작

        u.get()   // on READY the same button applies it
        assertTrue(fake.isInstallSplashScreenVisible)
        fake.installCompletes()
        assertNull(u.state.value.offer)
        assertEquals(listOf(AppUpdateType.FLEXIBLE), fake.started)   // never an immediate update
    }

    @Test fun aDownloadFinishedWhileAwayShowsRestartOnReturn() {
        val fake = Fake()
        fake.setUpdateAvailable(101)
        updates(fake).apply { check(); get() }
        fake.userAcceptsUpdate(); fake.downloadStarts(); fake.downloadCompletes()
        val again = updates(fake)   // the app started again
        again.onForeground()
        assertEquals(UpdatePhase.READY, again.state.value.phase)
        again.install()
        assertTrue(fake.isInstallSplashScreenVisible)
    }

    @Test fun nothingAvailableShowsNothingAndTheManualCheckSaysUpToDate() {
        val fake = Fake()
        fake.setUpdateNotAvailable()
        val u = updates(fake)
        u.check(force = true)
        val s = u.state.value
        assertNull(s.offer)
        assertTrue(s.checked && !s.checking && !s.checkFailed && s.enabled)
        assertTrue(fake.started.isEmpty())
    }

    @Test fun checksAreThrottledButAManualCheckAlwaysAsks() {
        val fake = Fake()
        fake.setUpdateNotAvailable()
        val u = updates(fake)
        u.check()
        fake.setUpdateAvailable(101)
        u.check()                          // too soon
        assertNull(u.state.value.offer)
        u.onForeground()                   // a quick return does not offer a new version either
        assertNull(u.state.value.offer)
        now += PlayUpdateRules.CHECK_EVERY_MS
        u.onForeground()                   // a few hours later it does
        assertEquals("101", u.state.value.offer?.version)
        fake.setUpdateAvailable(102)
        u.check(force = true)              // 업데이트 확인
        assertEquals("102", u.state.value.offer?.version)
    }

    @Test fun belowTheServersMinimumStartsAnImmediateUpdate() {
        val fake = Fake()
        fake.setUpdateAvailable(105)
        val u = updates(fake, versionCode = 100)
        u.requireAtLeast(103)
        assertTrue(u.state.value.required)
        assertEquals(listOf(AppUpdateType.IMMEDIATE), fake.started)
        assertTrue(fake.isImmediateFlowVisible)

        // the user closes Play's screen: the required screen stays, and coming back does not force it open again at once
        fake.userRejectsUpdate()
        u.onFlowResult(android.app.Activity.RESULT_CANCELED)
        now += PlayUpdateRules.CHECK_EVERY_MS
        u.onForeground()
        assertEquals(1, fake.started.size)
        assertTrue(u.state.value.required)
        u.get()   // 업데이트 on the required screen starts it again
        assertEquals(listOf(AppUpdateType.IMMEDIATE, AppUpdateType.IMMEDIATE), fake.started)
    }

    @Test fun atOrAboveTheMinimumNothingIsRequired() {
        val fake = Fake()
        fake.setUpdateAvailable(105)
        val u = updates(fake, versionCode = 103)
        u.requireAtLeast(103)
        u.requireAtLeast(null)
        u.requireAtLeast(0)
        assertFalse(u.state.value.required)
        assertTrue(fake.started.isEmpty())
    }

    @Test fun requiredButPlayAllowsOnlyFlexibleLeavesTheBlockingScreen() {
        val fake = Fake()
        fake.setUpdateAvailable(105, AppUpdateType.FLEXIBLE)
        val u = updates(fake, versionCode = 100)
        u.requireAtLeast(103)
        assertTrue(u.state.value.required)
        assertTrue(fake.started.isEmpty())   // no flexible flow for a required update; the screen's button opens the Play Store
    }

    @Test fun anImmediateUpdateLeftHalfWayIsResumedOnReturn() {
        val fake = Fake()
        fake.setUpdateAvailable(105)
        updates(fake).requireAtLeast(103)
        fake.userAcceptsUpdate()
        fake.downloadStarts()
        val again = updates(fake)   // the process was killed during Play's screen and the app opened again
        val before = fake.asked.size
        again.onForeground()   // Play says DEVELOPER_TRIGGERED_UPDATE_IN_PROGRESS: the immediate flow is asked for again
        assertEquals(before + 1, fake.asked.size)
        assertEquals(AppUpdateType.IMMEDIATE, fake.asked.last())
    }
}
