"""Custom skills (custom_skills.py): the skill list, proposals and drafts the owner approves, "save as skill" from a task or job."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from fastapi import Depends, HTTPException
from ..custom_skills import ProposalError, SkillProposals, draft_from_run
from fastapi import APIRouter
from .context import AppContext

log = logging.getLogger("uvicorn.error")


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    job_stream = ctx.job_stream
    journal_model = ctx.journal_model
    key_args = ctx.key_args
    packages = ctx.packages
    require_auth = ctx.require_auth
    skill_proposals = ctx.skill_proposals
    store = ctx.store
    turns = ctx.turns
    workers = ctx.workers

    # ---- custom skills (custom_skills.py): proposals and drafts the owner approves -------------------------------------------------
    def need_proposals() -> SkillProposals:
        if skill_proposals is None:
            raise HTTPException(404, "custom skills are not enabled on this server")
        return skill_proposals

    @router.get("/api/skills", dependencies=[Depends(require_auth)])
    async def get_skills() -> dict[str, Any]:
        if skill_proposals is not None:
            return {"items": skill_proposals.skills()}
        return {"items": [{"id": p.id, "name": p.name, "description": p.description, "source": p.source, "state": "ready",
                           "author": p.author} for p in packages.skills()]}

    @router.get("/api/skills/proposals", dependencies=[Depends(require_auth)])
    async def get_skill_proposals() -> dict[str, Any]:
        return need_proposals().public()

    @router.post("/api/skills/proposals/{entry_id}/approve", dependencies=[Depends(require_auth)])
    async def approve_skill_proposal(entry_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        expected = (body or {}).get("expected_hash")
        try:
            return need_proposals().approve(entry_id, expected_hash=str(expected) if expected else None)
        except KeyError:
            raise HTTPException(404, "no such proposal") from None
        except ProposalError as e:
            raise HTTPException(409, str(e)) from None

    @router.post("/api/skills/proposals/{entry_id}/discard", dependencies=[Depends(require_auth)])
    async def discard_skill_proposal(entry_id: str) -> dict[str, bool]:
        if not need_proposals().discard(entry_id):
            raise HTTPException(404, "no such proposal")
        return {"ok": True}

    @router.get("/api/skills/custom/{skill_id}", dependencies=[Depends(require_auth)])
    async def get_custom_skill(skill_id: str) -> dict[str, Any]:
        got = packages.custom_detail(skill_id)
        if got is None:
            raise HTTPException(404, "no such custom skill")
        return got

    @router.delete("/api/skills/custom/{skill_id}", dependencies=[Depends(require_auth)])
    async def delete_custom_skill(skill_id: str) -> dict[str, bool]:
        need_proposals()
        if not packages.remove_custom(skill_id):
            raise HTTPException(404, "no such custom skill")
        return {"ok": True}

    skill_drafts: set[asyncio.Task[Any]] = set()

    def start_skill_draft(key: str, origin: dict[str, Any], title: str, messages: list[dict[str, Any]], answer: str,
                          task_id: int | None) -> dict[str, Any]:
        props = need_proposals()
        if key in props.drafting:
            raise HTTPException(409, "a draft from this run is being written")
        chosen = journal_model()
        if not chosen:
            raise HTTPException(503, "choose a model first")
        props.drafting[key] = {k: v for k, v in origin.items() if k in ("task_id", "worker_id")}

        async def run() -> None:
            try:
                await draft_from_run(props, job_stream("other"), chosen, key_args(chosen[0]), title=title, messages=messages,
                                     answer=answer, origin=origin)
            except Exception as e:  # noqa: BLE001 - a failed draft is kept for the app to show, never raised
                log.info("skill draft from %s failed: %s", key, e)
                props.draft_failed(origin, str(e) if isinstance(e, ProposalError) else "")
            finally:
                props.drafting.pop(key, None)

        t = asyncio.create_task(run())
        skill_drafts.add(t)
        t.add_done_callback(skill_drafts.discard)
        return {"status": "drafting"}

    def turn_steps(task_id: int) -> list[dict[str, Any]]:
        """A finished chat task's tool steps, while its turn is still kept in memory, in the shape of agent messages."""
        t = next((t for t in turns.values() if t.task_id == task_id), None)
        out: list[dict[str, Any]] = []
        for ev in (t.events if t is not None else []):
            if ev.get("type") == "tool_started":
                out.append({"role": "assistant", "tool_calls": [{"function": {"name": ev.get("name"), "arguments": ev.get("arguments")}}]})
            elif ev.get("type") == "tool_result":
                out.append({"role": "tool", "content": str(ev.get("output") or "")})
        return out

    @router.post("/api/tasks/{task_id}/skill-draft", dependencies=[Depends(require_auth)], status_code=202)
    async def draft_skill_from_task(task_id: int) -> dict[str, Any]:
        need_proposals()
        task = activity.task(task_id, full=True)
        if task is None:
            raise HTTPException(404, "no such task")
        if task["status"] != "done":
            raise HTTPException(409, "only a finished task can become a skill")
        if task.get("worker_id"):
            w = workers.get(task["worker_id"])
            msgs = workers.transcript(task["worker_id"]) if w else []
        else:
            asked = store._all("SELECT role, content FROM messages WHERE role = 'user' AND created_at BETWEEN ? AND ? ORDER BY id LIMIT 1",
                               (task["started_at"] - 5, (task["finished_at"] or task["started_at"]) + 5))
            msgs = [*({"role": "user", "content": r["content"]} for r in asked), *turn_steps(task_id)]
        return start_skill_draft(f"task:{task_id}", {"type": "draft", "task_id": task_id}, task["title"], msgs, task.get("answer") or "",
                                 task_id)

    @router.post("/api/workers/{worker_id}/skill-draft", dependencies=[Depends(require_auth)], status_code=202)
    async def draft_skill_from_job(worker_id: str) -> dict[str, Any]:
        need_proposals()
        w = workers.get(worker_id)
        if w is None:
            raise HTTPException(404, "no such job")
        if w["status"] != "done":
            raise HTTPException(409, "only a finished job can become a skill")
        report = w.get("report") or {}
        answer = str(report.get("summary") or "") if isinstance(report, dict) else ""
        msgs = [{"role": "user", "content": str(w.get("task") or "")}, *workers.transcript(worker_id)]
        return start_skill_draft(f"worker:{worker_id}", {"type": "draft", "worker_id": worker_id, "task_id": w.get("task_id")},
                                 w["title"], msgs, answer, w.get("task_id"))

    return router
