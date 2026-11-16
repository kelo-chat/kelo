import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

private let sampleJson = """
{
  "items": [
    {
      "id": 1,
      "ts": 1794744000.0,
      "run_id": "r1",
      "task_id": 42,
      "turn_id": "t1",
      "source": "chat",
      "source_name": "Chat",
      "line": "Sent a mail to alice@example.com",
      "tool": "send_mail",
      "kind": "send",
      "connector": "Fastmail",
      "targets": ["mail:alice@example.com"],
      "state": "done",
      "outside_id": "m1",
      "detail": "",
      "checked_at": null
    },
    {
      "id": 2,
      "ts": 1794657600.0,
      "run_id": "r2",
      "task_id": null,
      "turn_id": "t2",
      "source": "schedule:1",
      "source_name": "Morning sync",
      "line": "Changed the calendar",
      "tool": "calendar_patch",
      "kind": "change",
      "connector": "Google Calendar",
      "targets": ["cal:event1"],
      "state": "unknown",
      "outside_id": "e1",
      "detail": "",
      "checked_at": null
    },
    {
      "id": 3,
      "ts": 1794571200.0,
      "run_id": "r3",
      "task_id": 99,
      "turn_id": "t3",
      "source": "chat",
      "source_name": "Chat",
      "line": "Deleted in Slack",
      "tool": "slack_delete",
      "kind": "delete",
      "connector": "Slack",
      "targets": ["slack:msg1"],
      "state": "failed",
      "outside_id": "s1",
      "detail": "token expired",
      "checked_at": null
    }
  ]
}
"""

@MainActor @Suite struct ActionsModelTests {
    private let utc = TimeZone(identifier: "UTC")!
    private let testToday = LocalDay(year: 2026, month: 11, day: 15)

    @Test func loadItemsWithDefaultSevenDaysPeriod() async {
        let t = RoutedTransport()
        t.on("GET /api/actions") { call, _ in
            #expect(call.query == "days=7")
            return (200, sampleJson)
        }

        let m = ActionsModel(api: t.api(), today: { self.testToday }, zone: utc)
        #expect(m.period == .sevenDays)
        await m.load()

        #expect(m.items.count == 3)
        #expect(m.failed == false)

        let first = m.items[0]
        #expect(first.id == 1)
        #expect(first.line == "Sent a mail to alice@example.com")
        #expect(first.sourceName == "Chat")
        #expect(first.kind == "send")
        #expect(first.state == "done")
        #expect(first.taskId == 42)

        let second = m.items[1]
        #expect(second.id == 2)
        #expect(second.line == "Changed the calendar")
        #expect(second.sourceName == "Morning sync")
        #expect(second.kind == "change")
        #expect(second.state == "unknown")
        #expect(second.taskId == nil)

        let third = m.items[2]
        #expect(third.id == 3)
        #expect(third.state == "failed")
    }

    @Test func changePeriodCallsApiWithNewDays() async {
        let t = RoutedTransport()
        var requestedDays: [String] = []
        t.on("GET /api/actions") { call, _ in
            requestedDays.append(call.query)
            return (200, sampleJson)
        }

        let m = ActionsModel(api: t.api(), today: { self.testToday }, zone: utc)
        await m.load()
        await m.setPeriod(.today)
        await m.setPeriod(.thirtyDays)

        #expect(requestedDays == ["days=7", "days=1", "days=30"])
        #expect(m.period == .thirtyDays)
    }

    @Test func groupingSplitsItemsIntoTodayYesterdayAndDate() async {
        let t = RoutedTransport()
        t.on("GET /api/actions", sampleJson)

        let m = ActionsModel(api: t.api(), today: { self.testToday }, zone: utc)
        await m.load()

        #expect(m.groups.count == 3)
        #expect(m.groups[0].label == .today)
        #expect(m.groups[0].items.map(\.id) == [1])

        #expect(m.groups[1].label == .yesterday)
        #expect(m.groups[1].items.map(\.id) == [2])

        #expect(m.groups[2].label == .date(LocalDay(year: 2026, month: 11, day: 13)))
        #expect(m.groups[2].items.map(\.id) == [3])
    }

    @Test func emptyListLoadsSuccessfully() async {
        let t = RoutedTransport()
        t.on("GET /api/actions", #"{"items":[]}"#)

        let m = ActionsModel(api: t.api(), today: { self.testToday }, zone: utc)
        await m.load()

        #expect(m.items.isEmpty)
        #expect(m.groups.isEmpty)
        #expect(m.failed == false)
    }

    @Test func failureSetsFailedFlag() async {
        let t = RoutedTransport()
        t.on("GET /api/actions", 500, #"{"detail":"internal error"}"#)

        let m = ActionsModel(api: t.api(), today: { self.testToday }, zone: utc)
        await m.load()

        #expect(m.failed == true)
        #expect(m.items.isEmpty)
    }
}
