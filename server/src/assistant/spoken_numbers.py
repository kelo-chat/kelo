"""Numbers, dates and times made speakable before Kelo's voice reads them (the safety net under the model's own spoken forms).

The model reads most numbers well, but it misreads some shapes (a clock time read as one number, a decimal point lost, a grouped
number left as digits). Those shapes, and only those, are rewritten here into words of the text's language. The rules of each
language come from libraries (num2words for the words, Babel's CLDR data for the decimal and group signs), never from a list in
Kelo. Nothing else in the text is touched.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from babel import Locale
from babel.numbers import NumberFormatError, get_decimal_symbol, get_group_symbol, parse_decimal
from num2words import CONVERTER_CLASSES, num2words

log = logging.getLogger("assistant.spoken_numbers")

CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")


def _code(tag: str) -> str | None:
    """num2words' code for a BCP-47 tag: the full tag with '_' (pt_BR) when it has one, else the primary subtag, else None."""
    full = tag.replace("-", "_")
    if full in CONVERTER_CLASSES:
        return full
    primary = tag.split("_", 1)[0].split("-", 1)[0].lower()
    return primary if primary in CONVERTER_CLASSES else None


class SpokenNumbers:
    """Rewrites the number shapes a speech model misreads into words of the text's language."""

    def __init__(self, langs: frozenset[str] | None = None):
        self.langs = langs            # None = every language num2words has a converter for

    def apply(self, text: str, lang: str) -> str:
        if not any(c.isdigit() for c in text or ""):
            return text
        try:
            return self._apply(text, lang)
        except Exception:  # noqa: BLE001 - a library problem must never stop a reply
            log.debug("spoken numbers left unchanged for %s", lang)   # never the text
            return text

    def _apply(self, text: str, lang: str) -> str:
        tag = (lang or "").replace("_", "-")
        primary = tag.split("-", 1)[0].lower()
        if self.langs is not None and primary not in self.langs:
            return text
        code = _code(tag)
        if code is None:
            return text
        locale = Locale.parse(tag, sep="-")
        words: Callable[[object], str] = lambda value: num2words(value, lang=code)  # noqa: E731

        text = CLOCK.sub(lambda m: _clock(m, words), text)

        dec = re.escape(get_decimal_symbol(locale))
        grp = re.escape(get_group_symbol(locale))
        number = re.compile(
            rf"(?<!\d)(?<!\d[{dec}{grp}])"
            rf"(?:\d{{1,3}}(?:[{grp}]\d{{3}})+(?:[{dec}]\d+)?|\d+[{dec}]\d+)"
            rf"(?!\d)(?![{dec}{grp}]\d)")

        def spoken(m: re.Match) -> str:
            try:
                value = parse_decimal(m.group(0), locale, strict=True)
            except NumberFormatError:
                return m.group(0)
            if value == value.to_integral_value():
                value = int(value)
            return words(value)

        return number.sub(spoken, text)


def _clock(m: re.Match, words: Callable[[object], str]) -> str:
    hour, minutes = int(m.group(1)), int(m.group(2))
    out = words(hour)
    if minutes:
        out += " " + words(minutes)
    return out
