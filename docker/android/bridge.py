#!/usr/bin/env python3
"""The Android bridge (docs/android-device.md): a small HTTP API in front of `adb`, for one device.

The server cannot reach the LAN, so adb lives in this sidecar (compose profile `android`). It listens on `android-net` only, answers
only to `Authorization: Bearer $ANDROID_BRIDGE_TOKEN` (at least 32 characters; it refuses to start with a shorter one), keeps one adb
connection to $ANDROID_DEVICE (`host:port`) and reconnects once when the device stops answering. Every adb call has a timeout and a
failure is answered as {"error": "..."}: nothing hangs. Standard library only.

    GET  /healthz                        ok (no token: the container's healthcheck)
    GET  /status                         {"connected", "device", "android", "model", "size": [w, h], "app": "<package/activity in front>",
                                          "play_store": bool (com.android.vending installed), "korean_input": bool (ADB Keyboard installed)}
    GET  /screen?image=0|1               {"xml": "<uiautomator dump>", "png": "<base64>"|null, "app"}
    GET  /screen?image=only              {"png": "<base64>"}: the picture alone, no dump (the browser view's frames)
    POST /tap {"x", "y"}   POST /swipe {"x1", "y1", "x2", "y2", "ms"}   POST /text {"text"}   POST /key {"key"}
    POST /open {"package"}               GET /apps -> {"items": [{"package", "label"}]}
    POST /install {"apk_url"}            not available in this version (501)
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

MIN_TOKEN = 32
DUMP_TIMEOUT = 20          # seconds: uiautomator dump
CALL_TIMEOUT = 10          # seconds: every other adb call
DUMP_PATH = "/data/local/tmp/kelo-ui.xml"
MAX_BODY = 64 * 1024
MAX_TEXT = 2000
MAX_APPS = 400
KEYS = {"back": 4, "home": 3, "enter": 66, "recent": 187, "delete": 67, "tab": 61}
ADB_IME = "com.android.adbkeyboard/.AdbIME"   # ADB Keyboard (github.com/senzhk/ADBKeyBoard): types any text sent by broadcast
IME_SETTLE = 0.5   # seconds for the keyboard switch to reach the focused field
PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
COMPONENT = re.compile(r"([A-Za-z][\w.]*)/([\w.$]+)")


class BridgeError(Exception):
    """What goes back to the server as {"error": message}."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ---- pure parts ------------------------------------------------------------------------------------------------------------------
def token_ok(header: str | None, token: str) -> bool:
    """`Authorization: Bearer <token>` against the configured token, in constant time. An empty or short token never matches."""
    if len(token) < MIN_TOKEN or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):].strip().encode(), token.encode())


def quote(text: str) -> str:
    """`text` as one single-quoted word for the device's shell (adb joins its arguments and the shell on the device splits them)."""
    return "'" + text.replace("'", "'\\''") + "'"


def ascii_typable(text: str) -> bool:
    """True when `input text` can type it: printable ASCII only, and no "%" (the command reads "%s" as a space, so a real "%s" would
    turn into one). Anything else goes through ADB Keyboard."""
    return bool(text) and all(0x20 <= ord(c) < 0x7F for c in text) and "%" not in text


def escape_input_text(text: str) -> str:
    """The argument of `input text` for ASCII `text`: spaces as %s, the rest quoted for the shell (backslash, quotes, $, &, |, ; ...)."""
    return quote(text.replace(" ", "%s"))


HOST_RE = re.compile(r"^[a-zA-Z0-9.-]+$")


def resolve_device(serial: str, adb_host: str = "") -> str:
    """The adb device address (host:port), taking ANDROID_ADB_HOST into account.

    When Kelo runs inside WSL 2 or a VM, localhost forwarding works Windows->WSL,
    not the other way. The Android emulator on the Windows host listens on loopback,
    so inside WSL 2 / container the bridge connects to the WSL default gateway
    (or mirrored networking 127.0.0.1) supplied by ANDROID_ADB_HOST.
    """
    host = (adb_host or "").strip()
    dev = (serial or "").strip()
    if not host or not dev:
        return dev
    h = host.split(":", 1)[0]
    if not HOST_RE.fullmatch(h):
        return dev
    if dev.startswith("127.0.0.1:") or dev.startswith("localhost:"):
        port = dev.split(":", 1)[1]
        return f"{h}:{port}"
    if dev.startswith(":") or dev.isdigit():
        port = dev.lstrip(":")
        return f"{h}:{port}"
    return dev


def package_of(component: str) -> str:
    m = COMPONENT.search(component or "")
    return m.group(1) if m else ""


def parse_size(text: str) -> list[int]:
    """`wm size` ("Physical size: 720x1280", maybe an "Override size:" line after it: that one is in force) -> [w, h]."""
    found = re.findall(r"(\d+)x(\d+)", text or "")
    return [int(found[-1][0]), int(found[-1][1])] if found else [0, 0]


def parse_front(text: str) -> str:
    """The package/activity in front, from `dumpsys window` lines (mCurrentFocus, mFocusedApp, topResumedActivity)."""
    for line in (text or "").splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line or "topResumedActivity" in line:
            m = COMPONENT.search(line)
            if m:
                return f"{m.group(1)}/{m.group(2)}"
    return ""


def parse_apps(text: str) -> list[dict[str, str]]:
    """`cmd package query-activities --brief` lines "package/activity" -> launchable apps (one per package, sorted). The device
    gives no label over adb, so the label is the package's last word, capitalised."""
    seen: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = COMPONENT.fullmatch(line.strip())
        if m and m.group(1) not in seen:
            seen[m.group(1)] = m.group(1).rsplit(".", 1)[-1].replace("_", " ").title()
    return [{"package": p, "label": seen[p]} for p in sorted(seen)][:MAX_APPS]


def int_args(body: dict[str, Any], *names: str) -> list[int]:
    out = []
    for n in names:
        v = body.get(n)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -10_000 <= v <= 10_000:
            raise BridgeError(f"{n} must be a number", 400)
        out.append(int(v))
    return out


# ---- the device ------------------------------------------------------------------------------------------------------------------
class Device:
    """One adb device, one call at a time (the screen has one state)."""

    def __init__(self, serial: str, *, adb: str = "adb"):
        self.serial, self.adb = serial, adb
        self.lock = threading.Lock()

    def run(self, args: list[str], timeout: int = CALL_TIMEOUT, *, binary: bool = False) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.adb, *args], capture_output=True, timeout=timeout, text=not binary, check=False,
                                  errors=None if binary else "replace")
        except subprocess.TimeoutExpired:
            raise BridgeError(f"adb timed out after {timeout} s", 504) from None
        except OSError as e:
            raise BridgeError(f"adb could not run: {e}") from None

    def state(self) -> str:
        try:
            r = self.run(["-s", self.serial, "get-state"], CALL_TIMEOUT)
        except BridgeError:
            return ""
        return (r.stdout or "").strip() if r.returncode == 0 else ""

    def connect(self) -> None:
        if ":" in self.serial:
            try:
                self.run(["connect", self.serial], CALL_TIMEOUT)
            except BridgeError:
                pass

    def ready(self) -> None:
        """Before each call: the device answers, else one reconnect, else an error."""
        if self.state() == "device":
            return
        self.connect()
        if self.state() != "device":
            raise BridgeError(f"the device {self.serial} does not answer (adb connect failed)")

    def shell(self, command: str, timeout: int = CALL_TIMEOUT, *, both: bool = False) -> str:
        """The command's output (`both`: its error output after it); an error when it exits non-zero."""
        r = self.run(["-s", self.serial, "shell", command], timeout)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            raise BridgeError(f"{command.split(' ', 1)[0]} failed: {out.strip()[:200] or 'exit ' + str(r.returncode)}")
        return out if both else r.stdout or ""

    # ---- the API
    def status(self) -> dict[str, Any]:
        try:
            self.ready()
        except BridgeError:
            return {"connected": False, "device": self.serial, "android": "", "size": [0, 0], "app": "", "model": "",
                    "play_store": False, "korean_input": False}
        return {"connected": True, "device": self.serial, "android": self.shell("getprop ro.build.version.release").strip(),
                "size": parse_size(self.shell("wm size")), "app": self.front(),
                "model": self.shell("getprop ro.product.model").strip(),
                "play_store": self.has_package("com.android.vending"), "korean_input": self.has_package("com.android.adbkeyboard")}

    def has_package(self, package: str) -> bool:
        """Whether `package` is installed (`pm path` prints "package:<apk>"; a missing one prints nothing and exits 1)."""
        try:
            return "package:" in self.shell(f"pm path {package}", both=True)
        except BridgeError:
            return False

    def front(self) -> str:
        try:
            return parse_front(self.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp|topResumedActivity'"))
        except BridgeError:
            return ""

    def screen(self, image: bool) -> dict[str, Any]:
        self.ready()
        self.shell(f"uiautomator dump {DUMP_PATH}", DUMP_TIMEOUT)    # through adb shell: as root inside the device it writes nothing
        xml = self.shell(f"cat {DUMP_PATH}", CALL_TIMEOUT)
        if "<hierarchy" not in xml:
            raise BridgeError("the screen could not be read (the dump came back empty)")
        png = None
        if image:
            r = self.run(["-s", self.serial, "exec-out", "screencap", "-p"], CALL_TIMEOUT, binary=True)
            if r.returncode == 0 and r.stdout.startswith(b"\x89PNG"):
                png = base64.b64encode(r.stdout).decode()
        return {"xml": xml, "png": png, "app": self.front()}

    def frame(self) -> dict[str, Any]:
        """The picture of the screen alone (no dump: fast enough for a live view)."""
        self.ready()
        r = self.run(["-s", self.serial, "exec-out", "screencap", "-p"], CALL_TIMEOUT, binary=True)
        if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
            raise BridgeError("the screen could not be captured")
        return {"png": base64.b64encode(r.stdout).decode()}

    def tap(self, x: int, y: int) -> dict[str, Any]:
        self.ready()
        self.shell(f"input tap {x} {y}")
        return {"ok": True}

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int) -> dict[str, Any]:
        self.ready()
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {max(50, min(ms, 5000))}")
        return {"ok": True}

    def text(self, text: str) -> dict[str, Any]:
        if not text or len(text) > MAX_TEXT:
            raise BridgeError(f"text must be 1 to {MAX_TEXT} characters", 400)
        self.ready()
        if ascii_typable(text):
            self.shell(f"input text {escape_input_text(text)}")
            return {"ok": True}
        if "package:" not in self.shell("pm path com.android.adbkeyboard", both=True):
            raise BridgeError("typing this text needs ADB Keyboard on the device (docs/android-device.md)")
        before = self.shell("settings get secure default_input_method").strip()
        self.shell(f"ime enable {ADB_IME}")
        self.shell(f"ime set {ADB_IME}")
        time.sleep(IME_SETTLE)
        try:   # base64 keeps every character (Korean, emoji, quotes) out of the shell's way
            self.shell("am broadcast -a ADB_INPUT_B64 --es msg " + base64.b64encode(text.encode()).decode())
        finally:
            if before and before != ADB_IME and before != "null":
                self.shell(f"ime set {quote(before)}")
        return {"ok": True}

    def key(self, key: str) -> dict[str, Any]:
        if key not in KEYS:
            raise BridgeError(f"key must be one of {', '.join(KEYS)}", 400)
        self.ready()
        self.shell(f"input keyevent {KEYS[key]}")
        return {"ok": True}

    def open(self, package: str) -> dict[str, Any]:
        """Opens the app's launcher screen: the launcher activity is looked up and started with `am start` (monkey exits with an error on
        redroid even when it works, so it is not used)."""
        if not PACKAGE.match(package or ""):
            raise BridgeError("package must be an Android package name (com.example.app)", 400)
        self.ready()
        try:
            out = self.shell(f"cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {package}")
        except BridgeError:
            out = ""
        found = [m for line in out.splitlines() if (m := COMPONENT.fullmatch(line.strip())) and m.group(1) == package]
        if not found:
            raise BridgeError(f"{package} is not installed or has no launcher screen", 404)
        started = self.shell(f"am start -n {found[-1].group(1)}/{found[-1].group(2)}")
        if re.search(r"^Error", started, re.M):
            raise BridgeError(f"{package} could not be opened: {started.strip()[:150]}")
        return {"ok": True}

    def apps(self) -> dict[str, Any]:
        self.ready()
        out = self.shell("cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER")
        return {"items": parse_apps(out)}


# ---- HTTP ------------------------------------------------------------------------------------------------------------------------
def handle(device: Device, token: str, method: str, target: str, auth: str | None, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """One request -> (HTTP status, JSON answer). Pure of sockets, so it can be tested."""
    parts = urlsplit(target)
    path, query = parts.path, parse_qs(parts.query)
    if method == "GET" and path == "/healthz":
        return 200, {"ok": True}
    if not token_ok(auth, token):
        return 401, {"error": "unauthorized"}
    try:
        with device.lock:
            if method == "GET" and path == "/status":
                return 200, device.status()
            if method == "GET" and path == "/screen":
                image = query.get("image", ["0"])[0]
                return 200, device.frame() if image == "only" else device.screen(image == "1")
            if method == "GET" and path == "/apps":
                return 200, device.apps()
            if method == "POST" and path == "/tap":
                return 200, device.tap(*int_args(body, "x", "y"))
            if method == "POST" and path == "/swipe":
                x1, y1, x2, y2 = int_args(body, "x1", "y1", "x2", "y2")
                return 200, device.swipe(x1, y1, x2, y2, int_args({"ms": body.get("ms", 300)}, "ms")[0])
            if method == "POST" and path == "/text":
                return 200, device.text(str(body.get("text") or ""))
            if method == "POST" and path == "/key":
                return 200, device.key(str(body.get("key") or ""))
            if method == "POST" and path == "/open":
                return 200, device.open(str(body.get("package") or ""))
            if method == "POST" and path == "/install":
                return 501, {"error": "installing apps through Kelo is not available in this version"}
    except BridgeError as e:
        return e.status, {"error": str(e)}
    return 404, {"error": "not found"}


def make_handler(device: Device, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "kelo-android-bridge"

        def log_message(self, fmt: str, *args: Any) -> None:   # never the token or typed text: the path only
            sys.stderr.write("%s %s\n" % (self.command, urlsplit(self.path).path))

        def _answer(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _run(self, method: str) -> None:
            body: dict[str, Any] = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    return self._answer(413, {"error": "request too large"})
                try:
                    got = json.loads(self.rfile.read(n) or b"{}")
                    body = got if isinstance(got, dict) else {}
                except ValueError:
                    return self._answer(400, {"error": "the body must be JSON"})
            try:
                status, payload = handle(device, token, method, self.path, self.headers.get("Authorization"), body)
            except Exception as e:  # noqa: BLE001 - nothing may hang or kill the server
                status, payload = 500, {"error": f"bridge error: {type(e).__name__}"}
            self._answer(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            self._run("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._run("POST")

    return Handler


def main() -> int:
    token = os.environ.get("ANDROID_BRIDGE_TOKEN", "").strip()
    raw_device = os.environ.get("ANDROID_DEVICE", "").strip()
    if not raw_device:
        print("ANDROID_DEVICE (host:port) is not set; not starting.", file=sys.stderr)
        return 2
    if len(token) < MIN_TOKEN:
        print(f"ANDROID_BRIDGE_TOKEN must be at least {MIN_TOKEN} characters; not starting.", file=sys.stderr)
        return 2
    adb_host = os.environ.get("ANDROID_ADB_HOST", "").strip()
    if adb_host and ":" not in adb_host:
        adb_host = f"{adb_host}:{os.environ.get('ANDROID_ADB_PORT', '5555').strip() or '5555'}"   # macOS sets the port separately
    serial = resolve_device(raw_device, adb_host)
    if not serial:
        print("ANDROID_DEVICE (host:port) is not set; not starting.", file=sys.stderr)
        return 2
    device = Device(serial)
    device.connect()
    server = ThreadingHTTPServer((os.environ.get("BRIDGE_HOST", "0.0.0.0"), int(os.environ.get("BRIDGE_PORT", "8090"))),
                                 make_handler(device, token))
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
