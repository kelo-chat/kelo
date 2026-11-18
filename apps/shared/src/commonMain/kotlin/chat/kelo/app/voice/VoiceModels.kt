package chat.kelo.app.voice

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** The server's speech model as GET /api/voice shows it (api/voice.py, voice_models.py). Attribution fields are data only: no app screen shows them. */
@Serializable
data class ModelView(
    val id: String,
    val name: String,
    val maker: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    @SerialName("memory_bytes") val memoryBytes: Long = 0,
    val languages: List<String> = emptyList(),
    @SerialName("license_name") val licenseName: String = "",
    @SerialName("license_url") val licenseUrl: String = "",
    @SerialName("source_url") val sourceUrl: String = "",
    val attribution: String = "",
)

@Serializable
data class VoiceProgress(val done: Long = 0, val total: Long = 0)

/** GET /api/voice: the owner's voice input state on this server. */
@Serializable
data class VoiceView(
    val state: String,                       // off | offered | downloading | ready | failed | unsupported
    val declined: Boolean = false,
    val recommended: ModelView? = null,
    val model: ModelView? = null,
    val progress: VoiceProgress? = null,
    val error: String? = null,
    val models: List<ModelView> = emptyList(),
    @SerialName("max_seconds") val maxSeconds: Int = 120,
    val runtime: String = "ready",           // ready | missing
)
