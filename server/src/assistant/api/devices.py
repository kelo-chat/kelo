"""Devices and pairing: a phone joins with a one-time code (shown as a QR), reviewers with multi-use access codes. Every device has the same rights; removing a phone and everything it shared."""

from __future__ import annotations

import io
from urllib.parse import urlsplit
from typing import Any
import segno
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi import APIRouter
from .context import AppContext
from .. import approval_sign, i18n
from ..keyguard import KeyGuardError


class AccessCodeIn(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    days: float = Field(default=30, gt=0, le=365)
    uses: int = Field(default=20, ge=1, le=1000)


class DeviceOwnerIn(BaseModel):
    owner: bool


class PairIn(BaseModel):
    code: str = Field(min_length=16, max_length=128)
    name: str = Field(default="", max_length=60)


class ApprovalKeyIn(BaseModel):
    alg: str = Field(max_length=10)                 # "ES256": ECDSA P-256 with SHA-256 (approval_sign.py)
    public_key: str = Field(min_length=1, max_length=400)   # base64 SubjectPublicKeyInfo DER, made in the phone's keystore
    sig: str | None = Field(default=None, max_length=400)   # replacing a key: the old key's signature over approval_sign.key_message


class PairStartIn(BaseModel):
    origin: str = Field(default="", max_length=300)   # the address this device reached the server at, e.g. https://me.example.com
    owner: bool = False         # accepted and ignored: there are no owner devices any more (every device has the same rights)
    first_owner: bool = False   # accepted and ignored


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    auth = ctx.auth
    e2e_server = ctx.e2e_server
    phone_cals = ctx.phone_cals
    phone_data = ctx.phone_data
    push = ctx.push
    require_auth = ctx.require_auth
    require_master = ctx.require_master
    settings = ctx.settings
    store = ctx.store

    # ---- devices. A phone is added with a one-time code (shown as a QR); it then holds its own token and can be removed alone.
    @router.post("/api/devices/pair", dependencies=[Depends(require_auth)])
    async def new_pairing(request: Request, body: PairStartIn | None = None) -> dict[str, Any]:
        # `owner` / `first_owner` in the body are ignored; the answer keeps "owner" for older apps (true, false on a review copy)
        out: dict[str, Any] = {"code": store.new_pairing(), "expires_in": int(store.PAIRING_TTL), "owner": auth.owner_flag(settings.review_mode),
                               "fingerprint": e2e_server.key.fingerprint}
        # a fixed public address wins over the page's own address (a temporary tunnel), so the new phone keeps working after a restart
        origin = (settings.public_url or (body.origin if body else "")).strip().rstrip("/")
        if origin:
            parts = urlsplit(origin)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query or parts.fragment \
                    or parts.username or parts.password:
                raise HTTPException(422, "origin must look like https://host or http://host:port")
            # the server's KE2E key rides in the fragment (never sent to any server): the app pins it (e2e-and-phone-key.md 1.3)
            link = f"{origin}/#pair={out['code']}&k={e2e_server.key.public_b64}"
            out["link"] = link
            svg = io.BytesIO()   # a standalone SVG (with its namespace) so it can be shown as an image
            segno.make(link, error="m").save(svg, kind="svg", scale=6, border=2, xmldecl=False)
            out["qr_svg"] = svg.getvalue().decode()
        return out

    @router.post("/api/pair")
    async def pair(body: PairIn, request: Request) -> dict[str, Any]:
        recent, now = auth.throttle(request)   # the same guard as a wrong token
        name = "".join(c for c in body.name.strip() if c.isprintable())[:40] or "device"
        # an older app reads everything after "#pair=" as the code, so it may send "CODE&k=<server key>" (KE2E links): the code is
        # what comes before the first "&" (docs/security/e2e-and-phone-key.md section 5 item 1)
        made = store.redeem_pairing(body.code.strip().split("&", 1)[0], name)
        if not made:
            recent.append(now)
            raise HTTPException(400, "this link or code has expired, was already used or was withdrawn; make a new one on a signed-in device")
        return {"token": made[1], "device_id": made[0]}

    # multi-use access codes (store.new_access_code): app store reviewers type one from the review notes; `assistant review code`
    @router.get("/api/devices/codes", dependencies=[Depends(require_master)])
    async def get_access_codes() -> dict[str, Any]:
        return {"codes": store.access_codes()}

    @router.post("/api/devices/codes", dependencies=[Depends(require_master)])
    async def new_access_code(body: AccessCodeIn) -> dict[str, Any]:
        try:
            code_id, code = store.new_access_code(body.label, days=body.days, uses=body.uses)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        row = next(c for c in store.access_codes() if c["id"] == code_id)
        return {"id": code_id, "code": code, "label": row["label"], "expires_at": row["expires_at"], "max_uses": row["max_uses"]}

    @router.delete("/api/devices/codes/{code_id}", dependencies=[Depends(require_master)])
    async def revoke_access_code(code_id: int) -> dict[str, Any]:
        gone = store.revoke_access_code(code_id)
        if gone is None:
            raise HTTPException(404, "no such access code")
        for device_id in gone:
            forget_device(device_id)
        return {"ok": True, "signed_out": len(gone)}

    # signed payment approvals: the calling phone stores its approval public key (docs/approval-signing.md). One per device; replacing
    # it needs the old key's signature over the new one, unless the device has no key yet.
    @router.post("/api/devices/approval-key", dependencies=[Depends(require_auth)])
    async def put_approval_key(request: Request, body: ApprovalKeyIn) -> dict[str, Any]:
        device_id = request.state.device_id
        if device_id is None:   # the master token is not a phone: it has no keystore to sign with
            raise HTTPException(403, {"code": "no_device", "message": i18n.t("Only a signed-in phone can add an approval key.")})
        try:
            approval_sign.check_public_key(body.alg, body.public_key)
        except approval_sign.BadKey as e:
            raise HTTPException(422, str(e)) from None
        old = store.approval_key(device_id)
        if old:
            if not body.sig:
                raise HTTPException(403, {"code": "signature_required", "message": i18n.t("Replacing the approval key needs the signature of the current key.")})
            if not approval_sign.verify(old, approval_sign.key_message(body.alg, body.public_key), body.sig):
                raise HTTPException(403, {"code": "bad_signature", "message": i18n.t("The signature does not match the current approval key.")})
        if not store.set_approval_key(device_id, body.public_key):
            raise HTTPException(404, "no such device")
        return {"ok": True, "approval_key": True}

    @router.get("/api/devices", dependencies=[Depends(require_auth)])
    async def get_devices(request: Request) -> dict[str, Any]:
        flag = auth.owner_flag(settings.review_mode)   # "owner": kept for older apps; every device has it (not on a review copy)
        return {"devices": [{**d, "owner": flag} for d in store.devices()], "current": request.state.device_id}

    @router.put("/api/devices/{device_id}/owner", dependencies=[Depends(require_master)])
    async def put_device_owner(device_id: int, body: DeviceOwnerIn) -> dict[str, Any]:
        """Kept for older tools: owner rights no longer exist (every device has the same rights), so this changes nothing."""
        if not any(d["id"] == device_id for d in store.devices()):
            raise HTTPException(404, "no such device")
        return {"ok": True, "id": device_id, "owner": auth.owner_flag(settings.review_mode)}

    def forget_device(device_id: int) -> None:
        try:
            store.keyguard.forget_device(device_id)   # a reviewer's device never holds the last copy; best effort here
        except KeyGuardError:
            pass
        push.unregister(device_id)   # a removed phone gets no more pushes
        phone_cals.delete(str(device_id))   # nor keeps its calendar here
        phone_data.delete_owner(str(device_id))   # nor its contacts, messages, calls, notifications, health data or waiting tasks

    @router.delete("/api/devices/{device_id}", dependencies=[Depends(require_auth)])
    async def remove_device(device_id: int) -> dict[str, bool]:
        if not any(d["id"] == device_id for d in store.devices()):
            raise HTTPException(404, "no such device")
        try:   # its Keyguard copy goes with it; the last one while Keyguard is on is refused (keyguard.py)
            store.keyguard.forget_device(device_id)
        except KeyGuardError as e:
            raise HTTPException(e.status, {"code": e.code, "message": str(e)}) from None
        if not store.delete_device(device_id):
            raise HTTPException(404, "no such device")
        forget_device(device_id)
        return {"ok": True}

    return router
