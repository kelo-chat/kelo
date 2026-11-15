package chat.kelo.desktop.chat

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.input.pointer.pointerHoverIcon
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.chat.ApprovalLine
import chat.kelo.app.chat.CardState
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.ModelNames
import chat.kelo.app.chat.ProviderConsentRules
import chat.kelo.app.chat.QuickReplyRules
import chat.kelo.app.chat.ReplyAnswer
import chat.kelo.app.chat.ReplyFields
import chat.kelo.app.chat.ReplySet
import chat.kelo.app.chat.ToolState
import chat.kelo.app.chat.vault.VaultCalls
import chat.kelo.app.chat.vault.VaultItem
import chat.kelo.app.chat.vault.VaultMode
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.launch

/** A full-width card in the chat (approvals, the secure form, a connect request). */
@Composable
fun CardBox(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val c = Kelo.colors
    Column(
        modifier.fillMaxWidth().widthIn(max = 640.dp).clip(RoundedCornerShape(16.dp)).background(c.card)
            .border(1.dp, c.line, RoundedCornerShape(16.dp)).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        content = content,
    )
}

@Composable
fun NoticeChip(text: String) {
    val c = Kelo.colors
    Text(
        text, fontSize = 12.sp, lineHeight = 16.sp, color = c.muted,
        modifier = Modifier.border(1.dp, c.line, RoundedCornerShape(99.dp)).padding(horizontal = 10.dp, vertical = 3.dp),
    )
}

/**
 * A finished tool as a small chip ("✓ 사이트 열기"); a running one is told by the status line instead. Several calls of one kind in a
 * row come folded ([ChatReducer.foldTools]) and show as one chip that updates in place ([FoldedToolChip]).
 */
@Composable
fun ToolChip(t: ChatItem.Tool) {
    if (t.folded) {
        FoldedToolChip(t)
        return
    }
    if (t.state == ToolState.RUNNING) return
    val res = when (t.state) {
        ToolState.RUNNING -> R.string.chat_tool_running
        ToolState.OK -> R.string.chat_tool_ok
        ToolState.ERROR -> R.string.chat_tool_error
        ToolState.DENIED -> R.string.chat_tool_denied
    }
    NoticeChip(stringResource(res, t.shown))
}

/**
 * Several calls of one kind in a row as ONE chip that updates in place (android/kelo .../chat/ChatScreen.kt FoldedToolChip): "메일 읽는
 * 중 · 3/6" while they run, "✓ 메일 6개 읽음" once done (failures counted after it). A click shows each call with what it was about.
 */
@Composable
fun FoldedToolChip(t: ChatItem.Tool) {
    val c = Kelo.colors
    var open by remember(t.key) { mutableStateOf(false) }
    val m = t.members
    val ok = ChatReducer.doneCount(t)
    val failed = m.count { it.state == ToolState.ERROR || it.state == ToolState.DENIED }
    val running = m.any { it.state == ToolState.RUNNING }
    val text = when {
        running -> stringResource(R.string.chat_tool_progress, t.shown, m.size - m.count { it.state == ToolState.RUNNING }, m.size)
        ok == 0 -> stringResource(if (m.all { it.state == ToolState.DENIED }) R.string.chat_tool_denied else R.string.chat_tool_error, t.shown) + " · ${m.size}"
        failed > 0 -> stringResource(R.string.chat_tool_ok_failed, ChatReducer.foldedText(t, ok), failed)
        else -> stringResource(R.string.chat_tool_ok, ChatReducer.foldedText(t, ok))
    }
    Column(Modifier.fillMaxWidth().animateContentSize(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth()) {
            Text(
                text + if (open) "  ▴" else "  ▾", fontSize = 12.sp, lineHeight = 16.sp, color = c.muted,
                modifier = Modifier.clip(RoundedCornerShape(99.dp)).border(1.dp, c.line, RoundedCornerShape(99.dp))
                    .clickable(role = androidx.compose.ui.semantics.Role.Button, onClick = { open = !open })
                    .pointerHoverIcon(androidx.compose.ui.input.pointer.PointerIcon.Hand)
                    .padding(horizontal = 10.dp, vertical = 3.dp),
            )
        }
        if (open) {
            Column(Modifier.padding(start = 10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                m.forEach { one ->
                    val mark = when (one.state) {
                        ToolState.RUNNING -> "⏳"
                        ToolState.OK -> "✓"
                        ToolState.ERROR -> "✗"
                        ToolState.DENIED -> "⛔"
                    }
                    Text("$mark ${one.detail.ifEmpty { one.shown }}", fontSize = 12.sp, lineHeight = 16.sp, color = c.muted)
                }
            }
        }
    }
}

/** The items a permission for many covers, behind "목록 보기 (37)": the user sees exactly what the one Allow is for. */
@Composable
fun ApprovalItems(items: List<String>) {
    val c = Kelo.colors
    var open by remember(items) { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().animateContentSize(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        LinkButton(stringResource(if (open) R.string.chat_approval_list_hide else R.string.chat_approval_list_show, items.size), onClick = { open = !open })
        if (open) {
            val scroll = androidx.compose.foundation.rememberScrollState()
            Box(Modifier.fillMaxWidth().heightIn(max = 280.dp).clip(RoundedCornerShape(10.dp)).background(c.bubble)) {
                Column(
                    Modifier.fillMaxWidth().verticalScroll(scroll).padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    items.forEachIndexed { i, line -> Text("${i + 1}. $line", style = MaterialTheme.typography.bodySmall, color = c.ink) }
                }
                androidx.compose.foundation.VerticalScrollbar(
                    androidx.compose.foundation.rememberScrollbarAdapter(scroll), Modifier.align(Alignment.CenterEnd).padding(vertical = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun OutcomeChip(a: ChatItem.Approval, site: String = "", verify: Boolean = false) {
    val c = Kelo.colors
    val check = Handover.isCheck(a)
    val (word, tone) = when {
        a.open -> return
        check && a.answer == true -> stringResource(if (verify) R.string.handover_verify_done else R.string.handover_solved) to c.success
        check && a.answer == false -> stringResource(R.string.handover_skipped, site.ifBlank { stringResource(R.string.handover_this_site) }) to c.muted
        a.answer == true -> stringResource(R.string.chat_outcome_allowed) to c.success
        a.answer == false -> stringResource(R.string.chat_outcome_denied) to c.danger
        else -> stringResource(R.string.chat_outcome_expired) to c.muted
    }
    Text(
        word, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold, color = tone,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(tone.copy(alpha = 0.12f)).padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

/**
 * A tool waiting for a yes or no (POST /api/approvals/{id}). Once answered or expired it stays as it was asked, without buttons,
 * with a chip saying how it ended. A payment shows what and how much; a card kept end-to-end on a phone can only be sent from
 * that phone, so here it can be refused but not allowed.
 */
@Composable
fun ApprovalCard(
    a: ChatItem.Approval, onAnswer: (Boolean) -> Unit, onKeepForSchedule: () -> Unit,
    site: String = "", onSolve: () -> Unit = {}, check: chat.kelo.app.chat.CheckInfo? = null,
) {
    val c = Kelo.colors
    val language = LocalStrings.current.code.substringBefore('-')
    if (Handover.isCheck(a)) {
        CardBox {
            HumanCheckBody(site, working = false, open = a.open, onSolve = onSolve, onSkip = { onAnswer(false) }, check = check) {
                OutcomeChip(a, site, verify = check?.isVerification == true)
            }
        }
        return
    }
    CardBox {
        val p = a.payment
        if (a.urgent && a.open) UrgentLine(a.missed)
        val grant = a.grant
        if (grant != null && p == null) {
            // the setup card of a "when X happens, do Y" task: its own layout (trigger, action, limits, the card's auto-pay limit)
            TaskGrantApproval(grant, a.open, outcome = { OutcomeChip(a) }) { ok -> onAnswer(ok) }
            return@CardBox
        }
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                if (p != null) stringResource(R.string.chat_purchase_question)
                else Handover.question(a.ask) ?: stringResource(R.string.chat_approval_ask, ProviderConsentRules.askWord(a.shown, language), ProviderConsentRules.objectParticle(a.shown)),
                color = c.ink, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f),
            )
            OutcomeChip(a)
        }
        if (p != null) {
            // the order at once: site, items, where it goes, what pays, coupon, total (chat/OrderParts.kt)
            OrderDetails(p, a.card)
            a.card?.let { card ->
                // why it asks (any card, also one saved to ask each time), as the server words it
                if (card.reasonText.isNotEmpty()) MutedText(stringResource(R.string.chat_purchase_not_auto, card.reasonText), small = true)
                if (card.needsCard && a.open) MutedText(stringResource(R.string.chat_card_error_not_on_phone), small = true)
            }
        } else {
            ApprovalFacts(a.lines, a.detail)
            if (a.items.isNotEmpty()) ApprovalItems(a.items)
        }
        // Allow also releases the site's saved payment PIN, which only the phone that saved it can do
        if (a.pin != null && a.open) MutedText(stringResource(R.string.desktop_pin_allow_note), small = true)
        if (a.open) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                SecondaryButton(stringResource(R.string.chat_approval_deny), onClick = { onAnswer(false) }, modifier = Modifier.weight(1f))
                if (!a.needsCard) PrimaryButton(stringResource(R.string.chat_approval_allow), onClick = { onAnswer(true) }, modifier = Modifier.weight(1f))
            }
            if (a.canRememberSchedule) LinkButton(stringResource(R.string.approval_keep_for_schedule), onClick = onKeepForSchedule)
        }
    }
}

/**
 * A site's check whether a person is there, handed to the user (the same in the chat and on the 작업 page): who asks, one short
 * sentence, "solve it" (the tab's live view with control taken; the card ends by itself once the check is gone) and "skip this
 * site". The buttons sit one under the other, full width, so long words in any language wrap instead of being cut. [trailing]: the
 * chip that says how it ended, beside the title.
 */
@Composable
fun HumanCheckBody(
    site: String, working: Boolean, onSolve: () -> Unit, onSkip: () -> Unit, open: Boolean = true,
    check: chat.kelo.app.chat.CheckInfo? = null,
    trailing: @Composable () -> Unit = {},
) {
    val c = Kelo.colors
    val where = site.ifBlank { stringResource(R.string.handover_this_site) }
    // a sign-in's second step (approve on the phone, or a code) is not a person check: its own words, the server's step first
    val verify = check?.isVerification == true
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(if (verify) KeloIcons.Shield else KeloIcons.User, contentDescription = null, tint = c.accent, modifier = Modifier.size(22.dp))
            Text(stringResource(if (verify) R.string.handover_verify_title else R.string.handover_title, where), style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f))
            trailing()
        }
        val step = check?.name.orEmpty()
        if (verify && step.isNotBlank()) Text(step, style = MaterialTheme.typography.bodyMedium, color = c.ink)
        Text(stringResource(if (verify) R.string.handover_verify_body else R.string.handover_body), style = MaterialTheme.typography.bodyMedium, color = c.muted)
        if (open) {
            PrimaryButton(stringResource(if (verify) R.string.handover_verify_open else R.string.handover_solve), enabled = !working, modifier = Modifier.fillMaxWidth(), onClick = onSolve)
            SecondaryButton(stringResource(R.string.handover_skip), enabled = !working, modifier = Modifier.fillMaxWidth(), onClick = onSkip)
        }
    }
}

/** An approval's readable pairs (hosts rather than URLs), or its one sentence; the raw arguments are never shown. */
@Composable
fun ApprovalFacts(lines: List<ApprovalLine>, detail: String?) {
    val c = Kelo.colors
    if (lines.isNotEmpty()) {
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(c.bubble).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            lines.forEach { l ->
                if (l.full) Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    if (l.label.isNotEmpty()) Text(l.label, style = MaterialTheme.typography.bodySmall, color = c.muted)
                    Text(l.value, style = MaterialTheme.typography.bodyMedium, color = c.ink)
                } else Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (l.label.isNotEmpty()) Text(l.label, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.widthIn(min = 56.dp, max = 140.dp))
                    Text(l.value, style = MaterialTheme.typography.bodySmall, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                }
            }
        }
    } else if (!detail.isNullOrBlank()) {
        Text(detail, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    }
}

@Composable
fun ResultLine(icon: ImageVector, text: String, muted: Boolean = false) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(icon, contentDescription = null, tint = if (muted) c.muted else c.ink, modifier = Modifier.size(16.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = if (muted) c.muted else c.ink)
    }
}

/**
 * The agent's questions with answers to click (one question: one click sends it; several: pick, then send together). An answer to
 * write ("Other", "직접 입력할게요") opens a text box under its question and the typed words are sent instead (QuickReplyRules, shared
 * with the phone app).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun QuickReplies(set: ReplySet, onSend: (String, List<ReplyAnswer>) -> Unit) {
    val c = Kelo.colors
    var picks by remember(set) { mutableStateOf(mapOf<Int, String>()) }
    var typed by remember(set) { mutableStateOf(mapOf<Int, String>()) }
    // what each field (a picker or a box) holds: its plain form once complete, "" while half filled; starts on the question's default
    var values by remember(set) {
        mutableStateOf(set.questions.withIndex().mapNotNull { (i, q) -> ReplyFields.start(q)?.takeIf { ReplyFields.isField(q) }?.let { i to it } }.toMap())
    }
    val show = rememberFieldWords()
    val writing = QuickReplyRules.writing(set, picks)
    val canSend = QuickReplyRules.canSend(set, picks, typed, values)
    val send = { if (canSend) onSend(QuickReplyRules.compose(set, picks, typed, values, show), QuickReplyRules.answers(set, picks, typed, values)) }
    val other = stringResource(R.string.reply_other)
    Column(Modifier.fillMaxWidth().padding(start = 4.dp, end = 32.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        set.questions.forEachIndexed { i, q ->
            if (q.question.isNotBlank() && (set.questions.size > 1 || ReplyFields.isField(q))) {
                Text(q.question, style = MaterialTheme.typography.labelLarge, color = c.muted)
            }
            if (ReplyFields.isField(q)) Box(Modifier.widthIn(max = 560.dp)) { ReplyField(q, values[i].orEmpty(), onValue = { values = values + (i to it) }) }
            else FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                QuickReplyRules.choices(q).forEach { o ->
                    ChoiceChip(if (o == QuickReplyRules.OTHER) other else o, selected = picks[i] == o, onClick = {
                        if (QuickReplyRules.sendsOnTap(set, q, o)) onSend(o, listOf(ReplyAnswer(i, o)))
                        else picks = if (picks[i] == o) picks - i else picks + (i to o)
                    })
                }
            }
            if (i in writing) {
                val focus = remember { androidx.compose.ui.focus.FocusRequester() }
                KeloTextField(
                    value = typed[i].orEmpty(), onValueChange = { typed = typed + (i to it.replace("\n", " ")) },
                    placeholder = QuickReplyRules.placeholder(q) ?: stringResource(R.string.reply_type_hint),
                    modifier = Modifier.widthIn(max = 560.dp).focusRequester(focus), maxLength = 300,
                    keyboardActions = androidx.compose.foundation.text.KeyboardActions(onDone = { send() }),
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(imeAction = androidx.compose.ui.text.input.ImeAction.Done),
                )
                LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
            }
        }
        if (QuickReplyRules.showsSend(set, picks)) {
            PrimaryButton(stringResource(R.string.reply_send), enabled = canSend, onClick = send)
            if (!QuickReplyRules.sendsAtOnce(set)) Text(stringResource(R.string.reply_pick_hint), style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
    }
}

/** A data: URL (a browser picture from the chat's events) as an image, decoded off the drawing. */
@Composable
fun rememberDataImage(dataUrl: String?): ImageBitmap? {
    var img by remember(dataUrl) { mutableStateOf<ImageBitmap?>(null) }
    LaunchedEffect(dataUrl) {
        img = dataUrl?.substringAfter("base64,", "")?.takeIf { it.isNotEmpty() }?.let { b64 ->
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) {
                runCatching { org.jetbrains.skia.Image.makeFromEncoded(java.util.Base64.getDecoder().decode(b64)).toComposeImageBitmap() }.getOrNull()
            }
        }
    }
    return img
}

/**
 * The assistant's browser tab as one compact row (android/kelo .../chat/PurchaseUi.kt BrowserCard): a tile with the site's first
 * letter, the site and what the agent is doing there, and 보기 (that tab live, where the user can also take it over). Under it,
 * once there are pictures, the last few as a small filmstrip (click one to see it large).
 */
@Composable
fun BrowserCard(b: ChatItem.Browser, onOpenLive: (() -> Unit)? = null) {
    val c = Kelo.colors
    var full by remember { mutableStateOf<String?>(null) }
    val site = b.host.ifEmpty { b.title.ifEmpty { stringResource(R.string.chat_browser_title) } }
    Column(
        Modifier.fillMaxWidth().widthIn(max = 640.dp).clip(RoundedCornerShape(12.dp)).border(1.dp, c.line, RoundedCornerShape(12.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Box(Modifier.size(32.dp).clip(RoundedCornerShape(8.dp)).background(SiteTile.color(b.host.ifEmpty { b.id }, c.dark)), contentAlignment = Alignment.Center) {
                Text(SiteTile.letter(b.host, b.title), color = chat.kelo.app.ui.theme.TileInk, fontWeight = FontWeight.Bold, fontSize = 15.sp)
            }
            Column(Modifier.weight(1f)) {
                Text(site, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                // the newest step; once the tab went through several sites in this reply, how many
                val step = b.status.ifEmpty { b.title }
                val line = if (b.hosts.size > 1) stringResource(R.string.chat_browser_step_sites, step, b.hosts.size) else step
                Text(line, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            if (onOpenLive != null) {
                androidx.compose.material3.TextButton(onClick = onOpenLive) {
                    Text(stringResource(R.string.chat_browser_view_short), color = c.accent, style = MaterialTheme.typography.labelLarge)
                }
            }
        }
        val strip = b.recent.ifEmpty { listOfNotNull(b.image) }
        if (strip.isNotEmpty()) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { strip.forEach { url -> FilmFrame(url, onOpen = { full = url }) } }
        }
    }
    full?.let { url ->
        val image = rememberDataImage(url)
        if (image != null) androidx.compose.material3.AlertDialog(
            onDismissRequest = { full = null }, containerColor = c.card,
            text = {
                Image(image, contentDescription = b.title.ifEmpty { b.host }, contentScale = ContentScale.Fit,
                    modifier = Modifier.widthIn(max = 900.dp).fillMaxWidth().clip(RoundedCornerShape(8.dp)))
            },
            confirmButton = { androidx.compose.material3.TextButton(onClick = { full = null }) { Text(stringResource(R.string.close), color = c.ink) } },
        )
    }
}

/** One small picture of the filmstrip (the top of the page, cropped). */
@Composable
private fun FilmFrame(dataUrl: String, onOpen: () -> Unit) {
    val c = Kelo.colors
    val image = rememberDataImage(dataUrl)
    val viewLabel = stringResource(R.string.chat_browser_view)
    Box(
        Modifier.size(width = 96.dp, height = 64.dp).clip(RoundedCornerShape(6.dp)).background(c.bubble).border(1.dp, c.line, RoundedCornerShape(6.dp))
            .clickable(enabled = image != null, role = androidx.compose.ui.semantics.Role.Image, onClickLabel = viewLabel, onClick = onOpen),
    ) {
        if (image != null) Image(image, contentDescription = viewLabel, contentScale = ContentScale.Crop, alignment = Alignment.TopCenter, modifier = Modifier.fillMaxSize())
    }
}

/** The letter and colour of a site's tile (android/kelo .../chat/PurchaseUi.kt SiteTile, ConnectorList.hue / tileColor). */
object SiteTile {
    fun letter(host: String, title: String): String {
        val h = host.removePrefix("www.")
        return (h.firstOrNull { it.isLetterOrDigit() } ?: title.firstOrNull { it.isLetterOrDigit() } ?: '?').uppercaseChar().toString()
    }

    fun hue(id: String): Float {
        var h = 0x811C9DC5.toInt()   // FNV-1a
        id.forEach { ch -> h = (h xor ch.code) * 0x01000193 }
        return ((h ushr 1) % 360).toFloat()
    }

    fun color(id: String, dark: Boolean): androidx.compose.ui.graphics.Color =
        androidx.compose.ui.graphics.Color.hsl(hue(id), 0.42f, if (dark) 0.34f else 0.38f)
}

@Composable
fun PaymentAutoRow(p: ChatItem.PaymentAuto) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        NoticeChip(
            if (p.cardLabel.isNotEmpty()) stringResource(R.string.chat_payment_auto, p.site, p.amountText, p.cardLabel)
            else stringResource(R.string.chat_payment_auto_no_card, p.site, p.amountText),
        )
        // an order paid on its own: what was in it and where it goes, in small lines under the notice
        val order = p.order
        if (order != null && order.items.isNotEmpty()) {
            val words = LocalStrings.current
            Text(order.items.joinToString(", ") { it.name + " " + words.get(R.string.order_quantity, it.quantity) },
                style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted, modifier = Modifier.padding(horizontal = 10.dp))
        }
        order?.shipping?.let { s ->
            val where = chat.kelo.app.wallet.AddressRules.who(s.label, chat.kelo.app.wallet.AddressRules.oneLine(s.formatted), "")
            if (where.isNotEmpty()) Text(stringResource(R.string.shipping_ask_done, where), style = MaterialTheme.typography.bodySmall,
                color = Kelo.colors.muted, modifier = Modifier.padding(horizontal = 10.dp))
        }
    }
}

/** An allowed payment waits for a card kept on a phone: only that phone can send it. */
@Composable
fun CardNeededRow(item: ChatItem.CardNeeded) {
    CardBox {
        Text(stringResource(R.string.chat_card_needed_title), style = MaterialTheme.typography.titleMedium, color = Kelo.colors.ink)
        MutedText(stringResource(R.string.chat_card_needed_body, item.site, item.amountText, item.card.label), small = true)
        if (!item.ended) MutedText(stringResource(R.string.chat_card_error_not_on_phone), small = true)
    }
}

/**
 * The secure form for a login or a key the agent asked for: the value goes to the server's vault (PUT /api/vault/{name}/login or
 * /api/credentials/{name}/secret) and is never shown again, nor seen by the agent. It folds into one line once the agent was told.
 */
@Composable
fun VaultCard(item: ChatItem.Vault, sessions: Sessions, onSettle: (CardState) -> Unit, onNotify: (String) -> Unit) {
    when (item.state) {
        CardState.OPEN -> SecureCard(item.name, sessions, onClosed = { onSettle(CardState.CANCELLED) }, onNotify = { text -> onSettle(CardState.DONE); onNotify(text) })
        CardState.DONE -> CardBox { ResultLine(KeloIcons.Shield, stringResource(R.string.chat_vault_done)) }
        CardState.CANCELLED -> CardBox { ResultLine(KeloIcons.Close, stringResource(R.string.chat_card_cancelled), muted = true) }
        CardState.EXPIRED -> CardBox { ResultLine(KeloIcons.Clock, stringResource(R.string.chat_card_expired), muted = true) }
    }
}

@Composable
private fun SecureCard(name: String, sessions: Sessions, onClosed: () -> Unit, onNotify: (String) -> Unit) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var item by remember(name) { mutableStateOf<VaultItem?>(null) }
    var done by remember(name) { mutableStateOf<VaultMode?>(null) }
    LaunchedEffect(name) { item = try { VaultCalls.find(sessions.requireApi(), name) } catch (_: ApiError) { null } }
    val it = item ?: return
    val what = stringResource(if (it.isLogin) R.string.vault_what_login else R.string.vault_what_key, it.label)
    CardBox {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(KeloIcons.Shield, contentDescription = null, tint = c.ink, modifier = Modifier.size(20.dp))
            Spacer(Modifier.size(8.dp))
            Text(stringResource(R.string.vault_card_needed, what), fontWeight = FontWeight.Bold, color = c.ink)
        }
        if (it.hosts.isNotEmpty()) MutedText(stringResource(R.string.vault_card_hosts, it.hosts.joinToString(", ")), small = true)
        val d = done
        if (d != null) {
            Text(stringResource(when (d) {
                VaultMode.AFTER_SUCCESS -> R.string.vault_card_done_after_success
                VaultMode.ONCE -> R.string.vault_card_done_once
                VaultMode.NOW -> R.string.vault_card_done_now
            }), style = MaterialTheme.typography.bodySmall, color = c.ink)
            val text = stringResource(if (d == VaultMode.NOW) R.string.vault_card_notify_saved else R.string.vault_card_notify_entered, what)
            PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
        } else {
            SecureForm(it, sessions, onSaved = { m -> done = m })
            LinkButton(stringResource(R.string.cancel), onClick = {
                scope.launch {
                    runCatching { VaultCalls.cancelDraft(sessions.requireApi(), name) }
                    onClosed()
                }
            })
        }
    }
}

/** The login (user name + password, and how long to keep it) or key form. */
@Composable
private fun SecureForm(item: VaultItem, sessions: Sessions, onSaved: (VaultMode) -> Unit) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var user by remember(item.name) { mutableStateOf("") }
    var pass by remember(item.name) { mutableStateOf("") }
    var mode by remember(item.name) { mutableStateOf(if (item.isLogin) VaultMode.AFTER_SUCCESS else VaultMode.NOW) }
    var msg by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val login = item.isLogin
    fun submit() {
        busy = true; msg = ""
        scope.launch {
            try {
                val api = sessions.requireApi()
                if (login) VaultCalls.putLogin(api, item.name, user.trim(), pass, mode) else VaultCalls.putSecret(api, item.name, pass.trim())
                user = ""; pass = ""
                onSaved(mode)
            } catch (e: ApiError) {
                msg = e.detail
            } finally {
                busy = false
            }
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (login) KeloTextField(user, { user = it }, stringResource(R.string.vault_username), maxLength = 200, label = stringResource(R.string.vault_username))
        KeloTextField(pass, { pass = it }, stringResource(if (login) R.string.vault_password else R.string.vault_key), password = true, maxLength = 512,
            label = stringResource(if (login) R.string.vault_password else R.string.vault_key))
        if (login) {
            Text(stringResource(R.string.vault_mode), style = MaterialTheme.typography.labelLarge, color = c.ink)
            VaultMode.entries.forEach { m ->
                val on = m == mode
                Column(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(if (on) c.mine else c.bubble).clickable { mode = m }.padding(10.dp),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    Text(stringResource(when (m) {
                        VaultMode.AFTER_SUCCESS -> R.string.vault_mode_after_success
                        VaultMode.NOW -> R.string.vault_mode_now
                        VaultMode.ONCE -> R.string.vault_mode_once
                    }), style = MaterialTheme.typography.bodyMedium, color = c.ink, fontWeight = if (on) FontWeight.SemiBold else null)
                    if (on) Text(stringResource(when (m) {
                        VaultMode.AFTER_SUCCESS -> R.string.vault_mode_after_success_help
                        VaultMode.NOW -> R.string.vault_mode_now_help
                        VaultMode.ONCE -> R.string.vault_mode_once_help
                    }), style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
            }
        }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
        PrimaryButton(stringResource(if (busy) R.string.vault_saving else R.string.vault_save), onClick = ::submit,
            enabled = !busy && pass.isNotBlank() && (!login || user.isNotBlank()))
        MutedText(stringResource(R.string.vault_footer), small = true)
    }
}

/** The agent asked to connect a service: a short card that opens that connector's page here. */
@Composable
fun ConnectCard(item: ChatItem.Connect, onOpen: (String) -> Unit, sessions: chat.kelo.app.core.Sessions? = null) {
    // the connector's row, for its name and icon (the plug and the id until it is known)
    val connector by androidx.compose.runtime.produceState<chat.kelo.app.connectors.Connector?>(null, item.service, item.state) {
        if (item.state == CardState.OPEN && sessions != null) {
            value = runCatching { chat.kelo.app.connectors.ConnectorCalls.list(sessions.requireApi()) }.getOrNull()?.find { it.id == item.service }
        }
    }
    CardBox {
        when (item.state) {
            CardState.OPEN -> {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    val x = connector
                    if (x != null) chat.kelo.desktop.pages.connectors.ConnectorTile(x, 28.dp)
                    else Icon(KeloIcons.Plug, contentDescription = null, tint = Kelo.colors.ink, modifier = Modifier.size(20.dp))
                    Text(x?.name?.ifBlank { null } ?: item.service, style = MaterialTheme.typography.titleMedium, color = Kelo.colors.ink, modifier = Modifier.weight(1f))
                }
                PrimaryButton(stringResource(R.string.desktop_connect_open, item.service), onClick = { onOpen(item.service) })
            }
            CardState.DONE -> ResultLine(KeloIcons.Check, stringResource(R.string.chat_connect_done))
            CardState.CANCELLED -> ResultLine(KeloIcons.Close, stringResource(R.string.chat_card_cancelled), muted = true)
            CardState.EXPIRED -> ResultLine(KeloIcons.Clock, stringResource(R.string.chat_card_expired), muted = true)
        }
    }
}

/** A model id as people read it (the fallback notice names the model it switched to). */
fun modelName(id: String): String = ModelNames.display(id, null)
