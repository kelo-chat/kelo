"""What a step on the device would commit, by the same rules as the browser's (tools/commits.py), judged from the words on the screen."""

from __future__ import annotations

from ..tools import commits
from .screen import Item


def tap_kind(item: Item) -> str:
    """The commit kind of tapping this item ("pay", "send", "delete", "apply", "account", "security", "optout"), "" for one that only
    looks or moves around."""
    return commits.judge_words(item.text or item.desc)


def type_kind(item: Item | None, text: str) -> str:
    """The commit kind of typing `text` into this field: "personal" for the owner's own details (an email, a phone number, a card
    number, a password field, a field named after one), "" for a search, a message or a review (its send button is the step that asks).
    `item` None: a field that was not named (the one in focus), judged by the text alone."""
    if item is None:
        return commits.judge_type(None, "", text)
    info = {"tag": "textarea" if item.multiline else "input", "type": "password" if item.password else "text",
            "attrs": f"{item.desc} {item.hint}".strip()}
    return commits.judge_type(info, "", text)
