"""Optional admin (billing) keys (backlog 61b, docs/keyring.md): balances.py's readers against recorded answers, then the server and the
REAL key container together (keyring_service.py behind httpx.ASGITransport, a fake provider behind it, as test_keyring_e2e.py does).
The admin key reaches the provider only as a GET on its billing path; the container refuses anything else."""

import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from assistant import balances, keyring_service
from assistant.keyring_client import PLACEHOLDER, KeyringTransport
from assistant.providers import get_provider
from assistant.server import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_server import AUTH, Running, settings  # noqa: E402

TOKEN = "t" * 40
CHAT = "sk-chat-REAL-0042"
ADMIN = "sk-admin-REAL-0042"
NOW = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)
NOV1 = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())
TODAY0 = int(datetime(2026, 11, 14, tzinfo=timezone.utc).timestamp())
COST = {"data": [
    {"starting_at": "2026-11-01T00:00:00Z", "ending_at": "2026-11-02T00:00:00Z", "results": [{"amount": "1234", "currency": "USD"}]},
    {"starting_at": "2026-11-14T00:00:00Z", "ending_at": "2026-11-15T00:00:00Z", "results": [{"amount": "66", "currency": "USD"}]},
], "has_more": False, "next_page": None}


def public(host, port, *a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(balances, "_today", lambda: NOW)


def fake_get(answers):
    """A `get(host, path)` that answers from {path prefix: (status, body)} and records every call."""
    calls = []

    async def get(host, path):
        calls.append((host, path))
        for prefix, answer in answers.items():
            if path.startswith(prefix):
                return answer
        raise AssertionError(f"unexpected {host}{path}")
    return get, calls


# ---------- balances: what each admin key reads (recorded answers, fake get) ----------

async def test_openai_spend_is_summed_for_the_month_and_for_today(today):
    body = {"data": [
        {"start_time": NOV1, "end_time": NOV1 + 86400, "results": [{"amount": {"value": 1.25, "currency": "usd"}}]},
        {"start_time": TODAY0, "end_time": TODAY0 + 86400, "results": [{"amount": {"value": 0.5, "currency": "usd"}}]},
    ], "has_more": False, "next_page": None}
    get, calls = fake_get({"/v1/organization/costs": (200, body)})
    assert await balances._admin_openai(get) == {"used_month": 1.75, "used_today": 0.5}
    assert calls[0] == ("api.openai.com", f"/v1/organization/costs?start_time={NOV1}&bucket_width=1d&limit=31")   # from 00:00 of the 1st


async def test_anthropic_cents_become_dollars_and_only_todays_bucket_counts_as_today(today):
    get, calls = fake_get({"/v1/organizations/cost_report": (200, COST)})
    assert await balances._admin_anthropic(get) == {"used_month": 13.0, "used_today": 0.66}
    assert calls[0] == ("api.anthropic.com", "/v1/organizations/cost_report?starting_at=2026-11-01T00:00:00Z&bucket_width=1d&limit=31")


async def test_openrouter_balance_is_credits_minus_usage(today):
    get, calls = fake_get({"/api/v1/credits": (200, {"data": {"total_credits": 100.5, "total_usage": 25.75}})})
    assert await balances._admin_openrouter(get) == {"balance": 74.75}
    assert calls[0] == ("openrouter.ai", "/api/v1/credits")


async def test_a_refused_admin_key_is_a_refused_balance_error_and_uses_the_anthropic_headers(today):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})

    with pytest.raises(balances.BalanceError) as e:
        await balances.admin_values(get_provider("anthropic"), transport=httpx.MockTransport(handler), resolver=public)
    assert e.value.refused is True
    assert seen[0].headers["x-api-key"] == PLACEHOLDER and seen[0].headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in seen[0].headers


async def test_a_provider_outage_is_not_a_refusal(today):
    get, _ = fake_get({"/v1/organization/costs": (503, None)})
    with pytest.raises(balances.BalanceError) as e:
        await balances._admin_openai(get)
    assert e.value.refused is False


async def test_providers_without_an_admin_key_are_refused_cleanly():
    with pytest.raises(balances.BalanceError):
        await balances.admin_values(get_provider("deepseek"), transport=httpx.MockTransport(lambda r: httpx.Response(200)), resolver=public)


# ---------- the server with the REAL key container ----------

class Provider:
    """The fake Anthropic behind the key container: records what really arrives."""

    def __init__(self, cost_status=200):
        self.seen: list[httpx.Request] = []
        self.cost_status = cost_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.path == "/v1/organizations/cost_report":
            if self.cost_status != 200:
                return reply(self.cost_status, {"error": {"message": "nope"}})
            return reply(200, COST)
        return reply(404, {"error": "no"})


def reply(status: int, body: dict) -> httpx.Response:
    """An answer the way a real provider's arrives: a body still to be read (the container streams it on)."""
    return httpx.Response(status, headers={"content-type": "application/json"}, stream=httpx.ByteStream(json.dumps(body).encode()))


def server(tmp_path, provider):
    ring = keyring_service.create_app(str(tmp_path / "ring"), TOKEN, resolver=public, transport=httpx.MockTransport(provider))
    app = create_app(settings(keyring_url="http://keyring:9500", keyring_token=TOKEN, db_path=str(tmp_path / "s.db")), tools=[],
                     keyring_transport=httpx.ASGITransport(app=ring), balance_resolver=public)
    return app, ring


def container(ring):
    """A client of the key container's own API (as the owner's tools would see it), to read its entries."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=ring), base_url="http://keyring:9500", headers={"x-keyring-token": TOKEN})


async def test_admin_key_lives_in_the_key_container_and_usage_shows_this_months_spend(monkeypatch, tmp_path, today):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = Provider()
    app, ring = server(tmp_path, provider)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c, container(ring) as box:
        keyring = app.state.appctx.keyring
        assert await keyring.unlock()
        assert (await c.put("/api/providers/anthropic/key", json={"api_key": CHAT})).json() == {"ok": True}
        assert (await c.put("/api/profile", json={"provider": "anthropic", "model": "claude-x"})).status_code == 200

        assert (await c.put("/api/providers/anthropic/admin-key", json={"api_key": "two words here"})).status_code == 422
        r = await c.put("/api/providers/anthropic/admin-key", json={"api_key": ADMIN})
        assert r.json() == {"ok": True, "checked": True}

        entries = {e["name"]: e for e in (await box.get("/entries")).json()["entries"]}
        assert entries["admin:anthropic"]["kind"] == "admin" and entries["admin:anthropic"]["hosts"] == ["api.anthropic.com"]
        assert entries["anthropic"]["kind"] == "chat"
        assert (await box.get("/entries")).text.count(ADMIN) == 0   # the container never lists a secret

        # the container lets the admin key through only as a GET on the cost path
        admin_side = httpx.AsyncClient(transport=KeyringTransport(keyring, "admin:anthropic"))
        assert (await admin_side.get("https://api.anthropic.com/v1/messages")).status_code == 403
        assert (await admin_side.post("https://api.anthropic.com/v1/organizations/cost_report", json={})).status_code == 403
        await admin_side.aclose()

        listing = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert listing["anthropic"]["admin_key"] == {"supported": True, "set": True, "hint": ADMIN[-4:]}
        assert listing["deepseek"]["admin_key"] == {"supported": False, "set": False, "hint": None}
        assert ADMIN not in (await c.get("/api/providers")).text

        usage = (await c.get("/api/usage")).json()
        main = next(a for a in usage["accounts"] if a["role"] == "main")
        assert main["provider"] == "anthropic" and main["admin"] is True and main["error"] is None
        assert main["used_month"] == 13.0 and main["used_today"] == 0.66 and main["currency"] == "USD"
        assert ADMIN not in json.dumps(usage) and CHAT not in json.dumps(usage)

        reads = [q for q in provider.seen if q.url.path == "/v1/organizations/cost_report"]
        assert reads and all(q.method == "GET" and q.headers["x-api-key"] == ADMIN for q in reads)
        assert not any(q.method == "POST" for q in provider.seen)   # the container refused every write with the admin key

        assert (await c.delete("/api/providers/anthropic/admin-key")).json() == {"ok": True}
        assert "admin:anthropic" not in [e["name"] for e in (await box.get("/entries")).json()["entries"]]
        assert (await c.delete("/api/providers/anthropic/admin-key")).status_code == 404
        usage = (await c.get("/api/usage")).json()
        main = next(a for a in usage["accounts"] if a["role"] == "main")
        assert not main.get("admin") and main["used_month"] is None

        assert (await c.put("/api/providers/deepseek/admin-key", json={"api_key": ADMIN})).status_code == 404


async def test_a_key_the_provider_refuses_is_removed_again(monkeypatch, tmp_path, today):
    provider = Provider(cost_status=401)
    app, ring = server(tmp_path, provider)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c, container(ring) as box:
        keyring = app.state.appctx.keyring
        assert await keyring.unlock()
        r = await c.put("/api/providers/anthropic/admin-key", json={"api_key": "sk-admin-refused-9999"})
        assert r.status_code == 422 and r.json()["detail"] == {"code": "key_refused"}
        assert "admin:anthropic" not in keyring.known
        assert "admin:anthropic" not in [e["name"] for e in (await box.get("/entries")).json()["entries"]]
        listing = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert listing["anthropic"]["admin_key"]["set"] is False


async def test_a_provider_that_is_down_keeps_the_admin_key(monkeypatch, tmp_path, today):
    provider = Provider(cost_status=503)
    app, ring = server(tmp_path, provider)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        keyring = app.state.appctx.keyring
        assert await keyring.unlock()
        r = await c.put("/api/providers/anthropic/admin-key", json={"api_key": ADMIN})
        assert r.json() == {"ok": True, "checked": False}
        assert "admin:anthropic" in keyring.known


async def test_admin_keys_need_the_key_container():
    app = create_app(settings(), tools=[])   # no KEYRING_URL: the container is off
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        r = await c.put("/api/providers/anthropic/admin-key", json={"api_key": ADMIN})
        assert r.status_code == 409 and r.json()["detail"] == {"code": "keyring_off"}
        listing = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        assert listing["anthropic"]["admin_key"] == {"supported": False, "set": False, "hint": None}
