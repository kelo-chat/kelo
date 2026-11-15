"""The Outlook mailbox (Outlook.com, Hotmail, Live, Microsoft 365) through Microsoft Graph, with the same sign-in as the Outlook connector
(vault item "microsoft-api", the typed-code connection of oauth_connect.py; its access token is renewed first when it has run out).

It is one more mailbox behind mailboxes.Mailboxes, in the same shape as an IMAP one (mail.Mail), so the chat's email tools, the morning
briefing and the heads-ups read it like any other:
  list     GET  /v1.0/me/mailFolders/{folder}/messages   (unread: isRead eq false; skip_bulk: Outlook's own "Other" inbox left out with
                                                         inferenceClassification eq 'focused', and list/bulk headers: mail.is_bulk)
  read     GET  /v1.0/me/messages/{id}                   (the text body: Prefer outlook.body-content-type="text"; attachment names)
  update   PATCH /v1.0/me/messages/{id} isRead / flag; POST /v1.0/me/messages/{id}/move to "archive", "deleteditems" or a folder,
           20 at a time through /v1.0/$batch. Nothing is ever deleted for good: "trash" moves to Deleted Items.
  send     POST /v1.0/me/sendMail, or /v1.0/me/messages/{id}/reply for an answer in its thread
  arrivals what was received after a cursor time (proactive.py): inbox / Other inbox / Junk Email, every page
  recent   the newest inbox messages of the last minutes with their text and sender headers (a sign-in code: tools/login_code.py)
Graph names a message by a long id; the tools speak in short numbers like IMAP's uids, so each id gets a number (UidMap, kept in the
store, the newest MAX_UIDS per mailbox).

The permission the owner chose when connecting decides what works: "Mail, calendar and contacts" (Mail.ReadWrite, Mail.Send) reads,
changes and sends; "Read only" (Mail.Read) only reads, and a change or a send says to connect Outlook again with the wider choice.
"""

from __future__ import annotations

import email
import email.policy
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any
from urllib.parse import quote, urlencode as _urlencode

from .mail import BODY_CAP, MailError, Selection, _sender_headers, is_bulk
from .mcp_oauth import open_token
from .netguard import NetGuardError, fetch
from .store import Store
from .vault import VaultError, use_credential

def urlencode(q: dict[str, str]) -> str:
    return _urlencode(q, quote_via=quote)   # spaces as %20: Graph reads a "+" in $filter / $search as a plus


ITEM = "microsoft-api"
ACCOUNT = "outlook"
HOST = "graph.microsoft.com"
API = f"https://{HOST}/v1.0"
MAX_UIDS = 3000
MAX_MATCHES = 5000          # messages one change may cover (a search over a whole folder)
BATCH = 20                  # Graph's limit of requests in one $batch
WELL_KNOWN = {"inbox": "inbox", "archive": "archive", "deleted items": "deleteditems", "deleteditems": "deleteditems", "trash": "deleteditems",
              "drafts": "drafts", "sent items": "sentitems", "sent": "sentitems", "sentitems": "sentitems", "junk email": "junkemail",
              "junk": "junkemail", "junkemail": "junkemail", "spam": "junkemail", "outbox": "outbox"}
READ_ONLY = ("Outlook was connected with \"Read only\", so mail can be read but not changed or sent. Ask the user to connect Outlook again "
             "in the Connectors tab and choose \"Mail, calendar and contacts\".")
ARRIVAL_PAGE = 50           # messages per request when reading what arrived since the last look
ARRIVAL_MARGIN = 120        # seconds the next look starts before this one did
SELECT = "id,subject,from,receivedDateTime,isRead,inferenceClassification,internetMessageHeaders"


class UidMap:
    """Short numbers for Graph's long message ids, per mailbox, kept in the store (the newest MAX_UIDS)."""

    def __init__(self, store: Store, account: str):
        self.store, self.key = store, f"mail-uids:{account}"

    def _load(self) -> dict[str, Any]:
        try:
            v = json.loads(self.store.meta(self.key) or "{}")
        except ValueError:
            v = {}
        return v if isinstance(v, dict) and isinstance(v.get("ids"), dict) else {"n": 0, "ids": {}}

    def numbers(self, ids: list[str]) -> list[str]:
        data = self._load()
        rev = {v: k for k, v in data["ids"].items()}
        out = []
        for i in ids:
            if i not in rev:
                data["n"] = int(data.get("n") or 0) + 1
                rev[i] = str(data["n"])
                data["ids"][rev[i]] = i
            out.append(rev[i])
        if len(data["ids"]) > MAX_UIDS:
            keep = sorted(data["ids"], key=int)[-MAX_UIDS:]
            data["ids"] = {k: data["ids"][k] for k in keep}
        self.store.set_meta(self.key, json.dumps(data))
        return out

    def ids(self, numbers: list[str] | tuple[str, ...]) -> list[str]:
        known = self._load()["ids"]
        missing = [n for n in numbers if n not in known]
        if missing:
            raise MailError(f"uid {missing[0]} is not one email_list gave for this mailbox; list the messages again")
        return [known[n] for n in numbers]


def _iso(v: Any) -> float:
    try:
        return datetime.fromisoformat(str(v or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _who(r: Any) -> str:
    e = (r or {}).get("emailAddress") or {}
    name, addr = str(e.get("name") or ""), str(e.get("address") or "")
    return f"{name} <{addr}>" if name and addr and name != addr else addr or name


def _headers(row: dict[str, Any]) -> email.message.Message:
    msg = email.message.Message()
    for h in row.get("internetMessageHeaders") or []:
        if isinstance(h, dict) and h.get("name"):
            try:
                msg[str(h["name"])] = str(h.get("value") or "")
            except Exception:  # noqa: BLE001 - a header that can not be kept is left out
                continue
    return msg


class GraphMail:
    def __init__(self, store: Store, *, fetcher: Callable[..., Any] = fetch):
        self.store, self.fetcher = store, fetcher
        self.uids = UidMap(store, ACCOUNT)

    # ---- the account
    def connected(self) -> bool:
        item = self.store.credential(ITEM)
        return bool(item and item.get("has_secret") and item.get("kind") == "api")

    def read_only(self) -> bool:
        from .oauth_connect import DeviceFlows
        return DeviceFlows(self.store).granted_scope("microsoft") == "read"

    def accounts(self) -> list[dict[str, Any]]:
        if not self.connected():
            return []
        label = str((self.store.credential(ITEM) or {}).get("label") or "")
        address = label.split(" · ")[-1] if " · " in label else ""
        return [{"name": ACCOUNT, "provider": "outlook", "label": f"Outlook · {address}" if address else "Outlook", "address": address}]

    # ---- one request
    async def _call(self, method: str, url: str, tool: str, detail: str, body: Any = None,
                    headers: dict[str, str] | None = None) -> tuple[int, Any]:
        if not self.connected():
            raise MailError("Outlook is not connected; ask the user to connect it in the Connectors tab")
        try:
            cred = await open_token(self.store, use_credential(self.store, ITEM, tool=tool, kind="api", detail=detail), self.fetcher)
            reply = await self.fetcher(method, url if url.startswith("https://") else API + url, allowed_hosts=[HOST],
                                       headers={"Authorization": f"Bearer {cred['secret']}", "Accept": "application/json",
                                                **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})},
                                       content=json.dumps(body).encode() if body is not None else None)
        except VaultError as e:
            raise MailError(str(e)) from None
        except NetGuardError as e:
            raise MailError(f"Outlook could not be reached ({str(e)[:120]}); nothing was repeated") from None
        try:
            data = json.loads(reply.body) if reply.body else {}
        except ValueError:
            data = {}
        if reply.status in (401,):
            raise MailError("Outlook refused the sign-in; ask the user to connect Outlook again in the Connectors tab")
        if reply.status == 403:
            raise MailError(READ_ONLY if self.read_only() else "Outlook did not allow this (HTTP 403); ask the user to connect Outlook again")
        return reply.status, data

    async def _folder(self, name: str, tool: str) -> tuple[str, str]:
        """(the folder's id or well-known name for a URL, its readable name)."""
        n = (name or "INBOX").strip()
        known = WELL_KNOWN.get(n.casefold())
        if known:
            return known, n
        st, data = await self._call("GET", "/me/mailFolders?" + urlencode({"$filter": f"displayName eq '{n.replace(chr(39), chr(39) * 2)}'",
                                                                          "$top": "1", "$select": "id,displayName"}), tool, f"folder {n}")
        rows = data.get("value") if isinstance(data, dict) else None
        if st != 200 or not rows:
            raise MailError(f"there is no folder {n!r} in Outlook (none is created)")
        return str(rows[0]["id"]), str(rows[0].get("displayName") or n)

    def _query(self, query: str, unread: bool, skip_bulk: bool, top: int, select: str = SELECT) -> dict[str, str]:
        q: dict[str, str] = {"$top": str(top), "$select": select}
        if query:   # Graph's search (KQL); it can not be combined with $filter or $orderby, so "unread" goes into it
            q["$search"] = '"' + (query + (" isread:false" if unread else "")).replace('"', "") + '"'
            return q
        cond = ["receivedDateTime ge 1900-01-01T00:00:00Z"]   # $orderby needs its property first in $filter
        if unread:
            cond.append("isRead eq false")
        if skip_bulk:
            cond.append("inferenceClassification eq 'focused'")
        q["$filter"] = " and ".join(cond)
        q["$orderby"] = "receivedDateTime desc"
        return q

    def _row(self, r: dict[str, Any], uid: str) -> dict[str, Any]:
        return {"uid": uid, "from": _who(r.get("from")), "subject": str(r.get("subject") or ""), "date": str(r.get("receivedDateTime") or ""),
                "unread": not r.get("isRead", True),
                "bulk": str(r.get("inferenceClassification") or "").lower() == "other" or is_bulk(_headers(r))}

    # ---- reading
    async def list(self, account: str = "", folder: str = "INBOX", query: str = "", limit: int = 10, unread: bool = False,
                   skip_bulk: bool = False) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 10), 30))
        fid, fname = await self._folder(folder, "email_list")
        want = limit
        top = min(30, limit * 3) if skip_bulk else limit
        st, data = await self._call("GET", f"/me/mailFolders/{quote(fid)}/messages?" + urlencode(self._query(query, unread, skip_bulk, top)),
                                    "email_list", f"{fname} {query}".strip())
        if st != 200:
            raise MailError(f"Outlook answered HTTP {st} for the message list")
        rows = [r for r in data.get("value") or [] if isinstance(r, dict) and r.get("id")]
        nums = self.uids.numbers([str(r["id"]) for r in rows])
        out = [self._row(r, n) for r, n in zip(rows, nums)]
        return [m for m in out if not m["bulk"]][:want] if skip_bulk else out

    # ---- what arrived since the last look (proactive.py)
    async def arrivals(self, account: str, cursor: dict[str, Any] | None, kind: str = "inbox") -> dict[str, Any] | None:
        """{"rows": [...oldest first], "cursor": {"t": ISO time}} of what was received after the cursor, paged through Graph's $skip, every
        page of it. kind "inbox": unread, Outlook's Focused inbox, no bulk headers; "promo": the Other inbox, unread; "junk": Junk Email,
        read or not. No cursor (first time): nothing, and a cursor at now. The cursor is the time this look started minus a safety margin
        (mail Graph shows a little late); the margin's overlap is left to the ledger. Raises MailError when Outlook can not be read."""
        start = datetime.now(timezone.utc)
        fresh = {"t": (start - timedelta(seconds=ARRIVAL_MARGIN)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if cursor is None or not str(cursor.get("t") or ""):
            return {"rows": [], "cursor": fresh}
        folder = "junkemail" if kind == "junk" else "inbox"
        cond = [f"receivedDateTime gt {cursor['t']}"]
        if kind != "junk":
            cond.append("isRead eq false")
            cond.append("inferenceClassification eq '%s'" % ("other" if kind == "promo" else "focused"))
        rows: list[dict[str, Any]] = []
        skip = 0
        while True:
            st, data = await self._call("GET", f"/me/mailFolders/{folder}/messages?" + urlencode({
                "$filter": " and ".join(cond), "$orderby": "receivedDateTime asc", "$top": str(ARRIVAL_PAGE), "$skip": str(skip),
                "$select": SELECT + ",bodyPreview"}), "proactive", f"new mail ({kind})")
            if st != 200:
                raise MailError(f"Outlook answered HTTP {st} for the new messages")
            page = [r for r in data.get("value") or [] if isinstance(r, dict) and r.get("id")]
            rows += page
            if len(page) < ARRIVAL_PAGE:
                break
            skip += ARRIVAL_PAGE
        nums = self.uids.numbers([str(r["id"]) for r in rows])
        out = []
        for r, n in zip(rows, nums):
            row = self._row(r, n)
            if kind == "inbox" and row["bulk"]:
                continue
            out.append({**row, "arrived": _iso(r.get("receivedDateTime")), "snippet": " ".join(str(r.get("bodyPreview") or "").split())[:300]})
        return {"rows": out, "cursor": fresh}

    async def read(self, account: str, uid: str, folder: str = "INBOX") -> dict[str, Any]:
        if not re.fullmatch(r"\d{1,12}", uid or ""):
            raise MailError("uid must be the number email_list gave")
        mid = self.uids.ids([uid])[0]
        st, r = await self._call("GET", f"/me/messages/{quote(mid)}?" + urlencode(
            {"$select": "subject,from,toRecipients,ccRecipients,receivedDateTime,body,internetMessageId,hasAttachments,conversationId"}),
            "email_read", f"{folder} {uid}", headers={"Prefer": 'outlook.body-content-type="text"'})
        if st != 200:
            raise MailError(f"no message with uid {uid} in Outlook (HTTP {st})")
        body = r.get("body") or {}
        text = str(body.get("content") or "")
        if str(body.get("contentType") or "").lower() == "html":
            text = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", text)))
        files: list[str] = []
        if r.get("hasAttachments"):
            st2, att = await self._call("GET", f"/me/messages/{quote(mid)}/attachments?$select=name", "email_read", f"attachments {uid}")
            files = [str(a.get("name")) for a in (att.get("value") or []) if st2 == 200 and isinstance(a, dict) and a.get("name")]
        return {"uid": uid, "from": _who(r.get("from")), "to": ", ".join(_who(x) for x in r.get("toRecipients") or []),
                "cc": ", ".join(_who(x) for x in r.get("ccRecipients") or []), "subject": str(r.get("subject") or ""),
                "date": str(r.get("receivedDateTime") or ""), "message_id": str(r.get("internetMessageId") or ""), "references": "",
                "text": re.sub(r"\n{3,}", "\n\n", text).strip()[:BODY_CAP * 4], "attachments": files}

    async def recent(self, minutes: float = 15, limit: int = 12, tool: str = "find_login_code") -> list[dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        st, data = await self._call("GET", "/me/mailFolders/inbox/messages?" + urlencode({
            "$filter": f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}", "$orderby": "receivedDateTime desc",
            "$top": str(max(1, min(limit, 30))), "$select": "subject,from,receivedDateTime,body,internetMessageHeaders"}),
            tool, f"messages of the last {int(minutes)} minutes", headers={"Prefer": 'outlook.body-content-type="text"'})
        if st != 200:
            return []
        out = []
        for r in data.get("value") or []:
            try:
                at = datetime.fromisoformat(str(r.get("receivedDateTime") or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                at = 0.0
            out.append({"from": _who(r.get("from")), "subject": str(r.get("subject") or ""), "at": at, "arrived": at,
                        "text": str((r.get("body") or {}).get("content") or "")[:6000], **_sender_headers(_headers(r)), "account": ACCOUNT})
        return out

    # ---- changing messages
    async def _matches(self, sel: Selection, tool: str) -> tuple[list[tuple[str, str, str]], str]:
        """[(number, Graph id, subject)] of the chosen messages, and the folder's name."""
        fid, fname = await self._folder(sel.folder, tool)
        if sel.uids:
            return [(n, i, "") for n, i in zip(sel.uids, self.uids.ids(list(sel.uids)))], fname
        url = f"/me/mailFolders/{quote(fid)}/messages?" + urlencode(self._query(sel.query, sel.unread, False, 100, "id,subject"))
        found: list[dict[str, Any]] = []
        while url and len(found) < MAX_MATCHES:
            st, data = await self._call("GET", url, tool, f"{fname} {sel.query}".strip())
            if st != 200:
                raise MailError(f"Outlook answered HTTP {st} for the search")
            found += [r for r in data.get("value") or [] if isinstance(r, dict) and r.get("id")]
            nxt = str(data.get("@odata.nextLink") or "")
            url = nxt if nxt.startswith(API + "/") else ""
        found = found[:MAX_MATCHES]
        nums = self.uids.numbers([str(r["id"]) for r in found])
        return [(n, str(r["id"]), str(r.get("subject") or "")) for n, r in zip(nums, found)], fname

    async def _dest(self, action: str, to: str, tool: str) -> tuple[str, str]:
        if action == "archive":
            return "archive", "Archive"
        if action == "trash":
            return "deleteditems", "Deleted Items"
        if action == "move":
            if not to.strip():
                raise MailError("say which folder to move them to")
            return await self._folder(to, tool)
        return "", ""

    async def plan(self, account: str, action: str, sel: Selection, to: str = "") -> dict[str, Any]:
        if self.read_only():
            raise MailError(READ_ONLY)
        todo, fname = await self._matches(sel, "email_update")
        _dest_id, dest = await self._dest(action, to, "email_update")
        subjects = [" ".join(s.split())[:60] for _, _, s in todo if s][:5] if len(todo) <= 5 else []
        return {"uids": [int(n) for n, _, _ in todo], "folder": fname, "dest": dest, "subjects": subjects, "account": ACCOUNT}

    async def update(self, account: str, action: str, sel: Selection, to: str = "", approved: list[int] | None = None) -> dict[str, Any]:
        if self.read_only():
            raise MailError(READ_ONLY)
        todo, fname = await self._matches(sel, "email_update")
        if approved is not None:   # the trash card showed these: never more than the user saw
            keep = {str(a) for a in approved}
            todo = [x for x in todo if x[0] in keep]
        dest_id, dest = await self._dest(action, to, "email_update")
        patch = {"read": {"isRead": True}, "unread": {"isRead": False}, "flag": {"flag": {"flagStatus": "flagged"}},
                 "unflag": {"flag": {"flagStatus": "notFlagged"}}}.get(action)
        if patch is None and not dest_id:
            raise MailError(f"unknown action {action!r}")
        done, stopped = 0, ""
        for i in range(0, len(todo), BATCH):
            part = todo[i:i + BATCH]
            reqs = [{"id": str(k), "method": "PATCH" if patch else "POST",
                     "url": f"/me/messages/{quote(mid)}" + ("" if patch else "/move"), "headers": {"Content-Type": "application/json"},
                     "body": patch if patch else {"destinationId": dest_id}} for k, (_, mid, _) in enumerate(part)]
            st, data = await self._call("POST", f"{API}/$batch", "email_update", f"{action} {len(part)} message(s)", body={"requests": reqs})
            if st != 200:
                stopped = f"HTTP {st}"
                break
            answers = [r for r in data.get("responses") or [] if isinstance(r, dict)]
            ok = sum(1 for r in answers if 200 <= int(r.get("status") or 0) < 300)
            done += ok
            if ok < len(part):
                bad = next((r for r in answers if not 200 <= int(r.get("status") or 0) < 300), {})
                if int(bad.get("status") or 0) == 403:
                    raise MailError(READ_ONLY if self.read_only() else "Outlook did not allow this change (HTTP 403)")
                stopped = str(((bad.get("body") or {}).get("error") or {}).get("message") or f"HTTP {bad.get('status')}")[:160]
                break
        if done:
            self.store.add_vault_log(ITEM, "used", f"{action}: {done} message(s)")
        return {"done": done, "total": len(todo), "already": 0, "folder": fname, "dest": dest, "gmail": False, "labels_only": False,
                "stopped": stopped}

    # ---- sending
    async def send(self, account: str, to: list[str], subject: str, body: str, cc: list[str] | None = None, reply_to_uid: str = "",
                   folder: str = "INBOX", reply_to: str = "") -> str:
        if self.read_only():
            raise MailError(READ_ONLY)
        people = [a.strip() for a in [*to, *(cc or [])] if a.strip()]
        if not to or not all(re.fullmatch(r"[^@\s,<>]+@[^@\s,<>]+\.[^@\s,<>]+", a) for a in people):
            raise MailError("every recipient must be a plain email address")
        if len(people) > 20:
            raise MailError("at most 20 recipients")
        msg: dict[str, Any] = {"toRecipients": [{"emailAddress": {"address": a}} for a in to],
                               "ccRecipients": [{"emailAddress": {"address": a}} for a in cc or []]}
        if reply_to and re.fullmatch(r"[^@\s,<>]+@[^@\s,<>]+\.[^@\s,<>]+", reply_to):
            msg["replyTo"] = [{"emailAddress": {"address": reply_to}}]
        if reply_to_uid:
            mid = self.uids.ids([reply_to_uid])[0]
            st, _ = await self._call("POST", f"/me/messages/{quote(mid)}/reply", "email_send", f"reply to {len(people)} recipient(s)",
                                     body={"message": msg, "comment": body[:50_000]})
        else:
            msg.update({"subject": subject[:300], "body": {"contentType": "Text", "content": body[:50_000]}})
            st, _ = await self._call("POST", "/me/sendMail", "email_send", f"to {len(people)} recipient(s)",
                                     body={"message": msg, "saveToSentItems": True})
        if st not in (200, 202):
            raise MailError(f"Outlook did not send it (HTTP {st}); nothing was repeated")
        self.store.add_vault_log(ITEM, "used", f"email sent to {len(people)} recipient(s)")
        return f"sent to {', '.join(people)}"


__all__ = ["ACCOUNT", "GraphMail", "READ_ONLY", "UidMap"]
