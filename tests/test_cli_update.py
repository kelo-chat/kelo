"""The CLI sidecar updating its own CLI into its home volume (providers/cli_update.py, cli_runner /update, the server's
/api/providers/{name}/cli/update). Nothing is installed for real: npm / the installer, `--version` and the sign-in check are fakes
that write or read small scripts in a temporary directory."""

import asyncio
import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from assistant.cli_runner import create_app
from assistant.providers import Done, ProviderError, TextDelta, cli_manage, cli_remote, cli_update, releases

TOKEN = "r" * 32
AUTH = {"authorization": f"Bearer {TOKEN}"}


def fake_bin(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class World:
    """A fake image copy, a fake registry and fake checks. `versions` maps a binary path to what `--version` answers."""

    def __init__(self, tmp: Path, *, image="2.1.300", latest="2.1.319", kind="claude"):
        self.tmp, self.kind = tmp, kind
        self.image = tmp / "opt" / "bin" / kind
        fake_bin(self.image, image)
        self.latest = latest
        self.broken: set[str] = set()      # versions whose binary does not start
        self.logged: dict[str, bool | None] = {}
        self.installs: list[tuple[str | None, Path]] = []
        self.gate: asyncio.Event | None = None
        self.install_error: Exception | None = None

    async def install(self, kind, version, target: Path, work: Path) -> str:
        assert work.is_dir() and not target.exists()
        self.installs.append((version, target))
        if self.gate:
            await self.gate.wait()
        if self.install_error:
            raise self.install_error
        fake_bin(target / "bin" / kind, version or self.latest)
        return "added 1 package"

    async def version_of(self, kind, path):
        if not Path(path).is_file():
            return None
        ver = releases.version_of(Path(path).read_text())
        return None if ver in self.broken else ver

    async def logged_in(self, kind, path):
        return self.logged.get(await self.version_of(kind, path) or "", True)

    async def latest_fn(self, kind):
        return self.latest

    def updater(self, **kw):
        return cli_update.CliUpdater(self.kind, root=self.tmp / "home" / ".kelo-cli" / self.kind, image_path=str(self.image),
                                     install=self.install, version_of=self.version_of, logged_in=self.logged_in,
                                     latest=self.latest_fn, **kw)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in ("CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND", "AGY_COMMAND", "ANTIGRAVITY_COMMAND"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(cli_update.shutil, "disk_usage", lambda p: type("U", (), {"free": 10 * 2**30})())


async def test_update_installs_into_the_volume_switches_and_the_env_points_at_it(tmp_path):
    w = World(tmp_path)
    u = w.updater()
    assert (await u.select())["source"] == "image" and os.environ["CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND"] == str(w.image)
    started = await u.start()
    assert started["state"] == "running" and started["from"] == "2.1.300"
    res = await u.wait()
    assert res["state"] == "done" and res["ok"] is True and res["from"] == "2.1.300" and res["to"] == "2.1.319"
    assert res["active"] == {"source": "volume", "version": "2.1.319"}
    new_bin = u.root / "2.1.319" / "bin" / "claude"
    assert os.environ["CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND"] == str(new_bin)
    assert json.loads((u.root / "state.json").read_text())["current"] == "2.1.319"
    assert w.installs == [("2.1.319", u.root / "2.1.319")]
    # already the latest: nothing installed again
    await u.start()
    res = await u.wait()
    assert res["ok"] and res.get("unchanged") is True and len(w.installs) == 1


async def test_a_new_version_that_does_not_start_is_never_switched_to(tmp_path):
    w = World(tmp_path)
    w.broken.add("2.1.319")
    u = w.updater()
    await u.select()
    await u.start()
    res = await u.wait()
    assert res["state"] == "failed" and res["ok"] is False and "does not start" in res["error"]
    assert res["error_key"] == "The new version does not start"
    assert res["active"] == {"source": "image", "version": "2.1.300"}
    assert os.environ["CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND"] == str(w.image)
    assert not (u.root / "2.1.319").exists() and not (u.root / "state.json").exists()


async def test_a_new_version_that_loses_the_sign_in_is_not_switched_to(tmp_path):
    w = World(tmp_path)
    w.logged["2.1.319"] = False
    u = w.updater()
    await u.select()
    await u.start()
    res = await u.wait()
    assert res["ok"] is False and "sign-in" in res["error"] and res["active"]["source"] == "image"


async def test_install_failure_keeps_the_old_one_and_reports_the_installer_output(tmp_path):
    w = World(tmp_path)
    w.install_error = cli_update.UpdateFailed("The install failed (exit {code})", "npm ERR! network", code=1)
    u = w.updater()
    await u.select()
    await u.start()
    res = await u.wait()
    assert res["ok"] is False and res["error"] == "The install failed (exit 1)" and res["error_fields"] == {"code": 1}
    assert "npm ERR" in res["error_detail"] and res["active"]["version"] == "2.1.300"


async def test_keeps_two_versions_and_falls_back_to_the_previous_then_the_image(tmp_path):
    w = World(tmp_path)
    u = w.updater()
    await u.select()
    for v in ("2.1.310", "2.1.319", "2.1.330"):
        w.latest = v
        await u.start()
        assert (await u.wait())["ok"]
    assert sorted(p.name for p in u.root.iterdir() if p.is_dir()) == ["2.1.319", "2.1.330"]
    assert json.loads((u.root / "state.json").read_text())["previous"] == "2.1.319"
    w.broken.add("2.1.330")                       # the current copy broke (a damaged volume, say): the previous one is used
    assert (await u.select()) == {"source": "volume", "version": "2.1.319"}
    w.broken.add("2.1.319")                       # both broken: the image's copy
    assert (await u.select()) == {"source": "image", "version": "2.1.300"}
    assert os.environ["CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND"] == str(w.image)


async def test_a_newer_image_wins_over_the_volume_copy(tmp_path):
    w = World(tmp_path)
    u = w.updater()
    await u.select()
    await u.start()
    assert (await u.wait())["active"]["source"] == "volume"
    fake_bin(w.image, "2.2.0")                    # `assistant update` built a newer image
    assert (await u.select()) == {"source": "image", "version": "2.2.0"}
    w.latest = "2.2.0"
    await u.start()
    res = await u.wait()
    assert res["ok"] and res.get("unchanged") and len(w.installs) == 1   # nothing to do


async def test_refusals_bad_version_running_update_and_busy_cli(tmp_path):
    w = World(tmp_path)
    u = w.updater()
    await u.select()
    with pytest.raises(cli_update.UpdateRefused) as e:
        await u.start("2.1; rm -rf /")
    assert e.value.status == 400
    w.gate = asyncio.Event()
    await u.start()
    await asyncio.sleep(0)
    with pytest.raises(cli_update.UpdateRefused, match="already running"):
        await u.start()
    w.gate.set()
    assert (await u.wait())["ok"]
    u.busy = lambda: True
    with pytest.raises(cli_update.UpdateRefused, match="answering a chat"):
        await u.start()
    agy = World(tmp_path / "agy", kind="agy").updater()
    with pytest.raises(cli_update.UpdateRefused, match="only the latest"):
        await agy.start("1.2.3")


async def test_pinned_version_is_passed_to_the_installer(tmp_path):
    w = World(tmp_path)
    u = w.updater()
    await u.select()
    await u.start("2.1.315")
    res = await u.wait()
    assert res["ok"] and res["to"] == "2.1.315" and w.installs[0][0] == "2.1.315"


# ---- the runner endpoints

def runner(w: World, stream_fn=None):
    u = w.updater()
    app = create_app("claude", TOKEN, stream_fn=stream_fn or (lambda p, r: None), updater=u)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner"), u


async def test_runner_update_needs_the_token_and_reports_progress(tmp_path, monkeypatch):
    w = World(tmp_path)
    c, u = runner(w)
    async with c:
        await u.select()
        assert (await c.post("/update", json={})).status_code == 401
        assert (await c.get("/update/status")).status_code == 401
        assert (await c.get("/update/status", headers=AUTH)).json()["state"] == "idle"
        r = await c.post("/update", json={}, headers=AUTH)
        assert r.status_code == 200 and r.json()["state"] == "running"
        await u.wait()
        st = (await c.get("/update/status", headers=AUTH)).json()
        assert st["state"] == "done" and st["to"] == "2.1.319"
        assert (await c.post("/update", json={"version": "x.y"}, headers=AUTH)).status_code == 400

        async def fake_status(provider, local=False):
            return {"installed": True, "logged_in": True, "version": "2.1.319 (Claude Code)", "hint": ""}

        monkeypatch.setattr(cli_manage, "cli_status", fake_status)
        st = (await c.get("/status", headers=AUTH)).json()
        assert st["self_update"] is True and st["cli_source"] == "volume"


async def test_runner_refuses_an_update_while_a_chat_runs(tmp_path):
    w = World(tmp_path)
    gate = asyncio.Event()

    async def slow(provider, req):
        yield TextDelta("hi")
        await gate.wait()
        yield Done("stop")

    c, u = runner(w, slow)
    async with c:
        await u.select()
        chat = asyncio.create_task(c.post("/run", headers=AUTH, json={"request": {}}))
        for _ in range(200):                      # until the chat is running in the runner
            await asyncio.sleep(0.01)
            if u.busy():
                break
        busy = await c.post("/update", json={}, headers=AUTH)
        assert busy.status_code == 409 and "answering a chat" in busy.json()["detail"]
        gate.set()
        assert (await chat).status_code == 200
        assert (await c.post("/update", json={}, headers=AUTH)).status_code == 200
        assert (await u.wait())["ok"]


# ---- the server: owner only, proxied to the sidecar

async def test_server_update_endpoint_is_owner_only_and_proxies_to_the_sidecar(tmp_path, monkeypatch):
    from assistant.server import create_app as server_app

    from test_server import AUTH as OWNER, Running, scripted, settings

    w = World(tmp_path)
    c, u = runner(w)
    await u.select()
    monkeypatch.setenv("ASSISTANT_CLI_CLAUDE_URL", "http://runner")
    monkeypatch.setenv("CLI_RUNNER_TOKEN", TOKEN)
    real_call = cli_remote._update_call

    async def via_runner(provider, method, path, *, json_body=None, client=None):
        return await real_call(provider, method, path, json_body=json_body, client=c)

    monkeypatch.setattr(cli_remote, "_update_call", via_runner)

    async def status(name, local=False):
        if name == cli_manage.CLAUDE:
            return {"installed": True, "installable": False, "logged_in": True, "version": "2.1.300 (Claude Code)", "hint": "",
                    "remote": True, "self_update": True}
        return {"installed": False, "installable": False, "logged_in": None, "hint": ""}

    monkeypatch.setattr(cli_manage, "cli_status", status)
    app = server_app(settings(unofficial_providers=True), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=OWNER) as s:
        provs = {p["name"]: p for p in (await s.get("/api/providers")).json()}
        assert provs[cli_manage.CLAUDE]["cli"]["updatable"] is True
        # a paired phone that is not the owner's
        code = (await s.post("/api/devices/pair", json={})).json()["code"]
        dev = (await s.post("/api/pair", json={"code": code, "name": "phone"})).json()
        phone = {"authorization": f"Bearer {dev['token']}"}
        assert (await s.post(f"/api/providers/{cli_manage.CLAUDE}/cli/update", json={}, headers=phone)).status_code == 403
        assert (await s.get(f"/api/providers/{cli_manage.CLAUDE}/cli/update", headers=phone)).status_code == 403
        provs = {p["name"]: p for p in (await s.get("/api/providers", headers=phone)).json()}
        assert provs[cli_manage.CLAUDE]["cli"]["updatable"] is False
        assert (await s.post("/api/providers/openai/cli/update", json={})).status_code == 400
        r = await s.post(f"/api/providers/{cli_manage.CLAUDE}/cli/update", json={})
        assert r.status_code == 200 and r.json()["state"] == "running" and r.json()["product"] == "Claude Code"
        await u.wait()
        st = (await s.get(f"/api/providers/{cli_manage.CLAUDE}/cli/update")).json()
        assert st["state"] == "done" and st["to"] == "2.1.319" and st["ok"] is True
        # a refusal from the sidecar keeps its status and reads in the user's language
        u.busy = lambda: True
        r = await s.post(f"/api/providers/{cli_manage.CLAUDE}/cli/update", json={}, headers={"accept-language": "ko"})
        assert r.status_code == 409 and "대화" in r.json()["detail"]
    await c.aclose()


async def test_server_without_a_sidecar_says_so(monkeypatch):
    monkeypatch.delenv("ASSISTANT_CLI_CLAUDE_URL", raising=False)
    with pytest.raises(ProviderError) as e:
        await cli_remote.update_start(cli_manage.CLAUDE)
    assert e.value.status == 409
