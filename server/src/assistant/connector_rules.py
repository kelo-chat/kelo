"""Connector rules in data (docs/connector-rules.md): which method of a connector sends, posts, deletes or forwards, how strongly it is
protected, and where its recipient is. The data is `data/connector_rules.json` (built in) and the `rules` section of a Nest package.

`ConnectorRules.for_request(connector, http_method, url, args)` and `.for_tool(connector, tool, args)` find the declared method of a
call and read its recipients from the call itself. effects.effect_of asks both and so gives a messenger or connector send its bound
recipients; the policy is read at the three places that could skip a card (permissions, schedule_grants, data_trail).

Nothing here decides by words: a method is found by its declared HTTP method and path shape or its declared tool name, a recipient by its
declared place in the call.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

POLICIES = ("standing", "task", "fresh")
KINDS = ("send", "change", "delete")
VIAS = ("request", "tool")
HTTP = ("POST", "PUT", "PATCH", "DELETE")
MAX_METHODS = 60
MAX_RECIPIENTS = 50
DATA = Path(__file__).with_name("data") / "connector_rules.json"
SEP = "#"   # connector:<name>#<recipient>
KIND_ORDER = {"read": 0, "change": 1, "send": 2, "delete": 3}


class RulesError(ValueError):
    """A rules section that cannot be used, with the reason."""


@dataclass(frozen=True)
class Method:
    id: str
    via: str
    kind: str
    policy: str = "standing"
    http: str = ""
    path: tuple[str, ...] = ()
    tool: str = ""
    where: tuple[str, ...] = ()      # places of the recipient: "path", "json.<key>", "arguments.<key>", "query.<key>"

    def recipients(self, args: Mapping[str, Any], captured: tuple[str, ...] = ()) -> list[str]:
        """The recipients of one call, case-folded, once each, in order. Empty when a declared place holds nothing."""
        out: list[str] = []
        for spec in self.where:
            for raw in _values(spec, args, captured):
                for piece in _split(raw):
                    if piece and piece not in out:
                        out.append(piece)
        return out[:MAX_RECIPIENTS]


def _split(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [p for v in value for p in _split(v)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if not isinstance(value, str):
        return []
    text = value.strip()
    if "@" in text and not text.startswith("@"):
        from .prepared_send import address_list
        return address_list(text)       # a mail-like value: "Name <a@b>", "a@b, c@d"
    return [text.casefold()] if text else []


def _dig(obj: Any, key: str) -> Any:
    for part in key.split("."):
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except ValueError:
                return None
        if not isinstance(obj, Mapping) or part not in obj:
            return None
        obj = obj[part]
    return obj


def _values(spec: str, args: Mapping[str, Any], captured: tuple[str, ...]) -> list[Any]:
    if spec == "path":
        return list(captured)
    root, _, key = spec.partition(".")
    if root == "json":
        return [_dig(args.get("json"), key)] if key else []
    if root == "arguments":
        return [_dig(args.get("arguments"), key)] if key else []
    if root == "query":
        return [v for k, v in parse_qsl(urlsplit(str(args.get("url") or "")).query) if k == key]
    return []


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _match(pattern: tuple[str, ...], url: str) -> tuple[str, ...] | None:
    """The segments `{recipient}` captured when the END of the URL's path has the shape of `pattern`, else None."""
    full = url if "://" in url else "https://x" + (url if url.startswith("/") else "/" + url)
    segs = _segments(urlsplit(full).path)
    if len(segs) < len(pattern):
        return None
    got: list[str] = []
    for want, have in zip(pattern, segs[len(segs) - len(pattern):]):
        if want == "{recipient}":
            got.append(have)
        elif want != "{}" and want != have:
            return None
    return tuple(got)


# ---------------------------------------------------------------------------------------------------------------- checking
def parse_method(raw: Any, where: str = "rules") -> Method:
    if not isinstance(raw, dict):
        raise RulesError(f"{where}: a method must be an object")
    mid = raw.get("id")
    if not isinstance(mid, str) or not mid.strip() or len(mid) > 60:
        raise RulesError(f"{where}: a method needs an id (up to 60 characters)")
    via, kind, policy = raw.get("via"), raw.get("kind"), raw.get("policy", "standing")
    if via not in VIAS:
        raise RulesError(f"{where}.{mid}: via must be one of {', '.join(VIAS)}")
    if kind not in KINDS:
        raise RulesError(f"{where}.{mid}: kind must be one of {', '.join(KINDS)}")
    if policy not in POLICIES:
        raise RulesError(f"{where}.{mid}: policy must be one of {', '.join(POLICIES)}")
    spec = raw.get("recipient", [])
    specs = [spec] if isinstance(spec, str) else spec
    if not isinstance(specs, list) or len(specs) > 8 or not all(isinstance(s, str) for s in specs):
        raise RulesError(f"{where}.{mid}: recipient must be a place or a list of places")
    for s in specs:
        root, _, key = s.partition(".")
        if not (s == "path" or (root in ("json", "arguments", "query") and key and len(s) <= 80)):
            raise RulesError(f"{where}.{mid}: a recipient place is path, json.<key>, arguments.<key> or query.<key>")
    http, path, tool = "", (), ""
    if via == "request":
        http = str(raw.get("http") or "").upper()
        text = raw.get("path")
        if http not in HTTP or not isinstance(text, str) or not _segments(text) or len(text) > 120 or "?" in text:
            raise RulesError(f"{where}.{mid}: a request method needs http ({', '.join(HTTP)}) and the end of its path")
        path = tuple(_segments(text))
        if "path" in specs and "{recipient}" not in path:
            raise RulesError(f"{where}.{mid}: the recipient is in the path, so the path needs a {{recipient}} segment")
    else:
        tool = raw.get("tool")
        if not isinstance(tool, str) or not tool.strip() or len(tool) > 120:
            raise RulesError(f"{where}.{mid}: a tool method needs the tool's name")
        if "path" in specs:
            raise RulesError(f"{where}.{mid}: a tool has no path to take the recipient from")
    return Method(mid.strip(), via, kind, policy, http, path, tool, tuple(specs))


def parse_rules(raw: Any, where: str = "rules") -> dict[str, tuple[Method, ...]]:
    """`{"<connector id>": [method, ...]}` checked; raises RulesError."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > 200:
        raise RulesError(f"{where} must be an object of connectors")
    out: dict[str, tuple[Method, ...]] = {}
    for cid, rows in raw.items():
        if not isinstance(cid, str) or not cid.strip() or not isinstance(rows, list) or not rows or len(rows) > MAX_METHODS:
            raise RulesError(f"{where}: each connector needs a list of up to {MAX_METHODS} methods")
        methods = tuple(parse_method(r, f"{where}.{cid}") for r in rows)
        ids = [m.id for m in methods]
        if len(set(ids)) != len(ids):
            raise RulesError(f"{where}.{cid}: method ids must be unique")
        out[cid.strip().casefold()] = methods
    return out


# ---------------------------------------------------------------------------------------------------------------- lookup
@dataclass(frozen=True)
class Found:
    method: Method
    recipients: list[str]

    @property
    def policy(self) -> str:
        return self.method.policy


class ConnectorRules:
    """The declarations of the built-in connectors plus, through `package_source`, those of installed Nest packages."""

    def __init__(self, builtin: Mapping[str, tuple[Method, ...]] | None = None,
                 package_source: Callable[[str], Mapping[str, Any] | None] | None = None) -> None:
        self.builtin = dict(builtin or {})
        self.package_source = package_source

    def methods(self, connector: str | None) -> tuple[Method, ...]:
        cid = (connector or "").strip().casefold()
        if not cid:
            return ()
        if cid in self.builtin:
            return self.builtin[cid]
        if self.package_source is not None:
            try:
                got = self.package_source(cid)
            except Exception:  # noqa: BLE001 - a broken source means no declarations, never a failure of the call
                return ()
            return tuple(got.get(cid, ())) if isinstance(got, Mapping) else ()
        return ()

    def for_request(self, connector: str | None, http: str, url: str, args: Mapping[str, Any]) -> Found | None:
        verb = (http or "").upper()
        for m in self.methods(connector):
            if m.via == "request" and m.http == verb:
                cap = _match(m.path, url or "")
                if cap is not None:
                    return Found(m, m.recipients({**args, "url": url}, cap))
        return None

    def for_tool(self, connector: str | None, tool: str, args: Mapping[str, Any]) -> Found | None:
        for m in self.methods(connector):
            if m.via == "tool" and m.tool == tool:
                return Found(m, m.recipients(args))
        return None


def load_builtin(path: Path = DATA) -> dict[str, tuple[Method, ...]]:
    return parse_rules(json.loads(path.read_text(encoding="utf-8")).get("connectors"), "connector_rules.json")


_RULES: ConnectorRules | None = None


def rules() -> ConnectorRules:
    global _RULES
    if _RULES is None:
        _RULES = ConnectorRules(load_builtin())
    return _RULES


def set_package_source(source: Callable[[str], Mapping[str, Any] | None] | None) -> None:
    """server.py: a function giving the parsed rules ({id: methods}) of an installed package, or None."""
    rules().package_source = source


def bound_target(connector_name: str, recipient: str) -> str:
    return f"connector:{connector_name}{SEP}{recipient}"


def strictest(a: str, b: str) -> str:
    """The stricter of two kinds: a declaration can only tighten."""
    return a if KIND_ORDER.get(a, 0) >= KIND_ORDER.get(b, 0) else b
