"""GET /api/providers answers from the kept claude / agy status and never waits on a slow sidecar; sign-in, sign-out and other CLI
changes still show at once. Only fakes: no CLI runs and no sidecar is called."""

import asyncio
import time

import httpx

from assistant import i18n
from assistant.providers import cli_manage, cli_remote
from assistant.providers.cli_status_cache import CliStatusCache
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, scripted


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeCli:
    """A CLI status that takes `delay` seconds to answer and counts the calls."""

    def __init__(self, delay=0.0, **st):
        self.delay, self.calls = delay, 0
        self.st = {"installed": True, "installable": False, "logged_in": True, "account": "a@b.c", "version": "1.0", "hint": "", **st}

    async def __call__(self, name, **_):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.st)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


# ---- the cache itself

async def test_answers_from_the_cache_within_the_ttl_and_refreshes_in_the_background_after():
    fake, clock = FakeCli(), Clock()
    cache = CliStatusCache(fake, ttl=45, clock=clock)
    first = await cache.get("claude")          # nothing known yet: waits for the first check (it is quick)
    assert first["logged_in"] is True and first["checked_at"] and fake.calls == 1
    clock.now += 30
    assert (await cache.get("claude"))["checked_at"] == first["checked_at"] and fake.calls == 1   # fresh: no new check
    clock.now += 20                            # older than the TTL: answered from the cache, checked again behind it
    fake.st["version"] = "2.0"
    old = await cache.get("claude")
    assert old["version"] == "1.0"
    await settle()
    assert fake.calls == 2 and (await cache.get("claude"))["version"] == "2.0"
    await cache.close()


async def test_a_slow_sidecar_is_never_waited_for_once_something_is_known():
    fake, clock = FakeCli(), Clock()
    cache = CliStatusCache(fake, ttl=45, clock=clock)
    await cache.get("agy")
    fake.delay = 5.0                           # the machine is busy now
    clock.now += 100
    t0 = time.perf_counter()
    for _ in range(5):
        st = await cache.get("agy")
    assert time.perf_counter() - t0 < 0.2 and st["installed"] is True
    await settle()
    assert fake.calls == 2                     # one background check, not one per look
    await cache.close()


async def test_nothing_known_and_a_slow_sidecar_waits_at_most_the_cold_wait():
    cache = CliStatusCache(FakeCli(delay=5.0), cold_wait=0.2)
    t0 = time.perf_counter()
    st = await cache.get("claude")
    assert time.perf_counter() - t0 < 1.0
    assert st["checking"] is True and st["checked_at"] is None and st["logged_in"] is None and st["installable"] is False
    await cache.close()


async def test_forget_status_makes_the_next_look_wait_for_the_new_state():
    fake, clock = FakeCli(), Clock()
    cache = CliStatusCache(fake, clock=clock)
    assert (await cache.get(cli_manage.CLAUDE))["logged_in"] is True
    fake.st.update(logged_in=False, account=None)          # signed out somewhere (a sidecar's own sign-out, an update ...)
    cli_manage.forget_status(cli_manage.CLAUDE)            # what every sign-in / sign-out / update / install path calls
    st = await cache.get(cli_manage.CLAUDE)
    assert st["logged_in"] is False and st["account"] is None and fake.calls == 2
    await cache.close()


async def test_an_older_check_never_replaces_a_newer_one():
    fake = FakeCli(delay=0.3)
    cache = CliStatusCache(fake)
    slow = asyncio.ensure_future(cache.get("x"))
    await asyncio.sleep(0.05)
    fake.delay, fake.st["logged_in"] = 0.0, False
    cache.invalidate("x")
    assert (await cache.refresh("x"))["logged_in"] is False
    await slow
    await asyncio.sleep(0.4)
    assert cache.peek("x")["logged_in"] is False
    await cache.close()


async def test_the_saved_copy_is_served_after_a_restart_and_checked_again():
    saved = {}
    fake = FakeCli()
    first = CliStatusCache(fake, load=saved.get, save=saved.__setitem__)
    await first.get("agy")
    await first.close()
    assert saved["agy"]["st"]["logged_in"] is True
    fake.delay, fake.st["version"] = 5.0, "9.9"
    second = CliStatusCache(fake, load=saved.get, save=saved.__setitem__)
    t0 = time.perf_counter()
    st = await second.get("agy")
    assert time.perf_counter() - t0 < 0.2 and st["version"] == "1.0" and st["checked_at"] == saved["agy"]["checked_at"]
    await second.close()


async def test_hints_are_written_in_each_requests_language():
    async def sidecar(name):   # what cli_remote.status answers for a running sidecar (in the language of the check)
        return {"installed": True, "logged_in": True, "remote": True, "hint_kind": "ok", **cli_remote.hints(name, "ok")}

    cache = CliStatusCache(sidecar)
    en = await cache.get(cli_manage.CLAUDE)
    assert en["update_hint"] == "Update to the latest: assistant update --cli claude" and "hint_kind" not in en
    tok = i18n.use("ko")
    try:
        ko = await cache.get(cli_manage.CLAUDE)
    finally:
        i18n.reset(tok)
    assert ko["update_hint"] == "최신으로 업데이트: assistant update --cli claude"
    await cache.close()


# ---- the endpoint

def app_for(tmp_path):
    return create_app(Settings(token=TOKEN, provider="openai", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                               unofficial_providers=True), tools=[], stream_fn=scripted())


async def test_providers_answer_at_once_with_a_slow_sidecar_and_say_when_each_cli_was_checked(tmp_path, monkeypatch):
    fake = FakeCli()
    monkeypatch.setattr(cli_manage, "cli_status", fake)
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        rows = {p["name"]: p for p in (await c.get("/api/providers")).json()}
        for name in (cli_manage.CLAUDE, cli_manage.AGY):
            assert rows[name]["cli"]["checked_at"] and rows[name]["signed_in"] is True and rows[name]["cli"]["installed"] is True
        fake.delay = 5.0                                     # the sidecar slows right down ...
        for name in (cli_manage.CLAUDE, cli_manage.AGY):     # ... and the kept answers are old
            app.state.cli_statuses._entries[name]["at"] -= 3600
        t0 = time.perf_counter()
        for _ in range(3):
            assert (await c.get("/api/providers")).status_code == 200
        assert time.perf_counter() - t0 < 1.0
        calls = fake.calls
        await asyncio.sleep(0.1)
        assert fake.calls == calls                           # one background check per CLI at a time
        fake.delay = 0.0


async def test_sign_out_and_sign_in_show_at_once(tmp_path, monkeypatch):
    fake = FakeCli()
    monkeypatch.setattr(cli_manage, "cli_status", fake)

    async def fake_logout(name):
        fake.st.update(logged_in=False, account=None)
        return {"removed": True, "still_signed_in": False}

    monkeypatch.setattr(cli_manage, "cli_logout", fake_logout)
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == cli_manage.AGY)
        assert row["signed_in"] is True and row["account"] == "a@b.c"
        assert (await c.delete("/api/providers/agy/login")).status_code == 200
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == cli_manage.AGY)
        assert row["signed_in"] is False and row["account"] is None
        # signed in again on the sidecar (its sign-in flow calls forget_status when it finishes)
        fake.st.update(logged_in=True, account="new@b.c")
        cli_manage.forget_status(cli_manage.AGY)
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == cli_manage.AGY)
        assert row["signed_in"] is True and row["account"] == "new@b.c"


async def test_a_restarted_server_starts_from_the_saved_status(tmp_path, monkeypatch):
    fake = FakeCli()
    monkeypatch.setattr(cli_manage, "cli_status", fake)
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.get("/api/providers")
    fake.delay = 5.0
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        t0 = time.perf_counter()
        row = next(p for p in (await c.get("/api/providers")).json() if p["name"] == cli_manage.CLAUDE)
        assert time.perf_counter() - t0 < 1.0 and row["signed_in"] is True and row["cli"]["checked_at"]
        fake.delay = 0.0
