"""What Kelo did outside (done_once.py): the steps that sent, changed or deleted, for the "what Kelo did" screen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..catalog import BY_ID
from ..i18n import t
from ..presets import KEYS
from .context import AppContext


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

    return router
