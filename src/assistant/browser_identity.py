"""Chrome identity for a Chromium build (browser container, BROWSER=chromium or an architecture without Google Chrome).

Google Chrome needs none of this: it already is Chrome. Debian's Chromium is the same engine but names itself "Chromium" in the
User-Agent Client Hints (Sec-CH-UA, navigator.userAgentData). This small DevTools client, run next to the browser by the entrypoint,
gives every target the identity of the matching Google Chrome release on this platform, so headers and JavaScript agree:

  - the reduced User-Agent string Chrome sends on Linux (also passed as --user-agent, so requests made outside DevTools match);
  - User-Agent Client Hints: brands "Google Chrome" / "Chromium" / Chrome's GREASE brand in Chrome's order, the full version list,
    platform "Linux", empty platform version and model, this machine's architecture and bitness, desktop form factor.

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


def user_agent(major: int) -> str:
    """The reduced User-Agent Google Chrome sends on every Linux machine (x86_64 and arm64 alike)."""
    return f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


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


def override(full_version: str, machine: str | None = None) -> dict[str, Any]:
    """Parameters for Emulation/Network.setUserAgentOverride."""
    brands, full_list = brand_lists(full_version)
    arch, bits = machine_arch(machine)
    return {
        "userAgent": user_agent(int(full_version.split(".")[0])),
        "userAgentMetadata": {
            "brands": brands, "fullVersionList": full_list, "fullVersion": full_version,
            "platform": "Linux", "platformVersion": "", "architecture": arch, "model": "", "mobile": False,
            "bitness": bits, "wow64": False, "formFactors": ["Desktop"],
        },
    }


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

    async def attached(self, ev: dict[str, Any]) -> None:
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
                t = asyncio.create_task(self.attached(msg["params"]))
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
    params = override(full)
    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None, ping_interval=None) as ws:
        print(f"browser_identity: presenting as Google Chrome {full} ({params['userAgentMetadata']['architecture']}"
              f"/{params['userAgentMetadata']['bitness']})", file=sys.stderr)
        await Identity(ws, params).run()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--user-agent":   # for the entrypoint: the --user-agent flag for `chromium --version`
        print(user_agent(int(sys.argv[2].split(".")[0])))
        raise SystemExit(0)
    raise SystemExit(asyncio.run(main()))
