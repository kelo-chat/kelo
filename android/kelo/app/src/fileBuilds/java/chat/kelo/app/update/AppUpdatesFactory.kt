package chat.kelo.app.update

import android.content.Context
import chat.kelo.app.BuildConfig

/** The builds handed out as a file (sideload, full; this folder is in both source sets): their own update check and download. */
object AppUpdatesFactory {
    fun create(context: Context): AppUpdates =
        AppUpdater(context, BuildConfig.FLAVOR, BuildConfig.VERSION_NAME, BuildConfig.UPDATE_BASE, versionCode = BuildConfig.VERSION_CODE.toLong())
}
