"""Discord as a way into the owner's agent (channels/discord.py). A fake gateway feeds JSON frames and a fake REST answers; nothing
touches the network."""

import asyncio
import json
import logging
import re
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from assistant.agent import Tool
from assistant.channels import Button, ChannelAuthError, ChannelRefused, ChannelRetry, Discord
from assistant.channels.base import Callback, Incoming, IncomingFile, Membership
from assistant.netguard import NetGuardError, Reply
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app

from test_channels_telegram import until
from test_extract import CONTENT
from test_server import AUTH, TOKEN, Running

DC_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcDE.secret-token-value-1234567890"
BOT = "900"


def j(obj, status=200):
    return Reply(status, {"content-type": "application/json"}, json.dumps(obj).encode() if obj is not None else b"", False)


class FakeREST:
    """Stands in for netguard.fetch. `calls` is [(method, path, body)]; `script[(method, path prefix)]` is a list of replies used in
    order (a dict, a (status, dict) pair, or an exception to raise)."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.headers: list[dict] = []
        self.script: dict[tuple[str, str], list] = {}
        self.files: dict[str, bytes] = {}
        self.next_id = 500

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None, timeout=None, max_body=None):
        parts = urlsplit(url)
        assert parts.hostname in allowed_hosts and parts.hostname in ("discord.com", "cdn.discordapp.com", "media.discordapp.net")
        if parts.hostname != "discord.com":
            self.calls.append((method, url, None))
            return Reply(200, {}, self.files[url], False)
        path = unquote(parts.path)[len("/api/v10"):]
        assert parts.path.startswith("/api/v10/")
        self.headers.append(dict(headers or {}))
        body = json.loads(content) if content else None
        self.calls.append((method, path, body))
        for (m, prefix), queued in self.script.items():
            if m == method and path.startswith(prefix) and queued:
                r = queued.pop(0)
                if isinstance(r, Exception):
                    raise r
                return j(r[1], r[0]) if isinstance(r, tuple) else j(r)
        if (method, path) == ("GET", "/users/@me"):
            return j({"id": BOT, "username": "kelobot", "global_name": "Kelo"})
        if (method, path) == ("GET", "/gateway/bot"):
            return j({"url": "wss://gateway.discord.gg"})
        if method == "POST" and re.fullmatch(r"/channels/[^/]+/messages", path):
            self.next_id += 1
            return j({"id": str(self.next_id)})
        return j(None, 204)

    def of(self, method, pattern):
        return [b for m, p, b in self.calls if m == method and re.fullmatch(pattern, p)]

    def sent(self, channel=None):
        return [b["content"] for m, p, b in self.calls if m == "POST" and re.fullmatch(r"/channels/%s/messages" % (channel or "[^/]+"), p)]


class FakeWS:
    def __init__(self, gw, url, kw):
        self.gw, self.url, self.kw = gw, url, kw
        self.q: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.q.put_nowait(json.dumps({"op": 10, "d": {"heartbeat_interval": gw.interval}}))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def recv(self):
        item = await self.q.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        self.gw.sent.append(frame)
        op = frame["op"]
        if op == 1 and self.gw.ack:
            self.q.put_nowait(json.dumps({"op": 11}))
        elif op in (2, 6):
            self.gw.on_auth(self, frame)

    async def close(self, code=1000, reason=""):
        self.q.put_nowait(ConnectionClosed(Close(code, reason), None))

    def frame(self, op, d=None, t=None, s=None):
        self.q.put_nowait(json.dumps({"op": op, "d": d, "t": t, "s": s}))

    def dispatch(self, t, d):
        self.gw.seq += 1
        self.frame(0, d, t, self.gw.seq)

    def drop(self, code=None):
        self.q.put_nowait(ConnectionClosed(Close(code, "") if code else None, None))


class FakeGateway:
    """`connect` for Discord(connect=...). Every socket starts with HELLO and answers IDENTIFY with READY and RESUME with RESUMED."""

    def __init__(self, interval=40):
        self.interval, self.ack = interval, True
        self.sockets: list[FakeWS] = []
        self.sent: list[dict] = []
        self.seq = 0
        self.refuse: Exception | None = None       # raised by connect()
        self.backlog: list[tuple[str, dict]] = []   # events delivered once the next session is up
        self.after_auth = None                      # callback(ws, frame) run after the normal answer

    def connect(self, url, **kw):
        if self.refuse is not None:
            raise self.refuse
        ws = FakeWS(self, url, kw)
        self.sockets.append(ws)
        return ws

    def on_auth(self, ws, frame):
        if frame["op"] == 2:
            ws.dispatch("READY", {"user": {"id": BOT, "username": "kelobot"}, "session_id": f"S{len(self.sockets)}",
                                  "resume_gateway_url": "wss://resume.discord.gg"})
        else:
            ws.dispatch("RESUMED", {})
        for t, d in self.backlog:
            ws.dispatch(t, d)
        self.backlog.clear()
        if self.after_auth:
            self.after_auth(ws, frame)

    def push(self, t, d):
        """Deliver an event on the newest open socket (or with the next session)."""
        live = [w for w in self.sockets if not w.closed]
        if live:
            live[-1].dispatch(t, d)
        else:
            self.backlog.append((t, d))


async def fast(_s):
    await asyncio.sleep(0.005)


def make(gw=None, rest=None, token=DC_TOKEN, **kw):
    rest = rest or FakeREST()
    gw = gw or FakeGateway()
    return Discord(lambda: token, fetch=rest.fetch, connect=gw.connect, sleep=fast, rand=lambda: 0.0, **kw), rest, gw


class Feed:
    """Keeps receive() open (the heartbeats and the session live in it) and collects what it yields or the error it raises."""

    def __init__(self, dc):
        self.dc, self.items, self.exc = dc, [], None
        self.task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self):
        try:
            async for item in self.dc.receive():
                self.items.append(item)
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001
            self.exc = e

    async def got(self, n, limit=3.0):
        for _ in range(int(limit / 0.005)):
            if self.exc is not None:
                raise self.exc
            if len(self.items) >= n:
                return self.items
            await asyncio.sleep(0.005)
        raise AssertionError(f"only {len(self.items)} items")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


async def take(dc, n, limit=3.0):
    async with Feed(dc) as f:
        return list(await f.got(n, limit))


def dm(text, *, uid="u1", cid="dm1", mid="m1", **extra):
    return {"id": mid, "channel_id": cid, "content": text, "author": {"id": uid, "username": "ann", "global_name": "Ann"}, **extra}


def guild_msg(text, *, uid="u2", cid="c1", gid="g1", mid="m2", mention=True, **extra):
    return {"id": mid, "channel_id": cid, "guild_id": gid, "content": text, "author": {"id": uid, "username": "bob"},
            "mentions": [{"id": BOT}] if mention else [], **extra}


# ---- the gateway
async def test_identify_heartbeat_and_resume():
    dc, rest, gw = make()
    gw.backlog.append(("MESSAGE_CREATE", dm("hello")))
    async with Feed(dc) as feed:
        first = await feed.got(1)
        assert isinstance(first[0], Incoming) and first[0].text == "hello" and first[0].private and first[0].update_id == 0
        assert gw.sockets[0].url == "wss://gateway.discord.gg/?v=10&encoding=json" and gw.sockets[0].kw["ping_interval"] is None
        ident = next(f for f in gw.sent if f["op"] == 2)["d"]
        assert ident["token"] == DC_TOKEN and ident["intents"] == 1 | (1 << 9) | (1 << 12) | (1 << 15) == 37377
        assert ident["properties"] == {"os": "linux", "browser": "kelo", "device": "kelo"}
        await asyncio.sleep(0.2)                                    # heartbeats go on: the first at once (rand 0), then every 40 ms
        beats = [f for f in gw.sent if f["op"] == 1]
        assert len(beats) >= 2 and beats[-1]["d"] == 2              # the last sequence number (READY = 1, the message = 2)
        # the socket drops: the next one is a RESUME on the resume address, with the session and the last sequence number
        gw.after_auth = lambda ws, f: ws.dispatch("MESSAGE_CREATE", dm("again", mid="m9"))
        gw.sockets[0].drop(1006)
        items = await feed.got(2)
        assert items[1].text == "again"
        assert gw.sockets[1].url.startswith("wss://resume.discord.gg/")
        resume = next(f for f in gw.sent if f["op"] == 6)["d"]
        assert resume == {"token": DC_TOKEN, "session_id": "S1", "seq": 2}
        # op 7 RECONNECT: reconnect and resume again, no new identify
        gw.after_auth = None
        gw.sockets[1].frame(7)
        await until(lambda: len(gw.sockets) >= 3 and len([f for f in gw.sent if f["op"] == 6]) == 2)
        assert len([f for f in gw.sent if f["op"] == 2]) == 1


async def test_invalid_session_waits_then_identifies_again():
    dc, rest, gw = make()
    slept = []

    async def noting(s):
        slept.append(s)
        await asyncio.sleep(0.005)
    dc._sleep = noting
    dc._rand = lambda: 0.5
    gw.after_auth = lambda ws, f: ws.frame(9, False) if len([x for x in gw.sent if x["op"] == 2]) == 1 else ws.dispatch("MESSAGE_CREATE", dm("second"))
    got = await take(dc, 1)
    assert got[0].text == "second"
    assert len([f for f in gw.sent if f["op"] == 2]) == 2 and 3.0 in slept            # 1 + 4 * 0.5 seconds
    assert dc.session_id == "S1"


async def test_close_4004_is_an_auth_stop_and_4014_asks_for_the_intent():
    dc, rest, gw = make()
    gw.after_auth = lambda ws, f: ws.drop(4004)
    with pytest.raises(ChannelAuthError) as e:
        await take(dc, 1)
    assert DC_TOKEN not in str(e.value) and not isinstance(e.value, ChannelRefused)
    dc, rest, gw = make()
    gw.after_auth = lambda ws, f: ws.drop(4014)
    with pytest.raises(ChannelRefused) as e:
        await take(dc, 1)
    assert e.value.notice == "Discord refused: turn on Message Content Intent for the bot in the Discord developer portal."
    # 4009: the session is gone, so the next connect is an IDENTIFY
    dc, rest, gw = make()
    gw.after_auth = lambda ws, f: (ws.drop(4009) if len(gw.sockets) == 1 else ws.dispatch("MESSAGE_CREATE", dm("fresh")))
    assert (await take(dc, 1))[0].text == "fresh" and len([f for f in gw.sent if f["op"] == 2]) == 2


async def test_unanswered_heartbeat_closes_the_connection():
    dc, rest, gw = make(FakeGateway(interval=20))
    gw.ack = False
    gw.after_auth = lambda ws, f: ws.dispatch("MESSAGE_CREATE", dm("x")) if len(gw.sockets) > 1 else None
    got = await take(dc, 1)
    assert got[0].text == "x" and len(gw.sockets) >= 2


async def test_only_discords_own_gateway_host_is_used():
    rest = FakeREST()
    rest.script[("GET", "/gateway/bot")] = [{"url": "wss://evil.example.com"}]
    dc, rest, gw = make(rest=rest)
    with pytest.raises(ChannelRetry):
        await take(dc, 1)
    assert gw.sockets == []                                      # the token never went to that host
    dc, rest, gw = make()
    gw.refuse = OSError("no route")
    with pytest.raises(ChannelRetry):
        await take(dc, 1)


# ---- events -> items
async def test_events_become_items():
    dc, rest, gw = make()
    gw.backlog += [
        ("GUILD_CREATE", {"id": "g1", "name": "Home", "owner_id": "u1", "channels": [{"id": "c1", "name": "general"}]}),
        ("MESSAGE_CREATE", dm("hi", mid="a1")),
        ("MESSAGE_CREATE", {**dm("bot talk", mid="a2"), "author": {"id": "b1", "bot": True}}),                    # other bots: never
        ("MESSAGE_CREATE", dm("me", mid="a3", uid=BOT)),                                                           # itself: never
        ("MESSAGE_CREATE", guild_msg(f"<@{BOT}> hello there", mid="a4")),
        ("MESSAGE_CREATE", guild_msg("chatter", mid="a5", mention=False)),
        ("MESSAGE_CREATE", guild_msg("a reply", mid="a6", mention=False, referenced_message={"id": "x", "author": {"id": BOT}})),
        ("MESSAGE_CREATE", dm("", mid="a7", attachments=[{"url": "https://cdn.discordapp.com/attachments/1/2/pic.png?ex=1", "filename": "my pic.png",
                                                          "size": 12, "content_type": "image/png"},
                                                         {"url": "https://cdn.discordapp.com/a/b.pdf", "filename": "b.pdf", "size": 3}])),
        ("MESSAGE_CREATE", dm("", mid="a8", sticker_items=[{"id": "s"}])),
        ("MESSAGE_CREATE", {**dm("joined", mid="a9"), "type": 7}),                                                  # a system message: never
        ("INTERACTION_CREATE", {"id": "i1", "token": "tok-1", "type": 3, "channel_id": "dm1", "message": {"id": "m5"},
                                "user": {"id": "u1"}, "data": {"custom_id": "a:abc:1"}}),
        ("INTERACTION_CREATE", {"id": "i2", "token": "tok-2", "type": 2, "channel_id": "dm1", "data": {}}),         # not a component tap
        ("GUILD_DELETE", {"id": "g1", "unavailable": True}),                                                         # an outage, not a removal
        ("GUILD_DELETE", {"id": "g1"}),
    ]
    items = await take(dc, 9)
    member, hi, mention, chatter, reply, files, sticker, tap, left = items
    assert member == Membership("guild:g1", "Home", "u1", True, 0)
    assert (hi.text, hi.private, hi.chat_id, hi.user_id, hi.name, hi.message_id) == ("hi", True, "dm1", "u1", "Ann", "a1")
    assert not mention.private and mention.group_id == "guild:g1" and mention.chat_id == "c1" and mention.chat_title == "Home #general"
    assert mention.mentioned and not mention.reply_to_bot and mention.text == "@kelobot hello there" and mention.name == "bob"
    assert not chatter.mentioned and not chatter.reply_to_bot
    assert reply.reply_to_bot and not reply.mentioned
    assert files.files == [IncomingFile("https://cdn.discordapp.com/attachments/1/2/pic.png?ex=1", "discord-a7-my_pic.png", 12, "photo"),
                           IncomingFile("https://cdn.discordapp.com/a/b.pdf", "discord-a7-b.pdf", 3, "file")]
    assert sticker.unsupported
    assert isinstance(tap, Callback) and (tap.chat_id, tap.user_id, tap.message_id, tap.data, tap.callback_id) == ("dm1", "u1", "m5", "a:abc:1", "i1:tok-1")
    assert left == Membership("guild:g1", "Home", "", False, 0)       # only the real removal (the outage said nothing)
    assert [m.chat_id for m in dc.memberships()] == []


# ---- REST
async def test_send_chunks_buttons_reply_and_no_pings():
    dc, rest, gw = make()
    text = "\n\n".join(f"paragraph {i} " + "word " * 80 for i in range(12))
    last = await dc.send("c1", text, [[Button("Allow", "a:x:1"), Button("Deny", "a:x:0")]], "m7")
    posts = rest.of("POST", r"/channels/c1/messages")
    assert len(posts) >= 3 and all(len(p["content"]) <= 1900 for p in posts) and last == str(rest.next_id)
    assert posts[0]["message_reference"] == {"message_id": "m7", "fail_if_not_exists": False} and all("message_reference" not in p for p in posts[1:])
    assert all("components" not in p for p in posts[:-1])
    row, = posts[-1]["components"]
    assert row["type"] == 1 and [(c["type"], c["style"], c["label"], c["custom_id"]) for c in row["components"]] == [
        (2, 1, "Allow", "a:x:1"), (2, 4, "Deny", "a:x:0")]
    assert all(p["allowed_mentions"] == {"parse": [], "replied_user": False} for p in posts)
    assert "".join(p["content"].replace("\n", "").replace(" ", "") for p in posts) == text.replace("\n", "").replace(" ", "")
    big = [[Button(f"b{i}", f"r:{i}")] for i in range(8)]
    await dc.send("c1", "pick", big)
    assert len(rest.of("POST", r"/channels/c1/messages")[-1]["components"]) == 5                  # five rows at most
    one_row = [[Button(f"b{i}", f"r:{i}") for i in range(7)]]
    await dc.send("c1", "pick", one_row)
    assert [len(r["components"]) for r in rest.of("POST", r"/channels/c1/messages")[-1]["components"]] == [5, 2]
    assert await dc.send("c1", "") and rest.of("POST", r"/channels/c1/messages")[-1]["content"] == "-"
    with pytest.raises(ValueError):
        await dc.send("../x", "no")


async def test_edit_typing_react_clear_and_callback():
    dc, rest, gw = make()
    await dc.edit("dm1", "m1", "new text")
    assert ("PATCH", "/channels/dm1/messages/m1", {"content": "new text", "components": []}) in rest.calls
    await dc.typing("dm1")
    await dc.react("dm1", "m1", "👀")
    assert ("PUT", "/channels/dm1/messages/m1/reactions/👀/@me", None) in rest.calls
    await dc.clear_buttons("dm1", "m1")
    assert ("PATCH", "/channels/dm1/messages/m1", {"components": []}) in rest.calls
    await dc.answer_callback("i1:tok-1")
    assert ("POST", "/interactions/i1/tok-1/callback", {"type": 6}) in rest.calls
    rest.script[("PUT", "/channels")] = [(400, {"message": "Unknown Emoji"})]
    await dc.react("dm1", "m1", "x")                                    # a refused reaction is not an error
    await dc.answer_callback("../../x:y")                               # nothing is called for a malformed id
    assert len(rest.of("POST", r"/interactions/.*")) == 1
    assert {h["Authorization"] for h in rest.headers} == {f"Bot {DC_TOKEN}"}


async def test_rest_errors():
    dc, rest, gw = make()
    rest.script[("POST", "/channels")] = [(401, {"message": "401: Unauthorized"})]
    with pytest.raises(ChannelAuthError) as e:
        await dc.send("c1", "hi")
    assert DC_TOKEN not in str(e.value)
    rest.script[("POST", "/channels")] = [(429, {"message": "You are being rate limited.", "retry_after": 2.5, "global": False})]
    with pytest.raises(ChannelRetry) as r:
        await dc.send("c1", "hi")
    assert r.value.after == 2.5
    rest.script[("POST", "/channels")] = [(502, {}), NetGuardError("the request timed out")]
    for _ in range(2):
        with pytest.raises(ChannelRetry):
            await dc.send("c1", "hi")
    rest.script[("POST", "/channels")] = [(403, {"message": "Missing Permissions", "code": 50013})]
    with pytest.raises(ValueError, match="Missing Permissions"):
        await dc.send("c1", "hi")
    assert await dc.me() == {"id": BOT, "username": "kelobot", "name": "Kelo"}
    with pytest.raises(ChannelAuthError):
        await Discord(lambda: "", fetch=rest.fetch).me()


async def test_download_reads_only_discords_cdn():
    dc, rest, gw = make()
    url = "https://cdn.discordapp.com/attachments/1/2/pic.png"
    rest.files[url] = b"x" * 150_000
    data = b"".join([c async for c in dc.download(IncomingFile(url, "discord-1-pic.png", 150_000, "photo"))])
    assert data == b"x" * 150_000
    assert {h.get("Authorization") for h in rest.headers} == set()           # the token never goes to the CDN
    with pytest.raises(ValueError):
        b"".join([c async for c in dc.download(IncomingFile("https://evil.example.com/a.png", "a", 1))])


# ---- the manager, in a whole app with a fake gateway and a scripted model
class DRig:
    def __init__(self, tmp_path, *steps, tools=None):
        self.rest, self.gw = FakeREST(), FakeGateway()
        self.seen: list = []
        self.steps = steps or ([TextDelta("Hello from Kelo"), Done("stop")],)

        async def model(prov, request, **kw):
            self.seen.append(request)
            for ev in self.steps[min(len(self.seen) - 1, len(self.steps) - 1)]:
                yield ev
        app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"),
                                  content_dir=str(CONTENT)), tools=tools if tools is not None else [], stream_fn=model,
                         mcp_fetcher=self.rest.fetch, channel_fetch=self.rest.fetch, channel_connect=self.gw.connect)
        self.app, self.run = app, Running(app)

    async def __aenter__(self):
        await self.run.__aenter__()
        self.c = httpx.AsyncClient(base_url=self.run.url, headers=AUTH)
        self.mgr = self.app.state.channels
        self.store = self.app.state.appctx.store
        self.activity = self.app.state.appctx.activity
        assert (await self.c.put("/api/ai-consent", json={"accepted": True})).status_code == 200

        async def quick(s):
            await asyncio.sleep(0.005)
        self.mgr._sleep = quick
        return self

    async def __aexit__(self, *exc):
        await self.c.aclose()
        await self.run.__aexit__(*exc)

    async def connect(self):
        r = await self.c.post("/api/keys/discord", json={"values": {"token": DC_TOKEN}})
        assert r.status_code == 200, r.text
        await until(lambda: self.mgr.status("discord")["connected"] and self.mgr.state("discord")["pairing"])

    def code(self):
        for m in reversed(self.store.messages()):
            if m["role"] == "assistant" and (hit := re.search(r"Send (\d{6}) to the bot", m["content"])):
                return hit.group(1)

    async def pair(self, uid="u1", cid="dm1"):
        await self.connect()
        self.gw.push("MESSAGE_CREATE", dm(self.code(), uid=uid, cid=cid, mid="p1"))
        await until(lambda: self.mgr.state("discord")["paired"])

    def messages(self):
        return [(m["role"], m["content"], m["via"]) for m in self.store.messages()]


@pytest.fixture
def rig(tmp_path):
    def build(*steps, **kw):
        return DRig(tmp_path, *steps, **kw)
    return build


async def test_dm_pairing_and_a_chat_turn(rig, caplog):
    caplog.set_level(logging.DEBUG)
    async with rig() as r:
        await r.connect()
        pairing = next(m for m in r.store.messages() if "Discord bot kelobot is connected" in m["content"])
        assert "direct message" in pairing["content"] and "http" not in pairing["content"]
        assert r.mgr.state("discord")["bot"] == {"id": BOT, "username": "kelobot"}
        r.gw.push("MESSAGE_CREATE", dm("000000", uid="u9", cid="dm9", mid="x1"))                  # a wrong code from a stranger
        r.gw.push("MESSAGE_CREATE", dm(r.code(), uid="u1", cid="dm1", mid="p1"))
        await until(lambda: r.mgr.state("discord")["paired"])
        assert r.mgr.state("discord")["paired"]["user_id"] == "u1" and r.mgr.state("discord")["paired"]["chat_id"] == "dm1"
        assert "Connected. You can talk to Kelo here." in r.rest.sent("dm1") and r.rest.sent("dm9") == []
        r.gw.push("MESSAGE_CREATE", dm("what is up", mid="p2"))
        await until(lambda: "Hello from Kelo" in r.rest.sent("dm1"))
        assert ("user", "what is up", "discord") in r.messages() and ("assistant", "Hello from Kelo", "discord") in r.messages()
        assert "came from the user's Discord" in [m for m in r.seen[0].messages if m["role"] == "user"][-1]["content"]
        assert r.mgr.state("discord")["offset"] == 0                                          # no offsets on Discord
        assert r.rest.of("PUT", r"/channels/dm1/messages/p2/reactions/.*")                    # the 👀 reaction
        assert DC_TOKEN not in caplog.text and DC_TOKEN not in json.dumps(r.mgr.state("discord"))
        assert not any(DC_TOKEN in json.dumps(m) for m in r.store.messages())
        # a stranger's DM later still gets nothing
        r.gw.push("MESSAGE_CREATE", dm("hello", uid="u9", cid="dm9", mid="x2"))
        await asyncio.sleep(0.15)
        assert r.rest.sent("dm9") == []


async def test_a_guild_the_owner_owns_is_allowed_and_one_they_do_not_own_is_ignored(rig):
    async with rig() as r:
        # the servers are known before the pairing (GUILD_CREATE at connect); pairing then makes the owner's own usable
        r.gw.backlog += [("GUILD_CREATE", {"id": "g1", "name": "Home", "owner_id": "u1", "channels": [{"id": "c1", "name": "general"}]}),
                         ("GUILD_CREATE", {"id": "g2", "name": "Strangers", "owner_id": "u2", "channels": [{"id": "c2", "name": "chat"}]})]
        await r.pair()
        await until(lambda: "guild:g1" in r.mgr.state("discord")["groups"])
        assert list(r.mgr.state("discord")["groups"]) == ["guild:g1"]
        assert r.mgr.state("discord")["groups"]["guild:g1"]["title"] == "Home"
        line = 'I can now be called in the group "Home": mention me or reply to me there.'
        await until(lambda: line in r.rest.sent("dm1"))
        assert r.rest.sent("c1") == [] and r.rest.sent("c2") == []
        # the same server reported again (a new session): no second notice; a new one the owner creates later is used
        r.gw.push("GUILD_CREATE", {"id": "g1", "name": "Home", "owner_id": "u1", "channels": []})
        r.gw.push("GUILD_CREATE", {"id": "g3", "name": "Later", "owner_id": "u1", "channels": []})
        await until(lambda: "guild:g3" in r.mgr.state("discord")["groups"])
        assert r.rest.sent("dm1").count(line) == 1
        # a server the bot is removed from is forgotten
        r.gw.push("GUILD_DELETE", {"id": "g3"})
        await until(lambda: "guild:g3" not in r.mgr.state("discord")["groups"])


async def test_mention_in_a_guild_starts_a_group_turn_and_other_servers_do_not(rig):
    steps = ([TextDelta("Sunny in Seoul."), Done("stop")],)
    async with rig(*steps, tools=[Tool(n, n, {"type": "object"}, _noop, read_only=True) for n in ("web_search", "web_fetch", "weather", "read_file")]) as r:
        r.gw.backlog += [("GUILD_CREATE", {"id": "g1", "name": "Home", "owner_id": "u1", "channels": [{"id": "c1", "name": "general"}]}),
                         ("GUILD_CREATE", {"id": "g2", "name": "Strangers", "owner_id": "u2", "channels": [{"id": "c2", "name": "chat"}]})]
        await r.pair()
        await until(lambda: "guild:g1" in r.mgr.state("discord")["groups"])
        before = len(r.store.messages())
        r.gw.push("MESSAGE_CREATE", guild_msg("chatting about lunch", mid="g0", mention=False))     # not called: context only
        r.gw.push("MESSAGE_CREATE", guild_msg(f"<@{BOT}> how is the weather", mid="g1m"))
        await until(lambda: "Sunny in Seoul." in r.rest.sent("c1"))
        post = [b for b in r.rest.of("POST", r"/channels/c1/messages")][-1]
        assert post["message_reference"]["message_id"] == "g1m"
        last = [m for m in r.seen[0].messages if m["role"] == "user"][-1]["content"]
        assert last == "bob: @kelobot how is the weather" and len(r.seen) == 1                     # one turn, for the call
        assert [m["content"] for m in r.seen[0].messages if m["role"] == "user"][0] == "bob: chatting about lunch"
        names = {t["function"]["name"] for t in r.seen[0].tools}
        assert names <= {"web_search", "web_fetch", "weather", "find_tools"} and "read_file" not in names
        assert len(r.store.messages()) == before and r.mgr.state("discord")["group_log"]["c1"][-1]["bot"] is True
        # a mention in a server the owner does not own: nothing happens
        r.gw.push("MESSAGE_CREATE", guild_msg(f"<@{BOT}> hello?", cid="c2", gid="g2", mid="s1"))
        await asyncio.sleep(0.2)
        assert r.rest.sent("c2") == [] and len(r.seen) == 1


async def _noop(args):
    return "ok"


async def test_button_interaction_answers_the_approval(rig):
    ran = []

    async def handler(args):
        ran.append(args)
        return "done"
    send = Tool("send", "send", {"type": "object"}, handler, requires_approval=True)
    steps = ([ToolCall("c1", "send", '{"to":"bob"}'), Done("tool_calls")], [TextDelta("It is done."), Done("stop")])
    async with rig(*steps, tools=[send]) as r:
        await r.pair()
        r.gw.push("MESSAGE_CREATE", dm("send it to bob", mid="p2"))
        card = await until(lambda: next((b for b in r.rest.of("POST", r"/channels/dm1/messages") if b.get("components")), None))
        row = card["components"][0]["components"]
        assert [(c["label"], c["style"]) for c in row] == [("Allow", 1), ("Deny", 4)]
        assert all(len(c["custom_id"]) <= 100 for c in row)
        mid = str(r.rest.next_id - 0)       # the card is the newest message so far
        # a stranger's tap does nothing
        r.gw.push("INTERACTION_CREATE", {"id": "i0", "token": "t0", "type": 3, "channel_id": "dm1", "message": {"id": mid},
                                         "user": {"id": "u9"}, "data": {"custom_id": row[0]["custom_id"]}})
        await until(lambda: r.rest.of("POST", r"/interactions/i0/t0/callback"))
        assert ran == []
        r.gw.push("INTERACTION_CREATE", {"id": "i1", "token": "t1", "type": 3, "channel_id": "dm1", "message": {"id": mid},
                                         "member": {"user": {"id": "u1"}}, "data": {"custom_id": row[0]["custom_id"]}})
        edit = await until(lambda: next(iter(r.rest.of("PATCH", r"/channels/dm1/messages/.*")), None))
        assert edit["content"].endswith("✅ Allowed") and edit["components"] == []
        await until(lambda: "It is done." in r.rest.sent("dm1"))
        assert ran == [{"to": "bob"}] and r.rest.of("POST", r"/interactions/i1/t1/callback") == [{"type": 6}]


def stopped(r):
    return [n for n in r.activity.notifications()["items"] if n["type"] == "channel_stopped"]


async def test_4014_notifies_once_and_stops(rig):
    async with rig() as r:
        await r.connect()
        r.gw.sockets[-1].drop(4014)
        await until(lambda: stopped(r))
        await asyncio.sleep(0.1)
        notes = stopped(r)
        assert len(notes) == 1 and notes[0]["title"] == "Discord stopped"
        assert notes[0]["body"] == "Discord refused: turn on Message Content Intent for the bot in the Discord developer portal."
        assert not r.mgr.status("discord")["connected"]


async def test_4004_stops_like_an_auth_error(rig):
    async with rig() as r:
        await r.connect()
        r.gw.sockets[-1].drop(4004)
        await until(lambda: stopped(r))
        await asyncio.sleep(0.1)
        notes = stopped(r)
        assert len(notes) == 1 and notes[0]["body"] == "Discord stopped: the bot token no longer works. Connect it again."
        assert not r.mgr.status("discord")["connected"]
