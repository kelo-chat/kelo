package chat.kelo.app.chat

import android.content.ContentResolver
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.media.ThumbnailUtils
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import java.io.ByteArrayOutputStream
import java.io.IOException

/**
 * A photo or file picked with "+", shown above the composer while it uploads (PUT /api/uploads/{name}) and sent with the next
 * message. [sent] / [total] are bytes (total is 0 while the photo is still being made smaller). [stored] is the name the server
 * keeps it under once it is up.
 */
data class Attachment(
    val id: String,
    val name: String,
    val image: Boolean,
    val state: AttachState = AttachState.UPLOADING,
    val sent: Long = 0,
    val total: Long = 0,
    val stored: String? = null,
    val error: AttachError? = null,
    val thumbnail: ImageBitmap? = null,
) {
    /** 0..1, or null while the size is not known yet */
    val progress: Float? get() = if (total > 0) (sent.toFloat() / total).coerceIn(0f, 1f) else null
}

enum class AttachState { UPLOADING, DONE, FAILED }

sealed interface AttachError {
    /** The server's own words (e.g. which kinds of file it takes) */
    data class Server(val text: String) : AttachError
    data object TooLarge : AttachError
    data object Network : AttachError
    data object Unreadable : AttachError
}

/**
 * What gets uploaded for one pick: the name to store it under, the bytes (a photo, made smaller) or a stream to read as it goes up
 * ([open]; a document or video is never held in memory), their type, and a small picture for the chip. [length]: the size, -1
 * unknown. [done] runs once the upload ended either way (a shared file's copy is deleted).
 */
class Prepared(
    val name: String,
    val bytes: ByteArray?,
    val type: String?,
    val thumbnail: ImageBitmap? = null,
    val length: Long = bytes?.size?.toLong() ?: -1L,
    val open: (() -> java.io.InputStream)? = null,
    val done: () -> Unit = {},
)

/** The words the message uses for its attachments (from string resources, so they follow the app's language). */
class AttachmentWords(
    /** One line per attachment under the typed text, e.g. "📎 %1$s" */
    val line: (String) -> String,
    /** The whole message when nothing was typed, e.g. "%1$s 내용을 읽고 요약해줘." with the names */
    val ask: (String) -> String,
    /** Between names in [ask], e.g. ", " */
    val separator: String,
)

object AttachmentText {
    /** The message sent: the typed text with a line per attachment under it, or (nothing typed) a request to read them. */
    fun compose(text: String, names: List<String>, words: AttachmentWords): String {
        val typed = text.trim()
        if (names.isEmpty()) return typed
        if (typed.isEmpty()) return words.ask(names.joinToString(words.separator))
        return typed + "\n\n" + names.joinToString("\n") { words.line(it) }
    }
}

/** How a photo is made smaller before it is uploaded. */
object ImageScale {
    const val MAX_EDGE = 2048
    const val JPEG_QUALITY = 85
    const val THUMB_PX = 160

    /** The size that fits [maxEdge] on the long edge, keeping the shape (unchanged when it already fits). */
    fun target(w: Int, h: Int, maxEdge: Int = MAX_EDGE): Pair<Int, Int> {
        val long = maxOf(w, h)
        if (long <= maxEdge || long <= 0) return w to h
        val f = maxEdge.toDouble() / long
        return maxOf(1, Math.round(w * f).toInt()) to maxOf(1, Math.round(h * f).toInt())
    }

    /** The largest power of two that still decodes at least [maxEdge] on the long edge (BitmapFactory's inSampleSize). */
    fun sampleSize(w: Int, h: Int, maxEdge: Int = MAX_EDGE): Int {
        var s = 1
        while (maxOf(w, h) / (s * 2) >= maxEdge) s *= 2
        return s
    }

    /**
     * Whether the picked bytes can go up as they are: a PNG that already fits. Everything else is re-encoded as JPEG, which also
     * drops what a camera writes into a photo (the place it was taken).
     */
    fun keepOriginal(w: Int, h: Int, mime: String?): Boolean = mime.equals("image/png", ignoreCase = true) && maxOf(w, h) <= MAX_EDGE

    /** The name a re-encoded photo is stored under: its own name with .jpg, or photo-<time>.jpg. */
    fun jpegName(display: String?, now: Long): String {
        val base = display?.substringAfterLast('/')?.substringBeforeLast('.')?.trim().orEmpty()
        return (base.ifEmpty { "photo-$now" }) + ".jpg"
    }
}

/** Reading what was picked (on a background thread). */
object AttachmentReader {
    class TooLarge : IOException()
    class Unreadable : IOException()

    fun displayName(resolver: ContentResolver, uri: Uri): Pair<String?, Long> {
        var name: String? = null
        var size = -1L
        runCatching {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let { i -> name = c.getString(i) }
                    c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { i -> size = c.getLong(i) }
                }
            }
        }
        return name to size
    }

    /** A file as it is (at most [max] bytes), read only while it goes up. */
    fun document(resolver: ContentResolver, uri: Uri, max: Long): Prepared {
        val (display, size) = displayName(resolver, uri)
        if (size > max) throw TooLarge()
        val type = resolver.getType(uri)
        return Prepared(chat.kelo.app.my.MediaKind.withSuffix(display ?: "document", type), null, type, length = size,
            open = { resolver.openInputStream(uri) ?: throw Unreadable() })
    }

    fun readAll(resolver: ContentResolver, uri: Uri, max: Long): ByteArray =
        resolver.openInputStream(uri)?.use { input ->
            val out = ByteArrayOutputStream()
            val buf = ByteArray(64 * 1024)
            var total = 0L
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                total += n
                if (total > max) throw TooLarge()
                out.write(buf, 0, n)
            }
            out.toByteArray()
        } ?: throw Unreadable()

    /** A photo: upright, at most [ImageScale.MAX_EDGE] on the long edge, as JPEG (a PNG that already fits goes as it is). */
    fun photo(resolver: ContentResolver, uri: Uri, max: Long, now: Long = System.currentTimeMillis()): Prepared {
        val (display, _) = displayName(resolver, uri)
        val mime = resolver.getType(uri)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        // only the size is read here (decodeStream gives no bitmap for that)
        resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds); true } ?: throw Unreadable()
        val w = bounds.outWidth
        val h = bounds.outHeight
        if (w <= 0 || h <= 0) throw Unreadable()
        val rotation = runCatching { resolver.openInputStream(uri)?.use { rotationOf(ExifInterface(it)) } }.getOrNull() ?: 0

        if (rotation == 0 && ImageScale.keepOriginal(w, h, mime)) {
            val bytes = readAll(resolver, uri, max)
            val thumb = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply {
                inSampleSize = ImageScale.sampleSize(w, h, ImageScale.THUMB_PX)
            })
            return Prepared(display ?: "image-$now.png", bytes, "image/png", thumb?.let(::thumbOf))
        }

        val decoded = resolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = ImageScale.sampleSize(w, h) })
        } ?: throw Unreadable()
        val (tw, th) = ImageScale.target(decoded.width, decoded.height)
        var bmp = if (tw != decoded.width || th != decoded.height) Bitmap.createScaledBitmap(decoded, tw, th, true) else decoded
        if (bmp !== decoded) decoded.recycle()
        if (rotation != 0) {
            val m = Matrix().apply { postRotate(rotation.toFloat()) }
            val turned = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
            if (turned !== bmp) bmp.recycle()
            bmp = turned
        }
        val out = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.JPEG, ImageScale.JPEG_QUALITY, out)
        val thumb = thumbOf(bmp)
        bmp.recycle()
        val bytes = out.toByteArray()
        if (bytes.size > max) throw TooLarge()
        return Prepared(ImageScale.jpegName(display, now), bytes, "image/jpeg", thumb)
    }

    private fun thumbOf(bmp: Bitmap): ImageBitmap =
        ThumbnailUtils.extractThumbnail(bmp, ImageScale.THUMB_PX, ImageScale.THUMB_PX).asImageBitmap()

    private fun rotationOf(exif: ExifInterface): Int = when (exif.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
        ExifInterface.ORIENTATION_ROTATE_90 -> 90
        ExifInterface.ORIENTATION_ROTATE_180 -> 180
        ExifInterface.ORIENTATION_ROTATE_270 -> 270
        else -> 0
    }
}
