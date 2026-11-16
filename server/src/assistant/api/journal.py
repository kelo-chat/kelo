"""The journal (journal.py): the nightly entries, rebuilt or deleted from the app."""

from __future__ import annotations

import re
from typing import Any
from fastapi import Depends, HTTPException
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    journal = ctx.journal
    require_auth = ctx.require_auth

    # ---- the journal -------------------------------------------------------------------------------------------------------------
    @router.get("/api/journal", dependencies=[Depends(require_auth)])
    async def get_journal(month: str | None = None) -> dict[str, Any]:
        month = month or __import__("datetime").datetime.now(journal.tz()).strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(422, "month must look like 2026-11")
        return {"month": month, "items": journal.entries(month=month), "pending": journal.pending(month)}

    @router.post("/api/journal/rebuild", dependencies=[Depends(require_auth)])
    async def rebuild_journal(date: str) -> dict[str, Any]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise HTTPException(422, "date must look like 2026-11-06")
        try:
            return await journal.write_day(date)
        except ValueError:
            raise HTTPException(422, "no such date") from None

    @router.delete("/api/journal/{entry_id}", dependencies=[Depends(require_auth)])
    async def delete_journal_entry(entry_id: int) -> dict[str, bool]:
        if not journal.delete(entry_id):
            raise HTTPException(404, "no such entry")
        return {"ok": True}

    return router
