package chat.kelo.app.chat

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Picture cards on a choice question: how the server's "cards" are read and what a screen reader says (desktop runs it too). */
class ReplyCardsTest {
    private val json = Json { ignoreUnknownKeys = true }
    private fun parse(cards: String?) = json.decodeFromString<ReplySet>(
        """{"questions":[{"question":"Which one?","options":["Blue mug","Green mug"]${cards?.let { ""","cards":$it""" } ?: ""}}]}""").questions.single()

    @Test fun cardsAreReadWithPictureDetailAndMark() {
        val q = parse("""[{"image":{"image_id":"img_ab12","url":"/api/chat-images/img_ab12","width":480,"height":320},"detail":"9,900원 · 화","recommended":true},{"detail":"8,500원 · 수"}]""")
        val cards = q.cardList!!
        assertEquals(2, cards.size)
        assertEquals("/api/chat-images/img_ab12", cards[0].image?.url)
        assertEquals(480, cards[0].image?.width)
        assertTrue(cards[0].recommended)
        assertNull(cards[1].image)
        assertEquals("8,500원 · 수", cards[1].detail)
    }

    @Test fun withoutCardsOrWithAMismatchTheChoiceStaysPlain() {
        assertNull(parse(null).cardList)
        assertNull(parse("""[{"detail":"only one"}]""").cardList)
        assertNull(parse("""[{"detail":"a"},{"detail":"b"},{"detail":"c"}]""").cardList)
    }

    @Test fun emptyCardsShowNothing() {
        assertNull(parse("""[{},{}]""").cardList)
        // one empty card among others keeps the list, in order
        assertEquals(2, parse("""[{},{"detail":"x"}]""").cardList?.size)
    }

    @Test fun theSpokenLabelIsOptionDetailRecommended() {
        val c = ReplyCard(detail = "14,800원 · 내일 새벽 도착", recommended = true)
        assertEquals("파란 머그, 14,800원 · 내일 새벽 도착, 추천", ReplyCardRules.label("파란 머그", c, "추천"))
        assertEquals("Green mug", ReplyCardRules.label("Green mug", ReplyCard(), "Recommended"))
        assertEquals("Mug, 5", ReplyCardRules.label("Mug", ReplyCard(detail = "5"), "Recommended"))
    }
}
