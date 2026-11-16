package chat.kelo.app.update

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.google.android.gms.tasks.Task
import com.google.android.play.core.appupdate.AppUpdateInfo
import com.google.android.play.core.appupdate.AppUpdateManager
import com.google.android.play.core.appupdate.AppUpdateOptions
import com.google.android.play.core.install.InstallStateUpdatedListener
import com.google.android.play.core.install.model.ActivityResult
import com.google.android.play.core.install.model.AppUpdateType
import com.google.android.play.core.install.model.InstallStatus
import com.google.android.play.core.ktx.isFlexibleUpdateAllowed
import com.google.android.play.core.ktx.isImmediateUpdateAllowed
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.util.concurrent.Executor

/**
 * The Play build's updates through Google Play's In-App Updates ([PlayUpdateRules] decides). Kelo never downloads or installs an
 * APK here: Play does, and only for a build installed from Play (internal testing, internal app sharing or the store). Elsewhere
 * (a debug build from Android Studio) Play answers "no update" or fails, and nothing shows.
 *
 * [executor] runs Play's answers (the main thread; tests run them in place). [prefs] may be null (tests).
 */
class PlayAppUpdates(
    private val context: Context,
    private val manager: AppUpdateManager,
    private val versionCode: Long,
    running: String,
    private val executor: Executor,
    private val prefs: SharedPreferences?,
    private val clock: () -> Long = System::currentTimeMillis,
) : AppUpdates {
    private val _state = MutableStateFlow(
        UpdateState(enabled = true, play = true, running = running, bannerSeen = prefs?.getString(KEY_SEEN, null)),
    )
    override val state: StateFlow<UpdateState> = _state.asStateFlow()

    private var launcher: ActivityResultLauncher<IntentSenderRequest>? = null
    private var lastCheck: Long? = null
    /** The flow this app started last (AppUpdateType), kept across restarts to tell a flexible download from an immediate update */
    private var startedType: Int? = prefs?.getInt(KEY_TYPE, -1)?.takeIf { it >= 0 }
    /** A required update starts Play's immediate screen by itself once; after the user closes it, only the button starts it again */
    private var autoImmediate = true

    private val listener = InstallStateUpdatedListener { st -> onInstallState(st.installStatus(), st.bytesDownloaded(), st.totalBytesToDownload()) }

    init {
        manager.registerListener(listener)   // the app's container lives as long as the process: never unregistered
    }

    override fun attach(activity: ComponentActivity) {
        val l = activity.registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { onFlowResult(it.resultCode) }
        useLauncher(l)
        activity.lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onResume(owner: LifecycleOwner) = onForeground()
            override fun onDestroy(owner: LifecycleOwner) {
                if (launcher === l) launcher = null
            }
        })
    }

    /** The launcher Play's update screens are started with (attach; tests give their own). */
    fun useLauncher(l: ActivityResultLauncher<IntentSenderRequest>?) {
        launcher = l
    }

    /** Coming back to the app: a full check when due; otherwise only a finished download or an update left half-way. */
    fun onForeground() {
        if (PlayUpdateRules.due(lastCheck, clock())) check() else ask(offerNew = false, userAsked = false)
    }

    override fun check(force: Boolean) {
        val now = clock()
        if (!force && !PlayUpdateRules.due(lastCheck, now)) return
        lastCheck = now
        ask(offerNew = true, userAsked = force)
    }

    override fun requireAtLeast(minVersionCode: Long?) {
        val required = UpdateRules.belowMin(versionCode, minVersionCode)
        val was = _state.value.required
        _state.update { it.copy(required = required) }
        if (required && !was) {
            autoImmediate = true
            check(force = true)
        }
    }

    override fun bannerSeen() {
        val v = _state.value.offer?.version ?: return
        prefs?.edit()?.putString(KEY_SEEN, v)?.apply()
        _state.update { it.copy(bannerSeen = v) }
    }

    /**
     * 업데이트 (banner, card, required screen): a finished download restarts into it; otherwise Play's flow starts with a fresh answer
     * (an AppUpdateInfo starts one flow only). When Play offers nothing this app can start, Kelo's Play Store page opens.
     */
    override fun get() {
        val s = _state.value
        bannerSeen()
        when (s.phase) {
            UpdatePhase.READY -> return install()
            UpdatePhase.DOWNLOADING -> return
            else -> {}
        }
        autoImmediate = true
        manager.appUpdateInfo.whenDone { task ->
            val info = task.resultOrNull()
            val type = info?.let { PlayUpdateRules.flowType(it.updateAvailability(), it.isFlexibleUpdateAllowed, it.isImmediateUpdateAllowed, _state.value.required) }
            if (info == null || type == null || !startFlow(info, type)) openStore()
        }
    }

    /** "다시 시작": Play installs the downloaded update and restarts Kelo. */
    override fun install() {
        manager.completeUpdate()
    }

    /** The result of Play's update screen. */
    fun onFlowResult(resultCode: Int) {
        when (resultCode) {
            Activity.RESULT_OK -> {}   // flexible: the download follows through the listener; immediate: Play restarts the app
            Activity.RESULT_CANCELED -> if (startedType == AppUpdateType.IMMEDIATE) autoImmediate = false else bannerSeen()
            ActivityResult.RESULT_IN_APP_UPDATE_FAILED -> _state.update { it.copy(phase = UpdatePhase.FAILED) }
        }
    }

    private fun ask(offerNew: Boolean, userAsked: Boolean) {
        if (userAsked) _state.update { it.copy(checking = true, checkFailed = false) }
        manager.appUpdateInfo.whenDone { task ->
            val info = task.resultOrNull()
            if (info == null) {
                if (offerNew) _state.update { it.copy(checking = false, checked = true, checkFailed = true) }
                return@whenDone
            }
            apply(info, offerNew)
        }
    }

    private fun apply(info: AppUpdateInfo, offerNew: Boolean) {
        val s = _state.value
        val action = PlayUpdateRules.decide(
            info.updateAvailability(), info.installStatus(), info.isFlexibleUpdateAllowed, info.isImmediateUpdateAllowed,
            s.required, startedFlexible = startedType == AppUpdateType.FLEXIBLE,
        )
        val offer = Release(version = info.availableVersionCode().toString(), size = info.totalBytesToDownload())
        _state.update { cur ->
            val base = cur.copy(checking = false, checked = cur.checked || offerNew, checkFailed = false)
            when (action) {
                PlayUpdateRules.Action.NONE -> base.copy(offer = null, phase = UpdatePhase.IDLE, progress = 0)
                PlayUpdateRules.Action.OFFER, PlayUpdateRules.Action.START_IMMEDIATE, PlayUpdateRules.Action.BLOCK ->
                    if (offerNew || cur.offer != null || cur.required) {
                        base.copy(offer = if (action == PlayUpdateRules.Action.BLOCK && info.availableVersionCode() <= 0) null else offer,
                            phase = if (cur.phase == UpdatePhase.FAILED) UpdatePhase.FAILED else UpdatePhase.IDLE)
                    } else base
                PlayUpdateRules.Action.DOWNLOADING ->
                    base.copy(offer = offer, phase = UpdatePhase.DOWNLOADING, progress = UpdateRules.percent(info.bytesDownloaded(), info.totalBytesToDownload()))
                PlayUpdateRules.Action.READY ->
                    // a download that just finished brings the banner back once, now with 다시 시작
                    base.copy(offer = offer, phase = UpdatePhase.READY, progress = 100, bannerSeen = if (cur.phase == UpdatePhase.READY) cur.bannerSeen else null)
                PlayUpdateRules.Action.RESUME_IMMEDIATE -> base.copy(offer = offer)
            }
        }
        when (action) {
            PlayUpdateRules.Action.RESUME_IMMEDIATE -> startFlow(info, AppUpdateType.IMMEDIATE)
            PlayUpdateRules.Action.START_IMMEDIATE -> if (autoImmediate) startFlow(info, AppUpdateType.IMMEDIATE)
            else -> {}
        }
    }

    private fun startFlow(info: AppUpdateInfo, type: Int): Boolean {
        val l = launcher ?: return false   // no activity yet: its resume asks again
        val started = runCatching { manager.startUpdateFlowForResult(info, l, AppUpdateOptions.newBuilder(type).build()) }.getOrDefault(false)
        if (started) {
            startedType = type
            prefs?.edit()?.putInt(KEY_TYPE, type)?.apply()
        }
        return started
    }

    private fun onInstallState(status: Int, bytes: Long, total: Long) {
        _state.update { cur ->
            when (status) {
                InstallStatus.PENDING, InstallStatus.DOWNLOADING -> cur.copy(phase = UpdatePhase.DOWNLOADING, progress = UpdateRules.percent(bytes, total))
                InstallStatus.DOWNLOADED -> cur.copy(phase = UpdatePhase.READY, progress = 100, bannerSeen = if (cur.phase == UpdatePhase.READY) cur.bannerSeen else null)
                InstallStatus.INSTALLED -> cur.copy(offer = null, phase = UpdatePhase.IDLE, progress = 0)
                InstallStatus.FAILED -> cur.copy(phase = UpdatePhase.FAILED)
                InstallStatus.CANCELED -> cur.copy(phase = UpdatePhase.IDLE, progress = 0)
                else -> cur   // INSTALLING: Play restarts the app
            }
        }
    }

    /** Kelo's page in the Play Store (the Play app, or the web page when it is missing). */
    private fun openStore() {
        val pkg = context.packageName
        val market = Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$pkg"))
        market.setPackage(PLAY_STORE)
        market.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            context.startActivity(market)
        } catch (_: ActivityNotFoundException) {
            val web = Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=$pkg"))
            web.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { context.startActivity(web) }
        }
    }

    private fun <T> Task<T>.whenDone(block: (Task<T>) -> Unit) {
        addOnCompleteListener(executor) { block(it) }
    }

    private fun <T> Task<T>.resultOrNull(): T? = if (isSuccessful) result else null

    companion object {
        private const val KEY_SEEN = "play_banner_seen"
        private const val KEY_TYPE = "play_flow_type"
        private const val PLAY_STORE = "com.android.vending"
    }
}
