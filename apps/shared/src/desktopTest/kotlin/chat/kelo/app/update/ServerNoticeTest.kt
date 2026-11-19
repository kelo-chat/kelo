package chat.kelo.app.update

import chat.kelo.app.core.AppVersion
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** The server-update notice's rules (update/ServerNotice.kt), read from the vectors every app shares (apps/shared/test-vectors/server-notice.json). */
class ServerNoticeTest {
    @After fun reset() { AppVersion.reset() }

    private fun vectors(): JsonArray {
        val start = File("").absoluteFile
        val file = generateSequence(start) { it.parentFile }.flatMap { sequenceOf(File(it, "test-vectors/server-notice.json"), File(it, "shared/test-vectors/server-notice.json")) }
            .firstOrNull { it.isFile } ?: error("apps/shared/test-vectors/server-notice.json not found from $start")
        return Json.parseToJsonElement(file.readText()).jsonArray
    }

    private fun long(o: kotlinx.serialization.json.JsonObject, k: String): Long? =
        o[k]?.takeIf { it !is JsonNull }?.jsonPrimitive?.content?.toLong()

    @Test fun theSharedVectorsDecideTheSameWay() {
        for (case in vectors()) {
            val o = case.jsonObject
            val offered = o["offered"]?.takeIf { it !is JsonNull }?.jsonPrimitive?.content
            val show = ServerNoticeRules.show(offered, long(o, "last_shown"), long(o, "app_shown"), long(o, "now")!!, o.getValue("covered").jsonPrimitive.content == "true")
            assertEquals(o.getValue("name").jsonPrimitive.content, o.getValue("show").jsonPrimitive.content == "true", show)
        }
    }

    @Test fun considerOpensOnceADayAndSavesTheTime() {
        var clock = 1_000_000_000_000L
        var saved: String? = null
        val notice = ServerNotice(load = { saved }, save = { saved = it }, now = { clock })
        assertTrue(notice.consider("0.10.6", appPopupShownMs = null, covered = false))
        assertEquals("0.10.6", notice.open.value)
        assertEquals("1000000000000", saved)
        notice.close()
        assertNull(notice.open.value)
        clock += 23L * 3600 * 1000
        assertFalse(notice.consider("0.10.6", appPopupShownMs = null, covered = false))
        val again = ServerNotice(load = { saved }, save = { saved = it }, now = { clock })
        assertFalse(again.consider("0.10.6", appPopupShownMs = null, covered = false))
        clock += 1L * 3600 * 1000
        assertTrue(again.consider("0.10.6", appPopupShownMs = null, covered = false))
    }

    @Test fun aBrokenStoreNeverBreaksTheNotice() {
        val notice = ServerNotice(load = { error("no storage") }, save = { error("full") }, now = { 5L })
        assertTrue(notice.consider("0.10.6", appPopupShownMs = null, covered = false))
        assertEquals("0.10.6", notice.open.value)
        notice.close()
    }

    @Test fun theAppPopupLastShownIsReadBack() {
        val popup = UpdatePopup(load = { "1234" }, save = {}, now = { 9_999L })
        assertEquals(1234L, popup.lastShownMs())
        assertNull(UpdatePopup(load = { null }, save = {}, now = { 0L }).lastShownMs())
    }

    @Test fun theInterceptorTakesTheServerUpdateOnlyFromAboutAndProfile() = runBlocking {
        val server = MockWebServer().apply { start() }
        try {
            fun json(update: String?) = MockResponse().setHeader("Content-Type", "application/json")
                .apply { update?.let { setHeader("X-Kelo-Server-Update", it) } }.setBody("{}")
            server.enqueue(json("0.10.6")); server.enqueue(json("0.10.7")); server.enqueue(json(null))
            val api = KeloApi(server.url("/").toString(), "t")
            api.get<JsonElement>("/api/about")
            assertEquals("0.10.6", AppVersion.serverUpdate.value)
            api.get<JsonElement>("/api/goals")                       // another call says nothing about it: kept
            assertEquals("0.10.6", AppVersion.serverUpdate.value)
            api.get<JsonElement>("/api/profile")                     // the profile without the header: no newer server any more
            assertNull(AppVersion.serverUpdate.value)
            AppVersion.setServerUpdate(" 0.10.9 "); assertEquals("0.10.9", AppVersion.serverUpdate.value)
            AppVersion.reset(); assertNull(AppVersion.serverUpdate.value)
        } finally {
            server.shutdown()
        }
    }
}
