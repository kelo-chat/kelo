package chat.kelo.desktop.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
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
import chat.kelo.desktop.core.ServerInstallRules
import chat.kelo.desktop.core.ServerInstallState
import chat.kelo.desktop.core.ServerInstaller
import kotlinx.coroutines.launch
import java.awt.Desktop
import java.io.File

/** The name of the desktop's "Use this PC as the Kelo server" sub page (Windows only; one of [MyScreenHooks.extraPages]). */
const val MY_SERVER_PAGE = "desktop-server"

/**
 * Installs the Kelo server on this Windows PC with one button: the app downloads the signed MSI, checks it and runs it (Windows asks
 * for administrator permission once). No terminal. The MSI itself opens the setup page when it is done.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ServerInstallPage(installer: ServerInstaller, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val st by installer.state.collectAsState()
    val installed = remember(st.phase) { installer.installed() }
    LaunchedEffect(Unit) { installer.prepare() }
    SubPage(string(Res.string.desktop_server_row), onBack) {
        MutedText(string(Res.string.desktop_server_row_hint), modifier = Modifier.padding(horizontal = 12.dp), small = true)
        if (installed) MutedText(string(Res.string.desktop_server_installed), modifier = Modifier.padding(horizontal = 12.dp))
        FlowRow(
            Modifier.padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (installed) {
                PrimaryButton(
                    string(Res.string.desktop_server_open_setup),
                    { Browser.open("http://localhost:${ServerInstallRules.SERVER_PORT}/") },
                    enabled = !st.busy,
                )
                SecondaryButton(string(Res.string.desktop_server_update), { scope.launch { installer.install() } }, enabled = !st.busy)
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
            ServerInstallState.Phase.UNAVAILABLE -> string(Res.string.desktop_server_unavailable)
            ServerInstallState.Phase.FAILED -> string(Res.string.desktop_server_failed)
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
}

/** The install log in the system's own viewer (a text file); nothing runs from it. */
private fun openLog(path: String) {
    runCatching { Desktop.getDesktop().open(File(path)) }
}
