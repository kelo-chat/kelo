"""KE2E in front of the whole app (docs/security/e2e-and-phone-key.md section 1 and 5): one ASGI layer, the outermost.

    POST /e2e      Content-Type application/x-kelo-e2e; body = a request envelope (0x01, or 0x02 with the body in frames).
                   The inner request runs through the normal app (same routes, auth, rate limits, version check); its answer goes
                   back as response frames in a 200 (Cache-Control: no-store), streamed as it is produced (server-sent events: one
                   frame per piece the app sends).
    GET  /e2e/ws   WebSocket; the first client message is the envelope whose inner `p` is the WebSocket path. Then the server's
                   first message is the head {"s": 101, "h": {}} (or an error head, then a close 4400/4401/4409), and every message
                   is one sealed frame each way, its first plaintext byte the type (0x01 text, 0x02 binary).

Errors before the envelope opens: unknown kid -> plaintext 409 {"code": "e2e_key_changed"}; anything that does not open -> plaintext
400 with no detail. Clock or replay -> SEALED 400 {"code": "e2e_clock", "now": <unix>} / {"code": "e2e_replay"} (spec 5.13).

The inner scope carries `scope["kelo_e2e"] = {"kid": ...}`; AuthGuard reads it (an e2e-only device is refused without it, and a
device's first KE2E call marks it e2e-only). Only the inner request's allowed headers (e2e.INNER_HEADERS, x-kelo-*) reach the app;
the outer request's own headers (cf-connecting-ip for the brute-force guard, host) are kept underneath.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import unquote, urlsplit

from .. import e2e

log = logging.getLogger("uvicorn.error")

MAX_BUFFERED = 32 * 1024 * 1024   # a 0x01 envelope is held whole; larger bodies use 0x02 (streamed)
# outer headers the app may still see; cf-ray: the outer request came through Cloudflare, whose 100 MB body limit applies to the
# envelope, so the inner upload route must keep its per-request limit (api/files.py one_request_limit)
OUTER_KEEP = (b"host", b"cf-connecting-ip", b"x-forwarded-for", b"user-agent", b"cf-ray")


def _plain(status: int, body: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else b""
    headers = [(b"cache-control", b"no-store"), (b"content-length", str(len(data)).encode())]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    return ({"type": "http.response.start", "status": status, "headers": headers},
            {"type": "http.response.body", "body": data})


def inner_scope(outer: dict[str, Any], inner: e2e.InnerRequest, kid: str, ws: bool = False) -> dict[str, Any]:
    parts = urlsplit(inner.path)
    keep = [(k, v) for k, v in outer.get("headers") or [] if k in OUTER_KEEP]
    headers = keep + [(k.encode("latin-1"), v.encode("latin-1")) for k, v in inner.headers.items()]
    if inner.body and not ws:
        headers.append((b"content-length", str(len(inner.body)).encode()))
    # ASGI: "path" is the DECODED path, "raw_path" the bytes as sent. Leaving it encoded broke every path segment with an escaped
    # character: a job's tab "task:w1" is sent as "task%3Aw1", and the live view looked for a tab named "task%3Aw1" (none: "closed")
    scope = {**outer, "path": unquote(parts.path), "raw_path": parts.path.encode("latin-1"), "query_string": parts.query.encode("latin-1"),
             "headers": headers, "kelo_e2e": {"kid": kid}}
    if ws:
        scope.update(type="websocket", scheme="wss" if outer.get("scheme") == "wss" else outer.get("scheme", "ws"))
        scope.pop("method", None)
    else:
        scope["method"] = inner.method
    return scope


class E2EGateway:
    """The outermost ASGI layer: /e2e and /e2e/ws are opened here; every other request passes through untouched."""

    def __init__(self, app: Any, server: e2e.E2EServer | None = None):
        self.app = app
        self.server = server

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = scope.get("path")
        if self.server is not None and scope.get("type") == "http" and path == "/e2e":
            return await self._http(scope, receive, send)
        if self.server is not None and scope.get("type") == "websocket" and path == "/e2e/ws":
            return await self._ws(scope, receive, send)
        return await self.app(scope, receive, send)

    # ---- HTTP -------------------------------------------------------------------------------------------------------------------
    async def _http(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert self.server is not None
        if scope.get("method") != "POST" or scope.get("query_string"):
            return await self._send(send, *_plain(400))
        buf = bytearray()
        more = True
        version = None
        # read until the envelope's head is complete (all of a 0x01 envelope; the head of a 0x02 one)
        while True:
            if version is None and buf:
                version = buf[0]
            if version == e2e.ENVELOPE_VERSION_STREAM:
                try:
                    need = e2e.stream_head_length(bytes(buf[:e2e.STREAM_HEAD_FIXED]))
                except e2e.BadEnvelope:
                    return await self._send(send, *_plain(400))
                if need is not None and len(buf) >= need:
                    break
            elif version is not None and version != e2e.ENVELOPE_VERSION:
                return await self._send(send, *_plain(400))
            if not more:
                break
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            buf += msg.get("body", b"")
            more = msg.get("more_body", False)
            if len(buf) > MAX_BUFFERED:
                return await self._send(send, *_plain(413))
        try:
            if version == e2e.ENVELOPE_VERSION_STREAM:
                need = e2e.stream_head_length(bytes(buf[:e2e.STREAM_HEAD_FIXED])) or 0
                if len(buf) < need:
                    raise e2e.BadEnvelope("short head")
                opened = self.server.open_stream_head(bytes(buf[:need]))
                rest = bytes(buf[need:])
            else:
                opened = self.server.open_request(bytes(buf))
                rest = b""
        except e2e.UnknownKid:
            return await self._send(send, *_plain(409, {"code": "e2e_key_changed",
                                                        "detail": "this server's key changed; pair the app again"}))
        except e2e.Rejected as r:
            body = {"code": "e2e_clock", "now": int(time.time())} if isinstance(r, e2e.StaleTimestamp) else {"code": "e2e_replay"}
            return await self._sealed_error(send, r.opened, 400, body)
        except e2e.E2EError:
            return await self._send(send, *_plain(400))

        inner = opened.inner
        sealer = opened.response_sealer()
        started = False
        finished = False

        if version == e2e.ENVELOPE_VERSION_STREAM:
            body_reader = opened.body_opener()
            queue: asyncio.Queue[tuple[bytes, bool] | None] = asyncio.Queue()

            async def pump() -> None:   # outer chunks -> opened body pieces, as they come
                nonlocal more
                pending = rest
                try:
                    while True:
                        for piece in body_reader.feed(pending):
                            await queue.put((piece, True))
                        if body_reader.done:
                            await queue.put((b"", False))
                            while (await receive())["type"] != "http.disconnect":   # then only the outer disconnect matters
                                pass
                            await queue.put(None)
                            return
                        if not more:
                            body_reader.close()
                        msg = await receive()
                        if msg["type"] == "http.disconnect":
                            await queue.put(None)
                            return
                        pending = msg.get("body", b"")
                        more = msg.get("more_body", False)
                except e2e.E2EError:
                    await queue.put(None)

            pump_task = asyncio.ensure_future(pump())

            async def inner_receive() -> dict[str, Any]:
                got = await queue.get()
                if got is None:
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": got[0], "more_body": got[1]}
        else:
            pump_task = None
            sent_body = False

            async def inner_receive() -> dict[str, Any]:
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": inner.body, "more_body": False}
                while True:   # after the body: only a disconnect of the outer request
                    msg = await receive()
                    if msg["type"] == "http.disconnect":
                        return msg

        async def inner_send(msg: dict[str, Any]) -> None:
            nonlocal started, finished
            if msg["type"] == "http.response.start":
                started = True
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", e2e.CONTENT_TYPE.encode()), (b"cache-control", b"no-store"),
                                        (b"x-content-type-options", b"nosniff")]})
                heads = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in msg.get("headers", [])]
                await send({"type": "http.response.body", "body": sealer.seal(e2e.response_head(msg["status"], heads)),
                            "more_body": True})
            elif msg["type"] == "http.response.body" and not finished:
                last = not msg.get("more_body", False)
                frames = b"".join(sealer.seal_chunks(msg.get("body", b""), last=last))
                finished = last
                await send({"type": "http.response.body", "body": frames, "more_body": not last})

        inner_scope_ = inner_scope(scope, inner, opened_kid(self.server))
        try:
            await self.app(inner_scope_, inner_receive, inner_send)
        finally:
            if pump_task is not None:
                pump_task.cancel()
        if started and not finished:   # the app ended without a last piece: close the stream properly
            await send({"type": "http.response.body", "body": sealer.finish(), "more_body": False})

    async def _sealed_error(self, send: Any, opened: e2e.OpenedRequest, status: int, body: dict[str, Any]) -> None:
        sealer = opened.response_sealer()
        data = json.dumps(body).encode()
        frames = sealer.seal(e2e.response_head(status, {"content-type": "application/json"})) + sealer.seal(data, last=True)
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", e2e.CONTENT_TYPE.encode()), (b"cache-control", b"no-store"),
                                (b"content-length", str(len(frames)).encode())]})
        await send({"type": "http.response.body", "body": frames})

    @staticmethod
    async def _send(send: Any, start: dict[str, Any], body: dict[str, Any]) -> None:
        await send(start)
        await send(body)

    # ---- WebSocket --------------------------------------------------------------------------------------------------------------
    async def _ws(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert self.server is not None
        msg = await receive()
        if msg["type"] != "websocket.connect":
            return
        await send({"type": "websocket.accept"})
        first = await receive()
        if first["type"] != "websocket.receive" or not first.get("bytes"):
            await send({"type": "websocket.close", "code": e2e.WS_CLOSE_BAD_ENVELOPE})
            return
        try:
            opened = self.server.open_request(first["bytes"])
        except e2e.UnknownKid:
            await send({"type": "websocket.close", "code": e2e.WS_CLOSE_KEY_CHANGED})
            return
        except e2e.E2EError:
            await send({"type": "websocket.close", "code": e2e.WS_CLOSE_BAD_ENVELOPE})
            return
        channel = opened.ws_channel()
        state = {"accepted": False, "closed": False}

        async def inner_receive() -> dict[str, Any]:
            if not state.get("connected"):
                state["connected"] = True
                return {"type": "websocket.connect"}
            while True:
                m = await receive()
                if m["type"] == "websocket.disconnect":
                    return m
                if m["type"] != "websocket.receive" or m.get("bytes") is None:
                    continue   # a plaintext text message is not part of the protocol
                try:
                    payload = channel.open(m["bytes"])
                except e2e.E2EError:
                    await send({"type": "websocket.close", "code": e2e.WS_CLOSE_BAD_ENVELOPE})
                    state["closed"] = True
                    return {"type": "websocket.disconnect", "code": e2e.WS_CLOSE_BAD_ENVELOPE}
                return {"type": "websocket.receive", **({"text": payload} if isinstance(payload, str) else {"bytes": payload})}

        async def inner_send(m: dict[str, Any]) -> None:
            if state["closed"]:
                return
            if m["type"] == "websocket.accept":
                state["accepted"] = True
                await send({"type": "websocket.send", "bytes": channel.seal(json.dumps({"s": 101, "h": {}}))})
            elif m["type"] == "websocket.send":
                data = m.get("text") if m.get("text") is not None else m.get("bytes", b"")
                await send({"type": "websocket.send", "bytes": channel.seal(data)})
            elif m["type"] == "websocket.close":
                state["closed"] = True
                code = int(m.get("code") or 1000)
                if not state["accepted"]:   # refused before accepting: an error head, then 4401 (not signed in / ticket refused)
                    await send({"type": "websocket.send", "bytes": channel.seal(json.dumps({"s": 401, "h": {}}))})
                    code = e2e.WS_CLOSE_UNAUTHORIZED
                await send({"type": "websocket.close", "code": code})
            elif m["type"] == "websocket.http.response.start":   # a denial response: same as a refusal
                state["closed"] = True
                await send({"type": "websocket.send",
                            "bytes": channel.seal(json.dumps({"s": int(m.get("status") or 403), "h": {}}))})
                await send({"type": "websocket.close", "code": e2e.WS_CLOSE_UNAUTHORIZED})

        try:
            await self.app(inner_scope(scope, opened.inner, opened_kid(self.server), ws=True), inner_receive, inner_send)
        except Exception:  # noqa: BLE001 - the socket closes; the log has the reason (never a frame's content)
            log.exception("KE2E WebSocket ended with an error")
            if not state["closed"]:
                await send({"type": "websocket.close", "code": 1011})
            return
        if not state["closed"]:
            await send({"type": "websocket.close", "code": 1000})


def opened_kid(server: e2e.E2EServer) -> str:
    return server.key.kid_hex
