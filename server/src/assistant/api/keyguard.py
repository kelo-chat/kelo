"""Keyguard in the API (keyguard.py; docs/security/e2e-and-phone-key.md section 2). Owner only. Key material travels only inside
KE2E: a call that carries or returns the key must come through /e2e from a signed-in phone (403 {"code": "e2e_required"} otherwise).

GET    /api/keyguard                {"mode": "server" | "phone", "locked", "env_key", "recovery", "phones": [{"device_id", "name",
                                    "alg", "created"}], "auto_pay_paused": n (auto-pay cards that wait because Keyguard is off;
                                    the disable answer carries it too)}   (works while locked)
POST   /api/keyguard/enroll         turn it on (or add this phone while unlocked): {"enrollment_id", "key" (the master key,
                                    base64url, to wrap with the phone's hardware key), "enabling", "recovery_code"? (enabling only:
                                    24 characters, shown ONCE)}; 409 {"code": "env_key"} while ASSISTANT_SECRET_KEY is set
POST   /api/keyguard/enroll/finish  {"enrollment_id", "alg": 1 | 2, "blob" (base64, first byte = alg)} -> the view. Enabling re-seals
                                    the secrets with the new key and deletes master.key
GET    /api/keyguard/blob           this phone's wrapped copy {"alg", "blob"} (works while locked)
POST   /api/keyguard/unlock         {"key"} -> {"ok", "locked": false}; 422 {"code": "wrong_key"}
POST   /api/keyguard/recover        {"code"} the recovery code instead of a phone (also the master token on the server machine:
                                    `assistant keyguard recover`) -> {"ok", "locked": false}
DELETE /api/keyguard/phones/{id}    drop a phone's copy (not the last one: 409 last_keyguard_phone, as DELETE /api/devices/{id})
POST   /api/keyguard/disable        while unlocked: the key goes back to master.key on the server
POST   /api/keyguard/prepare        master token (`assistant keyguard enable`): ASSISTANT_SECRET_KEY's key into master.key before the
                                    CLI removes it from .env -> {"ok", "written", "env_key"}
Locked: every endpoint that needs a secret answers 423 {"code": "locked"}; the apps show the unlock prompt.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..keyguard import KeyGuardError
from ..keyring_client import KeyringError, ring_of, start_up as keyring_up
from .context import AppContext


class FinishIn(BaseModel):
    enrollment_id: str = Field(min_length=8, max_length=64)
    alg: int
    blob: str = Field(min_length=8, max_length=8192)


class UnlockIn(BaseModel):
    key: str = Field(min_length=40, max_length=64)


class RecoverIn(BaseModel):
    code: str = Field(min_length=20, max_length=64)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    store = ctx.store
    guard = store.keyguard
    keyring = ctx.keyring
    require_owner = ctx.require_owner

    def phone_over_e2e(request: Request) -> int:
        """The calling phone's device id; key material only inside KE2E."""
        if not request.scope.get("kelo_e2e") or request.state.device_id is None:
            raise HTTPException(403, {"code": "e2e_required", "message": "this needs a phone talking to the server end to end"})
        return int(request.state.device_id)

    def master_or_e2e(request: Request) -> None:
        if request.state.device_id is not None and not request.scope.get("kelo_e2e"):
            raise HTTPException(403, {"code": "e2e_required", "message": "this needs a phone talking to the server end to end"})

    def refused(e: KeyGuardError) -> HTTPException:
        return HTTPException(e.status, {"code": e.code, "message": str(e)})

    def names() -> dict[int, str]:
        return {int(d["id"]): d["name"] for d in store.devices()}

    def view() -> dict[str, Any]:
        """The state, and how many auto-pay cards wait because Keyguard is off (auto-pay needs it: wallet.py keyguard_off)."""
        waiting = 0
        if guard.mode != "phone":
            try:
                waiting = len(store._all("SELECT id FROM wallet_cards WHERE auto_pay = 1 AND sealed IS NOT NULL"))
            except Exception:  # noqa: BLE001 - no wallet table yet
                waiting = 0
        return {**guard.view(names()), "auto_pay_paused": waiting}

    @router.get("/api/keyguard", dependencies=[Depends(require_owner)])
    async def keyguard_view() -> dict[str, Any]:
        return view()

    @router.post("/api/keyguard/enroll", dependencies=[Depends(require_owner)])
    async def keyguard_enroll(request: Request) -> dict[str, Any]:
        try:
            return guard.start_enroll(phone_over_e2e(request))
        except KeyGuardError as e:
            raise refused(e) from None

    @router.post("/api/keyguard/enroll/finish", dependencies=[Depends(require_owner)])
    async def keyguard_enroll_finish(body: FinishIn, request: Request) -> dict[str, Any]:
        device_id = phone_over_e2e(request)
        rotation = None
        try:
            if keyring.enabled:
                # Turning Keyguard on changes the key the key container's ring key comes from: re-seal its entries first, and put
                # them back if the rest of the step is refused (so the keys are never under a key the server does not hold).
                rotation = guard.pending_rotation(device_id, body.enrollment_id, body.alg, body.blob)
                if rotation:
                    await keyring.rekey(ring_of(rotation[0]), ring_of(rotation[1]))
            guard.finish_enroll(store, device_id, body.enrollment_id, body.alg, body.blob)
        except KeyringError as e:
            raise HTTPException(503, {"code": "keyring", "message": "the key store could not re-seal the keys; Keyguard was not turned on"}) from None
        except KeyGuardError as e:
            await undo_rekey(rotation)
            raise refused(e) from None
        except Exception:
            await undo_rekey(rotation)
            raise
        return view()

    async def undo_rekey(rotation: tuple[bytes, bytes] | None) -> None:
        if rotation:
            try:
                await keyring.rekey(ring_of(rotation[1]), ring_of(rotation[0]))
            except KeyringError:
                pass

    @router.get("/api/keyguard/blob", dependencies=[Depends(require_owner)])
    async def keyguard_blob(request: Request) -> dict[str, Any]:
        got = guard.blob_for(phone_over_e2e(request))
        if got is None:
            raise HTTPException(404, {"code": "not_enrolled", "message": "this phone holds no key"})
        return got

    @router.post("/api/keyguard/unlock", dependencies=[Depends(require_owner)])
    async def keyguard_unlock(body: UnlockIn, request: Request) -> dict[str, Any]:
        phone_over_e2e(request)
        try:
            guard.unlock(body.key)
        except KeyGuardError as e:
            raise refused(e) from None
        asyncio.ensure_future(keyring_up(store, keyring))   # the key container opens with it; the old keys move
        return {"ok": True, "locked": guard.locked}

    @router.post("/api/keyguard/recover", dependencies=[Depends(require_owner)])
    async def keyguard_recover(body: RecoverIn, request: Request) -> dict[str, Any]:
        master_or_e2e(request)
        try:
            guard.recover(body.code)
        except KeyGuardError as e:
            raise refused(e) from None
        asyncio.ensure_future(keyring_up(store, keyring))
        return {"ok": True, "locked": guard.locked}

    @router.delete("/api/keyguard/phones/{device_id}", dependencies=[Depends(require_owner)])
    async def keyguard_remove_phone(device_id: int) -> dict[str, Any]:
        try:
            guard.remove_phone(device_id)
        except KeyGuardError as e:
            raise refused(e) from None
        return view()

    @router.post("/api/keyguard/prepare", dependencies=[Depends(ctx.require_master)])
    async def keyguard_prepare() -> dict[str, Any]:
        """`assistant keyguard enable` on the server machine, before it removes ASSISTANT_SECRET_KEY from .env: the same key into
        master.key, so the server still opens its secrets after the restart. Master token only; the key is never in the answer."""
        return {"ok": True, "written": guard.write_master_from_env(), "env_key": bool(guard.env_secret)}

    @router.post("/api/keyguard/disable", dependencies=[Depends(require_owner)])
    async def keyguard_disable(request: Request) -> dict[str, Any]:
        master_or_e2e(request)
        try:
            guard.disable()
        except KeyGuardError as e:
            raise refused(e) from None
        return view()

    return router
