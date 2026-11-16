"""Release version (version.json, docs/versioning.md): reading it, where the server shows it, the min version contract, bump.py."""

import importlib.util
import json
import re
import shutil
import datetime as dt
from pathlib import Path

import httpx
import pytest

from assistant import version as rv
from assistant.app_releases import AppReleases
from assistant.server import Settings, create_app

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _bump():
    spec = importlib.util.spec_from_file_location("bump", ROOT / "tools" / "release" / "bump.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_tracked_file_is_valid_and_pyproject_and_uv_lock_agree():
    rel = rv.read(ROOT / "version.json")
    assert rv.parse(rel.version) and rel.build >= 1
    assert re.search(rf'(?m)^version = "{re.escape(rel.version)}"$', (ROOT / "pyproject.toml").read_text())
    assert f'name = "assistant"\nversion = "{rel.version}"' in (ROOT / "uv.lock").read_text()
    assert rv.current().version == rel.version and rv.current().source.endswith("version.json")
    assert "COPY version.json" in (ROOT / "docker" / "server.Dockerfile").read_text()


@pytest.mark.parametrize("raw,why", [
    ({"version": "1.0", "build": 1}, "release version"),
    ({"version": "0.9.0", "build": -1}, "build"),
    ({"version": "0.9.0", "build": "3"}, "build"),
    ({"version": "0.9.0", "build": True}, "build"),
    ({"version": "0.9.0", "build": 1, "min_server": "0.9.1"}, "newer"),
    ({"version": "0.9.0", "build": 1, "min_app": "x"}, "release version"),
])
def test_a_bad_file_is_refused_with_the_reason(tmp_path, raw, why):
    f = tmp_path / "version.json"
    f.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=why):
        rv.read(f)


def test_defaults_and_header_forms(tmp_path):
    f = tmp_path / "version.json"
    f.write_text('{"version": "0.9.3", "build": 12}')
    rel = rv.read(f)
    assert (rel.min_app, rel.min_server, rel.header, rel.label) == ("0.9.3", "0.9.3", "0.9.3+12", "0.9.3 (build 12)")
    assert rv.parse_app_header("android/0.9.0+402400") == ("android", "0.9.0", 402400)
    assert rv.parse_app_header("ios/1.2.3") == ("ios", "1.2.3", None)
    for bad in (None, "", "android", "phone/0.9.0", "android/0.9", "android/0.9.0+x", "Android/0.9.0"):
        assert rv.parse_app_header(bad) is None


def test_app_too_old_rules():
    rel = rv.Release("0.9.4", 5, "0.9.2", "0.9.0")
    assert rv.app_too_old("android/0.9.1+500", rel, {}) ["code"] == "app_too_old"
    assert rv.app_too_old("android/0.9.2+500", rel, {}) is None
    assert rv.app_too_old("android/0.9.3+400", rel, {"android": 401})["min_app_version"] == {"android": 401}
    assert rv.app_too_old("android/0.9.3", rel, {"android": 401}) is None          # no build given: only the release counts
    assert rv.app_too_old("desktop/0.9.3+1", rel, {"desktop": 100000008}) is None  # the desktop package minimum stays advisory
    assert rv.app_too_old(None, rel, {}) is None and rv.app_too_old("junk", rel, {}) is None


async def test_the_server_shows_its_release_and_refuses_a_too_old_app(tmp_path, monkeypatch):
    # a server that demands 0.9.0, with every platform's 0.9.x really released (releases/apps.json shape)
    rel = rv.Release("0.9.3", 15, "0.9.0", "0.9.0")
    monkeypatch.setattr(rv, "current", lambda: rel)
    released = AppReleases.parse({p: [{"version": "0.9.3", "build": 600, "channel": "download", "date": "2026-11-13"}]
                                  for p in ("android", "ios", "desktop")})
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                              min_app_version={"android": 500}), tools=[], app_releases=released)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test") as c:
        r = await c.get("/healthz")
        assert r.json() == {"ok": True} and r.headers["x-kelo-server"] == rel.header
        about = (await c.get("/api/about", headers=AUTH)).json()
        assert (about["version"], about["build"], about["min_app"], about["min_server"]) == (rel.version, str(rel.build), rel.min_app,
                                                                                             rel.min_server)
        assert about["min_app_version"] == {"android": 500}
        # an app below min_app: 426 on /api/*, before auth; /api/about still answers so it can show "update needed"
        old = {"X-Kelo-App": "android/0.0.1+900", **AUTH}
        r = await c.get("/api/profile", headers=old)
        assert r.status_code == 426 and r.json()["code"] == "app_too_old" and r.json()["min_app"] == rel.min_app
        assert r.headers["x-kelo-server"] == rel.header
        assert (await c.get("/api/about", headers=old)).status_code == 200
        assert (await c.get("/healthz", headers=old)).status_code == 200
        # below the operator's build minimum
        assert (await c.get("/api/profile", headers={"X-Kelo-App": f"android/{rel.version}+499", **AUTH})).status_code == 426
        # current app, no header, an unreadable header: served
        for h in ({"X-Kelo-App": f"android/{rel.version}+500"}, {}, {"X-Kelo-App": "something else"}):
            r = await c.get("/api/profile", headers={**h, **AUTH})
            assert r.status_code == 200 and r.headers["x-kelo-server"] == rel.header


def test_cli_version_main(capsys):
    from assistant.cli import main
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"Kelo {rv.current().label}"


def _copy_repo(tmp_path):
    for name in ("version.json", "pyproject.toml", "uv.lock"):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\nintro\n\n## Unreleased\n- a change\n\n## 0.1.5\n- old\n")
    return tmp_path


def test_bump_writes_every_place_and_never_tags(tmp_path, capsys):
    root = _copy_repo(tmp_path)
    bump = _bump()
    cur = json.loads((root / "version.json").read_text())
    data = bump.bump(root, "9.8.7", today=dt.date(2026, 11, 10))
    assert data == {"version": "9.8.7", "build": cur["build"] + 1, "min_app": cur["min_app"], "min_server": cur["min_server"]}
    assert rv.read(root / "version.json").version == "9.8.7"
    assert re.search(r'(?m)^version = "9\.8\.7"$', (root / "pyproject.toml").read_text())
    assert 'name = "assistant"\nversion = "9.8.7"' in (root / "uv.lock").read_text()
    log = (root / "CHANGELOG.md").read_text()
    assert f"## Unreleased\n\n## 9.8.7 (build {cur['build'] + 1}, 2026-11-10)\n- a change\n" in log and log.count("## Unreleased") == 1
    assert bump.main(["9.8.8", "--build", "50", "--min-app", "9.8.7", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "git tag -a v9.8.8" in out and rv.read(root / "version.json").build == 50
    assert rv.read(root / "version.json").min_app == "9.8.7"
    for bad in (["9.8.7"], ["9.8.9", "--build", "50"], ["x.y"], ["9.9.0", "--min-server", "9.9.1"]):
        with pytest.raises(SystemExit):
            bump.main([*bad, "--root", str(root)])


def test_bump_levels_and_same_version_only_as_a_rebuild(tmp_path):
    """A change gets a new version by its kind (patch | minor | major), never by its size; the build counts builds by itself."""
    root = _copy_repo(tmp_path)
    bump = _bump()
    (root / "version.json").write_text('{"version": "0.9.1", "build": 13, "min_app": "0.9.0", "min_server": "0.9.0"}')
    assert bump.next_version("0.9.1", "patch") == "0.9.2" and bump.next_version("0.9.1", "minor") == "0.10.0"
    assert bump.next_version("0.9.1", "major") == "1.0.0"
    assert bump.main(["patch", "--root", str(root)]) == 0
    r = rv.read(root / "version.json")
    assert (r.version, r.build, r.min_app) == ("0.9.2", 14, "0.9.0")
    with pytest.raises(SystemExit):
        bump.main(["0.9.2", "--root", str(root)])          # same version, no --rebuild
    assert bump.main(["0.9.2", "--rebuild", "--root", str(root)]) == 0
    assert rv.read(root / "version.json").build == 15
    assert bump.main(["minor", "--root", str(root)]) == 0 and rv.read(root / "version.json").version == "0.10.0"


def test_bump_check_tag(tmp_path):
    root = _copy_repo(tmp_path)
    bump = _bump()
    ver = rv.read(root / "version.json").version
    assert bump.main(["--check-tag", f"v{ver}", "--root", str(root)]) == 0
    assert bump.main(["--check-tag", f"v{ver}-beta.1", "--root", str(root)]) == 0
    assert bump.main(["--check-tag", "v99.0.0", "--root", str(root)]) == 1
