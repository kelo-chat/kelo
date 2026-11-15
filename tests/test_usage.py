"""Token use per model call by kind of job, the daily totals' migration, and the optional background model.

No test reaches a real provider: every model is a fake stream function, and a fake that is asked for an OpenAI provider fails the test.
"""

import sqlite3
import time
from datetime import datetime, timezone

import httpx
import pytest

from assistant import usage as usage_meter
from assistant.providers import ChatRequest, Done, TextDelta, Usage
from assistant.server import create_app
from assistant.store import Store
from test_server import AUTH, Running, read_events, settings


def fake(seen: list, *, fail: set[str] = frozenset(), usage=(100, 10, 60, 20)):
    async def fn(provider, request, **kw):
        assert "openai" not in str(provider).lower(), "tests never ask an OpenAI provider"
        seen.append((provider, request.model))
        if provider in fail:
            raise RuntimeError(f"{provider} is down")
        yield TextDelta('[{"title": "Day", "summary": "talked"}]')
        yield Usage(*usage)
        yield Done("stop")
    return fn


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def talk(c: httpx.AsyncClient, text: str) -> None:
    async with c.stream("POST", "/api/talk", json={"text": text, "timezone": "UTC"}) as r:
        await read_events(r)


# ---- store


def test_old_database_keeps_its_daily_totals(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE usage (day TEXT PRIMARY KEY, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0)")
    db.execute("INSERT INTO usage VALUES ('2026-01-02', 1000, 200), (?, 5, 7)", (time.strftime("%Y-%m-%d"),))
    db.commit()
    db.close()
    store = Store(path)
    assert store.usage() == {"today": {"input_tokens": 5, "output_tokens": 7}, "total": {"input_tokens": 1005, "output_tokens": 207}}
    store.add_usage_event("chat", provider="p", model="m", input_tokens=10, output_tokens=3, cache_read_tokens=4)
    assert store.usage()["total"] == {"input_tokens": 1015, "output_tokens": 210}
    assert store.usage_daily("2026-01-01")[0] == {"day": "2026-01-02", "input_tokens": 1000, "output_tokens": 200,
                                                  "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert store.usage_by("kind", "2026-01-01") == [{"kind": "chat", "calls": 1, "input_tokens": 10, "output_tokens": 3,
                                                     "cache_read_tokens": 4, "cache_write_tokens": 0}]
    Store(path)   # opening it again changes nothing
    assert Store(path).usage()["total"] == {"input_tokens": 1015, "output_tokens": 210}


def test_old_records_are_pruned_but_totals_stay(monkeypatch):
    store = Store()
    store.add_usage_event("chat", input_tokens=1)
    store._run("UPDATE usage_events SET day = '2020-01-01'")
    store.add_usage_event("journal", input_tokens=2)
    monkeypatch.setattr(Store, "USAGE_KEEP_ROWS", 1)
    store.add_usage_event("memory", input_tokens=3)
    assert store.prune_usage_events() == 2          # older than the kept days, and beyond the newest row
    assert [r["kind"] for r in store.usage_by("kind", "2000-01-01")] == ["memory"]
    assert store.usage()["today"]["input_tokens"] == 6


def test_kind_of_source():
    assert [usage_meter.kind_of_source(s) for s in ("chat", "api", "schedule:3", "checklist", "worker", "handoff", "background", None)] == [
        "chat", "chat", "schedule", "checklist", "worker", "worker", "task", "other"]
    assert usage_meter.share(60, 100) == 0.6 and usage_meter.share(1, 0) is None


async def test_a_job_outside_the_background_kinds_stays_on_its_model():
    seen, records = [], []
    counted = usage_meter.metered(fake(seen), records.append, lambda: None)
    run = usage_meter.background_stream(counted, "followup", lambda: ("anthropic", "small"), lambda p: {}, lambda k, why: None)
    async for _ev in run("p", ChatRequest(model="m", messages=[])):
        pass
    assert seen == [("p", "m")] and records[0]["kind"] == "followup" and records[0]["cache_read_tokens"] == 60


# ---- server


async def test_each_model_call_is_recorded_by_kind_with_its_cached_share():
    seen: list = []
    app = create_app(settings(), tools=[], stream_fn=fake(seen))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await talk(c, "hello")
        assert (await c.post("/api/journal/rebuild", params={"date": utc_today()})).json()["status"] == "done"
        u = (await c.get("/api/usage")).json()
        s = (await c.get("/api/usage/summary", params={"days": 7})).json()
    # the old shape is unchanged; every call counts in it now (the chat reply and the journal)
    assert u["today"] == {"input_tokens": 200, "output_tokens": 20} and set(u["total"]) == {"input_tokens", "output_tokens"}
    kinds = {k["kind"]: k for k in u["jobs"]["today"]["kinds"]}
    assert set(kinds) == {"chat", "journal"}
    assert kinds["chat"] == {"kind": "chat", "calls": 1, "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 60,
                             "cache_write_tokens": 20, "cache_share": 0.6}
    assert u["jobs"]["month"]["input_tokens"] == 200 and u["jobs"]["month"]["cache_share"] == 0.6
    assert u["background"] == {"provider": "", "model": "", "kinds": sorted(usage_meter.BACKGROUND_KINDS), "last_fallback": None}
    assert set(s) == {"since", "days", "kinds", "models", "total", "background"}
    assert s["days"][-1]["input_tokens"] == 200 and s["days"][-1]["cache_share"] == 0.6
    assert s["models"] == [{"provider": "p", "model": "m", "calls": 2, "input_tokens": 200, "output_tokens": 20,
                            "cache_read_tokens": 120, "cache_write_tokens": 40, "cache_share": 0.6}]
    assert s["total"]["calls"] == 2


async def test_background_model_runs_background_jobs_only_and_falls_back_to_the_main_model():
    seen: list = []
    fail: set[str] = set()
    app = create_app(settings(), tools=[], stream_fn=fake(seen, fail=fail))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        prof = (await c.put("/api/profile", json={"background_provider": "anthropic", "background_model": "fake-small"})).json()
        assert (prof["background_provider"], prof["background_model"]) == ("anthropic", "fake-small")
        await talk(c, "hello")
        assert seen == [("p", "m")]                              # the user's reply stays on the main model
        await c.post("/api/journal/rebuild", params={"date": utc_today()})
        assert seen[-1] == ("anthropic", "fake-small")           # the journal runs on the background model
        fail.add("anthropic")
        out = (await c.post("/api/journal/rebuild", params={"date": utc_today()})).json()
        assert out["status"] == "done"                           # ... and on the main model when that one fails
        assert seen[-2:] == [("anthropic", "fake-small"), ("p", "m")]
        u = (await c.get("/api/usage")).json()
        last = u["background"]["last_fallback"]
        assert last["kind"] == "journal" and "anthropic is down" in last["reason"] and last["model"] == "fake-small"
        models = {(m["provider"], m["model"]): m for m in (await c.get("/api/usage/summary")).json()["models"]}
        assert models[("anthropic", "fake-small")]["calls"] == 1 and models[("p", "m")]["calls"] == 2
        # back to the main model: no background model, the old failure is no longer shown
        await c.put("/api/profile", json={"background_provider": "", "background_model": ""})
        u = (await c.get("/api/usage")).json()
        assert u["background"]["provider"] == "" and u["background"]["last_fallback"] is None
        seen.clear()
        await c.post("/api/journal/rebuild", params={"date": utc_today()})
        assert seen == [("p", "m")]


async def test_background_model_choice_is_checked_and_apart_from_the_fallback_rule():
    app = create_app(settings(), tools=[], stream_fn=fake([]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/profile", json={"background_model": "x"})).status_code == 422          # a model without a provider
        assert (await c.put("/api/profile", json={"background_provider": "anthropic"})).status_code == 422   # a provider without a model
        assert (await c.put("/api/profile", json={"background_provider": "no-such-one", "background_model": "x"})).status_code == 422
        # the main model's own provider is fine for background work (only the fallback must differ from the main model)
        await c.put("/api/profile", json={"provider": "anthropic", "model": "big"})
        r = await c.put("/api/profile", json={"background_provider": "anthropic", "background_model": "small"})
        assert r.status_code == 200 and r.json()["background_model"] == "small"
        assert (await c.put("/api/profile", json={"fallback_provider": "anthropic", "fallback_model": "small"})).status_code == 422
        assert (await c.get("/api/profile")).json()["background_provider"] == "anthropic"
        # changing only the provider asks for a model again
        assert (await c.put("/api/profile", json={"background_provider": "deepseek"})).status_code == 422


@pytest.mark.parametrize("kind", sorted(usage_meter.BACKGROUND_KINDS))
async def test_every_background_kind_can_use_the_background_model(kind):
    seen, records, fell = [], [], []
    counted = usage_meter.metered(fake(seen, fail={"anthropic"}), records.append, lambda: {"source": "chat", "task_id": 4})
    run = usage_meter.background_stream(counted, kind, lambda: ("anthropic", "small"), lambda p: {}, lambda k, why: fell.append((k, why)))
    async for _ev in run("p", ChatRequest(model="m", messages=[])):
        pass
    assert seen == [("anthropic", "small"), ("p", "m")] and fell[0][0] == kind
    assert records == [{"kind": kind, "task_id": 4, "provider": "p", "model": "m", "input_tokens": 100, "output_tokens": 10,
                        "cache_read_tokens": 60, "cache_write_tokens": 20, "note": f"background model failed: {fell[0][1]}"}]


async def test_every_model_call_leaves_one_timing_line_without_text(caplog):
    """2026-11-12 (agy felt slow): one log line per call with the first event's time, the total, tokens and the message count."""
    import logging
    seen, records = [], []
    counted = usage_meter.metered(fake(seen), records.append, lambda: {"source": "chat"})
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        async for _ev in counted()("p", ChatRequest(model="m", messages=[{"role": "user", "content": "secret words here"}])):
            pass
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("model call ")]
    assert len(lines) == 1 and "p m (chat)" in lines[0] and "total " in lines[0] and "1 messages" in lines[0]
    assert "secret words" not in lines[0]
