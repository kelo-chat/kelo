"""S6 browser upgrades, against fakes: bot-check detection, downloads, uploads, PDFs, private tabs, site sign-in states, the action
archive and its API, the takeover journal, one-time codes from mail, per-tab time zone, and the egress proxy's LAN block."""

import asyncio
import base64
import io
import time

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from assistant.activity import rule_kind, schedule_rule_ok
from assistant.agent import current_call
from assistant.browser_history import ActionArchive, history_routes
from assistant.deliverables import Deliverables
from assistant.egress import Denied, Egress, split_target
from assistant import i18n
from assistant.home import Home
from assistant.tools.browser import BrowserSession, browser_owner, code_mask, cookie_for, site_of
from assistant.tools.browser_more import BrowserExtras, step_detail
from assistant.tools.challenge import CHECK_TRIES, PROBE_JS, check_boxes, detect, note_for, try_note
from assistant.tools.downloads import DownloadArea, DownloadError, blocked_name, looks_executable
from assistant.tools.login_code import extract, find_code, from_site, site_words
from assistant.tools.takeover import BINDING, summarize


def jpeg(w=800, h=500) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 160)).save(buf, "JPEG", quality=50)
    return buf.getvalue()


def probe(**kw):
    return {"kelo_check": 1, "title": "", "marks": {}, "frames": [], "scripts": [], "inputs": 3, **kw}


# ------------------------------------------------------------------------------------------------ bot-check detection
@pytest.mark.parametrize("kw,want", [
    ({"headers": {"cf-mitigated": "challenge", "server": "cloudflare"}, "status": 403}, ("cloudflare", "interstitial")),
    ({"probe": probe(marks={"cf_running": {"w": 300, "h": 60, "shown": True}})}, ("cloudflare", "interstitial")),
    ({"probe": probe(frames=[{"src": "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/rcv0/0/x",
                              "w": 300, "h": 65, "shown": True}])}, ("cloudflare", "widget")),
    ({"probe": probe(frames=[{"src": "https://www.google.com/recaptcha/api2/anchor?k=abc&size=normal", "w": 304, "h": 78, "shown": True}])},
     ("recaptcha", "widget")),
    ({"probe": probe(frames=[{"src": "https://www.recaptcha.net/recaptcha/api2/bframe?k=abc", "w": 400, "h": 580, "shown": True}])},
     ("recaptcha", "widget")),
    ({"probe": probe(frames=[{"src": "https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html#frame=checkbox", "w": 303, "h": 78,
                              "shown": True}])}, ("hcaptcha", "widget")),
    ({"headers": {"x-datadome": "protected", "server": "nginx"}, "status": 403}, ("datadome", "interstitial")),
    ({"probe": probe(frames=[{"src": "https://geo.captcha-delivery.com/captcha/?initialCid=x", "w": 900, "h": 700, "shown": True}])},
     ("datadome", "interstitial")),
    ({"headers": {"x-px-blocked": "1"}, "status": 403}, ("perimeterx", "interstitial")),
    ({"probe": probe(marks={"px": {"w": 310, "h": 100, "shown": True}})}, ("perimeterx", "interstitial")),
    ({"probe": probe(frames=[{"src": "https://client-api.arkoselabs.com/fc/gc/?token=x", "w": 400, "h": 400, "shown": True}])},
     ("arkose", "widget")),
    ({"probe": probe(marks={"geetest": {"w": 300, "h": 200, "shown": True}})}, ("geetest", "widget")),
    ({"headers": {"x-amzn-waf-action": "captcha"}, "status": 405}, ("aws_waf", "interstitial")),
    ({"headers": {"server": "cloudflare"}, "status": 403, "probe": probe(scripts=["https://challenges.cloudflare.com/turnstile/v0/api.js"])},
     ("cloudflare", "interstitial")),
])
def test_bot_checks_are_found_by_structure(kw, want):
    c = detect(kw.get("probe"), status=kw.get("status"), headers=kw.get("headers"))
    assert c is not None and (c.vendor, c.kind) == want and c.evidence


@pytest.mark.parametrize("kw", [
    {"probe": probe(frames=[{"src": "https://www.google.com/recaptcha/api2/anchor?k=abc&size=invisible", "w": 256, "h": 60, "shown": True}])},
    {"probe": probe(frames=[{"src": "https://www.google.com/recaptcha/api2/bframe?k=abc", "w": 0, "h": 0, "shown": False}])},
    {"probe": probe(frames=[{"src": "https://challenges.cloudflare.com/turnstile/x", "w": 0, "h": 0, "shown": False}])},
    {"probe": probe(title="Please complete the CAPTCHA to continue")},        # words in a title or text alone are not structure
    {"probe": probe(title="Just a moment...")},                                # not even a known interstitial's title
    {"probe": "page text"},                                                    # not a page reading at all
    {"probe": probe(), "frame_urls": ["https://challenges.cloudflare.com/turnstile/x"]},   # a frame without a size proves nothing
    {"probe": probe(), "headers": {"server": "cloudflare"}, "status": 200},
    {"probe": probe(), "headers": {"x-datadome": "1"}, "status": 200},       # DataDome watching an ordinary page
])
def test_ordinary_pages_and_invisible_widgets_are_not_checks(kw):
    assert detect(kw.get("probe"), status=kw.get("status"), headers=kw.get("headers"), frame_urls=kw.get("frame_urls")) is None


def test_the_probe_reads_no_values_and_avoids_the_fake_routing_words():
    assert "kelo_check" in PROBE_JS and ".value" not in PROBE_JS and "setAttribute" not in PROBE_JS and "pw:" not in PROBE_JS


def test_the_notes_let_the_agent_try_twice_then_offer_the_live_view():
    check = detect(probe(marks={"cf_running": {"w": 300, "h": 60, "shown": True}}))
    first = try_note(check, "shop.example.com", CHECK_TRIES)
    assert CHECK_TRIES == 2 and first.startswith("[Human check on the page") and "2 of 2 tries left" in first and "shop.example.com" in first
    assert "browser_human_check" in first and "hand_over" in first and "No outside solving services" in first and "scripts" in first
    assert "Never try" not in first and "never solve" not in first.lower()
    note = note_for(check, "shop.example.com")
    assert note.startswith("[Human check on the page") and "do not try it again" in note and "Take control" in note
    for text in (first, note):                                      # general words: no product of a check maker in the instructions
        rest = text.split(":", 1)[1]
        assert not any(v in rest.lower() for v in ("cloudflare", "recaptcha", "hcaptcha", "turnstile", "captcha"))


class CheckPage:
    def __init__(self, probe_value=None, url="https://shop.example.com/"):
        self.url, self.probe_value, self._closed = url, probe_value, False
        self.frames = [self]
        self.handlers = {}

    def is_closed(self): return self._closed
    async def title(self): return "Shop"
    def on(self, ev, fn): self.handlers[ev] = fn

    async def evaluate(self, js, arg=None):
        if "kelo_check" in js:
            return self.probe_value
        return "Welcome"

    async def goto(self, url, **kw): self.url = url


def session_for(page, **kw):
    async def connect():
        return page

    return BrowserSession(connect=connect, resolver=lambda h, p, *a, **k: [(2, 1, 6, "", ("93.184.216.34", p))], **kw)


async def test_a_page_reading_lets_the_agent_try_twice_before_the_user_is_told_once():
    page = CheckPage(probe(marks={"cf_running": {"w": 300, "h": 60, "shown": True}}))
    s = session_for(page)
    seen = []
    s.check_listeners.append(lambda key, c, host: seen.append((key, c.vendor, host)))
    out = await s.snapshot()                                            # first sighting: the agent may try
    assert "[Human check on the page" in out and "Cloudflare" in out and "2 of 2 tries left" in out and seen == []
    assert "1 of 2 tries left" in await s.snapshot() and seen == []    # one try used, nobody told yet
    out = await s.snapshot()                                            # second try used: now it goes to the user
    assert "It needs the user now" in out and "Take control" in out
    await s.snapshot()
    assert seen == [("owner", "cloudflare", "shop.example.com")]       # announced once, not on every reading
    page.probe_value = probe()
    out = await s.snapshot()
    assert "Human check" not in out and "is gone" in out
    page.probe_value = probe(marks={"cf_running": {"w": 300, "h": 60, "shown": True}})                  # a new check later gets its own tries
    assert "2 of 2 tries left" in await s.snapshot()


async def test_the_agent_can_hand_a_check_over_at_once():
    page = CheckPage(probe(marks={"cf_running": {"w": 300, "h": 60, "shown": True}}))
    s = session_for(page)
    assert "2 of 2 tries left" in await s.snapshot()
    s.give_up_check()
    assert "It needs the user now" in await s.snapshot()


class TryPage(CheckPage):
    """A page with a check box widget the agent can look at, click and type into (mouse, keyboard, screenshots)."""
    viewport_size = {"width": 1280, "height": 800}

    def __init__(self, probe_value):
        super().__init__(probe_value)
        self.clicks, self.keys = [], []
        page = self

        class Mouse:
            async def click(self, x, y, **kw): page.clicks.append((round(x), round(y)))
            async def move(self, x, y, **kw): pass

        class Keyboard:
            async def type(self, text, **kw): page.keys.append(text)
            async def press(self, key, **kw): page.keys.append(key)

        self.mouse, self.keyboard = Mouse(), Keyboard()

    async def screenshot(self, **kw):
        return jpeg(800, 500)

    async def wait_for_load_state(self, *a, **k):
        return None


WIDGET = probe(frames=[{"src": "https://challenges.cloudflare.com/x", "x": 100, "y": 200, "w": 300, "h": 65, "shown": True}])


async def test_the_agent_looks_clicks_and_types_on_the_check_only_and_each_reading_counts_as_a_try():
    page = TryPage(WIDGET)
    s = session_for(page)
    s.settle_seconds = 0
    s.sign_in_wait = 0
    t = {x.name: x for x in BrowserExtras(s).tools()}["browser_human_check"]
    assert not t.requires_approval and t.untrusted and "solving services" in t.description
    assert "captcha" not in t.description.lower() and "cloudflare" not in t.description.lower()
    out = await t.handler({"action": "click", "x": 10, "y": 10})
    assert "look at the picture first" in out and page.clicks == []
    shot = await t.handler({"action": "look"})
    assert shot.images and shot.images[0].startswith("data:image/jpeg;base64,") and "800x500" in shot.head
    assert "x 62-250, y 125-166" in shot.head                                           # the check's box, in the picture's pixels
    out = await t.handler({"action": "click", "x": 600, "y": 400})
    assert "outside the human check" in out and page.clicks == []                       # never a click on the rest of the page
    assert "click the check's answer box first" in await t.handler({"action": "type", "text": "abc"})
    out = await t.handler({"action": "click", "x": 100, "y": 140})
    assert page.clicks == [(160, 224)] and "tries left" in out
    out = await t.handler({"action": "type", "text": "x7Kp"})
    assert page.keys == ["x7Kp"] and "tries left" in out
    page.probe_value = probe()                                                          # the check went away
    assert "no human check on this page" in await t.handler({"action": "look"})


async def test_hand_over_gives_the_check_to_the_user_at_once():
    page = TryPage(WIDGET)
    s = session_for(page)
    t = {x.name: x for x in BrowserExtras(s).tools()}["browser_human_check"]
    seen = []
    s.check_listeners.append(lambda key, c, host: seen.append(c.vendor))
    out = await t.handler({"action": "hand_over"})
    assert "It needs the user now" in out and seen == ["cloudflare"]


def test_check_boxes_are_the_visible_check_frames_and_marks_only():
    p = probe(frames=[{"src": "https://challenges.cloudflare.com/x", "x": 10, "y": 20, "w": 300, "h": 65, "shown": True},
                      {"src": "https://ads.example.com/x", "x": 0, "y": 0, "w": 300, "h": 250, "shown": True},
                      {"src": "https://www.google.com/recaptcha/api2/bframe", "x": 0, "y": 0, "w": 0, "h": 0, "shown": False}],
              marks={"hcaptcha": {"x": 5, "y": 400, "w": 303, "h": 78, "shown": True}, "cf_running": {"x": 0, "y": 0, "w": 900, "h": 600, "shown": True}})
    assert check_boxes(p) == [{"x": 10, "y": 20, "w": 300, "h": 65}, {"x": 5, "y": 400, "w": 303, "h": 78}]
    assert check_boxes("not a probe") == []


async def test_the_main_documents_response_headers_are_evidence():
    page = CheckPage(probe())
    s = session_for(page)
    await s.page()

    class Req:
        resource_type = "document"

    class Resp:
        request, status, headers = Req(), 403, {"cf-mitigated": "challenge", "Set-Cookie": "secret=1"}
        frame = None

    page.main_frame = None
    page.handlers["response"](Resp())
    assert s._doc["owner"] == (403, {"cf-mitigated": "challenge"})      # only the headers that matter are kept
    assert "Human check" in await s.snapshot()


async def test_a_code_box_points_to_find_login_code_only_when_a_mailbox_is_connected():
    page = CheckPage(probe(otp=1))
    s = session_for(page)
    assert "find_login_code" not in await s.snapshot()
    s.has_mailbox = lambda: True
    assert "find_login_code" in await s.snapshot()


# ------------------------------------------------------------------------------------------------ downloads
def test_download_area_tracks_blocks_and_caps(tmp_path):
    area = DownloadArea(tmp_path, max_bytes=1000)
    d = area.begin("task:w1", {"guid": "g-aaaaaaaa", "url": "https://x.example/r.pdf", "suggestedFilename": "report.pdf"})
    assert d.state == "in_progress" and d.name == "report.pdf"
    assert area.begin("task:w1", {"guid": "g-bbbbbbbb", "suggestedFilename": "report.pdf"}).name == "report-1.pdf"
    assert area.begin("task:w1", {"guid": "g-cccccccc", "suggestedFilename": "setup.EXE"}).state == "blocked"
    too_big = area.progress({"guid": "g-bbbbbbbb", "state": "inProgress", "receivedBytes": 2000, "totalBytes": 5000})
    assert too_big is not None and too_big.state == "canceled" and "MB" in too_big.reason
    assert area.progress({"guid": "g-aaaaaaaa", "state": "completed", "receivedBytes": 5, "totalBytes": 5}) is None
    (tmp_path / "g-aaaaaaaa").write_bytes(b"%PDF-")
    assert area.read(area.find("task:w1", "report.pdf")) == b"%PDF-"
    assert [r["name"] for r in area.list("task:w1")][0] == "setup.EXE" and area.list("owner") == []
    with pytest.raises(DownloadError):
        area.find("owner", "report.pdf")                                       # another tab's downloads are not this tab's
    with pytest.raises(DownloadError, match="blocked"):
        area.read(area.find("task:w1", "setup.EXE"))


def test_a_program_disguised_by_its_name_is_refused_by_its_bytes(tmp_path):
    area = DownloadArea(tmp_path)
    d = area.begin("owner", {"guid": "g-dddddddd", "suggestedFilename": "invoice.pdf"})
    area.progress({"guid": "g-dddddddd", "state": "completed", "receivedBytes": 10})
    (tmp_path / "g-dddddddd").write_bytes(b"MZ\x90\x00rest")
    with pytest.raises(DownloadError, match="program"):
        area.read(d)
    assert d.state == "blocked"
    assert looks_executable(b"\x7fELF") and looks_executable(b"#!/bin/sh") and not looks_executable(b"%PDF")
    assert blocked_name("x.apk") and blocked_name("a.tar.sh") and not blocked_name("a.csv")


async def test_download_events_go_to_the_tab_whose_frame_started_them_and_bad_ones_are_cancelled(tmp_path):
    s = BrowserSession(connect=None)
    s.downloads = DownloadArea(tmp_path)
    sent = []

    class BCdp:
        async def send(self, m, p=None):
            sent.append((m, p))

    s._dl_cdp = BCdp()
    s._targets = {"task:w1": ("FRAME1", "CTX"), "owner": ("FRAME0", "CTX")}
    s._download_began({"guid": "g-11111111", "frameId": "FRAME1", "url": "https://x.example/a.zip", "suggestedFilename": "a.zip"})
    s._download_began({"guid": "g-22222222", "frameId": "FRAME0", "url": "https://x.example/b.msi", "suggestedFilename": "b.msi"})
    await asyncio.sleep(0)
    assert [r["name"] for r in s.downloads.list("task:w1")] == ["a.zip"]
    assert sent == [("Browser.cancelDownload", {"guid": "g-22222222"})]


def lib(tmp_path):
    home = Home(tmp_path / "home")
    return home, Deliverables(home)


async def test_saving_a_download_copies_it_into_the_library(tmp_path):
    home, files = lib(tmp_path)
    page = CheckPage(probe())
    s = session_for(page)
    extras = BrowserExtras(s, home=home, files=files, download_dir=str(tmp_path / "dl"))
    (tmp_path / "dl").mkdir()
    s.downloads.begin("owner", {"guid": "g-33333333", "url": "https://bank.example/statement", "suggestedFilename": "statement.pdf"})
    s.downloads.progress({"guid": "g-33333333", "state": "completed", "receivedBytes": 9, "totalBytes": 9})
    (tmp_path / "dl" / "g-33333333").write_bytes(b"%PDF-1.7\n")
    tools = {t.name: t for t in extras.tools()}
    assert "statement.pdf" in await tools["browser_downloads"].handler({})
    out = await tools["browser_save_download"].handler({"name": "statement.pdf", "save_as": "11월 명세서.pdf"})
    assert "files/11월 명세서.pdf" in out and (home.root / "files" / "11월 명세서.pdf").read_bytes() == b"%PDF-1.7\n"
    assert (await tools["browser_save_download"].handler({"name": "statement.pdf", "save_as": "run.sh"})).startswith("error:")
    assert (await tools["browser_save_download"].handler({"name": "nope.pdf"})).startswith("error: no download named")


# ------------------------------------------------------------------------------------------------ uploads
class Loc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    @property
    def first(self):
        return self

    async def set_input_files(self, files, timeout=None):
        self.page.set_files.append((self.sel, [(f["name"], f["mimeType"], f["buffer"]) for f in files]))

    async def fill(self, text, timeout=None):
        self.page.filled.append((self.sel, text))

    async def press(self, key, timeout=None):
        self.page.pressed.append((self.sel, key))


class FormPage(CheckPage):
    def __init__(self, url="https://forms.example.org/apply", boxes=1):
        super().__init__(probe(), url)
        self.set_files, self.filled, self.pressed, self.boxes = [], [], [], boxes
        self.body = "Thanks"

    def locator(self, sel):
        return Loc(self, sel)

    async def wait_for_load_state(self, *a, **k):
        return None

    async def evaluate(self, js, arg=None):
        if "kelo_check" in js:
            return self.probe_value
        if "data-kelo-otp" in js and "boxes" in js:
            return {"boxes": self.boxes}
        if "data-kelo-otp" in js:
            return None
        return self.body


async def test_an_upload_names_the_files_and_the_site_and_always_asks(tmp_path):
    home, files = lib(tmp_path)
    up = home.save_upload("이력서.pdf", b"%PDF-1.4 cv")
    files.save("portfolio.png", b"\x89PNG....")
    page = FormPage()
    s = session_for(page)
    t = {x.name: x for x in BrowserExtras(s, home=home, files=files).tools()}["browser_upload"]
    assert t.requires_approval and t.auto_approve is None
    assert rule_kind("browser_upload") == "" and not schedule_rule_ok("browser_upload")      # never remembered, not even per schedule
    assert t.validate({"selector": "#cv", "files": ["notes/secret.md"]}) is not None
    assert t.validate({"selector": "#cv", "files": ["../../etc/passwd"]}) is not None
    assert t.validate({"selector": "#cv", "files": []}) is not None
    args = {"selector": "#cv", "files": [up, "files/portfolio.png"]}
    assert t.validate(args) is None
    await s.page()
    card = await t.describe(args)
    assert "forms.example.org" in card and "이력서.pdf" in card and "portfolio.png" in card
    out = await t.handler(args)
    assert "Thanks" in out
    assert page.set_files == [("#cv", [("이력서.pdf", "application/pdf", b"%PDF-1.4 cv"), ("portfolio.png", "image/png", b"\x89PNG....")])]


async def test_an_upload_to_a_photo_button_goes_through_the_file_picker_or_the_hidden_input(tmp_path):
    """Threads and X hide the file input behind a photo button: the files go to the picker the click opens, or to the hidden input
    (owner 2026-11-14: the post waited two minutes while the agent kept failing on the button)."""
    home, files = lib(tmp_path)
    files.save("hero.mp4", b"\x00\x00\x00\x18ftypmp42")

    class Chooser:
        def __init__(self, page):
            self.page = page

        async def set_files(self, payload):
            self.page.chosen.append([f["name"] for f in payload])

    class Info:
        def __init__(self, page):
            self.page = page

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        @property
        def value(self):
            async def get():
                return Chooser(self.page)
            return get()

    class Button(Loc):
        async def set_input_files(self, files, timeout=None):
            raise RuntimeError("Element is not an <input>")

        async def click(self, timeout=None):
            self.page.clicked.append(self.sel)

    class SocialPage(FormPage):
        def __init__(self, picker=True):
            super().__init__("https://www.threads.com/")
            self.chosen, self.clicked, self.picker = [], [], picker

        def locator(self, sel):
            return Loc(self, sel) if sel == "input[type=file]" else Button(self, sel)

        def expect_file_chooser(self, timeout=None):
            if not self.picker:
                raise TimeoutError("no file chooser")
            return Info(self)

    page = SocialPage()
    s = session_for(page)
    t = {x.name: x for x in BrowserExtras(s, home=home, files=files).tools()}["browser_upload"]
    await s.page()
    await t.handler({"selector": "#photo", "files": ["files/hero.mp4"]})
    assert page.clicked == ["#photo"] and page.chosen == [["hero.mp4"]]

    class OneInput(Loc):
        async def count(self):
            return 1

        def nth(self, i):
            return self

        async def get_attribute(self, name):
            return "image/*,video/*"

    page2 = SocialPage(picker=False)
    page2.locator = lambda sel: OneInput(page2, sel) if sel == "input[type=file]" else Button(page2, sel)
    s2 = session_for(page2)
    t2 = {x.name: x for x in BrowserExtras(s2, home=home, files=files).tools()}["browser_upload"]
    await s2.page()
    await t2.handler({"selector": "#photo", "files": ["files/hero.mp4"]})
    assert page2.set_files == [("input[type=file]", [("hero.mp4", "video/mp4", b"\x00\x00\x00\x18ftypmp42")])]


# ------------------------------------------------------------------------------------------------ PDF
async def test_the_page_is_saved_as_a_pdf_into_the_library(tmp_path):
    home, files = lib(tmp_path)

    class Cdp:
        async def send(self, m, p=None):
            assert m == "Page.printToPDF" and p["printBackground"]
            return {"data": base64.b64encode(b"%PDF-1.7 page").decode()}

        async def detach(self):
            pass

    async def cdp(_page):
        return Cdp()

    page = CheckPage(probe(), "https://news.example.com/a")
    s = session_for(page, cdp=cdp)
    t = {x.name: x for x in BrowserExtras(s, home=home, files=files).tools()}["browser_save_pdf"]
    out = await t.handler({"name": "기사"})
    assert "files/기사.pdf" in out and (home.root / "files" / "기사.pdf").read_bytes() == b"%PDF-1.7 page"
    page.url = "about:blank"
    assert (await t.handler({})).startswith("error:")


# ------------------------------------------------------------------------------------------------ private tabs and site state
class Ctx:
    def __init__(self, cookies=None):
        self._cookies, self.added, self.scripts, self.closed, self.pages = list(cookies or []), [], [], False, []

    async def cookies(self):
        return list(self._cookies)

    async def add_cookies(self, cookies):
        self.added += cookies

    async def add_init_script(self, script=None):
        self.scripts.append(script)

    async def new_page(self):
        p = CheckPage(probe(), "about:blank")
        p.context = self
        self.pages.append(p)
        return p

    async def close(self):
        self.closed = True


def test_site_names_and_cookie_matching():
    assert site_of("https://www.Example.com/login") == "www.example.com" and site_of("example.co.kr") == "example.co.kr"
    for bad in ("", "localhost", "10.0.0.1", "javascript:alert(1)"):
        with pytest.raises(Exception):
            site_of(bad)
    assert cookie_for(".example.com", "www.example.com") and cookie_for("accounts.example.com", "example.com")
    assert not cookie_for("example.org", "example.com") and not cookie_for("badexample.com", "example.com")


async def test_private_browsing_gets_its_own_context_carries_one_sign_in_and_keeps_nothing():
    main = Ctx([{"name": "sid", "value": "s3cret", "domain": ".shop.example.com", "path": "/", "expires": -1, "httpOnly": True,
                 "secure": True, "sameSite": "Lax"}, {"name": "other", "value": "x", "domain": "news.example.org", "path": "/"}])
    normal = CheckPage(probe(), "https://shop.example.com/")
    normal.context = main
    made = []

    async def new_context():
        made.append(Ctx())
        return made[-1]

    s = session_for(normal, new_context=new_context)
    s.prepare_pages = False
    t = {x.name: x for x in BrowserExtras(s).tools()}
    out = await t["browser_private"].handler({"on": True, "use_login_of": "shop.example.com"})
    assert "privately" in out and "1 cookies" in out and "s3cret" not in out
    assert s.is_private() and made[0].added == [main._cookies[0]] and (await s.page()).context is made[0]
    off = await t["browser_private"].handler({"on": False})
    assert "normal browser profile" in off and made[0].closed and not s.is_private()


async def test_a_sign_in_state_saved_in_one_tab_loads_into_another():
    main = Ctx([{"name": "sid", "value": "v", "domain": "app.example.com", "path": "/"}])
    page = CheckPage(probe(), "https://app.example.com/home")
    page.context = main

    async def evaluate(js, arg=None):
        return [["token", "abc"]] if "localStorage" in js else (probe() if "kelo_check" in js else "x")

    page.evaluate = evaluate
    s = session_for(page)
    t = {x.name: x for x in BrowserExtras(s).tools()}["browser_site_state"]
    assert "1 cookies, 1 storage items" in await t.handler({"action": "save", "site": "app.example.com"})
    assert "app.example.com" in await t.handler({"action": "list"})
    other = Ctx()
    token = browser_owner.set("task:w9")
    try:
        s._pages["task:w9"] = await other.new_page()
        assert "Loaded" in await t.handler({"action": "load", "site": "app.example.com"})
    finally:
        browser_owner.reset(token)
    assert other.added[0]["name"] == "sid" and '"https://app.example.com"' in other.scripts[0] and "getItem(k) === null" in other.scripts[0]
    assert "Forgotten" in await t.handler({"action": "forget", "site": "app.example.com"})
    assert (await t.handler({"action": "load", "site": "app.example.com"})).startswith("error:")


# ------------------------------------------------------------------------------------------------ time zone and language per tab
async def test_each_new_tab_gets_the_users_time_zone_and_language():
    sent = []

    class Cdp:
        async def send(self, m, p=None):
            sent.append((m, p))
            return {"targetInfo": {"targetId": "T1", "browserContextId": "C1"}} if m == "Target.getTargetInfo" else {}

    async def cdp(_page):
        return Cdp()

    s = session_for(CheckPage(probe()), cdp=cdp)
    s.prepare_pages = True
    BrowserExtras(s, timezone=lambda: "Asia/Seoul", locale="ko-KR")
    s.downloads = None
    await s.page()
    assert ("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Seoul"}) in sent and ("Emulation.setLocaleOverride", {"locale": "ko-KR"}) in sent
    assert s._targets["owner"] == ("T1", "C1")


# ------------------------------------------------------------------------------------------------ action archive and its API
def test_the_archive_keeps_a_capped_manifest_and_forgets_after_14_days(tmp_path):
    now = [1_000_000.0]
    a = ActionArchive(tmp_path, max_frames=3, max_bytes=10_000_000, clock=lambda: now[0])
    for i in range(5):
        a.record("task:w1", b"\xff\xd8jpeg" + bytes([i]), url=f"https://x.example/{i}", title=f"t{i}", action="browser_open", detail="d",
                 task_id=7, width=800, height=500)
    assert a.record("owner", b"x", url="", title="", action="browser_open") is None              # a chat tab is not archived
    h = a.history("task:w1", limit=2)
    assert [i["seq"] for i in h["items"]] == [5, 4] and h["total"] == 3 and h["next_before"] == 4
    assert h["items"][0]["frame"] == "/api/browser/sessions/task:w1/history/5" and h["items"][0]["task_id"] == 7
    assert [i["seq"] for i in a.history("task:w1", before=4)["items"]] == [3]
    assert a.frame("task:w1", 1) is None and a.frame("task:w1", 5).startswith(b"\xff\xd8")     # the oldest went with the cap
    small = ActionArchive(tmp_path / "b", max_bytes=25, clock=lambda: now[0])
    for i in range(4):
        small.record("schedule:3", b"0123456789", url="u", title="t", action="browser_click")
    assert small.history("schedule:3")["total"] == 2                                             # the byte cap
    now[0] += 15 * 86400
    assert a.history("task:w1")["items"] == [] and a.purge() >= 1 and not (tmp_path / "task_w1").exists()


async def test_history_api_pages_and_serves_frames(tmp_path):
    a = ActionArchive(tmp_path)
    pic = jpeg()
    a.record("task:wabc", pic, url="https://x.example/", title="X", action="browser_open", detail="https://x.example/", width=800, height=500)
    app = FastAPI()
    history_routes(app, a, lambda: None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/browser/sessions/task:wabc/history", params={"limit": 10})
        assert r.status_code == 200 and r.json()["items"][0]["detail"] == "https://x.example/"
        f = await c.get(r.json()["items"][0]["frame"])
        assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg" and f.content == pic
        assert (await c.get("/api/browser/sessions/task:wabc/history/99")).status_code == 404
        assert (await c.get("/api/browser/sessions/owner/history")).json()["items"] == []
    off = FastAPI()
    history_routes(off, None, lambda: None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off), base_url="http://t") as c:
        assert (await c.get("/api/browser/sessions/task:w1/history")).status_code == 404


async def test_unattended_steps_are_archived_without_typed_text(tmp_path):
    from assistant.agent import Tool

    page = CheckPage(probe(), "https://shop.example.com/cart")

    async def shot(*a, **k):
        return b"\xff\xd8small", 800, 500

    s = session_for(page)
    s.screenshot = shot
    archive = ActionArchive(tmp_path)
    extras = BrowserExtras(s, archive=archive)

    async def typed(args):
        return "ok"

    tools = [Tool("browser_type", "", {}, typed), Tool("email_list", "", {}, typed)]
    extras.wrap(tools)
    extras.wrap(tools)                                                 # wrapping twice does not archive twice
    token = browser_owner.set("task:w5")
    try:
        s._pages["task:w5"] = page
        await tools[0].handler({"selector": "#q", "text": "my secret words"})
        await tools[1].handler({})
    finally:
        browser_owner.reset(token)
    rows = archive.history("task:w5")["items"]
    assert len(rows) == 1 and rows[0]["action"] == "browser_type" and rows[0]["detail"] == "#q" and "secret" not in str(rows)
    assert step_detail("find_login_code", {"site": "x"}) == ""


# ------------------------------------------------------------------------------------------------ takeover journal
class JCdp:
    def __init__(self):
        self.sent, self.handlers, self.detached = [], {}, False

    def on(self, ev, fn):
        self.handlers[ev] = fn

    async def send(self, m, p=None):
        self.sent.append((m, p))
        if m == "Page.addScriptToEvaluateOnNewDocument":
            return {"identifier": "7"}
        if m == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "F"}}}
        if m == "Page.createIsolatedWorld":
            return {"executionContextId": 3}
        return {}

    async def detach(self):
        self.detached = True

    def fire(self, ev, payload):
        self.handlers[ev](payload)


async def test_what_the_user_does_by_hand_reaches_the_agent_without_private_values():
    cdp = JCdp()

    async def cdp_for(_page):
        return cdp

    page = CheckPage(probe(), "https://shop.example.com/login")
    s = session_for(page, cdp=cdp_for)
    BrowserExtras(s)
    await s.page()
    s.take_control("owner", "dev1")
    await asyncio.sleep(0)
    world = [p for m, p in cdp.sent if m == "Runtime.addBinding"][0]
    assert world == {"name": BINDING, "executionContextName": "kelo-journal"}                    # the page's own scripts cannot call it
    assert [p for m, p in cdp.sent if m == "Page.addScriptToEvaluateOnNewDocument"][0]["worldName"] == "kelo-journal"
    import json
    say = lambda o: cdp.fire("Runtime.bindingCalled", {"name": BINDING, "payload": json.dumps(o)})  # noqa: E731
    say({"k": "field", "label": "이메일", "secret": False, "value": "kim@example.com"})
    say({"k": "field", "label": "비밀번호", "secret": True, "value": None})
    say({"k": "field", "label": "메모", "secret": False, "value": "4111 1111 1111 1111"})
    say({"k": "click", "label": "로그인", "href": ""})
    cdp.fire("Runtime.bindingCalled", {"name": "somethingElse", "payload": json.dumps({"k": "click", "label": "fake"})})
    cdp.fire("Page.frameNavigated", {"frame": {"id": "F", "url": "https://shop.example.com/account"}})
    cdp.fire("Page.frameNavigated", {"frame": {"id": "C", "parentId": "F", "url": "https://ads.example.net/"}})
    await s.release_control("owner")
    await asyncio.sleep(0)
    out = await s.snapshot()
    assert "[While the user controlled this tab" in out and "set '이메일' to 'kim@example.com'" in out and "clicked '로그인'" in out
    assert "filled '비밀번호' (a private field: value not recorded)" in out and "4111" not in out and "fake" not in out
    assert "went to https://shop.example.com/account" in out and "ads.example.net" not in out
    assert cdp.detached and ("Page.removeScriptToEvaluateOnNewDocument", {"identifier": "7"}) in cdp.sent
    assert "While the user" not in await s.snapshot()                                            # told once


def test_a_takeover_with_nothing_done_says_nothing_and_long_ones_are_shortened():
    assert summarize([], 30) == ""
    many = [{"k": "click", "label": f"b{i}"} for i in range(40)]
    text = summarize(many, 200)
    assert text.count("\n- ") <= 15 and "more steps" in text and "about 3 min" in text


# ------------------------------------------------------------------------------------------------ one-time codes from mail
@pytest.mark.parametrize("text,code", [
    ("Your GitHub launch code\n\nVerification code: 482913\n\nIf you didn't try to sign in...", "482913"),
    ("[네이버] 인증번호 [123456]를 입력해 주세요.", "123456"),
    ("Here is your one-time code\n\n  730 194  \n\nIt expires in 10 minutes.", "730194"),
    ("Use G-583920 to verify your account", "G-583920"),
    ("Your sign in code is AB12CD", "AB12CD"),
    ("Thanks for your order of 2024 items totaling $1299 on 12/05/2024. Call 010-1234-5678.", None),
    ("We received your payment of 15000원. Order 2025", None),
])
def test_codes_are_pulled_out_of_mail_text(text, code):
    assert extract(text) == code


def test_a_code_is_taken_only_from_mail_of_that_site():
    msgs = [{"from": "Shop News <news@othershop.example>", "subject": "Your code", "text": "code: 111111", "at": 3},
            {"from": "Example Accounts <no-reply@accounts.example.com>", "subject": "Sign-in code", "text": "Your code is 222333", "at": 2},
            {"from": "Example <no-reply@example.com>", "subject": "older", "text": "Your code is 999999", "at": 1}]
    got = find_code(msgs, "www.example.com")
    assert got is not None and got.code == "222333" and "accounts.example.com" in got.sender
    assert find_code(msgs, "github.com") is None
    assert site_words("https://login.example.co.kr/x") == ["example"] and from_site({"from": "GitHub <noreply@github.com>"}, "github.com")


class FakeMail:
    def __init__(self, msgs, accounts=True):
        self.msgs, self._acc, self.asked = msgs, accounts, 0

    def accounts(self):
        return [{"name": "mail-gmail"}] if self._acc else []

    async def recent(self, minutes=15, limit=12, tool="find_login_code"):
        self.asked += 1
        return self.msgs


async def run_code_tool(t, args, approve=True):
    """What the agent loop does with a tool call: hold, describe, card extras, then the handler on Allow."""
    token = current_call.set("call-1")
    try:
        held = await t.hold(args)
        if held:
            return held, None, None
        card = await t.describe(args)
        extra = t.approval_extra("ap1", args)
        out = await t.handler(args) if approve else t.declined
        return out, card, extra
    finally:
        current_call.reset(token)


async def test_find_login_code_shows_the_code_only_to_the_user_and_types_it_after_allow():
    mail = FakeMail([{"from": "Example <no-reply@example.com>", "subject": "Your code", "text": "Verification code: 482913", "at": time.time()}])
    page = FormPage("https://www.example.com/verify")
    page.body = "Enter code 482913? Welcome back"
    s = session_for(page)
    s.settle_seconds = 0
    s.sign_in_wait = 0
    t = {x.name: x for x in BrowserExtras(s, mail=mail).tools()}["find_login_code"]
    assert t.requires_approval and t.auto_approve is None and rule_kind("find_login_code") == "" and not schedule_rule_ok("find_login_code")
    await s.page()
    tok = i18n.use("ko")                                    # the approval card is in the user's language
    try:
        out, card, extra = await run_code_tool(t, {})
    finally:
        i18n.reset(tok)
    assert card == f"이 코드를 입력할까요? {code_mask('482913')} — 보낸 곳 Example <no-reply@example.com>" and "482913" not in card
    assert extra["detail"] == "이 코드를 입력할까요? 482913 — 보낸 곳 Example <no-reply@example.com>" and extra["login_code"]["code"] == "482913"
    assert "Found the code (6 digits)" in out and "482913" not in out and "[code hidden]" in out
    assert page.filled == [('[data-kelo-otp="0"]', "482913")] and page.pressed
    assert t.approval_extra("ap1", {}) == {}                                                     # nothing is kept after it was typed


async def test_find_login_code_split_boxes_decline_and_nothing_found():
    mail = FakeMail([{"from": "Example <no-reply@example.com>", "subject": "Code", "text": "Your code is 4821", "at": time.time()}])
    page = FormPage("https://example.com/verify", boxes=4)
    s = session_for(page)
    s.settle_seconds = 0
    s.sign_in_wait = 0
    extras = BrowserExtras(s, mail=mail)
    t = {x.name: x for x in extras.tools()}["find_login_code"]
    await s.page()
    out, _, _ = await run_code_tool(t, {"site": "example.com"})
    assert [v for _, v in page.filled] == ["4", "8", "2", "1"] and "4821" not in out
    page.filled.clear()
    out, _, _ = await run_code_tool(t, {"site": "example.com"}, approve=False)
    assert "did not want" in out and page.filled == []
    out, card, _ = await run_code_tool(t, {"site": "github.com"})
    assert out.startswith("No code found") and card is None
    nobox = BrowserExtras(session_for(FormPage()), mail=FakeMail([], accounts=False)).tools()
    out, _, _ = await run_code_tool({x.name: x for x in nobox}["find_login_code"], {"site": "example.com"})
    assert "No mailbox is connected" in out and "Never ask for the code in chat" in out


# ------------------------------------------------------------------------------------------------ the egress proxy (LAN block)
def resolver_of(mapping):
    def resolve(host, port, *a, **k):
        if host not in mapping:
            raise OSError("nxdomain")
        return [(2, 1, 6, "", (ip, port)) for ip in mapping[host]]
    return resolve


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.1", "172.17.0.2", "127.0.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
                                  "::1", "fd00::1", "fe80::1", "::ffff:192.168.0.1", "224.0.0.1", "lan.example", "mixed.example", "server",
                                  "update.googleapis.com", "x.clients2.google.com"])
async def test_the_egress_proxy_refuses_every_private_target(host):
    e = Egress(resolver=resolver_of({"lan.example": ["192.168.1.10"], "mixed.example": ["93.184.216.34", "10.1.1.1"],
                                     "server": ["172.20.0.5"], "update.googleapis.com": ["142.250.1.1"], "x.clients2.google.com": ["142.250.1.2"]}))
    with pytest.raises(Denied):
        await e.pick(host, 443)


async def test_the_egress_proxy_allows_public_addresses_and_connects_to_the_checked_one():
    e = Egress(resolver=resolver_of({"example.com": ["93.184.216.34"]}))
    assert await e.pick("example.com", 443) == "93.184.216.34" and await e.pick("1.1.1.1", 443) == "1.1.1.1"
    assert split_target("[2606:4700::1111]:443", 443) == ("2606:4700::1111", 443) and split_target("Example.com.", 80) == ("example.com", 80)


async def test_the_egress_proxy_answers_403_for_a_lan_connect_and_tunnels_a_public_one():
    async def upstream(reader, writer):   # stands in for a public site (reached on loopback only because this test allows it)
        data = await reader.read(100)
        writer.write(b"echo:" + data)
        await writer.drain()
        writer.close()

    up = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]
    e = Egress(resolver=resolver_of({"site.example": ["127.0.0.1"], "lan.example": ["192.168.0.10"]}),
               allow_ip=lambda ip: ip == "127.0.0.1")
    proxy = await asyncio.start_server(e.handle, "127.0.0.1", 0)
    port = proxy.sockets[0].getsockname()[1]
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT lan.example:443 HTTP/1.1\r\nHost: lan.example:443\r\n\r\n")
        await w.drain()
        answer = await r.read(500)
        assert answer.startswith(b"HTTP/1.1 403") and b"X-Kelo-Egress: blocked" in answer and b"private" in answer
        w.close()
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"GET http://lan.example/admin HTTP/1.1\r\nHost: lan.example\r\n\r\n")
        await w.drain()
        assert await r.read(500) == b""                                  # plain http to the LAN: closed, no answer a page could read
        w.close()
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"CONNECT site.example:{up_port} HTTP/1.1\r\n\r\n".encode())
        await w.drain()
        assert (await r.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        w.write(b"hello")
        await w.drain()
        assert await r.read(100) == b"echo:hello"
        w.close()
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET http://site.example:{up_port}/x HTTP/1.1\r\nHost: site.example\r\nProxy-Connection: keep-alive\r\n\r\n".encode())
        await w.drain()
        got = await r.read(500)
        assert got.startswith(b"echo:GET /x HTTP/1.1") and b"Proxy-Connection" not in got
        w.close()
    finally:
        proxy.close()
        up.close()
    assert e.refused == 2


async def test_recent_mail_is_read_without_marking_it_and_only_from_the_last_minutes(tmp_path):
    from email.message import EmailMessage
    from email.utils import formatdate

    from assistant.mail import Mail
    from assistant.store import Store
    from test_mail import ADDRESS, PASSWORD, FakeMailServer

    fake = FakeMailServer()
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = "Example <no-reply@example.com>", ADDRESS, "Your sign-in code", formatdate(time.time() - 120)
    m.set_content("Your verification code is 552901. It expires in 10 minutes.")
    fake.box["2"] = m.as_bytes()
    mail = Mail(Store(tmp_path / "s.db"), imap=fake.imap, smtp=fake.smtp)
    await mail.connect("gmail", ADDRESS, PASSWORD)
    got = await mail.recent(15)
    assert [x["subject"] for x in got] == ["Your sign-in code"]                       # yesterday's message is left out
    assert fake.searches[-1][0] == "SINCE" and find_code(got, "example.com").code == "552901"


@pytest.mark.parametrize("text,code", [("Your verification code is 552901. It expires soon.", "552901"), ("Version 1.552901 code", None)])
def test_a_sentence_ending_right_after_the_code_still_counts(text, code):
    assert extract(text) == code
