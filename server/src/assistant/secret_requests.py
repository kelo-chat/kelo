"""Fill-in cards the agent asks for in the chat: a service key or token, a website login, a payment card, a payment PIN.

The agent asks with a tool (add_service_key, request_login, request_card). That creates one request here and shows it in the chat
right where it was asked (a `secret_request` turn event) as a fill-in card. The request is kept in the database, so it shows up
again after a reload, a lost connection or on another of the owner's devices (GET /api/secret-requests), until it is answered,
cancelled or a day has gone by.

The value itself never comes here: the app sends it straight to the vault (PUT /api/credentials/{name}/secret,
PUT /api/vault/{name}/login), the wallet (POST /api/wallet/cards) or the payment PINs (POST /api/payment-pins, encrypted on the phone),
and only the fact that it was saved reaches this module. So the
value is never in the chat transcript, the model's context, a log line or a push notification.

Entering and saving the value is the user's consent (the card says where it will be sent), so there is no Allow/Deny card first. The
answer is still written to the approval log (allowed_once when saved, denied when cancelled).

After [Save] the agent goes on by itself: the tool that asked waits for the answer while its reply is followed (ENTRY_WAIT_SECONDS),
and an answer that comes later starts a short reply that carries on (the server's `on_answer`). A [Cancel] is told to the agent in the
waiting tool's result or, when the reply had ended, once with the user's next message (`take_notes`), so it does not ask again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

log = logging.getLogger("uvicorn.error")

KINDS = ("key", "login", "card", "payment_pin")
STATES = ("open", "saved", "cancelled", "expired", "replaced", "covered")
KEEP_OPEN_SECONDS = 86400.0       # an entry nobody answers is gone after a day (the vault forgets its empty request at the same time)
KEEP_CLOSED_SECONDS = 7 * 86400.0  # answered requests are kept a week (what the agent was told), then deleted
STALE_TASK_SECONDS = 1800.0       # an open request whose task has ended expires after 30 minutes
TOOL_OF = {"key": "add_service_key", "login": "request_login", "card": "request_card", "payment_pin": "request_payment_pin"}


def is_app_host(host: str) -> bool:
    return isinstance(host, str) and host.startswith("app:")


def all_app_hosts(hosts: list[str]) -> bool:
    return bool(hosts) and all(is_app_host(h) for h in hosts)


def norm_hosts(hosts: list[str] | None) -> set[str]:
    return {h.lower().rstrip("/") for h in (hosts or []) if isinstance(h, str)}


def matches_rule1(
    existing_row: dict[str, Any],
    *,
    kind: str,
    name: str = "",
    hosts: list[str] | None = None,
    task_id: int | None = None,
    turn_id: str = "",
    check_task_turn: bool = True,
) -> bool:
    """Rule 1: an older open request of the same kind matches when ANY of:
    - same name
    - hosts overlap
    - both requests' hosts are all app: hosts
    - same task_id (non-null) or same turn_id (non-empty) [skipped on save covering]
    """
    if existing_row.get("kind") != kind:
        return False
    ex_name = existing_row.get("name") or ""
    if name and ex_name == name:
        return True
    if not name and not ex_name and kind in ("card", "payment_pin"):
        return True
    ex_hosts = json.loads(existing_row.get("hosts") or "[]")
    new_hosts = list(hosts or [])
    if norm_hosts(ex_hosts) & norm_hosts(new_hosts):
        return True
    if all_app_hosts(ex_hosts) and all_app_hosts(new_hosts):
        return True
    if check_task_turn:
        ex_task_id = existing_row.get("task_id")
        if task_id is not None and ex_task_id is not None and ex_task_id == task_id:
            return True
        ex_turn_id = str(existing_row.get("turn_id") or "")
        if turn_id and ex_turn_id and ex_turn_id == turn_id:
            return True
    return False

SCHEMA = """
CREATE TABLE IF NOT EXISTS secret_requests (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    hosts TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    check_url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent',
    turn_id TEXT NOT NULL DEFAULT '',
    task_id INTEGER,
    worker_id TEXT NOT NULL DEFAULT '',
    run_source TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    after_id INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'open',
    result TEXT NOT NULL DEFAULT '{}',
    told INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    answered_at REAL
);
CREATE INDEX IF NOT EXISTS secret_requests_state ON secret_requests(state, created_at);
"""

REPLY_STATES = ("live", "started", "queued", "none")   # what an answer to a fill-in card leads to (answer_full)


class SecretRequests:
    """`log_consent(request, decision)`: writes the answer to the approval log. `on_answer(request) -> str | None`: an answer nobody was
    waiting for (the reply had ended); returns the id of a reply it started, if any."""

    def __init__(self, store: Any, *, clock: Callable[[], float] = time.time,
                 log_consent: Callable[[dict[str, Any], str], None] | None = None,
                 on_answer: Callable[[dict[str, Any]], str | None] | None = None):
        self.store, self.clock = store, clock
        self.log_consent, self.on_answer = log_consent, on_answer
        self.on_open: Callable[[dict[str, Any]], None] | None = None   # a new agent-asked card (not a login) opened: gets its request row (server: followups)
        store.secret_requests = self
        if not hasattr(store, "credential_listeners"):
            store.credential_listeners = []
        store.credential_listeners.append(self.login_checked)   # how a saved login's sign-in went (sign_in in public)
        self._waiters: dict[str, list[asyncio.Future[str]]] = {}
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    # ------------------------------------------------------------------ reading
    def _row(self, rid: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM secret_requests WHERE id = ?", (rid,))
        return rows[0] if rows else None

    def public(self, r: dict[str, Any]) -> dict[str, Any]:
        """What the apps get: what it is for, where it will be sent, its state. Never a value (there is none here)."""
        result = json.loads(r["result"] or "{}")
        out = {"id": r["id"], "kind": r["kind"], "name": r["name"], "label": r["label"], "hosts": json.loads(r["hosts"] or "[]"),
               "reason": r["reason"], "check": bool(r["check_url"]), "source": r["source"], "turn_id": r["turn_id"] or None,
               "task_id": r["task_id"], "after_id": int(r["after_id"] or 0), "state": r["state"], "created_at": r["created_at"],
               "answered_at": r["answered_at"], "expires_at": r["created_at"] + KEEP_OPEN_SECONDS}
        if r["state"] == "saved":
            out["result"] = {k: v for k, v in result.items() if k in ("checked", "mode", "card", "pin")}
            if r["kind"] == "login":
                # SIGN-IN STATE of a saved login (2026-11-10): "checking" from the save until the browser's sign-in with it tells how it
                # went: "signed_in", "failed" (the site rejected it), "code_needed" (a verification step is still open) or another
                # outcome word. Saved is not signed in: the apps word "checking" as such, never as "signed in".
                out["sign_in"] = str(result.get("sign_in") or "checking")
        return out

    def login_checked(self, name: str, outcome: str | None) -> None:
        """The browser's sign-in with login `name` ended (Store.set_credential_result): its newest saved request learns how."""
        if not outcome:
            return
        rows = self.store._all("SELECT id, result FROM secret_requests WHERE kind = 'login' AND name = ? AND state = 'saved' "
                               "ORDER BY answered_at DESC LIMIT 1", (name,))
        if not rows:
            return
        result = json.loads(rows[0]["result"] or "{}")
        result["sign_in"] = {"ok": "signed_in", "rejected": "failed"}.get(outcome, outcome)
        self.store._run("UPDATE secret_requests SET result = ? WHERE id = ?", (json.dumps(result), rows[0]["id"]))

    def get(self, rid: str) -> dict[str, Any] | None:
        r = self._row(rid)
        return self.public(r) if r else None

    def pending(self) -> list[dict[str, Any]]:
        """Open requests, oldest first, each with `message_id`: the agent message it belongs under (the first one stored after it was
        asked), or null while that reply is still being written."""
        self.sweep()
        rows = self.store._all("SELECT * FROM secret_requests WHERE state = 'open' ORDER BY created_at")
        out = []
        for r in rows:
            p = self.public(r)
            m = self.store._all("SELECT MIN(id) AS m FROM messages WHERE role = 'assistant' AND id > ?", (p["after_id"],))
            p["message_id"] = m[0]["m"] if m and m[0]["m"] else None
            out.append(p)
        return out

    def open_count(self) -> int:
        return int(self.store._all("SELECT COUNT(*) AS n FROM secret_requests WHERE state = 'open' AND created_at >= ?",
                                   (self.clock() - KEEP_OPEN_SECONDS,))[0]["n"])

    def open_kinds(self) -> dict[str, int]:
        """How many open requests of each kind (key, login, card, payment_pin): the apps' badge says what waits ("1 key to enter")."""
        rows = self.store._all("SELECT kind, COUNT(*) AS n FROM secret_requests WHERE state = 'open' AND created_at >= ? GROUP BY kind",
                               (self.clock() - KEEP_OPEN_SECONDS,))
        return {r["kind"]: int(r["n"]) for r in rows}

    def open_for(self, kind: str, name: str = "") -> dict[str, Any] | None:
        """The open request for one vault item (or the oldest open card request when `name` is empty)."""
        rows = self.store._all("SELECT * FROM secret_requests WHERE state = 'open' AND kind = ? AND name = ? AND created_at >= ? "
                               "ORDER BY created_at DESC LIMIT 1", (kind, name, self.clock() - KEEP_OPEN_SECONDS))
        return rows[0] if rows else None

    def check_url(self, rid: str) -> str:
        r = self._row(rid)
        return r["check_url"] if r else ""

    def sweep(self) -> None:
        """Open requests older than a day expire; stale requests (> 30m with ended task) expire; answered ones older than a week are deleted."""
        now = self.clock()
        has_tasks = bool(self.store._all("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"))
        if has_tasks:
            for r in self.store._all(
                "SELECT r.id FROM secret_requests r JOIN tasks t ON r.task_id = t.id "
                "WHERE r.state = 'open' AND r.created_at < ? AND t.status IN ('done', 'failed', 'stopped')",
                (now - STALE_TASK_SECONDS,),
            ):
                self._close(r["id"], "expired", {})
        for r in self.store._all("SELECT id FROM secret_requests WHERE state = 'open' AND created_at < ?", (now - KEEP_OPEN_SECONDS,)):
            self._close(r["id"], "expired", {})
        self.store._run("DELETE FROM secret_requests WHERE state != 'open' AND COALESCE(answered_at, created_at) < ?",
                        (now - KEEP_CLOSED_SECONDS,))

    # ------------------------------------------------------------------ asking
    def create(self, kind: str, *, name: str = "", label: str = "", hosts: list[str] | None = None, reason: str = "",
               check_url: str = "", source: str = "agent", turn: dict[str, Any] | None = None, worker_id: str = "",
               owner: str = "") -> dict[str, Any]:
        """A new open request. An older open one for the same item is replaced (its card folds); its waiter gets "replaced"."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        turn = turn or {}
        task_id = turn.get("task_id")
        turn_id = str(turn.get("turn_id") or "")
        with self.store._lock:
            for old in self.store._all("SELECT * FROM secret_requests WHERE state = 'open' AND kind = ?", (kind,)):
                if matches_rule1(old, kind=kind, name=name, hosts=hosts, task_id=task_id, turn_id=turn_id):
                    self._close(old["id"], "replaced", {}, told=True)
            after = self.store._all("SELECT COALESCE(MAX(id), 0) AS m FROM messages")[0]["m"]
            rid = "sr_" + uuid.uuid4().hex[:16]
            self.store._run("INSERT INTO secret_requests(id, kind, name, label, hosts, reason, check_url, source, turn_id, task_id, worker_id, "
                            "run_source, owner, after_id, state, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?)",
                            (rid, kind, name, label[:80], json.dumps(list(hosts or [])), reason[:300], check_url, source,
                             str(turn.get("turn_id") or ""), turn.get("task_id"), worker_id, str(turn.get("source") or ""), owner,
                             int(after or 0), self.clock()))
        if source == "agent" and kind != "login" and self.on_open is not None:   # a login has its own follow-up item (followups.py)
            try:
                self.on_open(self._row(rid) or {})
            except Exception:  # noqa: BLE001 - a follow-up that could not be made never fails the card
                log.exception("secret request %s: on_open failed", rid)
        return self.get(rid) or {}

    def find_open_matching(
        self,
        kind: str,
        *,
        name: str = "",
        hosts: list[str] | None = None,
        task_id: int | None = None,
        turn_id: str = "",
        max_age: float | None = None,
    ) -> dict[str, Any] | None:
        """Find an open request of the same kind matching Rule 1, optionally younger than max_age seconds."""
        now = self.clock()
        with self.store._lock:
            rows = self.store._all(
                "SELECT * FROM secret_requests WHERE state = 'open' AND kind = ? ORDER BY created_at DESC",
                (kind,),
            )
            for r in rows:
                if max_age is not None and (now - float(r.get("created_at") or 0)) >= max_age:
                    continue
                if matches_rule1(r, kind=kind, name=name, hosts=hosts, task_id=task_id, turn_id=turn_id):
                    return r
        return None

    def ensure_draft(self, kind: str, name: str, label: str, hosts: list[str]) -> None:
        """A vault item waiting for its value that no request stands for (made before fill-in cards existed, or started by the
        user): an open request of source "draft" so the Keychain lists it under "waiting for input". Never shown in the chat."""
        if not self.open_for(kind, name):
            self.create(kind, name=name, label=label, hosts=hosts, source="draft")

    async def wait(self, rid: str, timeout: float) -> str:
        """The request's state once answered, or "open" when `timeout` ran out first."""
        r = self._row(rid)
        if r is None:
            return "expired"
        if r["state"] != "open":
            return r["state"]
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(rid, []).append(fut)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            return "open"
        finally:
            waiters = self._waiters.get(rid)
            if waiters is not None:
                if fut in waiters:
                    waiters.remove(fut)
                if not waiters:
                    self._waiters.pop(rid, None)

    def waiting(self, rid: str) -> bool:
        return bool(self._waiters.get(rid))

    # ------------------------------------------------------------------ answering
    def _close(self, rid: str, state: str, result: dict[str, Any], told: bool = False) -> bool:
        cur = self.store._run("UPDATE secret_requests SET state = ?, result = ?, answered_at = ?, told = ? WHERE id = ? AND state = 'open'",
                              (state, json.dumps(result), self.clock(), int(told), rid))
        if cur.rowcount <= 0:
            return False
        for fut in list(self._waiters.get(rid, [])):
            if not fut.done():
                fut.set_result(state)
        if state == "saved":
            row = self._row(rid)
            if row and row.get("kind") == "login":
                self._close_covered_logins(row)
        return True

    def _close_covered_logins(self, saved_row: dict[str, Any]) -> None:
        saved_id = saved_row["id"]
        saved_name = saved_row.get("name") or ""
        saved_hosts = json.loads(saved_row.get("hosts") or "[]")
        open_rows = self.store._all(
            "SELECT * FROM secret_requests WHERE state = 'open' AND kind = 'login' AND id != ?",
            (saved_id,),
        )
        mode = str((json.loads(saved_row.get("result") or "{}") or {}).get("mode") or "now")
        for other in open_rows:
            if matches_rule1(other, kind="login", name=saved_name, hosts=saved_hosts, check_task_turn=False):
                self._close(other["id"], "covered", {"covered_by": saved_name, "mode": mode}, told=True)

    def covered_by(self, rid: str) -> dict[str, Any]:
        """For a request closed "covered": {"covered_by": the saved login's name, "mode": how it was saved}; {} otherwise."""
        r = self._row(rid)
        if r is None or r["state"] != "covered":
            return {}
        return json.loads(r["result"] or "{}")

    def answer(self, rid: str, state: str, result: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
        """Close an open request as "saved" or "cancelled". Returns (the request, the id of a reply started to carry on, if any).
        A request that was not open any more is returned as it is, and nothing else happens."""
        got = self.answer_full(rid, state, result)
        return got["request"], got["turn_id"]

    def answer_full(self, rid: str, state: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        """answer() with what happens next, for the apps: {"request", "turn_id", "reply"}. reply (REPLY_STATES):
          live     the reply that asked was waiting and goes on now with the answer
          started  a new reply started to carry on (turn_id: follow it with GET /api/turns/{id})
          queued   the work goes on later by itself (a background job resumed; a chat reply once the running one ends)
          none     nothing is coming (a cancel told with the next message, a scheduled run's next run, no model, not open)"""
        if state not in ("saved", "cancelled"):
            raise ValueError(state)
        r = self._row(rid)
        if r is None:
            return {"request": None, "turn_id": None, "reply": "none"}
        live = self.waiting(rid)
        if not self._close(rid, state, result or {}, told=live):
            return {"request": self.public(r), "turn_id": None, "reply": "none"}
        req = self.get(rid) or {}
        if self.log_consent is not None and r["source"] == "agent":
            try:
                self.log_consent(req, "allowed_once" if state == "saved" else "denied")
            except Exception:  # noqa: BLE001 - the record must never stop the answer
                pass
        started, reply = None, "live" if live else "none"
        if not live and r["source"] == "agent" and self.on_answer is not None:
            try:
                got = self.on_answer(req)   # a turn id, or (reply, turn id)
            except Exception:  # noqa: BLE001
                got = None
            if isinstance(got, tuple):
                reply, started = got
            elif got:
                reply, started = "started", got
        return {"request": req, "turn_id": started, "reply": reply}

    def saved(self, kind: str, name: str = "", result: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
        """The vault item `name` got its value (from the chat's entry, the Keychain page, or anywhere else): its open request is done."""
        got = self.saved_full(kind, name, result)
        return got["request"], got["turn_id"]

    def saved_full(self, kind: str, name: str = "", result: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.open_for(kind, name)
        if r is None:
            return {"request": None, "turn_id": None, "reply": "none"}
        return self.answer_full(r["id"], "saved", result)

    def cancel_for(self, kind: str, name: str) -> dict[str, Any] | None:
        r = self.open_for(kind, name)
        return self.answer(r["id"], "cancelled")[0] if r else None

    def cancel_for_full(self, kind: str, name: str) -> dict[str, Any]:
        r = self.open_for(kind, name)
        return self.answer_full(r["id"], "cancelled") if r else {"request": None, "turn_id": None, "reply": "none"}

    def mark_told(self, rid: str) -> None:
        self.store._run("UPDATE secret_requests SET told = 1 WHERE id = ?", (rid,))

    def take_notes(self) -> str:
        """Once, with the user's next message: entries they closed without saving after the reply that asked had ended."""
        rows = self.store._all("SELECT * FROM secret_requests WHERE state = 'cancelled' AND told = 0 AND source = 'agent' "
                               "AND answered_at >= ? ORDER BY answered_at", (self.clock() - KEEP_OPEN_SECONDS,))
        if not rows:
            return ""
        for r in rows:
            self.mark_told(r["id"])
        what = "; ".join(f"{describe(r['kind'])} for {r['label'] or r['name']}" for r in rows)
        return (f"[Not from the user: they closed the fill-in card you asked for ({what}) without saving it. Do not ask for it again "
                "unless they bring it up.]")


# Set by the server for each run: (the requests, `wait(request) -> final state` or None). `wait` is there while a followed reply runs:
# the tool that asked then waits for the user's answer ("saved" | "cancelled", or "open" when it ran out) and goes on in the same
# reply. None (a background run): the tool returns at once and an answer that comes later is handled by `on_answer`. Unset (tests
# without the app, the CLI): nothing is shown.
entry_context: ContextVar[tuple[SecretRequests, Callable[[dict[str, Any]], Awaitable[str]] | None] | None] = ContextVar(
    "entry_context", default=None)


def describe(kind: str) -> str:
    return {"key": "the service key", "login": "the website login", "card": "a payment card",
            "payment_pin": "a payment PIN"}.get(kind, "the entry")


async def ask(kind: str, *, name: str = "", label: str = "", hosts: list[str] | None = None, reason: str = "",
              check_url: str = "") -> tuple[dict[str, Any] | None, str]:
    """Open a fill-in card in the chat of the run that is going, and wait for the answer while that reply is followed.
    Returns (the request, its state: "saved" | "cancelled" | "open" (not answered yet) | "replaced" | "expired"). Outside the app's
    runs (tests, the CLI) nothing is shown: (None, "open")."""
    ctx = entry_context.get()
    if ctx is None:
        return None, "open"
    entries, waiter = ctx
    from .agent import emit_ui
    from .tools.browser import browser_owner
    from .workers import current_turn, current_worker

    turn = current_turn.get() or {}
    req = entries.create(kind, name=name, label=label, hosts=hosts, reason=reason, check_url=check_url, turn=turn,
                         worker_id=current_worker.get(), owner=browser_owner.get() or "")
    await emit_ui({"type": "secret_request", "request": req})
    if waiter is None:
        return req, "open"
    state = await waiter(req)
    now = entries.get(req["id"]) or req
    if state != "open":
        await emit_ui({"type": "secret_request", "request": now})   # the card folds in the reply it belongs to
    return now, state


async def check_key(cred: dict[str, Any], secret: str, check_url: str, fetcher: Callable[..., Awaitable[Any]]) -> tuple[str, str]:
    """Try a key the user is saving with one read-only request before it is stored: ("ok" | "rejected" | "unchecked", detail).
    The check is the one a known service has (presets.KEYS: the same check its own connect form makes), else the `check_url` the agent
    gave when it asked (a "who am I" GET on one of the key's hosts). Only a clear refusal (HTTP 401/403, or {"ok": false}) stops the
    save; a check that cannot say (another status, no answer) lets it through as not checked."""
    from .credentials import apply_secret
    from .netguard import NetGuardError
    from .presets import KEYS

    hosts, placement = list(cred.get("hosts") or []), dict(cred.get("placement") or {})
    preset = next((p for p in KEYS.values() if p.item == cred.get("name") and set(p.hosts) == set(hosts)), None)
    url, json_ok = (preset.verify.format(store=hosts[0] if hosts else ""), preset.verify_json_ok) if preset and preset.verify else (check_url, True)
    if not url or not hosts:
        return "unchecked", ""
    try:
        target, auth = apply_secret(url, {k: v for k, v in placement.items() if k != "fixed_headers"}, secret)
        reply = await fetcher("GET", target, allowed_hosts=hosts, headers={"Accept": "application/json", **placement.get("fixed_headers", {}), **auth})
    except NetGuardError:
        return "unchecked", "no answer"
    status = int(getattr(reply, "status", 0) or 0)
    if status in (401, 403):
        return "rejected", f"HTTP {status}"
    if 200 <= status < 300:
        if json_ok:
            try:
                body = json.loads(reply.body)
            except (ValueError, TypeError):
                body = None
            if isinstance(body, dict) and body.get("ok") is False:
                return "rejected", f"HTTP {status}"
        return "ok", f"HTTP {status}"
    return "unchecked", f"HTTP {status}"
