"""App downloads (downloads.py): only files the publisher's manifest names, with the right headers and byte ranges; the website's
download buttons; `assistant publish-app` (with a fake docker); and which server counts as the operator's."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from assistant import cli, downloads, website
from assistant.downloads import Downloads, Publisher, PublishError, names_itself, operator_flag, run_publish
from assistant.server import Settings, create_app

from test_devices import add_phone, bearer
from test_server import AUTH, TOKEN, Running, scripted, settings

APK = b"PK\x03\x04" + bytes(range(256)) * 40          # 10 KB of something
DMG = b"koly" * 3000


def publish_here(folder: Path, platform: str, name: str, data: bytes, version: str = "1.2.3", **extra) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    path = folder / "manifest.json"
    rows = json.loads(path.read_text())["platforms"] if path.exists() else {}
    rows[platform] = {"file": name, "version": version, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "date": "2026-11-08T09:00:00Z", **extra}
    path.write_text(json.dumps({"platforms": rows}))
    return rows[platform]


def app_with(folder: Path, **kw):
    return create_app(settings(downloads_dir=str(folder), **kw), tools=[], stream_fn=scripted())


# ---------------------------------------------------------------------------------------------------------------- routes
async def test_a_published_file_is_served_with_its_headers_and_the_stable_link_points_at_it(tmp_path):
    row = publish_here(tmp_path, "android", "kelo-0.1.402102.apk", APK, "0.1.402102")
    async with Running(app_with(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.get("/download/android")
        assert r.status_code == 302 and r.headers["location"] == "/download/android/kelo-0.1.402102.apk"
        assert r.headers["cache-control"] == "no-cache"
        plural = await c.get("/downloads/android")          # typed with an "s": the same newest APK
        assert plural.status_code == 302 and plural.headers["location"] == "/download/android/kelo-0.1.402102.apk"
        assert (await c.get("/apkdownload")).status_code == 404   # the old short address is gone
        f = await c.get("/download/android/kelo-0.1.402102.apk")
        assert f.status_code == 200 and f.content == APK
        assert f.headers["content-type"] == "application/vnd.android.package-archive"
        assert f.headers["content-disposition"] == 'attachment; filename="kelo-0.1.402102.apk"'
        assert f.headers["content-length"] == str(len(APK)) and f.headers["accept-ranges"] == "bytes"
        assert f.headers["etag"] == f'"{row["sha256"][:32]}"' and "max-age" in f.headers["cache-control"]
        assert f.headers["x-content-type-options"] == "nosniff"
        again = await c.get("/download/android/kelo-0.1.402102.apk", headers={"If-None-Match": f.headers["etag"]})
        assert again.status_code == 304 and again.content == b""
        head = await c.head("/download/android/kelo-0.1.402102.apk")
        assert head.status_code == 200 and head.headers["content-length"] == str(len(APK)) and head.content == b""
        followed = await c.get("/download/android", follow_redirects=True)
        assert followed.content == APK


async def test_byte_ranges_resume_a_download(tmp_path):
    publish_here(tmp_path, "mac", "kelo-1.0.0.dmg", DMG, "1.0.0")
    async with Running(app_with(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.get("/download/mac/kelo-1.0.0.dmg", headers={"Range": "bytes=100-199"})
        assert r.status_code == 206 and r.content == DMG[100:200]
        assert r.headers["content-range"] == f"bytes 100-199/{len(DMG)}" and r.headers["content-length"] == "100"
        tail = await c.get("/download/mac/kelo-1.0.0.dmg", headers={"Range": "bytes=-10"})
        assert tail.status_code == 206 and tail.content == DMG[-10:]
        bad = await c.get("/download/mac/kelo-1.0.0.dmg", headers={"Range": f"bytes={len(DMG) + 5}-"})
        assert bad.status_code == 416
        # If-Range with an old ETag: the whole (new) file, not a piece of it
        whole = await c.get("/download/mac/kelo-1.0.0.dmg", headers={"Range": "bytes=0-9", "If-Range": '"old"'})
        assert whole.status_code == 200 and whole.content == DMG


async def test_only_files_the_manifest_names_are_served_and_nothing_is_listed(tmp_path):
    publish_here(tmp_path, "android", "kelo-2.0.0.apk", APK, "2.0.0")
    (tmp_path / "kelo-1.0.0.apk").write_bytes(APK)                       # an older build left in the folder
    (tmp_path / "secret.txt").write_text("no")
    os.symlink(tmp_path / "secret.txt", tmp_path / "link.apk")
    async with Running(app_with(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        for path in ("/download/android/kelo-1.0.0.apk", "/download/android/secret.txt", "/download/android/manifest.json",
                     "/download/windows/kelo-2.0.0.apk", "/download/android/link.apk",
                     "/download/manifest.json", "/download/secret.txt", "/download/nope", "/download/windows", "/download/mac",
                     "/download/android/..%2Fsecret.txt", "/download/android/%2e%2e/secret.txt", "/download/..%2Fsecret.txt",
                     "/download/android/../secret.txt"):
            r = await c.get(path)
            assert r.status_code in (404, 405), (path, r.status_code)
            assert b"no" != r.content and b"kelo-1.0.0" not in r.content
        page = await c.get("/download")   # the download page, not a listing of the folder
        assert page.status_code == 200 and "text/html" in page.headers["content-type"] and "kelo-1.0.0.apk" not in page.text
        assert (await c.get("/download/android/kelo-2.0.0.apk")).status_code == 200


async def test_a_manifest_pointing_outside_or_at_a_half_copied_file_serves_nothing(tmp_path):
    folder = tmp_path / "downloads"
    publish_here(folder, "android", "kelo-1.0.0.apk", APK, "1.0.0")
    publish_here(folder, "windows", "kelo-1.0.0.exe", b"MZ" * 100, "1.0.0")
    (folder / "kelo-1.0.0.exe").write_bytes(b"MZ" * 10)                  # still being copied: the size is not the manifest's
    raw = json.loads((folder / "manifest.json").read_text())
    raw["platforms"]["mac"] = {**raw["platforms"]["android"], "file": "../outside.dmg"}
    raw["platforms"]["windows-msi"] = {**raw["platforms"]["android"], "file": "kelo.apk"}   # wrong extension
    (folder / "manifest.json").write_text(json.dumps(raw))
    (tmp_path / "outside.dmg").write_bytes(APK)
    async with Running(app_with(folder)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/download/windows")).status_code == 404
        assert (await c.get("/download/mac")).status_code == 404
        assert (await c.get("/download/windows-msi")).status_code == 404
        latest = (await c.get("/download/latest.json")).json()
        assert set(latest) == {"android"}


async def test_missing_folder_or_file_is_a_plain_404(tmp_path):
    async with Running(app_with(tmp_path / "nothing")) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/download/android")).status_code == 404
        assert (await c.get("/download/latest.json")).json() == {}
    publish_here(tmp_path / "d", "android", "kelo-1.0.0.apk", APK, "1.0.0")
    (tmp_path / "d" / "kelo-1.0.0.apk").unlink()
    async with Running(app_with(tmp_path / "d")) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/download/android")).status_code == 404
        assert (await c.get("/download/android/kelo-1.0.0.apk")).status_code == 404


async def test_latest_json_lists_version_size_sha_and_date_per_platform(tmp_path):
    a = publish_here(tmp_path, "android", "kelo-0.1.5.apk", APK, "0.1.5")
    w = publish_here(tmp_path, "windows", "kelo-1.0.0.exe", b"MZ" * 50, "1.0.0")
    publish_here(tmp_path, "windows-msi", "kelo-1.0.0.msi", b"\xd0\xcf" * 50, "1.0.0")
    async with Running(app_with(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.get("/download/latest.json")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        assert r.headers["access-control-allow-origin"] == "*" and "max-age=60" in r.headers["cache-control"]
        body = r.json()
        assert set(body) == {"android", "windows", "windows-msi"}
        assert body["android"] == {"version": "0.1.5", "size": len(APK), "sha256": a["sha256"], "date": "2026-11-08T09:00:00Z",
                                   "file": "kelo-0.1.5.apk", "url": "/download/android"}
        assert body["windows"]["sha256"] == w["sha256"]
        assert (await c.get("/download/latest.json", headers={"If-None-Match": r.headers["etag"]})).status_code == 304
        msi = await c.get("/download/windows-msi/kelo-1.0.0.msi")
        assert msi.headers["content-type"] == "application/x-msi"


def test_a_manifest_rewritten_mid_read_keeps_the_last_good_one(tmp_path):
    publish_here(tmp_path, "android", "kelo-1.0.0.apk", APK, "1.0.0")
    d = Downloads(tmp_path)
    assert set(d.manifest()) == {"android"}
    (tmp_path / "manifest.json").write_text('{"platforms": {"andr')            # half written
    assert set(d.manifest()) == {"android"}
    assert Downloads(tmp_path).manifest() == {}                              # nothing good seen before: nothing


# ---------------------------------------------------------------------------------------------------------------- website
async def test_the_home_page_shows_download_buttons_with_version_and_size_or_a_fallback(tmp_path):
    async with Running(app_with(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        en = (await c.get("/?lang=en")).text
        ko = (await c.get("/?lang=ko")).text
        assert 'id="get-app"' in en and en.count("Not published yet") == 3 and "/download/android" not in en.replace("latest.json", "")
        assert ko.count("아직 올라오지 않았어요") == 3
        assert "{{dl:" not in en and "{{dl:" not in ko
        assert "More info → Run anyway" in en and "notarized by Apple, so it opens right away" in en and "Open Anyway" not in en and "allow installing apps from this source" in en
        assert "추가 정보 → 실행" in ko and "Apple 공증을 받" in ko and "그래도 열기" not in ko and "이 출처" in ko
        assert "Google Play comes after the beta" in en and "apps.apple.com" in en and "apps.microsoft.com" in en
        tag = (await c.get("/?lang=en")).headers["etag"]
        publish_here(tmp_path, "android", "kelo-0.1.402102.apk", b"x" * 28_978_903, "0.1.402102")
        publish_here(tmp_path, "windows", "kelo-1.0.0.exe", b"x" * 1000, "1.0.0")
        r = await c.get("/?lang=en", headers={"If-None-Match": tag})
        assert r.status_code == 200                                          # a new build changes the page (and its ETag) at once
        en = r.text
        assert 'href="/download/android"' in en and "Version 0.1.402102 · 27.6 MB" in en
        assert 'href="/download/windows"' in en and 'href="/download/windows-msi"' not in en
        assert en.count("Not published yet") == 1                            # macOS only
        ko = (await c.get("/?lang=ko")).text
        assert "버전 0.1.402102 · 27.6 MB" in ko


def test_copy_rules_on_the_download_section():
    for lang in ("ko", "en"):
        html = website.render("index", lang)
        section = html[html.index('id="get-app"'):html.index('id="faq"')]
        for banned in ("your computer", "내 컴퓨터", "OAuth", "subscription", "구독", "wallet", "crypto", "블록체인"):
            assert banned not in section, (lang, banned)


def test_human_size():
    assert downloads.human_size(28_978_903) == "27.6 MB"
    assert downloads.human_size(150 * 1024 * 1024) == "150 MB"


# ---------------------------------------------------------------------------------------------------------------- operator
def test_which_server_is_the_operators():
    assert names_itself("https://kelo.chat", "https://kelo.chat/") is True
    assert names_itself("https://KELO.chat/x", "", "https://kelo.chat") is True
    assert names_itself("https://me.kelo.chat", "https://kelo.chat") is False
    assert names_itself("", "https://kelo.chat") is False
    assert names_itself("https://kelo.chat") is False
    assert operator_flag("1") is True and operator_flag("no") is False and operator_flag("") is None and operator_flag(None) is None


async def test_operator_status_is_owner_only_and_true_only_on_the_operators_server(tmp_path):
    def app(**kw):
        return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / f"{len(kw)}-{sorted(kw)}.db"),
                                   **kw), tools=[], stream_fn=scripted())

    cases = [({}, False),                                                                  # a self-hosted copy
             ({"public_url": "https://me.kelo.chat", "oauth_broker": "https://kelo.chat"}, False),
             ({"public_url": "https://kelo.chat", "oauth_broker": "https://kelo.chat"}, True),   # names itself as the broker
             ({"public_url": "https://kelo.chat", "oauth_broker": "https://kelo.chat", "operator": False}, False),
             ({"operator": True}, True)]
    for kw, want in cases:
        async with Running(app(**kw)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
            r = await c.get("/api/operator/status", headers=AUTH)
            assert r.status_code == 200 and r.json() == {"owner": True, "operator": want}, kw
            assert (await c.get("/api/operator/status")).status_code == 401
            _, ph = await add_phone(c, "kid")
            ok = await c.get("/api/operator/status", headers=bearer(ph.json()["token"]))    # every device has the same rights
            assert ok.status_code == 200 and ok.json() == {"owner": True, "operator": want}


def test_settings_read_the_operator_flag_and_downloads_folder():
    base = {"ASSISTANT_TOKEN": "x" * 20}
    assert Settings.from_env(base).operator is None and Settings.from_env(base).downloads_dir == ""
    s = Settings.from_env({**base, "ASSISTANT_OPERATOR": "1", "ASSISTANT_DOWNLOADS_DIR": "/srv/dl"})
    assert s.operator is True and s.downloads_dir == "/srv/dl"
    assert Settings.from_env({**base, "ASSISTANT_OPERATOR": "0"}).operator is False


# ---------------------------------------------------------------------------------------------------------------- publish-app
class FakeDocker:
    """Stands in for `docker compose ps` and `docker cp`: the container's /state/downloads is a folder on disk."""

    def __init__(self, volume: Path, cid: str = "abc123"):
        self.volume, self.cid, self.calls = volume, cid, []
        self.root_fails = False
        self.exec_fails = False

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["docker", "compose"] and "ps" in cmd:
            return SimpleNamespace(returncode=0, stdout=self.cid + "\n" if self.cid else "", stderr="")
        if cmd[:2] == ["docker", "cp"]:
            src, dst = cmd[2], cmd[3]
            if src.startswith(self.cid + ":"):                 # out of the container
                inside = self.volume / Path(src.split(":", 1)[1]).name
                if not inside.exists():
                    return SimpleNamespace(returncode=1, stdout="", stderr=f"Error response from daemon: Could not find the file {src}")
                Path(dst).write_bytes(inside.read_bytes())
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            assert dst == f"{self.cid}:/state/downloads" and src.endswith(f"{os.sep}.")
            self.volume.mkdir(exist_ok=True)
            for f in Path(src[:-2]).iterdir():
                (self.volume / f.name).write_bytes(f.read_bytes())
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[1:3] == ["dump", "badging"]:
            return SimpleNamespace(returncode=0, stdout="package: name='chat.kelo.app' versionCode='402102' versionName='0.1.402102'\n", stderr="")
        if cmd[:2] == ["docker", "exec"]:                      # INSTALLERS_HELPER, run for real on the fake volume
            if self.exec_fails:
                return SimpleNamespace(returncode=1, stdout="", stderr="Error response from daemon: container is not running")
            i = cmd.index("-c")
            script, args = cmd[i + 1], list(cmd[i + 2:])
            assert args[1] == "/state/downloads"
            if "-u" in cmd[:i] and self.root_fails and args[0] == "remove-installers":
                return SimpleNamespace(returncode=0, stdout=json.dumps({"removed": [], "failed": args[2:]}), stderr="")
            args[1] = str(self.volume)
            r = subprocess.run([sys.executable, "-c", script, *args], capture_output=True, text=True, timeout=60)
            return SimpleNamespace(returncode=r.returncode, stdout=r.stdout, stderr=r.stderr)
        raise AssertionError(cmd)


def test_publish_copies_the_file_first_then_writes_the_manifest(tmp_path):
    volume = tmp_path / "vol"
    docker, lines = FakeDocker(volume), []
    apk = tmp_path / "kelo-0.1.0.apk"
    apk.write_bytes(APK)
    pub = Publisher(tmp_path, run=docker, out=lines.append)
    downloads_aapt2 = downloads.find_aapt2
    downloads.find_aapt2 = lambda: "/sdk/aapt2"
    try:
        got = pub.publish("android", apk, now=lambda: datetime(2026, 11, 8, 9, 30, tzinfo=UTC), unsigned=True)
    finally:
        downloads.find_aapt2 = downloads_aapt2
    assert got["version"] == "0.1.402102" and got["file"] == "kelo-0.1.402102-402102.apk"   # aapt2's versionName and versionCode
    assert (volume / "kelo-0.1.402102-402102.apk").read_bytes() == APK
    rows = json.loads((volume / "manifest.json").read_text())["platforms"]
    assert rows == {"android": {"file": "kelo-0.1.402102-402102.apk", "version": "0.1.402102", "version_code": 402102, "size": len(APK),
                                "sha256": hashlib.sha256(APK).hexdigest(), "date": "2026-11-08T09:30:00Z"}}
    cps = [c for c in docker.calls if c[:2] == ["docker", "cp"] and not c[2].startswith("abc123:")]
    assert len(cps) == 2 and "file" in cps[0][2] and "meta" in cps[1][2]                 # the file goes in before the manifest
    assert any("https://kelo.chat/download/android" in line for line in lines)
    # and the server serves exactly that
    assert Downloads(volume).file("android")[1]["sha256"] == hashlib.sha256(APK).hexdigest()


def test_publish_keeps_the_other_platforms_and_picks_msi_and_versions(tmp_path):
    volume = tmp_path / "vol"
    docker = FakeDocker(volume)
    exe, msi, dmg = tmp_path / "Kelo-1.0.0.exe", tmp_path / "Kelo-1.0.0.msi", tmp_path / "Kelo.dmg"
    exe.write_bytes(b"MZ" * 10)
    msi.write_bytes(b"\xd0\xcf" * 10)
    dmg.write_bytes(DMG)
    pub = Publisher(tmp_path, run=docker, out=lambda s: None)
    pub.publish("windows", exe, unsigned=True)
    pub.publish("windows", msi, unsigned=True)                                     # an .msi given as "windows" is the MSI download
    with pytest.raises(PublishError, match="--version"):
        pub.publish("mac", dmg, unsigned=True)                                     # no version in the name
    pub.publish("mac", dmg, "v1.0.1", unsigned=True)
    rows = json.loads((volume / "manifest.json").read_text())["platforms"]
    assert {p: r["file"] for p, r in rows.items()} == {"windows": "kelo-1.0.0.exe", "windows-msi": "kelo-1.0.0.msi", "mac": "kelo-1.0.1.dmg"}
    with pytest.raises(PublishError, match="must be a .dmg"):
        pub.publish("mac", exe, unsigned=True)
    with pytest.raises(PublishError, match="must be a .apk"):
        pub.publish("android", dmg, "1.0", unsigned=True)
    with pytest.raises(PublishError, match="not a version"):
        pub.publish("mac", dmg, "1.0/../../x", unsigned=True)
    with pytest.raises(PublishError, match="no such file"):
        pub.publish("mac", tmp_path / "missing.dmg", "1.0", unsigned=True)
    with pytest.raises(PublishError, match="container was not found"):
        Publisher(tmp_path, run=FakeDocker(volume, cid=""), out=lambda s: None).publish("mac", dmg, "1.0.0", unsigned=True)


def test_which_old_installers_go(tmp_path):
    from assistant.downloads import installer_platform, old_installers
    assert [installer_platform(n) for n in ("kelo-1.0-402500.apk", "kelo-1.0.exe", "kelo-1.0.msi", "kelo-server-1.0.msi", "kelo-1.0.dmg",
                                            "kelo-server-1.0.zip", "kelo-1.0.zip", "manifest.json", "notices.json", "other.apk")] == \
        ["android", "windows", "windows-msi", "windows-server-msi", "mac", "mac-server-bundle", None, None, None, None]
    f = lambda name, t: {"name": name, "mtime": t}  # noqa: E731
    files = [f("kelo-1.0.apk", 1), f("kelo-1.1.apk", 2), f("kelo-1.2.apk", 3), f("kelo-1.3.apk", 4),
             f("kelo-1.0.msi", 1), f("kelo-1.1.msi", 2), f("kelo-server-0.9.msi", 1), f("kelo-server-1.0.msi", 2), f("kelo-server-1.1.msi", 3),
             f("kelo-0.8.dmg", 1), f("kelo-0.9.dmg", 2), f("manifest.json", 9), f("notices.json", 9), f("kelo-1.0.zip", 1)]
    named = ["kelo-1.2.apk", "kelo-1.1.msi", "kelo-server-1.1.msi"]       # 1.3.apk is newer but not named (a copy in progress)
    assert sorted(old_installers(files, named)) == ["kelo-1.0.apk", "kelo-1.0.msi", "kelo-1.1.apk", "kelo-1.3.apk",
                                                    "kelo-server-0.9.msi", "kelo-server-1.0.msi"]   # owner 2026-11-20: only the published file stays
    assert old_installers(files, named, keep_previous=2) == ["kelo-1.0.apk"]
    assert old_installers(files, None) == []                               # no readable manifest: nothing goes
    assert old_installers(files, ["kelo-1.0.apk", "kelo-1.1.apk"]) == ["kelo-1.2.apk", "kelo-1.3.apk"]   # a named file never goes, whatever its age


def test_a_publish_removes_older_installers_and_keeps_only_the_published_one(tmp_path):
    volume = tmp_path / "vol"
    volume.mkdir()
    for i, name in enumerate(("kelo-0.7.dmg", "kelo-0.8.dmg", "kelo-0.9.dmg", "kelo-1.0.exe", "kelo-0.9.exe", "notices.json")):
        (volume / name).write_bytes(b"x" * 10)
        os.utime(volume / name, (1_700_000_000 + i, 1_700_000_000 + i))
    (volume / "manifest.json").write_text(json.dumps({"platforms": {
        "mac": {"file": "kelo-0.9.dmg", "version": "0.9", "size": 10, "sha256": "0" * 64, "date": "2026-11-01T00:00:00Z"},
        "windows": {"file": "kelo-1.0.exe", "version": "1.0", "size": 10, "sha256": "0" * 64, "date": "2026-11-01T00:00:00Z"}}}))
    docker, lines = FakeDocker(volume), []
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    Publisher(tmp_path, run=docker, out=lines.append).publish("mac", dmg, "1.0.0", unsigned=True)
    assert sorted(p.name for p in volume.iterdir()) == ["kelo-1.0.0.dmg", "kelo-1.0.exe", "manifest.json", "notices.json"]
    execs = [c for c in docker.calls if c[:2] == ["docker", "exec"]]
    assert any(c[2:4] == ["-u", "0"] and "remove-installers" in c for c in execs)
    assert any("Removed 4 older installers (kelo-0.7.dmg, kelo-0.8.dmg, kelo-0.9.dmg, kelo-0.9.exe)" in x for x in lines)


def test_installers_root_could_not_remove_are_removed_by_the_servers_user(tmp_path):
    volume = tmp_path / "vol"
    volume.mkdir()
    for i, name in enumerate(("kelo-0.8.dmg", "kelo-0.9.dmg")):
        (volume / name).write_bytes(b"x")
        os.utime(volume / name, (1_700_000_000 + i, 1_700_000_000 + i))
    (volume / "manifest.json").write_text(json.dumps({"platforms": {
        "mac": {"file": "kelo-0.9.dmg", "version": "0.9", "size": 1, "sha256": "0" * 64, "date": "2026-11-01T00:00:00Z"}}}))
    docker = FakeDocker(volume)
    docker.root_fails = True
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    Publisher(tmp_path, run=docker, out=lambda s: None).publish("mac", dmg, "1.0.0", unsigned=True)
    assert sorted(p.name for p in volume.iterdir()) == ["kelo-1.0.0.dmg", "manifest.json"]


def test_a_failing_cleanup_never_fails_the_publish(tmp_path):
    volume = tmp_path / "vol"
    docker, lines = FakeDocker(volume), []
    docker.exec_fails = True
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    got = Publisher(tmp_path, run=docker, out=lines.append).publish("mac", dmg, "1.0.0", unsigned=True)
    assert got["file"] == "kelo-1.0.0.dmg" and (volume / "kelo-1.0.0.dmg").exists()
    assert any("Removing older installers was skipped" in x for x in lines)


def _operator_root(tmp_path, env: str) -> Path:
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text(env)
    return tmp_path


def test_publish_app_runs_only_on_the_operators_server(tmp_path):
    dmg = tmp_path / "Kelo-1.0.0.dmg"
    dmg.write_bytes(DMG)
    args = SimpleNamespace(platform="mac", file=str(dmg), version=None, unsigned=True)
    lines: list[str] = []
    root = _operator_root(tmp_path, "ASSISTANT_PUBLIC_URL=https://me.kelo.chat\nASSISTANT_KELO_BROKER=https://kelo.chat\n")
    docker = FakeDocker(tmp_path / "vol")
    assert run_publish(args, root, run=docker, out=lines.append) == 1
    assert "operator's server" in lines[-1] and not docker.calls
    (tmp_path / ".env").write_text("ASSISTANT_PUBLIC_URL=https://kelo.chat\nASSISTANT_OAUTH_BROKER=https://kelo.chat\n")
    lines.clear()
    assert run_publish(args, root, run=docker, out=lines.append) == 0
    assert any(line == "Published macOS 1.0.0: https://kelo.chat/download/mac" for line in lines)
    (tmp_path / ".env").write_text("ASSISTANT_PUBLIC_URL=https://dl.example.com\nASSISTANT_OPERATOR=1\n")
    lines.clear()
    assert run_publish(args, root, run=docker, out=lines.append) == 0
    assert any("https://dl.example.com/download/mac" in line for line in lines)
    assert run_publish(args, tmp_path / "elsewhere", run=docker, out=lines.append) == 1


def test_publish_app_is_a_cli_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(downloads, "run_publish", lambda a: seen.setdefault("args", a) and 0)
    assert cli.main(["publish-app", "android", "/x/kelo.apk", "--version", "0.1.2"]) == 0
    assert (seen["args"].platform, seen["args"].file, seen["args"].version) == ("android", "/x/kelo.apk", "0.1.2")
    with pytest.raises(SystemExit):
        cli.main(["publish-app", "ios", "/x/kelo.ipa"])


def test_both_v2_designs_fill_the_download_buttons_the_same_way():
    apps = {"android": {"version": "0.1.402102", "size": 28_978_903}, "windows": {"version": "1.0.0", "size": 1000}}
    for design in website.DESIGNS:
        bare = {lang: website.render("index", lang, design=design) for lang in website.LANGS}
        assert bare["en"].count("Not published yet") == 3 and bare["ko"].count("아직 올라오지 않았어요") == 3, design
        en = website.render("index", "en", apps, design=design)
        ko = website.render("index", "ko", apps, design=design)
        assert "{{dl:" not in en and "{{dl:" not in ko
        assert 'href="/download/android"' in en and "Version 0.1.402102 · 27.6 MB" in en and "버전 0.1.402102 · 27.6 MB" in ko, design
        assert 'href="/download/windows"' in en and 'href="/download/windows-msi"' not in en and en.count("Not published yet") == 1, design
        for lang, html in (("en", en), ("ko", ko)):
            section = html[html.index('id="get-app"'):html.index('id="faq"')]
            assert "More info → Run anyway" in section or "추가 정보 → 실행" in section, (design, lang)
            for banned in ("your computer", "내 컴퓨터", "OAuth", "subscription", "구독", "wallet", "crypto", "블록체인"):
                assert banned not in section, (design, lang, banned)


def test_publish_takes_version_and_build_and_latest_json_carries_the_build(tmp_path):
    """Android compares the build number: 0.9.0 with a higher build is offered. The name stays in "version" for older apps."""
    volume = tmp_path / "vol"
    pub = Publisher(tmp_path, run=FakeDocker(volume), out=lambda s: None)
    apk = tmp_path / "app-release.apk"
    apk.write_bytes(APK)
    got = pub.publish("android", apk, "0.9.0+402500", unsigned=True)
    assert (got["version"], got["version_code"], got["file"]) == ("0.9.0", 402500, "kelo-0.9.0-402500.apk")
    latest = Downloads(volume).latest("https://kelo.chat")
    assert latest["android"]["version"] == "0.9.0" and latest["android"]["version_code"] == 402500
    pub.publish("android", apk, "0.9.0+402600", unsigned=True)                                  # same name, higher build
    assert Downloads(volume).latest()["android"]["version_code"] == 402600
    for bad in ("0.9.0+", "0.9.0+x", "0.9.0+-1"):
        with pytest.raises(PublishError, match="build"):
            pub.publish("android", apk, bad, unsigned=True)
    dmg = tmp_path / "Kelo.dmg"
    dmg.write_bytes(DMG)
    pub.publish("mac", dmg, "1.0.8", unsigned=True)                                             # no build: no version_code (desktop keeps its own)
    assert "version_code" not in Downloads(volume).latest()["mac"]
    # an old manifest row without the field still reads; a bad value is left out
    (volume / "manifest.json").write_text(json.dumps({"platforms": {"android": {**json.loads((volume / "manifest.json").read_text())
                                                                              ["platforms"]["android"], "version_code": "402600"}}}))
    assert "version_code" not in Downloads(volume).manifest()["android"]


# ------------------------------------------------------------------------------------- a separate operator copy (KELO_OPERATOR_INSTANCE)
def test_the_publisher_finds_the_server_container_of_the_configured_operator_copy(tmp_path, monkeypatch):
    docker = FakeDocker(tmp_path / "vol")
    monkeypatch.delenv("KELO_OPERATOR_INSTANCE", raising=False)
    assert Publisher(tmp_path, run=docker, out=lambda s: None).container() == "abc123"
    assert "-p" not in docker.calls[-1] and "--env-file" not in docker.calls[-1]     # default: main, exactly as before
    monkeypatch.setenv("KELO_OPERATOR_INSTANCE", "main")
    Publisher(tmp_path, run=docker, out=lambda s: None).container()
    assert "-p" not in docker.calls[-1]
    monkeypatch.setenv("KELO_OPERATOR_INSTANCE", "operator")
    assert Publisher(tmp_path, run=docker, out=lambda s: None).container() == "abc123"
    cmd = docker.calls[-1]
    assert cmd[cmd.index("-p") + 1] == "assistant-u-operator"
    assert cmd[cmd.index("--env-file") + 1] == str(tmp_path / "instances" / "operator" / "env")
    monkeypatch.setenv("KELO_OPERATOR_INSTANCE", "../x")
    calls = len(docker.calls)
    with pytest.raises(PublishError, match="not a copy name"):
        Publisher(tmp_path, run=docker, out=lambda s: None).container()
    assert len(docker.calls) == calls
    monkeypatch.setenv("KELO_OPERATOR_INSTANCE", "operator")
    with pytest.raises(PublishError, match="operator copy's server container was not found"):
        Publisher(tmp_path, run=FakeDocker(tmp_path / "vol", cid=""), out=lambda s: None).container()


def test_the_operator_check_reads_the_configured_copys_settings(tmp_path, monkeypatch):
    root = _operator_root(tmp_path, "ASSISTANT_PUBLIC_URL=https://main.example.com\n")       # main is NOT the operator
    (root / "instances" / "operator").mkdir(parents=True)
    (root / "instances" / "operator" / "env").write_text("ASSISTANT_OPERATOR=1\nASSISTANT_PUBLIC_URL=https://operator.kelo.chat\n")
    lines: list[str] = []
    monkeypatch.delenv("KELO_OPERATOR_INSTANCE", raising=False)
    assert downloads._operator_public(root, lines.append) is None and "operator's server" in lines[-1]
    monkeypatch.setenv("KELO_OPERATOR_INSTANCE", "operator")
    assert downloads._operator_public(root, lines.append) == "https://operator.kelo.chat"
    monkeypatch.setenv("KELO_OPERATOR_INSTANCE", "nosuch")                                   # its settings file is missing
    assert downloads._operator_public(root, lines.append) is None and "error" in lines[-1]
    monkeypatch.setenv("KELO_OPERATOR_INSTANCE", "Bad Name")
    assert downloads._operator_public(root, lines.append) is None and "not a copy name" in lines[-1]


# ---------------------------------------------------------------------------------------------------------- Kelo server MSI
MSI = b"\xd0\xcf\x11\xe0" * 40


async def test_the_server_msi_is_served_and_in_latest_json_but_not_on_the_download_page(tmp_path):
    row = publish_here(tmp_path, "windows-server-msi", "kelo-server-0.10.2.msi", MSI, "0.10.2")
    publish_here(tmp_path, "windows-msi", "kelo-1.0.0.msi", b"\xd0\xcf" * 50, "1.0.0")
    async with Running(app_with(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.get("/download/windows-server-msi")
        assert r.status_code == 302 and r.headers["location"] == "/download/windows-server-msi/kelo-server-0.10.2.msi"
        f = await c.get("/download/windows-server-msi/kelo-server-0.10.2.msi")
        assert f.status_code == 200 and f.content == MSI and f.headers["content-type"] == "application/x-msi"
        assert (await c.get("/download/windows-server-msi/kelo-1.0.0.msi")).status_code == 404   # the app's MSI is not this file
        latest = (await c.get("/download/latest.json")).json()
        assert latest["windows-server-msi"] == {"version": "0.10.2", "size": len(MSI), "sha256": row["sha256"],
                                                "date": "2026-11-08T09:00:00Z", "file": "kelo-server-0.10.2.msi",
                                                "url": "/download/windows-server-msi"}
        assert latest["windows-msi"]["file"] == "kelo-1.0.0.msi"                 # the desktop app row is untouched
        for lang in ("en", "ko"):
            page = (await c.get(f"/?lang={lang}")).text
            assert "windows-server-msi" not in page and "kelo-server-" not in page
            assert "/download/windows-server-msi" not in page
        assert "windows-server-msi" not in (await c.get("/download?lang=en")).text
        assert "/download/windows-server-msi" not in (await c.get("/download?lang=ko")).text


def test_the_server_msi_keeps_its_own_platform_and_file_name(tmp_path):
    msi = tmp_path / "KeloServer-0.10.2-x64.msi"
    msi.write_bytes(MSI)
    assert downloads.platform_for("windows-server-msi", msi) == "windows-server-msi"
    assert downloads.platform_for("windows", msi) == "windows-msi"            # the old shortcut for the app's MSI stays as it was
    assert downloads.platform_for("windows-msi", msi) == "windows-msi"
    exe = tmp_path / "KeloServer-0.10.2-x64.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(PublishError, match="must be a .msi file"):
        downloads.platform_for("windows-server-msi", exe)
    volume = tmp_path / "vol"
    docker = FakeDocker(volume)
    pub = Publisher(tmp_path, run=docker, out=lambda s: None)
    got = pub.publish("windows-server-msi", msi, "0.10.2", unsigned=True)
    assert got["file"] == "kelo-server-0.10.2.msi" and (volume / "kelo-server-0.10.2.msi").read_bytes() == MSI
    pub.publish("windows", msi, "0.10.2", unsigned=True)                     # the app's MSI with the same version: a different file
    rows = json.loads((volume / "manifest.json").read_text())["platforms"]
    assert rows["windows-server-msi"]["file"] == "kelo-server-0.10.2.msi"
    assert rows["windows-msi"]["file"] == "kelo-0.10.2.msi"


def test_publish_app_does_not_record_the_server_msi_as_an_app_release(tmp_path, monkeypatch):
    msi = tmp_path / "KeloServer-0.10.2-x64.msi"
    msi.write_bytes(MSI)
    recorded: list[str] = []
    monkeypatch.setattr(downloads, "record_published", lambda root, row, out: recorded.append(row["file"]))
    root = _operator_root(tmp_path, "ASSISTANT_PUBLIC_URL=https://kelo.chat\nASSISTANT_KELO_BROKER=https://kelo.chat\n")
    lines: list[str] = []
    args = SimpleNamespace(platform="windows-server-msi", file=str(msi), version="0.10.2", unsigned=True, commit=None)
    assert run_publish(args, root, run=FakeDocker(tmp_path / "vol"), out=lines.append) == 0
    assert recorded == []
    assert any(line == "Published Kelo server for Windows (MSI) 0.10.2: https://kelo.chat/download/windows-server-msi" for line in lines)
    args.platform = "windows"
    assert run_publish(args, root, run=FakeDocker(tmp_path / "vol2"), out=lines.append) == 0
    assert recorded == ["kelo-0.10.2.msi"]                                     # the app's MSI is still recorded as before


def test_the_cli_accepts_the_server_msi_platform(monkeypatch):
    seen = {}
    monkeypatch.setattr(downloads, "run_publish", lambda a: seen.setdefault("args", a) and 0)
    assert cli.main(["publish-app", "windows-server-msi", "/x/KeloServer.msi", "--version", "0.10.2"]) == 0
    assert seen["args"].platform == "windows-server-msi"


# ------------------------------------------------------------------------------------------------------- Kelo server for Mac
BUNDLE = b"PK\x03\x04" + b"\x00" * 200


async def test_the_mac_server_bundle_is_served_and_in_latest_json_but_not_on_the_download_page(tmp_path):
    row = publish_here(tmp_path, "mac-server-bundle", "kelo-server-0.10.2.zip", BUNDLE, "0.10.2")
    publish_here(tmp_path, "mac", "Kelo-1.0.0.dmg", b"\x00" * 300, "1.0.0")
    async with Running(app_with(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.get("/download/mac-server-bundle")
        assert r.status_code == 302 and r.headers["location"] == "/download/mac-server-bundle/kelo-server-0.10.2.zip"
        f = await c.get("/download/mac-server-bundle/kelo-server-0.10.2.zip")
        assert f.status_code == 200 and f.content == BUNDLE and f.headers["content-type"] == "application/zip"
        latest = (await c.get("/download/latest.json")).json()
        assert latest["mac-server-bundle"] == {"version": "0.10.2", "size": len(BUNDLE), "sha256": row["sha256"],
                                               "date": "2026-11-08T09:00:00Z", "file": "kelo-server-0.10.2.zip",
                                               "url": "/download/mac-server-bundle"}
        assert latest["mac"]["file"] == "Kelo-1.0.0.dmg"                         # the desktop app's Mac row is untouched
        for lang in ("en", "ko"):
            page = (await c.get(f"/?lang={lang}")).text
            assert "mac-server-bundle" not in page and "kelo-server-" not in page
            assert "/download/mac-server-bundle" not in page
        assert "mac-server-bundle" not in (await c.get("/download?lang=en")).text
        assert "/download/mac-server-bundle" not in (await c.get("/download?lang=ko")).text


def test_the_mac_server_bundle_is_a_zip_and_keeps_its_own_platform(tmp_path):
    zip_file = tmp_path / "kelo-server-0.10.2-mac-arm64.zip"
    zip_file.write_bytes(BUNDLE)
    assert downloads.platform_for("mac-server-bundle", zip_file) == "mac-server-bundle"
    dmg = tmp_path / "Kelo-0.10.2.dmg"
    dmg.write_bytes(b"\x00")
    with pytest.raises(PublishError, match="must be a .zip file"):
        downloads.platform_for("mac-server-bundle", dmg)
    with pytest.raises(PublishError, match="must be a .dmg file"):
        downloads.platform_for("mac", zip_file)
    volume = tmp_path / "vol"
    pub = Publisher(tmp_path, run=FakeDocker(volume), out=lambda s: None)
    got = pub.publish("mac-server-bundle", zip_file, "0.10.2", unsigned=True)
    assert got["file"] == "kelo-server-0.10.2.zip" and (volume / "kelo-server-0.10.2.zip").read_bytes() == BUNDLE
    rows = json.loads((volume / "manifest.json").read_text())["platforms"]
    assert rows["mac-server-bundle"]["file"] == "kelo-server-0.10.2.zip"


def test_publish_app_does_not_record_the_mac_server_bundle_as_an_app_release(tmp_path, monkeypatch):
    zip_file = tmp_path / "kelo-server-0.10.2-mac-arm64.zip"
    zip_file.write_bytes(BUNDLE)
    recorded: list[str] = []
    monkeypatch.setattr(downloads, "record_published", lambda root, row, out: recorded.append(row["file"]))
    root = _operator_root(tmp_path, "ASSISTANT_PUBLIC_URL=https://kelo.chat\nASSISTANT_KELO_BROKER=https://kelo.chat\n")
    lines: list[str] = []
    args = SimpleNamespace(platform="mac-server-bundle", file=str(zip_file), version="0.10.2", unsigned=True, commit=None)
    assert run_publish(args, root, run=FakeDocker(tmp_path / "vol"), out=lines.append) == 0
    assert recorded == []
    assert any(line == "Published Kelo server for Mac (bundle) 0.10.2: https://kelo.chat/download/mac-server-bundle" for line in lines)


def test_the_cli_accepts_the_mac_server_bundle_platform(monkeypatch):
    seen = {}
    monkeypatch.setattr(downloads, "run_publish", lambda a: seen.setdefault("args", a) and 0)
    assert cli.main(["publish-app", "mac-server-bundle", "/x/kelo-server.zip", "--version", "0.10.2"]) == 0
    assert seen["args"].platform == "mac-server-bundle"


def test_latest_server_row_has_string_notes_and_notes_i18n(tmp_path):
    from assistant import release as rel

    server = rel.build_manifest("0.5.0", {c: "sha256:" + f"{i:x}" * 64 for i, c in enumerate(rel.COMPONENTS, start=1)},
                                date="2026-11-09T00:00:00Z", notes={"en": "Faster replies.", "ko": "답장이 빨라졌어요."})
    (tmp_path / "manifest.json").write_text(json.dumps({"platforms": {}, "server": server}))
    latest = Downloads(tmp_path).latest()
    assert latest["server"]["notes"] == "Faster replies."
    assert latest["server"]["notes_i18n"] == {"en": "Faster replies.", "ko": "답장이 빨라졌어요."}
    got = rel.from_latest(latest)
    assert got.note("ko") == "답장이 빨라졌어요." and got.note("de") == "Faster replies."


# ---------------------------------------------------------------------------------------------------------------- store rows (latest.json "store")
STORE_SIG = "A" * 86 + "=="
STORE_KEY = "0123456789abcdef"


def store_row(**kw) -> dict:
    return {"channel": "play", "version": "0.10.3", "build": 402600, "date": "2026-11-18", "sig": STORE_SIG, "key": STORE_KEY, **kw}


def test_latest_lists_store_rows_and_omits_an_empty_store(tmp_path):
    row = store_row()
    (tmp_path / "manifest.json").write_text(json.dumps({"platforms": {}, "store": {"android": {"play": row}}}))
    assert Downloads(tmp_path).latest()["store"] == {"android": {"play": row}}
    (tmp_path / "manifest.json").write_text(json.dumps({"platforms": {}, "store": {}}))
    assert "store" not in Downloads(tmp_path).latest()


def test_a_malformed_store_row_is_left_out(tmp_path):
    good = store_row(channel="appstore", version="0.10.4", build=26111411)
    bad = {
        "android": {"play": store_row(version="1.2")},                       # not MAJOR.MINOR.PATCH
        "desktop": {"msstore": store_row(channel="play")},                    # the channel is not the platform's
        "mac": {"download": store_row(channel="download")},                   # not an app with a store row
        "windows": 5,
    }
    (tmp_path / "manifest.json").write_text(json.dumps({"platforms": {}, "store": {**bad, "ios": {"appstore": good}}}))
    assert Downloads(tmp_path).latest()["store"] == {"ios": {"appstore": good}}
    short_sig = {"android": {"play": store_row(sig="abc")}}                    # a signature of the wrong shape
    (tmp_path / "manifest.json").write_text(json.dumps({"platforms": {}, "store": short_sig}))
    assert "store" not in Downloads(tmp_path).latest()


def test_publish_app_keeps_store_rows(tmp_path, monkeypatch):
    volume = tmp_path / "vol"
    volume.mkdir()
    (volume / "manifest.json").write_text(json.dumps({"platforms": {}, "store": {"ios": {"appstore": store_row(channel="appstore",
                                                                                             version="0.10.4", build=26111411)}}}))
    apk = tmp_path / "kelo-0.1.0.apk"
    apk.write_bytes(APK)
    monkeypatch.setattr(downloads, "find_aapt2", lambda: "/sdk/aapt2")
    Publisher(tmp_path, run=FakeDocker(volume), out=lambda s: None).publish("android", apk, unsigned=True)
    assert json.loads((volume / "manifest.json").read_text())["store"]["ios"]["appstore"]["version"] == "0.10.4"


def test_publish_server_keeps_store_rows(tmp_path):
    from assistant import release as rel
    from assistant.app_releases import AppReleases
    from assistant.downloads import ServerPublisher

    volume = tmp_path / "vol"
    volume.mkdir()
    (volume / "manifest.json").write_text(json.dumps({"platforms": {}, "store": {"desktop": {"msstore": store_row(
        channel="msstore", version="1.0.2", build=100000002)}}}))
    release = tmp_path / "release.json"
    release.write_text(json.dumps(rel.build_manifest("0.10.5", {c: "sha256:" + f"{i:x}" * 64 for i, c in enumerate(rel.COMPONENTS, 1)},
                                                     date="2026-11-18T00:00:00Z")))
    ServerPublisher(tmp_path, run=FakeDocker(volume), out=lambda s: None).publish_server(release, releases=AppReleases())
    raw = json.loads((volume / "manifest.json").read_text())
    assert raw["store"]["desktop"]["msstore"]["version"] == "1.0.2" and raw["server"]["version"] == "0.10.5"
