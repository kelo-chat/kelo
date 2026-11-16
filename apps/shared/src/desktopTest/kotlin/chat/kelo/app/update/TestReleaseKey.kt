package chat.kelo.app.update

import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.security.MessageDigest
import java.util.Base64

/**
 * A TEST signing key (never the release key; its seed is public on purpose) so tests can sign the rows of a mock latest.json. Its public
 * half is the one in resources/release_signature.json, the vector the Python side checks too (server/tests/test_release_sign.py).
 */
object TestReleaseKey {
    private const val SEED_HEX = "bff66840f298f431b43fffc5de5f1475e905c90cd3b82c5c06e81ae513cf49fa"
    private val priv = Ed25519PrivateKeyParameters(SEED_HEX.chunked(2).map { it.toInt(16).toByte() }.toByteArray(), 0)
    private val pub: ByteArray = priv.generatePublicKey().encoded
    val PUB_B64: String = Base64.getEncoder().encodeToString(pub)
    val KEY_ID: String = MessageDigest.getInstance("SHA-256").digest(pub).joinToString("") { "%02x".format(it) }.take(16)

    /** What the app is told to trust in tests: [ReleaseKeys.ALL]'s shape with the test key. */
    val KEYS: Map<String, String> = mapOf(KEY_ID to PUB_B64)

    /** The base64 signature of a row (the same text the server signs). */
    fun sign(platform: String, file: String, version: String, code: Long, size: Long, sha256: String, commit: String = ""): String {
        val text = ReleaseSignature.statement(platform, file, version, code, size, sha256, commit)
        val sig = Ed25519Signer().run {
            init(true, priv)
            update(text, 0, text.size)
            generateSignature()
        }
        return Base64.getEncoder().encodeToString(sig)
    }

    /** `"sig": "...", "key": "..."` (and the commit when there is one) as JSON members to put into a row of a mock latest.json. */
    fun members(platform: String, file: String, version: String, code: Long, size: Long, sha256: String, commit: String = ""): String =
        "\"sig\": \"${sign(platform, file, version, code, size, sha256, commit)}\", \"key\": \"$KEY_ID\"" +
            (if (commit.isEmpty()) "" else ", \"commit\": \"$commit\"")
}
