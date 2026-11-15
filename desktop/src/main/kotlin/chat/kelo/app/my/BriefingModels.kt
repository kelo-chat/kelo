package chat.kelo.app.my

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// The plain parts of android/kelo .../my/BriefingSettings.kt and my/BriefingSources.kt (those files also draw the settings with
// Android Compose), copied by hand so the desktop's settings page and the Android unit tests use the same model and rules.
// Keep in step with them (desktop/tools/copy_briefing_models.py writes this file).

/** Where a phone of the owner's last was (weather.py "here"): what the weather is for. */
@Serializable
data class BriefingPlaceInfo(
    val name: String = "",
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    @SerialName("country_code") val countryCode: String = "",
    val at: Double? = null,
)

@Serializable
data class WeatherProviderInfo(
    val id: String = "",
    val name: String = "",
    @SerialName("needs_key") val needsKey: Boolean = false,
    val attribution: String = "",
    @SerialName("attribution_url") val attributionUrl: String = "",
)

@Serializable
data class BriefingWeatherSettings(
    /** auto | open_meteo | kma | weatherapi */
    val provider: String = "auto",
    /** The last location a phone sent; null: none yet, so the briefing has no weather */
    val here: BriefingPlaceInfo? = null,
    val keys: Map<String, Boolean> = emptyMap(),
    val providers: List<WeatherProviderInfo> = emptyList(),
)

/** GET /api/briefing: the morning briefing's settings (server: briefing.py). */
@Serializable
data class BriefingSettings(
    val enabled: Boolean = true,
    /** auto (at the start of the day's hours, or the morning's first app open) | fixed ([fixedTime]) */
    @SerialName("time_mode") val timeMode: String = "auto",
    @SerialName("fixed_time") val fixedTime: String = "07:30",
    @SerialName("offset_min") val offsetMin: Int = 0,
    @SerialName("early_open") val earlyOpen: Boolean = true,
    val sections: Map<String, Boolean> = mapOf("weather" to true, "calendar" to true, "missed" to true),
    /** short | skip: what a day with nothing notable gets */
    @SerialName("quiet_days") val quietDays: String = "short",
    @SerialName("next_at") val nextAt: Double? = null,
    val weather: BriefingWeatherSettings = BriefingWeatherSettings(),
)

/** The briefing settings' small rules, apart from the UI. */
object BriefingSettingsRules {
    val OFFSETS = listOf(0, 10, 20, 30, 45, 60)
    val SECTIONS = listOf("weather", "calendar", "missed")
    val PROVIDERS = listOf("auto", "open_meteo", "kma", "weatherapi")
    val KEYED = listOf("kma", "weatherapi")

    /** The offset choices, with the current one kept even when it is not a preset. */
    fun offsets(current: Int): List<Int> = (OFFSETS + current.coerceIn(0, 60)).distinct().sorted()

    fun sectionOn(b: BriefingSettings, id: String): Boolean = b.sections[id] ?: true

    /** The PUT body that turns one section on or off. */
    fun sectionBody(id: String, on: Boolean): JsonObject = buildJsonObject { put("sections", buildJsonObject { put(id, on) }) }

    /** A key the owner pastes: one line, no spaces, 8..512 characters (the server checks it with a real request). */
    fun keyOk(key: String): Boolean = key.trim().let { it.length in 8..512 && it.none(Char::isWhitespace) }

    /** The quiet "weather shows once location is allowed" line: the weather is in the briefing, and there is no location to use. */
    fun needsLocation(b: BriefingSettings, granted: Boolean): Boolean = sectionOn(b, "weather") && (!granted || b.weather.here == null)
}

/** One account the briefing and the heads-ups can read (GET /api/briefing/sources). [problem]: the quiet line when it could not be read. */
@Serializable
data class SourceAccount(
    val id: String = "",
    /** calendar | mail */
    val kind: String = "calendar",
    val provider: String = "",
    /** the provider's name in the app's language ("Naver Calendar") */
    val name: String = "",
    val address: String = "",
    /** the short name on the briefing card */
    val label: String = "",
    val on: Boolean = true,
    val problem: String? = null,
)

/** One kind of account (calendars or mail): every account on unless the owner switched it off; a new one starts on. */
@Serializable
data class SourceGroup(val accounts: List<SourceAccount> = emptyList())

@Serializable
data class BriefingSources(val calendar: SourceGroup = SourceGroup(), val mail: SourceGroup = SourceGroup())

/** The "what it reads" list's small rules, apart from the UI. */
object BriefingSourcesRules {
    val KINDS = listOf("calendar", "mail")

    fun group(s: BriefingSources, kind: String): SourceGroup = if (kind == "mail") s.mail else s.calendar

    /** The line that names the account: its address, or the provider's name when it has none (the phone's calendar). */
    fun title(a: SourceAccount): String = a.address.ifBlank { a.name }

    /** The line under it: the provider, when the title is the address. */
    fun subtitle(a: SourceAccount): String = if (a.address.isBlank()) "" else a.name

    /** Groups worth showing: one with no account has nothing to choose. */
    fun shown(s: BriefingSources): List<String> = KINDS.filter { group(s, it).accounts.isNotEmpty() }

    /** How many of a group are read: "3 of 4". */
    fun readCount(g: SourceGroup): Pair<Int, Int> = g.accounts.count { it.on } to g.accounts.size

    fun anyProblem(s: BriefingSources): Boolean = KINDS.any { k -> group(s, k).accounts.any { it.problem != null } }

    /** The PUT body that switches one account on or off. */
    fun toggleBody(kind: String, id: String, on: Boolean): JsonObject =
        buildJsonObject { put(kind, buildJsonObject { put("accounts", buildJsonObject { put(id, on) }) }) }

    /** The group as it will be after one switch is flipped (shown at once, before the server answers). */
    fun flipped(s: BriefingSources, kind: String, id: String, on: Boolean): BriefingSources {
        val g = group(s, kind)
        val next = g.copy(accounts = g.accounts.map { if (it.id == id) it.copy(on = on) else it })
        return if (kind == "mail") s.copy(mail = next) else s.copy(calendar = next)
    }
}
