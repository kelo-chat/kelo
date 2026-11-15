#!/usr/bin/env python3
"""KE2E in front of the pretend server (desktop/tools/mock_server.py), for the iPhone simulator walk.

Apps from 0.9.0 talk only to servers that offer KE2E (spec 5.18), and the mock server speaks plaintext. This gateway runs the
server's own KE2E layer (src/assistant/api/e2e_gate.py E2EGateway, a fresh key each start) and forwards every opened inner
request to the mock unchanged; it also answers GET /api/e2e/info. The mock's answers (with its X-Kelo-Server header) go back
sealed. WebSockets (the live view) are not forwarded: the mock has none; the live view falls back to frame polling.

Run from the repository root (needs the server's environment):
    uv run python ios/tools/mock_e2e_gateway.py --port 8790 --upstream 8789
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx
import uvicorn
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from assistant import e2e
from assistant.api.e2e_gate import E2EGateway

HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "proxy-connection", "content-length", "host"}


def make_app(upstream: str, key: e2e.ServerKey) -> Any:
    client = httpx.AsyncClient(base_url=upstream, timeout=120)

    async def forward(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif m["type"] == "lifespan.shutdown":
                    await client.aclose()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4404})
            return
        if scope["path"] == "/api/e2e/info":
            body = json.dumps(key.public_info()).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json"), (b"x-kelo-server", b"0.9.0+1")]})
            await send({"type": "http.response.body", "body": body})
            return
        body = bytearray()
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        headers = [(k.decode(), v.decode()) for k, v in scope.get("headers", []) if k.decode().lower() not in HOP]
        url = scope["path"] + ("?" + scope["query_string"].decode() if scope.get("query_string") else "")
        req = client.build_request(scope["method"], url, headers=headers, content=bytes(body))
        r = await client.send(req, stream=True)
        try:
            out = [(k.encode(), v.encode()) for k, v in r.headers.items() if k.lower() not in HOP]
            await send({"type": "http.response.start", "status": r.status_code, "headers": out})
            async for chunk in r.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await r.aclose()

    return E2EGateway(forward, e2e.E2EServer(key))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--upstream", type=int, required=True, help="the mock server's port on 127.0.0.1")
    args = ap.parse_args()
    key = e2e.ServerKey(X25519PrivateKey.generate())
    print(f"KE2E gateway on 127.0.0.1:{args.port} -> 127.0.0.1:{args.upstream}, fingerprint {key.fingerprint}", flush=True)
    uvicorn.run(make_app(f"http://127.0.0.1:{args.upstream}", key), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
