"""The Chromium-backed check of the stated identity (browser_identity.identity_for): a page, a cross-site frame and a dedicated worker
of one Chrome all report the stated OS's client hints (Linux by default, Chrome OS with the switch), and the Linux navigator.platform
(on a Linux host, where the container runs).

Chrome is started here with its DevTools port open and the module's own Identity client attached to it, as the browser container's
entrypoint does. Skipped when no Chrome binary is found (Playwright's Chromium is used when it is installed)."""

import asyncio
import glob
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import websockets

from assistant import browser_identity as bi

ROOT = Path(__file__).resolve().parents[2]
PINNED = re.search(r"^ARG CHROME_VERSION=(\d+\.\d+\.\d+\.\d+)-\d+$", (ROOT / "docker/server.Dockerfile").read_text(), re.M).group(1)


def chrome_binary() -> str | None:
    for env in ("KELO_TEST_CHROME", "CHROME_BIN"):
        if os.environ.get(env) and Path(os.environ[env]).exists():
            return os.environ[env]
    hits = glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    hits += glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
    return hits[0] if hits else None


PROMISES = "() => Promise.all([window.__page, window.__worker, window.__frame || null])"
PAGE = """<!doctype html><title>chrome os</title><body>
<script>
  const report = () => ({
    userAgent: navigator.userAgent, platform: navigator.platform,
    uaData: navigator.userAgentData ? {platform: navigator.userAgentData.platform, mobile: navigator.userAgentData.mobile} : null,
  });
  window.addEventListener('message', (e) => { if (e.data && e.data.kind === 'frame') window.__frame = e.data.report; });
  window.__worker = new Promise((resolve) => {
    const w = new Worker('/worker.js');
    w.onmessage = (e) => resolve(e.data);
  });
  window.__page = report();
</script>
<iframe src="http://localhost:__PORT__/frame.html"></iframe></body>"""
FRAME = """<!doctype html><body><script>
  const r = {userAgent: navigator.userAgent, platform: navigator.platform,
             uaData: navigator.userAgentData ? {platform: navigator.userAgentData.platform, mobile: navigator.userAgentData.mobile} : null};
  parent.postMessage({kind: 'frame', report: r}, '*');
</script></body>"""
WORKER = """postMessage({userAgent: navigator.userAgent, platform: navigator.platform,
  uaData: navigator.userAgentData ? {platform: navigator.userAgentData.platform, mobile: navigator.userAgentData.mobile} : null});"""


def serve_dir() -> ThreadingHTTPServer:
    d = tempfile.mkdtemp(prefix="chromeos-identity-")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401 - keep the test output quiet
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=d))
    # the frame is on another host name (localhost), so it is a cross-site frame of the page on 127.0.0.1
    Path(d, "page.html").write_text(PAGE.replace("__PORT__", str(srv.server_address[1])))
    Path(d, "frame.html").write_text(FRAME)
    Path(d, "worker.js").write_text(WORKER)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Recorder(bi.Identity):
    """The module's own client, remembering the session of each page target it attaches to (target id -> session id)."""
    def __init__(self, ws, params):
        super().__init__(ws, params)
        self.pages: dict[str, str] = {}

    async def attached(self, ev, parent=None):
        info = ev.get("targetInfo") or {}
        if info.get("type") == "page":
            self.pages[info.get("targetId", "")] = ev["sessionId"]
        await super().attached(ev, parent)


def check_ua(ctx: dict, stated: str) -> None:
    if stated == "chromeos":
        assert "X11; CrOS x86_64 14541.0.0" in ctx["userAgent"] and "Linux" not in ctx["userAgent"], ctx
        assert ctx["uaData"] == {"platform": "Chrome OS", "mobile": False}, ctx
    else:
        assert "X11; Linux x86_64" in ctx["userAgent"] and "CrOS" not in ctx["userAgent"], ctx
        assert ctx["uaData"] == {"platform": "Linux", "mobile": False}, ctx


@pytest.mark.parametrize("stated", ["linux", "chromeos"])
def test_a_page_a_cross_site_frame_and_a_worker_all_state_the_stated_os(monkeypatch, stated):
    # the default (linux) is the machine's own OS; the Chrome OS switch (chromeos) is checked too, so the switch keeps working
    monkeypatch.setattr(bi, "STATED_OS", stated)
    binary = chrome_binary()
    if not binary:
        pytest.skip("no Chrome binary here (set KELO_TEST_CHROME, or install Playwright's Chromium)")
    srv = serve_dir()
    origin = f"http://127.0.0.1:{srv.server_address[1]}"

    async def go():
        port = free_port()
        profile = tempfile.mkdtemp(prefix="chromeos-profile-")
        proc = subprocess.Popen([binary, "--headless=new", "--no-sandbox", "--no-first-run", f"--remote-debugging-port={port}",
                                 f"--user-data-dir={profile}", "--disable-gpu", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import urllib.request
            info = None
            for _ in range(200):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                        info = json.load(r)
                    break
                except Exception:  # noqa: BLE001 - not up yet
                    await asyncio.sleep(0.1)
            assert info, "Chrome's DevTools never came up"
            async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None, ping_interval=None) as ws:
                ident = Recorder(ws, bi.identity_for(PINNED))
                reader = asyncio.create_task(ident.run())
                try:
                    # a blank page first: the identity is on before the address is loaded (a page loaded before it is not the test's)
                    created = await ident.send("Target.createTarget", {"url": "about:blank"})
                    tid = created["targetId"]
                    for _ in range(200):
                        if tid in ident.pages:
                            break
                        await asyncio.sleep(0.05)
                    session = ident.pages.get(tid)
                    assert session, "the page was never attached"
                    await ident.send("Page.navigate", {"url": f"{origin}/page.html"}, session)
                    res = None
                    last = None
                    for _ in range(300):
                        r = await ident.send("Runtime.evaluate", {"expression": f"(({PROMISES})())", "awaitPromise": True,
                                                                   "returnByValue": True}, session)
                        last = r
                        val = (r.get("result") or {}).get("value")
                        if val and val[2]:
                            res = val
                            break
                        await asyncio.sleep(0.1)
                    assert res, f"the page, its frame or its worker did not report: {last}"
                    page, worker, frame = res
                    check_ua(page, stated)
                    check_ua(frame, stated)
                    check_ua(worker, stated)
                    hv = await ident.send("Runtime.evaluate", {"expression": "navigator.userAgentData.getHighEntropyValues(['platformVersion']).then(v => v.platformVersion)",
                                                               "awaitPromise": True, "returnByValue": True}, session)
                    want = bi.CHROME_OS_PLATFORM_VERSIONS[int(PINNED.split(".")[0])] if stated == "chromeos" else ""
                    assert hv["result"]["value"] == want
                    if sys.platform.startswith("linux"):   # the container's host: navigator.platform is the machine's own, Linux x86_64
                        assert page["platform"] == frame["platform"] == worker["platform"] == "Linux x86_64"
                    await ident.send("Target.closeTarget", {"targetId": tid})
                finally:
                    reader.cancel()
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    try:
        asyncio.run(asyncio.wait_for(go(), 120))
    finally:
        srv.shutdown()
