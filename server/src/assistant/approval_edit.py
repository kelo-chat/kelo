"""Fixing the outgoing message inside its approval card, and what the approval log keeps about it.

A card for a tool that SENDS a message (a mail, a text) may carry `"editable": {"field", "text", "max"}`: the owner changes the text and
approves (POST /api/approvals/{id} {"approve": true, "edited": {field: new text}}). The server replaces exactly that argument before
the tool runs. Only the argument table below is editable: payments, browser steps and calls never are, and a deny ignores an edit.
`max` is the service's own limit (a text message), null when the service has none.

The approval log never keeps arguments (activity.py): of an edit it keeps the field's name and the two texts, secret-scrubbed and
short like the card's one-line detail, so the history can say "edited" and the owner can see what changed.
"""

from __future__ import annotations

from typing import Any

from . import effects, i18n
from .agent.found_secrets import scrub

EDITABLE: dict[str, str] = {"email_send": "body", "phone_send_text": "text"}   # tool -> the argument holding the message
KEEP = 300   # characters of each version the log keeps (the same cap as a card's detail line)


class EditRefused(ValueError):
    """The edit cannot be taken (an empty message, one over the limit, a field that is not editable): the answer is a 400 and the card stays open."""


def limit_of(name: str) -> int | None:
    """The most characters the service takes for this tool's message, None when it has no limit."""
    if name == "phone_send_text":
        from .phone_data import MAX_TEXT
        return MAX_TEXT
    return None


def editable(name: str, args: dict[str, Any] | None) -> dict[str, Any] | None:
    """The card's "editable" object for this call, None when its message cannot be edited here (any tool that does not send)."""
    field = EDITABLE.get(name)
    args = args if isinstance(args, dict) else {}
    if field is None or not isinstance(args.get(field), str):
        return None
    if args.get("html"):   # an edited plain text would no longer match the designed version sent with it: the owner approves or refuses
        return None
    effect = effects.effect_of(name, args)
    if effect is None or effect.kind != "send":
        return None
    return {"field": field, "text": args[field], "max": limit_of(name)}


def check(meta: dict[str, Any], edited: dict[str, Any]) -> tuple[str, str]:
    """(field, new text) of an edit for the pending card `meta`; EditRefused (its message already in the owner's language) when it is
    not allowed. The same two rules as the card's own sheet: not empty, not over the service's limit."""
    spec = meta.get("editable")
    if not isinstance(spec, dict) or not isinstance(meta.get("arguments"), dict):
        raise EditRefused(i18n.t("This message cannot be changed."))
    if set(edited) != {spec["field"]}:
        raise EditRefused(i18n.t("This message cannot be changed."))
    text = edited[spec["field"]]
    if not isinstance(text, str) or not text.strip():
        raise EditRefused(i18n.t("Add a message before sending."))
    if spec.get("max") is not None and len(text.strip()) > int(spec["max"]):
        raise EditRefused(i18n.t("The message is too long. Shorten it before sending."))
    return str(spec["field"]), text.strip()


def apply(meta: dict[str, Any], field: str, text: str) -> dict[str, str]:
    """Replaces exactly that argument of the waiting call (the dict the tool will run with) and notes what changed on the card's item."""
    args = meta["arguments"]
    before = str(args.get(field) or "")
    args[field] = text
    meta["editable"] = {**meta["editable"], "text": text}
    meta["edited"] = {"field": field, "before": before, "after": text}
    return meta["edited"]


def logged(text: str) -> str:
    """One version of an edited message as the log keeps it: secrets hidden, shortened."""
    return scrub(text)[:KEEP]


def where_of(name: str, args: dict[str, Any] | None, site: str = "") -> tuple[str, str]:
    """(kind, where) for the log: the tool's effect kind ("read", "change", "send", "delete" or "") and the recipient or place to show
    (the site when the card has one, else the effect's targets without their "connector:" / "site:" prefix)."""
    try:
        effect = effects.effect_of(name, args if isinstance(args, dict) else {})
    except Exception:  # noqa: BLE001 - the log line is written without them
        return "", site
    if effect is None:
        return "", site
    places = [x.removeprefix("site:").removeprefix("connector:") for x in effect.targets if x]
    return effect.kind, site or ", ".join(places)[:200]
