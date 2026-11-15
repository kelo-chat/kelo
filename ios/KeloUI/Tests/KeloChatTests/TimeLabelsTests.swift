import Testing
import Foundation
import KeloCore
@testable import KeloChat

private let seoul = TimeZone(identifier: "Asia/Seoul")!
private let newYork = TimeZone(identifier: "America/New_York")!

/// Unix ms of a local date-time "yyyy-MM-dd'T'HH:mm" in `zone`.
private func ms(_ text: String, _ zone: TimeZone = seoul) -> Int64 {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = zone
    f.dateFormat = text.count > 16 ? "yyyy-MM-dd'T'HH:mm:ss" : "yyyy-MM-dd'T'HH:mm"
    return Int64(f.date(from: text)!.timeIntervalSince1970 * 1000)
}

private func words(_ at: String, now: String, _ lang: String, _ today: String, _ yesterday: String) -> String {
    TimeLabels.label(ms(at), now: ms(now), zone: seoul, locale: Locale(identifier: lang), today: today, yesterday: yesterday)
        .replacingOccurrences(of: "\u{202F}", with: " ")
}

@Suite struct TimeLabelsTests {
    @Test func koreanWords() {
        let now = "2026-11-08T23:00"
        #expect(words("2026-11-08T22:00", now: now, "ko", "오늘", "어제") == "오늘 오후 10:00")
        #expect(words("2026-11-07T09:31", now: now, "ko", "오늘", "어제") == "어제 오전 9:31")
        #expect(words("2026-11-01T21:11", now: now, "ko", "오늘", "어제") == "11월 1일 오후 9:11")
        #expect(words("2025-10-19T09:22", now: now, "ko", "오늘", "어제") == "2025년 10월 19일 오전 9:22")
        #expect(words("2024-10-19T09:22", now: now, "ko", "오늘", "어제") == "2024년 10월 19일 오전 9:22")
    }

    @Test func englishWords() {
        let now = "2026-11-08T23:00"
        #expect(words("2026-11-08T22:00", now: now, "en", "Today", "Yesterday") == "Today 10:00 PM")
        #expect(words("2026-11-07T09:31", now: now, "en", "Today", "Yesterday") == "Yesterday 9:31 AM")
        #expect(words("2026-11-01T21:11", now: now, "en", "Today", "Yesterday") == "Nov 1, 9:11 PM")
        #expect(words("2024-10-19T09:22", now: now, "en", "Today", "Yesterday") == "Oct 19, 2024, 9:22 AM")
    }

    @Test func midnightIsTheDayBoundary() {
        #expect(TimeLabels.day(ms("2026-11-07T23:59"), now: ms("2026-11-08T00:00"), zone: seoul) == .yesterday)
        #expect(TimeLabels.day(ms("2026-11-08T00:00"), now: ms("2026-11-08T00:01"), zone: seoul) == .today)
        #expect(TimeLabels.day(ms("2026-11-06T23:59"), now: ms("2026-11-08T00:00"), zone: seoul) == .thisYear)
        #expect(TimeLabels.startsStretch(prev: ms("2026-11-07T23:59"), at: ms("2026-11-08T00:00"), zone: seoul))
    }

    @Test func thirtyMinutesStartsAStretch() {
        #expect(!TimeLabels.startsStretch(prev: ms("2026-11-08T10:00"), at: ms("2026-11-08T10:29:59"), zone: seoul))
        #expect(TimeLabels.startsStretch(prev: ms("2026-11-08T10:00"), at: ms("2026-11-08T10:30"), zone: seoul))
        #expect(TimeLabels.startsStretch(prev: nil, at: ms("2026-11-08T10:00"), zone: seoul))
    }

    @Test func newYear() {
        let now = ms("2027-01-01T08:00")
        #expect(TimeLabels.day(ms("2026-12-31T22:00"), now: now, zone: seoul) == .yesterday)
        #expect(TimeLabels.day(ms("2026-12-30T22:00"), now: now, zone: seoul) == .earlier)
        #expect(words("2026-12-30T22:00", now: "2027-01-01T08:00", "ko", "오늘", "어제") == "2026년 12월 30일 오후 10:00")
    }

    @Test func daylightSaving() {
        // 2026-03-08 in New York: 01:59 EST is followed by 03:00 EDT; 01:50 -> 03:10 is 20 real minutes
        #expect(!TimeLabels.startsStretch(prev: ms("2026-03-08T01:50", newYork), at: ms("2026-03-08T03:10", newYork), zone: newYork))
        #expect(TimeLabels.day(ms("2026-03-08T00:30", newYork), now: ms("2026-03-09T00:10", newYork), zone: newYork) == .yesterday)
    }

    @Test func onlyMessagesThatStartAStretch() {
        let items: [ChatItem] = [
            .msg(.init(key: "a", mine: true, text: "hi", at: ms("2026-11-08T10:00"))),
            .msg(.init(key: "b", mine: false, text: "hello", at: ms("2026-11-08T10:01"))),
            .notice(key: "n", .retry),
            .msg(.init(key: "c", mine: true, text: "again", at: ms("2026-11-08T10:29"))),
            .msg(.init(key: "d", mine: false, text: "later", at: ms("2026-11-08T11:00"))),
            .msg(.init(key: "e", mine: true, text: "no time")),
            .msg(.init(key: "f", mine: false, text: "next day", at: ms("2026-11-09T00:01"))),
        ]
        #expect(TimeLabels.labelled(items, zone: seoul) == ["a", "d", "f"])
    }

    @Test func liveMessagesAreDated() {
        let items = ChatReducer.apply([], .text("hi"), newKey: { "k" }, now: 1234)
        #expect(items.first?.asMsg?.at == 1234)
        let more = ChatReducer.apply(items, .text("!"), newKey: { "k2" }, now: 9999)
        #expect(more.first?.asMsg?.at == 1234)
    }

    @Test func plainJoinAndEveryLanguage() {
        #expect(TimeLabels.Patterns.plainJoin("MMM d 'at' h:mm a", time: "h:mm a") == "MMM d, h:mm a")
        #expect(TimeLabels.Patterns.plainJoin("d 'de' MMM, H:mm", time: "H:mm") == "d 'de' MMM, H:mm")
        for lang in ["en", "ko", "ja", "zh-Hans", "zh-Hant", "ar", "de", "es", "fr", "hi", "id", "it", "nl", "pl", "pt-BR", "ru", "th", "tr", "uk", "vi"] {
            let thisYear = TimeLabels.label(ms("2026-11-01T21:11"), now: ms("2026-11-08T23:00"), zone: seoul, locale: Locale(identifier: lang), today: "T", yesterday: "Y")
            #expect(!thisYear.contains("2026"), "\(lang): \(thisYear)")
            #expect(!thisYear.isEmpty)
        }
    }
}
