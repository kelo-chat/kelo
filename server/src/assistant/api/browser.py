"""The agent's browser tabs: the list with pictures, starting a background task in a tab, closing tabs nobody needs (the sweeper), the pictures of unattended runs, and the live view of a tab with the user taking control (LIVE VIEW API)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from typing import Any
from fastapi import Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field
from ..activity import title_from
from .. import i18n
from ..tools.browser import (
    BrowserPolicyError, CHAT_TAB_IDLE_SECONDS, browser_owner, SITE_TAB_IDLE_SECONDS, TAB_SWEEP_SECONDS, is_site_tab, one_run_tab,
)
from ..tools.sites import registrable
from ..chat_views import PHONE_KEY, VIEW_IDLE_SECONDS, ChatViews
from ..live_source import LiveSource, LiveSources
from ..tools.live import Frame, FrameGate, LiveEnded, LiveError, PointerThrottle, TICKET_SECONDS, pump_frames, pump_meta, viewer_css_size
from ..tools.vnc import CONNECT_SECONDS, ClientFilter, VncError, VncUnavailable
from ..tools.work_browser import work_tab
from ..browser_history import history_routes
from ..server import BACKGROUND_NOTE, ENDED_TABS_KEPT, HELD_STATUS, POLL_WAIT_MAX
from ..workers import STATUS_HEAD
from fastapi import APIRouter
from .context import AppContext

log = logging.getLogger("uvicorn.error")


class BrowserTaskIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    url: str | None = Field(default=None, max_length=2000)
    secrets: str | None = Field(default=None, pattern="^(allow|mask)$")


class TabIn(BaseModel):
    action: str = Field(max_length=10)   # "activate" | "close" (C1d: a tab of the held task window)


class ControlIn(BaseModel):
    take: bool
    by: str = Field(default="button", max_length=20)   # "handover" when the app took the screen because Kelo asked; else the owner's press


def _number(text: str | None) -> float | None:
    """A number from a query string (None when it is missing or not one)."""
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    app = ctx.app
    auth_blocked = ctx.auth_blocked
    auth_failed = ctx.auth_failed
    browser_more = ctx.browser_more
    device_of = ctx.device_of
    held_by = ctx.held_by
    hub = ctx.hub
    owner_tasks = ctx.owner_tasks
    pending_approvals = ctx.pending_approvals
    require_auth = ctx.require_auth
    running_turns = ctx.running_turns
    schedules = ctx.schedules
    secret_check = ctx.secret_check
    session = ctx.session
    start_background = ctx.start_background
    store = ctx.store
    sweep = ctx.sweep
    tab_failed = ctx.tab_failed
    live_sources: LiveSources = ctx.live_sources   # the browser's hub and the Android device, by key (live_source.py)
    turn_line = ctx.turn_line
    turns = ctx.turns
    vnc = ctx.vnc   # the X screen of the live view (tools/vnc.py), None without a browser
    workers = ctx.workers
    chat_views: ChatViews = ctx.chat_views   # the browser tabs and the phone shown in the chat (chat_views.py)

    # ---- browser tabs (one per phone, per schedule, per background task) ----------------------------------------------------------
    THUMB_WIDTH, THUMB_BYTES, SHOT_WIDTH, SHOT_BYTES = 360, 60_000, 1080, 300_000
    thumbs = ctx.thumbs   # owner -> (taken at, url, (data url, w, h)); a list request reuses one for 10 s

    def tab_label(key: str) -> str:
        if key == "owner":
            return i18n.t("Main tab")
        if key.isdigit():
            dev = next((d for d in store.devices() if str(d["id"]) == key), None)
            return dev["name"] if dev else i18n.t("Device {key}", key=key)
        if key.startswith("schedule:") and key[9:].isdigit():
            sch = schedules.get(int(key[9:]))
            return i18n.t("Automatic run · {name}", name=sch['name']) if sch else i18n.t("Automatic run")
        if key in owner_tasks and (task := activity.task(owner_tasks[key])):
            return task["title"]
        return key

    @router.get("/api/browser/sessions", dependencies=[Depends(require_auth)])
    async def get_browser_sessions(thumbnails: bool = True) -> dict[str, Any]:
        device = live_sources.device
        if session is None:
            return {"available": device is not None, "items": [await device.listing(running_turns())] if device is not None else []}
        busy = {t.owner: t for t in running_turns()}
        items = []
        for tab in await session.tabs():
            key = tab["key"]
            image, w, h = None, 0, 0
            if thumbnails:
                hit = thumbs.get(key)
                if hit and time.monotonic() - hit[0] < 10 and hit[1] == tab["url"]:
                    image, w, h = hit[2]
                else:
                    jpeg, w, h = await session.screenshot(key, THUMB_WIDTH, THUMB_BYTES)
                    image = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode() if jpeg else None
                    thumbs[key] = (time.monotonic(), tab["url"], (image, w, h))
            t = busy.get(key)
            holder = tab.get("controlled_by")
            waiting = not t and workers.waits_for_owner(key)   # a job that asked the owner and waits for the answer
            items.append({"id": key, "label": tab_label(key), "source": "browser", "browser": tab.get("browser", "main"),
                          "tabs": await session.window_tabs(key),
                          "kind": ("owner" if key == "owner" else "device" if key.isdigit() else key.split(":", 1)[0]),
                          "title": tab["title"], "url": tab["url"], "host": tab["host"],
                          "status": "working" if t else ("waiting" if waiting else "failed" if tab_failed.get(key) else "idle"),
                          "status_text": i18n.t(HELD_STATUS) if holder else (i18n.t(STATUS_HEAD["needs_input"]) if waiting else tab["status"]),
                          "controller": "user" if holder else "agent", "controlled_by": holder, "viewers": hub.watching(key) if hub else 0,
                          "last_activity": tab["last_activity"], "task_id": t.task_id if t else owner_tasks.get(key),
                          "thumbnail": image, "thumbnail_width": w, "thumbnail_height": h, "ended_at": None})
        open_now = {i["id"] for i in items}
        items += [{k: v for k, v in e.items() if k != "shot"} | ({} if thumbnails else {"thumbnail": None})
                  for key, e in ended_tabs.items() if key not in open_now]
        items.sort(key=lambda x: ({"working": 0, "ended": 2}.get(x["status"], 1), -(x["ended_at"] or x["last_activity"] or 0)))
        if device is not None:   # the Android device the owner set up: a live source next to the tabs
            items.insert(0, await device.listing(running_turns()))
        return {"available": True, "items": items}

    @router.post("/api/browser/sessions/{key}/tabs/{tab}", dependencies=[Depends(require_auth)])
    async def browser_window_tab(key: str, tab: str, body: TabIn, request: Request) -> dict[str, Any]:
        """Take the owner's own view of a held task window: switch to one of its tabs or close one (C1d). Only the device that holds
        the tab may; the window keeps its last tab (the task closes it)."""
        if session is None or session.controlled_by(key) != device_of(request):
            raise HTTPException(409, "take control of this tab first")
        if body.action not in ("activate", "close"):
            raise HTTPException(422, "action is activate or close")
        try:
            if body.action == "activate":
                await session.switch_tab(key, tab)
            else:
                await session.close_window_tab(key, tab)
        except BrowserPolicyError as e:
            raise HTTPException(409, str(e)) from None
        return {"tabs": await session.window_tabs(key)}

    @router.get("/api/browser/sessions/{key}/screenshot", dependencies=[Depends(require_auth)])
    async def get_browser_screenshot(key: str) -> Response:
        if session is None or not (session.has_tab(key) or key in ended_tabs):
            raise HTTPException(404, "no such tab")
        if session.has_tab(key):
            jpeg, _, _ = await session.screenshot(key, SHOT_WIDTH, SHOT_BYTES)
        else:
            jpeg = ended_tabs[key]["shot"]                  # the picture kept when the finished run's tab closed
        if jpeg is None:
            raise HTTPException(404, "this tab has no picture to show (a blank page)")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/api/browser/sessions", dependencies=[Depends(require_auth)], status_code=201)
    async def start_browser_task(body: BrowserTaskIn) -> dict[str, Any]:
        """Hand the agent a task to do in a tab of its own, while the user does something else (an unattended turn)."""
        if session is None:
            raise HTTPException(404, "this server has no browser")
        body.prompt = await secret_check(body.prompt, body.secrets)   # a pasted key or password stops here (409), as in chat
        key = f"task:{secrets.token_hex(4)}"
        prompt = body.prompt.strip() + (f"\nStart at: {body.url.strip()}" if body.url and body.url.strip() else "")
        title = title_from(body.prompt)
        turn = start_background(prompt, title=title, source="background", owner=key, note=BACKGROUND_NOTE, saved_as=i18n.t("[Task handed over · {title}]", title=title))
        return {"id": key, "task_id": turn.task_id, "turn_id": turn.id}

    @router.get("/api/browser/refusal-pauses", dependencies=[Depends(require_auth)])
    async def get_refusal_pauses() -> dict[str, Any]:
        """The sites the browser does not load for a while after they refused it (browser.py REFUSAL_PAUSE_SECONDS, six hours)."""
        return {"pauses": session.refusal_pauses() if session is not None else []}

    @router.delete("/api/browser/sign-ins/{site}", dependencies=[Depends(require_auth)])
    async def forget_browser_sign_in(site: str) -> dict[str, bool]:
        """The owner signs Kelo's browser out of a site: cookies and site data go, and the sign-in row goes (brief B4). A saved login stays."""
        if session is not None:
            try:
                await session.forget_site(site)   # also drops the sign-in row (on_site_forgotten)
            except BrowserPolicyError as e:
                raise HTTPException(400, str(e)) from None
        store.forget_browser_sign_in(registrable(site.strip().lower()))   # the row goes even when no browser is running
        return {"ok": True}

    @router.delete("/api/browser/profiles/{profile}", dependencies=[Depends(require_auth)])
    async def delete_browser_profile(profile: str) -> dict[str, bool]:
        """The owner deletes a named browser profile in the Keychain: its open windows close, its units and sign-in rows go (brief C2d)."""
        if not any(p["id"] == profile for p in store.browser_profiles()):
            raise HTTPException(404, "no such profile")
        if session is not None:
            await session.close_profile_windows(profile)
        store.delete_browser_profile(profile)
        return {"ok": True}

    @router.delete("/api/browser/profiles/{profile}/sites/{site}", dependencies=[Depends(require_auth)])
    async def forget_browser_profile_site(profile: str, site: str) -> dict[str, bool]:
        """The owner signs a named profile out of a site: its open contexts lose the site's cookies, its unit and the row go."""
        if not any(p["id"] == profile for p in store.browser_profiles()):
            raise HTTPException(404, "no such profile")
        if session is not None:
            await session.forget_profile_site(profile, site)
        store.forget_browser_unit(profile, registrable(site.strip().lower()))
        return {"ok": True}

    @router.delete("/api/browser/refusal-pauses/{site}", dependencies=[Depends(require_auth)])
    async def clear_refusal_pause(site: str) -> dict[str, bool]:
        """The owner clears a site's pause: the browser may load it again at once."""
        if session is None or not session.clear_pause(site):
            raise HTTPException(404, "not paused")
        return {"ok": True}

    @router.delete("/api/browser/sessions/{key}", dependencies=[Depends(require_auth)])
    async def close_browser_session(key: str) -> dict[str, bool]:
        if session is not None and not session.has_tab(key) and ended_tabs.pop(key, None) is not None:
            return {"ok": True}                              # an ended entry: just forget it
        if session is None or not session.has_tab(key):
            raise HTTPException(404, "no such tab")
        if any(t.owner == key for t in running_turns()):
            raise HTTPException(409, "the agent is working in this tab; stop that task first")
        chat_views.close(key, "owner")   # the owner closed it: its view in the chat goes with it
        viewed_tabs.discard(key)
        thumbs.pop(key, None)
        tab_failed.pop(key, None)
        ended_tabs.pop(key, None)
        return {"ok": await session.close_tab(key)}

    # ---- closing tabs nobody needs any more (see "Tab lifecycle" next to the BrowserTab contract) --------------------------------
    ended_tabs = ctx.ended_tabs   # schedule/task tab -> its finished entry (BrowserTab + "shot": the large JPEG), oldest first

    viewed_tabs: set[str] = set()   # run tabs the owner watched or held (not shown in the chat): kept VIEW_IDLE_SECONDS after the last use

    def tab_busy(key: str) -> str:
        """Why this tab must stay open right now ("" = it may close). A tab shown in the chat is closed only through its view;
        a run's tab the owner watched or held stays VIEW_IDLE_SECONDS after the last view or use ("viewed")."""
        if chat_views.get(key) is not None:
            return "view"       # shown in the chat: its view ends it (the main agent, the owner, or VIEW_IDLE_SECONDS untouched)
        if (hub is not None and hub.watching(key)) or (vnc is not None and vnc.viewing(key)):
            viewed_tabs.add(key)
            return "viewer"
        if held_by(key):
            viewed_tabs.add(key)
            return "control"
        if any((t := turns.get(p.get("turn_id", ""))) is not None and t.owner == key for p in pending_approvals.values()):
            return "approval"
        if any(t.owner == key for t in running_turns()):
            return "running"
        if workers.holds_tab(key):
            return "worker"     # a background job runs, waits for the user, or asked to keep it
        if key in viewed_tabs and one_run_tab(key) and session is not None and session.idle_for(key) < VIEW_IDLE_SECONDS:
            return "viewed"     # the owner watched this run's tab: it stays like a chat tab until VIEW_IDLE_SECONDS untouched
        return ""

    def view_busy(key: str) -> bool:
        """Work or a wait in this view's tab keeps the view (its idle clock does not run): a turn, a running job, an approval, the owner's hands."""
        if held_by(key) or any(t.owner == key for t in running_turns()):
            return True
        if any((t := turns.get(p.get("turn_id", ""))) is not None and t.owner == key for p in pending_approvals.values()):
            return True
        view = chat_views.get(key)
        return view is not None and bool(view.worker_id) and (r := workers._row(view.worker_id)) is not None and r["status"] == "running"

    async def retire_tab(key: str) -> bool:
        """A finished run's tab: keep its last picture and words as an ended entry, then close it. False = not now (busy, or gone)."""
        if session is None or not session.has_tab(key) or tab_busy(key):
            return False
        info = await session.tab_info(key) or {"title": "", "url": "", "host": "", "status": "", "last_activity": None}
        small, w, h = await session.screenshot(key, THUMB_WIDTH, THUMB_BYTES)
        large, _, _ = await session.screenshot(key, SHOT_WIDTH, SHOT_BYTES)
        if tab_busy(key) or not session.has_tab(key):   # someone came along while the pictures were taken
            return False
        entry = {"id": key, "label": tab_label(key), "kind": key.split(":", 1)[0], "title": info["title"], "url": info["url"],
                 "host": info["host"], "status": "ended", "status_text": info["status"], "controller": "agent", "controlled_by": None, "source": "browser",
                 "viewers": 0, "last_activity": info["last_activity"], "task_id": owner_tasks.get(key),
                 "thumbnail": "data:image/jpeg;base64," + base64.b64encode(small).decode() if small else None,
                 "thumbnail_width": w if small else 0, "thumbnail_height": h if small else 0, "ended_at": time.time(), "shot": large}
        await session.close_tab(key)
        thumbs.pop(key, None)
        tab_failed.pop(key, None)
        ended_tabs.pop(key, None)
        ended_tabs[key] = entry
        while len(ended_tabs) > ENDED_TABS_KEPT:
            ended_tabs.pop(next(iter(ended_tabs)))
        return True

    closing_tabs: set[str] = set()   # tabs whose view closed while a turn or a wait still used them: the sweeper closes them when free

    async def drop_view_tab(key: str) -> bool:
        """A view left the chat: its tab goes too, unless something else still holds it (then the sweeper closes it once it is free)."""
        if key == PHONE_KEY or session is None or not session.has_tab(key):
            return False
        viewed_tabs.discard(key)   # the owner's earlier look does not keep a tab the chat has let go of
        if tab_busy(key):
            closing_tabs.add(key)
            return False
        closing_tabs.discard(key)
        if one_run_tab(key):
            return await retire_tab(key)
        await session.close_tab(key)
        thumbs.pop(key, None)
        tab_failed.pop(key, None)
        return True

    async def close_view(key: str, reason: str) -> bool:
        """The view of `key` leaves the chat (reason: done, owner, idle, gone, cleared). Its tab closes with it when nothing else holds it."""
        if chat_views.close(key, reason) is None:
            return False
        log.info("chat view: closed %s (%s)", key, reason)   # no URLs in the log
        return await drop_view_tab(key)

    async def sweep_tabs() -> list[str]:
        """One pass: views the owner watches or holds, or whose tab was used lately, stay; views untouched for VIEW_IDLE_SECONDS close;
        finished runs' tabs are retired; chat tabs unused for an hour are closed. Returns the tabs closed."""
        closed: list[str] = []
        for view in chat_views.items():
            key = view.key
            if key == PHONE_KEY:
                phone = live_sources.get(PHONE_KEY)
                if phone is not None and (phone.watching(key) or phone.holder(key)):
                    chat_views.touch(key)                    # someone watches the phone or holds it: in use now
                continue
            if (hub is not None and hub.watching(key)) or (vnc is not None and vnc.viewing(key)) or held_by(key):
                chat_views.touch(key)                        # the owner is looking at it or holding it: it is in use now
            elif session is not None and session.has_tab(key):
                chat_views.touch(key, session.idle_for(key))  # the agent's last step in it counts
        for key in chat_views.idle(view_busy):
            if await close_view(key, "idle") and key != PHONE_KEY:
                closed.append(key)
        if session is None:
            return closed
        for view in chat_views.items():
            if view.key != PHONE_KEY and not session.has_tab(view.key):
                chat_views.close(view.key, "gone")          # its tab went away without its view: the list must not keep a dead card
        for key in list(closing_tabs):                      # a view closed while its tab was in use: closed now if it is free
            if chat_views.get(key) is not None or not session.has_tab(key):
                closing_tabs.discard(key)
            elif await drop_view_tab(key):
                closed.append(key)
        for key in session.open_keys():
            why = tab_busy(key)
            if why:
                if why in ("viewer", "control"):
                    session.touch(key)                       # the user looking at or holding a tab is using it
                continue
            if is_site_tab(key):                          # a watcher's site tab: stays between checks, closes when unused for a while
                if session.idle_for(key) >= SITE_TAB_IDLE_SECONDS:
                    await session.close_tab(key)
                    closed.append(key)
            elif one_run_tab(key):
                if await retire_tab(key):
                    closed.append(key)
            elif session.idle_for(key) >= CHAT_TAB_IDLE_SECONDS:
                await session.close_tab(key)
                thumbs.pop(key, None)
                tab_failed.pop(key, None)
                closed.append(key)
        if closed:
            log.info("browser: closed %d tab(s) nobody needs any more", len(closed))
        return closed

    async def tab_sweeper() -> None:
        while True:
            await asyncio.sleep(TAB_SWEEP_SECONDS)
            try:
                await sweep_tabs()
            except Exception:  # noqa: BLE001 - the next pass tries again
                log.warning("browser: tab sweep failed", exc_info=True)

    app.state.sweep_tabs, app.state.retire_tab, app.state.ended_tabs = sweep_tabs, retire_tab, ended_tabs
    app.state.chat_views, app.state.close_view, app.state.viewed_tabs = chat_views, close_view, viewed_tabs

    def resolve_view(target: str) -> str | None:
        """The view key a chat_view target names: 'browser' (this chat's tab), 'phone', a job id (w...) or a tab key."""
        if target in ("phone", PHONE_KEY):
            return PHONE_KEY
        if target == "browser":
            return browser_owner.get() or None
        for key in (target, f"task:{target}"):
            if chat_views.get(key) is not None or (session is not None and session.has_tab(key)):
                return key
        return None

    async def view_can_open(key: str) -> bool:
        if key == PHONE_KEY:
            return live_sources.get(PHONE_KEY) is not None
        return session is not None and session.has_tab(key)

    async def view_refresh(key: str) -> None:
        """The owner asked to see this chat's tab: a new card goes out now, so the picture is current."""
        if session is not None and key == browser_owner.get() and session.has_tab(key):
            info = await session.tab_info(key)
            await session.card(info["status"] if info else "", force=True)

    ctl = ctx.view_control   # the chat_view tool's browser side (chat_views.py); the tool itself is built in server.py
    ctl.resolve, ctl.tab_shown, ctl.close_tab = resolve_view, (lambda k: session is not None and session.has_tab(k)), drop_view_tab
    ctl.can_open, ctl.refresh = view_can_open, view_refresh

    @router.get("/api/chat/views", dependencies=[Depends(require_auth)])
    async def get_chat_views(since: int | None = None) -> dict[str, Any]:
        """What the chat shows as browser tabs and the phone (chat_views.py). Same version as `since`: nothing changed, no pictures sent."""
        if since is not None and since == chat_views.version:
            return {"version": chat_views.version, "unchanged": True, "at": time.time()}
        return {"version": chat_views.version, "items": [v.as_json() for v in chat_views.items()], "at": time.time()}
    app.state.sweep_turns = sweep
    history_routes(router, browser_more.archive if browser_more is not None else None, require_auth)   # pictures of unattended runs' steps

    # ---- live view of a tab and the user's hands on it (see LIVE VIEW API) ------------------------------------------------------
    def on_control(key: str, device: str | None) -> None:
        """Control of a tab changed hands: the reply working in it hears about it (a browser_control turn event)."""
        for t in running_turns():
            if t.owner == key:
                asyncio.ensure_future(t.emit({"type": "browser_control", "id": key, "controller": "user" if device else "agent"}))

    if session is not None:
        session.control_listeners.append(on_control)

    def need_source(key: str) -> LiveSource:
        """The live source that owns `key`: the device for `android` (404 when none is set up), else the browser's hub."""
        got = live_sources.get(key)
        if got is None:
            raise HTTPException(404, "this server has no device set up" if key == "android" else "this server has no browser")
        return got

    def control_view(live: LiveSource, key: str, device: str) -> dict[str, Any]:
        holder = live.holder(key)
        view = {"id": key, "controller": "user" if holder else "agent", "controlled_by": holder, "you_control": holder == device}
        if hasattr(live, "hold_view"):   # hold: "button" | "handover" | "" ; holder_away: the holder has no live connection (another device may take it)
            view.update(live.hold_view(key, device))
        return view

    def live_status(live: LiveSource, key: str, device: str) -> dict[str, Any]:
        t = live.agent_turn(key, running_turns())
        view = control_view(live, key, device)
        agent = {"busy": t is not None, "status": turn_line(t) if t else None, "task_id": t.task_id if t else None,
                 "title": t.title if t else None, "waiting_approval": bool(t and t.waiting > 0)}
        label = i18n.t(HELD_STATUS) if view["controller"] == "user" else (agent["status"] or "")
        health = live.health(key) if hasattr(live, "health") else {}   # page_busy / stale: the browser's live view only
        in_work = session is not None and getattr(session, "work", None) is not None and work_tab(key)
        # the page's own pop-up, which is never in the picture (owner report 2026-11-19): what it said and how Kelo answered
        shown = getattr(session, "page_dialog", None) if getattr(live, "kind", "") == "browser" else None
        dialog = shown(key) if shown is not None else None
        return {"type": "status", **view, "label": label, "agent": agent, "viewers": live.watching(key), **health,
                **({"browser": "work"} if in_work else {}), **({"dialog": dialog} if dialog else {})}

    def live_error(e: LiveError) -> HTTPException:
        return HTTPException(e.status, e.message)

    @router.post("/api/browser/sessions/{key}/live-ticket", dependencies=[Depends(require_auth)])
    async def live_ticket(key: str, request: Request) -> dict[str, Any]:
        return {"ticket": need_source(key).ticket(key, device_of(request)), "expires_in": TICKET_SECONDS}

    async def sign_in_socket(ws: WebSocket, key: str) -> tuple[LiveSource | None, str | None]:
        """Accept a live-view socket and sign it in: the one-time ticket from `live-ticket`, or the bearer header. (source, device); the
        device is None after the refusal has been sent (unauthorized, or too many failed tries) and the socket closed."""
        device: str | None = None
        if auth_blocked(ws):
            await ws.accept()
            await ws.close(4429, "too many failed attempts")
            return None, None
        ticket = ws.query_params.get("ticket", "")
        live = live_sources.get(key)   # the source of this key (None: the server has none)
        if ticket and live is not None:
            device = live.redeem(ticket, key)
        elif ws.headers.get("authorization"):
            try:
                require_auth(ws)  # type: ignore[arg-type]  # a WebSocket has the same headers / client / state
                device = device_of(ws)
            except HTTPException:
                device = None
        await ws.accept()
        if device is None:
            if ticket:
                auth_failed(ws)
            await ws.send_text(json.dumps({"type": "error", "code": "unauthorized", "message": "a fresh ticket (or the bearer header) is needed"}))
            await ws.close(4401, "unauthorized")
        return live, device

    @router.websocket("/api/browser/sessions/{key}/live")
    async def live_socket(ws: WebSocket, key: str) -> None:
        live, device = await sign_in_socket(ws, key)
        if device is None:
            return
        if live is None or not live.available(key):
            await ws.send_text(json.dumps({"type": "ended", "reason": "no_tab"}))
            await ws.close(4404, "no such tab")
            return
        viewer = f"ws:{secrets.token_hex(6)}"
        lock = asyncio.Lock()

        async def send_json(obj: dict[str, Any]) -> None:
            async with lock:
                await ws.send_text(json.dumps(obj, ensure_ascii=False))

        async def send_frame(frame: Frame) -> None:
            async with lock:   # the header and its picture always go out together, in this order
                await ws.send_text(json.dumps(frame.header(), ensure_ascii=False))
                await ws.send_bytes(frame.jpeg)

        q = ws.query_params
        quality = {k: int(q[k]) for k in ("max_width", "quality", "fps") if q.get(k, "").isdigit()}
        live.connected(key, device, +1)
        try:
            cast = await live.join(key, viewer, **quality)
            if getattr(live, "kind", "") == "browser":
                viewed_tabs.add(key)
        except LiveError as e:
            live.connected(key, device, -1)
            await send_json({"type": "ended", "reason": e.code})
            await ws.close(4404 if e.status == 404 else 1011, e.message[:100])
            return
        dirty = asyncio.Event()
        listener = lambda k: dirty.set() if k == key else None  # noqa: E731
        live.listeners.append(listener)
        ending: dict[str, Any] = {"reason": "", "code": 1000}
        last: dict[str, Any] = {}

        async def push_status(force: bool = False) -> None:
            st = live_status(live, key, device)
            if force or st != last.get("v"):
                last["v"] = st
                at = live.pointer(key) if hasattr(live, "pointer") else None
                await send_json({**st, "pointer": at} if at else st)   # a new watcher draws the pointer at once

        gate = FrameGate()   # {"type":"frames","on":false}: this viewer shows the X screen (/vnc) and is sent no pictures

        async def frames() -> None:
            try:
                await pump_frames(live, key, send_frame, fps=lambda: live.rate(key), gate=gate)   # 30 a second while the owner holds the tab
            except LiveEnded:
                ending.update(reason="tab_closed", code=1000)

        async def meta() -> None:   # {"type":"meta","url","title"} while this viewer's frames are off (the page header would go stale)
            if hasattr(live, "meta"):
                await pump_meta(live, key, send_json, gate)
            else:
                await asyncio.Event().wait()

        throttle = PointerThrottle()
        moved = asyncio.Event()

        def on_pointer(k: str, x: float, y: float, down: bool) -> None:
            if k == key:
                throttle.offer(x, y, down, time.monotonic())
                moved.set()

        if hasattr(live, "pointer_listeners"):
            live.pointer_listeners.append(on_pointer)

        async def pointer_loop() -> None:
            while True:
                await moved.wait()
                moved.clear()
                while (due := throttle.wait(time.monotonic())) is not None:
                    if due > 0:
                        await asyncio.sleep(due)
                    for msg in throttle.take(time.monotonic()):
                        await send_json(msg)

        async def status_loop() -> None:
            await push_status(force=True)
            while True:
                try:
                    await asyncio.wait_for(dirty.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                dirty.clear()
                if reason := live.end_reason(key, viewer, cast):
                    ending.update(reason=reason, code=4408 if reason == "no_ping" else 1000)   # no_ping: nothing heard from this phone for a minute
                    return
                await push_status()

        async def receive() -> None:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    ending.update(reason="", code=0)
                    return
                live.touch(key, viewer)
                if live.holder(key) == device:
                    live.seen(key, device)
                try:
                    data = json.loads(msg.get("text") or "")
                except (TypeError, ValueError):
                    await send_json({"type": "error", "code": "bad_message", "message": "send JSON text messages"})
                    continue
                if not isinstance(data, dict):
                    await send_json({"type": "error", "code": "bad_message", "message": "send a JSON object"})
                    continue
                kind = data.get("type")
                try:
                    if kind == "ping":
                        await send_json({"type": "pong", "ts": round(time.time(), 3)})
                    elif kind == "quality":
                        got = await live.configure(key, data.get("max_width"), data.get("quality"), data.get("fps"), data.get("every_nth_frame"))
                        await send_json({"type": "quality", **got})
                    elif kind == "frames":
                        gate.set(data.get("on") is not False)
                    elif kind == "view":   # the size of the owner's live area: the tab follows while they control it (live_v 2)
                        await send_json({"type": "view_ok", **await live.view(key, device, data)})
                    elif kind == "control":
                        if data.get("take"):
                            live.take(key, device, by=str(data.get("by") or "button"))
                        else:
                            await live.give_back(key, device)
                        await push_status(force=True)
                    else:
                        out = await live.input(key, device, data)
                        if data.get("ack"):
                            await send_json({**{k: v for k, v in out.items() if k != "type"}, "type": "input_ok", "input": out["type"]})
                except LiveError as e:
                    await send_json({"type": "error", "code": e.code, "message": e.message, "for": str(kind)[:20]})
                except Exception as e:  # noqa: BLE001 - the browser refused one step; the live view goes on (never echo the event)
                    await send_json({"type": "error", "code": "browser", "message": f"the browser could not do that ({type(e).__name__})", "for": str(kind)[:20]})

        tasks = [asyncio.ensure_future(c) for c in (frames(), status_loop(), receive(), pointer_loop(), meta())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if listener in live.listeners:
                live.listeners.remove(listener)
            if hasattr(live, "pointer_listeners") and on_pointer in live.pointer_listeners:
                live.pointer_listeners.remove(on_pointer)
            live.connected(key, device, -1)
            await live.leave(key, viewer)
            if getattr(live, "kind", "") == "browser" and session is not None and session.has_tab(key):
                viewed_tabs.add(key)                         # the owner looked at this tab: it is kept VIEW_IDLE_SECONDS after this
                session.touch(key)
        if ending["code"]:
            try:
                await send_json({"type": "ended", "reason": ending["reason"]})
                await ws.close(ending["code"], ending["reason"])
            except Exception:  # noqa: BLE001 - the phone already left
                pass

    @router.websocket("/api/browser/sessions/{key}/vnc")
    async def vnc_socket(ws: WebSocket, key: str) -> None:
        """The tab as the browser's real X screen over RFB. First message (text): {"type":"vnc","width","height"} (CSS px the screen was
        sized to) and then only binary RFB bytes both ways, or {"type":"error","code":"busy"|"unavailable"|"unauthorized"} and a close
        (the app shows the picture stream instead). Taking over, text and keys stay on the live socket: pointer, key and clipboard
        messages of a device that does not hold the tab are dropped (tools/vnc.ClientFilter)."""
        live, device = await sign_in_socket(ws, key)
        if device is None:
            return

        async def refuse(code: str, close: int) -> None:
            await ws.send_text(json.dumps({"type": "error", "code": code}))
            await ws.close(close, code)

        q = ws.query_params
        size = viewer_css_size(*(_number(q.get(n)) for n in ("width", "height", "density")))
        if (live is None or live.kind != "browser" or vnc is None or size is None or not live.available(key)):
            await refuse("unavailable", 4503)
            return
        width, height, _ratio = size
        try:
            if not await vnc.acquire(key, device):
                await refuse("busy", 4409)
                return
        except VncUnavailable:
            await refuse("unavailable", 4503)
            return
        up_writer: asyncio.StreamWriter | None = None
        screen: ClientFilter | None = None
        try:
            try:
                up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(*vnc.upstream_of(key)), CONNECT_SECONDS)
            except (OSError, asyncio.TimeoutError):
                await refuse("unavailable", 4503)
                return
            got = vnc.size_of(key) or (width, height)   # the screen keeps the size the first viewer of this tab asked for
            await ws.send_text(json.dumps({"type": "vnc", "width": got[0], "height": got[1]}))
            def allow(active: bool) -> bool:   # asked per message: taking over and giving back count at once; a hand on the screen is a touch
                if live.holder(key) != device:
                    return False
                if active and hasattr(live, "touched"):
                    live.touched(key, device)
                return True

            screen = ClientFilter(allow, on_release=(lambda: live.poke_editing(key)) if hasattr(live, "poke_editing") else None)
            gone = vnc.gone.get(key)

            async def from_viewer() -> None:
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    data = msg.get("bytes")
                    if not data:
                        continue   # the RFB stream is binary only
                    if live.holder(key) == device:
                        live.seen(key, device)
                    out = screen.feed(data)
                    if live.holder(key) != device:   # the hold went to someone else: a press or drag still down is let go
                        out += screen.lift()
                    if out:
                        up_writer.write(out)
                        await up_writer.drain()

            async def from_screen() -> None:
                while data := await up_reader.read(65536):
                    await ws.send_bytes(data)

            async def watch_tab() -> None:
                while live.available(key):
                    try:
                        await asyncio.wait_for((gone or asyncio.Event()).wait(), 1.0)
                        return   # the tab was closed under the viewers
                    except asyncio.TimeoutError:
                        if live.holder(key) != device and (lift := screen.lift()):   # the hold went away while this viewer sent nothing
                            up_writer.write(lift)
                            await up_writer.drain()

            tasks = [asyncio.ensure_future(c) for c in (from_viewer(), from_screen(), watch_tab())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
            bad = any(isinstance(r, VncError) for r in results)
            try:
                await ws.close(1003 if bad else 1000)
            except Exception:  # noqa: BLE001 - the phone already left
                pass
        finally:
            if up_writer is not None:
                if screen is not None and (lift := screen.lift()):   # the viewer leaves mid-press: the X server gets the button and keys up
                    up_writer.write(lift)
                up_writer.close()
            await vnc.release(key, device)

    @router.get("/api/browser/sessions/{key}/frame", dependencies=[Depends(require_auth)])
    async def live_frame(key: str, request: Request, after: int = 0, wait: float = 10.0, max_width: int | None = None,
                         quality: int | None = None, fps: int | None = None) -> Response:
        """The live view without a WebSocket: the newest frame above `after`, waiting up to `wait` s (204 when none came)."""
        live = need_source(key)
        device = device_of(request)
        viewer = f"poll:{device}"
        q = {k: v for k, v in (("max_width", max_width), ("quality", quality), ("fps", fps)) if v is not None}
        try:
            await live.join(key, viewer, **q)
        except LiveError as e:
            raise live_error(e) from None
        if live.holder(key) == device:
            live.seen(key, device)
        try:
            frame = await live.wait_frame(key, after, min(max(wait, 0.0), POLL_WAIT_MAX))
        except LiveEnded:
            raise HTTPException(404, "no such tab") from None
        live.touch(key, viewer)
        view = control_view(live, key, device)
        headers = {"Cache-Control": "no-store", "X-Controller": view["controller"], "X-You-Control": "1" if view["you_control"] else "0"}
        if frame is None:
            return Response(status_code=204, headers=headers)
        from urllib.parse import quote
        headers.update({"X-Frame-Seq": str(frame.seq), "X-Frame-W": str(frame.w), "X-Frame-H": str(frame.h),
                        "X-Frame-Css-W": str(frame.meta.get("deviceWidth") or ""), "X-Frame-Css-H": str(frame.meta.get("deviceHeight") or ""),
                        "X-Frame-Ts": f"{frame.ts:.3f}", "X-Live-V": "3", "X-Frame-Editing": "1" if frame.editing else "0", "X-Frame-Kind": frame.kind, "X-Frame-Field": frame.field_id, "X-Frame-Url": quote(frame.url[:1000], safe=":/?&=#%"),
                        "X-Frame-Title": quote(frame.title[:200], safe=""), "X-Content-Type-Options": "nosniff"})
        return Response(frame.jpeg, media_type="image/jpeg", headers=headers)

    @router.delete("/api/browser/sessions/{key}/frame", dependencies=[Depends(require_auth)])
    async def live_frame_leave(key: str, request: Request) -> dict[str, bool]:
        await need_source(key).leave(key, f"poll:{device_of(request)}")
        return {"ok": True}

    @router.post("/api/browser/sessions/{key}/control", dependencies=[Depends(require_auth)])
    async def browser_control(key: str, body: ControlIn, request: Request) -> dict[str, Any]:
        live, device = need_source(key), device_of(request)
        try:
            if body.take:
                live.take(key, device, by=body.by)
            else:
                await live.give_back(key, device)
        except LiveError as e:
            raise live_error(e) from None
        return control_view(live, key, device)

    @router.post("/api/browser/sessions/{key}/input", dependencies=[Depends(require_auth)])
    async def browser_input(key: str, request: Request) -> dict[str, Any]:
        live = need_source(key)
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(422, "send a JSON object") from None
        if not isinstance(data, dict):
            raise HTTPException(422, "send a JSON object")
        try:
            return await live.input(key, device_of(request), data)
        except LiveError as e:
            raise live_error(e) from None
        except Exception as e:  # noqa: BLE001 - never echo the event (it may be typed text)
            raise HTTPException(502, f"the browser could not do that ({type(e).__name__})") from None
    ctx.add(retire_tab=retire_tab, tab_sweeper=tab_sweeper)
    return router
