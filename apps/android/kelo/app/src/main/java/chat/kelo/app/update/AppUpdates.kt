package chat.kelo.app.update

import androidx.activity.ComponentActivity

/**
 * The app's updates, the same for every build so the banner, the card in 마이, 업데이트 확인 and the "update required" screen are
 * shared. Each build brings its own ([AppUpdatesFactory] lives in the build's own source set):
 *  - sideload / full ([AppUpdater]): reads https://kelo.chat/download/latest.json, downloads and checks the APK, opens Android's
 *    installer.
 *  - play (src/play: PlayAppUpdates): Google Play's In-App Updates; never downloads an APK itself. The Play Core library is only in
 *    the play build.
 */
interface AppUpdates : chat.kelo.app.platform.AppUpdateSource {
    /** Called once from the activity's onCreate: follows its resume (a check when due) and, for Play, holds its result launcher. */
    fun attach(activity: ComponentActivity)

    /** The banner was closed or used: it does not come back for this version. */
    fun bannerSeen()

    /**
     * The server's oldest supported build for this platform (GET /api/profile `min_app_version.android`, a versionCode; null or 0 =
     * none). Below it the app shows the required-update screen and, on Play, starts an immediate update.
     */
    fun requireAtLeast(minVersionCode: Long?)

    /** File builds only: "설정 열기" in the install-permission note. */
    fun openInstallSetting() {}

    fun dismissPermission() {}
}
