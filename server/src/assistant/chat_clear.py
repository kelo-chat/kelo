"""Clearing the chat history (DELETE /api/messages): the conversation and everything that was made from it, in one transaction.

Deleted: the messages and their rolling summary, the task log (the Work page), background jobs and their step transcripts, the
approval history, notifications, follow-ups, the nightly day summaries, scheduled runs' history (their results are answers of the
agent), the tap-to-answer buttons and "Continue" offers that hung on messages, goal suggestions still waiting for an answer, the reply log (every reply's events, turn_log.py).
Emptied (the row stays, the words go): the proactive ledger's message text (the row keeps a reminder from coming twice), the
evidence of goal suggestions that were set aside (the row keeps them from being suggested again), and schedules' link to their last
task (which is gone).

Kept: goals with their plans and logs, files, schedules and watchers themselves, connectors, the vault and the wallet, approved and
learned rules, the profile, devices, usage counters.

Every module keeps its tables in the store's one connection, which runs with PRAGMA secure_delete = ON (store.py): deleted rows are
overwritten in the file, not just unlinked. Stopping the work that is still running, the in-memory state and the browser pictures on
disk are the server's part (server.py, before and after this).
"""

from __future__ import annotations

from typing import Any

# (name in the result, SQL): run in this order, inside one transaction
DELETES: tuple[tuple[str, str], ...] = (
    ("messages", "DELETE FROM messages"),
    ("message_attachments", "DELETE FROM message_attachments"),   # the list of files sent with messages (the files stay)
    ("conversation_summary", "DELETE FROM conversation_summary"),
    ("tasks", "DELETE FROM tasks"),
    ("workers", "DELETE FROM workers"),
    ("worker_messages", "DELETE FROM worker_messages"),
    ("approvals", "DELETE FROM approval_log"),
    ("notifications", "DELETE FROM notifications"),
    ("followups", "DELETE FROM followups"),
    ("journal", "DELETE FROM journal"),
    ("journal_days", "DELETE FROM journal_days"),
    ("schedule_runs", "DELETE FROM schedule_runs"),
    ("resume_offers", "DELETE FROM resume_offers"),
    ("message_replies", "DELETE FROM message_replies"),
    ("message_comparisons", "DELETE FROM message_comparisons"),
    ("goal_suggestions", "DELETE FROM goal_suggestions WHERE status = 'proposed'"),
    ("turn_events", "DELETE FROM turn_events"),   # the reply log (turn_log.py): a cleared reply is not read again, not even after a restart
    ("turn_log", "DELETE FROM turn_log"),
)
BLANKS: tuple[tuple[str, str], ...] = (
    ("proactive_log", "UPDATE proactive_log SET message = '', reason = '', task_id = NULL WHERE message != '' OR reason != '' OR task_id IS NOT NULL"),
    ("goal_suggestions", "UPDATE goal_suggestions SET evidence = '', reason = '' WHERE status = 'dismissed' AND (evidence != '' OR reason != '')"),
    ("schedules", "UPDATE schedules SET last_task_id = NULL WHERE last_task_id IS NOT NULL"),
)


def _table(sql: str) -> str:
    words = sql.split()
    return words[2] if words[0] == "DELETE" else words[1]


def clear_tables(store: Any) -> dict[str, int]:
    """Delete the conversation and what came of it (see the module text). Returns rows deleted per item (a table this server does not
    have counts 0). All or nothing: an error rolls everything back."""
    out: dict[str, int] = {}
    with store._lock:
        have = {r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        try:
            for name, sql in DELETES:
                out[name] = store._db.execute(sql).rowcount if _table(sql) in have else 0
            for name, sql in BLANKS:
                if _table(sql) in have:
                    store._db.execute(sql)
            store._db.commit()
        except Exception:
            store._db.rollback()
            raise
    return out
