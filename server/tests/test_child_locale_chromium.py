"""A cross-site frame and a dedicated worker of a desktop tab in Korean see the tab's language (navigator.languages, Intl, the
Accept-Language header), not the browser's own list. Runs on Playwright Chromium with DevTools attached through ChildTargets (skipped
when Playwright Chromium is absent)."""

import json
import socket
import urllib.request

import pytest

from assistant.tools.child_targets import ChildTargets

pw = pytest.importorskip("playwright.async_api")
websockets = pytest.importorskip("websockets")

MAIN = """<!doctype html><html><body>
<iframe id="f" src="http://frame.test/frame"></iframe>
<script>
  const w = new Worker('/worker.js');
  w.onmessage = (e) => { window.workerSeen = e.data; };
  window.addEventListener('message', (e) => { if (e.data && e.data.kind === 'frame') window.frameSeen = e.data; });
</script></body></html>"""
FRAME = """<!doctype html><html><body><script>
  parent.postMessage({kind: 'frame', languages: navigator.languages, lang: navigator.language,
                      intl: Intl.DateTimeFormat().resolvedOptions().locale}, '*');
</script></body></html>"""
WORKER = """postMessage({languages: navigator.languages, lang: navigator.language, intl: Intl.DateTimeFormat().resolvedOptions().locale});"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_a_cross_site_frame_and_a_dedicated_worker_of_a_korean_tab_see_korean():
    async with pw.async_playwright() as p:
        port = _free_port()
        try:
            browser = await p.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}", "--site-per-process", "--isolate-origins=http://frame.test"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        children = None
        try:
            version = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=10).read())
            ws = await websockets.connect(version["webSocketDebuggerUrl"], max_size=None, ping_interval=None)
            children = ChildTargets(ws, lambda info, parent: "ko-KR")
            await children.start()
            context = await browser.new_context(locale="ko-KR")
            heads: dict[str, str] = {}

            async def route(r):
                url = r.request.url
                if url.startswith("http://frame.test/"):
                    heads["frame"] = r.request.headers.get("accept-language", "")
                    await r.fulfill(status=200, content_type="text/html", body=FRAME)
                elif url.endswith("/worker.js"):
                    await r.fulfill(status=200, content_type="text/javascript", body=WORKER)
                else:
                    await r.fulfill(status=200, content_type="text/html", body=MAIN)

            await context.route("http://*.test/**", route)
            page = await context.new_page()
            await page.goto("http://site.test/")
            for _ in range(100):
                if await page.evaluate("() => !!(window.frameSeen && window.workerSeen)"):
                    break
                await page.wait_for_timeout(50)
            frame = await page.evaluate("() => window.frameSeen")
            worker = await page.evaluate("() => window.workerSeen")
            assert frame["languages"][0] == "ko-KR" and frame["intl"] == "ko-KR", frame
            assert worker["languages"][0] == "ko-KR" and worker["intl"] == "ko-KR", worker
            assert heads["frame"].startswith("ko-KR"), heads
        finally:
            if children is not None:
                await children.close()
            await browser.close()
