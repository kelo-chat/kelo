"""`assistant eval export` (assistant/eval_export.py): redaction of each kind, the leak scan and its failure path, the read-only copy,
and the CLI exit codes. Real Store on temporary files; no network."""

import hashlib
import json
import time
from argparse import Namespace
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from assistant import eval_export as ex
from assistant.eval_cli import run_command
from assistant.store import Store
from assistant.turn_log import SCHEMA as TURN_SCHEMA

KEY = Fernet.generate_key().decode()
SECRET = "hunter2-EXAMPLE-value"
API_KEY = "sk-proj-EXAMPLEKEYNOTREAL000000"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("text, kind, raw", [
    ("write to jane.doe@example.test today", "email", "jane.doe@example.test"),
    ("call 010-1234-5678 after six", "phone", "010-1234-5678"),
    ("card 4111 1111 1111 1111 expires soon", "card", "4111 1111 1111 1111"),
    ("deliver to 테헤란로 123 please", "address", "테헤란로 123"),
    ("ship to 221 Baker Street", "address", "221 Baker Street"),
    ("the key is " + API_KEY, "api_key", API_KEY),
    ("my vault value " + SECRET + " here", "vault", SECRET),
])
def test_each_kind_is_redacted(text, kind, raw):
    out, counts = ex.redact(text, [SECRET])
    assert raw not in out and f"[redacted:{kind}]" in out
    assert counts.get(kind, 0) >= 1
    assert ex.leaks(out, [SECRET]) == []


def test_ordinary_text_and_numbers_are_left_alone():
    text = "Order 2024-05-01 for 3 items, 1234 5678 9012 3456 is not a card, see https://example.test/a/b"
    out, counts = ex.redact(text, [])
    assert out == text and counts == {}


def test_short_vault_values_are_not_used_as_patterns():
    out, counts = ex.redact("the cat is fine", ["cat"])
    assert out == "the cat is fine" and counts == {}


def test_redact_tree_counts_every_string():
    counts = {}
    tree = {"user": "mail jane.doe@example.test", "events": [{"output": ["010-1234-5678"]}], "n": 3}
    clean = ex.redact_tree(tree, [], counts)
    assert "jane.doe" not in json.dumps(clean) and clean["n"] == 3
    assert counts == {"email": 1, "phone": 1}


def test_leak_scan_finds_what_redaction_missed():
    assert ex.leaks("mail jane.doe@example.test", []) == ["email"]
    assert ex.leaks("value " + SECRET, [SECRET]) == ["vault"]


T0 = time.time() - 1000  # the reply started here (recent, so the CLI's 7-day window holds it); exports below run 100 s later


def make_db(path: Path, *, secret: str = SECRET) -> Store:
    store = Store(path, KEY)
    store._db.executescript(TURN_SCHEMA)
    store.save_credential("shop-login", "Shop", ["shop.example.test"], {}, "", kind="api")
    store.set_credential_secret("shop-login", secret)
    store._db.execute("INSERT INTO messages(role, content, created_at) VALUES('user', ?, ?)",
                      ("my mail is jane.doe@example.test, pay with 4111 1111 1111 1111", T0 - 1.0))
    store._db.execute("INSERT INTO turn_log(turn_id, status, started_at, ended_at) VALUES('t-1', 'done', ?, ?)", (T0, T0 + 9))
    events = [
        {"type": "tool_started", "id": "c1", "name": "web_search", "arguments": {"q": "phone case"}, "label": "Searching"},
        {"type": "tool_result", "id": "c1", "name": "web_search", "is_error": False, "output": "call 010-1234-5678 " + "x" * 3000},
        {"type": "text", "text": "Found it for jane.doe@example.test."},
        {"type": "done"},
    ]
    for seq, ev in enumerate(events):
        store._db.execute("INSERT INTO turn_events(turn_id, seq, ev) VALUES('t-1', ?, ?)", (seq, json.dumps(ev)))
    store._db.commit()
    return store


def test_export_writes_redacted_threads_and_leaves_the_database_alone(tmp_path):
    db = tmp_path / "assistant.db"
    make_db(db)
    before = digest(db)
    out = tmp_path / "golden"
    report = ex.export(db, out, days=7, secret_key=KEY, now=T0 + 100)
    assert report.ok and len(report.written) == 1
    assert digest(db) == before, "the live database must not change"
    text = (out / "t-1.json").read_text(encoding="utf-8")
    for raw in ("jane.doe@example.test", "010-1234-5678", "4111 1111 1111 1111", SECRET):
        assert raw not in text
    record = json.loads(text)
    assert record["user"].startswith("my mail is [redacted:email]")
    assert record["reply"] == "Found it for [redacted:email]."
    result = next(e for e in record["events"] if e["type"] == "tool_result")
    assert len(result["output"]) <= ex.ARG_CUT
    assert report.redactions["email"] >= 2 and report.redactions["card"] == 1 and report.redactions.get("vault", 0) == 0


def test_a_leak_stops_the_export_and_removes_its_files(tmp_path, monkeypatch):
    db = tmp_path / "assistant.db"
    make_db(db)
    monkeypatch.setattr(ex, "redact", lambda text, values: (text, {}))   # a redaction that does nothing: the scan must catch it
    report = ex.export(db, tmp_path / "golden", days=7, secret_key=KEY, now=T0 + 100)
    assert not report.ok and report.written == []
    assert any("email" in leak for leak in report.leaks)
    assert not list((tmp_path / "golden").glob("*.json"))


def test_an_unreadable_vault_stops_the_export(tmp_path, monkeypatch):
    db = tmp_path / "assistant.db"
    make_db(db)
    monkeypatch.setattr(Store, "credential", lambda self, name: {"secret": None, "username": ""})
    with pytest.raises(ex.ExportError):
        ex.export(db, tmp_path / "golden", days=7, secret_key=KEY, now=T0 + 100)


def test_cli_exit_codes(tmp_path, monkeypatch):
    db = tmp_path / "assistant.db"
    make_db(db)
    env = tmp_path / "env"
    env.write_text(f"ASSISTANT_HOME={tmp_path}\nASSISTANT_SECRET_KEY={KEY}\n", encoding="utf-8")
    lines = []
    args = Namespace(evcmd="export", days=7, out=tmp_path / "out", instance="main", env_file=env)
    assert run_command(args, out=lines.append) == 0 and any("1 thread(s)" in line for line in lines)
    empty_env = tmp_path / "empty.env"   # its own home folder, so no real database is ever read by a test
    empty_env.write_text(f"ASSISTANT_HOME={tmp_path / 'no-such-home'}\n", encoding="utf-8")
    missing = Namespace(evcmd="export", days=7, out=tmp_path / "o2", instance="main", env_file=empty_env)
    assert run_command(missing, out=lines.append) == 2
    monkeypatch.setattr(ex, "redact", lambda text, values: (text, {}))
    assert run_command(args, out=lines.append) == 1
    monkeypatch.setattr(Store, "credential", lambda self, name: {"secret": None, "username": ""})
    assert run_command(Namespace(evcmd="export", days=7, out=tmp_path / "o3", instance="main", env_file=env), out=lines.append) == 2
    assert any("nothing was kept" in line for line in lines)
