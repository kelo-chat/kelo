package chat.kelo.app.agent

import chat.kelo.app.ui.platform.graphemeStarts
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * The capture field as the page should have it: the committed text (it starts with [RemoteTyping.MARK]) and the composition the
 * keyboard still shows after it. [cursor] counts UTF-16 units inside [composing].
 */
data class Typed(val committed: String, val composing: String = "", val cursor: Int = composing.length)

/** One thing to do to the page's focused field (the server's `edit` steps). */
sealed interface Step {
    data class Text(val s: String) : Step
    data class Compose(val s: String, val cursor: Int) : Step
    data class Back(val n: Int) : Step
    data class Key(val name: String) : Step
}

/**
 * Turns what the owner's keyboard did to a hidden capture field into the steps that make the page's field show the same: a letter, a
 * Korean syllable forming (ㄱ -> 구 -> 군), a deletion, a replacement, Enter. Pure; the iPhone app has the same logic (the vectors in
 * apps/shared/test-vectors/remote-typing.json are read by both).
 */
object RemoteTyping {
    /** A zero-width space the field never loses, so Backspace on an empty-looking field still changes it (and reaches the page). */
    const val MARK = "​"
    val EMPTY = Typed(MARK)

    /** Most characters of one `text` step (the server's MAX_TEXT) and steps in one `edit` event. */
    const val MAX_TEXT = 2000
    const val MAX_STEPS = 64

    /** More characters than this (not composing): the field starts again from [EMPTY] (no steps). */
    const val MAX_KEPT = 64

    /** The field's text, selection and composition as a [Typed]: a composition that ends at the end of the text is [Typed.composing]. */
    fun typed(text: String, selStart: Int, selEnd: Int, compStart: Int?, compEnd: Int?): Typed {
        if (compStart != null && compEnd != null && compStart in 0..text.length && compEnd == text.length && compStart < compEnd) {
            val composing = text.substring(compStart)
            return Typed(text.substring(0, compStart), composing, (selEnd - compStart).coerceIn(0, composing.length))
        }
        return Typed(text)
    }

    /** The steps that turn the page's field from [old] into [new]; empty = nothing to send. */
    fun steps(old: Typed, new: Typed): List<Step> {
        val out = mutableListOf<Step>()
        var composing = old.composing.isNotEmpty()
        var p = 0
        val limit = minOf(old.committed.length, new.committed.length)
        while (p < limit && old.committed[p] == new.committed[p]) p++
        val oldStarts = graphemeStarts(old.committed).toSet() + old.committed.length
        val newStarts = graphemeStarts(new.committed).toSet() + new.committed.length
        while (p > 0 && !(p in oldStarts && p in newStarts)) p--   // never cut a grapheme in the middle
        val back = graphemeStarts(old.committed).count { it >= p }
        val ins = new.committed.substring(p)
        if (back > 0) {
            if (composing) out += Step.Compose("", 0)
            composing = false
            out += Step.Back(back)
        }
        var piece = StringBuilder()
        fun flushText() {
            if (piece.isNotEmpty()) { out += Step.Text(piece.toString()); piece = StringBuilder(); composing = false }
        }
        for (ch in ins) {
            if (ch == '\n' || ch == '\t') {
                flushText()
                if (composing) { out += Step.Text(old.composing); composing = false }
                out += Step.Key(if (ch == '\n') "Enter" else "Tab")
            } else piece.append(ch)
        }
        flushText()
        val shown = if (composing) old.composing else ""
        val shownCursor = if (composing) old.cursor else 0
        if (new.composing != shown || (new.composing.isNotEmpty() && new.cursor != shownCursor)) {
            out += Step.Compose(new.composing, if (new.composing.isEmpty()) 0 else new.cursor)
        }
        return merge(out)
    }

    private fun merge(steps: List<Step>): List<Step> {
        val out = mutableListOf<Step>()
        for (s in steps) {
            val last = out.lastOrNull()
            when {
                s is Step.Back && last is Step.Back -> out[out.lastIndex] = Step.Back(last.n + s.n)
                s is Step.Text && last is Step.Text -> out[out.lastIndex] = Step.Text(last.s + s.s)
                else -> out += s
            }
        }
        return out
    }

    /** True when the field has grown past [MAX_KEPT] graphemes and nothing is composing: it should start again from [EMPTY]. */
    fun mustReset(t: Typed): Boolean = t.composing.isEmpty() && graphemeStarts(t.committed).size > MAX_KEPT

    /** The `edit` events for [steps]: a long text is cut at [MAX_TEXT] and an event holds at most [MAX_STEPS] steps. */
    fun events(steps: List<Step>): List<JsonObject> {
        val flat = steps.flatMap { s ->
            if (s is Step.Text && s.s.length > MAX_TEXT) s.s.chunked(MAX_TEXT).map { Step.Text(it) } else listOf(s)
        }
        return flat.chunked(MAX_STEPS).map { json(it) }
    }

    /** One `edit` event: {"type":"edit","steps":[["compose","구",1],["back",2],["text","x"],["key","Enter"]],"ack":true} (the answer says when the page was busy). */
    fun json(steps: List<Step>): JsonObject = buildJsonObject {
        put("type", "edit")
        put("ack", true)
        put("steps", JsonArray(steps.map { s ->
            when (s) {
                is Step.Text -> JsonArray(listOf(JsonPrimitive("text"), JsonPrimitive(s.s)))
                is Step.Compose -> JsonArray(listOf(JsonPrimitive("compose"), JsonPrimitive(s.s), JsonPrimitive(s.cursor)))
                is Step.Back -> JsonArray(listOf(JsonPrimitive("back"), JsonPrimitive(s.n)))
                is Step.Key -> JsonArray(listOf(JsonPrimitive("key"), JsonPrimitive(s.name)))
            }
        }))
    }
}

/**
 * Sends what the capture field changes, in the owner's order: a keystroke goes at once (leading edge); changes inside [windowMs] after
 * it are merged into one trailing event (an IME that replaces several characters, a paste). Typed text lives only in [lastSent] and the
 * event on its way: never logged.
 */
class RemoteTypingSender(private val scope: CoroutineScope, private val windowMs: Long = 30, private val send: (JsonObject) -> Unit) {
    /** What the page has by now. */
    var lastSent: Typed = RemoteTyping.EMPTY
        private set
    private var pending: Typed? = null
    private var window: Job? = null

    fun update(t: Typed) {
        if (window?.isActive == true) { pending = t; return }
        emit(t)
        window = scope.launch {
            delay(windowMs)
            val p = pending
            pending = null
            if (p != null) emit(p)
        }
    }

    private fun emit(t: Typed) {
        val steps = RemoteTyping.steps(lastSent, t)
        lastSent = t
        for (e in RemoteTyping.events(steps)) send(e)
    }

    /** Sends what is pending now. Called before any other input the app sends, so the socket keeps the owner's order. */
    fun flush() {
        val p = pending ?: return
        pending = null
        emit(p)
    }

    /** Flushes, then commits what still composes (the page keeps what the owner saw): on hand-back, tab switch, page close. */
    fun commit() {
        flush()
        if (lastSent.composing.isNotEmpty()) {
            val t = lastSent
            lastSent = Typed(t.committed + t.composing)
            send(RemoteTyping.json(listOf(Step.Text(t.composing))))
        }
    }

    /** The field starts again from [RemoteTyping.EMPTY] with no steps (focus moved, a key was sent by hand, a paste...). False while composing. */
    fun reset(): Boolean {
        if (lastSent.composing.isNotEmpty() || pending?.composing?.isNotEmpty() == true) return false
        pending = null
        lastSent = RemoteTyping.EMPTY
        return true
    }

    /** A reset that also drops a composition (a failed `edit`: what the page has is no longer known). */
    fun forceReset() {
        pending = null
        lastSent = RemoteTyping.EMPTY
    }
}
