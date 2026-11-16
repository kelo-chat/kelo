"""Updating the Kelo server from the app: the server's side of the updater container (updater_service.py).

The server has no Docker socket and never controls a container. Owner-only routes pass four fixed commands on to the updater over the
internal network `updater-net` (ASSISTANT_UPDATER_URL, http://updater:9400 in compose.yml) with the shared UPDATER_TOKEN; only the
main copy's server has both, so another copy's owner (and any non-owner device) sees nothing.

GET  /api/server-update                -> status (below)
POST /api/server-update/check   {}     -> status, after reading the release list again
POST /api/server-update/update  {}     -> 202 status (job.state "running"); 409 {"detail"} no update / already running / local build
POST /api/server-update/rollback {}    -> 202 status (job.action "rollback"); 409 {"detail"} no rollback point
PUT  /api/server-update/settings {"auto_update": bool} -> status
Any signed-in device may use them (not on a review copy: 403). Status:
  {"state": "ok" | "local" | "unavailable", "message": "<localized, for local/unavailable>", "current": "0.4.0" | null,
   "latest": {"version", "date", "notes" (in the owner's language)} | null, "update_available": bool, "checked_at": epoch | null,
   "auto_update": bool, "auto_time": "HH:MM" | null (tonight's planned time while auto_update is on),
   "job": null | {"id", "action": update|rollback, "auto": bool, "state": running|done|failed|rolled_back|rollback_failed,
                  "step": preflight|download|verify|backup|restart|health|rollback|self_update|done, "progress": 0..100, "from", "to",
                  "error": "<English detail>" | null, "data_restored": bool, "warning"?: str, "started_at", "finished_at"},
   "rollback": {"available": bool, "version": str | null, "created_at": epoch | null}}
The server restarts during an update, so the apps keep asking through a few minutes of errors; the new server reads the job's end
from the updater (it keeps the job in its own state file).

AUTO-UPDATE (owner setting, off by default; KELO_AUTO_UPDATE=1 from the installer turns it on until the owner changes it in the app):
checked every 5 minutes. It starts an update only at night (22:00..08:00 in the profile's time zone), at or after AUTO_DELAY past
the quietest time learned from the owner's own use (quiet_hours.py, 03:50 until a week of use is known), after the nightly jobs ran,
outside the contact hours (proactive wake_start..wake_end), with no chat running and the app unused for IDLE_MINUTES, at most once
a night. The result comes as a notification (type "server_update"), as does every finished job and, with auto-update off, a new
version the first time it is seen. The release list is read every CHECK_HOURS.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from . import i18n
from .i18n import t
from .release import newer

CHECK_HOURS = 6
AUTO_DELAY = 45          # minutes after the quiet slot starts (the nightly jobs run first)
IDLE_MINUTES = 20
HOLD_RUNNING = 300       # seconds the last "running" answer stands in while the updater swaps itself
NIGHT_END_HOUR = 8
FINISHED = ("done", "failed", "rolled_back", "rollback_failed")
JOB_FIELDS = ("id", "action", "state", "step", "progress", "from", "to", "error", "data_restored", "warning", "started_at", "finished_at")
META_AUTO = "server_update:auto"
META_NOTED = "server_update:noted_job"
META_SEEN = "server_update:seen_version"
META_NIGHT = "server_update:auto_night"
META_AUTO_JOB = "server_update:auto_job"


class UpdaterDown(Exception):
    pass


class UpdaterClient:
    def __init__(self, url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0):
        self.url, self.token, self.transport, self.timeout = url.rstrip("/"), token, transport, timeout

    @property
    def configured(self) -> bool:
        return bool(self.url and len(self.token) >= 16)

    async def call(self, method: str, path: str) -> tuple[int, dict[str, Any]]:
        if not self.configured:
            raise UpdaterDown("not configured")
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as c:
                r = await c.request(method, self.url + path, headers={"Authorization": f"Bearer {self.token}"},
                                    **({"json": {}} if method == "POST" else {}))
        except httpx.HTTPError as e:
            raise UpdaterDown(type(e).__name__) from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 401:
            raise UpdaterDown("the updater refused the token")
        return r.status_code, body if isinstance(body, dict) else {}


class AutoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_update: bool


class ServerUpdates:
    def __init__(self, store: Any, client: UpdaterClient, *, version: Callable[[], str], notify: Callable[..., Any],
                 tz: Callable[[], str | None], quiet_slot: Callable[[float], tuple[int, int, str]] | None = None,
                 awake: Callable[[float], bool] = lambda ts: False, last_use: Callable[[], float] = lambda: 0.0,
                 busy: Callable[[], bool] = lambda: False, clock: Callable[[], float] = time.time, default_auto: bool = False):
        self.store, self.client, self.version, self.notify, self.tz = store, client, version, notify, tz
        self.quiet_slot = quiet_slot or (lambda now: (3, 50, "default"))
        self.awake, self.last_use, self.busy, self.clock, self.default_auto = awake, last_use, busy, clock, default_auto
        self._last: tuple[float, dict[str, Any] | None] = (0.0, None)

    # ------------------------------------------------------------------ settings
    def auto(self) -> bool:
        v = self.store.meta(META_AUTO)
        return self.default_auto if v in (None, "") else v == "1"

    def set_auto(self, on: bool) -> None:
        self.store.set_meta(META_AUTO, "1" if on else "0")

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.tz() or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def plan(self, now: float | None = None) -> dict[str, Any] | None:
        """Tonight's auto-update window: {"key" (the evening's date), "start", "end"} as local datetimes."""
        now = self.clock() if now is None else now
        local = datetime.fromtimestamp(now, self._zone())
        evening = local if local.hour >= 12 else local - timedelta(days=1)
        key = evening.strftime("%Y-%m-%d")
        h, m, _ = self.quiet_slot(now)
        base = evening.replace(hour=0, minute=0, second=0, microsecond=0)
        start = base + timedelta(days=0 if h >= 12 else 1, hours=h, minutes=m + AUTO_DELAY)
        end = base + timedelta(days=1, hours=NIGHT_END_HOUR)
        if start >= end:
            start = end - timedelta(minutes=30)
        return {"key": key, "start": start, "end": end}

    # ------------------------------------------------------------------ talking to the updater
    async def raw(self) -> dict[str, Any] | None:
        """The updater's status. While a job runs the updater replaces itself at the end (a few seconds without an answer): then
        the last answer is used for up to HOLD_RUNNING seconds, so the apps keep showing the job instead of "unavailable"."""
        try:
            code, body = await self.client.call("GET", "/status")
        except UpdaterDown:
            code, body = 0, {}
        if code == 200:
            self._last = (self.clock(), body)
            return body
        when, last = self._last
        job = (last or {}).get("job") or {}
        if last and job.get("state") == "running" and self.clock() - when < HOLD_RUNNING:
            return last
        return None

    def public(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        auto = self.auto()
        base: dict[str, Any] = {"state": "unavailable", "message": "", "current": self.version() or None, "latest": None,
                                "update_available": False, "checked_at": None, "auto_update": auto, "auto_time": None, "job": None,
                                "rollback": {"available": False, "version": None, "created_at": None}}
        if raw is None:
            base["message"] = t("Server updates from the app are not set up on this server")
            return base
        lang = i18n.current()
        latest = raw.get("latest") if isinstance(raw.get("latest"), dict) else None
        if latest:
            notes = latest.get("notes") if isinstance(latest.get("notes"), dict) else {}
            code = (lang or "en").replace("_", "-")
            note = notes.get(code) or notes.get(code.split("-")[0]) or notes.get("en") or next(iter(notes.values()), "")
            latest = {"version": latest.get("version"), "date": latest.get("date") or None, "notes": note}
        local = raw.get("mode") != "registry"
        job = raw.get("job") if isinstance(raw.get("job"), dict) else None
        if job:
            job = {k: job.get(k) for k in JOB_FIELDS if k in job} | {"auto": job.get("id") == self.store.meta(META_AUTO_JOB)}
        plan = self.plan() if auto and not local else None
        base.update(state="local" if local else "ok", current=raw.get("current") or self.version() or None, latest=latest,
                    update_available=bool(raw.get("update_available")) and not local, checked_at=raw.get("checked_at"), job=job,
                    auto_time=plan["start"].strftime("%H:%M") if plan else None,
                    rollback={**base["rollback"], **{k: v for k, v in (raw.get("rollback") or {}).items() if k in base["rollback"]}})
        if local:
            base["message"] = (t("This server uses Google Chrome, which is built on the server machine: update it there with kelo update")
                               if raw.get("reason") == "chrome" else
                               t("Kelo on this server was built from the source files: update it on the server machine with kelo update"))
        return base

    async def status(self) -> dict[str, Any]:
        return self.public(await self.raw())

    async def check(self) -> dict[str, Any]:
        try:
            code, body = await self.client.call("POST", "/check")
        except UpdaterDown:
            return self.public(None)
        return self.public(body if code == 200 else await self.raw())

    async def start(self, action: str, *, auto: bool = False) -> tuple[int, dict[str, Any]]:
        try:
            code, body = await self.client.call("POST", f"/{action}")
        except UpdaterDown:
            return 409, {"detail": t("Server updates from the app are not set up on this server")}
        if code != 202:
            return 409, {"detail": self._refusal(body)}
        job = body.get("job") or {}
        if auto and job.get("id"):
            self.store.set_meta(META_AUTO_JOB, str(job["id"]))
        return 202, self.public(body)

    @staticmethod
    def _refusal(body: dict[str, Any]) -> str:
        return {
            "busy": t("An update is already running"),
            "local": t("Kelo on this server was built from the source files: update it on the server machine with kelo update"),
            "up_to_date": t("The server is already up to date"),
            "no_release": t("The list of releases could not be read; try again later"),
            "no_rollback": t("There is nothing to go back to yet"),
        }.get(str(body.get("code")), str(body.get("detail") or t("The updater refused this")))

    # ------------------------------------------------------------------ every 5 minutes
    async def tick(self, now: float | None = None) -> list[str]:
        """Notifications for finished jobs and new versions, the periodic look at the release list, and the nightly auto-update."""
        now = self.clock() if now is None else now
        raw = await self.raw()
        if raw is None:
            return []
        done: list[str] = []
        job = raw.get("job") if isinstance(raw.get("job"), dict) else None
        if job and job.get("state") in FINISHED and job.get("id") and job["id"] != self.store.meta(META_NOTED):
            self.store.set_meta(META_NOTED, str(job["id"]))
            if float(job.get("finished_at") or now) > now - 7 * 86400:
                self._note_job(job)
                done.append("job")
        if raw.get("mode") != "registry":
            return done
        if now - float(raw.get("checked_at") or 0) >= CHECK_HOURS * 3600:
            try:
                code, body = await self.client.call("POST", "/check")
                raw = body if code == 200 else raw
            except UpdaterDown:
                return done
        latest = (raw.get("latest") or {}).get("version")
        if not (latest and newer(latest, raw.get("current"))):
            return done
        running = bool(job and job.get("state") == "running")
        if not self.auto():
            if self.store.meta(META_SEEN) != latest:
                self.store.set_meta(META_SEEN, latest)
                self.notify("server_update", t("Server update available: {version}", version=latest),
                            t("Open My, then Server update, to see what is new and update the server."))
                done.append("available")
            return done
        if running or not self.due(now):
            return done
        self.store.set_meta(META_NIGHT, self.plan(now)["key"])
        code, _ = await self.start("update", auto=True)
        if code == 202:
            done.append("auto")
        return done

    def due(self, now: float) -> bool:
        plan = self.plan(now)
        local = datetime.fromtimestamp(now, self._zone())
        if plan is None or not (plan["start"] <= local < plan["end"]) or self.store.meta(META_NIGHT) == plan["key"]:
            return False
        if self.awake(now) or self.busy() or now - self.last_use() < IDLE_MINUTES * 60:
            return False
        nightly = self.store.meta("nightly:last")          # the nightly jobs ran (or have had two hours to)
        return nightly == plan["key"] or local >= plan["start"] + timedelta(hours=2)

    def _note_job(self, job: dict[str, Any]) -> None:
        state, to, frm = job.get("state"), job.get("to") or "?", job.get("from") or "?"
        if job.get("action") == "rollback":
            title, body = ((t("Server went back"), t("The server runs version {version} again.", version=to)) if state == "done" else
                           (t("Going back did not finish"), t("Check the server machine: kelo backups lists the backups.")))
        elif state == "done":
            title, body = t("Server updated"), t("The server now runs version {version}.", version=to)
        elif state == "rolled_back":
            title = t("Server update did not work")
            body = t("Version {version} did not start properly, so the server went back to {previous} by itself.", version=to, previous=frm)
        elif state == "rollback_failed":
            title, body = t("Server update failed"), t("Version {version} did not start and going back did not finish. Check the server machine: kelo backups lists the backups.", version=to)
        else:
            title, body = t("Server update failed"), t("The update to {version} stopped before anything changed.", version=to)
        self.notify("server_update", title, body)


def routes(app: FastAPI, su: ServerUpdates, owner: Callable[..., Any]) -> None:
    """The SERVER UPDATE API (contract in this module's docstring)."""

    @app.get("/api/server-update", dependencies=[Depends(owner)])
    async def server_update() -> dict[str, Any]:
        return await su.status()

    @app.post("/api/server-update/check", dependencies=[Depends(owner)])
    async def server_update_check() -> dict[str, Any]:
        return await su.check()

    async def start(action: str) -> Any:
        code, body = await su.start(action)
        if code != 202:
            raise HTTPException(code, body.get("detail"))
        return JSONResponse(body, status_code=202)

    @app.post("/api/server-update/update", dependencies=[Depends(owner)])
    async def server_update_run() -> Any:
        return await start("update")

    @app.post("/api/server-update/rollback", dependencies=[Depends(owner)])
    async def server_update_rollback() -> Any:
        return await start("rollback")

    @app.put("/api/server-update/settings", dependencies=[Depends(owner)])
    async def server_update_settings(body: AutoIn) -> dict[str, Any]:
        su.set_auto(body.auto_update)
        return await su.status()


async def run_tick(su: ServerUpdates) -> None:
    try:
        await su.tick()
    except Exception:  # noqa: BLE001 - the next tick tries again
        logging.getLogger("uvicorn.error").warning("server update check failed", exc_info=True)

