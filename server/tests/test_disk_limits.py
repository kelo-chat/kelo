"""disk_limits.py: every rule removes only what it names (old, finished, orphaned) and leaves the owner's records and anything still open."""

import os
import time

import assistant.disk_limits as dl
from assistant.disk_limits import DAY, RULES, DiskLimits
from assistant.follow.store import FollowStore
from assistant.recheck import SCHEMA as RECHECK_SCHEMA
from assistant.store import Store

NOW = 1_800_000_000.0


def make() -> tuple[Store, DiskLimits]:
    store = Store(":memory:")
    FollowStore(store)
    store._db.executescript(RECHECK_SCHEMA)
    store._run("CREATE TABLE IF NOT EXISTS watchers (id INTEGER PRIMARY KEY)")
    store._run("CREATE TABLE IF NOT EXISTS message_replies (message_id INTEGER PRIMARY KEY, questions TEXT NOT NULL, goal_id INTEGER, created_at REAL NOT NULL)")
    store._run("CREATE TABLE IF NOT EXISTS message_comparisons (message_id INTEGER PRIMARY KEY, card TEXT NOT NULL, created_at REAL NOT NULL)")
    return store, DiskLimits(store, clock=lambda: NOW)


def count(store: Store, table: str) -> int:
    return store._all(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def test_every_rule_runs_on_an_empty_database_and_a_missing_table_is_skipped():
    store = Store(":memory:")          # none of the follow / review tables exist here
    assert DiskLimits(store, clock=lambda: NOW).prune_tables() == {}
    assert len(RULES) >= 15 and len({r.name for r in RULES}) == len(RULES)
    store2, d = make()
    assert d.prune_tables() == {}      # and with every table present but empty nothing is reported


def test_follow_through_rows_go_by_age_and_children_follow_their_parents():
    store, d = make()
    old, new = NOW - 90 * DAY, NOW - 1 * DAY
    for key, start in (("old", old), ("new", new)):
        store._run("INSERT INTO follow_items(key, kind, title, starts_at, created_at, updated_at) VALUES(?, 'trip', 't', ?, 0, 0)", (key, start))
    sql = ("INSERT INTO follow_checks(id, item_key, kind, window_start, window_end, next_at, created_at, state) "
           "VALUES(?, ?, 'x', 0, ?, 0, 0, ?)")
    store._run(sql, (1, "old", old, "active"))
    store._run(sql, (2, "new", NOW - 40 * DAY, "done"))      # finished long ago on a recent item
    store._run(sql, (3, "new", NOW + DAY, "active"))
    for cid, at in ((1, new), (2, new), (3, new), (3, NOW - 40 * DAY)):
        store._run("INSERT INTO follow_readings(check_id, at) VALUES(?, ?)", (cid, at))
    done = d.prune_tables()
    assert [r["key"] for r in store._all("SELECT key FROM follow_items")] == ["new"]
    assert [r["id"] for r in store._all("SELECT id FROM follow_checks ORDER BY id")] == [3]
    assert [r["check_id"] for r in store._all("SELECT check_id FROM follow_readings")] == [3]    # the recent reading of the live check only
    assert done["follow items long past"] == 1


def test_the_memory_pass_log_keeps_the_newest_and_never_touches_the_claims(monkeypatch):
    store, d = make()
    monkeypatch.setattr(dl, "RULES", tuple(dl.Rule(r.name, r.table, r.sql, r.days, 3 if r.keep else None) for r in dl.RULES))
    for i in range(6):
        store._run("INSERT INTO memory_runs(run_id, started_at) VALUES(?, ?)", (f"r{i}", float(i)))
    store._run("INSERT INTO memory_claims(claim_id, run_id, kind, salience, text, quote, speaker, handle, source_path, created_at, updated_at) "
               "VALUES('c1','r0','fact','high','t','q','owner','h','p',0,0)")
    d.prune_tables()
    assert [r["run_id"] for r in store._all("SELECT run_id FROM memory_runs ORDER BY started_at")] == ["r3", "r4", "r5"]
    assert count(store, "memory_claims") == 1


def test_open_rechecks_stay_and_old_closed_ones_go():
    store, d = make()
    old = NOW - 400 * DAY
    for state in ("closed", "open", "stopped"):
        store._run("INSERT INTO rechecks(topic_key, created, next_at, state) VALUES(?, ?, 0, ?)", (state, old, state))
    d.prune_tables()
    assert [r["state"] for r in store._all("SELECT state FROM rechecks")] == ["open"]


def test_payment_records_are_never_pruned():
    store, d = make()
    old = NOW - 800 * DAY
    for i, (state, created) in enumerate((("placed", old), ("unknown", old), ("pressed", old), ("placed", NOW - DAY))):
        store._run("INSERT INTO pay_attempts(id, approval_id, terms_id, owner, merchant_origin, state, created_at, updated_at) "
                   "VALUES(?, ?, ?, 'o', 'https://shop', ?, ?, ?)", (f"a{i}", f"p{i}", f"t{i}", state, created, created))
        store._run("INSERT INTO pay_approvals(id, terms_id, owner, merchant_origin, approved_total_minor, currency, terms_digest, source, "
                   "expires_at, state, created_at) VALUES(?, ?, 'o', 'https://shop', 1, 'KRW', 'd', 's', 0, 'used', ?)", (f"p{i}", f"t{i}", created))
        store._run("INSERT INTO order_terms(id, owner, status, merchant_origin, items, obligations, currency, total_minor, terms_digest, created_at) "
                   "VALUES(?, 'o', 'ready', 'https://shop', '[]', '[]', 'KRW', 1, 'd', ?)", (f"t{i}", created))
    d.prune_tables()
    assert [r["id"] for r in store._all("SELECT id FROM pay_attempts ORDER BY id")] == ["a0", "a1", "a2", "a3"]
    assert [r["id"] for r in store._all("SELECT id FROM pay_approvals ORDER BY id")] == ["p0", "p1", "p2", "p3"]
    assert [r["id"] for r in store._all("SELECT id FROM order_terms ORDER BY id")] == ["t0", "t1", "t2", "t3"]


def test_rows_hanging_on_removed_messages_go_but_chat_itself_stays():
    store, d = make()
    mid = store.add_message("user", "hello")
    for table, col in (("message_replies", "questions"), ("message_comparisons", "card")):
        store._run(f"INSERT INTO {table}(message_id, {col}, created_at) VALUES(?, 'x', 0)", (mid,))
        store._run(f"INSERT INTO {table}(message_id, {col}, created_at) VALUES(?, 'x', 0)", (mid + 99,))
    d.prune_tables()
    assert count(store, "message_replies") == 1 and count(store, "message_comparisons") == 1
    assert count(store, "messages") == 1


def test_the_owners_records_are_never_in_a_rule():
    owned = {"messages", "memory_claims", "journal", "goals", "wallet_payments", "wallet_cards", "credentials", "profile", "devices"}
    assert owned.isdisjoint({r.table for r in RULES})
    for r in RULES:
        assert "DELETE FROM" in r.sql and "WHERE" in r.sql, r.name       # no rule can empty a table


def test_the_database_file_shrinks_when_much_of_it_is_empty(tmp_path):
    path = tmp_path / "assistant.db"
    store = Store(str(path))
    store._run("CREATE TABLE junk(x BLOB)")
    for _ in range(200):
        store._db.execute("INSERT INTO junk VALUES(?)", (b"x" * 100_000,))
    store._db.commit()
    store._run("DELETE FROM junk")
    before = path.stat().st_size
    d = DiskLimits(store, clock=lambda: NOW)
    assert d.reclaim() is False                     # 20 MB of empty pages is under the 64 MB floor
    assert d.reclaim(force=True) is True
    assert path.stat().st_size < before / 2
    assert d.reclaim() is False                     # and not again within a week


def test_browser_history_over_the_cap_loses_whole_tabs_oldest_first_and_never_a_busy_one(tmp_path):
    root = tmp_path / "browser-history"
    now = time.time()
    for name, age in (("task_old", 5 * 3600), ("task_mid", 3 * 3600), ("task_busy", 60)):
        d = root / name
        d.mkdir(parents=True)
        (d / "000001.jpg").write_bytes(b"x" * 1000)
        (d / "manifest.jsonl").write_text(f'{{"seq": 1, "at": {now - age}}}\n')
        for p in (d / "000001.jpg", d / "manifest.jsonl", d):
            os.utime(p, (now - age, now - age))
    store = Store(":memory:")
    assert DiskLimits(store, history_root=root, history_cap=2500).prune_browser_history() == 1
    assert sorted(p.name for p in root.iterdir()) == ["task_busy", "task_mid"]
    DiskLimits(store, history_root=root, history_cap=0).prune_browser_history()
    assert [p.name for p in root.iterdir()] == ["task_busy"]


def test_run_reports_each_part_and_one_failing_part_does_not_stop_the_rest():
    store, d = make()
    d.prune_tables = lambda: (_ for _ in ()).throw(RuntimeError("boom"))     # type: ignore[method-assign]
    out = d.run()
    assert out["tables"] == "failed: RuntimeError" and out["browser_history"] == 0 and out["reclaimed"] is False


def test_every_table_the_server_creates_is_named_in_the_disk_growth_doc():
    """A new table must be put in docs/disk-growth.md (with its limit, or the reason it has none) before it ships."""
    import re
    from pathlib import Path
    src = Path(dl.__file__).parent
    names = set()
    for f in src.rglob("*.py"):
        names |= set(re.findall(r"CREATE (?:VIRTUAL )?TABLE IF NOT EXISTS ([a-z_0-9]+)", f.read_text(encoding="utf-8")))
    doc = (Path(dl.__file__).resolve().parents[3] / "docs" / "disk-growth.md").read_text(encoding="utf-8")
    missing = sorted(n for n in names if f"`{n}`" not in doc)
    assert not missing, f"not in docs/disk-growth.md: {missing}"
