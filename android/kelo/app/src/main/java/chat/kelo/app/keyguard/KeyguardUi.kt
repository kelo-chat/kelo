package chat.kelo.app.keyguard

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.border
import androidx.compose.ui.draw.shadow
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.DeviceList
import chat.kelo.app.my.ConfirmDialog
import chat.kelo.app.my.MyCard
import chat.kelo.app.my.SubPage
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.findActivity
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch

/** The Keyguard key of the signed-in server and its prompt (null when signed out). */
@Composable
fun rememberKeyguardAuthenticator(): KeyguardAuthenticator? {
    val context = LocalContext.current
    val api by LocalAppContainer.current.sessions.api.collectAsStateWithLifecycle()
    val url = api?.baseUrl ?: return null
    val activity = context.findActivity() as? FragmentActivity
    return remember(activity, url) { KeyguardAuthenticator(activity, KeyguardKey(context, url)) }
}

/** Why an unlock or enrol did not happen, as a string resource (null: the owner backed out, nothing to say). */
private fun authProblem(a: KeyguardAuth): Int? = when (a) {
    is KeyguardAuth.Ok, KeyguardAuth.Cancelled -> null
    KeyguardAuth.NoLock -> R.string.keyguard_no_lock
    KeyguardAuth.KeyLost -> R.string.keyguard_key_lost
    is KeyguardAuth.Failed -> R.string.keyguard_failed
}

/**
 * Unlocks Kelo with this phone's copy: GET /api/keyguard/blob (KE2E), the owner's check releases the Keystore key, the blob opens
 * here and the key goes to POST /api/keyguard/unlock once; its bytes are wiped. [onResult]: null when unlocked, else what to say
 * (a string resource), [R.string.keyguard_locked_other] when this phone holds no copy.
 */
class KeyguardUnlocker(private val auth: KeyguardAuthenticator, private val sessions: chat.kelo.app.core.Sessions) {
    companion object {
        /** The owner backed out of the check: nothing to say */
        const val CANCELLED = 0
    }

    suspend fun blobIv(): Pair<ByteArray, ByteArray>? {
        val b = try { KeyguardCalls.blob(sessions.requireApi()) } catch (e: ApiError) {
            if (e.status == 404) return null else throw e
        }
        return KeyguardBlob.decode(b.blob)
    }

    fun unlock(scope: kotlinx.coroutines.CoroutineScope, title: String, subtitle: String, onResult: (Int?) -> Unit) {
        scope.launch {
            val parts = try { blobIv() } catch (e: ApiError) { return@launch onResult(R.string.keyguard_failed) }
                ?: return@launch onResult(R.string.keyguard_locked_other)
            val (iv, sealed) = parts
            auth.forUnwrap(iv, title, subtitle) { a ->
                if (a !is KeyguardAuth.Ok) return@forUnwrap onResult(authProblem(a) ?: CANCELLED)
                val key = try { a.cipher.doFinal(sealed) } catch (e: Exception) { return@forUnwrap onResult(R.string.keyguard_key_lost) }
                scope.launch {
                    try {
                        KeyguardCalls.unlock(sessions.requireApi(), key)
                        sessions.setLocked(false)
                        onResult(null)
                    } catch (e: ApiError) {
                        onResult(if (e.code == KeyguardRules.CODE_WRONG_KEY) R.string.keyguard_key_lost else R.string.keyguard_failed)
                    } finally {
                        key.fill(0)
                    }
                }
            }
        }
    }
}

/**
 * "Kelo is locked" under the header while the server holds no key (423 answers, /api/agent/status "locked", the keyguard_locked
 * notification): Unlock with this phone's copy, or, on a phone without one, the recovery code in 마이 → Security.
 */
@Composable
fun LockedBanner(onRecover: () -> Unit, autoStart: Boolean, onAutoStarted: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val container = LocalAppContainer.current
    val auth = rememberKeyguardAuthenticator() ?: return
    val scope = rememberCoroutineScope()
    val unlocker = remember(auth) { KeyguardUnlocker(auth, container.sessions) }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<Int?>(null) }
    val title = stringResource(R.string.keyguard_prompt_title)
    val subtitle = stringResource(R.string.keyguard_prompt_subtitle)
    fun go() {
        if (busy) return
        busy = true; problem = null
        unlocker.unlock(scope, title, subtitle) { p -> busy = false; problem = p.takeIf { it != KeyguardUnlocker.CANCELLED } }
    }
    // opened from the keyguard_locked notification: the check starts by itself, once
    LaunchedEffect(autoStart) { if (autoStart) { onAutoStarted(); go() } }
    Column(
        // floats over the chat like the other cards under the header: a shadow and a line so it never reads as part of a message
        modifier.fillMaxWidth().shadow(8.dp, RoundedCornerShape(14.dp)).clip(RoundedCornerShape(14.dp)).background(c.card)
            .border(1.dp, c.line, RoundedCornerShape(14.dp)).padding(16.dp).semantics { liveRegion = LiveRegionMode.Polite },
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Icon(KeloIcons.Lock, contentDescription = null, tint = c.accent, modifier = Modifier.size(22.dp))
            Text(stringResource(R.string.keyguard_locked_title), style = MaterialTheme.typography.titleMedium, color = c.ink,
                modifier = Modifier.weight(1f).semantics { heading() })
        }
        val other = problem == R.string.keyguard_locked_other
        MutedText(stringResource(if (other) R.string.keyguard_locked_other else R.string.keyguard_locked_body), small = true)
        problem?.takeIf { !other }?.let { ErrorText(stringResource(it), small = true) }
        if (!other) PrimaryButton(stringResource(if (busy) R.string.keyguard_unlocking else R.string.keyguard_unlock), ::go, enabled = !busy, modifier = Modifier.fillMaxWidth())
        SecondaryButton(stringResource(R.string.keyguard_use_code), onRecover, modifier = Modifier.fillMaxWidth())
    }
}

/** 마이 → Security → the phone key: turn it on, the phones that hold it, unlock or recover, remove this phone's copy, turn it off. */
@Composable
fun KeyguardPage(onBack: () -> Unit, startWithCode: Boolean = false, enableNow: Boolean = false, onEnabled: () -> Unit = {}) {
    val c = Kelo.colors
    val container = LocalAppContainer.current
    val sessions = container.sessions
    val scope = rememberCoroutineScope()
    val auth = rememberKeyguardAuthenticator()
    var view by remember { mutableStateOf<KeyguardView?>(null) }
    var me by remember { mutableStateOf<Long?>(null) }
    var notOwner by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var note by remember { mutableStateOf<Int?>(null) }
    var recovery by remember { mutableStateOf<String?>(null) }   // shown once, then forgotten
    var wroteDown by remember { mutableStateOf(false) }
    var codeOpen by remember { mutableStateOf(startWithCode) }
    var code by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf<String?>(if (enableNow) "on" else null) }   // "remove" | "off" | "on"
    val promptWrap = stringResource(R.string.keyguard_prompt_wrap)
    val promptTitle = stringResource(R.string.keyguard_prompt_title)
    val promptSub = stringResource(R.string.keyguard_prompt_subtitle)
    val ctx = LocalContext.current

    suspend fun load() {
        try {
            val api = sessions.requireApi()
            view = KeyguardCalls.view(api)
            me = runCatching { api.get<DeviceList>("/api/devices").current }.getOrNull()
            sessions.setLocked(view?.locked == true)
        } catch (e: ApiError) {
            if (e.status == 403) notOwner = true else error = e.detail
        }
    }
    LaunchedEffect(Unit) { load() }

    fun fail(e: ApiError) {
        error = when {
            e.code == KeyguardRules.CODE_ENV_KEY -> ctx.getString(R.string.keyguard_env_key)
            e.code in KeyguardRules.LAST_PHONE_CODES -> ctx.getString(R.string.keyguard_last_phone)
            e.status == 403 -> ctx.getString(R.string.keyguard_not_owner)
            else -> e.detail
        }
    }

    /** Enrol this phone: a key from the server, wrapped here after the owner's check, the blob back; the recovery code once. */
    fun enroll() {
        val a = auth ?: return
        busy = true; error = null; note = null
        scope.launch {
            val answer = try { KeyguardCalls.enroll(sessions.requireApi()) } catch (e: ApiError) { busy = false; fail(e); return@launch }
            a.forWrap(promptWrap, promptSub) { r ->
                if (r !is KeyguardAuth.Ok) { busy = false; authProblem(r)?.let { error = ctx.getString(it) }; return@forWrap }
                val plain = answer.key.toByteArray(Charsets.US_ASCII)
                val blob = try { KeyguardBlob.encode(r.cipher.iv, r.cipher.doFinal(plain)) } catch (e: Exception) {
                    busy = false; error = ctx.getString(R.string.keyguard_failed); return@forWrap
                } finally { plain.fill(0) }
                scope.launch {
                    try {
                        view = KeyguardCalls.finish(sessions.requireApi(), answer.enrollmentId, blob)
                        recovery = answer.recoveryCode   // enabling only
                        wroteDown = false
                        if (recovery == null && view?.on == true) onEnabled()
                    } catch (e: ApiError) {
                        fail(e)
                    } finally {
                        busy = false
                    }
                }
            }
        }
    }

    // the recovery code, once: nothing else on the page until "I wrote it down"
    recovery?.let { rc ->
        SubPage(stringResource(R.string.keyguard_recovery_title), onBack = {}) {
            MyCard {
                Text(stringResource(R.string.keyguard_recovery_warning), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                SelectionContainer {
                    Text(KeyguardRules.codeLines(rc), fontFamily = FontFamily.Monospace, fontSize = 20.sp, fontWeight = FontWeight.Bold,
                        color = c.ink, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
                }
                // the whole line ticks the box, not just the box itself
                Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp))
                    .toggleable(value = wroteDown, role = androidx.compose.ui.semantics.Role.Checkbox, onValueChange = { wroteDown = it }),
                    verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(wroteDown, null, colors = CheckboxDefaults.colors(checkedColor = c.accent))
                    Text(stringResource(R.string.keyguard_recovery_confirm), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                }
                PrimaryButton(stringResource(R.string.keyguard_done), onClick = { recovery = null; onEnabled() }, enabled = wroteDown, modifier = Modifier.fillMaxWidth())
            }
        }
        return
    }

    SubPage(stringResource(R.string.keyguard_title), onBack) {
        val v = view
        MyCard {
            MutedText(stringResource(R.string.keyguard_intro), small = false)
            MutedText(stringResource(R.string.keyguard_restart_note), small = true)
            if (!KeyguardKey.perUse) MutedText(stringResource(R.string.keyguard_window_note), small = true)
        }
        when {
            notOwner -> MyCard { MutedText(stringResource(R.string.keyguard_not_owner), small = true) }
            v == null -> {}
            !v.on -> MyCard {
                PrimaryButton(stringResource(if (busy) R.string.keyguard_turning_on else R.string.keyguard_turn_on), onClick = { confirm = "on" },
                    enabled = !busy && auth != null, modifier = Modifier.fillMaxWidth())
            }
            else -> {
                if (v.locked) MyCard {
                    Text(stringResource(R.string.keyguard_locked_title), fontWeight = FontWeight.Bold, color = c.ink)
                    if (v.holds(me) && auth != null) {
                        PrimaryButton(stringResource(if (busy) R.string.keyguard_unlocking else R.string.keyguard_unlock), onClick = {
                            busy = true; error = null
                            KeyguardUnlocker(auth, sessions).unlock(scope, promptTitle, promptSub) { p ->
                                busy = false
                                if (p == null) scope.launch { load(); note = R.string.keyguard_unlocked } else if (p != KeyguardUnlocker.CANCELLED) error = ctx.getString(p)
                            }
                        }, enabled = !busy, modifier = Modifier.fillMaxWidth())
                    } else MutedText(stringResource(R.string.keyguard_locked_other), small = true)
                }
                MyCard {
                    Text(stringResource(R.string.keyguard_phones_title), fontWeight = FontWeight.Bold, color = c.ink)
                    v.phones.forEach { p ->
                        Text(if (p.deviceId == me) stringResource(R.string.keyguard_this_phone, p.name) else p.name, color = c.ink,
                            style = MaterialTheme.typography.bodyMedium)
                    }
                    if (!v.locked && !v.holds(me)) SecondaryButton(stringResource(R.string.keyguard_add_this_phone), onClick = ::enroll,
                        enabled = !busy && auth != null, modifier = Modifier.fillMaxWidth())
                    if (v.holds(me)) SecondaryButton(stringResource(R.string.keyguard_remove_this_phone), onClick = { confirm = "remove" },
                        enabled = !busy, modifier = Modifier.fillMaxWidth())
                    if (!v.locked) SecondaryButton(stringResource(R.string.keyguard_turn_off), onClick = { confirm = "off" }, enabled = !busy,
                        modifier = Modifier.fillMaxWidth())
                }
            }
        }
        if (v?.on == true || codeOpen) MyCard {
            if (!codeOpen) {
                SecondaryButton(stringResource(R.string.keyguard_use_code), onClick = { codeOpen = true }, modifier = Modifier.fillMaxWidth())
            } else {
                Text(stringResource(R.string.keyguard_use_code), fontWeight = FontWeight.Bold, color = c.ink)
                KeloTextField(code, { code = it }, placeholder = stringResource(R.string.keyguard_code_hint), modifier = Modifier.fillMaxWidth(),
                    // Password type opens a Latin keyboard even when the phone types Korean/Japanese; the text stays visible.
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters, autoCorrectEnabled = false, keyboardType = KeyboardType.Password))
                PrimaryButton(stringResource(R.string.keyguard_unlock), onClick = {
                    busy = true; error = null
                    scope.launch {
                        try {
                            KeyguardCalls.recover(sessions.requireApi(), code)
                            code = ""; codeOpen = false; sessions.setLocked(false); note = R.string.keyguard_unlocked
                            load()
                        } catch (e: ApiError) {
                            if (e.status == 422 || e.status == 400) error = ctx.getString(R.string.keyguard_code_wrong) else fail(e)
                        } finally {
                            busy = false
                        }
                    }
                }, enabled = !busy && KeyguardRules.codeReady(code), modifier = Modifier.fillMaxWidth())
            }
        }
        note?.let { MutedText(stringResource(it), small = true) }
        error?.let { ErrorText(it, small = true) }
    }

    when (confirm) {
        "on" -> ConfirmDialog(stringResource(R.string.keyguard_turn_on_confirm), stringResource(R.string.keyguard_turn_on),
            onYes = { confirm = null; enroll() }, onDismiss = { confirm = null }, danger = false)
        "remove" -> ConfirmDialog(stringResource(R.string.keyguard_remove_confirm), stringResource(R.string.keyguard_remove_this_phone), onYes = {
            confirm = null
            val id = me ?: return@ConfirmDialog
            scope.launch {
                try { view = KeyguardCalls.removePhone(sessions.requireApi(), id); auth?.key?.forget() } catch (e: ApiError) { fail(e) }
            }
        }, onDismiss = { confirm = null })
        "off" -> ConfirmDialog(stringResource(R.string.keyguard_turn_off_confirm), stringResource(R.string.keyguard_turn_off), onYes = {
            confirm = null
            scope.launch {
                try { view = KeyguardCalls.disable(sessions.requireApi()); auth?.key?.forget() } catch (e: ApiError) { fail(e) }
            }
        }, onDismiss = { confirm = null })
    }
}

/** Whether the signed-in server has Keyguard on (GET /api/keyguard), null while unknown; [refresh] asks again. */
class KeyguardState(val on: Boolean?, val refresh: () -> Unit)

@Composable
fun rememberKeyguardOn(): KeyguardState {
    val sessions = LocalAppContainer.current.sessions
    var on by remember { mutableStateOf<Boolean?>(null) }
    var tick by remember { mutableStateOf(0) }
    LaunchedEffect(tick) { on = runCatching { KeyguardCalls.view(sessions.requireApi()).on }.getOrNull() }
    return remember(on) { KeyguardState(on) { tick++ } }
}

/**
 * Where the Keyguard enable flow opens over the whole app: a layer inside the activity window (MainShell draws it above every
 * page), not a Dialog window, so the status bar keeps the app's own look. The page that asked keeps its state underneath.
 */
class KeyguardEnableHost {
    class Request(val onEnabled: () -> Unit, val onClose: () -> Unit)
    var request by mutableStateOf<Request?>(null)
}

val LocalKeyguardEnableHost = androidx.compose.runtime.staticCompositionLocalOf<KeyguardEnableHost?> { null }

/** The enable flow over the app while [host] has a request; back or ← closes it, finishing runs the request's onEnabled. */
@Composable
fun KeyguardEnableOverlay(host: KeyguardEnableHost) {
    val r = host.request ?: return
    val c = Kelo.colors
    androidx.activity.compose.BackHandler { host.request = null; r.onClose() }
    androidx.compose.foundation.layout.Box(
        Modifier.fillMaxSize().background(c.bg)
            // touches stop here: nothing under the page reacts
            .clickable(interactionSource = remember { androidx.compose.foundation.interaction.MutableInteractionSource() }, indication = null) {},
    ) {
        KeyguardPage(onBack = { host.request = null; r.onClose() }, enableNow = true, onEnabled = { host.request = null; r.onEnabled() })
    }
}

/**
 * "Auto-pay needs Keyguard" (owner's decision): Turn on Keyguard opens its enable flow over the app and, once it is on, [onEnabled]
 * goes on with what was asked (turning auto-pay on); Cancel leaves everything as it was.
 */
@Composable
fun KeyguardNeededDialog(onEnabled: () -> Unit, onDismiss: () -> Unit) {
    val c = Kelo.colors
    val host = LocalKeyguardEnableHost.current
    var enabling by remember { mutableStateOf(false) }
    if (enabling) {
        if (host != null) {
            LaunchedEffect(host) {
                host.request = KeyguardEnableHost.Request(onEnabled = { enabling = false; onEnabled() }, onClose = { enabling = false; onDismiss() })
            }
            return
        }
        // no host (outside the main shell): a full-screen dialog window
        androidx.compose.ui.window.Dialog(onDismissRequest = { enabling = false; onDismiss() },
            properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false)) {
            androidx.compose.foundation.layout.Box(Modifier.fillMaxSize().background(c.bg)) {
                KeyguardPage(onBack = { enabling = false; onDismiss() }, enableNow = true, onEnabled = { enabling = false; onEnabled() })
            }
        }
        return
    }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss, containerColor = c.card,
        title = { Text(stringResource(R.string.wallet_keyguard_needed_title), color = c.ink) },
        text = { Text(stringResource(R.string.wallet_keyguard_needed_body), color = c.ink, style = MaterialTheme.typography.bodyMedium) },
        confirmButton = { androidx.compose.material3.TextButton(onClick = { enabling = true }) { Text(stringResource(R.string.keyguard_turn_on_named), color = c.accent) } },
        dismissButton = { androidx.compose.material3.TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}
