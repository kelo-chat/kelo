package chat.kelo.desktop.pages.my

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.connectors.ConnectorCalls
import chat.kelo.app.connectors.OAuthApp
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Sessions
import chat.kelo.app.my.BetaAnswer
import chat.kelo.app.my.BetaRequestRow
import chat.kelo.app.my.BetaRequests
import chat.kelo.app.my.CloudflareConfig
import chat.kelo.app.my.CloudflareForm
import chat.kelo.app.my.FirebaseConfig
import chat.kelo.app.my.OperatorBetaRules
import chat.kelo.app.my.OperatorReportRules
import chat.kelo.app.my.OperatorRules
import chat.kelo.app.my.ReceivedReport
import chat.kelo.app.my.ReceivedReports
import chat.kelo.app.my.Reclaimed
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.my.ops.FieldLabel
import chat.kelo.desktop.pages.my.ops.KeyLine
import chat.kelo.desktop.pages.my.ops.Labeled
import chat.kelo.desktop.pages.my.ops.Loading
import chat.kelo.desktop.pages.my.ops.dayTime
import chat.kelo.desktop.ui.Card
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File

/**
 * 마이 → 운영자 (android/kelo .../my/OperatorPage.kt): only for the person whose server other copies of Kelo lean on, and only when
 * this device is the owner's (the My page shows the row only then; any other device gets 403 and the cards say so). Sign-in apps
 * (GitHub, Google), the push key, the free addresses (Cloudflare), beta requests for Google sign-in, and reports of bad replies.
 * Secrets go in and never come back out: password fields, nothing echoed.
 */
@Composable
fun OperatorPage(app: AppContainer, onBack: () -> Unit) {
    val sessions = app.sessions
    SubPage(stringResource(R.string.operator_page_title), onBack = onBack) {
        MutedText(stringResource(R.string.operator_page_intro), small = true)
        SectionLabel(stringResource(R.string.operator_section_signin))
        OperatorSettings(sessions, "github")
        OperatorSettings(sessions, "google")
        SectionLabel(stringResource(R.string.operator_section_push))
        FirebaseCard(sessions)
        SectionLabel(stringResource(R.string.operator_section_addresses))
        CloudflareCard(sessions)
        SectionLabel(stringResource(R.string.operator_section_beta))
        BetaRequestsCard(sessions)
        SectionLabel(stringResource(R.string.operator_section_reports))
        ReportsCard(sessions)
    }
}

@Composable
private fun BoldLine(text: String) = Text(text, fontWeight = FontWeight.Bold, color = Kelo.colors.ink, style = MaterialTheme.typography.bodyLarge)

/** The OAuth app (GitHub or Google) everyone signs in with; the secret can be replaced, never read back (connectors/DeviceConnect.kt). */
@Composable
private fun OperatorSettings(sessions: Sessions, provider: String) {
    val scope = rememberCoroutineScope()
    val github = provider == "github"
    var app by remember(provider) { mutableStateOf<OAuthApp?>(null) }
    var cid by remember(provider) { mutableStateOf("") }
    var secret by remember(provider) { mutableStateOf("") }
    var msg by remember(provider) { mutableStateOf("") }
    var owner by remember(provider) { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(provider) {
        try {
            val a = ConnectorCalls.oauthApp(sessions.requireApi(), provider)
            app = a; cid = a.clientId; owner = true
        } catch (e: ApiError) {
            owner = if (e.status == 403) false else null
        }
    }
    if (owner == false) return
    val a = app ?: run { Card { Loading() }; return }
    val secretHint = stringResource(if (github) R.string.operator_secret_github else R.string.operator_secret_google)
    Card {
        BoldLine(stringResource(if (github) R.string.operator_title_github else R.string.operator_title_google))
        MutedText(stringResource(R.string.operator_note), small = true)
        if (a.callback.isNotBlank()) {
            Text(stringResource(if (github) R.string.operator_callback_github else R.string.operator_callback_google, a.callback),
                style = MaterialTheme.typography.bodySmall, color = Kelo.colors.ink)
        }
        KeloTextField(cid, { cid = it }, stringResource(R.string.client_id), maxLength = 100, label = stringResource(R.string.client_id))
        KeloTextField(secret, { secret = it }, if (a.hasSecret) stringResource(R.string.operator_secret_saved, secretHint) else secretHint,
            password = true, maxLength = 200)
        val savedText = stringResource(R.string.operator_saved)
        PrimaryButton(stringResource(R.string.save), enabled = cid.isNotBlank(), onClick = {
            msg = ""
            scope.launch {
                try {
                    val saved = ConnectorCalls.saveOauthApp(sessions.requireApi(), provider, cid.trim(), secret.trim())
                    secret = ""; app = saved; cid = saved.clientId
                    msg = savedText
                } catch (e: ApiError) {
                    msg = e.detail
                }
            }
        })
        MutedText(stringResource(if (a.isBroker) R.string.operator_broker_on else R.string.operator_broker_off), small = true)
        if (!github) GoogleOpenSwitch(sessions, a.openToAll, onChanged = { app = it })
        if (msg.isNotEmpty()) MutedText(msg, small = true)
    }
}

/** Google only: new sign-ins open to everyone, after Google's verification (PUT /api/oauth-apps/google/open). */
@Composable
private fun GoogleOpenSwitch(sessions: Sessions, on: Boolean, onChanged: (OAuthApp) -> Unit) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    val title = stringResource(R.string.google_review_open_switch)
    Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            Text(stringResource(R.string.google_review_open_warning), style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(
            checked = on, enabled = !busy,
            onCheckedChange = { v ->
                busy = true; err = ""
                scope.launch {
                    try { onChanged(ConnectorCalls.setGoogleOpen(sessions.requireApi(), v)) } catch (e: ApiError) { err = e.detail } finally { busy = false }
                }
            },
            modifier = Modifier.semantics { contentDescription = title },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
    if (err.isNotEmpty()) ErrorText(err, small = true)
}

/** The system's open dialog for the service-account key (a small JSON file); null when nothing was picked. */
private fun pickKeyFile(): File? {
    val d = java.awt.FileDialog(null as java.awt.Frame?, "", java.awt.FileDialog.LOAD)
    d.isVisible = true
    val dir = d.directory ?: return null
    val f = d.file ?: return null
    return File(dir, f)
}

@Composable
private fun FirebaseCard(sessions: Sessions) {
    val scope = rememberCoroutineScope()
    var cfg by remember { mutableStateOf<FirebaseConfig?>(null) }
    var failed by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    var confirmOff by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        runCatching { sessions.requireApi().get<FirebaseConfig>("/api/operator/firebase") }.onSuccess { cfg = it }.onFailure { failed = true }
    }
    val notKey = stringResource(R.string.operator_fb_not_key)
    val saved = stringResource(R.string.operator_fb_saved)
    fun upload() {
        val file = pickKeyFile() ?: return
        busy = true; err = ""; msg = ""
        scope.launch {
            val bytes = withContext(Dispatchers.IO) {
                runCatching { file.inputStream().use { it.readNBytes(OperatorRules.MAX_KEY_FILE + 1) } }.getOrNull()
            }
            try {
                if (bytes == null || !OperatorRules.keyFileOk(bytes)) { err = notKey; return@launch }
                val out = sessions.requireApi().requestBytes("PUT", "/api/operator/firebase", bytes)
                cfg = KeloApi.json.decodeFromJsonElement(FirebaseConfig.serializer(), out)
                msg = saved
            } catch (e: ApiError) {
                err = e.detail
            } catch (e: IllegalArgumentException) {
                err = notKey
            } finally {
                bytes?.fill(0)
                busy = false
            }
        }
    }
    Card {
        BoldLine(stringResource(R.string.operator_fb_title))
        MutedText(stringResource(R.string.operator_fb_note), small = true)
        val c = cfg
        when {
            c == null && failed -> ErrorText(stringResource(R.string.connect_failed), small = true)
            c == null -> Loading()
            c.enabled -> {
                KeyLine(stringResource(R.string.operator_fb_project), c.projectId)
                KeyLine(stringResource(R.string.operator_fb_account), c.clientEmail)
            }
            else -> MutedText(stringResource(R.string.operator_fb_off), small = true)
        }
        if (c != null) {
            PrimaryButton(
                stringResource(if (busy) R.string.operator_fb_checking else if (c.enabled) R.string.operator_fb_replace else R.string.operator_fb_upload),
                enabled = !busy, onClick = ::upload,
            )
            if (c.enabled) LinkButton(stringResource(R.string.operator_fb_remove), onClick = { confirmOff = true }, danger = true)
        }
        if (msg.isNotEmpty()) MutedText(msg, small = true)
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
    if (confirmOff) {
        ConfirmDialog(stringResource(R.string.operator_fb_remove_confirm), stringResource(R.string.operator_fb_remove), onDismiss = { confirmOff = false }, onYes = {
            scope.launch {
                runCatching { sessions.requireApi().delete<FirebaseConfig>("/api/operator/firebase") }
                    .onSuccess { cfg = it; msg = ""; err = "" }.onFailure { err = (it as? ApiError)?.detail.orEmpty() }
            }
        })
    }
}

@Composable
private fun CloudflareCard(sessions: Sessions) {
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var cfg by remember { mutableStateOf<CloudflareConfig?>(null) }
    var failed by remember { mutableStateOf(false) }
    var form by remember { mutableStateOf(CloudflareForm()) }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    var confirmOff by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        runCatching { sessions.requireApi().get<CloudflareConfig>("/api/operator/cloudflare") }
            .onSuccess { cfg = it; form = OperatorRules.formOf(it) }.onFailure { failed = true }
    }
    Card {
        BoldLine(stringResource(R.string.operator_cf_title))
        MutedText(stringResource(R.string.operator_cf_note), small = true)
        val c = cfg
        if (c == null) {
            if (failed) ErrorText(stringResource(R.string.connect_failed), small = true) else Loading()
            return@Card
        }
        KeyLine(stringResource(R.string.operator_cf_state), stringResource(if (c.enabled) R.string.operator_cf_on else R.string.operator_cf_off))
        KeyLine(stringResource(R.string.operator_cf_used), stringResource(R.string.operator_cf_used_value, c.used, c.maxNames))
        FieldLabel(stringResource(R.string.operator_cf_token))
        KeloTextField(form.token, { form = form.copy(token = it) }, stringResource(if (c.hasToken) R.string.operator_cf_token_saved else R.string.operator_cf_token),
            password = true, maxLength = 200)
        FieldLabel(stringResource(R.string.operator_cf_account))
        KeloTextField(form.accountId, { form = form.copy(accountId = it) }, stringResource(R.string.operator_cf_account), maxLength = 32)
        FieldLabel(stringResource(R.string.operator_cf_zone_id))
        KeloTextField(form.zoneId, { form = form.copy(zoneId = it) }, stringResource(R.string.operator_cf_zone_id), maxLength = 32)
        FieldLabel(stringResource(R.string.operator_cf_zone))
        KeloTextField(form.zoneName, { form = form.copy(zoneName = it) }, stringResource(R.string.operator_cf_zone), maxLength = 253)
        FieldLabel(stringResource(R.string.operator_cf_max))
        KeloTextField(form.maxNames, { form = form.copy(maxNames = it.filter(Char::isDigit)) }, stringResource(R.string.operator_cf_max), maxLength = 6)
        val savedText = stringResource(R.string.operator_saved)
        PrimaryButton(stringResource(if (busy) R.string.operator_cf_checking else R.string.save), enabled = !busy, onClick = {
            msg = ""; err = ""
            val problem = OperatorRules.problem(form)
            if (problem != null) { err = strings.get(problem); return@PrimaryButton }
            busy = true
            scope.launch {
                try {
                    val out = sessions.requireApi().put<CloudflareConfig>("/api/operator/cloudflare", OperatorRules.cloudflareBody(form))
                    cfg = out; form = OperatorRules.formOf(out)   // the token field empties: it is never shown again
                    msg = savedText
                } catch (e: ApiError) {
                    err = e.detail
                } finally {
                    busy = false
                }
            }
        })
        SecondaryButton(stringResource(R.string.operator_cf_reclaim), enabled = c.enabled && !busy, onClick = {
            msg = ""; err = ""; busy = true
            scope.launch {
                try {
                    val r = sessions.requireApi().post<Reclaimed>("/api/operator/cloudflare/reclaim")
                    msg = if (r.released.isEmpty()) strings.get(R.string.operator_cf_reclaim_none)
                    else strings.plural(R.plurals.operator_cf_reclaimed, r.released.size, r.released.size)
                    runCatching { sessions.requireApi().get<CloudflareConfig>("/api/operator/cloudflare") }.onSuccess { cfg = it }
                } catch (e: ApiError) {
                    err = e.detail
                } finally {
                    busy = false
                }
            }
        })
        MutedText(pluralStringResource(R.plurals.operator_cf_idle, c.idleDays, c.idleDays), small = true)
        if (c.hasToken) LinkButton(stringResource(R.string.operator_cf_turn_off), onClick = { confirmOff = true }, danger = true)
        if (msg.isNotEmpty()) MutedText(msg, small = true)
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
    if (confirmOff) {
        ConfirmDialog(stringResource(R.string.operator_cf_off_confirm), stringResource(R.string.operator_cf_turn_off), onDismiss = { confirmOff = false }, onYes = {
            scope.launch {
                runCatching { sessions.requireApi().put<CloudflareConfig>("/api/operator/cloudflare", OperatorRules.cloudflareOff()) }
                    .onSuccess { cfg = it; form = OperatorRules.formOf(it) }.onFailure { err = (it as? ApiError)?.detail.orEmpty() }
            }
        })
    }
}

/** 운영자 → 베타 신청: people asking to try Google sign-in while the Google app is in Testing mode; approve or decline. */
@Composable
private fun BetaRequestsCard(sessions: Sessions) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<BetaRequests?>(null) }
    var failed by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    var msg by remember { mutableStateOf("") }
    var declining by remember { mutableStateOf<BetaRequestRow?>(null) }
    var deleting by remember { mutableStateOf<Long?>(null) }
    suspend fun load() {
        runCatching { sessions.requireApi().get<BetaRequests>("/api/operator/beta") }.onSuccess { data = it; failed = false }.onFailure { failed = true }
    }
    LaunchedEffect(Unit) { load() }
    val mailedText = stringResource(R.string.operator_beta_mailed, "%s")
    fun answer(r: BetaRequestRow, action: String, mail: Boolean) {
        err = ""; msg = ""
        scope.launch {
            try {
                val a = sessions.requireApi().post<BetaAnswer>("/api/operator/beta/${r.id}", buildJsonObject { put("action", action); put("email", mail) })
                msg = if (a.mailed) mailedText.replace("%s", r.email) else a.mailError
                load()
            } catch (e: ApiError) {
                err = e.detail
            }
        }
    }
    Card {
        MutedText(stringResource(R.string.operator_beta_note), small = true)
        val d = data ?: run {
            if (failed) ErrorText(stringResource(R.string.connect_failed), small = true) else Loading()
            return@Card
        }
        if (!d.receiving) MutedText(stringResource(R.string.operator_beta_not_receiving), small = true)
        if (d.mailFrom.isNotBlank()) MutedText(stringResource(R.string.operator_beta_mail_from, d.mailFrom), small = true)
        else Text(stringResource(R.string.operator_beta_no_mail), color = Kelo.colors.danger, style = MaterialTheme.typography.bodySmall)
        Text(stringResource(R.string.operator_beta_open, d.open), color = Kelo.colors.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
        if (d.requests.isEmpty()) MutedText(stringResource(R.string.operator_beta_empty), small = true)
        for (r in d.requests) BetaRow(r, onApprove = { answer(r, "approve", true) }, onDecline = { declining = r }, onDelete = { deleting = r.id })
        if (msg.isNotEmpty()) MutedText(msg, small = true)
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
    declining?.let { r ->
        var mail by remember(r.id) { mutableStateOf(false) }
        val c = Kelo.colors
        AlertDialog(
            onDismissRequest = { declining = null }, containerColor = c.card,
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(stringResource(R.string.operator_beta_decline_confirm), color = c.ink, style = MaterialTheme.typography.bodyLarge)
                    Text(r.email, color = c.muted, style = MaterialTheme.typography.bodySmall)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = mail, onCheckedChange = { mail = it }, colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent))
                        Text(stringResource(R.string.operator_beta_decline_mail), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    }
                }
            },
            confirmButton = { TextButton(onClick = { declining = null; answer(r, "decline", mail) }) { Text(stringResource(R.string.operator_beta_decline), color = c.danger) } },
            dismissButton = { TextButton(onClick = { declining = null }) { Text(stringResource(R.string.cancel), color = c.ink) } },
        )
    }
    deleting?.let { id ->
        ConfirmDialog(stringResource(R.string.operator_reports_delete_confirm), stringResource(R.string.operator_reports_delete), onDismiss = { deleting = null }, onYes = {
            deleting = null
            scope.launch {
                try { sessions.requireApi().request("DELETE", "/api/operator/beta/$id"); load() } catch (e: ApiError) { err = e.detail }
            }
        })
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BetaRow(r: BetaRequestRow, onApprove: () -> Unit, onDecline: () -> Unit, onDelete: () -> Unit) {
    val c = Kelo.colors
    val whenText = dayTime((r.createdAt * 1000).toLong())
    val (stateText, stateColor) = when (r.status) {
        "approved" -> stringResource(R.string.operator_beta_state_approved) to c.success
        "declined" -> stringResource(R.string.operator_beta_declined) to c.muted
        else -> stringResource(R.string.operator_beta_waiting) to c.accent
    }
    Column(Modifier.fillMaxWidth().background(c.bubble, RoundedCornerShape(12.dp)).padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(r.email, color = c.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
        Text(OperatorBetaRules.products(r.products) + " · " + whenText, color = c.muted, style = MaterialTheme.typography.bodySmall)
        Text(stateText, color = stateColor, style = MaterialTheme.typography.bodySmall)
        when (r.notice) {
            "no_mailbox" -> Text(stringResource(R.string.operator_beta_notice_no_mail), color = c.muted, style = MaterialTheme.typography.bodySmall)
            "failed" -> Text(stringResource(R.string.operator_beta_notice_failed), color = c.danger, style = MaterialTheme.typography.bodySmall)
        }
        if (OperatorBetaRules.canApprove(r)) {
            Text(stringResource(R.string.operator_beta_steps, r.email), color = c.ink, style = MaterialTheme.typography.bodySmall)
            PrimaryButton(stringResource(R.string.operator_beta_approve), onClick = onApprove)
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (OperatorBetaRules.canDecline(r)) SecondaryButton(stringResource(R.string.operator_beta_decline), onClick = onDecline)
            LinkButton(stringResource(R.string.operator_reports_delete), onClick = onDelete, danger = true)
        }
    }
}

/** 운영자 → 받은 신고: what users reported from inside the app, newest first; mark handled or delete. */
@Composable
private fun ReportsCard(sessions: Sessions) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<ReceivedReports?>(null) }
    var failed by remember { mutableStateOf(false) }
    var showHandled by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    var deleting by remember { mutableStateOf<Long?>(null) }
    suspend fun load() {
        runCatching { sessions.requireApi().get<ReceivedReports>(OperatorReportRules.path(showHandled)) }.onSuccess { data = it; failed = false }.onFailure { failed = true }
    }
    LaunchedEffect(showHandled) { load() }
    Card {
        MutedText(stringResource(R.string.operator_reports_note), small = true)
        val d = data ?: run {
            if (failed) ErrorText(stringResource(R.string.connect_failed), small = true) else Loading()
            return@Card
        }
        if (!d.receiving) MutedText(stringResource(R.string.operator_reports_not_receiving), small = true)
        Text(stringResource(R.string.operator_reports_open, d.open), color = Kelo.colors.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
        if (d.reports.isEmpty()) MutedText(stringResource(R.string.operator_reports_empty), small = true)
        for (r in d.reports) ReportRow(r, onMark = { handled ->
            scope.launch {
                try {
                    sessions.requireApi().request("POST", "/api/operator/reports/${r.id}", buildJsonObject { put("handled", handled) })
                    load()
                } catch (e: ApiError) {
                    err = e.detail
                }
            }
        }, onDelete = { deleting = r.id })
        LinkButton(stringResource(if (showHandled) R.string.operator_reports_hide_handled else R.string.operator_reports_show_handled), onClick = { showHandled = !showHandled })
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
    deleting?.let { id ->
        ConfirmDialog(stringResource(R.string.operator_reports_delete_confirm), stringResource(R.string.operator_reports_delete), onDismiss = { deleting = null }, onYes = {
            deleting = null
            scope.launch {
                try { sessions.requireApi().request("DELETE", "/api/operator/reports/$id"); load() } catch (e: ApiError) { err = e.detail }
            }
        })
    }
}

/** A report's reason in words (the phone's chat/ReportSheet.kt ReportReason); the server's word for one the app does not know. */
@Composable
private fun reasonText(wire: String): String = when (wire) {
    "harmful" -> stringResource(R.string.report_reason_harmful)
    "inaccurate" -> stringResource(R.string.report_reason_inaccurate)
    "privacy" -> stringResource(R.string.report_reason_privacy)
    "other" -> stringResource(R.string.report_reason_other)
    else -> wire
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ReportRow(r: ReceivedReport, onMark: (Boolean) -> Unit, onDelete: () -> Unit) {
    val c = Kelo.colors
    val unknown = stringResource(R.string.operator_reports_unknown)
    val whenText = dayTime((r.createdAt * 1000).toLong())
    Column(Modifier.fillMaxWidth().background(c.bubble, RoundedCornerShape(12.dp)).padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(reasonText(r.reason), color = c.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            if (r.handled) Text(stringResource(R.string.operator_reports_handled), color = c.success, style = MaterialTheme.typography.bodySmall)
        }
        Text(stringResource(R.string.operator_reports_meta, whenText, OperatorReportRules.modelName(r) ?: unknown, r.appVersion.ifBlank { unknown }),
            color = c.muted, style = MaterialTheme.typography.bodySmall)
        Labeled(stringResource(R.string.operator_reports_reply), r.reply)
        if (r.asked.isNotBlank()) Labeled(stringResource(R.string.operator_reports_asked), r.asked)
        if (r.comment.isNotBlank()) Labeled(stringResource(R.string.operator_reports_comment), r.comment)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SecondaryButton(stringResource(if (r.handled) R.string.operator_reports_unmark else R.string.operator_reports_mark), onClick = { onMark(!r.handled) })
            LinkButton(stringResource(R.string.operator_reports_delete), onClick = onDelete, danger = true)
        }
    }
}
