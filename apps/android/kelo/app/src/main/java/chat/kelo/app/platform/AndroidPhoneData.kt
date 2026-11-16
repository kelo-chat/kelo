package chat.kelo.app.platform

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import chat.kelo.app.device.DeviceFeature
import chat.kelo.app.device.DeviceSection

/** The phone's own data rows (device/DeviceUi.kt) behind [PhoneDataSection]. */
object AndroidPhoneData : PhoneDataSection {
    @Composable
    override fun Section(modifier: Modifier, openCalendar: Boolean, onCalendarOpened: () -> Unit) {
        DeviceSection(modifier, request = if (openCalendar) DeviceFeature.CALENDAR else null, onRequestHandled = onCalendarOpened)
    }
}
