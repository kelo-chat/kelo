"""What of a reply's text the conversation keeps (the live stream in the app is not touched: it shows every line as it comes).

A model often writes a short line right before a tool step ("I'll check the official timetable."), six times in a row on a long
errand: a note on what it is about to do, not the answer. A line that only led into a tool step is never stored as a reply's answer
(seen on main 2026-11-10: a stopped reply kept such a line as its final message; and 2026-11-15: a finished Sapporo reply kept six).

A reply that ends with "done": its text is split into segments at each tool step. Stored: the first segment (a short opener, or a
long early result: it is never dropped), every later segment that is not a lead-in, and the final segment always. A lead-in is a segment of at most
LEAD_IN_MAX characters, one paragraph (no blank line, no <message_break/> inside), that is followed by a tool step. Kept segments
are joined as they were written; where a lead-in was dropped between two of them and neither side has a line break or <message_break/> there, a blank
line goes in. A <message_break/> belongs to the segment it was written in (a dropped lead-in takes its own away).

A reply that ends any other way (stopped by the user, by the sweep, by a restart, or failed) keeps only the text written after its
last tool step of any kind: the answer it was writing when it ended.

Bookkeeping tools do not count as a tool step for the split (a line before a final `react` is part of the answer, not a lead-in):
BOOKKEEPING below; each is a tool whose chat label is hidden (tool_labels.py) and that only notes something down or attaches
buttons, never looks something up for the answer."""

from __future__ import annotations

import json
import re
from typing import Any

from .agent.fence import is_fenced

LEAD_IN_MAX = 160
BOOKKEEPING = frozenset({
    "react", "quick_replies", "compare_options", "find_tools", "offer_post", "set_tone", "finish_first_chat", "await_reply", "job_progress", "goal_log",
    "read_skill", "search_skills", "vault_status",
})
_BREAK = re.compile(r"<message_break\s*/?>")
_BREAK_END = re.compile(r"<message_break\s*/?>\s*$")
_PARAGRAPH = re.compile(r"\n\s*\n")


def is_lead_in(segment: str) -> bool:
    """A short single-paragraph line (the caller knows a tool step follows it)."""
    body = segment.strip()
    body = re.sub(r"^(?:\s*<message_break\s*/?>)+|(?:<message_break\s*/?>\s*)+$", "", body).strip()
    return bool(body) and len(body) <= LEAD_IN_MAX and not _PARAGRAPH.search(body) and not _BREAK.search(body)


def _what_came_back(item: dict[str, Any]) -> str:
    output = str(item.get("output") or "")
    if item.get("is_error") or output.startswith("Error"):
        return "error"
    if is_fenced(output):
        return "read"
    if re.search(r"\bdo not\b", output, re.I):   # an instruction to the model was said once, in its own turn: the steps keep the outcome only
        return "done"
    return (output.strip().splitlines() or [""])[0][:100] or "done"


class ReplyText:
    """Fed every event of a reply (the observe callback of run_turn); `text()` is what the conversation stores."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.cut = 0          # parts before this index were written before a tool step
        self.segments: list[str] = []   # text before each counted tool step (never empty ones)
        self.cur: list[str] = []
        self.done = False
        self.open: dict[str, str] = {}   # id -> "name arguments" of a tool step that has not returned yet (a stopped reply's unfinished step)
        self.names: dict[str, str] = {}  # id -> tool name, until its result comes
        self.steps: list[str] = []       # "name -> what came back" per step, one line for the model (steps_text); never outside text

    def feed(self, item: dict[str, Any]) -> None:
        kind = item.get("type")
        if kind == "text":
            self.parts.append(str(item.get("text") or ""))
            self.cur.append(str(item.get("text") or ""))
        elif kind == "tool_started":
            self.cut = len(self.parts)
            if item.get("name") not in BOOKKEEPING:
                self.names[str(item.get("id") or "")] = str(item.get("name") or "")
                args = item.get("arguments") or ""
                args = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str)
                self.open[str(item.get("id") or "")] = f"{item.get('name')} {args[:120]}".strip()
                seg = "".join(self.cur)
                if seg.strip():
                    self.segments.append(seg)
                self.cur = []
        elif kind == "tool_result":
            self.open.pop(str(item.get("id") or ""), None)
            name = self.names.pop(str(item.get("id") or ""), "")
            if name:
                self.steps.append(f"{name} -> {_what_came_back(item)}")
        elif kind == "done":
            self.done = True

    def steps_text(self) -> str:
        """The tool steps of this reply in one line ("watch_add -> Watcher #3 saved; email_list -> read"), stored with the message
        so the next turn's model knows what it did. Outside text is never copied: a fenced result is "read" only."""
        return "; ".join(self.steps)[:400]

    def unfinished(self) -> str:
        """The first tool step this reply started and never got a result for ("name arguments"), or "" (a stopped reply's request)."""
        return next(iter(self.open.values()), "")

    def text(self) -> str:
        if not self.done:
            return "".join(self.parts[self.cut:])
        tail = "".join(self.cur)
        segs = [*self.segments, tail] if tail.strip() else list(self.segments)   # no closing text: the last thing written is the answer
        out = ""
        dropped = False
        for i, seg in enumerate(segs):
            if 0 < i < len(segs) - 1 and is_lead_in(seg):   # the first and the final segment are always kept
                dropped = True
                continue
            if dropped and out and not out[-1:].isspace() and not seg[:1].isspace() and not _BREAK_END.search(out):
                out += "\n\n"
            dropped = False
            out += seg
        return out if segs else tail

    def clear(self) -> None:
        self.parts.clear()
        self.segments.clear()
        self.cur.clear()
        self.cut = 0

    def __bool__(self) -> bool:
        return bool(self.text())
