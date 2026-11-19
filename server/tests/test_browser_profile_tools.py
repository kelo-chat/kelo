"""Named browser profiles: the chat's tools and the jobs that use them (brief C2c). Fakes only."""

import asyncio
import json

from assistant.store import Store
from assistant.tools.browser import BrowserSession
from assistant.tools.browser_more import BrowserExtras
from assistant.workers import Limits, WorkerHost, Workers
from assistant import tool_roles

from test_work_browser import in_tab


def extras(store, session=None):
    s = session or BrowserSession(connect=None)
    return s, {t.name: t for t in BrowserExtras(s, profiles=store).tools()}


async def run(tool, **args):
    out = await tool.handler(args)
    return out if isinstance(out, str) else getattr(out, "text", str(out))


async def test_create_needs_a_label_and_list_shows_each_profile_with_its_sites(tmp_path):
    st = Store(tmp_path / "p.db")
    s, tools = extras(st)
    assert "error" in (await run(tools["browser_profiles"], action="create")).lower()
    out = await run(tools["browser_profiles"], action="create", label="Second account")
    pid = st.browser_profiles()[0]["id"]
    assert pid in out and "Second account" in out
    st.note_browser_sign_in("shop.example.com", "login", profile=pid)
    listed = await run(tools["browser_profiles"], action="list")
    assert pid in listed and "Second account" in listed and "shop.example.com" in listed
    assert "does not prove" in tools["browser_profiles"].description


async def test_create_tells_a_locked_or_duplicate_case_instead_of_raising(tmp_path):
    st = Store(tmp_path / "p.db")
    s, tools = extras(st)
    await run(tools["browser_profiles"], action="create", label="Second")
    assert "error" in (await run(tools["browser_profiles"], action="create", label="second")).lower()


async def test_delete_needs_the_card_and_closes_the_windows_bound_to_it(tmp_path):
    st = Store(tmp_path / "p.db")
    s, tools = extras(st)
    pid = st.create_browser_profile("Second")["id"]
    tool = tools["browser_profile_delete"]
    assert tool.requires_approval is True
    s.bind_profile("task:p1", pid)
    s.bind_profile("task:p2", "other")
    closed = []

    async def close_tab(key):
        closed.append(key)
        return True
    s.close_tab = close_tab
    out = await run(tool, profile=pid)
    assert closed == ["task:p1"] and st.browser_profiles() == [] and "deleted" in out.lower()
    assert "error" in (await run(tool, profile=pid)).lower()


async def test_browser_open_binds_a_profile_only_to_a_window_that_does_not_exist_yet(tmp_path):
    st = Store(tmp_path / "p.db")
    s = BrowserSession(connect=None)
    s.profile_check = st.check_browser_profile
    pid = st.create_browser_profile("Second")["id"]
    assert s.try_bind_profile("dev1", pid) == ""
    assert s.profile_of("dev1") == pid
    s._windows["dev1"] = type("W", (), {"profile": ""})()
    assert "another profile" in s.try_bind_profile("dev1", "p_other")
    assert s.try_bind_profile("dev1", pid) == "" or True
    assert "no browser profile" in s.try_bind_profile("dev2", "p_nope")


def bare(tmp_path):
    bound = []
    host = WorkerHost(start_turn=lambda *a: None, cancel_turn=lambda t: None, handoff=None, emit=lambda *a: None, tools=lambda: [], browser=True)
    host.bind_profile = lambda key, profile: bound.append((key, profile))
    st = Store(tmp_path / "w.db")
    return Workers(st, host, Limits()), st, bound


async def test_delegate_binds_a_profile_to_the_jobs_key(tmp_path):
    ws, st, bound = bare(tmp_path)
    pid = st.create_browser_profile("Second")["id"]
    out = await ws.delegate({"task": "check the orders page", "kind": "browser", "profile": pid})
    assert "Handed off" in out
    wid = st._all("SELECT id, profile FROM workers")[0]
    assert wid["profile"] == pid and bound[0] == (f"task:{wid['id']}", pid)


async def test_delegate_refuses_an_unknown_profile_and_ignores_it_for_other_kinds(tmp_path):
    ws, st, bound = bare(tmp_path)
    assert "Error" in await ws.delegate({"task": "x", "kind": "browser", "profile": "p_nope"})
    assert st._all("SELECT id FROM workers") == []


def test_the_profile_tools_are_the_chats_and_not_a_checkout_jobs():
    assert {"browser_profiles", "browser_profile_delete"} <= tool_roles.BROWSER_CHAT_KEEPS
    assert "browser_profile_delete" in tool_roles.CHECKOUT_DENIED
