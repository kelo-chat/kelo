"""Follow-through parcel source: the delivery status on a carrier's tracking page, read by the main model (no word of any language
is matched in code), the parcel found in a shipping mail by the model's mail read, the plan's fixed refs, and one runner pass from in
transit to delivered (the item is done). Every page and every model answer is a fake (no network)."""

from __future__ import annotations

import sqlite3

import pytest

from assistant.event_kinds import MailBookings
from assistant.follow.items import ItemFinder, UpcomingItem
from assistant.follow.plan import Planner
from assistant.follow.runner import FollowRunner
from assistant.follow.sources.page import PageSource
from assistant.follow.sources.parcel import SYSTEM, ParcelSource
from assistant.follow.store import FollowStore
from assistant.i18n import t

NOW = 1763600000.0
TRACK_URL = "https://track.example.com/?invoice=1234567890123"
MAIL_BODY = "Your order was sent.\nCarrier: Example Express\nTracking number: 1234567890123\nTrack it: " + TRACK_URL


class Page:
    """The tracking page as the fake fetcher returns it; `html` is changed between readings."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.urls: list[str] = []

    async def __call__(self, url: str) -> str:
        self.urls.append(url)
        return f"<html><head><title>Tracking</title><script>var x='delivered'</script></head><body><div>{self.html}</div></body></html>"


class Judge:
    """A fake main model: answers by the page text it is given through a table (the code under test never matches words)."""

    def __init__(self, table: dict[str, object]) -> None:
        self.table = table
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str):
        self.calls.append((system, user))
        for needle, answer in self.table.items():
            if needle in user:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return None


class Activity:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, note_type: str, title: str, detail: str, push: bool = False) -> None:
        self.notes.append((note_type, title, detail))


def parcel_item(starts_at: float = NOW - 86400) -> UpcomingItem:
    return UpcomingItem(key="mail:order-1", kind="parcel", title="Order shipped", starts_at=starts_at, ends_at=starts_at + 14 * 86400,
                        place="", refs={"tracking": "1234567890123", "carrier": "Example Express", "url": TRACK_URL, "date": "2025-11-19"},
                        source="mail")


def source(html: str, table: dict[str, object]) -> tuple[ParcelSource, Page, Judge]:
    page, judge = Page(html), Judge(table)
    return ParcelSource(page=PageSource(fetch_fn=page), ask=judge, clock=lambda: NOW), page, judge


REFS = {"tracking": "1234567890123", "url": TRACK_URL}


@pytest.mark.asyncio
async def test_the_status_is_what_the_model_says_about_the_page_text():
    src, page, judge = source("Left the depot, on its way", {"on its way": {"status": "in_transit"}})
    reading = await src.read(REFS)
    assert reading.ok is True and reading.fields == {"parcel": "in_transit"} and reading.done is False
    assert page.urls == [TRACK_URL]
    system, user = judge.calls[0]
    assert system == SYSTEM
    assert "on its way" in user and "var x" not in user        # visible text only: scripts never reach the model


@pytest.mark.asyncio
async def test_delivered_is_the_end_of_the_item():
    src, _, _ = source("handed over", {"handed over": {"status": "delivered"}})
    reading = await src.read(REFS)
    assert reading.ok is True and reading.done is True


@pytest.mark.asyncio
async def test_no_word_of_the_page_decides_anything_without_the_model():
    # the page says "delivered" in plain English; with no model (or an unknown answer) it is still no reading
    src, _, _ = source("Delivered", {})
    assert (await src.read(REFS)).ok is False
    src2, _, _ = source("Delivered", {"Delivered": {"status": "unknown"}})
    assert (await src2.read(REFS)).ok is False
    src3, _, _ = source("Delivered", {"Delivered": {"status": "teleported"}})
    assert (await src3.read(REFS)).ok is False
    src4, _, _ = source("Delivered", {"Delivered": RuntimeError("down")})
    assert (await src4.read(REFS)).ok is False
    assert ParcelSource(page=PageSource(fetch_fn=Page("x")), ask=None).available() is False


@pytest.mark.asyncio
async def test_an_unchanged_page_is_not_asked_twice():
    src, _, judge = source("on its way", {"on its way": {"status": "in_transit"}})
    await src.read(REFS)
    await src.read(REFS)
    assert len(judge.calls) == 1


def test_status_names_are_in_the_owners_language():
    assert t("Delivered", lang="ko") == "배송 완료"
    assert t("In transit", lang="ko") == "배송 중"
    assert t("Out for delivery", lang="ko") == "배송 출발"
    assert t("Picked up", lang="ko") == "집하"


def mail_read(answer):
    async def ask(system: str, user: str):
        return answer
    return MailBookings(ask)


SHIPPED = {"mails": {"0": {"kind": "parcel", "tracking": "1234567890123", "carrier": "Example Express", "url": TRACK_URL}}}


@pytest.mark.asyncio
async def test_a_shipping_mail_becomes_a_parcel_item_for_two_weeks():
    def mail(age: float, mid: str):
        return lambda: [{"id": mid, "subject": "Order shipped", "body": MAIL_BODY, "created_at": NOW - age}]
    items = await ItemFinder(mail=mail(3600, "order-1"), bookings=mail_read(SHIPPED)).find(NOW)
    assert len(items) == 1 and items[0].kind == "parcel" and items[0].source == "mail"
    assert items[0].refs["url"] == TRACK_URL and items[0].refs["tracking"] == "1234567890123" and items[0].refs["carrier"] == "Example Express"
    assert items[0].ends_at == items[0].starts_at + 14 * 86400
    older = await ItemFinder(mail=mail(5 * 86400, "order-2"), bookings=mail_read(SHIPPED)).find(NOW)
    assert [i.kind for i in older] == ["parcel"]                  # days later it may still be on its way
    assert await ItemFinder(mail=mail(20 * 86400, "order-3"), bookings=mail_read(SHIPPED)).find(NOW) == []


@pytest.mark.asyncio
async def test_a_parcel_needs_the_model_and_a_number_and_a_link_that_are_in_the_mail():
    mails = lambda: [{"id": "m1", "subject": "Order shipped", "body": MAIL_BODY, "created_at": NOW - 3600}]  # noqa: E731
    assert await ItemFinder(mail=mails).find(NOW) == []                                  # no model read: nothing
    no_link = {"mails": {"0": {"kind": "parcel", "tracking": "1234567890123", "carrier": "X", "url": ""}}}
    assert await ItemFinder(mail=mails, bookings=mail_read(no_link)).find(NOW) == []
    made_up = {"mails": {"0": {"kind": "parcel", "tracking": "9999999999999", "carrier": "X", "url": "https://elsewhere.example/9"}}}
    assert await ItemFinder(mail=mails, bookings=mail_read(made_up)).find(NOW) == []     # not in the mail: dropped


def test_the_plan_sets_the_parcel_refs_and_window_itself():
    src, _, _ = source("x", {})
    planner = Planner(sources=[src])
    it = parcel_item()
    plan = planner.validate({"checks": [{"kind": "parcel", "refs": {"url": "https://elsewhere.example"}, "every_minutes": 5,
                                         "from": "-3d", "until": "+1d"}]}, it)
    assert len(plan.checks) == 1
    ch = plan.checks[0]
    assert ch["refs"] == {"tracking": "1234567890123", "carrier": "Example Express", "url": TRACK_URL}
    assert ch["fields"] == ["parcel"]
    assert ch["every_minutes"] == 60
    assert ch["window_start"] == it.starts_at and ch["window_end"] == it.starts_at + 14 * 86400
    bare = UpcomingItem(key="k", kind="parcel", title="t", starts_at=NOW, ends_at=None, place="", refs={"tracking": "1"}, source="mail")
    assert planner.validate({"checks": [{"kind": "parcel"}]}, bare).checks == []
    # no model, no source: no parcel check
    assert Planner(sources=[ParcelSource(page=None, ask=None)]).validate({"checks": [{"kind": "parcel"}]}, it).checks == []


@pytest.mark.asyncio
async def test_without_a_model_plan_a_parcel_is_still_watched():
    src, _, _ = source("x", {})
    plan = await Planner(sources=[src]).plan(parcel_item())
    assert [c["kind"] for c in plan.checks] == ["parcel"]


@pytest.mark.asyncio
async def test_runner_tells_the_status_moves_and_ends_the_item_when_delivered():
    now = [NOW]
    src, page, _ = source("on its way", {"on its way": {"status": "in_transit"}, "handed over": {"status": "delivered"}})
    src.clock = lambda: now[0]
    db = sqlite3.connect(":memory:", check_same_thread=False)
    store = FollowStore(db, clock=lambda: now[0])
    activity = Activity()
    runner = FollowRunner(store, ItemFinder(), Planner(sources=[src]), [src], activity=activity, jitter=False, clock=lambda: now[0])

    await runner._plan_new(parcel_item(), NOW)
    assert [c["kind"] for c in store.list_checks(item_key="mail:order-1")] == ["parcel"]
    await runner.tick(NOW)
    assert activity.notes == []                                     # the first reading is the baseline

    page.html = "handed over"
    now[0] = NOW + 3600
    await runner.tick(NOW + 3600)
    assert activity.notes == [("follow_change", "Order shipped", t("Status: {status}", status=t("Delivered")))]
    assert store.get_item("mail:order-1")["state"] == "done"
    assert store.list_checks(item_key="mail:order-1", state="active") == []
