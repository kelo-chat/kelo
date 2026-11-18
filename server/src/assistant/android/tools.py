"""The agent's tools for the Android device (docs/android-device.md): look, tap, type, swipe, key, open, apps.

What the screen shows is outside text (fenced like a web page). A tap or typing step is judged by the words on the screen with the
browser's rules (commits.py), the app's package standing for the site: a step that only looks asks nothing, one that pays, sends,
deletes, applies or changes an account shows the owner a card first (unless a task permission of that kind covers it)."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import inspect
import json
import math
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..agent import Ours, Tool, ToolOutput
from ..i18n import N_, current as language, t
from ..tool_labels import label
from ..tools.browser import _COMMIT_WARNINGS, BrowserPolicyError, browser_owner
from .card import DeviceCard
from .device import DeviceClient, DeviceError
from .judge import type_kind
from .handover import LIMIT_SECONDS, PhoneHandover
from .screen import Item, Screen, ScreenReader

SETTLE_SECONDS = 0.8       # after a step, before the screen is read again
TAP_SLOP = 30              # a swipe whose two ends are closer than this many pixels is a tap (the device reads it as one)
KEYS = ("back", "home", "enter", "recent", "delete", "tab")
DIRECTIONS = ("up", "down", "left", "right")
SETUP_QUIET = 2            # an app's screens stop being offered to the setup judge after this many "none" in a row (until it is opened again)
SETUP_NODES = 60
SETUP_NOTES = 12           # lines of "what was answered" kept per app
PERMISSION_ACTION = "android.content.pm.action.REQUEST_PERMISSIONS"   # the intent the system's permission dialog answers (its package is asked)
PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
STALE = "Error: that ref is not on the screen you last looked at (it changed): call android_look and use its refs."
NO_SCREEN = "Error: look at the screen first (android_look): taps and typing go by what it shows."
NO_VISION = "This model can't see pictures: the refs and words above are the screen."
PHONE_ONCE = N_("Use the phone for this task? Kelo asks once per task.")
PHONE_UNGATED = ("android_show", "android_send_screenshot")   # they serve the owner or the chat reply, not the phone's step
LOOK_HEAD = ("The screen of the phone app, one item per line: a1 button / edit / check / text, in reading order. Tap and type by "
             "ref (android_tap ref a3); the refs are good for this screen only.")


class AndroidSession:
    """What is known of the screen, per owner (the phone asking, as the browser keys its tab): the last Screen, and the ref counter
    that keeps a ref of an earlier screen from naming something else on a later one."""

    def __init__(self, client: DeviceClient, reader: ScreenReader | None = None):
        self.client, self.reader = client, reader or ScreenReader()
        self._screens: dict[str, Screen] = {}
        self._next: dict[str, int] = {}
        # the step judge (step_judge.StepJudge; None = no model wired: the older word rules alone judge) and what it said about the
        # last steps: (owner, ref, screen) -> commit kind ("" = harmless, "unknown" = it could not be judged: asks)
        self.step_judge: Any = None
        self._net: dict[tuple[str, str, str], str] = {}
        # first-run handling (docs/phone-worker.md "First run of an app"): the job's brief (a callable, "" = unknown), Kelo's small table
        # (the Store: meta/set_meta; None = nothing remembered), what the setup judge said of the owner's current screen, the app in
        # front before a permission dialog, and how many screens of an app in a row had nothing to set up
        self.task: Callable[[], str] = lambda: ""
        self.memory: Any = None
        self._setups: dict[str, Any] = {}
        self._front: dict[str, str] = {}
        self._quiet: dict[tuple[str, str], int] = {}
        self._permission_package: str | None = None
        self.setup_log: dict[str, list[str]] = {}

    @property
    def owner(self) -> str:
        return browser_owner.get()

    def screen(self) -> Screen | None:
        return self._screens.get(self.owner)

    async def look(self, image: bool = False) -> tuple[Screen, str | None]:
        """Reads the device now: the new Screen is the owner's current one."""
        got = await self.client.screen(image)
        try:
            screen = self.reader.parse(str(got.get("xml") or ""), str(got.get("app") or ""), self._next.get(self.owner, 1))
        except ValueError as e:
            raise DeviceError(str(e)) from None
        self._screens[self.owner] = screen
        self._next[self.owner] = self._next.get(self.owner, 1) + len(screen.items)
        await self._classify(screen)
        png = got.get("png")
        return screen, png if isinstance(png, str) and png else None

    async def peek(self) -> Screen:
        """The device's screen now, read without touching the owner's screen and refs (a check made in the background or before a
        card must not turn the model's refs stale)."""
        got = await self.client.screen(False)
        try:
            return self.reader.parse(str(got.get("xml") or ""), str(got.get("app") or ""), 1)
        except ValueError as e:
            raise DeviceError(str(e)) from None

    # ---- the first screens of an app: permission dialog, intro, ad, terms (step_judge.SETUP_SYSTEM decides; structure only narrows who is asked)
    async def permission_package(self) -> str:
        """The package of the device's permission dialog, asked once from the device (never a written name); "" when it cannot be told."""
        if self._permission_package is None:
            try:
                self._permission_package = await self.client.resolve(PERMISSION_ACTION)
            except (DeviceError, AttributeError):
                return ""   # not remembered: the next look asks again
        return self._permission_package

    def _remembered(self, app: str) -> list[str]:
        if self.memory is None or not app:
            return []
        try:
            got = json.loads(self.memory.meta("phone_setup:" + app) or "[]")
        except ValueError:
            return []
        return [str(x) for x in got] if isinstance(got, list) else []

    def remember(self, app: str, note: str) -> None:
        """What was answered for an app on this device, one line each, so the same dialog later is answered the same way."""
        if not app or not note:
            return
        lines = [n for n in self._remembered(app) if n != note] + [note]
        if self.memory is not None:
            self.memory.set_meta("phone_setup:" + app, json.dumps(lines[-SETUP_NOTES:], ensure_ascii=False))

    def forget_setup(self, app: str) -> None:
        """Clears what was answered for an app (the owner clearing the app's data)."""
        if self.memory is not None:
            self.memory.set_meta("phone_setup:" + app, "")

    def opened(self, package: str) -> None:
        """An app was opened (again): its first screens are looked at once more."""
        self._quiet.pop((self.owner, package), None)

    async def _classify(self, screen: Screen) -> None:
        """Asks the setup judge about the screen just read, when it can be one of an app's first screens. Structure narrows who is asked:
        the system's permission dialog (by its package), or a screen with something to press and no field for the owner's own details,
        in an app whose last screens still showed something to set up."""
        owner = self.owner
        self._setups.pop(owner, None)
        judge = getattr(self.step_judge, "setup", None)
        if judge is None or not screen.items:
            return
        permission = await self.permission_package()
        dialog = bool(permission) and screen.package == permission
        if not dialog:
            self._front[owner] = screen.package
            if any(i.editable or i.password for i in screen.items) or not any(i.clickable for i in screen.items):
                return   # a form for the owner's data (a sign-up, a message), or nothing to press: not first-run handling
            if self._quiet.get((owner, screen.package), 0) >= SETUP_QUIET:
                return
        app = self._front.get(owner, "") if dialog else screen.package
        nodes = [{"ref": i.ref, "kind": i.kind, "text": i.words[:80], **({"checked": i.checked} if i.checked is not None else {}),
                  "clickable": i.clickable} for i in screen.items[:SETUP_NODES]]
        verdict = await judge({"surface": "phone app screen", "package": screen.package, "screen": screen.activity,
                               "system_permission_dialog": dialog, "app_in_front_before": app, "the_job": self.task()[:1500],
                               "answered_before_for_this_app": self._remembered(app), "controls": nodes})
        if verdict is None:
            return
        refs = {i.ref for i in screen.items}
        verdict = dataclasses.replace(verdict, allow=tuple(r for r in verdict.allow if r in refs))
        self._setups[owner] = (app, verdict)
        if not dialog:
            key = (owner, screen.package)
            self._quiet[key] = self._quiet.get(key, 0) + 1 if verdict.screen == "none" else 0

    def setup_note(self) -> str:
        """What the model is told under the screen: which controls get through a first-run screen, or that the owner must decide."""
        got = self._setups.get(self.owner)
        if got is None or got[1].screen == "none":
            return ""
        _, v = got
        if v.screen == "special_access":
            return ("This screen asks for access outside the normal permission dialog. Do not answer it yourself: report needs_input "
                    "saying what the owner must allow on the phone.")
        if v.allow:
            return (f"This is one of the app's first screens ({v.screen}). Controls that get through it without asking the owner: "
                    + ", ".join(v.allow) + ".")
        return ""

    def setup_done(self, args: dict[str, Any]) -> str:
        """After a tap the judge let through as setup: the line for the job's report, remembered for this app. "" for any other tap."""
        got, item = self._setups.get(self.owner), self.target(args)
        if got is None or item is None or got[1].screen in ("none", "special_access") or item.ref not in got[1].allow or not got[1].note:
            return ""
        app, verdict = got
        self.remember(app, verdict.note)
        line = f"{app or '?'}: {verdict.note}"
        self.setup_log.setdefault(self.owner, []).append(line)
        return line

    # ---- what a step is, judged from the last screen (no call to the device)
    def target(self, args: dict[str, Any]) -> Item | None:
        screen = self.screen()
        if screen is None:
            return None
        if args.get("ref"):
            return screen.item(str(args["ref"]))
        x, y = _point(args)
        return screen.at(x, y) if x is not None and y is not None else None

    def judge(self, tool: str, args: dict[str, Any]) -> str:
        """The commit kind of a step: "" (it only looks), a kind of commit, or "unknown" (a point with nothing readable under it)."""
        item = self.target(args)
        if tool == "android_type":
            return type_kind(item, str(args.get("text") or ""))
        if item is None:
            return "unknown" if (self.screen() is not None and not args.get("ref")) else ""
        first = self._setups.get(self.owner)
        if first is not None and first[1].screen == "special_access" and item.clickable:
            return "unknown"   # access outside the permission dialog is the owner's: always a card
        got = self._net.get(self._net_key(item))
        if got is None:
            # a tap the step judge has not judged (none wired, or it was not asked): a clickable control asks, never passes
            got = "unknown" if tool == "android_tap" and item.clickable and not item.editable else ""
        if got == "" and tool == "android_tap" and first is not None and item.ref in first[1].allow:
            return "setup"   # the control of an app's first screen the setup judge chose (nothing else flagged it): no card
        return got

    def _net_key(self, item: Item) -> tuple[str, str, str]:
        screen = self.screen()
        return self.owner, item.ref, screen.app if screen is not None else ""

    async def settle(self, tool: str, args: dict[str, Any]) -> None:
        """The safety net for a tap (a swipe that is a tap, Enter on a focused control): ask the step judge what the clickable node does
        (cached per screen + control) and remember it for judge(). spends_money -> "pay" (a payment card, signed on the phone); any other
        flag -> its kind; the judge unavailable -> "unknown" (a card). Words and symbols decide nothing."""
        if self.step_judge is None or tool != "android_tap":
            return
        item, screen = self.target(args), self.screen()
        if item is None or screen is None or not item.clickable or item.editable:
            return
        nodes = [{"class": i.cls, "text": i.text[:60], "desc": i.desc[:60]} for i in screen.items[:60] if i.ref != item.ref]
        verdict = await self.step_judge.phone(screen.package, screen.activity, {"class": item.cls, "text": item.text[:80],
                                                                                 "desc": item.desc[:80], "hint": item.hint[:60]}, nodes)
        self._net[self._net_key(item)] = "unknown" if verdict is None else verdict.kind
        if len(self._net) > 300:
            self._net.pop(next(iter(self._net)))

    async def enter_kind(self, item: Item) -> str:
        """The commit kind of pressing Enter in a field ("" = it only searches or filters), judged by what it submits: the step judge
        gets the field (hint, text, multiline) and the screen's other nodes. A password field, no judge, or no answer: "unknown" (a card).
        Cached per screen + field."""
        screen = self.screen()
        if item.password or self.step_judge is None or screen is None:
            return "unknown"
        key = (self.owner, item.ref, (screen.app or screen.package) + "|enter")
        if key in self._net:
            return self._net[key]
        nodes = [{"class": i.cls, "text": i.text[:60], "desc": i.desc[:60]} for i in screen.items[:60] if i.ref != item.ref]
        field = {"class": item.cls, "text": item.text[:80], "desc": item.desc[:80], "hint": item.hint[:60], "editable": True,
                 "multiline": item.multiline, "typed_length": len(item.text), "action": "the Enter key is pressed in this text field"}
        verdict = await self.step_judge.phone(screen.package, screen.activity, field, nodes)
        kind = "unknown" if verdict is None else verdict.kind
        self._net[key] = kind
        if len(self._net) > 300:
            self._net.pop(next(iter(self._net)))
        return kind

    def enter_cached(self, item: Item) -> str:
        """What enter_kind last said about this field on this screen ("" when it was not asked)."""
        screen = self.screen()
        return self._net.get((self.owner, item.ref, (screen.app or screen.package) + "|enter"), "") if screen is not None else ""

    def forget(self) -> None:
        """Drop the owner's last screen: the phone was used by hand, so its refs (and where they point) are out of date."""
        self._screens.pop(self.owner, None)

    def package(self) -> str:
        screen = self.screen()
        return screen.package if screen is not None else ""

    def secret(self, args: dict[str, Any]) -> bool:
        item = self.target(args)
        return bool(item is not None and item.password)

    async def judge_ahead(self, tool: str, args: dict[str, Any]) -> tuple[str, str]:
        """(commit kind, package) of a step, for the privacy guard and the action ledger (server.py's browser_step)."""
        await self.settle(tool, args)
        return self.judge(tool, args), self.package()

    def page(self, tool: str, args: dict[str, Any]) -> tuple[str, str]:
        """(package/activity, the words of the item) of a step, for the ledger's fingerprint (done_once.PageStep)."""
        screen = self.screen()
        item = self.target(args)
        where = "" if screen is None else screen.app or screen.package
        return where, item.words if item is not None else ""


class PhoneUse:
    """Tasks whose owner allowed the phone (one card per task, the first time a phone tool runs)."""
    KEEP = 12 * 3600

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock, self._ok = clock, {}

    def allowed(self, key: str | None) -> bool:      # None (no task: CLI, tests) -> True
        if key is None:
            return True
        at = self._ok.get(key)
        return at is not None and self.clock() - at < self.KEEP

    def allow(self, key: str | None) -> None:
        if key is None:
            return
        now = self.clock()
        self._ok = {k: at for k, at in self._ok.items() if now - at < self.KEEP}
        self._ok[key] = now


def task_key() -> str | None:
    """The task the running turn belongs to (its turn when it has no task); None outside a turn."""
    from ..workers import current_turn
    turn = current_turn.get() or {}
    key = turn.get("task_id") or turn.get("turn_id")
    return None if not key else str(key)


def _phone_gated(tool: Tool, phone_use: PhoneUse) -> Tool:
    """The tool with one approval card per task: until the owner allows the phone for this task, the card says so first; after a
    handler ran (the owner allowed its card, or the tool's own rule said no card was needed) the task is allowed."""
    original_approve, original_describe, original_handler = tool.auto_approve, tool.describe, tool.handler

    async def auto_approve(args: dict[str, Any]) -> bool:
        if not phone_use.allowed(task_key()):
            return False
        if original_approve is None:
            return True
        decided = original_approve(args)
        return bool(await decided if inspect.isawaitable(decided) else decided)

    async def describe(args: dict[str, Any]) -> str:
        if phone_use.allowed(task_key()):
            return await original_describe(args) if original_describe is not None else ""
        words = await original_describe(args) if original_describe is not None else label(tool.name)
        return t(PHONE_ONCE) + "\n" + words

    async def handler(args: dict[str, Any]) -> Any:
        result = await original_handler(args)
        phone_use.allow(task_key())
        return result

    return dataclasses.replace(tool, requires_approval=True, auto_approve=auto_approve, describe=describe, handler=handler)


def _point(args: dict[str, Any]) -> tuple[int | None, int | None]:
    try:
        return int(args["x"]), int(args["y"])
    except (KeyError, TypeError, ValueError):
        return None, None


def _tap_point(args: dict[str, Any]) -> dict[str, Any] | None:
    """A coordinate swipe whose ends are within TAP_SLOP pixels is a tap at its start: returned as android_tap's arguments, so it is judged
    and asked like one. None for a real swipe (and for a direction, or coordinates that are not numbers)."""
    if args.get("direction"):
        return None
    pts = [args.get(k) for k in ("x1", "y1", "x2", "y2")]
    if any(isinstance(p, bool) or not isinstance(p, (int, float)) for p in pts):
        return None
    x1, y1, x2, y2 = pts
    if math.hypot(x2 - x1, y2 - y1) < TAP_SLOP:
        return {"x": int(x1), "y": int(y1)}
    return None


def android_tools(client: DeviceClient, reader: ScreenReader | None = None, *, grants: Any = None, orders: Any = None,
                  approve_actions: bool = False, session: AndroidSession | None = None, handover: PhoneHandover | None = None,
                  card: DeviceCard | None = None, hold: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None,
                  notes: Callable[[], str] | None = None, phone_use: PhoneUse | None = None,
                  hold_act: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None) -> list[Tool]:
    """`grants`: the permissions the owner gave for one task (tools/task_permission.TaskGrants). `orders` is accepted for the day a
    phone app's order is covered like a site's (the browser's order path); not used yet. `approve_actions`: every tap and typing step
    asks (the owner's ASSISTANT_APPROVE_BROWSER_ACTIONS). `handover`: gives the phone's live view to the owner (android_show); None = no
    live view, no such tool. `card`: shows the phone in the chat after each step (the `device` turn event; None = no card). `hold`: a
    step waits while the owner has the phone in the apps' live view (android/live.DeviceLive.hold_for_user). `notes`: what the owner did by
    hand since the agent last looked (DeviceLive.take_note): the next screen reading starts with it and the old refs are dropped.
    `phone_use`: one approval card per task before the first phone step (PhoneUse); None = no such gate."""
    s = session or AndroidSession(client, reader)
    pending: list[str] = []
    def with_notes(plain: Any) -> Any:
        async def held_step(args: dict[str, Any]) -> str | None:   # the hold that also takes the owner's note when the phone is free again
            held = await plain(args) if plain is not None else None
            if held is None and notes is not None and (note := notes()):
                pending.append(note)
                s.forget()
            return held
        return held_step

    hold = with_notes(hold) if (hold is not None or notes is not None) else None
    acting = with_notes(hold_act) if hold_act is not None else hold   # the steps that act on the phone (hold_act: the moved-note too)

    async def shown(head: str, image: bool = False) -> ToolOutput | Ours:
        try:
            screen, png = await s.look(image)
        except DeviceError as e:
            return Ours(f"Error: {e}")
        if pending:   # the owner's notes from the hold (taken by hand while the agent waited) come first
            head, pending[:] = "\n".join([*pending, head]), []
        if note := s.setup_note():
            head = head + " " + note
        if png:
            return ToolOutput(screen.render(), ["data:image/png;base64," + png], head=head, no_vision=NO_VISION)
        return ToolOutput(screen.render(), head=head)

    async def after(head: str, status: str = "") -> ToolOutput | Ours:
        await asyncio.sleep(SETTLE_SECONDS)
        if card is not None and status:
            await card.show(status)   # the phone card of the chat (throttled, never fails the step)
        return await shown(head)

    async def look(args: dict[str, Any]) -> ToolOutput | Ours:
        if card is not None:
            await card.show(t("Looking at the phone"))
        return await shown(LOOK_HEAD, bool(args.get("image")))

    def refuse(args: dict[str, Any], *, typing: bool = False) -> str | None:
        """A reason to turn the call down before any card: no screen read yet, or a ref that is not on it."""
        if args.get("ref"):
            if s.screen() is None:
                return NO_SCREEN
            return None if s.target(args) is not None else STALE
        if typing:
            return None   # typing into the field in focus
        if _point(args) == (None, None):
            return "ref (from android_look) or x and y are needed"
        return None if s.screen() is not None else NO_SCREEN

    async def tap(args: dict[str, Any]) -> ToolOutput | Ours:
        if (why := refuse(args)) is not None:
            return Ours(why if why.startswith("Error") else "Error: " + why)
        item = s.target(args)
        x, y = item.center if item is not None and args.get("ref") else _point(args)
        first_run = s.judge("android_tap", args) == "setup"
        try:
            await client.tap(int(x or 0), int(y or 0))
        except DeviceError as e:
            return Ours(f"Error: {e}")
        said = f"Tapped {args['ref']}." if args.get("ref") else f"Tapped ({x}, {y})."
        if first_run and (line := s.setup_done(args)):
            said += f" First-run step, for your report: {line}."
        return await after(said, t("Tapping on the phone"))

    async def type_(args: dict[str, Any]) -> ToolOutput | Ours:
        text = str(args.get("text") or "")
        if not text:
            return Ours("Error: text is needed")
        if (why := refuse(args, typing=True)) is not None:
            return Ours(why if why.startswith("Error") else "Error: " + why)
        try:
            if args.get("ref"):
                item = s.target(args)
                await client.tap(*(item.center if item is not None else (0, 0)))   # the field first, so the text goes there
                await asyncio.sleep(SETTLE_SECONDS / 2)
            await client.text(text)
            if args.get("submit"):
                await client.key("enter")
        except DeviceError as e:
            return Ours(f"Error: {e}")
        return await after("Typed into " + (str(args["ref"]) if args.get("ref") else "the field in focus") + ".", t("Typing on the phone"))

    async def swipe(args: dict[str, Any]) -> ToolOutput | Ours:
        screen = s.screen()
        w, h = screen.size if screen is not None and all(screen.size) else (720, 1280)
        d = str(args.get("direction") or "")
        try:
            if d:
                if d not in DIRECTIONS:
                    return Ours(f"Error: direction must be one of {', '.join(DIRECTIONS)}")
                # "down" shows what is further down: the finger moves up
                cx, cy = w // 2, h // 2
                dx, dy = {"up": (0, 1), "down": (0, -1), "left": (1, 0), "right": (-1, 0)}[d]
                await client.swipe(cx - dx * w // 4, cy - dy * h // 4, cx + dx * w // 4, cy + dy * h // 4, 300)
            else:
                pts = [args.get(k) for k in ("x1", "y1", "x2", "y2")]
                if any(isinstance(p, bool) or not isinstance(p, (int, float)) for p in pts):
                    return Ours("Error: direction (up, down, left, right) or x1, y1, x2, y2 are needed")
                tap = _tap_point(args)
                if tap is not None:   # no real swipe: a tap, as android_tap does it (the same judging above)
                    await client.tap(tap["x"], tap["y"])
                else:
                    await client.swipe(*(int(p) for p in pts), int(args.get("ms") or 300))
        except DeviceError as e:
            return Ours(f"Error: {e}")
        return await after("Scrolled." if d else "Swiped.", t("Scrolling the phone"))

    async def key(args: dict[str, Any]) -> ToolOutput | Ours:
        k = str(args.get("key") or "")
        if k not in KEYS:
            return Ours(f"Error: key must be one of {', '.join(KEYS)}")
        try:
            await client.key(k)
        except DeviceError as e:
            return Ours(f"Error: {e}")
        return await after(f"Pressed {k}.", t("Pressing a key on the phone"))

    async def open_(args: dict[str, Any]) -> ToolOutput | Ours:
        app = str(args.get("app") or "").strip()
        if not app:
            return Ours("Error: app (a package name or the app's name) is needed")
        try:
            package = app
            if not PACKAGE.match(app):
                every = [a for a in (await client.apps()).get("items") or [] if isinstance(a, dict)]
                want = app.casefold()
                found = [a for a in every if want in (str(a.get("label", "")).casefold(), str(a.get("package", "")).casefold())]
                found = found or [a for a in every if want in f"{a.get('label', '')} {a.get('package', '')}".casefold()]
                if not found:
                    return Ours(f"Error: no app matches {app!r}; android_apps lists what can be opened")
                if len(found) > 1:
                    return Ours("Error: several apps match; use one package name: " + ", ".join(str(a.get("package")) for a in found[:8]))
                package = str(found[0].get("package"))
            await client.open(package)
        except DeviceError as e:
            return Ours(f"Error: {e}")
        s.opened(package)
        return await after(f"Opened {package}.", t("Opening an app on the phone"))

    async def apps(args: dict[str, Any]) -> ToolOutput | Ours:
        try:
            items = (await client.apps()).get("items") or []
        except DeviceError as e:
            return Ours(f"Error: {e}")
        lines = [f"{a.get('package')} — {a.get('label')}" for a in items if isinstance(a, dict)]
        return ToolOutput("\n".join(lines) or "(no apps)", head="Apps that can be opened (android_open with a package name):")

    async def show(args: dict[str, Any]) -> ToolOutput | Ours:
        reason = " ".join(str(args.get("reason") or "").split())
        if not reason:
            return Ours("Error: reason is needed: one short line telling the owner why they should look at or use the screen")
        assert handover is not None
        try:
            how = await handover.show(reason[:200], lang=language())
        except (BrowserPolicyError, DeviceError) as e:
            return Ours(f"Error: {e}")
        said = {"done": "The user used the phone's screen and is done.",
                "timeout": f"The user did not finish in {round(LIMIT_SECONDS / 60)} minutes; the phone's live view was released.",
                "error": f"The phone's screen could not be shown to the user ({handover.detail or 'the bridge failed'}). Nothing was opened in the browser.",
                "skipped": "The user chose not to use the phone's screen now. Do not ask again unless they bring it up.",
                }.get(how, "The phone's live view ended before the user was done.")
        return await shown(said + " The screen now:")

    # ---- approval: the browser's rules, the app's package as the site
    def decider(tool: str):
        as_tool = "browser_click" if tool == "android_tap" else "browser_type"

        async def decide(args: dict[str, Any]) -> bool:
            """True = no card: the step only looks, or a task permission of its kind covers it on this app."""
            await s.settle(tool, args)
            kind = s.judge(tool, args)
            if not kind or kind == "setup":
                return not approve_actions   # only looks, or an app's first-run control (unless the owner set every step to ask)
            if kind in ("unknown", "security", "pay") or approve_actions:
                return False   # unreadable, the account's protection, or money: always asks (a payment card), no permission covers it
            return bool(grants is not None and grants.use(kind, s.package(), tool=as_tool, secret=s.secret(args)))
        return decide

    def warning(args: dict[str, Any], tool: str) -> str:
        kind = s.judge(tool, args)
        return " " + t(_COMMIT_WARNINGS[kind]) if kind in _COMMIT_WARNINGS else ""

    async def describe_tap(args: dict[str, Any]) -> str:
        app, item = s.package() or "?", s.target(args)
        line = (t("Tap “{label}” in {app}.", app=app, label=" ".join(item.words.split())[:60]) if item is not None and item.words
                else t("Tap a button in {app}.", app=app))
        return line + warning(args, "android_tap")

    async def describe_type(args: dict[str, Any]) -> str:
        app, item = s.package() or "?", s.target(args)
        line = (t("Type into “{label}” in {app}.", app=app, label=" ".join((item.hint or item.desc or item.words).split())[:60])
                if item is not None and (item.hint or item.desc or item.words) else t("Type into a field in {app}.", app=app))
        return line + warning(args, "android_type")

    # ---- the other steps that act on the screen: a swipe that is a tap, Enter, and opening an app
    async def swipe_ok(args: dict[str, Any]) -> bool:
        tap = _tap_point(args)
        return True if tap is None else await decider("android_tap")(tap)   # a real swipe only moves the screen

    async def describe_swipe(args: dict[str, Any]) -> str:
        tap = _tap_point(args)
        return await describe_tap(tap) if tap is not None else ""

    async def key_ok(args: dict[str, Any]) -> bool:
        if str(args.get("key") or "") != "enter":
            return True   # back, home, recent, delete, tab move about; they commit nothing by themselves
        screen = s.screen()
        focused = next((i for i in (screen.items if screen is not None else []) if i.focused), None)
        if focused is None:
            return False   # Enter acts on something not read: it asks
        if focused.editable:
            # judged by what it submits: a search or a filter passes; a send, a payment, an account step, a password field, or no
            # answer asks (and so does every step when the owner set it so)
            return not await s.enter_kind(focused) and not approve_actions
        return await decider("android_tap")({"ref": focused.ref})   # Enter presses the focused item, so it is judged as its tap

    async def describe_key(args: dict[str, Any]) -> str:
        return t("Press Enter in {app}.", app=s.package() or "?")

    async def open_ok(args: dict[str, Any]) -> bool:
        return not approve_actions   # an app opened commits nothing by itself: the steps on its screen are judged when they come

    async def describe_open(args: dict[str, Any]) -> str:
        return t("Open {app} on the phone.", app=" ".join(str(args.get("app") or "").split())[:60] or "?")

    # a step the judge found to spend money is a payment card (commit "pay": the phone signs the answer, approval_sign.is_payment)
    def commit_of(tap: dict[str, Any] | None) -> dict[str, Any]:
        return {"commit": "pay"} if tap is not None and s.judge("android_tap", tap) == "pay" else {}

    def tap_extra(_id: str, args: dict[str, Any]) -> dict[str, Any]:
        return commit_of(args)

    def swipe_extra(_id: str, args: dict[str, Any]) -> dict[str, Any]:
        return commit_of(_tap_point(args))

    def key_extra(_id: str, args: dict[str, Any]) -> dict[str, Any]:
        screen = s.screen()
        focused = next((i for i in (screen.items if screen is not None else []) if i.focused), None)
        if focused is None or args.get("key") != "enter":
            return {}
        if focused.editable:   # the verdict key_ok cached: a field whose Enter spends money is a payment card too
            return {"commit": "pay"} if s.enter_cached(focused) == "pay" else {}
        return commit_of({"ref": focused.ref})

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    xy = {"x": {"type": "integer", "description": "a point of the screen, for what has no ref"},
          "y": {"type": "integer"}}
    extra = [Tool("android_show", "Hand the phone app's screen to the owner so they tap or type on it themselves, ONLY when a step needs "
                  "their hands (a human check, a step you cannot read or do): they get a card that opens the phone's live view, and press Done when finished (or it ends when they let go), and you get "
                  "the new screen. To SHOW them the screen, use android_send_screenshot instead, never this. The apps' phone card "
                  "(shown in the chat while you use the phone, with a live view the owner can take over) is preferred when the app supports "
                  "it: this tool is the fallback for older apps. Say why in reason, in one short line in the owner's language.",
                  obj({"reason": {"type": "string", "description": "why the owner should look, one short line"}}, ["reason"]),
                  show, untrusted=True, source="page")] if handover is not None else []
    tools = [
        *extra,
        Tool("android_look", "Read the screen of a phone app on the Android device the owner set up (for apps with no usable website): "
             "the app in front and its buttons, fields, switches and texts, each with a ref (a3). Look before you tap or type. If an app "
             "opens its sign-in in a browser on the phone, call android_login there (the phone checks the page belongs to the app).",
             obj({"image": {"type": "boolean", "description": "also a screenshot (for what the words do not tell)"}}, []),
             look, untrusted=True, source="page", hold=hold),
        Tool("android_tap", "Tap an item of the phone app's screen by its ref (from android_look), or a point (x, y). Returns the screen "
             "after. A tap that commits something in the user's name (pays, sends, deletes, signs up) shows them a card first.",
             obj({"ref": {"type": "string", "description": "a ref from your last look, e.g. a3"}, **xy}, []),
             tap, requires_approval=True, untrusted=True, source="page", validate=lambda a: refuse(a),
             auto_approve=decider("android_tap"), describe=describe_tap, hold=acting,
             approval_extra=tap_extra),
        Tool("android_type", "Type text into a field of the phone app (the field at ref, or the one in focus). Returns the screen after. "
             "Personal details show the user a card first; a message or a review does not (its send button does).",
             obj({"ref": {"type": "string", "description": "the field's ref from your last look"}, "text": {"type": "string"},
                  "submit": {"type": "boolean", "description": "press Enter after"}}, ["text"]),
             type_, requires_approval=True, untrusted=True, source="page", validate=lambda a: refuse(a, typing=True),
             auto_approve=decider("android_type"), describe=describe_type, hold=acting),
        Tool("android_swipe", "Scroll or swipe the phone app's screen: a direction (down shows what is further down), or the "
             "coordinates of a swipe. Returns the screen after.",
             obj({"direction": {"type": "string", "enum": list(DIRECTIONS)}, "x1": {"type": "integer"}, "y1": {"type": "integer"},
                  "x2": {"type": "integer"}, "y2": {"type": "integer"}}, []),
             swipe, requires_approval=True, auto_approve=swipe_ok, describe=describe_swipe, untrusted=True, source="page", hold=acting,
             approval_extra=swipe_extra),
        Tool("android_key", "Press a key on the phone app's device: back, home, enter, recent, delete or tab. Returns the screen after.",
             obj({"key": {"type": "string", "enum": list(KEYS)}}, ["key"]), key, requires_approval=True, auto_approve=key_ok,
             describe=describe_key, untrusted=True, source="page", hold=acting, approval_extra=key_extra),
        Tool("android_open", "Open an app on the phone app's device by its package name or its name. Returns the screen after.",
             obj({"app": {"type": "string", "description": "package (com.example.app) or the app's name"}}, ["app"]),
             open_, requires_approval=True, auto_approve=open_ok, describe=describe_open, untrusted=True, source="page", hold=hold),
        Tool("android_apps", "List the apps of the phone app's device that can be opened (package and name).",
             obj({}, []), apps, untrusted=True, source="page", read_only=True, hold=hold),
    ]
    if phone_use is None:
        return tools
    return [tool if tool.name in PHONE_UNGATED else _phone_gated(tool, phone_use) for tool in tools]


def phone_grab(client: Any, reader: Any) -> Any:
    """The phone's screen as a chat picture (chat_images.ChatImages.tool's GrabFn): the screenshot with every password field painted
    over (found in the same screen's dump), as JPEG at most 1080 px wide. The selector argument is unused."""
    async def grab(_selector: str) -> tuple[bytes | None, int, int, str]:
        import base64
        import io

        from PIL import Image, ImageDraw
        try:
            got = await client.screen(image=True)
        except Exception as e:  # noqa: BLE001 - the bridge or the device is away
            return None, 0, 0, f"the phone's screen could not be read ({e})"
        raw = got.get("png") or ""
        if not raw:
            return None, 0, 0, "the phone sent no picture"
        try:
            im = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
        except Exception:  # noqa: BLE001 - not a picture
            return None, 0, 0, "the phone's picture could not be read"
        try:
            screen = reader.parse(str(got.get("xml") or ""), str(got.get("app") or ""))
            draw = ImageDraw.Draw(im)
            for item in screen.items:
                if item.password:
                    draw.rectangle(item.bounds, fill=(32, 32, 32))   # a typed password never reaches the chat
        except Exception:  # noqa: BLE001 - no dump: refuse rather than risk showing a secret field
            return None, 0, 0, "the phone's screen could not be checked for password fields"
        im.thumbnail((1080, 2400))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue(), im.size[0], im.size[1], ""
    return grab
