"""Custom endpoints (base URL typed in the app) and provider balances in /api/usage."""

import json
import socket

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from assistant.providers import get_provider
from assistant.providers.registry import clear_stored_base_urls, normalize_base_url, set_stored_base_url
from assistant.server import create_app
from assistant.store import Store

from test_server import AUTH, Running, read_events, scripted, settings


# ---------- normalizing what the user types ----------

@pytest.mark.parametrize("typed,stored", [
    ("http://192.168.0.10:11434/v1/", "http://192.168.0.10:11434/v1"),
    ("  HTTPS://Host.Docker.Internal:1234/v1//  ", "https://host.docker.internal:1234/v1"),
    ("http://localhost:11434", "http://localhost:11434"),
    ("http://[::1]:8000/v1", "http://[::1]:8000/v1"),
    ("http://100.64.1.2/api/v1", "http://100.64.1.2/api/v1"),
])
def test_base_url_is_normalized_and_lan_addresses_are_fine(typed, stored):
    assert normalize_base_url(typed) == stored


@pytest.mark.parametrize("typed,why", [
    ("", "required"), ("ftp://h/v1", "http"), ("192.168.0.10:11434", "http"), ("http://", "host"),
    ("http://user:pw@h/v1", "password"), ("http://key@h/v1", "password"), ("http://h/a b", "spaces"),
    ("http://h/v\x001", None), ("http://h/v\t1", "spaces"), ("http://h:99999", "port"), ("http://h/v1?x=1", "query"), ("http://h/v1#f", "fragment"),
    ("http://h/" + "a" * 300, "too long"), ("http://169.254.169.254/latest", "link-local"), (123, "required"),
])
def test_bad_base_urls_are_refused_with_a_reason(typed, why):
    with pytest.raises(ValueError) as e:
        normalize_base_url(typed)
    if why:
        assert why in str(e.value)


def test_base_url_order_explicit_then_stored_then_env_then_data_file():
    lm = get_provider("lmstudio")
    assert lm.takes_url and get_provider("custom").takes_url and get_provider("azure-foundry").takes_url
    assert not get_provider("openrouter").takes_url and not get_provider("openai").takes_url
    env = {"LM_BASE_URL": "http://env-host:1234/v1/"}
    assert lm.resolve_base_url(env={}) == ""
    assert lm.resolve_base_url(env=env) == "http://env-host:1234/v1"
    set_stored_base_url("lmstudio", "http://stored:1234/v1")
    assert lm.resolve_base_url(env=env) == "http://stored:1234/v1"
    assert lm.resolve_base_url("http://explicit/v1/", env=env) == "http://explicit/v1"
    set_stored_base_url("openrouter", "http://evil/v1")              # never applies to a provider with a fixed address
    assert get_provider("openrouter").resolve_base_url(env={}) == "https://openrouter.ai/api/v1"
    clear_stored_base_urls()
    assert lm.resolve_base_url(env=env) == "http://env-host:1234/v1"


# ---------- a fake OpenAI-compatible server on loopback (a private address, as a LAN Ollama would be) ----------

def fake_openai(seen, *, models_status=200):
    app = FastAPI()

    @app.get("/v1/models")
    async def models(request: Request):
        seen.append(("models", request.headers.get("authorization")))
        if models_status != 200:
            return JSONResponse({"error": "boom"}, status_code=models_status)
        return {"object": "list", "data": [{"id": "qwen3:8b", "object": "model"}, {"id": "llama3.2", "object": "model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        seen.append(("chat", request.headers.get("authorization"), body["model"], body["stream"]))

        async def gen():
            for part in ("Hello", " from", " LAN"):
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": part}}]}) + "\n\n"
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 3}}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_custom_endpoint_contract_models_and_streaming_chat(tmp_path, monkeypatch):
    from assistant.models import _cache
    _cache.clear()
    monkeypatch.delenv("LM_BASE_URL", raising=False)
    seen = []
    db = str(tmp_path / "a.db")
    async with Running(fake_openai(seen)) as fake:
        base = f"{fake.url}/v1"
        app = create_app(settings(db_path=db), tools=[])      # the real provider client: no scripted stream
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
            assert (await c.put("/api/providers/custom/endpoint", json={"base_url": base})).status_code == 401
            c.headers.update(AUTH)
            provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
            cu = provs["custom"]
            assert cu["takes_url"] is True and cu["base_url"] == "" and cu["configured"] is False and cu["takes_key"] is True
            assert provs["openrouter"]["takes_url"] is False and "base_url" not in provs["openrouter"]
            r = (await c.get("/api/models", params={"provider": "custom"})).json()
            assert r["models"] == [] and "base URL" in r["error"]

            for bad in ({"base_url": "ftp://x"}, {"base_url": "http://u:p@h"}, {}, {"base_url": 5}, {"base_url": "http://h/ v1"}):
                res = await c.put("/api/providers/custom/endpoint", json=bad)
                assert res.status_code == 422 and isinstance(res.json()["detail"], str)
            assert (await c.put("/api/providers/openrouter/endpoint", json={"base_url": base})).status_code == 400
            assert (await c.put("/api/providers/nope/endpoint", json={"base_url": base})).status_code == 404
            assert (await c.delete("/api/providers/openrouter/endpoint")).status_code == 400

            res = await c.put("/api/providers/custom/endpoint", json={"base_url": base + "/"})
            assert res.status_code == 200 and res.json() == {"ok": True, "base_url": base}
            cu = next(p for p in (await c.get("/api/providers")).json() if p["name"] == "custom")
            assert cu["base_url"] == base and cu["configured"] is True and cu["key_source"] is None

            r = (await c.get("/api/models", params={"provider": "custom"})).json()
            assert r == {"provider": "custom", "models": ["llama3.2", "qwen3:8b"], "labels": {"llama3.2": "Llama3.2", "qwen3:8b": "Qwen3 8B"}, "error": None,
                         "info": {"llama3.2": {"images": False, "context": 131072}, "qwen3:8b": {"images": False, "context": 131072}},
                         "stale": False, "fetched_at": r["fetched_at"], "retry_in": None}
            assert seen == [("models", None)]                              # keyless: no Authorization header at all
            await c.get("/api/models", params={"provider": "custom"})
            assert len(seen) == 1                                          # cached
            await c.get("/api/models", params={"provider": "custom", "refresh": "true"})
            assert len(seen) == 2

            await c.put("/api/profile", json={"provider": "custom", "model": "qwen3:8b"})
            async with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}) as resp:
                events = await read_events(resp)
            assert "".join(e.get("text", "") for e in events if e["type"] == "text") == "Hello from LAN"
            assert events[-1]["type"] == "done"
            assert seen[-1] == ("chat", None, "qwen3:8b", True)

            # with a key: sent as a Bearer token to this endpoint only
            assert (await c.put("/api/providers/custom/key", json={"api_key": "sk-local-12345678"})).json() == {"ok": True}
            r = (await c.get("/api/models", params={"provider": "custom"})).json()   # a new key clears the list cache
            assert r["error"] is None and seen[-1] == ("models", "Bearer sk-local-12345678")
            async with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}) as resp:
                await read_events(resp)
            assert seen[-1][:2] == ("chat", "Bearer sk-local-12345678")
            listing = (await c.get("/api/providers")).text
            assert "sk-local-12345678" not in listing

        # stored in the database: a restarted server still knows it
        clear_stored_base_urls()
        assert Store(db).meta("base_url:custom") == base
        async with Running(create_app(settings(db_path=db), tools=[])) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            cu = next(p for p in (await c.get("/api/providers")).json() if p["name"] == "custom")
            assert cu["base_url"] == base and cu["configured"] is True
            res = await c.delete("/api/providers/custom/endpoint")
            assert res.status_code == 200 and res.json() == {"ok": True, "base_url": ""}
            cu = next(p for p in (await c.get("/api/providers")).json() if p["name"] == "custom")
            assert cu["base_url"] == "" and cu["configured"] is False
            assert (await c.get("/api/models", params={"provider": "custom"})).json()["error"]
            assert (await c.delete("/api/providers/custom/endpoint")).status_code == 200   # clearing twice is fine
        assert Store(db).meta("base_url:custom") == ""


async def test_lmstudio_endpoint_env_fallback_and_errors_as_text(monkeypatch):
    from assistant.models import _cache
    _cache.clear()
    monkeypatch.setenv("LM_BASE_URL", "http://env-only.invalid:1/v1")
    seen = []
    async with Running(fake_openai(seen, models_status=500)) as fake, \
            Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        res = await c.put("/api/providers/custom:lmstudio/endpoint", json={"base_url": f"{fake.url}/v1"})
        assert res.json()["base_url"] == f"{fake.url}/v1"
        r = (await c.get("/api/models", params={"provider": "lmstudio"})).json()
        assert r["models"] == [] and "HTTP 500" in r["error"]
        res = await c.delete("/api/providers/lmstudio/endpoint")
        assert res.json() == {"ok": True, "base_url": "http://env-only.invalid:1/v1"}   # the environment's value applies again
        await c.put("/api/providers/lmstudio/endpoint", json={"base_url": f"http://127.0.0.1:{closed_port()}/v1"})
        r = (await c.get("/api/models", params={"provider": "lmstudio", "refresh": "true"})).json()
        assert r["models"] == [] and "Could not reach" in r["error"]


async def test_a_profile_naming_a_removed_provider_is_reset_at_start(tmp_path):
    db = tmp_path / "a.db"
    Store(db).set_profile(provider="opencode-zen", model="x", fallback_provider="minimax-cn", fallback_model="y")
    async with Running(create_app(settings(db_path=str(db)), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        p = (await c.get("/api/profile")).json()
        assert (p["provider"], p["model"], p["fallback_provider"], p["fallback_model"]) == ("", "", "", "")
    Store(db).set_profile(provider="antigravity-oauth", model="gemini-3-flash", fallback_provider="openai", fallback_model="gpt-5")
    async with Running(create_app(settings(db_path=str(db)), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        p = (await c.get("/api/profile")).json()
        assert (p["provider"], p["fallback_provider"]) == ("antigravity-oauth", "openai")   # kept providers are untouched


# ---------- balances ----------

def public_resolver(host, port, *a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.2.3", port))]


class FakeProviders:
    """openrouter.ai and api.deepseek.com as netguard reaches them (by address, with the Host header)."""

    def __init__(self):
        self.calls = []
        self.key_status = 200
        self.credits_status = 403

    def __call__(self, request):
        host, path, auth = request.headers["host"], request.url.path, request.headers.get("authorization")
        self.calls.append((host, path, auth))
        if host == "openrouter.ai" and path == "/api/v1/key":
            if self.key_status != 200:
                return httpx.Response(self.key_status, json={"error": {"message": "nope"}})
            return httpx.Response(200, json={"data": {
                "label": "sk-or-v1-abc...xyz", "limit": 20, "limit_reset": "monthly", "limit_remaining": 12.5,
                "include_byok_in_limit": False, "usage": 31.2, "usage_daily": 0.25, "usage_weekly": 1.75, "usage_monthly": 7.5,
                "byok_usage": 0, "is_free_tier": False}})
        if host == "openrouter.ai" and path == "/api/v1/credits":
            if self.credits_status != 200:
                return httpx.Response(self.credits_status, json={"error": {"message": "Only management keys can perform this operation"}})
            return httpx.Response(200, json={"data": {"total_credits": 100.5, "total_usage": 25.75}})
        if host == "api.deepseek.com" and path == "/user/balance":
            return httpx.Response(200, json={"is_available": True, "balance_infos": [
                {"currency": "CNY", "total_balance": "110.00", "granted_balance": "10.00", "topped_up_balance": "100.00"},
                {"currency": "USD", "total_balance": "4.20", "granted_balance": "0.00", "topped_up_balance": "4.20"}]})
        return httpx.Response(404)


FIELDS = {"provider", "display_name", "currency", "balance", "used_today", "used_week", "used_month", "limit",
          "limit_remaining", "error", "checked_at"}


async def test_usage_accounts_only_for_the_main_provider(monkeypatch):
    for var in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    fake = FakeProviders()
    app = create_app(settings(), tools=[], stream_fn=scripted(), balance_transport=httpx.MockTransport(fake),
                     balance_resolver=public_resolver)
    or_key, ds_key, groq_key = "sk-or-v1-SECRETOR9999", "sk-SECRETDEEPSEEK8888", "gsk_SECRETGROQ7777"
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        u = (await c.get("/api/usage")).json()
        assert u["accounts"] == [] and fake.calls == []                   # "p" is not a real provider: nothing to ask

        await c.put("/api/profile", json={"provider": "openrouter", "model": "a/b", "fallback_provider": "mistral", "fallback_model": "m"})
        u = (await c.get("/api/usage")).json()
        rows = {a["provider"]: a for a in u["accounts"]}
        assert list(rows) == ["openrouter"] and fake.calls == []          # only the main provider (the fallback and others stay out)
        assert "API key" in rows["openrouter"]["error"] and rows["openrouter"]["balance"] is None

        for name, key in (("openrouter", or_key), ("deepseek", ds_key), ("groq", groq_key)):
            assert (await c.put(f"/api/providers/{name}/key", json={"api_key": key})).status_code == 200
        res = await c.get("/api/usage")
        for key in (or_key, ds_key, groq_key):
            assert key not in res.text
        rows = {a["provider"]: a for a in res.json()["accounts"]}
        assert list(rows) == ["openrouter"]                                # deepseek and groq have keys but are not the main model
        o = rows["openrouter"]
        assert set(o) == FIELDS | {"role"} and o["role"] == "main" and o["display_name"] == "OpenRouter" and o["currency"] == "USD" and o["error"] is None
        assert (o["used_today"], o["used_week"], o["used_month"], o["limit"], o["limit_remaining"]) == (0.25, 1.75, 7.5, 20, 12.5)
        assert o["balance"] is None                                        # an ordinary key may not read /credits
        assert o["checked_at"].endswith("Z")
        assert {(h, p) for h, p, _ in fake.calls} == {("openrouter.ai", "/api/v1/key"), ("openrouter.ai", "/api/v1/credits")}
        assert all(auth == f"Bearer {or_key}" for h, _, auth in fake.calls if h == "openrouter.ai")

        n = len(fake.calls)
        await c.get("/api/usage")
        assert len(fake.calls) == n                                        # cached for a minute
        await c.get("/api/usage", params={"refresh": "true"})
        assert ("openrouter.ai", "/api/v1/credits") not in {(h, p) for h, p, _ in fake.calls[n:]}   # 403 once: not asked again
        assert len(fake.calls) == n + 1

        fake.credits_status = 200                                          # a management key can read the account balance
        await c.put("/api/providers/openrouter/key", json={"api_key": "sk-or-v1-MGMTKEY55555"})   # new key: new cache entry
        o = next(a for a in (await c.get("/api/usage")).json()["accounts"] if a["provider"] == "openrouter")
        assert o["balance"] == 74.75 and o["error"] is None

        fake.key_status = 401
        o = next(a for a in (await c.get("/api/usage", params={"refresh": "true"})).json()["accounts"] if a["provider"] == "openrouter")
        assert "HTTP 401" in o["error"] and o["used_today"] is None and o["balance"] is None


async def test_balance_requests_keep_netguard_rules():
    from assistant import balances
    prov = get_provider("openrouter")

    def private(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    fake = FakeProviders()
    r = await balances.account(prov, "sk-x-12345678", transport=httpx.MockTransport(fake), resolver=private)
    assert fake.calls == [] and "Could not reach" in r["error"]           # a name that resolves inward is refused


# ---------- providers whose chat key can also read balance / spend ----------

class FakeMore:
    """api.novita.ai, ollama.com, api.together.ai, api.fireworks.ai and api.deepinfra.com as netguard reaches them."""

    def __init__(self, today):
        self.calls = []
        self.today = today
        self.status = {}

    def __call__(self, request):
        host, path, q = request.headers["host"], request.url.path, dict(request.url.params)
        self.calls.append((host, path, q, request.headers.get("authorization")))
        st = self.status.get((host, path))
        if st:
            return httpx.Response(st, json={"error": "nope"})
        day, month = self.today.strftime("%Y-%m-%d"), self.today.strftime("%Y-%m")
        if (host, path) == ("api.novita.ai", "/openapi/v1/billing/balance/detail"):
            return httpx.Response(200, json={"availableBalance": "1234500", "cashBalance": "800000", "creditLimit": "0",
                                             "pendingCharges": "0", "outstandingInvoices": "0"})
        if (host, path) == ("ollama.com", "/api/balance"):
            return httpx.Response(200, json={"included": {"balance_usd": 72.5, "allowance_usd": 100,
                                                          "period": {"from": "x", "until": "y"}}, "purchased": {"balance_usd": 25}})
        if (host, path) == ("ollama.com", "/api/usage"):
            assert q == {"range": "30d"}
            return httpx.Response(200, json={"range": "30d", "buckets": [
                {"from": "1999-01-01T00:00:00Z", "usage_usd": 9.0},
                {"from": f"{day}T00:00:00Z", "usage_usd": 0.5, "partial": True}]})
        if (host, path) == ("api.together.ai", "/v1/billing/usage"):
            assert q["month"] == month and q["granularity"] == "day"
            return httpx.Response(200, json={"object": "list", "currency": "USD", "next_cursor": None, "data": [
                {"date": f"{month}-01" if day != f"{month}-01" else "1999-01-01", "line_items": [{"cost": "1.25"}, {"cost": "0.75"}]},
                {"date": day, "line_items": [{"cost": "0.10"}]}]})
        if (host, path) == ("api.fireworks.ai", "/v1/accounts"):
            return httpx.Response(200, json={"accounts": [{"name": "accounts/my-acct"}]})
        if (host, path) == ("api.fireworks.ai", "/v1/accounts/my-acct/billing/summary"):
            assert q["granularity"] == "DAILY" and q["startTime"] == f"{month}-01T00:00:00Z"
            return httpx.Response(200, json={
                "lineItems": [{"totalCost": {"currencyCode": "USD", "units": "3", "nanos": 500000000}},
                              {"totalCost": {"currencyCode": "USD", "units": "0", "nanos": 250000000}}],
                "usageBuckets": [{"startTime": f"{day}T00:00:00Z",
                                  "lineItems": [{"totalCost": {"currencyCode": "USD", "units": "1", "nanos": 0}}]}]})
        if (host, path) == ("api.deepinfra.com", "/payment/usage"):
            assert q == {"from": self.today.strftime("%Y.%m")}
            return httpx.Response(200, json={"initial_month": "2024.01", "months": [
                {"period": self.today.strftime("%Y.%m"), "items": [], "total_cost": 1234, "interval": {}}]})
        if (host, path) == ("api.deepinfra.com", "/payment/config"):
            return httpx.Response(200, json={"limit": 50})
        return httpx.Response(404)


async def test_more_providers_report_balance_and_spend_with_the_chat_key():
    from datetime import datetime, timezone
    from assistant import balances
    fake = FakeMore(datetime.now(timezone.utc))
    tr = httpx.MockTransport(fake)
    key = "sk-SECRET-CHATKEY-4242"

    async def one(name):
        r = await balances.account(get_provider(name), key, transport=tr, resolver=public_resolver)
        assert set(r) == FIELDS and key not in json.dumps(r)
        return r

    r = await one("novita")
    assert (r["balance"], r["currency"], r["error"]) == (123.45, "USD", None)
    r = await one("ollama-cloud")
    assert (r["balance"], r["limit"], r["limit_remaining"], r["used_today"], r["error"]) == (97.5, 100, 72.5, 0.5, None)
    assert r["used_month"] == 0.5 and r["used_week"] == 0.5          # the 1999 bucket is outside both
    r = await one("together")
    assert (r["used_today"], r["used_month"], r["error"]) == (0.1, 2.1, None)
    r = await one("fireworks")
    assert (r["used_today"], r["used_month"], r["currency"], r["error"]) == (1.0, 3.75, "USD", None)
    r = await one("deepinfra")
    assert (r["used_month"], r["limit"], r["balance"], r["error"]) == (12.34, 50, None, None)

    hosts = {"api.novita.ai", "ollama.com", "api.together.ai", "api.fireworks.ai", "api.deepinfra.com"}
    assert {h for h, *_ in fake.calls} == hosts                      # only each provider's own fixed host
    assert all(auth == f"Bearer {key}" for *_, auth in fake.calls)

    n = len([c for c in fake.calls if c[1] == "/v1/accounts"])
    await one("fireworks")
    assert len([c for c in fake.calls if c[1] == "/v1/accounts"]) == n   # the account id is remembered per key


async def test_more_providers_errors_are_short_and_honest():
    from datetime import datetime, timezone
    from assistant import balances
    fake = FakeMore(datetime.now(timezone.utc))
    tr = httpx.MockTransport(fake)
    fake.status[("api.deepinfra.com", "/payment/usage")] = 403      # a new DeepInfra key has no billing:read scope
    r = await balances.account(get_provider("deepinfra"), "k-1", transport=tr, resolver=public_resolver)
    assert "billing access" in r["error"] and r["used_month"] is None
    fake.status[("api.together.ai", "/v1/billing/usage")] = 404    # the usage report is not turned on for this organization
    r = await balances.account(get_provider("together"), "k-1", transport=tr, resolver=public_resolver)
    assert "report its balance" in r["error"]
    fake.status[("api.novita.ai", "/openapi/v1/billing/balance/detail")] = 401
    r = await balances.account(get_provider("novita"), "k-1", transport=tr, resolver=public_resolver)
    assert "HTTP 401" in r["error"] and r["balance"] is None
    fake.status[("ollama.com", "/api/usage")] = 429                 # spend is optional; the balance still shows
    r = await balances.account(get_provider("ollama-cloud"), "k-1", transport=tr, resolver=public_resolver)
    assert r["balance"] == 97.5 and r["used_today"] is None and r["error"] is None
    r = await balances.account(get_provider("groq"), "k-1", transport=tr, resolver=public_resolver)
    assert r["error"] == "This service doesn't report its balance." and r["currency"] is None


async def test_more_providers_never_use_a_stored_base_url():
    from datetime import datetime, timezone
    from assistant import balances
    fake = FakeMore(datetime.now(timezone.utc))
    set_stored_base_url("together", "https://evil.example/v1")
    try:
        await balances.account(get_provider("together"), "k-1", transport=httpx.MockTransport(fake), resolver=public_resolver)
    finally:
        clear_stored_base_urls()
    assert {h for h, *_ in fake.calls} == {"api.together.ai"}


async def test_custom_endpoint_by_container_name_sends_tools_and_gets_tool_calls_back():
    """The free set-up in the README: FreeLLMAPI joined to Kelo's Docker network, typed in as http://freellmapi:3001/v1 with its
    unified key and the model "auto". Tools go out in the OpenAI shape and streamed tool calls come back as one call."""
    from assistant.providers import ChatRequest, Done, ToolCall, stream_chat

    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers.get("authorization"), body=json.loads(request.content))
        chunks = [{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                                      "function": {"name": "web_search", "arguments": ""}}]}}]},
                  {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"query": "weather"}'}}]}}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    tools = [{"type": "function", "function": {"name": "web_search", "description": "Search the web",
                                               "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}]
    set_stored_base_url("custom", normalize_base_url("http://freellmapi:3001/v1/"))
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            events = [e async for e in stream_chat("custom", ChatRequest(model="auto", messages=[{"role": "user", "content": "hi"}],
                                                                         tools=tools), api_key="freellmapi-test", client=client)]
    finally:
        clear_stored_base_urls()
    assert seen["url"] == "http://freellmapi:3001/v1/chat/completions" and seen["auth"] == "Bearer freellmapi-test"
    assert seen["body"]["model"] == "auto" and seen["body"]["stream"] is True and seen["body"]["tools"] == tools
    assert [e for e in events if isinstance(e, ToolCall)] == [ToolCall("call_1", "web_search", '{"query": "weather"}')]
    assert events[-1] == Done("tool_calls")


async def test_named_custom_endpoints(tmp_path):
    import re
    from assistant.providers.registry import set_user_endpoints
    set_user_endpoints([])
    clear_stored_base_urls()

    db = str(tmp_path / "endpoints.db")
    app = create_app(settings(db_path=db), tools=[])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        # POST two endpoints
        r1 = await c.post("/api/providers/custom-endpoints", json={"name": "Home Ollama", "base_url": "http://192.168.0.10:11434/v1"})
        assert r1.status_code == 201
        d1 = r1.json()
        assert d1["id"] == "custom-home-ollama" and d1["name"] == "Home Ollama"
        assert d1["base_url"] == "http://192.168.0.10:11434/v1" and d1["transport"] == "chat_completions"

        r2 = await c.post("/api/providers/custom-endpoints", json={"name": "Work vLLM", "base_url": "http://10.0.0.5:8000/v1"})
        assert r2.status_code == 201
        d2 = r2.json()
        assert d2["id"] == "custom-work-vllm" and d2["name"] == "Work vLLM"

        # Both are listed with their display names and user_endpoint true
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert "custom-home-ollama" in provs
        assert provs["custom-home-ollama"]["display_name"] == "Home Ollama"
        assert provs["custom-home-ollama"]["user_endpoint"] is True
        assert provs["custom-home-ollama"]["base_url"] == "http://192.168.0.10:11434/v1"

        assert "custom-work-vllm" in provs
        assert provs["custom-work-vllm"]["display_name"] == "Work vLLM"
        assert provs["custom-work-vllm"]["user_endpoint"] is True
        assert provs["custom-work-vllm"]["base_url"] == "http://10.0.0.5:8000/v1"

        # get_provider("custom-home-ollama") resolves and its base URL is the stored one
        p_home = get_provider("custom-home-ollama")
        assert p_home.display_name == "Home Ollama"
        assert p_home.resolve_base_url() == "http://192.168.0.10:11434/v1"

        # Name taken -> 409
        r_taken = await c.post("/api/providers/custom-endpoints", json={"name": "home ollama", "base_url": "http://1.2.3.4:8000/v1"})
        assert r_taken.status_code == 409 and r_taken.json()["code"] == "name_taken"

        # Built-in custom display name taken -> 409
        r_taken_custom = await c.post("/api/providers/custom-endpoints", json={"name": "custom", "base_url": "http://1.2.3.4:8000/v1"})
        assert r_taken_custom.status_code == 409 and r_taken_custom.json()["code"] == "name_taken"

        # Korean-only name gets a hex id
        r_ko = await c.post("/api/providers/custom-endpoints", json={"name": "집", "base_url": "http://192.168.0.20:11434/v1"})
        assert r_ko.status_code == 201
        d_ko = r_ko.json()
        assert d_ko["id"].startswith("custom-") and len(d_ko["id"]) == len("custom-") + 6
        assert re.fullmatch(r"custom-[0-9a-f]{6}", d_ko["id"])

        # 21st -> 409 too_many
        # Currently we have 3 (Home Ollama, Work vLLM, 집). Add 17 more to reach 20.
        for i in range(4, 21):
            res = await c.post("/api/providers/custom-endpoints", json={"name": f"EP {i}", "base_url": f"http://10.0.0.{i}:8000/v1"})
            assert res.status_code == 201
        r_21 = await c.post("/api/providers/custom-endpoints", json={"name": "EP 21", "base_url": "http://10.0.0.21:8000/v1"})
        assert r_21.status_code == 409 and r_21.json()["code"] == "too_many"

        # PATCH rename and address
        r_patch = await c.patch("/api/providers/custom-endpoints/custom-home-ollama",
                                json={"name": "Home Ollama Renamed", "base_url": "http://192.168.0.11:11434/v1"})
        assert r_patch.status_code == 200
        assert r_patch.json()["name"] == "Home Ollama Renamed"
        assert r_patch.json()["base_url"] == "http://192.168.0.11:11434/v1"
        assert get_provider("custom-home-ollama").display_name == "Home Ollama Renamed"
        assert get_provider("custom-home-ollama").resolve_base_url() == "http://192.168.0.11:11434/v1"

        # Set as main model
        await c.put("/api/profile", json={"provider": "custom-home-ollama", "model": "llama3"})

        # DELETE: 409 in_use while it is the main model
        r_del_inuse = await c.delete("/api/providers/custom-endpoints/custom-home-ollama")
        assert r_del_inuse.status_code == 409 and r_del_inuse.json()["code"] == "in_use"

        # Main = one endpoint, fallback = another on a different host -> accepted
        r_diff_host = await c.put("/api/profile", json={"fallback_provider": "custom-work-vllm", "fallback_model": "mistral"})
        assert r_diff_host.status_code == 200
        assert r_diff_host.json()["fallback_provider"] == "custom-work-vllm"

        # Clear fallback first, then patch Work vLLM to have same host as Home Ollama (192.168.0.11)
        await c.put("/api/profile", json={"fallback_provider": "", "fallback_model": ""})
        await c.patch("/api/providers/custom-endpoints/custom-work-vllm", json={"base_url": "http://192.168.0.11:9000/v1"})
        # Trying to set fallback to same host -> refused by the rule (422)
        r_same_host = await c.put("/api/profile", json={"fallback_provider": "custom-work-vllm", "fallback_model": "mistral"})
        assert r_same_host.status_code == 422
        assert "different provider than the main model" in r_same_host.json()["detail"]

        # Switch main model away from custom-home-ollama, then DELETE removes it
        await c.put("/api/profile", json={"provider": "openai", "model": "gpt-4o", "fallback_provider": "", "fallback_model": ""})
        r_del = await c.delete("/api/providers/custom-endpoints/custom-home-ollama")
        assert r_del.status_code == 200
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert "custom-home-ollama" not in provs

        # Also test PUT /api/providers/custom/name
        r_cname = await c.put("/api/providers/custom/name", json={"name": "My Ollama"})
        assert r_cname.status_code == 200
        provs = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert provs["custom"]["display_name"] == "My Ollama"

    # After a restart (a new create_app on the same database), the endpoints and their addresses are back
    set_user_endpoints([])
    clear_stored_base_urls()
    app2 = create_app(settings(db_path=db), tools=[])
    async with Running(app2) as srv2, httpx.AsyncClient(base_url=srv2.url, headers=AUTH) as c2:
        provs2 = {p["name"]: p for p in (await c2.get("/api/providers")).json()}
        assert "custom-work-vllm" in provs2
        assert provs2["custom-work-vllm"]["display_name"] == "Work vLLM"
        assert provs2["custom-work-vllm"]["base_url"] == "http://192.168.0.11:9000/v1"
        assert provs2["custom-work-vllm"]["user_endpoint"] is True
        assert get_provider("custom-work-vllm").resolve_base_url() == "http://192.168.0.11:9000/v1"
        assert provs2["custom"]["display_name"] == "My Ollama"
