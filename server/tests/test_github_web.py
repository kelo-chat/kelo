"""GitHub with a sign-in button: the operator's server holds the app secret (the broker); other copies sign in through it. Against a
pretend GitHub; no real service is contacted."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx

from assistant.github_web import GitHubWeb
from assistant.netguard import Reply
from assistant.oauth_connect import DeviceFlows
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running, scripted

CLIENT = "Ov23liKeloApp123456"
SECRET = "0123456789abcdef0123456789abcdef01234567"
RELAY = "https://kelo.example.org/oauth/relay"


def j(obj, status=200):
    return Reply(status, {"content-type": "application/json"}, json.dumps(obj).encode(), False)


class FakeGitHub:
    def __init__(self):
        self.challenge = ""
        self.token_calls: list[dict] = []

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None):
        host = urlsplit(url).hostname
        assert host in allowed_hosts
        if url == "https://github.com/login/oauth/access_token":
            form = parse_qs(content.decode())
            self.token_calls.append(form)
            ok = (form["client_id"] == [CLIENT] and form["client_secret"] == [SECRET] and form["code"] == ["gh-code"] and form["redirect_uri"] == [RELAY]
                  and base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest()).rstrip(b"=").decode() == self.challenge)
            return j({"access_token": "gho_web_token_1", "scope": "repo,read:user", "token_type": "bearer"} if ok else {"error": "bad_verification_code"})
        return j({}, 404)

    async def whoami(self, url, token):
        return {"login": "octocat"} if token == "gho_web_token_1" else {}


def broker_app(tmp_path, fake):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), oauth_relay=RELAY,
                               public_url="https://kelo.example.org"),
                      tools=[], stream_fn=scripted(), mcp_fetcher=fake.fetch, device_get=fake.whoami)


async def test_the_operator_sets_the_app_once_and_signs_in_with_a_button(tmp_path):
    fake = FakeGitHub()
    async with Running(broker_app(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/oauth/providers")).json()[0]["web"] is False                 # nothing set up yet
        assert (await c.put("/api/oauth-apps/github", json={"client_secret": "short"})).status_code == 422
        app = (await c.put("/api/oauth-apps/github", json={"client_id": CLIENT, "client_secret": SECRET})).json()
        assert app == {"client_id": CLIENT, "has_secret": True, "is_broker": True, "callback": RELAY, "broker": ""}
        assert SECRET not in json.dumps(app) and SECRET not in (await c.get("/api/oauth-apps/github")).text
        assert (await c.get("/api/oauth/providers")).json()[0]["web"] is True
        url = (await c.post("/api/github/web/start", json={"origin": "https://kelo.example.org", "scope": "public"})).json()["url"]
        q = parse_qs(urlsplit(url).query)
        assert url.startswith("https://github.com/login/oauth/authorize?") and q["client_id"] == [CLIENT] and q["redirect_uri"] == [RELAY]
        assert q["scope"] == ["public_repo read:user"] and q["code_challenge_method"] == ["S256"] and "client_secret" not in url
        fake.challenge = q["code_challenge"][0]
        hop = await c.get("/oauth/relay", params={"state": q["state"][0], "code": "gh-code"}, follow_redirects=False)
        assert hop.headers["location"].startswith("https://kelo.example.org/oauth/callback?")
        done = await c.get("/oauth/callback", params={"state": q["state"][0], "code": "gh-code"})
        assert "GitHub connected" in done.text
        item = next(i for i in (await c.get("/api/vault")).json()["items"] if i["name"] == "github-api")
        assert item["label"] == "GitHub · @octocat" and item["hosts"] == ["api.github.com"]
        assert "gho_web_token_1" not in str((await c.get("/api/vault")).json())


async def test_another_copy_signs_in_through_the_operators_broker_and_keeps_the_token_itself(tmp_path):
    fake = FakeGitHub()
    async with Running(broker_app(tmp_path / "op", fake)) as op, httpx.AsyncClient(base_url=op.url, headers=AUTH) as oc:
        await oc.put("/api/oauth-apps/github", json={"client_id": CLIENT, "client_secret": SECRET})
        # the other person's copy: no secret, no client id of its own; it knows only the relay and the broker
        async def via_broker(method, url, *, allowed_hosts, headers=None, content=None):
            if url.startswith("https://kelo.example.org/broker/"):
                r = await httpx.AsyncClient(base_url=op.url).request(method, url.replace("https://kelo.example.org", ""), headers=headers, content=content)
                return Reply(r.status_code, dict(r.headers), r.content, False)
            return await fake.fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content)
        st = Store(tmp_path / "friend.db")
        flows = DeviceFlows(st, get_json=fake.whoami)
        gh = GitHubWeb(st, flows, relay=RELAY, broker="https://kelo.example.org", fetcher=via_broker)
        assert not gh.is_broker() and await gh.available() and await gh.client_id() == CLIENT
        url = await gh.start("https://friend.trycloudflare.com")
        q = parse_qs(urlsplit(url).query)
        fake.challenge = q["code_challenge"][0]
        origin, problem = await gh.finish(q["state"][0], "gh-code")
        assert (origin, problem) == ("https://friend.trycloudflare.com", "")
        assert st.credential("github-api")["secret"] == "gho_web_token_1"            # the friend's copy holds its own token
        assert not (await oc.get("/api/vault")).json()["items"]                         # the broker kept nothing


async def test_the_broker_refuses_when_it_has_no_secret_another_return_address_or_too_many_requests(tmp_path):
    fake = FakeGitHub()
    async with Running(broker_app(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/broker/github/client")).status_code == 404
        assert (await c.post("/broker/github/exchange", json={"code": "gh-code", "redirect_uri": RELAY})).status_code == 404
        await c.put("/api/oauth-apps/github", json={"client_id": CLIENT, "client_secret": SECRET}, headers=AUTH)
        assert (await c.get("/broker/github/client")).json() == {"client_id": CLIENT}
        assert (await c.post("/broker/github/exchange", json={"code": "gh-code", "redirect_uri": "https://evil.example/cb"})).status_code == 422
        codes = [(await c.post("/broker/github/exchange", json={"code": "gh-code", "redirect_uri": RELAY, "code_verifier": "x"})).status_code for _ in range(22)]
        assert codes[0] == 200 and codes[-1] == 429
        assert (await c.put("/api/oauth-apps/github", json={"client_secret": SECRET})).status_code == 401   # settings need the owner's token


async def test_a_refused_or_cancelled_sign_in_saves_nothing(tmp_path):
    fake = FakeGitHub()
    async with Running(broker_app(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/oauth-apps/github", json={"client_id": CLIENT, "client_secret": SECRET})
        url = (await c.post("/api/github/web/start", json={"origin": "https://kelo.example.org"})).json()["url"]
        state = parse_qs(urlsplit(url).query)["state"][0]
        assert "canceled" in (await c.get("/oauth/callback", params={"state": state, "error": "access_denied"})).text
        url = (await c.post("/api/github/web/start", json={"origin": "https://kelo.example.org"})).json()["url"]
        state = parse_qs(urlsplit(url).query)["state"][0]                               # the challenge does not match: the code is useless
        assert "did not give a token" in (await c.get("/oauth/callback", params={"state": state, "code": "gh-code"})).text
        assert (await c.get("/api/vault")).json()["items"] == []


async def test_without_a_relay_the_button_is_not_offered(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/oauth-apps/github", json={"client_id": CLIENT, "client_secret": SECRET})
        assert (await c.get("/api/oauth/providers")).json()[0]["web"] is False
        assert (await c.post("/api/github/web/start", json={"origin": "https://x.example"})).status_code == 422
