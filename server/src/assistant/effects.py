"""What a tool call does, in one place: the effect of a step on a connector.

`effect_of(tool, args)` says, for the tools that reach a connector (mail, calendars, the phone, MCP services, services connected with a
key, apps on the owner's network, Nest packages), whether the call reads, changes, sends or deletes, on which connector, at what
targets, and with what message text. Three things read it:
  * schedule_grants.action_of: the standing permissions of a task ("send" / "change", deletes?) are a thin view of it;
  * permissions.py: the limits the owner sets per connector in the app (Connectors -> <name> -> What Kelo may do);
  * privacy_guard.py: a step that sends something outside is checked before it goes.

`Effect(kind, connector, targets, text, action)`:
  kind       "read" | "change" | "send" | "delete"
  connector  the connector's catalog id ("gmail", "google_calendar", "phone", "slack", "home_assistant", a Nest package id ...) or None
             for a step that reaches no connector (a click in the agent's browser: its target is "site:<domain>")
  targets    who or what it reaches, comparable (schedule_grants.norm_target): "kim@x.org", a phone number's digits, "calendar",
             "connector:<name>[/<tool>]", "site:<domain>"
  text       the message text of a send (mail body, text message, typed value)
  action     the id the owner's per-action setting uses: the tool's name, an MCP server's own tool name, or one of the two virtual
             ids for a deleting variant of a tool ("email_trash", "connector_delete")

How the kind of an MCP tool is found (mcp_kind), never by the words in its name: the server's annotations (destructiveHint -> delete,
readOnlyHint true -> read), then the connector's manifest (connector_rules.json, the per-method declaration, which can only tighten),
then ONE cached judge call per tool (tool_judge.ToolJudge, strict JSON kind + own_check, run by ensure_judged before the call is
checked). Unknown (no judge, an error) = a change that keeps its own card. keeps_own_checks reads the same sources.
`docs/connector-permissions.md` is generated from the tables below by tools/connector_effects.py (a test keeps it in step).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from . import connector_rules
from .i18n import N_

KINDS = ("read", "change", "send", "delete")
KIND_LABELS = {"read": N_("Read"), "change": N_("Change"), "send": N_("Send"), "delete": N_("Delete")}


@dataclass(frozen=True)
class Effect:
    kind: str
    connector: str | None
    targets: list[str] = field(default_factory=list)
    text: str = ""
    action: str = ""
    policy: str = ""   # "" | "standing" | "task" | "fresh": how strongly connector_rules.json protects this method ("" = no declaration)


@dataclass(frozen=True)
class ActionInfo:
    id: str
    label: str            # N_ key (translated by whoever shows it)
    kind: str


# ---------------------------------------------------------------------------------------------------------- the tables
# Every tool that reaches a connector, per family of connectors: the actions the owner can set one by one. A family's kinds are the
# kinds of its actions. Labels are short and translated (locales/<lang>.json).
MAIL_ACTIONS = (
    ActionInfo("email_list", N_("Find mail"), "read"),
    ActionInfo("email_read", N_("Read mail"), "read"),
    ActionInfo("email_update", N_("Organize mail"), "change"),
    ActionInfo("email_send", N_("Send mail"), "send"),
    ActionInfo("email_trash", N_("Move mail to the trash"), "delete"),
)
CALENDAR_ACTIONS = (   # Google Calendar (gcal_* tools; the account argument picks which sign-in, the connector stays one)
    ActionInfo("gcal_calendars", N_("List calendars"), "read"),
    ActionInfo("gcal_events", N_("Read events"), "read"),
    ActionInfo("gcal_create_event", N_("Add events"), "change"),
    ActionInfo("gcal_update_event", N_("Change events"), "change"),
    ActionInfo("gcal_delete_event", N_("Delete events"), "delete"),
)
READ_CALENDAR_ACTIONS = (ActionInfo("calendar_events", N_("Read events"), "read"),)   # Outlook, CalDAV: read only
PHONE_ACTIONS = (
    ActionInfo("phone_contacts", N_("Read contacts"), "read"),
    ActionInfo("phone_messages", N_("Read text messages"), "read"),
    ActionInfo("phone_call_log", N_("Read the call log"), "read"),
    ActionInfo("phone_notifications", N_("Read notifications"), "read"),
    ActionInfo("phone_health", N_("Read health data"), "read"),
    ActionInfo("phone_calendar_events", N_("Read the phone's calendar"), "read"),
    ActionInfo("phone_calendar_add", N_("Add calendar events"), "change"),
    ActionInfo("phone_calendar_update", N_("Change calendar events"), "change"),
    ActionInfo("phone_send_text", N_("Send text messages"), "send"),
    ActionInfo("phone_call", N_("Start phone calls"), "send"),
    ActionInfo("phone_calendar_delete", N_("Delete calendar events"), "delete"),
)
KEY_ACTIONS = (        # a service connected with a key, and an app on the owner's own network
    ActionInfo("connector_get", N_("Read data"), "read"),
    ActionInfo("connector_download", N_("Save files to the library"), "read"),
    ActionInfo("connector_send", N_("Change data"), "change"),
    ActionInfo("connector_delete", N_("Delete data"), "delete"),
)
KEY_SEND_ACTION = ActionInfo("connector_send", N_("Send messages"), "send")   # a preset that posts to people (presets.KeyPreset.sends)
MCP_ACTIONS = (ActionInfo("mcp_list_tools", N_("List the service's tools"), "read"),)   # the service's own tools are added as they are known
PACKAGE_ACTIONS = (    # tools of a Nest package that keeps entries on this server
    ActionInfo("package_entries", N_("Read entries"), "read"),
    ActionInfo("package_entry_add", N_("Add entries"), "change"),
    ActionInfo("package_entry_delete", N_("Delete entries"), "delete"),
)
FAMILIES: dict[str, tuple[ActionInfo, ...]] = {
    "mail": MAIL_ACTIONS, "calendar": CALENDAR_ACTIONS, "calendar_read": READ_CALENDAR_ACTIONS, "phone": PHONE_ACTIONS,
    "key": KEY_ACTIONS, "mcp": MCP_ACTIONS, "package": PACKAGE_ACTIONS,
}
# tool name -> (the family it belongs to, the kinds one call of it may have): what hides a tool whose every target is off
TOOL_FAMILY: dict[str, tuple[str, tuple[str, ...]]] = {
    "email_list": ("mail", ("read",)), "email_read": ("mail", ("read",)), "email_send": ("mail", ("send",)),
    "email_update": ("mail", ("change", "delete")),
    "gcal_calendars": ("calendar", ("read",)), "gcal_events": ("calendar", ("read",)),
    "gcal_create_event": ("calendar", ("change",)), "gcal_update_event": ("calendar", ("change",)),
    "gcal_delete_event": ("calendar", ("delete",)),
    **{a.id: ("phone", (a.kind,)) for a in PHONE_ACTIONS},
    "connector_get": ("key", ("read",)), "connector_download": ("key", ("read",)), "connector_send": ("key", ("change", "send", "delete")),
    "mcp_list_tools": ("mcp", ("read",)), "mcp_call_tool": ("mcp", KINDS),
    "package_entries": ("package", ("read",)), "package_entry_add": ("package", ("change",)),
    "package_entry_delete": ("package", ("delete",)),
}
# the tool behind an action id (virtual ids point at the tool that does it)
ACTION_TOOL = {"email_trash": "email_update", "connector_delete": "connector_send"}


# A call that keeps its own checks whatever the owner set: payments, purchases, card and PIN steps, sign-ups and account steps.
OWN_CHECK_TOOLS = frozenset({
    "confirm_purchase", "checkout_review", "checkout_submit", "order_preapprove", "purchase_result", "begin_order", "end_order", "browser_fill_card", "browser_fill_shipping", "request_card", "request_payment_pin",
    "browser_type_payment_pin", "browser_pin_keypad_card", "payment_step_wait", "browser_login", "android_login", "request_login", "add_service_key",
    "save_found_secret", "request_task_permission", "task_allow",
})
# MCP tools as the servers described them: {connector name: {tool: annotations}}; filled from mcp_list_tools (remember_mcp_tools)
_MCP_TOOLS: dict[str, dict[str, dict[str, Any]]] = {}


on_mcp_tools: Any = None   # (name, rows): told when a server's tool list was read (permissions.py keeps it across restarts)


def remember_mcp_tools(name: str, rows: Any, *, persist: bool = True) -> None:
    """The tools a connected MCP server listed (the `tools` of a tools/list answer): their annotations and description decide the kind
    of a call. The description is kept inside the tool's annotation dict (key "description") so it is saved with them."""
    if not isinstance(rows, list):
        return
    got: dict[str, dict[str, Any]] = {}
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"]:
            ann = dict(r["annotations"]) if isinstance(r.get("annotations"), dict) else {}
            if isinstance(r.get("description"), str) and r["description"].strip():
                ann["description"] = r["description"].strip()[:500]
            got[r["name"]] = ann
    if got:
        key = (name or "").strip().casefold()
        before = _MCP_TOOLS.get(key, {})
        for tool, ann in got.items():   # a changed description is judged again
            if tool in before and before[tool].get("description") != ann.get("description"):
                _JUDGED.pop((key, tool), None)
        _MCP_TOOLS[key] = {**before, **got}
        if persist and on_mcp_tools is not None:
            on_mcp_tools(key, rows)


def known_mcp_tools(name: str) -> dict[str, dict[str, Any]]:
    return dict(_MCP_TOOLS.get((name or "").strip().casefold(), {}))


def forget_mcp_tools() -> None:
    _MCP_TOOLS.clear()
    _JUDGED.clear()


KIND_RANK = {"read": 0, "change": 1, "send": 2, "delete": 3}   # how strict a kind is: a higher one always asks first

# What the tool judge said about a generic call: {(connector name, MCP tool or "METHOD /path"): ToolVerdict}; filled by ensure_judged
_JUDGED: dict[tuple[str, str], Any] = {}
JUDGE: Any = None   # tool_judge.ToolJudge (server.py wires it with the main model); None = no model: unknown calls keep a card


def judged(cname: str, sub: str) -> Any:
    return _JUDGED.get(((cname or "").strip().casefold(), sub))


def remember_judged(cname: str, sub: str, kind: str, own_check: bool) -> None:
    """Put a verdict in the cache (the judge's answer; a test's setup)."""
    from .tool_judge import ToolVerdict
    _JUDGED[((cname or "").strip().casefold(), sub)] = ToolVerdict(kind, own_check)


def mcp_kind(sub: str, annotations: dict[str, Any] | None = None, cname: str = "") -> str:
    """read | change | send | delete for one MCP tool, never from the words of its name: the server's destructiveHint -> delete, its
    readOnlyHint true -> read, else what the tool judge said (ensure_judged), else change (unknown asks). readOnlyHint false is never a
    read."""
    ann = annotations or {}
    if ann.get("destructiveHint") is True:
        return "delete"
    if ann.get("readOnlyHint") is True:
        return "read"
    v = judged(cname, sub)
    kind = v.kind if v is not None else "change"
    if ann.get("readOnlyHint") is False and KIND_RANK[kind] < KIND_RANK["change"]:
        return "change"
    return kind


async def ensure_judged(tool: Any, args: dict[str, Any]) -> None:
    """Before a generic call is checked: ask the tool judge once about an MCP tool or a REST request that no annotation, manifest or
    earlier answer covers. A failed request leaves nothing cached (the call then keeps its card)."""
    name = getattr(tool, "name", tool)
    if JUDGE is None or name not in ("mcp_call_tool", "connector_send"):
        return
    cname = str(args.get("name") or "").strip().casefold()
    conn = credential_connector(cname)
    if name == "mcp_call_tool":
        sub = str(args.get("tool") or "").strip()
        ann = known_mcp_tools(cname).get(sub) or {}
        if not sub or ann.get("readOnlyHint") is True or judged(cname, sub) is not None or connector_rules.rules().for_tool(conn, sub, args) is not None:
            return
        schema = args.get("arguments")
        context = {"connector": cname, "tool": sub, "description": str(ann.get("description") or ""),
                   "argument_names": sorted(schema)[:20] if isinstance(schema, dict) else [],
                   "server_hints": {k: v for k, v in ann.items() if k in ("readOnlyHint", "destructiveHint", "openWorldHint", "idempotentHint")}}
        key = f"mcp|{cname}|{sub}|{context['description']}"
    else:
        method = str(args.get("method") or "POST").upper()
        path = urlsplit(str(args.get("url") or "")).path or "/"
        sub = f"{method} {path}"
        if method == "GET" or judged(cname, sub) is not None or connector_rules.rules().for_request(conn, method, str(args.get("url") or ""), args) is not None:
            return
        body = args.get("json")
        context = {"connector": cname, "http_method": method, "path": path, "body_keys": sorted(body)[:20] if isinstance(body, dict) else []}
        key = f"rest|{cname}|{sub}"
    verdict = await JUDGE.judge(key, context)
    if verdict is not None:
        _JUDGED[(cname, sub)] = verdict


# ---------------------------------------------------------------------------------------------------------- ids and targets
def _host(value: str) -> str:
    text = (value or "").strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    return text.split("/", 1)[0].split(":", 1)[0]


def site_target(value: str) -> str:
    from .wallet import site_key
    return site_key(_host(value))


def _cal_target(calendar: Any) -> str:
    """The place a calendar change writes to: "calendar" (the account's own main calendar) or "calendar:<id>" (another one)."""
    cal = str(calendar or "").strip().casefold()
    return "calendar" if cal in ("", "primary") else f"calendar:{cal}"


def _event_text(args: dict[str, Any]) -> str:
    """The words a calendar change writes: its title, place and note (what others could have written)."""
    return "\n".join(str(args[k]) for k in ("title", "location", "description") if str(args.get(k) or "").strip())


def norm_target(value: str) -> str:
    """One recipient or place in a comparable form: an email (lower case), a phone number (its digits), "site:<domain>",
    "connector:<name>[/<tool>]", "calendar", or a contact name (case-folded)."""
    text = " ".join(str(value or "").split()).strip()
    low = text.casefold()
    if not low:
        return ""
    if low == "calendar":   # the calendar effects name their target so (one place for every calendar connector)
        return "calendar"
    if low.startswith(("calendar:", "event:")):   # another calendar, or one event of a calendar (a Google calendar id may hold "@")
        return low
    if low.startswith("site:"):
        return "site:" + site_target(low[5:])
    if low.startswith("connector:"):
        return "connector:" + low[10:].strip()
    if "@" in low and " " not in low:
        return low
    digits = re.sub(r"\D", "", low)
    if len(digits) >= 6 and not re.search(r"[^\d\s+\-().]", low):
        return digits[-10:]   # the last 10 digits: +82 10-... and 010-... compare the same
    if re.fullmatch(r"(https?://)?[a-z0-9.-]+\.[a-z]{2,}(/.*)?", low):
        return "site:" + site_target(low)
    return low


def mail_connector(account: str, accounts: Any = None) -> str | None:
    """The catalog id of the mailbox an email tool names (account "gmail", "mail-naver", "outlook", "nest-<id>", or an address).
    `accounts`: a function giving the connected mailboxes ([{"name", "provider", "address"}]), to answer for an empty account when
    exactly one is connected and for an address."""
    from .mail import PROVIDERS
    from .microsoft_mail import ACCOUNT as GRAPH_ACCOUNT
    a = (account or "").strip().casefold()
    boxes: list[dict[str, Any]] = []
    if accounts is not None:
        try:
            boxes = list(accounts())
        except Exception:  # noqa: BLE001 - an id we cannot resolve is judged by its name
            boxes = []
    if not a and len(boxes) == 1:
        a = str(boxes[0].get("name") or "").casefold()
    elif a and boxes:
        hit = next((b for b in boxes if a in (str(b.get("name") or "").casefold(), str(b.get("address") or "").casefold())), None)
        if hit is not None:
            a = str(hit.get("name") or "").casefold()
    if not a:
        return None
    if a == GRAPH_ACCOUNT:   # the Graph account's own name
        return "outlook_mail"
    if a.startswith("nest-"):
        return a[5:] or None
    p = PROVIDERS.get(a.removeprefix("mail-"))
    return p.connectors[0] if p else None


_ITEM_IDS: dict[str, str] | None = None


def _item_ids() -> dict[str, str]:
    """vault item name -> the connector's catalog id, for what is built into the server (the vault name is what the tools are given)."""
    global _ITEM_IDS
    if _ITEM_IDS is None:
        from .mcp_oauth import SERVICES
        from .oauth_connect import PROVIDERS as DEVICE
        from .presets import GOOGLE_CONNECTORS, KEYS
        out: dict[str, str] = {}
        for p in DEVICE.values():
            out.update({p.item: p.serves[0]})
        out.update({s.item: s.id for s in SERVICES.values()})
        out.update({k.item: k.id for k in KEYS.values()})
        out["google-sa"] = GOOGLE_CONNECTORS[0]
        _ITEM_IDS = out
    return _ITEM_IDS


def credential_connector(name: str, aliases: Any = None) -> str | None:
    """The connector a tool's `name` (the vault item of a key, MCP or self-hosted connection) belongs to."""
    key = (name or "").strip().casefold()
    if not key:
        return None
    if aliases is not None:
        try:
            got = aliases(key)
        except Exception:  # noqa: BLE001
            got = None
        if got:
            return str(got)
    return _item_ids().get(key, key)


def preset_sends(connector: str | None) -> bool:
    """Whether this key service posts to people (a messenger): its change calls are sends (presets.KeyPreset.sends)."""
    if not connector:
        return False
    from .presets import KEYS
    p = KEYS.get(connector)
    return bool(p is not None and p.sends)


ACCOUNT_COMMITS = frozenset({"security"})   # a browser step that changes an account's security or issues an official document: always a card


def keeps_own_checks(tool: Any, args: dict[str, Any], commit: str = "") -> bool:
    """A step the owner's "allow" never switches off: payments, purchases, card and PIN steps, sign-ups and account steps (their own
    checks always run). For the generic tools (MCP, a key service) the manifest, the server's hints or the tool judge decide; unknown asks."""
    name = getattr(tool, "name", tool)
    if name in OWN_CHECK_TOOLS or commit in ACCOUNT_COMMITS:
        return True
    if name in ("connector_get", "connector_download", "connector_send"):
        from . import forbidden
        if (hit := forbidden.classify(name, args)) is not None and hit.rule.mode == "ask":
            return True   # a call Kelo always asks about (forbidden.py)
    if name == "mcp_call_tool":
        cname, sub = str(args.get("name") or "").strip().casefold(), str(args.get("tool") or "").strip()
        if connector_rules.rules().for_tool(credential_connector(cname), sub, args) is not None:
            return False   # declared in the connector's manifest: its policy decides
        if (known_mcp_tools(cname).get(sub) or {}).get("readOnlyHint") is True:
            return False   # a read cannot pay (the server's own hint)
        v = judged(cname, sub)
        return True if v is None else v.own_check   # unknown: a card
    if name == "connector_send":
        cname, method, url = str(args.get("name") or "").strip().casefold(), str(args.get("method") or "POST").upper(), str(args.get("url") or "")
        if connector_rules.rules().for_request(credential_connector(cname), method, url, args) is not None:
            return False
        v = judged(cname, f"{method} {urlsplit(url).path or '/'}")
        return True if v is None else v.own_check
    return False


def _bound(cname: str, recipients: list[str]) -> list[str]:
    """The targets of a call whose connector declares where its recipient is: one per recipient, or the connector whole (which a
    recipient-bound permission does not cover: fail closed) when the call names none."""
    return [connector_rules.bound_target(cname, r) for r in recipients] or [f"connector:{cname}"]


# ---------------------------------------------------------------------------------------------------------- the classifier
def effect_of(tool: Any, args: dict[str, Any] | None, *, site: str = "", commit: str = "", accounts: Any = None,
              aliases: Any = None) -> Effect | None:
    """What this call does, or None when it reaches no connector and is no step a permission could name (a file, a note, a payment, ...).
    `site` and `commit` (commits.py's class of a click or typing step) are for the agent's browser; `accounts` (the connected
    mailboxes) and `aliases` (vault item -> catalog id) give an exact connector where the arguments alone do not."""
    name = str(getattr(tool, "name", tool) or "")
    args = args if isinstance(args, dict) else {}
    if name in ("email_list", "email_read"):
        box = str(args.get("account") or "").strip().casefold().removeprefix("mail-") or "mail"
        return Effect("read", mail_connector(str(args.get("account") or ""), accounts), [f"connector:{box}"], "", name)
    if name == "email_send":
        from .prepared_send import recipients_of   # every To, Cc and Bcc address the message will really reach
        # the designed (HTML) version is part of the text: the approval digest, done-once and the outside-text rule all read it
        text = "\n\n".join(x for x in (str(args.get("body") or ""), str(args.get("html") or "")) if x)
        return Effect("send", mail_connector(str(args.get("account") or ""), accounts),
                      [norm_target(x) for x in recipients_of(args)], text, name)
    if name == "email_update":
        box = str(args.get("account") or "").strip().casefold().removeprefix("mail-") or "mail"
        trash = str(args.get("action") or "").strip().lower() == "trash"
        # only trash changes what leaves the owner's mailbox: read, unread, flag, archive and move name no outside place (no card after outside text)
        return Effect("delete" if trash else "change", mail_connector(str(args.get("account") or ""), accounts),
                      [f"connector:{box}"] if trash else [], "", "email_trash" if trash else name)
    if name == "phone_send_text":
        return Effect("send", "phone", [norm_target(str(args.get("to") or ""))], str(args.get("text") or ""), name)
    if name == "phone_call":
        return Effect("send", "phone", [norm_target(str(args.get("to") or args.get("number") or ""))], "", name)
    if name in ("gcal_calendars", "gcal_events"):
        return Effect("read", "google_calendar", ["calendar"], "", name)
    if name in ("gcal_create_event", "gcal_update_event", "gcal_delete_event"):
        # the calendar it writes to, and the event it changes (an update or a deletion has no text form of its own)
        cal_target = _cal_target(args.get("calendar"))
        event = [f"event:{str(args.get('event_id') or '').strip()}"] if name != "gcal_create_event" and args.get("event_id") else []
        text = "" if name == "gcal_delete_event" else _event_text(args)
        return Effect("delete" if name == "gcal_delete_event" else "change", "google_calendar", [cal_target, *event], text, name)
    if name in ("phone_calendar_add", "phone_calendar_update", "phone_calendar_delete"):
        # the phone writes only to the calendar the owner picked in the app; an update or deletion names its event
        event = [f"event:{str(args.get('event_id') or '').strip()}"] if name != "phone_calendar_add" and args.get("event_id") is not None else []
        text = "" if name == "phone_calendar_delete" else _event_text(args)
        return Effect("delete" if name == "phone_calendar_delete" else "change", "phone", ["calendar", *event], text, name)
    if name in PHONE_READS:
        return Effect("read", "phone", [], "", name)
    if name == "calendar_events":
        return Effect("read", None, ["calendar"], "", name)   # reads every calendar the owner chose (briefing sources): no one connector
    if name in ("connector_get", "connector_download", "connector_send"):
        cname = str(args.get("name") or "").strip().casefold()
        conn = credential_connector(cname, aliases)
        method = str(args.get("method") or ("GET" if name != "connector_send" else "POST")).upper()
        if name != "connector_send" or method == "GET":
            return Effect("read", conn, [f"connector:{cname}"], "", name)
        body = args.get("json")
        kind = "delete" if method == "DELETE" else "send" if preset_sends(conn) else "change"
        if (said := judged(cname, f"{method} {urlsplit(str(args.get('url') or '')).path or '/'}")) is not None:
            kind = connector_rules.strictest(kind, said.kind)   # the tool judge can only tighten
        found = connector_rules.rules().for_request(conn, method, str(args.get("url") or ""), args)
        if found is not None:   # declared in data: its kind can only be stricter, its recipients bind the call
            kind = connector_rules.strictest(kind, found.method.kind)
            return Effect(kind, conn, _bound(cname, found.recipients), body if isinstance(body, str) else "",
                          "connector_delete" if kind == "delete" else name, found.policy)
        return Effect(kind, conn, [f"connector:{cname}"], "" if kind == "delete" else body if isinstance(body, str) else "",
                      "connector_delete" if kind == "delete" else name)
    if name in ("mcp_list_tools", "mcp_call_tool"):
        cname = str(args.get("name") or "").strip().casefold()
        conn = credential_connector(cname, aliases)
        if name == "mcp_list_tools":
            return Effect("read", conn, [f"connector:{cname}"], "", name)
        sub = str(args.get("tool") or "").strip()
        kind = mcp_kind(sub, known_mcp_tools(cname).get(sub), cname)
        extra = args.get("arguments")
        found = connector_rules.rules().for_tool(conn, sub, args)
        policy = ""
        targets = [f"connector:{cname}/{sub.casefold()}"]
        if found is not None:
            kind, policy = connector_rules.strictest(kind, found.method.kind), found.policy
            targets = _bound(cname, found.recipients) if found.recipients else targets
        return Effect(kind, conn, targets,
                      " ".join(str(v) for v in extra.values() if isinstance(v, str)) if kind == "send" and isinstance(extra, dict) else "", sub, policy)
    if name in ("package_entries", "package_entry_add", "package_entry_delete"):
        pkg = str(args.get("package") or "").strip().casefold()
        kind = {"package_entries": "read", "package_entry_add": "change", "package_entry_delete": "delete"}[name]
        return Effect(kind, pkg or None, [f"connector:{pkg}"], "", name)
    if name in ("browser_click", "browser_click_at", "browser_drag", "browser_type", "android_tap", "android_type") and commit and site:
        # a phone app's package stands for the site ("app:<package>")
        where = f"app:{site.strip().casefold()}" if name.startswith("android_") else "site:" + site_target(site)
        if commit in ("send", "form", "personal", "apply"):
            return Effect("send", None, [where], str(args.get("text") or "") if name in ("browser_type", "android_type") else "", name)
        if commit == "delete":
            return Effect("delete", None, [where], "", name)
    return None


PHONE_READS = frozenset(a.id for a in PHONE_ACTIONS if a.kind == "read")
# The tools action_of (standing permissions of a task) has always covered; the rest of effect_of is new and is not a permission's business
GRANTABLE = frozenset({"email_send", "email_update", "phone_send_text", "gcal_create_event", "gcal_update_event", "gcal_delete_event",
                       "phone_calendar_add", "phone_calendar_update", "phone_calendar_delete", "connector_send", "mcp_call_tool",
                       "browser_click", "browser_click_at", "browser_drag", "browser_type", "android_tap", "android_type"})


def all_actions(family: str, *, sends: bool = False) -> tuple[ActionInfo, ...]:
    """The actions of a family of connectors (a key service that posts to people has its change action as a send)."""
    acts = FAMILIES[family]
    if family == "key" and sends:
        return tuple(KEY_SEND_ACTION if a.id == "connector_send" else a for a in acts)
    return acts
