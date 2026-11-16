"""New models without an update: the Codex client version Kelo presents, the last good model list, thinking levels and context windows
read from the providers' own listings, and the CLI sidecars' version check. Every provider answer here is a hand-written sample in the
shape the provider documents; nothing reaches a real API (no request to chatgpt.com or api.openai.com is ever made)."""

import base64
import json
import time

import httpx
import pytest

from assistant import effort, models, windows
from assistant.providers import ChatRequest, Done, TextDelta, cli_manage, get_provider, oauth, releases, stream_chat
from assistant.providers.types import ListUnavailable, ProviderError
from assistant.server import create_app

from test_server import AUTH, Running, scripted, settings  # noqa: F401 (shared helpers)


def jwt(exp: float) -> str:
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b({'alg': 'none'})}.{b({'exp': exp, 'https://api.openai.com/auth': {'chatgpt_account_id': 'acct-1'}})}.sig"


@pytest.fixture
def codex_login(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_OAUTH_FILE", str(tmp_path / "oauth.json"))
    oauth._save("openai-codex", {"tokens": {"access_token": jwt(time.time() + 3600), "refresh_token": "r"}})


def fetcher(answers):
    calls = []

    async def fake(what):
        calls.append(what)
        return answers.get(what)
    fake.calls = calls
    return fake


# ---------------------------------------------------------------- releases: the latest published versions

async def test_npm_dist_tag_latest_is_read_and_cached_a_day(monkeypatch):
    seen = []

    async def fake_get_json(url):
        seen.append(url)
        if "@openai/codex" in url:
            return {"latest": "0.170.2", "alpha": "0.171.0-alpha.3", "darwin-arm64": "0.170.2-darwin-arm64"}
        if "claude-code" in url:
            return {"stable": "2.1.311", "latest": "2.1.319"}
        return {"version": "1.5.3", "url": "https://storage.googleapis.com/x.tar.gz", "sha512": "ab"}

    monkeypatch.setattr(releases, "_get_json", fake_get_json)
    releases.set_fetcher(None)   # the real look-up, over the fake JSON
    assert await releases.codex_version() == "0.170.2"            # dist-tags.latest, never an alpha
    assert await releases.latest("claude") == "2.1.319"
    assert await releases.latest("agy") == "1.5.3"
    assert seen[0] == "https://registry.npmjs.org/-/package/@openai/codex/dist-tags"
    assert "/manifests/" in seen[2] and seen[2].endswith(".json")
    await releases.codex_version()
    assert len(seen) == 3                                          # cached


async def test_codex_version_falls_back_to_the_pinned_one_and_keeps_a_last_good_answer(monkeypatch):
    releases.set_fetcher(fetcher({}))
    assert await releases.codex_version() == releases.CODEX_PINNED
    releases.set_fetcher(fetcher({"codex": "0.180.0"}))
    assert await releases.codex_version() == "0.180.0"
    down = fetcher({})
    releases._fetcher[0] = down
    releases._cache["codex"] = (time.monotonic() - releases.DAY - 1, "0.180.0")   # a day old, and the registry is down
    assert await releases.codex_version() == "0.180.0" and down.calls == ["codex"]
    assert await releases.codex_version() == "0.180.0" and down.calls == ["codex"]   # not asked again for 10 minutes


async def test_odd_registry_answers_are_not_taken(monkeypatch):
    for bad in ({"latest": "next"}, {"latest": "1.2"}, {"latest": 3}, ["0.1.0"], None):
        async def fake_get_json(url, bad=bad):
            return bad
        monkeypatch.setattr(releases, "_get_json", fake_get_json)
        assert await releases.fetch("codex") is None
    async def boom(url):
        raise httpx.ConnectTimeout("slow")
    monkeypatch.setattr(releases, "_get_json", boom)
    assert await releases.fetch("claude") is None


def test_version_comparison():
    assert releases.version_of("2.1.311 (Claude Code)") == "2.1.311" and releases.version_of("agy version 1.5.3") == "1.5.3"
    assert releases.newer("2.1.319", "2.1.311 (Claude Code)") and not releases.newer("2.1.311", "2.1.311 (Claude Code)")
    assert releases.newer("2.10.0", "2.9.9") and not releases.newer(None, "1.0.0") and not releases.newer("1.0.0", "weird")


def test_release_checks_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("ASSISTANT_NO_RELEASE_CHECK", "1")
    assert releases.latest_cached("claude") is None


# ---------------------------------------------------------------- the Codex client version: one version for listing and running

def test_codex_user_agent_has_the_official_format():
    ua = oauth.codex_headers(jwt(time.time() + 60), "0.170.2")
    assert ua["originator"] == "codex_cli_rs" and ua["ChatGPT-Account-ID"] == "acct-1"
    head, _, rest = ua["User-Agent"].partition(" ")
    assert head == "codex_cli_rs/0.170.2" and rest.startswith("(") and "; " in rest and "0.0.0" not in ua["User-Agent"]
    assert oauth.codex_headers("x")["User-Agent"].startswith(f"codex_cli_rs/{releases.CODEX_PINNED} ")


async def test_codex_model_list_and_turns_present_the_same_current_version(codex_login):
    releases.set_fetcher(fetcher({"codex": "0.170.2"}))
    seen = []

    def handler(req):
        seen.append((req.url.path, dict(req.url.params), req.headers["user-agent"]))
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol"}]})
        body = ("event: response.output_text.delta\ndata: " + json.dumps({"type": "response.output_text.delta", "delta": "ok"})
                + "\n\nevent: response.completed\ndata: " + json.dumps({"type": "response.completed", "response": {}}) + "\n\n")
        return httpx.Response(200, content=body.encode())

    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await models.list_models(get_provider("openai-codex"), c) == ["gpt-6.1-sol"]
    req = ChatRequest(model="gpt-6.1-sol", messages=[{"role": "user", "content": "hi"}], max_tokens=50)
    assert [e async for e in stream_chat("openai-codex", req, client=c)] == [TextDelta("ok"), Done("stop")]
    (lpath, lparams, lua), (_, _, rua) = seen
    assert lpath == "/backend-api/codex/models" and lparams == {"client_version": "0.170.2"}
    assert lua == rua and lua.startswith("codex_cli_rs/0.170.2 ")
    assert models.labels("openai-codex") == {"gpt-6.1-sol": "GPT-6.1 Sol"}


async def test_a_newer_codex_version_asks_for_the_list_again(codex_login):
    releases.set_fetcher(fetcher({"codex": "0.170.2"}))
    asked = []
    c = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: asked.append(req.url.params["client_version"]) or httpx.Response(200, json={"models": [{"slug": "a"}]})))
    prov = get_provider("openai-codex")
    await models.list_models(prov, c)
    await models.list_models(prov, c)
    assert asked == ["0.170.2"]                       # cached
    releases._cache["codex"] = (time.monotonic(), "0.171.0")
    await models.list_models(prov, c)
    assert asked == ["0.170.2", "0.171.0"]            # never offers a list asked with another version than the turns use


async def test_xai_shares_the_transport_but_gets_no_codex_version(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xk")
    seen = []
    c = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: seen.append(dict(req.url.params)) or httpx.Response(200, json={"data": [{"id": "grok-5"}]})))
    await models.list_models(get_provider("xai"), c)
    assert seen == [{}]


# ---------------------------------------------------------------- the last good list

class Flaky:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, req):
        self.calls += 1
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def ok(*ids):
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


async def test_a_failed_refresh_shows_the_last_good_list_marked_stale_with_backoff(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    prov = get_provider("openrouter")
    flaky = Flaky(ok("a", "b"), httpx.Response(503), httpx.ReadTimeout("slow"), ok("a", "b", "c"))
    c = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    saved = {}
    first = await models.model_list(prov, c, save=saved.update)
    assert first["models"] == ["a", "b"] and first["stale"] is False and first["fetched_at"] and saved["models"] == ["a", "b"]

    clock = [1000.0]
    monkeypatch.setattr(models, "_now", lambda: clock[0])
    r = await models.model_list(prov, c, refresh=True)                    # 503
    assert r["models"] == ["a", "b"] and r["stale"] is True and r["error"] is None
    assert r["fetched_at"] == first["fetched_at"] and r["retry_in"] == 30
    assert (await models.model_list(prov, c))["stale"] and flaky.calls == 2          # backing off: not asked
    clock[0] += 31
    r = await models.model_list(prov, c)                                  # timeout: the wait doubles
    assert r["stale"] and flaky.calls == 3 and r["retry_in"] == 60
    clock[0] += 61
    r = await models.model_list(prov, c)
    assert r == {**r, "models": ["a", "b", "c"], "stale": False} and flaky.calls == 4


def test_backoff_grows_to_half_an_hour():
    assert [models._backoff_after(n) for n in (1, 2, 3, 6, 7, 20)] == [30, 60, 120, 960, 1800, 1800]


async def test_the_error_shows_only_when_there_never_was_a_good_list(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    c = httpx.AsyncClient(transport=httpx.MockTransport(Flaky(httpx.ConnectError("down"))))
    r = await models.model_list(get_provider("openrouter"), c)
    assert r["models"] == [] and r["stale"] is False and "Could not reach" in r["error"]


async def test_the_saved_list_survives_a_restart(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    saved = {"models": ["x", "y"], "labels": {"x": "X"}, "effort": {"x": ["low", "medium", "high"], "y": []}, "fetched_at": 1700000000}
    c = httpx.AsyncClient(transport=httpx.MockTransport(Flaky(httpx.Response(502))))
    r = await models.model_list(get_provider("openrouter"), c, load=lambda: saved)
    assert r["models"] == ["x", "y"] and r["stale"] and r["fetched_at"] == 1700000000 and r["labels"] == {"x": "X"}
    assert effort.levels("openrouter", "y") == () and effort.levels("openrouter", "x") == ("low", "medium", "high")


async def test_credentials_problems_are_not_hidden_behind_an_old_list(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    prov = get_provider("openrouter")
    c = httpx.AsyncClient(transport=httpx.MockTransport(Flaky(ok("a"), httpx.Response(401))))
    await models.model_list(prov, c)
    with pytest.raises(ProviderError) as e:
        await models.model_list(prov, c, refresh=True)
    assert not isinstance(e.value, ListUnavailable)


async def test_a_truncated_list_does_not_replace_the_last_good_one_until_confirmed(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    prov = get_provider("openrouter")
    full = [f"m{i}" for i in range(20)]
    flaky = Flaky(ok(*full), ok("m1", "m2"), ok("m1", "m2"), ok("m1", "m2"))
    c = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    await models.model_list(prov, c)
    r = await models.model_list(prov, c, refresh=True)
    assert r["models"] == sorted(full) and r["stale"] is True             # 2 of 20: kept the long list
    r = await models.model_list(prov, c)                                  # asked again (not from the 5-minute cache) ...
    assert flaky.calls == 3 and r["models"] == ["m1", "m2"] and r["stale"] is False   # ... and the same twice: taken


async def test_cli_provider_list_falls_back_too(monkeypatch):
    answers = [{"gemini-3.8-flash": "Gemini 3.8 Flash"}, ListUnavailable("agy models timed out")]

    async def fake_models(name, local=False):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(cli_manage, "cli_models", fake_models)
    prov = get_provider(cli_manage.AGY)
    await models.model_list(prov, httpx.AsyncClient())
    r = await models.model_list(prov, httpx.AsyncClient(), refresh=True)
    assert r["models"] == ["gemini-3.8-flash"] and r["stale"] and r["labels"] == {"gemini-3.8-flash": "Gemini 3.8 Flash"}


async def test_api_models_carries_stale_fetched_at_and_listed_levels(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    flaky = Flaky(httpx.Response(200, json=OPENROUTER), httpx.Response(500))
    mc = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    async with Running(create_app(settings(), tools=[], stream_fn=scripted(), models_client=mc)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = (await c.get("/api/models", params={"provider": "openrouter"})).json()
        assert r["stale"] is False and r["fetched_at"] and r["error"] is None
        assert r["info"]["openai/gpt-6.1"]["effort_levels"] == ["low", "medium", "high"]
        assert r["info"]["meta-llama/llama-5-70b"]["effort_levels"] == []
        assert r["info"]["openai/gpt-6.1"]["context"] == 400000
        models._good.clear(); models._cache.clear(); effort._listed.clear()   # as after a restart: only the saved copy is left
        r2 = (await c.get("/api/models", params={"provider": "openrouter", "refresh": "true"})).json()
        assert r2["stale"] is True and r2["models"] == r["models"] and r2["fetched_at"] == r["fetched_at"]
        assert r2["info"]["meta-llama/llama-5-70b"]["effort_levels"] == []


# ---------------------------------------------------------------- capabilities from the listings (samples in each provider's documented shape)

CODEX = {"models": [
    {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "default_reasoning_level": "medium", "visibility": "list",
     "supported_in_api": True, "priority": 1, "context_window": 372000, "max_context_window": 1000000,
     "supported_reasoning_levels": [{"effort": "low", "description": "Fast"}, {"effort": "medium", "description": "Balanced"},
                                    {"effort": "high", "description": "Deep"}, {"effort": "xhigh", "description": "Deepest"}]},
    {"slug": "codex-mini-fast", "display_name": "Codex Mini", "visibility": "list", "supported_in_api": True, "priority": 2,
     "supported_reasoning_levels": []},
]}

ANTHROPIC = {"data": [
    {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5", "max_input_tokens": 1000000, "max_tokens": 128000,
     "capabilities": {"effort": {"supported": True, "low": {"supported": True}, "medium": {"supported": True},
                                 "high": {"supported": True}, "xhigh": {"supported": True}, "max": {"supported": True}},
                      "thinking": {"supported": True, "types": {"adaptive": {"supported": True}}}}},
    {"type": "model", "id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5", "max_input_tokens": 200000, "max_tokens": 64000,
     "capabilities": {"effort": {"supported": False, "low": {"supported": False}, "medium": {"supported": False},
                                 "high": {"supported": False}, "max": {"supported": False}, "xhigh": None}}},
    {"type": "model", "id": "claude-old", "display_name": "Claude Old", "capabilities": None},
], "has_more": False, "first_id": "claude-opus-5-5", "last_id": "claude-old"}

OPENROUTER = {"data": [
    {"id": "openai/gpt-6.1", "name": "OpenAI: GPT-6.1", "context_length": 400000,
     "top_provider": {"context_length": 400000, "max_completion_tokens": 128000},
     "supported_parameters": ["tools", "reasoning", "include_reasoning", "max_tokens"]},
    {"id": "meta-llama/llama-5-70b", "name": "Meta: Llama 5 70B", "context_length": 131072,
     "top_provider": {"max_completion_tokens": 16384}, "supported_parameters": ["tools", "temperature"]},
]}

GEMINI = {"models": [
    {"name": "models/gemini-3.8-pro", "displayName": "Gemini 3.8 Pro", "inputTokenLimit": 1048576, "outputTokenLimit": 65536,
     "supportedGenerationMethods": ["generateContent"], "thinking": True},
    {"name": "models/gemini-2.0-flash-lite", "displayName": "Gemini 2.0 Flash-Lite", "inputTokenLimit": 1048576,
     "outputTokenLimit": 8192, "thinking": False},
    {"name": "models/text-embedding-005", "displayName": "Text Embedding", "inputTokenLimit": 2048},
]}


async def listed(provider, payload, monkeypatch, env=None):
    if env:
        monkeypatch.setenv(env, "k")
    c = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    return await models.list_models(get_provider(provider), c)


async def test_codex_levels_and_windows_come_from_its_listing(codex_login, monkeypatch):
    assert await listed("openai-codex", CODEX, monkeypatch) == ["codex-mini-fast", "gpt-6.1-sol"]
    assert effort.levels("openai-codex", "gpt-6.1-sol") == ("low", "medium", "high", "xhigh")
    assert effort.request_extra("openai-codex", "xhigh", "gpt-6.1-sol") == {"reasoning": {"effort": "xhigh"}}
    assert effort.request_extra("openai-codex", "high", "codex-mini-fast") == {}          # its listing says: no levels
    assert effort.levels("openai-codex", "unlisted") == ("low", "medium", "high")          # the name-free fallback
    assert windows.provider_windows("openai-codex")["gpt-6.1-sol"] == (372000, None)


async def test_anthropic_levels_windows_and_names_come_from_its_listing(monkeypatch):
    seen = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    c = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: seen.append(dict(req.url.params)) or httpx.Response(200, json=ANTHROPIC)))
    await models.list_models(get_provider("anthropic"), c)
    assert seen == [{"limit": "1000"}]                                                     # not just the first 20
    assert effort.levels("anthropic", "claude-opus-5-5") == ("low", "medium", "high", "xhigh", "max")
    assert effort.request_extra("anthropic", "xhigh", "claude-opus-5-5") == {"output_config": {"effort": "xhigh"}}
    assert effort.request_extra("anthropic", "high", "claude-haiku-4-5") == {}
    assert effort.request_extra("anthropic", "high", "claude-old") == {} and effort.levels("anthropic") == ()
    w = windows.provider_windows("anthropic")
    assert w["claude-opus-5-5"] == (1000000, 128000) and w["claude-haiku-4-5"] == (200000, 64000)
    assert models.labels("anthropic")["claude-opus-5-5"] == "Claude Opus 5.5"


async def test_openrouter_reasoning_support_comes_from_supported_parameters(monkeypatch):
    await listed("openrouter", OPENROUTER, monkeypatch, "OPENROUTER_API_KEY")
    assert effort.request_extra("openrouter", "max", "openai/gpt-6.1") == {"reasoning": {"effort": "high"}}
    assert effort.request_extra("openrouter", "high", "meta-llama/llama-5-70b") == {}       # would have been sent before
    assert windows.provider_windows("openrouter")["meta-llama/llama-5-70b"] == (131072, 16384)


async def test_gemini_thinking_flag_comes_from_its_listing(monkeypatch):
    await listed("gemini", GEMINI, monkeypatch, "GEMINI_API_KEY")
    assert effort.levels("gemini", "gemini-3.8-pro") == ("low", "medium", "high")
    assert effort.request_extra("gemini", "high", "gemini-2.0-flash-lite") == {}
    assert effort.levels("gemini", "text-embedding-005") == ("low", "medium", "high")      # says nothing: the default stays
    assert windows.provider_windows("gemini")["gemini-3.8-pro"] == (1048576, 65536)
    assert models.labels("gemini")["gemini-3.8-pro"] == "Gemini 3.8 Pro"


def test_listings_without_reasoning_fields_change_nothing():
    for provider, row in (("openai", {"id": "gpt-6.1", "object": "model", "owned_by": "openai"}),
                          ("xai", {"id": "grok-5", "owned_by": "xai"}),
                          ("openrouter", {"id": "x/y"}), ("anthropic", {"id": "c", "capabilities": None})):
        assert effort.levels_from_listing(provider, row) is None


# ---------------------------------------------------------------- CLI sidecars going stale

async def test_cli_status_says_when_a_newer_cli_is_out_and_the_owner_hears_once_after_three_days(monkeypatch):
    async def status(name):
        if name == cli_manage.CLAUDE:
            return {"installed": True, "installable": False, "logged_in": True, "version": "2.1.300 (Claude Code)",
                    "update_hint": "Update to the latest: assistant update --cli claude", "hint": ""}
        return {"installed": False, "installable": False, "logged_in": None, "hint": ""}

    monkeypatch.setattr(cli_manage, "cli_status", status)
    releases.set_fetcher(fetcher({"claude": "2.1.319", "agy": "1.5.3"}))
    app = create_app(settings(unofficial_providers=True), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await releases.latest("claude")
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        cli = provs[cli_manage.CLAUDE]["cli"]
        assert cli["update_available"] is True and cli["latest_version"] == "2.1.319"
        check = app.state.check_cli_updates
        now = time.time()
        assert await check(now) == []                       # just noticed
        assert await check(now + 2 * 86400) == []
        assert await check(now + 3 * 86400 + 1) == [cli_manage.CLAUDE]
        assert await check(now + 4 * 86400) == []           # once per version
        notes = (await c.get("/api/notifications")).json()["items"]
        assert [n["type"] for n in notes] == ["cli_update"]
        assert notes[0]["provider"] == cli_manage.CLAUDE       # the apps open My → Model at this CLI's box
        assert "2.1.319" in notes[0]["body"] and "assistant update" in notes[0]["body"] and "Claude Code" in notes[0]["title"]
