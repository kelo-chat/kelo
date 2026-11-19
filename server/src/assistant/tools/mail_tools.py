"""The assistant's tools for every mailbox the user connected (mailboxes.py: Gmail, Naver, Daum, iCloud, Yahoo by IMAP; Outlook, Hotmail
and Microsoft 365 by Microsoft Graph; mail connectors from Kelo Nest). Reading is free; sending
asks the user every time and shows who it goes to. Changing messages (read/unread, flags, archive, move) is easy to undo and needs no
card; moving to the trash asks once for the whole batch, with its count. The password never reaches the model."""

from __future__ import annotations

import json
from email.utils import parseaddr
from typing import Any

from ..agent import Ours, Tool, current_call
from ..agent.fence import data
from ..code_shelf import SHELF
from .. import mail_html
from ..data_trail import USES_PARAM, note_item
from ..i18n import N_, t
from ..mail import ACTIONS, Mail, MailError, Selection
from ..netguard import NetGuardError
from ..vault import VaultError


READ_MAX = 10   # messages email_read reads in one call
TRASH_ONE = N_("1 message in {folder}")
TRASH_MANY = N_("{count} messages in {folder}")
TRASH_SEARCH = N_("Search: {query}")
TRASH_UNREAD = N_("unread only")
# the approval card's words for the actions that stay in the mailbox (no count: they are never held; trash has its own, with the count)
MAIL_WORDS = {"read": N_("Mark messages in {folder} as read"), "unread": N_("Mark messages in {folder} as unread"),
              "flag": N_("Flag messages in {folder}"), "unflag": N_("Unflag messages in {folder}"),
              "archive": N_("Archive messages in {folder}"), "move": N_("Move messages in {folder}"),
              "trash": N_("Move messages in {folder} to the trash")}
DONE_WORDS = {"read": "Marked {n} as read", "unread": "Marked {n} as unread", "flag": "Flagged {n}", "unflag": "Unflagged {n}",
              "archive": "Archived {n}", "move": "Moved {n}", "trash": "Moved {n}"}
STATE_WORDS = {"read": "read", "unread": "unread", "flag": "flagged", "unflag": "unflagged", "archive": "archived"}


def _address(sender: str) -> str:
    """The plain address of a "Name <address>" sender (what a reply's recipient is compared with)."""
    return parseaddr(sender)[1].strip().casefold()


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


def _addresses(value: Any) -> list[str]:
    return [str(x) for x in ([value] if isinstance(value, str) else (value or []))]


def describe_text(args: dict[str, Any]) -> str:
    """The one-line summary of a mail: who, the subject, and that a designed version comes along (the card, the history, the messenger)."""
    return t("To: {to}", to=", ".join(_addresses(args.get("to")))) + \
        (" · " + t("Cc: {cc}", cc=", ".join(_addresses(args.get("cc")))) if args.get("cc") else "") + \
        (" · " + t("Bcc: {bcc}", bcc=", ".join(_addresses(args.get("bcc")))) if args.get("bcc") else "") + \
        " · " + t("Subject: {subject}", subject=str(args.get("subject", ""))[:80]) + \
        (" · " + t("Includes a designed (HTML) version") if args.get("html") else "")


def card_extra(approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """The card of a mail: who gets it, the subject and the whole plain text (for a designed mail, the words of the designed version
    when the plain text is empty). A designed mail also gets where its preview is; the HTML itself never goes into the event, the
    apps fetch the sanitized preview from that route (api/approvals.py)."""
    to, cc, bcc = _addresses(args.get("to")), _addresses(args.get("cc")), _addresses(args.get("bcc"))
    html = str(args.get("html") or "")
    lines = [{"label": t("To"), "value": ", ".join(to)}]
    also = [t("Cc: {cc}", cc=", ".join(cc))] if cc else []
    if bcc:
        also.append(t("Bcc: {bcc}", bcc=", ".join(bcc)))
    if also:
        lines.append({"label": t("Also to"), "value": " · ".join(also)})
    lines.append({"label": t("Subject"), "value": str(args.get("subject", ""))})
    text = str(args.get("body") or "").strip() or (mail_html.to_text(mail_html.clean(html)) if html else "")
    if html and text:
        lines.append({"label": t("Message"), "value": text, "full": True})
    extra: dict[str, Any] = {"lines": lines}
    preview = mail_html.preview(html) if html else None
    if preview is not None:
        extra["mail"] = {"designed": True, "preview": f"/api/approvals/{approval_id}/mail-preview",
                         "pictures": preview.pictures, "links": preview.links}
        flat = " ".join(text.split())   # the notification: the mail's words, cut, and where the design is
        snippet = flat[:200] + ("…" if len(flat) > 200 else "")
        extra["notice_body"] = "\n".join(part for part in (describe_text(args), snippet, t("You can see the design in the Kelo app."))
                                         if part)[:400]
    return extra


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
        for m in rows:   # one label per message (data_trail.py): its uid is what `uses` and the owner's picks name
            line = f"{m['from']} {m['subject']}"
            note_item(str(m["uid"]), party=_address(str(m["from"])), title=str(m["subject"]), text=line)
        # a one-time code in a subject is held back: the model reads "[verification code #N]" (code_shelf.py); one judge request for all
        subjects = await SHELF.mask_many([(str(m["subject"]), str(m["from"]), str(m["subject"])) for m in rows], channel="mail")
        return "\n".join(f"- uid {m['uid']}{' (unread)' if m['unread'] else ''} · {m['date']} · from {m['from']} · {sub}"
                         for m, sub in zip(rows, subjects))

    async def read_one(account: str, uid: str, folder: str, room: int) -> str:
        try:
            m = await mail.read(account, uid, folder)
        except (MailError, NetGuardError) as e:
            return Ours(f"error: uid {uid}: {e}")
        # codes become "[verification code #N]" first: subject and body in one judge request
        subject, text = await SHELF.mask_many([(str(m["subject"]), str(m["from"]), str(m["subject"])), (m["text"], str(m["from"]), str(m["subject"]))],
                                              channel="mail", key=f"{account}|{folder}|{uid}")
        text = text[:room]
        cut = f"\n[Cut: showed the first {room:,} of {len(m['text']):,} characters of this message.]" if len(m["text"]) > room else ""
        files = f"\nAttachments: {', '.join(m['attachments'])}" if m["attachments"] else ""
        note_item(uid, party=_address(str(m["from"])), title=str(m["subject"]), text=f"{m['subject']}\n{m['text']}")
        # the whole message (headers too) is the sender's: marked as external data; the cut note is ours, outside
        return data(f"From: {m['from']}\nTo: {m['to']}\n{('Cc: ' + m['cc'] + chr(10)) if m['cc'] else ''}Date: {m['date']}\n"
                    f"Subject: {subject}{files}\n\n{text}") + cut

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
        to, cc, bcc = args.get("to") or [], args.get("cc") or [], args.get("bcc") or []
        to, cc, bcc = ([x] if isinstance(x, str) else x for x in (to, cc, bcc))
        try:
            return await mail.send(str(args.get("account", "")), [str(x) for x in to], str(args.get("subject", "")), str(args.get("body", "")),
                                   [str(x) for x in cc], str(args.get("reply_to_uid") or ""), str(args.get("folder") or "INBOX"),
                                   bcc=[str(x) for x in bcc], html=str(args.get("html") or ""))
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
        if plan is None:   # not held (another action, or no batch found yet): the action's own words, no count
            line = t(MAIL_WORDS.get(_action(args), MAIL_WORDS["trash"]), folder=folder)
        else:
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
        return describe_text(args)

    account = {"type": "string", "description": "which connected mailbox when there are several: " + ("gmail, naver, outlook ... "
                                                 "(the names an error lists)")}
    return [
        Tool("email_list", "List messages in one of the user's connected mailboxes, newest first.",
             {"type": "object", "properties": {"account": account, "folder": {"type": "string"},
                                               "query": {"type": "string", "description": "the mailbox's own search syntax if it has one, else words"},
                                               "limit": {"type": "integer"}, "unread": {"type": "boolean"}}},
             email_list, read_only=True, untrusted=True, source="email", trail="mail"),
        Tool("email_read", "Read messages by their uid from email_list (not marked as read). To read several, pass them all in uids "
             "in one call instead of one call each. One-time codes in a message appear as '[verification code #N]': you never see the code; "
             "a browser sign-in uses it with find_login_code code_ref N.",
             {"type": "object", "properties": {"account": account, "uid": {"type": "string"},
                                               "uids": {"type": "array", "items": {"type": "string"}, "maxItems": READ_MAX,
                                                        "description": f"several uids at once (up to {READ_MAX})"},
                                               "folder": {"type": "string"}}},
             email_read, read_only=True, untrusted=True, source="email", trail="mail"),
        Tool("email_send", "Send an email from the user's connected mailbox; the user approves each one. For a styled mail, also pass html.",
             {"type": "object", "properties": {"account": account, "to": {"type": "array", "items": {"type": "string"}},
                                               "cc": {"type": "array", "items": {"type": "string"}},
                                               "bcc": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"},
                                               "body": {"type": "string", "description": "the whole message, plain text (always, also with html)"},
                                               "html": {"type": "string", "description": "optional designed version of the same mail: simple "
                                                        "table-based HTML with inline CSS (no <style> block, no scripts, no forms); "
                                                        "scripts, forms and remote images are removed"},
                                               "reply_to_uid": {"type": "string", "description": "answer this message in its thread"},
                                               "uses": USES_PARAM},
              "required": ["to", "subject", "body"]},
             email_send, requires_approval=True, describe=describe, approval_extra=card_extra),
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
