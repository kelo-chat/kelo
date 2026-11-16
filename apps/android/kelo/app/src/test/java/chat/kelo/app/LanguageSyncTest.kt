package chat.kelo.app

import chat.kelo.app.LanguageSync.Step
import chat.kelo.app.core.Languages
import org.junit.Assert.assertEquals
import org.junit.Test

/** The app has no language step on first launch: it follows the phone until a language is picked in My -> Language. */
class LanguageSyncTest {
    @Test fun aNewInstallFollowsThePhoneAndWritesNothing() {
        assertEquals(Step.Nothing, LanguageSync.step(stored = null, synced = false, systemTag = null))
        assertEquals(Step.Nothing, LanguageSync.step(stored = null, synced = true, systemTag = null))
    }

    @Test fun aLanguageSetInTheSystemsPerAppSettingIsTakenEvenWithNoPick() {
        assertEquals(Step.Keep("ja"), LanguageSync.step(stored = null, synced = false, systemTag = "ja-JP"))
        assertEquals(Step.Keep(Languages.SYSTEM), LanguageSync.step(stored = null, synced = true, systemTag = "sw-KE"))
    }

    @Test fun aPickFromAnEarlierVersionsFirstScreenIsKeptAndHandedToTheSystemOnce() {
        assertEquals(Step.HandToSystem("ko"), LanguageSync.step(stored = "ko", synced = false, systemTag = null))
        assertEquals(Step.HandToSystem(Languages.SYSTEM), LanguageSync.step(stored = Languages.SYSTEM, synced = false, systemTag = null))
    }

    @Test fun afterThatTheSystemSettingWins() {
        assertEquals(Step.Keep("fr"), LanguageSync.step(stored = "ko", synced = true, systemTag = "fr-FR"))
        assertEquals(Step.Keep(Languages.SYSTEM), LanguageSync.step(stored = "ko", synced = true, systemTag = null))
        assertEquals(Step.Nothing, LanguageSync.step(stored = Languages.SYSTEM, synced = true, systemTag = null))
    }
}
