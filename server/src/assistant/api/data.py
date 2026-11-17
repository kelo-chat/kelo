"""Your data: take it with you, bring memories from another assistant, start over (data_controls.py, data_import.py).

All under the app's bearer token.
GET    /api/data                   {"confirm_word", "export": <status>, "import": <status>}   one call for the page
POST   /api/data/export            starts the background export (one at a time) -> the export status
GET    /api/data/export            {"state": idle|running|done|failed, "started_at"?, "finished_at"?, "bytes"?, "counts"?, "error"?,
                                    "file"?, "download"?}   "download" is the library path of the ZIP (GET it with the token: the same
                                    download as any result, /api/files/library/{name}/file); 503 when this server keeps no files
GET    /api/data/import/prompt     {"prompt"}   the text to paste into the old assistant (English; the facts come back in the owner's language)
POST   /api/data/import/text       body {"text", "assistant"?}   the old assistant's reply -> the import status (a background job)
PUT    /api/data/import/file       raw body = the ZIP of a ChatGPT, Claude or Gemini export (up to 100 MB; the format is found from the
                                    JSON inside) -> the import status; 413 too large, 422 not a ZIP, 409 an import is running
GET    /api/data/import            {"state": idle|running|done|failed|cancelled, "assistant", "total" (pieces), "done", "failed", "skipped",
                                    "added": [{"id", "text", "kind"}], "error"?, "started_at", "finished_at"?}
DELETE /api/data/import            stops a running import (what was added stays) -> the import status
POST   /api/data/reset             body {"confirm": "<the word>", "sign_out_connectors": false} -> {"ok", "backup": {"name", "path", "bytes"},
                                    "stopped": {...}, "cleared": {...}, "removed": {...}, "signed_out"?}. 400 when the word is wrong (nothing
                                    is touched); the backup ZIP is made first, and when it cannot be saved nothing is cleared (507/503).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..cred_keyring import spawn_sync
from ..data_controls import CONFIRM_WORD, DataControls, DataError
from ..data_import import MAX_ZIP, PORTABLE_PROMPT, Importer, new_upload_dir
from .context import AppContext

log = logging.getLogger(__name__)


class ImportTextIn(BaseModel):
    text: str = Field(max_length=2_000_000)
    assistant: str = Field(default="", max_length=60)


class ResetIn(BaseModel):
    confirm: str = Field(default="", max_length=60)
    sign_out_connectors: bool = False


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    data: DataControls = ctx.data_controls
    importer: Importer = ctx.data_importer
    store = ctx.store

    def fail(e: DataError) -> HTTPException:
        return HTTPException(e.status, str(e))

    @router.get("/api/data", dependencies=[Depends(require_auth)])
    async def get_data() -> dict[str, Any]:
        return {"confirm_word": CONFIRM_WORD, "export": data.export_status(), "import": importer.status()}

    @router.get("/api/data/export", dependencies=[Depends(require_auth)])
    async def get_export() -> dict[str, Any]:
        return data.export_status()

    @router.post("/api/data/export", dependencies=[Depends(require_auth)])
    async def start_export() -> dict[str, Any]:
        try:
            return data.start_export()
        except DataError as e:
            raise fail(e) from None

    @router.get("/api/data/import/prompt", dependencies=[Depends(require_auth)])
    async def import_prompt() -> dict[str, str]:
        return {"prompt": PORTABLE_PROMPT}

    @router.get("/api/data/import", dependencies=[Depends(require_auth)])
    async def get_import() -> dict[str, Any]:
        return importer.status()

    @router.post("/api/data/import/text", dependencies=[Depends(require_auth)])
    async def import_text(body: ImportTextIn) -> dict[str, Any]:
        try:
            return importer.start_text(body.text, body.assistant)
        except DataError as e:
            raise fail(e) from None

    @router.put("/api/data/import/file", dependencies=[Depends(require_auth)])
    async def import_file(request: Request) -> dict[str, Any]:
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > MAX_ZIP:
            raise HTTPException(413, "The file is larger than 100 MB.")
        if importer.running():
            raise HTTPException(409, "an import is already running")
        target = new_upload_dir() / "export.zip"
        size = 0
        try:
            with target.open("wb") as f:   # written as it arrives, never held whole
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_ZIP:
                        raise HTTPException(413, "The file is larger than 100 MB.")
                    f.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            target.parent.rmdir()
            raise
        try:
            return importer.start_file(target)
        except DataError as e:
            target.parent.rmdir()
            raise fail(e) from None

    @router.delete("/api/data/import", dependencies=[Depends(require_auth)])
    async def cancel_import() -> dict[str, Any]:
        return importer.cancel()

    def sign_out_one(name: str) -> None:
        """What DELETE /api/credentials/{name} does around the delete (api/connections.py)."""
        ctx.schedule_grants.drop_login(name)
        ctx.transient.discard(name)
        store.add_vault_log(name, "deleted", "removed by the user (start over)")
        for kind in ("key", "login"):
            ctx.entries.cancel_for(kind, name)

    data.sign_out_one = sign_out_one

    @router.post("/api/data/reset", dependencies=[Depends(require_auth)])
    async def reset(body: ResetIn) -> dict[str, Any]:
        if not data.confirmed(body.confirm):
            raise HTTPException(400, "type the confirmation word to start over")
        importer.cancel()
        async with ctx.clearing:
            try:
                backup = await asyncio.to_thread(data.backup_before_reset)   # first: when this fails nothing is cleared
            except DataError as e:
                raise fail(e) from None
            except Exception:  # noqa: BLE001
                log.exception("start over: the backup failed")
                raise HTTPException(500, "the backup could not be made, nothing was cleared") from None
            cleared = await ctx.clear_history()
            removed = await asyncio.to_thread(data.reset_stores, sign_out=body.sign_out_connectors)
        if body.sign_out_connectors:
            spawn_sync(store, getattr(ctx, "keyring", None))
        log.info("start over done: backup %s", backup["name"])
        out = {"ok": True, "backup": {k: backup[k] for k in ("name", "path", "bytes")}, "stopped": cleared["stopped"],
               "cleared": cleared["cleared"], "removed": {k: v for k, v in removed.items() if k != "signed_out"}}
        if "signed_out" in removed:
            out["signed_out"] = removed["signed_out"]
        return out

    return router
