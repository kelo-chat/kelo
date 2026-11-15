package chat.kelo.app.goals

import chat.kelo.app.core.Goal


// From android/kelo .../goals/GoalsViewModel.kt (that file is an Android view model). Keep in step with it.

/**
 * [removing]: goals swiped away whose deletion can still be undone; they are hidden here and deleted on the server only when the
 * undo offer is gone.
 */
data class GoalsState(
    val goals: List<Goal> = emptyList(),
    val loaded: Boolean = false,
    val failed: Boolean = false,
    /** The last refresh did not reach the server: the goals shown are the ones kept from before */
    val offline: Boolean = false,
    val removing: Set<Long> = emptySet(),
    /** Of [removing]: the ones whose linked schedules the user chose to turn off with them */
    val pauseSchedules: Set<Long> = emptySet(),
    /** Goals Kelo found in the conversations, offered at the top of the list */
    val suggestions: List<GoalSuggestion> = emptyList(),
    /** Suggestions being added or waved off right now (hidden at once) */
    val settling: Set<Long> = emptySet(),
) {
    val shownSuggestions: List<GoalSuggestion> get() = suggestions.filter { it.id !in settling }
    /** In progress, the nearest due date first */
    val tracking: List<Goal> get() = GoalsRules.ordered(goals.filter { !it.isDone && it.id !in removing })
    val done: List<Goal> get() = goals.filter { it.isDone && it.id !in removing }
}

