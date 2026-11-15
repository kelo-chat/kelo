"""HTTP routes for the user's library of results ("Results" in the app): files/ in the agent's home (see deliverables.py).

Contracts (all under the app's bearer token, except the share link itself):
GET    /api/files/library                 [{"name", "path": "files/<name>", "bytes", "type": "<MIME type>", "kind": document|spreadsheet|
                                            image|audio|video|data|archive|other, "created": <unix s>, "shared_until": <unix s>|null}]
                                            newest first
GET    /api/files/library/{name}/file     the file itself, always as a download (attachment, nosniff, no-store)
DELETE /api/files/library/{name}          moves it to the trash (its links stop working) -> {"ok", "trashed": {"id", "name", "bytes",
                                            "deleted_at", "purge_at"}}; kept TRASH_DAYS (30) days, then purged by a daily job
GET    /api/files/trash                   [{"id", "name", "bytes", "deleted_at", "purge_at"}] newest deletion first
POST   /api/files/library/{name}/restore  {name} = a trashed file's name (the latest of that name) or its trash id -> {"ok", "name": <kept as>}
POST   /api/files/library/{name}/share    body {"confirm": true, "hours"?: <=48, "origin"?: "https://<address the app uses>"} -> {"url",
                                            "path": "/shared/<token>", "token", "name", "expires_at"}. Every link is made on purpose:
                                            without "confirm": true the answer is 428 and nothing is made. The url uses the server's
                                            public address (ASSISTANT_PUBLIC_URL), else "origin", else the address of this request.
DELETE /api/files/library/{name}/share    revokes every link to the file -> {"ok", "revoked": <how many>}
GET    /shared/{token}                    public: the file as a download while the link is valid (48 h, not revoked, file not deleted);
                                            404 otherwise. No other file can be reached with a token.
404 {"detail"} for a missing file; 507 when the storage cap is reached (restore).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .deliverables import SHARE_HOURS, TRASH_DAYS, Deliverables, type_of
from .home import HomeError, HomeFull

DOWNLOAD_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": "sandbox; default-src 'none'"}


class ShareIn(BaseModel):
    confirm: bool = False
    hours: float = Field(default=SHARE_HOURS, gt=0, le=SHARE_HOURS)
    origin: str = Field(default="", max_length=300)


def share_url(base: str, token: str) -> str:
    return f"{base.rstrip('/')}/shared/{token}"


def request_origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def files_routers(get: Callable[[], Deliverables], public_url: str = "") -> tuple[APIRouter, APIRouter]:
    """(routes that need the bearer token, the public share route). `get` returns the library or raises HTTPException(404)."""
    api, public = APIRouter(), APIRouter()

    def missing(e: Exception) -> HTTPException:
        return HTTPException(404, str(e))

    @api.get("/api/files/library")
    async def library_list() -> list[dict[str, Any]]:
        return get().list()

    @api.get("/api/files/trash")
    async def library_trash() -> list[dict[str, Any]]:
        return get().trashed()

    @api.get("/api/files/library/{name}/file")
    async def library_file(name: str) -> FileResponse:
        try:
            path = get().path(name)
        except HomeError as e:
            raise missing(e) from None
        return FileResponse(path, media_type=type_of(path.name), filename=path.name, headers=DOWNLOAD_HEADERS)

    @api.delete("/api/files/library/{name}")
    async def library_delete(name: str) -> dict[str, Any]:
        try:
            return {"ok": True, "trashed": get().trash(name)}
        except HomeError as e:
            raise missing(e) from None

    @api.post("/api/files/library/{name}/restore")
    async def library_restore(name: str) -> dict[str, Any]:
        try:
            return {"ok": True, "name": get().restore(name)}
        except HomeFull as e:
            raise HTTPException(507, str(e)) from None
        except HomeError as e:
            raise missing(e) from None

    @api.post("/api/files/library/{name}/share")
    async def library_share(name: str, body: ShareIn, request: Request) -> dict[str, Any]:
        lib = get()
        try:
            lib.path(name)
        except HomeError as e:
            raise missing(e) from None
        if not body.confirm:
            raise HTTPException(428, "a share link makes the file downloadable by anyone who has the link: send confirm=true after the user agreed")
        s = lib.share(name, body.hours)
        base = public_url or body.origin.strip() or request_origin(request)
        return {"url": share_url(base, s["token"]), "path": f"/shared/{s['token']}", "token": s["token"], "name": s["name"],
                "expires_at": s["expires_at"]}

    @api.delete("/api/files/library/{name}/share")
    async def library_unshare(name: str) -> dict[str, Any]:
        return {"ok": True, "revoked": get().unshare(name)}

    @public.get("/shared/{token}")
    async def shared_file(token: str) -> FileResponse:
        try:
            lib = get()
        except HTTPException:
            raise HTTPException(404, "not found") from None
        path = lib.shared(token)
        if path is None:
            raise HTTPException(404, "this link does not exist or has expired")
        return FileResponse(path, media_type=type_of(path.name), filename=path.name, headers=DOWNLOAD_HEADERS)

    return api, public


__all__ = ["files_routers", "share_url", "TRASH_DAYS"]
