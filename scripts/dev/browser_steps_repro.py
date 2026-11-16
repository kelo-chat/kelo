"""Real-browser check of the browser step fixes (docs: CHANGELOG, tests/test_browser_steps.py). Run INSIDE a throwaway container made
from the server image with the browser role running and this checkout's server/src mounted over /app/server/src:

    docker run -d --name kelotest-bf-1 --user 1000:1000 --shm-size 1g -e ROLE=browser -v "$PWD/server/src:/app/server/src:ro" assistant:local
    docker exec -i -e PYTHONPATH=/app/server/src kelotest-bf-1 python - C K F < scripts/dev/browser_steps_repro.py
    docker rm -f kelotest-bf-1

Scenarios (a test site is served by this script on 127.0.0.1:8099; nothing leaves the container):
  C  a click starts a navigation that never answers (phone view): the step must come back in about 50 s with the deadline text
  K  the page is busy for 20 s after a click and the owner takes control at 5 s: the step must come back in about 5 s with the
     "took over" text
  F  the page's main thread is blocked for 60 s; the owner takes control and taps: the tap must be answered `busy` within 3 s
  G  as F, but the tap comes right after taking control (before the picture is known to be stale): `busy` within 3 s as well
"""

import asyncio
import sys
import time

from assistant.agent import ToolResult, run_agent
from assistant.providers import Done, TextDelta, ToolCall
from assistant.tools.browser import BrowserSession, browser_owner, browser_tools
from assistant.tools.live import LiveHub

PAGES = {
    "/c": "<h1>Start</h1><form action='/hang'><button id=go>Go</button></form>",
    "/k": "<h1>Busy</h1><button id=go onclick=\"const t=Date.now();while(Date.now()-t<20000);\">Go</button>",
    "/f": "<h1>Blocked</h1><button id=go onclick=\"const t=Date.now();while(Date.now()-t<60000);\">Go</button>",
}


async def site(reader, writer):
    head = (await reader.readuntil(b"\r\n\r\n")).decode("latin1")
    path = head.split(" ", 2)[1].split("?")[0]
    if path == "/hang":
        await asyncio.sleep(3600)   # a navigation that never gets an answer
    body = PAGES.get(path, "<h1>?</h1>").encode()
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    await writer.drain()
    writer.close()


def scripted(name, args):
    calls = iter([[ToolCall("c1", name, args), Done("tool_calls")], [TextDelta("ok"), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(calls):
            yield ev
    return fn


async def call(tools, name, args="{}"):
    async def approve(req):
        return True
    start = time.monotonic()
    out = [e async for e in run_agent("p", "m", [{"role": "user", "content": "go"}], tools, stream_fn=scripted(name, args), approve=approve)
           if isinstance(e, ToolResult)]
    return time.monotonic() - start, out[0].output if out else ""


async def main(which):
    srv = await asyncio.start_server(site, "127.0.0.1", 8099)
    session = BrowserSession("127.0.0.1", 9222, allow_private=True)
    hub = LiveHub(session, reaper=False)
    tools = browser_tools(session)
    browser_owner.set("k")
    for name in which:
        url = {"C": "/c", "K": "/k", "F": "/f", "G": "/f"}[name]
        took, _ = await call(tools, "browser_open", f'{{"url":"http://127.0.0.1:8099{url}","view":"mobile"}}')
        print(f"{name}: open {took:.1f}s", flush=True)
        if name == "C":
            took, out = await call(tools, "browser_click", '{"selector":"#go"}')
            print(f"C: click returned after {took:.1f}s (want <= ~50): {out[:160]!r}", flush=True)
        elif name == "K":
            await hub.join("k", "v")
            task = asyncio.ensure_future(call(tools, "browser_click", '{"selector":"#go"}'))
            await asyncio.sleep(5)
            at = time.monotonic()
            hub.take("k", "dev")
            took, out = await task
            print(f"K: step ended {time.monotonic() - at:.1f}s after take-control (want <= ~5): {out[:120]!r}", flush=True)
            await hub.give_back("k", "dev")
        elif name in ("F", "G"):
            await hub.join("k", "v")
            await asyncio.wait_for(hub.wait_frame("k", 0, 10), 12)
            task = asyncio.ensure_future(call(tools, "browser_click", '{"selector":"#go"}'))
            await asyncio.sleep(4)
            hub.take("k", "dev")
            await asyncio.sleep(3.5 if name == "F" else 0.3)   # F: the picture is stale by now
            at = time.monotonic()
            out = await hub.input("k", "dev", {"type": "tap", "x": 100, "y": 100})
            print(f"{name}: owner tap answered after {time.monotonic() - at:.1f}s (want <= 3): {out} health={hub.health('k')}", flush=True)
            task.cancel()
        await session.rescue("k")
        try:
            await hub.give_back("k", "dev")
        except Exception:
            pass
    await hub.close()
    await session.close()
    srv.close()


asyncio.run(main(sys.argv[1:] or ["C", "K", "F"]))
