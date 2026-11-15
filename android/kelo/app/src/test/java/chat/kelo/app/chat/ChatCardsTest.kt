package chat.kelo.app.chat

import chat.kelo.app.core.ApiError
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The browser row's filmstrip and tile, the outcome chips, the provider list's groups and the consent rules. */
class ChatCardsTest {
    @Test fun theFilmstripKeepsTheLastThreeDifferentPictures() {
        var s = emptyList<String>()
        listOf("a", "a", null, "b", "c", "d").forEach { s = ChatReducer.filmstrip(s, it) }
        assertEquals(listOf("b", "c", "d"), s)
        assertEquals(3, ChatReducer.FILMSTRIP)
    }

    @Test fun theSiteTileLetter() {
        assertEquals("E", SiteTile.letter("www.example.com", ""))
        assertEquals("1", SiteTile.letter("11st.co.kr", ""))
        assertEquals("쿠", SiteTile.letter("", "쿠팡"))
        assertEquals("?", SiteTile.letter("", ""))
    }

    @Test fun outcomes() {
        val a = ChatItem.Approval("k", "id", "t", "", null)
        assertNull(Outcomes.of(a))
        assertEquals(Outcome.ALLOWED, Outcomes.of(a.copy(answer = true)))
        assertEquals(Outcome.DENIED, Outcomes.of(a.copy(answer = false)))
        assertEquals(Outcome.EXPIRED, Outcomes.of(a.copy(expired = true)))
    }

    private fun p(name: String, unofficial: Boolean = false, consent: Int? = null, version: Int = 1, vendor: String = "") =
        ProviderInfo(name, name, unofficial = unofficial, vendor = vendor, consent = consent?.let { ProviderConsentRecord(1.0, it) }, consentVersion = version)

    @Test fun unofficialProvidersGoLastUnderADivider() {
        val opts = listOf(
            SelectOption("", "기본", keep = true),
            SelectOption("claude-subscription", "Claude 구독", group = 1, sortKey = "Claude"),
            SelectOption("openai", "OpenAI", rank = 0),
            SelectOption("agy", "Antigravity", group = 1, rank = 0),
            SelectOption("zai", "Z.ai"),
        )
        val sorted = SelectRules.arrange(opts, "", sorted = true)
        assertEquals(listOf("", "openai", "zai", "agy", "claude-subscription"), sorted.map { it.value })
        assertEquals(listOf(3), sorted.indices.filter { SelectRules.dividerBefore(sorted, it) })
        // given order is kept, but the later group still goes last
        assertEquals(listOf("", "openai", "zai", "claude-subscription", "agy"), SelectRules.arrange(opts, "", sorted = false).map { it.value })
        // no groups: exactly as given
        val plain = opts.map { it.copy(group = 0) }
        assertEquals(plain, SelectRules.arrange(plain, "", sorted = false))
        assertTrue(SelectRules.arrange(plain, "", sorted = true).indices.none { SelectRules.dividerBefore(SelectRules.arrange(plain, "", true), it) })
    }

    @Test fun consentIsNeededOncePerProviderAndVersion() {
        assertFalse(ProviderConsentRules.needs(p("openai")))
        assertTrue(ProviderConsentRules.needs(p("claude-subscription", unofficial = true)))
        assertFalse(ProviderConsentRules.needs(p("claude-subscription", unofficial = true, consent = 1)))
        assertTrue(ProviderConsentRules.needs(p("claude-subscription", unofficial = true, consent = 1, version = 2)))
        assertFalse(ProviderConsentRules.needs(ProviderConsentRules.accepted(p("qwen-oauth", unofficial = true, version = 3), 5.0)))
        assertEquals(1, ProviderConsentRules.group(p("x", unofficial = true)))
        assertEquals(0, ProviderConsentRules.group(p("x")))
    }

    @Test fun pendingChoicesAndThe428() {
        val list = listOf(p("claude-subscription", unofficial = true), p("openai"), p("minimax-oauth", unofficial = true, consent = 1))
        assertEquals(listOf("claude-subscription"), ProviderConsentRules.pending("claude-subscription", "openai", list).map { it.name })
        assertEquals(emptyList<String>(), ProviderConsentRules.pending("openai", "minimax-oauth", list).map { it.name })
        assertEquals(emptyList<String>(), ProviderConsentRules.pending("", "", list).map { it.name })
        assertEquals("claude-subscription", ProviderConsentRules.requiredBy(ApiError(428, "consent_required:claude-subscription")))
        assertNull(ProviderConsentRules.requiredBy(ApiError(422, "consent_required:x")))
        assertNull(ProviderConsentRules.requiredBy(ApiError(428, "something else")))
        assertEquals("asked.qwen-oauth.3", ProviderConsentRules.askedKey(p("qwen-oauth", unofficial = true, version = 3)))
    }

    @Test fun serviceNamesAndParticles() {
        assertEquals(ConsentService.CLAUDE_SUBSCRIPTION, ProviderConsentRules.service("claude-subscription-max"))
        assertEquals(ConsentService.ANTIGRAVITY, ProviderConsentRules.service("antigravity-oauth"))
        assertEquals(ConsentService.CHATGPT_SUBSCRIPTION, ProviderConsentRules.service("openai-codex"))
        assertEquals(ConsentService.QWEN, ProviderConsentRules.service("qwen-oauth"))
        assertEquals(ConsentService.MINIMAX, ProviderConsentRules.service("minimax-oauth"))
        assertEquals(ConsentService.OTHER, ProviderConsentRules.service("openai"))
        assertEquals("이", ProviderConsentRules.subjectParticle("Anthropic"))
        assertEquals("이", ProviderConsentRules.subjectParticle("Google"))
        assertEquals("가", ProviderConsentRules.subjectParticle("OpenAI"))
        assertEquals("가", ProviderConsentRules.subjectParticle("Alibaba"))
        assertEquals("가", ProviderConsentRules.subjectParticle("MiniMax"))
        assertEquals("이", ProviderConsentRules.subjectParticle("카카오뱅크"[0].toString() + "앱"))   // 앱: a final consonant
        assertEquals("가", ProviderConsentRules.subjectParticle("네이버"))
    }

    @Test fun anApprovalCanOfferToKeepAllowingInItsSchedule() {
        val ev = TurnEvent.parse(kotlinx.serialization.json.Json.parseToJsonElement(
            """{"type":"approval_request","approval_id":"a","name":"browser_click","can_remember_schedule":true,"schedule_id":3}""",
        )) as TurnEvent.ApprovalRequest
        assertTrue(ev.canRememberSchedule)
        val items = ChatReducer.apply(emptyList(), ev) { "k" }
        assertTrue((items.single() as ChatItem.Approval).canRememberSchedule)
    }
}

class ApprovalCardTextTest {
    @Test fun approvalsCarryReadablePairsNeverTheArguments() {
        val ev = TurnEvent.parse(kotlinx.serialization.json.Json.parseToJsonElement(
            """{"type":"approval_request","approval_id":"a","name":"browser_open","label":"사이트 열기","detail":"github.com 열기",
               "arguments":{"site":"https://github.com/login","name":"github"},
               "lines":[{"label":"사이트","value":"github.com"},{"label":"","value":"x"},{"label":"빈 값","value":""},
                        {"label":"3","value":"c"},{"label":"4","value":"d"},{"label":"5","value":"e"}]}""",
        )) as TurnEvent.ApprovalRequest
        assertEquals(listOf("github.com", "x", "c", "d"), ev.lines.map { it.value })   // empties dropped, at most four
        val item = ChatReducer.apply(emptyList(), ev) { "k" }.single() as ChatItem.Approval
        assertEquals(ev.lines, item.lines)
        assertEquals(emptyList<ApprovalLine>(), ApprovalLine.parseList(null))
    }

    @Test fun theObjectParticleFollowsTheLastSound() {
        assertEquals("를", ProviderConsentRules.objectParticle("사이트 열기"))
        assertEquals("adding an event", ProviderConsentRules.askWord("Adding an event", "en"))
        assertEquals("Gmail sending", ProviderConsentRules.askWord("Gmail sending", "en"))
        assertEquals("Adding an event", ProviderConsentRules.askWord("Adding an event", "de"))
        assertEquals("을", ProviderConsentRules.objectParticle("로그인 정보 입력"))
        assertEquals("을", ProviderConsentRules.objectParticle("browser_click"[0].toString() + "lick"))
        assertEquals("를", ProviderConsentRules.objectParticle("Notion 페이지 만들기"))
    }

    @Test fun aLoginFormAppearsWithoutAnApprovalButNotWhenRefused() {
        val ok = TurnEvent.ToolResult("1", "request_login", "saved as 'login-example-com'", false, null)
        assertTrue(ChatReducer.apply(emptyList(), ok) { "k" }.any { it is ChatItem.Vault })
        val refused = TurnEvent.ToolResult("1", "request_login", "saved as 'login-example-com'", false, false)
        assertFalse(ChatReducer.apply(emptyList(), refused) { "k" }.any { it is ChatItem.Vault })
    }
}
