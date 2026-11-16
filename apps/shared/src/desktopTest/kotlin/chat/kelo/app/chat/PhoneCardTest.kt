package chat.kelo.app.chat

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The phone card: a `device` turn event of the Android device is the browser card's sibling (one per reply, updated in place). */
class PhoneCardTest {
    private fun parse(json: String) = TurnEvent.parse(Json.parseToJsonElement(json))
    private var n = 0
    private val key = { "k${++n}" }
    private fun run(vararg evs: TurnEvent, start: List<ChatItem> = emptyList()) = evs.fold(start) { items, ev -> ChatReducer.apply(items, ev, key) }
    private val shot = "data:image/jpeg;base64,AQ=="

    @Test fun aDeviceEventBecomesAPhoneCardForTheKeyAndroid() {
        val ev = parse("""{"type":"device","id":"c1","key":"android","status":"휴대폰에서 누르는 중","image":"$shot","width":360,"height":780,"bytes":3,"at":1}""")
        ev as TurnEvent.Device
        assertEquals("android", ev.key); assertEquals("휴대폰에서 누르는 중", ev.status); assertEquals(shot, ev.image)
        val card = run(ev).single() as ChatItem.Browser
        assertTrue(card.device); assertEquals("android", card.tab); assertEquals("휴대폰에서 누르는 중", card.status)
        assertEquals(360, card.width); assertEquals(780, card.height)
        // "보기" opens the live view with the key android, the card's picture standing in when the device is away
        val target = chat.kelo.app.notify.OpenTarget.Tab.ofCard(card) as chat.kelo.app.notify.OpenTarget.Tab
        assertEquals("android", target.id); assertEquals(shot, target.picture)
    }

    @Test fun aPictureThatIsNotADataUrlIsLeftOutAndTheKeyDefaultsToAndroid() {
        val ev = parse("""{"type":"device","id":"c1","status":"s","image":"https://x/y.jpg"}""") as TurnEvent.Device
        assertNull(ev.image); assertEquals("android", ev.key)
    }

    @Test fun everyStepUpdatesTheOneCardInPlaceAndKeepsTheLastPicture() {
        val items = run(
            TurnEvent.Device("c1", "android", "휴대폰 화면 보는 중", shot, 360, 780),
            TurnEvent.Text("잠시만요"),
            TurnEvent.Device("c2", "android", "휴대폰에서 누르는 중", null),
        )
        val cards = items.filterIsInstance<ChatItem.Browser>()
        assertEquals(1, cards.size)
        assertEquals("k1", cards[0].key)
        assertEquals("휴대폰에서 누르는 중", cards[0].status)
        assertEquals(shot, cards[0].image); assertEquals(780, cards[0].height)
        // the next reply starts its own card
        val again = run(TurnEvent.Device("c3", "android", "s", null), start = items + ChatItem.Msg(key(), mine = true, text = "또"))
        assertEquals(2, again.filterIsInstance<ChatItem.Browser>().size)
    }

    @Test fun aPhoneCardAndABrowserCardDoNotMerge() {
        val items = run(
            TurnEvent.Browser("b1", "A", "u", "a.example", "사이트 보는 중", null, tab = "owner"),
            TurnEvent.Device("c1", "android", "휴대폰 화면 보는 중", null),
            TurnEvent.Browser("b2", "A", "u", "a.example", "페이지 누르는 중", null),   // an older server: no tab
            TurnEvent.Device("c2", "android", "휴대폰에서 누르는 중", null),
        )
        val cards = items.filterIsInstance<ChatItem.Browser>()
        assertEquals(2, cards.size)
        assertEquals(listOf(false, true), cards.map { it.device })
        assertEquals("페이지 누르는 중", cards[0].status); assertEquals("휴대폰에서 누르는 중", cards[1].status)
    }

    @Test fun phoneStepsShowAsTheCardNotAsChips() {
        fun started(id: String, name: String) = TurnEvent.ToolStarted(id, name, JsonObject(emptyMap()), "")
        fun ok(id: String, name: String) = TurnEvent.ToolResult(id, name, "ok", false, null)
        val items = run(
            started("t1", "android_look"), TurnEvent.Device("t1", "android", "휴대폰 화면 보는 중", null), ok("t1", "android_look"),
            started("t2", "android_tap"), TurnEvent.Device("t2", "android", "휴대폰에서 누르는 중", null), ok("t2", "android_tap"),
        )
        val rows = ChatReducer.foldTools(items)
        assertEquals(1, rows.size)
        assertTrue((rows.single() as ChatItem.Browser).device)
        // without a card (an older server) the steps stay visible
        val bare = ChatReducer.foldTools(run(started("x1", "android_look"), ok("x1", "android_look")))
        assertTrue(bare.single() is ChatItem.Tool)
    }

    @Test fun theStatusLineAndTheMoodFollowThePhoneLikeTheBrowser() {
        val ev = TurnEvent.Device("c1", "android", "휴대폰에 입력하는 중", null)
        assertEquals("휴대폰에 입력하는 중", AgentActivityRules.next(null, ev))
        assertEquals("keep", AgentActivityRules.next("keep", TurnEvent.Device("c2", "android", "", null)))
        assertEquals(TurnKind.Browser, AgentActivityRules.turnKind(TurnKind.Thinking, ev))
        assertEquals(TurnKind.Browser, AgentActivityRules.toolKind("android_tap"))
    }

    @Test fun thePhonePictureIsPortraitWithTheDevicesProportions() {
        assertEquals(360f / 780f, PhoneShape.aspect(360, 780), 0.001f)
        assertEquals(0.4f, PhoneShape.aspect(100, 1000), 0.001f)     // never thinner than a tall phone
        assertEquals(0.8f, PhoneShape.aspect(1000, 1000), 0.001f)
        assertEquals(9f / 19.5f, PhoneShape.aspect(0, 0), 0.001f)    // size unknown
        assertFalse(PhoneShape.aspect(720, 1560) > 1f)
    }
}
