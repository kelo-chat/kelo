"""Updating the server from the app (server_updates.py): owner-only routes passed to the updater, what the apps are told, the
notifications, and when the nightly auto-update starts."""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant.server_updates import ServerUpdates, UpdaterClient
from test_server import AUTH, Running, scripted, settings

TZ = ZoneInfo("Asia/Seoul")
UTOKEN = "u" * 40


class FakeUpdater:
    """The updater's HTTP side, in memory."""

    def __init__(self):
        self.status = {"mode": "registry", "reason": None, "current": "0.4.0",
                       "latest": {"version": "0.5.0", "date": "2026-11-09T00:00:00Z", "notes": {"en": "Faster.", "ko": "빨라졌어요."}},
                       "update_available": True, "checked_at": 0.0, "job": None,
                       "rollback": {"available": False, "version": None, "created_at": None}}
        self.calls = []
        self.refuse = None
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        assert request.headers["authorization"] == f"Bearer {UTOKEN}"
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == "POST":
            assert request.content in (b"{}", b"")                   # the server never sends parameters
        if path == "/status":
            return httpx.Response(200, json=self.status)
        if path == "/check":
            self.status["checked_at"] = 1e12
            return httpx.Response(200, json=self.status)
        if path in ("/update", "/rollback"):
            if self.refuse:
                return httpx.Response(409, json={"detail": "x", "code": self.refuse})
            self.status["job"] = {"id": "j1", "action": path[1:], "state": "running", "step": "preflight", "progress": 5,
                                  "from": "0.4.0", "to": "0.5.0", "error": None, "data_restored": False, "log": ["secret-free line"],
                                  "heartbeat": 1.0, "started_at": 1.0, "finished_at": None}
            return httpx.Response(202, json=self.status)
        return httpx.Response(404, json={})


class Store:
    def __init__(self):
        self.m = {}

    def meta(self, k):
        return self.m.get(k)

    def set_meta(self, k, v):
        self.m[k] = v


def ts(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp()


def make(fake=None, **kw):
    fake = fake or FakeUpdater()
    notes = []
    store = Store()
    su = ServerUpdates(store, UpdaterClient("http://updater:9400", UTOKEN, transport=httpx.MockTransport(fake.handler)),
                       version=lambda: "0.4.0", notify=lambda *a, **k: notes.append(a), tz=lambda: "Asia/Seoul",
                       quiet_slot=lambda now: (3, 50, "default"), **kw)
    return su, fake, notes, store


async def test_status_for_the_apps_picks_the_owners_language_and_hides_the_log():
    su, fake, notes, store = make()
    st = await su.status()
    assert st["state"] == "ok" and st["current"] == "0.4.0" and st["update_available"] is True and st["latest"]["notes"] == "Faster."
    assert st["auto_update"] is False and st["auto_time"] is None
    code, body = await su.start("update")
    assert code == 202 and body["job"]["state"] == "running" and "log" not in body["job"] and "heartbeat" not in body["job"]
    assert body["job"]["auto"] is False


async def test_local_build_and_no_updater_are_said_plainly():
    su, fake, notes, store = make()
    fake.status.update(mode="local", reason="local_build", update_available=False)
    st = await su.status()
    assert st["state"] == "local" and "kelo update" in st["message"] and st["update_available"] is False
    fake.down = True
    st = await su.status()
    assert st["state"] == "unavailable" and st["current"] == "0.4.0"
    assert (await su.start("update"))[0] == 409
    su2 = ServerUpdates(Store(), UpdaterClient("", ""), version=lambda: "1", notify=lambda *a: None, tz=lambda: None)
    assert (await su2.status())["state"] == "unavailable"


async def test_refusals_come_back_in_words():
    su, fake, notes, store = make()
    for code, word in (("busy", "already running"), ("up_to_date", "up to date"), ("no_rollback", "nothing to go back")):
        fake.refuse = code
        status, body = await su.start("update")
        assert status == 409 and word in body["detail"]


def test_the_auto_update_window_is_at_night_after_the_quiet_slot_outside_contact_hours():
    su, *_ = make(awake=lambda t: datetime.fromtimestamp(t, TZ).hour >= 7)
    su.store.set_meta("nightly:last", "2026-11-09")
    assert su.due(ts(2026, 11, 10, 4, 40)) is True                         # 03:50 + 45 min, nightly jobs done
    assert su.due(ts(2026, 11, 10, 4, 30)) is False                        # too early
    assert su.due(ts(2026, 11, 10, 7, 30)) is False                        # contact hours have begun
    assert su.due(ts(2026, 11, 10, 14, 0)) is False                        # daytime
    su.store.set_meta("nightly:last", "2026-11-08")                         # tonight's nightly jobs have not run yet
    assert su.due(ts(2026, 11, 10, 4, 40)) is False and su.due(ts(2026, 11, 10, 6, 40)) is True
    su.store.set_meta("server_update:auto_night", "2026-11-09")             # once a night
    assert su.due(ts(2026, 11, 10, 6, 40)) is False


def test_auto_update_waits_for_a_quiet_app_and_no_running_chat():
    now = ts(2026, 11, 10, 4, 40)
    busy = [False]
    su, *_ = make(last_use=lambda: now - 5 * 60, busy=lambda: busy[0])
    su.store.set_meta("nightly:last", "2026-11-09")
    assert su.due(now) is False
    su.last_use = lambda: now - 3600
    assert su.due(now) is True
    busy[0] = True
    assert su.due(now) is False


async def test_tick_runs_auto_update_once_and_notifies_the_result_once():
    now = ts(2026, 11, 10, 4, 40)
    su, fake, notes, store = make(clock=lambda: now)
    store.set_meta("nightly:last", "2026-11-09")
    store.set_meta("server_update:auto", "1")
    assert await su.tick(now) == ["auto"]
    assert ("POST", "/update") in fake.calls and store.meta("server_update:auto_job") == "j1"
    assert (await su.status())["job"]["auto"] is True and (await su.status())["auto_time"] == "04:35"
    assert await su.tick(now + 300) == []                                   # running, and once a night anyway
    fake.status["job"].update(state="done", finished_at=now + 400)
    fake.status.update(current="0.5.0", update_available=False)
    assert await su.tick(now + 600) == ["job"]
    assert notes[-1][0] == "server_update" and "0.5.0" in notes[-1][2]
    assert await su.tick(now + 900) == []                                   # told once


async def test_a_new_version_is_announced_once_when_auto_update_is_off_and_failures_are_told():
    su, fake, notes, store = make(clock=lambda: 1e12)
    fake.status["checked_at"] = 1e12
    assert await su.tick(1e12) == ["available"] and "0.5.0" in notes[-1][1]
    assert await su.tick(1e12 + 300) == []
    fake.status["job"] = {"id": "j2", "action": "update", "state": "rolled_back", "from": "0.4.0", "to": "0.5.0", "finished_at": 1e12}
    assert "job" in await su.tick(1e12 + 600)
    assert "0.4.0" in notes[-1][2] and "0.5.0" in notes[-1][2]


async def test_the_release_list_is_read_again_every_few_hours():
    su, fake, notes, store = make()
    fake.status["checked_at"] = 0.0
    await su.tick(1e9)
    assert ("POST", "/check") in fake.calls


# ---------------------------------------------------------------------------------------------------------------- the routes
@pytest.fixture
def fake():
    return FakeUpdater()


async def test_routes_are_owner_only_and_pass_fixed_commands(fake):
    from assistant.server import create_app

    app = create_app(settings(updater_url="http://updater:9400", updater_token=UTOKEN), tools=[], stream_fn=scripted(),
                     updater_transport=httpx.MockTransport(fake.handler))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/server-update")).status_code == 401
        st = (await c.get("/api/server-update", headers={**AUTH, "accept-language": "ko"})).json()
        assert st["state"] == "ok" and st["latest"]["notes"] == "빨라졌어요."
        code = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
        phone = {"Authorization": f"Bearer {(await c.post('/api/pair', json={'code': code, 'name': 'p'})).json()['token']}"}
        for method, path in (("GET", "/api/server-update"), ("POST", "/api/server-update/check"), ("POST", "/api/server-update/update"),
                             ("POST", "/api/server-update/rollback")):
            assert (await c.request(method, path, headers=phone, json={})).status_code == 403
        assert (await c.put("/api/server-update/settings", headers=phone, json={"auto_update": True})).status_code == 403
        assert (await c.post("/api/server-update/check", headers=AUTH, json={})).status_code == 200
        r = await c.post("/api/server-update/update", headers=AUTH, json={})
        assert r.status_code == 202 and r.json()["job"]["state"] == "running"
        fake.refuse = "busy"
        r = await c.post("/api/server-update/rollback", headers=AUTH, json={})
        assert r.status_code == 409 and r.json()["detail"]
        r = await c.put("/api/server-update/settings", headers=AUTH, json={"auto_update": True})
        assert r.status_code == 200 and r.json()["auto_update"] is True and r.json()["auto_time"]
        assert (await c.put("/api/server-update/settings", headers=AUTH, json={"auto_update": True, "image": "x"})).status_code == 422
    assert all(m in ("GET", "POST") and p in ("/status", "/check", "/update", "/rollback") for m, p in fake.calls)


async def test_a_copy_without_the_updater_says_unavailable():
    from assistant.server import create_app

    app = create_app(settings(), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        st = (await c.get("/api/server-update", headers=AUTH)).json()
        assert st["state"] == "unavailable"
        assert (await c.post("/api/server-update/update", headers=AUTH, json={})).status_code == 409


async def test_while_the_updater_replaces_itself_the_running_job_stays_visible():
    t = [1000.0]
    su, fake, notes, store = make(clock=lambda: t[0])
    await su.start("update")
    assert (await su.status())["job"]["state"] == "running"
    fake.down = True                                         # the old updater is gone, the new one not up yet
    t[0] += 30
    st = await su.status()
    assert st["state"] == "ok" and st["job"]["state"] == "running"
    t[0] += 600                                              # but not for ever
    assert (await su.status())["state"] == "unavailable"
