"""Follow API routes and agent tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..agent.loop import Tool
from ..i18n import t
from .items import UpcomingItem
from .runner import FollowRunner
from .store import FollowStore


def follow_routes(app: Any, runner: FollowRunner, auth: Callable[..., Any]) -> None:
    """The FOLLOW-THROUGH API: GET /api/follow, POST /api/follow/{key}/stop, POST /api/follow/{key}/replan."""

    @app.get("/api/follow", dependencies=[Depends(auth)])
    async def get_follow_items() -> dict[str, Any]:
        items = runner.store.list_items()
        out = []
        for it in items:
            checks = runner.store.list_checks(item_key=it["key"])
            checks_out = []
            for ch in checks:
                last = runner.store.last_reading(ch["id"], ok_only=True)
                checks_out.append({
                    "kind": ch["kind"],
                    "fields": ch["fields"],
                    "from": ch["from_offset"],
                    "until": ch["until_offset"],
                    "state": ch["state"],
                    "last": last["fields"] if last else None,
                })
            preps_out = [
                {"what": p.get("what", ""), "when": p.get("when", ""), "state": p.get("state", "later")}
                for p in it.get("prep", [])
            ]
            out.append({
                "key": it["key"],
                "kind": it["kind"],
                "title": it["title"],
                "starts_at": it["starts_at"],
                "checks": checks_out,
                "prep": preps_out,
                "state": it.get("state", "planned"),
            })
        return {"items": out}

    @app.post("/api/follow/{key}/stop", dependencies=[Depends(auth)])
    async def stop_follow_item(key: str) -> dict[str, Any]:
        it = runner.store.get_item(key)
        if it is None:
            raise HTTPException(404, f"no such follow item: {key}")
        runner.store.stop_item(key)
        return {"ok": True, "key": key, "state": "stopped"}

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


def follow_tools(runner: FollowRunner) -> list[Tool]:
    """Agent on-demand tools: follow_list, follow_stop."""

    async def follow_list(_args: dict[str, Any]) -> str:
        items = runner.store.list_items()
        if not items:
            return "No items currently being followed."
        lines = []
        for it in items:
            state = it.get("state", "planned")
            lines.append(f"- [{state}] {it['title']} (starts: {it['starts_at']}, key: {it['key']})")
            checks = runner.store.list_checks(item_key=it["key"])
            for ch in checks:
                lines.append(f"    * watch {ch['kind']}: {', '.join(ch['fields'])} ({ch['state']})")
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

    return [
        Tool(
            "follow_list",
            "List upcoming events, flights, and items Kelo is following or watching.",
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
    ]
