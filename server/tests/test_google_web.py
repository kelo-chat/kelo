"""Gmail with a Google sign-in: the operator's server holds the client secret and renews tokens for other copies; the mailbox is used
over IMAP/SMTP with XOAUTH2. Against a pretend Google and a pretend mail server; nothing real is contacted."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from assistant.google_web import GoogleWeb, email_of
from assistant.mail import Mail
from assistant.netguard import Reply
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.mail_tools import mail_tools

from test_mail import FakeMailServer
from test_server import AUTH, TOKEN, Running, scripted

CLIENT = "1234567890-abc.apps.googleusercontent.com"
SECRET = "GOCSPX-abcdefghijklmnopqrstuvwx"
RELAY = "https://kelo.example.org/oauth/relay"
ADDRESS = "nahyun.kim@gmail.test"


def j(obj, status=200):
    return Reply(status, {"content-type": "application/json"}, json.dumps(obj).encode(), False)


def id_token(email):
    pay = base64.urlsafe_b64encode(json.dumps({"email": email, "email_verified": True}).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJSUzI1NiJ9.{pay}.sig"


class FakeGoogle:
    def __init__(self):
        self.challenge = ""
        self.calls = []

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None):
        assert urlsplit(url).hostname in allowed_hosts
        if url == "https://oauth2.googleapis.com/token":
            f = parse_qs(content.decode())
            self.calls.append(f)
            if f["client_id"] != [CLIENT] or f["client_secret"] != [SECRET]:
                return j({"error": "invalid_client"}, 401)
            if f["grant_type"] == ["authorization_code"]:
                ok = f["code"] == ["g-code"] and f["redirect_uri"] == [RELAY] and \
                    base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"][0].encode()).digest()).rstrip(b"=").decode() == self.challenge
                return j({"access_token": "ya29.first", "refresh_token": "1//refresh", "expires_in": 3599, "id_token": id_token(ADDRESS),
                          "scope": "openid email https://mail.google.com/"} if ok else {"error": "invalid_grant"}, 200 if ok else 400)
            if f["grant_type"] == ["refresh_token"] and f["refresh_token"] == ["1//refresh"]:
                return j({"access_token": "ya29.renewed", "expires_in": 3599})
            return j({"error": "invalid_grant"}, 400)
        return j({}, 404)


class XoauthMail(FakeMailServer):
    """The pretend mail server accepts only Google tokens it issued."""
    def __init__(self):
        super().__init__()
        self.tokens = []

    def imap(self, host):
        conn = super().imap(host)
        server = self

        def authenticate(mech, cb):
            raw = cb(b"").decode()
            server.tokens.append(raw)
            if mech != "XOAUTH2" or f"user={ADDRESS}\x01auth=Bearer ya29." not in raw:
                import imaplib
                raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED]")
            return "OK", [b"ok"]
        conn.authenticate = authenticate
        return conn


def app_with(tmp_path, g, m):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), oauth_relay=RELAY,
                               public_url="https://kelo.example.org"),
                      tools=None, stream_fn=scripted(), mcp_fetcher=g.fetch, mail_imap=m.imap, mail_smtp=m.smtp)


async def test_the_operator_sets_the_google_app_and_gmail_connects_with_a_sign_in(tmp_path):
    g, m = FakeGoogle(), XoauthMail()
    async with Running(app_with(tmp_path, g, m)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/mail/providers")).json()["google_web"] is False
        assert (await c.put("/api/oauth-apps/google", json={"client_id": "not-a-google-id"})).status_code == 422
        app = (await c.put("/api/oauth-apps/google", json={"client_id": CLIENT, "client_secret": SECRET})).json()
        assert app["is_broker"] and app["has_secret"] and SECRET not in json.dumps(app)
        # set up: the operator's own copy signs in right away (its owner is the client's test user); other copies wait for the
        # review through the broker's switch (test_google_review)
        assert (await c.get("/api/mail/providers")).json()["google_web"] is True
        gmail = next(x for x in (await c.get("/api/connectors")).json() if x["id"] == "gmail")
        assert "connect_state" not in gmail
        assert (await c.put("/api/oauth-apps/google/open", json={"open": True})).json()["open_to_all"] is True
        assert (await c.get("/api/mail/providers")).json()["google_web"] is True
        url = (await c.post("/api/google/web/start", json={"origin": "https://kelo.example.org"})).json()["url"]
        q = parse_qs(urlsplit(url).query)
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?") and q["redirect_uri"] == [RELAY]
        assert q["access_type"] == ["offline"] and "https://mail.google.com/" in q["scope"][0] and q["code_challenge_method"] == ["S256"]
        g.challenge = q["code_challenge"][0]
        done = await c.get("/oauth/callback", params={"state": q["state"][0], "code": "g-code"}, headers={"Accept-Language": "en"})
        assert "Gmail connected" in done.text, done.text
        acct = (await c.get("/api/mail/providers")).json()["accounts"][0]
        assert acct["name"] == "mail-gmail" and acct["address"] == ADDRESS
        rows = {x["id"]: x for x in (await c.get("/api/connectors")).json()}
        assert rows["gmail"]["status"] == "connected" and rows["gmail"]["method"] == "google" and rows["gmail"]["account"] == ADDRESS
        assert rows["imap_email"]["status"] == "available"                        # signing in Gmail does not connect "IMAP Email"
        assert "app password" not in rows["gmail"]["description"]
        everything = str((await c.get("/api/vault")).json()) + str((await c.get("/api/vault/log")).json())
        assert "ya29" not in everything and "1//refresh" not in everything
    st = Store(tmp_path / "s.db")
    raw = (tmp_path / "s.db").read_bytes()
    assert b"1//refresh" not in raw


async def test_the_mail_tools_use_the_token_and_renew_it_when_it_runs_out(tmp_path):
    g, m = FakeGoogle(), XoauthMail()
    st = Store(tmp_path / "s.db")
    gw = GoogleWeb(st, relay=RELAY, fetcher=g.fetch)
    gw.set_app(CLIENT, SECRET)
    mail = Mail(st, imap=m.imap, smtp=m.smtp, renew=gw.refresh)
    await mail.connect_oauth("gmail", ADDRESS, {"access_token": "ya29.first", "refresh_token": "1//refresh", "expires_in": 3599})
    tools = {t.name: t for t in mail_tools(mail)}
    assert "청구서" in await tools["email_list"].handler({})
    assert "Bearer ya29.first" in m.tokens[-1]
    data = json.loads(st.credential("mail-gmail")["secret"])
    data["oauth"]["expires_at"] = time.time() - 1                       # an hour has passed
    st.set_credential_secret("mail-gmail", json.dumps(data))
    assert "청구서" in await tools["email_list"].handler({})
    assert "Bearer ya29.renewed" in m.tokens[-1] and g.calls[-1]["grant_type"] == ["refresh_token"]
    assert json.loads(st.credential("mail-gmail")["secret"])["oauth"]["refresh_token"] == "1//refresh"


async def test_another_copy_renews_through_the_broker_without_the_secret(tmp_path):
    g, m = FakeGoogle(), XoauthMail()
    async with Running(app_with(tmp_path / "op", g, m)) as op, httpx.AsyncClient(base_url=op.url, headers=AUTH) as oc:
        await oc.put("/api/oauth-apps/google", json={"client_id": CLIENT, "client_secret": SECRET})
        await oc.put("/api/oauth-apps/google/open", json={"open": True})

        async def via_broker(method, url, *, allowed_hosts, headers=None, content=None):
            if url.startswith("https://kelo.example.org/broker/"):
                r = await httpx.AsyncClient(base_url=op.url).request(method, url.replace("https://kelo.example.org", ""), headers=headers, content=content)
                return Reply(r.status_code, dict(r.headers), r.content, False)
            return await g.fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content)

        st = Store(tmp_path / "friend.db")
        gw = GoogleWeb(st, relay=RELAY, broker="https://kelo.example.org", fetcher=via_broker)
        assert not gw.is_broker() and await gw.available() and await gw.client_id() == CLIENT
        assert (await gw.refresh("1//refresh"))["access_token"] == "ya29.renewed"
        url = await gw.start("https://friend.example.net")
        q = parse_qs(urlsplit(url).query)
        g.challenge = q["code_challenge"][0]
        tok, origin = await gw.exchange(q["state"][0], "g-code")
        assert origin == "https://friend.example.net" and tok["refresh_token"] == "1//refresh" and email_of(tok["id_token"]) == ADDRESS
        async with httpx.AsyncClient(base_url=op.url) as anon:
            assert (await anon.post("/broker/google/exchange", json={"code": "g-code", "redirect_uri": "https://evil.example/cb"})).status_code == 422


async def test_a_cancelled_sign_in_saves_nothing(tmp_path):
    g, m = FakeGoogle(), XoauthMail()
    async with Running(app_with(tmp_path, g, m)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/oauth-apps/google", json={"client_id": CLIENT, "client_secret": SECRET})
        await c.put("/api/oauth-apps/google/open", json={"open": True})
        url = (await c.post("/api/google/web/start", json={"origin": "https://kelo.example.org"})).json()["url"]
        state = parse_qs(urlsplit(url).query)["state"][0]
        assert "canceled" in (await c.get("/oauth/callback", params={"state": state, "error": "access_denied"})).text
        assert (await c.get("/api/mail/providers")).json()["accounts"] == []
