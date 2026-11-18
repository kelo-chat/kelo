"""Voice input on the Kelo server (voice.py): which speech model the owner chose, its download, and the recording sent for text.

The audio arrives sealed like every API call (KE2E) and is held in memory for one recognition only (voice.py).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import voice_models as vm
from ..voice import MAX_BODY, VoiceError
from .context import AppContext

STATUS = {"voice_not_ready": 409, "voice_too_long": 413, "voice_format": 415, "voice_busy": 429,
          "voice_runtime_missing": 503, "voice_timeout": 504}


class ModelIn(BaseModel):
    model: str


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    voice = ctx.voice
    require_auth = ctx.require_auth

    @router.get("/api/voice", dependencies=[Depends(require_auth)])
    async def get_voice(lang: str | None = None) -> dict[str, Any]:
        return await voice.view(lang)

    @router.post("/api/voice/model", dependencies=[Depends(require_auth)])
    async def choose_model(body: ModelIn, request: Request, lang: str | None = None) -> JSONResponse:
        if body.model not in vm.CATALOG:
            return JSONResponse({"detail": "unknown speech model"}, status_code=422)
        await voice.choose(body.model)
        view = await voice.view(lang)
        return JSONResponse(view, status_code=202 if view["state"] == "downloading" else 200)

    @router.post("/api/voice/decline", dependencies=[Depends(require_auth)])
    async def decline(lang: str | None = None) -> dict[str, Any]:
        await voice.decline()
        return await voice.view(lang)

    @router.delete("/api/voice/model", dependencies=[Depends(require_auth)])
    async def remove_model(lang: str | None = None) -> dict[str, Any]:
        await voice.remove()
        return await voice.view(lang)

    @router.post("/api/voice/transcribe", dependencies=[Depends(require_auth)])
    async def transcribe(request: Request, lang: str | None = None) -> Any:
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return JSONResponse({"code": "voice_too_long"}, status_code=413)
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > MAX_BODY:
                return JSONResponse({"code": "voice_too_long"}, status_code=413)
        try:
            return await voice.transcribe(bytes(buf), lang)
        except VoiceError as e:
            return JSONResponse({"code": e.code}, status_code=STATUS[e.code])
        finally:
            del buf

    return router
