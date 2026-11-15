package chat.kelo.desktop.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.agent.PendingApproval
import chat.kelo.app.agent.RelTime
import chat.kelo.app.chat.ActionOutcome
import chat.kelo.app.chat.AskEnd
import chat.kelo.app.chat.AskOrigin
import chat.kelo.app.chat.ChatAsk
import chat.kelo.app.chat.ChatAsks
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.ProviderConsentRules
import chat.kelo.app.chat.ReportDraft
import chat.kelo.app.chat.ReportReason
import chat.kelo.app.chat.ReportRules
import chat.kelo.app.chat.vault.VaultCalls
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.followup.Followup
import chat.kelo.app.followup.FollowupAction
import chat.kelo.app.followup.FollowupRules
import chat.kelo.app.followup.FollowupStep
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.launch

/*
 * The chat's parts that the phone has in ChatAskCard.kt, agent/ApprovalParts.kt, followup/FollowupUi.kt, ReportSheet.kt,
 * StaleHint.kt and ChatScreen.kt's jump pill, drawn for the desktop.
 */

/** "3분 전" / "2시간 뒤" (android/kelo .../agent/AgentFormat.kt relText). */
@Composable
fun relText(seconds: Double, nowSeconds: Double = System.currentTimeMillis() / 1000.0): String {
    val r = RelTime.of(seconds, nowSeconds)
    val n = r.amount.toInt()
    return when (r.unit) {
        RelTime.Unit.NOW -> stringResource(if (r.future) R.string.agent_rel_soon else R.string.agent_rel_now)
        RelTime.Unit.MINUTES -> pluralStringResource(if (r.future) R.plurals.agent_rel_minutes_future else R.plurals.agent_rel_minutes_past, n, n)
        RelTime.Unit.HOURS -> pluralStringResource(if (r.future) R.plurals.agent_rel_hours_future else R.plurals.agent_rel_hours_past, n, n)
        RelTime.Unit.DAYS -> pluralStringResource(if (r.future) R.plurals.agent_rel_days_future else R.plurals.agent_rel_days_past, n, n)
    }
}

/**
 * A question from work the chat is not following (a background job, a scheduled run), live at the bottom of the chat: a line
 * saying what asked, then the same card and answers as on the 작업 page. Once answered (here, elsewhere, or a human check that
 * ended on the server) it folds into one line. [onAnswer]: (allow, remember the site, remember for the scheduled job).
 */
@Composable
fun ChatAskCard(ask: ChatAsk, onSolve: () -> Unit, onAnswer: (Boolean, Boolean, Boolean) -> Unit) {
    val a = ask.approval
    Box(Modifier.fillMaxWidth().animateContentSize()) {
        val end = ask.end
        CardBox {
            if (end != null) {
                val what = a.event.label.ifBlank { a.event.name }
                ResultLine(
                    when (end) { AskEnd.DENIED, AskEnd.SKIPPED -> KeloIcons.Close; AskEnd.GONE -> KeloIcons.Clock; else -> KeloIcons.Check },
                    when (end) {
                        AskEnd.SOLVED -> stringResource(if (a.isVerification) R.string.handover_verify_done else R.string.handover_solved)
                        AskEnd.SKIPPED -> stringResource(R.string.handover_skipped, a.site.ifBlank { stringResource(R.string.handover_this_site) })
                        else -> stringResource(R.string.chat_ask_resolved, what, askEndText(end))
                    },
                    muted = end == AskEnd.GONE || end == AskEnd.DENIED || end == AskEnd.SKIPPED,
                )
            } else {
                OriginLine(a)
                if (a.isHumanCheck) {
                    HumanCheckBody(a.site, ask.working, onSolve = onSolve, onSkip = { onAnswer(false, false, false) }, check = a.event.check)
                    AskedLine(a)
                } else {
                    PendingApprovalBody(a, ask.working, onAnswer)
                }
            }
        }
    }
}

@Composable
private fun AskedLine(a: PendingApproval) {
    if (a.askedAt > 0) Text(stringResource(R.string.agent_perm_asked, relText(a.askedAt)), style = MaterialTheme.typography.labelSmall, color = Kelo.colors.muted)
}

/** "따로 맡긴 일 · <the job's title>" over the card; a polite live region, so a screen reader says a new question came. */
@Composable
private fun OriginLine(a: PendingApproval) {
    val c = Kelo.colors
    val origin = ChatAsks.origin(a)
    val head = stringResource(
        when (origin) {
            AskOrigin.JOB -> R.string.chat_ask_from_job
            AskOrigin.SCHEDULE -> R.string.chat_ask_from_schedule
            AskOrigin.AUTO -> R.string.work_ask_unattended
            AskOrigin.CHAT -> R.string.chat_ask_from_reply
        },
    )
    val title = a.title.trim().takeIf { it.isNotEmpty() && origin != AskOrigin.CHAT }
    Row(
        Modifier.fillMaxWidth().semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Icon(if (origin == AskOrigin.SCHEDULE) KeloIcons.Calendar else KeloIcons.Work, contentDescription = null, tint = c.muted, modifier = Modifier.size(14.dp))
        Text(
            if (title != null) stringResource(R.string.chat_ask_titled, head, title) else head,
            style = MaterialTheme.typography.labelMedium, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun askEndText(e: AskEnd): String = stringResource(
    when (e) {
        AskEnd.ALLOWED, AskEnd.SOLVED -> R.string.agent_approval_allowed
        AskEnd.ALWAYS -> R.string.agent_approval_always
        AskEnd.KEPT_FOR_SCHEDULE -> R.string.approval_kept_for_schedule
        AskEnd.DENIED, AskEnd.SKIPPED -> R.string.agent_approval_denied
        AskEnd.GONE -> R.string.agent_approval_gone
    },
)

/**
 * What a waiting approval shows and offers (android/kelo .../agent/ApprovalParts.kt PendingApprovalBody): the server's whole
 * question, the site, what it will do, when it was asked, 거절 / 허용, "<site>에선 늘 허용" when the server allows remembering it,
 * and "keep allowing in this scheduled job" when offered. A payment with a card kept end-to-end on a phone can only be refused
 * here (only that phone can send the card).
 */
@Composable
fun PendingApprovalBody(a: PendingApproval, working: Boolean, onAnswer: (Boolean, Boolean, Boolean) -> Unit) {
    val c = Kelo.colors
    val language = LocalStrings.current.code.substringBefore('-')
    val payment = a.event.payment
    val needsCard = a.event.card?.needsCard == true
    if (a.event.urgent) UrgentLine(a.event.missed)
    a.event.grant?.takeIf { payment == null }?.let { grant ->
        // the setup card of a "when X happens, do Y" task, the same as in the chat
        TaskGrantApproval(grant, open = !working) { ok -> onAnswer(ok, false, false) }
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(if (payment != null) KeloIcons.Card else KeloIcons.Shield, contentDescription = null, tint = c.accent)
            val word = a.event.label.ifBlank { a.event.name }
            Text(
                if (payment != null) stringResource(R.string.chat_purchase_question)
                else ChatAsks.question(a.event.ask) ?: stringResource(R.string.chat_approval_ask, ProviderConsentRules.askWord(word, language), ProviderConsentRules.objectParticle(word)),
                style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f),
            )
        }
        if (payment != null) {
            // the order at once: site, items, where it goes, what pays, coupon, total (chat/OrderParts.kt)
            OrderDetails(payment, a.event.card)
            a.event.card?.let { card ->
                if (card.reasonText.isNotEmpty()) MutedText(stringResource(R.string.chat_purchase_not_auto, card.reasonText), small = true)
                if (card.needsCard) MutedText(stringResource(R.string.chat_card_error_not_on_phone), small = true)
            }
        } else {
            if (a.site.isNotBlank() && a.event.lines.isEmpty()) MutedText(a.site, small = true)
            ApprovalFacts(a.event.lines, a.event.detail)
            if (a.event.items.isNotEmpty()) ApprovalItems(a.event.items)
        }
        if (a.event.pin != null) MutedText(stringResource(R.string.desktop_pin_allow_note), small = true)
        AskedLine(a)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SecondaryButton(stringResource(R.string.chat_approval_deny), enabled = !working, modifier = Modifier.weight(1f), onClick = { onAnswer(false, false, false) })
            if (!needsCard) PrimaryButton(stringResource(R.string.chat_approval_allow), enabled = !working, modifier = Modifier.weight(1f), onClick = { onAnswer(true, false, false) })
        }
        if (a.canRemember && a.site.isNotBlank() && payment == null) {
            LinkButton(stringResource(R.string.agent_perm_always, a.site), onClick = { onAnswer(true, true, false) })
        }
        if (a.canRememberSchedule) LinkButton(stringResource(R.string.approval_keep_for_schedule), onClick = { onAnswer(true, false, true) })
    }
}

// ---- follow-up buttons under the agent's messages (followup/FollowupUi.kt)

@Composable
fun actionLabel(step: FollowupStep): String = when (step) {
    is FollowupStep.OpenLogin -> stringResource(R.string.followup_action_login)
    is FollowupStep.Answer -> stringResource(if (step.approve) R.string.followup_action_approve else R.string.followup_action_deny)
    is FollowupStep.ResumeWorker -> stringResource(R.string.followup_action_resume)
    is FollowupStep.Drop -> stringResource(R.string.followup_action_drop)
    is FollowupStep.Snooze -> stringResource(R.string.followup_action_snooze, laterText(step.minutes))
    is FollowupStep.Retry -> stringResource(R.string.followup_action_retry)
}

/** "30분 뒤" / "2시간 뒤" / "1일 뒤" */
@Composable
fun laterText(minutes: Int): String = when {
    minutes >= 1440 && minutes % 1440 == 0 -> pluralStringResource(R.plurals.agent_rel_days_future, minutes / 1440, minutes / 1440)
    minutes >= 60 && minutes % 60 == 0 -> pluralStringResource(R.plurals.agent_rel_hours_future, minutes / 60, minutes / 60)
    else -> pluralStringResource(R.plurals.agent_rel_minutes_future, minutes, minutes)
}

private fun primary(step: FollowupStep): Boolean =
    step is FollowupStep.OpenLogin || step is FollowupStep.Retry || (step is FollowupStep.Answer && step.approve) || step is FollowupStep.ResumeWorker

/** The buttons under an agent's message: they wrap onto more lines rather than squeeze; [busy] shows a small spinner instead. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FollowupButtons(buttons: List<Pair<FollowupAction, FollowupStep>>, busy: Boolean, onPress: (FollowupStep) -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    if (buttons.isEmpty()) return
    if (busy) {
        Row(modifier.padding(vertical = 6.dp)) { CircularProgressIndicator(Modifier.size(18.dp), color = c.muted, strokeWidth = 2.dp) }
        return
    }
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        val main = buttons.indexOfFirst { primary(it.second) }
        buttons.forEachIndexed { i, (_, step) ->
            val label = actionLabel(step)
            val pad = PaddingValues(horizontal = 14.dp, vertical = 8.dp)
            if (i == main) Button(
                onClick = { onPress(step) }, shape = RoundedCornerShape(50), contentPadding = pad,
                colors = ButtonDefaults.buttonColors(containerColor = c.accent, contentColor = c.onAccent),
            ) { Text(label, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center) }
            else OutlinedButton(
                onClick = { onPress(step) }, shape = RoundedCornerShape(50), contentPadding = pad, border = BorderStroke(1.dp, c.line),
                colors = ButtonDefaults.outlinedButtonColors(containerColor = c.card, contentColor = c.ink),
            ) { Text(label, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center) }
        }
    }
}

@Composable
fun outcomeText(o: ActionOutcome): String = stringResource(
    when (o) {
        ActionOutcome.APPROVED -> R.string.followup_done_approved
        ActionOutcome.DENIED -> R.string.followup_done_denied
        ActionOutcome.RESUMED -> R.string.followup_done_resumed
        ActionOutcome.DROPPED -> R.string.followup_done_dropped
        ActionOutcome.SNOOZED -> R.string.followup_done_snoozed
        ActionOutcome.SIGNED_IN -> R.string.followup_done_signed_in
        ActionOutcome.GONE -> R.string.followup_done_gone
        ActionOutcome.RESOLVED -> R.string.followup_done_resolved
    },
)

/** A message's follow-up: its buttons, or once settled the one line that says how. "Sign in" opens the secure form. */
@Composable
fun MessageFollowups(f: Followup, state: ChatState, model: ChatModel, sessions: Sessions) {
    var login by remember(f.id) { mutableStateOf<String?>(null) }
    val outcome = ChatReducer.closedOutcome(f, state.outcomes[f.id])
    if (outcome != null) {
        ResultLine(if (outcome == ActionOutcome.GONE) KeloIcons.Clock else KeloIcons.Check, outcomeText(outcome), muted = outcome == ActionOutcome.GONE)
        return
    }
    val buttons = remember(f.actions, f.id) { FollowupRules.buttons(f.actions, f.id) }
    FollowupButtons(buttons, busy = f.id in state.acting, onPress = { step ->
        if (step is FollowupStep.OpenLogin) login = step.name else model.runAction(f, step)
    })
    login?.let { name ->
        SignInDialog(name, sessions, onNotify = { text -> model.actionSignedIn(f); model.send(text) }, onClose = { login = null })
    }
}

/** The sign-in form for one keychain item, opened from a follow-up button (the phone's SignInSheet). */
@Composable
private fun SignInDialog(name: String, sessions: Sessions, onNotify: (String) -> Unit, onClose: () -> Unit) {
    val c = Kelo.colors
    var state by remember(name) { mutableStateOf(0) }   // 0 looking, 1 found, 2 gone
    LaunchedEffect(name) { state = try { if (VaultCalls.find(sessions.requireApi(), name) != null) 1 else 2 } catch (_: ApiError) { 2 } }
    AlertDialog(
        onDismissRequest = onClose, containerColor = c.bg,
        title = { Text(stringResource(R.string.signin_sheet_title), color = c.ink) },
        text = {
            Column(Modifier.widthIn(max = 520.dp).verticalScroll(rememberScrollState())) {
                when (state) {
                    0 -> CircularProgressIndicator(Modifier.size(22.dp), color = c.muted, strokeWidth = 2.dp)
                    1 -> VaultCard(
                        ChatItem.Vault(key = "signin:$name", name = name), sessions,
                        onSettle = { if (it != chat.kelo.app.chat.CardState.DONE) onClose() },
                        onNotify = { text -> onNotify(text); onClose() },
                    )
                    else -> MutedText(stringResource(R.string.signin_sheet_gone), small = true)
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text(stringResource(R.string.close), color = c.ink) } },
    )
}

// ---- 신고하기 (chat/ReportSheet.kt), as a dialog

/**
 * Tell the Kelo developer about a reply that was offensive, harmful, wrong or exposed someone's private details. It says exactly
 * what will be sent before anything is (POST /api/reports). [draft] is what was left from last time.
 */
@Composable
fun ReportDialog(sessions: Sessions, reply: String, asked: String?, draft: ReportDraft, onDraft: (ReportDraft) -> Unit, onSent: () -> Unit, onClose: () -> Unit) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var d by remember { mutableStateOf(draft) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    val offline = stringResource(R.string.report_failed_offline)
    AlertDialog(
        onDismissRequest = { if (!busy) { onDraft(d); onClose() } }, containerColor = c.card,
        title = { Text(stringResource(R.string.report_title), color = c.ink) },
        text = {
            Column(Modifier.widthIn(max = 560.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                MutedText(stringResource(R.string.report_intro), small = true)
                Text(stringResource(R.string.report_reason_label), color = c.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
                Column {
                    for (r in ReportReason.entries) {
                        Row(
                            Modifier.fillMaxWidth().selectable(d.reason == r, role = Role.RadioButton, onClick = { d = d.copy(reason = r) }).padding(vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = d.reason == r, onClick = null, colors = RadioButtonDefaults.colors(selectedColor = c.accent, unselectedColor = c.muted))
                            Text(stringResource(r.label), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 10.dp).weight(1f))
                        }
                    }
                }
                KeloTextField(d.comment, { d = d.copy(comment = it) }, stringResource(R.string.report_comment_hint), maxLength = ReportRules.MAX_COMMENT)
                if (asked != null) {
                    Row(
                        Modifier.fillMaxWidth().toggleable(d.includeAsked, role = Role.Checkbox, onValueChange = { d = d.copy(includeAsked = it) }).padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(checked = d.includeAsked, onCheckedChange = null, colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent, uncheckedColor = c.muted))
                        Text(stringResource(R.string.report_include_asked), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 8.dp).weight(1f))
                    }
                }
                Column(Modifier.fillMaxWidth().background(c.bubble, RoundedCornerShape(12.dp)).padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(stringResource(R.string.report_sent_title), color = c.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodySmall)
                    val lines = buildList {
                        add(R.string.report_sent_reply)
                        if (asked != null && d.includeAsked) add(R.string.report_sent_asked)
                        add(R.string.report_sent_reason)
                        add(R.string.report_sent_model)
                        add(R.string.report_sent_version)
                    }
                    for (l in lines) Text(stringResource(R.string.report_bullet, stringResource(l)), color = c.ink, style = MaterialTheme.typography.bodySmall)
                    Text(stringResource(R.string.report_sent_not), color = c.muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 4.dp))
                }
                if (err.isNotEmpty()) ErrorText(err, small = true)
            }
        },
        confirmButton = {
            PrimaryButton(stringResource(if (busy) R.string.report_sending else R.string.report_send), enabled = !busy && ReportRules.canSend(d), onClick = {
                busy = true; err = ""
                scope.launch {
                    try {
                        sessions.requireApi().request("POST", "/api/reports", ReportRules.body(d, reply, asked, "desktop " + chat.kelo.desktop.core.ReleaseInfo.VERSION + " (" + chat.kelo.desktop.core.ReleaseInfo.PACKAGE + ")"))
                        onSent()
                    } catch (e: ApiError) {
                        err = if (e.isNetwork) offline else e.detail
                        onDraft(d)
                    } finally {
                        busy = false
                    }
                }
            })
        },
        dismissButton = { TextButton(onClick = { onDraft(d); onClose() }, enabled = !busy) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

// ---- small floating parts

/** The "↓" pill: back to the newest row, with a dot when the assistant wrote more meanwhile. */
@Composable
fun JumpPill(unread: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    val label = stringResource(if (unread) R.string.chat_jump_latest_new else R.string.chat_jump_latest)
    Box {
        Box(
            Modifier.shadow(6.dp, CircleShape, clip = false).size(chat.kelo.app.chat.ChatOverlayRules.JUMP_SIZE.dp).clip(CircleShape).background(c.card).border(1.dp, c.line, CircleShape)
                .clickable(role = Role.Button, onClickLabel = label, onClick = onClick).semantics { contentDescription = label },
            contentAlignment = Alignment.Center,
        ) { Icon(KeloIcons.ArrowDown, contentDescription = null, tint = c.ink, modifier = Modifier.size(20.dp)) }
        if (unread) Box(Modifier.align(Alignment.TopEnd).size(11.dp).clip(CircleShape).background(c.card).padding(2.dp).clip(CircleShape).background(c.accent))
    }
}

/** A small floating pill: what shows was kept from last time, the server cannot be reached right now. Click to try again. */
@Composable
fun StaleHint(visible: Boolean, modifier: Modifier = Modifier, onRetry: (() -> Unit)? = null) {
    AnimatedVisibility(visible = visible, modifier = modifier, enter = fadeIn(), exit = fadeOut()) {
        val c = Kelo.colors
        val shape = RoundedCornerShape(50)
        Box(Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
            Row(
                Modifier.widthIn(max = 420.dp).shadow(3.dp, shape, clip = false).clip(shape).background(c.card).border(1.dp, c.line, shape)
                    .then(if (onRetry != null) Modifier.clickable(role = Role.Button, onClick = onRetry) else Modifier)
                    .semantics { liveRegion = LiveRegionMode.Polite }.heightIn(min = 28.dp).padding(horizontal = 12.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(7.dp),
            ) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(c.muted))
                Text(stringResource(R.string.stale_hint), color = c.muted, fontSize = 13.sp, lineHeight = 17.sp, textAlign = TextAlign.Center, maxLines = 3)
            }
        }
    }
}
