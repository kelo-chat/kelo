"""Fill-in cards in the chat (secret_requests.py): the agent asks for a key, a login or a card; the entry shows up right in the
reply (and stays until it is answered); saving it is the consent (no Allow/Deny card); the agent goes on by itself; a cancel is told
once and never nagged about. The value itself never reaches the transcript, the model, a log or the approval log.

All with a scripted model: no real model API is called."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from urllib.parse import unquote

import httpx
import pytest

from assistant.netguard import Reply
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.tools.connect_tools import connect_tools

from test_server import AUTH, TOKEN, Running

BOT_TOKEN = "123456789:AAH-very-secret-bot-token-XYZ"
PASSWORD = "hunter2-Pa$$w0rd!"
USER = "nahyun.kim@example.com"

TELEGRAM = {"name": "telegram", "label": "Telegram bot", "hosts": ["api.telegram.org"], "auth": {"type": "apiKey", "in": "path"},
            "check_url": "https://api.telegram.org/bot{secret}/getMe", "reason": "to send you messages"}


class Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    h = Collect()
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access", "assistant"]
    olds = [(logging.getLogger(n), logging.getLogger(n).level) for n in names]
    for lg, _ in olds:
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)
    yield h
    for lg, level in olds:
        lg.removeHandler(h)
        lg.setLevel(level)


class Model:
    """A scripted model that also keeps every request it was sent (to check what the model saw)."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    async def __call__(self, provider, request, **kw):
        self.seen.append([dict(m) for m in request.messages])
        for ev in self.turns.pop(0):
            yield ev

    def text(self) -> str:
        return json.dumps(self.seen, ensure_ascii=False, default=str)


def telegram_fetcher(calls: list[str], good: str = BOT_TOKEN):
    async def fetch(method, url, *, allowed_hosts, headers=None, content=None, **kw):
        url = unquote(url)
        calls.append(url.replace(good, "<token>"))
        assert "api.telegram.org" in allowed_hosts
        if url == f"https://api.telegram.org/bot{good}/getMe":
            return Reply(200, {"content-type": "application/json"}, b'{"ok":true,"result":{"username":"kelo_bot"}}', False)
        return Reply(401, {"content-type": "application/json"}, b'{"ok":false,"error_code":401,"description":"Unauthorized"}', False)
    return fetch


def make_app(tmp_path, model, *, fetcher=None):
    """The whole app as it builds its own tools (add_service_key, request_login, request_card...), with no browser."""
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"))
    return create_app(s, stream_fn=model, **({"mcp_fetcher": fetcher} if fetcher else {}))


def db_rows(tmp_path, sql, args=()):
    con = sqlite3.connect(tmp_path / "s.db")
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def raw_db(tmp_path) -> bytes:
    return b"".join(p.read_bytes() for p in tmp_path.glob("s.db*"))


async def talk(c, text):
    """POST /api/turns, then follow GET /api/turns/{id} until done: (turn id, events)."""
    tid = (await c.post("/api/turns", json={"text": text})).json()["id"]
    return tid, await follow(c, tid)


async def follow(c, tid, on_event=None):
    events, after = [], 0
    for _ in range(400):
        r = (await c.get(f"/api/turns/{tid}", params={"after": after, "wait": 2})).json()
        for ev in r["events"]:
            events.append(ev)
            if on_event is not None:
                await on_event(ev)
        after = r["next"]
        if r["done"]:
            return events
    raise AssertionError("the reply never ended")


def kinds(events):
    return [e["type"] for e in events if e["type"] != "reaction"]


async def test_a_new_key_opens_an_entry_in_the_chat_without_an_approval_card_and_goes_on_once_saved(tmp_path, logs):
    calls: list[str] = []
    model = Model([TextDelta("아래 입력창에 넣어 주세요."), ToolCall("c1", "add_service_key", json.dumps(TELEGRAM)), Done("tool_calls")],
                  [TextDelta("저장됐어요. 이어서 할게요."), Done("stop")])
    app = make_app(tmp_path, model, fetcher=telegram_fetcher(calls))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        seen_open = {}

        async def on_event(ev):
            if ev["type"] == "secret_request" and ev["request"]["state"] == "open":
                seen_open.update(ev["request"])
                listed = (await c.get("/api/secret-requests")).json()["items"]     # kept, so a reload or another phone shows it too
                assert [i["id"] for i in listed] == [ev["request"]["id"]] and listed[0]["message_id"] is None
                r = await c.put("/api/credentials/telegram/secret", json={"secret": BOT_TOKEN})
                got = r.json()
                assert r.status_code == 200 and got["ok"] and got["checked"] and "turn_id" not in got   # the asking reply goes on
                assert got["reply"] == "live" and got["request"]["state"] == "saved"
        tid = (await c.post("/api/turns", json={"text": "토큰 지금 보낼게"})).json()["id"]
        events = await follow(c, tid, on_event)
        assert "approval_request" not in kinds(events)                   # saving the value is the consent
        assert kinds(events) == ["text", "tool_started", "secret_request", "secret_request", "tool_result", "text", "done"]
        req = seen_open
        assert (req["kind"], req["name"], req["label"], req["hosts"], req["check"]) == ("key", "telegram", "Telegram bot", ["api.telegram.org"], True)
        folded = [e for e in events if e["type"] == "secret_request"][1]["request"]
        assert folded["state"] == "saved" and folded["result"] == {"checked": "ok"}
        result = next(e for e in events if e["type"] == "tool_result")
        assert "saved the key" in result["output"] and "accepted it" in result["output"] and not result["is_error"]
        assert calls == ["https://api.telegram.org/bot<token>/getMe"]  # tried once before it was stored
        vault = (await c.get("/api/vault")).json()
        assert [i["name"] for i in vault["items"]] == ["telegram"] and vault["items"][0]["last_result"] == "ok"
        assert (await c.get("/api/secret-requests")).json()["items"] == []
        everything = [json.dumps(events, ensure_ascii=False), model.text(), (await c.get("/api/messages")).text,
                      (await c.get("/api/vault/log")).text, "\n".join(logs.lines)]
    for blob in everything:
        assert BOT_TOKEN not in blob
    assert BOT_TOKEN.encode() not in raw_db(tmp_path)                    # sealed in the vault, never in the clear
    log = db_rows(tmp_path, "SELECT tool, decision, site, detail FROM approval_log")
    assert log == [{"tool": "add_service_key", "decision": "allowed_once", "site": "api.telegram.org",
                    "detail": "fill-in card in the chat: Telegram bot"}]
    assert "아래 입력창" in json.dumps(events, ensure_ascii=False)


async def test_a_key_the_service_refuses_is_not_saved_and_the_entry_stays_open(tmp_path, logs, monkeypatch):
    monkeypatch.setattr("assistant.server.ENTRY_WAIT_SECONDS", 0.3)
    calls: list[str] = []
    model = Model([ToolCall("c1", "add_service_key", json.dumps(TELEGRAM)), Done("tool_calls")], [TextDelta("아래에 넣어 주세요."), Done("stop")])
    app = make_app(tmp_path, model, fetcher=telegram_fetcher(calls))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        _, events = await talk(c, "텔레그램 연결해줘")
        out = next(e for e in events if e["type"] == "tool_result")["output"]
        assert "fill-in card for the key is now shown in the chat" in out and "entry below" in out and "Keychain" not in out
        r = await c.put("/api/credentials/telegram/secret", json={"secret": "999:wrong-token-value"})
        assert r.status_code == 422 and "did not accept" in r.json()["detail"] and "wrong-token" not in r.text
        assert (await c.get("/api/vault")).json()["items"] == []
        assert [i["name"] for i in (await c.get("/api/secret-requests")).json()["items"]] == ["telegram"]
        assert (await c.get("/api/vault/log")).json()[0]["action"] == "rejected"
        assert "wrong-token" not in "\n".join(logs.lines)


async def test_cancel_while_the_reply_waits_tells_the_agent_and_leaves_nothing(tmp_path):
    model = Model([ToolCall("c1", "add_service_key", json.dumps(TELEGRAM)), Done("tool_calls")], [TextDelta("알겠어요."), Done("stop")],
                  [TextDelta("네."), Done("stop")])
    app = make_app(tmp_path, model, fetcher=telegram_fetcher([]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        async def on_event(ev):
            if ev["type"] == "secret_request" and ev["request"]["state"] == "open":
                r = await c.post(f"/api/secret-requests/{ev['request']['id']}/cancel")
                assert r.json()["request"]["state"] == "cancelled" and r.json()["reply"] == "live"   # the waiting reply is told
        tid = (await c.post("/api/turns", json={"text": "토큰 보낼게"})).json()["id"]
        events = await follow(c, tid, on_event)
        out = next(e for e in events if e["type"] == "tool_result")["output"]
        assert "closed the fill-in card without saving" in out and "Do not ask for it again" in out
        vault = (await c.get("/api/vault")).json()
        assert vault["items"] == [] and vault["pending"] == []                 # the empty request is gone from the vault too
        assert (await c.get("/api/secret-requests")).json()["items"] == []
        await talk(c, "다른 얘기")
        assert "closed the fill-in card" not in json.dumps(model.seen[-1])  # told once, in the waiting reply: no second note
    assert db_rows(tmp_path, "SELECT tool, decision FROM approval_log") == [{"tool": "add_service_key", "decision": "denied"}]


async def test_moving_a_saved_key_to_other_hosts_still_asks_first(tmp_path):
    other = {**TELEGRAM, "hosts": ["api.example-relay.com"], "check_url": ""}
    model = Model([ToolCall("c1", "add_service_key", json.dumps(other)), Done("tool_calls")], [TextDelta("그대로 둘게요."), Done("stop")])
    app = make_app(tmp_path, model, fetcher=telegram_fetcher([]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        await c.post("/api/credentials", json={"name": "telegram", "label": "Telegram bot", "hosts": ["api.telegram.org"],
                                               "placement": {"type": "url_path_segment"}})
        assert (await c.put("/api/credentials/telegram/secret", json={"secret": BOT_TOKEN})).status_code == 200

        async def on_event(ev):
            if ev["type"] == "approval_request":
                assert ev["ask"] == "Send the saved key to new hosts?"
                assert "api.telegram.org" in ev["detail"] and "api.example-relay.com" in ev["detail"] and "deletes the saved key" in ev["detail"]
                await c.post(f"/api/approvals/{ev['approval_id']}", json={"approve": False})
        tid = (await c.post("/api/turns", json={"text": "릴레이로 바꿔"})).json()["id"]
        events = await follow(c, tid, on_event)
        assert "approval_request" in kinds(events) and "secret_request" not in kinds(events)
        assert "kept the saved key" in next(e for e in events if e["type"] == "tool_result")["output"]
        assert (await c.get("/api/vault")).json()["items"][0]["hosts"] == ["api.telegram.org"]   # nothing changed


async def test_saving_after_the_reply_ended_starts_a_short_reply_that_goes_on(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.ENTRY_WAIT_SECONDS", 0.3)
    model = Model([ToolCall("c1", "add_service_key", json.dumps(TELEGRAM)), Done("tool_calls")], [TextDelta("아래에 넣어 주세요."), Done("stop")],
                  [TextDelta("연결됐어요. 봇이 준비됐어요."), Done("stop")])
    app = make_app(tmp_path, model, fetcher=telegram_fetcher([]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        await talk(c, "토큰 보낼게")
        await asyncio.sleep(0.2)
        pending = (await c.get("/api/secret-requests")).json()["items"]
        msgs = (await c.get("/api/messages")).json()
        assert len(pending) == 1 and pending[0]["message_id"] == msgs[-1]["id"]   # under the agent message that asked
        st = (await c.get("/api/agent/status")).json()
        assert st["secret_requests"] == 1 and st["secret_request_kinds"] == {"key": 1}   # the badge can say what waits
        r = (await c.put("/api/credentials/telegram/secret", json={"secret": BOT_TOKEN})).json()
        assert r["turn_id"] and r["reply"] == "started" and r["request"]["state"] == "saved"
        events = await follow(c, r["turn_id"])
        assert "연결됐어요" in "".join(e.get("text", "") for e in events)
        note = model.seen[-1][-1]["content"]
        assert "just saved the key for 'telegram'" in note and "accepted it" in note and BOT_TOKEN not in model.text()
        assert (await c.get("/api/messages")).json()[-1]["content"] == "연결됐어요. 봇이 준비됐어요."
        assert (await c.get("/api/secret-requests")).json()["items"] == []
        st = (await c.get("/api/agent/status")).json()
        assert st["secret_requests"] == 0 and st["secret_request_kinds"] == {}     # handled: the apps' red badge goes away


async def test_a_cancel_after_the_reply_ended_is_told_once_with_the_next_message(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.ENTRY_WAIT_SECONDS", 0.2)
    model = Model([ToolCall("c1", "add_service_key", json.dumps(TELEGRAM)), Done("tool_calls")], [TextDelta("아래에 넣어 주세요."), Done("stop")],
                  [TextDelta("네."), Done("stop")], [TextDelta("네."), Done("stop")])
    app = make_app(tmp_path, model, fetcher=telegram_fetcher([]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        await talk(c, "토큰 보낼게")
        rid = (await c.get("/api/secret-requests")).json()["items"][0]["id"]
        r = (await c.post(f"/api/secret-requests/{rid}/cancel")).json()
        assert r["ok"] and "turn_id" not in r and r["reply"] == "none"          # a cancel starts nothing by itself
        await asyncio.sleep(0.1)
        assert len(model.seen) == 2
        await talk(c, "오늘 날씨는?")
        assert "closed the fill-in card you asked for (the service key for Telegram bot)" in model.seen[-1][-1]["content"]
        await talk(c, "고마워")
        assert "closed the fill-in card" not in model.seen[-1][-1]["content"]
        assert (await c.get("/api/messages")).json()[-2]["content"] == "고마워"   # the note is not stored as the user's words


async def test_a_login_entry_goes_on_to_browser_login_once_it_is_entered(tmp_path, logs):
    model = Model([ToolCall("c1", "request_login", json.dumps({"site": "https://shop.example.com/login", "label": "Example shop"})),
                   Done("tool_calls")], [TextDelta("로그인할게요."), Done("stop")])
    app = make_app(tmp_path, model)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        async def on_event(ev):
            if ev["type"] == "secret_request" and ev["request"]["state"] == "open":
                assert ev["request"]["kind"] == "login" and ev["request"]["hosts"] == ["shop.example.com"]
                r = await c.put(f"/api/vault/{ev['request']['name']}/login", json={"username": USER, "password": PASSWORD, "mode": "after_success"})
                assert r.status_code == 200 and "turn_id" not in r.json() and r.json()["reply"] == "live"
                assert r.json()["request"]["state"] == "saved" and r.json()["request"]["kind"] == "login"
        tid = (await c.post("/api/turns", json={"text": "쇼핑몰에 로그인해줘"})).json()["id"]
        events = await follow(c, tid, on_event)
        assert "approval_request" not in kinds(events)
        out = next(e for e in events if e["type"] == "tool_result")["output"]
        assert "saved once a sign-in with it works" in out and "browser_login(name='login-shop-example-com')" in out
        blobs = [json.dumps(events), model.text(), "\n".join(logs.lines), (await c.get("/api/vault/log")).text]
    for blob in blobs:
        assert PASSWORD not in blob and USER not in blob
    assert db_rows(tmp_path, "SELECT tool, decision FROM approval_log") == [{"tool": "request_login", "decision": "allowed_once"}]


async def test_request_card_waits_for_the_wallet_and_names_only_the_nickname(tmp_path):
    model = Model([TextDelta("결제할 카드가 없어요. 아래에서 추가해 주세요."),
                   ToolCall("c1", "request_card", json.dumps({"reason": "Order of 2 notebooks", "site": "https://shop.example.com"})),
                   Done("tool_calls")], [TextDelta("카드로 결제를 이어갈게요."), Done("stop")])
    app = make_app(tmp_path, model)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        async def on_event(ev):
            if ev["type"] == "secret_request" and ev["request"]["state"] == "open":
                req = ev["request"]
                assert (req["kind"], req["name"], req["label"], req["hosts"]) == ("card", "", "Order of 2 notebooks", ["shop.example.com"])
                assert (await c.post(f"/api/secret-requests/{req['id']}/card", json={"card_id": "card_nope"})).status_code == 404
                card = (await c.post("/api/wallet/cards", json={"label": "My Visa", "exp_month": 12, "exp_year": 2031, "auto_pay": False,
                                                                 "last4": "4242", "brand": "visa",
                                                                 "e2e": {"blob": "QUJDREVG", "alg": "AES-256-GCM", "key_id": "k1"}})).json()
                r = (await c.post(f"/api/secret-requests/{req['id']}/card", json={"card_id": card["id"]})).json()
                assert r["ok"] and r["request"]["result"]["card"]["last4"] == "4242"
        tid = (await c.post("/api/turns", json={"text": "노트 2권 주문해줘"})).json()["id"]
        events = await follow(c, tid, on_event)
        assert "approval_request" not in kinds(events)
        out = next(e for e in events if e["type"] == "tool_result")["output"]
        assert "added the card 'My Visa' (ends 4242)" in out and "wallet_cards" in out
    assert db_rows(tmp_path, "SELECT tool, decision FROM approval_log") == [{"tool": "request_card", "decision": "allowed_once"}]


async def test_a_key_waiting_without_a_request_is_listed_for_the_keychain_and_cancel_removes_it(tmp_path):
    app = make_app(tmp_path, Model())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        # an empty vault item from before fill-in cards (like a request made by an older server)
        await c.post("/api/credentials", json={"name": "telegram", "label": "telegram", "hosts": ["api.telegram.org"],
                                               "placement": {"type": "url_path_segment"}})
        v = (await c.get("/api/vault")).json()
        assert [i["name"] for i in v["pending"]] == ["telegram"]
        items = (await c.get("/api/secret-requests")).json()["items"]
        assert [(i["kind"], i["name"], i["source"]) for i in items] == [("key", "telegram", "draft")]
        await c.get("/api/vault")
        assert len((await c.get("/api/secret-requests")).json()["items"]) == 1     # listed once, however often the page loads
        await c.post(f"/api/secret-requests/{items[0]['id']}/cancel")
        assert (await c.get("/api/vault")).json()["pending"] == [] and (await c.get("/api/secret-requests")).json()["items"] == []
    assert db_rows(tmp_path, "SELECT * FROM approval_log") == []                  # nobody asked: nothing to record as an answer


async def test_secret_request_endpoints_need_the_token(tmp_path):
    app = make_app(tmp_path, Model())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/secret-requests")).status_code == 401
        assert (await c.post("/api/secret-requests/sr_x/cancel")).status_code == 401
        assert (await c.post("/api/secret-requests/sr_x/card", json={"card_id": "c"})).status_code == 401
        c.headers.update(AUTH)
        assert (await c.post("/api/secret-requests/sr_x/cancel")).status_code == 404


def test_check_url_must_stay_on_the_keys_hosts():
    from assistant.store import Store  # noqa: F401 - only the tool's own validation runs here

    async def run():
        tools = {t.name: t for t in connect_tools(_MemStore())}
        bad = await tools["add_service_key"].handler({**TELEGRAM, "check_url": "https://evil.example.com/bot{secret}/getMe"})
        assert bad.startswith("error:") and "check_url" in bad
        worse = await tools["add_service_key"].handler({**TELEGRAM, "check_url": "https://api.telegram.org/getMe?key={secret}"})
        assert worse.startswith("error:")
    asyncio.run(run())


class _MemStore:
    """Just enough of a Store for the tool's checks before anything is saved."""

    def credential(self, name):
        return None


def test_what_an_answer_leads_to(tmp_path):
    """answer_full: live (the asking reply waits), started (a new reply's id), queued (goes on later), none (nothing comes)."""
    from assistant.secret_requests import REPLY_STATES, SecretRequests
    from assistant.store import Store
    store = Store(tmp_path / "s.db")
    outcomes = iter([("queued", None), "turn-1", ("none", None)])
    reqs = SecretRequests(store, on_answer=lambda req: next(outcomes))

    def ask(name):
        return reqs.create("login", name=name, label=name, hosts=[f"{name}.example.com"])["id"]

    got = reqs.answer_full(ask("a"), "saved")
    assert got["reply"] == "queued" and got["turn_id"] is None and got["request"]["state"] == "saved"
    rid = ask("b")
    assert reqs.answer(rid, "saved")[1] == "turn-1"                       # the old two-value form still gives the turn id
    assert reqs.answer_full(rid, "saved") == {"request": reqs.get(rid), "turn_id": None, "reply": "none"}   # not open any more
    assert reqs.saved_full("login", "c")["reply"] == "none" and reqs.cancel_for_full("login", "c")["request"] is None
    assert reqs.answer_full(ask("d"), "saved")["reply"] == "none"
    assert reqs.answer_full("nope", "cancelled") == {"request": None, "turn_id": None, "reply": "none"}
    assert set(REPLY_STATES) == {"live", "started", "queued", "none"}
