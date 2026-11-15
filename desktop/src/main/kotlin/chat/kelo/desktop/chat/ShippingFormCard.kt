package chat.kelo.desktop.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ShippingFormState
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.wallet.AddressRules
import chat.kelo.app.wallet.ShippingAsk
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.my.AddressFields
import chat.kelo.desktop.pages.my.CountryField
import chat.kelo.desktop.pages.my.DraftSync
import chat.kelo.desktop.pages.my.AddressLabel
import chat.kelo.desktop.pages.my.FormFailed
import chat.kelo.desktop.pages.my.FormLoad
import chat.kelo.desktop.pages.my.rememberAddressForm
import chat.kelo.desktop.pages.my.rememberCountries
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.delay

/**
 * The agent asks once where an order goes (the turn event "shipping_form"; its ask_shipping tool waits for this answer). The
 * card holds the country (it starts at the agent's guess), the server's form for that country prefilled with what Kelo remembers,
 * the phone and a delivery note; "use for next orders" (on unless the agent said otherwise) with the nickname it is saved under;
 * and, when the agent asks, how to pay. Sent, it folds into one line ("배송지: 집 · …"); choosing to add a card opens the wallet
 * (cards are added on the phone). A form whose time ran out, or whose reply ended, stays only as a record.
 */
@Composable
fun ShippingFormCard(item: ChatItem.ShippingForm, sessions: Sessions, model: ChatModel, cardChosen: Boolean, onOpenWallet: () -> Unit) {
    // the time a form waits runs out while it is on screen: look again every few seconds
    var now by remember { mutableLongStateOf(System.currentTimeMillis() / 1000) }
    LaunchedEffect(item.ask.expiresAt, item.state) {
        while (item.open && item.ask.expiresAt > 0) {
            now = System.currentTimeMillis() / 1000
            delay(5_000)
        }
    }
    val state = if (item.open && item.ask.expired(now)) ShippingFormState.EXPIRED else item.state
    CardBox(Modifier.semantics { liveRegion = LiveRegionMode.Polite }) {
        when (state) {
            ShippingFormState.OPEN -> OpenForm(item.ask, sessions, model, onOpenWallet)
            ShippingFormState.DONE -> {
                val line = AddressRules.who(item.doneLabel, item.doneLine, "")
                ResultLine(KeloIcons.Check, stringResource(R.string.shipping_ask_done, line))
                if (cardChosen) MutedText(stringResource(R.string.desktop_ops_ship_card_on_phone), small = true)
            }
            ShippingFormState.CANCELLED -> ResultLine(KeloIcons.Close, stringResource(R.string.shipping_ask_cancelled), muted = true)
            ShippingFormState.EXPIRED -> ResultLine(KeloIcons.Clock, stringResource(R.string.shipping_ask_expired), muted = true)
            ShippingFormState.CLOSED -> ResultLine(KeloIcons.Check, stringResource(R.string.shipping_ask_closed), muted = true)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun OpenForm(ask: ShippingAsk, sessions: Sessions, model: ChatModel, onOpenWallet: () -> Unit) {
    val c = Kelo.colors
    val home = stringResource(R.string.shipping_home)
    var draft by remember(ask.formId) { mutableStateOf(AddressRules.draftOf(ask)) }
    var applied by remember(ask.formId) { mutableStateOf("") }   // the country whose form the draft was last fitted to
    var save by remember(ask.formId) { mutableStateOf(ask.save) }
    var label by remember(ask.formId) { mutableStateOf(ask.label.ifBlank { home }) }
    var payment by remember(ask.formId) { mutableStateOf<String?>(null) }
    var showProblems by remember(ask.formId) { mutableStateOf(false) }
    var busy by remember(ask.formId) { mutableStateOf(false) }
    var error by remember(ask.formId) { mutableStateOf("") }
    var attempt by remember(ask.formId) { mutableIntStateOf(0) }
    val country = draft.country.ifBlank { "KR" }
    val load = rememberAddressForm(sessions, country, attempt)
    val countries = rememberCountries(sessions)
    val form = (load as? FormLoad.Ready)?.form
    LaunchedEffect(form) {
        if (form != null && form.country != applied) {
            draft = DraftSync.onForm(draft, form, first = applied.isEmpty())
            applied = form.country
        }
    }
    val networkError = stringResource(R.string.chat_error_network)

    Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(KeloIcons.MapPin, contentDescription = null, tint = c.accent, modifier = Modifier.size(22.dp))
        Text(stringResource(R.string.shipping_ask_title), style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f))
    }
    val about = listOf(ask.site, ask.what).filter { it.isNotBlank() }.joinToString(" · ")
    if (about.isNotEmpty()) MutedText(about, small = true)
    CountryField(
        form?.labels?.country.orEmpty(), country,
        countries?.countries?.ifEmpty { null } ?: listOf(chat.kelo.app.wallet.AddressCountry(country, form?.countryName.orEmpty())),
        enabled = !busy,
    ) { picked -> draft = draft.copy(country = picked) }
    when (load) {
        FormLoad.Loading -> MutedText(stringResource(R.string.loading), small = true)
        is FormLoad.Failed -> FormFailed(load.detail) { attempt++ }
        is FormLoad.Ready -> if (applied == load.form.country) {
            val problems = AddressRules.problems(load.form, draft)
            AddressFields(load.form, draft, { draft = it }, problems, showProblems, enabled = !busy)
        }
    }
    Row(
        Modifier.fillMaxWidth().toggleable(save, enabled = !busy, role = Role.Checkbox, onValueChange = { save = it }).padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = save, onCheckedChange = null, enabled = !busy,
            colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent, uncheckedColor = c.muted))
        Text(stringResource(R.string.shipping_ask_save), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 8.dp).weight(1f))
    }
    if (save) {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            val nickname = form?.labels?.nickname?.ifBlank { null } ?: stringResource(R.string.shipping_nickname)
            AddressLabel(nickname)
            KeloTextField(label, { if (!busy) label = it.replace("\n", " ") }, stringResource(R.string.shipping_nickname_hint), maxLength = 40,
                modifier = Modifier.semantics { contentDescription = nickname })
        }
    }
    if (ask.askPayment) {
        AddressLabel(stringResource(R.string.shipping_ask_pay))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            ChoiceChip(stringResource(R.string.shipping_pay_card), selected = payment == ShippingAsk.PAY_CARD) {
                if (!busy) payment = if (payment == ShippingAsk.PAY_CARD) null else ShippingAsk.PAY_CARD
            }
            ChoiceChip(stringResource(R.string.shipping_pay_site), selected = payment == ShippingAsk.PAY_SITE) {
                if (!busy) payment = if (payment == ShippingAsk.PAY_SITE) null else ShippingAsk.PAY_SITE
            }
        }
    }
    if (error.isNotEmpty()) ErrorText(error, small = true)
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
        SecondaryButton(stringResource(R.string.cancel), enabled = !busy, modifier = Modifier.weight(1f), onClick = { model.cancelShipping(ask.formId) })
        PrimaryButton(
            stringResource(if (busy) R.string.shipping_ask_sending else R.string.shipping_ask_send),
            enabled = !busy && form != null && applied == form.country, modifier = Modifier.weight(1f),
            onClick = {
                val f = form ?: return@PrimaryButton
                if (!AddressRules.isValid(f, draft)) { showProblems = true; return@PrimaryButton }
                busy = true; error = ""
                model.answerShipping(
                    ask.formId, f, draft, save, if (save) label.ifBlank { home } else "", if (ask.askPayment) payment else null, networkError,
                    onError = { busy = false; error = it },
                    onDone = { pay -> busy = false; if (pay == ShippingAsk.PAY_CARD) onOpenWallet() },
                )
            },
        )
    }
}
