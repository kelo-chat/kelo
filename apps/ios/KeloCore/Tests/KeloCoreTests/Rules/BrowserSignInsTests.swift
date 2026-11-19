import Foundation
import Testing
@testable import KeloCore

/// GET /api/vault: a login's `browser` sign-in and the `browser_sites` without a saved login (brief B4).
@Suite struct BrowserSignInsTests {
    @Test func aLoginCarriesItsBrowserSignInAndTheSitesWithoutALoginAreListed() throws {
        let v = try JSONDecoder().decode(VaultState.self, from: Data("""
            {"locked":false,"items":[{"name":"login-github","kind":"login","label":"GitHub","hosts":["github.com"],
             "has_secret":true,"browser":{"site":"github.com","how":"login","signed_at":100.5,"last_used":200.0}},
             {"name":"login-x","kind":"login","label":"X","hosts":["x.com"],"has_secret":true}],
             "browser_sites":[{"site":"shop.example","how":"owner","signed_at":5.0,"last_used":6.0}]}
            """.utf8))
        #expect(v.items[0].browser == BrowserSignIn(site: "github.com", how: "login", signedAt: 100.5, lastUsed: 200.0))
        #expect(v.items[1].browser == nil)
        #expect(v.browserSites == [BrowserSignIn(site: "shop.example", how: "owner", signedAt: 5.0, lastUsed: 6.0)])
    }

    @Test func withoutTheFieldsNothingIsShown() throws {
        let v = try JSONDecoder().decode(VaultState.self, from: Data(#"{"items":[{"name":"k","kind":"api"}]}"#.utf8))
        #expect(v.items[0].browser == nil)
        #expect(v.browserSites.isEmpty)
    }
}

@Suite struct BrowserProfilesTests {
    @Test func namedProfilesAreListedWithTheirSitesAndAnOlderServerHasNone() throws {
        let v = try JSONDecoder().decode(VaultState.self, from: Data("""
            {"items":[],"browser_profiles":[{"id":"p_ab12cd34","label":"Second","created_at":9.0,
             "sites":[{"site":"shop.example","how":"login","signed_at":1.0,"last_used":2.0}]},{"id":"p_x","label":"Empty"}]}
            """.utf8))
        #expect(v.browserProfiles.map(\.id) == ["p_ab12cd34", "p_x"])
        #expect(v.browserProfiles[0].label == "Second")
        #expect(v.browserProfiles[0].sites.map(\.site) == ["shop.example"])
        #expect(v.browserProfiles[1].sites.isEmpty)
        #expect(try JSONDecoder().decode(VaultState.self, from: Data(#"{"items":[]}"#.utf8)).browserProfiles.isEmpty)
    }
}
