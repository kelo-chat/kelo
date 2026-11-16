"""Text the user sees, in the user's language.

English is the source language. Every user-facing string in the code is written in English and passed through `t()` where it is
shown (or marked with `N_()` where it is defined as a constant and translated later, at the point of output). Translations live in
`locales/<lang>.json`: one flat object per language whose keys are the exact English templates and whose values are the translations.
A missing key falls back to English, so a new language can be added one file at a time.

Which language: the `Accept-Language` header of the request (the app or a browser sends it), else the owner's language (kept in the
store's meta table as `ui_lang`), else English. The server sets the per-request language with `use()`; code outside a request reads
`current()`.

The owner's language (`ui_lang`, with where it came from in `ui_lang_from`) is what every background and scheduled job speaks:
notifications, the morning briefing, heads-ups, scheduled runs. It is the language the owner chose in the app (My → Language; the app
sends it with PUT /api/language when it is picked, "follow the phone" as the phone's language then). It is never changed by the
language of a single request: a request's Accept-Language only fills it while no choice was made yet (an empty value, or the setup's
guess from the owner's own messages), so another device, another app or a browser in another language can not flip it. Jobs started
from inside a request (a briefing brought early by an app open, a run started from a button) run in the owner's language too
(`owner_language()`), not in that request's.

Templates use `str.format` fields: `t("Saved {name}", name=x)`. Translators keep the `{fields}` as they are.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextvars import ContextVar, Token
from functools import cache
from pathlib import Path

DEFAULT = "en"
LOCALES = Path(__file__).with_name("locales")
_request_lang: ContextVar[str | None] = ContextVar("kelo_lang", default=None)
_fallback: list[Callable[[], str | None]] = []   # set by the server: the owner's last known app language


@cache
def catalog(lang: str) -> dict[str, str]:
    """The translations of one language ({} for English or a language without a file)."""
    if lang == DEFAULT or not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})?", lang or ""):
        return {}
    try:
        data = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def available() -> list[str]:
    return [DEFAULT, *sorted(p.stem for p in LOCALES.glob("*.json"))]


def pick(header: str | None) -> str | None:
    """The best language this server has for an Accept-Language header ("ko-KR,ko;q=0.9,en;q=0.8" -> "ko"). None without a header."""
    if not header or not header.strip():
        return None
    have = set(available())
    wanted: list[tuple[float, int, str]] = []
    for i, part in enumerate(header.split(",")):
        bits = part.strip().split(";")
        tag = bits[0].strip()
        if not tag or tag == "*":
            continue
        q = 1.0
        for b in bits[1:]:
            b = b.strip()
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        if q > 0:
            wanted.append((-q, i, tag))
    for _q, _i, tag in sorted(wanted):
        for cand in candidates(tag):
            if cand in have:
                return cand
    return DEFAULT


_TRADITIONAL_REGIONS = {"TW", "HK", "MO"}
_ALIASES = {"in": "id", "iw": "he", "ji": "yi"}   # legacy codes Java / old Android still send


def candidates(tag: str) -> list[str]:
    """Locale file names that can serve a language tag, best first: "zh-Hant-HK" -> ["zh-Hant"], "pt-PT" -> ["pt-BR", "pt"],
    "de-AT" -> ["de-AT", "de"], "in-ID" -> ["id-ID", "id"]."""
    parts = [p for p in tag.replace("_", "-").split("-") if p]
    if not parts:
        return []
    lang = _ALIASES.get(parts[0].lower(), parts[0].lower())
    rest = parts[1:]
    if lang == "zh":   # Chinese is split by script: Traditional (Taiwan, Hong Kong, Macau) or Simplified (the rest)
        script = next((p.title() for p in rest if len(p) == 4 and p.isalpha()), "")
        region = next((p.upper() for p in rest if len(p) == 2 and p.isalpha()), "")
        traditional = script == "Hant" or (not script and region in _TRADITIONAL_REGIONS)
        return ["zh-Hant" if traditional else "zh-Hans", "zh"]
    if lang == "pt":   # one Portuguese: Brazilian
        return ["pt-BR", "pt"]
    full = "-".join([lang, *rest])
    return [full, lang] if rest else [lang]


def set_fallback(fn: Callable[[], str | None] | None) -> None:
    _fallback.clear()
    if fn is not None:
        _fallback.append(fn)


def use(lang: str | None) -> Token:
    """Set the language for the rest of this request / task. Returns a token for `reset()`."""
    return _request_lang.set(lang)


def reset(token: Token) -> None:
    _request_lang.reset(token)


def owner() -> str:
    """The owner's language (the fallback the server set: `ui_lang`), else English: what background jobs speak."""
    for fn in _fallback:
        try:
            got = fn()
        except Exception:  # noqa: BLE001
            got = None
        if got:
            return got
    return DEFAULT


class owner_language:
    """`with owner_language():` the block speaks the owner's language, whatever request started it (async-safe: a context variable)."""

    def __enter__(self) -> str:
        lang = owner()
        self._token = _request_lang.set(lang)
        return lang

    def __exit__(self, *_exc: object) -> None:
        _request_lang.reset(self._token)


# The language a model is told to write in, in English (the prompt's words), and the scripts that language is written in: a reply in
# another script was written in another language (a model that ignored "Language: ko").
NAMES = {"en": "English", "ar": "Arabic", "de": "German", "es": "Spanish", "fr": "French", "hi": "Hindi", "id": "Indonesian",
         "it": "Italian", "ja": "Japanese", "ko": "Korean", "nl": "Dutch", "pl": "Polish", "pt-BR": "Brazilian Portuguese",
         "ru": "Russian", "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "vi": "Vietnamese", "zh-Hans": "Simplified Chinese",
         "zh-Hant": "Traditional Chinese"}
_SCRIPT = {"ko": r"[\uac00-\ud7a3\u3131-\u318e]", "ja": r"[\u3040-\u30ff\u4e00-\u9fff]", "zh-Hans": r"[\u4e00-\u9fff]",
           "zh-Hant": r"[\u4e00-\u9fff]", "ru": r"[\u0400-\u04ff]", "uk": r"[\u0400-\u04ff]", "ar": r"[\u0600-\u06ff]",
           "hi": r"[\u0900-\u097f]", "th": r"[\u0e00-\u0e7f]"}
_LATIN = r"[A-Za-z\u00c0-\u024f]"


def written_in(text: str, lang: str) -> bool:
    """Whether `text` looks written in `lang`, by the script of its words: a fair share of them in that language's script (Hangul for
    Korean, kana or kanji for Japanese, Latin for English, German, Vietnamese ...). Numbers and symbols do not count, and a few names in
    another script (an event's English title in a Korean line) do not make it another language. Latin-script languages are not told
    apart from each other."""
    words = re.findall(r"[^\W\d_]+", text or "")
    if len(words) < 2:
        return True
    want = re.compile(_SCRIPT.get(lang, _LATIN))
    mine = sum(1 for w in words if sum(1 for ch in w if want.match(ch)) * 2 > len(w))
    return mine >= len(words) * (0.5 if lang not in _SCRIPT else 0.34)


def owner_lang() -> str | None:
    """The owner's last known app language (not the language of whoever made this request: a website visitor, for example)."""
    for fn in _fallback:
        try:
            if lang := fn():
                return lang
        except Exception:  # noqa: BLE001
            continue
    return None


def current() -> str:
    lang = _request_lang.get()
    if lang:
        return lang
    for fn in _fallback:
        try:
            got = fn()
        except Exception:  # noqa: BLE001 - a broken store must never stop a message from being shown
            got = None
        if got:
            return got
    return DEFAULT


def t(text: str, /, lang: str | None = None, **fields: object) -> str:
    """`text` (an English template) in the user's language, with `fields` filled in."""
    template = catalog(lang or current()).get(text, text)
    if not fields:
        return template
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):   # a translation with a broken field: show the English one
        return text.format(**fields)


def N_(text: str) -> str:
    """Marks an English constant for translation where it is shown (the extraction test finds these). Returns it unchanged."""
    return text


__all__ = ["DEFAULT", "NAMES", "N_", "available", "candidates", "catalog", "current", "owner", "owner_language", "pick", "reset",
           "owner_lang", "set_fallback", "t", "use", "written_in"]
