"""app_update_for (app_updates.py, docs/version-notices.md part 3): only released rows, only signed download rows, min_server, channel."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistant import release_keys, release_sign
from assistant.app_releases import AppReleases
from assistant.app_updates import AppUpdates, HEADER, CHANNEL_HEADER
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running

KEY = Ed25519PrivateKey.generate()
PUB = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
KID = release_sign.key_id(PUB)


@pytest.fixture(autouse=True)
def our_key(monkeypatch):
    monkeypatch.setattr(release_keys, "RELEASE_KEYS", {KID: base64.b64encode(PUB).decode()})


def row(platform, version, code=None, *, sign=True):
    r = {"file": f"kelo-{version}.apk", "version": version, "size": 10, "sha256": "a" * 64, "date": "2026-11-10T00:00:00Z"}
    if code:
        r["version_code"] = code
    if sign:
        r = {**r, "sig": base64.b64encode(KEY.sign(release_sign.statement(platform, r))).decode(), "key": KID}
    return r


def records(**kw):
    return AppReleases.parse({"android": kw.get("android", []), "ios": kw.get("ios", []), "desktop": kw.get("desktop", [])})


def entry(version, build, channel="download", min_server=None):
    e = {"version": version, "build": build, "channel": channel, "date": "2026-11-14"}
    if min_server:
        e["min_server"] = min_server
    return e


def updates(rec, latest, server="0.10.2"):
    async def fetch():
        if isinstance(latest, Exception):
            raise latest
        return json.dumps(latest).encode()
    return AppUpdates(rec, server, fetcher=fetch)


async def test_a_newer_signed_download_is_offered():
    rec = records(android=[entry("0.10.1", 410372), entry("0.10.3", 410400)])
    u = updates(rec, {"android": row("android", "0.10.3", 410400)})
    assert await u.app_update_for("android", "download", "0.10.1", 410372) == "0.10.3"
    assert await u.app_update_for("android", "download", "0.10.3", 410400) is None   # not newer than the caller


async def test_an_unsigned_or_wrongly_signed_row_is_ignored():
    rec = records(android=[entry("0.10.3", 410400)])
    assert await updates(rec, {"android": row("android", "0.10.3", 410400, sign=False)}).app_update_for("android", "download", "0.10.1", 1) is None
    bad = {**row("android", "0.10.3", 410400), "size": 11}   # changed after signing
    assert await updates(rec, {"android": bad}).app_update_for("android", "download", "0.10.1", 1) is None


async def test_only_released_rows_count():
    # on the download page but not in the record of released apps
    rec = records(android=[entry("0.10.1", 410372)])
    u = updates(rec, {"android": row("android", "0.10.5", 410500)})
    assert await u.app_update_for("android", "download", "0.10.1", 410372) is None


async def test_min_server_is_respected():
    rec = records(android=[entry("0.10.3", 410400, min_server="0.11.0")])
    u = updates(rec, {"android": row("android", "0.10.3", 410400)}, server="0.10.2")
    assert await u.app_update_for("android", "download", "0.10.1", 410372) is None
    u = updates(rec, {"android": row("android", "0.10.3", 410400)}, server="0.11.0")
    assert await u.app_update_for("android", "download", "0.10.1", 410372) == "0.10.3"


async def test_channel_decides_what_may_be_offered():
    rec = records(android=[entry("0.10.1", 410372), entry("0.10.3", 410400), entry("0.10.2", 410380, "play")])
    u = updates(rec, {"android": row("android", "0.10.3", 410400)})
    assert await u.app_update_for("android", "play", "0.10.1", 410372) == "0.10.2"       # the store build sees the store release only
    assert await u.app_update_for("android", "download", "0.10.1", 410372) == "0.10.3"
    assert await u.app_update_for("android", "appstore", "0.10.1", 410372) is None
    assert await u.app_update_for("android", None, "0.10.1", 410372) is None
    assert await u.app_update_for("ios", "appstore", "0.10.1", 1) is None


async def test_store_rows_need_no_download_list_and_a_failed_list_gives_nothing_for_downloads():
    rec = records(ios=[entry("1.0.1", 5, "appstore")], android=[entry("0.10.3", 410400)])
    u = updates(rec, OSError("offline"))
    assert await u.app_update_for("ios", "appstore", "1.0.0", 4) == "1.0.1"
    assert await u.app_update_for("android", "download", "0.10.1", 1) is None


async def test_desktop_is_compared_by_build_and_any_signed_installer_row_lists_it():
    rec = records(desktop=[entry("1.0.9", 100000009), entry("1.0.10", 100000010)])
    u = updates(rec, {"windows": row("windows", "1.0.10", 100000010)})
    assert await u.app_update_for("desktop", "download", "0.10.1", 100000009) == "1.0.10"
    assert await u.app_update_for("desktop", "download", "0.10.1", 100000010) is None


async def test_the_list_is_read_once_per_period():
    calls = []
    now = [0.0]

    async def fetch():
        calls.append(1)
        return json.dumps({"android": row("android", "0.10.3", 410400)}).encode()
    u = AppUpdates(records(android=[entry("0.10.3", 410400)]), "0.10.2", fetcher=fetch, clock=lambda: now[0])
    for _ in range(3):
        await u.app_update_for("android", "download", "0.10.1", 1)
    assert len(calls) == 1
    now[0] = 7 * 3600
    await u.app_update_for("android", "download", "0.10.1", 1)
    assert len(calls) == 2


async def test_about_and_profile_carry_header_and_field(tmp_path):
    rec = records(android=[entry("0.10.1", 410372), entry("0.10.3", 410400)])
    latest = json.dumps({"android": row("android", "0.10.3", 410400)}).encode()

    async def fetch():
        return latest
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"))
    async with Running(create_app(s, tools=[], app_releases=rec, app_list_fetcher=fetch)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        hs = {"X-Kelo-App": "android/0.10.1+410372", CHANNEL_HEADER: "download"}
        about = await c.get("/api/about", headers=hs)
        assert about.json()["app_update"] == "0.10.3" and about.headers[HEADER] == "0.10.3"
        assert (await c.get("/api/profile", headers=hs)).headers[HEADER] == "0.10.3"
        assert (await c.get("/api/profile", headers={**hs, CHANNEL_HEADER: "play"})).headers.get(HEADER) is None
        plain = await c.get("/api/about")                                  # an older app: nothing
        assert plain.json()["app_update"] is None and HEADER not in plain.headers
        assert HEADER not in (await c.get("/api/profile")).headers
