"""The designed mail preview: the card's message lines and preview link (tools/mail_tools.py), the preview document and its blocks
(mail_html.preview), the route that serves them (api/approvals.py) and the notification line. The preview is the sanitized HTML that
will be sent, in a document that can load and run nothing."""

import asyncio
import json
import re

import httpx
from assistant import i18n, mail_html
from assistant.mail import Mail
from assistant.mailboxes import Mailboxes
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.store import Store
from assistant.tools.mail_tools import mail_tools

from test_agent_profile import app_settings
from test_mail import FakeMailServer
from test_server import AUTH, Running, follow

def head_start():
    return "<!doctype html><html>" + mail_html.PREVIEW_HEAD + "<body>"
RICH = ('<table style="width:100%; background-color: #f5f5f5" cellpadding="8"><tr><td align="center">'
        '<h2 style="color: #123456">Sale</h2><p>Only <b>today</b> <a href="https://kelo.chat/x">link</a></p>'
        '<img src="https://t.example/p.gif" alt="Logo"></td></tr></table>'
        '<ol><li>one</li><li>two</li></ol><p style="text-align: right">Bye<br>now</p>')


def email_send_tool(tmp_path):
    fake = FakeMailServer()
    mail = Mail(Store(tmp_path / "m.db"), imap=fake.imap, smtp=fake.smtp)
    return next(t for t in mail_tools(Mailboxes(mail)) if t.name == "email_send")


def tools_of(tmp_path):
    fake = FakeMailServer()
    mail = Mail(Store(tmp_path / "m.db"), imap=fake.imap, smtp=fake.smtp)
    return {t.name: t for t in mail_tools(Mailboxes(mail))}


def lines_of(extra):
    return {line["label"]: line for line in extra["lines"]}


def rendered(html):
    """The preview's document and blocks, lower-cased, for the case-insensitive checks."""
    p = mail_html.preview(html)
    assert p is not None
    return p.html.lower(), json.dumps(p.blocks).lower()


def every_span(blocks):
    for b in blocks:
        yield from b.get("spans", [])
        for cell in b.get("cells", []):
            yield from cell["spans"]


async def test_a_designed_mail_card_has_the_message_lines_and_a_preview_link(tmp_path):
    tool = tools_of(tmp_path)["email_send"]
    extra = tool.approval_extra("ap1", {"to": ["kim@example.com"], "subject": "Hi", "body": "Hello Kim",
                                        "html": "<p><b>Hello</b> Kim</p>"})
    assert lines_of(extra)["Message"] == {"label": "Message", "value": "Hello Kim", "full": True}
    assert extra["mail"] == {"designed": True, "preview": "/api/approvals/ap1/mail-preview", "pictures": 0, "links": 0}
    assert "<p>" not in json.dumps(extra) and "<b>" not in json.dumps(extra)   # the HTML itself never goes into the event
    assert "notice_body" in extra


async def test_a_plain_mail_has_no_message_line_and_no_preview(tmp_path):
    tool = tools_of(tmp_path)["email_send"]
    extra = tool.approval_extra("ap2", {"to": ["kim@example.com"], "subject": "Hi", "body": "Hello",
                                        "cc": ["a@example.com"], "bcc": ["b@example.com"]})
    assert "mail" not in extra and "notice_body" not in extra
    assert [line["label"] for line in extra["lines"]] == ["To", "Also to", "Subject"]
    assert lines_of(extra)["Also to"]["value"] == "Cc: a@example.com · Bcc: b@example.com"
    many = tool.approval_extra("ap3", {"to": ["a@x.com"], "cc": ["b@x.com"], "bcc": ["c@x.com"], "subject": "s", "body": "b",
                                       "html": "<p>x</p>"})
    assert len(many["lines"]) <= 4


async def test_an_empty_message_with_a_designed_version_shows_the_words_of_the_designed_version(tmp_path):
    tool = tools_of(tmp_path)["email_send"]
    extra = tool.approval_extra("ap4", {"to": ["kim@example.com"], "subject": "Hi", "body": "", "html": "<p><b>Hello</b> Kim</p>"})
    assert lines_of(extra)["Message"]["value"] == mail_html.to_text(mail_html.clean("<p><b>Hello</b> Kim</p>")) == "Hello Kim"


def test_the_preview_body_is_exactly_the_body_that_is_sent():
    p = mail_html.preview(RICH)
    sent = mail_html.clean(RICH)
    inner = sent.removeprefix("<html><body>").removesuffix("</body></html>")
    assert p.html == head_start() + inner + "</body></html>"
    assert p.html.count("<img") == 0 and p.pictures == 1 and p.links == 2   # the picture as a link, and the anchor


def test_the_document_has_one_csp_that_is_ours_and_the_fixed_head():
    p = mail_html.preview(RICH)
    assert p.html.count("http-equiv") == 1
    found = re.search(r'http-equiv="Content-Security-Policy" content="([^"]*)"', p.html)
    assert found and found.group(1) == mail_html.PREVIEW_CSP
    assert p.html.startswith(head_start())


def test_nothing_that_is_sent_gives_no_preview_and_the_size_is_known():
    assert mail_html.preview("<script>only()</script>") is None
    assert len(mail_html.preview("<p>" + "가" * 50_000 + "</p>").html.encode()) <= mail_html.PREVIEW_MAX_BYTES


def test_the_blocks_keep_headings_items_rows_and_alignment():
    blocks = mail_html.preview(RICH).blocks
    kinds = [b["kind"] for b in blocks]
    assert "row" in kinds and "item" in kinds and "paragraph" in kinds
    row = next(b for b in blocks if b["kind"] == "row")
    assert row["cells"][0]["align"] == "center"
    assert [b["ordinal"] for b in blocks if b["kind"] == "item"] == [1, 2]
    assert all(set(b) >= {"kind", "level", "ordinal", "depth", "align", "bg"} for b in blocks)
    right = next(b for b in blocks if b["kind"] == "paragraph" and any(s["text"].startswith("Bye") for s in b["spans"]))
    assert right["align"] == "end" and "Bye\nnow" in "".join(s["text"] for s in right["spans"])
    sale = next(s for b in blocks for s in every_span([b]) if s["text"].startswith("Sale"))
    assert sale["b"] is True and sale["color"] == "#123456"
    link = next(s for s in every_span(blocks) if s["href"] == "https://kelo.chat/x")
    assert link["text"].strip() == "link" and link["u"] is True and link["b"] is False and link["color"] == ""


def test_a_script_or_a_style_block_or_an_image_never_reaches_the_document_or_the_blocks():
    cases = ["<p>x</p><script>alert(1)</script>", "<p>x</p><img src=x onerror=alert(1)>",
             '<p>x</p><img src="https://t.example/p.gif" onerror="x()">']
    for html in cases:
        doc, blocks = rendered(html)
        for bad in ("<script", "alert(1)", "onerror", "<img", "src="):
            assert bad not in doc and bad not in blocks, (html, bad)
    doc, blocks = rendered('<p>x</p><img src="https://t.example/p.gif" onerror="x()">')
    assert '<a href="https://t.example/p.gif">' in doc
    assert mail_html.preview('<p>x</p><img src="https://t.example/p.gif" onerror="x()">').pictures == 1


BAD_HREFS = ['<a href="javascript:alert(1)">bad</a>', '<a href="JaVaScRiPt:alert(1)">bad</a>',
             '<a href="jav&#x09;ascript:alert(1)">bad</a>', '<a href=" &#14; javascript:alert(1)">bad</a>',
             '<a href="data:text/html,<script>1</script>">bad</a>', '<a href="vbscript:x">bad</a>',
             '<a href="file:///etc/passwd">bad</a>']


def test_unsafe_links_lose_their_target_and_keep_their_words():
    for html in BAD_HREFS:
        doc, blocks = rendered("<p>x</p>" + html)
        for bad in ("javascript:", "data:", "vbscript:", "file:"):
            assert bad not in doc and bad not in blocks, (html, bad)
        assert "<a>bad</a>" in doc and '"bad"' in blocks


REMOTE_LOADS = [
    '<div style="background:url(https://x.example/y.png)">a</div>',
    '<div style="background-image: url(&quot;//x/y&quot;)">a</div>',
    '<div style="behavior:url(x.htc)">a</div>',
    '<div style="width: expression(alert(1))">a</div>',
    "<style>@import url(https://x/y.css)</style><p>a</p>",
    "<link rel=stylesheet href=https://x/y.css><p>a</p>",
    '<table background="https://x/bg.png"><tr><td background="https://x/c.png">a</td></tr></table>',
    '<img srcset="https://x/a.png 2x"><p>a</p>',
    "<video poster=https://x/p.png>a</video><p>a</p>",
    "<svg><image href=https://x/a.png/></svg><p>a</p>",
    '<base href="https://evil.example/"><p>a</p>',
    '<meta http-equiv="refresh" content="0;url=https://x"><p>a</p>',
    "<object data=https://x>a</object><p>a</p>",
    "<embed src=https://x><p>a</p>",
    "<iframe src=https://x></iframe><p>a</p>",
    "<form action=https://x><input name=a><button>Go</button></form><p>a</p>",
]


def test_nothing_is_loaded_from_outside_and_no_style_can_load_or_run():
    for html in REMOTE_LOADS:
        doc, blocks = rendered(html)
        for bad in ("url(", "@import", "expression", "behavior", "<link", "<base", '<meta http-equiv="refresh"', "<iframe",
                    "<object", "<embed", "<form", "<input", "<button", "background=", "srcset", "poster", "<svg", "<video"):
            assert bad not in doc and bad not in blocks, (html, bad)
        outside = re.sub(r'<a href="[^"]*">', "", doc)
        assert "https://x" not in outside and '"https://x' not in blocks, html


def test_every_link_in_the_document_and_the_blocks_is_web_or_mail():
    p = mail_html.preview("<p>" + "</p><p>".join(BAD_HREFS + REMOTE_LOADS) + '<a href="https://ok.example/">ok</a>'
                          '<a href="mailto:kim@example.com">mail</a>')
    hrefs = re.findall(r'href="([^"]*)"', p.html) + [s["href"] for s in every_span(p.blocks)]
    assert hrefs and all(h.startswith(("http://", "https://", "mailto:")) for h in hrefs if h)


async def wait_for_pending(c):
    for _ in range(200):
        items = (await c.get("/api/approvals/pending")).json()["items"]
        if items:
            return items[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no approval is waiting")


def sends(args, *, tmp_path):
    script = iter([[ToolCall("c1", "email_send", json.dumps(args)), Done("tool_calls")], [TextDelta("보냈어요."), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev

    return create_app(app_settings(tmp_path), tools=[email_send_tool(tmp_path)], stream_fn=fn)


DESIGNED = {"to": ["kim@example.com"], "subject": "Hi", "body": "Hello Kim",
            "html": '<p><b>Hello</b> <i>Kim</i></p><img src="https://t.example/p.gif" alt="Logo">'}


async def test_the_preview_route_answers_only_for_a_pending_designed_mail(tmp_path):
    plain = {"to": ["kim@example.com"], "subject": "Hi", "body": "Plain only"}
    async with Running(sends(DESIGNED, tmp_path=tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c, \
            httpx.AsyncClient(base_url=srv.url) as anonymous:
        assert (await anonymous.get("/api/approvals/ap1/mail-preview")).status_code == 401
        assert (await c.get("/api/approvals/nope/mail-preview")).status_code == 404
        await c.post("/api/turns", json={"text": "메일 보내줘"})
        item = await wait_for_pending(c)
        assert "_mail_preview" not in item and "arguments" not in item and "<b>" not in json.dumps(item)
        r = await c.get(f"/api/approvals/{item['approval_id']}/mail-preview")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store" and r.headers["content-security-policy"] == mail_html.PREVIEW_CSP
        body = r.json()
        assert set(body) >= {"html", "blocks", "cut", "pictures", "links", "bytes"} and body["pictures"] == 1
        assert body["html"].startswith(head_start()) and "<b>Hello</b>" in body["html"]
        listed = (await c.get("/api/approvals/pending")).text
        assert "_mail_preview" not in listed and listed.count("<b>") == 0


async def test_the_preview_route_refuses_plain_mails_and_other_cards(tmp_path):
    async with Running(sends({"to": ["kim@example.com"], "subject": "Hi", "body": "Plain only"}, tmp_path=tmp_path)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/turns", json={"text": "메일 보내줘"})
        item = await wait_for_pending(c)
        assert (await c.get(f"/api/approvals/{item['approval_id']}/mail-preview")).status_code == 404


async def test_an_unattended_designed_mail_notifies_with_its_words_and_the_app_line(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    script = iter([[ToolCall("c1", "email_send", json.dumps(DESIGNED)), Done("tool_calls")], [TextDelta("다음에 볼게요."), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev

    async with Running(create_app(app_settings(tmp_path), tools=[email_send_tool(tmp_path)], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "메일", "prompt": "메일 보내줘", "cron": "0 9 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        notes = []
        for _ in range(100):
            notes = (await c.get("/api/notifications")).json()["items"]
            if any(n["type"] == "approval_waiting" for n in notes):
                break
            await asyncio.sleep(0.02)
        waiting = next(n for n in notes if n["type"] == "approval_waiting")
        assert "Hello Kim" in waiting["body"] and "You can see the design in the Kelo app." in waiting["body"]
        pend = (await c.get("/api/approvals/pending")).json()["items"]
        assert pend and "notice_body" not in pend[0]
        await follow(c, run["turn_id"])


def test_the_new_texts_are_in_every_locale_file():
    for lang in ("ko", "de", "ja", "pt-BR", "zh-Hant"):
        for text in ("Also to", "You can see the design in the Kelo app.", "The preview is too large to show."):
            assert text in i18n.catalog(lang), (lang, text)
