"""The work room: tools, approvals, the install proxy, the ledger, copying results back, the library (trash, share links) and its API.

No Docker here: a fake runner stands in for the container, or the real agent's HTTP handler is served on a Unix socket with its job
functions replaced (the real ones kill every process in the container, which must never happen on a test machine). The optional test
at the end runs the real image when KELO_DOCKER_TESTS=1.
"""

import asyncio
import base64
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

from assistant import workroom as wr
from assistant import workroom_agent as agent
from assistant.activity import Activity, rule_kind
from assistant.agent import ToolOutput, run_agent
from assistant.deliverables import SHARE_HOURS, TRASH_DAYS, Deliverables
from assistant.home import Home
from assistant.hosting.channels import workroom_agent_channel
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running

B64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeRunner:
    def __init__(self, build="b1", run=None, installed=None):
        self.calls, self.build = [], build
        self.run_answer = run or {"exit_code": 0, "stdout": "3.1.1\n", "stderr": "", "timed_out": False, "killed_for": "", "duration_s": 0.4,
                                  "files": [], "skipped": []}
        self.installed = installed if installed is not None else {"pip": [], "npm": []}
        self.proxy_seen = []

    async def call(self, path, body, timeout):
        self.calls.append((path, body))
        if path == "/health":
            return {"ok": True, "build": self.build}
        if path == "/run":
            return self.run_answer
        if path == "/install":
            self.proxy_seen.append(body["proxy_password"])
            for spec in body["packages"]:
                name = wr.normalize(body["kind"], spec)
                self.installed[body["kind"]] = [r for r in self.installed[body["kind"]] if r["name"] != name] + [{"name": name, "version": "1.0"}]
            return {"exit_code": 0, "output": "ok", "installed": self.installed[body["kind"]]}
        if path == "/uninstall":
            names = {wr.normalize(body["kind"], s) for s in body["packages"]}
            self.installed[body["kind"]] = [r for r in self.installed[body["kind"]] if r["name"] not in names]
            return {"exit_code": 0, "output": "", "installed": self.installed[body["kind"]]}
        if path == "/reset":
            self.installed = {"pip": [], "npm": []}
            return {"ok": True}
        return {"error": "?"}


class FakeProxy:
    def __init__(self):
        self.armed_with, self.contacted, self.refused, self.armed = [], [], [], False

    async def start(self):
        pass

    def arm(self, hosts):
        self.armed, self.armed_with = True, [*self.armed_with, hosts]
        return "p" * 32

    def disarm(self):
        self.armed = False


def room(tmp_path, runner=None, proxy=None, clock=None):
    home = Home(tmp_path / "home")
    files = Deliverables(home, clock or Clock())
    return wr.Workroom(home, runner or FakeRunner(), files=files, proxy=proxy or FakeProxy()), home


def by_name(tools):
    return {t.name: t for t in tools}


# ------------------------------------------------------------------------------------------------ tools and validation
async def test_run_sends_code_and_input_files_only_and_returns_what_the_code_printed(tmp_path):
    r = FakeRunner()
    w, home = room(tmp_path, r)
    home.save_upload("sales.xlsx", b"PK\x03\x04data")
    tools = by_name(wr.workroom_tools(w))
    assert tools["workroom_run"].requires_approval is False                    # no network, no secrets: no card, only the chip
    out = await tools["workroom_run"].handler({"language": "python", "code": "print(1)", "files_in": ["uploads/sales.xlsx"]})
    assert "exit code 0" in out and "3.1.1" in out and "No files were produced" in out
    path, body = r.calls[-1]
    assert path == "/run" and set(body) == {"language", "code", "files", "timeout_s"}
    assert body["files"] == [{"name": "sales.xlsx", "b64": B64(b"PK\x03\x04data")}] and body["timeout_s"] == 60
    assert "error: language must be" in await tools["workroom_run"].handler({"language": "ruby", "code": "x"})
    assert "error: code is empty" in await tools["workroom_run"].handler({"language": "bash", "code": " "})
    assert "files_in" in await tools["workroom_run"].handler({"language": "bash", "code": "ls", "files_in": ["../etc/passwd"]})
    assert "files_in" in await tools["workroom_run"].handler({"language": "bash", "code": "ls", "files_in": ["uploads/nope.pdf"]})
    await tools["workroom_run"].handler({"language": "bash", "code": "ls", "timeout_s": 9999})
    assert r.calls[-1][1]["timeout_s"] == wr.MAX_TIMEOUT


async def test_no_secret_ever_reaches_the_work_room(tmp_path, monkeypatch):
    secrets_ = {"ASSISTANT_TOKEN": "tok-" + "x" * 30, "ASSISTANT_SECRET_KEY": "sk-" + "y" * 30, "OPENROUTER_API_KEY": "or-" + "z" * 30,
                "CLI_RUNNER_TOKEN": "cli-" + "w" * 30}
    for k, v in secrets_.items():
        monkeypatch.setenv(k, v)
    r = FakeRunner()
    w, home = room(tmp_path, r)
    home.write("notes/plan.md", "# plan")
    tools = by_name(wr.workroom_tools(w))
    await tools["workroom_run"].handler({"language": "python", "code": "import os; print(os.environ)", "files_in": ["notes/plan.md"]})
    await tools["workroom_install"].handler({"kind": "pip", "packages": ["pandas-ta"], "reason": "지표 계산"})
    sent = json.dumps([b for _, b in r.calls])
    assert not any(v in sent for v in secrets_.values()) and "environ\"" not in sent.replace("os.environ", "")
    assert all(set(b or {}) <= {"language", "code", "files", "timeout_s", "kind", "packages", "proxy_password"} for _, b in r.calls)
    # and the container side builds each job's environment from nothing
    env = agent.job_env(Path("/work/job"))
    assert not set(secrets_) & set(env) and not any(v in json.dumps(env) for v in secrets_.values())
    assert env["OUT_DIR"] == "/work/job/out" and "HTTPS_PROXY" not in env


async def test_install_is_validated_before_any_card_and_the_card_says_what_and_why(tmp_path):
    w, _ = room(tmp_path)
    t = by_name(wr.workroom_tools(w))["workroom_install"]
    assert t.requires_approval and t.validate is not None
    assert "kind must be" in t.validate({"kind": "apt", "packages": ["x"], "reason": "r"})
    assert "plain package name" in t.validate({"kind": "pip", "packages": ["https://evil.example/x.whl"], "reason": "r"})
    assert "plain package name" in t.validate({"kind": "pip", "packages": ["-e ."], "reason": "r"})
    assert "plain package name" in t.validate({"kind": "npm", "packages": ["git+ssh://x"], "reason": "r"})
    assert "1 to 10" in t.validate({"kind": "pip", "packages": [], "reason": "r"})
    assert "reason is required" in t.validate({"kind": "pip", "packages": ["pandas-ta"], "reason": " "})
    assert t.validate({"kind": "pip", "packages": ["pandas-ta==0.3.14b0"], "reason": "r"}) is None
    assert t.validate({"kind": "npm", "packages": ["@types/node@20"], "reason": "r"}) is None
    card = await t.describe({"kind": "pip", "packages": ["pandas-ta"], "reason": "주가 지표를 계산하려고요"})
    assert card == "Install a program in the work room: pandas-ta (PyPI) — why: 주가 지표를 계산하려고요"


def script(*turns):
    it = iter(turns)

    async def fn(provider, request, **kw):
        for ev in next(it):
            yield ev
    return fn


async def test_running_code_asks_nothing_but_installing_asks_and_a_no_installs_nothing(tmp_path):
    r = FakeRunner()
    w, _ = room(tmp_path, r)
    asked = []

    async def deny(req):
        asked.append((req.name, req.detail))
        return False

    events = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "hi"}], wr.workroom_tools(w), approve=deny, stream_fn=script(
        [ToolCall("c1", "workroom_run", '{"language":"python","code":"print(1)"}'),
         ToolCall("c2", "workroom_install", '{"kind":"pip","packages":["pandas-ta"],"reason":"지표"}'), Done("tool_calls")],
        [TextDelta("ok"), Done("stop")]))]
    assert asked == [("workroom_install", "Install a program in the work room: pandas-ta (PyPI) — why: 지표")]
    assert [p for p, _ in r.calls if p != "/health"] == ["/run"]                           # declined: nothing was installed
    res = [e for e in events if type(e).__name__ == "ToolResult"]
    assert res[1].approved is False and "declined the install" in res[1].output


async def test_an_install_arms_the_proxy_for_the_registry_only_and_records_versions_in_the_ledger(tmp_path):
    r, proxy = FakeRunner(), FakeProxy()
    w, home = room(tmp_path, r, proxy)
    t = by_name(wr.workroom_tools(w))
    out = await t["workroom_install"].handler({"kind": "pip", "packages": ["Pandas_TA"], "reason": "지표 계산"})
    assert "Installed: pandas-ta 1.0" in out and proxy.armed is False                        # armed only while it ran
    assert proxy.armed_with == [("pypi.org", "files.pythonhosted.org")] and r.proxy_seen == ["p" * 32]
    ledger = json.loads((home.root / ".workroom" / "ledger.json").read_text())
    assert ledger["build"] == "b1" and [(p["kind"], p["name"], p["version"], p["reason"]) for p in ledger["packages"]] == [("pip", "pandas-ta", "1.0", "지표 계산")]
    await t["workroom_install"].handler({"kind": "npm", "packages": ["left-pad"], "reason": "x"})
    assert proxy.armed_with[-1] == ("registry.npmjs.org",)
    assert "pandas-ta 1.0 (pip)" in await t["workroom_files"].handler({"action": "list"})
    assert "Removed: pandas-ta" in await t["workroom_uninstall"].handler({"kind": "pip", "packages": ["pandas-ta"]})
    assert [p["name"] for p in w.ledger.packages()] == ["left-pad"]
    assert not (home.root / ".workroom").name in [x["path"] for x in home.list()]          # the ledger is hidden from the agent's files


async def test_after_an_update_the_approved_packages_come_back_pinned_before_the_next_job(tmp_path):
    r = FakeRunner(build="b1")
    w, _ = room(tmp_path, r)
    await w.install("pip", ["pandas-ta"], "x")
    await w.install("npm", ["left-pad"], "y")
    r.build = "b2"                                                                           # the image was rebuilt
    w2 = wr.Workroom(w.home, r, files=w.files, proxy=FakeProxy())                            # (a restarted server)
    res = await w2.run("python", "print(1)")
    paths = [p for p, _ in r.calls]
    assert paths[-5:] == ["/health", "/reset", "/install", "/install", "/run"]
    installs = [b["packages"] for p, b in r.calls[-5:] if p == "/install"]
    assert installs == [["pandas-ta==1.0"], ["left-pad@1.0"]] and "installed again" in res["note"]
    assert w2.ledger.load()["build"] == "b2"
    await w2.run("python", "print(2)")
    assert [p for p, _ in r.calls][-2:] == ["/health", "/run"] or [p for p, _ in r.calls][-1:] == ["/run"]


def test_remembering_an_install_covers_that_one_package_only():
    assert wr.install_site({"kind": "pip", "packages": ["Pandas_TA==0.3"]}) == "pandas-ta.pypi.install"
    assert wr.install_site({"kind": "npm", "packages": ["left-pad"]}) == "left-pad.npm.install"
    assert wr.install_site({"kind": "pip", "packages": ["a", "b"]}) == ""                   # several at once: ask every time
    assert wr.install_site({"kind": "npm", "packages": ["@types/node"]}) == ""
    assert rule_kind("workroom_install") == "install" and rule_kind("share_file") == "" and rule_kind("workroom_run") == ""
    act = Activity(Store())
    act.add_rule("workroom_install", "pandas-ta.pypi.install")
    assert act.matching_rule("workroom_install", "pandas-ta.pypi.install")
    assert act.matching_rule("workroom_install", "ta.pypi.install") is None
    assert act.matching_rule("workroom_install", "pandas-ta.npm.install") is None
    assert act.matching_rule("workroom_install", "evil-pandas-ta.pypi.install") is None


async def test_results_are_copied_into_the_library_within_the_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "MAX_OUT_FILES", 3)
    monkeypatch.setattr(wr, "MAX_OUT_FILE", 100)
    monkeypatch.setattr(wr, "MAX_OUT_TOTAL", 250)
    files = [{"name": "a.png", "bytes": 90, "b64": B64(b"a" * 90)}, {"name": "big.mp4", "bytes": 101, "b64": B64(b"b" * 101)},
             {"name": "b.xlsx", "bytes": 90, "b64": B64(b"c" * 90)}, {"name": "c.csv", "bytes": 90, "b64": B64(b"d" * 90)},
             {"name": "d.txt", "bytes": 10, "b64": B64(b"e" * 10)}, {"name": "e.txt", "bytes": 1, "b64": B64(b"f")},
             {"name": "../../x.sh", "bytes": 3, "b64": "!!!"}]
    run = {"exit_code": 0, "stdout": "", "stderr": "", "duration_s": 1, "files": files, "skipped": [{"name": "f.bin", "bytes": 9, "reason": "more than 20 files"}]}
    w, home = room(tmp_path, FakeRunner(run=run))
    res = await w.run("python", "x")
    assert [f["name"] for f in res["saved"]] == ["a.png", "b.xlsx", "d.txt"]
    reasons = {f["name"]: f["reason"] for f in res["not_saved"]}
    assert reasons["big.mp4"].startswith("larger than") and reasons["c.csv"].startswith("all outputs together")
    assert reasons["e.txt"].startswith("more than 3") and reasons["f.bin"] == "more than 20 files" and reasons["../../x.sh"] == "unreadable"
    assert sorted(p.name for p in (home.root / "files").iterdir()) == ["a.png", "b.xlsx", "d.txt"]
    res = await w.run("python", "x")                                                         # never overwritten
    assert res["saved"][0]["name"] == "a-1.png"
    text = wr.run_text(res)
    assert "Saved in the user's library (files/): a-1.png" in text and "Not saved: f.bin" in text and "big.mp4 (101B): larger than" in text


def picture_png(size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


async def test_pictures_from_the_view_folder_go_to_the_model_only_and_never_to_the_library(tmp_path):
    png = picture_png()
    run = {"exit_code": 0, "stdout": "done\n", "stderr": "", "timed_out": False, "killed_for": "", "duration_s": 1, "files": [],
           "skipped": [], "views": [{"name": "page-1.png", "bytes": len(png), "b64": B64(png)},
                                   {"name": "notes.png", "bytes": 5, "b64": B64(b"hello")},
                                   {"name": "bad.png", "bytes": 3, "b64": "!!!"}],
           "skipped_views": [{"name": "big.jpg", "bytes": 9, "reason": "more than 8 pictures"}]}
    w, home = room(tmp_path, FakeRunner(run=run))
    out = await by_name(wr.workroom_tools(w))["workroom_run"].handler({"language": "python", "code": "x"})
    assert isinstance(out, ToolOutput) and len(out.images) == 1
    assert out.images[0].startswith("data:image/jpeg;base64,") and out.no_vision == wr.NO_VISION_ROOM
    assert "Pictures from $VIEW_DIR follow (not saved to the user's files): page-1.png" in out.text
    assert ("Not shown from $VIEW_DIR: big.jpg (9B): more than 8 pictures; bad.png (0B): unreadable; notes.png (5B): not a picture"
            in out.text)
    assert "stdout:" in out.text and "done" in out.text
    assert not (home.root / "files").exists() or list((home.root / "files").iterdir()) == []


async def test_a_view_that_is_not_a_picture_is_named_and_no_picture_is_sent(tmp_path):
    run = {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "killed_for": "", "duration_s": 1, "files": [], "skipped": [],
           "views": [{"name": "fake.png", "bytes": 5, "b64": B64(b"hello")}], "skipped_views": []}
    w, _ = room(tmp_path, FakeRunner(run=run))
    out = await by_name(wr.workroom_tools(w))["workroom_run"].handler({"language": "python", "code": "x"})
    assert isinstance(out, str) and not isinstance(out, ToolOutput)
    assert "Not shown from $VIEW_DIR: fake.png (5B): not a picture" in out


async def test_without_pictures_the_run_answer_is_the_same_plain_text_as_before(tmp_path):
    w, _ = room(tmp_path, FakeRunner())
    out = await by_name(wr.workroom_tools(w))["workroom_run"].handler({"language": "python", "code": "print(3)"})
    assert isinstance(out, str) and not isinstance(out, ToolOutput)
    assert out == wr.run_text(await w.run("python", "print(3)"))


async def test_a_full_storage_cap_refuses_the_copy_with_a_reason(tmp_path):
    from assistant.quota import Quota
    run = {"exit_code": 0, "stdout": "", "stderr": "", "duration_s": 1, "files": [{"name": "a.bin", "bytes": 5000, "b64": B64(b"z" * 5000)}]}
    w, home = room(tmp_path, FakeRunner(run=run))
    home.quota = Quota(tmp_path, 1000)
    res = await w.run("bash", "x")
    assert res["saved"] == [] and "Storage is full" in res["not_saved"][0]["reason"]


async def test_a_stopped_work_room_is_reported_plainly(tmp_path):
    w, _ = room(tmp_path, wr.SocketRunner(tmp_path / "nope.sock"))
    out = await by_name(wr.workroom_tools(w))["workroom_run"].handler({"language": "python", "code": "print(1)"})
    assert out.startswith("error: the work room is not running")


# ------------------------------------------------------------------------------------------------ the install proxy
def short_dir():
    d = tempfile.mkdtemp(prefix="wr", dir="/tmp")   # Unix socket paths must be short (macOS: 104 bytes)
    return Path(d)


async def proxy_request(path, line, password=None):
    reader, writer = await asyncio.open_unix_connection(str(path))
    auth = f"Proxy-Authorization: Basic {B64(f'kelo:{password}'.encode())}\r\n" if password else ""
    writer.write(f"{line}\r\nHost: x\r\n{auth}\r\n".encode())
    await writer.drain()
    status = await asyncio.wait_for(reader.readline(), 5)
    return status.decode(), reader, writer


async def test_the_proxy_lets_only_an_armed_install_reach_only_the_registries_on_public_addresses():
    d = short_dir()
    echo_seen = []

    async def echo(r, w):
        data = await r.read(4)
        echo_seen.append(data)
        w.write(data)
        await w.drain()
        w.close()

    echo_srv = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = echo_srv.sockets[0].getsockname()[1]
    dialed = []

    async def connect(ip, p):
        dialed.append((ip, p))
        return await asyncio.open_connection("127.0.0.1", port)

    def resolver(host, *a):
        ip = {"pypi.org": "151.101.0.223", "files.pythonhosted.org": "10.0.0.7"}[host]
        return [(2, 1, 6, "", (ip, 443))]

    proxy = wr.InstallProxy(d / "proxy.sock", resolver=resolver, connect=connect)
    try:
        await proxy.start()
        status, _, w = await proxy_request(d / "proxy.sock", "CONNECT pypi.org:443 HTTP/1.1", "whatever-password-123")
        assert " 403 " in status and "no install is running" in proxy.refused[-1]
        w.close()
        pw = proxy.arm(wr.REGISTRY_HOSTS["pip"])
        for line, password, why in [("CONNECT pypi.org:443 HTTP/1.1", "wrong-password-0000000", "wrong install pass"),
                                    ("CONNECT pypi.org:443 HTTP/1.1", None, "wrong install pass"),
                                    ("GET http://pypi.org/ HTTP/1.1", pw, "only HTTPS"),
                                    ("CONNECT pypi.org:22 HTTP/1.1", pw, "only port 443"),
                                    ("CONNECT example.com:443 HTTP/1.1", pw, "not a package registry"),
                                    ("CONNECT 192.168.0.1:443 HTTP/1.1", pw, "not a package registry")]:
            status, _, w = await proxy_request(d / "proxy.sock", line, password)
            assert " 403 " in status and why in proxy.refused[-1], (line, proxy.refused[-1])
            w.close()
        status, _, w = await proxy_request(d / "proxy.sock", "CONNECT files.pythonhosted.org:443 HTTP/1.1", pw)
        assert " 502 " in status and "NetGuardError" in proxy.refused[-1]                   # a registry name pointing inside: refused
        w.close()
        status, r, w = await proxy_request(d / "proxy.sock", "CONNECT pypi.org:443 HTTP/1.1", pw)
        assert " 200 " in status
        await r.readline()
        w.write(b"ping")
        await w.drain()
        assert await asyncio.wait_for(r.read(4), 5) == b"ping" and dialed == [("151.101.0.223", 443)]   # the checked address is dialed
        w.close()
        assert proxy.contacted == ["pypi.org"]
        proxy.disarm()
        status, _, w = await proxy_request(d / "proxy.sock", "CONNECT pypi.org:443 HTTP/1.1", pw)
        assert " 403 " in status                                                            # the password dies with the install
        w.close()
    finally:
        await proxy.close()
        echo_srv.close()
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ the agent program (safe parts)
def test_agent_checks_names_collects_outputs_within_caps_and_clips_output(tmp_path, monkeypatch):
    assert agent.check_specs("pip", ["numpy==2.0", "pandas"]) == ["numpy==2.0", "pandas"]
    assert "not a package name" in agent.check_specs("pip", ["--index-url=http://x"])
    assert "not a package name" in agent.check_specs("npm", ["../x"])
    assert agent.safe_name("../../etc/passwd") == "passwd" and agent.safe_name("a b/c?.png") == "c_.png"
    out = tmp_path / "out"
    (out / "sub").mkdir(parents=True)
    (out / "a.txt").write_text("hi")
    (out / "sub" / "b.csv").write_text("1,2")
    (out / "empty.txt").write_text("")
    (out / "link").symlink_to("/etc/passwd")
    monkeypatch.setattr(agent, "MAX_OUT_FILES", 1)
    files, skipped = agent.collect_outputs(out)
    assert [f["name"] for f in files] == ["a.txt"] and base64.b64decode(files[0]["b64"]) == b"hi"
    assert {s["name"]: s["reason"] for s in skipped} == {"empty.txt": "empty", "sub_b.csv": "more than 1 files"}
    long = "x" * 100_000
    assert len(agent.clip(long)) < 70_000 and "characters cut" in agent.clip(long)


def test_agent_collects_pictures_for_the_model_within_caps_and_ignores_links_folders_and_other_files(tmp_path, monkeypatch):
    view = tmp_path / "view"
    (view / "sub").mkdir(parents=True)
    (view / "a.png").write_bytes(b"a" * 10)
    (view / "b.JPG").write_bytes(b"b" * 20)
    (view / "c.txt").write_text("not a picture by name")
    (view / "e.webp").write_bytes(b"e" * 30)
    (view / "sub" / "d.png").write_bytes(b"d")
    (view / "link.png").symlink_to(view / "a.png")
    views, skipped = agent.collect_views(view)
    assert [v["name"] for v in views] == ["a.png", "b.JPG", "e.webp"]
    assert views[1]["bytes"] == 20 and base64.b64decode(views[1]["b64"]) == b"b" * 20 and skipped == []
    assert agent.collect_views(tmp_path / "missing") == ([], [])
    monkeypatch.setattr(agent, "MAX_VIEWS", 2)
    views, skipped = agent.collect_views(view)
    assert [v["name"] for v in views] == ["a.png", "b.JPG"]
    assert skipped == [{"name": "e.webp", "bytes": 30, "reason": "more than 2 pictures"}]
    monkeypatch.setattr(agent, "MAX_VIEWS", 8)
    monkeypatch.setattr(agent, "MAX_VIEW_BYTES", 25)
    views, skipped = agent.collect_views(view)
    assert [v["name"] for v in views] == ["a.png", "b.JPG"] and skipped[0]["name"] == "e.webp"
    assert skipped[0]["reason"].startswith("larger than")
    monkeypatch.setattr(agent, "MAX_VIEW_BYTES", 100)
    monkeypatch.setattr(agent, "MAX_VIEW_TOTAL", 35)
    views, skipped = agent.collect_views(view)
    assert [v["name"] for v in views] == ["a.png", "b.JPG"] and skipped[0]["reason"].startswith("all pictures together")


def test_agent_gives_each_job_a_view_folder_and_returns_its_pictures_next_to_the_files(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "WORK", tmp_path / "work")
    monkeypatch.setattr(agent, "sweep", lambda: 0)            # a real sweep kills every process: never on a test machine
    assert agent.job_env(tmp_path / "job")["VIEW_DIR"] == str(tmp_path / "job" / "view")
    code = ("import os\n"
            "open(os.path.join(os.environ['VIEW_DIR'], 'page-1.png'), 'wb').write(b'pic')\n"
            "os.mkdir(os.path.join(os.environ['VIEW_DIR'], 'sub'))\n"
            "open(os.path.join(os.environ['OUT_DIR'], 'keep.txt'), 'w').write('x')\n")
    r = agent.do_run({"language": "python", "code": code, "timeout_s": 30})
    assert r["exit_code"] == 0, r
    assert [v["name"] for v in r["views"]] == ["page-1.png"] and base64.b64decode(r["views"][0]["b64"]) == b"pic"
    assert r["skipped_views"] == [] and [f["name"] for f in r["files"]] == ["keep.txt"]


def test_agent_runs_a_process_with_a_time_limit_and_reports_installed_versions(tmp_path, monkeypatch):
    r = agent.run_process(["python3", "-c", "print('hello')"], tmp_path, {"PATH": os.environ["PATH"]}, 20)
    assert r["exit_code"] == 0 and r["stdout"].strip() == "hello" and not r["timed_out"]
    r = agent.run_process(["python3", "-c", "import time; time.sleep(30)"], tmp_path, {"PATH": os.environ["PATH"]}, 1)
    assert r["timed_out"] and r["killed_for"] == "timeout" and r["duration_s"] < 10
    monkeypatch.setattr(agent, "TOOLS", tmp_path / "tools")
    site = tmp_path / "tools" / "py" / agent.PY_SITE / "pandas_ta-0.3.14b0.dist-info"
    site.mkdir(parents=True)
    (site / "METADATA").write_text("Metadata-Version: 2.1\nName: pandas_ta\nVersion: 0.3.14b0\n\nlong text")
    mod = tmp_path / "tools" / "npm" / "node_modules" / "@scope" / "pkg"
    mod.mkdir(parents=True)
    (mod / "package.json").write_text('{"name": "@scope/pkg", "version": "2.0.0"}')
    assert agent.installed("pip") == [{"name": "pandas_ta", "version": "0.3.14b0"}]
    assert agent.installed("npm") == [{"name": "@scope/pkg", "version": "2.0.0"}]


class AgentOnSocket:
    """The real agent HTTP handler on a Unix socket, with fake job functions."""

    def __init__(self, routes):
        self.dir = short_dir()
        self.saved = dict(agent.ROUTES)
        agent.ROUTES.clear()
        agent.ROUTES.update(routes)
        self.srv = workroom_agent_channel(self.dir).make_server(agent.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        agent.ROUTES.clear()
        agent.ROUTES.update(self.saved)
        shutil.rmtree(self.dir, ignore_errors=True)


def raw_connect(sock, host, password):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(str(sock))
    auth = base64.b64encode(f"kelo:{password}".encode()).decode()
    s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode())
    line = s.recv(200).decode()
    s.close()
    return line


async def test_through_the_app_install_asks_once_can_be_remembered_per_package_and_results_reach_the_library(tmp_path):
    seen = {}

    def fake_install(body):
        d = Path(seen["dir"])
        seen.setdefault("passwords", []).append(body["proxy_password"])
        seen.setdefault("other_host", []).append(raw_connect(d / "proxy.sock", "example.com", body["proxy_password"]))
        return {"exit_code": 0, "output": "ok", "installed": [{"name": "pandas_ta", "version": "0.3.14b0"}]}

    def fake_run(body):
        seen["run"] = body
        return {"exit_code": 0, "stdout": "done\n", "stderr": "", "timed_out": False, "killed_for": "", "duration_s": 0.1,
                "files": [{"name": "chart.png", "bytes": 4, "b64": B64(b"\x89PNG")}], "skipped": []}

    fake = AgentOnSocket({"/install": fake_install, "/run": fake_run, "/list": lambda b: {"pip": [], "npm": []}})
    seen["dir"] = str(fake.dir)
    calls = iter([
        [ToolCall("c1", "workroom_install", '{"kind":"pip","packages":["pandas-ta"],"reason":"지표 계산"}'), Done("tool_calls")],
        [TextDelta("ok"), Done("stop")],
        [ToolCall("c2", "workroom_install", '{"kind":"pip","packages":["pandas_ta"],"reason":"다시"}'),
         ToolCall("c3", "workroom_run", '{"language":"python","code":"print(1)","files_in":["uploads/a.pdf"]}'), Done("tool_calls")],
        [TextDelta("ok"), Done("stop")],
    ])

    async def fn(provider, request, **kw):
        for ev in next(calls):
            yield ev

    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False, workroom_dir=str(fake.dir), public_url="https://kelo.example.com")

    async def turn(c, text, answer=None):
        tid = (await c.post("/api/turns", json={"text": text})).json()["id"]
        events, after = [], 0
        for _ in range(200):
            r = (await c.get(f"/api/turns/{tid}", params={"after": after, "wait": 2})).json()
            events += r["events"]
            after = r["next"]
            for ev in r["events"]:
                if ev["type"] == "approval_request" and answer is not None:
                    events.append({"type": "answered", **(await c.post(f"/api/approvals/{ev['approval_id']}", json=answer)).json()})
            if r["done"]:
                return events
        raise AssertionError

    try:
        app = create_app(s, stream_fn=fn)
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            assert (await c.put("/api/uploads/a.pdf", content=b"%PDF-1.4 x")).status_code == 200
            first = await turn(c, "지표 계산해줘", {"approve": True, "remember": "site"})
            card = next(e for e in first if e["type"] == "approval_request")
            assert card["label"] == "Installing a program in the workroom" and card["detail"].startswith("Install a program in the work room: pandas-ta (PyPI)")
            assert card["site"] == "pandas-ta.pypi.install" and card["can_remember"] is True
            assert next(e for e in first if e["type"] == "answered")["decision"] == "allowed_always"
            assert seen["other_host"] == ["HTTP/1.1 403 Forbidden\r\nContent-Length: 22\r\nConnection: close\r\n\r\nnot a package registry"]
            second = await turn(c, "또 해줘")
            assert not any(e["type"] == "approval_request" for e in second)                 # remembered for this package only
            chip = next(e for e in second if e["type"] == "tool_started" and e["name"] == "workroom_run")
            assert chip["label"] == "Running in the workroom" and chip["hidden"] is False
            assert len(set(seen["passwords"])) == 2                                            # a new pass for every install
            assert seen["run"]["files"] == [{"name": "a.pdf", "b64": B64(b"%PDF-1.4 x")}]
            lib = (await c.get("/api/files/library")).json()
            assert [(f["name"], f["bytes"], f["type"], f["kind"]) for f in lib] == [("chart.png", 4, "image/png", "image")]
            off = await asyncio.to_thread(raw_connect, fake.dir / "proxy.sock", "pypi.org", seen["passwords"][-1])
            assert off.startswith("HTTP/1.1 403")                                              # off again
    finally:
        fake.close()


# ------------------------------------------------------------------------------------------------ the library
def test_library_keeps_every_file_newest_first_and_trash_restores_and_purges_after_30_days(tmp_path):
    clock = Clock()
    lib = Deliverables(Home(tmp_path / "h"), clock)
    assert lib.save("report.xlsx", b"1") == "report.xlsx"
    clock.t += 5
    assert lib.save("report.xlsx", b"22") == "report-1.xlsx"
    old = time.time() - 100
    os.utime(lib.path("report.xlsx"), (old, old))
    assert [r["name"] for r in lib.list()] == ["report-1.xlsx", "report.xlsx"]
    assert lib.save("../../evil.sh", b"x") == "evil.sh" and (lib.dir / "evil.sh").is_file()
    entry = lib.trash("report.xlsx")
    assert entry["purge_at"] == entry["deleted_at"] + TRASH_DAYS * 86400 and not (lib.dir / "report.xlsx").exists()
    assert [t["name"] for t in lib.trashed()] == ["report.xlsx"]
    lib.save("report.xlsx", b"new")
    assert lib.restore("report.xlsx") == "report-2.xlsx" and lib.trashed() == []               # a name in use: kept beside it
    lib.trash("evil.sh")
    clock.t += (TRASH_DAYS - 1) * 86400
    assert lib.purge() == 0 and len(lib.trashed()) == 1
    clock.t += 2 * 86400
    assert lib.purge() == 1 and lib.trashed() == [] and [p.name for p in lib.trash_dir.iterdir()] == ["index.json"]
    with pytest.raises(Exception, match="nothing named"):
        lib.restore("evil.sh")


def test_share_links_expire_after_48_hours_are_revocable_and_die_with_the_file(tmp_path):
    clock = Clock()
    lib = Deliverables(Home(tmp_path / "h"), clock)
    lib.save("a.pdf", b"%PDF")
    s = lib.share("a.pdf")
    assert s["expires_at"] == clock.t + SHARE_HOURS * 3600 and lib.shared(s["token"]) == lib.path("a.pdf")
    assert lib.list()[0]["shared_until"] == s["expires_at"]
    assert lib.share("a.pdf", hours=500)["expires_at"] == clock.t + SHARE_HOURS * 3600         # never longer than 48 h
    assert lib.shared("x" * 43) is None and lib.shared("../../etc") is None
    clock.t += SHARE_HOURS * 3600 + 1
    assert lib.shared(s["token"]) is None and lib.list()[0]["shared_until"] is None
    s2 = lib.share("a.pdf")
    assert lib.unshare("a.pdf") >= 1 and lib.shared(s2["token"]) is None
    s3 = lib.share("a.pdf")
    lib.trash("a.pdf")
    assert lib.shared(s3["token"]) is None
    lib.restore("a.pdf")
    assert lib.shared(s3["token"]) is None                                                       # deleting revoked it for good


async def test_the_agent_asks_for_every_share_link_and_none_is_remembered(tmp_path):
    w, _ = room(tmp_path)
    w.files.save("a.pdf", b"%PDF")
    tools = wr.workroom_tools(w, share=lambda n: {**w.files.share(n), "url": "https://k/shared/x"})
    t = by_name(tools)["share_file"]
    assert t.requires_approval and t.auto_approve is None and rule_kind("share_file") == ""
    assert "no file named" in t.validate({"name": "nope.pdf"}) and t.validate({"name": "files/a.pdf"}) is None
    assert "48 hours" in await t.describe({"name": "a.pdf"})
    asked = []

    async def allow(req):
        asked.append(req.name)
        return True

    events = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "공유"}], tools, approve=allow, stream_fn=script(
        [ToolCall("c1", "share_file", '{"name":"a.pdf"}'), Done("tool_calls")], [ToolCall("c2", "share_file", '{"name":"a.pdf"}'), Done("tool_calls")],
        [TextDelta("ok"), Done("stop")]))]
    assert asked == ["share_file", "share_file"]                                                 # a card for each link
    assert "https://k/shared/x" in [e for e in events if type(e).__name__ == "ToolResult"][0].output


async def test_library_api_lists_downloads_shares_trashes_and_restores(tmp_path):
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False, public_url="https://kelo.example.com")
    app = create_app(s, tools=[], stream_fn=script())
    lib = Deliverables(Home(tmp_path / "home"))
    lib.save("chart.png", b"\x89PNG")
    lib.save("data.csv", b"a,b\n")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c, httpx.AsyncClient(base_url=srv.url) as anon:
        assert (await anon.get("/api/files/library")).status_code == 401
        rows = (await c.get("/api/files/library")).json()
        assert {r["name"] for r in rows} == {"chart.png", "data.csv"} and all({"bytes", "type", "created", "kind"} <= set(r) for r in rows)
        f = await c.get("/api/files/library/data.csv/file")
        assert f.content == b"a,b\n" and "attachment" in f.headers["content-disposition"] and f.headers["x-content-type-options"] == "nosniff"
        assert (await c.get("/api/files/library/nope.csv/file")).status_code == 404
        assert (await c.post("/api/files/library/data.csv/share", json={})).status_code == 428          # made only on purpose
        sh = (await c.post("/api/files/library/data.csv/share", json={"confirm": True})).json()
        assert sh["url"] == f"https://kelo.example.com/shared/{sh['token']}" and sh["path"] == f"/shared/{sh['token']}"
        assert (await anon.get(sh["path"])).content == b"a,b\n"                                         # public, no token needed
        assert (await anon.get("/shared/" + "x" * 43)).status_code == 404
        assert (await c.delete("/api/files/library/data.csv/share")).json() == {"ok": True, "revoked": 1}
        assert (await anon.get(sh["path"])).status_code == 404
        d = (await c.delete("/api/files/library/chart.png")).json()
        assert d["ok"] and d["trashed"]["name"] == "chart.png"
        assert [t["name"] for t in (await c.get("/api/files/trash")).json()] == ["chart.png"]
        assert [r["name"] for r in (await c.get("/api/files/library")).json()] == ["data.csv"]
        assert (await c.post("/api/files/library/chart.png/restore")).json() == {"ok": True, "name": "chart.png"}
        assert (await c.post("/api/files/library/chart.png/restore")).status_code == 404
        assert (await c.get("/api/files/MEMORY.md")).json()["content"].startswith("# MEMORY.md")         # the agent files still work


# ------------------------------------------------------------------------------------------------ the real image (opt-in)
@pytest.mark.skipif(os.environ.get("KELO_DOCKER_TESTS") != "1" or not shutil.which("docker"),
                    reason="needs Docker and the assistant-workroom:local image; set KELO_DOCKER_TESTS=1")
def test_real_work_room_runs_python_without_network():
    name, vol = "kelo-wr-it", "kelo-wr-it-io"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt",
                    "no-new-privileges:true", "--user", "1003:1050", "--tmpfs", "/tmp", "-v", f"{vol}:/run/workroom", "-v", "/work", "-v", "/tools",
                    "assistant-workroom:local"], check=True, capture_output=True)
    client = r'''
import json, socket
s = socket.socket(socket.AF_UNIX); s.connect("/run/workroom/agent.sock")
code = "import pandas, urllib.request\nprint(pandas.__version__)\ntry:\n urllib.request.urlopen('https://example.com', timeout=5); print('NET')\nexcept Exception: print('NONET')"
body = json.dumps({"language": "python", "code": code, "timeout_s": 60}).encode()
s.sendall(b"POST /run HTTP/1.0\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
buf = b""
while (c := s.recv(65536)): buf += c
print(json.loads(buf.split(b"\r\n\r\n", 1)[1])["stdout"])
'''
    try:
        for _ in range(30):
            if subprocess.run(["docker", "exec", name, "test", "-S", "/run/workroom/agent.sock"]).returncode == 0:
                break
            subprocess.run(["sleep", "1"])
        out = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--user", "1003:1050", "-v", f"{vol}:/run/workroom",
                              "--entrypoint", "python3", "assistant-workroom:local", "-"], input=client, capture_output=True, text=True, timeout=120)
        assert "NONET" in out.stdout and "NET\n" not in out.stdout.replace("NONET", ""), out.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", vol], capture_output=True)
