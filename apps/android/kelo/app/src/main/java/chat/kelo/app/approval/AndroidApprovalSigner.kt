package chat.kelo.app.approval

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
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.Base64

/**
 * This phone's approval key (docs/approval-signing.md): one EC P-256 key in the Android Keystore (alias [ALIAS], StrongBox when the
 * phone has one). Every signature asks for the owner's check, like the Keyguard key: Android 11+ with a per-use check (the prompt
 * carries the signature); Android 8-10 cannot pair a signature with the check, so a signature is allowed for [VALIDITY_SECONDS] after
 * it. A new face or finger keeps the key; removing the screen lock deletes it.
 */
object AndroidApprovalSigner : ApprovalSigner {
    private const val PROVIDER = "AndroidKeyStore"
    private const val ALIAS = "kelo-approval"
    private const val PREFS = "kelo.approval"
    private const val VALIDITY_SECONDS = 30
    private const val SIGNATURE_ALGORITHM = "SHA256withECDSA"

    @Volatile private var app: Context? = null
    @Volatile private var activity: () -> FragmentActivity? = { null }

    /** Called once by the app container: the application context and how to reach the screen's activity. */
    fun attach(context: Context, activity: () -> FragmentActivity?) {
        app = context.applicationContext
        this.activity = activity
    }

    override val available: Boolean get() = app != null

    override fun publicKey(): String? = runCatching {
        if (!keystore().containsAlias(ALIAS)) generate()
        keystore().getCertificate(ALIAS)?.publicKey?.encoded?.let { Base64.getEncoder().encodeToString(it) }
    }.getOrNull()

    override fun registered(serverUrl: String, publicKey: String): Boolean =
        prefs()?.getString(prefKey(serverUrl), null) == publicKey

    override fun markRegistered(serverUrl: String, publicKey: String) {
        prefs()?.edit()?.putString(prefKey(serverUrl), publicKey)?.apply()
    }

    override fun sign(message: ByteArray, title: String, subtitle: String, onDone: (ApprovalSigned) -> Unit) {
        val context = app ?: return onDone(ApprovalSigned.KeyLost)
        val act = activity() ?: return onDone(ApprovalSigned.Failed("no activity"))
        if (!deviceSecure(context)) return onDone(ApprovalSigned.NoLock)
        if (act.supportFragmentManager.isStateSaved || act.isFinishing) return onDone(ApprovalSigned.Cancelled)
        val perUse = perUse()
        val key = privateKey() ?: return onDone(ApprovalSigned.KeyLost)
        val signature: Signature? = if (perUse) {
            try {
                Signature.getInstance(SIGNATURE_ALGORITHM).apply { initSign(key) }
            } catch (e: Exception) {
                return onDone(classify(e))
            }
        } else null
        val prompt = BiometricPrompt(act, ContextCompat.getMainExecutor(act), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                try {
                    val s = if (perUse) result.cryptoObject?.signature else Signature.getInstance(SIGNATURE_ALGORITHM).apply { initSign(key) }
                    if (s == null) return onDone(ApprovalSigned.KeyLost)
                    s.update(message)
                    onDone(ApprovalSigned.Ok(Base64.getEncoder().encodeToString(s.sign())))   // DER, as the server reads it
                } catch (e: Exception) {
                    onDone(classify(e))
                }
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                onDone(when {
                    VaultGateRules.isCancel(errorCode) -> ApprovalSigned.Cancelled
                    errorCode == BiometricPrompt.ERROR_NO_DEVICE_CREDENTIAL -> ApprovalSigned.NoLock
                    else -> ApprovalSigned.Failed(errString.toString())
                })
            }
        })
        val info = BiometricPrompt.PromptInfo.Builder().setTitle(title).setSubtitle(subtitle)
            .setAllowedAuthenticators(if (perUse) BIOMETRIC_STRONG or DEVICE_CREDENTIAL else BIOMETRIC_WEAK or DEVICE_CREDENTIAL)
            .build()
        try {
            if (perUse) prompt.authenticate(info, BiometricPrompt.CryptoObject(signature!!)) else prompt.authenticate(info)
        } catch (e: Exception) {
            onDone(ApprovalSigned.Failed(e.message ?: e.javaClass.simpleName))
        }
    }

    @Synchronized
    private fun generate() {
        val context = app ?: return
        val strongBox = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P && context.packageManager.hasSystemFeature(PackageManager.FEATURE_STRONGBOX_KEYSTORE)
        if (strongBox) {
            try {
                generateKey(strongBox = true)
                return
            } catch (_: StrongBoxUnavailableException) {
            }
        }
        generateKey(strongBox = false)
    }

    private fun generateKey(strongBox: Boolean) {
        val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(true)
            .setInvalidatedByBiometricEnrollment(false)
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL)
                } else {
                    @Suppress("DEPRECATION")
                    setUserAuthenticationValidityDurationSeconds(VALIDITY_SECONDS)
                }
                if (strongBox && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) setIsStrongBoxBacked(true)
            }
            .build()
        KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, PROVIDER).apply { initialize(spec) }.generateKeyPair()
    }

    private fun privateKey(): PrivateKey? = (keystore().getEntry(ALIAS, null) as? KeyStore.PrivateKeyEntry)?.privateKey

    private fun keystore(): KeyStore = KeyStore.getInstance(PROVIDER).apply { load(null) }

    private fun deviceSecure(context: Context): Boolean =
        (context.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager)?.isDeviceSecure == true

    private fun perUse(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R

    private fun prefs() = app?.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Keyed by the server address: one phone may pair several servers, each keeps its own registration. */
    private fun prefKey(serverUrl: String): String =
        "reg." + MessageDigest.getInstance("SHA-256").digest(serverUrl.trimEnd('/').lowercase().toByteArray())
            .take(12).joinToString("") { "%02x".format(it) }

    private fun classify(e: Exception): ApprovalSigned = when (e) {
        is KeyPermanentlyInvalidatedException -> ApprovalSigned.KeyLost
        is UserNotAuthenticatedException -> ApprovalSigned.Cancelled
        else -> ApprovalSigned.Failed(e.message ?: e.javaClass.simpleName)
    }
}
