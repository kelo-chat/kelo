package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.call.platform.PhoneListener
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.voice.VoiceApi
import chat.kelo.app.voice.VoiceModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * Owns the active [CallSession] for the signed-in app (Android, desktop, iPhone) (brief §4.9).
 * One per signed-in app.
 */
class CallCenter(
    private val scope: CoroutineScope,
    private val api: () -> VoiceApi?,
    private val stt: VoiceModel,
    private val speech: SpeechModel,
    private val audio: () -> CallAudio?,
    private val phoneListener: () -> PhoneListener?,
    /** Makes the device voice for ONE call (called once per call). */
    private val device: () -> SpeechEngine?,
    private val appLang: () -> String,
    private val clock: () -> Long = { System.currentTimeMillis() },
    /** The call's log lines (stages and timings, never the owner's words or Kelo's reply). */
    private val log: (String) -> Unit = {},
    private val hasMic: () -> Boolean = { true },
) {
    private val _session = MutableStateFlow<CallSession?>(null)
    val session: StateFlow<CallSession?> = _session.asStateFlow()

    private val _isMinimized = MutableStateFlow(false)
    val isMinimized: StateFlow<Boolean> = _isMinimized.asStateFlow()

    private var activeChat: CallChat? = null

    val available: StateFlow<Boolean> = stt.view
        .map { sttView -> CallAvailability.of(sttView, hasMic(), phoneListener() != null) }
        .stateIn(scope, SharingStarted.Eagerly, CallAvailability.of(stt.view.value, hasMic(), phoneListener() != null))

    fun attach(chat: CallChat): AutoCloseable {
        activeChat = chat
        stt.refresh(); speech.refresh()   // the call button and the next call read the server's voice models
        val job = scope.launch {
            chat.turnEvents.collect { ev ->
                if (ev is TurnEvent.CallOffer) {
                    ring(ev.callId, ev.topic, fromAgent = true, chat = chat)
                }
            }
        }
        return AutoCloseable {
            job.cancel()
            if (activeChat === chat) activeChat = null
        }
    }

    fun ringFromButton() {
        val chat = activeChat ?: return
        val callId = UUID.randomUUID().toString()
        ring(callId, topic = "", fromAgent = false, chat = chat)
        if (!CallRules.RING_ON_BUTTON) {
            _session.value?.accept()
        }
    }

    fun ring(callId: String, topic: String, fromAgent: Boolean, chat: CallChat) {
        if (_session.value != null && _session.value?.ui?.value?.phase != CallSteps.Phase.ENDED) {
            return // Ignore second ring while a call is active
        }
        val audioInstance = audio() ?: run { log("ring: no audio on this device"); return }
        val listener = phoneListener()
        val engine = device()
        _isMinimized.value = false

        val sess = CallSession(
            callId = callId,
            topic = topic,
            fromAgent = fromAgent,
            scope = scope,
            chat = chat,
            api = api,
            audio = audioInstance,
            stt = stt,
            speech = speech,
            phoneListener = listener,
            device = engine,
            appLang = appLang,
            clock = clock,
            log = log,
        )
        _session.value = sess
    }

    fun minimized(on: Boolean) {
        _isMinimized.value = on
        if (CallRules.MUTE_WHEN_MINIMIZED) _session.value?.setMuted(on)
    }

    fun clear() {
        _session.value = null
        _isMinimized.value = false
    }
}
