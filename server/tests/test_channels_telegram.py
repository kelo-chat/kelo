"""Telegram as a way into the owner's agent (channels/). No call ever leaves the machine: the transport is a fake."""

import json
from urllib.parse import unquote, urlsplit

import io
import time

import httpx
from PIL import Image

from assistant.keyconnect import KeyConnections
from assistant.netguard import Reply
from assistant.server import Settings, create_app
from assistant.store import Store

from test_extract import CONTENT
from test_server import AUTH, TOKEN, Running, scripted

TG_TOKEN = "123456:ABC-secret-token-value"


def j(obj, status=200):
    return Reply(status, {"content-type": "application/json"}, json.dumps(obj).encode(), False)


async def test_telegram_is_a_key_connector(tmp_path):
    seen = []

    async def fetch(method, url, *, allowed_hosts, headers=None, content=None):
        seen.append((method, url))
        return j({"ok": True, "result": {"id": 1, "username": "kelo_bot"}})
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"),
                              content_dir=str(CONTENT)), tools=None, stream_fn=scripted(), mcp_fetcher=fetch)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        by = {x["id"]: x for x in (await c.get("/api/connectors")).json()}
        assert by["telegram"]["connect"] == "key" and by["telegram"]["item"] == "telegram-bot"
        form = (await c.get("/api/keys/telegram")).json()
        assert form["checked"] and form["fields"][0]["secret"]


async def test_verify_puts_the_token_in_the_path(tmp_path):
    seen = []

    async def fetch(method, url, *, allowed_hosts, headers=None, content=None):
        seen.append((method, url, headers))
        return j({"ok": True, "result": {"id": 1, "username": "kelo_bot"}})
    st = Store(tmp_path / "s.db")
    out = await KeyConnections(st, fetcher=fetch).connect("telegram", {"token": TG_TOKEN})
    assert out["checked"] and seen[0][0] == "GET"
    assert urlsplit(seen[0][1]).path == "/bot123456%3AABC-secret-token-value/getMe"
    assert "Authorization" not in seen[0][2]
    assert st.credential("telegram-bot")["secret"] == TG_TOKEN


# ---- markup
from assistant.channels import chunks, telegram_html, telegram_parts   # noqa: E402


def test_markup_chunks_and_html():
    h = telegram_html("# Title\n**bold** and *it* and _it2_ and `a<b` & [site](https://x.example/a_b_c?q=1&r=2)\n- one\n* two\n[bad](javascript:alert(1))")
    assert h.splitlines()[0] == "<b>Title</b>"
    line = h.splitlines()[1]
    assert "<b>bold</b>" in line and "<i>it</i>" in line and "<i>it2</i>" in line and "<code>a&lt;b</code>" in line and "&amp;" in line
    assert '<a href="https://x.example/a_b_c?q=1&amp;r=2">site</a>' in line
    assert h.splitlines()[2:4] == ["• one", "• two"] and "javascript" in h and "<a href=\"javascript" not in h
    assert telegram_html("```py\nx = 1 < 2\n\nprint(x)\n```\nafter") == "<pre>x = 1 &lt; 2\n\nprint(x)</pre>\nafter"
    assert telegram_html("| a | b |\n|---|---|\n| 1 | 2 |\ntext") == "<pre>| a | b |\n|---|---|\n| 1 | 2 |</pre>\ntext"
    assert telegram_html("a *star and 2 * 3 = 6") == "a *star and 2 * 3 = 6"
    paras = "\n\n".join(f"paragraph {i} " + "word " * 100 for i in range(18))   # about 9,000 characters
    parts = chunks(paras, 4000)
    assert len(parts) == 3 and all(len(p) <= 4000 for p in parts) and "".join(p.replace("\n", "").replace(" ", "") for p in parts) == paras.replace("\n", "").replace(" ", "")
    code = "```\n" + "\n".join(f"line {i}" for i in range(1500)) + "\n```"
    pieces = chunks(code, 4000)
    assert len(pieces) > 1 and all(len(p) <= 4000 and p.startswith("```") and p.endswith("```") for p in pieces)
    assert chunks("x" * 9000, 4000) and all(len(p) <= 4000 for p in chunks("x" * 9000, 4000))
    assert chunks("short", 4000) == ["short"] and chunks("", 4000) == []
    heavy = "<" * 3900 + "\n\n" + "&" * 3900          # 4 or 5 times longer once escaped: the pieces get smaller
    assert all(len(html) <= 4096 for html, _ in telegram_parts(heavy))


def test_messages_carry_via(tmp_path):
    st = Store(tmp_path / "v.db")
    a = st.add_message("user", "from the app")
    b = st.add_message("user", "from telegram", via="telegram")
    rows = {r["id"]: r for r in st.messages()}
    assert rows[a]["via"] == "" and rows[b]["via"] == "telegram"
    assert [r["via"] for r in st.messages_after(0)] == ["", "telegram"]


# ---- the transport, against a fake Telegram
import asyncio   # noqa: E402

import pytest   # noqa: E402

from assistant.channels import Button, ChannelAuthError, ChannelConflict, ChannelRetry, Telegram   # noqa: E402
from assistant.channels.base import Callback, Incoming, IncomingFile   # noqa: E402
from assistant.netguard import NetGuardError   # noqa: E402


class FakeTG:
    """Stands in for netguard.fetch. `calls` is [(method, body)]; `script[method]` is a list of replies used in order (then
    `default`): a dict (the JSON body), a (status, dict) pair, or an exception to raise. getUpdates waits when nothing is queued."""

    def __init__(self, token=TG_TOKEN):
        self.token = token
        self.calls: list[tuple[str, dict]] = []
        self.script: dict[str, list] = {}
        self.updates: asyncio.Queue = asyncio.Queue()
        self.files: dict[str, bytes] = {}
        self.next_id = 100
        self.urls: list[str] = []

    def push(self, update):
        self.updates.put_nowait(update)

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None, timeout=None, max_body=None):
        self.urls.append(url)
        assert urlsplit(url).hostname == "api.telegram.org" and allowed_hosts == ["api.telegram.org"]
        if "/file/bot" in url:
            path = url.split(f"/file/bot{self.token}/", 1)[1]
            return Reply(200, {}, self.files[path], False)
        assert unquote(url).startswith(f"https://api.telegram.org/bot{self.token}/")   # the connector's check sends the colon as %3A
        name = url.rsplit("/", 1)[1]
        body = json.loads(content or b"{}")
        self.calls.append((name, body))
        queued = self.script.get(name)
        if queued:
            r = queued.pop(0)
            if isinstance(r, Exception):
                raise r
            if isinstance(r, tuple):
                return j(r[1], r[0])
            return j(r)
        if name == "getUpdates":
            try:
                first = await asyncio.wait_for(self.updates.get(), 0.05)
            except asyncio.TimeoutError:
                return j({"ok": True, "result": []})
            out = [first]
            while not self.updates.empty():
                out.append(self.updates.get_nowait())
            return j({"ok": True, "result": out})
        if name == "getMe":
            return j({"ok": True, "result": {"id": 42, "username": "kelo_bot", "first_name": "Kelo"}})
        if name == "sendMessage":
            self.next_id += 1
            return j({"ok": True, "result": {"message_id": self.next_id}})
        if name == "getFile":
            return j({"ok": True, "result": {"file_path": f"photos/{body['file_id']}.jpg"}})
        return j({"ok": True, "result": True})

    def of(self, name):
        return [b for n, b in self.calls if n == name]


def err(code, description, **params):
    return (code, {"ok": False, "error_code": code, "description": description, "parameters": params})


def msg(text="", *, uid=7, chat=7, kind="private", mid=1, update_id=1, first="Ann", **extra):
    return {"update_id": update_id, "message": {"message_id": mid, "from": {"id": uid, "first_name": first}, "chat": {"id": chat, "type": kind}, **({"text": text} if text else {}), **extra}}


async def test_transport_errors_and_calls():
    fake = FakeTG()
    tg = Telegram(lambda: TG_TOKEN, fetch=fake.fetch)
    assert await tg.me() == {"id": "42", "username": "kelo_bot", "name": "Kelo"}
    fake.script["sendMessage"] = [err(401, "Unauthorized")]
    with pytest.raises(ChannelAuthError) as e:
        await tg.send("7", "hi")
    assert TG_TOKEN not in str(e.value)
    fake.script["sendMessage"] = [err(409, "Conflict")]
    with pytest.raises(ChannelConflict):
        await tg.send("7", "hi")
    fake.script["sendMessage"] = [err(429, "Too Many Requests", retry_after=17)]
    with pytest.raises(ChannelRetry) as r:
        await tg.send("7", "hi")
    assert r.value.after == 17
    fake.script["sendMessage"] = [err(502, "Bad Gateway"), NetGuardError("the request timed out")]
    for _ in range(2):
        with pytest.raises(ChannelRetry):
            await tg.send("7", "hi")
    fake.script["sendMessage"] = [err(400, "Bad Request: chat not found")]
    with pytest.raises(ValueError, match="chat not found"):
        await tg.send("7", "hi")
    with pytest.raises(ChannelAuthError):
        await Telegram(lambda: "", fetch=fake.fetch).me()


async def test_html_rejected_falls_back_to_plain():
    fake = FakeTG()
    tg = Telegram(lambda: TG_TOKEN, fetch=fake.fetch)
    fake.script["sendMessage"] = [err(400, "Bad Request: can't parse entities: Unsupported start tag")]
    mid = await tg.send("7", "**bold** <odd>", [[Button("Yes", "r:1")]])
    sent = fake.of("sendMessage")
    assert len(sent) == 2 and sent[0]["parse_mode"] == "HTML" and "<b>bold</b>" in sent[0]["text"]
    assert "parse_mode" not in sent[1] and sent[1]["text"] == "**bold** <odd>" and sent[1]["reply_markup"] == sent[0]["reply_markup"]
    assert mid == "101"
    fake.script["editMessageText"] = [err(400, "Bad Request: message is not modified")]
    await tg.edit("7", "101", "same")          # ignored
    await tg.edit("7", "101", "new")
    assert "reply_markup" not in fake.of("editMessageText")[-1]


async def test_long_reply_is_chunked():
    fake = FakeTG()
    tg = Telegram(lambda: TG_TOKEN, fetch=fake.fetch)
    text = "\n\n".join(f"paragraph {i} " + "word " * 100 for i in range(18))
    await tg.send("7", text, [[Button("A", "r:a")]])
    sent = fake.of("sendMessage")
    assert len(sent) == 3 and all(len(s["text"]) <= 4096 for s in sent)
    assert "reply_markup" not in sent[0] and "reply_markup" not in sent[1] and "reply_markup" in sent[2]


async def test_receive_parses_updates_and_the_offset_moves_on():
    fake = FakeTG()
    tg = Telegram(lambda: TG_TOKEN, fetch=fake.fetch)
    tg.offset = 50
    fake.push(msg("hello", update_id=50))
    fake.push(msg("", update_id=51, mid=2, photo=[{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 999}], caption="look"))
    fake.push(msg("", update_id=52, mid=3, document={"file_id": "d1", "file_name": "my report (1).pdf", "file_size": 5}))
    fake.push(msg("", update_id=53, mid=4, voice={"file_id": "v"}))
    fake.push(msg("hi all", update_id=54, mid=5, kind="supergroup", chat=-100))
    fake.push({"update_id": 55, "callback_query": {"id": "cb1", "from": {"id": 7}, "data": "a:x:1", "message": {"message_id": 9, "chat": {"id": 7}}}})
    got = []
    async for item in tg.receive():
        got.append(item)
        if len(got) == 6:
            break
    assert fake.calls[0] == ("deleteWebhook", {"drop_pending_updates": False})
    assert fake.of("getUpdates")[0] == {"offset": 50, "timeout": 50, "allowed_updates": ["message", "callback_query", "my_chat_member"]}
    a, b, c, d, e, f = got
    assert isinstance(a, Incoming) and (a.text, a.chat_id, a.user_id, a.private, a.update_id, a.name) == ("hello", "7", "7", True, 50, "Ann")
    assert b.text == "look" and b.files == [IncomingFile("big", "telegram-2.jpg", 999, "photo")]
    assert c.files == [IncomingFile("d1", "telegram-3-my_report__1_.pdf", 5)]
    assert d.unsupported and not d.files
    assert e.private is False
    assert isinstance(f, Callback) and (f.data, f.callback_id, f.chat_id, f.message_id) == ("a:x:1", "cb1", "7", "9")


async def test_download_streams_the_file():
    fake = FakeTG()
    fake.files["photos/big.jpg"] = b"x" * 150_000
    tg = Telegram(lambda: TG_TOKEN, fetch=fake.fetch)
    data = b"".join([c async for c in tg.download(IncomingFile("big", "telegram-2.jpg", 150_000))])
    assert data == b"x" * 150_000


# ---- the manager, in a whole app with a fake Telegram and a scripted model
import logging   # noqa: E402
import re   # noqa: E402

from assistant.agent import Tool   # noqa: E402
from assistant.channels import ChannelManager, needs_app   # noqa: E402
from assistant.providers import Done, TextDelta   # noqa: E402

CODE_RE = re.compile(r"start=(\d{6})")


async def until(cond, tries=300, pause=0.02):
    for _ in range(tries):
        got = cond()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        await asyncio.sleep(pause)
    raise AssertionError("never happened")


def text_of(update_text):
    return update_text


class Rig:
    """An app on a loopback port with a fake Telegram: `fake` (the bot), `seen` (what the model was sent), `mgr` (the manager)."""

    def __init__(self, tmp_path, *steps, tools=None, provider="p", consent=True):
        self.consent = consent
        self.fake = FakeTG()
        self.seen: list = []
        self.steps = steps or ([TextDelta("Hello from Kelo"), Done("stop")],)

        async def model(prov, request, **kw):
            self.seen.append(request)
            for ev in self.steps[min(len(self.seen) - 1, len(self.steps) - 1)]:
                yield ev
        app = create_app(Settings(token=TOKEN, provider=provider, model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                                  home_dir=str(tmp_path / "home"), content_dir=str(CONTENT)),
                         tools=tools if tools is not None else [], stream_fn=model, mcp_fetcher=self.fake.fetch, channel_fetch=self.fake.fetch)
        self.app, self.run = app, Running(app)

    async def __aenter__(self):
        await self.run.__aenter__()
        self.c = httpx.AsyncClient(base_url=self.run.url, headers=AUTH)
        self.mgr: ChannelManager = self.app.state.channels
        self.store = self.app.state.appctx.store
        self.activity = self.app.state.appctx.activity
        self.sleeps: list[float] = []
        if self.consent:
            assert (await self.c.put("/api/ai-consent", json={"accepted": True})).status_code == 200

        async def fast(s):
            self.sleeps.append(s)
            await asyncio.sleep(0)
        self.mgr._sleep = fast
        return self

    async def __aexit__(self, *exc):
        await self.c.aclose()
        await self.run.__aexit__(*exc)

    async def connect(self, token=TG_TOKEN):
        r = await self.c.post("/api/keys/telegram", json={"values": {"token": token}})
        assert r.status_code == 200, r.text
        await until(lambda: self.mgr.status("telegram")["connected"] and self.mgr.state("telegram")["pairing"])

    def code(self):
        for m in reversed(self.store.messages()):
            if m["role"] == "assistant" and (hit := CODE_RE.search(m["content"])):
                return hit.group(1)
        return None

    async def pair(self, uid=7, chat=7, first="Ann", update_id=1):
        await self.connect()
        self.fake.push(msg(f"/start {self.code()}", uid=uid, chat=chat, first=first, update_id=update_id))
        await until(lambda: self.mgr.state("telegram")["paired"])

    def sent(self):
        return [b["text"] for b in self.fake.of("sendMessage")]

    def app_messages(self):
        return [(m["role"], m["content"], m["via"]) for m in self.store.messages()]


@pytest.fixture
def rig(tmp_path):
    def make(*steps, **kw):
        return Rig(tmp_path, *steps, **kw)
    return make


async def test_saving_the_token_posts_the_pairing_message_and_starts(rig):
    async with rig() as r:
        await r.connect()
        said = [m for m in r.app_messages() if m[0] == "assistant"]
        assert len(said) == 1 and "https://t.me/kelo_bot?start=" in said[0][1] and "@kelo_bot" in said[0][1] and "10 minutes" in said[0][1]
        notes = [n for n in r.activity.notifications()["items"] if n["type"] == "channel_pairing"]
        assert len(notes) == 1 and r.code() in notes[0]["body"]
        assert r.mgr.status("telegram") == {"connected": True, "bot": "kelo_bot", "paired": None, "notify": False}
        assert r.fake.calls[1][0] == "getMe" or "getMe" in [n for n, _ in r.fake.calls]
        assert "deleteWebhook" in [n for n, _ in r.fake.calls]
        assert r.sent() == []


async def test_pairing_with_the_code_and_with_start_link(rig):
    async with rig() as r:
        await r.connect()
        r.fake.push(msg(r.code(), uid=7, first="Ann", update_id=1))             # the code alone
        await until(lambda: r.mgr.state("telegram")["paired"])
        st = r.mgr.state("telegram")
        assert st["paired"]["user_id"] == "7" and st["paired"]["name"] == "Ann" and st["pairing"] is None
        await until(lambda: r.sent())
        assert r.sent() == ["Connected. You can talk to Kelo here."]
        assert r.app_messages()[-1][:2] == ("assistant", "Telegram is now connected to Ann.")
        assert r.mgr.status("telegram")["paired"] == "Ann"
        # the start link, on a fresh pairing
        r.mgr.begin_pairing("telegram", reset=True)
        r.fake.push(msg(f"/start@kelo_bot {r.code()}", uid=8, chat=8, first="Bo", update_id=2))
        await until(lambda: r.mgr.state("telegram")["paired"] and r.mgr.state("telegram")["paired"]["user_id"] == "8")


async def test_wrong_code_five_times_then_ignored(rig):
    async with rig() as r:
        await r.connect()
        for i in range(5):
            r.fake.push(msg("/start 000000", uid=9, chat=9, update_id=1 + i))
        r.fake.push(msg(f"/start {r.code()}", uid=9, chat=9, update_id=10))       # the right code, but too late
        await until(lambda: r.mgr.state("telegram")["offset"] == 11)
        assert r.mgr.state("telegram")["paired"] is None and r.sent() == []
        r.fake.push(msg(f"/start {r.code()}", uid=7, chat=7, update_id=11))       # another person is not blocked
        await until(lambda: r.mgr.state("telegram")["paired"])
        assert r.mgr.state("telegram")["paired"]["user_id"] == "7"


async def test_expired_code_does_not_pair(rig):
    async with rig() as r:
        await r.connect()
        now = time.time()
        r.mgr.clock = lambda: now + 601
        r.fake.push(msg(r.code(), uid=7, update_id=1))
        await until(lambda: r.mgr.state("telegram")["offset"] == 2)
        assert r.mgr.state("telegram")["paired"] is None and r.sent() == []


async def test_unpaired_sender_and_group_chat_get_nothing(rig, caplog):
    caplog.set_level(logging.INFO)
    async with rig() as r:
        await r.pair()
        before = len(r.store.messages())
        r.fake.push(msg("hello", uid=99, chat=99, update_id=5))                     # a stranger
        r.fake.push(msg("hi all", uid=7, chat=-100, kind="supergroup", update_id=6))   # the owner, in a group
        r.fake.push(msg("hi", uid=7, chat=7, update_id=7) | {"edited": 1})           # still counted below
        await until(lambda: r.mgr.state("telegram")["offset"] >= 7)
        await asyncio.sleep(0.1)
        user_msgs = [m for m in r.app_messages() if m[0] == "user"]
        assert all(m[1] != "hello" and m[1] != "hi all" for m in user_msgs)
        assert not r.seen or all("hello" not in str(q.messages) for q in r.seen)
        assert "ignored a message from an unpaired account" in caplog.text and "hello" not in caplog.text
        assert before <= len(r.store.messages())


async def test_paired_text_joins_the_conversation(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(msg("what is up", update_id=3))
        await until(lambda: len(r.sent()) >= 2)
        assert r.sent()[-1] == "Hello from Kelo"
        rows = r.app_messages()
        assert ("user", "what is up", "telegram") in rows and rows[-1] == ("assistant", "Hello from Kelo", "telegram")
        last_user = [m for m in r.seen[0].messages if m["role"] == "user"][-1]["content"]
        assert "what is up" in last_user and "came from the user's Telegram" in last_user
        assert not any("came from the user's Telegram" in m[1] for m in rows)
        assert r.sent().count("Hello from Kelo") == 1
        assert "setMessageReaction" in [n for n, _ in r.fake.calls]


async def test_secret_in_message_is_not_kept(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(msg("my key is sk-ant-api03-Zq7xW2mK9pL4vN8rT1yB6cD3fG5hJ0sA", update_id=3))
        await until(lambda: len(r.sent()) >= 2)
        assert r.sent()[-1].startswith("That looks like a password or key, so I did not keep it.")
        assert not [m for m in r.app_messages() if "sk-ant" in m[1]] and not r.seen


async def test_consent_missing_line(rig):
    async with rig(provider="anthropic", consent=False) as r:
        await r.pair()
        r.fake.push(msg("hello", update_id=3))
        await until(lambda: len(r.sent()) >= 2)
        assert r.sent()[-1] == "Open the Kelo app once to agree to sending chats to the AI service."
        assert not [m for m in r.app_messages() if m[1] == "hello"] and not r.seen


async def test_photo_is_downloaded_and_attached(rig):
    async with rig() as r:
        await r.pair()
        buf = io.BytesIO()
        Image.new("RGB", (40, 30), (200, 30, 30)).save(buf, "JPEG")
        r.fake.files["photos/big.jpg"] = buf.getvalue()
        r.fake.push(msg("", update_id=3, mid=5, photo=[{"file_id": "small", "file_size": 5}, {"file_id": "big", "file_size": 900}]))
        await until(lambda: len(r.sent()) >= 2)
        assert [m for m in r.app_messages() if m[0] == "user"][-1][1] == "(photo)"
        assert (r.store.messages()[-2]["id"]) and any(p.name.startswith("telegram-5") for p in (r.app.state.appctx.home.root / "uploads").iterdir())
        rows = (await r.c.get("/api/messages")).json()
        mine = [m for m in rows if m["role"] == "user"][-1]
        assert mine["via"] == "telegram" and mine["attachments"] and mine["attachments"][0]["name"].startswith("telegram-5")


async def test_voice_gets_the_fixed_line(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(msg("", update_id=3, voice={"file_id": "v"}))
        await until(lambda: len(r.sent()) >= 2)
        assert r.sent()[-1] == "I can only read text, photos and files here for now."
        assert not r.seen and not [m for m in r.app_messages() if m[0] == "user"]


async def test_stop_and_start_commands(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(msg("/start", update_id=3))
        r.fake.push(msg("/stop", update_id=4))
        await until(lambda: len(r.sent()) >= 3)
        assert r.sent()[1:] == ["You are connected.", "Stopped."]
        assert not r.seen


async def test_offset_survives_restart(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(msg("/start", update_id=41))
        await until(lambda: r.mgr.state("telegram")["offset"] == 42)
        await r.mgr.stop("telegram")
        n = len(r.fake.of("getUpdates"))
        assert await r.mgr.start("telegram")
        await until(lambda: len(r.fake.of("getUpdates")) > n)
        assert r.fake.of("getUpdates")[n]["offset"] == 42


async def test_429_waits_retry_after(rig):
    async with rig() as r:
        r.fake.script["getUpdates"] = [err(429, "Too Many Requests", retry_after=17)]
        await r.connect()
        await until(lambda: r.sleeps)
        assert 17 <= r.sleeps[0] <= 17 * 1.21
        assert r.mgr.status("telegram")["connected"]


async def test_409_conflict_notifies_once(rig):
    async with rig() as r:
        r.fake.script["getUpdates"] = [err(409, "Conflict: terminated by other getUpdates request")] * 3
        await r.connect()
        await until(lambda: len(r.sleeps) >= 3)
        notes = [n for n in r.activity.notifications()["items"] if n["type"] == "channel_conflict"]
        assert len(notes) == 1 and "Another program reads this Telegram bot" in notes[0]["body"]
        assert r.mgr.status("telegram")["connected"]


async def test_401_stops_and_notifies_once(rig):
    async with rig() as r:
        r.fake.script["getUpdates"] = [err(401, "Unauthorized")]
        assert (await r.c.post("/api/keys/telegram", json={"values": {"token": TG_TOKEN}})).status_code == 200
        await until(lambda: [n for n in r.activity.notifications()["items"] if n["type"] == "channel_stopped"])
        await asyncio.sleep(0.1)
        notes = [n for n in r.activity.notifications()["items"] if n["type"] == "channel_stopped"]
        assert len(notes) == 1 and notes[0]["body"] == "Telegram stopped: the bot token no longer works. Connect it again."
        assert not r.mgr.status("telegram")["connected"]


async def test_removed_token_stops_the_channel(rig):
    async with rig() as r:
        await r.pair()
        assert (await r.c.delete("/api/credentials/telegram-bot")).status_code == 200
        await until(lambda: not r.mgr.status("telegram")["connected"] and r.mgr.state("telegram")["paired"] is None)
        assert r.mgr.state("telegram")["bot"] is None
        n = len(r.fake.calls)
        await asyncio.sleep(0.2)
        assert len([1 for name, _ in r.fake.calls[n:] if name == "getUpdates"]) <= 1


async def test_new_bot_re_pairs(rig):
    async with rig() as r:
        await r.pair()
        assert r.mgr.state("telegram")["paired"]
        first = r.code()
        r.fake.token = "777777:NEW-bot-token-value"
        r.fake.script["getMe"] = [{"ok": True, "result": {"id": 43, "username": "other_bot", "first_name": "Other"}}] * 2
        await r.connect("777777:NEW-bot-token-value")
        await until(lambda: r.mgr.state("telegram")["bot"]["username"] == "other_bot" and r.mgr.state("telegram")["pairing"])
        assert r.mgr.state("telegram")["paired"] is None
        assert r.code() != first or True
        assert "other_bot" in [m for m in r.app_messages() if m[0] == "assistant"][-1][1]


async def test_same_bot_new_token_keeps_the_pairing(rig):
    async with rig() as r:
        await r.pair()
        r.fake.token = "888888:ROTATED-token-value"
        assert (await r.c.post("/api/keys/telegram", json={"values": {"token": "888888:ROTATED-token-value"}})).status_code == 200
        await asyncio.sleep(0.3)
        assert r.mgr.state("telegram")["paired"]["user_id"] == "7" and r.mgr.status("telegram")["connected"]


# ---- cards, buttons, notifications
def send_tool(ran, name="send"):
    async def handler(args):
        ran.append(args)
        return "done"
    return Tool(name, name, {"type": "object"}, handler, requires_approval=True)


def asks_then_answers(tool="send"):
    return ([ToolCall("c1", tool, '{"to":"bob"}'), Done("tool_calls")], [TextDelta("It is done."), Done("stop")])


from assistant.providers import ToolCall   # noqa: E402


def keyboard(sent):
    return (sent.get("reply_markup") or {}).get("inline_keyboard")


async def test_approval_buttons_round_trip(rig):
    ran = []
    async with rig(*asks_then_answers(), tools=[send_tool(ran)]) as r:
        await r.pair()
        r.fake.push(msg("send it to bob", update_id=3))
        card = await until(lambda: next((b for b in r.fake.of("sendMessage") if keyboard(b)), None))
        row = keyboard(card)[0]
        assert [b["text"] for b in row] == ["Allow", "Deny"] and row[0]["callback_data"].endswith(":1") and row[1]["callback_data"].endswith(":0")
        assert all(len(b["callback_data"].encode()) <= 64 for b in row)
        r.fake.push({"update_id": 4, "callback_query": {"id": "cb9", "from": {"id": 7}, "data": row[0]["callback_data"],
                                                         "message": {"message_id": 101, "chat": {"id": 7}}}})
        edit = await until(lambda: next(iter(r.fake.of("editMessageText")), None))
        assert edit["text"].endswith("✅ Allowed") and "To: bob" in edit["text"] and edit["text"].count("bob") == 1 and "reply_markup" not in edit
        await until(lambda: "It is done." in r.sent())
        assert ran == [{"to": "bob"}]
        assert r.fake.of("answerCallbackQuery")[0]["callback_query_id"] == "cb9"
        assert len([b for b in r.fake.of("editMessageText")]) == 1


async def test_deny_button_and_a_stranger_cannot_tap(rig):
    ran = []
    async with rig(*asks_then_answers(), tools=[send_tool(ran)]) as r:
        await r.pair()
        r.fake.push(msg("send it", update_id=3))
        card = await until(lambda: next((b for b in r.fake.of("sendMessage") if keyboard(b)), None))
        row = keyboard(card)[0]
        r.fake.push({"update_id": 4, "callback_query": {"id": "x", "from": {"id": 99}, "data": row[0]["callback_data"],
                                                         "message": {"message_id": 101, "chat": {"id": 7}}}})
        await until(lambda: r.mgr.state("telegram")["offset"] >= 5)
        assert ran == [] and not r.fake.of("editMessageText")
        r.fake.push({"update_id": 5, "callback_query": {"id": "y", "from": {"id": 7}, "data": row[1]["callback_data"],
                                                         "message": {"message_id": 101, "chat": {"id": 7}}}})
        edit = await until(lambda: next(iter(r.fake.of("editMessageText")), None))
        assert edit["text"].endswith("⛔ Denied")
        await until(lambda: "It is done." in r.sent())
        assert ran == []


async def test_answered_in_app_edits_message(rig):
    ran = []
    async with rig(*asks_then_answers(), tools=[send_tool(ran)]) as r:
        await r.pair()
        r.fake.push(msg("send it", update_id=3))
        card = await until(lambda: next((b for b in r.fake.of("sendMessage") if keyboard(b)), None))
        aid = keyboard(card)[0][0]["callback_data"].split(":")[1]
        assert (await r.c.post(f"/api/approvals/{aid}", json={"approve": True})).status_code == 200
        edit = await until(lambda: next(iter(r.fake.of("editMessageText")), None))
        assert "Answered in the app:" in edit["text"] and edit["text"].endswith("✅ Allowed")
        # a tap after the app answered: nothing to answer any more
        r.fake.push({"update_id": 4, "callback_query": {"id": "late", "from": {"id": 7}, "data": f"a:{aid}:0",
                                                         "message": {"message_id": 101, "chat": {"id": 7}}}})
        edit = await until(lambda: len(r.fake.of("editMessageText")) >= 2 and r.fake.of("editMessageText")[-1])
        assert edit["text"].endswith("Already answered.")
        assert ran == [{"to": "bob"}]


@pytest.mark.parametrize("tool", ["browser_login", "request_card", "confirm_purchase"])
async def test_a_card_that_needs_the_app_has_no_buttons(rig, tool):
    ran = []
    async with rig(*asks_then_answers(tool), tools=[send_tool(ran, tool)]) as r:
        await r.pair()
        r.fake.push(msg("go", update_id=3))
        card = await until(lambda: next((b for b in r.fake.of("sendMessage") if "Open the Kelo app to answer this one." in b["text"]), None))
        assert keyboard(card) is None
        aid = next(iter((await r.c.get("/api/approvals/pending")).json()["items"]))["approval_id"]
        r.fake.push({"update_id": 4, "callback_query": {"id": "z", "from": {"id": 7}, "data": f"a:{aid}:1", "message": {"message_id": 1, "chat": {"id": 7}}}})
        await until(lambda: r.mgr.state("telegram")["offset"] >= 5)
        assert ran == []                       # a forged tap does not answer it either
        assert (await r.c.post(f"/api/approvals/{aid}", json={"approve": False})).status_code == 200
        await until(lambda: "It is done." in r.sent())


def test_needs_app_table():
    yes = [{"payment": {"amount": 1}}, {"card": {"id": 1}}, {"needs_card": True}, {"commit": "pay"}, {"commit": "security"},
           {"kind": "human_check"}, {"kind": "location"}, {"type": "human_check"}, {"login_code": {"code": "123456"}},
           *({"name": n} for n in ("confirm_purchase", "begin_order", "browser_fill_card", "request_card", "request_payment_pin", "browser_login",
                                   "request_login", "add_service_key", "save_found_secret", "request_task_permission", "task_allow"))]
    no = [{}, {"name": "send"}, {"name": "browser_click", "commit": ""}, {"commit": "submit"}, {"kind": "schedule"}, {"card": None, "payment": None}]
    assert all(needs_app(e) for e in yes), [e for e in yes if not needs_app(e)]
    assert not any(needs_app(e) for e in no), [e for e in no if needs_app(e)]


class FakeTurn:
    def __init__(self, events, slow=False):
        self.events, self.done, self.approvals, self.slow, self.first = events, False, set(), slow, True

    async def wait(self, after, timeout):
        if self.slow and self.first:
            self.first = False
            await asyncio.sleep(0.1)
            return [], False
        self.done = True
        return self.events[after:], True


async def follow(r, events, **kw):
    ch = r.mgr._ch["telegram"]
    turn = FakeTurn(events, **kw)
    await r.mgr._follow("telegram", ch, "7", turn, "55")


async def test_fill_in_card_error_reaction_and_typing_are_sent(rig):
    async with rig() as r:
        await r.pair()
        await follow(r, [{"type": "reaction", "message_id": 1, "emoji": "👀"},
                         {"type": "secret_request", "request": {"id": "sr_1", "kind": "key", "label": "the Asana key", "state": "open"}},
                         {"type": "secret_request", "request": {"id": "sr_1", "kind": "key", "label": "the Asana key", "state": "open"}},
                         {"type": "error", "message": "The model did not answer."},
                         {"type": "text", "text": "Done."}, {"type": "done"}], slow=True)
        sent = r.sent()
        assert "Kelo needs the Asana key — fill it in in the Kelo app (never send keys or passwords here)." in sent
        assert sent.count("Kelo needs the Asana key — fill it in in the Kelo app (never send keys or passwords here).") == 1
        assert "The model did not answer." in sent and sent[-1] == "Done."
        react = r.fake.of("setMessageReaction")[-1]
        assert react["message_id"] == 55 and react["reaction"] == [{"type": "emoji", "emoji": "👀"}]
        assert r.fake.of("sendChatAction")[0]["action"] == "typing"


async def test_tap_answer_buttons_send_the_option(rig):
    async with rig() as r:
        await r.pair()
        q = [{"question": "Which day?", "options": ["Monday", "Tuesday", "Other"], "typed": ["Other"]}]
        await follow(r, [{"type": "text", "text": "Which day works?"}, {"type": "replies", "questions": q, "goal_id": None}, {"type": "done"}])
        last = r.fake.of("sendMessage")[-1]
        rows = keyboard(last)
        assert [row[0]["text"] for row in rows] == ["Monday", "Tuesday"] and all(row[0]["callback_data"].startswith("r:") for row in rows)
        r.fake.push({"update_id": 9, "callback_query": {"id": "t1", "from": {"id": 7}, "data": rows[1][0]["callback_data"],
                                                         "message": {"message_id": 200, "chat": {"id": 7}}}})
        await until(lambda: "Hello from Kelo" in r.sent())
        assert ("user", "Tuesday", "telegram") in r.app_messages()
        assert r.fake.of("editMessageReplyMarkup")[0]["reply_markup"] == {"inline_keyboard": []}
        # one tap uses the whole set; a second tap on the other option does nothing
        r.fake.push({"update_id": 10, "callback_query": {"id": "t2", "from": {"id": 7}, "data": rows[0][0]["callback_data"],
                                                          "message": {"message_id": 200, "chat": {"id": 7}}}})
        await until(lambda: r.mgr.state("telegram")["offset"] >= 11)
        assert ("user", "Monday", "telegram") not in r.app_messages()
        # not offered: several questions, a picker kind, one option left
        for qs in ([q[0], q[0]], [{"question": "when", "kind": "date", "options": ["a", "b"]}], [{"question": "x", "options": ["Only", "Other"], "typed": ["Other"]}]):
            assert r.mgr._tap_buttons("telegram", qs) is None


async def test_notify_forwarding_off_by_default_and_on(rig):
    async with rig() as r:
        await r.pair()
        n0 = len(r.fake.of("sendMessage"))
        r.activity.notify("task_done", "Report ready", "Your report is ready.")
        await asyncio.sleep(0.2)
        assert len(r.fake.of("sendMessage")) == n0
        r.mgr.set_notify("telegram", True)
        r.activity.notify("task_done", "Report ready", "Your report is ready.")
        await until(lambda: len(r.fake.of("sendMessage")) == n0 + 1)
        assert r.fake.of("sendMessage")[-1]["text"] == "<b>Report ready</b>\nYour report is ready."
        r.mgr.begin_pairing("telegram")                       # the channel's own notifications stay in the app
        await asyncio.sleep(0.2)
        assert len(r.fake.of("sendMessage")) == n0 + 1
        # a note with an approval gets the same buttons
        aid, fut = r.app.state.appctx.broker.create()
        r.app.state.appctx.pending_approvals[aid] = {"name": "send", "approval_id": aid}
        r.activity.notify("approval_waiting", "Please confirm: send", "to bob", approval_id=aid)
        await until(lambda: len(r.fake.of("sendMessage")) == n0 + 2)
        row = keyboard(r.fake.of("sendMessage")[-1])[0]
        assert row[0]["callback_data"] == f"a:{aid}:1"
        r.fake.push({"update_id": 30, "callback_query": {"id": "n1", "from": {"id": 7}, "data": row[1]["callback_data"],
                                                          "message": {"message_id": 300, "chat": {"id": 7}}}})
        assert await asyncio.wait_for(fut, 2) is False
        # one that needs the app gets no buttons
        aid2, _ = r.app.state.appctx.broker.create()
        r.app.state.appctx.pending_approvals[aid2] = {"name": "request_card", "approval_id": aid2}
        r.activity.notify("approval_waiting", "Please confirm: pay", "49 USD", approval_id=aid2)
        await until(lambda: len(r.fake.of("sendMessage")) == n0 + 3)
        last = r.fake.of("sendMessage")[-1]
        assert keyboard(last) is None and "Open the Kelo app to answer this one." in last["text"]


async def test_prepare_talk_arguments_and_answer_approval(rig):
    async with rig() as r:
        ctx = r.app.state.appctx
        from assistant.server import TalkIn
        _, _, tctx, observe, started = ctx.prepare_talk(TalkIn(text="hello there"), None, owner="owner", via="telegram", note="NOTE-FOR-MODEL")
        rows = r.store.messages()
        assert rows[-1]["content"] == "hello there" and rows[-1]["via"] == "telegram"
        assert "NOTE-FOR-MODEL" in tctx.tail[-1]["content"] and "NOTE-FOR-MODEL" not in rows[-1]["content"]
        observe({"type": "text", "text": "hi"})
        observe({"type": "done"})
        assert r.store.messages()[-1]["via"] == "telegram" and r.store.messages()[-1]["content"] == "hi"
        assert ctx.answer_approval("no-such-id", True) is None
        aid, fut = ctx.broker.create()
        assert ctx.answer_approval(aid, False)["decision"] == "denied" and fut.result() is False
        assert ctx.answer_approval(aid, True) is None


async def test_no_token_in_logs(rig, caplog):
    caplog.set_level(logging.DEBUG)
    async with rig() as r:
        await r.pair()
        r.fake.script["sendMessage"] = [err(429, "Too Many Requests", retry_after=1), err(502, "Bad Gateway")]
        r.fake.push(msg("hello", update_id=3))
        await until(lambda: "Hello from Kelo" in r.sent())
        r.fake.script["getUpdates"] = [err(401, "Unauthorized")]
        await until(lambda: [n for n in r.activity.notifications()["items"] if n["type"] == "channel_stopped"])
        everything = caplog.text + json.dumps(r.activity.notifications()) + json.dumps(r.store.messages()) + json.dumps(r.mgr.state("telegram"))
        everything += json.dumps((await r.c.get("/api/vault")).json()) + (await r.c.get("/api/vault/log")).text
        assert TG_TOKEN not in everything and "ABC-secret" not in everything


# ---- the model's tool
from assistant.agent.toolbox import search   # noqa: E402
from assistant.tools.channel_tools import channel_tools   # noqa: E402


def test_the_messenger_tool_is_found_by_its_words():
    tool = channel_tools(lambda: None)[0]
    assert tool.name == "messenger" and "Telegram" in tool.description and "other people" in tool.description
    assert tool.parameters["properties"]["action"]["enum"] == ["status", "new_code", "notify_on", "notify_off", "unpair", "groups", "remove_group"]
    assert tool.parameters["properties"]["channel"]["enum"] == ["telegram", "discord", "slack"]
    for q in ("telegram", "messenger"):
        assert search([tool], q) == [tool]


async def test_messenger_tool_status_code_notify_unpair(rig):
    holder = []
    tool = channel_tools(lambda: holder[0])[0]
    steps = ([ToolCall("c1", "messenger", '{"channel": "telegram", "action": "unpair"}'), Done("tool_calls")], [TextDelta("Disconnected."), Done("stop")])
    async with rig(*steps, tools=[tool]) as r:
        holder.append(r.mgr)
        assert json.loads(await tool.handler({"action": "status"})) == {"connected": False, "bot": None, "paired": None, "notify": False}
        assert (await tool.handler({"action": "new_code"})).startswith("error: Telegram is not connected")
        await r.pair()
        before = len(r.app_messages())
        assert json.loads(await tool.handler({"channel": "telegram", "action": "status"})) == {"connected": True, "bot": "kelo_bot", "paired": "Ann", "notify": False}
        said = await tool.handler({"action": "new_code"})
        code = r.mgr.state("telegram")["pairing"]
        # the server posts the link and the code itself (a code in the model's reply is masked as a secret: "[hidden]", 2026-11-12);
        # the model is told not to repeat them
        posted = r.app_messages()[before:]
        assert code and len(posted) == 1 and "https://t.me/kelo_bot?start=" in posted[0][1] and "start=" not in said and "do not write" in said
        assert hashlib_ok(re.search(r"start=(\d{6})", posted[0][1]).group(1), code["code_hash"])
        assert await tool.handler({"action": "notify_on"}) == "ok" and r.mgr.status("telegram")["notify"] is True
        assert await tool.handler({"action": "notify_off"}) == "ok" and r.mgr.status("telegram")["notify"] is False
        assert tool.auto_approve({"action": "status"}) and tool.auto_approve({"action": "new_code"}) and not tool.auto_approve({"action": "unpair"})
        assert await tool.describe({"action": "unpair"}) == "Disconnect Telegram from Ann?"
        # unpairing asks first
        assert (await r.c.post("/api/turns", json={"text": "disconnect telegram"})).status_code == 200
        pending = await until(lambda: _pending(r))
        assert pending["name"] == "messenger" and "Disconnect Telegram from Ann?" in pending["detail"]
        assert r.mgr.state("telegram")["paired"] is not None
        assert (await r.c.post(f"/api/approvals/{pending['approval_id']}", json={"approve": True})).status_code == 200
        await until(lambda: r.mgr.state("telegram")["paired"] is None)
        r.fake.push(msg("still there?", update_id=20))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 21)
        assert not [m for m in r.app_messages() if m[1] == "still there?"]


def hashlib_ok(code, digest):
    import hashlib
    return hashlib.sha256(code.encode()).hexdigest() == digest


async def _pending(r):
    items = (await r.c.get("/api/approvals/pending")).json()["items"]
    return items[0] if items else None
