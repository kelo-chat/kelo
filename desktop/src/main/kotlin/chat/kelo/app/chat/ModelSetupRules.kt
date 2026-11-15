package chat.kelo.app.chat

import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Profile
import chat.kelo.app.my.ProviderDetail
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// The plain rules of the phone's model settings, copied by hand because their Android files also hold Android UI:
// SignInSession / SignInRules from android/kelo .../chat/SignIn.kt, EndpointRules / KeyRules from .../my/ModelPage.kt,
// ModelChoice / ModelRules from .../chat/ModelSheet.kt, mmss / secondsLeft from .../connectors/ConnectorRules.kt.
// Keep them in step. SetupRules is the desktop's own (which providers the model sheet offers to set up).

/** POST /api/providers/{name}/login/start, GET …/login/status and POST …/login/finish answer with this. */
@Serializable
data class SignInSession(
    val session: String = "",
    /** "device_code": open [url], type [userCode] there, the app notices by itself. "url_code": open [url], paste back the code */
    val kind: String = "",
    val url: String = "",
    @SerialName("user_code") val userCode: String? = null,
    @SerialName("expires_in") val expiresIn: Long = 0,
    /** pending | done | failed | expired */
    val state: String = "pending",
    val message: String? = null,
    val account: String? = null,
)

/** When the sign-in shows and what the server's answers mean. */
object SignInRules {
    enum class Problem { CONSENT, ALREADY, OFF, FAILED }

    /** A provider the app can sign in (the server says `signin`) that is not signed in now. */
    fun offered(signin: Boolean, signedIn: Boolean?): Boolean = signin && signedIn != true

    fun isDeviceCode(s: SignInSession): Boolean = s.kind == "device_code"

    /** The pasted code (or the address the page ended on) can be sent: one line without spaces. */
    fun pasteReady(text: String): Boolean = text.trim().let { it.isNotEmpty() && it.none(Char::isWhitespace) && it.length <= 4096 }

    fun finished(s: SignInSession): Boolean = s.state == "done"

    /** The attempt ended without signing in; the user starts again. */
    fun ended(s: SignInSession): Boolean = s.state == "failed" || s.state == "expired"

    fun problem(e: ApiError): Problem = when {
        e.status == 428 -> Problem.CONSENT
        e.status == 409 && e.detail.startsWith("already_signed_in") -> Problem.ALREADY
        e.status == 503 -> Problem.OFF
        else -> Problem.FAILED
    }

    /** The server's own sentence, without a leading machine word ("already_signed_in: …"). */
    fun readable(e: ApiError): String = if (e.isNetwork) "" else e.detail.substringAfter("already_signed_in: ").trim()

    /** Seconds as m:ss. */
    fun mmss(seconds: Long): String {
        val s = seconds.coerceAtLeast(0)
        return "${s / 60}:${(s % 60).toString().padStart(2, '0')}"
    }

    /** Whole seconds left until [deadlineMs], never below zero. */
    fun secondsLeft(deadlineMs: Long, nowMs: Long): Long = Math.round((deadlineMs - nowMs).coerceAtLeast(0) / 1000.0)
}

/** Checks the address of an OpenAI-compatible server before it is sent (the server checks again). */
object EndpointRules {
    enum class Problem { EMPTY, SPACES, SCHEME, HOST }

    /** Null when [url] looks usable: http or https, a host, no spaces. */
    fun check(url: String): Problem? {
        val t = url.trim()
        if (t.isEmpty()) return Problem.EMPTY
        if (t.any { it.isWhitespace() }) return Problem.SPACES
        val scheme = t.substringBefore("://", "").lowercase()
        if (scheme != "http" && scheme != "https") return Problem.SCHEME
        val host = runCatching { java.net.URI(t).host }.getOrNull()
            ?: t.substringAfter("://").substringBefore('/').substringBefore('?').substringBefore('#').substringAfterLast('@').substringBefore(':')
        return if (host.isBlank()) Problem.HOST else null
    }
}

/** Which API key box a provider gets, from where its key comes (ProviderDetail.keySource). */
object KeyRules {
    enum class State { IN_APP, ENV, NONE }

    fun state(keySource: String?): State = when (keySource) {
        "app" -> State.IN_APP
        "env" -> State.ENV
        else -> State.NONE
    }

    /** A pasted key long enough to be worth sending (the server checks it properly). */
    fun ready(key: String): Boolean = key.trim().length >= 8
}

/** A provider + model pair as the settings edit it. */
data class ModelChoice(val provider: String, val model: String) {
    companion object {
        val NONE = ModelChoice("", "")
    }
}

object ModelRules {
    /** A fallback on the main model's own provider (from before that rule) is shown as empty. */
    fun initialFallback(p: Profile): ModelChoice =
        if (p.fallbackProvider.isNotEmpty() && p.fallbackProvider == p.provider) ModelChoice.NONE else ModelChoice(p.fallbackProvider, p.fallbackModel)

    /** Moving the main model onto the fallback's provider empties the fallback. Returns the new fallback and whether it was cleared. */
    fun afterMainChange(main: ModelChoice, fallback: ModelChoice): Pair<ModelChoice, Boolean> =
        if (main.provider.isNotEmpty() && main.provider == fallback.provider) ModelChoice.NONE to true else fallback to false

    /** A chosen provider needs a model name. */
    fun complete(c: ModelChoice): Boolean = c.provider.isEmpty() || c.model.isNotBlank()
}

/** The desktop model sheet's setup part: a server with no ready provider must still lead somewhere. */
object SetupRules {
    /** Something the app itself can do to make [p] ready: paste a key, enter an address, sign in, or install / sign in its CLI. */
    fun canSetUp(p: ProviderDetail): Boolean =
        p.takesKey || p.takesUrl || SignInRules.offered(p.signin, p.signedIn) || (p.cli != null && (!p.cli.installed && p.cli.installable))

    /**
     * The rows "<service> (needs setup) ›" under the ready providers' models: every provider that is not ready and can be set up
     * from the app, official ones first, then by name (the unofficial ones under their own heading).
     */
    fun rows(providers: List<ProviderDetail>): List<ProviderDetail> =
        providers.filter { it.configured != true && canSetUp(it) }
            .sortedWith(compareBy<ProviderDetail>({ if (it.unofficial) 1 else 0 }, { it.displayName.ifBlank { it.name }.lowercase() }))

    /** The thinking-level row only means something once a model is chosen. */
    fun showsEffort(profile: Profile): Boolean = profile.model.isNotBlank()

    /** The settings can be saved: a provider and a model name (the server's default needs neither, when it has one). */
    fun canSave(provider: String, model: String, serverHasDefault: Boolean): Boolean =
        if (provider.isEmpty()) serverHasDefault else model.isNotBlank()
}
