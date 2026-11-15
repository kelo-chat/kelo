"""KE2E wired in front of the app, end to end: a real server (uvicorn), a recording TCP proxy in the middle (standing in for
Cloudflare or anyone on the path) and the reference client (tests/e2e_client.py). The proxy's record must never hold a token, a
pairing code, a request body or an answer. Also: pairing with the key in the link, e2e-only devices, the top-level error code,
sealed clock and replay errors, streamed answers (SSE), streamed uploads (0x02), and the WebSocket gateway."""

import asyncio
import base64
import json
import time
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from assistant import e2e
from assistant.api.e2e_gate import E2EGateway
from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app

from e2e_client import E2EClient
from test_server import AUTH, TOKEN, Running

SECRET_TEXT = "what should I cook with the green apples tonight"
SECRET_REPLY = "bake them into a crumble with cinnamon"
SECRET_FILE = b"%PDF-1.4\n% file-secret-c4e2\n" + b"x" * 200_000


class Recorder:
    """A TCP proxy that keeps every byte it passes, both ways."""

    def __init__(self, upstream_port: int):
        self.upstream_port = upstream_port
        self.seen = bytearray()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._conn, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _conn(self, reader, writer):
        up_r, up_w = await asyncio.open_connection("127.0.0.1", self.upstream_port)

        async def pipe(src, dst):
            try:
                while data := await src.read(65536):
                    self.seen += data
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))

    def holds(self, text: str | bytes) -> bool:
        b = text.encode() if isinstance(text, str) else text
        return b in self.seen


def app_for(tmp_path, stream_fn=None):
    async def reply(provider, request, **kw):
        yield TextDelta(SECRET_REPLY)
        yield Done("stop")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "state" / "s.db"),
                               home_dir=str(home)), tools=[], stream_fn=stream_fn or reply)


async def call(http, client: E2EClient, method, path, headers=None, body=b"", **kw):
    """One KE2E call over httpx, the answer read as it streams. Returns (head, body, frames)."""
    sealed = client.seal(method, path, headers, body, **kw)
    async with http.stream("POST", "/e2e", content=sealed.envelope, headers={"content-type": e2e.CONTENT_TYPE}) as r:
        if r.status_code != 200:
            return {"s": r.status_code, "plain": True, "h": dict(r.headers)}, await r.aread(), []
        assert r.headers["content-type"] == e2e.CONTENT_TYPE and r.headers["cache-control"] == "no-store"
        frames = []
        async for chunk in r.aiter_raw():
            frames += sealed.opener.feed(chunk)
        sealed.opener.close()
    return sealed.opener.head, b"".join(frames[1:]), frames


async def test_pairing_and_calls_cross_the_path_only_as_ciphertext(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, Recorder(srv.port) as proxy, httpx.AsyncClient(base_url=proxy.url, timeout=30) as http:
        info = (await http.get("/api/e2e/info")).json()           # plaintext on purpose (1.5)
        pk = base64.urlsafe_b64decode(info["pk"] + "=" * (-len(info["pk"]) % 4))
        client = E2EClient(pk)
        assert info["kid"] == client.kid.hex() and info["fingerprint"] == client.fingerprint

        # the owner's tool asks for a pairing link through KE2E; the link carries the key in its fragment
        head, body, _ = await call(http, client, "POST", "/api/devices/pair", {"authorization": f"Bearer {TOKEN}",
                                                                               "content-type": "application/json"},
                                   json.dumps({"origin": "https://kelo.example"}).encode())
        out = json.loads(body)
        assert head["s"] == 200 and out["fingerprint"] == info["fingerprint"]
        frag = dict(p.split("=", 1) for p in urlsplit(out["link"]).fragment.split("&"))
        assert frag["pair"] == out["code"] and frag["k"] == info["pk"]

        # the phone pairs through KE2E with the pinned key, then signs in with its own token
        head, body, _ = await call(http, client, "POST", "/api/pair", {"content-type": "application/json"},
                                   json.dumps({"code": out["code"], "name": "Pixel"}).encode())
        token = json.loads(body)["token"]
        head, body, _ = await call(http, client, "GET", "/api/profile", {"authorization": f"Bearer {token}"})
        assert head["s"] == 200 and "agent_name" in json.loads(body)
        assert head["h"]["x-kelo-server"] and head["h"]["content-type"].startswith("application/json")

        # a streamed reply: the answer arrives frame by frame and never in the clear
        head, body, frames = await call(http, client, "POST", "/api/talk", {"authorization": f"Bearer {token}",
                                                                            "content-type": "application/json"},
                                        json.dumps({"text": SECRET_TEXT}).encode())
        assert head["s"] == 200 and head["h"]["content-type"].startswith("text/event-stream"), body
        assert SECRET_REPLY.encode() in body and len(frames) > 2

        # the device is e2e-only now: its token is refused in plaintext, with the code at the top level (5.10)
        async with httpx.AsyncClient(base_url=srv.url) as direct:   # not through the proxy: this one is plaintext on purpose
            r = await direct.get("/api/profile", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403 and r.json()["code"] == "e2e_required" and r.json()["detail"]["code"] == "e2e_required"
            # the master token is never e2e-only
            assert (await direct.get("/api/profile", headers=AUTH)).status_code == 200
            devices = (await direct.get("/api/devices", headers=AUTH)).json()["devices"]
            assert devices[0]["e2e"] is True

    for secret in (TOKEN, token, out["code"], SECRET_TEXT, SECRET_REPLY, "Pixel", "kelo.example"):
        assert not proxy.holds(secret), secret
    assert not proxy.holds(b"agent_name") and not proxy.holds(b"/api/talk") and not proxy.holds(b"/api/pair\"")
    assert proxy.holds(b"POST /e2e") and proxy.holds(b"/api/e2e/info")   # what stays visible: the outer path


async def test_a_streamed_upload_goes_through_in_frames(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, Recorder(srv.port) as proxy, httpx.AsyncClient(base_url=proxy.url, timeout=30) as http:
        info = (await http.get("/api/e2e/info")).json()
        client = E2EClient(base64.urlsafe_b64decode(info["pk"] + "=" * (-len(info["pk"]) % 4)))
        sealed, frames = client.seal_stream("PUT", "/api/uploads/report.pdf", {"authorization": f"Bearer {TOKEN}",
                                                                                "content-type": "application/pdf"}, SECRET_FILE)
        assert len(frames) == 4   # 200 KB in 64 KiB frames

        async def body():
            yield sealed.envelope
            for f in frames:
                yield f
        r = await http.post("/e2e", content=body(), headers={"content-type": e2e.CONTENT_TYPE})
        head, answer = e2e.open_response(sealed.response_key(), r.content)
        assert head["s"] == 200, answer
        assert json.loads(answer)["ok"] is True
        assert (tmp_path / "home" / json.loads(answer)["path"]).read_bytes() == SECRET_FILE
    assert not proxy.holds(b"file-secret-c4e2") and not proxy.holds(b"report.pdf")


async def test_errors_before_and_after_the_envelope_opens(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, timeout=30) as http:
        info = (await http.get("/api/e2e/info")).json()
        client = E2EClient(base64.urlsafe_b64decode(info["pk"] + "=" * (-len(info["pk"]) % 4)))
        # unknown kid: plaintext 409 with the code at the top level
        sealed = client.seal("GET", "/api/profile", kid=bytes(8))
        r = await http.post("/e2e", content=sealed.envelope, headers={"content-type": e2e.CONTENT_TYPE})
        assert r.status_code == 409 and r.json()["code"] == "e2e_key_changed"
        # does not open: plaintext 400, no detail
        bad = bytearray(client.seal("GET", "/api/profile").envelope)
        bad[-1] ^= 1
        r = await http.post("/e2e", content=bytes(bad), headers={"content-type": e2e.CONTENT_TYPE})
        assert r.status_code == 400 and r.content == b""
        assert (await http.get("/e2e")).status_code == 400
        # an old clock: SEALED 400 {"code": "e2e_clock", "now"} (5.13)
        head, body, _ = await call(http, client, "GET", "/api/profile", {"authorization": f"Bearer {TOKEN}"}, ts=int(time.time()) - 900)
        assert head["s"] == 400 and json.loads(body)["code"] == "e2e_clock" and abs(json.loads(body)["now"] - time.time()) < 5
        # a replay: SEALED 400 {"code": "e2e_replay"}
        sealed = client.seal("GET", "/api/profile", {"authorization": f"Bearer {TOKEN}"})
        for expect in (200, 400):
            r = await http.post("/e2e", content=sealed.envelope, headers={"content-type": e2e.CONTENT_TYPE})
            head, body = e2e.open_response(sealed.response_key(), r.content)
            assert head["s"] == expect
            sealed.opener = e2e.FrameOpener(sealed.response_key())
        assert json.loads(body) == {"code": "e2e_replay"}
        # an app error inside: its own status in frame 0, code at the top level
        head, body, _ = await call(http, client, "GET", "/api/profile")
        assert head["s"] == 401
        # a too old app inside KE2E: 426 from the version check, sealed
        head, body, _ = await call(http, client, "GET", "/api/profile", {"authorization": f"Bearer {TOKEN}",
                                                                         "x-kelo-app": "android/0.0.1+1"})
        assert head["s"] == 426 and json.loads(body)["code"] == "app_too_old"


async def test_rotating_the_key_makes_every_device_pair_again(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, timeout=30) as http:
        old = (await http.get("/api/e2e/info")).json()
        assert (await http.post("/api/e2e/rotate")).status_code == 401
        new = (await http.post("/api/e2e/rotate", headers=AUTH)).json()
        assert new["kid"] != old["kid"] and new["pair_again"] is True
        assert (await http.get("/api/e2e/info")).json()["kid"] == new["kid"]
        client = E2EClient(base64.urlsafe_b64decode(old["pk"] + "=" * (-len(old["pk"]) % 4)))
        r = await http.post("/e2e", content=client.seal("GET", "/api/profile").envelope, headers={"content-type": e2e.CONTENT_TYPE})
        assert r.status_code == 409 and r.json()["code"] == "e2e_key_changed"
    # the key survives a restart: the same file
    assert (tmp_path / "state" / "e2e" / "server.key").exists()
    app2 = app_for(tmp_path)
    async with Running(app2) as srv, httpx.AsyncClient(base_url=srv.url) as http:
        assert (await http.get("/api/e2e/info")).json()["kid"] == new["kid"]


def test_the_websocket_gateway_seals_both_ways_and_sends_a_head_first(tmp_path):
    inner = FastAPI()

    @inner.websocket("/echo")
    async def echo(ws: WebSocket):
        await ws.accept()
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("text") is not None:
                await ws.send_text("echo " + msg["text"])
            else:
                await ws.send_bytes(b"echo " + msg["bytes"])

    @inner.websocket("/refuse")
    async def refuse(ws: WebSocket):
        await ws.close(code=1008)

    key = e2e.ServerKey.load_or_create(tmp_path)
    app = E2EGateway(inner, e2e.E2EServer(key))
    client = E2EClient(key.public_bytes)
    with TestClient(app) as tc:
        sealed = client.seal("GET", "/echo")
        ch = sealed.ws_channel()
        with tc.websocket_connect("/e2e/ws") as ws:
            ws.send_bytes(sealed.envelope)
            assert json.loads(ch.open(ws.receive_bytes())) == {"s": 101, "h": {}}
            ws.send_bytes(ch.seal("hello"))
            assert ch.open(ws.receive_bytes()) == "echo hello"
            ws.send_bytes(ch.seal(b"\xff\xd8jpeg"))
            assert ch.open(ws.receive_bytes()) == b"echo \xff\xd8jpeg"
        # refused by the app: an error head, then 4401
        sealed = client.seal("GET", "/refuse")
        ch = sealed.ws_channel()
        with tc.websocket_connect("/e2e/ws") as ws:
            ws.send_bytes(sealed.envelope)
            assert json.loads(ch.open(ws.receive_bytes()))["s"] == 401
            msg = ws.receive()
            assert msg["type"] == "websocket.close" and msg["code"] == e2e.WS_CLOSE_UNAUTHORIZED
        # unknown kid -> 4409, junk -> 4400
        for env, code in ((client.seal("GET", "/echo", kid=bytes(8)).envelope, e2e.WS_CLOSE_KEY_CHANGED),
                          (b"junk", e2e.WS_CLOSE_BAD_ENVELOPE)):
            with tc.websocket_connect("/e2e/ws") as ws:
                ws.send_bytes(env)
                msg = ws.receive()
                assert msg["type"] == "websocket.close" and msg["code"] == code
