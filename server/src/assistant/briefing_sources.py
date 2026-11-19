"""Which connected accounts the morning briefing and the proactive pass read.

EVERY connected calendar (the phones', Google, Outlook, CalDAV: iCloud, Yahoo, Naver, an account of your own, a Nest connector that
provides one) and EVERY connected mailbox (IMAP: Gmail, Naver, Daum, iCloud, Yahoo, Gmail by Google sign-in; Outlook / Hotmail /
Microsoft 365 by Graph; a Nest connector that provides mail: mailboxes.py) is read, unless the owner switched that one account off.
Each account has one switch; an account connected later starts on.

    config  {"calendar": {"off": [...]}, "mail": {"off": [...]}}      (meta "briefing:sources")
Account ids: calendars "phone", "google:<item>", "outlook:microsoft-api", "caldav:<item>", "nest:<package>"; mail "mail:<name>"
(mail:mail-gmail, mail:outlook, mail:nest-<package>).
Older servers stored {"mode": "all"|"only", "ids": [...]} per group; it is read once and saved in the new shape: an account that was
left out ("all": in ids; "only": connected but not in ids) stays off, everything else is on.

A failing account never stops the others: the reason is remembered (meta "briefing:source-status") and shown as one quiet line in the
settings ("Could not read Naver Calendar: ..." with what to do), and the account is skipped until it works again.

API (the app's briefing settings):
GET  /api/briefing/sources      {"calendar": Group, "mail": Group}
       Group: {"accounts": [{"id", "kind", "provider", "name", "address", "label", "on": bool, "problem": str|null}]}
PUT  /api/briefing/sources      {"calendar"?: {"accounts": {id: bool}}, "mail"?: {...}} -> as GET; 422 {"detail"}
POST /api/briefing/sources/check  reads every account once now (what the briefing would do) and records the result -> as GET

The agent's tool `briefing_sources` flips the same switches from the chat; it needs no approval card (it only narrows or restores
what Kelo reads). The agent's tool `calendar_events` reads the same chosen calendars for the chat (all-day events included).
Cost per account is bounded instead of the number of accounts: each calendar and mailbox is read at the same time within its own time
limit (calendars.ACCOUNT_TIMEOUT, proactive.MAIL_ACCOUNT_TIMEOUT; the proactive pass reads every unread mail since its last look in
each mailbox, with proactive.ARRIVALS_TIMEOUT); one
that runs over is skipped this time and shown as "slow".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .agent import Ours, Tool
from .agent.fence import data, marked
from .calendars import Calendars, classify
from .event_search import matches, no_match_line, query_terms
from .i18n import N_, t
from .google_calendar import parse_when, when_text
from .mail import PROVIDERS as MAIL
from .proactive import ARRIVALS_TIMEOUT, MAIL_ACCOUNT_TIMEOUT
from .tools.connect_tools import asked_in_chat


log = logging.getLogger("uvicorn.error")

CONFIG_META, STATUS_META = "briefing:sources", "briefing:source-status"
KINDS = ("calendar", "mail")
MAX_SHOWN = 150
LIST_DAYS = 62          # calendar_events without query: a list over at most two months
SEARCH_DAYS = 550       # with query: a search over about 18 months in one call (no model runs, so a wide search is cheap)
SEARCH_PAGES = 8        # pages Google and Outlook may return for such a long search


def search_terms(query: Any) -> list[str]:
    """calendar_events' query as distinct lowercased words: a list of terms, or one string (older calls)."""
    raw = query if isinstance(query, list) else [query] if isinstance(query, str) else []
    out: list[str] = []
    for q in raw:
        w = str(q or "").strip().casefold()[:100]
        if w and w not in out:
            out.append(w)
    return out[:20]
BRAND = {"gmail": "Gmail", "icloud": "iCloud", "yahoo": "Yahoo", "naver": "Naver", "daum": "Daum", "google": "Google", "outlook": "Outlook",
         "caldav": "CalDAV"}

LINE = N_("Could not read {name}: {reason}")
REASON = {"auth": N_("The sign-in was refused."),
          "off": N_("CalDAV is not turned on for this account, or the provider does not offer it."),
          "unreachable": N_("The server could not be reached. It will be tried again."),
          "other": N_("Something went wrong. It will be tried again."),
          "slow": N_("It took too long, so it was skipped this time. It will be tried again.")}
AGAIN = N_("Connect it again in the Connectors tab.")
MAIL_AGAIN = N_("Check that IMAP is turned on, then connect the mailbox again in the Connectors tab.")


@dataclass(frozen=True)
class Acct:
    id: str
    kind: str          # calendar | mail
    provider: str
    name: str          # N_ key of the provider's name
    address: str
    item: str = ""


def _default() -> dict[str, Any]:
    return {k: {"off": []} for k in KINDS}


class Sources:
    def __init__(self, store: Any, calendars: Calendars, mail: Any, clock: Callable[[], float] = time.time):
        self.store, self.calendars, self.mail, self.clock = store, calendars, mail, clock
        # the owner's per-connector limits (permissions.py): False = the chat's calendar_events may not read this calendar. The briefing and
        # the heads-ups keep to the switches above.
        self.owner_allows: Callable[[Acct], bool] | None = None

    # ------------------------------------------------------------------ the accounts
    def accounts(self, kind: str) -> list[Acct]:
        if kind == "calendar":
            return [Acct(a.id, "calendar", a.provider, a.name, a.address, a.item) for a in self.calendars.accounts()]
        out = []
        for a in self.mail.accounts():
            p = MAIL.get(a["provider"])
            out.append(Acct(f"mail:{a['name']}", "mail", a["provider"], p.name if p else a.get("title") or a["provider"], a.get("address", ""),
                            a["name"]))
        return out

    # ------------------------------------------------------------------ what is switched off
    def config(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.store.meta(CONFIG_META) or "{}")
        except ValueError:
            raw = {}
        out, old = _default(), False
        for k in KINDS:
            g = raw.get(k) if isinstance(raw, dict) else None
            if not isinstance(g, dict):
                continue
            ids = [str(i) for i in (g.get("off") if "off" in g else g.get("ids")) or [] if isinstance(i, str)][:100]
            if "off" in g:
                out[k] = {"off": ids}
            elif g.get("mode") == "only":   # an older server's "only these": every account connected now and not chosen stays off
                old = True
                out[k] = {"off": [a.id for a in self.accounts(k) if a.id not in ids]}
            elif g.get("mode") == "all":     # an older server's "all except": the excluded ones stay off
                old = True
                out[k] = {"off": ids}
        if old:
            self._save(out)
        return out

    def _save(self, cfg: dict[str, Any]) -> None:
        self.store.set_meta(CONFIG_META, json.dumps(cfg))

    @staticmethod
    def _on(group: dict[str, Any], acct_id: str) -> bool:
        return acct_id not in group["off"]

    def wanted(self, kind: str, acct_id: str) -> bool:
        return self._on(self.config()[kind], acct_id)

    def selected(self, kind: str) -> list[Acct]:
        group = self.config()[kind]
        return [a for a in self.accounts(kind) if self._on(group, a.id)]

    def set_group(self, kind: str, accounts: dict[str, bool] | None = None) -> None:
        """The app's switches: `accounts` {id: on}."""
        if kind not in KINDS:
            raise ValueError("kind must be calendar or mail")
        cfg = self.config()
        known = [a.id for a in self.accounts(kind)]
        for acct_id in accounts or {}:
            if acct_id not in known:
                raise ValueError(f"no such account: {acct_id}")
        self._switch(cfg, kind, [i for i, on in (accounts or {}).items() if on], [i for i, on in (accounts or {}).items() if not on])
        self._save(cfg)

    @staticmethod
    def _switch(cfg: dict[str, Any], kind: str, on: list[str], off: list[str]) -> None:
        ids = [i for i in cfg[kind]["off"] if i not in on]
        cfg[kind] = {"off": ids + [i for i in off if i not in ids]}

    # ------------------------------------------------------------------ what happened when it was read
    def _status(self) -> dict[str, Any]:
        try:
            v = json.loads(self.store.meta(STATUS_META) or "{}")
        except ValueError:
            v = {}
        return v if isinstance(v, dict) else {}

    def report(self, acct_id: str, code: str, detail: str = "") -> None:
        st = self._status()
        if code == "ok":
            if acct_id not in st:
                return
            st.pop(acct_id)
        else:
            st[acct_id] = {"code": code, "detail": detail[:200], "at": self.clock()}
        live = {a.id for k in KINDS for a in self.accounts(k)}
        self.store.set_meta(STATUS_META, json.dumps({k: v for k, v in st.items() if k in live}))

    def problem(self, a: Acct, st: dict[str, Any] | None = None) -> str | None:
        got = (st if st is not None else self._status()).get(a.id)
        if not got:
            return None
        code = got.get("code") if got.get("code") in REASON else "other"
        reason = t(REASON[code])
        if code == "off" and a.kind == "mail":
            reason = t(REASON["other"])
        if code in ("auth", "off") and a.kind == "calendar":
            from .calendars import CALDAV
            p = CALDAV.get(a.provider)
            reason += " " + (t(p.howto) if p else t(AGAIN))
        elif code == "auth":
            reason += " " + (t(MAIL_AGAIN) if a.kind == "mail" and a.provider in MAIL else t(AGAIN))
        return t(LINE, name=t(a.name), reason=reason)

    # ------------------------------------------------------------------ labels
    def label(self, a: Acct, peers: list[Acct]) -> str:
        """The short name shown on a card: the provider, or the address when two accounts share the provider."""
        brand = t(a.name) if a.provider == "phone" else BRAND.get(a.provider, t(a.name))
        same = [p for p in peers if (BRAND.get(p.provider) or p.provider) == (BRAND.get(a.provider) or a.provider)]
        return a.address if len(same) > 1 and a.address else brand

    def multiple(self, kind: str) -> bool:
        return len(self.selected(kind)) > 1

    def labels(self, kind: str) -> dict[str, str]:
        peers = self.selected(kind)
        return {a.id: self.label(a, peers) for a in peers}

    def public(self) -> dict[str, Any]:
        cfg, st = self.config(), self._status()
        out: dict[str, Any] = {}
        for k in KINDS:
            accts = self.accounts(k)
            labels = {a.id: self.label(a, accts) for a in accts}
            out[k] = {"accounts": [
                {"id": a.id, "kind": k, "provider": a.provider, "name": t(a.name), "address": a.address, "label": labels[a.id],
                 "on": self._on(cfg[k], a.id), "problem": self.problem(a, st)} for a in accts]}
        return out

    # ------------------------------------------------------------------ reading (briefing.py, proactive.py)
    async def upcoming_events(self, hours: float = 24, *, all_day: bool = False, pages: int = 1) -> list[dict[str, Any]]:
        """The next `hours` of events of the calendars chosen (all-day ones too with `all_day`), each with "source" (a label) when more
        than one calendar is read."""
        group = self.config()["calendar"]
        evs = await self.calendars.upcoming(hours, wanted=lambda a: self._on(group, a.id), report=lambda a, c, d: self.report(a.id, c, d),
                                            **({"all_day": True} if all_day else {}), **({"pages": pages} if pages > 1 else {}))
        if self.multiple("calendar"):
            names = self.labels("calendar")
            evs = [{**e, "source": names.get(e.get("source_id", ""), "")} for e in evs]
        return evs

    def mail_view(self) -> "SelectedMail":
        return SelectedMail(self)

    async def check(self) -> None:
        """Read every account once, as the briefing would, so the settings show what is wrong before tomorrow morning."""
        await self.calendars.upcoming(24, report=lambda a, c, d: self.report(a.id, c, d))
        view = self.mail_view()
        await asyncio.gather(*(view.list(a.item, unread=True, limit=1) for a in self.accounts("mail")))

    # ------------------------------------------------------------------ reading for the chat
    async def events(self, start: datetime, end: datetime, query: Any = "", *,
                     pages: int = 1, tool: str = "calendar_events") -> tuple[list[dict[str, Any]], list[Acct], list[Acct], list[Acct]]:
        """(events of the chosen calendars overlapping [start, end), all-day ones included, each with "source"; the calendars read; the
        ones left out by the owner's choice; the ones that could not be read). `query`: words (event_search.query_terms), an event with
        any of them in its title, place or notes is kept. `tool`: who reads, for the vault's use log."""
        evs, _, read, left, failed = await self.events_and_total(start, end, query, pages=pages, tool=tool)
        return evs, read, left, failed

    async def events_and_total(self, start: datetime, end: datetime, query: Any = "", *, pages: int = 1,
                               tool: str = "calendar_events") -> tuple[list[dict[str, Any]], int, list[Acct], list[Acct], list[Acct]]:
        """As events(), plus the number of events in the range before the words filter it (so an empty search can say so). The same
        event from two calendars is listed once: the first copy is kept, the other's calendar goes to its "also" and a mail mark or the
        account of either copy is carried over."""
        group = self.config()["calendar"]
        accts = self.accounts("calendar")
        failed: list[str] = []

        def on(a: Any) -> bool:
            return self._on(group, a.id) and (self.owner_allows is None or self.owner_allows(a))

        def note(a: Any, code: str, detail: str) -> None:
            self.report(a.id, code, detail)
            if code != "ok":
                failed.append(a.id)
        evs = await self.calendars.read(start, end, all_day=True, wanted=on, report=note, tool=tool,
                                        **({"pages": pages} if pages > 1 else {}))
        read = [a for a in accts if on(a)]
        names = {a.id: self.label(a, read) for a in read}
        terms = query_terms(query)
        kept: dict[tuple[str, int], dict[str, Any]] = {}
        everything: list[dict[str, Any]] = []
        for e in evs:
            labelled = {**e, "source": names.get(e["source_id"], "")}
            key = (e["title"].strip().lower(), int(e["start"]) // 60)
            if key in kept:   # the same event from two calendars (a Google calendar synced into the phone): listed once
                first = kept[key]
                if labelled["source"] and labelled["source"] != first["source"]:
                    first.setdefault("also", []).append(labelled["source"])
                first["account"] = first.get("account") or labelled.get("account", "")
                first["own"] = bool(first.get("own")) and bool(labelled.get("own"))   # the owner's own only when every copy is
                continue
            kept[key] = labelled
            everything.append(labelled)
        out = [e for e in everything if matches(terms, e["title"], e.get("where", ""), e.get("notes", ""))]
        return out, len(everything), [a for a in read if a.id not in failed], [a for a in accts if not on(a)], [a for a in read if a.id in failed]

    def calendar_tool(self, timezone_name: Callable[[], str | None]) -> Tool:
        async def calendar_events(args: dict[str, Any]) -> str:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                tz: Any = ZoneInfo(timezone_name() or "UTC")
            except (ZoneInfoNotFoundError, ValueError):
                tz = ZoneInfo("UTC")
            try:
                start = parse_when(str(args["start"]), tz)[0] if args.get("start") else datetime.now(tz)
                end = parse_when(str(args["end"]), tz, end_of_day=True)[0] if args.get("end") else start + timedelta(days=7)
            except Exception as e:  # noqa: BLE001 - the words say what was wrong
                return f"error: {e}"
            if end <= start:
                return "error: end is before start"
            terms = query_terms(args.get("query"))
            if end - start > timedelta(days=SEARCH_DAYS if terms else LIST_DAYS):
                return ("error: a search covers at most 18 months at a time" if terms else
                        "error: list at most two months at a time; to find something over a longer span, give query")
            evs, total, read, left, failed = await self.events_and_total(start, end, terms, pages=SEARCH_PAGES if end - start > timedelta(days=LIST_DAYS) else 1)
            if not read and not failed:
                return ("No calendar is read: " + ("the user left every calendar out of what Kelo reads (they can change it with "
                        "briefing_sources)." if left else "none is connected. They can share the phone's calendar or connect one with connect_service."))
            lines = ["Calendars read: " + ", ".join(f"{t(a.name)}" + (f" ({a.address})" if a.address else "") for a in read) + "."]
            if failed:
                lines.append("Could not read now: " + ", ".join(t(a.name) for a in failed) + " (the user sees why under the briefing settings).")
            if left:
                lines.append("Left out by the user's choice: " + ", ".join(t(a.name) for a in left) + ".")
            zone = getattr(tz, "key", "UTC")
            span = f"between {start.strftime('%Y-%m-%d %H:%M')} and {end.strftime('%Y-%m-%d %H:%M')} ({zone})"
            if not evs:
                lines.append(no_match_line(terms, span, total) if terms else f"No events {span}.")
                return Ours("\n".join(lines))   # the app's own words: the calendars read and the span
            lines.append(f"Events ({zone}), {len(evs)}" + (f", the first {MAX_SHOWN} shown" if len(evs) > MAX_SHOWN else "") + ":")
            for e in evs[:MAX_SHOWN]:
                a = datetime.fromtimestamp(e["start"], tz)
                b = datetime.fromtimestamp(e["end"], tz) if e.get("end") else None
                if e.get("all_day") and e.get("day"):
                    a = datetime.fromisoformat(e["day"]).replace(tzinfo=tz)
                    b = a + timedelta(days=1) if b is None or b <= a else b
                when = when_text(a, b, bool(e.get("all_day")))
                source = e.get("source", "") if len(read) > 1 else ""
                if source and e.get("source_id") == "phone" and e.get("account") and e.get("account_type", "").lower() != "local":
                    source += f" (account {e['account']})"
                # an event the owner made on a calendar they own is their own data; any other event's title and place are others' text
                title, where = e["title"] or "(no title)", e.get("where", "")
                if not e.get("own"):
                    title = data(title)
                    where = data(where) if where else ""
                lines.append(f"- {when} · {title}" + (f" · at {where}" if where else "")
                             + (f" · {source}" if source else "")
                             + (f", also in {', '.join(e['also'])}" if e.get("also") else "")
                             )
            text = "\n".join(lines)
            return text if marked(text) else Ours(text)

        when = "YYYY-MM-DD or YYYY-MM-DDTHH:MM, the user's time zone"
        return Tool("calendar_events",
                    "The user's events from every calendar they connected (the phone's and each connected account or package), in one list, "
                    "following their choice of which calendars Kelo reads. Use it for what is on a day and to find the day of something the "
                    "user names (default: the next 7 days; at most 2 months, or 18 months with query). To find an event, search once: put "
                    "every spelling, language variant and short code of its name in query, matched as OR. Read only; to change an event use "
                    "the tools of its own calendar.",
                    {"type": "object", "properties": {"start": {"type": "string", "description": when}, "end": {"type": "string", "description": when},
                                                      "query": {"type": "array", "items": {"type": "string"},
                                                                "description": "words to look for; an event whose title, place or notes contains "
                                                                               "any one of them is shown (any case)"}}},
                    calendar_events, read_only=True, untrusted=True, source="calendar", trail="calendar")

    # ------------------------------------------------------------------ the agent's tool
    def _match(self, ref: str, kind: str | None) -> list[Acct]:
        r = ref.strip().lower()
        if not r:
            return []
        pool = [a for k in KINDS if kind in (None, k) for a in self.accounts(k)]
        exact = [a for a in pool if r in (a.id.lower(), a.address.lower(), a.item.lower())]
        if exact:
            return exact
        return [a for a in pool if r in a.address.lower() or r in a.provider.lower() or r in t(a.name).lower() or r in a.name.lower()
                or r in BRAND.get(a.provider, "").lower()]

    def describe(self) -> str:
        pub = self.public()
        lines = []
        for k, title in (("calendar", "Calendars"), ("mail", "Mailboxes")):
            g = pub[k]
            if not g["accounts"]:
                lines.append(f"{title}: none connected.")
                continue
            lines.append(f"{title}:")
            for a in g["accounts"]:
                lines.append(f"- {a['id']} | {a['name']} | {a['address'] or '-'} | read: {'yes' if a['on'] else 'no'}"
                             + (f" | problem: {a['problem']}" if a["problem"] else ""))
        return "\n".join(lines)

    def tool(self) -> Tool:
        async def briefing_sources(args: dict[str, Any]) -> str:
            action = str(args.get("action") or "show").strip().lower()
            if action == "show":
                return self.describe()
            if action not in ("on", "off"):
                return "error: action must be show, on or off"
            kind = str(args.get("kind") or "").strip().lower() or None
            if kind not in (None, "calendar", "mail", "both"):
                return "error: kind must be calendar, mail or both (or left out when the accounts are only of one kind)"
            both = kind == "both"
            kind = None if both else kind
            refs = [str(x) for x in (args.get("accounts") or []) if str(x).strip()] if isinstance(args.get("accounts"), list) else \
                ([str(args["accounts"])] if args.get("accounts") else [])
            if not refs:
                return "error: say which accounts (an id, address or provider name from action=show, or \"all\")"
            matched: dict[str, list[Acct]] = {}
            for ref in refs:
                if ref.strip().lower() == "all":
                    found = [a for k in KINDS if kind in (None, k) for a in self.accounts(k)]
                else:
                    found = self._match(ref, kind)
                    if not found:
                        return f"error: no connected account matches {ref!r}. Use action=show to see them, or ask the user which one they mean.\n" + self.describe()
                    if not both and kind is None and len({a.kind for a in found}) > 1:
                        return (f"error: {ref!r} is both a calendar and a mailbox. Say kind=calendar, kind=mail or kind=both, "
                                "whichever the user meant (ask them if it is not clear).\n" + self.describe())
                for a in found:
                    matched.setdefault(a.kind, [])
                    if a not in matched[a.kind]:
                        matched[a.kind].append(a)
            if not matched:
                return "error: no account is connected.\n" + self.describe()
            cfg = self.config()
            for k, accts in matched.items():
                ids = [a.id for a in accts]
                self._switch(cfg, k, ids if action == "on" else [], ids if action == "off" else [])
            self._save(cfg)
            names = ", ".join(f"{a.address or t(a.name)} ({a.kind})" for accts in matched.values() for a in accts)
            return f"ok: switched {action}: {names}.\n" + self.describe()

        acct = {"type": "array", "items": {"type": "string"},
                "description": "account ids, addresses or provider names as listed by action=show (e.g. 'work@company.com', 'naver'), or 'all'"}

        def switches(args: dict[str, Any]) -> bool:
            """A switch (on or off) asks: turning a source on widens what is read, turning it off silences its heads-ups. A view does not."""
            return str(args.get("action") or "show").strip().lower() in ("on", "off")

        async def describe(args: dict[str, Any]) -> str:
            raw = args.get("accounts")
            names = (", ".join(str(x) for x in raw) if isinstance(raw, list) else str(raw or "")).strip()[:120] or "all"
            if str(args.get("action") or "").strip().lower() == "on":
                return t("Let Kelo read {accounts} for the briefing, heads-ups and calendar questions?", accounts=names)
            return t("Stop Kelo reading {accounts} for the briefing, heads-ups and calendar questions?", accounts=names)

        return Tool("briefing_sources",
                    "Switch connected calendars and mailboxes on or off for what Kelo reads by itself (the morning briefing, heads-ups, the "
                    "calendar_events tool). All are on unless the user switched one off. Use it for 'leave my work mail out' (off) or "
                    "'put it back' (on). Asked in this chat a switch goes ahead; otherwise the user approves each one on a card; show needs no approval. Call action=show first when you do not "
                    "know the accounts; afterwards say in one short line what changed, naming the accounts.",
                    {"type": "object", "properties": {
                        "action": {"type": "string", "enum": ["show", "on", "off"],
                                   "description": "show: list the accounts and their switches; on / off: switch these accounts"},
                        "kind": {"type": "string", "enum": ["calendar", "mail", "both"],
                                 "description": "calendar or mail when the user named only one; both when they meant both; optional when "
                                                "the accounts are all of one kind"},
                        "accounts": acct}, "required": ["action"]},
                    briefing_sources, requires_approval=True, auto_approve=lambda a: not switches(a) or asked_in_chat(), describe=describe)


class SelectedMail:
    """The mail object the briefing and the proactive pass read through: only the mailboxes chosen, and a mailbox that fails is
    recorded and skipped (an empty list) instead of stopping the pass."""

    def __init__(self, sources: Sources):
        self.sources = sources

    def accounts(self) -> list[dict[str, Any]]:
        chosen = {a.item for a in self.sources.selected("mail")}
        return [a for a in self.sources.mail.accounts() if a["name"] in chosen]   # every chosen one; the cost is bounded per account

    async def list(self, account: str, **kw: Any) -> list[dict[str, Any]]:
        try:
            got = await asyncio.wait_for(self.sources.mail.list(account, **kw), MAIL_ACCOUNT_TIMEOUT)
        except asyncio.TimeoutError:
            log.info("mail %s took too long", account)
            self.sources.report(f"mail:{account}", "slow", f"no answer within {int(MAIL_ACCOUNT_TIMEOUT)} seconds")
            return []
        except Exception as e:  # noqa: BLE001 - one mailbox failing never stops the others
            log.info("mail %s failed (%s)", account, str(e)[:120])
            self.sources.report(f"mail:{account}", classify(e), str(e))
            return []
        self.sources.report(f"mail:{account}", "ok")
        return got

    async def arrivals(self, account: str, cursor: dict[str, Any] | None, kind: str = "inbox") -> dict[str, Any] | None:
        """What came in after `cursor` (mailboxes.Mailboxes.arrivals). Unlike list(), a mailbox that fails or is slow raises after being
        recorded: the caller must not move its cursor on as if nothing had arrived."""
        try:
            got = await asyncio.wait_for(self.sources.mail.arrivals(account, cursor, kind), ARRIVALS_TIMEOUT)
        except asyncio.TimeoutError:
            self.sources.report(f"mail:{account}", "slow", f"no answer within {int(ARRIVALS_TIMEOUT)} seconds")
            raise
        except Exception as e:  # noqa: BLE001 - recorded, then the caller keeps its old cursor
            log.info("mail %s failed (%s)", account, str(e)[:120])
            self.sources.report(f"mail:{account}", classify(e), str(e))
            raise
        self.sources.report(f"mail:{account}", "ok")
        return got


class GroupIn(BaseModel):
    accounts: dict[str, bool] | None = None


class SourcesIn(BaseModel):
    calendar: GroupIn | None = None
    mail: GroupIn | None = None


def sources_routes(app: FastAPI, sources: Sources, auth: Callable[..., Any]) -> None:
    @app.get("/api/briefing/sources", dependencies=[Depends(auth)])
    async def get_sources() -> dict[str, Any]:
        return sources.public()

    @app.put("/api/briefing/sources", dependencies=[Depends(auth)])
    async def put_sources(body: SourcesIn) -> dict[str, Any]:
        try:
            for k in KINDS:
                g = getattr(body, k)
                if g is not None:
                    sources.set_group(k, accounts=g.accounts)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return sources.public()

    @app.post("/api/briefing/sources/check", dependencies=[Depends(auth)])
    async def check_sources() -> dict[str, Any]:
        await sources.check()
        return sources.public()


__all__ = ["Acct", "SelectedMail", "Sources", "sources_routes"]
