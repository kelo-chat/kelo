import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

/// The vault item behind the compact card: its user name hint, and what the sheet's Save sends.
@MainActor
struct LoginSheetChatTests {
    private func item(_ json: String) throws -> ChatVaultItem {
        try JSONDecoder().decode(ChatVaultItem.self, from: Data(json.utf8))
    }

    @Test func theVaultItemCarriesTheHintAndAnOlderServerHasNone() throws {
        #expect(try item(#"{"name":"login-a","kind":"login","hosts":["a.com"],"username_hint":"me@a.com"}"#).usernameHint == "me@a.com")
        #expect(try item(#"{"name":"login-a","kind":"login","hosts":["a.com"]}"#).usernameHint == "")
        #expect(try item(#"{"name":"login-a","kind":"login","username_hint":""}"#).usernameHint == "")
    }

    @Test func theCheckboxDecidesTheModeSentToTheServer() async throws {
        let login = try item(#"{"name":"login-a","kind":"login","hosts":["a.com"]}"#)
        let t = RoutedTransport()
        t.on("PUT /api/vault/login-a/login", "{}")
        let api = KeloApi(baseUrl: "https://kelo.test", token: "tok", transport: t)
        let m = SecureEntrySaveModel()
        var saved = 0
        m.save(login, user: "me@a.com", pass: "pw", mode: LoginSheetRules.mode(saveInKeychain: false), api: api) { saved += 1 }
        await m.jobs.settled()
        #expect(saved == 1)
        #expect(t.calls.last?.body.contains("\"mode\":\"once\"") == true)
        m.save(login, user: "me@a.com", pass: "pw", mode: LoginSheetRules.mode(saveInKeychain: true), api: api) { saved += 1 }
        await m.jobs.settled()
        #expect(t.calls.last?.body.contains("\"mode\":\"after_success\"") == true)
    }

    @Test func aRefusedSaveKeepsTheSheetAndShowsWhy() async throws {
        let login = try item(#"{"name":"login-a","kind":"login"}"#)
        let t = RoutedTransport()
        t.on("PUT /api/vault/login-a/login", 422, #"{"detail":"nope"}"#)
        let api = KeloApi(baseUrl: "https://kelo.test", token: "tok", transport: t)
        let m = SecureEntrySaveModel()
        var saved = 0
        m.save(login, user: "u", pass: "p", mode: .afterSuccess, api: api) { saved += 1 }
        await m.jobs.settled()
        #expect(saved == 0)
        #expect(!m.msg.isEmpty)
    }
}
