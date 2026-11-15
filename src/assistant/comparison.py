"""Comparison cards (backlog 55b): the agent puts its comparison of a few options under its message as one card — the options side
by side over the same rows, the recommended one marked, a note on what to check first, and per option the sentence the owner sends to
choose it. Kept per message in message_comparisons; GET /api/messages adds "comparison" to the message:
{"title", "basis", "rows": [str], "options": [{"name", "values": [str] (one per row), "says", "recommended": bool}], "note",
"open": bool (false once the owner wrote after it: the app shows the card without its buttons)}."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS message_comparisons (
    message_id INTEGER PRIMARY KEY, card TEXT NOT NULL, created_at REAL NOT NULL);
"""
MAX_OPTIONS, MIN_OPTIONS, MAX_ROWS = 4, 2, 6
TITLE_MAX, BASIS_MAX, ROW_MAX, VALUE_MAX, NAME_MAX, SAYS_MAX, NOTE_MAX = 80, 120, 30, 60, 40, 200, 240


class ComparisonError(ValueError):
    """Shown to the agent as it is."""


def _clip(v: Any, n: int) -> str:
    return " ".join(str(v or "").split())[:n] if isinstance(v, (str, int, float)) else ""


def clean(raw: dict[str, Any]) -> dict[str, Any]:
    """The card as stored: rows and options trimmed, one value per row per option, at most one recommended."""
    rows = [r for r in (_clip(x, ROW_MAX) for x in (raw.get("rows") or [])) if r][:MAX_ROWS]
    if not rows:
        raise ComparisonError("rows: 1-6 short labels (e.g. monthly bill, one-time gift, effective cost)")
    opts_in = [o for o in (raw.get("options") or []) if isinstance(o, dict)][:MAX_OPTIONS]
    if len(opts_in) < MIN_OPTIONS:
        raise ComparisonError("options: 2-4 options to compare")
    options, recommended = [], False
    for o in opts_in:
        name = _clip(o.get("name"), NAME_MAX)
        if not name:
            raise ComparisonError("every option needs a name")
        values = [_clip(v, VALUE_MAX) for v in (o.get("values") or [])][:len(rows)]
        values += [""] * (len(rows) - len(values))
        rec = bool(o.get("recommended")) and not recommended
        recommended = recommended or rec
        options.append({"name": name, "values": values, "says": _clip(o.get("says"), SAYS_MAX), "recommended": rec})
    return {"title": _clip(raw.get("title"), TITLE_MAX), "basis": _clip(raw.get("basis"), BASIS_MAX), "rows": rows,
            "options": options, "note": _clip(raw.get("note"), NOTE_MAX)}


class Comparisons:
    def __init__(self, store: Any, hidden_marks: tuple[str, ...] = (), clock: Callable[[], float] = time.time) -> None:
        self.store, self.hidden_marks, self.clock = store, hidden_marks, clock
        store._db.executescript(SCHEMA)

    def attach(self, message_id: int, card: dict[str, Any]) -> None:
        self.store._run("INSERT OR REPLACE INTO message_comparisons(message_id, card, created_at) VALUES(?,?,?)",
                        (message_id, json.dumps(card, ensure_ascii=False), self.clock()))

    def for_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """GET /api/messages rows with "comparison" on the agent messages that have one; "open" while no user message came after."""
        if not rows:
            return rows
        ids = [m["id"] for m in rows if m.get("role") == "assistant"]
        if not ids:
            return rows
        marks = ",".join("?" * len(ids))
        found = {r["message_id"]: r for r in self.store._all(f"SELECT * FROM message_comparisons WHERE message_id IN ({marks})", tuple(ids))}
        if not found:
            return rows
        last_user = next((r["id"] for r in self.store._all("SELECT id, content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 50")
                          if not r["content"].startswith(self.hidden_marks)), 0)
        out = []
        for m in rows:
            r = found.get(m["id"])
            if r:
                try:
                    m = {**m, "comparison": {**json.loads(r["card"]), "open": m["id"] > last_user}}
                except ValueError:
                    pass
            out.append(m)
        return out


def comparison_tools(pending: dict[str, dict[str, Any]], current_turn: Callable[[], Any], emit_ui: Callable[[dict[str, Any]], Any]) -> list[Any]:
    from .agent import Tool

    async def show(args: dict[str, Any]) -> str:
        turn = current_turn() or {}
        if not turn.get("turn_id"):
            return "Error: a comparison card can only go with a chat reply."
        try:
            card = clean(args)
        except ComparisonError as e:
            return f"Error: {e}"
        pending[str(turn["turn_id"])] = card
        await emit_ui({"type": "comparison"})
        return ("The card goes under the message you are writing. Write the comparison briefly in words too (the basis, your "
                "recommendation and why); do not repeat every number of the card.")

    return [Tool("compare_options",
                 "Show 2-4 options side by side as one card under your message when you compare choices (plans, products, offers). "
                 "Only facts from tools or the user (never invented prices); rows are the same for every option; mark at most one "
                 "recommended; says: the whole sentence the user sends to choose that option, in their language.",
                 {"type": "object", "properties": {
                     "title": {"type": "string"}, "basis": {"type": "string", "description": "what the numbers assume, one line"},
                     "rows": {"type": "array", "items": {"type": "string"}, "description": "1-6 short row labels"},
                     "options": {"type": "array", "items": {"type": "object", "properties": {
                         "name": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}, "description": "one per row"},
                         "says": {"type": "string"}, "recommended": {"type": "boolean"}}, "required": ["name", "values"]}},
                     "note": {"type": "string", "description": "what to check before choosing, one line"}},
                  "required": ["rows", "options"]}, show)]
