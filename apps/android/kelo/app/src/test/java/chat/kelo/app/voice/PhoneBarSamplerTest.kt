package chat.kelo.app.voice

import org.junit.Assert.assertEquals
import org.junit.Test

class PhoneBarSamplerTest {
    @Test
    fun aSingleObservationHoldsUntilTheNextOne() {
        val obs = listOf(0L to 0.5f, 150L to 0.9f)
        // ticks at 80, 160, 240: 80 → 0.5, 160 → 0.9 (observed at 150), 240 → 0.9
        assertEquals(listOf(0.5f, 0.9f, 0.9f), PhoneBarSampler.sample(obs, 80L, 0L, 240L))
    }

    @Test
    fun silenceBeforeTheFirstObservationIsZero() {
        assertEquals(listOf(0f, 0f), PhoneBarSampler.sample(emptyList(), 80L, 0L, 160L))
    }

    @Test
    fun onlyTicksAfterTheLastPushAreReturned() {
        val obs = listOf(0L to 1f, 100L to 0.2f)
        // the tick at 80 was pushed before: from 80 only 160 and 240 come back, the first at 0.2
        assertEquals(listOf(0.2f, 0.2f), PhoneBarSampler.sample(obs, 80L, 80L, 240L))
    }

    @Test
    fun noTickYetGivesNothing() {
        assertEquals(emptyList<Float>(), PhoneBarSampler.sample(listOf(0L to 1f), 80L, 0L, 79L))
    }
}
