"""The idle question about a button hold, and the saved holds that survive a restart (owner decision 2026-11-18).

`HoldAsker.tick` (every 15 s, scheduler) asks once per idle period, in the chat (an assistant message with two quick-reply buttons) and
with a push notification, whether the owner keeps controlling the screen or hands it back to Kelo. The answer is matched in
`prepare_talk` by its exact label (`answer`); any other text is ordinary chat. `HoldBook` keeps the button holds in the store
(meta `live_holds`, JSON), so a restarted server restores them and asks at once for a screen that is gone."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from .i18n import N_, t
from .tools.holds import Hold

META_HOLDS = "live_holds"

ASK_TITLE = N_("Still controlling it yourself?")
ASK_LINE = N_("You took over {where} and haven't touched it for 10 minutes. Hand it back to {agent}, or keep controlling it?")
ASK_GONE_LINE = N_("The screen you were controlling ({where}) closed because the browser or Kelo server restarted. Hand it back to {agent} "
                   "so it can carry on, or keep it paused?")
BACK_LABEL = N_("Hand back to {agent}")
KEEP_LABEL = N_("Keep controlling")
PHONE_WHERE = N_("the phone")

# Not from the user: the server's own words for the model, in the turn after an answer (never stored).
HOLD_BACK_NOTE = ("[Not from the user: they tapped the button that hands {what} back to you, answering Kelo's question; the server has "
                  "already handed it back. A background job that was waiting on it goes on by itself: do not do its work. If this chat "
                  "was in the middle of something there, read the screen again and go on; otherwise confirm in one short sentence.]")
HOLD_KEEP_NOTE = ("[Not from the user: they chose to keep controlling {what}; nothing changes and you still may not act on it. Answer in "
                  "one short sentence and stop.]")


def answer_note(answer: str, what: str) -> str:
    """The note the model gets with the owner's answer ("back" | "keep"), for the screen `what` ("the browser tab" / "the phone")."""
    return (HOLD_BACK_NOTE if answer == "back" else HOLD_KEEP_NOTE).format(what=what)


class HoldBook:
    """The button holds of the server, in the store: {key: {"device", "since", "ask_mid"?}}. Hand-over holds are never saved."""

    def __init__(self, store: Any):
        self.store = store

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.store.meta(META_HOLDS) or "{}")
        except (TypeError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("device")} if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.store.set_meta(META_HOLDS, json.dumps(data, ensure_ascii=False, sort_keys=True))

    def save(self, key: str, hold: Hold, *, since: float | None = None) -> None:
        data = self.load()
        prev = data.get(key, {})
        entry: dict[str, Any] = {"device": hold.device, "since": since if since is not None else prev.get("since", time.time())}
        if prev.get("ask_mid") is not None:
            entry["ask_mid"] = prev["ask_mid"]
        data[key] = entry
        self._write(data)

    def ask(self, key: str, mid: int | None) -> None:
        data = self.load()
        if key in data:
            if mid is None:
                data[key].pop("ask_mid", None)
            else:
                data[key]["ask_mid"] = mid
            self._write(data)

    def forget(self, key: str) -> None:
        data = self.load()
        if key in data:
            data.pop(key)
            self._write(data)


class HoldAsker:
    """Asks the owner, once per idle period, whether to keep a button hold or hand the screen back to Kelo."""

    def __init__(self, sources: Any, store: Any, activity: Any, goal_book: Any, *, agent_name: Callable[[], str],
                 clock: Callable[[], float] = time.monotonic, book: HoldBook | None = None):
        self.sources, self.store, self.activity, self.goals = sources, store, activity, goal_book
        self.agent_name, self.clock, self.book = agent_name, clock, book
        self.open: dict[str, tuple[int, Any]] = {}      # key -> (question message id, the source holding it)
        self.last_what = ""                              # the screen the last answer was about, for the model's note ("the phone" / "the browser tab")

    def _sources(self) -> list[Any]:
        return [s for s in (getattr(self.sources, "browser", None), getattr(self.sources, "device", None)) if s is not None]

    async def tick(self) -> None:
        for source in self._sources():
            holds = source.holds
            for key, hold in holds.items().items():
                if key not in self.open and holds.due_question(key):
                    self._ask(source, key, hold)

    def _ask(self, source: Any, key: str, hold: Hold) -> None:
        agent = self.agent_name() or "Kelo"
        where = source.where(key)
        line = t(ASK_GONE_LINE if hold.gone else ASK_LINE, where=where, agent=agent)
        mid = self.store.add_message("assistant", line)
        back, keep = t(BACK_LABEL, agent=agent), t(KEEP_LABEL)
        self.goals.attach_replies(mid, [{"question": "", "options": [back, keep]}], None)
        self.open[key] = (mid, source)
        source.holds.asked(key)
        if self.book is not None:
            self.book.ask(key, mid)
        self.activity.notify("hold_idle", t(ASK_TITLE), line[:400])

    def close(self, key: str) -> None:
        """The hold was touched or handed back: its open question goes (the buttons are removed, the message stays)."""
        got = self.open.pop(key, None)
        if got is None:
            return                       # nothing open for this hold: a touch costs no store write
        self.goals.detach_replies(got[0])
        if self.book is not None:
            self.book.ask(key, None)

    def restore(self) -> None:
        """After a restart: the questions saved with the holds are open again (their buttons are still in the store)."""
        if self.book is None:
            return
        for source in self._sources():
            for key, entry in self.book.load().items():
                if entry.get("ask_mid") is not None and key in source.holds.items():
                    self.open[key] = (int(entry["ask_mid"]), source)

    async def answer(self, after_user: int, text: str) -> str | None:
        """"back" (the owner handed the screen back, done now), "keep" (nothing changes), or None: the text is not the label of an open
        question that came after the owner's last message."""
        text = (text or "").strip()
        if not text:
            return None
        for key, (mid, source) in sorted(self.open.items(), key=lambda kv: -kv[1][0]):
            if mid <= after_user:
                continue
            options = self._options(mid)
            if len(options) != 2 or text not in options:
                continue
            self.last_what = "the phone" if getattr(source, "kind", "") == "device" else "the browser tab"
            if text == options[0]:
                await source.release(key)
                self.close(key)
                return "back"
            source.holds.keep(key)
            self.close(key)
            return "keep"
        return None

    def _options(self, mid: int) -> list[str]:
        for q in self.goals.questions_of(mid) or []:
            opts = q.get("options") or []
            return [str(o) for o in opts]
        return []
