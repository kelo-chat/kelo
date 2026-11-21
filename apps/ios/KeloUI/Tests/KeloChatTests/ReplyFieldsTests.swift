import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// The typed answer fields and the answers to write (Android ReplyFieldsTest / QuickReplyRulesTest, the same cases).
@Suite struct ReplyFieldsTests {
    private func q(_ kind: String, min: Double? = nil, max: Double? = nil, unit: String = "", def: String = "") -> ReplyQuestion {
        ReplyQuestion(question: "Q", kind: kind, defaultValue: def, min: min, max: max, unit: unit)
    }

    @Test func kindsAreReadAndAnUnknownOneFallsBack() {
        #expect(ReplyFields.kind(ReplyQuestion(question: "Q", options: ["a", "b"])) == ReplyFields.choice)
        #expect(ReplyFields.kind(q(" Date ")) == ReplyFields.date)
        #expect(ReplyFields.kind(ReplyQuestion(question: "Q", options: ["a", "b"], kind: "colour")) == ReplyFields.choice)
        #expect(ReplyFields.kind(q("colour")) == ReplyFields.text)
    }

    @Test func answersAreCheckedLikeTheServerDoes() {
        #expect(ReplyFields.check(q("date"), "2026-11-10") == "2026-11-10")
        #expect(ReplyFields.check(q("date"), "2026-02-30") == nil)
        #expect(ReplyFields.check(q("time"), "9:05") == "09:05")
        #expect(ReplyFields.check(q("time"), "24:00") == nil)
        #expect(ReplyFields.check(q("datetime"), "2026-01-02T7:00") == "2026-01-02 07:00")
        #expect(ReplyFields.check(q("weekdays"), "wed, mon mon") == "mon,wed")
        #expect(ReplyFields.check(q("weekdays"), "someday") == nil)
        #expect(ReplyFields.check(q("weekdays_time"), "fri,mon 7:00") == "mon,fri 07:00")
        #expect(ReplyFields.check(q("weekdays_time"), "mon") == nil)
        #expect(ReplyFields.check(q("number"), "1,000") == "1000")
        #expect(ReplyFields.check(q("number"), "2.5") == "2.5")
        #expect(ReplyFields.check(q("number", min: 1), "0") == nil)
        #expect(ReplyFields.check(q("number"), "nan") == nil)
        #expect(ReplyFields.check(q("text"), "   ") == nil)
        #expect(ReplyFields.start(q("time", def: "21:30")) == "21:30")
        #expect(ReplyFields.range(q("number", min: 1, max: 5))! == ("1", "5"))
        #expect(ReplyFields.range(q("number", min: 1)) == nil)
    }

    private let ko = ReplyFields.Words(daily: "매일", weekly: "매주 %1$s", dailyAt: "매일 %1$s", weeklyAt: "매주 %1$s %2$s", dateAt: "%1$s %2$s", separator: ", ")
    private func plain(_ s: String) -> String { s.replacingOccurrences(of: "\u{202F}", with: " ").replacingOccurrences(of: "\u{00A0}", with: " ") }

    @Test func answersShowInTheUsersWords() {
        let koKR = Locale(identifier: "ko_KR"), en = Locale(identifier: "en_US")
        #expect(plain(ReplyFields.display(q("weekdays_time"), "mon,wed 12:00", locale: koKR, words: ko, firstWeekday: 2)) == "매주 월, 수 오후 12:00")
        #expect(ReplyFields.display(q("weekdays"), "mon,sun", locale: koKR, words: ko, firstWeekday: 1) == "매주 일, 월")
        #expect(plain(ReplyFields.display(q("weekdays_time"), "mon,tue,wed,thu,fri,sat,sun 07:00", locale: koKR, words: ko, firstWeekday: 2)) == "매일 오전 7:00")
        #expect(plain(ReplyFields.display(q("weekdays_time"), "mon,wed 12:00", locale: en, words: .init(), firstWeekday: 1)) == "Every Mon, Wed at 12:00 PM")
        #expect(ReplyFields.display(q("weekdays"), "sat", locale: en, words: .init(), firstWeekday: 1) == "Weekly on Sat")
        #expect(ReplyFields.display(q("date"), "2026-11-10", locale: en, words: .init(), firstWeekday: 1) == "Tue, Nov 10, 2026")
        #expect(ReplyFields.display(q("number", unit: "pcs"), "3", locale: en, words: .init(), firstWeekday: 1) == "3 pcs")
        #expect(ReplyFields.display(q("number", unit: "개"), "3", locale: koKR, words: ko, firstWeekday: 2) == "3개")
        #expect(ReplyFields.display(q("number", unit: "%"), "50", locale: en, words: .init(), firstWeekday: 1) == "50%")
    }

    @Test func daysToggleFreelyAndFollowTheWeek() {
        #expect(ReplyFields.toggle([], 0) == [0])
        #expect(ReplyFields.toggle([0], 0) == [])
        #expect(ReplyFields.weekOrder(firstWeekday: 1).first == 6)   // Sunday first
        #expect(ReplyFields.weekOrder(firstWeekday: 2) == [0, 1, 2, 3, 4, 5, 6])
        #expect(ReplyFields.daysPlain([6, 1]) == "tue,sun")
    }

    @Test func aChoiceShowsOnlyTheServersOptions() {
        let when = ReplyQuestion(question: "When", options: ["Morning", "Evening"])
        #expect(QuickReplyRules.choices(when) == ["Morning", "Evening"])   // no answer of the app's own
        #expect(QuickReplyRules.choices(ReplyQuestion(options: ["Continue"])) == ["Continue"])
        #expect(QuickReplyRules.choices(ReplyQuestion(question: "Day", options: ["a", "b"], kind: "date")).isEmpty)
        #expect(!QuickReplyRules.isTyped(when, "Morning"))
    }

    @Test func aTypedOptionOpensABoxAndNothingElseDoes() {
        let when = ReplyQuestion(question: "When", options: ["Morning", "Type it"], typed: ["Type it"])
        #expect(QuickReplyRules.choices(when) == ["Morning", "Type it"])
        let one = ReplySet(questions: [when])
        #expect(!QuickReplyRules.sendsOnTap(one, when, "Type it"))
        #expect(QuickReplyRules.showsSend(one, picks: [0: "Type it"]))
        #expect(!QuickReplyRules.canSend(one, picks: [0: "Type it"], typed: [:]))
        #expect(QuickReplyRules.compose(one, picks: [0: "Type it"], typed: [0: " Late night "]) == "Late night")
    }

    @Test func fieldsAreSentInWordsWithTheirPlainValuesAlongside() {
        let set = ReplySet(questions: [ReplyQuestion(question: "Which", options: ["x", "y"]), ReplyQuestion(question: "Days", kind: "weekdays_time"),
                                       ReplyQuestion(question: "How many", kind: "number", min: 1, max: 5)])
        #expect(!QuickReplyRules.sendsAtOnce(ReplySet(questions: [set.questions[1]])))
        #expect(!QuickReplyRules.canSend(set, picks: [:], typed: [:], values: [2: "9"]))
        #expect(QuickReplyRules.canSend(set, picks: [:], typed: [:], values: [2: "3"]))
        let values = [1: "wed,mon 12:00", 2: "3"]
        #expect(QuickReplyRules.answers(set, picks: [0: "x"], values: values) == [ReplyAnswer(0, "x"), ReplyAnswer(1, "mon,wed 12:00"), ReplyAnswer(2, "3")])
        #expect(QuickReplyRules.compose(set, picks: [0: "x"], values: values, show: { "<\($1)>" }) == "Which: x\nDays: <mon,wed 12:00>\nHow many: <3>")
    }

    @Test func readsTheNewFields() throws {
        let data = #"{"questions":[{"question":"Q","options":[],"kind":"number","default":"3","min":1,"max":5,"unit":"pcs"}]}"#.data(using: .utf8)!
        let s = try JSONDecoder().decode(ReplySet.self, from: data)
        #expect(s.questions[0].kind == "number" && s.questions[0].defaultValue == "3" && s.questions[0].min == 1 && s.questions[0].max == 5 && s.questions[0].unit == "pcs")
    }
}
