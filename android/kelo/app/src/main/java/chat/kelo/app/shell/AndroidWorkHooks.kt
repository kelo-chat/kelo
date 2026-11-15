package chat.kelo.app.shell

import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.Dp
import chat.kelo.app.R
import chat.kelo.app.chat.AndroidChatPlatform
import chat.kelo.app.chat.CardSenders
import chat.kelo.app.chat.ChatPlatform
import chat.kelo.app.ui.PushHost
import chat.kelo.app.wallet.CardSendError
import chat.kelo.app.work.WorkScreenHooks
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow

/**
 * What the phone's shell gives the shared work screen: the tab bar's inset and re-select, hiding the bars under a sub page, the
 * push-style page transitions with the back gesture, the biometric card sending, and the keychain sign-in sheet.
 */
@Composable
fun rememberAndroidWorkHooks(): WorkScreenHooks {
    val chrome = LocalShellChrome.current
    val reselectFlow = remember { MutableSharedFlow<Unit>(extraBufferCapacity = 1) }
    OnTabReselect(Routes.WORK) { reselectFlow.emit(Unit) }
    val bar = bottomBarHeight()
    return remember(chrome, bar) {
        object : WorkScreenHooks {
            override val reselect: Flow<Unit> = reselectFlow
            override val bottomInset: Dp = bar
            override val platform: ChatPlatform = AndroidChatPlatform
            override fun hideChrome(key: String, hidden: Boolean) = chrome.hide(key, hidden)

            // an end-to-end card goes from this phone only after the owner's check: decrypt, send to its slot, wipe
            @Composable override fun cardSenders(): CardSenders = chat.kelo.app.wallet.rememberCardSenders()

            @Composable override fun PageStack(state: List<String>, back: List<String>?, onBack: () -> Unit, content: @Composable (List<String>) -> Unit) {
                PushHost(state = state, depth = { it.size }, back = back, onBack = onBack, content = content)
            }

            @Composable override fun SignInSheet(name: String, onNotify: (String) -> Unit, onClose: () -> Unit) {
                chat.kelo.app.followup.SignInSheet(name, onNotify, onClose)
            }
        }
    }
}
