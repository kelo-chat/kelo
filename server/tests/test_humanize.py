"""Human-paced browser input (tools/humanize.py): the path and timing generators, the fallbacks to the plain calls, and (when Playwright's
Chromium is installed) a real page that is clicked, typed into and scrolled. Pages come from set_content; nothing goes to the network."""

import asyncio
import math
import random

import pytest

from assistant.tools import humanize as h
from assistant.tools.humanize import TIMING, Timing


def dist_to_line(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    return abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / math.hypot(x2 - x1, y2 - y1)


@pytest.mark.parametrize("seed", range(20))
def test_the_pointer_path_curves_ends_exactly_on_target_and_stays_on_the_page(seed):
    rng = random.Random(seed)
    start, end = (100.0, 600.0), (900.0, 150.0)
    pts = h.pointer_path(start, end, rng, bounds=(1280, 800))
    assert pts[-1] == end
    assert max(dist_to_line(p, start, end) for p in pts) > 8          # not a straight line
    assert all(0 <= x <= 1279 and 0 <= y <= 799 for x, y in pts)
    assert h.TIMING.move_min_steps <= len(pts) <= 2 * h.TIMING.move_max_steps


def test_short_moves_still_bend_and_tiny_ones_jump():
    pts = h.pointer_path((10, 10), (60, 10), random.Random(3))
    assert max(abs(y - 10) for _, y in pts) >= 2
    assert h.pointer_path((10, 10), (10.4, 10), random.Random(3)) == [(10.4, 10)]


def test_jitter_is_bounded():
    tm = Timing(curve_min=0, curve_max=0, curve_min_px=0, overshoot_chance=0, jitter_px=5, jitter_max_px=2)
    pts = h.pointer_path((0, 100), (1000, 100), random.Random(1), tm)
    assert all(abs(y - 100) <= 2 + 1e-9 for _, y in pts)
    assert len({round(y, 3) for _, y in pts}) > 3                    # but it is there


def test_steps_and_duration_grow_with_distance_and_shrink_with_target_size():
    assert h.step_count(20) < h.step_count(400) <= TIMING.move_max_steps
    assert h.move_seconds(50, 40) < h.move_seconds(800, 40)
    assert h.move_seconds(800, 200) < h.move_seconds(800, 10)
    assert TIMING.move_min_s <= h.move_seconds(1, 1000) and h.move_seconds(10_000, 1) <= TIMING.move_max_s


def test_long_moves_sometimes_overshoot_and_come_back():
    start, end = (0.0, 400.0), (1000.0, 400.0)
    over = 0
    for seed in range(200):
        pts = h.pointer_path(start, end, random.Random(seed))
        assert pts[-1] == end
        over += any(x > end[0] + 2 for x, _ in pts)
    assert 10 < over < 80                                             # around overshoot_chance (0.18) of 200


def test_click_points_stay_inside_the_box_off_the_edges_and_lean_to_the_centre():
    box = {"x": 200, "y": 300, "width": 120, "height": 40}
    rng = random.Random(7)
    pts = [h.click_point(box, rng) for _ in range(2000)]
    m = TIMING.edge_margin
    assert all(200 + 120 * m - 1e-9 <= x <= 320 - 120 * m + 1e-9 and 300 + 40 * m - 1e-9 <= y <= 340 - 40 * m + 1e-9 for x, y in pts)
    near = sum(abs(x - 260) < 15 and abs(y - 320) < 5 for x, y in pts)
    assert near > len(pts) * 0.35
    assert len({(round(x), round(y)) for x, y in pts}) > 100          # not the same spot each time
    assert h.click_point({"x": 5, "y": 5, "width": 1, "height": 1}, rng) == (5.5, 5.5)


def test_key_gaps_vary_pause_after_spaces_and_punctuation_and_fit_the_budget():
    rng = random.Random(11)
    text = "hello world, this is a test. " * 2
    gaps = h.key_gaps(text, rng)
    assert len(gaps) == len(text) and len({round(g, 4) for g in gaps}) > len(text) // 2
    plain = [g for c, g in zip(text, gaps) if c.isalpha()]
    after = [g for c, g in zip(text, gaps) if c in ".,"]
    assert sum(after) / len(after) > sum(plain) / len(plain)
    assert all(g >= TIMING.key_min_s * 0.99 for g in plain) and sum(gaps) <= TIMING.type_budget_s + 1e-9
    long = h.key_gaps("x" * 3000, random.Random(1))
    assert abs(sum(long) - TIMING.type_budget_s) < 1e-6                # long text is typed faster, not for minutes


def test_the_same_seed_gives_the_same_input():
    a, b = random.Random(42), random.Random(42)
    assert h.pointer_path((0, 0), (500, 300), a) == h.pointer_path((0, 0), (500, 300), b)
    assert h.key_gaps("password1!", a) == h.key_gaps("password1!", b)
    assert h.wheel_steps(900, a) == h.wheel_steps(900, b)
    assert h.click_point({"x": 0, "y": 0, "width": 80, "height": 30}, a) == h.click_point({"x": 0, "y": 0, "width": 80, "height": 30}, b)


def test_secrets_are_typed_exactly_and_ordinary_text_may_get_a_fixed_typo():
    tm = Timing(typo_chance=0.5)
    secret = h.typing_plan("hunter2password", random.Random(5), tm, secret=True)
    assert [k for kind, k, _ in secret] == list("hunter2password") and all(kind == "type" for kind, _, _ in secret)
    plain = h.typing_plan("hello there friend", random.Random(5), tm)
    assert any(k == "Backspace" for _, k, _ in plain)
    out = []
    for kind, k, _ in plain:                                         # replaying the plan gives back the text
        out.pop() if k == "Backspace" else out.append(k)
    assert "".join(out) == "hello there friend"
    assert all(k != "Backspace" for _, k, _ in h.typing_plan("hello there friend", random.Random(5)))   # off by default


def test_wheel_steps_are_uneven_add_up_and_sometimes_step_back():
    backs = 0
    for seed in range(50):
        steps = h.wheel_steps(1500, random.Random(seed))
        assert abs(sum(d for d, _ in steps) - 1500) < 1
        assert len({abs(d) for d, _ in steps}) > 3 and len({round(p, 3) for _, p in steps}) > 3
        backs += any(d < 0 for d, _ in steps)
    assert backs > 3
    assert all(d < 0 for d, _ in h.wheel_steps(-400, random.Random(1)) if abs(d) > TIMING.scroll_back_px[1])
    assert h.wheel_steps(0.2, random.Random(1)) == []


def test_off_by_default_in_tests_and_by_the_environment(monkeypatch):
    assert not h.enabled()                                           # conftest turns it off
    h.configure(reset=True)
    monkeypatch.setenv("ASSISTANT_HUMAN_INPUT", "0")
    assert not h.enabled()
    monkeypatch.setenv("ASSISTANT_HUMAN_INPUT", "1")
    assert h.enabled()
    monkeypatch.delenv("ASSISTANT_HUMAN_INPUT")
    assert h.enabled()                                               # on unless switched off


# ---------------------------------------------------------------------------------------------------------------- against fakes

class Mouse:
    def __init__(self):
        self.moves, self.wheels = [], []

    async def move(self, x, y, **kw):
        self.moves.append((x, y))

    async def wheel(self, dx, dy):
        self.wheels.append(dy)


class Loc:
    def __init__(self, page, box, value=""):
        self.page_, self.box, self.value, self.clicks, self.keys, self.fills = page, box, value, [], [], []

    async def bounding_box(self, timeout=None):
        return self.box

    async def scroll_into_view_if_needed(self, timeout=None):
        pass

    async def click(self, position=None, delay=None, timeout=None):
        self.clicks.append((position, delay))

    async def input_value(self, timeout=None):
        return self.value

    async def fill(self, v, timeout=None):
        self.fills.append(v)
        self.value = v

    async def press_sequentially(self, ch, timeout=None):
        self.keys.append(ch)
        self.value += ch

    async def press(self, key, timeout=None):
        self.keys.append(key)
        if key == "Backspace":
            self.value = self.value[:-1]

    @property
    def first(self):
        return self


class Page:
    viewport_size = {"width": 1280, "height": 800}

    def __init__(self, box):
        self.mouse, self.loc, self.plain = Mouse(), Loc(self, box), []

    def locator(self, sel):
        return self.loc


@pytest.fixture
def on():
    h.configure(enabled=True, seed=1234, speed=0)
    yield
    h.configure(reset=True, enabled=False)


async def test_a_click_moves_along_a_curve_and_presses_inside_the_box(on):
    page = Page({"x": 400, "y": 300, "width": 100, "height": 30})

    async def plain(ms):
        page.plain.append(ms)
    await h.click(page, "#go", timeout_ms=10_000, fallback=plain)
    assert page.plain == [] and len(page.loc.clicks) == 1
    pos, delay = page.loc.clicks[0]
    assert 0 < pos["x"] < 100 and 0 < pos["y"] < 30 and delay == 0   # speed 0: no held time
    assert len(page.mouse.moves) >= TIMING.move_min_steps
    last = h.last_pointer(page)
    assert last == (400 + pos["x"], 300 + pos["y"])
    before = len(page.mouse.moves)
    await h.click(page, "#go", timeout_ms=10_000, fallback=plain)   # the next move starts where the last one ended
    first = page.mouse.moves[before]
    assert math.hypot(first[0] - last[0], first[1] - last[1]) < 40


async def test_an_element_without_a_box_uses_the_plain_call_with_its_full_timeout(on):
    page = Page(None)
    got = []

    async def plain(ms):
        got.append(ms)
    await h.click(page, "#hidden", timeout_ms=10_000, fallback=plain)
    await h.type_text(page, "abc", selector="#hidden", timeout_ms=10_000, fallback=plain)
    assert got == [10_000, 10_000] and page.loc.clicks == [] and page.mouse.moves == []


async def test_a_page_without_a_mouse_or_switched_off_uses_the_plain_call(on):
    class Bare:
        pass
    got = []

    async def plain(ms):
        got.append(ms)
    await h.click(Bare(), "#x", timeout_ms=5, fallback=plain)
    h.configure(enabled=False)
    page = Page({"x": 0, "y": 0, "width": 10, "height": 10})
    await h.click(page, "#x", timeout_ms=7, fallback=plain)
    await h.type_text(page, "x", selector="#x", timeout_ms=8, fallback=plain)
    await h.dwell(page)
    assert got == [5, 7, 8] and page.mouse.moves == []


async def test_typing_replaces_the_value_key_by_key_and_falls_back_when_the_field_disagrees(on):
    page = Page({"x": 100, "y": 100, "width": 200, "height": 30})
    page.loc.value = "old"
    plain = []

    async def fill(ms):
        plain.append(ms)
    await h.type_text(page, "s3cret!", selector="#pw", secret=True, timeout_ms=10_000, fallback=fill)
    assert page.loc.fills == [""] and page.loc.keys == list("s3cret!") and page.loc.value == "s3cret!" and plain == []

    class Swallow(Loc):                                              # a field whose script eats the keys
        async def press_sequentially(self, ch, timeout=None):
            pass
    page.loc = Swallow(page, page.loc.box)
    await h.type_text(page, "abc", selector="#q", timeout_ms=10_000, fallback=fill)
    assert plain == [10_000]
    await h.type_text(page, "two\nlines", selector="#q", timeout_ms=9, fallback=fill)   # control characters: plain fill
    assert plain == [10_000, 9]


async def test_an_element_below_the_fold_is_scrolled_to_with_the_wheel(on):
    page = Page({"x": 100, "y": 2400, "width": 120, "height": 30})
    loc = page.loc

    class Scrolling(Mouse):
        async def wheel(self, dx, dy):
            self.wheels.append(dy)
            loc.box = dict(loc.box, y=loc.box["y"] - dy)
    page.mouse = Scrolling()
    await h.click(page, "#far", timeout_ms=10_000, fallback=None)
    assert len(page.mouse.wheels) > 5 and len(loc.clicks) == 1
    assert 800 * TIMING.view_band[0] <= loc.box["y"] + 15 <= 800 * TIMING.view_band[1]


async def test_dwell_drifts_the_pointer_a_little(on):
    page = Page(None)
    await h.dwell(page)
    xs = [x for x, _ in page.mouse.moves]
    ys = [y for _, y in page.mouse.moves]
    assert page.mouse.moves and max(xs) - min(xs) < 2 * TIMING.drift_px[1] + 10 and max(ys) - min(ys) < 2 * TIMING.drift_px[1] + 10


# ---------------------------------------------------------------------------------------------------------------- a real browser

pw = pytest.importorskip("playwright.async_api")


@pytest.fixture
async def chromium():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        page = await (await b.new_context(viewport={"width": 1000, "height": 700})).new_page()
        yield page
        await b.close()


PAGE = """<!doctype html><body style="margin:0;height:3000px">
<input id=q style="position:absolute;left:100px;top:100px;width:300px;height:30px">
<button id=go style="position:absolute;left:600px;top:2200px;width:120px;height:40px">Go</button>
<script>
  window.moves = []; window.keys = []; window.clicks = [];
  addEventListener('mousemove', e => moves.push([e.clientX, e.clientY]));
  addEventListener('keydown', e => keys.push([e.key, performance.now()]));
  go.addEventListener('click', e => { const r = go.getBoundingClientRect(); clicks.push([e.clientX - r.left, e.clientY - r.top]); });
</script>"""


async def test_a_real_page_gets_curved_moves_typed_keys_and_a_wheel_scroll(chromium, on):
    h.configure(speed=0.05)
    page = chromium
    await page.set_content(PAGE)
    await h.type_text(page, "hello kelo", selector="#q", timeout_ms=5_000, fallback=lambda ms: page.fill("#q", "hello kelo", timeout=ms))
    assert await page.input_value("#q") == "hello kelo"
    keys = await page.evaluate("keys")
    assert [k for k, _ in keys] == list("hello kelo")
    gaps = [b - a for (_, a), (_, b) in zip(keys, keys[1:])]
    assert len({round(g) for g in gaps}) > 1                         # not one fixed interval
    await h.click(page, "#go", timeout_ms=5_000, fallback=lambda ms: page.click("#go", timeout=ms))
    clicks = await page.evaluate("clicks")
    assert len(clicks) == 1 and 0 < clicks[0][0] < 120 and 0 < clicks[0][1] < 40
    assert await page.evaluate("scrollY") > 1000                    # reached by scrolling
    moves = await page.evaluate("moves")
    assert len(moves) > 2 * TIMING.move_min_steps


# ---------------------------------------------------------------------------------------------------------------- input-method typing

def _syllable_keys(ch):
    from assistant.tools import ime
    return ime.script_for(ch).keys(ch)


def test_hangul_keystrokes_follow_the_two_beolsik_layout():
    def show(ch):
        return [(k.code, k.shift, k.composed) for k in _syllable_keys(ch)]
    assert show("박") == [("KeyQ", False, "ㅂ"), ("KeyK", False, "바"), ("KeyR", False, "박")]
    assert show("값")[-2:] == [("KeyQ", False, "갑"), ("KeyT", False, "값")]            # compound final: two keys
    assert show("쌍")[0] == ("KeyT", True, "ㅆ")                                         # doubled consonant: Shift
    assert [k.composed for k in _syllable_keys("뷁")] == ["ㅂ", "부", "붸", "뷀", "뷁"]   # compound vowel and final
    assert show("ㅋ") == [("KeyZ", False, "ㅋ")]
    from assistant.tools import ime
    assert ime.script_for("a") is None and ime.script_for(" ") is None and not ime.uses_ime("abc 123")
    for ch in "가힣한글":                                                                # every syllable ends as itself
        assert _syllable_keys(ch)[-1].composed == ch


def test_the_plan_splits_hangul_into_keys_and_leaves_the_rest_alone():
    import random
    plan = h.ime_plan("a 한", random.Random(3), TIMING)
    assert [p[0] for p in plan] == ["type", "type", "ime"] and plan[2][1] == "한" and len(plan[2][2]) == 3
    long = h.ime_plan("가나다라마바사" * 40, random.Random(3), TIMING)
    assert sum(sum(w) for k, _, w in long if k == "ime") <= TIMING.type_budget_s + 1e-6


class Cdp:
    def __init__(self, refuse_composition=False):
        self.calls, self.refuse = [], refuse_composition

    async def __call__(self, method, params):
        if self.refuse and method == "Input.imeSetComposition":
            raise RuntimeError("not supported")
        self.calls.append((method, params))


async def test_korean_is_typed_per_key_through_the_input_method(on):
    page = Page({"x": 100, "y": 100, "width": 200, "height": 30})
    cdp, plain = Cdp(), []

    async def fill(ms):
        plain.append(ms)
    commits = []

    async def press(ch, timeout=None):                       # the commit lands in the field, like Chrome
        page.loc.keys.append(ch)
        page.loc.value += ch
    page.loc.press_sequentially = press
    orig = cdp.__call__

    async def send(method, params):
        await orig(method, params)
        if method == "Input.insertText":
            page.loc.value += params["text"]
            commits.append(params["text"])
    await h.type_text(page, "박 a", selector="#q", timeout_ms=10_000, fallback=fill, cdp=send)
    assert plain == [] and page.loc.value == "박 a" and commits == ["박"] and page.loc.keys == [" ", "a"]
    seq = [(m, p.get("key"), p.get("code"), p.get("windowsVirtualKeyCode"), p.get("text")) for m, p in cdp.calls]
    assert seq[:4] == [("Input.dispatchKeyEvent", "Process", "KeyQ", 229, None),
                       ("Input.imeSetComposition", None, None, None, "ㅂ"),
                       ("Input.dispatchKeyEvent", "ㅂ", "KeyQ", 81, None),
                       ("Input.dispatchKeyEvent", "Process", "KeyK", 229, None)]
    assert [p["text"] for m, p in cdp.calls if m == "Input.imeSetComposition"] == ["ㅂ", "바", "박"]
    assert seq[-1][0] == "Input.insertText"


async def test_when_composition_is_refused_the_same_keys_are_sent_and_only_commits_go_in(on):
    page = Page({"x": 100, "y": 100, "width": 200, "height": 30})
    cdp = Cdp(refuse_composition=True)

    async def send(method, params):
        await cdp(method, params)
        if method == "Input.insertText":
            page.loc.value += params["text"]

    async def fill(ms):
        raise AssertionError("plain fill")
    await h.type_text(page, "한글", selector="#q", timeout_ms=10_000, fallback=fill, cdp=send)
    assert page.loc.value == "한글"
    assert [m for m, _ in cdp.calls].count("Input.insertText") == 2
    assert sum(1 for m, p in cdp.calls if m == "Input.dispatchKeyEvent" and p["key"] == "Process") == 6


async def test_secrets_and_a_missing_session_keep_the_plain_typing(on):
    page = Page({"x": 100, "y": 100, "width": 200, "height": 30})
    cdp = Cdp()

    async def fill(ms):
        raise AssertionError("plain fill")
    await h.type_text(page, "한", selector="#q", secret=True, timeout_ms=10_000, fallback=fill, cdp=cdp)
    await h.type_text(page, "글", selector="#q", timeout_ms=10_000, fallback=fill)
    assert cdp.calls == [] and page.loc.keys == ["한", "글"] and page.loc.value == "글"   # each typed plainly; the second replaced the first


async def test_the_path_taken_is_logged_without_the_text(on, caplog):
    import logging
    page = Page({"x": 100, "y": 100, "width": 200, "height": 30})
    page.loc.press_sequentially = lambda ch, timeout=None: asyncio.sleep(0)    # keys that land nowhere

    async def fill(ms):
        pass
    with caplog.at_level(logging.DEBUG, logger="assistant.tools.humanize"):
        await h.type_text(page, "비밀text", selector="#q", timeout_ms=10_000, fallback=fill)
    lines = [r.getMessage() for r in caplog.records]
    assert "type_text: fallback: value differs" in lines and not any("비밀" in l or "text" in l.split(":", 1)[1].split("fallback")[0] for l in lines)


async def test_a_real_page_sees_korean_as_input_method_events(chromium, on):
    page = chromium
    await page.set_content("<input id=q><script>window.ev=[];for(const t of ['keydown','compositionstart','compositionupdate','compositionend',"
                           "'input'])q.addEventListener(t,e=>ev.push(t=='keydown'?'keydown:'+e.key+'/'+e.keyCode:t=='compositionupdate'?t+':'+e.data:t))</script>")
    session = await page.context.new_cdp_session(page)

    async def fill(ms):
        raise AssertionError("fell back to the plain fill")
    await h.type_text(page, "한a글", selector="#q", timeout_ms=5_000, fallback=fill, cdp=lambda m, p: session.send(m, p))
    assert await page.input_value("#q") == "한a글"
    ev = await page.evaluate("ev")
    assert ev[:6] == ["keydown:Process/229", "compositionstart", "compositionupdate:ㅎ", "input", "keydown:Process/229", "compositionupdate:하"]
    assert "compositionupdate:한" in ev and ev.count("compositionend") == 2 and "keydown:a/65" in ev
