package chat.kelo.desktop.chat

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import chat.kelo.app.R
import chat.kelo.app.chat.MessageFile
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.stringResource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * How a user's message with files is drawn (pure, so it is tested). A copy of the phone's MessageFileRules
 * (android/kelo .../chat/MessageFiles.kt, whose file also holds Android drawing code), plus [kindOf] for a file this computer sends.
 */
object MessageFileRules {
    /**
     * The bubble's text: nothing when it is the app's own sentence ([autoText]) and the files show; else the typed text without the
     * lines the app added for each file ([line] of its path or name), since the files show as pictures and chips.
     */
    fun shownText(text: String, files: List<MessageFile>, autoText: Boolean, line: (String) -> String): String {
        if (files.isEmpty()) return text
        if (autoText) return ""
        val added = files.flatMap { listOf(line(it.path), line(it.name)) }.toSet()
        return text.lines().filterNot { it.trim() in added }.joinToString("\n").trim()
    }

    /** Rows of the picture grid: one big picture alone, else two a row. */
    fun rows(pictures: Int): List<Int> = when {
        pictures <= 0 -> emptyList()
        pictures == 1 -> listOf(1)
        else -> List(pictures / 2) { 2 } + if (pictures % 2 == 1) listOf(1) else emptyList()
    }

    private val IMAGE = setOf("png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "bmp")
    private val VIDEO = setOf("mp4", "mov", "m4v", "webm", "mkv", "avi")

    /** What a file sent from here is, by its name (the stored message's own kind replaces it once the server has it). */
    fun kindOf(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        in IMAGE -> "image"
        in VIDEO -> "video"
        else -> "document"
    }
}

/**
 * The files the user sent with a message, at the end side above (or instead of) the text: pictures and videos as a small grid of
 * thumbnails (GET thumb with the signed-in API; a click shows the picture whole), other files as chips with their names.
 */
@Composable
fun UserFiles(files: List<MessageFile>, sessions: Sessions, modifier: Modifier = Modifier) {
    val pictures = files.filter { it.visual }
    val others = files.filterNot { it.visual }
    Column(modifier, horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        var at = 0
        for (n in MessageFileRules.rows(pictures.size)) {
            val row = pictures.subList(at, at + n)
            at += n
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                for (f in row) Thumb(f, if (n == 1 && pictures.size == 1) 200.dp else 104.dp, sessions)
            }
        }
        for (f in others) FileChip(f)
    }
}

@Composable
private fun Thumb(f: MessageFile, side: Dp, sessions: Sessions) {
    val c = Kelo.colors
    var bitmap by remember(f.path, f.thumb) { mutableStateOf<ImageBitmap?>(null) }
    var open by remember(f.path) { mutableStateOf(false) }
    LaunchedEffect(f.path, f.thumb) {
        val url = f.thumb ?: return@LaunchedEffect   // the server makes it once the message is stored (user_message brings it)
        bitmap = load(sessions, url)
    }
    val shape = RoundedCornerShape(14.dp)
    Box(
        Modifier.size(side).clip(shape).background(c.card).border(1.dp, c.line, shape)
            .clickable(role = Role.Image, onClickLabel = f.name) { if (f.kind == "image") open = true },
        contentAlignment = Alignment.Center,
    ) {
        val b = bitmap
        if (b != null) Image(b, contentDescription = f.name, modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        else Icon(KeloIcons.Image, contentDescription = f.name, tint = c.muted)
    }
    if (open) FullPicture(f, sessions, onClose = { open = false })
}

/** The picture whole over the chat (the original file, read with the signed-in API). */
@Composable
private fun FullPicture(f: MessageFile, sessions: Sessions, onClose: () -> Unit) {
    var bitmap by remember(f.url) { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(f.url) { if (f.url.isNotEmpty()) bitmap = load(sessions, f.url) }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.92f)).clickable { onClose() }, contentAlignment = Alignment.Center) {
            bitmap?.let { Image(it, contentDescription = f.name, modifier = Modifier.fillMaxSize().padding(24.dp), contentScale = ContentScale.Fit) }
        }
    }
}

@Composable
private fun FileChip(f: MessageFile) {
    val c = Kelo.colors
    val shape = RoundedCornerShape(12.dp)
    Row(
        Modifier.widthIn(max = 260.dp).clip(shape).background(c.mine).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(KeloIcons.File, contentDescription = stringResource(R.string.chat_attached_file), tint = c.ink, modifier = Modifier.size(18.dp))
        // one line (a file name is one value), cut at the end
        Text(f.name, color = c.ink, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

private suspend fun load(sessions: Sessions, url: String): ImageBitmap? = runCatching {
    val bytes = sessions.requireApi().download(url)
    withContext(Dispatchers.Default) { org.jetbrains.skia.Image.makeFromEncoded(bytes).toComposeImageBitmap() }
}.getOrNull()
