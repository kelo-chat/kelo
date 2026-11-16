import Testing
import Foundation
import KeloCore
@testable import KeloWork

@Suite struct WatcherDecodingTests {
    @Test func aWatcherWithBuyReadyDecodesProblemsSignInAndRemembered() throws {
        let json = """
        {
            "id": 42,
            "name": "Special Watcher",
            "buy": {
                "armed": true,
                "tab": "buy:42",
                "last_kept_at": 1700000000.5,
                "signed_in": true,
                "problems": [
                    {"code": "out_of_stock", "text": "Item is temporarily out of stock"},
                    {"code": "address_missing", "text": "No shipping address selected"}
                ],
                "remembered": true
            }
        }
        """
        let w = try JSONDecoder().decode(Watcher.self, from: Data(json.utf8))
        #expect(w.id == 42)
        let b = try #require(w.buy)
        #expect(b.armed == true)
        #expect(b.lastKeptAt == 1700000000.5)
        #expect(b.signedIn == true)
        #expect(b.remembered == true)
        #expect(b.problems.count == 2)
        #expect(b.problems[0].code == "out_of_stock")
        #expect(b.problems[0].text == "Item is temporarily out of stock")
        #expect(b.problems[1].code == "address_missing")
        #expect(b.problems[1].text == "No shipping address selected")
    }

    @Test func aWatcherWithoutBuyGivesNil() throws {
        let json = """
        {
            "id": 43,
            "name": "Regular Watcher"
        }
        """
        let w = try JSONDecoder().decode(Watcher.self, from: Data(json.utf8))
        #expect(w.id == 43)
        #expect(w.buy == nil)

        let jsonNull = """
        {
            "id": 44,
            "name": "Watcher With Null Buy",
            "buy": null
        }
        """
        let wNull = try JSONDecoder().decode(Watcher.self, from: Data(jsonNull.utf8))
        #expect(wNull.id == 44)
        #expect(wNull.buy == nil)
    }
}
