"""Kelo Nest, the parts of a package manifest beyond skills and connectors: what a package may do (`permissions`), the screens it adds
to the apps (`ui`, a small declarative schema the apps render natively), and the entries it keeps on this server (`store`).

Nothing here runs code from a package. A package only *describes*: which hosts it talks to, which of the owner's data it reads, which
screens it shows and what their buttons do. The server checks every field here, computes the package's effective permissions from
what the manifest really uses, and enforces them at run time (nest.py). The format is in docs/nest.md and docs/nest-ui.md.

Permissions (`permissions` in the manifest, all optional; what the manifest uses must be declared once the section is present):

    hosts         where it sends requests (connector hosts, OAuth endpoints, MCP server, browser site, links its buttons open)
    image_hosts   where the pictures it shows come from (fetched by this server, never by the phone: no tracking)
    reads         the owner's data it reads: mail, calendar, contacts, messages, files, photos, location, health, wallet, tasks, memory
    scopes        OAuth scopes it asks for at sign-in
    mcp_tools     tools of its MCP server its screens call to show data (read only)
    ui            surfaces it adds: tab, my_section, home_card, chat_card
    actions       what its buttons can do: store, agent, connector, mcp, open_url
    store         whether it keeps entries on this server

A package written before Nest (no `permissions` section) gets exactly what its manifest uses, with no `reads`.

Capabilities (`provides`, connectors only): a connector that holds a mailbox or a calendar says so, with how Kelo's standard operations
map to its tools (an MCP connector, or a key connector with an mcp_url) or to requests on its API (a key or OAuth connector with a
base_url). Kelo's built-in features (the chat's email and calendar tools, the morning briefing, the heads-ups, the accounts list in
the briefing settings) then read it like a built-in mailbox or calendar, with no change to Kelo:

    "provides": {
      "mail": {                                     required: list, read, mark_read, archive, trash   optional: mark_unread
        "list":      {"tool": "search_messages", "arguments": {"unread_only": "{{unread}}", "max": "{{limit}}", "q": "{{query}}"},
                      "select": "messages", "fields": {"uid": "id", "subject": "subject", "from": "from.email", "date": "received",
                                                       "unread": "unread", "bulk": "is_promotion"}},
        "read":      {"tool": "get_message", "arguments": {"id": "{{uid}}"}, "fields": {"text": "body", "subject": "subject"}},
        "mark_read": {"tool": "set_read", "arguments": {"id": "{{uid}}", "read": true}},
        "archive":   {"request": {"method": "POST", "path": "/messages/{{uid}}/archive"}},
        "trash":     {"tool": "trash_message", "arguments": {"id": "{{uid}}"}}},
      "calendar": {                                 required: events
        "events":    {"tool": "list_events", "arguments": {"from": "{{start}}", "to": "{{end}}"}, "select": "items",
                      "fields": {"id": "id", "title": "summary", "start": "start", "end": "end", "where": "location", "all_day": "all_day"}}}}

Bindings ({{...}}, a whole value keeps its type): list: unread, limit, query, folder; read and the changes: uid; events: start, end
(ISO 8601 with the offset), start_date, end_date (YYYY-MM-DD). Fields name where each of Kelo's fields is in a row (dotted paths);
`select` is where the rows are in the answer. Required fields: list uid and subject, read text, events title and start. A package that
declares a capability without its required operations or fields is refused. `provides` implies `reads` (mail, calendar) and its tools
in `mcp_tools`, so the owner sees it before installing and can take it back.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from . import i18n
from .i18n import N_
from .netguard import NetGuardError, valid_host
from .packages import LANG, PackageError, _https, _line

UI_SCHEMA = 1                                  # the UI schema version this server reads and serves (docs/nest-ui.md)
READS = ("mail", "calendar", "contacts", "messages", "files", "photos", "location", "health", "wallet", "tasks", "memory")
PLACES = ("tab", "my_section", "home_card", "chat_card")
ACTION_KINDS = ("store", "agent", "connector", "mcp", "open_url")
PERMISSION_FIELDS = ("hosts", "image_hosts", "reads", "scopes", "mcp_tools", "ui", "actions", "store")
LIST_PERMISSIONS = ("hosts", "image_hosts", "reads", "scopes", "mcp_tools", "ui", "actions")
# the owner may take these back one by one without uninstalling (the rest make the package what it is: revoke = disable)
REVOCABLE = ("reads", "ui", "actions")

# what the screens may show, per surface kind, and in all
PLACE_LIMITS = {"tab": 1, "my_section": 1, "home_card": 2, "chat_card": 2}
MAX_SURFACES = 6
MAX_SCREENS = 8
MAX_COMPONENTS = 60                            # in one screen, every nested component counted
MAX_DEPTH = 3                                  # a card in a card in a screen, no deeper
MAX_DATA = 8
MAX_ACTIONS = 16
MAX_FIELDS = 12                                # in one form
MAX_OPTIONS = 30                               # in one select field
MAX_LIST = 100                                 # rows one list shows
MAX_POINTS = 60                                # points one chart shows
MAX_UI_BYTES = 48 * 1024                       # the ui section as JSON
MAX_TEXT = 200                                 # one text in a screen
MAX_PROMPT = 600                               # an agent action's request
MIN_REFRESH, MAX_REFRESH = 60, 86_400          # seconds a fetched answer is kept before it is fetched again
MAX_HOSTS = 8

# the icon set the apps carry (no pictures from packages in the bars: one look, nothing to fetch)
ICONS = ("home", "star", "heart", "check", "check_circle", "list", "calendar", "clock", "alarm", "bell", "box", "truck", "cart",
         "bag", "wallet", "card", "chart", "bar_chart", "book", "note", "pen", "camera", "image", "music", "video", "map", "pin", "globe",
         "sun", "cloud", "moon", "fire", "drop", "leaf", "bolt", "flag", "tag", "gift", "trophy", "target", "person", "people", "chat",
         "mail", "phone", "link", "search", "settings", "info", "warning", "plus", "refresh", "trash", "edit", "play", "lock", "key",
         "sparkle", "food", "coffee", "fitness", "pill", "plane", "car", "school", "work", "money", "weather", "news", "game")

COMPONENTS = ("header", "text", "stat", "list", "card", "chart", "image", "button", "form", "empty")
TEXT_STYLES = ("body", "caption", "title", "label")
BUTTON_STYLES = ("primary", "secondary", "danger")
FIELD_TYPES = ("text", "multiline", "number", "date", "time", "select", "toggle")
STORE_TYPES = ("text", "number", "bool", "date", "datetime")
DATA_SOURCES = ("store", "connector", "mcp", "kelo")
ACTION_TYPES = ("store_add", "store_delete", "agent", "connector", "mcp", "open_url", "refresh")
# the owner's data a screen can show through a `kelo` data source, and the permission each needs (wallet, mail and messages never:
# a skill may declare them, a screen cannot show them)
FEEDS = {"calendar.upcoming": "calendar", "tasks.recent": "tasks", "files.recent": "files"}

ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
FIELD_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
BINDING = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
REF = re.compile(r"^(item|form|data|count|now|today)((?:\.[A-Za-z0-9_-]{1,40}){0,6})$")
PATH = re.compile(r"^[A-Za-z0-9_-]{1,40}(?:\.[A-Za-z0-9_-]{1,40}){0,7}$")
SCOPE = re.compile(r"^[\x21\x23-\x5b\x5d-\x7e]{1,120}$")   # RFC 6749 scope-token characters
MCP_TOOL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# capabilities a connector may provide (`provides`): the operations of each, which are required, the bindings each operation's
# arguments may use, and Kelo's fields its answer maps (required ones first in REQUIRED_FIELDS)
PROVIDES = {"mail": {"list": ("unread", "limit", "query", "folder"), "read": ("uid",), "mark_read": ("uid",), "mark_unread": ("uid",),
                     "archive": ("uid",), "trash": ("uid",)},
            "calendar": {"events": ("start", "end", "start_date", "end_date")}}
REQUIRED_OPS = {"mail": ("list", "read", "mark_read", "archive", "trash"), "calendar": ("events",)}
OP_FIELDS = {"list": ("uid", "subject", "from", "date", "unread", "bulk"), "read": ("text", "from", "to", "cc", "subject", "date"),
             "events": ("title", "start", "id", "end", "where", "all_day")}
REQUIRED_FIELDS = {"list": ("uid", "subject"), "read": ("text",), "events": ("title", "start")}
HTTP_METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")


# ---------------------------------------------------------------------------------------------------------------- small checks
def _id(v: Any, what: str) -> str:
    if not isinstance(v, str) or not ID.fullmatch(v):
        raise PackageError(f"{what} must be an id (lower-case letters, digits and '_', starting with a letter, up to 32)")
    return v


def _obj(v: Any, what: str, allowed: Iterable[str], *, required: Iterable[str] = ()) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise PackageError(f"{what} must be an object")
    bad = set(v) - set(allowed)
    if bad:
        raise PackageError(f"{what}: unknown fields {', '.join(sorted(bad))}")
    missing = [k for k in required if k not in v]
    if missing:
        raise PackageError(f"{what}: missing {', '.join(missing)}")
    return v


def _int(v: Any, what: str, lo: int, hi: int, default: int | None = None) -> int:
    if v is None and default is not None:
        return default
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise PackageError(f"{what} must be a whole number from {lo} to {hi}")
    return v


def _bindings(s: str, what: str, *, data_ids: Iterable[str] = (), allow: Iterable[str] = ("item", "form", "data", "count", "now", "today")) -> None:
    allowed, ids = set(allow), set(data_ids)
    for m in BINDING.finditer(s):
        ref = REF.fullmatch(m.group(1))
        if not ref or ref.group(1) not in allowed:
            raise PackageError(f"{what}: {{{{{m.group(1)[:40]}}}}} is not something this place can show ({', '.join(sorted(allowed))})")
        kind, rest = ref.group(1), ref.group(2)
        if kind in ("data", "count"):
            first = rest[1:].split(".")[0] if rest else ""
            if first not in ids:
                raise PackageError(f"{what}: {{{{{m.group(1)[:40]}}}}} names no data source of this package")
        if kind in ("now", "today") and rest:
            raise PackageError(f"{what}: {{{{{kind}}}}} takes no path")
    if "{{" in BINDING.sub("", s) or "}}" in BINDING.sub("", s):
        raise PackageError(f"{what}: unbalanced {{{{ }}}}")


def text(v: Any, what: str, limit: int = MAX_TEXT, *, required: bool = False, data_ids: Iterable[str] = (),
         allow: Iterable[str] = ("item", "form", "data", "count", "now", "today")) -> Any:
    """A text in a screen: one line, or per language {"en": "...", "ko": "..."} ("en" required). May hold {{bindings}}."""
    if isinstance(v, dict):
        if not v or len(v) > 30 or "en" not in v:
            raise PackageError(f"{what} must be a text or an object of up to 30 languages with an \"en\" text")
        out = {}
        for lang, s in v.items():
            if not isinstance(lang, str) or not LANG.fullmatch(lang):
                raise PackageError(f"{what}: {lang!r} is not a language tag")
            out[lang] = _line(s, f"{what}.{lang}", limit, required=True)
            _bindings(out[lang], what, data_ids=data_ids, allow=allow)
        return MappingProxyType(out)
    s = _line(v, what, limit, required=required)
    _bindings(s, what, data_ids=data_ids, allow=allow)
    return s


def pick_text(v: Any, lang: str | None = None) -> str:
    """A text in the reader's language (a plain text is the same in every language)."""
    if isinstance(v, Mapping):
        for cand in [*i18n.candidates(lang or i18n.current()), i18n.DEFAULT]:
            if v.get(cand):
                return str(v[cand])
        return str(next(iter(v.values()), ""))
    return str(v or "")


def _hosts(v: Any, what: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or len(v) > MAX_HOSTS:
        raise PackageError(f"{what} must be a list of at most {MAX_HOSTS} host names")
    try:
        return tuple(sorted({valid_host(str(h)) for h in v}))
    except NetGuardError as e:
        raise PackageError(f"{what}: {e}") from None


def _words(v: Any, what: str, allowed: Iterable[str]) -> tuple[str, ...]:
    ok = tuple(allowed)
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) and x in ok for x in v):
        raise PackageError(f"{what} must be a list of: {', '.join(ok)}")
    return tuple(x for x in ok if x in v)


# ---------------------------------------------------------------------------------------------------------------- store
def parse_store(raw: Any) -> Mapping[str, Any]:
    """`store`: {"collections": {"<id>": {"fields": {"<field>": {"type", "max"?, "required"?}}, "max_records"?}}}."""
    if raw is None:
        return MappingProxyType({})
    _obj(raw, "store", ("collections",), required=("collections",))
    cols = raw["collections"]
    if not isinstance(cols, dict) or not 1 <= len(cols) <= 4:
        raise PackageError("store.collections must be an object of 1-4 collections")
    out: dict[str, Any] = {}
    for cid, spec in cols.items():
        _id(cid, "a store collection id")
        _obj(spec, f"store.collections.{cid}", ("fields", "max_records"), required=("fields",))
        fields = spec["fields"]
        if not isinstance(fields, dict) or not 1 <= len(fields) <= 12:
            raise PackageError(f"store.collections.{cid}.fields must be an object of 1-12 fields")
        fo: dict[str, Any] = {}
        for fid, f in fields.items():
            if not isinstance(fid, str) or not FIELD_ID.fullmatch(fid):
                raise PackageError(f"store.collections.{cid}: {fid!r} is not a field id")
            _obj(f, f"store.collections.{cid}.fields.{fid}", ("type", "max", "required"), required=("type",))
            if f["type"] not in STORE_TYPES:
                raise PackageError(f"store.collections.{cid}.fields.{fid}.type must be one of {', '.join(STORE_TYPES)}")
            req = f.get("required", False)
            if not isinstance(req, bool):
                raise PackageError(f"store.collections.{cid}.fields.{fid}.required must be true or false")
            fo[fid] = MappingProxyType({"type": f["type"], "max": _int(f.get("max"), f"{fid}.max", 1, 2000, 200), "required": req})
        out[cid] = MappingProxyType({"fields": MappingProxyType(fo), "max_records": _int(spec.get("max_records"), f"{cid}.max_records", 1, 1000, 200)})
    return MappingProxyType({"collections": MappingProxyType(out)})


# ---------------------------------------------------------------------------------------------------------------- ui
class _Ctx:
    def __init__(self, data: Mapping[str, Any], actions: Mapping[str, Any], image_hosts: set[str]):
        self.data, self.actions, self.image_hosts = data, actions, image_hosts
        self.count = 0
        self.uses_images = set()   # hosts of fixed image addresses


def _data_source(did: str, raw: Any, connect: Mapping[str, Any], store: Mapping[str, Any]) -> Mapping[str, Any]:
    what = f"ui.data.{did}"
    if not isinstance(raw, dict) or raw.get("source") not in DATA_SOURCES:
        raise PackageError(f"{what}.source must be one of {', '.join(DATA_SOURCES)}")
    src = raw["source"]
    if src == "store":
        _obj(raw, what, ("source", "collection", "limit", "order"), required=("collection",))
        if raw["collection"] not in (store.get("collections") or {}):
            raise PackageError(f"{what}.collection must be one of this package's store collections")
        order = raw.get("order", "newest")
        if order not in ("newest", "oldest"):
            raise PackageError(f"{what}.order must be newest or oldest")
        return MappingProxyType({"source": src, "collection": raw["collection"], "limit": _int(raw.get("limit"), f"{what}.limit", 1, MAX_LIST, 50),
                                 "order": order})
    if src == "connector":
        _obj(raw, what, ("source", "path", "query", "select", "refresh", "limit"), required=("path",))
        if connect.get("type") not in ("key", "oauth"):
            raise PackageError(f"{what}: a connector data source needs this package's own key or oauth connector")
        if not connect.get("base_url"):
            raise PackageError(f"{what}: the connector needs a base_url")
        path = raw["path"]
        if not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,300}", path) or "/../" in path + "/" or "//" in path:
            raise PackageError(f"{what}.path must be a plain path starting with / (no query: use query)")
        query = raw.get("query") or {}
        if not isinstance(query, dict) or len(query) > 12 or not all(isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,40}", k)
                                                                      and isinstance(v, (str, int)) and len(str(v)) <= 200 for k, v in query.items()):
            raise PackageError(f"{what}.query must be an object of up to 12 short values")
        placement = connect.get("placement") or {}
        if placement.get("type") == "query_param" and placement.get("param") in query:
            raise PackageError(f"{what}.query must not set the key's own parameter")
        for v in query.values():
            if "{{" in str(v):
                raise PackageError(f"{what}.query takes fixed values only")
        return MappingProxyType({"source": src, "path": path, "query": MappingProxyType({k: str(v) for k, v in query.items()}),
                                 "select": _select(raw.get("select"), what), "limit": _int(raw.get("limit"), f"{what}.limit", 1, MAX_LIST, 50),
                                 "refresh": _int(raw.get("refresh"), f"{what}.refresh", MIN_REFRESH, MAX_REFRESH, 300)})
    if src == "mcp":
        _obj(raw, what, ("source", "tool", "arguments", "select", "refresh", "limit"), required=("tool",))
        if connect.get("type") != "mcp":
            raise PackageError(f"{what}: an mcp data source needs this package's own mcp connector")
        tool = raw["tool"]
        if not isinstance(tool, str) or not MCP_TOOL.fullmatch(tool):
            raise PackageError(f"{what}.tool must be a tool name")
        args = raw.get("arguments") or {}
        if not isinstance(args, dict) or len(json.dumps(args)) > 2000 or "{{" in json.dumps(args):
            raise PackageError(f"{what}.arguments must be a small object of fixed values")
        return MappingProxyType({"source": src, "tool": tool, "arguments": MappingProxyType(dict(args)), "select": _select(raw.get("select"), what),
                                 "limit": _int(raw.get("limit"), f"{what}.limit", 1, MAX_LIST, 50),
                                 "refresh": _int(raw.get("refresh"), f"{what}.refresh", MIN_REFRESH, MAX_REFRESH, 300)})
    _obj(raw, what, ("source", "feed", "limit"), required=("feed",))
    if raw["feed"] not in FEEDS:
        raise PackageError(f"{what}.feed must be one of {', '.join(FEEDS)}")
    return MappingProxyType({"source": src, "feed": raw["feed"], "limit": _int(raw.get("limit"), f"{what}.limit", 1, 50, 20)})


def _select(v: Any, what: str) -> str:
    if v in (None, ""):
        return ""
    if not isinstance(v, str) or not PATH.fullmatch(v):
        raise PackageError(f"{what}.select must be a dotted path into the answer (data.items)")
    return v


def _template_obj(v: Any, what: str, *, depth: int = 0) -> Any:
    """A request body or tool arguments: JSON whose strings may hold {{form.x}}, {{item.x}}, {{now}}, {{today}}."""
    if depth > 4:
        raise PackageError(f"{what} nests too deep")
    if isinstance(v, dict):
        if len(v) > 20:
            raise PackageError(f"{what} has too many fields")
        return MappingProxyType({str(k)[:60]: _template_obj(x, what, depth=depth + 1) for k, x in v.items()})
    if isinstance(v, list):
        if len(v) > 20:
            raise PackageError(f"{what} has too many items")
        return tuple(_template_obj(x, what, depth=depth + 1) for x in v)
    if isinstance(v, str):
        if len(v) > 400:
            raise PackageError(f"{what} has a text longer than 400 characters")
        _bindings(v, what, allow=("item", "form", "now", "today"))
        return v
    if v is None or isinstance(v, (bool, int, float)):
        return v
    raise PackageError(f"{what} holds something that is not JSON")


def _action(aid: str, raw: Any, connect: Mapping[str, Any], store: Mapping[str, Any]) -> Mapping[str, Any]:
    what = f"ui.actions.{aid}"
    if not isinstance(raw, dict) or raw.get("type") not in ACTION_TYPES:
        raise PackageError(f"{what}.type must be one of {', '.join(ACTION_TYPES)}")
    kind = raw["type"]
    common = ("type", "label", "done")
    out: dict[str, Any] = {"type": kind, "label": text(raw.get("label"), f"{what}.label", 60, allow=()),
                           "done": text(raw.get("done"), f"{what}.done", 120, allow=())}
    cols = store.get("collections") or {}
    if kind in ("store_add", "store_delete"):
        _obj(raw, what, (*common, "collection", "values"), required=("collection",))
        if raw["collection"] not in cols:
            raise PackageError(f"{what}.collection must be one of this package's store collections")
        out["collection"] = raw["collection"]
        if kind == "store_add":
            values = raw.get("values")
            fields = cols[raw["collection"]]["fields"]
            if not isinstance(values, dict) or not values or set(values) - set(fields):
                raise PackageError(f"{what}.values must set fields of the collection ({', '.join(fields)})")
            out["values"] = MappingProxyType({k: _template_obj(v, f"{what}.values.{k}") for k, v in values.items()})
            for fid, f in fields.items():
                if f["required"] and fid not in values:
                    raise PackageError(f"{what}.values must set the required field {fid}")
        elif "values" in raw:
            raise PackageError(f"{what}: store_delete takes no values (it removes the row it is on)")
        return MappingProxyType(out)
    if kind == "agent":
        _obj(raw, what, (*common, "prompt"), required=("prompt", "label"))
        out["prompt"] = text(raw["prompt"], f"{what}.prompt", MAX_PROMPT, required=True, allow=())
        return MappingProxyType(out)
    if kind == "connector":
        _obj(raw, what, (*common, "method", "path", "body", "confirm"), required=("method", "path", "confirm"))
        if connect.get("type") not in ("key", "oauth") or not connect.get("base_url"):
            raise PackageError(f"{what}: a connector action needs this package's own key or oauth connector with a base_url")
        if raw["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            raise PackageError(f"{what}.method must be POST, PUT, PATCH or DELETE")
        path = raw["path"]
        if not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/{} -]{0,300}", path) or "/../" in path + "/" or "?" in path:
            raise PackageError(f"{what}.path must be a plain path starting with /")
        _bindings(path, f"{what}.path", allow=("item", "form"))
        out.update(method=raw["method"], path=path, body=_template_obj(raw.get("body"), f"{what}.body"),
                   confirm=text(raw["confirm"], f"{what}.confirm", MAX_TEXT, required=True, allow=()))
        return MappingProxyType(out)
    if kind == "mcp":
        _obj(raw, what, (*common, "tool", "arguments", "confirm"), required=("tool", "confirm"))
        if connect.get("type") != "mcp":
            raise PackageError(f"{what}: an mcp action needs this package's own mcp connector")
        if not isinstance(raw["tool"], str) or not MCP_TOOL.fullmatch(raw["tool"]):
            raise PackageError(f"{what}.tool must be a tool name")
        out.update(tool=raw["tool"], arguments=_template_obj(raw.get("arguments") or {}, f"{what}.arguments"),
                   confirm=text(raw["confirm"], f"{what}.confirm", MAX_TEXT, required=True, allow=()))
        return MappingProxyType(out)
    if kind == "open_url":
        _obj(raw, what, (*common, "url"), required=("url",))
        out["url"] = _https(raw["url"], f"{what}.url")
        return MappingProxyType(out)
    _obj(raw, what, common)
    return MappingProxyType(out)


def _component(raw: Any, ctx: _Ctx, depth: int, where: str, *, item_of: str = "") -> Mapping[str, Any]:
    """One component, checked. `item_of`: the data source of the list row it is in ({{item...}} allowed)."""
    ctx.count += 1
    if ctx.count > MAX_COMPONENTS:
        raise PackageError(f"{where}: more than {MAX_COMPONENTS} components in one screen")
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        raise PackageError(f"{where}: a component must be an object with a type")
    kind = raw["type"]
    ids = list(ctx.data)
    allow = ("data", "count", "now", "today", *(("item",) if item_of else ()))

    def tx(key: str, limit: int = MAX_TEXT, required: bool = False) -> Any:
        return text(raw.get(key), f"{where}.{key}", limit, required=required, data_ids=ids, allow=allow)

    def act(key: str = "action", *, aid: Any = None, row_of: str = "") -> str:
        """An action id on this component (`key`) or given (`aid`); `row_of`: the data source of the list row it is on."""
        aid = raw.get(key) if aid is None else aid
        if aid is None or aid == "":
            return ""
        if aid not in ctx.actions:
            raise PackageError(f"{where}.{key} must name one of this package's actions")
        a = ctx.actions[aid]
        if a["type"] == "store_delete":
            if not row_of:
                raise PackageError(f"{where}.{key}: store_delete works only on a list row")
            if ctx.data[row_of]["source"] != "store" or ctx.data[row_of]["collection"] != a["collection"]:
                raise PackageError(f"{where}.{key}: store_delete must be on a row of the same store collection")
        if row_of and ctx.data[row_of]["source"] == "kelo" and a["type"] in ("connector", "mcp"):
            raise PackageError(f"{where}.{key}: a row showing your own data cannot send it to an outside service")
        return aid

    if kind not in COMPONENTS:
        # a component of a newer schema: kept as a placeholder the apps show as "update the app" (nothing of it is passed on)
        return MappingProxyType({"type": "unsupported", "was": kind[:40]})
    if kind == "header":
        _obj(raw, where, ("type", "text", "subtitle"), required=("text",))
        return MappingProxyType({"type": kind, "text": tx("text", required=True), "subtitle": tx("subtitle")})
    if kind == "text":
        _obj(raw, where, ("type", "text", "style"), required=("text",))
        style = raw.get("style", "body")
        if style not in TEXT_STYLES:
            raise PackageError(f"{where}.style must be one of {', '.join(TEXT_STYLES)}")
        return MappingProxyType({"type": kind, "text": tx("text", 600, required=True), "style": style})
    if kind == "stat":
        _obj(raw, where, ("type", "label", "value", "unit", "caption", "icon"), required=("label", "value"))
        return MappingProxyType({"type": kind, "label": tx("label", 60, True), "value": tx("value", 60, True), "unit": tx("unit", 20),
                                 "caption": tx("caption"), "icon": _icon(raw.get("icon"), where, optional=True)})
    if kind == "empty":
        _obj(raw, where, ("type", "text", "icon", "action"), required=("text",))
        return MappingProxyType({"type": kind, "text": tx("text", required=True), "icon": _icon(raw.get("icon"), where, optional=True),
                                 "action": act()})
    if kind == "button":
        _obj(raw, where, ("type", "label", "action", "style"), required=("label", "action"))
        style = raw.get("style", "primary")
        if style not in BUTTON_STYLES:
            raise PackageError(f"{where}.style must be one of {', '.join(BUTTON_STYLES)}")
        return MappingProxyType({"type": kind, "label": tx("label", 60, True), "action": act(row_of=item_of), "style": style})
    if kind == "image":
        _obj(raw, where, ("type", "src", "alt", "height"), required=("src", "alt"))
        src = raw["src"]
        if not isinstance(src, str):
            raise PackageError(f"{where}.src must be an address or a binding")
        if BINDING.fullmatch(src.strip()):
            _bindings(src, f"{where}.src", data_ids=ids, allow=allow)
        else:
            url = _https(src, f"{where}.src")
            host = (urlsplit(url).hostname or "").lower()
            ctx.uses_images.add(host)
        return MappingProxyType({"type": kind, "src": src.strip(), "alt": tx("alt", 120, True),
                                 "height": _int(raw.get("height"), f"{where}.height", 40, 400, 160)})
    if kind == "chart":
        _obj(raw, where, ("type", "kind", "data", "x", "y", "label"), required=("kind", "data", "x", "y"))
        if raw["kind"] not in ("line", "bar"):
            raise PackageError(f"{where}.kind must be line or bar")
        if raw["data"] not in ctx.data:
            raise PackageError(f"{where}.data must name one of this package's data sources")
        for k in ("x", "y"):
            if not isinstance(raw[k], str) or not PATH.fullmatch(raw[k]):
                raise PackageError(f"{where}.{k} must be a field of the rows (a dotted path)")
        return MappingProxyType({"type": kind, "kind": raw["kind"], "data": raw["data"], "x": raw["x"], "y": raw["y"], "label": tx("label", 60)})
    if kind == "list":
        _obj(raw, where, ("type", "data", "item", "max", "empty"), required=("data", "item"))
        did = raw["data"]
        if did not in ctx.data:
            raise PackageError(f"{where}.data must name one of this package's data sources")
        item = raw["item"]
        _obj(item, f"{where}.item", ("title", "subtitle", "trailing", "icon", "image", "action"), required=("title",))
        row_ctx_allow = ("item", "data", "count", "now", "today")

        def it(key: str, limit: int = MAX_TEXT, required: bool = False) -> Any:
            return text(item.get(key), f"{where}.item.{key}", limit, required=required, data_ids=ids, allow=row_ctx_allow)

        image = item.get("image") or ""
        if image:
            if not isinstance(image, str) or not BINDING.fullmatch(image.strip()):
                raise PackageError(f"{where}.item.image must be a binding such as {{{{item.picture}}}}")
            _bindings(image, f"{where}.item.image", data_ids=ids, allow=row_ctx_allow)
        aid = act(aid=item.get("action"), row_of=did)
        empty = None
        if raw.get("empty") is not None:
            e = _obj(raw["empty"], f"{where}.empty", ("text", "icon"), required=("text",))
            empty = MappingProxyType({"text": text(e["text"], f"{where}.empty.text", MAX_TEXT, required=True, data_ids=ids, allow=("data", "count")),
                                      "icon": _icon(e.get("icon"), f"{where}.empty", optional=True)})
        return MappingProxyType({"type": kind, "data": did, "max": _int(raw.get("max"), f"{where}.max", 1, MAX_LIST, 20),
                                 "item": MappingProxyType({"title": it("title", required=True), "subtitle": it("subtitle"), "trailing": it("trailing", 60),
                                                           "icon": _icon(item.get("icon"), f"{where}.item", optional=True), "image": image.strip(),
                                                           "action": aid}),
                                 "empty": empty})
    if kind == "card":
        _obj(raw, where, ("type", "title", "subtitle", "children", "action"), required=("children",))
        if depth + 1 > MAX_DEPTH:
            raise PackageError(f"{where}: cards nest too deep")
        kids = raw["children"]
        if not isinstance(kids, list) or not 1 <= len(kids) <= 20:
            raise PackageError(f"{where}.children must be a list of 1-20 components")
        return MappingProxyType({"type": kind, "title": tx("title"), "subtitle": tx("subtitle"), "action": act(),
                                 "children": tuple(_component(k, ctx, depth + 1, f"{where}.children[{i}]", item_of=item_of) for i, k in enumerate(kids))})
    # form
    _obj(raw, where, ("type", "fields", "submit"), required=("fields", "submit"))
    fields = raw["fields"]
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS:
        raise PackageError(f"{where}.fields must be a list of 1-{MAX_FIELDS} fields")
    out_fields, seen = [], set()
    for i, f in enumerate(fields):
        fw = f"{where}.fields[{i}]"
        _obj(f, fw, ("id", "type", "label", "required", "placeholder", "options", "min", "max", "max_length", "default"),
             required=("id", "type", "label"))
        if not isinstance(f["id"], str) or not FIELD_ID.fullmatch(f["id"]) or f["id"] in seen:
            raise PackageError(f"{fw}.id must be a field id used once in the form")
        seen.add(f["id"])
        if f["type"] not in FIELD_TYPES:
            raise PackageError(f"{fw}.type must be one of {', '.join(FIELD_TYPES)}")
        req = f.get("required", False)
        if not isinstance(req, bool):
            raise PackageError(f"{fw}.required must be true or false")
        one: dict[str, Any] = {"id": f["id"], "type": f["type"], "label": text(f["label"], f"{fw}.label", 60, required=True, allow=()),
                               "required": req, "placeholder": text(f.get("placeholder"), f"{fw}.placeholder", 80, allow=())}
        if f["type"] == "select":
            opts = f.get("options")
            if not isinstance(opts, list) or not 1 <= len(opts) <= MAX_OPTIONS:
                raise PackageError(f"{fw}.options must be a list of 1-{MAX_OPTIONS} options")
            one["options"] = tuple(MappingProxyType({"value": _line(_obj(o, f"{fw}.options", ("value", "label"), required=("value", "label"))["value"],
                                                                    f"{fw}.options.value", 60, required=True),
                                                     "label": text(o["label"], f"{fw}.options.label", 60, required=True, allow=())}) for o in opts)
        elif "options" in f:
            raise PackageError(f"{fw}.options is only for a select field")
        if f["type"] == "number":
            for k in ("min", "max"):
                if k in f and (not isinstance(f[k], (int, float)) or isinstance(f[k], bool)):
                    raise PackageError(f"{fw}.{k} must be a number")
                if k in f:
                    one[k] = f[k]
        if f["type"] in ("text", "multiline"):
            one["max_length"] = _int(f.get("max_length"), f"{fw}.max_length", 1, 2000, 200 if f["type"] == "text" else 1000)
        if "default" in f:
            d = f["default"]
            if not isinstance(d, (str, int, float, bool)) or (isinstance(d, str) and len(d) > 200):
                raise PackageError(f"{fw}.default must be a short value")
            one["default"] = d
        out_fields.append(MappingProxyType(one))
    sub = _obj(raw["submit"], f"{where}.submit", ("label", "action"), required=("label", "action"))
    if sub["action"] not in ctx.actions:
        raise PackageError(f"{where}.submit.action must name one of this package's actions")
    if ctx.actions[sub["action"]]["type"] == "store_delete":
        raise PackageError(f"{where}.submit.action cannot be store_delete")
    return MappingProxyType({"type": "form", "fields": tuple(out_fields),
                             "submit": MappingProxyType({"label": text(sub["label"], f"{where}.submit.label", 60, required=True, allow=()),
                                                         "action": sub["action"]})})


def _icon(v: Any, where: str, *, optional: bool = False) -> str:
    if v in (None, "") and optional:
        return ""
    if v not in ICONS:
        raise PackageError(f"{where}.icon must be one of the icon names in docs/nest-ui.md (for example star, list, box)")
    return str(v)


def parse_ui(raw: Any, *, connect: Mapping[str, Any] | None = None, store: Mapping[str, Any] | None = None,
             image_hosts: Iterable[str] | None = ()) -> Mapping[str, Any]:
    """The `ui` section, checked and normalized: surfaces, data sources, actions and screens. Raises PackageError."""
    if raw is None:
        return MappingProxyType({})
    connect, store = connect or {}, store or {}
    if len(json.dumps(raw, ensure_ascii=False).encode()) > MAX_UI_BYTES:
        raise PackageError(f"ui is larger than {MAX_UI_BYTES // 1024} KiB")
    _obj(raw, "ui", ("schema", "surfaces", "data", "actions", "screens"), required=("schema", "surfaces", "screens"))
    schema = raw["schema"]
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        raise PackageError("ui.schema must be a schema version (1)")
    data_raw, actions_raw, screens_raw = raw.get("data") or {}, raw.get("actions") or {}, raw["screens"]
    if not isinstance(data_raw, dict) or len(data_raw) > MAX_DATA:
        raise PackageError(f"ui.data must be an object of at most {MAX_DATA} data sources")
    if not isinstance(actions_raw, dict) or len(actions_raw) > MAX_ACTIONS:
        raise PackageError(f"ui.actions must be an object of at most {MAX_ACTIONS} actions")
    if not isinstance(screens_raw, dict) or not 1 <= len(screens_raw) <= MAX_SCREENS:
        raise PackageError(f"ui.screens must be an object of 1-{MAX_SCREENS} screens")
    data = {_id(k, "a data source id"): _data_source(k, v, connect, store) for k, v in data_raw.items()}
    actions = {_id(k, "an action id"): _action(k, v, connect, store) for k, v in actions_raw.items()}
    ctx = _Ctx(data, actions, set(image_hosts or ()))
    screens: dict[str, Any] = {}
    for sid, s in screens_raw.items():
        _id(sid, "a screen id")
        _obj(s, f"ui.screens.{sid}", ("title", "components"), required=("components",))
        comps = s["components"]
        if not isinstance(comps, list) or not 1 <= len(comps) <= MAX_COMPONENTS:
            raise PackageError(f"ui.screens.{sid}.components must be a list of 1-{MAX_COMPONENTS} components")
        ctx.count = 0
        screens[sid] = MappingProxyType({"title": text(s.get("title"), f"ui.screens.{sid}.title", 60, data_ids=list(data), allow=("data", "count")),
                                         "components": tuple(_component(c, ctx, 1, f"ui.screens.{sid}.components[{i}]") for i, c in enumerate(comps))})
    surfaces_raw = raw["surfaces"]
    if not isinstance(surfaces_raw, list) or not 1 <= len(surfaces_raw) <= MAX_SURFACES:
        raise PackageError(f"ui.surfaces must be a list of 1-{MAX_SURFACES} surfaces")
    surfaces, per = [], {p: 0 for p in PLACES}
    seen: set[str] = set()
    for i, s in enumerate(surfaces_raw):
        w = f"ui.surfaces[{i}]"
        _obj(s, w, ("id", "place", "title", "icon", "screen"), required=("id", "place", "title", "screen"))
        sid = _id(s["id"], f"{w}.id")
        if sid in seen:
            raise PackageError(f"{w}.id {sid} is used twice")
        seen.add(sid)
        if s["place"] not in PLACES:
            raise PackageError(f"{w}.place must be one of {', '.join(PLACES)}")
        per[s["place"]] += 1
        if per[s["place"]] > PLACE_LIMITS[s["place"]]:
            raise PackageError(f"a package may add at most {PLACE_LIMITS[s['place']]} surface(s) of place {s['place']}")
        if s["screen"] not in screens:
            raise PackageError(f"{w}.screen must name one of the screens")
        icon = _icon(s.get("icon"), w, optional=s["place"] != "tab")
        surfaces.append(MappingProxyType({"id": sid, "place": s["place"], "title": text(s["title"], f"{w}.title", 24, required=True, allow=()),
                                          "icon": icon, "screen": s["screen"]}))
    missing = ctx.uses_images - ctx.image_hosts
    if missing and image_hosts is not None:
        raise PackageError(f"pictures come from {', '.join(sorted(missing))}: declare it in permissions.image_hosts")
    return MappingProxyType({"schema": schema, "surfaces": tuple(surfaces), "data": MappingProxyType(data),
                             "actions": MappingProxyType(actions), "screens": MappingProxyType(screens)})


# ---------------------------------------------------------------------------------------------------------------- permissions
def parse_permissions(raw: Any) -> Mapping[str, Any] | None:
    """The declared `permissions` section, checked (None when the manifest has none)."""
    if raw is None:
        return None
    _obj(raw, "permissions", PERMISSION_FIELDS)
    scopes = raw.get("scopes")
    if scopes is not None and (not isinstance(scopes, list) or len(scopes) > 30 or not all(isinstance(s, str) and SCOPE.fullmatch(s) for s in scopes)):
        raise PackageError("permissions.scopes must be a list of up to 30 OAuth scopes")
    tools = raw.get("mcp_tools")
    if tools is not None and (not isinstance(tools, list) or len(tools) > 30 or not all(isinstance(s, str) and MCP_TOOL.fullmatch(s) for s in tools)):
        raise PackageError("permissions.mcp_tools must be a list of up to 30 tool names")
    store = raw.get("store", False)
    if not isinstance(store, bool):
        raise PackageError("permissions.store must be true or false")
    return MappingProxyType({
        "hosts": _hosts(raw.get("hosts"), "permissions.hosts"), "image_hosts": _hosts(raw.get("image_hosts"), "permissions.image_hosts"),
        "reads": _words(raw.get("reads"), "permissions.reads", READS), "scopes": tuple(sorted(set(scopes or ()))),
        "mcp_tools": tuple(sorted(set(tools or ()))), "ui": _words(raw.get("ui"), "permissions.ui", PLACES),
        "actions": _words(raw.get("actions"), "permissions.actions", ACTION_KINDS), "store": store})


def _op_value(v: Any, what: str, names: tuple[str, ...], depth: int = 0) -> Any:
    """An argument or body template: JSON values whose strings may hold {{binding}} of `names`."""
    if depth > 4:
        raise PackageError(f"{what} is nested too deeply")
    if isinstance(v, str):
        for m in BINDING.finditer(v):
            if m.group(1) not in names:
                raise PackageError(f"{what}: {{{{{m.group(1)}}}}} is not one of {', '.join(names)}")
        return v[:500]
    if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, list):
        return tuple(_op_value(x, what, names, depth + 1) for x in v[:20])
    if isinstance(v, dict):
        if len(v) > 20 or not all(isinstance(k, str) and 0 < len(k) <= 64 for k in v):
            raise PackageError(f"{what} must have up to 20 named entries")
        return MappingProxyType({k: _op_value(x, what, names, depth + 1) for k, x in v.items()})
    raise PackageError(f"{what} holds a value that is not JSON")


def parse_provides(raw: Any, *, kind: str, connect: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `provides` section (see the module's docstring), checked: every required operation and field, bindings, tools or requests
    the connector can make. {} when absent."""
    if raw is None:
        return MappingProxyType({})
    if kind != "connector":
        raise PackageError("only a connector can provide mail or a calendar")
    _obj(raw, "provides", PROVIDES)
    ctype = connect.get("type")
    can_tool = ctype == "mcp" or (ctype == "key" and bool(connect.get("mcp_url")))
    can_request = ctype in ("key", "oauth") and bool(connect.get("base_url"))
    out: dict[str, Any] = {}
    for cap, ops in raw.items():
        _obj(ops, f"provides.{cap}", PROVIDES[cap])
        missing = [o for o in REQUIRED_OPS[cap] if o not in ops]
        if missing:
            raise PackageError(f"provides.{cap} needs the operations {', '.join(missing)} (required: {', '.join(REQUIRED_OPS[cap])})")
        got: dict[str, Any] = {}
        for op, spec in ops.items():
            where = f"provides.{cap}.{op}"
            _obj(spec, where, ("tool", "arguments", "request", "select", "fields"))
            names = PROVIDES[cap][op]
            one: dict[str, Any] = {}
            if ("tool" in spec) == ("request" in spec):
                raise PackageError(f"{where} needs either a tool or a request")
            if "tool" in spec:
                if not can_tool:
                    raise PackageError(f"{where}: a tool needs an MCP connection (connect.type mcp, or a key connector with mcp_url)")
                if not isinstance(spec["tool"], str) or not MCP_TOOL.fullmatch(spec["tool"]):
                    raise PackageError(f"{where}.tool must be a tool name")
                args = spec.get("arguments") or {}
                if not isinstance(args, dict):
                    raise PackageError(f"{where}.arguments must be an object")
                one = {"tool": spec["tool"], "arguments": _op_value(args, f"{where}.arguments", names)}
            else:
                if not can_request:
                    raise PackageError(f"{where}: a request needs a key or OAuth connector with a base_url")
                req = _obj(spec["request"], f"{where}.request", ("method", "path", "body"), required=("method", "path"))
                method = str(req["method"]).upper()
                path = req["path"]
                if method not in HTTP_METHODS or not isinstance(path, str) or not path.startswith("/") or len(path) > 300 or "//" in path:
                    raise PackageError(f"{where}.request needs a method ({', '.join(HTTP_METHODS)}) and a path that starts with /")
                one = {"request": MappingProxyType({"method": method, "path": _op_value(path, f"{where}.request.path", names),
                                                    "body": _op_value(req.get("body"), f"{where}.request.body", names)})}
            if spec.get("select") not in (None, ""):
                if not isinstance(spec["select"], str) or not PATH.fullmatch(spec["select"]):
                    raise PackageError(f"{where}.select must be a dotted path")
                one["select"] = spec["select"]
            fields = spec.get("fields") or {}
            if op in OP_FIELDS:
                _obj(fields, f"{where}.fields", OP_FIELDS[op])
                need = [f for f in REQUIRED_FIELDS[op] if f not in fields]
                if need:
                    raise PackageError(f"{where}.fields needs {', '.join(need)}")
                for f, path_ in fields.items():
                    if not isinstance(path_, str) or not PATH.fullmatch(path_):
                        raise PackageError(f"{where}.fields.{f} must be a dotted path in a row")
                one["fields"] = MappingProxyType(dict(fields))
            elif fields:
                raise PackageError(f"{where} has no fields to map")
            got[op] = MappingProxyType(one)
        out[cap] = MappingProxyType(got)
    return MappingProxyType(out)


SITUATION_FIELDS = ("request", "tool", "arguments", "mode", "select", "fields", "id", "lines", "max_rows", "max_chars", "every_minutes", "wakes")


def parse_situation(raw: Any, *, connect: Mapping[str, Any]) -> Any:
    """`connector.situation`: the one read-only call that shows the service's state (docs/nest.md, "Situation read"). Checked, then compiled to
    a situation_reads.SituationRead: a request is a GET through the connection (connector_get), a tool is an MCP tool of the connection
    (mcp_call_tool). None when absent. These two compile rules are the only place a package names Kelo's own tools."""
    from .situation_reads import parse_read
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PackageError("connector.situation must be an object")
    bad = set(raw) - set(SITUATION_FIELDS)
    if bad:
        raise PackageError(f"connector.situation: unknown fields {', '.join(sorted(bad))}")
    if ("request" in raw) == ("tool" in raw):
        raise PackageError("connector.situation needs either a request or a tool")
    ctype = connect.get("type")
    if "request" in raw:
        if "arguments" in raw:
            raise PackageError("connector.situation: arguments belong to a tool, not to a request")
        req = raw["request"]
        if not isinstance(req, dict) or set(req) != {"method", "path"} or str(req.get("method")).upper() != "GET":
            raise PackageError("connector.situation.request needs method GET and a path")
        path = req["path"]
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 300 or "//" in path:
            raise PackageError("connector.situation.request.path must start with /")
        if not (ctype in ("key", "oauth") and connect.get("base_url")):
            raise PackageError("connector.situation.request needs a key or OAuth connector with a base_url")
        args: dict[str, Any] = {"name": "{{connection}}", "url": str(connect["base_url"]).rstrip("/") + path}
        tool = "connector_get"
    else:
        name = raw["tool"]
        if not isinstance(name, str) or not MCP_TOOL.fullmatch(name):
            raise PackageError("connector.situation.tool must be a tool name")
        if not (ctype == "mcp" or (ctype == "key" and bool(connect.get("mcp_url")))):
            raise PackageError("connector.situation.tool needs an MCP connection (connect.type mcp, or a key connector with mcp_url)")
        arguments = raw.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise PackageError("connector.situation.arguments must be an object")
        args = {"name": "{{connection}}", "tool": name, "arguments": arguments}
        tool = "mcp_call_tool"
    spec = {"tool": tool, "arguments": args, **{k: raw[k] for k in SITUATION_FIELDS if k in raw and k not in ("request", "tool", "arguments")}}
    try:
        return parse_read(spec, where="connector.situation")
    except ValueError as e:
        raise PackageError(str(e)) from None


def implied_permissions(connect: Mapping[str, Any], ui: Mapping[str, Any], store: Mapping[str, Any],
                        provides: Mapping[str, Any] | None = None, situation: Any = None) -> dict[str, Any]:
    """What the manifest really uses: hosts from the connector and the links, image hosts from fixed pictures, MCP tools and reads from
    data sources, surfaces, kinds of actions, whether it keeps entries."""
    hosts: set[str] = set()
    kind = connect.get("type")
    if kind in ("mcp", "key", "oauth"):
        hosts |= set(connect.get("hosts") or ())
    if kind == "oauth":
        for k in ("authorize_url", "token_url"):
            if connect.get(k):
                hosts.add((urlsplit(connect[k]).hostname or "").lower())
    if kind == "browser" and connect.get("url"):
        hosts.add((urlsplit(connect["url"]).hostname or "").lower())
    tools: set[str] = set()
    reads: set[str] = set()
    actions: set[str] = set()
    for d in (ui.get("data") or {}).values():
        if d["source"] == "mcp":
            tools.add(d["tool"])
        if d["source"] == "kelo":
            reads.add(FEEDS[d["feed"]])
    for a in (ui.get("actions") or {}).values():
        t = a["type"]
        actions.add({"store_add": "store", "store_delete": "store", "refresh": ""}.get(t, t))
        if t == "open_url":
            hosts.add((urlsplit(a["url"]).hostname or "").lower())
    actions.discard("")
    for cap, ops in (provides or {}).items():   # a mailbox or a calendar Kelo reads: that read, and the tools it calls
        reads.add(cap)
        tools |= {spec["tool"] for spec in ops.values() if spec.get("tool")}
    if situation is not None and situation.tool == "mcp_call_tool":   # the situation read's MCP tool: shown on the install card too
        tools.add(str(situation.arguments.get("tool")))
    images: set[str] = set()
    for s in (ui.get("screens") or {}).values():
        _walk_images(s["components"], images)
    return {"hosts": tuple(sorted(h for h in hosts if h)), "image_hosts": tuple(sorted(images)), "reads": tuple(r for r in READS if r in reads),
            "scopes": tuple(sorted(connect.get("scopes") or ())) if kind == "oauth" else (), "mcp_tools": tuple(sorted(tools)),
            "ui": tuple(p for p in PLACES if any(s["place"] == p for s in ui.get("surfaces") or ())),
            "actions": tuple(a for a in ACTION_KINDS if a in actions), "store": bool(store.get("collections"))}


def _walk_images(comps: Iterable[Mapping[str, Any]], out: set[str]) -> None:
    for c in comps:
        if c["type"] == "image" and not BINDING.fullmatch(c["src"]):
            out.add((urlsplit(c["src"]).hostname or "").lower())
        if c["type"] == "card":
            _walk_images(c["children"], out)


def effective_permissions(declared: Mapping[str, Any] | None, implied: Mapping[str, Any]) -> Mapping[str, Any]:
    """The permissions a package gets. With a `permissions` section, everything the manifest uses must be declared in it (so the summary
    a publisher shows is never smaller than what the package does); the result is what is declared. Without one, what it uses.
    Raises PackageError naming what is missing."""
    if declared is None:
        return MappingProxyType({k: (tuple(v) if k != "store" else bool(v)) for k, v in implied.items()})
    for k in LIST_PERMISSIONS:
        missing = sorted(set(implied.get(k) or ()) - set(declared.get(k) or ()))
        if missing:
            raise PackageError(f"declare {', '.join(missing)} in permissions.{k} (the manifest uses it)")
    if implied.get("store") and not declared.get("store"):
        raise PackageError("declare \"store\": true in permissions (the manifest keeps entries)")
    return MappingProxyType({k: declared[k] for k in PERMISSION_FIELDS})


def plain(perms: Mapping[str, Any] | None) -> dict[str, Any]:
    """Permissions as plain JSON (lists, booleans) in the fixed order."""
    perms = perms or {}
    return {k: (bool(perms.get(k)) if k == "store" else list(perms.get(k) or ())) for k in PERMISSION_FIELDS}


def permissions_hash(perms: Mapping[str, Any] | None) -> str:
    return hashlib.sha256(b"kelo-nest-permissions-1\n" + json.dumps(plain(perms), sort_keys=True).encode()).hexdigest()


def diff(old: Mapping[str, Any] | None, new: Mapping[str, Any] | None) -> dict[str, Any]:
    """What an update changes: {"widened": [{"what", "added"}], "narrowed": [{"what", "removed"}]}. A widened permission needs the
    owner's approval again."""
    a, b = plain(old), plain(new)
    widened, narrowed = [], []
    for k in LIST_PERMISSIONS:
        added = [x for x in b[k] if x not in a[k]]
        removed = [x for x in a[k] if x not in b[k]]
        if added:
            widened.append({"what": k, "added": added})
        if removed:
            narrowed.append({"what": k, "removed": removed})
    if b["store"] and not a["store"]:
        widened.append({"what": "store", "added": [True]})
    if a["store"] and not b["store"]:
        narrowed.append({"what": "store", "removed": [True]})
    return {"widened": widened, "narrowed": narrowed}


# ---------------------------------------------------------------------------------------------------------------- plain words
READ_WORDS = {
    "mail": N_("Reads your mail"), "calendar": N_("Reads your calendar"), "contacts": N_("Reads your contacts"),
    "messages": N_("Reads your text messages"), "files": N_("Reads your files in Kelo"), "photos": N_("Reads your photos"),
    "location": N_("Knows your location"), "health": N_("Reads your health data"), "wallet": N_("Uses your wallet (each payment is still asked first)"),
    "tasks": N_("Reads what Kelo did for you (your task list)"), "memory": N_("Reads what Kelo remembers about you"),
}
PLACE_WORDS = {"tab": N_("Adds a tab to the bottom bar"), "my_section": N_("Adds a section to My page"),
               "home_card": N_("Adds a card to Home"), "chat_card": N_("Adds cards to the chat")}
ACTION_WORDS = {"store": N_("Its buttons save and remove its own entries"),
                "agent": N_("Its buttons ask Kelo to do something (Kelo's usual approvals still apply)"),
                "connector": N_("Its buttons send changes to its service (you confirm each one)"),
                "mcp": N_("Its buttons run tools of its service (you confirm each one)"),
                "open_url": N_("Its buttons open web pages in your browser")}
CONNECT_WORDS = {"mcp": N_("Signs in to its service on the service's own page"),
                 "oauth": N_("Signs in to your account on its service"),
                 "key": N_("Uses an API key you make on its service"),
                 "browser": N_("Kelo opens its site in its own browser; a login you save stays in the vault")}


def describe(perms: Mapping[str, Any] | None, *, kind: str = "", connect_type: str = "", lang: str | None = None) -> list[dict[str, str]]:
    """The permissions in plain words, in the reader's language: [{"key", "text", "level": "info" | "caution"}]."""
    p = plain(perms)
    out: list[dict[str, str]] = []

    def add(key: str, msg: str, level: str = "info", **fields: Any) -> None:
        out.append({"key": key, "text": i18n.t(msg, lang=lang, **fields), "level": level})

    if kind == "skill":
        add("skill", N_("Adds a guide Kelo reads when it fits your request"))
    if connect_type in CONNECT_WORDS:
        add("connect", CONNECT_WORDS[connect_type], "caution")
    if p["hosts"]:
        add("hosts", N_("Connects only to {hosts}"), "caution", hosts=", ".join(p["hosts"]))
    if p["scopes"]:
        add("scopes", N_("Asks for these permissions when you sign in: {scopes}"), "caution", scopes=", ".join(p["scopes"]))
    for r in p["reads"]:
        add(f"reads.{r}", READ_WORDS[r], "caution")
    if p["mcp_tools"]:
        add("mcp_tools", N_("Shows data from these tools of its service: {tools}"), tools=", ".join(p["mcp_tools"]))
    for s in p["ui"]:
        add(f"ui.{s}", PLACE_WORDS[s])
    if p["image_hosts"]:
        add("image_hosts", N_("Shows pictures from {hosts} (fetched by your server, not your phone)"), hosts=", ".join(p["image_hosts"]))
    for a in p["actions"]:
        add(f"actions.{a}", ACTION_WORDS[a], "caution" if a in ("connector", "mcp") else "info")
    if p["store"]:
        add("store", N_("Keeps its own entries on your server"))
    add("code", N_("Runs no code on your server or your phone"))
    return out


__all__ = ["ACTION_KINDS", "COMPONENTS", "FEEDS", "ICONS", "PERMISSION_FIELDS", "PLACES", "PLACE_LIMITS", "READS", "REVOCABLE", "UI_SCHEMA",
           "describe", "diff", "effective_permissions", "implied_permissions", "parse_permissions", "parse_store", "parse_ui", "permissions_hash",
           "pick_text", "plain", "text"]
