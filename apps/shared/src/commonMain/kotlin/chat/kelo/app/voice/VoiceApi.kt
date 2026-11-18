package chat.kelo.app.voice

import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody

/** The calls behind the server's voice input (api/voice.py). Every call goes through [KeloApi], so KE2E seals it. */
class VoiceApi(private val api: KeloApi) {

    /** GET /api/voice: null when the server is older than voice input (404). */
    suspend fun view(lang: String): VoiceView? = try {
        api.get<VoiceView>("/api/voice?lang=${KeloApi.segment(lang)}")
    } catch (e: ApiError) {
        if (e.status == 404) null else throw e
    }

    /** POST /api/voice/model: starts (or resumes) the download of [id] and makes it the model in use. */
    suspend fun choose(id: String): VoiceView = api.post(
        "/api/voice/model", KeloApi.body(ModelChoice(id)),
    )

    /** POST /api/voice/decline: the owner chose "취소" on the first-tap question. */
    suspend fun decline(): VoiceView = api.post("/api/voice/decline", JsonObject(emptyMap()))

    /** DELETE /api/voice/model: removes the model files on the server. */
    suspend fun remove(): VoiceView = api.delete("/api/voice/model")

    /** POST /api/voice/transcribe with a WAV (16 kHz mono PCM16). Returns the recognised text, which may be empty. */
    suspend fun transcribe(wav: ByteArray): String {
        val req = api.requestBuilder("/api/voice/transcribe")
            .post(wav.toRequestBody("audio/wav".toMediaType()))
            .build()
        val answer = KeloApi.readJson(api.sendWithReadTimeout(req, TRANSCRIBE_READ_SECONDS).toString(Charsets.UTF_8))
        return (answer as? JsonObject)?.get("text")?.jsonPrimitive?.content.orEmpty()
    }

    @Serializable
    private data class ModelChoice(val model: String)

    companion object {
        /** A long clip takes seconds on a small server; the default 60 s read timeout is too short for the 2-minute cap. */
        const val TRANSCRIBE_READ_SECONDS = 120L
    }
}
