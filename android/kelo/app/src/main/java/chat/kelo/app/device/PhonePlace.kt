package chat.kelo.app.device

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Geocoder
import android.location.Location
import android.location.LocationManager
import android.os.Build
import androidx.core.content.ContextCompat
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.coroutines.resume

/**
 * This phone's location for the owner's Kelo server (weather.py "here"): the briefing's weather and the agent's `my_location` tool for
 * "near me" questions. Sent whenever the app is opened and location is allowed (precise or approximate: whichever the owner picked in
 * the system dialog, with its accuracy), at most every [EVERY_MS]; never in the background. Only the newest one is kept, on the owner's
 * own server. Asked for when the briefing's weather is turned on, or from its "allow" line ([PERMISSIONS]).
 */
object PhonePlace {
    private const val PREFS = "briefing_place"
    private const val KEY_AT = "sent_at"
    private const val KEY_ASKED = "asked"
    const val EVERY_MS = 15 * 60 * 1000L
    private const val WAIT_MS = 10_000L

    /** Asked together, so the system dialog offers "precise" and "approximate"; either works. */
    val PERMISSIONS = arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)

    private fun has(context: Context, p: String) = ContextCompat.checkSelfPermission(context, p) == PackageManager.PERMISSION_GRANTED

    /** Location is allowed, precise or approximate. */
    fun granted(context: Context): Boolean = has(context, Manifest.permission.ACCESS_COARSE_LOCATION) || has(context, Manifest.permission.ACCESS_FINE_LOCATION)

    /** The system dialog was shown once already (after a second "no" Android stops showing it: the button opens the app's settings). */
    fun asked(context: Context): Boolean = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_ASKED, false)

    fun markAsked(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_ASKED, true).apply()
    }

    /** Due to send: allowed, and the last one is [EVERY_MS] old (or in the future: the clock moved). */
    fun due(granted: Boolean, lastAt: Long, now: Long): Boolean = granted && (now - lastAt >= EVERY_MS || now < lastAt)

    /** Sends the phone's location when it is due (or [force], right after the owner allowed it). */
    suspend fun maybeSend(context: Context, api: KeloApi, force: Boolean = false) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        val go = if (force) granted(context) else due(granted(context), prefs.getLong(KEY_AT, 0L), now)
        if (!go) return
        val place = read(context) ?: return
        try {
            api.post<JsonElement>("/api/briefing/location", place)
            prefs.edit().putLong(KEY_AT, now).apply()
        } catch (_: ApiError) {
        }
    }

    /**
     * The place as the server takes it (POST /api/briefing/location, and the location card's "place"): the position, its accuracy in
     * metres when the phone knows it, and the town's name, country and neighbourhood (sublocality, e.g. a Korean "dong") when the
     * phone's own geocoder has them.
     */
    fun body(lat: Double, lon: Double, accuracy: Float?, name: String, country: String, sublocality: String = ""): JsonObject = buildJsonObject {
        put("lat", lat)
        put("lon", lon)
        if (accuracy != null) put("accuracy", accuracy.toDouble())
        if (name.isNotBlank()) put("name", name)
        if (country.isNotBlank()) put("country_code", country)
        if (sublocality.isNotBlank()) put("sublocality", sublocality)
    }

    /** This phone's place now (allowed, and the phone could tell), else null. */
    suspend fun read(context: Context): JsonObject? {
        val loc = current(context) ?: return null
        val p = placeName(context, loc)
        return body(loc.latitude, loc.longitude, if (loc.hasAccuracy()) loc.accuracy else null, p.name, p.country, p.sublocality)
    }

    /** What the phone's geocoder said: the town, its country code and the neighbourhood (each "" when unknown). */
    data class PlaceName(val name: String = "", val country: String = "", val sublocality: String = "")

    /** The neighbourhood of an address: its sublocality, unless that only repeats the town. */
    fun sublocalityOf(sub: String?, town: String): String = sub?.trim()?.takeIf { it.isNotEmpty() && it != town }.orEmpty()

    @SuppressLint("MissingPermission")   // checked by granted() before every call
    private suspend fun current(context: Context): Location? {
        if (!granted(context)) return null
        val lm = context.getSystemService(LocationManager::class.java) ?: return null
        // the fused provider (Android 12+) gives the best the owner allowed: precise, or approximate when that is all they chose
        val order = buildList {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) add(LocationManager.FUSED_PROVIDER)
            add(LocationManager.NETWORK_PROVIDER)
            if (has(context, Manifest.permission.ACCESS_FINE_LOCATION)) add(LocationManager.GPS_PROVIDER)
            add(LocationManager.PASSIVE_PROVIDER)
        }
        val provider = order.firstOrNull { runCatching { lm.isProviderEnabled(it) }.getOrDefault(false) } ?: return null
        val last = runCatching { lm.getLastKnownLocation(provider) }.getOrNull()
        if (last != null && System.currentTimeMillis() - last.time < EVERY_MS) return last
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return last
        return withTimeoutOrNull(WAIT_MS) {
            suspendCancellableCoroutine { cont ->
                val cancel = android.os.CancellationSignal()
                cont.invokeOnCancellation { cancel.cancel() }
                runCatching {
                    lm.getCurrentLocation(provider, cancel, ContextCompat.getMainExecutor(context)) { loc -> if (cont.isActive) cont.resume(loc) }
                }.onFailure { if (cont.isActive) cont.resume(null) }
            }
        } ?: last
    }

    /** The town, country code and neighbourhood from the phone's own geocoder (empty when it has none). */
    private suspend fun placeName(context: Context, loc: Location): PlaceName {
        if (!Geocoder.isPresent()) return PlaceName()
        val geocoder = Geocoder(context)
        val found = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            withTimeoutOrNull(WAIT_MS) {
                suspendCancellableCoroutine { cont ->
                    geocoder.getFromLocation(loc.latitude, loc.longitude, 1, object : Geocoder.GeocodeListener {
                        override fun onGeocode(addresses: MutableList<android.location.Address>) { if (cont.isActive) cont.resume(addresses.firstOrNull()) }
                        override fun onError(errorMessage: String?) { if (cont.isActive) cont.resume(null) }
                    })
                }
            }
        } else {
            withContext(Dispatchers.IO) {
                @Suppress("DEPRECATION")
                runCatching { geocoder.getFromLocation(loc.latitude, loc.longitude, 1)?.firstOrNull() }.getOrNull()
            }
        } ?: return PlaceName()
        val name = found.locality ?: found.subAdminArea ?: found.adminArea ?: ""
        return PlaceName(name, found.countryCode ?: "", sublocalityOf(found.subLocality, name))
    }
}
