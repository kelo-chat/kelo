"""One login card per account at a time (secret_requests.py, request_login): an older open request of the same kind is replaced when it is
the same item, shares a host, is an app on the same phone, or belongs to the same task or reply; asking again while one is open does not
make a second card; a saved login covers the other open requests for the same account ("covered"); a request whose task has ended
expires after 30 minutes.

All with a real Store on a temp file and no model: no network is used."""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet

from assistant.secret_requests import STATES, SecretRequests, entry_context
from assistant.store import Store
from assistant.tools.vault_tools import vault_tools
from assistant.workers import current_turn

KEY = Fernet.generate_key().decode()
GMS = ["app:com.google.android.gms"]
PLAY = ["app:com.android.vending"]


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    st = Store(tmp_path / "a.db", KEY)
    entries = SecretRequests(st, clock=clock)
    return st, entries, clock


def open_names(st) -> list[str]:
    return sorted(r["name"] for r in st._all("SELECT name FROM secret_requests WHERE state = 'open'"))


def tool(st, name: str):
    return {t.name: t for t in vault_tools(st, None)}[name]


def test_different_names_on_the_same_host_replace_each_other(env):
    st, entries, _ = env
    a = entries.create("login", name="shop-a", label="A", hosts=["shop.example.com"])
    entries.create("login", name="shop-b", label="B", hosts=["shop.example.com"])
    assert entries.get(a["id"])["state"] == "replaced"
    assert open_names(st) == ["shop-b"]


def test_two_app_requests_on_one_phone_replace_each_other(env):
    st, entries, _ = env
    g = entries.create("login", name="google-gms", label="Google", hosts=GMS)
    entries.create("login", name="google-play", label="Play", hosts=PLAY)
    assert entries.get(g["id"])["state"] == "replaced"
    assert open_names(st) == ["google-play"]


def test_the_same_task_replaces_and_unrelated_requests_stay(env):
    st, entries, _ = env
    turn = {"task_id": 7, "turn_id": "t-1"}
    a = entries.create("login", name="a", hosts=["a.example.com"], turn=turn)
    entries.create("login", name="b", hosts=["b.example.com"], turn={"task_id": 7})
    assert entries.get(a["id"])["state"] == "replaced"
    other = entries.create("login", name="c", hosts=["c.example.com"], turn={"task_id": 8, "turn_id": "t-2"})
    assert entries.get(other["id"])["state"] == "open"
    assert open_names(st) == ["b", "c"]


def test_a_card_is_not_replaced_across_kinds(env):
    st, entries, _ = env
    k = entries.create("key", name="shop-key", hosts=["shop.example.com"])
    entries.create("login", name="shop-login", hosts=["shop.example.com"])
    assert entries.get(k["id"])["state"] == "open"


async def test_asking_again_while_one_is_open_creates_no_row(env):
    st, entries, _ = env
    first = await tool(st, "request_login").handler({"site": "https://shop.example.com/login", "label": "Shop"})
    assert "fill-in card" in first
    token = entry_context.set((entries, None))
    turn_token = current_turn.set({"task_id": None, "turn_id": "t-9"})
    try:
        again = await tool(st, "request_login").handler({"site": "https://shop.example.com/login", "label": "Shop"})
    finally:
        entry_context.reset(token)
        current_turn.reset(turn_token)
    assert again == "Already asked the user for this sign-in (Shop); wait for it — do not ask again."
    assert len(st._all("SELECT id FROM secret_requests")) == 1


async def test_two_requests_at_the_same_moment_leave_one_open_row(env):
    st, entries, _ = env
    t = tool(st, "request_login")
    await asyncio.gather(t.handler({"site": "https://shop.example.com/login"}), t.handler({"site": "https://shop.example.com/login"}))
    assert len(st._all("SELECT id FROM secret_requests WHERE state = 'open'")) == 1


async def test_saving_one_covers_the_other_open_requests_and_wakes_their_waiters(env):
    st, entries, _ = env
    # two open rows for one account can only exist from before this rule (the older code kept both): simulate that
    g = entries.create("login", name="google-gms", label="Google", hosts=GMS)
    p = entries.create("login", name="google-play", label="Play", hosts=PLAY)
    st._run("UPDATE secret_requests SET state = 'open', answered_at = NULL WHERE id = ?", (g["id"],))
    waiting = asyncio.ensure_future(entries.wait(p["id"], 5))
    await asyncio.sleep(0)
    entries.answer(g["id"], "saved", {"mode": "after_success"})
    assert await waiting == "covered"
    assert entries.get(p["id"])["state"] == "covered"
    assert entries.covered_by(p["id"]) == {"covered_by": "google-gms", "mode": "after_success"}
    assert open_names(st) == []


async def test_a_waiting_reply_goes_on_with_the_saved_login_name(env):
    st, entries, _ = env
    gms = entries.create("login", name="google-gms", label="Google", hosts=GMS)

    async def waiter(req):
        # the user saves the open card (google-gms) while the reply waits on it, asked for another name of the same phone
        assert req["id"] == gms["id"]
        entries.answer(gms["id"], "saved", {"mode": "now"})
        return "saved"

    token = entry_context.set((entries, waiter))
    try:
        out = await tool(st, "request_login").handler({"site": "app:com.android.vending", "name": "google-play"})
    finally:
        entry_context.reset(token)
    assert "Go on now" in out and "android_login(name='google-gms')" in out
    assert "google-play" not in out


async def test_a_stale_request_of_an_ended_task_expires_after_30_minutes(env):
    st, entries, clock = env
    st._run("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
    st._run("INSERT INTO tasks(id, status) VALUES (1, 'done'), (2, 'running'), (3, 'done')")
    old = entries.create("login", name="old", hosts=["old.example.com"], turn={"task_id": 1})
    running = entries.create("login", name="running", hosts=["run.example.com"], turn={"task_id": 2})
    clock.now += 1801
    recent = entries.create("login", name="recent", hosts=["new.example.com"], turn={"task_id": 3})
    entries.sweep()
    assert entries.get(old["id"])["state"] == "expired"
    assert entries.get(running["id"])["state"] == "open"
    assert entries.get(recent["id"])["state"] == "open"
    clock.now += 86400
    entries.sweep()
    assert entries.get(running["id"])["state"] == "expired"   # the one-day rule still applies to the rest


def test_covered_is_a_known_state():
    assert "covered" in STATES


# ------------------------------------------------------------------ the account name the card is pre-filled with (username_hint)
def open_hint(st) -> list[str]:
    return [r["username_hint"] for r in st._all("SELECT username_hint FROM secret_requests WHERE state = 'open'")]


async def test_request_login_keeps_the_given_account_as_a_non_secret_prefill(env):
    st, entries, _ = env
    await tool(st, "request_login").handler({"site": "https://shop.example.com/login", "username": "  nahyun.kim@example.com "})
    assert open_hint(st) == ["nahyun.kim@example.com"]
    public = entries.public(st._all("SELECT * FROM secret_requests")[0])
    assert public["username_hint"] == "nahyun.kim@example.com"


async def test_request_login_without_username_has_an_empty_hint(env):
    st, entries, _ = env
    await tool(st, "request_login").handler({"site": "https://shop.example.com/login"})
    assert open_hint(st) == [""]
    assert entries.public(st._all("SELECT * FROM secret_requests")[0])["username_hint"] == ""


@pytest.mark.parametrize("bad", ["x" * 201, "a\nb@example.com", "a\x00b", 5, None])
async def test_a_bad_username_hint_is_dropped_not_stored(env, bad):
    st, _, _ = env
    out = await tool(st, "request_login").handler({"site": "https://shop.example.com/login", "username": bad})
    assert "fill-in card" in out
    assert open_hint(st) == [""]


def test_the_tool_describes_the_username_argument(env):
    st, _, _ = env
    assert "username" in tool(st, "request_login").parameters["properties"]


def test_an_old_database_gets_the_column(tmp_path):
    st = Store(tmp_path / "b.db", KEY)
    with st._lock:
        st._db.execute("DROP TABLE IF EXISTS secret_requests")
        st._db.execute("CREATE TABLE secret_requests (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', "
                       "label TEXT NOT NULL DEFAULT '', hosts TEXT NOT NULL DEFAULT '[]', reason TEXT NOT NULL DEFAULT '', "
                       "check_url TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'agent', turn_id TEXT NOT NULL DEFAULT '', "
                       "task_id INTEGER, worker_id TEXT NOT NULL DEFAULT '', run_source TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', "
                       "after_id INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'open', result TEXT NOT NULL DEFAULT '{}', "
                       "told INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, answered_at REAL)")
        st._db.commit()
    entries = SecretRequests(st)
    req = entries.create("login", name="x", hosts=["x.example.com"], username_hint="me@example.com")
    assert req["username_hint"] == "me@example.com"


async def test_the_vault_item_of_a_pending_login_carries_the_prefill(env):
    st, _, _ = env
    await tool(st, "request_login").handler({"site": "https://shop.example.com/login", "username": "me@example.com"})
    item = [c for c in st.credentials() if c["kind"] == "login"][0]
    assert not item["has_secret"] and item["username_hint"] == "me@example.com"
    await tool(st, "request_login").handler({"site": "https://other.example.org/login"})
    item = [c for c in st.credentials() if "other.example.org" in c["hosts"]][0]
    assert item["username_hint"] == ""
