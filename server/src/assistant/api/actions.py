"""What Kelo did outside (done_once.py): the steps that sent, changed or deleted, for the "what Kelo did" screen."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..activity import host_of
from ..catalog import BY_ID
from ..i18n import t
from ..presets import KEYS
from .context import AppContext


def normalize_source(source: str | None) -> str:
    s = (source or "").strip()
    if s.startswith("schedule:") or s == "schedule":
        return "schedule"
    if s in ("chat", "api", "offer") or s.startswith("chat:"):
        return "chat"
    return "job"


def site_from_targets(targets: list[str] | tuple[str, ...]) -> str | None:
    for tgt in targets:
        s = str(tgt)
        if s.startswith("site:"):
            return host_of(s.removeprefix("site:")) or s.removeprefix("site:")
    return None


def connector_from_targets(targets: list[str] | tuple[str, ...], connector: str | None = None) -> str | None:
    if connector:
        return connector
    for tgt in targets:
        s = str(tgt)
        if s.startswith("connector:"):
            return s.removeprefix("connector:")
    return None


def connector_display_name(cid: str, lang: str | None = None) -> str:
    if not cid:
        return ""
    s = BY_ID.get(cid)
    if s is not None:
        return t(s.name, lang=lang) if s.regions else s.name
    p = KEYS.get(cid)
    if p is not None:
        return p.name
    return cid


def line_of(rec: Any, lang: str | None = None) -> str:
    kind = getattr(rec, "kind", None) or (rec.get("kind") if isinstance(rec, dict) else "")
    targets = getattr(rec, "targets", None) or (rec.get("targets") if isinstance(rec, dict) else [])
    connector = getattr(rec, "connector", None) or (rec.get("connector") if isinstance(rec, dict) else "")
    tool = getattr(rec, "tool", None) or (rec.get("tool") if isinstance(rec, dict) else "")

    cid = connector or ""
    if not cid:
        for tgt in targets:
            if str(tgt).startswith("connector:"):
                cid = str(tgt).removeprefix("connector:")
                break

    if kind == "send":
        who = next((str(tgt) for tgt in targets if "@" in str(tgt)), "")
        if who:
            return t("Sent a mail to {who}", lang=lang, who=who)
        site_tgt = next((str(tgt) for tgt in targets if str(tgt).startswith("site:")), "")
        if site_tgt:
            site = site_tgt.removeprefix("site:")
            return t("Sent a form on {site}", lang=lang, site=site)
        return t("Sent something", lang=lang)

    if kind == "change":
        is_calendar = any(str(tgt) == "calendar" or str(tgt).startswith("calendar") for tgt in targets) or str(tool).startswith(("gcal_", "phone_calendar"))
        if is_calendar:
            return t("Changed the calendar", lang=lang)
        return t("Changed something", lang=lang)

    if kind == "delete":
        if cid:
            cname = connector_display_name(cid, lang=lang)
            return t("Deleted in {connector}", lang=lang, connector=cname)
        return t("Deleted something", lang=lang)

    return t("Changed something", lang=lang)


def source_name_of(source: str, task_id: int | None, schedules: Any = None, activity: Any = None, lang: str | None = None) -> str:
    if source == "chat":
        return t("Chat", lang=lang)
    if source.startswith("schedule:"):
        raw_id = source.removeprefix("schedule:")
        try:
            sid = int(raw_id)
        except ValueError:
            sid = None
        s = schedules.get(sid) if sid is not None and schedules is not None else None
        if s is not None and s.get("name"):
            return str(s["name"])
        return t("a schedule that is gone", lang=lang)
    if task_id is not None and activity is not None:
        try:
            task = activity.task(task_id)
            if task and task.get("title"):
                return str(task["title"])
        except Exception:
            pass
    return source.capitalize() if source else ""


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    ledger = ctx.action_ledger
    try:
        schedules = ctx.schedules
    except AttributeError:
        schedules = None
    try:
        activity = ctx.activity
    except AttributeError:
        activity = None

    @router.get("/api/actions", dependencies=[Depends(require_auth)])
    async def actions(days: int = Query(7, ge=1, le=30)) -> dict[str, Any]:
        """The steps of the last `days` days (up to 30), newest first: {"items": [{"id", "ts", "run_id", "task_id", "turn_id",
        "source" ("chat", "schedule:<id>", ...), "source_name", "line", "tool", "kind" (send | change | delete), "connector"|null,
        "targets" (as they are: an address, a number, "calendar", "connector:<name>", "site:<domain>"), "state" (done | unknown | failed),
        "outside_id" (the message, order or entry id the answer showed, "" when none), "detail" (at most 200 characters about a failure
        or an unknown result, never a message body), "checked_at"|null (when action_result settled an unknown one)}]}. An "unknown" one
        is a step that may or may not have gone out; the agent checks it and marks it done or not done."""
        items = []
        for r in ledger.list(days):
            pub = r.public()
            pub["line"] = line_of(r)
            pub["source_name"] = source_name_of(r.source, r.task_id, schedules=schedules, activity=activity)
            items.append(pub)
        return {"items": items}

    @router.get("/api/did", dependencies=[Depends(require_auth)])
    async def did(
        days: int = Query(7, ge=1, le=30),
        before: float | None = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        """A single time-ordered timeline of what Kelo did: actions from the ledger (send, change, delete)
        merged with tasks from the task log (reads, browser visits, finished chat turns), newest first.
        De-duplicated when both describe the same step (same task_id and within 5 seconds)."""
        clock = getattr(ledger, "clock", time.time)
        now = clock()
        since = now - max(1, min(int(days), 30)) * 86400.0

        # 1. Action rows from the ledger (send / change / delete)
        action_rows: list[dict[str, Any]] = []
        action_times_by_task: dict[int, list[float]] = {}
        for r in ledger.list(days):
            r_ts = float(r.ts)
            if r_ts < since:
                continue
            targets = r.targets if hasattr(r, "targets") else ()
            site = site_from_targets(targets)
            cid = connector_from_targets(targets, getattr(r, "connector", None))
            src = normalize_source(getattr(r, "source", None))
            tid = r.task_id
            outcome = getattr(r, "state", None) or "done"
            row = {
                "at": r_ts,
                "kind": "action",
                "title": line_of(r),
                "detail": getattr(r, "detail", "") or "",
                "source": src,
                "site": site,
                "connector": cid,
                "task_id": tid,
                "outcome": outcome,
            }
            action_rows.append(row)
            if tid is not None:
                action_times_by_task.setdefault(tid, []).append(r_ts)

        # 2. Rows from the task log (reads, browser visits, finished chat turns)
        task_rows: list[dict[str, Any]] = []
        store = getattr(ledger, "store", None) or getattr(ctx, "store", None)
        if store is not None:
            try:
                # Finished tasks only (status NOT IN running, waiting_approval, waiting_model)
                t_rows = store._all(
                    "SELECT id, turn_id, title, status, result, answer, error, source, started_at, finished_at, worker_id "
                    "FROM tasks "
                    "WHERE COALESCE(finished_at, started_at) >= ? "
                    "AND status NOT IN ('running', 'waiting_approval', 'waiting_model')",
                    (since,),
                )
            except Exception:
                t_rows = []

            if t_rows:
                task_ids = [int(x["id"]) for x in t_rows]
                marks = ",".join("?" * len(task_ids))

                # Batch lookup approvals
                approvals_by_task: dict[int, list[dict[str, Any]]] = {}
                try:
                    for a in store._all(f"SELECT task_id, tool, site FROM approval_log WHERE task_id IN ({marks})", tuple(task_ids)):
                        approvals_by_task.setdefault(int(a["task_id"]), []).append(a)
                except Exception:
                    pass

                # Batch lookup workers
                worker_ids = [x["worker_id"] for x in t_rows if x.get("worker_id")]
                worker_map: dict[str, dict[str, Any]] = {}
                if worker_ids:
                    w_marks = ",".join("?" * len(worker_ids))
                    try:
                        for w in store._all(f"SELECT id, kind, url, last_url FROM workers WHERE id IN ({w_marks})", tuple(worker_ids)):
                            worker_map[w["id"]] = w
                    except Exception:
                        pass

                for t_row in t_rows:
                    tid = int(t_row["id"])
                    t_started = float(t_row["started_at"])
                    t_at = float(t_row["finished_at"] or t_row["started_at"])
                    norm_src = normalize_source(t_row.get("source"))

                    # De-duplication check: same task_id and within 5 s of an action step
                    if tid in action_times_by_task:
                        if any(abs(a_at - t_at) <= 5.0 or abs(a_at - t_started) <= 5.0 for a_at in action_times_by_task[tid]):
                            continue

                    apps = approvals_by_task.get(tid, [])
                    has_browser_approval = any(str(a.get("tool") or "").startswith("browser_") or bool(a.get("site")) for a in apps)
                    app_site = next((str(a["site"]) for a in apps if a.get("site")), None)

                    w = worker_map.get(t_row.get("worker_id")) if t_row.get("worker_id") else None
                    is_browser_worker = (w is not None and w.get("kind") == "browser")
                    worker_site = None
                    if w is not None:
                        url = w.get("last_url") or w.get("url") or ""
                        if url:
                            worker_site = host_of(url) or None

                    site = app_site or worker_site or None
                    is_browser = (
                        str(t_row.get("source") or "") in ("browser", "background")
                        or has_browser_approval
                        or is_browser_worker
                    )

                    if is_browser:
                        kind = "browser"
                    elif norm_src == "chat":
                        kind = "reply"
                        site = None
                    else:
                        kind = "task"
                        site = None

                    title = str(t_row.get("title") or "").strip() or t("Conversation")
                    detail = str(t_row.get("result") or "").strip() or str(t_row.get("error") or "").strip()
                    outcome = str(t_row.get("status") or "") or "done"

                    task_rows.append({
                        "at": t_at,
                        "kind": kind,
                        "title": title,
                        "detail": detail,
                        "source": norm_src,
                        "site": site,
                        "connector": None,
                        "task_id": tid,
                        "outcome": outcome,
                    })

        # 3. Merge and sort newest first
        merged = action_rows + task_rows
        merged.sort(key=lambda r: (r["at"], r.get("task_id") or 0), reverse=True)

        # 4. Paging
        if before is not None:
            merged = [r for r in merged if r["at"] < before]

        items = merged[:limit]
        next_before = items[-1]["at"] if len(merged) > limit else None
        return {"items": items, "next_before": next_before}

    return router
