"""Chrome identity for the browser container (docs/browser.md "Identity"): Google Chrome, or a Chromium build presenting as it.

Google Chrome already is Chrome. Debian's Chromium is the same engine but names itself "Chromium" in the User-Agent Client Hints
(Sec-CH-UA, navigator.userAgentData). This small DevTools client, run next to the browser by the entrypoint, gives every target the
identity of the matching Google Chrome release, so headers and JavaScript agree:

  - the reduced User-Agent string Google Chrome sends on Linux ("X11; Linux x86_64"), also passed as --user-agent so requests made
    outside DevTools match;
  - User-Agent Client Hints: brands "Google Chrome" / "Chromium" / Chrome's GREASE brand in Chrome's order, the full version list,
    platform "Linux", this machine's architecture and bitness, desktop form factor, no model, no platform version.

The stated OS is the machine's own: Linux. That is what this machine is, and Google Chrome on Linux states exactly that. The CPU,
navigator.platform ("Linux x86_64") and everything else are the machine's own in every case.

The Chrome OS identity (a Chromebook: "X11; CrOS x86_64 14541.0.0", platform "Chrome OS" and the Chrome OS platform version of the
milestone from CHROME_OS_PLATFORM_VERSIONS) was tried on 2026-11-18 because some sites were thought to refuse desktop Linux by name.
A later check (the reference agent's Coupang chat, kelo-agents reviews of 2026-11-19) found that a signed-out Linux Chrome on a server is refused, and a
signed-in Google Chrome that states Linux passes: sign-in decides, not the stated OS. So Linux is stated again. The switch stays, off:

  - STATED_OS = "chromeos" states Chrome OS wherever the milestone has its row; `python -m assistant.browser_identity --stated-os`
    prints the value, and the entrypoint, the desktop rules and the server's own desktop expectation all read it from here;
  - a milestone with no row keeps the Linux identity (linux_override), so nothing is stated that was not read from a source.

It auto-attaches (flattened, waiting for the debugger) to every page, popup, cross-site iframe, dedicated/shared/service worker and
worklet before it runs, applies the override and lets it go. Nothing else is changed: no canvas, WebGL, font or timing tricks; the
language and time zone stay the user's own (--accept-lang, TZ and the server's per-tab overrides).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import platform
import sys
import urllib.request
from typing import Any

DEVTOOLS = "http://127.0.0.1:9222"

# Chromium's own GREASE rules (components/embedder_support/user_agent_utils.cc), seeded with the major version.
_GREASE_CHARS = (" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_")
_GREASE_VERSIONS = ("8", "99", "24")
_ORDERS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
# Attached targets that run scripts; the rest (e.g. "browser", "tab") need nothing.
_FRAME_TYPES = {"page", "iframe", "background_page", "app", "webview", "other"}

# The Chrome OS platform version that shipped with each Chrome milestone (the Chrome OS build's major.minor.bugfix, not Chrome's).
# Source: chromiumdash, https://chromiumdash.appspot.com/cros/fetch_serving_builds (read 2026-11-18): the Chrome 158 Chrome OS beta
# builds (158.0.8127.24) run ChromeOS 16868.13.0, the dev builds (158.0.8127.5) 16868.5.0. The stable build of a milestone can have a
# later patch; add a row per milestone from that same source, never a number that was not read from it.
CHROME_OS_PLATFORM_VERSIONS: dict[int, str] = {158: "16868.13.0"}
# The OS the browser states: "linux" (the machine as it is, the default) or "chromeos" (the Chromebook identity, off; see the docstring).
STATED_OS = "linux"
# The reduced platform of Chrome OS and of Linux, as Chromium sends them (components/embedder_support/user_agent_utils.cc).
_CHROME_OS_PLATFORM = "X11; CrOS x86_64 14541.0.0"
_LINUX_PLATFORM = "X11; Linux x86_64"


def _reduced(platform_part: str, major: int) -> str:
    return f"Mozilla/5.0 ({platform_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


def _chrome_os(major: int) -> bool:
    """True when the Chrome OS identity is stated for this milestone: the switch is on and the milestone has its row."""
    if STATED_OS not in ("linux", "chromeos"):
        raise ValueError(f"STATED_OS must be 'linux' or 'chromeos', not {STATED_OS!r}")
    return STATED_OS == "chromeos" and major in CHROME_OS_PLATFORM_VERSIONS


def user_agent(major: int) -> str:
    """The reduced User-Agent this browser states: Google Chrome's on Linux (default), on a Chromebook when the switch is on."""
    return _reduced(_CHROME_OS_PLATFORM, major) if _chrome_os(major) else linux_user_agent(major)


def linux_user_agent(major: int) -> str:
    """The reduced User-Agent of Google Chrome on Linux (the stated one by default)."""
    return _reduced(_LINUX_PLATFORM, major)


def brand_lists(full_version: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(brands, fullVersionList) exactly as Google Chrome of this version reports them."""
    major = int(full_version.split(".")[0])
    grease = f"Not{_GREASE_CHARS[major % 11]}A{_GREASE_CHARS[(major + 1) % 11]}Brand"
    gv = _GREASE_VERSIONS[major % 3]
    entries = [(grease, gv, f"{gv}.0.0.0"), ("Chromium", str(major), full_version), ("Google Chrome", str(major), full_version)]
    order = _ORDERS[major % 6]
    shuffled: list[tuple[str, str, str]] = [entries[0]] * 3
    for i, e in enumerate(entries):
        shuffled[order[i]] = e
    return ([{"brand": b, "version": v} for b, v, _ in shuffled], [{"brand": b, "version": f} for b, _, f in shuffled])


def machine_arch(machine: str | None = None) -> tuple[str, str]:
    """(architecture, bitness) as Chrome reports them for this CPU."""
    m = (machine or platform.machine()).lower()
    arch = "arm" if m.startswith(("arm", "aarch")) else "x86"
    bits = "64" if m in ("x86_64", "amd64", "aarch64", "arm64") or m.endswith("64") else "32"
    return arch, bits


def _params(full_version: str, machine: str | None, *, chrome_os: bool) -> dict[str, Any]:
    brands, full_list = brand_lists(full_version)
    arch, bits = machine_arch(machine)
    major = int(full_version.split(".")[0])
    if chrome_os:
        ua = _reduced(_CHROME_OS_PLATFORM, major)
        platform_name, platform_version = "Chrome OS", CHROME_OS_PLATFORM_VERSIONS[major]
    else:
        ua = linux_user_agent(major)
        platform_name, platform_version = "Linux", ""
    return {
        "userAgent": ua,
        "userAgentMetadata": {
            "brands": brands, "fullVersionList": full_list, "fullVersion": full_version,
            "platform": platform_name, "platformVersion": platform_version, "architecture": arch, "model": "", "mobile": False,
            "bitness": bits, "wow64": False, "formFactors": ["Desktop"],
        },
    }


def override(full_version: str, machine: str | None = None) -> dict[str, Any]:
    """Parameters for Emulation/Network.setUserAgentOverride: Chrome OS. KeyError when the milestone has no row in
    CHROME_OS_PLATFORM_VERSIONS (the platform version is never invented)."""
    major = int(full_version.split(".")[0])
    if major not in CHROME_OS_PLATFORM_VERSIONS:
        raise KeyError(f"no Chrome OS platform version for Chrome {major}: add its row to CHROME_OS_PLATFORM_VERSIONS")
    return _params(full_version, machine, chrome_os=True)


def linux_override(full_version: str, machine: str | None = None) -> dict[str, Any]:
    """Parameters for the Linux identity (what the browser states without the Chrome OS identity)."""
    return _params(full_version, machine, chrome_os=False)


def identity_for(full_version: str, machine: str | None = None) -> dict[str, Any]:
    """The identity this browser states: Linux (default), Chrome OS only with STATED_OS "chromeos" and the milestone's row."""
    major = int(full_version.split(".")[0])
    return override(full_version, machine) if _chrome_os(major) else linux_override(full_version, machine)


class Identity:
    """One DevTools connection to the browser; keeps every attached session alive so the overrides stay in force."""

    def __init__(self, ws: Any, params: dict[str, Any]) -> None:
        self.ws, self.params = ws, params
        self._ids = itertools.count(1)
        self._wait: dict[int, asyncio.Future[Any]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def send(self, method: str, params: dict[str, Any] | None = None, session: str | None = None) -> Any:
        mid = next(self._ids)
        msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._wait[mid] = fut
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, 10)

    async def auto_attach(self, session: str | None = None) -> None:
        await self.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}, session)

    async def attached(self, ev: dict[str, Any], parent: str | None = None) -> None:
        session, kind = ev["sessionId"], (ev.get("targetInfo") or {}).get("type", "")
        try:
            done = False
            if kind in _FRAME_TYPES:
                try:
                    await self.send("Emulation.setUserAgentOverride", self.params, session)
                    done = True
                except Exception:  # noqa: BLE001 - fall back to the Network domain below
                    pass
            if not done:
                try:
                    await self.send("Network.setUserAgentOverride", self.params, session)
                except Exception:  # noqa: BLE001 - a target without either domain (nothing to change there)
                    pass
            try:
                await self.auto_attach(session)   # its own iframes and workers
            except Exception:  # noqa: BLE001
                pass
        finally:
            if ev.get("waitingForDebugger"):
                try:
                    await self.send("Runtime.runIfWaitingForDebugger", None, session)
                except Exception:  # noqa: BLE001 - the target went away
                    pass

    async def run(self) -> None:
        reader = asyncio.create_task(self._read())
        await self.auto_attach()
        await reader

    async def _read(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg:
                fut = self._wait.pop(msg["id"], None)
                if fut is not None and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(str(msg["error"].get("message"))))
                    else:
                        fut.set_result(msg.get("result"))
            elif msg.get("method") == "Target.attachedToTarget":
                t = asyncio.create_task(self.attached(msg["params"], msg.get("sessionId")))   # sessionId: the parent that reported it
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)


def _version(base: str = DEVTOOLS) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base}/json/version", timeout=3) as r:
        return json.load(r)


async def main() -> int:
    import websockets

    info: dict[str, Any] = {}
    for _ in range(600):   # the browser starts right after this process
        try:
            info = await asyncio.to_thread(_version)
            break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.5)
    if not info:
        print("browser_identity: DevTools never came up", file=sys.stderr)
        return 1
    full = str(info.get("Browser", "")).split("/")[-1]
    if not full[:1].isdigit():
        print(f"browser_identity: unexpected version {info.get('Browser')!r}", file=sys.stderr)
        return 1
    params = identity_for(full)
    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None, ping_interval=None) as ws:
        print(f"browser_identity: presenting as Chrome {full} on {params['userAgentMetadata']['platform']} "
              f"({params['userAgentMetadata']['architecture']}/{params['userAgentMetadata']['bitness']})", file=sys.stderr)
        await Identity(ws, params).run()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--user-agent":   # for the entrypoint: the --user-agent flag for `chromium --version`
        print(identity_for(sys.argv[2])["userAgent"])
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--stated-os":   # for the entrypoint: linux or chromeos, the one switch (STATED_OS)
        print(STATED_OS)
        raise SystemExit(0)
    raise SystemExit(asyncio.run(main()))
