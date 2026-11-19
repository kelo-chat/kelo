package chat.kelo.app.core

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.Interceptor

/**
 * The version contract with the server (docs/versioning.md, "MIN VERSION CONTRACT"):
 * - every call says which release this app is: `X-Kelo-App: <platform>/<version>+<build>` (set [header] once at start-up; it also
 *   travels inside KE2E, where `x-kelo-*` headers go),
 * - every answer says which release the server is: `X-Kelo-Server: <version>+<build>`; [server] keeps the newest one seen and
 *   [serverTooOld] compares it with the release this app needs ([minServer], compiled in from version.json).
 * Plain JVM: the desktop app shares this file and sets its own header.
 */
object AppVersion {
    /** e.g. "android/0.9.0+404735"; empty = not set (tests) */
    @Volatile var header: String = ""

    /** Where this build is updated from, sent as `X-Kelo-App-Channel`: download (a file: sideload, installer), play, appstore or msstore; empty = not said */
    @Volatile var channel: String = ""

    /** The oldest server release this app works with (version.json "min_server"), e.g. "0.9.0"; empty = no check */
    @Volatile var minServer: String = ""

    private val _server = MutableStateFlow<ServerSeen?>(null)
    /** The newest server answer's version (null until one came) */
    val server: StateFlow<ServerSeen?> = _server.asStateFlow()

    private val _update = MutableStateFlow<String?>(null)
    /**
     * A newer released app that works with this server (`X-Kelo-App-Update` on /api/about and /api/profile, or `app_update` in /api/about;
     * docs/version-notices.md part 3): its version, or null. The shell shows a banner once per version ([chat.kelo.app.update.AppNotice]).
     */
    val update: StateFlow<String?> = _update.asStateFlow()

    private val _serverUpdate = MutableStateFlow<String?>(null)
    /**
     * A newer server release this caller may install (`X-Kelo-Server-Update` on /api/about and /api/profile): its version, or null.
     * The apps show the server-update notice for it (update/ServerNotice.kt), at most once a day.
     */
    val serverUpdate: StateFlow<String?> = _serverUpdate.asStateFlow()

    /** What an answer said: [version] like "0.9.0+1", or null when the header was missing (a server older than 0.9.0). */
    data class ServerSeen(val version: String?)

    val interceptor = Interceptor { chain ->
        val req = chain.request()
        val b = req.newBuilder()
        if (header.isNotEmpty() && req.header(HEADER_APP) == null) b.header(HEADER_APP, header)
        if (channel.isNotEmpty() && req.header(HEADER_CHANNEL) == null) b.header(HEADER_CHANNEL, channel)
        val res = chain.proceed(b.build())
        if (res.header(HEADER_LOCAL) == null) {
            observe(res.code, res.header(HEADER_SERVER), res.header("Content-Type"))
            val path = req.url.encodedPath
            if (res.code == 200 && (path == "/api/about" || path == "/api/profile")) {
                setUpdate(res.header(HEADER_UPDATE))
                setServerUpdate(res.header(HEADER_SERVER_UPDATE))
            }
        }
        res
    }

    /**
     * Notes the server's version from one answer. Only answers that surely come from the Kelo server count: a missing header on a
     * 5xx or an HTML page is a proxy or tunnel speaking (Cloudflare's 502), not an old server.
     */
    fun observe(status: Int, serverHeader: String?, contentType: String?) {
        if (!serverHeader.isNullOrBlank()) { _server.value = ServerSeen(serverHeader.trim()); return }
        if (status in 200..499 && contentType.orEmpty().startsWith("application/json")) _server.value = ServerSeen(null)
    }

    /** The answer of /api/about or /api/profile named [version] (or none, which clears an earlier one). */
    fun setUpdate(version: String?) { _update.value = version?.trim()?.takeIf { it.isNotEmpty() } }

    /** The answer of /api/about or /api/profile named a newer server release (or none, which clears an earlier one). */
    fun setServerUpdate(version: String?) { _serverUpdate.value = version?.trim()?.takeIf { it.isNotEmpty() } }

    fun reset() { _server.value = null; _update.value = null; _serverUpdate.value = null }

    /** Whether [seen] is older than [min] (both "MAJOR.MINOR.PATCH", a "+build" ignored). A missing version is too old. */
    fun serverTooOld(seen: ServerSeen?, min: String = minServer): Boolean {
        if (seen == null || min.isBlank()) return false
        val v = seen.version ?: return true
        return compare(v, min) < 0
    }

    /** "0.9.1+3" vs "0.9.0": negative when [a] is older. Missing parts count as 0; anything after '-' or '+' is ignored. */
    fun compare(a: String, b: String): Int {
        fun parts(s: String) = s.trim().removePrefix("v").substringBefore('+').substringBefore('-').split('.').map { it.toIntOrNull() ?: 0 }
        val x = parts(a)
        val y = parts(b)
        for (i in 0 until maxOf(x.size, y.size)) {
            val d = x.getOrElse(i) { 0 }.compareTo(y.getOrElse(i) { 0 })
            if (d != 0) return d
        }
        return 0
    }

    /** The version part of a header ("0.9.0+1" -> "0.9.0"). */
    fun versionOf(header: String?): String? = header?.substringBefore('+')?.trim()?.ifEmpty { null }

    const val HEADER_APP = "X-Kelo-App"
    const val HEADER_SERVER = "X-Kelo-Server"
    const val HEADER_CHANNEL = "X-Kelo-App-Channel"
    const val HEADER_UPDATE = "X-Kelo-App-Update"
    const val HEADER_SERVER_UPDATE = "X-Kelo-Server-Update"
    /** On answers the app's own transport made (a KE2E error), never from the server */
    const val HEADER_LOCAL = "X-Kelo-Local"
}
