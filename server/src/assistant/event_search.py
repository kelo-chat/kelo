"""How every calendar read tool reads its `query` (phone_calendar_events, gcal_events, calendar_events): several terms, an event that
contains ANY one of them (title, place, notes; any case, Unicode-normalized) is kept, and a search that keeps nothing says that a filter
was applied and how many events the range holds without it, so an over-narrow search never reads as an empty calendar."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

MAX_TERMS = 20
_SPLIT = re.compile(r"[\s,;]+")


def norm(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def query_terms(query: Any) -> list[str]:
    """A list: each item is one term (an item may keep its spaces: a phrase). A string that is a JSON array of strings is read as
    that list; any other string is split at spaces and commas into words. Distinct, normalized, at most MAX_TERMS of 100 chars."""
    if isinstance(query, str):
        s = query.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                query = parsed
        if isinstance(query, str):
            query = _SPLIT.split(query)
    if not isinstance(query, (list, tuple)):
        return []
    out: list[str] = []
    for q in query:
        if isinstance(q, (dict, list, tuple)):
            continue
        w = norm(q).strip().strip("\"'")[:100]
        if w and w not in out:
            out.append(w)
    return out[:MAX_TERMS]


def matches(terms: list[str], *fields: Any) -> bool:
    """No terms: everything matches. Else: any one term is inside the joined fields."""
    if not terms:
        return True
    text = norm(" ".join(str(f or "") for f in fields))
    return any(t in text for t in terms)


def terms_text(terms: list[str]) -> str:
    return " or ".join(f'"{t}"' for t in terms)


def no_match_line(terms: list[str], span: str, total: int) -> str:
    """The result line of a search that kept nothing. `span`: 'between A and B (Zone)'; `total`: events in that span without the filter."""
    if total <= 0:
        return f"No events {span}, with or without the search words."
    return (f"No events matching {terms_text(terms)} {span}. The search keeps an event whose title, place or notes contain any one of "
            f"these words; without it the same range holds {total} event{'s' if total != 1 else ''}: read the range without query "
            "before saying something is not there.")

