package chat.kelo.desktop.pages.my

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.wallet.AddressCountries
import chat.kelo.app.wallet.AddressCountry
import chat.kelo.app.wallet.AddressDraft
import chat.kelo.app.wallet.AddressField
import chat.kelo.app.wallet.AddressForm
import chat.kelo.app.wallet.AddressKeys
import chat.kelo.app.wallet.AddressProblem
import chat.kelo.app.wallet.AddressRules
import chat.kelo.app.wallet.ShippingCalls
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.SecondaryButton
import java.text.Collator
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/*
 * The address form the server describes (GET /api/address/form/{CC}), drawn the same in the wallet's shipping pages and in the
 * chat's address card (android/kelo .../wallet/ShippingModels.kt holds the models and the rules, shared with the phone). The
 * fields come in the server's order, one per row; their labels are the server's (in the request's language). Every label sits
 * above its field as plain text, so a long one wraps instead of being cut inside the box.
 */

/** A country's name in the app's language (the JVM's own names), else the server's English one. */
internal fun countryName(code: String, english: String, locale: Locale): String {
    val shown = runCatching { Locale("", code).getDisplayCountry(locale) }.getOrNull().orEmpty()
    return if (shown.isBlank() || shown.equals(code, ignoreCase = true)) english.ifBlank { code } else shown
}

/** The forms and the country list, kept for the run (per language: the labels come in the request's language). */
internal object AddressCache {
    private val forms = ConcurrentHashMap<String, AddressForm>()
    @Volatile private var countries: Pair<String, AddressCountries>? = null

    suspend fun form(calls: ShippingCalls, country: String, lang: String): AddressForm {
        val k = "$lang:${country.uppercase()}"
        return forms[k] ?: calls.form(country).also { forms[k] = it }
    }

    suspend fun countries(calls: ShippingCalls, lang: String): AddressCountries {
        countries?.takeIf { it.first == lang }?.let { return it.second }
        return calls.countries().also { countries = lang to it }
    }

    /** Forgets everything (a test starts a new mock; the server may have changed). */
    fun clear() { forms.clear(); countries = null }
}

/** A form being fetched, there, or not reachable ([detail]: the server's words). */
internal sealed interface FormLoad {
    data object Loading : FormLoad
    data class Ready(val form: AddressForm) : FormLoad
    data class Failed(val detail: String) : FormLoad
}

/** The form of [country] in the app's language; [attempt] fetches it again after a failure. */
@Composable
internal fun rememberAddressForm(sessions: Sessions, country: String, attempt: Int = 0): FormLoad {
    val lang = LocalStrings.current.code
    val state by produceState<FormLoad>(FormLoad.Loading, country, lang, attempt) {
        value = FormLoad.Loading
        value = try {
            FormLoad.Ready(AddressCache.form(ShippingCalls(sessions.requireApi()), country, lang))
        } catch (e: ApiError) {
            FormLoad.Failed(e.detail)
        } catch (e: IllegalStateException) {
            FormLoad.Failed(e.message.orEmpty())
        }
    }
    return state
}

/** The countries (sorted by the name shown in the app's language) and the one a new address starts with; null while loading. */
@Composable
internal fun rememberCountries(sessions: Sessions): AddressCountries? {
    val lang = LocalStrings.current.code
    val locale = LocalStrings.current.locale
    val state by produceState<AddressCountries?>(null, lang) {
        value = try {
            val raw = AddressCache.countries(ShippingCalls(sessions.requireApi()), lang)
            raw.copy(countries = AddressRules.sorted(raw.countries, { code -> countryName(code, raw.countries.firstOrNull { it.code == code }?.name.orEmpty(), locale) },
                Collator.getInstance(locale).let { col -> Comparator<String> { a, b -> col.compare(a, b) } }))
        } catch (e: ApiError) {
            AddressCountries()   // not reachable: the picker offers the address's own country only
        } catch (e: IllegalStateException) {
            AddressCountries()
        }
    }
    return state
}

/** A field's label, with the server's "optional" word when it is not required. */
@Composable
internal fun fieldLabel(label: String, required: Boolean, optionalWord: String): String =
    if (required) label else stringResource(R.string.shipping_label_optional, label, optionalWord.ifBlank { stringResource(R.string.shipping_optional) })

/** What a problem says under its field. */
@Composable
internal fun problemText(p: AddressProblem, field: AddressField?): String = when (p) {
    AddressProblem.REQUIRED -> stringResource(R.string.shipping_err_required)
    AddressProblem.PATTERN -> field?.example?.takeIf { it.isNotBlank() }?.let { stringResource(R.string.shipping_err_pattern, it) }
        ?: stringResource(R.string.shipping_err_format)
    AddressProblem.PHONE -> stringResource(R.string.shipping_err_phone)
}

/** A label line above a field (wraps; never inside the box, where a long one would be cut). */
@Composable
internal fun AddressLabel(text: String) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = Kelo.colors.ink, modifier = Modifier.padding(start = 2.dp))
}

/**
 * The country row: the shown name; a click opens the list (searchable by the shown name, the English one or the code). [label]:
 * the server's word for it (the form's labels.country).
 */
@Composable
internal fun CountryField(label: String, code: String, countries: List<AddressCountry>, enabled: Boolean = true, onPick: (String) -> Unit) {
    val c = Kelo.colors
    val locale = LocalStrings.current.locale
    var open by remember { mutableStateOf(false) }
    val english = countries.firstOrNull { it.code == code }?.name.orEmpty()
    val shown = countryName(code, english, locale)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        AddressLabel(label.ifBlank { stringResource(R.string.shipping_country) })
        PickBox(shown, enabled = enabled && countries.size > 1, description = label) { open = true }
    }
    if (open) CountryDialog(countries, code, onDismiss = { open = false }) { picked -> open = false; if (picked != code) onPick(picked) }
}

@Composable
private fun CountryDialog(countries: List<AddressCountry>, current: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val c = Kelo.colors
    val locale = LocalStrings.current.locale
    var query by remember { mutableStateOf("") }
    val shown: (String) -> String = { code -> countryName(code, countries.firstOrNull { it.code == code }?.name.orEmpty(), locale) }
    val rows = remember(query, countries, locale) { AddressRules.search(countries, query, shown) }
    AlertDialog(
        onDismissRequest = onDismiss, containerColor = c.card,
        title = { Text(stringResource(R.string.shipping_country), color = c.ink) },
        text = {
            Column(Modifier.widthIn(min = 260.dp, max = 480.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val searchWord = stringResource(R.string.shipping_country_search)
                KeloTextField(query, { query = it.take(60) }, searchWord, modifier = Modifier.semantics { contentDescription = searchWord })
                if (rows.isEmpty()) Text(stringResource(R.string.shipping_country_none), color = c.muted, style = MaterialTheme.typography.bodySmall)
                LazyColumn(Modifier.fillMaxWidth().heightIn(max = 380.dp)) {
                    items(rows, key = { it.code }) { country ->
                        val on = country.code == current
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(if (on) c.mine else c.card)
                                .clickable(role = Role.RadioButton) { onPick(country.code) }.padding(horizontal = 10.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Text(shown(country.code), color = c.ink, style = MaterialTheme.typography.bodyMedium,
                                fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal, modifier = Modifier.weight(1f))
                            if (on) Icon(KeloIcons.Check, contentDescription = null, tint = c.accent, modifier = Modifier.size(18.dp))
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/** A box that looks like a text field and opens a list ([text]: what is picked, or the hint in muted ink when [hint]). */
@Composable
internal fun PickBox(text: String, enabled: Boolean = true, hint: Boolean = false, description: String = "", onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp).clip(RoundedCornerShape(12.dp)).border(1.dp, c.line, RoundedCornerShape(12.dp))
            .background(c.card).clickable(enabled = enabled, role = Role.DropdownList, onClick = onClick)
            .semantics { if (description.isNotEmpty()) contentDescription = description }
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(text, color = if (hint) c.muted else c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        if (enabled) Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp).rotate(if (androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl) -90f else 90f))
    }
}

/** A field with [AddressField.options]: the option's name shows, its key is kept. */
@Composable
private fun OptionField(field: AddressField, value: String, enabled: Boolean, label: String, onValue: (String) -> Unit) {
    val c = Kelo.colors
    var open by remember { mutableStateOf(false) }
    Box(Modifier.fillMaxWidth()) {
        PickBox(
            if (value.isEmpty()) stringResource(R.string.shipping_pick) else field.optionName(value),
            enabled = enabled, hint = value.isEmpty(), description = label,
        ) { open = true }
        DropdownMenu(open, onDismissRequest = { open = false }, modifier = Modifier.heightIn(max = 380.dp).widthIn(min = 220.dp, max = 420.dp).background(c.card)) {
            field.options.orEmpty().forEach { o ->
                DropdownMenuItem(
                    text = { Text(o.name.ifBlank { o.key }, color = c.ink, fontWeight = if (o.key == value) FontWeight.SemiBold else FontWeight.Normal) },
                    onClick = { open = false; onValue(o.key) },
                )
            }
        }
    }
}

/**
 * The form's fields in its order, then the phone (the calling code as a fixed chip; the user types the national number) and the
 * delivery note. [problems] show under their fields once [showProblems] (after the first try to save or send).
 */
@Composable
internal fun AddressFields(
    form: AddressForm, draft: AddressDraft, onDraft: (AddressDraft) -> Unit,
    problems: Map<String, AddressProblem>, showProblems: Boolean, enabled: Boolean = true,
) {
    val c = Kelo.colors
    val optional = form.labels.optional
    for (f in form.fields) {
        val label = fieldLabel(f.label.ifBlank { f.key }, f.required, optional)
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            AddressLabel(label)
            if (f.isPicker) OptionField(f, draft.value(f.key), enabled, label) { onDraft(draft.with(f.key, it)) }
            else KeloTextField(
                draft.value(f.key), { if (enabled) onDraft(draft.with(f.key, it.replace("\n", " "))) },
                placeholder = f.example?.takeIf { it.isNotBlank() }?.let { stringResource(R.string.shipping_example, it) } ?: "",
                maxLength = if (f.key == AddressKeys.POSTAL_CODE || f.key == AddressKeys.SORTING_CODE) 20 else 200,
                keyboardOptions = if (f.numeric) KeyboardOptions(keyboardType = KeyboardType.Number) else KeyboardOptions.Default,
                modifier = Modifier.semantics { contentDescription = label },
            )
            if (showProblems) problems[f.key]?.let { ErrorText(problemText(it, f), small = true) }
        }
    }
    val phoneLabel = fieldLabel(form.phone.label.ifBlank { AddressKeys.PHONE }, form.phone.required, optional)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        AddressLabel(phoneLabel)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val code = form.phone.callingCode
            if (code.isNotBlank()) {
                val a11y = stringResource(R.string.shipping_calling_code, code)
                Text(
                    code, color = c.ink, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clip(RoundedCornerShape(10.dp)).background(c.bubble).border(1.dp, c.line, RoundedCornerShape(10.dp))
                        .semantics { contentDescription = a11y }.padding(horizontal = 12.dp, vertical = 14.dp),
                )
            }
            KeloTextField(
                draft.phone, { if (enabled) onDraft(draft.copy(phone = it.replace("\n", ""))) }, placeholder = "",
                maxLength = 30, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                modifier = Modifier.weight(1f).semantics { contentDescription = phoneLabel },
            )
        }
        if (showProblems) problems[AddressKeys.PHONE]?.let { ErrorText(problemText(it, null), small = true) }
    }
    val noteLabel = fieldLabel(form.note.label.ifBlank { AddressKeys.NOTE }, form.note.required, optional)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        AddressLabel(noteLabel)
        KeloTextField(draft.note, { if (enabled) onDraft(draft.copy(note = it)) }, placeholder = "", singleLine = false, maxLength = 300,
            modifier = Modifier.semantics { contentDescription = noteLabel })
    }
}

/** The form could not be fetched: the server's words and a way to try again. */
@Composable
internal fun FormFailed(detail: String, onRetry: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ErrorText(stringResource(R.string.shipping_form_failed) + if (detail.isNotBlank()) "\n$detail" else "", small = true)
        SecondaryButton(stringResource(R.string.retry), onClick = onRetry)
    }
}

/**
 * Keeps a draft in step with the form shown: the first form a draft meets gives the phone its national part (a saved profile's
 * phone is E.164), and a new country's form keeps only the values of keys both forms have ([AddressRules.carry]).
 */
internal object DraftSync {
    fun onForm(draft: AddressDraft, form: AddressForm, first: Boolean): AddressDraft {
        val carried = AddressRules.carry(draft, form)
        return if (first) carried.copy(phone = AddressRules.nationalPart(carried.phone, form.phone.callingCode)) else carried
    }
}
