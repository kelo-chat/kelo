"""A fill-in card the agent showed (a key, a payment card, a payment PIN) that nobody fills is followed up like a login: the item
is made when the card opens (secret_requests.on_open), closes when the card is filled in / cancelled / expired / replaced, keeps the
login's timing, names what and where, and offers only snooze and drop."""

import httpx

from assistant.followups import ENTRY_WHAT, TEMPLATES
from assistant.secret_requests import SecretRequests
from assistant.server import Settings, create_app

from test_followups import HOUR, MIN, Writer, ts
from test_followups import make as make_followups
from test_server import AUTH, TOKEN, Running


def make(tmp_path=None, **kw):
    f, store, clock, sent, writer = make_followups(tmp_path, **kw)
    entries = SecretRequests(store, clock=clock)
    f.entries = entries
    entries.on_open = f.entry_requested
    return f, entries, store, clock, sent, writer


def open_items(f, kind="entry"):
    return [i for i in f.items() if i["kind"] == kind]


async def test_an_agent_key_card_becomes_a_followup():
    f, entries, store, clock, sent, writer = make()
    req = entries.create("key", name="openai", label="OpenAI key", reason="to read your usage")
    [item] = open_items(f)
    assert item["title"] == "OpenAI key" and item["detail"] == "a key" and item["stage"] == "first" and item["next_at"] == clock.t + 30 * MIN
    row = f._find("entry", req["id"])
    assert row["ref"] == req["id"] and f.get(item["id"])["status"] == "open"
    assert f.entry_requested(entries._row(req["id"])) == item["id"]                 # the same card again keeps the one item
    assert len(open_items(f)) == 1
    clock.t += 30 * MIN
    assert await f.tick() == 1 and sent[0][0]["kind"] == "entry"
    facts = writer.requests[0].messages[-1]["content"]
    assert "OpenAI key" in facts and "fill-in card" in facts and "service key" in facts and "never into the chat" in facts


async def test_card_and_pin_too_login_and_drafts_not():
    f, entries, store, clock, sent, _ = make()
    entries.create("card", name="card-1", label="Visa")
    entries.create("payment_pin", name="pin-1", label="Payment PIN")
    entries.create("login", name="login-x", label="Shop", hosts=["shop.example.com"])      # has its own item (request_login)
    entries.ensure_draft("key", "draft-key", "Draft", [])                                  # not asked by the agent
    entries.create("key", name="user-key", label="User", source="user")
    assert sorted((i["title"], i["detail"]) for i in open_items(f)) == [("Payment PIN", "a payment PIN"), ("Visa", "a payment card")]


async def test_filling_the_card_closes_it():
    f, entries, store, clock, sent, _ = make()
    req = entries.create("key", name="openai", label="OpenAI key")
    entries.answer(req["id"], "saved")
    clock.t += 30 * MIN
    assert await f.tick() == 0 and sent == []                                              # nothing to remind about
    assert open_items(f) == []
    closed = f._find("entry", req["id"], open_only=False)
    assert closed["status"] == "resolved" and closed["closed_why"] == "the card was filled in"


async def test_cancel_and_expiry_close_it():
    f, entries, store, clock, sent, _ = make()
    a = entries.create("card", name="c1", label="Visa")
    b = entries.create("key", name="k1", label="Key")
    c = entries.create("key", name="k2", label="Key two")
    entries.answer(a["id"], "cancelled")
    entries.create("key", name="k2", label="Key two again")                                 # replaces the older k2 card
    clock.t += 2 * 86400                                                                   # a day passes: the open ones expire
    entries.sweep()
    f.reconcile()
    assert open_items(f) == []
    why = {r["ref"]: r["closed_why"] for r in store._all("SELECT ref, closed_why FROM followups WHERE kind = 'entry'")}
    assert set(why.values()) == {"the card was closed"} and {a["id"], b["id"], c["id"]} <= set(why)


async def test_reminders_follow_the_login_timing():
    f, entries, store, clock, sent, writer = make(t0=ts(2026, 11, 7, 10, 0))
    entries.create("key", name="openai", label="OpenAI key")
    clock.t += 29 * MIN
    assert await f.tick() == 0
    clock.t += MIN
    assert await f.tick() == 1
    clock.t += 3 * HOUR
    assert await f.tick() == 1 and "This is the last reminder." in writer.requests[1].messages[-1]["content"]
    [item] = open_items(f)
    assert item["stage"] == "on_hold" and item["next_at"] is None
    clock.t += 24 * HOUR - 1
    assert await f.tick() == 0 and len(sent) == 2                                          # on hold: no more


async def test_entry_followup_message_names_what_and_where():
    f, entries, store, clock, sent, _ = make(writer=Writer(fail=True))                      # the model fails: the fixed words
    where = "Fill in the card in the chat (or My → Keychain → Waiting for input), or tell me to drop it."
    entries.create("card", name="c1", label="Visa 1234")
    clock.t += 30 * MIN
    assert await f.tick() == 1
    assert sent[-1][1] == f"I still need a payment card (Visa 1234) to go on. {where}"
    entries.create("key", name="k1", label="OpenAI key")
    clock.t += 30 * MIN
    assert await f.tick() == 1
    assert sent[-1][1] == f"I still need a key (OpenAI key) to go on. {where}"
    assert set(ENTRY_WHAT) == {"key", "card", "payment_pin"} and TEMPLATES["entry"].count("{what}") == 1


async def test_api_lists_kind_entry_with_snooze_and_drop_only(tmp_path):
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home"), db_path=str(tmp_path / "a.db"))
    app = create_app(s, stream_fn=Writer())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        req = app.state.secret_requests.create("key", name="openai", label="OpenAI key")
        app.state.secret_requests.create("login", name="login-s", label="Shop", hosts=["s.example.com"])
        items = (await c.get("/api/followups")).json()["items"]
        [item] = [i for i in items if i["kind"] == "entry"]
        assert item["title"] == "OpenAI key" and item["status"] == "open" and item["login"] is None
        assert [a["kind"] for a in item["actions"]] == ["snooze", "drop"]
        got = (await c.post(f"/api/followups/{item['id']}/drop")).json()
        assert got["ok"] and got["item"]["status"] == "dropped"
        assert app.state.secret_requests.get(req["id"])["state"] == "open"                  # dropping only stops the reminders
