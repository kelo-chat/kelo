"""Background work while Keyguard is locked (keyguard.py): one general rule. Jobs that need a key or a sealed secret wait quietly
(not run, not marked done) and run once at the first tick after the unlock if still due; housekeeping keeps running; work deferred
with after_unlock runs right after the unlock; Kelo's own working keys (push install secret, ...) stay usable while locked."""

import asyncio
import base64
import logging
import os

import pytest

from assistant import keyguard as kg_mod
from assistant.keyguard import Locked
from assistant.schedules import Schedules, Scheduler, _quiet
from assistant.store import Store

BLOB = base64.b64encode(b"\x01" + os.urandom(60)).decode()


@pytest.fixture(autouse=True)
def quick_argon(monkeypatch):
    monkeypatch.setattr(kg_mod, "ARGON", {"iterations": 1, "lanes": 1, "memory_cost": 8 * 1024})


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


async def test_jobs_that_need_a_key_wait_and_run_once_after_the_unlock(tmp_path, caplog):
    store = Store(tmp_path / "s.db")
    clock = Clock(1_800_000_000.0)
    sch = Scheduler(Schedules(store), clock=clock)
    ran: list[str] = []

    def job(name):
        async def run():
            ran.append(name)
        return run
    sch.add_every("briefing", 60, job("briefing"))
    sch.add_every("storage_watch", 60, job("storage_watch"), needs_secrets=False)
    store.set_meta("daily:journal", "2000-01-01")                       # due long ago
    sch.add_daily("journal", 0, 0, job("journal"))
    locked = [True]
    sch.paused = lambda: locked[0]
    sch.tick()                                                          # first tick: periods start
    clock.t += 61
    with caplog.at_level(logging.INFO, logger="assistant.schedules"):
        for _ in range(3):
            sch.tick()
            await asyncio.sleep(0)
            clock.t += 61
    assert ran.count("storage_watch") == 3 and "briefing" not in ran and "journal" not in ran   # housekeeping only
    assert store.meta("daily:journal") == "2000-01-01"                 # not marked done while it waited
    assert sum("wait for the unlock" in r.getMessage() for r in caplog.records) <= 1   # said once, never anything secret
    locked[0] = False
    sch.tick()
    await asyncio.sleep(0)
    assert ran.count("briefing") == 1 and ran.count("journal") == 1     # at the first tick after the unlock
    sch.tick()
    await asyncio.sleep(0)
    assert ran.count("briefing") == 1 and ran.count("journal") == 1     # once, then its normal period again


async def test_a_job_that_still_meets_the_lock_ends_quietly(caplog):
    async def needs_key():
        raise Locked("the server is locked")
    with caplog.at_level(logging.INFO):
        await _quiet("proactive", needs_key)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_after_unlock_runs_waiting_work_once_and_system_keys_work_while_locked(tmp_path):
    store = Store(tmp_path / "s.db")
    store.set_key("openai", "sk-provider-secret")
    store.set_key("push:install_id", "install-secret")                  # Kelo's own working key (the unlock push needs it)
    got = store.keyguard.start_enroll(1)
    store.keyguard.finish_enroll(store, 1, got["enrollment_id"], 1, BLOB)
    store2 = Store(tmp_path / "s.db")
    assert store2.keyguard.locked
    assert store2.key("push:install_id") == "install-secret"           # not under Keyguard
    with pytest.raises(Locked):
        store2.key("openai")
    done: list[str] = []

    async def later():
        done.append("async")
    store2.keyguard.after_unlock(lambda: done.append("sync"))
    store2.keyguard.after_unlock(later)
    assert done == []
    store2.keyguard.unlock(got["key"])
    await asyncio.sleep(0)
    assert sorted(done) == ["async", "sync"]
    store2.keyguard.after_unlock(lambda: done.append("now"))           # already unlocked: at once
    assert done[-1] == "now"
    assert store2.key("openai") == "sk-provider-secret"
