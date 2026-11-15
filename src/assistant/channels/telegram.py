"""The Telegram Bot API as a Channel (base.py). Every call is a POST of JSON to https://api.telegram.org/bot<token>/<method>
through `fetch` (netguard.fetch: only that host, only public addresses, no redirects), so tests give it a fake and nothing else
touches the network. The token is read at each call (it can be replaced while the channel runs) and is never put in a log line or
an exception message: errors are built from the method name only.

Kelo is the bot's only reader: receive() starts with deleteWebhook and then long-polls getUpdates (docs/messengers.md).
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Callable

from .. import netguard
from ..netguard import NetGuardError
from .base import (Button, Callback, Channel, ChannelAuthError, ChannelConflict, ChannelRetry, Incoming, IncomingFile, Membership)
from .markup import telegram_parts

HOST = "api.telegram.org"
TOKEN_OK = re.compile(r"^[0-9A-Za-z:_-]{10,200}$")
UNSUPPORTED = ("voice", "video_note", "sticker", "location", "contact", "video", "audio", "animation", "venue", "poll")
MAX_FILE = 21_000_000        # Telegram lets a bot download 20 MB


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "file"


class Telegram(Channel):
    id = "telegram"
    item = "telegram-bot"
    name = "Telegram"

    def __init__(self, token_fn: Callable[[], str], *, fetch: Callable[..., Any] = netguard.fetch) -> None:
        self._token = token_fn
        self._fetch = fetch
        self.bot_id, self.bot_name = "", ""      # set by me(): a group message is "for the bot" when it names or answers these

    # ---- one call
    def _base(self) -> str:
        token = self._token()
        if not TOKEN_OK.match(token or ""):
            raise ChannelAuthError("no usable bot token")
        return f"https://{HOST}/bot{token}"

    async def _call(self, method: str, body: dict[str, Any], *, timeout: float = 30) -> Any:
        url = f"{self._base()}/{method}"
        try:
            r = await self._fetch("POST", url, allowed_hosts=[HOST], headers={"Content-Type": "application/json"},
                                  content=json.dumps(body).encode(), timeout=timeout)
        except NetGuardError:
            raise ChannelRetry(5) from None
        try:
            data = json.loads(r.body)
        except ValueError:
            data = {}
        if r.status == 200 and data.get("ok"):
            return data.get("result")
        code = int(data.get("error_code") or r.status)
        if code in (401, 404):
            raise ChannelAuthError(f"Telegram refused the token ({method})")
        if code == 409:
            raise ChannelConflict(f"another reader for this bot ({method})")
        if code == 429:
            raise ChannelRetry(float((data.get("parameters") or {}).get("retry_after") or 5))
        if code >= 500:
            raise ChannelRetry(5)
        raise ValueError(str(data.get("description") or f"{method} failed ({code})"))

    # ---- Channel
    async def me(self) -> dict[str, str]:
        r = await self._call("getMe", {})
        self.bot_id, self.bot_name = str(r.get("id", "")), str(r.get("username", ""))
        return {"id": self.bot_id, "username": self.bot_name, "name": str(r.get("first_name", ""))}

    def link(self, bot: dict[str, str], code: str) -> str:
        return f"https://t.me/{bot.get('username', '')}?start={code}"

    async def receive(self) -> AsyncIterator[Incoming | Callback | Membership]:
        await self._call("deleteWebhook", {"drop_pending_updates": False})   # a bot has one reader; Kelo is it
        if not self.bot_id:
            await self.me()                  # the parser needs the bot's own id and name (mentions, replies)
        while True:
            updates = await self._call("getUpdates", {"offset": self.offset, "timeout": 50,
                                                      "allowed_updates": ["message", "callback_query", "my_chat_member"]}, timeout=60)
            for u in updates or []:
                item = self.parse(u)
                if item is not None:
                    yield item
                self.offset = max(self.offset, int(u.get("update_id", 0)) + 1)

    def parse(self, u: dict[str, Any]) -> Incoming | Callback | Membership | None:
        uid = int(u.get("update_id", 0))
        cq = u.get("callback_query")
        if cq:
            m = cq.get("message") or {}
            return Callback(chat_id=str((m.get("chat") or {}).get("id", "")), user_id=str((cq.get("from") or {}).get("id", "")),
                            message_id=str(m.get("message_id", "")), data=str(cq.get("data") or ""), callback_id=str(cq.get("id", "")), update_id=uid)
        mc = u.get("my_chat_member")
        if mc:
            return self._membership(mc, uid)
        m = u.get("message")
        if not m:
            return None
        sender = m.get("from") or {}
        mid = str(m.get("message_id", ""))
        inc = Incoming(chat_id=str((m.get("chat") or {}).get("id", "")), user_id=str(sender.get("id", "")), message_id=mid,
                       text=str(m.get("text") or m.get("caption") or ""), private=(m.get("chat") or {}).get("type") == "private",
                       name=str(sender.get("first_name") or sender.get("username") or ""), update_id=uid)
        if not inc.private:
            inc.chat_title = str((m.get("chat") or {}).get("title") or "")
            inc.mentioned = self._mentions_bot(m)
            inc.reply_to_bot = bool(self.bot_id) and str(((m.get("reply_to_message") or {}).get("from") or {}).get("id", "")) == self.bot_id
        if m.get("photo"):
            big = m["photo"][-1]
            inc.files.append(IncomingFile(str(big.get("file_id", "")), f"telegram-{mid}.jpg", int(big.get("file_size") or 0), "photo"))
        elif m.get("document"):
            d = m["document"]
            inc.files.append(IncomingFile(str(d.get("file_id", "")), f"telegram-{mid}-{_safe_name(str(d.get('file_name') or 'file'))}", int(d.get("file_size") or 0)))
        elif any(k in m for k in UNSUPPORTED):
            inc.unsupported = True
        return inc

    def _mentions_bot(self, m: dict[str, Any]) -> bool:
        """The message names the bot: an @username or text-mention entity, or a command addressed to it (/ask@bot ...)."""
        bot = self.bot_name.casefold()
        text = str(m.get("text") or m.get("caption") or "")
        raw = text.encode("utf-16-le")             # entity offsets count UTF-16 units
        for e in (m.get("entities") or m.get("caption_entities") or []):
            kind = e.get("type")
            if kind == "text_mention" and self.bot_id and str((e.get("user") or {}).get("id", "")) == self.bot_id:
                return True
            if kind == "mention" and bot:
                start, size = int(e.get("offset", 0)) * 2, int(e.get("length", 0)) * 2
                if raw[start:start + size].decode("utf-16-le", "ignore").casefold() == "@" + bot:
                    return True
        first = text.split(None, 1)[0].casefold() if text.strip() else ""
        return bool(bot) and first.startswith("/") and first.endswith("@" + bot)

    def _membership(self, mc: dict[str, Any], uid: int) -> Membership | None:
        """The bot joined or left a group (a my_chat_member update). A private chat (the owner blocking the bot) is not a group."""
        chat = mc.get("chat") or {}
        if chat.get("type") not in ("group", "supergroup"):
            return None
        new, old = mc.get("new_chat_member") or {}, mc.get("old_chat_member") or {}
        was, now = old.get("status") in ("member", "administrator"), new.get("status") in ("member", "administrator")
        if new.get("status") == "restricted":
            now = bool(new.get("is_member"))
        if was == now and old.get("status") != "restricted":
            return None                                   # promoted, demoted or muted: nothing changed about being there
        return Membership(str(chat.get("id", "")), str(chat.get("title") or ""), str((mc.get("from") or {}).get("id", "")), now, uid)

    async def send(self, chat_id: str, text: str, buttons: list[list[Button]] | None = None, reply_to: str = "") -> str:
        """`text` is Markdown; long text goes out as several messages, the buttons under the last. Returns the last message's id."""
        parts = telegram_parts(text, self.LIMIT) or [("", "")]
        last = ""
        for n, (html, plain) in enumerate(parts):
            body: dict[str, Any] = {"chat_id": chat_id, "text": html or "-", "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_to and n == 0 and reply_to.isdigit():
                body["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
            if buttons and n == len(parts) - 1:
                body["reply_markup"] = {"inline_keyboard": [[{"text": b.text, "callback_data": b.data} for b in row] for row in buttons]}
            try:
                r = await self._call("sendMessage", body)
            except ValueError as e:
                if not _markup_refused(e):
                    raise
                body.pop("parse_mode")
                body["text"] = plain or "-"
                r = await self._call("sendMessage", body)
            last = str((r or {}).get("message_id", ""))
        return last

    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        (html, plain), *_ = telegram_parts(text, self.LIMIT) or [("-", "-")]
        body: dict[str, Any] = {"chat_id": chat_id, "message_id": int(message_id), "text": html, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            try:
                await self._call("editMessageText", body)
            except ValueError as e:
                if not _markup_refused(e):
                    raise
                body.pop("parse_mode")
                body["text"] = plain
                await self._call("editMessageText", body)
        except ValueError as e:
            if "not modified" not in str(e):
                raise

    async def typing(self, chat_id: str) -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)

    async def react(self, chat_id: str, message_id: str, emoji: str) -> None:
        try:
            await self._call("setMessageReaction", {"chat_id": chat_id, "message_id": int(message_id), "reaction": [{"type": "emoji", "emoji": emoji}]}, timeout=10)
        except (ValueError, ChannelRetry):
            return None      # not every emoji is allowed there

    async def clear_buttons(self, chat_id: str, message_id: str) -> None:
        try:
            await self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": int(message_id), "reply_markup": {"inline_keyboard": []}}, timeout=10)
        except (ValueError, ChannelRetry):
            return None

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            await self._call("answerCallbackQuery", {"callback_query_id": callback_id, **({"text": text} if text else {})}, timeout=10)
        except (ValueError, ChannelRetry):
            return None

    async def download(self, f: IncomingFile) -> AsyncIterator[bytes]:
        info = await self._call("getFile", {"file_id": f.file_id})
        path = str((info or {}).get("file_path") or "")
        if not path or ".." in path or not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
            raise ValueError("Telegram gave no file")
        try:
            r = await self._fetch("GET", f"https://{HOST}/file/bot{self._token()}/{path}", allowed_hosts=[HOST], timeout=120, max_body=MAX_FILE)
        except NetGuardError:
            raise ChannelRetry(5) from None
        if r.status != 200:
            raise ValueError("Telegram would not send the file")
        for i in range(0, len(r.body), 65536):
            yield r.body[i:i + 65536]


def _markup_refused(e: Exception) -> bool:
    s = str(e).lower()
    return "parse entities" in s or "entities" in s or "too long" in s
