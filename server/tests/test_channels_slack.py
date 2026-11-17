"""Slack as a way into the owner's agent (channels/slack.py). A fake Socket Mode feeds JSON frames and a fake REST answers;
nothing touches the network."""

import asyncio
import json
import re
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from assistant.channels import Button, Callback, ChannelAuthError, ChannelRefused, ChannelRetry, Incoming, Membership, Slack
from assistant.channels.base import IncomingFile
from assistant.channels.markup import slack_mrkdwn
from assistant.channels.slack import bundle
from assistant.keyconnect import KeyError_, KeyConnections, mint
from assistant.netguard import NetGuardError, Reply
from assistant.presets import KEYS
from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app

from test_channels_telegram import until
from test_extract import CONTENT
from test_server import AUTH, TOKEN, Running

SECRET = json.dumps({"mark": "slack-v1", "user": "xoxp-1-aaaa", "bot": "xoxb-1-bbbb", "app": "xapp-1-cccc"})
BOT_ID = "U_BOT"
BOT_NAME = "kelobot"


def j(obj, status=200):
    return Reply(status, {"content-type": "application/json"}, json.dumps(obj).encode() if obj is not None else b"", False)


class FakeREST:
    """Stands in for netguard.fetch for Slack Web API and file downloads."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.headers: list[dict] = []
        self.script: dict[str, list] = {}
        self.files: dict[str, bytes] = {}
        self.next_ts = 1700000000.000100

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None, timeout=None, max_body=None):
        parts = urlsplit(url)
        assert parts.hostname in allowed_hosts and parts.hostname in ("slack.com", "files.slack.com")
        self.headers.append(dict(headers or {}))
        if parts.hostname == "files.slack.com":
            self.calls.append((method, url, None))
            body = self.files.get(url, b"file-bytes")
            return Reply(200, {}, body, False)
        api_method = parts.path[len("/api/"):]
        assert parts.path.startswith("/api/")
        body = json.loads(content) if content else {}
        self.calls.append((method, api_method, body))
        if api_method in self.script and self.script[api_method]:
            r = self.script[api_method].pop(0)
            if isinstance(r, Exception):
                raise r
            return j(r[1], r[0]) if isinstance(r, tuple) else j(r)
        if api_method == "auth.test":
            return j({"ok": True, "user_id": BOT_ID, "user": BOT_NAME})
        if api_method == "apps.connections.open":
            return j({"ok": True, "url": "wss://wss-primary.slack.com/link"})
        if api_method == "conversations.info":
            return j({"ok": True, "channel": {"name": "general"}})
        if api_method == "chat.postMessage":
            self.next_ts += 1.0
            return j({"ok": True, "ts": f"{self.next_ts:.6f}"})
        if api_method == "chat.update":
            return j({"ok": True, "ts": body.get("ts", "1700000000.000100")})
        if api_method == "reactions.add":
            return j({"ok": True})
        return j({"ok": True})

    def sent(self, channel=None):
        return [b["text"] for m, p, b in self.calls if p == "chat.postMessage" and (channel is None or b.get("channel") == channel)]

    def of(self, api_method):
        return [b for m, p, b in self.calls if p == api_method]


class FakeWS:
    def __init__(self, mgr, url, kw):
        self.mgr, self.url, self.kw = mgr, url, kw
        self.q: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.q.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        self.mgr.sent.append(frame)

    def push(self, frame):
        self.q.put_nowait(json.dumps(frame) if isinstance(frame, dict) else frame)

    def close(self):
        self.q.put_nowait(ConnectionClosed(None, None))


class FakeSocketManager:
    def __init__(self):
        self.sockets: list[FakeWS] = []
        self.sent: list[dict] = []
        self.refuse: Exception | None = None
        self.backlog: list[dict] = []

    def connect(self, url, **kw):
        if self.refuse is not None:
            raise self.refuse
        ws = FakeWS(self, url, kw)
        self.sockets.append(ws)
        for frame in self.backlog:
            ws.push(frame)
        self.backlog.clear()
        return ws

    def push(self, frame):
        live = [w for w in self.sockets if not w.closed]
        if live:
            live[-1].push(frame)
        else:
            self.backlog.append(frame)


async def fast(_s):
    await asyncio.sleep(0.005)


def make(sm=None, rest=None, secret=SECRET, **kw):
    rest = rest or FakeREST()
    sm = sm or FakeSocketManager()
    return Slack(lambda: secret, fetch=rest.fetch, connect=sm.connect, sleep=fast, **kw), rest, sm


class Feed:
    def __init__(self, sl):
        self.sl, self.items, self.exc = sl, [], None
        self.task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self):
        try:
            async for item in self.sl.receive():
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


# ---- 1. bundle and carries_chat
def test_bundle_and_carries_chat():
    assert bundle("xoxp-1234") is None
    assert bundle("") is None
    assert bundle('{"other": 1}') is None
    b = bundle(SECRET)
    assert b == {"user": "xoxp-1-aaaa", "bot": "xoxb-1-bbbb", "app": "xapp-1-cccc"}
    assert Slack.carries_chat(SECRET) is True
    assert Slack.carries_chat("xoxp-1234") is False
    assert Slack.carries_chat(json.dumps({"mark": "slack-v1", "user": "xoxp-1", "bot": "", "app": "xapp-1"})) is False
    assert Slack.carries_chat(json.dumps({"mark": "slack-v1", "user": "xoxp-1", "bot": "xoxb-1", "app": ""})) is False


# ---- 2. me()
async def test_me_calls_auth_test_with_bot_token():
    sl, rest, sm = make()
    info = await sl.me()
    assert info == {"id": BOT_ID, "username": BOT_NAME, "name": BOT_NAME}
    assert sl.me_id == BOT_ID and sl.me_name == BOT_NAME
    assert rest.calls[0] == ("POST", "auth.test", {})
    assert rest.headers[0].get("Authorization") == "Bearer xoxb-1-bbbb"


# ---- 3. receive socket url validation and token
async def test_receive_socket_url_validation_and_token():
    sl, rest, sm = make()
    rest.script["apps.connections.open"] = [{"ok": True, "url": "wss://evil.example/x"}]
    with pytest.raises(ChannelRetry) as exc:
        async for _ in sl.receive():
            break
    assert exc.value.after == 30
    assert len(sm.sockets) == 0
    # apps.connections.open was called with app token
    call = next(c for c in rest.calls if c[1] == "apps.connections.open")
    idx = rest.calls.index(call)
    assert rest.headers[idx].get("Authorization") == "Bearer xapp-1-cccc"


# ---- 4. envelopes are acked BEFORE item is yielded
async def test_envelopes_are_acked_before_item_yielded():
    sl, rest, sm = make()
    await sl.me()
    frame = {
        "type": "events_api",
        "envelope_id": "env-ack-1",
        "payload": {
            "event": {"type": "message", "channel": "D0123", "user": "U123", "ts": "1700000000.000100", "text": "hello", "channel_type": "im"}
        },
    }
    sm.backlog.append(frame)
    async with Feed(sl) as feed:
        items = await feed.got(1)
        assert len(items) == 1
        assert {"envelope_id": "env-ack-1"} in sm.sent


# ---- 5. events become items
async def test_events_become_items():
    sl, rest, sm = make()
    await sl.me()
    frames = [
        # a DM message
        {"type": "events_api", "envelope_id": "e1", "payload": {"event": {
            "type": "message", "channel": "D0100", "user": "U100", "ts": "1700000000.000100", "text": "dm text", "channel_type": "im"
        }}},
        # a channel message with mention
        {"type": "events_api", "envelope_id": "e2", "payload": {"event": {
            "type": "message", "channel": "C0200", "user": "U200", "ts": "1700000000.000200", "text": f"<@{BOT_ID}> hello bot"
        }}},
        # a thread reply to bot
        {"type": "events_api", "envelope_id": "e3", "payload": {"event": {
            "type": "message", "channel": "C0200", "user": "U200", "ts": "1700000000.000300", "text": "reply in thread",
            "thread_ts": "1700000000.000200", "parent_user_id": BOT_ID
        }}},
        # a bot_id message is kept; message_changed is ignored
        {"type": "events_api", "envelope_id": "e4", "payload": {"event": {
            "type": "message", "channel": "C0200", "user": "U300", "ts": "1700000000.000400", "text": "bot msg", "bot_id": "B999"
        }}},
        {"type": "events_api", "envelope_id": "e5", "payload": {"event": {
            "type": "message", "channel": "C0200", "user": "U300", "ts": "1700000000.000500", "subtype": "message_changed"
        }}},
        # member_joined_channel for bot
        {"type": "events_api", "envelope_id": "e6", "payload": {"event": {
            "type": "member_joined_channel", "channel": "C0200", "user": BOT_ID, "inviter": "U1"
        }}},
        # channel_left
        {"type": "events_api", "envelope_id": "e7", "payload": {"event": {
            "type": "channel_left", "channel": "C0200"
        }}},
        # interactive block_actions
        {"type": "interactive", "envelope_id": "e8", "payload": {
            "type": "block_actions", "channel": {"id": "C0200"}, "user": {"id": "U200"},
            "message": {"ts": "1700000000.000600"}, "trigger_id": "trig-1", "actions": [{"value": "button_data_1"}]
        }},
    ]
    for f in frames:
        sm.backlog.append(f)
    async with Feed(sl) as feed:
        items = await feed.got(6)
        # 1. DM
        assert isinstance(items[0], Incoming) and items[0].private and items[0].text == "dm text"
        # 2. Channel mention
        assert isinstance(items[1], Incoming) and not items[1].private and items[1].mentioned
        assert items[1].text == f"@{BOT_NAME} hello bot" and items[1].chat_title == "#general"
        # 3. Thread reply to bot
        assert isinstance(items[2], Incoming) and items[2].reply_to_bot
        # 4. a bot's message
        assert isinstance(items[3], Incoming) and items[3].text == "bot msg"
        # 5. member_joined_channel
        assert isinstance(items[4], Membership) and items[4].joined and items[4].added_by == "U1"
        # 6. channel_left
        assert isinstance(items[5], Membership) and not items[5].joined


# ---- 6. disconnect and refused errors
async def test_disconnect_and_refused_errors():
    # link_disabled -> ChannelRefused
    sl, rest, sm = make()
    await sl.me()
    sm.backlog.append({"type": "disconnect", "reason": "link_disabled"})
    with pytest.raises(ChannelRefused):
        async for _ in sl.receive():
            break

    # refresh_requested -> new connections.open
    sl2, rest2, sm2 = make()
    await sl2.me()
    sm2.backlog.append({"type": "disconnect", "reason": "refresh_requested"})
    async with Feed(sl2) as feed:
        await until(lambda: len(sm2.sockets) >= 2)
        sm2.push({"type": "events_api", "envelope_id": "e99", "payload": {"event": {
            "type": "message", "channel": "D0100", "user": "U100", "ts": "1700000000.000100", "text": "after refresh", "channel_type": "im"
        }}})
        got = await feed.got(1)
        assert got[0].text == "after refresh"
        assert len([c for c in rest2.calls if c[1] == "apps.connections.open"]) >= 2

    # not_allowed_token_type -> ChannelRefused
    sl3, rest3, sm3 = make()
    rest3.script["apps.connections.open"] = [{"ok": False, "error": "not_allowed_token_type"}]
    with pytest.raises(ChannelRefused):
        async for _ in sl3.receive():
            break

    # invalid_auth -> ChannelAuthError
    sl4, rest4, sm4 = make()
    rest4.script["apps.connections.open"] = [{"ok": False, "error": "invalid_auth"}]
    with pytest.raises(ChannelAuthError):
        async for _ in sl4.receive():
            break


# ---- 7. send()
async def test_send_split_buttons_and_threads():
    sl, rest, sm = make()
    btns = [[Button("Allow", "act:1"), Button("Deny", "act:0")]]

    # 1. Text over LIMIT is split and buttons are only on the last
    long_text = ("Hello world! " * 300) + "\n\n" + ("Second section. " * 300)
    await sl.send("C0123", long_text, buttons=btns)
    post_calls = [b for m, p, b in rest.calls if p == "chat.postMessage" and b["channel"] == "C0123"]
    assert len(post_calls) >= 2
    assert "blocks" not in post_calls[0]
    last_call = post_calls[-1]
    assert "blocks" in last_call
    actions_block = next(b for b in last_call["blocks"] if b["type"] == "actions")
    danger_btn = next(e for e in actions_block["elements"] if e["value"] == "act:0")
    assert danger_btn["style"] == "danger"

    # 2. In DM ("D..."), no thread_ts
    await sl.send("D0999", "Hello in DM", reply_to="1700000000.000100")
    dm_call = [b for m, p, b in rest.calls if p == "chat.postMessage" and b["channel"] == "D0999"][-1]
    assert "thread_ts" not in dm_call

    # 3. In channel, reply_to gives thread_ts of message
    sl._threads.put("1700000000.000100", "1700000000.000050")
    await sl.send("C0123", "Reply in thread", reply_to="1700000000.000100")
    chan_call = [b for m, p, b in rest.calls if p == "chat.postMessage" and b["channel"] == "C0123"][-1]
    assert chan_call["thread_ts"] == "1700000000.000050"

    # 4. Markdown **x** -> *x*
    await sl.send("C0123", "This is **bold** text")
    bold_call = [b for m, p, b in rest.calls if p == "chat.postMessage" and b["channel"] == "C0123"][-1]
    assert bold_call["text"] == "This is *bold* text"


# ---- 8. edit, clear_buttons, react
async def test_edit_clear_buttons_and_react():
    sl, rest, sm = make()
    # edit
    await sl.edit("C0123", "1700000000.000100", "Updated text")
    edit_call = next(b for m, p, b in rest.calls if p == "chat.update")
    assert edit_call == {"channel": "C0123", "ts": "1700000000.000100", "text": "Updated text", "blocks": []}

    # clear_buttons after send with buttons
    btns = [[Button("Click", "val")]]
    ts = await sl.send("C0123", "Buttons text", buttons=btns)
    await sl.clear_buttons("C0123", ts)
    clear_call = [b for m, p, b in rest.calls if p == "chat.update"][-1]
    assert clear_call["ts"] == ts and clear_call["blocks"] == []

    # clear_buttons for unknown ts does nothing
    calls_before = len(rest.calls)
    await sl.clear_buttons("C0123", "1700000099.999999")
    assert len(rest.calls) == calls_before

    # react with 👍 -> +1
    await sl.react("C0123", "1700000000.000100", "👍")
    react_call = next(b for m, p, b in rest.calls if p == "reactions.add")
    assert react_call == {"channel": "C0123", "timestamp": "1700000000.000100", "name": "+1"}

    # react with unknown emoji sends nothing
    calls_before = len(rest.calls)
    await sl.react("C0123", "1700000000.000100", "👽")
    assert len(rest.calls) == calls_before


# ---- 9. download
async def test_download_host_check_and_token():
    sl, rest, sm = make()
    rest.files["https://files.slack.com/files-pri/T01-F01/download.png"] = b"png-bytes"

    # allowed host
    chunks = []
    f = IncomingFile("https://files.slack.com/files-pri/T01-F01/download.png", "slack-f.png", 9)
    async for chunk in sl.download(f):
        chunks.append(chunk)
    assert b"".join(chunks) == b"png-bytes"
    dl_header = rest.headers[-1]
    assert dl_header.get("Authorization") == "Bearer xoxb-1-bbbb"

    # evil host -> ValueError
    bad_f = IncomingFile("https://evil.example.com/file.png", "evil.png", 8)
    with pytest.raises(ValueError, match="Slack gave no file"):
        async for _ in sl.download(bad_f):
            pass


# ---- 10. slack_mrkdwn
def test_slack_mrkdwn_formatting():
    # bold, link, heading, escaping of < > &, code spans and fences
    md = (
        "# Main Heading\n"
        "Here is **bold** text and [Kelo](https://example.com/chat) link.\n"
        "Escaping: 5 < 10 & 10 > 5.\n"
        "Inline `a < b & c > d` code.\n"
        "~~strike~~\n"
        "```python\n"
        "if x < y and y > z:\n"
        "    print('code fence')\n"
        "```\n"
    )
    res = slack_mrkdwn(md)
    lines = res.split("\n")
    assert lines[0] == "*Main Heading*"
    assert "*bold*" in lines[1] and "<https://example.com/chat|Kelo>" in lines[1]
    assert "5 &lt; 10 &amp; 10 &gt; 5." in lines[2]
    assert "`a < b & c > d`" in lines[3]  # inside backticks untouched
    assert "~strike~" in lines[4]
    assert lines[5] == "```python"
    assert lines[6] == "if x < y and y > z:"  # inside fence untouched
    assert lines[7] == "    print('code fence')"
    assert lines[8] == "```"


# ---- 11. keyconnect
async def test_keyconnect_slack():
    rest = FakeREST()
    store = type("DummyStore", (), {
        "credential": lambda self, item: None,
        "delete_credential": lambda self, item: None,
        "save_credential": lambda self, *args, **kw: None,
        "set_credential_secret": lambda self, item, secret: None,
        "set_credential_result": lambda self, item, res, msg: None,
        "add_vault_log": lambda self, *args, **kw: None,
    })()
    kc = KeyConnections(store, fetcher=rest.fetch)

    # 1. Connecting with three values saves bundle and verifies with USER token
    saved = {}
    store.set_credential_secret = lambda item, sec: saved.update(secret=sec)
    res = await kc.connect("slack", {"token": "xoxp-1-aaaa", "bot": "xoxb-1-bbbb", "app": "xapp-1-cccc"})
    assert res["item"] == "slack-api" and res["checked"] is True
    b = json.loads(saved["secret"])
    assert b["mark"] == "slack-v1" and b["user"] == "xoxp-1-aaaa" and b["bot"] == "xoxb-1-bbbb" and b["app"] == "xapp-1-cccc"
    # verify was auth.test with USER token
    auth_calls = [idx for idx, c in enumerate(rest.calls) if c[1] == "auth.test"]
    assert len(auth_calls) >= 1
    assert rest.headers[auth_calls[0]]["Authorization"] == "Bearer xoxp-1-aaaa"

    # 2. Wrong prefix refused
    with pytest.raises(KeyError_, match="Paste the three Slack tokens"):
        await kc.connect("slack", {"token": "wrong-token", "bot": "xoxb-1-bbbb", "app": "xapp-1-cccc"})

    # 3. mint() returns user token without fetch
    kc_fetch_count = len(rest.calls)
    tok = await mint(saved["secret"], rest.fetch)
    assert tok == "xoxp-1-aaaa"
    assert len(rest.calls) == kc_fetch_count


# ---- 12. Server level with the rig
class SRig:
    def __init__(self, tmp_path, *steps, tools=None, model=None, split=None):
        self.rest = FakeREST()
        self.sm = FakeSocketManager()
        self.seen: list = []
        self.steps = steps or ([TextDelta("Hello from Slack Kelo"), Done("stop")],)

        async def model_steps(prov, request, **kw):
            self.seen.append(request)
            for ev in self.steps[min(len(self.seen) - 1, len(self.steps) - 1)]:
                yield ev

        from split_pay import app_args   # `model`: a role-aware fake (split_pay.Split); `split`: payment_specialist on/off (None = default)
        settings_extra, app_extra = app_args(split)
        app = create_app(
            Settings(
                token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                home_dir=str(tmp_path / "home"), content_dir=str(CONTENT), **settings_extra
            ),
            tools=tools if tools is not None else [],
            stream_fn=model or model_steps,
            mcp_fetcher=self.rest.fetch,
            channel_fetch=self.rest.fetch,
            channel_connect=self.sm.connect,
            **app_extra,
        )
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

    def code(self):
        for m in reversed(self.store.messages()):
            if m["role"] == "assistant" and (hit := re.search(r"Send (\d{6}) to the bot", m["content"])):
                return hit.group(1)

    def push_dm(self, text, uid="U01", cid="D01", ts="1700000001.000100"):
        frame = {
            "type": "events_api",
            "envelope_id": f"env-{ts}",
            "payload": {
                "event": {
                    "type": "message",
                    "channel": cid,
                    "user": uid,
                    "ts": ts,
                    "text": text,
                    "channel_type": "im",
                }
            },
        }
        self.sm.push(frame)


@pytest.fixture
def srig(tmp_path):
    def build(*steps, **kw):
        return SRig(tmp_path, *steps, **kw)
    return build


async def test_server_slack_channel_pairing_turn_and_legacy_secret(srig):
    async with srig() as r:
        # 1. Plain old token starts nothing and sends no notification
        r.store.save_credential("slack-api", "Slack", ["slack.com"], {"type": "bearer_header"}, kind="api")
        r.store.set_credential_secret("slack-api", "xoxp-old-legacy-token")
        await asyncio.sleep(0.1)
        assert not r.mgr.status("slack")["connected"]
        stopped_notes = [n for n in r.activity.notifications()["items"] if n["type"] == "channel_stopped"]
        assert len(stopped_notes) == 0

        # 2. Saving SECRET starts the Slack channel and posts a pairing code
        r.store.set_credential_secret("slack-api", SECRET)
        await until(lambda: r.mgr.status("slack")["connected"] and r.mgr.state("slack")["pairing"])
        pairing_code = r.code()
        assert pairing_code is not None

        # 3. DM with code pairs it
        r.push_dm(pairing_code, uid="U01", cid="D01", ts="1700000001.000100")
        await until(lambda: r.mgr.state("slack")["paired"])
        assert r.mgr.state("slack")["paired"]["user_id"] == "U01"
        assert r.mgr.state("slack")["paired"]["chat_id"] == "D01"
        assert "Connected. You can talk to Kelo here." in r.rest.sent("D01")

        # 4. Following DM starts a chat turn whose reply goes back with chat.postMessage
        r.push_dm("what's up", uid="U01", cid="D01", ts="1700000002.000200")
        await until(lambda: "Hello from Slack Kelo" in r.rest.sent("D01"))
        assert any(b.get("channel") == "D01" and "Hello from Slack Kelo" in b.get("text", "")
                   for b in r.rest.of("chat.postMessage"))


async def test_a_card_a_checkout_job_needs_the_app_for_comes_to_the_chat_that_started_it(srig):
    """A checkout job outlives the reply that handed it over; its approval card is sent to the chat (no buttons: only the app answers it)."""
    from split_pay import Split, job
    from test_channels_telegram import send_tool
    ran = []
    async with srig(tools=[send_tool(ran, "request_card")], model=Split(job(("request_card", {})), closing="It is done."), split=True) as r:
        r.store.save_credential("slack-api", "Slack", ["slack.com"], {"type": "bearer_header"}, kind="api")
        r.store.set_credential_secret("slack-api", SECRET)
        await until(lambda: r.mgr.status("slack")["connected"] and r.mgr.state("slack")["pairing"])
        r.push_dm(r.code(), uid="U01", cid="D01", ts="1700000001.000100")
        await until(lambda: r.mgr.state("slack")["paired"])
        r.push_dm("go", uid="U01", cid="D01", ts="1700000002.000200")
        await until(lambda: any("Open the Kelo app to answer this one." in s for s in r.rest.sent("D01")))
        assert not any("blocks" in b and any(x["type"] == "actions" for x in b["blocks"]) for b in r.rest.of("chat.postMessage"))
        aid = next(iter((await r.c.get("/api/approvals/pending")).json()["items"]))["approval_id"]
        assert (await r.c.post(f"/api/approvals/{aid}", json={"approve": False})).status_code == 200
        await until(lambda: "It is done." in r.rest.sent("D01"))
        assert ran == []
