package chat.kelo.app.update

import android.content.Context
import androidx.core.content.ContextCompat
import chat.kelo.app.BuildConfig
import com.google.android.play.core.appupdate.AppUpdateManagerFactory

/** The Play build: Google Play's In-App Updates (the Play Core library is a dependency of this build only). */
object AppUpdatesFactory {
    fun create(context: Context): AppUpdates = PlayAppUpdates(
        context = context,
        manager = AppUpdateManagerFactory.create(context),
        versionCode = BuildConfig.VERSION_CODE.toLong(),
        running = BuildConfig.VERSION_NAME,
        executor = ContextCompat.getMainExecutor(context),
        prefs = context.getSharedPreferences("kelo_update", Context.MODE_PRIVATE),
    )
}
