"""How Kelo said it will respond for the rest of a call (owner 2026-11-20: one turn after Kelo agreed to respond in a new way for the
rest of a call, it answered as before). The promise judge (scheduled_runs.PromiseJudge) hands back such words of a call reply
("in_conversation"); turn_engine.promise_round keeps them here per call, and every later turn of that call carries them in one note
(keep_note, added in server.prepare_talk) until the call is forgotten. In memory only: a call lasts minutes, and a restart loses only
this reminder (the words stay in the conversation). No word lists: the words are the reply's own."""

from __future__ import annotations

import re
from collections import OrderedDict

KEPT_MAX = 3      # commitments carried per call (the newest)
CALLS_MAX = 8     # calls remembered at once (the oldest goes first)
QUOTE_MAX = 160   # characters of one commitment


def keep_note(kept: list[str]) -> str:
    """The line a call turn's message gets while Kelo has said in this call how it will respond from now on ("" when it has not)."""
    if not kept:
        return ""
    said = "; ".join(f"\"{q}\"" for q in kept)
    return (f"[Not from the user: earlier in this call you told the user how you will respond from now on: {said} (where they "
            "differ, the newest counts). Respond to this message that way too, unless it changes or ends that.]")


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", " ", str(text or "").lower()).strip()


class CallBook:
    """call id -> the newest few things Kelo said it will keep doing in that call (its own words)."""

    def __init__(self) -> None:
        self._kept: OrderedDict[str, list[str]] = OrderedDict()

    def kept(self, call_id: str) -> list[str]:
        return list(self._kept.get(call_id, []))

    def keep(self, call_id: str, quote: str) -> None:
        quote = " ".join(str(quote or "").split())[:QUOTE_MAX]
        if not call_id or not _key(quote):
            return
        items = [q for q in self._kept.pop(call_id, []) if _key(q) != _key(quote)]
        self._kept[call_id] = [*items, quote][-KEPT_MAX:]
        while len(self._kept) > CALLS_MAX:
            self._kept.popitem(last=False)
