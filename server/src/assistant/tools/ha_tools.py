"""The agent's tools for Home Assistant live watches (ha_events.py)."""

from __future__ import annotations

from typing import Any

from ..agent import Tool
from ..ha_events import HaEvents, HaWatchError


def ha_watch_tools(ha: HaEvents) -> list[Tool]:
    def line(w: dict[str, Any]) -> str:
        cond = f"becomes '{w['to_state']}'" if w["to_state"] else "changes state"
        state = "on" if w["enabled"] else "off"
        return f"- #{w['id']} '{w['name']}': {w['entity_id']} {cond}; {state}{'; once' if w['once'] else ''}; fired {w['fire_count']}x"

    async def add(args: dict[str, Any]) -> str:
        try:
            w = ha.add(str(args.get("entity_id", "")), str(args.get("name", "")), to_state=str(args.get("to_state", "") or ""),
                       note=str(args.get("note", "") or ""), once=bool(args.get("once")),
                       cooldown=int(args.get("cooldown_seconds") or 60))
        except (HaWatchError, ValueError) as e:
            return f"error: {e}"
        return "Watching now (live, no polling):\n" + line(w)

    async def list_(args: dict[str, Any]) -> str:
        rows = ha.list()
        return "\n".join(line(w) for w in rows) if rows else "No Home Assistant watches."

    async def remove(args: dict[str, Any]) -> str:
        try:
            wid = int(str(args.get("id", "")).lstrip("#"))
        except ValueError:
            return "error: give the watch's id"
        return "Removed." if ha.remove(wid) else f"error: no Home Assistant watch #{wid}"

    return [
        Tool("ha_watch_add",
             "Watch a Home Assistant entity live and tell the user the moment it changes (\"tell me when the front door opens\", \"when "
             "the washer finishes\"). Find the entity_id first with connector_get GET /api/states on the Home Assistant connector. "
             "to_state: the state to wait for (e.g. 'on', 'open', 'off', 'idle'); leave empty for any change. note: what the user wants "
             "done when it happens. once: stop after the first hit.",
             {"type": "object", "properties": {
                 "entity_id": {"type": "string"}, "name": {"type": "string"}, "to_state": {"type": "string"},
                 "note": {"type": "string"}, "once": {"type": "boolean"}, "cooldown_seconds": {"type": "integer"}},
              "required": ["entity_id", "name"]}, add),
        Tool("ha_watch_list", "The live Home Assistant watches.", {"type": "object", "properties": {}}, list_, read_only=True),
        Tool("ha_watch_remove", "Stop a live Home Assistant watch by its id.",
             {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}, remove),
    ]
