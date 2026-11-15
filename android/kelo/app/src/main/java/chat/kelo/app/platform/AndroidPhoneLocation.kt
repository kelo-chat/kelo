package chat.kelo.app.platform

import android.content.Context
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.ActivityCompat
import chat.kelo.app.core.KeloApi
import chat.kelo.app.device.PhonePlace
import chat.kelo.app.ui.ActivityHost
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.JsonObject
import kotlin.coroutines.resume

/** The phone's location permission and place upload behind [PhoneLocation]. */
class AndroidPhoneLocation(private val context: Context, private val host: ActivityHost) : PhoneLocation {
    private var asks = 0

    override fun servicesOn(): Boolean = runCatching {
        val lm = context.getSystemService(android.location.LocationManager::class.java) ?: return@runCatching true
        androidx.core.location.LocationManagerCompat.isLocationEnabled(lm)
    }.getOrDefault(true)

    override fun granted(): Boolean = PhonePlace.granted(context)

    // after a second "no" the system stops asking: only the settings page can allow it
    override fun blocked(): Boolean {
        val activity = host.current ?: return false
        return PhonePlace.asked(context) && PhonePlace.PERMISSIONS.none { ActivityCompat.shouldShowRequestPermissionRationale(activity, it) }
    }

    override suspend fun request(): LocationAccess {
        val activity = host.current ?: return LocationAccess.DENIED
        if (PhonePlace.granted(context)) return LocationAccess.GRANTED
        if (blocked()) return LocationAccess.BLOCKED
        PhonePlace.markAsked(context)
        val registry = activity.activityResultRegistry
        val got = suspendCancellableCoroutine<Map<String, Boolean>> { cont ->
            var launcher: androidx.activity.result.ActivityResultLauncher<Array<String>>? = null
            launcher = registry.register("kelo-location-${asks++}", ActivityResultContracts.RequestMultiplePermissions()) { r -> launcher?.unregister(); if (cont.isActive) cont.resume(r) }
            cont.invokeOnCancellation { launcher?.unregister() }
            launcher.launch(PhonePlace.PERMISSIONS)
        }
        return if (got.values.any { it } || PhonePlace.granted(context)) LocationAccess.GRANTED else LocationAccess.DENIED
    }

    override suspend fun send(api: KeloApi, force: Boolean) = PhonePlace.maybeSend(context.applicationContext, api, force)

    override suspend fun read(): JsonObject? = PhonePlace.read(context.applicationContext)
}
