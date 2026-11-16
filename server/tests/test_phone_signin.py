"""Signing in to a subscription provider from the phone (providers/login_flows.py, cli_login.py, the sidecar's /login/...).
Only fakes: fake CLIs (small Python scripts that behave like `claude auth login` and `agy`), a fake OAuth service on
httpx.MockTransport, and the sidecar app in-process. No real CLI, sign-in service or model is contacted."""

import json
import sys
import textwrap

import httpx
import pytest

from assistant.cli_runner import create_app as create_runner
from assistant.providers import ProviderError, cli_login, cli_manage, cli_remote, oauth
from assistant.providers.login_flows import LoginFlows
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, scripted

RUNNER_TOKEN = "r" * 32
CLAUDE_URL = ("https://claude.com/cai/oauth/authorize?code=true&client_id=x&response_type=code"
              "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=user%3Ainference&state=S1")
AGY_URL = ("https://accounts.google.com/o/oauth2/auth?access_type=offline&client_id=y&redirect_uri=https%3A%2F%2F"
           "antigravity.google%2Foauth-callback&response_type=code&scope=openid&state=S2")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_OAUTH_FILE", str(tmp_path / "oauth.json"))
    monkeypatch.setattr(oauth, "_qwen_cli_path", lambda: tmp_path / "qwen" / "oauth_creds.json")
    for var in ("ASSISTANT_CLI_CLAUDE_URL", "ASSISTANT_CLI_AGY_URL", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLI_RUNNER_TOKEN", RUNNER_TOKEN)


# ---- fake CLIs

FAKE_CLAUDE = textwrap.dedent('''
    import sys
    print("Opening browser to sign in…")
    print("If the browser didn't open, visit: %s")
    sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "good-code#S1":
        open(sys.argv[1], "w").write("{}")      # what the CLI keeps as its login
        print("Login successful."); sys.exit(0)
    print("Login failed: That code wasn't accepted."); sys.exit(1)
''') % CLAUDE_URL

# like agy: needs a terminal, shows a menu, then the address wrapped over several lines and a code field
FAKE_AGY = textwrap.dedent('''
    import os, sys, time, tty
    assert os.isatty(0), "agy needs a terminal"
    tty.setraw(0)
    out = lambda s: (sys.stdout.write(s.replace("\\n", "\\r\\n")), sys.stdout.flush())
    out("\\x1b[?25l Welcome to the Antigravity CLI. You are currently not signed in.\\n\\n Select login method:\\n > 1. Google OAuth\\n")
    assert os.read(0, 1) == b"\\r"
    url = %r
    out("\\x1b[1m Signing in...\\x1b[0m Your browser should open automatically. If not:\\n\\n " + url[:60] + "\\n " + url[60:130] + "\\n " + url[130:] + "\\n\\n")
    out(" If you aren't automatically redirected, paste the authorization code below:\\n")
    code = b""
    while not code.endswith(b"\\r"):
        code += os.read(0, 64)
    if code.strip() == b"good-agy-code":
        os.makedirs(sys.argv[1], exist_ok=True)
        open(os.path.join(sys.argv[1], "antigravity-oauth-token"), "w").write("{}")
        out(" Signed in.\\n")
    else:
        out(" Got an error: token exchange failed\\n")
    time.sleep(30)                          # agy would go on to its chat screen: the driver must stop it
''') % AGY_URL


def fake_cli(tmp_path, provider):
    script = tmp_path / f"fake_{'claude' if provider == cli_manage.CLAUDE else 'agy'}.py"
    script.write_text(FAKE_CLAUDE if provider == cli_manage.CLAUDE else FAKE_AGY)
    target = str(tmp_path / ("claude-login.json" if provider == cli_manage.CLAUDE else "agy-home"))
    return cli_login.CliLogin(provider, command=[sys.executable, str(script), target],
                              token_dir=lambda: tmp_path / "agy-home", env=lambda: {"PATH": "/usr/bin:/bin"},
                              url_timeout=10, finish_timeout=10)


# ---- the address and the pasted code

def test_find_url_reads_hyperlinks_and_rejoins_wrapped_lines():
    assert cli_login.find_url(cli_manage.CLAUDE, f"visit: {CLAUDE_URL}\nPaste code here if prompted > ") == CLAUDE_URL
    assert cli_login.find_url(cli_manage.CLAUDE, f"visit: {CLAUDE_URL}") is None              # the line is not finished yet
    wrapped = f" {AGY_URL[:50]}\r\n {AGY_URL[50:]}\r\n\r\n Copy and paste the URL"
    assert cli_login.find_url(cli_manage.AGY, "\x1b[1m" + wrapped) == AGY_URL
    assert cli_login.find_url(cli_manage.AGY, f"\x1b]8;id=1;{AGY_URL}\x1b\\Click here\x1b]8;;\x1b\\") == AGY_URL
    assert cli_login.find_url(cli_manage.AGY, "https://evil.example/o/oauth2/auth?state=1\n\n") is None   # only Google's page


def test_normalize_code_takes_the_code_or_the_whole_address():
    assert cli_login.normalize_code(cli_manage.CLAUDE, "  abc#S1 ") == "abc#S1"
    assert cli_login.normalize_code(cli_manage.CLAUDE, "https://platform.claude.com/oauth/code/callback?code=abc&state=S1") == "abc#S1"
    assert cli_login.normalize_code(cli_manage.AGY, "https://antigravity.google/oauth-callback?code=4/0Ab&scope=x") == "4/0Ab"
    for bad in ("", "two words", "x" * 5000, "https://antigravity.google/oauth-callback?error=access_denied"):
        with pytest.raises(ProviderError) as e:
            cli_login.normalize_code(cli_manage.AGY, bad)
        assert e.value.status == 422


# ---- driving the CLIs

async def test_claude_login_hands_out_the_address_and_types_the_code(tmp_path):
    lg = fake_cli(tmp_path, cli_manage.CLAUDE)
    r = await lg.start()
    assert r["url"] == CLAUDE_URL and r["expires_in"] == 600 and len(r["session"]) > 16
    with pytest.raises(ProviderError) as e:
        await lg.finish(r["session"], "wrong#S1")
    assert e.value.status == 400 and "wrong" not in str(e.value)                              # the code is never echoed
    assert lg.status(r["session"])["state"] == "failed"
    r = await lg.start()
    with pytest.raises(ProviderError) as e:
        await lg.finish("not-the-session", "good-code#S1")
    assert e.value.status == 404
    done = await lg.finish(r["session"], "https://platform.claude.com/oauth/code/callback?code=good-code&state=S1")
    assert done["state"] == "done" and (tmp_path / "claude-login.json").exists()
    lg.close()


async def test_agy_login_runs_in_a_terminal_and_stops_the_cli_after_the_token(tmp_path):
    lg = fake_cli(tmp_path, cli_manage.AGY)
    r = await lg.start()
    assert r["url"] == AGY_URL
    proc = lg._session.p.proc
    with pytest.raises(ProviderError):
        await lg.finish(r["session"], "bad-code")
    r = await lg.start()
    proc = lg._session.p.proc
    assert (await lg.finish(r["session"], "good-agy-code"))["state"] == "done"
    assert (tmp_path / "agy-home" / "antigravity-oauth-token").exists()
    await proc.wait()                                                                          # stopped, not left on its chat screen
    with pytest.raises(ProviderError) as e:                                                    # signed in: agy is not opened again
        await lg.start()
    assert e.value.status == 409


async def test_a_started_sign_in_expires_and_can_be_cancelled(tmp_path):
    lg = fake_cli(tmp_path, cli_manage.CLAUDE)
    lg.ttl = 0.0
    r = await lg.start()
    assert lg.status(r["session"])["state"] == "expired"
    lg.ttl = 600
    r = await lg.start()
    assert await lg.cancel(r["session"]) == {"ok": True} and await lg.cancel(r["session"]) == {"ok": False}


# ---- the sidecar's endpoints and the server's client for them

class FakeCli:
    def __init__(self):
        self.codes = []

    async def start(self):
        return {"session": "side-1", "url": CLAUDE_URL, "expires_in": 600}

    async def finish(self, sid, code):
        self.codes.append(code)
        if code != "good#S1":
            raise ProviderError("The code was not accepted. It may be incomplete or expired. Start the sign-in again", status=400)
        return {"state": "done", "expires_in": 0}

    def status(self, sid):
        return {"state": "pending", "expires_in": 500}

    async def cancel(self, sid):
        return {"ok": True}


async def test_sidecar_endpoints_need_the_token_and_relay_errors(monkeypatch):
    fake = FakeCli()
    app = create_runner("claude", RUNNER_TOKEN, login=fake)
    async with Running(app) as side:
        monkeypatch.setenv("ASSISTANT_CLI_CLAUDE_URL", side.url)
        async with httpx.AsyncClient(base_url=side.url) as c:
            assert (await c.post("/login/start")).status_code == 401
        assert (await cli_remote.login_start(cli_manage.CLAUDE))["url"] == CLAUDE_URL
        with pytest.raises(ProviderError) as e:
            await cli_remote.login_finish(cli_manage.CLAUDE, "side-1", "bad#S1")
        assert e.value.status == 400 and "not accepted" in str(e.value)
        assert (await cli_remote.login_finish(cli_manage.CLAUDE, "side-1", "good#S1"))["state"] == "done"
        assert (await cli_remote.login_status(cli_manage.CLAUDE, "side-1"))["state"] == "pending"
    with pytest.raises(cli_remote.SidecarOff) as e:                                            # the container is off now
        await cli_remote.login_start(cli_manage.CLAUDE)
    assert e.value.status == 503 and "assistant enable oauth" in str(e.value)


# ---- the server's endpoints

def oauth_service(log):
    """A fake device-flow service for Codex, Qwen and MiniMax."""
    polls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        log.append(str(req.url))
        path = req.url.path
        if path.endswith("/deviceauth/usercode"):
            return httpx.Response(200, json={"device_auth_id": "dev-secret", "user_code": "ABCD-1234", "interval": "5"})
        if path.endswith("/deviceauth/token"):
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(403, json={})
            return httpx.Response(200, json={"authorization_code": "ac", "code_verifier": "cv"})
        if path == "/oauth/token" and req.url.host == "auth.openai.com":
            return httpx.Response(200, json={"access_token": "codex-access-token", "refresh_token": "rt"})
        if path.endswith("/device/code"):
            return httpx.Response(200, json={"device_code": "dc", "user_code": "QW-1", "verification_uri": "https://chat.qwen.ai/authorize",
                                             "verification_uri_complete": "https://chat.qwen.ai/authorize?user_code=QW-1", "expires_in": 600})
        if path.endswith("/oauth2/token"):
            return httpx.Response(400, json={"error": "access_denied"})
        return httpx.Response(404)
    return handler


def app_for(tmp_path, flows, unofficial=True):
    return create_app(Settings(token=TOKEN, provider="openai", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                               unofficial_providers=unofficial),
                      tools=[], stream_fn=scripted(), login_flows=flows)


async def test_device_code_sign_in_end_to_end(tmp_path):
    log = []
    now = [1000.0]
    service = httpx.MockTransport(oauth_service(log))
    flows = LoginFlows(http=lambda: httpx.AsyncClient(transport=service), now=lambda: now[0])
    async with Running(app_for(tmp_path, flows)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == "openai-codex")
        assert row["signin"] is True and row["signed_in"] is False
        r = await c.post("/api/providers/openai-codex/login/start", json={})
        assert r.status_code == 428 and r.json()["detail"] == "consent_required:openai-codex"   # the notice comes first
        assert (await c.put("/api/providers/openai-codex/consent", json={"accepted": True})).status_code == 200
        r = (await c.post("/api/providers/openai-codex/login/start", json={})).json()
        assert r["kind"] == "device_code" and r["url"] == "https://auth.openai.com/codex/device" and r["user_code"] == "ABCD-1234"
        assert r["state"] == "pending" and r["expires_in"] == 900 and "dev-secret" not in json.dumps(r)
        sid = r["session"]
        st = (await c.get(f"/api/providers/openai-codex/login/status?session={sid}")).json()
        assert st["state"] == "pending" and not any("deviceauth/token" in u for u in log)    # asked too early: no call
        now[0] += 6
        assert (await c.get(f"/api/providers/openai-codex/login/status?session={sid}")).json()["state"] == "pending"
        now[0] += 6
        st = (await c.get(f"/api/providers/openai-codex/login/status?session={sid}")).json()
        assert st["state"] == "done" and "codex-access-token" not in json.dumps(st)
        assert oauth.status()["openai-codex"]["logged_in"] is True
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == "openai-codex")
        assert row["signed_in"] is True
        r = await c.post("/api/providers/openai-codex/login/start", json={})
        assert r.status_code == 409 and r.json()["detail"].startswith("already_signed_in")
        assert (await c.get("/api/providers/openai-codex/login/status?session=nope")).status_code == 404
        assert (await c.post("/api/providers/openai/login/start", json={})).status_code == 400   # an API-key provider

        # Qwen: the address carries the code; a refusal ends the sign-in with a readable state
        assert (await c.put("/api/providers/qwen-oauth/consent", json={"accepted": True})).status_code == 200
        r = (await c.post("/api/providers/qwen-oauth/login/start", json={})).json()
        assert r["url"].endswith("user_code=QW-1") and r["user_code"] == "QW-1"
        st = (await c.post("/api/providers/qwen-oauth/login/finish", json={"session": r["session"]})).json()   # "I approved it"
        assert st["state"] == "failed" and st["message"]
    assert oauth.status()["qwen-oauth"]["logged_in"] is False


async def test_cli_sign_in_through_the_sidecar(tmp_path, monkeypatch):
    fake = FakeCli()
    state = {"logged_in": False}

    async def fake_status(name, *, local=False):
        return {"installed": True, "installable": False, "logged_in": state["logged_in"], "account": "me@x.y" if state["logged_in"] else None}

    monkeypatch.setattr(cli_manage, "cli_status", fake_status)
    async with Running(create_runner("claude", RUNNER_TOKEN, login=fake)) as side:
        monkeypatch.setenv("ASSISTANT_CLI_CLAUDE_URL", side.url)
        async with Running(app_for(tmp_path, LoginFlows())) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            assert (await c.put("/api/providers/claude-cli/consent", json={"accepted": True})).status_code == 200
            r = (await c.post("/api/providers/claude-cli/login/start", json={})).json()
            assert r["kind"] == "url_code" and r["url"] == CLAUDE_URL and "user_code" not in r and r["session"] != "side-1"
            bad = await c.post("/api/providers/claude-cli/login/finish", json={"session": r["session"], "code": "two words"})
            assert bad.status_code == 422 and fake.codes == []                                 # checked before it leaves the server
            bad = await c.post("/api/providers/claude-cli/login/finish", json={"session": r["session"], "code": "bad#S1"})
            assert bad.status_code == 400 and "not accepted" in bad.json()["detail"] and "bad#S1" not in bad.text
            state["logged_in"] = True
            ok = await c.post("/api/providers/claude-cli/login/finish",
                              json={"session": r["session"], "redirect_url": "https://platform.claude.com/oauth/code/callback?code=good&state=S1"})
            assert ok.status_code == 200 and ok.json()["state"] == "done" and ok.json()["account"] == "me@x.y"
            assert fake.codes == ["bad#S1", "good#S1"]
    monkeypatch.delenv("ASSISTANT_CLI_CLAUDE_URL")


async def test_the_container_being_off_is_explained(tmp_path, monkeypatch):
    async def fake_status(name, *, local=False):
        return {"installed": False, "installable": False, "logged_in": None}

    monkeypatch.setattr(cli_manage, "cli_status", fake_status)
    monkeypatch.setenv("ASSISTANT_CLI_AGY_URL", "http://127.0.0.1:9")                          # nothing listens there
    async with Running(app_for(tmp_path, LoginFlows())) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/providers/agy/consent", json={"accepted": True})).status_code == 200
        r = await c.post("/api/providers/agy/login/start", json={})
        assert r.status_code == 503 and "assistant enable oauth" in r.json()["detail"] and "Antigravity" in r.json()["detail"]


async def test_sign_in_is_refused_while_subscription_sign_ins_are_off(tmp_path):
    async with Running(app_for(tmp_path, LoginFlows(), unofficial=False)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/providers/openai-codex/login/start", json={})).status_code == 403


async def test_a_blocked_or_odd_answer_from_the_sign_in_service_is_readable():
    # e.g. a web firewall page (HTML) instead of JSON, as chat.qwen.ai sometimes sends to servers
    page = httpx.MockTransport(lambda req: httpx.Response(200, text="<!doctype html><script></script>",
                                                          headers={"content-type": "text/html"}))
    flows = LoginFlows(http=lambda: httpx.AsyncClient(transport=page))
    with pytest.raises(ProviderError) as e:
        await flows.start("qwen-oauth")
    assert e.value.status == 502 and "does not understand" in str(e.value)
