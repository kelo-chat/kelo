package chat.kelo.app.update

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** Where a download stands. */
enum class UpdatePhase { IDLE, DOWNLOADING, VERIFYING, READY, FAILED, MISMATCH }

/**
 * [offer]: a newer build than the one running (null: none, or not checked yet). [checking] / [checkFailed]: the last check.
 * [needsPermission]: the APK is ready but Android does not yet let Kelo hand it to the installer (the user is sent to the setting).
 * [play]: the offer comes from Google Play (no version or size of its own to show; READY means "restart to apply").
 * [required]: the server says this build is too old (`min_app_version`): the required-update screen covers the app.
 */
data class UpdateState(
    val enabled: Boolean = false,
    val running: String = "",
    val checking: Boolean = false,
    val checked: Boolean = false,
    val checkFailed: Boolean = false,
    val offer: Release? = null,
    val phase: UpdatePhase = UpdatePhase.IDLE,
    val progress: Int = 0,
    val needsPermission: Boolean = false,
    val bannerSeen: String? = null,
    val play: Boolean = false,
    val required: Boolean = false,
) {
    val showBanner: Boolean get() = enabled && !required && UpdateRules.showBanner(offer?.version, bannerSeen)
}

/**
 * The update check of the builds handed out as a file (see [UpdateRules]; a Play build never runs it). Reads the public version
 * list with its own plain HTTP client (no token, no language header, nothing about the user), downloads the APK into the app's
 * cache with progress, checks its SHA-256 against the list, and then opens Android's own installer, where the user confirms.
 * Nothing is ever installed silently.
 */
class AppUpdater(
    private val context: Context,
    flavor: String,
    private val running: String,
    base: String,
    private val versionCode: Long = 0,
    private val client: OkHttpClient = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build(),
) : AppUpdates {
    private val base = base.trimEnd('/')
    private val prefs = context.getSharedPreferences("kelo_update", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _state = MutableStateFlow(
        UpdateState(enabled = UpdateRules.supported(flavor), running = running, bannerSeen = prefs.getString(KEY_SEEN, null)),
    )
    override val state: StateFlow<UpdateState> = _state.asStateFlow()
    private var lastCheck: Long? = null
    private var download: Job? = null
    private val dir get() = File(context.cacheDir, "updates")

    /** Coming back to the app checks again when a day has passed. */
    override fun attach(activity: ComponentActivity) {
        activity.lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onResume(owner: LifecycleOwner) = check()
        })
    }

    /** Below the server's minimum: the required screen, with this build's own download (a fresh check finds it). */
    override fun requireAtLeast(minVersionCode: Long?) {
        if (!_state.value.enabled) return
        val required = UpdateRules.belowMin(versionCode, minVersionCode)
        val was = _state.value.required
        _state.update { it.copy(required = required) }
        if (required && !was) check(force = true)
    }

    /** At start and then once a day ([UpdateRules.due]); [force]: the user tapped 업데이트 확인. */
    override fun check(force: Boolean) {
        if (!_state.value.enabled || _state.value.checking) return
        val now = System.currentTimeMillis()
        if (!force && !UpdateRules.due(lastCheck, now)) return
        lastCheck = now
        _state.update { it.copy(checking = true, checkFailed = false) }
        scope.launch {
            val release = try {
                val req = Request.Builder().url(base + UpdateRules.LATEST_PATH).header("User-Agent", UpdateRules.userAgent(running))
                    .header("Cache-Control", "no-cache").build()
                client.newCall(req).execute().use { r -> if (r.isSuccessful) UpdateRules.parse(r.body?.string().orEmpty()) else throw IOException("HTTP ${r.code}") }
            } catch (e: IOException) {
                _state.update { it.copy(checking = false, checked = true, checkFailed = true) }
                return@launch
            }
            val offer = UpdateRules.offer(release, running, versionCode)
            _state.update { s ->
                // a new offer forgets a download of an older one
                val same = s.offer?.version == offer?.version
                s.copy(checking = false, checked = true, checkFailed = false, offer = offer,
                    phase = if (same) s.phase else UpdatePhase.IDLE, progress = if (same) s.progress else 0)
            }
        }
    }

    /** The banner was closed or used: it does not come back for this version. */
    override fun bannerSeen() {
        val v = _state.value.offer?.version ?: return
        prefs.edit().putString(KEY_SEEN, v).apply()
        _state.update { it.copy(bannerSeen = v) }
    }

    /**
     * 받기: download, check, then hand to the installer. A file already downloaded and checked goes to the installer at once. With
     * nothing offered yet (the required screen before a check found the build), it checks again.
     */
    override fun get() {
        val offer = _state.value.offer ?: return check(force = true)
        bannerSeen()
        if (_state.value.phase == UpdatePhase.READY && apk(offer).exists()) {
            install()
            return
        }
        if (download?.isActive == true) return
        _state.update { it.copy(phase = UpdatePhase.DOWNLOADING, progress = 0, needsPermission = false) }
        download = scope.launch {
            val target = apk(offer)
            val part = File(target.path + ".part")
            try {
                dir.mkdirs()
                dir.listFiles()?.filter { it.name != part.name }?.forEach { it.delete() }   // older downloads go
                val req = Request.Builder().url(UpdateRules.resolve(base, offer.url)).header("User-Agent", UpdateRules.userAgent(running)).build()
                val md = MessageDigest.getInstance("SHA-256")
                var done = 0L
                client.newCall(req).execute().use { r ->
                    if (!r.isSuccessful) throw IOException("HTTP ${r.code}")
                    val body = r.body ?: throw IOException("no body")
                    val total = body.contentLength().takeIf { it > 0 } ?: offer.size
                    body.byteStream().use { input ->
                        part.outputStream().use { out ->
                            val buf = ByteArray(64 * 1024)
                            while (isActive) {
                                val n = input.read(buf)
                                if (n < 0) break
                                out.write(buf, 0, n)
                                md.update(buf, 0, n)
                                done += n
                                if (done > offer.size) throw IOException("larger than published")
                                val p = UpdateRules.percent(done, total)
                                if (p != _state.value.progress) _state.update { it.copy(progress = p) }
                            }
                        }
                    }
                }
                _state.update { it.copy(phase = UpdatePhase.VERIFYING, progress = 100) }
                val sha = md.digest().joinToString("") { "%02x".format(it) }
                if (!UpdateRules.verified(sha, done, offer)) {
                    part.delete()
                    _state.update { it.copy(phase = UpdatePhase.MISMATCH) }
                    return@launch
                }
                if (!part.renameTo(target)) throw IOException("rename failed")
                _state.update { it.copy(phase = UpdatePhase.READY) }
                withContext(Dispatchers.Main) { install() }
            } catch (e: IOException) {
                part.delete()
                _state.update { it.copy(phase = UpdatePhase.FAILED) }
            }
        }
    }

    /** Opens Android's installer with the checked APK (the user confirms there); first the setting when installs are not allowed yet. */
    override fun install() {
        val offer = _state.value.offer ?: return
        val file = apk(offer)
        if (_state.value.phase != UpdatePhase.READY || !file.exists()) return
        if (!context.packageManager.canRequestPackageInstalls()) {
            _state.update { it.copy(needsPermission = true) }
            return
        }
        _state.update { it.copy(needsPermission = false) }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
        val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, APK_TYPE)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            context.startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            _state.update { it.copy(phase = UpdatePhase.FAILED) }
        }
    }

    /** "설정 열기" in the permission note: Android's per-app "install unknown apps" switch for Kelo. */
    override fun openInstallSetting() {
        _state.update { it.copy(needsPermission = false) }
        val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(intent) }
    }

    override fun dismissPermission() = _state.update { it.copy(needsPermission = false) }

    private fun apk(r: Release): File = File(dir, "kelo-${r.version.filter { it.isLetterOrDigit() || it == '.' }}.apk")

    companion object {
        private const val KEY_SEEN = "banner_seen"
        const val APK_TYPE = "application/vnd.android.package-archive"
    }
}
