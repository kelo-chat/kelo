package chat.kelo.app.update

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

/**
 * The signed text and the signature check, against the one vector the Python side reads too (resources/release_signature.json,
 * server/tests/test_release_sign.py): made once with a TEST key, never the release key.
 */
class ReleaseSignatureTest {
    private val v: JsonObject = Json.parseToJsonElement(
        javaClass.getResourceAsStream("/release_signature.json")!!.readBytes().toString(Charsets.UTF_8),
    ).jsonObject
    private val row = v.getValue("row").jsonObject
    private fun s(o: JsonObject, k: String) = o.getValue(k).jsonPrimitive.content
    private val platform = s(v, "platform")
    private val keyId = s(v, "key_id")
    private val sig = s(v, "sig")
    private val keys = mapOf(keyId to s(v, "public_key"))

    private fun verify(
        platform: String = this.platform, file: String = s(row, "file"), version: String = s(row, "version"),
        code: Long = s(row, "version_code").toLong(), size: Long = s(row, "size").toLong(), sha: String = s(row, "sha256"),
        commit: String = s(row, "commit"), keyId: String = this.keyId, sig: String = this.sig, keys: Map<String, String> = this.keys,
    ) = ReleaseSignature.verify(platform, file, version, code, size, sha, commit, keyId, sig, keys)

    @Test fun theSignedTextIsExactlyTheVectorsText() {
        val text = ReleaseSignature.statement(platform, s(row, "file"), s(row, "version"), s(row, "version_code").toLong(),
            s(row, "size").toLong(), s(row, "sha256"), s(row, "commit"))
        assertEquals(s(v, "statement"), text.toString(Charsets.UTF_8))
        // no build number and no commit: the lines stay, empty
        assertEquals("kelo-release-v1\nplatform=mac\nfile=k.dmg\nversion=1.0\nversion_code=\nsize=5\nsha256=ab\ncommit=\n",
            ReleaseSignature.statement("mac", "k.dmg", "1.0", 0, 5, "ab", "").toString(Charsets.UTF_8))
    }

    @Test fun theTestKeyIsTheVectorsKey() {
        assertEquals(s(v, "public_key"), TestReleaseKey.PUB_B64)
        assertEquals(keyId, TestReleaseKey.KEY_ID)
        assertEquals(sig, TestReleaseKey.sign(platform, s(row, "file"), s(row, "version"), s(row, "version_code").toLong(),
            s(row, "size").toLong(), s(row, "sha256"), s(row, "commit")))   // Ed25519 is deterministic: both languages sign the same bytes
    }

    @Test fun theVectorsSignatureVerifies() {
        assertTrue(verify())
    }

    @Test fun changingAnySingleFieldBreaksIt() {
        assertFalse(verify(file = "kelo-1.2.3-4243.exe"))
        assertFalse(verify(version = "1.2.4"))
        assertFalse(verify(code = 4243))
        assertFalse(verify(code = 0))
        assertFalse(verify(size = 1234568))
        assertFalse(verify(sha = "0".repeat(64)))
        assertFalse(verify(commit = "0".repeat(40)))
        assertFalse(verify(commit = ""))
    }

    @Test fun anotherPlatformAnUnknownKeyAndBrokenEncodingsFail() {
        assertFalse(verify(platform = "windows-msi"))
        assertFalse(verify(platform = "mac"))
        assertFalse(verify(keyId = "0123456789abcdef"))                 // not in the list
        assertFalse(verify(keys = emptyMap()))
        assertFalse(verify(sig = sig.dropLast(4) + "!!!!"))              // broken base64
        assertFalse(verify(sig = sig.dropLast(2)))                       // not padded
        assertFalse(verify(sig = sig.replace('A', 'B').replace('f', 'g')))
        assertFalse(verify(sig = ""))
        assertFalse(verify(sig = Base64.getEncoder().encodeToString(ByteArray(63))))   // not 64 bytes
        assertFalse(verify(keys = mapOf(keyId to "not base64")))
        assertFalse(verify(keys = mapOf(keyId to Base64.getEncoder().encodeToString(ByteArray(33)))))
        // a key listed under an id that is not its own is refused
        assertFalse(verify(keyId = "0123456789abcdef", keys = mapOf("0123456789abcdef" to s(v, "public_key"))))
    }
}
