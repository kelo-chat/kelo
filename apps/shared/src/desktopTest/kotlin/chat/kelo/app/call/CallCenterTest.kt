package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.call.platform.PhoneListener
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.core.KeloApi
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.voice.VoiceApi
import chat.kelo.app.voice.VoiceModel
import chat.kelo.app.voice.VoiceView
import chat.kelo.app.voice.Wav
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class CallCenterTest {

    private class FakeChat : CallChat {
        val sentFromCall = mutableListOf<Pair<String, String>>()
        override val turnEvents = MutableSharedFlow<TurnEvent>(extraBufferCapacity = 64)
        override val callTurnEvents = MutableSharedFlow<CallTurnEvent>(extraBufferCapacity = 64)
        private val _busy = MutableStateFlow(false)
        override val busy: StateFlow<Boolean> = _busy.asStateFlow()
        /** As ChatTurns.apply does: every event goes to both flows, tagged with the call that started its turn (null: a typed reply). */
        suspend fun emit(callId: String?, ev: TurnEvent) { turnEvents.emit(ev); callTurnEvents.emit(CallTurnEvent(callId, ev)) }
        override fun sendFromCall(text: String, callId: String, amend: Boolean): Boolean { sentFromCall.add(text to callId); return true }
        override fun takeBackFromCall(callId: String): Boolean = false
        override fun followFromCall(turnId: String, callId: String): Boolean = true
    }

    private class FakeAudio : CallAudio {
        override var echoCancel: Boolean = true
        override val hasSpeakerSwitch: Boolean = true
        override var speakerOn: Boolean = true
        override fun startMic(onChunk: (ByteArray) -> Unit) = true
        override fun stopMic() {}
        override suspend fun play(pcm16le: ByteArray, sampleRate: Int, onLevel: (Float) -> Unit) {}
        override fun stopPlayback() {}
        override fun chime(on: Boolean) {}
        override fun release() {}
        override var onInterrupted: (() -> Unit)? = null
    }

    private val readyStt = VoiceView(state = "ready", runtime = "ready")

    @Test fun `call center responds to call offer and button`() = runTest {
        val chat = FakeChat()
        val audio = FakeAudio()
        val sttModel = VoiceModel({ null }, backgroundScope, { "ko" }, readyStt)
        val speechModel = SpeechModel({ null }, backgroundScope, null)

        val center = CallCenter(
            scope = backgroundScope,
            api = { null },
            stt = sttModel,
            speech = speechModel,
            audio = { audio },
            phoneListener = { null },
            device = { null },
            appLang = { "ko" },
            clock = { 1000L },
            log = {},
            hasMic = { true },
        )

        assertTrue(center.available.value)

        val handle = center.attach(chat)
        runCurrent()
        assertNull(center.session.value)

        // Agent offers a call
        chat.turnEvents.emit(TurnEvent.CallOffer("call_offer_99", "agent topic"))
        runCurrent()

        val sess = center.session.value
        assertNotNull(sess)
        assertEquals("call_offer_99", sess?.callId)
        assertEquals(CallSteps.Phase.RINGING, sess?.ui?.value?.phase)

        // Minimize call
        assertFalse(center.isMinimized.value)
        center.minimized(true)
        assertTrue(center.isMinimized.value)
        assertTrue(sess!!.ui.value.muted)

        center.minimized(false)
        assertFalse(center.isMinimized.value)

        sess.hangUp()
        runCurrent()
        handle.close()
    }

    private class LoadingStt(scope: CoroutineScope, private val after: VoiceView) : VoiceModel({ null }, scope, { "ko" }) {
        private val v = MutableStateFlow<VoiceView?>(null)
        override val view: StateFlow<VoiceView?> = v
        var loads = 0
        override suspend fun load() { loads++; delay(5); v.value = after }
    }
    private class LoadingSpeech(scope: CoroutineScope, private val after: SpeechView) : SpeechModel({ null }, scope) {
        private val v = MutableStateFlow<SpeechView?>(null)
        override val view: StateFlow<SpeechView?> = v
        var loads = 0
        override suspend fun load() { loads++; delay(5); v.value = after }
    }
    private class RecordingAudio : CallAudio {
        override var echoCancel: Boolean = true
        override val hasSpeakerSwitch: Boolean = true
        override var speakerOn: Boolean = true
        var micStarted = false
        val played = mutableListOf<ByteArray>()
        override fun startMic(onChunk: (ByteArray) -> Unit): Boolean { micStarted = true; return true }
        override fun stopMic() {}
        override suspend fun play(pcm16le: ByteArray, sampleRate: Int, onLevel: (Float) -> Unit) { played.add(pcm16le) }
        override fun stopPlayback() {}
        override fun chime(on: Boolean) {}
        override fun release() {}
        override var onInterrupted: (() -> Unit)? = null
    }
    private class CountingEngine : SpeechEngine {
        val spoken = mutableListOf<String>()
        var stops = 0
        var released = false
        override fun speak(text: String, language: String, onDone: () -> Unit) { spoken.add(text); onDone() }
        override fun stop() { stops++ }
        override fun release() { released = true }
    }
    private class SilentPhone : PhoneListener {
        override fun listenOnce(language: String, onStarted: () -> Unit, onLevel: (Float) -> Unit, onDone: (String?) -> Unit) {}
        override fun cancel() {}
    }

    @Test fun `a call reads the server's voice models and then uses server ears and Kelo's own voice`() = runTest {
        // 2026-11-19 (owner's phone): the call's models were never read, so every call ran on the phone's recognizer and the device
        // voice even with both server models ready.
        val stt = LoadingStt(backgroundScope, VoiceView(state = "ready", runtime = "ready"))
        val speech = LoadingSpeech(backgroundScope, SpeechView(state = "ready", runtime = "ready", languages = listOf("ko")))
        val audio = RecordingAudio(); val engine = CountingEngine(); val chat = FakeChat()
        val api = object : VoiceApi(KeloApi("http://localhost", "t")) {
            override suspend fun answered(callId: String, timezone: String?, topic: String?): String = "turn_greet"
            override suspend fun speak(text: String, lang: String, voice: Int?, speed: Double?): ByteArray { delay(20); return Wav.encode(ByteArray(640), 44100) }
        }
        val center = CallCenter(
            scope = backgroundScope, api = { api }, stt = stt, speech = speech, audio = { audio }, phoneListener = { SilentPhone() },
            device = { engine }, appLang = { "ko" }, clock = { 1000L }, log = {}, hasMic = { true },
        )
        val handle = center.attach(chat)
        center.ringFromButton()
        val session = center.session.value!!
        session.accept()
        advanceTimeBy(100); runCurrent()
        assertTrue(stt.loads >= 1); assertTrue(speech.loads >= 1)
        assertEquals(CallEars.Way.SERVER, session.ui.value.ears)
        assertTrue("the server's ears use the recorder", audio.micStarted)
        chat.emit(session.callId, TurnEvent.Text("안녕하세요. 반가워요."))
        chat.emit(session.callId, TurnEvent.Done(1, 1, 1))
        advanceTimeBy(500); runCurrent()
        assertTrue("Kelo's own voice played", audio.played.isNotEmpty())
        assertTrue("the device voice did not speak", engine.spoken.isEmpty())
        session.hangUp(); runCurrent()
        handle.close()
    }

    @Test fun `a call uses one device voice and hanging up stops and releases that one`() = runTest {
        // 2026-11-19: a new TextToSpeech per piece; stop() stopped a different engine than the one speaking.
        var made = 0
        val engine = CountingEngine(); val chat = FakeChat(); val audio = RecordingAudio()
        val center = CallCenter(
            scope = backgroundScope, api = { null }, stt = VoiceModel({ null }, backgroundScope, { "ko" }, readyStt),
            speech = SpeechModel({ null }, backgroundScope, null), audio = { audio }, phoneListener = { null },
            device = { made++; engine }, appLang = { "ko" }, clock = { 1000L }, log = {}, hasMic = { true },
        )
        val handle = center.attach(chat)
        center.ringFromButton()
        val session = center.session.value!!
        session.accept(); advanceTimeBy(100); runCurrent()
        chat.emit(session.callId, TurnEvent.Text("첫 문장이에요. 둘째 문장이에요. 셋째 문장이에요."))
        chat.emit(session.callId, TurnEvent.Done(1, 1, 1))
        advanceTimeBy(500); runCurrent()
        assertTrue(engine.spoken.isNotEmpty())
        session.hangUp(); runCurrent()
        assertEquals("one device voice per call", 1, made)
        assertTrue("hanging up stops the voice that speaks", engine.stops >= 1)
        assertTrue(engine.released)
        handle.close()
    }
}
