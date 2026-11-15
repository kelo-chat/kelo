"""The server's KE2E key in the API (docs/security/e2e-and-phone-key.md 1.2, 1.3, 1.5).

GET  /api/e2e/info     plaintext, no sign-in: {"v": 1, "kid", "pk" (base64url), "fingerprint"}. Apps that got no key from a QR
                       (typed codes, devices paired before KE2E) pin this one "not verified" (trust on first use).
POST /api/e2e/rotate   the master token only (`assistant e2e rotate`): a new key pair; every device must pair again (their pinned
                       key no longer matches: 409 e2e_key_changed). Answers the new public info.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import APIRouter, Depends

from .. import e2e
from .context import AppContext


def load_key(state_root: Path | None) -> e2e.ServerKey:
    """<state>/e2e/server.key, made on first start; a server without a state folder (tests, :memory:) gets one in memory."""
    return e2e.ServerKey.load_or_create(state_root) if state_root is not None else e2e.ServerKey(X25519PrivateKey.generate())


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    e2e_server: e2e.E2EServer = ctx.e2e_server
    state_root: Path | None = ctx.state_root
    require_master = ctx.require_master

    @router.get("/api/e2e/info")
    async def e2e_info() -> dict[str, Any]:
        return e2e_server.key.public_info()

    @router.post("/api/e2e/rotate", dependencies=[Depends(require_master)])
    async def e2e_rotate() -> dict[str, Any]:
        e2e_server.key = e2e.ServerKey.rotate(state_root) if state_root is not None else load_key(None)
        return {**e2e_server.key.public_info(), "pair_again": True}

    return router
