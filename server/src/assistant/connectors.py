"""Rows for the Connectors screen (GET /api/connectors), built from the service catalog (catalog.py) and the table of how each
service connects here (connect_ways.py).

One row: {"id", "name", "description", "category", "status": connected|available|browser, "connect": device|mcp|mail|key|
browser|google, "via", "item", "site", "steps", "fallback"?: {another way: "connect", "via", "item", "blurb", "connected"},
"method"?: how it IS connected when that differs by account (google = Google sign-in, password = app password, key), "account"?: who,
"connect_state"?: "pending_review" (Google sign-in not open yet; "note" says so in words, "support_url"? a link to show under it),
"limited": bool (anything the owner set under "What Kelo may do" differs from the default: permissions.py), "featured": bool, "rank": int|null, "featured_regions": [ISO country codes], "icon"?, "icon_small"?, "icon_color"?: the service's own
app icon (connector_icons.py; left out when there is none)}.
Services that cannot be used here are left out.

Featured: FEATURED is shown first everywhere (rank 0..37, roughly by worldwide use). A service with `regions` (catalog.Service) is
featured too when the request comes from one of those regions, ranked after the worldwide list. The region comes from the request's
Accept-Language (a region subtag such as ko-KR -> KR, or a language's home region such as ko -> KR); without the header, from the
owner's stored app language (i18n.current()).
"""

from __future__ import annotations

from typing import Any

from . import connector_icons, i18n
from .catalog import CATALOG, HIDDEN, HIDDEN_WAYS
from .i18n import N_, t
from .packages import Registry
from .presets import NETWORK_IDS, SELF_HOSTED_IDS

CATEGORY = N_("Service")
SELF_HOSTED_CATEGORY = N_("Self-hosted")
NETWORK_CATEGORY = N_("Network")
# Shown first when the Connectors screen opens: services used widely around the world, roughly by popularity. Everything else is
# still listed ("featured": false) and found by search or "show all".
FEATURED = ["gmail", "google_calendar", "google_drive", "outlook_mail", "outlook_calendar", "imap_email", "notion", "slack", "zoom", "github",
            "dropbox", "google_docs", "google_sheets", "spotify", "instagram", "facebook_cli", "messenger_read", "canva", "trello",
            "todoist", "asana", "atlassian", "figma", "paypal", "zapier", "airtable", "miro", "monday", "clickup", "calendly",
            "evernote", "google_slides", "google_contacts", "box", "threads", "wordpress_com", "expedia", "deepl"]
# the region a language is mostly used in, for a header without a region subtag
LANGUAGE_REGION = {"ko": "KR", "ja": "JP", "zh": "CN", "de": "DE", "fr": "FR", "es": "ES", "pt": "BR", "it": "IT", "nl": "NL",
                   "pl": "PL", "tr": "TR", "ru": "RU", "vi": "VN", "th": "TH", "id": "ID", "hi": "IN", "ar": "SA", "sv": "SE"}


def regions_for(accept_language: str | None) -> set[str]:
    """Regions of the device: every region subtag in the header plus the home region of each language; without a header, the
    home region of the owner's stored language."""
    out: set[str] = set()
    tags = [p.split(";")[0].strip().replace("_", "-") for p in (accept_language or "").split(",") if p.strip()]
    if not tags:
        tags = [i18n.current()]
    for tag in tags:
        bits = tag.split("-")
        if len(bits) > 1 and len(bits[-1]) == 2 and bits[-1].isalpha():
            out.add(bits[-1].upper())
        if region := LANGUAGE_REGION.get(bits[0].lower()):
            out.add(region)
    return out


def list_connectors(*, connected: set[str] | None = None, available: dict[str, str] | None = None,
                    registry: Registry | None = None) -> list[dict[str, Any]]:
    """The bare catalog as rows ("coming_soon" unless connected / available). With `registry`, installed connector packages follow
    the built-in catalog (their name and description are already in the reader's language: packages.Package.text)."""
    connected, available = connected or set(), available or {}
    services = registry.services() if registry is not None else CATALOG
    builtin = {s.id for s in CATALOG}

    def icons(cid: str) -> dict[str, str]:
        return connector_icons.for_connector(cid) if cid in builtin or registry is None else registry.icon_fields(cid)

    return [{"id": s.id, "name": t(s.name) if s.regions else s.name,
             "description": available.get(s.id) or (t(s.description) if s.regions else s.description), "category": t(SELF_HOSTED_CATEGORY if s.id in SELF_HOSTED_IDS else NETWORK_CATEGORY if s.id in NETWORK_IDS else CATEGORY),
             "status": "connected" if s.id in connected else "available" if s.id in available else "coming_soon",
             "featured_regions": list(s.regions), **icons(s.id)} for s in services if s.id not in HIDDEN or s.id in connected]


def connector_rows(table: dict[str, dict[str, Any]], *, regions: set[str] | None = None,
                   registry: Registry | None = None) -> list[dict[str, Any]]:
    """The Connectors screen: catalog rows merged with how each one connects (`table` = connect_ways.ways(...)). Installed connector
    packages that ask to be featured come after the regional ones."""
    regions = regions or set()
    services = registry.services() if registry is not None else CATALOG
    local = [s.id for s in services if set(s.regions) & regions]
    local += [cid for cid in (registry.featured_ids() if registry is not None else []) if cid not in local]
    out = []
    for r in list_connectors(registry=registry):
        w = table.get(r["id"])
        if w is None:
            out.append({**r, "connect": None, "via": "", "item": "", "site": "", "featured": False, "rank": None})
            continue
        if not w.get("connect"):
            continue   # cannot be used in this app: not listed (the assistant still learns why from connect_service)
        if w.get("connect") in HIDDEN_WAYS and not w.get("connected"):
            continue   # sites the agent just opens in its browser: not shown as connectors
        status = ("connected" if w.get("connected") else w["connect"] if w.get("connect") == "browser" else "available")
        rank = FEATURED.index(r["id"]) if r["id"] in FEATURED else len(FEATURED) + local.index(r["id"]) if r["id"] in local else None
        blurb = w.get("blurb") or w.get("reason") or r["description"]
        if r["featured_regions"]:   # a regional service: its own line first, then how it is used here
            blurb = f"{r['description']}. {blurb}"
        out.append({**r, "status": status, "description": blurb, "connect": w.get("connect"), "via": w.get("via", ""),
                    "item": w.get("item", ""), "site": w.get("site", ""), "steps": w.get("steps", []),
                    **({"fallback": w["fallback"]} if w.get("fallback") else {}),
                    **({k: w[k] for k in ("method", "account", "connect_state", "note", "support_url") if w.get(k)}),
                    **({"source": "installed", "author": w.get("author", "")} if w.get("installed") else {}),
                    "featured": rank is not None, "rank": rank})
    return out
