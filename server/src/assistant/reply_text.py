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

from . import tool_labels
from .agent.fence import is_fenced
from .repeats import collapse_repeat

LEAD_IN_MAX = 160
STEPS_MAX = 400      # the steps line the next turn reads: whole steps only, a picture step always kept (see steps_text)
BOOKKEEPING = frozenset({
    "react", "quick_replies", "compare_options", "find_tools", "offer_post", "set_tone", "finish_first_chat", "await_reply", "job_progress", "goal_log",
    "read_skill", "search_skills", "vault_status",
})
_BREAK = re.compile(r"<message_break\s*/?>")
_BREAK_END = re.compile(r"<message_break\s*/?>\s*$")
_PARAGRAPH = re.compile(r"\n\s*\n")
ROW_JOIN = "<message_break/>"    # only used to run collapse_repeat over a whole reply; never stored between rows
_EDGE = re.compile(r"^(?:\s|<message_break\s*/?>)+|(?:\s|<message_break\s*/?>)+$")


def is_lead_in(segment: str) -> bool:
    """A short single-paragraph line (the caller knows a tool step follows it)."""
    body = segment.strip()
    body = re.sub(r"^(?:\s*<message_break\s*/?>)+|(?:<message_break\s*/?>\s*)+$", "", body).strip()
    return bool(body) and len(body) <= LEAD_IN_MAX and not _PARAGRAPH.search(body) and not _BREAK.search(body)


def _same(text: str) -> str:
    """A quote or a segment for comparison: its words, without spaces, the break marks and the final punctuation."""
    body = _BREAK.sub("", text or "")
    return re.sub(r"[\s.,!?;:。！？…]+$", "", "".join(body.split())).casefold()


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
        self.streamed: list[str] = []
        self.cut = 0          # parts before this index were written before a tool step
        self.segments: list[str] = []   # text before each counted tool step (never empty ones)
        self.cur: list[str] = []
        self.done = False
        self.open: dict[str, str] = {}   # id -> "name arguments" of a tool step that has not returned yet (a stopped reply's unfinished step)
        self.names: dict[str, str] = {}  # id -> tool name, until its result comes
        self.steps: list[str] = []       # "name -> what came back" per step, one line for the model (steps_text); never outside text
        self.args: dict[str, str] = {}   # id -> the whole argument text of a tool step, until its result comes (a picture step says how to call it again)
        self.kept: set[int] = set()      # indexes of steps that steps_text always keeps (a step that showed a picture)
        self.order: list[dict[str, str]] = []   # the reply in order: {"said": text written before a step} / {"did": the step's line} (timeline)
        self.gaps: list[bool] = []       # per segment: a step the apps show as a chip started after it, before the next text

    def feed(self, item: dict[str, Any]) -> None:
        kind = item.get("type")
        if kind == "text":
            self.streamed.append(str(item.get("text") or ""))
            self.cur.append(str(item.get("text") or ""))
        elif kind == "tool_started":
            self.cut = len(self.streamed)
            if item.get("name") not in BOOKKEEPING:
                self.names[str(item.get("id") or "")] = str(item.get("name") or "")
                args = item.get("arguments") or ""
                args = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str)
                self.open[str(item.get("id") or "")] = f"{item.get('name')} {args[:120]}".strip()
                self.args[str(item.get("id") or "")] = args
                seg = "".join(self.cur)
                if seg.strip():
                    self.segments.append(seg)
                    self.gaps.append(False)
                    self.order.append({"said": seg})
                self.cur = []
                if self.segments and not "".join(self.cur).strip() and not tool_labels.hidden(str(item.get("name") or "")):
                    self.gaps[-1] = True   # the apps show this step as a chip: the text after it is a new bubble
        elif kind == "tool_result":
            rid = str(item.get("id") or "")
            self.open.pop(rid, None)
            name = self.names.pop(rid, "")
            args = self.args.pop(rid, "")
            if name:
                pictures = 0 if item.get("is_error") else int(item.get("pictures") or 0)
                if pictures > 0:   # the picture reached the model of this reply only: the next turn is told how to look at it again
                    a = args if len(args) <= 200 else "with the same arguments"
                    self.kept.add(len(self.steps))
                    self.steps.append(f"{name} -> looked at {pictures} picture{'s' if pictures > 1 else ''}; not attached any more: "
                                      f"to see it again call {name} {a}")
                else:
                    self.steps.append(f"{name} -> {_what_came_back(item)}")
                self.order.append({"did": self.steps[-1]})
        elif kind == "done":
            self.done = True
            tail = "".join(self.cur)
            if tail.strip():
                self.order.append({"said": tail})

    def parts(self) -> list[str]:
        """The reply as the chat showed it: one part per bubble that a shown tool step started. Lead-ins are left out as in text(); a
        single part is exactly text()."""
        if not self.done:
            t = self.text()
            return [t] if t.strip() else []
        tail = "".join(self.cur)
        segs = [*self.segments, tail] if tail.strip() else list(self.segments)
        out, cur, dropped, last = [], "", False, -1
        for i, seg in enumerate(segs):
            if 0 < i < len(segs) - 1 and is_lead_in(seg):
                dropped = True
                continue
            if last >= 0 and any(self.gaps[last:i]):          # a chip between the last kept segment and this one
                out.append(cur)
                cur = ""
            elif dropped and cur and not cur[-1:].isspace() and not seg[:1].isspace() and not _BREAK_END.search(cur):
                cur += "\n\n"                                 # same joining rule as text()
            dropped = False
            cur += seg
            last = i
        out.append(cur)
        if len(out) == 1:
            return [self.text()] if self.text().strip() else []
        return [p for p in (_EDGE.sub("", p) for p in out) if p]

    def rows(self, split: bool = True) -> list[str]:
        """What the conversation stores: one message per part (parts()); `split` False for a call's reply. collapse_repeat runs over
        the whole reply; its cut, always a beginning of what it was given, is mapped back to the parts."""
        parts = self.parts() if split else []
        if len(parts) <= 1:
            whole = collapse_repeat(self.text())
            return [whole] if whole.strip() else []
        joined = ROW_JOIN.join(parts)
        kept = collapse_repeat(joined)
        if not joined.startswith(kept):    # collapse_repeat only ever cuts the end; anything else keeps every part
            return parts
        out, start = [], 0
        for p in parts:
            end = start + len(p)
            if len(kept) >= end:
                out.append(p)
            else:
                rest = _EDGE.sub("", kept[start:]) if len(kept) > start else ""
                if rest:
                    out.append(rest)
                break
            start = end + len(ROW_JOIN)
        return out

    def steps_text(self) -> str:
        """The tool steps of this reply in one line ("watch_add -> Watcher #3 saved; email_list -> read"), stored with the message
        so the next turn's model knows what it did. Outside text is never copied: a fenced result is "read" only. A step is never cut:
        steps are taken in order while the line stays within STEPS_MAX, a step that showed a picture is always taken, and the steps
        left out are counted at the end ("(+N more steps)")."""
        out: list[str] = []
        left = 0
        for i, step in enumerate(self.steps):
            if i in self.kept:
                out.append(step)
            elif left or len("; ".join([*out, step])) > STEPS_MAX:
                left += 1
            else:
                out.append(step)
        if left:
            out.append(f"(+{left} more steps)")
        return "; ".join(out)

    def timeline(self, limit: int = 1500) -> list[dict[str, str]]:
        """The end of the reply in order: what it said before each tool step and the step ("did"), as far as `limit` characters go (whole
        entries). Outside text is never copied: a step's line says "read" for a fenced result."""
        out: list[dict[str, str]] = []
        used = 0
        for entry in reversed(self.order):
            size = len(next(iter(entry.values())))
            if used + size > limit:
                break
            out.insert(0, entry)
            used += size
        return out

    def lead_in_done(self, quote: str) -> bool:
        """True when `quote` is a lead-in the reply wrote right before a tool step that then worked (no error): the words announced work
        the same reply did, so they are no promise. Only the text the reply keeps is compared (a lead-in is kept as the first segment)."""
        want = _same(quote)
        if not want:
            return False
        for i, entry in enumerate(self.order[:-1]):
            said = entry.get("said")
            if said is None or not is_lead_in(said) or _same(said) != want:
                continue
            did = self.order[i + 1].get("did")
            if did is not None and " -> error" not in did:
                return True
        return False

    def unfinished(self) -> str:
        """The first tool step this reply started and never got a result for ("name arguments"), or "" (a stopped reply's request)."""
        return next(iter(self.open.values()), "")

    def text(self) -> str:
        if not self.done:
            return "".join(self.streamed[self.cut:])
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
        self.streamed.clear()
        self.segments.clear()
        self.cur.clear()
        self.cut = 0
        self.gaps.clear()

    def __bool__(self) -> bool:
        return bool(self.text())
