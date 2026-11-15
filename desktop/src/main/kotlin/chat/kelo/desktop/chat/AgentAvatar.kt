package chat.kelo.desktop.chat

import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toComposeImageBitmap
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.CacheSlot
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.WeakHashMap

/** The agent's picture as kept here (encrypted, like every screen's data): its version and the bytes in base64. */
@Serializable
data class AvatarBlob(val version: Long = 0, val data: String = "")

/**
 * The agent's picture (GET/PUT/DELETE /api/agent/avatar), as android/kelo .../agent/AgentAvatar.kt: [image] is null while there is
 * none (Kelo's character shows then). The server's avatar_version (GET /api/agent/status, the profile) says when it changed; the
 * picture is fetched only then and kept for the next launch. One per sign-in ([of]); the header and the My → agent page share it.
 */
class AgentAvatar(private val sessions: Sessions) {
    private val _image = MutableStateFlow<ImageBitmap?>(null)
    val image: StateFlow<ImageBitmap?> = _image.asStateFlow()
    private var shown = -1L
    private val lock = Mutex()

    /** Brings the picture in step with [version] (0 = none). */
    suspend fun sync(version: Long) = lock.withLock {
        if (version == shown) return@withLock
        if (version <= 0) {
            _image.value = null
            shown = 0L
            runCatching { sessions.keep(SLOT, AvatarBlob()) }
            return@withLock
        }
        val kept = withContext(Dispatchers.IO) { runCatching { sessions.cached(SLOT) }.getOrNull() }
        if (kept != null && kept.version == version && kept.data.isNotEmpty()) {
            decode(java.util.Base64.getDecoder().decode(kept.data))?.let { _image.value = it; shown = version; return@withLock }
        }
        val api = sessions.api.value ?: return@withLock
        try {
            val bytes = api.download("/api/agent/avatar")
            val img = decode(bytes) ?: return@withLock
            _image.value = img
            shown = version
            runCatching { sessions.keep(api, SLOT, AvatarBlob(version, java.util.Base64.getEncoder().encodeToString(bytes))) }
        } catch (e: ApiError) {
            if (e.status == 404) { _image.value = null; shown = 0 }
        }
    }

    /** Shows the kept picture at once on launch (before the server is asked). */
    suspend fun warm() = lock.withLock {
        if (shown >= 0) return@withLock
        val kept = withContext(Dispatchers.IO) { runCatching { sessions.cached(SLOT) }.getOrNull() } ?: return@withLock
        if (kept.version > 0 && kept.data.isNotEmpty()) {
            decode(java.util.Base64.getDecoder().decode(kept.data))?.let { _image.value = it; shown = kept.version }
        }
    }

    /** PUT the picture (JPEG/PNG/WebP, at most 2 MB); the server makes it 512×512. Returns the new version. */
    suspend fun upload(api: KeloApi, bytes: ByteArray, type: String): Long {
        val req = api.requestBuilder("/api/agent/avatar").put(bytes.toRequestBody(type.toMediaTypeOrNull())).build()
        val answer = KeloApi.readJson(api.send(req).toString(Charsets.UTF_8))
        val v = (answer as? kotlinx.serialization.json.JsonObject)?.get("avatar_version")?.let { (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toLongOrNull() } ?: 0L
        lock.withLock { shown = -1L }
        sync(v)
        return v
    }

    suspend fun remove(api: KeloApi) {
        api.delete<JsonElement>("/api/agent/avatar")
        sync(0)
    }

    private suspend fun decode(bytes: ByteArray): ImageBitmap? = withContext(Dispatchers.Default) {
        runCatching { org.jetbrains.skia.Image.makeFromEncoded(bytes).toComposeImageBitmap() }.getOrNull()
    }

    companion object {
        const val MAX_BYTES = 2_000_000L
        private val SLOT = CacheSlot("avatar", AvatarBlob.serializer())
        private val all = WeakHashMap<Sessions, AgentAvatar>()

        /** The one picture holder of these [sessions]. */
        @Synchronized fun of(sessions: Sessions): AgentAvatar = all.getOrPut(sessions) { AgentAvatar(sessions) }
    }
}
