"""Kelo Nest screens on this server (nest.py, nest_routes.py): what a package's screens may show and do, and the agent turns they start."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from fastapi import Request
from .. import tool_labels
from ..nest_routes import nest_routes
from ..server import TalkIn
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    deliverables = ctx.deliverables
    nest = ctx.nest
    phone_cals = ctx.phone_cals
    prepare_talk = ctx.prepare_talk
    require_auth = ctx.require_auth
    require_owner = ctx.require_owner
    start_turn = ctx.start_turn
    store = ctx.store

    # ---- Kelo Nest (nest.py, nest_routes.py): what a package's screens may show and do on this server
    async def nest_agent(shown: str, note: str, request: Request) -> str:
        """A button in a package's screen asked Kelo for something: a normal chat reply. The chat shows what was tapped; the package's
        words go to Kelo only as fenced third-party text (nest.py), so its rules and approvals apply as for any message."""
        provider, model, ctx, observe, started = prepare_talk(TalkIn(text=shown[:200]), request)
        ctx.tail[-1]["content"] = f"{ctx.tail[-1]['content']}\n\n{note}"
        return started(start_turn(provider, model, ctx.tail, observe, context=ctx)).id

    def nest_logged(pid: str, kind: str, where: str, decision: str) -> None:
        if kind in ("connector", "mcp"):   # the owner confirmed a request to the package's service in its screen
            tool = "connector_send" if kind == "connector" else "mcp_call_tool"
            activity.log_approval(uuid.uuid4().hex, task_id=None, tool=tool, label=tool_labels.label(tool), site=where,
                                  detail=f"{pid}: {where}", decision="allowed" if decision == "allowed" else "denied")

    def nest_when(ts: Any) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            return datetime.fromtimestamp(float(ts)).astimezone(ZoneInfo(store.profile().get("timezone") or "UTC")).isoformat(timespec="minutes")
        except (TypeError, ValueError, OSError, ZoneInfoNotFoundError):
            return ""

    nest.start_agent = nest_agent
    nest.on_action = nest_logged
    nest.on_withdrawn = lambda title, body: activity.notify("nest_withdrawn", title, body)
    nest.feeds = {
        "calendar.upcoming": lambda n: [{"title": e["title"], "start": nest_when(e["start"]), "where": e.get("where") or ""}
                                        for e in phone_cals.upcoming(48)][:n],
        "tasks.recent": lambda n: [{"title": r["title"], "status": r["status"], "started": nest_when(r["started_at"]), "result": r.get("result") or ""}
                                   for r in activity.tasks(limit=n)],
        "files.recent": lambda n: [{"name": f["name"], "kind": f["kind"], "bytes": f["bytes"], "created": nest_when(f["created"])}
                                   for f in (deliverables.list() if deliverables else [])][:n],
    }
    nest_routes(router, nest, require_auth, require_owner)
    return router
