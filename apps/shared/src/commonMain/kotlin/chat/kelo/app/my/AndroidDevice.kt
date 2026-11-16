package chat.kelo.app.my

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** The device's screen size in pixels (GET /api/android "screen"). */
@Serializable
data class AndroidScreen(val width: Int = 0, val height: Int = 0)

/**
 * GET /api/android and POST /api/android/check (server api/android_device.py): the Android device Kelo drives. Not [configured]: the
 * rest is empty and My shows no row. Never the device's address (that stays in the server's .env).
 */
@Serializable
data class AndroidDeviceInfo(
    val configured: Boolean = false,
    val connected: Boolean = false,
    val model: String = "",
    @SerialName("android_version") val androidVersion: String = "",
    val screen: AndroidScreen? = null,
    @SerialName("play_store") val playStore: Boolean = false,
    @SerialName("korean_input") val koreanInput: Boolean = false,
    @SerialName("checked_at") val checkedAt: Double = 0.0,
)

object AndroidDeviceRules {
    /** The README section that says how to set the device up (the page's link). */
    const val README_URL = "https://github.com/kelo-chat/kelo#android"

    /** The live view key of the device (the same one the chat's phone card opens). */
    const val LIVE_KEY = "android"

    /** The page's row shows only for a server with a device set up. */
    fun shown(info: AndroidDeviceInfo?): Boolean = info?.configured == true

    /** The screen size as "720 × 1280"; null when the device gave none. */
    fun screenSize(info: AndroidDeviceInfo): String? = info.screen?.takeIf { it.width > 0 && it.height > 0 }?.let { "${it.width} × ${it.height}" }
}
