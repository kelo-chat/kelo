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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
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
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.wallet.AddressCountry
import chat.kelo.app.wallet.AddressDraft
import chat.kelo.app.wallet.AddressRules
import chat.kelo.app.wallet.ShippingCalls
import chat.kelo.app.wallet.ShippingProfile
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.my.ops.Loading
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.MenuGroup
import chat.kelo.desktop.ui.MenuRow
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.launch

/*
 * 마이 → 결제 카드 → 배송 정보 (android/kelo .../wallet/ShippingPages.kt): the addresses Kelo ships an order to, next to the cards.
 * Unlike a card, an address holds nothing that needs the phone's key, so it is added, changed and removed here too
 * (GET/POST/PATCH/DELETE /api/wallet/shipping). The form is the server's for the address's country (pages/my/ShippingParts.kt).
 */

/**
 * The wallet's shipping section: each address with its nickname, a "Default" tag on the default one, the name and the address on
 * one line (two lines at most); "Add address" under them. [profiles] null: still loading; [failed]: the server's words.
 */
@Composable
internal fun ShippingSection(profiles: List<ShippingProfile>?, failed: String, onOpen: (ShippingProfile) -> Unit, onAdd: () -> Unit) {
    SectionLabel(stringResource(R.string.shipping_section))
    when {
        profiles == null && failed.isNotEmpty() -> ErrorText(failed, small = true, modifier = Modifier.padding(horizontal = 4.dp))
        profiles == null -> Loading()
        profiles.isEmpty() -> MutedText(stringResource(R.string.shipping_empty), small = true, modifier = Modifier.padding(horizontal = 4.dp))
        else -> Column { profiles.forEach { p -> ShippingRow(p) { onOpen(p) } } }
    }
    MenuGroup { row { MenuRow(KeloIcons.Plus, stringResource(R.string.shipping_add), onClick = onAdd) } }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ShippingRow(p: ShippingProfile, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button, onClick = onClick).padding(horizontal = 4.dp, vertical = 10.dp),
        verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Icon(KeloIcons.MapPin, contentDescription = null, tint = if (p.isDefault) c.accent else c.muted,
            modifier = Modifier.padding(top = 2.dp).clip(RoundedCornerShape(10.dp)).background(c.bubble).padding(8.dp).size(20.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(p.label.ifBlank { p.name }, color = c.ink, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
                if (p.isDefault) Text(stringResource(R.string.wallet_tag_default), color = c.ink, style = MaterialTheme.typography.labelSmall, maxLines = 1,
                    modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(c.mine).padding(horizontal = 8.dp, vertical = 3.dp))
            }
            if (p.name.isNotBlank()) Text(p.name, color = c.ink, style = MaterialTheme.typography.bodyMedium)
            val line = p.oneLine.ifBlank { AddressRules.oneLine(p.formatted) }
            if (line.isNotBlank()) Text(line, color = c.muted, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
    }
}

/**
 * Adding ([profile] null) or changing an address: the nickname first ("집" / "Home" for a new one), the country, the server's form
 * for it in its order, the phone (the calling code as a chip) and the delivery note, then "use as default". Save checks the
 * required fields and the postal code's pattern here first; the server's 422 says which field it did not take. Delete asks first.
 * [firstOne]: no address is saved yet (a new one then becomes the default by itself).
 */
@Composable
internal fun ShippingEditPage(
    sessions: Sessions, profile: ShippingProfile?, firstOne: Boolean, onBack: () -> Unit, onSaved: () -> Unit, onDeleted: () -> Unit,
) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    val home = stringResource(R.string.shipping_home)
    val countries = rememberCountries(sessions)
    var draft by remember(profile?.id) {
        mutableStateOf(profile?.let { AddressDraft(it.country, it.values(), it.phone, it.note) })
    }
    // a new address starts at the server's suggestion (the last address's country, else the server's own)
    LaunchedEffect(countries) {
        if (draft == null && countries != null) draft = AddressDraft(country = countries.defaultCountry.ifBlank { countries.countries.firstOrNull()?.code ?: "KR" })
    }
    var applied by remember(profile?.id) { mutableStateOf("") }
    var label by remember(profile?.id) { mutableStateOf(profile?.label ?: home) }
    var makeDefault by remember(profile?.id) { mutableStateOf(profile?.isDefault ?: firstOne) }
    var showProblems by remember(profile?.id) { mutableStateOf(false) }
    var busy by remember(profile?.id) { mutableStateOf(false) }
    var msg by remember(profile?.id) { mutableStateOf("") }
    var deleting by remember(profile?.id) { mutableStateOf(false) }
    var attempt by remember(profile?.id) { mutableIntStateOf(0) }

    SubPage(stringResource(if (profile == null) R.string.shipping_add else R.string.shipping_edit_title), onBack = onBack) {
        val d = draft
        if (d == null) { Loading(); return@SubPage }
        val load = rememberAddressForm(sessions, d.country, attempt)
        val form = (load as? FormLoad.Ready)?.form
        LaunchedEffect(form) {
            val cur = draft
            if (form != null && cur != null && form.country != applied) {
                draft = DraftSync.onForm(cur, form, first = applied.isEmpty())
                applied = form.country
            }
        }
        MyCard {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                val nickname = form?.labels?.nickname?.ifBlank { null } ?: stringResource(R.string.shipping_nickname)
                AddressLabel(nickname)
                KeloTextField(label, { label = it.replace("\n", " ") }, stringResource(R.string.shipping_nickname_hint), maxLength = 40,
                    modifier = Modifier.semantics { contentDescription = nickname })
            }
            CountryField(
                form?.labels?.country.orEmpty(), d.country,
                countries?.countries?.ifEmpty { null } ?: listOf(AddressCountry(d.country, form?.countryName.orEmpty())),
                enabled = !busy,
            ) { picked -> draft = d.copy(country = picked) }
            when (load) {
                FormLoad.Loading -> Loading()
                is FormLoad.Failed -> FormFailed(load.detail) { attempt++ }
                is FormLoad.Ready -> if (applied == load.form.country) {
                    AddressFields(load.form, d, { draft = it }, AddressRules.problems(load.form, d), showProblems, enabled = !busy)
                }
            }
        }
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.shipping_default_title), color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                // the default can only move to another address (the server then picks none to be without one)
                Switch(
                    checked = makeDefault, enabled = !busy && profile?.isDefault != true && !(profile == null && firstOne),
                    onCheckedChange = { makeDefault = it },
                    colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.bg),
                )
            }
            MutedText(stringResource(R.string.shipping_default_note), small = true)
        }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
        PrimaryButton(stringResource(R.string.save), enabled = !busy && form != null && applied == form.country, modifier = Modifier.fillMaxWidth(), onClick = {
            val f = form ?: return@PrimaryButton
            val cur = draft ?: return@PrimaryButton
            if (!AddressRules.isValid(f, cur)) { showProblems = true; return@PrimaryButton }
            busy = true; msg = ""
            scope.launch {
                try {
                    val calls = ShippingCalls(sessions.requireApi())
                    val name = label.trim().ifEmpty { home }
                    if (profile == null) calls.create(AddressRules.profileBody(f, cur, name, makeDefault))
                    else calls.update(profile.id, AddressRules.profileBody(f, cur, name, if (makeDefault && !profile.isDefault) true else null))
                    onSaved()
                } catch (e: ApiError) {
                    msg = e.detail
                } finally {
                    busy = false
                }
            }
        })
        if (profile != null) {
            MenuGroup { row { MenuRow(KeloIcons.Trash, stringResource(R.string.shipping_delete), onClick = { deleting = true }, danger = true) } }
        }
    }
    if (deleting && profile != null) {
        ConfirmDialog(stringResource(R.string.shipping_delete_confirm, profile.label.ifBlank { profile.name }), stringResource(R.string.delete),
            onDismiss = { deleting = false },
            onYes = {
                scope.launch {
                    try { ShippingCalls(sessions.requireApi()).delete(profile.id); onDeleted() } catch (e: ApiError) { msg = e.detail }
                }
            })
    }
}
