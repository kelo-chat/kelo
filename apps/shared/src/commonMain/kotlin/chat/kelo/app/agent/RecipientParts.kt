package chat.kelo.app.agent

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.theme.Kelo

/** The kind of data in the user's words (the server names the kind; a kind this app does not know reads as "data"). */
@Composable
fun trailKindWord(kind: String): String = string(
    when (kind) {
        "mail" -> Res.string.trail_kind_mail
        "file" -> Res.string.trail_kind_file
        "memory" -> Res.string.trail_kind_memory
        "contacts" -> Res.string.trail_kind_contacts
        "calendar" -> Res.string.trail_kind_calendar
        "photos" -> Res.string.trail_kind_photos
        "messages" -> Res.string.trail_kind_messages
        "notes" -> Res.string.trail_kind_notes
        "vault" -> Res.string.trail_kind_vault
        "profile" -> Res.string.trail_kind_profile
        "web" -> Res.string.trail_kind_web
        "channel" -> Res.string.trail_kind_channel
        "device" -> Res.string.trail_kind_device
        "connector" -> Res.string.trail_kind_connector
        else -> Res.string.trail_kind_other
    },
)

/** The recipient as the user reads it; the public link has its own words. */
@Composable
fun trailRecipientWord(recipient: String): String = RecipientRules.shown(recipient)?.let { TokenText.fit(it, 40) } ?: string(Res.string.trail_recipient_link)

/**
 * What the data-trail card says: which kind of data goes to which recipient, with the titles of the items (never their content).
 * One block per offer; long recipients and spaceless titles are cut instead of widening the card.
 */
@Composable
fun RecipientOffers(offers: List<TrailOffer>) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(c.bubble).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        offers.forEach { o ->
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(string(Res.string.trail_goes_to, trailKindWord(o.kind), trailRecipientWord(o.recipient)), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                if (o.titles.isNotEmpty()) {
                    Text(o.titles.joinToString(" · ") { TokenText.fit(it, 40) }, style = MaterialTheme.typography.bodySmall, color = c.muted,
                        maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

/**
 * The data-trail card's body and its three answers, one under the other so a long label in any language fits: Allow once
 * (primary), Always for this recipient, Deny.
 */
@Composable
fun RecipientChoices(offers: List<TrailOffer>, enabled: Boolean, onAnswer: (RecipientRules.Answer) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        RecipientOffers(offers)
        PrimaryButton(string(Res.string.trail_allow_once), enabled = enabled, modifier = Modifier.fillMaxWidth(), onClick = { onAnswer(RecipientRules.Answer.ONCE) })
        SecondaryButton(string(Res.string.trail_allow_always), enabled = enabled, modifier = Modifier.fillMaxWidth(), onClick = { onAnswer(RecipientRules.Answer.ALWAYS) })
        SecondaryButton(string(Res.string.chat_approval_deny), enabled = enabled, modifier = Modifier.fillMaxWidth(), onClick = { onAnswer(RecipientRules.Answer.DENY) })
    }
}

/** One "always for this recipient": the recipient, the kind of data and since when, and a trash button (asks first). */
@Composable
fun RecipientAllowRow(r: RecipientAllow, onRemove: () -> Unit) {
    val c = Kelo.colors
    var confirm by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }
    val who = trailRecipientWord(r.recipient)
    val kind = trailKindWord(r.kind)
    androidx.compose.foundation.layout.Row(
        Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 10.dp, bottom = 10.dp),
        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(who, style = MaterialTheme.typography.bodyLarge, fontWeight = androidx.compose.ui.text.font.FontWeight.Medium, color = c.ink,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(kind, style = MaterialTheme.typography.bodySmall, color = c.muted)
            if (r.createdAt > 0) Text(string(Res.string.recipient_allow_since, relText(r.createdAt)), style = MaterialTheme.typography.labelSmall, color = c.muted)
        }
        androidx.compose.material3.IconButton(onClick = { confirm = true }) {
            androidx.compose.material3.Icon(chat.kelo.app.ui.icons.KeloIcons.Trash, contentDescription = string(Res.string.recipient_allow_remove, who), tint = c.muted)
        }
    }
    if (confirm) {
        chat.kelo.app.ui.components.ConfirmDialog(string(Res.string.recipient_allow_remove_confirm, kind, who), string(Res.string.agent_rule_revoke_yes),
            onDismiss = { confirm = false }, onYes = onRemove)
    }
}
