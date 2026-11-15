import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// Android chat/BriefingCardTest.kt: the briefing card read from a stored message, where each item opens, the hours line.
@Suite struct BriefingCardTests {
    @Test func aStoredMessageCarriesItsCard() throws {
        let m = try JSON.decode(ChatMessage.self, from: try JSON.parse("""
            {"id": 7, "role": "assistant", "content": "오후 비 · 일정 2개", "created_at": 1,
             "briefing": {"summary": "오후 비", "place": {"name": "서울"}, "weather": {"headline": "비", "notes": [{"kind": "rain_start", "text": "15:00부터 비"}]},
              "events": [{"title": "회의", "start": 1762650000, "where": "3층", "open": {"kind": "event", "start": 1762650000}}],
              "missed": [{"kind": "mail", "title": "견적", "open": {"kind": "mail", "subject": "견적"}}, {"kind": "promise", "title": "약속"}],
              "hours": {"id": "h1", "day_type": "weekend", "wake_start": "10:00", "wake_end": "01:00"}}}
        """))
        let card = try #require(m.briefing)
        #expect(card.place?.name.isEmpty == false && card.weather?.unit == "C" && card.events.first?.place == "3층")
        #expect(BriefingRules.sections(card) == ["weather", "today", "missed", "hours"])
        #expect(BriefingRules.tap(card.events[0].open) == .event(1762650000))
        #expect(BriefingRules.tap(card.missed[0].open) == .mail("견적"))
        #expect(BriefingRules.tap(card.missed[1].open) == nil)
        #expect(BriefingRules.hoursShown(card.hours))
        if case .msg(let row) = ChatReducer.item(m) { #expect(row.briefing == card) } else { Issue.record("not a message row") }
    }

    @Test func bigEventsThisWeekMakeOneComingUpLine() throws {
        let card = try JSON.decode(BriefingCard.self, from: try JSON.parse("""
            {"day": "2026-11-09", "ahead": [{"title": "상하이 여행", "start": 1794700000, "kind": "trip", "first": "2026-11-12", "last": "2026-11-14"},
                                             {"title": "Flight KE893", "start": 1794900000, "kind": "flight", "first": "2026-11-15", "last": "2026-11-15"}]}
        """))
        #expect(BriefingRules.sections(card) == ["ahead"])
        #expect(BriefingRules.aheadLine(card) { _, m, d in "\(m)/\(d)" } == "상하이 여행 11/12 – 11/14 · Flight KE893 11/15")
        #expect(try JSON.decode(BriefingCard.self, from: try JSON.parse("{\"day\": \"2026-11-09\"}")).ahead.isEmpty)
    }

    @Test func itemsOpenTheirPlace() {
        #expect(BriefingRules.tap(BriefingOpen(kind: "followup", messageId: 12)) == .chatAt(12))
        #expect(BriefingRules.tap(BriefingOpen(kind: "followup")) == .waiting)
        #expect(BriefingRules.tap(BriefingOpen(kind: "job", taskId: 3)) == .task(3))
        #expect(BriefingRules.tap(BriefingOpen(kind: "goal")) == .goals)
        #expect(BriefingRules.tap(BriefingOpen(kind: "mail", subject: "  ")) == nil)
        #expect(!BriefingRules.hoursShown(BriefingHours(id: "", wakeStart: "10:00")))
    }

    @Test func aCardOnScreenTakesItsNewStateWhenTheBriefingChangesElsewhere() throws {
        let hours = BriefingHours(id: "h1", dayType: "weekend", wakeStart: "10:00", wakeEnd: "01:00")
        let card = BriefingCard(day: "2026-11-09", hours: hours)
        let items: [ChatItem] = [
            .msg(.init(key: "k1", mine: true, text: "hi", id: 40)),
            .msg(.init(key: "k2", mine: false, text: "Morning briefing", id: 41, briefing: card)),
            .msg(.init(key: "k3", mine: false, text: "later", id: 42)),
        ]
        var answered = card
        answered.hours = nil
        let after = ChatReducer.withBriefings(items, [41: answered])
        #expect(after[1].asMsg?.briefing?.hours == nil)
        #expect(after.map(\.key) == items.map(\.key))
        #expect(after[0] == items[0] && after[2] == items[2])
        #expect(ChatReducer.withBriefings(items, [:]) == items)   // the card's message is not in the page asked for
        let s = try JSON.decode(AgentStatus.self, from: try JSON.parse(#"{"busy": false, "briefing_rev": "|h1"}"#))
        #expect(s.briefingRev == "|h1")
        #expect(try JSON.decode(AgentStatus.self, from: try JSON.parse(#"{"busy": false}"#)).briefingRev == nil)   // an older server
    }

    private static let smart = """
        {"day": "2026-11-10", "summary": "s", "headline": "Rain from noon, and Kim is waiting.", "line": "Rain from noon, and Kim is waiting.",
         "weather": {"id": "w", "headline": "Rain", "notes": []},
         "events": [{"id": "e1", "title": "Design review", "start": 1762736400, "open": {"kind": "event", "start": 1762736400}}],
         "ahead": [{"id": "a1", "title": "Shanghai trip", "start": 1794700000, "kind": "trip", "first": "2026-11-12", "last": "2026-11-14"}],
         "missed": [{"id": "m1", "kind": "mail", "title": "Quote from Kim", "open": {"kind": "mail", "subject": "Quote"}},
                    {"id": "m2", "kind": "promise", "title": "Call mum"}],
         "connections": [{"text": "Rain and the review overlap.", "refs": ["w", "e1", "zz", "e1", " "]}, {"text": "  ", "refs": ["w"]},
                         {"text": "Kim's quote and a promise.", "refs": ["m1", "m2", "a1"]}],
         "suggestions": [{"text": "Shall I draft a reply to Kim?", "refs": ["m1"]}, {"text": "", "refs": []}, {"text": "Move the review indoors?"}],
         "future_field": {"x": 1}}
        """

    @Test func anOldCardHasNoneOfTheNewParts() throws {
        let card = try JSON.decode(BriefingCard.self, from: try JSON.parse(#"{"day": "2026-11-09", "line": "Old line", "events": [{"title": "회의", "start": 1}]}"#))
        #expect(card.headline == nil && card.connections.isEmpty && card.suggestions.isEmpty && card.events[0].id == "")
        #expect(BriefingRules.headline(card) == "Old line")
        #expect(BriefingRules.sections(card) == ["headline", "today"])
        #expect(BriefingRules.chips(BriefingLink(text: "x", refs: ["e1"]), card, weatherLabel: "Weather", clock: { _ in "9:00" }).isEmpty)
    }

    @Test func nullsAndUnknownFieldsAreTolerated() throws {
        let card = try JSON.decode(BriefingCard.self, from: try JSON.parse(#"""
            {"day": "d", "headline": null, "line": null, "connections": null, "suggestions": [{"text": null, "refs": null}, {"text": "Ask?", "refs": [1, null, "m1"]}],
             "weather": {"id": null, "headline": "Rain"}, "extra": [1]}
        """#))
        #expect(card.headline == nil && BriefingRules.headline(card) == nil && card.connections.isEmpty)
        #expect(BriefingRules.steps(card).map(\.text) == ["Ask?"] && BriefingRules.steps(card)[0].refs.contains("m1"))
        #expect(BriefingRules.sections(card) == ["weather", "steps"])
    }

    @Test func theHeadlineWinsOverTheOlderLineAndNeverBothShow() throws {
        let card = try JSON.decode(BriefingCard.self, from: try JSON.parse(Self.smart))
        #expect(BriefingRules.headline(card) == "Rain from noon, and Kim is waiting.")
        #expect(BriefingRules.headline(BriefingCard(line: "Line", headline: "  ")) == "Line")
        #expect(BriefingRules.headline(BriefingCard(line: "Line", headline: "Head")) == "Head")
    }

    @Test func sectionsComeInTheCardsOrder() throws {
        var card = try JSON.decode(BriefingCard.self, from: try JSON.parse(Self.smart))
        card.hours = BriefingHours(id: "h1", dayType: "weekday", wakeStart: "07:30")
        #expect(BriefingRules.sections(card) == ["headline", "notes", "weather", "today", "ahead", "missed", "steps", "hours"])
        #expect(BriefingRules.sections(BriefingCard(day: "d")).isEmpty)
    }

    @Test func notesAndStepsDropOnlyTheEmptyOnes() throws {
        let card = try JSON.decode(BriefingCard.self, from: try JSON.parse(Self.smart))
        #expect(BriefingRules.notes(card).map(\.text) == ["Rain and the review overlap.", "Kim's quote and a promise."])
        #expect(BriefingRules.steps(card).map(\.text) == ["Shall I draft a reply to Kim?", "Move the review indoors?"])
        #expect(BriefingRules.steps(card)[1].refs.isEmpty)
        let many = BriefingCard(connections: (1...6).map { BriefingLink(text: "n\($0)", refs: ["w"]) })
        #expect(BriefingRules.notes(many).count == 6)   // nothing is capped on the phone
    }

    @Test func chipsAreLabelledWithTheirItemAndDoWhatItsTapDoes() throws {
        let card = try JSON.decode(BriefingCard.self, from: try JSON.parse(Self.smart))
        let notes = BriefingRules.notes(card)
        let first = BriefingRules.chips(notes[0], card, weatherLabel: "Weather", clock: { _ in "10:00" })
        // "zz" points at nothing, "e1" twice shows once, a blank ref is skipped
        #expect(first == [BriefingChip(label: "Weather"), BriefingChip(label: "10:00 Design review", tap: .event(1762736400))])
        let second = BriefingRules.chips(notes[1], card, weatherLabel: "Weather", clock: { _ in "" })
        #expect(second == [BriefingChip(label: "Quote from Kim", tap: .mail("Quote")),
                           BriefingChip(label: "Call mum", tap: nil),    // no tap target: shown, not clickable
                           BriefingChip(label: "Shanghai trip", tap: nil)])
        #expect(first[0].tap == nil)   // the weather has no place of its own
    }

    @Test func aCardWithoutAWeatherHasNoWeatherChip() {
        let card = BriefingCard(events: [BriefingEvent(title: "Lunch", start: 5, id: "e1")])
        let chips = BriefingRules.chips(BriefingLink(text: "t", refs: ["w", "e1"]), card, weatherLabel: "Weather", clock: { _ in "12:00" })
    }
}
