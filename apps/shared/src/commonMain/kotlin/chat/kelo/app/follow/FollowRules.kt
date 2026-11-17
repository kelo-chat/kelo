package chat.kelo.app.follow

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*
import chat.kelo.app.ui.theme.Kelo
import org.jetbrains.compose.resources.StringResource

object FollowRules {
    /** Maps a prep state ("later", "running", "done", "failed") to its string resource. */
    fun prepStateRes(state: String): StringResource = when (state) {
        "running" -> Res.string.follow_prep_running
        "done" -> Res.string.follow_prep_done
        "failed" -> Res.string.follow_prep_failed
        else -> Res.string.follow_prep_later
    }

    /** Maps a prep state to a natural Korean or English word for non-Compose contexts / tests. */
    fun prepStateWord(state: String, isKo: Boolean = true): String = when (state) {
        "running" -> if (isKo) "하는 중" else "In progress"
        "done" -> if (isKo) "해 뒀어요" else "Done ahead"
        "failed" -> if (isKo) "실패" else "Failed"
        else -> if (isKo) "예정" else "Scheduled"
    }

    /** Maps an item state to its string resource. */
    fun itemStateRes(state: String): StringResource = when (state) {
        "stopped" -> Res.string.follow_state_stopped
        "done" -> Res.string.follow_state_done
        else -> Res.string.follow_state_watching
    }

    /**
     * Builds one line of what Kelo does from the plan:
     * e.g. "출발 하루 전 알림 · 체크인 열리면 준비" from the plan.
     */
    fun planSummary(item: FollowItem, isKo: Boolean = true): String {
        val parts = mutableListOf<String>()
        val tell = item.plan?.tell
        val lead = tell?.lead.orEmpty()
        val hasOneDayLead = lead.any { it == "-1d" || it == "-24h" }
        val hasLeadHour = lead.firstOrNull { it.endsWith("h") && it != "-24h" }

        // 1. Lead notification
        if (hasOneDayLead) {
            val isFlightOrTrip = item.kind in setOf("flight", "trip")
            parts += if (isKo) {
                if (isFlightOrTrip) "출발 하루 전 알림" else "하루 전 알림"
            } else {
                "Notification 1 day before"
            }
        } else if (hasLeadHour != null) {
            val hours = hasLeadHour.trimStart('+', '-').trimEnd('h', 'H').toIntOrNull() ?: 3
            parts += if (isKo) "${hours}시간 전 알림" else "Notification $hours hours before"
        }

        // 2. Preparation
        val allPreps = if (item.prep.isNotEmpty()) item.prep else item.plan?.prep.orEmpty()
        val checkInPrep = allPreps.firstOrNull { it.kind == "check_in" }
        if (checkInPrep != null) {
            parts += if (isKo) "체크인 열리면 준비" else "Prepare check-in when open"
        } else if (allPreps.isNotEmpty()) {
            val first = allPreps.first().what
            if (first.isNotBlank()) {
                parts += if (isKo) "$first 준비" else "Prepare $first"
            }
        }

        // 3. Fallback
        if (parts.isEmpty()) {
            if (item.checks.isNotEmpty() || tell?.onChange == true) {
                parts += if (isKo) "변경 사항 확인" else "Watch for changes"
            } else {
                parts += if (isKo) "일정 챙기기" else "Look after schedule"
            }
        }

        return parts.joinToString(" · ")
    }
}

@Composable
fun prepStateText(state: String): String = string(FollowRules.prepStateRes(state))

@Composable
fun prepStateTone(state: String): Color {
    val c = Kelo.colors
    return when (state) {
        "running" -> c.warn
        "done" -> c.success
        "failed" -> c.danger
        else -> c.muted
    }
}

@Composable
fun itemStateText(state: String): String = string(FollowRules.itemStateRes(state))

@Composable
fun followPlanSummary(item: FollowItem): String {
    val parts = mutableListOf<String>()
    val tell = item.plan?.tell
    val lead = tell?.lead.orEmpty()
    val hasOneDayLead = lead.any { it == "-1d" || it == "-24h" }
    val hasLeadHour = lead.firstOrNull { it.endsWith("h") && it != "-24h" }

    if (hasOneDayLead) {
        val isFlightOrTrip = item.kind in setOf("flight", "trip")
        parts += if (isFlightOrTrip) string(Res.string.follow_summary_lead_flight)
                 else string(Res.string.follow_summary_lead_general)
    } else if (hasLeadHour != null) {
        val hours = hasLeadHour.trimStart('+', '-').trimEnd('h', 'H').toIntOrNull() ?: 3
        parts += string(Res.string.follow_summary_lead_hours, hours)
    }

    val allPreps = if (item.prep.isNotEmpty()) item.prep else item.plan?.prep.orEmpty()
    val checkInPrep = allPreps.firstOrNull { it.kind == "check_in" }
    if (checkInPrep != null) {
        parts += string(Res.string.follow_summary_prep_checkin)
    } else if (allPreps.isNotEmpty()) {
        val first = allPreps.first().what
        if (first.isNotBlank()) {
            parts += string(Res.string.follow_summary_prep_general, first)
        }
    }

    if (parts.isEmpty()) {
        if (item.checks.isNotEmpty() || tell?.onChange == true) {
            parts += string(Res.string.follow_summary_watch_changes)
        } else {
            parts += string(Res.string.follow_summary_default)
        }
    }

    return parts.joinToString(" · ")
}
