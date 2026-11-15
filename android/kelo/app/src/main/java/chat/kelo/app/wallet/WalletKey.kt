package chat.kelo.app.wallet

import android.app.KeyguardManager
import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import android.security.keystore.UserNotAuthenticatedException
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import chat.kelo.app.ui.VaultGateRules
import chat.kelo.app.ui.findActivity
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * The phone's own card key: AES-256-GCM inside the Android Keystore, created with setUserAuthenticationRequired(true). On Android 11+
 * every use needs the owner's strong biometric or screen lock right then (the prompt carries the cipher as a CryptoObject); on 8-10,
 * where a CryptoObject cannot be paired with the screen lock, a use is allowed for [VALIDITY_SECONDS] after the check.
 *
 * [keyId] names the key in the blobs this phone makes; it changes whenever a new key is made, so blobs made before (or on another
 * phone) are recognised as ones this phone cannot open.
 */
class WalletKey(context: Context) {
    private val app = context.applicationContext
    private val prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** This phone's key id, or null when it has no usable key. */
    fun keyId(): String? {
        val id = prefs.getString(KEY_ID, null) ?: return null
        return if (runCatching { keystore().containsAlias(ALIAS) }.getOrDefault(false)) id else null
    }

    /** The phone has a screen lock (without one the Keystore cannot make a key that asks for it). */
    fun deviceSecure(): Boolean = (app.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager)?.isDeviceSecure == true

    /** A cipher ready to encrypt, making the key first when there is none. */
    fun encryptCipher(): Cipher {
        val key = existingKey() ?: newKey()
        return Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key) }
    }

    /** A cipher ready to decrypt a blob with [iv]; throws [KeyGone] when this phone has no key (or it was invalidated). */
    fun decryptCipher(iv: ByteArray): Cipher {
        val key = existingKey() ?: throw KeyGone()
        return Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_BITS, iv)) }
    }

    /** Forgets a key the Keystore no longer lets anyone use (a new fingerprint was enrolled, the screen lock was removed). */
    fun forget() {
        runCatching { keystore().deleteEntry(ALIAS) }
        prefs.edit().remove(KEY_ID).apply()
    }

    private fun existingKey(): SecretKey? {
        if (prefs.getString(KEY_ID, null) == null) return null
        return (runCatching { keystore().getEntry(ALIAS, null) }.getOrNull() as? KeyStore.SecretKeyEntry)?.secretKey
    }

    @Synchronized
    private fun newKey(): SecretKey {
        runCatching { keystore().deleteEntry(ALIAS) }
        val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setRandomizedEncryptionRequired(true)
            .setUserAuthenticationRequired(true)
            .setInvalidatedByBiometricEnrollment(true)
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL)
                } else {
                    @Suppress("DEPRECATION")
                    setUserAuthenticationValidityDurationSeconds(VALIDITY_SECONDS)
                }
            }
            .build()
        val key = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER).apply { init(spec) }.generateKey()
        val id = ByteArray(9).also { SecureRandom().nextBytes(it) }.joinToString("") { String.format(java.util.Locale.ROOT, "%02x", it) }
        prefs.edit().putString(KEY_ID, "android-$id").apply()
        return key
    }

    private fun keystore(): KeyStore = KeyStore.getInstance(PROVIDER).apply { load(null) }

    class KeyGone : Exception("no wallet key on this phone")

    companion object {
        const val ALIAS = "chat.kelo.app.wallet.v1"
        const val VALIDITY_SECONDS = 30
        private const val PROVIDER = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val TAG_BITS = 128
        private const val PREFS = "kelo.wallet"
        private const val KEY_ID = "key_id"
    }
}

/** A Keystore cipher behind the [CardCipher] interface. */
class JceCardCipher(private val cipher: Cipher) : CardCipher {
    override val iv: ByteArray get() = cipher.iv
    override fun doFinal(input: ByteArray): ByteArray = cipher.doFinal(input)
}

/** What asking for the card key came to. */
sealed interface CardAuth {
    data class Ok(val cipher: CardCipher, val keyId: String) : CardAuth
    /** The user backed out of the prompt */
    data object Cancelled : CardAuth
    /** The phone has no screen lock, so no card key can exist */
    data object NoLock : CardAuth
    /** This phone has no key that opens this card (made elsewhere, or the key was invalidated) */
    data object KeyLost : CardAuth
    data class Failed(val message: String) : CardAuth
}

/** Asks for the owner's check and hands back a cipher the Keystore lets this app use once. */
class CardAuthenticator internal constructor(private val activity: FragmentActivity?, val key: WalletKey) {
    /** Encrypt a new card (or a re-entered one). */
    fun forSaving(title: String, subtitle: String, onDone: (CardAuth) -> Unit) = run(title, subtitle, onDone) { key.encryptCipher() }

    /** Decrypt a blob whose IV is [iv]. */
    fun forPaying(iv: ByteArray, title: String, subtitle: String, onDone: (CardAuth) -> Unit) =
        run(title, subtitle, onDone) { key.decryptCipher(iv) }

    private fun run(title: String, subtitle: String, onDone: (CardAuth) -> Unit, make: () -> Cipher) {
        val act = activity ?: return onDone(CardAuth.Failed("no activity"))
        if (!key.deviceSecure()) return onDone(CardAuth.NoLock)
        // BiometricPrompt quietly does nothing once the activity has saved its state: answer instead of leaving the caller waiting
        if (act.supportFragmentManager.isStateSaved || act.isFinishing) return onDone(CardAuth.Cancelled)
        val perUse = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R
        var cipher: Cipher? = null
        if (perUse) {
            cipher = try { make() } catch (e: Exception) { return onDone(classify(e)) }
        }
        var ended = false
        fun end() { if (!ended) { ended = true; CardPrompts.ended() } }
        val prompt = BiometricPrompt(act, ContextCompat.getMainExecutor(act), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                end()
                val c = if (perUse) result.cryptoObject?.cipher else try { make() } catch (e: Exception) { return onDone(classify(e)) }
                val id = key.keyId()
                if (c == null || id == null) onDone(CardAuth.KeyLost) else onDone(CardAuth.Ok(JceCardCipher(c), id))
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                end()
                onDone(
                    when {
                        VaultGateRules.isCancel(errorCode) -> CardAuth.Cancelled
                        errorCode == BiometricPrompt.ERROR_NO_DEVICE_CREDENTIAL -> CardAuth.NoLock
                        else -> CardAuth.Failed(errString.toString())
                    },
                )
            }
        })
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle(title)
            .setSubtitle(subtitle)
            .setAllowedAuthenticators(if (perUse) BIOMETRIC_STRONG or DEVICE_CREDENTIAL else BIOMETRIC_WEAK or DEVICE_CREDENTIAL)
            .build()
        CardPrompts.started()
        try {
            if (perUse) prompt.authenticate(info, BiometricPrompt.CryptoObject(cipher!!)) else prompt.authenticate(info)
        } catch (e: Exception) {
            end()
            onDone(CardAuth.Failed(e.message ?: e.javaClass.simpleName))
        }
    }

    private fun classify(e: Exception): CardAuth = when (e) {
        is KeyPermanentlyInvalidatedException -> { key.forget(); CardAuth.KeyLost }
        is WalletKey.KeyGone -> CardAuth.KeyLost
        is UserNotAuthenticatedException -> CardAuth.Cancelled
        is IllegalStateException -> if (!key.deviceSecure()) CardAuth.NoLock else CardAuth.Failed(e.message ?: "")
        is java.security.InvalidAlgorithmParameterException -> if (!key.deviceSecure()) CardAuth.NoLock else CardAuth.Failed(e.message ?: "")
        else -> CardAuth.Failed(e.message ?: e.javaClass.simpleName)
    }
}

/**
 * How many card-key checks are on screen. On Android 9 and older the screen-lock check is an activity of its own, so the app goes to
 * the background during it; the card pages must not lock (and throw away a save that is waiting for the check) because of that.
 */
internal object CardPrompts {
    private var count = 0

    @Synchronized fun started() { count++ }
    @Synchronized fun ended() { count = (count - 1).coerceAtLeast(0) }
    @Synchronized fun active(): Boolean = count > 0
}

@Composable
fun rememberCardAuthenticator(): CardAuthenticator {
    val context = LocalContext.current
    val activity = context.findActivity() as? FragmentActivity
    return remember(activity) { CardAuthenticator(activity, WalletKey(context)) }
}
