package chat.kelo.app.platform

import androidx.compose.runtime.Composable
import chat.kelo.app.notify.NotifyPrefs
import chat.kelo.app.notify.Notifier
import chat.kelo.app.notify.rememberNotificationEnabler
import chat.kelo.app.notify.rememberNotificationsOn

/** The phone's notification permission behind [PhoneNotifications]. */
class AndroidPhoneNotifications(private val notifier: Notifier, private val prefs: NotifyPrefs) : PhoneNotifications {
    @Composable override fun isOn(): Boolean = rememberNotificationsOn()
    @Composable override fun rememberEnabler(): () -> Unit = rememberNotificationEnabler()
    override fun pushNeedsDevice(baseUrl: String): Boolean = prefs.pushNeedsDevice(baseUrl)
    override fun cancelAll() = notifier.cancelAll()
}
