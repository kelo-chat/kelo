"""Error answers (docs/security/e2e-and-phone-key.md 5.10): an HTTPException whose detail is {"code": ..., ...} is answered as
{"detail": {...as before...}, "code": "<code>"}, so every app's error type can read `code` at the top level next to `detail`
(older apps keep reading detail.code). Everything else is FastAPI's own answer, unchanged."""

from __future__ import annotations

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException


async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return JSONResponse({"detail": detail, "code": detail["code"]}, status_code=exc.status_code, headers=exc.headers)
    return await http_exception_handler(request, exc)


async def locked_error(request: Request, exc: Exception) -> Response:
    """Keyguard holds no key (keyguard.Locked): 423 {"code": "locked"}; the apps show "Kelo is locked: open it" and unlock."""
    return JSONResponse({"detail": {"code": "locked", "message": str(exc)}, "code": "locked"}, status_code=423)
