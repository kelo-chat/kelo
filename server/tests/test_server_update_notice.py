"""The server-update notice's server side (server_updates.offer, X-Kelo-Server-Update on /api/about and /api/profile): only what the
last updater answer says, only to a caller that may update this server, never on a review copy or without an updater."""

import httpx

from assistant.server import create_app
from assistant.server_updates import ServerUpdates, UpdaterClient
from test_server import AUTH, Running, scripted, settings
from test_server_updates import UTOKEN, FakeUpdater, Store, make

HEADER = "X-Kelo-Server-Update"


async def test_offer_names_a_newer_release_from_the_last_status():
    su, fake, notes, store = make()
    assert su.offer() is None                                  # nothing read yet: no answer, no network call
    await su.status()
    assert su.offer() == "0.5.0"


async def test_offer_is_none_for_local_builds_running_jobs_auto_update_and_stale_status():
    now = [1_700_000_000.0]
    fake = FakeUpdater()
    su, _, _, store = make(fake, clock=lambda: now[0])
    await su.status()
    assert su.offer() == "0.5.0"
    fake.status["mode"] = "source"                              # built from the source files: nothing to offer
    await su.status()
    assert su.offer() is None
    fake.status["mode"] = "registry"
    fake.status["job"] = {"id": "j9", "action": "update", "state": "running", "step": "download", "progress": 40, "from": "0.4.0",
                          "to": "0.5.0", "error": None, "data_restored": False, "started_at": 1.0, "finished_at": None}
    await su.status()
    assert su.offer() is None
    fake.status["job"] = None
    await su.status()
    su.set_auto(True)                                           # automatic updates tell the owner afterwards, not ahead
    assert su.offer() is None
    su.set_auto(False)
    assert su.offer() == "0.5.0"
    now[0] += 1801                                              # an answer older than 30 minutes: the updater stopped answering
    assert su.offer() is None


async def test_offer_is_none_when_latest_is_not_newer_than_version_json():
    su, fake, notes, store = make()
    await su.status()
    su.version = lambda: "0.5.0"                                # this server already runs the offered release
    assert su.offer() is None
    su.version = lambda: "not a version"
    assert su.offer() is None


async def test_about_and_profile_send_x_kelo_server_update_to_a_device_that_may_update(tmp_path):
    fake = FakeUpdater()
    fake.status["latest"]["version"] = "99.1.0"
    app = create_app(settings(updater_url="http://updater:9400", updater_token=UTOKEN), tools=[], stream_fn=scripted(),
                     updater_transport=httpx.MockTransport(fake.handler))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/server-update", headers=AUTH)).status_code == 200      # reads the status once
        about = await c.get("/api/about", headers=AUTH)
        assert about.headers[HEADER] == "99.1.0" and about.json()["server_update"] == "99.1.0"
        code = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
        phone = {"Authorization": f"Bearer {(await c.post('/api/pair', json={'code': code, 'name': 'p'})).json()['token']}"}
        assert (await c.get("/api/about", headers=phone)).headers[HEADER] == "99.1.0"
        profile = await c.get("/api/profile", headers=phone)
        assert profile.headers[HEADER] == "99.1.0" and "server_update" not in profile.json()


async def test_no_server_update_header_on_a_review_copy_device_or_without_an_updater(tmp_path):
    fake = FakeUpdater()
    fake.status["latest"]["version"] = "99.1.0"
    app = create_app(settings(updater_url="http://updater:9400", updater_token=UTOKEN, review_mode=True), tools=[], stream_fn=scripted(),
                     updater_transport=httpx.MockTransport(fake.handler))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        await c.get("/api/server-update", headers=AUTH)
        assert (await c.get("/api/about", headers=AUTH)).headers[HEADER] == "99.1.0"   # the access key may update this server
        code = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
        phone = {"Authorization": f"Bearer {(await c.post('/api/pair', json={'code': code, 'name': 'p'})).json()['token']}"}
        assert HEADER not in (await c.get("/api/about", headers=phone)).headers
    app = create_app(settings(), tools=[], stream_fn=scripted())                       # no updater at all
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        about = await c.get("/api/about", headers=AUTH)
        assert HEADER not in about.headers and about.json()["server_update"] is None
