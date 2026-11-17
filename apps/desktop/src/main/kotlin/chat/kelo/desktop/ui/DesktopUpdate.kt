package chat.kelo.desktop.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.update.Release
import chat.kelo.app.update.UpdatePhase
import chat.kelo.app.update.UpdateState
import chat.kelo.app.update.checkValue
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.string
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.UpdateState as DesktopUpdateState
import kotlinx.coroutines.launch

/*
 * The update screens are the shared ones (chat.kelo.app.update.UpdateUi); this file only maps the desktop updater's own state
 * (download, check and open the installer, see core/Updates.kt) onto the shared [UpdateState] and wires the buttons to it.
 */

/** The updater's state as the shared screens read it: an opened installer is "ready", a failed download or open is "failed". */
internal fun DesktopUpdateState.toShared(running: String, bannerSeen: String, required: Boolean, quitNote: String? = null, openFailed: String? = null): UpdateState = UpdateState(
    enabled = true,
    running = running,
    checking = phase == DesktopUpdateState.Phase.CHECKING,
    checked = phase !in setOf(DesktopUpdateState.Phase.IDLE, DesktopUpdateState.Phase.CHECKING),
    checkFailed = phase == DesktopUpdateState.Phase.CHECK_FAILED,
    offer = if (newer) build?.let { Release(version = it.version, size = it.size) } else null,
    phase = when (phase) {
        DesktopUpdateState.Phase.DOWNLOADING -> UpdatePhase.DOWNLOADING
        DesktopUpdateState.Phase.VERIFYING -> UpdatePhase.VERIFYING
        DesktopUpdateState.Phase.OPENED -> UpdatePhase.READY
        DesktopUpdateState.Phase.MISMATCH -> UpdatePhase.MISMATCH
        DesktopUpdateState.Phase.DOWNLOAD_FAILED, DesktopUpdateState.Phase.OPEN_FAILED -> UpdatePhase.FAILED
        else -> UpdatePhase.IDLE
    },
    progress = (progress * 100).toInt(),
    bannerSeen = bannerSeen,
    required = required,
    installNote = if (phase == DesktopUpdateState.Phase.OPENED) quitNote else null,
    failureDetail = if (phase == DesktopUpdateState.Phase.OPEN_FAILED) openFailed else null,
)

/** The updater's state for the shared screens, kept up to date. */
@Composable
private fun AppContainer.sharedUpdateState(): UpdateState {
    val raw by updates.state.collectAsStateWithLifecycle()
    val seen by settings.dismissedUpdate.collectAsStateWithLifecycle()
    val required by updates.required.collectAsStateWithLifecycle()
    val quitNote = string(Res.string.desktop_update_quit_note)
    val openFailed = string(Res.string.desktop_update_open_failed, raw.path.orEmpty())
    return remember(raw, seen, required, quitNote, openFailed) { raw.toShared(updates.currentVersion.orEmpty(), seen, required, quitNote, openFailed) }
}

/** The value under My's "업데이트 확인" row. */
@Composable
fun updateRowValue(s: DesktopUpdateState): String? = checkValue(s.toShared("", "", false))

/** The one-time banner over the page: 받기 starts the download and opens My, where the progress shows; either button puts it away. */
@Composable
fun UpdateBanner(app: AppContainer, onOpenMy: () -> Unit, modifier: Modifier = Modifier) {
    val s = app.sharedUpdateState()
    val raw by app.updates.state.collectAsStateWithLifecycle()
    val version = s.offer?.version ?: return
    if (!s.showBanner || raw.phase != DesktopUpdateState.Phase.AVAILABLE) return
    chat.kelo.app.update.UpdateBanner(
        s, modifier = modifier,
        onGet = { app.settings.setDismissedUpdate(version); app.scope.launch { app.updates.download() }; onOpenMy() },
        onInstall = {},
        onClose = { app.settings.setDismissedUpdate(version) },
    )
}

/**
 * A newer released app that works with the server (X-Kelo-App-Update): once per version, never over the update banner. The button is the
 * app's own update when it knows the offer (the download starts, My shows it), else My (its "check for updates").
 */
@Composable
fun AppNoticeHost(app: AppContainer, onOpenMy: () -> Unit, modifier: Modifier = Modifier) {
    val offered by chat.kelo.app.core.AppVersion.update.collectAsStateWithLifecycle()
    val dismissed by app.settings.appNotice.dismissed.collectAsStateWithLifecycle()
    val s = app.sharedUpdateState()
    val version = offered?.takeIf { chat.kelo.app.update.AppNoticeRules.show(it, dismissed) } ?: return
    if (s.showBanner) return
    chat.kelo.app.update.AppNoticeBanner(
        version, store = false, modifier = modifier,
        onAction = { app.settings.appNotice.dismiss(version); if (s.offer != null) app.scope.launch { app.updates.download() }; onOpenMy() },
        onClose = { app.settings.appNotice.dismiss(version) },
    )
}

/** My's card while a newer build is known. */
@Composable
fun UpdateCard(app: AppContainer, modifier: Modifier = Modifier) {
    val s = app.sharedUpdateState()
    chat.kelo.app.update.UpdateCard(s, onGet = { app.scope.launch { app.updates.download() } }, onInstall = { app.scope.launch { app.updates.reopen() } }, modifier = modifier)
}

/** The server says this version is too old: this covers the window until Kelo is updated (the button starts this app's own update). */
@Composable
fun UpdateRequiredScreen(app: AppContainer, modifier: Modifier = Modifier) {
    val s = app.sharedUpdateState()
    chat.kelo.app.update.UpdateRequiredScreen(
        s, modifier = modifier,
        onGet = { app.scope.launch { app.updates.updateNow() } },
        onInstall = { app.scope.launch { app.updates.reopen() } },
        onLeave = {},
    )
}

/**
 * The "new version" popup (shared rules: once per version, Later = 3 days). Armed at start and when the window comes back after more
 * than an hour; the action is the app's own update (the download starts, My shows it). A store build has no page of ours to open, so
 * it shows no popup (its banner and My stay).
 */
@Composable
fun updatePopupOpen(app: AppContainer): chat.kelo.app.update.UpdateCandidate? {
    val open by app.settings.updatePopup.open.collectAsStateWithLifecycle()
    return open
}

@Composable
fun UpdatePopupHost(app: AppContainer, onOpenMy: () -> Unit) {
    val s = app.sharedUpdateState()
    val offered by chat.kelo.app.core.AppVersion.update.collectAsStateWithLifecycle()
    val candidate = chat.kelo.app.update.UpdatePopupRules.candidate(s, offered, chat.kelo.app.core.AppVersion.channel)
        ?.takeIf { it.source == chat.kelo.app.update.UpdatePopupRules.Source.UPDATER && !s.required }
    val focused = androidx.compose.ui.platform.LocalWindowInfo.current.isWindowFocused
    androidx.compose.runtime.LaunchedEffect(focused) {
        if (!focused) app.settings.updatePopup.left()
        else if (app.settings.updatePopup.returned()) { app.updates.check(); app.settings.updatePopup.consider(candidate) }
    }
    androidx.compose.runtime.LaunchedEffect(candidate) { app.settings.updatePopup.consider(candidate) }
    val shown = updatePopupOpen(app) ?: return
    chat.kelo.app.update.UpdatePopupDialog(
        shown,
        onUpdate = { app.settings.updatePopup.close(); if (s.offer != null) app.scope.launch { app.updates.download() }; onOpenMy() },
        onLater = { app.settings.updatePopup.close() },
    )
}
