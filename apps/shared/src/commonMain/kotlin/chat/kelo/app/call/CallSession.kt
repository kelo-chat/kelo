package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.call.platform.PhoneListener
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.voice.LevelMeter
import chat.kelo.app.voice.LevelTrail
import chat.kelo.app.voice.VoiceApi
import chat.kelo.app.voice.VoiceModel
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.loadStringIn
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
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
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
    val earsTrouble: Boolean = false,              // the phone's recognizer failed PHONE_ERRORS_NOTE times in a row
    val voiceTrouble: CallVoiceTrouble? = null,    // Kelo's voice could not be played as planned (the screen says so)
)

/**
 * One line of the call on the call screen: the owner's words ([mine]) or Kelo's reply. The same words go to the chat as messages with
 * via=call; this list only shows them for the call's lifetime and is never stored (2026-11-19, owner).
 */
data class CallLine(val id: Int, val mine: Boolean, val text: String)

/** The phases in which the chat going free ends the reply that held the owner's words (CallSession.chatBusyJob). */
private val LIVE_PHASES = setOf(
    CallSteps.Phase.SPEAKING, CallSteps.Phase.WAITING, CallSteps.Phase.LISTENING, CallSteps.Phase.HEARING, CallSteps.Phase.TRANSCRIBING,
)

/** The live state and coordinator of one call (brief call-mode §4.9; fixes of brief call-silent, 2026-11-19). */
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
    private val device: SpeechEngine?,          // the call's one device voice (released when the call ends)
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

    private var micAllowed = audio.micGranted
    private var askedStt = false
    private var askedVoice = false
    private var deviceSpokeThisCall = false
    private var dismissedVoiceLine = false

    private var connectStartMs: Long = 0L
    private var lastUtterancePcm = ByteArray(0)
    private var lastPlaybackStopMs = 0L
    private var micOpen = false               // the recorder runs (server ears only)
    private var phoneMisses = 0               // phone recognizer tries in a row that heard nothing (back-off)
    private var phoneErrors = 0               // of those, the ones that failed
    private var lastEars: CallEars.Way? = null
    private var resending = false
    private val transcribing = Mutex()   // one piece at a time, in the order spoken

    private val _lines = MutableStateFlow<List<CallLine>>(emptyList())
    val lines: StateFlow<List<CallLine>> = _lines.asStateFlow()
    private var nextLineId = 0
    private var keloOpen = false

    private var modelsJob: Job? = null
    private var phoneJob: Job? = null

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
        onEvent = { ev -> onQueueEvent(ev) },
        log = log,
        clock = clock,
    )

    init {
        log("ring call=${callId.take(8)} from_agent=$fromAgent")
        audio.onInterrupted = {
            log("audio interrupted")
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

        // The server's voice models are read for every call (2026-11-19: the call's own model views were never read).
        val readFrom = clock()
        modelsJob = scope.launch {
            val ears = launch { quietly("stt models") { stt.load() } }
            val voice = launch { quietly("speech models") { speech.load() } }
            ears.join(); voice.join()
            log("models stt=${sttLine()} speech=${speechLine()} ms=${clock() - readFrom}")
        }

        // Only this call's turns are spoken: a typed reply or another call's reply is not (2026-11-19).
        turnEventsJob = scope.launch {
            chat.callTurnEvents.collect { e -> if (e.callId == callId) { if (e.questionOpen) onQuestionOpened() else onTurnEvent(e.event) } }
        }

        // The chat freed up (a reply ended, a question closed): the reply ended, and held words go out.
        chatBusyJob = scope.launch {
            var wasBusy = false
            chat.busy.collect { busy ->
                val phase = state.phase
                if (!busy && (wasBusy || state.held != null || state.again != null || state.replyBusy) && phase in LIVE_PHASES) {
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

    private fun onTurnEvent(ev: TurnEvent) {
        if (CallRules.isAnswer(ev)) step(CallSteps.Event.Answered)
        when (ev) {
            is TurnEvent.Text -> {
                addKeloText(ev.text)
                timings.mark(CallTimings.Step.FIRST_TEXT, clock())
                val pieces = cutter.add(ev.text)
                for (p in pieces) {
                    timings.mark(CallTimings.Step.FIRST_SENTENCE, clock())
                    queue.add(p)
                }
            }
            is TurnEvent.Done, is TurnEvent.Error -> {
                closeKeloLine()
                val rest = cutter.finish()
                for (p in rest) queue.add(p)
            }
            is TurnEvent.ApprovalRequest, is TurnEvent.SecretRequested, is TurnEvent.ShippingForm,
            is TurnEvent.LocationRequest, is TurnEvent.CardNeeded, is TurnEvent.PinNeeded -> {
                _ui.update { it.copy(cardWaiting = true) }
                speakCardWaiting()
            }
            else -> {}
        }
    }

    /** A secret or consent question opened during the call's turn: the card line once, then back to listening (2026-11-19). */
    private fun onQuestionOpened() {
        _ui.update { it.copy(cardWaiting = true) }
        speakCardWaiting()
        step(CallSteps.Event.QuestionOpened)
    }

    /** The card line is spoken in the app's language (call_card_waiting, 20 locales). */
    private fun speakCardWaiting() {
        scope.launch { queue.add(loadStringIn(appLang(), Res.string.call_card_waiting)) }
    }

    private fun onQueueEvent(ev: SpeechQueue.Event) {
        when (ev) {
            SpeechQueue.Event.FIRST_AUDIO -> timings.mark(CallTimings.Step.FIRST_AUDIO, clock())
            SpeechQueue.Event.FIRST_PLAY -> {
                timings.mark(CallTimings.Step.PLAY_START, clock())
                log("first piece playing ${timings.line()}")
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
            SpeechQueue.Event.VOICE_FALLBACK -> _ui.update { if (it.voiceTrouble == null) it.copy(voiceTrouble = CallVoiceTrouble.DEVICE_VOICE) else it }
            SpeechQueue.Event.SILENT -> _ui.update { it.copy(voiceTrouble = CallVoiceTrouble.NO_VOICE) }
        }
    }

    fun accept() {
        ringJob?.cancel()
        audio.chime(false)
        step(CallSteps.Event.Accept)
        val loading = modelsJob
        if (loading == null || loading.isCompleted || (stt.view.value != null && speech.view.value != null)) {
            checkReadiness()
        } else {
            // the first call after start: wait (at most MODELS_WAIT_MS) for the server's models before choosing ears and voice
            scope.launch {
                withTimeoutOrNull(CallRules.MODELS_WAIT_MS) { loading.join() }
                checkReadiness()
            }
        }
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
        if (state.phase != CallSteps.Phase.PREPARING) return
        val need = CallReadiness.next(
            stt = stt.view.value,
            voice = speech.view.value,
            micAllowed = micAllowed,
            phoneRecognizer = phoneListener != null,
            askedStt = askedStt,
            askedVoice = askedVoice,
        )
        log("need=$need stt=${sttLine()} speech=${speechLine()}")
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
        if (next.phase != previousState.phase) {
            log("phase ${previousState.phase}->${next.phase}")
            if (next.phase == CallSteps.Phase.ENDED) log("end reason=${next.ended}")
        }

        if (next.phase == CallSteps.Phase.CONNECTING && connectStartMs == 0L) {
            connectStartMs = clock()
            startClockJob()
        }

        for (a in actions) {
            when (a) {
                CallSteps.Action.OPEN_MIC -> openEars()
                CallSteps.Action.CLOSE_MIC -> { audio.stopMic(); micOpen = false }
                CallSteps.Action.SEND_ANSWERED -> sendAnswered()
                CallSteps.Action.TRANSCRIBE -> transcribe((ev as? CallSteps.Event.SpeechEnded)?.pcm ?: lastUtterancePcm)
                CallSteps.Action.SEND_TEXT -> next.sent?.let { sendText(it) }
                CallSteps.Action.HOLD_TEXT -> {
                    log("words held until the reply ends")
                    if (!chat.busy.value && !resending) {   // the chat is already free: the held words go now
                        resending = true
                        try { step(CallSteps.Event.ReplyEnded) } finally { resending = false }
                    }
                }
                CallSteps.Action.TAKE_BACK ->
                    if (chat.takeBackFromCall(callId)) log("unanswered words taken back")
                    else { log("take back refused"); step(CallSteps.Event.TakeBackRefused) }
                CallSteps.Action.STOP_PLAYBACK -> { queue.stop(); audio.stopPlayback() }
                CallSteps.Action.END -> endCall()
                CallSteps.Action.NONE -> {}
            }
        }
        // The phone's recognizer starts on ENTERING listening only (2026-11-19: it restarted after every step: a "ding" loop).
        if (next.phase == CallSteps.Phase.LISTENING && previousState.phase != CallSteps.Phase.LISTENING) startListening()
        if (next.phase != CallSteps.Phase.LISTENING && next.phase != CallSteps.Phase.HEARING) { phoneJob?.cancel(); phoneJob = null }
    }

    private fun startListening() {
        resetQuietTimer()
        val ears = CallEars.pick(stt.view.value, phoneListener != null)
        _ui.update { it.copy(ears = ears) }
        if (ears != lastEars) { log("ears=$ears"); lastEars = ears }
        openEars()
        if (ears == CallEars.Way.PHONE) listenByPhone()
    }

    /** The recorder runs only for the server's ears: the phone's recognizer needs the microphone to itself (C9, 2026-11-19). */
    private fun openEars() {
        val ears = CallEars.pick(stt.view.value, phoneListener != null)
        if (ears == CallEars.Way.SERVER && !micOpen) {
            // chunks come on the recorder's thread: the session runs them on its own scope, one after another
            micOpen = audio.startMic { chunk -> scope.launch { onMicChunk(chunk) } }
            if (!micOpen) log("mic failed to open")
        } else if (ears != CallEars.Way.SERVER && micOpen) {
            audio.stopMic()
            micOpen = false
        }
    }

    /** Starts the phone's recognizer now, or after the back-off when the last tries heard nothing ([CallRules.phoneRetryWait]). */
    private fun listenByPhone() {
        val listener = phoneListener ?: return
        phoneJob?.cancel()
        val wait = CallRules.phoneRetryWait(phoneMisses)
        phoneJob = scope.launch {
            if (wait > 0) delay(wait)
            if (state.phase != CallSteps.Phase.LISTENING) return@launch
            listener.listenOnce(
                language = appLang(),
                onStarted = { step(CallSteps.Event.SpeechStarted) },
                onLevel = { lvl -> trail.push(lvl) },
                onDone = { text -> onPhoneHeard(text) },
            )
        }
    }

    private fun onPhoneHeard(text: String?) {
        if (state.phase != CallSteps.Phase.LISTENING && state.phase != CallSteps.Phase.HEARING) return
        val words = text?.trim().orEmpty()
        if (words.isEmpty() || _ui.value.muted) {
            phoneMisses++
            if (text == null) phoneErrors++
            if (phoneErrors >= CallRules.PHONE_ERRORS_NOTE) _ui.update { it.copy(earsTrouble = true) }
            log("phone ears: ${if (text == null) "error" else "nothing heard"} misses=$phoneMisses next_in_ms=${CallRules.phoneRetryWait(phoneMisses)}")
            // from HEARING back to listening is an entry (startListening applies the back-off); still listening: listen again here
            if (state.phase == CallSteps.Phase.HEARING) step(CallSteps.Event.Transcribed("")) else listenByPhone()
            return
        }
        phoneMisses = 0
        phoneErrors = 0
        _ui.update { it.copy(earsTrouble = false) }
        timings.mark(CallTimings.Step.STT_DONE, clock())
        log("phone ears: heard=true")
        addMineLine(words, join = state.held != null || state.again != null)
        step(CallSteps.Event.Transcribed(words))
    }

    private fun sendAnswered() {
        scope.launch {
            val turnId = try {
                api()?.answered(callId, TimeZone.getDefault().id, topic.ifEmpty { null })
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                log("answered failed: ${CallLog.why(e)}")
                null
            }
            if (!turnId.isNullOrEmpty() && chat.followFromCall(turnId, callId)) {
                log("answered: following the greeting")
                step(CallSteps.Event.TurnStarted)
            } else {
                log(if (turnId.isNullOrEmpty()) "answered: no greeting" else "answered: chat busy, greeting not followed")
                step(CallSteps.Event.TurnStarted)
                step(CallSteps.Event.ReplyEnded)
            }
        }
    }

    /** Turns one piece of the owner's speech into text. Pieces run one at a time, in the order spoken (owner 2026-11-20). */
    private fun transcribe(pcm: ByteArray) {
        scope.launch {
            transcribing.withLock {
                try {
                    val text = api()?.transcribe(Wav.encode(pcm, 16000)) ?: ""
                    _ui.update { it.copy(offline = false) }
                    timings.mark(CallTimings.Step.STT_DONE, clock())
                    log("server ears: heard=${text.isNotBlank()}")
                    if (text.isNotBlank()) addMineLine(text.trim(), join = state.held != null || state.again != null)
                    step(CallSteps.Event.Transcribed(text))
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    log("transcribe failed: ${CallLog.why(e)}")
                    _ui.update { it.copy(offline = true) }
                    step(CallSteps.Event.TranscribeFailed(offline = true))
                }
            }
        }
    }

    /** The owner's line; [join]: the words go on the owner's last line (the same turn, heard before Kelo answered). */
    internal fun addMineLine(text: String, join: Boolean = false) {
        keloOpen = false
        val last = _lines.value.lastOrNull()
        if (join && last != null && last.mine) {
            _lines.update { it.dropLast(1) + last.copy(text = last.text + " " + text) }
            return
        }
        _lines.update { it + CallLine(nextLineId++, mine = true, text = text) }
    }

    /** Kelo's reply text as it comes: appended to the open line of Kelo, or a new line. */
    internal fun addKeloText(delta: String) {
        if (delta.isEmpty()) return
        _lines.update { all ->
            val last = all.lastOrNull()
            if (keloOpen && last != null && !last.mine) all.dropLast(1) + last.copy(text = last.text + delta)
            else all + CallLine(nextLineId++, mine = false, text = delta)
        }
        keloOpen = true
    }

    internal fun closeKeloLine() {
        keloOpen = false
    }

    /** Words typed into the call: shown as the owner's line and sent as a call turn (answered by voice). */
    fun sendTyped(text: String) {
        val words = text.trim()
        if (words.isEmpty() || state.phase == CallSteps.Phase.ENDED || state.phase == CallSteps.Phase.RINGING) return
        log("typed words")
        addMineLine(words, join = state.held != null || state.again != null)
        if (CallEars.pick(stt.view.value, phoneListener != null) == CallEars.Way.PHONE) phoneListener?.cancel()
        step(CallSteps.Event.Typed(words))
    }

    private fun sendText(out: CallSteps.Outgoing) {
        timings.mark(CallTimings.Step.TURN_STARTED, clock())
        if (chat.sendFromCall(out.text, callId, out.amend)) {
            log("turn sent")
            return
        }
        // the chat is busy (a typed reply, an open question): keep the words and send them when it is free (2026-11-19)
        log("turn refused: chat busy, words held")
        step(CallSteps.Event.SendRefused(out.text, out.amend))
        if (!chat.busy.value && !resending) {
            resending = true
            try { step(CallSteps.Event.ReplyEnded) } finally { resending = false }
        }
    }

    private fun sttLine(): String = stt.view.value?.let { "${it.state}/${it.runtime}" } ?: "none"
    private fun speechLine(): String = speech.view.value?.let { "${it.state}/${it.runtime}/langs=${it.languages.size}" } ?: "none"

    private suspend fun quietly(what: String, block: suspend () -> Unit) {
        try { block() } catch (e: CancellationException) { throw e } catch (e: Exception) { log("$what failed: ${CallLog.why(e)}") }
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
        modelsJob?.cancel()
        phoneJob?.cancel()
        phoneListener?.cancel()
        audio.chime(false)
        audio.release()
        micOpen = false
        queue.stop()
        device?.release()
    }
}
