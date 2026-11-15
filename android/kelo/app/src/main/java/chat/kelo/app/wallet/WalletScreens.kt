package chat.kelo.app.wallet

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.IconButton
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.my.Bar
import chat.kelo.app.my.ConfirmDialog
import chat.kelo.app.my.EmptyNote
import chat.kelo.app.my.MenuGroup
import chat.kelo.app.my.MenuRow
import chat.kelo.app.my.MyCard
import chat.kelo.app.my.SectionLabel
import chat.kelo.app.my.SubPage
import chat.kelo.app.my.appLocale
import chat.kelo.app.my.whenText
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * The list: new auto-payments, the cards as small card faces, and the way to the limits and the history. How the cards are kept
 * safe is one tap away behind the (i) in the bar; a new card is added with the button at the bottom.
 */
@Composable
internal fun WalletList(
    info: WalletInfo, keyId: String?, notices: List<WalletPayment>, onBack: () -> Unit, go: (WalletView) -> Unit, onChanged: () -> Unit = {},
) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var explain by remember { mutableStateOf(false) }
    // a card's row "turn on auto-pay": the notice, then this phone's own copy opened with the owner's check (no retyping)
    val turnOn = rememberAutoPayOn(go = go, onChanged = onChanged)
    // auto-pay works only with Keyguard on: with it off, auto-pay cards show as paused (owner's decision)
    val keyguardOn = chat.kelo.app.keyguard.rememberKeyguardOn().on
    val keyguardPause = keyguardOn == false && info.cards.any { AutoPayGate.pausedForKeyguard(it, keyguardOn) }
    Box(Modifier.fillMaxSize()) {
        SubPage(stringResource(R.string.wallet_title), onBack, actions = {
            IconButton(onClick = { explain = true }) { Icon(KeloIcons.Info, contentDescription = stringResource(R.string.wallet_safe_title), tint = c.ink) }
        }) {
            if (notices.isNotEmpty()) {
                MyCard {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(KeloIcons.Card, contentDescription = null, tint = c.success, modifier = Modifier.size(20.dp))
                        Text(
                            pluralStringResource(R.plurals.wallet_notices_title, notices.size, notices.size),
                            fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge,
                        )
                    }
                    notices.take(5).forEach { p ->
                        Text(
                            stringResource(R.string.wallet_notice_line, p.site, p.amountText.ifEmpty { moneyText(p.amount, p.currency) }, p.cardLabel, whenText(p.at)),
                            style = MaterialTheme.typography.bodySmall, color = c.ink,
                        )
                    }
                }
            }
            if (info.paused) {
                MyCard(onClick = { go(WalletView.Limits) }) {
                    Text(stringResource(R.string.wallet_paused_banner), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                }
            }
            if (info.cards.isEmpty()) Note(stringResource(R.string.wallet_no_cards))
            Column {
                info.cards.forEach { card -> CardRow(card, keyId, keyguardOn, onTurnOn = { turnOn(card) }) { go(WalletView.Card(card.id)) } }
            }
            // where Kelo ships what it orders, next to what it pays with (absent on a server without addresses)
            info.shipping?.let { list ->
                ShippingSection(list, onOpen = { p -> go(WalletView.ShippingEdit(p.id)) }, onAdd = { go(WalletView.ShippingEdit(null)) })
            }
            SectionLabel(stringResource(R.string.wallet_auto_section))
            val autoCount = info.autoCount
            MenuGroup {
                row {
                    // how many cards really pay on their own now: never just "on" while every card asks
                    MenuRow(
                        KeloIcons.Shield, stringResource(R.string.wallet_limits_title), onClick = { go(WalletView.Limits) },
                        value = when {
                            info.paused || keyguardPause -> stringResource(R.string.wallet_auto_paused_value)
                            autoCount == 0 -> stringResource(R.string.wallet_auto_none_value)
                            else -> pluralStringResource(R.plurals.wallet_auto_cards_value, autoCount, autoCount)
                        },
                    )
                }
                row { MenuRow(KeloIcons.Receipt, stringResource(R.string.wallet_history_title), onClick = { go(WalletView.Payments) }) }
            }
            if (info.cards.isNotEmpty() && autoCount == 0 && !info.paused) Note(stringResource(R.string.wallet_auto_none_note))
            if (autoCount > 0 && !keyguardPause) Note(stringResource(R.string.wallet_first_site_rule))
            if (info.scheduleGrants.isNotEmpty()) {
                SectionLabel(stringResource(R.string.wallet_schedule_section))
                chat.kelo.app.agent.ScheduleGrantRows(info.scheduleGrants, showSchedule = true) { g ->
                    scope.launch { runCatching { chat.kelo.app.agent.ScheduleGrantCalls.revoke(sessions.requireApi(), g) }.onSuccess { onChanged() } }
                }
                Note(stringResource(R.string.grant_section_note))
            }
            androidx.compose.foundation.layout.Spacer(Modifier.size(72.dp))
        }
        ExtendedFloatingActionButton(
            onClick = { go(WalletView.Add) }, containerColor = c.accent, contentColor = c.onAccent,
            icon = { Icon(KeloIcons.Plus, contentDescription = null) }, text = { Text(stringResource(R.string.wallet_add_title)) },
            modifier = Modifier.align(Alignment.BottomEnd).navigationBarsPadding().padding(16.dp),
        )
    }
    if (explain) {
        AlertDialog(
            onDismissRequest = { explain = false }, containerColor = c.card,
            icon = { Icon(KeloIcons.Lock, contentDescription = null, tint = c.accent) },
            title = { Text(stringResource(R.string.wallet_safe_title), color = c.ink) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(stringResource(R.string.wallet_safe_body), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                    Text(stringResource(R.string.wallet_relock_note), color = c.muted, style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = { TextButton(onClick = { explain = false }) { Text(stringResource(R.string.ok), color = c.accent) } },
        )
    }
}

/**
 * One card in the list: its face, its name, expiry and last use, whether it pays on its own or why it asks, and what state it is
 * in. A card saved to ask each time offers turning auto-pay on right here ([onTurnOn]: the number and CVC are asked for again).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CardRow(card: WalletCard, keyId: String?, keyguardOn: Boolean?, onTurnOn: () -> Unit, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(androidx.compose.foundation.shape.RoundedCornerShape(12.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 4.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        CardFaceThumb(CardBrand.of(card.brand), card.last4, dimmed = card.expired)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(card.label, color = c.ink, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            cardSubtitle(card)?.let { Text(it, color = c.muted, style = MaterialTheme.typography.bodySmall) }
            Text(
                stringResource(R.string.wallet_card_meta, Expiry.display(card.expMonth, card.expYear), whenText(card.lastUsed)),
                color = c.muted, style = MaterialTheme.typography.bodySmall,
            )
            autoLine(card, keyguardOn)?.let { Text(it, color = c.ink, style = MaterialTheme.typography.bodySmall) }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                StatusTags(card, keyId, keyguardOn)
            }
            if (card.offersTurnOn) LinkButton(stringResource(R.string.wallet_auto_turn_on_card), onClick = onTurnOn)
        }
    }
}

/** Whether the card pays on its own, or why it asks each time, in one line (null for a card that cannot pay at all). */
@Composable
internal fun autoLine(card: WalletCard, keyguardOn: Boolean? = null): String? = when {
    card.expired || !card.usable -> null
    AutoPayGate.pausedForKeyguard(card, keyguardOn) -> stringResource(R.string.wallet_auto_paused_keyguard)
    card.autoNow -> stringResource(R.string.wallet_row_pays_auto)
    else -> reasonText(card.reason)
}

@Composable
internal fun reasonText(reason: String): String = stringResource(
    when (reason) {
        WalletCard.REASON_NO_SERVER_COPY -> R.string.wallet_reason_no_server_copy
        WalletCard.REASON_PAUSED -> R.string.wallet_reason_paused
        WalletCard.REASON_VAULT_LOCKED -> R.string.wallet_reason_vault_locked
        else -> R.string.wallet_reason_not_auto
    },
)

/** The line under a card's nickname: brand and last 4 ("Visa ••4242"); null when the nickname already is exactly that. */
internal fun cardSubtitle(card: WalletCard): String? {
    val b = CardBrand.of(card.brand)
    return CardMask.subtitle(card.label, brandName(b) ?: brandShort(b), card.last4)
}

@Composable
private fun StatusTags(card: WalletCard, keyId: String?, keyguardOn: Boolean? = null) {
    if (card.isDefault) CardTag(stringResource(R.string.wallet_tag_default), TagTone.ACCENT)
    // paused until Keyguard is on: "Paused", not "Asks each time" (an auto-only card cannot be asked on this phone)
    if (AutoPayGate.pausedForKeyguard(card, keyguardOn)) CardTag(stringResource(R.string.wallet_auto_paused_value), TagTone.DANGER)
    else if (card.autoNow) CardTag(stringResource(R.string.wallet_tag_auto), TagTone.ACCENT)
    else if (!card.expired && card.usable) CardTag(stringResource(R.string.wallet_tag_asks))
    when (CardStatusRules.of(card, keyId)) {
        CardStatus.EXPIRED -> CardTag(stringResource(R.string.wallet_tag_expired), TagTone.DANGER)
        CardStatus.NOT_ON_PHONE -> CardTag(stringResource(R.string.wallet_tag_not_here), TagTone.DANGER)
        CardStatus.UNFINISHED -> CardTag(stringResource(R.string.wallet_tag_unfinished), TagTone.DANGER)
        CardStatus.AUTO_ONLY -> CardTag(stringResource(R.string.wallet_tag_auto_only))
        CardStatus.USABLE -> CardTag(stringResource(R.string.wallet_tag_ready))
    }
}

/**
 * One card: its nickname (the title) with brand and last 4, its state, whether it is the default card, auto-pay on or off, the
 * payments Kelo made with it, and removing it. The auto-pay limits live on the wallet list, not here.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun CardPage(
    card: WalletCard, keyId: String?, onBack: () -> Unit, go: (WalletView) -> Unit, onChanged: () -> Unit, onDeleted: () -> Unit,
    cards: List<WalletCard> = listOf(card),
) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    val c = Kelo.colors
    var msg by remember { mutableStateOf("") }
    var renaming by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf(false) }
    var turningOff by remember { mutableStateOf(false) }
    var newDefault by remember { mutableStateOf<String?>(null) }   // deleting the default: the card picked to take its place
    val turnOn = rememberAutoPayOn(go = go, onChanged = onChanged)
    val keyguard = chat.kelo.app.keyguard.rememberKeyguardOn()
    var keyguardFor by remember { mutableStateOf(false) }
    if (keyguardFor) chat.kelo.app.keyguard.KeyguardNeededDialog(
        onEnabled = { keyguardFor = false; keyguard.refresh(); onChanged() }, onDismiss = { keyguardFor = false })
    val container = LocalAppContainer.current
    val context = androidx.compose.ui.platform.LocalContext.current
    val status = CardStatusRules.of(card, keyId)
    fun calls() = WalletCalls(sessions.requireApi())
    fun act(block: suspend WalletCalls.() -> Unit) {
        msg = ""
        scope.launch {
            try { calls().block(); haptics.confirm() } catch (e: ApiError) { msg = e.detail; haptics.reject() }
        }
    }

    SubPage(card.label, onBack) {
        CardFaceLarge(
            CardBrand.of(card.brand), stringResource(R.string.wallet_card_masked, CardMask.last4(card.last4)), card.holder,
            Expiry.display(card.expMonth, card.expYear), modifier = Modifier.padding(horizontal = 24.dp),
        )
        cardSubtitle(card)?.let { Text(it, color = c.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.align(Alignment.CenterHorizontally)) }
        MyCard {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) { StatusTags(card, keyId, keyguard.on) }
            MutedText(stringResource(R.string.wallet_card_dates, whenText(card.createdAt), whenText(card.lastUsed)), small = true)
        }
        when (status) {
            CardStatus.NOT_ON_PHONE, CardStatus.UNFINISHED -> MyCard {
                ErrorText(stringResource(R.string.wallet_not_here_body), small = true)
                SecondaryButton(stringResource(R.string.wallet_reenter_title), onClick = { go(WalletView.Reenter(card.id, autoPay = false)) }, modifier = Modifier.fillMaxWidth())
            }
            CardStatus.AUTO_ONLY -> MyCard {
                MutedText(stringResource(R.string.wallet_auto_only_body), small = true)
                SecondaryButton(stringResource(R.string.wallet_reenter_title), onClick = { go(WalletView.Reenter(card.id, autoPay = false)) }, modifier = Modifier.fillMaxWidth())
            }
            CardStatus.EXPIRED -> MyCard { ErrorText(stringResource(R.string.wallet_expired_body), small = true) }
            CardStatus.USABLE -> {}
        }

        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.wallet_auto_title), color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                Switch(
                    checked = card.autoPay, enabled = !card.expired,
                    onCheckedChange = { on -> if (on) turnOn(card) else turningOff = true },
                    colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.bg),
                )
            }
            MutedText(stringResource(if (card.autoPay) R.string.wallet_auto_on_body else R.string.wallet_auto_off_body), small = true)
            // an auto-pay card on a server without Keyguard: paused (each payment asks on the phone) until Keyguard is on
            if (AutoPayGate.pausedForKeyguard(card, keyguard.on)) {
                Text(stringResource(R.string.wallet_auto_paused_keyguard), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                SecondaryButton(stringResource(R.string.keyguard_turn_on_named), onClick = { keyguardFor = true }, modifier = Modifier.fillMaxWidth())
            }
            if (!card.autoNow && !card.expired && card.usable) {
                // why this card asks each time, and the way to change it (this phone opens its own copy; no retyping)
                Text(reasonText(card.reason), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                if (!card.autoPay) {
                    SecondaryButton(stringResource(R.string.wallet_auto_turn_on_card), onClick = { turnOn(card) }, modifier = Modifier.fillMaxWidth())
                    MutedText(stringResource(R.string.wallet_auto_from_phone), small = true)
                }
            }
            MutedText(stringResource(R.string.wallet_first_site_rule), small = true)
            MutedText(stringResource(R.string.wallet_auto_tip), small = true)
        }
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.wallet_default_title), color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                if (card.isDefault) CardTag(stringResource(R.string.wallet_tag_default), TagTone.ACCENT)
            }
            MutedText(stringResource(R.string.wallet_default_on), small = true)
            if (!card.isDefault && !card.expired) {
                SecondaryButton(stringResource(R.string.wallet_make_default), onClick = { act { makeDefault(card.id); onChanged() } }, modifier = Modifier.fillMaxWidth())
            }
        }
        MenuGroup {
            row { MenuRow(KeloIcons.Edit, stringResource(R.string.wallet_nickname), onClick = { renaming = true }, value = card.label) }
        }
        SectionLabel(stringResource(R.string.wallet_card_history))
        CardHistory(card.id) { link ->
            when (link) {
                PaymentLink.Chat -> container.openRequests.value = chat.kelo.app.notify.OpenTarget.Chat
                is PaymentLink.Task -> container.openRequests.value = chat.kelo.app.notify.OpenTarget.Task(link.id)
                PaymentLink.None -> {}
            }
        }
        MenuGroup { row { MenuRow(KeloIcons.Trash, stringResource(R.string.wallet_delete), onClick = { deleting = true }, danger = true) } }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
    }

    if (turningOff) {
        ConfirmDialog(
            text = stringResource(
                if (status == CardStatus.USABLE) R.string.wallet_auto_off_confirm else R.string.wallet_auto_off_confirm_unusable,
            ),
            yes = stringResource(R.string.wallet_auto_turn_off), danger = status != CardStatus.USABLE,
            onDismiss = { turningOff = false },
            onYes = { act { patch(card.id, buildJsonObject { put("auto_pay", false) }); onChanged() } },
        )
    }
    val choices = DefaultCardRules.choicesOnDelete(card, cards)
    if (deleting && choices != null) {
        // the default card with two or more others: which one Kelo pays with from now on
        val picked = newDefault ?: DefaultCardRules.suggested(choices)?.id
        AlertDialog(
            onDismissRequest = { deleting = false; newDefault = null }, containerColor = c.card,
            title = { Text(stringResource(R.string.wallet_delete_default_title), color = c.ink) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(stringResource(R.string.wallet_delete_default_body, card.label), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                    choices.forEach { other ->
                        Row(
                            Modifier.fillMaxWidth().clip(androidx.compose.foundation.shape.RoundedCornerShape(8.dp))
                                .clickable(role = Role.RadioButton) { newDefault = other.id }.padding(vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            androidx.compose.material3.RadioButton(selected = picked == other.id, onClick = { newDefault = other.id },
                                colors = androidx.compose.material3.RadioButtonDefaults.colors(selectedColor = c.accent))
                            Column(Modifier.weight(1f)) {
                                Text(other.label, color = c.ink, style = MaterialTheme.typography.bodyMedium)
                                cardSubtitle(other)?.let { Text(it, color = c.muted, style = MaterialTheme.typography.bodySmall) }
                            }
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    deleting = false
                    val next = picked
                    newDefault = null
                    act { delete(card.id, next); WalletConsent(context).forget(card.id); onDeleted() }
                }, enabled = picked != null) { Text(stringResource(R.string.delete), color = c.danger) }
            },
            dismissButton = { TextButton(onClick = { deleting = false; newDefault = null }) { Text(stringResource(R.string.cancel), color = c.ink) } },
        )
    } else if (deleting) {
        ConfirmDialog(
            text = stringResource(R.string.wallet_delete_confirm, card.label), yes = stringResource(R.string.delete),
            onDismiss = { deleting = false },
            onYes = { act { delete(card.id); WalletConsent(context).forget(card.id); onDeleted() } },
        )
    }
    if (renaming) RenameDialog(card.label, onDismiss = { renaming = false }) { name ->
        act { patch(card.id, buildJsonObject { put("label", name.trim()) }); onChanged() }
    }
}

/**
 * Turning auto-pay on for a card: the notice (ticked), then this phone's own end-to-end copy opened with the owner's fingerprint,
 * face or screen lock and sent once to the auto-pay endpoint (the number is never typed again, never shown or logged). A card this
 * phone holds no copy of (added on another device) says so, with typing the number as the last resort. Returns "start for this card".
 */
@Composable
internal fun rememberAutoPayOn(go: (WalletView) -> Unit, onChanged: () -> Unit): (WalletCard) -> Unit {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    val auth = rememberCardAuthenticator()
    val c = Kelo.colors
    var asking by remember { mutableStateOf<WalletCard?>(null) }
    var notHere by remember { mutableStateOf<WalletCard?>(null) }
    var failed by remember { mutableStateOf(false) }
    // auto-pay needs Keyguard: with it off (or not known yet) the dialog comes first, and turning Keyguard on goes on from there
    val keyguard = chat.kelo.app.keyguard.rememberKeyguardOn()
    var needKeyguard by remember { mutableStateOf<WalletCard?>(null) }
    needKeyguard?.let { card ->
        chat.kelo.app.keyguard.KeyguardNeededDialog(
            onEnabled = { needKeyguard = null; keyguard.refresh(); asking = card },
            onDismiss = { needKeyguard = null },
        )
    }
    val title = stringResource(R.string.wallet_auth_auto_title)
    val subtitleOf = stringResource(R.string.wallet_auth_auto_subtitle)
    asking?.let { card ->
        AutoPayDialog(onDismiss = { asking = null }, onConfirm = {
            asking = null
            scope.launch {
                val r = runCatching {
                    turnOnAutoPay(WalletCalls(sessions.requireApi()), auth, card, title, String.format(subtitleOf, card.label))
                }.getOrElse { CardSendError.OTHER }
                when (r) {
                    null -> { haptics.confirm(); onChanged() }
                    CardSendError.NOT_ON_PHONE -> notHere = card
                    CardSendError.KEYGUARD_REQUIRED -> needKeyguard = card   // the server says so: the same dialog
                    CardSendError.CANCELLED -> {}
                    else -> { failed = true; haptics.reject() }
                }
            }
        })
    }
    notHere?.let { card ->
        AlertDialog(
            onDismissRequest = { notHere = null }, containerColor = c.card,
            title = { Text(stringResource(R.string.wallet_auto_not_here_title), color = c.ink) },
            text = { Text(stringResource(R.string.wallet_auto_not_here_body), color = c.ink, style = MaterialTheme.typography.bodyMedium) },
            confirmButton = {
                TextButton(onClick = { notHere = null; go(WalletView.Reenter(card.id, autoPay = true)) }) {
                    Text(stringResource(R.string.wallet_auto_enter_number), color = c.accent)
                }
            },
            dismissButton = { TextButton(onClick = { notHere = null }) { Text(stringResource(R.string.cancel), color = c.ink) } },
        )
    }
    if (failed) {
        AlertDialog(
            onDismissRequest = { failed = false }, containerColor = c.card,
            text = { Text(stringResource(R.string.wallet_auto_on_failed), color = c.ink, style = MaterialTheme.typography.bodyMedium) },
            confirmButton = { TextButton(onClick = { failed = false }) { Text(stringResource(R.string.ok), color = c.accent) } },
        )
    }
    return { card -> if (AutoPayGate.mayTurnOn(keyguard.on)) asking = card else needKeyguard = card }
}

/** The payments Kelo made with one card, newest first, older ones a page at a time; each opens its chat or task when known. */
@Composable
private fun CardHistory(cardId: String, onOpen: (PaymentLink) -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var items by remember(cardId) { mutableStateOf<List<WalletPayment>?>(null) }
    var next by remember(cardId) { mutableStateOf<Long?>(null) }
    var loading by remember(cardId) { mutableStateOf(false) }
    var msg by remember(cardId) { mutableStateOf("") }
    fun load(before: Long?) {
        loading = true
        scope.launch {
            try {
                val page = WalletCalls(sessions.requireApi()).cardPayments(cardId, before)
                items = CardHistoryRules.merge(if (before == null) emptyList() else items.orEmpty(), page)
                next = page.nextBeforeId
                msg = ""
            } catch (e: ApiError) {
                msg = e.detail
                if (items == null) items = emptyList()
            } finally {
                loading = false
            }
        }
    }
    LaunchedEffect(cardId) { load(null) }
    val list = items
    when {
        list == null -> SkeletonRows(2)
        list.isEmpty() -> EmptyNote(stringResource(R.string.wallet_card_history_empty))
        else -> MenuGroup {
            list.forEach { p ->
                row {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            CardTag(stringResource(if (p.auto) R.string.wallet_kind_auto else R.string.wallet_kind_approved), if (p.auto) TagTone.ACCENT else TagTone.PLAIN)
                            Text(p.site.ifEmpty { p.host }, color = c.ink, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                            Text(p.amountText.ifEmpty { moneyText(p.amount, p.currency) }, color = c.ink, fontWeight = FontWeight.SemiBold)
                        }
                        if (p.summary.isNotEmpty()) Text(p.summary, color = c.ink, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(whenText(p.at), color = c.muted, style = MaterialTheme.typography.bodySmall)
                        when (val link = CardHistoryRules.link(p)) {
                            PaymentLink.Chat -> LinkButton(stringResource(R.string.wallet_open_chat), onClick = { onOpen(link) })
                            is PaymentLink.Task -> LinkButton(stringResource(R.string.wallet_open_task), onClick = { onOpen(link) })
                            PaymentLink.None -> {}
                        }
                    }
                }
            }
        }
    }
    if (next != null && !list.isNullOrEmpty()) {
        SecondaryButton(stringResource(R.string.wallet_card_history_more), onClick = { load(next) }, enabled = !loading, modifier = Modifier.fillMaxWidth())
    }
    if (msg.isNotEmpty()) ErrorText(msg, small = true)
}

/** Before auto-pay goes on: what it means, ticked, then 켜기 (this phone then opens its own copy of the card). */
@Composable
private fun AutoPayDialog(onDismiss: () -> Unit, onConfirm: () -> Unit) {
    val c = Kelo.colors
    var agreed by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss, containerColor = c.card,
        title = { Text(stringResource(R.string.wallet_auto_confirm_title), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.wallet_auto_notice), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                // no retyping: this phone opens its own copy of the card after the owner's check
                Text(stringResource(R.string.wallet_auto_from_phone), color = c.muted, style = MaterialTheme.typography.bodySmall)
                AgreeRow(agreed, onAgree = { agreed = it })
            }
        },
        confirmButton = { TextButton(onClick = onConfirm, enabled = agreed) { Text(stringResource(R.string.wallet_auto_confirm_yes), color = if (agreed) c.accent else c.muted) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

@Composable
private fun RenameDialog(current: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    val c = Kelo.colors
    // the cursor starts after the current name, ready to change it
    var field by remember { mutableStateOf(androidx.compose.ui.text.input.TextFieldValue(current, androidx.compose.ui.text.TextRange(current.length))) }
    val name = field.text
    // a name never holds card digits (the server refuses that too)
    val digitsInside = Regex("(?:\\d[ -]?){12,}").containsMatchIn(name)
    AlertDialog(
        onDismissRequest = onDismiss, containerColor = c.card,
        title = { Text(stringResource(R.string.wallet_nickname), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                androidx.compose.material3.OutlinedTextField(
                    value = field, onValueChange = { v -> field = if (v.text.length <= 60) v else field },
                    singleLine = true, modifier = Modifier.fillMaxWidth(),
                    placeholder = { chat.kelo.app.ui.components.FieldPlaceholder(stringResource(R.string.wallet_rename_hint)) },
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                    shape = androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
                    colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = c.card, unfocusedContainerColor = c.card, focusedBorderColor = c.accent,
                        unfocusedBorderColor = c.line, cursorColor = c.accent, focusedTextColor = c.ink, unfocusedTextColor = c.ink,
                    ),
                )
                MutedText(stringResource(R.string.wallet_rename_note), small = true)
                if (digitsInside) ErrorText(stringResource(R.string.wallet_rename_no_digits), small = true)
            }
        },
        confirmButton = {
            TextButton(onClick = { onDismiss(); onSave(name) }, enabled = !digitsInside) { Text(stringResource(R.string.save), color = c.accent) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/**
 * 자동결제 한도: the global pause, and per currency the limit for one payment and for a month with what was paid automatically this
 * month. Each can go back to the default; another currency can be added.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun LimitsPage(paused: Boolean, onBack: () -> Unit, onPaused: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    val c = Kelo.colors
    var data by remember { mutableStateOf<WalletLimits?>(null) }
    var isPaused by remember { mutableStateOf(paused) }
    var added by remember { mutableStateOf(setOf<String>()) }
    var editing by remember { mutableStateOf<String?>(null) }
    var adding by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf("") }
    val home = LimitRules.homeCurrencies(appLocale(), android.content.res.Resources.getSystem().configuration.locales[0] ?: java.util.Locale.getDefault())
    fun calls() = WalletCalls(sessions.requireApi())
    suspend fun load() {
        try { data = calls().limits() } catch (e: ApiError) { msg = e.detail; if (data == null) data = WalletLimits() }
    }
    LaunchedEffect(Unit) { load() }

    SubPage(stringResource(R.string.wallet_limits_title), onBack) {
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.wallet_pause_title), color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                Switch(
                    checked = isPaused,
                    onCheckedChange = { on ->
                        msg = ""
                        scope.launch {
                            try { isPaused = calls().pause(on).paused; haptics.confirm(); onPaused() } catch (e: ApiError) { msg = e.detail; haptics.reject() }
                        }
                    },
                    colors = SwitchDefaults.colors(checkedTrackColor = c.danger, checkedThumbColor = c.bg),
                )
            }
            MutedText(stringResource(if (isPaused) R.string.wallet_pause_on_body else R.string.wallet_pause_off_body), small = true)
        }
        Note(stringResource(R.string.wallet_limits_help))
        val d = data
        if (d == null) { SkeletonRows(3); return@SubPage }
        if (d.month.isNotEmpty()) Note(stringResource(R.string.wallet_limits_month, d.month, d.timezone))
        val shown = LimitRules.visible(d.limits, home, added + listOfNotNull(editing))
        shown.forEach { row ->
            LimitCard(
                row, editing = editing == row.currency, onEdit = { editing = if (editing == row.currency) null else row.currency },
                onSave = { per, mon ->
                    msg = ""
                    scope.launch {
                        try { calls().setLimit(row.currency, per, mon); editing = null; haptics.confirm(); load() } catch (e: ApiError) { msg = e.detail; haptics.reject() }
                    }
                },
                onReset = {
                    msg = ""
                    scope.launch {
                        try { calls().resetLimit(row.currency); editing = null; added = added - row.currency; haptics.confirm(); load() } catch (e: ApiError) { msg = e.detail }
                    }
                },
            )
        }
        // a currency the user is adding that the server does not know yet
        editing?.takeIf { cur -> d.limits.none { it.currency == cur } }?.let { cur ->
            LimitCard(
                WalletLimit(cur), editing = true, onEdit = { editing = null },
                onSave = { per, mon ->
                    scope.launch {
                        try { calls().setLimit(cur, per, mon); editing = null; added = added + cur; haptics.confirm(); load() } catch (e: ApiError) { msg = e.detail; haptics.reject() }
                    }
                },
                onReset = { editing = null },
            )
        }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
        MenuGroup { row { MenuRow(KeloIcons.Plus, stringResource(R.string.wallet_limits_add), onClick = { adding = true }) } }
        if (adding) AddCurrencyDialog(LimitRules.addable(d.limits, shown), onDismiss = { adding = false }) { cur ->
            adding = false
            added = added + cur
            editing = cur
        }
    }
}

@Composable
private fun LimitCard(row: WalletLimit, editing: Boolean, onEdit: () -> Unit, onSave: (Double, Double) -> Unit, onReset: () -> Unit) {
    val c = Kelo.colors
    MyCard(onClick = if (editing) null else onEdit) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(row.currency, fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
            CardTag(
                stringResource(
                    when {
                        row.added -> R.string.wallet_limit_added
                        row.custom -> R.string.wallet_limit_custom
                        else -> R.string.wallet_limit_default
                    },
                ),
                if (row.custom || row.added) TagTone.ACCENT else TagTone.PLAIN,
            )
        }
        if (row.monthly <= 0 && row.perPayment <= 0 && (row.custom || row.added)) {
            MutedText(stringResource(R.string.wallet_limit_never), small = true)
        } else {
            Text(
                stringResource(R.string.wallet_limit_line, moneyText(row.perPayment, row.currency), moneyText(row.monthly, row.currency)),
                color = c.ink, style = MaterialTheme.typography.bodyMedium,
            )
            Bar(LimitRules.used(row.spentThisMonth, row.monthly), hot = row.spentThisMonth >= row.monthly && row.monthly > 0)
            MutedText(stringResource(R.string.wallet_limit_spent, moneyText(row.spentThisMonth, row.currency)), small = true)
        }
        if (editing) LimitEditor(row, onSave, onReset, onCancel = onEdit)
    }
}

@Composable
private fun LimitEditor(row: WalletLimit, onSave: (Double, Double) -> Unit, onReset: () -> Unit, onCancel: () -> Unit) {
    val known = row.custom || row.defaultPerPayment != null
    var per by remember(row.currency) { mutableStateOf(if (known) Money.plain(row.perPayment, row.currency) else "") }
    var mon by remember(row.currency) { mutableStateOf(if (known) Money.plain(row.monthly, row.currency) else "") }
    val perV = Money.parse(per, row.currency)
    val monV = Money.parse(mon, row.currency)
    val check = LimitRules.check(perV, monV)
    val decimals = Money.decimals(row.currency) > 0
    val kb = KeyboardOptions(keyboardType = if (decimals) KeyboardType.Decimal else KeyboardType.Number)
    WalletField(stringResource(R.string.wallet_limit_per, row.currency), per, { per = it.take(16) }, keyboard = kb,
        error = if (per.isNotEmpty() && perV == null) stringResource(R.string.wallet_limit_bad_number) else null)
    WalletField(stringResource(R.string.wallet_limit_month, row.currency), mon, { mon = it.take(16) }, keyboard = kb,
        error = when {
            mon.isNotEmpty() && monV == null -> stringResource(R.string.wallet_limit_bad_number)
            check == LimitCheck.PER_ABOVE_MONTHLY -> stringResource(R.string.wallet_limit_per_above)
            else -> null
        })
    MutedText(stringResource(R.string.wallet_limit_zero_note), small = true)
    row.defaultPerPayment?.let { dp ->
        MutedText(stringResource(R.string.wallet_limit_default_line, moneyText(dp, row.currency), moneyText(row.defaultMonthly ?: 0.0, row.currency)), small = true)
    }
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        SecondaryButton(stringResource(R.string.cancel), onClick = onCancel, modifier = Modifier.weight(1f))
        PrimaryButton(stringResource(R.string.save), onClick = { if (perV != null && monV != null) onSave(perV, monV) }, enabled = check == LimitCheck.OK, modifier = Modifier.weight(1f))
    }
    if (row.custom) {
        LinkButton(stringResource(if (row.added) R.string.wallet_limit_remove else R.string.wallet_limit_reset), onClick = onReset, danger = row.added)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AddCurrencyDialog(options: List<String>, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val c = Kelo.colors
    var code by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss, containerColor = c.card,
        title = { Text(stringResource(R.string.wallet_limits_add), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                KeloTextField(code, { code = it.uppercase().filter { ch -> ch in 'A'..'Z' }.take(3) }, stringResource(R.string.wallet_currency_hint),
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters, autoCorrectEnabled = false))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    options.take(18).forEach { o -> chat.kelo.app.ui.components.ChoiceChip(o, selected = o == code) { code = o } }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onPick(code) }, enabled = Money.validCode(code)) { Text(stringResource(R.string.wallet_currency_add), color = c.accent) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/** 결제 내역: every payment that went ahead, newest first, automatic or allowed on the phone. */
@Composable
internal fun PaymentsPage(onBack: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val c = Kelo.colors
    var items by remember { mutableStateOf<List<WalletPayment>?>(null) }
    var msg by remember { mutableStateOf("") }
    LaunchedEffect(Unit) {
        items = try { WalletCalls(sessions.requireApi()).payments().items } catch (e: ApiError) { msg = e.detail; emptyList() }
    }
    SubPage(stringResource(R.string.wallet_history_title), onBack) {
        val list = items
        if (list == null) { SkeletonRows(4); return@SubPage }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
        if (list.isEmpty()) EmptyNote(stringResource(R.string.wallet_history_empty))
        MenuGroup {
            list.forEach { p ->
                row {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            CardTag(stringResource(if (p.auto) R.string.wallet_kind_auto else R.string.wallet_kind_approved), if (p.auto) TagTone.ACCENT else TagTone.PLAIN)
                            Text(p.site.ifEmpty { p.host }, color = c.ink, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                            Text(p.amountText.ifEmpty { moneyText(p.amount, p.currency) }, color = c.ink, fontWeight = FontWeight.SemiBold)
                        }
                        if (p.summary.isNotEmpty()) Text(p.summary, color = c.ink, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(
                            if (p.cardLabel.isEmpty()) whenText(p.at) else stringResource(R.string.wallet_history_meta, p.cardLabel, whenText(p.at)),
                            color = c.muted, style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            }
        }
    }
}
