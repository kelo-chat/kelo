"""Retention for everything the server writes that only grows with time (docs/disk-growth.md has the table of every place).

One class, `DiskLimits`, run once a night (server.py) and once at start-up. It removes only records and files that nobody asked to keep:
bookkeeping about finished work, rebuildable caches, old step pictures. It never touches the owner's own things: chat messages, memory
claims, notes, uploads, the library, journal, goals, payment history (wallet), the vault, cards and settings.

Table rules are plain SQL with an age or a row count, oldest first; each names the table so a module that was never started (its
table does not exist) is skipped. Rules decide by dates, states and links between tables, never by words in the text.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DAY = 86400.0
FOLLOW_READING_DAYS = 30            # one check's readings (what a flight or a parcel said at that minute)
FOLLOW_CHECK_DAYS = 30              # a finished or stopped check, counted from the end of its window
FOLLOW_ITEM_DAYS = 60               # an upcoming item (trip, appointment) counted from its end
FOLLOW_USAGE_MONTHS = 24
FOLLOW_REMOVED_DAYS = 60             # an item the owner removed from the list, counted from its start (the calendar never lists a past event)
MEMORY_RUNS_KEEP = 500              # newest passes of the memory pipeline (the claims themselves are never pruned)
RECHECK_DAYS = 180                  # a closed / stopped / sent recheck
CORRECTION_DAYS = 365               # a resolved correction of the nightly review
REVIEW_DAY_KEEP_DAYS = 400
BUY_PLAN_DAYS = 180                 # remembered checkout steps not used for this long
PAYMENT_PATH_DAYS = 365
BROWSER_HISTORY_CAP = 1024**3       # all step pictures together; the oldest tab goes first (each tab is also capped by browser_history.py)
BROWSER_HISTORY_BUSY = 3600.0       # a tab written this recently may still be running
VACUUM_MIN_FREE = 64 * 1024**2      # give pages back to the disk only when this much of the database file is empty ...
VACUUM_MIN_SHARE = 0.30             # ... and it is at least this share of the file
VACUUM_EVERY = 7 * DAY


@dataclass(frozen=True)
class Rule:
    """`sql` may use :cut (epoch seconds, now - days) and :cut_month ("YYYY-MM") when `days` is set, and :keep when `keep` is set."""
    name: str
    table: str
    sql: str
    days: float | None = None
    keep: int | None = None


RULES: tuple[Rule, ...] = (
    # follow-through (follow/store.py): foreign keys are off in this database, so the children are removed by hand
    Rule("follow readings", "follow_readings", "DELETE FROM follow_readings WHERE at < :cut", days=FOLLOW_READING_DAYS),
    Rule("follow checks of finished work", "follow_checks",
         "DELETE FROM follow_checks WHERE state != 'active' AND window_end < :cut", days=FOLLOW_CHECK_DAYS),
    Rule("follow items long past", "follow_items",
         "DELETE FROM follow_items WHERE COALESCE(ends_at, starts_at) < :cut", days=FOLLOW_ITEM_DAYS),
    Rule("follow checks of removed items", "follow_checks",
         "DELETE FROM follow_checks WHERE item_key NOT IN (SELECT key FROM follow_items)"),
    Rule("follow readings of removed checks", "follow_readings",
         "DELETE FROM follow_readings WHERE check_id NOT IN (SELECT id FROM follow_checks)"),
    Rule("follow usage months", "follow_usage", "DELETE FROM follow_usage WHERE month < :cut_month", days=FOLLOW_USAGE_MONTHS * 30.5),
    Rule("follow removed items", "follow_removed", "DELETE FROM follow_removed WHERE starts_at < :cut", days=FOLLOW_REMOVED_DAYS),
    # memory pipeline: the run log only (memory_claims are the owner's memory and stay)
    Rule("memory pass log", "memory_runs",
         "DELETE FROM memory_runs WHERE run_id NOT IN (SELECT run_id FROM memory_runs ORDER BY started_at DESC LIMIT :keep)",
         keep=MEMORY_RUNS_KEEP),
    Rule("rechecks done", "rechecks",
         "DELETE FROM rechecks WHERE state IN ('closed', 'stopped', 'sent') AND MAX(created, COALESCE(last_checked, 0)) < :cut",
         days=RECHECK_DAYS),
    Rule("resolved corrections", "kept_corrections",
         "DELETE FROM kept_corrections WHERE status != 'open' AND COALESCE(resolved_at, last_seen_at) < :cut", days=CORRECTION_DAYS),
    Rule("daily review rows", "review_days", "DELETE FROM review_days WHERE created_at < :cut", days=REVIEW_DAY_KEEP_DAYS),
    # payments: finished records only; anything pressed or unknown stays until it is settled
    Rule("remembered checkout steps not used", "buy_plans",
         "DELETE FROM buy_plans WHERE COALESCE(last_ok_at, created_at) < :cut", days=BUY_PLAN_DAYS),
    Rule("buy-ready rows of removed watchers", "buy_ready",
         "DELETE FROM buy_ready WHERE watcher_id NOT IN (SELECT id FROM watchers)"),
    Rule("payment ways not seen", "payment_paths", "DELETE FROM payment_paths WHERE updated_at < :cut", days=PAYMENT_PATH_DAYS),
    # rows that hang on a chat message or a task that is gone
    Rule("question lists of removed messages", "message_replies",
         "DELETE FROM message_replies WHERE message_id NOT IN (SELECT id FROM messages)"),
    Rule("comparison cards of removed messages", "message_comparisons",
         "DELETE FROM message_comparisons WHERE message_id NOT IN (SELECT id FROM messages)"),
    Rule("resume offers of removed tasks", "resume_offers", "DELETE FROM resume_offers WHERE task_id NOT IN (SELECT id FROM tasks)"),
)


class DiskLimits:
    def __init__(self, store: Any, *, history_root: str | Path | None = None, history_cap: int = BROWSER_HISTORY_CAP,
                 clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        self.history_root = Path(history_root) if history_root else None
        self.history_cap = history_cap
        self._vacuumed = 0.0

    # ------------------------------------------------------------------ tables
    def prune_tables(self) -> dict[str, int]:
        """Run every rule. Returns {rule name: rows removed} for the rules that removed something. A missing table is skipped."""
        now = self.clock()
        out: dict[str, int] = {}
        for rule in RULES:
            params: dict[str, Any] = {}
            if rule.days is not None:
                params["cut"] = now - rule.days * DAY
                params["cut_month"] = time.strftime("%Y-%m", time.gmtime(now - rule.days * DAY))
            if rule.keep is not None:
                params["keep"] = rule.keep
            try:
                n = self.store._run(rule.sql, params).rowcount   # named parameters: those a rule does not use are ignored
            except sqlite3.OperationalError as e:
                if "no such table" in str(e) or "no such column" in str(e):
                    continue
                raise
            if n and n > 0:
                out[rule.name] = n
        return out

    def reclaim(self, *, force: bool = False) -> bool:
        """Give the empty pages of the database file back to the disk (SQLite keeps them otherwise). Only when a lot of the file is
        empty and not more than once a week; the write-ahead log is folded in first. Returns whether it ran."""
        now = self.clock()
        if not force and now - self._vacuumed < VACUUM_EVERY:
            return False
        db = self.store._db
        with self.store._lock:
            page = db.execute("PRAGMA page_size").fetchone()[0]
            total = db.execute("PRAGMA page_count").fetchone()[0]
            free = db.execute("PRAGMA freelist_count").fetchone()[0]
            if not force and (free * page < VACUUM_MIN_FREE or free < VACUUM_MIN_SHARE * max(1, total)):
                return False
            self._vacuumed = now
            db.commit()
            try:
                db.execute("VACUUM")
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:    # a full disk or a busy file: try again next week
                return False
        return True

    # ------------------------------------------------------------------ files
    def prune_browser_history(self) -> int:
        """Step pictures of unattended runs: over the cap, whole tabs go, oldest first, never one written in the last hour.
        (Per-tab and per-age limits are in browser_history.py.) Returns the folders removed."""
        root = self.history_root
        if root is None or not root.is_dir():
            return 0
        from .browser_history import ActionArchive
        from .storage_cleanup import _newest, _size    # the same sizing as the cleanup screen
        now = self.clock()
        removed = ActionArchive(root, clock=self.clock).purge()    # steps older than its KEEP_DAYS, also when no run has written lately
        tabs = []
        for d in root.iterdir():
            if d.is_symlink() or not d.is_dir():
                continue
            tabs.append((_newest(d), d, _size(d)))
        tabs.sort(key=lambda t: t[0])
        total = sum(t[2] for t in tabs)
        for when, d, size in tabs:
            if total <= self.history_cap:
                break
            if now - when < BROWSER_HISTORY_BUSY:
                continue
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            removed += 1
        return removed

    # ------------------------------------------------------------------ all
    def run(self) -> dict[str, Any]:
        """Everything above, each part on its own so one failure does not stop the others."""
        result: dict[str, Any] = {}
        for name, step in (("tables", self.prune_tables), ("browser_history", self.prune_browser_history), ("reclaimed", self.reclaim)):
            try:
                result[name] = step()
            except Exception as e:   # noqa: BLE001 - a retention job must never take the server down
                result[name] = f"failed: {type(e).__name__}"
        return result
