package chat.kelo.app.my

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppServices
import chat.kelo.app.core.AddressMove
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch

/** The line under the address field for each way a move can fail (null: it went through). Pure, so the three apps and the tests share it. */
object ServerAddressRules {
    fun problem(r: AddressMove): org.jetbrains.compose.resources.StringResource? = when (r) {
        AddressMove.MOVED -> null
        AddressMove.SAME_ADDRESS -> Res.string.server_address_same
        AddressMove.INVALID, AddressMove.NOT_SUPPORTED -> Res.string.server_address_invalid
        AddressMove.UNREACHABLE -> Res.string.server_address_unreachable
        AddressMove.OTHER_SERVER -> Res.string.server_address_other
    }
}

/**
 * My -> Server address -> Change address: the same Kelo server at a new https address. The app checks that the new address answers
 * with this server's own key (Sessions.moveServer); only then it switches, keeping the device token and the key.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServerAddressSheet(current: String, onMoved: () -> Unit, onClose: () -> Unit) {
    val c = Kelo.colors
    val services = LocalAppServices.current
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<org.jetbrains.compose.resources.StringResource?>(null) }
    ModalBottomSheet(
        onDismissRequest = { if (!busy) onClose() },
        sheetState = chat.kelo.app.ui.components.rememberKeloSheetState(),
        containerColor = c.card,
    ) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).imePadding().navigationBarsPadding()
                .padding(start = 20.dp, end = 20.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(string(Res.string.server_address_title), style = MaterialTheme.typography.titleMedium, color = c.ink,
                modifier = Modifier.semantics { heading() })
            MutedText(string(Res.string.server_address_body, current), small = true)
            KeloTextField(
                text, { text = it; problem = null }, "https://",
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
            problem?.let { ErrorText(string(it), small = true) }
            PrimaryButton(
                string(if (busy) Res.string.server_address_checking else Res.string.server_address_switch),
                enabled = !busy && text.isNotBlank(),
                onClick = {
                    busy = true; problem = null
                    scope.launch {
                        val r = services.sessions.moveServer(text, services.allowLocalHttp)
                        busy = false
                        problem = ServerAddressRules.problem(r)
                        if (r == AddressMove.MOVED) onMoved()
                    }
                },
            )
        }
    }
}
