package chat.kelo.app.my

import androidx.compose.foundation.background
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Switch
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.ui.PushHost
import chat.kelo.app.ui.SecureWindow
import chat.kelo.app.ui.components.SkeletonLines
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.rememberSkeletonShown
import androidx.compose.foundation.layout.fillMaxSize
import chat.kelo.app.chat.SelectField
import chat.kelo.app.chat.SelectOption
import chat.kelo.app.chat.vault.SecureEntryForm
import chat.kelo.app.chat.vault.VaultCalls
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/** Where an API key goes in the request. */
private val PLACES = listOf(
    "bearer_header" to R.string.vault_place_bearer, "custom_header" to R.string.vault_place_header,
    "query_param" to R.string.vault_place_query, "url_path_segment" to R.string.vault_place_path,
)

/** The log's actions in words. */
private val ACTIONS = mapOf(
    "used" to R.string.vault_action_used, "denied" to R.string.vault_action_denied, "requested" to R.string.vault_action_requested,
    "added" to R.string.vault_action_added, "saved" to R.string.vault_action_saved, "replaced" to R.string.vault_action_replaced,
    "entered-once" to R.string.vault_action_entered_once, "deleted" to R.string.vault_action_deleted, "setting" to R.string.vault_action_setting,
    "locked" to R.string.vault_action_locked, "unlocked" to R.string.vault_action_unlocked, "result" to R.string.vault_action_result,
    "failed" to R.string.vault_action_failed, "rejected" to R.string.vault_action_rejected, "cancelled" to R.string.vault_action_cancelled,
    "code-typed" to R.string.vault_action_code_typed,
)

/** What the last real use showed. */
private val RESULTS = mapOf(
    "ok" to R.string.vault_result_ok, "failed" to R.string.vault_result_failed, "rejected" to R.string.vault_result_rejected,
    "code_needed" to R.string.vault_result_code_needed, "blocked" to R.string.vault_result_blocked, "unknown" to R.string.vault_result_unknown,
)

/** The vault's own sub pages. */
private sealed interface VaultView {
    data object List : VaultView
    data class Item(val name: String) : VaultView
    data class Ai(val name: String) : VaultView
    data object AddLogin : VaultView
    data object Log : VaultView
}

private fun VaultView.key(): String = when (this) {
    VaultView.List -> "list"
    is VaultView.Item -> "item:$name"
    is VaultView.Ai -> "ai:$name"
    VaultView.AddLogin -> "addLogin"
    VaultView.Log -> "log"
}

private fun viewOf(key: String): VaultView = when {
    key.startsWith("item:") -> VaultView.Item(key.removePrefix("item:"))
    key.startsWith("ai:") -> VaultView.Ai(key.removePrefix("ai:"))
    key == "addLogin" -> VaultView.AddLogin
    key == "log" -> VaultView.Log
    else -> VaultView.List
}

/**
 * Every password and key the assistant may use, what each is for, and the lock. Values can be added or replaced
 * here, never read. The window is kept out of screenshots and the recent-apps preview while it is open. [noLock]: the phone has
 * no screen lock, so it was opened without a check; the page says so.
 */
@Composable
fun VaultPage(onBack: () -> Unit, onGoModel: () -> Unit, noLock: Boolean = false) {
    SecureWindow()
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var info by remember { mutableStateOf<VaultState?>(null) }
    var providers by remember { mutableStateOf<List<ProviderDetail>>(emptyList()) }
    var viewKey by rememberSaveable { mutableStateOf(VaultView.List.key()) }
    fun go(v: VaultView) { viewKey = v.key() }
    val list = { go(VaultView.List) }

    suspend fun loadNow() { runCatching { sessions.requireApi().get<VaultState>("/api/vault") }.onSuccess { info = it } }
    suspend fun loadProvidersNow() { runCatching { sessions.requireApi().get<List<ProviderDetail>>("/api/providers") }.onSuccess { providers = it } }
    val load: () -> Unit = { scope.launch { loadNow() } }
    val loadProviders: () -> Unit = { scope.launch { loadProvidersNow() } }
    LaunchedEffect(Unit) { loadNow(); loadProvidersNow() }

    val v = info
    if (rememberSkeletonShown(v == null) || v == null) {
        SubPage(stringResource(R.string.vault_title), onBack) { MyCard { SkeletonLines(2) }; SkeletonRows(3) }
        return
    }
    // leaving a login or key that was started but never filled in: nothing stays behind in the vault
    fun leaveItem(name: String) {
        val item = (v.items + v.pending).find { it.name == name }
        if (item != null && !item.hasSecret) scope.launch { runCatching { VaultCalls.cancelDraft(sessions.requireApi(), item.name) } }
        list(); load()
    }
    val stepBack: () -> Unit = {
        when (val now = viewOf(viewKey)) {
            is VaultView.Item -> leaveItem(now.name)
            else -> list()
        }
    }
    // the vault's own pages slide over its list; the back gesture steps back one page at a time
    PushHost(
        state = viewKey,
        depth = { if (viewOf(it) == VaultView.List) 0 else 1 },
        back = if (viewOf(viewKey) == VaultView.List) null else VaultView.List.key(),
        onBack = stepBack,
        modifier = Modifier.fillMaxSize(),
    ) { key ->
        when (val view = viewOf(key)) {
            VaultView.Log -> LogPage(onBack = list)
            VaultView.AddLogin -> AddLogin(onBack = list, onDone = { name -> load(); go(VaultView.Item(name)) })
            is VaultView.Ai -> providers.find { it.name == view.name }?.let { p ->
                AiKeyPage(p, onBack = list, onGoModel = onGoModel, onChanged = loadProviders)
            } ?: VaultList(v, providers, noLock, onBack, ::go, load)
            is VaultView.Item -> (v.items + v.pending).find { it.name == view.name }?.let { item ->
                ItemPage(item, locked = v.locked, verifying = item.name in v.verifying, onChanged = load, onBack = { leaveItem(item.name) })
            } ?: VaultList(v, providers, noLock, onBack, ::go, load)
            VaultView.List -> VaultList(v, providers, noLock, onBack, ::go, load)
        }
    }
}

@Composable
private fun VaultList(v: VaultState, providers: List<ProviderDetail>, noLock: Boolean, onBack: () -> Unit, go: (VaultView) -> Unit, load: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    suspend fun loadNow() = load()
    fun toggleLock() {
        scope.launch {
            runCatching { sessions.requireApi().put<JsonElement>("/api/vault/lock", buildJsonObject { put("locked", !v.locked) }) }
            loadNow()
        }
    }
    fun setMailCodes(on: Boolean) {
        scope.launch {
            runCatching { sessions.requireApi().put<JsonElement>("/api/vault/settings", buildJsonObject { put("mail_codes_auto", on) }) }
            loadNow()
        }
    }

    val logins = v.items.filter { it.kind == VaultEntry.KIND_LOGIN }
    val mailboxes = v.items.filter { it.kind == VaultEntry.KIND_EMAIL }
    val keys = v.items.filter { it.kind == VaultEntry.KIND_API }
    val ai = providers.filter { it.keySource == "app" }
    val c = Kelo.colors
    SubPage(stringResource(R.string.vault_title), onBack) {
        if (noLock) MutedText(stringResource(R.string.vault_gate_no_lock), small = true, modifier = Modifier.padding(horizontal = 4.dp))
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                IconTitle(KeloIcons.Shield, stringResource(if (v.locked) R.string.vault_locked_title else R.string.vault_safe_title), Modifier.weight(1f))
                if (v.locked) PrimaryButton(stringResource(R.string.vault_unlock), onClick = ::toggleLock, modifier = Modifier.weight(0.6f))
                else SecondaryButton(stringResource(R.string.vault_lock), onClick = ::toggleLock)
            }
            MutedText(stringResource(if (v.locked) R.string.vault_locked_body else R.string.vault_safe_body), small = true)
        }

        WaitingEntries(v, load)

        SectionLabel(stringResource(R.string.vault_logins))
        MenuGroup { logins.forEach { i -> row { ItemRow(i, KeloIcons.Globe) { go(VaultView.Item(i.name)) } } } }
        MenuGroup { row { MenuRow(KeloIcons.Plus, stringResource(R.string.vault_add_login), onClick = { go(VaultView.AddLogin) }) } }
        MailCodesSwitch(v.mailCodesAuto, ::setMailCodes)
        PaymentPinSection(v.locked)

        if (mailboxes.isNotEmpty()) {
            SectionLabel(stringResource(R.string.vault_mailboxes))
            MenuGroup { mailboxes.forEach { i -> row { ItemRow(i, KeloIcons.Key, i.label.split(" · ").first()) { go(VaultView.Item(i.name)) } } } }
        }

        if (keys.isNotEmpty()) {
            SectionLabel(stringResource(R.string.vault_keys))
            MutedText(stringResource(R.string.vault_keys_help), small = true, modifier = Modifier.padding(horizontal = 4.dp))
            MenuGroup { keys.forEach { i -> row { ItemRow(i, KeloIcons.Key) { go(VaultView.Item(i.name)) } } } }
        }

        if (ai.isNotEmpty()) {
            SectionLabel(stringResource(R.string.vault_ai_keys))
            MenuGroup {
                ai.forEach { p ->
                    row {
                        MenuRow(
                            KeloIcons.Key, p.displayName, onClick = { go(VaultView.Ai(p.name)) },
                            value = p.keyHint?.let { stringResource(R.string.vault_key_hint, it) } ?: stringResource(R.string.vault_stored),
                        )
                    }
                }
            }
        }

        SectionLabel(stringResource(R.string.vault_records))
        MenuGroup { row { MenuRow(KeloIcons.Shield, stringResource(R.string.vault_log), onClick = { go(VaultView.Log) }) } }
    }
}

/**
 * "입력 대기": the keys, logins and cards Kelo asked for that have no value yet (GET /api/secret-requests), newest first, at the top
 * of the Keychain. Each opens the same secure entry as in the chat, right here (saving it lets the agent go on); 삭제 cancels it.
 */
@Composable
private fun WaitingEntries(v: VaultState, load: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var list by remember { mutableStateOf<List<chat.kelo.app.chat.vault.SecretRequest>>(emptyList()) }
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(v) { runCatching { chat.kelo.app.chat.vault.SecretCalls.list(sessions.requireApi()) }.onSuccess { list = it } }
    val waiting = chat.kelo.app.chat.vault.SecretRules.waiting(list)
    if (waiting.isEmpty()) return
    fun drop(r: chat.kelo.app.chat.vault.SecretRequest) {
        list = list.filterNot { it.id == r.id }
        scope.launch { runCatching { chat.kelo.app.chat.vault.SecretCalls.cancel(sessions.requireApi(), r.id) }; load() }
    }
    val c = Kelo.colors
    SectionLabel(stringResource(R.string.secret_waiting_title))
    MutedText(stringResource(R.string.secret_waiting_help), small = true, modifier = Modifier.padding(horizontal = 4.dp))
    waiting.forEach { r ->
        MyCard {
            if (open == r.id) {
                chat.kelo.app.chat.vault.SecretEntryCard(
                    r, framed = false,
                    onSaved = { _, _ -> open = null; list = list.filterNot { it.id == r.id }; load() },
                    onCancel = { open = null; drop(r) },
                    onGone = { open = null; list = list.filterNot { it.id == r.id }; load() },
                )
            } else {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    androidx.compose.material3.Icon(if (r.isCard) KeloIcons.Card else if (r.isLogin) KeloIcons.Globe else KeloIcons.Key,
                        contentDescription = null, tint = c.ink, modifier = Modifier.padding(top = 2.dp))
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(r.label.ifEmpty { r.name }, style = MaterialTheme.typography.bodyLarge, color = c.ink, maxLines = 2,
                            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                        val kind = stringResource(if (r.isCard) R.string.secret_kind_card else if (r.isLogin) R.string.secret_kind_login else R.string.secret_kind_key)
                        val hosts = chat.kelo.app.chat.vault.SecretRules.hostsText(r.hosts)
                        Text(if (hosts.isEmpty()) kind else "$kind · $hosts", style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2,
                            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    PrimaryButton(stringResource(R.string.secret_fill), onClick = { open = r.id }, modifier = Modifier.weight(1f))
                    SecondaryButton(stringResource(R.string.delete), onClick = { drop(r) })
                }
            }
        }
    }
}

/** "Use codes from my mail automatically": on, a code the site mails during a sign-in Kelo started is typed without asking. */
@Composable
private fun MailCodesSwitch(on: Boolean, onChange: (Boolean) -> Unit) {
    val c = Kelo.colors
    val title = stringResource(R.string.vault_mail_codes_title)
    MyCard {
        Row(
            Modifier.fillMaxWidth().toggleable(value = on, role = Role.Switch, onValueChange = onChange),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(title, style = MaterialTheme.typography.titleSmall, color = c.ink, modifier = Modifier.weight(1f).padding(end = 12.dp))
            Switch(
                checked = on, onCheckedChange = null, modifier = Modifier.semantics { contentDescription = title },
                colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
            )
        }
        MutedText(stringResource(if (on) R.string.vault_mail_codes_on else R.string.vault_mail_codes_off), small = true)
    }
}

/** A vault row; every item listed holds a value (requests without one are not shown). */
@Composable
private fun ItemRow(i: VaultEntry, icon: androidx.compose.ui.graphics.vector.ImageVector, label: String = i.label, onClick: () -> Unit) {
    val c = Kelo.colors
    val stored = i.usernameHint ?: stringResource(R.string.vault_stored)
    when {
        i.isBad -> MenuRow(
            icon, label, onClick,
            value = stringResource(if (i.isLogin) R.string.vault_status_login_failed else R.string.vault_status_key_rejected),
            valueColor = c.danger,
        )
        i.lastResult == "ok" -> MenuRow(icon, label, onClick, value = stringResource(R.string.vault_status_ok, stored))
        else -> MenuRow(icon, label, onClick, value = stored)
    }
}

/** One login or key: where it can be used, how it last went, how a login is allowed, and replacing or removing it. */
@Composable
private fun ItemPage(item: VaultEntry, locked: Boolean, verifying: Boolean, onChanged: () -> Unit, onBack: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var msg by remember { mutableStateOf("") }
    val bad = item.isBad
    val signedIn = item.signedIn
    var open by rememberSaveable(item.name) { mutableStateOf(!item.hasSecret || bad) }
    var confirm by remember { mutableStateOf(false) }
    val c = Kelo.colors
    val path = "/api/vault/${KeloApi.segment(item.name)}"

    fun patch(build: kotlinx.serialization.json.JsonObjectBuilder.() -> Unit) {
        scope.launch {
            try {
                sessions.requireApi().patch<JsonElement>(path, buildJsonObject(build))
                onChanged()
            } catch (e: ApiError) {
                msg = e.detail
            }
        }
    }

    SubPage(item.label, onBack) {
        MyCard {
            val kind = stringResource(
                when {
                    item.isLogin -> R.string.vault_kind_login
                    item.kind == VaultEntry.KIND_EMAIL -> R.string.vault_kind_email
                    signedIn -> R.string.vault_kind_signed_in
                    else -> R.string.vault_kind_key
                },
            )
            Text(
                chat.kelo.app.ui.components.emphasized(stringResource(R.string.vault_kind_hosts, kind), item.hosts.joinToString(", "), code = true),
                color = c.ink,
            )
            val parts = mutableListOf<String>()
            parts += if (item.hasSecret) {
                val s = stringResource(if (bad) R.string.vault_saved_plain else R.string.vault_saved_ok)
                if (item.usernameHint != null) stringResource(R.string.vault_join, s, item.usernameHint) else s
            } else stringResource(R.string.vault_not_entered)
            parts += stringResource(R.string.vault_last_used, whenText(item.lastUsed))
            if (item.kind == VaultEntry.KIND_API && !signedIn) {
                val p = item.placement
                val place = when (p.type) {
                    "custom_header" -> stringResource(R.string.vault_place_header_named, p.header.orEmpty())
                    "query_param" -> stringResource(R.string.vault_place_query_named, p.param.orEmpty())
                    else -> PLACES.firstOrNull { it.first == p.type }?.let { stringResource(it.second) }.orEmpty()
                }
                parts += stringResource(R.string.vault_place, place)
            }
            if (item.endpoint.isNotEmpty()) parts += stringResource(R.string.vault_mcp, item.endpoint)
            MutedText(parts.joinToString(stringResource(R.string.usage_group_joiner)), small = true)
            if (locked) ErrorText(stringResource(R.string.vault_item_locked), small = true)
            if (verifying) Text(stringResource(R.string.vault_verifying), style = MaterialTheme.typography.bodySmall, color = c.ink)
            val result = item.lastResult
            if (result != null) {
                val words = RESULTS[result]?.let { stringResource(it) } ?: result
                val line = buildList {
                    add(words); add(whenText(item.lastResultAt))
                    if (item.kind == VaultEntry.KIND_API && item.lastResultDetail.isNotEmpty()) add(item.lastResultDetail)
                }.joinToString(stringResource(R.string.usage_group_joiner))
                if (bad) ErrorText(line, small = true) else MutedText(line, small = true)
            } else if (item.hasSecret) {
                MutedText(stringResource(R.string.vault_not_used_yet), small = true)
            }
            if (bad && !signedIn) {
                ErrorText(stringResource(if (item.isLogin) R.string.vault_bad_login else R.string.vault_bad_key), small = true)
                LinkButton(stringResource(R.string.vault_try_again), onClick = { patch { put("clear_result", true) } })
            }
        }
        if (item.isLogin) {
            MyCard {
                Text(stringResource(R.string.vault_when_login), style = MaterialTheme.typography.titleSmall, color = c.ink)
                Segmented(
                    listOf(stringResource(R.string.vault_ask_every_time), stringResource(R.string.vault_without_asking)),
                    selected = if (item.autoAllow) 1 else 0,
                    onSelect = { i -> patch { put("auto_allow", i == 1) } },
                )
                MutedText(stringResource(if (item.autoAllow) R.string.vault_auto_allow_on else R.string.vault_auto_allow_off), small = true)
            }
            if (item.scheduleGrants.isNotEmpty()) {
                MyCard {
                    Text(stringResource(R.string.grant_login_section), style = MaterialTheme.typography.titleSmall, color = c.ink)
                    chat.kelo.app.agent.ScheduleGrantRows(item.scheduleGrants, showSchedule = true) { g ->
                        scope.launch {
                            try { chat.kelo.app.agent.ScheduleGrantCalls.revoke(sessions.requireApi(), g); onChanged() } catch (e: ApiError) { msg = e.detail }
                        }
                    }
                    MutedText(stringResource(R.string.grant_section_note), small = true)
                }
            }
        }
        MyCard {
            when {
                signedIn -> MutedText(
                    (if (bad) stringResource(R.string.vault_disconnected) else "") + stringResource(R.string.vault_reconnect_in_connectors),
                    small = true,
                )
                open -> SecureEntryForm(item.asItem(), onSaved = { open = false; onChanged() })
                else -> SecondaryButton(
                    stringResource(if (item.isLogin) R.string.vault_change_login else R.string.vault_change_key),
                    onClick = { open = true }, modifier = Modifier.fillMaxWidth(),
                )
            }
        }
        MyCard { LinkButton(stringResource(R.string.vault_remove), onClick = { confirm = true }, danger = true) }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
    }
    if (confirm) {
        ConfirmDialog(
            text = stringResource(R.string.vault_remove_confirm, item.label), yes = stringResource(R.string.delete),
            onDismiss = { confirm = false },
            onYes = {
                scope.launch {
                    try {
                        sessions.requireApi().delete<JsonElement>("/api/credentials/${KeloApi.segment(item.name)}")
                        onChanged(); onBack()
                    } catch (e: ApiError) {
                        msg = e.detail
                    }
                }
            },
        )
    }
}

/** .segmented: two or more choices side by side. */
@Composable
private fun Segmented(options: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(c.bubble).padding(3.dp)) {
        options.forEachIndexed { i, o ->
            val on = i == selected
            Box(
                Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(11.dp))
                    .background(if (on) c.card else c.bubble)
                    .clickable(role = Role.RadioButton) { onSelect(i) }
                    .padding(vertical = 10.dp),
                contentAlignment = Alignment.Center,
            ) { Text(o, color = c.ink, fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal) }
        }
    }
}

/** The sign-in page's address and a name; the next page takes the username and password. */
@Composable
private fun AddLogin(onDone: (String) -> Unit, onBack: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var site by rememberSaveable { mutableStateOf("") }
    var label by rememberSaveable { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    fun add() {
        err = ""
        scope.launch {
            try {
                val r = sessions.requireApi().post<NewLogin>("/api/vault/logins", buildJsonObject { put("site", site.trim()); put("label", label.trim()) })
                onDone(r.name)
            } catch (e: ApiError) {
                err = e.detail
            }
        }
    }
    SubPage(stringResource(R.string.vault_add_login), onBack) {
        MyCard {
            FieldLabel(stringResource(R.string.vault_login_site))
            KeloTextField(
                site, { site = it }, stringResource(R.string.vault_login_site_placeholder),
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Uri),
            )
            FieldLabel(stringResource(R.string.vault_login_name))
            KeloTextField(label, { label = it }, stringResource(R.string.vault_login_name_placeholder), maxLength = 80)
            MutedText(stringResource(R.string.vault_login_site_help), small = true)
            if (err.isNotEmpty()) ErrorText(err, small = true)
            PrimaryButton(stringResource(R.string.onboarding_next), onClick = ::add, enabled = site.isNotBlank())
        }
    }
}

/** The key of an AI service. Changing or removing it happens here; choosing which model answers is on the model page. */
@Composable
private fun AiKeyPage(p: ProviderDetail, onBack: () -> Unit, onGoModel: () -> Unit, onChanged: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var key by remember { mutableStateOf("") }
    var msg by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf(false) }
    val savedText = stringResource(R.string.operator_saved)
    val path = "/api/providers/${KeloApi.segment(p.name)}/key"
    fun save() {
        msg = ""
        scope.launch {
            try {
                sessions.requireApi().put<JsonElement>(path, buildJsonObject { put("api_key", key.trim()) })
                key = ""; msg = savedText; onChanged()
            } catch (e: ApiError) {
                msg = e.detail
            }
        }
    }
    SubPage(p.displayName, onBack) {
        MyCard {
            Text(stringResource(R.string.vault_ai_key_title), color = Kelo.colors.ink)
            MutedText(
                if (p.keyHint != null) stringResource(R.string.vault_ai_key_saved_hint, p.keyHint) else stringResource(R.string.vault_ai_key_saved),
                small = true,
            )
            MutedText(stringResource(R.string.vault_ai_key_note), small = true)
        }
        MyCard {
            FieldLabel(stringResource(R.string.model_key_replace))
            KeloTextField(
                key, { key = it }, stringResource(R.string.model_key_paste), password = true, maxLength = 512,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Password),
            )
            PrimaryButton(stringResource(R.string.save), onClick = ::save, enabled = key.trim().length >= 8)
            if (msg.isNotEmpty()) MutedText(msg, small = true)
        }
        MyCard { LinkButton(stringResource(R.string.model_key_delete), onClick = { confirm = true }, danger = true) }
        Row(Modifier.padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            MutedText(stringResource(R.string.vault_ai_model_choice), small = true, modifier = Modifier.weight(1f))
            LinkButton(stringResource(R.string.chat_model_settings), onClick = onGoModel)
        }
    }
    if (confirm) {
        ConfirmDialog(
            text = stringResource(R.string.vault_ai_key_delete_confirm, p.displayName), yes = stringResource(R.string.delete),
            onDismiss = { confirm = false },
            onYes = {
                scope.launch {
                    runCatching { sessions.requireApi().delete<JsonElement>(path) }
                    onChanged(); onBack()
                }
            },
        )
    }
}

/** What happened in the vault. The agent can neither read nor erase it. */
@Composable
private fun LogPage(onBack: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    var rows by remember { mutableStateOf<List<VaultLogRow>?>(null) }
    LaunchedEffect(Unit) {
        rows = try { sessions.requireApi().get<List<VaultLogRow>>("/api/vault/log?limit=200") } catch (_: ApiError) { emptyList() }
    }
    val c = Kelo.colors
    SubPage(stringResource(R.string.vault_log), onBack) {
        MutedText(stringResource(R.string.vault_log_help), small = true)
        val list = rows
        if (list != null && list.isEmpty()) EmptyNote(stringResource(R.string.vault_log_empty))
        if (!list.isNullOrEmpty()) {
            MenuGroup {
                list.forEach { r ->
                    row {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Badge(ACTIONS[r.action]?.let { stringResource(it) } ?: r.action, denied = !r.ok)
                            Column(Modifier.weight(1f)) {
                                Text(r.item, fontWeight = FontWeight.Bold, color = c.ink, maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                                if (r.detail.isNotEmpty()) MutedText(r.detail, small = true)
                            }
                            MutedText(whenText(r.ts), small = true)
                        }
                    }
                }
            }
        }
    }
}
