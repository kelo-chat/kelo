"""One browser window per task (brief C1a): the tab model. Pure: fake pages only."""

import pytest

from assistant.tools.task_window import TaskWindow


class P:
    def __init__(self, url=""):
        self.url = url


def test_ids_are_never_reused_after_a_close():
    w = TaskWindow("task:w1")
    a, b = w.add(P()), w.add(P())
    w.remove(a.page)
    c = w.add(P())
    assert (a.id, b.id, c.id) == ("t1", "t2", "t3")


def test_removing_the_active_tab_goes_to_its_opener_when_it_is_still_open():
    w = TaskWindow("task:w1")
    first = w.add(P()); w.activate(first.id)
    popup = w.add(P("https://b"), opener=first.id); w.activate(popup.id)
    w.remove(popup.page)
    assert w.active == first.id


def test_removing_the_active_tab_without_an_open_opener_goes_to_the_last_tab():
    w = TaskWindow("task:w1")
    first = w.add(P()); w.activate(first.id)
    second = w.add(P(), opener=first.id)
    third = w.add(P())
    w.activate(second.id)
    w.remove(first.page)                 # the opener is gone: the active one is the second, which is removed next
    w.activate(second.id)
    w.remove(second.page)
    assert w.active == third.id


def test_removing_an_inactive_tab_keeps_the_active_one():
    w = TaskWindow("task:w1")
    a = w.add(P()); b = w.add(P()); w.activate(b.id)
    w.remove(a.page)
    assert w.active == b.id and w.active_tab() is b


def test_removing_the_last_tab_leaves_no_active_tab():
    w = TaskWindow("task:w1")
    a = w.add(P()); w.activate(a.id)
    assert w.remove(a.page) is a and w.active == "" and w.active_tab() is None
    assert w.remove(a.page) is None


def test_activating_an_unknown_tab_is_a_key_error():
    w = TaskWindow("task:w1")
    w.add(P())
    with pytest.raises(KeyError):
        w.activate("t9")


def test_the_listing_is_in_opening_order_with_the_active_one_marked():
    w = TaskWindow("task:w1")
    a = w.add(P("https://a")); b = w.add(P("https://b"), opener=a.id); c = w.add(P("https://c"))
    w.activate(b.id)
    assert w.listing() == [
        {"id": "t1", "url": "https://a", "active": False, "opener": ""},
        {"id": "t2", "url": "https://b", "active": True, "opener": "t1"},
        {"id": "t3", "url": "https://c", "active": False, "opener": ""},
    ]
    assert w.get("t3") is c and w.find(b.page) is b
