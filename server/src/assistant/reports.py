"""Reports of a bad AI reply, sent from the phone app to the Kelo developer (Google Play's generative-AI policy: users can flag
offensive or harmful output from inside the app).

Who holds what:
  - The phone sends POST /api/reports to its own server: a reason, an optional comment, the reported reply and (only if the user
    ticked it) the user's message before it, and the app's version. The server adds the name of the model it is set to use.
  - Each copy forwards the report to the operator's server (the same broker that relays sign-ins, pushes and Kelo addresses):
    POST /broker/reports. No user token travels; the copy proves who it is with its own random report install secret (made on
    first use, kept encrypted in its own database) and the broker keeps only its SHA-256, the same scheme as push.py.
  - The operator's own copy (its public address is the broker's address) stores the report directly, without an HTTP hop.
  - The operator reads them with GET /api/operator/reports (any signed-in device), marks them handled, and can delete one.

Nothing else is sent: no other chat history, no account data, no address of the user's server. The broker does not store the IP
address; it lives only in the in-memory rate counter, like every other /broker/* route. Reports are kept KEEP_DAYS days (the
privacy policy says one year, and deletable on request) and removed after that whenever reports are written or read.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from .i18n import t
from .netguard import NetGuardError, Reply, fetch
from .store import Store

log = logging.getLogger("uvicorn.error")
Fetcher = Callable[..., Awaitable[Reply]]

REASONS = ("harmful", "inaccurate", "privacy", "other")
MAX_REPLY = 8000                  # characters of the reported reply
MAX_ASKED = 4000                  # characters of the user's message before it
MAX_COMMENT = 1000
MAX_BODY = 48_000                 # bytes of one request (the texts above, as UTF-8 JSON, fit with room to spare)
KEEP_DAYS = 365
MAX_STORED = 50_000               # reports the operator keeps at most (older ones go first after KEEP_DAYS anyway)
PER_IP_HOUR = 60
PER_INSTALL_HOUR = 10
PER_INSTALL_DAY = 50
NEW_INSTALLS_PER_IP_DAY = 20
INSTALL_KEY = "reports:install_id"
INSTALL_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.+_ -]{1,40}$")
NAME_RE = re.compile(r"^[0-9A-Za-z._:/@+ -]{1,120}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS operator_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, reason TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '',
    reply TEXT NOT NULL, asked TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT '', install TEXT NOT NULL, handled_at REAL);
CREATE TABLE IF NOT EXISTS report_installs (owner TEXT PRIMARY KEY, created_at REAL NOT NULL);
"""


class ReportError(Exception):
    """Shown to the caller as it is; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def _clean(v: Any, limit: int) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ReportError("texts must be strings")
    v = "".join(ch for ch in v if ch in "\n\t" or ch.isprintable()).strip()
    if len(v) > limit:
        raise ReportError(f"too long (at most {limit} characters)", 413)
    return v


def check_report(body: Any) -> dict[str, str]:
    """The only fields a report may carry, checked the same way on the copy and again on the broker."""
    if not isinstance(body, dict):
        raise ReportError("send {reason, reply, comment?, asked?, app_version?, provider?, model?}")
    reason = body.get("reason")
    if reason not in REASONS:
        raise ReportError(f"reason must be one of {', '.join(REASONS)}")
    reply = _clean(body.get("reply"), MAX_REPLY)
    if not reply:
        raise ReportError("reply must hold the reported text")
    out = {"reason": str(reason), "reply": reply, "comment": _clean(body.get("comment"), MAX_COMMENT),
           "asked": _clean(body.get("asked"), MAX_ASKED)}
    for key, pattern in (("app_version", VERSION_RE), ("provider", NAME_RE), ("model", NAME_RE)):
        v = _clean(body.get(key), 120)
        out[key] = v if v and pattern.match(v) else ""
    return out


def _schema(store: Store) -> None:
    with store._lock:
        store._db.executescript(SCHEMA)
        store._db.commit()


# ---------------------------------------------------------------------------------------------------------------- operator side
class ReportBox:
    """The operator's stored reports (SQLite, in the server's own database)."""

    def __init__(self, store: Store, *, wall: Callable[[], float] = time.time):
        _schema(store)
        self.store, self.wall = store, wall
        self.on_add: Callable[[dict[str, str]], Any] | None = None   # a new report: the operator is told (server.py: a notification)

    def purge(self) -> int:
        return self.store._run("DELETE FROM operator_reports WHERE created_at < ?", (self.wall() - KEEP_DAYS * 86400,)).rowcount

    def add(self, report: dict[str, str], install: str) -> int:
        self.purge()
        if int(self.store._all("SELECT COUNT(*) AS n FROM operator_reports")[0]["n"]) >= MAX_STORED:
            raise ReportError("the report box is full; try again later", 503)
        cur = self.store._run(
            "INSERT INTO operator_reports(created_at, reason, comment, reply, asked, provider, model, app_version, install) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (self.wall(), report["reason"], report["comment"], report["reply"], report["asked"], report["provider"], report["model"],
             report["app_version"], install))
        if self.on_add is not None:
            try:
                self.on_add(report)
            except Exception:  # noqa: BLE001 - a report is never lost over its notice
                log.exception("reports: telling the operator failed")
        return int(cur.lastrowid or 0)

    def list(self, *, limit: int = 100, handled: bool | None = None) -> dict[str, Any]:
        self.purge()
        where = "" if handled is None else ("WHERE handled_at IS NOT NULL" if handled else "WHERE handled_at IS NULL")
        rows = self.store._all(f"SELECT * FROM operator_reports {where} ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))
        open_n = int(self.store._all("SELECT COUNT(*) AS n FROM operator_reports WHERE handled_at IS NULL")[0]["n"])
        return {"reports": [self._view(r) for r in rows], "open": open_n, "keep_days": KEEP_DAYS}

    @staticmethod
    def _view(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["id"], "created_at": r["created_at"], "reason": r["reason"], "comment": r["comment"], "reply": r["reply"],
                "asked": r["asked"], "provider": r["provider"], "model": r["model"], "app_version": r["app_version"],
                "install": r["install"][:12], "handled": r["handled_at"] is not None, "handled_at": r["handled_at"]}

    def mark(self, report_id: int, handled: bool) -> dict[str, Any] | None:
        self.store._run("UPDATE operator_reports SET handled_at = ? WHERE id = ?", (self.wall() if handled else None, report_id))
        rows = self.store._all("SELECT * FROM operator_reports WHERE id = ?", (report_id,))
        return self._view(rows[0]) if rows else None

    def delete(self, report_id: int) -> bool:
        return self.store._run("DELETE FROM operator_reports WHERE id = ?", (report_id,)).rowcount > 0


class ReportBroker:
    """POST /broker/reports on the operator's server: who may send, how often, and how much."""

    def __init__(self, box: ReportBox, *, clock: Callable[[], float] = time.monotonic):
        self.box, self.clock = box, clock
        self.hits: dict[str, deque[float]] = {}

    def _gate(self, key: str, limit: int, window: float, what: str) -> None:
        now = self.clock()
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise ReportError(f"too many {what}; try again later", 429, int(window - (now - q[0])) + 1)
        q.append(now)
        if len(self.hits) > 20000:
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 86400]:
                self.hits.pop(k, None)

    def owner(self, install_id: Any, ip: str) -> str:
        if not isinstance(install_id, str) or not INSTALL_RE.match(install_id):
            raise ReportError("install_id must be the copy's own random secret (32-128 letters, digits, '-' or '_')", 403)
        owner = hashlib.sha256(install_id.encode()).hexdigest()
        store = self.box.store
        if not store._all("SELECT 1 FROM report_installs WHERE owner = ?", (owner,)):
            self._gate(f"new:{ip}", NEW_INSTALLS_PER_IP_DAY, 86400, "new copies from one address")
            store._run("INSERT OR IGNORE INTO report_installs(owner, created_at) VALUES(?, ?)", (owner, self.box.wall()))
        return owner

    def handle(self, body: Any, ip: str) -> dict[str, Any]:
        self._gate(f"ip:{ip}", PER_IP_HOUR, 3600, "reports from one address")
        if not isinstance(body, dict):
            raise ReportError("send {install_id, report}")
        report = check_report(body.get("report"))
        owner = self.owner(body.get("install_id"), ip)
        self._gate(f"install:{owner}", PER_INSTALL_HOUR, 3600, "reports from this copy")
        self._gate(f"day:{owner}", PER_INSTALL_DAY, 86400, "reports from this copy today")
        rid = self.box.add(report, owner)
        log.info("reports: one report received (%s)", report["reason"])
        return {"ok": True, "id": rid}


# ---------------------------------------------------------------------------------------------------------------- each copy
class ReportClient:
    """This copy's way to the developer: straight into its own box when it is the operator, else through the broker."""

    def __init__(self, store: Store, broker: ReportBroker, *, broker_url: str, public_url: str, fetcher: Fetcher = fetch):
        self.store, self.broker, self.fetcher = store, broker, fetcher
        self.url = broker_url.strip().rstrip("/")
        self.public = public_url.strip().rstrip("/")

    def is_operator(self) -> bool:
        """This server is the one other copies send their reports to (its public address is the broker's)."""
        host = (urlsplit(self.url).hostname or "").lower()
        return bool(host) and host == (urlsplit(self.public).hostname or "").lower()

    def install_id(self) -> str:
        have = self.store.key(INSTALL_KEY)
        if have and INSTALL_RE.match(have):
            return have
        made = secrets.token_urlsafe(32)
        self.store.set_key(INSTALL_KEY, made)
        return made

    async def send(self, report: dict[str, str]) -> dict[str, Any]:
        report = check_report(report)
        if self.is_operator():
            owner = hashlib.sha256(self.install_id().encode()).hexdigest()
            return {"ok": True, "id": self.broker.box.add(report, owner), "to": "here"}
        if not self.url:
            raise ReportError(t("This server has no address for the Kelo developer, so the report could not be sent."), 503)
        body = json.dumps({"install_id": self.install_id(), "report": report}).encode()
        try:
            r = await self.fetcher("POST", f"{self.url}/broker/reports", allowed_hosts=[urlsplit(self.url).hostname or ""], content=body,
                                   headers={"Content-Type": "application/json", "Accept": "application/json"})
        except NetGuardError:
            raise ReportError(t("Could not reach the Kelo developer's server. Your report was kept; try sending it again later."), 502) from None
        if r.status == 200:
            log.info("reports: one report sent to the developer (%s)", report["reason"])
            return {"ok": True, "to": "developer"}
        if r.status == 429:
            wait = None
            v = {k.lower(): v for k, v in (r.headers or {}).items()}.get("retry-after", "")
            if v.isdigit():
                wait = int(v)
            raise ReportError(t("Too many reports were sent from here for now. Try again later."), 429, wait)
        if r.status in (413, 422):
            raise ReportError(t("The report was too long or not complete. Shorten the comment and try again."), 422)
        log.warning("reports: the developer's server answered HTTP %d", r.status)
        raise ReportError(t("Could not reach the Kelo developer's server. Your report was kept; try sending it again later."), 502)
