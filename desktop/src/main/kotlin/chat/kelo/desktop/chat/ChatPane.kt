package chat.kelo.desktop.chat

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ContextMenuArea
import androidx.compose.foundation.ContextMenuItem
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.ui.draw.rotate
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.wrapContentWidth
import chat.kelo.desktop.ui.SecondaryButton
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draganddrop.DragAndDropEvent
import androidx.compose.ui.draganddrop.DragAndDropTarget
import androidx.compose.ui.draganddrop.awtTransferable
import androidx.compose.foundation.draganddrop.dragAndDropTarget
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isMetaPressed
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.chat.ChatError
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatOverlayRules
import chat.kelo.app.chat.ChatAsks
import chat.kelo.app.chat.ChatScroll
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.launch
import chat.kelo.app.chat.ChatNotice
import chat.kelo.app.chat.Markdown
import chat.kelo.app.chat.MessageActions
import chat.kelo.app.chat.MsgAction
import chat.kelo.app.chat.Phase
import chat.kelo.app.chat.PhaseKind
import chat.kelo.app.core.Profile
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.stringResource
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import java.awt.datatransfer.DataFlavor
import java.io.File

/**
 * The chat page: the conversation (newest at the bottom), the status line while a reply is written, and the composer. Files can
 * be dropped anywhere on the page (or picked with "+"): each goes up at once (100 MB at most per file) and is named in the next
 * message. Enter sends, Shift+Enter starts a new line. Right-click a message to copy it or try again.
 *
 * [topInset] is the floating header's height: the list scrolls under it.
 */
@OptIn(ExperimentalFoundationApi::class, ExperimentalComposeUiApi::class)
@Composable
fun ChatPane(
    model: ChatModel,
    sessions: Sessions,
    profile: Profile,
    onProfile: (Profile) -> Unit,
    onOpenConnector: (String) -> Unit,
    topInset: androidx.compose.ui.unit.Dp,
    modifier: Modifier = Modifier,
    /** "Solve it" on a human check: open that browser tab's live view with control taken */
    onSolveCheck: (tab: String) -> Unit = {},
    /** A card asked for a place: a browser card's 보기 opens the tab its pictures came from (app.openRequests) */
    onOpenTarget: (chat.kelo.app.notify.OpenTarget) -> Unit = {},
    /** A follow-up notification asked for one message: the chat scrolls to it, then empties this */
    focus: kotlinx.coroutines.flow.MutableStateFlow<Long?>? = null,
    /** The morning briefing's goal item: the Goals page */
    onOpenGoals: () -> Unit = {},
    /** A row in 답이 필요해요 asked for a secure entry's card: the chat scrolls to it, marks it for a moment, then empties this */
    entryFocus: kotlinx.coroutines.flow.MutableStateFlow<String?>? = null,
    /** A secure entry of this kind was saved or cancelled here: the badge counts it out at once */
    onEntryClosed: (String) -> Unit = {},
) {
    val c = Kelo.colors
    val state by model.state.collectAsState()
    val strings = LocalStrings.current
    var chooser by remember { mutableStateOf(false) }
    // the current provider's model list: the chip's readable name and the chooser's first section. The last good one (kept on this
    // computer) shows at once, so the chip never changes its wording when the fresh list arrives (see ModelCatalog)
    val modelList by androidx.compose.runtime.produceState(ModelCatalog.keptList(sessions, profile.provider), profile.provider, sessions) {
        val kept = ModelCatalog.keptList(sessions, profile.provider)
        value = kept
        if (profile.provider.isNotEmpty()) {
            val fresh = try {
                ModelCatalog.fetch(sessions, profile.provider)
            } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (_: Exception) { null }
            value = ModelCatalog.shown(fresh, kept)
        }
    }
    /** The model settings, open on a provider ("" = on the one in use), or closed (null) */
    var setup by remember { mutableStateOf<String?>(null) }
    var dropping by remember { mutableStateOf(false) }
    val inputFocus = remember { FocusRequester() }
    val words = remember(strings) {
        AttachmentWords(
            line = { strings.get(R.string.chat_attachment_line, it) },
            ask = { strings.get(R.string.chat_upload_prefill, it) },
            separator = strings.get(R.string.chat_attachment_separator),
        )
    }
    LaunchedEffect(Unit) {
        model.loadProviders()
        // the buttons under messages come from the open follow-ups; a fill-in card the agent asked for that is still open shows again
        // after the app was closed: asked here, not only when the status poll counts one (as the phone)
        if (!model.state.value.loaded) model.reload() else { model.loadFollowups(); model.refreshSecrets() }
        model.refreshAsks(force = true)   // a background job's question waiting since before the chat was shown
        runCatching { inputFocus.requestFocus() }
        while (true) {   // another device may talk to the same agent meanwhile (as on the phone, every 4 s while shown)
            delay(4_000)
            model.checkForNews()
        }
    }
    // 신고하기: the reply being reported (its row key), and what was left in the dialog per reply when it closed without sending
    var reporting by remember { mutableStateOf<String?>(null) }
    val reportDrafts = remember { androidx.compose.runtime.mutableStateMapOf<String, chat.kelo.app.chat.ReportDraft>() }
    var thanks by remember { mutableStateOf(false) }
    LaunchedEffect(thanks) { if (thanks) { delay(3_000); thanks = false } }

    val dropTarget = remember {
        object : DragAndDropTarget {
            override fun onStarted(event: DragAndDropEvent) { dropping = true }
            override fun onEnded(event: DragAndDropEvent) { dropping = false }
            override fun onExited(event: DragAndDropEvent) { dropping = false }
            override fun onEntered(event: DragAndDropEvent) { dropping = true }
            override fun onDrop(event: DragAndDropEvent): Boolean {
                dropping = false
                val t = event.awtTransferable
                if (!t.isDataFlavorSupported(DataFlavor.javaFileListFlavor)) return false
                @Suppress("UNCHECKED_CAST")
                val files = (t.getTransferData(DataFlavor.javaFileListFlavor) as? List<File>).orEmpty().filter { it.isFile }
                files.forEach(model::attach)
                return files.isNotEmpty()
            }
        }
    }

    Box(
        modifier.fillMaxSize().background(c.bg).dragAndDropTarget(
            shouldStartDragAndDrop = { e -> e.awtTransferable.isDataFlavorSupported(DataFlavor.javaFileListFlavor) },
            target = dropTarget,
        ),
    ) {
        // the composer floats over the messages: they run on under it and fade out in a scrim (composerScrim) instead of being cut off,
        // and the list keeps room at its end for the composer's measured height so the last row can always scroll above the pill
        val density = androidx.compose.ui.platform.LocalDensity.current
        var composerHeight by remember { mutableStateOf(0.dp) }
        var trayHeight by remember { mutableStateOf(0.dp) }
        // the "↓" button: the list says when it shows; it is drawn after the composer, on the card's top border (ChatOverlayRules)
        val jump = remember { chat.kelo.app.chat.JumpButtonState() }
        Box(Modifier.fillMaxSize()) {
            Messages(state, model, sessions, profile.agentName, onOpenConnector, topInset, Modifier.fillMaxSize(), onSolveCheck, onOpenTarget, focus,
                onReport = { key -> reporting = key }, bottom = composerHeight, onOpenGoals = onOpenGoals, jump = jump,
                entryFocus = entryFocus, onEntryClosed = onEntryClosed)
            Box(
                Modifier.align(Alignment.BottomCenter).fillMaxWidth()
                    .onSizeChanged { composerHeight = with(density) { it.height.toDp() } }
                    .composerScrim(c.bg),
                contentAlignment = Alignment.Center,
            ) {
                Column(Modifier.widthIn(max = 860.dp).fillMaxWidth()) {
                    Box(Modifier.onSizeChanged { trayHeight = with(density) { it.height.toDp() } }) {
                        if (state.attachments.isNotEmpty()) AttachmentTray(state.attachments, model::removeAttachment)
                    }
                    Composer(
                        input = state.input, busy = state.busy,
                        canSend = state.busy || ((state.input.isNotBlank() || state.ready.isNotEmpty()) && !state.uploading),
                        focus = inputFocus,
                        onInput = model::setInput,
                        onSend = { model.send(words = words) },
                        onStop = model::stop,
                        onAttach = { pickFiles().forEach(model::attach) },
                        onPasteFile = model::attach,
                        chipText = modelChipText(profile, state.providers, modelList),
                        onChip = { chooser = true },
                    )
                }
            }
            // on the composer card's top border at the end side, centred over 보내기 (within the composer's width in a wide window);
            // on top of the attachments while they wait
            val card = composerHeight - ComposerScrimFade - trayHeight - COMPOSER_TOP
            val lift = ChatOverlayRules.jumpLift(card.value,
                if (trayHeight > 0.dp) (composerHeight - ComposerScrimFade).value else 0f).dp
            Box(Modifier.align(Alignment.BottomCenter).widthIn(max = 860.dp).fillMaxWidth()) {
                androidx.compose.animation.AnimatedVisibility(
                    visible = jump.shown,
                    modifier = Modifier.align(Alignment.BottomEnd).padding(end = JUMP_END, bottom = lift),
                    enter = androidx.compose.animation.fadeIn(), exit = androidx.compose.animation.fadeOut(),
                ) {
                    JumpPill(jump.unread, onClick = { jump.onClick() })
                }
            }
        }
        // a new server has no model yet: the chat leads to choosing one (as on the phone)
        if (profile.modelReady == false) {
            Column(
                Modifier.align(Alignment.TopCenter).padding(top = topInset + 8.dp, start = 16.dp, end = 16.dp).widthIn(max = 560.dp).fillMaxWidth()
                    .clip(RoundedCornerShape(18.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(18.dp)).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(stringResource(R.string.model_first_title), style = MaterialTheme.typography.titleMedium, color = c.ink)
                Text(stringResource(R.string.model_first_body), style = MaterialTheme.typography.bodyMedium, color = c.muted)
                // the sheet right here: the ready services' models, and every service that can be set up (key, address, sign-in)
                chat.kelo.desktop.ui.PrimaryButton(stringResource(R.string.model_first_button), onClick = { chooser = true })
            }
        }
        // the history shown is the one kept from last time and the server cannot be reached right now
        StaleHint(state.offline && !state.busy, Modifier.align(Alignment.TopCenter).padding(top = topInset), onRetry = model::reload)
        if (thanks) Text(
            stringResource(R.string.report_thanks), color = c.bg, style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 120.dp, start = 16.dp, end = 16.dp).widthIn(max = 520.dp)
                .clip(RoundedCornerShape(8.dp)).background(c.ink).padding(horizontal = 14.dp, vertical = 10.dp),
        )
        if (dropping) {
            Box(
                Modifier.fillMaxSize().padding(12.dp).clip(RoundedCornerShape(20.dp)).background(c.accent.copy(alpha = 0.10f))
                    .border(2.dp, c.accent, RoundedCornerShape(20.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(24.dp)) {
                    Icon(KeloIcons.File, contentDescription = null, tint = c.accent, modifier = Modifier.size(40.dp))
                    Text(stringResource(R.string.desktop_drop_here, profile.agentName.ifBlank { "Kelo" }), style = MaterialTheme.typography.titleMedium, color = c.ink)
                    Text(stringResource(R.string.desktop_attach_files), style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
            }
        }
        if (chooser) {
            ModelChooser(
                sessions, profile, state.providers, currentList = modelList, onProfile = onProfile,
                onEffort = { v -> model.pickEffort(v, onProfile); chooser = false },
                onSetup = { p -> chooser = false; setup = p.orEmpty() },
                onClose = { chooser = false },
            )
        }
        reporting?.let { key ->
            val reply = chat.kelo.app.chat.ReportRules.replyText(state.items, key)
            if (reply == null) reporting = null
            else ReportDialog(
                sessions, reply = reply, asked = chat.kelo.app.chat.ReportRules.askedBefore(state.items, key), draft = reportDrafts[key] ?: chat.kelo.app.chat.ReportDraft(),
                onDraft = { reportDrafts[key] = it },
                onSent = { reportDrafts.remove(key); reporting = null; thanks = true },
                onClose = { reporting = null },
            )
        }
        setup?.let { start ->
            ModelSetup(
                sessions, profile, startProvider = start.ifEmpty { null }, onProfile = onProfile,
                onProvidersChanged = model::setProviders,
                onClose = { setup = null; model.loadProviders() },
            )
        }
        // a message the server held back for a key or password (chat.kelo.app.chat.SecretGuard): save it, send it as is, or edit it
        state.secretAsk?.let { ask ->
            SecretDialog(sessions, ask, onSaved = model::secretSaved, onSendAsIs = model::secretSendAsIs, onEdit = model::secretEdit)
        }
        // the one question before chats first go to an AI service (chat.kelo.app.chat.AiConsent)
        state.consentAsk?.let { ask ->
            ConsentDialog(ask, state.consentBusy, state.consentError, onAgree = model::consentAgree, onNotNow = model::consentNotNow)
        }
    }
}

/** The system's own file picker (several files at once). */
fun pickFiles(): List<File> {
    val dialog = java.awt.FileDialog(null as java.awt.Frame?, "", java.awt.FileDialog.LOAD)
    dialog.isMultipleMode = true
    dialog.isVisible = true
    return dialog.files?.toList().orEmpty()
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Messages(
    state: ChatState, model: ChatModel, sessions: Sessions, agentName: String, onOpenConnector: (String) -> Unit,
    top: androidx.compose.ui.unit.Dp, modifier: Modifier, onSolveCheck: (String) -> Unit,
    onOpenTarget: (chat.kelo.app.notify.OpenTarget) -> Unit, focusFlow: kotlinx.coroutines.flow.MutableStateFlow<Long?>?,
    onReport: (String) -> Unit, bottom: androidx.compose.ui.unit.Dp = 0.dp, onOpenGoals: () -> Unit = {},
    jump: chat.kelo.app.chat.JumpButtonState = remember { chat.kelo.app.chat.JumpButtonState() },
    entryFocus: kotlinx.coroutines.flow.MutableStateFlow<String?>? = null,
    onEntryClosed: (String) -> Unit = {},
) {
    val c = Kelo.colors
    val list = rememberLazyListState()
    val scope = rememberCoroutineScope()
    // the open secure entries from the server's list join the rows under the message that asked (same rules as the phone)
    // tool chips of one kind in a row fold into one chip that grows in place (ChatReducer.foldTools)
    val rows = remember(state.items, state.secrets) { chat.kelo.app.chat.ChatReducer.foldTools(chat.kelo.app.chat.ChatReducer.withPending(state.items, state.secrets)) }.asReversed()
    // the messages that start a new stretch (first shown, a new day, or TimeLabels.GAP_MINUTES after the one before) get a time line
    val timed = remember(state.items) { chat.kelo.app.chat.TimeLabels.labelled(state.items, java.time.ZoneId.systemDefault()) }
    val timeLabeler = rememberTimeLabeler()
    val clipboard = LocalClipboardManager.current
    val stepCalls = remember(sessions) { { chat.kelo.app.chat.PaymentStepCalls(sessions.requireApi()) } }
    fun stepActions(step: chat.kelo.app.chat.PaymentStep) = PaymentStepActions(
        answer = { answer, code -> model.answerPaymentStep(step.stepId, answer, code) },
        openLive = { onOpenTarget(chat.kelo.app.notify.OpenTarget.Tab(step.tab.ifEmpty { "owner" }, control = true)) },
        padImage = { runCatching { stepCalls().pad(step.stepId) }.getOrNull() },
        padTap = { x, y -> runCatching { stepCalls().tap(step.stepId, x, y) }.getOrNull() },
        padClear = { runCatching { stepCalls().clear(step.stepId) }.getOrNull() },
    )
    val last = state.items.lastOrNull()
    val streamingKey = (last as? ChatItem.Msg)?.takeIf { !it.mine && it.id == null }?.key
    LaunchedEffect(state.items.size, state.phase?.kind, last?.key) {
        // requested, not run here: a bubble that comes and goes within one frame (a message the server held back) would otherwise
        // scroll a list whose composition is still pending
        if (ChatScroll.follow(list.firstVisibleItemIndex, last is ChatItem.Msg && last.mine)) list.requestScrollToItem(0)
    }
    // questions from a background job or a scheduled run, live at the very bottom (newest lowest); a new one pulls the list down
    // like a new row does, or lights the "↓" pill's dot when the reader is up the list
    val asks = remember(state.asks, state.items) { ChatAsks.visible(state.asks, state.items) }
    val openAsks = asks.filter { it.open }.map { it.id }
    var askUnread by remember { mutableStateOf(false) }
    var askIdsBefore by remember { mutableStateOf(openAsks.toSet()) }
    LaunchedEffect(openAsks) {
        val arrived = openAsks.any { it !in askIdsBefore }
        askIdsBefore = openAsks.toSet()
        if (arrived) {
            if (ChatScroll.follow(list.firstVisibleItemIndex, false)) list.animateScrollToItem(0) else askUnread = true
        }
    }
    // a follow-up notification asked for one message: scroll to it once it is in the list (a fresh reload brings it)
    val focusId = focusFlow?.collectAsState()?.value
    var focusReloaded by remember { mutableStateOf(false) }
    LaunchedEffect(focusId, rows.size) {
        val id = focusId ?: return@LaunchedEffect
        if (!focusReloaded) model.loadFollowups()
        val i = rows.indexOfFirst { it is ChatItem.Msg && it.id == id }
        if (i >= 0) {
            list.animateScrollToItem(i + asks.size + if (state.phase != null) 1 else 0)
            focusFlow?.value = null
            focusReloaded = false
        } else if (!focusReloaded) {
            focusReloaded = true
            model.reload()
        } else if (state.loaded) {
            focusFlow?.value = null   // not among the newest messages: the chat simply opens at the bottom
            focusReloaded = false
        }
    }
    // a row in 답이 필요해요 asked for a secure entry's card: scroll to it and mark it for a moment
    val entryId = entryFocus?.collectAsState()?.value
    var entryAsked by remember { mutableStateOf(false) }
    var marked by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(entryId, rows.size) {
        val id = entryId ?: return@LaunchedEffect
        val i = rows.indexOfFirst { it is ChatItem.Secret && it.request.id == id }
        val before = asks.size + state.pinNeeds.takeLast(2).size + chat.kelo.app.chat.PaymentStepRules.visible(state.paymentSteps).size +
            if (state.phase != null) 1 else 0
        when {
            i >= 0 -> {
                list.animateScrollToItem(i + before)
                entryFocus.value = null
                entryAsked = false
                marked = id
            }
            !entryAsked -> { entryAsked = true; model.refreshSecrets() }
            else -> { kotlinx.coroutines.delay(3_000); entryFocus.value = null; entryAsked = false }
        }
    }
    // the mark goes after a moment (its own effect: a list change must not cancel it)
    LaunchedEffect(marked) {
        if (marked == null) return@LaunchedEffect
        kotlinx.coroutines.delay(1_800)
        marked = null
    }
    LaunchedEffect(list) {
        snapshotFlow { (list.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0) to list.layoutInfo.totalItemsCount }
            .map { (lastShown, total) -> total > 0 && lastShown >= total - 3 }
            .distinctUntilChanged()
            .collect { nearTop -> if (nearTop) model.loadOlder() }
    }
    // the "↓" pill: shown away from the newest row, with a dot when the assistant wrote more meanwhile
    val slop = with(androidx.compose.ui.platform.LocalDensity.current) { 24.dp.roundToPx() }
    val atBottom by remember { derivedStateOf { ChatScroll.atBottom(list.firstVisibleItemIndex, list.firstVisibleItemScrollOffset, slop) } }
    val signature = ChatScroll.replySignature(state.items)
    var seenSignature by remember { mutableLongStateOf(signature) }
    var unread by remember { mutableStateOf(false) }
    LaunchedEffect(signature, atBottom, askUnread) {
        unread = ChatScroll.unread(unread, atBottom, signature != seenSignature || askUnread)
        seenSignature = signature
        askUnread = false
    }
    val showJump = !atBottom && state.items.isNotEmpty()
    androidx.compose.runtime.SideEffect {
        jump.shown = showJump
        jump.unread = unread
        jump.onClick = { scope.launch { list.animateScrollToItem(0) } }
    }
    if (state.loaded && state.items.isEmpty() && state.phase == null && asks.isEmpty()) {
        Box(modifier.fillMaxWidth().padding(top = top).padding(24.dp), contentAlignment = Alignment.Center) {
            Text(stringResource(R.string.chat_hint, agentName.ifBlank { "Kelo" }), color = c.muted, style = MaterialTheme.typography.bodyLarge)
        }
        return
    }
    fun act(item: ChatItem, text: String, a: MsgAction) {
        when (a) {
            MsgAction.COPY -> clipboard.setText(AnnotatedString(Markdown.plain(text)))
            MsgAction.RETRY -> model.retry(item.key)
            MsgAction.EDIT -> model.setInput(text)
            MsgAction.REPORT -> onReport(item.key)
            else -> {}
        }
    }
    Box(modifier.fillMaxWidth(), contentAlignment = Alignment.TopCenter) {
        // as the phone and every other page: the messages scroll on under the agent and its name at the top (the top padding
        // starts the first message below the name); at the bottom the list runs on under the composer's scrim, and the "↓" sits on
        // the composer
        LazyColumn(
            Modifier.fillMaxSize(),
            state = list,
            reverseLayout = true,
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = top + 10.dp, bottom = maxOf(bottom, 10.dp)),
            verticalArrangement = Arrangement.spacedBy(8.dp, Alignment.Bottom),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            items(asks.asReversed(), key = { "ask:" + it.id }, contentType = { "ask" }) { ask ->
                Wide {
                    ChatAskCard(ask, onSolve = { onOpenTarget(chat.kelo.app.notify.OpenTarget.Tab(ask.approval.tab, control = true)) }) { ok, rememberSite, rememberSchedule ->
                        model.answerAsk(ask.id, ok, rememberSite = rememberSite, rememberSchedule = rememberSchedule)
                    }
                }
            }
            // payment steps that need the user, and PINs waiting for the phone: time-critical, at the very bottom
            items(state.pinNeeds.takeLast(2).asReversed(), key = { "pin:" + it.slotId }, contentType = { "pin" }) { need ->
                Wide { PinNeedCard(need) }
            }
            items(chat.kelo.app.chat.PaymentStepRules.visible(state.paymentSteps).asReversed(), key = { "step:" + it.stepId }, contentType = { "step" }) { step ->
                Wide { PaymentStepCard(step, stepActions(step)) }
            }
            state.phase?.let { p -> item(key = "status") { Wide { StatusLine(p) } } }
            items(rows, key = { it.key }, contentType = { it::class }) { item ->
                val actions = remember(item, state.items, state.busy) { MessageActions.actions(state.items, item.key, state.busy) }
                Wide {
                    when (item) {
                        is ChatItem.Msg -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            if (item.key in timed) TimeLine(timeLabeler.label(item.at))
                            if (item.mine) {
                                // the files sent with it: pictures as thumbnails, other files as chips; the text without the app's own lines
                                if (item.files.isNotEmpty()) UserFiles(item.files, sessions, Modifier.fillMaxWidth().wrapContentWidth(Alignment.End))
                                val lineWords = LocalStrings.current
                                val shown = MessageFileRules.shownText(item.text, item.files, item.autoText) { p -> lineWords.get(R.string.chat_attachment_line, p) }
                                if (shown.isNotEmpty() || item.files.isEmpty()) Bubble(shown, mine = true, actions, reaction = item.reaction) { a -> act(item, item.text, a) }
                            }
                            else if (item.briefing != null) {
                                val words = LocalStrings.current
                                BriefingCardView(
                                    item.briefing!!,
                                    onTap = { t ->
                                        when (t) {
                                            is chat.kelo.app.chat.BriefingTap.ChatAt -> focusFlow?.value = t.messageId
                                            chat.kelo.app.chat.BriefingTap.Waiting -> onOpenTarget(chat.kelo.app.notify.OpenTarget.Activity)
                                            is chat.kelo.app.chat.BriefingTap.Task -> onOpenTarget(chat.kelo.app.notify.OpenTarget.Task(t.id))
                                            chat.kelo.app.chat.BriefingTap.Goals -> onOpenGoals()
                                            is chat.kelo.app.chat.BriefingTap.Event -> {}
                                            is chat.kelo.app.chat.BriefingTap.Mail -> model.setInput(words.get(R.string.briefing_mail_ask, t.subject))
                                            is chat.kelo.app.chat.BriefingTap.Suggest -> model.setInput(t.text)
                                        }
                                    },
                                    onHours = { h, accept ->
                                        scope.launch {
                                            runCatching {
                                                sessions.requireApi().post<kotlinx.serialization.json.JsonElement>(
                                                    if (accept) chat.kelo.app.my.ContactHoursRules.acceptPath(h.id) else chat.kelo.app.my.ContactHoursRules.keepPath(h.id))
                                            }
                                        }
                                    },
                                    onLink = { url -> chat.kelo.desktop.core.Browser.open(url) },
                                )
                            } else {
                                if (item.text.isNotEmpty() || item.images.isEmpty()) Markdown.splitBubbles(item.text).forEach { part ->
                                    Bubble(part, mine = false, if (state.busy && item.key == streamingKey) emptyList() else actions) { a -> act(item, part, a) }
                                }
                                item.images.forEach { img -> ChatPicture(img, sessions) }
                                item.id?.let { id -> state.followups[id] }?.let { f -> MessageFollowups(f, state, model, sessions) }
                                item.replies?.takeIf { !state.busy && chat.kelo.app.chat.QuickReplyRules.isOpen(state.items, item.key) }?.let { r ->
                                    QuickReplies(r, onSend = { text, answers -> model.send(text, answers = answers) })
                                }
                            }
                        }
                        is ChatItem.Tool -> ToolChip(item)
                        is ChatItem.Approval -> ApprovalCard(item, onAnswer = { ok -> model.approve(item.id, ok) },
                            onKeepForSchedule = { model.approve(item.id, true, rememberSchedule = true) },
                            site = state.checks[item.id]?.site.orEmpty(), check = state.checks[item.id]?.check,
                            onSolve = { onSolveCheck(state.checks[item.id]?.tab ?: "owner") })
                        is ChatItem.Browser -> BrowserCard(item, onOpenLive = { onOpenTarget(chat.kelo.app.notify.OpenTarget.Tab.ofCard(item)) })
                        is ChatItem.CardNeeded -> CardNeededRow(item)
                        is ChatItem.PaymentAuto -> PaymentAutoRow(item)
                        // a secure entry the agent asked for (a key, a login, a card): SecretEntry.kt
                        is ChatItem.Secret -> {
                            val ring by androidx.compose.animation.animateColorAsState(
                                if (marked == item.request.id) c.accent else c.accent.copy(alpha = 0f), label = "entry-mark")
                            SecretEntryCard(
                                item.request, sessions,
                                onSaved = { answer, result -> onEntryClosed(item.request.kind); model.secretSaved(item.request, answer, result) },
                                onCancel = { onEntryClosed(item.request.kind); model.secretCancelled(item.request) },
                                onGone = { model.secretGone(item.request) },
                                modifier = Modifier.border(2.dp, ring, RoundedCornerShape(16.dp)),
                            )
                        }
                        // a location request: this computer has no place to give (a phone shares it), so only "Not now" here
                        is ChatItem.LocationAsk -> LocationAskCard(item, onNotNow = { model.refuseLocation(item.approvalId) })
                        is ChatItem.ShippingForm -> ShippingFormCard(
                            item, sessions, model, cardChosen = item.ask.formId in state.shippingCard,
                            onOpenWallet = { onOpenTarget(chat.kelo.app.notify.OpenTarget.Wallet) },
                        )
                        is ChatItem.Vault -> VaultCard(item, sessions, onSettle = { s -> model.settleCard(item.key, s) }, onNotify = { t -> model.send(t) })
                        is ChatItem.Connect -> ConnectCard(item, onOpen = onOpenConnector, sessions = sessions)
                        is ChatItem.Notice -> NoticeChip(noticeText(item.notice))
                        is ChatItem.Error -> {
                            val text = errorText(item.error)
                            ErrorBubble(text, actions) { a -> act(item, text, a) }
                        }
                    }
                }
            }
        }
    }
}

/** Keeps a row at a comfortable reading width in a wide window. */
@Composable
private fun Wide(content: @Composable () -> Unit) {
    Box(Modifier.widthIn(max = 820.dp).fillMaxWidth()) { content() }
}

@Composable
private fun noticeText(n: ChatNotice): String = when (n) {
    is ChatNotice.Fallback -> stringResource(R.string.chat_notice_fallback, modelName(n.model), n.reason)
    ChatNotice.Retry -> stringResource(R.string.chat_notice_retry)
    ChatNotice.Reconnecting -> stringResource(R.string.chat_notice_reconnecting)
    is ChatNotice.Uploaded -> stringResource(R.string.chat_notice_uploaded, n.name)
}

@Composable
private fun errorText(e: ChatError): String = when (e) {
    ChatError.StepLimit -> stringResource(R.string.chat_error_step_limit)
    is ChatError.Message -> e.text
    is ChatError.PickModel -> stringResource(R.string.chat_error_pick_model)
    ChatError.Network -> stringResource(R.string.chat_error_network)
    ChatError.TooLarge -> stringResource(R.string.chat_upload_too_large)
    is ChatError.Unsupported -> stringResource(R.string.share_unsupported, e.name)
    ChatError.ShareFailed -> stringResource(R.string.share_failed)
    ChatError.CameraDenied, ChatError.CameraMissing -> stringResource(R.string.share_failed)
}

private val BubbleShape = RoundedCornerShape(20.dp)

@Composable
private fun menuItems(actions: List<MsgAction>, onAction: (MsgAction) -> Unit): List<ContextMenuItem> {
    val copy = stringResource(R.string.msg_action_copy)
    val retry = stringResource(R.string.msg_action_retry)
    val edit = stringResource(R.string.msg_action_edit)
    val report = stringResource(R.string.msg_action_report)
    return actions.mapNotNull { a ->
        when (a) {
            MsgAction.COPY -> ContextMenuItem(copy) { onAction(a) }
            MsgAction.RETRY -> ContextMenuItem(retry) { onAction(a) }
            MsgAction.EDIT -> ContextMenuItem(edit) { onAction(a) }
            MsgAction.REPORT -> ContextMenuItem(report) { onAction(a) }
            else -> null
        }
    }
}

/** A message bubble: the user's on the right, the agent's (Markdown) on the left. Text can be selected; right-click for the menu. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Bubble(text: String, mine: Boolean, actions: List<MsgAction>, reaction: String = "", onAction: (MsgAction) -> Unit) {
    val c = Kelo.colors
    val items = menuItems(actions, onAction)
    Row(Modifier.fillMaxWidth().padding(start = if (mine) 64.dp else 0.dp, end = if (mine) 0.dp else 64.dp), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        // the user's message: the emoji Kelo put on it (Reactions) hangs over the bubble's bottom edge near its end corner
        BubbleWithReaction(reaction) {
            ContextMenuArea(items = { items }) {
                Box(Modifier.clip(BubbleShape).background(if (mine) c.mine else c.bubble).padding(horizontal = 15.dp, vertical = 11.dp)) {
                    SelectionContainer {
                        if (mine) Text(text, color = c.ink, style = MaterialTheme.typography.bodyLarge)
                        else MarkdownView(text)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ErrorBubble(text: String, actions: List<MsgAction>, onAction: (MsgAction) -> Unit) {
    val c = Kelo.colors
    val items = menuItems(actions, onAction)
    Row(Modifier.fillMaxWidth().padding(end = 64.dp)) {
        ContextMenuArea(items = { items }) {
            Column(Modifier.clip(BubbleShape).background(c.dangerSoft).padding(horizontal = 15.dp, vertical = 11.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                SelectionContainer { Text(text, style = MaterialTheme.typography.bodyLarge, color = c.dangerInk) }
                if (MsgAction.RETRY in actions) {
                    Text(stringResource(R.string.msg_action_retry), color = c.dangerInk, style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.clip(RoundedCornerShape(8.dp)).border(1.dp, c.dangerInk.copy(alpha = 0.4f), RoundedCornerShape(8.dp))
                            .clickable { onAction(MsgAction.RETRY) }.padding(horizontal = 10.dp, vertical = 4.dp))
                }
            }
        }
    }
}

/** "생각하는 중… 12s" with three breathing dots, while a reply is in progress. */
@Composable
private fun StatusLine(p: Phase) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(p.since) {
        while (true) { now = System.currentTimeMillis(); delay(1000) }
    }
    val phase = stringResource(
        when (p.kind) {
            PhaseKind.WAITING -> R.string.chat_phase_waiting
            PhaseKind.THINKING -> if (ChatReducer.longThinking(p, now)) R.string.chat_phase_thinking_long else R.string.chat_phase_thinking
            PhaseKind.WRITING -> R.string.chat_phase_writing
            PhaseKind.TOOL -> R.string.chat_phase_tool
            PhaseKind.APPROVAL -> R.string.chat_phase_approval
            PhaseKind.SWITCHING -> R.string.chat_phase_switching
        },
    )
    val text = when {
        p.label.isNullOrEmpty() -> stringResource(R.string.chat_status, phase)
        p.kind == PhaseKind.TOOL -> stringResource(R.string.chat_status, p.label)
        p.kind == PhaseKind.SWITCHING -> stringResource(R.string.chat_status_label, phase, modelName(p.label))
        else -> stringResource(R.string.chat_status_label, phase, p.label)
    }
    val seconds = ((now - p.since) / 1000).coerceAtLeast(0).toInt()
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Dots()
        Text(text, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
        Text(stringResource(R.string.chat_seconds, seconds), style = MaterialTheme.typography.bodySmall, color = c.muted)
    }
}

@Composable
private fun Dots() {
    val t = rememberInfiniteTransition(label = "dots")
    Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
        repeat(3) { i ->
            val a by t.animateFloat(0.25f, 1f, infiniteRepeatable(tween(600, delayMillis = i * 150), RepeatMode.Reverse), label = "dot$i")
            Box(Modifier.size(6.dp).alpha(a).background(Kelo.colors.muted, CircleShape))
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AttachmentTray(items: List<Attachment>, onRemove: (String) -> Unit) {
    FlowRow(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        items.forEach { a -> AttachmentChip(a) { onRemove(a.id) } }
    }
}

@Composable
private fun AttachmentChip(a: Attachment, onRemove: () -> Unit) {
    val c = Kelo.colors
    val failed = a.state == AttachState.FAILED
    Column(
        Modifier.widthIn(min = 160.dp, max = 300.dp).clip(RoundedCornerShape(12.dp)).background(if (failed) c.dangerSoft else c.card)
            .border(1.dp, c.line, RoundedCornerShape(12.dp)).padding(start = 10.dp, end = 2.dp, top = 4.dp, bottom = 6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(KeloIcons.File, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
            Text(a.name, style = MaterialTheme.typography.bodySmall, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            IconButton(onClick = onRemove, modifier = Modifier.size(28.dp)) {
                Icon(KeloIcons.Close, contentDescription = stringResource(R.string.attach_remove), tint = c.muted, modifier = Modifier.size(14.dp))
            }
        }
        when (a.state) {
            AttachState.UPLOADING -> a.progress?.let { p -> LinearProgressIndicator(progress = { p }, color = c.accent, trackColor = c.bubble, modifier = Modifier.fillMaxWidth().padding(end = 8.dp)) }
                ?: Text(stringResource(R.string.attach_preparing), style = MaterialTheme.typography.labelSmall, color = c.muted)
            AttachState.DONE -> Text(stringResource(R.string.attach_done), style = MaterialTheme.typography.labelSmall, color = c.success)
            AttachState.FAILED -> Text(
                when (val e = a.error) {
                    is AttachError.Server -> e.text
                    AttachError.TooLarge -> stringResource(R.string.chat_upload_too_large)
                    AttachError.Network -> stringResource(R.string.attach_error_network)
                    else -> stringResource(R.string.attach_error_unreadable)
                },
                style = MaterialTheme.typography.labelSmall, color = c.dangerInk,
            )
        }
    }
}

/** The model chip inside the composer: "<model> <thinking level>"; opens the model chooser. */
@Composable
private fun ModelChip(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Row(
        modifier.clip(RoundedCornerShape(99.dp)).clickable(onClick = onClick).heightIn(min = 34.dp).padding(start = 10.dp, end = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(text, color = c.ink, fontSize = 13.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
        Icon(KeloIcons.ChevronRight, contentDescription = stringResource(R.string.chat_model_settings), tint = c.muted,
            modifier = Modifier.size(16.dp).rotate(if (androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl) -90f else 90f))
    }
}

/**
 * One rounded box: the text on top, under it "+", the model chip and 보내기 (Stop while a reply runs). Enter sends, Shift+Enter
 * adds a line.
 */
@Composable
private fun Composer(
    input: String, busy: Boolean, canSend: Boolean, focus: FocusRequester,
    onInput: (String) -> Unit, onSend: () -> Unit, onStop: () -> Unit, onAttach: () -> Unit,
    chipText: String, onChip: () -> Unit, onPasteFile: (java.io.File) -> Unit = {},
) {
    val c = Kelo.colors
    var focused by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = COMPOSER_TOP, bottom = 12.dp)
            .clip(RoundedCornerShape(24.dp)).background(c.card).border(1.dp, if (focused) c.accent else c.line, RoundedCornerShape(24.dp)),
    ) {
        var field by remember { mutableStateOf(TextFieldValue(input, TextRange(input.length))) }
        val shown = if (field.text == input) field else TextFieldValue(input, TextRange(input.length)).also { field = it }
        OutlinedTextField(
            value = shown,
            onValueChange = { v -> field = v; if (v.text != input) onInput(v.text) },
            // a little more room above the first line: the "↓" rests on the card's top-end strip and never covers the text
            modifier = Modifier.padding(top = ChatOverlayRules.fieldTopExtra(16f).dp)
                .fillMaxWidth().heightIn(min = 52.dp).focusRequester(focus).onFocusChanged { focused = it.isFocused }
                .onPreviewKeyEvent { e ->
                    // Ctrl+V / Cmd+V with a picture on the clipboard: it is attached; text still pastes as text
                    if (e.type == KeyEventType.KeyDown && e.key == Key.V && (e.isCtrlPressed || e.isMetaPressed) && !e.isShiftPressed) {
                        val pictures = ClipboardImages.fromClipboard()
                        if (pictures != null) { pictures.forEach(onPasteFile); return@onPreviewKeyEvent true }
                    }
                    if (e.type == KeyEventType.KeyDown && (e.key == Key.Enter || e.key == Key.NumPadEnter)) {
                        if (e.isShiftPressed) {
                            val sel = field.selection
                            val text = field.text.replaceRange(sel.min, sel.max, "\n")
                            field = TextFieldValue(text, TextRange(sel.min + 1))
                            onInput(text)
                        } else if (!busy && canSend) onSend()
                        true
                    } else false
                },
            placeholder = { Text(stringResource(R.string.chat_input_placeholder), color = c.muted) },
            maxLines = 8,
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                focusedBorderColor = Color.Transparent, unfocusedBorderColor = Color.Transparent, cursorColor = c.accent,
                focusedTextColor = c.ink, unfocusedTextColor = c.ink,
            ),
        )
        Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 8.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            IconButton(onClick = onAttach) { Icon(KeloIcons.Plus, contentDescription = stringResource(R.string.desktop_attach_files), tint = c.muted) }
            // the shortcut hint only where there is room for it (a narrow window keeps the chip and the button whole)
            androidx.compose.foundation.layout.BoxWithConstraints(Modifier.weight(1f).padding(horizontal = 4.dp)) {
                if (maxWidth > 220.dp) Text(stringResource(R.string.desktop_enter_hint), style = MaterialTheme.typography.labelSmall, color = c.muted, maxLines = 2,
                    overflow = TextOverflow.Ellipsis)
            }
            ModelChip(chipText, onChip, Modifier.widthIn(max = 260.dp))
            IconButton(
                onClick = if (busy) onStop else onSend, enabled = canSend,
                colors = IconButtonDefaults.iconButtonColors(containerColor = c.ink, contentColor = c.bg, disabledContainerColor = c.disabledFill, disabledContentColor = c.disabledInk),
            ) {
                Icon(if (busy) StopIcon else KeloIcons.Send, contentDescription = stringResource(if (busy) R.string.chat_stop else R.string.chat_send))
            }
        }
    }
}

/** The composer's own space above its card (Composer: padding top). */
private val COMPOSER_TOP = 6.dp
/** From the composer's end edge to the "↓" button's: the card's 16dp margin plus 14dp, which centres it over 보내기. */
private val JUMP_END = 30.dp

private val StopIcon: ImageVector by lazy {
    ImageVector.Builder("stop", 24.dp, 24.dp, 24f, 24f).apply {
        addPath(androidx.compose.ui.graphics.vector.PathParser().parsePathString("M8 7h8q1 0 1 1v8q0 1-1 1H8q-1 0-1-1V8q0-1 1-1Z").toNodes(), fill = SolidColor(Color.Black))
    }.build()
}

/**
 * A reply waits for the user's place (LOCATION REQUEST). This computer cannot read one (the phone app shares it), so the card says
 * why, points to the phone and offers "Not now" (POST {"allowed": false}); an ended request leaves one line, as on the phone.
 */
@Composable
private fun LocationAskCard(item: ChatItem.LocationAsk, onNotNow: () -> Unit) {
    val c = Kelo.colors
    CardBox {
        val end = when (item.state) {
            ChatItem.LocationAsk.ALLOWED -> R.string.location_ask_shared
            ChatItem.LocationAsk.REFUSED -> R.string.location_ask_refused
            ChatItem.LocationAsk.WAITING, ChatItem.LocationAsk.SENDING -> null
            else -> R.string.location_ask_expired
        }
        if (end != null) {
            ResultLine(if (item.state == ChatItem.LocationAsk.ALLOWED) KeloIcons.Check else KeloIcons.Close, stringResource(end),
                muted = item.state != ChatItem.LocationAsk.ALLOWED)
            return@CardBox
        }
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(KeloIcons.MapPin, contentDescription = null, tint = c.accent)
            Text(stringResource(R.string.location_ask_title), style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f))
        }
        if (item.reason.isNotBlank()) Text(item.reason, style = MaterialTheme.typography.bodyMedium, color = c.ink)
        Text(stringResource(R.string.desktop_location_on_phone), style = MaterialTheme.typography.bodySmall, color = c.muted)
        SecondaryButton(stringResource(R.string.location_ask_not_now), enabled = item.state == ChatItem.LocationAsk.WAITING,
            onClick = onNotNow, modifier = Modifier.fillMaxWidth())
    }
}
