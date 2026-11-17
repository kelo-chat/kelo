"""What conditional tasks and background jobs may do on their own (schedule_grants.py, tools/task_permission.py): list and revoke."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, HTTPException
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    schedule_grants = ctx.schedule_grants
    watchers = ctx.watchers
    workers = ctx.workers
    preapprovals = ctx.preapprovals

    # ---- what conditional tasks may do on their own (schedule_grants.py; contracts with SCHEDULES above)
    @router.get("/api/watchers/{wid}/grants", dependencies=[Depends(require_auth)])
    async def watcher_grants(wid: int) -> dict[str, Any]:
        if watchers.get(wid) is None:
            raise HTTPException(404, "no such watcher")
        return {"items": schedule_grants.list(scope=f"watcher:{wid}")}

    @router.delete("/api/watchers/{wid}/grants/{gid}", dependencies=[Depends(require_auth)])
    async def revoke_watcher_grant(wid: int, gid: int) -> dict[str, bool]:
        if not schedule_grants.revoke(gid, scope=f"watcher:{wid}"):
            raise HTTPException(404, "no such permission")
        return {"ok": True}

    @router.get("/api/workers/{wid}/grants", dependencies=[Depends(require_auth)])
    async def worker_grants(wid: str) -> dict[str, Any]:
        if workers.get(wid) is None:
            raise HTTPException(404, "no such job")
        return {"items": schedule_grants.list(scope=f"job:{wid}")}

    @router.delete("/api/workers/{wid}/grants/{gid}", dependencies=[Depends(require_auth)])
    async def revoke_worker_grant(wid: str, gid: int) -> dict[str, bool]:
        if not schedule_grants.revoke(gid, scope=f"job:{wid}"):
            raise HTTPException(404, "no such permission")
        return {"ok": True}

    @router.get("/api/task-grants", dependencies=[Depends(require_auth)])
    async def all_task_grants() -> dict[str, Any]:
        return {"items": schedule_grants.list()}

    @router.delete("/api/task-grants/{gid}", dependencies=[Depends(require_auth)])
    async def revoke_task_grant(gid: int) -> dict[str, bool]:
        if not schedule_grants.revoke(gid):
            raise HTTPException(404, "no such permission")
        return {"ok": True}

    # ---- the owner's advance cover for payments (pay_preapprovals.py, docs/payments-v2.md P3)
    # GET /api/permissions/preapprovals {"enabled", "items": [{"id", "owner_kind": task|schedule, "owner_id", "owner_name" (the schedule's
    #     name, "" for a task), "intent", "items": [{"description", "qty"}], "requirements": [str], "shipping", "merchant_policy",
    #     "delegated_choices": [str], "max_total" (number), "currency", "expires_at" (unix), "state": "active", "created_at"}]}
    #     (active ones only: one order each)             DELETE /api/permissions/preapprovals/{id} {"ok"} (404 = gone)
    # GET /api/permissions/pay-allows {"enabled", "items": [{"id", "schedule_id", "schedule_name", "merchant_origin", "payment_label",
    #     "currency", "created_at"}]}: what an Allow on a scheduled task's payment card also covers (same shop, same payment method)
    #                                                    DELETE /api/permissions/pay-allows/{id} {"ok"} (404 = gone)
    # GET/PUT /api/permissions/payment-cover {"enabled": bool}: the settings switch; off = nothing is covered in advance, and the agent's
    #     order_preapprove refuses (rows stay listed and removable)
    @router.get("/api/permissions/preapprovals", dependencies=[Depends(require_auth)])
    async def list_preapprovals() -> dict[str, Any]:
        return {"enabled": preapprovals.enabled(), "items": preapprovals.public_specs()}

    @router.delete("/api/permissions/preapprovals/{pid}", dependencies=[Depends(require_auth)])
    async def revoke_preapproval(pid: str) -> dict[str, bool]:
        if not preapprovals.revoke(pid):
            raise HTTPException(404, "no such pre-approval")
        return {"ok": True}

    @router.get("/api/permissions/pay-allows", dependencies=[Depends(require_auth)])
    async def list_pay_allows() -> dict[str, Any]:
        return {"enabled": preapprovals.enabled(),
                "items": [{"id": a["id"], "schedule_id": a["schedule_id"], "schedule_name": a["schedule_name"],
                           "merchant_origin": a["merchant_origin"], "payment_label": a["payment_label"], "currency": a["currency"],
                           "created_at": int(a["created_at"])} for a in preapprovals.allows()]}

    @router.delete("/api/permissions/pay-allows/{aid}", dependencies=[Depends(require_auth)])
    async def remove_pay_allow(aid: str) -> dict[str, bool]:
        if not preapprovals.allow_remove(aid):
            raise HTTPException(404, "no such payment allow")
        return {"ok": True}

    @router.get("/api/permissions/payment-cover", dependencies=[Depends(require_auth)])
    async def payment_cover() -> dict[str, bool]:
        return {"enabled": preapprovals.enabled()}

    @router.put("/api/permissions/payment-cover", dependencies=[Depends(require_auth)])
    async def set_payment_cover(body: dict[str, Any]) -> dict[str, bool]:
        if not isinstance(body.get("enabled"), bool):
            raise HTTPException(422, "enabled must be true or false")
        preapprovals.set_enabled(body["enabled"])
        return {"enabled": preapprovals.enabled()}

    return router
