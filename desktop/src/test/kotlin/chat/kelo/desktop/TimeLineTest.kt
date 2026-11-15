package chat.kelo.desktop

import chat.kelo.app.core.Languages
import chat.kelo.desktop.chat.DesktopTimePatterns
import chat.kelo.desktop.chat.TimeLabeler
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** The desktop's time line words, from the JDK's locale patterns (the rules themselves: chat/TimeLabelsTest, shared). */
class TimeLineTest {
    private val seoul = ZoneId.of("Asia/Seoul")
    private fun ms(t: String) = LocalDateTime.parse(t).atZone(seoul).toInstant().toEpochMilli()
    private val now = ms("2026-11-08T23:00")

    private fun words(locale: Locale, today: String, yesterday: String, at: String) =
        TimeLabeler(locale, seoul, today, yesterday).label(ms(at), now).replace(' ', ' ')

    @Test fun korean() {
        val ko = Locale.forLanguageTag("ko")
        assertEquals("오늘 오후 10:00", words(ko, "오늘", "어제", "2026-11-08T22:00"))
        assertEquals("어제 오전 9:31", words(ko, "오늘", "어제", "2026-11-07T09:31"))
        assertEquals("11월 1일 오후 9:11", words(ko, "오늘", "어제", "2026-11-01T21:11"))
        assertEquals("2024년 10월 19일 오전 9:22", words(ko, "오늘", "어제", "2024-10-19T09:22"))
        assertEquals("2025년 10월 19일 오전 9:22", words(ko, "오늘", "어제", "2025-10-19T09:22"))
    }

    @Test fun english() {
        val en = Locale.forLanguageTag("en")
        assertEquals("Today 10:00 PM", words(en, "Today", "Yesterday", "2026-11-08T22:00"))
        assertEquals("Yesterday 9:31 AM", words(en, "Today", "Yesterday", "2026-11-07T09:31"))
        assertEquals("Nov 1, 9:11 PM", words(en, "Today", "Yesterday", "2026-11-01T21:11"))
        assertEquals("Oct 19, 2024, 9:22 AM", words(en, "Today", "Yesterday", "2024-10-19T09:22"))
    }

    @Test fun yearTakenOut() {
        assertEquals("MMM d, h:mm a", DesktopTimePatterns.withoutYear("MMM d, y, h:mm a"))
        assertEquals("M월 d일 a h:mm", DesktopTimePatterns.withoutYear("y년 M월 d일 a h:mm"))
        assertEquals("M月d日 H:mm", DesktopTimePatterns.withoutYear("y年M月d日 H:mm"))
        assertEquals("d MMM, HH:mm", DesktopTimePatterns.withoutYear("d MMM y 'г'., HH:mm"))
        assertEquals("d 'de' MMM HH:mm", DesktopTimePatterns.withoutYear("d 'de' MMM 'de' y HH:mm"))
        assertEquals("HH:mm, d MMM", DesktopTimePatterns.withoutYear("HH:mm, d MMM, y"))
    }

    @Test fun everyLanguageFormatsWithoutAYearThisYear() {
        Languages.ALL.forEach { lang ->
            val p = DesktopTimePatterns.of(Locale.forLanguageTag(lang.code))
            val thisYear = DateTimeFormatter.ofPattern(p.monthDayTime, Locale.forLanguageTag(lang.code)).withZone(seoul)
                .format(java.time.Instant.ofEpochMilli(ms("2026-11-01T21:11")))
            assertFalse("${lang.code}: $thisYear", thisYear.contains("2026"))
            val earlier = DateTimeFormatter.ofPattern(p.fullDateTime, Locale.forLanguageTag(lang.code)).withZone(seoul)
                .format(java.time.Instant.ofEpochMilli(ms("2024-10-19T09:22")))
            assert(earlier.contains("2024") || lang.code == "th") { "${lang.code}: $earlier" }
        }
    }
}
