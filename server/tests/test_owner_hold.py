"""The owner's hold on a live screen (browser tab or phone): a button hold ("직접 조작") ends only when the owner hands it back, or
answers the idle question with "hand back"; a hand-over hold (the app took the screen because Kelo asked) keeps the old ends.
Brief: kelo-agents/briefs/takeover-lease.md (revision 2026-11-18)."""

import asyncio

import pytest

from assistant.hold_asks import HoldAsker, HoldBook
from assistant.tools.holds import HANDS_WAIT_SECONDS, IDLE_ASK_SECONDS, Holds
from assistant.live_source import LiveSources
from assistant.tools.browser import CONTROLLED_NOTE
from assistant.tools.live import LiveError, LiveHub
from assistant.tools.vnc import ClientFilter

from test_browser_live import Clock, FakeCDP, FakePage, make_session, settle


class FakeStore:
    """The parts of the store the hold code uses: messages and meta (a dict, so it survives a 'restart' in the test)."""

    def __init__(self, meta=None):
        self.messages: list[tuple[int, str, str]] = []
        self.meta_values: dict[str, str] = dict(meta or {})

    def add_message(self, role, text, via=""):
        mid = len(self.messages) + 1
        self.messages.append((mid, role, text))
        return mid

    def meta(self, key):
        return self.meta_values.get(key, "")

    def set_meta(self, key, value):
        self.meta_values[key] = value


class FakeActivity:
    def __init__(self):
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, type_, title, body="", **kw):
        self.notes.append((type_, title, body))
        return len(self.notes)


class FakeGoalBook:
    def __init__(self):
        self.replies: dict[int, list] = {}

    def attach_replies(self, mid, questions, goal_id):
        self.replies[mid] = questions

    def detach_replies(self, mid):
        self.replies.pop(mid, None)

    def questions_of(self, mid):
        return self.replies.get(mid, [])


def browser_hub(clock, page=None):
    sess, cdps = make_session(page)
    return sess, cdps, LiveHub(sess, clock, reaper=False)


def asker_for(hub, clock, store=None, activity=None, goals=None):
    store = store or FakeStore()
    activity = activity or FakeActivity()
    goals = goals or FakeGoalBook()
    return HoldAsker(LiveSources(hub, None), store, activity, goals, agent_name=lambda: "Kelo", clock=clock), store, activity, goals


def labels(goals, mid):
    options = goals.replies[mid][0]["options"]
    return options[0], options[1]


# ------------------------------------------------------------------------------------------------------------- the holds model
def test_a_touch_renews_the_idle_clock_and_a_question_is_due_only_after_ten_minutes():
    clock = Clock()
    holds = Holds(clock)
    holds.take("owner", "1", "button")
    clock.t += IDLE_ASK_SECONDS - 1
    assert not holds.due_question("owner")
    holds.touched("owner", "1")
    clock.t += IDLE_ASK_SECONDS - 1
    assert not holds.due_question("owner")
    clock.t += 2
    assert holds.due_question("owner")
    holds.asked("owner")
    assert not holds.due_question("owner")          # one question per idle period
    holds.keep("owner")                              # "keep" starts the next period
    clock.t += IDLE_ASK_SECONDS + 1
    assert holds.due_question("owner")


def test_a_handover_hold_never_gets_an_idle_question_and_the_owners_press_makes_a_button_hold():
    clock = Clock()
    holds = Holds(clock)
    holds.take("owner", "1", "handover")
    clock.t += 3 * IDLE_ASK_SECONDS
    assert not holds.due_question("owner")
    holds.take("owner", "1", "handover")             # the app's own take again: no change
    assert holds.get("owner").kind == "handover"
    holds.take("owner", "1", "button")               # the owner presses 직접 조작: a button hold now
    assert holds.get("owner").kind == "button"
    holds.take("owner", "2", "handover")             # a move to another device keeps the kind
    assert holds.get("owner").device == "2" and holds.get("owner").kind == "button"


# ------------------------------------------------------------------------------------------------------------- browser: no return by itself
async def test_a_button_hold_never_goes_back_by_itself():
    from assistant.tools.browser import browser_owner

    clock = Clock()
    sess, _, hub = browser_hub(clock)
    sess.control_wait = 0.05
    hub.take("owner", "1")                            # a button hold (no `by`: an old app)
    hub.connected("owner", "1", +1)
    hub.connected("owner", "1", -1)
    clock.t += 3600
    await hub.reap()
    assert sess.controlled_by("owner") == "1"          # an hour away, the phone gone: still held
    token = browser_owner.set("owner")
    try:
        assert await sess.hold_for_user() == CONTROLLED_NOTE.format(minutes=1)   # a waiting step is told after its wait
    finally:
        browser_owner.reset(token)
    assert sess.controlled_by("owner") == "1"


async def test_hand_back_is_the_only_way_back_and_the_waiting_step_goes_on():
    clock = Clock()
    sess, _, hub = browser_hub(clock)
    hub.take("owner", "1")
    waiting = asyncio.ensure_future(sess.wait_for_agent_turn("owner", 5.0))
    await settle()
    assert not waiting.done()
    await hub.reap()
    assert not waiting.done()
    await hub.give_back("owner", "1")
    assert await asyncio.wait_for(waiting, 1) is True


async def test_a_handover_hold_keeps_the_30_s_grace():
    clock = Clock()
    sess, _, hub = browser_hub(clock)
    hub.take("owner", "1", by="handover")
    hub.connected("owner", "1", +1)
    clock.t += 120
    await hub.reap()
    assert sess.controlled_by("owner") == "1"          # connected: kept however long
    hub.connected("owner", "1", -1)
    clock.t += 20
    await hub.reap()
    assert sess.controlled_by("owner") == "1"          # within the grace time
    clock.t += 11
    await hub.reap()
    assert sess.controlled_by("owner") is None


async def test_only_the_holders_input_counts_as_a_touch():
    clock = Clock()
    sess, cdps, hub = browser_hub(clock)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1")
    first = hub.holds.get("owner").touched_at
    clock.t += 100
    await hub.view("owner", "1", {"width": 400, "height": 800, "density": 2})
    hub.touch("owner", "a")
    assert hub.holds.get("owner").touched_at == first   # a view, a watcher: not a touch
    with pytest.raises(LiveError):
        await hub.input("owner", "2", {"type": "tap", "x": 1, "y": 1})
    assert hub.holds.get("owner").touched_at == first   # another device's input: not a touch
    await hub.input("owner", "1", {"type": "tap", "x": 10, "y": 10})
    assert hub.holds.get("owner").touched_at == clock.t   # the holder's tap: a touch


async def test_the_idle_question_goes_out_once_after_ten_minutes():
    clock = Clock()
    sess, _, hub = browser_hub(clock)
    asker, store, activity, goals = asker_for(hub, clock)
    hub.asker = asker
    hub.take("owner", "1")
    clock.t += IDLE_ASK_SECONDS - 1
    await asker.tick()
    assert store.messages == [] and activity.notes == []
    clock.t += 2
    await asker.tick()
    assert len(store.messages) == 1 and "10" in store.messages[0][2]
    mid = store.messages[0][0]
    back, keep = labels(goals, mid)
    assert keep and back != keep
    assert [n[0] for n in activity.notes] == ["hold_idle"]
    clock.t += 30 * 60
    await asker.tick()
    assert len(store.messages) == 1 and len(activity.notes) == 1   # no second question in this idle period


async def test_a_touch_starts_a_new_idle_period_and_removes_the_buttons():
    clock = Clock()
    sess, cdps, hub = browser_hub(clock)
    asker, store, activity, goals = asker_for(hub, clock)
    hub.asker = asker
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1")
    clock.t += IDLE_ASK_SECONDS + 1
    await asker.tick()
    mid = store.messages[0][0]
    assert mid in goals.replies
    clock.t += 10
    await hub.input("owner", "1", {"type": "tap", "x": 10, "y": 10})
    assert mid not in goals.replies                    # the buttons are gone (the message stays)
    assert len(store.messages) == 1
    clock.t += IDLE_ASK_SECONDS - 5
    await asker.tick()
    assert len(store.messages) == 1                    # the clock started at the touch
    clock.t += 10
    await asker.tick()
    assert len(store.messages) == 2                    # ten minutes after the touch: a new question


async def test_answer_back_hands_it_back_and_says_so_to_the_paused_jobs():
    clock = Clock()
    sess, _, hub = browser_hub(clock)
    asker, store, activity, goals = asker_for(hub, clock)
    hub.asker = asker
    told = []
    sess.handback_listeners.append(lambda key, summary, reason, kind: told.append((key, reason, kind)))
    hub.take("owner", "1")
    clock.t += IDLE_ASK_SECONDS + 1
    await asker.tick()
    mid = store.messages[0][0]
    back, _keep = labels(goals, mid)
    assert await asker.answer(0, back) == "back"
    assert sess.controlled_by("owner") is None
    assert told == [("owner", "owner", "browser")]
    assert mid not in goals.replies


async def test_answer_keep_restarts_the_ten_minutes_and_other_text_is_not_an_answer():
    clock = Clock()
    sess, _, hub = browser_hub(clock)
    asker, store, activity, goals = asker_for(hub, clock)
    hub.asker = asker
    hub.take("owner", "1")
    clock.t += IDLE_ASK_SECONDS + 1
    await asker.tick()
    mid = store.messages[0][0]
    back, keep = labels(goals, mid)
    assert await asker.answer(0, "그냥 물어본 거예요") is None      # ordinary chat: nothing is guessed from words
    assert sess.controlled_by("owner") == "1"
    assert await asker.answer(0, keep) == "keep"
    assert sess.controlled_by("owner") == "1" and mid not in goals.replies
    clock.t += IDLE_ASK_SECONDS - 5
    await asker.tick()
    assert len(store.messages) == 1
    clock.t += 10
    await asker.tick()
    assert len(store.messages) == 2


async def test_a_held_tab_whose_page_is_gone_stays_held_and_is_asked_about_at_once():
    clock = Clock()
    page = FakePage()
    sess, _, hub = browser_hub(clock, page)
    asker, store, activity, goals = asker_for(hub, clock)
    hub.asker = asker
    hub.take("owner", "1")
    page._closed = True
    await hub.reap()
    assert sess.controlled_by("owner") == "1"
    assert hub.holds.get("owner").gone is True
    await asker.tick()
    assert len(store.messages) == 1                    # no wait for the ten minutes
    assert "restart" in store.messages[0][2] or "closed" in store.messages[0][2]


async def test_button_holds_come_back_after_a_restart(tmp_path):
    clock = Clock()
    sess, _, hub = browser_hub(clock)
    store = FakeStore()
    book = HoldBook(store)
    hub.book = book
    hub.take("owner", "1")
    assert "owner" in book.load()
    sess._pages["handover-tab"] = FakePage()
    hub.take("handover-tab", "2", by="handover")
    assert "handover-tab" not in book.load()          # hand-over holds are not kept over a restart
    # a restart: a new hub, the same saved holds
    sess2, _, hub2 = browser_hub(clock)
    hub2.book = HoldBook(store)
    for key, entry in HoldBook(store).load().items():
        hub2.restore(key, entry["device"])
    assert sess2.controlled_by("owner") == "1"
    assert hub2.holds.get("owner").gone is True and hub2.holds.get("owner").kind == "button"
    hub2.take("owner", "1")  # the same device again: no change
    await hub2.give_back("owner", "1")
    assert "owner" not in HoldBook(store).load()


async def test_another_device_may_take_a_hold_whose_holder_is_away_but_not_one_that_is_connected():
    clock = Clock()
    sess, _, hub = browser_hub(clock)
    hub.take("owner", "1")
    hub.connected("owner", "1", +1)
    with pytest.raises(LiveError) as e:
        hub.take("owner", "2")
    assert e.value.status == 409
    assert hub.hold_view("owner", "2") == {"hold": "button", "holder_away": False}
    hub.connected("owner", "1", -1)
    assert hub.hold_view("owner", "2") == {"hold": "button", "holder_away": True}
    hub.take("owner", "2")                             # moved, not given back: Kelo never gets the tab in between
    assert sess.controlled_by("owner") == "2"
    assert hub.holds.get("owner").kind == "button"
    hub.connected("owner", "2", +1)
    assert hub.hold_view("owner", "1") == {"hold": "button", "holder_away": False}


async def test_a_tap_on_a_picture_older_than_the_stopped_step_is_dropped():
    clock = Clock()
    sess, cdps, hub = browser_hub(clock)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    old_seq = hub.latest("owner").seq
    sess._steps["owner"] = asyncio.ensure_future(asyncio.sleep(30))   # an agent step is running on the tab
    hub.take("owner", "1")
    await settle(20)
    cdps[0].frame()                                    # the stopped step's last move landed: a newer picture
    await settle()
    out = await hub.input("owner", "1", {"type": "tap", "x": 10, "y": 10, "frame_seq": old_seq})
    assert out.get("stale") is True and out["ok"] is False
    assert cdps[0].methods("Input.dispatchMouseEvent") == []
    newest = hub.latest("owner").seq
    out = await hub.input("owner", "1", {"type": "tap", "x": 10, "y": 10, "frame_seq": newest})
    assert out["ok"] is True                            # a tap on the newest picture goes through and clears the fence
    assert hub.holds.get("owner").fence is None


async def test_take_without_a_running_step_does_not_stop_page_loading():
    clock = Clock()
    sess, cdps, hub = browser_hub(clock)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1")
    await settle(20)
    stops = [m for c in cdps for m, _ in c.sent if m == "Page.stopLoading"]
    assert stops == []


async def test_after_the_owner_moved_the_next_click_reads_first():
    from assistant.tools.browser import MOVED_NOTE, browser_owner

    clock = Clock()
    sess, _, hub = browser_hub(clock)
    sess.take_control("owner", "1")
    await sess.release_control("owner", moved=True)
    token = browser_owner.set("owner")
    try:
        assert await sess.hold_to_act() == MOVED_NOTE
        await sess.snapshot()                           # reading the page clears it
        assert await sess.hold_to_act() is None
    finally:
        browser_owner.reset(token)


def test_the_stale_frame_and_idle_numbers_are_the_brief_ones():
    from assistant.tools.holds import HANDS_WAIT_SECONDS as hands
    from assistant.tools import browser

    assert IDLE_ASK_SECONDS == 600.0 and hands == 300.0 and browser.CONTROL_WAIT_SECONDS == HANDS_WAIT_SECONDS


# ------------------------------------------------------------------------------------------------------------- the VNC relay
HANDSHAKE = b"RFB 003.008\n" + b"\x01" + b"\x01"     # the version and the security choice pass first
POINTER_MOVE = bytes([5, 0, 0, 100, 0, 100])          # no button: a bare move
POINTER_PRESS = bytes([5, 1, 0, 100, 0, 100])         # left button down


def test_a_bare_pointer_move_is_not_active_and_a_press_is():
    seen = []
    f = ClientFilter(lambda active: seen.append(active) or True)
    f.feed(HANDSHAKE)
    f.feed(POINTER_MOVE)
    f.feed(POINTER_PRESS)
    f.feed(bytes([5, 0, 0, 100, 0, 100]))                # the release: a changed mask
    assert seen == [False, True, True]


def test_a_lift_releases_the_button_and_the_keys_still_down():
    f = ClientFilter(lambda active: True)
    f.feed(HANDSHAKE)
    f.feed(POINTER_PRESS)
    f.feed(bytes([4, 1, 0, 0]) + (0x61).to_bytes(4, "big"))   # KeyEvent down 'a'
    lift = f.lift()
    assert lift.startswith(bytes([5, 0])) and bytes([4, 0, 0, 0]) + (0x61).to_bytes(4, "big") in lift
    assert f.lift() == b""                                # nothing left to lift


# ------------------------------------------------------------------------------------------------------------- the PIN keypad
async def test_the_pin_pad_does_not_release_a_hold_it_did_not_take():
    from assistant.tools import payment_step as ps

    sess, _ = make_session()
    sess.take_control("owner", "1")                    # the owner's own press was on before the keypad came up
    pad = ps.PinPad(sess, "owner", "mugshop.example")
    pad.held_by = "1"                                  # the keypad marks the hold it shares, but did not take it
    await pad.close()
    assert sess.controlled_by("owner") == "1"          # the owner's hold stays
    sess2, _ = make_session()
    pad2 = ps.PinPad(sess2, "owner", "mugshop.example")
    sess2.take_control("owner", "1")                   # the keypad took it itself
    pad2.held_by, pad2.took = "1", True
    await pad2.close()
    assert sess2.controlled_by("owner") is None


# ------------------------------------------------------------------------------------------------------------- the phone
def phone(**kw):
    from test_android_live import make as make_phone

    bridge, live = make_phone(**kw)
    clock = Clock()
    live.clock = clock
    return bridge, live, clock


async def test_a_phone_button_hold_never_goes_back_by_itself():
    _, live, clock = phone()
    live.take("android", "d1")
    live.connected("android", "d1", +1)
    live.connected("android", "d1", -1)
    clock.t += 3600
    await live.step()
    await live.step()
    assert live.holder("android") == "d1" and live.button_hold()


async def test_a_phone_hand_over_hold_keeps_the_grace_and_a_phone_step_waits_five_minutes_then_is_told():
    from assistant.android.live import CONTROL_WAIT, PHONE_HELD_NOTE

    _, live, clock = phone()
    assert live.control_wait == CONTROL_WAIT == 300.0
    live.take("android", "d1", by="handover")
    live.connected("android", "d1", +1)
    live.connected("android", "d1", -1)
    clock.t += 31
    await live.step()
    assert live.holder("android") is None              # the hand-over's grace: as before
    live.take("android", "d1")
    live.control_wait = 0.05
    assert await live.hold_for_user() == PHONE_HELD_NOTE.format(minutes=1)
    assert live.holder("android") == "d1"


async def test_the_phone_moved_by_hand_gives_one_moved_note_until_the_screen_is_read():
    from assistant.android.live import PHONE_MOVED_NOTE

    _, live, clock = phone()
    live.take("android", "d1")
    await live.input("android", "d1", {"type": "key", "key": "back"})
    await live.give_back("android", "d1")
    assert await live.hold_to_act() == PHONE_MOVED_NOTE
    live.take_note()                                   # the next look reads the screen (and the owner's note)
    assert await live.hold_to_act() is None


async def test_the_phone_idle_question_names_the_phone_and_a_restart_restores_its_hold():
    from assistant.hold_asks import HoldBook
    from assistant.tools.holds import IDLE_ASK_SECONDS

    _, live, clock = phone()
    store = FakeStore()
    goals = FakeGoalBook()
    asker = HoldAsker(LiveSources(None, live), store, FakeActivity(), goals, agent_name=lambda: "Kelo", clock=clock, book=HoldBook(store))
    live.asker = asker
    live.book = HoldBook(store)
    live.take("android", "d1")
    assert "android" in HoldBook(store).load()
    clock.t += IDLE_ASK_SECONDS + 1
    await asker.tick()
    assert len(store.messages) == 1 and "phone" in store.messages[0][2]
    back, keep = labels(goals, store.messages[0][0])
    assert await asker.answer(0, back) == "back"
    assert live.holder("android") is None and "android" not in HoldBook(store).load()
    live.take("android", "d1")
    # a restart: the saved hold comes back as the owner's, with no question yet (the phone is there)
    _, live2, clock2 = phone()
    live2.book = HoldBook(store)
    for key, entry in HoldBook(store).load().items():
        if key == "android":
            live2.restore(entry["device"])
    assert live2.holder("android") == "d1" and live2.button_hold()


async def test_a_waiting_phone_question_is_answered_from_any_device_and_the_keep_restarts_the_clock():
    from assistant.tools.holds import IDLE_ASK_SECONDS

    _, live, clock = phone()
    store = FakeStore()
    goals = FakeGoalBook()
    asker = HoldAsker(LiveSources(None, live), store, FakeActivity(), goals, agent_name=lambda: "Kelo", clock=clock)
    live.asker = asker
    live.take("android", "d1")
    clock.t += IDLE_ASK_SECONDS + 1
    await asker.tick()
    mid = store.messages[0][0]
    back, keep = labels(goals, mid)
    assert await asker.answer(0, keep) == "keep" and live.holder("android") == "d1"
    clock.t += IDLE_ASK_SECONDS - 5
    await asker.tick()
    assert len(store.messages) == 1


# ------------------------------------------------------------------------------------------------------------- the routes
async def test_the_control_answers_say_how_the_hold_was_taken_and_whether_its_holder_is_away(tmp_path):
    import httpx

    from assistant.server import create_app
    from test_agent_profile import app_settings
    from test_browser_live import calling
    from test_server import AUTH, Running

    sess, _ = make_session()
    sess._pages["7"] = FakePage("https://other.example.com")
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/browser/sessions/owner/control", json={"take": True, "by": "handover"})
        assert r.json()["hold"] == "handover" and r.json()["holder_away"] is True
        r = await c.post("/api/browser/sessions/owner/control", json={"take": False})
        assert r.json()["controller"] == "agent" and r.json()["hold"] == ""
        r = await c.post("/api/browser/sessions/owner/control", json={"take": True})
        assert r.json()["hold"] == "button" and r.json()["you_control"] is True
        r = await c.post("/api/browser/sessions/owner/control", json={"take": False})
        assert r.json()["controller"] == "agent"


async def test_a_touch_with_no_open_question_writes_nothing_to_the_store():
    clock = Clock()
    sess, cdps, hub = browser_hub(clock)
    store = FakeStore()
    hub.book = HoldBook(store)
    asker, _, _, _ = asker_for(hub, clock, store=store)
    hub.asker = asker
    hub.take("owner", "1")
    writes = []
    real = store.set_meta
    store.set_meta = lambda k, v: (writes.append(k), real(k, v))[1]
    hub.touched("owner", "1")
    hub.touched("owner", "1")
    assert writes == []                 # no question was open: the store is not written on each tap
