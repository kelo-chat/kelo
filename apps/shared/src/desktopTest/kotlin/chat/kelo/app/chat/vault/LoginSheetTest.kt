package chat.kelo.app.chat.vault

import chat.kelo.app.core.KeloApi
import chat.kelo.app.my.MyFakeServer
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The compact login card and its sheet: prefill, when Save is enabled, the checkbox's mode, one automatic continue, cancel / gone. */
@OptIn(ExperimentalCoroutinesApi::class)
class LoginSheetTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null

    @Before fun main() { Dispatchers.setMain(Dispatchers.Default) }

    @After fun stop() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    @Test fun theUserNameStartsWithTheKnownAccountAndAKeyHasNone() {
        assertEquals("me@example.com", LoginSheetRules.prefill(true, "  me@example.com "))
        assertEquals("", LoginSheetRules.prefill(true, null))
        assertEquals("", LoginSheetRules.prefill(true, ""))
        assertEquals("me@example.com", LoginSheetRules.prefill(true, "me@example.com\nsecond"))
        assertEquals("", LoginSheetRules.prefill(false, "me@example.com"))
    }

    @Test fun theRequestParsesTheServersPrefill() {
        val json = """{"id":"sr_1","kind":"login","name":"shop","label":"Shop","hosts":["shop.example.com"],"username_hint":"me@example.com"}"""
        val parsed = KeloApi.json.decodeFromString(SecretRequest.serializer(), json)
        assertEquals("me@example.com", parsed.usernameHint)
        assertEquals("", KeloApi.json.decodeFromString(SecretRequest.serializer(), """{"id":"sr_2","kind":"login"}""").usernameHint)
    }

    @Test fun saveWaitsForBothFieldsAndNeverWhileSaving() {
        assertFalse(LoginSheetRules.canSave(true, "me", "", busy = false))
        assertFalse(LoginSheetRules.canSave(true, " ", "pw", busy = false))
        assertTrue(LoginSheetRules.canSave(true, "me", "pw", busy = false))
        assertFalse(LoginSheetRules.canSave(true, "me", "pw", busy = true))
        assertFalse(LoginSheetRules.canSave(false, "", "short", busy = false))
        assertTrue(LoginSheetRules.canSave(false, "", "sk-12345678", busy = false))
    }

    @Test fun theCheckboxPicksTheSavingMode() {
        assertEquals(VaultMode.AFTER_SUCCESS, LoginSheetRules.mode(true))
        assertEquals(VaultMode.ONCE, LoginSheetRules.mode(false))
    }

    @Test fun theCardNamesTheSiteOfALoginAndTheServiceOfAKey() {
        assertEquals("shop.example.com", LoginSheetRules.forName(true, listOf("shop.example.com"), "Shop", "shop"))
        assertEquals("Shop", LoginSheetRules.forName(true, emptyList(), "Shop", "shop"))
        assertEquals("Telegram bot", LoginSheetRules.forName(false, listOf("api.telegram.org"), "Telegram bot", "telegram"))
        assertEquals("api.telegram.org", LoginSheetRules.forName(false, listOf("api.telegram.org"), "", "telegram"))
    }

    @Test fun theAgentIsToldOnceRightAfterASaveAndOnlyWithAHandler() {
        assertFalse(LoginSheetRules.shouldNotify(saved = false, alreadySent = false, hasHandler = true))
        assertTrue(LoginSheetRules.shouldNotify(saved = true, alreadySent = false, hasHandler = true))
        assertFalse(LoginSheetRules.shouldNotify(saved = true, alreadySent = true, hasHandler = true))
        assertFalse(LoginSheetRules.shouldNotify(saved = true, alreadySent = false, hasHandler = false))
    }

    @Test fun anUncheckedBoxSendsTheLoginForOneUse() {
        val bodies = java.util.Collections.synchronizedList(mutableListOf<String>())
        val s = MyFakeServer { m, p, body -> if (m == "PUT" && p == "/api/vault/shop/login") { bodies += body; ok("""{}""") } else null }.also { server = it }
        val vm = vms.make { SecretEntryViewModel(s.sessions()) }
        var outcome: SecretOutcome? = null
        vm.saveLogin("shop", " me ", "pw", LoginSheetRules.mode(false)) { outcome = it }
        until("saved") { outcome != null }
        assertTrue(outcome is SecretOutcome.Saved)
        assertEquals("once", (outcome as SecretOutcome.Saved).result.mode)
        assertTrue(bodies.any { it.contains("\"mode\":\"once\"") })
    }

    @Test fun aFailedSaveKeepsTheSheetsProblemAndAGoneRequestFolds() {
        val s = MyFakeServer { _, p, _ -> if (p.contains("/gone/")) fail(404, "gone") else fail(400, "wrong") }.also { server = it }
        val vm = vms.make { SecretEntryViewModel(s.sessions()) }
        var outcome: SecretOutcome? = null
        vm.saveLogin("shop", "me", "pw", VaultMode.AFTER_SUCCESS) { outcome = it }
        until("failed") { outcome != null }
        assertEquals("wrong", vm.state.value.problem?.detail)
        outcome = null
        vm.saveLogin("gone", "me", "pw", VaultMode.AFTER_SUCCESS) { outcome = it }
        until("gone") { outcome != null }
        assertEquals(SecretOutcome.Gone, outcome)
    }
}
