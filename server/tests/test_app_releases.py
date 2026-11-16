"""app_releases.py: the record of released apps, the check, and `assistant release-record`."""

import json
from types import SimpleNamespace

import httpx
import pytest

from assistant import cli, downloads
from assistant.app_releases import AppReleases, RecordError, run_record
from assistant.downloads import PublishError, ServerPublisher, record_published, run_publish_server
from assistant.server import Settings, create_app

from test_downloads import FakeDocker, _operator_root
from test_release import D, manifest


def entry(version, build, channel="download", date="2026-11-13"):
    return {"version": version, "build": build, "channel": channel, "date": date}


RECORD = {"android": [entry("0.9.3", 409159), entry("0.10.0", 410000, "play"), entry("0.9.9", 409900)],
          "ios": [], "desktop": [entry("1.0.8", 100000008, "msstore")]}


def record():
    return AppReleases.parse(RECORD)


def test_latest_is_by_version_then_build():
    r = record()
    assert r.latest("android").version == "0.10.0"          # 0.10.0 is newer than 0.9.9 (numbers, not text)
    assert r.latest("ios") is None and r.latest("desktop").build == 100000008
    same = AppReleases.parse({"android": [entry("0.9.3", 5), entry("0.9.3", 7), entry("0.9.2", 99)]})
    assert same.latest("android").build == 7


@pytest.mark.parametrize("minimum, ok", [("0.9.0", True), ("0.10.0", True), ("0.10.1", False), ("0.9.3", True), ("1.0.0", False)])
def test_check_min_against_the_latest_version(minimum, ok):
    r = AppReleases.parse({p: [entry("0.10.0", 410000)] for p in ("android", "ios", "desktop")})
    assert (r.check_min(minimum) == []) is ok


def test_min_below_equal_and_above_the_latest():
    r = AppReleases.parse({"android": [entry("0.9.3", 1)], "ios": [entry("0.9.3", 1)], "desktop": [entry("0.9.3", 1)]})
    assert r.check_min("0.9.2") == [] and r.check_min("0.9.3") == []
    above = r.check_min("0.9.4")
    assert {p.platform for p in above} == {"android", "ios", "desktop"} and above[0].newest.version == "0.9.3"
    assert "0.9.4" in above[0].message and "0.9.3" in above[0].message and "android" in above[0].message


def test_ten_is_above_nine_not_below_it():
    r = AppReleases.parse({"android": [entry("0.9.3", 1)], "ios": [entry("0.9.3", 1)], "desktop": [entry("0.9.3", 1)]})
    assert len(r.check_min("0.10.0")) == 3                                  # 0.10.0 > 0.9.3 (a text comparison would say below)
    assert AppReleases.parse({p: [entry("0.10.0", 1)] for p in ("android", "ios", "desktop")}).check_min("0.9.3") == []


def test_a_platform_with_no_release_is_a_problem_when_it_has_a_minimum():
    r = record()
    assert [p.platform for p in r.check_min("0.9.0")] == ["ios"] and r.check_min("0.9.0")[0].newest is None
    assert "no ios app has been released" in r.check_min("0.9.0")[0].message
    assert r.check_min(None) == [] and r.check_min("0.0.0") == []            # no minimum demands nothing, even for ios
    assert [p.platform for p in r.check_min(None, {"ios": 5})] == ["ios"]
    assert r.check_min(None, {"ios": 0}) == []


def test_a_build_minimum_above_the_latest_build():
    r = record()
    assert r.check_min(None, {"android": 410000}) == []
    over = r.check_min(None, {"android": 410001, "desktop": 100000008})
    assert [(p.platform, p.wanted) for p in over] == [("android", "build 410001")] and over[0].newest.build == 410000
    assert [p.platform for p in r.check_min(None, {"desktop": 100000009})] == ["desktop"]


def test_not_a_version_raises():
    with pytest.raises(ValueError):
        record().check_min("latest")


def test_manifest_minimums_are_versions_or_builds():
    r = record()
    assert r.check_manifest({"android": "0.10.0", "desktop": "1.0.8"}) == []
    assert r.check_manifest({"android": "410000"}) == [] and len(r.check_manifest({"android": "9.9.9"})) == 1
    assert [p.platform for p in r.check_manifest({"android": "410001", "ios": "1.0.0", "desktop": "1.0.0"})] == ["android", "ios"]
    assert "not a version" in r.check_manifest({"android": "$(id)"})[0].message


def test_an_unreadable_record_or_a_missing_one_releases_nothing(monkeypatch, tmp_path):
    bad = tmp_path / "apps.json"
    bad.write_text('{"android": [{"version": "latest", "build": 1, "channel": "download", "date": "2026-11-13"}]}')
    with pytest.raises(RecordError):
        AppReleases.read(bad)
    monkeypatch.setenv("KELO_APP_RELEASES", str(bad))
    assert AppReleases.load().latest("android") is None                      # nothing counts as released: nothing is enforced


# ----------------------------------------------------------------------------------------------------------- release-record
def args(platform="android", version="0.9.4", build="409200", channel="play", file=None, date="2026-11-14"):
    return SimpleNamespace(platform=platform, version=version, build=build, channel=channel, file=str(file) if file else None, date=date)


def test_release_record_appends_and_the_file_stays_one_line_per_release(tmp_path):
    f = tmp_path / "apps.json"
    f.write_text(record().dumps())
    out: list[str] = []
    assert run_record(args(file=f), out.append) == 0 and "Recorded android 0.9.4" in out[0]
    r = AppReleases.read(f)
    assert r.latest("android").version == "0.10.0" and {x.version for x in r.entries["android"]} == {"0.9.3", "0.9.4", "0.9.9", "0.10.0"}
    assert f.read_text() == r.dumps() and json.loads(f.read_text())["android"][0]["channel"] in ("download", "play")


@pytest.mark.parametrize("change, why", [
    ({"platform": "windows"}, "unknown platform"), ({"platform": "web"}, "unknown platform"),
    ({"version": "latest"}, "not a release version"), ({"version": "0.9"}, "not a release version"), ({"version": "v0.9.4"}, "not a release version"),
    ({"build": "abc"}, "whole number"), ({"build": "0"}, "whole number above 0"),
    ({"channel": "testflight"}, "channel"), ({"channel": "internal"}, "channel"), ({"date": "13-11-2026"}, "date"),
])
def test_release_record_refuses_bad_input_and_changes_nothing(tmp_path, change, why):
    f = tmp_path / "apps.json"
    f.write_text(record().dumps())
    before, out = f.read_text(), []
    assert run_record(args(file=f, **change), out.append) == 1 and why in out[0]
    assert f.read_text() == before


def test_release_record_a_duplicate_is_not_added_twice(tmp_path):
    f = tmp_path / "apps.json"
    f.write_text(record().dumps())
    out: list[str] = []
    assert run_record(args(version="0.9.3", build="409159", file=f), out.append) == 0 and "already recorded" in out[0]
    assert f.read_text() == record().dumps()
    assert record().add("android", "0.9.3", 409159, "play") is False          # also when it is the same build through another channel


def test_release_record_is_a_cli_command(tmp_path, capsys):
    f = tmp_path / "apps.json"
    f.write_text(record().dumps())
    assert cli.main(["release-record", "ios", "1.0.0", "26111024", "appstore", "--date", "2026-11-14", "--file", str(f)]) == 0
    assert AppReleases.read(f).latest("ios").build == 26111024
    assert cli.main(["release-record", "ios", "1.0.0", "26111024", "testflight", "--file", str(f)]) == 1
    assert "channel" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------------------- publish-app
def test_publish_app_records_the_download_or_prints_the_line(tmp_path):
    out: list[str] = []
    row = {"platform": "android", "version": "0.9.4", "version_code": 409200}
    record_published(tmp_path, row, out.append)                              # a copy without the repo
    assert json.loads(out[0].splitlines()[-1].strip())["build"] == 409200 and "releases/apps.json" in out[0]
    (tmp_path / "releases").mkdir()
    (tmp_path / "releases" / "apps.json").write_text(record().dumps())
    out.clear()
    record_published(tmp_path, row, out.append)
    assert "Recorded android 0.9.4" in out[0] and AppReleases.read(tmp_path / "releases" / "apps.json").latest("android").version == "0.10.0"
    record_published(tmp_path, {"platform": "windows-msi", "version": "1.0.9"}, out.append)   # desktop: the build comes from the version
    assert {x.build for x in AppReleases.read(tmp_path / "releases" / "apps.json").entries["desktop"]} == {100000008, 100000009}
    out.clear()
    record_published(tmp_path, {"platform": "android", "version": "0.9.5"}, out.append)       # no build known: says what to run
    assert "assistant release-record android 0.9.5 <build> download" in out[0]


# ----------------------------------------------------------------------------------------------------------- publish-server
def test_publish_server_refuses_a_minimum_that_was_not_released(tmp_path):
    f = tmp_path / "release.json"
    f.write_text(json.dumps(manifest(min_app={"android": "9.9.9"})))
    root = _operator_root(tmp_path, "ASSISTANT_OPERATOR=1\n")
    docker, lines = FakeDocker(tmp_path / "vol"), []
    assert run_publish_server(SimpleNamespace(manifest=str(f)), root, run=docker, out=lines.append, releases=record()) == 1
    assert "android" in lines[-1] and "9.9.9" in lines[-1] and "0.10.0" in lines[-1] and not docker.calls   # nothing was touched
    with pytest.raises(PublishError, match="never released"):
        ServerPublisher(root, run=docker, out=lambda s: None).publish_server(f, releases=record())
    f.write_text(json.dumps(manifest(min_app={"android": "0.10.0", "desktop": "1.0.8"})))
    assert run_publish_server(SimpleNamespace(manifest=str(f)), root, run=docker, out=lines.append, releases=record()) == 0
    assert (tmp_path / "vol" / "manifest.json").is_file()


def test_the_manifest_tool_refuses_it_too(tmp_path, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("release_manifest", Path(__file__).resolve().parents[2] / "tools" / "release_manifest.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    digests = tmp_path / "digests"
    digests.mkdir()
    for comp, digest in D.items():
        (digests / comp).write_text(digest)
    rec = tmp_path / "apps.json"
    rec.write_text(record().dumps())
    monkeypatch.setenv("KELO_APP_RELEASES", str(rec))
    out = tmp_path / "release.json"
    base = ["--version", "0.5.0", "--digests-dir", str(digests), "--out", str(out), "--date", "2026-11-09T00:00:00Z"]
    assert tool.main([*base, "--min-app", "android=9.9.9"]) == 1 and not out.exists()
    assert "android" in capsys.readouterr().err
    assert tool.main([*base, "--min-app", "android=0.10.0"]) == 0 and out.is_file()


# ---------------------------------------------------------------------------------------------------------------- server start
async def test_a_server_started_with_an_unreleased_minimum_starts_and_does_not_enforce_it(tmp_path, caplog):
    s = Settings(token="t" * 32, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                 min_app_version={"android": 999999, "desktop": 100000008})
    app = create_app(s, tools=[], app_releases=record())                       # does not raise
    auth = {"Authorization": "Bearer " + "t" * 32}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test") as c:
        about = (await c.get("/api/about", headers=auth)).json()
        assert about["min_app_ignored"] == ["android"] and about["min_app_version"] == {"desktop": 100000008}
        assert (await c.get("/api/profile", headers=auth)).json()["min_app_version"] == {"desktop": 100000008}
        # the ignored platform is not locked out; the one with a real minimum still is
        assert (await c.get("/api/profile", headers={**auth, "X-Kelo-App": "android/0.9.3+1"})).status_code == 200
        assert (await c.get("/api/profile", headers={**auth, "X-Kelo-App": "desktop/0.9.3+1"})).status_code == 200   # desktop minimum is advisory
    assert any("android" in r.getMessage() and "IGNORED" in r.getMessage() for r in caplog.records)


async def test_a_misbuilt_image_with_min_app_above_the_record_sends_no_426(tmp_path, monkeypatch):
    from assistant import version as rv
    monkeypatch.setattr(rv, "current", lambda: rv.Release("0.9.3", 15, "0.9.4", "0.9.0"))   # min_app 0.9.4: android's newest is 0.10.0
    r = AppReleases.parse({"android": [entry("0.9.3", 1)], "ios": [entry("0.9.4", 2)], "desktop": [entry("0.9.3", 3)]})
    s = Settings(token="t" * 32, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"))
    app = create_app(s, tools=[], app_releases=r)
    auth = {"Authorization": "Bearer " + "t" * 32}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test") as c:
        assert (await c.get("/api/profile", headers={**auth, "X-Kelo-App": "android/0.9.0+1"})).status_code == 200   # released only up to 0.9.3
        assert (await c.get("/api/profile", headers={**auth, "X-Kelo-App": "ios/0.9.0+1"})).status_code == 426       # 0.9.4 is released for ios
        about = (await c.get("/api/about", headers=auth)).json()
        assert about["min_app_ignored"] == ["android", "desktop"]
