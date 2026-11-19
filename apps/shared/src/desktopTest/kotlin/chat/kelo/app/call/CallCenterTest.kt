package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.call.platform.PhoneListener
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.voice.VoiceModel
import chat.kelo.app.voice.VoiceView
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
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
        override val turnEvents = MutableSharedFlow<TurnEvent>(extraBufferCapacity = 64)
        private val _busy = MutableStateFlow(false)
        override val busy: StateFlow<Boolean> = _busy.asStateFlow()
        override fun sendFromCall(text: String, callId: String) = true
        override fun followFromCall(turnId: String) {}
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
}
