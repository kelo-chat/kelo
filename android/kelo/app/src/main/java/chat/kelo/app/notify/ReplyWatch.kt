package chat.kelo.app.notify

import android.app.Activity
import android.app.Application
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import androidx.core.content.ContextCompat
import chat.kelo.app.R
import chat.kelo.app.chat.ReplyLive
import chat.kelo.app.chat.ReplyLiveRules
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.chat.TurnWatch
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Whether any of the app's screens is on screen (counted from the activities' start / stop). */
class AppVisibility : Application.ActivityLifecycleCallbacks {
    private var started = 0
    private val _visible = MutableStateFlow(false)
    val visible: StateFlow<Boolean> = _visible.asStateFlow()
    /** Called when the last screen goes away for real (not a rotation) */
    var onHidden: () -> Unit = {}
    var onShown: () -> Unit = {}

    override fun onActivityStarted(activity: Activity) {
        if (started++ == 0) { _visible.value = true; onShown() }
    }

    override fun onActivityStopped(activity: Activity) {
        started = (started - 1).coerceAtLeast(0)
        if (started == 0 && !activity.isChangingConfigurations) { _visible.value = false; onHidden() }
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
    override fun onActivityResumed(activity: Activity) {}
    override fun onActivityPaused(activity: Activity) {}
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
    override fun onActivityDestroyed(activity: Activity) {}
}

/** The decisions of [ReplyWatch], apart from Android so they can be tested. */
object ReplyRules {
    sealed interface Post {
        data class Done(val firstLine: String) : Post
        data object Failed : Post
        data class Approval(val label: String, val payment: Boolean) : Post
        /** The agent waits for a delivery address (an order); [what]: what is ordered, else the site */
        data class Shipping(val what: String, val formId: String) : Post
    }

    /** What to post when the reply ends: nothing while the app is on screen. */
    fun onFinish(ok: Boolean, sawError: Boolean, visible: Boolean, answer: String): Post? = when {
        visible -> null
        ok && !sawError -> Post.Done(NotifyRules.firstLine(answer))
        else -> Post.Failed
    }

    /** An approval the followed reply asks for while the app is away. */
    fun onApproval(ev: TurnEvent.ApprovalRequest, visible: Boolean): Post? =
        if (visible) null else Post.Approval(ev.label.ifEmpty { ev.name }, ev.payment != null)

    /** An address question the followed reply waits on while the app is away. */
    fun onShipping(ev: TurnEvent.ShippingForm, visible: Boolean): Post? =
        if (visible) null else Post.Shipping(ev.ask.what.ifBlank { ev.ask.site }, ev.ask.formId)

    /** At most one notification update a second (Android drops faster ones) */
    const val UPDATE_EVERY_MS = 1_000L

    /** The foreground service is started when the app goes away while a reply is followed. */
    fun startService(following: Boolean, visible: Boolean): Boolean = following && !visible
}

/**
 * Follows the chat's reply for notifications. The chat's own [chat.kelo.app.chat.TurnFollower] keeps following the reply while the
 * app is away; when the app goes to the background during a reply, [ReplyService] (a short-lived foreground service with a quiet
 * ongoing line) keeps the process alive so it can finish, and this posts "reply done" (its first line) or "failed", or an approval
 * that waits. Nothing is posted while the app is on screen.
 */
class ReplyWatch(private val context: Context, private val notifier: Notifier, private val visibility: AppVisibility) : TurnWatch {
    private val _following = MutableStateFlow(false)
    val following: StateFlow<Boolean> = _following.asStateFlow()
    private val answer = StringBuilder()
    private var sawError = false

    /** The step, site and newest picture of the reply that runs (shown in the ongoing notification while the app is away). */
    @Volatile var live: ReplyLive = ReplyLive()
        private set
    private var stopper: (() -> Unit)? = null
    private var lastPostMs = 0L

    init {
        visibility.onHidden = { if (ReplyRules.startService(_following.value, false)) startService() }
        visibility.onShown = {
            // back in the app: the chat shows the reply itself
            notifier.cancel(Notifier.REPLY_ID)
        }
    }

    override fun started() {
        answer.clear()
        sawError = false
        live = ReplyLive()
        _following.value = true
        notifier.cancel(Notifier.REPLY_ID)
    }

    override fun stopWith(stop: () -> Unit) {
        stopper = stop
    }

    /** The notification's Stop. It runs on the main thread, from the service's onStartCommand. */
    fun stopNow() {
        stopper?.invoke()
    }

    override fun event(ev: TurnEvent) {
        ReplyLiveRules.next(live, ev, SystemClock.elapsedRealtime())?.let { s ->
            live = s
            val now = SystemClock.elapsedRealtime()
            if (_following.value && !visibility.visible.value && now - lastPostMs >= ReplyRules.UPDATE_EVERY_MS) {
                lastPostMs = now
                notifier.updateWorking(s)
            }
        }
        when (ev) {
            is TurnEvent.Text -> answer.append(ev.text)
            is TurnEvent.Error, is TurnEvent.StepLimit -> sawError = true
            is TurnEvent.ApprovalRequest -> ReplyRules.onApproval(ev, visibility.visible.value)?.let(::post)
            is TurnEvent.ShippingForm -> ReplyRules.onShipping(ev, visibility.visible.value)?.let(::post)
            else -> {}
        }
    }

    override fun finished(ok: Boolean) {
        _following.value = false
        stopper = null
        runCatching { notifier.cancel(Notifier.WORKING_ID) }   // a step update that raced the end never leaves the ongoing line behind
        ReplyRules.onFinish(ok, sawError, visibility.visible.value, answer.toString())?.let(::post)
        answer.clear()
    }

    /** The user stopped the reply (or left it): nothing to tell. */
    override fun cancelled() {
        _following.value = false
        stopper = null
        runCatching { notifier.cancel(Notifier.WORKING_ID) }
        answer.clear()
    }

    private fun post(p: ReplyRules.Post) {
        when (p) {
            is ReplyRules.Post.Done -> notifier.post(
                Notifier.REPLY_ID, Channel.REPLIES, context.getString(R.string.notify_reply_done),
                p.firstLine.ifEmpty { context.getString(R.string.notify_open_hint) }, OpenTarget.Chat,
            )
            ReplyRules.Post.Failed -> notifier.post(
                Notifier.REPLY_ID, Channel.REPLIES, context.getString(R.string.notify_reply_failed),
                context.getString(R.string.notify_reply_failed_body), OpenTarget.Chat,
            )
            is ReplyRules.Post.Approval -> notifier.post(
                NotifyRules.idFor("approval", p.label),
                if (p.payment) Channel.PAYMENTS else Channel.APPROVALS,
                context.getString(if (p.payment) R.string.notify_payment_waiting else R.string.notify_approval_waiting),
                p.label, OpenTarget.Chat,
            )
            is ReplyRules.Post.Shipping -> notifier.post(
                NotifyRules.idFor("shipping", p.formId), Channel.APPROVALS, context.getString(R.string.shipping_ask_title),
                p.what.ifEmpty { context.getString(R.string.notify_open_hint) }, OpenTarget.Chat,
            )
        }
    }

    private fun startService() {
        try {
            ContextCompat.startForegroundService(context, Intent(context, ReplyService::class.java))
        } catch (e: Exception) {
            // not allowed right now (the system's background rules): the reply still finishes if the process lives, and the
            // 15-minute check reports it otherwise
            Log.w("KeloReply", "could not start the reply service", e)
        }
    }
}
