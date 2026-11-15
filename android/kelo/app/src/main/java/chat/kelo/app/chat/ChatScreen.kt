package chat.kelo.app.chat

import android.content.ActivityNotFoundException
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.ripple
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.IntSize
import androidx.lifecycle.compose.LifecycleEventEffect
import chat.kelo.app.shell.HideShellChrome
import chat.kelo.app.shell.ShellChrome
import chat.kelo.app.shell.bottomBarHeight
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.theme.Motion
import chat.kelo.app.ui.theme.pressShrink
import kotlinx.coroutines.launch
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.result.PickVisualMediaRequest
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import chat.kelo.app.shell.ChromeRules
import java.io.File
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.content.contentReceiver
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.input.TextFieldLineLimits
import androidx.compose.foundation.text.input.placeCursorAtEnd
import androidx.compose.foundation.text.input.rememberTextFieldState
import androidx.compose.runtime.snapshotFlow
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.chat.vault.CardBox
import chat.kelo.app.chat.vault.SecureCard
import chat.kelo.app.core.Profile
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.StaleHint
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.emphasized
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.shell.OnTabReselect
import chat.kelo.app.shell.Routes
import chat.kelo.app.shell.headerHeight
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map

/** At most this many photos at once from the photo picker. */
private const val MAX_PHOTOS = 5
/** The FileProvider (manifest) the camera app writes its photo through: the package name plus this. */
private const val FILES_AUTHORITY = ".files"

/** The documents the agent can read (web: accept=".pdf,.docx,.xlsx,.xlsm"). */
private val DOCUMENT_TYPES = arrayOf(
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroEnabled.12",
    "video/*",
    "audio/*",
)

/**
 * The 채팅 tab. [onOpenConnector] opens the Connectors tab at a connector, for the card that appears when the
 * assistant asks the user to connect a service.
 *
 * While the keyboard is up the tab bar steps aside and the composer rides on the keyboard (the window insets move it, frame by
 * frame); when the keyboard goes away the field lets go of its focus. Text shared from another app lands in the draft, a shared
 * document is uploaded like the "+" button does.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ChatScreen(
    profile: Profile, onProfile: (Profile) -> Unit, onOpenConnector: (String) -> Unit = {}, onOpenBrowser: (ChatItem.Browser) -> Unit = {},
    onOpenCardForm: () -> Unit = {},
    /** The goals tab (a goal in the morning briefing) */
    onOpenGoals: () -> Unit = {},
) {
    val container = LocalAppContainer.current
    val sessions = container.sessions
    val vm: ChatViewModel = viewModel { ChatViewModel(sessions, container.store, container.agentActivity, container.replyWatch) }
    val state by vm.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val haptics = rememberHaptics()
    var sheet by remember { mutableStateOf(false) }
    var chooser by remember { mutableStateOf(false) }
    // the current provider's model list: the chip's readable name and the chooser's first section. The last good one (kept on the
    // phone) shows at once, so the chip never changes its wording when the fresh list arrives (see ModelCatalog)
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
    var askNotify by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(vm) {
        // the buttons under messages come from the open follow-ups; a fill-in card the agent asked for (a key, a login, a card) that
        // is still open shows again after the app was closed: asked here, not only when the status poll counts one
        if (!vm.state.value.loaded) vm.reload() else { vm.loadFollowups(); vm.refreshSecrets() }
        vm.loadProviders()
        vm.refreshAsks(force = true)   // a background job's question waiting since before the chat was shown
    }
    LifecycleEventEffect(androidx.lifecycle.Lifecycle.Event.ON_RESUME) {
        if (vm.state.value.loaded) { vm.loadFollowups(); vm.refreshSecrets() }
        vm.refreshAsks(force = true)   // e.g. back from an approval notification, or from the 작업 page
    }
    LaunchedEffect(vm, haptics) {
        vm.events.collect { e ->
            when (e) {
                ChatEvent.SENT -> {
                    haptics.tick()
                    // after the first message: shall Kelo say when a reply is ready? (once per phone)
                    val prefs = container.notifyPrefs
                    if (chat.kelo.app.notify.AskRules.shouldAsk(prefs.askedPermission, container.notifier.enabled())) askNotify = true
                    prefs.askedPermission = true
                }
                ChatEvent.REPLY_DONE -> haptics.confirm()
                ChatEvent.ERROR -> haptics.reject()
                ChatEvent.PAYMENT_AUTO -> haptics.confirm()
            }
        }
    }
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) { vm.saveDraftNow() }
    // Another phone may have talked to the same assistant: keep this screen in step while it is idle and in view.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                vm.checkForNews()
                delay(4000)
            }
        }
    }
    // something shared from another app (kept until now if it came before signing in)
    val shared by container.shareInbox.pending.collectAsStateWithLifecycle()
    LaunchedEffect(shared) {
        val s = shared ?: return@LaunchedEffect
        if (container.shareInbox.take(s)) vm.receive(s)
    }

    // "+": a photo (the system picker, no permission), the camera, or a file; each goes into the tray and uploads at once
    val resolver = context.contentResolver
    var attachSheet by remember { mutableStateOf(false) }
    val photoLabel = stringResource(R.string.attach_photo)
    val photos = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia(MAX_PHOTOS)) { uris ->
        uris.forEach { uri -> vm.attach(image = true, label = photoLabel) { AttachmentReader.photo(resolver, uri, ChatViewModel.MAX_UPLOAD) } }
    }
    val files = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            val label = AttachmentReader.displayName(resolver, uri).first ?: context.getString(R.string.attach_file)
            vm.attach(image = false, label = label) { AttachmentReader.document(resolver, uri, ChatViewModel.MAX_UPLOAD) }
        }
    }
    // the photo the camera app writes (kept across the activity being recreated while the camera is open)
    var capture by rememberSaveable { mutableStateOf<String?>(null) }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val path = capture ?: return@rememberLauncherForActivityResult
        capture = null
        val f = File(path)
        if (!ok || !f.exists() || f.length() == 0L) { f.delete(); return@rememberLauncherForActivityResult }
        val uri = FileProvider.getUriForFile(context, context.packageName + FILES_AUTHORITY, f)
        vm.attach(image = true, label = photoLabel) {
            try { AttachmentReader.photo(resolver, uri, ChatViewModel.MAX_UPLOAD) } finally { f.delete() }
        }
    }
    fun openCamera() {
        val dir = File(context.cacheDir, "camera").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }   // a photo from a camera visit that never came back
        val f = File(dir, "capture-" + System.currentTimeMillis() + ".jpg")
        capture = f.path
        try {
            camera.launch(FileProvider.getUriForFile(context, context.packageName + FILES_AUTHORITY, f))
        } catch (_: ActivityNotFoundException) {
            capture = null
            vm.showError(ChatError.CameraMissing)
        }
    }
    val cameraPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) openCamera() else vm.showError(ChatError.CameraDenied)
    }
    fun pick(source: AttachSource) {
        attachSheet = false
        try {
            when (source) {
                AttachSource.PHOTO -> photos.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                AttachSource.FILE -> files.launch(DOCUMENT_TYPES)
                // the app holds the camera permission (QR scan), so the camera app may only be asked once it is granted
                AttachSource.CAMERA ->
                    if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) openCamera()
                    else cameraPermission.launch(Manifest.permission.CAMERA)
            }
        } catch (_: ActivityNotFoundException) {
        }
    }
    val words = AttachmentWords(
        line = { name -> context.getString(R.string.chat_attachment_line, name) },
        ask = { names -> context.getString(R.string.chat_upload_prefill, names) },
        separator = stringResource(R.string.chat_attachment_separator),
    )

    // the keyboard: the tab bar steps aside while it is up for the composer, and the field lets go of its focus when it goes.
    // Only while the composer has focus: a keyboard left over from the screen before (pairing) never keeps the tab bar away.
    val imeVisible = WindowInsets.isImeVisible
    var composerFocused by remember { mutableStateOf(false) }
    HideShellChrome(ShellChrome.KEY_KEYBOARD, ChromeRules.keyboardHidesBar(imeVisible, composerFocused))
    val focus = LocalFocusManager.current
    LaunchedEffect(imeVisible) { if (!imeVisible) focus.clearFocus() }
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) { focus.clearFocus() }

    // 수정해서 다시 보내기: the text goes into the composer, which takes the focus (and the keyboard comes up)
    val composerFocus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    var editTick by remember { mutableStateOf(0) }
    LaunchedEffect(editTick) {
        if (editTick > 0) {
            runCatching { composerFocus.requestFocus() }
            keyboard?.show()
        }
    }
    // 텍스트 선택: the message on a page of its own
    var selecting by remember { mutableStateOf<String?>(null) }
    // 신고하기: the reply being reported (its row key), and what was left in the sheet per reply when it closed without sending
    var reporting by remember { mutableStateOf<String?>(null) }
    val reportDrafts = remember { mutableStateMapOf<String, ReportDraft>() }
    val snackbar = remember { SnackbarHostState() }
    val reportScope = rememberCoroutineScope()
    val reportThanks = stringResource(R.string.report_thanks)
    val onMessageAction: (ChatItem, String, MsgAction) -> Unit = { item, text, action ->
        when (action) {
            MsgAction.SHARE -> shareText(context, text)
            MsgAction.SELECT -> { focus.clearFocus(); selecting = text }
            MsgAction.RETRY -> vm.retry(item.key)
            MsgAction.EDIT -> { vm.setInput(text); editTick++ }
            MsgAction.REPORT -> { focus.clearFocus(); reporting = item.key }
            MsgAction.COPY -> {}
        }
    }

    // The page takes the whole window and the tab bar floats over its bottom: keep the composer above whichever is taller, the
    // tab bar or the keyboard. Done with window insets in layout, so the keyboard's animation moves it smoothly without
    // recomposing the chat.
    val bar = bottomBarHeight()
    // the composer floats over the messages: they run on under it and fade out in a scrim (ComposerScrim) instead of being cut off,
    // and the list keeps room at its end for the composer's measured height so the last row can always scroll above the pill
    val density = LocalDensity.current
    var composerHeight by remember { mutableStateOf(0.dp) }
    var trayHeight by remember { mutableStateOf(0.dp) }
    // the "↓" button: the list says when it shows; it is drawn after the composer, on the card's top border (ChatOverlayRules)
    val jump = remember { JumpButtonState() }
    Box(Modifier.fillMaxSize()) {
        Box(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.ime.union(WindowInsets(bottom = bar)))) {
            Messages(state, profile.agentName, vm, onOpenConnector, onMessageAction, Modifier.fillMaxSize(), onOpenBrowser, bottom = composerHeight,
                onOpenCardForm = onOpenCardForm, onOpenGoals = onOpenGoals, jump = jump)
            Column(
                Modifier.align(Alignment.BottomCenter).fillMaxWidth()
                    .onSizeChanged { composerHeight = with(density) { it.height.toDp() } }   // the pill (and tray) and the fade above it
                    .composerScrim(Kelo.colors.bg),
            ) {
                Box(Modifier.onSizeChanged { trayHeight = with(density) { it.height.toDp() } }) {
                    AttachmentTray(state.attachments, onRemove = vm::removeAttachment)
                }
                Composer(
                    input = state.input, busy = state.busy,
                    canSend = state.busy || (!state.uploading && (state.input.isNotBlank() || state.ready.isNotEmpty())),
                    focusRequester = composerFocus, onFocus = { composerFocused = it },
                    onInput = vm::setInput, onSend = { vm.send(words = words) }, onStop = vm::stop,
                    onAttach = { attachSheet = true },
                    chipText = modelChipText(profile, state.providers, modelList), onChip = { focus.clearFocus(); chooser = true },
                    // a picture pasted or tapped in from the keyboard: the same way as one picked with "+"
                    onImages = { uris -> uris.take(MAX_PHOTOS).forEach { uri ->
                        vm.attach(image = true, label = photoLabel) { AttachmentReader.photo(resolver, uri, ChatViewModel.MAX_UPLOAD) } } },
                )
            }
            // on the composer card's top border at the end side, centred over 보내기; on top of the attachments while they wait
            val card = composerHeight - ComposerScrimFade - trayHeight - COMPOSER_TOP
            val lift = ChatOverlayRules.jumpLift(card.value, if (trayHeight > 0.dp) (composerHeight - ComposerScrimFade).value else 0f).dp
            AnimatedVisibility(
                visible = jump.shown,
                modifier = Modifier.align(Alignment.BottomEnd).padding(end = JUMP_END, bottom = lift),
                enter = fadeIn(Motion.small(LocalReduceMotion.current)) + scaleIn(Motion.small(LocalReduceMotion.current), initialScale = 0.8f),
                exit = fadeOut(Motion.small(LocalReduceMotion.current)) + scaleOut(Motion.small(LocalReduceMotion.current), targetScale = 0.8f),
            ) {
                JumpPill(jump.unread, onClick = { jump.onClick() })
            }
        }
        // the history shown is the one kept from last time and the server cannot be reached right now
        StaleHint(state.offline && !state.busy, Modifier.align(Alignment.TopCenter).padding(top = headerHeight()), onRetry = vm::reload)
        val reduce = LocalReduceMotion.current
        AnimatedVisibility(
            visible = selecting != null,
            enter = fadeIn(Motion.small(reduce)) + slideInVertically(Motion.enter(reduce)) { it / 8 },
            exit = fadeOut(Motion.small(reduce)) + slideOutVertically(Motion.exit(reduce)) { it / 8 },
        ) {
            // the text stays while the page slides away
            var shown by remember { mutableStateOf("") }
            selecting?.let { shown = it }
            SelectTextPage(shown, onClose = { selecting = null })
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).windowInsetsPadding(WindowInsets.ime.union(WindowInsets(bottom = bar))).padding(bottom = 72.dp))
    }
    reporting?.let { key ->
        val reply = ReportRules.replyText(state.items, key)
        if (reply == null) {
            reporting = null
        } else {
            ReportSheet(
                reply = reply, asked = ReportRules.askedBefore(state.items, key), draft = reportDrafts[key] ?: ReportDraft(),
                onDraft = { reportDrafts[key] = it },
                onSent = {
                    reportDrafts.remove(key)
                    reporting = null
                    reportScope.launch { snackbar.showSnackbar(reportThanks) }
                },
                onClose = { reporting = null },
            )
        }
    }
    if (chooser) ModelChooser(
        profile, state.providers, modelList, onProfile = onProfile,
        onEffort = { vm.pickEffort(it, onProfile) },
        onMore = { chooser = false; sheet = true },
        onClose = { chooser = false },
    )
    if (sheet) ModelSheet(profile, onSaved = onProfile, onClose = { sheet = false })
    if (attachSheet) AttachSheet(onPick = ::pick, onClose = { attachSheet = false })
    // a message held back for a key (the secret sheet below) was not sent: no question over the sheet, it waits for the next message
    val heldBack = state.secretAsk != null || state.consentAsk != null
    LaunchedEffect(heldBack) {
        if (heldBack && askNotify) { askNotify = false; container.notifyPrefs.askedPermission = false }
    }
    if (askNotify && !heldBack) chat.kelo.app.notify.NotificationAskDialog(profile.agentName, onDone = { askNotify = false })
    // a message the server held back for a key or password (SecretGuard): save it, send it as is, or edit it
    state.secretAsk?.let { ask ->
        SecretSheet(
            ask,
            onSaved = { value, reference -> vm.secretSaved(value, reference); haptics.confirm() },
            onCard = { vm.secretToCard(container.secretHandoff); onOpenCardForm() },
            onSendAsIs = vm::secretSendAsIs,
            onEdit = vm::secretEdit,
        )
    }
    // the one question before chats first go to an AI service (AiConsent): Agree sends the message, Not now keeps it here
    state.consentAsk?.let { ask ->
        val consentBusy by vm.consentBusy.collectAsStateWithLifecycle()
        val consentError by vm.consentError.collectAsStateWithLifecycle()
        ConsentSheet(ask, consentBusy, consentError, onAgree = vm::consentAgree, onNotNow = vm::consentNotNow)
    }
    // the wallet's card form kept a card number from such a message: its reference goes into the composer
    val cardKept by container.secretHandoff.saved.collectAsStateWithLifecycle()
    val cardReference = stringResource(R.string.secret_saved_ref, stringResource(R.string.secret_use_card))
    LaunchedEffect(cardKept) {
        if (cardKept && container.secretHandoff.takeSaved()) vm.secretCardSaved(cardReference)
    }
}

@Composable
private fun Messages(
    state: ChatState, agentName: String, vm: ChatViewModel, onOpenConnector: (String) -> Unit,
    onAction: (ChatItem, String, MsgAction) -> Unit, modifier: Modifier, onOpenBrowser: (ChatItem.Browser) -> Unit = {},
    bottom: Dp = 0.dp,
    /** The wallet's card form (an address question answered with "Add a card") */
    onOpenCardForm: () -> Unit = {},
    onOpenGoals: () -> Unit = {},
    /** The "↓" button, drawn by the screen over the composer: this list tells it when to show and where it leads */
    jump: JumpButtonState = remember { JumpButtonState() },
) {
    val list = rememberLazyListState()
    // the open secure entries from the server's list join the rows under the message that asked (a reload, another phone)
    // tool chips of one kind in a row fold into one chip that grows in place (ChatReducer.foldTools)
    val rows = remember(state.items, state.secrets) { ChatReducer.foldTools(ChatReducer.withPending(state.items, state.secrets)) }.asReversed()
    // the messages that start a new stretch (first shown, a new day, or TimeLabels.GAP_MINUTES after the one before) get a time line
    val timed = remember(state.items) { TimeLabels.labelled(state.items, java.time.ZoneId.systemDefault()) }
    val timeLabeler = rememberTimeLabeler()
    val top = headerHeight()
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    val container = LocalAppContainer.current
    val reduce = LocalReduceMotion.current
    // an end-to-end card goes from this phone only after the owner's check: decrypt, send to the approval's slot, wipe
    val sessions = LocalAppContainer.current.sessions
    val cardAuth = chat.kelo.app.wallet.rememberCardAuthenticator()
    val payTitle = stringResource(R.string.wallet_auth_pay_title)
    val paySubtitle = stringResource(R.string.wallet_auth_pay_subtitle)
    fun cardSender(approvalId: String, cardId: String): suspend () -> chat.kelo.app.wallet.CardSendError? = {
        chat.kelo.app.wallet.deliverCard(chat.kelo.app.wallet.WalletCalls(sessions.requireApi()), cardAuth, approvalId, cardId, payTitle, paySubtitle)
    }
    // a payment PIN goes from this phone the same way: the owner's check, open, send once to the payment's slot, wipe
    val pinTitle = stringResource(R.string.pin_auth_title)
    val pinSubtitle = stringResource(R.string.pin_auth_subtitle)
    fun pinSender(slotId: String, pinId: String): suspend () -> chat.kelo.app.wallet.CardSendError? = {
        chat.kelo.app.wallet.releasePin(sessions.requireApi(), cardAuth, slotId, pinId, pinTitle, pinSubtitle)
    }
    LaunchedEffect(state.pinNeeds) {
        state.pinNeeds.filter { it.send == CardSend.Idle && vm.takePinOnAllow(it.slotId) }.forEach { n -> vm.releasePin(n.slotId, pinSender(n.slotId, n.pinId)) }
    }
    val stepCalls = remember(sessions) { { PaymentStepCalls(sessions.requireApi()) } }
    fun stepActions(step: PaymentStep) = PaymentStepActions(
        answer = { answer, code -> haptics.tick(); vm.answerPaymentStep(step.stepId, answer, code) },
        openLive = { container.openRequests.value = chat.kelo.app.notify.OpenTarget.Tab(step.tab.ifEmpty { "owner" }, control = true) },
        padImage = { runCatching { stepCalls().pad(step.stepId) }.getOrNull() },
        padTap = { x, y -> haptics.tick(); runCatching { stepCalls().tap(step.stepId, x, y) }.getOrNull() },
        padClear = { runCatching { stepCalls().clear(step.stepId) }.getOrNull() },
    )
    OnTabReselect(Routes.CHAT) { list.animateScrollToItem(0) }   // newest first: the list is drawn from the bottom up

    // follow the newest row, unless the user scrolled up to read older ones
    val last = state.items.lastOrNull()
    // the reply being written right now: the newest row, from the assistant, made in this session (not history)
    val streamingKey = (last as? ChatItem.Msg)?.takeIf { !it.mine && it.id == null }?.key
    LaunchedEffect(state.items.size, state.phase?.kind, last?.key) {
        if (ChatScroll.follow(list.firstVisibleItemIndex, last is ChatItem.Msg && last.mine)) list.scrollToItem(0)
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
    val focusFlow = LocalAppContainer.current.chatFocus
    val focus by focusFlow.collectAsStateWithLifecycle()
    var focusReloaded by remember { mutableStateOf(false) }
    LaunchedEffect(focus, rows.size) {
        val id = focus ?: return@LaunchedEffect
        if (!focusReloaded) vm.loadFollowups()
        val i = rows.indexOfFirst { it is ChatItem.Msg && it.id == id }
        if (i >= 0) {
            list.animateScrollToItem(i + if (state.phase != null) 1 else 0)
            focusFlow.value = null
            focusReloaded = false
        } else if (!focusReloaded) {
            focusReloaded = true
            vm.reload()
        } else if (state.loaded) {
            focusFlow.value = null   // not among the newest messages: the chat simply opens at the bottom
            focusReloaded = false
        }
    }
    // a row in 답이 필요해요 asked for a secure entry's card: scroll to it and mark it for a moment
    val entryFlow = LocalAppContainer.current.chatEntryFocus
    val entryFocus by entryFlow.collectAsStateWithLifecycle()
    var entryAsked by remember { mutableStateOf(false) }
    var marked by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(entryFocus, rows.size) {
        val id = entryFocus ?: return@LaunchedEffect
        val i = rows.indexOfFirst { it is ChatItem.Secret && it.request.id == id }
        val before = asks.size + state.pinNeeds.takeLast(2).size + PaymentStepRules.visible(state.paymentSteps).size + if (state.phase != null) 1 else 0
        when {
            i >= 0 -> {
                list.animateScrollToItem(i + before)
                entryFlow.value = null
                entryAsked = false
                marked = id
            }
            !entryAsked -> { entryAsked = true; vm.refreshSecrets() }
            else -> { delay(ENTRY_WAIT_MS); entryFlow.value = null; entryAsked = false }   // gone meanwhile: the chat stays where it is
        }
    }
    // the mark goes after a moment (its own effect: a list change must not cancel it)
    LaunchedEffect(marked) {
        if (marked == null) return@LaunchedEffect
        delay(ENTRY_MARK_MS)
        marked = null
    }
    // older messages when the top is reached
    LaunchedEffect(list) {
        snapshotFlow { (list.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0) to list.layoutInfo.totalItemsCount }
            .map { (lastShown, total) -> total > 0 && lastShown >= total - 3 }
            .distinctUntilChanged()
            .collect { nearTop -> if (nearTop) vm.loadOlder() }
    }
    // the "↓" pill: shown away from the newest row, with a dot when the assistant wrote more meanwhile
    val slop = with(LocalDensity.current) { 24.dp.roundToPx() }
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
    SideEffect {
        jump.shown = showJump
        jump.unread = unread
        jump.onClick = {
            haptics.tick()
            scope.launch { if (reduce) list.scrollToItem(0) else list.animateScrollToItem(0) }
        }
    }

    if (state.loaded && state.items.isEmpty() && state.phase == null && asks.isEmpty()) {
        Box(modifier.fillMaxWidth().padding(top = top).padding(24.dp), contentAlignment = Alignment.Center) {
            Text(stringResource(R.string.chat_hint, agentName), color = Kelo.colors.muted, style = MaterialTheme.typography.bodyLarge)
        }
        return
    }
    Box(modifier.fillMaxWidth()) {
        // like every other page: the messages scroll on under the floating agent and its name tag (only the shell's short status-bar
        // fade above them); the top padding starts the first message below the tag. At the bottom the list runs on under the
        // composer's scrim, and the "↓" sits on the composer (ChatOverlayRules)
        LazyColumn(
            Modifier.fillMaxSize(),
            state = list,
            reverseLayout = true,
            contentPadding = PaddingValues(start = 14.dp, end = 14.dp, top = top + 10.dp, bottom = maxOf(bottom, 10.dp)),
            verticalArrangement = Arrangement.spacedBy(8.dp, Alignment.Bottom),
        ) {
            items(asks.asReversed(), key = { "ask:" + it.id }, contentType = { "ask" }) { ask ->
                Box(Modifier.animateItem(fadeInSpec = if (reduce) null else tween(Motion.SMALL_MS), placementSpec = if (reduce) null else Motion.rise(),
                    fadeOutSpec = if (reduce) null else tween(Motion.SMALL_MS))) {
                    ChatAskCard(ask, onSolve = {
                        haptics.tick()
                        container.openRequests.value = chat.kelo.app.notify.OpenTarget.Tab(ask.approval.tab, control = true)
                    }) { ok, rememberSite, rememberSchedule ->
                        val card = ask.approval.event.card
                        if (ok && card != null && card.needsCard) {
                            haptics.tick()
                            vm.answerAsk(ask.id, true, sendCard = cardSender(ask.id, card.card.id))
                        } else {
                            if (ok) haptics.confirm() else haptics.reject()
                            vm.answerAsk(ask.id, ok, rememberSite = rememberSite, rememberSchedule = rememberSchedule)
                        }
                    }
                }
            }
            // payment steps that need the user, and PINs waiting for this phone: time-critical, at the very bottom
            items(state.pinNeeds.takeLast(2).asReversed(), key = { "pin:" + it.slotId }, contentType = { "pin" }) { need ->
                PinNeedCard(need) { haptics.tick(); vm.releasePin(need.slotId, pinSender(need.slotId, need.pinId)) }
            }
            items(PaymentStepRules.visible(state.paymentSteps).asReversed(), key = { "step:" + it.stepId }, contentType = { "step" }) { step ->
                PaymentStepCard(step, stepActions(step))
            }
            state.phase?.let { p -> item(key = "status") { StatusLine(p) } }
            items(rows, key = { it.key }, contentType = { it::class }) { item ->
                // rows made during this session rise into place; rows from history just appear
                val fresh = remember(item.key) { vm.takeFresh(item.key) }
                Box(
                    Modifier
                        .animateItem(fadeInSpec = null, placementSpec = if (reduce) null else Motion.rise(), fadeOutSpec = if (reduce) null else tween(Motion.SMALL_MS))
                        .riseIn(fresh && !reduce, fromEnd = item is ChatItem.Msg && item.mine),
                ) {
                    // the long-press menu: what this row offers right now (다시 시도 only on the newest reply or an error)
                    val actions = remember(item, state.items, state.busy) { MessageActions.actions(state.items, item.key, state.busy) }
                    when (item) {
                        is ChatItem.Msg -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            if (item.key in timed) TimeLine(timeLabeler.label(item.at))
                            if (item.mine) {
                                // the files sent with it (pictures as thumbnails, files as chips); the app's own sentence is not shown
                                if (item.files.isNotEmpty()) UserFiles(item.files, Modifier.fillMaxWidth().padding(start = 48.dp))
                                val lineWord = stringResource(R.string.chat_attachment_line, "\u0000")
                                val shown = remember(item.text, item.files, item.autoText, lineWord) {
                                    MessageFileRules.shownText(item.text, item.files, item.autoText) { lineWord.replace("\u0000", it) }
                                }
                                if (shown.isNotEmpty() || item.files.isEmpty()) UserBubble(shown, item.reaction, actions) { a -> onAction(item, item.text, a) }
                            }
                            else if (item.briefing != null) {
                                val ctx = LocalContext.current
                                BriefingCardView(
                                    item.briefing,
                                    onTap = { t ->
                                        haptics.tick()
                                        when (t) {
                                            is BriefingTap.ChatAt -> container.chatFocus.value = t.messageId
                                            BriefingTap.Waiting -> container.openRequests.value = chat.kelo.app.notify.OpenTarget.Activity
                                            is BriefingTap.Task -> container.openRequests.value = chat.kelo.app.notify.OpenTarget.Task(t.id)
                                            BriefingTap.Goals -> onOpenGoals()
                                            is BriefingTap.Event -> runCatching {
                                                ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW,
                                                    android.net.Uri.parse("content://com.android.calendar/time/${t.atMs}")))
                                            }
                                            is BriefingTap.Mail -> vm.setInput(ctx.getString(R.string.briefing_mail_ask, t.subject))
                                            is BriefingTap.Suggest -> vm.setInput(t.text)
                                        }
                                    },
                                    onHours = { h, accept ->
                                        scope.launch {
                                            runCatching {
                                                container.sessions.requireApi().post<kotlinx.serialization.json.JsonElement>(
                                                    if (accept) chat.kelo.app.my.ContactHoursRules.acceptPath(h.id) else chat.kelo.app.my.ContactHoursRules.keepPath(h.id))
                                            }
                                        }
                                    },
                                    onLink = { url -> runCatching { ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url))) } },
                                )
                            } else {
                                if (item.text.isNotEmpty() || item.images.isEmpty()) {
                                    AssistantBubbles(item.text, streaming = state.busy && item.key == streamingKey, actions) { text, a -> onAction(item, text, a) }
                                }
                                item.images.forEach { img -> ChatPicture(img) }
                                item.id?.let { id -> state.followups[id] }?.let { f -> MessageFollowups(f, state, vm) }
                                item.replies?.takeIf { !state.busy && QuickReplyRules.isOpen(state.items, item.key) }?.let { r ->
                                    QuickReplies(r, onSend = { text, answers -> haptics.tick(); vm.send(text, answers = answers) })
                                }
                            }
                        }
                        is ChatItem.Tool -> ToolChip(item)
                        is ChatItem.Approval -> ApprovalCard(item, onAnswer = { id, ok ->
                            if (ok && item.pin != null) vm.pinAllowed(id)   // its pin_needed row then asks for the owner's check at once
                            val card = item.card
                            if (ok && card != null && card.needsCard) {
                                haptics.tick()
                                vm.approveWithCard(id, cardSender(id, card.card.id))
                            } else {
                                if (ok) haptics.confirm() else haptics.reject()
                                vm.approve(id, ok)
                            }
                        }, onKeepForSchedule = { id -> haptics.confirm(); vm.approve(id, true, rememberSchedule = true) })
                        is ChatItem.Browser -> BrowserCard(item, onOpenLive = { onOpenBrowser(item) })
                        is ChatItem.CardNeeded -> CardNeededCard(item, onSend = {
                            haptics.tick()
                            vm.sendCard(item.approvalId, cardSender(item.approvalId, item.card.id))
                        })
                        is ChatItem.PaymentAuto -> PaymentAutoRow(item)
                        is ChatItem.ShippingForm -> ShippingFormCard(item) { outcome ->
                            vm.shippingAnswered(item.ask.formId, outcome)
                            // "Add a card" as the way to pay: the wallet's own card form, behind its lock
                            if (outcome is ShippingOutcome.Sent && outcome.payment == chat.kelo.app.wallet.ShippingAsk.PAY_CARD) onOpenCardForm()
                        }
                        is ChatItem.Secret -> {
                            val on = marked == item.request.id
                            val ring by androidx.compose.animation.animateColorAsState(
                                if (on) Kelo.colors.accent else Kelo.colors.accent.copy(alpha = 0f), Motion.small(reduce), label = "entry-mark")
                            chat.kelo.app.chat.vault.SecretEntryCard(
                                item.request,
                                onSaved = { answer, result -> container.agentActivity.entryClosed(item.request.kind); vm.secretSaved(item.request, answer, result) },
                                onCancel = { haptics.tick(); container.agentActivity.entryClosed(item.request.kind); vm.secretCancelled(item.request) },
                                onGone = { vm.secretGone(item.request) },
                                modifier = Modifier.border(2.dp, ring, RoundedCornerShape(16.dp)),
                            )
                        }
                        is ChatItem.Vault -> VaultCardRow(item, vm)
                        is ChatItem.Connect -> ConnectCard(item, vm)
                        is ChatItem.LocationAsk -> LocationAskCard(item, vm)
                        is ChatItem.Notice -> NoticeChip(noticeText(item.notice))
                        is ChatItem.Error -> {
                            val text = errorText(item.error)
                            ErrorBubble(text, actions) { a -> onAction(item, text, a) }
                        }
                    }
                }
            }
        }
    }
}

/** A row made in this session rises a little and grows from about 92% as it appears (a soft spring). */
@Composable
private fun Modifier.riseIn(active: Boolean, fromEnd: Boolean): Modifier {
    if (!active) return this
    val p = remember { Animatable(0f) }
    LaunchedEffect(Unit) { p.animateTo(1f, Motion.rise()) }
    return graphicsLayer {
        val v = p.value
        alpha = v.coerceIn(0f, 1f)
        val s = 0.92f + 0.08f * v
        scaleX = s
        scaleY = s
        translationY = (1f - v) * 18.dp.toPx()
        transformOrigin = TransformOrigin(if (fromEnd) 1f else 0f, 1f)
    }
}

/** The composer's own space above its card (Composer: padding top). */
private val COMPOSER_TOP = 6.dp
/** From the screen's end edge to the "↓" button's: the card's 12dp margin plus 12dp, which centres it over 보내기. */
private val JUMP_END = 24.dp

/** The "↓" on the composer's top border that takes the list back to the newest message; the dot: the assistant wrote more meanwhile. */
@Composable
private fun JumpPill(unread: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    val source = remember { MutableInteractionSource() }
    val label = stringResource(if (unread) R.string.chat_jump_latest_new else R.string.chat_jump_latest)
    Box {
        Box(
            Modifier
                .pressShrink(source)
                .shadow(6.dp, CircleShape, clip = false)
                .size(ChatOverlayRules.JUMP_SIZE.dp)
                .clip(CircleShape)
                .background(c.card)
                .border(1.dp, c.line, CircleShape)
                .clickable(interactionSource = source, indication = ripple(), role = Role.Button, onClickLabel = label, onClick = onClick)
                .semantics { contentDescription = label },
            contentAlignment = Alignment.Center,
        ) { Icon(KeloIcons.ArrowDown, contentDescription = null, tint = c.ink, modifier = Modifier.size(20.dp)) }
        if (unread) {
            Box(
                Modifier.align(Alignment.TopEnd).size(11.dp).clip(CircleShape).background(c.card).padding(2.dp)
                    .clip(CircleShape).background(c.accent),
            )
        }
    }
}

@Composable
private fun noticeText(n: ChatNotice): String = when (n) {
    is ChatNotice.Fallback -> stringResource(R.string.chat_notice_fallback, ModelNames.display(n.model, null), n.reason)
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
    ChatError.CameraDenied -> stringResource(R.string.chat_camera_denied)
    ChatError.CameraMissing -> stringResource(R.string.chat_camera_missing)
}

private val BubbleShape = RoundedCornerShape(20.dp)

/**
 * The user's own message; a long press opens its menu (복사, 공유, 텍스트 선택, 수정해서 다시 보내기). [reaction]: the emoji Kelo put on
 * it ([Reactions]), a chip over the bubble's bottom edge near its end corner ([BubbleWithReaction]); the row keeps room for the part
 * that hangs below.
 */
@Composable
private fun UserBubble(text: String, reaction: String, actions: List<MsgAction>, onAction: (MsgAction) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 48.dp), horizontalArrangement = Arrangement.End) {
        BubbleWithReaction(reaction) {
            ActionBubble(actions, copyText = { text }, shape = BubbleShape, fromEnd = true, onAction = onAction) {
                Text(
                    text, color = Kelo.colors.ink, style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.clip(BubbleShape).background(Kelo.colors.mine).padding(horizontal = 15.dp, vertical = 11.dp),
                )
            }
        }
    }
}

/**
 * A reply, one bubble per <message_break/>. While it is [streaming] the text is revealed smoothly ([rememberRevealed]) instead of
 * jumping by a chunk at each poll; only the block being written is parsed again as text arrives ([StreamingMarkdown]).
 */
@Composable
private fun AssistantBubbles(text: String, streaming: Boolean, actions: List<MsgAction>, onAction: (String, MsgAction) -> Unit) {
    val c = Kelo.colors
    val parser = remember(c) { StreamingMarkdown(MdStyles.of(c)) }
    val bubbles = remember(text, parser) { parser.bubbles(text) }
    // each bubble's own text, without the Markdown marks, for 복사 / 공유 / 텍스트 선택: worked out when one is used
    val plain: (Int) -> String = remember(text) { { i -> Markdown.plain(Markdown.splitBubbles(text).getOrElse(i) { "" }) } }
    val total = remember(bubbles) { bubbles.sumOf { b -> b.sumOf { it.length } } }
    val revealed by rememberRevealed(total, streaming)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        var before = 0
        for ((i, blocks) in bubbles.withIndex()) {
            val visible = revealed - before
            before += blocks.sumOf { it.length }
            if (visible <= 0f) break
            Row(Modifier.fillMaxWidth().padding(end = 48.dp)) {
                // no menu while the reply is still being written
                ActionBubble(if (streaming) emptyList() else actions, copyText = { plain(i) }, shape = BubbleShape, fromEnd = false,
                    onAction = { a -> onAction(plain(i), a) }) {
                    StyledMarkdown(
                        blocks,
                        Modifier.clip(BubbleShape).background(c.bubble).padding(horizontal = 15.dp, vertical = 11.dp),
                        visible = if (revealed >= total) null else visible,
                    )
                }
            }
        }
    }
}

/** An error; a long press offers 복사 and (when it is after the user's last message) 다시 시도. */
@Composable
private fun ErrorBubble(text: String, actions: List<MsgAction>, onAction: (MsgAction) -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().padding(end = 48.dp)) {
        ActionBubble(actions, copyText = { text }, shape = BubbleShape, fromEnd = false, onAction = onAction) {
            Text(
                text, style = MaterialTheme.typography.bodyLarge,
                color = c.dangerInk,
                modifier = Modifier.clip(BubbleShape).background(c.dangerSoft)
                    .padding(horizontal = 15.dp, vertical = 11.dp),
            )
        }
    }
}

@Composable
private fun NoticeChip(text: String) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth()) {
        Text(
            text, fontSize = 12.sp, lineHeight = 16.sp, color = c.muted,
            modifier = Modifier.border(1.dp, c.line, RoundedCornerShape(99.dp)).padding(horizontal = 10.dp, vertical = 3.dp),
        )
    }
}

@Composable
private fun ToolChip(t: ChatItem.Tool) {
    if (t.folded) {
        FoldedToolChip(t)
        return
    }
    // a tool still running is already told by the status line under the messages: one line, not two
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
 * Several calls of one kind in a row as ONE chip that updates in place: "메일 읽는 중 · 3/6" while they run, "✓ 메일 6개 읽음" once done
 * (failures counted after it). Tapping it shows each call with what it was about.
 */
@Composable
private fun FoldedToolChip(t: ChatItem.Tool) {
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
                    .clickable(role = Role.Button, onClick = { open = !open }).padding(horizontal = 10.dp, vertical = 3.dp),
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

/** How long a card takes to change size (the secure and connect cards folding into one line). */
internal val FoldSpec = tween<IntSize>(Motion.STANDARD_MS, easing = Motion.Standard)

/**
 * A tool waiting for a yes or no. Once answered (or expired) the card stays as it was asked, without its buttons, and a chip on
 * it says how it ended.
 */
@Composable
private fun ApprovalCard(a: ChatItem.Approval, onAnswer: (String, Boolean) -> Unit, onKeepForSchedule: (String) -> Unit = {}) {
    val c = Kelo.colors
    CardBox {
        val payment = a.payment
        if (a.urgent && a.open) chat.kelo.app.agent.UrgentLine(a.missed)
        if (payment != null) {
            PurchaseApproval(a, payment, onAnswer = { ok -> onAnswer(a.id, ok) })
            return@CardBox
        }
        val grant = a.grant
        if (grant != null) {
            // the setup card of a "when X happens, do Y" task: its own layout (trigger, action, limits, the card's auto-pay limit)
            chat.kelo.app.agent.TaskGrantApproval(grant, a.open, outcome = { Outcomes.of(a)?.let { OutcomeChip(it) } }) { ok -> onAnswer(a.id, ok) }
            return@CardBox
        }
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(ChatAsks.question(a.ask) ?: stringResource(R.string.chat_approval_ask, ProviderConsentRules.askWord(a.shown, androidx.compose.ui.platform.LocalConfiguration.current.locales[0].language), ProviderConsentRules.objectParticle(a.shown)), color = c.ink, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            Outcomes.of(a)?.let { OutcomeChip(it) }
        }
        // what it will do, in words: the readable pairs, else the server's sentence; the raw arguments are never shown
        ApprovalFacts(a.lines, a.detail)
        if (a.items.isNotEmpty()) ApprovalItems(a.items)
        if (a.open) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                SecondaryButton(stringResource(R.string.chat_approval_deny), onClick = { onAnswer(a.id, false) }, modifier = Modifier.weight(1f))
                PrimaryButton(stringResource(R.string.chat_approval_allow), onClick = { onAnswer(a.id, true) }, modifier = Modifier.weight(1f))
            }
            if (a.canRememberSchedule) LinkButton(stringResource(R.string.approval_keep_for_schedule), onClick = { onKeepForSchedule(a.id) })
        }
    }
}

/** The items a permission for many covers, behind "see the list (37)": the user sees exactly what the one Allow is for. */
@Composable
internal fun ApprovalItems(items: List<String>) {
    val c = Kelo.colors
    var open by remember(items) { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().animateContentSize(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        LinkButton(stringResource(if (open) R.string.chat_approval_list_hide else R.string.chat_approval_list_show, items.size), onClick = { open = !open })
        if (open) {
            Column(
                Modifier.fillMaxWidth().heightIn(max = 280.dp).clip(RoundedCornerShape(10.dp)).background(c.bubble)
                    .verticalScroll(androidx.compose.foundation.rememberScrollState()).padding(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                items.forEachIndexed { i, line ->
                    Text("${i + 1}. $line", style = MaterialTheme.typography.bodySmall, color = c.ink)
                }
            }
        }
    }
}

/** An approval's readable pairs as a small two-column list, or its one sentence when there are none. */
@Composable
internal fun ApprovalFacts(lines: List<ApprovalLine>, detail: String?) {
    val c = Kelo.colors
    if (lines.isNotEmpty()) {
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(c.bubble).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            lines.forEach { l ->
                if (l.full) {
                    // the whole thing, as it will be sent (e.g. a text message): the label above, every word below
                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        if (l.label.isNotEmpty()) Text(l.label, style = MaterialTheme.typography.bodySmall, color = c.muted)
                        Text(l.value, style = MaterialTheme.typography.bodyMedium, color = c.ink)
                    }
                } else Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (l.label.isNotEmpty()) Text(l.label, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.widthIn(min = 56.dp, max = 110.dp))
                    Text(chat.kelo.app.ui.components.TokenText.fit(l.value, 28), style = MaterialTheme.typography.bodySmall, color = c.ink,
                        maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                }
            }
        }
    } else if (!detail.isNullOrBlank()) {
        Text(detail, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    }
}

/** A card's folded result: a small icon and one line ("허용함 · gmail.send"). */
@Composable
internal fun ResultLine(icon: ImageVector, text: String, muted: Boolean = false) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(icon, contentDescription = null, tint = if (muted) c.muted else c.ink, modifier = Modifier.size(16.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = if (muted) c.muted else c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

/**
 * A follow-up's buttons under the agent's message (sign in, allow, go on, ask later, stop asking); once one was used, one line
 * says what happened. 로그인하기 opens the sign-in sheet here.
 */
@Composable
private fun MessageFollowups(f: chat.kelo.app.followup.Followup, state: ChatState, vm: ChatViewModel) {
    val haptics = rememberHaptics()
    var login by remember(f.id) { mutableStateOf<String?>(null) }
    val outcome = ChatReducer.closedOutcome(f, state.outcomes[f.id])
    if (outcome != null) {
        ResultLine(if (outcome == ActionOutcome.GONE) KeloIcons.Clock else KeloIcons.Check, chat.kelo.app.followup.outcomeText(outcome), muted = outcome == ActionOutcome.GONE)
        return
    }
    val buttons = remember(f.actions, f.id) { chat.kelo.app.followup.FollowupRules.buttons(f.actions, f.id) }
    chat.kelo.app.followup.FollowupButtons(buttons, busy = f.id in state.acting, onPress = { step ->
        haptics.tick()
        if (step is chat.kelo.app.followup.FollowupStep.OpenLogin) login = step.name else vm.runAction(f, step)
    })
    login?.let { name ->
        chat.kelo.app.followup.SignInSheet(name, onNotify = { text -> vm.actionSignedIn(f); vm.send(text) }, onClose = { login = null })
    }
}

/** The secure form for a login or a key; it folds into one line once the agent was told, or when a newer request replaced it. */
@Composable
private fun VaultCardRow(item: ChatItem.Vault, vm: ChatViewModel) {
    // one box around both forms, so the change of size between them animates
    Box(Modifier.fillMaxWidth().animateContentSize(FoldSpec)) {
        when (item.state) {
            CardState.OPEN -> SecureCard(item.name, onNotify = { text ->
                vm.settleCard(item.key, CardState.DONE)
                vm.send(text)
            })
            CardState.DONE -> CardBox { ResultLine(KeloIcons.Shield, stringResource(R.string.chat_vault_done)) }
            CardState.CANCELLED -> CardBox { ResultLine(KeloIcons.Close, stringResource(R.string.chat_card_cancelled), muted = true) }
            CardState.EXPIRED -> CardBox { ResultLine(KeloIcons.Clock, stringResource(R.string.chat_card_expired), muted = true) }
        }
    }
}

/**
 * The assistant asked the user to connect a service (connect_service): the connector's own connect panel, right here in the chat
 * When it is connected, the panel's "에이전트에게 알리기" tells the agent and the card folds into one line.
 */
@Composable
private fun ConnectCard(item: ChatItem.Connect, vm: ChatViewModel) {
    CardBox(Modifier.animateContentSize(FoldSpec)) {
        when (item.state) {
            CardState.OPEN -> chat.kelo.app.connectors.ConnectPanel(item.service, header = true, onNotify = { text ->
                vm.settleCard(item.key, CardState.DONE)
                vm.send(text)
            })
            CardState.DONE -> ResultLine(KeloIcons.Check, stringResource(R.string.chat_connect_done))
            CardState.CANCELLED -> ResultLine(KeloIcons.Close, stringResource(R.string.chat_card_cancelled), muted = true)
            CardState.EXPIRED -> ResultLine(KeloIcons.Clock, stringResource(R.string.chat_card_expired), muted = true)
        }
    }
}

@Composable
private fun StatusLine(p: Phase) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(p.since) {
        while (true) {
            now = System.currentTimeMillis()
            delay(1000)
        }
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
        // a tool's own words already say what is going on ("사이트 여는 중")
        p.kind == PhaseKind.TOOL -> stringResource(R.string.chat_status, p.label)
        // the fallback's model, readable rather than its id
        p.kind == PhaseKind.SWITCHING -> stringResource(R.string.chat_status_label, phase, ModelNames.display(p.label, null))
        else -> stringResource(R.string.chat_status_label, phase, p.label)
    }
    val seconds = ((now - p.since) / 1000).coerceAtLeast(0).toInt()
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp).semantics { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Dots()
        Text(text, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
        Text(stringResource(R.string.chat_seconds, seconds), style = MaterialTheme.typography.bodySmall, color = c.muted)
    }
}

@Composable
private fun Dots() {
    val t = rememberInfiniteTransition(label = "dots")
    Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
        repeat(3) { i ->
            val a by t.animateFloat(
                initialValue = 0.25f, targetValue = 1f, label = "dot$i",
                animationSpec = infiniteRepeatable(tween(600, delayMillis = i * 150), RepeatMode.Reverse),
            )
            Box(Modifier.size(6.dp).alpha(a).background(Kelo.colors.muted, CircleShape))
        }
    }
}

// ---- the composer: one rounded box with the text on top and, under it, "+", the model chip, the mic and 보내기

/** The model chip inside the composer: "<model> <thinking level>"; opens the model chooser. Main model only, never the fallback. */
@Composable
private fun ModelChip(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val source = remember { MutableInteractionSource() }
    val description = stringResource(R.string.chat_model_settings)
    Row(
        modifier
            .clip(RoundedCornerShape(99.dp))
            .pressShrink(source)
            .clickable(interactionSource = source, indication = ripple(), role = Role.Button, onClick = onClick)
            .semantics(mergeDescendants = true) { contentDescription = "$description: $text" }
            .heightIn(min = 36.dp)
            .padding(start = 10.dp, end = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(text, color = c.ink, fontSize = 13.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f, fill = false))
        Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp).graphicsLayer { rotationZ = 90f })
    }
}

@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
private fun Composer(
    input: String, busy: Boolean, canSend: Boolean, focusRequester: FocusRequester, onFocus: (Boolean) -> Unit,
    onInput: (String) -> Unit, onSend: () -> Unit, onStop: () -> Unit, onAttach: () -> Unit,
    chipText: String, onChip: () -> Unit, onImages: (List<android.net.Uri>) -> Unit = {},
) {
    val c = Kelo.colors
    var focused by remember { mutableStateOf(false) }
    val border by androidx.compose.animation.animateColorAsState(
        if (focused) c.accent else c.line, Motion.small(LocalReduceMotion.current), label = "composer-border",
    )
    Column(
        Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = COMPOSER_TOP, bottom = 10.dp)
            .clip(RoundedCornerShape(24.dp)).background(c.card).border(1.dp, border, RoundedCornerShape(24.dp)),
    ) {
        // a TextFieldState field: the one kind that takes pictures from the keyboard (Gboard's and Samsung's clipboard screenshot above
        // the keys), from a long-press Paste and from a drop; text put in from outside (수정해서 다시 보내기, a share) puts the cursor
        // at its end
        val state = rememberTextFieldState(input)
        LaunchedEffect(input) {
            if (state.text.toString() != input) state.edit { replace(0, length, input); placeCursorAtEnd() }
        }
        LaunchedEffect(state) {
            snapshotFlow { state.text.toString() }.collect { if (it != input) onInput(it) }
        }
        val muted = c.muted
        val placeholder = stringResource(R.string.chat_input_placeholder)
        val textStyle = MaterialTheme.typography.bodyLarge.copy(color = c.ink)
        BasicTextField(
            state = state,
            // a little more room above the first line: the "↓" rests on the card's top-end strip and never covers the text
            modifier = Modifier.padding(top = ChatOverlayRules.fieldTopExtra(16f).dp)
                .fillMaxWidth().heightIn(min = 48.dp).focusRequester(focusRequester)
                .onFocusChanged { focused = it.isFocused; onFocus(it.isFocused) }
                .contentReceiver(PastedImages.receiver(onImages)),
            textStyle = textStyle,
            cursorBrush = SolidColor(c.accent),
            lineLimits = TextFieldLineLimits.MultiLine(1, 6),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            decorator = { inner ->
                Box(Modifier.padding(horizontal = 16.dp, vertical = 14.dp)) {
                    if (state.text.isEmpty()) Text(placeholder, style = textStyle, color = muted)
                    inner()
                }
            },
        )
        Row(
            Modifier.fillMaxWidth().padding(start = 4.dp, end = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            IconButton(onClick = onAttach) {
                Icon(KeloIcons.Plus, contentDescription = stringResource(R.string.chat_attach), tint = c.muted)
            }
            // the chip takes what is left (ellipsized), so a long model name never squeezes 보내기 on a narrow screen
            Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) { ModelChip(chipText, onChip, Modifier.widthIn(max = 220.dp)) }
            // the mic, left of 보내기: spoken words go into the field at the cursor
            chat.kelo.app.voice.MicButton(
                field = { TextFieldValue(state.text.toString(), state.selection) },
                onField = { v -> state.edit { replace(0, length, v.text); selection = v.selection } },
            )
            val sendSource = remember { MutableInteractionSource() }
            IconButton(
                onClick = if (busy) onStop else onSend,
                enabled = canSend,
                interactionSource = sendSource,
                colors = IconButtonDefaults.iconButtonColors(
                    containerColor = c.ink, contentColor = c.bg, disabledContainerColor = c.disabledFill, disabledContentColor = c.disabledInk,
                ),
                modifier = Modifier.pressShrink(sendSource),
            ) {
                Icon(
                    if (busy) StopIcon else KeloIcons.Send,
                    contentDescription = stringResource(if (busy) R.string.chat_stop else R.string.chat_send),
                )
            }
        }
    }
}

/** How long a secure entry's card stays marked after a row in 답이 필요해요 led to it, and how long a missing one is waited for. */
private const val ENTRY_MARK_MS = 1_800L
private const val ENTRY_WAIT_MS = 3_000L

/** A filled square. */
private val StopIcon: ImageVector by lazy {
    ImageVector.Builder("stop", 24.dp, 24.dp, 24f, 24f).apply {
        path(fill = SolidColor(Color.Black)) {
            moveTo(8f, 7f); lineTo(16f, 7f); quadTo(17f, 7f, 17f, 8f); lineTo(17f, 16f); quadTo(17f, 17f, 16f, 17f)
            lineTo(8f, 17f); quadTo(7f, 17f, 7f, 16f); lineTo(7f, 8f); quadTo(7f, 7f, 8f, 7f); close()
        }
    }.build()
}
