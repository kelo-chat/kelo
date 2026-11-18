"""The first conversation: Kelo gets to know the owner (their name, timezone, waking hours, whether it may write first).

State is two meta rows, shown to the apps as `first_chat_done` (a bool) in GET /api/profile:
- `first_chat_done`: "0" while the first conversation is open, "1" once it is over. A server that never had the row is decided once at
  start-up (`migrate`): an owner who already has something in user.md, or more than MIGRATE_MESSAGES messages, is done (they are not asked
  now); a new server stays open.
- `first_chat_owner_messages`: how many messages the owner has sent while it was open. At AUTO_FINISH_MESSAGES the conversation is over
  by itself, so the block below never stays in the prompt for ever.

While it is open the chat's "This Turn" carries `BLOCK`, and two tools are offered: `set_owner_settings` (the values Kelo asks about)
and `finish_first_chat`. The gate (permissions.Gate) hides both once it is over.
"""

from __future__ import annotations

from typing import Any

from . import home as homes
from .prompt import valid_timezone
from .proactive import HHMM

META_DONE, META_COUNT = "first_chat_done", "first_chat_owner_messages"
AUTO_FINISH_MESSAGES = 30       # the owner's messages after which it is over whatever was asked
MIGRATE_MESSAGES = 20           # a conversation longer than this: the owner is not a new one
TOOLS = ("set_owner_settings", "finish_first_chat")

BLOCK = (
    "This is the owner's first conversation with you. If their first message is a request, do that first. Early on, in your own words "
    "and briefly, find out: their name and what to call them; their timezone (the profile has a guess: confirm it); the hours they "
    "are usually awake; and whether they want you to write to them first (proactive messages), and about what. Skip what the "
    "profile or user.md already tells you. Ask one or two things at a time, never a form. Note what they tell you with `remember` (their own words), and set the profile values with "
    "set_owner_settings (name, timezone, waking hours, proactive on or off), but only what the owner said yes to. When you have "
    "what you need, or they would rather skip it, call finish_first_chat.")


class FirstChat:
    def __init__(self, store: Any, home: Any = None):
        self.store, self.home = store, home

    # ---- state
    def done(self) -> bool:
        return self.store.meta(META_DONE) == "1"

    @property
    def version(self) -> int:
        """1 while the conversation is open, else 0: when it flips, a running turn works out its tool list again."""
        return 0 if self.done() else 1

    def finish(self) -> None:
        self.store.set_meta(META_DONE, "1")

    def note_owner_message(self) -> None:
        """The owner sent a message: count it while the conversation is open; the 30th one closes it."""
        if self.done():
            return
        try:
            n = int(self.store.meta(META_COUNT) or 0) + 1
        except ValueError:
            n = 1
        if n >= AUTO_FINISH_MESSAGES:
            self.finish()
        else:
            self.store.set_meta(META_COUNT, str(n))

    def hides(self, tool_name: str) -> bool:
        return tool_name in TOOLS and self.done()

    def block(self) -> str:
        return "" if self.done() else BLOCK

    # ---- start-up
    def migrate(self) -> bool:
        """Decide once, for a server that has no row yet. Returns whether the conversation is done."""
        if self.store.meta(META_DONE):
            return self.done()
        known = self._has_user_notes() or self.store.message_count() > MIGRATE_MESSAGES
        self.store.set_meta(META_DONE, "1" if known else "0")
        return known

    def _has_user_notes(self) -> bool:
        if self.home is None:
            return False
        try:
            text = self.home.read("user.md")
        except Exception:  # noqa: BLE001 - a file that cannot be read says nothing about the owner
            return False
        return not homes.bare_template("user.md", text)


def settings_tool(store: Any, proactive: Any, first: FirstChat) -> Any:
    """set_owner_settings: what the first conversation learns, saved like the app's own settings (the profile and the proactive settings)."""
    from .agent import Tool

    async def set_owner_settings(a: dict[str, Any]) -> str:
        saved: list[str] = []
        name = str(a.get("user_name") or "").strip()
        if name:
            store.set_profile(user_name=name[:60])
            saved.append(f"name {name[:60]}")
        if a.get("timezone"):
            tz = valid_timezone(str(a["timezone"]).strip())
            if tz is None:
                return "Error: timezone must be an IANA name such as Asia/Seoul or Europe/Berlin."
            store.set_profile(timezone=tz)
            saved.append(f"timezone {tz}")
        changes: dict[str, Any] = {}
        for key in ("wake_start", "wake_end"):
            if a.get(key):
                if not HHMM.match(str(a[key]).strip()):
                    return f"Error: {key} must be a time like 07:00."
                changes[key] = str(a[key]).strip()
        if isinstance(a.get("proactive"), bool):
            changes["enabled"] = a["proactive"]
        if changes:
            try:
                proactive.update(**changes)
            except ValueError as e:
                return f"Error: {e}"
            saved += [f"{'proactive messages' if k == 'enabled' else k} {v}" for k, v in changes.items()]
        return ("Saved: " + ", ".join(saved) + ".") if saved else "Nothing to save: give at least one value."

    async def finish_first_chat(a: dict[str, Any]) -> str:
        first.finish()
        return "The first conversation is over; you will not be asked to run it again."

    return [
        Tool("set_owner_settings",
             "Save what the owner told you in the first conversation: the name to call them, their timezone, the hours they are awake and "
             "whether you may write to them first. Only values they agreed to; leave out the rest.",
             {"type": "object", "properties": {
                 "user_name": {"type": "string", "description": "what to call the owner"},
                 "timezone": {"type": "string", "description": "IANA name, e.g. Asia/Seoul"},
                 "wake_start": {"type": "string", "description": "HH:MM, when they usually wake"},
                 "wake_end": {"type": "string", "description": "HH:MM, when they usually go to bed"},
                 "proactive": {"type": "boolean", "description": "true: you may message them first; false: never"}}},
             set_owner_settings),
        Tool("finish_first_chat",
             "Call this once you know the owner well enough from the first conversation, or they want to skip it. It stops the extra "
             "first-conversation instructions and these two tools.",
             {"type": "object", "properties": {}}, finish_first_chat),
    ]
