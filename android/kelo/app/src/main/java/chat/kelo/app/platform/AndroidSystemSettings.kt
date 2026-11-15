package chat.kelo.app.platform

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings

/** The phone's own settings pages behind [SystemSettings]. */
class AndroidSystemSettings(private val context: Context) : SystemSettings {
    override fun canOpen(page: SettingsPage) = true

    override fun open(page: SettingsPage) {
        val intent = when (page) {
            SettingsPage.APP_DETAILS -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
            SettingsPage.NOTIFICATIONS ->
                if (Build.VERSION.SDK_INT >= 26) Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
                else Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
            SettingsPage.SECURITY -> Intent(Settings.ACTION_SECURITY_SETTINGS)
            SettingsPage.LOCATION -> Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)
        }
        val start = { i: Intent -> context.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        runCatching { start(intent) }.onFailure {
            // the security or location page is missing on some phones: the general settings
            if (page == SettingsPage.SECURITY || page == SettingsPage.LOCATION) runCatching { start(Intent(Settings.ACTION_SETTINGS)) }
        }
    }
}
