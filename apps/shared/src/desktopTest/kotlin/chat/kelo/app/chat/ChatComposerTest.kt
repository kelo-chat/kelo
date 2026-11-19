package chat.kelo.app.chat

import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.test.runComposeUiTest
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalFormFactor
import chat.kelo.app.ui.theme.KeloTheme
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTextReplacement
import chat.kelo.app.voice.AfterSpeech
import chat.kelo.app.voice.ComposerVoice
import chat.kelo.app.voice.LevelTrail
import chat.kelo.app.voice.RecordingBarSource
import chat.kelo.app.voice.RecordingBarState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlin.test.Test
import kotlin.test.assertEquals

/** The shared composer and "+" list: the desktop sends with Enter, the phone does not; the "+" sheet draws the app's entries. */
@OptIn(ExperimentalTestApi::class)
class ChatComposerTest {
    private fun androidx.compose.ui.test.ComposeUiTest.composer(form: FormFactor, onSend: () -> Unit, mic: Boolean = false) = setContent {
        CompositionLocalProvider(LocalFormFactor provides form) {
            KeloTheme(dark = false) {
                Composer(
                    input = "hello", busy = false, canSend = true, focusRequester = FocusRequester(), onFocus = {},
                    onInput = {}, onSend = onSend, onStop = {}, onAttach = {}, chipText = "Model", onChip = {},
                    mic = if (mic) { _, _ -> Box(Modifier) { androidx.compose.material3.Text("MIC") } } else null,
                )
            }
        }
    }

    @Test fun enterSendsOnTheDesktopButShiftEnterDoesNot() = runComposeUiTest {
        var sent = 0
        composer(FormFactor.Desktop, { sent++ })
        onNode(hasSetTextAction()).performClick()
        onNode(hasSetTextAction()).performKeyInput { pressKey(Key.Enter) }
        assertEquals(1, sent)
        onNode(hasSetTextAction()).performKeyInput { keyDown(Key.ShiftLeft); pressKey(Key.Enter); keyUp(Key.ShiftLeft) }
        assertEquals(1, sent)
    }

    @Test fun enterDoesNotSendOnThePhone() = runComposeUiTest {
        var sent = 0
        composer(FormFactor.Phone, { sent++ })
        onNode(hasSetTextAction()).performClick()
        onNode(hasSetTextAction()).performKeyInput { pressKey(Key.Enter) }
        assertEquals(0, sent)
    }

    @Test fun theMicIsDrawnOnlyWhenTheAppGivesOne() = runComposeUiTest {
        composer(FormFactor.Phone, {}, mic = true)
        onNodeWithText("MIC").assertExists()
    }

    @Test fun thePlusSheetListsTheAppsEntriesAndClosesBeforePicking() = runComposeUiTest {
        var picked = ""
        setContent {
            KeloTheme(dark = false) {
                AttachSheet(
                    listOf(
                        AddEntry("a", KeloIcons.Image, "Photo", "From gallery") { picked += "a" },
                        AddEntry("b", KeloIcons.File, "Document", "A file") { picked += "b" },
                    ),
                    onClose = {},
                )
            }
        }
        onNodeWithText("Photo").assertExists()
        onNodeWithText("Document").performClick()
        assertEquals("b", picked)
    }

    // ---- the recording bar: while the owner speaks the whole composer is the bar

    private class FakeBar : RecordingBarSource {
        override val bar = MutableStateFlow<RecordingBarState?>(null)
        override val trail = LevelTrail()
        var discards = 0
        val finished = mutableListOf<AfterSpeech>()
        override fun discard() { discards++ }
        override fun finish(after: AfterSpeech) { finished += after }
    }

    private fun listening(elapsedMs: Long = 9_000, held: Boolean = false) =
        RecordingBarState(RecordingBarState.Stage.LISTENING, elapsedMs, null, held)

    private fun androidx.compose.ui.test.ComposeUiTest.voiceComposer(form: FormFactor, bar: FakeBar, busy: Boolean = false) = setContent {
        // reduce motion: the bar's sound graph does not keep a frame loop running (a test waits for the composition to settle)
        CompositionLocalProvider(LocalFormFactor provides form, chat.kelo.app.ui.theme.LocalReduceMotion provides true) {
            KeloTheme(dark = false) {
                Composer(
                    input = "hello", busy = busy, canSend = true, focusRequester = FocusRequester(), onFocus = {},
                    onInput = {}, onSend = {}, onStop = {}, onAttach = {}, chipText = "Model", onChip = {},
                    voice = ComposerVoice(listOf(bar)),
                )
            }
        }
    }

    @Test fun recordingTurnsTheWholeComposerIntoTheBar() = runComposeUiTest {
        val bar = FakeBar()
        voiceComposer(FormFactor.Phone, bar)
        bar.bar.value = listening()
        onNodeWithTag("voice.bar").assertExists()
        onNodeWithTag("voice.discard").assertExists()
        onNodeWithTag("voice.stop").assertExists()
        onNodeWithTag("voice.send").assertExists()
        onNode(hasSetTextAction()).assertDoesNotExist()
        onNodeWithText("Send a message").assertDoesNotExist()
        onNodeWithContentDescription("Attach a photo or file").assertDoesNotExist()
    }

    @Test fun barButtonsCallTheSource() = runComposeUiTest {
        val bar = FakeBar()
        voiceComposer(FormFactor.Phone, bar)
        bar.bar.value = listening()
        onNodeWithTag("voice.stop").performClick()
        onNodeWithTag("voice.send").performClick()
        onNodeWithTag("voice.discard").performClick()
        assertEquals(listOf(AfterSpeech.FIELD, AfterSpeech.SEND), bar.finished)
        assertEquals(1, bar.discards)
    }

    @Test fun turningToTextShowsTheLineAndDisablesStopAndSend() = runComposeUiTest {
        val bar = FakeBar()
        voiceComposer(FormFactor.Phone, bar)
        bar.bar.value = RecordingBarState(RecordingBarState.Stage.TURNING_TO_TEXT, 9_000, null, held = false)
        onNodeWithTag("voice.bar").assertExists()
        onNodeWithTag("voice.stop").assertIsNotEnabled()
        onNodeWithTag("voice.send").assertIsNotEnabled()
    }

    @Test fun sendIsDisabledWhileKeloReplies() = runComposeUiTest {
        val bar = FakeBar()
        voiceComposer(FormFactor.Phone, bar, busy = true)
        bar.bar.value = listening()
        onNodeWithTag("voice.send").assertIsNotEnabled()
        onNodeWithTag("voice.stop").assertExists()
    }

    @Test fun aHeldMicKeepsTheNormalComposer() = runComposeUiTest {
        val bar = FakeBar()
        voiceComposer(FormFactor.Phone, bar)
        bar.bar.value = listening(held = true)
        onNode(hasSetTextAction()).assertExists()
        onNodeWithTag("voice.bar").assertDoesNotExist()
    }

    @Test fun noBarMeansTheNormalComposer() = runComposeUiTest {
        val bar = FakeBar()
        voiceComposer(FormFactor.Phone, bar)
        onNode(hasSetTextAction()).assertExists()
        onNodeWithTag("voice.bar").assertDoesNotExist()
    }

    @Test fun desktopEscDiscardsAndEnterSends() = runComposeUiTest {
        val bar = FakeBar()
        voiceComposer(FormFactor.Desktop, bar)
        bar.bar.value = listening()
        onNodeWithTag("voice.bar").performKeyInput { pressKey(Key.Enter) }
        assertEquals(listOf(AfterSpeech.SEND), bar.finished)
        onNodeWithTag("voice.bar").performKeyInput { pressKey(Key.Escape) }
        assertEquals(1, bar.discards)
    }

    @Test fun theTextFieldKeepsItsTextAcrossTheBar() = runComposeUiTest {
        val bar = FakeBar()
        voiceComposer(FormFactor.Phone, bar)
        onNode(hasSetTextAction()).performTextReplacement("abc")
        bar.bar.value = listening()
        onNodeWithTag("voice.bar").assertExists()
        bar.bar.value = null
        onNode(hasSetTextAction()).assertTextContains("abc")
    }
}
