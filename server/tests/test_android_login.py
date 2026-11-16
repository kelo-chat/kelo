"""android_login (tools/vault_tools.py) and android/signin.py: the saved login typed into an app's sign-in screen on the Android device,
through a fake bridge that moves from one saved dump to the next. Fields found, the two-page Google flow, success / failed /
code_needed, the values never in a result or a log, a login for another app refused, the approval rules and the held login."""

import asyncio
import functools
import json
import logging
import pathlib
import re

import httpx
import pytest
from cryptography.fernet import Fernet

from assistant.android.device import DeviceClient
from assistant.android.screen import ScreenReader
from assistant.android.signin import SignInReader, find_fields, find_submit, masked
from assistant.android.tools import AndroidSession
from assistant.store import Store
from assistant.tools import vault_tools as vt
from assistant.tools.vault_tools import login_site_ok, site_host, vault_tools
from assistant.vault import Transient

FIX = pathlib.Path(__file__).parent / "fixtures" / "android"
TOKEN = "t" * 40
KEY = Fernet.generate_key().decode()
USER, PASSWORD = "kelo.test@example.com", "Hunter2-correct-horse"
GMS = "com.google.android.gms/com.google.android.gms.auth.uiflows.minutemaid.MinuteMaidActivity"
PLAY = "com.android.vending/com.android.vending.AssetBrowserActivity"
SHOP_SIGNIN = "com.example.shop/com.example.shop.LoginActivity"
SHOP_HOME = "com.example.shop/com.example.shop.HomeActivity"
APPS = {"google_email": GMS, "google_password": GMS, "google_password_wrong": GMS, "google_check_phone": GMS, "play_signed_in": PLAY,
        "app_signin_form": SHOP_SIGNIN, "app_signed_in": SHOP_HOME, "settings": "com.android.settings/.Settings"}


def dump(name):
    return (FIX / f"{name}.xml").read_text(encoding="utf-8")


def screen_of(name):
    return ScreenReader().parse(dump(name), APPS[name])


class Bridge:
    """The bridge with a device that moves on: `moves` maps a screen to the one a submit leads to (Enter, or a tap on its button)."""

    def __init__(self, screen, moves=None, enter_moves=True):
        self.screen, self.moves, self.enter_moves, self.log = screen, dict(moves or {}), enter_moves, []

    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.log.append((path, body))
        if path == "/screen":
            return httpx.Response(200, json={"xml": dump(self.screen), "png": None, "app": APPS[self.screen]})
        if path == "/key" and body.get("key") == "enter" and self.enter_moves:
            self.screen = self.moves.get(self.screen, self.screen)
        if path == "/tap" and not self.enter_moves:
            button = find_submit(screen_of(self.screen))
            if button is not None and button.contains(body["x"], body["y"]):
                self.screen = self.moves.get(self.screen, self.screen)
        return httpx.Response(200, json={"ok": True})

    def typed(self):
        return [b["text"] for p, b in self.log if p == "/text"]

    def paths(self):
        return [p for p, _ in self.log]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    for name in ("ANDROID_TYPE_PAUSE", "ANDROID_SUBMIT_WAIT", "ANDROID_SETTLE", "ANDROID_POLL"):
        monkeypatch.setattr(vt, name, 0.0)
    monkeypatch.setattr(vt, "ANDROID_SIGN_IN_WAIT", 0.05)
    monkeypatch.setattr(vt, "CONFIRM_DELAYS", (0.0, 0.0))


def setup(tmp_path, bridge, hosts=("app:com.example.shop",), name="shop", entered=True, **cred):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = Store(tmp_path / "a.db", KEY)
    store.save_credential(name, "Shop", list(hosts), {}, kind="login")
    if entered:
        store.set_login(name, USER, PASSWORD)
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(bridge)))
    session = AndroidSession(client, ScreenReader())
    transient = Transient()
    tools = {t.name: t for t in vault_tools(store, None, transient, android=session)}
    return store, transient, tools, session


def call(tools, **args):
    return asyncio.run(tools["android_login"].handler(args))


# ------------------------------------------------------------------------------------------------ fields (pure, on dumps)
def test_the_google_email_page_has_a_user_field_and_no_password_field():
    user, password = find_fields(screen_of("google_email"))
    assert user is not None and user.hint == "Email or phone" and user.focused and password is None


def test_the_google_password_page_has_a_password_field_and_no_user_field():
    user, password = find_fields(screen_of("google_password"))
    assert password is not None and password.password and user is None


def test_a_form_with_both_fields_gives_the_edit_above_the_password_as_the_user_field():
    user, password = find_fields(screen_of("app_signin_form"))
    assert user.hint == "Email" and password.password and user.bounds[3] <= password.bounds[1]


def test_a_screen_with_no_fields_finds_none():
    assert find_fields(screen_of("settings")) == (None, None)


def test_play_with_no_window_gives_no_screen_and_no_fields():
    """Live, 2026-11-14: com.android.vending/.AssetBrowserActivity on the owner's redroid is resumed but has no window surface, so
    `uiautomator dump` answers "ERROR: null root node returned by UiTestAutomationBridge" and writes nothing (no fixture to save).
    What the bridge makes of that is an error (test_android_bridge); a dump that is only the root finds nothing, without errors."""
    empty = ScreenReader().parse('<?xml version="1.0"?><hierarchy rotation="0"><node bounds="[0,0][720,1280]" package="com.android.vending"/></hierarchy>',
                                 "com.android.vending/.AssetBrowserActivity")
    assert find_fields(empty) == (None, None) and find_submit(empty) is None
    assert SignInReader().read(screen_of("google_password"), empty)[0] == "unknown"
    assert SignInReader().read(empty, empty)[0] == "unknown"
    assert not SignInReader.left_sign_in(empty, GMS)


def test_the_submit_button_is_the_one_named_next_or_sign_in():
    assert find_submit(screen_of("google_email")).words == "Next"
    assert find_submit(screen_of("app_signin_form")).words == "Sign in"
    assert find_submit(screen_of("settings")) is None


def test_what_is_typed_in_a_field_is_replaced_in_the_screen_the_model_reads():
    xml = re.sub(r'text="" (resource-id="identifierId")', rf'text="{USER}" \1', dump("google_email"))
    screen = ScreenReader().parse(xml, GMS)
    assert USER in " ".join(i.text for i in screen.items)
    out = masked(screen, (USER, PASSWORD))
    assert USER not in out and "(filled)" in out
    assert "Email or phone" in masked(screen_of("google_email"))      # an empty field keeps its name


@pytest.mark.parametrize("after,want", [("google_password_wrong", "failed"), ("google_check_phone", "code_needed"),
                                        ("play_signed_in", "success"), ("google_password", "unknown")])
def test_the_screen_after_a_submit_is_read(after, want):
    assert SignInReader().read(screen_of("google_password"), screen_of(after))[0] == want


def test_a_check_your_phone_screen_is_an_approval_not_a_code():
    assert SignInReader().step(screen_of("google_check_phone")) == "approve"
    assert SignInReader().step(screen_of("google_password")) == ""


# ------------------------------------------------------------------------------------------------ app hosts
def test_an_app_host_is_accepted_only_in_its_package_form():
    assert site_host("app:com.example.shop") == "app:com.example.shop" and site_host("APP:com.Example.shop") == "app:com.example.shop"
    for bad in ("app:", "app:shop", "app:com.example.shop/x", "app:Com..x", "app:1com.x"):
        with pytest.raises(Exception):
            site_host(bad)


def test_a_login_matches_the_app_it_names_and_the_service_whose_sign_in_the_app_shows():
    assert login_site_ok("app:com.example.shop", ["app:com.example.shop"])
    assert not login_site_ok("app:com.example.other", ["app:com.example.shop"])
    assert login_site_ok("app:com.google.android.gms", ["accounts.google.com"])      # Google's sign-in inside Play services
    assert login_site_ok("app:com.android.vending", ["google.com"])
    assert not login_site_ok("app:com.android.vending", ["example.com"])
    assert not login_site_ok("app:com.example.shop", ["accounts.google.com"])        # an ordinary app is not Google
    assert not login_site_ok("accounts.google.com", ["app:com.google.android.gms"])  # and an app login is never typed on a web page


def test_request_login_asks_for_an_app_login_and_names_android_login_next(tmp_path):
    store, _, tools, _ = setup(tmp_path, Bridge("settings"), hosts=(), entered=False)
    store.delete_draft("shop")
    out = asyncio.run(tools["request_login"].handler({"site": "app:com.example.shop", "name": "shop", "label": "Shop"}))
    assert "android_login" in out and "browser_login" not in out


# ------------------------------------------------------------------------------------------------ the tool
def test_both_fields_on_one_screen_are_typed_and_the_sign_in_is_read(tmp_path):
    bridge = Bridge("app_signin_form", {"app_signin_form": "app_signed_in"})
    store, _, tools, _ = setup(tmp_path, bridge)
    out = call(tools, name="shop")
    assert bridge.typed() == [USER, PASSWORD] and "[sign-in result: success" in out
    assert USER not in out and PASSWORD not in out
    assert store.credential("shop")["last_result"] == "ok"
    taps = [b for p, b in bridge.log if p == "/tap"]
    assert taps[0] == {"x": 360, "y": 340} and taps[1] == {"x": 360, "y": 460}      # the user field, then the password field


def test_googles_two_pages_are_typed_one_step_at_a_time(tmp_path):
    bridge = Bridge("google_email", {"google_email": "google_password", "google_password": "play_signed_in"})
    store, _, tools, _ = setup(tmp_path, bridge, hosts=("accounts.google.com",), name="google")
    first = call(tools, name="google", step="username")
    assert bridge.typed() == [USER] and "[sign-in result: unknown" in first and "Welcome" in first   # the password page is now shown
    assert store.credential("google")["last_result"] is None                                           # an e-mail step says nothing about the password
    second = call(tools, name="google", step="password")
    assert bridge.typed() == [USER, PASSWORD] and "[sign-in result: success" in second
    assert store.credential("google")["last_result"] == "ok"


def test_a_wrong_password_is_failed_and_the_login_is_not_tried_again(tmp_path):
    bridge = Bridge("google_password", {"google_password": "google_password_wrong"})
    store, _, tools, _ = setup(tmp_path, bridge, hosts=("accounts.google.com",), name="google")
    out = call(tools, name="google", step="password")
    assert "[sign-in result: failed" in out and "will not be tried again" in out and PASSWORD not in out
    bridge.screen, bridge.log = "google_password", []
    again = call(tools, name="google", step="password")
    assert again.startswith("error:") and "rejected" in again and bridge.typed() == []


def test_check_your_phone_is_a_pending_verification_and_points_to_android_show(tmp_path):
    bridge = Bridge("google_password", {"google_password": "google_check_phone"})
    _, _, tools, _ = setup(tmp_path, bridge, hosts=("accounts.google.com",), name="google")
    out = call(tools, name="google", step="password")
    assert "[sign-in result: verification_pending (approve)" in out and "NOT finished" in out and "android_show" in out


def test_enter_that_moves_nothing_is_followed_by_a_tap_on_next(tmp_path):
    bridge = Bridge("google_email", {"google_email": "google_password"}, enter_moves=False)
    _, _, tools, _ = setup(tmp_path, bridge, hosts=("accounts.google.com",), name="google")
    out = call(tools, name="google", step="username")
    assert "Welcome" in out and ("/key", {"key": "enter"}) in bridge.log
    assert {"x": 606, "y": 1040} in [b for p, b in bridge.log if p == "/tap"]      # the centre of Next


def test_the_values_are_in_no_result_and_no_log(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    bridge = Bridge("app_signin_form", {"app_signin_form": "app_signed_in"})
    store, _, tools, _ = setup(tmp_path, bridge)
    out = call(tools, name="shop")
    logged = " ".join(r["detail"] or "" for r in store.vault_log(50)) + caplog.text
    for text in (out, logged):
        assert USER not in text and PASSWORD not in text and "Hunter2" not in text
    assert "android_login on com.example.shop: success" in logged


def test_a_device_error_that_quotes_the_text_is_cleaned(tmp_path):
    def quoting(request):
        if request.url.path == "/text":
            return httpx.Response(502, json={"error": f"input failed on {PASSWORD}"})
        return Bridge("app_signin_form")(request)

    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(quoting)))
    store = Store(tmp_path / "a.db", KEY)
    store.save_credential("shop", "Shop", ["app:com.example.shop"], {}, kind="login")
    store.set_login("shop", USER, PASSWORD)
    tools = {t.name: t for t in vault_tools(store, None, Transient(), android=AndroidSession(client, ScreenReader()))}
    out = call(tools, name="shop")
    assert out.startswith("error:") and PASSWORD not in out and "[hidden]" in out
    assert PASSWORD not in " ".join(r["detail"] or "" for r in store.vault_log(50))


def test_a_login_for_another_app_is_refused_and_nothing_is_typed(tmp_path):
    bridge = Bridge("app_signin_form")
    store, _, tools, _ = setup(tmp_path, bridge, hosts=("app:com.example.other",))
    out = call(tools, name="shop")
    assert out.startswith("error:") and "com.example.shop" in out and "com.example.other" in out
    assert bridge.typed() == [] and "/tap" not in bridge.paths() and "/key" not in bridge.paths()


def test_a_login_for_a_web_site_is_not_typed_into_an_ordinary_app(tmp_path):
    bridge = Bridge("app_signin_form")
    _, _, tools, _ = setup(tmp_path, bridge, hosts=("accounts.google.com",))
    assert call(tools, name="shop").startswith("error:") and bridge.typed() == []


def test_a_screen_without_the_expected_field_types_nothing(tmp_path):
    bridge = Bridge("settings")
    _, _, tools, _ = setup(tmp_path, bridge, hosts=("app:com.android.settings",))
    out = call(tools, name="shop")
    assert out.startswith("error:") and "no sign-in field" in out and bridge.typed() == []


def test_the_vaults_own_checks_come_first(tmp_path):
    bridge = Bridge("app_signin_form")
    store, _, tools, _ = setup(tmp_path, bridge, entered=False)
    assert "has not entered" in call(tools, name="shop")
    store.set_login("shop", USER, PASSWORD)
    assert call(tools, name="nobody").startswith("error:") and "no website login" in call(tools, name="nobody")
    assert call(tools, name="shop", step="sideways").startswith("error:")
    assert bridge.typed() == []
    store.set_vault_locked(True)
    assert "locked the keychain" in call(tools, name="shop") and bridge.typed() == []


def test_a_held_login_is_saved_once_the_device_shows_it_is_signed_in(tmp_path):
    bridge = Bridge("app_signin_form")      # Enter moves nothing and the screen stays: the result is unknown at first
    store, transient, tools, _ = setup(tmp_path, bridge, entered=False)
    transient.put("shop", USER, PASSWORD, promote=True)

    async def go():
        out = await tools["android_login"].handler({"name": "shop"})
        assert "[sign-in result: unknown" in out and not store.credential("shop")["has_secret"]
        bridge.screen = "app_signed_in"       # the app takes its time, then is signed in
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.credential("shop")["has_secret"]:
                break
    asyncio.run(go())
    assert store.credential("shop")["has_secret"] and store.credential("shop")["last_result"] == "ok"


# ------------------------------------------------------------------------------------------------ approval
def test_the_card_names_the_login_and_the_app_and_the_vaults_rules_decide_whether_it_shows(tmp_path):
    bridge = Bridge("app_signin_form")
    store, transient, tools, _ = setup(tmp_path, bridge)
    tool = tools["android_login"]
    assert tool.requires_approval and tool.untrusted
    assert asyncio.run(tool.describe({"name": "shop"})) == "Type your Shop login into com.example.shop."
    assert not tool.auto_approve({"name": "shop"})                        # asks each time
    store.set_auto_allow("shop", True)                                    # the owner let this login sign in by itself
    assert tool.auto_approve({"name": "shop"})
    store.set_auto_allow("shop", False)
    transient.put("shop", USER, PASSWORD)                                 # typed in for this sign-in: no second card
    assert tool.auto_approve({"name": "shop"})
    assert not tool.auto_approve({"name": "nobody"})
    assert bridge.typed() == []                                           # asking typed nothing
