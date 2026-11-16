package chat.kelo.app.chat

import android.net.Uri
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.content.MediaType
import androidx.compose.foundation.content.ReceiveContentListener
import androidx.compose.foundation.content.TransferableContent
import androidx.compose.foundation.content.consume
import androidx.compose.foundation.content.hasMediaType

/**
 * Pictures that come into the chat's text field instead of text: the screenshot Gboard or Samsung Keyboard shows above the keys
 * (the keyboard's "commit content"), a long-press Paste of an image on the clipboard, or a drop. They go the same way as a photo
 * picked with "+" (the tray, the size limit, removable before sending); text that comes with them is still typed in.
 */
@OptIn(ExperimentalFoundationApi::class)
object PastedImages {
    /** The field's listener: takes every image item and hands the rest back so text still pastes. */
    fun receiver(onImages: (List<Uri>) -> Unit): ReceiveContentListener = ReceiveContentListener { content: TransferableContent ->
        if (!content.hasMediaType(MediaType.Image)) return@ReceiveContentListener content
        val taken = ArrayList<Uri>()
        val rest = content.consume { item ->
            val uri = item.uri
            val image = uri != null && (item.text == null || item.text.isEmpty())
            if (image) taken += uri!!
            image
        }
        if (taken.isNotEmpty()) onImages(taken)
        rest
    }
}
