package chat.kelo.app.work

import chat.kelo.app.agent.DryRun
import chat.kelo.app.agent.LastCheck
import chat.kelo.app.agent.Watcher
import chat.kelo.app.core.KeloApi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WatchPagesTest {
    @Test
    fun `newer of last_check and dry_run is shown when last_check is newer`() {
        val dry = DryRun(at = 100.0, ok = true, detail = "dry run finding")
        val check = LastCheck(at = 200.0, detail = "latest check finding")
        val w = Watcher(id = 1, dryRun = dry, lastCheck = check)

        val res = watchLastResult(w)
        assertNotNull(res)
        assertEquals("latest check finding", res?.text)
        assertEquals(200.0, res?.at)
        assertFalse(res?.isError ?: true)
    }

    @Test
    fun `newer of last_check and dry_run is shown when dry_run is newer`() {
        val dry = DryRun(at = 300.0, ok = true, detail = "manual check finding")
        val check = LastCheck(at = 200.0, detail = "older scheduled check")
        val w = Watcher(id = 1, dryRun = dry, lastCheck = check)

        val res = watchLastResult(w)
        assertNotNull(res)
        assertEquals("manual check finding", res?.text)
        assertEquals(300.0, res?.at)
        assertFalse(res?.isError ?: true)
    }

    @Test
    fun `shows last_check when dry_run is null`() {
        val check = LastCheck(at = 200.0, detail = "scheduled check only")
        val w = Watcher(id = 1, dryRun = null, lastCheck = check)

        val res = watchLastResult(w)
        assertNotNull(res)
        assertEquals("scheduled check only", res?.text)
        assertEquals(200.0, res?.at)
    }

    @Test
    fun `shows dry_run when last_check is null`() {
        val dry = DryRun(at = 150.0, ok = true, detail = "dry run only")
        val w = Watcher(id = 1, dryRun = dry, lastCheck = null)

        val res = watchLastResult(w)
        assertNotNull(res)
        assertEquals("dry run only", res?.text)
        assertEquals(150.0, res?.at)
    }

    @Test
    fun `returns null when both are null`() {
        val w = Watcher(id = 1, dryRun = null, lastCheck = null)
        assertNull(watchLastResult(w))
    }

    @Test
    fun `error text and danger flag are preserved`() {
        val checkErr = LastCheck(at = 400.0, error = "Connection failed")
        val wErr = Watcher(id = 1, lastCheck = checkErr)
        val resErr = watchLastResult(wErr)
        assertNotNull(resErr)
        assertEquals("Connection failed", resErr?.text)
        assertTrue(resErr?.isError ?: false)

        val dryErr = DryRun(at = 500.0, ok = false, error = "HTTP 404")
        val wDryErr = Watcher(id = 2, dryRun = dryErr)
        val resDryErr = watchLastResult(wDryErr)
        assertNotNull(resDryErr)
        assertEquals("HTTP 404", resDryErr?.text)
        assertTrue(resDryErr?.isError ?: false)
    }

    @Test
    fun `decodes watcher with last_check`() {
        val json = """{
            "id": 10,
            "name": "Check Test",
            "last_check": {
                "at": 1700001000.0,
                "detail": "가격이 50,000원으로 확인되었어요",
                "value": "50,000",
                "hit": true,
                "error": null
            }
        }"""
        val w = KeloApi.json.decodeFromString(Watcher.serializer(), json)
        assertEquals(10L, w.id)
        assertNotNull(w.lastCheck)
        assertEquals(1700001000.0, w.lastCheck?.at)
        assertEquals("가격이 50,000원으로 확인되었어요", w.lastCheck?.detail)
        assertEquals("50,000", w.lastCheck?.value)
        assertEquals(true, w.lastCheck?.hit)
        assertNull(w.lastCheck?.error)
    }

    @Test
    fun `decodes watcher event with count and defaults to 1`() {
        val jsonWithCount = """{
            "id": 1,
            "at": 1700002000.0,
            "kind": "checked",
            "detail": "1,250,000원",
            "value": "1,250,000",
            "count": 5
        }"""
        val ev1 = KeloApi.json.decodeFromString(chat.kelo.app.agent.WatcherEvent.serializer(), jsonWithCount)
        assertEquals(5, ev1.count)
        assertEquals("checked", ev1.kind)

        val jsonWithoutCount = """{
            "id": 2,
            "at": 1700002000.0,
            "kind": "fired",
            "detail": "980,000원",
            "value": "980,000"
        }"""
        val ev2 = KeloApi.json.decodeFromString(chat.kelo.app.agent.WatcherEvent.serializer(), jsonWithoutCount)
        assertEquals(1, ev2.count)
    }
}
