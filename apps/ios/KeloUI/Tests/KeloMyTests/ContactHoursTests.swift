import Testing
import Foundation
import KeloCore
@testable import KeloMy

// Android my/ContactHoursRulesTest.kt
@Suite struct ContactHoursTests {
    @Test func readsTheSuggestionAndWhereTheHoursCameFrom() throws {
        let body = """
        {"enabled": true, "wake_start": "07:00", "wake_end": "22:00", "auto_hours": false, "hours_source": "default",
         "guard": {"owner_spoke": true}, "hours_suggestion": {"id": "a1b2c3", "wake_start": "10:00", "wake_end": "01:00",
         "current_start": "07:00", "current_end": "22:00", "active_days": 12, "created_at": 1762650000.5}}
        """
        let h = try JSONDecoder().decode(ContactHours.self, from: Data(body.utf8))
        #expect(h.suggestion?.id == "a1b2c3" && h.suggestion?.activeDays == 12)
        #expect(ContactHoursRules.shown(h.suggestion))
        #expect(!ContactHoursRules.learned(h.hoursSource))
        let old = try JSONDecoder().decode(ContactHours.self, from: Data(#"{"enabled": false, "wake_start": "08:00", "wake_end": "21:00", "hours_suggestion": null}"#.utf8))
        #expect(old.suggestion == nil)
        #expect(old.hoursSource == "default")
        #expect(!old.autoHours)
        let settings = try JSONDecoder().decode(ProactiveSettings.self, from: Data(#"{"enabled": true, "auto_hours": true, "hours_source": "learned"}"#.utf8))
        #expect(settings.autoHours && ContactHoursRules.learned(settings.hoursSource))
    }

    @Test func windowsPastMidnight() {
        #expect(ContactHoursRules.pastMidnight("10:00", "01:00"))
        #expect(ContactHoursRules.pastMidnight("23:30", "00:00"))
        #expect(!ContactHoursRules.pastMidnight("07:00", "22:00"))
        #expect(!ContactHoursRules.pastMidnight("07:00", "07:00"))
        #expect(!ContactHoursRules.pastMidnight("bad", "01:00"))
        #expect(ContactHoursRules.minutes("23:30") == 23 * 60 + 30)
        #expect(ContactHoursRules.minutes(" 7:00 ") == 7 * 60)
        #expect(ContactHoursRules.minutes("24:00") == nil && ContactHoursRules.minutes("7") == nil)
    }

    @Test func onlyAUsableDifferentSuggestionIsShown() {
        let s = HoursSuggestion(id: "x", wakeStart: "10:00", wakeEnd: "01:00", currentStart: "07:00", currentEnd: "22:00", activeDays: 9)
        #expect(ContactHoursRules.shown(s))
        #expect(!ContactHoursRules.shown(nil))
        var noId = s; noId.id = ""
        #expect(!ContactHoursRules.shown(noId))
        var bad = s; bad.wakeEnd = "25:00"
        #expect(!ContactHoursRules.shown(bad))
        var same = s; same.currentStart = "10:00"; same.currentEnd = "01:00"
        #expect(!ContactHoursRules.shown(same))
        var oneEnd = s; oneEnd.currentStart = "10:00"
        #expect(ContactHoursRules.shown(oneEnd))
    }

    @Test func acceptAndKeepCallsEncodeTheId() {
        #expect(ContactHoursRules.acceptPath("a1b2c3") == "/api/proactive/hours/a1b2c3/accept")
        #expect(ContactHoursRules.keepPath("a/b") == "/api/proactive/hours/a%2Fb/keep")
    }
}

/// Android ContactHoursRules for weekday and weekend rows (my/NotifyPage.kt HoursBlock, RecordsPage).
@Suite struct WeekendHoursTests {
    func hours(_ json: String) throws -> ContactHours { try JSON.decode(ContactHours.self, from: try JSON.parse(json)) }

    @Test func weekendRowsFollowTheWeekdaysUntilTheyHaveTheirOwn() throws {
        let h = try hours(#"{"wake_start":"07:00","wake_end":"23:00","hours_source":"manual","holiday_country":"KR"}"#)
        #expect(h.holidayCountry == "KR")
        #expect(ContactHoursRules.window(h, ContactHoursRules.weekend) == ("07:00", "23:00", "default"))
        let own = try hours(#"{"wake_start":"07:00","wake_end":"23:00","weekend_start":"10:00","weekend_end":"01:00","weekend_source":"learned"}"#)
        let w = ContactHoursRules.window(own, ContactHoursRules.weekend)
        #expect(w.start == "10:00" && w.end == "01:00" && w.source == "learned")
        #expect(ContactHoursRules.field(ContactHoursRules.weekend, start: true) == "weekend_start")
        #expect(ContactHoursRules.applyPath("weekend") == "/api/proactive/hours/learned/weekend/apply")
    }

    @Test func aLearnedWindowIsOfferedOnlyWhenItDiffers() throws {
        let h = try hours(#"{"wake_start":"07:00","wake_end":"23:00","learned":{"weekday":{"wake_start":"07:00","wake_end":"23:00"},"weekend":{"wake_start":"10:00","wake_end":"01:00","active_days":5}}}"#)
        #expect(ContactHoursRules.learnedFor(h, ContactHoursRules.weekday) == nil)
        #expect(ContactHoursRules.learnedFor(h, ContactHoursRules.weekend)?.activeDays == 5)
    }

    @Test func theWeekdaySuggestionComesFirstThenTheWeekendOne() throws {
        let h = try hours(#"{"wake_start":"07:00","wake_end":"23:00","weekend_suggestion":{"id":"s2","wake_start":"10:00","wake_end":"01:00","current_start":"07:00","current_end":"23:00"}}"#)
        let s = ContactHoursRules.open(h)
        #expect(s?.id == "s2" && s?.dayType == ContactHoursRules.weekend)
        let both = try hours(#"{"hours_suggestion":{"id":"s1","wake_start":"09:00","wake_end":"23:00","current_start":"07:00","current_end":"23:00"},"weekend_suggestion":{"id":"s2","wake_start":"10:00","wake_end":"01:00"}}"#)
        #expect(ContactHoursRules.open(both)?.id == "s1")
    }

    @Test func timeButtonsShareARowOnlyWhenBothFitInHalf() {
        #expect(ContactHoursRules.timesSideBySide(110, 100, available: 256, gap: 10))
        #expect(ContactHoursRules.timesSideBySide(123, 123, available: 256, gap: 10))
        #expect(!ContactHoursRules.timesSideBySide(80, 150, available: 256, gap: 10))
        #expect(!ContactHoursRules.timesSideBySide(124, 100, available: 256, gap: 10))
        #expect(!ContactHoursRules.timesSideBySide(0, 0, available: 0, gap: 10))
    }
}
