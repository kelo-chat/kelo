"""Back off when a service says slow down (connector_cooldown.py): a 429, or a 403/503 that marks a rate limit by its structure, sets a shared
cooldown for the connector; calls to it during the cooldown are answered without a request; a success resets; a plain 403 does not count."""

import json

import httpx
import pytest

from assistant import workers
from assistant.agent import Tool
from assistant.connector_cooldown import Cooldowns, connector_now, cooldowns, is_rate_limit, observe, retry_after
from assistant.netguard import fetch
from assistant.permissions import Gate, Permissions
from assistant.store import Store

from test_permissions import fake_tools, run_calls

GOOGLE_RATE = json.dumps({"error": {"code": 403, "status": "PERMISSION_DENIED", "errors": [{"reason": "rateLimitExceeded"}]}}).encode()
GOOGLE_USER_RATE = json.dumps({"error": {"errors": [{"reason": "userRateLimitExceeded", "domain": "usageLimits"}]}}).encode()
GOOGLE_EXHAUSTED = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}).encode()
GOOGLE_PLAIN = json.dumps({"error": {"code": 403, "status": "PERMISSION_DENIED", "errors": [{"reason": "forbidden"}]}}).encode()
RESOLVE = lambda host, port, *a: [(2, 1, 6, "", ("93.184.216.34", port))]  # noqa: E731


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def fresh():
    cooldowns.clear()
    yield
    cooldowns.clear()


def test_what_counts_as_a_slow_down_is_decided_by_structure():
    assert is_rate_limit(429, {}, b"")
    assert is_rate_limit(503, {"retry-after": "30"}, b"")
    assert is_rate_limit(403, {"retry-after": "30"}, b"")
    assert is_rate_limit(403, {}, GOOGLE_RATE) and is_rate_limit(403, {}, GOOGLE_USER_RATE) and is_rate_limit(403, {}, GOOGLE_EXHAUSTED)
    assert not is_rate_limit(403, {}, GOOGLE_PLAIN) and not is_rate_limit(403, {}, b"") and not is_rate_limit(403, {}, b"<html>no</html>")
    assert not is_rate_limit(503, {}, b"") and not is_rate_limit(500, {"retry-after": "5"}, b"") and not is_rate_limit(200, {}, b"")
    assert retry_after({"retry-after": "120"}) == 120 and retry_after({"retry-after": "soon"}) is None and retry_after({}) is None


def test_the_cooldown_follows_retry_after_else_doubles_from_30_s_to_15_min_and_a_success_resets():
    clock = Clock()
    c = Cooldowns(clock)
    assert c.blocked("gmail") is None
    until = c.note_rate_limit("gmail", {"retry-after": "120"})
    assert until == clock.now + 120 and c.blocked("gmail") == until
    clock.now += 121
    assert c.blocked("gmail") is None
    steps = []
    for _ in range(8):
        steps.append(c.note_rate_limit("gmail", {}) - clock.now)
        clock.now += steps[-1] + 1
    assert steps == [30, 60, 120, 240, 480, 900, 900, 900]
    c.note_success("gmail")
    assert c.note_rate_limit("gmail", {}) - clock.now == 30                                      # reset after a success
    assert c.note_rate_limit("gmail", {}) - clock.now == 30                                      # a call already in flight is not a new step
    assert c.blocked("slack") is None                                                            # other connectors are not affected


async def reply_with(status, headers=None, body=b"{}", *, cid="google_calendar"):
    token = connector_now.set(cid)
    try:
        return await fetch("GET", "https://api.example.com/x", allowed_hosts=["api.example.com"], resolver=RESOLVE,
                           transport=httpx.MockTransport(lambda req: httpx.Response(status, headers=headers or {}, content=body)))
    finally:
        connector_now.reset(token)


async def test_a_429_with_retry_after_from_the_real_fetch_sets_the_cooldown():
    r = await reply_with(429, {"Retry-After": "90"})
    assert r.status == 429
    until = cooldowns.blocked("google_calendar")
    assert until is not None and 80 < until - cooldowns.clock() <= 90


async def test_a_google_rate_limit_403_counts_and_a_plain_403_does_not():
    await reply_with(403, body=GOOGLE_PLAIN)
    assert cooldowns.blocked("google_calendar") is None
    await reply_with(403, body=GOOGLE_RATE)
    assert cooldowns.blocked("google_calendar") is not None


async def test_a_reply_outside_a_connector_call_is_not_noted():
    token = connector_now.set("")
    connector_now.reset(token)
    await fetch("GET", "https://api.example.com/x", allowed_hosts=["api.example.com"], resolver=RESOLVE,
                transport=httpx.MockTransport(lambda req: httpx.Response(429, content=b"{}")))
    assert cooldowns.blocked("google_calendar") is None


async def test_a_success_clears_the_doubling():
    await reply_with(429)
    first = cooldowns.blocked("google_calendar") - cooldowns.clock()
    await reply_with(200)
    cooldowns.clear("google_calendar")
    await reply_with(429)
    assert abs((cooldowns.blocked("google_calendar") - cooldowns.clock()) - first) < 2          # back to the first step, not 60 s


def perms(clock):
    store = Store()
    tools = fake_tools()
    return Permissions(store, tools=lambda: tools, names=lambda cid: {"google_calendar": "Calendar"}.get(cid, cid),
                       cooldowns=Cooldowns(clock))


def counting(name, ran):
    async def handler(args):
        ran.append(args)
        return "done"
    return Tool(name, name, {"type": "object"}, handler, requires_approval=False)


async def test_a_call_during_the_cooldown_is_not_made_and_says_when_to_try_again():
    clock = Clock()
    p = perms(clock)
    p.cooldowns.note_rate_limit("google_calendar", {"retry-after": "600"})
    ran = []
    res, asked, _ = await run_calls([counting("gcal_events", ran)], Gate(p), ("gcal_events", {}))
    assert ran == [] and asked == [] and res[0].is_error
    assert res[0].output.startswith("Calendar asked Kelo to slow down; try again after ")
    hhmm = res[0].output.rsplit(" ", 1)[1]
    assert len(hhmm) == 6 and hhmm[2] == ":" and hhmm.endswith(".")                              # "HH:MM."
    clock.now += 601                                                                             # over: the call goes through again
    res, _, _ = await run_calls([counting("gcal_events", ran)], Gate(p), ("gcal_events", {}))
    assert ran == [{}] and not res[0].is_error


async def test_a_background_job_is_told_to_report_blocked_with_retry_after_min():
    clock = Clock()
    p = perms(clock)
    p.cooldowns.note_rate_limit("google_calendar", {"retry-after": "600"})
    token = workers.current_worker.set("w1")
    try:
        res, _, _ = await run_calls([counting("gcal_events", [])], Gate(p), ("gcal_events", {}))
    finally:
        workers.current_worker.reset(token)
    assert "report it as blocked with retry_after_min=10" in res[0].output
    res, _, _ = await run_calls([counting("gcal_events", [])], Gate(p), ("gcal_events", {}))   # a chat turn: no job advice
    assert "retry_after_min" not in res[0].output


async def test_the_loop_notes_a_slow_down_reply_of_the_tool_it_runs_and_the_next_call_is_not_made():
    """End to end: the tool's own request gets a 429; the second call (any turn) is refused without a request."""
    p = Permissions(Store(), tools=lambda: fake_tools(), names=lambda cid: "Calendar", cooldowns=cooldowns)
    requests = []

    async def handler(args):
        requests.append(args)
        return str((await reply_with_current(429, {"retry-after": "300"})).status)
    res, _, _ = await run_calls([Tool("gcal_events", "x", {"type": "object"}, handler, requires_approval=False)], Gate(p),
                                ("gcal_events", {}), ("gcal_events", {}))
    assert len(requests) == 1 and "asked Kelo to slow down" in res[1].output


async def reply_with_current(status, headers):
    return await fetch("GET", "https://api.example.com/x", allowed_hosts=["api.example.com"], resolver=RESOLVE,
                       transport=httpx.MockTransport(lambda req: httpx.Response(status, headers=headers, content=b"{}")))
