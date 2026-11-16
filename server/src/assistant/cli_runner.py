"""Sidecar service: runs ONE subscription CLI (claude or agy) for the assistant server.

    CLI_PROVIDER=claude|agy  CLI_RUNNER_TOKEN=...  uvicorn --factory assistant.cli_runner:app_factory

POST /run (SSE of provider events), GET /status, POST /logout, GET /healthz, and the phone sign-in (cli_login):
POST /login/start, POST /login/finish {session, code}, GET /login/status?session=, POST /login/cancel {session}.
POST /update {version?} starts installing a newer CLI into the home volume and GET /update/status follows it (cli_update).
Runs in its own container with no
model keys and no ASSISTANT_TOKEN; only the shared CLI_RUNNER_TOKEN guards it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .providers import cli_agents, cli_login, cli_manage, cli_remote, cli_update
from .providers.types import ProviderError

PROVIDERS = {"claude": cli_manage.CLAUDE, "agy": cli_manage.AGY}


class RunIn(BaseModel):
    request: dict[str, Any]


class LoginIn(BaseModel):
    session: str = ""
    code: str = ""


class UpdateIn(BaseModel):
    version: str | None = None


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def create_app(provider_key: str, token: str, *, stream_fn: Any = None, login: cli_login.CliLogin | None = None,
               updater: cli_update.CliUpdater | None = None) -> FastAPI:
    if provider_key not in PROVIDERS:
        raise RuntimeError("CLI_PROVIDER must be 'claude' or 'agy'")
    if len(token) < 16:
        raise RuntimeError("CLI_RUNNER_TOKEN must be set (16+ characters); `assistant setup` creates it")
    provider = PROVIDERS[provider_key]
    run = stream_fn or cli_agents.stream
    signin = login or cli_login.CliLogin(provider)
    running = {"runs": 0}                     # chats using the CLI right now: an update does not start under one
    retired: list[Any] = []                   # clients of the previous binary, closed once no chat uses them

    def close_retired() -> None:
        while retired and not running["runs"]:
            with contextlib.suppress(Exception):
                retired.pop().close()

    def switched() -> None:
        old = cli_agents.retire_client(provider)
        if old is not None:
            retired.append(old)
        close_retired()

    upd = updater or cli_update.CliUpdater(provider_key)
    upd.busy = lambda: running["runs"] > 0
    upd.on_switch = switched

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        with contextlib.suppress(Exception):   # a broken volume copy only means the image's copy is used
            await upd.select()
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def require_auth(request: Request) -> None:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(value.encode(), token.encode()):
            raise HTTPException(401, "invalid or missing bearer token")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/status", dependencies=[Depends(require_auth)])
    async def status() -> dict[str, Any]:
        st = await cli_manage.cli_status(provider, local=True)
        return {**st, "self_update": True, "cli_source": upd.source}   # self_update: this runner has POST /update

    # ---- the CLI updating itself into the home volume (no Docker involved; see providers/cli_update.py)
    @app.post("/update", dependencies=[Depends(require_auth)])
    async def update(body: UpdateIn | None = None) -> dict[str, Any]:
        try:
            return await upd.start((body.version if body else None) or None)
        except cli_update.UpdateRefused as e:
            raise HTTPException(e.status, str(e)) from None

    @app.get("/update/status", dependencies=[Depends(require_auth)])
    async def update_status() -> dict[str, Any]:
        return upd.status()

    @app.get("/models", dependencies=[Depends(require_auth)])
    async def models() -> dict[str, Any]:
        try:
            return {"models": await cli_manage.cli_models(provider, local=True)}
        except ProviderError as e:
            raise HTTPException(502, str(e)) from None

    @app.get("/usage", dependencies=[Depends(require_auth)])
    async def usage() -> dict[str, Any]:
        try:
            return {"windows": await cli_manage.cli_usage(provider, local=True)}
        except ProviderError as e:
            raise HTTPException(502, str(e)) from None

    @app.post("/logout", dependencies=[Depends(require_auth)])
    async def logout() -> dict[str, Any]:
        try:
            return await cli_manage.cli_logout(provider, local=True)
        except ProviderError as e:
            raise HTTPException(502, str(e)) from None

    # ---- sign-in from the phone: the CLI's own login, driven here (the code is never logged or echoed back)
    def _login_error(e: ProviderError) -> HTTPException:
        return HTTPException(e.status if isinstance(e.status, int) and 400 <= e.status < 600 else 502, str(e))

    @app.post("/login/start", dependencies=[Depends(require_auth)])
    async def login_start() -> dict[str, Any]:
        try:
            return await signin.start()
        except ProviderError as e:
            raise _login_error(e) from None

    @app.post("/login/finish", dependencies=[Depends(require_auth)])
    async def login_finish(body: LoginIn) -> dict[str, Any]:
        try:
            return await signin.finish(body.session, body.code)
        except ProviderError as e:
            raise _login_error(e) from None

    @app.get("/login/status", dependencies=[Depends(require_auth)])
    async def login_status(session: str = "") -> dict[str, Any]:
        try:
            return signin.status(session)
        except ProviderError as e:
            raise _login_error(e) from None

    @app.post("/login/cancel", dependencies=[Depends(require_auth)])
    async def login_cancel(body: LoginIn) -> dict[str, Any]:
        return await signin.cancel(body.session)

    @app.post("/run", dependencies=[Depends(require_auth)])
    async def run_cli(body: RunIn) -> StreamingResponse:
        req = cli_remote.request_from_json(body.request)

        async def events() -> AsyncIterator[str]:
            running["runs"] += 1
            try:
                async for ev in run(provider, req):
                    yield _sse(cli_remote.encode_event(ev))
            except ProviderError as e:
                yield _sse({"type": "error", "message": str(e)[:500], "status": e.status})
            except Exception as e:  # noqa: BLE001 - relay to the server, never crash the stream
                yield _sse({"type": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"})
            finally:
                running["runs"] -= 1
                close_retired()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/warm", dependencies=[Depends(require_auth)])
    async def warm_cli(body: RunIn) -> dict[str, Any]:
        """Start the chat's CLI session for this system prompt without a model request (agy only; see AgyPool.warm)."""
        try:
            return {"warm": await cli_agents.warm(provider, cli_remote.request_from_json(body.request))}
        except Exception as e:  # noqa: BLE001 - warming is only an optimisation
            return {"warm": False, "error": type(e).__name__}

    return app


def _show_own_logs() -> None:
    """The sidecar's own INFO lines (agy_pool: which session a call used, and why a new one started) in the container log next to
    uvicorn's; only times, modes and reasons are logged there, never prompts or tokens."""
    own = logging.getLogger("assistant")
    if not own.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
        own.addHandler(h)
        own.setLevel(logging.INFO)
        own.propagate = False


def app_factory() -> FastAPI:
    _show_own_logs()
    return create_app(os.environ.get("CLI_PROVIDER", ""), os.environ.get("CLI_RUNNER_TOKEN", ""))
