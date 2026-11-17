package chat.kelo.app.agent

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import chat.kelo.app.LocalAppServices
import chat.kelo.app.chat.ApprovalItems

/** The everyday category in the user's words ("Memory", "Notes"…); a category this app does not know reads as it came. */
@Composable
fun forgetCategoryWord(category: String): String = string(
    when (category) {
        "memory" -> Res.string.forget_cat_memory
        "notes" -> Res.string.forget_cat_notes
        "reminders" -> Res.string.forget_cat_reminders
        "skills" -> Res.string.forget_cat_skills
        "history" -> Res.string.forget_cat_history
        else -> Res.string.forget_cat_other
    },
)

/**
 * Under the forget card (tool `forget_confirm`): "See the list (N)", each place as its category and a preview of at most 60
 * characters. Shown only when the server still has the plan; anything else leaves the card as it is.
 */
@Composable
fun ForgetPlanList(planId: String) {
    val sessions = LocalAppServices.current.sessions
    val plan by produceState<ForgetPlanView?>(null, planId) {
        value = runCatching { ForgetCalls.plan(sessions.requireApi(), planId) }.getOrNull()
    }
    val view = plan ?: return
    val words = ForgetRules.CATEGORIES.associateWith { forgetCategoryWord(it) }
    val lines = ForgetRules.lines(view) { words[it] ?: it }
    if (lines.isNotEmpty()) ApprovalItems(lines)
}
