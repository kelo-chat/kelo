package chat.kelo.app.voice

import chat.kelo.app.call.SpeechView
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody

enum class SpeakProblem { NOT_READY, LANG, BUSY, OFFLINE, FAILED }

fun speakProblem(e: ApiError): SpeakProblem = when {
    e.isNetwork -> SpeakProblem.OFFLINE
    e.code == "voice_speech_not_ready" -> SpeakProblem.NOT_READY
    e.code == "voice_speech_lang" -> SpeakProblem.LANG
    e.code == "voice_busy" -> SpeakProblem.BUSY
    else -> SpeakProblem.FAILED
}

class SpeakException(val problem: SpeakProblem, cause: Throwable? = null) : Exception(problem.name, cause)

/** The calls behind the server's voice input (api/voice.py). Every call goes through [KeloApi], so KE2E seals it. */
open class VoiceApi(private val api: KeloApi) : Transcriber {

    /** GET /api/voice: null when the server is older than voice input (404). */
    open suspend fun view(lang: String): VoiceView? = try {
        api.get<VoiceView>("/api/voice?lang=${KeloApi.segment(lang)}")
    } catch (e: ApiError) {
        if (e.status == 404) null else throw e
    }

    /** POST /api/voice/model: starts (or resumes) the download of [id] and makes it the model in use. */
    open suspend fun choose(id: String): VoiceView = api.post(
        "/api/voice/model", KeloApi.body(ModelChoice(id)),
    )

    /** POST /api/voice/decline: the owner chose "취소" on the first-tap question. */
    open suspend fun decline(): VoiceView = api.post("/api/voice/decline", JsonObject(emptyMap()))

    /** DELETE /api/voice/model: removes the model files on the server. */
    open suspend fun remove(): VoiceView = api.delete("/api/voice/model")

    /** POST /api/voice/transcribe with a WAV (16 kHz mono PCM16). Returns the recognised text, which may be empty. */
    override suspend fun transcribe(wav: ByteArray): String {
        val req = api.requestBuilder("/api/voice/transcribe")
            .post(wav.toRequestBody("audio/wav".toMediaType()))
            .build()
        val answer = KeloApi.readJson(api.sendWithReadTimeout(req, TRANSCRIBE_READ_SECONDS).toString(Charsets.UTF_8))
        return (answer as? JsonObject)?.get("text")?.jsonPrimitive?.content.orEmpty()
    }

    /** GET /api/voice/speech: null when the server does not support speech out (404). */
    open suspend fun speechView(): SpeechView? = try {
        api.get<SpeechView>("/api/voice/speech")
    } catch (e: ApiError) {
        if (e.status == 404) null else throw e
    }

    /** POST /api/voice/speech/model: starts (or resumes) download of Supertonic 3. */
    open suspend fun speechChoose(): SpeechView = api.post("/api/voice/speech/model", JsonObject(emptyMap()))

    /** POST /api/voice/speech/decline: owner declined Kelo voice model on first call. */
    open suspend fun speechDecline(): SpeechView = api.post("/api/voice/speech/decline", JsonObject(emptyMap()))

    /** DELETE /api/voice/speech/model: removes the speech model files on the server. */
    open suspend fun speechRemove(): SpeechView = api.delete("/api/voice/speech/model")

    /** PUT /api/voice/speech/prefs: update voice sid (0-9) and/or speed (0.7-1.5). */
    open suspend fun speechPrefs(voice: Int? = null, speed: Double? = null): SpeechView {
        val body = buildJsonObject {
            if (voice != null) put("voice", JsonPrimitive(voice))
            if (speed != null) put("speed", JsonPrimitive(speed))
        }
        return api.put("/api/voice/speech/prefs", body)
    }

    /** POST /api/voice/speak: returns WAV PCM16 mono 44.1 kHz audio bytes. */
    open suspend fun speak(text: String, lang: String, voice: Int? = null, speed: Double? = null): ByteArray {
        val body = buildJsonObject {
            put("text", JsonPrimitive(text))
            put("lang", JsonPrimitive(lang))
            if (voice != null) put("voice", JsonPrimitive(voice))
            if (speed != null) put("speed", JsonPrimitive(speed))
        }
        val req = api.requestBuilder("/api/voice/speak")
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build()
        try {
            return api.sendWithReadTimeout(req, SPEAK_READ_SECONDS)
        } catch (e: ApiError) {
            throw SpeakException(speakProblem(e), e)
        }
    }

    /** POST /api/calls/{call_id}/answered: starts turn with hidden note, returns turn id. */
    open suspend fun answered(callId: String, timezone: String? = null, topic: String? = null): String {
        val body = buildJsonObject {
            if (timezone != null) put("timezone", JsonPrimitive(timezone))
            if (topic != null) put("topic", JsonPrimitive(topic))
        }
        val res = api.post<JsonObject>("/api/calls/${KeloApi.segment(callId)}/answered", body)
        return (res["id"] as? JsonPrimitive)?.content.orEmpty()
    }

    @Serializable
    private data class ModelChoice(val model: String)

    companion object {
        /** A long clip takes seconds on a small server; the default 60 s read timeout is too short for the 2-minute cap. */
        const val TRANSCRIBE_READ_SECONDS = 120L
        /** Speech synthesis timeout (brief §4.6: sendWithReadTimeout 40 s). */
        const val SPEAK_READ_SECONDS = 40L
    }
}
