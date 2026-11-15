"""The agent's own files (MEMORY.md, ...), the user's uploads and their previews; the library of results (files_api.py) is included here too."""

from __future__ import annotations

import asyncio
from typing import Any
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from ..home import Home, HomeError, HomeFull, UploadTooLarge
from ..media import MediaError, kind_of, mime_of
from ..tools.extract import extracted_path
from ..deliverables import Deliverables
from ..files_api import files_routers
from fastapi import APIRouter
from .context import AppContext


class FileIn(BaseModel):
    content: str = Field(max_length=200_000)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    deliverables = ctx.deliverables
    extract_upload = ctx.extract_upload
    home = ctx.home
    require_auth = ctx.require_auth
    settings = ctx.settings

    def need_home() -> Home:
        if home is None:
            raise HTTPException(404, "the agent's files are not enabled on this server")
        return home

    def need_library() -> Deliverables:
        if deliverables is None:
            raise HTTPException(404, "the agent's files are not enabled on this server")
        return deliverables

    # the library of results (files_api.py has the contracts); before /api/files/{path}, which would otherwise take these paths
    _files_api, _files_public = files_routers(need_library, settings.public_url)
    app.include_router(_files_api, dependencies=[Depends(require_auth)])
    app.include_router(_files_public)

    @router.get("/api/files", dependencies=[Depends(require_auth)])
    async def list_agent_files() -> list[dict[str, Any]]:
        return need_home().tree()

    @router.get("/api/files/{path:path}", dependencies=[Depends(require_auth)])
    async def read_agent_file(path: str) -> dict[str, Any]:
        try:
            return {"path": path, "content": need_home().read(path)}
        except HomeError as e:
            raise HTTPException(404, str(e)) from None

    @router.put("/api/files/{path:path}", dependencies=[Depends(require_auth)])
    async def write_agent_file(path: str, body: FileIn) -> dict[str, Any]:
        try:
            return {"path": need_home().write(path, body.content, by="user"), "ok": True}
        except HomeFull as e:
            raise HTTPException(507, str(e)) from None
        except HomeError as e:
            raise HTTPException(422, str(e)) from None

    @router.put("/api/uploads/{name}", dependencies=[Depends(require_auth)])
    async def upload_document(name: str, request: Request) -> dict[str, Any]:
        """The user hands the agent a file: PDF / Word / Excel, a photo (JPEG, PNG, WebP, HEIC; at most 15 MB; GPS removed, HEIC
        kept as JPEG), a video or a sound file. Raw request body = the file; it is written to disk as it arrives (never held in
        memory whole), at most home.MAX_UPLOAD (99,000,000 bytes: "up to 100 MB", under Cloudflare's 100 MB body limit) per file and never past the storage cap or the free disk.
        Answer: {"path": "uploads/<name as kept>", "ok": true} (a HEIC comes back as .jpg). 413 too large, 507 no room, 422 refused."""
        h = need_home()
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        try:
            return {"path": await h.receive_upload(name, request.stream(), declared=declared), "ok": True}
        except UploadTooLarge as e:
            raise HTTPException(413, str(e)) from None
        except HomeFull as e:
            raise HTTPException(507, str(e)) from None
        except HomeError as e:
            raise HTTPException(422, str(e)) from None

    @router.delete("/api/uploads/{name}", dependencies=[Depends(require_auth)])
    async def delete_upload(name: str) -> dict[str, bool]:
        try:
            return {"ok": need_home().delete_upload(name)}
        except HomeError as e:
            raise HTTPException(404, str(e)) from None

    @router.get("/api/uploads", dependencies=[Depends(require_auth)])
    async def list_uploads() -> list[dict[str, Any]]:
        """[{"path", "bytes", "modified", "kind": document|image|video|audio, "type": MIME, "duration"?: seconds (video/audio),
        "width"?, "height"? (video)}]. Lengths are read once per file and kept (media.py)."""
        h = need_home()
        rows = h.uploads()

        def facts() -> None:
            for u in rows:
                u["type"] = mime_of(u["path"])
                if u["kind"] in ("video", "audio"):
                    u.update({k: v for k, v in h.media.info(h.root / u["path"]).items() if v is not None})
        await asyncio.to_thread(facts)
        return rows

    MEDIA_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "Content-Security-Policy": "sandbox; default-src 'none'"}

    @router.get("/api/uploads/{name}/file", dependencies=[Depends(require_auth)])
    async def upload_file(name: str, inline: bool = False) -> FileResponse:
        """The user's own upload, as it was uploaded: a download, or with ?inline=1 to show or play in the app. Range requests are
        answered (206), so a video streams and can be skipped through without fetching it all."""
        try:
            path = need_home().upload_path(name)
        except HomeError as e:
            raise HTTPException(404, str(e)) from None
        return FileResponse(path, media_type=mime_of(path.name), filename=path.name, content_disposition_type="inline" if inline else "attachment",
                            headers=MEDIA_HEADERS)

    @router.get("/api/uploads/{name}/thumb", dependencies=[Depends(require_auth)])
    async def upload_thumb(name: str, size: int = 256) -> FileResponse:
        """A JPEG preview of an uploaded picture (turned upright) or video (a frame about a second in), at most `size` pixels on
        its long side (rounded up to 128/256/512/1024/2048). Made once and kept. 404 no such file, 422 no preview possible."""
        h = need_home()
        try:
            path = h.upload_path(name)
            thumb = await asyncio.to_thread(h.media.thumb, path, size)
        except HomeError as e:
            raise HTTPException(404, str(e)) from None
        except MediaError as e:
            raise HTTPException(422, str(e)) from None
        return FileResponse(thumb, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"})

    @router.get("/api/uploads/{name}/text", dependencies=[Depends(require_auth)])
    async def upload_text(name: str) -> dict[str, Any]:
        """The text of an uploaded document, for reading it in the app (extracted on first use, then kept)."""
        h = need_home()
        try:
            path = h.upload_path(name)
            if kind_of(path.name) != "document":
                raise HTTPException(422, "only documents have text to read")
            rel = extracted_path(path.stem)
            try:
                text = h.read(rel)
            except HomeError:
                assert extract_upload is not None
                await extract_upload(name)
                text = h.read(rel)
        except HomeFull as e:
            raise HTTPException(507, str(e)) from None
        except HomeError as e:
            raise HTTPException(404, str(e)) from None
        return {"name": path.name, "text": text, "truncated": "[truncated:" in text[-80:]}

    return router
