"""Each user's own language sets the tabs' locale (Intl, navigator.language), the browser default when none is set; the time zone
is the user's profile zone (BrowserExtras). Pure mapping plus the tab override over a fake DevTools session."""

import pytest

from assistant.tools.browser_locale import REGION, tab_locale
from test_browser_more import session_for, probe, CheckPage   # the same fakes as the time-zone test
from assistant.tools.browser_more import BrowserExtras


@pytest.mark.parametrize("app,default,expected", [
    ("ja", "ko-KR", "ja-JP"),          # the user's language wins over the browser default
    ("ko", "en-US", "ko-KR"),
    ("pt-BR", "ko-KR", "pt-BR"),
    ("zh-Hant", "ko-KR", "zh-TW"),
    ("", "ko-KR", "ko-KR"),            # no language set: today's default
    ("", "", ""),                      # nothing set anywhere: the browser's own stays
    ("sv", "ko-KR", "sv"),             # a language tag without a table entry is used as it is
    ("../etc", "ko-KR", "ko-KR"),      # not a tag: the default
])
def test_the_tab_locale_is_the_users_language_else_the_default(app, default, expected):
    assert tab_locale(app, default) == expected


def test_every_app_language_has_a_region():
    from pathlib import Path
    locales = Path(__file__).resolve().parents[1] / "src" / "assistant" / "locales"
    for f in locales.glob("*.json"):
        assert f.stem in REGION, f.stem


async def test_a_new_tab_gets_the_users_language_and_the_profile_zone_not_the_default():
    sent = []

    class Cdp:
        async def send(self, m, p=None):
            sent.append((m, p))
            return {"targetInfo": {"targetId": "T1", "browserContextId": "C1"}} if m == "Target.getTargetInfo" else {}

    async def cdp(_page):
        return Cdp()

    s = session_for(CheckPage(probe()), cdp=cdp)
    s.prepare_pages = True
    BrowserExtras(s, timezone=lambda: "Asia/Tokyo", locale="ko-KR", language=lambda: "ja")
    s.downloads = None
    await s.page()
    assert ("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Tokyo"}) in sent
    assert ("Emulation.setLocaleOverride", {"locale": "ja-JP"}) in sent
    assert ("Emulation.setLocaleOverride", {"locale": "ko-KR"}) not in sent
