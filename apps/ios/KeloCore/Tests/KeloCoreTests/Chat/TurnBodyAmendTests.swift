import Testing
@testable import KeloCore

/// POST /api/turns "amend": sent only with a call's words (owner 2026-11-20: the words replace the call's unanswered words).
@Suite struct TurnBodyAmendTests {
    @Test func amendIsSentWithACall() {
        #expect(TurnBody.of(text: "a", timezone: "UTC", call: "c1234567", amend: true)["amend"]?.boolValue == true)
    }

    @Test func amendIsLeftOutWithoutACallOrWhenFalse() {
        #expect(TurnBody.of(text: "a", timezone: "UTC", call: nil, amend: true)["amend"] == nil)
        #expect(TurnBody.of(text: "a", timezone: "UTC", call: "c1234567", amend: false)["amend"] == nil)
        #expect(TurnBody.of(text: "a", timezone: "UTC")["amend"] == nil)
    }
}
