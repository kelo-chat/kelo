"""A sign-in that an app hands to a browser (OAuth in the system browser): when the saved login for the app may be typed there.

The login is saved for the app (`app:<package>`), so by default it is typed only while that app is in front. An app that opens its own
sign-in page in a browser is still the app's sign-in, and it is proven by structure, asked from the device (no site, package or word list):
  1. the app in front is a browser: it handles VIEW for the page's https address;
  2. the page is https and carries a `redirect_uri` (or `redirect_url`) parameter that comes back to the bound app: a custom scheme that
     the device resolves only to the bound package, or an https address whose host has a verified App Link for the bound package;
  3. the page's HOST belongs to the app: its registrable domain (Public Suffix List) equals that of a host the bound package has a
     VERIFIED App Link for (a wildcard counts by its domain). `redirect_uri` is the page's own parameter, so an attacker's page can
     carry it; only the host ties the page to the app. No verified links: refused;
  4. where the device tells who launched the browser activity, it is the bound package (unknown: rule 3 alone decides);
  5. (the caller) the sign-in fields are on the screen.
Everything here only reads: the questions are the bridge's read-only endpoints (DeviceClient.handlers / intent_data / app_links)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from .device import DeviceClient, DeviceError
from ..tools.sites import registrable
from .screen import Screen

REDIRECT_PARAMS = ("redirect_uri", "redirect_url")


def address_of(screen: Screen) -> str:
    """The full web address a browser shows in its address field (an edit or text node whose words are one http(s) address), "" if none."""
    for i in screen.items:
        words = (i.text or "").strip()
        if i.kind in ("edit", "text") and words.lower().startswith(("http://", "https://")) and " " not in words:
            return words
    return ""


def redirect_of(url: str) -> str:
    """The value of the page address's redirect_uri / redirect_url parameter ("" when there is none)."""
    query = parse_qs(urlsplit(url).query)
    for name in REDIRECT_PARAMS:
        if query.get(name) and query[name][0].strip():
            return query[name][0].strip()
    return ""


def _host_matches(host: str, pattern: str) -> bool:
    host, pattern = host.lower(), pattern.lower()
    return host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[2:])


async def page_address(client: DeviceClient, screen: Screen, launched: str | None = None) -> str:
    """The sign-in page's address: the one the app's intent opened (the bridge), else the browser's address field. When both are
    known they must be the same host, else "" (the page in front is not the page the app opened)."""
    if launched is None:
        try:
            launched = await client.intent_data()
        except DeviceError:
            launched = ""
    shown = address_of(screen)
    launched = launched if launched.lower().startswith(("http://", "https://")) else ""
    if launched and shown and (urlsplit(launched).hostname or "") != (urlsplit(shown).hostname or ""):
        return ""
    return launched or shown


async def refusal(client: DeviceClient, screen: Screen, bound: list[str]) -> str:
    """"" when the screen is the sign-in page that a bound app opened in a browser; else why not (for the log, not the model)."""
    bound = [b for b in bound if b]
    if not bound or not screen.package:
        return "no app is bound to this login"
    try:
        data, caller = await client.launch()
    except DeviceError:
        data, caller = "", ""
    url = await page_address(client, screen, data)
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return "the page is not an https page"
    try:
        if screen.package not in await client.handlers(url):
            return "the app in front is not a browser"
        domains = {registrable(h.removeprefix("*.")) for p in bound for h in await client.app_links(p)}
        if registrable(parts.hostname) not in domains:
            return "the page's host does not belong to the app (no verified App Link on its domain)"
        if caller and caller not in bound:
            return "the browser was opened by another app"
        target = redirect_of(url)
        if not target:
            return "the page has no redirect back to an app"
        back = urlsplit(target)
        if back.scheme in ("http", "https"):
            if back.scheme != "https" or not back.hostname:
                return "the redirect is not https"
            for package in bound:
                if any(_host_matches(back.hostname, h) for h in await client.app_links(package)):
                    return ""
            return "the redirect address has no verified App Link for the app"
        owners = await client.handlers(target)
    except DeviceError as e:
        return f"the device could not answer ({str(e)[:60]})"
    if owners and all(o in bound for o in owners):
        return ""
    return "the redirect comes back to another app"
