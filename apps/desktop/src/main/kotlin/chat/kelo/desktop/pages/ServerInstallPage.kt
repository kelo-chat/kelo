package chat.kelo.desktop.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.text.*
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.Browser
import chat.kelo.desktop.core.DesktopPlatform
import chat.kelo.desktop.core.MacServerRules
import chat.kelo.desktop.core.ServerInstallRules
import chat.kelo.desktop.core.ServerInstallState
import chat.kelo.desktop.core.ServerInstaller
import kotlinx.coroutines.launch
import java.awt.Desktop
import java.io.File

/** The name of the desktop's server install sub page (Windows and Mac; one of [MyScreenHooks.extraPages]). */
const val MY_SERVER_PAGE = "desktop-server"

/**
 * Installs the Kelo server on this PC or this Mac with one button: the app downloads the signed installer, checks it and installs it
 * (Windows asks for administrator permission once; the Mac asks for the administrator password once). No terminal. The setup page
 * opens in the browser when it is done. On a Mac the server can also be removed, optionally with its chats, keys and files.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ServerInstallPage(installer: ServerInstaller, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val st by installer.state.collectAsState()
    val installed = remember(st.phase) { installer.installed() }
    val texts = ServerInstallTexts.forOs(DesktopPlatform.os)
    var confirmRemove by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { installer.prepare() }
    SubPage(string(texts.row), onBack) {
        MutedText(string(texts.hint), modifier = Modifier.padding(horizontal = 12.dp), small = true)
        if (installed) MutedText(string(texts.installed), modifier = Modifier.padding(horizontal = 12.dp))
        FlowRow(
            Modifier.padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (installed) {
                PrimaryButton(
                    string(Res.string.desktop_server_open_setup),
                    { Browser.open("http://localhost:${MacServerRules.serverPort()}/") },
                    enabled = !st.busy,
                )
                SecondaryButton(string(Res.string.desktop_server_update), { scope.launch { installer.install() } }, enabled = !st.busy)
                if (installer.canRemove) {
                    SecondaryButton(string(Res.string.desktop_server_remove), { confirmRemove = true }, enabled = !st.busy)
                }
            } else if (st.ready) {
                PrimaryButton(string(Res.string.desktop_server_install), { scope.launch { installer.install() } }, enabled = !st.busy)
            }
        }
        val status: String? = when (st.phase) {
            ServerInstallState.Phase.IDLE -> null
            ServerInstallState.Phase.CHECKING -> string(Res.string.desktop_server_checking)
            ServerInstallState.Phase.DOWNLOADING, ServerInstallState.Phase.VERIFYING -> string(Res.string.desktop_server_downloading)
            ServerInstallState.Phase.INSTALLING -> string(Res.string.desktop_server_installing)
            ServerInstallState.Phase.STARTING -> string(Res.string.desktop_server_starting)
            ServerInstallState.Phase.DONE -> string(Res.string.desktop_server_done)
            ServerInstallState.Phase.RESTART -> string(Res.string.desktop_server_restart)
            ServerInstallState.Phase.CANCELLED -> string(Res.string.desktop_server_cancelled)
            ServerInstallState.Phase.UNAVAILABLE -> string(texts.unavailable)
            ServerInstallState.Phase.FAILED -> failureText(st)
            ServerInstallState.Phase.REMOVING -> string(Res.string.desktop_server_removing)
            ServerInstallState.Phase.REMOVED -> string(Res.string.desktop_server_removed)
        }
        if (status != null) MutedText(status, modifier = Modifier.padding(horizontal = 12.dp))
        if (st.phase == ServerInstallState.Phase.DOWNLOADING) {
            Box(
                Modifier.padding(horizontal = 12.dp).fillMaxWidth().height(6.dp).background(Kelo.colors.line),
            ) {
                Box(Modifier.fillMaxWidth(st.progress.coerceIn(0f, 1f)).height(6.dp).background(Kelo.colors.accent))
            }
        }
        st.log?.takeIf { st.phase == ServerInstallState.Phase.FAILED }?.let { log ->
            LinkButton(string(Res.string.desktop_server_show_log), { openLog(log) }, modifier = Modifier.padding(horizontal = 12.dp))
        }
    }
    if (confirmRemove) {
        RemoveServerDialog(
            onConfirm = { deleteData -> confirmRemove = false; scope.launch { installer.remove(deleteData) } },
            onDismiss = { confirmRemove = false },
        )
    }
}

/** What a failed install says: the port in use, the older version refused, or the generic failure. */
@Composable
private fun failureText(st: ServerInstallState): String = when (st.failure) {
    ServerInstallState.Failure.PORT_BUSY -> string(Res.string.desktop_server_port_busy, st.port ?: MacServerRules.serverPort())
    ServerInstallState.Failure.DOWNGRADE -> string(Res.string.desktop_server_downgrade)
    else -> string(Res.string.desktop_server_failed)
}

/** "Remove the Kelo server from this Mac?" with the one choice that matters: delete the chats, keys and files too (off by default). */
@Composable
private fun RemoveServerDialog(onConfirm: (deleteData: Boolean) -> Unit, onDismiss: () -> Unit) {
    val c = Kelo.colors
    var deleteData by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.card,
        title = { Text(string(Res.string.desktop_server_remove_title), color = c.ink) },
        text = {
            Column {
                Text(string(Res.string.desktop_server_remove_body), color = c.ink)
                Row(
                    Modifier.fillMaxWidth().clickable { deleteData = !deleteData }.padding(vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = deleteData, onCheckedChange = { deleteData = it })
                    Text(string(Res.string.desktop_server_remove_data), color = c.ink)
                }
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(deleteData) }) { Text(string(Res.string.desktop_server_remove), color = c.danger) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(string(Res.string.cancel), color = c.ink) } },
    )
}

/** The install log in the system's own viewer (a text file); nothing runs from it. */
private fun openLog(path: String) {
    runCatching { Desktop.getDesktop().open(File(path)) }
}
