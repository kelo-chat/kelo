package chat.kelo.baselineprofile

import androidx.benchmark.macro.junit4.BaselineProfileRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import chat.kelo.baselineprofile.KeloJourney.browse
import chat.kelo.baselineprofile.KeloJourney.launch
import chat.kelo.baselineprofile.KeloJourney.signedIn
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Writes Kelo's baseline profile (and start-up profile) from [KeloJourney]:
 *   ./gradlew :app:generateReleaseBaselineProfile -Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true
 * with a phone or emulator (API 33+, or rooted 28+) connected and signed in to a Kelo server. Only GET requests are made.
 */
@RunWith(AndroidJUnit4::class)
class BaselineProfileGenerator {
    @get:Rule val rule = BaselineProfileRule()

    @Test
    fun generate() = rule.collect(packageName = KeloJourney.PACKAGE, includeInStartupProfile = true) {
        launch()
        if (signedIn()) browse()
    }
}
