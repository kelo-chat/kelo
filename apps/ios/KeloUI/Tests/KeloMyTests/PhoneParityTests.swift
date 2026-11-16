import Testing
import Foundation
import KeloCore
import KeloShared
@testable import KeloMy

@Suite struct PhoneParityTests {
    @Test func idMapIsStable() {
        var m = PhoneIdMap()
        let a = m.number("EV-A"), b = m.number("EV-B")
        #expect(a != b)
        #expect(m.number("EV-A") == a)
        #expect(m.identifier(b) == "EV-B")
        m.keep(["EV-B"])
        #expect(m.identifier(a) == nil)
        #expect(m.number("EV-C") > b)   // numbers are never given out again
    }

    @Test func snapshotWithWriteCalendarAndNumberIds() {
        let zone = TimeZone(identifier: "Asia/Seoul")!
        var m = PhoneIdMap()
        let clean = PhoneCalendarRules.clean([PhoneEvent(id: "EV-1", title: "Lunch", start: 100, end: 200, allDay: false)], zone: zone) { m.number($0) }
        #expect(clean[0]["id"]?.int64Value == 1)
        let snap = PhoneCalendarRules.snapshot(clean, window: (0, 1), zone: zone, deviceName: "iPhone", write: (7, "Home"))
        #expect(snap["can_write"]?.boolValue == true)
        #expect(snap["write_calendar"]?["id"]?.int64Value == 7)
        #expect(snap["write_calendar"]?["name"]?.stringValue == "Home")
        #expect(PhoneCalendarRules.snapshot(clean, window: (0, 1), zone: zone, deviceName: "x")["write_calendar"] == nil)
    }

    @Test func deviceTasks() throws {
        let list = DeviceTask.parseList(try JSON.parse(#"{"items":[{"id":3,"kind":"dial","number":"+82 10-1234-5678","name":"Mina","expires_at":2000},{"kind":"x"}]}"#))
        #expect(list.count == 1)
        let t = list[0]
        #expect(DeviceTaskRules.dialNumber(t.s("number")) == "+821012345678")
        #expect(DeviceTaskRules.refuse(t, on: { $0 == .dialer }, calWrite: false, now: 1000) == nil)
        #expect(DeviceTaskRules.refuse(t, on: { _ in false }, calWrite: false, now: 1000) != nil)
        #expect(DeviceTaskRules.refuse(t, on: { _ in true }, calWrite: true, now: 3000) == "expired")
        let cal = DeviceTask(id: 4, kind: "cal_add")
        #expect(DeviceTaskRules.refuse(cal, on: { _ in true }, calWrite: false, now: 0) != nil)
        #expect(DeviceTaskRules.refuse(cal, on: { _ in true }, calWrite: true, now: 0) == nil)
        #expect(DeviceTaskRules.refuse(DeviceTask(id: 5, kind: "sms"), on: { _ in true }, calWrite: true, now: 0) != nil)
        #expect(DeviceTaskRules.dialWay(visible: true) == .open)
        #expect(DeviceTaskRules.dialWay(visible: false) == .notify)
        let zone = TimeZone(identifier: "Asia/Seoul")!
        let span = try #require(DeviceTaskRules.allDay(first: "2026-11-09", last: "2026-11-10", zone: zone))
        #expect(span.1 - span.0 == 2 * 86400)
    }

    @Test func betaRules() {
        #expect(OperatorBetaRules.products(["gmail", "calendar", "x"]) == "Gmail, Google Calendar")
        #expect(OperatorBetaRules.canApprove(BetaRequestRow(status: "requested")))
        #expect(!OperatorBetaRules.canApprove(BetaRequestRow(status: "approved")))
        #expect(OperatorBetaRules.canDecline(BetaRequestRow(status: "approved")))
        #expect(!OperatorBetaRules.canDecline(BetaRequestRow(status: "declined")))
        #expect(MyRoute.of(.operator) == .operator)
    }
}

@Suite struct OperatorStatusTests {
    @Test func operatorFrom() throws {
        #expect(OperatorRules.operatorFrom(nil, true) == true)
        #expect(OperatorRules.operatorFrom(nil, false) == false)
        #expect(OperatorRules.operatorFrom(403, nil) == false)
        #expect(OperatorRules.operatorFrom(404, nil, fallback: { OperatorRules.ownerFrom(nil) }) == true)
        #expect(OperatorRules.operatorFrom(404, nil, fallback: { OperatorRules.ownerFrom(403) }) == false)
        #expect(OperatorRules.operatorFrom(404, nil) == nil)
        #expect(OperatorRules.operatorFrom(500, nil) == nil)
        let st = try JSONDecoder().decode(OperatorStatus.self, from: Data(#"{"owner":true,"operator":true}"#.utf8))
        #expect(st.isOperator && st.owner)
    }
}
