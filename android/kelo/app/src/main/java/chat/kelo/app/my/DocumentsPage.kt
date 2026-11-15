package chat.kelo.app.my

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.chat.MarkdownText
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.PushHost
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.rememberSkeletonShown
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import java.io.IOException
import kotlin.coroutines.cancellation.CancellationException

/** The server's per-file limit (home.MAX_UPLOAD, "up to 100 MB": under Cloudflare's 100 MB body limit); the storage cap and the
 * server's free disk can refuse less. Checked here before anything is sent. */
internal const val DOCS_MAX_UPLOAD = 99_000_000L

/** What the server takes: PDF, Word, Excel, photos, videos and sound. */
private val UPLOAD_TYPES = arrayOf(
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroEnabled.12",
    "image/*",
    "video/*",
    "audio/*",
)

/** The picked file as the upload needs it: its name, its size (-1 unknown) and type. Its bytes are read only while it goes up. */
private data class Picked(val name: String, val size: Long, val type: String?)

/** Upload progress: bytes sent of the total (-1 unknown). */
private class Progress {
    var sent by mutableLongStateOf(0L)
    var total by mutableLongStateOf(-1L)
    var fraction by mutableFloatStateOf(0f)
}

/**
 * The files the user has uploaded, and only those (nothing the assistant came with is listed here): photos and videos as a grid
 * of previews, documents and sound files as rows. A photo opens on the whole screen; a video or sound file plays in the app.
 */
@Composable
fun DocumentsPage(onBack: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val resolver = LocalContext.current.contentResolver
    val scope = rememberCoroutineScope()
    var docs by remember { mutableStateOf<List<Upload>?>(null) }
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    var msg by remember { mutableStateOf("") }
    var uploading by remember { mutableStateOf<Job?>(null) }
    val progress = remember { Progress() }
    val uploaded = stringResource(R.string.docs_uploaded)
    val tooLarge = stringResource(R.string.docs_too_large)
    val readFailed = stringResource(R.string.docs_read_failed)

    suspend fun load() {
        docs = try { sessions.requireApi().get<List<Upload>>("/api/uploads") } catch (_: ApiError) { emptyList() }
    }
    LaunchedEffect(Unit) { load() }

    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        msg = ""
        progress.sent = 0; progress.total = -1; progress.fraction = 0f
        uploading = scope.launch {
            try {
                val p = withContext(Dispatchers.IO) { describe(resolver, uri) }
                if (p.size > DOCS_MAX_UPLOAD) throw TooLarge()
                sessions.requireApi().uploadStream(p.name, p.size, p.type, open = {
                    resolver.openInputStream(uri) ?: throw IOException("no stream")
                }) { sent, total ->
                    if (sent > DOCS_MAX_UPLOAD) throw TooLarge()
                    val f = if (total > 0) (sent.toFloat() / total).coerceIn(0f, 1f) else 0f
                    if (sent == total || f - progress.fraction >= 0.005f || sent - progress.sent >= 1_000_000) {
                        progress.sent = sent; progress.total = total; progress.fraction = f
                    }
                }
                load()
                msg = uploaded
            } catch (_: TooLarge) {
                msg = tooLarge
            } catch (e: CancellationException) {
                throw e
            } catch (_: IOException) {
                msg = readFailed
            } catch (e: ApiError) {
                msg = if (e.cause is TooLarge) tooLarge else e.detail
            } finally {
                uploading = null
            }
        }
    }

    val list = docs
    val shown = open?.let { name -> list?.firstOrNull { it.name == name } ?: Upload("uploads/$name", kind = MediaKind.of(name)) }
    // a file's page slides over the list; the back gesture steps back to it
    PushHost(
        state = open.orEmpty(),
        depth = { if (it.isEmpty()) 0 else 1 },
        back = if (open == null) null else "",
        onBack = { open = null },
        modifier = Modifier.fillMaxSize(),
    ) { state ->
        val u = if (state.isNotEmpty()) (shown?.takeIf { it.name == state } ?: Upload("uploads/$state", kind = MediaKind.of(state))) else null
        if (u != null) {
            FileViewer(u, onClose = { open = null }, onDeleted = { open = null; scope.launch { load() } })
        } else {
            DocumentList(
                list, uploading != null, progress, msg, onBack,
                onUpload = { pick.launch(UPLOAD_TYPES) }, onCancel = { uploading?.cancel(); uploading = null },
                onOpen = { open = it },
            )
        }
    }
}

@Composable
private fun DocumentList(
    docs: List<Upload>?, busy: Boolean, progress: Progress, msg: String, onBack: () -> Unit,
    onUpload: () -> Unit, onCancel: () -> Unit, onOpen: (String) -> Unit,
) {
    SubPage(stringResource(R.string.docs_title), onBack) {
        MyCard {
            if (busy) {
                val total = progress.total
                if (total > 0) LinearProgressIndicator(progress = { progress.fraction }, modifier = Modifier.fillMaxWidth(), color = Kelo.colors.accent)
                else LinearProgressIndicator(modifier = Modifier.fillMaxWidth(), color = Kelo.colors.accent)
                Row(Modifier.fillMaxWidth()) {
                    MutedText(
                        if (total > 0) stringResource(R.string.docs_upload_progress, storageSize(progress.sent), storageSize(total), (progress.fraction * 100).toInt())
                        else stringResource(R.string.docs_uploading),
                        small = true, modifier = Modifier.weight(1f),
                    )
                    LinkButton(stringResource(R.string.cancel), onClick = onCancel)
                }
            } else {
                PrimaryButton(stringResource(R.string.docs_upload), onClick = onUpload)
            }
            MutedText(stringResource(R.string.docs_hint), small = true)
            if (msg.isNotEmpty()) MutedText(msg, small = true)
        }
        if (rememberSkeletonShown(docs == null)) {
            SkeletonRows(3)
        } else if (docs != null && docs.isEmpty()) {
            EmptyNote(stringResource(R.string.docs_empty))
        } else if (!docs.isNullOrEmpty()) {
            val media = docs.filter { it.isMedia }.sortedByDescending { it.modified }
            val files = docs.filterNot { it.isMedia }
            if (media.isNotEmpty()) {
                SectionLabel(stringResource(R.string.docs_media))
                MediaGrid(media, onOpen = { onOpen(it.name) })
            }
            if (files.isNotEmpty()) {
                if (media.isNotEmpty()) SectionLabel(stringResource(R.string.docs_files))
                MenuGroup {
                    files.forEach { d ->
                        row {
                            val day = stringResource(R.string.docs_size_day, fileSize(d.bytes), shortDay(d.modified))
                            MenuRow(
                                if (d.kind == "audio") KeloIcons.Music else KeloIcons.File, d.name, onClick = { onOpen(d.name) },
                                value = d.duration?.takeIf { d.kind == "audio" }?.let { stringResource(R.string.docs_size_day, clipLength(it), day) } ?: day,
                            )
                        }
                    }
                }
            }
        }
    }
}

/** One file: a photo on the whole screen, a video or sound file playing, or a document's text. Save the original, or delete. */
@Composable
private fun FileViewer(u: Upload, onClose: () -> Unit, onDeleted: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val resolver = LocalContext.current.contentResolver
    val scope = rememberCoroutineScope()
    var err by remember(u.name) { mutableStateOf("") }
    var confirm by remember { mutableStateOf(false) }
    var savedMsg by remember { mutableStateOf("") }
    val saved = stringResource(R.string.docs_saved)
    val saveFailed = stringResource(R.string.docs_save_failed)
    val saving = stringResource(R.string.docs_saving)

    // the original: the user picks where it goes, then it is streamed with the token straight into that file
    val save = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument(u.type.ifEmpty { "application/octet-stream" })) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        savedMsg = saving
        scope.launch {
            try {
                withContext(Dispatchers.IO) {
                    (resolver.openOutputStream(uri) ?: throw IOException("no stream")).use { out ->
                        sessions.requireApi().downloadTo("/api/uploads/${KeloApi.segment(u.name)}/file", out)
                    }
                }
                savedMsg = saved
            } catch (e: ApiError) {
                savedMsg = ""
                err = e.detail
            } catch (_: IOException) {
                savedMsg = saveFailed
            }
        }
    }
    val note = listOf(savedMsg, err).filter { it.isNotEmpty() }.joinToString(" · ")

    when (u.kind) {
        "image" -> PhotoPage(u, onClose, onSave = { save.launch(u.name) }, onDelete = { confirm = true }, note = note)
        "video", "audio" -> PlayerPage(u, onClose, onSave = { save.launch(u.name) }, onDelete = { confirm = true }, note = note)
        else -> DocumentText(u.name, onClose, onSave = { save.launch(u.name) }, onDelete = { confirm = true }, savedMsg = savedMsg, err = err)
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
                    } catch (e: ApiError) {
                        err = e.detail
                    }
                }
            },
        )
    }
}

/** A document: its text as the assistant reads it, the original to save, and delete. */
@Composable
private fun DocumentText(name: String, onClose: () -> Unit, onSave: () -> Unit, onDelete: () -> Unit, savedMsg: String, err: String) {
    val sessions = LocalAppContainer.current.sessions
    var text by remember(name) { mutableStateOf<String?>(null) }
    var readErr by remember(name) { mutableStateOf("") }
    LaunchedEffect(name) {
        try {
            text = sessions.requireApi().get<UploadText>("/api/uploads/${KeloApi.segment(name)}/text").text
        } catch (e: ApiError) {
            readErr = e.detail
        }
    }
    SubPage(name, onClose) {
        MyCard {
            Row(Modifier.fillMaxWidth()) {
                LinkButton(stringResource(R.string.docs_download), onClick = onSave)
                Row(Modifier.weight(1f)) {}
                LinkButton(stringResource(R.string.delete), onClick = onDelete, danger = true)
            }
            if (savedMsg.isNotEmpty()) MutedText(savedMsg, small = true)
            val t = text
            when {
                err.isNotEmpty() -> ErrorText(err, small = true)
                readErr.isNotEmpty() -> ErrorText(readErr, small = true)
                t == null -> MutedText(stringResource(R.string.docs_reading), small = true)
                else -> MarkdownText(t)
            }
        }
    }
}

private class TooLarge : IOException()

/** The picked file's name (with a suffix the server recognises), size and type; nothing is read yet. */
private fun describe(resolver: ContentResolver, uri: Uri): Picked {
    var name = uri.lastPathSegment?.substringAfterLast('/') ?: "document"
    var size = -1L
    resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
        if (c.moveToFirst()) {
            c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let { i -> c.getString(i)?.let { name = it } }
            c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { size = c.getLong(it) }
        }
    }
    val type = resolver.getType(uri)
    return Picked(MediaKind.withSuffix(name, type), size, type)
}
