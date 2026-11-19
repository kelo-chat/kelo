package chat.kelo.app.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.agent.clockText
import chat.kelo.app.call.CallLine
import chat.kelo.app.call.CallLineRow
import chat.kelo.app.res.Res
import chat.kelo.app.res.call_chat_hide
import chat.kelo.app.res.call_chat_length
import chat.kelo.app.res.call_chat_show
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.text.plural
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo

/** A call in the chat: the handset, "Call · 3 min · 2:10 PM", the owner's first words; a tap opens the call's lines (owner 2026-11-20). */
@Composable
fun CallCardRow(card: ChatItem.CallCard, open: Boolean, agentName: String, onToggle: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val n = CallCards.minutes(card)
    val length = plural(Res.plurals.call_chat_length, n, n)
    val at = card.lines.first().at
    val title = if (at > 0L) "$length · ${clockText(at / 1000.0)}" else length
    val preview = remember(card) { CallCards.preview(card) }
    val action = string(if (open) Res.string.call_chat_hide else Res.string.call_chat_show)
    val shape = RoundedCornerShape(16.dp)
    Column(modifier.fillMaxWidth().clip(shape).background(c.card).border(1.dp, c.line, shape).testTag("chat.call.card")) {
        Row(
            Modifier.fillMaxWidth().clickable(onClickLabel = action, role = Role.Button, onClick = onToggle)
                .padding(horizontal = 14.dp, vertical = 12.dp).testTag("chat.call.head"),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(KeloIcons.Handset, contentDescription = null, tint = c.accent, modifier = Modifier.size(18.dp))
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (preview.isNotEmpty()) {
                    Text(preview, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            Icon(KeloIcons.ChevronDown, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp).rotate(if (open) 180f else 0f))
        }
        if (open) {
            Column(
                Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, bottom = 12.dp).testTag("chat.call.lines"),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                card.lines.forEachIndexed { i, m -> if (m.text.isNotBlank()) CallLineRow(CallLine(i, m.mine, m.text), agentName, photo = null) }
            }
        }
    }
}
