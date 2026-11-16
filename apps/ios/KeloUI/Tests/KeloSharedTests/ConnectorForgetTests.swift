import Foundation
import Testing
import KeloCore
@testable import KeloShared

/// After a disconnect: the question "also delete the data?" is asked only when the answer carried data and something is left, and it
/// says the counts (zero parts left out) plus the trash line when there are files. Words are compared with the same L(...) calls, so the
/// tests do not depend on the test machine's language.
@Suite struct ConnectorForgetTests {
    @Test @MainActor func noDataInTheAnswerAsksNothing() {
        // an older server answers only {"ok": true}: nil data
        #expect(ForgetRules.ask(item: "mail-gmail", name: "Gmail", data: nil) == nil)
    }

    @Test @MainActor func nothingLeftAsksNothing() {
        #expect(ForgetRules.ask(item: "mail-gmail", name: "Gmail", data: ConnectorDataCounts(files: 0, notes: 0, items: 0)) == nil)
    }

    @Test @MainActor func anyCountAboveZeroAsks() {
        let counts = ConnectorDataCounts(files: 0, notes: 0, items: 5)
        let ask = ForgetRules.ask(item: "mail-gmail", name: "Gmail", data: counts)
        #expect(ask == ForgetAsk(item: "mail-gmail", name: "Gmail", counts: counts))
        #expect(ask?.id == "mail-gmail")
    }

    @Test @MainActor func theCountsAreWrittenWithZeroPartsLeftOut() {
        let body = ForgetRules.body(ConnectorDataCounts(files: 0, notes: 2, items: 5))
        #expect(body == [L(R.string.connector_forget_notes, 2), L(R.string.connector_forget_items, 5)].joined(separator: " · "))
    }

    @Test @MainActor func aFileAddsTheTrashLine() {
        let body = ForgetRules.body(ConnectorDataCounts(files: 3, notes: 2, items: 5))
        let parts = [L(R.string.connector_forget_files, 3), L(R.string.connector_forget_notes, 2), L(R.string.connector_forget_items, 5)]
        #expect(body == (parts.joined(separator: " · ") + "\n" + L(R.string.connector_forget_trash)))
    }

    @Test @MainActor func noFilesMeansNoTrashLine() {
        let body = ForgetRules.body(ConnectorDataCounts(files: 0, notes: 1, items: 0))
        #expect(!body.contains(L(R.string.connector_forget_trash)))
        #expect(body == L(R.string.connector_forget_notes, 1))
    }

    @Test @MainActor func aDisconnectAnswerDecidesTheQuestion() throws {
        // the disconnect's answer as the app reads it (KeloCore ConnectorDataCounts.afterDisconnect)
        let answer = try JSON.parse(#"{"ok":true,"data":{"files":3,"notes":0,"items":5}}"#)
        let ask = ForgetRules.ask(item: "gmail", name: "Gmail", data: ConnectorDataCounts.afterDisconnect(answer))
        #expect(ask?.counts == ConnectorDataCounts(files: 3, notes: 0, items: 5))
        let none = try JSON.parse(#"{"ok":true}"#)
        #expect(ForgetRules.ask(item: "gmail", name: "Gmail", data: ConnectorDataCounts.afterDisconnect(none)) == nil)
    }
}
