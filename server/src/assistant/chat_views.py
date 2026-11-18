"""The browser tabs and the phone the chat shows: a "view" per tab or phone, kept on screen until the main agent or the owner closes it.

Owner rule (2026-11-17): a browser or the phone shown in the chat is hidden only by Kelo's main agent (a job reported done, the owner
asked to close it) or by the owner. A view the owner asked to open stays until the owner closes it. Nothing else removes it, and a
view untouched for VIEW_IDLE_SECONDS closes by itself. Jobs never decide: the main-only tool `chat_view` does.

In memory only: a server restart closes every browser tab anyway, and the phone's view is lost on restart (not stored)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from .agent import Tool, emit_ui

VIEW_IDLE_SECONDS = 600.0   # a view nobody touched this long is closed (owner rule 2026-11-17)
CLOSE_REASONS = ("done", "owner", "idle", "gone", "cleared")
PHONE_KEY = "android"
VIEW_KINDS = ("browser", "phone")


@dataclass
class ChatView:
    key: str                       # the BrowserTab id ("owner", a device id, "task:..."), or "android" for the phone
    kind: str                      # "browser" | "phone"
    owner_asked: bool = False      # the owner asked to see it: only the owner's close (or idle) ends it
    worker_id: str | None = None   # the background job that works in it, if any
    turn_id: str = ""              # the reply that showed its newest card (its message anchors the card after the reply)
    anchor_id: int = 0             # newest stored message id when its newest card came (the app puts the card after that message)
    title: str = ""
    url: str = ""
    host: str = ""
    status: str = ""
    image: str | None = None       # the newest card's picture (data URL, as the card)
    width: int = 0
    height: int = 0
    opened_at: float = 0.0
    touched_at: float = 0.0

    def as_json(self) -> dict[str, Any]:
        return {**asdict(self), "closes_at": self.touched_at + VIEW_IDLE_SECONDS}


class ChatViews:
    """The views of this server. `newest_id` is the id of the newest stored message; `clock` is time.time (tests pass their own)."""

    def __init__(self, newest_id: Callable[[], int], clock: Callable[[], float] = time.time):
        self.newest_id, self.clock = newest_id, clock
        self.version = 0   # +1 on every change that shows in the chat (open, card, close, anchor); the apps poll with it
        self._views: dict[str, ChatView] = {}

    def get(self, key: str) -> ChatView | None:
        return self._views.get(key)

    def items(self) -> list[ChatView]:
        """Oldest first."""
        return sorted(self._views.values(), key=lambda v: v.opened_at)

    def open(self, key: str, kind: str, *, owner_asked: bool = False, worker_id: str | None = None) -> ChatView:
        """Show `key` in the chat (idempotent). `owner_asked` only ever goes from False to True."""
        now = self.clock()
        view = self._views.get(key)
        if view is None:
            view = self._views[key] = ChatView(key, kind, anchor_id=self.newest_id(), opened_at=now, touched_at=now)
            self.version += 1
        if worker_id and view.worker_id != worker_id:
            view.worker_id = worker_id
            self.version += 1
        if owner_asked and not view.owner_asked:
            view.owner_asked = True
            self.version += 1
        return view

    def card(self, key: str, kind: str, item: dict[str, Any], *, turn_id: str = "", worker_id: str | None = None) -> ChatView:
        """A browser or phone card went out in a reply: the view is open and shows the card's words and picture."""
        view = self.open(key, kind, worker_id=worker_id)
        view.title, view.url = str(item.get("title") or view.title), str(item.get("url") or view.url)
        view.host, view.status = str(item.get("host") or view.host), str(item.get("status") or view.status)
        view.image = item.get("image") if item.get("image") is not None else view.image
        view.width, view.height = int(item.get("width") or 0), int(item.get("height") or 0)
        view.anchor_id, view.turn_id = self.newest_id(), turn_id or view.turn_id
        view.touched_at = self.clock()
        self.version += 1
        return view

    def anchor(self, turn_id: str, message_id: int) -> None:
        """The reply of `turn_id` was stored as message `message_id`: its cards go after that message."""
        for view in self._views.values():
            if view.turn_id == turn_id and view.anchor_id != message_id:
                view.anchor_id = message_id
                self.version += 1

    def touch(self, key: str, idle: float = 0.0) -> None:
        """The view was used now, or `idle` seconds ago (the tab's own idle time). A key without a view is ignored."""
        view = self._views.get(key)
        if view is not None:
            view.touched_at = max(view.touched_at, self.clock() - idle)

    def close(self, key: str, reason: str) -> ChatView | None:
        """The view is gone from the chat (the caller closes the tab with it). None when there was no view."""
        view = self._views.pop(key, None)
        if view is not None:
            self.version += 1
        return view

    def idle(self, busy: Callable[[str], bool]) -> list[str]:
        """Keys untouched for VIEW_IDLE_SECONDS that nothing works in or waits on (`busy`)."""
        now = self.clock()
        return [k for k, v in self._views.items() if now - v.touched_at >= VIEW_IDLE_SECONDS and not busy(k)]


def view_wanted(turn: Any, parent_source: str) -> bool:
    """Whether a card in this reply shows in the chat as a view: a chat reply, or a job that a chat asked for (not a schedule,
    a watcher or an unattended run)."""
    if turn.worker_id:
        return parent_source in ("chat", "api")
    return turn.source in ("chat", "api") and not turn.unattended


class ViewControl:
    """What the chat_view tool calls on the browser side (api/browser.py fills these in when its router is built; the tool
    looks them up per call). Defaults answer "nothing there"."""

    def __init__(self) -> None:
        self.resolve: Callable[[str], str | None] = lambda target: None
        self.tab_shown: Callable[[str], bool] = lambda key: False
        self.close_tab: Callable[[str], Awaitable[bool]] = _no
        self.can_open: Callable[[str], Awaitable[bool]] = _no
        self.refresh: Callable[[str], Awaitable[None]] = _nothing


async def _no(_key: str) -> bool:
    return False


async def _nothing(_key: str) -> None:
    return None


def chat_view_tool(views: ChatViews, ctl: ViewControl) -> Tool:
    """The main-only tool that shows or hides a browser tab or the phone in the chat (see ViewControl)."""

    async def handler(args: dict[str, Any]) -> str:
        action = str(args.get("action") or "")
        key = ctl.resolve(str(args.get("target") or "").strip())
        if key is None:
            return "Error: no such browser tab or phone. Use 'browser' (this chat's tab), a job id (w...), or 'phone'."
        if action == "open":
            if not await ctl.can_open(key):
                return "Error: nothing to show for that target: no browser tab is open for it and the phone is not set up."
            kind = "phone" if key == PHONE_KEY else "browser"
            asked = bool(args.get("owner_asked"))
            if kind == "browser" and not ctl.tab_shown(key):
                return "Error: that browser tab is not open."
            was = views.get(key)
            views.open(key, kind, owner_asked=asked)
            if kind == "browser" and (was is None or asked):
                await ctl.refresh(key)
            await emit_ui({"type": "view", "key": key, "state": "open", "owner_asked": views.get(key).owner_asked})
            return "Shown in the chat" + (" (the owner asked to see it: keep it until the owner asks to close it)." if asked else ".")
        if action == "close":
            reason = str(args.get("reason") or "done")
            if reason not in ("done", "owner"):
                return "Error: reason must be 'done' (the work is finished) or 'owner' (the owner asked to close it)."
            view = views.get(key)
            if view is None:
                return "Not shown in the chat, nothing to close."
            if view.owner_asked and reason == "done":
                return "Not closed: the owner asked to see this; close it only when the owner asks (reason='owner')."
            views.close(key, reason)
            await ctl.close_tab(key)
            await emit_ui({"type": "view", "key": key, "state": "closed", "reason": reason})
            return "Closed in the chat."
        return "Error: action must be 'open' or 'close'."

    return Tool(
        name="chat_view",
        description=(
            "Show or hide a browser tab or the phone in the chat. The browser or phone you or a job used stays shown until you close "
            "it here. action='open': show it; target 'browser' for this chat's tab, a job id (w...) for that job's tab, or 'phone'; "
            "set owner_asked=true when the owner asked to see it, then only the owner's request closes it. action='close': reason='done' "
            "when a job reported done or the work in it is finished and the owner does not need it any more; reason='owner' when the "
            "owner asked to close it. Leave it shown while the owner has to act in it (a login, a check, a choice). Untouched for 10 "
            "minutes it closes by itself."
        ),
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["open", "close"]},
            "target": {"type": "string", "description": "'browser', a job id (w...), or 'phone'"},
            "owner_asked": {"type": "boolean"},
            "reason": {"type": "string", "enum": ["done", "owner"]},
        }, "required": ["action", "target"]},
        handler=handler,
    )
