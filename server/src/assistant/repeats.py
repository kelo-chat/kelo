"""Keeping a reply from saying the same thing twice.

Some models write their message, call a tool (quick_replies, await_reply, ...), then write the very same message again as their final
answer. Both runs of text belong to one stored chat message, so the user would read it twice. Two guards:

RepeatGuard   sits on a turn's event stream. After a tool call, text that repeats an earlier run of text of the same turn (whitespace
              and bubble breaks ignored; the same or nearly the same words, SIMILAR) is held back; once it stops matching it is let
              through unchanged; if the turn ends while it still matches, it is dropped. So the stream and what is saved keep one copy.
collapse_repeat
              the safety net for a whole stored message: "X X'" (X' the same as X or nearly) becomes "X".
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

_BREAK = re.compile(r"<message_break\s*/?>")
SIMILAR = 0.8      # this alike (0..1) counts as the same message said again
HEAD = 24          # characters of a message's opening that must come back for a repeat


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", _BREAK.sub(" ", text or "")).strip()


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def collapse_repeat(text: str) -> str:
    """The text with a repeat of itself at the end taken off, else unchanged. A repeat: the text's opening comes back at a later
    point and what follows from there is (nearly, SIMILAR) the same as what came before it, as when a model writes its message,
    calls a tool, and writes it again in slightly different words."""
    raw = text or ""
    head = norm(raw)[:HEAD]
    if len(norm(raw)) < 2 * HEAD:
        return text
    # where the opening comes back in the raw text (whitespace may differ, so look for its first words)
    probe = raw.lstrip()[:HEAD // 2]
    at = raw.find(probe, len(raw) - len(raw.lstrip()) + 1)
    while at > 0:
        first, second = norm(raw[:at]), norm(raw[at:])
        if first.startswith(head[:HEAD // 2]) and len(second) >= 0.6 * len(first) and similar(first, second) >= SIMILAR:
            return raw[:at].rstrip()
        at = raw.find(probe, at + 1)
    return text


class RepeatGuard:
    def __init__(self) -> None:
        self.segments: list[str] = []   # earlier runs of text of this turn (normalized), each ended by a tool call
        self.current: list[str] = []
        self.held: list[dict[str, Any]] = []
        self.holding = False
        self.dropped = ""

    def _close_run(self) -> None:
        run = norm("".join(self.current))
        if run:
            self.segments.append(run)
        self.current = []

    def _repeats(self) -> bool:
        """Still possibly a repeat: the held text matches the start of an earlier run exactly, or (from HEAD characters on) nearly."""
        buf = self._buf()
        for seg in self.segments:
            if seg.startswith(buf):
                return True
            if len(buf) < HEAD:
                if similar(buf, seg[:len(buf)]) >= SIMILAR - 0.1:
                    return True
            elif similar(buf, seg[:len(buf)]) >= SIMILAR:
                return True
        return False

    def _buf(self) -> str:
        """The held text, normalized, without a bubble break still being written at its end ("<message_br")."""
        return norm(re.sub(r"<[^>]*$", "", "".join(i.get("text", "") for i in self.held)))

    def _release(self) -> list[dict[str, Any]]:
        out, self.held, self.holding = self.held, [], False
        self.current += [i.get("text", "") for i in out]
        return out

    def feed(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        """The events to pass on now for `item` (none while a possible repeat is held)."""
        kind = item.get("type")
        if kind == "text":
            if self.holding:
                self.held.append(item)
                return [] if self._repeats() else self._release()
            self.current.append(str(item.get("text", "")))
            return [item]
        if kind == "tool_started":
            out = self._release() if self.held else []
            self._close_run()
            self.holding = bool(self.segments)
            return [*out, item]
        if kind in ("done", "error", "step_limit"):
            out = self._release() if self.held and not self._clear_repeat() else []
            self.end()
            return [*out, item]
        return [item]

    def _clear_repeat(self) -> bool:
        """At the end: the held text is dropped only when it surely repeats (exactly, or nearly and at least HEAD characters long)."""
        buf = self._buf()
        return not buf or any(seg.startswith(buf) or (len(buf) >= HEAD and similar(buf, seg[:len(buf)]) >= SIMILAR) for seg in self.segments)

    def end(self) -> str:
        """The turn is over: a held repeat is dropped. Returns what was dropped ("" when nothing)."""
        if self.held:
            self.dropped = "".join(i.get("text", "") for i in self.held)
            self.held = []
        self.holding = False
        return self.dropped
