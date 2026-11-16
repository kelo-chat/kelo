package chat.kelo.app.device

import android.Manifest
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** When this phone's location goes to the server: whenever the app is opened and location is allowed, not more often than every few minutes. */
class PhonePlaceTest {
    @Test fun onlyWhenAllowedAndNotTooOften() {
        val now = 1_794_520_800_000L
        assertTrue(PhonePlace.due(granted = true, lastAt = 0L, now = now))
        assertFalse(PhonePlace.due(granted = false, lastAt = 0L, now = now))
        assertFalse(PhonePlace.due(granted = true, lastAt = now - 60_000L, now = now))
        assertTrue(PhonePlace.due(granted = true, lastAt = now - PhonePlace.EVERY_MS, now = now))
        assertTrue(PhonePlace.due(granted = true, lastAt = now + 3_600_000L, now = now))   // the clock moved back
    }

    @Test fun asksForPreciseAndApproximateTogether() {
        // both in one request: the system dialog then offers "precise" and "approximate", and either is enough
        assertEquals(setOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), PhonePlace.PERMISSIONS.toSet())
    }

    @Test fun sendsTheAccuracyWhenItIsKnown() {
        val b = PhonePlace.body(37.498123, 127.027612, 35f, "강남구", "KR")
        assertEquals("35.0", b["accuracy"]!!.jsonPrimitive.content)
        assertEquals("강남구", b["name"]!!.jsonPrimitive.content)
        val bare = PhonePlace.body(37.5, 127.0, null, "", "")
        assertNull(bare["accuracy"]); assertNull(bare["name"]); assertNull(bare["country_code"])
    }

    @Test fun theNeighbourhoodGoesWithThePlace() {
        val b = PhonePlace.body(37.498123, 127.027612, 35f, "강남구", "KR", "역삼동")
        org.junit.Assert.assertEquals("역삼동", (b["sublocality"] as kotlinx.serialization.json.JsonPrimitive).content)
        org.junit.Assert.assertNull(PhonePlace.body(37.5, 127.0, null, "", "")["sublocality"])
        org.junit.Assert.assertEquals("", PhonePlace.sublocalityOf("강남구", "강남구"))   // only repeats the town
        org.junit.Assert.assertEquals("", PhonePlace.sublocalityOf(null, "Seoul"))
        org.junit.Assert.assertEquals("Yeoksam-dong", PhonePlace.sublocalityOf(" Yeoksam-dong ", "Seoul"))
    }
}
