package chat.kelo.app.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdatePopupTest {
    private val hour = 60L * 60 * 1000
    private fun rel(v: String, notes: String = "") = Release(version = v, size = 5, sha256 = "a".repeat(64), url = "/d", notes = notes)
    private fun state(running: String, offer: Release? = null) = UpdateState(enabled = true, running = running, offer = offer)
    private val cand = UpdateCandidate("0.9.1", "", UpdatePopupRules.Source.STORE)
    private val newer = UpdateCandidate("0.9.2", "", UpdatePopupRules.Source.STORE)

    private class Clock(var now: Long = 1_000_000_000L)
    private fun popup(c: Clock, saved: MutableList<String> = mutableListOf(), start: String? = null) =
        UpdatePopup({ start }, { saved += it }, { c.now })

    /** shown [ago] hours before now, then the app starts again (armed) */
    private fun shownAgo(c: Clock, ago: Long): UpdatePopup = popup(c, start = (c.now - ago * hour).toString())

    @Test fun aFileBuildTakesTheSignedOfferWithItsNotes() {
        val c = UpdatePopupRules.candidate(state("0.9.0", rel("0.9.1", " Faster start ")), null, "download")!!
        assertEquals("0.9.1", c.version); assertEquals("Faster start", c.notes); assertEquals(UpdatePopupRules.Source.UPDATER, c.source)
    }

    @Test fun aStoreBuildTakesTheServersVersionAndOpensTheStore() {
        for (channel in listOf("play", "appstore", "msstore")) {
            assertEquals(UpdatePopupRules.Source.STORE, UpdatePopupRules.candidate(state("0.9.0", rel("0.9.1")), "0.9.1", channel)!!.source)
        }
        assertEquals("0.9.1", UpdatePopupRules.candidate(state("0.9.0", rel("0.9.5")), "0.9.1", "play")!!.version)
    }

    @Test fun aStoreBuildNeedsTheStoreToSayNewerFirst() {
        // the store says none, although the server names a newer app (its signed store row exists): no popup
        for (channel in listOf("play", "appstore", "msstore")) {
            assertNull(UpdatePopupRules.candidate(state("0.9.0"), "0.9.1", channel))
        }
    }

    @Test fun aStoreOfferWithoutTheServersNameShowsNothing() {
        // the store has a newer build, but the server names none for this server (the store row's min_server is above it)
        assertNull(UpdatePopupRules.candidate(state("0.9.0", rel("0.9.1")), null, "play"))
        assertNull(UpdatePopupRules.candidate(state("0.9.0", rel("0.9.1")), " ", "appstore"))
    }

    @Test fun aStoreBuildWithoutAnOfferOfItsOwnTakesTheStoresWordAsTheOffer() {
        // the Windows Store copy has no Release offer: the Store's own answer (storeNewer) is what says newer comes first
        val storeSaysNewer = state("0.9.0").copy(storeNewer = true)
        assertEquals(UpdatePopupRules.Source.STORE, UpdatePopupRules.candidate(storeSaysNewer, "0.9.1", "msstore")!!.source)
        assertEquals("0.9.1", UpdatePopupRules.candidate(storeSaysNewer, "0.9.1", "msstore")!!.version)
        // the Store says none: no popup, although the server names a newer app
        assertNull(UpdatePopupRules.candidate(state("0.9.0"), "0.9.1", "msstore"))
        // the Store says newer but the server names none for this server (min_server): nothing
        assertNull(UpdatePopupRules.candidate(storeSaysNewer, null, "msstore"))
        // the Store's word is not the file build's: it takes its news from the server and opens its own updater
        assertEquals(UpdatePopupRules.Source.UPDATER, UpdatePopupRules.candidate(storeSaysNewer, "0.9.1", "download")!!.source)
    }

    @Test fun aStoreBuildNeverTakesTheFileOffer() {
        // a store build's notice comes from the store, not from kelo.chat's file rows
        assertNull(UpdatePopupRules.candidate(state("0.9.0", null), null, "play"))
    }

    @Test fun anOlderOrEqualVersionNeverPopsUp() {
        assertNull(UpdatePopupRules.candidate(state("0.9.1"), "0.9.1", "play"))
        assertNull(UpdatePopupRules.candidate(state("0.9.2"), "0.9.1", "play"))
        assertNull(UpdatePopupRules.candidate(state("0.9.0"), null, "play"))
        assertNull(UpdatePopupRules.candidate(state("0.9.1"), "0.9.0", "download"))
        assertNull(UpdatePopupRules.candidate(state("0.9.0"), " ", "download"))
        val c = Clock(); val p = popup(c)
        assertFalse(p.consider(null)); assertNull(p.open.value)   // already on the newest version: nothing, and nothing is recorded
        assertTrue(p.consider(cand))
    }

    @Test fun aFileBuildFallsBackToTheServersVersion() {
        val c = UpdatePopupRules.candidate(state("0.9.0"), "0.9.1", "download")!!
        assertEquals(UpdatePopupRules.Source.UPDATER, c.source); assertEquals("", c.notes)
    }

    @Test fun theNotesAreCutToOneShortLine() {
        assertEquals(UpdatePopupRules.NOTES_MAX, UpdatePopupRules.candidate(state("0.9.0", rel("0.9.1", "x".repeat(1000))), null, "download")!!.notes.length)
    }

    @Test fun shown23HoursAgoDoesNotShowAgain() {
        val p = shownAgo(Clock(), 23)
        assertFalse(p.consider(cand)); assertNull(p.open.value)
    }

    @Test fun shown25HoursAgoShows() {
        val p = shownAgo(Clock(), 25)
        assertTrue(p.consider(cand)); assertEquals(cand, p.open.value)
    }

    @Test fun aNewerVersionThanTheLastShownOneStillWaitsFor24Hours() {
        val c = Clock(); val saved = mutableListOf<String>()
        val p = popup(c, saved)
        assertTrue(p.consider(cand)); p.close()
        c.now += 5 * hour
        p.left(); c.now += 2 * hour; assertTrue(p.returned())    // back after more than an hour: armed again
        assertFalse(p.consider(newer))                            // 7 h after the first showing
        c.now += 17 * hour + 1
        p.left(); c.now += 2 * hour; p.returned()
        assertTrue(p.consider(newer))                             // past 24 h
        assertEquals(2, saved.size)
    }

    @Test fun whateverTheAnswerItWaitsADayAndAnswerClosesIt() {
        val c = Clock(); val p = popup(c)
        assertTrue(p.consider(cand)); assertTrue(p.consider(cand))   // asking again while open keeps it open, records once
        p.close(); assertNull(p.open.value)
        p.left(); c.now += 2 * hour; p.returned()
        assertFalse(p.consider(cand))
    }

    @Test fun backAfterMoreThanAnHourArmsItAgainButNotSooner() {
        val c = Clock(); val p = shownAgo(c, 30)
        p.consider(cand); p.close()
        c.now += 30 * hour
        p.left(); c.now += UpdatePopupRules.AWAY_MS
        assertFalse(p.returned()); assertFalse(p.consider(cand))    // exactly an hour is not enough
        p.left(); c.now += UpdatePopupRules.AWAY_MS + 1
        assertTrue(p.returned()); assertTrue(p.consider(cand))
    }

    @Test fun theTimeSurvivesARestartAndBadStorageIsHarmless() {
        val saved = mutableListOf<String>(); val c = Clock()
        assertTrue(popup(c, saved).consider(cand))
        c.now += 2 * hour
        assertFalse(popup(c, start = saved.last()).consider(cand))
        val broken = UpdatePopup({ error("no storage") }, { error("no storage") }, { c.now })
        assertTrue(broken.consider(cand))
        assertTrue(popup(c, start = "garbage").consider(cand))
        assertTrue(UpdatePopupRules.due(c.now + hour, c.now))   // a clock set back does not block it
    }
}
