"""The Kelo server and the REAL key container together (keyring_service.py + keyring_client.py, docs/keyring.md): the server holds
no provider key, a chat turn and the model list reach the provider with the real key, and a restart of the key container is
unlocked again by the server. Only the provider is fake."""

import json
import socket
import sys
from pathlib import Path

import httpx

from assistant import keyring_service
from assistant.server import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_server import AUTH, Running, read_events, settings  # noqa: E402

TOKEN = "t" * 40
SECRET = "sk-REAL-SECRET-0042"
OPENAI_SSE = (b'data: {"choices":[{"delta":{"content":"hello through the keyring"}}]}\n\n'
              b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n'
              b"data: [DONE]\n\n")


class Provider:
    """The fake OpenAI behind the key container: records what really arrives."""

    def __init__(self):
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.path.endswith("/models"):   # a body still to be read, as a real provider's is
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  stream=httpx.ByteStream(json.dumps({"data": [{"id": "gpt-x"}]}).encode()))
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(OPENAI_SSE))
        return httpx.Response(404, stream=httpx.ByteStream(b'{"error": "no"}'))


def dns(host, port, *a):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def ring_app(state: Path, provider: Provider):
    return keyring_service.create_app(str(state), TOKEN, resolver=dns, transport=httpx.MockTransport(provider))


async def test_the_server_and_the_real_key_container_make_a_chat_turn_with_the_key_only_the_container_holds(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = Provider()
    ring = ring_app(tmp_path / "ring", provider)
    app = create_app(settings(keyring_url="http://keyring:9500", keyring_token=TOKEN, db_path=str(tmp_path / "s.db")), tools=[],
                     keyring_transport=httpx.ASGITransport(app=ring))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        keyring = app.state.appctx.keyring
        assert await keyring.unlock()                                    # what the start-up task does
        assert (await c.put("/api/providers/openai/key", json={"api_key": SECRET})).json() == {"ok": True}
        store = app.state.appctx.store
        assert "openai" not in store.key_providers()                     # not in the server's database
        assert SECRET.encode() not in (tmp_path / "s.db").read_bytes()
        assert SECRET.encode() not in (tmp_path / "ring" / "keyring.db").read_bytes()   # sealed on the container's disk too
        listing = (await c.get("/api/providers")).text
        assert SECRET not in listing

        assert (await c.get("/api/models", params={"provider": "openai"})).json()["models"] == ["gpt-x"]
        await c.put("/api/profile", json={"provider": "openai", "model": "gpt-x"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            events = await read_events(r)
        assert "hello through the keyring" in json.dumps(events)
        chat = [q for q in provider.seen if q.url.path.endswith("/chat/completions")]
        assert chat and chat[0].headers["authorization"] == f"Bearer {SECRET}" and chat[0].headers["host"] == "api.openai.com"
        assert not any(k.startswith("x-keyring-") for q in provider.seen for k in q.headers)

        # the key container restarts (a new process on the same disk: locked): the next call unlocks it again by itself
        ring2 = ring_app(tmp_path / "ring", provider)
        keyring._transport = httpx.ASGITransport(app=ring2)
        keyring._inner = None
        keyring._http = httpx.AsyncClient(base_url=keyring.url, transport=keyring._transport, trust_env=False)
        keyring._clients.clear()
        assert (await c.get("/api/models", params={"provider": "openai", "refresh": True})).json()["models"] == ["gpt-x"]
        assert provider.seen[-1].headers["authorization"] == f"Bearer {SECRET}"

        assert (await c.delete("/api/providers/openai/key")).status_code == 200
        assert "openai" not in keyring.known
