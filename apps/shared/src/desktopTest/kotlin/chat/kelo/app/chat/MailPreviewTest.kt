package chat.kelo.app.chat

import chat.kelo.app.agent.PendingApproval
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonElement
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The designed mail preview in the apps: the card's `mail` part, the link rules, the preview the server sends (parsed), and its store. */
class MailPreviewTest {
    private lateinit var server: MockWebServer

    /** A real preview() answer from the server (server/tests/test_mail_preview.py builds the same kind of sample). */
    private val serverSample = """{"html": "<!doctype html><html><head><meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; media-src 'none'; frame-src 'none'; child-src 'none'; object-src 'none'; script-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><meta name=\"referrer\" content=\"no-referrer\"><style>html,body{margin:0;padding:0;background:#fff;color:#111}body{padding:12px;font-family:-apple-system,system-ui,Roboto,\"Segoe UI\",sans-serif;font-size:15px;line-height:1.45;overflow-wrap:anywhere;word-break:keep-all}table{max-width:100%!important;border-collapse:collapse}td,th{overflow-wrap:anywhere}a{color:#0b57d0;text-decoration:underline}</style></head><body><h1 style=\"color: #123456\">Sale</h1><ol><li>one</li><li>two</li></ol><table><tr><td align=\"center\" bgcolor=\"#f5f5f5\">a<b>b</b> <a href=\"https://kelo.chat/x\">link</a></td><td>c</td></tr></table><p style=\"text-align: right; background-color: #eee\">Bye<br>now</p><hr><blockquote>q</blockquote></body></html>", "blocks": [{"kind": "heading", "level": 1, "ordinal": 0, "depth": 0, "align": "start", "bg": "", "spans": [{"text": "Sale", "b": true, "i": false, "u": false, "s": false, "color": "#123456", "href": ""}]}, {"kind": "item", "level": 0, "ordinal": 1, "depth": 0, "align": "start", "bg": "", "spans": [{"text": "one", "b": false, "i": false, "u": false, "s": false, "color": "", "href": ""}]}, {"kind": "item", "level": 0, "ordinal": 2, "depth": 0, "align": "start", "bg": "", "spans": [{"text": "two", "b": false, "i": false, "u": false, "s": false, "color": "", "href": ""}]}, {"kind": "row", "level": 0, "ordinal": 0, "depth": 0, "align": "start", "bg": "", "cells": [{"spans": [{"text": "a", "b": false, "i": false, "u": false, "s": false, "color": "", "href": ""}, {"text": "b", "b": true, "i": false, "u": false, "s": false, "color": "", "href": ""}, {"text": " ", "b": false, "i": false, "u": false, "s": false, "color": "", "href": ""}, {"text": "link", "b": false, "i": false, "u": true, "s": false, "color": "", "href": "https://kelo.chat/x"}], "bg": "#f5f5f5", "align": "center"}, {"spans": [{"text": "c", "b": false, "i": false, "u": false, "s": false, "color": "", "href": ""}], "bg": "", "align": "start"}]}, {"kind": "paragraph", "level": 0, "ordinal": 0, "depth": 0, "align": "end", "bg": "#eeeeee", "spans": [{"text": "Bye\nnow", "b": false, "i": false, "u": false, "s": false, "color": "", "href": ""}]}, {"kind": "rule", "level": 0, "ordinal": 0, "depth": 0, "align": "start", "bg": ""}, {"kind": "quote", "level": 0, "ordinal": 0, "depth": 0, "align": "start", "bg": "", "spans": [{"text": "q", "b": false, "i": false, "u": false, "s": false, "color": "", "href": ""}]}], "cut": false, "pictures": 0, "links": 1, "bytes": 1115}"""

    @Before fun start() { server = MockWebServer() }
    @After fun stop() { server.shutdown() }

    private fun approval(extra: String = "", args: String = "{}") = Json.parseToJsonElement(
        """{"type":"approval_request","approval_id":"ap1","name":"email_send","arguments":$args,"detail":"To: kim@example.com"$extra}""",
    )

    @Test fun parsesTheMailPartOfAnApprovalRequest() {
        val ev = TurnEvent.parse(approval(""",
              "mail": {"designed": true, "preview": "/api/approvals/ap1/mail-preview", "pictures": 1, "links": 2}""")) as TurnEvent.ApprovalRequest
        assertEquals(MailDesign("/api/approvals/ap1/mail-preview", 1, 2), ev.mail)
    }

    @Test fun mailDesignRejectsAnythingThatIsNotAServerPreviewPath() {
        assertNull(MailDesign.parse(Json.parseToJsonElement("""{"designed": false, "preview": "/api/approvals/a/mail-preview"}""")))
        assertNull(MailDesign.parse(Json.parseToJsonElement("""{"designed": true}""")))
        assertNull(MailDesign.parse(Json.parseToJsonElement("""{"designed": true, "preview": "https://x/"}""")))
        assertNull(MailDesign.parse(null))
        assertEquals(MailDesign("/api/approvals/a/mail-preview", 0, 0),
            MailDesign.parse(Json.parseToJsonElement("""{"designed": true, "preview": "/api/approvals/a/mail-preview"}""")))
    }

    @Test fun anApprovalWithHtmlArgumentsButNoMailPartHasNoPreview() {
        val ev = TurnEvent.parse(approval(args = """{"to":["kim@example.com"],"html":"<p>raw</p>"}""")) as TurnEvent.ApprovalRequest
        assertNull(ev.mail)
        assertEquals("<p>raw</p>", (ev.arguments["html"] as JsonPrimitive).content)   // the raw arguments stay in the event, never rendered
    }

    @Test fun thePendingListCarriesTheMailPartToo() {
        val item = Json.parseToJsonElement("""{"approval_id":"ap1","type":"approval_request","name":"email_send","arguments":{},
            "mail":{"designed":true,"preview":"/api/approvals/ap1/mail-preview","pictures":0,"links":0},"asked_at":1.0}""") as JsonObject
        assertEquals("/api/approvals/ap1/mail-preview", PendingApproval.parse(item)?.event?.mail?.previewPath)
    }

    @Test fun linksOpenOnlyForWebAndMailAddresses() {
        for (ok in listOf("https://a.example/x", "http://a", "mailto:kim@example.com")) assertEquals(ok, MailPreviewRules.linkToOpen(ok))
        for (bad in listOf("javascript:alert(1)", "JAVASCRIPT:x", " javascript:x", "data:text/html,x", "file:///x",
            "intent://x#Intent;end", "about:blank", "content://x", "kelo://x", "", "https:", "jav\tascript:x")) {
            assertNull(bad, MailPreviewRules.linkToOpen(bad))
        }
        assertNull(MailPreviewRules.linkToOpen(null))
    }

    @Test fun theConfirmShowsTheHostOrTheAddress() {
        assertEquals("a.example", MailPreviewRules.linkLabel("https://a.example/x?y=1"))
        assertEquals("kim@example.com", MailPreviewRules.linkLabel("mailto:kim@example.com"))
    }

    @Test fun theServersPreviewParsesIntoBlocks() {
        val p = MailPreviewRules.parse(Json.parseToJsonElement(serverSample))
        assertNotNull(p)
        p!!
        assertEquals(0, p.pictures)   // the sample has no picture
        assertEquals(1, p.links)      // one link in the sample: https://kelo.chat/x
        assertTrue(!p.cut)
        val heading = p.blocks[0]
        assertEquals(MailBlock.Kind.HEADING, heading.kind)
        assertEquals(1, heading.level)
        assertEquals(0xFF123456L, heading.spans[0].color)
        assertEquals(listOf(1, 2), p.blocks.filter { it.kind == MailBlock.Kind.ITEM }.map { it.ordinal })
        val row = p.blocks.first { it.kind == MailBlock.Kind.ROW }
        assertEquals(2, row.cells.size)
        assertEquals(MailAlign.CENTER, row.cells[0].align)
        assertEquals(0xFFF5F5F5L, row.cells[0].bg)
        assertEquals("https://kelo.chat/x", row.cells[0].spans.first { it.href.isNotEmpty() }.href)
        val bye = p.blocks.first { it.kind == MailBlock.Kind.PARAGRAPH }
        assertEquals(MailAlign.END, bye.align)
        assertEquals(0xFFEEEEEEL, bye.bg)
        assertTrue(p.blocks.any { it.kind == MailBlock.Kind.RULE })
        assertTrue(p.blocks.any { it.kind == MailBlock.Kind.QUOTE })
    }

    @Test fun aCraftedJavascriptLinkIsDroppedAndAnUnknownKindToo() {
        val p = MailPreviewRules.parse(Json.parseToJsonElement("""
            {"html":"<p>x</p>","cut":false,"pictures":0,"links":0,"bytes":1,"blocks":[
              {"kind":"paragraph","spans":[{"text":"bad","href":"javascript:alert(1)","color":"#ff0000"}]},
              {"kind":"video","spans":[{"text":"never"}]},
              {"kind":"paragraph","spans":[{"text":"ok","href":"mailto:kim@example.com","color":"red"}]}]}"""))
        assertNotNull(p)
        assertEquals(2, p!!.blocks.size)
        assertEquals("", p.blocks[0].spans[0].href)
        assertEquals(0xFFFF0000L, p.blocks[0].spans[0].color)
        assertEquals("mailto:kim@example.com", p.blocks[1].spans[0].href)
        assertNull("named colours are not used", p.blocks[1].spans[0].color)
    }

    @Test fun theStoreFetchesOnceKeepsTheAnswerAndRetriesAfterAFailure() = runTest {
        server.enqueue(MockResponse().setBody(serverSample))
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"detail":"boom"}"""))
        server.enqueue(MockResponse().setBody(serverSample))
        val api = KeloApi(server.url("/").toString(), "t")
        val store = MailPreviewStore { api }
        store.load("ap1", "/api/approvals/ap1/mail-preview")
        store.load("ap1", "/api/approvals/ap1/mail-preview")   // already ready: no second call
        assertTrue(store.state("ap1").value is MailPreviewStore.State.Ready)
        assertEquals(1, server.requestCount)
        store.load("ap2", "/api/approvals/ap2/mail-preview")
        assertEquals(MailPreviewStore.State.Failed, store.state("ap2").value)
        store.load("ap2", "/api/approvals/ap2/mail-preview")   // a failure may be retried
        assertTrue(store.state("ap2").value is MailPreviewStore.State.Ready)
        assertEquals(3, server.requestCount)
    }

    @Test fun aGoneOrTooBigPreviewIsSaidSo() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"no such pending mail"}"""))
        server.enqueue(MockResponse().setResponseCode(413).setBody("""{"detail":{"code":"too_big","message":"big"}}"""))
        val api = KeloApi(server.url("/").toString(), "t")
        val store = MailPreviewStore { api }
        store.load("gone", "/api/approvals/gone/mail-preview")
        store.load("big", "/api/approvals/big/mail-preview")
        assertEquals(MailPreviewStore.State.Gone, store.state("gone").value)
        assertEquals(MailPreviewStore.State.TooBig, store.state("big").value)
    }

    @Test fun theStoreKeepsEightPreviews() = runTest {
        repeat(10) { server.enqueue(MockResponse().setBody(serverSample)) }   // 9 loads, then the evicted one again
        val api = KeloApi(server.url("/").toString(), "t")
        val store = MailPreviewStore { api }
        for (i in 0 until 9) store.load("ap$i", "/api/approvals/ap$i/mail-preview")
        assertEquals(9, server.requestCount)
        store.load("ap8", "/api/approvals/ap8/mail-preview")   // the newest is still kept
        assertEquals(9, server.requestCount)
        store.load("ap0", "/api/approvals/ap0/mail-preview")   // the oldest was dropped: it is fetched again
        assertEquals(10, server.requestCount)
    }
}
