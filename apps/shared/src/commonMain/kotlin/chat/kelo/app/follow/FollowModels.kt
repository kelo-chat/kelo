package chat.kelo.app.follow

import chat.kelo.app.core.KeloApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/** One thing Kelo checks for an upcoming item (flight gate/status, page condition, etc.). */
@Serializable
data class FollowCheck(
    val kind: String = "",
    val fields: List<String> = emptyList(),
    val from: String = "",
    val until: String = "",
    val state: String = "active",
    val last: JsonObject? = null,
    @SerialName("next_at") val nextAt: Double? = null,
)

/** One preparation Kelo runs ahead of time (online check-in, booking check, documents). */
@Serializable
data class FollowPrep(
    val what: String = "",
    val `when`: String = "",
    val state: String = "later", // "later", "running", "done", "failed"
    val kind: String = "", // "check_in" | "other" (the plan's own mark; "" from an older server): the words of [what] are never read
)

@Serializable
data class FollowPlanTell(
    val lead: List<String> = emptyList(),
    @SerialName("on_change") val onChange: Boolean = true,
)

@Serializable
data class FollowPlanData(
    val checks: List<JsonObject> = emptyList(),
    val prep: List<FollowPrep> = emptyList(),
    val tell: FollowPlanTell? = null,
)

/** One item Kelo is following or watching (GET /api/follow). */
@Serializable
data class FollowItem(
    val key: String = "",
    val kind: String = "",
    val title: String = "",
    @SerialName("starts_at") val startsAt: Double = 0.0,
    val checks: List<FollowCheck> = emptyList(),
    val prep: List<FollowPrep> = emptyList(),
    val plan: FollowPlanData? = null,
    val state: String = "planned", // "planned", "watching", "done", "stopped"
) {
    /** The earliest upcoming check among active checks. */
    val nextCheck: Double?
        get() = checks.filter { it.state == "active" && it.nextAt != null }.minOfOrNull { it.nextAt!! }
}

@Serializable
data class FollowItemList(
    val items: List<FollowItem> = emptyList(),
)

@Serializable
data class FollowActionResponse(
    val ok: Boolean = false,
    val key: String = "",
    val state: String = "",
)

object FollowCalls {
    suspend fun list(api: KeloApi): List<FollowItem> =
        api.get<FollowItemList>("/api/follow").items

    suspend fun stop(api: KeloApi, key: String): FollowActionResponse =
        api.post<FollowActionResponse>("/api/follow/${KeloApi.segment(key)}/stop")

    suspend fun replan(api: KeloApi, key: String): FollowActionResponse =
        api.post<FollowActionResponse>("/api/follow/${KeloApi.segment(key)}/replan")
}
