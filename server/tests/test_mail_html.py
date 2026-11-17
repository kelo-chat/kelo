"""Designed (HTML) mail: the sanitizer, the multipart message over IMAP/SMTP, the Graph payload, the approval card line and the digest."""

import pytest

from assistant import i18n, mail_html
from assistant.approval_edit import editable
from assistant.effects import effect_of
from assistant.mail import Mail, MailError
from assistant.mailboxes import Mailboxes
from assistant.prepared_send import freeze
from assistant.store import Store
from assistant.tools.mail_tools import mail_tools

from test_mail import ADDRESS, PASSWORD, FakeMailServer
from test_microsoft_mail import outlook


def test_the_sanitizer_removes_code_frames_forms_and_event_handlers():
    dirty = ('<p onclick="steal()" style="color: red">Hi <b>there</b></p>'
             '<script>alert(1)</script><style>p{}</style><iframe src="https://x.test"></iframe>'
             '<object data="a"></object><embed src="b"><form action="/x"><input name="pw"><button>Go</button></form>'
             '<a href="javascript:alert(2)">bad</a><a href=" jav&#x09;ascript:alert(3)">bad2</a>'
             '<a href="https://kelo.chat/x" onmouseover="x()">ok</a><a href="mailto:a@b.org">mail</a>'
             '<img src="https://tracker.test/p.gif" alt="Logo"><blink>old</blink>')
    out = mail_html.clean(dirty)
    assert out.startswith("<html><body>") and out.endswith("</body></html>")
    for bad in ("<script", "<iframe", "<object", "<embed", "<form", "<input", "<button", "<style", "<img", "<blink", "onclick",
                "onmouseover", "javascript", "alert", "steal"):
        assert bad not in out.lower(), bad
    assert "<p style=\"color: red\">Hi <b>there</b></p>" in out           # allowed structure and inline CSS stay
    assert '<a href="https://kelo.chat/x">ok</a>' in out and '<a href="mailto:a@b.org">mail</a>' in out
    assert '<a href="https://tracker.test/p.gif">Logo</a>' in out          # a remote image is only a link, never fetched
    assert "tracker.test" not in out.replace('<a href="https://tracker.test/p.gif">Logo</a>', "")
    assert "old" in out                                                     # unknown tag: the tag goes, the words stay
    assert "<a>bad</a>" in out and "bad2" in out                            # bad links lose their target


def test_inline_css_that_loads_or_runs_is_dropped_but_plain_declarations_stay():
    out = mail_html.clean('<td style="background: url(http://t.test/x.png); color: #123456; width: expression(alert(1))">a</td>')
    assert "url(" not in out and "expression" not in out and "color: #123456" in out
    assert "expression" not in mail_html.clean('<p style="x: \\65 xpression(1)">a</p>')


def test_text_is_escaped_and_the_plain_version_keeps_the_words():
    out = mail_html.clean("<p>1 &lt; 2 &amp; &quot;q&quot; &lt;script&gt;</p>")
    assert "1 &lt; 2 &amp; \"q\" &lt;script&gt;" in out
    text = mail_html.to_text("<h1>Sale</h1><p>Only <b>today</b>.</p><table><tr><td>A</td><td>B</td></tr></table>"
                             "<script>evil()</script><style>p{}</style>")
    assert text == "Sale\nOnly today.\nA\tB" and "evil" not in text


def test_an_unclosed_tree_is_closed_and_nothing_is_left_open():
    out = mail_html.clean("<div><p>one<span>two")
    assert out == "<html><body><div><p>one<span>two</span></p></div></body></html>"
    assert mail_html.clean("<script>only code</script>") == ""


async def test_an_html_mail_goes_out_as_plain_text_plus_a_sanitized_html_part(tmp_path):
    fake = FakeMailServer()
    st = Store(tmp_path / "s.db")
    mail = Mail(st, imap=fake.imap, smtp=fake.smtp)
    await mail.connect("gmail", ADDRESS, PASSWORD)
    await mail.send("", ["kim@example.com"], "Newsletter", "Plain words.", html='<p style="color: #333">Designed <b>words</b></p>'
                    '<script>x()</script>')
    m = fake.sent[-1]
    assert m.get_content_type() == "multipart/alternative"
    parts = {p.get_content_type(): p.get_content() for p in m.iter_parts()}
    assert parts["text/plain"].strip() == "Plain words."
    assert '<p style="color: #333">Designed <b>words</b></p>' in parts["text/html"] and "script" not in parts["text/html"]


async def test_without_a_plain_body_the_html_text_is_used_and_a_plain_mail_is_unchanged(tmp_path):
    fake = FakeMailServer()
    st = Store(tmp_path / "s.db")
    mail = Mail(st, imap=fake.imap, smtp=fake.smtp)
    await mail.connect("gmail", ADDRESS, PASSWORD)
    await mail.send("", ["kim@example.com"], "A", "", html="<h2>Title</h2><p>Body text</p>")
    parts = {p.get_content_type(): p.get_content() for p in fake.sent[-1].iter_parts()}
    assert parts["text/plain"].strip() == "Title\nBody text"
    await mail.send("", ["kim@example.com"], "B", "just text")
    assert fake.sent[-1].get_content_type() == "text/plain"
    with pytest.raises(MailError):   # nothing survives the cleaning: refused before anything is signed in or sent
        await mail.send("", ["kim@example.com"], "C", "x", html="<script>only()</script>")
    assert len(fake.sent) == 2


async def test_the_mailboxes_pass_bcc_and_the_html_on_to_the_mailbox(tmp_path):
    fake = FakeMailServer()
    st = Store(tmp_path / "s.db")
    mail = Mail(st, imap=fake.imap, smtp=fake.smtp)
    await mail.connect("gmail", ADDRESS, PASSWORD)
    boxes = Mailboxes(mail)
    await boxes.send("", ["kim@example.com"], "Hi", "hello", bcc=["hidden@example.com"], html="<p>styled</p>")
    m = fake.sent[-1]
    assert m["Bcc"] == "hidden@example.com"
    assert m.get_content_type() == "multipart/alternative"


async def test_graph_sends_the_designed_version_as_html(tmp_path):
    _st, g, gm = outlook(tmp_path)
    await gm.send("", ["a@example.org"], "Hi", "Hello", html='<p>Designed</p><script>no()</script>')
    body = g.sent[-1]["message"]["body"]
    assert body["contentType"] == "HTML" and "<p>Designed</p>" in body["content"] and "no()" not in body["content"]
    await gm.send("", ["a@example.org"], "Plain", "Hello")
    assert g.sent[-1]["message"]["body"] == {"contentType": "Text", "content": "Hello"}


async def test_graph_passes_bcc_as_bcc_recipients(tmp_path):
    _st, g, gm = outlook(tmp_path)
    await gm.send("", ["a@example.org"], "Hi", "Hello", bcc=["hidden@example.org"])
    assert g.sent[-1]["message"]["bccRecipients"] == [{"emailAddress": {"address": "hidden@example.org"}}]


async def test_the_card_names_the_designed_version_and_the_digest_and_edit_follow_it(tmp_path):
    st = Store(tmp_path / "s.db")
    fake = FakeMailServer()
    mail = Mail(st, imap=fake.imap, smtp=fake.smtp)
    tools = {t.name: t for t in mail_tools(Mailboxes(mail))}
    plain = await tools["email_send"].describe({"to": ["kim@example.com"], "subject": "x"})
    styled = await tools["email_send"].describe({"to": ["kim@example.com"], "subject": "x", "html": "<p>a</p>"})
    assert styled == plain + " · Includes a designed (HTML) version"
    assert "html" in tools["email_send"].parameters["properties"]
    args = {"account": "", "to": ["kim@example.com"], "subject": "x", "body": "plain", "html": "<p>a</p>"}
    assert effect_of("email_send", args).text == "plain\n\n<p>a</p>"
    assert effect_of("email_send", {**args, "html": ""}).text == "plain"
    assert freeze("email_send", args).digest != freeze("email_send", {**args, "html": "<p>b</p>"}).digest
    assert freeze("email_send", {**args, "html": ""}).digest == freeze("email_send", {**args, "html": ""}).digest
    assert editable("email_send", args) is None and editable("email_send", {**args, "html": ""}) is not None
    tok = i18n.use("ko")
    try:
        assert (await tools["email_send"].describe({"to": ["kim@example.com"], "subject": "x", "html": "<p>a</p>"})) == \
            "받는 사람: kim@example.com · 제목: x · 디자인(HTML) 포함"
    finally:
        i18n.reset(tok)
