"""Calls with Kelo in the app (calls.py): the app says the owner answered the incoming call, and Kelo starts its greeting.

The call's words come later as normal spoken messages (POST /api/turns with `call`, see chat.py); this route only starts the reply
with the hidden "answered" message, so the greeting is Kelo's own and the owner sees nothing of the hidden text.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field

from ..calls import CALL_ID_PATTERN, answered_text
from ..server import TalkIn
from .context import AppContext


class AnsweredIn(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    topic: str | None = Field(default=None, max_length=120)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    prepare_talk = ctx.prepare_talk
    start_turn = ctx.start_turn
    require_auth = ctx.require_auth

    @router.post("/api/calls/{call_id}/answered", dependencies=[Depends(require_auth)])
    async def answered(body: AnsweredIn, request: Request,
                       call_id: str = Path(pattern=CALL_ID_PATTERN)) -> dict[str, Any]:
        """The owner took the call: Kelo's turn starts with the hidden message (answered_text). 428 when the AI company is not yet
        agreed to (the app asks first, nothing is sent)."""
        talk = TalkIn(text=answered_text(body.topic or ""), timezone=body.timezone, ai_consent="check", call=call_id, can_call=True)
        provider, model, tctx, observe, started = await prepare_talk(talk, request)
        turn = started(start_turn(provider, model, tctx.tail, observe, context=tctx, can_call=True))
        return {"id": turn.id}

    return router
