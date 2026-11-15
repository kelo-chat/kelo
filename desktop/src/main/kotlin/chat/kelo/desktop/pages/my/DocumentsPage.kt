package chat.kelo.desktop.pages.my

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.my.Upload
import chat.kelo.app.my.UploadText
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.chat.MarkdownView
import chat.kelo.desktop.chat.pickFiles
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.EmptyNote
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MenuGroup
import chat.kelo.desktop.ui.MenuRow
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import java.io.File
import kotlin.coroutines.cancellation.CancellationException

/** The server's per-file limit (just under Cloudflare's 100 MB body limit), checked before anything is sent. */
internal const val DOCS_MAX_UPLOAD = 99_000_000L

/** Which kind a file is by its name (android/kelo .../my/DocumentMedia.kt MediaKind). */
object MediaKind {
    private val IMAGE = setOf("jpg", "jpeg", "png", "webp", "heic", "heif")
    private val VIDEO = setOf("mp4", "m4v", "mov", "webm", "3gp", "mkv")
    private val AUDIO = setOf("mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac", "amr")

    fun of(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        in IMAGE -> "image"
        in VIDEO -> "video"
        in AUDIO -> "audio"
        else -> "document"
    }
}

/** 65.4 -> "1:05", 3723 -> "1:02:03". */
fun clipLength(seconds: Double): String {
    val s = seconds.toLong().coerceAtLeast(0)
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = s % 60
    return if (h > 0) String.format(java.util.Locale.ROOT, "%d:%02d:%02d", h, m, sec) else String.format(java.util.Locale.ROOT, "%d:%02d", m, sec)
}

/** The server's previews (GET /api/uploads/{name}/thumb), decoded once per run. */
private object Thumbs {
    private val cache = object : LinkedHashMap<String, ImageBitmap>(64, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, ImageBitmap>?) = size > 120
    }

    @Synchronized fun cached(key: String): ImageBitmap? = cache[key]

    suspend fun load(api: KeloApi, name: String, size: Int, version: String): ImageBitmap? {
        val key = "$name|$size|$version"
        cached(key)?.let { return it }
        val bytes = try { api.download("/api/uploads/${KeloApi.segment(name)}/thumb?size=$size") } catch (_: ApiError) { return null } catch (_: Exception) { return null }
        val img = withContext(Dispatchers.Default) { decodeImage(bytes) } ?: return null
        synchronized(this) { cache[key] = img }
        return img
    }
}

/**
 * 마이 → 문서 (android/kelo .../my/DocumentsPage.kt): the files the user uploaded and only those. Photos and videos as a grid of
 * previews, documents and sound files as rows. A photo opens on the whole page; a document shows its text as the assistant reads it;
 * a video or sound file opens in the system's own player. Save the original, or delete.
 */
@Composable
fun DocumentsPage(app: AppContainer, onBack: () -> Unit) {
    val sessions = app.sessions
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var docs by remember { mutableStateOf<List<Upload>?>(null) }
    var open by remember { mutableStateOf<String?>(null) }
    var msg by remember { mutableStateOf("") }
    var uploading by remember { mutableStateOf<Job?>(null) }
    var progress by remember { mutableFloatStateOf(-1f) }
    var progressText by remember { mutableStateOf("") }

    suspend fun load() {
        docs = try { sessions.requireApi().get<List<Upload>>("/api/uploads") } catch (_: ApiError) { emptyList() }
    }
    LaunchedEffect(Unit) { load() }

    fun upload() {
        val files = pickFiles()
        if (files.isEmpty()) return
        msg = ""
        uploading = scope.launch {
            try {
                for (f in files) {
                    if (f.length() > DOCS_MAX_UPLOAD) { msg = strings.get(R.string.docs_too_large); continue }
                    progress = -1f
                    val type = runCatching { java.nio.file.Files.probeContentType(f.toPath()) }.getOrNull()
                    sessions.requireApi().uploadStream(f.name, f.length(), type, { f.inputStream() }) { sent, total ->
                        progress = if (total > 0) (sent.toFloat() / total).coerceIn(0f, 1f) else -1f
                        progressText = f.name
                    }
                    msg = strings.get(R.string.docs_uploaded)
                }
                load()
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                msg = e.detail
            } catch (e: java.io.IOException) {
                msg = strings.get(R.string.docs_read_failed)
            } finally {
                uploading = null
            }
        }
    }

    val list = docs
    val name = open
    if (name != null) {
        val u = list?.firstOrNull { it.name == name } ?: Upload("uploads/$name", kind = MediaKind.of(name))
        FileViewer(app, u, onClose = { open = null }, onDeleted = { open = null; scope.launch { load() } })
        return
    }
    SubPage(stringResource(R.string.docs_title), onBack) {
        MyCard {
            if (uploading != null) {
                if (progress >= 0f) LinearProgressIndicator(progress = { progress }, modifier = Modifier.fillMaxWidth(), color = Kelo.colors.accent, trackColor = Kelo.colors.bubble)
                else LinearProgressIndicator(modifier = Modifier.fillMaxWidth(), color = Kelo.colors.accent, trackColor = Kelo.colors.bubble)
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    MutedText(stringResource(R.string.docs_uploading) + " " + progressText, small = true, modifier = Modifier.weight(1f))
                    LinkButton(stringResource(R.string.cancel), onClick = { uploading?.cancel(); uploading = null })
                }
            } else {
                PrimaryButton(stringResource(R.string.docs_upload), onClick = ::upload)
            }
            MutedText(stringResource(R.string.docs_hint), small = true)
            if (msg.isNotEmpty()) MutedText(msg, small = true)
        }
        when {
            list == null -> MutedText(stringResource(R.string.loading), small = true)
            list.isEmpty() -> EmptyNote(stringResource(R.string.docs_empty))
            else -> {
                val media = list.filter { it.isMedia }.sortedByDescending { it.modified }
                val files = list.filterNot { it.isMedia }
                if (media.isNotEmpty()) {
                    SectionLabel(stringResource(R.string.docs_media))
                    MediaGrid(app, media, onOpen = { open = it.name })
                }
                if (files.isNotEmpty()) {
                    if (media.isNotEmpty()) SectionLabel(stringResource(R.string.docs_files))
                    MenuGroup {
                        files.sortedByDescending { it.modified }.forEach { d ->
                            row {
                                val day = stringResource(R.string.docs_size_day, fileSize(d.bytes), shortDay(d.modified))
                                MenuRow(
                                    if (d.kind == "audio") KeloIcons.Music else KeloIcons.File, d.name, onClick = { open = d.name },
                                    value = d.duration?.takeIf { d.kind == "audio" }?.let { stringResource(R.string.docs_size_day, clipLength(it), day) } ?: day,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun rememberThumb(app: AppContainer, u: Upload, size: Int): ImageBitmap? {
    var img by remember(u.name, u.bytes, u.modified) { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(u.name, u.bytes, u.modified) {
        img = runCatching { Thumbs.load(app.sessions.requireApi(), u.name, size, "${u.bytes}-${u.modified}") }.getOrNull()
    }
    return img
}

/** Photos and videos as square previews, as many in a row as fit (at least three). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MediaGrid(app: AppContainer, items: List<Upload>, onOpen: (Upload) -> Unit) {
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val gap = 6.dp
        val perRow = maxOf(3, (maxWidth / 150.dp).toInt())
        val side = (maxWidth - gap * (perRow - 1)) / perRow
        FlowRow(horizontalArrangement = Arrangement.spacedBy(gap), verticalArrangement = Arrangement.spacedBy(gap)) {
            items.forEach { u -> MediaTile(app, u, { onOpen(u) }, Modifier.width(side)) }
        }
    }
}

@Composable
private fun MediaTile(app: AppContainer, u: Upload, onOpen: () -> Unit, modifier: Modifier) {
    val c = Kelo.colors
    val thumb = rememberThumb(app, u, 320)
    Box(
        modifier.aspectRatio(1f).clip(RoundedCornerShape(8.dp)).background(c.bubble).clickable(role = Role.Button, onClick = onOpen)
            .semantics { contentDescription = u.name },
        contentAlignment = Alignment.Center,
    ) {
        if (thumb != null) Image(thumb, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.matchParentSize())
        else Icon(if (u.kind == "video") KeloIcons.Video else KeloIcons.Image, contentDescription = null, tint = c.muted)
        if (u.kind == "video") {
            Text(
                u.duration?.let { clipLength(it) } ?: "▶", color = Color.White, style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.align(Alignment.BottomEnd).padding(6.dp).clip(RoundedCornerShape(4.dp)).background(Color(0x99000000)).padding(horizontal = 5.dp, vertical = 1.dp),
            )
        }
    }
}

/** One file: a photo, a document's text, or a video or sound file to open in the system's player. Save the original, or delete. */
@Composable
private fun FileViewer(app: AppContainer, u: Upload, onClose: () -> Unit, onDeleted: () -> Unit) {
    val sessions = app.sessions
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var err by remember(u.name) { mutableStateOf("") }
    var note by remember(u.name) { mutableStateOf("") }
    var confirm by remember { mutableStateOf(false) }
    val path = "/api/uploads/${KeloApi.segment(u.name)}/file"

    fun save() {
        val target = saveAs(u.name) ?: return
        note = strings.get(R.string.docs_saving)
        scope.launch {
            note = try {
                withContext(Dispatchers.IO) { target.outputStream().use { out -> sessions.requireApi().downloadTo(path, out) } }
                strings.get(R.string.docs_saved)
            } catch (e: ApiError) { err = e.detail; "" } catch (_: Exception) { strings.get(R.string.docs_save_failed) }
        }
    }
    /** A video or sound file: into a temporary file, then the system's own player opens it. */
    fun openOutside() {
        scope.launch {
            try {
                val dir = File(app.dataDir, "open").apply { mkdirs() }
                val f = File(dir, u.name.substringAfterLast('/'))
                withContext(Dispatchers.IO) {
                    f.outputStream().use { out -> sessions.requireApi().downloadTo(path, out) }
                    java.awt.Desktop.getDesktop().open(f)
                }
            } catch (e: ApiError) { err = e.detail } catch (_: Exception) { err = strings.get(R.string.docs_save_failed) }
        }
    }

    SubPage(u.name, onClose) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            LinkButton(stringResource(R.string.docs_download), onClick = ::save)
            if (u.kind == "video" || u.kind == "audio") LinkButton(stringResource(R.string.desktop_my_open_outside), onClick = ::openOutside)
            Box(Modifier.weight(1f))
            LinkButton(stringResource(R.string.delete), onClick = { confirm = true }, danger = true)
        }
        if (note.isNotEmpty()) MutedText(note, small = true)
        if (err.isNotEmpty()) ErrorText(err, small = true)
        when (u.kind) {
            "image" -> {
                var full by remember(u.name) { mutableStateOf<ImageBitmap?>(null) }
                LaunchedEffect(u.name) { full = runCatching { decodeImage(sessions.requireApi().download(path)) }.getOrNull() }
                val shown = full ?: rememberThumb(app, u, 1024)
                if (shown != null) Image(shown, contentDescription = u.name, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxWidth().heightIn(max = 720.dp))
                else MutedText(stringResource(R.string.loading), small = true)
            }
            "video", "audio" -> {
                val thumb = if (u.kind == "video") rememberThumb(app, u, 1024) else null
                if (thumb != null) Image(thumb, contentDescription = u.name, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxWidth().heightIn(max = 520.dp))
                MutedText(listOfNotNull(u.duration?.let { clipLength(it) }, fileSize(u.bytes)).joinToString(" · "), small = true)
            }
            else -> DocumentText(app, u.name)
        }
    }
    if (confirm) {
        ConfirmDialog(
            text = stringResource(if (u.kind == "document") R.string.docs_delete_confirm else R.string.docs_delete_media_confirm),
            yes = stringResource(R.string.delete),
            onDismiss = { confirm = false },
            onYes = {
                scope.launch {
                    try {
                        sessions.requireApi().delete<JsonElement>("/api/uploads/${KeloApi.segment(u.name)}")
                        onDeleted()
                    } catch (e: ApiError) { err = e.detail }
                }
            },
        )
    }
}

@Composable
private fun DocumentText(app: AppContainer, name: String) {
    var text by remember(name) { mutableStateOf<String?>(null) }
    var readErr by remember(name) { mutableStateOf("") }
    LaunchedEffect(name) {
        try { text = app.sessions.requireApi().get<UploadText>("/api/uploads/${KeloApi.segment(name)}/text").text } catch (e: ApiError) { readErr = e.detail }
    }
    MyCard {
        val t = text
        when {
            readErr.isNotEmpty() -> ErrorText(readErr, small = true)
            t == null -> MutedText(stringResource(R.string.docs_reading), small = true)
            else -> MarkdownView(t)
        }
    }
}

/** Where to save a file (the system's save dialog). */
private fun saveAs(name: String): File? {
    val d = java.awt.FileDialog(null as java.awt.Frame?, "", java.awt.FileDialog.SAVE)
    d.file = name.substringAfterLast('/')
    d.isVisible = true
    val dir = d.directory ?: return null
    val file = d.file ?: return null
    return File(dir, file)
}
