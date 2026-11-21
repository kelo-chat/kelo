package chat.kelo.app.goals

import chat.kelo.app.core.Goal
import chat.kelo.app.follow.FollowItem
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * One row of the one list of what Kelo carries (GET /api/carried): a work item Kelo took on, with the follow items of the same
 * thing under it ([follow]); or a follow item on its own ([key] "follow:<key>", [follow] holds just that item).
 */
@Serializable
data class CarriedItem(
    /** "work:<id>" or "follow:<key>" */
    val key: String = "",
    val title: String = "",
    /** open | done | planned | watching | stopped (a follow item's own state) */
    val state: String = "open",
    @SerialName("starts_at") val startsAt: Double? = null,
    @SerialName("all_day") val allDay: Boolean = false,
    /** The work item (a work row), else null */
    val work: Goal? = null,
    val follow: List<FollowItem> = emptyList(),
)

@Serializable
data class CarriedList(val items: List<CarriedItem> = emptyList())

/** The one list's rules, apart from the UI. */
object CarriedRules {
    fun isWork(item: CarriedItem): Boolean = item.key.startsWith("work:")

    /** A work row's follow items; a follow row's own item. */
    fun linked(item: CarriedItem): List<FollowItem> = if (isWork(item)) item.follow else item.follow.take(1)

    /** The open rows, the nearest date first and the undated ones after them (the server's order is kept for equal dates); the done
     *  and stopped rows last. Each thing once. */
    fun ordered(items: List<CarriedItem>): List<CarriedItem> {
        fun open(i: CarriedItem) = i.state != "done" && i.state != "stopped"
        val (live, rest) = items.partition(::open)
        val dated = live.filter { it.startsAt != null }.sortedBy { it.startsAt }
        val undated = live.filter { it.startsAt == null }
        return dated + undated + rest
    }

    /** The follow items of a work item, by the work item's id ("work:<id>"), from the one list. */
    fun followOf(items: List<CarriedItem>, goalId: Long): List<FollowItem> =
        items.firstOrNull { it.key == "work:$goalId" }?.follow.orEmpty()
}
