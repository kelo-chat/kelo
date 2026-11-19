package chat.kelo.app.voice

import android.widget.Toast
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.ui.platform.LocalHaptics
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged

/**
 * The composer's voice on Android, one per chat screen (above the composer, so it lives while the bar replaces the mic row): the
 * server's recording, the phone's own recognizer, the speech model's state, and the [composer] that the bar and the mic share.
 */
@Stable
class AndroidComposerVoice internal constructor(
    val model: VoiceModel,
    val server: ServerMicController,
    val phone: PhoneBarSource,
    val composer: ComposerVoice,
) {
    /** The phone's recognizer (the mic asks it whether it is there at all). */
    val speech: SpeechInput get() = phone.speech

    /** A hold-to-talk finger is down: the composer keeps its normal look while it is held. */
    var held: Boolean = false
        set(value) {
            field = value
            server.held = value
            phone.held = value
        }

    /** The recording ends the way the owner asked: ■ (FIELD) or ↑ (SEND). Whichever source is recording. */
    fun finish(after: AfterSpeech) {
        if (server.recording) server.finish(after) else phone.finish(after)
    }

    /** ✕, or a slide off the held mic: whichever source is recording drops it. */
    fun discard() {
        server.discard()
        phone.discard()
    }

    /** True while either source listens (the bar's listening stage; the header's character listens too). */
    fun listening(): Flow<Boolean> =
        combine(server.bar, phone.bar) { s, p -> s?.stage == RecordingBarState.Stage.LISTENING || p != null }.distinctUntilChanged()
}

/** The composer reference the two sources need before the composer exists (it is made from their sources). */
private class ComposerRef {
    private var composer: ComposerVoice? = null
    fun set(v: ComposerVoice) { composer = v }
    fun get(): ComposerVoice = composer ?: error("the composer voice is not made yet")
}

/**
 * The composer's voice for this chat screen: remembered above the composer, so the bar (which replaces the mic row) does not dispose
 * the recorder, the recognizer or the level trail. Android's recognizer errors come as a toast (the problem text), and the haptic
 * tick is given when a recognition ends.
 */
@Composable
fun rememberAndroidComposerVoice(): AndroidComposerVoice {
    val context = LocalContext.current
    val container = LocalAppContainer.current
    val scope = rememberCoroutineScope()
    val haptics = LocalHaptics.current
    val apiNow by container.sessions.api.collectAsState()
    val model = remember(container) {
        VoiceModel({ container.sessions.api.value }, scope) {
            chat.kelo.app.my.NoticesRules.language(container.language.current, container.language.systemShownCode)
        }
    }
    LaunchedEffect(apiNow) { model.refresh() }
    val problemText = mapOf(
        SpeechProblem.NO_MATCH to stringResource(R.string.voice_no_match),
        SpeechProblem.NETWORK to stringResource(R.string.voice_dictation_offline),
        SpeechProblem.BUSY to stringResource(R.string.voice_busy),
        SpeechProblem.OTHER to stringResource(R.string.voice_dictation_failed),
    )
    val endedHandler by rememberUpdatedState<(SpeechProblem?) -> Unit> { problem ->
        haptics.confirm()
        problem?.let { Toast.makeText(context, problemText[it], Toast.LENGTH_SHORT).show() }
    }
    val recorder = remember { PhoneRecorder(context) }
    val avc = remember(recorder) {
        val ref = ComposerRef()
        val server = ServerMicController(
            recorder,
            { container.sessions.api.value?.let { VoiceApi(it) } },
            scope,
            onBegin = { ref.get().begin() },
            onText = { words, after -> ref.get().deliver(words, after) },
        )
        val phone = PhoneBarSource(scope, composer = { ref.get() }, onEnded = { endedHandler(it) })
        AndroidComposerVoice(model, server, phone, ComposerVoice(listOf(server, phone))).also { ref.set(it.composer) }
    }
    // the phone's recognizer: its callbacks point back at the phone source above
    val speech = rememberSpeechInput(
        onWords = { words, _ -> avc.phone.words(words) },
        onEnd = { problem -> avc.phone.ended(problem) },
    )
    avc.phone.speech = speech

    // the header's character listens while the owner speaks (set here, so it is not reset when the mic leaves the composition)
    val activity = container.agentActivity
    LaunchedEffect(avc) { avc.listening().collect { activity.listening.value = it } }
    DisposableEffect(activity) { onDispose { activity.listening.value = false } }
    return avc
}
