"""The 90-day cleanup of the look's own history (situation.cleanup): old told, dropped and expired items with their events, passes, the
situation rows of the ledger and the snapshots of connectors gone for good. Runs in batches; a prepared item is never removed by it."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import assistant.situation as situation_module
from situation_fake import ts
from test_situation import rig

from assistant.situation import CLEANUP_BATCH, KEEP_DAYS

DAY = 86400
SRC = Path(__file__).resolve().parents[1] / "src" / "assistant"


def _item(r, key: str, status: str, updated: float) -> int:
    cur = r.sit.store._run("INSERT INTO help_items(key, title, prepared, evidence_json, expires_at, status, created_at, updated_at, pass_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?)", (key, key, "text", "[]", updated + DAY, status, updated, updated, updated))
    return int(cur.lastrowid)


def _count(r, table: str) -> int:
    return r.sit.store._all(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def test_rows_older_than_ninety_days_go_and_newer_ones_stay(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    now = r.clock.t
    old, new = now - 91 * DAY, now - 89 * DAY
    old_item, new_item = _item(r, "old", "told", old), _item(r, "new", "told", new)
    _item(r, "old-dropped", "dropped", old)
    prepared_old = _item(r, "still-open", "prepared", old)
    r.sit.store._run("INSERT INTO help_events(item_id, pass_id, at, status, why, priority) VALUES(?,?,?,?,?,?)", (old_item, None, old, "told", "", "regular"))
    r.sit.store._run("INSERT INTO help_events(item_id, pass_id, at, status, why, priority) VALUES(?,?,?,?,?,?)", (new_item, None, new, "told", "", "regular"))
    r.sit.store._run("INSERT INTO situation_passes(at, status, sources_json) VALUES(?,?,?)", (old, "nothing", "[]"))
    r.sit.store._run("INSERT INTO situation_passes(at, status, sources_json) VALUES(?,?,?)", (new, "nothing", "[]"))
    for when in (old, new):
        r.sit.store._run("INSERT INTO proactive_log(key, source, title, status, decided_at, expires_at, day, kind) VALUES(?,?,?,?,?,?,?,?)",
                         ("situation:x", "situation", "t", "sent", when, when + DAY, "2026-08-01", ""))
    counts = asyncio.run(r.sit.cleanup(now))
    remaining = {row["key"] for row in r.sit.store._all("SELECT key FROM help_items")}
    assert remaining == {"new", "still-open"}                       # the old told and dropped went; the prepared one never does
    assert _count(r, "help_events") == 1
    assert _count(r, "situation_passes") == 1
    assert r.sit.store._all("SELECT COUNT(*) AS n FROM proactive_log WHERE source = 'situation'")[0]["n"] == 1
    assert counts["help_items"] == 2 and counts["situation_passes"] == 1 and counts["proactive_log"] == 1
    assert prepared_old                                              # (the open item existed before the cleanup)


def test_old_passes_go_in_batches_of_the_batch_size(tmp_path, monkeypatch):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    now = r.clock.t
    monkeypatch.setattr(situation_module, "CLEANUP_BATCH", 500)
    for _i in range(1234):
        r.sit.store._run("INSERT INTO situation_passes(at, status, sources_json) VALUES(?,?,?)", (now - 100 * DAY, "nothing", "[]"))
    calls: list[str] = []
    real = r.sit.store._run

    def counting(sql: str, params: tuple = ()):   # the DELETEs the cleanup makes
        if sql.startswith("DELETE FROM situation_passes"):
            calls.append(sql)
        return real(sql, params)
    monkeypatch.setattr(r.sit.store, "_run", counting)
    counts = asyncio.run(r.sit.cleanup(now))
    assert counts["situation_passes"] == 1234 and len(calls) == 3   # 500 + 500 + 234
    monkeypatch.setattr(r.sit.store, "_run", real)
    assert _count(r, "situation_passes") == 0


def test_a_removed_connectors_snapshot_goes_and_a_live_one_stays(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    now = r.clock.t
    r.sit.store.set_meta("situation:source:gone-service", '{"fp": "a", "ids": null, "since": 1, "read_at": %r}' % (now - 100 * DAY))
    r.sit.store.set_meta("situation:source:live-service", '{"fp": "b", "ids": null, "since": 1, "read_at": %r}' % (now - 3600))
    asyncio.run(r.sit.cleanup(now))
    keys = {row["key"] for row in r.sit.store._all("SELECT key FROM meta WHERE key LIKE 'situation:source:%'")}
    assert keys == {"situation:source:live-service"}


def test_the_cleanup_runs_every_night_at_four_thirty_five_and_the_api_says_ninety_days():
    server = (SRC / "server.py").read_text(encoding="utf-8")
    assert re.search(r'scheduler\.add_daily\("situation_cleanup", 4, 35, situation\.cleanup, needs_secrets=False\)', server)
    assert KEEP_DAYS == 90 and CLEANUP_BATCH == 500
