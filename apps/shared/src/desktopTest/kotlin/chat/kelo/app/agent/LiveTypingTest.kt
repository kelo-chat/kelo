package chat.kelo.app.agent

import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.TestCoroutineScheduler
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import org.jetbrains.compose.resources.StringResource
import org.junit.After
import org.junit.Before
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Live typing in the live view: when it is on (live_v 3), which field has focus, and how the view model keeps the owner's order. */
@OptIn(ExperimentalCoroutinesApi::class)
class LiveTypingTest {
    private val m = RemoteTyping.MARK
    private val sched = TestCoroutineScheduler()
    private val replies = MutableSharedFlow<InputReply>(extraBufferCapacity = 8)

    private inner class Source : LiveSource {
        override val frame: StateFlow<LiveFrame?> = MutableStateFlow(null)
        override val state: StateFlow<LiveState> = MutableStateFlow(LiveState())
        override val pointer: StateFlow<LivePointer?> = MutableStateFlow(null)
        override val notices: SharedFlow<StringResource> = MutableSharedFlow()
        override val replies: SharedFlow<InputReply> get() = this@LiveTypingTest.replies.asSharedFlow()
        val sent = mutableListOf<JsonObject>()
        val controls = mutableListOf<Boolean>()
        override fun start() {}
        override fun stop(giveBack: Boolean) {}
        override fun close() {}
        override suspend fun control(take: Boolean, handover: Boolean): LiveClient.ControlResult { controls += take; return LiveClient.ControlResult.Ok }
        override fun send(event: JsonObject) { sent += event }
        override fun type(text: String) { sent += LiveMessages.text(text) }
    }

    private val source = Source()
    private lateinit var vm: LiveViewModel
    private val store = ViewModelStore()

    @Before fun main() {
        Dispatchers.setMain(UnconfinedTestDispatcher(sched))
        vm = ViewModelProvider.create(store, viewModelFactory { initializer { LiveViewModel { source } } })["live-a", LiveViewModel::class]
    }

    @After fun end() { store.clear(); Dispatchers.resetMain() }

    private fun names(e: JsonObject) = if (e["type"]!!.jsonPrimitive.content == "edit")
        e["steps"]!!.jsonArray.joinToString(" ") { it.jsonArray.joinToString(",") { x -> x.jsonPrimitive.content } }
    else e["type"]!!.jsonPrimitive.content + ":" + (e["key"] ?: e["text"] ?: "")

    // ---- the rules

    @Test fun liveTypingIsOnForLiveV3InTheMetaLineOrTheFrameHeaderAndNotBefore() {
        assertFalse(LiveHeader.liveTyping(null, null))
        assertFalse(LiveHeader.liveTyping(null, FrameHeader(liveV = 2, editing = true)))
        assertTrue(LiveHeader.liveTyping(null, FrameHeader(liveV = 3)))
        assertTrue(LiveHeader.liveTyping(LiveMeta(liveV = 3), FrameHeader(liveV = 2)))
        assertFalse(LiveHeader.liveTyping(LiveMeta(liveV = 0), null))   // a meta line from before live typing
    }

    @Test fun theFocusedFieldComesFromTheMetaLineElseTheFrameHeader() {
        assertNull(LiveHeader.typing(null, FrameHeader(editing = false, kind = "number")))
        assertEquals(Typing("number", "ab12cd34"), LiveHeader.typing(null, FrameHeader(editing = true, kind = "number", field = "ab12cd34")))
        assertEquals(Typing("secret", "ff"), LiveHeader.typing(LiveMeta(editing = true, kind = "secret", field = "ff"), FrameHeader(editing = true, kind = "text")))
        assertNull(LiveHeader.typing(LiveMeta(editing = false, kind = "text"), FrameHeader(editing = true)))
    }

    @Test fun theMetaLineAndTheHeaderReadTheNewFieldsAndOldOnesWithout() {
        val j = chat.kelo.app.core.KeloApi.json
        val meta = (LiveMessages.parse("""{"type":"meta","url":"u","title":"t","editing":true,"kind":"tel","field":"12345678","live_v":3}""") as LiveIn.Meta).meta
        assertEquals(LiveMeta("u", "t", true, "tel", "12345678", 3), meta)
        val old = (LiveMessages.parse("""{"type":"meta","url":"u","title":"t","editing":true}""") as LiveIn.Meta).meta
        assertEquals(LiveMeta("u", "t", true, "text", "", 0), old)
        val head = (LiveMessages.parse("""{"type":"frame","seq":2,"editing":true,"kind":"email","field":"aa","live_v":3}""") as LiveIn.Frame).header
        assertEquals("email", head.kind); assertEquals("aa", head.field); assertEquals(3, head.liveV)
        assertTrue(j.encodeToString(FrameHeader.serializer(), FrameHeader()).isNotEmpty())
    }

    @Test fun theKeyboardFitsTheFieldAndHardwareKeysMapToPageKeys() {
        assertEquals(androidx.compose.ui.text.input.KeyboardType.Password, keyboardTypeFor("secret"))
        assertEquals(androidx.compose.ui.text.input.KeyboardType.Number, keyboardTypeFor("number"))
        assertEquals(androidx.compose.ui.text.input.KeyboardType.Phone, keyboardTypeFor("tel"))
        assertEquals(androidx.compose.ui.text.input.KeyboardType.Email, keyboardTypeFor("email"))
        assertEquals(androidx.compose.ui.text.input.KeyboardType.Uri, keyboardTypeFor("url"))
        assertEquals(androidx.compose.ui.text.input.KeyboardType.Text, keyboardTypeFor("multiline"))
        assertEquals(androidx.compose.ui.text.input.KeyboardType.Text, keyboardTypeFor(null))
        assertEquals("Tab", pageKeyName(androidx.compose.ui.input.key.Key.Tab))
        assertEquals("ArrowLeft", pageKeyName(androidx.compose.ui.input.key.Key.DirectionLeft))
        assertNull(pageKeyName(androidx.compose.ui.input.key.Key.A))
        assertNull(pageKeyName(androidx.compose.ui.input.key.Key.Backspace))   // goes through the field
    }

    // ---- the view model

    @Test fun aKeystrokeGoesAtOnceAndAnotherInputFlushesWhatIsPendingFirst() {
        vm.typingUpdate(Typed(m + "a"))
        vm.typingUpdate(Typed(m + "ab"))                       // inside the 30 ms window: held
        assertEquals(listOf("text,a"), source.sent.map(::names))
        vm.send(LiveMessages.nav("reload"))                    // another input: what is pending goes first
        assertEquals(listOf("text,a", "text,b", "reload:"), source.sent.map(::names))
    }

    @Test fun aBurstBecomesTheTrailingEventAfterTheWindow() {
        vm.typingUpdate(Typed(m, "ㄱ"))
        vm.typingUpdate(Typed(m, "구"))
        vm.typingUpdate(Typed(m, "군"))
        assertEquals(1, source.sent.size)
        sched.advanceTimeBy(31)
        assertEquals(listOf("compose,ㄱ,1", "compose,군,1"), source.sent.map(::names))
    }

    @Test fun handingBackWhileComposingCommitsWhatWasShownFirst() {
        vm.typingUpdate(Typed(m, "ㄱ"))
        source.sent.clear()
        runBlocking { vm.control(false) }
        assertEquals(listOf("text,ㄱ"), source.sent.map(::names))
        assertEquals(listOf(false), source.controls)
    }

    @Test fun namedKeysCommitWhatComposesSendTheKeysAndStartTheFieldAgain() {
        vm.typingUpdate(Typed(m, "구"))
        source.sent.clear()
        val before = vm.typingResets.value
        vm.typingKeys(listOf("Enter"))
        assertEquals(listOf("text,구", "key,Enter"), source.sent.map(::names))
        assertEquals(RemoteTyping.EMPTY, vm.typingSent)
        assertTrue(vm.typingResets.value > before)
    }

    @Test fun aPasteStartsTheFieldAgainAndAComposingOneIsCommittedBeforeIt() {
        vm.typingUpdate(Typed(m, "ㄱ"))
        source.sent.clear()
        vm.type("hello")
        assertEquals(listOf("text,ㄱ", "text:\"hello\""), source.sent.map(::names))
        assertEquals(RemoteTyping.EMPTY, vm.typingSent)
    }

    @Test fun anEditThePageDidNotTakeStartsTheFieldAgain() {
        vm.typingUpdate(Typed(m + "abc"))
        val before = vm.typingResets.value
        replies.tryEmit(InputReply(input = "edit", busy = true))
        for (i in 0 until 50) { if (vm.typingResets.value > before) break; Thread.sleep(10) }
        assertTrue(vm.typingResets.value > before)
        assertEquals(RemoteTyping.EMPTY, vm.typingSent)
    }
}
