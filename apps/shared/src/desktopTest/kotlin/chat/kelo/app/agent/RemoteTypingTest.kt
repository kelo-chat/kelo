package chat.kelo.app.agent

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** The capture field's diff (agent/RemoteTyping.kt), read from the vectors every app shares (apps/shared/test-vectors/remote-typing.json). */
@OptIn(ExperimentalCoroutinesApi::class)
class RemoteTypingTest {
    private fun vectors(): JsonArray {
        val start = File("").absoluteFile
        val file = generateSequence(start) { it.parentFile }.flatMap { sequenceOf(File(it, "test-vectors/remote-typing.json"), File(it, "shared/test-vectors/remote-typing.json")) }
            .firstOrNull { it.isFile } ?: error("apps/shared/test-vectors/remote-typing.json not found from $start")
        return Json.parseToJsonElement(file.readText()).jsonArray
    }

    private fun typed(o: JsonObject): Typed {
        val composing = o["composing"]!!.jsonPrimitive.content
        val cursor = o["cursor"]?.jsonPrimitive?.int ?: composing.length
        return Typed(o["committed"]!!.jsonPrimitive.content, composing, cursor)
    }

    private fun step(a: JsonArray): Step = when (a[0].jsonPrimitive.content) {
        "text" -> Step.Text(a[1].jsonPrimitive.content)
        "compose" -> Step.Compose(a[1].jsonPrimitive.content, a[2].jsonPrimitive.int)
        "back" -> Step.Back(a[1].jsonPrimitive.int)
        else -> Step.Key(a[1].jsonPrimitive.content)
    }

    @Test fun everyVectorGivesItsSteps() {
        val rows = vectors()
        assertTrue(rows.size >= 20)
        for (row in rows) {
            val o = row.jsonObject
            val want = o["steps"]!!.jsonArray.map { step(it.jsonArray) }
            val got = RemoteTyping.steps(typed(o["old"]!!.jsonObject), typed(o["new"]!!.jsonObject))
            assertEquals(o["name"]!!.jsonPrimitive.content, want, got)
        }
    }

    @Test fun typedTakesACompositionAtTheEndAsComposing() {
        val m = RemoteTyping.MARK
        assertEquals(Typed(m, "구", 1), RemoteTyping.typed(m + "구", 2, 2, 1, 2))
        assertEquals(Typed(m + "구"), RemoteTyping.typed(m + "구", 2, 2, null, null))
        assertEquals(Typed("ab"), RemoteTyping.typed("ab", 1, 1, 0, 1))            // a composition that does not end at the end: all committed
        assertEquals(Typed(m, "中", 0), RemoteTyping.typed(m + "中", 1, 1, 1, 2))   // the cursor is inside the composition
    }

    @Test fun theFieldStartsAgainWhenItGrowsPastSixtyFourGraphemesAndNothingComposes() {
        assertFalse(RemoteTyping.mustReset(Typed(RemoteTyping.MARK + "a".repeat(63))))
        assertTrue(RemoteTyping.mustReset(Typed(RemoteTyping.MARK + "a".repeat(64))))
        assertFalse(RemoteTyping.mustReset(Typed(RemoteTyping.MARK + "a".repeat(64), "ㄱ")))
    }

    @Test fun theEventIsTheServersEditMessageAndLongTextIsCut() {
        val e = RemoteTyping.json(listOf(Step.Compose("구", 1), Step.Back(2), Step.Text("x"), Step.Key("Enter")))
        assertEquals("""{"type":"edit","ack":true,"steps":[["compose","구",1],["back",2],["text","x"],["key","Enter"]]}""", e.toString())
        val long = RemoteTyping.events(listOf(Step.Text("a".repeat(4500))))
        assertEquals(1, long.size)
        assertEquals(3, long[0]["steps"]!!.jsonArray.size)
        assertEquals(2, RemoteTyping.events(List(65) { Step.Key("Tab") }).size)
    }

    // ---- the sender

    private val m = RemoteTyping.MARK
    private fun names(e: JsonObject) = e["steps"]!!.jsonArray.joinToString(" ") { it.jsonArray.joinToString(",") { x -> x.jsonPrimitive.content } }

    @Test fun oneKeystrokeGoesAtOnceAndABurstBecomesTwoEvents() = runTest {
        val sent = mutableListOf<JsonObject>()
        val s = RemoteTypingSender(this, 30) { sent += it }
        s.update(Typed(m, "ㄱ"))
        assertEquals(listOf("compose,ㄱ,1"), sent.map(::names))                      // at t = 0
        sent.clear()
        s.update(Typed(m, "구"))
        s.update(Typed(m, "군"))
        s.update(Typed(m + "구", "니"))
        s.update(Typed(m + "구", "닉"))
        assertTrue(sent.isEmpty())                                                   // inside the window: held
        advanceTimeBy(31)
        assertEquals(1, sent.size)                                                   // ...and one trailing event
        assertEquals(RemoteTyping.events(RemoteTyping.steps(Typed(m, "ㄱ"), Typed(m + "구", "닉"))).map(::names), sent.map(::names))
    }

    @Test fun flushKeepsTheOwnersOrderBeforeAnotherInput() = runTest {
        val sent = mutableListOf<JsonObject>()
        val s = RemoteTypingSender(this, 30) { sent += it }
        s.update(Typed(m + "a"))
        s.update(Typed(m + "ab"))                                                    // held in the window
        assertEquals(1, sent.size)
        s.flush()
        assertEquals(listOf("text,a", "text,b"), sent.map(::names))
        s.flush()
        assertEquals(2, sent.size)                                                   // nothing pending: nothing sent
    }

    @Test fun commitSendsWhatStillComposesAsText() = runTest {
        val sent = mutableListOf<JsonObject>()
        val s = RemoteTypingSender(this, 30) { sent += it }
        s.update(Typed(m, "ㄱ"))
        sent.clear()
        s.commit()
        assertEquals(listOf("text,ㄱ"), sent.map(::names))
        assertEquals(Typed(m + "ㄱ"), s.lastSent)
        sent.clear()
        s.commit()
        assertTrue(sent.isEmpty())
    }

    @Test fun resetDoesNothingWhileComposingAndStartsAgainOtherwise() = runTest {
        val s = RemoteTypingSender(this, 30) { }
        s.update(Typed(m, "ㄱ"))
        assertFalse(s.reset())
        assertEquals(Typed(m, "ㄱ"), s.lastSent)
        s.commit()
        assertTrue(s.reset())
        assertEquals(RemoteTyping.EMPTY, s.lastSent)
        s.update(Typed(m, "ㄴ"))
        s.forceReset()                                                               // a failed edit: the page is no longer known
        assertEquals(RemoteTyping.EMPTY, s.lastSent)
        assertNull(null)
    }
}
