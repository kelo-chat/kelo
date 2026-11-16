"""More of the browser: files in and out of pages, PDFs, private tabs, a site's sign-in state, one-time codes from the user's mail, the
pictures of unattended runs and what the user does by hand. The pieces live in browser.py (BrowserSession), downloads.py, challenge.py,
takeover.py, login_code.py and ../browser_history.py; this module turns them into the agent's tools and wires them to the app.

Tools (all act on the device's own tab; short labels in tool_labels.py):
  browser_downloads()                          what the pages of this tab downloaded: name, size, state, site
  browser_save_download(name, save_as?)        copy one finished download into the user's library; programs never
  browser_upload(selector, files)              put the user's files (uploads/... or files/...) into a file input — an approval card naming
                                               the files and the site, every time (never remembered)
  browser_save_pdf(name?)                      the page as a PDF in the library
  browser_private(on, use_login_of?)           private browsing for a sensitive one-off visit: a context of its own, nothing kept
  browser_site_state(action, site)             save / load / forget / list a site's sign-in state (cookies + local storage, memory only)
  find_login_code(site, selector?, submit?)    a code the site mailed in the last 15 minutes: shown to the user on an approval card and typed
                                               only after Allow; the model sees "Found the code (6 digits)", never the code. During a
                                               sign-in the agent started on that site, a code from that site's own mail is typed without
                                               the card (auto_code below; the owner's setting, default on)
  browser_look()                               a picture of the visible page with a box and its ref (e12) on every control
  browser_human_check(action, x?, y?, text?)   the agent's own try at a human check on its page (challenge.py): a picture, a click or
                                               keys on the check itself, or hand it to the user now
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from ..agent import Ours, Tool, ToolOutput, current_call, emit_ui, handover
from ..agent.fence import data
from ..browser_history import FRAME_BYTES, FRAME_WIDTH, ActionArchive
from ..deliverables import LibraryFileError
from ..home import HomeError
from ..i18n import t
from .browser import BrowserPolicyError, BrowserSession, browser_owner, code_mask, one_run_tab, site_of
from .challenge import CHECK_TRIES, Challenge
from .downloads import DownloadArea, DownloadError, blocked_name, clean_name
from .login_code import AUTO_WINDOW_SECONDS, FoundCode, find_code, pick_auto_code
from .takeover import TakeoverJournal

NO_VISION_LOOK = "This model can't see pictures: use browser_read (only: controls)"
LOOK_HEAD = ("A picture of the visible part of the page, {w}x{h} pixels. Boxes carry refs (e12): click those with browser_click "
             "selector e12. For a point or a drag on this picture (things without a ref), load browser_click_at / browser_drag with find_tools.")
NO_VISION_CHECK = ("[The model you are running on cannot look at pictures, so the picture was not seen. Try the check from the page "
                   "text with your browser steps, or hand it to the user with action hand_over.]")
CODE_MINUTES = 15          # how far back find_login_code looks
CODE_HOLD_SECONDS = 600    # a found code waits this long for the user's answer, in memory only
AUTO_CODE_WAIT_SECONDS = 40.0   # after a sign-in asks for a code, the site's own mail is waited for this long before asking the user
AUTO_CODE_FIND_WAIT_SECONDS = 15.0   # ...and this long when the agent calls find_login_code later in that sign-in
AUTO_CODE_POLL_SECONDS = 8.0    # the mailbox is looked at this often meanwhile
ARCHIVED_TOOLS = ("browser_", "confirm_purchase", "purchase_result", "find_login_code")


class BrowserExtras:
    def __init__(self, session: BrowserSession, *, home: Any = None, files: Any = None, mail: Any = None, archive: ActionArchive | None = None,
                 notify: Callable[[str, str, int | None, bool], Any] | None = None, timezone: Callable[[], str] | None = None,
                 locale: str = "", download_dir: str | None = None, clock: Callable[[], float] = time.monotonic,
                 auto_codes: Callable[[], bool] | None = None, vault_log: Callable[..., Any] | None = None):
        """`auto_codes`: the owner's "Use codes from my mail automatically" (default on); `vault_log(item, action, detail)`: where a code
        typed without asking is recorded for the owner (never the code itself)."""
        self.session, self.home, self.files, self.mail, self.archive, self.notify = session, home, files, mail, archive, notify
        self.clock = clock
        self.wall: Callable[[], float] = time.time           # mail times are wall-clock times
        self.auto_codes = auto_codes or (lambda: True)
        self.vault_log = vault_log
        self.code_wait, self.find_wait, self.code_poll = AUTO_CODE_WAIT_SECONDS, AUTO_CODE_FIND_WAIT_SECONDS, AUTO_CODE_POLL_SECONDS
        session.timezone = timezone or (lambda: "")
        session.locale = locale
        session.downloads = DownloadArea(download_dir or None)
        session.journal = TakeoverJournal(session.cdp_session)
        session.has_mailbox = self.has_mailbox
        session.auto_code = self.auto_code
        session.check_listeners.append(self._on_check)
        self._codes: dict[str, tuple[FoundCode, float]] = {}   # tool call id -> the code found for it (until typed or declined)

    def has_mailbox(self) -> bool:
        try:
            return bool(self.mail is not None and self.mail.accounts())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ a sign-in code from the site's own mail, typed without asking
    def sign_in_for(self, host: str) -> dict[str, Any] | None:
        """The sign-in the agent itself started on this tab, in this task, on the site of `host` (same registrable domain), while it is
        fresh enough for a code to be typed without asking."""
        from ..wallet import site_key
        from ..workers import current_turn

        rec = self.session.sign_ins.get(browser_owner.get())
        if not rec or not host or rec.get("site") != site_key(host):
            return None
        if rec.get("task") != (current_turn.get() or {}).get("task_id") or self.wall() - float(rec.get("at") or 0) > AUTO_WINDOW_SECONDS:
            return None
        return rec

    async def auto_code(self, host: str, wait: float | None = None) -> str | None:
        """During a sign-in the agent started on `host`'s site, find the code that site mailed (login_code.pick_auto_code: the right
        time, the site's own sender, one clear code) and type it into the code box of that site's page, without asking, when the owner
        allows it. Returns what the agent is told (who sent it, never the code), or None: the normal flow (find_login_code's card, or
        asking the user) takes over. Nothing about the code is logged or kept."""
        from ..wallet import site_key

        if self.mail is None or not self.auto_codes() or not self.has_mailbox():
            return None
        rec = self.sign_in_for(host)
        if rec is None:
            return None
        deadline = self.wall() + (self.code_wait if wait is None else wait)
        while True:
            try:
                msgs = await self.mail.recent(AUTO_WINDOW_SECONDS / 60 + 2)
            except Exception:  # noqa: BLE001 - a mailbox problem: the normal flow
                return None
            found, why = pick_auto_code(msgs, rec["site"], float(rec["at"]), self.wall())
            if found is not None or why in ("ambiguous", "late") or self.wall() >= deadline:
                break
            await asyncio.sleep(max(0.0, min(self.code_poll, deadline - self.wall())))
        if found is None:
            return None
        where = self.session.current_host()
        if site_key(where) != rec["site"]:     # typed only into that site's own page
            return None
        try:
            text = await self.session.fill_code(found.code)
        except BrowserPolicyError:
            return None
        finally:
            code_len = len(found.code)
            found.code = ""
        self.session.sign_ins.pop(browser_owner.get(), None)   # one code per sign-in
        if self.vault_log is not None:
            try:
                self.vault_log("mail-code", "code-typed", f"typed the sign-in code from {found.sender} mail on {where} ({found.matched})")
            except Exception:  # noqa: BLE001 - the log never stops the sign-in
                pass
        await self.session.card(t("Typed the sign-in code from {sender} mail", sender=found.sender), force=True)
        return (f"Found a code from {found.sender} (the site's own mail; matched by {found.matched}) and typed it ({code_len} "
                f"characters) into the sign-in page of {where} without asking, as the user's setting allows. The code is not kept. Tell "
                f"the user in one short line that you typed the sign-in code from {found.sender} mail.\n\n{text}")

    # ------------------------------------------------------------------ a bot check: tell the user (chat event, notification)
    async def _on_check(self, key: str, c: Challenge, host: str) -> None:
        """Without an app turn that hands the check over (handover), the old way: a chat event and, unattended, a notification. With
        one, the hand-over's own card and notification say it (one, not two)."""
        from ..workers import current_turn

        if handover.get() is not None:
            return
        await emit_ui({"type": "browser_check", "tab": key, "site": host, "vendor": c.vendor, "name": c.name, "kind": c.kind})
        turn = current_turn.get() or {}
        if self.notify is not None and turn.get("unattended"):
            self.notify(t("A human check is needed"), t("{site} is showing a screen that checks whether you are a person. Open the browser tab and do it yourself.", site=host or t("The site")),
                        turn.get("task_id"), True)

    # ------------------------------------------------------------------ the pictures of unattended runs
    def wrap(self, tools: list[Tool]) -> None:
        """After each successful browser step of a background job or an automatic run, keep a picture of its tab (browser_history.py)."""
        if self.archive is None:
            return
        for t in tools:
            if not t.name.startswith(ARCHIVED_TOOLS) or getattr(t.handler, "_archived", False):
                continue
            orig = t.handler

            async def handler(args: dict[str, Any], _orig: Any = orig, _name: str = t.name) -> Any:
                out = await _orig(args)
                await self.archive_step(_name, args)
                return out

            handler._archived = True  # type: ignore[attr-defined]
            t.handler = handler

    async def archive_step(self, action: str, args: dict[str, Any]) -> None:
        key = browser_owner.get()
        if self.archive is None or not one_run_tab(key):
            return
        from ..workers import current_turn

        try:
            page = self.session.raw_page(key)
            url = str(page.url or "") if page is not None else ""
            title = str(await page.title())[:200] if page is not None and url.startswith(("http://", "https://")) else ""
            jpeg, w, h = await self.session.screenshot(key, FRAME_WIDTH, FRAME_BYTES)
            self.archive.record(key, jpeg, url=url, title=title, action=action, detail=step_detail(action, args, self.session),
                                task_id=(current_turn.get() or {}).get("task_id"), width=w, height=h)
        except Exception:  # noqa: BLE001 - the archive never breaks a step
            pass

    # ------------------------------------------------------------------ files
    def _user_file(self, path: str) -> tuple[str, bytes]:
        p = str(path or "").strip().lstrip("/")
        if p.startswith("uploads/") and self.home is not None:
            real = self.home.upload_path(p)
        elif p.startswith("files/") and self.files is not None:
            real = self.files.path(p.split("/", 1)[1])
        else:
            raise HomeError(f"{path!r}: only the user's uploads (uploads/...) and library files (files/...) can be uploaded")
        if real.stat().st_size > 50 * 1024**2:    # checked before reading: an upload can be a 2 GB video
            raise HomeError(f"{real.name} is larger than 50 MB")
        return real.name, real.read_bytes()

    def tools(self) -> list[Tool]:
        s = self.session
        obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731

        async def shown(text: str, status: str) -> str:
            await s.card(status)
            return text

        async def downloads(args: dict[str, Any]) -> str:
            rows = s.downloads.list(browser_owner.get()) if s.downloads else []
            if not rows:
                return Ours("Nothing was downloaded in this tab yet. A download starts when a page sends a file (a download link or button).")
            lines = []
            for r in rows:
                size = human_size(r["bytes"])
                why = f" ({r['reason']})" if r["reason"] else ""
                lines.append(f"- {r['name']} · {size} · {r['state'].replace('_', ' ')}{why} · from {r['site'] or 'the page'}")
            # the file names come from the sites: marked as external data, fenced by the loop
            return "Downloads of this tab (newest first). Copy one into the user's library with browser_save_download(name):\n" + data("\n".join(lines))

        async def save_download(args: dict[str, Any]) -> str:
            if s.downloads is None or self.files is None:
                return "error: this server has no library to save downloads into"
            try:
                d = s.downloads.find(browser_owner.get(), str(args.get("name", "")))
                name = clean_name(str(args.get("save_as") or d.name))
                if blocked_name(name):
                    return "error: programs and scripts are not kept in the library"
                saved = self.files.save(name, s.downloads.read(d))
            except (DownloadError, LibraryFileError, HomeError) as e:
                return f"error: {e}"
            return f"Saved to the user's library as files/{saved} ({human_size(d.received)})."

        async def upload(args: dict[str, Any]) -> str:
            got = [self._user_file(p) for p in args.get("files") or []]
            out = await s.upload(str(args.get("selector") or "input[type=file]"),
                                 [(n, mimetypes.guess_type(n)[0] or "application/octet-stream", b) for n, b in got])
            return await shown(out, t("Uploading files"))

        def check_upload(args: dict[str, Any]) -> str | None:
            paths = args.get("files")
            if not isinstance(paths, list) or not paths or len(paths) > 10:
                return "files must list 1 to 10 paths (uploads/... or files/...)"
            try:
                total = sum(len(self._user_file(str(p))[1]) for p in paths)
            except (HomeError, LibraryFileError, OSError) as e:
                return str(e)
            return None if total <= 50 * 1024**2 else "the files are larger than 50 MB together"

        async def describe_upload(args: dict[str, Any]) -> str:
            names = ", ".join(str(p).rsplit("/", 1)[-1] for p in args.get("files") or [])[:160]
            return t("Upload my files to {host}: {names}. The files are sent to this site.", host=s.current_host() or t("blank page"), names=names)

        async def save_pdf(args: dict[str, Any]) -> str:
            if self.files is None:
                return "error: this server has no library to save into"
            try:
                data, title, url = await s.save_pdf()
                base = clean_name(str(args.get("name") or title or urlsplit(url).hostname or "page"))
                saved = self.files.save(base if base.lower().endswith(".pdf") else base + ".pdf", data)
            except (BrowserPolicyError, LibraryFileError, HomeError) as e:
                return f"error: {e}"
            return f"Saved the page as a PDF in the user's library: files/{saved} ({human_size(len(data))})."

        async def private(args: dict[str, Any]) -> str:
            on = bool(args.get("on", True))
            carry = str(args.get("use_login_of") or "").strip()
            try:
                got = await s.set_private(on, carry if on else "")
            except BrowserPolicyError as e:
                return f"error: {e}"
            if not on:
                return "Private browsing is off: this tab uses the normal browser profile again; everything of the private tab is gone."
            more = (f" The sign-in of {carry} was copied in ({got['carried']['cookies']} cookies, {got['carried']['storage']} storage items)."
                    if got.get("carried") else "")
            return ("This tab now browses privately: no cookies, sign-ins, storage or history from the normal profile, and nothing is kept "
                    "when it closes or private browsing is turned off." + more + " Open the site with browser_open.")

        async def site_state(args: dict[str, Any]) -> str:
            action = str(args.get("action") or "")
            try:
                if action == "list":
                    sites = s.saved_sites()
                    return "Saved sign-in states: " + (", ".join(sites) if sites else "(none)")
                site = str(args.get("site") or "")
                if action == "save":
                    n = await s.save_site_state(site)
                    return (f"Saved the sign-in state of {site_of(site)} from this tab ({n['cookies']} cookies, {n['storage']} storage items), in memory "
                            "for 12 hours. Another tab (a private one, or a background job's) can load it with action=load.")
                if action == "load":
                    n = await s.load_site_state(site)
                    return f"Loaded the sign-in state of {site_of(site)} into this tab ({n['cookies']} cookies, {n['storage']} storage items). Open the site now."
                if action == "forget":
                    return "Forgotten." if s.forget_site_state(site) else "There was nothing saved for that site."
            except BrowserPolicyError as e:
                return f"error: {e}"
            return "error: action must be save, load, forget or list"

        async def forget_site(args: dict[str, Any]) -> str:
            site, user_said = str(args.get("site") or ""), str(args.get("user_said") or "")
            try:
                res = await s.forget_site(site, user_said=user_said)
            except BrowserPolicyError as e:
                return f"Error: {e}"
            return await shown(res, t("Clearing site data"))

        # ---- one-time codes from the user's mail
        def purge_codes() -> None:
            now = self.clock()
            for k in [k for k, (_, at) in self._codes.items() if now - at > CODE_HOLD_SECONDS]:
                self._codes.pop(k, None)

        async def code_hold(args: dict[str, Any]) -> str | None:
            held = await s.hold_for_user(args)
            if held:
                return held
            purge_codes()
            try:
                site = site_of(str(args.get("site") or "") or s.current_host())
            except BrowserPolicyError as e:
                return f"error: {e}"
            if (why := getattr(s, "approve_pending", lambda _s: None)(site)) is not None:   # an approval on the phone has no code to find (2026-11-10)
                return why
            if not self.has_mailbox():
                return ("No mailbox is connected, so there is no mail to look in. Ask the user to type the code into the page themselves in the "
                        "live browser (Browser, then Take control), or to connect their mailbox. Never ask for the code in chat.")
            typed = await self.auto_code(site, wait=self.find_wait)   # during the agent's own sign-in on that site: no card
            if typed:
                return typed
            try:
                found = find_code(await self.mail.recent(CODE_MINUTES), site)
            except Exception as e:  # noqa: BLE001 - a mailbox problem is reported, not raised
                return f"error: could not read the mailbox ({str(e)[:160]})"
            if found is None:
                return (f"No code found: no message from {site} with a code arrived in the last {CODE_MINUTES} minutes. If the site just sent it, "
                        "wait a minute and try once more; otherwise ask the user to type it in the live browser.")
            self._codes[current_call.get()] = (found, self.clock())
            return None

        def found_now() -> FoundCode | None:
            got = self._codes.get(current_call.get())
            return got[0] if got else None

        async def code_describe(args: dict[str, Any]) -> str:
            f = found_now()
            return t("Enter this code? {code} — from {sender}", code=code_mask(f.code), sender=f.sender) if f else t("Enter the code found in your mail.")

        def code_extra(_approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
            f = found_now()
            if f is None:
                return {}
            # only in the live card (memory): the log and the notification get the masked line from code_describe
            return {"detail": t("Enter this code? {code} — from {sender}", code=f.code, sender=f.sender),
                    "login_code": {"code": f.code, "from": f.sender, "subject": f.subject, "received_at": f.at or None}}

        async def code_enter(args: dict[str, Any]) -> str:
            got = self._codes.pop(current_call.get(), None)
            if got is None:
                return "error: the code is no longer held (it waited too long); call find_login_code again"
            f = got[0]
            try:
                text = await s.fill_code(f.code, selector=str(args.get("selector") or ""), submit=bool(args.get("submit", True)))
            except BrowserPolicyError as e:
                return f"error: {e}"
            return await shown(f"Found the code ({len(f.code)} digits) and typed it into the page after the user allowed it. The code is not kept.\n\n{text}",
                               t("Entering the verification code"))

        # ---- the agent's own try at a human check on its page (at most CHECK_TRIES page readings, then it goes to the user)
        async def human_check(args: dict[str, Any]) -> Any:
            action = str(args.get("action") or "")
            try:
                if action == "hand_over":
                    await s.page()
                    s.give_up_check()
                    return await shown(await s.snapshot(), t("Waiting for you to confirm you are a person"))
                if action == "look":
                    found, jpeg, w, h, boxes = await s.check_picture()
                    if found is None:
                        return Ours("There is no human check on this page now: go on with browser_read.")
                    if jpeg is None:
                        return Ours("Could not take a picture of the page. Try the check with browser_click, or hand it over.")
                    where = "; ".join(f"x {b['x']}-{b['x'] + b['w']}, y {b['y']}-{b['y'] + b['h']}" for b in boxes[:4])
                    head = (f"A picture of the page with the human check ({found.name}), {w}x{h} pixels. "
                            + (f"The check is at {where}. " if where else "The whole page is the check. ")
                            + "Click with action click and x, y in this picture's pixels; type into what you clicked with action type.")
                    return ToolOutput(f"Title: {data(await (await s.page()).title())}",
                                      ["data:image/jpeg;base64," + base64.b64encode(jpeg).decode()], head=head, no_vision=NO_VISION_CHECK)
                if action == "click":
                    text = await s.click_in_check(float(args.get("x", -1)), float(args.get("y", -1)))
                    return await shown(text, t("Trying the human check"))
                if action == "type":
                    return await shown(await s.type_in_check(str(args.get("text") or "")), t("Trying the human check"))
            except (BrowserPolicyError, TypeError, ValueError) as e:
                return Ours(f"error: {e}")
            return Ours("error: action must be look, click, type or hand_over")

        async def look(args: dict[str, Any]) -> Any:
            try:
                jpeg, w, h, _n = await s.look()
            except BrowserPolicyError as e:
                return Ours(f"error: {e}")
            if jpeg is None:
                return Ours("Could not take a picture of the page. Read it with browser_read instead.")
            page = await s.page()
            return ToolOutput(f"URL: {page.url}\nTitle: {data(await page.title())}",
                              ["data:image/jpeg;base64," + base64.b64encode(jpeg).decode()],
                              head=LOOK_HEAD.format(w=w, h=h), no_vision=NO_VISION_LOOK)

        return [
            Tool("browser_downloads", "List what your browser tab downloaded (programs and scripts never are).", obj({}, []), downloads,
                 untrusted=True, source="page"),
            Tool("browser_save_download", "Copy a finished download of your browser tab into the user's library (files/).",
                 obj({"name": {"type": "string", "description": "as browser_downloads lists it"},
                      "save_as": {"type": "string", "description": "another file name"}}, ["name"]), save_download),
            Tool("browser_upload", "Put the user's files into a file input of the page; the user approves every upload.",
                 obj({"selector": {"type": "string", "description": "the file input, or the button that adds a photo or video (the hidden input "
                                                                         "behind it is found)"},
                      "files": {"type": "array", "items": {"type": "string"}, "description": "paths under uploads/ or files/"}},
                     ["selector", "files"]),
                 upload, requires_approval=True, untrusted=True, source="page", validate=check_upload, describe=describe_upload,
                 declined="The user did not allow uploading those files. Do not retry; ask what they want instead."),
            Tool("browser_save_pdf", "Save the page of your browser tab as a PDF in the user's library (files/).",
                 obj({"name": {"type": "string"}}, []), save_pdf),
            Tool("browser_private", "Private browsing for a sensitive one-off visit: on gives this tab a fresh context that keeps nothing; "
                 "off goes back to the normal profile.",
                 obj({"on": {"type": "boolean"},
                      "use_login_of": {"type": "string", "description": "a site whose sign-in is copied into the private tab"}}, ["on"]),
                 private),
            Tool("browser_site_state", "Move a site's sign-in state between tabs (save from this tab, kept in memory 12 hours; load into "
                 "this tab; forget; list). Normal tabs already share sign-ins: this is for private tabs or a lost sign-in.",
                 obj({"action": {"type": "string", "enum": ["save", "load", "forget", "list"]}, "site": {"type": "string"}}, ["action"]),
                 site_state),
            Tool("browser_forget_site", "Remove cookies and site data of that site's domain from the browser's default context, and drop its saved sign-in state. Use it only right after the user agreed to clear that site's data in this conversation. user_said must be the user's words agreeing to clear cookies and retry (required to ensure the user explicitly consented before data is removed).",
                 obj({"site": {"type": "string", "description": "the site to forget (e.g. example.com)"},
                      "user_said": {"type": "string", "description": "the user's words agreeing to clear that site's cookies and site data"}},
                     ["site", "user_said"]),
                 forget_site),
            Tool("find_login_code", "A sign-in page wants a one-time code the site sent by email: finds it in the user's mailbox (last "
                 f"{CODE_MINUTES} minutes) and types it in once the user allows. You never see the code.",
                 obj({"site": {"type": "string", "description": "default: the page's"},
                      "selector": {"type": "string", "description": "the code box, if not found by itself"},
                      "submit": {"type": "boolean", "description": "press Enter after (default true)"}}, []),
                 code_enter, requires_approval=True, untrusted=True, source="page", hold=code_hold, describe=code_describe, approval_extra=code_extra,
                 declined="The user did not want that code typed. Do not retry; ask them how to go on."),
            Tool("browser_look", "A picture of the visible part of your page with a box and its ref (e12) on everything that can be "
                 "clicked or typed into. For a page that is mostly pictures, a canvas or a map, or when a reading gives no ref for what "
                 "you need; otherwise browser_read is cheaper. To click a point or drag on it (canvas, map, slider), load browser_click_at and "
                 "browser_drag with find_tools.",
                 obj({}, []), look, untrusted=True, source="page", hold=s.hold_for_user),
            Tool("browser_human_check", f"Try the human check on your page yourself, at most {CHECK_TRIES} tries: look (a picture of the page), "
                 "click (x, y in that picture, on the check only), type (into what you clicked), or hand_over (give it to the user now, "
                 "also for a kind you cannot do). No outside solving services, and never change the page's scripts.",
                 obj({"action": {"type": "string", "enum": ["look", "click", "type", "hand_over"]},
                      "x": {"type": "integer"}, "y": {"type": "integer"}, "text": {"type": "string"}}, ["action"]),
                 human_check, untrusted=True, source="page", hold=s.hold_for_user),
        ]


def human_size(n: int) -> str:
    return f"{n} bytes" if n < 1024 else f"{n / 1024:,.0f} KB" if n < 1024**2 else f"{n / 1024**2:,.1f} MB"


def step_detail(action: str, args: dict[str, Any], session: Any = None) -> str:
    """Where a step acted, for the archive: never typed text, codes or card data. A ref (e12) is shown by the words the page reading
    gave it when the session is at hand."""
    if action == "browser_open":
        return str(args.get("url") or "")[:200]
    if action in ("browser_click", "browser_type", "browser_upload"):
        files = args.get("files") if action == "browser_upload" else None
        sel = str(args.get("selector") or "")
        if session is not None and hasattr(session, "selector_words"):
            sel = session.selector_words(sel)
        return sel[:120] + (f" ← {', '.join(str(f).rsplit('/', 1)[-1] for f in files)}"[:160] if files else "")
    if action == "browser_click_at":
        return f"point {args.get('x')},{args.get('y')}"[:60]
    if action == "browser_drag" and isinstance(args.get("path"), list) and args["path"]:
        a, b = args["path"][0], args["path"][-1]
        return (f"drag {a.get('x')},{a.get('y')} → {b.get('x')},{b.get('y')}" if isinstance(a, dict) and isinstance(b, dict) else "drag")[:60]
    if action in ("browser_save_download", "browser_save_pdf"):
        return str(args.get("name") or "")[:120]
    if action == "browser_login":
        return str(args.get("name") or "")[:60]
    return ""
