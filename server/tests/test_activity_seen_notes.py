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


def test_the_newest_chat_reply_failed_until_a_later_reply_works(tmp_path):
    """The apps' character shows the error face while the newest chat reply failed (even when someone watched it), not for a background
    task, and clears when a later chat reply works."""
    act = Activity(Store(tmp_path / "a.db"))
    assert act.last_reply_failed() is False
    chat = act.start_task("ask", source="chat")
    act.finish_task(chat, "failed", error="model", seen=True)
    assert act.last_reply_failed() is True
    bg = act.start_task("watch", source="watch")
    act.finish_task(bg, "done")
    assert act.last_reply_failed() is True                       # a background task does not count
    running = act.start_task("ask again", source="chat")
    assert act.last_reply_failed() is False                      # a newer chat reply is running
    act.finish_task(running, "done", answer="ok")
    assert act.last_reply_failed() is False
