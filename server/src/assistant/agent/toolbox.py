"""Tools on demand: a run shows the model a small core set, then the tools activated in its conversation or job; every other tool
is one `find_tools` call away.

Why: tool definitions were three quarters of every first request. Most turns need a handful of tools, so the rest load when a task
needs them. It works the same for every provider (API or CLI sidecar): the loop simply sends a different tool list on the next step.

Rules (run_agent applies them, see loop.py):
- The core tools come first, in the order of the run's tool list, and never change, so the start of the request (system prompt, then
  tools) stays the same from turn to turn and a provider's prompt cache keeps working.
- Activated tools follow at the END, in the order they were activated. They stay for the rest of the conversation or job: the owner
  of the ToolBox saves the list (`on_change`) and hands it back to the next run (server.py keeps one list per scope: the chat, each
  schedule, each background job).
- A tool the model calls by name without loading it first still runs (and is loaded from then on): a model that knows a tool from
  an earlier run, a skill or the instructions is never refused for that.
- Tools already called in the messages a run starts with are loaded up front (a background job going on from its transcript).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from typing import Any

from .loop import Tool, current_box

# What nearly every turn may need, chosen from what the prompt names as first steps: answering and asking (quick_replies,
# await_reply), the agent's own files and skills, looking things up (web_search, web_fetch), the browser's basic steps and signing
# in (vault_status, request_login, browser_login), connecting a service, the payment check that must come before any final pay
# click (confirm_purchase: kept in the core so it can never be skipped for want of loading it) and the start of an order the user asked
# for (begin_order: the one-card order path), handing off long work (delegate),
# a background job's final report and progress line, the reaction on the user's message (react), the weather (so a weather question never opens a
# browser), and find_tools itself. Rare ones load with find_tools (the prompt diet, 2026-11-11): browser_click_at, browser_drag (points of
# a browser_look picture: canvas, maps, sliders), browser_view (desktop / phone view) and places_near; browser_look names the first two.
CORE_TOOLS: frozenset[str] = frozenset({
    "find_tools", "quick_replies", "await_reply",
    "read_file", "write_file", "list_files", "read_skill",
    "web_search", "web_fetch",
    "browser_open", "browser_read", "browser_click", "browser_type", "browser_login", "browser_look",
    "vault_status", "request_login", "connect_service", "add_service_key",
    "begin_order", "confirm_purchase", "delegate", "report", "job_progress", "remember", "react",
    "weather", "recheck_later",
    "action_result",   # shown only while a step's result is unknown (done_once.py: the gate hides it otherwise)
})
# loaded up front for every automatic run of a schedule (the run note tells it to pause a job that cannot work any more, or to try again later when the block is temporary)
SCHEDULE_RUN_TOOLS: tuple[str, ...] = ("schedule_pause", "schedule_retry")
ACTIVE_MAX = 30            # at most this many activated tools per scope; the oldest go first
ACTIVE_TTL = 45 * 86_400   # a saved activation not used for this long is dropped when the scope is loaded again
FIND_LIMIT = 8             # tools one search loads


class ToolBox:
    """The tools one run shows: `core` (always, in the run's own order) and `active` (activated, appended in order)."""

    def __init__(self, core: Iterable[str], active: Iterable[str] = (), *,
                 on_change: Callable[[list[str], set[str]], None] | None = None):
        self.core = frozenset(core)
        self.active: list[str] = [n for n in dict.fromkeys(active) if n and n not in self.core]
        self.used: set[str] = set()   # activated tools called in this run (their saved activation is renewed)
        self.on_change = on_change    # (activated names in order, names used in this run): saves the list
        self.blocked: Callable[[str], bool] | None = None   # a tool the owner switched off everywhere (permissions.py): never shown or loaded

    def shown(self, tools: list[Tool]) -> list[Tool]:
        by_name = {t.name: t for t in tools}
        out = [t for t in tools if t.name in self.core] + [by_name[n] for n in self.active if n in by_name]
        return [t for t in out if not (self.blocked is not None and self.blocked(t.name))]

    def is_shown(self, name: str) -> bool:
        return name in self.core or name in self.active

    def activate(self, names: Iterable[str], known: Iterable[str] | None = None) -> list[str]:
        """Load these tools (those in `known`, when given) at the end of the list. Returns the ones that were not loaded before."""
        ok = set(known) if known is not None else None
        added = [n for n in dict.fromkeys(names) if n and not self.is_shown(n) and (ok is None or n in ok)]
        if added:
            self.active.extend(added)
            if len(self.active) > ACTIVE_MAX:
                del self.active[:len(self.active) - ACTIVE_MAX]
            self.used.update(added)
            self._save()
        return added

    def mark_used(self, name: str) -> None:
        """An activated tool was called: its activation is renewed (kept past ACTIVE_TTL while it is in use)."""
        if name in self.active and name not in self.used:
            self.used.add(name)
            self._save()

    def _save(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(list(self.active), set(self.used))
            except Exception:  # noqa: BLE001 - saving the list never breaks the run
                pass


_WORD = re.compile(r"[^\w]+")
_STOP = {"a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "my", "me", "i", "with", "tool", "tools", "use", "using",
         "do", "can", "want", "please", "some", "any", "from", "by", "it", "is", "be", "that", "this", "at", "as"}


def _words(text: str) -> list[str]:
    return [w for w in _WORD.split((text or "").lower()) if len(w) > 1 and w not in _STOP]


def _stem(w: str) -> str:
    for end in ("ing", "es", "ed", "s"):
        if len(w) > len(end) + 3 and w.endswith(end):
            return w[: -len(end)]
    return w


def _haystack(t: Tool) -> tuple[set[str], str]:
    props = (t.parameters or {}).get("properties") or {}
    extra = " ".join(f"{k} {v.get('description', '')}" for k, v in props.items() if isinstance(v, dict))
    return set(t.name.lower().split("_")), f"{t.name.replace('_', ' ')} {t.description} {extra}".lower()


def search(tools: list[Tool], query: str, limit: int = FIND_LIMIT) -> list[Tool]:
    """The tools that best match the query's words: whole tool names first, then words of the name, then the description."""
    q = (query or "").strip().lower()
    words = _words(q)
    scored: list[tuple[int, int, Tool]] = []
    for i, t in enumerate(tools):
        if t.name == "find_tools":
            continue
        parts, hay = _haystack(t)
        score = 6 if len(words) > 1 and " ".join(words) in hay else 0   # the whole phrase is in the description ("phone app")
        if t.name.lower() in q.replace(" ", "_").split(",") or re.search(rf"(?<![\w]){re.escape(t.name.lower())}(?![\w])", q):
            score += 100
        for w in words:
            s = _stem(w)
            if w in parts or s in parts or any(p.startswith(s) for p in parts if len(s) >= 4):
                score += 4
            elif re.search(rf"\b{re.escape(s)}", hay):
                score += 1
        if score:
            scored.append((-score, i, t))
    return [t for _, _, t in sorted(scored, key=lambda x: (x[0], x[1]))[:limit]]


def summary(t: Tool) -> str:
    """A tool's first sentence: what a listing shows."""
    first = re.split(r"(?<=[.!?])\s", t.description.strip(), maxsplit=1)[0].rstrip(".")
    return first if len(first) <= 160 else first[:160].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def families(names: Iterable[str]) -> str:
    """Tool names grouped by their first word ("schedule_*", "phone_*") with single tools as they are: a short map of what exists."""
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(n.split("_", 1)[0], []).append(n)
    return ", ".join(f"{k}_*" if len(v) > 1 else v[0] for k, v in groups.items())


def find_tools_tool(loadable: Callable[[], list[str]] | None = None) -> Tool:
    """The one meta-tool: load tools by keywords or exact names for the rest of the conversation or job. `loadable`: the names of
    the tools that are not in the core, for the short map in the description (taken once, when the tool is made)."""
    names = loadable() if loadable else []
    where = f" Loadable: {families(names)}." if names else ""

    async def find_tools(args: dict[str, Any]) -> str:
        cur = current_box.get()
        if cur is None:
            return "All your tools are already in your list."
        box, tools = cur
        hidden = [t for t in tools if box is not None and not box.is_shown(t.name) and not (box.blocked is not None and box.blocked(t.name))]
        if box is None or not hidden:
            return "All your tools are already in your list."
        exact = args.get("names")
        wanted = [str(n).strip() for n in (exact if isinstance(exact, list) else str(exact or "").split(","))] if exact else []
        query = str(args.get("query") or "").strip()
        known = {t.name: t for t in tools}
        picked = [known[n] for n in wanted if n in known]
        if query:
            picked += [t for t in search(hidden, query) if t not in picked]
        if not picked:
            return (f"No tool matches {query or wanted!r}. Tools you can load: {', '.join(t.name for t in hidden)}. "
                    "Call find_tools again with names from this list, or answer with the tools you have.")
        added = set(box.activate([t.name for t in picked], known))
        lines = [f"- {t.name}: {summary(t)}" + ("" if t.name in added else " (already loaded)") for t in picked]
        return "Loaded for the rest of this conversation; call them now:\n" + "\n".join(lines)

    return Tool(
        "find_tools",
        "Load more tools by keywords (what you need to do) or exact names; call it before a tool you have not got." + where,
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "keywords in English"},
            "names": {"type": "array", "items": {"type": "string"}, "description": "exact tool names"}}},
        find_tools, read_only=True)


def tool_catalog(tools: list[Tool]) -> str:
    """One line per tool shown now, then the names that find_tools loads: what a model that went looking for its tool list is told."""
    cur = current_box.get()
    box = cur[0] if cur else None
    shown = box.shown(tools) if box else tools
    lines = [f"- {t.name}: {summary(t)}" for t in shown]
    out = "Tools you can call right now (call them by name, with the arguments they describe):\n" + "\n".join(lines)
    rest = [t.name for t in tools if box is not None and not box.is_shown(t.name)]
    if rest:
        out += "\nMore tools, loaded with find_tools: " + ", ".join(rest)
    return out


class ToolScopes:
    """The activated tools of each scope ("chat", "schedule:<id>", "job:<id>", ...) kept in the store's meta table, so they stay
    for the rest of the conversation or job and across restarts. `get_meta(key) -> str`, `set_meta(key, value)`."""

    PREFIX = "tools_active:"

    def __init__(self, get_meta: Callable[[str], str], set_meta: Callable[[str, str], None], clock: Callable[[], float] = time.time):
        self._get, self._set, self._clock = get_meta, set_meta, clock

    def _rows(self, scope: str) -> dict[str, float]:
        import json
        try:
            rows = json.loads(self._get(self.PREFIX + scope) or "[]")
        except ValueError:
            return {}
        out: dict[str, float] = {}
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, list) and len(r) == 2 and isinstance(r[0], str):
                try:
                    out[r[0]] = float(r[1] or 0)
                except (TypeError, ValueError):
                    continue
        return out

    def load(self, scope: str) -> list[str]:
        """The scope's activated tools in order, without those unused for ACTIVE_TTL."""
        now = self._clock()
        return [n for n, at in self._rows(scope).items() if now - at < ACTIVE_TTL]

    def save(self, scope: str, names: list[str], used: Iterable[str] = ()) -> None:
        """Keep `names` (in order) for the scope; the ones in `used`, and new ones, are stamped now, the rest keep their stamp."""
        import json
        now, old, fresh = self._clock(), self._rows(scope), set(used)
        self._set(self.PREFIX + scope, json.dumps([[n, now if n in fresh or n not in old else old[n]] for n in names[-ACTIVE_MAX:]]))

    def add(self, scope: str, names: Iterable[str]) -> None:
        """Load these tools in that scope too (e.g. a background job gets the tools of the conversation that started it)."""
        have = self.load(scope)
        new = [n for n in dict.fromkeys(names) if n not in have]
        if new:
            self.save(scope, have + new, new)

    def clear(self, scope: str) -> None:
        self._set(self.PREFIX + scope, "")

    def box(self, scope: str, core: Iterable[str], up_front: Iterable[str] = ()) -> ToolBox:
        """A ToolBox for one run of this scope: what the scope had, plus `up_front`; every activation is saved."""
        return ToolBox(core, [*self.load(scope), *up_front], on_change=lambda names, used: self.save(scope, names, used))
