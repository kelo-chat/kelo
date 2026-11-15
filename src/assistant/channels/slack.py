"""Slack as a Channel (base.py): the Web API with the bot token for everything the bot says and does, Socket Mode (a websocket opened
with the app-level token) for what people say to it.

Tokens: the Slack connector keeps one bundle {"mark": "slack-v1", "user", "bot", "app"} (keyconnect.py); this class reads "bot" and
"app" at each call and never puts a token in a log line or an exception message. Every HTTP call goes through `fetch` (netguard.fetch:
only Slack's hosts, public addresses, no redirects), so tests give it a fake.

Socket Mode (docs: api.slack.com/apis/socket-mode): apps.connections.open -> a wss URL -> "hello" -> envelopes. Each envelope is
acknowledged ({"envelope_id"}) BEFORE it is handled (Slack sends it again when no ack comes within 3 seconds). A "disconnect" frame
asks for a new connection. Slack has no update offsets: every item has update_id 0.

Groups: a Slack channel the bot was invited to is a group; member_joined_channel names the `inviter`, which the manager compares
with the pairing. Replies in a channel go into the thread of the message they answer; in a direct message they are plain messages.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlsplit

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from .. import netguard
from ..i18n import t
from ..netguard import NetGuardError
from .base import Button, Callback, Channel, ChannelAuthError, ChannelRefused, ChannelRetry, Incoming, IncomingFile, Membership
from .markup import chunks, slack_mrkdwn

API = "https://slack.com/api/"
HOSTS = ["slack.com"]
FILE_HOSTS = ["files.slack.com"]
SOCKET_SUFFIX = "slack.com"                 # the only hosts a Socket Mode URL may name
MARK = "slack-v1"
ID_OK = re.compile(r"^[A-Z0-9]{2,40}$")     # channel and user ids ("D0123", "C0456", "U0789")
TS_OK = re.compile(r"^[0-9]{6,12}\.[0-9]{1,9}$")
AUTH_ERRORS = {"invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired", "no_permission"}
MAX_FILE = 25_000_000
MAX_FRAME = 4 * 1024 * 1024
BUTTONS_PER_ROW, ROWS = 5, 5
REMEMBER = 300                              # how many sent and seen messages are kept for threads and button clearing
EMOJI = {"👀": "eyes", "👍": "+1", "✅": "white_check_mark", "❤️": "heart", "🙏": "pray", "😂": "joy", "🎉": "tada", "👌": "ok_hand",
         "🤔": "thinking_face", "⏳": "hourglass_flowing_sand", "❌": "x"}


def bundle(secret: str) -> dict[str, str] | None:
    """The three tokens of a Slack connection, or None for an older plain token (reading and posting only, no chat)."""
    if not (secret or "").startswith("{"):
        return None
    try:
        b = json.loads(secret)
    except ValueError:
        return None
    if not isinstance(b, dict) or b.get("mark") != MARK:
        return None
    return {k: str(b.get(k) or "") for k in ("user", "bot", "app")}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "file"


class _Recent(OrderedDict[str, str]):
    """A small map that forgets its oldest entries."""

    def put(self, key: str, value: str) -> None:
        self[key] = value
        self.move_to_end(key)
        while len(self) > REMEMBER:
            self.popitem(last=False)


class Slack(Channel):
    id = "slack"
    item = "slack-api"
    name = "Slack"
    LIMIT = 2900                              # a section block holds 3000 characters; keep a margin
    FILE_LIMIT = MAX_FILE

    def __init__(self, token_fn: Callable[[], str], *, fetch: Callable[..., Any] = netguard.fetch, connect: Callable[..., Any] | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self._secret = token_fn
        self._fetch = fetch
        self._connect = connect or websockets.connect
        self._sleep = sleep
        self.me_id, self.me_name = "", ""
        self.titles: dict[str, str] = {}      # channel id -> "#name"
        self._threads = _Recent()             # message ts -> the ts of its thread's first message (channels only)
        self._sent = _Recent()                # ts of a message sent with buttons -> its text (to take the buttons off later)

    @staticmethod
    def carries_chat(secret: str) -> bool:
        b = bundle(secret)
        return bool(b and b["bot"] and b["app"])

    # ---- Web API
    def _token(self, which: str) -> str:
        b = bundle(self._secret())
        tok = (b or {}).get(which, "")
        if not tok.startswith("xoxb-" if which == "bot" else "xapp-"):
            raise ChannelAuthError("no usable Slack token")
        return tok

    async def _call(self, method: str, body: dict[str, Any] | None = None, *, which: str = "bot", timeout: float = 30) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token(which)}", "Content-Type": "application/json; charset=utf-8"}
        try:
            r = await self._fetch("POST", API + method, allowed_hosts=HOSTS, headers=headers,
                                  content=json.dumps(body or {}).encode(), timeout=timeout)
        except NetGuardError:
            raise ChannelRetry(5) from None
        if r.status == 429:
            raise ChannelRetry(float((r.headers or {}).get("retry-after") or 5))
        if r.status >= 500:
            raise ChannelRetry(5)
        try:
            data = json.loads(r.body) if r.body else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if data.get("ok"):
            return data
        err = str(data.get("error") or f"HTTP {r.status}")
        if err in AUTH_ERRORS:
            raise ChannelAuthError(f"Slack refused the token ({method})")
        if err == "ratelimited":
            raise ChannelRetry(5)
        raise ValueError(f"{method} failed ({err})")

    # ---- Channel
    async def me(self) -> dict[str, str]:
        r = await self._call("auth.test")
        self.me_id, self.me_name = str(r.get("user_id", "")), str(r.get("user", ""))
        return {"id": self.me_id, "username": self.me_name, "name": self.me_name}

    async def receive(self) -> AsyncIterator[Incoming | Callback | Membership]:
        while True:
            url = await self._socket_url()
            try:
                async with self._connect(url, max_size=MAX_FRAME, open_timeout=20) as ws:
                    async for item in self._socket(ws):
                        yield item
            except ConnectionClosed:
                pass                            # Slack closes old connections now and then: open a new one
            except (OSError, asyncio.TimeoutError, WebSocketException):
                raise ChannelRetry(5) from None
            await self._sleep(1)

    async def _socket_url(self) -> str:
        try:
            r = await self._call("apps.connections.open", which="app")
        except ValueError as e:
            raise ChannelRefused(t("Slack refused Socket Mode: turn it on for the app and give the app-level token connections:write ({error}).",
                                   error=str(e)[:80])) from None
        url = str(r.get("url") or "")
        host = (urlsplit(url).hostname or "").lower()
        if urlsplit(url).scheme != "wss" or not (host == SOCKET_SUFFIX or host.endswith("." + SOCKET_SUFFIX)):
            raise ChannelRetry(30)              # not Slack's own socket: never connect
        return url

    async def _socket(self, ws: Any) -> AsyncIterator[Incoming | Callback | Membership]:
        async for raw in ws:
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            if kind == "disconnect":
                if frame.get("reason") == "link_disabled":
                    raise ChannelRefused(t("Slack refused Socket Mode: turn it on for the app and give the app-level token connections:write ({error}).",
                                           error="link_disabled"))
                return                          # refresh_requested / warning: a new connection
            if frame.get("envelope_id"):
                await ws.send(json.dumps({"envelope_id": frame["envelope_id"]}))
            payload = frame.get("payload") or {}
            if kind == "events_api":
                for item in await self._event((payload.get("event") or {})):
                    yield item
            elif kind == "interactive" and payload.get("type") == "block_actions":
                cb = self._action(payload)
                if cb is not None:
                    yield cb

    async def send(self, chat_id: str, text: str, buttons: list[list[Button]] | None = None, reply_to: str = "") -> str:
        """Markdown in, mrkdwn out; long text goes out as several messages, the buttons under the last. In a channel the answer goes
        into the thread of the message it answers."""
        if not ID_OK.match(chat_id):
            raise ValueError("not a Slack channel")
        parts = chunks(text, self.LIMIT) or ["-"]
        thread = ""
        if reply_to and TS_OK.match(reply_to) and not chat_id.startswith("D"):
            thread = self._threads.get(reply_to, reply_to)
        last = ""
        for n, part in enumerate(parts):
            md = slack_mrkdwn(part)
            body: dict[str, Any] = {"channel": chat_id, "text": md, "unfurl_links": False, "unfurl_media": False}
            if thread:
                body["thread_ts"] = thread
            with_buttons = bool(buttons) and n == len(parts) - 1
            if with_buttons:
                body["blocks"] = [{"type": "section", "text": {"type": "mrkdwn", "text": md}}, *self._rows(buttons or [])]
            r = await self._call("chat.postMessage", body)
            last = str(r.get("ts") or "")
            if last and thread:
                self._threads.put(last, thread)
            if last and with_buttons:
                self._sent.put(last, md)
        return last

    @staticmethod
    def _rows(buttons: list[list[Button]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        n = 0
        for row in buttons:
            for i in range(0, len(row), BUTTONS_PER_ROW):
                elements = []
                for b in row[i:i + BUTTONS_PER_ROW]:
                    n += 1
                    elements.append({"type": "button", "text": {"type": "plain_text", "text": b.text[:75]}, "value": b.data[:2000],
                                     "action_id": f"kelo-{n}", **({"style": "danger"} if b.data.endswith(":0") else {})})
                rows.append({"type": "actions", "elements": elements})
        return rows[:ROWS]

    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        if not (ID_OK.match(chat_id) and TS_OK.match(message_id)):
            return
        first = slack_mrkdwn((chunks(text, self.LIMIT) or ["-"])[0])
        await self._call("chat.update", {"channel": chat_id, "ts": message_id, "text": first, "blocks": []})
        self._sent.pop(message_id, None)

    async def typing(self, chat_id: str) -> None:
        return None                             # Slack shows no typing for bots

    async def react(self, chat_id: str, message_id: str, emoji: str) -> None:
        name = EMOJI.get(emoji)
        if not (name and ID_OK.match(chat_id) and TS_OK.match(message_id)):
            return
        try:
            await self._call("reactions.add", {"channel": chat_id, "timestamp": message_id, "name": name}, timeout=10)
        except (ValueError, ChannelRetry):
            return None

    async def clear_buttons(self, chat_id: str, message_id: str) -> None:
        text = self._sent.pop(message_id, None)
        if text is None or not (ID_OK.match(chat_id) and TS_OK.match(message_id)):
            return
        try:
            await self._call("chat.update", {"channel": chat_id, "ts": message_id, "text": text, "blocks": []}, timeout=10)
        except (ValueError, ChannelRetry):
            return None

    async def download(self, f: IncomingFile) -> AsyncIterator[bytes]:
        if (urlsplit(f.file_id).hostname or "").lower() not in FILE_HOSTS or urlsplit(f.file_id).scheme != "https":
            raise ValueError("Slack gave no file")
        try:
            r = await self._fetch("GET", f.file_id, allowed_hosts=FILE_HOSTS, headers={"Authorization": f"Bearer {self._token('bot')}"},
                                  timeout=120, max_body=MAX_FILE + 1)
        except NetGuardError:
            raise ChannelRetry(5) from None
        if r.status != 200:
            raise ValueError("Slack would not send the file")
        if len(r.body) > MAX_FILE:
            raise ValueError("the file is too large")
        for i in range(0, len(r.body), 65536):
            yield r.body[i:i + 65536]

    # ---- events -> items
    async def _title(self, channel: str) -> str:
        if channel not in self.titles:
            try:
                info = await self._call("conversations.info", {"channel": channel}, timeout=10)
                self.titles[channel] = "#" + str((info.get("channel") or {}).get("name") or channel)
            except (ValueError, ChannelRetry):
                return f"#{channel}"
        return self.titles[channel]

    async def _event(self, e: dict[str, Any]) -> list[Incoming | Callback | Membership]:
        kind = e.get("type")
        if kind == "member_joined_channel" and str(e.get("user", "")) == self.me_id and self.me_id:
            channel = str(e.get("channel", ""))
            return [Membership(channel, await self._title(channel), str(e.get("inviter") or ""), True)]
        if kind in ("channel_left", "group_left"):
            channel = str(e.get("channel", ""))
            return [Membership(channel, self.titles.pop(channel, f"#{channel}"), "", False)]
        if kind == "message":
            m = await self._message(e)
            return [m] if m is not None else []
        return []

    async def _message(self, e: dict[str, Any]) -> Incoming | None:
        user = str(e.get("user") or "")
        if (not user or user == self.me_id or e.get("bot_id") or e.get("subtype") not in (None, "file_share", "thread_broadcast")):
            return None                         # itself, other bots, edits, deletes, join notices
        channel, ts = str(e.get("channel", "")), str(e.get("ts", ""))
        if not (ID_OK.match(channel) and TS_OK.match(ts)):
            return None
        text = str(e.get("text") or "")
        mentioned = bool(self.me_id) and f"<@{self.me_id}>" in text
        if self.me_id:
            text = text.replace(f"<@{self.me_id}>", f"@{self.me_name or 'bot'}")
        private = e.get("channel_type") == "im"
        inc = Incoming(chat_id=channel, user_id=user, message_id=ts, text=text, private=private)
        if not private:
            root = str(e.get("thread_ts") or ts)
            self._threads.put(ts, root)
            inc.chat_title = await self._title(channel)
            inc.mentioned = mentioned
            inc.reply_to_bot = bool(e.get("thread_ts")) and str(e.get("parent_user_id") or "") == self.me_id and bool(self.me_id)
        for f in e.get("files") or []:
            url = str(f.get("url_private_download") or "")
            kind = "photo" if str(f.get("mimetype") or "").startswith("image/") else "file"
            inc.files.append(IncomingFile(url, f"slack-{ts.replace('.', '-')}-{_safe_name(str(f.get('name') or 'file'))}", int(f.get("size") or 0), kind))
        if any(str(f.get("subtype") or "") == "slack_audio" or str(f.get("mimetype") or "").startswith("audio/") for f in e.get("files") or []):
            inc.files, inc.unsupported = [], True
        return inc

    def _action(self, p: dict[str, Any]) -> Callback | None:
        actions = p.get("actions") or []
        if not actions:
            return None
        return Callback(chat_id=str((p.get("channel") or {}).get("id", "")), user_id=str((p.get("user") or {}).get("id", "")),
                        message_id=str((p.get("message") or {}).get("ts", "")), data=str(actions[0].get("value") or ""),
                        callback_id=str(p.get("trigger_id") or ""))
