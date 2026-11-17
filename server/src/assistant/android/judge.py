"""What a step on the device would commit: the step judge (step_judge.py) says what a tap does (android/tools.py settle), and typing is
judged by the value and the field's own structure (tools/commits.py). No word decides anything here."""

from __future__ import annotations

from ..tools import commits
from .screen import Item


def type_kind(item: Item | None, text: str) -> str:
    """The commit kind of typing `text` into this field: "personal" for the owner's own details (an email, a phone number, a card
    number, a password field), "" for a search, a message or a review (its send button is the step that asks).
    `item` None: a field that was not named (the one in focus), judged by the text alone."""
    if item is None:
        return commits.judge_type(None, "", text)
    info = {"tag": "textarea" if item.multiline else "input", "type": "password" if item.password else "text"}
    return commits.judge_type(info, "", text)
