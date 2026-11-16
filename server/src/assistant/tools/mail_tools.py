"""The assistant's tools for every mailbox the user connected (mailboxes.py: Gmail, Naver, Daum, iCloud, Yahoo by IMAP; Outlook, Hotmail
and Microsoft 365 by Microsoft Graph; mail connectors from Kelo Nest). Reading is free; sending
asks the user every time and shows who it goes to. Changing messages (read/unread, flags, archive, move) is easy to undo and needs no
card; moving to the trash asks once for the whole batch, with its count. The password never reaches the model."""

from __future__ import annotations

import json
from typing import Any

from ..agent import Ours, Tool, current_call
from ..agent.fence import data
from ..i18n import N_, t
from ..mail import ACTIONS, Mail, MailError, Selection
from ..netguard import NetGuardError
from ..vault import VaultError


READ_MAX = 10   # messages email_read reads in one call
TRASH_ONE = N_("1 message in {folder}")
TRASH_MANY = N_("{count} messages in {folder}")
TRASH_SEARCH = N_("Search: {query}")
TRASH_UNREAD = N_("unread only")
DONE_WORDS = {"read": "Marked {n} as read", "unread": "Marked {n} as unread", "flag": "Flagged {n}", "unflag": "Unflagged {n}",
              "archive": "Archived {n}", "move": "Moved {n}", "trash": "Moved {n}"}
STATE_WORDS = {"read": "read", "unread": "unread", "flag": "flagged", "unflag": "unflagged", "archive": "archived"}


def _action(args: dict[str, Any]) -> str:
    return str(args.get("action") or "").strip().lower()


def _n(k: int) -> str:
    return f"{k:,} message" + ("" if k == 1 else "s")


def update_report(action: str, got: dict[str, Any]) -> str:
    """The model's words for what email_update did: what, how many, from where to where."""
    where = f" in {got['folder']}"
    if action == "trash":
        where = f" from {got['folder']} to the trash ({got['dest']})"
    elif action == "move" or (action == "archive" and got["dest"]):
        where = f" from {got['folder']} to {got['dest']}" if not got.get("labels_only") else f" (label {got['dest']} added; All Mail keeps every message)"
    elif action == "archive" and got.get("gmail"):
        where = f" (the Inbox label taken off{'' if got['folder'].upper() == 'INBOX' else ' in ' + got['folder']}; they stay in All Mail)"
    state = STATE_WORDS.get(action, "that way")
    if not got["total"]:
        if got["already"] == 1:
            return f"That message was already {state}; nothing was changed."
        if got["already"]:
            return f"All {_n(got['already'])} were already {state}; nothing was changed."
        return "No messages matched; nothing was changed."
    line = DONE_WORDS[action].format(n=_n(got["done"])) + where + "."
    if got["already"]:
        line += f" {got['already']:,} {'was' if got['already'] == 1 else 'were'} already {state}."
    if action == "trash" and got["done"]:
        line += " They can be restored from there."
    if got["stopped"]:
        line += f" The mail server stopped after {got['done']:,} of {got['total']:,} ({got['stopped']}); the rest were not changed."
    return line


def mail_tools(mail: Mail) -> list[Tool]:
    async def email_list(args: dict[str, Any]) -> str:
        try:
            rows = await mail.list(str(args.get("account", "")), str(args.get("folder") or "INBOX"), str(args.get("query") or ""),
                                   int(args.get("limit") or 10), bool(args.get("unread")))
        except (MailError, NetGuardError, ValueError) as e:
            return Ours(f"error: {e}")
        if not rows:
            return Ours("No messages matched.")
        # senders and subjects are other people's words: the list is fenced as external data
        return "\n".join(f"- uid {m['uid']}{' (unread)' if m['unread'] else ''} · {m['date']} · from {m['from']} · {m['subject']}" for m in rows)

    async def read_one(account: str, uid: str, folder: str, room: int) -> str:
        try:
            m = await mail.read(account, uid, folder)
        except (MailError, NetGuardError) as e:
            return Ours(f"error: uid {uid}: {e}")
        text = m["text"][:room]
        cut = f"\n[Cut: showed the first {room:,} of {len(m['text']):,} characters of this message.]" if len(m["text"]) > room else ""
        files = f"\nAttachments: {', '.join(m['attachments'])}" if m["attachments"] else ""
        # the whole message (headers too) is the sender's: marked as external data; the cut note is ours, outside
        return data(f"From: {m['from']}\nTo: {m['to']}\n{('Cc: ' + m['cc'] + chr(10)) if m['cc'] else ''}Date: {m['date']}\n"
                    f"Subject: {m['subject']}{files}\n\n{text}") + cut

    async def email_read(args: dict[str, Any]) -> str:
        account, folder = str(args.get("account", "")), str(args.get("folder") or "INBOX")
        uids = args.get("uids") if isinstance(args.get("uids"), list) else []
        uids = [str(u).strip() for u in uids if str(u).strip()][:READ_MAX] or [str(args.get("uid", ""))]
        if len(uids) == 1:
            return await read_one(account, uids[0], folder, 12_000)
        # several at once: one step instead of one per message, each message a little shorter
        room = max(2_000, 24_000 // len(uids))
        parts = []
        for uid in uids:
            got = await read_one(account, uid, folder, room)
            parts.append(got if isinstance(got, Ours) else f"--- message uid {uid} ---\n{got}")
        if all(isinstance(p, Ours) for p in parts):
            return Ours("\n".join(parts))
        return "\n\n".join(parts)

    async def email_send(args: dict[str, Any]) -> str:
        to, cc = args.get("to") or [], args.get("cc") or []
        if isinstance(to, str):
            to = [to]
        if isinstance(cc, str):
            cc = [cc]
        try:
            return await mail.send(str(args.get("account", "")), [str(x) for x in to], str(args.get("subject", "")), str(args.get("body", "")),
                                   [str(x) for x in cc], str(args.get("reply_to_uid") or ""), str(args.get("folder") or "INBOX"))
        except (MailError, NetGuardError) as e:
            return f"error: {e}"

    def selection(args: dict[str, Any]) -> Selection:
        return Selection.of(str(args.get("folder") or "INBOX"), args.get("uids") or ([args["uid"]] if args.get("uid") else []),
                            str(args.get("query") or ""), bool(args.get("unread")), bool(args.get("all")))

    trash_batches: dict[str, dict[str, Any]] = {}   # tool call id -> the batch its trash card showed

    async def update_hold(args: dict[str, Any]) -> str | None:
        """Before a trash card: find the batch once (count, a few subjects). Nothing matched or a refusal: said without a card."""
        if _action(args) != "trash":
            return None
        try:
            plan = await mail.plan(str(args.get("account", "")), "trash", selection(args))
        except (MailError, NetGuardError, VaultError, ValueError) as e:
            return f"error: {e}"
        if not plan["uids"]:
            return "No messages matched; nothing was moved to the trash."
        if not str(args.get("account") or "").strip():
            args["account"] = plan["account"].removeprefix("mail-")   # so a task's permission for this mailbox can be checked
        if len(trash_batches) > 50:
            trash_batches.clear()
        trash_batches[current_call.get()] = plan
        return None

    async def update_describe(args: dict[str, Any]) -> str:
        plan = trash_batches.get(current_call.get())
        folder = str(args.get("folder") or "INBOX")
        if plan is None:
            return t(TRASH_MANY, count="?", folder=folder)
        n = len(plan["uids"])
        line = t(TRASH_ONE, folder=plan["folder"]) if n == 1 else t(TRASH_MANY, count=f"{n:,}", folder=plan["folder"])
        if plan["subjects"]:
            line += ": " + " · ".join(plan["subjects"])
        if args.get("query"):
            line += " · " + t(TRASH_SEARCH, query=str(args["query"])[:80])
        if args.get("unread"):
            line += " · " + t(TRASH_UNREAD)
        return line

    def update_declined(args: dict[str, Any]) -> str:
        trash_batches.pop(current_call.get(), None)
        return "The user declined moving these messages to the trash. Nothing was moved; do not retry it, ask what they would like instead."

    async def email_update(args: dict[str, Any]) -> str:
        action = _action(args)
        if action not in ACTIONS:
            return f"error: action must be one of {', '.join(ACTIONS)}"
        plan = trash_batches.pop(current_call.get(), None) if action == "trash" else None
        try:
            got = await mail.update(str(args.get("account", "")), action, selection(args), str(args.get("to") or ""),
                                    plan["uids"] if plan else None)
        except (MailError, NetGuardError, VaultError, ValueError) as e:
            return f"error: {e}"
        return update_report(action, got)

    async def describe(args: dict[str, Any]) -> str:
        to = args.get("to") or []
        to = [to] if isinstance(to, str) else to
        return t("To: {to}", to=", ".join(map(str, to))) + \
            (" · " + t("Cc: {cc}", cc=", ".join(map(str, args.get("cc") or []))) if args.get("cc") else "") + \
            " · " + t("Subject: {subject}", subject=str(args.get("subject", ""))[:80])

    account = {"type": "string", "description": "which connected mailbox when there are several: " + ("gmail, naver, outlook ... "
                                                 "(the names an error lists)")}
    return [
        Tool("email_list", "List messages in one of the user's connected mailboxes (Gmail, Outlook, Naver, Daum, iCloud, Yahoo ...), newest first.",
             {"type": "object", "properties": {"account": account, "folder": {"type": "string"},
                                               "query": {"type": "string", "description": "the mailbox's own search syntax if it has one, else words"},
                                               "limit": {"type": "integer"}, "unread": {"type": "boolean"}}},
             email_list, read_only=True, untrusted=True, source="email"),
        Tool("email_read", "Read messages by their uid from email_list (not marked as read). To read several, pass them all in uids "
             "in one call instead of one call each.",
             {"type": "object", "properties": {"account": account, "uid": {"type": "string"},
                                               "uids": {"type": "array", "items": {"type": "string"}, "maxItems": READ_MAX,
                                                        "description": f"several uids at once (up to {READ_MAX})"},
                                               "folder": {"type": "string"}}},
             email_read, read_only=True, untrusted=True, source="email"),
        Tool("email_send", "Send an email from the user's connected mailbox; the user approves each one.",
             {"type": "object", "properties": {"account": account, "to": {"type": "array", "items": {"type": "string"}},
                                               "cc": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"},
                                               "body": {"type": "string", "description": "the whole message, plain text"},
                                               "reply_to_uid": {"type": "string", "description": "answer this message in its thread"}},
              "required": ["to", "subject", "body"]},
             email_send, requires_approval=True, describe=describe),
        Tool("email_update", "Change messages in the user's connected mailbox: mark read or unread, flag (star) or unflag, archive, "
             "move to a folder or label, or move to the trash (restorable; the user approves that once per batch). Pick them by uids "
             "from email_list, or by a search like email_list's (folder, query, unread): a search covers every match, thousands too.",
             {"type": "object", "properties": {"account": account,
                                               "action": {"type": "string", "enum": list(ACTIONS)},
                                               "uids": {"type": "array", "items": {"type": "string"}, "description": "uids from email_list"},
                                               "folder": {"type": "string", "description": "where the messages are (default INBOX)"},
                                               "query": {"type": "string", "description": "same as email_list's query"},
                                               "unread": {"type": "boolean", "description": "only unread messages"},
                                               "all": {"type": "boolean", "description": "every message in the folder"},
                                               "to": {"type": "string", "description": "for move: the folder or label"}},
              "required": ["action"]},
             email_update, requires_approval=True, auto_approve=lambda a: _action(a) != "trash", hold=update_hold,
             describe=update_describe, declined=update_declined),
    ]
