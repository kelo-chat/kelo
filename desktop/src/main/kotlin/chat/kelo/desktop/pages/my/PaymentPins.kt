package chat.kelo.desktop.pages.my

import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.MenuGroup
import chat.kelo.desktop.ui.MenuRow
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.SectionLabel
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

// "결제 비밀번호" in the Keychain (android/kelo .../my/PaymentPinSection.kt). A payment PIN is sealed with the key of the phone that
// saved it, so a computer never adds or releases one: it lists them (name, site, how many digits; never the PIN), can delete one,
// and says that PINs are added in the Kelo app on the phone.

/** GET /api/payment-pins: what the server knows of a saved PIN (never the PIN itself). */
@Serializable
private data class PinRow(val id: String, val site: String = "", val label: String = "", val digits: Int = 0)

@Serializable
private data class PinRows(val items: List<PinRow> = emptyList())

@Composable
fun PaymentPinSection(sessions: Sessions) {
    val scope = rememberCoroutineScope()
    var pins by remember { mutableStateOf<List<PinRow>>(emptyList()) }
    var deleting by remember { mutableStateOf<PinRow?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    LaunchedEffect(reload) { runCatching { sessions.requireApi().get<PinRows>("/api/payment-pins").items }.onSuccess { pins = it } }
    SectionLabel(stringResource(R.string.pin_section))
    MutedText(stringResource(R.string.pin_section_help), small = true, modifier = Modifier.padding(horizontal = 4.dp))
    if (pins.isNotEmpty()) {
        MenuGroup {
            pins.forEach { p ->
                row { MenuRow(KeloIcons.Lock, p.label.ifEmpty { p.site }, onClick = { deleting = p }, value = stringResource(R.string.pin_row_value, p.site, p.digits)) }
            }
        }
    }
    MutedText(stringResource(R.string.desktop_pin_add_on_phone), small = true, modifier = Modifier.padding(horizontal = 4.dp))
    deleting?.let { p ->
        ConfirmDialog(
            text = stringResource(R.string.pin_row_value, p.site, p.digits) + "\n\n" + stringResource(R.string.pin_delete_body),
            yes = stringResource(R.string.pin_delete), title = p.label.ifEmpty { p.site },
            onDismiss = { deleting = null },
            onYes = {
                deleting = null
                scope.launch {
                    runCatching { sessions.requireApi().delete<JsonElement>("/api/payment-pins/${KeloApi.segment(p.id)}") }
                    reload++
                }
            },
        )
    }
}
