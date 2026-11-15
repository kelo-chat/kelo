"""A long run tells once how far it got (run_progress.py, backlog 66)."""

from assistant import run_progress
from assistant.run_progress import RunProgress


def test_once_per_run_after_the_wait(monkeypatch):
    now = [0.0]
    p = RunProgress(clock=lambda: now[0])
    k = p.key("t1", None)
    p.begin(k)
    assert p.add(k) == 1 and not p.due(k)           # too early
    now[0] = run_progress.PROGRESS_AFTER + 1
    assert p.add(k) == 2 and p.due(k)
    assert p.add(k) == 3 and not p.due(k)           # never twice
    p.end(k)
    assert not p.due(k)


def test_nothing_without_an_application(monkeypatch):
    now = [0.0]
    p = RunProgress(clock=lambda: now[0])
    k = p.key("t1", None)
    p.begin(k)
    now[0] = 10_000
    assert not p.due(k)


def test_a_job_is_one_run_over_its_segments():
    now = [0.0]
    p = RunProgress(clock=lambda: now[0])
    k = p.key("turn-a", "w1")
    assert k == p.key("turn-b", "w1") == "job:w1"
    p.begin(k)
    p.add(k)
    now[0] = 300
    p.begin(k)                                       # the next segment keeps the first segment's clock
    p.add(k)
    now[0] = run_progress.PROGRESS_AFTER + 1
    assert p.add(k) == 3 and p.due(k)


def test_the_wait_is_read_at_each_call(monkeypatch):
    monkeypatch.setattr(run_progress, "PROGRESS_AFTER", 0.0)
    p = RunProgress(clock=lambda: 5.0)
    k = p.key("t", None)
    p.begin(k)
    p.add(k)
    assert p.due(k)
