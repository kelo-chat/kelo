package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.call.platform.PhoneListener
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.voice.LevelMeter
import chat.kelo.app.voice.LevelTrail
import chat.kelo.app.voice.VoiceApi
import chat.kelo.app.voice.VoiceModel
import chat.kelo.app.voice.Wav
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.TimeZone

data class CallUi(
    val phase: CallSteps.Phase = CallSteps.Phase.RINGING,
    val elapsedMs: Long = 0L,
    val muted: Boolean = false,
    val speakerOn: Boolean = true,
    val need: CallReadiness.Need = CallReadiness.Need.NOTHING,
    val cardWaiting: Boolean = false,
    val offline: Boolean = false,
    val ears: CallEars.Way = CallEars.Way.NONE,
    val voiceLine: Boolean = false,
    val voiceDownloadPercent: Int? = null,
    val endedReason: CallSteps.EndReason? = null,
)

/** The live state and coordinator of one call (brief §4.9). */
class CallSession(
    val callId: String,
    val topic: String,
    val fromAgent: Boolean,
    private val scope: CoroutineScope,
    private val chat: CallChat,
    private val api: () -> VoiceApi?,
    private val audio: CallAudio,
    private val stt: VoiceModel,
    private val speech: SpeechModel,
    private val phoneListener: PhoneListener?,
    private val device: () -> SpeechEngine?,
    private val appLang: () -> String,
    private val clock: () -> Long,
    private val log: (String) -> Unit,
) {
    private var state = CallSteps.State(CallSteps.Phase.RINGING)
    private val _ui = MutableStateFlow(
        CallUi(
            phase = state.phase,
            speakerOn = audio.speakerOn,
            ears = CallEars.pick(stt.view.value, phoneListener != null),
        ),
    )
    val ui: StateFlow<CallUi> = _ui.asStateFlow()

    val trail = LevelTrail()
    private val micLevelMeter = LevelMeter(16000)
    private val speechGate = SpeechGate()
    private val cutter = SentenceCutter()
    private val timings = CallTimings()

    private var micAllowed = true
    private var askedStt = false
    private var askedVoice = false
    private var deviceSpokeThisCall = false
    private var dismissedVoiceLine = false

    private var connectStartMs: Long = 0L
    private var lastUtterancePcm = ByteArray(0)
    private var lastPlaybackStopMs = 0L

    private var clockJob: Job? = null
    private var ringJob: Job? = null
    private var quietJob: Job? = null
    private var turnEventsJob: Job? = null
    private var chatBusyJob: Job? = null
    private var speechViewJob: Job? = null

    private val queue: SpeechQueue = SpeechQueue(
        scope = scope,
        audio = audio,
        server = { text, lang ->
            timings.mark(CallTimings.Step.FIRST_AUDIO, clock())
            api()?.speak(text, lang)
        },
        device = device,
        serverLang = { SpeechLang.server(it, speech.view.value) },
        appLang = appLang,
        onLevel = { lvl -> trail.push(lvl) },
        onEvent = { ev ->
            when (ev) {
                SpeechQueue.Event.FIRST_AUDIO -> {
                    timings.mark(CallTimings.Step.FIRST_AUDIO, clock())
                }
                SpeechQueue.Event.FIRST_PLAY -> {
                    timings.mark(CallTimings.Step.PLAY_START, clock())
                    log(timings.line())
                    step(CallSteps.Event.FirstSentence)
                    resetQuietTimer()
                }
                SpeechQueue.Event.IDLE -> {
                    lastPlaybackStopMs = clock()
                    step(CallSteps.Event.QueueIdle(chat.busy.value))
                    resetQuietTimer()
                }
                SpeechQueue.Event.DEVICE_SPOKE -> {
                    deviceSpokeThisCall = true
                    _ui.update { it.copy(voiceLine = CallVoiceLine.show(speech.view.value, true, dismissedVoiceLine)) }
                }
            }
        },
    )

    init {
        audio.onInterrupted = {
            step(CallSteps.Event.Interrupted)
        }
        audio.chime(true)
        ringJob = scope.launch {
            delay(CallRules.RING_MS)
            if (state.phase == CallSteps.Phase.RINGING) {
                audio.chime(false)
                step(CallSteps.Event.RingTimeout)
            }
        }

        // Listen for turn events
        turnEventsJob = scope.launch {
            chat.turnEvents.collect { ev ->
                when (ev) {
                    is TurnEvent.Text -> {
                        timings.mark(CallTimings.Step.FIRST_TEXT, clock())
                        val pieces = cutter.add(ev.text)
                        for (p in pieces) {
                            timings.mark(CallTimings.Step.FIRST_SENTENCE, clock())
                            queue.add(p)
                        }
                    }
                    is TurnEvent.Done, is TurnEvent.Error -> {
                        val rest = cutter.finish()
                        for (p in rest) queue.add(p)
                    }
                    is TurnEvent.ApprovalRequest, is TurnEvent.SecretRequested, is TurnEvent.ShippingForm,
                    is TurnEvent.LocationRequest, is TurnEvent.CardNeeded, is TurnEvent.PinNeeded -> {
                        _ui.update { it.copy(cardWaiting = true) }
                        queue.add("화면에서 확인할 게 있어요.")
                    }
                    else -> {}
                }
            }
        }

        // Listen for chat busy changes (brief line 734: chat.state.busy true -> false = ReplyEnded)
        chatBusyJob = scope.launch {
            var wasBusy = false
            chat.busy.collect { busy ->
                if (wasBusy && !busy && (state.phase == CallSteps.Phase.SPEAKING || state.phase == CallSteps.Phase.WAITING)) {
                    step(CallSteps.Event.ReplyEnded)
                }
                wasBusy = busy
            }
        }

        // Listen for speech download progress
        speechViewJob = scope.launch {
            speech.view.collect { spView ->
                val percent = spView?.progress?.let { if (it.total > 0) ((it.done * 100) / it.total).toInt() else 0 }
                _ui.update {
                    it.copy(
                        voiceDownloadPercent = percent,
                        voiceLine = CallVoiceLine.show(spView, deviceSpokeThisCall, dismissedVoiceLine),
                    )
                }
            }
        }
    }

    fun accept() {
        ringJob?.cancel()
        audio.chime(false)
        step(CallSteps.Event.Accept)
        checkReadiness()
    }

    fun decline() {
        ringJob?.cancel()
        audio.chime(false)
        step(CallSteps.Event.Decline)
    }

    fun hangUp() {
        ringJob?.cancel()
        audio.chime(false)
        step(CallSteps.Event.HangUp)
    }

    fun setMuted(on: Boolean) {
        _ui.update { it.copy(muted = on) }
    }

    fun setSpeaker(on: Boolean) {
        audio.speakerOn = on
        _ui.update { it.copy(speakerOn = on) }
    }

    fun micPermission(granted: Boolean) {
        if (!granted) {
            step(CallSteps.Event.NotPossible(CallSteps.EndReason.MIC_DENIED))
        } else {
            micAllowed = true
            checkReadiness()
        }
    }

    fun answerSttQuestion(ok: Boolean) {
        askedStt = true
        if (ok) {
            stt.choose("sensevoice-small")
        } else {
            stt.decline()
        }
        checkReadiness()
    }

    fun answerVoiceQuestion(ok: Boolean) {
        askedVoice = true
        if (ok) {
            speech.choose()
        } else {
            speech.decline()
        }
        checkReadiness()
    }

    fun getBetterVoice() {
        speech.choose()
        _ui.update { it.copy(voiceLine = false) }
    }

    fun dismissVoiceLine() {
        dismissedVoiceLine = true
        _ui.update { it.copy(voiceLine = false) }
    }

    fun appBackground() {
        step(CallSteps.Event.Background)
    }

    fun stopVoice() {
        queue.stop()
        audio.stopPlayback()
    }

    private fun checkReadiness() {
        val need = CallReadiness.next(
            stt = stt.view.value,
            voice = speech.view.value,
            micAllowed = micAllowed,
            phoneRecognizer = phoneListener != null,
            askedStt = askedStt,
            askedVoice = askedVoice,
        )
        _ui.update { it.copy(need = need) }
        when (need) {
            CallReadiness.Need.MIC_PERMISSION -> {}
            CallReadiness.Need.STT_QUESTION -> {}
            CallReadiness.Need.VOICE_QUESTION -> {}
            CallReadiness.Need.IMPOSSIBLE -> step(CallSteps.Event.NotPossible(CallSteps.EndReason.NO_STT))
            CallReadiness.Need.NOTHING -> step(CallSteps.Event.Prepared)
        }
    }

    private fun step(ev: CallSteps.Event) {
        val bargeIn = audio.echoCancel && CallRules.BARGE_IN_PHONES && phoneListener == null
        val previousState = state
        val (next, actions) = CallSteps.step(state, ev, bargeIn)
        state = next
        _ui.update {
            it.copy(
                phase = next.phase,
                endedReason = next.ended,
                ears = CallEars.pick(stt.view.value, phoneListener != null),
            )
        }

        if (next.phase == CallSteps.Phase.CONNECTING && connectStartMs == 0L) {
            connectStartMs = clock()
            startClockJob()
        }

        for (a in actions) {
            when (a) {
                CallSteps.Action.OPEN_MIC -> {
                    audio.startMic { chunk -> onMicChunk(chunk) }
                }
                CallSteps.Action.CLOSE_MIC -> {
                    audio.stopMic()
                }
                CallSteps.Action.SEND_ANSWERED -> {
                    sendAnswered()
                }
                CallSteps.Action.TRANSCRIBE -> {
                    transcribe()
                }
                CallSteps.Action.SEND_TEXT -> {
                    val textToSend = when (ev) {
                        is CallSteps.Event.Transcribed -> ev.text
                        is CallSteps.Event.ReplyEnded -> previousState.held
                        else -> next.held ?: previousState.held
                    }
                    if (!textToSend.isNullOrEmpty()) {
                        sendText(textToSend)
                    }
                }
                CallSteps.Action.HOLD_TEXT -> {}
                CallSteps.Action.STOP_PLAYBACK -> {
                    queue.stop()
                    audio.stopPlayback()
                }
                CallSteps.Action.END -> {
                    endCall()
                }
                CallSteps.Action.NONE -> {}
            }
        }

        if (next.phase == CallSteps.Phase.LISTENING) {
            startListening()
        }
    }

    private fun startListening() {
        resetQuietTimer()
        val ears = CallEars.pick(stt.view.value, phoneListener != null)
        _ui.update { it.copy(ears = ears) }
        if (ears == CallEars.Way.PHONE && phoneListener != null) {
            phoneListener.listenOnce(
                language = appLang(),
                onStarted = { step(CallSteps.Event.SpeechStarted) },
                onLevel = { lvl -> trail.push(lvl) },
                onDone = { text -> step(CallSteps.Event.Transcribed(text.orEmpty())) },
            )
        }
    }

    private fun onMicChunk(chunk: ByteArray) {
        if (_ui.value.muted) return
        val now = clock()
        if (now - lastPlaybackStopMs < CallRules.ECHO_TAIL_MS) return

        val levels = micLevelMeter.add(chunk)
        for (lvl in levels) trail.push(lvl)

        speechGate.speaking = state.phase == CallSteps.Phase.SPEAKING
        val gateEvents = speechGate.feed(chunk)
        for (gev in gateEvents) {
            when (gev) {
                SpeechGate.Out.Started -> {
                    timings.mark(CallTimings.Step.SPEECH_END, clock()) // anchor start
                    resetQuietTimer()
                    step(CallSteps.Event.SpeechStarted)
                }
                is SpeechGate.Out.Ended -> {
                    lastUtterancePcm = gev.pcm16le
                    step(CallSteps.Event.SpeechEnded(gev.pcm16le))
                }
                SpeechGate.Out.Dropped -> {
                    step(CallSteps.Event.SpeechDropped)
                }
            }
        }
    }

    private fun sendAnswered() {
        scope.launch {
            val turnId = try {
                api()?.answered(callId, TimeZone.getDefault().id, topic.ifEmpty { null })
            } catch (_: Exception) {
                null
            }
            if (!turnId.isNullOrEmpty()) {
                chat.followFromCall(turnId)
                step(CallSteps.Event.TurnStarted)
            } else {
                step(CallSteps.Event.TurnStarted)
                step(CallSteps.Event.ReplyEnded)
            }
        }
    }

    private fun transcribe() {
        val pcm = lastUtterancePcm
        scope.launch {
            try {
                val text = api()?.transcribe(Wav.encode(pcm, 16000)) ?: ""
                _ui.update { it.copy(offline = false) }
                timings.mark(CallTimings.Step.STT_DONE, clock())
                step(CallSteps.Event.Transcribed(text))
            } catch (_: Exception) {
                _ui.update { it.copy(offline = true) }
                step(CallSteps.Event.TranscribeFailed(offline = true))
            }
        }
    }

    private fun sendText(text: String) {
        timings.mark(CallTimings.Step.TURN_STARTED, clock())
        val ok = chat.sendFromCall(text, callId)
        if (!ok) {
            // Held by state machine
        }
    }

    private fun startClockJob() {
        clockJob?.cancel()
        clockJob = scope.launch {
            while (state.phase != CallSteps.Phase.ENDED) {
                delay(1000)
                val elapsed = (clock() - connectStartMs).coerceAtLeast(0L)
                _ui.update { it.copy(elapsedMs = elapsed) }
            }
        }
    }

    private fun resetQuietTimer() {
        quietJob?.cancel()
        quietJob = scope.launch {
            delay(CallRules.QUIET_END_MS)
            if (state.phase != CallSteps.Phase.ENDED) {
                step(CallSteps.Event.QuietTimeout)
            }
        }
    }

    fun release() {
        endCall()
    }

    private fun endCall() {
        ringJob?.cancel()
        clockJob?.cancel()
        quietJob?.cancel()
        turnEventsJob?.cancel()
        chatBusyJob?.cancel()
        speechViewJob?.cancel()
        phoneListener?.cancel()
        audio.chime(false)
        audio.release()
        queue.stop()
    }
}
