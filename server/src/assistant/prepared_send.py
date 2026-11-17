"""The real recipients of an outgoing message, and the frozen send (docs/data-trail.md, "Bound recipients").

`recipients_of(name, args)`: who a message will REALLY reach: every To, Cc and Bcc address, each argument a string or a list, each element
read as an address list ("Name <a@b.org>", "a@b.org, c@d.org"). A piece that is no address stays as written, so it matches no permission.
The permission checks (task permission, the owner's recipient allows, the data trail, the outside-text rule) all read this one list.

`PreparedSend`: the send as the owner approved it, frozen by a digest of the tool, the account, the recipients, the thread, the subject
and the body. The agent loop freezes it after the gate judged the call and checks it again right before the tool runs: another digest
means the approval was for something else, and the owner is asked again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Any

RECIPIENT_ARGS = ("to", "cc", "bcc")
THREAD_ARGS = ("reply_to_uid", "thread_id", "thread")


def _pieces(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if x is not None]
    return [str(value)]


def address_list(value: Any) -> list[str]:
    """The addresses of one argument (a string or a list of strings), lower case, in order, once each."""
    out: list[str] = []
    for raw in _pieces(value):
        if not raw.strip():
            continue
        found = [addr.strip().casefold() or name.strip().casefold() for name, addr in getaddresses([raw])]
        for x in found or [raw.strip().casefold()]:
            if x and x not in out:
                out.append(x)
    return out


def recipients_of(args: dict[str, Any]) -> list[str]:
    """Every To, Cc and Bcc address of an email call, once each, in that order."""
    out: list[str] = []
    for key in RECIPIENT_ARGS:
        for x in address_list((args or {}).get(key)):
            if x not in out:
                out.append(x)
    return out


@dataclass(frozen=True)
class PreparedSend:
    tool: str
    account: str
    recipients: tuple[str, ...]
    thread: str
    subject: str
    body: str

    @property
    def digest(self) -> str:
        blob = json.dumps([self.tool, self.account, sorted(self.recipients), self.thread, self.subject, self.body], ensure_ascii=False)
        return hashlib.blake2b(blob.encode("utf-8", "replace"), digest_size=16).hexdigest()


def freeze(tool: str, args: dict[str, Any] | None) -> PreparedSend | None:
    """The call as a frozen send, or None for a call that sends nothing (effects.py decides what a send is)."""
    from . import effects
    args = args if isinstance(args, dict) else {}
    e = effects.effect_of(tool, args)
    if e is None or e.kind != "send":
        return None
    thread = next((str(args[k]) for k in THREAD_ARGS if args.get(k)), "")
    return PreparedSend(tool, str(args.get("account") or "").strip().casefold(), tuple(e.targets), thread,
                        str(args.get("subject") or ""), e.text or str(args.get("body") or args.get("text") or ""))
