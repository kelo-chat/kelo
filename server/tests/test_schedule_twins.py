"""2026-11-18 (watcher #3/#4 and the repeated request): an agent asked for the same job twice saves one. The user's own schedules are
never merged (created_by "user"): a second one they make on purpose is theirs to keep."""

from assistant.schedules import Schedules
from assistant.store import Store


def _schedules(tmp_path):
    return Schedules(Store(tmp_path / "s.db"))


def test_the_same_job_asked_again_by_the_agent_is_the_one_saved(tmp_path):
    sch = _schedules(tmp_path)
    first = sch.create("Price", "check the price", every_minutes=30, created_by="agent")
    again = sch.create("Price (renamed)", "check the price", every_minutes=30, created_by="agent")
    assert again["id"] == first["id"] and again.get("twin") is True and again["name"] == "Price (renamed)"
    assert len(sch.list()) == 1


def test_a_different_time_prompt_or_zone_is_another_job(tmp_path):
    sch = _schedules(tmp_path)
    a = sch.create("Price", "check the price", every_minutes=30, created_by="agent")
    b = sch.create("Price", "check the price", every_minutes=60, created_by="agent")
    c = sch.create("Price", "check the stock", every_minutes=30, created_by="agent")
    assert len({a["id"], b["id"], c["id"]}) == 3 and not b.get("twin") and not c.get("twin")


def test_the_users_own_second_schedule_is_not_merged(tmp_path):
    sch = _schedules(tmp_path)
    a = sch.create("Price", "check the price", every_minutes=30)                      # created_by "user"
    b = sch.create("Price", "check the price", every_minutes=30)
    assert a["id"] != b["id"] and not b.get("twin")


def test_a_deleted_job_is_not_a_twin(tmp_path):
    sch = _schedules(tmp_path)
    a = sch.create("Price", "check the price", every_minutes=30, created_by="agent")
    sch.store._run("UPDATE schedules SET archived_at = ? WHERE id = ?", (1.0, a["id"]))
    b = sch.create("Price", "check the price", every_minutes=30, created_by="agent")
    assert b["id"] != a["id"] and not b.get("twin")
