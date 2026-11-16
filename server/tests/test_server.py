import asyncio
import json
import socket

import httpx
import pytest
import uvicorn

from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall, Usage
from assistant.server import Settings, create_app

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Running:
    def __init__(self, app):
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.url = f"http://127.0.0.1:{self.port}"

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task


def scripted(*turns):
    it = iter(turns)

    async def fn(provider, request, **kw):
        for ev in next(it):
            if isinstance(ev, Exception):
                raise ev
            yield ev
    return fn


def settings(**kw):
    return Settings(token=TOKEN, provider="p", model="m", cdp_host=None, **kw)


async def read_events(response, reactions=False):
    """The reply's events (without the reactions on the user's message unless asked: test_reactions.py covers those)."""
    return [e for e in [json.loads(line[6:]) async for line in response.aiter_lines() if line.startswith("data: ")]
            if reactions or e.get("type") != "reaction"]


BODY = {"messages": [{"role": "user", "content": "hi"}]}


async def test_auth_and_validation():
    app = create_app(settings(), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/healthz")).json() == {"ok": True}
        assert (await c.post("/api/chat", json=BODY)).status_code == 401
        assert (await c.post("/api/chat", json=BODY, headers={"Authorization": "Bearer nope"})).status_code == 401
        assert (await c.post("/api/chat", json=BODY, headers={"Authorization": f"Basic {TOKEN}"})).status_code == 401
        assert (await c.post("/api/approvals/x", json={"approve": True})).status_code == 401
        assert (await c.post("/api/approvals/x", json={"approve": True}, headers=AUTH)).status_code == 404
        assert (await c.post("/api/chat", json={"messages": []}, headers=AUTH)).status_code == 422
        s = Settings(token=TOKEN, cdp_host=None)  # no provider/model configured
        app2 = create_app(s, tools=[], stream_fn=scripted())
    async with Running(app2) as srv2, httpx.AsyncClient(base_url=srv2.url) as c:
        assert (await c.post("/api/chat", json=BODY, headers=AUTH)).status_code == 400


async def test_chat_streams_text_and_done_and_adds_system_prompt():
    seen = {}

    async def fn(provider, request, **kw):
        seen["provider"], seen["model"], seen["messages"] = provider, request.model, request.messages
        yield TextDelta("Hel"); yield TextDelta("lo"); yield Usage(3, 2); yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            events = await read_events(r)
    assert [e["type"] for e in events] == ["text", "text", "done"]
    assert events[-1] == {"type": "done", "input_tokens": 3, "output_tokens": 2, "steps": 1}
    assert (seen["provider"], seen["model"]) == ("p", "m")
    assert seen["messages"][0]["role"] == "system" and "UNTRUSTED CONTENT" in seen["messages"][0]["content"]


def danger_tool(ran):
    async def handler(args):
        ran.append(args)
        return "done"
    return Tool("send", "send", {"type": "object"}, handler, requires_approval=True)


@pytest.mark.parametrize("approve", [True, False])
async def test_approval_roundtrip_over_http(approve):
    ran = []
    fn = scripted([ToolCall("c1", "send", '{"to":"bob"}'), Done("tool_calls")], [TextDelta("ok"), Done("stop")])
    async with Running(create_app(settings(), tools=[danger_tool(ran)], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url) as c:
        events = []
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                events.append(ev)
                if ev["type"] == "approval_request":
                    assert ev["name"] == "send" and ev["arguments"] == {"to": "bob"}
                    resp = await c.post(f"/api/approvals/{ev['approval_id']}", json={"approve": approve}, headers=AUTH)
                    assert resp.status_code == 200
    kinds = [e["type"] for e in events]
    assert kinds == ["tool_started", "approval_request", "tool_result", "text", "done"]
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["approved"] is approve and result["is_error"] is (not approve)
    assert ran == ([{"to": "bob"}] if approve else [])


async def test_unanswered_approval_times_out_as_denied(monkeypatch):
    monkeypatch.setattr("assistant.server.APPROVAL_TIMEOUT_SECONDS", 0.2)
    ran = []
    fn = scripted([ToolCall("c1", "send", "{}")], [TextDelta("ok"), Done("stop")])
    async with Running(create_app(settings(), tools=[danger_tool(ran)], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    assert ran == [] and next(e for e in events if e["type"] == "tool_result")["approved"] is False


async def test_provider_failure_becomes_error_event_not_a_crash():
    fn = scripted([RuntimeError("boom sk-SECRET")])
    async with Running(create_app(settings(), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
        assert (await c.get("/healthz")).status_code == 200  # server still alive
    # the user's words in the chat; the exception only in the detail (the log, the task's record)
    assert events[-1]["type"] == "error" and "RuntimeError" not in events[-1]["message"] and "RuntimeError" in events[-1]["detail"]


def test_settings_from_env_requires_a_real_token():
    with pytest.raises(RuntimeError, match="ASSISTANT_TOKEN"):
        Settings.from_env({})
    with pytest.raises(RuntimeError):
        Settings.from_env({"ASSISTANT_TOKEN": "short"})
    s = Settings.from_env({"ASSISTANT_TOKEN": "x" * 20, "ASSISTANT_APPROVE_BROWSER_ACTIONS": "0", "BROWSER_CDP_HOST": ""})
    assert s.approve_browser_actions is False and s.cdp_host is None and s.allow_private_browsing is False
    assert Settings.from_env({"ASSISTANT_TOKEN": "x" * 20}).approve_browser_actions is False   # default: only committing steps ask
    assert Settings.from_env({"ASSISTANT_TOKEN": "x" * 20, "ASSISTANT_APPROVE_BROWSER_ACTIONS": "1"}).approve_browser_actions is True


# ---- app API: profile / talk / goals / connectors / static

async def test_talk_persists_history_usage_and_persona():
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request.messages)
        yield TextDelta("안녕"); yield TextDelta("하세요"); yield Usage(5, 7); yield Done("stop")

    app = create_app(settings(), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"user_name": "홍길동", "agent_name": "Pip"})
        await c.post("/api/goals", json={"title": "매일 운동", "category": "건강", "kickoff": False})
        for text in ("하나", "둘"):
            async with c.stream("POST", "/api/talk", json={"text": text}) as r:
                await read_events(r)
        msgs = (await c.get("/api/messages")).json()
        usage = (await c.get("/api/usage")).json()
        cleared = (await c.delete("/api/messages")).json()
        assert cleared["ok"] is True and cleared["cleared"]["messages"] == 4
        assert (await c.get("/api/messages")).json() == []
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "하나"), ("assistant", "안녕하세요"), ("user", "둘"), ("assistant", "안녕하세요")]
    assert usage["today"] == {"input_tokens": 10, "output_tokens": 14}
    from assistant.server import TURN_NOTE
    turn = seen[1][-2]["content"]                                   # this turn's lines: a note right before the user's message
    assert turn.startswith(TURN_NOTE) and "Pip" in turn and "홍길동" in turn and "매일 운동" in turn
    assert seen[1][0]["content"] == seen[0][0]["content"]           # the system text itself is the same every turn
    assert [m["content"] for m in seen[1][1:] if not m["content"].startswith(TURN_NOTE)] == ["하나", "안녕하세요", "둘"]  # history replayed


async def test_profile_provider_model_used_when_env_unset():
    seen = {}

    async def fn(provider, request, **kw):
        seen["p"], seen["m"] = provider, request.model
        yield TextDelta("x"); yield Done("stop")

    app = create_app(Settings(token=TOKEN, cdp_host=None), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/talk", json={"text": "hi"})).status_code == 400
        await c.put("/api/profile", json={"provider": "openrouter", "model": "some/model"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            await read_events(r)
    assert seen == {"p": "openrouter", "m": "some/model"}


async def test_goals_crud_and_validation():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/goals")).status_code == 401
        c.headers.update(AUTH)
        g = (await c.post("/api/goals", json={"title": "책 읽기", "category": "관심사"})).json()
        assert g["status"] == "tracking"
        assert (await c.patch(f"/api/goals/{g['id']}", json={"status": "done", "note": "끝"})).json()["status"] == "done"
        assert (await c.patch(f"/api/goals/{g['id']}", json={"status": "bogus"})).status_code == 422
        assert (await c.patch("/api/goals/999", json={"note": "x"})).status_code == 404
        assert (await c.post("/api/goals", json={"title": ""})).status_code == 422
        assert (await c.delete(f"/api/goals/{g['id']}")).status_code == 200
        assert (await c.delete(f"/api/goals/{g['id']}")).status_code == 404


async def test_connectors_are_honest_about_what_works():
    async with Running(create_app(settings(unofficial_providers=True), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        by_id = {x["id"]: x for x in (await c.get("/api/connectors")).json()}
        provs = (await c.get("/api/providers")).json()
    assert "browser" not in by_id                                                                 # the browser is part of the app, not a connector
    assert by_id["gmail"]["status"] == "available" and by_id["gmail"]["connect"] == "mail"     # an app password, checked before saving
    assert by_id["slack"]["status"] == "available" and by_id["slack"]["connect"] == "key"      # a token the user makes on Slack
    assert any(p["name"] == "openai-codex" and p["oauth"] for p in provs)


async def test_no_web_app_only_a_page_that_opens_the_phone_app(tmp_path):
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        home = await c.get("/")
        assert home.status_code == 200 and "package=chat.kelo.app" in home.text and 'id="pair"' in home.text   # the website (test_website.py)
        assert (await c.get("/goals")).status_code == 404 and (await c.get("/sw.js")).status_code == 404
        assert (await c.get("/api/goals")).status_code == 401
        assert (await c.get("/api/nope", headers=AUTH)).status_code == 404


def test_store_file_is_private(tmp_path):
    from assistant.store import Store
    Store(tmp_path / "sub" / "a.db")
    assert oct((tmp_path / "sub" / "a.db").stat().st_mode & 0o777) == "0o600"


async def test_failed_auth_is_rate_limited_per_client(monkeypatch):
    monkeypatch.setattr("assistant.api.auth.AUTH_FAIL_LIMIT", 3)
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        bad = {"Authorization": "Bearer nope"}
        codes = [(await c.get("/api/goals", headers={**bad, "CF-Connecting-IP": "1.1.1.1"})).status_code for _ in range(5)]
        assert codes == [401, 401, 401, 429, 429]
        # the locked-out client is refused even with the right token; others are unaffected
        assert (await c.get("/api/goals", headers={**AUTH, "CF-Connecting-IP": "1.1.1.1"})).status_code == 429
        assert (await c.get("/api/goals", headers={**AUTH, "CF-Connecting-IP": "2.2.2.2"})).status_code == 200


async def test_models_listing_uses_configured_credentials_and_degrades_gracefully(monkeypatch):
    from assistant.models import _cache
    _cache.clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    calls = []

    def handler(req):
        calls.append((str(req.url), req.headers.get("authorization")))
        return httpx.Response(200, json={"data": [{"id": "b/model"}, {"id": "a/model", "context_length": 64000},
                                                  {"id": "models/a/model"}, {"id": "google/gemini-3.8-flash"}]})

    mc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(settings(), tools=[], stream_fn=scripted(), models_client=mc)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = (await c.get("/api/models", params={"provider": "openrouter"})).json()
        assert r["models"] == ["a/model", "b/model", "google/gemini-3.8-flash"] and r["error"] is None
        # what the app's model list may say: the provider's own window, the published table's, never the default guess
        assert r["info"]["a/model"]["context"] == 64000 and r["info"]["b/model"]["context"] is None
        assert r["info"]["google/gemini-3.8-flash"]["context"] == 1048576
        assert r["info"]["a/model"]["images"] is False   # OpenRouter is treated as text-only (providers/vision.py)
        assert calls[0][0].endswith("/models") and calls[0][1] == "Bearer sk-test"
        await c.get("/api/models", params={"provider": "openrouter"})
        assert len(calls) == 1  # cached
        assert (await c.get("/api/models", params={"provider": "nope"})).status_code == 404
        monkeypatch.delenv("OPENROUTER_API_KEY")
        _cache.clear()
        r = (await c.get("/api/models", params={"provider": "openrouter"})).json()
        assert r["models"] == [] and "No API key" in r["error"]
        assert len(calls) == 1  # no request without a key
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert provs["openrouter"]["configured"] is False


async def test_api_key_entered_in_app_is_used_hidden_and_removable(monkeypatch):
    from assistant.models import _cache
    _cache.clear()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    seen = {}

    async def fn(provider, request, **kw):
        seen["api_key"] = kw.get("api_key")
        yield TextDelta("x"); yield Done("stop")

    calls = []
    mc = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: calls.append(req.headers.get("authorization")) or httpx.Response(200, json={"data": [{"id": "m1"}]})))
    app = create_app(settings(), tools=[], stream_fn=fn, models_client=mc)
    secret = "sk-or-SECRETVALUE1234"
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.put("/api/providers/openrouter/key", json={"api_key": secret})).status_code == 401
        c.headers.update(AUTH)
        assert (await c.put("/api/providers/openrouter/key", json={"api_key": "has space 12345"})).status_code == 422
        assert (await c.put("/api/providers/openrouter/key", json={"api_key": "bad\nkey12345"})).status_code == 422
        assert (await c.put("/api/providers/openai-codex/key", json={"api_key": secret})).status_code == 400  # OAuth provider
        assert (await c.put("/api/providers/nope/key", json={"api_key": secret})).status_code == 404

        assert (await c.put("/api/providers/openrouter/key", json={"api_key": secret})).json() == {"ok": True}
        listing = await c.get("/api/providers")
        assert secret not in listing.text  # write-only
        p = next(x for x in listing.json() if x["name"] == "openrouter")
        assert p["configured"] and p["key_source"] == "app" and p["key_hint"] == "1234" and p["takes_key"]

        assert (await c.get("/api/models", params={"provider": "openrouter"})).json()["models"] == ["m1"]
        assert calls == [f"Bearer {secret}"]
        await c.put("/api/profile", json={"provider": "openrouter", "model": "m1"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            await read_events(r)
        assert seen["api_key"] == secret

        assert (await c.delete("/api/providers/openrouter/key")).status_code == 200
        assert (await c.delete("/api/providers/openrouter/key")).status_code == 404
        p = next(x for x in (await c.get("/api/providers")).json() if x["name"] == "openrouter")
        assert p["configured"] is False and p["key_source"] is None
        assert (await c.get("/api/models", params={"provider": "openrouter"})).json()["error"]  # cache cleared, no key


def test_stored_api_keys_are_encrypted_at_rest(tmp_path):
    import sqlite3
    from cryptography.fernet import Fernet
    from assistant.store import Store

    db = tmp_path / "s" / "a.db"
    st = Store(db)
    st.set_key("openrouter", "sk-PLAINTEXT-SECRET")
    assert (tmp_path / "s" / "master.key").stat().st_mode & 0o777 == 0o600
    raw = sqlite3.connect(db).execute("select key from api_keys").fetchone()[0]
    assert "PLAINTEXT" not in raw and b"PLAINTEXT" not in db.read_bytes()
    assert Store(db).key("openrouter") == "sk-PLAINTEXT-SECRET"  # survives restart via the key file

    # a copy of the DB without the master key is useless; with a different key the key reads as unset
    other = tmp_path / "copy.db"
    other.write_bytes(db.read_bytes())
    assert Store(other).key("openrouter") is None
    # explicit env key keeps the master key off the data dir
    k = Fernet.generate_key().decode()
    st2 = Store(tmp_path / "e" / "b.db", k)
    st2.set_key("openrouter", "abc12345")
    assert not (tmp_path / "e" / "master.key").exists() and Store(tmp_path / "e" / "b.db", k).key("openrouter") == "abc12345"
    with pytest.raises(RuntimeError, match="Fernet"):
        Store(tmp_path / "f.db", "not-a-key")


async def test_cli_provider_status_install_guard_and_static_models(monkeypatch):
    from assistant.providers import cli_manage

    async def fake_status(name):
        return {"installed": name != "antigravity-oauth", "installable": name == "antigravity-oauth",
                "logged_in": None, "hint": "h"}

    async def fake_install():
        return "installed ok"

    monkeypatch.setattr(cli_manage, "cli_status", fake_status)
    monkeypatch.setattr(cli_manage, "install_agy", fake_install)
    async with Running(create_app(settings(unofficial_providers=True), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.post("/api/providers/antigravity-oauth/install")).status_code == 401
        c.headers.update(AUTH)
        by = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        claude, agy = by["claude-subscription-directsdk-experimental"], by["antigravity-oauth"]
        assert claude["configured"] is True and claude["cli"]["installed"]
        assert agy["configured"] is False and agy["cli"]["installable"]
        assert (await c.post("/api/providers/antigravity-oauth/install")).json() == {"ok": True, "output": "installed ok"}
        assert (await c.post("/api/providers/openrouter/install")).status_code == 400  # nothing else is installable
        assert (await c.post("/api/providers/claude-subscription-directsdk-experimental/install")).status_code == 400
        ms = (await c.get("/api/models", params={"provider": "claude-subscription-directsdk-experimental"})).json()
        assert "sonnet" in ms["models"] and ms["error"] is None


async def test_install_agy_refuses_when_present_and_reports_failures(monkeypatch):
    from assistant.providers import cli_manage
    monkeypatch.setattr(cli_manage, "find_command", lambda *a, **k: "/x/agy")
    with pytest.raises(cli_manage.ProviderError, match="already installed"):
        await cli_manage.install_agy()
    monkeypatch.setattr(cli_manage, "find_command", lambda *a, **k: None)

    class R:  # installer download that is not a script
        status_code, content = 200, b"<html>nope</html>"

    class C:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return R()

    monkeypatch.setattr("httpx.AsyncClient", C)
    with pytest.raises(cli_manage.ProviderError, match="install script"):
        await cli_manage.install_agy()
    assert cli_manage._installing is False  # guard released after failure


async def test_cli_install_and_status_refuse_inside_docker(monkeypatch):
    from assistant.providers import cli_manage
    monkeypatch.setattr(cli_manage, "in_container", lambda: True)
    with pytest.raises(cli_manage.ProviderError, match="run the Kelo server outside a container"):
        await cli_manage.install_agy()


async def test_cli_provider_models_come_from_the_cli_with_labels(monkeypatch):
    from assistant.models import _cache, _labels
    from assistant.providers import cli_manage
    _cache.clear(); _labels.clear()

    async def fake_models(name, local=False):
        return {"gemini-3.8-flash-low": "Gemini 3.8 Flash (Low)", "claude-sonnet-4-6": "Claude Sonnet 4.6"}

    monkeypatch.setattr(cli_manage, "cli_models", fake_models)
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = (await c.get("/api/models", params={"provider": "antigravity-oauth"})).json()
        assert r["models"] == ["gemini-3.8-flash-low", "claude-sonnet-4-6"] and r["error"] is None
        assert r["labels"]["gemini-3.8-flash-low"] == "Gemini 3.8 Flash (Low)"

        async def broken(name, local=False):
            raise cli_manage.ProviderError("agy에 로그인했는지 확인하세요")
        monkeypatch.setattr(cli_manage, "cli_models", broken)
        _cache.clear(); _labels.clear()
        r = (await c.get("/api/models", params={"provider": "antigravity-oauth"})).json()
        assert r["models"] == [] and "로그인" in r["error"]  # degrades to manual entry, never a 5xx


async def test_usage_shows_the_selected_cli_providers_limits(monkeypatch):
    from assistant.providers import cli_manage
    calls = []

    async def fake_usage(name, local=False):
        calls.append(name)
        return [{"group": None, "label": "Current session", "used_percent": 28, "resets_at": "2026-11-05T21:00:00Z", "resets_text": None}]

    monkeypatch.setattr(cli_manage, "cli_usage", fake_usage)
    async with Running(create_app(settings(unofficial_providers=True), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        u = (await c.get("/api/usage")).json()
        assert u["subscription"] is None and u["today"] == {"input_tokens": 0, "output_tokens": 0}  # "p" is not a CLI provider

        await c.put("/api/providers/claude-subscription-directsdk-experimental/consent", json={"accepted": True})
        await c.put("/api/profile", json={"provider": "claude-subscription-directsdk-experimental", "model": "sonnet"})
        u = (await c.get("/api/usage")).json()
        assert u["subscription"]["windows"][0]["used_percent"] == 28 and u["subscription"]["error"] is None
        await c.get("/api/usage")
        assert len(calls) == 1                         # cached for a minute: opening settings must not spawn the CLI every time
        await c.get("/api/usage", params={"refresh": "true"})
        assert len(calls) == 2

        async def broken(name, local=False):
            raise cli_manage.ProviderError("사용량 정보를 받지 못했어요. 로그인했는지 확인하세요")
        monkeypatch.setattr(cli_manage, "cli_usage", broken)
        u = (await c.get("/api/usage", params={"refresh": "true"})).json()
        assert u["subscription"]["windows"] == [] and "로그인" in u["subscription"]["error"]  # never a 5xx


async def test_usage_subscriptions_roles_and_deduplication(tmp_path, monkeypatch):
    from assistant.providers import cli_manage

    async def fake_usage(name, local=False):
        return [{"group": None, "label": "Session", "used_percent": 10, "resets_at": None, "resets_text": None}]

    monkeypatch.setattr(cli_manage, "cli_usage", fake_usage)
    st = settings(db_path=str(tmp_path / "s.db"), unofficial_providers=True)
    async with Running(create_app(st, tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        # an API-key main: subscriptions is [] and subscription is null
        u = (await c.get("/api/usage")).json()
        assert u["subscription"] is None
        assert u["subscriptions"] == []

        await c.put("/api/providers/claude-subscription-directsdk-experimental/consent", json={"accepted": True})
        await c.put("/api/providers/antigravity-oauth/consent", json={"accepted": True})

        # main Claude + background agy -> two subscriptions with roles main and background
        await c.put("/api/profile", json={
            "provider": "claude-subscription-directsdk-experimental", "model": "sonnet",
            "background_provider": "antigravity-oauth", "background_model": "flash",
        })
        u = (await c.get("/api/usage")).json()
        assert u["subscription"] is not None
        assert u["subscription"]["provider"] == "claude-subscription-directsdk-experimental"
        assert len(u["subscriptions"]) == 2
        assert u["subscriptions"][0]["provider"] == "claude-subscription-directsdk-experimental"
        assert u["subscriptions"][0]["role"] == "main"
        assert u["subscriptions"][1]["provider"] == "antigravity-oauth"
        assert u["subscriptions"][1]["role"] == "background"

        # main = fallback -> one entry (main role kept)
        from assistant.store import Store
        Store(st.db_path).set_profile(
            provider="claude-subscription-directsdk-experimental", model="sonnet",
            fallback_provider="claude-subscription-directsdk-experimental", fallback_model="opus",
            background_provider="", background_model="",
        )
        u = (await c.get("/api/usage")).json()
        assert len(u["subscriptions"]) == 1
        assert u["subscriptions"][0]["provider"] == "claude-subscription-directsdk-experimental"
        assert u["subscriptions"][0]["role"] == "main"


async def test_fallback_model_takes_over_when_the_primary_fails_before_answering(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    tried = []

    async def fn(provider, request, **kw):
        tried.append((provider, request.model, kw.get("api_key")))
        if provider == "openrouter":
            raise RuntimeError("HTTP 429 rate limited sk-SECRET")
        yield TextDelta("from fallback"); yield Done("stop")

    app = create_app(Settings(token=TOKEN, cdp_host=None), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"provider": "openrouter", "model": "big/model",
                                          "fallback_provider": "deepseek", "fallback_model": "small"})
        await c.put("/api/providers/deepseek/key", json={"api_key": "sk-deepseek-1234"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            ev = await read_events(r)
        saved = (await c.get("/api/messages")).json()
    assert [e["type"] for e in ev] == ["fallback", "text", "done"]
    assert ev[0]["provider"] == "deepseek" and ev[0]["model"] == "small" and "429" in ev[0]["detail"] and "429" not in ev[0]["reason"]
    assert tried == [("openrouter", "big/model", None), ("deepseek", "small", "sk-deepseek-1234")]  # its own stored key
    assert saved[-1]["content"] == "from fallback"


async def test_no_fallback_when_unset_same_as_primary_or_after_output_started():
    async def always_fails(provider, request, **kw):
        raise RuntimeError("boom")
        yield

    async def fails_midway(provider, request, **kw):
        yield TextDelta("partial")
        raise RuntimeError("dropped")

    async with Running(create_app(settings(), tools=[], stream_fn=always_fails)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            assert [e["type"] for e in await read_events(r)] == ["error"]            # none configured
        refused = await c.put("/api/profile", json={"fallback_provider": "p", "fallback_model": "m"})
        assert refused.status_code == 422                                            # the primary's own provider cannot be the fallback
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            assert [e["type"] for e in await read_events(r)] == ["error"]            # so there is none: no pointless retry
    async with Running(create_app(settings(), tools=[], stream_fn=fails_midway)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"fallback_provider": "q", "fallback_model": "m2"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            assert [e["type"] for e in await read_events(r)] == ["text", "error"]    # already answered: never switch mid-reply


async def test_fallback_can_be_cleared():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"fallback_provider": "q", "fallback_model": "m2"})
        assert (await c.get("/api/profile")).json()["fallback_model"] == "m2"
        await c.put("/api/profile", json={"fallback_provider": "", "fallback_model": ""})
        p = (await c.get("/api/profile")).json()
        assert p["fallback_provider"] == "" and p["fallback_model"] == ""


async def test_thinking_status_reaches_the_client_as_a_bare_event():
    from assistant.providers import Thinking

    async def fn(provider, request, **kw):
        yield Thinking(); yield TextDelta("hi"); yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            ev = await read_events(r)
    assert ev[0] == {"type": "thinking"} and [e["type"] for e in ev] == ["thinking", "text", "done"]


async def test_an_empty_or_reasoning_only_reply_is_an_error_not_a_silent_blank():
    from assistant.providers import Thinking
    tries = []

    async def only_thinks(provider, request, **kw):
        tries.append(request.messages[-1])
        yield Thinking(); yield Done("stop")           # e.g. agy tried a native tool and the plugin blocked it

    async with Running(create_app(settings(), tools=[], stream_fn=only_thinks)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            ev = await read_events(r)
        assert (await c.get("/api/messages")).json()[-1]["role"] == "user"   # nothing was saved as the assistant's answer
    assert [e["type"] for e in ev] == ["thinking", "thinking", "error"] and "empty reply" in ev[-1]["detail"]
    assert "empty reply" not in ev[-1]["message"]
    assert len(tries) == 2 and tries[1]["role"] == "system" and "no shell" in tries[1]["content"]   # one nudged retry, no more
    assert tries[0]["role"] == "user"


async def test_an_empty_reply_triggers_the_fallback_model():
    from assistant.providers import Thinking

    async def fn(provider, request, **kw):
        if provider == "p":
            yield Thinking(); yield Done("stop")
        else:
            yield TextDelta("rescued"); yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"fallback_provider": "q", "fallback_model": "m2"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            ev = await read_events(r)
    assert [e["type"] for e in ev] == ["thinking", "thinking", "fallback", "text", "done"] and "empty reply" in ev[2]["detail"]


async def test_a_nudged_retry_can_rescue_an_empty_first_reply():
    from assistant.providers import Thinking

    async def fn(provider, request, **kw):
        if any(m["role"] == "system" and "no shell" in m["content"] for m in request.messages):
            yield TextDelta("answered after the nudge"); yield Done("stop")
        else:
            yield Thinking(); yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            ev = await read_events(r)
        saved = (await c.get("/api/messages")).json()
    assert [e["type"] for e in ev] == ["thinking", "text", "done"] and saved[-1]["content"] == "answered after the nudge"


async def test_tools_then_no_answer_is_reported_not_left_blank():
    from assistant.agent import Tool

    async def handler(args):
        return "ok"

    async def fn(provider, request, **kw):
        if not any(m.get("role") == "tool" for m in request.messages):
            yield ToolCall("c1", "look", "{}"); yield Done("tool_calls")
        else:
            yield Done("stop")                                   # silent forever

    tool = Tool("look", "look", {"type": "object"}, handler)
    async with Running(create_app(settings(), tools=[tool], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            ev = await read_events(r)
    kinds = [e["type"] for e in ev]
    # a plain line in the user's words and a Continue button, never an error code or a blank screen
    assert "tool_started" in kinds and kinds[-1] == "done" and "error" not in kinds and "replies" in kinds
    assert "Press Continue" in "".join(e["text"] for e in ev if e["type"] == "text")
    assert "retry" not in kinds    # this tool is not read-only: starting the turn over could repeat what it did


def looker(*, long_marker="LONG-PROMPT", answers_under_short_rules=True):
    """A model that, under the long prompt, only looks around (a read-only tool) and never answers; under the short rules it answers."""
    seen = []

    async def fn(provider, request, **kw):
        system = next((m["content"] for m in request.messages if m["role"] == "system" and m["content"] != ""), "")
        seen.append((provider, "LONG" if long_marker in system else "SHORT", [m["role"] for m in request.messages]))
        if long_marker in system or not answers_under_short_rules:
            if not any(m.get("role") == "tool" for m in request.messages):
                yield ToolCall("c1", "look", "{}"); yield Usage(100, 10); yield Done("tool_calls")
            else:
                yield Usage(100, 0); yield Done("stop")            # silent after its look-around
        else:
            yield TextDelta("signed in"); yield Usage(30, 5); yield Done("stop")
    return fn, seen


def look_tool():
    async def handler(args):
        return "files: a, b"
    return Tool("look", "look around", {"type": "object"}, handler, read_only=True)


LONG_BODY = {"messages": [{"role": "system", "content": "LONG-PROMPT " + "x" * 2000}, {"role": "user", "content": "log in to github"}]}


async def test_a_model_that_only_looks_around_gets_one_more_try_under_the_short_rules():
    fn, seen = looker()
    async with Running(create_app(settings(), tools=[look_tool()], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=LONG_BODY) as r:
            ev = await read_events(r)
    kinds = [e["type"] for e in ev]
    assert kinds.count("retry") == 1 and kinds.index("tool_result") < kinds.index("retry") < kinds.index("text") and kinds[-1] == "done"
    assert seen[0][1] == "LONG" and seen[-1][1] == "SHORT" and all(x[1] == "LONG" for x in seen[:-1])
    assert seen[-1][2] == ["system", "user"]                                      # the second try starts clean: rules + the user's message only
    assert ev[-1]["input_tokens"] > 30 + 100 and ev[-1]["output_tokens"] > 5    # the second try alone is 30/5: the first try's tokens are counted too
    assert not any(e["type"] == "error" for e in ev)


async def test_the_second_try_is_made_once_and_then_the_failure_is_reported():
    fn, seen = looker(answers_under_short_rules=False)
    async with Running(create_app(settings(), tools=[look_tool()], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json={"messages": [{"role": "system", "content": "SHORT"}, *LONG_BODY["messages"][1:]]}) as r:
            ev = await read_events(r)
    kinds = [e["type"] for e in ev]
    assert kinds.count("retry") == 1 and kinds[-1] == "done" and "error" not in kinds
    assert "Press Continue" in "".join(e["text"] for e in ev if e["type"] == "text")


async def test_after_the_second_try_fails_the_fallback_model_may_still_answer_when_nothing_was_changed():
    async def fn(provider, request, **kw):
        if provider == "p":
            if not any(m.get("role") == "tool" for m in request.messages):
                yield ToolCall("c1", "look", "{}"); yield Done("tool_calls")
            else:
                yield Done("stop")
        else:
            yield TextDelta("the other model answered"); yield Done("stop")

    async with Running(create_app(settings(), tools=[look_tool()], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"fallback_provider": "q", "fallback_model": "m2"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            ev = await read_events(r)
    kinds = [e["type"] for e in ev]
    assert "retry" in kinds and kinds.index("retry") < kinds.index("fallback") < kinds.index("text") and kinds[-1] == "done"
    assert "no answer" in next(e for e in ev if e["type"] == "fallback")["detail"]


async def test_a_look_around_that_ends_in_text_is_not_started_over():
    async def fn(provider, request, **kw):
        if not any(m.get("role") == "tool" for m in request.messages):
            yield ToolCall("c1", "look", "{}"); yield Done("tool_calls")
        else:
            yield TextDelta("here is what I found"); yield Done("stop")

    async with Running(create_app(settings(), tools=[look_tool()], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY) as r:
            kinds = [e["type"] for e in await read_events(r)]
    assert "retry" not in kinds and kinds[-1] == "done", " ".join(kinds)


async def test_a_pass_that_goes_in_circles_is_cut_short_and_started_over_without_waiting_for_it_to_give_up():
    seen = []

    async def fn(provider, request, **kw):
        system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
        seen.append("LONG" if "LONG-PROMPT" in system else "SHORT")
        if "LONG-PROMPT" in system:
            n = sum(1 for m in request.messages if m["role"] == "tool")
            yield ToolCall(f"c{n}", "look", "{}"); yield Done("tool_calls")      # the same look-around again and again
        else:
            yield TextDelta("signed in"); yield Done("stop")

    async with Running(create_app(settings(max_steps=12), tools=[look_tool()], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=LONG_BODY) as r:
            ev = await read_events(r)
    kinds = [e["type"] for e in ev]
    assert seen == ["LONG", "LONG", "SHORT"]                      # the first repeat ends the pass: not three or ten more rounds
    assert kinds.count("tool_started") == kinds.count("tool_result") == 2     # no chip is left spinning
    assert kinds.index("retry") > kinds.index("tool_result") and kinds[-2:] == ["text", "done"]


async def test_a_repeat_after_something_was_changed_is_left_alone():
    async def fn(provider, request, **kw):
        n = sum(1 for m in request.messages if m["role"] == "tool")
        if n == 0:
            yield ToolCall("w1", "change", "{}"); yield Done("tool_calls")
        elif n < 3:
            yield ToolCall(f"c{n}", "look", "{}"); yield Done("tool_calls")
        else:
            yield TextDelta("done"); yield Done("stop")

    async def change(args):
        return "changed"

    tools = [look_tool(), Tool("change", "changes things", {"type": "object"}, change)]
    async with Running(create_app(settings(), tools=tools, stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY) as r:
            kinds = [e["type"] for e in await read_events(r)]
    assert "retry" not in kinds and kinds[-1] == "done", " ".join(kinds)


async def test_a_quiet_reply_stream_keeps_sending_comment_lines_so_the_connection_is_not_dropped(monkeypatch):
    import assistant.server as server_module
    monkeypatch.setattr(server_module, "HEARTBEAT_SECONDS", 0.05)

    async def slow(provider, request, **kw):
        await asyncio.sleep(0.4)            # a model that thinks for a while before it says anything
        yield TextDelta("finally"); yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=slow)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY) as r:
            lines = [ln async for ln in r.aiter_lines()]
    assert lines[0] == ": connected"                                   # bytes right away
    assert sum(1 for ln in lines if ln == ": keep-alive") >= 3          # and while it was quiet
    events = [json.loads(ln[6:]) for ln in lines if ln.startswith("data: ")]
    assert [e["type"] for e in events] == ["text", "done"]              # the comment lines are not events


async def test_the_app_reads_the_stream_through_the_comment_lines(monkeypatch):
    import assistant.server as server_module
    monkeypatch.setattr(server_module, "HEARTBEAT_SECONDS", 0.02)

    async def slow(provider, request, **kw):
        await asyncio.sleep(0.15)
        yield TextDelta("hi"); yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=slow)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "hello"}) as r:
            ev = await read_events(r)
        assert [e["type"] for e in ev] == ["text", "done"]
        assert (await c.get("/api/messages")).json()[-1]["content"] == "hi"       # the answer was saved as before


# ---- replies followed with short requests (what the phone app does: a stream can be cut by a phone, a router or a quick tunnel)

async def follow(c, turn_id, after=0, wait=5, reactions=False):
    """Ask again and again until the reply is over; returns every event (the reactions on the user's message only when asked:
    test_reactions.py covers those)."""
    events = []
    for _ in range(200):
        r = (await c.get(f"/api/turns/{turn_id}", params={"after": after, "wait": wait})).json()
        events += [e for e in r["events"] if reactions or e.get("type") != "reaction"]
        after = r["next"]
        if r["done"]:
            return events
    raise AssertionError("the reply never ended")


async def test_a_reply_can_be_followed_with_short_requests_and_is_saved():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted([TextDelta("hel"), TextDelta("lo"), Usage(4, 2), Done("stop")]))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        started = await c.post("/api/turns", json={"text": "hi"})
        assert started.status_code == 200
        events = await follow(c, started.json()["id"])
        assert [e["type"] for e in events] == ["text", "text", "done"] and "".join(e["text"] for e in events if e["type"] == "text") == "hello"
        assert [m["content"] for m in (await c.get("/api/messages")).json()] == ["hi", "hello"]


async def test_asking_again_from_the_same_place_gives_the_same_events_so_a_broken_request_loses_nothing():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted([TextDelta("a"), TextDelta("b"), TextDelta("c"), Done("stop")]))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        first = (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 5})).json()
        again = (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 5})).json()      # the answer to the first never arrived
        assert again["events"][:len(first["events"])] == first["events"]
        tail = (await c.get(f"/api/turns/{tid}", params={"after": 1, "wait": 5})).json()
        assert tail["events"] == again["events"][1:] and tail["next"] == again["next"]           # from the middle, nothing repeated or skipped


async def test_a_reply_nobody_is_following_still_finishes_and_is_saved():
    async def slow(provider, request, **kw):
        await asyncio.sleep(0.3)
        yield TextDelta("done while you were away"); yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=slow)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/turns", json={"text": "hi"})
        await asyncio.sleep(0.8)                                         # the phone was asleep; no request was made
        assert (await c.get("/api/messages")).json()[-1]["content"] == "done while you were away"


async def test_a_poll_waits_for_news_and_comes_back_empty_when_there_is_none():
    async def slow(provider, request, **kw):
        await asyncio.sleep(1.0)
        yield TextDelta("late"); yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=slow)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        t0 = asyncio.get_running_loop().time()
        r = (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 0.2})).json()   # no 👀 yet: the model has not answered
        assert r == {"events": [], "next": 0, "done": False} and 0.15 < asyncio.get_running_loop().time() - t0 < 0.9
        assert [e["type"] for e in await follow(c, tid)][-1] == "done"


async def test_stopping_a_reply_cancels_the_model_and_ends_it_for_the_reader():
    state = {"cancelled": False}

    async def forever(provider, request, **kw):
        yield TextDelta("partial ")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=forever)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 2})                  # the first words arrived
        assert (await c.delete(f"/api/turns/{tid}")).json() == {"ok": True}
        events = await follow(c, tid, after=2)                                           # after 👀 and the first words
        assert state["cancelled"] and events == []                                       # it ended; nothing more was said
        assert (await c.get("/api/messages")).json()[-1]["content"] == "partial "      # what was said before the stop is kept


async def test_a_reply_that_nobody_asks_about_any_more_is_stopped(monkeypatch):
    import assistant.server as server_module
    monkeypatch.setattr(server_module, "TURN_IDLE_SECONDS", 0.2)
    state = {"cancelled": False}

    async def forever(provider, request, **kw):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        yield Done("stop")

    async with Running(create_app(settings(), tools=[], stream_fn=forever)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        first = (await c.post("/api/turns", json={"text": "one"})).json()["id"]
        await asyncio.sleep(0.5)                                                         # the phone is gone
        await c.post("/api/turns", json={"text": "two"})                                 # the next request tidies up
        await asyncio.sleep(0.2)
        assert state["cancelled"]
        r = (await c.get(f"/api/turns/{first}", params={"after": 0, "wait": 0})).json()
        assert r["done"] is True


async def test_unknown_replies_and_missing_tokens_are_refused():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.post("/api/turns", json={"text": "hi"})).status_code == 401
        assert (await c.get("/api/turns/abc")).status_code == 401
        assert (await c.delete("/api/turns/abc")).status_code == 401
        assert (await c.get("/api/turns/nope", headers=AUTH)).status_code == 404
        assert (await c.delete("/api/turns/nope", headers=AUTH)).status_code == 404


async def test_an_approval_works_while_the_reply_is_followed_with_short_requests():
    ran = []

    async def handler(args):
        ran.append(args)
        return "sent"

    tool = Tool("send", "send something", {"type": "object"}, handler, requires_approval=True)

    async def fn(provider, request, **kw):
        if not any(m.get("role") == "tool" for m in request.messages):
            yield ToolCall("c1", "send", "{}"); yield Done("tool_calls")
        else:
            yield TextDelta("sent it"); yield Done("stop")

    async with Running(create_app(settings(), tools=[tool], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "send it"})).json()["id"]
        after, events = 0, []
        for _ in range(50):
            r = (await c.get(f"/api/turns/{tid}", params={"after": after, "wait": 5})).json()
            events += r["events"]
            after = r["next"]
            ask = next((e for e in r["events"] if e["type"] == "approval_request"), None)
            if ask:
                assert (await c.post(f"/api/approvals/{ask['approval_id']}", json={"approve": True})).status_code == 200
            if r["done"]:
                break
        assert ran == [{}] and [e["type"] for e in events][-1] == "done" and any(e["type"] == "text" and e["text"] == "sent it" for e in events)


# ---- the main model and the fallback never come from the same provider

async def test_the_fallback_must_come_from_another_provider_than_the_main_model():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        ok = await c.put("/api/profile", json={"provider": "p", "model": "m", "fallback_provider": "q", "fallback_model": "m2"})
        assert ok.status_code == 200 and ok.json()["fallback_provider"] == "q"
        # the main model moves onto the fallback's provider: the fallback is emptied
        moved = (await c.put("/api/profile", json={"provider": "q", "model": "m3"})).json()
        assert (moved["provider"], moved["fallback_provider"], moved["fallback_model"]) == ("q", "", "")
        # asking for the main model's provider as the fallback is refused, and nothing changes
        bad = await c.put("/api/profile", json={"fallback_provider": "q", "fallback_model": "x"})
        assert bad.status_code == 422 and "different provider" in bad.json()["detail"]
        assert (await c.get("/api/profile")).json()["fallback_provider"] == ""
        # both at once, as an older page sends them (the fallback unchanged): emptied, not refused
        await c.put("/api/profile", json={"provider": "p", "model": "m", "fallback_provider": "q", "fallback_model": "m2"})
        stale = await c.put("/api/profile", json={"provider": "q", "model": "m3", "fallback_provider": "q", "fallback_model": "m2"})
        assert stale.status_code == 200 and stale.json()["provider"] == "q" and stale.json()["fallback_provider"] == ""
        # one request that picks the same new provider for both is refused as a whole
        both = await c.put("/api/profile", json={"provider": "r", "model": "m", "fallback_provider": "r", "fallback_model": "x"})
        assert both.status_code == 422 and (await c.get("/api/profile")).json()["provider"] == "q"
        # clearing, and settings that have nothing to do with it, are always fine
        await c.put("/api/profile", json={"fallback_provider": "s", "fallback_model": "m5"})
        assert (await c.put("/api/profile", json={"effort": ""})).json()["fallback_provider"] == "s"
        assert (await c.put("/api/profile", json={"fallback_provider": "", "fallback_model": ""})).json()["fallback_provider"] == ""


async def test_the_servers_default_provider_counts_as_the_main_model_and_other_names_of_a_provider_are_the_same_provider():
    async with Running(create_app(settings(unofficial_providers=True), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        # no provider chosen in the app: the server's own default ("p") is the main model
        assert (await c.put("/api/profile", json={"fallback_provider": "p", "fallback_model": "m"})).status_code == 422
        assert (await c.put("/api/profile", json={"fallback_provider": "q", "fallback_model": "m2"})).status_code == 200
        # agy is another name of antigravity-oauth (an unofficial provider: consent first)
        await c.put("/api/providers/antigravity-oauth/consent", json={"accepted": True})
        assert (await c.put("/api/profile", json={"provider": "antigravity-oauth", "model": "g", "fallback_provider": "", "fallback_model": ""})).status_code == 200
        assert (await c.put("/api/profile", json={"fallback_provider": "agy", "fallback_model": "g2"})).status_code == 422
        await c.put("/api/profile", json={"provider": "openai", "model": "o", "fallback_provider": "agy", "fallback_model": "g2"})
        moved = (await c.put("/api/profile", json={"provider": "antigravity-oauth", "model": "g"})).json()
        assert moved["fallback_provider"] == "" and moved["provider"] == "antigravity-oauth"      # the user's case: choose agy while agy is the fallback


async def test_a_profile_saved_before_the_rule_has_its_fallback_emptied_when_the_app_starts(tmp_path):
    from assistant.store import Store
    db = tmp_path / "s.db"
    old = Store(db)                                   # data from before the rule: both on provider p
    old.set_profile(provider="p", model="m", fallback_provider="p", fallback_model="m2")
    other = Store(tmp_path / "t.db")                  # a pair that is fine stays as it is
    other.set_profile(provider="p", model="m", fallback_provider="q", fallback_model="m2")
    async with Running(create_app(settings(db_path=str(db)), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        p = (await c.get("/api/profile")).json()
        assert (p["provider"], p["model"], p["fallback_provider"], p["fallback_model"]) == ("p", "m", "", "")
    async with Running(create_app(settings(db_path=str(tmp_path / "t.db")), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/profile")).json()["fallback_provider"] == "q"


async def test_a_fallback_on_the_same_provider_is_never_used_even_if_one_gets_into_the_profile(tmp_path):
    from assistant.store import Store
    db = tmp_path / "s.db"
    tried = []

    async def fails(provider, request, **kw):
        tried.append(request.model)
        raise RuntimeError("boom")
        yield

    async with Running(create_app(settings(db_path=str(db)), tools=[], stream_fn=fails)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        Store(db).set_profile(provider="p", model="m", fallback_provider="p", fallback_model="m2")    # written behind the app's back, while it runs
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            kinds = [e["type"] for e in await read_events(r)]
        assert (await c.put("/api/profile", json={"effort": ""})).json()["fallback_provider"] == "p"   # unrelated updates leave it alone
    assert kinds == ["error"] and tried == ["m"]      # no second attempt on the same provider


async def test_a_chat_request_can_forbid_the_fallback_model_so_it_goes_to_the_named_model_only():
    tried = []

    async def fn(provider, request, **kw):
        tried.append(provider)
        if provider == "p":
            raise RuntimeError("boom")
        yield TextDelta("from the fallback"); yield Done("stop")

    body = {"messages": [{"role": "user", "content": "hi"}], "provider": "p", "model": "m"}
    async with Running(create_app(settings(), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"fallback_provider": "q", "fallback_model": "m2"})
        async with c.stream("POST", "/api/chat", json={**body, "fallback": False}) as r:
            kinds = [e["type"] for e in await read_events(r)]
        assert kinds == ["error"] and tried == ["p"]                       # no second attempt anywhere else
        tried.clear()
        async with c.stream("POST", "/api/chat", json=body) as r:         # by default the profile's fallback still catches a failure
            kinds = [e["type"] for e in await read_events(r)]
        assert kinds == ["fallback", "text", "done"] and tried == ["p", "q"]
