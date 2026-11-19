"""The agent screen: live status (polled every few seconds) and the task log."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, HTTPException, Request
from .. import i18n
from ..api.schemas import SeenIn
from ..server import HELD_STATUS
from ..turn_engine import Turn
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    avatar = ctx.avatar
    briefing = ctx.briefing
    cancel_turn = ctx.cancel_turn
    device_of = ctx.device_of
    entries = ctx.entries
    followups = ctx.followups
    held_by = ctx.held_by
    pending_approvals = ctx.pending_approvals
    require_auth = ctx.require_auth
    running_turns = ctx.running_turns
    store = ctx.store
    turn_line = ctx.turn_line
    turns = ctx.turns

    def live_line(t: Turn) -> str:
        return i18n.t(HELD_STATUS) if held_by(t.owner) else turn_line(t)

    @router.get("/api/agent/status", dependencies=[Depends(require_auth)])
    async def agent_status(request: Request) -> dict[str, Any]:
        live = running_turns()
        top = live[0] if live else None
        holder = held_by(top.owner if top else device_of(request))
        return {"busy": bool(live), "status": live_line(top) if top else None, "task_id": top.task_id if top else None,
                "turn_id": top.id if top else None, "title": top.title if top else None, "source": top.source if top else None,
                "controller": "user" if holder else "agent", "controlled_by": holder,
                "running": [{"task_id": t.task_id, "turn_id": t.id, "title": t.title, "status": live_line(t), "source": t.source,
                             "waiting_approval": t.waiting > 0, "tab": t.owner, "controller": "user" if held_by(t.owner) else "agent"}
                            for t in live],
                "pending_approvals": len(pending_approvals), "open_followups": followups.open_count(), "unseen_tasks": activity.unseen_tasks(),
                "last_reply_failed": activity.last_reply_failed(),
                "secret_requests": entries.open_count(), "secret_request_kinds": entries.open_kinds(), "unseen_notifications": activity.unseen_notifications(), "avatar_version": avatar.version(),
                "briefing_rev": briefing.revision(),
                "agent_name": (prof := store.profile())["agent_name"], "tone": prof["tone"],
                "locked": store.keyguard.locked}   # Keyguard holds no key: the apps show "Kelo is locked" (keyguard.py)

    def live_fields(task: dict[str, Any]) -> dict[str, Any]:
        t = turns.get(task["turn_id"])
        running = t is not None and not t.done
        return {**task, "live_status": live_line(t) if running and t else None,
                "turn_available": t is not None}

    @router.get("/api/tasks", dependencies=[Depends(require_auth)])
    async def get_tasks(limit: int = 50, before: int | None = None, source: str | None = None) -> dict[str, Any]:
        items = [live_fields(x) for x in activity.tasks(limit, before, source)]
        return {"items": items, "unseen": activity.unseen_tasks(),
                "next_before": items[-1]["id"] if items and len(items) >= max(1, min(limit, 200)) else None}

    @router.post("/api/tasks/seen", dependencies=[Depends(require_auth)])
    async def seen_tasks(body: SeenIn) -> dict[str, Any]:
        return {"ok": True, "marked": activity.mark_tasks_seen(body.up_to), "unseen": activity.unseen_tasks()}

    @router.get("/api/tasks/{task_id}", dependencies=[Depends(require_auth)])
    async def get_task(task_id: int) -> dict[str, Any]:
        task = activity.task(task_id, full=True)
        if task is None:
            raise HTTPException(404, "no such task")
        return {**live_fields(task), "approvals": activity.approvals(50, task_id=task_id)}

    @router.post("/api/tasks/{task_id}/stop", dependencies=[Depends(require_auth)])
    async def stop_task(task_id: int) -> dict[str, bool]:
        t = next((t for t in turns.values() if t.task_id == task_id and not t.done), None)
        if t is None:
            raise HTTPException(404, "that task is not running")
        cancel_turn(t)
        return {"ok": True}
    return router
