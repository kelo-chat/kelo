package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.call.platform.PhoneListener
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.core.KeloApi
import chat.kelo.app.voice.ModelView
import chat.kelo.app.voice.VoiceApi
import chat.kelo.app.voice.VoiceModel
import chat.kelo.app.voice.VoiceView
import chat.kelo.app.voice.Wav
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class CallSessionTest {

    private class FakeChat : CallChat {
        val sentFromCall = mutableListOf<Pair<String, String>>()
        val followedFromCall = mutableListOf<String>()
        override val turnEvents = MutableSharedFlow<TurnEvent>(extraBufferCapacity = 64)
        private val _busy = MutableStateFlow(false)
        override val busy: StateFlow<Boolean> = _busy.asStateFlow()

        fun setBusy(b: Boolean) { _busy.value = b }

        override fun sendFromCall(text: String, callId: String): Boolean {
            sentFromCall.add(text to callId)
            return true
        }

        override fun followFromCall(turnId: String) {
            followedFromCall.add(turnId)
        }
    }

    private class FakeAudio : CallAudio {
        override var echoCancel: Boolean = true
        override val hasSpeakerSwitch: Boolean = true
        override var speakerOn: Boolean = true
        var micRunning = false
        var micHandler: ((ByteArray) -> Unit)? = null
        val played = mutableListOf<ByteArray>()
        var stoppedPlayback = false
        var released = false

        override fun startMic(onChunk: (ByteArray) -> Unit): Boolean {
            micRunning = true
            micHandler = onChunk
            return true
        }
        override fun stopMic() { micRunning = false; micHandler = null }
        override suspend fun play(pcm16le: ByteArray, sampleRate: Int, onLevel: (Float) -> Unit) {
            played.add(pcm16le)
        }
        override fun stopPlayback() { stoppedPlayback = true }
        override fun chime(on: Boolean) {}
        override fun release() { released = true; stopMic() }
        override var onInterrupted: (() -> Unit)? = null
    }

    private class FakePhoneListener : PhoneListener {
        var listening = false
        var lastLang: String? = null
        var doneCallback: ((String?) -> Unit)? = null
        override fun listenOnce(language: String, onStarted: () -> Unit, onLevel: (Float) -> Unit, onDone: (String?) -> Unit) {
            listening = true
            lastLang = language
            doneCallback = onDone
        }
        override fun cancel() { listening = false }
    }

    private class FakeSpeechEngine : SpeechEngine {
        val spoken = mutableListOf<String>()
        override fun speak(text: String, language: String, onDone: () -> Unit) {
            spoken.add(text)
            onDone()
        }
        override fun stop() {}
    }

    private val readyStt = VoiceView(state = "ready", runtime = "ready")
    private val readySpeech = SpeechView(state = "ready", runtime = "ready", languages = listOf("ko", "en"))

    @Test fun `accept answered follow and text events spoken`() = runTest {
        val chat = FakeChat()
        val audio = FakeAudio()
        val device = FakeSpeechEngine()
        var answeredCallId: String? = null
        var transcribedWav: ByteArray? = null

        val fakeVoiceApi = object : VoiceApi(KeloApi("http://localhost", "t")) {
            override suspend fun answered(callId: String, timezone: String?, topic: String?): String {
                answeredCallId = callId
                return "turn_ans_1"
            }
            override suspend fun speak(text: String, lang: String, voice: Int?, speed: Double?): ByteArray {
                return Wav.encode(text.encodeToByteArray(), 44100)
            }
            override suspend fun transcribe(wav: ByteArray): String {
                transcribedWav = wav
                return "user spoken words"
            }
        }

        val sttModel = VoiceModel({ null }, this, { "ko" }, readyStt)
        val speechModel = SpeechModel({ null }, this, readySpeech)

        val session = CallSession(
            callId = "call_123",
            topic = "test topic",
            fromAgent = false,
            scope = this,
            chat = chat,
            api = { fakeVoiceApi },
            audio = audio,
            stt = sttModel,
            speech = speechModel,
            phoneListener = null,
            device = { device },
            appLang = { "ko" },
            clock = { 1000L },
            log = {},
        )

        // Accept
        session.accept()
        session.micPermission(true)
        session.answerSttQuestion(true)
        session.answerVoiceQuestion(true)
        runCurrent()

        assertEquals("call_123", answeredCallId)
        assertEquals(listOf("turn_ans_1"), chat.followedFromCall)
        assertTrue(audio.micRunning)

        // Text event received from agent reply
        chat.turnEvents.emit(TurnEvent.Text("안녕하세요! Kelo입니다."))
        chat.turnEvents.emit(TurnEvent.Done(1, 1, 1))
        runCurrent()

        assertTrue("Playback occurred", audio.played.isNotEmpty())
        session.hangUp()
        runCurrent()
    }

    @Test fun `background ends the call`() = runTest {
        val chat = FakeChat()
        val audio = FakeAudio()
        val sttModel = VoiceModel({ null }, this, { "ko" }, readyStt)
        val speechModel = SpeechModel({ null }, this, readySpeech)

        val session = CallSession(
            callId = "c1",
            topic = "",
            fromAgent = false,
            scope = this,
            chat = chat,
            api = { null },
            audio = audio,
            stt = sttModel,
            speech = speechModel,
            phoneListener = null,
            device = { null },
            appLang = { "ko" },
            clock = { 1000L },
            log = {},
        )

        session.accept()
        session.appBackground()
        runCurrent()

        assertEquals(CallSteps.Phase.ENDED, session.ui.value.phase)
        assertEquals(CallSteps.EndReason.BACKGROUND, session.ui.value.endedReason)
        assertTrue(audio.released)
    }

    @Test fun `quiet timeout at 5 minutes ends the call`() = runTest {
        var now = 1000L
        val chat = FakeChat()
        val audio = FakeAudio()
        val sttModel = VoiceModel({ null }, this, { "ko" }, readyStt)
        val speechModel = SpeechModel({ null }, this, readySpeech)

        val session = CallSession(
            callId = "c1",
            topic = "",
            fromAgent = false,
            scope = this,
            chat = chat,
            api = { null },
            audio = audio,
            stt = sttModel,
            speech = speechModel,
            phoneListener = null,
            device = { null },
            appLang = { "ko" },
            clock = { now },
            log = {},
        )

        session.accept()
        session.micPermission(true)
        session.answerSttQuestion(true)
        session.answerVoiceQuestion(true)
        runCurrent()

        // Advance 5 minutes (CallRules.QUIET_END_MS = 300_000)
        advanceTimeBy(CallRules.QUIET_END_MS + 1000)
        runCurrent()

        assertEquals(CallSteps.Phase.ENDED, session.ui.value.phase)
        assertEquals(CallSteps.EndReason.QUIET, session.ui.value.endedReason)
    }

    @Test fun `phone listener fallback used when STT is not ready`() = runTest {
        val chat = FakeChat()
        val audio = FakeAudio()
        val phone = FakePhoneListener()
        val sttModel = VoiceModel({ null }, this, { "ko" }, null)
        val speechModel = SpeechModel({ null }, this, null)

        val session = CallSession(
            callId = "c_phone",
            topic = "",
            fromAgent = false,
            scope = this,
            chat = chat,
            api = { null },
            audio = audio,
            stt = sttModel,
            speech = speechModel,
            phoneListener = phone,
            device = { null },
            appLang = { "ko" },
            clock = { 1000L },
            log = {},
        )

        session.accept()
        session.micPermission(true)
        session.answerSttQuestion(true)
        session.answerVoiceQuestion(true)
        runCurrent()

        // Phase is LISTENING, phone listener should be active
        assertTrue(phone.listening)
        phone.doneCallback?.invoke("recognized via phone")
        runCurrent()

        assertEquals(1, chat.sentFromCall.size)
        assertEquals("recognized via phone" to "c_phone", chat.sentFromCall[0])
        session.hangUp()
        runCurrent()
    }
}
