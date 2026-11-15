"""Review mode: a copy of Kelo that an app store's reviewers sign in to (ASSISTANT_REVIEW_MODE=1; `assistant review create`).

It must look and work exactly like the real product: no banner, no demo shop, every feature on (chat, connectors, schedules, the
browser, the wallet and payments). Only three things differ:
- Subscription sign-ins (the "unofficial" providers) are off and cannot be turned on, chosen or used, even when the profile or the
  environment names one: such a choice is cleared at start-up and any model call to one is refused (`official_only`).
- Operator settings stay hidden: reviewers' phones are ordinary devices (never the owner's rights) and the copy never calls itself
  the operator's server.
- The model is set up on the server by the operator (`assistant review model`: ASSISTANT_PROVIDER / ASSISTANT_MODEL and the API key
  in the copy's settings file). At start-up that server default is written into the profile when the profile names no model, so a
  reviewer's phone shows the model by name and is never asked to choose one.

Reviewers sign in with a multi-use access code (store.new_access_code; `assistant review code`).

`python -m assistant.review reset-state /state` (run by `assistant review reset` in a one-off server container while the copy is
stopped) empties a copy's state folder back to fresh: chats, memory, files, connectors and everything else go; what keeps the copy
working for the next reviewer stays (`reset_state`).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

ENV_KEY = "ASSISTANT_REVIEW_MODE"
STORES = {"apple": "App Store (iPhone)", "google": "Google Play (Android)", "ms": "Microsoft Store (Windows)",
          "testflight": "TestFlight beta (iPhone)"}

UNOFFICIAL_OFF = ("Subscription sign-in models are turned off on this demo server. Choose a model that uses an API key in My → Model.")


def enabled(env: dict[str, str]) -> bool:
    return env.get(ENV_KEY, "").strip().lower() not in ("", "0", "false", "no")


def official_only(stream_fn: Callable[..., AsyncIterator[Any]], is_unofficial: Callable[[Any], bool]) -> Callable[..., AsyncIterator[Any]]:
    """A model stream that refuses the subscription sign-in providers, whatever asked for one."""
    from .providers import ProviderError

    async def guarded(provider: Any, request: Any, **kw: Any) -> AsyncIterator[Any]:
        if is_unofficial(provider):
            raise ProviderError(UNOFFICIAL_OFF, status=403)
        async for ev in stream_fn(provider, request, **kw):
            yield ev
    return guarded


# ------------------------------------------------------------------------------------------------ reset (offline, server stopped)
# what a fresh copy keeps: who may sign in and the keys saved for models. Everything else (chats, memory, goals, schedules, watches,
# connectors and logins in the vault, the wallet, phone data, files, pictures, packages, usage) goes. The model choice goes too, so the
# server's default (the settings file, `assistant review model`) is written into the profile again at the next start.
KEEP_TABLES = ("profile", "api_keys", "devices", "pairings", "access_codes", "push_tokens", "push_installs", "meta")
KEEP_PROFILE = ("timezone",)
KEEP_META = ("base_url:", "model_windows:", "push:install_id", "reports:install_id", "oauth_client_id", "google_signin_open",
             "support_url")
KEEP_FILES = ("assistant.db", "master.key")
DB_NAME = "assistant.db"


def _kept(table: str, row: dict[str, Any]) -> bool:
    if table == "profile":
        return row.get("key") in KEEP_PROFILE
    if table == "meta":
        return str(row.get("key", "")).startswith(KEEP_META)
    return True


def reset_state(state: Path) -> dict[str, int]:
    """Make `state` (a copy's /state) fresh. Returns {"rows_kept", "removed"} (rows copied, files and folders deleted)."""
    state = Path(state)
    if not state.is_dir():
        raise SystemExit(f"no such folder: {state}")
    db, kept = state / DB_NAME, 0
    if db.is_file():
        fresh = state / (DB_NAME + ".reset")
        fresh.unlink(missing_ok=True)
        os.close(os.open(fresh, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600))
        src = sqlite3.connect(str(db))
        src.row_factory = sqlite3.Row
        dst = sqlite3.connect(str(fresh))
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            have = {r["name"]: r["sql"] for r in src.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")}
            seq = {r["name"]: r["seq"] for r in src.execute("SELECT name, seq FROM sqlite_sequence")} if "sqlite_sequence" in have else {}
            for table in KEEP_TABLES:
                if table not in have or not have[table]:
                    continue
                dst.execute(have[table])
                rows = [dict(r) for r in src.execute(f"SELECT * FROM {table}")]   # noqa: S608 - table names are our constants
                rows = [r for r in rows if _kept(table, r)]
                if rows:
                    cols = list(rows[0])
                    dst.executemany(f"INSERT INTO {table}({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",  # noqa: S608
                                    [tuple(r[c] for c in cols) for r in rows])
                    kept += len(rows)
                if table in seq:   # ids are never handed out twice (a signed-out phone's id stays unused)
                    if not dst.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (seq[table], table)).rowcount:
                        dst.execute("INSERT INTO sqlite_sequence(name, seq) VALUES(?, ?)", (table, seq[table]))
            dst.commit()
        finally:
            src.close()
            dst.close()
        os.replace(fresh, db)
        for extra in ("-wal", "-shm", "-journal"):
            (state / (DB_NAME + extra)).unlink(missing_ok=True)
    removed = 0
    for child in state.iterdir():
        if child.name in KEEP_FILES:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    return {"rows_kept": kept, "removed": removed}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "reset-state":
        print("usage: python -m assistant.review reset-state <state folder>", file=sys.stderr)
        return 64
    done = reset_state(Path(args[1]))
    print(f"reset: kept {done['rows_kept']} rows of settings and sign-ins, removed {done['removed']} files and folders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
