package chat.kelo.app.notify

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.app.ActivityCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.ui.findActivity
import chat.kelo.app.ui.theme.Kelo

/** Whether the question may be asked in the chat now: once per phone, and only while notifications are off. */
object AskRules {
    fun shouldAsk(askedBefore: Boolean, enabled: Boolean): Boolean = !askedBefore && !enabled
}

/** Opens the system page of Kelo's notifications (where they can be turned back on). */
fun openNotificationSettings(context: Context) {
    val intent = if (Build.VERSION.SDK_INT >= 26) {
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
    } else {
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
    }
    runCatching { context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}

/**
 * Turning notifications on: the system's own question on Android 13+ when it may still be asked, else the settings page. Returns
 * the action; [onResult] gets whether they are on afterwards.
 */
@Composable
fun rememberNotificationEnabler(onResult: (Boolean) -> Unit = {}): () -> Unit {
    val context = LocalContext.current
    val notifier = LocalAppContainer.current.notifier
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) notifier.ensureChannels()
        onResult(granted && notifier.enabled())
    }
    return {
        val activity: Activity? = context.findActivity()
        val permissionMissing = Build.VERSION.SDK_INT >= 33 && !notifier.enabled() &&
            androidx.core.content.ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED
        val askedBefore = LocalAskMemory.asked(context)
        // the system asks at most twice; after a second "no" only the settings page can turn them on
        val canAsk = permissionMissing && (!askedBefore || (activity != null && ActivityCompat.shouldShowRequestPermissionRationale(activity, Manifest.permission.POST_NOTIFICATIONS)))
        if (canAsk) {
            LocalAskMemory.markAsked(context)
            launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            openNotificationSettings(context)
        }
    }
}

/** Remembers that the system's permission question was shown once (so a later tap knows to go to the settings instead). */
private object LocalAskMemory {
    fun asked(context: Context) = context.getSharedPreferences("kelo_notify", Context.MODE_PRIVATE).getBoolean("system_asked", false)
    fun markAsked(context: Context) = context.getSharedPreferences("kelo_notify", Context.MODE_PRIVATE).edit().putBoolean("system_asked", true).apply()
}

/**
 * The question after the user's first message: shall Kelo tell them when a reply is ready? "Yes" leads to the system's own
 * permission question. Asked once per phone.
 */
@Composable
fun NotificationAskDialog(agentName: String, onDone: () -> Unit) {
    val c = Kelo.colors
    val enable = rememberNotificationEnabler { onDone() }
    AlertDialog(
        onDismissRequest = onDone,
        containerColor = c.card,
        title = { Text(stringResource(R.string.notify_ask_title), color = c.ink) },
        text = { Text(stringResource(R.string.notify_ask_body, agentName), color = c.ink) },
        confirmButton = { TextButton(onClick = { enable(); onDone() }) { Text(stringResource(R.string.notify_ask_yes), color = c.accent) } },
        dismissButton = { TextButton(onClick = onDone) { Text(stringResource(R.string.notify_ask_later), color = c.ink) } },
    )
}

/** Whether notifications are on, re-read whenever the app comes back (the user may have changed it in the settings). */
@Composable
fun rememberNotificationsOn(): Boolean {
    val notifier = LocalAppContainer.current.notifier
    var on by remember { mutableStateOf(notifier.enabled()) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { on = notifier.enabled() }
    return on
}
