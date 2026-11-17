package chat.kelo.app.connectors

import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import chat.kelo.app.ui.text.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.rememberCoroutineScope
import chat.kelo.app.AppServices
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.LocalAppServices
import chat.kelo.app.platform.LocalToaster
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.loadString
import chat.kelo.app.ui.text.string
import kotlinx.coroutines.launch

/**
 * The question after a disconnect (or a vault delete) that left data behind: "<name>에서 가져온 자료도 지울까요?" with the counts and,
 * for files, that they go to the library's trash. "지우기" deletes it (POST …/forget) and a toast says what went; "남겨두기" keeps it.
 * Drawn once by the app's shell; it shows only while [Sessions.forgetOffer] has an offer.
 */
@Composable
fun ForgetDataHost(services: AppServices = LocalAppServices.current) {
    val sessions = services.sessions
    val offer = sessions.forgetOffer.collectAsState().value ?: return
    val toaster = LocalToaster.current
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = { sessions.dismissForget() },
        containerColor = c.card,
        title = { Text(string(Res.string.connector_forget_title, offer.name), color = c.ink) },
        text = {
            Column {
                Text(ForgetRules.parts(offer.data).map { (part, n) -> string(ForgetRules.resource(part), n) }.joinToString(" · "), color = c.ink)
                if (ForgetRules.mentionsTrash(offer.data)) Text(string(Res.string.connector_forget_trash), color = c.muted)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                scope.launch {
                    val gone = runCatching { ConnectorCalls.forget(sessions.requireApi(), offer.id) }.getOrNull()
                    sessions.dismissForget()
                    if (gone != null) toaster.show(loadString(Res.string.connector_forget_done, forgetCountsText(gone)))
                }
            }) { Text(string(Res.string.connector_forget_delete), color = c.danger) }
        },
        dismissButton = { TextButton(onClick = { sessions.dismissForget() }) { Text(string(Res.string.connector_forget_keep), color = c.ink) } },
    )
}
