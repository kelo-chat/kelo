"""The Kelo Nest HTTP API for the apps (nest.py does the work). Reading is open to every signed-in device; anything that changes what
is installed or what a package may do is owner-only (the owner's access key, or a phone the owner marked as theirs).

    GET    /api/nest/status                              the tab limit
    POST   /api/nest/install-from-link           owner   {"url"} -> the package's card (its "token"); nothing installed; 422 not a package
    POST   /api/nest/install-from-link/confirm   owner   {"token", "accept": <permissions hash>} -> the installed row; 410 link_expired,
                                                         409 look_again | already_installed | changed, 422 refused
    GET    /api/nest/packages/{id}                       the installed row (state, permissions, repo, update as last checked); 404
    POST   /api/nest/packages/{id}/update        owner   {"accept"?: <new permissions hash>} (a pinned package is 409)
    DELETE /api/nest/packages/{id}?keep_data=0   owner   uninstall (its entries and its vault item go too, unless keep_data)
    PUT    /api/nest/packages/{id}/pin           owner   {"pinned": bool}
    PUT    /api/nest/packages/{id}/enabled       owner   {"enabled": bool}
    PUT    /api/nest/packages/{id}/permissions   owner   {"revoke": ["reads.calendar", ...], "grant": [...]}
    PUT    /api/nest/packages/{id}/client-id     owner   {"client_id": "..."} (an OAuth package with client "owner")
    GET    /api/nest/installed                           every installed package with state, permissions, update
    GET    /api/nest/updates                             installed packages with an update (not pinned)
    GET    /api/nest/extensions                          what to render where (docs/nest-ui.md)
    PUT    /api/nest/extensions/tabs             owner   {"show": ["<package>/<surface>", ...]} (at most MAX_TABS)
    GET    /api/nest/extensions/{id}/data?surface=       one surface's screen with its data filled in
    POST   /api/nest/extensions/{id}/actions/{action}    {"form"?: {...}, "item"?: <row key>, "data"?: <data id>, "confirm"?: <token>}
    GET    /api/nest/extensions/{id}/image?src=          a picture of a screen, fetched by the server
    GET    /api/nest/drafts                              every draft with state, version, updated_at, shared
    GET    /api/nest/drafts/{id}                         the files (text) and validator findings
    DELETE /api/nest/drafts/{id}                 owner   delete draft
    POST   /api/nest/drafts/{id}/share           owner   start chat turn to share to GitHub (202 {"turn_id"}, 409 github_write_needed)
    POST   /api/nest/drafts/from-skill/{id}      owner   copy custom skill into a draft

Link previews, link installs and updates load the package strictly: an unknown top-level manifest field, or key-like text in any
file, refuses it (422 with the reason; the reason names the file and line, never the value).

New and optional, older apps ignore them. The preview card (install-from-link) and an update's view (check_update) carry
"contents": {"files": [{"path", "bytes", "sha256"}] sorted by path, "total_bytes", "texts": {"<file>": "<full text>"} for every
.md file and the manifest (a text longer than 64 KB is cut and ends with "…"; "cut" lists those files), "endpoints": every host and
URL the package can reach, sorted, "warnings": notes from loading it}. An update's view also has "changed": {"added", "removed",
"modified"} (paths against the installed files), and an installed row's update shows that "changed" without "contents".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .nest import MAX_TABS, Nest, NestError


class UpdateIn(BaseModel):
    accept: str = Field(default="", max_length=64)


class PinIn(BaseModel):
    pinned: bool


class EnabledIn(BaseModel):
    enabled: bool


class PermissionsIn(BaseModel):
    revoke: list[str] = Field(default_factory=list, max_length=40)
    grant: list[str] = Field(default_factory=list, max_length=40)


class ClientIdIn(BaseModel):
    client_id: str = Field(max_length=200)


class TabsIn(BaseModel):
    show: list[str] = Field(default_factory=list, max_length=20)


class ActionIn(BaseModel):
    form: dict[str, Any] = Field(default_factory=dict)
    item: str = Field(default="", max_length=64)
    data: str = Field(default="", max_length=40)
    confirm: str = Field(default="", max_length=64)


class InstallFromLinkIn(BaseModel):
    url: str = Field(max_length=500)


class InstallConfirmIn(BaseModel):
    token: str = Field(max_length=64)
    accept: str = Field(default="", max_length=64)


class FromSkillIn(BaseModel):
    draft_id: str = Field(default="", max_length=64)


def _fail(e: NestError) -> JSONResponse:
    extra = dict(e.extra)
    if "error" in extra:
        extra.setdefault("code", extra["error"])   # the apps read ApiError.code (top-level "code")
    return JSONResponse({"detail": str(e), **extra}, status_code=e.status)


def nest_routes(app: FastAPI, nest: Nest, auth: Callable[..., Any], owner: Callable[..., Any]) -> None:
    def call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except NestError as e:
            return _fail(e)

    async def acall(coro: Any) -> Any:
        try:
            return await coro
        except NestError as e:
            return _fail(e)

    @app.get("/api/nest/status", dependencies=[Depends(auth)])
    async def nest_status() -> dict[str, Any]:
        return {"max_tabs": MAX_TABS}

    @app.post("/api/nest/install-from-link", dependencies=[Depends(owner)])
    async def nest_install_from_link(body: InstallFromLinkIn, request: Request) -> Any:
        lang = request.headers.get("accept-language")
        return await acall(nest.install_from_link(body.url, lang=lang))

    @app.post("/api/nest/install-from-link/confirm", dependencies=[Depends(owner)])
    async def nest_install_confirm(body: InstallConfirmIn, request: Request) -> Any:
        lang = request.headers.get("accept-language")
        return await acall(nest.install_link(body.token, body.accept, lang=lang))

    @app.get("/api/nest/packages/{pid}", dependencies=[Depends(auth)])
    async def nest_package(pid: str, request: Request) -> Any:
        lang = request.headers.get("accept-language")
        return call(lambda: nest.package(pid, lang))

    @app.post("/api/nest/packages/{pid}/update", dependencies=[Depends(owner)])
    async def nest_update(pid: str, body: UpdateIn, request: Request) -> Any:
        lang = request.headers.get("accept-language")
        return await acall(nest.update(pid, accept=body.accept, lang=lang))

    @app.delete("/api/nest/packages/{pid}", dependencies=[Depends(owner)])
    async def nest_uninstall(pid: str, keep_data: bool = False) -> dict[str, bool]:
        if not nest.uninstall(pid, keep_data=keep_data):
            raise HTTPException(404, "not installed")
        return {"ok": True}

    @app.put("/api/nest/packages/{pid}/pin", dependencies=[Depends(owner)])
    async def nest_pin(pid: str, body: PinIn) -> Any:
        return call(lambda: nest.pin(pid, body.pinned))

    @app.put("/api/nest/packages/{pid}/enabled", dependencies=[Depends(owner)])
    async def nest_enabled(pid: str, body: EnabledIn) -> Any:
        return call(lambda: nest.set_enabled(pid, body.enabled))

    @app.put("/api/nest/packages/{pid}/permissions", dependencies=[Depends(owner)])
    async def nest_permissions(pid: str, body: PermissionsIn) -> Any:
        return call(lambda: nest.set_permissions(pid, revoke=body.revoke, grant=body.grant))

    @app.put("/api/nest/packages/{pid}/client-id", dependencies=[Depends(owner)])
    async def nest_client_id(pid: str, body: ClientIdIn) -> Any:
        return call(lambda: nest.set_client_id(pid, body.client_id))

    @app.get("/api/nest/installed", dependencies=[Depends(auth)])
    async def nest_installed() -> dict[str, Any]:
        return {"items": nest.installed()}

    @app.get("/api/nest/updates", dependencies=[Depends(auth)])
    async def nest_updates() -> dict[str, Any]:
        return {"items": await nest.updates()}

    @app.get("/api/nest/extensions", dependencies=[Depends(auth)])
    async def nest_extensions() -> dict[str, Any]:
        return nest.extensions()

    @app.put("/api/nest/extensions/tabs", dependencies=[Depends(owner)])
    async def nest_tabs(body: TabsIn) -> dict[str, Any]:
        return nest.choose_tabs(body.show)

    @app.get("/api/nest/extensions/{pid}/data", dependencies=[Depends(auth)])
    async def nest_data(pid: str, surface: str) -> Any:
        return await acall(nest.screen(pid, surface))

    @app.post("/api/nest/extensions/{pid}/actions/{aid}", dependencies=[Depends(auth)])
    async def nest_action(pid: str, aid: str, body: ActionIn, request: Request) -> Any:
        return await acall(nest.action(pid, aid, form=body.form, item=body.item, data=body.data, confirm=body.confirm, request=request))

    @app.get("/api/nest/extensions/{pid}/image", dependencies=[Depends(auth)])
    async def nest_image(pid: str, src: str) -> Response:
        try:
            body, ctype = await nest.image(pid, src[:1000])
        except NestError as e:
            return _fail(e)
        return Response(body, media_type=ctype, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                                                         "Content-Security-Policy": "default-src 'none'"})

    @app.get("/api/nest/drafts", dependencies=[Depends(auth)])
    async def nest_drafts() -> dict[str, Any]:
        return {"items": nest.drafts_summary()}

    @app.get("/api/nest/drafts/{pid}", dependencies=[Depends(auth)])
    async def nest_draft_details(pid: str) -> Any:
        return call(lambda: nest.draft_details(pid))

    @app.delete("/api/nest/drafts/{pid}", dependencies=[Depends(owner)])
    async def nest_draft_delete(pid: str) -> dict[str, bool]:
        def run() -> dict[str, bool]:
            nest.delete_draft(pid)
            return {"ok": True}
        return call(run)

    @app.post("/api/nest/drafts/{pid}/share", status_code=202, dependencies=[Depends(owner)])
    async def nest_draft_share(pid: str, request: Request) -> Any:
        d = nest.draft(pid)
        if d is None:
            raise HTTPException(404, f"draft {pid!r} not found")
        if not nest.has_github_write():
            return JSONResponse({"code": "github_write_needed", "detail": "github write access needed"}, status_code=409)
        if hasattr(nest, "start_share_turn") and nest.start_share_turn is not None:
            turn_id = await nest.start_share_turn(d, request)
            return {"turn_id": turn_id}
        raise HTTPException(500, "share turn not configured")

    @app.post("/api/nest/drafts/from-skill/{pid}", dependencies=[Depends(owner)])
    async def nest_draft_from_skill(pid: str, body: FromSkillIn | None = None) -> Any:
        target_id = body.draft_id if body and body.draft_id else ""
        return call(lambda: nest.draft_from_skill(pid, draft_id=target_id))


__all__ = ["nest_routes"]
