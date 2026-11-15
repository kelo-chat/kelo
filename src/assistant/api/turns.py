"""Following a reply: GET /api/turns/{id} (what is new since `after`, waiting up to `wait` seconds) and stopping it."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, HTTPException
from ..server import POLL_WAIT_MAX
from ..turn_engine import Turn
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    cancel_turn = ctx.cancel_turn
    require_auth = ctx.require_auth
    turns = ctx.turns

    def find_turn(turn_id: str) -> Turn:
        turn = turns.get(turn_id)
        if turn is None:
            raise HTTPException(404, "that reply is gone (it was stopped, or finished a while ago)")
        return turn

    @router.get("/api/turns/{turn_id}", dependencies=[Depends(require_auth)])
    async def follow_turn(turn_id: str, after: int = 0, wait: float = 20.0) -> dict[str, Any]:
        """What is new since event `after`; waits up to `wait` seconds when nothing is. Asking again with the same `after` is always safe."""
        turn = find_turn(turn_id)
        after = max(0, min(after, len(turn.events)))
        events, done = await turn.wait(after, min(max(wait, 0.0), POLL_WAIT_MAX))
        return {"events": events, "next": after + len(events), "done": done}

    @router.delete("/api/turns/{turn_id}", dependencies=[Depends(require_auth)])
    async def stop_turn(turn_id: str) -> dict[str, bool]:
        cancel_turn(find_turn(turn_id))
        return {"ok": True}
    return router
