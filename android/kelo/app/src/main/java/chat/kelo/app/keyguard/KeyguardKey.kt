package chat.kelo.app.keyguard

import android.app.KeyguardManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import android.security.keystore.UserNotAuthenticatedException
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import chat.kelo.app.ui.VaultGateRules
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * This phone's hardware key for one Kelo server's Keyguard (spec 2.4 Android, 5.9, 5.15): AES-256-GCM in the Android Keystore
 * (StrongBox when the phone has one), unusable without the owner: Android 11+ asks for a strong biometric or the screen lock at every
 * use (the prompt carries the cipher); Android 8-10 cannot pair a cipher with the screen lock, so a use is allowed for
 * [VALIDITY_SECONDS] after the check. Re-enrolling a face or finger keeps the key; removing the screen lock deletes it (then the
 * recovery code is the way back). One key per server address, so pairing another server never replaces this one.
 */
class KeyguardKey(context: Context, serverUrl: String) {
    private val app = context.applicationContext
    val alias: String = ALIAS_PREFIX + MessageDigest.getInstance("SHA-256")
        .digest(serverUrl.trimEnd('/').lowercase().toByteArray()).take(12).joinToString("") { "%02x".format(it) }

    fun deviceSecure(): Boolean = (app.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager)?.isDeviceSecure == true

    fun exists(): Boolean = runCatching { keystore().containsAlias(alias) }.getOrDefault(false)

    /** A cipher ready to wrap: always a NEW key (enrolling again replaces this phone's copy on the server too). */
    fun wrapCipher(): Cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, newKey()) }

    /** A cipher ready to unwrap a blob with [iv]; throws [KeyGone] when this phone has no key for that server. */
    fun unwrapCipher(iv: ByteArray): Cipher {
        val key = (runCatching { keystore().getEntry(alias, null) }.getOrNull() as? KeyStore.SecretKeyEntry)?.secretKey ?: throw KeyGone()
        return Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_BITS, iv)) }
    }

    fun forget() { runCatching { keystore().deleteEntry(alias) } }

    @Synchronized
    private fun newKey(): SecretKey {
        forget()
        val strongBox = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P && app.packageManager.hasSystemFeature(PackageManager.FEATURE_STRONGBOX_KEYSTORE)
        if (strongBox) {
            try { return generate(strongBox = true) } catch (_: StrongBoxUnavailableException) { }
        }
        return generate(strongBox = false)
    }

    private fun generate(strongBox: Boolean): SecretKey {
        val spec = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setRandomizedEncryptionRequired(true)
            .setUserAuthenticationRequired(true)
            .setInvalidatedByBiometricEnrollment(false)   // 5.9: a new face or finger keeps the key
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL)
                } else {
                    @Suppress("DEPRECATION")
                    setUserAuthenticationValidityDurationSeconds(VALIDITY_SECONDS)   // 5.15
                }
                if (strongBox && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) setIsStrongBoxBacked(true)
            }
            .build()
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER).apply { init(spec) }.generateKey()
    }

    private fun keystore(): KeyStore = KeyStore.getInstance(PROVIDER).apply { load(null) }

    class KeyGone : Exception("no keyguard key on this phone")

    companion object {
        const val ALIAS_PREFIX = "chat.kelo.app.keyguard.v1."
        const val VALIDITY_SECONDS = 30
        private const val PROVIDER = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val TAG_BITS = 128

        /** Android 8-10: the 30-second window instead of a check at every use (the Security page says so). */
        val perUse: Boolean get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R
    }
}

/** This phone's Keyguard key for the signed-in server ([KeyguardKey], one per server address) and the owner's check that releases it once. */
class AndroidKeyguardKeys(
    context: Context,
    private val activity: () -> FragmentActivity?,
    private val serverUrl: () -> String?,
) : KeyguardKeys {
    private val app = context.applicationContext
    override val available = true
    override val checkEveryUse: Boolean get() = KeyguardKey.perUse

    private fun key(): KeyguardKey? = serverUrl()?.let { KeyguardKey(app, it) }

    override fun forWrap(title: String, subtitle: String, onDone: (KeyguardAuth) -> Unit) =
        run(title, subtitle, onDone) { it.wrapCipher() }

    override fun forUnwrap(iv: ByteArray, title: String, subtitle: String, onDone: (KeyguardAuth) -> Unit) =
        run(title, subtitle, onDone) { it.unwrapCipher(iv) }

    override fun forget() { key()?.forget() }

    private fun run(title: String, subtitle: String, onDone: (KeyguardAuth) -> Unit, make: (KeyguardKey) -> Cipher) {
        val key = key() ?: return onDone(KeyguardAuth.Failed("signed out"))
        val act = activity() ?: return onDone(KeyguardAuth.Failed("no activity"))
        if (!key.deviceSecure()) return onDone(KeyguardAuth.NoLock)
        if (act.supportFragmentManager.isStateSaved || act.isFinishing) return onDone(KeyguardAuth.Cancelled)
        val perUse = KeyguardKey.perUse
        var cipher: Cipher? = null
        if (perUse) cipher = try { make(key) } catch (e: Exception) { return onDone(classify(key, e)) }
        val prompt = BiometricPrompt(act, ContextCompat.getMainExecutor(act), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                val c = try {
                    (if (perUse) result.cryptoObject?.cipher else make(key))?.also { it.updateAAD(KeyguardBlob.AAD) }
                } catch (e: Exception) {
                    return onDone(classify(key, e))
                }
                onDone(if (c == null) KeyguardAuth.KeyLost else KeyguardAuth.Ok(JavaKeyguardCipher(c)))
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                onDone(when {
                    VaultGateRules.isCancel(errorCode) -> KeyguardAuth.Cancelled
                    errorCode == BiometricPrompt.ERROR_NO_DEVICE_CREDENTIAL -> KeyguardAuth.NoLock
                    else -> KeyguardAuth.Failed(errString.toString())
                })
            }
        })
        val info = BiometricPrompt.PromptInfo.Builder().setTitle(title).setSubtitle(subtitle)
            .setAllowedAuthenticators(if (perUse) BIOMETRIC_STRONG or DEVICE_CREDENTIAL else BIOMETRIC_WEAK or DEVICE_CREDENTIAL)
            .build()
        try {
            if (perUse) prompt.authenticate(info, BiometricPrompt.CryptoObject(cipher!!)) else prompt.authenticate(info)
        } catch (e: Exception) {
            onDone(KeyguardAuth.Failed(e.message ?: e.javaClass.simpleName))
        }
    }

    private fun classify(key: KeyguardKey, e: Exception): KeyguardAuth = when (e) {
        is KeyPermanentlyInvalidatedException -> { key.forget(); KeyguardAuth.KeyLost }
        is KeyguardKey.KeyGone -> KeyguardAuth.KeyLost
        is UserNotAuthenticatedException -> KeyguardAuth.Cancelled
        is IllegalStateException, is java.security.InvalidAlgorithmParameterException ->
            if (!key.deviceSecure()) KeyguardAuth.NoLock else KeyguardAuth.Failed(e.message ?: "")
        else -> KeyguardAuth.Failed(e.message ?: e.javaClass.simpleName)
    }
}

/** A javax cipher as the shared code takes it. */
private class JavaKeyguardCipher(private val c: Cipher) : KeyguardCipher {
    override val iv: ByteArray get() = c.iv
    override fun doFinal(input: ByteArray): ByteArray = c.doFinal(input)
}
