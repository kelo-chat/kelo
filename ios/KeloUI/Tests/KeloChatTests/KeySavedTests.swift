import Testing
import Foundation
import KeloCore
@testable import KeloChat

private func keys() -> () -> String {
    var n = 0
    return { n += 1; return "k\(n)" }
}

@Suite struct KeySavedTests {
    @Test func parseServerLineWithTwoHosts() {
        let line = "Saved the key for 'openweather' in the keychain (hosts: api.openweathermap.org, openweathermap.org). You never see it."
        let parsed = KeySavedRules.parse(line)
        #expect(parsed?.name == "openweather")
        #expect(parsed?.hosts == ["api.openweathermap.org", "openweathermap.org"])
    }

    @Test func otherToolLineGivesNil() {
        let line = "error: no hidden secret #2 in this conversation turn"
        #expect(KeySavedRules.parse(line) == nil)
    }

    @Test func toolResultOfSaveFoundSecretAddsKeySavedItem() {
        let k = keys()
        var items: [ChatItem] = []
        let output = "Saved the key for 'acme' in the keychain (hosts: api.acme.com, other.acme.com). You never see it."
        items = ChatReducer.apply(items, .toolStarted(.init(id: "s1", name: "save_found_secret")), newKey: k)
        items = ChatReducer.apply(items, .toolResult(.init(id: "s1", name: "save_found_secret", output: output, isError: false, approved: nil)), newKey: k)
        let saved = items.compactMap { if case .keySaved(let s) = $0 { return s } else { return nil } }
        #expect(saved.count == 1)
        #expect(saved[0].name == "acme")
        #expect(saved[0].hosts == ["api.acme.com", "other.acme.com"])
    }
}
