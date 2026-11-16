package chat.kelo.app.update

import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.security.MessageDigest
import java.util.Base64

/**
 * The signature on a row of latest.json (server/src/assistant/release_sign.py writes it on the operator's computer). The signed text
 * (UTF-8, "\n" line ends, one final "\n") names the platform, the file, its version, build number, size and sha256, and the public
 * source commit it was built from, so a server that was broken into cannot swap the file or the numbers without the key.
 */
object ReleaseSignature {
    private val SIG = Regex("^[A-Za-z0-9+/]{86}==$")      // 64 raw bytes, standard base64, padded
    private val PUB = Regex("^[A-Za-z0-9+/]{43}=$")       // 32 raw bytes
    private val KEY_ID = Regex("^[0-9a-f]{16}$")

    /** The exact signed text. [code] <= 0 means no build number (empty in the text); an empty [commit] stays empty. */
    fun statement(platform: String, file: String, version: String, code: Long, size: Long, sha256: String, commit: String): ByteArray =
        ("kelo-release-v1\nplatform=$platform\nfile=$file\nversion=$version\nversion_code=${if (code > 0) code.toString() else ""}\n" +
            "size=$size\nsha256=$sha256\ncommit=$commit\n").toByteArray(Charsets.UTF_8)

    /** True only when [sigB64] is a valid signature of the row by the key [keyId] in [keys]. False on anything malformed; never throws. */
    fun verify(
        platform: String, file: String, version: String, code: Long, size: Long, sha256: String, commit: String,
        keyId: String, sigB64: String, keys: Map<String, String> = ReleaseKeys.ALL,
    ): Boolean = runCatching {
        val pubB64 = keys[keyId] ?: return false
        if (!KEY_ID.matches(keyId) || !SIG.matches(sigB64) || !PUB.matches(pubB64)) return false
        if (commit.isNotEmpty() && !Regex("^[0-9a-f]{40}$").matches(commit)) return false
        val pub = Base64.getDecoder().decode(pubB64)
        val sig = Base64.getDecoder().decode(sigB64)
        if (pub.size != 32 || sig.size != 64) return false
        val id = MessageDigest.getInstance("SHA-256").digest(pub).joinToString("") { "%02x".format(it) }.take(16)
        if (id != keyId) return false
        val text = statement(platform, file, version, code, size, sha256, commit)
        Ed25519Signer().run {
            init(false, Ed25519PublicKeyParameters(pub, 0))
            update(text, 0, text.size)
            verifySignature(sig)
        }
    }.getOrDefault(false)
}

/** Where an update may be downloaded from: only the address the list itself came from, over https. */
object UpdateUrls {
    private val LOCAL = setOf("127.0.0.1", "localhost", "[::1]", "::1")

    /** [resolved] has the scheme, host and port of [base] and is https (plain http only for 127.0.0.1, localhost and ::1: tests, local mocks). */
    fun allowed(base: String, resolved: String): Boolean = runCatching {
        val b = java.net.URI(base)
        val r = java.net.URI(resolved)
        val scheme = r.scheme?.lowercase() ?: return false
        val host = r.host?.lowercase() ?: return false
        if (r.userInfo != null || scheme != b.scheme?.lowercase() || host != b.host?.lowercase()) return false
        if (portOf(r) != portOf(b)) return false
        scheme == "https" || (scheme == "http" && host in LOCAL)
    }.getOrDefault(false)

    private fun portOf(u: java.net.URI): Int = if (u.port != -1) u.port else if (u.scheme.equals("https", ignoreCase = true)) 443 else 80
}
