"""What an unattended run says before its first approval card, and what it does not ask again (run_asks.py), and which reads a
site's rule can remember (activity.rule_kind)."""

from assistant.activity import rule_kind
from assistant.run_asks import RunAsks, purpose_body, purpose_lines


def test_a_read_of_a_site_can_be_remembered_and_a_send_or_a_payment_cannot():
    assert rule_kind("web_fetch") == "web_read"
    assert rule_kind("send") == "" and rule_kind("confirm_purchase") == ""


def test_a_site_allowed_once_in_a_run_is_not_asked_again_in_that_run():
    asks = RunAsks()
    assert not asks.allowed_before("web_read", "tworld.co.kr")
    asks.allow("web_read", "tworld.co.kr")
    assert asks.allowed_before("web_read", "tworld.co.kr")
    assert not asks.allowed_before("web_read", "other.example")
    assert not asks.allowed_before("browser_action", "tworld.co.kr")
    asks.allow("", "pay.example")                     # a step that can never be remembered is never kept
    assert not asks.allowed_before("", "pay.example")


def test_a_run_reading_three_pages_of_one_host_asks_once_and_a_new_run_asks_again():
    def run(asks):
        asked = []
        for url in ("https://tworld.co.kr/a", "https://tworld.co.kr/b", "https://tworld.co.kr/c"):
            host = "tworld.co.kr"
            if not asks.allowed_before("web_read", host):
                asked.append(url)
                asks.allow("web_read", host)   # the owner allowed it
        return asked

    asks = RunAsks()
    assert run(asks) == ["https://tworld.co.kr/a"]
    assert run(RunAsks()) == ["https://tworld.co.kr/a"]


def test_a_run_says_why_once_before_its_first_card():
    asks = RunAsks()
    first = asks.announce("whether the answer I gave earlier (Kelo AI approved) still holds", "apps.apple.com")
    assert first == "Because whether the answer I gave earlier (Kelo AI approved) still holds, I am going to check apps.apple.com."
    assert asks.announce("another reason", "other.example") == ""
    assert RunAsks().announce("", "apps.apple.com") == ""


def test_the_card_line_and_the_push_carry_the_runs_own_reason():
    lines = [{"label": "URL", "value": "apps.apple.com"}]
    out = purpose_lines("App Store 승인 확인", lines)
    assert out[0] == {"label": "Why", "value": "App Store 승인 확인"} and out[1:] == lines
    assert purpose_lines("", lines) is lines
    assert len(purpose_lines("x", [{"label": str(i), "value": "v"} for i in range(4)])) == 4
    assert purpose_body("x", "Checking again: A", "apps.apple.com") == "Checking again: A · apps.apple.com · Why: x"
    assert purpose_body("", "Checking again: A", "apps.apple.com") == "apps.apple.com"


# ------------------------------------------------------------------------------------------ the whole path, through the app
import asyncio
import json

import httpx

from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from test_approval_sign import app_settings, pair, pending_one, start_turn
from test_server import AUTH, Running
from assistant.server import create_app


def read_tool():
    async def handler(args):
        return "page text"

    async def describe(args):
        return "Read a page"

    return Tool("web_fetch", "web_fetch", {"type": "object"}, handler, requires_approval=True, describe=describe)


def scripted(calls_by_turn):
    """A fake model: each turn's model steps are the tool calls named (one call per step), then the closing text."""
    steps = []
    for n, urls in enumerate(calls_by_turn):
        steps += [[ToolCall(f"c{n}-{i}", "web_fetch", json.dumps({"url": u})), Done("tool_calls")] for i, u in enumerate(urls)]
        steps.append([TextDelta("ok"), Done("stop")])
    it, seen = iter(steps), []

    async def fn(provider, request, **kw):
        seen.append(1)
        for ev in next(it):
            yield ev
    return fn, seen


async def test_a_run_reading_three_pages_of_one_site_is_asked_once_and_a_later_run_asks_again(tmp_path):
    fn, seen = scripted([["https://tworld.co.kr/a", "https://tworld.co.kr/b", "https://tworld.co.kr/c"], ["https://tworld.co.kr/d"]])
    async with Running(create_app(app_settings(tmp_path), tools=[read_tool()], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        phone, _ = await pair(c)
        await start_turn(c, "tworld 페이지 세 개 읽어줘")
        card = await pending_one(c, phone)
        assert card["name"] == "web_fetch" and card["site"] == "tworld.co.kr"
        assert card["can_remember"] is True                      # a read of a site: the usual "remember for this site" choice
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": True}, headers=phone)
        for _ in range(300):                                     # the run goes on through its three reads and its closing words
            if len(seen) >= 4:
                break
            await asyncio.sleep(0.02)
        assert len(seen) == 4
        assert (await c.get("/api/approvals/pending", headers=phone)).json()["items"] == []   # no second card in the same run

        await start_turn(c, "한 페이지 더")
        again = await pending_one(c, phone)                       # a new run asks again
        assert again["site"] == "tworld.co.kr"
        await c.post(f"/api/approvals/{again['approval_id']}", json={"approve": False}, headers=phone)


def test_a_site_allowed_in_a_run_covers_its_subdomains_and_its_parent():
    asks = RunAsks()
    asks.allow("web_read", "tworld.co.kr")
    assert asks.allowed_before("web_read", "m.tworld.co.kr")
    asks.allow("web_read", "apple.com")
    assert asks.allowed_before("web_read", "apps.apple.com")
    assert not asks.allowed_before("web_read", "evil-tworld.co.kr")
    assert not asks.allowed_before("browser_action", "m.tworld.co.kr")
    asks.allow("web_read", "kelo.chat")
    assert asks.allowed_before("web_read", "kelo.chat") and not asks.allowed_before("web_read", "kelo.chat.evil.example")
