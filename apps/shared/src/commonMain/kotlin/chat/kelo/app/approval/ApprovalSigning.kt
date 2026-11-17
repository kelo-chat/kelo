package chat.kelo.app.approval

import chat.kelo.app.chat.ChatError
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.res.Res
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.loadString
import kotlin.coroutines.cancellation.CancellationException
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Signed payment approvals (docs/approval-signing.md, docs/security/e2e-and-phone-key.md part 6). The phone signs the Allow of a
 * payment card with its approval key (ES256, in the hardware keystore, behind fingerprint or face). Desktop has no key: it sends
 * no signature and the server answers 403 signature_required.
 */
const val APPROVAL_ALG = "ES256"

/** What the phone's approval key gave back for one signature. */
sealed interface ApprovalSigned {
    /** [sig]: base64 DER ECDSA P-256 over SHA-256 of the message */
    data class Ok(val sig: String) : ApprovalSigned
    /** The owner backed out of the check */
    data object Cancelled : ApprovalSigned
    /** The phone has no screen lock, so no key can ask for the check */
    data object NoLock : ApprovalSigned
    /** This phone has no usable approval key */
    data object KeyLost : ApprovalSigned
    data class Failed(val message: String) : ApprovalSigned
}

/**
 * This phone's approval key and the owner's check that comes with each signature. [None]: the device holds no key (the desktop):
 * the approval is sent without a signature.
 */
interface ApprovalSigner {
    /** This device can sign at all. */
    val available: Boolean

    /** SPKI DER, base64, of this phone's approval key (made on first use), or null when there is none. */
    fun publicKey(): String?

    /** Whether [publicKey] was already registered with the server at [serverUrl] from this phone. */
    fun registered(serverUrl: String, publicKey: String): Boolean = false

    /** Remembers that [publicKey] is registered with [serverUrl]. */
    fun markRegistered(serverUrl: String, publicKey: String) {}

    /** The owner's check, then one signature over [message] with the approval key. [onDone] is called exactly once. */
    fun sign(message: ByteArray, title: String, subtitle: String, onDone: (ApprovalSigned) -> Unit) = onDone(ApprovalSigned.KeyLost)

    companion object {
        val None: ApprovalSigner = object : ApprovalSigner {
            override val available = false
            override fun publicKey(): String? = null
        }
    }
}

/** The bytes the phone signs, and whether the server checks a signature for an approval. */
object ApprovalSignRules {
    const val MESSAGE_PREFIX = "kelo-approval v1\n"
    const val SIGNATURE_REQUIRED = "signature_required"
    const val BAD_SIGNATURE = "bad_signature"

    /** UTF-8 of "kelo-approval v1\n" + approval_id + "\n" + ("approve" | "deny") + "\n" + issued_at (server: approval_sign.approval_message). */
    fun message(approvalId: String, approve: Boolean, issuedAt: Long): ByteArray =
        "$MESSAGE_PREFIX$approvalId\n${if (approve) "approve" else "deny"}\n$issuedAt".toByteArray(Charsets.UTF_8)

    /**
     * The server's approval_sign.is_payment on the approval_request event: `bool(event.get("payment"))`, or the purchase card
     * (name confirm_purchase), or a browser click judged to be the paying one (commit "pay"). Python truthiness is mirrored.
     */
    fun isPayment(event: JsonObject): Boolean =
        truthy(event["payment"]) || str(event["name"]) == "confirm_purchase" || str(event["commit"]) == "pay"

    private fun str(e: JsonElement?): String? = (e as? JsonPrimitive)?.takeIf { it.isString }?.content

    private fun truthy(e: JsonElement?): Boolean = when (e) {
        null, JsonNull -> false
        is JsonObject -> e.isNotEmpty()
        is JsonArray -> e.isNotEmpty()
        is JsonPrimitive -> when {
            e.isString -> e.content.isNotEmpty()
            e.content == "true" -> true
            e.content == "false" -> false
            else -> e.content.toDoubleOrNull()?.let { it != 0.0 } ?: false
        }
    }
}

/** What the Allow of a payment came to on this phone. */
sealed interface ApprovalSignOutcome {
    /** [sig] is null when this phone cannot sign (the server then decides, as before) */
    data class Ready(val sig: String?) : ApprovalSignOutcome
    /** The owner backed out: the card stays open, nothing is sent */
    data object Cancelled : ApprovalSignOutcome
    /** The card stays open and [error] says why */
    data class Failed(val error: ChatError) : ApprovalSignOutcome
}

/** The owner's check and the signature of an Allow of a payment (suspends until the check is answered). */
suspend fun signPaymentAllow(signer: ApprovalSigner, approvalId: String, issuedAt: Long): ApprovalSignOutcome {
    if (!signer.available) return ApprovalSignOutcome.Ready(null)
    val title = loadString(Res.string.approval_sign_title)
    val subtitle = loadString(Res.string.approval_sign_subtitle)
    val done = suspendCancellableCoroutine<ApprovalSigned> { k ->
        signer.sign(ApprovalSignRules.message(approvalId, true, issuedAt), title, subtitle) { k.resume(it) }
    }
    return when (done) {
        is ApprovalSigned.Ok -> ApprovalSignOutcome.Ready(done.sig)
        ApprovalSigned.Cancelled -> ApprovalSignOutcome.Cancelled
        ApprovalSigned.NoLock -> ApprovalSignOutcome.Failed(ChatError.Localized(Res.string.keyguard_no_lock))
        ApprovalSigned.KeyLost -> ApprovalSignOutcome.Failed(ChatError.Localized(Res.string.approval_sign_key_lost))
        is ApprovalSigned.Failed -> ApprovalSignOutcome.Failed(ChatError.Localized(Res.string.approval_sign_failed))
    }
}

/** What a refused payment Allow (403 signature_required / bad_signature) says. */
object ApprovalSignErrors {
    fun isSignature(e: ApiError): Boolean = e.code == ApprovalSignRules.SIGNATURE_REQUIRED || e.code == ApprovalSignRules.BAD_SIGNATURE

    /** Desktop (no key) gets the fixed "approve on the phone" line; a phone shows the server's own message. */
    fun error(e: ApiError, signerAvailable: Boolean): ChatError {
        if (e.code == ApprovalSignRules.SIGNATURE_REQUIRED && !signerAvailable) return ChatError.Localized(Res.string.approval_pay_on_phone)
        val serverMessage = (e.detailJson?.get("message") as? JsonPrimitive)?.takeIf { it.isString }?.content
        return serverMessage?.takeIf { it.isNotBlank() }?.let { ChatError.Message(it) } ?: ChatError.Localized(Res.string.approval_sign_failed)
    }
}

/** Registers this phone's approval key with the server once per key (POST /api/devices/approval-key). */
object ApprovalKeyRegistrar {
    suspend fun refresh(signer: ApprovalSigner, api: KeloApi) {
        if (!signer.available) return
        val pub = signer.publicKey() ?: return
        if (signer.registered(api.baseUrl, pub)) return
        try {
            api.post<JsonElement>("/api/devices/approval-key", buildJsonObject {
                put("alg", APPROVAL_ALG)
                put("public_key", pub)
            })
            signer.markRegistered(api.baseUrl, pub)
        } catch (e: CancellationException) {
            throw e
        } catch (_: ApiError) {
            // 404: an older server, asked again on the next start. 403 signature_required: the server keeps another key for this
            // phone (its keystore was wiped); the device must be removed and paired again. Nothing else to do here.
        }
    }
}
