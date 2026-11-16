"""What every messenger channel (Telegram, Discord, Slack) has to offer the ChannelManager (manager.py).

A channel is a transport only: it reads what the owner sends, sends text with optional buttons, edits it, shows "typing" and
downloads files. Who may talk (pairing), the chat turn, approvals and the files are the manager's, so a new messenger is one class.

Offsets: `receive()` yields items that carry an `update_id`; the manager stores `update_id + 1` as the channel's offset after it
has handled the item, and gives it back to the channel (through the `offset` property) when the channel starts again.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


class ChannelAuthError(Exception):
    """The platform rejected the token (Telegram: 401 / 404): the channel stops until the owner connects it again."""


class ChannelRefused(ChannelAuthError):
    """The platform refuses the bot for a reason the owner can fix (Discord: a gateway intent that is off). Stops like an auth error;
    `notice` is the owner's line, already in their language."""

    def __init__(self, notice: str) -> None:
        super().__init__("the platform refused the bot")
        self.notice = notice


class ChannelConflict(Exception):
    """Another program reads the same bot (Telegram: 409, a webhook or another getUpdates reader)."""


class ChannelRetry(Exception):
    """A rate limit (429 retry_after), a server error or a network failure: wait `after` seconds and try again."""

    def __init__(self, after: float = 5.0) -> None:
        super().__init__("retry later")
        self.after = float(after)


@dataclass
class IncomingFile:
    file_id: str
    name: str              # a safe file name: "<platform>-<message_id>.<ext>"
    size: int
    kind: str = "file"     # "photo" | "file" (the text the message gets when it has none)


@dataclass
class Incoming:
    chat_id: str           # the platform's chat (Telegram: the private chat id as str)
    user_id: str           # the sender (Telegram: from.id as str)
    message_id: str        # the platform message (for reactions)
    text: str
    files: list[IncomingFile] = field(default_factory=list)   # photos / documents to download
    unsupported: bool = False     # voice, sticker, location ...: answered with a fixed line, nothing stored
    private: bool = True
    name: str = ""         # the sender's first name or user name (shown once, when pairing)
    update_id: int = 0
    # ---- groups (a chat with more than the owner: the manager answers only when called, manager.py _on_group)
    chat_title: str = ""   # the group's name
    mentioned: bool = False       # the message @mentions the bot
    reply_to_bot: bool = False    # it replies to one of the bot's messages
    group_id: str = ""     # what the allow-list knows the group by when that is not the chat (Discord: "guild:<id>"); "" = chat_id


@dataclass
class Membership:
    """The bot was added to (joined) or removed from (not joined) a group. `added_by`: the platform user id that did it ("" unknown)."""
    chat_id: str
    title: str
    added_by: str
    joined: bool
    update_id: int = 0


@dataclass
class Button:
    text: str
    data: str              # opaque callback data, at most 64 bytes (Telegram's limit)


@dataclass
class Callback:
    chat_id: str
    user_id: str
    message_id: str
    data: str
    callback_id: str
    update_id: int = 0


class Channel(ABC):
    id: str = ""           # "telegram"
    item: str = ""         # the vault item holding the token, "telegram-bot"
    name: str = ""         # shown to the owner: "Telegram"
    LIMIT: int = 4000      # most characters of one message after markup (Telegram allows 4096; keep a margin)
    FILE_LIMIT: int = 20_000_000    # the largest file the bot may download
    offset: int = 0        # set by the manager before receive() starts

    @abstractmethod
    async def me(self) -> dict[str, str]:
        """{"id", "username", "name"} of the bot (Telegram getMe)."""

    @abstractmethod
    def receive(self) -> AsyncIterator[Incoming | Callback | Membership]:
        """Long-running: yields what people send (and the bot's group changes) until cancelled. Raises the three errors above."""

    @abstractmethod
    async def send(self, chat_id: str, text: str, buttons: list[list[Button]] | None = None, reply_to: str = "") -> str:
        """Markdown in, the platform's markup out. `reply_to`: the message this one answers (shown as a reply, where the platform has that).
        Returns the new message's id."""

    @abstractmethod
    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        """Text only; drops the buttons."""

    @abstractmethod
    async def typing(self, chat_id: str) -> None: ...

    async def react(self, chat_id: str, message_id: str, emoji: str) -> None:   # optional
        return None

    async def clear_buttons(self, chat_id: str, message_id: str) -> None:   # optional: the text stays, the buttons go
        return None

    async def answer_callback(self, callback_id: str, text: str = "") -> None:   # optional: stops a button's spinner
        return None

    @abstractmethod
    def download(self, f: IncomingFile) -> AsyncIterator[bytes]: ...

    def memberships(self) -> list[Membership]:
        """The groups the bot is in as far as the platform has told it (Discord's servers); the manager reads them once the owner has
        paired, because a server seen before that was not yet known to be theirs. Telegram can only report a change, so [] there."""
        return []

    def link(self, bot: dict[str, str], code: str) -> str:
        """The link that opens the bot with the pairing code ("" when the platform has none)."""
        return ""

    @staticmethod
    def carries_chat(secret: str) -> bool:
        """The stored secret is enough to run this chat (Slack: the connector may hold an older reading-only token)."""
        return bool(secret)

