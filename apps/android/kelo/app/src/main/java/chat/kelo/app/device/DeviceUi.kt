package chat.kelo.app.device

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.R
import chat.kelo.app.ui.platform.LocalReloadOnReturn
import chat.kelo.app.my.agoText
import chat.kelo.app.phonecal.PhoneCalendarSync
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.LocalHaptics
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val Handset by lazy {
    line("handset", "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2Z")
}
private val CallList by lazy {
    line("call-list", "M15 3h6M15 7h6M15 11h4",
        "M13 16.5v2.3a1.6 1.6 0 0 1-1.7 1.6A15.6 15.6 0 0 1 3.6 12.7 1.6 1.6 0 0 1 5.2 11h2.3a1.6 1.6 0 0 1 1.6 1.4l.5 2.2-1.1 1.1a12.6 12.6 0 0 0 4 4l1.1-1.1 2.2.5a1.6 1.6 0 0 1 1.4 1.6")
}

/** A line icon in Kelo's style (24 grid, stroke 1.8, round ends), for the two rows KeloIcons has nothing for. */
private fun line(name: String, vararg paths: String): ImageVector =
    ImageVector.Builder(name = name, defaultWidth = 24.dp, defaultHeight = 24.dp, viewportWidth = 24f, viewportHeight = 24f).apply {
        paths.forEach { addPath(addPathNodes(it), fill = null, stroke = SolidColor(Color.Black), strokeLineWidth = 1.8f, strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round) }
    }.build()

private fun DeviceFeature.icon(): ImageVector = when (this) {
    DeviceFeature.CALENDAR -> KeloIcons.Calendar
    DeviceFeature.CONTACTS -> KeloIcons.User
    DeviceFeature.MESSAGES -> KeloIcons.Chat
    DeviceFeature.DIALER -> Handset
    DeviceFeature.NOTIFICATIONS -> KeloIcons.Bell
    DeviceFeature.CALL_LOG -> CallList
    DeviceFeature.HEALTH -> KeloIcons.Pulse
}

private fun DeviceFeature.title(): Int = when (this) {
    DeviceFeature.CALENDAR -> R.string.device_calendar
    DeviceFeature.CONTACTS -> R.string.device_contacts
    DeviceFeature.MESSAGES -> R.string.device_messages
    DeviceFeature.DIALER -> R.string.device_dialer
    DeviceFeature.NOTIFICATIONS -> R.string.device_notifications
    DeviceFeature.CALL_LOG -> R.string.device_call_log
    DeviceFeature.HEALTH -> R.string.device_health
}

private fun DeviceFeature.does(): Int = when (this) {
    DeviceFeature.CALENDAR -> R.string.device_calendar_does
    DeviceFeature.CONTACTS -> R.string.device_contacts_does
    DeviceFeature.MESSAGES -> R.string.device_messages_does
    DeviceFeature.DIALER -> R.string.device_dialer_does
    DeviceFeature.NOTIFICATIONS -> R.string.device_notifications_does
    DeviceFeature.CALL_LOG -> R.string.device_call_log_does
    DeviceFeature.HEALTH -> R.string.device_health_does
}

private fun DeviceFeature.sends(): Int = when (this) {
    DeviceFeature.CALENDAR -> R.string.device_calendar_sends
    DeviceFeature.CONTACTS -> R.string.device_contacts_sends
    DeviceFeature.MESSAGES -> R.string.device_messages_sends
    DeviceFeature.DIALER -> R.string.device_dialer_sends
    DeviceFeature.NOTIFICATIONS -> R.string.device_notifications_sends
    DeviceFeature.CALL_LOG -> R.string.device_call_log_sends
    DeviceFeature.HEALTH -> R.string.device_health_sends
}

private fun denied(context: android.content.Context, f: DeviceFeature): Boolean =
    if (f == DeviceFeature.CALENDAR) PhoneCalendarSync.prefs(context).denied else DeviceSync.prefs(context).denied(f)

private fun setDenied(context: android.content.Context, f: DeviceFeature, v: Boolean) =
    if (f == DeviceFeature.CALENDAR) PhoneCalendarSync.prefs(context).denied = v else DeviceSync.prefs(context).setDenied(f, v)

/**
 * 이 기기에서: one row per thing this phone can share with the user's own server (icon, name, "이 기기에서", 연결 / 연결됨). A row opens
 * a small sheet with what Kelo can do, what is sent, the switch and the last sync. Shown at the bottom of 연결 and in 마이 → 권한.
 */
@Composable
fun DeviceSection(modifier: Modifier = Modifier, title: Boolean = true, request: DeviceFeature? = null, onRequestHandled: () -> Unit = {}) {
    val context = LocalContext.current
    val c = Kelo.colors
    var version by remember { mutableIntStateOf(0) }
    var open by remember { mutableStateOf<DeviceFeature?>(null) }
    // another screen asked for one row's sheet (Google Calendar's "휴대폰 캘린더로 연결하기")
    LaunchedEffect(request) { if (request != null) { open = request; onRequestHandled() } }
    LocalReloadOnReturn.current(Unit) { version++ }   // back from the system's settings: a permission may have changed
    val rows = remember { DeviceSync.shown() }
    Column(modifier.fillMaxWidth()) {
        if (title) Text(
            stringResource(R.string.device_section), style = MaterialTheme.typography.titleSmall, color = c.muted,
            modifier = Modifier.padding(start = 4.dp, top = 6.dp, bottom = 8.dp).semantics { heading() },
        )
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card)) {
            rows.forEachIndexed { i, f ->
                if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(start = 68.dp, end = 16.dp))
                @Suppress("UNUSED_EXPRESSION") version
                val on = DeviceSync.on(context, f)
                val allowed = DeviceSync.permitted(context, f)
                DeviceRow(f, connected = on && allowed, needsPermission = (on || denied(context, f)) && !allowed) { open = f }
            }
        }
    }
    open?.let { f -> DeviceSheet(f, onClose = { open = null; version++ }, onChanged = { version++ }) }
}

@Composable
private fun DeviceTile(f: DeviceFeature, size: androidx.compose.ui.unit.Dp) {
    val c = Kelo.colors
    Box(Modifier.size(size).clip(RoundedCornerShape(size * 0.28f)).background(c.infoSoft), contentAlignment = Alignment.Center) {
        Icon(f.icon(), contentDescription = null, tint = c.infoInk, modifier = Modifier.size(size * 0.55f))
    }
}

@Composable
private fun DeviceRow(f: DeviceFeature, connected: Boolean, needsPermission: Boolean, onOpen: () -> Unit) {
    val c = Kelo.colors
    val name = stringResource(f.title())
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClickLabel = name, onClick = onOpen).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        DeviceTile(f, 40.dp)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(name, color = c.ink, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(stringResource(R.string.device_row_sub), color = c.muted, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        val pill = RoundedCornerShape(99.dp)
        val label = stringResource(when { needsPermission -> R.string.device_needs_permission; connected -> R.string.device_connected; else -> R.string.device_row_connect })
        val (bg, fg) = when { needsPermission -> c.dangerSoft to c.dangerInk; connected -> c.successSoft to c.successInk; else -> Color.Transparent to c.accent }
        Text(
            label, color = fg, maxLines = 1, style = MaterialTheme.typography.labelLarge.copy(fontSize = 13.sp, lineHeight = 18.sp),
            modifier = Modifier.clip(pill).background(bg).then(if (!connected && !needsPermission) Modifier.border(1.dp, c.accent, pill) else Modifier)
                .padding(horizontal = 12.dp, vertical = 5.dp),
        )
    }
}

/** One row's sheet: what it does, what goes to the server, the switch, the last sync (it keeps syncing by itself), and its options. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DeviceSheet(f: DeviceFeature, onClose: () -> Unit, onChanged: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val haptics = LocalHaptics.current
    val c = Kelo.colors
    val prefs = remember { DeviceSync.prefs(context) }
    var on by remember { mutableStateOf(DeviceSync.on(context, f)) }
    var allowed by remember { mutableStateOf(DeviceSync.permitted(context, f)) }
    var isDenied by remember { mutableStateOf(denied(context, f) && !allowed) }
    var offFailed by remember { mutableStateOf(false) }
    var disclosure by remember { mutableStateOf(false) }
    var refresh by remember { mutableIntStateOf(0) }
    val status by DeviceSync.status.collectAsStateWithLifecycle()
    val calStatus by PhoneCalendarSync.status.collectAsStateWithLifecycle()
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { delay(20_000); now = System.currentTimeMillis() } }   // "3분 전" keeps moving

    fun changed() { refresh++; onChanged() }

    fun turnedOn() {
        allowed = true; isDenied = false; setDenied(context, f, false); on = true
        scope.launch { DeviceSync.turnOn(context, f); changed() }
    }

    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { res ->
        if (res.isNotEmpty() && res.values.all { it }) turnedOn() else { allowed = false; isDenied = true; changed() }
    }
    val askHealth = rememberLauncherForActivityResult(PermissionController.createRequestPermissionResultContract()) { granted ->
        val want = HealthReader.permissions(prefs.healthTypes)
        prefs.healthGranted = granted.any { it in want }
        if (prefs.healthGranted) turnedOn() else { allowed = false; isDenied = true; setDenied(context, f, true); changed() }
    }

    LocalReloadOnReturn.current(Unit) {
        if (f == DeviceFeature.HEALTH && HealthReader.available(context)) prefs.healthGranted = HealthReader.granted(context, prefs.healthTypes).isNotEmpty()
        allowed = DeviceSync.permitted(context, f)
        when {
            f == DeviceFeature.NOTIFICATIONS && allowed && prefs.notifAgreed && !on -> turnedOn()   // came back with access given
            allowed && denied(context, f) -> turnedOn()
            allowed && on -> DeviceSync.sync(context, f, force = false)
            !allowed -> isDenied = denied(context, f)
        }
    }

    fun openAppSettings() = runCatching {
        context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
    fun openListenerSettings() = runCatching {
        context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun toggle(want: Boolean) {
        haptics.tick()
        offFailed = false
        if (!want) {
            on = false; isDenied = false; setDenied(context, f, false)
            if (f == DeviceFeature.NOTIFICATIONS) prefs.notifAgreed = false
            scope.launch { offFailed = !DeviceSync.turnOff(context, f); changed() }
            return
        }
        when (f) {
            DeviceFeature.DIALER -> turnedOn()
            DeviceFeature.NOTIFICATIONS -> if (DeviceSync.permitted(context, f) && prefs.notifAgreed) turnedOn() else disclosure = true
            DeviceFeature.HEALTH -> if (HealthReader.available(context)) {
                setDenied(context, f, true)
                askHealth.launch(HealthReader.permissions(prefs.healthTypes.ifEmpty { DeviceRules.DEFAULT_HEALTH.also { prefs.healthTypes = it } }))
            } else { isDenied = true }
            else -> if (DeviceSync.permitted(context, f)) turnedOn() else {
                setDenied(context, f, true)   // until the answer says otherwise
                ask.launch(f.permissions.toTypedArray())
            }
        }
    }

    if (disclosure) {
        // Google Play's prominent disclosure: what is collected and why, an affirmative choice, before the system screen
        AlertDialog(
            onDismissRequest = { disclosure = false },
            title = { Text(stringResource(R.string.device_notif_disclosure_title)) },
            text = { Text(stringResource(R.string.device_notif_disclosure)) },
            confirmButton = { TextButton(onClick = { disclosure = false; prefs.notifAgreed = true; openListenerSettings() }) { Text(stringResource(R.string.device_agree)) } },
            dismissButton = { TextButton(onClick = { disclosure = false; prefs.notifAgreed = false }) { Text(stringResource(R.string.device_decline)) } },
            containerColor = c.card,
        )
    }

    ModalBottomSheet(onDismissRequest = onClose, sheetState = chat.kelo.app.ui.components.rememberKeloSheetState(), containerColor = c.card) {
        @Suppress("UNUSED_EXPRESSION") refresh
        val title = stringResource(f.title())
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(start = 20.dp, end = 20.dp, bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                DeviceTile(f, 44.dp)
                Text(title, style = MaterialTheme.typography.titleLarge, color = c.ink, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f).semantics { heading() })
            }
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(stringResource(f.does()), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                MutedText(if (f == DeviceFeature.MESSAGES) stringResource(f.sends()) + " (" + stringResource(R.string.device_days, prefs.messageDays) + ")" else stringResource(f.sends()), small = true)
            }
            SwitchRow(stringResource(R.string.device_switch), on && allowed, title) { toggle(it) }

            val busy = if (f == DeviceFeature.CALENDAR) calStatus.busy else status[f]?.busy == true
            val failed = if (f == DeviceFeature.CALENDAR) calStatus.failed else status[f]?.failed == true
            when {
                f == DeviceFeature.HEALTH && !HealthReader.available(context) -> {
                    ErrorText(stringResource(R.string.device_health_missing), small = true)
                    SecondaryButton(stringResource(R.string.device_health_install), onClick = {
                        runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=${HealthReader.PROVIDER}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                    })
                }
                (isDenied || on) && !allowed -> {
                    ErrorText(stringResource(when (f) {
                        DeviceFeature.NOTIFICATIONS -> R.string.device_notif_access_off
                        DeviceFeature.HEALTH -> R.string.device_health_denied
                        else -> R.string.device_denied
                    }), small = true)
                    SecondaryButton(stringResource(if (f == DeviceFeature.HEALTH) R.string.device_health_allow else R.string.device_open_settings), onClick = {
                        when (f) {
                            DeviceFeature.NOTIFICATIONS -> if (prefs.notifAgreed) openListenerSettings() else disclosure = true
                            DeviceFeature.HEALTH -> askHealth.launch(HealthReader.permissions(prefs.healthTypes))
                            else -> openAppSettings()
                        }
                    })
                }
                on -> {
                    val last = DeviceSync.lastAt(context, f)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        val line = when {
                            f == DeviceFeature.DIALER -> stringResource(R.string.device_ready)
                            busy && last == 0L -> stringResource(R.string.device_sending)
                            last == 0L -> stringResource(R.string.device_auto_never)
                            else -> {
                                val ago = agoText(last / 1000.0, now)
                                val count = countText(f, if (f == DeviceFeature.CALENDAR) maxOf(calStatus.count, PhoneCalendarSync.prefs(context).lastCount) else maxOf(status[f]?.count ?: 0, prefs.lastCount(f)))
                                stringResource(if (f == DeviceFeature.HEALTH) R.string.device_auto_foreground else R.string.device_auto, ago) + (count?.let { " · $it" } ?: "")
                            }
                        }
                        MutedText(line, small = true, modifier = Modifier.weight(1f))
                        if (f != DeviceFeature.DIALER) {
                            val send = stringResource(if (busy) R.string.device_sending else R.string.device_send_now)
                            IconButton(onClick = { if (!busy) scope.launch { DeviceSync.sync(context, f, force = true) } }, modifier = Modifier.semantics { contentDescription = send }) {
                                Icon(KeloIcons.Retry, contentDescription = null, tint = if (busy) c.muted else c.accent, modifier = Modifier.size(20.dp))
                            }
                        }
                    }
                    if (failed && !busy) ErrorText(stringResource(R.string.device_send_failed), small = true)
                }
                offFailed -> ErrorText(stringResource(R.string.device_off_failed), small = true)
            }
            if (on && allowed) Extras(f, onChanged = { changed() })
            Spacer(Modifier.height(4.dp))
        }
    }
}

@Composable
private fun countText(f: DeviceFeature, n: Int): String? = when (f) {
    DeviceFeature.CALENDAR -> pluralStringResource(R.plurals.device_count_events, n, n)
    DeviceFeature.CONTACTS -> pluralStringResource(R.plurals.device_count_contacts, n, n)
    DeviceFeature.MESSAGES -> pluralStringResource(R.plurals.device_count_messages, n, n)
    DeviceFeature.CALL_LOG -> pluralStringResource(R.plurals.device_count_calls, n, n)
    DeviceFeature.HEALTH -> pluralStringResource(R.plurals.device_count_days, n, n)
    DeviceFeature.NOTIFICATIONS -> pluralStringResource(R.plurals.device_count_notes, n, n)
    DeviceFeature.DIALER -> null
}

@Composable
private fun SwitchRow(text: String, checked: Boolean, description: String = text, sub: String? = null, onChange: (Boolean) -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.bubble)
            .toggleable(value = checked, role = Role.Switch, onValueChange = onChange).padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f).padding(end = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(text, style = MaterialTheme.typography.bodyLarge, color = c.ink, fontWeight = FontWeight.Medium)
            if (sub != null) Text(sub, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(
            checked = checked, onCheckedChange = null, modifier = Modifier.semantics { contentDescription = description },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
}

/** Each row's own options, shown once it is on: calendar writing and where, the message window and sending, apps left out, health kinds. */
@Composable
private fun Extras(f: DeviceFeature, onChanged: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    val prefs = remember { DeviceSync.prefs(context) }
    when (f) {
        DeviceFeature.CALENDAR -> {
            val cal = remember { PhoneCalendarSync.prefs(context) }
            var write by remember { mutableStateOf(cal.writeAllowed && PhoneCalendarSync.writePermitted(context)) }
            var writeDenied by remember { mutableStateOf(false) }
            var pick by remember { mutableLongStateOf(cal.writeCalendar) }
            fun enable() { cal.writeAllowed = true; write = true; writeDenied = false; scope.launch { PhoneCalendarSync.sync(context, true); onChanged() } }
            val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok -> if (ok) enable() else writeDenied = true }
            SwitchRow(stringResource(R.string.device_cal_write), write, sub = stringResource(R.string.device_cal_write_line)) { want ->
                if (want) { if (PhoneCalendarSync.writePermitted(context)) enable() else ask.launch(android.Manifest.permission.WRITE_CALENDAR) }
                else { cal.writeAllowed = false; write = false; scope.launch { PhoneCalendarSync.sync(context, true); onChanged() } }
            }
            if (writeDenied) ErrorText(stringResource(R.string.device_cal_write_denied), small = true)
            if (write) {
                val cals = remember(write) { PhoneCalendarSync.writableCalendars(context) }
                Label(stringResource(R.string.device_cal_write_to))
                if (cals.isEmpty()) MutedText(stringResource(R.string.device_cal_none), small = true)
                val chosen = cals.firstOrNull { it.id == pick }?.id ?: cals.firstOrNull()?.id
                cals.forEach { k ->
                    Choice(k.name.ifBlank { k.account } + if (k.account.isNotBlank() && k.account != k.name) " · ${k.account}" else "", selected = k.id == chosen) {
                        pick = k.id; cal.writeCalendar = k.id; scope.launch { PhoneCalendarSync.sync(context, true) }
                    }
                }
            }
        }
        DeviceFeature.MESSAGES -> {
            var days by remember { mutableIntStateOf(prefs.messageDays) }
            var send by remember { mutableStateOf(prefs.smsSend && DeviceSync.has(context, "android.permission.SEND_SMS")) }
            var sendDenied by remember { mutableStateOf(false) }
            fun enable() { prefs.smsSend = true; send = true; sendDenied = false; DeviceSync.sendSoon(context, f) }
            val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok -> if (ok) enable() else sendDenied = true }
            Label(stringResource(R.string.device_msg_window))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                DeviceRules.MESSAGE_WINDOWS.forEach { d ->
                    Pill(stringResource(R.string.device_days, d), d == days) { days = d; prefs.messageDays = d; DeviceSync.sendSoon(context, f); onChanged() }
                }
            }
            SwitchRow(stringResource(R.string.device_msg_send), send, sub = stringResource(R.string.device_msg_send_line)) { want ->
                if (want) { if (DeviceSync.has(context, "android.permission.SEND_SMS")) enable() else ask.launch("android.permission.SEND_SMS") }
                else { prefs.smsSend = false; send = false; DeviceSync.sendSoon(context, f) }
            }
            if (sendDenied) ErrorText(stringResource(R.string.device_msg_send_denied), small = true)
        }
        DeviceFeature.NOTIFICATIONS -> {
            var excluded by remember { mutableStateOf(prefs.excluded) }
            val apps = remember { prefs.seenApps() }
            Label(stringResource(R.string.device_notif_exclude))
            if (apps.isEmpty()) MutedText(stringResource(R.string.device_notif_exclude_empty), small = true)
            apps.forEach { (pkg, label) ->
                Check(label, pkg in excluded) { want -> excluded = if (want) excluded + pkg else excluded - pkg; prefs.excluded = excluded }
            }
        }
        DeviceFeature.HEALTH -> {
            var types by remember { mutableStateOf(prefs.healthTypes) }
            val askHealth = rememberLauncherForActivityResult(PermissionController.createRequestPermissionResultContract()) { _ ->
                scope.launch { prefs.healthGranted = HealthReader.granted(context, types).isNotEmpty(); DeviceSync.sync(context, f, true); onChanged() }
            }
            Label(stringResource(R.string.device_health_types))
            val names = mapOf("steps" to R.string.device_health_steps, "sleep" to R.string.device_health_sleep, "heart_rate" to R.string.device_health_heart,
                "weight" to R.string.device_health_weight, "exercise" to R.string.device_health_exercise)
            DeviceRules.HEALTH_TYPES.forEach { t ->
                Check(stringResource(names.getValue(t)), t in types) { want ->
                    val next = if (want) types + t else types - t
                    if (next.isEmpty()) return@Check
                    types = next; prefs.healthTypes = next
                    if (want) askHealth.launch(HealthReader.permissions(setOf(t))) else scope.launch { DeviceSync.sync(context, f, true) }
                }
            }
        }
        else -> Unit
    }
    @Suppress("UNUSED_EXPRESSION") c
}

@Composable
private fun Label(text: String) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = Kelo.colors.muted, modifier = Modifier.padding(top = 4.dp))
}

@Composable
private fun Choice(text: String, selected: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().selectable(selected, role = Role.RadioButton, onClick = onClick).padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        RadioButton(selected = selected, onClick = null, colors = RadioButtonDefaults.colors(selectedColor = c.accent))
        Text(text, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.padding(start = 8.dp))
    }
}

@Composable
private fun Check(text: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().toggleable(checked, role = Role.Checkbox, onValueChange = onChange).padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Checkbox(checked = checked, onCheckedChange = null, colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent))
        Text(text, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.padding(start = 8.dp))
    }
}

@Composable
private fun Pill(text: String, selected: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    val shape = RoundedCornerShape(99.dp)
    Text(
        text, color = if (selected) c.onAccent else c.ink, style = MaterialTheme.typography.labelLarge,
        modifier = Modifier.clip(shape).background(if (selected) c.accent else Color.Transparent).border(1.dp, if (selected) c.accent else c.line, shape)
            .selectable(selected, role = Role.RadioButton, onClick = onClick).padding(horizontal = 14.dp, vertical = 6.dp),
    )
}
