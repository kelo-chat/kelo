"""Fixing the outgoing message inside its approval card (approval_edit.py), and the history rows that go with it."""

import asyncio
import json

import httpx

from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, follow


def settings(tmp_path, **kw):
    return Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), **kw)


def tool(name, ran, **kw):
    async def handler(args):
        ran.append(dict(args))
        return "done"
    return Tool(name, name, {"type": "object"}, handler, requires_approval=True, **kw)


def model(*calls):
    """A fake model: the first turn calls the given tools one by one (each its own model turn), then answers."""
    script = iter([[ToolCall(f"c{i}", name, json.dumps(args)), Done("tool_calls")] for i, (name, args) in enumerate(calls)]
                  + [[TextDelta("ok"), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev
    return fn


MAIL = {"to": ["bob@example.com"], "subject": "Hi", "body": "See you at five."}
TEXT = {"to": "+82 10 5555 1234", "text": "I am late."}


async def pending(c, count=1):
    for _ in range(200):
        items = (await c.get("/api/approvals/pending")).json()["items"]
        if len(items) >= count:
            return items
        await asyncio.sleep(0.02)
    raise AssertionError("no approval card")


async def ask(tmp_path, name, args, ran, **tool_kw):
    """Starts a reply that calls the tool and waits for its card; returns (server, client, turn id, card)."""
    app = create_app(settings(tmp_path), tools=[tool(name, ran, **tool_kw)], stream_fn=model((name, args)))
    srv = Running(app)
    srv.app = app
    await srv.__aenter__()
    c = httpx.AsyncClient(base_url=srv.url, headers=AUTH)
    tid = (await c.post("/api/turns", json={"text": "go"})).json()["id"]
    return srv, c, tid, (await pending(c))[0]


async def close(srv, c):
    await c.aclose()
    await srv.__aexit__(None, None, None)


async def test_only_a_send_tool_has_an_editable_message(tmp_path):
    for sub in ("mail", "text", "email_update", "some_other_tool", "pay"):
        (tmp_path / sub).mkdir()
    ran = []
    srv, c, tid, card = await ask(tmp_path / "mail", "email_send", MAIL, ran)
    try:
        assert card["editable"] == {"field": "body", "text": "See you at five.", "max": None}
        assert "arguments" not in card
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False})
        await follow(c, tid)
    finally:
        await close(srv, c)
    srv, c, tid, card = await ask(tmp_path / "text", "phone_send_text", TEXT, [])
    try:
        assert card["editable"]["field"] == "text" and card["editable"]["max"] == 1600
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False})
        await follow(c, tid)
    finally:
        await close(srv, c)
    for name in ("email_update", "some_other_tool"):   # nothing else is ever editable
        srv, c, tid, card = await ask(tmp_path / name, name, {"body": "x", "text": "y"}, [])
        try:
            assert "editable" not in card
            await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False})
            await follow(c, tid)
        finally:
            await close(srv, c)


async def test_an_edit_replaces_only_that_argument_and_the_log_keeps_both_versions(tmp_path):
    ran = []
    srv, c, tid, card = await ask(tmp_path, "email_send", MAIL, ran)
    try:
        r = await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": True, "edited": {"body": "Running ten minutes late."}})
        assert r.status_code == 200 and r.json()["edited"] is True and r.json()["decision"] == "allowed_once"
        await follow(c, tid)
        assert ran == [{**MAIL, "body": "Running ten minutes late."}]   # the recipients and the subject are untouched
        row = (await c.get("/api/approvals/history")).json()["items"][0]
        assert row["edited"] is True and row["via"] == "app" and row["kind"] == "send" and row["where"] == "bob@example.com"
        assert row["result"] == "allow"
        assert "Running ten minutes late." not in json.dumps(row) and "See you at five." not in json.dumps(row)   # the list never carries the text
        stored = srv.app.state.activity.store._all("SELECT edit_field, edit_before, edit_after FROM approval_log")[0]
        assert dict(stored) == {"edit_field": "body", "edit_before": "See you at five.", "edit_after": "Running ten minutes late."}
    finally:
        await close(srv, c)


async def test_an_empty_or_too_long_or_foreign_edit_is_refused_and_the_card_stays_open(tmp_path):
    ran = []
    srv, c, tid, card = await ask(tmp_path, "phone_send_text", TEXT, ran, validate=lambda a: None)
    try:
        url = f"/api/approvals/{card['approval_id']}"
        for edited in ({"text": "   "}, {"text": "x" * 1601}, {"to": "0100000000"}, {"text": "ok", "to": "0100000000"}):
            r = await c.post(url, json={"approve": True, "edited": edited})
            assert r.status_code == 400, edited
        assert (await c.post(url, json={"approve": True, "edited": {"text": " "}})).json()["detail"]   # a message in the owner's words
        assert ran == [] and len(await pending(c)) == 1   # still waiting
        ok = await c.post(url, json={"approve": True, "edited": {"text": "x" * 1600}})
        assert ok.status_code == 200
        await follow(c, tid)
        assert ran == [{**TEXT, "text": "x" * 1600}]
    finally:
        await close(srv, c)


async def test_a_deny_ignores_an_edit(tmp_path):
    ran = []
    srv, c, tid, card = await ask(tmp_path, "email_send", MAIL, ran)
    try:
        r = await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False, "edited": {"body": ""}})
        assert r.status_code == 200 and r.json()["decision"] == "denied" and "edited" not in r.json()
        await follow(c, tid)
        assert ran == []
        assert (await c.get("/api/approvals/history")).json()["items"][0]["edited"] is False
    finally:
        await close(srv, c)


async def test_a_payment_is_never_editable(tmp_path):
    ran = []
    srv, c, tid, card = await ask(tmp_path / "pay", "email_send", MAIL, ran, approval_extra=lambda _id, _args: {"payment": {"amount": 10}})
    try:
        assert "editable" not in card
        r = await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": True, "edited": {"body": "z"}})
        assert r.status_code == 400
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False})
        await follow(c, tid)
    finally:
        await close(srv, c)


async def test_the_privacy_guard_runs_on_the_new_text(tmp_path):
    ran = []
    srv, c, tid, card = await ask(tmp_path, "email_send", MAIL, ran)
    try:
        card_number = "My card is 4111 1111 1111 1111."
        r = await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": True, "edited": {"body": card_number}})
        assert r.status_code == 200
        second = [p for p in await pending(c) if p["approval_id"] != card["approval_id"]]
        assert second and "editable" not in second[0]   # the text the guard judged is final
        assert ran == []                                  # nothing was sent on the first answer
        await c.post(f"/api/approvals/{second[0]['approval_id']}", json={"approve": False})
        await follow(c, tid)
        assert ran == []
    finally:
        await close(srv, c)


async def test_history_pages_newest_first_and_never_holds_a_secret(tmp_path):
    ran = []
    secret = "sk-ant-api03-" + "A" * 40
    srv, c, tid, card = await ask(tmp_path, "email_send", {**MAIL, "body": secret}, ran)
    try:
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": True, "edited": {"body": f"key {secret} ok"}})
        await follow(c, tid)
        activity = srv.app.state.activity
        for i in range(5):
            activity.log_approval(f"x{i}", task_id=None, tool="email_send", label="Send mail", site="", detail="d", decision="denied",
                                  kind="send", target=f"a{i}@example.com")
        first = (await c.get("/api/approvals/history", params={"limit": 3})).json()
        assert len(first["items"]) == 3 and first["next_before"] is not None
        later = (await c.get("/api/approvals/history", params={"limit": 50, "before": first["next_before"]})).json()
        assert {i["id"] for i in later["items"]}.isdisjoint(i["id"] for i in first["items"])
        assert secret not in json.dumps([first, later])
        kept = dict(activity.store._all("SELECT edit_before, edit_after FROM approval_log WHERE edit_field != ''")[0])
        assert secret not in json.dumps(kept) and "[hidden]" in kept["edit_after"]
    finally:
        await close(srv, c)


async def test_history_and_edit_need_a_token(tmp_path):
    ran = []
    srv, c, tid, card = await ask(tmp_path, "email_send", MAIL, ran)
    try:
        async with httpx.AsyncClient(base_url=srv.url) as anon:
            assert (await anon.get("/api/approvals/history")).status_code == 401
            assert (await anon.post(f"/api/approvals/{card['approval_id']}", json={"approve": True, "edited": {"body": "x"}})).status_code == 401
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False})
        await follow(c, tid)
    finally:
        await close(srv, c)
