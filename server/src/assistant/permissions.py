"""What the owner lets Kelo do with each connector, set in the app (Connectors -> <name> -> What Kelo may do).

A connector can have up to four kinds of action: read, change, send, delete (effects.py says which kinds a connector has and what each
of its tools does). For each kind, and for single actions (a tool, or an MCP server's own tool), the owner picks a mode:
  allow  no approval card (payments, purchases, card and PIN steps, sign-ups and account steps keep their own checks always, and so
         does anything the privacy guard flags: privacy_guard.py)
  ask    an approval card every time, even where a standing rule or a task's permission would have allowed the step
  off    the step is not run; the model is told the owner turned it off and not to try another way (a connector whose every kind of
         a tool is off does not even show that tool to the model)
Nothing set = today's behaviour: reads run, and a change, send or delete asks where the tool asks today and runs where it does not
(`default`, computed from the tools themselves, never hard-coded). An action's mode beats its kind's.

Stored in the meta table as `permissions:<connector id>` = {"read": mode, "change": mode, "send": mode, "delete": mode,
"actions": {"<action id>": mode}} (only what was set).

Gate(permissions, guard) is what agent/loop.py asks before a tool call runs, for chat, schedules and background jobs alike
(`await gate.check(tool, args)` -> Verdict; `gate.hides(tool_name)`; `gate.limits_line()` for "This Turn").

PERMISSIONS API (api/connectors.py; under the app's bearer token):
GET    /api/connectors/{id}/permissions   {"connector": id, "name": str, "kinds": [{"kind": read|change|send|delete, "mode", "default",
                                            "actions": [{"id", "label", "mode", "default"}]}]}  only the kinds this connector has (a
                                            read-only CalDAV calendar: just "read"). Labels are short and translated. "mode" is what
                                            applies now (what was set, else the default). The ids: Gmail, Outlook and IMAP mailboxes, Google
                                            Calendar, "phone" (the shared phone), key services, apps on the owner's network, MCP services (their
                                            own tools appear as Kelo learns them), Nest packages that keep entries.
PUT    /api/connectors/{id}/permissions   body as GET, partial: {"read"?: mode, "change"?: ..., "actions"?: {"<id>": mode|null}}; a null
                                            clears that setting back to the default; -> the same as GET; 404 unknown connector; 422 for a
                                            kind the connector does not have, an unknown action id or a mode other than allow|ask|off
DELETE /api/connectors/{id}/permissions   every setting of this connector back to the default -> the same as GET
GET    /api/connectors rows gain "limited": true when anything set differs from the default (off, or ask where it was allow, or an
                                            "ask" that overrides a standing rule)
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import effects, forbidden
from .effects import FAMILIES, KINDS, ActionInfo, Effect, all_actions
from .i18n import N_, t
from .privacy_guard import URL_TOOLS

log = logging.getLogger("uvicorn.error")

MODES = ("allow", "ask", "off")
PREFIX = "permissions:"
MCP_PREFIX = "permissions-mcp:"
OFF_MESSAGE = ("The owner turned this off for {name} in the app (Connectors → {name} → What Kelo may do). Tell them if it matters; "
               "do not try another way.")
LIMIT_WORDS = {"read": ("no reading", "reading asks"), "change": ("no changes", "changes ask"), "send": ("no sending", "sending asks"),
               "delete": ("no deleting", "deleting asks")}
LIMITS_MAX = 200
PHONE_NAME = N_("Phone")


class PermissionsError(ValueError):
    """A setting the owner's app sent that makes no sense (unknown kind, action or mode)."""


@dataclass(frozen=True)
class Info:
    """What the settings need to know about one connector."""
    id: str
    name: str
    family: str | None            # effects.FAMILIES key, None = a connector without settings (the agent's own browser)
    sends: bool = False


@dataclass
class Verdict:
    action: str = "pass"          # pass (as today) | off | ask (card, no rule or grant skips it) | allow (no card)
    message: str = ""             # off: what the model is told
    card: str = ""                # ask: a line for the approval card (the privacy guard says what goes where)
    connector: str = ""
    kind: str = ""


def family_of(cid: str, row: dict[str, Any] | None = None, *, package: Any = None) -> str | None:
    """The family of a connector (effects.FAMILIES) from its id and its row of the connect table (connect_ways.ways)."""
    if cid == "phone":
        return "phone"
    if cid in ("gmail", "imap_email", "outlook_mail"):
        return "mail"
    if cid == "google_calendar":
        return "calendar"
    if cid in ("outlook_calendar", "caldav_calendar"):
        return "calendar_read"
    way = (row or {}).get("connect")
    if package is not None and getattr(package, "provides", None):
        prov = package.provides
        if "mail" in prov:
            return "mail"
        if "calendar" in prov:
            return "calendar_read"
    if way == "mcp":
        return "mcp"
    if way in ("key", "device"):
        return "key"
    if package is not None and getattr(package, "store", None) and not way:
        return "package"
    return None


def connected_by_family(table: dict[str, dict[str, Any]], accounts: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Family -> the ids of the connectors connected now: from the connect table (connect_ways.ways) and the connected mailboxes."""
    out: dict[str, list[str]] = {"phone": ["phone"]}
    boxes = {effects.mail_connector(str(a.get("name") or "")) for a in accounts}
    out["mail"] = sorted(c for c in boxes if c)
    for cid, row in table.items():
        if not row.get("connected"):
            continue
        fam = family_of(cid, row)
        if fam in ("calendar", "key", "mcp"):
            out.setdefault(fam, []).append(cid)
    return out


class Permissions:
    def __init__(self, store: Any, *, tools: Callable[[], list[Any]] | None = None, names: Callable[[str], str] | None = None,
                 accounts: Callable[[], list[dict[str, Any]]] | None = None, aliases: Callable[[str], str | None] | None = None,
                 connected: Callable[[], dict[str, list[str]]] | None = None, clock: Callable[[], float] = time.time):
        self.store = store
        self.tools = tools or (lambda: [])                # the run's tools: what asks today comes from them
        self.names = names or (lambda cid: cid)           # connector id -> the name shown to the owner and the model
        self.accounts = accounts                          # the connected mailboxes (an empty `account` of an email tool)
        self.aliases = aliases                            # vault item -> catalog id (installed packages)
        self.connected = connected or (lambda: {})        # family -> the ids of the connectors connected now (hides a tool)
        self.clock = clock
        self._cfg: dict[str, dict[str, Any]] | None = None
        self.version = 0                                  # counts changes of the settings (a running turn re-reads its tool list)
        effects.on_mcp_tools = self._mcp_listed
        for name_, cached in self._persisted_mcp().items():
            effects.remember_mcp_tools(name_, [{"name": n, "annotations": a} for n, a in cached.items()], persist=False)

    # ------------------------------------------------------------------ storage
    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cfg is None:
            out: dict[str, dict[str, Any]] = {}
            for key, raw in self._rows(PREFIX).items():
                try:
                    v = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(v, dict):
                    out[key] = self._clean(v)
            self._cfg = out
        return self._cfg

    def _rows(self, prefix: str) -> dict[str, str]:
        try:
            return self.store.meta_prefix(prefix)
        except AttributeError:
            return {}

    @staticmethod
    def _clean(v: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {k: v[k] for k in KINDS if v.get(k) in MODES}
        acts = v.get("actions")
        if isinstance(acts, dict):
            out["actions"] = {str(k): m for k, m in acts.items() if m in MODES}
        return {k: x for k, x in out.items() if x}

    def _save(self, cid: str, cfg: dict[str, Any]) -> None:
        cfg = self._clean(cfg)
        cache = self._load()
        self.version += 1
        if cfg:
            cache[cid] = cfg
        else:
            cache.pop(cid, None)
        self.store.set_meta(PREFIX + cid, json.dumps(cfg, ensure_ascii=False) if cfg else "")

    def explicit_config(self, cid: str) -> dict[str, Any]:
        return json.loads(json.dumps(self._load().get(cid, {})))

    # ------------------------------------------------------------------ MCP tools as the servers listed them
    def _persisted_mcp(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, raw in self._rows(MCP_PREFIX).items():
            try:
                v = json.loads(raw)
            except ValueError:
                continue
            if isinstance(v, dict):
                out[key] = v
        return out

    def _mcp_listed(self, name: str, rows: Any) -> None:
        try:
            self.store.set_meta(MCP_PREFIX + (name or "").strip().casefold(),
                                json.dumps(effects.known_mcp_tools(name), ensure_ascii=False))
        except Exception:  # noqa: BLE001 - remembering the list never breaks the call that listed it
            log.exception("could not keep the tools an MCP server listed")

    # ------------------------------------------------------------------ what asks today
    def _tool(self, name: str) -> Any:
        return next((x for x in self.tools() if x.name == name), None)

    def ask_today(self, action: str) -> bool:
        """Whether one action asks for approval today (its tool is flagged for approval and nothing but the owner's rules lifts it)."""
        name = effects.ACTION_TOOL.get(action, action)
        tool = self._tool(name)
        if tool is None:
            tool = self._tool("mcp_call_tool") if action not in effects.TOOL_FAMILY and action not in effects.ACTION_TOOL else None
        if tool is None:
            return False
        if not tool.requires_approval:
            return False
        if name == "email_update" and action != "email_trash":
            return False   # moving to the trash is the only mail change that asks (mail_tools.py)
        return True

    def default(self, info: Info, kind: str, action: str | None = None) -> str:
        if action is not None:
            return "ask" if self.ask_today(action) else "allow"
        acts = [a for a in self.actions(info) if a.kind == kind]
        if info.family == "mcp" and kind != "read":
            return "ask" if self.ask_today("mcp_call_tool") else "allow"
        if info.family == "mcp":
            return "ask" if self.ask_today("mcp_call_tool") else "allow"
        return "ask" if any(self.ask_today(a.id) for a in acts) else "allow"

    # ------------------------------------------------------------------ the connector's kinds and actions
    def actions(self, info: Info) -> list[ActionInfo]:
        if info.family is None:
            return []
        out = list(all_actions(info.family, sends=info.sends))
        if info.family == "mcp":
            known = effects.known_mcp_tools(info.id)
            for alias in {info.id, *self._item_names(info.id)}:
                known.update(effects.known_mcp_tools(alias))
            out += [ActionInfo(n, _pretty(n), effects.mcp_kind(n, a)) for n, a in sorted(known.items())]
        return out

    def _item_names(self, cid: str) -> set[str]:
        from .effects import _item_ids
        return {item for item, c in _item_ids().items() if c == cid}

    def kinds(self, info: Info) -> list[str]:
        have = {a.kind for a in self.actions(info)}
        if info.family == "mcp":
            have = set(KINDS)   # any tool a server has may be of any kind
        return [k for k in KINDS if k in have]

    # ------------------------------------------------------------------ reading and writing
    def describe(self, info: Info) -> dict[str, Any]:
        cfg = self._load().get(info.id, {})
        acts = self.actions(info)
        out = []
        for kind in self.kinds(info):
            rows = [{"id": a.id, "label": t(a.label), "mode": cfg.get("actions", {}).get(a.id) or cfg.get(kind) or self.default(info, kind, a.id),
                     "default": self.default(info, kind, a.id)}
                    for a in acts if a.kind == kind]
            out.append({"kind": kind, "mode": cfg.get(kind) or self.default(info, kind), "default": self.default(info, kind), "actions": rows})
        return {"connector": info.id, "name": info.name, "kinds": out}

    def put(self, info: Info, body: dict[str, Any]) -> None:
        """A (partial) change: {"read"?: mode|None, ..., "actions"?: {id: mode|None}}. PermissionsError for anything unknown."""
        if info.family is None:
            raise PermissionsError("this connector has no settings")
        kinds, acts = self.kinds(info), {a.id for a in self.actions(info)}
        cfg = self.explicit_config(info.id)
        for key, value in body.items():
            if key in KINDS:
                if key not in kinds:
                    raise PermissionsError(f"{info.name} has no {key} actions")
                _set(cfg, key, value)
            elif key == "actions":
                if not isinstance(value, dict):
                    raise PermissionsError("actions must be an object of {action id: mode}")
                for aid, mode in value.items():
                    if aid not in acts:
                        raise PermissionsError(f"unknown action {aid!r}")
                    _set(cfg.setdefault("actions", {}), aid, mode)
            elif key not in ("connector", "name"):
                raise PermissionsError(f"unknown field {key!r}")
        self._save(info.id, cfg)

    def reset(self, cid: str) -> None:
        self._save(cid, {})

    def limited(self, cid: str, info: Info | None = None) -> bool:
        """Anything set that differs from today's behaviour: off, ask where it was allow, or an "ask" that now overrides a standing rule."""
        cfg = self._load().get(cid)
        if not cfg:
            return False
        for kind in KINDS:
            if cfg.get(kind) in ("off", "ask") or (cfg.get(kind) == "allow" and info is not None and self.default(info, kind) != "allow"):
                return True
        return any(m in ("off", "ask") or (m == "allow" and info is not None and self.default(info, "", a) != "allow")
                   for a, m in (cfg.get("actions") or {}).items())

    # ------------------------------------------------------------------ the decision for one call
    def mode_of(self, cid: str, kind: str, action: str) -> str | None:
        """What the owner set for this action (its own setting first, then its kind's); None = nothing set (today's behaviour)."""
        cfg = self._load().get(cid)
        if not cfg:
            return None
        return (cfg.get("actions") or {}).get(action) or cfg.get(kind)

    def effect(self, tool: Any, args: dict[str, Any]) -> Effect | None:
        return effects.effect_of(tool, args, accounts=self.accounts, aliases=self.aliases)

    def hides(self, tool_name: str) -> bool:
        """A tool is not offered to the model when, for every connector it could act on, every kind of its calls is off."""
        fam = effects.TOOL_FAMILY.get(tool_name)
        if fam is None or not self._load():
            return False
        family, kinds = fam
        targets = self.connected().get(family) or []
        if not targets:
            return False
        acts = [a for a in FAMILIES[family] if effects.ACTION_TOOL.get(a.id, a.id) == tool_name]
        for cid in targets:
            if family == "mcp" or not acts:
                if any(self.mode_of(cid, k, tool_name) != "off" for k in kinds):
                    return False
            elif any(self.mode_of(cid, a.kind, a.id) != "off" for a in acts):
                return False
        return True

    def verdict(self, tool: Any, args: dict[str, Any]) -> Verdict:
        e = self.effect(tool, args)
        if e is None or not e.connector:
            return Verdict()
        mode = self.mode_of(e.connector, e.kind, e.action)
        v = Verdict(connector=e.connector, kind=e.kind)
        if mode == "off":
            return Verdict("off", OFF_MESSAGE.format(name=self.names(e.connector)), connector=e.connector, kind=e.kind)
        if mode == "ask":
            v.action = "ask"
        elif mode == "allow" and not effects.keeps_own_checks(tool, args):
            v.action = "allow"
        return v

    def limits_line(self) -> str:
        """One line for the model's "This Turn": what the owner limited ("Gmail: no sending; Calendar: deleting asks."), "" when nothing is."""
        parts: list[str] = []
        for cid, cfg in sorted(self._load().items()):
            bits = []
            for kind in KINDS:
                m = cfg.get(kind)
                if m in ("off", "ask"):
                    bits.append(LIMIT_WORDS[kind][0 if m == "off" else 1])
            for aid, m in sorted((cfg.get("actions") or {}).items()):
                if m in ("off", "ask"):
                    bits.append(f"{_pretty(aid).lower()} {'off' if m == 'off' else 'asks'}")
            if bits:
                parts.append(f"{self.names(cid)}: {', '.join(bits)}")
        if not parts:
            return ""
        line = "Owner's limits: " + "; ".join(parts) + "."
        while len(line) > LIMITS_MAX and len(parts) > 1:
            parts.pop()
            line = "Owner's limits: " + "; ".join(parts) + "; more."
        return line if len(line) <= LIMITS_MAX else line[:LIMITS_MAX - 1].rstrip(" ,;:") + "…"


def flat_body(body: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """A PUT body in the flat form {"read": mode, ..., "actions": {id: mode}}. The GET shape ({"kinds": [{"kind", "mode", "actions":
    [{"id", "mode"}]}]}) is taken too: there only what differs from what applies now counts, so putting an unchanged GET back changes
    nothing."""
    if not isinstance(body, dict):
        raise PermissionsError("the body must be an object")
    flat = {k: v for k, v in body.items() if k != "kinds"}
    kinds = body.get("kinds")
    if kinds is None:
        return flat
    if not isinstance(kinds, list):
        raise PermissionsError("kinds must be a list")
    now = {k["kind"]: k for k in current.get("kinds", [])}
    for row in kinds:
        if not isinstance(row, dict) or row.get("kind") not in KINDS:
            raise PermissionsError(f"unknown kind {row.get('kind') if isinstance(row, dict) else row!r}")
        have = now.get(row["kind"])
        if have is None:
            raise PermissionsError(f"{current.get('name', 'this connector')} has no {row['kind']} actions")
        if "mode" in row and (row["mode"] is None or row["mode"] != have["mode"]):
            flat[row["kind"]] = row["mode"]
        acts = row.get("actions") or []
        if not isinstance(acts, list):
            raise PermissionsError("actions must be a list")
        was = {a["id"]: a["mode"] for a in have["actions"]}
        for a in acts:
            if not isinstance(a, dict) or not isinstance(a.get("id"), str):
                raise PermissionsError("each action needs an id")
            if "mode" in a and (a["mode"] is None or a["id"] not in was or a["mode"] != was[a["id"]]):
                flat.setdefault("actions", {})[a["id"]] = a["mode"]
    return flat


def _set(into: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        into.pop(key, None)
    elif value in MODES:
        into[key] = value
    else:
        raise PermissionsError(f"mode must be allow, ask or off (not {value!r})")


def _pretty(tool: str) -> str:
    """An MCP tool's own name as a short label: send_message -> Send message."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(tool)).replace("_", " ").replace("-", " ").strip()
    return (words[:1].upper() + words[1:])[:60] or str(tool)


APPLY_COMMITS = frozenset({"apply", "form", "personal"})   # schedule_grants.APPLY_STEPS


class Gate:
    """What agent/loop.py asks before a tool call: the owner's per-connector limits (Permissions), the privacy guard, and the rule for
    steps after outside text (outside_rule.py)."""

    def __init__(self, permissions: Permissions, guard: Any = None, *, browser_step: Any = None, apply_covers: Any = None,
                 outside: Any = None, on_stopped: Any = None, once: Any = None):
        """`guard`: privacy_guard.PrivacyGuard. `browser_step(tool, args)` (async): (commit kind, site) of a browser click or typing
        step, as commits.py judges it. `apply_covers(site)`: a task's "apply" permission covers this site's form (schedule_grants).
        `outside`: outside_rule.OutsideReview (a send, change or delete after outside text asks unless the owner named the target).
        `on_stopped()`: told when the guard or that rule turned a step into a card or refused it (injection_watch.step_stopped).
        `once`: done_once.DoneOnce; its tool action_result is offered only while some step's result is unknown."""
        self.once = once
        self.permissions, self.guard = permissions, guard
        self.browser_step, self.apply_covers = browser_step, apply_covers
        self.outside, self.on_stopped = outside, on_stopped

    async def check(self, tool: Any, args: dict[str, Any]) -> Verdict:
        """The owner's limits and the privacy guard, plus forbidden.py: a call Kelo never makes is not run (whoever asks, even where the
        owner allowed the connector); one it always asks about is a card no setting, rule or task permission skips."""
        hit = forbidden.classify(tool, args)
        if hit is not None and hit.rule.mode == "never":
            return Verdict("off", hit.refusal, connector=hit.preset)
        v = await self._check(tool, args)
        if hit is None or v.action == "off":   # (what the owner turned off stays off)
            return v
        return Verdict("ask", card=f"{hit.card}\n{v.card}".strip() if v.card else hit.card, connector=v.connector or hit.preset, kind=v.kind)

    async def _check(self, tool: Any, args: dict[str, Any]) -> Verdict:
        name = str(getattr(tool, "name", tool))
        e = self.permissions.effect(tool, args)
        v = self.permissions.verdict(tool, args) if e is not None else Verdict()
        if v.action == "off":
            return v
        commit, site = await self._browser_judgement(name, args) if (self.guard or self.outside) else ("", "")
        hit = self._guard_hit(tool, name, args, e, commit, site)
        if hit is not None and hit.leak:   # Kelo's private marker on its way out: not run, whatever is allowed
            return Verdict("off", hit.line, connector=v.connector, kind=v.kind)
        line = self.outside.line(e, name, args, commit=commit, site=site) if self.outside is not None else None
        if hit is None and line is None:
            return v
        if self.on_stopped is not None:
            self.on_stopped()
        card = "\n".join(x for x in (hit.line if hit is not None else "", line or "") if x)
        return Verdict("ask", card=card, connector=v.connector, kind=v.kind)   # even where "allow" is set or a grant covers it

    async def _browser_judgement(self, name: str, args: dict[str, Any]) -> tuple[str, str]:
        """(commit kind, site) of typing into a page, as commits.py judges it; ("", "") for any other step or without a browser."""
        if name not in ("browser_type", "android_type") or not args.get("text") or self.browser_step is None:   # a click carries no text of its own
            return "", ""
        try:
            return await self.browser_step(name, args)
        except Exception:  # noqa: BLE001 - a step that cannot be judged here is judged by its own tool
            return "", ""

    def _guard_hit(self, tool: Any, name: str, args: dict[str, Any], e: Any, commit: str, site: str) -> Any:
        if self.guard is None:
            return None
        if e is not None and e.kind in ("send", "change", "delete"):
            return self.guard.check(tool, args, e)
        if name in URL_TOOLS:
            return self.guard.check_url(tool, args)
        if site:
            basic_ok = bool(commit in APPLY_COMMITS and self.apply_covers is not None and self.apply_covers(site))
            return self.guard.check(tool, args, None, commit=commit, site=site, basic_ok=basic_ok)
        return None

    def hides(self, tool_name: str) -> bool:
        if self.once is not None and self.once.hides(tool_name):
            return True
        return self.permissions.hides(tool_name)

    def hidden_key(self) -> tuple[int, ...]:
        """Changes when the owner changes a setting (or a step's result becomes unknown or known): a running turn then works out its
        tool list again."""
        return (self.permissions.version, self.once.version if self.once is not None else 0)

    def limits_line(self) -> str:
        return self.permissions.limits_line()
