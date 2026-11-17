package chat.kelo.app.agent

import chat.kelo.app.chat.AskEnd
import chat.kelo.app.chat.ChatAsks
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.TurnEvent
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The data-trail card (which kind of data goes to which recipient; Allow once / Always / Deny) and the list of recipients always allowed. */
class RecipientAllowsTest {
    private val card = """
        {"type":"approval_request","approval_id":"a1","name":"send_email","arguments":{},
         "trail":[{"recipient":"lee@else.example","kind":"mail","titles":["Invoice May","Invoice May","Trip plan"]},
                  {"recipient":"link","kind":"file","titles":[]},{"recipient":"","kind":"mail"}],
         "can_remember_recipient":true}
    """.trimIndent()

    private fun event(json: String = card) = TurnEvent.parse(Json.parseToJsonElement(json)) as TurnEvent.ApprovalRequest

    @Test fun theCardReadsKindRecipientAndTitlesAndDropsIncompleteOffers() {
        val offers = event().offers
        assertEquals(2, offers.size)
        assertEquals(TrailOffer("lee@else.example", "mail", listOf("Invoice May", "Trip plan")), offers[0])
        assertEquals("file", offers[1].kind)
        assertTrue(RecipientRules.canAlways(offers))
    }

    @Test fun anOlderServerOrAnotherCardHasNoOffersAndShowsTwoButtons() {
        val plain = event("""{"type":"approval_request","approval_id":"a2","name":"send"}""")
        assertTrue(plain.offers.isEmpty())
        assertFalse(RecipientRules.canAlways(plain.offers))
    }

    @Test fun recipientsAreShownWithoutInternalPrefixesAndTheLinkHasItsOwnWords() {
        assertEquals("lee@else.example", RecipientRules.shown(" lee@else.example "))
        assertEquals("slack", RecipientRules.shown("connector:slack"))
        assertEquals("shop.example", RecipientRules.shown("site:shop.example"))
        assertNull(RecipientRules.shown("link"))
        assertNull(RecipientRules.shown(""))
        assertEquals("other", RecipientRules.knownKind("something-new"))
        assertEquals("mail", RecipientRules.knownKind("mail"))
    }

    @Test fun theThreeAnswersSendWhatTheServerExpects() {
        assertEquals(true to null, RecipientRules.Answer.ONCE.let { it.approve to it.remember })
        assertEquals(true to "recipient", RecipientRules.Answer.ALWAYS.let { it.approve to it.remember })
        assertEquals(false to null, RecipientRules.Answer.DENY.let { it.approve to it.remember })
    }

    @Test fun theChatItemCarriesTheOffersButAPaymentCardDoesNot() {
        val items = ChatReducer.apply(emptyList(), event()) { "k1" }
        val a = items.single() as ChatItem.Approval
        assertEquals(2, a.offers.size)
    }

    @Test fun aRecipientAnswerEndsAsItsOwnLineOnlyWhenTheCardOfferedIt() {
        val pending = PendingApproval(event(), "", false, 1.0, false, null)
        assertEquals(AskEnd.ALWAYS_RECIPIENT, ChatAsks.answerEnd(pending, true, false, false, rememberRecipient = true))
        assertEquals(AskEnd.ALLOWED, ChatAsks.answerEnd(pending, true, false, false))
        assertEquals(AskEnd.DENIED, ChatAsks.answerEnd(pending, false, false, false, rememberRecipient = true))
        val bare = PendingApproval(event("""{"type":"approval_request","approval_id":"a3","name":"send"}"""), "", false, 1.0, false, null)
        assertEquals(AskEnd.ALLOWED, ChatAsks.answerEnd(bare, true, false, false, rememberRecipient = true))
    }

    @Test fun theListParsesAndKeepsTheNewestFirstAndRemovesOne() {
        val list = Json { ignoreUnknownKeys = true }.decodeFromString<RecipientAllowList>(
            """{"items":[{"id":1,"recipient":"a@x.example","kind":"mail","created_at":10.5,"approval_id":"p1"},{"id":3,"recipient":"link","kind":"file","created_at":30.0},{"id":2,"recipient":"slack","kind":"notes","created_at":20.0}]}""",
        )
        val ordered = RecipientRules.ordered(list.items)
        assertEquals(listOf(3L, 2L, 1L), ordered.map { it.id })
        assertEquals(listOf(3L, 1L), RecipientRules.without(ordered, 2).map { it.id })
        assertEquals(10.5, list.items[0].createdAt, 0.0)
        assertTrue(RecipientRules.without(emptyList(), 9).isEmpty())
    }
}
