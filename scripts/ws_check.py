"""Check that a reverse proxy passes WebSocket upgrades to Kelo (the browser live view needs them).

    ASSISTANT_TOKEN=... uv run python scripts/ws_check.py https://kelo.me.duckdns.org

Asks for a one-time live-view ticket for a tab that does not exist and opens the WebSocket with it. Kelo answers that over the
WebSocket ({"type":"ended","reason":"no_tab"}, close 4404), so no browser tab is opened or watched. The token is never printed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import websockets


async def main(base: str) -> int:
    token = os.environ.get("ASSISTANT_TOKEN", "")
    if not token:
        print("set ASSISTANT_TOKEN (the server token or a device token)")
        return 2
    base = base.rstrip("/")
    tab = "ws-check"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{base}/api/browser/sessions/{tab}/live-ticket", headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        print(f"could not get a ticket: HTTP {r.status_code}")
        return 1
    ticket = r.json()["ticket"]
    url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"/api/browser/sessions/{tab}/live?ticket={ticket}"
    try:
        async with websockets.connect(url, open_timeout=15) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), 10))
            try:
                await asyncio.wait_for(ws.recv(), 10)
            except websockets.ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
            else:
                code = None
    except websockets.InvalidStatus as e:
        print(f"websocket upgrade FAILED: the proxy answered HTTP {e.response.status_code} (turn on WebSocket support)")
        return 1
    except (OSError, asyncio.TimeoutError, websockets.InvalidHandshake) as e:
        print(f"websocket upgrade FAILED: {type(e).__name__}: {e}")
        return 1
    print(f"websocket upgrade OK (server said {first}, close code {code})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080")))
