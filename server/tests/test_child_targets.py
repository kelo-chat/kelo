"""The frames and workers of a tab (tools/child_targets.py): a cross-site frame or a worker of a tab in another language gets the browser's
own identity with that language; everything else only goes on. Every child is resumed, even when a step fails. Fake socket: no browser."""

import asyncio
import json

from assistant.tools.child_targets import ChildTargets


class FakeSocket:
    """Records what is sent and answers every command at once (the reader of ChildTargets resolves nothing here)."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.sent: list[dict] = []
        self.fail = fail
        self.child: ChildTargets | None = None
        self.closed = False

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        fut = self.child._wait.pop(msg["id"])
        if msg["method"] in self.fail:
            fut.set_exception(RuntimeError("refused"))
        else:
            fut.set_result({"product": "Chrome/131.0.6778.85"} if msg["method"] == "Browser.getVersion" else {})

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):   # no events from this fake browser: the reader waits until it is cancelled
        await asyncio.Event().wait()

    def methods(self, session: str | None = None) -> list[str]:
        return [m["method"] for m in self.sent if m.get("sessionId") == session and m["method"] != "Target.setAutoAttach"]


def child(locale: str = "", fail: tuple[str, ...] = ()) -> tuple[ChildTargets, FakeSocket]:
    ws = FakeSocket(fail)
    c = ChildTargets(ws, lambda info, parent: locale)
    ws.child = c
    return c, ws


def event(kind: str, session: str = "s1", url: str = "https://site.example/", waiting: bool = True) -> dict:
    return {"sessionId": session, "waitingForDebugger": waiting,
            "targetInfo": {"type": kind, "targetId": f"t-{session}", "url": url}}


async def test_a_child_of_a_tab_without_a_language_is_only_resumed():
    c, ws = child("")
    await c.attached(event("iframe", "f2"), parent="page-session")
    await c.attached(event("worker", "w2"), parent="page-session")
    assert ws.methods("f2") == ["Runtime.runIfWaitingForDebugger"]
    assert ws.methods("w2") == ["Runtime.runIfWaitingForDebugger"]


async def test_a_child_of_a_tab_with_a_language_gets_the_browser_identity_with_that_language_then_runs():
    c, ws = child("ko-KR")
    await c.attached(event("iframe", "f5"), parent="page-session")
    await c.attached(event("worker", "w5"), parent="page-session")
    assert ws.methods("f5") == ["Emulation.setUserAgentOverride", "Emulation.setLocaleOverride", "Runtime.runIfWaitingForDebugger"]
    assert ws.methods("w5") == ["Network.setUserAgentOverride", "Emulation.setLocaleOverride", "Runtime.runIfWaitingForDebugger"]
    ua = next(m["params"] for m in ws.sent if m["method"] == "Network.setUserAgentOverride")
    assert ua["acceptLanguage"] == "ko-KR,ko,en-US,en"
    assert ua["userAgentMetadata"]["platform"] == "Linux" and ua["userAgentMetadata"]["mobile"] is False
    assert "Android" not in ua["userAgent"] and "Mobile" not in ua["userAgent"]


async def test_a_failing_step_still_resumes_the_child_and_is_recorded():
    c, ws = child("ko-KR", fail=("Emulation.setLocaleOverride", "Network.setUserAgentOverride"))
    await c.attached(event("iframe", "f3"), parent="page-session")
    await c.attached(event("worker", "w3"), parent="page-session")
    assert ws.methods("f3")[-1] == "Runtime.runIfWaitingForDebugger"
    assert ws.methods("w3")[-1] == "Runtime.runIfWaitingForDebugger"
    assert any("desktop locale" in f for f in c.failures) and any("desktop ua" in f for f in c.failures)


async def test_a_page_is_passed_through_and_its_children_are_attached():
    c, ws = child("ko-KR")
    await c.attached(event("page", "p1"), parent=None)
    assert "Emulation.setUserAgentOverride" not in ws.methods("p1")
    assert ws.methods("p1") == ["Runtime.runIfWaitingForDebugger"]
    assert any(m["method"] == "Target.setAutoAttach" and m.get("sessionId") == "p1" for m in ws.sent)


async def test_the_language_is_asked_with_the_parent_target_of_the_session_that_reported_the_child():
    asked: list[tuple[str, str]] = []
    ws = FakeSocket()

    def locale_for(info, parent):
        asked.append((info["type"], parent))
        return ""
    c = ChildTargets(ws, locale_for)
    ws.child = c
    await c.attached(event("page", "p9"), parent=None)           # target t-p9 is the page
    await c.attached(event("iframe", "f9"), parent="p9")
    assert asked == [("iframe", "t-p9")]                         # a page is passed through, a frame is asked with its parent


async def test_the_browser_session_gives_a_frame_the_language_of_its_tab_and_a_shared_worker_the_language_of_its_site():
    from assistant.tools.browser import BrowserSession, browser_owner
    s = BrowserSession(connect=None)
    key = browser_owner.get()
    s._targets[key] = ("t-page", "")
    s._pages[key] = type("P", (), {"url": "https://shop.example.com/deal"})()
    s._tab_locales[key] = "ko-KR"
    assert s._child_locale({"type": "iframe", "targetId": "f-x"}, "t-page") == "ko-KR"
    assert s._child_locale({"type": "iframe", "targetId": "f-y"}, "f-x") == "ko-KR"          # a frame inside that frame
    assert s._child_locale({"type": "worker", "targetId": "w"}, "t-page") == "ko-KR"
    assert s._child_locale({"type": "service_worker", "url": "https://shop.example.com/sw.js"}, "") == "ko-KR"
    assert s._child_locale({"type": "service_worker", "url": "https://other.example.org/sw.js"}, "") == ""
    assert s._child_locale({"type": "iframe", "targetId": "f-z"}, "t-unknown") == ""


async def test_close_stops_the_reader_and_the_connection():
    c, ws = child("")
    await c.start()
    await c.close()
    await asyncio.sleep(0)
    assert ws.closed is True
