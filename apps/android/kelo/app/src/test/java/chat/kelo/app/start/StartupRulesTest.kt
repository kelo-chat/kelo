package chat.kelo.app.start

import chat.kelo.app.SplashRules
import chat.kelo.app.core.Profile
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StartupRulesTest {
    private val kept = ProfileState.Ready(Profile(agentName = "Kelo"))

    @Test fun aKeptProfileStaysWhenTheServerCannotBeReached() {
        assertEquals(kept, ProfileRules.afterFailure(kept, unauthorized = false))
        assertEquals(ProfileState.Failed, ProfileRules.afterFailure(ProfileState.Loading, unauthorized = false))
    }

    @Test fun aRejectedTokenSignsOutEvenWithAKeptProfile() {
        assertEquals(ProfileState.SignedOut, ProfileRules.afterFailure(kept, unauthorized = true))
    }

    @Test fun theFirstScreenIsKnownOnceTheProfileIsNotBeingWaitedFor() {
        assertTrue(ProfileRules.settled(signedIn = false, state = ProfileState.SignedOut))
        assertFalse(ProfileRules.settled(signedIn = true, state = ProfileState.Loading))
        assertFalse(ProfileRules.settled(signedIn = true, state = ProfileState.SignedOut))
        assertTrue(ProfileRules.settled(signedIn = true, state = kept))
        assertTrue(ProfileRules.settled(signedIn = true, state = ProfileState.Failed))
    }

    @Test fun theSplashGoesWhenSettledOrAfterASecond() {
        assertFalse(SplashRules.canGo(settled = false, shownForMs = 200))
        assertTrue(SplashRules.canGo(settled = true, shownForMs = 5))
        assertTrue(SplashRules.canGo(settled = false, shownForMs = SplashRules.MAX_MS))
    }
}
