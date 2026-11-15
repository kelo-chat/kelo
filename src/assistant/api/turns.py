"""Following a reply: GET /api/turns/{id} (what is new since `after`, waiting up to `wait` seconds), the WebSocket GET /api/turns/{id}/ws
(the same events, numbered, pushed as they happen) and stopping it. After a restart both answer from the reply log (turn_log.py)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from fastapi import Depends, HTTPException, WebSocket, WebSocketDisconnect
from ..server import POLL_WAIT_MAX
from ..turn_engine import Turn
from fastapi import APIRouter
from .context import AppContext

PING_SECONDS = 25.0   # a WebSocket with nothing new says so this often (a proxy would cut a quiet connection)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    cancel_turn = ctx.cancel_turn
    require_auth = ctx.require_auth
    turns = ctx.turns
    turn_log = getattr(ctx, "turn_log", None)

    def find_turn(turn_id: str) -> Turn:
        turn = turns.get(turn_id)
        if turn is None:
            raise HTTPException(404, "that reply is gone (it was stopped, or finished a while ago)")
        return turn

    @router.get("/api/turns/{turn_id}", dependencies=[Depends(require_auth)])
    async def follow_turn(turn_id: str, after: int = 0, wait: float = 20.0) -> dict[str, Any]:
        """What is new since event `after`; waits up to `wait` seconds when nothing is. Asking again with the same `after` is always safe."""
        turn = turns.get(turn_id)
        if turn is None:
            got = turn_log.events(turn_id, max(0, after)) if turn_log is not None else None
            if got is None:
                raise HTTPException(404, "that reply is gone (it was stopped, or finished a while ago)")
            return {"events": got, "next": max(0, after) + len(got), "done": True}
        after = max(0, min(after, len(turn.events)))
        events, done = await turn.wait(after, min(max(wait, 0.0), POLL_WAIT_MAX))
        return {"events": events, "next": after + len(events), "done": done}

    @router.websocket("/api/turns/{turn_id}/ws")
    async def turn_socket(ws: WebSocket, turn_id: str) -> None:
        """Follow a reply: {"seq", "event"} for each event from `after` on, then {"done": true, "next"}; {"type": "ping"} every
        PING_SECONDS while nothing happens; {"type": "gone"} and close 4404 for an unknown reply; 4401 without a valid token."""
        try:
            require_auth(ws)  # type: ignore[arg-type]  # a WebSocket has the same headers / client / state
        except HTTPException:
            await ws.accept()
            await ws.close(4401, "unauthorized")
            return
        await ws.accept()
        try:
            after = max(0, int(ws.query_params.get("after") or 0))
        except ValueError:
            after = 0
        closed = asyncio.Event()

        async def reader() -> None:          # the client may send pings; any message or a disconnect is noticed here
            try:
                while True:
                    await ws.receive_text()
            except Exception:  # noqa: BLE001
                closed.set()

        listen = asyncio.create_task(reader())
        try:
            turn = turns.get(turn_id)
            if turn is None:
                got = turn_log.events(turn_id, after) if turn_log is not None else None
                if got is None:
                    await ws.send_text(json.dumps({"type": "gone"}))
                    await ws.close(4404, "no such reply")
                    return
                for i, ev in enumerate(got):
                    await ws.send_text(json.dumps({"seq": after + i, "event": ev}, ensure_ascii=False))
                await ws.send_text(json.dumps({"done": True, "next": after + len(got)}))
                await ws.close(1000)
                return
            after = min(after, len(turn.events))
            while not closed.is_set():
                events, done = await turn.wait(after, PING_SECONDS)
                for ev in events:
                    await ws.send_text(json.dumps({"seq": after, "event": ev}, ensure_ascii=False))
                    after += 1
                if done and after >= len(turn.events):
                    await ws.send_text(json.dumps({"done": True, "next": after}))
                    await ws.close(1000)
                    return
                if not events:
                    await ws.send_text(json.dumps({"type": "ping"}))
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            listen.cancel()

    @router.delete("/api/turns/{turn_id}", dependencies=[Depends(require_auth)])
    async def stop_turn(turn_id: str) -> dict[str, bool]:
        cancel_turn(find_turn(turn_id))
        return {"ok": True}
    return router
