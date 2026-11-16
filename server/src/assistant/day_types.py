"""Weekday or weekend: the owner's contact hours, the mascot's night and the morning briefing follow two windows, one for weekdays and
one for weekends and public holidays (active_hours.py learns both, proactive.py keeps them).

Which country's holidays: the country of the profile's time zone (the tz database's zone.tab when the system has it, else a short built-in table), else none
(only the weekend days count). Holidays come from the `holidays` package (MIT licence; computed, lunar holidays and substitute days
included, Korea among them); a country it does not know has no holidays here, only its weekend.

Which days are the weekend: the country's own weekend from the same package (Saturday and Sunday in most places; Friday and Saturday in
some), Saturday and Sunday when unknown.

Which day a moment belongs to: a window can run past midnight (10:00–02:00), so a moment belongs to the local date it is DAY_CUT_HOURS
earlier: 01:00 on Saturday is still Friday night and follows the weekday window. Apps that show the window (the mascot's night) get
the day types of the next days from the server (`upcoming`), so they never need a holiday table of their own.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WEEKDAY, WEEKEND = "weekday", "weekend"
DAY_TYPES = (WEEKDAY, WEEKEND)
DAY_CUT_HOURS = 4

# A short table for systems without zone.tab: the zones most owners have (the rest falls back to "no holidays, Sat/Sun weekend").
_ZONES = {
    "Asia/Seoul": "KR", "Asia/Tokyo": "JP", "Asia/Shanghai": "CN", "Asia/Hong_Kong": "HK", "Asia/Taipei": "TW", "Asia/Singapore": "SG",
    "Asia/Bangkok": "TH", "Asia/Ho_Chi_Minh": "VN", "Asia/Jakarta": "ID", "Asia/Kolkata": "IN", "Asia/Calcutta": "IN", "Asia/Manila": "PH",
    "Asia/Kuala_Lumpur": "MY", "Asia/Dubai": "AE", "Asia/Riyadh": "SA", "Asia/Jerusalem": "IL", "Europe/Istanbul": "TR",
    "Europe/London": "GB", "Europe/Dublin": "IE", "Europe/Paris": "FR", "Europe/Berlin": "DE", "Europe/Madrid": "ES", "Europe/Rome": "IT",
    "Europe/Amsterdam": "NL", "Europe/Brussels": "BE", "Europe/Vienna": "AT", "Europe/Zurich": "CH", "Europe/Stockholm": "SE",
    "Europe/Oslo": "NO", "Europe/Copenhagen": "DK", "Europe/Helsinki": "FI", "Europe/Warsaw": "PL", "Europe/Prague": "CZ",
    "Europe/Lisbon": "PT", "Europe/Athens": "GR", "Europe/Kyiv": "UA", "Europe/Kiev": "UA", "Europe/Moscow": "RU",
    "America/New_York": "US", "America/Chicago": "US", "America/Denver": "US", "America/Los_Angeles": "US", "America/Phoenix": "US",
    "America/Anchorage": "US", "Pacific/Honolulu": "US", "America/Toronto": "CA", "America/Vancouver": "CA", "America/Mexico_City": "MX",
    "America/Sao_Paulo": "BR", "America/Argentina/Buenos_Aires": "AR", "America/Bogota": "CO", "America/Santiago": "CL",
    "America/Lima": "PE", "Australia/Sydney": "AU", "Australia/Melbourne": "AU", "Australia/Brisbane": "AU", "Australia/Perth": "AU",
    "Pacific/Auckland": "NZ", "Africa/Cairo": "EG", "Africa/Johannesburg": "ZA", "Africa/Lagos": "NG", "Africa/Nairobi": "KE",
}


@lru_cache(maxsize=1)
def _zone_tab() -> dict[str, str]:
    for p in (Path("/usr/share/zoneinfo/zone.tab"), Path("/usr/share/zoneinfo/zone1970.tab")):
        try:
            out: dict[str, str] = {}
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    out.setdefault(parts[2].strip(), parts[0].split(",")[0].strip().upper())
            if out:
                return out
        except OSError:
            continue
    return {}


def country_of_zone(zone: str | None) -> str:
    z = (zone or "").strip()
    return _ZONES.get(z) or _zone_tab().get(z, "")


def country_for(store: Any) -> str:
    """The owner's country for holidays: the time zone's ("" when unknown)."""
    return country_of_zone(store.profile().get("timezone"))


@lru_cache(maxsize=64)
def _calendar(country: str, year: int) -> tuple[frozenset[date], frozenset[int]]:
    """(the public holidays of that year, the weekend weekdays 0=Mon..6=Sun) of a country."""
    if not country:
        return frozenset(), frozenset({5, 6})
    try:
        import holidays
        h = holidays.country_holidays(country, years=year)
    except Exception:  # noqa: BLE001 - an unknown country or the package missing: the plain weekend
        return frozenset(), frozenset({5, 6})
    weekend = frozenset(int(d) for d in (getattr(h, "weekend", None) or {5, 6}))
    return frozenset(h.keys()), weekend or frozenset({5, 6})


def holiday(d: date, country: str) -> bool:
    return d in _calendar(country, d.year)[0]


def day_type(d: date, country: str) -> str:
    """weekday | weekend (public holidays count as weekend)."""
    days, weekend = _calendar(country, d.year)
    return WEEKEND if d.weekday() in weekend or d in days else WEEKDAY


def logical_date(ts: float, tz: ZoneInfo) -> date:
    """The day a moment belongs to for the contact hours: DAY_CUT_HOURS earlier, so a late night stays with the evening before."""
    return (datetime.fromtimestamp(ts, tz) - timedelta(hours=DAY_CUT_HOURS)).date()


def type_at(ts: float, tz: ZoneInfo, country: str) -> str:
    return day_type(logical_date(ts, tz), country)


def upcoming(ts: float, tz: ZoneInfo, country: str, days: int = 9) -> dict[str, str]:
    """{YYYY-MM-DD: weekday|weekend} from yesterday on, for the apps (they pick today's window without a holiday table)."""
    first = datetime.fromtimestamp(ts, tz).date() - timedelta(days=1)
    return {(first + timedelta(days=i)).isoformat(): day_type(first + timedelta(days=i), country) for i in range(days)}


__all__ = ["DAY_CUT_HOURS", "DAY_TYPES", "WEEKDAY", "WEEKEND", "country_for", "country_of_zone", "day_type", "holiday", "logical_date",
           "type_at", "upcoming"]
