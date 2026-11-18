"""The memory pass driver (memory_upkeep.py, docs/memory-pipeline.md steps 1-15) end to end with one fake model that answers every
step by its prompt: claims admitted -> a MEMORY.md line promoted with its claim, a past event retired and not reframed, the dust case
(nothing changes), the usage limit (pending, then retry, also between claims and reconcile), undo of the whole pass, and no model call
when nothing is due. No request ever leaves the process."""

import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from assistant.activity import Activity
from assistant.home import Home
from assistant.memory_inbox import MemoryInbox
from assistant.memory_upkeep import BACKUPS, IDLE_BEFORE_RUN, MemoryUpkeep
from assistant.providers import Done, TextDelta
from assistant.providers.types import QuotaExhausted
from assistant.store import Store

SEOUL = ZoneInfo("Asia/Seoul")
MEM = ("# MEMORY.md\n\n## About the user\n- Likes tea\n\n## Ongoing\n- Dentist visit is on 2026-11-10. Parking is behind the clinic.\n\n"
       "## Decided & done\n")
CLEAN = "# MEMORY.md\n\n## About the user\n- Likes tea\n\n## Ongoing\n\n## Decided & done\n"


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


KINDS = (("# Extract Claims", "extract"), ("# Verify Claims", "verify"), ("You reconcile one long-term", "reconcile"),
         ("You review proposed edits", "review"), ("independent preservation review", "preservation"))


def kind_of(req) -> str:
    sysmsg = req.messages[0]["content"]
    return next((k for needle, k in KINDS if needle in sysmsg), "other")


def brain(**steps):
    """A fake model: each step (extract, verify, reconcile, review, preservation) is a dict, an exception, or a function of the request
    (returning either). Unset steps: extract finds nothing, verify passes everything, reconcile and review leave things alone/pass."""
    calls: list = []

    def default(kind, req):
        if kind == "extract":
            return {"claims": []}
        if kind == "verify":
            ids = [b.split("\n")[0].strip() for b in req.messages[-1]["content"].split("### Claim ID: ")[1:]]
            return {"verdicts": [{"claim_id": i, "ok": True, "lens": lens, "reason": ""} for i in ids for lens in ("refute", "evidence")]}
        if kind in ("review", "preservation"):
            lenses = ("evidence", "temporal", "completeness", "preservation")
            return {"verdicts": [{"lens": lens, "verdict": "pass", "reason": "", "evidence": []} for lens in lenses],
                    "verdict": "pass", "reason": ""}
        return {"no_change": "nothing to do"}

    async def fn(provider, req, **kw):
        kind = kind_of(req)
        calls.append((kind, req))
        step = steps.get(kind)
        out = step(req) if callable(step) else (step if step is not None else default(kind, req))
        if isinstance(out, Exception):
            raise out
        yield TextDelta(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))
        yield Done("stop")
    fn.calls = calls
    fn.kinds = lambda: [k for k, _ in calls]
    return fn


def setup(tmp_path, fn, *, now=ts(2026, 11, 16, 4, 0), memory=CLEAN, swept=True):
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m", user_name="Mina")
    home = Home(tmp_path / "home")
    clock = Clock(now)
    inbox = MemoryInbox(home, tz=lambda: "Asia/Seoul", clock=clock)
    up = MemoryUpkeep(store, home, Activity(store, clock), fn, lambda: ("p", "m"), enabled=lambda: True, clock=clock, inbox=inbox,
                      last_use=lambda: 0.0)
    if memory is not None:
        home.write("MEMORY.md", memory, by="system")
    if swept:
        store.set_meta("memory:last_sweep_at", repr(now - 3600))
    return store, home, up, clock, inbox


def say(store, at, role, text, author=None) -> int:
    """A message row. The owner's own user messages carry author 'owner' (owner_messages.py); pass author="" for a hidden row."""
    author = ("owner" if role == "user" else "") if author is None else author
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,?)", (role, text, at, author))
    return store._all("SELECT MAX(id) AS n FROM messages")[0]["n"]


def read(home, rel):
    return (home.root / rel).read_text(encoding="utf-8")


def sha(home, rel):
    p = home.root / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def claim_of(mid, text, quote, kind="preference"):
    return {"kind": kind, "salience": "high", "text": text, "quote": quote, "speaker": "user", "handle": f"m{mid}", "secret": False}


def claim_id_in(req) -> str:
    return re.search(r"id:(c[0-9a-f]{10})", req.messages[-1]["content"]).group(1)


def plan_for(req):
    """Retire the passed dentist date (deleting its sentence, no outcome written) and promote the new claim under About the user."""
    cid = claim_id_in(req)
    return {"edits": [
        {"handle": "edit:1", "op": "insert_after", "start": 4, "end": 4, "guard": "- Likes tea", "reason": "promote",
         "replacement": "- Takes tea without sugar", "evidence": [f"claim:{cid}"],
         "origin": {"source_kind": "user", "source": "m1", "user_request": "I always take my tea without sugar"}},
        {"handle": "edit:2", "op": "replace", "start": 7, "end": 7, "guard": "- Dentist visit", "reason": "retire_expired",
         "replacement": "- Parking is behind the clinic.", "evidence": []}]}


# ---------------------------------------------------------------------------------------------------------- end to end
async def test_claims_are_admitted_the_line_is_promoted_with_its_claim_and_a_past_event_is_retired_not_reframed(tmp_path):
    fn = brain(extract=lambda req: {"claims": [claim_of(1, "Takes tea without sugar", "I always take my tea without sugar")]},
               reconcile=plan_for)
    store, home, up, clock, inbox = setup(tmp_path, fn, memory=MEM)
    say(store, clock.t - 3600, "user", "I always take my tea without sugar")
    out = await up.run(trigger="nightly")
    assert out["status"] == "done" and out["files"] == ["MEMORY.md"] and out["trigger"] == "nightly"
    assert fn.kinds() == ["extract", "verify", "reconcile", "review", "preservation"]   # the edits touch most of a short file: step 11 ran
    mem = read(home, "MEMORY.md")
    assert "- Takes tea without sugar" in mem and "Dentist" not in mem and "- Parking is behind the clinic." in mem
    assert not re.search(r"went|attended|completed|done:", mem, re.I)                  # a passed date retires wording, never an outcome
    [row] = store._all("SELECT claim_id, status, text FROM memory_claims")
    assert row["status"] == "active" and row["text"] == "Takes tea without sugar"
    log = read(home, "memory/log/2026-11-16.md")
    assert f"claim:{row['claim_id']}" in log and "I always take my tea without sugar" in log
    # the receipt and the Records page line
    stats = out["stats"]
    assert (stats["claims_extracted"], stats["claims_admitted"], stats["claims_dropped"]) == (1, 1, 0)
    assert stats["edits_proposed"] == 2 and stats["edits_grounded"] == 2 and stats["edits_applied"] == 2 and stats["edits_withheld"] == 0
    assert stats["model_requests"] == len(fn.calls) == 5 and stats["bytes"]["MEMORY.md"] == [len(MEM), len(mem)]
    [run] = store._all("SELECT run_id, outcome, stats FROM memory_runs")
    assert run["outcome"] == "changed" and json.loads(run["stats"])["claims_admitted"] == 1
    assert store.meta("memory:last_sweep_at") == repr(clock.t) and store.meta("memory:seen_until")
    pub = up.public()["last_pass"]
    assert pub["files"] == ["MEMORY.md"] and pub["can_undo"] is True and pub["stats"]["edits_applied"] == 2
    assert up.changes()["changes"][0]["file"] == "MEMORY.md"
    assert not (home.root / BACKUPS / out["id"] / "stage").exists()


async def test_the_dust_case_end_to_end_changes_nothing_in_the_files(tmp_path):
    sent = ts(2026, 11, 16, 3, 30)
    claim = claim_of(1, "The air on 2026-11-15 was bad all day", "the air was bad all day", "fact")

    def verify(req):
        body = req.messages[-1]["content"]
        refuse = "2026-11-15" in body and "written 2026-11-16" in body          # a claim about the 15th resting on data of the 16th
        ids = [b.split("\n")[0].strip() for b in body.split("### Claim ID: ")[1:]]
        return {"verdicts": [{"claim_id": i, "ok": not refuse, "lens": lens, "reason": "next-day data" if refuse else ""}
                             for i in ids for lens in ("refute", "evidence")]}
    fn = brain(extract={"claims": [claim]}, verify=verify)
    store, home, up, clock, inbox = setup(tmp_path, fn, memory=MEM, swept=False)
    say(store, sent, "user", "This morning the car is covered in dust, the air was bad all day.")
    files = ("user.md", "MEMORY.md", "AGENTS.md")
    before = {f: sha(home, f) for f in files}
    out = await up.run()
    assert out["status"] == "done" and out["files"] == [] and out["stats"]["claims_admitted"] == 0 and out["stats"]["claims_dropped"] == 1
    assert {f: sha(home, f) for f in files} == before
    assert not (home.root / "memory/log/2026-11-16.md").exists() and store._all("SELECT * FROM memory_claims") == []
    assert "review" not in fn.kinds() and up.public()["last_pass"] is None and up.backups() == []
    assert len(store._all("SELECT * FROM memory_runs")) == 1


async def test_nothing_new_and_nothing_due_makes_no_model_call(tmp_path):
    fn = brain()
    store, home, up, clock, inbox = setup(tmp_path, fn)
    out = await up.run()
    assert out["status"] == "skipped" and out["code"] == "no_news" and fn.calls == []
    assert store._all("SELECT outcome FROM memory_runs")[0]["outcome"] == "no_change"


async def test_a_file_that_is_due_is_reconciled_without_new_messages_and_without_a_claims_request(tmp_path):
    fn = brain(reconcile={"no_change": "the dated line still holds"})
    store, home, up, clock, inbox = setup(tmp_path, fn, memory=MEM)           # a date before today makes the file due
    out = await up.run(trigger="nightly")
    assert out["status"] == "done" and fn.kinds() == ["reconcile"] and read(home, "MEMORY.md") == MEM
    assert out["stats"]["claims_extracted"] == 0 and store.meta("memory:last_sweep_at") == repr(clock.t)


async def test_switched_off_means_no_pass_and_no_model_call(tmp_path):
    fn = brain()
    store, home, up, clock, inbox = setup(tmp_path, fn, memory=MEM, swept=False)
    store.set_meta("reflection:enabled", "0")
    up._enabled = lambda: False
    say(store, clock.t - 3600, "user", "hello")
    for out in (await up.run(), await up.nightly()):
        assert out["status"] == "skipped" and out["code"] == "switched_off"
    assert fn.calls == [] and read(home, "MEMORY.md") == MEM


# ---------------------------------------------------------------------------------------------------------- usage limit
async def test_the_usage_limit_keeps_the_pass_pending_and_the_retry_runs_it(tmp_path):
    now = ts(2026, 11, 16, 4, 0)
    state = {"limited": True}

    def extract(req):
        if state["limited"]:
            return QuotaExhausted("p: used up", provider="p", reset_at=now + 3600, until=now + 3630)
        return {"claims": [claim_of(1, "Takes tea without sugar", "I always take my tea without sugar")]}
    fn = brain(extract=extract, reconcile=plan_for)
    store, home, up, clock, inbox = setup(tmp_path, fn, now=now, memory=MEM)
    up.last_use = lambda: now - IDLE_BEFORE_RUN - 1
    say(store, now - 7200, "user", "I always take my tea without sugar")
    first = await up.run(trigger="nightly")
    assert first["status"] == "pending" and first["code"] == "model_limit" and first["retry_at"] == now + 3630
    assert store.meta("memory:seen_until") in ("", None) and read(home, "MEMORY.md") == MEM
    assert up.public()["last_attempt"]["retry_at"] == now + 3630
    clock.t = now + 1800
    assert await up.idle_tick() is None and len(fn.calls) == 1            # not before the hold is over
    clock.t = now + 3700
    up.last_use = lambda: clock.t - IDLE_BEFORE_RUN - 1
    state["limited"] = False
    done = await up.idle_tick()
    assert done["status"] == "done" and done["trigger"] == "retry" and "- Takes tea without sugar" in read(home, "MEMORY.md")


async def test_a_limit_between_claims_and_reconcile_keeps_the_claims_for_the_retry(tmp_path):
    now = ts(2026, 11, 16, 4, 0)
    state = {"limited": True}

    def reconcile(req):
        if state["limited"]:
            return QuotaExhausted("p: used up", provider="p", reset_at=now + 3600, until=now + 3630)
        return plan_for(req)
    fn = brain(extract={"claims": [claim_of(1, "Takes tea without sugar", "I always take my tea without sugar")]}, reconcile=reconcile)
    store, home, up, clock, inbox = setup(tmp_path, fn, now=now, memory=MEM)
    say(store, now - 7200, "user", "I always take my tea without sugar")
    first = await up.run()
    assert first["status"] == "pending" and first["code"] == "model_limit"
    assert len(store._all("SELECT * FROM memory_claims")) == 1 and json.loads(store.meta("memory:unreconciled"))   # admitted, not yet filed
    assert store.meta("memory:seen_until") and read(home, "MEMORY.md") == MEM
    clock.t = now + 3700
    state["limited"] = False
    second = await up.run(trigger="retry")                                  # no new message: the waiting claim alone calls for it
    assert second["status"] == "done" and fn.kinds().count("extract") == 1 and "- Takes tea without sugar" in read(home, "MEMORY.md")
    assert json.loads(store.meta("memory:unreconciled")) == []


async def test_a_failing_or_unusable_model_changes_nothing(tmp_path):
    store, home, up, clock, inbox = setup(tmp_path, brain(extract="not json at all"), memory=MEM, swept=False)
    say(store, clock.t - 3600, "user", "hello")
    assert (await up.run())["code"] == "bad_output"
    up.stream_fn = brain(extract=RuntimeError("boom"))
    assert (await up.run())["code"] == "model_error"
    up.model = lambda: None
    assert (await up.run())["status"] == "pending"
    assert read(home, "MEMORY.md") == MEM and up.backups() == [] and up.public()["last_pass"] is None


# ---------------------------------------------------------------------------------------------------------- undo and the inbox
async def test_undo_puts_back_every_file_of_the_pass_and_the_inbox_note(tmp_path):
    inbox_text = "Takes tea without sugar"
    ids = {}
    fn = brain(extract=lambda req: {"claims": [claim_of(ids["m"], "Takes tea without sugar", inbox_text)]}, reconcile=plan_for)
    store, home, up, clock, inbox = setup(tmp_path, fn, memory=MEM)
    ids["m"] = say(store, clock.t - 3600, "user", inbox_text)          # the owner's own message the inbox note names
    inbox.add(inbox_text, "preference", ids["m"])
    assert len(inbox.lines()) == 1
    out = await up.run()
    assert out["status"] == "done" and out["files"] == ["MEMORY.md"] and out["filed"] == 1 and inbox.lines() == []
    assert "- Takes tea without sugar" in read(home, "MEMORY.md")
    res = up.undo()
    assert res["restored"] == ["MEMORY.md"] and res["kept"] == [] and res["inbox_back"] == 1
    assert read(home, "MEMORY.md") == MEM and len(inbox.lines()) == 1
    assert res["last_pass"]["undone"] is True and res["last_pass"]["can_undo"] is False
    with pytest.raises(LookupError):
        up.undo()


async def test_undo_keeps_a_file_edited_after_the_pass(tmp_path):
    fn = brain(extract=lambda req: {"claims": [claim_of(1, "Takes tea without sugar", "I always take my tea without sugar")]},
               reconcile=plan_for)
    store, home, up, clock, inbox = setup(tmp_path, fn, memory=MEM)
    say(store, clock.t - 3600, "user", "I always take my tea without sugar")
    await up.run()
    home.write("MEMORY.md", read(home, "MEMORY.md") + "- The user's own line\n", by="user")
    res = up.undo()
    assert res["kept"] == ["MEMORY.md"] and "The user's own line" in read(home, "MEMORY.md")


async def test_a_file_written_while_the_model_thinks_is_left_alone(tmp_path):
    store, home, up, clock, inbox = setup(tmp_path, None, memory=MEM)
    inner = brain(extract=lambda req: {"claims": [claim_of(1, "Takes tea without sugar", "I always take my tea without sugar")]},
                  reconcile=plan_for)

    async def fn(provider, req, **kw):
        if kind_of(req) == "review":
            home.write("MEMORY.md", CLEAN + "- written meanwhile\n", by="user")
        async for ev in inner(provider, req, **kw):
            yield ev
    up.stream_fn = fn
    say(store, clock.t - 3600, "user", "I always take my tea without sugar")
    out = await up.run()
    assert out["files"] == [] and "written meanwhile" in read(home, "MEMORY.md") and "Takes tea" not in read(home, "MEMORY.md")
