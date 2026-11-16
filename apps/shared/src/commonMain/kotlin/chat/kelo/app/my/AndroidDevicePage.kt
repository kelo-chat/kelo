package chat.kelo.app.my

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.Box
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.KeyBox
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.MyCard
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo

/**
 * 마이 → 휴대폰 기기: whether the device Kelo uses is connected (model, Android version, screen size, Play Store, Korean input), a
 * connection check and its live screen. The address is not edited here: it is in the server's .env (the note says so).
 */
@Composable
fun AndroidDevicePage(vm: AndroidDeviceViewModel, onBack: () -> Unit, onOpenScreen: () -> Unit, onOpenReadme: () -> Unit) {
    val ui by vm.state.collectAsStateWithLifecycle()
    SubPage(string(Res.string.android_device_title), onBack) {
        AndroidDeviceContent(ui, onCheck = vm::check, onOpenScreen = onOpenScreen, onOpenReadme = onOpenReadme)
    }
}

@Composable
fun AndroidDeviceContent(ui: AndroidDeviceUiState, onCheck: () -> Unit, onOpenScreen: () -> Unit, onOpenReadme: () -> Unit) {
    val c = Kelo.colors
    val info = ui.info ?: AndroidDeviceInfo(configured = true)
    MyCard {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.size(10.dp).clip(CircleShape).background(if (info.connected) c.success else c.muted))
            Text(
                string(if (info.connected) Res.string.android_device_connected else Res.string.android_device_not_connected),
                style = MaterialTheme.typography.titleMedium, color = c.ink,
            )
        }
        if (info.connected) {
            if (info.model.isNotBlank()) Text(info.model, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            val size = AndroidDeviceRules.screenSize(info)
            val detail = when {
                info.androidVersion.isBlank() && size == null -> null
                info.androidVersion.isBlank() -> size
                size == null -> string(Res.string.android_device_version, info.androidVersion)
                else -> string(Res.string.android_device_detail, info.androidVersion, info.screen!!.width, info.screen.height)
            }
            if (detail != null) MutedText(detail, small = true)
            KeyBox(first = false) {
                CheckLine(string(Res.string.android_device_play_store), info.playStore)
                CheckLine(string(Res.string.android_device_korean_input), info.koreanInput)
            }
        }
        KeyBox(first = false) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                SecondaryButton(string(Res.string.android_device_check), onClick = onCheck, enabled = !ui.checking, modifier = Modifier.weight(1f))
                PrimaryButton(string(Res.string.android_device_view), onClick = onOpenScreen, enabled = info.connected, modifier = Modifier.weight(1f))
            }
        }
    }
    MutedText(string(Res.string.android_device_note), small = true, modifier = Modifier.padding(top = 4.dp))
    LinkButton(string(Res.string.android_device_readme), onClick = onOpenReadme)
}

/** "Play 스토어  ✓" or "Play 스토어  없음". */
@Composable
private fun CheckLine(label: String, present: Boolean) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyLarge, color = c.ink, modifier = Modifier.weight(1f))
        Text(
            if (present) string(Res.string.check_mark) else string(Res.string.android_device_missing),
            style = MaterialTheme.typography.bodyLarge, color = if (present) c.success else c.muted,
        )
    }
}
