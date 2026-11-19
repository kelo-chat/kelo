package chat.kelo.app.call

import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.voice.ModelView
import chat.kelo.app.voice.VoiceProgress
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One of the ten Kelo voices as GET /api/voice/speech lists it: female 1-5 (ids 0-4), male 1-5 (ids 5-9). */
@Serializable
data class SpeechVoice(val id: Int, val kind: String, val n: Int)

/** GET /api/voice/speech: the state of Kelo's own voice (the speech-out model) on this server (server voice_speak.py, api/voice.py). */
@Serializable
data class SpeechView(
    val state: String,                       // offered | off | downloading | ready | failed
    val declined: Boolean = false,
    val model: ModelView? = null,
    val progress: VoiceProgress? = null,
    val error: String? = null,
    val voice: Int = 0,
    val speed: Double = 1.0,
    val voices: List<SpeechVoice> = emptyList(),
    val languages: List<String> = emptyList(),
    val runtime: String = "ready",           // ready | missing
)

/** A reply event with the call that started its turn (null: a typed reply, or a reply the server started by itself). */
data class CallTurnEvent(val callId: String?, val event: TurnEvent, val questionOpen: Boolean = false)

/** The chat operations a call session needs from [chat.kelo.app.chat.ChatViewModel] (brief call-mode §4.6, §4.9; call-silent). */
interface CallChat {
    /** Starts a turn with the owner's words for call [callId]; [amend]: they replace the call's unanswered words (sent again with
     *  more). False when the chat cannot take it now ([busy]). */
    fun sendFromCall(text: String, callId: String, amend: Boolean = false): Boolean
    /** Stops this call's running turn when nothing of its answer has come yet and drops the owner's bubble of it; false otherwise. */
    fun takeBackFromCall(callId: String): Boolean
    /** Follows the call's greeting turn [turnId]; false when the chat is busy with another reply. */
    fun followFromCall(turnId: String, callId: String): Boolean
    /** Every reply event (CallCenter rings on CallOffer). */
    val turnEvents: SharedFlow<TurnEvent>
    /** Every reply event tagged with the call that started its turn; a call speaks only its own (2026-11-19). */
    val callTurnEvents: SharedFlow<CallTurnEvent>
    /** The chat cannot take a call's words now: a reply runs, or a secret or consent question is open. */
    val busy: StateFlow<Boolean>
}
