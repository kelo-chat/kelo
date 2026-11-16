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
 * One row of GET /api/actions (What Kelo did outside; server api/actions.py, done_once.py).
 */
@Serializable
data class ActionItem(
    val id: Long = 0,
    val ts: Double = 0.0,
    @SerialName("run_id") val runId: String = "",
    @SerialName("task_id") val taskId: Long? = null,
    @SerialName("turn_id") val turnId: String = "",
    val source: String = "",
    @SerialName("source_name") val sourceName: String = "",
    val line: String = "",
    val tool: String = "",
    val kind: String = "",
    val connector: String? = null,
    val targets: List<String> = emptyList(),
    val state: String = "done",
    @SerialName("outside_id") val outsideId: String = "",
    val detail: String = "",
    @SerialName("checked_at") val checkedAt: Double? = null,
)

@Serializable
data class ActionList(
    val items: List<ActionItem> = emptyList(),
)

/** The time period chips for GET /api/actions?days=N. */
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
            if (item.ts > 0) {
                Instant.ofEpochMilli((item.ts * 1000).toLong()).atZone(zone).toLocalDate()
            } else {
                null
            }
        }
    }
}
