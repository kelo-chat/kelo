package chat.kelo.app.my

import chat.kelo.app.agent.DayGroups
import chat.kelo.app.res.*
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import org.jetbrains.compose.resources.StringResource

/**
 * One row of GET /api/did (or fallback to GET /api/actions).
 */
@Serializable
data class ActionItem(
    val id: Long = 0,
    val at: Double = 0.0,
    val ts: Double = 0.0,
    val kind: String = "",
    val title: String = "",
    val line: String = "",
    val detail: String = "",
    val source: String = "",
    @SerialName("source_name") val sourceName: String = "",
    val site: String? = null,
    val connector: String? = null,
    @SerialName("task_id") val taskId: Long? = null,
    val outcome: String = "",
    val state: String = "",
    @SerialName("run_id") val runId: String = "",
    @SerialName("turn_id") val turnId: String = "",
    val tool: String = "",
    val targets: List<String> = emptyList(),
    @SerialName("outside_id") val outsideId: String = "",
    @SerialName("checked_at") val checkedAt: Double? = null,
) {
    val timestamp: Double get() = if (at > 0.0) at else ts
    val displayTitle: String get() = title.ifBlank { line }
    val displayOutcome: String get() = outcome.ifBlank { state }.ifBlank { "done" }

    val normalizedKind: String get() = when (kind) {
        "send", "change", "delete" -> "action"
        else -> kind
    }
}

@Serializable
data class ActionList(
    val items: List<ActionItem> = emptyList(),
    @SerialName("next_before") val nextBefore: Double? = null,
)

/** Filter chips for "Kelo가 한 일": 전체 / 보내기·바꾸기 / 브라우저 / 작업. */
enum class DidFilter(val labelRes: StringResource) {
    ALL(Res.string.did_filter_all),
    ACTIONS(Res.string.did_filter_actions),
    BROWSER(Res.string.did_filter_browser),
    TASKS(Res.string.did_filter_tasks);

    fun matches(item: ActionItem): Boolean = when (this) {
        ALL -> true
        ACTIONS -> item.normalizedKind == "action"
        BROWSER -> item.normalizedKind == "browser"
        TASKS -> item.normalizedKind == "task"
    }
}

/** The time period chips for GET /api/did?days=N (or GET /api/actions?days=N). */
enum class ActionPeriod(val days: Int, val labelRes: StringResource) {
    TODAY(1, Res.string.actions_period_today),
    DAYS_7(7, Res.string.actions_period_7d),
    DAYS_30(30, Res.string.actions_period_30d);

    companion object {
        val DEFAULT = DAYS_7
    }
}

object ActionRules {
    /**
     * Groups action items by day: 오늘 (Today), 어제 (Yesterday), and dates in the reader's locale.
     * Newest first, preserved from the server list.
     */
    fun groupByDay(
        items: List<ActionItem>,
        today: LocalDate = LocalDate.now(),
        zone: ZoneId = ZoneId.systemDefault(),
    ): List<DayGroups.Group<ActionItem>> {
        return DayGroups.group(items, today) { item ->
            val ts = item.timestamp
            if (ts > 0) {
                Instant.ofEpochMilli((ts * 1000).toLong()).atZone(zone).toLocalDate()
            } else {
                null
            }
        }
    }
}
