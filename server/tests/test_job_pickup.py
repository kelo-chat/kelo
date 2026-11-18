"""A cut-off job is picked up where it stopped, by itself, and never repeats a step that may already have happened (job_cuts.py,
the ledgers' cut handling, workers.py startup / pick-up / limit). Pure parts first, then the Workers behaviour with a fake clock and
the real server for a hard kill. No real model calls."""

import asyncio
import json
import time

import httpx

from assistant import job_cuts
from assistant.agent import Tool
from assistant.done_once import STARTED_DETAIL, ActionLedger
from assistant.pay_terms import AttemptJournal
from assistant.providers import Done, ToolCall
from assistant.server import create_app
from assistant.store import Store
from assistant.workers import HANDOFF_MARK, Limits, WorkerHost, Workers, _Recorder

from test_agent_profile import app_settings
from test_server import AUTH, Running
from test_workers import Brain, call, delegating, one_worker, tool_results, until, worker_list


def _asst(*calls):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}} for cid, name, args in calls]}


def _tool(cid, out="ok"):
    return {"role": "tool", "tool_call_id": cid, "content": out}


# ---------------------------------------------------------------------------------------------------------- pure
def test_open_calls_finds_a_call_without_a_result():
    msgs = [{"role": "user", "content": "go"}, _asst(("c1", "browser_read", {"x": 1}), ("c2", "browser_click", {"selector": "#a"})),
            _tool("c1", "read"), _asst(("c3", "email_send", {"to": "kim@x.org"}))]
    got = job_cuts.open_calls(msgs)
    assert [(c.call_id, c.tool) for c in got] == [("c2", "browser_click"), ("c3", "email_send")]
    assert got[0].args == {"selector": "#a"} and got[1].args == {"to": "kim@x.org"}


def test_open_calls_ignores_answered_calls():
    msgs = [_asst(("c1", "browser_read", {})), _tool("c1"), _asst(("c2", "browser_look", {})), _tool("c2")]
    assert job_cuts.open_calls(msgs) == []
    assert job_cuts.open_calls([]) == []


def test_cut_result_by_kind():
    read = job_cuts.OpenCall("c1", "browser_read", {})
    send = job_cuts.OpenCall("c2", "email_send", {})
    pay = job_cuts.OpenCall("c3", "checkout_submit", {})
    assert job_cuts.cut_result(read, {}, None) == job_cuts.CUT_READ
    assert job_cuts.cut_result(send, {"email_send": 17}, None) == job_cuts.CUT_ACT.format(id=17)
    assert "record 17" in job_cuts.cut_result(send, {"email_send": 17}, None)
    assert job_cuts.cut_result(pay, {"checkout_submit": 4}, "pt_1") == job_cuts.CUT_PAY
    # without a pay attempt a checkout_submit is judged like any step
    assert job_cuts.cut_result(pay, {}, None) == job_cuts.CUT_READ


def test_next_pickup_waits_0_60_300_then_stops():
    assert [job_cuts.next_pickup(n) for n in (1, 2, 3)] == [0.0, 60.0, 300.0]
    assert job_cuts.next_pickup(4) is None
    assert job_cuts.next_pickup(9) is None
    assert job_cuts.MAX_PICKUPS == 3


def test_pickup_note_tab_lines_and_checks():
    base = dict(at=1.0, why="restart", step=None, pay=None, url="https://shop.example.com/cart")
    kept = job_cuts.pickup_note(job_cuts.Cut(records=(), tab="kept", **base))
    assert job_cuts.TAB_KEPT in kept and "restarted while you were working" in kept and kept.endswith(job_cuts.PICKUP_TAIL)
    assert "CHECKS" not in kept and "Before anything that would repeat them" not in kept

    new = job_cuts.pickup_note(job_cuts.Cut(records=(), tab="new", **base))
    assert job_cuts.TAB_NEW.format(url="https://shop.example.com/cart") in new

    held = job_cuts.pickup_note(job_cuts.Cut(records=(), tab="held", **base))
    assert job_cuts.TAB_HELD in held

    none = job_cuts.pickup_note(job_cuts.Cut(records=(), tab="none", **base))
    assert job_cuts.TAB_KEPT not in none and job_cuts.TAB_NEW.split("{")[0] not in none and job_cuts.TAB_HELD not in none

    both = job_cuts.pickup_note(job_cuts.Cut(records=(5,), tab="new", texts=("email send to kim@x.org",), **{**base, "pay": "pt_9"}))
    assert "shop.example.com" in both                     # the pay check names the shop's site
    assert "record 5: email send to kim@x.org" in both
    assert job_cuts.PAY_CHECK.split("{")[0] in both
    assert both.endswith(job_cuts.PICKUP_TAIL)

    browser = job_cuts.pickup_note(job_cuts.Cut(records=(), tab="kept", **{**base, "why": "browser"}))
    assert "The browser restarted" in browser


def test_cut_round_trips_through_json():
    step = job_cuts.OpenCall("c9", "browser_click", {"selector": "#buy"})
    cut = job_cuts.Cut(at=12.5, why="restart", step=step, records=(3, 4), pay=None, tab="kept", url="https://a.example/x", texts=("a", "b"))
    back = job_cuts.Cut.of(cut.to_json())
    assert back == cut
    assert job_cuts.Cut.of("") is None and job_cuts.Cut.of("not json") is None


# ---------------------------------------------------------------------------------------------------------- ledgers
def test_a_reserved_press_from_before_the_start_becomes_unknown():
    store = Store(":memory:")
    now = [100.0]
    journal = AttemptJournal(store, lambda: now[0])
    approval = {"id": "pa_1", "terms_id": "ot_1", "merchant_origin": "https://shop.example.com"}
    aid = journal.reserve(approval, "task:wabc", "1")     # written before the press; the server is killed here
    now[0] = 200.0
    assert journal.cut_open(before=150.0) == 1
    row = journal.get(aid)
    assert row["state"] == "unknown" and "nobody saw" in row["evidence"]
    assert row["check_jobs"], "an unknown press gets its first later check"
    # a pressed attempt is already blocking and stays as it is
    aid2 = journal.reserve(approval, "task:wabc", "1")
    journal.pressed(aid2)
    assert journal.cut_open(before=150.0) == 0 and journal.get(aid2)["state"] == "pressed"


def test_a_cut_pay_press_is_seen_by_the_picked_up_run_on_the_same_tab():
    store = Store(":memory:")
    journal = AttemptJournal(store, lambda: 10.0)
    approval = {"id": "pa_2", "terms_id": "ot_2", "merchant_origin": "https://shop.example.com"}
    aid = journal.reserve(approval, "task:wabc", "1")
    journal.pressed(aid)                                  # task 1 (the cut run) pressed
    # the picked-up run is task 2 of the same job: the old press still blocks it, on any task of this tab
    assert journal.open("task:wabc", "2") is None
    assert journal.open("task:wabc", "2", any_task=True)["id"] == aid
    assert journal.open_for_tab("task:wabc")["id"] == aid
    assert journal.open_for_tab("16") is None             # a chat tab keeps its own rule
    # a settled attempt stops blocking
    journal.settle(aid, "not_placed", "the order page shows an empty cart")
    assert journal.open_for_tab("task:wabc") is None


def test_the_action_ledger_marks_cut_records_and_moves_them_to_the_picked_up_run():
    store = Store(":memory:")
    now = [100.0]
    ledger = ActionLedger(store, clock=lambda: now[0])
    rid = ledger.record(run_id="1", task_id=1, turn_id="t1", source="task", tool="email_send", kind="send", connector="mail",
                        targets=["kim@x.org"], fingerprint="fp1", state="unknown", detail=STARTED_DETAIL)
    done = ledger.record(run_id="1", task_id=1, turn_id="t1", source="task", tool="email_send", kind="send", connector="mail",
                         targets=["a@x.org"], fingerprint="fp3", state="done")
    now[0] = 130.0                                       # the server started at 120: a step of the live run is after that
    other = ledger.record(run_id="2", task_id=2, turn_id="t2", source="task", tool="email_send", kind="send", connector="mail",
                          targets=["lee@x.org"], fingerprint="fp2", state="unknown", detail=STARTED_DETAIL)
    now[0] = 150.0
    cut = ledger.cut_open(before=120.0)
    assert [r.id for r in cut] == [rid]
    assert ledger.get(rid).state == "unknown" and ledger.get(rid).detail == "cut off by a restart"
    assert ledger.get(other).detail == "started, no answer yet"        # not from the cut run: a live run, untouched
    assert ledger.get(done).state == "done"
    assert [r.id for r in ledger.of_task(1)] == [rid]                   # the cut run's unknown records
    assert ledger.of_task(None) == []
    ledger.move_run([rid], "9", 9)
    assert [r.id for r in ledger.of_task(9)] == [rid] and ledger.get(rid).run_id == "9"
    assert ledger.of_task(1) == []
    assert [r.id for r in ledger.open_unknown("9")] == [rid]            # run_ended of the picked-up run reports it


def test_the_done_once_busy_flag_is_true_while_a_consequential_step_is_in_flight():
    from assistant.done_once import DoneOnce
    store = Store(":memory:")
    once = DoneOnce(ActionLedger(store), effect=lambda tool, args: None)
    assert once.busy() is False
    once.inflight.add(5)
    assert once.busy() is True
    once.inflight.discard(5)
    assert once.busy() is False


def test_the_attempt_journal_busy_flag_is_true_between_reserve_and_pressed():
    store = Store(":memory:")
    journal = AttemptJournal(store, lambda: 1.0)
    approval = {"id": "pa_3", "terms_id": "ot_3", "merchant_origin": "https://s.example"}
    aid = journal.reserve(approval, "task:wx", "1")
    assert journal.busy() is True
    journal.pressed(aid)
    assert journal.busy() is False


# ---------------------------------------------------------------------------------------------------------- Workers, no loop
async def _quiet(*_a, **_k):
    return None


def _boom(*_a, **_k):
    raise RuntimeError("no model in this test")


def _bare(clock_now=1000.0, host_kw=None, store=None):
    now = [clock_now]
    host = WorkerHost(start_turn=_boom, cancel_turn=lambda t: None, handoff=_quiet, emit=_quiet, tools=lambda: [], **(host_kw or {}))
    store = store if store is not None else Store(":memory:")
    return Workers(store, host, Limits(), resume_on_start=False, clock=lambda: now[0]), store, now


def _job(store, wid, status="running", cuts=0, **extra):
    cols = {"id": wid, "kind": "browser", "title": "shop", "task": "t", "url": "https://shop.example.com/cart", "status": status,
            "tab": f"task:{wid}", "created_at": 1.0, "updated_at": 1.0, "max_steps": 10, "deadline_at": 9e9, "minutes": 5,
            "token_cap": 1000, "cuts": cuts, "last_url": "https://shop.example.com/cart", "auto_resume": 1 if status == "interrupted" else 0,
            **extra}
    store._run("INSERT INTO workers(" + ", ".join(cols) + ") VALUES(" + ",".join("?" * len(cols)) + ")", tuple(cols.values()))
    store._run("INSERT INTO worker_messages(worker_id, seq, message) VALUES(?,?,?)", (wid, 0, json.dumps({"role": "user", "content": "go"})))


def _kill(store, wid, *calls):
    """A hard kill: the rows say running, and the transcript ends with the calls that never got a result."""
    seq = store._all("SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM worker_messages WHERE worker_id = ?", (wid,))[0]["n"]
    store._run("UPDATE workers SET status = 'running', auto_resume = 0 WHERE id = ?", (wid,))
    for i, (cid, name) in enumerate(calls):
        store._run("INSERT INTO worker_messages(worker_id, seq, message) VALUES(?,?,?)",
                   (wid, seq + i, json.dumps(_asst((cid, name, {})))))


def test_a_restart_cuts_the_running_job_and_answers_its_open_call(tmp_path):
    ws, store, now = _bare()
    _job(store, "w1")
    _kill(store, "w1", ("c-kill", "browser_click"))
    ws.startup()
    msgs = ws.transcript("w1")
    assert msgs[-1] == {"role": "tool", "tool_call_id": "c-kill", "content": job_cuts.CUT_READ}   # the open call gets a cut result
    row = store._all("SELECT * FROM workers WHERE id = 'w1'")[0]
    assert row["status"] == "interrupted" and row["cuts"] == 1
    cut = job_cuts.Cut.of(row["cut"])
    assert cut.why == "restart" and cut.step.tool == "browser_click" and cut.tab == "new"   # a last url and no open page: a new tab
    assert json.loads(row["wait_on"]) == {"kind": "cut", "until": 1000.0}     # the first pick-up is at once


def test_three_cuts_in_a_row_wait_0_60_300_and_the_fourth_stops_the_job():
    _, store, _ = _bare()   # the tables exist once a Workers is made on the database
    _job(store, "w2")
    waits = []
    for n in range(1, 5):
        ws, _, now = _bare(store=store)   # each start is a new process: a new Workers on the same database
        _kill(store, "w2", (f"c-{n}", "browser_read"))
        ws.startup()
        row = store._all("SELECT * FROM workers WHERE id = 'w2'")[0]
        assert row["cuts"] == n
        if row["status"] == "blocked":
            waits.append(None)
            break
        waits.append(json.loads(row["wait_on"])["until"] - now[0])
        now[0] += 1.0
    assert waits == [0.0, 60.0, 300.0, None]
    row = store._all("SELECT * FROM workers WHERE id = 'w2'")[0]
    assert row["status"] == "blocked" and json.loads(row["report"])["status"] == "blocked"
    assert "4" in json.loads(row["report"])["summary"]           # the line names how many times it was cut
    assert row["cuts"] == 4


def test_a_finished_tool_call_after_a_pick_up_resets_the_cut_count():
    ws, store, _ = _bare()
    _job(store, "w3", status="running", cuts=2)
    rec = _Recorder(ws, "w3", 1, None)
    rec({"type": "tool_started", "id": "x1", "name": "browser_read", "arguments": {}})
    rec({"type": "tool_result", "id": "x1", "name": "browser_read", "output": "ok", "is_error": False})
    assert store._all("SELECT cuts FROM workers WHERE id = 'w3'")[0]["cuts"] == 0


async def test_a_manual_resume_resets_the_cut_count():
    ws, store, _ = _bare()
    _job(store, "w4", status="interrupted", cuts=2, auto_resume=0)
    ok, how = ws.resume("w4", "", why="The user resumed you from the app.")
    assert ok and how == "resumed"
    assert store._all("SELECT cuts FROM workers WHERE id = 'w4'")[0]["cuts"] == 0
    await asyncio.sleep(0.05)   # the job's own task ends (its start fails in this test): no task is left behind


def test_graceful_stop_waits_for_a_consequential_step_in_flight():
    import time as _time
    state = {"until": _time.monotonic() + 0.6}

    def busy():
        return _time.monotonic() < state["until"]

    ws, store, _ = _bare(host_kw={"step_busy": busy})
    started = _time.monotonic()
    asyncio.run(ws.shutdown())
    waited = _time.monotonic() - started
    assert 0.4 <= waited < 5.0, waited           # the step finished before the cut; the drain is capped at DRAIN_SECONDS


def test_public_shows_a_picked_up_job_and_the_cut_wait():
    ws, store, now = _bare()
    _job(store, "w5", status="running")
    store._run("UPDATE workers SET picked_up_at = ?, pickups = 1, cut = ? WHERE id = 'w5'",
               (1000.0, job_cuts.Cut(at=1.0, why="browser", step=None, records=(), pay=None, tab="new", url="").to_json()))
    row = store._all("SELECT * FROM workers WHERE id = 'w5'")[0]
    assert ws.public(row)["picked_up"] == {"at": 1000.0, "why": "browser"}
    _job(store, "w6", status="interrupted", cuts=1)
    store._run("UPDATE workers SET wait_on = ? WHERE id = 'w6'", (json.dumps({"kind": "cut", "until": 1090.0}),))
    pub = ws.public(store._all("SELECT * FROM workers WHERE id = 'w6'")[0])
    assert pub["waiting_for"] == "cut" and pub["wake_at"] == 1090.0 and pub["status"] == "interrupted"


def test_handoff_says_it_was_picked_up():
    ws, store, _ = _bare()
    base = {"id": "w7", "title": "shop", "kind": "browser", "parent_source": "chat", "steps": 3, "last_url": None}
    rep = {"status": "done", "summary": "ok", "details": ""}
    assert "picked up where it stopped" not in ws._handoff_text({**base, "pickups": 0}, rep, {})
    assert "picked up where it stopped by itself" in ws._handoff_text({**base, "pickups": 2}, rep, {})


def test_a_job_running_on_a_shop_counts_as_a_step_for_the_auto_update():
    ws, store, _ = _bare(host_kw={"step_busy": lambda: True})
    assert ws.step_busy() is True
    ws2, _, _ = _bare()     # no step in flight and no checkout job running
    assert ws2.step_busy() is False


# ---------------------------------------------------------------------------------------------------------- the real server
async def test_a_hard_kill_mid_step_is_picked_up_with_a_result_for_the_cut_call(tmp_path):
    settings = app_settings(tmp_path)
    stuck = asyncio.Event()

    async def note(args):
        return "first page read: 3 items"

    tool = Tool("browser_read", "read", {"type": "object"}, note, read_only=True)

    async def worker1(req, n):
        if n == 0:
            return call("browser_read", {}, cid="c-first")
        stuck.set()
        await asyncio.Event().wait()
        return []

    brain1 = Brain(chat=delegating({"task": "여러 페이지를 읽어줘", "kind": "general"}), worker=worker1)
    async with Running(create_app(settings, tools=[tool], stream_fn=brain1, with_worker_tools=True)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        from test_server import follow
        await follow(c, (await c.post("/api/turns", json={"text": "읽어줘"})).json()["id"])
        await until(lambda: stuck.is_set())
        wid = (await one_worker(c))["id"]

    # the process died: no graceful stop wrote anything, so the row still says running and the step has no result
    store = Store(settings.db_path)
    _kill(store, wid, ("c-kill", "browser_read"))

    seen = []

    def worker2(req, n):
        seen.append(req)
        return call("report", {"status": "done", "summary": "다 읽었어요."}, cid="c-end")

    brain2 = Brain(worker=worker2)
    async with Running(create_app(settings, tools=[tool], stream_fn=brain2, with_worker_tools=True)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await one_worker(c, "done")
        req = seen[0]
        results = [m for m in req.messages if m.get("role") == "tool" and m.get("tool_call_id") == "c-kill"]
        assert results and results[0]["content"] == job_cuts.CUT_READ
        last = req.messages[-1]["content"]
        assert "restarted while you were working" in last
        assert job_cuts.PICKUP_TAIL in last
        pub = (await c.get(f"/api/workers/{wid}")).json()
        assert pub.get("picked_up") is None      # done: the line is for a running job only


# ---------------------------------------------------------------------------------------------------------- the browser: tab book, a lost page
class _Target:
    """A DevTools session on one fake page: Target.getTargetInfo answers the page's target id."""

    def __init__(self, target: str):
        self.target = target

    async def send(self, method, params=None):
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"targetId": self.target, "browserContextId": "ctx"}}
        return {}

    async def detach(self):
        return None


class _Page:
    def __init__(self, url: str, target: str = ""):
        self.url, self.target, self.closed, self.handlers = url, target, False, {}

    def is_closed(self):
        return self.closed

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    async def close(self):
        self.closed = True


class _Ctx:
    def __init__(self, pages):
        self.pages = list(pages)

    async def new_page(self):
        p = _Page("about:blank", f"new-{len(self.pages)}")
        self.pages.append(p)
        return p


def _session(book, pages):
    from assistant.tools.browser import BrowserSession

    async def cdp(page):
        return _Target(page.target)

    sess = BrowserSession(connect=None, cdp=cdp, resolver=lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    sess.book = book
    sess.prepare_pages = False

    async def no_connect():
        return None

    sess._ensure_browser = no_connect
    sess._ctx = _Ctx(pages)
    return sess


def test_the_tab_book_re_adopts_the_same_page_after_a_restart_and_closes_the_leftovers():
    from assistant.tools.tab_book import TabBook
    store = Store(":memory:")
    book = TabBook(store)
    book.save("task:w1", "T1", "https://shop.example.com/cart")
    book.save("task:w9", "T9", "https://gone.example.com/")
    shop = _Page("https://shop.example.com/cart", "T1")
    leftover = _Page("https://old.example.com/x", "T5")
    sess = _session(book, [shop, leftover])
    out = asyncio.run(sess.adopt_after_restart())
    assert out == {"task:w1": "kept", "task:w9": "gone"}
    assert sess._pages["task:w1"] is shop and sess.tab_state("task:w1") == "kept"
    assert "task:w9" not in book.all()                       # its page is gone: forgotten
    assert sess.tab_state("task:w9") == "new"
    assert leftover.closed                                   # a leftover of no tab is closed
    assert not shop.closed


def test_a_new_key_never_takes_another_keys_leftover_page():
    from assistant.tools.browser import browser_owner
    from assistant.tools.tab_book import TabBook
    store = Store(":memory:")
    other_job = _Page("https://other.example.com/basket", "T7")   # another job's page, still open in the browser
    sess = _session(TabBook(store), [other_job])
    sess.prepare_pages = False
    token = browser_owner.set("task:w2")
    try:
        got = asyncio.run(sess.page())
    finally:
        browser_owner.reset(token)
    assert got is not other_job and got.url == "about:blank"
    assert sess._pages["task:w2"] is got


def test_private_tabs_are_not_booked():
    from assistant.tools.tab_book import TabBook
    store = Store(":memory:")
    book = TabBook(store)
    sess = _session(book, [])
    sess._private["task:w3"] = object()                      # a private tab: its own context ends with the connection
    page = _Page("https://shop.example.com/", "T3")
    asyncio.run(sess._prepare(page, "task:w3"))
    assert book.all() == {}


def test_a_lost_browser_gives_a_note_and_the_first_click_reads_first():
    from assistant.tools.browser import LOST_ACT_NOTE, browser_owner
    from assistant.tools.tab_book import TabBook
    store = Store(":memory:")
    sess = _session(TabBook(store), [])
    told = []
    sess.on_lost = told.append
    page = _Page("https://shop.example.com/cart", "T4")
    sess._pages["task:w4"] = page
    sess._watch(page, "task:w4")
    for fn in page.handlers.get("crash", []):                # the page's renderer crashed (the browser lost it)
        fn()
    assert "task:w4" not in sess._pages and sess._lost["task:w4"] == "https://shop.example.com/cart"
    assert told == ["task:w4"]
    token = browser_owner.set("task:w4")
    try:
        got = asyncio.run(sess.page())                       # the next step gets a new blank page with the note
        assert got.url == "about:blank"
        assert any("browser restarted" in n for n in sess._notes["task:w4"])
        assert asyncio.run(sess.hold_to_act()) == LOST_ACT_NOTE   # the first click / type is refused until the page is read
    finally:
        browser_owner.reset(token)


def test_a_job_card_says_the_browser_went_on_in_a_new_tab(tmp_path):
    ws, store, _ = _bare()
    _job(store, "w8", status="running")
    ws.browser_lost("task:w8")
    lines = json.loads(store._all("SELECT lines FROM workers WHERE id = 'w8'")[0]["lines"])
    assert lines and "new tab" in lines[-1]["text"]


def test_auto_update_waits_while_a_job_step_is_in_flight():
    from test_server_updates import make, ts
    now = ts(2026, 11, 10, 4, 40)
    flying = [True]
    su, *_ = make(last_use=lambda: now - 3600, busy=lambda: flying[0])
    su.store.set_meta("nightly:last", "2026-11-09")
    assert su.due(now) is False
    flying[0] = False
    assert su.due(now) is True
