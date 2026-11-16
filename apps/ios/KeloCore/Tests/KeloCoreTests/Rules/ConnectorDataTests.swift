import Foundation
import Testing
@testable import KeloCore

/// What a disconnect and a forget answer about a connector's data (server connector_data.py; the app asks only when something is left).
@Suite struct ConnectorDataTests {
    private func json(_ text: String) throws -> JSON { try JSON.parse(text) }

    @Test func aDisconnectAnswerCarriesTheCounts() throws {
        let c = ConnectorDataCounts.afterDisconnect(try json(#"{"ok":true,"data":{"files":3,"notes":0,"items":5}}"#))
        #expect(c == ConnectorDataCounts(files: 3, notes: 0, items: 5))
        #expect(c?.any == true)
    }

    @Test func aDisconnectAnswerWithoutDataAsksNothing() throws {
        // an older server answers only {"ok": true}: no question at all
        #expect(ConnectorDataCounts.afterDisconnect(try json(#"{"ok":true}"#)) == nil)
        #expect(ConnectorDataCounts.afterDisconnect(try json(#"{"ok":true,"data":null}"#)) == nil)
        #expect(ConnectorDataCounts.afterDisconnect(try json("null")) == nil)
    }

    @Test func zeroCountsAskNothing() throws {
        let c = ConnectorDataCounts.afterDisconnect(try json(#"{"ok":true,"data":{"files":0,"notes":0,"items":0}}"#))
        #expect(c != nil)
        #expect(c?.any == false)
    }

    @Test func missingCountsAreZero() throws {
        let c = ConnectorDataCounts.afterDisconnect(try json(#"{"ok":true,"data":{"items":2}}"#))
        #expect(c == ConnectorDataCounts(files: 0, notes: 0, items: 2))
    }

    @Test func aForgetAnswerIsReadWrappedOrAtTheTop() throws {
        #expect(ConnectorDataCounts.afterForget(try json(#"{"files":1,"notes":2,"items":3}"#)) == ConnectorDataCounts(files: 1, notes: 2, items: 3))
        #expect(ConnectorDataCounts.afterForget(try json(#"{"ok":true,"data":{"files":4,"notes":0,"items":0}}"#)) == ConnectorDataCounts(files: 4))
    }
}
