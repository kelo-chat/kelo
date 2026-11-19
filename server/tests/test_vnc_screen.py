"""tools/vnc.XScreen against an in-process RFB server, and the VncScreen arbiter with fakes (no browser, no X)."""

import asyncio
import struct

import pytest

from assistant.tools.vnc import DEFAULT_SIZE, VncError, VncScreen, VncUnavailable, XScreen

from fake_rfb import FakeRfb


# ---------------------------------------------------------------------------------------------------------- XScreen
async def test_resize_follows_the_rfb_steps_and_the_screen_has_the_new_size():
    fake = FakeRfb()
    port = await fake.start()
    try:
        await XScreen("127.0.0.1", port).resize(500, 900)
    finally:
        await fake.stop()
    assert fake.size == (500, 900)
    assert fake.handshakes == [b"RFB 003.008\n\x01\x01"]                       # RFB 3.8, security None, shared
    assert fake.encodings == [[0, -308]]                                         # Raw and ExtendedDesktopSize
    assert fake.requests == [(0, 0, 0, 1, 1)] * 2                                # not incremental, 1 x 1: for the layout, and again to get the answer
    assert fake.resizes == [(500, 900, 7, 1)]                                    # the screen id and flags the server itself listed


async def test_size_reads_the_server_init():
    fake = FakeRfb(size=(412, 915))
    port = await fake.start()
    try:
        assert await XScreen("127.0.0.1", port).size() == (412, 915)
    finally:
        await fake.stop()
    assert fake.resizes == []


async def test_a_screen_that_already_has_the_size_is_not_asked_again():
    fake = FakeRfb(size=(500, 900))
    port = await fake.start()
    try:
        await XScreen("127.0.0.1", port).resize(500, 900)
    finally:
        await fake.stop()
    assert fake.resizes == [] and fake.encodings == []


async def test_a_refused_size_raises():
    fake = FakeRfb(resize_status=1)
    port = await fake.start()
    try:
        with pytest.raises(VncError, match="refused 500x900"):
            await XScreen("127.0.0.1", port).resize(500, 900)
    finally:
        await fake.stop()
    assert fake.size == DEFAULT_SIZE


async def test_a_server_that_never_answers_raises_within_the_timeout():
    fake = FakeRfb(answer_update=False)
    port = await fake.start()
    try:
        started = asyncio.get_running_loop().time()
        with pytest.raises(VncError):
            await XScreen("127.0.0.1", port, timeout=0.3).resize(500, 900)
        assert asyncio.get_running_loop().time() - started < 2
    finally:
        await fake.stop()


async def test_nothing_listening_raises_vnc_error_not_a_connection_error():
    fake = FakeRfb()
    port = await fake.start()
    await fake.stop()
    with pytest.raises(VncError):
        await XScreen("127.0.0.1", port, timeout=1).resize(500, 900)


async def test_a_server_that_wants_a_password_raises():
    async def client(reader, writer):
        writer.write(b"RFB 003.008\n")
        await reader.readexactly(12)
        writer.write(b"\x01\x02")   # only VNC authentication
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()

    server = await asyncio.start_server(client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(VncError, match="password"):
            await XScreen("127.0.0.1", port).size()
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------------------------------------- VncScreen
class FakeCdp:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self.detached = False

    async def send(self, method, params=None):
        self.sent.append((method, dict(params or {})))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 11}
        return {}

    async def detach(self):
        self.detached = True

    def methods(self):
        return [m for m, _ in self.sent]


class FakePage:
    def is_closed(self):
        return False


class FakeSession:
    def __init__(self, keys=("owner", "other")):
        self.pages = {k: FakePage() for k in keys}
        self.tab = {k: FakeCdp() for k in keys}

    def raw_page(self, key):
        return self.pages.get(key)

    def tab_cdp(self, key):
        return self.tab.get(key)

    def target_id(self, key):
        return f"target-{key}"

    async def cdp_session(self, page):
        return FakeCdp()


class FakeX:
    host, port = "browser", 5901

    def __init__(self, fail=False, gate=None, now=DEFAULT_SIZE):
        self.sizes: list[tuple[int, int]] = []
        self.fail, self.gate, self.now = fail, gate, now

    async def size(self):
        if self.fail:
            raise VncError("no X screen")
        return self.now

    async def resize(self, w, h):
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise VncError("no X screen")
        self.sizes.append((w, h))
        self.now = (w, h)


def screen(session=None, x=None):
    refits: list[str] = []

    async def refit(key):
        refits.append(key)

    s = VncScreen(session or FakeSession(), x or FakeX(), refit=refit)
    return s, refits


async def test_the_first_viewer_brings_the_tab_forward_sizes_the_screen_and_makes_the_window_fullscreen():
    sess, x = FakeSession(), FakeX()
    s, _ = screen(sess, x)
    assert await s.acquire("owner", "dev1") is True
    assert sess.tab["owner"].sent == [
        ("Target.activateTarget", {"targetId": "target-owner"}), ("Page.bringToFront", {}),
        ("Browser.getWindowForTarget", {"targetId": "target-owner"}),
        ("Browser.setWindowBounds", {"windowId": 11, "bounds": {"windowState": "fullscreen"}})]
    assert x.sizes == [] and s.holds("owner") and s.size_of("owner") == DEFAULT_SIZE and s.size_of("other") is None


async def test_the_window_is_made_fullscreen_after_the_screen_changed_size_not_before():
    order: list[str] = []
    sess = FakeSession()

    class Spy(FakeX):   # a screen narrower than the desktop is put back to it first (the resize)
        async def resize(self, w, h):
            order.append("resize")
            await FakeX.resize(self, w, h)

    orig = sess.tab["owner"].send

    async def send(method, params=None):
        order.append(method)
        return await orig(method, params)

    sess.tab["owner"].send = send
    s, _ = screen(sess, Spy(now=(500, 889)))
    await s.acquire("owner", "dev1")
    assert order == ["Target.activateTarget", "Page.bringToFront", "resize", "Browser.getWindowForTarget", "Browser.setWindowBounds"]


async def test_a_second_tab_is_busy_and_nothing_is_touched():
    sess, x = FakeSession(), FakeX()
    s, _ = screen(sess, x)
    assert await s.acquire("owner", "dev1")
    assert await s.acquire("other", "dev2") is False
    assert x.sizes == [] and sess.tab["other"].sent == [] and s.holds("owner")


async def test_two_requests_for_different_tabs_at_once_give_the_screen_to_one():
    gate = asyncio.Event()
    s, _ = screen(FakeSession(), FakeX(gate=gate))
    first = asyncio.ensure_future(s.acquire("owner", "dev1"))
    second = asyncio.ensure_future(s.acquire("other", "dev2"))
    await asyncio.sleep(0.05)
    gate.set()
    assert sorted(await asyncio.gather(first, second)) == [False, True]


async def test_viewers_of_the_same_tab_share_the_screen_and_the_last_one_to_leave_puts_it_back():
    sess, x = FakeSession(), FakeX()
    s, refits = screen(sess, x)
    assert await s.acquire("owner", "dev1")
    assert await s.acquire("owner", "dev2") is True
    assert x.sizes == [] and s.size_of("owner") == DEFAULT_SIZE   # the screen keeps its size
    await s.release("owner", "dev1")
    assert s.holds("owner") and x.sizes == []
    await s.release("owner", "dev2")
    assert not s.holds("owner") and x.sizes == [] and refits == ["owner"]
    assert await s.acquire("other", "dev3") is True   # free again


async def test_a_device_with_two_screens_open_keeps_the_tab_until_both_are_closed():
    s, _ = screen()
    await s.acquire("owner", "dev1")
    await s.acquire("owner", "dev1")
    await s.release("owner", "dev1")
    assert s.holds("owner")
    await s.release("owner", "dev1")
    assert not s.holds("owner")


async def test_a_release_by_one_that_never_had_the_screen_changes_nothing():
    s, _ = screen()
    await s.acquire("owner", "dev1")
    await s.release("other", "dev1")
    await s.release("owner", "stranger")
    assert s.holds("owner")


async def test_the_tab_gets_no_emulation():
    sess = FakeSession()
    s, _ = screen(sess)
    await s.acquire("owner", "dev1")
    assert not any(m.startswith("Emulation.") for m in sess.tab["owner"].methods())
    await s.release("owner", "dev1")


async def test_the_tab_keeps_the_desktop_screen_whatever_the_viewer_asks_for():
    """Owner report 2026-11-17: the live view resized the whole X screen to the phone's width, so a desktop-view tab was laid out ~500 px
    wide and coupang showed its narrow layout. A desktop-view tab is never shown on a screen narrower than the desktop's."""
    sess, x = FakeSession(), FakeX()
    s, refits = screen(sess, x)
    assert await s.acquire("owner", "dev1") is True       # the viewer's own size is not used
    assert x.sizes == [] and s.size_of("owner") == DEFAULT_SIZE
    assert await s.acquire("owner", "dev2") is True       # a second viewer does not change it either
    await s.release("owner", "dev1")
    await s.release("owner", "dev2")
    assert x.sizes == [] and not s.holds("owner") and refits == ["owner"]


async def test_the_tab_keeps_the_size_the_screen_already_has_and_leaves_it_so():
    x = FakeX(now=(1920, 1080))
    s, _ = screen(FakeSession(), x)
    await s.acquire("owner", "dev1")
    assert s.size_of("owner") == (1920, 1080) and x.sizes == []
    await s.release("owner", "dev1")
    assert x.sizes == []


async def test_a_narrow_screen_a_crashed_server_left_behind_goes_back_to_the_desktop_size():
    x = FakeX(now=(500, 889))
    s, _ = screen(FakeSession(), x)
    await s.acquire("owner", "dev1")
    assert x.sizes == [DEFAULT_SIZE] and s.size_of("owner") == DEFAULT_SIZE


async def test_a_tab_that_closes_releases_the_screen_and_tells_its_viewers():
    sess, x = FakeSession(), FakeX()
    s, refits = screen(sess, x)
    await s.acquire("owner", "dev1")
    gone = s.gone["owner"]
    sess.pages.pop("owner")
    await s.tab_closed("owner")
    assert gone.is_set() and not s.holds("owner") and x.sizes == [] and refits == []
    await s.release("owner", "dev1")   # the closing socket's own release finds nothing to do
    assert await s.acquire("other", "dev2") is True


async def test_the_closing_of_another_tab_changes_nothing():
    s, _ = screen()
    await s.acquire("owner", "dev1")
    await s.tab_closed("other")
    assert s.holds("owner") and not s.gone["owner"].is_set()


async def test_a_screen_that_cannot_be_sized_is_unavailable_and_stays_free():
    x = FakeX(fail=True)
    s, _ = screen(FakeSession(), x)
    with pytest.raises(VncUnavailable):
        await s.acquire("owner", "dev1")
    assert not s.holds("owner")
    x.fail = False
    assert await s.acquire("other", "dev2") is True


async def test_a_step_of_bringing_the_tab_forward_that_fails_does_not_stop_the_screen():
    sess, x = FakeSession(), FakeX()

    async def broken(method, params=None):
        raise RuntimeError("target gone")

    sess.tab["owner"].send = broken
    s, _ = screen(sess, x)
    assert await s.acquire("owner", "dev1") is True and x.sizes == []


async def test_a_tab_without_its_own_devtools_session_gets_a_temporary_one():
    sess = FakeSession()
    temporary = FakeCdp()
    sess.tab_cdp = lambda key: None

    async def cdp_session(page):
        return temporary

    sess.cdp_session = cdp_session
    s, _ = screen(sess)
    await s.acquire("owner", "dev1")
    assert "Target.activateTarget" in temporary.methods() and temporary.detached is True


# ---------------------------------------------------------------------------------------------------------- the live hub's own size override
async def test_viewing_is_true_between_the_first_acquire_and_the_last_release_of_that_tab():
    s, _ = screen()
    assert s.viewing("owner") is False
    assert await s.acquire("owner", "dev1") is True
    assert await s.acquire("owner", "dev2") is True
    assert s.viewing("owner") is True and s.viewing("other") is False
    await s.release("owner", "dev1")
    assert s.viewing("owner") is True
    await s.release("owner", "dev2")
    assert s.viewing("owner") is False


# ---------------------------------------------------------------------------------------------------------- VncScreens (work browser)
from assistant.tools.vnc import VncScreens, WORK_VNC_PORT  # noqa: E402
from assistant.tools.work_browser import work_tab  # noqa: E402


def pair(session):
    main = VncScreen(session, FakeX())
    work = VncScreen(session, FakeX())
    work.xscreen.port = WORK_VNC_PORT
    return VncScreens(main, work, work_tab), main, work


async def test_the_owners_tab_and_a_job_tab_each_have_their_own_screen_and_neither_is_busy():
    session = FakeSession(keys=("owner", "task:w1", "task:w2"))
    vs, main, work = pair(session)
    assert await vs.acquire("owner", "phone")
    assert await vs.acquire("task:w1", "viewer")     # the work screen is free: not "busy"
    assert main.key == "owner" and work.key == "task:w1"
    assert vs.holds("owner") and vs.holds("task:w1") and vs.viewing("task:w1")
    assert vs.upstream_of("owner") == main.upstream and vs.upstream_of("task:w1") == (work.upstream[0], WORK_VNC_PORT)


async def test_a_second_work_tab_is_busy_while_the_work_screen_shows_the_first():
    session = FakeSession(keys=("task:w1", "task:w2"))
    vs, main, work = pair(session)
    assert await vs.acquire("task:w1", "viewer")
    assert not await vs.acquire("task:w2", "viewer")
    await vs.release("task:w1", "viewer")
    assert await vs.acquire("task:w2", "viewer")


async def test_a_new_work_window_brings_the_held_work_tab_forward_again_and_a_free_screen_is_left_alone():
    session = FakeSession(keys=("task:w1",))
    vs, main, work = pair(session)
    fronted = []

    async def refront():
        fronted.append(work.key)

    work.refront = refront
    await vs.work_window_opened("task:w1")       # nothing held: nothing to bring forward
    assert fronted == []
    await vs.acquire("task:w1", "viewer")
    await vs.work_window_opened("task:w1")
    assert fronted == ["task:w1"]


async def test_tab_closed_and_gone_are_routed_to_the_screen_that_shows_the_tab():
    session = FakeSession(keys=("task:w1",))
    vs, main, work = pair(session)
    await vs.acquire("task:w1", "viewer")
    gone = vs.gone["task:w1"]
    await vs.tab_closed("task:w1")
    assert gone.is_set() and work.key is None and vs.size_of("task:w1") is None


async def test_without_a_work_screen_every_tab_is_on_the_main_screen():
    session = FakeSession(keys=("task:w1",))
    main = VncScreen(session, FakeX())
    vs = VncScreens(main, None, work_tab)
    assert await vs.acquire("task:w1", "viewer") and main.key == "task:w1"
    assert vs.upstream_of("task:w1") == main.upstream


async def test_a_tab_switch_while_held_brings_the_new_tab_forward():
    """C1d: the owner switches tabs of the held task window: the screen brings the new active tab forward; a free screen is left alone."""
    session = FakeSession(keys=("task:w1",))
    vs, main, work = pair(session)
    fronted = []

    async def refront():
        fronted.append(work.key)

    work.refront = refront
    await vs.tab_switched("task:w1")       # nothing held
    assert fronted == []
    await vs.acquire("task:w1", "viewer")
    await vs.tab_switched("task:w1")
    assert fronted == ["task:w1"]
    await vs.tab_switched("owner")         # the main screen holds nothing: no call there
    assert fronted == ["task:w1"]
