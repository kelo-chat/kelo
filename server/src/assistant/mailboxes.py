"""Every connected mailbox behind one interface, whatever the connection: what the chat's email tools (tools/mail_tools.py), the morning
briefing and the heads-ups (through briefing_sources.Sources), and sign-in codes (tools/login_code.py) read.

    IMAP          mail.Mail          app passwords (Gmail, Naver, Daum, iCloud, Yahoo) and Gmail by Google sign-in (XOAUTH2)
    Graph         microsoft_mail     Outlook.com, Hotmail, Live and Microsoft 365, with the Outlook connector's sign-in
    Nest          nest_sources       a connector from Kelo Nest whose manifest provides mail (nest_manifest.parse_provides)

Accounts are named "mail-<provider>" (IMAP), "outlook" (Graph) and "nest-<package id>"; each backend answers in the IMAP backend's
shapes (list rows {"uid", "from", "subject", "date", "unread", "bulk"}, read, plan / update, send, recent).

COVERAGE says, for every connector in the catalog that holds a mailbox or a calendar, which of the two Kelo reads through it, and for
the ones that look like mail or calendar but hold neither (or reach the same mailbox through another connector), why. server/tests/
test_mail_coverage.py fails when a connector is added that looks like mail or a calendar and is in neither table, and checks that
every connector in COVERAGE really shows up in the briefing's accounts and in the email tools once connected; installed Nest
connectors are covered by their `provides`.
"""

from __future__ import annotations

from typing import Any

from .i18n import N_
from .mail import Mail, MailError, Selection

# catalog id -> what Kelo reads through it (the briefing, the heads-ups, calendar_events, email_list / email_read / email_update)
COVERAGE: dict[str, tuple[str, ...]] = {
    "gmail": ("mail",),                       # IMAP with an app password, or the Google sign-in (XOAUTH2)
    "imap_email": ("mail", "calendar"),       # Naver, Daum, iCloud, Yahoo by IMAP; the iCloud, Yahoo and Naver calendars by CalDAV
    "outlook_mail": ("mail",),                # Microsoft Graph (microsoft_mail.py)
    "outlook_calendar": ("calendar",),        # Microsoft Graph (microsoft_calendar.py)
    "google_calendar": ("calendar",),         # Google sign-in or a service account (google_calendar.py)
    "caldav_calendar": ("calendar",),         # any CalDAV server with an app password (calendars.py)
}
# connectors whose name or description reads like mail or a calendar but that hold no mailbox or calendar of the owner's to read, or
# reach one that another connector reads; each with the reason
NOT_A_SOURCE: dict[str, str] = {
    "outlook_contacts": "an address book, not a mailbox or a calendar",
    "superhuman_mail": "a mail app on top of a Gmail or Outlook mailbox: that mailbox is read through the Gmail or Outlook connector",
    "calendly": "booking pages: the meetings it books land in the owner's Google or Outlook calendar, which is read there",
    "zoom": "video meetings: the meetings are on the owner's calendar, which is read there",
    "naver": "the Naver site in the browser: Naver Mail and Naver Calendar are read through IMAP Email (IMAP and CalDAV)",
    "close": "a sales CRM's outgoing email, not the owner's mailbox",
    "resend": "sends transactional email; it has no inbox",
    "mailerlite": "newsletter campaigns, not a mailbox",
    "kit": "newsletter campaigns, not a mailbox",
    "customer_io": "automated customer messages, not a mailbox",
    "fathom": "meeting recordings and notes, not a calendar",
    "fireflies": "meeting transcripts, not a calendar",
    "krisp": "meeting notes, not a calendar",
    "tldv": "meeting recordings, not a calendar",
    "read_ai": "meeting summaries, not a calendar",
    "typefully": "schedules social posts, not a calendar",
    "metricool": "schedules social posts, not a calendar",
    "mixpanel": "product analytics events, not calendar events",
    "otter": "conversation transcripts, not a calendar",
}

OUTLOOK_MAIL = N_("Outlook Mail")


class Mailboxes:
    def __init__(self, imap: Mail, graph: Any = None, nest: Any = None):
        self.imap, self.graph, self.nest = imap, graph, nest

    def _backends(self) -> list[Any]:
        return [b for b in (self.imap, self.graph, self.nest) if b is not None]

    def accounts(self) -> list[dict[str, Any]]:
        """Every connected mailbox: [{"name", "provider", "label", "address"}] (+ "title": the name to show, when not an IMAP provider's)."""
        out = list(self.imap.accounts())
        if self.graph is not None:
            out += [{**a, "title": OUTLOOK_MAIL} for a in self.graph.accounts()]
        if self.nest is not None:
            out += self.nest.accounts()
        return out

    def _pick(self, account: str) -> tuple[Any, str]:
        """(the backend, the account's name) for what the model or a card said ("", "gmail", "mail-naver", "outlook", "nest-x")."""
        a = (account or "").strip()
        mine = self.accounts()
        if not a:
            if not mine:
                raise MailError("no mailbox is connected. Ask the user to connect one with connect_service (Gmail, Outlook, or another "
                                "mailbox with IMAP Email).")
            if len(mine) > 1:
                raise MailError("several mailboxes are connected; say which with account: " + ", ".join(m["name"] for m in mine))
            a = mine[0]["name"]
        low = a.lower()
        if self.graph is not None and low in ("outlook", "microsoft-api", "hotmail", "microsoft 365", "office365", "live"):
            return self.graph, "outlook"
        if self.nest is not None and any(m["name"] == low or m["name"] == "nest-" + low for m in self.nest.accounts()):
            return self.nest, low if low.startswith("nest-") else "nest-" + low
        named = next((m for m in mine if low in (m["name"].lower(), (m.get("address") or "").lower())), None)
        if named is not None:
            if named["name"] == "outlook":
                return self.graph, "outlook"
            if named["name"].startswith("nest-"):
                return self.nest, named["name"]
            return self.imap, named["name"]
        return self.imap, a   # an IMAP mailbox by its provider ("gmail"); the IMAP side says when it is not connected

    async def list(self, account: str = "", folder: str = "INBOX", query: str = "", limit: int = 10, unread: bool = False,
                   skip_bulk: bool = False) -> list[dict[str, Any]]:
        b, name = self._pick(account)
        return await b.list(name, folder, query, limit, unread, skip_bulk=skip_bulk)

    async def arrivals(self, account: str, cursor: dict[str, Any] | None, kind: str = "inbox") -> dict[str, Any] | None:
        """Everything that came in after `cursor` in one mailbox (see Mail.arrivals / GraphMail.arrivals), oldest first, with the new
        cursor; None when this kind (spam folder, promotions) does not exist there."""
        b, name = self._pick(account)
        return await b.arrivals(name, cursor, kind)

    async def read(self, account: str, uid: str, folder: str = "INBOX") -> dict[str, Any]:
        b, name = self._pick(account)
        return await b.read(name, uid, folder)

    async def plan(self, account: str, action: str, sel: Selection, to: str = "") -> dict[str, Any]:
        b, name = self._pick(account)
        return await b.plan(name, action, sel, to)

    async def update(self, account: str, action: str, sel: Selection, to: str = "", approved: list[int] | None = None) -> dict[str, Any]:
        b, name = self._pick(account)
        return await b.update(name, action, sel, to, approved)

    async def send(self, account: str, to: list[str], subject: str, body: str, cc: list[str] | None = None, reply_to_uid: str = "",
                   folder: str = "INBOX", reply_to: str = "") -> str:
        b, name = self._pick(account)
        return await b.send(name, to, subject, body, cc, reply_to_uid, folder, reply_to)

    async def recent(self, minutes: float = 15, limit: int = 12, tool: str = "find_login_code") -> list[dict[str, Any]]:
        """The last minutes of mail from every connected mailbox, newest first (sign-in codes)."""
        out: list[dict[str, Any]] = []
        if self.imap.accounts():
            out += await self.imap.recent(minutes, limit, tool)
        for b in (self.graph, self.nest):
            if b is not None and b.accounts():
                try:
                    out += await b.recent(minutes, limit, tool)
                except MailError:
                    continue
        return sorted(out, key=lambda m: -(m.get("arrived") or m.get("at") or 0))


__all__ = ["COVERAGE", "Mailboxes", "NOT_A_SOURCE"]
