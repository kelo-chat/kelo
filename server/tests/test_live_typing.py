"""Live typing in "take control": the `edit` input (ordered steps of an IME composition, committed text, deletions and keys) goes
to the page as real DevTools input, against a fake DevTools session (here) and a real Chromium (below, skipped without it).
Typed text is never logged and never in a reply."""

from __future__ import annotations

import logging

import pytest

from assistant.tools import live as live_mod
import asyncio

from assistant.tools.live import LiveError, LiveHub

from test_browser_live import FakePage, make_session

EDIT = [["compose", "ㄱ"], ["compose", "구"], ["text", "구"], ["back", 2], ["key", "Enter"], ["compose", "", 0]]


async def held(page=None):
    sess, cdps = make_session(page or FakePage())
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1")
    hub.connected("owner", "1", +1)
    return sess, cdps[0], hub


def ime(cdp):
    return [(m, p) for m, p in cdp.sent if m.startswith("Input.")]


async def test_an_edit_runs_its_steps_in_order_as_devtools_input():
    sess, cdp, hub = await held()
    out = await hub.input("owner", "1", {"type": "edit", "steps": EDIT})
    assert out == {"ok": True, "type": "edit", "steps": 6}
    back = {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8}
    enter = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
    assert ime(cdp) == [
        ("Input.imeSetComposition", {"text": "ㄱ", "selectionStart": 1, "selectionEnd": 1}),
        ("Input.imeSetComposition", {"text": "구", "selectionStart": 1, "selectionEnd": 1}),
        ("Input.insertText", {"text": "구"}),
        ("Input.dispatchKeyEvent", {"type": "rawKeyDown", **back}), ("Input.dispatchKeyEvent", {"type": "keyUp", **back}),
        ("Input.dispatchKeyEvent", {"type": "rawKeyDown", **back}), ("Input.dispatchKeyEvent", {"type": "keyUp", **back}),
        ("Input.dispatchKeyEvent", {"type": "keyDown", **enter, "text": "\r"}), ("Input.dispatchKeyEvent", {"type": "keyUp", **enter}),
        ("Input.imeSetComposition", {"text": "", "selectionStart": 0, "selectionEnd": 0}),
    ]


async def test_a_compose_cursor_is_kept_and_defaults_to_the_end():
    sess, cdp, hub = await held()
    await hub.input("owner", "1", {"type": "edit", "steps": [["compose", "中", 0], ["compose", "中文"]]})
    assert [p for m, p in ime(cdp)] == [{"text": "中", "selectionStart": 0, "selectionEnd": 0}, {"text": "中文", "selectionStart": 2, "selectionEnd": 2}]


@pytest.mark.parametrize("steps", [
    [["text", "a"]] * 65,                      # more than 64 steps
    [["back", 201]], [["back", 0]], [["back", "2"]], [["jump", 1]], [["text", "a\nb"]], [["text", ""]], [["text", "x" * 2001]],
    [["compose", "x" * 201]], [["compose", "ab", 3]], [["compose", "ab", -1]], [["key", "F5"]], ["text"], [], "nope", [["text", 5]],
])
async def test_a_bad_edit_is_refused_before_any_step_runs(steps):
    sess, cdp, hub = await held()
    with pytest.raises(LiveError) as e:
        await hub.input("owner", "1", {"type": "edit", "steps": [["text", "ok"], *steps] if isinstance(steps, list) and steps else steps})
    assert e.value.status == 422 and e.value.code == "bad_event"
    assert ime(cdp) == []


async def test_only_the_holder_may_edit():
    sess, cdp, hub = await held()
    with pytest.raises(LiveError) as e:
        await hub.input("owner", "2", {"type": "edit", "steps": [["text", "a"]]})
    assert e.value.code == "not_controller" and ime(cdp) == []


async def test_a_stale_page_answers_busy_and_nothing_is_typed():
    sess, cdp, hub = await held()
    hub.casts["owner"].stale = True
    out = await hub.input("owner", "1", {"type": "edit", "steps": [["text", "a"]]})
    assert out["ok"] is False and out["busy"] is True and ime(cdp) == []


async def test_typed_text_is_never_logged_nor_answered(caplog):
    caplog.set_level(logging.DEBUG)
    # live.py logs to "uvicorn.error", which an earlier test that started uvicorn may have stopped propagating: listen on it directly
    lg = logging.getLogger(live_mod.log.name)
    lg.addHandler(caplog.handler)
    old_level = lg.level
    lg.setLevel(logging.DEBUG)
    try:
        await _typed_text_is_never_logged_nor_answered(caplog)
    finally:
        lg.removeHandler(caplog.handler)
        lg.setLevel(old_level)


async def _typed_text_is_never_logged_nor_answered(caplog):
    sess, cdp, hub = await held()
    out = await hub.input("owner", "1", {"type": "edit", "steps": [["compose", "비밀SECRET"], ["text", "비밀SECRET"]]})
    assert "SECRET" not in repr(out) and "비밀" not in repr(out)
    assert "SECRET" not in caplog.text and "비밀" not in caplog.text
    assert "live edit: 2 steps" in caplog.text                        # the timing line the latency measure reads: steps and milliseconds only
    with pytest.raises(LiveError) as e:
        await hub.input("owner", "1", {"type": "edit", "steps": [["text", "SECRET\n"]]})
    assert "SECRET" not in str(e.value)


async def test_with_the_compose_switch_off_a_composition_is_deleted_and_retyped(monkeypatch):
    monkeypatch.setattr(live_mod, "COMPOSE_ON", False)
    sess, cdp, hub = await held()
    await hub.input("owner", "1", {"type": "edit", "steps": [["compose", "ㄱ"], ["compose", "구"], ["compose", ""]]})
    seq = [(m, p.get("text", p.get("key"))) for m, p in ime(cdp)]
    assert seq == [("Input.insertText", "ㄱ"),
                   ("Input.dispatchKeyEvent", "Backspace"), ("Input.dispatchKeyEvent", "Backspace"), ("Input.insertText", "구"),
                   ("Input.dispatchKeyEvent", "Backspace"), ("Input.dispatchKeyEvent", "Backspace")]
    assert all(m != "Input.imeSetComposition" for m, _ in ime(cdp))


async def test_the_protocol_version_says_live_typing():
    assert live_mod.LIVE_V == 3 and "edit" in live_mod.INPUT_TYPES


# ---------------------------------------------------------------------------------------------------------- release while composing
async def test_giving_back_while_composing_keeps_what_was_shown():
    sess, cdp, hub = await held()
    await hub.input("owner", "1", {"type": "edit", "steps": [["compose", "구"]]})
    n = len(cdp.methods("Input.insertText"))
    await hub.give_back("owner", "1")
    assert cdp.methods("Input.insertText")[n:] == [{"text": "구"}]


async def test_nothing_is_committed_on_release_after_the_text_was_committed_or_the_composition_ended():
    sess, cdp, hub = await held()
    await hub.input("owner", "1", {"type": "edit", "steps": [["compose", "구"], ["text", "구"]]})
    await hub.input("owner", "1", {"type": "edit", "steps": [["compose", "ㄴ"], ["compose", ""]]})
    n = len(cdp.methods("Input.insertText"))
    await hub.give_back("owner", "1")
    assert len(cdp.methods("Input.insertText")) == n


async def test_a_hold_that_times_out_commits_the_composition_too(monkeypatch):
    from test_browser_live import Clock
    clock = Clock()
    sess, cdps = make_session(FakePage())
    hub = LiveHub(sess, clock, reaper=False)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1", by="handover")
    await hub.input("owner", "1", {"type": "edit", "steps": [["compose", "군"]]})
    clock.t += live_mod.CONTROL_GRACE + 5                       # the holder has no live connection for longer than the grace
    n = len(cdps[0].methods("Input.insertText"))
    await hub.reap()
    assert sess.controlled_by("owner") is None
    assert cdps[0].methods("Input.insertText")[n:] == [{"text": "군"}]


# ---------------------------------------------------------------------------------------------------------- a real Chromium
pw = pytest.importorskip("playwright.async_api")
HOST = "https://m.example.com"
FIELDS = """<input id=i><textarea id=t></textarea><div id=c contenteditable style="min-height:20px"></div>
<iframe id=f src="https://other.example.net/inner" style="width:300px;height:80px"></iframe>
<script>window.ev=[];for(const n of ['compositionstart','compositionupdate','compositionend'])
document.addEventListener(n,e=>ev.push(n+':'+e.data),true)</script>"""
INNER = "<body style='margin:0'><input id=i></body>"


def _serve(mobile):
    async def serve(route):
        body = INNER if route.request.url.startswith("https://other.example.net") else FIELDS
        meta = "<meta name=viewport content='width=device-width, initial-scale=1'>" if mobile else ""
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=f"<!doctype html><meta charset=utf-8>{meta}<body>{body}</body>")
    return serve


@pytest.fixture(params=["desktop", "phone"])
async def chromium(request):
    from assistant.tools import humanize
    from assistant.tools.browser import BrowserSession, browser_owner
    from fixtures.order_harness import public as PUBLIC
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True, args=["--site-per-process"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        phone = request.param == "phone"
        c = await b.new_context(**({"viewport": {"width": 390, "height": 800}, "is_mobile": True, "has_touch": True} if phone else {"no_viewport": True}))
        await c.route("https://**/*", _serve(phone))
        page = await c.new_page()

        async def connect():
            return page
        s = BrowserSession(connect=connect, resolver=PUBLIC)
        s.overlay_pause = 0.05
        await s.open(HOST + "/")
        await asyncio.sleep(0.4)
        hub = LiveHub(s, reaper=False)
        key = browser_owner.get()
        await hub.join(key, "viewer")
        assert await hub.wait_frame(key, 0, 5) is not None
        hub.take(key, "dev")

        async def edit(steps):
            return await hub.input(key, "dev", {"type": "edit", "steps": steps})
        yield SimpleNamespace(page=page, edit=edit, hub=hub, key=key)
        await hub.give_back(key, "dev")
        await hub.leave(key, "viewer")
        await b.close()
    humanize.configure(reset=True)


import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402


async def value(page, sel="#i"):
    return await page.evaluate(f"document.querySelector('{sel}').value")


async def test_korean_shows_live_in_the_field_as_the_owner_types_it(chromium):
    page = chromium.page
    await page.focus("#i")
    seen = []
    for steps in ([["compose", "ㄱ"]], [["compose", "구"]], [["compose", "군"]], [["text", "구"], ["compose", "니"]],
                  [["compose", "ㄴ"]], [["compose", ""]], [["text", "ㄴ"]], [["back", 1]]):
        await chromium.edit(steps)
        seen.append(await value(page))
    assert seen == ["ㄱ", "구", "군", "구니", "구ㄴ", "구", "구ㄴ", "구"]
    ev = await page.evaluate("window.ev")
    assert ev[0].startswith("compositionstart") and any(e.startswith("compositionend") for e in ev)


async def test_a_composition_that_is_committed_stays_and_a_textarea_takes_enter_as_a_newline(chromium):
    page = chromium.page
    await page.focus("#t")
    await chromium.edit([["compose", "안"], ["text", "안"], ["key", "Enter"], ["text", "녕"]])
    assert await value(page, "#t") == "안\n녕"


async def test_japanese_in_an_editable_region(chromium):
    page = chromium.page
    await page.focus("#c")
    await chromium.edit([["compose", "かんじ"], ["compose", "漢字"]])
    assert await page.evaluate("document.querySelector('#c').textContent") == "漢字"
    await chromium.edit([["text", "漢字"]])
    assert await page.evaluate("document.querySelector('#c').textContent") == "漢字"


async def test_typing_reaches_a_focused_cross_origin_iframe(chromium):
    page = chromium.page
    inner = page.frame_locator("#f").locator("#i")
    await inner.click()
    await chromium.edit([["compose", "ㄱ"], ["compose", "구"]])
    assert await inner.input_value() == "구"
    await chromium.edit([["text", "구"], ["key", "Tab"]])
    assert await inner.input_value() == "구"


# ---------------------------------------------------------------------------------------------------------- the focused field
async def look(page):
    return await page.evaluate(live_mod.EDITING_JS)


FOCUS_PAGE = """<input id=pw type=password><input id=n inputmode=numeric><input id=tel type=tel><textarea id=t></textarea>
<input id=a><input id=b name=other><button id=btn>go</button><input id=cc autocomplete="cc-number">
<iframe id=same srcdoc="<input id=si type=email>" style="width:200px;height:40px"></iframe>
<iframe id=cross src="https://other.example.net/inner" style="width:200px;height:40px"></iframe>"""


async def test_the_focus_look_says_what_keyboard_the_field_fits(chromium):
    page = chromium.page
    await page.set_content(FOCUS_PAGE)
    got = {}
    for sel in ("#pw", "#n", "#tel", "#t", "#a", "#cc", "#c"):
        if await page.query_selector(sel) is None:
            continue
        await page.focus(sel)
        got[sel] = (await look(page))["kind"]
    assert got == {"#pw": "secret", "#n": "number", "#tel": "tel", "#t": "multiline", "#a": "text", "#cc": "secret"}
    await page.focus("#btn")
    assert (await look(page))["editing"] is False
    await page.evaluate("document.activeElement.blur()")
    assert (await look(page)) == {"editing": False, "kind": "text", "field": ""}


async def test_the_focus_look_follows_a_same_origin_frame_and_counts_a_cross_origin_one_as_a_text_field(chromium):
    page = chromium.page
    await page.set_content(FOCUS_PAGE)
    await page.frame_locator("#same").locator("#si").focus()
    inner = await look(page)
    assert inner["editing"] is True and inner["kind"] == "email"
    await page.evaluate("document.querySelector('#cross').focus()")
    outer = await look(page)
    assert outer["editing"] is True and outer["kind"] == "text" and outer["field"].startswith("frame:")


async def test_two_fields_differ_one_field_is_stable_and_nothing_of_the_page_changes(chromium):
    page = chromium.page
    await page.set_content(FOCUS_PAGE)
    before = await page.evaluate("Object.getOwnPropertyNames(window).sort().join()")
    html = await page.evaluate("document.documentElement.outerHTML")
    await page.focus("#a")
    a1 = (await look(page))["field"]
    assert a1 == (await look(page))["field"]
    await page.focus("#b")
    assert (await look(page))["field"] != a1
    await page.focus("#a")
    assert (await look(page))["field"] == a1
    assert await page.evaluate("Object.getOwnPropertyNames(window).sort().join()") == before
    assert await page.evaluate("document.documentElement.outerHTML") == html   # no attribute or node was added
    await page.evaluate("document.querySelector('#a').value='secret-value'")
    assert "secret-value" not in repr(await look(page))


async def test_the_server_hashes_the_field_and_the_meta_line_carries_kind_field_and_live_v(chromium, monkeypatch):
    monkeypatch.setattr(live_mod, "EDITING_EVERY", 0.0)
    page = chromium.page
    await page.focus("#i")
    for _ in range(40):                  # a look that was already in flight (from a frame) may still carry the answer from before the focus
        meta = await chromium.hub.meta(chromium.key)
        if meta["editing"]:
            break
        await asyncio.sleep(0.05)
    assert meta["editing"] is True and meta["kind"] == "text" and meta["live_v"] == 3
    assert len(meta["field"]) == 8 and all(c in "0123456789abcdef" for c in meta["field"])
    cast = chromium.hub.casts[chromium.key]
    assert cast.field_id == meta["field"] and cast.kind == "text"


# ---------------------------------------------------------------------------------------------------------- pointer release -> a quick look
import struct  # noqa: E402

from assistant.tools.vnc import ClientFilter  # noqa: E402

PRESS = struct.pack(">BBHH", 5, 1, 10, 20)
RELEASE = struct.pack(">BBHH", 5, 0, 10, 20)
MOVE = struct.pack(">BBHH", 5, 0, 30, 40)


def test_a_kept_pointer_release_calls_on_release_once_and_nothing_else_does():
    calls = []
    f = ClientFilter(lambda: True, on_release=lambda: calls.append(1))
    f.feed(b"RFB 003.008\n" + b"\x01" + b"\x01")
    f.feed(MOVE)
    f.feed(PRESS)
    assert calls == []
    f.feed(RELEASE)
    assert calls == [1]
    f.feed(MOVE)
    assert calls == [1]


def test_a_dropped_release_and_one_split_at_every_byte_behave():
    calls = []
    f = ClientFilter(lambda: False, on_release=lambda: calls.append(1))   # not the holder: dropped
    f.feed(b"RFB 003.008\n\x01\x01" + PRESS + RELEASE)
    assert calls == []
    for cut in range(1, len(PRESS + RELEASE)):
        calls.clear()
        g = ClientFilter(lambda: True, on_release=lambda: calls.append(1))
        g.feed(b"RFB 003.008\n\x01\x01")
        data = PRESS + RELEASE
        g.feed(data[:cut])
        g.feed(data[cut:])
        assert calls == [1], cut


def test_a_failing_on_release_never_breaks_the_relay():
    def boom():
        raise RuntimeError("x")
    f = ClientFilter(lambda: True, on_release=boom)
    f.feed(b"RFB 003.008\n\x01\x01")
    assert f.feed(PRESS + RELEASE) == PRESS + RELEASE


async def test_a_poke_after_a_release_makes_the_meta_line_follow_a_focus_move_in_well_under_the_poll(monkeypatch):
    monkeypatch.setattr(live_mod, "EDITING_EVERY", 10.0)         # the ordinary poll would take ten seconds
    monkeypatch.setattr(live_mod, "POKE_FIRST", 0.01)
    monkeypatch.setattr(live_mod, "POKE_SECOND", 0.05)
    sess, cdp, hub = await held()
    answers = {"v": {"editing": False, "kind": "text", "field": ""}}
    orig = cdp.send

    async def send(method, params=None):
        if method == "Runtime.evaluate" and (params or {}).get("expression") == live_mod.EDITING_JS:
            cdp.sent.append((method, dict(params)))
            return {"result": {"value": answers["v"]}}
        return await orig(method, params)
    cdp.send = send
    gate = live_mod.FrameGate()
    gate.enabled = False
    seen = []

    async def out(m):
        seen.append(m)
    task = asyncio.ensure_future(live_mod.pump_meta(hub, "owner", out, gate, every=10.0))
    await asyncio.sleep(0.1)
    assert seen and seen[0]["editing"] is False and seen[0]["kind"] == "text" and seen[0]["live_v"] == 3
    answers["v"] = {"editing": True, "kind": "number", "field": "INPUT|number||x||1,2,3,4"}
    hub.poke_editing("owner")
    await asyncio.sleep(0.3)
    task.cancel()
    assert seen[-1]["editing"] is True and seen[-1]["kind"] == "number" and len(seen[-1]["field"]) == 8
    assert len(seen) == 2


async def test_the_frame_header_and_the_poll_headers_carry_kind_and_field():
    sess, cdp, hub = await held()
    cast = hub.casts["owner"]
    cast.editing, cast.kind, cast.field_id = True, "email", "abcd1234"
    cdp.frame(sid=2)
    f = await hub.wait_frame("owner", 1, 1.0)
    h = f.header()
    assert h["kind"] == "email" and h["field"] == "abcd1234" and h["live_v"] == 3
