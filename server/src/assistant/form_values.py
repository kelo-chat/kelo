"""Private form values (docs/data-trail.md, phase T2): may the owner's own value be typed into this page field without a card?

A value of the owner's (the home address, a phone number: privacy_guard.py) typed into a page field passes only when the field's HTML
autocomplete token is of that value's kind (the WHATWG token table, server/content/autocomplete_tokens.json: data, not words) and the page
is the task's site or a place the task's permissions cover. A field with no token, an unknown token or a token of another kind asks.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

# the privacy guard's categories (privacy_guard.CATEGORY_LABELS) a field token can vouch for, and the token kind that does it;
# every other category (ID, card, account, health, someone else's details, door codes, saved logins) always asks
VOUCHED = {"address": "address", "phone": "phone"}


@lru_cache(maxsize=1)
def table() -> tuple[frozenset[str], dict[str, str]]:
    """(the scope and contact modifiers, token -> kind) from server/content/autocomplete_tokens.json (ASSISTANT_CONTENT_DIR in the container)."""
    root = Path(os.environ.get("ASSISTANT_CONTENT_DIR") or Path(__file__).resolve().parents[2] / "content")
    try:
        raw = json.loads((root / "autocomplete_tokens.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset(), {}
    return (frozenset(str(x) for x in raw.get("modifiers", [])),
            {str(k): str(v) for k, v in (raw.get("tokens") or {}).items()})


def field_kind(autocomplete: Any) -> str:
    """The kind of value the field's autocomplete attribute asks for ("" for none, off, on, several field names or an unknown one)."""
    modifiers, tokens = table()
    names = [x for x in str(autocomplete or "").casefold().split()
             if x != "webauthn" and not x.startswith("section-") and x not in modifiers]
    return tokens.get(names[0], "") if len(names) == 1 else ""


def vouches(categories: Iterable[str], autocomplete: Any) -> bool:
    """Every category found in the typed text is of the kind this field's token asks for."""
    cats = list(categories)
    kind = field_kind(autocomplete)
    return bool(cats and kind) and all(VOUCHED.get(c) == kind for c in cats)
