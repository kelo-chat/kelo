"""Memory pass pipeline part 1: claims (steps 1-5, claims table, dated log, memory_explain).

Docs: docs/memory-pipeline.md.
Shared shapes: memory_types.py.
"""

from __future__ import annotations

import os
import asyncio
import json
import logging
import re
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .agent import Tool
from .home import Home, HomeError, PEOPLE_DIR
from .memory_inbox import OWNER_LINE, MemoryInbox
from .owner_messages import OwnerMessages
from .memory_types import (
    CLAIM_KINDS,
    SALIENCE,
    SPEAKERS,
    Claim,
    Window,
)
from .memory_upkeep import (
    CAPS,
    LOOKBACK_SECONDS,
    MAX_LOOKBACK_SECONDS,
    MEMORY_BUDGET,
    PERSON_CAP,
    _content_lines,
    in_layout,
    scrub,
)
from .agent.fence import fence
from .model_limits import limit_of
from .providers import ChatRequest, TextDelta
from .reflection import parse_output

log = logging.getLogger(__name__)

FENCE_BUDGET = 120_000   # characters of one fenced block (a window is far smaller; nothing the checks need is cut)
PROMPTS_DIR = Path(os.environ.get("ASSISTANT_CONTENT_DIR") or Path(__file__).resolve().parents[2] / "content") / "prompts" / "memory"


def add_claim_row(
    store: Any,
    claim: Claim,
    run_id: str,
    source_path: str,
    source_line: int,
    ts: float,
) -> None:
    """Insert one admitted claim into memory_claims."""
    with store._lock:
        store._db.execute(
            """INSERT INTO memory_claims(
                claim_id, run_id, kind, salience, text, quote, speaker,
                handle, supersedes, status, valid_until, source_path,
                source_line, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
            (
                claim.id,
                run_id,
                claim.kind,
                claim.salience,
                claim.text,
                claim.quote,
                claim.speaker,
                claim.handle,
                claim.supersedes,
                claim.valid_until,
                source_path,
                source_line,
                ts,
                ts,
            ),
        )
        store._db.commit()


def supersede_claim(store: Any, claim_id: str, ts: float) -> None:
    """Mark a claim as superseded."""
    with store._lock:
        store._db.execute(
            "UPDATE memory_claims SET status = 'superseded', updated_at = ? WHERE claim_id = ?",
            (ts, claim_id),
        )
        store._db.commit()


def record_run(
    store: Any,
    run_id: str,
    started_at: float,
    finished_at: float,
    trigger: str,
    outcome: str,
    stats: dict[str, Any],
) -> None:
    """Write run receipt to memory_runs."""
    with store._lock:
        store._db.execute(
            """INSERT INTO memory_runs(run_id, started_at, finished_at, trigger, outcome, stats)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                   finished_at=excluded.finished_at,
                   trigger=excluded.trigger,
                   outcome=excluded.outcome,
                   stats=excluded.stats""",
            (run_id, started_at, finished_at, trigger, outcome, json.dumps(stats, ensure_ascii=False)),
        )
        store._db.commit()


def active_claims(store: Any, limit: int = 20) -> list[Claim]:
    """Return active claims, newest first."""
    with store._lock:
        rows = store._db.execute(
            """SELECT claim_id, kind, salience, text, quote, speaker, handle, valid_until, supersedes
               FROM memory_claims WHERE status = 'active' ORDER BY rowid DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            Claim(
                id=r["claim_id"],
                kind=r["kind"],
                salience=r["salience"],
                text=r["text"],
                quote=r["quote"],
                speaker=r["speaker"],
                handle=r["handle"],
                valid_until=r["valid_until"],
                supersedes=r["supersedes"],
            )
            for r in rows
        ]


def search_claims(
    store: Any,
    query: str,
    limit: int = 5,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """Search claims by full text (FTS5 with fallback to LIKE)."""
    words = re.findall(r"\w+", query or "")
    if not words:
        return []
    terms = [f'"{w}"*' for w in words if len(w) >= 2]
    if not terms:
        terms = [f'"{w}"' for w in words]
    fts_q = " OR ".join(terms)

    with store._lock:
        try:
            sql = """
                SELECT c.claim_id, c.run_id, c.kind, c.salience, c.text, c.quote, c.speaker,
                       c.handle, c.supersedes, c.status, c.valid_until, c.source_path,
                       c.source_line, c.created_at, c.updated_at
                FROM memory_claims c
                JOIN memory_claims_fts f ON c.rowid = f.rowid
                WHERE memory_claims_fts MATCH ?
            """
            if active_only:
                sql += " AND c.status = 'active'"
            sql += " ORDER BY (c.status = 'active') DESC, bm25(memory_claims_fts) LIMIT ?"
            rows = store._db.execute(sql, (fts_q, limit)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            like_sql = """
                SELECT claim_id, run_id, kind, salience, text, quote, speaker,
                       handle, supersedes, status, valid_until, source_path,
                       source_line, created_at, updated_at
                FROM memory_claims
                WHERE (text LIKE ? OR quote LIKE ?)
            """
            if active_only:
                like_sql += " AND status = 'active'"
            like_sql += " ORDER BY (status = 'active') DESC, rowid DESC LIMIT ?"
            pattern = f"%{words[0]}%"
            rows = store._db.execute(like_sql, (pattern, pattern, limit)).fetchall()
            return [dict(r) for r in rows]


# ------------------------------------------------------------------ Explain agent tool
def explain_tool(store: Any, tz: Callable[[], str] = lambda: "UTC") -> Tool:
    """Agent tool to inspect what is remembered and why: memory_explain(text)."""
    async def memory_explain(args: dict[str, Any]) -> str:
        text = str(args.get("text") or "").strip()
        if not text:
            return "error: text query is required"
        claims = search_claims(store, text, limit=5)
        if not claims:
            return "No memory claims match that statement."
        try:
            zone = ZoneInfo(tz() if callable(tz) else tz)
        except Exception:
            zone = ZoneInfo("UTC")
        lines = []
        for c in claims:
            date_str = datetime.fromtimestamp(c["created_at"], zone).strftime("%Y-%m-%d")
            status = c["status"]
            if c.get("supersedes"):
                status += f" (supersedes {c['supersedes']})"
            item = [
                f"- Claim: {c['text']}",
                f"  Quote: \"{c['quote']}\"",
                f"  Speaker: {c['speaker']}",
                f"  Date: {date_str}",
                f"  Source: {c['handle']}",
                f"  Status: {status}",
            ]
            if c.get("valid_until"):
                item.append(f"  Valid until: {c['valid_until']}")
            lines.append("\n".join(item))
        return "\n\n".join(lines)

    return Tool(
        "memory_explain",
        "Look up the active memory claims that back what you know about a topic or statement, with quotes and sources, for 'how do you know that?' questions.",
        {"type": "object", "properties": {"text": {"type": "string", "description": "the topic, fact or statement to look up"}}, "required": ["text"]},
        memory_explain,
        read_only=True,
    )


def claims_tools(store: Any, tz: Callable[[], str] = lambda: "UTC") -> list[Tool]:
    """Expose memory claims agent tools."""
    return [explain_tool(store, tz=tz)]


# ------------------------------------------------------------------ Step 1: build_window
def build_window(
    store: Any,
    home: Home,
    inbox: MemoryInbox | None = None,
    tz: Callable[[], str] | str = "UTC",
    clock: Callable[[], float] = time.time,
    since: float | None = None,
    run_id: str | None = None,
) -> Window:
    """Step 1: gather the owner's own messages and the owner's inbox lines into a Window (docs/memory-pipeline.md, step 1)."""
    now = clock()
    tz_str = tz() if callable(tz) else tz
    try:
        zone = ZoneInfo(tz_str or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")

    as_of = datetime.fromtimestamp(now, zone).isoformat()
    date_str = datetime.fromtimestamp(now, zone).strftime("%Y-%m-%d")
    log_path = f"memory/log/{date_str}.md"

    if run_id is None:
        run_id = datetime.fromtimestamp(now, zone).strftime("%Y%m%dT%H%M%S") + f"-{secrets.token_hex(3)}"

    if since is None:
        try:
            seen = float(store.meta("memory:seen_until") or 0)
        except ValueError:
            seen = 0.0
        since = max(seen or now - LOOKBACK_SECONDS, now - MAX_LOOKBACK_SECONDS)

    # The owner's own messages only (owner_messages.py), and the messages the owner's inbox lines name, older ones included. Tasks,
    # approvals, the assistant's replies and every hidden row are not read by the pass at all.
    owners = OwnerMessages(store)
    messages = [{"id": r["id"], "role": "user", "text": scrub(r["text"])[0], "created_at": r["created_at"]} for r in owners.since(since)]
    inbox_lines = [ln for ln in (inbox.lines() if inbox is not None else []) if OWNER_LINE.match(ln)]
    have = {m["id"] for m in messages}
    named = [int(OWNER_LINE.match(ln).group(2)) for ln in inbox_lines]
    messages += [{"id": r["id"], "role": "user", "text": scrub(r["text"])[0], "created_at": r["created_at"]}
                 for r in owners.by_ids([i for i in named if i not in have])]
    messages.sort(key=lambda m: m["id"])
    has_new = bool(messages or inbox_lines)

    return Window(
        run_id=run_id,
        as_of=as_of,
        since=since,
        messages=messages,
        inbox=inbox_lines,
        log_path=log_path,
        has_new=has_new,
    )


# ------------------------------------------------------------------ Step 2: reconcile_due
def _extract_dates(text: str) -> list[str]:
    """Extract standard ISO or locale formatted date strings as YYYY-MM-DD."""
    dates = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        dates.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    for m in re.finditer(r"\b(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dates.append(f"{y:04d}-{mo:02d}-{d:02d}")
    for m in re.finditer(r"\b(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dates.append(f"{y:04d}-{mo:02d}-{d:02d}")
    return dates


def reconcile_due(
    store: Any,
    home: Home,
    as_of: str,
    admitted_claims: list[Claim] | None = None,
    clock: Callable[[], float] | float | None = None,
) -> bool:
    """Step 2: determine whether reconciliation is due."""
    # 1. Admitted claims this run
    if admitted_claims:
        return True

    # 2. Over cap
    try:
        mem_text = home.read("MEMORY.md")
    except HomeError:
        mem_text = ""
    if len(mem_text) > MEMORY_BUDGET:
        return True

    try:
        user_text = home.read("user.md")
    except HomeError:
        user_text = ""
    if len(user_text) > CAPS.get("user.md", 1500):
        return True

    try:
        agents_text = home.read("AGENTS.md")
    except HomeError:
        agents_text = ""
    if len(agents_text) > CAPS.get("AGENTS.md", 1500):
        return True

    try:
        for p in home.list(PEOPLE_DIR):
            if p.get("type") == "file" and (p.get("bytes") or 0) > PERSON_CAP:
                return True
    except HomeError:
        pass

    # 3. Dates before as_of
    as_of_date = as_of[:10] if len(as_of) >= 10 else ""
    if as_of_date and mem_text:
        for line in mem_text.splitlines():
            for d in _extract_dates(line):
                if d < as_of_date:
                    return True

    # 4. Layout migration
    if mem_text and bool(_content_lines(mem_text)) and not in_layout(mem_text):
        return True

    # 5. 7 days since last sweep
    now = clock() if callable(clock) else (clock if clock is not None else time.time())
    try:
        last_sweep = float(store.meta("memory:last_sweep_at") or 0)
    except ValueError:
        last_sweep = 0.0
    if (now - last_sweep) >= 7 * 86400:   # never swept counts as due; the pass driver sets memory:last_sweep_at after a sweep
        return True

    return False


# ------------------------------------------------------------------ Step 4: Shape checks
def _normalize_text(s: str) -> str:
    """Normalize quotes and whitespace for verbatim substring checking."""
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


def check_shape(claim: Claim, window: Window) -> tuple[bool, str]:
    """Step 4 shape checks: validate schema, handle existence, verbatim quote, speaker, valid_until, secrets."""
    if claim.kind not in CLAIM_KINDS:
        return False, f"invalid kind: {claim.kind}"
    if claim.salience not in SALIENCE:
        return False, f"invalid salience: {claim.salience}"
    if claim.speaker not in SPEAKERS:
        return False, f"invalid speaker: {claim.speaker}"
    if not claim.text or not isinstance(claim.text, str) or not claim.text.strip():
        return False, "empty text"
    if not claim.quote or not isinstance(claim.quote, str) or not claim.quote.strip():
        return False, "empty quote"
    if not claim.handle or not isinstance(claim.handle, str) or not claim.handle.strip():
        return False, "empty handle"

    # Only the owner's own words are admitted: speaker user, and a handle that is one of this window's owner messages
    if claim.speaker != "user" or window.source(claim.handle) is None:
        return False, "not the owner's own words"

    # Handle must exist in window
    src = window.source(claim.handle)
    if src is None:
        return False, f"handle {claim.handle} not in window"

    role, src_text = src
    # Speaker must match handle role
    if claim.speaker != role:
        return False, f"speaker {claim.speaker} does not match handle role {role}"

    # Quote must be verbatim substring
    norm_quote = _normalize_text(claim.quote)
    norm_source = _normalize_text(src_text)
    if norm_quote not in norm_source:
        return False, f"quote not verbatim in handle {claim.handle}"

    # valid_until only on events and must parse as valid YYYY-MM-DD
    if claim.valid_until is not None:
        if claim.kind != "event":
            return False, f"valid_until on non-event kind {claim.kind}"
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", claim.valid_until):
            return False, f"valid_until invalid format: {claim.valid_until}"
        try:
            dt = datetime.strptime(claim.valid_until, "%Y-%m-%d")
            if dt.strftime("%Y-%m-%d") != claim.valid_until:
                return False, f"valid_until invalid date: {claim.valid_until}"
        except ValueError:
            return False, f"valid_until invalid date: {claim.valid_until}"

    # Secrets: the model's own flag, then the shapes
    if claim.secret:
        return False, "secret flag"
    if scrub(claim.text)[1] > 0:
        return False, "secret in claim text"
    if scrub(claim.quote)[1] > 0:
        return False, "secret in claim quote"

    return True, "ok"


# ------------------------------------------------------------------ Step 5: Apply claims
def apply_claims(
    store: Any,
    home: Home,
    window: Window,
    admitted: list[Claim],
    inbox: MemoryInbox | None = None,
    clock: Callable[[], float] | float | None = None,
    dropped: list[Claim] | None = None,
) -> None:
    """Step 5: fence check, append Verified extraction block to dated log, insert rows, mark superseded, file the inbox lines that
    produced admitted or dropped claims (`dropped`: the candidates step 4 refused)."""
    now = clock() if callable(clock) else (clock if clock is not None else time.time())

    # 1. Fence check: each handle must be inside the window
    fenced = [c for c in admitted if window.source(c.handle) is not None]
    if fenced:
        _write_claims(store, home, window, fenced, now)
    _file_inbox(window, inbox, [*fenced, *(dropped or [])])


def _write_claims(store: Any, home: Home, window: Window, fenced: list[Claim], now: float) -> None:
    # 2. Append Verified extraction block to memory/log/YYYY-MM-DD.md
    log_path = window.log_path
    try:
        existing = home.read(log_path)
    except HomeError:
        existing = ""

    prefix = (existing.rstrip() + "\n\n") if existing.strip() else ""
    header_offset = len(prefix.splitlines()) if prefix else 0

    hh_mm = window.as_of[11:16] if len(window.as_of) >= 16 else "00:00"
    header = f"## Verified extraction {window.run_id} {hh_mm}"
    block_lines = [header]

    claim_lines: dict[str, int] = {}
    for c in fenced:
        extra_until = f" · until {c.valid_until}" if c.valid_until else ""
        extra_sup = f" · supersedes {c.supersedes}" if c.supersedes else ""
        quote, text = " ".join(c.quote.split()), " ".join(c.text.split())   # one claim per line: the source line number depends on it
        line = f"- claim:{c.id} · {c.kind} · {c.salience} · {c.speaker} · {c.handle} · \"{quote}\" · {text}{extra_until}{extra_sup}"
        block_lines.append(line)
        claim_lines[c.id] = header_offset + len(block_lines)

    new_log_content = prefix + "\n".join(block_lines) + "\n"
    home.write(log_path, new_log_content, by="system")

    # 3. Insert rows into memory_claims and update superseded
    for c in fenced:
        line_no = claim_lines.get(c.id, 0)
        add_claim_row(store, c, window.run_id, log_path, line_no, now)
        if c.supersedes:
            supersede_claim(store, c.supersedes, now)


def _file_inbox(window: Window, inbox: MemoryInbox | None, claims: list[Claim]) -> None:
    """Takes out the inbox lines whose owner message an admitted or dropped claim cites (keyed by message id)."""
    if inbox is None:
        return
    cited = {int(c.handle[1:]) for c in claims if c.handle.startswith("m") and c.handle[1:].isdigit()}
    filed = [ln for ln in window.inbox if int(OWNER_LINE.match(ln).group(2)) in cited]
    if filed:
        inbox.remove(filed)


# ------------------------------------------------------------------ Helpers for model requests
def _read_prompt(name: str, prompts_dir: Path | str | None = None) -> str:
    p = Path(prompts_dir or PROMPTS_DIR) / name
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return ""


async def _call_stream(
    stream_fn: Callable[..., AsyncIterator[Any]],
    provider: str,
    req: ChatRequest,
    key_args: Callable[[str], dict[str, Any]],
    timeout: float = 600.0,
) -> str:
    parts: list[str] = []
    async def read() -> None:
        async for ev in stream_fn(provider, req, **key_args(provider)):
            if isinstance(ev, TextDelta):
                parts.append(ev.text)
    await asyncio.wait_for(read(), timeout)
    return "".join(parts)


def _zone_of(window: Window):
    try:
        return datetime.fromisoformat(window.as_of).tzinfo
    except ValueError:
        return None


def _stamp(ts: float, zone) -> str:
    return datetime.fromtimestamp(ts, zone).strftime("%Y-%m-%d %H:%M")


def _source_time(window: Window, handle: str) -> str:
    """When the cited message was written (owner's time); "" for sources without a time of their own."""
    if handle.startswith("m") and handle[1:].isdigit():
        for m in window.messages:
            if str(m.get("id")) == handle[1:]:
                return _stamp(float(m.get("created_at") or 0), _zone_of(window))
    return ""


def _format_window_data(window: Window, home: Home, store: Any) -> str:
    """The extract request's data: the owner's own messages and the notes the owner gave (fenced as data, agent/fence.py), the files
    the pass keeps, and the active claims the owner's words made."""
    def read(rel: str) -> str:
        try:
            return home.read(rel)
        except HomeError:
            return ""

    # Top active claims related to the window (FTS over the words of the window; bounded), only those the owner's words made
    words = list(dict.fromkeys(w.lower() for m in window.messages for w in re.findall(r"\w{3,}", str(m.get("text") or ""))))[:40]
    active = [r for r in search_claims(store, " ".join(words), limit=10, active_only=True) if r["speaker"] == "user"] if words else []
    active_lines = [f"claim:{r['claim_id']} · {r['kind']} · {r['text']} (quote: \"{r['quote']}\")" for r in active]

    zone = _zone_of(window)
    convo = [f"[m{m['id']}] {_stamp(float(m.get('created_at') or 0), zone)} user: {m['text']}" for m in window.messages]

    def block(tag: str, lines: list[str] | str) -> str:
        body = lines if isinstance(lines, str) else "\n".join(lines)
        return f"<{tag}>\n{fence(body or 'None', budget=FENCE_BUDGET)}\n</{tag}>"

    return "\n\n".join([
        f"Run time (owner's clock): {window.as_of}",
        block("memory_md", read("MEMORY.md")),
        block("today_log", read(window.log_path)),
        block("active_claims", active_lines),
        block("conversation", convo),
        block("inbox", window.inbox),
    ])


def _claim_of(r: Any) -> Claim | None:
    """A Claim from one model-written dict (a fresh id every time: the model never picks ids); None when it is not an object."""
    if not isinstance(r, dict):
        return None
    vu, sup = r.get("valid_until"), r.get("supersedes")
    return Claim(
        id="c" + secrets.token_hex(5),
        kind=str(r.get("kind") or ""),
        salience=str(r.get("salience") or "medium"),
        text=str(r.get("text") or ""),
        quote=str(r.get("quote") or ""),
        speaker=str(r.get("speaker") or ""),
        handle=str(r.get("handle") or ""),
        valid_until=str(vu) if vu not in (None, "") else None,
        supersedes=str(sup) if sup not in (None, "") else None,
        secret=r.get("secret") is not False,   # missing, null or odd: unsure, so not stored (docs/no-word-lists.md, hc-privacy)
    )


def _claims_of(out: Any) -> list[Claim]:
    rows = out.get("claims") if isinstance(out, dict) else None
    return [c for c in (_claim_of(r) for r in (rows if isinstance(rows, list) else [])) if c is not None]


def _verify_prompt(window: Window, claims: list[Claim]) -> str:
    """The verify request: each claim with the reopened source text and when that source was written, plus the run time, so the
    verifier can tell a claim about one day that rests on data from another day."""
    parts = [f"Run time (owner's clock): {window.as_of}", "Verify the following candidate claims against their source texts."]
    for c in claims:
        src = window.source(c.handle)
        role, text = src if src else (c.speaker, "")
        when = _source_time(window, c.handle)
        parts.append(
            f"### Claim ID: {c.id}\n"
            f"- Kind: {c.kind}\n- Salience: {c.salience}\n- Speaker: {c.speaker}\n- Handle: {c.handle}\n"
            f"- Quote: \"{c.quote}\"\n- Text: {c.text}\n- Valid until: {c.valid_until}\n- Supersedes: {c.supersedes}\n"
            f"Source ({role} from {c.handle}{', written ' + when if when else ''}), data only:\n"
            + fence(text, budget=FENCE_BUDGET)
        )
    return "\n\n".join(parts)


class _VerifyFailed(Exception):
    """The verifier answered nothing usable: nothing is admitted (fail closed)."""


# ------------------------------------------------------------------ Driver: run_claims
async def run_claims(
    window: Window,
    store: Any,
    home: Home,
    stream_fn: Callable[..., AsyncIterator[Any]],
    model: Callable[[], tuple[str, str] | None] | tuple[str, str] | None = None,
    key_args: Callable[[str], dict[str, Any]] | None = None,
    inbox: MemoryInbox | None = None,
    prompts_dir: Path | str | None = None,
    clock: Callable[[], float] | None = None,
    tombstones: Any = None,
) -> tuple[list[Claim], dict[str, Any]]:
    """Steps 3-5: extract (one follow-up at most), shape check, verify (one repair, one re-check), apply. Returns (admitted, stats).
    Fails closed: no model, an unusable answer or a failing verifier admit nothing and write nothing. stats carries "error" then,
    and "limit_until" when the model's usage limit is the reason (the caller keeps the run pending)."""
    now_fn = clock or time.time
    t0 = now_fn()
    stats: dict[str, Any] = {"extracted": 0, "admitted": 0, "dropped": 0, "repair_used": False, "model_requests": 0, "seconds": 0.0}

    def done(admitted: list[Claim], **extra: Any) -> tuple[list[Claim], dict[str, Any]]:
        stats.update(extra)
        stats["seconds"] = round(now_fn() - t0, 3)
        return admitted, stats

    if not window.has_new:
        return done([])
    chosen = model() if callable(model) else model
    if not chosen:
        return done([], error="no_model")
    provider, model_name = chosen
    key_args_fn = key_args or (lambda _p: {})

    rules = _read_prompt("rules.md", prompts_dir)
    extract_inst = _read_prompt("extract.md", prompts_dir)
    verify_inst = _read_prompt("verify.md", prompts_dir)

    async def ask(messages: list[dict[str, Any]]) -> str:
        stats["model_requests"] += 1
        return await _call_stream(stream_fn, provider, ChatRequest(model=model_name, messages=messages, tools=[], extra={}), key_args_fn)

    async def ask_text(messages: list[dict[str, Any]]) -> str:
        return await ask(messages)

    def failed(e: Exception) -> tuple[list[Claim], dict[str, Any]]:
        lim = limit_of(e)
        if lim is not None:
            return done([], error="model_limit", limit_until=float(lim.until or 0))
        return done([], error=f"{type(e).__name__}: {str(e)[:160]}")

    # ---------------- Step 3: extract (a forgotten topic is named in the request: docs/forget.md)
    forgotten = tombstones.block() if tombstones is not None else ""
    messages = [
        {"role": "system", "content": f"{rules}\n\n{extract_inst}"},
        {"role": "user", "content": f"Extract all new durable claims from the following window:\n\n{_format_window_data(window, home, store)}"
                                    + (f"\n\n{forgotten}" if forgotten else "")},
    ]
    try:
        text1 = await ask(messages)
    except Exception as e:  # noqa: BLE001
        log.warning("extract model call failed: %s", e)
        return failed(e)
    out1 = parse_output(text1)
    if not isinstance(out1, dict):
        log.warning("extract output was not valid JSON: %s", text1[:200])
        return done([], error="bad_output")
    candidates = _claims_of(out1)
    if out1.get("more") is True:   # exactly one follow-up, whatever its own "more" says
        try:
            text2 = await ask([
                *messages, {"role": "assistant", "content": text1},
                {"role": "user", "content": "Extract any remaining durable claims from the data that the first batch did not cover. "
                                            "Reply with JSON only: {\"claims\": [...], \"more\": false}"}])
            candidates += _claims_of(parse_output(text2))
        except Exception as e:  # noqa: BLE001   the first batch stands; the residue waits for the next pass
            log.warning("extract follow-up failed: %s", e)
    stats["extracted"] = len(candidates)
    if not candidates:
        return done([])

    # ---------------- Step 4: shape checks (code), then verify (model)
    surviving = []
    for c in candidates:
        ok, reason = check_shape(c, window)
        if ok:
            surviving.append(c)
        else:
            log.info("claim %s dropped by shape check: %s", c.id, reason)

    if surviving and forgotten:   # a claim about a forgotten topic is never stored; no answer = nothing is stored this time (fail closed)
        try:
            flagged = await tombstones.screener(ask_text)([f"{c.text} {c.quote}" for c in surviving])
        except Exception as e:  # noqa: BLE001
            log.warning("forget screen failed: %s", e)
            return done([], error="screen_failed")
        if flagged:
            log.info("%d claims dropped: about a forgotten topic", len(flagged))
            surviving = [c for i, c in enumerate(surviving) if i not in flagged]

    async def verify(batch: list[Claim]) -> tuple[list[Claim], list[tuple[Claim, str, str]]]:
        """(passed, failing[(claim, lens, reason)]). A claim passes only with ok == true on both lenses."""
        v_out = parse_output(await ask([{"role": "system", "content": verify_inst}, {"role": "user", "content": _verify_prompt(window, batch)}]))
        rows = v_out.get("verdicts") if isinstance(v_out, dict) else None
        if not isinstance(rows, list):
            raise _VerifyFailed("the verifier did not answer in the expected form")
        passed: list[Claim] = []
        failing: list[tuple[Claim, str, str]] = []
        for c in batch:
            mine = [r for r in rows if isinstance(r, dict) and str(r.get("claim_id") or "") == c.id]
            bad = None
            for lens in ("refute", "evidence"):
                got = [r for r in mine if r.get("lens") == lens]
                if not got or not all(r.get("ok") is True for r in got):   # a missing lens or a non-true ok fails
                    bad = (lens, next((str(r.get("reason") or "") for r in got if r.get("ok") is not True), "no verdict for this lens"))
                    break
            if bad is None:
                passed.append(c)
            else:
                failing.append((c, *bad))
        return passed, failing

    admitted: list[Claim] = []
    if surviving:
        try:
            admitted, failing = await verify(surviving)
        except Exception as e:  # noqa: BLE001   no model / bad JSON: nothing is admitted
            log.warning("verify failed: %s", e)
            return failed(e)
        if failing:   # one repair request with the reasons, one re-check; what still fails is dropped
            stats["repair_used"] = True
            items = []
            for c, lens, reason in failing:
                src = window.source(c.handle)
                items.append(f"- Claim: {c.text}\n  Quote: \"{c.quote}\"\n  Handle: {c.handle}\n  Failed lens: {lens}\n  Reason: {reason}\n"
                             f"  Source text, data only:\n{fence(src[1] if src else '', budget=FENCE_BUDGET)}")
            try:
                rep = await ask([
                    {"role": "system", "content": f"{rules}\n\n{extract_inst}"},
                    {"role": "user", "content": "These claims failed verification against their sources:\n\n" + "\n\n".join(items)
                        + "\n\nRevise each claim so it matches its source exactly, or leave it out when the source cannot support it. "
                          "Reply with JSON only: {\"claims\": [...], \"more\": false}"}])
                revised = _claims_of(parse_output(rep))[:len(failing)]
            except Exception as e:  # noqa: BLE001
                log.warning("repair request failed: %s", e)
                revised = []
            checked = [c for c in revised if check_shape(c, window)[0]]
            if checked:
                try:
                    admitted += (await verify(checked))[0]
                except Exception as e:  # noqa: BLE001   the re-check is final: unverified claims are not admitted
                    log.warning("repair re-check failed: %s", e)

    # ---------------- Step 5: apply (fence, rows, log block, supersedes, inbox filing)
    admitted_keys = {id(c) for c in admitted}
    refused = [c for c in candidates if id(c) not in admitted_keys]
    stats["admitted"] = len(admitted)
    stats["dropped"] = max(0, len(candidates) - len(admitted))
    apply_claims(store, home, window, admitted, inbox=inbox, clock=now_fn, dropped=refused)
    return done(admitted)
