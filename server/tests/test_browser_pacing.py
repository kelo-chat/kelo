"""One site is not searched again within seconds: a submit waits 12-25 s after the last one, other navigations 2-5 s (tools/pacing.py)."""

import random

import pytest

from assistant.tools import humanize as human
from assistant.tools.browser import BrowserSession, browser_owner
from assistant.tools.pacing import NAV_GAP_S, SUBMIT_GAP_S, SitePacer

from test_browser_tools import PUBLIC, FakePage


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s


@pytest.fixture
def paced():
    human.configure(reset=True, enabled=True, speed=1.0, seed=5)
    yield
    human.configure(reset=True, enabled=False)


def make(page, clock):
    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC, clock=clock)
    s.pacer = SitePacer(clock, random.Random(3))
    s.sleep = clock.sleep
    return s


def test_gaps_are_per_registrable_host_and_within_the_ranges():
    c = Clock()
    p = SitePacer(c, random.Random(1))
    assert p.need("www.shop.example.com", "submit") == 0
    p.record("www.shop.example.com", "submit", 1.0)
    assert SUBMIT_GAP_S[0] <= p.need("m.shop.example.com", "submit") <= SUBMIT_GAP_S[1]     # same site, another name
    assert NAV_GAP_S[0] <= p.need("shop.example.com", "nav") <= NAV_GAP_S[1]
    assert p.need("other.example.org", "submit") == 0
    c.t += 30
    assert p.need("shop.example.com", "submit") == 0 and p.need("shop.example.com", "nav") == 0
    p.record("shop.example.com", "nav", 1.0)                       # a plain navigation leaves the submit gap alone
    assert p.need("shop.example.com", "submit") == 0
    p.record("shop.example.com", "submit", 0.0)                    # speed 0: no waiting at all
    assert p.need("shop.example.com", "submit") == 0


async def test_the_second_search_on_a_host_waits_and_the_first_does_not(paced):
    c, page = Clock(), FakePage()
    page.url = "https://example.com/"
    s = make(page, c)
    t0 = c.t
    await s.type("input[name=q]", "one", submit=True)
    assert c.t - t0 < 0.01                                        # nothing to wait for
    await s.type("input[name=q]", "two", submit=True)
    assert SUBMIT_GAP_S[0] <= c.t - t0 <= SUBMIT_GAP_S[1]
    assert [x for x in page.calls if x[0] == "press"] == [("press", "input[name=q]", "Enter")] * 2


async def test_a_wait_the_step_has_no_time_for_is_not_slept_and_nothing_is_typed(paced):
    c, page = Clock(), FakePage()
    page.url = "https://example.com/"
    s = make(page, c)
    s.pacer.record("example.com", "submit", 1.0)
    s._step_clock["owner"] = {"spent": 40.0, "limit": 45.0}
    t0 = c.t
    out = await s.type("input[name=q]", "again", submit=True)
    assert "Not submitted" in out and "Wait about" in out and "example.com" in out
    assert c.t == t0 and page.calls == []
    s._step_clock["owner"] = {"spent": 0.0, "limit": 45.0}        # with the whole step left it waits and goes
    await s.type("input[name=q]", "again", submit=True)
    assert c.t - t0 >= SUBMIT_GAP_S[0] - 1 and ("press", "input[name=q]", "Enter") in page.calls


async def test_opening_an_address_on_the_same_host_keeps_the_short_gap_and_speed_zero_never_waits(paced):
    c, page = Clock(), FakePage()
    s = make(page, c)
    t0 = c.t
    await s.open("https://example.com/a")
    await s.open("https://example.com/b")
    assert NAV_GAP_S[0] <= c.t - t0 <= NAV_GAP_S[1]
    human.configure(speed=0)
    t1 = c.t
    for _ in range(3):
        await s.type("input[name=q]", "x", submit=True)
        await s.open("https://example.com/c")
    assert c.t == t1
