package chat.kelo.app.call

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
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
import chat.kelo.app.chat.CallMark
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.core.AesGcmCipher
import chat.kelo.app.core.KeyValueStore
import chat.kelo.app.core.Profile
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Sessions
import chat.kelo.app.shell.FloatingHeader
import chat.kelo.app.shell.ShellUiState
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.voice.VoiceModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import java.lang.reflect.Proxy
import javax.crypto.KeyGenerator
import kotlin.test.Test
import kotlin.test.assertTrue

@OptIn(ExperimentalTestApi::class, ExperimentalCoroutinesApi::class)
class CallScreensTest {

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

    @Test
    fun testCallControlClick() = runComposeUiTest {
        var clicked = false
        setContent {
            CallControl(
                icon = KeloIcons.Handset,
                size = 72.dp,
                bg = Kelo.colors.success,
                tint = Kelo.colors.onSuccess,
                label = "Accept",
                tag = "call.accept",
                onClick = { clicked = true },
            )
        }

        onNodeWithTag("call.accept").assertIsDisplayed()
        onNodeWithTag("call.accept").performClick()
        assertTrue(clicked)
    }

    @Test
    fun testFloatingHeaderCallButton() = runComposeUiTest {
        var callTriggered = false
        val profile = Profile(agentName = "Kelo")
        val ui = ShellUiState()
        val services = fakeAppServices()
        val lifecycleOwner = fakeLifecycleOwner()

        setContent {
            CompositionLocalProvider(
                LocalLifecycleOwner provides lifecycleOwner,
                LocalAppServices provides services,
                LocalReduceMotion provides true,
            ) {
                FloatingHeader(
                    profile = profile,
                    ui = ui,
                    onAgent = {},
                    onCall = { callTriggered = true },
                )
            }
        }

        onNodeWithTag("chat.call").assertIsDisplayed()
        onNodeWithTag("chat.call").performClick()
        assertTrue(callTriggered)
    }

    @Test
    fun testFloatingHeaderWithoutCallButton() = runComposeUiTest {
        val profile = Profile(agentName = "Kelo")
        val ui = ShellUiState()
        val services = fakeAppServices()
        val lifecycleOwner = fakeLifecycleOwner()

        setContent {
            CompositionLocalProvider(
                LocalLifecycleOwner provides lifecycleOwner,
                LocalAppServices provides services,
                LocalReduceMotion provides true,
            ) {
                FloatingHeader(
                    profile = profile,
                    ui = ui,
                    onAgent = {},
                    onCall = null,
                )
            }
        }

        onNodeWithTag("chat.call").assertDoesNotExist()
    }

    @Test
    fun testCallMarkRenders() = runComposeUiTest {
        setContent {
            CallMark(Modifier.testTag("call.mark"))
        }

        onNodeWithTag("call.mark").assertIsDisplayed()
    }

    private class FakeCallChat : CallChat {
        override fun sendFromCall(text: String, callId: String): Boolean = true
        override fun followFromCall(turnId: String) {}
        override val turnEvents = MutableSharedFlow<TurnEvent>(extraBufferCapacity = 16)
        override val busy: StateFlow<Boolean> = MutableStateFlow(false)
    }

    private class FakeCallAudio : CallAudio {
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

    @Test
    fun testIncomingCallScreenWrapping320dp() = runComposeUiTest {
        val scope = CoroutineScope(UnconfinedTestDispatcher())
        val session = CallSession(
            callId = "c1",
            topic = "Wichtiges Gespräch über Termine und Kalender",
            fromAgent = true,
            scope = scope,
            chat = FakeCallChat(),
            api = { null },
            audio = FakeCallAudio(),
            stt = VoiceModel({ null }, scope, { "de" }, null),
            speech = SpeechModel({ null }, scope, null),
            phoneListener = null,
            device = { null },
            appLang = { "de" },
            clock = { 1000L },
            log = {},
        )
        val services = fakeAppServices()
        val lifecycleOwner = fakeLifecycleOwner()

        setContent {
            CompositionLocalProvider(
                LocalLifecycleOwner provides lifecycleOwner,
                LocalAppServices provides services,
                LocalReduceMotion provides true,
            ) {
                Box(Modifier.width(320.dp)) {
                    IncomingCallScreen(
                        session = session,
                        agentName = "Kelo Assistent",
                    )
                }
            }
        }

        onNodeWithTag("call.incoming").assertIsDisplayed()
        onNodeWithTag("call.accept").assertIsDisplayed()
        onNodeWithTag("call.decline").assertIsDisplayed()
    }

    @Test
    fun testInCallScreenWrapping412dp() = runComposeUiTest {
        val scope = CoroutineScope(UnconfinedTestDispatcher())
        val session = CallSession(
            callId = "c1",
            topic = "긴 회의 안건에 대한 논의와 일정 조율",
            fromAgent = false,
            scope = scope,
            chat = FakeCallChat(),
            api = { null },
            audio = FakeCallAudio(),
            stt = VoiceModel({ null }, scope, { "ko" }, null),
            speech = SpeechModel({ null }, scope, null),
            phoneListener = null,
            device = { null },
            appLang = { "ko" },
            clock = { 1000L },
            log = {},
        )
        val services = fakeAppServices()
        val lifecycleOwner = fakeLifecycleOwner()

        setContent {
            CompositionLocalProvider(
                LocalLifecycleOwner provides lifecycleOwner,
                LocalAppServices provides services,
                LocalReduceMotion provides true,
            ) {
                Box(Modifier.width(412.dp)) {
                    InCallScreen(
                        session = session,
                        agentName = "Kelo",
                    )
                }
            }
        }

        onNodeWithTag("call.active").assertIsDisplayed()
        onNodeWithTag("call.hangup").assertIsDisplayed()
        onNodeWithTag("call.mute").assertIsDisplayed()
    }
}
