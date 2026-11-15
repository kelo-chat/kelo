package chat.kelo.app.notify

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64

/** Browser pictures for the ongoing notification: a data: URL decoded once (the last one is kept, the same picture comes again on
 *  every update), scaled to at most 720 px wide. */
internal object NotifyPictures {
    private var lastUrl: String? = null
    private var lastBitmap: Bitmap? = null

    @Synchronized
    fun bitmap(dataUrl: String?): Bitmap? {
        if (dataUrl == null) return null
        if (dataUrl == lastUrl) return lastBitmap
        val bmp = try {
            val bytes = Base64.decode(dataUrl.substringAfter("base64,", ""), Base64.DEFAULT)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.let { b ->
                if (b.width <= 720) b else Bitmap.createScaledBitmap(b, 720, (b.height * 720f / b.width).toInt().coerceAtLeast(1), true)
            }
        } catch (e: IllegalArgumentException) { null }
        lastUrl = dataUrl; lastBitmap = bmp
        return bmp
    }
}
