package chat.kelo.desktop.pages.my

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.ScheduleGrantCalls
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.wallet.CardBrand
import chat.kelo.app.wallet.CardHistoryRules
import chat.kelo.app.wallet.CardMask
import chat.kelo.app.wallet.DefaultCardRules
import chat.kelo.app.wallet.PaymentLink
import chat.kelo.app.wallet.Expiry
import chat.kelo.app.wallet.LimitCheck
import chat.kelo.app.wallet.LimitRules
import chat.kelo.app.wallet.Money
import chat.kelo.desktop.pages.my.ops.WalletCalls
import chat.kelo.app.wallet.WalletCard
import chat.kelo.app.wallet.WalletInfo
import chat.kelo.app.wallet.WalletLimit
import chat.kelo.app.wallet.WalletLimits
import chat.kelo.app.wallet.WalletPayment
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.my.ops.CardFaceLarge
import chat.kelo.desktop.pages.my.ops.CardFaceThumb
import chat.kelo.desktop.pages.my.ops.Loading
import chat.kelo.desktop.pages.my.ops.whenText
import chat.kelo.desktop.pages.work.ScheduleGrantRows
import chat.kelo.desktop.ui.Bar
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.EmptyNote
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MenuGroup
import chat.kelo.desktop.ui.MenuRow
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** The card pages' own sub pages. */
private sealed interface WalletView {
    data object List : WalletView
    data class Card(val id: String) : WalletView
    data object Limits : WalletView
    data object Payments : WalletView
    /** A shipping address to change, or a new one ([id] null) */
    data class Shipping(val id: String?) : WalletView
}

/** How a card stands, seen from the desktop (which never holds a card's key: the phone does). */
internal enum class DesktopCardStatus { EXPIRED, ON_PHONE, AUTO_ONLY, UNFINISHED }

internal object DesktopCardRules {
    /** Expired first; a card with the phone's copy pays from the phone; one with only the server's copy, by auto-pay only. */
    fun of(card: WalletCard): DesktopCardStatus = when {
        card.expired -> DesktopCardStatus.EXPIRED
        card.e2e -> DesktopCardStatus.ON_PHONE
        card.serverCopy -> DesktopCardStatus.AUTO_ONLY
        else -> DesktopCardStatus.UNFINISHED
    }
}

/**
 * 마이 → 결제 카드 (android/kelo .../wallet/WalletPage.kt and WalletScreens.kt). A card's number is locked with a key only the phone
 * has, so adding a card, re-entering its details and turning auto-pay on happen in the phone app; here the cards are listed, can
 * be renamed or removed, auto-pay turned off, its limits and the pause set, and the payments looked through. There is no lock
 * screen here (the phone's opens with its fingerprint or screen lock); nothing on these pages can read or send a card.
 */
@Composable
fun WalletPage(app: AppContainer, onBack: () -> Unit, onSeen: () -> Unit) {
    val sessions = app.sessions
    val scope = rememberCoroutineScope()
    var info by remember { mutableStateOf<WalletInfo?>(null) }
    var notices by remember { mutableStateOf<List<WalletPayment>>(emptyList()) }
    var failed by remember { mutableStateOf("") }
    var view by remember { mutableStateOf<WalletView>(WalletView.List) }
    // the shipping addresses (next to the cards; pages/my/ShippingPages.kt)
    var shipping by remember { mutableStateOf<List<chat.kelo.app.wallet.ShippingProfile>?>(null) }
    var shippingFailed by remember { mutableStateOf("") }
    fun calls() = WalletCalls(sessions.requireApi())
    suspend fun loadNow() {
        try { info = calls().info(); failed = "" } catch (e: ApiError) { failed = e.detail }
    }
    suspend fun loadShipping() {
        try { shipping = chat.kelo.app.wallet.ShippingCalls(sessions.requireApi()).list(); shippingFailed = "" } catch (e: ApiError) { shippingFailed = e.detail }
    }
    val load: () -> Unit = { scope.launch { loadNow() } }
    LaunchedEffect(Unit) {
        launch { loadShipping() }
        loadNow()
        // automatic payments not looked at yet: shown at the top of the list once, then marked as seen
        runCatching { calls().notifications() }.onSuccess { n ->
            notices = n.items.filter { !it.seen }
            val top = n.items.maxOfOrNull { it.id }
            if (n.unseen > 0 && top != null) runCatching { calls().markSeen(top) }.onSuccess { onSeen() }
        }
    }
    val v = info
    if (v == null) {
        SubPage(stringResource(R.string.wallet_title), onBack = onBack) { if (failed.isNotEmpty()) ErrorText(failed, small = true) else Loading() }
        return
    }
    val toList = { view = WalletView.List }
    val list: @Composable () -> Unit = { WalletList(sessions, v, notices, onBack, go = { view = it }, onChanged = load, shipping = shipping, shippingFailed = shippingFailed) }
    when (val shown = view) {
        WalletView.List -> list()
        is WalletView.Card -> v.cards.find { it.id == shown.id }?.let { card ->
            CardPage(sessions, card, onBack = toList, onChanged = load, onDeleted = { load(); toList() }, cards = v.cards, app = app)
        } ?: list()
        WalletView.Limits -> LimitsPage(sessions, v.paused, onBack = toList, onPaused = load)
        WalletView.Payments -> PaymentsPage(sessions, onBack = toList)
        is WalletView.Shipping -> {
            val profile = shown.id?.let { id -> shipping?.find { it.id == id } }
            if (shown.id != null && profile == null) list()
            else ShippingEditPage(
                sessions, profile, firstOne = shipping.isNullOrEmpty(), onBack = toList,
                onSaved = { scope.launch { loadShipping(); toList() } },
                onDeleted = { scope.launch { loadShipping(); toList() } },
            )
        }
    }
}

@Composable
private fun Note(text: String) = MutedText(text, small = true, modifier = Modifier.padding(horizontal = 4.dp))

private enum class TagTone { PLAIN, ACCENT, DANGER }

@Composable
private fun CardTag(text: String, tone: TagTone = TagTone.PLAIN) {
    val c = Kelo.colors
    val (bg, fg) = when (tone) {
        TagTone.PLAIN -> c.bubble to c.muted
        TagTone.ACCENT -> c.mine to c.ink
        TagTone.DANGER -> c.dangerSoft to c.dangerInk
    }
    Text(text, color = fg, style = MaterialTheme.typography.labelSmall, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(bg).padding(horizontal = 8.dp, vertical = 3.dp))
}

/** An amount in a currency ("30,000원", "25.00 USD"), digits in the app's language. */
@Composable
internal fun moneyText(amount: Double, currency: String): String {
    val n = Money.number(amount, currency, LocalStrings.current.locale)
    return if (currency.equals("KRW", ignoreCase = true)) stringResource(R.string.wallet_money_krw, n) else stringResource(R.string.wallet_money, n, currency.uppercase())
}

@Composable
private fun WalletList(
    sessions: Sessions, info: WalletInfo, notices: List<WalletPayment>, onBack: () -> Unit, go: (WalletView) -> Unit, onChanged: () -> Unit,
    shipping: List<chat.kelo.app.wallet.ShippingProfile>? = null, shippingFailed: String = "",
) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var explain by remember { mutableStateOf(false) }
    // auto-pay works only with Keyguard on (GET /api/keyguard, owner only; null = not known): with it off, auto-pay cards show
    // as paused, as on the phone (AutoPayGate)
    var keyguardOn by remember { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(info) { keyguardOn = runCatching { chat.kelo.app.keyguard.KeyguardCalls.view(sessions.requireApi()).on }.getOrNull() }
    val keyguardPause = keyguardOn == false && info.cards.any { chat.kelo.app.wallet.AutoPayGate.pausedForKeyguard(it, keyguardOn) }
    SubPage(stringResource(R.string.wallet_title), onBack = onBack, actions = {
        IconButton(onClick = { explain = true }) { Icon(KeloIcons.Info, contentDescription = stringResource(R.string.wallet_safe_title), tint = c.ink) }
    }) {
        // the phone holds each card's key: adding a card and confirming a payment happen there
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(KeloIcons.Phone, contentDescription = null, tint = c.accent)
                Text(stringResource(R.string.wallet_add_title), fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge)
            }
            MutedText(stringResource(R.string.desktop_ops_wallet_note), small = true)
        }
        if (notices.isNotEmpty()) {
            MyCard {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(KeloIcons.Card, contentDescription = null, tint = c.success)
                    Text(pluralStringResource(R.plurals.wallet_notices_title, notices.size, notices.size), fontWeight = FontWeight.Bold, color = c.ink,
                        style = MaterialTheme.typography.bodyLarge)
                }
                notices.take(5).forEach { p ->
                    Text(stringResource(R.string.wallet_notice_line, p.site, p.amountText.ifEmpty { moneyText(p.amount, p.currency) }, p.cardLabel, whenText(p.at)),
                        style = MaterialTheme.typography.bodySmall, color = c.ink)
                }
            }
        }
        if (info.paused) {
            MyCard(onClick = { go(WalletView.Limits) }) {
                Text(stringResource(R.string.desktop_ops_wallet_paused_banner), color = c.ink, style = MaterialTheme.typography.bodyMedium)
            }
        }
        if (info.cards.isEmpty()) Note(stringResource(R.string.wallet_no_cards))
        Column { info.cards.forEach { card -> CardRow(card, keyguardOn) { go(WalletView.Card(card.id)) } } }
        // where orders go, next to what pays for them
        ShippingSection(shipping, shippingFailed, onOpen = { go(WalletView.Shipping(it.id)) }, onAdd = { go(WalletView.Shipping(null)) })
        SectionLabel(stringResource(R.string.wallet_auto_section))
        val autoCount = info.autoCount
        MenuGroup {
            row {
                // how many cards really pay on their own now: never just "on" while every card asks
                MenuRow(KeloIcons.Shield, stringResource(R.string.wallet_limits_title), onClick = { go(WalletView.Limits) },
                    value = when {
                        info.paused || keyguardPause -> stringResource(R.string.wallet_auto_paused_value)
                        autoCount == 0 -> stringResource(R.string.wallet_auto_none_value)
                        else -> pluralStringResource(R.plurals.wallet_auto_cards_value, autoCount, autoCount)
                    })
            }
            row { MenuRow(KeloIcons.Receipt, stringResource(R.string.wallet_history_title), onClick = { go(WalletView.Payments) }) }
        }
        // the phone's wording says "this phone" and "open a card": here payments are confirmed on the phone, where auto-pay is turned on
        if (info.cards.isNotEmpty() && autoCount == 0 && !info.paused) Note(stringResource(R.string.desktop_ops_wallet_auto_none_note))
        if (autoCount > 0 && !keyguardPause) Note(stringResource(R.string.wallet_first_site_rule))
        if (info.scheduleGrants.isNotEmpty()) {
            SectionLabel(stringResource(R.string.wallet_schedule_section))
            ScheduleGrantRows(info.scheduleGrants, showSchedule = true) { g ->
                scope.launch { runCatching { ScheduleGrantCalls.revoke(sessions.requireApi(), g) }.onSuccess { onChanged() } }
            }
            Note(stringResource(R.string.grant_section_note))
        }
    }
    if (explain) {
        AlertDialog(
            onDismissRequest = { explain = false }, containerColor = c.card,
            icon = { Icon(KeloIcons.Lock, contentDescription = null, tint = c.accent) },
            title = { Text(stringResource(R.string.wallet_safe_title), color = c.ink) },
            text = { Text(stringResource(R.string.desktop_ops_wallet_safe_body), color = c.ink, style = MaterialTheme.typography.bodyMedium) },
            confirmButton = { TextButton(onClick = { explain = false }) { Text(stringResource(R.string.ok), color = c.accent) } },
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CardRow(card: WalletCard, keyguardOn: Boolean?, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button, onClick = onClick).padding(horizontal = 4.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        CardFaceThumb(CardBrand.of(card.brand), card.last4, dimmed = card.expired)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(card.label, color = c.ink, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            cardSubtitle(card)?.let { Text(it, color = c.muted, style = MaterialTheme.typography.bodySmall) }
            Text(stringResource(R.string.wallet_card_meta, Expiry.display(card.expMonth, card.expYear), whenText(card.lastUsed)), color = c.muted,
                style = MaterialTheme.typography.bodySmall)
            autoLine(card, keyguardOn)?.let { Text(it, color = c.ink, style = MaterialTheme.typography.bodySmall) }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) { StatusTags(card, keyguardOn) }
            // a card saved to ask each time: how to turn auto-pay on (the number and CVC again, which only the phone app asks for)
            if (card.offersTurnOn) MutedText(stringResource(R.string.desktop_ops_wallet_on_phone), small = true)
        }
    }
}

/** Whether the card pays on its own, or why it asks each time, in one line (null for a card that cannot pay at all). */
@Composable
internal fun autoLine(card: WalletCard, keyguardOn: Boolean? = null): String? = when {
    card.expired || !card.usable -> null
    chat.kelo.app.wallet.AutoPayGate.pausedForKeyguard(card, keyguardOn) -> stringResource(R.string.wallet_auto_paused_keyguard)
    card.autoNow -> stringResource(R.string.wallet_row_pays_auto)
    else -> askReasonText(card.reason)
}

@Composable
internal fun askReasonText(reason: String): String = stringResource(
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
    return CardMask.subtitle(card.label, chat.kelo.desktop.pages.my.ops.brandName(b) ?: chat.kelo.desktop.pages.my.ops.brandShort(b), card.last4)
}

@Composable
private fun StatusTags(card: WalletCard, keyguardOn: Boolean? = null) {
    if (card.isDefault) CardTag(stringResource(R.string.wallet_tag_default), TagTone.ACCENT)
    // paused until Keyguard is on: "Paused", not "Asks each time" (as on the phone)
    if (chat.kelo.app.wallet.AutoPayGate.pausedForKeyguard(card, keyguardOn)) CardTag(stringResource(R.string.wallet_auto_paused_value), TagTone.DANGER)
    else if (card.autoNow) CardTag(stringResource(R.string.wallet_tag_auto), TagTone.ACCENT)
    else if (!card.expired && card.usable) CardTag(stringResource(R.string.wallet_tag_asks))
    when (DesktopCardRules.of(card)) {
        DesktopCardStatus.EXPIRED -> CardTag(stringResource(R.string.wallet_tag_expired), TagTone.DANGER)
        DesktopCardStatus.UNFINISHED -> CardTag(stringResource(R.string.wallet_tag_unfinished), TagTone.DANGER)
        DesktopCardStatus.AUTO_ONLY -> CardTag(stringResource(R.string.wallet_tag_auto_only))
        DesktopCardStatus.ON_PHONE -> CardTag(stringResource(R.string.desktop_ops_tag_phone))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CardPage(
    sessions: Sessions, card: WalletCard, onBack: () -> Unit, onChanged: () -> Unit, onDeleted: () -> Unit, cards: List<WalletCard>,
    app: AppContainer,
) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var msg by remember { mutableStateOf("") }
    var renaming by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf(false) }
    var turningOff by remember { mutableStateOf(false) }
    var newDefault by remember { mutableStateOf<String?>(null) }   // deleting the default: the card picked to take its place
    val status = DesktopCardRules.of(card)
    // Keyguard on the server (GET /api/keyguard, owner only; null = not known): auto-pay waits for it (AutoPayGate)
    var keyguardOn by remember { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(Unit) { keyguardOn = runCatching { chat.kelo.app.keyguard.KeyguardCalls.view(sessions.requireApi()).on }.getOrNull() }
    fun act(block: suspend WalletCalls.() -> Unit) {
        msg = ""
        scope.launch { try { WalletCalls(sessions.requireApi()).block() } catch (e: ApiError) { msg = e.detail } }
    }
    SubPage(card.label, onBack = onBack) {
        CardFaceLarge(CardBrand.of(card.brand), stringResource(R.string.wallet_card_masked, CardMask.last4(card.last4)), card.holder,
            Expiry.display(card.expMonth, card.expYear), modifier = Modifier.widthIn(max = 380.dp).padding(horizontal = 24.dp))
        cardSubtitle(card)?.let { Text(it, color = c.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(horizontal = 24.dp)) }
        MyCard {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) { StatusTags(card, keyguardOn) }
            MutedText(stringResource(R.string.wallet_card_dates, whenText(card.createdAt), whenText(card.lastUsed)), small = true)
        }
        when (status) {
            DesktopCardStatus.UNFINISHED -> MyCard { ErrorText(stringResource(R.string.desktop_ops_wallet_unfinished_body), small = true) }
            DesktopCardStatus.AUTO_ONLY -> MyCard { MutedText(stringResource(R.string.desktop_ops_wallet_auto_only_body), small = true) }
            DesktopCardStatus.EXPIRED -> MyCard { ErrorText(stringResource(R.string.wallet_expired_body), small = true) }
            DesktopCardStatus.ON_PHONE -> {}
        }
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.wallet_auto_title), color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                // turning it on needs the card number and CVC again, which only the phone app asks for
                Switch(
                    checked = card.autoPay, enabled = card.autoPay && !card.expired,
                    onCheckedChange = { on -> if (!on) turningOff = true },
                    colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.bg),
                )
            }
            MutedText(stringResource(if (card.autoPay) R.string.wallet_auto_on_body else R.string.desktop_ops_wallet_auto_off_body), small = true)
            // an auto-pay card on a server without Keyguard: paused (each payment asks on the phone) until it is turned on there
            if (chat.kelo.app.wallet.AutoPayGate.pausedForKeyguard(card, keyguardOn)) {
                Text(stringResource(R.string.wallet_auto_paused_keyguard), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                MutedText(stringResource(R.string.wallet_auto_copy_note), small = true)
            }
            if (!card.autoNow && !card.expired && card.usable) {
                // why this card asks each time; turning auto-pay on needs the number and CVC again, which only the phone app asks for
                Text(askReasonText(card.reason), color = c.ink, style = MaterialTheme.typography.bodyMedium)
            }
            if (!card.autoPay) MutedText(stringResource(R.string.desktop_ops_wallet_on_phone), small = true)
            MutedText(stringResource(R.string.wallet_first_site_rule), small = true)
            MutedText(stringResource(R.string.wallet_auto_tip), small = true)
        }
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.wallet_default_title), color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                if (card.isDefault) CardTag(stringResource(R.string.wallet_tag_default), TagTone.ACCENT)
            }
            MutedText(stringResource(R.string.wallet_default_on), small = true)
            if (!card.isDefault && !card.expired) SecondaryButton(stringResource(R.string.wallet_make_default), onClick = { act { makeDefault(card.id); onChanged() } })
        }
        MenuGroup {
            row { MenuRow(KeloIcons.Edit, stringResource(R.string.wallet_nickname), onClick = { renaming = true }, value = card.label) }
        }
        SectionLabel(stringResource(R.string.wallet_card_history))
        CardHistory(sessions, card.id) { link ->
            when (link) {
                PaymentLink.Chat -> app.openRequests.value = chat.kelo.app.notify.OpenTarget.Chat
                is PaymentLink.Task -> app.openRequests.value = chat.kelo.app.notify.OpenTarget.Task(link.id)
                PaymentLink.None -> {}
            }
        }
        MenuGroup { row { MenuRow(KeloIcons.Trash, stringResource(R.string.wallet_delete), onClick = { deleting = true }, danger = true) } }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
    }
    if (turningOff) {
        ConfirmDialog(
            text = stringResource(if (status == DesktopCardStatus.ON_PHONE) R.string.desktop_ops_wallet_auto_off_confirm else R.string.desktop_ops_wallet_auto_off_confirm_unusable),
            yes = stringResource(R.string.wallet_auto_turn_off), danger = status != DesktopCardStatus.ON_PHONE,
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
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable(role = Role.RadioButton) { newDefault = other.id }
                                .padding(vertical = 6.dp),
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
                    act { delete(card.id, next); onDeleted() }
                }, enabled = picked != null) { Text(stringResource(R.string.delete), color = c.danger) }
            },
            dismissButton = { TextButton(onClick = { deleting = false; newDefault = null }) { Text(stringResource(R.string.cancel), color = c.ink) } },
        )
    } else if (deleting) {
        ConfirmDialog(stringResource(R.string.wallet_delete_confirm, card.label), stringResource(R.string.delete), onDismiss = { deleting = false },
            onYes = { act { delete(card.id); onDeleted() } })
    }
    if (renaming) RenameDialog(card.label, onDismiss = { renaming = false }) { name -> act { patch(card.id, buildJsonObject { put("label", name.trim()) }); onChanged() } }
}

/** The payments Kelo made with one card, newest first, older ones a page at a time; each opens its chat or task when known. */
@Composable
private fun CardHistory(sessions: Sessions, cardId: String, onOpen: (PaymentLink) -> Unit) {
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
        list == null -> Loading()
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
    if (next != null && !list.isNullOrEmpty()) SecondaryButton(stringResource(R.string.wallet_card_history_more), onClick = { load(next) }, enabled = !loading)
    if (msg.isNotEmpty()) ErrorText(msg, small = true)
}

@Composable
private fun RenameDialog(current: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    val c = Kelo.colors
    var name by remember { mutableStateOf(current) }
    // a name never holds card digits (the server refuses that too)
    val digitsInside = Regex("(?:\\d[ -]?){12,}").containsMatchIn(name)
    AlertDialog(
        onDismissRequest = onDismiss, containerColor = c.card,
        title = { Text(stringResource(R.string.wallet_nickname), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                KeloTextField(name, { name = it }, stringResource(R.string.wallet_rename_hint), maxLength = 60)
                MutedText(stringResource(R.string.wallet_rename_note), small = true)
                if (digitsInside) ErrorText(stringResource(R.string.wallet_rename_no_digits), small = true)
            }
        },
        confirmButton = { TextButton(onClick = { onDismiss(); onSave(name) }, enabled = !digitsInside) { Text(stringResource(R.string.save), color = c.accent) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/** 자동결제 한도: the pause, and per currency the limit for one payment and for a month, with this month's automatic spending. */
@Composable
private fun LimitsPage(sessions: Sessions, paused: Boolean, onBack: () -> Unit, onPaused: () -> Unit) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var data by remember { mutableStateOf<WalletLimits?>(null) }
    var isPaused by remember { mutableStateOf(paused) }
    var added by remember { mutableStateOf(setOf<String>()) }
    var editing by remember { mutableStateOf<String?>(null) }
    var adding by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf("") }
    val home = LimitRules.homeCurrencies(LocalStrings.current.locale, java.util.Locale.getDefault())
    fun calls() = WalletCalls(sessions.requireApi())
    suspend fun load() {
        try { data = calls().limits() } catch (e: ApiError) { msg = e.detail; if (data == null) data = WalletLimits() }
    }
    LaunchedEffect(Unit) { load() }
    SubPage(stringResource(R.string.wallet_limits_title), onBack = onBack) {
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.wallet_pause_title), color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                Switch(
                    checked = isPaused,
                    onCheckedChange = { on ->
                        msg = ""
                        scope.launch { try { isPaused = calls().pause(on).paused; onPaused() } catch (e: ApiError) { msg = e.detail } }
                    },
                    colors = SwitchDefaults.colors(checkedTrackColor = c.danger, checkedThumbColor = c.bg),
                )
            }
            MutedText(stringResource(if (isPaused) R.string.desktop_ops_wallet_pause_on_body else R.string.wallet_pause_off_body), small = true)
        }
        Note(stringResource(R.string.wallet_limits_help))
        val d = data
        if (d == null) { Loading(); return@SubPage }
        if (d.month.isNotEmpty()) Note(stringResource(R.string.wallet_limits_month, d.month, d.timezone))
        val shown = LimitRules.visible(d.limits, home, added + listOfNotNull(editing))
        shown.forEach { row ->
            LimitCard(
                row, editing = editing == row.currency, onEdit = { editing = if (editing == row.currency) null else row.currency },
                onSave = { per, mon ->
                    msg = ""
                    scope.launch { try { calls().setLimit(row.currency, per, mon); editing = null; load() } catch (e: ApiError) { msg = e.detail } }
                },
                onReset = {
                    msg = ""
                    scope.launch { try { calls().resetLimit(row.currency); editing = null; added = added - row.currency; load() } catch (e: ApiError) { msg = e.detail } }
                },
            )
        }
        // a currency being added that the server does not know yet
        editing?.takeIf { cur -> d.limits.none { it.currency == cur } }?.let { cur ->
            LimitCard(
                WalletLimit(cur), editing = true, onEdit = { editing = null },
                onSave = { per, mon ->
                    scope.launch { try { calls().setLimit(cur, per, mon); editing = null; added = added + cur; load() } catch (e: ApiError) { msg = e.detail } }
                },
                onReset = { editing = null },
            )
        }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
        MenuGroup { row { MenuRow(KeloIcons.Plus, stringResource(R.string.wallet_limits_add), onClick = { adding = true }) } }
        if (adding) AddCurrencyDialog(LimitRules.addable(d.limits, shown), onDismiss = { adding = false }) { cur ->
            adding = false; added = added + cur; editing = cur
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
                stringResource(when { row.added -> R.string.wallet_limit_added; row.custom -> R.string.wallet_limit_custom; else -> R.string.wallet_limit_default }),
                if (row.custom || row.added) TagTone.ACCENT else TagTone.PLAIN,
            )
        }
        if (row.monthly <= 0 && row.perPayment <= 0 && (row.custom || row.added)) {
            MutedText(stringResource(R.string.wallet_limit_never), small = true)
        } else {
            Text(stringResource(R.string.wallet_limit_line, moneyText(row.perPayment, row.currency), moneyText(row.monthly, row.currency)),
                color = c.ink, style = MaterialTheme.typography.bodyMedium)
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
    KeloTextField(per, { per = it.take(16) }, stringResource(R.string.wallet_limit_per, row.currency), label = stringResource(R.string.wallet_limit_per, row.currency))
    if (per.isNotEmpty() && perV == null) ErrorText(stringResource(R.string.wallet_limit_bad_number), small = true)
    KeloTextField(mon, { mon = it.take(16) }, stringResource(R.string.wallet_limit_month, row.currency), label = stringResource(R.string.wallet_limit_month, row.currency))
    when {
        mon.isNotEmpty() && monV == null -> ErrorText(stringResource(R.string.wallet_limit_bad_number), small = true)
        check == LimitCheck.PER_ABOVE_MONTHLY -> ErrorText(stringResource(R.string.wallet_limit_per_above), small = true)
    }
    MutedText(stringResource(R.string.wallet_limit_zero_note), small = true)
    row.defaultPerPayment?.let { dp ->
        MutedText(stringResource(R.string.wallet_limit_default_line, moneyText(dp, row.currency), moneyText(row.defaultMonthly ?: 0.0, row.currency)), small = true)
    }
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        SecondaryButton(stringResource(R.string.cancel), onClick = onCancel, modifier = Modifier.weight(1f))
        PrimaryButton(stringResource(R.string.save), onClick = { if (perV != null && monV != null) onSave(perV, monV) }, enabled = check == LimitCheck.OK, modifier = Modifier.weight(1f))
    }
    if (row.custom) LinkButton(stringResource(if (row.added) R.string.wallet_limit_remove else R.string.wallet_limit_reset), onClick = onReset, danger = row.added)
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
                KeloTextField(code, { code = it.uppercase().filter { ch -> ch in 'A'..'Z' }.take(3) }, stringResource(R.string.wallet_currency_hint))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    options.take(18).forEach { o -> ChoiceChip(o, selected = o == code, onClick = { code = o }) }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onPick(code) }, enabled = Money.validCode(code)) { Text(stringResource(R.string.wallet_currency_add), color = c.accent) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/** 결제 내역: every payment that went ahead, newest first, automatic or allowed on the phone. */
@Composable
private fun PaymentsPage(sessions: Sessions, onBack: () -> Unit) {
    val c = Kelo.colors
    var items by remember { mutableStateOf<List<WalletPayment>?>(null) }
    var msg by remember { mutableStateOf("") }
    LaunchedEffect(Unit) { items = try { WalletCalls(sessions.requireApi()).payments().items } catch (e: ApiError) { msg = e.detail; emptyList() } }
    SubPage(stringResource(R.string.wallet_history_title), onBack = onBack) {
        val list = items
        if (list == null) { Loading(); return@SubPage }
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
                        Text(if (p.cardLabel.isEmpty()) whenText(p.at) else stringResource(R.string.wallet_history_meta, p.cardLabel, whenText(p.at)),
                            color = c.muted, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}
