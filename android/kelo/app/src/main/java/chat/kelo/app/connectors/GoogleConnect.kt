package chat.kelo.app.connectors

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import kotlinx.coroutines.launch

/**
 * Google Calendar: the Google sign-in first (only calendar permissions are asked; the same Google account can also hold Gmail), each
 * connected account with its own disconnect, and the service account kept as the other way ([Connector.fallback]).
 * When the operator has not set up the Google client, the server's reason is shown in words and the other way stays open.
 * While new Google sign-ins wait for Google's review ([ConnectorRules.pendingReview] / [GoogleCalendarState.pendingReview]) there is no
 * sign-in button: the note, and the phone calendar as the other way ([onPhoneCalendar] goes to it under 이 기기에서). Accounts already
 * connected stay, each with its disconnect.
 */
@Composable
internal fun GoogleConnect(c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?, onPhoneCalendar: (() -> Unit)? = null) {
    val sessions = LocalAppContainer.current.sessions
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var state by remember(c.id) { mutableStateOf<GoogleCalendarState?>(null) }
    var failed by remember(c.id) { mutableStateOf(false) }
    var err by remember(c.id) { mutableStateOf("") }
    var busy by remember(c.id) { mutableStateOf(false) }
    var removing by remember { mutableStateOf<GoogleCalendarAccount?>(null) }
    var other by rememberSaveable(c.id) { mutableStateOf(false) }

    suspend fun load() {
        try {
            state = ConnectorCalls.googleCalendar(sessions.requireApi()); failed = false
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e   // left the screen: not a failure
        } catch (_: Exception) {
            failed = true
        }
    }
    // back from Google's page: the button is ready again and the accounts are read again
    ReloadOnReturn(c.id) { load(); busy = false }

    removing?.let { a ->
        ConfirmDialog(
            stringResource(R.string.gcal_disconnect_confirm, a.address), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(sessions.requireApi(), a.name) }; load(); onChanged() } },
            onDismiss = { removing = null },
        )
    }

    val s = state
    if (s == null) {
        if (failed) ErrorText(stringResource(R.string.connect_failed), small = true) else chat.kelo.app.ui.components.SkeletonLines(2)
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        s.accounts.forEach { a ->
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                ShieldTitle(a.address)
                MutedText(
                    stringResource(
                        when {
                            a.problem -> R.string.gcal_account_problem
                            a.canWrite -> R.string.gcal_account_note
                            else -> R.string.gcal_account_read_only
                        },
                    ),
                    small = true,
                )
                LinkButton(stringResource(R.string.connector_disconnect), onClick = { removing = a }, danger = true)
            }
        }
        if (s.accounts.isNotEmpty() && onNotify != null) {
            val text = stringResource(R.string.chat_connect_notify, c.name)
            PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
        }
        val pending = ConnectorRules.pendingReview(c) || s.pendingReview
        if (pending) {
            if (s.accounts.isEmpty() || s.accounts.any { it.problem }) {
                ReviewNote(c, onOpened = { scope.launch { load() }; onChanged() })
                MutedText(stringResource(R.string.google_review_phone_calendar_help), small = true)
                if (onPhoneCalendar != null) LinkButton(stringResource(R.string.google_review_phone_calendar), onClick = onPhoneCalendar)
            }
        } else if (s.accounts.isEmpty()) BoldLine(stringResource(R.string.gcal_title))
        if (pending) {
            // no Google sign-in button until the review is done
        } else if (s.available) {
            val label = when {
                busy -> stringResource(R.string.connector_opening)
                s.accounts.isEmpty() -> stringResource(R.string.gcal_sign_in)
                s.accounts.any { it.problem } -> stringResource(R.string.gcal_sign_in_again)
                else -> stringResource(R.string.gcal_add_account)
            }
            val start: () -> Unit = {
                err = ""; busy = true
                scope.launch {
                    try {
                        val url = ConnectorCalls.googleWebStart(sessions.requireApi(), "calendar")
                        if (!openInBrowser(context, url)) { err = context.getString(R.string.connector_browser_failed); busy = false }
                    } catch (e: ApiError) {
                        err = e.detail; busy = false
                    }
                }
            }
            if (s.accounts.isEmpty() || s.accounts.any { it.problem }) PrimaryButton(label, enabled = !busy, onClick = start)
            else SecondaryButton(label, onClick = start, enabled = !busy)
            if (s.accounts.isEmpty()) MutedText(stringResource(R.string.gcal_help), small = true)
        } else if (s.accounts.isEmpty()) {
            MutedText(s.reason.ifBlank { stringResource(R.string.gcal_not_ready) }, small = true)
        }
        if (err.isNotEmpty()) ErrorText(err, small = true)

        val fb = c.fallback
        if (fb != null && fb.connect == "key" && (!pending || fb.connected)) {
            if (other || fb.connected) {
                BoldLine(stringResource(R.string.gcal_other_way))
                KeyConnect(c.copy(connect = "key", via = fb.via, item = fb.item, description = fb.blurb), onChanged, null)
            } else {
                LinkButton(stringResource(R.string.gcal_other_way_open), onClick = { other = true })
            }
        }
        OperatorSettings("google", onSaved = { scope.launch { load() } })
    }
}
