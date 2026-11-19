"""Live checks (Chromium through Playwright) of the work browser's per-site seeding (card p-browser-signin-shared).

S1 asks: can a cookie be put into a browser context before a document request is sent with it? Three hooks are tried on a local
server (two host names stand for two sites): a context route (route.fallback after add_cookies), the same across a redirect to the
other host name, and a CDP Fetch pause at the Request stage. The answers decide how the work browser seeds a site (see
work_browser.SEED_PER_SITE). Skipped when no Chromium for Playwright is installed."""

import asyncio
import glob
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright_async = pytest.importorskip("playwright.async_api")


def chromium_installed() -> bool:
    return bool(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/*")) or glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/*")))


pytestmark = pytest.mark.skipif(not chromium_installed(), reason="no Chromium for Playwright installed")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path.startswith("/echo"):
            body = f"cookie={self.headers.get('Cookie', '')}".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/hop"):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/echo")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path.startswith("/pop"):
            body = f"<html><body><script>window.open('http://localhost:{port}/echo');</script></body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/set"):
            body = b"set"
            self.send_response(200)
            self.send_header("Set-Cookie", "keep=yes; Path=/")             # a session cookie: no Expires, no Max-Age
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def _route_hook(ctx):
    async def handler(route, request):
        if request.resource_type == "document":
            from urllib.parse import urlsplit
            host = urlsplit(request.url).hostname
            await ctx.add_cookies([{"name": "s", "value": "1", "domain": host, "path": "/"}])
        await route.fallback()
    return handler


async def _body(page, url):
    resp = await page.goto(url)
    return (await resp.text()) if resp else ""


async def test_route_hook_seeds_before_the_document_request(server):
    """S1-1: the route hook puts the cookie in before the request of a document."""
    async with playwright_async.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context()
            await ctx.route("**/*", _route_hook(ctx))
            page = await ctx.new_page()
            assert "cookie=s=1" in await _body(page, f"http://127.0.0.1:{server}/echo")
            await ctx.close()
        finally:
            await browser.close()


async def test_route_hook_popup_is_seeded(server):
    """S1-1b: a popup opened by a page of the context gets the cookie too (context routes cover popups)."""
    async with playwright_async.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context()
            await ctx.route("**/*", _route_hook(ctx))
            page = await ctx.new_page()
            await page.goto(f"http://127.0.0.1:{server}/echo")
            await ctx.add_cookies([{"name": "s", "value": "1", "domain": "127.0.0.1", "path": "/"}])
            async with ctx.expect_page() as got:
                await page.evaluate(f"window.open('http://127.0.0.1:{server}/echo')")
            popup = await got.value
            await popup.wait_for_load_state()
            assert "cookie=s=1" in await popup.evaluate("document.body.innerText")
            await ctx.close()
        finally:
            await browser.close()


async def test_route_hook_and_a_redirect_to_another_host(server):
    """S1-2 (recorded, not a gate): does the hook seed the second host of a redirect chain?"""
    async with playwright_async.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context()
            await ctx.route("**/*", _route_hook(ctx))
            page = await ctx.new_page()
            body = await _body(page, f"http://127.0.0.1:{server}/hop")
            print(f"\nS1-2 redirect via route hook: {'seeded' if 'cookie=s=1' in body else 'not seeded'}: {body!r}")
            await ctx.close()
        finally:
            await browser.close()


async def test_cdp_fetch_hook_and_a_redirect(server):
    """S1-3 (recorded, not a gate): a CDP Fetch pause at the Request stage, for document requests only."""
    from urllib.parse import urlsplit
    async with playwright_async.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            cdp = await ctx.new_cdp_session(page)

            async def on_paused(ev):
                host = urlsplit(ev["request"]["url"]).hostname
                try:
                    await ctx.add_cookies([{"name": "s", "value": "1", "domain": host, "path": "/"}])
                finally:
                    await cdp.send("Fetch.continueRequest", {"requestId": ev["requestId"]})

            cdp.on("Fetch.requestPaused", lambda ev: asyncio.ensure_future(on_paused(ev)))
            await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]})
            body = await _body(page, f"http://127.0.0.1:{server}/hop")
            print(f"\nS1-3 redirect via CDP Fetch hook: {'seeded' if 'cookie=s=1' in body else 'not seeded'}: {body!r}")
            await ctx.close()
        finally:
            await browser.close()


async def test_route_hook_and_cdp_fetch_hook_together_seed_a_redirect(server):
    """S1-4: both hooks on one page (the path R+F of the card): a redirect to the other host is seeded, and nothing hangs."""
    from urllib.parse import urlsplit
    async with playwright_async.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context()
            await ctx.route("**/*", _route_hook(ctx))
            page = await ctx.new_page()
            cdp = await ctx.new_cdp_session(page)

            async def on_paused(ev):
                host = urlsplit(ev["request"]["url"]).hostname
                try:
                    await ctx.add_cookies([{"name": "s", "value": "1", "domain": host, "path": "/"}])
                finally:
                    await cdp.send("Fetch.continueRequest", {"requestId": ev["requestId"]})

            cdp.on("Fetch.requestPaused", lambda ev: asyncio.ensure_future(on_paused(ev)))
            await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]})
            body = await _body(page, f"http://127.0.0.1:{server}/hop")
            print(f"\nS1-4 route + CDP hook, redirect: {'seeded' if 'cookie=s=1' in body else 'not seeded'}: {body!r}")
            assert "cookie=s=1" in body
            await ctx.close()
        finally:
            await browser.close()


async def _restart_keeps(server, profile: Path, args: list[str]) -> bool:
    """A session cookie set by the site, the full browser closed and started again on the same profile with `args` (the saved tabs
    removed in between, as docker/entrypoint.sh does): is the cookie still there? `channel="chromium"` is the full browser; the
    headless shell (`headless=True` alone) has no session restore at all."""
    import shutil

    async with playwright_async.async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(str(profile), channel="chromium", headless=True, args=args)
        page = await ctx.new_page()
        await page.goto(f"http://127.0.0.1:{server}/set")
        await ctx.close()
        shutil.rmtree(profile / "Default" / "Sessions", ignore_errors=True)
        ctx = await pw.chromium.launch_persistent_context(str(profile), channel="chromium", headless=True, args=args)
        try:
            return any(c["name"] == "keep" and c["value"] == "yes" for c in await ctx.cookies())
        finally:
            await ctx.close()


async def test_a_session_cookie_survives_a_restart_with_the_restore_switch(server, tmp_path):
    """Card p-signin-gaps S1: the owner's browser starts with --restore-last-session (docker/entrypoint.sh), so a sign-in without
    "stay signed in" survives a restart of the browser container."""
    assert await _restart_keeps(server, tmp_path / "profile", ["--restore-last-session"])


async def test_without_the_restore_switch_a_session_cookie_is_lost(server, tmp_path):
    """Why the switch is passed: without it Chrome deletes session cookies at start (the profile preference alone did not keep them)."""
    import json

    profile = tmp_path / "profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text(json.dumps({"session": {"restore_on_startup": 1}}))
    assert not await _restart_keeps(server, profile, [])


@pytest.mark.xfail(strict=True, reason="card p-browser-signin-shared C1c live result: a tab made with Target.createTarget (no newWindow) "
                    "lands in a new window, not in the focused one (headless Chromium). Per the card's rule, `new` is not offered for "
                    "default-profile main windows; the coordinator is told. Not retried with other flags.")
async def test_a_new_tab_opens_in_the_focused_window(server):
    """Card C1c: browser_tabs new in a main-browser task window opens the tab with Target.createTarget (no newWindow): Chrome puts it in
    the last focused window. The task's own window must be the one it lands in. If this fails, `new` is not offered for default-profile
    main windows (the tool description says so) and the coordinator is told."""
    async with playwright_async.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            bcdp = await browser.new_browser_cdp_session()
            made = [(await bcdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True}))["targetId"] for _ in range(2)]
            windows = [(await bcdp.send("Browser.getWindowForTarget", {"targetId": t}))["windowId"] for t in made]
            assert windows[0] != windows[1]
            await bcdp.send("Target.activateTarget", {"targetId": made[1]})       # the second window is the focused one
            landed = await bcdp.send("Target.createTarget", {"url": "about:blank"})
            window_of_new = (await bcdp.send("Browser.getWindowForTarget", {"targetId": landed["targetId"]}))["windowId"]
            assert window_of_new == windows[1]
        finally:
            await browser.close()
