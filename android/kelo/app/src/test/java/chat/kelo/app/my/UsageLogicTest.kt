package chat.kelo.app.my

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneOffset

class UsageLogicTest {
    private val name: (String) -> String = { UsageLogic.groupName(it, { b -> "$b 모델" }, " · ") }
    private fun w(group: String?, label: String, pct: Int = 10) = UsageWindow(group = group, label = label, usedPercent = pct)
    private fun usage(vararg ws: UsageWindow, error: String? = null) =
        Usage(subscription = Subscription("antigravity-oauth", ws.toList(), error))

    @Test fun groupNameMatchesWeb() {
        assertEquals("Gemini 모델", name("Gemini Models"))
        assertEquals("Claude · GPT 모델", name("Claude and GPT models"))
        assertEquals("Claude · GPT 모델", name("Claude and GPT model"))
        assertEquals("Other", name("Other"))
    }

    @Test fun activeGroupNeedsExactlyOneHit() {
        val groups = listOf("Gemini Models", "Claude and GPT models")
        assertEquals("Gemini Models", UsageLogic.activeGroup(groups, "gemini-3.8-flash-high"))
        assertEquals("Claude and GPT models", UsageLogic.activeGroup(groups, "claude-opus-4"))
        assertNull(UsageLogic.activeGroup(groups, "llama-3"))
        // the stop words ("models", "and") never count
        assertNull(UsageLogic.activeGroup(groups, "models-and"))
        // both groups match: cannot be told
        assertNull(UsageLogic.activeGroup(listOf("Gemini Models", "Gemini Pro"), "gemini-pro"))
    }

    @Test fun activeGroupComesFirstAndOthersKeepOrder() {
        val u = usage(
            w("Claude and GPT models", "Current session"), w("Gemini Models", "Current session"),
            w("Gemini Models", "Current week"), w("Claude and GPT models", "Current week"),
        )
        val gs = UsageLogic.groups(u, "gemini-3.8-flash", name)
        assertEquals(listOf("Gemini Models", "Claude and GPT models"), gs.map { it.group })
        assertTrue(gs[0].active)
        assertFalse(gs[1].active)
        assertEquals("Gemini 모델", gs[0].name)
        assertEquals(listOf("Current session", "Current week"), gs[0].windows.map { it.label })
        assertEquals(2, gs[1].windows.size)
        // short form: the other group is one line; full form: everything
        assertTrue(UsageLogic.oneLine(gs, gs[1], full = false))
        assertFalse(UsageLogic.oneLine(gs, gs[0], full = false))
        assertFalse(UsageLogic.oneLine(gs, gs[1], full = true))
    }

    @Test fun unknownModelKeepsServerOrderAndShowsAllInFull() {
        val u = usage(w("Claude and GPT models", "a"), w("Gemini Models", "b"))
        val gs = UsageLogic.groups(u, "", name)
        assertEquals(listOf("Claude and GPT models", "Gemini Models"), gs.map { it.group })
        assertTrue(gs.none { it.active })
        assertFalse(UsageLogic.oneLine(gs, gs[1], full = false))
    }

    @Test fun ungroupedWindowsFormOneNamelessGroup() {
        val gs = UsageLogic.groups(usage(w(null, "Current session"), w(null, "Current week")), "claude-sonnet", name)
        assertEquals(1, gs.size)
        assertNull(gs[0].group)
        assertEquals("", gs[0].name)
        assertFalse(gs[0].active)
        assertEquals(2, gs[0].windows.size)
    }

    @Test fun noGroupsWithoutSubscriptionOrWithError() {
        assertTrue(UsageLogic.groups(null, "x", name).isEmpty())
        assertTrue(UsageLogic.groups(Usage(), "x", name).isEmpty())
        assertTrue(UsageLogic.groups(usage(w("G", "a"), error = "로그인이 필요해요"), "g", name).isEmpty())
    }

    @Test fun labelsAreReplacedLikeWeb() {
        val ko = UsageLogic.LABEL_SOURCES.zip(listOf("현재 세션", "이번 주", "모든 모델", "주간 한도", "5시간 한도"))
        assertEquals("현재 세션", UsageLogic.label("Current session", ko))
        assertEquals("이번 주 (모든 모델)", UsageLogic.label("Current week (all models)", ko))
        assertEquals("Something else", UsageLogic.label("Something else", ko))
    }

    @Test fun resetBuckets() {
        val now = 1_700_000_000_000L
        fun at(mins: Long) = java.time.Instant.ofEpochMilli(now + mins * 60000).toString()
        fun rel(mins: Long) = (UsageLogic.reset(UsageWindow(resetsAt = at(mins)), now, ZoneOffset.UTC) as ResetInfo.At).rel
        assertEquals(ResetInfo.Rel.Soon, rel(0))
        assertEquals(ResetInfo.Rel.Soon, rel(-5))
        assertEquals(ResetInfo.Rel.Minutes(30), rel(30))
        assertEquals(ResetInfo.Rel.Hours(5), rel(300))
        assertEquals(ResetInfo.Rel.Hours(2), rel(90))      // 1.5 rounds up, like Math.round
        assertEquals(ResetInfo.Rel.Days(2), rel(2880))
        // an unreadable time falls back to the words, which are read here when they can be
        assertEquals(ResetInfo.At(ResetInfo.Rel.Hours(5), now + 5 * 3600_000), UsageLogic.reset(UsageWindow(resetsAt = "soon", resetsText = "in 5 hours"), now))
        assertEquals(ResetInfo.Text("whenever"), UsageLogic.reset(UsageWindow(resetsAt = "soon", resetsText = "whenever"), now))
        assertNull(UsageLogic.reset(UsageWindow(), now))
    }

    @Test fun parsesOffsetsAndLocalTimes() {
        assertEquals(0L, UsageLogic.parseTime("1970-01-01T00:00:00+00:00", ZoneOffset.UTC))
        assertEquals(0L, UsageLogic.parseTime("1970-01-01T09:00:00+09:00", ZoneOffset.UTC))
        assertEquals(0L, UsageLogic.parseTime("1970-01-01T00:00:00Z", ZoneOffset.UTC))
        assertEquals(-9 * 3600_000L, UsageLogic.parseTime("1970-01-01T00:00:00", ZoneOffset.ofHours(9)))
        assertNull(UsageLogic.parseTime("tomorrow", ZoneOffset.UTC))
    }

    @Test fun bars() {
        assertEquals(1f, UsageLogic.barFraction(130))
        assertTrue(UsageLogic.isHot(90))
        assertFalse(UsageLogic.isHot(89))
        assertEquals(0, UsageLogic.left(120))
    }

    private fun k(kind: String, input: Long, output: Long = 0, read: Long = 0) =
        KindUsage(kind = kind, inputTokens = input, outputTokens = output, cacheReadTokens = read)

    @Test fun jobRowsListKindsThatUsedTokensLargestFirst() {
        val part = UsagePart(kinds = listOf(k("journal", 100, 20), k("chat", 1000, 200, read = 600), k("probe", 0), k("brand-new", 5), k("other", 5)))
        val rows = UsageLogic.jobRows(part)
        assertEquals(listOf("chat", "journal", "other"), rows.map { it.kind })   // nothing used: no row; unknown kinds go to "other"
        assertEquals(JobRow("chat", 1200, 60), rows[0])
        assertEquals(JobRow("other", 10, null), rows[2])
        assertTrue(UsageLogic.jobRows(null).isEmpty())
        // a tie keeps the server's order of kinds
        assertEquals(listOf("chat", "memory"), UsageLogic.jobRows(UsagePart(kinds = listOf(k("memory", 5), k("chat", 5)))).map { it.kind })
    }

    @Test fun cachePercentOnlyWhenReported() {
        assertNull(UsageLogic.cachePercent(0, 1000))           // no cache reported: no "0%"
        assertNull(UsageLogic.cachePercent(10, 0))
        assertEquals(1, UsageLogic.cachePercent(1, 10_000))    // some was read: never shown as 0
        assertEquals(60, UsageLogic.cachePercent(600, 1000))
        assertEquals(100, UsageLogic.cachePercent(2000, 1000))
    }

    @Test fun backgroundFailureShowsOnlyForTheModelStillChosen() {
        val state = BackgroundModelState("anthropic", "small", BackgroundFallback(1.7e9, "journal", "down"))
        assertEquals("journal", UsageLogic.fallbackShown(state, "anthropic", "small")?.kind)
        assertNull(UsageLogic.fallbackShown(state, "anthropic", "other"))
        assertNull(UsageLogic.fallbackShown(state, "", ""))
        assertNull(UsageLogic.fallbackShown(state.copy(lastFallback = null), "anthropic", "small"))
        assertNull(UsageLogic.fallbackShown(null, "anthropic", "small"))
    }

    @Test fun olderServerUsageDecodesWithoutJobs() {
        val json = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
        val old = json.decodeFromString(Usage.serializer(), """{"today":{"input_tokens":1,"output_tokens":2},"total":{"input_tokens":3,"output_tokens":4}}""")
        assertNull(old.jobs)
        val now = json.decodeFromString(Usage.serializer(), """{"today":{"input_tokens":1,"output_tokens":2},"total":{"input_tokens":3,"output_tokens":4},
            "jobs":{"today":{"since":"2026-11-09","calls":2,"input_tokens":200,"output_tokens":20,"cache_read_tokens":120,"cache_write_tokens":40,
            "cache_share":0.6,"kinds":[{"kind":"chat","calls":1,"input_tokens":100,"output_tokens":10,"cache_read_tokens":60,"cache_write_tokens":20,
            "cache_share":0.6}]},"month":{}},"background":{"provider":"","model":"","kinds":["journal"],"last_fallback":null}}""")
        assertEquals(220, UsageLogic.partTokens(now.jobs?.today))
        assertEquals(JobRow("chat", 110, 60), UsageLogic.jobRows(now.jobs?.today).single())
        assertEquals("", now.background?.provider)
    }
}
