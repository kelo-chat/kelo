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
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class CallSessionTest {

    private class FakeChat : CallChat {
        val sentFromCall = mutableListOf<Pair<String, String>>()
        val attempts = mutableListOf<String>()
        val amends = mutableListOf<Boolean>()
        val takenBack = mutableListOf<String>()
        var takeBackAnswer = true
        val followedFromCall = mutableListOf<String>()
        override val turnEvents = MutableSharedFlow<TurnEvent>(extraBufferCapacity = 64)
        override val callTurnEvents = MutableSharedFlow<CallTurnEvent>(extraBufferCapacity = 64)
        private val _busy = MutableStateFlow(false)
        override val busy: StateFlow<Boolean> = _busy.asStateFlow()
        fun setBusy(b: Boolean) { _busy.value = b }
        /** As ChatTurns.apply does: every event goes to both flows, tagged with the call that started its turn (null: a typed reply). */
        suspend fun emit(callId: String?, ev: TurnEvent) { turnEvents.emit(ev); callTurnEvents.emit(CallTurnEvent(callId, ev)) }
        override fun sendFromCall(text: String, callId: String, amend: Boolean): Boolean {
            attempts.add(text)
            if (_busy.value) return false
            sentFromCall.add(text to callId)
            amends.add(amend)
            return true
        }
        override fun takeBackFromCall(callId: String): Boolean {
            takenBack.add(callId)
            if (takeBackAnswer) _busy.value = false
            return takeBackAnswer
        }
        override fun followFromCall(turnId: String, callId: String): Boolean { followedFromCall.add(turnId); return true }
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
        var starts = 0
        override fun listenOnce(language: String, onStarted: () -> Unit, onLevel: (Float) -> Unit, onDone: (String?) -> Unit) {
            starts++
            listening = true
            lastLang = language
            doneCallback = onDone
        }
        override fun cancel() { listening = false }
    }

    private class FakeSpeechEngine : SpeechEngine {
        val spoken = mutableListOf<String>()
        var stops = 0
        var released = false
        override fun speak(text: String, language: String, onDone: () -> Unit) {
            spoken.add(text)
            onDone()
        }
        override fun stop() { stops++ }
        override fun release() { released = true }
    }

    /** A server session for the words: the greeting is answered at once, every piece is turned into text by the test (in order). */
    private fun serverSession(
        scope: CoroutineScope, chat: CallChat, audio: CallAudio, api: VoiceApi, now: () -> Long,
    ): CallSession = CallSession(
        callId = "c_srv", topic = "", fromAgent = false, scope = scope, chat = chat, api = { api }, audio = audio,
        stt = VoiceModel({ null }, scope, { "ko" }, readyStt), speech = SpeechModel({ null }, scope, readySpeech), phoneListener = null,
        device = FakeSpeechEngine(), appLang = { "ko" }, clock = now, log = {},
    )

    private fun transcribingApi(results: ArrayDeque<CompletableDeferred<String>>): VoiceApi =
        object : VoiceApi(KeloApi("http://localhost", "t")) {
            override suspend fun answered(callId: String, timezone: String?, topic: String?): String = "turn_greet"
            override suspend fun speak(text: String, lang: String, voice: Int?, speed: Double?): ByteArray = Wav.encode(ByteArray(640), 44100)
            override suspend fun transcribe(wav: ByteArray): String = results.removeFirst().await()
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
            device = device,
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
        chat.emit("call_123", TurnEvent.Text("안녕하세요! Kelo입니다."))
        chat.emit("call_123", TurnEvent.Done(1, 1, 1))
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
            device = null,
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
            device = null,
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
            device = null,
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

    private fun phoneSession(
        scope: CoroutineScope, chat: FakeChat, phone: FakePhoneListener, audio: CallAudio,
        lines: MutableList<String> = mutableListOf(), api: VoiceApi? = null, speech: SpeechView? = null,
    ) = CallSession(
        callId = "c_phone", topic = "", fromAgent = false, scope = scope, chat = chat, api = { api }, audio = audio,
        stt = VoiceModel({ null }, scope, { "ko" }, null), speech = SpeechModel({ null }, scope, speech),
        phoneListener = phone, device = FakeSpeechEngine(), appLang = { "ko" }, clock = { 1000L }, log = { lines += it },
    )

    private fun greetingApi() = object : VoiceApi(KeloApi("http://localhost", "t")) {
        override suspend fun answered(callId: String, timezone: String?, topic: String?): String = "turn_greet"
        override suspend fun speak(text: String, lang: String, voice: Int?, speed: Double?): ByteArray = Wav.encode(ByteArray(640), 44100)
    }

    @Test fun `phone ears listen on entering listening only, back off after misses, and leave the recorder closed`() = runTest {
        // 2026-11-19: the recognizer restarted after every step while listening and at once after a no-match or an error, with the
        // recorder open too: a "ding" loop on the owner's phone.
        val chat = FakeChat(); val phone = FakePhoneListener(); val audio = FakeAudio()
        val session = phoneSession(this, chat, phone, audio)
        session.accept()
        runCurrent()
        assertEquals(CallSteps.Phase.LISTENING, session.ui.value.phase)
        assertEquals(1, phone.starts)
        assertFalse("the phone's recognizer has the microphone to itself", audio.micRunning)

        fun miss(text: String?, waitMs: Long) {
            phone.doneCallback!!.invoke(text)
            runCurrent()
            val before = phone.starts
            advanceTimeBy(waitMs - 1); runCurrent()
            assertEquals("no restart before $waitMs ms", before, phone.starts)
            advanceTimeBy(2); runCurrent()
            assertEquals("restart after $waitMs ms", before + 1, phone.starts)
        }
        miss("", 1_000)
        miss(null, 2_000)
        miss(null, 4_000)
        assertFalse(session.ui.value.earsTrouble)
        miss(null, 8_000)
        assertTrue("three errors in a row say the phone isn't hearing", session.ui.value.earsTrouble)
        miss(null, 8_000)

        phone.doneCallback!!.invoke("내일 일정 알려줘")
        runCurrent()
        assertEquals(listOf("내일 일정 알려줘" to "c_phone"), chat.sentFromCall)
        assertFalse(session.ui.value.earsTrouble)
        session.hangUp(); runCurrent()
    }

    @Test fun `words heard while the chat is busy are held and sent when it is free`() = runTest {
        // 2026-11-19: the owner's words were dropped while a typed reply ran.
        val chat = FakeChat(); val phone = FakePhoneListener(); val audio = FakeAudio()
        val session = phoneSession(this, chat, phone, audio)
        session.accept(); runCurrent()
        chat.setBusy(true); runCurrent()
        phone.doneCallback!!.invoke("메일 확인해줘")
        runCurrent()
        assertEquals(listOf("메일 확인해줘"), chat.attempts)
        assertTrue(chat.sentFromCall.isEmpty())
        assertEquals(CallSteps.Phase.WAITING, session.ui.value.phase)
        chat.setBusy(false); runCurrent()
        assertEquals(listOf("메일 확인해줘" to "c_phone"), chat.sentFromCall)
        session.hangUp(); runCurrent()
    }

    @Test fun `only the call's own turn is spoken, not a typed reply or another call's`() = runTest {
        // 2026-11-19: the call spoke every turn's text, a typed reply included.
        val chat = FakeChat(); val audio = FakeAudio()
        val session = CallSession(
            callId = "c_own", topic = "", fromAgent = false, scope = this, chat = chat, api = { greetingApi() }, audio = audio,
            stt = VoiceModel({ null }, this, { "ko" }, readyStt), speech = SpeechModel({ null }, this, readySpeech),
            phoneListener = null, device = FakeSpeechEngine(), appLang = { "ko" }, clock = { 1000L }, log = {},
        )
        session.accept(); runCurrent()
        chat.emit(null, TurnEvent.Text("입력한 질문의 답이에요."))
        chat.emit(null, TurnEvent.Done(1, 1, 1))
        chat.emit("c_other", TurnEvent.Text("다른 통화의 답이에요."))
        chat.emit("c_other", TurnEvent.Done(1, 1, 1))
        runCurrent()
        assertTrue("a typed or another call's reply is not spoken", audio.played.isEmpty())
        chat.emit("c_own", TurnEvent.Text("통화의 답이에요."))
        chat.emit("c_own", TurnEvent.Done(1, 1, 1))
        runCurrent()
        assertTrue(audio.played.isNotEmpty())
        session.hangUp(); runCurrent()
    }

    @Test fun `the call log names each stage and never the words`() = runTest {
        val chat = FakeChat(); val phone = FakePhoneListener(); val audio = FakeAudio(); val lines = mutableListOf<String>()
        val session = phoneSession(this, chat, phone, audio, lines, greetingApi(), readySpeech)
        session.accept(); runCurrent()
        chat.setBusy(true); runCurrent()
        chat.emit("c_phone", TurnEvent.Text("카드 비밀번호는 4321이에요."))
        chat.emit("c_phone", TurnEvent.Done(1, 1, 1))
        runCurrent()
        chat.setBusy(false); runCurrent()
        phone.doneCallback!!.invoke("내 주민번호 알려줘")
        runCurrent()
        session.hangUp(); runCurrent()
        val log = lines.joinToString("\n")
        for (stage in listOf("ring call=", "models ", "ears=PHONE", "answered", "voice=server", "fetched", "play voice=server", "turn sent", "end reason=HUNG_UP")) {
            assertTrue("log has '$stage':\n$log", log.contains(stage))
        }
        for (words in listOf("4321", "비밀번호", "주민번호")) assertFalse("the log never has the words", log.contains(words))
    }

    @Test fun `no voice to play shows the no-voice line`() = runTest {
        val chat = FakeChat(); val audio = FakeAudio()
        val session = CallSession(
            callId = "c_mute", topic = "", fromAgent = false, scope = this, chat = chat, api = { null }, audio = audio,
            stt = VoiceModel({ null }, this, { "ko" }, readyStt), speech = SpeechModel({ null }, this, null),
            phoneListener = null, device = null, appLang = { "ko" }, clock = { 1000L }, log = {},
        )
        session.accept(); runCurrent()
        chat.emit("c_mute", TurnEvent.Text("안녕하세요."))
        chat.emit("c_mute", TurnEvent.Done(1, 1, 1))
        runCurrent()
        assertEquals(CallVoiceTrouble.NO_VOICE, session.ui.value.voiceTrouble)
        session.hangUp(); runCurrent()
    }

    @Test fun `the call's lines show the owner's words and Kelo's reply, and typed words go as a call turn`() = runTest {
        val chat = FakeChat(); val phone = FakePhoneListener(); val audio = FakeAudio()
        val session = phoneSession(this, chat, phone, audio)
        session.accept(); runCurrent()
        phone.doneCallback!!.invoke("오늘 날씨 어때?")
        runCurrent()
        chat.emit("c_phone", TurnEvent.Text("맑고 "))
        chat.emit("c_phone", TurnEvent.Text("따뜻해요."))
        chat.emit("c_phone", TurnEvent.Done(1, 1, 1))
        runCurrent()
        assertEquals(listOf(true to "오늘 날씨 어때?", false to "맑고 따뜻해요."), session.lines.value.map { it.mine to it.text })
        chat.setBusy(true); runCurrent(); chat.setBusy(false); runCurrent()
        session.sendTyped("  내일은?  ")
        runCurrent()
        assertEquals("내일은?" to "c_phone", chat.sentFromCall.last())
        assertEquals(true to "내일은?", session.lines.value.last().let { it.mine to it.text })
        session.hangUp(); runCurrent()
    }

    @Test fun `a call that starts without the microphone asks for it, and ends with its reason when refused`() = runTest {
        val noMic = object : CallAudio by FakeAudio() { override val micGranted: Boolean = false }
        val session = phoneSession(this, FakeChat(), FakePhoneListener(), noMic)
        session.accept(); runCurrent()
        assertEquals(CallSteps.Phase.PREPARING, session.ui.value.phase)
        assertEquals(CallReadiness.Need.MIC_PERMISSION, session.ui.value.need)
        session.micPermission(false); runCurrent()
        assertEquals(CallSteps.Phase.ENDED, session.ui.value.phase)
        assertEquals(CallSteps.EndReason.MIC_DENIED, session.ui.value.endedReason)
    }

    @Test fun `words heard while the first piece is turned into text go as one turn`() = runTest {   // owner 2026-11-20
        val chat = FakeChat(); val audio = FakeAudio(); var now = 10_000L
        val d1 = CompletableDeferred<String>(); val d2 = CompletableDeferred<String>()
        val session = serverSession(this, chat, audio, transcribingApi(ArrayDeque(listOf(d1, d2))), { now })
        session.accept(); session.micPermission(true); runCurrent()
        chat.setBusy(true); runCurrent(); chat.setBusy(false); runCurrent()          // the greeting ends: listening
        assertEquals(CallSteps.Phase.LISTENING, session.ui.value.phase)
        audio.micHandler!!(TestPcm.silence(400) + TestPcm.tone(800) + TestPcm.silence(1100)); runCurrent()
        audio.micHandler!!(TestPcm.tone(600)); runCurrent()                          // the owner goes on while the first piece is read
        d1.complete("나 목소리"); runCurrent()
        assertTrue(chat.sentFromCall.isEmpty())
        audio.micHandler!!(TestPcm.silence(1100)); runCurrent()
        d2.complete("너무 끊겨"); runCurrent()
        assertEquals(listOf("나 목소리 너무 끊겨" to "c_srv"), chat.sentFromCall)
        assertEquals(listOf("나 목소리 너무 끊겨"), session.lines.value.filter { it.mine }.map { it.text })
        session.hangUp(); runCurrent()
    }

    @Test fun `talking again before Kelo answers takes the first words back and sends them again with the rest`() = runTest {
        val chat = FakeChat(); val audio = FakeAudio(); var now = 10_000L
        val d1 = CompletableDeferred<String>(); val d2 = CompletableDeferred<String>()
        val session = serverSession(this, chat, audio, transcribingApi(ArrayDeque(listOf(d1, d2))), { now })
        session.accept(); session.micPermission(true); runCurrent()
        chat.setBusy(true); runCurrent(); chat.setBusy(false); runCurrent()
        audio.micHandler!!(TestPcm.silence(400) + TestPcm.tone(800) + TestPcm.silence(1100)); runCurrent()
        d1.complete("나 목소리"); runCurrent()
        assertEquals(listOf("나 목소리" to "c_srv"), chat.sentFromCall); assertEquals(listOf(false), chat.amends)
        chat.setBusy(true); runCurrent()                                             // Kelo's turn runs, nothing answered yet
        audio.micHandler!!(TestPcm.tone(600)); runCurrent()
        assertEquals(listOf("c_srv"), chat.takenBack)
        audio.micHandler!!(TestPcm.silence(1100)); runCurrent()
        d2.complete("너무 끊겨"); runCurrent()
        assertEquals("나 목소리 너무 끊겨" to "c_srv", chat.sentFromCall.last()); assertEquals(true, chat.amends.last())
        assertEquals(listOf("나 목소리 너무 끊겨"), session.lines.value.filter { it.mine }.map { it.text })
        session.hangUp(); runCurrent()
    }

    @Test fun `a granted microphone lets the call go on`() = runTest {
        val noMic = object : CallAudio by FakeAudio() { override val micGranted: Boolean = false }
        val session = phoneSession(this, FakeChat(), FakePhoneListener(), noMic)
        session.accept(); runCurrent()
        session.micPermission(true); runCurrent()
        assertEquals(CallSteps.Phase.LISTENING, session.ui.value.phase)
        session.hangUp(); runCurrent()
    }
}
