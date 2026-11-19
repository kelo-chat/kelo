package chat.kelo.app.call

import chat.kelo.app.chat.SpokenText
import chat.kelo.app.voice.VoiceView

/** Which voice says a piece of Kelo's reply (brief §4.5). */
object SpeechLang {
    fun of(piece: String, appLang: String): String = SpokenText.language(piece, appLang)

    /** The BCP-47 primary subtag when the server's Kelo voice is ready and speaks it; null for the device voice. */
    fun server(tag: String, view: SpeechView?): String? {
        if (view == null || view.state != "ready" || view.runtime != "ready") return null
        val primary = tag.substringBefore('-').substringBefore('_').lowercase()
        return if (primary in view.languages) primary else null
    }
}

/** What the call still needs before it can open the mic (brief §4.5). */
object CallReadiness {
    enum class Need { MIC_PERMISSION, STT_QUESTION, VOICE_QUESTION, NOTHING, IMPOSSIBLE }

    fun next(
        stt: VoiceView?, voice: SpeechView?, micAllowed: Boolean, phoneRecognizer: Boolean,
        askedStt: Boolean, askedVoice: Boolean,
    ): Need {
        if (!micAllowed) return Need.MIC_PERMISSION
        if (stt?.state == "offered" && !askedStt) return Need.STT_QUESTION
        if (CallEars.pick(stt, phoneRecognizer) == CallEars.Way.NONE) return Need.IMPOSSIBLE
        // Asked once per install (askedVoice): "offered" (not chosen yet) and "off" (declined) both get the strong question first.
        if (!askedVoice && voice != null && (voice.state == "offered" || voice.state == "off")) return Need.VOICE_QUESTION
        return Need.NOTHING
    }
}

/** Who hears the owner right now (C9): the server's STT when it is ready, else the phone's recognizer, else nobody. */
object CallEars {
    enum class Way { SERVER, PHONE, NONE }

    fun pick(stt: VoiceView?, phoneRecognizer: Boolean): Way = when {
        stt != null && stt.state == "ready" && stt.runtime == "ready" -> Way.SERVER
        phoneRecognizer -> Way.PHONE
        else -> Way.NONE
    }
}

/** The one-time "better voice" line on the call screen (C17). */
object CallVoiceLine {
    fun show(voice: SpeechView?, deviceSpokeThisCall: Boolean, dismissed: Boolean): Boolean =
        deviceSpokeThisCall && !dismissed && voice != null && voice.state != "ready"
}

/** Whether the header call button shows and `can_call` is sent (C4, C9). */
object CallAvailability {
    fun of(stt: VoiceView?, micAvailable: Boolean, phoneRecognizer: Boolean): Boolean =
        micAvailable && CallEars.pick(stt, phoneRecognizer) != CallEars.Way.NONE ||
            micAvailable && stt != null && stt.state == "offered" && stt.runtime == "ready"
}

/** One owner turn timings: speech end → … → first sound (brief §4.5). */
class CallTimings {
    enum class Step { SPEECH_END, STT_DONE, TURN_STARTED, FIRST_TEXT, FIRST_SENTENCE, FIRST_AUDIO, PLAY_START }

    private val marks = mutableMapOf<Step, Long>()

    fun mark(step: Step, atMs: Long) {
        marks[step] = atMs
    }

    fun line(): String {
        val parts = mutableListOf<String>()
        fun delta(from: Step, to: Step, label: String) {
            val t0 = marks[from]
            val t1 = marks[to]
            if (t0 != null && t1 != null && t1 >= t0) {
                parts.add("$label=${t1 - t0}")
            }
        }
        delta(Step.SPEECH_END, Step.STT_DONE, "speech_end→stt")
        delta(Step.STT_DONE, Step.TURN_STARTED, "stt→turn")
        delta(Step.TURN_STARTED, Step.FIRST_TEXT, "turn→text")
        delta(Step.FIRST_TEXT, Step.FIRST_SENTENCE, "text→sentence")
        delta(Step.FIRST_SENTENCE, Step.FIRST_AUDIO, "sentence→audio")
        delta(Step.FIRST_AUDIO, Step.PLAY_START, "audio→play")

        val start = marks[Step.SPEECH_END]
        val end = marks[Step.PLAY_START]
        if (start != null && end != null && end >= start) {
            parts.add("total=${end - start}")
        }
        return parts.joinToString(" ")
    }
}

