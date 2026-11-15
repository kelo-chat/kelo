"""The server's side of the key container (keyring_client.py, docs/keyring.md), against a fake keyring that follows the contract."""

import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from assistant.keyguard import KeyGuard, Locked
from assistant.keyring_client import (
    PLACEHOLDER, KeyringClient, KeyringError, KeyringTransport, host_entry, move_keys, ring_key_from,
)
from assistant.server import create_app
from assistant.store import Store

sys.path.insert(0, str(Path(__file__).parent))
from test_server import AUTH, Running, read_events, settings  # noqa: E402

TOKEN = "k" * 32
OPENAI_SSE = (b'data: {"choices":[{"delta":{"content":"hello from upstream"}}]}\n\n'
              b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n'
              b"data: [DONE]\n\n")


class FakeKeyring:
    """The keyring's contract (docs/keyring.md): /unlock, /entries, /rekey, and /fwd with the placeholder swapped for the secret."""

    def __init__(self, fail_put: str = ""):
        self.entries: dict[str, dict] = {}
        self.locked = True
        self.ring: str | None = None
        self.unlocks = 0
        self.forwarded: list[dict] = []   # what reached the "upstream": method, url, headers (secret in), body
        self.fail_put = fail_put
        self.rekeys: list[tuple[str, str]] = []
        self.transport = httpx.MockTransport(self.handle)

    def restart(self) -> None:
        self.locked = True

    def reply(self, status: int, body: dict) -> httpx.Response:
        return httpx.Response(status, json=body)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.headers.get("x-keyring-token") != TOKEN:
            return self.reply(401, {"code": "unauthorized"})
        if path == "/unlock":
            key = json.loads(request.content)["key"]
            if self.ring not in (None, key):
                return self.reply(409, {"code": "key_mismatch"})
            self.ring, self.locked = key, False
            self.unlocks += 1
            return self.reply(200, {"ok": True})
        if self.locked:
            return self.reply(423, {"code": "locked"})
        if path == "/rekey":
            body = json.loads(request.content)
            if body["old"] != self.ring:
                return self.reply(409, {"code": "key_mismatch"})
            self.ring = body["new"]
            self.rekeys.append((body["old"], body["new"]))
            return self.reply(200, {"ok": True, "entries": len(self.entries)})
        if path == "/entries" and request.method == "GET":
            return self.reply(200, {"entries": [{k: v for k, v in e.items() if k != "secret"} | {"name": n}
                                                for n, e in self.entries.items()]})
        if path.startswith("/entries/"):
            name = path.split("/", 2)[2]
            if request.method == "PUT":
                if name == self.fail_put:
                    return self.reply(500, {"code": "disk"})
                self.entries[name] = json.loads(request.content)
                return self.reply(200, {"ok": True})
            if request.method == "DELETE":
                return self.reply(200, {"ok": True, "removed": self.entries.pop(name, None) is not None})
        if path.startswith("/fwd/"):
            name, _, rest = path[5:].partition("/")
            entry = self.entries.get(name)
            if entry is None:
                return self.reply(404, {"code": "no_key"})
            upstream = request.headers["x-keyring-upstream"]
            headers = {k: v.replace(PLACEHOLDER, entry["secret"]) for k, v in request.headers.items()
                       if not k.startswith("x-keyring-") and k != "host"}
            url = f"{upstream}/{rest}" + (f"?{request.url.query.decode()}" if request.url.query else "")
            self.forwarded.append({"method": request.method, "url": url, "headers": headers, "body": request.content})
            if rest.endswith("models"):
                return self.reply(200, {"data": [{"id": "gpt-x"}]})
            return httpx.Response(200, content=OPENAI_SSE, headers={"content-type": "text/event-stream"})
        return self.reply(404, {"code": "nope"})


def client_of(fake: FakeKeyring, ring: bytes | None = b"r" * 32) -> KeyringClient:
    return KeyringClient("http://keyring:9500", TOKEN, lambda: ring, transport=fake.transport)


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


# ---- the transport ---------------------------------------------------------------------------------------------------------------
async def test_transport_rewrites_to_the_proxy_and_passes_the_body():
    fake = FakeKeyring()
    kr = client_of(fake)
    assert await kr.unlock()
    await kr.put("openai", "sk-REAL-KEY-1234", ["api.openai.com"], False, hint="1234")
    assert kr.has("openai") and kr.hints["openai"] == "1234"
    http = kr.client_for("openai", httpx.Timeout(10.0))
    r = await http.post("https://api.openai.com/v1/chat/completions?x=1", headers={"authorization": f"Bearer {PLACEHOLDER}"},
                        json={"model": "m"})
    assert r.status_code == 200
    seen = fake.forwarded[0]
    assert seen["url"] == "https://api.openai.com/v1/chat/completions?x=1"   # upstream + "/" + path from the /fwd/<name>/ URL
    assert seen["headers"]["authorization"] == "Bearer sk-REAL-KEY-1234"
    assert json.loads(seen["body"]) == {"model": "m"}
    assert kr.client_for("openai", httpx.Timeout(1.0)) is http   # one client per name


async def test_transport_sends_token_and_upstream_headers():
    got = {}

    def handler(request: httpx.Request) -> httpx.Response:
        got["url"], got["headers"] = str(request.url), dict(request.headers)
        return httpx.Response(200, json={})

    kr = KeyringClient("http://keyring:9500", TOKEN, lambda: None, transport=httpx.MockTransport(handler))
    await kr.client_for("custom", httpx.Timeout(5.0)).get("http://10.0.0.5:11434/v1/models")
    assert got["url"] == "http://keyring:9500/fwd/custom/v1/models"
    assert got["headers"]["x-keyring-token"] == TOKEN
    assert got["headers"]["x-keyring-upstream"] == "http://10.0.0.5:11434"


async def test_transport_unlocks_once_on_423_and_retries():
    fake = FakeKeyring()
    kr = client_of(fake)
    await kr.unlock()
    await kr.put("openai", "sk-REAL", ["api.openai.com"], False, hint="REAL")
    fake.restart()   # the keyring restarted: locked again, entries kept
    before = fake.unlocks
    r = await kr.client_for("openai", httpx.Timeout(5.0)).get("https://api.openai.com/v1/models",
                                                              headers={"authorization": f"Bearer {PLACEHOLDER}"})
    assert r.status_code == 200 and fake.unlocks == before + 1
    assert len(fake.forwarded) == 1


async def test_transport_gives_up_after_one_retry_when_the_ring_key_is_missing():
    fake = FakeKeyring()
    kr = client_of(fake, ring=None)   # Keyguard locked: nothing to send
    r = await kr.client_for("openai", httpx.Timeout(5.0)).get("https://api.openai.com/v1/models")
    assert r.status_code == 423 and fake.unlocks == 0


async def test_control_call_unlocks_on_423_and_errors_carry_the_code():
    fake = FakeKeyring()
    kr = client_of(fake)
    await kr.put("a", "secret-value", ["api.openai.com"], False, hint="alue")   # starts locked: unlocked on the way
    assert fake.unlocks == 1 and "a" in fake.entries
    with pytest.raises(KeyringError) as e:
        await KeyringClient("http://keyring:9500", "wrong", lambda: b"r" * 32, transport=fake.transport).refresh()
    assert e.value.code == "unauthorized" and e.value.status == 401
    dead = KeyringClient("http://keyring:9500", TOKEN, lambda: b"r" * 32,
                         transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    with pytest.raises(KeyringError) as e:
        await dead.put("a", "secret-value", ["h.example"], False, hint="x")
    assert e.value.code == "unreachable"


async def test_disabled_client_is_off():
    kr = KeyringClient("", "", lambda: b"x" * 32)
    assert not kr.enabled and not kr.has("openai")


# ---- the ring key and the entries ------------------------------------------------------------------------------------------------
def test_ring_key_is_32_bytes_differs_from_the_master_key_and_is_none_while_locked(tmp_path):
    guard = KeyGuard(tmp_path)
    ring = ring_key_from(guard)()
    assert ring is not None and len(ring) == 32 and ring != guard.key() and ring not in guard.key()
    assert ring_key_from(guard)() == ring   # the same key gives the same ring key

    class Locks:
        def key(self):
            raise Locked("locked")

    assert ring_key_from(Locks())() is None


def test_host_entry_marks_private_addresses_local(monkeypatch):
    import socket
    answers = {"api.openai.com": "104.18.7.192", "sneaky.example.com": "10.1.2.3"}
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [(2, 1, 6, "", (answers.get(h, "93.184.216.34"), 443))])
    assert host_entry("https://api.openai.com/v1") == (["api.openai.com"], False)
    assert host_entry("https://sneaky.example.com/v1") == (["sneaky.example.com"], True)   # a name that resolves to a private address
    assert host_entry("http://192.168.1.20:11434/v1") == (["192.168.1.20"], True)
    assert host_entry("http://localhost:1234/v1") == (["localhost"], True)
    assert host_entry("http://100.64.0.9/v1") == (["100.64.0.9"], True)
    assert host_entry("http://169.254.1.1/v1") == (["169.254.1.1"], True)
    assert host_entry("http://my-nas.local:8080/v1") == (["my-nas.local"], True)
    assert host_entry("http://box.lan/v1") == (["box.lan"], True)
    assert host_entry("http://x.home.arpa/v1") == (["x.home.arpa"], True)
    assert host_entry("http://[::1]:8000/v1") == (["::1"], True)
    assert host_entry("http://HOST.Example.COM") is not None and host_entry("http://HOST.Example.COM")[0] == ["host.example.com"]
    assert host_entry("") is None and host_entry("not a url") is None


# ---- moving the old keys ---------------------------------------------------------------------------------------------------------
async def test_move_keys_moves_provider_keys_and_leaves_system_keys(tmp_path):
    store = Store(tmp_path / "a.db")
    store.set_key("openai", "sk-OPENAI-AAAA")
    store.set_key("openrouter", "sk-OR-BBBB")
    store.set_key("push:install_id", "system-secret")
    fake = FakeKeyring()
    kr = client_of(fake)
    await kr.unlock()
    assert await move_keys(store, kr) == 2
    assert fake.entries["openai"]["secret"] == "sk-OPENAI-AAAA" and fake.entries["openai"]["hosts"] == ["api.openai.com"]
    assert fake.entries["openrouter"]["hint"] == "BBBB"
    assert store.key_providers() == {"push:install_id"}
    assert store.key("push:install_id") == "system-secret"
    assert await move_keys(store, kr) == 0   # nothing left


async def test_move_keys_leaves_a_key_that_failed_where_it_was(tmp_path):
    store = Store(tmp_path / "a.db")
    store.set_key("openai", "sk-OPENAI-AAAA")
    store.set_key("openrouter", "sk-OR-BBBB")
    fake = FakeKeyring(fail_put="openai")
    kr = client_of(fake)
    await kr.unlock()
    assert await move_keys(store, kr) == 1
    assert store.key_providers() == {"openai"} and store.key("openai") == "sk-OPENAI-AAAA"
    assert "openai" not in fake.entries and "openrouter" in fake.entries


# ---- through the app -------------------------------------------------------------------------------------------------------------
def app_with(fake: FakeKeyring, **kw):
    return create_app(settings(keyring_url="http://keyring:9500", keyring_token=TOKEN), tools=[], keyring_transport=fake.transport, **kw)


async def test_app_key_lives_in_the_keyring_and_a_chat_turn_goes_through_it(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake = FakeKeyring()
    app = app_with(fake)
    secret = "sk-REALSECRET-ab12"
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/providers/openai/key", json={"api_key": secret})).json() == {"ok": True}
        assert fake.entries["openai"]["secret"] == secret and fake.entries["openai"]["hosts"] == ["api.openai.com"]
        assert fake.entries["openai"]["local"] is False and fake.entries["openai"]["hint"] == "ab12"
        store = app.state.appctx.store
        assert "openai" not in store.key_providers()   # not in api_keys
        listing = await c.get("/api/providers")
        assert secret not in listing.text
        p = next(x for x in listing.json() if x["name"] == "openai")
        assert p["configured"] and p["key_source"] == "app" and p["key_hint"] == "ab12"

        assert (await c.get("/api/models", params={"provider": "openai"})).json()["models"] == ["gpt-x"]
        assert fake.forwarded[-1]["headers"]["authorization"] == f"Bearer {secret}"

        await c.put("/api/profile", json={"provider": "openai", "model": "gpt-x"})
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            events = await read_events(r)
        assert "hello from upstream" in json.dumps(events)
        chat = [f for f in fake.forwarded if f["url"].endswith("/chat/completions")]
        assert chat and chat[0]["url"].startswith("https://api.openai.com/") and chat[0]["headers"]["authorization"] == f"Bearer {secret}"

        assert (await c.delete("/api/providers/openai/key")).status_code == 200
        assert "openai" not in fake.entries
        assert (await c.delete("/api/providers/openai/key")).status_code == 404
        p = next(x for x in (await c.get("/api/providers")).json() if x["name"] == "openai")
        assert p["key_source"] is None


async def test_app_the_server_itself_only_sends_the_placeholder(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake = FakeKeyring()
    seen = []
    real = fake.handle

    def spy(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/fwd/"):
            seen.append(request.headers["authorization"])
        return real(request)

    fake.transport = httpx.MockTransport(spy)
    app = app_with(fake)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/providers/openai/key", json={"api_key": "sk-REALSECRET-ab12"})
        await c.get("/api/models", params={"provider": "openai"})
    assert seen == [f"Bearer {PLACEHOLDER}"]


async def test_app_old_database_keys_move_at_start(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake = FakeKeyring()
    s = settings(keyring_url="http://keyring:9500", keyring_token=TOKEN, db_path=str(tmp_path / "a.db"))
    Store(tmp_path / "a.db").set_key("openai", "sk-OLD-KEY-0001")
    app = create_app(s, tools=[], keyring_transport=fake.transport)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for _ in range(100):
            if "openai" in fake.entries:
                break
            import asyncio
            await asyncio.sleep(0.05)
        assert fake.entries["openai"]["secret"] == "sk-OLD-KEY-0001"
        assert "openai" not in app.state.appctx.store.key_providers()


async def test_app_changing_a_custom_endpoint_host_removes_its_entry():
    fake = FakeKeyring()
    app = app_with(fake)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/providers/custom/key", json={"api_key": "sk-x-12345678"})).status_code == 422   # no address yet
        assert (await c.put("/api/providers/custom/endpoint", json={"base_url": "http://192.168.1.5:11434/v1"})).status_code == 200
        assert (await c.put("/api/providers/custom/key", json={"api_key": "sk-x-12345678"})).status_code == 200
        assert fake.entries["custom"]["hosts"] == ["192.168.1.5"] and fake.entries["custom"]["local"] is True
        # the same host, another port or path: the entry stays
        assert (await c.put("/api/providers/custom/endpoint", json={"base_url": "http://192.168.1.5:8000/v1"})).status_code == 200
        assert "custom" in fake.entries
        # another host: the key was saved for the old one only
        assert (await c.put("/api/providers/custom/endpoint", json={"base_url": "http://10.9.9.9:8000/v1"})).status_code == 200
        assert "custom" not in fake.entries
        p = next(x for x in (await c.get("/api/providers")).json() if x["name"] == "custom")
        assert p["key_source"] is None


async def test_app_put_key_is_refused_when_the_keyring_is_away(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    down = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    app = create_app(settings(keyring_url="http://keyring:9500", keyring_token=TOKEN), tools=[], keyring_transport=down)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.put("/api/providers/openai/key", json={"api_key": "sk-REALSECRET-ab12"})
        assert r.status_code == 503
        assert app.state.appctx.store.key_providers() == set()   # no fall back to the database


async def test_app_keyguard_rotation_rekeys_the_keyring_first(tmp_path):
    """Turning Keyguard on changes the personal-data key: the keyring is re-sealed before, and a refused step puts it back."""
    from assistant.keyring_client import ring_of
    fake = FakeKeyring()
    app = create_app(settings(keyring_url="http://keyring:9500", keyring_token=TOKEN, db_path=str(tmp_path / "a.db")), tools=[],
                     keyring_transport=fake.transport)
    guard = app.state.appctx.store.keyguard
    old = guard.key()
    kr = app.state.appctx.keyring
    await kr.unlock()
    out = guard.start_enroll(7)
    new = out["key"].encode()
    pending = guard.pending_rotation(7, out["enrollment_id"], 1, b64(bytes([1]) + b"x" * 40))
    assert pending == (old, new)
    await kr.rekey(ring_of(pending[0]), ring_of(pending[1]))
    assert fake.ring == b64(ring_of(new))
    with pytest.raises(Exception):
        guard.pending_rotation(8, out["enrollment_id"], 1, b64(bytes([1]) + b"x" * 40))   # not this phone's
    assert urlsplit("http://keyring:9500").port == 9500
