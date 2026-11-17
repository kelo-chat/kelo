package chat.kelo.app.agent

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One topic Kelo was asked to forget (GET /api/forget). The words it was found by never leave the server. */
@Serializable
data class ForgottenTopic(
    val id: String,
    val label: String = "",
    @SerialName("created_at") val createdAt: Double = 0.0,
    /** What the cleanup removed, per everyday category (memory, notes, reminders, skills, history) */
    val removed: Map<String, Int> = emptyMap(),
)

@Serializable
data class ForgottenList(val items: List<ForgottenTopic> = emptyList())

/** One place the waiting cleanup would delete from: its category and a short preview of the owner's own text. */
@Serializable
data class ForgetPlanItem(val category: String = "", val show: String = "")

/** GET /api/forget/plans/{id}: the list under the forget card. */
@Serializable
data class ForgetPlanView(val id: String = "", val label: String = "", val items: List<ForgetPlanItem> = emptyList())

/** What the forgotten-topics page and the forget card decide, away from the screens so it can be tested. */
object ForgetRules {
    /** The everyday categories the server names (forget.CATEGORY_LABELS), in the order they are told. */
    val CATEGORIES: List<String> = listOf("memory", "notes", "reminders", "skills", "history")

    const val PREVIEW_CHARS = 60

    /** The newest topic first. */
    fun ordered(items: List<ForgottenTopic>): List<ForgottenTopic> = items.sortedByDescending { it.createdAt }

    fun without(items: List<ForgottenTopic>, id: String): List<ForgottenTopic> = items.filter { it.id != id }

    /** (category, count) for what was removed, known categories only, in a fixed order, zeros left out. */
    fun parts(removed: Map<String, Int>): List<Pair<String, Int>> =
        CATEGORIES.mapNotNull { c -> removed[c]?.takeIf { it > 0 }?.let { c to it } }

    /** A preview of at most [PREVIEW_CHARS] characters on one line (never cutting a pair of surrogate chars). */
    fun preview(show: String): String {
        val one = show.trim().replace(Regex("\\s+"), " ")
        if (one.codePointCount(0, one.length) <= PREVIEW_CHARS) return one
        val end = one.offsetByCodePoints(0, PREVIEW_CHARS - 1)
        return one.substring(0, end).trimEnd() + "…"
    }

    /** The lines under the forget card: "category · preview", the empty ones dropped. */
    fun lines(plan: ForgetPlanView, categoryWord: (String) -> String): List<String> = plan.items.mapNotNull { i ->
        val p = preview(i.show)
        if (p.isEmpty()) null else "${categoryWord(i.category)} · $p"
    }
}

/** The forget routes (docs/forget.md). */
object ForgetCalls {
    suspend fun list(api: chat.kelo.app.core.KeloApi): List<ForgottenTopic> = ForgetRules.ordered(api.get<ForgottenList>("/api/forget").items)

    /** "Allow again": done, or already gone (404). */
    suspend fun allowAgain(api: chat.kelo.app.core.KeloApi, id: String) {
        try {
            api.delete<kotlinx.serialization.json.JsonElement>("/api/forget/${chat.kelo.app.core.KeloApi.segment(id)}")
        } catch (e: chat.kelo.app.core.ApiError) {
            if (e.status != 404) throw e
        }
    }

    /** The waiting plan's list; null when it is gone or too old (404). */
    suspend fun plan(api: chat.kelo.app.core.KeloApi, id: String): ForgetPlanView? = try {
        api.get<ForgetPlanView>("/api/forget/plans/${chat.kelo.app.core.KeloApi.segment(id)}")
    } catch (e: chat.kelo.app.core.ApiError) {
        if (e.status == 404) null else throw e
    }
}
