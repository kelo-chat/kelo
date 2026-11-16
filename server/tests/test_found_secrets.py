"""A key the agent comes across never reaches the model or the chat (agent/found_secrets.py): tool results show it as [secret #N],
save_found_secret puts the real value in the keychain (hosts limited to the page's own site), a key written into a reply is hidden even
when it arrives split over several pieces, and ordinary text with URLs, UUIDs and hashes is left alone. Fake model, no network."""

import json
import re

import httpx
import pytest

from assistant.agent import Tool, run_agent
from assistant.agent.found_secrets import FoundSecrets, Masker, registrable_domain, scrub
from assistant.agent.loop import AssistantText, ToolResult
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.store import Store
from assistant.tools.connect_tools import connect_tools

from test_agent_profile import app_settings
from test_server import AUTH, Running, follow

KEY = "sk-proj-Zq81LmN0pQrStUvWxYz4567AbCdEfGh"
AUTH_BEARER = {"type": "http", "scheme": "bearer"}


def page_tool(text=None):
    page = text or f"URL: https://platform.acme.com/api-keys\nYour new secret key: {KEY}\nCopy it now, it will not be shown again."

    async def handler(args):
        return page
    return Tool("read_page", "read a page", {"type": "object", "properties": {}}, handler, untrusted=True, source="page", read_only=True)


def scripted_calls(*steps):
    """A fake model: each step is a list of events; keeps every request it was sent."""
    seen, it = [], iter(steps)

    async def fn(provider, request, **kw):
        seen.append(request)
        for ev in next(it):
            yield ev
    fn.seen = seen
    return fn


def call(name, args, cid=None):
    return [ToolCall(cid or f"c-{name}", name, json.dumps(args)), Done("tool_calls")]


async def drive(model, tools, text="set up the api"):
    events = []
    async for ev in run_agent("p", "m", [{"role": "user", "content": text}], tools, stream_fn=model, max_steps=6):
        events.append(ev)
    return events


async def test_a_key_on_a_page_is_hidden_from_the_model_and_saved_on_request(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    tools = [page_tool(), *connect_tools(store)]
    model = scripted_calls(
        call("read_page", {}),
        call("save_found_secret", {"ref": "#1", "name": "acme", "label": "Acme", "hosts": ["api.acme.com"], "auth": AUTH_BEARER}),
        [TextDelta("Saved it."), Done("stop")])
    events = await drive(model, tools)
    for req in model.seen:
        assert KEY not in json.dumps(req.messages)
    second = json.dumps(model.seen[1].messages)
    assert "[secret #1: api_key, hidden" in second and 'save_found_secret(ref=\\"#1\\")' in second
    assert "platform.acme.com/api-keys" in second                       # the address of the page is not hidden
    assert all(KEY not in str(getattr(e, "output", "")) for e in events)
    saved = store.credential("acme")
    assert saved["secret"] == KEY and saved["hosts"] == ["api.acme.com"] and saved["kind"] == "api"
    result = next(e for e in events if isinstance(e, ToolResult) and e.name == "save_found_secret")
    assert not result.is_error and KEY not in result.output
    assert re.match(r"^Saved the key for '([^']+)' in the keychain \(hosts: ([^)]*)\)", result.output)


async def test_save_found_secret_matches_keychain_card_regex(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    tools = [page_tool(), *connect_tools(store)]
    model = scripted_calls(
        call("read_page", {}),
        call("save_found_secret", {"ref": "#1", "name": "acme", "label": "Acme", "hosts": ["api.acme.com", "other.acme.com"], "auth": AUTH_BEARER}),
        [TextDelta("Saved it."), Done("stop")])
    events = await drive(model, tools)
    result = next(e for e in events if isinstance(e, ToolResult) and e.name == "save_found_secret")
    assert not result.is_error
    m = re.match(r"^Saved the key for '([^']+)' in the keychain \(hosts: ([^)]*)\)", result.output)
    assert m is not None
    assert m.group(1) == "acme"
    assert m.group(2) == "api.acme.com, other.acme.com"


async def test_the_key_may_go_only_to_the_site_it_was_found_on_unless_the_user_allows_it(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    tool = {t.name: t for t in connect_tools(store)}["save_found_secret"]
    from assistant.agent import found_secrets
    reg = FoundSecrets()
    reg.hide(f"Key: {KEY}", "https://platform.acme.com/keys")
    token = found_secrets.current.set(reg)
    try:
        same = {"ref": "#1", "name": "acme", "hosts": ["api.acme.com", "eu.api.acme.com"], "auth": AUTH_BEARER}
        assert tool.auto_approve(same) is True                                           # same registrable domain: no card
        assert tool.auto_approve({**same, "hosts": ["api.other.com"]}) is False          # another site: the user is asked
        assert tool.auto_approve({**same, "hosts": ["evilacme.com"]}) is False
        assert "api.other.com" in await tool.describe({**same, "hosts": ["api.other.com"]})
        out = await tool.handler({"ref": "#1", "name": "acme", "auth": AUTH_BEARER})     # no hosts named: the page's own host
        assert not out.startswith("error")
    finally:
        found_secrets.current.reset(token)
    assert store.credential("acme")["hosts"] == ["platform.acme.com"] and store.credential("acme")["secret"] == KEY
    assert registrable_domain("api.example.co.uk") == "example.co.uk" and registrable_domain("dash.openai.com") == "openai.com"


async def test_an_unknown_or_expired_ref_is_an_error_and_nothing_is_saved(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    tool = {t.name: t for t in connect_tools(store)}["save_found_secret"]
    from assistant.agent import found_secrets
    now = [1000.0]
    reg = FoundSecrets(clock=lambda: now[0])
    reg.hide(f"Key: {KEY}", "https://platform.acme.com/keys")
    token = found_secrets.current.set(reg)
    try:
        bad = await tool.handler({"ref": "#9", "name": "acme", "hosts": ["api.acme.com"], "auth": AUTH_BEARER})
        assert bad.startswith("error:") and "nothing was saved" in bad
        now[0] += 3601                                                                    # an hour later it is gone
        late = await tool.handler({"ref": "#1", "name": "acme", "hosts": ["api.acme.com"], "auth": AUTH_BEARER})
        assert late.startswith("error:") and "expired" in late
    finally:
        found_secrets.current.reset(token)
    assert store.credential("acme") is None
    outside = await tool.handler({"ref": "#1", "name": "acme", "hosts": ["api.acme.com"], "auth": AUTH_BEARER})   # no run at all
    assert outside.startswith("error:") and store.credential("acme") is None


async def test_a_key_split_over_several_pieces_never_reaches_the_chat_or_the_history(tmp_path):
    pieces = ["Your key is sk-proj-Zq81L", "mN0pQrStUvWxY", "z4567AbCdEf", "Gh and that is all. See https://example.com/a?b=1 ",
              "id 3f2b1c4e-1111-2222-3333-444455556666 sha 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08."]
    model = scripted_calls([*[TextDelta(p) for p in pieces], Done("stop")])
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=model)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        started = await c.post("/api/turns", json={"text": "what is my key?"})
        events = await follow(c, started.json()["id"])
        shown = "".join(e["text"] for e in events if e["type"] == "text")
        assert KEY not in shown and KEY not in json.dumps(events) and "Zq81L" not in shown
        assert "[hidden]" in shown and "that is all." in shown
        assert "https://example.com/a?b=1" in shown and "3f2b1c4e-1111-2222-3333-444455556666" in shown   # ordinary things stay
        assert "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08" in shown
        stored = json.dumps((await c.get("/api/messages")).json())
        assert KEY not in stored and "Zq81L" not in stored and "[hidden]" in stored


def test_ordinary_text_is_untouched_by_every_part():
    text = ("Order 20261110-884213 ships to a@b.com; call +82 10-1234-5678. See https://shop.example.com/p/7f3a9c1e2b4d6f80a1b2c3d4e5f60718 "
            "and id 3f2b1c4e-1111-2222-3333-444455556666, hash 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08. "
            "The password must be at least 8 characters.")
    assert scrub(text) == text
    assert FoundSecrets().hide(text) == text
    m = Masker()
    assert m.feed(text[:40]) + m.feed(text[40:]) + m.finish() == text


def test_a_private_key_block_split_over_pieces_is_hidden_whole():
    head = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n"
    m = Masker()
    out = m.feed("Here: " + head) + m.feed("abcdEFGH1234\nmore/body+==\n-----END PRIVATE KEY-----\nDone.") + m.finish()
    assert "MIIEvQ" not in out and "abcdEFGH" not in out and out.endswith("Done.") and "[hidden]" in out


def test_a_notification_never_carries_a_key(tmp_path):
    from assistant.activity import Activity
    act = Activity(Store(tmp_path / "a.db"))
    act.notify("task_done", f"Made {KEY}", f"The key is {KEY}, keep it safe.", push=False)
    note = act.notifications()["items"][0]
    assert KEY not in json.dumps(note) and "[hidden]" in note["body"] and "keep it safe." in note["body"]
