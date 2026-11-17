package chat.kelo.app.approval

import chat.kelo.app.chat.ChatError
import chat.kelo.app.core.ApiError
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64

/**
 * The payment Allow's signed message and the payment rule, against docs/security/approval-sign-vectors.json (the server's
 * approval_sign.py makes the file; these checks are what every app must pass). Signatures are verified with the JVM's own ECDSA, so
 * the DER and SHA-256 format is proven here, not only the bytes.
 */
class ApprovalSignTest {
    private val vectorsFile: File by lazy {
        generateSequence(File("").absoluteFile) { it.parentFile }.take(6)
            .map { File(it, "docs/security/approval-sign-vectors.json") }.firstOrNull { it.isFile }
            ?: File("docs/security/approval-sign-vectors.json")
    }

    private val vectors: JsonObject by lazy { Json.parseToJsonElement(vectorsFile.readText()).jsonObject }

    private fun hex(s: String): ByteArray = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private fun publicKey(): java.security.PublicKey =
        KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(Base64.getDecoder().decode(vectors["public_key_spki_b64"]!!.jsonPrimitive.content)))

    private fun verify(message: ByteArray, sigB64: String): Boolean =
        Signature.getInstance("SHA256withECDSA").run {
            initVerify(publicKey())
            update(message)
            verify(Base64.getDecoder().decode(sigB64))
        }

    @Test fun theMessageBytesMatchTheSharedVectors() {
        assumeTrue("docs/security/approval-sign-vectors.json not found", vectorsFile.isFile)
        val approvals = vectors["approvals"]!!.jsonArray
        assertEquals(3, approvals.size)
        for (a in approvals) {
            val o = a.jsonObject
            val id = o["approval_id"]!!.jsonPrimitive.content
            val approve = o["decision"]!!.jsonPrimitive.content == "approve"
            val issuedAt = o["issued_at"]!!.jsonPrimitive.longOrNull!!
            val expected = hex(o["message_hex"]!!.jsonPrimitive.content)
            assertArrayEquals(expected, ApprovalSignRules.message(id, approve, issuedAt))
        }
    }

    @Test fun theSharedSignaturesVerifyWithTheJvmEcdsa() {
        assumeTrue("docs/security/approval-sign-vectors.json not found", vectorsFile.isFile)
        for (a in vectors["approvals"]!!.jsonArray) {
            val o = a.jsonObject
            val message = hex(o["message_hex"]!!.jsonPrimitive.content)
            assertTrue(o["approval_id"]!!.jsonPrimitive.content, verify(message, o["sig_b64"]!!.jsonPrimitive.content))
        }
    }

    @Test fun theBadVectorsFail() {
        assumeTrue("docs/security/approval-sign-vectors.json not found", vectorsFile.isFile)
        val bad = vectors["bad"]!!.jsonArray
        assertEquals(2, bad.size)
        for (b in bad) {
            val o = b.jsonObject
            val message = hex(o["message_hex"]!!.jsonPrimitive.content)
            assertFalse(o["name"]!!.jsonPrimitive.content, verify(message, o["sig_b64"]!!.jsonPrimitive.content))
        }
    }

    @Test fun aDenialHasItsOwnMessage() {
        val approve = ApprovalSignRules.message("x", true, 5).toString(Charsets.UTF_8)
        val deny = ApprovalSignRules.message("x", false, 5).toString(Charsets.UTF_8)
        assertEquals("kelo-approval v1\nx\napprove\n5", approve)
        assertEquals("kelo-approval v1\nx\ndeny\n5", deny)
    }

    /** The rule of server approval_sign.is_payment, on the approval_request event as the server sends it. */
    private fun event(vararg pairs: Pair<String, String?>): JsonObject = JsonObject(pairs.associate { (k, v) ->
        k to (v?.let { Json.parseToJsonElement(it) } ?: JsonNull)
    })

    @Test fun thePurchaseCardIsAPayment() {
        assertTrue(ApprovalSignRules.isPayment(event("name" to "\"confirm_purchase\"")))
    }

    @Test fun anyCardWithAPaymentPartIsAPayment() {
        assertTrue(ApprovalSignRules.isPayment(event("name" to "\"other\"", "payment" to "{\"amount\":1}")))
        assertFalse(ApprovalSignRules.isPayment(event("name" to "\"other\"", "payment" to "{}")))   // an empty object is false in Python
        assertFalse(ApprovalSignRules.isPayment(event("name" to "\"other\"", "payment" to "null")))
        assertFalse(ApprovalSignRules.isPayment(event("name" to "\"other\"", "payment" to "false")))
        assertTrue(ApprovalSignRules.isPayment(event("name" to "\"other\"", "payment" to "true")))
        assertTrue(ApprovalSignRules.isPayment(event("name" to "\"other\"", "payment" to "\"x\"")))
        assertFalse(ApprovalSignRules.isPayment(event("name" to "\"other\"", "payment" to "\"\"")))
    }

    @Test fun aBrowserClickThatPaysIsAPayment() {
        assertTrue(ApprovalSignRules.isPayment(event("name" to "\"browser_click\"", "commit" to "\"pay\"")))
        assertFalse(ApprovalSignRules.isPayment(event("name" to "\"browser_click\"", "commit" to "\"submit\"")))
    }

    @Test fun denialsAndOtherCardsAreNotPayments() {
        assertFalse(ApprovalSignRules.isPayment(event("name" to "\"browser_click\"")))
        assertFalse(ApprovalSignRules.isPayment(event("name" to "\"purchase_result\"", "commit" to "\"cancel\"")))
        assertFalse(ApprovalSignRules.isPayment(event()))
    }

    @Test fun aSignatureRequiredAnswerOnDesktopSaysToApproveOnThePhone() {
        val e = ApiError(403, "HTTP 403", detailJson = JsonObject(mapOf("code" to Json.parseToJsonElement("\"signature_required\""))), code = "signature_required")
        assertTrue(ApprovalSignErrors.isSignature(e))
        assertTrue(ApprovalSignErrors.error(e, signerAvailable = false) is ChatError.Localized)
    }

    @Test fun aPhoneShowsTheServersOwnMessage() {
        val detail = Json.parseToJsonElement("{\"code\":\"bad_signature\",\"message\":\"This payment approval was not signed by this phone.\"}").jsonObject
        val e = ApiError(403, "HTTP 403", detailJson = detail, code = "bad_signature")
        val err = ApprovalSignErrors.error(e, signerAvailable = true)
        assertEquals("This payment approval was not signed by this phone.", (err as ChatError.Message).text)
    }

    @Test fun anotherRefusalIsNotASignatureError() {
        assertFalse(ApprovalSignErrors.isSignature(ApiError(409, "HTTP 409", code = "locked")))
    }
}
