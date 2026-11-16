"""The key container ("keyring", docs/keyring.md): keeps the AI providers' keys the owner entered, outside the Kelo server.

The server sends a model call to /fwd/<name>/<path> with the header x-keyring-upstream and the literal KELO_KEYRING_SECRET where the
key belongs; this adds the real key, only for that entry's own provider host(s). The ring key (which seals the keys on disk) lives
only in memory: the container starts locked and the server hands the key over (POST /unlock).

    KEYRING_TOKEN=<32+ characters> KEYRING_STATE=/state python -m assistant.keyring_service
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, quote_plus, unquote, urlsplit

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .hosting import paths
from .netguard import NetGuardError, resolve_local, resolve_public

log = logging.getLogger("keyring")

PLACEHOLDER = "KELO_KEYRING_SECRET"
NAME = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
MAX_BODY = 20 * 1024 * 1024
MAX_CONNECTOR_BODY = 8 * 1024 * 1024
MAX_ENTRIES = 200
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authenticate", "proxy-authorization"}
TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0)
CHECK_TEXT = b"kelo-keyring check"
KEY_HEADERS = {"authorization", "x-api-key", "x-goog-api-key", "api-key"}   # where a provider reads its key
SLOT_HEADER = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SLOT_PARAM = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-\[\]]{0,63}$")


class RingLocked(Exception):
    pass


class RingMismatch(Exception):
    pass


def _key(text: Any) -> bytes:
    try:
        raw = base64.b64decode(str(text).replace("-", "+").replace("_", "/"), validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != 32:
        raise ValueError("key must be base64 of 32 bytes")
    return raw


def _check(key: bytes) -> str:
    return hmac.new(key, CHECK_TEXT, hashlib.sha256).hexdigest()


def _seal(key: bytes, name: str, secret: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, secret.encode(), name.encode())


def _open(key: bytes, name: str, blob: bytes) -> str:
    return AESGCM(key).decrypt(blob[:12], blob[12:], name.encode()).decode()


class Ring:
    """The store (SQLite) and the ring key (memory only)."""

    def __init__(self, state_dir: str):
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, "keyring.db")
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS entries(name TEXT PRIMARY KEY, kind TEXT, sealed BLOB, hosts TEXT, local INTEGER, "
            "paths TEXT, hint TEXT, created_at REAL, slot TEXT);"
            "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(entries)")}
        if "slot" not in have:
            self.db.execute("ALTER TABLE entries ADD COLUMN slot TEXT")
        self.key: bytes | None = None
        self.lock = threading.Lock()

    @property
    def locked(self) -> bool:
        return self.key is None

    def _stored_check(self) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key='check'").fetchone()
        return row[0] if row else None

    def unlock(self, key: bytes) -> None:
        with self.lock:
            stored = self._stored_check()
            if stored is None:
                self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('check', ?)", (_check(key),))
            elif not hmac.compare_digest(stored, _check(key)):
                raise RingMismatch()
            self.key = key

    def rekey(self, old: bytes, new: bytes) -> int:
        with self.lock:
            stored = self._stored_check()
            if stored is None or not hmac.compare_digest(stored, _check(old)):
                raise RingMismatch()
            self.db.execute("BEGIN IMMEDIATE")
            try:
                rows = self.db.execute("SELECT name, sealed FROM entries").fetchall()
                for name, sealed in rows:
                    self.db.execute("UPDATE entries SET sealed=? WHERE name=?", (_seal(new, name, _open(old, name, sealed)), name))
                self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('check', ?)", (_check(new),))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.key = new
            return len(rows)

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    def exists(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM entries WHERE name=?", (name,)).fetchone() is not None

    def put(self, name: str, secret: str, kind: str, hosts: list[str], local: bool, paths: list[str], hint: str,
            slot: dict[str, str] | None = None) -> None:
        with self.lock:
            if self.key is None:
                raise RingLocked()
            self.db.execute(
                "INSERT OR REPLACE INTO entries(name, kind, sealed, hosts, local, paths, hint, created_at, slot) VALUES(?,?,?,?,?,?,?,?,?)",
                (name, kind, _seal(self.key, name, secret), json.dumps(hosts), int(local), json.dumps(paths), hint, time.time(),
                 json.dumps(slot) if slot is not None else None))

    def delete(self, name: str) -> bool:
        with self.lock:
            return self.db.execute("DELETE FROM entries WHERE name=?", (name,)).rowcount > 0

    def listing(self) -> list[dict]:
        rows = self.db.execute("SELECT name, kind, hosts, local, hint, slot FROM entries ORDER BY name").fetchall()
        return [{"name": n, "kind": k, "hosts": json.loads(h), "local": bool(lo), "hint": hint,
                 **({"slot": json.loads(slot)} if slot else {})} for n, k, h, lo, hint, slot in rows]

    def get(self, name: str) -> dict | None:
        """The entry with its secret opened (needs the ring key)."""
        with self.lock:
            if self.key is None:
                raise RingLocked()
            row = self.db.execute("SELECT kind, sealed, hosts, local, paths, slot FROM entries WHERE name=?", (name,)).fetchone()
            if row is None:
                return None
            try:
                secret = _open(self.key, name, row[1])
            except InvalidTag:
                return None
            slot = json.loads(row[5]) if row[5] else None
            return {"kind": row[0], "secret": secret, "hosts": json.loads(row[2]), "local": bool(row[3]), "paths": json.loads(row[4]), "slot": slot}


def _err(status: int, code: str, detail: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status)


def _valid_host(h: Any) -> bool:
    if not isinstance(h, str) or not h or len(h) > 253 or h != h.lower():
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return bool(HOSTNAME.match(h))


def _validate_entry(name: str, body: Any) -> tuple[str, str, list[str], bool, list[str], str, dict[str, str] | None]:
    """(secret, kind, hosts, local, paths, hint, slot) or ValueError with the reason."""
    if not NAME.match(name):
        raise ValueError("bad name")
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    kind = body.get("kind")
    if kind not in ("chat", "admin", "connector"):
        raise ValueError("kind must be chat, admin or connector")
    hosts = body.get("hosts")
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 5 or not all(_valid_host(h) for h in hosts):
        raise ValueError("hosts: 1-5 lowercase host names or IP addresses, no scheme, path or port")
    paths = body.get("paths") or []
    if not isinstance(paths, list) or not all(isinstance(p, str) and p.startswith("/") and len(p) <= 200 for p in paths):
        raise ValueError("paths must start with /")
    if paths and kind != "admin":
        raise ValueError("paths are only for admin keys")
    slot = body.get("slot")
    if kind == "connector":
        if not isinstance(slot, dict):
            raise ValueError("connector keys require a slot object")
        has_header = "header" in slot
        has_param = "param" in slot
        if (has_header and has_param) or (not has_header and not has_param):
            raise ValueError("slot must have exactly one of header or param")
        if has_header:
            h = slot.get("header")
            if not isinstance(h, str) or not SLOT_HEADER.match(h):
                raise ValueError("slot header must match ^[a-z][a-z0-9-]{0,63}$")
            slot = {"header": h}
        else:
            p = slot.get("param")
            if not isinstance(p, str) or not SLOT_PARAM.match(p):
                raise ValueError("slot param must match ^[A-Za-z_][A-Za-z0-9_.\\-\\[\\]]{0,63}$")
            slot = {"param": p}
    elif slot is not None:
        raise ValueError("slot is only for connector keys")
    secret = body.get("secret")
    if not isinstance(secret, str) or not 1 <= len(secret) <= 4096 or "\n" in secret or "\r" in secret:
        raise ValueError("secret: 1-4096 characters, one line")
    hint = body.get("hint") or ""
    if not isinstance(hint, str) or len(hint) > 12:
        raise ValueError("hint: at most 12 characters")
    local = body.get("local", False)
    if not isinstance(local, bool):
        raise ValueError("local must be true or false")
    return secret, kind, hosts, local, paths, hint, slot


def create_app(state_dir: str, token: str, *, resolver: Callable[..., list] | None = None,
               transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    ring = Ring(state_dir)

    def denied(request: Request) -> JSONResponse | None:
        got = request.headers.get("x-keyring-token", "")
        if not token or not hmac.compare_digest(got.encode(), token.encode()):
            return _err(401, "unauthorized")
        return None

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "locked": ring.locked})

    async def json_body(request: Request) -> Any:
        try:
            return json.loads(await request.body())
        except ValueError:
            return None

    async def unlock(request: Request) -> Response:
        if (r := denied(request)) is not None:
            return r
        body = await json_body(request)
        try:
            key = _key((body or {}).get("key"))
        except (ValueError, AttributeError):
            return _err(422, "bad_request", "key must be base64 of 32 bytes")
        try:
            ring.unlock(key)
        except RingMismatch:
            return _err(409, "key_mismatch")
        return JSONResponse({"ok": True})

    async def rekey(request: Request) -> Response:
        if (r := denied(request)) is not None:
            return r
        body = await json_body(request)
        try:
            old, new = _key((body or {}).get("old")), _key((body or {}).get("new"))
        except (ValueError, AttributeError):
            return _err(422, "bad_request", "old and new must be base64 of 32 bytes")
        try:
            n = ring.rekey(old, new)
        except RingMismatch:
            return _err(409, "key_mismatch")
        return JSONResponse({"ok": True, "entries": n})

    async def entries(request: Request) -> Response:
        if (r := denied(request)) is not None:
            return r
        return JSONResponse({"entries": ring.listing()})

    async def put_entry(request: Request) -> Response:
        if (r := denied(request)) is not None:
            return r
        if ring.locked:
            return _err(423, "locked")
        name = request.path_params["name"]
        try:
            secret, kind, hosts, local, paths, hint, slot = _validate_entry(name, await json_body(request))
        except ValueError as e:
            return _err(422, "bad_request", str(e))
        if not ring.exists(name) and ring.count() >= MAX_ENTRIES:
            return _err(507, "too_many_entries")
        try:
            ring.put(name, secret, kind, hosts, local, paths, hint, slot)
        except RingLocked:
            return _err(423, "locked")
        return JSONResponse({"ok": True})

    async def delete_entry(request: Request) -> Response:
        if (r := denied(request)) is not None:
            return r
        return JSONResponse({"ok": True, "removed": ring.delete(request.path_params["name"])})

    async def forward(request: Request) -> Response:
        if (r := denied(request)) is not None:
            return r
        if ring.locked:
            return _err(423, "locked")
        name = request.path_params["name"]
        try:
            entry = ring.get(name) if NAME.match(name) else None
        except RingLocked:
            return _err(423, "locked")
        if entry is None:
            return _err(404, "no_key")
        started = time.monotonic()
        up = urlsplit(request.headers.get("x-keyring-upstream", ""))
        try:
            port = up.port
        except ValueError:
            return _err(422, "bad_request", "bad upstream port")
        host = (up.hostname or "").lower()
        if up.username is not None or up.password is not None or "@" in up.netloc:
            return _err(403, "host_not_allowed", "no user info in the upstream")
        if up.scheme != "https" and not (up.scheme == "http" and entry["local"]):
            return _err(403, "host_not_allowed", "the upstream must be https")
        if not host or host not in entry["hosts"]:
            return _err(403, "host_not_allowed")
        tail_raw = request.scope.get("raw_path")
        prefix = f"/fwd/{name}/"
        raw = tail_raw.decode("latin-1").split("?", 1)[0] if tail_raw else quote(request.scope["path"])
        raw_tail = raw[len(prefix):] if raw.startswith(prefix) else quote(request.path_params.get("path", ""))
        base = up.path.rstrip("/")
        if entry["kind"] == "admin":
            if request.method != "GET":
                return _err(403, "not_allowed")
            joined = base + "/" + unquote(raw_tail)
            segs = joined.split("/")[1:]
            if ".." in segs or "." in segs or "" in segs[:-1] or joined.startswith("//") or \
                    not any(joined.startswith(p) for p in entry["paths"]):
                return _err(403, "not_allowed")
        # the address: resolved once, checked, and that address is the one connected to
        eff_port = port or (443 if up.scheme == "https" else 80)
        try:
            ip = await (resolve_local(host, eff_port, resolver) if entry["local"] else resolve_public(host, resolver))
        except NetGuardError:
            return _err(403, "address_refused")
        ip_url = f"[{ip}]" if ":" in ip else ip
        is_connector = entry["kind"] == "connector"
        slot = entry.get("slot") or {}
        raw_query = request.scope.get("query_string", b"").decode("latin-1")
        parts = raw_query.split("&") if raw_query else []
        if PLACEHOLDER in raw_tail:
            return _err(400, "bad_placement")

        if is_connector:
            allowed_param = slot.get("param")
            allowed_header = slot.get("header")
            if any(PLACEHOLDER in q and (not allowed_param or q != f"{allowed_param}={PLACEHOLDER}") for q in parts):
                return _err(400, "bad_placement")
            query = "&".join(q.replace(PLACEHOLDER, quote(entry["secret"], safe="")) if allowed_param and q == f"{allowed_param}={PLACEHOLDER}" else q for q in parts)
            target = f"{up.scheme}://{ip_url}:{eff_port}{base}/{raw_tail}" + (f"?{query}" if query else "")
            headers: list[tuple[str, str]] = []
            for k, v in request.headers.raw:
                kl, val = k.decode("latin-1").lower(), v.decode("latin-1")
                if kl in HOP or kl in ("host", "content-length") or kl.startswith(("x-keyring-", "proxy-")):
                    continue
                if PLACEHOLDER in val:
                    if not allowed_header or kl != allowed_header:
                        return _err(400, "bad_placement")
                    val = val.replace(PLACEHOLDER, entry["secret"])
                headers.append((k.decode("latin-1"), val))
        else:
            if any(PLACEHOLDER in q and q != f"key={PLACEHOLDER}" for q in parts):
                return _err(400, "bad_placement")
            query = "&".join(q.replace(PLACEHOLDER, entry["secret"]) if q == f"key={PLACEHOLDER}" else q for q in parts)
            target = f"{up.scheme}://{ip_url}:{eff_port}{base}/{raw_tail}" + (f"?{query}" if query else "")
            headers: list[tuple[str, str]] = []
            for k, v in request.headers.raw:
                kl, val = k.decode("latin-1").lower(), v.decode("latin-1")
                if kl in HOP or kl in ("host", "content-length") or kl.startswith(("x-keyring-", "proxy-")):
                    continue
                if PLACEHOLDER in val:
                    if kl not in KEY_HEADERS:
                        return _err(400, "bad_placement")
                    val = val.replace(PLACEHOLDER, entry["secret"])
                headers.append((k.decode("latin-1"), val))
        headers.append(("host", f"{host}:{port}" if port else host))
        if is_connector:   # an answer the container must read to redact the key: ask for it uncompressed
            headers = [(k, v) for k, v in headers if k.lower() != "accept-encoding"] + [("accept-encoding", "identity")]
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                return _err(413, "too_large")
            chunks.append(chunk)
        ext = {"sni_hostname": host} if up.scheme == "https" and not _is_ip(host) else {}
        client = httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=TIMEOUT, trust_env=False)
        try:
            req = client.build_request(request.method, target, headers=headers, content=b"".join(chunks), extensions=ext)
            resp = await client.send(req, stream=True)
        except httpx.TimeoutException:
            await client.aclose()
            return _err(504, "upstream_timeout")
        except httpx.HTTPError:
            await client.aclose()
            return _err(502, "upstream_error")
        log.info("%s host=%s method=%s status=%d ms=%d", name, host, request.method, resp.status_code,
                 (time.monotonic() - started) * 1000)

        if is_connector:
            try:
                if resp.is_stream_consumed:
                    body = resp.content
                    if len(body) > MAX_CONNECTOR_BODY:
                        return _err(502, "too_big")
                else:
                    chunks, size = [], 0
                    async for chunk in resp.aiter_bytes():   # decoded: a compressed answer is redacted as the text it is
                        size += len(chunk)
                        if size > MAX_CONNECTOR_BODY:
                            return _err(502, "too_big")
                        chunks.append(chunk)
                    body = b"".join(chunks)
            except httpx.TimeoutException:
                return _err(504, "upstream_timeout")
            except httpx.HTTPError:
                return _err(502, "upstream_error")
            finally:
                await resp.aclose()
                await client.aclose()

            s = entry["secret"]
            forms = [s.encode(), quote(s, safe="").encode(), quote_plus(s).encode()]
            seen_forms: set[bytes] = set()
            for f in forms:
                if f not in seen_forms:
                    seen_forms.add(f)
                    body = body.replace(f, b"[redacted]")

            out: list[tuple[bytes, bytes]] = []
            for k, v in resp.headers.raw:
                kl = k.decode("latin-1").lower()
                if kl in HOP or kl.startswith("proxy-") or kl in ("content-length", "content-encoding"):
                    continue   # the body below is the decoded one
                val = v.decode("latin-1")
                for f_str in {s, quote(s, safe=""), quote_plus(s)}:
                    val = val.replace(f_str, "[redacted]")
                out.append((k, val.encode("latin-1")))
            out.append((b"content-length", str(len(body)).encode("latin-1")))

            res = Response(content=body, status_code=resp.status_code)
            res.raw_headers = out
            return res

        async def close() -> None:
            await resp.aclose()
            await client.aclose()

        out_sr = [(k, v) for k, v in resp.headers.raw if k.decode("latin-1").lower() not in HOP
                  and not k.decode("latin-1").lower().startswith("proxy-")]
        sr = StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, background=BackgroundTask(close))
        sr.raw_headers = [(k, v) for k, v in out_sr]
        return sr

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    return Starlette(routes=[
        Route("/healthz", healthz),
        Route("/unlock", unlock, methods=["POST"]),
        Route("/rekey", rekey, methods=["POST"]),
        Route("/entries", entries, methods=["GET"]),
        Route("/entries/{name}", put_entry, methods=["PUT"]),
        Route("/entries/{name}", delete_entry, methods=["DELETE"]),
        Route("/fwd/{name}/{path:path}", forward, methods=methods),
    ])


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def main() -> None:
    import uvicorn
    token = os.environ.get("KEYRING_TOKEN", "")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if len(token) < 32:
        # no shared secret yet (a copy from before the key container): run with a token nobody has instead of restarting again and
        # again; the server keeps the keys in its database until `assistant update` writes KEYRING_TOKEN and both restart
        log.warning("KEYRING_TOKEN is not set (32+ characters): every call is refused until it is")
        token = secrets.token_urlsafe(48)   # known to nobody: /healthz answers, everything else is 401
    app = create_app(paths.default_keyring_state_dir(), token)
    uvicorn.run(app, host="0.0.0.0", port=9500, access_log=False, server_header=False)


if __name__ == "__main__":
    main()
