package chat.kelo.desktop

import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isMetaPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.type
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Notification
import chat.kelo.app.notify.NotifyRules
import chat.kelo.desktop.core.NoteText
import androidx.compose.ui.window.Tray
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.WindowPosition
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberTrayState
import androidx.compose.ui.window.rememberWindowState
import chat.kelo.app.R
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.Autostart
import chat.kelo.desktop.core.DesktopNote
import chat.kelo.desktop.core.Log
import chat.kelo.desktop.core.NotePoller
import chat.kelo.desktop.core.Platform
import chat.kelo.desktop.core.SingleInstance
import chat.kelo.desktop.i18n.Strings
import chat.kelo.desktop.ui.KeloApp
import chat.kelo.desktop.ui.Page
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.awt.Dimension
import java.awt.Frame

/**
 * Kelo for the desktop.
 *
 *   Kelo                  the window
 *   Kelo --tray           start hidden in the system tray (how it starts at sign-in)
 *   Kelo --snapshot DIR   draw the main screens into DIR as PNGs and quit (screenshots for the README; see Snapshots.kt)
 *
 * Closing the window leaves Kelo in the tray (unless that is switched off in My → this device), where it keeps checking for the
 * agent's notifications. Ctrl+1…5 (⌘ on a Mac) switch pages.
 */
fun main(args: Array<String>) {
    System.setProperty("apple.awt.application.name", "Kelo")
    val snapshotAt = args.indexOf("--snapshot")
    if (snapshotAt >= 0) {
        Snapshots.run(java.io.File(args.getOrNull(snapshotAt + 1) ?: "screens"), args.toList())
        return
    }
    Log.install()
    Log.line("start ${chat.kelo.desktop.core.ReleaseInfo.VERSION} (package ${System.getProperty("jpackage.app-version") ?: "dev"}) on ${System.getProperty("os.name")} ${System.getProperty("os.version")}, java ${System.getProperty("java.version")}, args=${args.filter { it.startsWith("--") }}")
    val app = AppContainer()
    Log.line(if (app.sessions.api.value != null) "signed in" else "not signed in")
    var showRequest: () -> Unit = {}
    val single = SingleInstance(app.dataDir)
    if (!single.acquire { showRequest() }) { Log.line("another Kelo runs: asked it to show"); return }   // it shows its window instead
    val startHidden = Autostart.TRAY_ARG in args
    val pairArg = args.firstOrNull { it.contains("#pair=") }.orEmpty()

    application {
        var visible by remember { mutableStateOf(!startHidden) }
        val trayState = rememberTrayState()
        val icon = remember { loadIcon("/icons/tray.png") }
        val windowIcon = remember { loadIcon("/icons/kelo-256.png") }
        val lang by app.settings.language.collectAsState()
        val strings = remember(lang) { Strings.of(app.settings.shownLanguage()) }
        val keepInTray by app.settings.keepInTray.collectAsState()
        var toldAboutTray by remember { mutableStateOf(false) }
        showRequest = { visible = true; app.windowShown.value = true }

        LaunchedEffect(Unit) {
            app.notes.collect { n ->
                trayState.sendNotification(Notification(n.title, n.body, if (n.kind == DesktopNote.Kind.WARNING) Notification.Type.Warning else Notification.Type.Info))
            }
        }
        LaunchedEffect(Unit) {
            launch(Dispatchers.IO) {
                NotePoller(app.settings, shown = { app.windowShown.value }) { n ->
                    // in the app's own words, as the phone's notifications; a click opens where the phone's would (NotifyRules.target)
                    val (title, body) = NoteText.of(n) { id -> strings.get(id) }
                    app.notify(DesktopNote(title, body, if (NotePoller.isWarning(n)) DesktopNote.Kind.WARNING else DesktopNote.Kind.INFO, target = NotifyRules.target(n)))
                }.loop { app.sessions.api.value }
            }
        }

        if (icon != null) Tray(
            icon = icon, state = trayState, tooltip = "Kelo",
            // a click on the tray icon, or on a notification's balloon: up comes the window, on what the newest notification was about
            onAction = { showRequest(); app.openLastNote() },
            menu = {
                Item(strings.get(R.string.desktop_tray_open), onClick = { visible = true; app.windowShown.value = true })
                Item(strings.get(R.string.tab_work), onClick = { visible = true; app.openPage.value = Page.WORK.name })
                Separator()
                Item(strings.get(R.string.desktop_tray_quit), onClick = { single.release(); exitApplication() })
            },
        )

        val state = rememberWindowState(size = DpSize(1180.dp, 800.dp), position = WindowPosition.PlatformDefault)
        Window(
            onCloseRequest = {
                if (keepInTray && icon != null) {
                    visible = false
                    app.windowShown.value = false
                    if (!toldAboutTray) {
                        toldAboutTray = true
                        trayState.sendNotification(Notification("Kelo", strings.get(R.string.desktop_still_running)))
                    }
                } else {
                    single.release()
                    exitApplication()
                }
            },
            visible = visible,
            title = "Kelo",
            icon = windowIcon,
            state = state,
            onPreviewKeyEvent = { e ->
                val mod = if (Platform.os == Platform.Os.MAC) e.isMetaPressed else e.isCtrlPressed
                if (e.type == KeyEventType.KeyDown && mod) {
                    val page = when (e.key) {
                        Key.One -> Page.CHAT; Key.Two -> Page.GOALS; Key.Three -> Page.WORK; Key.Four -> Page.CONNECTORS; Key.Five -> Page.MY
                        else -> null
                    }
                    if (page != null) { app.openPage.value = page.name; true } else false
                } else false
            },
        ) {
            LaunchedEffect(Unit) {
                Log.line("window ${window.width}x${window.height}, tray ${if (icon != null) "on" else "off"}, theme ${app.settings.theme.value}, language ${app.settings.shownLanguage()}")
                window.minimumSize = Dimension(520, 560)
                showRequest = {
                    visible = true
                    app.windowShown.value = true
                    java.awt.EventQueue.invokeLater { window.extendedState = window.extendedState and Frame.ICONIFIED.inv(); window.toFront(); window.requestFocus() }
                }
                window.addWindowStateListener { app.windowShown.value = visible && (window.extendedState and Frame.ICONIFIED) == 0 }
                window.addWindowFocusListener(object : java.awt.event.WindowAdapter() {
                    override fun windowGainedFocus(e: java.awt.event.WindowEvent?) { app.windowShown.value = true }
                })
            }
            KeloApp(app, initialLink = pairArg)
        }
    }
}

/** A PNG from the app's resources as a painter (the tray and window icons made by `./gradlew renderIcons`). */
private fun loadIcon(path: String): Painter? = runCatching {
    val bytes = Snapshots::class.java.getResourceAsStream(path)?.use { it.readBytes() } ?: return null
    BitmapPainter(org.jetbrains.skia.Image.makeFromEncoded(bytes).toComposeImageBitmap())
}.getOrNull()
