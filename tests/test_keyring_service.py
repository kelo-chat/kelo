"""The key container (keyring_service.py, docs/keyring.md): sealing, locking, the proxy and its refusals. No network."""

import base64
import re
import socket
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from assistant.keyring_service import create_app

TOKEN = "t" * 40
H = {"x-keyring-token": TOKEN}
K1 = base64.b64encode(b"1" * 32).decode()
K2 = base64.b64encode(b"2" * 32).decode()
SECRET = "sk-very-secret-value-123"
ROOT = Path(__file__).resolve().parents[1]


def dns(ip):
    return lambda host, port, *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def resp(status=200, headers=None, content=b'{"ok": true}'):
    """A response whose body is still a stream to read (as a real upstream's is)."""
    stream = httpx.ByteStream(content) if isinstance(content, bytes) else content
    return httpx.Response(status, headers=headers, stream=stream)


class Up:
    """A fake upstream that records the requests."""

    def __init__(self, handler=None):
        self.seen = []
        self.handler = handler or (lambda r: resp())

    def __call__(self, request):
        self.seen.append(request)
        return self.handler(request)


def make(tmp_path, up=None, ip="93.184.216.34"):
    up = up or Up()
    app = create_app(str(tmp_path), TOKEN, resolver=dns(ip), transport=httpx.MockTransport(up))
    return TestClient(app), up


def entry(**kw):
    return {"secret": SECRET, "kind": "chat", "hosts": ["api.openai.com"], "local": False, "paths": [], "hint": "…3", **kw}


def ready(tmp_path, up=None, ip="93.184.216.34", name="openai", **kw):
    c, up = make(tmp_path, up, ip)
    assert c.post("/unlock", json={"key": K1}, headers=H).status_code == 200
    assert c.put(f"/entries/{name}", json=entry(**kw), headers=H).status_code == 200
    return c, up


def fwd(c, path="v1/chat", name="openai", up="https://api.openai.com/v1", method="POST", **kw):
    h = {**H, "x-keyring-upstream": up, **kw.pop("headers", {})}
    return c.request(method, f"/fwd/{name}/{path}", headers=h, **kw)


def test_token_healthz_and_locked(tmp_path):
    c, _ = make(tmp_path)
    assert c.get("/entries").status_code == 401 and c.get("/entries").json() == {"code": "unauthorized"}
    assert c.get("/entries", headers={"x-keyring-token": "x"}).status_code == 401
    assert c.get("/healthz").json() == {"ok": True, "locked": True}
    assert c.put("/entries/openai", json=entry(), headers=H).status_code == 423
    assert c.post("/unlock", json={"key": K1}, headers=H).json() == {"ok": True}
    assert c.get("/healthz").json() == {"ok": True, "locked": False}
    assert c.post("/unlock", json={"key": K2}, headers=H).json() == {"code": "key_mismatch"}


def test_put_list_and_nothing_in_plain_text(tmp_path):
    c, _ = ready(tmp_path)
    r = c.get("/entries", headers=H)
    assert r.json() == {"entries": [{"name": "openai", "kind": "chat", "hosts": ["api.openai.com"], "local": False, "hint": "…3"}]}
    assert SECRET not in r.text
    assert SECRET.encode() not in (tmp_path / "keyring.db").read_bytes()
    assert (tmp_path / "keyring.db").stat().st_mode & 0o777 == 0o600
    assert c.delete("/entries/openai", headers=H).json() == {"ok": True, "removed": True}
    assert c.delete("/entries/openai", headers=H).json() == {"ok": True, "removed": False}


def test_a_restart_is_locked_and_the_same_key_opens_it(tmp_path):
    ready(tmp_path)
    c, up = make(tmp_path)
    assert c.get("/healthz").json()["locked"] is True
    assert fwd(c, json={}).status_code == 423
    assert c.post("/unlock", json={"key": K2}, headers=H).status_code == 409
    assert c.post("/unlock", json={"key": K1}, headers=H).status_code == 200
    assert fwd(c, headers={"authorization": "Bearer KELO_KEYRING_SECRET"}, json={}).status_code == 200
    assert up.seen[0].headers["authorization"] == f"Bearer {SECRET}"


def test_rekey(tmp_path):
    c, up = ready(tmp_path)
    assert c.post("/rekey", json={"old": K2, "new": K1}, headers=H).status_code == 409
    assert c.post("/rekey", json={"old": K1, "new": K2}, headers=H).json() == {"ok": True, "entries": 1}
    assert fwd(c, headers={"x-api-key": "KELO_KEYRING_SECRET"}, json={}).status_code == 200
    assert up.seen[-1].headers["x-api-key"] == SECRET
    c2, _ = make(tmp_path)
    assert c2.post("/unlock", json={"key": K1}, headers=H).status_code == 409
    assert c2.post("/unlock", json={"key": K2}, headers=H).status_code == 200


def test_proxy_places_the_secret_and_drops_internal_headers(tmp_path):
    c, up = ready(tmp_path)
    r = fwd(c, "v1/models/x", method="POST", params={"key": "KELO_KEYRING_SECRET", "a": "b"},
            headers={"authorization": "Bearer KELO_KEYRING_SECRET", "x-keep": "1"}, json={"hello": 1})
    assert r.status_code == 200
    q = up.seen[0]
    assert q.headers["authorization"] == f"Bearer {SECRET}" and q.headers["x-keep"] == "1"
    assert q.headers["host"] == "api.openai.com"
    assert not [k for k in q.headers if k.startswith("x-keyring-")]
    assert f"key={SECRET}" in q.url.query.decode() and "a=b" in q.url.query.decode()
    assert q.url.path == "/v1/v1/models/x" and q.url.host == "93.184.216.34"
    assert q.read() == b'{"hello":1}' or b"hello" in q.read()


def test_sse_streams_in_pieces_and_redirects_are_not_followed(tmp_path):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for p in (b"data: 1\n\n", b"data: 2\n\n"):
                yield p

    def handler(r):
        if r.url.path.endswith("/redir"):
            return resp(302, {"location": "http://169.254.169.254/"}, b"")
        return resp(200, {"content-type": "text/event-stream", "connection": "close"}, Chunks())

    c, up = ready(tmp_path, Up(handler))
    with c.stream("POST", "/fwd/openai/stream", headers={**H, "x-keyring-upstream": "https://api.openai.com/v1"}, json={}) as r:
        pieces = list(r.iter_raw())
        assert r.headers["content-type"] == "text/event-stream" and "connection" not in r.headers
    assert b"".join(pieces) == b"data: 1\n\ndata: 2\n\n" and len(pieces) >= 2
    r = fwd(c, "redir", method="GET", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "http://169.254.169.254/" and len(up.seen) == 2


def test_refusals(tmp_path):
    c, up = ready(tmp_path)
    assert fwd(c, up="https://evil.example.com/v1", json={}).json()["code"] == "host_not_allowed"
    assert fwd(c, up="http://api.openai.com/v1", json={}).status_code == 403
    assert fwd(c, up="https://u:p@api.openai.com/v1", json={}).status_code == 403
    assert fwd(c, name="nope", json={}).json() == {"code": "no_key"}
    assert fwd(c, up="https://api.openai.com:443/v1", json={}).status_code == 200
    assert len(up.seen) == 1


def test_private_address_only_for_local_entries(tmp_path):
    c, up = ready(tmp_path, ip="10.0.0.5")
    assert fwd(c, json={}).status_code == 403 and not up.seen
    c, up = ready(tmp_path / "b", ip="10.0.0.5", name="custom-home", hosts=["ollama.home.example"], local=True)
    assert fwd(c, name="custom-home", up="http://ollama.home.example:11434/v1", json={}).status_code == 200
    assert up.seen[0].url.host == "10.0.0.5" and up.seen[0].url.port == 11434
    assert up.seen[0].headers["host"] == "ollama.home.example:11434"
    for bad in ("169.254.169.254", "127.0.0.1"):
        c, _ = ready(tmp_path / bad, ip=bad, name="custom-x", hosts=["x.example"], local=True)
        assert fwd(c, name="custom-x", up="http://x.example/v1", json={}).status_code == 403
    for bad in ("100.64.0.9", "169.254.169.254", "fd00:ec2::254"):
        c, _ = ready(tmp_path / ("p" + bad), ip=bad)
        assert fwd(c, json={}).status_code == 403


def test_admin_entries_are_read_only_on_their_paths(tmp_path):
    c, up = ready(tmp_path, name="admin:openai", kind="admin", paths=["/v1/organization/costs"])
    kw = dict(name="admin:openai")
    assert fwd(c, "organization/costs", method="POST", json={}, **kw).json() == {"code": "not_allowed"}
    assert fwd(c, "organization/users", method="GET", **kw).status_code == 403
    assert fwd(c, "organization/costs/../users", method="GET", **kw).status_code == 403
    assert fwd(c, "organization/costs%2f..%2fusers", method="GET", **kw).status_code == 403
    assert not up.seen
    assert fwd(c, "organization/costs", method="GET", **kw).status_code == 200 and len(up.seen) == 1


def test_validation(tmp_path):
    c, _ = ready(tmp_path)

    def put(name="ok", **kw):
        return c.put(f"/entries/{name}", json=entry(**kw), headers=H).status_code

    assert put("Bad Name") == 422 and put("-x") == 422
    assert put(kind="other") == 422
    for hosts in (["https://a.com"], ["a.com/x"], ["a.com:443"], ["A.com"], [], ["a.com"] * 6, ["x" * 254]):
        assert put(hosts=hosts) == 422
    assert put(hosts=["10.0.0.5"], local=True) == 200
    assert put(paths=["/v1"]) == 422 and put(kind="admin", paths=["v1"]) == 422
    assert put(secret="") == 422 and put(secret="a\nb") == 422 and put(secret="x" * 4097) == 422
    assert put(hint="x" * 13) == 422


def test_limits(tmp_path):
    c, _ = ready(tmp_path)
    for i in range(199):
        assert c.put(f"/entries/k{i}", json=entry(), headers=H).status_code == 200
    assert c.put("/entries/one-more", json=entry(), headers=H).status_code == 507
    assert c.put("/entries/k1", json=entry(), headers=H).status_code == 200   # replacing one is fine


def test_compose_and_entrypoint_have_the_service():
    compose = (ROOT / "compose.yml").read_text()
    svc = re.search(r"^  keyring:\n(.*?)(?=^  \S|^\S)", compose, re.M | re.S).group(1)
    assert "ROLE: keyring" in svc and "env_file" not in svc and 'user: "1005:1005"' in svc and "read_only: true" in svc
    assert "networks: [keyring-net, keyring-out]" in svc and "<<: *hardening" in svc and "ports:" not in svc
    assert re.search(r"^  keyring-net: \{internal: true\}", compose, re.M) and re.search(r"^  keyring-state: \{\}", compose, re.M)
    server = re.search(r"^  server:\n(.*?)(?=^  \S)", compose, re.M | re.S).group(1)
    assert "keyring-net" in server and "KEYRING_URL: http://keyring:9500" in server and "depends_on: [browser]" in server
    assert "keyring)" in (ROOT / "docker/entrypoint.sh").read_text()


def test_the_key_goes_only_into_auth_headers_and_a_key_parameter(tmp_path):
    c, up = ready(tmp_path)
    assert fwd(c, headers={"x-api-key": "KELO_KEYRING_SECRET"}).status_code == 200
    assert up.seen[-1].headers["x-api-key"] == SECRET
    n = len(up.seen)
    # anywhere an answer might echo it back: refused, nothing sent upstream
    assert fwd(c, headers={"x-echo": "KELO_KEYRING_SECRET"}).json()["code"] == "bad_placement"
    assert fwd(c, params={"q": "KELO_KEYRING_SECRET"}).json()["code"] == "bad_placement"
    assert fwd(c, params={"key": "x KELO_KEYRING_SECRET"}).json()["code"] == "bad_placement"
    assert fwd(c, path="v1/KELO_KEYRING_SECRET").json()["code"] == "bad_placement"
    assert len(up.seen) == n
