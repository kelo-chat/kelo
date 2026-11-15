package chat.kelo.app.agent

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneOffset

class DayGroupsTest {
    private val today = LocalDate.of(2026, 11, 7)
    private fun task(id: Long, day: String, started: Double = 0.0) = AgentTask(id = id, day = day, startedAt = started)

    @Test fun groupsTodayYesterdayAndDatesInOrder() {
        val items = listOf(task(5, "2026-11-07"), task(4, "2026-11-07"), task(3, "2026-11-06"), task(2, "2026-11-01"), task(1, "2025-12-31"))
        val g = DayGroups.group(items, today) { DayGroups.dayOf(it) }
        assertEquals(listOf(DayGroups.Label.Today, DayGroups.Label.Yesterday, DayGroups.Label.Date(LocalDate.of(2026, 11, 1)),
            DayGroups.Label.Date(LocalDate.of(2025, 12, 31))), g.map { it.label })
        assertEquals(listOf(5L, 4L), g[0].items.map { it.id })
        assertEquals(listOf(3L), g[1].items.map { it.id })
    }

    @Test fun keepsItemOrderAndJoinsADayThatComesBack() {
        val items = listOf(task(3, "2026-11-07"), task(2, "2026-11-06"), task(1, "2026-11-07"))
        val g = DayGroups.group(items, today) { DayGroups.dayOf(it) }
        assertEquals(2, g.size)
        assertEquals(listOf(3L, 1L), g[0].items.map { it.id })
    }

    @Test fun missingDayFallsBackToStartTimeThenUnknown() {
        val started = LocalDate.of(2026, 11, 6).atTime(12, 0).toEpochSecond(ZoneOffset.UTC).toDouble()
        assertEquals(LocalDate.of(2026, 11, 6), DayGroups.dayOf(task(1, "", started), ZoneOffset.UTC))
        assertNull(DayGroups.dayOf(task(2, "garbage")))
        assertEquals(DayGroups.Label.Unknown, DayGroups.label(null, today))
        assertEquals(LocalDate.of(2026, 11, 5), DayGroups.parseDay("2026-11-05T10:00:00"))
    }
}

class RelTimeTest {
    private val now = 1_800_000_000.0

    @Test fun nowAndSoon() {
        assertEquals(RelTime.Rel(RelTime.Unit.NOW, 0, false), RelTime.of(now - 20, now))
        assertEquals(RelTime.Rel(RelTime.Unit.NOW, 0, true), RelTime.of(now + 59, now))
    }

    @Test fun minutesHoursDays() {
        assertEquals(RelTime.Rel(RelTime.Unit.MINUTES, 3, false), RelTime.of(now - 180, now))
        assertEquals(RelTime.Rel(RelTime.Unit.MINUTES, 59, true), RelTime.of(now + 59 * 60, now))
        assertEquals(RelTime.Rel(RelTime.Unit.HOURS, 8, true), RelTime.of(now + 8 * 3600 + 600, now))
        // 1 h 50 min away reads as two hours
        assertEquals(RelTime.Rel(RelTime.Unit.HOURS, 2, true), RelTime.of(now + 6600, now))
        assertEquals(RelTime.Rel(RelTime.Unit.HOURS, 23, false), RelTime.of(now - 23 * 3600, now))
        assertEquals(RelTime.Rel(RelTime.Unit.DAYS, 1, true), RelTime.of(now + 24 * 3600, now))
        assertEquals(RelTime.Rel(RelTime.Unit.DAYS, 3, false), RelTime.of(now - 3 * 86400 - 100, now))
    }

    @Test fun roundingAtTheEdgeMovesToTheNextUnit() {
        // 59.6 minutes rounds to 60: shown as one hour, never "60 minutes"
        assertEquals(RelTime.Rel(RelTime.Unit.HOURS, 1, true), RelTime.of(now + 59.6 * 60, now))
    }
}

class CronBuilderTest {
    @Test fun buildsFromPickerChoices() {
        assertEquals("0 9 * * *", CronBuilder.cron(Repeat.Daily(9, 0)))
        assertEquals("30 7 * * 1-5", CronBuilder.cron(Repeat.Weekdays(7, 30)))
        assertEquals("15 18 * * 1,3", CronBuilder.cron(Repeat.Weekly(setOf(3, 1), 18, 15)))
        assertEquals("0 8 * * 0,6", CronBuilder.cron(Repeat.Weekly(setOf(6, 7), 8, 0)))   // 7 is Sunday too
        assertEquals("0 10 15 * *", CronBuilder.cron(Repeat.Monthly(15, 10, 0)))
        assertEquals("5 */3 * * *", CronBuilder.cron(Repeat.EveryHours(3, 5)))
        assertEquals("0 * * * *", CronBuilder.cron(Repeat.EveryHours(1, 0)))
        assertEquals("1 2 3 4 5", CronBuilder.cron(Repeat.Custom("  1 2 3 4 5 ")))
        assertNull(CronBuilder.cron(Repeat.Once(LocalDate.of(2026, 11, 8), 9, 0)))
    }

    @Test fun oneTimeRunIsLocalIso() {
        assertEquals("2026-11-08T09:05", CronBuilder.runAt(Repeat.Once(LocalDate.of(2026, 11, 8), 9, 5)))
    }

    @Test fun parsesBackWhatThePickerMakes() {
        val choices = listOf(
            Repeat.Daily(9, 0), Repeat.Weekdays(7, 30), Repeat.Weekly(setOf(1, 3), 18, 15), Repeat.Weekly(setOf(0, 6), 8, 0),
            Repeat.Monthly(15, 10, 0), Repeat.EveryHours(3, 5), Repeat.EveryHours(1, 0),
        )
        choices.forEach { r -> assertEquals(r, CronBuilder.parse(CronBuilder.cron(r))) }
    }

    @Test fun parsesNamesMacrosAndEquivalents() {
        assertEquals(Repeat.Weekdays(0, 9), CronBuilder.parse("9 0 * * mon-fri"))
        assertEquals(Repeat.Weekdays(0, 9), CronBuilder.parse("9 0 * * 1,2,3,4,5"))
        assertEquals(Repeat.Daily(0, 0), CronBuilder.parse("@daily"))
        assertEquals(Repeat.EveryHours(1, 0), CronBuilder.parse("@hourly"))
        assertEquals(Repeat.Weekly(setOf(0), 0, 0), CronBuilder.parse("@weekly"))
        assertEquals(Repeat.Monthly(1, 0, 0), CronBuilder.parse("@monthly"))
        assertEquals(Repeat.Daily(6, 0), CronBuilder.parse("0 6 * * 0-6"))
        assertEquals(Repeat.Weekly(setOf(0), 9, 0), CronBuilder.parse("0 9 * * 7"))
    }

    @Test fun keepsWhatItCannotShowAsCustom() {
        assertEquals(Repeat.Yearly(1, 1, 9, 0), CronBuilder.parse("0 9 1 1 *"))
        listOf("*/10 * * * *", "0 9 1 * 1", "0 9 1 1-3 *", "0 9-17 * * *", "0 9 * * 1/2", "bad", "0 25 * * *", "0 9 * * 5-1").forEach {
            assertTrue(it, CronBuilder.parse(it) is Repeat.Custom)
        }
        assertEquals(Repeat.Custom(""), CronBuilder.parse(null))
    }

    @Test fun savedScheduleBackToPicker() {
        assertEquals(Repeat.Once(LocalDate.of(2026, 11, 8), 9, 0),
            CronBuilder.fromSchedule(Schedule(id = 1, kind = "once", runAt = "2026-11-08T09:00:00+09:00")))
        assertEquals(Repeat.Daily(9, 0), CronBuilder.fromSchedule(Schedule(id = 2, kind = "cron", cron = "0 9 * * *")))
        assertEquals(Repeat.Once(LocalDate.of(2026, 1, 2), 3, 4), CronBuilder.parseRunAt("2026-01-02T03:04"))
        assertNull(CronBuilder.parseRunAt("tomorrow"))
    }

    @Test fun problemsBeforeSaving() {
        assertEquals(CronBuilder.Problem.NO_DAY, CronBuilder.problem(Repeat.Weekly(emptySet(), 9, 0)))
        assertEquals(CronBuilder.Problem.BAD_CRON, CronBuilder.problem(Repeat.Custom("0 9 *")))
        assertNull(CronBuilder.problem(Repeat.Custom("@daily")))
        assertNull(CronBuilder.problem(Repeat.Daily(9, 0)))
    }

    @Test fun pickerDaysStartOnMonday() {
        assertEquals(java.time.DayOfWeek.MONDAY, CronBuilder.dayOfWeek(CronBuilder.PICKER_DAYS.first()))
        assertEquals(java.time.DayOfWeek.SUNDAY, CronBuilder.dayOfWeek(0))
        assertEquals(java.time.DayOfWeek.SUNDAY, CronBuilder.dayOfWeek(7))
    }

    @Test fun nextRunPrefersTheServerNumber() {
        assertEquals(100.0, ScheduleTimes.nextRunSeconds(Schedule(id = 1, nextRunTs = 100.0, nextRun = "2026-11-08T09:00:00+09:00"))!!, 0.0)
        assertEquals(1_794_096_000.0, ScheduleTimes.nextRunSeconds(Schedule(id = 1, nextRun = "2026-11-08T09:00:00+09:00"))!!, 0.0)
        assertNull(ScheduleTimes.nextRunSeconds(Schedule(id = 1)))
    }
}

class JournalDaysTest {
    @Test fun daysJoinEntriesAndGapsNewestFirst() {
        val e = listOf(JournalEntry(1, "2026-11-05", "a"), JournalEntry(2, "2026-11-05", "b"), JournalEntry(3, "2026-11-03", "c"))
        val g = listOf(JournalGap("2026-11-06", "pending"), JournalGap("2026-11-03", "failed", "x"))
        val days = JournalDays.days(e, g)
        assertEquals(listOf("2026-11-06", "2026-11-05", "2026-11-03"), days.map { it.date })
        assertEquals(2, days[1].entries.size)
        assertEquals("failed", days[2].gap?.status)
    }
}

class PendingApprovalTest {
    @Test fun readsThePendingList() {
        val json = Json.parseToJsonElement(
            """{"items":[
              {"type":"approval_request","approval_id":"a1","id":"c1","name":"browser_click","detail":"주문 버튼","label":"사이트에서 누르기",
               "site":"shop.example","can_remember":true,"task_id":7,"expires_at":1,"turn_id":"t","asked_at":12.5,"unattended":true},
              {"type":"approval_request","approval_id":"a2","name":"confirm_purchase","label":"결제",
               "payment":{"site":"shop.example","summary":"책","amount":12000,"currency":"KRW","amount_text":"12,000원"},
               "card":{"id":"k","label":"내 카드","brand":"visa","last4":"1234","auto_pay":false,"needs_card":true}},
              {"type":"approval_request","name":"no id"}
            ]}""",
        )
        val list = PendingApproval.parseList(json)
        assertEquals(2, list.size)
        val a = list[0]
        assertEquals("a1", a.id); assertEquals("shop.example", a.site); assertTrue(a.canRemember); assertTrue(a.unattended)
        assertEquals(7L, a.taskId); assertEquals(12.5, a.askedAt, 0.0)
        assertTrue(list[1].isPayment)
        assertTrue(list[1].event.card!!.needsCard)
        assertEquals(false, list[1].canRemember)
        assertNull(PendingApproval.parse(Json.parseToJsonElement("""{"type":"text"}""").jsonObject))
    }
}

class AvatarVersionTest {
    /** the server's version is a millisecond time: it must fit (an Int overflowed and broke the profile and the status) */
    @Test fun bigVersionsDecode() {
        val json = chat.kelo.app.core.KeloApi.json
        assertEquals(1_794_027_761_958L, json.decodeFromString(chat.kelo.app.core.Profile.serializer(), """{"agent_name":"K","avatar_version":1794027761958}""").avatarVersion)
        assertEquals(1_794_027_761_958L, json.decodeFromString(chat.kelo.app.chat.AgentStatus.serializer(), """{"busy":false,"avatar_version":1794027761958}""").avatarVersion)
        assertEquals(1_794_027_761_958L, json.decodeFromString(AvatarSaved.serializer(), """{"avatar_version":1794027761958,"mime":"image/jpeg"}""").avatarVersion)
    }
}

class PlanRulesTest {
    private val now = java.time.LocalDateTime.of(2026, 11, 7, 8, 0)   // a Saturday
    private val today = now.toLocalDate()

    @Test fun eachRepeatBecomesTheCronTheServerKeeps() {
        val d = LocalDate.of(2026, 11, 8)
        assertNull(PlanRules.cron(SchedulePlan(d, 9, 0)))
        assertEquals("2026-11-08T09:00", PlanRules.runAt(SchedulePlan(d, 9, 0)))
        assertEquals("0 9 * * *", PlanRules.cron(SchedulePlan(d, 9, 0, Freq.DAILY)))
        assertEquals("30 7 * * 1-5", PlanRules.cron(SchedulePlan(d, 7, 30, Freq.WEEKDAYS)))
        assertEquals("0 10 * * 0,6", PlanRules.cron(SchedulePlan(d, 10, 0, Freq.WEEKENDS)))
        assertEquals("15 18 * * 1,3", PlanRules.cron(SchedulePlan(d, 18, 15, Freq.WEEKLY, setOf(3, 1))))
        assertEquals("0 9 8 * *", PlanRules.cron(SchedulePlan(d, 9, 0, Freq.MONTHLY)))
        assertEquals("0 9 8 11 *", PlanRules.cron(SchedulePlan(d, 9, 0, Freq.YEARLY)))
    }

    @Test fun aFirstDayGoesOnlyWhenItIsLaterThanTheRepeatWouldStart() {
        assertEquals("", PlanRules.startsOn(SchedulePlan(today, 9, 0, Freq.DAILY), now))
        assertEquals("", PlanRules.startsOn(SchedulePlan(today.plusDays(1), 7, 0, Freq.DAILY), java.time.LocalDateTime.of(2026, 11, 7, 8, 0)))
        assertEquals("2026-11-20", PlanRules.startsOn(SchedulePlan(LocalDate.of(2026, 11, 20), 9, 0, Freq.DAILY), now))
        assertEquals("", PlanRules.startsOn(SchedulePlan(LocalDate.of(2026, 11, 20), 9, 0), now))   // one time: no first day
        assertEquals("2026-12-31", PlanRules.endsOn(SchedulePlan(today, 9, 0, Freq.DAILY, until = LocalDate.of(2026, 12, 31))))
        assertEquals("", PlanRules.endsOn(SchedulePlan(today, 9, 0, Freq.NONE, until = LocalDate.of(2026, 12, 31))))
    }

    @Test fun problemsAreCaughtBeforeSaving() {
        assertEquals(PlanRules.Problem.NO_DAY, PlanRules.problem(SchedulePlan(today, 9, 0, Freq.WEEKLY), now))
        assertEquals(PlanRules.Problem.PAST, PlanRules.problem(SchedulePlan(today, 7, 0), now))
        assertNull(PlanRules.problem(SchedulePlan(today, 9, 0), now))
        assertEquals(PlanRules.Problem.END_BEFORE_START,
            PlanRules.problem(SchedulePlan(LocalDate.of(2026, 12, 1), 9, 0, Freq.DAILY, until = LocalDate.of(2026, 11, 30)), now))
    }

    @Test fun savedSchedulesComeBackInTheSimpleFormOrNot() {
        val daily = Schedule(1, kind = "cron", cron = "0 9 * * *", nextRun = "2026-11-08T09:00+09:00", endsOn = "2026-12-31")
        assertEquals(SchedulePlan(LocalDate.of(2026, 11, 8), 9, 0, Freq.DAILY, until = LocalDate.of(2026, 12, 31)), PlanRules.fromSchedule(daily, today))
        assertEquals(Freq.WEEKENDS, PlanRules.fromSchedule(Schedule(2, cron = "0 10 * * 6,0"), today)!!.freq)
        val monthly = PlanRules.fromSchedule(Schedule(3, cron = "0 9 15 * *"), today)!!
        assertEquals(Freq.MONTHLY to LocalDate.of(2026, 11, 15), monthly.freq to monthly.date)
        val yearly = PlanRules.fromSchedule(Schedule(4, cron = "0 8 25 12 *"), today)!!
        assertEquals(Freq.YEARLY to LocalDate.of(2026, 12, 25), yearly.freq to yearly.date)
        val once = PlanRules.fromSchedule(Schedule(5, kind = "once", runAt = "2026-11-09T07:30:00+09:00"), today)!!
        assertEquals(SchedulePlan(LocalDate.of(2026, 11, 9), 7, 30), once)
        // a window schedule shows as its plain time (the spread is never offered)
        assertEquals(Freq.DAILY, PlanRules.fromSchedule(Schedule(6, kind = "window", cron = "0 9 * * *"), today)!!.freq)
        // what the form cannot show
        assertNull(PlanRules.fromSchedule(Schedule(7, kind = "interval", everyMinutes = 90), today))
        assertNull(PlanRules.fromSchedule(Schedule(8, cron = "0 */3 * * *"), today))
        assertNull(PlanRules.fromSchedule(Schedule(9, cron = "0 9 1-7 * 1"), today))
        val replaced = PlanRules.replacing(Schedule(9, cron = "0 9 1-7 * 1", nextRun = "2026-12-07T09:00+09:00"), today)
        assertEquals(SchedulePlan(today, 9, 0, Freq.DAILY), replaced)
    }

    @Test fun aCronIsNeverTakenForWords() {
        listOf("0 9 * * *", "@daily", "*/5 * * * *", "Custom schedule (0 9 1-7 * 1)").forEach { assertTrue(it, CronBuilder.looksLikeCron(it)) }
        listOf("Every day at 9:00 AM", "매일 오전 9:00", "Every month on day 1, 15 at 9:00 AM", "정해진 규칙으로 반복").forEach {
            assertTrue(it, !CronBuilder.looksLikeCron(it))
        }
    }
}

class PlanSheetRulesTest {
    @Test fun theWeekStartsOnTheLocalesFirstDay() {
        assertEquals(listOf(1, 2, 3, 4, 5, 6, 0), PlanRules.weekOrder(java.time.DayOfWeek.MONDAY))
        assertEquals(listOf(0, 1, 2, 3, 4, 5, 6), PlanRules.weekOrder(java.time.DayOfWeek.SUNDAY))
        assertEquals(listOf(6, 0, 1, 2, 3, 4, 5), PlanRules.weekOrder(java.time.DayOfWeek.SATURDAY))
    }

    @Test fun weeklyStartsOnTheDatesWeekdayAndKeepsChosenDays() {
        val wed = LocalDate.of(2026, 12, 9)
        val p = SchedulePlan(wed, 9, 0)
        assertEquals(setOf(3), PlanRules.withFreq(p, Freq.WEEKLY).days)
        assertEquals(Freq.WEEKLY, PlanRules.withFreq(p, Freq.WEEKLY).freq)
        assertEquals(setOf(1, 5), PlanRules.withFreq(p.copy(days = setOf(1, 5)), Freq.WEEKLY).days)
        assertEquals(emptySet<Int>(), PlanRules.withFreq(p, Freq.MONTHLY).days)
        val sun = SchedulePlan(LocalDate.of(2026, 11, 8), 9, 0)
        assertEquals(setOf(0), PlanRules.withFreq(sun, Freq.WEEKLY).days)
    }

    @Test fun theLastWeekdayStaysOn() {
        assertEquals(setOf(1, 3), PlanRules.toggleDay(setOf(1), 3))
        assertEquals(setOf(3), PlanRules.toggleDay(setOf(1, 3), 1))
        assertEquals(setOf(3), PlanRules.toggleDay(setOf(3), 3))
    }

    @Test fun theFirstOfferedLastDayIsAMonthAfterTheStart() {
        val today = LocalDate.of(2026, 11, 8)
        assertEquals(LocalDate.of(2026, 12, 9), PlanRules.defaultUntil(LocalDate.of(2026, 11, 9), today))
        assertEquals(LocalDate.of(2026, 12, 8), PlanRules.defaultUntil(LocalDate.of(2026, 10, 1), today))
    }

    @Test fun circleLabelsStayShort() {
        assertEquals("월", PlanRules.circleLabel("월", "월"))
        assertEquals("Mon", PlanRules.circleLabel("Mon", "M"))
        assertEquals("lun", PlanRules.circleLabel("lun.", "L"))
        assertEquals("T2", PlanRules.circleLabel("Th 2", "T2"))
        assertEquals("ن", PlanRules.circleLabel("الاثنين", "ن"))
    }
}
