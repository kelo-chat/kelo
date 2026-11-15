package chat.kelo.app.connectors

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File

/**
 * The services' app icons from the server (GET /api/connectors/icons/…), decoded once and kept in memory, and the files kept on
 * disk (cacheDir/connector-icons) so the list shows them at once next time and offline. A URL that cannot be fetched gives null
 * and the tile keeps its initials.
 */
object ConnectorIcons {
    private val memory = object : LruCache<String, ImageBitmap>(16 * 1024 * 1024) {
        override fun sizeOf(key: String, value: ImageBitmap) = value.width * value.height * 4
    }
    private val lock = Mutex()

    fun cached(url: String): ImageBitmap? = memory.get(url)

    suspend fun load(api: KeloApi?, dir: File, url: String): ImageBitmap? {
        if (!ConnectorIconRules.valid(url)) return null
        memory.get(url)?.let { return it }
        return lock.withLock {
            memory.get(url)?.let { return@withLock it }
            val file = File(dir, ConnectorIconRules.fileName(url))
            val img = withContext(Dispatchers.IO) {
                val kept = runCatching { if (file.isFile) file.readBytes() else null }.getOrNull()
                kept?.let(::decode) ?: run {
                    val bytes = runCatching { api?.download(url) }.getOrNull() ?: return@run null
                    decode(bytes)?.also {
                        runCatching {
                            dir.mkdirs()
                            val name = ConnectorIconRules.fileName(url)
                            dir.listFiles()?.filter { f -> ConnectorIconRules.stale(url, f.name) }?.forEach { f -> f.delete() }   // older versions
                            val tmp = File(dir, "$name.tmp")
                            tmp.writeBytes(bytes)
                            tmp.renameTo(file)
                        }
                    }
                }
            }
            img?.also { memory.put(url, it) }
        }
    }

    private fun decode(bytes: ByteArray): ImageBitmap? =
        runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() }.getOrNull()
}

/**
 * A service's picture: its own app icon in a rounded square when the server has one, else [fallback] (the initials tile). While the
 * icon loads, a soft square in its colour holds the place, so the row does not jump.
 */
@Composable
fun ServiceIcon(icon: String, small: String, color: String, size: Dp, fallback: @Composable () -> Unit) {
    val url = ConnectorIconRules.pick(icon, small, size.value)
    if (url.isEmpty()) {
        fallback()
        return
    }
    val sessions = LocalAppContainer.current.sessions
    val dir = File(LocalContext.current.cacheDir, "connector-icons")
    // null = still loading, Unit = failed
    val state by produceState<Any?>(ConnectorIcons.cached(url), url) {
        if (value == null) value = ConnectorIcons.load(sessions.api.value, dir, url) ?: Unit
    }
    val shape = RoundedCornerShape(size * 0.28f)
    when (val s = state) {
        is ImageBitmap -> Image(
            s, contentDescription = null, contentScale = ContentScale.Crop,
            modifier = Modifier.size(size).clip(shape).border(1.dp, Kelo.colors.line, shape),
        )
        null -> {
            val tint = ConnectorIconRules.color(color)?.let { Color(it).copy(alpha = 0.18f) } ?: Kelo.colors.bubble
            Box(Modifier.size(size).clip(shape).background(tint))
        }
        else -> fallback()
    }
}
