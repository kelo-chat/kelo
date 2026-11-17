import Testing
import Foundation
import KeloCore
@testable import KeloChat

private func keys() -> () -> String {
    var n = 0
    return { n += 1; return "k\(n)" }
}

private func saved(_ service: String, _ hosts: [String]) -> JSONObject {
    ["saved_key": .object(["service": .string(service), "hosts": .array(hosts.map { .string($0) })])]
}

@Suite struct KeySavedTests {
    @Test func theServerFactsGiveNameAndHosts() {
        let r = TurnEvent.ToolResult(id: "1", name: "save_found_secret", output: "", isError: false, approved: nil,
                                     facts: saved("openweather", ["api.openweathermap.org", "openweathermap.org"]))
        let parsed = KeySavedRules.of(r)
        #expect(parsed?.name == "openweather")
        #expect(parsed?.hosts == ["api.openweathermap.org", "openweathermap.org"])
    }

    @Test func theWordsOfTheTextAreNeverRead() {
        let line = "Saved the key for 'openweather' in the keychain (hosts: api.openweathermap.org). You never see it."
        #expect(KeySavedRules.of(.init(id: "1", name: "save_found_secret", output: line, isError: false, approved: nil)) == nil)
        #expect(KeySavedRules.of(.init(id: "1", name: "x", output: "error: no hidden secret #2 in this conversation turn", isError: true, approved: nil)) == nil)
    }

    @Test func toolResultOfSaveFoundSecretAddsKeySavedItem() {
        let k = keys()
        var items: [ChatItem] = []
        items = ChatReducer.apply(items, .toolStarted(.init(id: "s1", name: "save_found_secret")), newKey: k)
        items = ChatReducer.apply(items, .toolResult(.init(id: "s1", name: "save_found_secret", output: "", isError: false, approved: nil,
                                                           facts: saved("acme", ["api.acme.com", "other.acme.com"]))), newKey: k)
        let savedItems = items.compactMap { if case .keySaved(let s) = $0 { return s } else { return nil } }
        #expect(savedItems.count == 1)
        #expect(savedItems[0].name == "acme")
        #expect(savedItems[0].hosts == ["api.acme.com", "other.acme.com"])
    }

    @Test func connectServiceShowsNoCardWhenTheFactSaysAlready() {
        let k = keys()
        func chips(_ facts: JSONObject, output: String) -> [ChatItem] {
            var items: [ChatItem] = []
            items = ChatReducer.apply(items, .toolStarted(.init(id: "c", name: "connect_service", arguments: ["service": .string("github")])), newKey: k)
            return ChatReducer.apply(items, .toolResult(.init(id: "c", name: "connect_service", output: output, isError: false, approved: nil, facts: facts)), newKey: k)
        }
        func hasConnect(_ items: [ChatItem]) -> Bool { items.contains { if case .connect = $0 { return true }; return false } }
        #expect(!hasConnect(chips(["already": .bool(true)], output: "github is connected")))
        #expect(hasConnect(chips([:], output: "github is already connected")))   // the sentence says nothing
    }
}
