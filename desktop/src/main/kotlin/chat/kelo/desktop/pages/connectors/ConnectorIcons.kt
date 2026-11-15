package chat.kelo.desktop.pages.connectors

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
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import chat.kelo.app.connectors.ConnectorIconRules
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File

/**
 * The services' app icons from the server (GET /api/connectors/icons/…), as android/kelo .../connectors/ConnectorIcons.kt: decoded
 * once and kept in memory, the files kept in [Platform.dataDir]/connector-icons for the next launch and offline. A URL that cannot
 * be fetched gives null and the tile keeps its initials. [bind] is called once by the AppContainer.
 */
object ConnectorIcons {
    @Volatile private var sessions: Sessions? = null
    @Volatile private var dir: File? = null
    private val memory = object : LinkedHashMap<String, ImageBitmap>(64, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, ImageBitmap>?) = size > 256
    }
    private val lock = Mutex()

    fun bind(sessions: Sessions, dir: File) {
        this.sessions = sessions
        this.dir = dir
    }

    fun cached(url: String): ImageBitmap? = synchronized(memory) { memory[url] }

    suspend fun load(url: String): ImageBitmap? {
        if (!ConnectorIconRules.valid(url)) return null
        cached(url)?.let { return it }
        return lock.withLock {
            cached(url)?.let { return@withLock it }
            val folder = dir
            val name = ConnectorIconRules.fileName(url)
            val img = withContext(Dispatchers.IO) {
                val file = folder?.let { File(it, name) }
                val kept = runCatching { if (file?.isFile == true) file.readBytes() else null }.getOrNull()
                kept?.let(::decode) ?: run {
                    val bytes = runCatching { sessions?.api?.value?.download(url) }.getOrNull() ?: return@run null
                    decode(bytes)?.also {
                        if (folder != null && file != null) runCatching {
                            folder.mkdirs()
                            folder.listFiles()?.filter { f -> ConnectorIconRules.stale(url, f.name) }?.forEach { f -> f.delete() }   // older versions
                            val tmp = File(folder, "$name.tmp")
                            tmp.writeBytes(bytes)
                            if (!tmp.renameTo(file)) tmp.delete()
                        }
                    }
                }
            }
            img?.also { synchronized(memory) { memory[url] = it } }
        }
    }

    private fun decode(bytes: ByteArray): ImageBitmap? =
        runCatching { org.jetbrains.skia.Image.makeFromEncoded(bytes).toComposeImageBitmap() }.getOrNull()
}

/**
 * A service's picture: its own app icon in a rounded square when the server has one, else [fallback] (the initials tile). While the
 * icon loads, a soft square in its colour holds the place.
 */
@Composable
fun ServiceIcon(icon: String, small: String, color: String, size: Dp, fallback: @Composable () -> Unit) {
    val url = ConnectorIconRules.pick(icon, small, size.value)
    if (url.isEmpty()) {
        fallback()
        return
    }
    // null = still loading, Unit = failed
    val state by produceState<Any?>(ConnectorIcons.cached(url), url) {
        if (value == null) value = ConnectorIcons.load(url) ?: Unit
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
