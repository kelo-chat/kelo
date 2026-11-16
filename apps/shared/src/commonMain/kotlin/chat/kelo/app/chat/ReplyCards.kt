package chat.kelo.app.chat

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
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
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppServices
import chat.kelo.app.res.*
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.LocalHaptics
import chat.kelo.app.ui.platform.decodeImage
import chat.kelo.app.ui.platform.formFactor
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.pressShrink
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** The words a screen reader says for a picture card (the picture itself adds nothing): "option, detail, recommended". */
object ReplyCardRules {
    fun label(option: String, card: ReplyCard, recommendedWord: String): String =
        listOfNotNull(option.trim().ifEmpty { null }, card.detail.trim().ifEmpty { null }, recommendedWord.takeIf { card.recommended }).joinToString(", ")
}

/**
 * A choice question's options as a vertical list of tappable cards (a picture, the option, its detail line, a mark on the one Kelo
 * suggests). A tap does what the option's button does ([onPick]); [picked] is the option chosen but not yet sent (an answer to write).
 */
@Composable
fun ReplyCardList(options: List<String>, cards: List<ReplyCard>, picked: String?, onPick: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().widthIn(max = formFactor(phone = 420.dp, desktop = 520.dp)), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEachIndexed { i, o -> ReplyCardRow(o, cards.getOrNull(i) ?: ReplyCard(), selected = picked == o, onClick = { onPick(o) }) }
    }
}

@Composable
private fun ReplyCardRow(option: String, card: ReplyCard, selected: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    val source = remember { MutableInteractionSource() }
    val haptics = LocalHaptics.current
    val shape = RoundedCornerShape(12.dp)
    val recommended = string(Res.string.reply_recommended)
    val said = ReplyCardRules.label(option, card, recommended)
    Row(
        Modifier.fillMaxWidth().pressShrink(source).clip(shape).background(if (selected) c.mine else c.card)
            .border(1.dp, if (card.recommended) c.accent else c.line, shape)
            .clickable(interactionSource = source, indication = null, role = Role.Button, onClick = { haptics.tick(); onClick() })
            .semantics(mergeDescendants = true) { contentDescription = said }
            .padding(8.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically,
    ) {
        CardThumb(card.image)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(option, style = MaterialTheme.typography.bodyMedium, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis)
            if (card.detail.isNotBlank()) {
                Text(card.detail, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
        if (card.recommended) Icon(KeloIcons.Spark, contentDescription = recommended, tint = c.accent, modifier = Modifier.size(20.dp))
    }
}

/** The card's square picture, read with the signed-in API like the chat's pictures; an empty tile while there is none or it loads. */
@Composable
private fun CardThumb(img: ReplyCardImage?) {
    val c = Kelo.colors
    val sessions = LocalAppServices.current.sessions
    var bitmap by remember(img?.url) { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(img?.url) {
        val url = img?.url?.takeIf { it.isNotEmpty() } ?: return@LaunchedEffect
        bitmap = runCatching {
            val bytes = sessions.requireApi().download(url)
            withContext(Dispatchers.Default) { decodeImage(bytes) }
        }.getOrNull()
    }
    val shape = RoundedCornerShape(10.dp)
    Box(Modifier.size(THUMB).clip(shape).background(c.bg).border(1.dp, c.line, shape)) {
        bitmap?.let { Image(it, contentDescription = null, modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop) }
    }
}

private val THUMB = 80.dp
