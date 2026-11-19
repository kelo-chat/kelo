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

/** The chat operations a call session needs from [chat.kelo.app.chat.ChatViewModel] (brief §4.6, §4.9). */
interface CallChat {
    fun sendFromCall(text: String, callId: String): Boolean
    fun followFromCall(turnId: String)
    val turnEvents: SharedFlow<TurnEvent>
    val busy: StateFlow<Boolean>
}

