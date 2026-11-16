package chat.kelo.app.device

import android.content.Context

/**
 * Per row under 이 기기에서, kept on the phone (not secret: switches, times, counts, settings, app package names): on/off, "asked and
 * refused", a wipe still owed to the server, the last send. The calendar row keeps its own (chat.kelo.app.phonecal.PhoneCalendarPrefs).
 */
class DevicePrefs(context: Context) {
    private val p = context.getSharedPreferences("kelo_device", Context.MODE_PRIVATE)

    fun on(f: DeviceFeature): Boolean = p.getBoolean("on.${f.key}", false)
    fun setOn(f: DeviceFeature, v: Boolean) = p.edit().putBoolean("on.${f.key}", v).apply()

    fun denied(f: DeviceFeature): Boolean = p.getBoolean("denied.${f.key}", false)
    fun setDenied(f: DeviceFeature, v: Boolean) = p.edit().putBoolean("denied.${f.key}", v).apply()

    /** Turned off while the server could not be reached: its copy is deleted at the next chance. */
    fun wipePending(f: DeviceFeature): Boolean = p.getBoolean("wipe.${f.key}", false)
    fun setWipePending(f: DeviceFeature, v: Boolean) = p.edit().putBoolean("wipe.${f.key}", v).apply()

    fun lastAt(f: DeviceFeature): Long = p.getLong("last.at.${f.key}", 0)
    fun lastCount(f: DeviceFeature): Int = p.getInt("last.count.${f.key}", 0)
    fun sent(f: DeviceFeature, at: Long, count: Int) = p.edit().putLong("last.at.${f.key}", at).putInt("last.count.${f.key}", count).apply()
    fun clear(f: DeviceFeature) = p.edit().remove("last.at.${f.key}").remove("last.count.${f.key}").apply()

    // ---- messages
    var messageDays: Int
        get() = DeviceRules.messageDays(p.getInt("messages.days", DeviceRules.DEFAULT_MESSAGE_DAYS))
        set(v) = p.edit().putInt("messages.days", DeviceRules.messageDays(v)).apply()

    /** "문자 보내기 허용": the phone may send a text the user approved (off by default). */
    var smsSend: Boolean
        get() = p.getBoolean("messages.send", false)
        set(v) = p.edit().putBoolean("messages.send", v).apply()

    // ---- notifications
    var excluded: Set<String>
        get() = p.getStringSet("notif.excluded", emptySet()).orEmpty().toSet()
        set(v) = p.edit().putStringSet("notif.excluded", v).apply()

    /** Apps that posted a notification lately (package to label), so the user can leave one out. At most 60, newest kept. */
    fun seenApps(): List<Pair<String, String>> =
        p.getString("notif.seen", "").orEmpty().lines().mapNotNull { l -> l.split('\t').takeIf { it.size == 2 }?.let { it[0] to it[1] } }

    fun noteSeen(pkg: String, label: String) {
        val now = seenApps().filter { it.first != pkg }
        val next = (listOf(pkg to label.replace('\t', ' ').replace('\n', ' ')) + now).take(60)
        p.edit().putString("notif.seen", next.joinToString("\n") { "${it.first}\t${it.second}" }).apply()
    }

    /** The user agreed to the disclosure before notification access was opened. */
    var notifAgreed: Boolean
        get() = p.getBoolean("notif.agreed", false)
        set(v) = p.edit().putBoolean("notif.agreed", v).apply()

    // ---- health
    var healthTypes: Set<String>
        get() = p.getStringSet("health.types", DeviceRules.DEFAULT_HEALTH).orEmpty().filter { it in DeviceRules.HEALTH_TYPES }.toSet()
        set(v) = p.edit().putStringSet("health.types", v.filter { it in DeviceRules.HEALTH_TYPES }.toSet()).apply()

    /** Whether the Health Connect permissions for the ticked kinds were granted when last checked (the check itself is async). */
    var healthGranted: Boolean
        get() = p.getBoolean("health.granted", false)
        set(v) = p.edit().putBoolean("health.granted", v).apply()
}
