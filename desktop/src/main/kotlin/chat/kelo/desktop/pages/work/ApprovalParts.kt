// Ported from android/kelo .../agent/ApprovalParts.kt (keep in step with it).
package chat.kelo.desktop.pages.work

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.desktop.ui.Card
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import chat.kelo.app.agent.*
import chat.kelo.app.work.*
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.EmptyNote
import chat.kelo.desktop.ui.Badge
import chat.kelo.desktop.ui.Tag
import androidx.compose.runtime.collectAsState
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo

/**
 * One approval waiting for an answer. A payment is answered here the same way as in the chat: with a card kept only on this
 * phone, Allow first asks for the owner's check and sends the card.
 */
@Composable
internal fun PendingCard(vm: WorkModel, a: PendingApproval, working: Boolean) {
    if (a.isHumanCheck) {
        Card {
            HumanCheckBody(a, working, onSolve = { vm.openLive(a.tab, control = true) }, onSkip = { vm.answer(a, false) })
        }
        return
    }
    Card {
        PendingApprovalBody(a, working, null) { ok, rememberSite, rememberSchedule ->
            vm.answer(a, ok, remember = rememberSite, rememberSchedule = rememberSchedule)
        }
    }
}

/**
 * What a waiting approval shows and offers, the same on the 작업 page and in the chat: a payment as the chat's purchase card;
 * anything else as the question, the site, what it will do, when it was asked, 거절 / 허용, "<site>에선 늘 허용" when the server allows
 * remembering it, and "keep allowing in this scheduled job" when that is offered. [onAnswer]: (allow, remember the site, remember
 * for the scheduled job); sending an end-to-end card first is the caller's part.
 */
@Composable
internal fun PendingApprovalBody(
    a: PendingApproval, working: Boolean, cardError: chat.kelo.app.wallet.CardSendError?,
    onAnswer: (Boolean, Boolean, Boolean) -> Unit,
) {
    val c = Kelo.colors
    val payment = a.event.payment
    if (a.event.urgent) chat.kelo.desktop.chat.UrgentLine(a.event.missed)
    a.event.grant?.takeIf { payment == null }?.let { grant ->
        // the setup card of a "when X happens, do Y" task, the same as in the chat
        chat.kelo.desktop.chat.TaskGrantApproval(grant, open = !working) { ok -> onAnswer(ok, false, false) }
        return
    }
    if (payment != null) {
        // a payment: where, what, how much and which card; a card kept end-to-end on a phone can only be sent from that phone,
        // so here it can be refused but not allowed (as the desktop chat's purchase card)
        val card = a.event.card
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(KeloIcons.Card, contentDescription = null, tint = c.accent)
                Text(stringResource(R.string.chat_purchase_question), style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f))
            }
            // the order at once: site, items, where it goes, what pays, coupon, total (chat/OrderParts.kt)
            chat.kelo.desktop.chat.OrderDetails(payment, card)
            card?.reasonText?.takeIf { it.isNotEmpty() }?.let { MutedText(stringResource(R.string.chat_purchase_not_auto, it), small = true) }
            if (card?.needsCard == true) MutedText(stringResource(R.string.chat_card_error_not_on_phone), small = true)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                SecondaryButton(stringResource(R.string.chat_approval_deny), enabled = !working, modifier = Modifier.weight(1f), onClick = { onAnswer(false, false, false) })
                if (card?.needsCard != true) PrimaryButton(stringResource(R.string.chat_approval_allow), enabled = !working, modifier = Modifier.weight(1f), onClick = { onAnswer(true, false, false) })
            }
        }
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(KeloIcons.Shield, contentDescription = null, tint = c.accent)
            Text(chat.kelo.app.chat.ChatAsks.question(a.event.ask) ?: a.event.label.ifBlank { a.event.name }.let { w -> stringResource(R.string.chat_approval_ask, chat.kelo.app.chat.ProviderConsentRules.askWord(w, appLocale().language), chat.kelo.app.chat.ProviderConsentRules.objectParticle(w)) }, style = MaterialTheme.typography.titleMedium,
                color = c.ink, modifier = Modifier.weight(1f))
        }
        if (a.site.isNotBlank() && a.event.lines.isEmpty()) MutedText(a.site, small = true)
        WorkApprovalFacts(a.event.lines, a.event.detail)
        if (a.event.items.isNotEmpty()) chat.kelo.desktop.chat.ApprovalItems(a.event.items)
        if (a.event.pin != null) MutedText(stringResource(R.string.desktop_pin_allow_note), small = true)
        if (a.askedAt > 0) Text(stringResource(R.string.agent_perm_asked, relText(a.askedAt)), style = MaterialTheme.typography.labelSmall, color = c.muted)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SecondaryButton(stringResource(R.string.chat_approval_deny), enabled = !working, modifier = Modifier.weight(1f), onClick = { onAnswer(false, false, false) })
            PrimaryButton(stringResource(R.string.chat_approval_allow), enabled = !working, modifier = Modifier.weight(1f), onClick = { onAnswer(true, false, false) })
        }
        if (a.canRemember && a.site.isNotBlank()) {
            LinkButton(stringResource(R.string.agent_perm_always, a.site), onClick = { onAnswer(true, true, false) })
        }
        if (a.canRememberSchedule) {
            LinkButton(stringResource(R.string.approval_keep_for_schedule), onClick = { onAnswer(true, false, true) })
        }
    }
}

/**
 * A site's check whether a person is there, handed to the user (the same on the 작업 page and in the chat): who asks, one short
 * sentence, "직접 풀기" (the tab live with control taken; the card ends by itself once the check is gone) and "이 사이트 건너뛰기"
 * (the agent goes on without that site and says so). The buttons sit one under the other so long words in any language fit.
 */
@Composable
internal fun HumanCheckBody(a: PendingApproval, working: Boolean, onSolve: () -> Unit, onSkip: () -> Unit) {
    val c = Kelo.colors
    val site = a.site.ifBlank { stringResource(R.string.handover_this_site) }
    // a sign-in's second step (approve on the phone, or a code) is not a person check: its own words, the server's step first
    val verify = a.isVerification
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(if (verify) KeloIcons.Shield else KeloIcons.User, contentDescription = null, tint = c.accent)
            Text(stringResource(if (verify) R.string.handover_verify_title else R.string.handover_title, site),
                style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f))
        }
        val step = a.event.check?.name.orEmpty()
        if (verify && step.isNotBlank()) Text(step, style = MaterialTheme.typography.bodyMedium, color = c.ink)
        Text(stringResource(if (verify) R.string.handover_verify_body else R.string.handover_body), style = MaterialTheme.typography.bodyMedium, color = c.muted)
        if (a.askedAt > 0) Text(stringResource(R.string.agent_perm_asked, relText(a.askedAt)), style = MaterialTheme.typography.labelSmall, color = c.muted)
        PrimaryButton(stringResource(if (verify) R.string.handover_verify_open else R.string.handover_solve), enabled = !working, modifier = Modifier.fillMaxWidth(), onClick = onSolve)
        SecondaryButton(stringResource(R.string.handover_skip), enabled = !working, modifier = Modifier.fillMaxWidth(), onClick = onSkip)
    }
}

@Composable
internal fun RuleRow(r: ApprovalRule, onRevoke: () -> Unit) {
    val c = Kelo.colors
    var confirm by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 10.dp, bottom = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(r.site, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(ruleKind(r), style = MaterialTheme.typography.bodySmall, color = c.muted)
            Text(pluralStringResource(R.plurals.agent_rule_uses, r.uses, r.uses), style = MaterialTheme.typography.labelSmall, color = c.muted)
        }
        IconButton(onClick = { confirm = true }) {
            Icon(KeloIcons.Trash, contentDescription = stringResource(R.string.agent_rule_revoke, r.site), tint = c.muted)
        }
    }
    if (confirm) {
        ConfirmDialog(stringResource(R.string.agent_rule_revoke_confirm, r.site), stringResource(R.string.agent_rule_revoke_yes), onDismiss = { confirm = false }, onYes = onRevoke)
    }
}

/** What a rule allows, in the app's words (the server's label for kinds it does not know). */
@Composable
private fun ruleKind(r: ApprovalRule): String = when (r.kind) {
    "browser_action" -> stringResource(R.string.agent_rule_kind_browser)
    "login" -> stringResource(R.string.agent_rule_kind_login)
    else -> r.kindLabel.ifBlank { r.kind }
}


/** An approval's readable pairs (hosts rather than URLs), or its one sentence; the raw arguments are never shown. */
@Composable
internal fun WorkApprovalFacts(lines: List<chat.kelo.app.chat.ApprovalLine>, detail: String?) {
    val c = Kelo.colors
    if (lines.isNotEmpty()) {
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(c.bubble).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            lines.forEach { l ->
                if (l.full) Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    if (l.label.isNotEmpty()) Text(l.label, style = MaterialTheme.typography.bodySmall, color = c.muted)
                    Text(l.value, style = MaterialTheme.typography.bodyMedium, color = c.ink)
                } else Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (l.label.isNotEmpty()) Text(l.label, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.widthIn(min = 56.dp, max = 140.dp))
                    Text(l.value, style = MaterialTheme.typography.bodySmall, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                }
            }
        }
    } else if (!detail.isNullOrBlank()) {
        Text(detail, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    }
}
