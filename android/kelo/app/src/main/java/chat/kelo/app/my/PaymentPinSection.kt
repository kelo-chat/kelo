package chat.kelo.app.my

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.wallet.PaymentPin
import chat.kelo.app.wallet.PinCalls
import chat.kelo.app.wallet.PinForm
import chat.kelo.app.wallet.WalletKey
import kotlinx.coroutines.launch

/**
 * "결제 비밀번호" in the Keychain: the payment PINs saved for sites (name, site, how many digits; never the PIN), adding one, and
 * deleting one. A PIN saved on another phone is marked: only that phone can release it.
 */
@Composable
fun PaymentPinSection(locked: Boolean) {
    val sessions = LocalAppContainer.current.sessions
    val context = androidx.compose.ui.platform.LocalContext.current
    val keyId = remember { WalletKey(context).keyId() }
    val scope = rememberCoroutineScope()
    var pins by remember { mutableStateOf<List<PaymentPin>>(emptyList()) }
    var adding by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf<PaymentPin?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    LaunchedEffect(reload) { runCatching { PinCalls(sessions.requireApi()).list() }.onSuccess { pins = it } }
    SectionLabel(stringResource(R.string.pin_section))
    MutedText(stringResource(R.string.pin_section_help), small = true, modifier = Modifier.padding(horizontal = 4.dp))
    if (pins.isNotEmpty()) {
        MenuGroup {
            pins.forEach { p ->
                row {
                    MenuRow(
                        KeloIcons.Lock, p.label.ifEmpty { p.site }, onClick = { deleting = p },
                        value = if (p.ours(keyId)) stringResource(R.string.pin_row_value, p.site, p.digits)
                        else stringResource(R.string.pin_row_other_phone, p.site),
                    )
                }
            }
        }
    }
    if (adding) {
        MyCard {
            PinForm(onSaved = { adding = false; reload++ })
        }
    } else if (!locked) {
        MenuGroup { row { MenuRow(KeloIcons.Plus, stringResource(R.string.pin_add), onClick = { adding = true }) } }
    }
    deleting?.let { p ->
        AlertDialog(
            onDismissRequest = { deleting = null },
            title = { Text(p.label.ifEmpty { p.site }) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(stringResource(R.string.pin_row_value, p.site, p.digits))
                    Text(stringResource(R.string.pin_delete_body))
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    deleting = null
                    scope.launch { runCatching { PinCalls(sessions.requireApi()).delete(p.id) }; reload++ }
                }) { Text(stringResource(R.string.pin_delete)) }
            },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text(stringResource(R.string.cancel)) } },
        )
    }
}
