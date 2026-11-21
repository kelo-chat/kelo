"""A background job's final report is owed to the chat from the moment the job settles (workers.report_owed_at, set in the same statement
that writes the report), and it is handed over until the chat has it: a restart in between does not lose it, and a report the chat
already stores gets its turn once. Fake host, fake clock, no model."""

import json

from assistant.store import Store
from assistant.workers import Workers, WorkerHost

from test_watching import Clock, ts


def job_row(store, wid: str, now: float, report: dict, owed: bool = True) -> None:
    Workers(store, host_with([]), clock=lambda: now)   # creates the workers table (a fresh store has none yet)
    store._run("INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, max_steps, deadline_at, minutes, token_cap, "
               "report, wait_on, report_owed_at) VALUES(?, 'delegate', 'report job', 'do it', ?, 'tab', ?, ?, 10, ?, 5, 1000, ?, '{}', ?)",
               (wid, report["status"], now, now, now + 600, json.dumps(report), now if owed else None))


def host_with(handoffs: list, fail: bool = False, ack: bool = False) -> WorkerHost:
    """`ack`: the chat stored the hidden message, so the handoff turn named it (turn_workers calls report_handed)."""
    holder: dict = {}

    async def handoff(row, text, head, once_key=""):
        if fail:
            raise RuntimeError("the chat is busy")
        handoffs.append((row["id"], once_key))
        if ack:
            holder["workers"].report_handed(row["id"], once_key)
    host = WorkerHost(start_turn=None, cancel_turn=None, handoff=handoff, emit=None, tools=lambda: [])
    host.holder = holder
    return host


def report_at(now: float, status: str = "done") -> dict:
    return {"status": status, "summary": "done", "details": "", "keep_tab": False, "at": now}


async def test_a_job_report_owed_at_a_restart_is_handed_to_the_chat_once():
    store = Store()
    clock = Clock(ts(2026, 11, 7, 8, 0))
    job_row(store, "w1", clock.t, report_at(clock.t))
    handed: list = []
    workers = Workers(store, host_with(handed), clock=clock)
    await workers.hand_owed()                      # the first try went out; the chat never stored it (not in the record)
    assert workers.owed() == ["w1"]
    assert handed == [("w1", f"job-report:w1:{int(clock.t * 1000)}")]
    assert store._all("SELECT report_owed_at FROM workers WHERE id = 'w1'")[0]["report_owed_at"] is not None

    handed2: list = []
    host = host_with(handed2, ack=True)
    restarted = Workers(store, host, clock=clock)   # a new process start on the same database
    host.holder["workers"] = restarted
    await restarted.hand_owed()
    assert handed2 == [("w1", f"job-report:w1:{int(clock.t * 1000)}")] and restarted.owed() == []


async def test_a_report_already_stored_goes_on_without_a_second_hidden_message():
    store = Store()
    clock = Clock(ts(2026, 11, 7, 8, 0))
    job_row(store, "w2", clock.t, report_at(clock.t))
    key = f"job-report:w2:{int(clock.t * 1000)}"
    mid = store.add_message("user", "[hidden report]", once_key=key)
    assert store.message_by_once_key(key) == mid
    assert store.add_message("user", "[hidden report, again]", once_key=key) == mid   # the same key never stores a second message
    assert len(store._all("SELECT id FROM messages WHERE once_key = ?", (key,))) == 1


async def test_without_a_model_the_report_stays_owed_and_goes_out_on_a_later_tick():
    store = Store()
    clock = Clock(ts(2026, 11, 7, 8, 0))
    job_row(store, "w3", clock.t, report_at(clock.t, "failed"))
    handed: list = []
    workers = Workers(store, host_with(handed, fail=True), clock=clock)
    await workers.hand_owed()                                     # the chat could not take it yet
    assert workers.owed() == ["w3"]
    host = host_with(handed, ack=True)
    workers2 = Workers(store, host, clock=clock)
    host.holder["workers"] = workers2
    await workers2.hand_owed()
    assert workers2.owed() == [] and len(handed) == 1
