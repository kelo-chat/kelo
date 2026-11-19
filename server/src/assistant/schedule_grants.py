"""What the user allowed ONCE, when a "when X happens, do Y" task was set up, so that it can act when X happens without a card.

A conditional task runs while nobody watches: a schedule (a repeating job, or one that polls for a condition), a watcher (a cheap
check that wakes the agent when its condition holds) or a background job. Whatever mechanism the agent picks, the approval process is
the same. The agent asks ONE question at setup time (the `task_allow` tool, one approval card) that names the trigger in plain words,
exactly what will be done, where / to whom, and the limits. What the user allows is kept here, scoped to that task ("schedule:<id>",
"watcher:<id>" or "job:<id>"; a job a task hands work to with `delegate` works under its parent's grants too):

- login:    that saved login (by name), on that site (its registrable domain). A run of that task types it with browser_login without a
            card. A chat or another task still follows the login's own setting ("asks each time" by default).
- purchase: that site, what is bought (the user's words), a most per time and a currency, a most number of purchases (`max_count`, 1
            for a one-shot task; 0 = no count, for a repeating job) and a monthly cap (given, or the most per time x the count, or for
            a repeating schedule x its runs in a month), optionally the payment card. A run completes a purchase there without a card
            when the total is within all of them, the item is the same kind (a word of `what` is in the order's summary) and the payment
            way does not need the phone (an end-to-end card without a server copy always does). It stands in for the wallet's
            first-payment-on-a-site rule and the card's per-payment auto-pay limit, only for that task and only inside its own limits.
            The wallet's pause and the vault lock still stop it. One covered purchase per run: a second payment or a changed total in
            the same run asks.
- send:     messages to the named recipients (email addresses, phone numbers or contact names) and/or posts on one site, at most
            `max_count` times, optionally following a template (its fixed words must be in the message; the parts in {braces} vary).
- change:   changes to what was named (the calendar, a connected service, a site), at most `max_count` times; deleting only when
            `allow_delete` was allowed.
- apply:    applications on one site (its registrable domain): what is applied for (the user's words, e.g. "선물 체험단"), at most
            `max_count` per run and optionally `total_count` over all runs ("매일 체험단 확인하고 신청해"). A click the browser judges
            "apply" (tools/commits.py) on that site is one application; a "form" click that follows it in the same run and typing the
            form's details there belong to it (not counted; a "form" click before any application counts as one). Password fields,
            sign-ups and account steps, payments and other sites are never covered. The user gets ONE notice after the run with
            what was applied for ("N건 신청했어요"), not one per click.

Never covered: account or password changes, sign-ups, another site / recipient / card, another task, a total over the cap, a deletion
that was not allowed, anything after the count is used up. Those stop and ask with a card; when the task was marked urgent (a cancelled
ticket, a limited offer) the card's notification is urgent and says what will be missed if nobody answers.

A one-shot task (`once`) ends when the granted count of a counted grant is used: the schedule or watcher is turned off at once (no second
trigger), the user is told, and its grants are removed when the run that used them ends, so nothing is bought or sent twice. Every
covered step is written to the approval log as allowed (on_use), and the user is notified right after it (a purchase: the payment
notification; a message or a change: a "done" notification with what was done).

Deleting the task removes its grants (schedule, watcher, job ended); so does deleting the login (its login grants). Each grant can be
revoked on the task's page (DELETE /api/schedules/{id}/grants/{gid}, /api/watchers/{id}/grants/{gid}, /api/workers/{id}/grants/{gid},
/api/task-grants/{gid}) and a login grant also on the login's page in the vault.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .agent import Tool, approval_of_current_call
from .connector_rules import SEP
from .effects import GRANTABLE, effect_of, norm_target  # noqa: F401  (norm_target: re-exported, other modules take it from here)
from .i18n import N_, t
from .wallet import CURRENCY, money_text, site_key, site_keys

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL, kind TEXT NOT NULL, login TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL, what TEXT NOT NULL DEFAULT '', max_amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT '',
    monthly_cap REAL NOT NULL DEFAULT 0, runs_per_month INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
    from_approval TEXT NOT NULL DEFAULT '', uses INTEGER NOT NULL DEFAULT 0, last_used REAL);
CREATE INDEX IF NOT EXISTS schedule_grants_by_schedule ON schedule_grants(schedule_id);
"""
# columns added after the first version (schedules only, login and purchase only)
COLUMNS = (("shipping_id", "TEXT NOT NULL DEFAULT ''"), ("scope", "TEXT NOT NULL DEFAULT ''"), ("trigger_text", "TEXT NOT NULL DEFAULT ''"),
           ("action_text", "TEXT NOT NULL DEFAULT ''"), ("max_count", "INTEGER NOT NULL DEFAULT 0"), ("card_id", "TEXT NOT NULL DEFAULT ''"),
           ("targets", "TEXT NOT NULL DEFAULT ''"), ("template", "TEXT NOT NULL DEFAULT ''"), ("varies", "TEXT NOT NULL DEFAULT ''"),
           ("allow_delete", "INTEGER NOT NULL DEFAULT 0"), ("once", "INTEGER NOT NULL DEFAULT 0"), ("urgent", "INTEGER NOT NULL DEFAULT 0"),
           ("missed", "TEXT NOT NULL DEFAULT ''"), ("use_pin", "INTEGER NOT NULL DEFAULT 0"),
           ("total_count", "INTEGER NOT NULL DEFAULT 0"))
KINDS = ("login", "purchase", "send", "change", "apply")
COUNTED = ("purchase", "send", "change", "apply")
CLICK_TOOLS = ("browser_click", "browser_click_at", "browser_drag")   # a point step counts as a click on the element read there
APPLY_STEPS = ("apply", "form", "personal")   # browser commits an apply permission covers on its site (password fields never)
SCOPES = ("schedule", "watcher", "job")
MAX_WHAT = 80
MAX_WORDS = 300
MAX_TARGETS = 10
MAX_COUNT = 100
ACCOUNT_REFUSED = "a standing permission never covers sign-ups, account or password changes"
NOT_CHECKED = ("a standing permission could not be checked (the model that reads what it covers did not answer). Tell the user, and ask "
               "again later; nothing was allowed")

LOGIN_COVERED = N_("Allowed at setup: signing in with {login} on {site}")
PURCHASE_COVERED = N_("Allowed at setup: buying {what} on {site} ({amount})")
ACTION_COVERED = N_("Allowed at setup: {action} ({used}/{count})")
ACTION_COVERED_FREE = N_("Allowed at setup: {action}")
APPLY_COVERED = N_("apply for {what} on {site}")
APPLIED_TITLE = N_("{count} application(s) sent")
PROGRESS_TITLE = N_("{count} application(s) sent so far, still going")   # a long run's one progress notice (run_progress.py)
PROGRESS_NOTICE = N_("Still working on it: {step} ({count} done)")       # any long run still working after 10 min (run_progress.py)
ACK_LINE = N_("Got it, on it now")                                       # code-level acknowledgement before slow work (turn_engine.py)

# why a run's purchase is NOT covered although its task has a purchase permission (the approval card and the model say this)
MISSES = {
    "task_other_site": "this task may buy only on {site}; this payment is on another site",
    "task_currency": "this task's permission is in {currency}; this payment is in another currency",
    "task_over_cap": "the total is over the most the user allowed per time for this task ({amount})",
    "task_over_monthly": "this task has used up the monthly cap the user allowed ({amount})",
    "task_other_item": "this task may buy only '{what}'; this order looks like something else",
    "task_again": ("this run already made the one purchase the permission covers; another payment or a changed total in the same run "
                   "needs the user's approval"),
    "task_used_up": "this task already made the {count} purchase(s) the user allowed; another one needs the user's approval",
    "task_other_card": "this task may pay only with the card '{card}'; this payment uses another card",
}
# why a message or a change is NOT covered although its task has such a permission
ACTION_MISSES = {
    "task_other_target": "the user allowed this task to {kind} only to/at: {targets}; this goes elsewhere",
    "task_other_text": "the message does not follow the wording the user allowed ({template})",
    "task_no_delete": "the user allowed this task to change things, but not to delete or cancel anything",
    "task_used_up": "this task already used the {count} time(s) the user allowed; another one needs the user's approval",
    "task_account": "account, password and sign-up steps are never covered by a standing permission",
    "task_fresh_only": "this method of the service always needs the user's own approval; no task permission covers it",
    "task_other_site": "the user allowed this task to apply only on {site}; this is another site",
    "task_run_used_up": ("this run already sent the {count} application(s) the user allowed per run; another one needs the user's "
                         "approval"),
}

# the same, in the user's words, for the approval card's "why it asks" line
ACTION_REASONS = {
    "task_other_target": N_("Not a recipient or place allowed at setup"),
    "task_other_text": N_("The message differs from the wording allowed at setup"),
    "task_no_delete": N_("Deleting was not allowed at setup"),
    "task_used_up": N_("The number of times allowed at setup is used up"),
    "task_account": N_("Account and sign-up steps always ask"),
    "task_fresh_only": N_("This action always asks, whatever was allowed at setup"),
    "task_other_site": N_("Not a recipient or place allowed at setup"),
    "task_run_used_up": N_("The number of times allowed at setup is used up"),
}


def _words(text: str) -> set[str]:
    return {w.casefold() for w in re.findall(r"[^\W\d_]{2,}", text or "")}


def words_of(text: str) -> set[str]:
    """The words a permission's `what` is compared by (public for buy_replay.py)."""
    return _words(text)


def _host(value: str) -> str:
    text = (value or "").strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    return text.split("/", 1)[0].split(":", 1)[0]


def _site(value: str) -> str:
    return site_key(_host(value))


def _sites(value: str) -> list[str]:
    """The keys a saved permission of this site may have (the list's, and the pre-2026-11-10 rule's: wallet.site_keys)."""
    return site_keys(_host(value))


def _clip(value: Any, n: int = MAX_WORDS) -> str:
    return " ".join(str(value or "").split())[:n] if isinstance(value, (str, int, float)) else ""


def change_target(value: str) -> str:
    """A change permission's place: "calendar", a site, or else a connected service by name ("connector:<name>")."""
    n = norm_target(value)
    if n and not (n == "calendar" or n.startswith(("site:", "connector:")) or "@" in n or n.isdigit()):
        n = "connector:" + n
    return n


def template_ok(template: str, text: str) -> bool:
    """The message keeps the template's fixed words (the parts outside {braces}); a template without braces must be in it whole."""
    if not template.strip():
        return True
    body = " ".join((text or "").split()).casefold()
    parts = [" ".join(p.split()).casefold() for p in re.split(r"\{[^}]*\}", template)]
    return all(p in body for p in parts if len(p.strip(" .,!?~-:;")) >= 2)


@dataclass
class PurchaseCheck:
    grant: dict[str, Any] | None   # the covering grant, or None
    reason: str = ""               # "" = covered or no purchase grant at all; else a MISSES key
    note: str = ""                 # the reason in words, for the model


@dataclass
class ActionCheck:
    grant: dict[str, Any] | None
    reason: str = ""
    note: str = ""
    kind: str = ""                 # send | change


def action_of(tool: str, args: dict[str, Any], site: str = "", commit: str = "") -> tuple[str, list[str], bool, str] | None:
    """What a step does, as a standing permission sees it: (kind send|change, its targets, whether it deletes, the message text), or
    None for a step no send/change permission can cover (payments, sign-ups and account steps, code, installs, reads, ...). A thin view
    of effects.effect_of, which knows what every tool does; only the tools a permission has always covered are viewed."""
    if tool not in GRANTABLE:
        return None
    e = effect_of(tool, args, site=site, commit=commit)
    if e is None:
        return None
    if tool == "mcp_call_tool":   # a standing permission has always seen every MCP call as a change; a delete is the effect's kind (hints, manifest, tool judge)
        return "change", e.targets, e.kind == "delete", ""
    if tool == "connector_send":
        return "change", e.targets, e.kind == "delete", ""
    if e.kind == "read":
        return None
    if tool == "email_update" and e.kind != "delete":
        return None   # only moving to the trash ever asks (the rest is easy to undo): a deleting change of the mailbox
    return ("send" if e.kind == "send" else "change"), e.targets, e.kind == "delete", e.text


def _target_ok(want: str, have: set[str]) -> bool:
    if want in have:
        return True
    if want.startswith(("calendar:", "event:")) and "calendar" in have:   # a grant for the calendars covers every calendar and event
        return True
    if want.startswith("site:") and any(f"site:{k}" in have for k in site_keys(want[5:])):   # saved under the pre-2026-11-10 rule
        return True
    if want.startswith("connector:") and "/" in want and want.split("/", 1)[0] in have:   # a connector named whole covers its tools
        return True
    if want.startswith("connector:") and SEP in want and want.split(SEP, 1)[0] in have:   # ... and every recipient bound to it
        return True
    return False


def current_scopes(job_parent: dict[str, str] | None = None) -> list[str]:
    """The conditional task(s) the running turn belongs to: its watcher ("watcher:<id>", set on the turn a watcher's hit starts), its
    schedule ("schedule:<id>"), its background job ("job:<id>") and the task that job was handed off from. [] in a plain chat."""
    from .workers import current_turn, current_worker
    turn = current_turn.get() or {}
    out: list[str] = []
    if turn.get("cond"):
        out.append(str(turn["cond"]))
    src = str(turn.get("source") or "")
    if src.startswith("schedule:") and src[9:].isdigit():
        out.append(src)
    wid = current_worker.get()
    if wid:
        out.append(f"job:{wid}")
        if job_parent and job_parent.get(wid):
            out.append(job_parent[wid])
    return list(dict.fromkeys(out))


def current_schedule_id() -> int | None:
    from .schedules import current_schedule_id as cur
    return cur()


def _run_key() -> str:
    """The run going on (its task), so that a standing permission covers one purchase per run: a second payment or a changed total
    in the same run asks."""
    from .workers import current_turn
    turn = current_turn.get() or {}
    return str(turn.get("task_id") or turn.get("turn_id") or "")


def unattended_now() -> bool:
    from .workers import current_turn, current_worker
    return bool((current_turn.get() or {}).get("unattended") or current_worker.get())


class ConditionalGrants:
    def __init__(self, store: Any, schedules: Any = None, wallet: Any = None, clock: Callable[[], float] = time.time):
        self.store, self.schedules, self.wallet, self.clock = store, schedules, wallet, clock
        self.watchers: Any = None   # watchers.Watchers, to name a watcher's grants
        self.workers: Any = None    # workers.Workers, to name a job's grants
        self.shipping: Any = None   # shipping.ShippingBook, to name a purchase permission's address
        self.judge: Any = None      # step_judge.StepJudge: reads the words of a permission at setup (account steps are never covered)
        self.pins: Any = None       # payment_pins.PaymentPins: a purchase permission may offer the site's saved payment PIN (use_pin)
        # (grant, tool, host, detail): the server writes the approval log (decision allowed_by_rule)
        self.on_use: Callable[[dict[str, Any], str, str, str], None] | None = None
        # (scope, grant): a one-shot task used its granted count; the server turns the schedule / watcher off and tells the user
        self.on_finished: Callable[[str, dict[str, Any]], None] | None = None
        self._run_used: set[tuple[int, str]] = set()   # (grant, run) whose one covered purchase is done: a second one asks
        self._job_parent: dict[str, str] = {}          # job id -> the task scope it was handed off from
        self._finishing: set[str] = set()              # one-shot scopes whose count is used: grants go when their run ends
        self._applied: dict[tuple[int, str], int] = {} # (apply grant, run) -> applications sent in that run
        with store._lock:
            store._db.executescript(SCHEMA)
            have = {r[1] for r in store._db.execute("PRAGMA table_info(schedule_grants)")}
            for name, decl in COLUMNS:
                if name not in have:
                    store._db.execute(f"ALTER TABLE schedule_grants ADD COLUMN {name} {decl}")
            # older rows: schedules only
            store._db.execute("UPDATE schedule_grants SET scope = 'schedule:' || schedule_id WHERE scope = ''")
            store._db.execute("CREATE INDEX IF NOT EXISTS schedule_grants_by_scope ON schedule_grants(scope)")
            store._db.commit()

    # ------------------------------------------------------------------ scopes
    @staticmethod
    def scope_key(kind: str, ref: Any) -> str:
        return f"{kind}:{ref}" if kind in SCOPES and str(ref or "").strip() else ""

    def scope_info(self, scope: str) -> dict[str, Any]:
        """{"kind", "ref", "name", "when", "exists"} of a task scope."""
        kind, _, ref = scope.partition(":")
        name = when = ""
        exists = False
        if kind == "schedule" and self.schedules is not None and ref.isdigit():
            s = self.schedules.get(int(ref))
            exists = s is not None
            name, when = (s or {}).get("name", ""), (s or {}).get("description", "")
        elif kind == "watcher" and self.watchers is not None and ref.isdigit():
            w = self.watchers.get(int(ref))
            exists = w is not None
            name, when = (w or {}).get("name", ""), (w or {}).get("description", "")
        elif kind == "job" and self.workers is not None and ref:
            j = self.workers.get(ref)
            exists = j is not None
            name = (j or {}).get("title", "")
        return {"kind": kind, "ref": ref, "name": name, "when": when, "exists": exists}

    def link_job(self, wid: str, parent: str) -> None:
        """A job handed off from a conditional task works under that task's grants too."""
        if wid and parent:
            if len(self._job_parent) > 500:
                self._job_parent.clear()
            self._job_parent[str(wid)] = parent

    def scopes(self) -> list[str]:
        return current_scopes(self._job_parent)

    def turn_scope(self) -> str:
        """The one task scope a covered step is counted for (the first of the running turn's)."""
        s = self.scopes()
        return s[0] if s else ""

    # ------------------------------------------------------------------ keeping them
    def _public(self, r: dict[str, Any]) -> dict[str, Any]:
        info = self.scope_info(r["scope"])
        out = {"id": r["id"], "scope": info["kind"], "scope_ref": info["ref"], "scope_key": r["scope"], "task_name": info["name"],
               "task_when": info["when"], "schedule_id": r["schedule_id"] if info["kind"] == "schedule" else 0,
               "schedule_name": info["name"] if info["kind"] == "schedule" else "",
               "schedule_when": info["when"] if info["kind"] == "schedule" else "", "kind": r["kind"], "site": r["site"],
               "created_at": r["created_at"], "uses": r["uses"], "last_used": r["last_used"], "trigger": r["trigger_text"],
               "action": r["action_text"], "once": bool(r["once"]), "urgent": bool(r["urgent"]), "missed": r["missed"],
               "max_count": int(r["max_count"] or 0)}
        if r["kind"] == "login":
            item = self.store.credential(r["login"]) if r["login"] else None
            out.update(login=r["login"], login_label=(item or {}).get("label") or r["login"])
        elif r["kind"] == "purchase":
            ship = self.shipping.get(r.get("shipping_id") or "") if self.shipping is not None and r.get("shipping_id") else None
            card = self.wallet.card(r["card_id"]) if self.wallet is not None and r.get("card_id") else None
            out.update(shipping_id=r.get("shipping_id") or None, shipping_label=(ship or {}).get("label"),
                       shipping_one_line=(ship or {}).get("one_line"))
            out.update(what=r["what"], max_amount=r["max_amount"], currency=r["currency"], monthly_cap=r["monthly_cap"],
                       runs_per_month=r["runs_per_month"], max_amount_text=money_text(r["max_amount"], r["currency"]),
                       monthly_cap_text=money_text(r["monthly_cap"], r["currency"]), spent_this_month=self.spent(r["id"]),
                       card_id=r.get("card_id") or None, card_label=(card or {}).get("label"), card_last4=(card or {}).get("last4"),
                       used=int(r["uses"] or 0), use_pin=bool(r.get("use_pin")))
        else:
            try:
                targets = [str(x) for x in json.loads(r["targets"] or "[]")]
            except ValueError:
                targets = []
            out.update(what=r["what"], targets=targets, template=r["template"], varies=r["varies"],
                       allow_delete=bool(r["allow_delete"]), used=int(r["uses"] or 0))
            if r["kind"] == "apply":   # max_count: per run; total_count: over all runs (0 = none); used: over all runs
                out.update(total_count=int(r.get("total_count") or 0))
        return out

    def list(self, schedule_id: int | None = None, *, login: str | None = None, kind: str | None = None,
             scope: str | None = None) -> list[dict[str, Any]]:
        if schedule_id is not None:
            scope = f"schedule:{schedule_id}"
        rows = self.store._all("SELECT * FROM schedule_grants WHERE (? IS NULL OR scope = ?) AND (? IS NULL OR login = ?) "
                               "AND (? IS NULL OR kind = ?) ORDER BY created_at DESC, id DESC",
                               (scope, scope, login, login, kind, kind))
        return [self._public(r) for r in rows]

    def get(self, gid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM schedule_grants WHERE id = ?", (gid,))
        return self._public(rows[0]) if rows else None

    def _scope_of(self, schedule_id: int | None, scope: str | None) -> tuple[str, int]:
        if scope:
            kind, _, ref = scope.partition(":")
            return scope, int(ref) if kind == "schedule" and ref.isdigit() else 0
        return f"schedule:{int(schedule_id or 0)}", int(schedule_id or 0)

    def _insert(self, scope: str, sid: int, kind: str, meta: dict[str, Any], **cols: Any) -> int:
        row = {"schedule_id": sid, "scope": scope, "kind": kind, "created_at": self.clock(), "site": "", **cols,
               "trigger_text": _clip(meta.get("trigger")), "action_text": _clip(meta.get("action")), "once": int(bool(meta.get("once"))),
               "urgent": int(bool(meta.get("urgent"))), "missed": _clip(meta.get("missed"))}
        names = ", ".join(row)
        return int(self.store._run(f"INSERT INTO schedule_grants({names}) VALUES({', '.join('?' * len(row))})",
                                   tuple(row.values())).lastrowid or 0)

    def add_login(self, schedule_id: int | None, login: str, site: str, from_approval: str = "", *, scope: str | None = None,
                  **meta: Any) -> dict[str, Any]:
        scope, sid = self._scope_of(schedule_id, scope)
        site = _site(site)
        self.store._run("DELETE FROM schedule_grants WHERE scope = ? AND kind = 'login' AND login = ? AND site = ?", (scope, login, site))
        gid = self._insert(scope, sid, "login", meta, login=login, site=site, from_approval=from_approval)
        self.store.add_vault_log(login, "schedule-allowed", f"{scope} may sign in on {site} without asking")
        return self.get(gid) or {}

    def add_purchase(self, schedule_id: int | None, site: str, what: str, max_amount: float, currency: str, from_approval: str = "",
                     shipping_id: str = "", *, scope: str | None = None, max_count: int = 0, monthly_cap: float | None = None,
                     card_id: str = "", use_pin: bool = False, **meta: Any) -> dict[str, Any]:
        """`use_pin`: a run may have the site's saved payment PIN entered, but only after the phone releases it for that run (the
        owner confirms with fingerprint or face each time; the task is urgent so that request is noticed)."""
        scope, sid = self._scope_of(schedule_id, scope)
        site, cur = _site(site), (currency or "KRW").upper()
        count = max(0, min(MAX_COUNT, int(max_count or 0)))
        runs = 1
        if scope.startswith("schedule:") and self.schedules is not None:
            runs = max(1, int(self.schedules.runs_per_month(sid) or 1))
        if monthly_cap is not None and float(monthly_cap) > 0:
            cap = float(monthly_cap)
        else:
            cap = float(max_amount) * (min(count, runs) if count and scope.startswith("schedule:") else count or runs)
        self.store._run("DELETE FROM schedule_grants WHERE scope = ? AND kind = 'purchase' AND site = ?", (scope, site))
        gid = self._insert(scope, sid, "purchase", meta, site=site, what=what.strip()[:MAX_WHAT], max_amount=float(max_amount),
                           currency=cur, monthly_cap=cap, runs_per_month=runs, from_approval=from_approval, shipping_id=shipping_id or "",
                           max_count=count, card_id=card_id or "", use_pin=int(bool(use_pin)))
        self.store.add_vault_log("wallet", "schedule-allowed", f"{scope} may buy on {site}: up to {money_text(float(max_amount), cur)} "
                                 f"per time, {money_text(cap, cur)} per month" + (f", {count} time(s)" if count else ""))
        return self.get(gid) or {}

    def add_action(self, scope: str, kind: str, what: str, targets: list[str], *, max_count: int = 0, template: str = "",
                   varies: str = "", allow_delete: bool = False, from_approval: str = "", **meta: Any) -> dict[str, Any]:
        """A send or change permission of a task: what, to/at which targets, how many times."""
        scope, sid = self._scope_of(None, scope)
        norm = change_target if kind == "change" else norm_target
        clean = list(dict.fromkeys(x for x in (norm(v) for v in targets) if x))[:MAX_TARGETS]
        site = next((x[5:] for x in clean if x.startswith("site:")), "")
        self.store._run("DELETE FROM schedule_grants WHERE scope = ? AND kind = ?", (scope, kind))
        gid = self._insert(scope, sid, kind, meta, site=site, what=_clip(what, MAX_WORDS), targets=json.dumps(clean, ensure_ascii=False),
                           template=_clip(template, 600), varies=_clip(varies), allow_delete=int(bool(allow_delete)),
                           max_count=max(0, min(MAX_COUNT, int(max_count or 0))), from_approval=from_approval)
        self.store.add_vault_log("tasks", "schedule-allowed", f"{scope} may {kind} without asking: {', '.join(clean)[:200]}")
        return self.get(gid) or {}

    def add_apply(self, scope: str, site: str, what: str, *, max_count: int = 1, total_count: int = 0, from_approval: str = "",
                  **meta: Any) -> dict[str, Any]:
        """An apply permission of a task: applications for `what` on one site, at most `max_count` per run, `total_count` in all (0: no
        total)."""
        scope, sid = self._scope_of(None, scope)
        site = _site(site)
        self.store._run("DELETE FROM schedule_grants WHERE scope = ? AND kind = 'apply'", (scope,))
        gid = self._insert(scope, sid, "apply", meta, site=site, what=_clip(what, MAX_WHAT),
                           targets=json.dumps([f"site:{site}"], ensure_ascii=False), max_count=max(1, min(MAX_COUNT, int(max_count or 1))),
                           total_count=max(0, min(MAX_COUNT * 31, int(total_count or 0))), from_approval=from_approval)
        self.store.add_vault_log("tasks", "schedule-allowed", f"{scope} may apply on {site} without asking: {what[:80]}, "
                                 f"{max_count} per run" + (f", {total_count} in all" if total_count else ""))
        return self.get(gid) or {}

    def revoke(self, gid: int, schedule_id: int | None = None, *, scope: str | None = None) -> bool:
        g = self.get(gid)
        if g is None or (schedule_id is not None and g["scope_key"] != f"schedule:{schedule_id}") or (scope and g["scope_key"] != scope):
            return False
        self.store._run("DELETE FROM schedule_grants WHERE id = ?", (gid,))
        self.store.add_vault_log(g.get("login") or ("wallet" if g["kind"] == "purchase" else "tasks"), "schedule-revoked",
                                 f"{g['scope_key']}: {g['kind']} on {g['site'] or '-'}")
        return True

    def drop_scope(self, scope: str) -> int:
        self._finishing.discard(scope)
        return self.store._run("DELETE FROM schedule_grants WHERE scope = ?", (scope,)).rowcount

    def drop_schedule(self, schedule_id: int) -> int:
        return self.drop_scope(f"schedule:{schedule_id}")

    def drop_watcher(self, watcher_id: int) -> int:
        return self.drop_scope(f"watcher:{watcher_id}")

    def drop_job(self, wid: str) -> int:
        self._job_parent.pop(str(wid), None)
        return self.drop_scope(f"job:{wid}")

    def drop_login(self, login: str) -> int:
        return self.store._run("DELETE FROM schedule_grants WHERE kind = 'login' AND login = ?", (login,)).rowcount

    async def account_check(self, texts: list[str]) -> str:
        """"" when none of these plain-words descriptions of what a permission covers signs up, changes an account's security or
        deletes an account, else the reason. The step judge reads them (one cached request each); no judge or no answer is a refusal."""
        for text in texts:
            verdict = await self.judge.text(text) if self.judge is not None else None
            if verdict is None:
                return NOT_CHECKED
            if verdict.signs_up or verdict.account_security:
                return ACCOUNT_REFUSED
        return ""

    def spent(self, gid: int, month: str | None = None) -> float:
        if self.wallet is None:
            return 0.0
        rows = self.store._all("SELECT COALESCE(SUM(amount), 0) AS s FROM wallet_payments WHERE grant_id = ? AND month = ?",
                               (gid, month or self.wallet.month_key()))
        return float(rows[0]["s"])

    def summary(self, scope: str) -> str:
        """What the task may do on its own, in one line for the run's note (the model), or ""."""
        items = self.list(scope=scope)
        if not items:
            return ""
        parts = []
        for g in items:
            left = (f", {max(0, g['max_count'] - g.get('used', 0))} of {g['max_count']} left"
                    if g["max_count"] and g["kind"] in COUNTED and g["kind"] != "apply" else "")
            if g["kind"] == "login":
                parts.append(f"sign in with the saved login '{g['login']}' on {g['site']}")
            elif g["kind"] == "purchase":
                card = f" with the card '{g['card_label']}'" if g.get("card_label") else ""
                parts.append(f"buy '{g['what']}' on {g['site']} up to {g['max_amount_text']} per time{card}{left}")
            elif g["kind"] == "apply":
                total = (f", {max(0, g['total_count'] - g.get('used', 0))} of {g['total_count']} left in all" if g.get("total_count")
                         else "")
                parts.append(f"apply for '{g['what']}' on {g['site']}, at most {g['max_count']} per run{total} (password, sign-up and "
                             "account steps and payments still ask)")
            elif g["kind"] == "send":
                tmpl = f" (wording: {g['template']})" if g["template"] else ""
                parts.append(f"send to {', '.join(g['targets'])}{tmpl}{left}")
            else:
                parts.append(f"change {g['what'] or 'it'} at {', '.join(g['targets'])}{' (deleting allowed)' if g['allow_delete'] else ''}{left}")
        head = items[0]
        once = " It is one-shot: it ends once the count is used." if head["once"] else ""
        return (f"[Allowed by the user when this task was set up (trigger: {head['trigger'] or '-'}; to do: {head['action'] or '-'}): "
                + "; ".join(parts) + f". Do exactly that now, without asking, and nothing more.{once} Anything outside it (another "
                "site, recipient or card, a higher total, a sign-up, an account change, a deletion not allowed) stops and asks.]")

    def _used(self, g: dict[str, Any], tool: str, host: str, detail: str) -> None:
        self.store._run("UPDATE schedule_grants SET uses = uses + 1, last_used = ? WHERE id = ?", (self.clock(), g["id"]))
        if self.on_use is not None:
            try:
                self.on_use(g, tool, host, detail)
            except Exception:  # noqa: BLE001 - the log never blocks the step
                pass
        limit = int(g.get("total_count") or 0) if g["kind"] == "apply" else int(g.get("max_count") or 0)
        if g["kind"] in COUNTED and limit and g.get("once"):
            fresh = self.get(g["id"]) or {}
            if int(fresh.get("used") or 0) >= limit and g["scope_key"] not in self._finishing:
                self._finishing.add(g["scope_key"])
                if self.on_finished is not None:
                    try:
                        self.on_finished(g["scope_key"], fresh)
                    except Exception:  # noqa: BLE001 - the step itself already went through
                        pass

    def urgency(self) -> tuple[bool, str]:
        """(whether the running task was marked urgent at setup, what is missed without an answer) for the cards it asks."""
        scopes = self.scopes()
        if not scopes:
            return False, ""
        marks = ", ".join("?" * len(scopes))
        rows = self.store._all(f"SELECT missed FROM schedule_grants WHERE scope IN ({marks}) AND urgent = 1 ORDER BY id LIMIT 1", tuple(scopes))
        return (True, str(rows[0]["missed"] or "")) if rows else (False, "")

    def finishing(self, scope: str) -> bool:
        return scope in self._finishing

    def settle(self, scopes: list[str]) -> int:
        """A run ended: the grants of a one-shot task whose count it used go now (its schedule or watcher is already off)."""
        n = 0
        for s in scopes:
            if s in self._finishing:
                n += self.drop_scope(s)
        return n

    # ------------------------------------------------------------------ using them (only inside a run of that task)
    def _rows(self, kind: str, scopes: list[str]) -> list[dict[str, Any]]:
        if not scopes:
            return []
        marks = ", ".join("?" * len(scopes))
        return self.store._all(f"SELECT * FROM schedule_grants WHERE scope IN ({marks}) AND kind = ? ORDER BY id", (*scopes, kind))

    def login_cover(self, login: str, host: str, schedule_id: int | None = None) -> dict[str, Any] | None:
        """The login grant of the task whose run is going on that covers typing `login` on `host`, or None."""
        scopes = [f"schedule:{schedule_id}"] if schedule_id else self.scopes()
        site = _site(host)
        if not scopes or not login or not site:
            return None
        rows = [r for r in self._rows("login", scopes) if r["login"] == login and r["site"] in _sites(host)]
        return self._public(rows[0]) if rows else None

    def use_login(self, g: dict[str, Any], host: str) -> None:
        self._used(g, "browser_login", host, t(LOGIN_COVERED, login=g.get("login_label") or g.get("login"), site=g["site"]))

    def purchase_check(self, host: str, amount: float, currency: str, summary: str, schedule_id: int | None = None,
                       card: dict[str, Any] | None = None) -> PurchaseCheck:
        """Whether a run of the task going on may complete this purchase on its own. Outside a task's run, or for a task without a
        purchase grant: PurchaseCheck(None, "")."""
        scopes = [f"schedule:{schedule_id}"] if schedule_id else self.scopes()
        rows = self._rows("purchase", scopes)
        if not rows:
            return PurchaseCheck(None)
        site, cur = _site(host), (currency or "KRW").upper()
        here = [r for r in rows if r["site"] in _sites(host)]
        if not here:
            return PurchaseCheck(None, "task_other_site", MISSES["task_other_site"].format(site=", ".join(r["site"] for r in rows)))
        r = here[0]
        if (run := _run_key()) and (r["id"], run) in self._run_used:
            return PurchaseCheck(None, "task_again", MISSES["task_again"])
        if r["max_count"] and int(r["uses"] or 0) >= int(r["max_count"]):
            return PurchaseCheck(None, "task_used_up", MISSES["task_used_up"].format(count=r["max_count"]))
        if r["currency"] != cur:
            return PurchaseCheck(None, "task_currency", MISSES["task_currency"].format(currency=r["currency"]))
        if amount > r["max_amount"] + 1e-9:
            return PurchaseCheck(None, "task_over_cap", MISSES["task_over_cap"].format(amount=money_text(r["max_amount"], cur)))
        if self.spent(r["id"]) + amount > r["monthly_cap"] + 1e-9:
            return PurchaseCheck(None, "task_over_monthly", MISSES["task_over_monthly"].format(amount=money_text(r["monthly_cap"], cur)))
        want = _words(r["what"])
        if want and not (want & _words(summary)):
            return PurchaseCheck(None, "task_other_item", MISSES["task_other_item"].format(what=r["what"]))
        if r["card_id"] and card is not None and card.get("id") != r["card_id"]:
            named = self.wallet.card(r["card_id"]) if self.wallet is not None else None
            return PurchaseCheck(None, "task_other_card", MISSES["task_other_card"].format(card=(named or {}).get("label") or "?"))
        return PurchaseCheck(self._public(r))

    def grant_card(self, g: dict[str, Any] | None) -> dict[str, Any] | None:
        """The wallet card a purchase permission names (None: whatever the run uses)."""
        if g is None or not g.get("card_id") or self.wallet is None:
            return None
        return self.wallet.card(g["card_id"])

    def use_purchase(self, g: dict[str, Any], host: str, amount: float, currency: str) -> None:
        if run := _run_key():
            if len(self._run_used) > 500:
                self._run_used.clear()
            self._run_used.add((g["id"], run))
        self._used(g, "confirm_purchase", host, t(PURCHASE_COVERED, what=g["what"], site=g["site"], amount=money_text(amount, currency)))

    def action_check(self, tool: str, args: dict[str, Any], site: str = "", commit: str = "") -> ActionCheck:
        """Whether a run of the task going on may do this message or change on its own. ActionCheck(None) when the task has no
        permission of that kind (or the step is not one a permission can cover): it asks as usual."""
        if commit == "unknown":
            return ActionCheck(None)   # a step whose target could not be read is never covered
        if commit == "account":
            scopes = self.scopes()
            if scopes and (self._rows("send", scopes) or self._rows("change", scopes) or self._rows("apply", scopes)):
                return ActionCheck(None, "task_account", ACTION_MISSES["task_account"])
            return ActionCheck(None)
        if tool in (*CLICK_TOOLS, "browser_type") and commit in APPLY_STEPS and site:
            hit = self._apply_check(tool, site, commit)
            if hit is not None:
                return hit
        found = action_of(tool, args, site, commit)
        if found is None:
            return ActionCheck(None)
        kind, wanted, deleting, text = found
        if (e := effect_of(tool, args, site=site, commit=commit)) is not None and e.policy == "fresh":
            return ActionCheck(None, "task_fresh_only", ACTION_MISSES["task_fresh_only"], kind)   # declared in connector_rules.json
        rows = self._rows(kind, self.scopes())
        if not rows or not wanted:
            return ActionCheck(None, kind=kind)
        r = rows[0]
        g = self._public(r)
        have = set(g["targets"])
        if not all(_target_ok(w, have) for w in wanted):
            return ActionCheck(None, "task_other_target", ACTION_MISSES["task_other_target"].format(
                kind=kind, targets=", ".join(g["targets"]) or "-"), kind)
        if deleting and not g["allow_delete"]:
            return ActionCheck(None, "task_no_delete", ACTION_MISSES["task_no_delete"], kind)
        if g["max_count"] and g["used"] >= g["max_count"]:
            return ActionCheck(None, "task_used_up", ACTION_MISSES["task_used_up"].format(count=g["max_count"]), kind)
        if kind == "send" and text and not template_ok(g["template"], text):
            return ActionCheck(None, "task_other_text", ACTION_MISSES["task_other_text"].format(template=g["template"]), kind)
        return ActionCheck(g, kind=kind)

    def _apply_check(self, tool: str, site: str, commit: str) -> ActionCheck | None:
        """An application step of a task with an apply permission; None when the task has none (or, for a step that is not itself an
        application, none on this site: a send permission may still cover it)."""
        rows = self._rows("apply", self.scopes())
        if not rows:
            return None
        here = [r for r in rows if r["site"] in _sites(site)]
        if not here:
            if commit != "apply":
                return None
            return ActionCheck(None, "task_other_site", ACTION_MISSES["task_other_site"].format(site=", ".join(r["site"] for r in rows)),
                               "apply")
        g = self._public(here[0])
        in_run = self._applied.get((g["id"], _run_key()), 0)
        if self._apply_counts(tool, commit, in_run):
            if in_run >= g["max_count"]:
                return ActionCheck(None, "task_run_used_up", ACTION_MISSES["task_run_used_up"].format(count=g["max_count"]), "apply")
            if g["total_count"] and g["used"] >= g["total_count"]:
                return ActionCheck(None, "task_used_up", ACTION_MISSES["task_used_up"].format(count=g["total_count"]), "apply")
        elif in_run >= g["max_count"] and commit == "form":
            return ActionCheck(None, "task_run_used_up", ACTION_MISSES["task_run_used_up"].format(count=g["max_count"]), "apply")
        return ActionCheck(g, kind="apply")

    def covered_targets(self) -> set[str]:
        """Everywhere the running task's standing permissions reach (the targets of its send / change permissions, the sites of its
        apply, purchase and login permissions), in the comparable form of effects.norm_target. Empty in a plain chat. The rule for steps
        after outside text (outside_rule.py) takes these as the places the task may act on."""
        scopes = self.scopes()
        out: set[str] = set()
        for kind in ("send", "change", "apply", "purchase", "login"):
            for r in self._rows(kind, scopes):
                if r.get("site"):
                    out.update(f"site:{k}" for k in site_keys(str(r["site"])))
                try:
                    out.update(str(x) for x in json.loads(r.get("targets") or "[]"))
                except ValueError:
                    continue
        return out

    def apply_covers(self, site: str) -> bool:
        """The task going on has an "apply" permission for this site: its form's basic fields (name, contact phone and email, shipping
        address) need no card; a field the privacy guard puts in another category (ID, account, health, ...) still does."""
        return any(r["site"] in _sites(site) for r in self._rows("apply", self.scopes()))

    @staticmethod
    def _apply_counts(tool: str, commit: str, in_run: int) -> bool:
        """Whether an application step sends one application: an "apply" click, or a "form" click before any in this run."""
        return tool in CLICK_TOOLS and (commit == "apply" or (commit == "form" and in_run == 0))

    @staticmethod
    def counts(tool: str, commit: str = "") -> bool:
        """Whether a covered step uses one of the granted times: the message or change itself, not typing into its form."""
        return tool != "browser_type" and commit not in ("personal",)

    def use_action(self, g: dict[str, Any], tool: str, host: str = "", commit: str = "") -> bool:
        """A covered step goes ahead: written to the approval log (on_use). True when it used one of the granted times."""
        if g.get("kind") == "apply":
            return self._use_apply(g, tool, host, commit)
        if not self.counts(tool, commit):
            if self.on_use is not None:
                try:
                    self.on_use(g, tool, host, t(ACTION_COVERED_FREE, action=g["action"] or g["what"] or g["kind"]))
                except Exception:  # noqa: BLE001
                    pass
            return False
        used = int(g.get("used") or 0) + 1
        detail = (t(ACTION_COVERED, action=g["action"] or g["what"] or g["kind"], used=used, count=g["max_count"]) if g["max_count"]
                  else t(ACTION_COVERED_FREE, action=g["action"] or g["what"] or g["kind"]))
        self._used(g, tool, host, detail)
        return True

    def _use_apply(self, g: dict[str, Any], tool: str, host: str, commit: str) -> bool:
        key = (int(g["id"]), _run_key())
        in_run = self._applied.get(key, 0)
        action = t(APPLY_COVERED, what=g["what"] or g["action"], site=g["site"])
        if not self._apply_counts(tool, commit, in_run):   # filling in / sending the form of an application already counted
            if self.on_use is not None:
                try:
                    self.on_use(g, tool, host, t(ACTION_COVERED_FREE, action=action))
                except Exception:  # noqa: BLE001
                    pass
            return False
        if len(self._applied) > 500:
            self._applied.clear()
        self._applied[key] = in_run + 1
        self._used(g, tool, host, t(ACTION_COVERED, action=action, used=in_run + 1, count=g["max_count"]))
        return True


ScheduleGrants = ConditionalGrants   # the first version knew schedules only


# ---------------------------------------------------------------------------------------------------------------- the setup question
def _int(v: Any, default: int = 0) -> int:
    if isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _amount(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def task_allow_tools(grants: ConditionalGrants, wallet: Any = None) -> list[Tool]:
    def scope_of(args: dict[str, Any]) -> str:
        for key, kind in (("schedule_id", "schedule"), ("watcher_id", "watcher"), ("job_id", "job"), ("id", "schedule")):
            v = args.get(key)
            if v not in (None, "", 0):
                ref = str(v).strip().lstrip("#")
                return grants.scope_key(kind, ref)
        return ""

    def given(args: dict[str, Any]) -> list[str]:
        return [k for k in ("schedule_id", "watcher_id", "job_id") if args.get(k) not in (None, "", 0)]

    def part(args: dict[str, Any], key: str) -> dict[str, Any] | None:
        p = args.get(key)
        return p if isinstance(p, dict) and p else None

    def login_item(args: dict[str, Any]) -> dict[str, Any] | None:
        name = str(args.get("login") or "").strip()
        return grants.store.credential(name) if name else None

    def once_of(args: dict[str, Any], scope: str) -> bool:
        if isinstance(args.get("once"), bool):
            return bool(args["once"])
        kind, _, ref = scope.partition(":")
        if kind == "schedule" and grants.schedules is not None and ref.isdigit():
            # a job on the calendar (every Monday at 9) repeats what it does; one that polls every few minutes waits for something
            # to happen once, like a watcher. The agent says which with `once`; without it, only a calendar repeat is not one-shot
            s = grants.schedules.get(int(ref)) or {}
            return s.get("kind") not in ("cron", "window")
        return True

    def count_of(p: dict[str, Any], once: bool) -> int:
        n = _int(p.get("max_count"), 0)
        return max(0, min(MAX_COUNT, n)) if n else (1 if once else 0)

    def card_of(p: dict[str, Any] | None) -> dict[str, Any] | None:
        if p is None or wallet is None:
            return None
        if p.get("card"):
            return wallet.find(p.get("card"))
        return None

    def ship_of(p: dict[str, Any] | None) -> dict[str, Any] | None:
        if p is None or grants.shipping is None:
            return None
        return grants.shipping.find(p.get("shipping")) if p.get("shipping") else None

    def targets_of(p: dict[str, Any]) -> list[str]:
        raw = p.get("to") if p.get("to") is not None else p.get("where")
        items = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) and raw.strip() else []
        out = [str(x) for x in items if str(x or "").strip()]
        if str(p.get("site") or "").strip():
            out.append("site:" + str(p["site"]))
        return out

    def where_of(p: dict[str, Any]) -> list[str]:
        """A change permission's places: "calendar", a connected service by name, or a site address."""
        return [n for n in (change_target(x) for x in targets_of(p)) if n]

    async def account_hold(args: dict[str, Any]) -> str | None:
        """Before the card: what each part of the permission covers, read by the step judge (docs/no-word-lists.md H3). A sign-up or an
        account / password change is refused; no judge or an answer it cannot give is refused too (fail closed)."""
        texts = []
        for kind, keys in (("purchase", ("what",)), ("send", ("template", "what")), ("change", ("what",)), ("apply", ("what",))):
            if (p := part(args, kind)) is not None:
                if text := " ".join(_clip(p.get(k)) for k in keys if _clip(p.get(k))):
                    texts.append(text)
        verdict = await grants.account_check(texts)
        return f"Error: task_allow was not run: {verdict}" if verdict else None

    def validate(args: dict[str, Any]) -> str | None:
        if unattended_now():
            return ("this is asked once while the task is being set up, with the user there, never inside a run of a task (nobody is "
                    "there to answer). Go on without it and say so in the run's report")
        if len(given(args)) > 1:
            return "give exactly one of schedule_id, watcher_id or job_id: the task this permission is for"
        scope = scope_of(args)
        if not scope:
            return "give the task: schedule_id (from schedule_create), watcher_id (from watch_add) or job_id (from delegate)"
        if not grants.scope_info(scope)["exists"]:
            kind, _, ref = scope.partition(":")
            return f"no {kind} #{ref}; create it first and pass its id"
        if not _clip(args.get("trigger")):
            return "trigger is required: when this happens, in the user's plain words (e.g. 'a cancelled seat shows up on the booking page')"
        if not _clip(args.get("action")):
            return "action is required: exactly what will be done when it happens, in plain words"
        if not any((args.get("login"), part(args, "purchase"), part(args, "send"), part(args, "change"), part(args, "apply"))):
            return "give what the task may do on its own: login, purchase, send, change and/or apply"
        if args.get("login"):
            item = login_item(args)
            if item is None or item["kind"] != "login":
                return (f"no website login named {args.get('login')!r}. Ask for it with request_login first (the request already counts: "
                        "pass its name here)")
        if (p := part(args, "purchase")) is not None:
            if not _site(str(p.get("site") or "")):
                return "purchase.site is required: the address of the site where it pays"
            what = " ".join(str(p.get("what") or "").split())
            if not what:
                return "purchase.what is required: what is bought, in the user's words"
            if len(what) > MAX_WHAT:
                return f"purchase.what is too long; keep it under {MAX_WHAT} characters"
            amount = _amount(p.get("max_amount"))
            if amount is None or amount <= 0:
                return "purchase.max_amount is required: the most one purchase may cost, as a number above 0 (what the user agreed)"
            if not CURRENCY.fullmatch(str(p.get("currency") or "KRW").upper()):
                return "purchase.currency must be a 3-letter code such as KRW"
            if p.get("max_count") is not None and not 0 <= _int(p.get("max_count"), -1) <= MAX_COUNT:
                return f"purchase.max_count must be between 1 and {MAX_COUNT} (0 = no count, only for a repeating job)"
            cap = p.get("monthly_cap")
            if cap is not None and ((_amount(cap) or 0) < amount):
                return "purchase.monthly_cap must be at least max_amount"
            if p.get("card") and card_of(p) is None:
                names = ", ".join(f"'{c['label']}'" for c in wallet.cards()) if wallet is not None else ""
                return f"no card {str(p.get('card'))[:40]!r} in the wallet. Cards: {names or 'none'} (wallet_cards)"
            if p.get("shipping") and ship_of(p) is None:
                names = ", ".join(f"'{x['label']}'" for x in grants.shipping.profiles()) if grants.shipping is not None else ""
                return f"no shipping profile {str(p.get('shipping'))[:40]!r}. Profiles: {names or 'none'} (the user can add one in Wallet)"
        if (p := part(args, "send")) is not None:
            if not targets_of(p):
                return "send.to is required: the recipients (email addresses, phone numbers or contact names), or send.site for posts on a site"
            if len(targets_of(p)) > MAX_TARGETS:
                return f"at most {MAX_TARGETS} recipients"
        if (p := part(args, "change")) is not None:
            if not _clip(p.get("what")):
                return "change.what is required: what may be changed, in plain words (e.g. 'move my run event to the evening')"
            if not where_of(p):
                return "change.where is required: 'calendar', a connected service's name, or a site address"
        if (p := part(args, "apply")) is not None:
            if not _site(str(p.get("site") or "")):
                return "apply.site is required: the address of the site where the applications are sent"
            what = " ".join(str(p.get("what") or "").split())
            if not what:
                return "apply.what is required: what is applied for, in the user's words (e.g. 'gift trial campaigns')"
            if len(what) > MAX_WHAT:
                return f"apply.what is too long; keep it under {MAX_WHAT} characters"
            if p.get("max_count") is not None and not 1 <= _int(p.get("max_count"), 0) <= MAX_COUNT:
                return f"apply.max_count must be between 1 and {MAX_COUNT}: the most applications in one run"
            if p.get("total") is not None and _int(p.get("total"), -1) < 0:
                return "apply.total must be 0 (no total) or the most applications over all runs"
        return None

    def meta(args: dict[str, Any], scope: str) -> dict[str, Any]:
        return {"trigger": _clip(args.get("trigger")), "action": _clip(args.get("action")), "once": once_of(args, scope),
                "urgent": bool(args.get("urgent")), "missed": _clip(args.get("missed"))}

    def purchase_view(p: dict[str, Any], once: bool, scope: str) -> dict[str, Any]:
        cur, amount = str(p.get("currency") or "KRW").upper(), _amount(p.get("max_amount")) or 0.0
        count = count_of(p, once)
        kind, _, ref = scope.partition(":")
        runs = grants.schedules.runs_per_month(int(ref)) if kind == "schedule" and grants.schedules is not None and ref.isdigit() else 1
        cap = _amount(p.get("monthly_cap")) or amount * (min(count, max(1, runs)) if count and kind == "schedule" else count or max(1, runs))
        card = card_of(p) or (wallet.default_card() if wallet is not None and not p.get("pay_with") else None)
        card_view = None
        note = ""
        if card is not None:
            limit = wallet.limit_for(cur) if wallet is not None else None
            auto = bool(card.get("auto_pay")) and bool(card.get("server_copy"))
            limit_text = money_text(limit[0], cur) if auto and limit else ""
            phone_only = not card.get("server_copy")
            card_view = {"id": card["id"], "label": card["label"], "last4": card.get("last4", ""), "auto_pay": auto,
                         "auto_limit": limit[0] if auto and limit else None, "auto_limit_text": limit_text, "phone_only": phone_only}
            if phone_only:
                note = t("This card is kept on the phone only, so each payment will still ask.")
            elif limit_text:
                note = t("This card's auto-pay limit is {limit}. Allow up to {amount} for this task?", limit=limit_text,
                         amount=money_text(amount, cur))
            else:
                note = t("This card does not pay on its own. Allow up to {amount} for this task?", amount=money_text(amount, cur))
        ship = ship_of(p)
        pins = getattr(grants, "pins", None)
        use_pin = bool(p.get("use_pin")) and pins is not None and pins.for_host(_site(str(p.get("site") or ""))) is not None
        return {"use_pin": use_pin, "site": _site(str(p.get("site") or "")), "what": " ".join(str(p.get("what") or "").split()), "max_amount": amount,
                "currency": cur, "max_amount_text": money_text(amount, cur), "max_count": count, "monthly_cap": cap,
                "monthly_cap_text": money_text(cap, cur), "card": card_view, "card_note": note,
                "shipping": f"{ship['label']} · {ship['one_line']}"[:160] if ship else ""}

    def view(args: dict[str, Any]) -> dict[str, Any]:
        """The setup card's facts, for the app's own layout (and as plain lines for any other client)."""
        scope = scope_of(args)
        info = grants.scope_info(scope)
        once = once_of(args, scope)
        out: dict[str, Any] = {"scope": info["kind"], "task_name": info["name"], "task_when": info["when"],
                               "trigger": _clip(args.get("trigger")), "action": _clip(args.get("action")), "once": once,
                               "urgent": bool(args.get("urgent")), "missed": _clip(args.get("missed")),
                               "login": None, "purchase": None, "send": None, "change": None, "apply": None}
        if (item := login_item(args)) is not None:
            out["login"] = {"label": item["label"], "site": _site(item["hosts"][0]) if item.get("hosts") else ""}
        if (p := part(args, "purchase")) is not None:
            out["purchase"] = purchase_view(p, once, scope)
        if (p := part(args, "send")) is not None:
            out["send"] = {"to": [str(x) for x in targets_of(p) if not str(x).startswith("site:")],
                           "site": _site(str(p.get("site") or "")), "template": _clip(p.get("template"), 600),
                           "varies": _clip(p.get("varies")), "max_count": count_of(p, once)}
        if (p := part(args, "change")) is not None:
            out["change"] = {"what": _clip(p.get("what")), "where": [str(x) for x in targets_of(p)], "allow_delete": bool(p.get("allow_delete")),
                             "max_count": count_of(p, once)}
        if (p := part(args, "apply")) is not None:
            out["apply"] = {"site": _site(str(p.get("site") or "")), "what": " ".join(str(p.get("what") or "").split())[:MAX_WHAT],
                            "max_count": max(1, min(MAX_COUNT, _int(p.get("max_count"), 1))), "total": max(0, _int(p.get("total"), 0))}
        return out

    def times(n: int) -> str:
        return t("{count} time(s)", count=n) if n else t("no count limit")

    def card_lines(_approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        v = view(args)
        lines = [{"label": t("Task"), "value": f"{v['task_name']}{' · ' + v['task_when'] if v['task_when'] else ''}"[:120]},
                 {"label": t("When"), "value": v["trigger"][:160]}, {"label": t("Will do"), "value": v["action"][:160]}]
        if v["login"]:
            lines.append({"label": t("Login"), "value": f"{v['login']['label']} · {v['login']['site']}"[:120]})
        if (p := v["purchase"]) is not None:
            lines.append({"label": t("Purchase"), "value": f"{p['what']} · {p['site']}"[:120]})
            lines.append({"label": t("Up to"), "value": t("{amount} per time, {times}, at most {monthly} a month", amount=p["max_amount_text"],
                                                           times=times(p["max_count"]), monthly=p["monthly_cap_text"])})
            if p["card"]:
                lines.append({"label": t("Card"), "value": f"{p['card']['label']} ••{p['card']['last4']}"})
            if p["card_note"]:
                lines.append({"label": "", "value": p["card_note"], "full": True})
            if p["shipping"]:
                lines.append({"label": t("Ship to"), "value": p["shipping"]})
            if p.get("use_pin"):
                lines.append({"label": t("Payment PIN"), "value": t("Uses the saved payment PIN; your phone asks for your fingerprint or face "
                                                                    "each time, with an urgent notice"), "full": True})
        if (s := v["send"]) is not None:
            lines.append({"label": t("Send to"), "value": ", ".join([*s["to"], *([s["site"]] if s["site"] else [])])[:160]})
            if s["template"]:
                lines.append({"label": t("Message"), "value": s["template"], "full": True})
            if s["varies"]:
                lines.append({"label": t("May vary"), "value": s["varies"]})
            lines.append({"label": t("Up to"), "value": times(s["max_count"])})
        if (c := v["change"]) is not None:
            lines.append({"label": t("May change"), "value": f"{c['what']} · {', '.join(c['where'])}"[:160]})
            lines.append({"label": t("Up to"), "value": times(c["max_count"]) + (" · " + t("deleting allowed") if c["allow_delete"] else "")})
        if (a := v["apply"]) is not None:
            lines.append({"label": t("Apply for"), "value": f"{a['what']} · {a['site']}"[:160]})
            lines.append({"label": t("Up to"), "value": t("{count} per run", count=a["max_count"])
                          + (" · " + t("{count} in total", count=a["total"]) if a["total"] else "")})
        if v["once"]:
            lines.append({"label": "", "value": t("Ends by itself after that."), "full": True})
        return {"lines": lines, "grant": v}

    async def describe(args: dict[str, Any]) -> str:
        v = view(args)
        out = [t("When {trigger}: {action}", trigger=v["trigger"], action=v["action"])]
        if (p := v["purchase"]) is not None:
            out.append(t("buy {what} on {site}, up to {amount} per time ({times})", what=p["what"], site=p["site"],
                         amount=p["max_amount_text"], times=times(p["max_count"])))
        if (s := v["send"]) is not None:
            out.append(t("send to {to}", to=", ".join([*s["to"], *([s["site"]] if s["site"] else [])])))
        if (c := v["change"]) is not None:
            out.append(t("change {what}", what=c["what"]))
        if (a := v["apply"]) is not None:
            out.append(t(APPLY_COVERED, what=a["what"], site=a["site"]) + " (" + t("{count} per run", count=a["max_count"]) + ")")
        if v["login"]:
            out.append(t("sign in with the {login} login on {site}", login=v["login"]["label"], site=v["login"]["site"]))
        return f"{out[0]} · {'; '.join(out[1:])}. {t('Anything else still asks.')}"

    async def handler(args: dict[str, Any]) -> str:
        scope = scope_of(args)
        m = meta(args, scope)
        approval = approval_of_current_call()
        said = []
        if (item := login_item(args)) is not None:
            g = grants.add_login(None, item["name"], item["hosts"][0], approval, scope=scope, **m)
            said.append(f"sign in with the '{item['label']}' login on {g['site']} without a card")
        if (p := part(args, "purchase")) is not None:
            cur, amount = str(p.get("currency") or "KRW").upper(), _amount(p.get("max_amount")) or 0.0
            ship, card = ship_of(p), card_of(p)
            use_pin = bool(purchase_view(p, m["once"], scope).get("use_pin"))
            if use_pin:   # the PIN is released per run with the owner's fingerprint / face: the request must be noticed in time
                m = {**m, "urgent": True, "missed": m.get("missed") or t("the purchase waits for your fingerprint to enter the payment PIN")}
            g = grants.add_purchase(None, str(p["site"]), " ".join(str(p["what"]).split()), amount, cur, approval,
                                    shipping_id=(ship or {}).get("id", ""), scope=scope, max_count=count_of(p, m["once"]),
                                    monthly_cap=_amount(p.get("monthly_cap")), card_id=(card or {}).get("id", ""), use_pin=use_pin, **m)
            if use_pin:
                said.append(f"have the saved payment PIN of {g['site']} entered in a run, but only after the user's phone releases it for "
                            "that run (they confirm with fingerprint or face each time; the task is marked urgent so that request is "
                            "noticed). Without that the run waits for them to enter it")
            count = f", at most {g['max_count']} time(s)" if g["max_count"] else ""
            said.append(f"buy '{g['what']}' on {g['site']} without a card when the total is at most {g['max_amount_text']}{count} (and at "
                        f"most {g['monthly_cap_text']} a month); this stands in for the card's auto-pay limit and the first-payment-on-"
                        "a-site rule for this task only; the user is notified after each purchase"
                        + (f"; orders go to the shipping profile '{ship['label']}'" if ship else "")
                        + (f"; pay with the card '{card['label']}'" if card else ""))
            if card is not None and not card["server_copy"]:
                said.append(f"BUT the card '{card['label']}' is kept on the phone only (end-to-end), so the phone must send it for every "
                            "payment and it will still ask. Tell the user exactly that: they can turn on auto-pay for this card in "
                            "Wallet (it asks for the number and CVC again), or the site's own balance can be used")
        if (p := part(args, "send")) is not None:
            g = grants.add_action(scope, "send", _clip(p.get("what")) or m["action"], targets_of(p), max_count=count_of(p, m["once"]),
                                  template=_clip(p.get("template"), 600), varies=_clip(p.get("varies")), from_approval=approval, **m)
            said.append(f"send to {', '.join(g['targets'])} without a card" + (f", at most {g['max_count']} time(s)" if g["max_count"] else "")
                        + (f", keeping to the wording '{g['template']}' (only the parts in braces vary)" if g["template"] else ""))
        if (p := part(args, "change")) is not None:
            g = grants.add_action(scope, "change", _clip(p.get("what")), where_of(p), max_count=count_of(p, m["once"]),
                                  allow_delete=bool(p.get("allow_delete")), from_approval=approval, **m)
            said.append(f"change '{g['what']}' at {', '.join(g['targets'])} without a card" +
                        (f", at most {g['max_count']} time(s)" if g["max_count"] else "") + ("" if g["allow_delete"] else "; never delete"))
        if (p := part(args, "apply")) is not None:
            total = max(0, _int(p.get("total"), 0))
            g = grants.add_apply(scope, str(p["site"]), " ".join(str(p["what"]).split()), max_count=_int(p.get("max_count"), 1),
                                 total_count=total, from_approval=approval, **m)
            said.append(f"apply for '{g['what']}' on {g['site']} without a card, at most {g['max_count']} per run"
                        + (f" and {g['total_count']} in all" if g["total_count"] else "")
                        + "; filling in the application form there is covered too, but password fields, sign-ups, account steps and "
                        "payments still ask; the user gets one notice after each run listing what was applied for")
        info = grants.scope_info(scope)
        end = (" It is one-shot: once that is done the task turns itself off, so nothing happens twice." if m["once"] else "")
        return (f"ALLOWED for {info['kind']} #{info['ref']} '{info['name']}': when {m['trigger']}, {'; '.join(said)}.{end} Tell the user "
                "plainly that from then on this happens on its own without asking and they get a notice right after. A higher total, "
                "another site, recipient or card, a sign-up, an account change or a deletion not allowed still stop and ask"
                + (" with an urgent notice" if m["urgent"] else "") + ".")

    item_props = {"type": "integer", "minimum": 1, "maximum": MAX_COUNT, "description": "how many times at most (default 1 when once)"}
    return [Tool(
        "task_allow",
        "Ask ONCE, with the user there, while setting up any task that acts later on its own ('when X happens, do Y': a watcher, a "
        "polling or repeating schedule, a background job), never in a run. One approval card names the trigger, exactly what will be "
        "done, the site/recipients/card and the limits. Once allowed, the task does that without a card when it fires and the user "
        "is notified right after; anything outside still asks. Without it every step stops for an approval nobody may be there to give.",
        {"type": "object", "properties": {
            "schedule_id": {"type": "integer"}, "watcher_id": {"type": "integer"}, "job_id": {"type": "string"},
            "trigger": {"type": "string", "description": "when it happens, in the user's plain words"},
            "action": {"type": "string", "description": "exactly what will be done then, in plain words"},
            "once": {"type": "boolean", "description": "true: done after it happened once (default, except a calendar repeat)"},
            "urgent": {"type": "boolean", "description": "true when a late answer means missing it (a cancelled seat, limited stock)"},
            "missed": {"type": "string", "description": "with urgent: what is missed if a card is not answered in time"},
            "login": {"type": "string", "description": "a saved login's name (also one just requested)"},
            "purchase": {"type": "object", "properties": {
                "site": {"type": "string"}, "what": {"type": "string", "description": "in the user's words"},
                "max_amount": {"type": "number", "description": "the most one purchase costs, as agreed"},
                "currency": {"type": "string"}, "max_count": item_props,
                "monthly_cap": {"type": "number", "description": "optional; default max_amount x max_count"},
                "card": {"type": "string", "description": "a wallet card label (default card if left out)"},
                "shipping": {"type": "string", "description": "a shipping profile's nickname, if it is delivered"},
                "use_pin": {"type": "boolean", "description": "the site's checkout asks for the payment PIN the user saved: allow it to be "
                                                              "entered (the phone still asks for fingerprint each run)"}},
                "required": ["site", "what", "max_amount"]},
            "send": {"type": "object", "properties": {
                "to": {"type": "array", "items": {"type": "string"}, "description": "email addresses, phone numbers or contact names"},
                "site": {"type": "string", "description": "for posts or forms on a site"},
                "template": {"type": "string", "description": "the message; {braces} mark the parts that may vary"},
                "varies": {"type": "string", "description": "what may vary, in words"}, "max_count": item_props}},
            "change": {"type": "object", "properties": {
                "what": {"type": "string", "description": "what may be changed, in plain words"},
                "where": {"type": "array", "items": {"type": "string"}, "description": "'calendar', a connected service name, or a site"},
                "allow_delete": {"type": "boolean"}, "max_count": item_props},
                "required": ["what", "where"]},
            "apply": {"type": "object", "description": "applications on one site (a trial campaign, a draw, an event sign-up form)",
                      "properties": {
                "site": {"type": "string"}, "what": {"type": "string", "description": "what is applied for, in the user's words"},
                "max_count": {"type": "integer", "minimum": 1, "maximum": MAX_COUNT, "description": "most applications per run (default 1)"},
                "total": {"type": "integer", "minimum": 0, "description": "optional: most applications over all runs"}},
                "required": ["site", "what"]},
        }, "required": ["trigger", "action"]},
        handler, requires_approval=True, validate=validate, describe=describe, approval_extra=card_lines, hold=account_hold,
        declined=("The user did NOT allow this task to act on its own. When it fires, each step will ask (an approval card then, which "
                  "may come too late). Tell the user that in one sentence; do not ask again."),
    )]


schedule_allow_tools = task_allow_tools   # the first version's name
