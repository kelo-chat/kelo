"""The Kelo Nest HTTP API for the apps (nest.py does the work). Reading is open to every signed-in device; anything that changes what
is installed or what a package may do is owner-only (the owner's access key, or a phone the owner marked as theirs).

    GET    /api/nest/status                              index state (trusted key, sequence, expiry, last error), tab limit
    GET    /api/nest/browse?q=&kind=                     search the index (skill | connector | extension)
    POST   /api/nest/refresh                     owner   fetch the index again now (applies the kill-switch list)
    GET    /api/nest/packages/{id}                       details: versions, permissions in plain words and their hash, installed state, update
    POST   /api/nest/packages/{id}/install       owner   {"version"?, "accept": <permissions hash>}
    POST   /api/nest/packages/{id}/update        owner   {"version"?, "accept"?: <new permissions hash>, "switch_source"?: bool}
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
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .nest import Nest, NestError


class InstallIn(BaseModel):
    version: str | None = Field(default=None, max_length=60)
    accept: str = Field(default="", max_length=64)


class UpdateIn(BaseModel):
    version: str | None = Field(default=None, max_length=60)
    accept: str = Field(default="", max_length=64)
    switch_source: bool = False


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


def _fail(e: NestError) -> JSONResponse:
    return JSONResponse({"detail": str(e), **e.extra}, status_code=e.status)


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
        from .nest import MAX_TABS
        return {**nest.index_status(), "max_tabs": MAX_TABS}

    @app.get("/api/nest/browse", dependencies=[Depends(auth)])
    async def nest_browse(q: str = "", kind: str = "") -> Any:
        if kind not in ("", "skill", "connector", "extension"):
            raise HTTPException(422, "kind must be skill, connector or extension")
        return await acall(nest.browse(q[:100], kind))

    @app.post("/api/nest/refresh", dependencies=[Depends(owner)])
    async def nest_refresh() -> Any:
        async def run() -> dict[str, Any]:
            await nest.refresh_index(force=True)
            return nest.index_status()
        return await acall(run())

    @app.get("/api/nest/packages/{pid}", dependencies=[Depends(auth)])
    async def nest_details(pid: str) -> Any:
        return await acall(nest.details(pid))

    @app.post("/api/nest/packages/{pid}/install", dependencies=[Depends(owner)])
    async def nest_install(pid: str, body: InstallIn) -> Any:
        return await acall(nest.install(pid, version=body.version, accept=body.accept))

    @app.post("/api/nest/packages/{pid}/update", dependencies=[Depends(owner)])
    async def nest_update(pid: str, body: UpdateIn) -> Any:
        return await acall(nest.update(pid, version=body.version, accept=body.accept, switch_source=body.switch_source))

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


__all__ = ["nest_routes"]
