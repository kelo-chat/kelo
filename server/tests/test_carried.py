"""One list of what Kelo carries (owner 2026-11-20, D15): a work item and the follow items of the same real thing show once. A link comes
only from structure (the agent names the follow item, the same source id, the same flight and day, the same tracking number, or the
nightly look-back's same_as key), never from titles. Every follow item becomes a work item once at start and when it is found (decision
18), and the nightly look-back adds nothing for a work item it names (decision 19). Fake models only; in-memory store."""

import json

import httpx

from assistant.carried import CarriedLinks
from assistant.follow.items import UpcomingItem
from assistant.follow.store import FollowStore
from assistant.follow.upcoming import UpcomingExtractor
from assistant.goals import GoalBook
from assistant.prompt import follow_line
from assistant.server import create_app
from assistant.store import Store

from test_server import AUTH, Running, settings

NOW = 1_795_000_000.0          # a fixed "now" for the tests
DAY = 86400.0


def fixture():
    store = Store()
    book = GoalBook(store)
    fs = FollowStore(store, clock=lambda: NOW)
    links = CarriedLinks(store, fs, book, clock=lambda: NOW)
    book.carried = links
    return store, book, fs, links


def item(key, title="Dinner", starts=NOW + DAY, refs=None, source="calendar"):
    return UpcomingItem(key=key, kind="appointment", title=title, starts_at=starts, ends_at=None, place="",
                        refs=dict(refs or {}), source=source)


def test_a_work_item_and_a_follow_item_with_the_same_event_id_show_once():
    store, book, fs, links = fixture()
    w = book.create("Trip to Busan", kind="work", summary="booked", origin="agent")
    book.merge_refs(w["id"], {"event": "cal:gcal:abc"})
    fs.save_item(item("cal:gcal:abc", title="Busan trip"))
    links.match_follow("cal:gcal:abc")
    carried = links.items()
    assert len(carried) == 1 and carried[0]["key"] == f"work:{w['id']}"
    assert [f["key"] for f in carried[0]["follow"]] == ["cal:gcal:abc"]


def test_the_agent_links_a_work_item_to_a_follow_item_by_its_key():
    store, book, fs, links = fixture()
    fs.save_item(item("cal:gcal:xyz", title="Dentist"))
    w = book.create("Dentist visit", kind="work", summary="", origin="agent")
    assert links.link(w["id"], "cal:gcal:xyz", "kelo") is True
    assert links.work_of("cal:gcal:xyz") == w["id"]
    assert links.link(w["id"] + 1, "cal:gcal:xyz", "kelo") is False      # linked elsewhere: refused


def test_a_new_follow_item_with_the_same_flight_and_day_links_itself():
    store, book, fs, links = fixture()
    w = book.create("Flight to Seoul", kind="work", summary="", origin="agent")
    book.merge_refs(w["id"], {"flight": "KE123", "date": "2026-11-21"})
    fs.save_item(item("mail:1", title="Your booking", refs={"flight": "KE 123", "date": "2026-11-21"}, source="mail"))
    links.match_follow("mail:1")
    assert links.work_of("mail:1") == w["id"]


def test_the_same_tracking_number_links_a_parcel():
    store, book, fs, links = fixture()
    w = book.create("Parcel from the shop", kind="work", summary="", origin="agent")
    book.merge_refs(w["id"], {"tracking": "1Z999AA1"})
    fs.save_item(item("mail:9", title="Shipped", refs={"tracking": "1z999aa1"}, source="mail"))
    links.match_follow("mail:9")
    assert links.work_of("mail:9") == w["id"]


def test_titles_never_link():
    store, book, fs, links = fixture()
    dinner = book.create("Dinner", kind="work", summary="", origin="agent")
    fs.save_item(item("mail:5", title="Dinner", source="mail"))
    links.match_follow("mail:5")
    assert links.work_of("mail:5") != dinner["id"]          # the same title does not link: it becomes a work item of its own
    assert len(links.items()) == 2


def test_a_removed_follow_item_leaves_its_work_item():
    store, book, fs, links = fixture()
    fs.on_removed = links.unlink_follow
    fs.save_item(item("cal:gcal:r", title="Review"))
    w = book.create("Review the plan", kind="work", summary="", origin="agent")
    links.link(w["id"], "cal:gcal:r", "kelo")
    fs.remove_item("cal:gcal:r")
    assert links.work_of("cal:gcal:r") is None
    assert [c["follow"] for c in links.items()] == [[]]


def test_the_look_back_same_as_a_work_item_adds_no_second_item():
    store, book, fs, links = fixture()
    w = book.create("Dinner with Ann", kind="work", summary="", origin="agent")
    ex = UpcomingExtractor(fs, clock=lambda: NOW)
    out = {"upcoming": [{"title": "Dinner with Ann", "date": "2026-11-21", "quote": "dinner with ann",
                         "same_as": f"work:{w['id']}"}]}
    assert ex.apply(out, ["Dinner with Ann on the 21st"]) == 0
    assert fs.list_items() == []


def test_follow_list_and_goal_text_name_the_link_and_the_prompt_line_skips_it():
    store, book, fs, links = fixture()
    fs.save_item(item("cal:gcal:p", title="Pick up"))
    w = book.create("Pick up the parcel", kind="work", summary="", origin="agent")
    links.link(w["id"], "cal:gcal:p", "kelo")
    assert f"Follow items: cal:gcal:p Pick up" in book.text(w["id"])
    rows = [{"key": "cal:gcal:p", "title": "Pick up", "starts_at": NOW + DAY, "source": "calendar"},
            {"key": "cal:gcal:q", "title": "Other", "starts_at": NOW + 2 * DAY, "source": "calendar"}]
    line = follow_line(rows, "UTC")
    assert "Pick up" in line and "Other" in line    # the prompt line keeps every item (the list screen shows each one once)


async def test_the_old_endpoints_are_unchanged_and_the_one_list_is_new(tmp_path):
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=None)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/follow")).status_code == 200
        assert (await c.get("/api/goals", params={"include": "work"})).status_code == 200
        body = (await c.get("/api/carried")).json()
        assert body == {"items": []}


def test_existing_follow_items_after_the_change():
    """owner 2026-11-20 (decision 18, option 나): each follow item not linked becomes a work item once, keeping its dates, source and
    state; an item of an existing work item (same flight and day) is linked to it instead."""
    store, book, fs, links = fixture()
    w = book.create("Flight to Seoul", kind="work", summary="", origin="agent")
    book.merge_refs(w["id"], {"flight": "KE123", "date": "2026-11-21"})
    fs.save_item(item("mail:1", title="Booking", refs={"flight": "KE123", "date": "2026-11-21"}, source="mail"))
    fs.save_item(item("cal:gcal:z", title="Lunch", starts=NOW + 3 * DAY, source="calendar"))
    links.migrate()
    assert links.work_of("mail:1") == w["id"]
    lunch = links.work_of("cal:gcal:z")
    assert lunch is not None and lunch != w["id"]
    assert fs.get_item("cal:gcal:z")["state"] == "planned"                 # its state is kept
    entries = [e["text"] for e in book.logs(lunch, None)]
    assert any("Kelo started following this from calendar" in t for t in entries)
    order = [c["title"] for c in links.items()]
    assert order == ["Flight to Seoul", "Lunch"]                            # nearest date first; the flight is the nearer one
