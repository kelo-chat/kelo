package chat.kelo.app.goals

import chat.kelo.app.core.Goal
import org.junit.Assert.assertEquals
import org.junit.Test

class GoalsStateTest {
    private val goals = listOf(
        Goal(1, "운동"), Goal(2, "독서", status = Goal.STATUS_DONE), Goal(3, "물 마시기"),
    )

    @Test fun swipedGoalsAreHiddenUntilUndone() {
        val s = GoalsState(goals, loaded = true, removing = setOf(3L, 2L))
        assertEquals(listOf(1L), s.tracking.map { it.id })
        assertEquals(emptyList<Long>(), s.done.map { it.id })
        val undone = s.copy(removing = s.removing - 2L)
        assertEquals(listOf(2L), undone.done.map { it.id })
    }

    @Test fun childrenSitUnderTheirParentAndOrphansStayAtTheTop() {
        val goals = listOf(Goal(1, "Fitness"), Goal(2, "Run", parentId = 1), Goal(3, "Read"), Goal(4, "Stretch", parentId = 9))
        val groups = GoalsRules.grouped(goals)
        assertEquals(listOf(1L, 3L, 4L), groups.map { it.goal.id })
        assertEquals(listOf(2L), groups[0].children.map { it.id })
        assertEquals(emptyList<Long>(), groups[1].children.map { it.id })
    }

    @Test fun workItemsAreListedApartFromGoals() {
        val goals = listOf(Goal(1, "Fitness"), Goal(5, "Parcel", kind = "work"), Goal(6, "Booking", kind = "work", status = Goal.STATUS_DONE))
        assertEquals(listOf(5L), GoalsRules.work(goals).map { it.id })
        assertEquals(listOf(1L), GoalsRules.grouped(goals).map { it.goal.id })
        assertEquals(listOf(1L), GoalsState(goals, loaded = true).tracking.map { it.id })
    }

}
