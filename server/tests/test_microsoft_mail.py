"""The Outlook mailbox by Microsoft Graph (microsoft_mail.py) behind mailboxes.Mailboxes: listed, read and changed like an IMAP one,
Outlook's "Other" inbox and list/bulk headers left out of the briefing, a "Read only" grant that says to connect again for changes, and
the account in the briefing's accounts list (mail:outlook) and in the email tools. A fake Graph only; no network."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from assistant.briefing_sources import Sources
from assistant.mail import Mail, MailError, Selection
from assistant.mailboxes import Mailboxes
from assistant.microsoft_mail import READ_ONLY, GraphMail
from assistant.netguard import Reply
from assistant.store import Store
from assistant.tools.mail_tools import mail_tools

ROWS = [
    {"id": "AAMk-1", "subject": "내일 회의 시간 바꿀 수 있을까요?", "from": {"emailAddress": {"name": "민지", "address": "minji@example.org"}},
     "receivedDateTime": "2026-11-10T00:10:00Z", "isRead": False, "inferenceClassification": "focused", "internetMessageHeaders": []},
    {"id": "AAMk-2", "subject": "Ending Soon: The Fall Wardrobe Event", "from": {"emailAddress": {"name": "Ralph Lauren", "address": "news@e.example.com"}},
     "receivedDateTime": "2026-11-10T00:05:00Z", "isRead": False, "inferenceClassification": "focused",
     "internetMessageHeaders": [{"name": "List-Unsubscribe", "value": "<mailto:unsub@e.example.com>"}]},
    {"id": "AAMk-3", "subject": "Weekly deals", "from": {"emailAddress": {"address": "deals@shop.example.com"}},
     "receivedDateTime": "2026-11-10T00:01:00Z", "isRead": False, "inferenceClassification": "other", "internetMessageHeaders": []},
]


class FakeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.batches: list[list[dict]] = []
        self.sent: list[dict] = []

    async def __call__(self, method, url, *, allowed_hosts, headers=None, content=None, **_):
        assert allowed_hosts == ["graph.microsoft.com"] and url.startswith("https://graph.microsoft.com/v1.0/")
        assert headers["Authorization"] == "Bearer access-token"
        body = json.loads(content) if content else None
        self.calls.append((method, url, body))
        path = urlsplit(url).path.removeprefix("/v1.0")
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        if path == "/me/mailFolders/inbox/messages":
            rows = ROWS
            if "isRead eq false" in q.get("$filter", ""):
                rows = [r for r in rows if not r["isRead"]]
            if "inferenceClassification eq 'focused'" in q.get("$filter", ""):
                rows = [r for r in rows if r["inferenceClassification"] == "focused"]
            return Reply(200, {}, json.dumps({"value": rows[: int(q.get("$top", 10))]}).encode(), False)
        if path.startswith("/me/messages/") and method == "GET" and not path.endswith("/attachments"):
            mid = unquote(path.split("/")[3])
            r = next(r for r in ROWS if r["id"] == mid)
            return Reply(200, {}, json.dumps({**r, "toRecipients": [{"emailAddress": {"address": "me@outlook.com"}}], "ccRecipients": [],
                                              "body": {"contentType": "text", "content": "내일 3시로 바꿔도 될까요?"},
                                              "internetMessageId": "<m1@example.org>", "hasAttachments": False}).encode(), False)
        if path == "/$batch":
            self.batches.append(body["requests"])
            return Reply(200, {}, json.dumps({"responses": [{"id": r["id"], "status": 200, "body": {}} for r in body["requests"]]}).encode(), False)
        if path == "/me/sendMail":
            self.sent.append(body)
            return Reply(202, {}, b"", False)
        return Reply(404, {}, b"{}", False)


def outlook(tmp_path, scope: str = "all") -> tuple[Store, FakeGraph, GraphMail]:
    st = Store(tmp_path / "s.db")
    st.save_credential("microsoft-api", "Microsoft · me@outlook.com", ["graph.microsoft.com"], {"type": "bearer_header"}, "", kind="api")
    st.set_credential_secret("microsoft-api", "access-token")
    st.set_meta("oauth_scope.microsoft", scope)
    g = FakeGraph()
    return st, g, GraphMail(st, fetcher=g)


async def test_outlook_mail_is_listed_read_and_changed_like_any_mailbox(tmp_path):
    st, g, gm = outlook(tmp_path)
    boxes = Mailboxes(Mail(st), gm)
    assert boxes.accounts() == [{"name": "outlook", "provider": "outlook", "label": "Outlook · me@outlook.com", "address": "me@outlook.com",
                                 "title": "Outlook Mail"}]
    rows = await boxes.list("", unread=True)                        # the only mailbox: no account needed
    assert [r["subject"] for r in rows] == [r["subject"] for r in ROWS] and [r["bulk"] for r in rows] == [False, True, True]
    assert all(r["uid"].isdigit() for r in rows)                     # Graph's long ids become short numbers, like IMAP uids
    # the briefing and the heads-ups: Outlook's own "Other" inbox and the list/bulk headers are left out
    briefing = await boxes.list("outlook", unread=True, skip_bulk=True)
    assert [r["subject"] for r in briefing] == ["내일 회의 시간 바꿀 수 있을까요?"]
    assert "inferenceClassification eq 'focused'" in unquote(g.calls[-1][1])
    msg = await boxes.read("outlook", rows[0]["uid"])
    assert msg["text"] == "내일 3시로 바꿔도 될까요?" and msg["from"] == "민지 <minji@example.org>" and msg["to"] == "me@outlook.com"
    got = await boxes.update("outlook", "read", Selection.of(uids=[rows[0]["uid"]]))
    assert got["done"] == 1 and g.batches[-1] == [{"id": "0", "method": "PATCH", "url": "/me/messages/AAMk-1",
                                                   "headers": {"Content-Type": "application/json"}, "body": {"isRead": True}}]
    got = await boxes.update("outlook", "archive", Selection.of(uids=[rows[1]["uid"], rows[2]["uid"]]))
    assert got["done"] == 2 and got["dest"] == "Archive" and [r["body"] for r in g.batches[-1]] == [{"destinationId": "archive"}] * 2
    plan = await boxes.plan("outlook", "trash", Selection.of(uids=[rows[2]["uid"]]))
    assert plan["uids"] == [int(rows[2]["uid"])] and plan["dest"] == "Deleted Items"
    got = await boxes.update("outlook", "trash", Selection.of(uids=[rows[2]["uid"]]), approved=plan["uids"])
    assert got["done"] == 1 and g.batches[-1][0]["url"] == "/me/messages/AAMk-3/move" and g.batches[-1][0]["body"] == {"destinationId": "deleteditems"}
    assert await boxes.send("outlook", ["a@example.org"], "Hi", "Hello") == "sent to a@example.org"
    assert g.sent[-1]["message"]["toRecipients"] == [{"emailAddress": {"address": "a@example.org"}}]
    with pytest.raises(MailError):
        await boxes.read("outlook", "99999")                         # a number email_list never gave


async def test_a_read_only_grant_reads_and_says_to_connect_again_for_changes(tmp_path):
    st, g, gm = outlook(tmp_path, scope="read")
    boxes = Mailboxes(Mail(st), gm)
    rows = await boxes.list("outlook", unread=True)
    assert len(rows) == 3
    n = len(g.calls)
    for act in (boxes.update("outlook", "read", Selection.of(uids=[rows[0]["uid"]])), boxes.plan("outlook", "trash", Selection.of(uids=[rows[0]["uid"]])),
                boxes.send("outlook", ["a@example.org"], "Hi", "Hello")):
        with pytest.raises(MailError) as e:
            await act
        assert str(e.value) == READ_ONLY and "Mail, calendar and contacts" in str(e.value)
    assert len(g.calls) == n                                         # nothing was sent to Outlook


async def test_outlook_mail_is_in_the_briefings_accounts_and_the_email_tools(tmp_path):
    st, g, gm = outlook(tmp_path)

    class NoCalendars:
        def accounts(self):
            return []
    boxes = Mailboxes(Mail(st), gm)
    src = Sources(st, NoCalendars(), boxes)
    pub = src.public()["mail"]["accounts"]
    assert [(a["id"], a["provider"], a["name"], a["address"], a["on"]) for a in pub] == [("mail:outlook", "outlook", "Outlook Mail", "me@outlook.com", True)]
    view = src.mail_view()
    assert [a["name"] for a in view.accounts()] == ["outlook"]
    src.set_group("mail", {"mail:outlook": False})
    assert view.accounts() == []                                     # switched off: not read
    tools = {t.name: t for t in mail_tools(boxes)}
    out = await tools["email_list"].handler({"account": "outlook", "unread": True})
    assert "내일 회의 시간 바꿀 수 있을까요?" in out and "(unread)" in out
