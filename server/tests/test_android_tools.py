"""The agent's tools for the Android device (android/tools.py) through a fake bridge (httpx MockTransport) and saved dumps: what each
tool sends, the refs, the approval rules of the browser with the app's package as the site, the fence, the ledger (done once), and the
wiring in the app (off without a device, found by find_tools)."""

import dataclasses
import functools
import json
import pathlib

import httpx
import pytest

from assistant import effects
from assistant.agent import Ours, Tool, ToolOutput
from assistant.agent.loop import _result_text
from assistant.agent.toolbox import search
from assistant.android import tools as android
from assistant.android.device import DeviceClient
from assistant.android.screen import ScreenReader
from assistant.android.tools import AndroidSession, android_tools
from assistant.done_once import ActionLedger, DoneOnce
from assistant.providers import Done, TextDelta
from assistant.server import create_app
from assistant.store import Store
from assistant.tools.browser import browser_owner

from test_agent_profile import app_settings
from test_toolbox import scripted

FIX = pathlib.Path(__file__).parent / "fixtures" / "android"
TOKEN = "t" * 40
REVIEW_APP = "com.example.shop/com.example.shop.ReviewWriteActivity"


class Bridge:
    """The bridge as the server sees it: one current screen, every call recorded. `screens` maps a name to a (dump, app) pair."""

    def __init__(self, screen="review_form", app=REVIEW_APP):
        self.screen, self.app, self.log = screen, app, []
        self.apps = [{"package": "com.android.settings", "label": "Settings"}, {"package": "com.example.shop", "label": "Shop"}]
        self.fail = None

    def show(self, name, app):
        self.screen, self.app = name, app

    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.log.append((request.method, path, body))
        if self.fail:
            return httpx.Response(502, json={"error": self.fail})
        if path == "/screen":
            png = "iVBORw0KGgo=" if request.url.params.get("image") == "1" else None
            return httpx.Response(200, json={"xml": (FIX / f"{self.screen}.xml").read_text(encoding="utf-8"), "png": png, "app": self.app})
        if path == "/apps":
            return httpx.Response(200, json={"items": self.apps})
        return httpx.Response(200, json={"ok": True})

    def calls(self, path=None):
        return [b for m, p, b in self.log if path is None or p == path]


class Grants:
    """TaskGrants stand-in: covers the kinds it was given."""

    def __init__(self, *kinds):
        self.kinds, self.used = kinds, []

    def use(self, kind, host, *, tool="browser_click", secret=False):
        self.used.append((kind, host, tool, secret))
        return kind in self.kinds and not secret


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(android, "SETTLE_SECONDS", 0)


def make(bridge=None, grants=None, **kw):
    bridge = bridge or Bridge()
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(bridge)))
    session = AndroidSession(client, ScreenReader())
    tools = {t.name: t for t in android_tools(client, grants=grants, session=session, **kw)}
    return bridge, session, tools


async def run(tools, name, **args):
    return await tools[name].handler(args)


def ref_of(session, words):
    return next(i.ref for i in session.screen().items if i.words == words)


# ---- the seven tools through the bridge
def test_the_seven_tools_of_the_doc_and_which_ask():
    _, _, tools = make()
    assert list(tools) == ["android_look", "android_tap", "android_type", "android_swipe", "android_key", "android_open", "android_apps"]
    assert {n for n, t in tools.items() if t.requires_approval} == {"android_tap", "android_type"}
    assert all(t.untrusted and t.source == "page" for t in tools.values())


async def test_look_returns_the_screen_with_refs_and_asks_for_the_picture_only_when_wanted():
    bridge, session, tools = make()
    out = await run(tools, "android_look")
    assert isinstance(out, ToolOutput) and out.images == []
    assert out.text.splitlines()[0] == "App: com.example.shop (ReviewWriteActivity)   720x1280"
    assert 'a4 button "등록하기"' in out.text and bridge.log[-1][1] == "/screen"
    with_picture = await run(tools, "android_look", image=True)
    assert with_picture.images == ["data:image/png;base64,iVBORw0KGgo="] and with_picture.no_vision


async def test_what_the_screen_shows_is_fenced_as_outside_text():
    _, _, tools = make()
    out = await run(tools, "android_look")
    shown = _result_text(tools["android_look"], out)
    assert "[UNTRUSTED CONTENT" in shown and shown.index("<<<") < shown.index("등록하기") < shown.index("[END UNTRUSTED CONTENT")
    assert shown.index("The screen of the phone app") < shown.index("[UNTRUSTED CONTENT")          # our own words stay outside the fence


async def test_tap_by_ref_taps_the_centre_and_returns_the_new_screen():
    bridge, session, tools = make()
    await run(tools, "android_look")
    out = await run(tools, "android_tap", ref=ref_of(session, "Back"))
    assert bridge.calls("/tap") == [{"x": 56, "y": 104}] and isinstance(out, ToolOutput) and out.text.startswith("App: com.example.shop")
    assert bridge.log[-1][1] == "/screen"                                           # one dump after the step
    await run(tools, "android_tap", x=300, y=400)
    assert bridge.calls("/tap")[-1] == {"x": 300, "y": 400}


async def test_type_taps_the_field_first_then_types_and_can_press_enter():
    bridge, session, tools = make()
    await run(tools, "android_look")
    await run(tools, "android_type", ref="a3", text="소스가 맛있어요", submit=True)
    assert [(m, p) for m, p, _ in bridge.log if p != "/screen"] == [("POST", "/tap"), ("POST", "/text"), ("POST", "/key")]
    assert bridge.calls("/tap") == [{"x": 360, "y": 400}] and bridge.calls("/text") == [{"text": "소스가 맛있어요"}] and bridge.calls("/key") == [{"key": "enter"}]
    await run(tools, "android_type", text="더 쓰기")                                   # the field in focus: no tap
    assert len(bridge.calls("/tap")) == 1 and bridge.calls("/text")[-1] == {"text": "더 쓰기"}


async def test_swipe_key_open_and_apps():
    bridge, session, tools = make()
    await run(tools, "android_look")
    await run(tools, "android_swipe", direction="down")
    assert bridge.calls("/swipe") == [{"x1": 360, "y1": 960, "x2": 360, "y2": 320, "ms": 300}]      # the finger moves up
    await run(tools, "android_swipe", x1=1, y1=2, x2=3, y2=4)
    assert bridge.calls("/swipe")[-1] == {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "ms": 300}
    assert str(await run(tools, "android_swipe", direction="sideways")).startswith("Error")
    await run(tools, "android_key", key="back")
    assert bridge.calls("/key") == [{"key": "back"}] and str(await run(tools, "android_key", key="power")).startswith("Error")
    await run(tools, "android_open", app="com.android.settings")
    await run(tools, "android_open", app="shop")                                    # by the app's name
    assert [b["package"] for b in bridge.calls("/open")] == ["com.android.settings", "com.example.shop"]
    assert "no app matches" in str(await run(tools, "android_open", app="Nonexistent"))
    listing = await run(tools, "android_apps")
    assert "com.android.settings — Settings" in listing.text


async def test_a_bridge_error_comes_back_as_words_for_the_model():
    bridge, _, tools = make()
    bridge.fail = "the device 192.168.1.50:5555 does not answer (adb connect failed)"
    out = await run(tools, "android_look")
    assert isinstance(out, Ours) and out.startswith("Error:") and "does not answer" in out and TOKEN not in out


async def test_a_ref_from_an_earlier_screen_is_refused_and_so_is_a_tap_before_any_look():
    bridge, session, tools = make()
    assert "look at the screen first" in tools["android_tap"].validate({"ref": "a1"})
    await run(tools, "android_look")
    old = ref_of(session, "등록하기")
    bridge.show("pay", "com.example.shop/.PayActivity")
    await run(tools, "android_look")
    assert "not on the screen you last looked at" in tools["android_tap"].validate({"ref": old})
    assert "not on the screen" in str(await run(tools, "android_tap", ref=old)) and bridge.calls("/tap") == []
    assert tools["android_tap"].validate({"ref": ref_of(session, "결제하기")}) is None
    assert tools["android_tap"].validate({}) is not None


async def test_each_phone_keeps_its_own_screen():
    _, session, tools = make()
    await run(tools, "android_look")
    token = browser_owner.set("other-phone")
    try:
        assert session.screen() is None and "look at the screen first" in tools["android_tap"].validate({"ref": "a1"})
    finally:
        browser_owner.reset(token)
    assert session.screen() is not None


# ---- approval: the browser's rules with the package as the site
async def test_a_tap_that_pays_asks_and_a_tap_that_only_looks_does_not():
    bridge, session, tools = make(Bridge("pay", "com.example.shop/.PayActivity"))
    await run(tools, "android_look")
    pay, tap = tools["android_tap"], ref_of(session, "결제하기")
    assert session.judge("android_tap", {"ref": tap}) == "pay"
    assert await pay.auto_approve({"ref": tap}) is False
    assert await pay.auto_approve({"ref": ref_of(session, "선물 포장")}) is True               # words with no commit: no card
    assert "결제하기" in await pay.describe({"ref": tap}) and "com.example.shop" in await pay.describe({"ref": tap})
    assert "payment" in await pay.describe({"ref": tap})


async def test_a_settings_tap_does_not_ask():
    _, session, tools = make(Bridge("settings", "com.android.settings/.Settings"))
    await run(tools, "android_look")
    assert await tools["android_tap"].auto_approve({"ref": ref_of(session, "All apps 10 apps installed")}) is True


async def test_a_point_with_nothing_readable_under_it_always_asks():
    _, session, tools = make(Bridge("settings", "com.android.settings/.Settings"))
    await run(tools, "android_look")
    assert await tools["android_tap"].auto_approve({"x": 700, "y": 1270}) is False


async def test_typing_a_review_does_not_ask_but_personal_details_do():
    _, session, tools = make()
    await run(tools, "android_look")
    kind = tools["android_type"].auto_approve
    assert await kind({"ref": "a3", "text": "소스가 맛있어요"}) is True
    assert await kind({"ref": "a3", "text": "전화는 010-1234-5678"}) is False
    assert await kind({"text": "kim@example.org"}) is False and await kind({"text": "hello"}) is True


async def test_a_task_grant_covers_its_kind_on_that_app_only():
    grants = Grants("send")
    _, session, tools = make(grants=grants)
    await run(tools, "android_look")
    submit = ref_of(session, "등록하기")
    assert await tools["android_tap"].auto_approve({"ref": submit}) is True
    assert grants.used == [("send", "com.example.shop", "browser_click", False)]
    _, session2, tools2 = make(grants=Grants("pay"))
    await run(tools2, "android_look")
    assert await tools2["android_tap"].auto_approve({"ref": ref_of(session2, "등록하기")}) is False         # a grant of another kind


async def test_the_owners_every_step_asks_setting_makes_every_tap_ask():
    _, session, tools = make(Bridge("settings", "com.android.settings/.Settings"), approve_actions=True)
    await run(tools, "android_look")
    assert await tools["android_tap"].auto_approve({"ref": ref_of(session, "Navigate up")}) is False


# ---- the effect and the ledger: done once
def test_an_app_step_is_an_effect_on_the_app_like_a_sites():
    e = effects.effect_of("android_tap", {"ref": "a4"}, site="com.example.shop", commit="send")
    assert (e.kind, e.targets, e.action) == ("send", ["app:com.example.shop"], "android_tap")
    t = effects.effect_of("android_type", {"text": "hello"}, site="com.example.shop", commit="personal")
    assert t.kind == "send" and t.text == "hello"
    assert effects.effect_of("android_tap", {"ref": "a4"}, site="com.example.shop", commit="delete").kind == "delete"
    assert effects.effect_of("android_tap", {"ref": "a4"}, site="com.example.shop", commit="") is None
    assert {"android_tap", "android_type"} <= effects.GRANTABLE


async def test_done_once_stops_a_second_submit_of_the_same_typed_text_and_lets_a_different_text_through():
    bridge, session, tools = make()
    await run(tools, "android_look")
    store = Store()
    once = DoneOnce(ActionLedger(store), effect=lambda tool, args: effects.effect_of(tool, args),
                    turn=lambda: {"task_id": 1, "turn_id": "t1", "source": "chat", "unattended": False, "request": "write the review"},
                    browser_step=session.judge_ahead, browser_page=session.page)

    async def step(name, args, result="등록되었습니다"):
        att = await once.begin(Tool(name, name, {}, None), args)
        return (att.block, att) if att is not None and att.block else ("", att)

    # (the typing step records its text; the tap is the commit)
    await once.begin(Tool("android_type", "android_type", {}, None), {"ref": "a3", "text": "소스가 맛있어요"})
    submit = ref_of(session, "등록하기")
    first_block, att = await step("android_tap", {"ref": submit})
    assert first_block == "" and att is not None and att.record_id
    att.settle("등록되었습니다", "등록되었습니다")
    again_block, _ = await step("android_tap", {"ref": submit})
    assert again_block.startswith("Not run: the same android tap on app:com.example.shop already went out")
    await once.begin(Tool("android_type", "android_type", {}, None), {"ref": "a3", "text": "밥이 고슬고슬해요"})
    other_block, other = await step("android_tap", {"ref": submit})
    assert other_block == "" and other.record_id
    row = once.ledger.list(1)[0]
    assert row.targets == ("app:com.example.shop",)
    assert session.page("android_tap", {"ref": submit}) == (REVIEW_APP, "등록하기")


# ---- the app
def phone_app(tmp_path, **kw):
    settings = app_settings(tmp_path, home_dir=str(tmp_path / "home"), **kw)
    return create_app(settings, stream_fn=scripted([TextDelta("ok"), Done("stop")]), with_schedule_check=False)


def test_the_tools_exist_only_with_a_device_a_long_token_and_the_bridge_address(tmp_path):
    on = {"android_url": "http://android:8090", "android_token": TOKEN, "android_device": "192.168.1.50:5555"}
    names = lambda app: {t.name for t in app.state.tools if t.name.startswith("android_")}   # noqa: E731
    assert len(names(phone_app(tmp_path / "a", **on))) == 9      # the seven steps, android_login and android_send_screenshot (android_show needs the browser)
    assert names(phone_app(tmp_path / "b", **{**on, "android_device": ""})) == set()
    assert names(phone_app(tmp_path / "c", **{**on, "android_token": "short"})) == set()
    assert names(phone_app(tmp_path / "d", **{**on, "android_url": ""})) == set()
    assert names(phone_app(tmp_path / "e")) == set()


def test_the_mirror_tool_comes_with_a_browser(tmp_path):
    on = {"android_url": "http://android:8090", "android_token": TOKEN, "android_device": "192.168.1.50:5555"}
    names = lambda app: {t.name for t in app.state.tools if t.name.startswith("android_")}   # noqa: E731
    assert "android_show" not in names(phone_app(tmp_path / "a", **on))
    settings = app_settings(tmp_path / "b", home_dir=str(tmp_path / "b" / "home"), **on)
    with_browser = names(create_app(dataclasses.replace(settings, cdp_host="browser"), stream_fn=scripted([TextDelta("ok"), Done("stop")]),
                                    with_schedule_check=False))
    assert {"android_show", "android_login", "android_send_screenshot"} <= with_browser and len(with_browser) == 10


def test_find_tools_finds_them_by_android_and_by_phone_app_and_they_are_not_in_the_core(tmp_path):
    from assistant.agent.toolbox import CORE_TOOLS
    app = phone_app(tmp_path, android_url="http://android:8090", android_token=TOKEN, android_device="192.168.1.50:5555")
    hidden = [t for t in app.state.tools if t.name not in CORE_TOOLS]
    assert not any(t.name.startswith("android_") for t in app.state.tools if t.name in CORE_TOOLS)
    assert {t.name for t in search(hidden, "android")[:20] if t.name.startswith("android_")} == {t.name for t in hidden if t.name.startswith("android_") and t.name != "android_login"}   # the sign-in is found by "sign in"
    for query in ("phone app", "android phone app", "open an app on the phone"):
        assert search(hidden, query)[0].name.startswith("android_"), query
    finder = next(t for t in app.state.tools if t.name == "find_tools")
    assert "android_*" in finder.description
    for t in (x for x in hidden if x.name.startswith("android_")):
        assert search(hidden, t.name)[0] is t and t in search(hidden, t.name.replace("_", " "))


async def test_the_phone_screenshot_paints_over_password_fields():
    import base64
    import io

    from PIL import Image

    from assistant.android.screen import ScreenReader
    from assistant.android.tools import phone_grab

    im = Image.new("RGB", (100, 100), (255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    xml = ('<?xml version="1.0"?><hierarchy rotation="0"><node class="android.widget.FrameLayout" bounds="[0,0][100,100]" package="x.y">'
           '<node class="android.widget.EditText" password="true" text="secret" bounds="[10,10][60,30]" package="x.y"/></node></hierarchy>')

    class Client:
        async def screen(self, image=False):
            return {"png": base64.b64encode(buf.getvalue()).decode(), "xml": xml, "app": "x.y/.Main"}

    jpeg, w, h, problem = await phone_grab(Client(), ScreenReader())("")
    assert jpeg and not problem and (w, h) == (100, 100)
    shot = Image.open(io.BytesIO(jpeg)).convert("RGB")
    assert sum(shot.getpixel((30, 20))) < 200 and sum(shot.getpixel((80, 80))) > 700   # the field is dark, the rest untouched


async def test_without_a_dump_no_phone_screenshot_is_sent():
    from assistant.android.screen import ScreenReader
    from assistant.android.tools import phone_grab

    class Client:
        async def screen(self, image=False):
            return {"png": "", "xml": ""}

    jpeg, *_ , problem = await phone_grab(Client(), ScreenReader())("")
    assert jpeg is None and problem
