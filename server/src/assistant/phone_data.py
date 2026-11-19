"""What the user's phone shares from itself ("이 기기에서"): contacts, text messages, the call log, notifications and Health Connect
summaries, plus two things the agent may ask the phone to do (send a text, open the dialer) after the user approves.

Each item is its own switch in the Kelo app and is sent only after the user turns it on (Android asks for the permission then). Every
phone keeps its own copy here (owner = the paired device's id, or "owner" for the access key), sealed at rest with AES-256-GCM (the
store's personal-data key, bound to the kind and the device), replaced by the next upload and wiped when the switch is turned off or
the phone is removed. Nothing is ever written to the phone's contacts, messages or call log by these endpoints.

Snapshots (each upload replaces this phone's copy of that kind):
PUT    /api/phone/contacts   {"device_name"?, "items": [Contact]}             -> {"ok", "count", "uploaded_at"}
       Contact: {"name", "phones": [{"number", "label"?}], "emails": [str], "org"?, "title"?, "birthday"?: "YYYY-MM-DD" | "--MM-DD"}
PUT    /api/phone/messages   {"device_name"?, "window_days"?: 30, "can_send"?: bool, "items": [Message]}
       Message: {"address", "name"?, "body", "at": unix, "box": "inbox" | "sent", "kind"?: "sms" | "mms", "thread"?: int}
PUT    /api/phone/call-log   {"device_name"?, "window_days"?: 30, "items": [Call]}
       Call: {"number", "name"?, "type": incoming|outgoing|missed|rejected|blocked|voicemail, "at": unix, "duration": seconds}
PUT    /api/phone/health     {"device_name"?, "timezone"?, "types": [steps|sleep|heart_rate|weight|exercise], "days": [Day]}
       Day: {"day": "YYYY-MM-DD", "steps"?, "sleep_minutes"?, "sleep_start"?, "sleep_end"?, "heart_min"?, "heart_avg"?, "heart_max"?,
             "weight_kg"?, "exercise"?: [{"type", "title"?, "start", "end", "minutes"}]}
PUT    /api/phone/dialer     {"device_name"?}                                   (no data: this phone may open its dialer for Kelo)
A batch that is added to (kept KEEP_NOTIFICATION_DAYS days):
POST   /api/phone/notifications {"device_name"?, "items": [{"app", "package", "title", "text", "at": unix}]} -> {"ok", "added", "count"}
For every kind: DELETE /api/phone/{kind} -> {"ok"} (the switch turned off on this phone: its copy is wiped) and
GET /api/phone/{kind} -> {"shared", "uploaded_at", "count", "devices": [{"name", "uploaded_at", "count", "this"}]}.
GET    /api/phone            -> {"kinds": {kind: {"shared", "uploaded_at", "count"}}}   (this phone only; calendar is /api/phone-calendar)

Work the phone does after an approval (the server never sends a text or places a call itself):
POST   /api/phone/tasks/claim                -> {"items": [Task]}   (this phone's queued tasks; each is handed out once)
       Task: {"id", "kind", "expires_at", ...}: sms {"number", "name", "text"} · dial {"number", "name"} ·
             cal_add {"calendar_id", "title", "start", "end", "all_day", "location"} · cal_update {"event_id", same fields, only the
             changed ones} · cal_delete {"event_id", "title"}
POST   /api/phone/tasks/{id}/result {"ok": bool, "outcome"?: "sent" | "opened" | "notified" | "done", "error"?} -> {"ok"}
A queued task wakes the phone with a push that carries only its id ({"type": "device_task", "id"}); the app also checks after the user
answers an approval, when it comes to the front and from its 15-minute job. Texts and calendar changes expire after SMS_TTL, dialer requests after DIAL_TTL.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from .agent import Tool
from .code_shelf import SHELF
from .data_trail import USES_PARAM, note_item
from .i18n import t
from .phone_calendar import _parse, _zone, ago
from .tools.connect_tools import owner_request
from .store import Store

SNAPSHOT_KINDS = ("contacts", "messages", "call_log", "health", "dialer")
KINDS = (*SNAPSHOT_KINDS, "notifications")

# What the hourly look reads of each phone item the owner shares (situation_reads.declared): data, one declaration per kind. An item
# with no state of its own (contacts, the dialer) has none. "lines": the tool's list lines start with "- "; the freshness line does not,
# so "last sync" text stays out of the fingerprint. Labels are translated when the pack is built.
SITUATION: dict[str, dict[str, Any]] = {
    "messages": {"tool": "phone_messages", "arguments": {"days": 1, "limit": 30}, "mode": "changes", "lines": "^- ", "max_rows": 30,
                 "label": "Text messages"},
    "call_log": {"tool": "phone_call_log", "arguments": {"days": 1, "limit": 20}, "mode": "changes", "lines": "^- ", "max_rows": 20,
                 "label": "Calls"},
    "notifications": {"tool": "phone_notifications", "arguments": {"hours": 3, "limit": 40}, "mode": "changes", "lines": "^- ",
                      "max_rows": 40, "label": "Notifications"},
    "health": {"tool": "phone_health", "arguments": {}, "mode": "state", "lines": "^- ", "max_rows": 20, "wakes": False, "label": "Health"},
}
PATHS = {"contacts": "contacts", "messages": "messages", "call-log": "call_log", "health": "health", "dialer": "dialer",
         "notifications": "notifications"}
LIMITS = {"contacts": 10_000, "messages": 6000, "call_log": 5000, "health": 400}
MAX_BYTES = {"contacts": 4_000_000, "messages": 8_000_000, "call_log": 2_000_000, "health": 1_000_000, "dialer": 10_000,
             "notifications": 1_000_000}
KEEP_NOTIFICATION_DAYS = 7
MAX_NOTIFICATIONS = 3000            # per phone; the oldest go first
MAX_BATCH = 300
SMS_TTL = 3600                      # a text the phone has not picked up within an hour is not sent any more
DIAL_TTL = 600
WAIT_SECONDS = 40.0                 # how long the tool waits for the phone to say how it went
MAX_TEXT = 1600                     # about ten SMS parts
HEALTH_TYPES = ("steps", "sleep", "heart_rate", "weight", "exercise")
CALL_TYPES = ("incoming", "outgoing", "missed", "rejected", "blocked", "voicemail")

SCHEMA = """
CREATE TABLE IF NOT EXISTS phone_data (
    owner TEXT NOT NULL, kind TEXT NOT NULL, device_name TEXT NOT NULL DEFAULT '', data TEXT NOT NULL, uploaded_at REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (owner, kind));
CREATE TABLE IF NOT EXISTS phone_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, posted_at REAL NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS phone_notes_owner ON phone_notes(owner, posted_at);
CREATE UNIQUE INDEX IF NOT EXISTS phone_notes_digest ON phone_notes(owner, digest);
CREATE TABLE IF NOT EXISTS phone_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL,
    expires_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'queued', outcome TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
    taken_at REAL, done_at REAL);
"""


# ---- cleaning what the phone sends (anything odd is left out, long text clipped)
def _s(v: Any, n: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v or "")).strip()[:n]


def _ts(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0 < f < 4_102_444_800 else None


def _int(v: Any, lo: int, hi: int) -> int | None:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def _num(v: Any, lo: float, hi: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 2) if lo <= f <= hi else None


def digits(number: str) -> str:
    return re.sub(r"\D", "", number or "")


def same_number(a: str, b: str) -> bool:
    """Two phone numbers are the same line (the last 8 digits match; +82 10... and 010... too)."""
    da, db = digits(a), digits(b)
    if not da or not db:
        return False
    return da == db or (len(da) >= 8 and len(db) >= 8 and da[-8:] == db[-8:])


def clean_contact(c: Any) -> dict[str, Any] | None:
    if not isinstance(c, dict):
        return None
    name = _s(c.get("name"), 120)
    phones = []
    for p in (c.get("phones") or [])[:10] if isinstance(c.get("phones"), list) else []:
        p = p if isinstance(p, dict) else {"number": p}
        num = _s(p.get("number"), 40)
        if digits(num):
            phones.append({"number": num, "label": _s(p.get("label"), 30)})
    emails = [e for e in (_s(x, 120) for x in (c.get("emails") or [])[:10] if isinstance(c.get("emails"), list)) if "@" in e]
    if not name and not phones and not emails:
        return None
    bday = _s(c.get("birthday"), 10)
    if not re.fullmatch(r"(\d{4}|-)-\d{2}-\d{2}", bday):
        bday = ""
    return {"name": name, "phones": phones, "emails": emails, "org": _s(c.get("org"), 120), "title": _s(c.get("title"), 120), "birthday": bday}


def clean_message(m: Any) -> dict[str, Any] | None:
    if not isinstance(m, dict):
        return None
    at = _ts(m.get("at"))
    address = _s(m.get("address"), 60)
    if at is None or not address:
        return None
    return {"address": address, "name": _s(m.get("name"), 120), "body": _s(m.get("body"), 2000), "at": at,
            "box": "sent" if m.get("box") == "sent" else "inbox", "kind": "mms" if m.get("kind") == "mms" else "sms",
            "thread": _int(m.get("thread"), 0, 10**12) or 0}


def clean_call(c: Any) -> dict[str, Any] | None:
    if not isinstance(c, dict):
        return None
    at = _ts(c.get("at"))
    if at is None:
        return None
    kind = c.get("type") if c.get("type") in CALL_TYPES else "incoming"
    return {"number": _s(c.get("number"), 40), "name": _s(c.get("name"), 120), "type": kind, "at": at,
            "duration": _int(c.get("duration"), 0, 86_400 * 2) or 0}


def clean_day(d: Any) -> dict[str, Any] | None:
    if not isinstance(d, dict):
        return None
    try:
        day = date.fromisoformat(str(d.get("day"))).isoformat()
    except ValueError:
        return None
    out: dict[str, Any] = {"day": day}
    for k, lo, hi in (("steps", 0, 300_000), ("sleep_minutes", 0, 24 * 60 * 2)):
        if (n := _int(d.get(k), lo, hi)) is not None:
            out[k] = n
    for k in ("sleep_start", "sleep_end"):
        if (v := _ts(d.get(k))) is not None:
            out[k] = v
    for k in ("heart_min", "heart_avg", "heart_max", "heart_resting"):
        if (v := _num(d.get(k), 20, 260)) is not None:
            out[k] = v
    if (w := _num(d.get("weight_kg"), 1, 500)) is not None:
        out["weight_kg"] = w
    ex = []
    for e in (d.get("exercise") or [])[:30] if isinstance(d.get("exercise"), list) else []:
        if not isinstance(e, dict) or (s := _ts(e.get("start"))) is None:
            continue
        end = _ts(e.get("end")) or s
        ex.append({"type": _s(e.get("type"), 40) or "exercise", "title": _s(e.get("title"), 80), "start": s, "end": max(s, end),
                   "minutes": _int(e.get("minutes"), 0, 24 * 60) or round(max(0.0, end - s) / 60)})
    if ex:
        out["exercise"] = ex
    return out if len(out) > 1 else None


def clean_note(n: Any) -> dict[str, Any] | None:
    if not isinstance(n, dict):
        return None
    at = _ts(n.get("at"))
    title, text = _s(n.get("title"), 200), _s(n.get("text"), 1000)
    if at is None or not (title or text):
        return None
    return {"app": _s(n.get("app"), 60), "package": _s(n.get("package"), 120), "title": title, "text": text, "at": at}


class PhoneData:
    """Sealed per-phone copies of what the phone shares, the notifications it forwards, and the tasks it is asked to do."""

    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        self.on_task: Callable[[int], None] | None = None      # a task was queued: wake the phones (push, ids only)
        self._waiters: dict[int, asyncio.Event] = {}
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    @staticmethod
    def _aad(kind: str, owner: str) -> str:
        return f"phone-{kind}:{owner}"

    def _seal(self, kind: str, owner: str, obj: Any) -> str:
        return self.store.data_sealer.seal(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), self._aad(kind, owner))

    def _open(self, kind: str, owner: str, sealed: str) -> Any:
        plain = self.store.data_sealer.open(sealed, self._aad(kind, owner))
        if plain is None:
            return None   # the master key changed or the row was altered: treated as not shared
        try:
            return json.loads(plain)
        except ValueError:
            return None

    # ---- snapshots
    def put(self, owner: str, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        if kind not in SNAPSHOT_KINDS:
            raise ValueError("unknown kind")
        data: dict[str, Any]
        if kind == "dialer":
            data, count = {}, 0
        elif kind == "health":
            raw = body.get("days")
            if not isinstance(raw, list):
                raise ValueError("days must be a list")
            days = sorted((d for d in (clean_day(x) for x in raw[:LIMITS["health"]]) if d), key=lambda d: d["day"])
            types = [x for x in (body.get("types") or []) if x in HEALTH_TYPES] if isinstance(body.get("types"), list) else []
            data, count = {"timezone": _s(body.get("timezone"), 64), "types": types, "days": days}, len(days)
        else:
            raw = body.get("items")
            if not isinstance(raw, list):
                raise ValueError("items must be a list")
            clean = {"contacts": clean_contact, "messages": clean_message, "call_log": clean_call}[kind]
            items = [x for x in (clean(i) for i in raw[:LIMITS[kind]]) if x is not None]
            if kind != "contacts":
                items.sort(key=lambda x: x["at"], reverse=True)
            data = {"items": items, "window_days": _int(body.get("window_days"), 1, 365) or 30}
            if kind == "messages":
                data["can_send"] = bool(body.get("can_send"))
            count = len(items)
        now = self.clock()
        self.store._run("INSERT INTO phone_data(owner, kind, device_name, data, uploaded_at, count) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(owner, kind) DO UPDATE SET device_name = excluded.device_name, data = excluded.data, "
                        "uploaded_at = excluded.uploaded_at, count = excluded.count",
                        (owner, kind, _s(body.get("device_name"), 40), self._seal(kind, owner, data), now, count))
        return {"ok": True, "count": count, "uploaded_at": now}

    def snapshots(self, kind: str) -> list[dict[str, Any]]:
        out = []
        for r in self.store._all("SELECT * FROM phone_data WHERE kind = ? ORDER BY uploaded_at DESC", (kind,)):
            data = self._open(kind, r["owner"], r["data"])
            if isinstance(data, dict):
                out.append({"owner": r["owner"], "device_name": r["device_name"], "uploaded_at": r["uploaded_at"], **data})
        return out

    def _digest(self, text: str) -> str:
        """A keyed hash (HMAC with a random key kept encrypted in the store) that spots a notification sent twice without
        revealing its words to anyone who reads the database file."""
        key = self.store.key("phone:note-digest")
        if not key:
            key = secrets.token_urlsafe(32)
            self.store.set_key("phone:note-digest", key)
        return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()[:32]

    # ---- notifications
    def add_notes(self, owner: str, body: dict[str, Any]) -> dict[str, Any]:
        raw = body.get("items")
        if not isinstance(raw, list):
            raise ValueError("items must be a list")
        now = self.clock()
        added = 0
        for n in (clean_note(x) for x in raw[:MAX_BATCH]):
            if n is None or n["at"] < now - KEEP_NOTIFICATION_DAYS * 86400:
                continue
            digest = self._digest(f"{owner}|{n['package']}|{n['title']}|{n['text']}|{int(n['at'])}")
            cur = self.store._run("INSERT OR IGNORE INTO phone_notes(owner, posted_at, digest, data) VALUES(?,?,?,?)",
                                  (owner, n["at"], digest, self._seal("notifications", owner, n)))
            added += cur.rowcount
        # the device name is kept in the snapshot table (no data) so the status lists this phone like the others
        self.store._run("INSERT INTO phone_data(owner, kind, device_name, data, uploaded_at, count) VALUES(?,?,?,?,?,0) "
                        "ON CONFLICT(owner, kind) DO UPDATE SET device_name = excluded.device_name, uploaded_at = excluded.uploaded_at",
                        (owner, "notifications", _s(body.get("device_name"), 40), self._seal("notifications", owner, {}), now))
        self.prune(owner)
        count = self.store._all("SELECT COUNT(*) AS n FROM phone_notes WHERE owner = ?", (owner,))[0]["n"]
        self.store._run("UPDATE phone_data SET count = ? WHERE owner = ? AND kind = 'notifications'", (count, owner))
        return {"ok": True, "added": added, "count": count}

    def prune(self, owner: str | None = None) -> None:
        cutoff = self.clock() - KEEP_NOTIFICATION_DAYS * 86400
        self.store._run("DELETE FROM phone_notes WHERE posted_at < ?", (cutoff,))
        for r in self.store._all("SELECT owner, COUNT(*) AS n FROM phone_notes GROUP BY owner HAVING n > ?", (MAX_NOTIFICATIONS,)):
            if owner is None or r["owner"] == owner:
                self.store._run("DELETE FROM phone_notes WHERE id IN (SELECT id FROM phone_notes WHERE owner = ? ORDER BY posted_at ASC LIMIT ?)",
                                (r["owner"], r["n"] - MAX_NOTIFICATIONS))

    def notes(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(every kept notification, newest first, with its phone's name; the phones that forward them)."""
        self.prune()
        names = {r["owner"]: r for r in self.store._all("SELECT owner, device_name, uploaded_at FROM phone_data WHERE kind = 'notifications'")}
        out = []
        for r in self.store._all("SELECT owner, data FROM phone_notes ORDER BY posted_at DESC"):
            if r["owner"] not in names:
                continue
            n = self._open("notifications", r["owner"], r["data"])
            if isinstance(n, dict):
                out.append({**n, "device": names[r["owner"]]["device_name"]})
        return out, [{"owner": o, "device_name": r["device_name"], "uploaded_at": r["uploaded_at"]} for o, r in names.items()]

    # ---- for every kind
    def delete(self, owner: str, kind: str) -> bool:
        n = self.store._run("DELETE FROM phone_data WHERE owner = ? AND kind = ?", (owner, kind)).rowcount
        if kind == "notifications":
            n += self.store._run("DELETE FROM phone_notes WHERE owner = ?", (owner,)).rowcount
        if kind in ("messages", "dialer"):   # its waiting texts or calls are not done any more
            self._fail_open(owner, "sms" if kind == "messages" else "dial", "turned off on the phone")
        if kind == "calendar":
            for k in ("cal_add", "cal_update", "cal_delete"):
                self._fail_open(owner, k, "calendar writing turned off on the phone")
        return n > 0

    def delete_owner(self, owner: str) -> None:
        """The phone was removed: nothing it shared stays here."""
        for kind in KINDS:
            self.delete(owner, kind)
        self.store._run("DELETE FROM phone_tasks WHERE owner = ?", (owner,))

    def status(self, owner: str, kind: str) -> dict[str, Any]:
        rows = self.store._all("SELECT owner, device_name, uploaded_at, count FROM phone_data WHERE kind = ? ORDER BY uploaded_at DESC", (kind,))
        mine = next((r for r in rows if r["owner"] == owner), None)
        return {"shared": mine is not None, "uploaded_at": mine["uploaded_at"] if mine else None, "count": mine["count"] if mine else 0,
                "devices": [{"name": r["device_name"], "uploaded_at": r["uploaded_at"], "count": r["count"], "this": r["owner"] == owner}
                            for r in rows]}

    def summary(self, owner: str) -> dict[str, Any]:
        rows = {r["kind"]: r for r in self.store._all("SELECT kind, uploaded_at, count FROM phone_data WHERE owner = ?", (owner,))}
        return {"kinds": {k: {"shared": k in rows, "uploaded_at": rows[k]["uploaded_at"] if k in rows else None,
                              "count": rows[k]["count"] if k in rows else 0} for k in KINDS}}

    def shared_kinds(self) -> set[str]:
        """The kinds any phone shares now (the owner's switches): situation_reads reads only these."""
        return {r["kind"] for r in self.store._all("SELECT DISTINCT kind FROM phone_data")}

    # ---- tasks for the phone (after an approval)
    def sender(self) -> dict[str, Any] | None:
        """The phone that sends texts: the newest one that shares messages and may send them."""
        return next((s for s in self.snapshots("messages") if s.get("can_send")), None)

    def dialer(self) -> dict[str, Any] | None:
        rows = self.store._all("SELECT owner, device_name, uploaded_at FROM phone_data WHERE kind = 'dialer' ORDER BY uploaded_at DESC LIMIT 1")
        return dict(rows[0]) if rows else None

    def queue(self, owner: str, kind: str, payload: dict[str, Any]) -> int:
        now = self.clock()
        ttl = DIAL_TTL if kind == "dial" else SMS_TTL
        cur = self.store._run("INSERT INTO phone_tasks(owner, kind, data, created_at, expires_at) VALUES(?,?,?,?,?)",
                              (owner, kind, self.store.data_sealer.seal(json.dumps(payload, ensure_ascii=False), f"phone-task:{owner}"), now, now + ttl))
        task_id = int(cur.lastrowid or 0)
        self._waiters[task_id] = asyncio.Event()
        if self.on_task is not None:
            try:
                self.on_task(task_id)
            except Exception:  # noqa: BLE001 - the phone also checks on its own
                pass
        return task_id

    def claim(self, owner: str) -> list[dict[str, Any]]:
        now = self.clock()
        self._expire()
        out = []
        for r in self.store._all("SELECT * FROM phone_tasks WHERE owner = ? AND state = 'queued' ORDER BY id", (owner,)):
            if self.store._run("UPDATE phone_tasks SET state = 'taken', taken_at = ? WHERE id = ? AND state = 'queued'", (now, r["id"])).rowcount == 0:
                continue   # another check of the same phone took it
            plain = self.store.data_sealer.open(r["data"], f"phone-task:{owner}")
            p = json.loads(plain) if plain else {}
            out.append({**p, "id": r["id"], "kind": r["kind"], "expires_at": r["expires_at"]})
        return out

    def result(self, owner: str, task_id: int, body: dict[str, Any]) -> bool:
        ok = bool(body.get("ok"))
        outcome = body.get("outcome") if body.get("outcome") in ("sent", "opened", "notified", "done") else ("sent" if ok else "")
        n = self.store._run("UPDATE phone_tasks SET state = ?, outcome = ?, error = ?, done_at = ? WHERE id = ? AND owner = ? AND state IN ('queued', 'taken')",
                            ("done" if ok else "failed", outcome, _s(body.get("error"), 200), self.clock(), task_id, owner)).rowcount
        if n and (ev := self._waiters.get(task_id)):
            ev.set()
        return n > 0

    def task(self, task_id: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT id, owner, kind, state, outcome, error, created_at, expires_at FROM phone_tasks WHERE id = ?", (task_id,))
        return dict(rows[0]) if rows else None

    async def wait(self, task_id: int, timeout: float = WAIT_SECONDS) -> dict[str, Any] | None:
        ev = self._waiters.get(task_id)
        try:
            if ev is not None:
                await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._waiters.pop(task_id, None)
        return self.task(task_id)

    def _expire(self) -> None:
        self.store._run("UPDATE phone_tasks SET state = 'expired' WHERE state = 'queued' AND expires_at < ?", (self.clock(),))
        self.store._run("DELETE FROM phone_tasks WHERE created_at < ?", (self.clock() - 7 * 86400,))   # the outcome is kept a week

    def _fail_open(self, owner: str, kind: str, why: str) -> None:
        for r in self.store._all("SELECT id FROM phone_tasks WHERE owner = ? AND kind = ? AND state = 'queued'", (owner, kind)):
            self.result(owner, r["id"], {"ok": False, "error": why})




# ---- the agent's tools
NOT_SHARED = ("{what} is not shared from the user's phone. The user can turn it on in the Kelo app (Connectors → On this device → {row}); "
              "it is sent only to the user's own server.")


def _freshness(snaps: list[dict[str, Any]], now: float) -> list[str]:
    out = []
    for s in snaps:
        stale = " — old, the phone has not sent an update for a while" if now - s["uploaded_at"] > 2 * 86400 else ""
        out.append(f"From {s['device_name'] or 'a phone'}, updated {ago(now - s['uploaded_at'])}{stale}.")
    return out


def _who(name: str, number: str) -> str:
    return f"{name} ({number})" if name and number and name != number else (name or number or "unknown")


def phone_tools(data: PhoneData, timezone_name: Callable[[], str | None]) -> list[Tool]:
    def tz() -> Any:
        return _zone(timezone_name())

    def fmt(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz()).strftime("%Y-%m-%d %H:%M")

    def contacts() -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        out = []
        for s in data.snapshots("contacts"):
            for c in s.get("items", []):
                key = (c["name"].lower(), digits(c["phones"][0]["number"]) if c["phones"] else "")
                if key not in seen:
                    seen.add(key)
                    out.append(c)
        return out

    def name_for(number: str) -> str:
        for c in contacts():
            if any(same_number(p["number"], number) for p in c["phones"]):
                return c["name"]
        return ""

    def resolve(to: str) -> tuple[str, str, str]:
        """(number, contact name, problem) for a number or a contact's name."""
        to = str(to or "").strip()
        if not to:
            return "", "", "say who: a phone number, or a contact's name"
        if re.fullmatch(r"[+\d\s().\-]{3,40}", to) and len(digits(to)) >= 3:
            return to, name_for(to), ""
        q = to.lower()
        found = [c for c in contacts() if c["phones"] and q in c["name"].lower()]
        exact = [c for c in found if c["name"].lower() == q]
        found = exact or found
        if not found:
            return "", "", f"no contact called {to!r} with a phone number" + ("" if data.snapshots("contacts") else " (contacts are not shared)")
        if len(found) > 1 or len(found[0]["phones"]) > 1:   # which number is the mobile one is not guessed from its label: ask
            options = "; ".join(f"{c['name']}: " + ", ".join(p["number"] for p in c["phones"]) for c in found[:6])
            return "", "", f"{to!r} matches more than one number — ask which one, then give the number: {options}"
        c = found[0]
        return c["phones"][0]["number"], c["name"], ""

    # ---- contacts
    async def phone_contacts(args: dict[str, Any]) -> str:
        snaps = data.snapshots("contacts")
        if not snaps:
            return NOT_SHARED.format(what="Contacts", row="Contacts")
        q = str(args.get("query") or "").strip().lower()
        qd = digits(q)
        limit = max(1, min(int(args.get("limit") or 20), 100))
        hits = []
        for c in contacts():
            hay = " ".join([c["name"], c["org"], c["title"], *c["emails"]]).lower()
            if not q or q in hay or (len(qd) >= 3 and any(qd in digits(p["number"]) for p in c["phones"])):
                hits.append(c)
        lines = _freshness(snaps, data.clock())
        if not hits:
            lines.append(f"No contact matches {q!r}." if q else "The shared contacts are empty.")
            return "\n".join(lines)
        lines.append(f"Contacts, {len(hits)}{' (first ' + str(limit) + ' shown)' if len(hits) > limit else ''}:")
        for c in hits[:limit]:
            parts = [", ".join(f"{p['number']}{' (' + p['label'] + ')' if p['label'] else ''}" for p in c["phones"])] if c["phones"] else []
            parts += [", ".join(c["emails"])] if c["emails"] else []
            work = " · ".join(x for x in (c["title"], c["org"]) if x)
            parts += [work] if work else []
            parts += [f"birthday {c['birthday'].lstrip('-')}"] if c["birthday"] else []
            lines.append(f"- {c['name'] or '(no name)'}: " + " · ".join(parts))
            note_item(f"contact:{c['name'] or parts[0] if parts else ''}", title=c["name"] or "", text=lines[-1])
        return "\n".join(lines)

    # ---- messages
    async def phone_messages(args: dict[str, Any]) -> str:
        snaps = data.snapshots("messages")
        if not snaps:
            return NOT_SHARED.format(what="Text messages", row="Messages")
        now = data.clock()
        q = str(args.get("query") or "").strip().lower()
        with_ = str(args.get("with") or "").strip()
        days = max(1, min(int(args.get("days") or 30), 365))
        box = args.get("box") if args.get("box") in ("inbox", "sent") else ""
        limit = max(1, min(int(args.get("limit") or 40), 200))
        hits, seen = [], set()
        for s in snaps:
            for m in s.get("items", []):
                if m["at"] < now - days * 86400 or (box and m["box"] != box):
                    continue
                if with_ and not (same_number(m["address"], with_) or with_.lower() in (m["name"] or name_for(m["address"])).lower()):
                    continue
                if q and q not in f"{m['body']} {m['name']} {m['address']}".lower():
                    continue
                key = (digits(m["address"])[-8:], int(m["at"]), m["body"][:40])
                if key not in seen:
                    seen.add(key)
                    hits.append(m)
        hits.sort(key=lambda m: m["at"], reverse=True)
        lines = _freshness(snaps, now)
        window = min(s.get("window_days", 30) for s in snaps)
        if days > window:
            lines.append(f"The phone shares only the last {window} days of messages.")
        if not hits:
            lines.append("No messages match." if (q or with_) else f"No messages in the last {days} days.")
            return "\n".join(lines)
        lines.append(f"Messages ({getattr(tz(), 'key', 'UTC')}), newest first, {len(hits)}"
                     f"{' (first ' + str(limit) + ' shown)' if len(hits) > limit else ''}:")
        shown_all = await SHELF.mask_many([(str(m["body"] or ""), str(m["address"] or ""), "") for m in hits[:limit]], channel="text")   # code_shelf.py
        for m, shown in zip(hits[:limit], shown_all):
            who = _who(m["name"] or name_for(m["address"]), m["address"])
            arrow = f"to {who}" if m["box"] == "sent" else f"from {who}"
            lines.append(f"- {fmt(m['at'])} {arrow}{' (MMS)' if m['kind'] == 'mms' else ''}: {shown or '(no text)'}")
            note_item(f"message:{m['at']}:{m['address']}", party=str(m["address"] or ""), title=str(who), text=str(m["body"] or ""))
        return "\n".join(lines)

    def check_send(args: dict[str, Any]) -> str | None:
        if data.sender() is None:
            return ("no phone can send texts for Kelo. The user can turn on Messages with sending allowed in the Kelo app "
                    "(only in the full Kelo build installed with adb; the regular APK and the Play Store version do not read or send texts)")
        text = str(args.get("text") or "").strip()
        if not text:
            return "the text is empty"
        if len(text) > MAX_TEXT:
            return f"the text is too long ({len(text)} characters; at most {MAX_TEXT})"
        number, _, problem = resolve(str(args.get("to") or ""))
        if problem:
            return problem
        if len(digits(number)) < 7:
            return "short numbers (service codes) can cost money and are not texted by Kelo; give a full phone number"
        return None

    async def describe_send(args: dict[str, Any]) -> str:
        number, name, _ = resolve(str(args.get("to") or ""))
        text = " ".join(str(args.get("text") or "").split())
        return t("Text to {who}: “{text}”", who=_who(name, number), text=text[:160] + ("…" if len(text) > 160 else ""))

    def send_lines(_approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        number, name, _ = resolve(str(args.get("to") or ""))
        sender = data.sender()
        lines = [{"label": t("To"), "value": f"{name} · {number}" if name else number},
                 {"label": t("Message"), "value": str(args.get("text") or "").strip(), "full": True}]
        if sender:
            lines.append({"label": t("Sent from"), "value": sender["device_name"] or t("this phone")})
        return {"lines": lines}

    async def phone_send_text(args: dict[str, Any]) -> str:
        sender = data.sender()
        if sender is None:
            return "error: no phone can send texts for Kelo right now"
        number, name, problem = resolve(str(args.get("to") or ""))
        if problem:
            return f"error: {problem}"
        text = str(args.get("text") or "").strip()
        task_id = data.queue(sender["owner"], "sms", {"number": number, "name": name, "text": text})
        done = await data.wait(task_id)
        who = _who(name, number)
        if done and done["state"] == "done":
            return f"Sent the text to {who} from {sender['device_name'] or 'the phone'}."
        if done and done["state"] == "failed":
            return f"The phone could not send the text to {who}: {done['error'] or 'unknown reason'}. Tell the user; do not retry on your own."
        return (f"The text to {who} is waiting for {sender['device_name'] or 'the phone'} to pick it up (the phone seems offline). It is sent "
                "when the phone comes online within an hour, otherwise it is dropped. Tell the user it is not sent yet.")

    # ---- calls
    def check_call(args: dict[str, Any]) -> str | None:
        if data.dialer() is None:
            return "no phone has the dialer turned on for Kelo. The user can turn on Phone calls in the Kelo app (Connectors → On this device)"
        _, _, problem = resolve(str(args.get("to") or args.get("number") or ""))
        return problem or None

    async def describe_call(args: dict[str, Any]) -> str:
        number, name, _ = resolve(str(args.get("to") or args.get("number") or ""))
        return t("Open the dialer with {who} filled in. You press call yourself.", who=_who(name, number))

    def call_lines(_approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        number, name, _ = resolve(str(args.get("to") or args.get("number") or ""))
        d = data.dialer()
        lines = [{"label": t("Call"), "value": f"{name} · {number}" if name else number}]
        if str(args.get("reason") or "").strip():
            lines.append({"label": t("Why"), "value": str(args.get("reason")).strip()[:200]})
        if d:
            lines.append({"label": t("On"), "value": d["device_name"] or t("this phone")})
        lines.append({"label": t("Note"), "value": t("Kelo only opens the dialer; the call starts when you press call."), "full": True})
        return {"lines": lines}

    async def phone_call(args: dict[str, Any]) -> str:
        d = data.dialer()
        if d is None:
            return "error: no phone has the dialer turned on for Kelo"
        number, name, problem = resolve(str(args.get("to") or args.get("number") or ""))
        if problem:
            return f"error: {problem}"
        task_id = data.queue(d["owner"], "dial", {"number": number, "name": name})
        done = await data.wait(task_id, timeout=25)
        who = _who(name, number)
        if done and done["state"] == "done":
            how = "showed a notification to open the dialer" if done["outcome"] == "notified" else "opened the dialer"
            return f"The phone {how} with {who} filled in. The user places the call themselves; Kelo cannot hear or join it."
        if done and done["state"] == "failed":
            return f"The phone could not open the dialer: {done['error'] or 'unknown reason'}."
        return f"The phone has not picked up the request yet (it may be offline); it opens the dialer for {who} if it comes online within 10 minutes."

    # ---- call log
    async def phone_call_log(args: dict[str, Any]) -> str:
        snaps = data.snapshots("call_log")
        if not snaps:
            return NOT_SHARED.format(what="The call log", row="Call history")
        now = data.clock()
        days = max(1, min(int(args.get("days") or 7), 365))
        kind = args.get("type") if args.get("type") in CALL_TYPES else ""
        q = str(args.get("query") or "").strip()
        limit = max(1, min(int(args.get("limit") or 50), 200))
        hits = []
        for s in snaps:
            for c in s.get("items", []):
                if c["at"] < now - days * 86400 or (kind and c["type"] != kind):
                    continue
                name = c["name"] or name_for(c["number"])
                if q and not (same_number(c["number"], q) or q.lower() in name.lower()):
                    continue
                hits.append({**c, "name": name})
        hits.sort(key=lambda c: c["at"], reverse=True)
        lines = _freshness(snaps, now)
        if not hits:
            lines.append(f"No calls in the last {days} days match." if (q or kind) else f"No calls in the last {days} days.")
            return "\n".join(lines)
        lines.append(f"Calls ({getattr(tz(), 'key', 'UTC')}), newest first, {len(hits)}{' (first ' + str(limit) + ' shown)' if len(hits) > limit else ''}:")
        for c in hits[:limit]:
            dur = f", {c['duration'] // 60} min {c['duration'] % 60} s" if c["duration"] else ""
            lines.append(f"- {fmt(c['at'])} {c['type']} · {_who(c['name'], c['number'] or 'hidden number')}{dur}")
        return "\n".join(lines)

    # ---- notifications
    async def phone_notifications(args: dict[str, Any]) -> str:
        notes, phones = data.notes()
        if not phones:
            return NOT_SHARED.format(what="Notifications", row="Notifications")
        now = data.clock()
        hours = max(1, min(int(args.get("hours") or 24), KEEP_NOTIFICATION_DAYS * 24))
        app = str(args.get("app") or "").strip().lower()
        q = str(args.get("query") or "").strip().lower()
        limit = max(1, min(int(args.get("limit") or 40), 200))
        hits = [n for n in notes if n["at"] >= now - hours * 3600 and (not app or app in f"{n['app']} {n['package']}".lower())
                and (not q or q in f"{n['title']} {n['text']}".lower())]
        lines = [f"Notifications forwarded by {p['device_name'] or 'a phone'}, last one {ago(now - p['uploaded_at'])}." for p in phones]
        if not hits:
            lines.append(f"No notifications in the last {hours} hours match." if (app or q) else f"No notifications in the last {hours} hours.")
            return "\n".join(lines)
        lines.append(f"Notifications ({getattr(tz(), 'key', 'UTC')}), newest first, {len(hits)}{' (first ' + str(limit) + ' shown)' if len(hits) > limit else ''}:")
        part = hits[:limit]
        both = await SHELF.mask_many([*((str(n["text"] or ""), str(n["app"] or n["package"]), str(n["title"])) for n in part),
                                      *((str(n["title"]), str(n["app"] or n["package"]), "") for n in part)], channel="text")   # one judge request
        shown, titles = both[:len(part)], both[len(part):]
        for n, title, text in zip(hits[:limit], titles, shown):
            lines.append(f"- {fmt(n['at'])} [{n['app'] or n['package']}] {title}{': ' + text if n['text'] else ''}")
        return "\n".join(lines)

    # ---- health
    async def phone_health(args: dict[str, Any]) -> str:
        snaps = data.snapshots("health")
        if not snaps:
            return NOT_SHARED.format(what="Health Connect data", row="Health Connect")
        zone = tz()
        try:
            start = _parse(args["start"], zone).date() if args.get("start") else (datetime.now(zone) - timedelta(days=6)).date()
            end = _parse(args["end"], zone).date() if args.get("end") else datetime.now(zone).date()
        except (ValueError, TypeError):
            return "error: give start and end as dates (2026-11-08)"
        if end < start:
            return "error: end is before start"
        want = [m for m in (args.get("metrics") or []) if m in HEALTH_TYPES] if isinstance(args.get("metrics"), list) else []
        s = snaps[0]   # the newest phone's summaries (one person's health)
        shared = s.get("types", [])
        lines = _freshness(snaps[:1], data.clock())
        lines.append(f"Shared from Health Connect: {', '.join(shared) or 'nothing'}.")
        missing = [m for m in want if m not in shared]
        if missing:
            lines.append(f"Not shared (the user did not tick it in the Kelo app): {', '.join(missing)}.")
        show = [m for m in (want or shared) if m in shared]
        days = [d for d in s.get("days", []) if start.isoformat() <= d["day"] <= end.isoformat()]
        if not days:
            lines.append(f"No data between {start} and {end}.")
            return "\n".join(lines)
        for d in days:
            parts = []
            if "steps" in show and "steps" in d:
                parts.append(f"{d['steps']:,} steps")
            if "sleep" in show and "sleep_minutes" in d:
                span = f" ({fmt(d['sleep_start'])[11:]}–{fmt(d['sleep_end'])[11:]})" if d.get("sleep_start") and d.get("sleep_end") else ""
                parts.append(f"slept {d['sleep_minutes'] // 60} h {d['sleep_minutes'] % 60} min{span}")
            if "heart_rate" in show and "heart_avg" in d:
                parts.append(f"heart rate avg {d['heart_avg']:.0f} (min {d.get('heart_min', 0):.0f}, max {d.get('heart_max', 0):.0f})"
                             + (f", resting {d['heart_resting']:.0f}" if d.get("heart_resting") else ""))
            if "weight" in show and "weight_kg" in d:
                parts.append(f"weight {d['weight_kg']} kg")
            if "exercise" in show and d.get("exercise"):
                parts.append("exercise: " + ", ".join(f"{e['title'] or e['type']} {e['minutes']} min" for e in d["exercise"]))
            if parts:
                lines.append(f"- {d['day']}: " + " · ".join(parts))
        return "\n".join(lines)

    who_desc = "a phone number, or a contact's name that matches one contact with one mobile number"
    when = "YYYY-MM-DD, the user's time zone"
    return [
        Tool("phone_contacts", "Search the contacts on the user's phone.",
             {"type": "object", "properties": {"query": {"type": "string", "description": "part of a name, number, email or company; empty: all"},
                                               "limit": {"type": "integer"}}},
             phone_contacts, read_only=True, untrusted=True, source="phone", trail="contacts"),
        Tool("phone_messages", "Read text messages on the user's phone, newest first (default: the last 30 days). One-time codes appear as "
             "'[verification code #N]': you never see the code; a browser sign-in uses it with find_login_code code_ref N.",
             {"type": "object", "properties": {"query": {"type": "string"},
                                               "with": {"type": "string", "description": "only the conversation with this number or contact"},
                                               "days": {"type": "integer"},
                                               "box": {"type": "string", "enum": ["inbox", "sent"]}, "limit": {"type": "integer"}}},
             phone_messages, read_only=True, untrusted=True, source="phone", trail="messages"),
        Tool("phone_send_text", "Send a text message from the user's phone; the user approves the recipient and the whole text first.",
             {"type": "object", "properties": {"to": {"type": "string", "description": who_desc},
                                               "text": {"type": "string", "description": "the complete message, in the user's voice"},
                                               "uses": USES_PARAM},
              "required": ["to", "text"]},
             phone_send_text, requires_approval=True, describe=describe_send, validate=check_send, approval_extra=send_lines,
             declined="The user declined to send this text. Do not send it or retry; ask what they would like instead."),
        Tool("phone_call", "Open the phone's dialer with a number filled in, after the user approves; the user presses call. Kelo never "
             "starts or hears a call.",
             {"type": "object", "properties": {"to": {"type": "string", "description": who_desc},
                                               "reason": {"type": "string", "description": "a few words, shown on the approval card"}},
              "required": ["to"]},
             phone_call, requires_approval=True, describe=describe_call, validate=check_call, approval_extra=call_lines),
        Tool("phone_call_log", "Read the call history on the user's phone, newest first (default: the last 7 days, up to 30).",
             {"type": "object", "properties": {"days": {"type": "integer"}, "query": {"type": "string"},
                                               "type": {"type": "string", "enum": list(CALL_TYPES)}, "limit": {"type": "integer"}}},
             phone_call_log, read_only=True, untrusted=True, source="phone", trail="device"),
        Tool("phone_notifications", "Search the notifications the user's phone received, newest first (default: the last 24 hours, up to 7 "
             "days).",
             {"type": "object", "properties": {"hours": {"type": "integer"}, "app": {"type": "string"}, "query": {"type": "string"},
                                               "limit": {"type": "integer"}}},
             phone_notifications, read_only=True, untrusted=True, source="phone", trail="device"),
        Tool("phone_health", "Read daily health summaries the user chose to share from their phone (default: the last 7 days).",
             {"type": "object", "properties": {"start": {"type": "string", "description": when}, "end": {"type": "string", "description": when},
                                               "metrics": {"type": "array", "items": {"type": "string", "enum": list(HEALTH_TYPES)}}}},
             phone_health, read_only=True, untrusted=True, source="phone", trail="device"),
    ]


# ---- writing to the phone's calendar (only when the user allowed it in the app; every change is approved on a card)
def phone_calendar_write_tools(data: PhoneData, cals: Any, timezone_name: Callable[[], str | None]) -> list[Tool]:
    """cals: the PhoneCalendars (phone_calendar.py) whose snapshots say which phone may be written to and which calendar."""
    from .phone_calendar import _when

    def tz() -> Any:
        return _zone(timezone_name())

    def writer() -> dict[str, Any] | None:
        return next((s for s in cals.snapshots() if s.get("can_write") and s.get("write_calendar")), None)

    def find(event_id: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        try:
            eid = int(event_id)
        except (TypeError, ValueError):
            return None, None
        for s in cals.snapshots():
            if not s.get("can_write"):
                continue
            for e in s["events"]:
                if e.get("id") == eid:
                    return s, e
        return None, None

    def times(args: dict[str, Any], base: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
        """start/end/all_day from the arguments (missing ones kept from base), or a problem."""
        zone = tz()
        out: dict[str, Any] = {}
        all_day = bool(args.get("all_day")) if "all_day" in args else bool(base and base.get("all_day"))
        try:
            if args.get("start"):
                st = _parse(args["start"], zone)
                if len(str(args["start"]).strip()) == 10 and "all_day" not in args:
                    all_day = True
            elif base:
                st = datetime.fromtimestamp(base["start"], zone)
            else:
                return {}, "give start: a date (2026-11-08) for an all-day event or a local time (2026-11-08T15:00)"
            if args.get("end"):
                en = _parse(args["end"], zone, end_of_day=all_day and len(str(args["end"]).strip()) == 10)
            elif base and not args.get("start"):
                en = datetime.fromtimestamp(base["end"], zone)
            else:
                en = st + (timedelta(days=1) if all_day else timedelta(hours=1))
        except (ValueError, TypeError):
            return {}, "give times as a date (2026-11-08) or a local time (2026-11-08T15:00)"
        if all_day:
            st = st.replace(hour=0, minute=0, second=0, microsecond=0)
            if en <= st:
                en = st + timedelta(days=1)
        if en < st:
            return {}, "end is before start"
        out.update(start=st.timestamp(), end=en.timestamp(), all_day=all_day)
        if all_day:
            out.update(day=st.date().isoformat(), last_day=(en - timedelta(seconds=1)).date().isoformat())
        return out, ""

    def when_text(ev: dict[str, Any]) -> str:
        return _when({**ev, "day": ev.get("day"), "last_day": ev.get("last_day")}, tz())

    # ---- add
    def check_add(args: dict[str, Any]) -> str | None:
        if writer() is None:
            return ("no phone allows Kelo to change its calendar. The user can turn on 'allow adding and changing events' in the Kelo app "
                    "(Connectors → On this device → Calendar)")
        if not str(args.get("title") or "").strip():
            return "give the event a title"
        return times(args)[1] or None

    async def describe_add(args: dict[str, Any]) -> str:
        ev, _ = times(args)
        return t("Add to the phone calendar: {title} · {when}", title=str(args.get("title") or "").strip()[:80], when=when_text(ev) if ev else "")

    def add_lines(_a: str, args: dict[str, Any]) -> dict[str, Any]:
        ev, _ = times(args)
        w = writer()
        lines = [{"label": t("Event"), "value": str(args.get("title") or "").strip()[:200]}, {"label": t("When"), "value": when_text(ev) if ev else ""}]
        if str(args.get("location") or "").strip():
            lines.append({"label": t("Place"), "value": str(args["location"]).strip()[:200]})
        if w:
            lines.append({"label": t("Calendar"), "value": f"{w['write_calendar'].get('name') or t('Phone calendar')} · {w['device_name'] or t('this phone')}"})
        return {"lines": lines[:4]}

    async def run(owner: str, kind: str, payload: dict[str, Any], what: str) -> str:
        task_id = data.queue(owner, kind, payload)
        done = await data.wait(task_id, timeout=30)
        if done and done["state"] == "done":
            return f"Done on the phone: {what}. The phone sends a fresh copy of its calendar now."
        if done and done["state"] == "failed":
            return f"The phone could not do it ({what}): {done['error'] or 'unknown reason'}. Tell the user; do not retry on your own."
        return f"Not done yet: the phone seems offline. It does it ({what}) if it comes online within an hour. Tell the user."

    async def phone_calendar_add(args: dict[str, Any]) -> str:
        w = writer()
        if w is None:
            return "error: no phone allows changing its calendar"
        ev, problem = times(args)
        if problem:
            return f"error: {problem}"
        title = str(args.get("title") or "").strip()[:300]
        payload = {"calendar_id": w["write_calendar"]["id"], "title": title, "location": str(args.get("location") or "").strip()[:300],
                   "description": str(args.get("notes") or "").strip()[:2000], "timezone": getattr(tz(), "key", "UTC"), **ev}
        return await run(w["owner"], "cal_add", payload, f"added {title!r} ({when_text(ev)}) to {w['write_calendar'].get('name') or 'the calendar'}")

    # ---- change
    def check_change(args: dict[str, Any]) -> str | None:
        if writer() is None:
            return "no phone allows Kelo to change its calendar (the user can allow it in the Kelo app)"
        s, e = find(args.get("event_id"))
        if e is None:
            return "no such event on a phone that allows changes; use phone_calendar_events to find the event id"
        if not any(k in args for k in ("title", "start", "end", "location", "all_day")):
            return "say what to change (title, start, end, location)"
        return times(args, e)[1] or None

    async def describe_change(args: dict[str, Any]) -> str:
        _, e = find(args.get("event_id"))
        if e is None:
            return ""
        ev, _ = times(args, e)
        return t("Change in the phone calendar: {title} · {when}", title=str(args.get("title") or e["title"])[:80], when=when_text(ev) if ev else "")

    def change_lines(_a: str, args: dict[str, Any]) -> dict[str, Any]:
        _, e = find(args.get("event_id"))
        if e is None:
            return {}
        ev, _ = times(args, e)
        lines = [{"label": t("Event"), "value": e["title"] + (f" → {str(args['title']).strip()}" if str(args.get("title") or "").strip() else "")},
                 {"label": t("Now"), "value": when_text(e)}]
        if ev and (ev["start"], ev["end"]) != (e["start"], e["end"]):
            lines.append({"label": t("New time"), "value": when_text(ev)})
        if "location" in args:
            lines.append({"label": t("Place"), "value": str(args.get("location") or "").strip()[:200] or t("(none)")})
        return {"lines": lines[:4]}

    async def phone_calendar_update(args: dict[str, Any]) -> str:
        s, e = find(args.get("event_id"))
        if s is None or e is None:
            return "error: no such event"
        ev, problem = times(args, e)
        if problem:
            return f"error: {problem}"
        payload: dict[str, Any] = {"event_id": e["id"], "title_was": e["title"]}
        if "title" in args:
            payload["title"] = str(args.get("title") or "").strip()[:300]
        if "location" in args:
            payload["location"] = str(args.get("location") or "").strip()[:300]
        if any(k in args for k in ("start", "end", "all_day")):
            payload.update(ev)
            payload["timezone"] = getattr(tz(), "key", "UTC")
        return await run(s["owner"], "cal_update", payload, f"changed {e['title']!r}")

    # ---- delete
    def check_delete(args: dict[str, Any]) -> str | None:
        _, e = find(args.get("event_id"))
        return None if e is not None else "no such event on a phone that allows changes; use phone_calendar_events to find the event id"

    async def describe_delete(args: dict[str, Any]) -> str:
        _, e = find(args.get("event_id"))
        return t("Delete from the phone calendar: {title} · {when}", title=e["title"][:80], when=when_text(e)) if e else ""

    def delete_lines(_a: str, args: dict[str, Any]) -> dict[str, Any]:
        s, e = find(args.get("event_id"))
        if e is None:
            return {}
        return {"lines": [{"label": t("Event"), "value": e["title"]}, {"label": t("When"), "value": when_text(e)},
                          {"label": t("Calendar"), "value": e.get("calendar") or t("Phone calendar")}]}

    async def phone_calendar_delete(args: dict[str, Any]) -> str:
        s, e = find(args.get("event_id"))
        if s is None or e is None:
            return "error: no such event"
        return await run(s["owner"], "cal_delete", {"event_id": e["id"], "title": e["title"]}, f"deleted {e['title']!r}")

    when = "YYYY-MM-DD (all day) or YYYY-MM-DDTHH:MM, the user's time zone"
    fields = {"title": {"type": "string"}, "start": {"type": "string", "description": when}, "end": {"type": "string", "description": when},
              "all_day": {"type": "boolean"}, "location": {"type": "string"}}
    return [
        Tool("phone_calendar_add", "Add an event to the calendar on the user's phone. When the user asked for it in the chat it runs at once; "
             "otherwise the user approves it on a card. Without end it lasts an hour (all-day: a day). For several changes of one request, call "
             "them together in one step: the user then sees one card.",
             {"type": "object", "properties": {**fields, "notes": {"type": "string"}}, "required": ["title", "start"]},
             phone_calendar_add, requires_approval=True, describe=describe_add, validate=check_add, approval_extra=add_lines,
             auto_approve=lambda a: owner_request(), shares_card=True),
        Tool("phone_calendar_update", "Change an event in the phone calendar, giving only what changes. When the user asked for it in the chat it "
             "runs at once; otherwise the user approves it on a card. For several changes of one request, call them together in one step.",
             {"type": "object", "properties": {"event_id": {"type": "integer", "description": "from phone_calendar_events"}, **fields}, "required": ["event_id"]},
             phone_calendar_update, requires_approval=True, describe=describe_change, validate=check_change, approval_extra=change_lines,
             auto_approve=lambda a: owner_request(), shares_card=True),
        Tool("phone_calendar_delete", "Delete an event from the phone calendar. When the user asked for it in the chat it runs at once; otherwise "
             "the user approves it on a card. For several deletions of one request, call them together in one step.",
             {"type": "object", "properties": {"event_id": {"type": "integer", "description": "from phone_calendar_events"}}, "required": ["event_id"]},
             phone_calendar_delete, requires_approval=True, describe=describe_delete, validate=check_delete, approval_extra=delete_lines,
             auto_approve=lambda a: owner_request(), shares_card=True),
    ]


def declared_items(kinds_on: Any) -> list[Any]:
    """The phone items the owner shares, as situation_reads.Declared (sid "phone/<kind>"), in kind order."""
    from .situation_reads import Declared, read_of   # lazy: situation_reads reads the connectors' definitions
    out = []
    for kind in sorted(SITUATION):
        if kind not in set(kinds_on):
            continue
        raw = SITUATION[kind]
        read = read_of(raw, where=f"phone/{kind}")
        if read is not None:
            out.append(Declared(sid=f"phone/{kind}", name_key="phone", label=read.label, item="", read=read, kind="phone"))
    return out
