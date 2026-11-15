"""Capture first, file later: the agent's `remember` tool appends a durable fact, a correction or a working lesson the moment it shows
up in the chat to memory/inbox.md, one dated line with the message it came from. The memory pass (memory_upkeep.py) files those lines
into user.md / MEMORY.md / people pages / AGENTS.md and only then takes them out of the inbox: a pass that fails or is cut off
loses nothing, and lines added while a pass runs stay for the next one.

Each turn the newest lines (INBOX_SHOWN) go into the turn's own note (not the cached system prompt), so a fresh fact works before it
is filed. The file keeps at most INBOX_MAX lines (the oldest go first when it is full).

Line format:  - YYYY-MM-DD HH:MM · m<message id> · <kind> · <text>
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .agent import Tool
from .home import INBOX_FILE as INBOX, Home

INBOX_MAX = 200
INBOX_SHOWN = 15
TEXT_MAX = 300
KINDS = ("fact", "preference", "correction", "commitment", "person", "lesson")
HEADER = "# inbox.md\n\nFresh notes waiting to be filed into the memory files (newest first).\n"
LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (m\d+|-) · ([a-z]+) · (.+)$")


class MemoryInbox:
    def __init__(self, home: Home, tz: Callable[[], str] = lambda: "UTC", clock: Callable[[], float] = time.time):
        self.home, self.tz, self.clock = home, tz, clock
        self.path = home.root / INBOX
        self._lock = threading.Lock()

    def lines(self) -> list[str]:
        """The inbox lines, newest first."""
        if not self.path.is_file() or self.path.is_symlink():
            return []
        return [ln for ln in self.path.read_text(encoding="utf-8", errors="replace").splitlines() if LINE.match(ln)]

    def _write(self, lines: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(HEADER + ("\n" + "\n".join(lines) + "\n" if lines else ""), encoding="utf-8")
        os.replace(tmp, self.path)
        if self.home.quota:
            self.home.quota.wrote()

    def add(self, text: str, kind: str = "fact", source: int | None = None) -> str:
        """Appends one line (newest first) and returns it. A line already waiting with the same text is not added twice."""
        from .memory_upkeep import scrub
        text = scrub(re.sub(r"\s+", " ", str(text or "")).strip())[0][:TEXT_MAX]
        if len(text) < 3:
            raise ValueError("the note is empty (or held only something secret, which is never kept)")
        kind = kind if kind in KINDS else "fact"
        try:
            zone = ZoneInfo(self.tz() or "UTC")
        except Exception:  # noqa: BLE001
            zone = ZoneInfo("UTC")
        when = datetime.fromtimestamp(self.clock(), zone).strftime("%Y-%m-%d %H:%M")
        line = f"- {when} · {f'm{source}' if source else '-'} · {kind} · {text}"
        with self._lock:
            lines = self.lines()
            if any(LINE.match(ln).group(4).strip().lower() == text.lower() for ln in lines):
                return next(ln for ln in lines if LINE.match(ln).group(4).strip().lower() == text.lower())
            self._write([line, *lines][:INBOX_MAX])
        return line

    def remove(self, filed: list[str]) -> int:
        """Takes exactly these lines out (the ones a pass filed); lines added since stay. Returns how many went."""
        if not filed:
            return 0
        gone = set(filed)
        with self._lock:
            lines = self.lines()
            keep = [ln for ln in lines if ln not in gone]
            if len(keep) != len(lines):
                self._write(keep)
            return len(lines) - len(keep)

    def restore(self, lines: list[str]) -> int:
        """Puts filed lines back (an undone pass): the ones not waiting already, in time order with the rest, newest first, within
        INBOX_MAX. Returns how many came back."""
        back = [ln for ln in lines if LINE.match(ln)]
        with self._lock:
            now = self.lines()
            new = [ln for ln in back if ln not in now]
            if new:
                merged = sorted([*now, *new], key=lambda ln: LINE.match(ln).group(1), reverse=True)   # stable: equal times keep order
                self._write(merged[:INBOX_MAX])
            return len(new)

    def prompt_block(self) -> str:
        """The newest lines for the turn's note ("" when the inbox is empty)."""
        lines = self.lines()
        if not lines:
            return ""
        more = len(lines) - INBOX_SHOWN
        return ("### Just noted (not filed into your files yet; true until the user says otherwise)\n" + "\n".join(lines[:INBOX_SHOWN])
                + (f"\n[{more} older in {INBOX}]" if more > 0 else ""))


def inbox_tools(inbox: MemoryInbox, source_id: Callable[[], int | None] = lambda: None) -> list[Tool]:
    async def remember(args: dict[str, Any]) -> str:
        try:
            line = inbox.add(str(args.get("text") or ""), str(args.get("kind") or "fact"), source_id())
        except ValueError as e:
            return f"error: {e}"
        return f"noted: {line[2:]}"

    return [Tool(
        "remember",
        "Note something worth keeping the moment it comes up, as one short line in the user's words (filed into your notes later). "
        "Only what the user said themselves, never your reading of their words or of connected data; not passing task details; "
        "never secrets.",
        {"type": "object", "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)}}, "required": ["text"]},
        remember)]
