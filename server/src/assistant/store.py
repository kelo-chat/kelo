"""SQLite state for the single-user app: profile, conversation, goals, usage.

One file (``$ASSISTANT_HOME/assistant.db``, default ``~/.assistant``). Calls are short and
synchronous; a lock serializes them so one connection can be shared with the event loop.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .keyguard import CARDS_INFO, VAULT_INFO, KeyGuard, Locked, is_system_key, load_server_key
from .keyring_client import PLACEHOLDER
from .i18n import t
from .calls import CALL_MARK
from .vault import KEYCHAIN, LOG_KEEP, Sealer, VaultError, aad_for, mask_username

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'tracking',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS api_keys (provider TEXT PRIMARY KEY, key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credentials (
    name TEXT PRIMARY KEY, label TEXT NOT NULL, hosts TEXT NOT NULL, placement TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '', secret TEXT, created_at REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'api', auto_allow INTEGER NOT NULL DEFAULT 0, last_used REAL,
    last_result TEXT, last_result_at REAL, last_result_detail TEXT NOT NULL DEFAULT '',
    keyring INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vault_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, item TEXT NOT NULL, action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', ok INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL, last_seen REAL, owner INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pairings (code_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL, owner INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS access_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, code_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL,
    expires_at REAL NOT NULL, max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0, revoked_at REAL);
CREATE TABLE IF NOT EXISTS conversation_summary (
    id INTEGER PRIMARY KEY CHECK (id = 1), summary TEXT NOT NULL DEFAULT '', up_to_message_id INTEGER NOT NULL DEFAULT 0,
    updated_at REAL, messages_folded INTEGER NOT NULL DEFAULT 0, compactions INTEGER NOT NULL DEFAULT 0,
    memory_flushes INTEGER NOT NULL DEFAULT 0, last_memory_flush_at REAL);
CREATE TABLE IF NOT EXISTS usage (
    day TEXT PRIMARY KEY, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, task_id INTEGER,
    provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS usage_events_day ON usage_events(day);
CREATE TABLE IF NOT EXISTS learned_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, rule TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '', date TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5, status TEXT NOT NULL DEFAULT 'proposed', updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS notices_read (id TEXT PRIMARY KEY, read_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memory_claims (
    claim_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, salience TEXT NOT NULL,
    text TEXT NOT NULL, quote TEXT NOT NULL, speaker TEXT NOT NULL, handle TEXT NOT NULL,
    supersedes TEXT, status TEXT NOT NULL DEFAULT 'active', valid_until TEXT,
    source_path TEXT NOT NULL, source_line INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS memory_claims_status ON memory_claims(status);
CREATE INDEX IF NOT EXISTS memory_claims_handle ON memory_claims(handle);
CREATE INDEX IF NOT EXISTS memory_claims_run ON memory_claims(run_id);
CREATE TABLE IF NOT EXISTS memory_runs (
    run_id TEXT PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL,
    trigger TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '', stats TEXT NOT NULL DEFAULT '{}');
CREATE VIRTUAL TABLE IF NOT EXISTS memory_claims_fts USING fts5(
    text, quote, content='memory_claims', content_rowid='rowid', tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS memory_claims_ai AFTER INSERT ON memory_claims BEGIN
    INSERT INTO memory_claims_fts(rowid, text, quote) VALUES (new.rowid, new.text, new.quote);
END;
CREATE TRIGGER IF NOT EXISTS memory_claims_ad AFTER DELETE ON memory_claims BEGIN
    INSERT INTO memory_claims_fts(memory_claims_fts, rowid, text, quote) VALUES('delete', old.rowid, old.text, old.quote);
END;
CREATE TRIGGER IF NOT EXISTS memory_claims_au AFTER UPDATE ON memory_claims BEGIN
    INSERT INTO memory_claims_fts(memory_claims_fts, rowid, text, quote) VALUES('delete', old.rowid, old.text, old.quote);
    INSERT INTO memory_claims_fts(rowid, text, quote) VALUES (new.rowid, new.text, new.quote);
END;
CREATE TABLE IF NOT EXISTS trail_labels (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, source_id TEXT NOT NULL, party TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '', private INTEGER NOT NULL DEFAULT 1, outside INTEGER NOT NULL DEFAULT 0, at REAL NOT NULL,
    runs BLOB NOT NULL, tokens TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS trail_labels_run ON trail_labels(run_id);
CREATE TABLE IF NOT EXISTS recipient_allows (
    id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT NOT NULL, kind TEXT NOT NULL, created_at REAL NOT NULL, approval_id TEXT NOT NULL DEFAULT '',
    UNIQUE(recipient, kind));
CREATE TABLE IF NOT EXISTS order_terms (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, task TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, merchant_origin TEXT NOT NULL,
    merchant_name TEXT NOT NULL DEFAULT '', items TEXT NOT NULL, obligations TEXT NOT NULL, currency TEXT NOT NULL,
    total_minor INTEGER NOT NULL, conditional_minor INTEGER NOT NULL DEFAULT 0, conditional_terms TEXT NOT NULL DEFAULT '',
    payment_label TEXT NOT NULL DEFAULT '', terms_digest TEXT NOT NULL, checked_at REAL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS order_terms_by_owner ON order_terms(owner, task);
CREATE TABLE IF NOT EXISTS pay_approvals (
    id TEXT PRIMARY KEY, terms_id TEXT NOT NULL, owner TEXT NOT NULL, task TEXT NOT NULL DEFAULT '', merchant_origin TEXT NOT NULL,
    approved_total_minor INTEGER NOT NULL, approved_conditional_minor INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL,
    terms_digest TEXT NOT NULL, source TEXT NOT NULL, approval_id TEXT, signed INTEGER NOT NULL DEFAULT 0, expires_at REAL NOT NULL,
    state TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS pay_approvals_by_owner ON pay_approvals(owner, task);
CREATE TABLE IF NOT EXISTS pay_attempts (
    id TEXT PRIMARY KEY, approval_id TEXT NOT NULL, terms_id TEXT NOT NULL, owner TEXT NOT NULL, task TEXT NOT NULL DEFAULT '',
    merchant_origin TEXT NOT NULL, state TEXT NOT NULL, pressed_at REAL, evidence TEXT NOT NULL DEFAULT '',
    receipt_digest TEXT NOT NULL DEFAULT '', receipt_text TEXT NOT NULL DEFAULT '', check_jobs TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS pay_attempts_by_owner ON pay_attempts(owner, task, state);
CREATE TABLE IF NOT EXISTS forget_tombstones (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, terms TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL, removed TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS forget_plans (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, terms TEXT NOT NULL DEFAULT '[]', items TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS order_preapprovals (
    id TEXT PRIMARY KEY, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, spec TEXT NOT NULL, max_total_minor INTEGER NOT NULL,
    currency TEXT NOT NULL, expires_at REAL NOT NULL, state TEXT NOT NULL, approval_id TEXT NOT NULL DEFAULT '',
    matched_digest TEXT NOT NULL DEFAULT '', matched_origin TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS order_preapprovals_by_owner ON order_preapprovals(owner_kind, owner_id, state);
CREATE TABLE IF NOT EXISTS schedule_pay_allows (
    id TEXT PRIMARY KEY, schedule_id INTEGER NOT NULL, merchant_origin TEXT NOT NULL, payment_label TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL, approval_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
    UNIQUE(schedule_id, merchant_origin, payment_label, currency));
CREATE TABLE IF NOT EXISTS browser_sign_ins (
    profile TEXT NOT NULL DEFAULT '', site TEXT NOT NULL, how TEXT NOT NULL, signed_at REAL NOT NULL, last_used REAL,
    PRIMARY KEY(profile, site));
CREATE TABLE IF NOT EXISTS browser_profiles (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS browser_profile_units (
    profile TEXT NOT NULL, site TEXT NOT NULL, unit TEXT NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY(profile, site));
"""

PROFILE_KEYS = ("user_name", "agent_name", "provider", "model", "fallback_provider", "fallback_model", "effort", "timezone",
                "background_provider", "background_model", "tone")
# what an unset key reads as: "tone" is how Kelo talks to the user (prompt.TONES), polite unless the owner picked friendly
PROFILE_DEFAULTS = {"tone": "polite"}
GOAL_STATUSES = ("tracking", "done")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _load_key(path: str | Path, secret: str | None) -> bytes:
    """The server-mode master key (kept for callers of the old helper; KeyGuard owns it now)."""
    return load_server_key(None if str(path) == ":memory:" else Path(path).with_name("master.key"), secret)


# Full-text search over the conversation (chat_search.py): an FTS5 index on messages.content with the trigram tokenizer, so Korean and
# other substrings match. External content: the index holds no second copy of the words. Three triggers keep it in step with every
# insert, edit and delete (the chat clear deletes the rows, so the words leave the index with them; secure_delete overwrites them).
SEARCH_INDEX = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""
SEARCH_BACKFILL_BATCH = 500


def schema_fingerprint(db: sqlite3.Connection) -> str:
    """A short hash of every table, index and trigger definition. The schema is upgraded in place at start-up (CREATE ... IF NOT
    EXISTS plus ALTER TABLE ADD COLUMN when a column is missing, here and in the modules that own a table), so there is no version
    number: a different fingerprint means a newer server changed the database. `assistant update` compares it before and after an
    update (the same formula runs inside the container in updates.py) to know whether a rollback must also restore the database."""
    rows = db.execute("SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
                      "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    return hashlib.sha256("\n".join("\t".join(str(c) for c in r) for r in rows).encode()).hexdigest()[:16]


CALL_IDS_META = "messages:call_id_v1"


def backfill_call_ids(db: sqlite3.Connection) -> bool:
    """Once per database (meta `messages:call_id_v1`): call messages stored before messages.call_id get one id per call, so the apps
    show each call as one card. A call is a run of via="call" rows; its hidden start message (calls.CALL_MARK) starts a new call and any
    other row ends it. The id is "legacy-<first row id>". Returns True when it ran."""
    if db.execute("SELECT 1 FROM meta WHERE key = ?", (CALL_IDS_META,)).fetchone():
        return False
    first = db.execute("SELECT MIN(id) FROM messages WHERE via = 'call' AND call_id = ''").fetchone()[0]
    updates: list[tuple[str, int]] = []
    if first is not None:
        current = ""
        for rid, role, content, via, cid in db.execute(
                "SELECT id, role, content, via, call_id FROM messages WHERE id >= ? ORDER BY id", (first,)).fetchall():
            if via != "call":
                current = ""
                continue
            if cid:
                current = cid
                continue
            if not current or (role == "user" and str(content).startswith(CALL_MARK)):
                current = f"legacy-{rid}"
            updates.append((current, rid))
    db.executemany("UPDATE messages SET call_id = ? WHERE id = ?", updates)
    db.execute("INSERT INTO meta(key, value) VALUES(?, ?)", (CALL_IDS_META, "1"))
    db.commit()
    return True


class Store:
    def __init__(self, path: str | Path = ":memory:", secret_key: str | None = None, keyguard: KeyGuard | None = None):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if str(path) != ":memory:":  # create 0600 up front: it holds the conversation
            os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o600))
        # every secret's key comes from the KeyGuard (keyguard.py): master.key / $ASSISTANT_SECRET_KEY, or the phones
        self.keyguard = keyguard or KeyGuard(Path(path).parent if str(path) != ":memory:" else None, secret_key)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA secure_delete = ON")   # a deleted card or login is overwritten in the file, not just unlinked
            index_was_there = self._db.execute("SELECT 1 FROM sqlite_master WHERE name = 'messages_fts'").fetchone() is not None
            self._db.executescript(SCHEMA)
            have = {r[1] for r in self._db.execute("PRAGMA table_info(credentials)")}
            for col, ddl in (("kind", "TEXT NOT NULL DEFAULT 'api'"), ("auto_allow", "INTEGER NOT NULL DEFAULT 0"), ("last_used", "REAL"),
                             ("last_result", "TEXT"), ("last_result_at", "REAL"), ("last_result_detail", "TEXT NOT NULL DEFAULT ''"),
                             ("keyring", "INTEGER NOT NULL DEFAULT 0")):
                if col not in have:             # a database made before the vault existed
                    self._db.execute(f"ALTER TABLE credentials ADD COLUMN {col} {ddl}")
            for table in ("devices", "pairings"):   # old column, kept so an older server can open this database; nothing reads it any more
                if "owner" not in {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN owner INTEGER NOT NULL DEFAULT 0")
            if "code_id" not in {r[1] for r in self._db.execute("PRAGMA table_info(devices)")}:   # before multi-use access codes
                self._db.execute("ALTER TABLE devices ADD COLUMN code_id INTEGER")
            if "e2e_only" not in {r[1] for r in self._db.execute("PRAGMA table_info(devices)")}:   # before KE2E
                self._db.execute("ALTER TABLE devices ADD COLUMN e2e_only INTEGER NOT NULL DEFAULT 0")
            if "approval_key" not in {r[1] for r in self._db.execute("PRAGMA table_info(devices)")}:   # before signed payment approvals
                self._db.execute("ALTER TABLE devices ADD COLUMN approval_key TEXT NOT NULL DEFAULT ''")
            have = {r[1] for r in self._db.execute("PRAGMA table_info(messages)")}
            for col in ("reaction", "reaction_hold", "reaction_next", "via", "author", "steps", "call_id", "once_key"):   # reactions on the owner's messages (reactions.py); via = the messenger a message came from; author = owner/forwarded/tapped (owner_messages.py); steps = the tool steps of an assistant reply, for the model only (reply_text.py); call_id = the in-app call a message belongs to (calls.py), "" for everything else
                if col not in have:
                    self._db.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            # a visible delivery's message is stored once per delivery key (deliveries.py): the same key never makes a second message
            self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS messages_once ON messages(once_key) WHERE once_key != ''")
            have = {r[1] for r in self._db.execute("PRAGMA table_info(usage)")}
            for col in ("cache_read_tokens", "cache_write_tokens"):   # a database made before the cached share was counted: rows kept
                if col not in have:
                    self._db.execute(f"ALTER TABLE usage ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
            self._db.executescript(SEARCH_INDEX)   # the triggers keep the index in step from now on
            if not index_was_there:               # a database made before the search existed: index what is already there, once
                self._backfill_search_index()
            from .owner_messages import backfill_authors, retract_unowned_claims   # local: the markers' modules import the store
            backfill_authors(self._db)            # a database made before messages.author: the owner's own rows, once (owner_messages.py)
            retract_unowned_claims(self._db)      # claims that are not the owner's own words stop being active, once (rows kept)
            backfill_call_ids(self._db)   # call messages stored before call_id: one id per call, once
            self._db.commit()
        self._usage_writes = 0
        self.credential_watchers: list[Callable[[str, bool], None]] = []
        self.credential_listeners: list[Callable[[str, str | None], None]] = []   # (name, result) of each set_credential_result
        self.prune_usage_events()

    # The sealers, asked of the KeyGuard at each use: while it is locked (phone mode after a restart) these raise keyguard.Locked.
    @property
    def _fernet(self) -> Fernet:          # provider API keys (older format, still read)
        return self.keyguard.fernet()

    def _fernet_for(self, name: str) -> Fernet:
        """AI providers' keys are under Keyguard; Kelo's own working keys ("push:install_id", ...) are not (keyguard.is_system_key)."""
        return self.keyguard.system_fernet() if is_system_key(name) else self.keyguard.fernet()

    @property
    def _sealer(self) -> Sealer:          # the vault: AES-256-GCM bound to where each value may be used
        return self.keyguard.sealer(VAULT_INFO)

    @property
    def card_sealer(self) -> Sealer:      # the wallet's auto-pay cards: a key of their own
        return self.keyguard.sealer(CARDS_INFO)

    @property
    def data_sealer(self) -> Sealer:      # personal data kept for the agent (not under Keyguard: readable while locked)
        return self.keyguard.data_sealer()

    def _backfill_search_index(self) -> None:
        """Index every stored message, in batches (called once at start-up, inside __init__'s lock; the search is a substring index)."""
        last = 0
        while True:
            rows = self._db.execute("SELECT id, content FROM messages WHERE id > ? ORDER BY id LIMIT ?",
                                    (last, SEARCH_BACKFILL_BATCH)).fetchall()
            if not rows:
                break
            self._db.executemany("INSERT INTO messages_fts(rowid, content) VALUES (?, ?)", [(r[0], r[1]) for r in rows])
            last = rows[-1][0]
            self._db.commit()

    def _run(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def schema_info(self) -> dict[str, Any]:
        """For GET /api/about: the database opens, its schema fingerprint and how many tables it has (migrations run in __init__,
        so a store that answers has finished them)."""
        with self._lock:
            tables = self._db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchone()[0]
            return {"ok": True, "schema": schema_fingerprint(self._db), "tables": int(tables), "migrations": "done"}

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # ---- profile
    def profile(self) -> dict[str, str]:
        rows = self._all("SELECT key, value FROM profile")
        out = {k: "" for k in PROFILE_KEYS}
        out.update({r["key"]: r["value"] for r in rows if r["key"] in PROFILE_KEYS})
        return {k: v or PROFILE_DEFAULTS.get(k, "") for k, v in out.items()}

    def set_profile(self, **values: str) -> dict[str, str]:
        for k, v in values.items():
            if k in PROFILE_KEYS and v is not None:
                self._run("INSERT INTO profile(key, value) VALUES(?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v.strip()))
        return self.profile()

    # ---- conversation (one continuous chat)
    def add_message(self, role: str, content: str, via: str = "", author: str = "", steps: str = "", call_id: str = "",
                    once_key: str = "") -> int:
        """`via`: the messenger the message came through ("telegram"); "" for the app. `author`: owner, forwarded or tapped for the
        owner's own user messages (owner_messages.py); "" for everything else, hidden handoff rows included. `steps`: the reply's
        tool steps in one line (reply_text.py), read by the model only; the apps never get it. `call_id`: the in-app call these words
        belong to (the call id the app sent, calls.py); "" otherwise."""
        if once_key:   # a delivery's message: stored once, the existing one is given back when the same key comes again
            with self._lock:
                got = self._db.execute("SELECT id FROM messages WHERE once_key = ?", (once_key,)).fetchone()
                if got is not None:
                    return int(got[0])
                cur = self._db.execute("INSERT INTO messages(role, content, created_at, via, author, steps, call_id, once_key) "
                                       "VALUES(?,?,?,?,?,?,?,?)", (role, content, time.time(), via, author, steps, call_id, once_key))
                self._db.commit()
                return cur.lastrowid or 0
        return self._run("INSERT INTO messages(role, content, created_at, via, author, steps, call_id) VALUES(?,?,?,?,?,?,?)",
                         (role, content, time.time(), via, author, steps, call_id)).lastrowid or 0

    def message_by_once_key(self, key: str) -> int | None:
        rows = self._all("SELECT id FROM messages WHERE once_key = ?", (key,))
        return int(rows[0]["id"]) if rows else None

    def message_count(self) -> int:
        """How many messages the conversation holds (the owner's and the assistant's)."""
        return int(self._all("SELECT COUNT(*) AS n FROM messages")[0]["n"])

    def messages(self, *, limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
        """Newest `limit` messages (before `before_id` if given), oldest first."""
        rows = self._all(
            "SELECT id, role, content, created_at, reaction, via, author, call_id FROM messages WHERE (? IS NULL OR id < ?) "
            "ORDER BY id DESC LIMIT ?", (before_id, before_id, limit))
        return rows[::-1]

    def messages_after(self, after_id: int, *, before_id: int | None = None, limit: int = 2000) -> list[dict[str, Any]]:
        """The newest `limit` messages with after_id < id (< before_id if given), oldest first."""
        rows = self._all("SELECT id, role, content, created_at, via, steps FROM messages WHERE id > ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                         (after_id, before_id, before_id, limit))
        return rows[::-1]

    def drop_unanswered_call_words(self, call_id: str) -> bool:
        """Deletes the newest message when it is the owner's words of call `call_id` that nothing answered yet (no row after it): the
        owner went on talking and the app sends those words again with the rest (POST /api/turns "amend", calls.py). True when one went."""
        with self._lock:
            row = self._db.execute("SELECT id, role, content, via, call_id FROM messages ORDER BY id DESC LIMIT 1").fetchone()
            if (row is None or row["role"] != "user" or row["via"] != "call" or row["call_id"] != call_id
                    or str(row["content"]).startswith(CALL_MARK)):
                return False
            self._db.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
            self._db.commit()
            return True

    def condensed_messages(self, *, before_id: int, after_id: int = 0, query: str = "", limit: int = 10) -> tuple[list[dict[str, Any]], bool]:
        """(the newest `limit` user/assistant messages with after_id < id < before_id whose text contains `query` (case-insensitive
        LIKE; all when empty), oldest first; whether older ones match too). The chat's messages only: no tool results, nothing else."""
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self._all("SELECT id, role, content, created_at FROM messages WHERE id > ? AND id < ? AND role IN ('user', 'assistant') "
                         "AND (? = '' OR content LIKE ? ESCAPE '\\') ORDER BY id DESC LIMIT ?",
                         (after_id, before_id, query, like, limit + 1))
        return rows[:limit][::-1], len(rows) > limit

    def first_messages(self, n: int, *, before_id: int | None = None, after_id: int = 0) -> list[dict[str, Any]]:
        """The first `n` messages of the conversation (since it was last cleared, or after `after_id`), oldest first."""
        return self._all("SELECT id, role, content, created_at, steps FROM messages WHERE id > ? AND (? IS NULL OR id < ?) ORDER BY id LIMIT ?",
                         (after_id, before_id, before_id, n))

    # ---- /new: the model's conversation starts again after this message id (the messages stay, the app still shows them)
    def session_start(self) -> int:
        try:
            return int(self.meta("session_start_id") or 0)
        except ValueError:
            return 0

    def start_session(self, after_id: int) -> None:
        """A new conversation for the model: nothing up to `after_id` goes into its context, and the old summary is forgotten."""
        self.set_meta("session_start_id", str(int(after_id)))
        self.clear_conversation_summary()

    def clear_messages(self) -> None:
        """Clearing the chat also forgets the summary of it."""
        self._run("DELETE FROM messages")
        self.clear_conversation_summary()
        self.set_meta("session_start_id", "0")

    # ---- the rolling summary of the conversation's older messages (compaction.py)
    def conversation_summary(self) -> dict[str, Any]:
        rows = self._all("SELECT * FROM conversation_summary WHERE id = 1")
        r = rows[0] if rows else {}
        return {"summary": r.get("summary") or "", "up_to_message_id": int(r.get("up_to_message_id") or 0), "updated_at": r.get("updated_at"),
                "messages_folded": int(r.get("messages_folded") or 0), "compactions": int(r.get("compactions") or 0),
                "memory_flushes": int(r.get("memory_flushes") or 0), "last_memory_flush_at": r.get("last_memory_flush_at")}

    def save_conversation_summary(self, summary: str, up_to_message_id: int, folded: int, *, expect_up_to: int) -> bool:
        """Replace the summary, unless it changed since `expect_up_to` was read or the chat was cleared meanwhile (False)."""
        with self._lock:
            row = self._db.execute("SELECT up_to_message_id FROM conversation_summary WHERE id = 1").fetchone()
            if (int(row[0]) if row else 0) != expect_up_to or not self._db.execute("SELECT 1 FROM messages WHERE id = ?", (up_to_message_id,)).fetchone():
                return False
            self._db.execute("INSERT INTO conversation_summary(id, summary, up_to_message_id, updated_at, messages_folded, compactions) "
                             "VALUES(1, ?, ?, ?, ?, 1) ON CONFLICT(id) DO UPDATE SET summary = excluded.summary, "
                             "up_to_message_id = excluded.up_to_message_id, updated_at = excluded.updated_at, "
                             "messages_folded = messages_folded + excluded.messages_folded, compactions = compactions + 1",
                             (summary, up_to_message_id, time.time(), folded))
            self._db.commit()
            return True

    def note_memory_flush(self) -> None:
        self._run("INSERT INTO conversation_summary(id, memory_flushes, last_memory_flush_at) VALUES(1, 1, ?) ON CONFLICT(id) DO UPDATE SET "
                  "memory_flushes = memory_flushes + 1, last_memory_flush_at = excluded.last_memory_flush_at", (time.time(),))

    def clear_conversation_summary(self) -> None:
        self._run("DELETE FROM conversation_summary")

    # ---- goals
    def goals(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM goals ORDER BY status = 'done', updated_at DESC, id DESC")

    def add_goal(self, title: str, category: str = "", note: str = "") -> dict[str, Any]:
        now = time.time()
        gid = self._run("INSERT INTO goals(title, category, note, created_at, updated_at) VALUES(?,?,?,?,?)",
                        (title.strip(), category.strip(), note.strip(), now, now)).lastrowid
        return self.goal(gid) or {}

    def goal(self, goal_id: int | None) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM goals WHERE id = ?", (goal_id,))
        return rows[0] if rows else None

    def update_goal(self, goal_id: int, **fields: str | None) -> dict[str, Any] | None:
        allowed = {k: v.strip() for k, v in fields.items() if v is not None and k in ("title", "category", "note", "status")}
        if allowed.get("status", "tracking") not in GOAL_STATUSES:
            raise ValueError(f"status must be one of {GOAL_STATUSES}")
        if allowed and self.goal(goal_id):
            sets = ", ".join(f"{k} = ?" for k in allowed)
            self._run(f"UPDATE goals SET {sets}, updated_at = ? WHERE id = ?", (*allowed.values(), time.time(), goal_id))
        return self.goal(goal_id)

    def delete_goal(self, goal_id: int) -> bool:
        return self._run("DELETE FROM goals WHERE id = ?", (goal_id,)).rowcount > 0

    # ---- provider API keys (entered in the app; encrypted at rest; never returned over the API)
    def set_key(self, provider: str, key: str) -> None:
        self._run("INSERT INTO api_keys(provider, key) VALUES(?, ?) ON CONFLICT(provider) DO UPDATE SET key=excluded.key",
                  (provider, self._fernet_for(provider).encrypt(key.encode()).decode()))

    def delete_key(self, provider: str) -> bool:
        return self._run("DELETE FROM api_keys WHERE provider = ?", (provider,)).rowcount > 0

    def key(self, provider: str) -> str | None:
        rows = self._all("SELECT key FROM api_keys WHERE provider = ?", (provider,))
        if not rows:
            return None
        try:
            return self._fernet_for(provider).decrypt(rows[0]["key"].encode()).decode()
        except InvalidToken:  # master key changed/lost: treat as not set so the user can re-enter it
            return None

    def key_providers(self) -> set[str]:
        return {r["provider"] for r in self._all("SELECT provider FROM api_keys")}

    # ---- the vault: API keys for services and website logins. Values are sealed (AES-256-GCM, bound to name/kind/hosts/placement);
    # nothing here, and nothing the app answers with, ever contains one.
    def save_credential(self, name: str, label: str, hosts: list[str], placement: dict[str, Any], endpoint: str = "", kind: str = "api") -> None:
        """Create or re-register. A changed host list or placement drops the stored value: it was approved for the old ones."""
        old = self._credential_row(name)
        if old and old["kind"] != kind:
            raise VaultError(f"{name!r} is already used by a {'website login' if old['kind'] == 'login' else 'service key'}; pick another name")
        same_hosts_placement = bool(old and json.loads(old["hosts"]) == hosts and json.loads(old["placement"]) == placement)
        keep = old["secret"] if same_hosts_placement else None
        keep_keyring = int(old["keyring"]) if same_hosts_placement and "keyring" in old.keys() else 0
        self._run("INSERT INTO credentials(name,label,hosts,placement,endpoint,secret,created_at,kind,keyring) VALUES(?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET label=excluded.label,hosts=excluded.hosts,placement=excluded.placement,"
                  "endpoint=excluded.endpoint,secret=excluded.secret,keyring=excluded.keyring,"
                  "created_at=CASE WHEN excluded.secret IS NULL AND excluded.keyring = 0 THEN excluded.created_at ELSE credentials.created_at END,"   # asked for again: a fresh request
                  "last_result=CASE WHEN excluded.secret IS NULL AND excluded.keyring = 0 THEN NULL ELSE credentials.last_result END",
                  (name, label, json.dumps(hosts), json.dumps(placement), endpoint, keep, time.time(), kind, keep_keyring))

    def _credential_row(self, name: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM credentials WHERE name = ?", (name,))
        return rows[0] if rows else None

    def _open(self, r: dict[str, Any]) -> str | None:
        """The plaintext stored for a row, or None (nothing stored, tampered with, or the master key changed).
        A value in the older Fernet format is read and sealed again in the new one on the spot."""
        if not r["secret"]:
            return None
        hosts, placement = json.loads(r["hosts"]), json.loads(r["placement"])
        aad = aad_for(r["name"], r["kind"], hosts, placement)
        plain = self._sealer.open(r["secret"], aad)
        if plain is None and not r["secret"].startswith("v2:"):
            try:
                plain = self._fernet.decrypt(r["secret"].encode()).decode()
            except InvalidToken:
                return None
            self._run("UPDATE credentials SET secret = ? WHERE name = ?", (self._sealer.seal(plain, aad), r["name"]))
        return plain

    def _public(self, r: dict[str, Any]) -> dict[str, Any]:
        keyring = bool(r["keyring"]) if "keyring" in r.keys() else False
        out = {"name": r["name"], "label": r["label"], "kind": r["kind"], "hosts": json.loads(r["hosts"]), "placement": json.loads(r["placement"]),
               "endpoint": r["endpoint"], "has_secret": bool(r["secret"]) or keyring, "keyring": keyring, "auto_allow": bool(r["auto_allow"]), "last_used": r["last_used"],
               "last_result": r["last_result"], "last_result_at": r["last_result_at"], "last_result_detail": r["last_result_detail"] or ""}
        if r["kind"] in ("login", "email") and r["secret"]:
            try:
                out["username_hint"] = mask_username(json.loads(self._open(r) or "{}").get("u", ""))
            except (ValueError, Locked):   # locked (Keyguard): the list still shows, without the hint
                out["username_hint"] = ""
        elif r["kind"] == "login":
            out["username_hint"] = self._asked_username(r["name"])   # no value yet: the account the assistant asked with (non-secret prefill), "" when none
        return out

    def _asked_username(self, name: str) -> str:
        """The `username` request_login was asked with for the newest open fill-in card of login `name`; "" when there is none."""
        try:
            rows = self._all("SELECT username_hint FROM secret_requests WHERE kind = 'login' AND name = ? AND state = 'open' "
                             "ORDER BY created_at DESC LIMIT 1", (name,))
        except sqlite3.Error:   # the table is made by SecretRequests; a store without it has no requests
            return ""
        return (rows[0]["username_hint"] if rows else "") or ""

    # Kelo's browser sign-ins: which sites a browser profile is signed in to. Sites only, never a cookie or a value (brief B1).
    def note_browser_sign_in(self, site: str, how: str, profile: str = "") -> None:
        """`how` is "login" (a saved login of the vault succeeded) or "owner" (the owner signed in by hand during a take-over)."""
        now = time.time()
        self._run("INSERT INTO browser_sign_ins(profile, site, how, signed_at, last_used) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT(profile, site) DO UPDATE SET how = excluded.how, signed_at = excluded.signed_at, "
                  "last_used = excluded.last_used", (profile, site, how, now, now))

    def touch_browser_site(self, site: str, profile: str = "") -> None:
        self._run("UPDATE browser_sign_ins SET last_used = ? WHERE profile = ? AND site = ?", (time.time(), profile, site))

    def browser_sign_ins(self, profile: str | None = None) -> list[dict[str, Any]]:
        """All rows (profile None), or the rows of one profile ("" = the default profile); each row carries its profile."""
        if profile is None:
            rows = self._all("SELECT * FROM browser_sign_ins ORDER BY profile, site")
        else:
            rows = self._all("SELECT * FROM browser_sign_ins WHERE profile = ? ORDER BY site", (profile,))
        return [{"profile": r["profile"], "site": r["site"], "how": r["how"], "signed_at": r["signed_at"], "last_used": r["last_used"]}
                for r in rows]

    def forget_browser_sign_in(self, site: str, profile: str = "") -> bool:
        return self._run("DELETE FROM browser_sign_ins WHERE profile = ? AND site = ?", (profile, site)).rowcount > 0

    # Named browser profiles: another account on the same site. A profile is a set of site units, each sealed with the vault's sealer
    # (the cookies and local storage of one site); the default profile is the owner's own browser and has no row here (brief C2a).
    def _unit_aad(self, profile: str, site: str) -> str:
        return f"browser-unit:{profile}:{site}"

    def _unit_unlocked(self) -> None:
        if self.vault_locked():
            raise VaultError(f"The user has locked the keychain, so a browser profile cannot be opened right now. Tell them they can unlock it in {t(KEYCHAIN)}.")

    def create_browser_profile(self, label: str) -> dict[str, Any]:
        label = (label or "").strip()
        if not 1 <= len(label) <= 40:
            raise ValueError("a profile name is 1 to 40 characters")
        if any(p["label"].lower() == label.lower() for p in self.browser_profiles()):
            raise ValueError("a profile with this name exists")
        row = {"id": "p_" + secrets.token_hex(4), "label": label, "created_at": time.time()}
        self._run("INSERT INTO browser_profiles(id, label, created_at) VALUES (?, ?, ?)", (row["id"], label, row["created_at"]))
        return row

    def browser_profiles(self) -> list[dict[str, Any]]:
        out = []
        for r in self._all("SELECT * FROM browser_profiles ORDER BY created_at, id"):
            sites = [{k: v[k] for k in ("site", "how", "signed_at", "last_used")} for v in self.browser_sign_ins(r["id"])]
            out.append({"id": r["id"], "label": r["label"], "created_at": r["created_at"], "sites": sites})
        return out

    def delete_browser_profile(self, profile_id: str) -> bool:
        self._run("DELETE FROM browser_profile_units WHERE profile = ?", (profile_id,))
        self._run("DELETE FROM browser_sign_ins WHERE profile = ?", (profile_id,))
        return self._run("DELETE FROM browser_profiles WHERE id = ?", (profile_id,)).rowcount > 0

    def check_browser_profile(self, profile: str) -> None:
        """A named profile can be opened: the keychain is not locked and the profile exists (VaultError says why not)."""
        self._unit_unlocked()
        if not self._all("SELECT 1 FROM browser_profiles WHERE id = ?", (profile,)):
            raise VaultError("There is no browser profile with this id: list them with browser_profiles.")

    def browser_unit(self, profile: str, site: str) -> dict[str, Any]:
        """The cookies and local storage this profile holds for a site; empty when none (or when the sealed row does not open)."""
        self._unit_unlocked()
        rows = self._all("SELECT unit FROM browser_profile_units WHERE profile = ? AND site = ?", (profile, site))
        plain = self._sealer.open(rows[0]["unit"], self._unit_aad(profile, site)) if rows else None
        if plain is None:
            return {"cookies": [], "storage": {}}
        try:
            unit = json.loads(plain)
        except ValueError:
            return {"cookies": [], "storage": {}}
        return {"cookies": list(unit.get("cookies") or []), "storage": dict(unit.get("storage") or {})}

    def save_browser_unit(self, profile: str, site: str, unit: dict[str, Any]) -> None:
        self._unit_unlocked()
        sealed = self._sealer.seal(json.dumps(unit, separators=(",", ":"), ensure_ascii=False), self._unit_aad(profile, site))
        self._run("INSERT INTO browser_profile_units(profile, site, unit, updated_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(profile, site) DO UPDATE SET unit = excluded.unit, updated_at = excluded.updated_at",
                  (profile, site, sealed, time.time()))

    def forget_browser_unit(self, profile: str, site: str) -> bool:
        self._run("DELETE FROM browser_sign_ins WHERE profile = ? AND site = ?", (profile, site))
        return self._run("DELETE FROM browser_profile_units WHERE profile = ? AND site = ?", (profile, site)).rowcount > 0

    def credentials(self) -> list[dict[str, Any]]:
        """Every item in the vault as the app may show it: what it is for and whether a value is stored, never the value."""
        return [self._public(r) for r in self._all("SELECT * FROM credentials ORDER BY created_at")]

    def credential(self, name: str) -> dict[str, Any] | None:
        """Internal use only: includes the opened value (None when not entered yet, altered, or the master key changed).
        For a website login `secret` is the password and `username` the user name."""
        r = self._credential_row(name)
        if not r:
            return None
        out = self._public(r)
        if bool(r["keyring"]) if "keyring" in r.keys() else False:
            _ = self._sealer
            plain = PLACEHOLDER if not self.vault_locked() else None
        else:
            plain = self._open(r)
        out["username"] = ""
        if r["kind"] == "login" and plain:
            try:
                data = json.loads(plain)
                out["username"], plain = str(data.get("u", "")), str(data.get("p", ""))
            except ValueError:
                plain = None
        out["secret"] = plain
        return out

    def credential_to_keyring(self, name: str, moved: str) -> bool:
        """The value `moved` is now in the key container: drop it here, unless the row holds another value by now (saved meanwhile)."""
        r = self._credential_row(name)
        if not r or not r["secret"] or self._open(r) != moved:
            return False
        return self._run("UPDATE credentials SET secret = NULL, keyring = 1 WHERE name = ? AND secret = ?", (name, r["secret"])).rowcount > 0

    def _seal_for(self, name: str, plain: str) -> str | None:
        r = self._credential_row(name)
        return self._sealer.seal(plain, aad_for(name, r["kind"], json.loads(r["hosts"]), json.loads(r["placement"]))) if r else None

    def _credential_changed(self, name: str, removed: bool) -> None:
        for fn in list(self.credential_watchers):   # e.g. a messenger channel starts, restarts or stops on its token (channels/manager.py)
            try:
                fn(name, removed)
            except Exception:  # noqa: BLE001 - a watcher never breaks the vault
                pass

    def set_credential_secret(self, name: str, secret: str) -> bool:
        sealed = self._seal_for(name, secret)
        # a new value has not been tried yet: what happened with the old one no longer applies
        done = sealed is not None and self._run("UPDATE credentials SET secret = ?, keyring = 0, last_result = NULL, last_result_at = NULL, last_result_detail = '' "
                                                "WHERE name = ?", (sealed, name)).rowcount > 0
        if done:
            self._credential_changed(name, False)
        return done

    def set_login(self, name: str, username: str, password: str) -> bool:
        sealed = self._seal_for(name, json.dumps({"u": username, "p": password}, ensure_ascii=False))
        return sealed is not None and self._run("UPDATE credentials SET secret = ?, last_result = NULL, last_result_at = NULL, last_result_detail = '' "
                                                "WHERE name = ? AND kind = 'login'", (sealed, name)).rowcount > 0

    def set_credential_result(self, name: str, result: str | None, detail: str = "") -> None:
        """What the last real use of the stored value showed: ok, failed (a sign-in the site rejected), rejected (a key the service refused),
        code_needed, blocked or unknown. None forgets it (for example when the user says "try again")."""
        self._run("UPDATE credentials SET last_result = ?, last_result_at = ?, last_result_detail = ? WHERE name = ?",
                  (result, time.time() if result else None, detail[:200] if result else "", name))
        for fn in list(getattr(self, "credential_listeners", [])):   # e.g. the login's fill-in card learns how the sign-in went
            try:
                fn(name, result)
            except Exception:  # noqa: BLE001 - a listener never breaks the record
                pass

    def set_auto_allow(self, name: str, allow: bool) -> bool:
        return self._run("UPDATE credentials SET auto_allow = ? WHERE name = ?", (int(allow), name)).rowcount > 0

    def set_credential_label(self, name: str, label: str) -> bool:
        return self._run("UPDATE credentials SET label = ? WHERE name = ?", (label, name)).rowcount > 0

    def touch_credential(self, name: str) -> None:
        self._run("UPDATE credentials SET last_used = ? WHERE name = ?", (time.time(), name))

    def delete_credential(self, name: str) -> bool:
        gone = self._run("DELETE FROM credentials WHERE name = ?", (name,)).rowcount > 0
        if gone:
            self._credential_changed(name, True)
        return gone

    # An entry that was set up (the assistant asked for a login, or the user started adding one) but never got a value is only a request,
    # not something in the vault. These helpers forget such requests; an entry that holds a value is never touched.
    def delete_draft(self, name: str) -> bool:
        return self._run("DELETE FROM credentials WHERE name = ? AND (secret IS NULL OR secret = '')", (name,)).rowcount > 0

    def purge_drafts(self, older_than: float, keep: Iterable[str] = ()) -> int:
        """Forget requests that were never answered and were made before `older_than`, except the names in `keep`."""
        held = set(keep)
        gone = [r["name"] for r in self._all("SELECT name FROM credentials WHERE (secret IS NULL OR secret = '') AND created_at < ?", (older_than,))
                if r["name"] not in held]
        for name in gone:
            self.delete_draft(name)
        return len(gone)

    # small settings that are not secret (for example a public OAuth client id)
    def meta(self, key: str) -> str:
        rows = self._all("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else ""

    def meta_prefix(self, prefix: str) -> dict[str, str]:
        """Every meta row whose key starts with `prefix`, as {key without the prefix: value}."""
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return {r["key"][len(prefix):]: r["value"] for r in self._all("SELECT key, value FROM meta WHERE key LIKE ? ESCAPE '\\'", (like,))}

    def set_meta(self, key: str, value: str) -> None:
        if value:
            self._run("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        else:
            self._run("DELETE FROM meta WHERE key = ?", (key,))

    # -------------------------------------------------- notices read state (notices.py)
    def notices_read_ids(self) -> set[str]:
        """The ids of notices this user has already read."""
        return {r["id"] for r in self._all("SELECT id FROM notices_read")}

    def set_notice_read(self, nid: str) -> None:
        """Mark a notice as read (idempotent)."""
        self._run("INSERT INTO notices_read(id, read_at) VALUES(?, ?) ON CONFLICT(id) DO NOTHING", (nid, time.time()))

    # the lock: while it is on, the agent can use nothing in the vault
    def vault_locked(self) -> bool:
        rows = self._all("SELECT value FROM meta WHERE key = 'vault_locked'")
        return bool(rows) and rows[0]["value"] == "1"

    def set_vault_locked(self, locked: bool) -> None:
        self._run("INSERT INTO meta(key, value) VALUES('vault_locked', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("1" if locked else "0",))

    # "Use codes from my mail automatically": during a sign-in the agent started, a code that site mailed is typed without asking
    # (tools/browser_more.py auto_code). On unless the user turned it off.
    def mail_codes_auto(self) -> bool:
        return self.meta("mail_codes_auto") != "0"

    def set_mail_codes_auto(self, on: bool) -> None:
        self.set_meta("mail_codes_auto", "1" if on else "0")

    # the log of everything that touches the vault. Only the app can read it; the agent has no tool for it, and nothing deletes single entries.
    def add_vault_log(self, item: str, action: str, detail: str = "", ok: bool = True) -> None:
        self._run("INSERT INTO vault_log(ts, item, action, detail, ok) VALUES(?,?,?,?,?)", (time.time(), item, action, detail[:300], int(ok)))
        self._run("DELETE FROM vault_log WHERE id <= (SELECT MAX(id) FROM vault_log) - ?", (LOG_KEEP,))

    def vault_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return [{**r, "ok": bool(r["ok"])} for r in self._all("SELECT ts, item, action, detail, ok FROM vault_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, LOG_KEEP)),))]

    # ---- devices: every phone/computer that signs in gets its own token, so one can be removed without touching the others
    PAIRING_TTL = 600.0
    LAST_SEEN_EVERY = 60.0

    @staticmethod
    def _hash(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()  # the secrets are 192-256 random bits: a plain hash is enough

    def new_pairing(self, owner: bool = False) -> str:   # `owner` is ignored: every device has the same rights
        """A one-time code (valid ten minutes) that turns into a device token on first use. Only its hash is stored.
        `owner` is ignored (there are no owner devices any more)."""
        code = secrets.token_urlsafe(24)
        self._run("DELETE FROM pairings WHERE expires_at < ?", (time.time(),))
        self._run("INSERT INTO pairings(code_hash, expires_at, owner) VALUES(?, ?, ?)",
                  (self._hash(code), time.time() + self.PAIRING_TTL, 0))
        return code

    def redeem_pairing(self, code: str, name: str) -> tuple[int, str] | None:
        """(device id, device token) when the code is valid; the code is spent either way it is presented correctly.
        A multi-use access code (`new_access_code`) is tried when no one-time code matches."""
        with self._lock:
            row = self._db.execute("SELECT expires_at FROM pairings WHERE code_hash = ?", (self._hash(code),)).fetchone()
            if not row:
                return self._redeem_access_code(code, name)
            self._db.execute("DELETE FROM pairings WHERE code_hash = ?", (self._hash(code),))
            if row[0] < time.time():
                self._db.commit()
                return None
            token = secrets.token_urlsafe(32)
            cur = self._db.execute("INSERT INTO devices(name, token_hash, created_at) VALUES(?,?,?)",
                                     (name, self._hash(token), time.time()))
            self._db.commit()
            return int(cur.lastrowid), token

    def device_for_token(self, token: str) -> dict[str, Any] | None:
        rows = self._all("SELECT id, name, last_seen, e2e_only FROM devices WHERE token_hash = ?", (self._hash(token),))
        if not rows:
            return None
        row = rows[0]
        if not row["last_seen"] or time.time() - row["last_seen"] > self.LAST_SEEN_EVERY:
            self._run("UPDATE devices SET last_seen = ? WHERE id = ?", (time.time(), row["id"]))
        return {"id": row["id"], "name": row["name"], "e2e_only": bool(row["e2e_only"])}

    def devices(self) -> list[dict[str, Any]]:
        """Every signed-in device; "code": the label of the access code it joined with ("" for a one-time code)."""
        rows = self._all("SELECT d.id, d.name, d.created_at, d.last_seen, d.e2e_only, d.approval_key != '' AS approval_key, "
                         "COALESCE(c.label, '') AS code FROM devices d LEFT JOIN access_codes c ON c.id = d.code_id ORDER BY d.id")
        return [{**{k: v for k, v in r.items() if k not in ("e2e_only", "approval_key")}, "e2e": bool(r["e2e_only"]),
                 "approval_key": bool(r["approval_key"])} for r in rows]

    # ---- approval keys (approval_sign.py): one ES256 public key per device, held by the phone's keystore. Removing the device removes it.
    def approval_key(self, device_id: int) -> str:
        """The device's approval public key (base64 SubjectPublicKeyInfo DER), "" when it has none."""
        rows = self._all("SELECT approval_key FROM devices WHERE id = ?", (device_id,))
        return rows[0]["approval_key"] if rows else ""

    def set_approval_key(self, device_id: int, public_key: str) -> bool:
        return self._run("UPDATE devices SET approval_key = ? WHERE id = ?", (public_key, device_id)).rowcount > 0

    def set_device_e2e_only(self, device_id: int) -> bool:
        """KE2E: after a device's first end-to-end call its token is refused on plaintext /api/* (e2e-and-phone-key.md 1.3).
        Pairing again makes a new device; nothing turns it back."""
        return self._run("UPDATE devices SET e2e_only = 1 WHERE id = ? AND e2e_only = 0", (device_id,)).rowcount > 0

    def delete_device(self, device_id: int) -> bool:
        return self._run("DELETE FROM devices WHERE id = ?", (device_id,)).rowcount > 0

    # ---- access codes: one code many phones may use for a while (an app store's reviewers, typed from the review notes). Each phone
    # that uses one becomes a device like any other tagged with the code's label. Only a hash is kept, of the
    # code with case, spaces and dashes taken out, so "abcd-efgh..." typed on a phone matches "ABCD-EFGH...". Revoking a code ends it
    # and signs out every device that joined with it; a code that only ran out leaves its devices signed in.
    ACCESS_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no I, L, O, 0, 1: nothing that reads as something else
    ACCESS_GROUPS = 4                                      # XXXX-XXXX-XXXX-XXXX: 16 symbols, about 79 random bits, 19 characters
    ACCESS_MAX_DAYS = 365
    ACCESS_MAX_USES = 1000

    @classmethod
    def _access_hash(cls, code: str) -> str:
        return cls._hash("access-code:" + "".join(code.split()).replace("-", "").upper())

    def new_access_code(self, label: str, *, days: float = 30, uses: int = 20) -> tuple[int, str]:
        """(id, code). `label` names it in the device list; `days` it can be used for; `uses` phones it can sign in."""
        label = "".join(ch for ch in str(label).strip() if ch.isprintable())[:40]
        if not label:
            raise ValueError("the code needs a label (for example: review-apple)")
        if not 0 < float(days) <= self.ACCESS_MAX_DAYS:
            raise ValueError(f"days must be more than 0 and at most {self.ACCESS_MAX_DAYS}")
        if not 1 <= int(uses) <= self.ACCESS_MAX_USES:
            raise ValueError(f"uses must be 1 to {self.ACCESS_MAX_USES}")
        code = "-".join("".join(secrets.choice(self.ACCESS_ALPHABET) for _ in range(4)) for _ in range(self.ACCESS_GROUPS))
        now = time.time()
        cur = self._run("INSERT INTO access_codes(label, code_hash, created_at, expires_at, max_uses) VALUES(?,?,?,?,?)",
                        (label, self._access_hash(code), now, now + float(days) * 86_400, int(uses)))
        return int(cur.lastrowid), code

    def _redeem_access_code(self, code: str, name: str) -> tuple[int, str] | None:
        """Called with the lock held."""
        row = self._db.execute("SELECT id, expires_at, max_uses, uses, revoked_at FROM access_codes WHERE code_hash = ?",
                               (self._access_hash(code),)).fetchone()
        if not row or row[4] is not None or row[1] < time.time() or row[3] >= row[2]:
            return None
        token = secrets.token_urlsafe(32)
        self._db.execute("UPDATE access_codes SET uses = uses + 1 WHERE id = ?", (row[0],))
        cur = self._db.execute("INSERT INTO devices(name, token_hash, created_at, code_id) VALUES(?,?,?,?)",
                               (name, self._hash(token), time.time(), row[0]))
        self._db.commit()
        return int(cur.lastrowid), token

    def access_codes(self) -> list[dict[str, Any]]:
        """Every access code without the code itself: {"id", "label", "created_at", "expires_at", "max_uses", "uses", "devices"
        (signed in now), "revoked_at", "state": "active" | "expired" | "used_up" | "revoked"}."""
        rows = self._all("SELECT c.id, c.label, c.created_at, c.expires_at, c.max_uses, c.uses, c.revoked_at, "
                         "(SELECT COUNT(*) FROM devices d WHERE d.code_id = c.id) AS devices FROM access_codes c ORDER BY c.id")
        now = time.time()
        for r in rows:
            r["state"] = ("revoked" if r["revoked_at"] is not None else "expired" if r["expires_at"] < now
                          else "used_up" if r["uses"] >= r["max_uses"] else "active")
        return rows

    def revoke_access_code(self, code_id: int) -> list[int] | None:
        """End a code and sign out the devices that joined with it. Returns their ids (to clean up after them), None if no such code."""
        with self._lock:
            if not self._db.execute("SELECT 1 FROM access_codes WHERE id = ?", (code_id,)).fetchone():
                return None
            ids = [int(r[0]) for r in self._db.execute("SELECT id FROM devices WHERE code_id = ?", (code_id,))]
            self._db.execute("UPDATE access_codes SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?", (time.time(), code_id))
            self._db.execute("DELETE FROM devices WHERE code_id = ?", (code_id,))
            self._db.commit()
            return ids

    # ---- usage: daily totals (kept for good) and one record per model call (USAGE_KEEP_DAYS / USAGE_KEEP_ROWS, the newest kept)
    USAGE_KEEP_DAYS = 400
    USAGE_KEEP_ROWS = 200_000
    _USAGE_SUMS = ("COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens, "
                   "COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens, COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens")

    def add_usage(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> None:
        self._run("INSERT INTO usage(day, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(day) DO UPDATE SET input_tokens = input_tokens + excluded.input_tokens, "
                  "output_tokens = output_tokens + excluded.output_tokens, cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens, "
                  "cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens",
                  (_today(), int(input_tokens), int(output_tokens), int(cache_read_tokens), int(cache_write_tokens)))

    def add_usage_event(self, kind: str, *, provider: str = "", model: str = "", input_tokens: int = 0, output_tokens: int = 0,
                        cache_read_tokens: int = 0, cache_write_tokens: int = 0, task_id: int | None = None, note: str = "") -> None:
        """One model call: kept as a record and added to the day's totals."""
        vals = [max(0, int(v or 0)) for v in (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)]
        self._run("INSERT INTO usage_events(ts, day, kind, task_id, provider, model, input_tokens, output_tokens, cache_read_tokens, "
                  "cache_write_tokens, note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (time.time(), _today(), kind[:40], task_id, provider[:80], model[:120], *vals, note[:300]))
        self.add_usage(*vals)
        self._usage_writes += 1
        if self._usage_writes % 500 == 0:
            self.prune_usage_events()

    def prune_usage_events(self) -> int:
        """Records older than USAGE_KEEP_DAYS go, and beyond the newest USAGE_KEEP_ROWS; the daily totals stay."""
        cut = time.strftime("%Y-%m-%d", time.localtime(time.time() - self.USAGE_KEEP_DAYS * 86400))
        n = self._run("DELETE FROM usage_events WHERE day < ?", (cut,)).rowcount
        n += self._run("DELETE FROM usage_events WHERE id <= (SELECT MAX(id) FROM usage_events) - ?", (self.USAGE_KEEP_ROWS,)).rowcount
        return n

    def usage(self) -> dict[str, Any]:
        today = self._all("SELECT input_tokens, output_tokens FROM usage WHERE day = ?", (_today(),))
        total = self._all("SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o FROM usage")[0]
        t = today[0] if today else {"input_tokens": 0, "output_tokens": 0}
        return {"today": {"input_tokens": t["input_tokens"], "output_tokens": t["output_tokens"]},
                "total": {"input_tokens": total["i"], "output_tokens": total["o"]}}

    def usage_daily(self, since: str) -> list[dict[str, Any]]:
        """The daily totals from `since` (YYYY-MM-DD) on, oldest first."""
        return self._all("SELECT day, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens FROM usage WHERE day >= ? "
                         "ORDER BY day", (since,))

    def usage_by(self, column: str, since: str) -> list[dict[str, Any]]:
        """The records from `since` on, summed by kind or by (provider, model), the largest first; each with its number of calls."""
        group = {"kind": "kind", "model": "provider, model"}[column]
        return self._all(f"SELECT {group}, COUNT(*) AS calls, {self._USAGE_SUMS} FROM usage_events WHERE day >= ? GROUP BY {group} "
                         "ORDER BY SUM(input_tokens + output_tokens) DESC", (since,))

    def usage_sum(self, since: str) -> dict[str, Any]:
        return self._all(f"SELECT COUNT(*) AS calls, {self._USAGE_SUMS} FROM usage_events WHERE day >= ?", (since,))[0]
