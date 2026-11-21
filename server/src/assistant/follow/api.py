"""Follow API routes and agent tools."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..agent.loop import Tool
from ..i18n import t
from .items import UpcomingItem
from .runner import FollowRunner
from .store import FollowStore


def item_view(store: Any, it: dict[str, Any]) -> dict[str, Any]:
    """One follow item as the apps show it (GET /api/follow items; GET /api/carried uses the same shape for the items inside a work item)."""
    checks_out = []
    for ch in store.list_checks(item_key=it["key"]):
        last = store.last_reading(ch["id"], ok_only=True)
        checks_out.append({
            "kind": ch["kind"],
            "fields": ch["fields"],
            "from": ch["from_offset"],
            "until": ch["until_offset"],
            "state": ch["state"],
            "last": last["fields"] if last else None,
            "next_at": ch.get("next_at"),
            # a check waiting for the month (an allowance used up) keeps its reason in "note" and counts no errors
            "paused": ch["state"] == "active" and bool(ch.get("last_error")) and not ch.get("consecutive_errors"),
            "note": ch.get("last_error") or "",
        })
    preps_out = [{"what": p.get("what", ""), "when": p.get("when", ""), "state": p.get("state", "later")} for p in it.get("prep", [])]
    return {
        "key": it["key"],
        "kind": it["kind"],
        "title": it["title"],
        "starts_at": it["starts_at"],
        "all_day": bool((it.get("refs") or {}).get("all_day")),   # no time was given: the apps show the day only
        "checks": checks_out,
        "prep": preps_out,
        "plan": it.get("plan", {}),
        "state": it.get("state", "planned"),
    }


def follow_routes(app: Any, runner: FollowRunner, auth: Callable[..., Any]) -> None:
    """The FOLLOW-THROUGH API: GET /api/follow, POST /api/follow/{key}/stop, POST /api/follow/{key}/replan, DELETE /api/follow/{key}."""

    @app.get("/api/follow", dependencies=[Depends(auth)])
    async def get_follow_items() -> dict[str, Any]:
        return {"items": [item_view(runner.store, it) for it in runner.store.list_items()], "usage": runner.usage()}

    @app.post("/api/follow/{key}/stop", dependencies=[Depends(auth)])
    async def stop_follow_item(key: str) -> dict[str, Any]:
        it = runner.store.get_item(key)
        if it is None:
            raise HTTPException(404, f"no such follow item: {key}")
        runner.store.stop_item(key)
        return {"ok": True, "key": key, "state": "stopped"}

    @app.delete("/api/follow/{key}", dependencies=[Depends(auth)])
    async def remove_follow_item(key: str) -> dict[str, Any]:
        if not runner.store.remove_item(key):
            raise HTTPException(404, f"no such follow item: {key}")
        return {"ok": True, "key": key, "state": "removed"}

    @app.post("/api/follow/{key}/replan", dependencies=[Depends(auth)])
    async def replan_follow_item(key: str) -> dict[str, Any]:
        it = runner.store.get_item(key)
        if it is None:
            raise HTTPException(404, f"no such follow item: {key}")
        now = runner.clock()
        item = UpcomingItem(
            key=it["key"],
            kind=it["kind"],
            title=it["title"],
            starts_at=it["starts_at"],
            ends_at=it.get("ends_at"),
            place=it.get("place", ""),
            refs=it.get("refs", {}),
            source=it.get("source", ""),
        )
        plan = await runner.planner.plan(item, runner._get_notify_prefs_text())
        runner.store.delete_checks_for_item(key)
        if plan.is_empty():
            runner.store.save_item(item, plan.to_dict(), state="done")
            state = "done"
        else:
            runner.store.save_item(item, plan.to_dict(), state="watching", prep=plan.prep)
            state = "watching"
            for ch in plan.checks:
                runner.store.save_check(
                    item_key=item.key,
                    kind=ch["kind"],
                    refs=ch["refs"],
                    fields=ch["fields"],
                    from_offset=ch["from"],
                    until_offset=ch["until"],
                    window_start=ch["window_start"],
                    window_end=ch["window_end"],
                    every_minutes=ch["every_minutes"],
                    next_at=max(now, ch["window_start"]),
                    state="active",
                )
        return {"ok": True, "key": key, "state": state}


STATE_WORDS = {"later": "waiting until its window", "planned": "planned", "watching": "being watched", "done": "done",
               "stopped": "stopped"}
LIST_MAX = 40   # items the list tool prints (the soonest ones)
SOURCE_WORDS = {"calendar": "calendar", "mail": "mail", "order": "order", "goal": "goal", "chat": "chat"}


def item_when(it: dict[str, Any], tz: Any) -> str:
    """The item's day (and time, when it has one) in the owner's zone."""
    local = datetime.fromtimestamp(float(it.get("starts_at") or 0), tz)
    return local.strftime("%Y-%m-%d") if (it.get("refs") or {}).get("all_day") else local.strftime("%Y-%m-%d %H:%M")


def item_does(it: dict[str, Any], checks: list[dict[str, Any]]) -> str:
    """What Kelo does for the item, in a few words: its reminders, what it watches, what it prepares."""
    plan = it.get("plan") or {}
    parts = []
    lead = (plan.get("tell") or {}).get("lead") or []
    if lead:
        parts.append("reminds before it (" + ", ".join(str(x) for x in lead) + ")")
    for ch in checks:
        parts.append(f"watches {ch['kind']} ({ch['state']})")
    preps = it.get("prep") or plan.get("prep") or []
    if preps:
        parts.append(f"prepares {len(preps)} thing(s)")
    if (plan.get("tell") or {}).get("on_change") and checks:
        parts.append("tells the owner about changes")
    return "; ".join(parts) or "keeps it on the list only"


def follow_tools(runner: FollowRunner) -> list[Tool]:
    """Agent on-demand tools: follow_list, follow_stop, follow_remove."""

    async def follow_list(_args: dict[str, Any]) -> str:
        items = runner.store.list_items()
        if not items:
            return "Kelo keeps nothing for the owner right now."
        tz = runner.tz
        lines = [f"Kelo keeps {len(items)} item(s) for the owner (the owner's local time), soonest first:"]
        for it in items[:LIST_MAX]:
            checks = runner.store.list_checks(item_key=it["key"])
            state = STATE_WORDS.get(it.get("state", "planned"), it.get("state", "planned"))
            source = SOURCE_WORDS.get(str(it.get("source") or ""), str(it.get("source") or "unknown"))
            where = f", {it['place']}" if it.get("place") else ""
            links = getattr(runner, "links", None)   # the work item it is linked to (carried.py), named in the line
            work = links.work_of(it["key"]) if links is not None else None
            tie = f"; work item #{work}" if work is not None else ""
            lines.append(f"- {item_when(it, tz)} {it['title']} (from {source}{where}; {state}{tie}; key {it['key']})")
            refs = it.get("refs") or {}
            if refs.get("what"):
                lines.append(f"    what: {refs['what']}")
            if refs.get("quote"):
                lines.append(f"    the owner's words: {refs['quote']}")
            lines.append(f"    Kelo: {item_does(it, checks)}")
        if len(items) > LIST_MAX:
            lines.append(f"(+{len(items) - LIST_MAX} more not shown)")
        return "\n".join(lines)

    async def follow_stop(args: dict[str, Any]) -> str:
        key = str(args.get("key") or "").strip()
        if not key:
            return "Error: key is required to stop following."
        it = runner.store.get_item(key)
        if it is None:
            return f"No follow item found with key {key}."
        runner.store.stop_item(key)
        return f"Stopped watching {it['title']} ({key})."

    async def follow_remove(args: dict[str, Any]) -> str:
        key = str(args.get("key") or "").strip()
        if not key:
            return "Error: key is required to remove a follow item."
        it = runner.store.get_item(key)
        if it is None or not runner.store.remove_item(key):
            return f"No follow item found with key {key}."
        return f"Removed {it['title']} ({key}) from the list; Kelo will not add it again."

    return [
        Tool(
            "follow_list",
            "List what Kelo keeps for the owner (the Work screen's follow list): upcoming events, flights, bookings, parcels and dates "
            "taken from the chat. Each line gives the local day (and time when there is one), the title, where it came from, its state, "
            "what Kelo does for it and its key. Call it first when the owner asks about one of these items or about a date that may be on "
            "the list.",
            {"type": "object", "properties": {}},
            follow_list,
        ),
        Tool(
            "follow_stop",
            "Stop watching or checking an upcoming event, flight, or item by its key.",
            {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The item key to stop watching (e.g. 'cal:123')"},
                },
                "required": ["key"],
            },
            follow_stop,
        ),
        Tool(
            "follow_remove",
            "Take an item off the list for good (the owner's own wish to drop it, not just to pause it): Kelo stops it and does not add "
            "it again. Use follow_stop to pause instead.",
            {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The item key to remove from the list (e.g. 'cal:123')"},
                },
                "required": ["key"],
            },
            follow_remove,
        ),
    ]
