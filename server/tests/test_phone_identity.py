"""The phone view looks like a real Android phone (BrowserSession._phone, set_view): navigator.platform, the client hints' model and
platform version, no plugins or PDF viewer, the phone's core count and GPU, all with native-looking functions. The fake-CDP tests check
what is sent; the real-Chromium tests (skipped without Playwright's browser) check what a page sees. The GPU answer (GPU_SCRIPT, given to
every context) is the phone's in the phone view and the desktop's Intel GPU in the desktop view, and it flips with the view."""

from __future__ import annotations

import json

import pytest

from assistant.tools.browser import (DESKTOP_DEVICE, GPU_DATA, GPU_SCRIPT, PHONE_DEVICE, PHONE_SCRIPT, BrowserSession, browser_owner,
                                     mobile_identity)

from test_purchase import PUBLIC

pw = pytest.importorskip("playwright.async_api")

FIXTURE = "https://fixture.test/"
PAGE = "<!doctype html><title>phone</title><body>phone identity fixture</body>"
DESKTOP_STAND_IN = """
Object.defineProperty(Navigator.prototype, 'plugins', {configurable: true, get() { if (this !== navigator) throw new TypeError('Illegal invocation'); return {length: 5}; }});
Object.defineProperty(Navigator.prototype, 'mimeTypes', {configurable: true, get() { if (this !== navigator) throw new TypeError('Illegal invocation'); return {length: 2}; }});
Object.defineProperty(Navigator.prototype, 'pdfViewerEnabled', {configurable: true, get() { if (this !== navigator) throw new TypeError('Illegal invocation'); return true; }});
"""


class FakeCDP:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.sent.append((method, dict(params or {})))
        if method == "Browser.getVersion":
            return {"product": "Chrome/131.0.6778.85"}
        if method == "Page.addScriptToEvaluateOnNewDocument":
            return {"identifier": "script-1"}
        return {}

    def calls(self, method: str) -> list[dict]:
        return [p for m, p in self.sent if m == method]


class FakePage:
    def is_closed(self) -> bool:
        return False

    async def evaluate(self, _js: str) -> str:
        return "Mozilla/5.0 Chrome/131.0.0.0"


class FakeContext:
    def __init__(self, pages=()) -> None:
        self.pages = list(pages)
        self.scripts: list[str] = []

    async def add_init_script(self, script: str) -> None:
        self.scripts.append(script)


class FakeOpenPage:
    def __init__(self) -> None:
        self.evaluated: list[str] = []

    async def evaluate(self, src: str) -> None:
        self.evaluated.append(src)


async def test_a_context_gets_the_gpu_script_once_even_when_asked_twice_and_open_pages_run_it_once():
    s = BrowserSession(connect=None, resolver=PUBLIC)
    open_page = FakeOpenPage()
    ctx = FakeContext(pages=[open_page])
    await s._install_gpu(ctx)
    await s._install_gpu(ctx)
    assert len(ctx.scripts) == 1 and ctx.scripts[0].startswith(f"({GPU_SCRIPT})(")
    assert open_page.evaluated == [ctx.scripts[0]]
    other = FakeContext()
    await s._install_gpu(other)
    assert len(other.scripts) == 1


def test_the_gpu_answers_come_from_the_page_mobile_flag_not_the_view_name():
    arg = GPU_DATA
    assert arg["desktop"] == DESKTOP_DEVICE
    assert arg["phone"]["gl_renderer"] == PHONE_DEVICE["gl_renderer"]
    assert "Intel" not in GPU_SCRIPT and "Mali" not in GPU_SCRIPT and "userAgentData.mobile" in GPU_SCRIPT


def test_the_phone_identity_says_it_is_a_pixel_on_android():
    p = mobile_identity("131.0.6778.85")
    md = p["userAgentMetadata"]
    assert p["platform"] == "Linux armv81"
    assert md["model"] == "Pixel 7" and md["platformVersion"] == "14.0.0"
    assert md["platform"] == "Android" and "Pixel" not in p["userAgent"]     # the reduced User-Agent string stays as Chrome sends it


async def test_the_phone_view_sends_the_cores_and_the_device_script_and_keeps_its_id_out_of_the_user_agent():
    cdp = FakeCDP()
    ident = await BrowserSession._phone(cdp, FakePage())
    assert cdp.calls("Emulation.setHardwareConcurrencyOverride") == [{"hardwareConcurrency": PHONE_DEVICE["cores"]}]
    (added,) = cdp.calls("Page.addScriptToEvaluateOnNewDocument")
    assert added["runImmediately"] is True and added["source"].startswith(f"({PHONE_SCRIPT})(")
    assert "Mali-G710" not in PHONE_SCRIPT and "Pixel 7" not in PHONE_SCRIPT      # the values come in as the argument only
    assert json.loads(added["source"][len(f"({PHONE_SCRIPT})("):-1]) == PHONE_DEVICE
    (ua,) = cdp.calls("Emulation.setUserAgentOverride")
    assert "_script" not in ua and ident["_script"] == "script-1"


async def test_going_back_to_desktop_removes_the_device_script_with_its_identifier():
    cdp = FakeCDP()

    async def connect():
        return FakePage()
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    key = browser_owner.get()
    s._pages[key] = FakePage()          # the tab's page: page() would otherwise start a new one and drop the phone view
    s._view_cdp[key] = cdp
    s._mobile_id[key] = {"_script": "script-1"}
    assert await s.set_view("desktop") is True
    assert cdp.calls("Page.removeScriptToEvaluateOnNewDocument") == [{"identifier": "script-1"}]


@pytest.fixture
async def page():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        ctx = await b.new_context()
        pg = await ctx.new_page()

        async def serve(route):
            await route.fulfill(status=200, content_type="text/html", body=PAGE)
        await pg.route("https://fixture.test/**", serve)
        await BrowserSession(connect=None, resolver=PUBLIC)._install_gpu(ctx)
        cdp = await ctx.new_cdp_session(pg)
        await cdp.send("Page.enable", {})
        # Headless Chromium here has no plugins and no PDF viewer, so a desktop page's are stood in for first (a script that runs before
        # the phone's): without the phone script the page would still show them.
        await cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": DESKTOP_STAND_IN})
        await BrowserSession._phone(cdp, pg)
        await pg.goto(FIXTURE)
        yield pg
        await b.close()


async def test_the_page_sees_a_phone_platform_and_core_count(page):
    assert await page.evaluate("() => navigator.platform") == "Linux armv81"
    assert await page.evaluate("() => navigator.hardwareConcurrency") == PHONE_DEVICE["cores"]


async def test_the_page_sees_no_plugins_and_no_pdf_viewer(page):
    assert await page.evaluate("() => [navigator.plugins.length, navigator.pdfViewerEnabled]") == [0, False]
    r = await page.evaluate("""() => ({
        plugins: navigator.plugins.length, mimeTypes: navigator.mimeTypes.length,
        isPluginArray: navigator.plugins instanceof PluginArray, isMimeArray: navigator.mimeTypes instanceof MimeTypeArray,
        item: navigator.plugins.item(0), pdf: navigator.pdfViewerEnabled })""")
    assert r == {"plugins": 0, "mimeTypes": 0, "isPluginArray": True, "isMimeArray": True, "item": None, "pdf": False}


async def test_the_empty_plugin_list_has_no_own_properties_and_the_getter_keeps_its_receiver_check(page):
    """A real phone's navigator.plugins has no own properties; a hand-made object with its own `length`/`item` gives itself away."""
    r = await page.evaluate("""() => {
        let threw = false;
        try { Object.getOwnPropertyDescriptor(Navigator.prototype, 'plugins').get.call({}); } catch (e) { threw = true; }
        return { own: Object.getOwnPropertyNames(navigator.plugins).length + Object.getOwnPropertyNames(navigator.mimeTypes).length,
                 spread: [...navigator.plugins].length, named: navigator.plugins.namedItem('x'),
                 itemNative: navigator.plugins.item.toString().includes('[native code]'), threw };
    }""")
    assert r == {"own": 0, "spread": 0, "named": None, "itemNative": True, "threw": True}


async def test_the_page_sees_the_phone_gpu(page):
    assert await gpu(page) == ["ARM", "Mali-G710 MP7"]


async def gpu(page) -> list | None:
    r = await page.evaluate("""() => {
        const c = document.createElement('canvas').getContext('webgl');
        if (!c) return null;
        const e = c.getExtension('WEBGL_debug_renderer_info');
        return [c.getParameter(e.UNMASKED_VENDOR_WEBGL), c.getParameter(e.UNMASKED_RENDERER_WEBGL)]; }""")
    return r


async def test_the_replaced_functions_still_read_as_native(page):
    r = await page.evaluate("""() => [
        Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter),
        Object.getOwnPropertyDescriptor(Navigator.prototype, 'plugins').get.toString()]""")
    assert all("[native code]" in s for s in r)


async def test_the_client_hints_say_pixel_7_and_android_14(page):
    r = await page.evaluate("""() => navigator.userAgentData.getHighEntropyValues(['model', 'platformVersion'])""")
    assert r["model"] == "Pixel 7" and r["platformVersion"] == "14.0.0"


@pytest.fixture
async def view_session():
    """A tab in a Playwright context that has GPU_SCRIPT, switched with BrowserSession.set_view like the tools do."""
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        ctx = await b.new_context()
        pg = await ctx.new_page()

        async def serve(route):
            await route.fulfill(status=200, content_type="text/html", body=PAGE)
        await pg.route("https://fixture.test/**", serve)

        async def connect():
            return pg
        s = BrowserSession(connect=connect, resolver=PUBLIC)
        await s._install_gpu(ctx)
        s._pages[browser_owner.get()] = pg
        await pg.goto(FIXTURE)
        yield s, pg
        await b.close()


async def test_switching_views_and_reloading_flips_the_gpu_and_keeps_native_functions(view_session):
    s, pg = view_session
    assert await gpu(pg) == [DESKTOP_DEVICE["gl_vendor"], DESKTOP_DEVICE["gl_renderer"]]
    assert await s.set_view("mobile") is True
    await pg.reload()
    assert await gpu(pg) == ["ARM", "Mali-G710 MP7"]
    assert "[native code]" in await pg.evaluate("() => Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter)")
    assert await s.set_view("desktop") is True
    await pg.reload()
    assert await gpu(pg) == [DESKTOP_DEVICE["gl_vendor"], DESKTOP_DEVICE["gl_renderer"]]
    assert "[native code]" in await pg.evaluate("() => Function.prototype.toString.call(WebGL2RenderingContext.prototype.getParameter)")
