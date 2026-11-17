"""A rig for the situation research tests: a real Store, Home and Proactive, a fake clock, a scripted agent in place of the model."""

from __future__ import annotations

import inspect
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from assistant.agent import Tool
from assistant.home import Home
from assistant.owner_copies import OwnerCopies
from assistant.proactive import Proactive
from assistant.situation import Section, SituationResearch
from assistant.store import Store

SEOUL = ZoneInfo("Asia/Seoul")

# what the app lists: reads and acting tools alike, so a test can see the research role keep only the reads
READS = ("read_file", "list_files", "read_earlier_messages", "memory_explain", "web_search", "web_fetch", "weather", "email_list",
         "email_read", "gcal_events", "gcal_calendars", "calendar_events", "schedule_list", "watch_list", "connector_get", "mcp_list_tools")
ACTING = ("email_send", "email_update", "gcal_create_event", "gcal_delete_event", "phone_send_text", "phone_call", "phone_messages",
          "android_tap", "android_look", "browser_click", "browser_type", "browser_login", "checkout_submit", "confirm_purchase", "browser_fill_card",
          "schedule_create", "schedule_update", "schedule_delete", "watch_add", "delegate", "connector_send", "mcp_call_tool", "write_file",
          "connector_download", "remember", "react")


def ts(*a: int) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def app_tools() -> list[Tool]:
    async def ok(_a: dict[str, Any]) -> str:
        return "ok"
    return [Tool(n, n, {"type": "object", "properties": {}}, ok) for n in (*READS, *ACTING)]


class FakeAgent:
    """Stands in for the unattended agent turns: a script per scope gets the tools it was given (by name) and the user text."""

    def __init__(self) -> None:
        self.scripts: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []

    def on(self, scope: str, fn: Any) -> None:
        self.scripts[scope] = fn

    def count(self, scope: str | None = None) -> int:
        return len([c for c in self.calls if scope is None or c["scope"] == scope])

    async def run_turn(self, system: str, user: str, tools: list[Tool], steps: int, seconds: float, title: str, scope: str) -> str:
        self.calls.append({"scope": scope, "system": system, "user": user, "tools": [t.name for t in tools], "steps": steps, "seconds": seconds,
                           "title": title})
        fn = self.scripts.get(scope)
        if fn is None:
            return ""
        got = fn({t.name: t for t in tools}, user)
        if inspect.isawaitable(got):
            got = await got
        return got or ""


class FakeMail:
    def __init__(self, items: list[dict[str, Any]] | None = None):
        self.items, self.calls = items or [], 0

    def accounts(self) -> list[dict[str, Any]]:
        return [{"name": "mail-gmail", "provider": "gmail", "label": "Gmail · me@example.org", "address": "me@example.org"}]

    async def arrivals(self, account: str, cursor: Any, kind: str = "inbox") -> dict[str, Any] | None:
        self.calls += 1
        if kind != "inbox":
            return None
        rows, self.items = [dict(m) for m in self.items], []   # what arrived is read once: the next look sees only new mail
        return {"rows": rows, "cursor": {"t": self.calls}}


def mail(uid: str, subject: str, sender: str = "Kim <kim@example.org>") -> dict[str, Any]:
    return {"uid": uid, "from": sender, "subject": subject, "date": "Tue, 17 Nov 2026 09:00:00 +0900", "unread": True, "snippet": ""}


class DeviceSource:
    """A SituationSource: one line per device that is on (the smart-home source of a later run, here a fake)."""

    def __init__(self, lines: list[str] | None = None):
        self.lines = lines or []

    def section(self, now: float, since: float) -> Section | None:
        return Section("Home devices", list(self.lines), note="Devices at home and whether they are on.") if self.lines else None


class Rig:
    def __init__(self, tmp_path: Any, t: float, *, mail_box: FakeMail | None = None, events: list[dict[str, Any]] | None = None):
        self.store = Store()
        self.store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
        self.home = Home(tmp_path / "home")
        self.home.owner_copies = OwnerCopies(self.store)
        self.clock = Clock(t)
        self.sent: list[dict[str, Any]] = []
        self.events = events if events is not None else []
        self.mail = mail_box
        self.agent = FakeAgent()
        self.judged = 0

        async def deliver(item: dict[str, Any]) -> int:
            self.sent.append(item)
            return len(self.sent)

        async def calendar() -> list[dict[str, Any]]:
            return [dict(e) for e in self.events]

        async def judge(provider: Any, request: Any, **kw: Any):   # the old candidate pass's model: counted, answers drop for everything
            import json
            from assistant.providers import Done, TextDelta
            self.judged += 1
            body = request.messages[-1]["content"]
            items = [ln.split(". ", 1) for ln in body.split("Items:\n", 1)[1].splitlines() if ". " in ln]
            yield TextDelta(json.dumps({"items": [{"n": int(n), "urgency": "drop", "reason": "x", "message": ""} for n, _t in items]}))
            yield Done("stop")

        self.p = Proactive(self.store, self.home, stream_fn=judge, model=lambda: ("p", "m"), deliver=deliver, mail=mail_box, calendar=calendar,
                           clock=self.clock)
        self.sit = SituationResearch(self.p, run_turn=self.agent.run_turn, tools=app_tools)
        self.p.update(enabled=True)
        self.say("user", "안녕", self.clock.t - 3 * 86400)   # the owner has written here once (nothing proactive runs before that)

    def say(self, role: str, text: str, at: float) -> int:
        mid = self.store.add_message(role, text)
        self.store._run("UPDATE messages SET created_at = ? WHERE id = ?", (at, mid))
        return mid

    def advance(self, seconds: float) -> None:
        self.clock.t += seconds

    def items(self, *statuses: str) -> list[dict[str, Any]]:
        return self.sit.items_by(*(statuses or ("prepared", "selected", "told", "dropped", "expired")), since=0)


def label_of(user: str, needle: str) -> str:
    """The [source:N] label of the pack line that contains `needle` (the scripted agent cites evidence the way the model would)."""
    for m in re.finditer(r"\[source:(\d+)\] (.*?)(?=\n\n|\Z)", user, re.S):
        if needle in m.group(2):
            return f"source:{m.group(1)}"
    raise AssertionError(f"no entry with {needle!r} in the pack")
