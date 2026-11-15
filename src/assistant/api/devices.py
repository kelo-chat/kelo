"""Devices and pairing: a phone joins with a one-time code (shown as a QR), reviewers with multi-use access codes; owner rights; removing a phone and everything it shared."""

from __future__ import annotations

import io
from urllib.parse import urlsplit
from typing import Any
import segno
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi import APIRouter
from .context import AppContext
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


class PairStartIn(BaseModel):
    origin: str = Field(default="", max_length=300)   # the address this device reached the server at, e.g. https://me.example.com
    owner: bool = False   # the phone it signs in gets the owner's rights (operator settings); master token only (`assistant phone add --owner`)
    first_owner: bool = False


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
        owner = bool(body and (body.owner or (body.first_owner and not any(d["owner"] for d in store.devices()))))
        if body and (body.owner or body.first_owner) and request.state.device_id is not None:   # owner rights are handed out on the server machine only, never phone to phone
            raise HTTPException(403, "only the access key on the server machine can sign a phone in as the owner (assistant phone add --owner)")
        out: dict[str, Any] = {"code": store.new_pairing(owner=owner), "expires_in": int(store.PAIRING_TTL), "owner": owner,
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

    @router.get("/api/devices", dependencies=[Depends(require_auth)])
    async def get_devices(request: Request) -> dict[str, Any]:
        return {"devices": store.devices(), "current": request.state.device_id}

    @router.put("/api/devices/{device_id}/owner", dependencies=[Depends(require_master)])
    async def put_device_owner(device_id: int, body: DeviceOwnerIn) -> dict[str, Any]:
        """Give a paired phone the owner's rights (operator settings) or take them back: master token only (`assistant phone owner`)."""
        if not store.set_device_owner(device_id, body.owner):
            raise HTTPException(404, "no such device")
        return {"ok": True, "id": device_id, "owner": body.owner}

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
