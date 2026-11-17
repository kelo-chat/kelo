"""Page readings with refs (tools/page_tree.py): the page as Playwright's accessibility tree, `only: controls`, clicking and typing
by ref, a stale ref, and the approval judge for a ref whose element could not be read (it judges the ref's words, never "e12").
Real Chromium on pages served from memory (skipped without Playwright's browser)."""

from __future__ import annotations

import pytest
from fixtures.order_harness import Env

from assistant.agent.fence import plain
from assistant.store import Store
from assistant.tools import humanize
from assistant.tools.browser import BrowserPolicyError, browser_owner
from assistant.tools.page_tree import (
    controls_only,
    frame_hosts,
    interactive_refs,
    ref_boxes,
    ref_name,
    ref_of,
    ref_selector,
    ref_words,
    strip_boxes,
)

TREE = """- generic [active] [ref=e1]:
  - heading "Shop" [level=1] [ref=e2]
  - paragraph [ref=e3]: Some paragraph text here.
  - textbox "Search" [ref=e4]
  - button "Go" [ref=e5]
  - link "Cart (2)" [ref=e6] [cursor=pointer]:
    - /url: "#x"
  - generic [ref=e7] [cursor=pointer]: Clickable div
  - combobox "Qty" [ref=e8]:
    - option "1" [selected]
  - button [ref=e9]:
    - img
    - text: Pay now
  - iframe [ref=e11]:
    - generic [ref=f1e1]:
      - paragraph [ref=f1e2]: inner para
      - button "Inner \\"quoted\\" btn" [ref=f1e3]"""

BOXED = """- generic [active] [ref=e1] [box=0,21,800,2568]:
  - heading "Shop" [level=1] [ref=e2] [box=0,21,800,37]
  - button "Go" [ref=e5] [box=153.5,-114,34,21]
  - iframe [ref=e11] [box=85,2172,404,204]:
    - button "Inner" [ref=f1e3] [box=8,50,69,21]"""


@pytest.mark.parametrize("given, ref", [("e12", "e12"), ("f1e3", "f1e3"), ("[ref=e12]", "e12"), ("ref=e12", "e12"),
                                        ("aria-ref=e12", "e12"), ("  e5 ", "e5"), ("button#go", None), ("text=e12 items", None),
                                        ("#e12", None), ("", None), ("e", None)])
def test_ref_of_and_ref_selector(given, ref):
    assert ref_of(given) == ref
    assert ref_selector(given) == (f"aria-ref={ref}" if ref else given)


def test_controls_only_keeps_what_acts_and_headings():
    got = controls_only(TREE)
    for kept in ('heading "Shop"', 'textbox "Search"', 'button "Go"', 'link "Cart (2)"', '- /url: "#x"', 'combobox "Qty"',
                 'option "1"', "Clickable div", "[ref=e9]", 'button "Inner'):
        assert kept in got, kept
    for dropped in ("paragraph", "Some paragraph", "inner para", "- generic [active]", "- iframe", "- img", "text: Pay now"):
        assert dropped not in got, dropped
    assert "    - /url" in got and "      - button" in got   # original indentation
    assert len(got) < len(TREE)
    assert controls_only('- paragraph: x\n  - /url: "y"') == ""   # an address line only under a kept link


def test_ref_words_names_and_boxes():
    w = ref_words(TREE)
    assert w["e5"] == 'button "Go"' and w["e4"] == 'textbox "Search"' and w["e7"] == 'generic "Clickable div"'
    assert w["e9"] == 'button "Pay now"'                     # named by the text under it
    assert w["f1e3"] == 'button "Inner "quoted" btn"' and w["e3"] == 'paragraph "Some paragraph text here."'
    assert ref_name(w["e5"]) == "Go" and ref_name("iframe") == ""
    assert ref_boxes(BOXED) == {"e1": (0, 21, 800, 2568), "e2": (0, 21, 800, 37), "e5": (153.5, -114, 34, 21),
                                "e11": (85, 2172, 404, 204), "f1e3": (8, 50, 69, 21)}
    assert "[box=" not in strip_boxes(BOXED) and '- button "Go" [ref=e5]' in strip_boxes(BOXED)
    assert ref_words(BOXED)["e5"] == 'button "Go"'
    assert interactive_refs(TREE) == ["e4", "e5", "e6", "e7", "e8", "e9", "f1e3"]
    assert frame_hosts(TREE) == {"f1": "e11"}


# ---------------------------------------------------------------------------------------------------------------- real Chromium
pw = pytest.importorskip("playwright.async_api")

SHOP = "https://shop.example"
PAGES = {
    "/": """<h1>Mugs</h1><p>A long paragraph about mugs that nobody needs to act on.</p>
<label>Search <input id=q></label><button id=go onclick="document.getElementById('out').textContent='went ' + document.getElementById('q').value">Go</button>
<div id=out></div><a href="/next">Next page</a>
<iframe src="https://pay.shop.example/frame" width=500 height=200></iframe>""",
    "/frame": """<p>Frame words</p><button onclick="this.textContent='pressed'">Frame button</button>""",
    "/next": "<h1>Next</h1><button>Other</button>",
    "/pay": """<h1>Checkout</h1><p>Total 24,000 KRW</p><button id=pay onclick="document.body.innerHTML='<p>paid</p>'">Pay now</button>""",
}


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True, args=["--site-per-process"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(viewport={"width": 1000, "height": 700})

        async def serve(route):
            path = "/" + route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
            body = PAGES.get(path)
            await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><title>Shop</title><body>{body or 'ok'}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


async def env_on(ctx) -> Env:
    env = Env(Store(":memory:"), await ctx.new_page())
    env.session.overlay_pause = 0.05
    return env


def ref_for(reading: str, words: str) -> str:
    line = next(ln for ln in plain(reading).splitlines() if words in ln and "[ref=" in ln)
    return line.split("[ref=", 1)[1].split("]", 1)[0]


async def test_a_reading_has_refs_controls_only_is_shorter_and_frames_have_f_refs(ctx):
    env = await env_on(ctx)
    run = await env.run([("browser_open", {"url": SHOP + "/"}), ("browser_read", {}), ("browser_read", {"only": "controls"})])
    full, short = plain(run.result("browser_read", 0).output), plain(run.result("browser_read", 1).output)
    assert '- button "Go" [ref=' in full and "[ref=e" in full and "long paragraph about mugs" in full
    assert '[ref=f' in full and "Frame button" in full and "Frame words" in full                # the frame is in the tree
    assert "[A frame inside the page, from pay.shop.example:]" in full
    assert '- button "Go" [ref=' in short and 'heading "Mugs"' in short and "Frame button" in short
    assert "long paragraph about mugs" not in short and "Frame words" not in short and len(short) < len(full)
    assert ref_for(full, "Frame button").startswith("f")


async def test_click_and_type_by_ref_and_a_stale_ref_says_read_again(ctx):
    env = await env_on(ctx)
    first = await env.run([("browser_open", {"url": SHOP + "/"})])
    opened = first.result("browser_open").output
    q, go, frame_btn = ref_for(opened, 'textbox "Search"'), ref_for(opened, 'button "Go"'), ref_for(opened, "Frame button")
    run = await env.run([("browser_type", {"selector": q, "text": "blue"}), ("browser_click", {"selector": go}),
                         ("browser_click", {"selector": f"[ref={frame_btn}]"})])
    assert not any(r.is_error for r in run.results), [r.output[:300] for r in run.results]
    assert run.cards == []
    assert "went blue" in plain(run.result("browser_click", 0).output)
    frame = next(f for f in env.page.frames if "pay.shop.example" in f.url)
    assert await frame.evaluate("() => document.querySelector('button').textContent") == "pressed"
    # after going to another page, refs of the old reading are gone
    await env.page.goto(SHOP + "/next")
    with pytest.raises(BrowserPolicyError, match="not on the page any more: read the page again"):
        await env.session.click(go)
    with pytest.raises(BrowserPolicyError, match="ref f1e"):
        await env.session.click(frame_btn)


async def test_a_ref_whose_element_cannot_be_read_is_judged_by_its_words(ctx, monkeypatch):
    env = await env_on(ctx)
    first = await env.run([("browser_open", {"url": SHOP + "/pay"})])
    pay = ref_for(first.result("browser_open").output, 'button "Pay now"')

    async def unreadable(selector):
        return None
    monkeypatch.setattr(env.session, "read_target", unreadable)
    browser_owner.set("owner")
    assert await env.session.judge("browser_click", {"selector": pay}) == ""
    assert env.session.selector_words(pay) == 'button "Pay now"' and env.session.selector_words("#pay") == "#pay"
    assert await env.session.judge("browser_click", {"selector": "e999"}) == "unknown"    # a ref the reading never had
    # an unknown ref always asks, even inside an order the user asked for
    run = await env.run([("begin_order", {"site": "shop.example", "what": "a mug"}), ("browser_click", {"selector": "e999"})],
                        answer=lambda req: False)
    assert "browser_click" in run.card_tools()


async def test_a_ref_missing_from_the_last_reading_is_named_by_the_element_itself(ctx, monkeypatch):
    # 2026-11-12: the same apply button clicked a second time asked "press a button?" though the owner had asked to apply
    env = await env_on(ctx)
    first = await env.run([("browser_open", {"url": SHOP + "/pay"})])
    pay = ref_for(first.result("browser_open").output, 'button "Pay now"')

    async def unreadable(selector):
        return None
    monkeypatch.setattr(env.session, "read_target", unreadable)
    browser_owner.set("owner")
    env.session._ref_words["owner"] = {}                      # the reading after a click no longer carries it
    assert await env.session.judge("browser_click", {"selector": pay}) == ""   # read from the element: its words decide nothing


async def test_the_plain_text_reading_stays_when_the_tree_cannot_be_read(ctx, monkeypatch):
    env = await env_on(ctx)

    async def broken(page):
        return None
    monkeypatch.setattr(env.session, "_tree", broken)
    run = await env.run([("browser_open", {"url": SHOP + "/"})])
    out = plain(run.result("browser_open").output)
    assert "[ref=" not in out and "A long paragraph about mugs" in out
    assert "[A frame inside the page, from pay.shop.example:]" in out and "Frame words" in out


# ---------------------------------------------------------------------------------------------------------------- browser_look
async def test_look_labels_the_controls_and_always_takes_the_labels_out(ctx, monkeypatch):
    env = await env_on(ctx)
    await env.run([("browser_open", {"url": SHOP + "/"})])
    browser_owner.set("owner")
    seen = []
    real = env.session.shoot

    async def shoot(page, key=None, **kw):
        seen.append(await page.evaluate("() => document.querySelectorAll('[data-kelo-look]').length"))
        return await real(page, key, **kw)
    monkeypatch.setattr(env.session, "shoot", shoot)
    jpeg, w, h, n = await env.session.look()
    assert jpeg and jpeg[:2] == b"\xff\xd8" and w == 1000 and h > 0 and n >= 4   # search, Go, Next page, the frame's button
    assert seen == [1]                                                             # one host while the picture was taken
    assert await env.page.evaluate("() => document.querySelectorAll('[data-kelo-look]').length") == 0
    assert env.session._look_url["owner"] == SHOP + "/" and env.session._shot_scale["owner"] == pytest.approx(1.0)

    async def broken(page, key=None, **kw):
        raise RuntimeError("no picture")
    monkeypatch.setattr(env.session, "shoot", broken)
    assert (await env.session.look())[0] is None
    assert await env.page.evaluate("() => document.querySelectorAll('[data-kelo-look]').length") == 0


async def test_look_boxes_of_a_frame_are_moved_to_where_the_frame_sits(ctx, monkeypatch):
    env = await env_on(ctx)
    await env.run([("browser_open", {"url": SHOP + "/"})])
    browser_owner.set("owner")
    drawn = []
    real = env.page.evaluate

    async def evaluate(js, arg=None):
        if "data-kelo-look" in js and isinstance(arg, list):
            drawn.extend(arg)
        return await (real(js, arg) if arg is not None else real(js))
    monkeypatch.setattr(env.page, "evaluate", evaluate)
    await env.session.look()
    inner = next(b for b in drawn if b["ref"].startswith("f"))
    frame_el = await env.page.locator("iframe").bounding_box()
    real_btn = await env.page.frame_locator("iframe").locator("button").bounding_box()
    assert frame_el["x"] < inner["x"] and frame_el["y"] < inner["y"]
    assert abs(inner["x"] - real_btn["x"]) < 2 and abs(inner["y"] - real_btn["y"]) < 2


async def test_the_look_tool_returns_a_picture_with_the_ref_rule():
    from assistant.agent import ToolOutput
    from assistant.tools.browser_more import NO_VISION_LOOK, BrowserExtras
    from test_browser_tools import FakePage, session_with

    s = session_with(FakePage())

    async def look():
        return b"\xff\xd8jpeg", 1024, 700, 3
    s.look = look
    tool = next(t for t in BrowserExtras(s).tools() if t.name == "browser_look")
    assert tool.untrusted and not tool.requires_approval
    out = await tool.handler({})
    assert isinstance(out, ToolOutput) and out.images[0].startswith("data:image/jpeg;base64,")
    assert "1024x700 pixels" in out.head and "browser_click selector e12" in out.head and out.no_vision == NO_VISION_LOOK


# ---------------------------------------------------------------------------------------------------------------- click_at / drag
POINTS = """<style>body{margin:0} .p{position:absolute;width:160px;height:40px}</style>
<button class=p id=more style="left:20px;top:20px" onclick="document.getElementById('log').textContent='more shown'">Show more</button>
<button class=p id=pay style="left:20px;top:100px" onclick="document.getElementById('log').textContent='PAID'">Pay now</button>
<iframe style="position:absolute;left:300px;top:20px;border:0" width=300 height=150 srcdoc="<canvas width=300 height=150></canvas>"></iframe>
<input type=range id=vol min=0 max=100 value=0 style="position:absolute;left:20px;top:300px;width:300px;margin:0">
<div id=log style="position:absolute;left:20px;top:400px">-</div>"""
PAGES["/points"] = POINTS
PAGES["/elsewhere"] = "<p>elsewhere</p>"


async def point_env(ctx) -> Env:
    from assistant.tools.browser import point_tools
    env = await env_on(ctx)
    from assistant.tools.browser_more import BrowserExtras
    look = next(t for t in BrowserExtras(env.session).tools() if t.name == "browser_look")
    env.tools = [*env.tools, look, *point_tools(env.session, orders=env.orders)]
    env.by_name = {t.name: t for t in env.tools}
    return env


async def log_text(env) -> str:
    return await env.page.evaluate("() => document.getElementById('log').textContent")


async def test_click_at_needs_a_look_of_the_page_it_is_on(ctx):
    env = await point_env(ctx)
    run = await env.run([("browser_open", {"url": SHOP + "/points"}), ("browser_click_at", {"x": 100, "y": 40})])
    assert run.result("browser_click_at").is_error and "look at the page first (browser_look)" in run.result("browser_click_at").output
    run = await env.run([("browser_look", {}), ("browser_open", {"url": SHOP + "/elsewhere"}), ("browser_click_at", {"x": 100, "y": 40}),
                         ("browser_drag", {"path": [{"x": 1, "y": 1}]})])
    assert run.result("browser_click_at").is_error and "look at the page first" in run.result("browser_click_at").output
    assert run.result("browser_drag").is_error and "2 to 20 points" in run.result("browser_drag").output
    assert run.cards == []


async def test_click_at_on_an_ordinary_button_acts_without_a_card(ctx):
    env = await point_env(ctx)
    run = await env.run([("browser_open", {"url": SHOP + "/points"}), ("browser_look", {}), ("browser_click_at", {"x": 100, "y": 40})])
    assert not run.result("browser_click_at").is_error, run.result("browser_click_at").output[:300]
    assert run.cards == [] and await log_text(env) == "more shown"
    assert "more shown" in plain(run.result("browser_click_at").output)


async def test_a_point_kelo_cannot_read_always_asks_also_inside_an_order_or_a_purchase_window(ctx):
    env = await point_env(ctx)
    run = await env.run([("browser_open", {"url": SHOP + "/points"}), ("browser_look", {}), ("browser_click_at", {"x": 400, "y": 80})],
                        answer=lambda req: False)
    assert run.card_tools() == ["browser_click_at"] and "could not read what is there" in run.cards[0].detail
    browser_owner.set("owner")
    assert env.session.judged("point:400,80") == "unknown"
    # an order the user asked for, and a purchase they just allowed on this site, never cover it
    run = await env.run([("begin_order", {"site": "shop.example", "what": "a mug"}), ("browser_look", {}),
                         ("browser_click_at", {"x": 400, "y": 80})], answer=lambda req: False)
    assert run.card_tools() == ["browser_click_at"]
    browser_owner.set("owner")
    env.session.allow_purchase("shop.example")
    decide = env.by_name["browser_click_at"].auto_approve
    assert await decide({"x": 400, "y": 80}) is False
    assert env.session.purchase_active()                                   # ...and it used none of the window's clicks
    assert env.orders.covers("unknown", "shop.example", tool="browser_click", info=None, selector="", url=SHOP) is False
    from assistant.schedule_grants import ScheduleGrants
    assert ScheduleGrants.action_check(object.__new__(ScheduleGrants), "browser_click_at", {}, "shop.example", "unknown").grant is None
    # a point of a known kind inside the allowed purchase counts like a click
    assert await decide({"x": 100, "y": 120}) is True


async def test_drag_moves_a_slider(ctx):
    env = await point_env(ctx)
    run = await env.run([("browser_open", {"url": SHOP + "/points"}), ("browser_look", {}),
                         ("browser_drag", {"path": [{"x": 26, "y": 308}, {"x": 150, "y": 308}, {"x": 318, "y": 308}]})])
    assert not run.result("browser_drag").is_error, run.result("browser_drag").output[:300]
    assert int(await env.page.evaluate("() => document.getElementById('vol').value")) >= 90


# ---------------------------------------------------------------------------------------------------------------- background tabs
async def test_a_new_tab_is_kept_active_and_focused_like_a_tab_in_front():
    from assistant.tools.browser import BrowserSession

    sent = []

    class Cdp:
        async def send(self, method, params=None):
            sent.append((method, params))
            if method == "Page.setWebLifecycleState":
                raise RuntimeError("an older browser")   # one failing call never stops the others
            return {"targetInfo": {"targetId": "T", "browserContextId": "C"}} if method == "Target.getTargetInfo" else {}

    async def cdp(page):
        return Cdp()
    s = BrowserSession(cdp=cdp)
    s.locale = "ko-KR"
    await s._prepare(object(), "owner")
    assert ("Page.setWebLifecycleState", {"state": "active"}) in sent
    assert ("Emulation.setFocusEmulationEnabled", {"enabled": True}) in sent
    assert [m for m, _ in sent].index("Emulation.setLocaleOverride") < [m for m, _ in sent].index("Page.setWebLifecycleState")
    assert s._targets["owner"] == ("T", "C")


def test_for_reading_drops_the_focus_marker_and_wraps_long_text():
    from assistant.tools.page_tree import for_reading
    long = "  - generic [ref=e3]: \"" + " ".join(f"word{i}" for i in range(100)) + "\""
    got = for_reading('- generic [active] [ref=e1]:\n' + long + '\n  - button "Go" [active] [ref=e5]')
    lines = got.splitlines()
    assert "[active]" not in got and lines[0] == "- generic [ref=e1]:" and lines[-1] == '  - button "Go" [ref=e5]'
    assert all(len(ln) <= 160 for ln in lines) and len(lines) > 4
    assert lines[1].startswith("  - generic [ref=e3]:") and lines[2].startswith("      word")
    assert " ".join(ln.strip() for ln in lines[1:-1]) == long.strip()


async def test_a_typed_value_is_secret_by_the_field_and_an_unreadable_field_is_masked_whatever_its_label(ctx, monkeypatch):
    # hc-final-a: no word of a label decides a secret. A password field (type or autocomplete) is secret; a field that cannot be
    # read at all is secret when something is typed into it (fail closed); a click on an unreadable element is not.
    env = await env_on(ctx)
    first = await env.run([("browser_open", {"url": SHOP + "/pay"})])
    pay = ref_for(first.result("browser_open").output, 'button "Pay now"')

    async def unreadable(selector):
        return None
    monkeypatch.setattr(env.session, "read_target", unreadable)
    browser_owner.set("owner")
    env.session._ref_words["owner"] = {}
    await env.session.judge("browser_type", {"selector": "e999", "text": "hunter2"})
    assert env.session.judged_secret("e999") is True                              # unreadable and typed into: masked
    await env.session.judge("browser_type", {"selector": "#search-box", "text": "shoes"})
    assert env.session.judged_secret("#search-box") is True                       # unreadable, the words say "search": still masked
    await env.session.judge("browser_click", {"selector": "e998"})
    assert env.session.judged_secret("e998") is False                             # a click carries no typed value
    # a readable password field is secret by its type, whatever it is called
    async def password_field(selector):
        return {"type": "password", "autocomplete": "", "tag": "input"}
    monkeypatch.setattr(env.session, "read_target", password_field)
    await env.session.judge("browser_type", {"selector": pay, "text": "x"})
    assert env.session.judged_secret(pay) is True
