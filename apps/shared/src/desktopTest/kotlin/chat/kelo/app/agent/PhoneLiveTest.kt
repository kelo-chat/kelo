package chat.kelo.app.agent

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.jetbrains.compose.resources.StringResource
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** The live view of the Android device (the key "android"): a portrait screen, Back / Home / Recents, take control / give back. */
class PhoneLiveTest {
    private class FakeSource : LiveSource {
        override val frame: StateFlow<LiveFrame?> = MutableStateFlow(null)
        override val state: StateFlow<LiveState> = MutableStateFlow(LiveState())
        override val pointer: StateFlow<LivePointer?> = MutableStateFlow(null)
        override val notices: SharedFlow<StringResource> = MutableSharedFlow()
        val sent = mutableListOf<JsonObject>()
        val takes = mutableListOf<Boolean>()
        override fun start() {}
        override fun stop(giveBack: Boolean) {}
        override fun close() {}
        override suspend fun control(take: Boolean): LiveClient.ControlResult { takes += take; return LiveClient.ControlResult.Ok }
        override fun send(event: JsonObject) { sent += event }
        override fun type(text: String) {}
    }

    @Test fun onlyTheKeyAndroidIsTheDevice() {
        assertTrue(LiveKinds.isDevice("android"))
        assertFalse(LiveKinds.isDevice("owner")); assertFalse(LiveKinds.isDevice("task:w1")); assertFalse(LiveKinds.isDevice("3"))
    }

    @Test fun theThreeKeysAreSentAsKeyEventsInTheServersWords() {
        assertEquals(listOf("back", "home", "recents"), LiveKinds.PHONE_KEYS)
        val source = FakeSource()
        val vm = LiveViewModel { source }
        LiveKinds.PHONE_KEYS.forEach { vm.send(LiveMessages.deviceKey(it)) }
        assertEquals(listOf("back", "home", "recents"), source.sent.map { (it["key"] as JsonPrimitive).content })
        assertTrue(source.sent.all { (it["type"] as JsonPrimitive).content == "key" })
    }

    @Test fun aDragIsOneSwipeInFramePixelsWithItsDuration() {
        val m = LiveMessages.swipe(FramePoint(180, 600), FramePoint(180, 100), 360, 780, 250)
        assertEquals("swipe", (m["type"] as JsonPrimitive).content)
        assertEquals(listOf("180", "600", "180", "100", "250", "360", "780"),
            listOf("x1", "y1", "x2", "y2", "ms", "frame_w", "frame_h").map { (m[it] as JsonPrimitive).content })
        assertEquals(120, LiveKinds.swipeMs(5)); assertEquals(300, LiveKinds.swipeMs(300)); assertEquals(800, LiveKinds.swipeMs(9_000))
    }

    @Test fun aTapOnThePortraitFrameIsMappedThroughTheLetterbox() {
        // a 360 x 780 frame in a 400 x 800 view: scaled 1.0256, centred horizontally
        val p = LiveGeometry.toFrame(200f, 400f, 400f, 800f, 360, 780)!!
        assertEquals(FramePoint(180, 390), p)
        val tap = LiveMessages.tap(p, 360, 780)
        assertEquals("180", (tap["x"] as JsonPrimitive).content); assertEquals("360", (tap["frame_w"] as JsonPrimitive).content)
    }

    @Test fun takeControlAndGiveBackGoThroughTheView() = runBlocking {
        val source = FakeSource()
        val vm = LiveViewModel { source }
        assertEquals(LiveClient.ControlResult.Ok, vm.control(true))
        assertEquals(LiveClient.ControlResult.Ok, vm.control(false))
        assertEquals(listOf(true, false), source.takes)
    }
}
