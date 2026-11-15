package chat.kelo.app.update

import chat.kelo.app.BuildConfig
import org.junit.Assert.assertEquals
import org.junit.Test

class UpdateBuildTest {
    @Test fun theCheckIsOnForEveryBuildButPlay() {
        // this very build: the Play unit tests run with FLAVOR "play" and must have the check off
        assertEquals(BuildConfig.FLAVOR != "play", UpdateRules.supported(BuildConfig.FLAVOR))
        if (BuildConfig.BUILD_TYPE == "release") assertEquals("https://kelo.chat", BuildConfig.UPDATE_BASE)
    }
}
