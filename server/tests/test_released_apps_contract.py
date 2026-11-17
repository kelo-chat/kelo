"""A server change must never break an app that people already have (docs/version-notices.md part 1).

For the newest entry of every platform in releases/apps.json there is a fixture, `fixtures/app_requests/<platform>-<version>.json`:
the requests that release makes on start and in normal use (method, path, minimal body), recorded from the app's API client code at
its release tag, with the top-level fields of the answer that release reads. Each is replayed against a fresh server with the release's
own X-Kelo-App header: never 426, never 404/405 for a route (the fixture's "expect" lists the other statuses a route may give), and the
named fields still there. When an app release goes live its fixture is added (docs/versioning.md, release checklist).
"""

import json
from pathlib import Path

import httpx
import pytest

from assistant.app_releases import AppReleases
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "app_requests"
RECORD = AppReleases.read(REPO / "releases" / "apps.json")
NEWEST = {p: RECORD.latest(p) for p in ("android", "ios", "desktop") if RECORD.latest(p)}


def fixture_for(platform, rel):
    return FIXTURES / f"{platform}-{rel.version}.json"


@pytest.mark.parametrize("platform", sorted(NEWEST))
def test_the_newest_release_of_each_platform_has_a_fixture(platform):
    rel = NEWEST[platform]
    assert fixture_for(platform, rel).is_file(), (
        f"{platform} {rel.version} is in releases/apps.json but has no request fixture: add "
        f"server/tests/fixtures/app_requests/{platform}-{rel.version}.json, recorded from that release's API client code")


def test_every_fixture_is_well_formed():
    files = sorted(FIXTURES.glob("*.json"))
    assert files
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        assert d["headers"]["X-Kelo-App"].split("/")[0] in ("android", "ios", "desktop"), f.name
        assert d["requests"], f.name
        for r in d["requests"]:
            assert r["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE") and r["path"].startswith("/api/"), (f.name, r)


@pytest.mark.parametrize("platform", sorted(NEWEST))
async def test_the_server_still_serves_the_released_app(platform, tmp_path):
    rel = NEWEST[platform]
    spec = json.loads(fixture_for(platform, rel).read_text(encoding="utf-8"))
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"),
                 home_dir=str(tmp_path / "home"))   # the agent's files on, as on a normal server
    problems = []
    async with Running(create_app(s, tools=[])) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, **spec["headers"]}, timeout=30) as c:
        for r in spec["requests"]:
            got = await c.request(r["method"], r["path"], json=r.get("body"))
            tag = f"{r['method']} {r['path']}"
            if got.status_code == 426:
                problems.append(f"{tag}: 426 {got.text[:120]}")
                continue
            if got.status_code not in r.get("expect", [200]):
                problems.append(f"{tag}: HTTP {got.status_code} {got.text[:120]}")
                continue
            if got.status_code != 200 or not (r.get("fields") or r.get("item_fields")):
                continue
            body = got.json()
            if r.get("fields"):
                missing = [k for k in r["fields"] if not isinstance(body, dict) or k not in body]
                if missing:
                    problems.append(f"{tag}: the answer lost {missing}")
            if r.get("item_fields") and isinstance(body, list) and body:
                missing = [k for k in r["item_fields"] if k not in body[0]]
                if missing:
                    problems.append(f"{tag}: items lost {missing}")
    assert not problems, f"the server breaks {platform} {rel.version}:\n" + "\n".join(problems)
