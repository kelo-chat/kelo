"""What Kelo did outside (done_once.py): the steps that sent, changed or deleted, for the later "what Kelo did" screen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    ledger = ctx.action_ledger

    @router.get("/api/actions", dependencies=[Depends(require_auth)])
    async def actions(days: int = Query(7, ge=1, le=30)) -> dict[str, Any]:
        """The steps of the last `days` days (up to 30), newest first: {"items": [{"id", "ts", "run_id", "task_id", "turn_id",
        "source" ("chat", "schedule:<id>", ...), "tool", "kind" (send | change | delete), "connector"|null, "targets" (as they are: an
        address, a number, "calendar", "connector:<name>", "site:<domain>"), "state" (done | unknown | failed), "outside_id" (the message,
        order or entry id the answer showed, "" when none), "detail" (at most 200 characters about a failure or an unknown result, never a
        message body), "checked_at"|null (when action_result settled an unknown one)}]}. An "unknown" one is a step that may or may not
        have gone out; the agent checks it and marks it done or not done."""
        return {"items": [r.public() for r in ledger.list(days)]}

    return router
