"""Mailboxes and calendars that Kelo Nest connectors provide (`provides` in their manifest: nest_manifest.parse_provides), behind the
same interfaces as the built-in ones, so the chat's email and calendar tools, the morning briefing, the heads-ups and the accounts list
in the briefing settings read them with no change to Kelo.

NestMail is one more backend of mailboxes.Mailboxes (accounts "nest-<package id>"): list, read, and the changes the package maps
(mark read, mark unread when it has it, archive, trash). A change picks messages by the uids email_list gave; a whole search is not
offered (a package's tools may not search the same way). Sending is not one of the operations: that stays with the package's own tools.
NestCalendars is one more kind of account of calendars.Calendars ("nest:<package id>").

The package's service answers through nest.Nest.provide: only its declared hosts and tools, with the owner's sign-in, and only while the
owner lets it read that kind of data. Its answers are other people's words, fenced like any mail or event text.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from .mail import MailError, Selection
from .microsoft_mail import UidMap
from .nest import NestError, _get

PREFIX = "nest-"
CHANGES = {"read": "mark_read", "unread": "mark_unread", "archive": "archive", "trash": "trash"}


def _rows(answer: Any, select: str) -> list[Any]:
    got = _get(answer, select) if select else answer
    if isinstance(got, Mapping):
        for k in ("items", "messages", "events", "value", "results", "data"):
            if isinstance(got.get(k), list):
                return list(got[k])
        return [got]
    return list(got) if isinstance(got, list) else []


def _field(row: Any, fields: Mapping[str, str], name: str) -> Any:
    path = fields.get(name)
    return _get(row, path) if path else None


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, Mapping):   # {"name", "email"} / {"emailAddress": {...}}
        inner = v.get("emailAddress") if isinstance(v.get("emailAddress"), Mapping) else v
        name, addr = str(inner.get("name") or ""), str(inner.get("email") or inner.get("address") or "")
        return f"{name} <{addr}>" if name and addr else addr or name
    if isinstance(v, (list, tuple)):
        return ", ".join(_text(x) for x in v)
    return str(v)


def when(v: Any, tz: Any) -> tuple[float | None, bool]:
    """(unix time, all day) from a number (seconds or milliseconds), an ISO time (no offset: the owner's time zone) or a date alone."""
    if isinstance(v, Mapping):   # {"dateTime"} / {"date"} as calendars write it
        v = v.get("dateTime") or v.get("date_time") or v.get("date")
    if isinstance(v, bool) or v is None:
        return None, False
    if isinstance(v, (int, float)):
        return (float(v) / 1000 if v > 1e11 else float(v)), False
    s = str(v).strip()
    try:
        if len(s) == 10:
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day, tzinfo=tz).timestamp(), True
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=tz)).timestamp(), False
    except ValueError:
        return None, False


class NestMail:
    def __init__(self, nest: Any, store: Any):
        self.nest, self.store = nest, store

    def _pkg(self, account: str) -> Any:
        pid = account.removeprefix(PREFIX)
        p = next((p for p in self.nest.providing("mail") if p.id == pid), None)
        if p is None:
            raise MailError(f"no mailbox {account!r} is connected")
        return p

    def accounts(self) -> list[dict[str, Any]]:
        return [{"name": PREFIX + p.id, "provider": f"nest:{p.id}", "label": p.text("name"), "address": "", "title": p.text("name")}
                for p in self.nest.providing("mail")]

    async def _op(self, p: Any, op: str, values: dict[str, Any]) -> Any:
        try:
            return await self.nest.provide(p, "mail", op, values)
        except NestError as e:
            raise MailError(str(e)) from None

    async def list(self, account: str = "", folder: str = "INBOX", query: str = "", limit: int = 10, unread: bool = False,
                   skip_bulk: bool = False) -> list[dict[str, Any]]:
        p = self._pkg(account)
        limit = max(1, min(int(limit or 10), 30))
        spec = p.provides["mail"]["list"]
        answer = await self._op(p, "list", {"unread": bool(unread), "limit": limit, "query": query, "folder": folder or "INBOX"})
        fields = spec.get("fields") or {}
        rows = [r for r in _rows(answer, spec.get("select", "")) if _field(r, fields, "uid") not in (None, "")][:limit * 3]
        nums = UidMap(self.store, PREFIX + p.id).numbers([str(_field(r, fields, "uid")) for r in rows])
        out = []
        for r, n in zip(rows, nums):
            flag = _field(r, fields, "unread")
            out.append({"uid": n, "from": _text(_field(r, fields, "from")), "subject": _text(_field(r, fields, "subject")),
                        "date": _text(_field(r, fields, "date")), "unread": bool(flag) if flag is not None else bool(unread),
                        "bulk": bool(_field(r, fields, "bulk"))})
        if unread:
            out = [m for m in out if m["unread"]]
        return ([m for m in out if not m["bulk"]] if skip_bulk else out)[:limit]

    async def arrivals(self, account: str, cursor: dict[str, Any] | None, kind: str = "inbox") -> dict[str, Any] | None:
        """What a package's mailbox shows as unread, not bulk, when asked (a package gives no received time to page by, so the ledger
        of proactive.py keeps the same message from being judged twice); the first look only sets the cursor. Only kind "inbox"."""
        import time
        fresh = {"t": time.time()}
        if kind != "inbox":
            return None
        if cursor is None:
            return {"rows": [], "cursor": fresh}
        rows = await self.list(account, unread=True, limit=30, skip_bulk=True)
        return {"rows": [{**m, "arrived": 0.0, "snippet": ""} for m in reversed(rows)], "cursor": fresh}

    async def read(self, account: str, uid: str, folder: str = "INBOX") -> dict[str, Any]:
        p = self._pkg(account)
        mid = UidMap(self.store, PREFIX + p.id).ids([uid])[0]
        spec = p.provides["mail"]["read"]
        answer = await self._op(p, "read", {"uid": mid})
        rows = _rows(answer, spec.get("select", ""))
        r = rows[0] if rows else {}
        fields = spec.get("fields") or {}
        return {"uid": uid, "from": _text(_field(r, fields, "from")), "to": _text(_field(r, fields, "to")), "cc": _text(_field(r, fields, "cc")),
                "subject": _text(_field(r, fields, "subject")), "date": _text(_field(r, fields, "date")), "message_id": "", "references": "",
                "text": _text(_field(r, fields, "text"))[:48_000], "attachments": []}

    def _ids(self, p: Any, action: str, sel: Selection) -> list[tuple[str, str]]:
        if action not in CHANGES or CHANGES[action] not in p.provides["mail"]:
            raise MailError(f"{p.text('name')} can not {action} messages from Kelo; its own tools may")
        if not sel.uids:
            raise MailError(f"{p.text('name')} changes messages picked by the uids email_list gave, not by a search")
        return list(zip(sel.uids, UidMap(self.store, PREFIX + p.id).ids(list(sel.uids))))

    async def plan(self, account: str, action: str, sel: Selection, to: str = "") -> dict[str, Any]:
        p = self._pkg(account)
        todo = self._ids(p, action, sel)
        return {"uids": [int(n) for n, _ in todo], "folder": sel.folder, "dest": "Trash" if action == "trash" else "", "subjects": [],
                "account": PREFIX + p.id}

    async def update(self, account: str, action: str, sel: Selection, to: str = "", approved: list[int] | None = None) -> dict[str, Any]:
        p = self._pkg(account)
        todo = self._ids(p, action, sel)
        if approved is not None:
            keep = {str(a) for a in approved}
            todo = [x for x in todo if x[0] in keep]
        done, stopped = 0, ""
        for _n, mid in todo:
            try:
                await self.nest.provide(p, "mail", CHANGES[action], {"uid": mid})
            except NestError as e:
                stopped = str(e)[:160]
                break
            done += 1
        return {"done": done, "total": len(todo), "already": 0, "folder": sel.folder, "dest": "Trash" if action == "trash" else
                ("Archive" if action == "archive" else ""), "gmail": False, "labels_only": False, "stopped": stopped}

    async def send(self, account: str, *_a: Any, **_k: Any) -> str:
        raise MailError(f"{self._pkg(account).text('name')} does not send mail through Kelo's email_send; use its own tools")

    async def recent(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return []   # sign-in codes need the sender headers, which a package's tools do not give


class NestCalendars:
    def __init__(self, nest: Any):
        self.nest = nest

    def accounts(self) -> list[Any]:
        return self.nest.providing("calendar")

    async def events(self, pid: str, start: datetime, end: datetime, *, all_day: bool = False, tz: Any = timezone.utc) -> list[dict[str, Any]]:
        p = next((p for p in self.nest.providing("calendar") if p.id == pid), None)
        if p is None:
            return []
        spec = p.provides["calendar"]["events"]
        answer = await self.nest.provide(p, "calendar", "events", {
            "start": start.astimezone(tz).isoformat(timespec="seconds"), "end": end.astimezone(tz).isoformat(timespec="seconds"),
            "start_date": start.astimezone(tz).date().isoformat(), "end_date": end.astimezone(tz).date().isoformat()})
        fields = spec.get("fields") or {}
        out = []
        for r in _rows(answer, spec.get("select", "")):
            at, whole = when(_field(r, fields, "start"), tz)
            if at is None:
                continue
            stop, _ = when(_field(r, fields, "end"), tz)
            whole = bool(_field(r, fields, "all_day")) or whole
            if (whole and not all_day) or (stop or at) < start.timestamp() or at >= end.timestamp():
                continue
            ident = str(_field(r, fields, "id") or int(at))
            out.append({"id": f"nest:{p.id}:{ident}"[:200], "title": _text(_field(r, fields, "title"))[:200], "start": at,
                        "where": _text(_field(r, fields, "where"))[:200], "all_day": whole,
                        **({"end": stop} if stop and stop >= at else {}),
                        **({"day": datetime.fromtimestamp(at, tz).date().isoformat()} if whole else {})})
        return out


__all__ = ["NestCalendars", "NestMail", "when"]
