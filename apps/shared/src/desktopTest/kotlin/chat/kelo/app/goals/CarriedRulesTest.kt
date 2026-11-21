package chat.kelo.app.goals

import chat.kelo.app.core.KeloApi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CarriedRulesTest {
    private val sample = """
        {"items":[
          {"key":"follow:cal:x","title":"Pick up the parcel","state":"open","starts_at":1800000000,"all_day":false,"work":null,
           "follow":[{"key":"cal:x","kind":"appointment","title":"Pick up the parcel","starts_at":1800000000,"state":"planned"}]},
          {"key":"work:7","title":"Trip to Busan","state":"open","starts_at":1795000000,"all_day":true,
           "work":{"id":7,"title":"Trip to Busan","category":"","note":"","status":"tracking","kind":"work"},
           "follow":[{"key":"mail:1","kind":"flight","title":"KE123","starts_at":1795000000,"state":"watching"}]},
          {"key":"work:9","title":"Renew the passport","state":"open","starts_at":null,"work":{"id":9,"title":"Renew the passport","status":"tracking","kind":"work"},"follow":[]},
          {"key":"work:3","title":"Old errand","state":"done","starts_at":1700000000,"work":{"id":3,"title":"Old errand","status":"done","kind":"work"},"follow":[]}
        ]}
    """.trimIndent()

    private fun items() = KeloApi.json.decodeFromString<CarriedList>(sample).items

    @Test fun readsTheOneListWithItsFollowItemsUnderTheWorkItem() {
        val all = items()
        assertEquals(4, all.size)
        assertTrue(CarriedRules.isWork(all[1]))
        assertFalse(CarriedRules.isWork(all[0]))
        assertEquals(listOf("mail:1"), CarriedRules.linked(all[1]).map { it.key })
        assertEquals(listOf("cal:x"), CarriedRules.linked(all[0]).map { it.key })
        assertEquals("Trip to Busan", all[1].work?.title)
    }

    @Test fun openRowsComeFirstNearestDateFirstThenUndatedThenDone() {
        val order = CarriedRules.ordered(items()).map { it.key }
        assertEquals(listOf("work:7", "follow:cal:x", "work:9", "work:3"), order)
    }

    @Test fun eachThingShowsOnce() {
        val keys = CarriedRules.ordered(items()).map { it.key }
        assertEquals(keys.size, keys.toSet().size)
        // the follow item of the trip is inside its work row, not a row of its own
        assertFalse(keys.contains("follow:mail:1"))
    }

    @Test fun theFollowItemsOfAWorkItemAreFoundByItsId() {
        val all = items()
        assertEquals(listOf("mail:1"), CarriedRules.followOf(all, 7).map { it.key })
        assertEquals(emptyList<String>(), CarriedRules.followOf(all, 9).map { it.key })
        assertEquals(emptyList<String>(), CarriedRules.followOf(all, 42).map { it.key })
    }
}
