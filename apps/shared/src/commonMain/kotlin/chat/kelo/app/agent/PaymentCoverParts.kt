package chat.kelo.app.agent

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.LocalHaptics
import chat.kelo.app.ui.theme.Kelo

/** Where the schedule or task an order was approved for is named: "For the schedule “Weekly shop”", or "For one task". */
@Composable
private fun coverFor(forSchedule: Boolean, name: String): String = when {
    !forSchedule -> string(Res.string.paycover_for_task)
    name.isBlank() -> string(Res.string.paycover_for_a_schedule)
    else -> string(Res.string.paycover_for_schedule, TokenText.fit(name, 30))
}

/**
 * 마이 → 권한 → "Payments approved in advance": the switch (off: every payment asks), each order approved in advance (what, the
 * all-in cap, until when, for which task or schedule) and each schedule's payment allow (schedule, shop, payment method), every
 * one removable. Rows stay listed while the switch is off (they are just not used).
 */
@Composable
fun PaymentCoverSection(
    cover: PaymentCover, onSwitch: (Boolean) -> Unit, onRemoveOrder: (Preapproval) -> Unit, onRemoveAllow: (PayAllow) -> Unit,
) {
    val c = Kelo.colors
    GroupCard { PaymentCoverSwitchRow(cover.enabled, onSwitch) }
    if (cover.empty) {
        MutedText(string(Res.string.paycover_empty), small = true, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
        return
    }
    if (!cover.enabled) MutedText(string(Res.string.paycover_off_note), small = true, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
    if (cover.orders.isNotEmpty()) {
        MutedText(string(Res.string.paycover_orders), small = true, modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 4.dp))
        GroupCard {
            cover.orders.forEachIndexed { i, p ->
                if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                PreapprovalRow(p, onRemove = { onRemoveOrder(p) })
            }
        }
    }
    if (cover.allows.isNotEmpty()) {
        MutedText(string(Res.string.paycover_allows), small = true, modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 4.dp))
        GroupCard {
            cover.allows.forEachIndexed { i, a ->
                if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                PayAllowRow(a, onRemove = { onRemoveAllow(a) })
            }
        }
    }
}

/** "Allow payments approved in advance" with its one-line explanation; the whole row toggles. */
@Composable
fun PaymentCoverSwitchRow(on: Boolean, onToggle: (Boolean) -> Unit) {
    val c = Kelo.colors
    val haptics = LocalHaptics.current
    val label = string(Res.string.paycover_switch)
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Switch) { haptics.tick(); onToggle(!on) }.padding(start = 16.dp, end = 12.dp, top = 12.dp, bottom = 12.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(label, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink)
            Text(string(Res.string.paycover_switch_note), style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(
            checked = on, onCheckedChange = null,
            modifier = Modifier.semantics { contentDescription = label },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
}

/** One order approved in advance: what, the all-in cap, until when, for which task or schedule, and a trash button (asks first). */
@Composable
fun PreapprovalRow(p: Preapproval, onRemove: () -> Unit) {
    val c = Kelo.colors
    var confirm by remember { mutableStateOf(false) }
    val what = TokenText.fit(PaymentCoverRules.what(p.intent, p.items), 40)
    Row(
        Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 10.dp, bottom = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(what, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis)
            Text(string(Res.string.paycover_cap, chat.kelo.app.wallet.moneyText(p.maxTotal, p.currency)), style = MaterialTheme.typography.bodySmall, color = c.ink)
            if (p.expiresAt > 0) Text(string(Res.string.paycover_until, chat.kelo.app.my.dayTime(p.expiresAt * 1000)), style = MaterialTheme.typography.bodySmall, color = c.muted)
            Text("${coverFor(p.forSchedule, p.ownerName)} · ${string(Res.string.paycover_one_order)}", style = MaterialTheme.typography.labelSmall, color = c.muted)
        }
        IconButton(onClick = { confirm = true }) {
            Icon(KeloIcons.Trash, contentDescription = string(Res.string.paycover_remove, what), tint = c.muted)
        }
    }
    if (confirm) {
        ConfirmDialog(string(Res.string.paycover_remove_order_confirm), string(Res.string.agent_rule_revoke_yes), onDismiss = { confirm = false }, onYes = onRemove)
    }
}

/** One schedule's payment allow: the schedule, the shop and the payment method it covers, and a trash button (asks first). */
@Composable
fun PayAllowRow(a: PayAllow, onRemove: () -> Unit) {
    val c = Kelo.colors
    var confirm by remember { mutableStateOf(false) }
    val name = a.scheduleName.ifBlank { string(Res.string.paycover_for_a_schedule) }
    Row(
        Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 10.dp, bottom = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(TokenText.fit(name, 40), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
            val shop = a.merchantOrigin.removePrefix("https://").removePrefix("http://").trimEnd('/')
            Text(listOf(shop, a.paymentLabel).filter { it.isNotBlank() }.joinToString(" · ") { TokenText.fit(it, 30) },
                style = MaterialTheme.typography.bodySmall, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis)
            Text(string(Res.string.paycover_allow_scope), style = MaterialTheme.typography.labelSmall, color = c.muted)
        }
        IconButton(onClick = { confirm = true }) {
            Icon(KeloIcons.Trash, contentDescription = string(Res.string.paycover_remove, name), tint = c.muted)
        }
    }
    if (confirm) {
        ConfirmDialog(string(Res.string.paycover_remove_allow_confirm, TokenText.fit(name, 30)), string(Res.string.agent_rule_revoke_yes), onDismiss = { confirm = false }, onYes = onRemove)
    }
}

/**
 * The approval card of `order_preapprove`, in words: what is approved, the most it may cost all in, until when, and that it covers
 * one order only; the must-haves, delivery, shops and what Kelo may decide when the order has them. Nothing is paid by this card.
 */
@Composable
fun PreapprovalFacts(p: PreapprovalAsk) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(c.bubble).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        PreapprovalFact(string(Res.string.preapprove_what), PaymentCoverRules.whatLines(p).map { TokenText.fit(it, 40) })
        PreapprovalFact(string(Res.string.preapprove_cap), listOf(string(Res.string.preapprove_cap_value, PaymentCoverRules.capText(p))), strong = true)
        if (p.expiresAt > 0) PreapprovalFact(string(Res.string.preapprove_until), listOf(chat.kelo.app.my.dayTime(p.expiresAt * 1000)))
        PreapprovalFact(string(Res.string.preapprove_covers), listOf(string(Res.string.preapprove_covers_value), coverFor(p.forSchedule, "")))
        if (p.requirements.isNotEmpty()) PreapprovalFact(string(Res.string.preapprove_requirements), p.requirements.map { TokenText.fit(it, 40) })
        if (p.shipping.isNotBlank()) PreapprovalFact(string(Res.string.preapprove_shipping), listOf(TokenText.fit(p.shipping, 40)))
        if (p.merchantPolicy.isNotBlank()) PreapprovalFact(string(Res.string.preapprove_shops), listOf(TokenText.fit(p.merchantPolicy, 40)))
        if (p.delegatedChoices.isNotEmpty()) PreapprovalFact(string(Res.string.preapprove_delegated), p.delegatedChoices.map { TokenText.fit(it, 40) })
    }
}

@Composable
private fun PreapprovalFact(label: String, lines: List<String>, strong: Boolean = false) {
    val c = Kelo.colors
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.widthIn(min = 56.dp, max = 100.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            lines.forEach { Text(it, style = if (strong) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodySmall,
                fontWeight = if (strong) FontWeight.SemiBold else null, color = c.ink) }
        }
    }
}
