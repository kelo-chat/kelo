"""First run of an app on the phone (docs/phone-worker.md "First run of an app"): the permission dialog is found by the package the
device itself names, the setup judge (fake here) says which controls get through an app's first screens, those taps are the kind
"setup" and raise no card, special access and anything else flagged keep asking, and what was answered is reported and remembered."""

import functools
import json

import httpx
import pytest

from assistant import step_judge
from assistant.android import tools as android
from assistant.android.device import DeviceClient
from assistant.android.screen import ScreenReader
from assistant.android.tools import AndroidSession, android_tools
from assistant.step_judge import SetupVerdict, StepVerdict
from assistant.tools.browser import browser_owner

TOKEN = "t" * 40
CONTROLLER = "com.google.android.permissioncontroller"
SHOP = "com.example.shop"


def node(i, cls, package, text="", desc="", clickable=False, checkable=False, checked=False, y=0, password=False):
    return (f'<node index="{i}" text="{text}" resource-id="" class="{cls}" package="{package}" content-desc="{desc}" checkable="{str(checkable).lower()}" '
            f'checked="{str(checked).lower()}" clickable="{str(clickable).lower()}" enabled="true" focusable="true" focused="false" scrollable="false" '
            f'long-clickable="false" password="{str(password).lower()}" selected="false" bounds="[40,{y}][680,{y + 90}]" hint="" />')


def dump(package, *rows):
    """A uiautomator dump: rows are (kind, text) with kind text | button | check | checked | edit."""
    cls = {"text": "android.widget.TextView", "button": "android.widget.Button", "check": "android.widget.CheckBox",
           "checked": "android.widget.CheckBox", "edit": "android.widget.EditText"}
    out = []
    for n, (kind, text) in enumerate(rows):
        out.append(node(n, cls[kind], package, text, clickable=kind != "text", checkable=kind in ("check", "checked"), checked=kind == "checked",
                        y=100 + n * 100))
    return ('<?xml version="1.0"?><hierarchy rotation="0"><node index="0" text="" resource-id="" class="android.widget.FrameLayout" '
            f'package="{package}" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" '
            f'focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][720,1280]" hint="">'
            + "".join(out) + "</node></hierarchy>")


SCREENS = {
    "notifications": (CONTROLLER, [("text", "Allow Shop to send you notifications?"), ("button", "Allow"), ("button", "Don't allow")]),
    "location": (CONTROLLER, [("text", "Allow Shop to access this device's location?"), ("button", "While using the app"),
                              ("button", "Only this time"), ("button", "Don't allow")]),
    "special": ("com.android.settings", [("text", "Allow Shop to draw over other apps"), ("button", "Allow display over other apps"),
                                         ("button", "Back")]),
    "tour": (SHOP, [("text", "Welcome to Shop"), ("button", "Next"), ("button", "Skip")]),
    "terms": (SHOP, [("text", "Terms of use"), ("checked", "I accept the terms (required)"), ("check", "Send me offers"),
                     ("check", "Share my data with partners"), ("button", "Continue")]),
    "ad": (SHOP, [("text", "Summer sale"), ("button", "Close"), ("button", "Buy the sale item")]),
    "signup": (SHOP, [("text", "Create your account"), ("edit", ""), ("edit", ""), ("button", "Sign up")]),
    "home": (SHOP, [("text", "Today"), ("button", "Orders"), ("button", "Search")]),
}


class Bridge:
    def __init__(self, screen="tour"):
        self.screen, self.log, self.permission = screen, [], CONTROLLER

    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.log.append((request.method, path, body))
        if path == "/screen":
            package, rows = SCREENS[self.screen]
            return httpx.Response(200, json={"xml": dump(package, *rows), "png": None, "app": f"{package}/.MainActivity"})
        if path == "/resolve":
            return httpx.Response(200, json={"package": self.permission})
        return httpx.Response(200, json={"ok": True})

    def calls(self, path):
        return [b for m, p, b in self.log if p == path]


class Judge:
    """A fake step judge: `phone` finds every tap harmless unless the node is in `flagged`; `setup` answers by the screen's first words
    (what the main model would do), recording what it was asked."""

    def __init__(self, flagged=()):
        self.flagged, self.asked = set(flagged), []

    async def phone(self, package, activity, item, screen):
        return StepVerdict(item.get("text") in self.flagged, False, False, False, False)

    async def setup(self, ctx):
        self.asked.append(ctx)
        controls = {c["text"]: c["ref"] for c in ctx["controls"]}
        first = next((c["text"] for c in ctx["controls"] if c["kind"] == "text"), "")
        pick = lambda *names: tuple(controls[n] for n in names if n in controls)  # noqa: E731
        job = ctx["the_job"]
        if "notifications" in first:
            return SetupVerdict("permission", pick("Allow"), "notifications allowed")
        if "location" in first:
            return (SetupVerdict("permission", pick("Only this time"), "location: only this time") if "delivery address" in job
                    else SetupVerdict("permission", pick("Don't allow"), "location denied: not needed"))
        if "draw over" in first:
            return SetupVerdict("special_access", (), "")
        if first == "Welcome to Shop":
            return SetupVerdict("intro", pick("Skip"), "intro skipped")
        if first == "Terms of use":
            return SetupVerdict("terms", pick("I accept the terms (required)", "Continue"), "terms accepted, optional boxes left unticked")
        if first == "Summer sale":
            return SetupVerdict("popup", pick("Close"), "ad closed")
        return SetupVerdict("none", (), "")


class Memory:
    def __init__(self):
        self.rows = {}

    def meta(self, key):
        return self.rows.get(key, "")

    def set_meta(self, key, value):
        self.rows[key] = value


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(android, "SETTLE_SECONDS", 0)


def make(screen="tour", judge=None, job="", memory=None, **kw):
    bridge = Bridge(screen)
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(bridge)))
    session = AndroidSession(client, ScreenReader())
    session.step_judge = judge if judge is not None else Judge()
    session.task = lambda: job
    session.memory = memory
    tools = {t.name: t for t in android_tools(client, session=session, **kw)}
    return bridge, session, tools


def ref(session, words):
    return next(i.ref for i in session.screen().items if i.words == words)


async def look(tools, bridge, screen):
    bridge.screen = screen
    return await tools["android_look"].handler({})


async def kind(session, ref_):
    """The commit kind of tapping a ref, as the approval sees it (the step judge asked first)."""
    args = {"ref": ref_}
    await session.settle("android_tap", args)
    return session.judge("android_tap", args)


async def tap(tools, session, words):
    """A tap as the approval path does it: asked (judged) first, then made."""
    args = {"ref": ref(session, words)}
    assert await tools["android_tap"].auto_approve(args) is True
    return await tools["android_tap"].handler(args)


async def asks(tools, session, words):
    """True when tapping the control needs a card."""
    return not await tools["android_tap"].auto_approve({"ref": ref(session, words)})


# ---- the permission dialog is found by the package the device names
async def test_the_permission_package_is_asked_from_the_device_once_and_the_dialog_is_told_to_the_judge_with_the_app_before():
    bridge, session, tools = make("home")
    await tools["android_look"].handler({})
    await look(tools, bridge, "notifications")
    await look(tools, bridge, "location")
    assert bridge.calls("/resolve") != [] and len(bridge.calls("/resolve")) == 1            # asked once, then kept
    dialog = [c for c in session.step_judge.asked if c["system_permission_dialog"]]
    assert len(dialog) == 2 and {c["app_in_front_before"] for c in dialog} == {SHOP}        # the requesting app = the app in front before
    assert all(not c["system_permission_dialog"] for c in session.step_judge.asked[:1])


async def test_another_device_with_another_permission_package_is_found_the_same_way():
    bridge, session, tools = make("home")
    bridge.permission = "vendor.permissions"
    await tools["android_look"].handler({})
    bridge.screen = "notifications"
    SCREENS["notifications"] = ("vendor.permissions", SCREENS["notifications"][1])
    try:
        await tools["android_look"].handler({})
    finally:
        SCREENS["notifications"] = (CONTROLLER, SCREENS["notifications"][1])
    assert session.step_judge.asked[-1]["system_permission_dialog"] is True


async def test_a_permission_dialog_of_a_package_that_is_not_the_devices_is_not_taken_for_the_system_one():
    bridge, session, tools = make("home")
    bridge.permission = "some.other.package"
    await tools["android_look"].handler({})
    await look(tools, bridge, "notifications")
    assert session.step_judge.asked[-1]["system_permission_dialog"] is False


# ---- what to answer
async def test_notifications_are_allowed_without_a_card_and_the_report_line_is_given():
    bridge, session, tools = make("home", job="order food")
    await tools["android_look"].handler({})                      # the app is in front, then it asks
    out = await look(tools, bridge, "notifications")
    assert "Controls that get through" in out.head
    allow = ref(session, "Allow")
    assert await kind(session, allow) == "setup"
    assert await tools["android_tap"].auto_approve({"ref": allow}) is True
    done = await tools["android_tap"].handler({"ref": allow})
    assert "First-run step, for your report" in done.head and "notifications allowed" in done.head
    assert session.setup_log[session.owner] == [f"{SHOP}: notifications allowed"]


async def test_location_for_a_task_that_needs_it_is_only_this_time_and_for_one_that_does_not_it_is_denied():
    bridge, session, tools = make("location", job="find the nearest store and set the delivery address")
    await tools["android_look"].handler({})
    assert await kind(session, ref(session, "Only this time")) == "setup"
    assert await kind(session, ref(session, "Don't allow")) == ""            # not chosen for this job: judged as before
    assert not await asks(tools, session, "Only this time")
    bridge, session, tools = make("location", job="check my order history")
    await tools["android_look"].handler({})
    assert await kind(session, ref(session, "Don't allow")) == "setup"
    assert await kind(session, ref(session, "Only this time")) == ""
    assert session.step_judge.asked[-1]["the_job"] == "check my order history"                 # the brief reaches the judge


async def test_special_access_is_never_let_through_and_the_model_is_told_the_owner_decides():
    bridge, session, tools = make("special", job="anything")
    out = await tools["android_look"].handler({})
    assert "needs_input" in out.head and "Controls that get through" not in out.head
    for words in ("Allow display over other apps", "Back"):
        assert await kind(session, ref(session, words)) == "unknown"
        assert await asks(tools, session, words)


async def test_an_intro_tour_is_skipped_and_an_ad_is_closed_without_a_card():
    bridge, session, tools = make("tour")
    await tools["android_look"].handler({})
    assert not await asks(tools, session, "Skip")
    assert await kind(session, ref(session, "Skip")) == "setup"
    assert await kind(session, ref(session, "Next")) == ""
    await look(tools, bridge, "ad")
    assert await kind(session, ref(session, "Close")) == "setup"
    assert await kind(session, ref(session, "Buy the sale item")) == ""


async def test_terms_get_only_the_required_box_and_the_accept_control():
    bridge, session, tools = make("terms")
    await tools["android_look"].handler({})
    kinds = {w: await kind(session, ref(session, w)) for w in
             ("I accept the terms (required)", "Send me offers", "Share my data with partners", "Continue")}
    assert kinds == {"I accept the terms (required)": "setup", "Send me offers": "", "Share my data with partners": "", "Continue": "setup"}


async def test_a_sign_up_screen_is_not_first_run_handling_and_the_judge_is_not_even_asked():
    bridge, session, tools = make("signup")
    out = await tools["android_look"].handler({})
    assert session.step_judge.asked == [] and "first screens" not in out.head
    assert await kind(session, ref(session, "Sign up")) == ""                # as before: the commit judge decides


async def test_a_sign_up_the_judge_would_wrongly_call_setup_is_still_not_setup_because_it_has_fields_for_the_owners_data():
    class Eager(Judge):
        async def setup(self, ctx):
            self.asked.append(ctx)
            return SetupVerdict("popup", tuple(c["ref"] for c in ctx["controls"] if c["kind"] == "button"), "closed")
    bridge, session, tools = make("signup", judge=Eager())
    await tools["android_look"].handler({})
    assert await kind(session, ref(session, "Sign up")) != "setup"


# ---- safety: nothing else changes
async def test_a_control_the_step_judge_flags_still_asks_even_when_the_setup_judge_chose_it():
    bridge, session, tools = make("ad", judge=Judge(flagged={"Close"}))
    await tools["android_look"].handler({})
    assert await kind(session, ref(session, "Close")) == "unknown" or await kind(session, ref(session, "Close")) == "pay"
    assert await asks(tools, session, "Close")


async def test_the_owners_every_step_asks_setting_still_asks_for_a_setup_tap():
    bridge, session, tools = make("tour", approve_actions=True)
    await tools["android_look"].handler({})
    assert await asks(tools, session, "Skip")


async def test_when_the_setup_judge_is_down_or_unreadable_nothing_is_let_through():
    class Down(Judge):
        async def setup(self, ctx):
            return None
    bridge, session, tools = make("tour", judge=Down())
    out = await tools["android_look"].handler({})
    assert "first screens" not in out.head
    assert await kind(session, ref(session, "Skip")) == ""


async def test_a_ref_the_judge_made_up_is_dropped():
    class Liar(Judge):
        async def setup(self, ctx):
            return SetupVerdict("popup", ("a999", "zz"), "x")
    bridge, session, tools = make("tour", judge=Liar())
    out = await tools["android_look"].handler({})
    assert "Controls that get through" not in out.head


async def test_the_next_screen_is_judged_afresh_so_a_ref_of_the_old_one_is_not_setup():
    bridge, session, tools = make("tour")
    await tools["android_look"].handler({})
    old = ref(session, "Skip")
    await look(tools, bridge, "home")
    assert await kind(session, old) != "setup"


# ---- who is asked: an app's first screens only
async def test_after_two_screens_with_nothing_to_set_up_the_app_is_no_longer_asked_until_it_is_opened_again():
    bridge, session, tools = make("home")
    for _ in range(4):
        await tools["android_look"].handler({})
    assert len(session.step_judge.asked) == 2
    bridge.screen = "home"
    await tools["android_open"].handler({"app": SHOP})
    await tools["android_look"].handler({})
    assert len(session.step_judge.asked) == 4                  # the open's own look + this one: asked again


async def test_a_permission_dialog_is_asked_even_in_an_app_that_has_gone_quiet():
    bridge, session, tools = make("home")
    for _ in range(3):
        await tools["android_look"].handler({})
    n = len(session.step_judge.asked)
    await look(tools, bridge, "notifications")
    assert len(session.step_judge.asked) == n + 1


# ---- remembered per app
async def test_what_was_answered_is_remembered_per_app_and_given_to_the_judge_next_time_and_can_be_cleared():
    mem = Memory()
    bridge, session, tools = make("home", memory=mem)
    await tools["android_look"].handler({})
    await look(tools, bridge, "notifications")
    await tap(tools, session, "Allow")
    assert json.loads(mem.rows[f"phone_setup:{SHOP}"]) == ["notifications allowed"]
    await look(tools, bridge, "notifications")
    assert session.step_judge.asked[-1]["answered_before_for_this_app"] == ["notifications allowed"]
    session.forget_setup(SHOP)
    await look(tools, bridge, "notifications")
    assert session.step_judge.asked[-1]["answered_before_for_this_app"] == []


async def test_the_report_gets_one_line_per_first_run_step_of_the_app():
    bridge, session, tools = make("tour")
    await tools["android_look"].handler({})
    await tap(tools, session, "Skip")
    await look(tools, bridge, "terms")
    await tap(tools, session, "Continue")
    assert session.setup_log[session.owner] == [f"{SHOP}: intro skipped", f"{SHOP}: terms accepted, optional boxes left unticked"]


async def test_a_tap_that_is_not_a_setup_step_adds_no_report_line():
    bridge, session, tools = make("tour")
    await tools["android_look"].handler({})
    out = await tap(tools, session, "Next")
    assert "First-run" not in out.head and session.setup_log == {}


# ---- the pieces
def test_the_setup_answer_is_strict_json():
    ok = step_judge.parse_setup({"screen": "popup", "allow": ["a1", " A2"], "note": " ad  closed "})
    assert ok == SetupVerdict("popup", ("a1", "a2"), "ad closed")
    assert step_judge.parse_setup({"screen": "special_access", "allow": ["a1"], "note": ""}).allow == ()      # the owner decides
    assert step_judge.parse_setup({"screen": "none", "allow": ["a1"], "note": ""}).allow == ()
    for bad in (None, "x", {"screen": "other", "allow": [], "note": ""}, {"screen": "popup", "allow": "a1", "note": ""},
                {"screen": "popup", "allow": [1], "note": ""}, {"screen": "popup", "allow": [], "note": 3}):
        assert step_judge.parse_setup(bad) is None


async def test_the_real_step_judge_sends_the_rules_and_the_screen_and_reads_the_answer():
    seen = []

    async def ask(system, user):
        seen.append((system, user))
        return {"screen": "intro", "allow": ["a2"], "note": "intro skipped"}
    got = await step_judge.StepJudge(ask).setup({"controls": [{"ref": "a2"}], "the_job": "x"})
    assert got == SetupVerdict("intro", ("a2",), "intro skipped")
    system = seen[0][0]
    for rule in ("Notifications: allow", "narrowest choice", "deny choice", "special_access", "optional box", "Creating an account"):
        assert rule in system
    assert "\"a2\"" in seen[0][1]

    async def boom(system, user):
        raise RuntimeError("down")
    assert await step_judge.StepJudge(boom).setup({}) is None


def test_the_bridge_names_the_package_that_answers_an_intent_action(monkeypatch):
    import importlib.util
    import pathlib
    import subprocess
    path = pathlib.Path(__file__).resolve().parents[2] / "docker" / "android" / "bridge.py"
    spec = importlib.util.spec_from_file_location("android_bridge_fr", path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    shells = []

    def run(argv, **kw):
        shells.append(argv)
        out = ("get-state" in " ".join(argv) and "device\n") or "priority=0 match=0x0\ncom.google.android.permissioncontroller/com.android.permissioncontroller.permission.ui.GrantPermissionsActivity\n"
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(bridge.subprocess, "run", run)
    device = bridge.Device("192.168.1.50:5555")
    got = bridge.handle(device, TOKEN, "GET", "/resolve?action=android.content.pm.action.REQUEST_PERMISSIONS", f"Bearer {TOKEN}", {})
    assert got == (200, {"package": "com.google.android.permissioncontroller"})
    assert any("resolve-activity --brief -a android.content.pm.action.REQUEST_PERMISSIONS" in " ".join(a) for a in shells)
    bad = bridge.handle(device, TOKEN, "GET", "/resolve?action=x;reboot", f"Bearer {TOKEN}", {})
    assert bad[0] == 400


def test_the_phone_job_is_told_to_report_first_run_steps():
    from assistant.workers import PHONE_BRIEF
    assert "First-run step" in PHONE_BRIEF


def test_the_app_gives_the_session_the_jobs_brief_and_the_small_table(tmp_path):
    from assistant.workers import current_worker
    from test_android_tools import phone_app
    app = phone_app(tmp_path, android_url="http://android:8090", android_token=TOKEN, android_device="192.168.1.50:5555")
    session = app.state.android_session
    assert session.task() == ""                                        # not a job: no brief
    token = current_worker.set("no-such-job")
    try:
        assert session.task() == ""
    finally:
        current_worker.reset(token)
    session.memory.set_meta("phone_setup:x", "[1]")
    assert session.memory.meta("phone_setup:x") == "[1]" and type(session.memory).__name__ == "Store"   # the table is the app's own store
