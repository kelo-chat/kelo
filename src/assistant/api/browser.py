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
    CHAT_TAB_IDLE_SECONDS, SITE_TAB_IDLE_SECONDS, TAB_SWEEP_SECONDS, is_site_tab, one_run_tab,
)
from ..tools.live import Frame, LiveEnded, LiveError, LiveHub, TICKET_SECONDS, pump_frames
from ..browser_history import history_routes
from ..server import BACKGROUND_NOTE, ENDED_TABS_KEPT, HELD_STATUS, POLL_WAIT_MAX
from fastapi import APIRouter
from .context import AppContext

log = logging.getLogger("uvicorn.error")


class BrowserTaskIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    url: str | None = Field(default=None, max_length=2000)
    secrets: str | None = Field(default=None, pattern="^(allow|mask)$")


class ControlIn(BaseModel):
    take: bool


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
    tickets = ctx.tickets
    turn_line = ctx.turn_line
    turns = ctx.turns
    workers = ctx.workers

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
        if session is None:
            return {"available": False, "items": []}
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
            items.append({"id": key, "label": tab_label(key), "kind": ("owner" if key == "owner" else "device" if key.isdigit() else key.split(":", 1)[0]),
                          "title": tab["title"], "url": tab["url"], "host": tab["host"],
                          "status": "working" if t else ("failed" if tab_failed.get(key) else "idle"),
                          "status_text": i18n.t(HELD_STATUS) if holder else tab["status"],
                          "controller": "user" if holder else "agent", "controlled_by": holder, "viewers": hub.watching(key) if hub else 0,
                          "last_activity": tab["last_activity"], "task_id": t.task_id if t else owner_tasks.get(key),
                          "thumbnail": image, "thumbnail_width": w, "thumbnail_height": h, "ended_at": None})
        open_now = {i["id"] for i in items}
        items += [{k: v for k, v in e.items() if k != "shot"} | ({} if thumbnails else {"thumbnail": None})
                  for key, e in ended_tabs.items() if key not in open_now]
        items.sort(key=lambda x: ({"working": 0, "ended": 2}.get(x["status"], 1), -(x["ended_at"] or x["last_activity"] or 0)))
        return {"available": True, "items": items}

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
        body.prompt = secret_check(body.prompt, body.secrets)   # a pasted key or password stops here (409), as in chat
        key = f"task:{secrets.token_hex(4)}"
        prompt = body.prompt.strip() + (f"\nStart at: {body.url.strip()}" if body.url and body.url.strip() else "")
        title = title_from(body.prompt)
        turn = start_background(prompt, title=title, source="background", owner=key, note=BACKGROUND_NOTE, saved_as=i18n.t("[Task handed over · {title}]", title=title))
        return {"id": key, "task_id": turn.task_id, "turn_id": turn.id}

    @router.delete("/api/browser/sessions/{key}", dependencies=[Depends(require_auth)])
    async def close_browser_session(key: str) -> dict[str, bool]:
        if session is not None and not session.has_tab(key) and ended_tabs.pop(key, None) is not None:
            return {"ok": True}                              # an ended entry: just forget it
        if session is None or not session.has_tab(key):
            raise HTTPException(404, "no such tab")
        if any(t.owner == key for t in running_turns()):
            raise HTTPException(409, "the agent is working in this tab; stop that task first")
        thumbs.pop(key, None)
        tab_failed.pop(key, None)
        ended_tabs.pop(key, None)
        return {"ok": await session.close_tab(key)}

    # ---- closing tabs nobody needs any more (see "Tab lifecycle" next to the BrowserTab contract) --------------------------------
    ended_tabs = ctx.ended_tabs   # schedule/task tab -> its finished entry (BrowserTab + "shot": the large JPEG), oldest first

    def tab_busy(key: str) -> str:
        """Why this tab must stay open right now ("" = it may close)."""
        if hub is not None and hub.watching(key):
            return "viewer"
        if held_by(key):
            return "control"
        if any((t := turns.get(p.get("turn_id", ""))) is not None and t.owner == key for p in pending_approvals.values()):
            return "approval"
        if any(t.owner == key for t in running_turns()):
            return "running"
        if workers.holds_tab(key):
            return "worker"     # a background job runs, waits for the user, or asked to keep it
        return ""

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
                 "host": info["host"], "status": "ended", "status_text": info["status"], "controller": "agent", "controlled_by": None,
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

    async def sweep_tabs() -> list[str]:
        """One pass: finished runs' tabs are retired, chat tabs unused for an hour are closed. Returns the tabs closed."""
        closed: list[str] = []
        if session is None:
            return closed
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

    def need_hub() -> LiveHub:
        if hub is None:
            raise HTTPException(404, "this server has no browser")
        return hub

    def control_view(key: str, device: str) -> dict[str, Any]:
        holder = held_by(key)
        return {"id": key, "controller": "user" if holder else "agent", "controlled_by": holder, "you_control": holder == device}

    def live_status(key: str, device: str) -> dict[str, Any]:
        t = next((t for t in running_turns() if t.owner == key), None)
        view = control_view(key, device)
        agent = {"busy": t is not None, "status": turn_line(t) if t else None, "task_id": t.task_id if t else None,
                 "title": t.title if t else None, "waiting_approval": bool(t and t.waiting > 0)}
        label = i18n.t(HELD_STATUS) if view["controller"] == "user" else (agent["status"] or "")
        return {"type": "status", **view, "label": label, "agent": agent, "viewers": hub.watching(key) if hub else 0}

    def live_error(e: LiveError) -> HTTPException:
        return HTTPException(e.status, e.message)

    @router.post("/api/browser/sessions/{key}/live-ticket", dependencies=[Depends(require_auth)])
    async def live_ticket(key: str, request: Request) -> dict[str, Any]:
        need_hub()
        return {"ticket": tickets.issue(key, device_of(request)), "expires_in": TICKET_SECONDS}

    @router.websocket("/api/browser/sessions/{key}/live")
    async def live_socket(ws: WebSocket, key: str) -> None:
        device: str | None = None
        if auth_blocked(ws):
            await ws.accept()
            await ws.close(4429, "too many failed attempts")
            return
        ticket = ws.query_params.get("ticket", "")
        if ticket:
            device = tickets.redeem(ticket, key)
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
            return
        if hub is None or session is None or session.raw_page(key) is None:
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
        hub.connected(key, device, +1)
        try:
            cast = await hub.join(key, viewer, **quality)
        except LiveError as e:
            hub.connected(key, device, -1)
            await send_json({"type": "ended", "reason": e.code})
            await ws.close(4404 if e.status == 404 else 1011, e.message[:100])
            return
        dirty = asyncio.Event()
        listener = lambda k: dirty.set() if k == key else None  # noqa: E731
        hub.listeners.append(listener)
        ending: dict[str, Any] = {"reason": "", "code": 1000}
        last: dict[str, Any] = {}

        async def push_status(force: bool = False) -> None:
            st = live_status(key, device)
            if force or st != last.get("v"):
                last["v"] = st
                await send_json(st)

        async def frames() -> None:
            try:
                await pump_frames(hub, key, send_frame, fps=lambda: cast.settings["fps"])
            except LiveEnded:
                ending.update(reason="tab_closed", code=1000)

        async def status_loop() -> None:
            await push_status(force=True)
            while True:
                try:
                    await asyncio.wait_for(dirty.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                dirty.clear()
                if hub.casts.get(key) is not cast or cast.ended:
                    ending.update(reason="tab_closed", code=1000)
                    return
                if viewer not in cast.viewers:   # nothing heard from this phone for a minute
                    ending.update(reason="no_ping", code=4408)
                    return
                await push_status()

        async def receive() -> None:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    ending.update(reason="", code=0)
                    return
                hub.touch(key, viewer)
                if session.controlled_by(key) == device:
                    hub.seen(key, device)
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
                        got = await hub.configure(key, data.get("max_width"), data.get("quality"), data.get("fps"), data.get("every_nth_frame"))
                        await send_json({"type": "quality", **got})
                    elif kind == "control":
                        if data.get("take"):
                            hub.take(key, device)
                        else:
                            await hub.give_back(key, device)
                        await push_status(force=True)
                    else:
                        out = await hub.input(key, device, data)
                        if data.get("ack"):
                            await send_json({**{k: v for k, v in out.items() if k != "type"}, "type": "input_ok", "input": out["type"]})
                except LiveError as e:
                    await send_json({"type": "error", "code": e.code, "message": e.message, "for": str(kind)[:20]})
                except Exception as e:  # noqa: BLE001 - the browser refused one step; the live view goes on (never echo the event)
                    await send_json({"type": "error", "code": "browser", "message": f"the browser could not do that ({type(e).__name__})", "for": str(kind)[:20]})

        tasks = [asyncio.ensure_future(c) for c in (frames(), status_loop(), receive())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if listener in hub.listeners:
                hub.listeners.remove(listener)
            hub.connected(key, device, -1)
            await hub.leave(key, viewer)
        if ending["code"]:
            try:
                await send_json({"type": "ended", "reason": ending["reason"]})
                await ws.close(ending["code"], ending["reason"])
            except Exception:  # noqa: BLE001 - the phone already left
                pass

    @router.get("/api/browser/sessions/{key}/frame", dependencies=[Depends(require_auth)])
    async def live_frame(key: str, request: Request, after: int = 0, wait: float = 10.0, max_width: int | None = None,
                         quality: int | None = None, fps: int | None = None) -> Response:
        """The live view without a WebSocket: the newest frame above `after`, waiting up to `wait` s (204 when none came)."""
        live = need_hub()
        device = device_of(request)
        viewer = f"poll:{device}"
        q = {k: v for k, v in (("max_width", max_width), ("quality", quality), ("fps", fps)) if v is not None}
        try:
            await live.join(key, viewer, **q)
        except LiveError as e:
            raise live_error(e) from None
        if held_by(key) == device:
            live.seen(key, device)
        try:
            frame = await live.wait_frame(key, after, min(max(wait, 0.0), POLL_WAIT_MAX))
        except LiveEnded:
            raise HTTPException(404, "no such tab") from None
        live.touch(key, viewer)
        view = control_view(key, device)
        headers = {"Cache-Control": "no-store", "X-Controller": view["controller"], "X-You-Control": "1" if view["you_control"] else "0"}
        if frame is None:
            return Response(status_code=204, headers=headers)
        from urllib.parse import quote
        headers.update({"X-Frame-Seq": str(frame.seq), "X-Frame-W": str(frame.w), "X-Frame-H": str(frame.h),
                        "X-Frame-Css-W": str(frame.meta.get("deviceWidth") or ""), "X-Frame-Css-H": str(frame.meta.get("deviceHeight") or ""),
                        "X-Frame-Ts": f"{frame.ts:.3f}", "X-Frame-Url": quote(frame.url[:1000], safe=":/?&=#%"),
                        "X-Frame-Title": quote(frame.title[:200], safe=""), "X-Content-Type-Options": "nosniff"})
        return Response(frame.jpeg, media_type="image/jpeg", headers=headers)

    @router.delete("/api/browser/sessions/{key}/frame", dependencies=[Depends(require_auth)])
    async def live_frame_leave(key: str, request: Request) -> dict[str, bool]:
        await need_hub().leave(key, f"poll:{device_of(request)}")
        return {"ok": True}

    @router.post("/api/browser/sessions/{key}/control", dependencies=[Depends(require_auth)])
    async def browser_control(key: str, body: ControlIn, request: Request) -> dict[str, Any]:
        live, device = need_hub(), device_of(request)
        try:
            if body.take:
                live.take(key, device)
            else:
                await live.give_back(key, device)
        except LiveError as e:
            raise live_error(e) from None
        return control_view(key, device)

    @router.post("/api/browser/sessions/{key}/input", dependencies=[Depends(require_auth)])
    async def browser_input(key: str, request: Request) -> dict[str, Any]:
        live = need_hub()
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
