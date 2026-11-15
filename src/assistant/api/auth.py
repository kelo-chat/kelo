"""Who is asking: the master token (the owner's own tools on the server machine) or a paired device's token, with a brute-force guard.

`AuthGuard` is the one place every route's auth goes through (`Depends(guard.require_auth)` and the owner / master variants), the
live-view WebSocket (auth_failed / auth_blocked) and POST /api/pair (throttle). KE2E (docs/security/e2e-and-phone-key.md 1.3)
plugs in here: a device marked e2e-only is refused when its token arrives on plaintext /api/* (`check_transport`).
"""

from __future__ import annotations

import secrets
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

AUTH_FAIL_LIMIT = 10  # failed bearer attempts per client...
AUTH_FAIL_WINDOW = 60.0  # ...per this many seconds


class AuthGuard:
    def __init__(self, token: str, store: Any, note_use: Callable[[Request], None]):
        self.token = token
        self.store = store
        self.note_use = note_use   # a signed-in request counts as the app being used (server.py note_use)
        self.failures: dict[str, deque[float]] = {}

    @staticmethod
    def client_key(request: Any) -> str:
        # Behind a Cloudflare tunnel every request comes from 127.0.0.1; the real client is in this header.
        return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")

    def throttle(self, request: Any) -> tuple[deque[float], float]:
        """The client's recent failures (older ones dropped) and now; 429 once there are too many. A caller appends `now` on failure."""
        key, now = self.client_key(request), time.monotonic()
        recent = self.failures.setdefault(key, deque())
        while recent and now - recent[0] > AUTH_FAIL_WINDOW:
            recent.popleft()
        if len(recent) >= AUTH_FAIL_LIMIT:  # brute-force guard: only failed attempts count
            raise HTTPException(429, "too many failed attempts; try again later",
                                headers={"Retry-After": str(int(AUTH_FAIL_WINDOW))})
        return recent, now

    def check_transport(self, request: Request, device: dict[str, Any]) -> None:
        """KE2E (e2e-and-phone-key.md 1.3): a device's first call through /e2e marks it e2e-only; from then on its token is refused on
        plaintext /api/* (403 {"code": "e2e_required"}), so a stolen token cannot be used around the encryption. The master token is
        never affected (the owner's own tools on the server machine)."""
        through_e2e = bool(request.scope.get("kelo_e2e"))
        if through_e2e:
            if not device.get("e2e_only"):
                self.store.set_device_e2e_only(device["id"])
            return
        if device.get("e2e_only"):
            raise HTTPException(403, {"code": "e2e_required",
                                      "message": "this device talks to the server end to end only; update or pair the app again"})

    def require_auth(self, request: Request) -> None:
        recent, now = self.throttle(request)
        supplied = request.headers.get("authorization", "")
        scheme, _, value = supplied.partition(" ")
        request.state.device_id = None            # None = the master token (the owner's own tools); otherwise the signed-in device
        request.state.device_owner = False        # a paired phone given the owner's rights on the server machine (operator settings only)
        if scheme.lower() == "bearer" and secrets.compare_digest(value.encode(), self.token.encode()):
            self.note_use(request)
            return
        device = self.store.device_for_token(value) if scheme.lower() == "bearer" and value else None
        if device:
            self.check_transport(request, device)
            request.state.device_id = device["id"]
            request.state.device_owner = bool(device.get("owner"))
            self.note_use(request)
            return
        recent.append(now)
        if len(self.failures) > 1000:  # bound memory under a spray of addresses
            for k in [k for k, v in self.failures.items() if not v or now - v[-1] > AUTH_FAIL_WINDOW]:
                self.failures.pop(k, None)
        raise HTTPException(401, "invalid or missing bearer token", headers={"WWW-Authenticate": "Bearer"})

    def require_owner(self, request: Request) -> None:
        """Operator settings: the owner's own access key, or a phone the owner marked as theirs on the server machine
        (`assistant phone add --owner` / `assistant phone owner --grant`). Any other paired phone gets 403. This is the only
        thing an owner phone gains: it keeps its own device id everywhere else (its browser tab, push, phone data)."""
        self.require_auth(request)
        if request.state.device_id is not None and not request.state.device_owner:
            raise HTTPException(403, "only the owner's own token can change this")

    def require_master(self, request: Request) -> None:
        """The master token itself (whoever controls the server machine): who may hand out or take back owner rights."""
        self.require_auth(request)
        if request.state.device_id is not None:
            raise HTTPException(403, "only the access key on the server machine can do this (assistant phone owner)")

    @staticmethod
    def device_of(conn: Any) -> str:
        """Who is asking, as a browser owner key: the paired device's id, or "owner" for the master token."""
        dev = getattr(conn.state, "device_id", None)
        return str(dev) if dev is not None else "owner"

    def auth_failed(self, conn: Any) -> None:
        self.failures.setdefault(self.client_key(conn), deque()).append(time.monotonic())

    def auth_blocked(self, conn: Any) -> bool:
        recent, now = self.failures.get(self.client_key(conn)) or deque(), time.monotonic()
        return sum(1 for t in recent if now - t <= AUTH_FAIL_WINDOW) >= AUTH_FAIL_LIMIT
