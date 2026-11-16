"""`assistant eval export`: the owner's recent replies written down as golden threads for the replay tests (server/tests/evals/golden).

What is written, per reply: the user's message, the tool calls (name and arguments), the tool results cut to 2 KB, the approval cards and
the text of the reply. Nothing else (no profile, no keys, no file contents beyond a tool's own result).

Redaction happens before anything is written: every value stored in the vault (its secret and user name, read with the server's own key),
the known key and token shapes and card numbers the secret guard knows, e-mail addresses, phone numbers, card-number-like digit runs (Luhn
checked) and street addresses (Korean road names and English) become `[redacted:<kind>]`. After the files are written, every file is read
back and scanned with the same rules; one leak deletes everything this run wrote and the export fails (exit 1).

The live database is never written: it is copied with SQLite's backup API (read-only source) into a private temporary folder, the copy's
key file is copied with it, and the copy is removed at the end. Turn events are kept for 24 hours (turn_log.KEEP_HOURS), so an older
reply is not in the export even when --days is larger.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import secret_guard
from .store import Store

ARG_CUT = 2048
VALUE_MIN = 4                       # shorter vault values would match ordinary words; they are not written as "vault" redactions
TURN_RECORD_TYPES = ("tool_started", "tool_result", "approval_request", "text")
USER_LAG_SECONDS = 5.0              # a user message is the one stored at or just before the reply started

# ---- patterns ---------------------------------------------------------------------------------------------------------------------
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\d-])(?:\+\d{1,3}[ -]?)?0?1[016789][ -.]?\d{3,4}[ -.]?\d{4}(?![\d-])"          # Korean mobile
                   r"|(?<![\d-])0\d{1,2}[ -.]\d{3,4}[ -.]\d{4}(?![\d-])"                                  # 02-123-4567 style
                   r"|\+\d{1,3}[ -]\d{2,4}[ -]\d{3,4}[ -]\d{3,4}(?![\d-])")                              # +1 555 123 4567 style
CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
ADDRESS = re.compile(r"[가-힣0-9]{1,12}(?:로|길)\s?\d{1,4}(?:-\d{1,4})?(?![0-9])"
                     r"|\d{1,5}\s+(?:[A-Z][A-Za-z]+\s+){1,3}(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Lane|Ln|Drive|Dr)\b\.?")


class ExportError(RuntimeError):
    """The export cannot be made safely (the vault cannot be read, or a leak was found); nothing is left behind."""


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0 and len(digits) >= 13


def _cards(text: str) -> list[re.Match[str]]:
    return [m for m in CARD.finditer(text) if luhn_ok(re.sub(r"\D", "", m.group(0)))]


def _spans(text: str, values: list[str]) -> list[tuple[int, int, str]]:
    """Every place a redaction applies to, as (start, end, kind)."""
    spans: list[tuple[int, int, str]] = []
    for v in values:
        for m in re.finditer(re.escape(v), text):
            spans.append((m.start(), m.end(), "vault"))
    for h in secret_guard.find(text):
        spans.append((h.start, h.end, h.kind))
    for kind, pattern in (("email", EMAIL), ("phone", PHONE), ("address", ADDRESS)):
        spans += [(m.start(), m.end(), kind) for m in pattern.finditer(text)]
    spans += [(m.start(), m.end(), "card") for m in _cards(text)]
    return spans


def _apply(text: str, spans: list[tuple[int, int, str]]) -> tuple[str, dict[str, int]]:
    """Replaces the spans (overlaps: the earliest and longest wins), counts each kind."""
    counts: dict[str, int] = {}
    picked: list[tuple[int, int, str]] = []
    for start, end, kind in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if picked and start < picked[-1][1]:
            continue
        picked.append((start, end, kind))
    out, pos = [], 0
    for start, end, kind in picked:
        out.append(text[pos:start])
        out.append(f"[redacted:{kind}]")
        counts[kind] = counts.get(kind, 0) + 1
        pos = end
    out.append(text[pos:])
    return "".join(out), counts


def redact(text: str, vault_values: list[str]) -> tuple[str, dict[str, int]]:
    values = sorted({v for v in vault_values if len(v) >= VALUE_MIN}, key=len, reverse=True)
    return _apply(text, _spans(text, values))


def leaks(text: str, vault_values: list[str]) -> list[str]:
    """Kinds of sensitive text still in `text` (empty when clean). Used on the written files."""
    values = [v for v in vault_values if len(v) >= VALUE_MIN]
    found = sorted({kind for _, _, kind in _spans(text, values)})
    return found


def redact_tree(node: Any, vault_values: list[str], counts: dict[str, int]) -> Any:
    if isinstance(node, str):
        new, c = redact(node, vault_values)
        for k, n in c.items():
            counts[k] = counts.get(k, 0) + n
        return new
    if isinstance(node, list):
        return [redact_tree(x, vault_values, counts) for x in node]
    if isinstance(node, dict):
        return {k: redact_tree(v, vault_values, counts) for k, v in node.items()}
    return node


# ---- reading ----------------------------------------------------------------------------------------------------------------------
def vault_values(store: Store) -> list[str]:
    """The secrets and user names of every login and key in the vault (read with the server's key). Raises when one cannot be read."""
    values: list[str] = []
    for item in store.credentials():
        full = store.credential(item["name"])
        if full is None:
            continue
        for field_name in ("secret", "username"):
            v = full.get(field_name)
            if isinstance(v, str) and len(v) >= VALUE_MIN:
                values.append(v)
    if store.credentials() and not values:
        raise ExportError("the vault is there but no value could be read; export stopped (nothing written)")
    return values


def copy_database(src: Path, dst_dir: Path) -> Path:
    """A private copy of the database (and its key file) to read from; the source is opened read-only."""
    dst = dst_dir / "copy.db"
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    key = src.with_name("master.key")
    if key.is_file():
        shutil.copy2(key, dst_dir / "master.key")
    return dst


def threads(store: Store, days: float, now: float) -> list[dict[str, Any]]:
    """One record per reply that started in the window: its user message, its events and its text."""
    since = now - days * 86400
    if not store._all("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'turn_log'"):
        raise ExportError("this database has no reply log (turn_log); is it the assistant's database?")
    rows = store._all("SELECT turn_id, status, started_at, ended_at FROM turn_log WHERE started_at >= ? ORDER BY started_at", (since,))
    user_rows = store._all("SELECT id, content, created_at FROM messages WHERE role = 'user' ORDER BY created_at, id")
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        events = store._all("SELECT ev FROM turn_events WHERE turn_id = ? ORDER BY seq", (row["turn_id"],))
        user = None
        for m in user_rows:
            if m["id"] not in used and m["created_at"] <= row["started_at"] + USER_LAG_SECONDS:
                user = m
        if user is not None:
            used.add(user["id"])
        record_events, reply = [], []
        for e in events:
            ev = json.loads(e["ev"])
            kind = ev.get("type")
            if kind == "text":
                reply.append(ev.get("text", ""))
            elif kind == "tool_started":
                record_events.append({"type": kind, "name": ev.get("name", ""), "arguments": ev.get("arguments", {})})
            elif kind == "tool_result":
                record_events.append({"type": kind, "name": ev.get("name", ""), "is_error": bool(ev.get("is_error")),
                                      "output": str(ev.get("output", ""))[:ARG_CUT]})
            elif kind == "approval_request":
                record_events.append({"type": kind, "name": ev.get("name", ""), "arguments": ev.get("arguments", {})})
        out.append({"format": 1, "turn_id": row["turn_id"], "status": row["status"],
                    "day": time.strftime("%Y-%m-%d", time.localtime(row["started_at"])),
                    "user": user["content"] if user else "", "events": record_events, "reply": "".join(reply)})
    return out


# ---- the export -------------------------------------------------------------------------------------------------------------------
@dataclass
class Report:
    written: list[Path] = field(default_factory=list)
    redactions: dict[str, int] = field(default_factory=dict)
    leaks: list[str] = field(default_factory=list)      # what the scan found (non-empty = failed, nothing kept)

    @property
    def ok(self) -> bool:
        return not self.leaks


def export(db_path: Path, out_dir: Path, days: float = 7, *, secret_key: str | None = None, now: float | None = None) -> Report:
    now = time.time() if now is None else now
    report = Report()
    work = Path(tempfile.mkdtemp(prefix="kelo-eval-"))
    try:
        copy = copy_database(db_path, work)
        store = Store(copy, secret_key or None)
        values = vault_values(store)
        records = threads(store, days, now)
        out_dir.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        for rec in records:
            clean = redact_tree(rec, values, counts)
            for e in clean["events"]:   # the cut is made again after redaction: a placeholder can be longer than the text it replaces
                if "output" in e:
                    e["output"] = e["output"][:ARG_CUT]
            name = re.sub(r"[^A-Za-z0-9_-]", "", clean["turn_id"]) or "turn"
            path = out_dir / f"{name}.json"
            path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report.written.append(path)
        report.redactions = counts
        for path in report.written:   # the scan reads what is on disk now
            found = leaks(path.read_text(encoding="utf-8"), values)
            if found:
                report.leaks += [f"{path.name}: {k}" for k in found]
        if report.leaks:
            for path in report.written:
                path.unlink(missing_ok=True)
            report.written = []
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report


def env_for(env_file: Path) -> dict[str, str]:
    """KEY=VALUE lines of an env file (no shell expansion; quotes removed)."""
    out: dict[str, str] = {}
    if not env_file.is_file():
        return out
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def db_of(env: dict[str, str]) -> tuple[Path, str]:
    home = Path(env.get("ASSISTANT_HOME") or Path.home() / ".assistant")
    return home / "assistant.db", env.get("ASSISTANT_SECRET_KEY", "")
