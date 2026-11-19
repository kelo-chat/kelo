"""The work browser's frames and workers are covered too (addendum item 5): each new connection of the work browser opens its own
ChildTargets connection, as the main browser does. Fake socket and fake version document: no browser."""

import asyncio

import pytest

from assistant.tools import browser as browser_mod
from assistant.tools.browser import BrowserSession
from assistant.tools.work_browser import WorkBrowser



class FakeChildren:
    """ChildTargets as the session opens it: records its socket, starts and closes at once (the protocol is tested in test_child_targets)."""
    made: list = []

    def __init__(self, ws, locale_for=None) -> None:
        self.ws, self.closed = ws, False
        FakeChildren.made.append(self)

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeConnect:
    """A connect() for WorkBrowser that answers at once, like a browser that is up."""
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self):
        self.calls += 1

        class Browser:
            contexts: list = []
            def is_connected(self):
                return True
        return Browser()


async def test_a_new_work_browser_connection_opens_its_own_children_connection(monkeypatch):
    opened: list[str] = []
    FakeChildren.made.clear()

    async def fake_connect(url, **kwargs):
        opened.append(url)
        return object()

    class FakeWebsockets:
        connect = staticmethod(fake_connect)

    import sys
    monkeypatch.setitem(sys.modules, "websockets", FakeWebsockets)
    monkeypatch.setattr(browser_mod, "ChildTargets", FakeChildren)
    monkeypatch.setattr(browser_mod, "_devtools_version", lambda ip, port: '{"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"}')
    s = BrowserSession(connect=lambda: None)
    work = WorkBrowser("localhost", 9223, connect=FakeConnect())
    s.work = work
    work.on_connected = s.work_connected
    assert await work.ensure()
    for _ in range(50):          # the hook runs as a task; the connection is made in a thread
        if s._work_children is not None:
            break
        await asyncio.sleep(0.02)
    assert s._work_children is not None
    assert opened == ["ws://127.0.0.1:9223/devtools/browser/x"]
    assert s._children is None   # the main browser was not touched
    assert len(FakeChildren.made) == 1 and not FakeChildren.made[0].closed


async def test_a_work_browser_that_is_not_answering_leaves_the_children_unset(monkeypatch):
    def refused(ip, port):
        raise OSError("refused")
    monkeypatch.setattr(browser_mod, "_devtools_version", refused)
    s = BrowserSession(connect=lambda: None)
    await s.work_connected("localhost", 9223)
    assert s._work_children is None
