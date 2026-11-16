"""Asking to try Google sign-in while it is closed (beta.py): the phone -> its own server -> the operator's broker (/broker/beta), the
operator told at once (a notification, and an email to support through the Gmail connected there), the owner-only list, Approved /
Decline with one email to the requester, and the requester's copy then allowed to sign in. Rate limits, dedupe, bad input and what is
kept. Everything runs against local servers, a pretend Google and a pretend mail server; no real mail is sent and no model is asked."""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from assistant import beta as beta_mod
from assistant.netguard import Reply
from assistant.server import Settings, create_app

from test_google_web import CLIENT, RELAY, SECRET, FakeGoogle
from test_mail import PASSWORD, FakeMailServer
from test_server import AUTH, TOKEN, Running, scripted

BROKER = "https://kelo.example.org"
OP_GMAIL = "operator@gmail.test"
ASKER = "friend.person@gmail.com"


@pytest.fixture(autouse=True)
def beta_requests_open(monkeypatch):
    """The request flow stays in the code (closed in production, owner 2026-11-15): these tests run it opened."""
    from assistant.api import operator
    monkeypatch.setattr(operator, "BETA_REQUESTS_OPEN", True)


def never_called(*a, **kw):
    raise AssertionError("no model may be asked anything here")


def op_app(tmp_path, mail):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "op.db"), oauth_relay=RELAY,
                               public_url=BROKER),
                      tools=None, stream_fn=never_called, mail_imap=mail.imap, mail_smtp=mail.smtp)


def friend_app(tmp_path, fetcher, name="friend"):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / f"{name}.db"), oauth_relay=RELAY,
                               oauth_broker=BROKER, public_url=f"https://{name}.example.net"),
                      tools=None, stream_fn=never_called, mcp_fetcher=fetcher)


def via(op_url, google, seen):
    """Requests for the broker's address go to the operator's local server; Google's to the pretend Google."""
    async def fetch(method, url, *, allowed_hosts, headers=None, content=None):
        assert urlsplit(url).hostname in allowed_hosts
        if url.startswith(BROKER + "/broker/"):
            seen.append((url.removeprefix(BROKER), json.loads(content) if content else None))
            async with httpx.AsyncClient(base_url=op_url) as hop:
                r = await hop.request(method, url.removeprefix(BROKER), headers=headers, content=content)
            return Reply(r.status_code, dict(r.headers), r.content, False)
        return await google.fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content)
    return fetch


async def set_up_operator(oc, *, gmail=True):
    await oc.put("/api/oauth-apps/google", json={"client_id": CLIENT, "client_secret": SECRET})
    if gmail:   # the operator's own mailbox, which the beta emails go out from
        ok = await oc.post("/api/mail/accounts", json={"provider": "gmail", "address": OP_GMAIL, "password": PASSWORD})
        assert ok.status_code == 200


class OpMail(FakeMailServer):
    def imap(self, host):
        conn = super().imap(host)
        conn.login = lambda user, pw: ("OK", [b"ok"])
        return conn


async def test_request_notify_approve_and_the_copy_signs_in(tmp_path, monkeypatch):
    g, m, seen = FakeGoogle(), OpMail(), []
    op = op_app(tmp_path, m)
    async with Running(op) as o, httpx.AsyncClient(base_url=o.url, headers=AUTH) as oc:
        await set_up_operator(oc)
        friend = friend_app(tmp_path, via(o.url, g, seen))
        async with Running(friend) as f, httpx.AsyncClient(base_url=f.url, headers={**AUTH, "Accept-Language": "ko-KR"}) as fc:
            # closed: the rows wait for the review, and nothing has been asked yet (no broker call for a copy that never asked)
            assert (await fc.get("/api/google/beta")).json()["status"] == "none"
            assert not [s for s in seen if s[0].startswith("/broker/beta")]
            rows = {x["id"]: x for x in (await fc.get("/api/connectors")).json()}
            assert rows["gmail"]["connect_state"] == "pending_review"
            # a bad address is refused in words, before anything leaves the copy
            bad = await fc.post("/api/google/beta", json={"email": "not an email", "products": ["gmail"]})
            assert bad.status_code == 422 and "Google" in bad.json()["detail"]
            assert not [s for s in seen if s[0] == "/broker/beta"]
            r = await fc.post("/api/google/beta", json={"email": " Friend.Person@Gmail.com ", "products": ["gmail", "nope"],
                                                       "app_version": "1.3.0", "extra": "dropped"})
            assert r.status_code == 200, r.text
            got = r.json()
            assert got["status"] == "requested" and got["email"] == ASKER and got["products"] == ["gmail"] and got["open"] is False
            # what left the copy: its install secret and the four request fields, nothing else
            sent = next(b for p, b in seen if p == "/broker/beta")
            assert set(sent) == {"install_id", "request"}
            assert sent["request"] == {"email": ASKER, "products": ["gmail"], "app_version": "1.3.0", "language": "ko"}
            # the state survives reopening (kept in the copy's own database)
            assert (await fc.get("/api/google/beta")).json()["status"] == "requested"

            # the operator: one stored row with only the hash of the install, a notification, and one email to support
            await op.state.beta_desk.drain()
            row = op.state.beta_desk.store._all("SELECT * FROM beta_requests")[0]
            install = friend.state.reports.install_id()
            assert row["install"] == beta_mod.install_hash(install) and install not in json.dumps(row)
            assert set(row) == {"id", "created_at", "email", "products", "install", "language", "app_version", "status", "handled_at", "notice"}
            assert "127.0.0.1" not in json.dumps(row) and row["notice"] == "sent"
            notes = (await oc.get("/api/notifications")).json()["items"]
            assert notes[0]["type"] == "beta_request" and ASKER in notes[0]["body"]
            assert len(m.sent) == 1
            mail = m.sent[0]
            assert mail["To"] == "support@kelo.chat" and mail["From"] == OP_GMAIL
            assert mail["Subject"] == f"[Kelo beta] {ASKER} asked for Gmail access"
            text = mail.get_content()
            assert ASKER in text and "Test users" in text and "Audience" in text and "Approved" in text
            assert install not in text and row["install"] not in text and "1.3.0" not in text   # nothing else about the requester

            # asking again for the same address and another connector: no new row, no new email, products merged
            again = await fc.post("/api/google/beta", json={"email": ASKER, "products": ["calendar"]})
            assert again.json()["products"] == ["gmail", "calendar"]
            await op.state.beta_desk.drain()
            assert len(m.sent) == 1 and len(op.state.beta_desk.store._all("SELECT * FROM beta_requests")) == 1

            # the list is the owner's only
            listed = (await oc.get("/api/operator/beta")).json()
            assert listed["open"] == 1 and listed["mail_from"] == OP_GMAIL and listed["receiving"] is True
            req = listed["requests"][0]
            assert req["email"] == ASKER and req["products"] == ["gmail", "calendar"] and req["status"] == "requested"
            async with httpx.AsyncClient(base_url=o.url) as anon:
                assert (await anon.get("/api/operator/beta")).status_code == 401
                assert (await anon.post(f"/api/operator/beta/{req['id']}", json={"action": "approve"})).status_code == 401
            from test_devices import add_phone, bearer
            _, pr = await add_phone(oc)
            phone = bearer(pr.json()["token"])
            assert (await oc.get("/api/operator/beta", headers=phone)).status_code == 200      # every device has the same rights

            # before the operator answers, the copy's sign-in stays closed (and the broker's exchange refuses its install)
            async with httpx.AsyncClient(base_url=o.url) as anon:
                r = await anon.post("/broker/google/exchange", json={"code": "g-code", "redirect_uri": RELAY, "code_verifier": "v",
                                                                   "install_id": install})
                assert r.status_code == 409

            # Approved: one email to the requester in their language, Reply-To support
            ans = (await oc.post(f"/api/operator/beta/{req['id']}", json={"action": "approve"})).json()
            assert ans["mailed"] is True and ans["request"]["status"] == "approved"
            mail = m.sent[1]
            assert mail["To"] == ASKER and mail["Reply-To"] == "support@kelo.chat" and mail["From"] == OP_GMAIL
            assert "승인" in mail["Subject"] and "7일" in mail.get_content() and "Gmail" in mail.get_content()

            # the copy learns it on its next look after the cache time (not on every screen)
            calls = len([s for s in seen if s[0] == "/broker/beta/status"])
            assert (await fc.get("/api/google/beta")).json()["status"] == "requested"
            assert len([s for s in seen if s[0] == "/broker/beta/status"]) == calls
            monkeypatch.setattr(beta_mod, "CACHE_SECONDS", 0.0)
            now = (await fc.get("/api/google/beta")).json()
            assert now["status"] == "approved" and now["open"] is True
            rows = {x["id"]: x for x in (await fc.get("/api/connectors")).json()}
            assert "connect_state" not in rows["gmail"] and "connect_state" not in rows["google_calendar"]
            assert (await fc.get("/api/mail/providers")).json()["google_web"] is True
            assert (await fc.get("/api/google/calendar")).json()["pending_review"] is False

            # the sign-in starts, and the broker lets this install's exchange in (another install stays out)
            url = (await fc.post("/api/google/web/start", json={"origin": "https://friend.example.net", "product": "gmail"})).json()["url"]
            assert parse_qs(urlsplit(url).query)["client_id"] == [CLIENT]
            async with httpx.AsyncClient(base_url=o.url) as anon:
                r = await anon.post("/broker/google/exchange", json={"code": "g-code", "redirect_uri": RELAY, "code_verifier": "v",
                                                                   "install_id": install})
                assert r.status_code != 409                     # let in (Google itself then refuses this made-up code)
                r = await anon.post("/broker/google/exchange", json={"code": "g-code", "redirect_uri": RELAY, "code_verifier": "v",
                                                                   "install_id": "x" * 43})
                assert r.status_code == 409                     # another install is not
            # the global switch still works as before
            assert (await oc.get("/api/oauth-apps/google")).json()["open_to_all"] is False


async def test_decline_mails_only_when_asked_and_the_copy_says_not_approved(tmp_path, monkeypatch):
    g, m, seen = FakeGoogle(), OpMail(), []
    op = op_app(tmp_path, m)
    monkeypatch.setattr(beta_mod, "CACHE_SECONDS", 0.0)
    async with Running(op) as o, httpx.AsyncClient(base_url=o.url, headers=AUTH) as oc:
        await set_up_operator(oc)
        friend = friend_app(tmp_path, via(o.url, g, seen))
        async with Running(friend) as f, httpx.AsyncClient(base_url=f.url, headers=AUTH) as fc:
            assert (await fc.post("/api/google/beta", json={"email": ASKER, "products": ["calendar"]})).status_code == 200
            await op.state.beta_desk.drain()
            rid = (await oc.get("/api/operator/beta")).json()["requests"][0]["id"]
            ans = (await oc.post(f"/api/operator/beta/{rid}", json={"action": "decline"})).json()
            assert ans["mailed"] is False and ans["request"]["status"] == "declined" and len(m.sent) == 1   # only the support email
            assert (await fc.get("/api/google/beta")).json()["status"] == "declined"
            # asking again for the same address keeps the answer (no new row, no new email)
            assert (await fc.post("/api/google/beta", json={"email": ASKER, "products": ["calendar"]})).json()["status"] == "declined"
            await op.state.beta_desk.drain()
            assert len(m.sent) == 1
            # with the toggle on, one short polite email in English (no language was sent)
            ans = (await oc.post(f"/api/operator/beta/{rid}", json={"action": "decline", "email": True})).json()
            assert ans["mailed"] is True and m.sent[-1]["To"] == ASKER and "not approved" in m.sent[-1].get_content()
            assert m.sent[-1]["Reply-To"] == "support@kelo.chat"
            assert (await oc.delete(f"/api/operator/beta/{rid}")).json() == {"deleted": True}
            assert (await oc.delete(f"/api/operator/beta/{rid}")).status_code == 404
            assert (await fc.get("/api/google/beta")).json()["status"] == "none"   # deleted: the button comes back


async def test_without_a_gmail_mailbox_no_email_is_sent_and_the_list_says_so(tmp_path):
    g, m, seen = FakeGoogle(), OpMail(), []
    op = op_app(tmp_path, m)
    async with Running(op) as o, httpx.AsyncClient(base_url=o.url, headers=AUTH) as oc:
        await set_up_operator(oc, gmail=False)
        friend = friend_app(tmp_path, via(o.url, g, seen))
        async with Running(friend) as f, httpx.AsyncClient(base_url=f.url, headers=AUTH) as fc:
            assert (await fc.post("/api/google/beta", json={"email": ASKER, "products": ["gmail"]})).status_code == 200
            await op.state.beta_desk.drain()
            listed = (await oc.get("/api/operator/beta")).json()
            assert listed["mail_from"] == "" and listed["requests"][0]["notice"] == "no_mailbox"
            ans = (await oc.post(f"/api/operator/beta/{listed['requests'][0]['id']}", json={"action": "approve"})).json()
            assert ans["mailed"] is False and "Gmail" in ans["mail_error"] and ans["request"]["status"] == "approved"
            assert m.sent == []


async def test_the_operator_own_copy_stores_directly(tmp_path):
    m = OpMail()
    op = op_app(tmp_path, m)
    async with Running(op) as o, httpx.AsyncClient(base_url=o.url, headers=AUTH) as oc:
        await set_up_operator(oc)
        r = (await oc.post("/api/google/beta", json={"email": ASKER, "products": ["gmail"]})).json()
        assert r["status"] == "requested"
        await op.state.beta_desk.drain()
        rid = (await oc.get("/api/operator/beta")).json()["requests"][0]["id"]
        await oc.post(f"/api/operator/beta/{rid}", json={"action": "approve", "email": False})
        assert len(m.sent) == 1                                            # the support email only; Approved without the mail
        assert (await oc.get("/api/google/beta")).json()["open"] is True


async def test_broker_rate_limits_dedupe_and_bad_input(tmp_path):
    m = OpMail()
    op = op_app(tmp_path, m)
    async with Running(op) as o, httpx.AsyncClient(base_url=o.url, headers=AUTH) as oc:
        await set_up_operator(oc, gmail=False)
        async with httpx.AsyncClient(base_url=o.url) as anon:
            secret = "s" * 43
            req = {"email": ASKER, "products": ["gmail"], "app_version": "1.3.0", "language": "ko"}
            assert (await anon.post("/broker/beta", json={"install_id": "short", "request": req})).status_code == 403
            assert (await anon.post("/broker/beta", json={"install_id": secret, "request": {**req, "email": "a@b"}})).status_code == 422
            assert (await anon.post("/broker/beta", json={"install_id": secret, "request": {**req, "products": []}})).status_code == 422
            assert (await anon.post("/broker/beta", content=b"not json")).status_code == 422
            for _ in range(3):   # the same install and address: one row
                assert (await anon.post("/broker/beta", json={"install_id": secret, "request": req})).json()["status"] == "requested"
            assert len(op.state.beta_desk.store._all("SELECT * FROM beta_requests")) == 1
            # another address from the same install replaces its open request
            assert (await anon.post("/broker/beta", json={"install_id": secret, "request": {**req, "email": "other@example.com"}})).status_code == 200
            rows = op.state.beta_desk.store._all("SELECT * FROM beta_requests")
            assert [r["email"] for r in rows] == ["other@example.com"]
            # five an hour per install
            assert (await anon.post("/broker/beta", json={"install_id": secret, "request": req})).status_code == 200
            r = await anon.post("/broker/beta", json={"install_id": secret, "request": req})
            assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
            # status: the install secret is required
            assert (await anon.post("/broker/beta/status", json={})).status_code == 403
            assert (await anon.post("/broker/beta/status", json={"install_id": secret})).json() == {"status": "requested", "open": False}
            assert (await anon.post("/broker/beta/status", json={"install_id": "t" * 43})).json() == {"status": "none", "open": False}


async def test_a_server_that_is_not_the_broker_refuses_and_a_copy_without_a_broker_says_so(tmp_path):
    m = OpMail()
    plain = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "p.db")),
                       tools=None, stream_fn=scripted())
    async with Running(plain) as p, httpx.AsyncClient(base_url=p.url, headers=AUTH) as c:
        assert (await c.post("/broker/beta", json={})).status_code == 404
        assert (await c.post("/broker/beta/status", json={})).status_code == 404
        assert (await c.get("/api/google/beta")).json()["can_request"] is False
        r = await c.post("/api/google/beta", json={"email": ASKER, "products": ["gmail"]})
        assert r.status_code == 503
    del m


def test_purge_keeps_handled_rows_only_for_a_while(tmp_path):
    from assistant.store import Store
    now = [1_000_000.0]
    desk = beta_mod.BetaDesk(Store(tmp_path / "s.db"), wall=lambda: now[0])
    import asyncio

    async def go():
        a = desk.add({"email": "a@example.com", "products": ["gmail"], "language": "en", "app_version": ""}, "h1")
        b = desk.add({"email": "b@example.com", "products": ["gmail"], "language": "en", "app_version": ""}, "h2")
        await desk.decline(a["id"])
        await desk.approve(b["id"], send_mail=False)
        now[0] += (beta_mod.KEEP_DAYS + 1) * 86400
        desk.purge()
        assert desk.store._all("SELECT * FROM beta_requests") == []
        assert desk.allowed("h2") and desk.status_of("h2") == "approved"     # the approval itself stays (hash only)
        assert desk.status_of("h1") == "none"
        c = desk.add({"email": "c@example.com", "products": ["calendar"], "language": "en", "app_version": ""}, "h3")
        now[0] += (beta_mod.OPEN_KEEP_DAYS + 1) * 86400
        desk.purge()
        assert desk.row(c["id"]) is None
    asyncio.run(go())


@pytest.mark.parametrize("email,ok", [("a.b+c@gmail.com", True), ("A@EXAMPLE.CO.KR", True), ("a@b", False), ("a b@c.com", False),
                                      ("@c.com", False), ("a@-c.com", False), ("x" * 65 + "@c.com", False), ("", False), (None, False)])
def test_email_check(email, ok):
    assert bool(beta_mod.clean_email(email)) is ok


async def test_an_approved_copy_sends_its_install_secret_with_the_exchange(tmp_path):
    from assistant.google_web import GoogleWeb
    from assistant.store import Store
    g, bodies = FakeGoogle(), []

    async def broker(method, url, *, allowed_hosts, headers=None, content=None):
        if url.endswith("/broker/google/client"):
            return Reply(200, {}, json.dumps({"client_id": CLIENT, "open": False}).encode(), False)
        bodies.append(json.loads(content))
        return Reply(200, {}, json.dumps({"access_token": "ya29.x"}).encode(), False)

    gw = GoogleWeb(Store(tmp_path / "s.db"), relay=RELAY, broker=BROKER, fetcher=broker)
    await gw.client_id()
    assert not gw.open_here()
    gw.beta_open, gw.beta_install = (lambda: True), (lambda: "i" * 43)
    url = await gw.start("https://friend.example.net", "calendar")
    await gw.exchange(parse_qs(urlsplit(url).query)["state"][0], "g-code")
    assert bodies[-1]["install_id"] == "i" * 43
    del g


async def test_requests_are_closed_the_app_shows_no_button_and_a_request_is_refused(tmp_path, monkeypatch):
    from assistant.api import operator
    monkeypatch.setattr(operator, "BETA_REQUESTS_OPEN", False)
    async with Running(op_app(tmp_path, FakeMailServer())) as op, httpx.AsyncClient(base_url=op.url, headers=AUTH) as c:
        await c.put("/api/oauth-apps/google", json={"client_id": CLIENT, "client_secret": SECRET})
        assert (await c.get("/api/google/beta")).json()["can_request"] is False
        r = await c.post("/api/google/beta", json={"email": "a@example.com", "products": ["calendar"]})
        assert r.status_code == 410
        async with httpx.AsyncClient(base_url=op.url) as anon:
            assert (await anon.post("/broker/beta", json={"email": "a@example.com", "products": ["calendar"]})).status_code == 410
