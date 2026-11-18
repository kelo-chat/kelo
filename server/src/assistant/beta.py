"""Asking for early access to Google sign-in (Gmail, Google Calendar) while the operator's Google app is in Google's Testing mode.

In Testing mode only the test users the operator adds by hand in Google Cloud Console (at most 100) can sign in, so new Google sign-ins
stay closed for everyone (google_web.py). This lets a person ask to be one of them, and lets the operator let that one copy in:

  - The phone sends POST /api/google/beta to its own server: the Google address it will connect and which connectors. The copy keeps
    the request (and its state) in its own database and forwards {email, products, app_version, language} to the operator's server:
    POST /broker/beta, proving who it is with the same random install secret as reports (reports.py); the broker keeps only its
    SHA-256. The operator's own copy stores the request directly.
  - The operator's server stores it (BetaDesk), tells the operator at once (a notification on their own app, and an email to SUPPORT
    sent through the Gmail mailbox connected on that server, if one is), and lists it in 운영자 → 베타 신청 (any signed-in device).
  - The operator adds the address as a test user in Google Cloud Console, then presses Approved: the requester gets one email (in their
    language, Reply-To SUPPORT) and their install is allowed to start Google sign-ins (beta_installs). Decline: an optional short email.
  - The copy learns the outcome when its connector screens are opened: GET /api/google/beta (and /api/connectors, /api/google/calendar,
    /api/mail/providers) asks POST /broker/beta/status with its install secret, at most every CACHE_SECONDS. Approved: the Google sign-in
    button shows; the broker's /broker/google/exchange lets that install's sign-ins through (google_web.py, server.py).

What is kept: the request row (address, products, install hash, language, app version, times, status) until it is handled, then
KEEP_DAYS days; an unhandled one at most OPEN_KEEP_DAYS days. An approved install's hash stays in beta_installs (no address) so its
weekly re-sign-in keeps working. No IP address is stored (it lives only in the in-memory rate counters). Mail goes out only for a new
request (to SUPPORT) and when the operator presses Approved or Decline (to the requester); nothing else ever sends it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from . import i18n
from .i18n import t
from .netguard import NetGuardError, Reply, fetch
from .reports import INSTALL_RE, ReportBroker, ReportError
from .store import Store

log = logging.getLogger("uvicorn.error")
Fetcher = Callable[..., Awaitable[Reply]]
Mailer = Callable[[str, str, str, str], Awaitable[str]]   # (to, subject, body, reply_to) -> what the mailbox said

PRODUCTS = {"gmail": "Gmail", "calendar": "Google Calendar"}
STATUSES = ("requested", "approved", "declined")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,24}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.+_ -]{1,40}$")
SUPPORT = "support@kelo.chat"
CONSOLE = "https://console.cloud.google.com/auth/audience"
KEEP_DAYS = 30                    # a handled request (approved or declined) is deleted this long after it was handled
OPEN_KEEP_DAYS = 180              # an unhandled one at most this long
MAX_OPEN = 1000                   # unhandled requests the operator keeps at most
PER_IP_HOUR = 20
PER_INSTALL_HOUR = 5
PER_INSTALL_DAY = 10
STATUS_PER_IP_HOUR = 240          # status checks (a copy asks at most every CACHE_SECONDS while its connector screens are open)
CACHE_SECONDS = 180.0
COPY_META = "google_beta_request"  # this copy's own request: {"email", "products", "status", "at", "checked"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS beta_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, email TEXT NOT NULL, products TEXT NOT NULL, install TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en', app_version TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'requested',
    handled_at REAL, notice TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS beta_requests_install ON beta_requests(install);
CREATE TABLE IF NOT EXISTS beta_installs (install TEXT PRIMARY KEY, approved_at REAL NOT NULL);
"""


class BetaError(Exception):
    """Shown to the caller as it is; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def clean_email(v: Any) -> str:
    """A plain address (lower-cased), or "" when it is not one."""
    if not isinstance(v, str):
        return ""
    v = v.strip().lower()
    return v if len(v) <= 254 and EMAIL_RE.match(v) else ""


def clean_products(v: Any) -> list[str]:
    items = v if isinstance(v, list) else [v] if isinstance(v, str) else []
    return [p for p in PRODUCTS if p in items]


def check_request(body: Any) -> dict[str, Any]:
    """The only fields a request may carry, checked the same way on the copy and again on the broker."""
    if not isinstance(body, dict):
        raise BetaError("send {email, products, app_version?, language?}")
    email = clean_email(body.get("email"))
    if not email:
        raise BetaError("email must be a plain email address")
    products = clean_products(body.get("products"))
    if not products:
        raise BetaError(f"products must name at least one of {', '.join(PRODUCTS)}")
    ver = body.get("app_version")
    lang = body.get("language")
    return {"email": email, "products": products,
            "app_version": ver.strip() if isinstance(ver, str) and VERSION_RE.match(ver.strip()) else "",
            "language": lang if isinstance(lang, str) and lang in i18n.available() else "en"}


def names(products: list[str] | str) -> str:
    items = products.split(",") if isinstance(products, str) else products
    return ", ".join(PRODUCTS[p] for p in PRODUCTS if p in items)


def install_hash(install_id: str) -> str:
    return hashlib.sha256(install_id.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------------------- operator side
class BetaDesk:
    """The operator's requests (SQLite, in the server's own database), the approved installs, and the mail that goes with them."""

    def __init__(self, store: Store, *, notify: Callable[[str, str], Any] | None = None, mailer: Mailer | None = None,
                 mail_account: Callable[[], str] = lambda: "", wall: Callable[[], float] = time.time):
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()
        self.store, self.notify, self.mailer, self.mail_account, self.wall = store, notify, mailer, mail_account, wall
        self.on_event: Callable[[str, dict[str, Any]], Any] | None = None   # the site copy's feed of events (operator_events.py)
        self.tasks: set[asyncio.Task[Any]] = set()

    def purge(self) -> None:
        now = self.wall()
        self.store._run("DELETE FROM beta_requests WHERE (handled_at IS NOT NULL AND handled_at < ?) OR created_at < ?",
                        (now - KEEP_DAYS * 86400, now - OPEN_KEEP_DAYS * 86400))

    def allowed(self, install: str) -> bool:
        return bool(install) and bool(self.store._all("SELECT 1 FROM beta_installs WHERE install = ?", (install,)))

    def status_of(self, install: str) -> str:
        """"approved" (the install is let in), else the state of its latest request ("requested", "declined"), else "none"."""
        self.purge()
        if self.allowed(install):
            return "approved"
        rows = self.store._all("SELECT status FROM beta_requests WHERE install = ? ORDER BY id DESC LIMIT 1", (install,))
        return str(rows[0]["status"]) if rows else "none"

    def add(self, req: dict[str, Any], install: str) -> dict[str, Any]:
        """Stores a request. One open request per install: the same address again adds its products (no new notice); another
        address replaces the open one. An install that is already let in, or whose request for this address was declined, keeps
        that answer."""
        self.purge()
        if self.allowed(install):
            return {"status": "approved", "new": False}
        mine = self.store._all("SELECT * FROM beta_requests WHERE install = ? ORDER BY id DESC", (install,))
        same = next((r for r in mine if r["email"] == req["email"]), None)
        if same is not None and same["status"] == "declined":
            return {"status": "declined", "new": False, "id": same["id"]}
        if same is not None and same["status"] == "requested":
            products = sorted(set(same["products"].split(",")) | set(req["products"]), key=list(PRODUCTS).index)
            self.store._run("UPDATE beta_requests SET products = ?, language = ?, app_version = ? WHERE id = ?",
                            (",".join(products), req["language"], req["app_version"], same["id"]))
            return {"status": "requested", "new": False, "id": same["id"]}
        open_n = int(self.store._all("SELECT COUNT(*) AS n FROM beta_requests WHERE status = 'requested'")[0]["n"])
        if open_n >= MAX_OPEN:
            raise BetaError("the request list is full; try again later", 503)
        self.store._run("DELETE FROM beta_requests WHERE install = ? AND status = 'requested'", (install,))
        cur = self.store._run(
            "INSERT INTO beta_requests(created_at, email, products, install, language, app_version) VALUES(?,?,?,?,?,?)",
            (self.wall(), req["email"], ",".join(req["products"]), install, req["language"], req["app_version"]))
        rid = int(cur.lastrowid or 0)
        self._tell_operator(rid)
        return {"status": "requested", "new": True, "id": rid}

    def row(self, rid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM beta_requests WHERE id = ?", (rid,))
        return rows[0] if rows else None

    @staticmethod
    def view(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["id"], "created_at": r["created_at"], "email": r["email"], "products": r["products"].split(","),
                "language": r["language"], "app_version": r["app_version"], "status": r["status"], "handled_at": r["handled_at"],
                "notice": r["notice"]}

    def list(self, *, limit: int = 200) -> dict[str, Any]:
        self.purge()
        rows = self.store._all("SELECT * FROM beta_requests ORDER BY (status = 'requested') DESC, id DESC LIMIT ?", (max(1, min(limit, 500)),))
        open_n = int(self.store._all("SELECT COUNT(*) AS n FROM beta_requests WHERE status = 'requested'")[0]["n"])
        allowed = int(self.store._all("SELECT COUNT(*) AS n FROM beta_installs")[0]["n"])
        return {"requests": [self.view(r) for r in rows], "open": open_n, "approved_installs": allowed, "keep_days": KEEP_DAYS,
                "mail_from": self.mail_account(), "support": SUPPORT}

    def delete(self, rid: int) -> bool:
        return self.store._run("DELETE FROM beta_requests WHERE id = ?", (rid,)).rowcount > 0

    # ---- the operator's answers
    async def approve(self, rid: int, *, send_mail: bool = True) -> dict[str, Any]:
        r = self.row(rid)
        if r is None:
            raise BetaError("no such request", 404)
        now = self.wall()
        self.store._run("UPDATE beta_requests SET status = 'approved', handled_at = ? WHERE id = ?", (now, rid))
        self.store._run("INSERT OR REPLACE INTO beta_installs(install, approved_at) VALUES(?, ?)", (r["install"], now))
        mailed = await self._mail_requester(r, approved=True) if send_mail else {"mailed": False, "mail_error": ""}
        return {"request": self.view(self.row(rid) or r), **mailed}

    async def decline(self, rid: int, *, send_mail: bool = False) -> dict[str, Any]:
        r = self.row(rid)
        if r is None:
            raise BetaError("no such request", 404)
        self.store._run("UPDATE beta_requests SET status = 'declined', handled_at = ? WHERE id = ?", (self.wall(), rid))
        self.store._run("DELETE FROM beta_installs WHERE install = ?", (r["install"],))   # declining an approved one takes it back
        mailed = await self._mail_requester(r, approved=False) if send_mail else {"mailed": False, "mail_error": ""}
        return {"request": self.view(self.row(rid) or r), **mailed}

    # ---- mail and notices (only ever from add/approve/decline above)
    def _tell_operator(self, rid: int) -> None:
        r = self.row(rid)
        if r is None:
            return
        if self.on_event is not None:
            try:
                self.on_event("beta_request", {"id": rid, "email": r["email"], "products": r["products"].split(",")})
            except Exception:  # noqa: BLE001 - the request is kept either way
                log.warning("beta: could not keep the event")
        if self.notify is not None:
            try:
                self.notify(t("Google sign-in beta request"), f"{r['email']} · {names(r['products'])}")
            except Exception:  # noqa: BLE001 - a notice problem never loses the request
                log.warning("beta: could not add the notification")
        if self.mailer is None or not self.mail_account():
            self.store._run("UPDATE beta_requests SET notice = 'no_mailbox' WHERE id = ?", (rid,))
            return
        task = asyncio.get_running_loop().create_task(self._mail_support(r))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def drain(self) -> None:
        """Waits for the support emails still being sent (tests; shutdown)."""
        while self.tasks:
            running = list(self.tasks)
            await asyncio.gather(*running, return_exceptions=True)
            self.tasks.difference_update(running)   # do not wait for the done-callbacks (they run on a later loop turn)

    async def _mail_support(self, r: dict[str, Any]) -> None:
        email, products = r["email"], names(r["products"])
        when = datetime.fromtimestamp(r["created_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        subject = f"[Kelo beta] {email} asked for {products} access"
        body = (f"Someone asked for beta access to Google sign-in in Kelo.\n\n"
                f"Email: {email}\nFor: {products}\nAsked at: {when}\n\n"
                f"To let them in:\n"
                f"1. Open Google Cloud Console ({CONSOLE}) in the project that holds Kelo's Google sign-in.\n"
                f"2. Google Auth Platform (older console: OAuth consent screen) → Audience → Test users → Add users → {email} → Save.\n"
                f"3. In Kelo: My → Operator → Beta requests → press Approved. Kelo then emails them and lets their copy sign in.\n\n"
                f"Google allows at most 100 test users while the app is in Testing.\n")
        try:
            assert self.mailer is not None
            await self.mailer(SUPPORT, subject, body, "")
            notice = "sent"
        except Exception as e:  # noqa: BLE001 - logged without the address
            log.warning("beta: the email to support could not be sent (%s)", type(e).__name__)
            notice = "failed"
        self.store._run("UPDATE beta_requests SET notice = ? WHERE id = ?", (notice, r["id"]))

    async def _mail_requester(self, r: dict[str, Any], *, approved: bool) -> dict[str, Any]:
        if self.mailer is None or not self.mail_account():
            return {"mailed": False, "mail_error": t("No Gmail is connected on this server, so no email was sent.")}
        lang, products = r["language"], names(r["products"])
        first = PRODUCTS[r["products"].split(",")[0]]
        if approved:
            subject = t("Your Kelo beta access is approved", lang=lang)
            body = "\n\n".join((
                t("Your Kelo beta access for {products} is approved.", lang=lang, products=products),
                t("Open Kelo, go to Connectors → {service} and connect again.", lang=lang, service=first),
                t("While Kelo is in beta, Google asks you to sign in again every 7 days. When it stops working, just connect again.", lang=lang),
                t("Questions? Just reply to this email.", lang=lang) + "\n— Kelo"))
        else:
            subject = t("About your Kelo beta request", lang=lang)
            body = "\n\n".join((
                t("Thank you for your interest in Kelo. We can't add more people to the Google sign-in beta right now, so your request "
                  "for {products} was not approved this time.", lang=lang, products=products),
                t("Google sign-in opens to everyone once Google's review is done.", lang=lang),
                t("Questions? Just reply to this email.", lang=lang) + "\n— Kelo"))
        try:
            await self.mailer(r["email"], subject, body, SUPPORT)
        except Exception as e:  # noqa: BLE001 - the answer is saved either way; the operator sees that the mail failed
            log.warning("beta: the email to the requester could not be sent (%s)", type(e).__name__)
            return {"mailed": False, "mail_error": t("The email could not be sent: {error}", error=str(e)[:160])}
        return {"mailed": True, "mail_error": ""}


class BetaBroker:
    """POST /broker/beta and /broker/beta/status on the operator's server: who may ask, how often."""

    def __init__(self, desk: BetaDesk, installs: ReportBroker, *, clock: Callable[[], float] = time.monotonic):
        self.desk, self.installs, self.clock = desk, installs, clock
        self.hits: dict[str, deque[float]] = {}

    def _gate(self, key: str, limit: int, window: float, what: str) -> None:
        now = self.clock()
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise BetaError(f"too many {what}; try again later", 429, int(window - (now - q[0])) + 1)
        q.append(now)
        if len(self.hits) > 20000:
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 86400]:
                self.hits.pop(k, None)

    def _owner(self, install_id: Any, ip: str) -> str:
        try:   # the same install secret and new-copy limit as reports (one secret per copy)
            return self.installs.owner(install_id, ip)
        except ReportError as e:
            raise BetaError(str(e), e.status, e.retry_after) from None

    def handle(self, body: Any, ip: str) -> dict[str, Any]:
        self._gate(f"ip:{ip}", PER_IP_HOUR, 3600, "requests from one address")
        if not isinstance(body, dict):
            raise BetaError("send {install_id, request}")
        req = check_request(body.get("request"))
        owner = self._owner(body.get("install_id"), ip)
        self._gate(f"install:{owner}", PER_INSTALL_HOUR, 3600, "requests from this copy")
        self._gate(f"day:{owner}", PER_INSTALL_DAY, 86400, "requests from this copy today")
        got = self.desk.add(req, owner)
        log.info("beta: one request received (%s)", "new" if got["new"] else got["status"])
        return {"ok": True, "status": got["status"]}

    def status(self, body: Any, ip: str, open_to_all: bool) -> dict[str, Any]:
        self._gate(f"status:{ip}", STATUS_PER_IP_HOUR, 3600, "status checks from one address")
        install_id = body.get("install_id") if isinstance(body, dict) else None
        if not isinstance(install_id, str) or not INSTALL_RE.match(install_id):
            raise BetaError("install_id must be the copy's own random secret", 403)
        status = self.desk.status_of(install_hash(install_id))
        return {"status": status, "open": open_to_all or status == "approved"}


# ---------------------------------------------------------------------------------------------------------------- each copy
class BetaClient:
    """This copy's request: kept in its own database, sent straight to its own desk when it is the operator, else to the broker."""

    def __init__(self, store: Store, desk: BetaDesk, *, broker_url: str, install_id: Callable[[], str],
                 is_operator: Callable[[], bool], fetcher: Fetcher = fetch, wall: Callable[[], float] = time.time):
        self.store, self.desk, self.install_id, self.is_operator, self.fetcher, self.wall = store, desk, install_id, is_operator, fetcher, wall
        self.url = broker_url.strip().rstrip("/")

    def state(self) -> dict[str, Any]:
        try:
            s = json.loads(self.store.meta(COPY_META) or "{}")
        except ValueError:
            s = {}
        if not isinstance(s, dict) or s.get("status") not in STATUSES:
            return {"status": "none", "email": "", "products": [], "at": None}
        return {"status": s["status"], "email": str(s.get("email") or ""), "products": clean_products(s.get("products")),
                "at": s.get("at"), "checked": float(s.get("checked") or 0)}

    def _save(self, s: dict[str, Any]) -> None:
        self.store.set_meta(COPY_META, json.dumps(s) if s.get("status") in STATUSES else "")

    def approved(self) -> bool:
        """This install may start Google sign-ins while they are closed for everyone (as last learned; see refresh)."""
        if self.is_operator():
            return self.desk.allowed(install_hash(self.install_id()))
        return self.state()["status"] == "approved"

    def install_for_exchange(self) -> str:
        """The install secret, for the broker's exchange to recognise an approved copy ("" when this copy is not approved)."""
        return self.install_id() if self.state()["status"] == "approved" else ""

    async def refresh(self, *, force: bool = False) -> dict[str, Any]:
        """Learns what the operator answered: at most every CACHE_SECONDS, and only when this copy has asked."""
        s = self.state()
        if s["status"] == "none":
            return s
        if self.is_operator():
            got = self.desk.status_of(install_hash(self.install_id()))
        else:
            if not force and self.wall() - s.get("checked", 0) < CACHE_SECONDS:
                return s
            if not self.url:
                return s
            try:
                r = await self.fetcher("POST", f"{self.url}/broker/beta/status", allowed_hosts=[urlsplit(self.url).hostname or ""],
                                       content=json.dumps({"install_id": self.install_id()}).encode(),
                                       headers={"Content-Type": "application/json", "Accept": "application/json"})
                body = json.loads(r.body) if r.status == 200 else None
            except (NetGuardError, ValueError):
                body = None
            if not isinstance(body, dict) or body.get("status") not in (*STATUSES, "none"):
                self._save({**s, "checked": self.wall()})   # try again after the cache time, not on every screen
                return self.state()
            got = str(body["status"])
        s = {**s, "status": got, "checked": self.wall()}
        if got == "none":   # the operator deleted it: the button comes back
            s = {"status": "none"}
        self._save(s)
        return self.state()

    async def send(self, body: Any, *, language: str) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise BetaError(t("Enter the Google email address you will connect."))
        email = clean_email(body.get("email"))
        if not email:
            raise BetaError(t("Enter the Google email address you will connect."))
        try:
            req = check_request({**body, "email": email, "language": language})
        except BetaError:
            raise BetaError(t("Choose Gmail or Google Calendar.")) from None
        s = self.state()
        if s["status"] == "approved":
            return s
        if s["status"] == "requested" and s["email"] == email and set(req["products"]) <= set(s["products"]):
            return s   # already asked for exactly this
        if s["status"] == "requested" and s["email"] == email:
            req["products"] = [p for p in PRODUCTS if p in set(req["products"]) | set(s["products"])]
        if self.is_operator():
            status = self.desk.add(req, install_hash(self.install_id()))["status"]
        else:
            status = await self._forward(req)
        self._save({"status": status, "email": email, "products": req["products"], "at": self.wall(), "checked": self.wall()})
        return self.state()

    async def _forward(self, req: dict[str, Any]) -> str:
        if not self.url:
            raise BetaError(t("This server has no address for the Kelo developer, so the request could not be sent."), 503)
        content = json.dumps({"install_id": self.install_id(), "request": req}).encode()
        try:
            r = await self.fetcher("POST", f"{self.url}/broker/beta", allowed_hosts=[urlsplit(self.url).hostname or ""], content=content,
                                   headers={"Content-Type": "application/json", "Accept": "application/json"})
        except NetGuardError:
            raise BetaError(t("Could not reach the Kelo developer's server. Try again later."), 502) from None
        if r.status == 200:
            try:
                got = json.loads(r.body)
            except ValueError:
                got = {}
            status = got.get("status") if isinstance(got, dict) else None
            return str(status) if status in STATUSES else "requested"
        if r.status == 429:
            v = {k.lower(): v for k, v in (r.headers or {}).items()}.get("retry-after", "")
            raise BetaError(t("Too many requests were sent from here for now. Try again later."), 429, int(v) if v.isdigit() else None)
        if r.status == 422:
            raise BetaError(t("Enter the Google email address you will connect."), 422)
        log.warning("beta: the developer's server answered HTTP %d", r.status)
        raise BetaError(t("Could not reach the Kelo developer's server. Try again later."), 502)
