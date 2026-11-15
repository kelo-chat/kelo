"""The whole sign-in with an approval in an app, end to end on real Chromium (2026-11-10, 0.9.0+6: the card went out a minute late,
the model searched mail for a code that did not exist, the remember box stayed unticked and a second card followed the approval).

A fake site: a sign-in form -> a verification page ("approve in the app", an unticked "don't ask again on this browser" box) whose
script polls the site -> on approval a redirect page for about 1.5 s -> signed in. A stub model does what the tool text tells it:
it calls browser_login, then ticks the option it was shown. A stub user approves once the card is out (a moment later)."""

from __future__ import annotations

import asyncio
import json
import re
import time

import pytest

from assistant.agent import handover
from assistant.agent.fence import plain
from assistant.store import Store
from assistant.tools import humanize
from assistant.tools.browser import BrowserSession, browser_tools
from assistant.tools.vault_tools import vault_tools

from fixtures.order_harness import public as PUBLIC
from test_vault import KEY, PASSWORD, USER

pw = pytest.importorskip("playwright.async_api")

BASE = "https://id.example.com"
POLL = "<script>setInterval(async () => { const r = await fetch('/status'); const j = await r.json(); if (j.approved) location.href = '%s'; }, 200);</script>"


def pages(after_approval: str) -> dict[str, str]:
    return {
        "/login": "<h1>Sign in</h1><form action=/login/verify method=post><input name=u><input name=p type=password><button>Sign in</button></form>",
        "/login/verify": ("<h1>2단계 인증</h1><p>카카오톡으로 로그인 확인 요청을 보냈어요. 휴대폰에서 승인해 주세요.</p>"
                          "<label><input type=checkbox id=keep> 이 브라우저에서 2단계 인증 사용 안 함</label>" + POLL % after_approval),
        "/login/redirect": "<p>로그인 중…</p><script>setTimeout(() => location.href = '/home', 1500)</script>",
        "/login/keep": "<h1>로그인 상태를 유지할까요?</h1><button id=yes onclick=\"location.href='/home'\">예</button>"
                       "<button id=no onclick=\"location.href='/home'\">아니요</button>",
        "/home": "<h1>Welcome back</h1><a href=/logout>Sign out</a>",
    }


@pytest.fixture
def site():
    return {"approved": False, "served": {}, "pages": pages("/login/redirect")}


@pytest.fixture
async def ctx(site):
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)

        async def serve(route):
            path = re.sub(r"^https://[^/]+", "", route.request.url).split("?")[0]
            if path == "/status":
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"approved": site["approved"]}))
                return
            site["served"].setdefault(path, time.monotonic())
            title = "Home" if path == "/home" else "카카오계정"
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><title>{title}</title><body>{site['pages'].get(path, '<p>x</p>')}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


async def setup(ctx, tmp_path):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    s.check_poll = 0.2
    s.settle_seconds = 0.2
    st = Store(tmp_path / "a.db", KEY.decode())
    st.save_credential("kakao", "Kakao", ["id.example.com"], {}, kind="login")
    st.set_login("kakao", USER, PASSWORD)
    tools = {t.name: t for t in [*vault_tools(st, s), *browser_tools(s)]}
    return s, page, tools


def user(site, page, events):
    """The stub user and the server's hand-over (turn_engine's outcome mapping): the card goes out, the user approves a moment
    later in the app, the step's watcher says how it ended."""
    async def hand(info, gone):
        events.append(("card", time.monotonic(), info["step"]))
        watch = asyncio.ensure_future(gone())
        await asyncio.sleep(1.2)                                   # opening the phone, KakaoTalk, tapping "approve"
        events.append(("approved", time.monotonic(), await page.is_checked("#keep") if "verify" in page.url else None))
        site["approved"] = True
        got = await asyncio.wait_for(watch, 15)
        outcome = "progressed" if got == "progressed" else "solved" if got else "closed"
        events.append(("ended", time.monotonic(), outcome))
        return outcome
    return hand


async def model_steps(s, tools, events):
    """The stub model: browser_login, then what the result tells it (tick the option shown, else read the page)."""
    out = await tools["browser_login"].handler({"name": "kakao"})
    events.append(("login_result", time.monotonic(), out))
    assert "find_login_code" not in out and "verification code" not in out.lower()
    m = re.search(r'(\[data-kelo-opt="\d+"\]): [^\n]*이 브라우저에서 2단계 인증 사용 안 함', plain(out))
    assert m, out
    allowed = tools["browser_click"].auto_approve({"selector": m.group(1)})
    assert (await allowed if hasattr(allowed, "__await__") else allowed) is True
    events.append(("tick", time.monotonic(), m.group(1)))
    return plain(await tools["browser_click"].handler({"selector": m.group(1)}))


async def test_an_app_approval_sign_in_end_to_end(ctx, site, tmp_path):
    s, page, tools = await setup(ctx, tmp_path)
    await s.open(f"{BASE}/login")
    events: list[tuple] = []
    token = handover.set(user(site, page, events))
    try:
        after = await model_steps(s, tools, events)
    finally:
        handover.reset(token)
    cards = [e for e in events if e[0] == "card"]
    assert len(cards) == 1 and cards[0][2] == "approve"                                    # exactly one hand-over
    assert cards[0][1] - site["served"]["/login/verify"] < 1.5                             # out within about a second of detection
    tick = next(e for e in events if e[0] == "tick")
    approved = next(e for e in events if e[0] == "approved")
    assert tick[1] < approved[1] and approved[2] is True                                   # ticked before the approval landed
    assert [e[2] for e in events if e[0] == "ended"] == ["solved"]                         # through the redirect: signed in
    login_text = next(e for e in events if e[0] == "login_result")[2]
    assert "verification_pending (approve)" in login_text and "There is NO code" in login_text
    assert "is done (the page is past signing in)" in after and "Welcome back" in after
    assert not s.approve_pending("id.example.com") or "NO code" in s.approve_pending("id.example.com")


async def test_a_keep_me_signed_in_page_after_the_approval_goes_back_to_the_model_without_a_second_card(ctx, site, tmp_path):
    site["pages"] = pages("/login/keep")
    s, page, tools = await setup(ctx, tmp_path)
    await s.open(f"{BASE}/login")
    events: list[tuple] = []
    token = handover.set(user(site, page, events))
    try:
        after = await model_steps(s, tools, events)
        assert "sign-in is NOT finished yet" in after and "로그인 상태를 유지할까요?" in after   # control back on the question page
        allowed = tools["browser_click"].auto_approve({"selector": "#yes"})
        assert (await allowed if hasattr(allowed, "__await__") else allowed) is True     # "예" goes on with the own sign-in
        done = plain(await tools["browser_click"].handler({"selector": "#yes"}))
    finally:
        handover.reset(token)
    assert len([e for e in events if e[0] == "card"]) == 1                                  # no second card
    assert [e[2] for e in events if e[0] == "ended"] == ["progressed"]
    assert "Welcome back" in done
