"""The hand-over of a final checkout step by structure (tools/checkout_handover.py): a run that reads a final checkout page, or whose click
the guard refused there, gets ONE checkout job started by the app; a second read starts nothing; a cart page starts nothing; the switch
off changes nothing. Real Chromium on local pages (test_checkout.py), a recording stand-in for Workers."""

import pytest

from assistant.agent import ToolOutput
from assistant.tools import checkout_handover
from assistant.tools.browser import browser_tools
from assistant.tools.checkout import REFUSAL
from assistant.tools.checkout_handover import CheckoutHandover

from test_checkout import ctx, on  # noqa: F401


class Jobs:
    """What the app's Workers does for the hand-over: delegate(by_app) starts a job, open_checkout says which shop has one."""

    def __init__(self):
        self.started: list[dict] = []
        self.open: dict[str, str] = {}

    async def delegate(self, args, *, by_app=False):
        assert by_app and args["kind"] == "checkout"
        self.started.append(args)
        wid = f"w{len(self.started):03d}"
        self.open["shop.example.com"] = wid
        return f"Handed off; you will hear back when it is done. (job {wid}: checkout, up to 40 steps / 30 min, in a browser tab of its own.)"

    def open_checkout(self, host):
        return self.open.get(host)


def hooked(session, jobs, **kw):
    hand = CheckoutHandover(session, jobs.delegate, jobs.open_checkout, request=lambda: "buy two blue mugs", **kw)
    return {t.name: t for t in checkout_handover.wrapped(browser_tools(session), hand)}


def said(res):
    return res.tail if isinstance(res, ToolOutput) else str(res)


async def test_a_finder_that_reads_a_final_checkout_page_starts_exactly_one_checkout_job_with_that_url(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    jobs, tools = Jobs(), None
    tools = hooked(session, jobs)
    first = await tools["browser_read"].handler({})
    assert len(jobs.started) == 1
    job = jobs.started[0]
    assert job["url"] == "https://shop.example.com/co" and job["kind"] == "checkout"
    assert "Shop: shop.example.com" in job["task"] and "24,000" in job["task"] and "buy two blue mugs" in job["task"]
    assert "w001" in said(first) and "handed this checkout step over" in said(first)
    await tools["browser_read"].handler({})                         # a second read of the same page
    assert len(jobs.started) == 1


async def test_a_job_that_already_ended_is_not_started_again_by_a_re_read_of_the_same_request(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    jobs = Jobs()
    tools = hooked(session, jobs)
    await tools["browser_read"].handler({})
    jobs.open.clear()                                                # the job reported done
    res = await tools["browser_read"].handler({})
    assert len(jobs.started) == 1 and "handed" not in said(res)


async def test_the_refusal_of_the_click_guard_starts_the_job_and_still_refuses(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    jobs = Jobs()
    tools = hooked(session, jobs)
    held = await tools["browser_click"].hold({"selector": "#buy"})
    assert held.startswith(REFUSAL) and "w001" in held
    assert len(jobs.started) == 1 and await page.evaluate("window.bought || 0") == 0
    await tools["browser_click"].hold({"selector": "#buy"})
    assert len(jobs.started) == 1


async def test_a_page_that_is_not_a_checkout_step_starts_nothing(ctx):
    session, _ = await on(ctx, "https://shop.example.com/product")
    jobs = Jobs()
    tools = hooked(session, jobs)
    res = await tools["browser_read"].handler({})
    assert not jobs.started and "handed" not in said(res)


async def test_the_specialists_own_run_never_hands_over(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    jobs = Jobs()
    tools = hooked(session, jobs, active=lambda: False)
    await tools["browser_read"].handler({})
    assert not jobs.started


async def test_the_order_and_the_open_job_are_in_the_brief_and_an_open_job_blocks_a_second(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    jobs = Jobs()
    jobs.open["shop.example.com"] = "w999"
    tools = hooked(session, jobs)
    res = await tools["browser_read"].handler({})
    assert not jobs.started and "w999" in said(res)


async def test_the_brief_has_the_order_terms(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")

    class O:
        what, site, profile, grant = "two blue mugs", "shop.example.com", {"label": "Home"}, {"max_total": 30000, "nested": {"x": 1}}
    jobs = Jobs()
    tools = hooked(session, jobs, order=lambda: O())
    await tools["browser_read"].handler({})
    task = jobs.started[0]["task"]
    assert "two blue mugs" in task and "Ship to the profile: Home" in task and "max_total" in task and "nested" not in task
