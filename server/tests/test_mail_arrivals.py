"""What arrived in a mailbox since the last look (Mail.arrivals / GraphMail.arrivals / Mailboxes.arrivals): the cursor, every unread
non-bulk message (no count limit, paged), the spam and promotions skim, Outlook by receivedDateTime. Pretend servers only."""

from __future__ import annotations

import json
import re
from email.message import EmailMessage
from urllib.parse import parse_qs, urlsplit

import pytest

from assistant.mail import FETCH_CHUNK, Mail, MailError
from assistant.mailboxes import Mailboxes
from assistant.microsoft_mail import ARRIVAL_PAGE, GraphMail
from assistant.netguard import Reply
from assistant.store import Store

ADDRESS, PASSWORD = "me@gmail.test", "abcdefghijklmnop"


def parse_set(text):
    out = set()
    for part in text.split(","):
        a, _, b = part.partition(":")
        out |= set(range(int(a), int(b or a) + 1))
    return out


class Msg:
    def __init__(self, uid, subject="hello", sender="kim@example.com", seen=False, bulk=False, cat="primary", body="Body text here."):
        self.uid, self.subject, self.sender, self.seen, self.bulk, self.cat, self.body = uid, subject, sender, seen, bulk, cat, body


class FakeImap:
    """INBOX and a spam folder with real UIDs, UIDVALIDITY and UIDNEXT; every FETCH must PEEK (nothing becomes read)."""

    def __init__(self, *, gmail=True, validity=7, spam_name="[Gmail]/Spam", spam_flag=True):
        self.gmail, self.validity = gmail, validity
        self.boxes: dict[str, dict[int, Msg]] = {"INBOX": {}}
        self.next_uid = {"INBOX": 100}
        self.flags = {"INBOX": ""}
        if spam_name:
            self.boxes[spam_name] = {}
            self.next_uid[spam_name] = 500
            self.flags[spam_name] = "\\Junk" if spam_flag else ""
        self.log: list[tuple] = []

    def add(self, subject="hello", folder="INBOX", **kw) -> int:
        uid = self.next_uid[folder]
        self.next_uid[folder] += 1
        self.boxes[folder][uid] = Msg(uid, subject, **kw)
        return uid

    def imap(self, host):
        server = self

        class Conn:
            selected = "INBOX"

            def login(self, user, pw):
                return "OK", [b"ok"]

            def list(self, *a):
                return "OK", [f'({server.flags[n]}) "/" "{n}"'.encode() for n in server.boxes]

            def select(self, folder, readonly=False):
                assert readonly is True
                name = json.loads(folder) if folder.startswith('"') else folder
                if name not in server.boxes:
                    return "NO", [b"no such folder"]
                self.selected = name
                return "OK", [str(len(server.boxes[name])).encode()]

            def response(self, code):
                if code == "UIDVALIDITY":
                    return code, [str(server.validity).encode()]
                if code == "UIDNEXT":
                    return code, [str(server.next_uid[self.selected]).encode()]
                return code, [None]

            def uid(self, cmd, *args):
                server.log.append((cmd, self.selected, *args))
                box = server.boxes[self.selected]
                if cmd == "SEARCH":
                    found = set(box)
                    args = list(args)
                    i = 0
                    while i < len(args):
                        a = args[i]
                        if a == "UNSEEN":
                            found = {u for u in found if not box[u].seen}
                        elif a == "UID":
                            i += 1
                            lo, _, hi = args[i].partition(":")
                            found = {u for u in found if int(lo) <= u <= (int(hi) if hi not in ("*", "") else 10**9)}
                            if not found and lo != "" and box:
                                found = {max(box)}   # RFC 3501: "n:*" always matches the highest message
                        elif a == "SINCE":
                            i += 1
                        elif a == "X-GM-RAW":
                            i += 1
                            q = args[i].strip('"')
                            if q.startswith("{"):
                                found = {u for u in found if box[u].cat in ("promotions", "social", "forums")}
                            else:
                                found = {u for u in found if box[u].cat not in ("promotions", "social", "forums")}
                        elif a == "ALL":
                            pass
                        else:
                            raise AssertionError(f"unexpected search key {a}")
                        i += 1
                    return "OK", [" ".join(str(u) for u in sorted(found)).encode()]
                which, spec = args
                assert "BODY.PEEK" in spec and "BODY[" not in spec.replace("BODY.PEEK[", ""), spec
                rows = []
                for u in sorted(parse_set(which)):
                    if u not in box:
                        continue
                    m = box[u]
                    em = EmailMessage()
                    em["From"], em["Subject"], em["Date"] = m.sender, m.subject, "Fri, 06 Nov 2026 10:00:00 +0900"
                    if m.bulk:
                        em["List-Unsubscribe"] = "<mailto:u@example.com>"
                    em.set_content(m.body)
                    head = b"".join(f"{k}: {v}\r\n".encode() for k, v in em.items() if k in ("From", "Subject", "Date", "List-Unsubscribe", "Content-Type",
                                                                                          "Content-Transfer-Encoding", "MIME-Version")) + b"\r\n"
                    text = em.get_payload(decode=True)[:2000]
                    flags = "\\Seen" if m.seen else ""
                    rows += [(f'{u} (UID {u} FLAGS ({flags}) INTERNALDATE "06-Nov-2026 10:00:00 +0900" BODY[HEADER.FIELDS (X)] {{{len(head)}}}'.encode(), head),
                             (f" BODY[TEXT]<0> {{{len(text)}}}".encode(), text), b")"]
                return "OK", rows

            def logout(self):
                return "BYE", []
        return Conn()


def mailbox(tmp_path, fake, provider="gmail"):
    st = Store(tmp_path / "s.db")
    mail = Mail(st, imap=fake.imap, smtp=None)
    st.save_credential(f"mail-{provider}", f"{provider} · {ADDRESS}", ["imap.x.test"], {"type": "mailbox"}, "", kind="email")
    st.set_credential_secret(f"mail-{provider}", json.dumps({"u": ADDRESS, "p": PASSWORD}))
    return st, mail, f"mail-{provider}"


async def test_a_mailbox_seen_for_the_first_time_starts_at_now_and_the_next_look_gets_only_what_came_after(tmp_path):
    fake = FakeImap()
    fake.add("old one")
    fake.add("old two")
    st, mail, acct = mailbox(tmp_path, fake)
    first = await mail.arrivals(acct, None)
    assert first["rows"] == [] and first["cursor"]["uid"] == 101 and first["cursor"]["v"] == 7     # no backlog
    fake.add("new one", body="Please confirm the meeting.")
    second = await mail.arrivals(acct, first["cursor"])
    assert [r["subject"] for r in second["rows"]] == ["new one"] and second["cursor"]["uid"] == 102
    assert "Please confirm the meeting." in second["rows"][0]["snippet"] and second["rows"][0]["unread"]
    seen = len(fake.log)
    third = await mail.arrivals(acct, second["cursor"])
    assert third["rows"] == [] and third["cursor"]["uid"] == 102
    assert len(fake.log) == seen                                                                   # nothing new: not even a search


async def test_every_unread_message_since_the_cursor_is_returned_not_just_five_and_read_bulk_and_promotions_stay_out(tmp_path):
    fake = FakeImap()
    st, mail, acct = mailbox(tmp_path, fake)
    cur = (await mail.arrivals(acct, None))["cursor"]
    for i in range(12):
        fake.add(f"personal {i}")
    fake.add("already read", seen=True)
    fake.add("newsletter", bulk=True)
    fake.add("sale", cat="promotions")
    got = await mail.arrivals(acct, cur)
    assert [r["subject"] for r in got["rows"]] == [f"personal {i}" for i in range(12)]            # oldest first, all 12
    assert got["cursor"]["uid"] == 114                                                             # past the ones left out as well


async def test_a_long_gap_is_read_in_pages(tmp_path):
    fake = FakeImap(gmail=False)
    st, mail, acct = mailbox(tmp_path, fake, "naver")
    cur = (await mail.arrivals(acct, None))["cursor"]
    for i in range(FETCH_CHUNK * 2 + 30):
        fake.add(f"m{i}")
    got = await mail.arrivals(acct, cur)
    assert len(got["rows"]) == FETCH_CHUNK * 2 + 30
    assert len([c for c in fake.log if c[0] == "FETCH"]) == 3                                      # 100 + 100 + 30


async def test_a_renumbered_folder_falls_back_to_the_date(tmp_path):
    fake = FakeImap()
    st, mail, acct = mailbox(tmp_path, fake)
    cur = (await mail.arrivals(acct, None))["cursor"]
    fake.validity = 8
    fake.add("after renumbering")
    got = await mail.arrivals(acct, {**cur, "t": 0})
    assert [r["subject"] for r in got["rows"]] == ["after renumbering"] and got["cursor"]["v"] == 8


async def test_the_spam_folder_is_found_by_its_junk_mark_and_skipped_when_there_is_none(tmp_path):
    fake = FakeImap()
    st, mail, acct = mailbox(tmp_path, fake)
    cur = (await mail.arrivals(acct, None, "junk"))["cursor"]
    fake.add("Your invoice is overdue", folder="[Gmail]/Spam", seen=True)                          # in spam, whether read or not
    fake.add("win a prize", folder="[Gmail]/Spam", bulk=True)
    got = await mail.arrivals(acct, cur, "junk")
    assert [r["subject"] for r in got["rows"]] == ["Your invoice is overdue", "win a prize"]
    (tmp_path / "n").mkdir()
    (tmp_path / "x").mkdir()
    named = FakeImap(gmail=False, spam_name="스팸메일함", spam_flag=False)                          # no \Junk mark: no guess from the name
    st2, mail2, acct2 = mailbox(tmp_path / "n", named, "naver")
    assert await mail2.arrivals(acct2, None, "junk") is None
    marked = FakeImap(gmail=False, spam_name="Anything")
    (tmp_path / "m").mkdir()
    st4, mail4, acct4 = mailbox(tmp_path / "m", marked, "naver")
    assert (await mail4.arrivals(acct4, None, "junk"))["rows"] == []
    none = FakeImap(gmail=False, spam_name=None)
    st3, mail3, acct3 = mailbox(tmp_path / "x", none, "naver")
    assert await mail3.arrivals(acct3, None, "junk") is None
    assert await mail3.arrivals(acct3, None, "promo") is None                                      # only Gmail sorts promotions


async def test_gmail_promotions_are_their_own_kind(tmp_path):
    fake = FakeImap()
    st, mail, acct = mailbox(tmp_path, fake)
    cur = (await mail.arrivals(acct, None, "promo"))["cursor"]
    fake.add("flash sale", cat="promotions")
    fake.add("a person", cat="primary")
    got = await mail.arrivals(acct, cur, "promo")
    assert [r["subject"] for r in got["rows"]] == ["flash sale"]


async def test_a_mailbox_that_cannot_be_reached_raises_so_the_caller_keeps_its_cursor(tmp_path):
    def broken(host):
        raise OSError("down")
    st = Store(tmp_path / "s.db")
    st.save_credential("mail-gmail", f"Gmail · {ADDRESS}", ["imap.gmail.com"], {"type": "mailbox"}, "", kind="email")
    st.set_credential_secret("mail-gmail", json.dumps({"u": ADDRESS, "p": PASSWORD}))
    with pytest.raises(MailError):
        await Mail(st, imap=broken, smtp=None).arrivals("mail-gmail", {"v": 1, "uid": 5, "t": 0})


# ------------------------------------------------------------------------------------------------------------------ Outlook
def row(i, received, *, read=False, cls="focused", headers=(), subject=None, preview="Hello there"):
    return {"id": f"AAMk-{i}", "subject": subject or f"mail {i}", "from": {"emailAddress": {"name": "Min", "address": "min@example.org"}},
            "receivedDateTime": received, "isRead": read, "inferenceClassification": cls, "bodyPreview": preview,
            "internetMessageHeaders": [{"name": h, "value": "x"} for h in headers]}


class FakeGraph:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {"inbox": [], "junkemail": []}
        self.calls: list[dict] = []
        self.fail = False

    async def __call__(self, method, url, *, allowed_hosts, headers=None, content=None, **_):
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        folder = parts.path.split("/")[-2]
        self.calls.append({"folder": folder, **q})
        if self.fail:
            return Reply(500, {}, b"{}", False)
        flt = q.get("$filter", "")
        rows = list(self.rows[folder])
        m = re.search(r"receivedDateTime gt (\S+)", flt)
        if m:
            rows = [r for r in rows if r["receivedDateTime"] > m.group(1)]
        if "isRead eq false" in flt:
            rows = [r for r in rows if not r["isRead"]]
        c = re.search(r"inferenceClassification eq '(\w+)'", flt)
        if c:
            rows = [r for r in rows if r["inferenceClassification"] == c.group(1)]
        rows.sort(key=lambda r: r["receivedDateTime"])
        skip, top = int(q.get("$skip", 0)), int(q["$top"])
        return Reply(200, {}, json.dumps({"value": rows[skip:skip + top]}).encode(), False)


def outlook(tmp_path):
    st = Store(tmp_path / "o.db")
    st.save_credential("microsoft-api", "Microsoft · me@outlook.com", ["graph.microsoft.com"], {"type": "bearer_header"}, "", kind="api")
    st.set_credential_secret("microsoft-api", "access-token")
    g = FakeGraph()
    return g, GraphMail(st, fetcher=g)


async def test_outlook_arrivals_by_received_time_with_every_page(tmp_path):
    g, gm = outlook(tmp_path)
    assert (await gm.arrivals("outlook", None))["rows"] == [] and g.calls == []                   # first time: a cursor at now, no request
    cur = {"t": "2026-11-10T00:00:00Z"}
    for i in range(ARRIVAL_PAGE + 7):
        g.rows["inbox"].append(row(i, f"2026-11-10T01:{i // 60:02d}:{i % 60:02d}Z"))
    g.rows["inbox"].append(row(900, "2026-11-10T02:00:00Z", read=True))                           # read: out
    g.rows["inbox"].append(row(901, "2026-11-10T02:01:00Z", cls="other"))                         # Other inbox: out
    g.rows["inbox"].append(row(902, "2026-11-10T02:02:00Z", headers=("List-Unsubscribe",)))       # bulk headers: out
    g.rows["inbox"].append(row(903, "2026-11-09T23:00:00Z"))                                       # before the cursor: out
    got = await gm.arrivals("outlook", cur)
    assert [r["subject"] for r in got["rows"]] == [f"mail {i}" for i in range(ARRIVAL_PAGE + 7)]
    assert got["rows"][0]["snippet"] == "Hello there" and len(g.calls) == 2                       # two pages
    assert got["cursor"]["t"] > "2026-11-10T00:00:00Z" and got["cursor"]["t"].endswith("Z")


async def test_outlook_promotions_and_junk_kinds_and_a_failure_raises(tmp_path):
    g, gm = outlook(tmp_path)
    cur = {"t": "2026-11-10T00:00:00Z"}
    g.rows["inbox"] += [row(1, "2026-11-10T01:00:00Z", cls="other", subject="deal"), row(2, "2026-11-10T01:01:00Z", subject="person")]
    g.rows["junkemail"] += [row(3, "2026-11-10T01:02:00Z", read=True, subject="from junk")]
    assert [r["subject"] for r in (await gm.arrivals("outlook", cur, "promo"))["rows"]] == ["deal"]
    assert [r["subject"] for r in (await gm.arrivals("outlook", cur, "junk"))["rows"]] == ["from junk"]
    assert g.calls[-1]["folder"] == "junkemail"
    g.fail = True
    with pytest.raises(MailError):
        await gm.arrivals("outlook", cur)


async def test_mailboxes_routes_arrivals_to_the_right_backend(tmp_path):
    g, gm = outlook(tmp_path)
    boxes = Mailboxes(Mail(Store(tmp_path / "i.db")), gm)
    g.rows["inbox"].append(row(1, "2026-11-10T01:00:00Z"))
    got = await boxes.arrivals("outlook", {"t": "2026-11-10T00:00:00Z"})
    assert [r["subject"] for r in got["rows"]] == ["mail 1"]
