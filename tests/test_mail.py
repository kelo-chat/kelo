"""Mailboxes connected with an app password (IMAP to read, SMTP to send), against a pretend mail server. No real server is contacted."""

import imaplib
import json
from email.message import EmailMessage

import httpx

from assistant import i18n
from assistant.mail import Mail
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.mail_tools import mail_tools

from test_extract import CONTENT
from test_server import AUTH, TOKEN, Running, scripted

ADDRESS = "nahyun.kim@gmail.test"
PASSWORD = "abcd efgh ijkl mnop"     # Gmail shows app passwords in groups of four


def message(uid, sender, subject, body, msg_id):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"], m["Message-ID"] = sender, ADDRESS, subject, "Fri, 06 Nov 2026 10:00:00 +0900", msg_id
    m.set_content(body)
    return m.as_bytes()


class FakeMailServer:
    def __init__(self):
        self.box = {"1": message("1", "kim@example.com", "회의 일정", "내일 10시에 봬요.", "<m1@example.com>"),
                    "2": message("2", "billing@example.com", "청구서", "이번 달 청구서입니다.", "<m2@example.com>")}
        self.searches: list[tuple] = []
        self.sent: list[EmailMessage] = []
        self.logins: list[tuple[str, str]] = []

    def imap(self, host):
        server = self
        assert host == "imap.gmail.com"

        class Conn:
            def login(self, user, pw):
                server.logins.append((user, pw))
                if pw != PASSWORD.replace(" ", ""):
                    raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
                return "OK", [b"ok"]

            def select(self, folder, readonly=False):
                assert readonly is True                                       # reading never changes the mailbox
                return "OK", [b"2"]

            def uid(self, cmd, *args):
                if cmd == "SEARCH":
                    server.searches.append(args)
                    return "OK", [b"1 2"]
                which, spec = args
                rows = []
                for u in which.split(","):
                    raw = server.box[u]
                    if "HEADER.FIELDS" in spec:
                        head = raw.split(b"\n\n", 1)[0]
                        rows += [(f"{u} (UID {u} FLAGS ({'\\\\Seen' if u == '1' else ''}) BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {{1}}".encode(), head), b")"]
                    else:
                        rows += [(f"{u} (UID {u} BODY[] {{1}}".encode(), raw), b")"]
                return "OK", rows

            def logout(self):
                return "BYE", []
        return Conn()

    def smtp(self, host, port):
        server = self
        assert (host, port) == ("smtp.gmail.com", 465)

        class Conn:
            def login(self, user, pw):
                return 235, b"ok"

            def send_message(self, msg):
                server.sent.append(msg)

            def quit(self):
                pass
        return Conn()


def app_with(tmp_path, fake):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"),
                                     content_dir=str(CONTENT)),
                      tools=None, stream_fn=scripted(), mail_imap=fake.imap, mail_smtp=fake.smtp)


async def test_a_mailbox_is_saved_only_after_a_real_sign_in_works(tmp_path):
    fake = FakeMailServer()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        wrong = await c.post("/api/mail/accounts", json={"provider": "gmail", "address": ADDRESS, "password": "my-normal-password"})
        assert wrong.status_code == 422 and "Nothing was saved" in wrong.json()["detail"] and "app password" in wrong.json()["detail"]
        assert (await c.get("/api/vault")).json()["items"] == []
        ok = await c.post("/api/mail/accounts", json={"provider": "gmail", "address": ADDRESS, "password": PASSWORD})
        assert ok.status_code == 200 and ok.json()["name"] == "mail-gmail"
        assert fake.logins[-1] == (ADDRESS, PASSWORD.replace(" ", ""))          # the spaces Gmail shows are not part of it
        item = (await c.get("/api/vault")).json()["items"][0]
        assert item["kind"] == "email" and item["hosts"] == ["imap.gmail.com", "smtp.gmail.com"] and item["last_result"] == "ok"
        rows = {x["id"]: x for x in (await c.get("/api/connectors")).json()}
        assert rows["gmail"]["status"] == "connected" and rows["imap_email"]["status"] == "available"   # a Gmail box is Gmail's only
        assert rows["gmail"]["method"] == "password" and rows["gmail"]["account"] == ADDRESS and "account" not in rows["imap_email"]
        listed = (await c.get("/api/mail/providers")).json()
        assert [p["id"] for p in listed["providers"]] == ["gmail", "icloud", "yahoo", "naver", "daum"] and listed["accounts"][0]["provider"] == "gmail"
        everything = str((await c.get("/api/vault")).json()) + str(listed) + str((await c.get("/api/vault/log")).json()) + ok.text
        assert PASSWORD.replace(" ", "") not in everything and "abcd" not in everything
        raw = (tmp_path / "s.db").read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("s.db-*"))
        assert PASSWORD.replace(" ", "").encode() not in raw
        for bad in [{"provider": "hotmail", "address": ADDRESS, "password": "x"}, {"provider": "gmail", "address": "not-an-address", "password": "x"}]:
            assert (await c.post("/api/mail/accounts", json=bad)).status_code == 422


async def test_the_assistant_lists_reads_and_sends_with_approval(tmp_path):
    fake = FakeMailServer()
    st = Store(tmp_path / "s.db")
    mail = Mail(st, imap=fake.imap, smtp=fake.smtp)
    await mail.connect("gmail", ADDRESS, PASSWORD)
    tools = {t.name: t for t in mail_tools(mail)}
    assert tools["email_list"].read_only and tools["email_read"].read_only and tools["email_send"].requires_approval
    listed = await tools["email_list"].handler({"query": "from:kim newer_than:7d"})
    assert "uid 2" in listed.splitlines()[0] and "청구서" in listed and "uid 1" in listed and "(unread)" in listed
    assert fake.searches[-1] == ("X-GM-RAW", '"from:kim newer_than:7d"')        # Gmail's own search words
    read = await tools["email_read"].handler({"uid": "1"})
    assert "내일 10시에 봬요." in read and "From: kim@example.com" in read and PASSWORD not in read
    assert (await tools["email_read"].handler({"uid": "1; DROP"})).startswith("error:")
    # several messages in one call (one step instead of one per message), each fenced as the sender's words
    both = await tools["email_read"].handler({"uids": ["1", "2"]})
    assert "--- message uid 1 ---" in both and "--- message uid 2 ---" in both and "내일 10시에 봬요." in both and "청구서" in both
    assert "uids" in tools["email_read"].parameters["properties"] and "several" in tools["email_read"].description
    detail = await tools["email_send"].describe({"to": ["kim@example.com"], "subject": "Re: 회의 일정"})
    assert "kim@example.com" in detail and "회의 일정" in detail and detail.startswith("To: ")
    tok = i18n.use("ko")
    try:
        assert (await tools["email_send"].describe({"to": ["kim@example.com"], "subject": "x"})) == "받는 사람: kim@example.com · 제목: x"
    finally:
        i18n.reset(tok)
    sent = await tools["email_send"].handler({"to": ["kim@example.com"], "subject": "Re: 회의 일정", "body": "네, 내일 봬요.", "reply_to_uid": "1"})
    assert sent == "sent to kim@example.com"
    m = fake.sent[0]
    assert m["From"] == ADDRESS and m["To"] == "kim@example.com" and m["In-Reply-To"] == "<m1@example.com>" and "내일 봬요" in m.get_content()
    assert (await tools["email_send"].handler({"to": ["a@b.c, evil"], "subject": "x", "body": "y"})).startswith("error:")
    assert any(r["action"] == "used" and "sent to 1" in r["detail"] for r in st.vault_log(20))


async def test_without_a_mailbox_the_tools_say_how_to_connect_one(tmp_path):
    tools = {t.name: t for t in mail_tools(Mail(Store(":memory:")))}
    out = await tools["email_list"].handler({})
    assert out.startswith("error:") and "connect_service" in out


async def test_a_locked_vault_stops_the_mail_tools(tmp_path):
    fake = FakeMailServer()
    st = Store(tmp_path / "s.db")
    mail = Mail(st, imap=fake.imap, smtp=fake.smtp)
    await mail.connect("gmail", ADDRESS, PASSWORD)
    st.set_vault_locked(True)
    out = await {t.name: t for t in mail_tools(mail)}["email_list"].handler({})
    assert out.startswith("error:") and "locked" in out
    assert json.loads(st.credential("mail-gmail")["secret"])["u"] == ADDRESS
