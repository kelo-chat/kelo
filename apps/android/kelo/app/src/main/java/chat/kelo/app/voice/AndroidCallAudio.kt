package chat.kelo.app.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import androidx.core.content.ContextCompat
import chat.kelo.app.call.platform.CallAudio
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import kotlin.math.PI
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Pure helper rules and audio calculations for [AndroidCallAudio].
 */
object AndroidCallAudioRules {
    /**
     * Picks the best communication device type to route to:
     * 1. Bluetooth SCO or BLE Headset if present
     * 2. Builtin speaker if speakerOn is true
     * 3. Builtin earpiece if speakerOn is false
     */
    fun pickCommunicationDeviceType(availableTypes: Collection<Int>, speakerOn: Boolean): Int? {
        if (AudioDeviceInfo.TYPE_BLUETOOTH_SCO in availableTypes) return AudioDeviceInfo.TYPE_BLUETOOTH_SCO
        if (AudioDeviceInfo.TYPE_BLE_HEADSET in availableTypes) return AudioDeviceInfo.TYPE_BLE_HEADSET
        return if (speakerOn) {
            if (AudioDeviceInfo.TYPE_BUILTIN_SPEAKER in availableTypes) AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
            else availableTypes.firstOrNull()
        } else {
            if (AudioDeviceInfo.TYPE_BUILTIN_EARPIECE in availableTypes) AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
            else availableTypes.firstOrNull()
        }
    }

    /**
     * Generates a 2-note sine chime (660 Hz then 880 Hz, 180 ms each, 20 ms fades, total 360 ms)
     * as 16-bit mono little-endian PCM audio.
     */
    fun generateChimePcm(sampleRate: Int = 16000): ByteArray {
        val note1Ms = 180
        val note2Ms = 180
        val fadeMs = 20
        val note1Samples = (sampleRate * note1Ms) / 1000
        val note2Samples = (sampleRate * note2Ms) / 1000
        val fadeSamples = (sampleRate * fadeMs) / 1000
        val totalSamples = note1Samples + note2Samples
        val out = ByteArray(totalSamples * 2)

        val amplitude = 18000.0 // audible and clean without clipping

        // Note 1: 660 Hz
        for (i in 0 until note1Samples) {
            val env = when {
                i < fadeSamples -> i.toDouble() / fadeSamples
                i > note1Samples - fadeSamples -> (note1Samples - 1 - i).toDouble() / fadeSamples
                else -> 1.0
            }
            val sample = (sin(2.0 * PI * 660.0 * i / sampleRate) * env * amplitude).toInt().coerceIn(-32768, 32767).toShort()
            out[i * 2] = (sample.toInt() and 0xFF).toByte()
            out[i * 2 + 1] = ((sample.toInt() shr 8) and 0xFF).toByte()
        }

        // Note 2: 880 Hz
        val offset = note1Samples
        for (i in 0 until note2Samples) {
            val env = when {
                i < fadeSamples -> i.toDouble() / fadeSamples
                i > note2Samples - fadeSamples -> (note2Samples - 1 - i).toDouble() / fadeSamples
                else -> 1.0
            }
            val sample = (sin(2.0 * PI * 880.0 * i / sampleRate) * env * amplitude).toInt().coerceIn(-32768, 32767).toShort()
            val sampleIdx = offset + i
            out[sampleIdx * 2] = (sample.toInt() and 0xFF).toByte()
            out[sampleIdx * 2 + 1] = ((sample.toInt() shr 8) and 0xFF).toByte()
        }

        return out
    }
}

/**
 * Android implementation of [CallAudio] for voice calls (brief §6.1).
 */
class AndroidCallAudio(private val context: Context, private val log: (String) -> Unit = {}) : CallAudio {
    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val previousAudioMode: Int = audioManager.mode
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override val micGranted: Boolean
        get() = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    override var echoCancel: Boolean = runCatching { AcousticEchoCanceler.isAvailable() }.getOrDefault(false)
        private set

    override val hasSpeakerSwitch: Boolean = true

    override var speakerOn: Boolean = true
        set(value) {
            field = value
            applyRouting(value)
        }

    override var onInterrupted: (() -> Unit)? = null

    // AudioRecord state
    private val recordLock = Any()
    private var record: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private var ns: NoiseSuppressor? = null
    private var reader: Thread? = null
    private var recordRate = 16000
    @Volatile private var recording = false

    // AudioTrack playback state
    private val playLock = Any()
    private var playTrack: AudioTrack? = null
    @Volatile private var playbackStopped = false

    // Chime state
    private var chimeJob: Job? = null
    private var chimeTrack: AudioTrack? = null
    private val vibrator: Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val vm = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
        vm?.defaultVibrator ?: context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
    }

    // Audio Focus
    private var focusRequest: AudioFocusRequest? = null
    private val focusListener = AudioManager.OnAudioFocusChangeListener { focusChange ->
        log("audio focus change=$focusChange")
        if (focusChange == AudioManager.AUDIOFOCUS_LOSS || focusChange == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) {
            onInterrupted?.invoke()
        }
    }

    init {
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        requestAudioFocus()
        applyRouting(speakerOn)
    }

    private fun requestAudioFocus() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setOnAudioFocusChangeListener(focusListener)
                .build()
            focusRequest = req
            audioManager.requestAudioFocus(req)
        } else {
            @Suppress("DEPRECATION")
            audioManager.requestAudioFocus(focusListener, AudioManager.STREAM_VOICE_CALL, AudioManager.AUDIOFOCUS_GAIN)
        }
    }

    private fun abandonAudioFocus() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
            focusRequest = null
        } else {
            @Suppress("DEPRECATION")
            audioManager.abandonAudioFocus(focusListener)
        }
    }

    private fun applyRouting(speaker: Boolean) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val devices = audioManager.availableCommunicationDevices
            val types = devices.map { it.type }
            val targetType = AndroidCallAudioRules.pickCommunicationDeviceType(types, speaker)
            val matched = devices.firstOrNull { it.type == targetType }
            if (matched != null) {
                audioManager.setCommunicationDevice(matched)
            }
        } else {
            @Suppress("DEPRECATION")
            audioManager.isSpeakerphoneOn = speaker
        }
    }

    override fun startMic(onChunk: (ByteArray) -> Unit): Boolean {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            return false
        }
        synchronized(recordLock) {
            stopMicLocked()
            val opened = openRecord(16000) ?: openRecord(48000) ?: return false
            record = opened.first
            recordRate = opened.second
            recording = true

            val r = opened.first
            val sessionId = r.audioSessionId
            if (AcousticEchoCanceler.isAvailable()) {
                aec = runCatching { AcousticEchoCanceler.create(sessionId)?.apply { enabled = true } }.getOrNull()
                echoCancel = aec?.enabled == true
            }
            if (NoiseSuppressor.isAvailable()) {
                ns = runCatching { NoiseSuppressor.create(sessionId)?.apply { enabled = true } }.getOrNull()
            }

            val frame = (3200 * recordRate) / 16000
            reader = Thread({
                val buf = ByteArray(frame)
                while (recording) {
                    val n = r.read(buf, 0, buf.size)
                    if (n <= 0) break
                    val pcm = Resample.to16k(buf.copyOf(n - n % 2), recordRate)
                    onChunk(pcm)
                }
            }, "android-call-mic").apply { start() }
            return true
        }
    }

    override fun stopMic() {
        synchronized(recordLock) {
            stopMicLocked()
        }
    }

    private fun stopMicLocked() {
        recording = false
        reader?.let { runCatching { it.join(1000) } }
        reader = null
        aec?.let { runCatching { it.release() } }
        aec = null
        ns?.let { runCatching { it.release() } }
        ns = null
        record?.let {
            runCatching { it.stop() }
            runCatching { it.release() }
        }
        record = null
    }

    private fun openRecord(sampleRate: Int): Pair<AudioRecord, Int>? {
        val min = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (min <= 0) return null
        val size = maxOf(min, 3200 * sampleRate / 16000 * 4)
        val r = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                size
            )
        } catch (_: IllegalArgumentException) {
            return null
        }
        if (r.state != AudioRecord.STATE_INITIALIZED) {
            r.release()
            return null
        }
        r.startRecording()
        return r to sampleRate
    }

    override suspend fun play(pcm16le: ByteArray, sampleRate: Int, onLevel: (Float) -> Unit): Unit = withContext(Dispatchers.IO) {
        playbackStopped = false
        val minBuf = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val pieceBytes = ((sampleRate * 20L) / 1000L).toInt() * 2
        val bufSize = maxOf(minBuf, pieceBytes * 4)

        val track = AudioTrack(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build(),
            AudioFormat.Builder()
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(sampleRate)
                .build(),
            bufSize,
            AudioTrack.MODE_STREAM,
            AudioManager.AUDIO_SESSION_ID_GENERATE
        )
        if (track.state != AudioTrack.STATE_INITIALIZED) { log("audio track not ready rate=$sampleRate"); runCatching { track.release() }; return@withContext }
        synchronized(playLock) {
            playTrack = track
        }

        try {
            track.play()
            var offset = 0
            while (offset < pcm16le.size && !playbackStopped) {
                val len = minOf(pieceBytes, pcm16le.size - offset)
                val written = track.write(pcm16le, offset, len)
                if (written > 0) {
                    var sumSq = 0.0
                    val samples = written / 2
                    for (i in 0 until written step 2) {
                        val sampleVal = ((pcm16le[offset + i].toInt() and 0xFF) or (pcm16le[offset + i + 1].toInt() shl 8)).toShort()
                        val s = sampleVal.toDouble() / 32768.0
                        sumSq += s * s
                    }
                    val rms = if (samples > 0) sqrt(sumSq / samples).toFloat() else 0f
                    val level = RecordingBarRules.level(rms)
                    onLevel(level)
                    offset += written
                } else {
                    if (!playbackStopped) log("audio write failed code=$written")
                    break
                }
            }
            // Wait briefly for the audio pipeline to clear
            if (!playbackStopped) {
                delay(50)
            }
        } finally {
            onLevel(0f)
            synchronized(playLock) {
                runCatching { track.stop() }
                runCatching { track.release() }
                if (playTrack === track) playTrack = null
            }
        }
    }

    override fun stopPlayback() {
        playbackStopped = true
        synchronized(playLock) {
            playTrack?.let {
                runCatching { it.pause() }
                runCatching { it.flush() }
            }
        }
    }

    override fun chime(on: Boolean) {
        chimeJob?.cancel()
        chimeJob = null
        synchronized(playLock) {
            chimeTrack?.let {
                runCatching { it.stop() }
                runCatching { it.release() }
            }
            chimeTrack = null
        }
        vibrator?.cancel()

        if (!on) return

        val ringerMode = audioManager.ringerMode
        if (ringerMode == AudioManager.RINGER_MODE_SILENT) return

        // Vibration unless silent
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            vibrator?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 250, 1750), 0))
        } else {
            @Suppress("DEPRECATION")
            vibrator?.vibrate(longArrayOf(0, 250, 1750), 0)
        }

        // Chime audio only when normal ringer mode
        if (ringerMode == AudioManager.RINGER_MODE_NORMAL) {
            chimeJob = scope.launch(Dispatchers.IO) {
                val sampleRate = 16000
                val pcm = AndroidCallAudioRules.generateChimePcm(sampleRate)
                val minBuf = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
                val track = AudioTrack(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build(),
                    AudioFormat.Builder()
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sampleRate)
                        .build(),
                    maxOf(minBuf, pcm.size * 2),
                    AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE
                )
                synchronized(playLock) {
                    chimeTrack = track
                }
                track.play()
                try {
                    while (isActive) {
                        track.write(pcm, 0, pcm.size)
                        // 360 ms played, pause until 2000 ms cycle ends (1640 ms)
                        delay(1640)
                    }
                } finally {
                    runCatching { track.stop() }
                    runCatching { track.release() }
                }
            }
        }
    }

    override fun release() {
        stopMic()
        stopPlayback()
        chime(false)
        scope.cancel()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audioManager.clearCommunicationDevice()
        }
        audioManager.mode = previousAudioMode
        abandonAudioFocus()
    }
}
