package chat.kelo.app.agent

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo

@Composable
private fun timesText(n: Int): String = when {
    n == 1 -> stringResource(R.string.task_grant_times_one)
    n > 1 -> stringResource(R.string.task_grant_times, n)
    else -> stringResource(R.string.task_grant_no_count)
}

/**
 * The setup card: the question, the trigger and the action, then one block per thing allowed (purchase, message, change, login) with
 * its limits, the card's own auto-pay limit next to the amount asked for, "ends by itself" for a one-shot task, and when time matters
 * what a late answer would miss. Deny / Allow while it is open; [outcome] is the chip once answered.
 */
@Composable
fun TaskGrantApproval(g: TaskGrantAsk, open: Boolean, outcome: @Composable () -> Unit = {}, onAnswer: (Boolean) -> Unit) {
    val c = Kelo.colors
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.task_grant_question), style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f))
            outcome()
        }
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.bubble).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (g.taskName.isNotBlank()) {
                Text(listOf(g.taskName, g.taskWhen).filter { it.isNotBlank() }.joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall, color = c.muted)
            }
            GrantPart(KeloIcons.Bell, stringResource(R.string.task_grant_when)) {
                Text(g.trigger, style = MaterialTheme.typography.bodyMedium, color = c.ink)
            }
            GrantPart(KeloIcons.Spark, stringResource(R.string.task_grant_does)) {
                Text(g.action, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = c.ink)
            }
            g.purchase?.let { p ->
                HorizontalDivider(color = c.line)
                GrantPart(KeloIcons.Package, stringResource(R.string.task_grant_buy)) {
                    Text(p.what, style = MaterialTheme.typography.bodyMedium, color = c.ink)
                    if (p.site.isNotBlank()) Text(p.site, style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
                GrantPart(KeloIcons.Receipt, stringResource(R.string.task_grant_limits)) {
                    Text(stringResource(R.string.task_grant_per_time, p.maxAmountText), style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.SemiBold, color = c.ink)
                    Text(timesText(p.maxCount), style = MaterialTheme.typography.bodySmall, color = c.ink)
                    if (p.monthlyCapText.isNotBlank()) Text(stringResource(R.string.task_grant_monthly, p.monthlyCapText),
                        style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
                p.card?.let { card ->
                    GrantPart(KeloIcons.Card, stringResource(R.string.task_grant_card)) {
                        Text(listOf(card.label, card.last4.takeIf { it.isNotBlank() }?.let { "••$it" }).filterNotNull().joinToString(" "),
                            style = MaterialTheme.typography.bodyMedium, color = c.ink)
                        val line = when (TaskGrantRules.cardLine(p)) {
                            CardLimitLine.AUTO_LIMIT -> stringResource(R.string.task_grant_card_limit, card.autoLimitText, p.maxAmountText)
                            CardLimitLine.NOT_AUTO -> stringResource(R.string.task_grant_card_not_auto, p.maxAmountText)
                            CardLimitLine.PHONE_ONLY -> stringResource(R.string.task_grant_card_phone_only)
                            CardLimitLine.NONE -> ""
                        }
                        if (line.isNotEmpty()) {
                            Text(line, style = MaterialTheme.typography.bodySmall,
                                color = if (TaskGrantRules.aboveCardLimit(p) || card.phoneOnly) c.warn else c.muted)
                        }
                    }
                }
                if (p.shipping.isNotBlank()) {
                    GrantPart(KeloIcons.MapPin, stringResource(R.string.task_grant_ship_to)) {
                        Text(p.shipping, style = MaterialTheme.typography.bodySmall, color = c.ink)
                    }
                }
            }
            g.send?.let { s ->
                HorizontalDivider(color = c.line)
                GrantPart(KeloIcons.Send, stringResource(R.string.task_grant_send_to)) {
                    Text(TaskGrantRules.recipients(s), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                    Text(timesText(s.maxCount), style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
                if (s.template.isNotBlank()) {
                    GrantPart(KeloIcons.Chat, stringResource(R.string.task_grant_message)) {
                        Text(s.template, style = MaterialTheme.typography.bodyMedium, color = c.ink)
                        if (s.varies.isNotBlank()) Text(stringResource(R.string.task_grant_varies, s.varies), style = MaterialTheme.typography.bodySmall, color = c.muted)
                    }
                }
            }
            g.change?.let { ch ->
                HorizontalDivider(color = c.line)
                GrantPart(KeloIcons.Edit, stringResource(R.string.task_grant_change)) {
                    Text(ch.what, style = MaterialTheme.typography.bodyMedium, color = c.ink)
                    Text(TaskGrantRules.places(ch), style = MaterialTheme.typography.bodySmall, color = c.muted)
                    Text(timesText(ch.maxCount), style = MaterialTheme.typography.bodySmall, color = c.muted)
                    Text(stringResource(if (ch.allowDelete) R.string.task_grant_delete_yes else R.string.task_grant_delete_no),
                        style = MaterialTheme.typography.bodySmall, color = if (ch.allowDelete) c.warn else c.muted)
                }
            }
            g.login?.let { l ->
                HorizontalDivider(color = c.line)
                GrantPart(KeloIcons.Key, stringResource(R.string.task_grant_login)) {
                    Text(stringResource(R.string.task_grant_login_line, l.label, l.site), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                }
            }
        }
        if (g.once) Text(stringResource(R.string.task_grant_once), style = MaterialTheme.typography.bodySmall, color = c.muted)
        Text(stringResource(R.string.task_grant_after), style = MaterialTheme.typography.bodySmall, color = c.muted)
        if (g.urgent) UrgentLine(g.missed)
        if (open) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                SecondaryButton(stringResource(R.string.chat_approval_deny), onClick = { onAnswer(false) }, modifier = Modifier.weight(1f))
                PrimaryButton(stringResource(R.string.chat_approval_allow), onClick = { onAnswer(true) }, modifier = Modifier.weight(1f))
            }
        }
    }
}

/** "급해요 · 답이 늦으면 자리가 다른 사람에게 가요": a card of a task marked urgent at setup. */
@Composable
fun UrgentLine(missed: String) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(c.dangerSoft).padding(horizontal = 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(KeloIcons.Alert, contentDescription = null, tint = c.dangerInk, modifier = Modifier.size(16.dp))
        Text(
            if (missed.isNotBlank()) stringResource(R.string.task_grant_urgent_missed, missed) else stringResource(R.string.task_grant_urgent),
            style = MaterialTheme.typography.bodySmall, color = c.dangerInk, modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun GrantPart(icon: ImageVector, title: String, content: @Composable () -> Unit) {
    val c = Kelo.colors
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(icon, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
            Text(title, style = MaterialTheme.typography.labelMedium, color = c.muted)
        }
        Column(Modifier.padding(start = 22.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) { content() }
    }
}
