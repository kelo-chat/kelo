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

import re
from typing import Any

LEAD_IN_MAX = 160
BOOKKEEPING = frozenset({
    "react", "quick_replies", "compare_options", "find_tools", "offer_post", "set_tone", "await_reply", "job_progress", "goal_log",
    "recheck_later", "recheck_result", "read_skill", "search_skills", "vault_status",
})
_BREAK = re.compile(r"<message_break\s*/?>")
_BREAK_END = re.compile(r"<message_break\s*/?>\s*$")
_PARAGRAPH = re.compile(r"\n\s*\n")


def is_lead_in(segment: str) -> bool:
    """A short single-paragraph line (the caller knows a tool step follows it)."""
    body = segment.strip()
    body = re.sub(r"^(?:\s*<message_break\s*/?>)+|(?:<message_break\s*/?>\s*)+$", "", body).strip()
    return bool(body) and len(body) <= LEAD_IN_MAX and not _PARAGRAPH.search(body) and not _BREAK.search(body)


class ReplyText:
    """Fed every event of a reply (the observe callback of run_turn); `text()` is what the conversation stores."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.cut = 0          # parts before this index were written before a tool step
        self.segments: list[str] = []   # text before each counted tool step (never empty ones)
        self.cur: list[str] = []
        self.done = False

    def feed(self, item: dict[str, Any]) -> None:
        kind = item.get("type")
        if kind == "text":
            self.parts.append(str(item.get("text") or ""))
            self.cur.append(str(item.get("text") or ""))
        elif kind == "tool_started":
            self.cut = len(self.parts)
            if item.get("name") not in BOOKKEEPING:
                seg = "".join(self.cur)
                if seg.strip():
                    self.segments.append(seg)
                self.cur = []
        elif kind == "done":
            self.done = True

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
