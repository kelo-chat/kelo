package chat.kelo.app.platform

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.result.contract.ActivityResultContract
import androidx.activity.result.contract.ActivityResultContracts
import chat.kelo.app.chat.AttachmentReader
import chat.kelo.app.chat.AttachmentUnreadable
import chat.kelo.app.my.MediaKind
import chat.kelo.app.ui.ActivityHost
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.IOException
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume

/** The system's photo and document pickers behind [FilePicker]: one activity-result registration per call, dropped when it answers. */
class AndroidFilePicker(private val host: ActivityHost) : FilePicker {
    private val ids = AtomicInteger()

    override suspend fun pickImage(maxBytes: Long): PickedImage? {
        val activity = host.current ?: return null
        val uri = ask(ActivityResultContracts.PickVisualMedia(), androidx.activity.result.PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) ?: return null
        val p = withContext(Dispatchers.IO) { AttachmentReader.photo(activity.contentResolver, uri, maxBytes) }
        return PickedImage(p.bytes ?: throw AttachmentUnreadable(), p.type ?: "image/jpeg")
    }

    override suspend fun pickFile(types: List<String>): PickedFile? {
        val activity = host.current ?: return null
        val uri = ask(ActivityResultContracts.OpenDocument(), types.toTypedArray()) ?: return null
        val resolver = activity.contentResolver
        return withContext(Dispatchers.IO) { describe(resolver, uri) }
    }

    override suspend fun saveFile(name: String, type: String): SaveTarget? {
        val activity = host.current ?: return null
        val uri = ask(ActivityResultContracts.CreateDocument(type.ifEmpty { "application/octet-stream" }), name) ?: return null
        val resolver = activity.contentResolver
        return SaveTarget { resolver.openOutputStream(uri) ?: throw IOException("no stream") }
    }

    private suspend fun <I> ask(contract: ActivityResultContract<I, Uri?>, input: I): Uri? {
        val registry = host.current?.activityResultRegistry ?: return null
        return suspendCancellableCoroutine { cont ->
            val key = "kelo-file-${ids.incrementAndGet()}"
            var launcher: androidx.activity.result.ActivityResultLauncher<I>? = null
            launcher = registry.register(key, contract) { uri -> launcher?.unregister(); if (cont.isActive) cont.resume(uri) }
            cont.invokeOnCancellation { launcher?.unregister() }
            try { launcher.launch(input) } catch (e: Exception) { launcher.unregister(); if (cont.isActive) cont.resume(null) }
        }
    }

    /** The picked file's name (with a suffix the server recognises), size and type; nothing is read yet. */
    private fun describe(resolver: ContentResolver, uri: Uri): PickedFile {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: "document"
        var size = -1L
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let { i -> c.getString(i)?.let { name = it } }
                c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { size = c.getLong(it) }
            }
        }
        val type = resolver.getType(uri)
        return PickedFile(MediaKind.withSuffix(name, type), size, type.orEmpty()) { resolver.openInputStream(uri) ?: throw IOException("no stream") }
    }
}
