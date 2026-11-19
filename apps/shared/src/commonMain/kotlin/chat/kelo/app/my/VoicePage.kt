package chat.kelo.app.my

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Slider
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppServices
import chat.kelo.app.call.SpeechModel
import chat.kelo.app.call.SpeechView
import chat.kelo.app.call.SpeechVoice
import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.core.Languages
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.MyCard
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.update.UpdateRules
import chat.kelo.app.voice.ModelView
import chat.kelo.app.voice.VoiceApi
import chat.kelo.app.voice.VoiceModel
import chat.kelo.app.voice.VoiceView
import chat.kelo.app.voice.Wav
import chat.kelo.app.voice.voiceDownloadingLine
import chat.kelo.app.voice.voiceProblemLine
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/**
 * My → 음성: speech recognition (STT) and Kelo's voice (TTS) on the Kelo server.
 * No licence text here.
 */
@Composable
fun VoicePage(voice: VoiceModel, speech: SpeechModel? = null, onBack: () -> Unit) {
    val c = Kelo.colors
    val services = LocalAppServices.current
    val coroutineScope = rememberCoroutineScope()
    val effectiveSpeech = speech ?: remember(services.sessions) {
        SpeechModel({ services.sessions.api.value }, coroutineScope)
    }

    val view by voice.view.collectAsState()
    val error by voice.error.collectAsState()
    val sView by effectiveSpeech.view.collectAsState()
    val sError by effectiveSpeech.error.collectAsState()

    var confirmRemove by remember { mutableStateOf(false) }
    var confirmRemoveSpeech by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        voice.refresh()
        effectiveSpeech.refresh()
    }

    // Audio preview for TTS voice rows
    var playingVoiceId by remember { mutableStateOf<Int?>(null) }
    val audioFactory = services.callAudio
    val audio = remember(audioFactory) { audioFactory?.invoke() }
    val sampleText = string(Res.string.voice_speech_sample)
    val appLang = services.language.current ?: services.language.systemShownCode

    DisposableEffect(audio) {
        onDispose {
            audio?.release()
        }
    }

    fun stopAudio() {
        audio?.stopPlayback()
        playingVoiceId = null
    }

    fun togglePlay(id: Int, speed: Double) {
        if (playingVoiceId == id) {
            stopAudio()
        } else {
            stopAudio()
            playingVoiceId = id
            coroutineScope.launch {
                try {
                    val client = services.sessions.api.value
                    if (client != null && audio != null) {
                        val voiceApi = VoiceApi(client)
                        val wav = voiceApi.speak(sampleText, appLang, voice = id, speed = speed)
                        val decoded = Wav.decode(wav)
                        if (decoded != null && playingVoiceId == id) {
                            audio.play(decoded.first, decoded.second) { /* level */ }
                        }
                    }
                } catch (e: Exception) {
                    // Ignore playback errors
                } finally {
                    if (playingVoiceId == id) {
                        playingVoiceId = null
                    }
                }
            }
        }
    }

    SubPage(string(Res.string.voice_page_title), onBack) {
        // --- 1. Speech Recognition (STT) Section ---
        MutedText(string(Res.string.voice_page_intro))
        val v: VoiceView? = view
        if (v == null) {
            MutedText(string(Res.string.voice_state_off))
        } else {
            MyCard {
                val current = v.model
                if (current != null) {
                    Text(current.name, color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.titleSmall)
                    MutedText(string(Res.string.voice_model_size, UpdateRules.size(current.sizeBytes), UpdateRules.size(current.memoryBytes)))
                    Text(stateLine(v), color = if (v.state == "failed") c.dangerInk else c.muted, style = androidx.compose.material3.MaterialTheme.typography.bodySmall)
                } else {
                    Text(string(Res.string.voice_state_off), color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium)
                    if (v.recommended != null) PrimaryButton(string(Res.string.voice_download_model), { voice.choose(v.recommended.id) }, Modifier.fillMaxWidth())
                }
                if (v.runtime != "ready") MutedText(string(Res.string.voice_runtime_missing))
                if (v.state == "unsupported") MutedText(string(Res.string.voice_phone_note))
            }
            if (v.models.isNotEmpty()) {
                MyCard {
                    Text(string(Res.string.voice_change_model), color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.titleSmall)
                    v.models.forEach { m -> ModelChoice(m, selected = v.model?.id == m.id, enabled = v.state != "downloading") { voice.choose(m.id) } }
                }
            }
            if (v.model != null) {
                SecondaryButton(string(Res.string.voice_remove_model), { confirmRemove = true }, Modifier.fillMaxWidth())
            }
        }
        error?.let { ErrorText(voiceProblemLine(it)) }

        // --- 2. Kelo's Voice (TTS) Section ---
        Spacer(Modifier.height(20.dp))
        Text(string(Res.string.voice_speech_title), color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.titleMedium)

        val sv: SpeechView? = sView
        if (sv == null || sv.state == "offered" || sv.state == "off") {
            MyCard {
                MutedText(string(Res.string.voice_speech_device_note))
                val sizeStr = sv?.model?.sizeBytes?.let { UpdateRules.size(it) } ?: "145 MB"
                PrimaryButton(
                    string(Res.string.voice_speech_download, sizeStr),
                    onClick = { effectiveSpeech.choose() },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        } else if (sv.state == "downloading") {
            MyCard {
                val progress = sv.progress
                val percent = progress?.let { if (it.total > 0) ((it.done * 100) / it.total).toInt() else 0 } ?: 0
                Row(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.5.dp, color = c.accent)
                    Text(string(Res.string.voice_speech_downloading, percent), color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium)
                }
            }
        } else if (sv.state == "failed") {
            MyCard {
                Text(
                    sv.error ?: string(Res.string.voice_server_download_failed),
                    color = c.dangerInk,
                    style = androidx.compose.material3.MaterialTheme.typography.bodySmall,
                )
                PrimaryButton(
                    string(Res.string.voice_retry),
                    onClick = { effectiveSpeech.choose() },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        } else if (sv.state == "ready") {
            val langSubtag = appLang.substringBefore('-').lowercase()
            val isSupported = sv.languages.isEmpty() || langSubtag in sv.languages || appLang in sv.languages
            if (!isSupported) {
                MyCard {
                    MutedText(string(Res.string.voice_speech_unsupported_lang))
                }
            } else {
                MyCard {
                    val voices = if (sv.voices.isNotEmpty()) sv.voices else {
                        (0..4).map { SpeechVoice(it, "female", it + 1) } + (5..9).map { SpeechVoice(it, "male", it - 4) }
                    }
                    voices.forEach { voiceItem ->
                        val selected = sv.voice == voiceItem.id
                        val label = if (voiceItem.kind == "female") {
                            string(Res.string.voice_speech_voice_female, voiceItem.n)
                        } else {
                            string(Res.string.voice_speech_voice_male, voiceItem.n)
                        }
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { effectiveSpeech.prefs(voice = voiceItem.id) }
                                .padding(vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(
                                selected = selected,
                                onClick = { effectiveSpeech.prefs(voice = voiceItem.id) },
                            )
                            Spacer(Modifier.size(8.dp))
                            Text(label, color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                            TextButton(onClick = { togglePlay(voiceItem.id, sv.speed) }) {
                                Text(
                                    string(if (playingVoiceId == voiceItem.id) Res.string.voice_speech_stop else Res.string.voice_speech_listen),
                                    color = c.accent,
                                )
                            }
                        }
                    }

                    // Speed slider: 0.7 - 1.5, step 0.1
                    Spacer(Modifier.height(8.dp))
                    var sliderSpeed by remember(sv.speed) { mutableFloatStateOf(sv.speed.toFloat()) }
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(string(Res.string.voice_speech_speed), color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium)
                        val speedStr = "%.1f".format(sliderSpeed)
                        Text(string(Res.string.voice_speech_speed_value, speedStr), color = c.muted, style = androidx.compose.material3.MaterialTheme.typography.bodySmall)
                    }
                    Slider(
                        value = sliderSpeed,
                        onValueChange = { sliderSpeed = ((it * 10f).roundToInt() / 10f).coerceIn(0.7f, 1.5f) },
                        onValueChangeFinished = { effectiveSpeech.prefs(speed = sliderSpeed.toDouble()) },
                        valueRange = 0.7f..1.5f,
                        steps = 7,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }

            SecondaryButton(
                string(Res.string.voice_speech_remove),
                onClick = { confirmRemoveSpeech = true },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        sError?.let { ErrorText(it) }
    }

    if (confirmRemove) {
        ConfirmDialog(
            text = string(Res.string.voice_remove_confirm),
            yes = string(Res.string.voice_remove_model),
            onYes = { voice.remove(); confirmRemove = false },
            onDismiss = { confirmRemove = false },
        )
    }

    if (confirmRemoveSpeech) {
        ConfirmDialog(
            text = string(Res.string.voice_speech_remove_confirm),
            yes = string(Res.string.voice_speech_remove),
            onYes = { effectiveSpeech.remove(); confirmRemoveSpeech = false },
            onDismiss = { confirmRemoveSpeech = false },
        )
    }
}

/** The state line under the model: the percent while it downloads, else "in use" or the phone's recognizer. */
@Composable
private fun stateLine(v: VoiceView): String {
    val p = v.progress
    return when (v.state) {
        "downloading" -> voiceDownloadingLine(p?.done ?: 0, p?.total ?: 0)
        "ready" -> string(Res.string.voice_model_in_use)
        "failed" -> string(Res.string.voice_server_download_failed)
        else -> string(Res.string.voice_state_off)
    }
}

@Composable
private fun ModelChoice(model: ModelView, selected: Boolean, enabled: Boolean, onPick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled && !selected, onClick = onPick)
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = if (enabled && !selected) onPick else null, enabled = enabled)
        Spacer(Modifier.size(8.dp))
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(model.name, color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium)
            MutedText(model.languages.joinToString(", ") { Languages.name(it) }, small = true)
            MutedText(string(Res.string.voice_model_size, UpdateRules.size(model.sizeBytes), UpdateRules.size(model.memoryBytes)), small = true)
        }
    }
}
