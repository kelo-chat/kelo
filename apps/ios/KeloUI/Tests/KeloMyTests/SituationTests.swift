import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloMy

// Android my/SituationRulesTest.kt: "What Kelo looked at" — the codes, the grouping in a local zone, the decoding.
@Suite struct SituationTests {
    func list(_ json: String) throws -> SituationList { try JSON.decode(SituationList.self, from: try JSON.parse(json)) }
    func detail(_ json: String) throws -> SituationDetail { try JSON.decode(SituationDetail.self, from: try JSON.parse(json)) }

    @Test func everyPassCodeHasItsOutcomeAndAnUnknownOneIsGeneric() {
        #expect(SituationRules.outcome("no_change") == .noChange)
        #expect(SituationRules.outcome("nothing") == .nothing)
        #expect(SituationRules.outcome("proposed") == .proposed)
        #expect(SituationRules.outcome("selected") == .selected)
        #expect(SituationRules.outcome("delivered") == .delivered)
        #expect(SituationRules.outcome("running") == .running)
        #expect(SituationRules.outcome("failed") == .failed)
        #expect(SituationRules.outcome("skipped") == .failed)
        #expect(SituationRules.outcome("a_new_code") == .other)
        #expect(SituationRules.outcome("") == .other)
    }

    @Test func everySourceStateFallsIntoOneDetailGroup() {
        #expect(SituationRules.bucket("new") == .changed && SituationRules.bucket("changed") == .changed)
        #expect(SituationRules.bucket("same") == .same && SituationRules.bucket("not_due") == .same)
        #expect(SituationRules.bucket("failed") == .failed && SituationRules.bucket("timeout") == .failed && SituationRules.bucket("skipped") == .failed)
        #expect(SituationRules.bucket("off") == .off)
        #expect(SituationRules.bucket("something_else") == .other)
        let b = SituationRules.bucketed([
            SituationSource(id: "a", name: "A", kind: "connector", state: "new"),
            SituationSource(id: "b", name: "B", kind: "connector", state: "same"),
            SituationSource(id: "c", name: "C", kind: "connector", state: "timeout"),
        ])
        #expect(b[.changed]?.map(\.id) == ["a"])
        #expect(b[.same]?.map(\.id) == ["b"])
        #expect(b[.failed]?.map(\.id) == ["c"])
    }

    @Test func anItemsStatusChangesMapToTheTimelineKinds() {
        #expect(SituationRules.eventKind(status: "proposed", priority: "") == .proposed)
        #expect(SituationRules.eventKind(status: "selected", priority: "today") == .selectedToday)
        #expect(SituationRules.eventKind(status: "selected", priority: "regular") == .selectedRegular)
        #expect(SituationRules.eventKind(status: "selected", priority: "a_new_priority") == .selectedRegular)
        #expect(SituationRules.eventKind(status: "told", priority: "regular") == .told)
        #expect(SituationRules.eventKind(status: "dropped", priority: "") == .dropped)
        #expect(SituationRules.eventKind(status: "expired", priority: "") == .expired)
        #expect(SituationRules.eventKind(status: "waiting", priority: "") == .waiting)
        #expect(SituationRules.eventKind(status: "moved", priority: "") == .other)
    }

    @Test func theSectionsComeFirstAndTheOnDemandCoversAreSeparate() {
        let looked = [
            SituationLooked(id: "home_assistant", name: "Home Assistant", kind: "connector", hourly: true),
            SituationLooked(id: "memory", kind: "section", hourly: true),
            SituationLooked(id: "conversation", kind: "section", hourly: true),
            SituationLooked(id: "mail", kind: "section", hourly: true),
            SituationLooked(id: "notes", name: "Notes", kind: "connector", hourly: false),
            SituationLooked(id: "calendar", kind: "section", hourly: true),
        ]
        #expect(SituationRules.hourly(looked).map(\.id) == ["conversation", "mail", "calendar", "memory", "home_assistant"])
        #expect(SituationRules.onDemand(looked).map(\.id) == ["notes"])
    }

    @Test func passesGroupByTheLocalDayAndAPassWithoutATimeIsUnknown() {
        var seoul = Calendar(identifier: .gregorian)
        seoul.timeZone = TimeZone(identifier: "Asia/Seoul")!
        func at(_ y: Int, _ mo: Int, _ d: Int, _ h: Int, _ mi: Int) -> Double {
            seoul.date(from: DateComponents(year: y, month: mo, day: d, hour: h, minute: mi))!.timeIntervalSince1970
        }
        let passes = [
            SituationPass(id: 3, at: at(2026, 11, 18, 0, 10)),
            SituationPass(id: 2, at: at(2026, 11, 17, 23, 30)),
            SituationPass(id: 1, at: 0),
        ]
        let groups = SituationRules.groupPasses(passes, today: LocalDay(year: 2026, month: 11, day: 18), zone: seoul.timeZone)
        #expect(groups.map(\.label) == [.today, .yesterday, .unknown])
        #expect(groups[0].items.map(\.id) == [3])
        #expect(groups[1].items.map(\.id) == [2])
        // the same instant is on the day before in UTC: the grouping follows the owner's zone
        #expect(LocalDay(seconds: at(2026, 11, 18, 0, 10), zone: TimeZone(identifier: "UTC")!) == LocalDay(year: 2026, month: 11, day: 17))
    }

    @Test func eventsGroupByItemInTheOrderTheyHappened() {
        let groups = SituationRules.groupEvents([
            SituationEvent(itemId: 7, title: "Invoice due", status: "proposed", at: 1),
            SituationEvent(itemId: 8, title: "", status: "proposed", at: 2),
            SituationEvent(itemId: 7, title: "", status: "selected", priority: "today", at: 3),
            SituationEvent(itemId: 7, title: "Invoice due", status: "told", priority: "today", at: 4),
        ])
        #expect(groups.map(\.itemId) == [7, 8])
        #expect(groups[0].title == "Invoice due")
        #expect(groups[0].events.map(\.status) == ["proposed", "selected", "told"])
        #expect(groups[1].title == "")
    }

    @Test func readsTheListAndTheDetailAndIgnoresWhatItDoesNotKnow() throws {
        let l = try list(#"""
        {"research": true, "every_minutes": 60, "keep_days": 90, "future": 1,
         "looked_at": [{"id": "mail", "name": "", "kind": "section", "hourly": true}],
         "passes": [{"id": 12, "at": 1762650000.5, "status": "proposed", "proposed": 1,
           "counts": {"proposed": 1, "selected": 0, "told": 0, "dropped": 0},
           "sources": [{"id": "mail", "name": "", "kind": "section", "state": "changed", "entries": 3}]}],
         "open": [], "next_before": 12}
        """#)
        #expect(l.research && l.keepDays == 90 && l.nextBefore == 12)
        #expect(l.passes[0].sources[0].state == "changed")
        #expect(l.passes[0].counts.proposed == 1)
        let d = try detail(#"""
        {"pass": {"id": 12, "at": 1762650000.5, "status": "proposed", "counts": {"proposed": 1}}, "sources": [],
         "events": [{"item_id": 7, "title": "Invoice due", "status": "told", "why": "told:today", "priority": "today", "at": 1762650100.0}]}
        """#)
        #expect(d.events[0].itemId == 7 && d.events[0].status == "told")
        #expect(d.pass.counts.proposed == 1)
        #expect(try list("{}").passes.isEmpty)
    }

    @Test func theLooksAreReadAndAnOlderServerHasNone() throws {
        let l = try list(#"""
        {"research": true, "keep_days": 90, "looked_at": [], "passes": [],
         "looks": [{"when": "hourly", "times": [], "covers": [{"id": "mail", "name": "", "kind": "section"}]},
                   {"when": "send", "times": ["12:00", "16:00", "x"], "covers": [{"id": "conversation", "name": "", "kind": "section"}]},
                   {"when": "briefing", "times": ["08:00"], "covers": [{"id": "corrections", "name": "", "kind": "section"}]},
                   {"when": "later_kind", "times": [], "covers": [{"id": "a", "name": "A", "kind": "connector"}]},
                   {"when": "needed", "times": [], "covers": []}]}
        """#)
        let shown = SituationRules.shownLooks(l.looks ?? [])
        #expect(shown.map { SituationRules.lookKind($0.whenCode) } == [.hourly, .send, .briefing])
        #expect(SituationRules.lookTimes(shown[1]) == ["12:00", "16:00"])
        #expect(shown[2].covers.map(\.id) == ["corrections"])
        #expect(try list(#"{"looked_at": []}"#).looks == nil)
    }

    @MainActor @Test func theNewSectionCodesHaveTheirNames() {
        withLanguage("en") {
            #expect(sectionName("corrections") == L(R.string.situation_section_corrections))
            #expect(sectionName("held") == L(R.string.deliveries_held_title))
            #expect(sectionName("weather") == L(R.string.briefing_weather))
            #expect(sectionName("missed") == L(R.string.briefing_missed))
            #expect(sectionName("a_new_code") == L(R.string.situation_section_other))
        }
    }

    @Test func theProactiveSettingsReadTheResearchFlag() throws {
        let on = try JSON.decode(ProactiveSettings.self, from: try JSON.parse(#"{"enabled": true, "research": true}"#))
        #expect(on.research)
        let older = try JSON.decode(ProactiveSettings.self, from: try JSON.parse(#"{"enabled": true}"#))
        #expect(!older.research)
    }
}
