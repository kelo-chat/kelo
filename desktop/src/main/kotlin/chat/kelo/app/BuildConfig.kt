package chat.kelo.app

/**
 * What the shared Android code reads from Android's generated BuildConfig.
 *
 * [DEBUG] is read only by chat.kelo.app.core.ServerAddress.isAllowed: true lets the app reach a Kelo server over plain http on
 * this same device (localhost / 127.0.0.1), as Android's debug builds may. On the desktop that is an ordinary setup (the Kelo
 * server running in Docker on the same PC); every other address still needs https.
 */
object BuildConfig {
    const val DEBUG = true
}
