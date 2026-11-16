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

/** Reading what was picked (on a background thread). */
object AttachmentReader {

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

    /**
     * The most a photo may be while the phone reads it whole (a PNG that already fits goes up as it is; the scaled JPEG is made in memory).
     * This is the phone's own memory limit, not the server's: the server's photo limit (15 MB) comes from the upload limits.
     */
    const val PHOTO_MEMORY_MAX = 32_000_000L

    /** A file as it is, read only while it goes up (any size: the upload plan decides, with the server's limits). */
    fun document(resolver: ContentResolver, uri: Uri): Prepared {
        val (display, size) = displayName(resolver, uri)
        val type = resolver.getType(uri)
        return Prepared(chat.kelo.app.my.MediaKind.withSuffix(display ?: "document", type), null, type, length = size,
            open = { resolver.openInputStream(uri) ?: throw AttachmentUnreadable() })
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
                if (total > max) throw AttachmentTooLarge(max)
                out.write(buf, 0, n)
            }
            out.toByteArray()
        } ?: throw AttachmentUnreadable()

    /** A photo: upright, at most [ImageScale.MAX_EDGE] on the long edge, as JPEG (a PNG that already fits goes as it is). */
    fun photo(resolver: ContentResolver, uri: Uri, max: Long = PHOTO_MEMORY_MAX, now: Long = System.currentTimeMillis()): Prepared {
        val (display, _) = displayName(resolver, uri)
        val mime = resolver.getType(uri)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        // only the size is read here (decodeStream gives no bitmap for that)
        resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds); true } ?: throw AttachmentUnreadable()
        val w = bounds.outWidth
        val h = bounds.outHeight
        if (w <= 0 || h <= 0) throw AttachmentUnreadable()
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
        } ?: throw AttachmentUnreadable()
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
        if (bytes.size > max) throw AttachmentTooLarge(max)
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
