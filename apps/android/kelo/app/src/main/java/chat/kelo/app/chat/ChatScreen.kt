package chat.kelo.app.chat

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.foundation.content.contentReceiver
import chat.kelo.app.shell.OnTabReselect
import chat.kelo.app.shell.Routes
import chat.kelo.app.ui.icons.KeloIcons
import kotlinx.coroutines.flow.MutableSharedFlow
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import chat.kelo.app.ui.platform.LocalTextSharer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.Profile
import chat.kelo.app.shell.ChromeRules
import chat.kelo.app.shell.HideShellChrome
import chat.kelo.app.shell.ShellChrome
import chat.kelo.app.shell.bottomBarHeight
import chat.kelo.app.shell.headerHeight
import chat.kelo.app.ui.components.StaleHint
import chat.kelo.app.ui.platform.LocalHaptics
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.theme.Motion
import java.io.File
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

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
 * The 채팅 tab on the phone: the shared screen ([ChatScreenContent]) plus what only the phone does: the chat's view model, the
 * lifecycle (reload on resume, saving the draft, the news check), the photo / camera / file launchers and their permission, text and
 * files shared from another app, the notification question, and the hand-over of a card number to the wallet.
 * [onOpenConnector] opens the Connectors tab at a connector, for the card that appears when the assistant asks the user to connect a
 * service.
 */
@OptIn(ExperimentalLayoutApi::class, androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun ChatScreen(
    profile: Profile, onProfile: (Profile) -> Unit, onOpenConnector: (String) -> Unit = {}, onOpenBrowser: (ChatItem.Browser) -> Unit = {},
    onOpenCardForm: () -> Unit = {},
    /** The goals tab (a goal in the morning briefing) */
    onOpenGoals: () -> Unit = {},
) {
    val container = LocalAppContainer.current
    val sessions = container.sessions
    val vm: ChatViewModel = viewModel {
        ChatViewModel(
            sessions,
            container.store,
            container.agentActivity,
            container.replyWatch,
            AndroidChatPlatform,
            canCall = { container.calls.available.value },
        )
    }
    DisposableEffect(vm) {
        val h = container.calls.attach(vm)
        onDispose { h.close() }
    }
    val state by vm.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val haptics = LocalHaptics.current
    var askNotify by rememberSaveable { mutableStateOf(false) }

    LifecycleEventEffect(androidx.lifecycle.Lifecycle.Event.ON_RESUME) {
        if (vm.state.value.loaded) { vm.loadFollowups(); vm.refreshSecrets() }
        vm.refreshAsks(force = true)   // e.g. back from an approval notification, or from the 작업 page
    }
    LaunchedEffect(vm) {
        vm.events.collect { e ->
            if (e == ChatEvent.SENT) {
                // after the first message: shall Kelo say when a reply is ready? (once per phone)
                val prefs = container.notifyPrefs
                if (chat.kelo.app.notify.AskRules.shouldAsk(prefs.askedPermission, container.notifier.enabled())) askNotify = true
                prefs.askedPermission = true
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
    val focus = LocalFocusManager.current
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) { focus.clearFocus() }

    // "+": a photo (the system picker, no permission), the camera, or a file; each goes into the tray and uploads at once
    val resolver = context.contentResolver
    val photoLabel = stringResource(R.string.attach_photo)
    val photos = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia(MAX_PHOTOS)) { uris ->
        uris.forEach { uri -> vm.attach(image = true, label = photoLabel) { AttachmentReader.photo(resolver, uri) } }
    }
    val files = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            val label = AttachmentReader.displayName(resolver, uri).first ?: context.getString(R.string.attach_file)
            vm.attach(image = false, label = label) { AttachmentReader.document(resolver, uri) }
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
            try { AttachmentReader.photo(resolver, uri) } finally { f.delete() }
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
    val entries = listOf(
        AddEntry("photo", KeloIcons.Image, stringResource(R.string.attach_photo), stringResource(R.string.attach_photo_hint)) { pick(AttachSource.PHOTO) },
        AddEntry("camera", KeloIcons.Camera, stringResource(R.string.attach_camera), stringResource(R.string.attach_camera_hint)) { pick(AttachSource.CAMERA) },
        AddEntry("file", KeloIcons.File, stringResource(R.string.attach_file), stringResource(R.string.attach_file_hint)) { pick(AttachSource.FILE) },
    )

    // the tab bar's re-select scrolls to the newest message
    val reselect = remember { MutableSharedFlow<Unit>(extraBufferCapacity = 1) }
    OnTabReselect(Routes.CHAT) { reselect.emit(Unit) }
    // the composer's voice: the mic and the recording bar share it (remembered here, above the composer)
    val voice = chat.kelo.app.voice.rememberAndroidComposerVoice()
    val hooks = ChatScreenHooks(
        addEntries = entries,
        // a picture pasted or tapped in from the keyboard: the same way as one picked with "+"
        fieldModifier = Modifier.contentReceiver(PastedImages.receiver { uris -> uris.take(MAX_PHOTOS).forEach { uri ->
            vm.attach(image = true, label = photoLabel) { AttachmentReader.photo(resolver, uri) } } }),
        voice = voice.composer,
        mic = { _, _ -> chat.kelo.app.voice.MicButton(voice) },
        cardSenders = { chat.kelo.app.wallet.rememberCardSenders() },
        keyboardChrome = { imeVisible, composerFocused -> HideShellChrome(ShellChrome.KEY_KEYBOARD, ChromeRules.keyboardHidesBar(imeVisible, composerFocused)) },
        bottomInset = bottomBarHeight(),
        reselect = reselect,
        openCalendarAt = { atMs ->
            runCatching {
                context.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW,
                    android.net.Uri.parse("content://com.android.calendar/time/$atMs")))
            }
        },
        secretToCard = { vm.secretToCard(container.secretHandoff) },
    )
    ChatScreenContent(vm, profile, onProfile, hooks, onOpenConnector, onOpenBrowser, onOpenCardForm, onOpenGoals)

    // a message held back for a key (the secret sheet) was not sent: no question over the sheet, it waits for the next message
    val heldBack = state.secretAsk != null || state.consentAsk != null
    LaunchedEffect(heldBack) {
        if (heldBack && askNotify) { askNotify = false; container.notifyPrefs.askedPermission = false }
    }
    if (askNotify && !heldBack) chat.kelo.app.notify.NotificationAskDialog(profile.agentName, onDone = { askNotify = false })
    // the wallet's card form kept a card number from such a message: its reference goes into the composer
    val cardKept by container.secretHandoff.saved.collectAsStateWithLifecycle()
    val cardReference = stringResource(R.string.secret_saved_ref, stringResource(R.string.secret_use_card))
    LaunchedEffect(cardKept) {
        if (cardKept && container.secretHandoff.takeSaved()) vm.secretCardSaved(cardReference)
    }
}

/** What "+" can pick (the phone's own launchers). */
private enum class AttachSource { PHOTO, CAMERA, FILE }
