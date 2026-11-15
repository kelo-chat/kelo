package chat.kelo.app.ui

import android.app.KeyguardManager
import android.content.Context
import android.widget.Toast
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import chat.kelo.app.R

/** What opening the vault takes on this phone. */
enum class GateDecision {
    /** Ask for a fingerprint / face, or the screen-lock PIN, pattern or password */
    PROMPT,
    /** The phone has no screen lock at all: open it, and say so on the page */
    OPEN_NO_LOCK,
}

object VaultGateRules {
    /**
     * Biometrics or the device credential, whichever the user has. BIOMETRIC_WEAK | DEVICE_CREDENTIAL is the one combination that
     * works on every Android version the app runs on (26+). Without any screen lock there is nothing to check against.
     */
    const val AUTHENTICATORS = BIOMETRIC_WEAK or DEVICE_CREDENTIAL

    fun decide(canAuthenticate: Int, deviceSecure: Boolean): GateDecision = when {
        canAuthenticate == BiometricManager.BIOMETRIC_SUCCESS -> GateDecision.PROMPT
        deviceSecure -> GateDecision.PROMPT
        else -> GateDecision.OPEN_NO_LOCK
    }

    /** An error from the prompt that means "this phone cannot check anyone" rather than "the user said no". */
    fun meansNoLock(errorCode: Int): Boolean = errorCode == BiometricPrompt.ERROR_NO_DEVICE_CREDENTIAL ||
        errorCode == BiometricPrompt.ERROR_HW_NOT_PRESENT || errorCode == BiometricPrompt.ERROR_NO_BIOMETRICS

    /** The user backed out: nothing to say. */
    fun isCancel(errorCode: Int): Boolean = errorCode == BiometricPrompt.ERROR_USER_CANCELED ||
        errorCode == BiometricPrompt.ERROR_NEGATIVE_BUTTON || errorCode == BiometricPrompt.ERROR_CANCELED
}

private fun Context.deviceSecure(): Boolean = (getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager)?.isDeviceSecure == true

/**
 * Returns an action that asks the user to prove it is them (fingerprint, face, or the screen lock) and then calls [onOpen].
 * [onOpen]'s argument is true when the phone has no screen lock, so nothing could be checked. With [onNoLock] given, a phone
 * without a screen lock never opens: [onNoLock] is called instead (the card pages need a lock to protect anything). [onNotOpened]
 * runs when the prompt went away without opening (backed out of, or an error).
 */
@Composable
fun rememberVaultGate(
    onNoLock: (() -> Unit)? = null, title: String? = null, onNotOpened: (() -> Unit)? = null, onOpen: (noLock: Boolean) -> Unit,
): () -> Unit {
    val context = LocalContext.current
    val activity = context.findActivity() as? FragmentActivity
    val noLockAction by rememberUpdatedState(onNoLock)
    val given by rememberUpdatedState(onOpen)
    val notOpened by rememberUpdatedState(onNotOpened)
    val open: (Boolean) -> Unit = { noLock -> val n = noLockAction; if (noLock && n != null) n() else given(noLock) }
    val title = title ?: stringResource(R.string.vault_gate_title)
    val subtitle = stringResource(R.string.vault_gate_subtitle)
    return remember(activity, title, subtitle) {
        gate@{
            if (activity == null) { open(true); return@gate }
            // BiometricPrompt quietly does nothing once the activity has saved its state (the app is going to the background): say
            // so, so the caller shows its own way to ask again instead of waiting for a prompt that never comes
            if (activity.supportFragmentManager.isStateSaved || activity.isFinishing) { notOpened?.invoke(); return@gate }
            val secure = activity.deviceSecure()
            val can = BiometricManager.from(activity).canAuthenticate(VaultGateRules.AUTHENTICATORS)
            when (VaultGateRules.decide(can, secure)) {
                GateDecision.OPEN_NO_LOCK -> open(true)
                GateDecision.PROMPT -> {
                    val prompt = BiometricPrompt(
                        activity, ContextCompat.getMainExecutor(activity),
                        object : BiometricPrompt.AuthenticationCallback() {
                            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = open(false)
                            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                                when {
                                    VaultGateRules.meansNoLock(errorCode) && !activity.deviceSecure() -> open(true)
                                    VaultGateRules.isCancel(errorCode) -> notOpened?.invoke()
                                    else -> { Toast.makeText(activity, errString, Toast.LENGTH_SHORT).show(); notOpened?.invoke() }
                                }
                            }
                        },
                    )
                    val info = BiometricPrompt.PromptInfo.Builder()
                        .setTitle(title)
                        .setSubtitle(subtitle)
                        .setAllowedAuthenticators(VaultGateRules.AUTHENTICATORS)
                        .build()
                    runCatching { prompt.authenticate(info) }.onFailure {
                        // never open on a failure to ask, unless there is no lock to ask for
                        if (!secure) open(true) else notOpened?.invoke()
                    }
                }
            }
        }
    }
}
