package chat.kelo.app.my

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** GET /api/vault: a login's `browser` sign-in and the `browser_sites` without a saved login (brief B4). */
class BrowserSignInsTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun aLoginCarriesItsBrowserSignInAndTheSitesWithoutALoginAreListed() {
        val v = json.decodeFromString<VaultState>(
            """{"locked":false,"items":[{"name":"login-github","kind":"login","label":"GitHub","hosts":["github.com"],
               "has_secret":true,"browser":{"site":"github.com","how":"login","signed_at":100.5,"last_used":200.0}},
               {"name":"login-x","kind":"login","label":"X","hosts":["x.com"],"has_secret":true}],
               "browser_sites":[{"site":"shop.example","how":"owner","signed_at":5.0,"last_used":6.0}]}""",
        )
        assertEquals(BrowserSignIn(site = "github.com", how = "login", signedAt = 100.5, lastUsed = 200.0), v.items[0].browser)
        assertNull(v.items[1].browser)
        assertEquals(listOf(BrowserSignIn(site = "shop.example", how = "owner", signedAt = 5.0, lastUsed = 6.0)), v.browserSites)
    }

    @Test fun withoutTheFieldsNothingIsShown() {
        val v = json.decodeFromString<VaultState>("""{"items":[{"name":"k","kind":"api"}]}""")
        assertNull(v.items[0].browser)
        assertEquals(emptyList<BrowserSignIn>(), v.browserSites)
    }

    @Test fun namedProfilesAreListedWithTheirSitesAndAnOlderServerHasNone() {
        val v = json.decodeFromString<VaultState>(
            """{"items":[],"browser_profiles":[{"id":"p_ab12cd34","label":"Second","created_at":9.0,
               "sites":[{"site":"shop.example","how":"login","signed_at":1.0,"last_used":2.0}]},{"id":"p_x","label":"Empty"}]}""",
        )
        assertEquals(listOf("p_ab12cd34", "p_x"), v.browserProfiles.map { it.id })
        assertEquals("Second", v.browserProfiles[0].label)
        assertEquals(listOf("shop.example"), v.browserProfiles[0].sites.map { it.site })
        assertEquals(emptyList<BrowserSignIn>(), v.browserProfiles[1].sites)
        assertEquals(emptyList<BrowserProfile>(), json.decodeFromString<VaultState>("""{"items":[]}""").browserProfiles)
    }
}
