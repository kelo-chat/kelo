"""Browser turns kept small (agent/loop.py PAGE_KEEP, browser.py changes after a step on the same page, older pictures not sent
again, the size of every request logged by part) without dropping what the model still needs, and a replay of a long buying turn
(the shape of the failed lottery turn: a window, a frame, many clicks on one page) measured before and after."""

from __future__ import annotations

import json
import logging

import pytest

from assistant.agent import loop as loop_mod
from assistant.agent.fence import plain
from assistant.agent.loop import DIGEST_PREFIX, OLD_PICTURE, Tool, ToolOutput, run_agent
from assistant.compaction import request_tokens
from assistant.providers import Done, TextDelta, ToolCall
from assistant.store import Store
from assistant.tools import humanize
from assistant.tools.browser import CHANGES_HEAD, NO_TEXT_CHANGE, changed_lines
from assistant.agent.fence import data

import fixtures.order_harness as harness
from fixtures.order_harness import Env

pw = pytest.importorskip("playwright.async_api")


# ---------------------------------------------------------------- what changed on a page
def test_changed_lines_shows_the_changes_with_context_and_what_left():
    old = "\n".join(["Header", "Menu", *[f"Notice {i} about the weekly draw" for i in range(20)], "Pick 6 numbers", "Selected: none",
                     "Total: 0", "Footer a", "Footer b", "Footer c"])
    new = old.replace("Selected: none", "Selected: 1 2 3 4 5 6").replace("Total: 0", "Total: 5,000원")
    got = changed_lines(old, new)
    assert "Selected: 1 2 3 4 5 6" in got and "Total: 5,000원" in got and "Pick 6 numbers" in got   # changes + context
    assert "Header" not in got and "[No longer on the page:]\nSelected: none" in got
    assert changed_lines(old, old) == ""
    assert changed_lines(old, "something else entirely\nand more") is None      # mostly new: the whole page instead


# ---------------------------------------------------------------- the history of one run
def page(url: str, text: str, note: str = "") -> str:
    return f"URL: {url}\nTitle: {data('Shop')}\n{note}\n{data(text)}"


def scripted(steps, seen):
    turns = [[ToolCall(f"c{i}", n, json.dumps(a)), Done("tool_calls")] for i, (n, a) in enumerate(steps)] + [[TextDelta("done"), Done("stop")]]
    it = iter(turns)

    async def fn(provider, request, **kw):
        seen.append(request)
        for ev in next(it, [TextDelta("."), Done("stop")]):
            yield ev
    return fn


async def test_older_page_readings_become_one_line_and_the_newest_stay_whole(caplog):
    readings = iter([page("https://a.example/1", "FIRST PAGE " * 50, "[A note of ours on page one.]"),
                     page("https://a.example/2", "SECOND PAGE " * 50), page("https://a.example/3", "THIRD PAGE " * 50),
                     page("https://a.example/3", "only the changed line", CHANGES_HEAD.replace("]", "]")),
                     "Error: not run"])

    async def browse(_):
        return next(readings)
    tool = Tool("browser_read", "read", {"type": "object", "properties": {}}, browse, untrusted=True, source="page")
    earlier = [{"role": "user", "content": "go"}]
    seen: list = []
    with caplog.at_level(logging.INFO, logger="assistant.agent"):
        events = [e async for e in run_agent("p", "m", earlier, [tool], stream_fn=scripted([("browser_read", {})] * 5, seen))]
    last = [m for m in seen[-1].messages if m.get("role") == "tool"]
    assert last[0]["content"].startswith(DIGEST_PREFIX) and "https://a.example/1" in last[0]["content"]
    assert "A note of ours on page one." in last[0]["content"] and "FIRST PAGE" not in last[0]["content"]
    assert "SECOND PAGE" in plain(last[1]["content"]) and "THIRD PAGE" in plain(last[2]["content"])       # the last two full ones
    assert "only the changed line" in plain(last[3]["content"])                                         # a partial one after them
    assert "Error: not run" in plain(last[4]["content"])   # not a page reading: it replaces nothing
    # the stored conversation keeps the short form too; the caller's messages are never changed
    stored = [m for m in events[-1].new_messages if m.get("role") == "tool"]
    assert stored[0]["content"].startswith(DIGEST_PREFIX) and earlier == [{"role": "user", "content": "go"}]
    # each request's size is logged by part, without content
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("request size")]
    assert len(lines) == len(seen) and "page readings" in lines[-1] and "PAGE" not in "".join(lines)


async def test_a_newer_reading_replaces_only_what_it_supersedes():
    readings = iter([page(f"https://a.example/{i}", f"PAGE {i} " * 40) for i in range(4)])

    async def browse(_):
        return next(readings)
    tool = Tool("browser_open", "open", {"type": "object", "properties": {}}, browse, untrusted=True, source="page")
    seen: list = []
    [e async for e in run_agent("p", "m", [{"role": "user", "content": "go"}], [tool], stream_fn=scripted([("browser_open", {})] * 4, seen))]
    # between two requests in a row, only the one entry that dropped out of the window changed (the cached start stays the same)
    for a, b in zip(seen[1:], seen[2:]):
        common = list(zip(a.messages, b.messages))
        assert sum(1 for x, y in common if x != y) <= 1


async def test_older_pictures_are_not_sent_again():
    shots = iter([ToolOutput("one", ["data:image/jpeg;base64,AAAA"]), ToolOutput("two", ["data:image/jpeg;base64,BBBB"])])

    async def look(_):
        return next(shots)
    tool = Tool("browser_human_check", "look", {"type": "object", "properties": {}}, look)
    seen: list = []
    [e async for e in run_agent("p", "m", [{"role": "user", "content": "go"}], [tool], images_ok=True,
                                stream_fn=scripted([("browser_human_check", {})] * 2, seen))]
    sent = json.dumps(seen[-1].messages)
    assert "BBBB" in sent and "AAAA" not in sent and OLD_PICTURE.format(name="browser_human_check") in sent


# ---------------------------------------------------------------- a long buying turn on local pages, before and after
FILLER = "\n".join(f"공지 {i}: 이번 주 판매 안내와 당첨 확인 방법, 고객센터 운영 시간 변경, 이벤트 참여 방법에 관한 자세한 설명이 있습니다." for i in range(60))
NUMS = "".join(f"<button id=n{i} onclick=\"pick({i})\">{i}</button>" for i in range(1, 46))
HOME = "https://www.ticketshop.example/"
PAGES = {
    HOME: f"<h1>Ticket shop</h1><pre>{FILLER}</pre><a id=open href=\"#\" onclick=\"window.open('https://ol.ticketshop.example/game','g');return false\">구매하기 (새창)</a>",
    "https://ol.ticketshop.example/game": f"<h1>Game</h1><pre>{FILLER[:2500]}</pre><iframe src=\"https://pay.ticketshop.example/frame\" width=900 height=600></iframe>",
    "https://pay.ticketshop.example/frame": f"""<script>let s=[];function pick(n){{if(!s.includes(n)&&s.length<6)s.push(n);
      document.getElementById('sel').textContent='선택: '+s.join(' ');document.getElementById('tot').textContent='합계: '+(s.length==6?'5,000원':'0원')}}</script>
      <p>1장 5,000원. 번호 6개를 고르세요.</p>{NUMS}<div id=sel>선택: 없음</div><div id=tot>합계: 0원</div>
      <pre>{FILLER[:2000]}</pre>
      <button id=buy onclick="if(confirm('구매하시겠습니까?')){{fetch('/buy',{{method:'POST'}}).then(()=>{{document.body.innerHTML='<p>구매 완료 번호 A-1</p>'}})}}">구매하기</button>""",
}
STEPS = [("browser_open", {"url": HOME}), ("browser_read", {}), ("begin_order", {"site": "ticketshop.example", "what": "티켓 1장 1 2 3 4 5 6"}),
         ("browser_click", {"selector": "#open"}), ("browser_read", {}),
         *[("browser_click", {"selector": f"#n{i}"}) for i in range(1, 7)], ("browser_read", {}),
         ("confirm_purchase", {"summary": "티켓 1장 (1 2 3 4 5 6)", "amount": 5000, "currency": "KRW", "site": "ticketshop.example"}),
         ("browser_click", {"selector": "#buy"}), ("browser_read", {})]


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)
        c.bought = []

        async def serve(route):
            url = route.request.url.split("?")[0]
            if url.endswith("/buy"):
                c.bought.append(url)
                await route.fulfill(status=200, content_type="application/json", body="{}")
                return
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><title>Tickets</title><body>{PAGES.get(url, '<p>x</p>')}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


ORIGINAL_SCRIPTED = harness.scripted


async def replay(ctx, monkeypatch, *, small: bool):
    sizes: list[int] = []
    real = ORIGINAL_SCRIPTED

    def measured(steps):
        fn = real(steps)

        async def wrap(provider, request, **kw):
            sizes.append(request_tokens(request.messages))
            async for ev in fn(provider, request, **kw):
                yield ev
        return wrap
    monkeypatch.setattr(harness, "scripted", measured)
    monkeypatch.setattr(loop_mod, "PAGE_KEEP", 2 if small else 10**6)
    env = Env(Store(":memory:"), await ctx.new_page())
    env.session.overlay_pause = 0.05
    env.session.changes_only = small
    run = await env.run(STEPS)
    return run, sizes


async def test_a_long_buying_turn_still_completes_and_costs_far_less(ctx, monkeypatch, capsys):
    before_run, before = await replay(ctx, monkeypatch, small=False)
    ctx.bought.clear()
    run, after = await replay(ctx, monkeypatch, small=True)
    # the flow is the same and completes: one card (the payment), one purchase, the numbers picked in the frame are seen
    assert run.card_tools() == ["confirm_purchase"] == before_run.card_tools() and ctx.bought == ["https://pay.ticketshop.example/buy"]
    assert all(not r.is_error for r in run.results), [(r.name, r.output[:200]) for r in run.results if r.is_error]
    picks = [plain(r.output) for r in run.results if r.name == "browser_click"][1:7]
    assert all(CHANGES_HEAD in p or NO_TEXT_CHANGE in p for p in picks)
    assert "선택: 1 2 3 4 5 6" in picks[-1] and "합계: 5,000원" in picks[-1]          # what the model needs to pay is there
    assert "구매 완료 번호 A-1" in plain(run.result("browser_read", 3).output)
    total_before, total_after = sum(before), sum(after)
    with capsys.disabled():
        print(f"\n[replayed buying turn, {len(before)}/{len(after)} requests, {len(before_run.results)}/{len(run.results)} results] before: total {total_before:,} tokens, largest request "
              f"{max(before):,}; after: total {total_after:,}, largest {max(after):,} ({100 - 100 * total_after // total_before}% less)")
    assert total_after < total_before * 0.6 and max(after) < max(before) * 0.6
