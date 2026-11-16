"""The agent's tools for the Android device (docs/android-device.md): look, tap, type, swipe, key, open, apps.

What the screen shows is outside text (fenced like a web page). A tap or typing step is judged by the words on the screen with the
browser's rules (commits.py), the app's package standing for the site: a step that only looks asks nothing, one that pays, sends,
deletes, applies or changes an account shows the owner a card first (unless a task permission of that kind covers it)."""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ..agent import Ours, Tool, ToolOutput
from ..i18n import current as language, t
from ..tools.browser import _COMMIT_WARNINGS, BrowserPolicyError, browser_owner
from .card import DeviceCard
from .device import DeviceClient, DeviceError
from .judge import tap_kind, type_kind
from .mirror import LIMIT_SECONDS, AndroidMirror
from .screen import Item, Screen, ScreenReader

SETTLE_SECONDS = 0.8       # after a step, before the screen is read again
KEYS = ("back", "home", "enter", "recent", "delete", "tab")
DIRECTIONS = ("up", "down", "left", "right")
PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
STALE = "Error: that ref is not on the screen you last looked at (it changed): call android_look and use its refs."
NO_SCREEN = "Error: look at the screen first (android_look): taps and typing go by what it shows."
NO_VISION = "This model can't see pictures: the refs and words above are the screen."
LOOK_HEAD = ("The screen of the phone app, one item per line: a1 button / edit / check / text, in reading order. Tap and type by "
             "ref (android_tap ref a3); the refs are good for this screen only.")


class AndroidSession:
    """What is known of the screen, per owner (the phone asking, as the browser keys its tab): the last Screen, and the ref counter
    that keeps a ref of an earlier screen from naming something else on a later one."""

    def __init__(self, client: DeviceClient, reader: ScreenReader | None = None):
        self.client, self.reader = client, reader or ScreenReader()
        self._screens: dict[str, Screen] = {}
        self._next: dict[str, int] = {}

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
        return tap_kind(item)

    def package(self) -> str:
        screen = self.screen()
        return screen.package if screen is not None else ""

    def secret(self, args: dict[str, Any]) -> bool:
        item = self.target(args)
        return bool(item is not None and item.password)

    async def judge_ahead(self, tool: str, args: dict[str, Any]) -> tuple[str, str]:
        """(commit kind, package) of a step, for the privacy guard and the action ledger (server.py's browser_step)."""
        return self.judge(tool, args), self.package()

    def page(self, tool: str, args: dict[str, Any]) -> tuple[str, str]:
        """(package/activity, the words of the item) of a step, for the ledger's fingerprint (done_once.PageStep)."""
        screen = self.screen()
        item = self.target(args)
        where = "" if screen is None else screen.app or screen.package
        return where, item.words if item is not None else ""


def _point(args: dict[str, Any]) -> tuple[int | None, int | None]:
    try:
        return int(args["x"]), int(args["y"])
    except (KeyError, TypeError, ValueError):
        return None, None


def android_tools(client: DeviceClient, reader: ScreenReader | None = None, *, grants: Any = None, orders: Any = None,
                  approve_actions: bool = False, session: AndroidSession | None = None, mirror: AndroidMirror | None = None,
                  card: DeviceCard | None = None, hold: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None) -> list[Tool]:
    """`grants`: the permissions the owner gave for one task (tools/task_permission.TaskGrants). `orders` is accepted for the day a
    phone app's order is covered like a site's (the browser's order path); not used yet. `approve_actions`: every tap and typing step
    asks (the owner's ASSISTANT_APPROVE_BROWSER_ACTIONS). `mirror`: shows the device in Kelo's browser view (android_show); None = no
    browser set up, no such tool. `card`: shows the phone in the chat after each step (the `device` turn event; None = no card). `hold`: a
    step waits while the owner has the phone in the apps' live view (android/live.DeviceLive.hold_for_user)."""
    s = session or AndroidSession(client, reader)

    async def shown(head: str, image: bool = False) -> ToolOutput | Ours:
        try:
            screen, png = await s.look(image)
        except DeviceError as e:
            return Ours(f"Error: {e}")
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
        try:
            await client.tap(int(x or 0), int(y or 0))
        except DeviceError as e:
            return Ours(f"Error: {e}")
        return await after(f"Tapped {args['ref']}." if args.get("ref") else f"Tapped ({x}, {y}).", t("Tapping on the phone"))

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
        assert mirror is not None
        try:
            how = await mirror.show(reason[:200], lang=language())
        except (BrowserPolicyError, DeviceError) as e:
            return Ours(f"Error: {e}")
        said = {"done": "The user used the phone's screen in the browser view and pressed Done.",
                "timeout": f"The user did not press Done in {round(LIMIT_SECONDS / 60)} minutes; the browser view was closed.",
                "error": f"The phone's screen stopped showing in the browser view ({mirror.detail or 'the bridge failed'}); it was closed.",
                "skipped": "The user chose not to use the phone's screen now. Do not ask again unless they bring it up.",
                }.get(how, "The browser view of the phone's screen was closed before the user pressed Done.")
        return await shown(said + " The screen now:")

    # ---- approval: the browser's rules, the app's package as the site
    def decider(tool: str):
        as_tool = "browser_click" if tool == "android_tap" else "browser_type"

        async def decide(args: dict[str, Any]) -> bool:
            """True = no card: the step only looks, or a task permission of its kind covers it on this app."""
            kind = s.judge(tool, args)
            if not kind:
                return not approve_actions   # only looks (unless the owner set every step to ask)
            if kind in ("unknown", "security") or approve_actions:
                return False   # unreadable or the account's protection: always asks, no permission covers it
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

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    xy = {"x": {"type": "integer", "description": "a point of the screen, for what has no ref"},
          "y": {"type": "integer"}}
    extra = [Tool("android_show", "Hand the phone app's screen to the owner so they tap or type on it themselves, ONLY when a step needs "
                  "their hands (a human check, a step you cannot read or do): they get a card, press Done when finished, and you get "
                  "the new screen. To SHOW them the screen, use android_send_screenshot instead, never this. The apps' phone card "
                  "(shown in the chat while you use the phone, with a live view the owner can take over) is preferred when the app supports "
                  "it: this tool is the fallback for older apps. Say why in reason, in one short line in the owner's language.",
                  obj({"reason": {"type": "string", "description": "why the owner should look, one short line"}}, ["reason"]),
                  show, untrusted=True, source="page")] if mirror is not None else []
    return [
        *extra,
        Tool("android_look", "Read the screen of a phone app on the Android device the owner set up (for apps with no usable website): "
             "the app in front and its buttons, fields, switches and texts, each with a ref (a3). Look before you tap or type.",
             obj({"image": {"type": "boolean", "description": "also a screenshot (for what the words do not tell)"}}, []),
             look, untrusted=True, source="page", hold=hold),
        Tool("android_tap", "Tap an item of the phone app's screen by its ref (from android_look), or a point (x, y). Returns the screen "
             "after. A tap that commits something in the user's name (pays, sends, deletes, signs up) shows them a card first.",
             obj({"ref": {"type": "string", "description": "a ref from your last look, e.g. a3"}, **xy}, []),
             tap, requires_approval=True, untrusted=True, source="page", validate=lambda a: refuse(a),
             auto_approve=decider("android_tap"), describe=describe_tap, hold=hold),
        Tool("android_type", "Type text into a field of the phone app (the field at ref, or the one in focus). Returns the screen after. "
             "Personal details show the user a card first; a message or a review does not (its send button does).",
             obj({"ref": {"type": "string", "description": "the field's ref from your last look"}, "text": {"type": "string"},
                  "submit": {"type": "boolean", "description": "press Enter after"}}, ["text"]),
             type_, requires_approval=True, untrusted=True, source="page", validate=lambda a: refuse(a, typing=True),
             auto_approve=decider("android_type"), describe=describe_type, hold=hold),
        Tool("android_swipe", "Scroll or swipe the phone app's screen: a direction (down shows what is further down), or the "
             "coordinates of a swipe. Returns the screen after.",
             obj({"direction": {"type": "string", "enum": list(DIRECTIONS)}, "x1": {"type": "integer"}, "y1": {"type": "integer"},
                  "x2": {"type": "integer"}, "y2": {"type": "integer"}}, []),
             swipe, untrusted=True, source="page", hold=hold),
        Tool("android_key", "Press a key on the phone app's device: back, home, enter, recent, delete or tab. Returns the screen after.",
             obj({"key": {"type": "string", "enum": list(KEYS)}}, ["key"]), key, untrusted=True, source="page", hold=hold),
        Tool("android_open", "Open an app on the phone app's device by its package name or its name. Returns the screen after.",
             obj({"app": {"type": "string", "description": "package (com.example.app) or the app's name"}}, ["app"]),
             open_, untrusted=True, source="page", hold=hold),
        Tool("android_apps", "List the apps of the phone app's device that can be opened (package and name).",
             obj({}, []), apps, untrusted=True, source="page", read_only=True, hold=hold),
    ]


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
