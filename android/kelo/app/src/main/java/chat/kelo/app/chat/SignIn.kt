package chat.kelo.app.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.connectors.ConnectorRules
import chat.kelo.app.connectors.ReloadOnReturn
import chat.kelo.app.connectors.openInBrowser
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** POST /api/providers/{name}/login/start, GET …/login/status and POST …/login/finish answer with this. */
@Serializable
data class SignInSession(
    val session: String = "",
    /** "device_code": open [url], type [userCode] there, the app notices by itself. "url_code": open [url], paste back the code */
    val kind: String = "",
    val url: String = "",
    @SerialName("user_code") val userCode: String? = null,
    @SerialName("expires_in") val expiresIn: Long = 0,
    /** pending | done | failed | expired */
    val state: String = "pending",
    val message: String? = null,
    val account: String? = null,
)

/** When the sign-in shows and what the server's answers mean. */
object SignInRules {
    enum class Problem { CONSENT, ALREADY, OFF, FAILED }

    /** A provider the app can sign in (the server says `signin`) that is not signed in now. */
    fun offered(signin: Boolean, signedIn: Boolean?): Boolean = signin && signedIn != true

    fun isDeviceCode(s: SignInSession): Boolean = s.kind == "device_code"

    /** The pasted code (or the address the page ended on) can be sent: one line without spaces. */
    fun pasteReady(text: String): Boolean = text.trim().let { it.isNotEmpty() && it.none(Char::isWhitespace) && it.length <= 4096 }

    fun finished(s: SignInSession): Boolean = s.state == "done"

    /** The attempt ended without signing in; the user starts again. */
    fun ended(s: SignInSession): Boolean = s.state == "failed" || s.state == "expired"

    fun problem(e: ApiError): Problem = when {
        e.status == 428 -> Problem.CONSENT
        e.status == 409 && e.detail.startsWith("already_signed_in") -> Problem.ALREADY
        e.status == 503 -> Problem.OFF
        else -> Problem.FAILED
    }

    /** The server's own sentence, without a leading machine word ("already_signed_in: …"). */
    fun readable(e: ApiError): String = if (e.isNetwork) "" else e.detail.substringAfter("already_signed_in: ").trim()
}

/**
 * 로그인 for a provider reached by signing in (Claude, Antigravity, ChatGPT, Qwen, MiniMax), all on the phone: the consent notice
 * first (when not given yet), then the provider's page in the browser and either a code to type there (the app notices by itself)
 * or a field to paste the code the page shows. [onSignedIn] runs once it worked (the caller loads the providers again; the row then
 * shows the account and 로그아웃 instead of this box).
 */
@Composable
fun SignInBox(provider: ProviderInfo, gate: ConsentGate, onSignedIn: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    val done by rememberUpdatedState(onSignedIn)
    val path = "/api/providers/${KeloApi.segment(provider.name)}/login"

    var s by remember(provider.name) { mutableStateOf<SignInSession?>(null) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    var paste by remember { mutableStateOf("") }
    var copied by remember { mutableStateOf(false) }
    var deadline by remember { mutableLongStateOf(0L) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val browserFailed = stringResource(R.string.connector_browser_failed)
    val failedText = stringResource(R.string.signin_failed)
    val alreadyText = stringResource(R.string.signin_already)

    fun take(next: SignInSession) {
        s = next
        if (next.state == "pending") deadline = System.currentTimeMillis() + next.expiresIn * 1000
        if (SignInRules.finished(next)) done()
    }

    fun fail(e: ApiError) {
        err = when (SignInRules.problem(e)) {
            SignInRules.Problem.ALREADY -> alreadyText
            else -> SignInRules.readable(e).ifEmpty { failedText }
        }
    }

    fun start() {
        if (busy) return
        busy = true; err = ""; paste = ""; copied = false
        scope.launch {
            try {
                take(sessions.requireApi().post<SignInSession>("$path/start", buildJsonObject {}))
            } catch (e: ApiError) {
                if (SignInRules.problem(e) == SignInRules.Problem.CONSENT) {
                    busy = false
                    gate.request(provider.copy(consent = null)) { start() }   // recorded elsewhere meanwhile, or asked again
                    return@launch
                }
                if (SignInRules.problem(e) == SignInRules.Problem.ALREADY) done()
                fail(e)
            } finally {
                busy = false
            }
        }
    }

    fun finish() {
        val cur = s ?: return
        busy = true; err = ""
        scope.launch {
            try {
                take(sessions.requireApi().post<SignInSession>("$path/finish", buildJsonObject {
                    put("session", cur.session); put("code", paste.trim())
                }))
                paste = ""
            } catch (e: ApiError) {
                if (e.status == 404 || e.status == 410) s = cur.copy(state = "expired")
                fail(e)
            } finally {
                busy = false
            }
        }
    }

    fun cancel() {
        val cur = s ?: return
        s = null; err = ""; paste = ""
        scope.launch { runCatching { sessions.requireApi().post<kotlinx.serialization.json.JsonElement>("$path/cancel", buildJsonObject { put("session", cur.session) }) } }
    }

    suspend fun refresh() {
        val cur = s ?: return
        if (cur.state != "pending" || !SignInRules.isDeviceCode(cur)) return
        try {
            take(sessions.requireApi().get<SignInSession>("$path/status?session=${KeloApi.segment(cur.session)}"))
        } catch (e: ApiError) {
            if (e.status == 404) s = cur.copy(state = "expired")
        }
    }

    // a code typed on the provider's page: ask every third second while it waits, and at once when the user comes back to the app
    val waiting = s?.let { it.state == "pending" } == true
    ReloadOnReturn(s?.session) { refresh() }
    LaunchedEffect(s?.session, waiting) {
        if (!waiting) return@LaunchedEffect
        var ticks = 0
        while (true) {
            delay(1000)
            now = System.currentTimeMillis()
            if (++ticks % 3 == 0) refresh()
            if (s?.state != "pending") break
        }
    }

    val name = provider.displayName.ifBlank { stringResource(R.string.signin_this_service) }
    val cur = s
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        when {
            cur == null -> {
                MutedText(stringResource(R.string.signin_intro, name), small = true)
                PrimaryButton(stringResource(if (busy) R.string.signin_starting else R.string.signin_button), onClick = { gate.request(provider) { start() } }, enabled = !busy)
            }
            SignInRules.finished(cur) -> MutedText(
                if (cur.account.isNullOrBlank()) stringResource(R.string.signin_done) else stringResource(R.string.signin_done_account, cur.account),
                small = true,
            )
            SignInRules.ended(cur) -> {
                ErrorText(cur.message?.takeIf { it.isNotBlank() } ?: stringResource(R.string.signin_ended), small = true)
                PrimaryButton(stringResource(R.string.signin_again), onClick = ::start, enabled = !busy)
            }
            else -> {
                val secs = ConnectorRules.secondsLeft(deadline, now)
                val device = SignInRules.isDeviceCode(cur)
                Text(stringResource(R.string.signin_step_open, name), style = MaterialTheme.typography.bodyMedium, color = Kelo.colors.ink)
                PrimaryButton(stringResource(R.string.signin_open_browser), onClick = { if (!openInBrowser(context, cur.url)) err = browserFailed })
                if (device) {
                    val code = cur.userCode.orEmpty()
                    if (code.isNotEmpty()) {
                        Text(stringResource(R.string.signin_step_code), style = MaterialTheme.typography.bodyMedium, color = Kelo.colors.ink)
                        CodeBlock(code)
                        SecondaryButton(
                            stringResource(if (copied) R.string.device_copied else R.string.device_copy),
                            onClick = { clipboard.setText(AnnotatedString(code)); copied = true }, modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    MutedText(stringResource(R.string.signin_device_wait, ConnectorRules.mmss(secs)), small = true)
                    LinkButton(stringResource(R.string.signin_check_now), onClick = ::finish)
                } else {
                    Text(stringResource(R.string.signin_step_paste), style = MaterialTheme.typography.bodyMedium, color = Kelo.colors.ink)
                    KeloTextField(
                        paste, { paste = it; err = "" }, stringResource(R.string.signin_paste_placeholder), maxLength = 4096,
                        modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Uri),
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        SecondaryButton(
                            stringResource(R.string.signin_paste), modifier = Modifier.weight(1f),
                            onClick = { clipboard.getText()?.text?.trim()?.let { if (it.isNotEmpty()) { paste = it; err = "" } } },
                        )
                        PrimaryButton(
                            stringResource(if (busy) R.string.signin_checking else R.string.signin_finish), onClick = ::finish,
                            enabled = !busy && SignInRules.pasteReady(paste), modifier = Modifier.weight(1f),
                        )
                    }
                    MutedText(stringResource(R.string.signin_paste_help, ConnectorRules.mmss(secs)), small = true)
                }
                LinkButton(stringResource(R.string.cancel), onClick = ::cancel)
            }
        }
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
}

@Composable
private fun CodeBlock(code: String) {
    val c = Kelo.colors
    val label = stringResource(R.string.device_code_label)
    SelectionContainer {
        Text(
            code,
            modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(c.bubble)
                .padding(horizontal = 8.dp, vertical = 14.dp).semantics { contentDescription = label },
            color = c.ink, textAlign = TextAlign.Center, fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold, fontSize = 26.sp, letterSpacing = 3.sp,
        )
    }
}
