package chat.kelo.app.chat

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import chat.kelo.app.R
import chat.kelo.app.chat.vault.CardBox
import chat.kelo.app.device.PhonePlace
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch

/** What the location card does for each tap (pure, so it is tested). */
object LocationAskRules {
    enum class Step { SEND, ASK_SYSTEM, OPEN_SETTINGS }

    /**
     * "Share my location": with the permission, read and send; without it, the system prompt, unless the server says the owner
     * refused before ([settings]) or Android no longer shows the prompt ([blocked]: asked before and no rationale): then the phone's
     * settings for Kelo.
     */
    fun onShare(granted: Boolean, settings: Boolean, blocked: Boolean): Step = when {
        granted -> Step.SEND
        settings || blocked -> Step.OPEN_SETTINGS
        else -> Step.ASK_SYSTEM
    }

    /** The one line an ended card leaves (a string resource), or null while it is open. */
    fun endLine(state: String): Int? = when (state) {
        ChatItem.LocationAsk.ALLOWED -> R.string.location_ask_shared
        ChatItem.LocationAsk.REFUSED -> R.string.location_ask_refused
        ChatItem.LocationAsk.WAITING, ChatItem.LocationAsk.SENDING -> null
        else -> R.string.location_ask_expired
    }
}

/**
 * The reply waits for this phone's place (LOCATION REQUEST): why (the agent's words), "Share my location" and "Not now"; when the
 * owner refused before, the button opens Kelo's settings instead of the system prompt, and coming back with the permission sends it.
 * The place goes only to the owner's own server ([PhonePlace.read]: position, accuracy, town, country, neighbourhood).
 */
@Composable
fun LocationAskCard(item: ChatItem.LocationAsk, vm: ChatViewModel) {
    val c = Kelo.colors
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var settingsOpened by remember(item.approvalId) { mutableStateOf(false) }
    var unavailable by remember(item.approvalId) { mutableStateOf(false) }

    fun send() {
        vm.locationReading(item.approvalId)
        scope.launch {
            val place = runCatching { PhonePlace.read(context.applicationContext) }.getOrNull()
            unavailable = place == null
            vm.answerLocation(item.approvalId, place)
        }
    }

    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { got ->
        PhonePlace.markAsked(context)
        if (got.values.any { it } || PhonePlace.granted(context)) send() else vm.answerLocation(item.approvalId, null)
    }
    // back from the settings page: allowed there -> send at once
    val owner = LocalLifecycleOwner.current
    val current by rememberUpdatedState(item)
    DisposableEffect(owner, item.approvalId) {
        val obs = LifecycleEventObserver { _, e ->
            if (e == Lifecycle.Event.ON_RESUME && settingsOpened && current.state == ChatItem.LocationAsk.WAITING) {
                settingsOpened = false
                if (PhonePlace.granted(context)) send()
            }
        }
        owner.lifecycle.addObserver(obs)
        onDispose { owner.lifecycle.removeObserver(obs) }
    }

    CardBox {
        val end = LocationAskRules.endLine(item.state)
        if (end != null) {
            ResultLine(if (item.state == ChatItem.LocationAsk.ALLOWED) KeloIcons.Check else KeloIcons.Close, stringResource(end),
                muted = item.state != ChatItem.LocationAsk.ALLOWED)
            if (item.state == ChatItem.LocationAsk.REFUSED && unavailable) {
                Text(stringResource(R.string.location_ask_unavailable), color = c.muted, style = MaterialTheme.typography.bodySmall)
            }
            return@CardBox
        }
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(KeloIcons.MapPin, contentDescription = null, tint = c.accent)
                Text(stringResource(R.string.location_ask_title), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
                    color = c.ink, modifier = Modifier.weight(1f).semantics { heading() })
            }
            Text(item.reason.ifBlank { stringResource(R.string.location_ask_body) }, style = MaterialTheme.typography.bodyMedium, color = c.ink)
            Text(stringResource(R.string.location_ask_private), style = MaterialTheme.typography.bodySmall, color = c.muted)
            if (item.state == ChatItem.LocationAsk.SENDING) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CircularProgressIndicator(Modifier.size(16.dp), color = c.accent, strokeWidth = 2.dp)
                    Text(stringResource(R.string.location_ask_reading), style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
                return@Column
            }
            val activity = context as? Activity
            val granted = PhonePlace.granted(context)
            val blocked = PhonePlace.asked(context) && activity != null &&
                PhonePlace.PERMISSIONS.none { androidx.core.app.ActivityCompat.shouldShowRequestPermissionRationale(activity, it) }
            val step = LocationAskRules.onShare(granted, item.settings, blocked)
            if (step == LocationAskRules.Step.OPEN_SETTINGS) {
                Text(stringResource(R.string.location_ask_settings_hint), style = MaterialTheme.typography.bodySmall, color = c.muted)
            }
            // one under the other, so long words in any language fit at 320 dp
            PrimaryButton(
                stringResource(if (step == LocationAskRules.Step.OPEN_SETTINGS) R.string.location_ask_settings else R.string.location_ask_share),
                onClick = {
                    when (step) {
                        LocationAskRules.Step.SEND -> send()
                        LocationAskRules.Step.ASK_SYSTEM -> ask.launch(PhonePlace.PERMISSIONS)
                        LocationAskRules.Step.OPEN_SETTINGS -> { settingsOpened = true; openAppSettings(context) }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
            SecondaryButton(stringResource(R.string.location_ask_not_now), onClick = { vm.answerLocation(item.approvalId, null) }, modifier = Modifier.fillMaxWidth())
        }
    }
}

/** Kelo's page in the phone's settings (where location can be turned on). */
private fun openAppSettings(context: Context) {
    runCatching {
        context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
