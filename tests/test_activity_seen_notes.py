"""Looking at finished tasks also clears their task notifications, so the header's news dot goes away."""
from assistant.activity import Activity
from assistant.store import Store


def test_seen_task_clears_its_notification(tmp_path):
    act = Activity(Store(tmp_path / "a.db"))
    tid = act.start_task("check a page")
    act.finish_task(tid, "done", answer="ok")
    act.notify("task_done", "done", task_id=tid, push=False)
    act.notify("approval", "allow?", task_id=tid, approval_id="ap1", push=False)
    assert act.unseen_tasks() == 1 and act.unseen_notifications() == 2
    act.mark_tasks_seen(tid)
    assert act.unseen_tasks() == 0
    assert act.unseen_notifications() == 1   # the approval's own note stays until it is answered
