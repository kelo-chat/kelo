package chat.kelo.app.start

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.TextButton
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.PairingLinks
import chat.kelo.app.ui.components.CenterCard
import chat.kelo.app.ui.components.CenterMessage
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.emphasized
import chat.kelo.app.ui.theme.Kelo

private object ConnectRoutes {
    const val CONNECT = "connect"
    const val SCAN = "connect/scan"
}

/** Signed out: the connect screen and its QR scanner. */
@Composable
fun ConnectFlow() {
    val container = LocalAppContainer.current
    val vm: ConnectViewModel = viewModel { ConnectViewModel(container) }
    val state by vm.state.collectAsStateWithLifecycle()
    val nav = rememberNavController()

    if (state.pairing) {
        CenterMessage(stringResource(R.string.connect_pairing))
        return
    }
    state.confirm?.let { link ->
        val host = android.net.Uri.parse(link.serverUrl).let { u -> u.host + if (u.port > 0) ":${u.port}" else "" }
        val c = Kelo.colors
        AlertDialog(
            onDismissRequest = vm::cancelPair,
            containerColor = c.card,
            title = { Text(stringResource(R.string.connect_confirm_title), color = c.ink) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(host, color = c.ink, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
                    Text(stringResource(R.string.connect_confirm_body), color = c.muted, style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = { TextButton(onClick = vm::confirmPair) { Text(stringResource(R.string.connect_confirm_yes), color = c.accent) } },
            dismissButton = { TextButton(onClick = vm::cancelPair) { Text(stringResource(R.string.cancel), color = c.ink) } },
        )
    }
    NavHost(nav, startDestination = ConnectRoutes.CONNECT) {
        composable(ConnectRoutes.CONNECT) {
            ConnectScreen(vm, state, onScan = { nav.navigate(ConnectRoutes.SCAN) { launchSingleTop = true } })
        }
        composable(ConnectRoutes.SCAN) {
            QrScanScreen(
                onClose = { nav.popBackStack() },
                accept = { raw -> PairingLinks.parse(raw) != null },
                onScanned = { raw ->
                    nav.popBackStack()
                    vm.useLink(raw)
                },
            )
        }
    }
}

@Composable
private fun ConnectScreen(vm: ConnectViewModel, state: ConnectState, onScan: () -> Unit) {
    val c = Kelo.colors
    val clipboard = LocalClipboardManager.current
    var withLink by rememberSaveable { mutableStateOf(false) }
    var link by rememberSaveable { mutableStateOf("") }
    var withKey by rememberSaveable { mutableStateOf(false) }
    var server by rememberSaveable { mutableStateOf(vm.lastServerUrl) }
    var key by rememberSaveable { mutableStateOf("") }

    CenterCard {
        Text(stringResource(R.string.connect_title), style = MaterialTheme.typography.headlineMedium)
        state.notice?.let { ErrorText(it.resolve()) }
        MutedText(stringResource(R.string.connect_lead))
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Step(1, emphasized(stringResource(R.string.connect_step1), stringResource(R.string.connect_step1_path)))
            Step(2, AnnotatedString(stringResource(R.string.connect_step2)))
        }
        Text(
            emphasized(stringResource(R.string.connect_first_device), stringResource(R.string.connect_first_device_cmd), code = true),
            color = c.muted, style = MaterialTheme.typography.bodySmall,
        )

        PrimaryButton(stringResource(R.string.connect_scan), onClick = onScan)

        if (withLink) {
            KeloTextField(
                value = link, onValueChange = { link = it; vm.clearLinkError() },
                placeholder = stringResource(R.string.connect_link_placeholder),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
                keyboardActions = KeyboardActions(onGo = { if (link.isNotBlank()) vm.useLink(link) }),
            )
            state.linkError?.let { ErrorText(it.resolve(), small = true) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                SecondaryButton(stringResource(R.string.connect_link_paste), onClick = {
                    clipboard.getText()?.text?.trim()?.let { link = it; vm.clearLinkError() }
                })
                PrimaryButton(stringResource(R.string.connect_submit), onClick = { vm.useLink(link) },
                    enabled = link.isNotBlank(), modifier = Modifier.weight(1f))
            }
        } else {
            SecondaryButton(stringResource(R.string.connect_paste_link), onClick = { withLink = true }, modifier = Modifier.fillMaxWidth())
        }

        if (withKey) {
            Text(stringResource(R.string.connect_server_label), color = c.muted, style = MaterialTheme.typography.bodySmall)
            KeloTextField(
                value = server, onValueChange = { server = it },
                placeholder = stringResource(R.string.connect_server_placeholder),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next, autoCorrectEnabled = false),
            )
            KeloTextField(
                value = key, onValueChange = { key = it }, password = true,
                placeholder = stringResource(R.string.connect_key_placeholder),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Go, autoCorrectEnabled = false),
                keyboardActions = KeyboardActions(onGo = {
                    if (!state.keyBusy && key.isNotBlank() && server.isNotBlank()) vm.connectWithKey(server, key)
                }),
            )
            MutedText(stringResource(R.string.connect_key_hint), small = true)
            state.keyError?.let { ErrorText(it.resolve()) }
            PrimaryButton(
                stringResource(if (state.keyBusy) R.string.connect_checking else R.string.connect_submit),
                onClick = { vm.connectWithKey(server, key) },
                enabled = !state.keyBusy && key.isNotBlank() && server.isNotBlank(),
            )
        } else {
            LinkButton(stringResource(R.string.connect_with_key), onClick = { withKey = true })
        }
    }
}

@Composable
private fun Step(n: Int, text: AnnotatedString) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(stringResource(R.string.step_number, n), style = MaterialTheme.typography.bodyLarge)
        Text(text, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
    }
}
