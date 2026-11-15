"""Looking back (reflection.py) and keeping the memory files current (memory_upkeep.py)."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, HTTPException
from ..memory_upkeep import MemoryUpkeep
from ..reflection import Reflection
from ..home import HomeError
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    memory_upkeep = ctx.memory_upkeep
    reflection = ctx.reflection
    require_auth = ctx.require_auth

    # ---- looking back (reflection.py) ----------------------------------------------------------------------------------------------
    def need_reflection() -> Reflection:
        if reflection is None:
            raise HTTPException(404, "the agent's files are not enabled on this server")
        return reflection

    @router.get("/api/reflection", dependencies=[Depends(require_auth)])
    async def get_reflection() -> dict[str, Any]:
        out = need_reflection().public()
        if memory_upkeep is not None:
            out["memory"] = memory_upkeep.public()
        return out

    @router.put("/api/reflection/guide", dependencies=[Depends(require_auth)])
    async def put_reflection_guide() -> dict[str, Any]:
        raise HTTPException(410, "the written guide was replaced by learned rules: PATCH /api/reflection/rules/{id}")

    @router.patch("/api/reflection/rules/{rule_id}", dependencies=[Depends(require_auth)])
    async def patch_reflection_rule(rule_id: int, body: dict[str, Any]) -> dict[str, Any]:
        status = body.get("status") if isinstance(body, dict) else None
        try:
            return need_reflection().set_status(rule_id, str(status or ""))
        except KeyError:
            raise HTTPException(404, "no such rule") from None
        except HomeError as e:
            raise HTTPException(422, str(e)) from None

    @router.put("/api/reflection/enabled", dependencies=[Depends(require_auth)])
    async def put_reflection_enabled(body: dict[str, Any]) -> dict[str, Any]:
        on = body.get("enabled") if isinstance(body, dict) else None
        if not isinstance(on, bool):
            raise HTTPException(422, "enabled must be true or false")
        return need_reflection().set_enabled(on)

    @router.post("/api/reflection/run", dependencies=[Depends(require_auth)])
    async def run_reflection(force: bool = False) -> dict[str, Any]:
        out = await need_reflection().run(force=force)
        if out["status"] == "busy":
            raise HTTPException(409, out["reason"])
        return out

    # ---- keeping the memory files current (memory_upkeep.py) -----------------------------------------------------------------------
    def need_upkeep() -> MemoryUpkeep:
        if memory_upkeep is None:
            raise HTTPException(404, "the agent's files are not enabled on this server")
        return memory_upkeep

    @router.get("/api/reflection/memory", dependencies=[Depends(require_auth)])
    async def get_memory_pass() -> dict[str, Any]:
        return need_upkeep().changes()

    @router.post("/api/reflection/memory/run", dependencies=[Depends(require_auth)])
    async def run_memory_pass(force: bool = False) -> dict[str, Any]:
        out = await need_upkeep().run(trigger="manual", force=force)
        if out["status"] == "busy":
            raise HTTPException(409, out["reason"])
        return out

    @router.post("/api/reflection/memory/undo", dependencies=[Depends(require_auth)])
    async def undo_memory_pass() -> dict[str, Any]:
        up = need_upkeep()
        if up.lock.locked():
            raise HTTPException(409, "a memory pass is going")
        try:
            return up.undo()
        except LookupError as e:
            raise HTTPException(404, str(e)) from None

    return router
