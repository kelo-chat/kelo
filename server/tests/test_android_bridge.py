"""The bridge container's script (docker/android/bridge.py): its pure parts, with subprocess mocked. Never runs adb."""

import importlib.util
import json
import pathlib
import subprocess

import pytest

PATH = pathlib.Path(__file__).resolve().parents[2] / "docker" / "android" / "bridge.py"
spec = importlib.util.spec_from_file_location("android_bridge", PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

TOKEN = "k" * 40
SERIAL = "192.168.1.50:5555"
HIERARCHY = '<?xml version="1.0"?><hierarchy rotation="0"><node bounds="[0,0][720,1280]"/></hierarchy>'


class Adb:
    """subprocess.run stand-in: records every adb call, answers by the command."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        assert argv[0] == "adb" and kw.get("timeout")        # every call has a timeout
        joined = " ".join(argv[1:])
        for key, value in self.answers.items():
            if key in joined:
                if isinstance(value, BaseException):
                    raise value
                code, out = value if isinstance(value, tuple) else (0, value)
                err = ""
                return subprocess.CompletedProcess(argv, code, out if kw.get("text") else out.encode() if isinstance(out, str) else out, err if kw.get("text") else err.encode())
        return subprocess.CompletedProcess(argv, 0, "" if kw.get("text") else b"", "" if kw.get("text") else b"")

    def shells(self):
        return [a[-1] for a, _ in self.calls if "shell" in a]


@pytest.fixture
def adb(monkeypatch):
    fake = Adb({"get-state": "device\n", "cat /data/local/tmp/kelo-ui.xml": HIERARCHY})
    monkeypatch.setattr(bridge.subprocess, "run", fake)
    return fake


def call(device, method, path, body=None, auth=f"Bearer {TOKEN}"):
    return bridge.handle(device, TOKEN, method, path, auth, body or {})


# ---- pure parts
@pytest.mark.parametrize("text,out", [("hello world", "'hello%sworld'"), ("it's", "'it'\\''s'"), ("a&b;c|d$e", "'a&b;c|d$e'"),
                                      ('say "hi" `x` \\n', "'say%s\"hi\"%s`x`%s\\n'")])
def test_input_text_arguments_are_quoted_and_spaces_become_percent_s(text, out):
    assert bridge.ascii_typable(text) and bridge.escape_input_text(text) == out


@pytest.mark.parametrize("text", ["한글", "naïve", "100% sure", "a\nb", ""])
def test_what_input_text_cannot_type_goes_through_the_clipboard(text):
    assert not bridge.ascii_typable(text)


def test_the_token_check():
    assert bridge.token_ok(f"Bearer {TOKEN}", TOKEN)
    assert not bridge.token_ok("Bearer nope", TOKEN) and not bridge.token_ok(None, TOKEN) and not bridge.token_ok(TOKEN, TOKEN)
    assert not bridge.token_ok("Bearer short", "short") and not bridge.token_ok("Bearer ", "")     # a short token never matches, even itself


def test_it_refuses_to_start_with_a_short_token_or_no_device(monkeypatch, capsys):
    monkeypatch.setenv("ANDROID_DEVICE", SERIAL)
    monkeypatch.setenv("ANDROID_BRIDGE_TOKEN", "x" * 31)
    monkeypatch.delenv("ANDROID_ADB_HOST", raising=False)
    assert bridge.main() == 2 and "at least 32" in capsys.readouterr().err
    monkeypatch.setenv("ANDROID_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("ANDROID_DEVICE", "")
    assert bridge.main() == 2 and "ANDROID_DEVICE" in capsys.readouterr().err
    # Finding 15: setting ANDROID_ADB_HOST alone must not bypass the no-device check
    monkeypatch.setenv("ANDROID_ADB_HOST", "172.28.0.1")
    assert bridge.main() == 2 and "ANDROID_DEVICE" in capsys.readouterr().err


def test_resolve_device_with_and_without_android_adb_host():
    # Without ANDROID_ADB_HOST: returns device as is
    assert bridge.resolve_device("192.168.1.50:5555", "") == "192.168.1.50:5555"
    assert bridge.resolve_device("127.0.0.1:5555", "") == "127.0.0.1:5555"
    assert bridge.resolve_device("", "") == ""

    # With ANDROID_ADB_HOST: replaces localhost/loopback or supplies host for port
    host = "172.28.0.1"
    assert bridge.resolve_device("127.0.0.1:5555", host) == "172.28.0.1:5555"
    assert bridge.resolve_device("localhost:5555", host) == "172.28.0.1:5555"
    assert bridge.resolve_device(":5555", host) == "172.28.0.1:5555"
    assert bridge.resolve_device("5555", host) == "172.28.0.1:5555"
    # Empty device is never resolved
    assert bridge.resolve_device("", host) == ""
    # Preserves explicit remote target
    assert bridge.resolve_device("192.168.1.50:5555", host) == "192.168.1.50:5555"
    # Invalid host characters are rejected
    assert bridge.resolve_device("127.0.0.1:5555", "; bad host") == "127.0.0.1:5555"


def test_main_uses_android_adb_host_when_device_is_loopback(monkeypatch, adb):
    monkeypatch.setenv("ANDROID_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("ANDROID_DEVICE", "127.0.0.1:5555")
    monkeypatch.setenv("ANDROID_ADB_HOST", "172.28.0.1")

    # Mock server to not block
    class FakeServer:
        def __init__(self, addr, handler):
            self.addr = addr
            self.daemon_threads = False
        def serve_forever(self):
            pass

    monkeypatch.setattr(bridge, "ThreadingHTTPServer", FakeServer)
    assert bridge.main() == 0
    # adb connect was called with the resolved host
    connect_calls = [c[0] for c in adb.calls if "connect" in c[0]]
    assert any("172.28.0.1:5555" in arg for c in connect_calls for arg in c)


def test_parsers():
    assert bridge.parse_size("Physical size: 720x1280\nOverride size: 540x960\n") == [540, 960] and bridge.parse_size("") == [0, 0]
    assert bridge.parse_front("  mCurrentFocus=Window{4f2 u0 com.android.settings/com.android.settings.Settings}") == "com.android.settings/com.android.settings.Settings"
    assert bridge.parse_front("nothing") == ""
    apps = bridge.parse_apps("priority=0 match=0x1\ncom.android.settings/.Settings\ncom.android.chrome/com.google.android.apps.chrome.Main\ncom.android.settings/.Other\n")
    assert apps == [{"package": "com.android.chrome", "label": "Chrome"}, {"package": "com.android.settings", "label": "Settings"}]


# ---- the API with adb mocked
def test_unauthorized_calls_are_refused_but_healthz_needs_no_token(adb):
    d = bridge.Device(SERIAL)
    assert call(d, "GET", "/healthz", auth=None) == (200, {"ok": True})
    assert call(d, "GET", "/status", auth=None)[0] == 401 and call(d, "POST", "/tap", {"x": 1, "y": 1}, auth="Bearer x")[0] == 401
    assert adb.calls == []


def test_screen_dumps_through_adb_shell_then_reads_the_file(adb):
    d = bridge.Device(SERIAL)
    status, body = call(d, "GET", "/screen?image=0")
    assert status == 200 and body["xml"] == HIERARCHY and body["png"] is None
    shells = adb.shells()
    assert shells[0] == "uiautomator dump /data/local/tmp/kelo-ui.xml" and shells[1] == "cat /data/local/tmp/kelo-ui.xml"
    dump_call = next(c for c in adb.calls if c[0][-1].startswith("uiautomator"))
    assert dump_call[0][:4] == ["adb", "-s", SERIAL, "shell"] and dump_call[1]["timeout"] == 20      # the doc's timeout for the dump
    assert all(kw["timeout"] == 10 for argv, kw in adb.calls if argv is not dump_call[0])                    # every other call: 10 s


def test_an_empty_dump_is_an_error_not_an_empty_screen(monkeypatch):
    monkeypatch.setattr(bridge.subprocess, "run", Adb({"get-state": "device\n", "cat /data": ""}))
    status, body = call(bridge.Device(SERIAL), "GET", "/screen")
    assert status == 502 and "empty" in body["error"]


def test_the_screenshot_comes_as_base64_only_when_asked(monkeypatch):
    fake = Adb({"get-state": "device\n", "cat /data": HIERARCHY, "screencap": b"\x89PNG-bytes"})
    monkeypatch.setattr(bridge.subprocess, "run", fake)
    _, body = call(bridge.Device(SERIAL), "GET", "/screen?image=1")
    import base64
    assert base64.b64decode(body["png"]) == b"\x89PNG-bytes"


def test_tap_swipe_key_validate_and_use_input(adb):
    d = bridge.Device(SERIAL)
    assert call(d, "POST", "/tap", {"x": 360, "y": 640})[0] == 200
    assert call(d, "POST", "/swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "ms": 100})[0] == 200
    assert call(d, "POST", "/key", {"key": "back"})[0] == 200
    assert adb.shells() == ["input tap 360 640", "input swipe 1 2 3 4 100", "input keyevent 4"]
    assert call(d, "POST", "/tap", {"x": "a", "y": 1})[0] == 400 and call(d, "POST", "/key", {"key": "power"})[0] == 400
    assert call(d, "POST", "/tap", {"x": 10 ** 9, "y": 1})[0] == 400


def test_ascii_text_goes_through_input_text_and_other_text_through_adb_keyboard(monkeypatch):
    import base64
    fake = Adb({"get-state": "device\n", "pm path com.android.adbkeyboard": "package:/data/app/x/base.apk\n",
                "settings get secure default_input_method": "com.android.inputmethod.latin/.LatinIME\n"})
    monkeypatch.setattr(bridge.subprocess, "run", fake)
    monkeypatch.setattr(bridge, "IME_SETTLE", 0)
    d = bridge.Device(SERIAL)
    call(d, "POST", "/text", {"text": "hello there"})
    call(d, "POST", "/text", {"text": "안녕 😀 'x'"})
    b64 = base64.b64encode("안녕 😀 'x'".encode()).decode()
    assert fake.shells() == ["input text 'hello%sthere'", "pm path com.android.adbkeyboard", "settings get secure default_input_method",
                             f"ime enable {bridge.ADB_IME}", f"ime set {bridge.ADB_IME}", f"am broadcast -a ADB_INPUT_B64 --es msg {b64}",
                             "ime set 'com.android.inputmethod.latin/.LatinIME'"]   # the owner's keyboard comes back


def test_non_ascii_typing_without_adb_keyboard_answers_a_clear_error(monkeypatch):
    monkeypatch.setattr(bridge.subprocess, "run", Adb({"get-state": "device\n"}))
    status, body = call(bridge.Device(SERIAL), "POST", "/text", {"text": "안녕"})
    assert status == 502 and "ADB Keyboard" in body["error"]


def test_open_looks_up_the_launcher_activity_and_starts_it(monkeypatch):
    fake = Adb({"get-state": "device\n", "resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER com.nope.app":
                "No activity found", "resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER com.android.settings":
                "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\ncom.android.settings/.Settings\n"})
    monkeypatch.setattr(bridge.subprocess, "run", fake)
    d = bridge.Device(SERIAL)
    assert call(d, "POST", "/open", {"package": "com.nope.app"})[0] == 404
    assert call(d, "POST", "/open", {"package": "x; rm -rf /"})[0] == 400 and call(d, "POST", "/open", {"package": "nodots"})[0] == 400
    assert call(d, "POST", "/open", {"package": "com.android.settings"})[0] == 200
    assert fake.shells()[-1] == "am start -n com.android.settings/.Settings"


def test_open_reports_an_am_start_error(monkeypatch):
    monkeypatch.setattr(bridge.subprocess, "run", Adb({"get-state": "device\n", "resolve-activity": "com.a.b/.Main\n",
                                                       "am start": "Error: Activity class {com.a.b/com.a.b.Main} does not exist."}))
    status, body = call(bridge.Device(SERIAL), "POST", "/open", {"package": "com.a.b"})
    assert status == 502 and "could not be opened" in body["error"]


def test_install_is_not_available_in_this_version(adb):
    status, body = call(bridge.Device(SERIAL), "POST", "/install", {"apk_url": "https://example.org/a.apk"})
    assert status == 501 and "not available" in body["error"] and adb.calls == []


def test_a_device_that_stops_answering_is_reconnected_once(monkeypatch):
    states = iter([(1, "error: device offline"), (0, "device\n")])

    class Flaky(Adb):
        def __call__(self, argv, **kw):
            if "get-state" in argv:
                self.calls.append((argv, kw))
                code, out = next(states)
                return subprocess.CompletedProcess(argv, code, out, "")
            return super().__call__(argv, **kw)
    fake = Flaky({"input tap": ""})
    monkeypatch.setattr(bridge.subprocess, "run", fake)
    assert call(bridge.Device(SERIAL), "POST", "/tap", {"x": 1, "y": 2})[0] == 200
    assert ["adb", "connect", SERIAL] in [a for a, _ in fake.calls]


def test_a_device_that_does_not_come_back_is_an_error_and_adb_timeouts_do_not_hang(monkeypatch):
    monkeypatch.setattr(bridge.subprocess, "run", Adb({"get-state": (1, "error: no devices")}))
    status, body = call(bridge.Device(SERIAL), "POST", "/tap", {"x": 1, "y": 2})
    assert status == 502 and "does not answer" in body["error"]
    monkeypatch.setattr(bridge.subprocess, "run", Adb({"get-state": "device\n", "uiautomator": subprocess.TimeoutExpired("adb", 20)}))
    status, body = call(bridge.Device(SERIAL), "GET", "/screen")
    assert status == 504 and "timed out" in body["error"]
    assert call(bridge.Device(SERIAL), "GET", "/nothing")[0] == 404


def test_status_reports_an_unreachable_device_without_failing(monkeypatch):
    monkeypatch.setattr(bridge.subprocess, "run", Adb({"get-state": (1, "error")}))
    status, body = call(bridge.Device(SERIAL), "GET", "/status")
    assert status == 200 and body["connected"] is False and body["device"] == SERIAL


# ---- typed text is never logged; the picture alone has no dump
SECRET_ASCII, SECRET_KOREAN = "Hunter2-correct-horse", "비밀번호-한글"


def test_the_bridge_never_logs_typed_text_or_the_token(adb, capsys):
    import threading
    import urllib.error
    import urllib.request

    adb.answers["pm path com.android.adbkeyboard"] = "package:/data/app/adbkeyboard.apk\n"
    device = bridge.Device(SERIAL)
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(device, TOKEN))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for text in (SECRET_ASCII, SECRET_KOREAN, "x" * 5000):     # typed, typed through the keyboard app, and refused (too long)
            req = urllib.request.Request(base + "/text", data=json.dumps({"text": text}).encode(), method="POST",
                                         headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=10).read()
            except urllib.error.HTTPError as e:
                assert e.code == 400
        bad = urllib.request.Request(base + "/text?text=" + SECRET_ASCII, data=b"{}", method="POST", headers={"Authorization": "Bearer nope"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad, timeout=10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    logged = capsys.readouterr().err
    assert "POST /text" in logged                                    # the request itself is logged: its path only
    for secret in (SECRET_ASCII, SECRET_KOREAN, TOKEN, "Bearer", "x" * 50):
        assert secret not in logged
    assert any(SECRET_ASCII in c for c in adb.shells())        # ... and it did reach the device


def test_the_picture_alone_is_a_screencap_without_a_dump(adb):
    adb.answers["exec-out screencap"] = b"\x89PNG\r\n" + b"x" * 20
    out = call(bridge.Device(SERIAL), "GET", "/screen?image=only")
    assert out[0] == 200 and set(out[1]) == {"png"} and not any("uiautomator" in c for c in adb.shells())
    adb.answers["exec-out screencap"] = b"not a picture"
    assert call(bridge.Device(SERIAL), "GET", "/screen?image=only")[0] == 502


def test_a_dump_with_no_window_is_an_error_not_a_screen(adb):
    """Play's first screen on the owner's redroid (2026-11-14): the activity is resumed with no window, uiautomator prints "ERROR: null root
    node returned by UiTestAutomationBridge" and writes no file, so `cat` fails."""
    adb.answers["uiautomator dump"] = (1, "ERROR: null root node returned by UiTestAutomationBridge.\n")
    adb.answers["cat /data/local/tmp/kelo-ui.xml"] = (1, "cat: /data/local/tmp/kelo-ui.xml: No such file or directory\n")
    status, body = call(bridge.Device(SERIAL), "GET", "/screen")
    assert status == 502 and "failed" in body["error"] or "could not be read" in body["error"]


def test_status_tells_the_model_and_whether_play_store_and_adb_keyboard_are_installed(monkeypatch):
    fake = Adb({"get-state": "device\n", "ro.build.version.release": "15\n", "ro.product.model": "Pixel 7\n", "wm size": "Physical size: 720x1280\n",
                "pm path com.android.vending": "package:/data/app/vending.apk\n", "pm path com.android.adbkeyboard": (1, "")})
    monkeypatch.setattr(bridge.subprocess, "run", fake)
    status, body = call(bridge.Device(SERIAL), "GET", "/status")
    assert status == 200 and body["connected"] is True and body["model"] == "Pixel 7" and body["android"] == "15"
    assert body["play_store"] is True and body["korean_input"] is False
    monkeypatch.setattr(bridge.subprocess, "run", Adb({"get-state": (1, "error")}))
    _, off = call(bridge.Device(SERIAL), "GET", "/status")
    assert off["connected"] is False and off["play_store"] is False and off["korean_input"] is False and off["model"] == ""


# ---- read-only questions for the browser sign-in check
def test_handlers_intent_and_app_links_parse_what_the_device_prints():
    assert bridge.parse_handlers("2 activities found:\n  org.chromium.webview_shell/.WebViewBrowserActivity\n  com.android.chrome/com.google.android.apps.chrome.Main\n"
                                 "  org.chromium.webview_shell/.Other\n") == ["org.chromium.webview_shell", "com.android.chrome"]
    assert bridge.parse_handlers("No activity found") == []
    top = ("TASK 1\n  ACTIVITY org.chromium.webview_shell/.Main abc pid=1\n    Intent {act=android.intent.action.VIEW dat=https://a.example.com/x?redirect_uri=a:/b&s=1}\n"
           "    intent={act=android.intent.action.VIEW dat=https://a.example.com/x?redirect_uri=a:/b&s=1 cmp=org.chromium.webview_shell/.Main}\n")
    assert bridge.parse_intent_data(top) == "https://a.example.com/x?redirect_uri=a:/b&s=1"
    assert bridge.parse_intent_data("nothing") == ""
    both = top.replace("ACTIVITY org", "ACTIVITY org").replace("    Intent {", "    launchedFromUid=1 launchedFromPackage=com.example.shop\n    Intent {", 1) + "  ACTIVITY other/.X 1\n    launchedFromPackage=evil.app\n"
    assert bridge.parse_caller(both) == "com.example.shop" and bridge.parse_caller(top) == "" and bridge.parse_caller("") == ""
    assert bridge.parse_app_links("com.example.shop:\n  Domain verification state:\n    app.example.com: verified\n    other.com: none\n    b.com: 1024\n") == ["app.example.com"]


def test_the_new_endpoints_ask_only_and_quote_the_address(adb):
    adb.answers["query-activities"] = "1 activities found:\n  com.example.shop/.Redirect\n"
    device = bridge.Device(SERIAL)
    status, got = call(device, "GET", "/handlers?url=com.example.shop.oauth%3A%2Foauth2redirect%3Fa%3D%27%3Brm")
    assert status == 200 and got == {"packages": ["com.example.shop"]}
    ran = [s for s in adb.shells() if "query-activities" in s][0]
    assert "-d 'com.example.shop.oauth:/oauth2redirect?a='\\''" in ran and "android.intent.action.VIEW" in ran
    assert call(device, "GET", "/handlers?url=not%20an%20address")[0] == 400 and call(device, "GET", "/handlers")[0] == 400
    assert call(device, "GET", "/applinks?package=a%3Bb")[0] == 400
    assert call(device, "GET", "/intent", auth=None)[0] == 401
    adb.answers["get-app-links"] = "app.example.com: verified\n"
    assert call(device, "GET", "/applinks?package=com.example.shop")[1] == {"hosts": ["app.example.com"]}
    assert not [s for s in adb.shells() if any(w in s for w in ("input ", "am start", "pm install"))]
