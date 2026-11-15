package chat.kelo.desktop.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import chat.kelo.app.R
import chat.kelo.app.core.Profile
import chat.kelo.app.ui.theme.KeloTheme
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.desktop.chat.ChatModel
import chat.kelo.desktop.core.AppContainer
import chat.kelo.app.notify.OpenTarget
import chat.kelo.desktop.core.DesktopNote
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.Strings

/** The app's theme and language around [content]: the user's choices (My → this device), else the system's. */
@Composable
fun KeloFrame(app: AppContainer, content: @Composable () -> Unit) {
    val theme by app.settings.theme.collectAsState()
    val lang by app.settings.language.collectAsState()
    val reduce by app.settings.reduceMotion.collectAsState()
    val code = remember(lang) { app.settings.shownLanguage() }
    val strings = remember(code) { Strings.of(code) }
    val dark = when (theme) { "dark" -> true; "light" -> false; else -> isSystemInDarkTheme() }
    CompositionLocalProvider(LocalStrings provides strings, LocalReduceMotion provides reduce) {
        KeloTheme(dark = dark, tag = code) {
            // Arabic reads right to left: the whole window mirrors
            CompositionLocalProvider(
                androidx.compose.ui.platform.LocalLayoutDirection provides
                    if (code == "ar") androidx.compose.ui.unit.LayoutDirection.Rtl else androidx.compose.ui.unit.LayoutDirection.Ltr,
                content = content,
            )
        }
    }
}

/**
 * The window's content: the connect screen while signed out, else the app. The chat lives as long as the sign-in, so a reply keeps
 * coming while another page is open; when it finishes with the window hidden, the tray says so.
 */
@Composable
fun KeloApp(app: AppContainer, initialPage: Page = Page.CHAT, initialLink: String = "") {
    KeloFrame(app) {
        val api by app.sessions.api.collectAsState()
        if (api == null) {
            PairScreen(app, onSignedIn = { }, initialLink = initialLink)
            return@KeloFrame
        }
        val chat = remember(api) { ChatModel(app) }
        DisposableEffect(chat) { onDispose { chat.close() } }
        var page by remember { mutableStateOf(initialPage) }
        var connector by remember { mutableStateOf<String?>(null) }
        val open by app.openPage.collectAsState()
        LaunchedEffect(open) {
            val o = open ?: return@LaunchedEffect
            app.openPage.value = null
            when {
                o.startsWith("connector:") -> { connector = o.removePrefix("connector:"); page = Page.CONNECTORS }
                else -> Page.entries.firstOrNull { it.name.equals(o, ignoreCase = true) }?.let { page = it }
            }
        }
        LaunchedEffect(api) {
            runCatching { app.sessions.requireApi().get<Profile>("/api/profile") }.onSuccess(app::setProfile)
        }
        val strings = LocalStrings.current
        LaunchedEffect(chat) {
            chat.finished.collect { ok ->
                if (!app.windowShown.value) {
                    app.notify(
                        if (ok) DesktopNote(strings.get(R.string.notify_reply_done), app.profile.value?.agentName.orEmpty(), target = OpenTarget.Chat)
                        else DesktopNote(strings.get(R.string.notify_reply_failed), strings.get(R.string.notify_reply_failed_body), DesktopNote.Kind.WARNING, target = OpenTarget.Chat),
                    )
                }
            }
        }
        Shell(app, chat, page, onPage = { page = it }, onSignedOut = {}, connectorToOpen = connector, onConnectorOpened = { connector = null })
    }
}
