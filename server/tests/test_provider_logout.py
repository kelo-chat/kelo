"""Signing out of a provider reached by signing in (CLI subscriptions and OAuth): the stored login really goes, consent stays,
the main model is protected and a fallback on it is emptied. Only fakes: no CLI runs and no provider is called."""

import base64
import json

import httpx
import pytest

from assistant.cli_runner import create_app as create_runner
from assistant.providers import ProviderError, cli_manage, cli_remote, oauth
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, scripted

RUNNER_TOKEN = "r" * 32


def codex_jwt(email: str = "me@example.com") -> str:
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b({'alg': 'none'})}.{b({'exp': 9999999999, 'https://api.openai.com/profile': {'email': email}})}.sig"


@pytest.fixture(autouse=True)
def oauth_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_OAUTH_FILE", str(tmp_path / "oauth.json"))
    monkeypatch.setattr(oauth, "_qwen_cli_path", lambda: tmp_path / "qwen" / "oauth_creds.json")
    for var in ("ASSISTANT_CLI_CLAUDE_URL", "ASSISTANT_CLI_AGY_URL", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "oauth.json"


def sign_in_codex():
    oauth._save("openai-codex", {"tokens": {"access_token": codex_jwt(), "refresh_token": "r"}})


def app_for(tmp_path, provider="openai"):
    return create_app(Settings(token=TOKEN, provider=provider, model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                               unofficial_providers=True),
                      tools=[], stream_fn=scripted())


# ---- the token file

def test_logout_removes_the_entry_and_the_qwen_cli_file(tmp_path):
    sign_in_codex()
    assert oauth.status()["openai-codex"] == {"logged_in": True, "account": "me@example.com"}
    assert oauth.logout("openai-codex") is True and oauth.status()["openai-codex"] == {"logged_in": False}
    assert oauth.logout("openai-codex") is False
    cli = oauth._qwen_cli_path()
    cli.parent.mkdir()
    cli.write_text('{"access_token": "x"}')
    assert oauth.status()["qwen-oauth"]["logged_in"] is True
    assert oauth.logout("qwen-oauth") is True and not cli.exists() and oauth.status()["qwen-oauth"]["logged_in"] is False


# ---- the endpoint, OAuth providers

async def test_sign_out_of_an_oauth_provider_keeps_consent_and_clears_a_fallback(tmp_path):
    sign_in_codex()
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/providers/openai-codex/consent", json={"accepted": True})).status_code == 200
        assert (await c.put("/api/profile", json={"fallback_provider": "openai-codex", "fallback_model": "gpt"})).status_code == 200
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == "openai-codex")
        assert row["signed_in"] is True and row["account"] == "me@example.com"
        r = await c.delete("/api/providers/openai-codex/login")
        assert r.status_code == 200 and r.json() == {"ok": True, "name": "openai-codex", "removed": True,
                                                     "still_signed_in": False, "fallback_cleared": True,
                                                     "background_cleared": False}
        prof = (await c.get("/api/profile")).json()
        assert prof["fallback_provider"] == "" and prof["fallback_model"] == ""
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == "openai-codex")
        assert row["signed_in"] is False and row["logged_in"] is False and row["account"] is None
        assert row["consent"]["version"] >= 1   # only the login went
        assert (await c.delete("/api/providers/openai-codex/login")).status_code == 404   # nothing left: no-op
        assert (await c.delete("/api/providers/openai/login")).status_code == 400          # an API-key provider
        assert (await c.delete("/api/providers/nope/login")).status_code == 404


async def test_the_main_model_is_not_signed_out(tmp_path):
    sign_in_codex()
    async with Running(app_for(tmp_path, provider="openai-codex")) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.delete("/api/providers/openai-codex/login")
        assert r.status_code == 409 and r.json()["detail"].startswith("main_model_uses_login")
    assert oauth.status()["openai-codex"]["logged_in"] is True


# ---- the endpoint, CLI providers (status and sign-out faked)

async def test_sign_out_of_a_cli_provider_goes_through_the_cli(tmp_path, monkeypatch):
    state = {"logged_in": True}
    calls = []

    async def fake_status(name):
        return {"installed": True, "installable": False, "logged_in": state["logged_in"], "account": "a@b.c" if state["logged_in"] else None, "hint": ""}

    async def fake_logout(name):
        calls.append(name)
        state["logged_in"] = False
        return {"removed": True, "still_signed_in": False}

    monkeypatch.setattr(cli_manage, "cli_status", fake_status)
    monkeypatch.setattr(cli_manage, "cli_logout", fake_logout)
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == cli_manage.AGY)
        assert row["signed_in"] is True and row["account"] == "a@b.c"
        r = await c.delete("/api/providers/agy/login")
        assert r.status_code == 200 and r.json()["fallback_cleared"] is False and calls == [cli_manage.AGY]
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == cli_manage.AGY)
        assert row["signed_in"] is False
        assert (await c.delete("/api/providers/agy/login")).status_code == 404 and calls == [cli_manage.AGY]
        state["logged_in"] = True
        assert (await c.put("/api/providers/claude-cli/consent", json={"accepted": True})).status_code == 200
        assert (await c.put("/api/profile", json={"provider": "claude-cli", "model": "sonnet"})).status_code == 200
        assert (await c.delete("/api/providers/claude-cli/login")).status_code == 409 and calls == [cli_manage.AGY]


# ---- inside the CLI's home (local or in the sidecar)

async def test_claude_sign_out_uses_its_own_command_and_removes_the_credentials_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND", "/fake/claude")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / ".credentials.json").write_text("{}")
    (tmp_path / "claude" / "settings.json").write_text("{}")
    seen = []

    async def fake_run(argv, env, timeout=20.0):
        seen.append(argv)
        assert "ASSISTANT_TOKEN" not in env
        return 0

    monkeypatch.setattr(cli_manage, "_run_cli", fake_run)
    assert await cli_manage.cli_logout(cli_manage.CLAUDE, local=True) == {"removed": True, "still_signed_in": False}
    assert seen == [["/fake/claude", "auth", "logout"]]
    assert not (tmp_path / "claude" / ".credentials.json").exists() and (tmp_path / "claude" / "settings.json").exists()


async def test_agy_sign_out_removes_only_its_token_files(tmp_path, monkeypatch):
    d = tmp_path / "agy"
    d.mkdir()
    for f in ("jetski-standalone-oauth-token", "antigravity-oauth-token", "settings.json"):
        (d / f).write_text("x" * 20)
    monkeypatch.setenv("ANTIGRAVITY_CONFIG_DIR", str(d))
    assert await cli_manage.cli_logout(cli_manage.AGY, local=True) == {"removed": True, "still_signed_in": False}
    assert sorted(p.name for p in d.iterdir()) == ["settings.json"]
    assert await cli_manage.cli_logout(cli_manage.AGY, local=True) == {"removed": False, "still_signed_in": False}


async def test_the_server_asks_the_sidecar_to_sign_out(monkeypatch):
    monkeypatch.setenv("ASSISTANT_CLI_CLAUDE_URL", "http://runner")
    monkeypatch.setenv("CLI_RUNNER_TOKEN", RUNNER_TOKEN)
    seen = []

    async def fake_logout(name, *, local=False):
        seen.append((name, local))
        return {"removed": True, "still_signed_in": False}

    monkeypatch.setattr(cli_manage, "cli_logout", fake_logout)
    app = create_runner("claude", RUNNER_TOKEN, stream_fn=lambda p, r: iter(()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner") as c:
        assert (await c.post("/logout")).status_code == 401
        assert await cli_remote.logout(cli_manage.CLAUDE, client=c) == {"removed": True, "still_signed_in": False}
    assert seen == [(cli_manage.CLAUDE, True)]
    old = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"detail": "Not Found"})))
    with pytest.raises(ProviderError, match="out of date"):
        await cli_remote.logout(cli_manage.CLAUDE, client=old)
