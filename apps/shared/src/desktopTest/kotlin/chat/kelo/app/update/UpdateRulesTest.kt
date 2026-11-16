package chat.kelo.app.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream

class UpdateRulesTest {
    private val sha = "e3edb0bf396c28c22a7b55294c5cb7a53dbeacbf343b6a8f3d66ba731a0f6093"
    private val keys = TestReleaseKey.KEYS
    private val androidSig = TestReleaseKey.members("android", "kelo-0.1.402322.apk", "0.1.402322", 0, 28981523, sha)
    private val macSig = TestReleaseKey.members("mac", "kelo-1.0.2.dmg", "1.0.2", 0, 5, sha)
    private val latest = """{"android": {"date": "2026-11-08T12:07:36Z", "file": "kelo-0.1.402322.apk", "sha256": "$sha",
        "size": 28981523, "url": "/download/android", "version": "0.1.402322", $androidSig},
        "mac": {"date": "", "file": "kelo-1.0.2.dmg", "sha256": "$sha", "size": 5, "url": "/download/mac", "version": "1.0.2", $macSig}}"""

    @Test fun versionsCompareByNumberNotByText() {
        assertTrue(UpdateRules.newer("0.1.402322", "0.1.402102"))
        assertTrue(UpdateRules.newer("0.1.100000", "0.1.99999"))     // text order would say no
        assertTrue(UpdateRules.newer("0.2.1", "0.1.999999"))
        assertTrue(UpdateRules.newer("1.0.10", "1.0.9"))
        assertFalse(UpdateRules.newer("0.1.402102", "0.1.402102"))   // the same build is not an update
        assertFalse(UpdateRules.newer("0.1.402101", "0.1.402102"))   // an older one never is
        assertFalse(UpdateRules.newer("1.2", "1.2.0"))
        assertTrue(UpdateRules.newer("1.2.1", "1.2"))
        assertFalse(UpdateRules.newer("", "0.1.1"))
        assertTrue(UpdateRules.newer("1.0.3-beta", "1.0.2"))
        assertEquals(listOf(0L, 1L, 402322L), UpdateRules.parts("0.1.402322"))
    }

    @Test fun latestJsonGivesTheAndroidRowOnlyWhenCompleteAndSigned() {
        val r = UpdateRules.parse(latest, keys = keys)!!
        assertEquals("0.1.402322", r.version)
        assertEquals(28981523L, r.size)
        assertEquals(sha, r.sha256)
        assertEquals("/download/android", r.url)
        assertEquals("1.0.2", UpdateRules.parse(latest, "mac", keys)!!.version)
        assertNull(UpdateRules.parse("""{"mac": {"version": "1.0.2"}}""", keys = keys))                     // no android row
        assertNull(UpdateRules.parse("""{"android": {"version": "0.1.9", "size": 5, "url": "/x", "sha256": "abc"}}""", keys = keys))   // bad checksum
        assertNull(UpdateRules.parse("""{"android": {"version": "0.1.9", "size": 0, "url": "/x", "sha256": "$sha"}}""", keys = keys))
        assertNull(UpdateRules.parse("not json", keys = keys))
        assertEquals(sha, UpdateRules.parse(latest.replace(sha, sha.uppercase()), keys = keys)!!.sha256)
    }

    @Test fun aRowWithoutAValidSignatureIsNeverOffered() {
        val unsigned = latest.replace(androidSig, "\"date2\": \"\"")
        assertNull(UpdateRules.parse(unsigned, keys = keys))                                           // no signature
        assertNull(UpdateRules.parse(latest, keys = emptyMap()))                                       // signed by a key the app does not know
        assertNull(UpdateRules.parse(latest))                                                          // the real key list is empty for now: nothing verifies
        assertNull(UpdateRules.parse(latest.replace("0.1.402322\", ", "0.1.402323\", "), keys = keys))   // the version changed after signing
        assertNull(UpdateRules.parse(latest.replace("28981523", "28981524"), keys = keys))              // so did the size
        assertNull(UpdateRules.parse(latest.replace("\"/download/android\"", "\"/download/android\", \"commit\": \"${"c".repeat(40)}\""), keys = keys))
        val other = TestReleaseKey.sign("mac", "kelo-0.1.402322.apk", "0.1.402322", 0, 28981523, sha)  // a signature made for another platform
        assertNull(UpdateRules.parse(latest.replace(androidSig, "\"sig\": \"$other\", \"key\": \"${TestReleaseKey.KEY_ID}\""), keys = keys))
        assertEquals("1.0.2", UpdateRules.parse(latest, "mac", keys)!!.version)                        // the mac row stands on its own
    }

    @Test fun anOfferIsOnlyANewerBuild() {
        val r = UpdateRules.parse(latest, keys = keys)
        assertEquals("0.1.402322", UpdateRules.offer(r, "0.1.400000")?.version)
        assertNull(UpdateRules.offer(r, "0.1.402322"))
        assertNull(UpdateRules.offer(r, "0.1.500000"))
        assertNull(UpdateRules.offer(null, "0.1.1"))
    }

    @Test fun theRisingBuildNumberDecidesWhenLatestJsonGivesIt() {
        val r = Release(version = "0.9.0", code = 404800, size = 1, sha256 = "a".repeat(64), url = "/download/android")
        assertEquals(404800L, UpdateRules.offer(r, "0.9.0", runningCode = 404700)?.code)   // same release, newer build
        assertNull(UpdateRules.offer(r, "0.9.0", runningCode = 404800))
        assertNull(UpdateRules.offer(r, "0.1.405000", runningCode = 405000))              // an old naming with a higher build
        // an older latest.json without the build: the version names, as before (0.9.0 is not offered over itself)
        assertNull(UpdateRules.offer(r.copy(code = 0), "0.9.0", runningCode = 1))
        val b = "b".repeat(64)
        val sig = TestReleaseKey.members("android", "kelo-0.9.0-404800.apk", "0.9.0", 404800, 5, b)
        assertEquals(404800L, UpdateRules.parse("""{"android":{"file":"kelo-0.9.0-404800.apk","version":"0.9.0","version_code":404800,"size":5,"sha256":"$b","url":"/x",$sig}}""", keys = keys)?.code)
    }

    @Test fun theFileAddressIsResolvedAgainstWhereTheListCameFromAndNeverLeavesIt() {
        assertEquals("https://kelo.chat/download/android", UpdateRules.resolve("https://kelo.chat", "/download/android"))
        assertEquals("http://127.0.0.1:8931/download/android", UpdateRules.resolve("http://127.0.0.1:8931/", "/download/android"))
        assertNull(UpdateRules.resolve("https://kelo.chat", "https://cdn.example/k.apk"))             // another host
        assertNull(UpdateRules.resolve("https://kelo.chat", "//cdn.example/k.apk"))
        assertNull(UpdateRules.resolve("http://10.0.2.2:8931/", "/download/android"))                 // plain http on a public/LAN host
    }

    @Test fun downloadsStayOnTheListsOwnHttpsOrigin() {
        assertTrue(UpdateUrls.allowed("https://kelo.chat", "https://kelo.chat/download/android"))
        assertTrue(UpdateUrls.allowed("https://kelo.chat/", "https://KELO.chat:443/download/android"))
        assertFalse(UpdateUrls.allowed("https://kelo.chat", "https://cdn.kelo.chat/download/android"))   // another host
        assertFalse(UpdateUrls.allowed("https://kelo.chat", "https://evil.example/download/android"))
        assertFalse(UpdateUrls.allowed("https://kelo.chat", "https://kelo.chat@evil.example/x"))
        assertFalse(UpdateUrls.allowed("https://kelo.chat", "http://kelo.chat/download/android"))        // http on a public host
        assertFalse(UpdateUrls.allowed("http://kelo.chat", "http://kelo.chat/download/android"))
        assertFalse(UpdateUrls.allowed("https://kelo.chat", "https://kelo.chat:8443/download/android"))  // another port
        assertFalse(UpdateUrls.allowed("https://kelo.chat", "file:///etc/passwd"))
        assertFalse(UpdateUrls.allowed("https://kelo.chat", "not a url"))
        assertTrue(UpdateUrls.allowed("http://127.0.0.1:8931", "http://127.0.0.1:8931/download/android"))   // a local mock
        assertTrue(UpdateUrls.allowed("http://localhost:9", "http://localhost:9/x"))
        assertTrue(UpdateUrls.allowed("http://[::1]:9", "http://[::1]:9/x"))
        assertFalse(UpdateUrls.allowed("http://127.0.0.1:8931", "http://127.0.0.1:8932/x"))
        assertFalse(UpdateUrls.allowed("http://127.0.0.1:8931", "http://localhost:8931/x"))
    }

    @Test fun theDownloadMustMatchThePublishedChecksumAndSize() {
        val bytes = "hello kelo".toByteArray()
        val digest = UpdateRules.sha256(ByteArrayInputStream(bytes))
        assertEquals(64, digest.length)
        val r = Release(version = "0.1.2", size = bytes.size.toLong(), sha256 = digest, url = "/download/android")
        assertTrue(UpdateRules.verified(digest, bytes.size.toLong(), r))
        assertTrue(UpdateRules.verified(digest.uppercase(), bytes.size.toLong(), r))
        assertFalse(UpdateRules.verified(sha, bytes.size.toLong(), r))            // another file
        assertFalse(UpdateRules.verified(digest, bytes.size + 1L, r))             // cut short or padded
        // a known vector: sha256("abc")
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", UpdateRules.sha256(ByteArrayInputStream("abc".toByteArray())))
    }

    @Test fun checksAtStartThenOnceADayAndTheBannerOncePerVersion() {
        assertTrue(UpdateRules.due(null, 1_000))
        assertFalse(UpdateRules.due(1_000, 1_000 + UpdateRules.CHECK_EVERY_MS - 1))
        assertTrue(UpdateRules.due(1_000, 1_000 + UpdateRules.CHECK_EVERY_MS))
        assertTrue(UpdateRules.due(5_000, 1_000))                        // the clock went back: check again
        assertTrue(UpdateRules.showBanner("0.1.5", null))
        assertFalse(UpdateRules.showBanner("0.1.5", "0.1.5"))
        assertTrue(UpdateRules.showBanner("0.1.6", "0.1.5"))
        assertFalse(UpdateRules.showBanner(null, null))
    }

    @Test fun theServersMinimumHoldsBackOnlyOlderBuilds() {
        assertTrue(UpdateRules.belowMin(402300, 402400))
        assertFalse(UpdateRules.belowMin(402400, 402400))   // the minimum itself is fine
        assertFalse(UpdateRules.belowMin(402500, 402400))
        assertFalse(UpdateRules.belowMin(402300, null))     // an older server says nothing
        assertFalse(UpdateRules.belowMin(402300, 0))        // 0 = no minimum
        assertFalse(UpdateRules.belowMin(0, 402400))        // an unknown running build is never locked out
    }

    @Test fun aRequiredUpdateReplacesTheBanner() {
        val offered = UpdateState(enabled = true, offer = Release(version = "0.1.9"))
        assertTrue(offered.showBanner)
        assertFalse(offered.copy(required = true).showBanner)   // the required screen covers the app instead
        assertFalse(offered.copy(enabled = false).showBanner)
    }

    @Test fun onlyTheBuildsHandedOutAsAFileCheck() {
        assertTrue(UpdateRules.supported("sideload"))
        assertTrue(UpdateRules.supported("full"))
        assertFalse(UpdateRules.supported("play"))
    }

    @Test fun theCheckSaysOnlyWhichAppAndVersionAsks() {
        assertEquals("Kelo-Android/0.1.402322", UpdateRules.userAgent("0.1.402322"))
        assertEquals("27.6\u00A0MB", UpdateRules.size(28981523))
        assertEquals(0, UpdateRules.percent(5, 0))
        assertEquals(50, UpdateRules.percent(5, 10))
        assertEquals(100, UpdateRules.percent(12, 10))
    }
}
