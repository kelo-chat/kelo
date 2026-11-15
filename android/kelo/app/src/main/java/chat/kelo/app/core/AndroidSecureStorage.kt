package chat.kelo.app.core

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/** SharedPreferences as a [KeyValueStore]. */
class SharedPrefsStore(private val prefs: SharedPreferences) : KeyValueStore {
    override fun getString(key: String): String? = prefs.getString(key, null)
    override fun putString(key: String, value: String) = prefs.edit().putString(key, value).apply()
    override fun remove(key: String) = prefs.edit().remove(key).apply()
}

/** AES-256-GCM with a key created inside the Android Keystore; the key material never leaves it. */
class KeystoreCipher(private val alias: String = DEFAULT_ALIAS) : AesGcmCipher({ keystoreKey(alias) }) {
    companion object {
        const val DEFAULT_ALIAS = "chat.kelo.app.store.v1"
        private const val PROVIDER = "AndroidKeyStore"

        @Synchronized
        fun keystoreKey(alias: String): SecretKey {
            val ks = KeyStore.getInstance(PROVIDER).apply { load(null) }
            (ks.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
            val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER)
            gen.init(
                KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .setRandomizedEncryptionRequired(true)
                    .build(),
            )
            return gen.generateKey()
        }
    }
}

fun secureStoreFor(context: Context): SecureStore = SecureStore(
    SharedPrefsStore(context.getSharedPreferences("kelo.store", Context.MODE_PRIVATE)),
    KeystoreCipher(),
)
