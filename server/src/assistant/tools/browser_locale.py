"""The language a tab of the browser speaks: the user's own app language (the app's setting, as a locale), else the browser's default.

One rule for every context of a tab (page, frames, workers) because the tab's locale override is what they all read: Intl and
navigator.language follow it. The user's time zone is set per tab the same way (BrowserExtras, timezone); nothing is faked.
"""

from __future__ import annotations

import re

# the app's language codes (locales/*.json) -> the region a desktop Chrome of that language reports
REGION = {
    "ar": "ar-SA", "de": "de-DE", "en": "en-US", "es": "es-ES", "fr": "fr-FR", "hi": "hi-IN", "id": "id-ID", "it": "it-IT",
    "ja": "ja-JP", "ko": "ko-KR", "nl": "nl-NL", "pl": "pl-PL", "pt-BR": "pt-BR", "ru": "ru-RU", "th": "th-TH", "tr": "tr-TR",
    "uk": "uk-UA", "vi": "vi-VN", "zh-Hans": "zh-CN", "zh-Hant": "zh-TW",
}
_TAG = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4}|-[A-Za-z]{4}-[A-Za-z]{2}|-[0-9]{3})?$")


def tab_locale(app_language: str, browser_default: str) -> str:
    """The locale for a tab: the user's app language mapped to its region ("ja" -> "ja-JP"), or `browser_default` when the user
    has no language set (or it is not a language tag). "" when neither is set (the browser's own stays)."""
    lang = (app_language or "").strip()
    if lang in REGION:
        return REGION[lang]
    if lang and _TAG.match(lang):
        return lang
    return (browser_default or "").strip()
