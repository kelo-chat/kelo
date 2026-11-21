"""Declared situation reads: each connected service (and each phone item the owner shares) declares ONE read-only call that gives a
snapshot of its current state. The hourly look (situation.py) reads them all through one code path (SourceReader) and puts what it finds
into the context pack as "Connected: <name>" sections.

Declarations are DATA, written next to each connector's own definition (presets.KeyPreset.situation, selfhosted `_p(situation=...)`,
oauth_connect.DeviceProvider.situation, mcp_oauth.McpService.situation, a Nest package's connector.situation, phone_data.SITUATION).
This module never names a connector, a connector id, a phone kind or a service's path (tests/test_situation_sources.py enforces it).

A declaration may name the kinds of data its rows are (`content`, a list): each kind is the name of a guidance file in
content/prompts/situation_kinds/ (KINDS_DIR). The hourly look shows the kind on the section and gives the model that file's guidance;
a read without `content` follows the general rules. The list of kinds is the files that exist, never a list in code.

A declared read is one of Kelo's own tools with fixed arguments. Only reads run: the effect must be a read and the owner's limits must
allow it, unattended. Snapshots hold hashes, ids and times only, never content.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from .agent.loop import Ours, ToolOutput
from .agent.fence import split
from .nest import _get as _dig
from .i18n import t
from .workers import current_turn

log = logging.getLogger("uvicorn.error")

# budgets (named, never inline magic numbers)
READ_SECONDS = 20.0            # one read
READ_PARALLEL = 4              # reads at once
SOURCES_SECONDS = 90.0         # all reads of one pass; what is not done by then is a timeout
SOURCES_CHARS = 24_000         # the sum of all declared sections; the rest (in sid order) is left out
MAX_IDS = 500                  # ids kept per snapshot (changes mode)
ROW_VALUE_CHARS = 200          # one value in a row
MODES = ("state", "changes")
SNAPSHOT = "situation:source:{sid}"
KINDS_DIR = "situation_kinds"   # content/prompts/<KINDS_DIR>/<kind>.md: one guidance file per kind of data; the file names are the kinds

_BINDING = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class SituationRead:
    tool: str                                  # one of Kelo's tool names
    arguments: Mapping[str, Any]               # JSON values; strings may hold {{binding}}
    mode: str = "state"                        # "state" | "changes"
    select: str = ""                           # dotted path to the rows in a JSON answer ("" = the answer itself)
    fields: Mapping[str, str] = field(default_factory=dict)   # label -> dotted path in a row; {} = the row as compact JSON
    id: tuple[str, ...] = ()                   # changes mode: dotted paths whose values make a row's id
    lines: str = ""                            # a text answer: keep the lines matching this regex (also what is fingerprinted)
    max_rows: int = 30
    max_chars: int = 3000
    every_minutes: int = 0                     # 0 = every pass; else at most once per this many minutes
    wakes: bool = True                         # False: shown when a pass runs, but its change alone never starts a research turn
    label: str = ""                            # a sub-name when one connector has several items (the phone's)
    content: tuple[str, ...] = ()              # the kinds of data the rows are (file names in KINDS_DIR); () = general rules only


@dataclass
class Projected:
    rows: list[str]
    ids: list[str]
    fingerprint: str
    more: int
    note: str = ""                 # set when the answer could not be read (a short reason, never content)


@dataclass
class Declared:
    sid: str                       # stable id: the definition's id (+ "/" + label for a phone item)
    name_key: str                  # what the names function turns into the connector's name
    label: str                     # a sub-name ("" = none)
    item: str                      # the vault item: the {{connection}} value ("" when none)
    read: SituationRead
    kind: str = "connector"        # "connector" | "phone" (sources_json)


@dataclass
class SourceSection:
    sid: str
    title: str
    rows: list[str]
    ids: list[str] | None          # changes mode: the ids this pass saw
    fingerprint: str
    note: str
    wakes: bool
    fence_source: str
    outcome: str
    call: str                      # the app's own words: tool + arguments without the connection name
    compact: bool = False          # not_due or unchanged: the pack says "No change since"
    since: float = 0.0             # when the fingerprint last changed
    kind: str = "connector"        # "connector" | "phone" (sources_json)
    content: tuple[str, ...] = ()  # the kinds of data of the rows (file names in KINDS_DIR)


# ------------------------------------------------------------------------------------------------------------- parsing
def content_kinds() -> frozenset[str]:
    """The kinds of data a read may name in `content`: the guidance files that exist (situation.prompts_dir() / KINDS_DIR / *.md)."""
    from .situation import prompts_dir   # lazy: situation imports this module
    d = prompts_dir() / KINDS_DIR
    return frozenset(p.stem for p in d.glob("*.md")) if d.is_dir() else frozenset()


def _bindings_in(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_BINDING.findall(value))
    if isinstance(value, Mapping):
        return set().union(*(_bindings_in(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_bindings_in(v) for v in value)) if value else set()
    return set()


def _json_ok(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def parse_read(raw: Mapping[str, Any], *, where: str) -> SituationRead:
    """Validates one declaration. ValueError with the reason (the caller decides who sees it)."""
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where}: the situation read must be an object")
    tool = raw.get("tool")
    if not isinstance(tool, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool):
        raise ValueError(f"{where}: tool must be a tool name")
    args = raw.get("arguments", {})
    if not isinstance(args, Mapping) or not _json_ok(args):
        raise ValueError(f"{where}: arguments must be a JSON object")
    unknown = _bindings_in(args) - set(BINDINGS)
    if unknown:
        raise ValueError(f"{where}: unknown binding {sorted(unknown)[0]}")
    mode = raw.get("mode", "state")
    if mode not in MODES:
        raise ValueError(f"{where}: mode must be state or changes")
    select = raw.get("select", "")
    if not isinstance(select, str):
        raise ValueError(f"{where}: select must be a path")
    fields = raw.get("fields", {})
    if not isinstance(fields, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
        raise ValueError(f"{where}: fields must map label to path")
    ids = raw.get("id", ())
    ids = (ids,) if isinstance(ids, str) else tuple(ids) if isinstance(ids, (list, tuple)) else None
    if ids is None or not all(isinstance(i, str) for i in ids):
        raise ValueError(f"{where}: id must be a path or a list of paths")
    lines = raw.get("lines", "")
    if not isinstance(lines, str) or len(lines) > 200:
        raise ValueError(f"{where}: lines must be a regex of at most 200 characters")
    try:
        re.compile(lines)
    except re.error as e:
        raise ValueError(f"{where}: lines does not compile ({e.__class__.__name__})") from None
    max_rows, max_chars = raw.get("max_rows", 30), raw.get("max_chars", 3000)
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or not 1 <= max_rows <= 100:
        raise ValueError(f"{where}: max_rows must be 1..100")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 200 <= max_chars <= 6000:
        raise ValueError(f"{where}: max_chars must be 200..6000")
    every = raw.get("every_minutes", 0)
    if not isinstance(every, int) or isinstance(every, bool) or (every and every < 60):
        raise ValueError(f"{where}: every_minutes is 0 or at least 60")
    wakes = raw.get("wakes", True)
    if not isinstance(wakes, bool):
        raise ValueError(f"{where}: wakes must be true or false")
    label = raw.get("label", "")
    if not isinstance(label, str):
        raise ValueError(f"{where}: label must be text")
    if mode == "changes" and not (ids or lines):
        raise ValueError(f"{where}: changes needs id (JSON) or lines (text)")
    content = raw.get("content", ())
    content = (content,) if isinstance(content, str) else content
    if not isinstance(content, (list, tuple)) or not all(isinstance(c, str) for c in content):
        raise ValueError(f"{where}: content must be a kind of data or a list of them")
    unknown = sorted(set(content) - content_kinds())
    if unknown:
        raise ValueError(f"{where}: unknown kind of data {unknown[0]!r}; the kinds are {', '.join(sorted(content_kinds()))}")
    return SituationRead(tool=tool, arguments=dict(args), mode=mode, select=select, fields=dict(fields), id=ids, lines=lines,
                         max_rows=max_rows, max_chars=max_chars, every_minutes=every, wakes=wakes, label=label,
                         content=tuple(dict.fromkeys(content)))


@lru_cache(maxsize=256)
def _parse_cached(blob: str, where: str) -> SituationRead:
    return parse_read(json.loads(blob), where=where)


def read_of(defn_situation: Any, *, where: str) -> SituationRead | None:
    """The read a definition declares: None when it declares none; a parsed SituationRead passes through."""
    if defn_situation is None:
        return None
    if isinstance(defn_situation, SituationRead):
        return defn_situation
    return _parse_cached(json.dumps(defn_situation, sort_keys=True), where)


# ------------------------------------------------------------------------------------------------------------- arguments
def _time_values(now: float, since: float, tz: Any) -> dict[str, Any]:
    utc = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"since": utc, "since_unix": int(since), "since_date": datetime.fromtimestamp(since, tz).date().isoformat(),
            "now": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "now_unix": int(now),
            "today": datetime.fromtimestamp(now, tz).date().isoformat()}


BINDINGS = frozenset({"connection", *_time_values(0.0, 0.0, timezone.utc)})   # the names a declaration may use in {{...}}


def fill(read: SituationRead, values: Mapping[str, Any]) -> dict[str, Any]:
    """The arguments with the bindings filled. A string that is exactly one binding keeps its type; inside a longer string the value is
    percent-encoded (a URL path or query)."""
    def walk(v: Any) -> Any:
        if isinstance(v, str):
            whole = re.fullmatch(r"\{\{(\w+)\}\}", v)
            if whole:
                return values[whole.group(1)]
            return _BINDING.sub(lambda m: quote(str(values[m.group(1)]), safe=""), v)
        if isinstance(v, Mapping):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        return v
    return walk(dict(read.arguments))


def _call_words(tool: str, args: Mapping[str, Any]) -> str:
    """The app's own words for the call, without the connection name (the research turn may read more with its own tools)."""
    shown = {k: v for k, v in args.items() if k != "name"}
    return f"{tool} {json.dumps(shown, ensure_ascii=False, sort_keys=True)}" if shown else tool


# ------------------------------------------------------------------------------------------------------------- projection
def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float, str)):
        return _clip(v, ROW_VALUE_CHARS)
    return _clip(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")), ROW_VALUE_CHARS)


def _row_text(read: SituationRead, row: Any) -> str:
    if not read.fields:
        return _clip(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")), ROW_VALUE_CHARS * 4)
    parts = []
    for label, path in read.fields.items():
        v = _value(_dig(row, path)) if isinstance(row, (Mapping, list)) else ""
        if v:
            parts.append(f"{label}: {v}")
    return " · ".join(parts)


def _row_id(read: SituationRead, row: Any) -> str:
    vals = [_dig(row, p) for p in read.id]
    if any(v is not None for v in vals):
        return "|".join("" if v is None else str(v) for v in vals)
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _data_text(text: str) -> str:
    """The data parts of a tool answer (fence.split); a plain answer with no data parts is taken whole."""
    parts = split(text)
    if any(is_data for is_data, _p in parts):
        return "".join(p for is_data, p in parts if is_data)
    return text


def project(read: SituationRead, result_text: str) -> Projected:
    """Rows, ids and fingerprint of one answer, under the declaration's limits."""
    data = _data_text(result_text).strip()
    if not data:
        return Projected([], [], "", 0, note="the answer was empty")
    try:
        answer: Any = json.loads(data)
        is_json = True
    except ValueError:
        answer, is_json = None, False
    if is_json:
        node: Any = answer
        for seg in [s for s in read.select.split(".") if s]:
            node = _dig(node, seg)
        if node is None:
            return Projected([], [], "", 0, note="the answer has no rows at the expected place")
        items = node if isinstance(node, list) else [node]
        keyed = [(_row_id(read, r) if read.id else "", _row_text(read, r)) for r in items]
    else:
        kept = [ln.strip() for ln in data.splitlines() if ln.strip()]
        if read.lines:
            kept = [ln for ln in kept if re.search(read.lines, ln)]
        keyed = [(hashlib.sha256(ln.encode()).hexdigest()[:16], _clip(ln, ROW_VALUE_CHARS * 4)) for ln in kept]
    rows: list[str] = []
    ids: list[str] = []
    used = 0
    for rid, tx in keyed:
        if not tx:
            continue
        if len(rows) >= read.max_rows or used + len(tx) > read.max_chars:
            break
        rows.append(tx)
        ids.append(rid)
        used += len(tx)
    more = max(0, len([1 for _rid, tx in keyed if tx]) - len(rows))
    basis = "\n".join(sorted(set(ids))) if read.mode == "changes" else "\n".join(rows)
    return Projected(rows, ids, hashlib.sha256(basis.encode()).hexdigest(), more)


# ------------------------------------------------------------------------------------------------------------- collecting
def declared(ways: Mapping[str, Mapping[str, Any]], registry: Any, phone_declared: Iterable[Declared] = ()) -> list[Declared]:
    """Every declared read of what is connected now, in sid order (stable). Each connected row's definition is found in the table its
    `connect` names (no id is named here); a definition serving several catalog ids is collected once. `phone_declared` are the phone
    items the owner shares (phone_data.declared_items)."""
    from .mcp_oauth import SERVICES
    from .oauth_connect import PROVIDERS
    from .presets import KEYS

    device = {p.id: p for p in PROVIDERS.values()}
    out: list[Declared] = []
    seen: set[str] = set()
    for cid, row in sorted(ways.items()):
        if not row.get("connected"):
            continue
        via, connect = row.get("via") or cid, row.get("connect")
        defn: Any = None
        if connect == "key":
            defn = KEYS.get(via)
        elif connect == "device":
            defn = device.get(via)
        elif connect == "mcp":
            defn = SERVICES.get(via)
        if defn is None and registry is not None and (row.get("installed") or connect == "mcp"):
            defn = registry.installed_connector(via)
        if defn is None:
            continue
        sid = str(getattr(defn, "id", via))
        if sid in seen:
            continue
        seen.add(sid)
        read = read_of(getattr(defn, "situation", None), where=sid)
        if read is not None:
            out.append(Declared(sid=sid, name_key=sid, label="", item=str(row.get("item") or ""), read=read))
    out += list(phone_declared)
    return sorted(out, key=lambda d: d.sid)


# ------------------------------------------------------------------------------------------------------------- the reader
class SourceReader:
    """Reads every declared source of one pass: one code path for every kind of connector and phone item (see the module docstring)."""

    def __init__(self, *, declared: Callable[[], list[Declared]], tools: Callable[[], list[Any]], gate: Any, permissions: Any,
                 names: Callable[[str], str], store: Any, tz: Callable[[], Any], clock: Callable[[], float]):
        self._declared, self._tools, self._gate, self._permissions = declared, tools, gate, permissions
        self._names, self._store, self._tz, self._clock = names, store, tz, clock
        self._pending: dict[str, dict[str, Any]] = {}
        self._last: list[dict[str, Any]] = []

    def _saved(self, sid: str) -> dict[str, Any]:
        try:
            got = json.loads(self._store.meta(SNAPSHOT.format(sid=sid)) or "null")
        except ValueError:
            got = None
        return got if isinstance(got, dict) else {}

    def _title(self, d: Declared) -> str:
        name = self._names(d.name_key)
        return f"{name} · {t(d.label)}" if d.label else name

    async def sections(self, now: float, since: float) -> list[SourceSection]:
        """What build_pack adds: one section per declared source, in sid order; the budget leaves out the ones past SOURCES_CHARS."""
        self._pending = {}
        decl = list(self._declared())
        tools = {getattr(t, "name", ""): t for t in self._tools()}
        base = _time_values(now, since, self._tz())
        sem = asyncio.Semaphore(READ_PARALLEL)
        results: dict[str, SourceSection] = {}

        async def one(d: Declared) -> None:
            async with sem:
                results[d.sid] = await self._read_one(d, tools, base, now)

        tasks = {d.sid: asyncio.ensure_future(one(d)) for d in decl}
        if tasks:
            _finished, pending = await asyncio.wait(list(tasks.values()), timeout=SOURCES_SECONDS)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        out: list[SourceSection] = []
        used = 0
        for d in decl:
            sec = results.get(d.sid) or self._failed(d, "timeout", "")
            size = sum(len(r) for r in sec.rows)
            if sec.rows and used + size > SOURCES_CHARS:
                sec = self._failed(d, "skipped", "the pack is full")
                self._pending.pop(d.sid, None)   # not shown: its snapshot is not saved, so the next pass sees it as new
            used += size if sec.rows else 0
            out.append(sec)
        self._last = [{"id": s.sid, "name": s.title, "kind": s.kind, "state": s.outcome, "entries": len(s.rows)} for s in out]
        return out

    def _failed(self, d: Declared, outcome: str, note: str) -> SourceSection:
        return SourceSection(d.sid, self._title(d), [], None, "", note, d.read.wakes, d.kind, outcome,
                             _call_words(d.read.tool, d.read.arguments), kind=d.kind, content=d.read.content)

    async def _read_one(self, d: Declared, tools: Mapping[str, Any], base: dict[str, Any], now: float) -> SourceSection:
        read, saved = d.read, self._saved(d.sid)
        call = _call_words(read.tool, read.arguments)
        if read.every_minutes and saved.get("read_at") and now - float(saved["read_at"]) < read.every_minutes * 60:
            return self._unchanged(d, "not_due", saved, call)
        tool = tools.get(read.tool)
        if tool is None:
            return self._failed(d, "failed", "tool_missing")
        args = fill(read, {**base, "connection": d.item})
        call = _call_words(read.tool, args)
        token = current_turn.set({"unattended": True, "source": "situation", "title": self._title(d)})
        try:
            await _ensure(tool, args)
            eff = self._permissions.effect(tool, args)
            effect_kind = getattr(eff, "kind", None)   # what the call does (a read or not), not a connector's kind
            if eff is None or effect_kind != "read":
                log.warning("situation read %s: %s is not a read; not run", d.sid, read.tool)
                return self._failed(d, "failed", "not_a_read")
            verdict = await self._gate.check(tool, args)
            if verdict.action in ("off", "ask"):
                return self._failed(d, "off", "")
            out = await asyncio.wait_for(tool.handler(args), READ_SECONDS)
        except asyncio.TimeoutError:
            return self._failed(d, "timeout", "")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - one source failing never stops the pass; the message may quote the service
            return self._failed(d, "failed", type(e).__name__)
        finally:
            current_turn.reset(token)
        if isinstance(out, Ours) or (isinstance(out, str) and out.startswith("error:")):
            return self._failed(d, "failed", "error")
        got = project(read, out.text if isinstance(out, ToolOutput) else str(out))
        if got.note:
            return self._failed(d, "failed", got.note[:160])
        return self._answered(d, call, got, saved, now)

    def _unchanged(self, d: Declared, outcome: str, saved: dict[str, Any], call: str) -> SourceSection:
        """A source not read this pass, or read and unchanged: the pack says so in one line; its saved fingerprint and ids carry over."""
        ids = saved.get("ids") if d.read.mode == "changes" else None
        return SourceSection(d.sid, self._title(d), [], list(ids or []) if d.read.mode == "changes" else None,
                             str(saved.get("fp", "")), "", d.read.wakes, d.kind, outcome, call, compact=True,
                             since=float(saved.get("since") or 0), kind=d.kind, content=d.read.content)

    def _answered(self, d: Declared, call: str, got: Projected, saved: dict[str, Any], now: float) -> SourceSection:
        read = d.read
        if read.mode == "changes":
            seen = set(saved.get("ids") or []) if saved.get("read_at") else None
            fresh = [(r, i) for r, i in zip(got.rows, got.ids) if seen is None or i not in seen]
            if seen is None:
                outcome = "new"
            else:
                outcome = "changed" if fresh else "same"
            if outcome == "same":
                ids_all = list(got.ids)
                section = self._unchanged(d, "same", {**saved, "ids": ids_all}, call)
            else:
                section = SourceSection(d.sid, self._title(d), [r for r, _i in fresh], list(got.ids), got.fingerprint,
                                        f"{got.more} more not shown" if got.more else "", read.wakes, d.kind, outcome, call,
                                        since=now, kind=d.kind, content=read.content)
            self._pending[d.sid] = {"fp": got.fingerprint, "ids": list(got.ids)[:MAX_IDS], "since": section.since or now, "read_at": now}
            return section
        same = bool(saved.get("fp")) and saved.get("fp") == got.fingerprint
        since = float(saved.get("since") or now) if same else now
        self._pending[d.sid] = {"fp": got.fingerprint, "ids": None, "since": since, "read_at": now}   # read_at: the last good read
        if same:
            return self._unchanged(d, "same", {**saved, "since": since}, call)
        outcome = "changed" if saved.get("fp") else "new"
        return SourceSection(d.sid, self._title(d), got.rows, None, got.fingerprint, f"{got.more} more not shown" if got.more else "",
                             read.wakes, d.kind, outcome, call, since=now, kind=d.kind, content=read.content)

    def commit(self) -> None:
        """Saves the snapshots of the pass that got through (a failed pass re-reads the same changes next time)."""
        for sid, snap in self._pending.items():
            self._store.set_meta(SNAPSHOT.format(sid=sid), json.dumps(snap, sort_keys=True))
        self._pending = {}

    def last(self) -> list[dict[str, Any]]:
        return list(self._last)


async def _ensure(tool: Any, args: dict[str, Any]) -> None:
    from . import effects
    await effects.ensure_judged(tool, args)


__all__ = ["SituationRead", "Declared", "SourceReader", "SourceSection", "Projected", "parse_read", "read_of", "fill", "project",
           "declared", "content_kinds", "KINDS_DIR", "READ_SECONDS", "READ_PARALLEL", "SOURCES_SECONDS", "SOURCES_CHARS"]
