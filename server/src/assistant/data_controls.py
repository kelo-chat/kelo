"""Your data (My > Your data): take it with you, bring memories from another assistant, start over.

EXPORT. One ZIP in the results library (files/, so the existing download of a result serves it, GET /api/files/library/{name}/file):
  README.txt, messages.jsonl (the whole conversation), memory/ (MEMORY.md, user.md, assistant.md, AGENTS.md, the dated logs, people pages,
  the inbox), memory/claims.jsonl (what is remembered and why), schedules.json, follows.json, connectors.json (what is connected, by
  name and state only), profile.json (name, language, model choice, tone, proactive settings).
  NEVER in it: tokens, passwords, API keys, vault items (their names, labels and hosts included), device tokens, the master key, the
  database file. connectors.json is a whitelist of three fields; nothing is copied from the vault or the credentials table.
  The ZIP is built in a background thread (one at a time); GET shows the state and, when done, the library name to download.

START OVER (reset). After a typed confirmation word the server first saves the same ZIP as `kelo-backup-before-reset-<time>.zip` in the
results library (when that fails nothing is cleared), then clears: the conversation and what came of it (chat_clear.py, the stop of
running work is the API's part), the memory files (the four core files go back to their templates, memory/ with the logs, people
pages and inbox, the memory pass backups and the older versions kept of those files), the claims and memory runs, the "never
again" topics of forget, follows (items, checks, readings), schedules and their runs. KEPT: connectors, the vault, models, pairing,
files, goals, rules, usage. With "sign out of connectors" every connection in the vault is removed too (as the app's disconnect does).
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import threading
import time
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .deliverables import Deliverables, LibraryFileError
from .home import CORE, SIDE_FILES, TEMPLATES, Home, HomeError

log = logging.getLogger(__name__)

EXPORT_PREFIX = "kelo-data-export-"
BACKUP_PREFIX = "kelo-backup-before-reset-"
CONFIRM_WORD = "reset"           # what the app asks the owner to type before a reset (compared without case or spaces around it)
MEMORY_FILES = (*CORE, *SIDE_FILES)
HIDDEN_COPIES = (".memory-passes",)   # memory pass backups: whole copies of the memory files
CONNECTOR_FIELDS = ("id", "name", "status")   # the only things of a connector that go into the export

README = """Your Kelo data
Made {when} (UTC).

messages.jsonl      the conversation, one message per line
memory/             what Kelo remembers: MEMORY.md, user.md, assistant.md, AGENTS.md, dated logs, people pages
memory/claims.jsonl the facts behind that memory, with the quote and the source of each
schedules.json      your schedules (and the archived ones)
follows.json        what Kelo follows for you (trips, parcels, flights)
connectors.json     which services are connected (names and state only)
profile.json        your name, language, model choice and proactive settings

Not in this file, on purpose: passwords, tokens, API keys and anything else kept in the vault.
"""


def stamp(clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(clock(), UTC).strftime("%Y%m%d-%H%M%S")


class DataError(Exception):
    """Something the owner can be told: the message is safe to show."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class DataControls:
    def __init__(self, store: Any, home: Home | None, deliverables: Deliverables | None, *, schedules: Any = None,
                 connectors: Callable[[], list[dict[str, Any]]] = lambda: [], proactive: Callable[[], dict[str, Any]] = lambda: {},
                 clock: Callable[[], float] = time.time, sign_out: Callable[[str], None] | None = None) -> None:
        self.store, self.home, self.deliverables = store, home, deliverables
        self.schedules, self.connectors, self.proactive, self.clock = schedules, connectors, proactive, clock
        self.sign_out_one = sign_out      # called once per vault item before it is deleted (the API drops what hangs on it)
        self._lock = threading.Lock()
        self._job: dict[str, Any] = {}    # the running or last export of this process

    # ------------------------------------------------------------------ what goes into the ZIP
    def _rows(self, sql: str) -> list[dict[str, Any]]:
        try:
            return [dict(r) for r in self.store._all(sql)]
        except Exception:  # noqa: BLE001 - a table this server does not have
            return []

    def _memory_files(self) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        if self.home is None:
            return out
        root = self.home.root
        for name in MEMORY_FILES:
            p = root / name
            if p.is_file() and not p.is_symlink():
                out[f"memory-files/{name}"] = p.read_bytes()
        mem = root / "memory"
        if mem.is_dir() and not mem.is_symlink():
            for p in sorted(mem.rglob("*")):
                if p.is_file() and not p.is_symlink() and not any(part.startswith(".") for part in p.relative_to(root).parts):
                    out[f"memory-files/{p.relative_to(root).as_posix()}"] = p.read_bytes()
        return out

    def _connectors(self) -> list[dict[str, Any]]:
        try:
            rows = self.connectors()
        except Exception:  # noqa: BLE001
            log.exception("export: the connector list failed")
            rows = []
        return [{k: r.get(k) for k in CONNECTOR_FIELDS} for r in rows if isinstance(r, dict) and r.get("status") == "connected"]

    def _schedules(self) -> dict[str, Any]:
        if self.schedules is None:
            return {"schedules": [], "archived": []}
        return {"schedules": self.schedules.list(), "archived": self.schedules.archived()}

    @staticmethod
    def _jsonl(rows: list[dict[str, Any]]) -> bytes:
        return "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows).encode("utf-8")

    def build_zip(self) -> tuple[bytes, dict[str, int]]:
        """The export as bytes and what it holds (counts per part)."""
        now = datetime.fromtimestamp(self.clock(), UTC)
        messages = self._rows("SELECT id, role, content, created_at, via FROM messages ORDER BY id")
        for m in messages:
            m["at"] = datetime.fromtimestamp(m["created_at"], UTC).isoformat(timespec="seconds")
        claims = self._rows("SELECT * FROM memory_claims ORDER BY created_at")
        follows = self._rows("SELECT * FROM follow_items ORDER BY starts_at")
        files = self._memory_files()
        sched = self._schedules()
        connectors = self._connectors()
        profile = {"profile": self.store.profile(), "proactive": self.proactive() or {}}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", README.format(when=now.strftime("%Y-%m-%d %H:%M")))
            z.writestr("messages.jsonl", self._jsonl(messages))
            for name, data in files.items():
                z.writestr(name, data)
            z.writestr("memory-files/memory/claims.jsonl", self._jsonl(claims))
            z.writestr("schedules.json", json.dumps(sched, ensure_ascii=False, indent=1, default=str))
            z.writestr("follows.json", json.dumps(follows, ensure_ascii=False, indent=1, default=str))
            z.writestr("connectors.json", json.dumps(connectors, ensure_ascii=False, indent=1))
            z.writestr("profile.json", json.dumps(profile, ensure_ascii=False, indent=1, default=str))
        counts = {"messages": len(messages), "memory_files": len(files), "claims": len(claims),
                  "schedules": len(sched["schedules"]) + len(sched["archived"]), "follows": len(follows), "connectors": len(connectors)}
        return buf.getvalue(), counts

    def save_zip(self, prefix: str) -> tuple[str, int, dict[str, int]]:
        """Build the ZIP and keep it in the results library: (name, bytes, counts). DataError when it cannot be kept."""
        if self.deliverables is None:
            raise DataError("this server has no place to keep files", 503)
        data, counts = self.build_zip()
        try:
            name = self.deliverables.save(f"{prefix}{stamp(self.clock)}.zip", data)
        except (LibraryFileError, HomeError) as e:
            raise DataError(str(e), 507) from e
        return name, len(data), counts

    # ------------------------------------------------------------------ export job
    def _state(self) -> dict[str, Any]:
        """The running or last export; after a restart the newest ZIP in the library stands for it."""
        if self._job:
            return dict(self._job)
        if self.deliverables is not None:
            rows = [r for r in self.deliverables.list() if r["name"].startswith(EXPORT_PREFIX) and r["name"].endswith(".zip")]
            if rows:
                r = rows[0]
                return {"state": "done", "file": r["name"], "bytes": r["bytes"], "finished_at": r["created"], "counts": {}}
        return {"state": "idle"}

    def export_status(self) -> dict[str, Any]:
        s = self._state()
        out: dict[str, Any] = {"state": s.get("state", "idle")}
        for k in ("started_at", "finished_at", "bytes", "counts", "error"):
            if s.get(k) not in (None, ""):
                out[k] = s[k]
        if s.get("file"):
            out["file"] = s["file"]
            out["download"] = f"/api/files/library/{s['file']}/file"
        return out

    def start_export(self) -> dict[str, Any]:
        """Start the background export (one at a time: a second call while one runs only reports it)."""
        if self.deliverables is None:
            raise DataError("this server has no place to keep files", 503)
        with self._lock:
            if self._job.get("state") == "running":
                return self.export_status()
            self._job = {"state": "running", "started_at": self.clock()}
        threading.Thread(target=self._run_export, name="data-export", daemon=True).start()
        return self.export_status()

    def _run_export(self) -> None:
        try:
            name, size, counts = self.save_zip(EXPORT_PREFIX)
            done = {"state": "done", "file": name, "bytes": size, "counts": counts, "finished_at": self.clock()}
        except DataError as e:
            done = {"state": "failed", "error": str(e), "finished_at": self.clock()}
        except Exception:  # noqa: BLE001 - never leave the job "running"
            log.exception("data export failed")
            done = {"state": "failed", "error": "the export could not be made", "finished_at": self.clock()}
        with self._lock:
            self._job = {**self._job, **done}

    # ------------------------------------------------------------------ start over
    @staticmethod
    def confirmed(word: Any) -> bool:
        return isinstance(word, str) and word.strip().casefold() == CONFIRM_WORD

    def backup_before_reset(self) -> dict[str, Any]:
        name, size, counts = self.save_zip(BACKUP_PREFIX)
        return {"name": name, "path": f"files/{name}", "bytes": size, "counts": counts}

    def reset_stores(self, *, sign_out: bool = False) -> dict[str, Any]:
        """The data part of a start over (the conversation is cleared by the API before this). Returns what was removed."""
        out: dict[str, Any] = {}
        out["memory_claims"] = self._delete("memory_claims")
        out["memory_runs"] = self._delete("memory_runs")
        out["forget"] = self._delete("forget_tombstones") + self._delete("forget_plans")
        out["follows"] = self._delete("follow_readings") + self._delete("follow_checks") + self._delete("follow_items")
        out["schedules"] = self._delete("schedules") + self._delete("schedule_runs")
        out["memory_files"] = self._reset_files()
        if sign_out:
            out["signed_out"] = self._sign_out()
        return out

    def _delete(self, table: str) -> int:
        with self.store._lock:
            have = self.store._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
            if not have:
                return 0
            n = self.store._db.execute(f"DELETE FROM {table}").rowcount   # noqa: S608 - a table name of this module
            self.store._db.commit()
            return n

    def _reset_files(self) -> int:
        if self.home is None:
            return 0
        root, n = self.home.root, 0
        for name in CORE:
            self.home.write(name, TEMPLATES[name], by="system")
            n += 1
        # CHECKLIST.md and NOTIFY_PREFS.md are what the owner told Kelo to do or not to do: they stay
        mem = root / "memory"
        if mem.is_dir() and not mem.is_symlink():
            n += sum(1 for p in mem.rglob("*") if p.is_file())
            shutil.rmtree(mem, ignore_errors=True)
        mem.mkdir(exist_ok=True, mode=0o700)
        for d in HIDDEN_COPIES:
            shutil.rmtree(root / d, ignore_errors=True)
        hist = root / ".history"
        if hist.is_dir():   # older versions kept of the files that were just emptied
            for p in hist.iterdir():
                if p.name in CORE or p.name == "memory" or p.name.startswith("memory__"):
                    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        return n

    def _sign_out(self) -> int:
        n = 0
        for c in self.store.credentials():
            name = c["name"]
            if self.sign_out_one is not None:
                self.sign_out_one(name)
            if self.store.delete_credential(name):
                n += 1
        return n


__all__ = ["BACKUP_PREFIX", "CONFIRM_WORD", "DataControls", "DataError", "EXPORT_PREFIX"]
