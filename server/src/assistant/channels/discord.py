"""Discord as a Channel (base.py): REST for everything the bot says and does, the Gateway (a websocket) for what people say to it.

REST: https://discord.com/api/v10 with `Authorization: Bot <token>` through `fetch` (netguard.fetch: only Discord's hosts, only public
addresses, no redirects), so tests give it a fake and nothing else touches the network. The token is read at each call and is never put
in a log line or an exception message: errors are built from the method and path only.

Gateway (docs: discord.com/developers/docs/events/gateway): HELLO -> heartbeats -> IDENTIFY (or RESUME after a drop). The session id,
the resume address and the last sequence number are kept on the instance, so even a restart of receive() resumes. Discord has no
update offsets: every item has update_id 0 and the manager keeps no offset for it.

Groups: a Discord server ("guild") counts as "added by the owner" when its owner is the paired account. The channel only reports
the facts (GUILD_CREATE -> Membership with added_by = the guild's owner); the manager compares with the pairing. A guild's messages
carry group_id "guild:<id>", their replies go to the message's own channel.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote, urlsplit

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from .. import netguard
from ..i18n import t
from ..netguard import NetGuardError
from .base import Button, Callback, Channel, ChannelAuthError, ChannelRefused, ChannelRetry, Incoming, IncomingFile, Membership
from .markup import chunks

API = "https://discord.com/api/v10"
HOSTS = ["discord.com", "cdn.discordapp.com", "media.discordapp.net"]
GATEWAY_SUFFIXES = ("discord.gg",)           # the only hosts the gateway address may name (also the resume address)
TOKEN_OK = re.compile(r"^[A-Za-z0-9._-]{20,200}$")
ID_OK = re.compile(r"^[0-9A-Za-z_-]{1,300}$")
# GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
INTENTS = 1 | (1 << 9) | (1 << 12) | (1 << 15)
MAX_FILE = 25_000_000
MAX_FRAME = 16 * 1024 * 1024
BUTTONS_PER_ROW, ROWS = 5, 5
VOICE_MESSAGE = 1 << 13                      # message flag


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "file"


class Discord(Channel):
    id = "discord"
    item = "discord-bot"
    name = "Discord"
    LIMIT = 1900                              # Discord allows 2000 characters a message; keep a margin
    FILE_LIMIT = MAX_FILE

    def __init__(self, token_fn: Callable[[], str], *, fetch: Callable[..., Any] = netguard.fetch, connect: Callable[..., Any] | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep, rand: Callable[[], float] = random.random) -> None:
        self._token = token_fn
        self._fetch = fetch
        self._connect = connect or websockets.connect
        self._sleep, self._rand = sleep, rand
        self.me_id, self.me_name = "", ""
        self.session_id, self.resume_url, self.seq = "", "", None      # kept across reconnects
        self.guilds: dict[str, dict[str, str]] = {}                    # id -> {"name", "owner_id"}
        self.channels: dict[str, str] = {}                              # channel id -> name
        self._acked = True

    # ---- REST
    def _auth(self) -> str:
        token = self._token()
        if not TOKEN_OK.match(token or ""):
            raise ChannelAuthError("no usable bot token")
        return token

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: float = 30) -> Any:
        headers = {"Authorization": f"Bot {self._auth()}", "User-Agent": "DiscordBot (kelo, 1.0)"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            r = await self._fetch(method, API + path, allowed_hosts=HOSTS, headers=headers,
                                  content=json.dumps(body).encode() if body is not None else None, timeout=timeout)
        except NetGuardError:
            raise ChannelRetry(5) from None
        try:
            data = json.loads(r.body) if r.body else None
        except ValueError:
            data = None
        if 200 <= r.status < 300:
            return data
        if r.status == 401:
            raise ChannelAuthError(f"Discord refused the token ({method} {path.split('?')[0]})")
        if r.status == 429:
            after = (data or {}).get("retry_after") if isinstance(data, dict) else None
            raise ChannelRetry(float(after or (r.headers or {}).get("retry-after") or 5))
        if r.status >= 500:
            raise ChannelRetry(5)
        raise ValueError(str((data or {}).get("message") if isinstance(data, dict) else "") or f"{method} failed ({r.status})")

    # ---- Channel
    async def me(self) -> dict[str, str]:
        r = await self._call("GET", "/users/@me")
        self.me_id, self.me_name = str(r.get("id", "")), str(r.get("username", ""))
        return {"id": self.me_id, "username": self.me_name, "name": str(r.get("global_name") or r.get("username") or "")}

    def memberships(self) -> list[Membership]:
        return [Membership(f"guild:{gid}", g["name"], g["owner_id"], True) for gid, g in self.guilds.items()]

    async def receive(self) -> AsyncIterator[Incoming | Callback | Membership]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        task = asyncio.get_running_loop().create_task(self._gateway(queue))
        try:
            while True:
                item = await queue.get()
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def send(self, chat_id: str, text: str, buttons: list[list[Button]] | None = None, reply_to: str = "") -> str:
        """`text` is Markdown, which Discord renders itself; long text goes out as several messages, the buttons under the last."""
        if not ID_OK.match(chat_id):
            raise ValueError("not a Discord channel")
        parts = chunks(text, self.LIMIT) or ["-"]
        last = ""
        for n, part in enumerate(parts):
            body: dict[str, Any] = {"content": part, "allowed_mentions": {"parse": [], "replied_user": False}}   # a reply never pings anyone
            if reply_to and n == 0 and ID_OK.match(reply_to):
                body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
            if buttons and n == len(parts) - 1:
                body["components"] = self._rows(buttons)
            r = await self._call("POST", f"/channels/{chat_id}/messages", body)
            last = str((r or {}).get("id", ""))
        return last

    @staticmethod
    def _rows(buttons: list[list[Button]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in buttons:
            for i in range(0, len(row), BUTTONS_PER_ROW):
                rows.append({"type": 1, "components": [{"type": 2, "style": 4 if b.data.endswith(":0") else 1, "label": b.text[:80],
                                                        "custom_id": b.data[:100]} for b in row[i:i + BUTTONS_PER_ROW]]})
        return rows[:ROWS]

    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        if not (ID_OK.match(chat_id) and ID_OK.match(message_id)):
            return
        first = (chunks(text, self.LIMIT) or ["-"])[0]
        await self._call("PATCH", f"/channels/{chat_id}/messages/{message_id}", {"content": first, "components": []})

    async def typing(self, chat_id: str) -> None:
        if ID_OK.match(chat_id):
            await self._call("POST", f"/channels/{chat_id}/typing", timeout=10)

    async def react(self, chat_id: str, message_id: str, emoji: str) -> None:
        if not (ID_OK.match(chat_id) and ID_OK.match(message_id)):
            return
        try:
            await self._call("PUT", f"/channels/{chat_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me", timeout=10)
        except (ValueError, ChannelRetry):
            return None

    async def clear_buttons(self, chat_id: str, message_id: str) -> None:
        if not (ID_OK.match(chat_id) and ID_OK.match(message_id)):
            return
        try:
            await self._call("PATCH", f"/channels/{chat_id}/messages/{message_id}", {"components": []}, timeout=10)
        except (ValueError, ChannelRetry):
            return None

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Type 6: the tap is acknowledged and nothing else changes (the manager edits the message itself)."""
        iid, _, token = callback_id.partition(":")
        if not (ID_OK.match(iid) and ID_OK.match(token)):
            return
        try:
            await self._call("POST", f"/interactions/{iid}/{token}/callback", {"type": 6}, timeout=10)
        except (ValueError, ChannelRetry):
            return None

    async def download(self, f: IncomingFile) -> AsyncIterator[bytes]:
        host = (urlsplit(f.file_id).hostname or "").lower()
        if host not in HOSTS[1:]:
            raise ValueError("Discord gave no file")
        try:
            r = await self._fetch("GET", f.file_id, allowed_hosts=HOSTS[1:], timeout=120, max_body=MAX_FILE + 1)   # public: no token sent
        except NetGuardError:
            raise ChannelRetry(5) from None
        if r.status != 200:
            raise ValueError("Discord would not send the file")
        if len(r.body) > MAX_FILE:
            raise ValueError("the file is too large")
        for i in range(0, len(r.body), 65536):
            yield r.body[i:i + 65536]

    # ---- Gateway
    async def _gateway(self, queue: asyncio.Queue[Any]) -> None:
        """Runs the connection and hands every parsed item to `queue`; whatever stops it (the three channel errors, a surprise) is
        put there too, so receive() raises it to the manager."""
        try:
            await self._sessions(queue)
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001
            queue.put_nowait(e)

    async def _gateway_url(self) -> str:
        data = await self._call("GET", "/gateway/bot")
        return self._wss(str((data or {}).get("url") or ""))

    @staticmethod
    def _wss(url: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "wss" or not any(host == s or host.endswith("." + s) for s in GATEWAY_SUFFIXES):
            raise ChannelRetry(30)            # not Discord's gateway: never connect (the token goes over this socket)
        return f"wss://{host}"

    async def _sessions(self, queue: asyncio.Queue[Any]) -> None:
        while True:
            url = self._wss(self.resume_url) if self.session_id and self.resume_url else await self._gateway_url()
            try:
                async with self._connect(f"{url}/?v=10&encoding=json", max_size=MAX_FRAME, ping_interval=None, open_timeout=20) as ws:
                    await self._socket(ws, queue)
            except ConnectionClosed as e:
                self._closed(e)
            except (OSError, asyncio.TimeoutError, WebSocketException):
                raise ChannelRetry(5) from None
            await self._sleep(1)               # then the next try (resume when the session is still good)

    def _closed(self, e: ConnectionClosed) -> None:
        code = e.rcvd.code if e.rcvd is not None else None
        if code == 4004:
            raise ChannelAuthError("Discord refused the token")
        if code == 4014:
            raise ChannelRefused(t("Discord refused: turn on Message Content Intent for the bot in the Discord developer portal."))
        if code in (4007, 4009):               # the session cannot be resumed: start a new one
            self.session_id, self.resume_url, self.seq = "", "", None
        elif code in (4010, 4011, 4012, 4013):
            raise ChannelRetry(60)

    async def _send_op(self, ws: Any, op: int, d: Any) -> None:
        await ws.send(json.dumps({"op": op, "d": d}))

    async def _identify(self, ws: Any) -> None:
        await self._send_op(ws, 2, {"token": self._auth(), "intents": INTENTS,
                                    "properties": {"os": "linux", "browser": "kelo", "device": "kelo"}})

    async def _socket(self, ws: Any, queue: asyncio.Queue[Any]) -> None:
        hello = json.loads(await ws.recv())
        if hello.get("op") != 10:
            raise ChannelRetry(5)
        interval = float((hello.get("d") or {}).get("heartbeat_interval") or 41250) / 1000
        self._acked = True
        beat = asyncio.get_running_loop().create_task(self._beat(ws, interval))
        try:
            if self.session_id:
                await self._send_op(ws, 6, {"token": self._auth(), "session_id": self.session_id, "seq": self.seq})
            else:
                await self._identify(ws)
            while True:
                frame = json.loads(await ws.recv())            # raises ConnectionClosed when the socket ends
                if frame.get("s") is not None:
                    self.seq = frame["s"]
                op = frame.get("op")
                if op == 0:
                    for item in self._dispatch(str(frame.get("t") or ""), frame.get("d") or {}):
                        queue.put_nowait(item)
                elif op == 1:
                    await self._send_op(ws, 1, self.seq)
                elif op == 11:
                    self._acked = True
                elif op == 7:                  # RECONNECT: Discord asks for a new connection, the session stays good
                    return
                elif op == 9:                  # INVALID_SESSION: wait a little, then start a new session on this socket
                    self.session_id, self.resume_url, self.seq = "", "", None
                    await self._sleep(1 + 4 * self._rand())
                    await self._identify(ws)
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)

    async def _beat(self, ws: Any, interval: float) -> None:
        await self._sleep(interval * self._rand())
        while True:
            if not self._acked:                # the last heartbeat was never answered: the connection is dead
                await ws.close(4000, "no heartbeat ack")
                return
            self._acked = False
            await self._send_op(ws, 1, self.seq)
            await self._sleep(interval)

    # ---- events -> items
    def _dispatch(self, kind: str, d: dict[str, Any]) -> list[Incoming | Callback | Membership]:
        if kind == "READY":
            user = d.get("user") or {}
            self.me_id, self.me_name = str(user.get("id", "")), str(user.get("username", ""))
            self.session_id, self.resume_url = str(d.get("session_id") or ""), str(d.get("resume_gateway_url") or "")
            return []
        if kind == "GUILD_CREATE":
            gid = str(d.get("id", ""))
            self.guilds[gid] = {"name": str(d.get("name") or ""), "owner_id": str(d.get("owner_id") or "")}
            for c in [*(d.get("channels") or []), *(d.get("threads") or [])]:
                self.channels[str(c.get("id", ""))] = str(c.get("name") or "")
            return [Membership(f"guild:{gid}", self.guilds[gid]["name"], self.guilds[gid]["owner_id"], True)]
        if kind == "GUILD_DELETE":
            gid = str(d.get("id", ""))
            if d.get("unavailable"):           # an outage on Discord's side, not a removal
                return []
            g = self.guilds.pop(gid, None)
            return [Membership(f"guild:{gid}", (g or {}).get("name", ""), "", False)]
        if kind in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "THREAD_CREATE"):
            self.channels[str(d.get("id", ""))] = str(d.get("name") or "")
            return []
        if kind == "MESSAGE_CREATE":
            m = self._message(d)
            return [m] if m is not None else []
        if kind == "INTERACTION_CREATE" and d.get("type") == 3:
            who = (d.get("member") or {}).get("user") or d.get("user") or {}
            return [Callback(chat_id=str(d.get("channel_id", "")), user_id=str(who.get("id", "")), message_id=str((d.get("message") or {}).get("id", "")),
                             data=str((d.get("data") or {}).get("custom_id") or ""), callback_id=f"{d.get('id', '')}:{d.get('token', '')}")]
        return []

    def _message(self, d: dict[str, Any]) -> Incoming | None:
        author = d.get("author") or {}
        if str(author.get("id", "")) == self.me_id or d.get("type", 0) not in (0, 19):
            return None                       # itself, join notices and the like (other bots and webhooks are kept: they are read like anyone's message)
        mid, channel = str(d.get("id", "")), str(d.get("channel_id", ""))
        text = str(d.get("content") or "")
        if self.me_id:
            text = re.sub(rf"<@!?{re.escape(self.me_id)}>", f"@{self.me_name or 'bot'}", text)
        inc = Incoming(chat_id=channel, user_id=str(author.get("id", "")), message_id=mid, text=text, private=not d.get("guild_id"),
                       name=str(author.get("global_name") or author.get("username") or ""),
                       forwarded=bool(d.get("message_snapshots")))   # a forwarded message carries its snapshots (Discord API)
        if d.get("guild_id"):
            gid = str(d["guild_id"])
            guild = self.guilds.get(gid, {}).get("name", "")
            chan = self.channels.get(channel, "")
            inc.group_id = f"guild:{gid}"
            inc.chat_title = f"{guild} #{chan}" if guild and chan else guild
            inc.mentioned = any(str(u.get("id", "")) == self.me_id for u in d.get("mentions") or []) and bool(self.me_id)
            inc.reply_to_bot = bool(self.me_id) and str(((d.get("referenced_message") or {}).get("author") or {}).get("id", "")) == self.me_id
        for a in d.get("attachments") or []:
            kind = "photo" if str(a.get("content_type") or "").startswith("image/") else "file"
            inc.files.append(IncomingFile(str(a.get("url") or ""), f"discord-{mid}-{_safe_name(str(a.get('filename') or 'file'))}",
                                          int(a.get("size") or 0), kind))
        if int(d.get("flags") or 0) & VOICE_MESSAGE or (d.get("sticker_items") and not inc.files):
            inc.files, inc.unsupported = [], True
        return inc
