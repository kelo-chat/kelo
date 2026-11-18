"""The agent's own files (MEMORY.md, ...), the user's uploads and their previews; the library of results (files_api.py) is included here too.

FILE MANAGER for the agent's files (all under the app's bearer token; paths are relative to the agent's home, e.g. "memory/people/kim.md"):
GET    /api/files[?folders=1]          [{"path", "bytes", "writable"}] every readable text file; with folders=1 also the folders (even empty
                                         ones) as {"path", "type": "dir", "bytes": null, "writable"} and every row has "type": file|dir
GET    /api/files/{path}               {"path", "content"}; 404 missing, unreadable or not reachable
PUT    /api/files/{path}               body {"content"} (<= 100,000 bytes) -> {"path", "ok"}: creates the file (and its folders) when it is
                                         new, replaces it otherwise (the old text is kept in .history, written by "user"). Writable places
                                         only: the core files, memory/ and notes/ (422 anywhere else); 507 storage full
POST   /api/files/{path}/folder        a new folder inside memory/ or notes/ (parents too; an existing folder is fine) -> {"ok", "path"};
                                         422 outside those two, 404 for a path that is not reachable
DELETE /api/files/{path}               moves a file, or a folder with its files, inside memory/ or notes/ to the trash ->
                                         {"ok", "trashed": {"id", "path", "bytes", "deleted_at", "type": file|dir, "purge_at"}}.
                                         409 {"detail": "core file", "reset": true} for assistant.md, user.md, MEMORY.md, AGENTS.md,
                                         CHECKLIST.md and NOTIFY_PREFS.md (use reset); 422 for uploads/ and files/ (they have their own delete
                                         and trash: DELETE /api/uploads/{name}, DELETE /api/files/library/{name}) and for the top level or
                                         the memory/ and notes/ folders themselves; 404 missing or not reachable
POST   /api/files/{path}/reset         core files only: the old text goes to the trash, the file gets its template text again (as at first
                                         start) -> {"ok", "path", "trashed": {...}|null}; 422 for any other file
GET    /api/home-trash                 [{"id", "path", "bytes", "deleted_at", "type", "purge_at"}] newest deletion first. Kept TRASH_DAYS (30)
                                         days, then purged by a daily job. Not the library's trash (GET /api/files/trash, results in files/)
POST   /api/home-trash/{id}/restore    back to its old path -> {"ok", "path"}; when something is there again the path is "<name> (restored)"
                                         ("(restored 2)"...); 404 unknown id
DELETE /api/home-trash/{id}            gone for good -> {"ok"}; 404 unknown id
Never reachable through any of these: anything outside the agent's home (absolute paths, "..", symbolic links), hidden names (.env,
.history, .backup, .trash itself, the vault and the database live elsewhere): 404 (PUT: 422). The agent's own file tools (read_file,
write_file, list_files) cannot reach the trash either. The agent learns about the owner's edit or delete the way it learns about a PUT: its
files are read fresh every turn (the file list and the core files in its prompt), so a deleted file is simply gone and a reset file is
the template again; nothing else is sent to it. The library of results is in files_api.py (/api/files/library/*, /api/files/trash).
"""

from __future__ import annotations

import asyncio
from typing import Any
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from .. import home as home_mod
from ..home import CoreFile, Home, HomeError, HomeFull, NotDeletable, UploadBusy, UploadTooLarge, UploadUnknown
from ..i18n import t
from ..photos import MAX_IMAGE, is_image
from ..media import MediaError, kind_of, mime_of
from ..tools.extract import extracted_path
from ..deliverables import Deliverables
from ..files_api import files_routers
from fastapi import APIRouter
from .context import AppContext


class FileIn(BaseModel):
    content: str = Field(max_length=200_000)


class PartsStart(BaseModel):
    size: int = Field(gt=0)
    sha256: str | None = None


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
    async def list_agent_files(folders: bool = False) -> list[dict[str, Any]]:
        return need_home().tree(folders)

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

    @router.post("/api/files/{path:path}/folder", dependencies=[Depends(require_auth)])
    async def make_agent_folder(path: str) -> dict[str, Any]:
        try:
            return {"ok": True, "path": need_home().mkdir(path)}
        except NotDeletable as e:
            raise HTTPException(422, str(e)) from None
        except HomeError as e:
            raise HTTPException(404, str(e)) from None

    @router.post("/api/files/{path:path}/reset", dependencies=[Depends(require_auth)])
    async def reset_agent_file(path: str) -> dict[str, Any]:
        try:
            return {"ok": True, **need_home().reset(path)}
        except NotDeletable as e:
            raise HTTPException(422, str(e)) from None
        except HomeFull as e:
            raise HTTPException(507, str(e)) from None
        except HomeError as e:
            raise HTTPException(404, str(e)) from None

    @router.delete("/api/files/{path:path}", dependencies=[Depends(require_auth)])
    async def delete_agent_file(path: str) -> Any:
        try:
            return {"ok": True, "trashed": need_home().trash(path, by="user")}
        except CoreFile:
            return JSONResponse({"detail": "core file", "reset": True}, status_code=409)
        except NotDeletable as e:
            raise HTTPException(422, str(e)) from None
        except HomeError as e:
            raise HTTPException(404, str(e)) from None

    @router.get("/api/home-trash", dependencies=[Depends(require_auth)])
    async def home_trash() -> list[dict[str, Any]]:
        return need_home().trashed()

    @router.post("/api/home-trash/{tid}/restore", dependencies=[Depends(require_auth)])
    async def home_trash_restore(tid: str) -> dict[str, Any]:
        try:
            return {"ok": True, "path": need_home().restore(tid)}
        except HomeFull as e:
            raise HTTPException(507, str(e)) from None
        except HomeError as e:
            raise HTTPException(404, str(e)) from None

    @router.delete("/api/home-trash/{tid}", dependencies=[Depends(require_auth)])
    async def home_trash_forget(tid: str) -> dict[str, bool]:
        try:
            need_home().forget_trashed(tid)
        except HomeError as e:
            raise HTTPException(404, str(e)) from None
        return {"ok": True}

    # UPLOADS (contract for the apps; all under the bearer token).
    #   GET  /api/uploads/limits -> {"single_max": 99000000 | null, "part_size": 90000000, "image_max": 15000000}. single_max is the most
    #        one PUT /api/uploads/{name} may carry on THIS route: a number when the request came through Cloudflare (it has a CF-Ray header:
    #        the tunnel or a proxied own domain refuses bodies over 100 MB), null on Tailscale or a direct own domain (no per-file limit,
    #        only the storage cap and free disk). Files over single_max, and on any route files over part_size if the app prefers, go in parts:
    #   POST   /api/uploads/{name}/parts  {"size": bytes, "sha256": hex?} -> {"upload_id", "part_size", "parts"}
    #          (413 photo over image_max, 507 no room for `size`, 409 three part-uploads already open, 422 name/size refused)
    #   PUT    /api/uploads/parts/{upload_id}/{index}  raw body = part `index` (0-based); every part exactly part_size bytes except the last
    #          (the rest of the file) -> {"ok": true, "received": number of parts held}. Sent again = replaced (resume). 422 wrong size or
    #          index, 404 unknown or swept id, 507 no room (the part is dropped, earlier parts stay)
    #   GET    /api/uploads/parts/{upload_id} -> {"name", "size", "part_size", "received": [indices]}  (what to send after a broken connection)
    #   POST   /api/uploads/parts/{upload_id}/finish -> {"path", "ok": true}; 422 a part is missing (upload stays open: send it, finish again),
    #          or total size / sha256 wrong (upload dropped, start again); 507 no room to join
    #   DELETE /api/uploads/parts/{upload_id} -> {"ok": true}; open part-uploads untouched for 24 h are removed by the server.

    def one_request_limit(request: Request) -> int | None:
        """Per route: through Cloudflare (CF-Ray present) one request body is at most MAX_UPLOAD; otherwise no per-file limit."""
        return home_mod.MAX_UPLOAD if "cf-ray" in request.headers else None

    @router.get("/api/uploads/limits", dependencies=[Depends(require_auth)])
    async def upload_limits(request: Request) -> dict[str, Any]:
        return {"single_max": one_request_limit(request), "part_size": home_mod.PART_SIZE, "image_max": MAX_IMAGE}

    @router.put("/api/uploads/{name}", dependencies=[Depends(require_auth)])
    async def upload_document(name: str, request: Request) -> dict[str, Any]:
        """The user hands the agent a file: PDF / Word / Excel, a photo (JPEG, PNG, WebP, HEIC; at most 15 MB; GPS removed, HEIC
        kept as JPEG), a video or a sound file. Raw request body = the file; it is written to disk as it arrives (never held in
        memory whole). Per-file limit: none, except when the request came through Cloudflare (CF-Ray header): then home.MAX_UPLOAD
        (99,000,000 bytes, under Cloudflare's 100 MB body limit) and the app should send bigger files in parts (below). Photos are
        always at most 15 MB, and nothing passes the storage cap or the free disk.
        Answer: {"path": "uploads/<name as kept>", "ok": true} (a HEIC comes back as .jpg). 413 too large, 507 no room, 422 refused."""
        h = need_home()
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        limit = one_request_limit(request)
        try:
            return {"path": await h.receive_upload(name, request.stream(), declared=declared, limit=limit, unlimited=limit is None), "ok": True}
        except UploadTooLarge as e:
            if limit is not None and not is_image(name):
                raise HTTPException(413, t("Through this connection one request can be up to {size}; the app sends bigger files in parts.",
                                           size="100 MB")) from None
            raise HTTPException(413, str(e)) from None
        except HomeFull as e:
            raise HTTPException(507, str(e)) from None
        except HomeError as e:
            raise HTTPException(422, str(e)) from None

    def part_error(e: HomeError) -> HTTPException:
        if isinstance(e, UploadTooLarge):
            return HTTPException(413, str(e))
        if isinstance(e, HomeFull):
            return HTTPException(507, str(e))
        if isinstance(e, UploadBusy):
            return HTTPException(409, str(e))
        if isinstance(e, UploadUnknown):
            return HTTPException(404, str(e))
        return HTTPException(422, str(e))

    @router.post("/api/uploads/{name}/parts", dependencies=[Depends(require_auth)])
    async def upload_parts_start(name: str, body: PartsStart) -> dict[str, Any]:
        try:
            return need_home().start_parts(name, body.size, body.sha256)
        except HomeError as e:
            raise part_error(e) from None

    @router.put("/api/uploads/parts/{upload_id}/{index}", dependencies=[Depends(require_auth)])
    async def upload_part(upload_id: str, index: int, request: Request) -> dict[str, Any]:
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        try:
            return {"ok": True, "received": await need_home().receive_part(upload_id, index, request.stream(), declared=declared)}
        except HomeError as e:
            raise part_error(e) from None

    @router.get("/api/uploads/parts/{upload_id}", dependencies=[Depends(require_auth)])
    async def upload_parts_status(upload_id: str) -> dict[str, Any]:
        try:
            return need_home().parts_status(upload_id)
        except HomeError as e:
            raise part_error(e) from None

    @router.post("/api/uploads/parts/{upload_id}/finish", dependencies=[Depends(require_auth)])
    async def upload_parts_finish(upload_id: str) -> dict[str, Any]:
        try:
            return {"path": await need_home().finish_parts(upload_id), "ok": True}
        except HomeError as e:
            raise part_error(e) from None

    @router.delete("/api/uploads/parts/{upload_id}", dependencies=[Depends(require_auth)])
    async def upload_parts_cancel(upload_id: str) -> dict[str, bool]:
        try:
            need_home().cancel_parts(upload_id)
        except HomeError as e:
            raise part_error(e) from None
        return {"ok": True}

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
