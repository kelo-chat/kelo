"""An app that signs in through a browser (android/browser_signin.py + android_login): the login saved for the app is typed on the
browser's sign-in page only when the DEVICE says the page belongs to that app. A fake bridge answers the read-only questions."""

import asyncio
import functools
import html
import json
import pathlib

import httpx
import pytest
from cryptography.fernet import Fernet

from assistant.android.browser_signin import address_of, redirect_of, refusal
from assistant.android.device import DeviceClient
from assistant.android.screen import ScreenReader
from assistant.android.tools import AndroidSession
from assistant.store import Store
from assistant.tools import vault_tools as vt
from assistant.tools.vault_tools import vault_tools
from assistant.vault import Transient

FIX = pathlib.Path(__file__).parent / "fixtures" / "android"
TOKEN, KEY = "t" * 40, Fernet.generate_key().decode()
USER, PASSWORD = "kelo.test@example.com", "Hunter2-correct-horse"
APP, OTHER, BROWSER = "com.example.shop", "com.example.other", "org.chromium.webview_shell"
PAGE = "https://login.example.com/login?client_id=1&response_type=code&redirect_uri={r}&state=x"
SCHEME = "com.example.shop.oauth:/oauth2redirect"
ADDRESS = ('<node index="0" text="{url}" resource-id="" class="android.widget.EditText" package="' + BROWSER + '" content-desc="" '
           'checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" '
           'long-clickable="false" password="false" selected="false" bounds="[0,0][720,90]" drawing-order="1" hint="" />')


def browser_dump(url="", package=BROWSER):
    xml = (FIX / "app_signin_form.xml").read_text(encoding="utf-8").replace("com.example.shop", package).replace("com.google.android.gms", package)
    return xml.replace('bounds="[0,96][720,1184]" drawing-order="1" hint=""><', 'bounds="[0,96][720,1184]" drawing-order="1" hint="">'
                       + (ADDRESS.format(url=html.escape(url)) if url else "") + "<", 1)


class Device:
    """The device: what it says about handlers, the launching intent and App Links. Records every request."""

    def __init__(self, *, page=PAGE.format(r=SCHEME), shown="", package=BROWSER, handlers=None, links="default", caller="", intent_error=False):
        self.page, self.shown, self.package, self.caller, self.intent_error = page, shown, package, caller, intent_error
        self.links = {APP: ["login.example.com"]} if links == "default" else links
        self.handlers = {"https": [BROWSER, "com.android.chrome"], SCHEME.split(":")[0]: [APP], **(handlers or {})}
        self.log = []

    def __call__(self, request):
        path, body = request.url.path, json.loads(request.content) if request.content else {}
        self.log.append((request.method, path, dict(request.url.params), body))
        if path == "/screen":
            return httpx.Response(200, json={"xml": browser_dump(self.shown, self.package), "png": None, "app": f"{self.package}/.Main"})
        if path == "/handlers":
            return httpx.Response(200, json={"packages": self.handlers.get(request.url.params["url"].split(":")[0], [])})
        if path == "/intent":
            return httpx.Response(200, json={"error": "no"} if self.intent_error else {"data": self.page, "caller": self.caller})
        if path == "/applinks":
            return httpx.Response(200, json={"hosts": self.links.get(request.url.params["package"], [])})
        return httpx.Response(200, json={"ok": True})

    def typed(self):
        return [b["text"] for _, p, _, b in self.log if p == "/text"]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    for name in ("ANDROID_TYPE_PAUSE", "ANDROID_SUBMIT_WAIT", "ANDROID_SETTLE", "ANDROID_POLL"):
        monkeypatch.setattr(vt, name, 0.0)
    monkeypatch.setattr(vt, "ANDROID_SIGN_IN_WAIT", 0.05)
    monkeypatch.setattr(vt, "CONFIRM_DELAYS", (0.0, 0.0))


def rig(tmp_path, device, hosts=("app:" + APP,)):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = Store(tmp_path / "a.db", KEY)
    store.save_credential("shop", "Shop", list(hosts), {}, kind="login")
    store.set_login("shop", USER, PASSWORD)
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(device)))
    tools = {t.name: t for t in vault_tools(store, None, Transient(), android=AndroidSession(client, ScreenReader()))}
    return store, client, tools


def login(tools):
    return asyncio.run(tools["android_login"].handler({"name": "shop"}))


def screen(device_package=BROWSER, url=""):
    return ScreenReader().parse(browser_dump(url, device_package), f"{device_package}/.Main")


# ---- pure parts
def test_the_address_field_and_the_redirect_are_read_from_structure():
    assert address_of(screen(url="https://login.example.com/x?redirect_uri=a:/b")) == "https://login.example.com/x?redirect_uri=a:/b"
    assert address_of(screen()) == ""
    assert redirect_of(PAGE.format(r=SCHEME.replace(":", "%3A"))) == SCHEME
    assert redirect_of("https://x.example.com/?redirect_url=app:/cb") == "app:/cb"
    assert redirect_of("https://x.example.com/?next=app:/cb") == ""


# ---- android_login on the browser's page
def test_a_browser_page_whose_redirect_comes_back_to_the_bound_app_is_typed_into(tmp_path):
    dev = Device()
    store, _, tools = rig(tmp_path, dev)
    login(tools)
    assert USER in dev.typed() and PASSWORD in dev.typed()
    assert any("app sign-in page" in r["detail"] and BROWSER in r["detail"] and APP in r["detail"] for r in store.vault_log(20))
    assert all(m == "GET" for m, p, *_ in dev.log if p in ("/handlers", "/intent", "/applinks"))   # read-only questions only


def test_the_same_page_with_a_redirect_to_another_app_is_refused(tmp_path):
    dev = Device(handlers={SCHEME.split(":")[0]: [OTHER]})
    store, _, tools = rig(tmp_path, dev)
    assert login(tools).startswith("error:") and dev.typed() == []
    assert any(r["action"] == "denied" for r in store.vault_log(20))


def test_a_scheme_two_apps_claim_is_refused(tmp_path):
    dev = Device(handlers={SCHEME.split(":")[0]: [APP, OTHER]})
    assert login(rig(tmp_path, dev)[2]).startswith("error:") and dev.typed() == []


def test_a_scheme_nobody_claims_is_refused(tmp_path):
    dev = Device(handlers={SCHEME.split(":")[0]: []})
    assert login(rig(tmp_path, dev)[2]).startswith("error:") and dev.typed() == []


def test_a_foreground_that_is_not_a_browser_is_refused(tmp_path):
    dev = Device(package=OTHER)   # same page and redirect, but the app in front does not handle VIEW for https
    assert login(rig(tmp_path, dev)[2]).startswith("error:") and dev.typed() == []


@pytest.mark.parametrize("page", ["http://login.example.com/login?redirect_uri=" + SCHEME, "https://login.example.com/login?code=1", ""])
def test_http_no_redirect_or_no_address_is_refused(tmp_path, page):
    dev = Device(page=page)
    assert login(rig(tmp_path, dev)[2]).startswith("error:") and dev.typed() == []


def test_an_https_redirect_needs_a_verified_app_link_for_the_bound_app(tmp_path):
    page = PAGE.format(r="https://app.example.com/cb")
    ok = Device(page=page, links={APP: ["app.example.com", "login.example.com"]})
    login(rig(tmp_path / "1", ok)[2])
    assert PASSWORD in ok.typed()
    wild = Device(page=page, links={APP: ["*.example.com"]})
    login(rig(tmp_path / "2", wild)[2])
    assert PASSWORD in wild.typed()
    for n, links in enumerate(({APP: ["login.example.com"]}, {OTHER: ["app.example.com"]}, {APP: ["example.org"]}), 3):
        bad = Device(page=page, links=links)
        assert login(rig(tmp_path / str(n), bad)[2]).startswith("error:") and bad.typed() == []


def test_the_address_field_is_used_when_the_intent_is_unknown_and_a_different_host_there_refuses(tmp_path):
    dev = Device(page="", shown=PAGE.format(r=SCHEME))
    login(rig(tmp_path / "1", dev)[2])
    assert PASSWORD in dev.typed()
    other = Device(shown="https://evil.example.net/login?redirect_uri=" + SCHEME)   # the page in front is not the page the app opened
    assert login(rig(tmp_path / "2", other)[2]).startswith("error:") and other.typed() == []


def test_a_device_that_cannot_answer_refuses(tmp_path):
    class Broken(Device):
        def __call__(self, request):
            if request.url.path == "/handlers":
                return httpx.Response(502, json={"error": "adb timed out"})
            return super().__call__(request)

    dev = Broken()
    assert login(rig(tmp_path, dev)[2]).startswith("error:") and dev.typed() == []


def test_the_app_itself_in_front_does_not_ask_the_device_anything_more(tmp_path):
    dev = Device(package=APP)
    login(rig(tmp_path, dev)[2])
    assert PASSWORD in dev.typed() and not [p for _, p, *_ in dev.log if p in ("/handlers", "/intent", "/applinks")]


def test_the_refusal_reasons_name_the_missing_proof():
    dev = Device(handlers={SCHEME.split(":")[0]: [OTHER]})
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(dev)))
    assert "another app" in asyncio.run(refusal(client, screen(), [APP]))
    assert "host" in asyncio.run(refusal(client, screen(), [OTHER]))   # OTHER claims the scheme but has no verified link for the page's host
    assert "no app" in asyncio.run(refusal(client, screen(), []))


# ---- the page's host must belong to the app (the redirect is the page's own parameter, so an attacker's page can carry it)
def test_an_evil_host_with_a_valid_app_redirect_is_refused(tmp_path):
    dev = Device(page=PAGE.format(r=SCHEME).replace("login.example.com", "evil.example.net"))
    store, _, tools = rig(tmp_path, dev)
    assert login(tools).startswith("error:") and dev.typed() == []
    assert any("host" in r["detail"] for r in store.vault_log(20))


def test_a_host_on_the_apps_verified_domain_types_even_as_a_sibling_subdomain(tmp_path):
    dev = Device(page=PAGE.format(r=SCHEME).replace("login.example.com", "accounts.eu.example.com"), links={APP: ["*.example.com"]})
    login(rig(tmp_path, dev)[2])
    assert PASSWORD in dev.typed()


def test_a_caller_that_is_another_package_is_refused(tmp_path):
    dev = Device(caller=OTHER)
    assert login(rig(tmp_path, dev)[2]).startswith("error:") and dev.typed() == []


def test_the_bound_app_as_caller_types_and_an_unknown_caller_leaves_it_to_the_host_rule(tmp_path):
    for n, caller in enumerate((APP, "")):
        dev = Device(caller=caller)
        login(rig(tmp_path / str(n), dev)[2])
        assert PASSWORD in dev.typed()


def test_an_app_with_no_verified_links_is_refused(tmp_path):
    dev = Device(links={})
    assert login(rig(tmp_path, dev)[2]).startswith("error:") and dev.typed() == []
