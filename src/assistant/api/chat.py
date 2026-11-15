"""The chat: the AI consent (ai_consent.py; App Store guideline 5.1.2(i)), a stateless turn (/api/chat), and a turn of the stored conversation streamed (/api/talk) or started to be followed (/api/turns)."""

from __future__ import annotations

import logging
from typing import Any
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from ..tools.browser import (
    browser_owner,
)
from ..server import TalkIn
from fastapi import APIRouter
from .context import AppContext

log = logging.getLogger("uvicorn.error")


class AiConsentIn(BaseModel):
    accepted: bool


class ChatIn(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    fallback: bool = True   # false: this request goes to the named model only, never to the profile's fallback model
    secrets: str | None = Field(default=None, pattern="^(allow|mask)$")


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    consents = ctx.consents
    prepare_talk = ctx.prepare_talk
    require_auth = ctx.require_auth
    resolve_model = ctx.resolve_model
    secret_check = ctx.secret_check
    settings = ctx.settings
    start_turn = ctx.start_turn
    store = ctx.store
    stream_turn = ctx.stream_turn
    turn_response = ctx.turn_response
    with_system = ctx.with_system

    # ---- asking before chats go to an AI company (ai_consent.py; App Store guideline 5.1.2(i))
    @router.get("/api/ai-consent", dependencies=[Depends(require_auth)])
    async def get_ai_consent() -> dict[str, Any]:
        return consents.status(store.profile(), settings.provider)

    @router.put("/api/ai-consent", dependencies=[Depends(require_auth)])
    async def put_ai_consent(body: AiConsentIn, request: Request) -> dict[str, Any]:
        """The owner agreed on a device: kept once for the server, with that device and the time."""
        if not body.accepted:
            raise HTTPException(422, "accepted must be true (Not now records nothing)")
        st = consents.status(store.profile(), settings.provider)
        device = str(request.state.device_id or "owner")
        consents.accept(device, st["company"])
        log.info("AI data-sharing consent recorded (device %s, service %s)", device, st["company"] or "-")
        return consents.status(store.profile(), settings.provider)

    @router.delete("/api/ai-consent", dependencies=[Depends(require_auth)])
    async def delete_ai_consent() -> dict[str, Any]:
        """Takes the agreement back: the next message asks again."""
        if not consents.withdraw():
            raise HTTPException(404, "no agreement recorded")
        return consents.status(store.profile(), settings.provider)

    @router.post("/api/chat", dependencies=[Depends(require_auth)])
    async def chat(body: ChatIn, request: Request) -> StreamingResponse:
        browser_owner.set(str(request.state.device_id or "owner"))
        provider, model = resolve_model(body.provider, body.model)
        for m in body.messages:   # an API client's own conversation: its user messages are checked like the app's
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                m["content"] = secret_check(m["content"], body.secrets)
        return turn_response(provider, model, with_system(body.messages), effort=body.effort, fallback=body.fallback, source="api")

    @router.post("/api/talk", dependencies=[Depends(require_auth)])
    async def talk(body: TalkIn, request: Request) -> StreamingResponse:
        """The reply as a stream (the page that leaves stops it). Quick tunnels and sleeping phones drop streams: the app uses /api/turns."""
        provider, model, ctx, observe, started = prepare_talk(body, request)
        return stream_turn(started(start_turn(provider, model, ctx.tail, observe, context=ctx)))

    @router.post("/api/turns", dependencies=[Depends(require_auth)])
    async def begin_turn(body: TalkIn, request: Request) -> dict[str, str]:
        """Start a reply and return at once; the page follows it with short requests to GET /api/turns/{id}."""
        provider, model, ctx, observe, started = prepare_talk(body, request)
        return {"id": started(start_turn(provider, model, ctx.tail, observe, context=ctx)).id}
    return router
