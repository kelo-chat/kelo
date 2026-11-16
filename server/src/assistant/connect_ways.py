"""For every service in the catalog: how it is connected in this app, which vault item holds it, and whether it is connected now.
The Connectors tab, the assistant's connect_service tool and the skills list all read this one table."""

from __future__ import annotations

from typing import Any

from .calendars import generic_accounts
from .catalog import BY_ID, HIDDEN, SIGN_IN_CAUTION
from .mail import PROVIDERS as MAIL, Mail
from .mcp_oauth import SERVICES as MCP, refused
from .oauth_connect import PROVIDERS as DEVICE
from .presets import BROWSER, GOOGLE, GOOGLE_CONNECTORS, KEYS, UNSUPPORTED, browser_hint
from .google_web import REVIEW_NOTE, support_url
from .i18n import N_, t
from .packages import Package, Registry
from .store import Store
from .vpn import VpnRoute

CALENDAR_ITEM = "google-calendar"
CALENDAR_BLURB = N_("Sees your schedule and adds, moves or deletes events. Each change is asked first.")
CALDAV_BLURB = N_("Reads your calendar (read only) from any provider that offers CalDAV, with an app password.")
MAIL_BLURB = {"gmail": N_("Finds, reads and organizes your Gmail (mark read, archive, trash), and sends mail when you allow it."),
              "imap_email": N_("Connects Naver, Daum or iCloud mail with an app password. Kelo finds, reads and organizes it, and sends "
                               "mail when you allow it.")}


def _pending(store: Store) -> dict[str, Any]:
    """What a Google row carries while new Google sign-ins are closed (google_web.py): the state, the note, and the support link if set."""
    url = support_url(store)
    return {"connect_state": "pending_review", "note": t(REVIEW_NOTE), **({"support_url": url} if url else {})}


def ways(store: Store, mail: Mail, *, browser: bool, google_open: bool = False, registry: Registry | None = None,
         relay: str = "", vpn: VpnRoute | None = None) -> dict[str, dict[str, Any]]:
    """`relay`: the fixed page sign-ins return to (the address an organisation's admin has to allow, in a caution note).
    `google_open`: new Google sign-ins are open (GoogleWeb.open_to_all()). While closed, Gmail and Google Calendar rows that are not
    connected yet say "pending_review" (Gmail keeps the app password as the other way); connected ones stay as they are.
    `vpn`: the VPN / Proxy connectors (vpn.py) are listed only where the vpn container exists, and a connected one shows "exit <ip>".
    `registry`: installed connector packages (packages.py) are added after the built-in ones; a built-in id always wins."""
    have = {c["name"] for c in store.credentials() if c["has_secret"]}
    out: dict[str, dict[str, Any]] = {}
    for p in DEVICE.values():
        for cid in p.serves:
            out[cid] = {"connect": "device", "via": p.id, "item": p.item, "blurb": t(p.blurb), "connected": p.item in have}
    for s in MCP.values():
        out[s.id] = {"connect": "mcp", "via": s.id, "item": s.item, "blurb": t(s.blurb), "connected": s.item in have}
    signed = {c["name"]: c for c in store.credentials() if c["has_secret"]}
    for cid in ("gmail", "imap_email"):
        # a Gmail mailbox belongs to the gmail connector only: "IMAP Email" counts the other mailboxes (Naver, Daum, iCloud, ...)
        mine = [a for a in mail.accounts() if cid in MAIL[a["provider"]].connectors and (cid == "gmail" or a["provider"] != "gmail")]
        row = {"connect": "mail", "via": cid, "item": mine[0]["name"] if mine else "", "blurb": t(MAIL_BLURB[cid]), "connected": bool(mine)}
        if mine:   # how it is connected and with which address, for the detail screen's facts
            oauth = (signed.get(mine[0]["name"]) or {}).get("placement", {}).get("via") == "oauth"
            row.update({"method": "google" if oauth else "password", "account": ", ".join(a["address"] for a in mine)})
        elif cid == "gmail" and not google_open:
            row.update(_pending(store))
        out[cid] = row
    mine = generic_accounts(store)
    out["caldav_calendar"] = {"connect": "caldav", "via": "caldav_calendar", "item": mine[0]["name"] if mine else "",
                              "blurb": t(CALDAV_BLURB), "connected": bool(mine),
                              **({"method": "password", "account": ", ".join(a["address"] for a in mine)} if mine else {})}
    for k in KEYS.values():
        if k.route and (vpn is None or not vpn.available):
            out[k.id] = {"connect": None, "reason": t("This installation has no VPN support.")}   # not listed (connectors.connector_rows)
            continue
        out[k.id] = {"connect": "key", "via": k.id, "item": k.item, "blurb": t(k.blurb), "connected": k.item in have}
        if k.route and k.item in have and vpn is not None and (account := vpn.account()) and vpn.kind == k.route:
            out[k.id]["account"] = account
    for cid in GOOGLE_CONNECTORS:
        out[cid] = {"connect": "key", "via": GOOGLE.id, "item": GOOGLE.item, "blurb": t(GOOGLE.blurb), "connected": GOOGLE.item in have}
    # Google Calendar: the Google sign-in first (google_calendar.py); the service account stays as the other way
    signed_in = sorted(n for n in have if n == CALENDAR_ITEM or n.startswith(CALENDAR_ITEM + "-"))
    row = {"connect": "google", "via": "calendar", "item": signed_in[0] if signed_in else CALENDAR_ITEM,
           "blurb": t(CALENDAR_BLURB), "connected": bool(signed_in) or GOOGLE.item in have, "signed_in": bool(signed_in),
           "fallback": {**out["google_calendar"], "blurb": t(GOOGLE.blurb)}}
    if signed_in:
        row.update({"method": "google", "account": ", ".join(signed[n]["label"].split(" · ")[-1] for n in signed_in)})
    elif GOOGLE.item in have:
        row.update({"method": "key", "item": GOOGLE.item, "account": signed[GOOGLE.item]["label"].split(" · ")[-1] if " · " in signed[GOOGLE.item]["label"] else ""})
    if not google_open:
        if GOOGLE.item not in have:
            del row["fallback"]   # the service account stays out of the way unless it is how this calendar is connected
        if not row["connected"]:
            row.update(_pending(store))
    out["google_calendar"] = row
    for cid, site in BROWSER.items():
        out[cid] = ({"connect": "browser", "via": cid, "item": "", "site": site, "connected": False,
                     "blurb": t("The agent opens this site in its own browser. If a login is needed, leave it in the vault.") + " " + browser_hint(cid)}
                    if browser else {"connect": None, "reason": t("This app's browser is turned off, so this can't be used.")})
    for cid in SIGN_IN_CAUTION:
        if cid in out:
            out[cid] = cautioned(out[cid], cid, out, store, relay)
    for c in SIGN_IN_CAUTION.values():
        if c.instead and c.instead not in BY_ID:
            out.pop(c.instead, None)   # an other way only (a key preset that is no connector of its own): reached through its service's row
    for cid, reason in UNSUPPORTED.items():
        out[cid] = {"connect": None, "reason": t(reason)}
    for cid, reason in HIDDEN.items():
        # a service whose sign-in refuses this app (catalog.HIDDEN): no card for it, the assistant is told why; one already connected stays
        if cid in out and not out[cid].get("connected"):
            out[cid] = {"connect": None, "reason": reason}
    if registry is not None:
        for p in registry.installed("connector"):
            if p.id not in out:
                out[p.id] = installed_way(p, have, browser=browser)
    return out


def cautioned(row: dict[str, Any], cid: str, out: dict[str, dict[str, Any]], store: Store, relay: str) -> dict[str, Any]:
    """An MCP sign-in that may turn this app down only after the login (catalog.SIGN_IN_CAUTION): the row says so before the sign-in
    opens ("note"), with the other way when there is one ("fallback": a key row). Once the service has refused this app
    (mcp_oauth.refused), or the other way is the one connected, the row is that other way; with no other way it can't be connected."""
    if row.get("connect") != "mcp" or row.get("connected"):
        return row
    caution = SIGN_IN_CAUTION[cid]
    alt = out.get(caution.instead) if caution.instead else None
    name = MCP[cid].name
    if alt is not None and (alt.get("connected") or refused(store, cid)):
        return {**alt, **({} if alt.get("connected") else {"note": t("{name} turned this app's sign-in down, so connect it with a key you make on {name}.", name=name)})}
    if refused(store, cid):
        return {"connect": None, "reason": t("{name} only accepts apps it approved in advance, so this app can't connect to it.", name=name)}
    if caution.kind == "admin":
        note = (t("{name} lets each organization's admin choose which apps may connect. If its page shows an error after you log in, ask your admin to allow this return address: {address}", name=name, address=relay)
                if relay else t("{name} lets each organization's admin choose which apps may connect. If its page shows an error after you log in, ask your admin to allow this app's return address.", name=name))
    elif alt is not None:
        note = t("{name} may accept only apps it approved in advance, and that shows only after you log in. If its page shows an error then, connect with a key instead.", name=name)
    else:
        note = t("{name} may accept only apps it approved in advance, and that shows only after you log in. If its page shows an error then, it can't be connected from this app for now.", name=name)
    extra: dict[str, Any] = {"note": note}
    if alt is not None:
        extra["fallback"] = {"connect": "key", "via": alt["via"], "item": alt["item"], "blurb": alt["blurb"], "connected": False,
                             "title": t("Connect with a key instead")}
    return {**row, **extra}


def installed_way(p: Package, have: set[str], *, browser: bool) -> dict[str, Any]:
    """The row of an installed connector package: the same shape as a built-in row of its kind ("installed": True marks it)."""
    c, blurb = p.connect, p.text("blurb") or p.text("description")
    kind = c.get("type")
    if kind in ("mcp", "key"):
        return {"connect": kind, "via": p.id, "item": p.item, "blurb": blurb, "connected": p.item in have, "installed": True, "author": p.author}
    if kind == "oauth":   # an OAuth API package: the same sign-in button as an MCP server (POST /api/mcp-oauth/<id>/start)
        return {"connect": "mcp", "via": p.id, "item": p.item, "blurb": blurb, "connected": p.item in have, "installed": True, "author": p.author,
                "oauth": True, **({"client": "owner"} if c.get("client") == "owner" else {})}
    if kind == "browser":
        if not browser:
            return {"connect": None, "reason": t("This app's browser is turned off, so this can't be used."), "installed": True}
        hint = t("If the site blocks automated visits, signing in may not work.") if c.get("blocks_bots") else ""
        return {"connect": "browser", "via": p.id, "item": "", "site": c["url"], "connected": False, "installed": True, "author": p.author,
                "blurb": (blurb + " " if c.get("blurb") else "") + t("The agent opens this site in its own browser. If a login is needed, leave it in the vault.") + (" " + hint if hint else "")}
    return {"connect": None, "reason": p.text("reason") or c.get("reason", ""), "installed": True}
