"""request_task_permission: one Allow for a repeated step the user asked for ("apply to these 30 campaigns", "send these 12 invites").

Without it every committing click asks (commits.py), so a bulk request means a card per item. With it the agent asks once, up front:
the site, the kind of step, an upper count ("musinsa.com · 체험단 신청 · up to 30"). Once the user allows it, the browser steps of that
kind on that site (its registrable domain) go through without a card, for this task only:

- the task: the chat reply that asked (a job it hands the work to with `delegate` inherits the permission), or the background job
  that asked. It ends with that reply / job (done, failed or stopped), after MAX_HOURS at the latest, or when the user revokes it
  (Work page; DELETE /api/task-permissions/{id});
- the count: each covered click of the step itself (an "apply" / "send" / "delete" click) counts once; when `max_count` is used up
  the next one asks again. Other covered steps (a "Continue" in the form, typing the applicant's details) do not count, but every
  grant stops after STEPS_PER_ITEM x max_count covered steps in all;
- never covered: payments and anything that buys or books ("pay"), sign-ups and account or password changes ("account"), the
  account's protection and official documents ("security": app passwords, 2-step verification, devices, 발급), typing into
  a password field, and deletions unless deleting was the permitted step. checkout_review is untouched.

Many items on many sites ("opt out of all of these senders", "decline these invitations"): the agent first finds the items, then asks
once with the real number and the list (`targets`, shown on the card behind "see the list"), and the sites they are on (`sites`). When
the user asked for all / every one of them (`all`), the count is the number found, up to MAX_ALL (no approving in chunks). For a
low-risk, reversible kind (LOW_RISK: opting out) the permission may also cover `from_links`: any site that a link in the user's own
data read in this task leads to (messages, documents, calendar entries, connected services; see `note_links`), and with `all` more
items of the same kind that turn up while doing it. On many sites one item is one site: its first counted step counts, at most
STEPS_PER_ITEM covered steps there.

Every covered step is written to the approval log (decision "allowed_by_rule", see the server's `on_use`), next to the Allow itself.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..agent import Tool, approval_of_current_call
from ..i18n import N_, t
from .sites import registrable as site_key

MAX_COUNT = 100
MAX_ALL = 1000            # "all of them": the number found, never approved in chunks
MAX_TARGETS = 1000
MAX_SITES = 200
MAX_HOURS = 6.0
STEPS_PER_ITEM = 10
MAX_ACTION = 80
BONUS_PER_ITEM = 3        # model steps one item of an allowed bulk job may take on top of the reply's usual budget
BONUS_CAP = 150

# The kinds a user can allow for a task, and the commit kinds (commits.py) each one covers.
KINDS: dict[str, frozenset[str]] = {
    "apply": frozenset({"apply", "send", "form", "personal"}),    # applications, entries, RSVPs: the form, its details, its submit
    "send": frozenset({"send", "form", "personal"}),              # messages, posts, comments, invitations
    "delete": frozenset({"delete"}),                              # removing many items the user named
    "optout": frozenset({"optout"}),                              # opting out, stopping something being received (reversible)
}
COUNTED: dict[str, frozenset[str]] = {"apply": frozenset({"apply", "send"}), "send": frozenset({"send"}), "delete": frozenset({"delete"}),
                                      "optout": frozenset({"optout"})}
NEVER = frozenset({"account", "security"})
LOW_RISK = frozenset({"optout"})   # spends nothing, sends nothing in the user's name, can be turned back on

DESCRIBE = N_("On {site}: {action}, up to {count} times, only while this task runs. Payments and account changes still ask each time.")
DESCRIBE_MANY = N_("{action}: {count} of them, only while this task runs. Payments, sign-ups and account changes still ask each time.")
DESCRIBE_ALL = N_("{action}: all {count} found, only while this task runs. Payments, sign-ups and account changes still ask each time.")
DESCRIBE_ALL_MORE = N_("{action}: all {count} found, and more of the same kind found while doing it, only while this task runs. "
                       "Payments, sign-ups and account changes still ask each time.")
WHERE_LINKS = N_("Sites the links in what was found lead to")
_URL = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.I)
COVERED = N_("Allowed for this task: {action} ({used}/{count})")


def _site(value: str) -> str:
    text = (value or "").strip()
    host = urlsplit(text if "://" in text else "https://" + text).hostname or ""
    return site_key(host.lower())


@dataclass
class Grant:
    id: str
    site: str                 # registrable domain ("musinsa.com")
    kind: str                 # apply | send | delete
    action: str               # the user's words for the step ("체험단 신청")
    max_count: int
    scopes: set[str]          # "turn:<id>" / "job:<id>": where it holds
    created_at: float
    until: float
    task_id: int | None = None
    approval_id: str = ""
    used: int = 0             # counted steps (the step itself); on many sites: the sites where it was done
    steps: int = 0            # every covered step
    log: list[str] = field(default_factory=list)
    sites: set[str] = field(default_factory=set)      # more sites it holds on (many items, one per site)
    from_links: bool = False  # also any site a link in the user's own data read in this task leads to (LOW_RISK kinds only)
    grows: bool = False       # "all of them": more items of the same kind found while doing it raise the count (LOW_RISK kinds only)
    targets: list[str] = field(default_factory=list)  # the items the user saw on the card
    what: str = ""            # what the items are, in the user's words
    done_sites: set[str] = field(default_factory=set)
    site_steps: dict[str, int] = field(default_factory=dict)

    @property
    def many(self) -> bool:
        return bool(self.sites) or self.from_links

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "site": self.site, "kind": self.kind, "action": self.action, "max_count": self.max_count, "used": self.used,
                "task_id": self.task_id, "approval_id": self.approval_id or None, "created_at": self.created_at, "expires_at": self.until,
                "jobs": sorted(s[4:] for s in self.scopes if s.startswith("job:")), "sites": sorted(self.sites),
                "from_links": self.from_links, "all": self.grows or None, "targets": len(self.targets), "what": self.what}


def current_scope() -> str:
    """The task running now: its background job ("job:<id>") or its chat reply ("turn:<id>"); "" outside a task."""
    from ..workers import current_turn, current_worker
    wid = current_worker.get()
    if wid:
        return f"job:{wid}"
    turn = current_turn.get() or {}
    return f"turn:{turn['turn_id']}" if turn.get("turn_id") else ""


class TaskGrants:
    """The permissions given for running tasks (in memory: a restart ends every task, and with it its permission)."""

    def __init__(self, *, alive: Callable[[str], bool] = lambda _s: True, clock: Callable[[], float] = time.time,
                 scope: Callable[[], str] = current_scope):
        self.alive, self.clock, self.scope = alive, clock, scope
        self.on_use: Callable[[Grant, str, str, str], None] | None = None   # (grant, tool, commit kind, host): the server logs it
        self.on_change: Callable[[], None] | None = None
        self._grants: dict[str, Grant] = {}
        self._links: dict[str, set[str]] = {}   # scope -> sites that links in the user's own data read in that task lead to

    # ------------------------------------------------------------------ keeping them
    def _live(self, g: Grant) -> bool:
        if self.clock() >= g.until:
            return False
        g.scopes = {s for s in g.scopes if self._alive(s)}
        return bool(g.scopes)

    def _alive(self, scope: str) -> bool:
        try:
            return bool(self.alive(scope))
        except Exception:  # noqa: BLE001 - when in doubt the permission is over
            return False

    def _prune(self) -> None:
        for gid in [gid for gid, g in self._grants.items() if not self._live(g)]:
            self._grants.pop(gid, None)

    def add(self, *, site: str = "", kind: str, action: str, max_count: int, task_id: int | None = None, approval_id: str = "",
            scope: str | None = None, sites: list[str] | None = None, from_links: bool = False, all_found: bool = False,
            targets: list[str] | None = None, what: str = "") -> Grant:
        scope = self.scope() if scope is None else scope
        now = self.clock()
        targets = [" ".join(str(x).split())[:120] for x in (targets or []) if str(x).strip()][:MAX_TARGETS]
        cap = MAX_ALL if all_found else MAX_COUNT
        count = max(int(max_count), len(targets)) if all_found else int(max_count)
        low = kind in LOW_RISK
        more = {k for k in (_site(x) for x in (sites or [])[:MAX_SITES]) if k}
        g = Grant("tp" + secrets.token_hex(4), _site(site), kind, action.strip()[:MAX_ACTION], max(1, min(cap, count)),
                  {scope} if scope else set(), now, now + MAX_HOURS * 3600, task_id, approval_id,
                  sites=more, from_links=bool(from_links and low), grows=bool(all_found and low), targets=targets, what=what.strip()[:MAX_ACTION])
        self._grants[g.id] = g
        self._changed()
        return g

    # ------------------------------------------------------------------ links in the user's own data (for from_links)
    def note_links(self, text: str, scope: str | None = None) -> int:
        """The sites the links in `text` lead to: `text` is the user's own data a tool read in this task (a message, a document, an
        entry of a connected service). Returns how many new sites were noted."""
        scope = self.scope() if scope is None else scope
        if not scope or not text:
            return 0
        seen = self._links.setdefault(scope, set())
        before = len(seen)
        for m in _URL.finditer(text[:400_000]):
            if (k := _site(m.group(0).rstrip(".,;:!?"))):
                seen.add(k)
            if len(seen) >= 5000:
                break
        return len(seen) - before

    def linked(self, site: str, scopes: set[str]) -> bool:
        return any(site in self._links.get(s, ()) for s in scopes)

    def inherit(self, from_scope: str, to_scope: str) -> int:
        """A job started by a task that holds permissions works under them too (the same count, shared), with the links it read."""
        n = 0
        for g in self._grants.values():
            if from_scope in g.scopes:
                g.scopes.add(to_scope)
                n += 1
        if from_scope in self._links:
            self._links.setdefault(to_scope, set()).update(self._links[from_scope])
        return n

    def end(self, scope: str) -> None:
        """That task is over: its permissions stop (one shared with a job it started holds on in the job)."""
        for g in self._grants.values():
            g.scopes.discard(scope)
        self._links.pop(scope, None)
        self._prune()
        self._changed()

    def progress(self, scope: str) -> tuple[int, int] | None:
        """How far the items allowed for this task are: (done, total) over its live permissions, or None without any."""
        mine = [g for g in self._grants.values() if scope in g.scopes and self._live(g)]
        if not mine:
            return None
        return sum(g.used for g in mine), sum(g.max_count for g in mine)

    def step_bonus(self, scope: str | None = None) -> int:
        """Extra model steps a task with a live permission for many items may use (agent/loop.py): long bulk work the user allowed
        should not stop half way at the usual step budget."""
        scope = self.scope() if scope is None else scope
        if not scope:
            return 0
        left = sum(max(0, g.max_count - g.used) for g in self._grants.values() if scope in g.scopes and self._live(g))
        return min(BONUS_CAP, left * BONUS_PER_ITEM)

    def revoke(self, gid: str) -> bool:
        ok = self._grants.pop(gid, None) is not None
        if ok:
            self._changed()
        return ok

    def active(self) -> list[dict[str, Any]]:
        self._prune()
        return [g.public() for g in sorted(self._grants.values(), key=lambda g: -g.created_at)]

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ using them
    def covering(self, commit: str, host: str, *, secret: bool = False) -> Grant | None:
        """The permission of the running task that covers this step, or None (then the step asks as usual)."""
        if not commit or commit in NEVER or secret:
            return None
        scope, site = self.scope(), _site(host)
        if not scope or not site:
            return None
        for g in self._grants.values():
            if not (scope in g.scopes and commit in KINDS.get(g.kind, ()) and self._live(g)):
                continue
            if g.many:
                if not (g.site == site or site in g.sites or (g.from_links and self.linked(site, g.scopes))):
                    continue
                if g.site_steps.get(site, 0) >= STEPS_PER_ITEM:
                    continue   # one item is one site: a loop of steps there asks again
                new = commit in COUNTED[g.kind] and site not in g.done_sites
                if new and len(g.done_sites) >= g.max_count and not (g.grows and g.max_count < MAX_ALL):
                    continue
                return g
            if (g.site == site and g.steps < g.max_count * STEPS_PER_ITEM
                    and (commit not in COUNTED[g.kind] or g.used < g.max_count)):
                return g
        return None

    def use(self, commit: str, host: str, *, tool: str = "browser_click", secret: bool = False) -> bool:
        """True = this step is covered (and counted); False = ask the user as usual."""
        g = self.covering(commit, host, secret=secret)
        if g is None:
            return False
        g.steps += 1
        if g.many:
            site = _site(host)
            g.site_steps[site] = g.site_steps.get(site, 0) + 1
            if tool == "browser_click" and commit in COUNTED[g.kind] and site not in g.done_sites:
                if len(g.done_sites) >= g.max_count:
                    g.max_count += 1   # "all of them": one more of the same kind turned up while doing it
                g.done_sites.add(site)
                g.used = len(g.done_sites)
        elif tool == "browser_click" and commit in COUNTED[g.kind]:
            g.used += 1
        if self.on_use is not None:
            try:
                self.on_use(g, tool, commit, host)
            except Exception:  # noqa: BLE001 - the log never blocks the step
                pass
        self._changed()
        return True


def _list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [" ".join(str(x).split())[:120] for x in value if str(x or "").strip()][:limit]


def plan_of(args: dict[str, Any]) -> dict[str, Any]:
    """The call's arguments, cleaned: site, sites, from_links, all, targets, what, kind, action, count (the count to ask for)."""
    try:
        count = int(args.get("max_count") or 0)
    except (TypeError, ValueError):
        count = 0
    targets = _list(args.get("targets"), MAX_TARGETS)
    every = bool(args.get("all"))
    if every and targets:
        count = max(count, len(targets))
    sites = [k for k in dict.fromkeys(_site(x) for x in _list(args.get("sites"), MAX_SITES)) if k]
    return {"site": _site(str(args.get("site") or "")), "sites": sites, "from_links": bool(args.get("from_links")), "all": every,
            "targets": targets, "what": " ".join(str(args.get("what") or "").split())[:MAX_ACTION],
            "kind": str(args.get("kind") or "").strip().lower(), "action": " ".join(str(args.get("action") or "").split())[:MAX_ACTION],
            "count": count}


def many(plan: dict[str, Any]) -> bool:
    return bool(plan["sites"]) or plan["from_links"]


def task_permission_tools(grants: TaskGrants, current_host: Callable[[], str] = lambda: "",
                          task_id: Callable[[], int | None] = lambda: None,
                          owner_request: Callable[[], str] | None = None) -> list[Tool]:
    """`owner_request` is accepted for the server's existing call and NOT read: the owner's words never decide a permission's card
    (docs/no-word-lists.md, 2026-11-16). A card is shown for every request_task_permission; the owner's Allow is the grant."""
    def validate(args: dict[str, Any]) -> str | None:
        p = plan_of(args)
        site, kind, action, count = p["site"], p["kind"], p["action"], p["count"]
        if not site and not many(p):
            return ("say where the steps happen: site (one site, e.g. musinsa.com), sites (one per item when the items are on different "
                    "sites), or from_links (the sites the links in what you found lead to)")
        if kind not in KINDS:
            return ("kind must be apply, send, delete or optout. Payments and bookings go through checkout_review each time; sign-ups and "
                    "account changes always ask")
        if not action:
            return "action is required: the step in the user's language, short (e.g. 'opt out', 'decline the invitation')"
        if p["from_links"] and kind not in LOW_RISK:
            return f"from_links is only for low-risk, reversible steps (kind {', '.join(sorted(LOW_RISK))}); list the sites instead"
        if p["all"] and not p["targets"]:
            return "all needs targets: first find every item, then pass the list (the card shows the user the real number and the list)"
        cap = MAX_ALL if p["all"] else MAX_COUNT
        if not 1 <= count <= cap:
            if count > cap and not p["all"]:
                return (f"max_count must be at most {MAX_COUNT}. If the user asked for all of them, pass every item found in targets with "
                        "all=true: one card covers them all")
            return f"max_count must be between 1 and {cap}: the number of items the user's request needs"
        if not grants.scope():
            return "this works only inside a task (a chat reply or a background job)"
        page = _site(current_host())
        if site and not many(p) and page and page != site:
            return f"the browser is on {page}, not on {site}. Ask for the site you will do the steps on"
        return None

    def card_text(p: dict[str, Any]) -> str:
        if not many(p) and not p["targets"]:
            return t(DESCRIBE, site=p["site"], action=p["action"], count=p["count"])
        if p["all"]:
            return t(DESCRIBE_ALL_MORE if p["kind"] in LOW_RISK else DESCRIBE_ALL, action=p["action"], count=p["count"])
        return t(DESCRIBE_MANY, action=p["action"], count=p["count"])

    async def describe(args: dict[str, Any]) -> str:
        return card_text(plan_of(args))

    def extra(_approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """The card's list (behind "see the list") and where the steps happen, in words."""
        p = plan_of(args)
        where = [p["site"], *p["sites"]] if p["site"] else list(p["sites"])
        shown = ", ".join(where[:5]) + (f" +{len(where) - 5}" if len(where) > 5 else "")
        if p["from_links"]:
            shown = f"{shown} · {t(WHERE_LINKS)}" if shown else t(WHERE_LINKS)
        lines = [{"label": t("Site"), "value": shown}] if shown else []
        return {"lines": lines, "items": p["targets"]}

    async def handler(args: dict[str, Any]) -> str:
        p = plan_of(args)
        g = grants.add(site=p["site"], kind=p["kind"], action=p["action"], max_count=p["count"], task_id=task_id(),
                       approval_id=approval_of_current_call(), sites=p["sites"], from_links=p["from_links"], all_found=p["all"],
                       targets=p["targets"], what=p["what"])
        if not g.many:
            return (f"ALLOWED for this task only: '{p['action']}' on {g.site} (kind {g.kind}) up to {g.max_count} times. Those browser steps "
                    f"on {g.site} now go through without a card; when the count is used up, the next one asks again. Payments, bookings, "
                    "sign-ups, account or password changes still ask each time. Do only what the user asked; stop and tell them if the site "
                    "asks for anything else (money, a new account, personal details they did not give).")
        where = ", ".join(sorted(({g.site} if g.site else set()) | g.sites)[:20])
        links = " and on the sites the links in what you found lead to" if g.from_links else ""
        more = " More items of the same kind found while doing it are covered too." if g.grows else ""
        return (f"ALLOWED for this task only: '{p['action']}' (kind {g.kind}) for {g.max_count} items, one item per site, on {where or 'no listed site'}"
                f"{links}.{more} Those browser steps now go through without a card; do the items one after another without asking again. "
                "Payments, bookings, sign-ups, account or password changes still ask each time. Do only what the user asked; skip an item "
                "and say so if its site asks for anything else (money, a new account, personal details they did not give).")

    return [Tool(
        "request_task_permission",
        "When the user asked for the same kind of step on many items (all / every / these N), ask once before the first: find the items "
        "first, then pass the real count and the list (targets; all=true when they said all or every). Once allowed, those steps go "
        "through without a card, for this task only. Never for payments, bookings, sign-ups or account changes.",
        {"type": "object", "properties": {
            "kind": {"type": "string", "enum": sorted(KINDS),
                     "description": "apply (applications, entries), send (messages, posts, invitations), delete (removing items), "
                                    "optout (opting out, stopping something being received: low risk, reversible)"},
            "action": {"type": "string", "description": "the step in the user's language, short"},
            "max_count": {"type": "integer", "minimum": 1, "maximum": MAX_ALL, "description": "how many items (with all: the number found)"},
            "site": {"type": "string", "description": "the site's address, when every step is on one site"},
            "sites": {"type": "array", "items": {"type": "string"}, "description": "the items' sites, when they are on different sites"},
            "from_links": {"type": "boolean", "description": "optout only: also any site a link in the user's own data you read in this "
                                                             "task leads to"},
            "targets": {"type": "array", "items": {"type": "string"}, "description": "the items found, one short line each (shown to "
                                                                                     "the user as the list)"},
            "all": {"type": "boolean", "description": "the user asked for all / every one of them: the count is the number found"},
            "what": {"type": "string", "description": "what the items are, in the user's language, plural (shown in the question)"},
            "reason": {"type": "string", "description": "what the user asked for, in a few words"},
        }, "required": ["kind", "action", "max_count"]},
        handler, requires_approval=True, validate=validate, describe=describe, approval_extra=extra,
        declined=("The user did NOT allow these steps for the whole task. Each such step will ask on its own; do only a few, or ask the "
                  "user how they want to go on."),
    )]
