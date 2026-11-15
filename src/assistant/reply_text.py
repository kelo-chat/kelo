"""What of a reply's text the conversation keeps.

A model often writes a short line right before a tool step ("Direct login URL gives an error; try the base."): a note on what it is
about to do, not the answer, and it does not always follow the app language. The chat shows it live, as it comes. A reply that ends
with "done" keeps all of its text, as before. A reply that ends any other way (stopped by the user, by the sweep, by a restart, or
failed) keeps only the text written after its last tool step: the answer it was writing when it ended. A line that only led into a
tool step is never stored as the reply's answer (seen on main 2026-11-10: a stopped reply kept such a line as its final message)."""

from __future__ import annotations

from typing import Any


class ReplyText:
    """Fed every event of a reply (the observe callback of run_turn); `text()` is what the conversation stores."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.cut = 0          # parts before this index were written before a tool step
        self.done = False

    def feed(self, item: dict[str, Any]) -> None:
        kind = item.get("type")
        if kind == "text":
            self.parts.append(str(item.get("text") or ""))
        elif kind == "tool_started":
            self.cut = len(self.parts)
        elif kind == "done":
            self.done = True

    def text(self) -> str:
        return "".join(self.parts if self.done else self.parts[self.cut:])

    def clear(self) -> None:
        self.parts.clear()
        self.cut = 0

    def __bool__(self) -> bool:
        return bool(self.text())
