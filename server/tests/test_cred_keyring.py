"""Tests for connector API keys in the key container (cred_keyring.py, docs/keyring.md, backlog 61c)."""

import base64
import functools
import json
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from assistant import cred_keyring, keyring_service
from assistant.cred_keyring import RESERVED, entry_name, keyring_ok, slot_of, via
from assistant.keyring_client import PLACEHOLDER, KeyringClient
from assistant.netguard import fetch
from assistant.store import Store
from assistant.tools.connect_tools import connect_tools

TOKEN = "t" * 40
K1 = base64.b64encode(b"1" * 32).decode()


def dns(ip="93.184.216.34"):
    return lambda host, port, *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def make_ring(state_dir: Path, upstream: Any, token: str = TOKEN, ip: str = "93.184.216.34"):
    return keyring_service.create_app(str(state_dir), token, resolver=dns(ip), transport=httpx.MockTransport(upstream))


async def test_keyring_service_connector_entry_and_streaming(tmp_path):
    seen = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/stream"):
            async def s():
                yield b"chunk1"
                yield b"chunk2"
            return httpx.Response(200, content=s())
        body = json.dumps({"echo": request.headers.get("x-api-key", "") or request.url.query.decode()}).encode()
        h = {"content-type": "application/json", "x-echo": request.headers.get("x-api-key", "") or "none"}
        return httpx.Response(200, headers=h, stream=httpx.ByteStream(body))

    ring = make_ring(tmp_path / "ring", upstream)
    ring_transport = httpx.ASGITransport(app=ring)
    async with httpx.AsyncClient(transport=ring_transport, base_url="http://keyring:9500", headers={"x-keyring-token": TOKEN}) as rc:
        assert (await rc.post("/unlock", json={"key": K1})).status_code == 200

        # Put connector entry with slot header "x-api-key"
        res = await rc.put("/entries/cred:test-hdr", json={
            "secret": "my-secret-key-12345",
            "kind": "connector",
            "hosts": ["api.example.com"],
            "local": False,
            "paths": [],
            "hint": "2345",
            "slot": {"header": "x-api-key"},
        })
        assert res.status_code == 200

        # Placeholder in x-api-key is replaced with real secret
        r = await rc.post("/fwd/cred:test-hdr/v1/test", headers={
            "x-keyring-upstream": "https://api.example.com",
            "x-api-key": PLACEHOLDER,
        })
        assert r.status_code == 200
        assert seen[-1].headers["x-api-key"] == "my-secret-key-12345"

        # The fake service echoes the key in body and in header -> answer shows [redacted]
        assert "[redacted]" in r.text
        assert "my-secret-key-12345" not in r.text
        assert r.headers.get("x-echo") == "[redacted]"
        assert int(r.headers["content-length"]) == len(r.content)

        # Placeholder in authorization for that entry -> 400 bad_placement
        r_bad = await rc.post("/fwd/cred:test-hdr/v1/test", headers={
            "x-keyring-upstream": "https://api.example.com",
            "authorization": f"Bearer {PLACEHOLDER}",
        })
        assert r_bad.status_code == 400
        assert r_bad.json()["code"] == "bad_placement"

        # Put connector entry with slot param "api_key"
        res2 = await rc.put("/entries/cred:test-param", json={
            "secret": "param-secret-key-999",
            "kind": "connector",
            "hosts": ["api.example.com"],
            "local": False,
            "paths": [],
            "hint": "-999",
            "slot": {"param": "api_key"},
        })
        assert res2.status_code == 200

        # ?api_key=KELO_KEYRING_SECRET works
        r_param = await rc.get(f"/fwd/cred:test-param/v1/test?api_key={PLACEHOLDER}", headers={
            "x-keyring-upstream": "https://api.example.com",
        })
        assert r_param.status_code == 200
        assert "api_key=param-secret-key-999" in seen[-1].url.query.decode()

        # ?token=... -> 400
        r_param_bad = await rc.get(f"/fwd/cred:test-param/v1/test?token={PLACEHOLDER}", headers={
            "x-keyring-upstream": "https://api.example.com",
        })
        assert r_param_bad.status_code == 400
        assert r_param_bad.json()["code"] == "bad_placement"

        # Chat entry still streams as before
        res3 = await rc.put("/entries/test-chat", json={
            "secret": "chat-secret-abc",
            "kind": "chat",
            "hosts": ["api.example.com"],
            "local": False,
            "paths": [],
            "hint": "-abc",
        })
        assert res3.status_code == 200
        async with rc.stream("POST", "/fwd/test-chat/stream", headers={
            "x-keyring-upstream": "https://api.example.com",
            "authorization": f"Bearer {PLACEHOLDER}",
        }) as stream_resp:
            assert stream_resp.status_code == 200
            chunks = [chunk async for chunk in stream_resp.aiter_bytes()]
            assert b"".join(chunks) == b"chunk1chunk2"


def test_keyring_ok():
    base = {"name": "test-service", "kind": "api", "hosts": ["api.example.com"], "placement": {"type": "bearer_header"}}
    valid_key = "plain-api-key-12345"

    # bearer/custom/query yes
    assert keyring_ok(base, valid_key) is True
    assert keyring_ok({**base, "placement": {"type": "custom_header", "header": "X-API-Key"}}, valid_key) is True
    assert keyring_ok({**base, "placement": {"type": "query_param", "param": "key"}}, valid_key) is True

    # basic_auth no
    assert keyring_ok({**base, "placement": {"type": "basic_auth"}}, valid_key) is False
    # url_path_segment no
    assert keyring_ok({**base, "placement": {"type": "url_path_segment"}}, valid_key) is False
    # local origin no
    assert keyring_ok({**base, "placement": {"type": "bearer_header", "origin": "http://192.168.1.10"}}, valid_key) is False
    # JSON secret no
    assert keyring_ok(base, '{"token": "abc", "refresh": "def"}') is False
    assert keyring_ok(base, '["a", "b"]') is False
    # minted secret no
    assert keyring_ok(base, '{"mark": "google-sa-v1", "key": "abc"}') is False
    # login secret no
    assert keyring_ok(base, '{"mark": "local-login-v1", "u": "user", "p": "pass"}') is False
    # bundle secret no
    assert keyring_ok(base, '{"mark": "oauth-bundle-v1", "token": "t"}') is False
    # reserved name no
    for res_name in RESERVED:
        assert keyring_ok({**base, "name": res_name}, valid_key) is False
    assert len(RESERVED) >= 7


async def test_sync(tmp_path):
    upstream_mock = httpx.MockTransport(lambda r: httpx.Response(200, content=b"{}"))
    ring = make_ring(tmp_path / "ring", upstream_mock)
    ring_transport = httpx.ASGITransport(app=ring)
    st = Store(tmp_path / "test.db")
    keyring = KeyringClient("http://keyring:9500", TOKEN, lambda: b"1" * 32, transport=ring_transport)
    assert await keyring.unlock()

    # Save a qualifying credential
    st.save_credential("myapi", "My API", ["api.example.com"], {"type": "bearer_header"}, kind="api")
    st.set_credential_secret("myapi", "my-api-secret-12345")
    assert st.credential("myapi")["secret"] == "my-api-secret-12345"
    assert st.credentials()[0]["keyring"] is False

    # Sync: a vault key moves (row secret NULL, keyring 1, container has cred:<name> kind connector with the slot)
    res = await cred_keyring.sync(st, keyring)
    assert res == {"moved": 1, "deleted": 0}

    row = st._credential_row("myapi")
    assert row["secret"] is None
    assert row["keyring"] == 1
    assert st.credentials()[0]["keyring"] is True
    assert st.credentials()[0]["has_secret"] is True
    assert st.credential("myapi")["secret"] == PLACEHOLDER

    # Container has cred:myapi with kind connector and slot {"header": "authorization"}
    await keyring.refresh()
    assert "cred:myapi" in keyring.known
    ring_entries = (await keyring._call("GET", "/entries")).json()["entries"]
    entry = next(e for e in ring_entries if e["name"] == "cred:myapi")
    assert entry["kind"] == "connector"
    assert entry["slot"] == {"header": "authorization"}

    # A deleted credential's entry is deleted on the next sync
    st.delete_credential("myapi")
    res_del = await cred_keyring.sync(st, keyring)
    assert res_del == {"moved": 0, "deleted": 1}
    await keyring.refresh()
    assert "cred:myapi" not in keyring.known

    # A re-registered credential with new hosts -> keyring 0 and the entry is deleted
    st.save_credential("rehost", "Rehost", ["api.old.com"], {"type": "bearer_header"}, kind="api")
    st.set_credential_secret("rehost", "rehost-secret-123")
    await cred_keyring.sync(st, keyring)
    assert st.credentials()[0]["keyring"] is True
    await keyring.refresh()
    assert "cred:rehost" in keyring.known

    # Re-register with new hosts
    st.save_credential("rehost", "Rehost", ["api.new.com"], {"type": "bearer_header"}, kind="api")
    assert st._credential_row("rehost")["keyring"] == 0
    assert st.credentials()[0]["keyring"] is False

    res_re = await cred_keyring.sync(st, keyring)
    assert res_re == {"moved": 0, "deleted": 1}
    await keyring.refresh()
    assert "cred:rehost" not in keyring.known


async def test_end_to_end_connector_get(tmp_path):
    seen = []

    def fake_svc(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=b'{"result": "ok"}')

    ring = make_ring(tmp_path / "ring", fake_svc)
    ring_transport = httpx.ASGITransport(app=ring)
    st = Store(tmp_path / "s.db")
    keyring = KeyringClient("http://keyring:9500", TOKEN, lambda: b"1" * 32, transport=ring_transport)
    assert await keyring.unlock()

    # Add a credential
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "custom_header", "header": "X-Acme-Key"}, kind="api")
    st.set_credential_secret("acme", "super-secret-acme-key")

    # Move to keyring
    await cred_keyring.sync(st, keyring)
    assert st._credential_row("acme")["secret"] is None
    assert st._credential_row("acme")["keyring"] == 1

    # Tools
    custom_fetcher = functools.partial(fetch, resolver=dns())
    tools = {t.name: t for t in connect_tools(st, fetcher=custom_fetcher, keyring=keyring)}
    out = await tools["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/v1/items"})
    assert "HTTP 200" in out
    assert len(seen) == 1
    # Real key in the right header
    assert seen[0].headers["x-acme-key"] == "super-secret-acme-key"
    assert seen[0].headers["host"] == "api.acme.com"

    # Server never held it after the move
    assert st._credential_row("acme")["secret"] is None

    # With vault locked the call is refused as today
    st.set_vault_locked(True)
    out_locked = await tools["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/v1/items"})
    assert "error: The user has locked the keychain" in out_locked


def test_keyring_client_enabled():
    kr31 = KeyringClient("http://keyring:9500", "t" * 31, lambda: b"k" * 32)
    assert kr31.enabled is False

    kr32 = KeyringClient("http://keyring:9500", "t" * 32, lambda: b"k" * 32)
    assert kr32.enabled is True

    kr0 = KeyringClient("http://keyring:9500", "", lambda: b"k" * 32)
    assert kr0.enabled is False


async def test_a_compressed_answer_is_redacted_too(tmp_path):
    """A service that gzips its answer (and echoes the key in it): the container asks for identity, and redacts the decoded body."""
    import gzip
    asked = []

    def upstream(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers.get("accept-encoding"))
        body = gzip.compress(json.dumps({"echo": request.headers.get("x-api-key", "")}).encode())
        return httpx.Response(200, headers={"content-encoding": "gzip", "content-type": "application/json"}, stream=httpx.ByteStream(body))

    ring = make_ring(tmp_path / "ring", upstream)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ring), base_url="http://keyring:9500",
                                 headers={"x-keyring-token": TOKEN}) as rc:
        assert (await rc.post("/unlock", json={"key": K1})).status_code == 200
        assert (await rc.put("/entries/cred:gz", json={"secret": "gz-secret-key-98765", "kind": "connector", "hosts": ["api.example.com"],
                                                       "local": False, "hint": "8765", "slot": {"header": "x-api-key"}})).status_code == 200
        r = await rc.get("/fwd/cred:gz/v1/x", headers={"x-keyring-upstream": "https://api.example.com", "x-api-key": PLACEHOLDER,
                                                        "accept-encoding": "gzip, br"})
    assert asked == ["identity"]
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert r.json() == {"echo": "[redacted]"}


async def test_a_key_saved_while_the_move_ran_is_not_lost(tmp_path):
    ring = make_ring(tmp_path / "ring", lambda r: httpx.Response(200, content=b"{}"))
    st = Store(tmp_path / "test.db")
    keyring = KeyringClient("http://keyring:9500", TOKEN, lambda: b"1" * 32, transport=httpx.ASGITransport(app=ring))
    assert await keyring.unlock()
    st.save_credential("race", "Race", ["api.example.com"], {"type": "bearer_header"}, kind="api")
    st.set_credential_secret("race", "old-secret-11111")
    real_put = keyring.put

    async def put_then_owner_saves(*a, **kw):   # the owner saves a new key while the old one is on its way to the container
        await real_put(*a, **kw)
        st.set_credential_secret("race", "new-secret-22222")

    keyring.put = put_then_owner_saves
    res = await cred_keyring.sync(st, keyring)
    assert res["moved"] == 0
    assert st.credential("race")["secret"] == "new-secret-22222"   # still in the vault, moved on the next sync
    keyring.put = real_put
    assert (await cred_keyring.sync(st, keyring))["moved"] == 1
    assert st.credential("race")["secret"] == PLACEHOLDER
