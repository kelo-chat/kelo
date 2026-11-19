package chat.kelo.app.call

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toAwtImage
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.compose.LocalLifecycleOwner
import chat.kelo.app.AppServices
import chat.kelo.app.LocalAppServices
import chat.kelo.app.agent.AgentAvatar
import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.core.AesGcmCipher
import chat.kelo.app.core.KeyValueStore
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.theme.KeloTheme
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.voice.VoiceModel
import chat.kelo.app.voice.VoiceView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import java.io.File
import java.lang.reflect.Proxy
import javax.crypto.KeyGenerator
import javax.imageio.ImageIO
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import kotlin.test.Test
import kotlin.test.assertTrue

/** The in-call screen at phone widths, light and dark, with a few lines of the call (brief call-silent S4: looked at by eye). */
@OptIn(ExperimentalTestApi::class)
class InCallScreenShotsTest {

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private class MapStore : KeyValueStore {
        private val map = mutableMapOf<String, String>()
        override fun getString(key: String): String? = map[key]
        override fun putString(key: String, value: String) { map[key] = value }
        override fun remove(key: String) { map.remove(key) }
    }

    private class ShotChat : CallChat {
        override fun sendFromCall(text: String, callId: String, amend: Boolean): Boolean = true
        override fun takeBackFromCall(callId: String): Boolean = false
        override fun followFromCall(turnId: String, callId: String): Boolean = true
        override val turnEvents = MutableSharedFlow<TurnEvent>(extraBufferCapacity = 16)
        override val callTurnEvents = MutableSharedFlow<CallTurnEvent>(extraBufferCapacity = 16)
        private val _busy: MutableStateFlow<Boolean> = MutableStateFlow(false)
        override val busy: StateFlow<Boolean> = _busy.asStateFlow()
    }

    private class ShotAudio : CallAudio {
        override val echoCancel: Boolean = false
        override val hasSpeakerSwitch: Boolean = false
        override var speakerOn: Boolean = true
        override var onInterrupted: (() -> Unit)? = null
        override fun startMic(onChunk: (ByteArray) -> Unit): Boolean = true
        override fun stopMic() {}
        override suspend fun play(pcm16le: ByteArray, sampleRate: Int, onLevel: (Float) -> Unit) {}
        override fun stopPlayback() {}
        override fun chime(on: Boolean) {}
        override fun release() {}
    }

    private fun fakeLifecycleOwner(): LifecycleOwner = object : LifecycleOwner {
        val registry = LifecycleRegistry(this).apply { currentState = Lifecycle.State.RESUMED }
        override val lifecycle: Lifecycle get() = registry
    }

    private fun fakeAppServices(): AppServices {
        val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val store = SecureStore(MapStore(), AesGcmCipher { key })
        val avatar = AgentAvatar(Sessions(store, requireE2e = false))
        return Proxy.newProxyInstance(
            AppServices::class.java.classLoader,
            arrayOf(AppServices::class.java),
        ) { _, method, _ ->
            if (method.name == "getAgentAvatar") avatar else null
        } as AppServices
    }

    /** One screen per test: each setContent adds to the composition, so the captures must not share one test. */
    private fun captureScreen(width: Int, dark: Boolean) = runComposeUiTest {
        val outDir = File(System.getenv("KELO_SHOTS_DIR") ?: "build/screens/call").apply { mkdirs() }
        val scope = CoroutineScope(UnconfinedTestDispatcher())
        val session = CallSession(
            callId = "c_shot", topic = "", fromAgent = false, scope = scope, chat = ShotChat(), api = { null },
            audio = ShotAudio(), stt = VoiceModel({ null }, scope, { "ko" }, VoiceView(state = "ready", runtime = "ready")),
            speech = SpeechModel({ null }, scope, null), phoneListener = null, device = null,
            appLang = { "ko" }, clock = { 1000L }, log = {},
        )
        session.accept()
        session.addMineLine("내일 오전 10시에 치과 예약이 있는지 확인해줄래?")
        session.addKeloText("네, 내일 오전 10시에 치과 예약이 있어요. 알림도 30분 전에 드릴게요.")
        session.closeKeloLine()
        val services = fakeAppServices()
        val lifecycleOwner = fakeLifecycleOwner()
        setContent {
            CompositionLocalProvider(
                LocalLifecycleOwner provides lifecycleOwner,
                LocalAppServices provides services,
                LocalReduceMotion provides true,
            ) {
                KeloTheme(dark = dark) {
                    Box(Modifier.width(width.dp).height(760.dp).testTag("call.shot")) {
                        InCallScreen(session = session, agentName = "Kelo", onMinimize = {})
                    }
                }
            }
        }
        waitForIdle()
        for (tag in listOf("call.minimize", "call.clock", "call.sound", "call.transcript", "call.mute", "call.hangup", "call.keyboard")) {
            onNodeWithTag(tag).assertIsDisplayed()
        }
        val shotRight = onNodeWithTag("call.shot").fetchSemanticsNode().boundsInRoot.right
        val hangupRight = onNodeWithTag("call.hangup").fetchSemanticsNode().boundsInRoot.right
        assertTrue(hangupRight <= shotRight, "the hang-up button is clipped at ${width}dp")
        val image = onNodeWithTag("call.shot").captureToImage().toAwtImage()
        val file = File(outDir, "in-call-${if (dark) "dark" else "light"}-${width}dp.png")
        ImageIO.write(image, "png", file)
        assertTrue(file.exists() && file.length() > 0, "Saved ${file.name}")
        session.hangUp()
    }

    @Test fun inCallLight320() = captureScreen(320, dark = false)
    @Test fun inCallDark320() = captureScreen(320, dark = true)
    @Test fun inCallLight412() = captureScreen(412, dark = false)
    @Test fun inCallDark412() = captureScreen(412, dark = true)
}
