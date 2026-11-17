"""Forget on request (forget.py, docs/forget.md): the plan, the judge, the cleanup of every place, older copies, the tombstone and
the memory pass respecting it, the owner's card, the API. A fake model answers every request; nothing leaves the process."""

import json
import re

import httpx
import pytest

from assistant.forget import CATEGORY_LABELS, Forget, ForgetError, ScreenFailed, Tombstones, clean_terms, forget_tools, sha
from assistant.goals import GoalBook
from assistant.home import Home
from assistant.memory_claims import add_claim_row, run_claims
from assistant.memory_types import Claim
from assistant.schedules import Schedules
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.activity import NEVER_REMEMBER, Activity

from test_memory_pass import brain, claim_id_in, read, say, setup, ts
from test_memory_claims import answering, make_window
from test_server import AUTH, TOKEN, Running

SUBJECT = "the sister's lawsuit"
TERMS = ["lawsuit", "landlord"]


def judge_of(flag="lawsuit", fail=False):
    """The fake main model: picks the numbered items whose text holds `flag` (a stand-in for the model's judgement)."""
    calls = []

    async def ask(system, user):
        calls.append(user)
        if fail:
            return None
        return {"about": [int(m.group(1)) for m in re.finditer(r"^\[(\d+)\] .*" + re.escape(flag), user, re.M | re.I)]}
    ask.calls = calls
    return ask


class Skills:
    """Stands in for packages.Registry: the custom skills Kelo wrote."""
    def __init__(self, **files):
        self.files = {k: {"SKILL.md": v} for k, v in files.items()}

    def custom_ids(self):
        return set(self.files)

    def custom_files(self, sid):
        return self.files[sid]

    def remove_custom(self, sid):
        return self.files.pop(sid, None) is not None


def world(tmp_path, ask=None, **kw):
    store = Store()
    home = Home(tmp_path / "home")
    sched = Schedules(store)
    GoalBook(store, home)
    Activity(store)
    forget = Forget(store, home, ask or judge_of(), schedules=sched, **kw)
    return store, home, sched, forget


def fill(store, home, sched):
    """One topic spread over every kind of place, plus look-alikes that must stay."""
    m1 = say(store, 100.0, "user", "My sister has a lawsuit against the landlord, please remember it")
    m2 = say(store, 101.0, "assistant", "Noted, I will keep that in mind.")
    m3 = say(store, 102.0, "user", "The landlord of my office is called Park and he is nice")   # same word, other matter
    store._run("INSERT INTO conversation_summary(id, summary, up_to_message_id, updated_at) VALUES (1, ?, 3, 1.0)",
               ("Talked about the sister's lawsuit and tea.",))
    add_claim_row(store, Claim("c1111111111", "person", "high", "Sister has a lawsuit against the landlord", "has a lawsuit", "user", f"m{m1}"),
                  "r1", "memory/log/2026-11-16.md", 3, 5.0)
    add_claim_row(store, Claim("c2222222222", "fact", "high", "The owner supports the sister during the process", "supports", "user", f"m{m1}",
                               supersedes="c1111111111"), "r1", "memory/log/2026-11-16.md", 4, 6.0)
    add_claim_row(store, Claim("c3333333333", "preference", "high", "Likes tea", "likes tea", "user", f"m{m3}"), "r1", "memory/log/2026-11-16.md", 5, 7.0)
    home.write("MEMORY.md", "# MEMORY.md\n\n## About the user\n- Likes tea\n- Sister has a lawsuit against the landlord\n", by="system")
    home.write("memory/people/sister.md", "---\nname: Sister\n---\n# Sister\n- Lawsuit about the apartment\n", by="system")
    home.write("memory/log/2026-11-16.md",
               "## Verified extraction r1 09:00\n- claim:c1111111111 · person · high · user · m1 · \"has a lawsuit\" · Sister has a lawsuit\n"
               "- claim:c2222222222 · fact · high · user · m1 · \"supports\" · The owner supports the sister during the process\n"
               "- claim:c3333333333 · preference · high · user · m3 · \"likes tea\" · Likes tea\n", by="system")
    home.write("memory/log/2026-11-16.md", home.read("memory/log/2026-11-16.md") + "- noted the Park landlord is nice\n", by="system")
    sched.create("Lawsuit date", "Remind me about the sister's lawsuit hearing", cron="0 9 * * 1", timezone="UTC")
    sched.create("Tea", "Remind me to buy tea", cron="0 8 * * *", timezone="UTC")
    store.add_goal("Help with the lawsuit paperwork", note="sister")
    store._run("INSERT INTO tasks(title, status, result, started_at) VALUES ('Read about the lawsuit', 'done', 'ok', 1.0)")
    return m1, m2, m3


async def test_the_plan_finds_every_place_and_the_judge_drops_the_look_alike(tmp_path):
    ask = judge_of()
    store, home, sched, forget = world(tmp_path, ask, skills=Skills(lawsuit_helper="Steps for the lawsuit", tea="Brew tea"))
    fill(store, home, sched)
    got = await forget.plan(SUBJECT, TERMS)
    assert got["counts"] == {"memory": 5, "reminders": 2, "skills": 1, "history": 3} and got["total"] == 11
    plan = forget.get_plan(got["plan_id"])
    kinds = sorted((i["k"], i["cat"]) for i in plan["items"])
    assert ("summary", "memory") in kinds and not any(k == "msg" for k, _ in kinds)
    assert ("schedule", "reminders") in kinds and ("skill", "skills") in kinds # and ("schedule", "reminders") in kinds and ("skill", "skills") in kinds
    assert sum(1 for i in plan["items"] if i["k"] == "claim") == 2          # the lawsuit claim and the claim it superseded by chain
    assert not any(i["k"] == "claim" and i["ref"] == "c3333333333" for i in plan["items"])
    assert any(i["k"] == "line" and i["ref"] == "memory/people/sister.md" for i in plan["items"])
    assert any(i["k"] == "row" and i["ref"].startswith("goals:") for i in plan["items"])
    assert any(i["k"] == "row" and i["ref"].startswith("tasks:") for i in plan["items"])
    # the structure adds the dated log lines of the removed claims, and the claim linked by supersession though it has no search term
    log_lines = [i for i in plan["items"] if i["k"] == "line" and i["ref"] == "memory/log/2026-11-16.md"]
    assert any(i.get("derived") for i in log_lines) and any(i["ref"] == "c2222222222" and i.get("derived") for i in plan["items"] if i["k"] == "claim")
    assert all("text" not in i for i in plan["items"])                          # the plan keeps locators and a short preview only
    view = forget.plan_view(got["plan_id"])
    assert view["items"] and set(view["items"][0]) == {"category", "show"}


async def test_no_model_answer_means_no_plan_and_too_broad_terms_are_refused(tmp_path):
    store, home, sched, forget = world(tmp_path, judge_of(fail=True))
    fill(store, home, sched)
    with pytest.raises(ForgetError, match="could not judge"):
        await forget.plan(SUBJECT, TERMS)
    assert store._all("SELECT * FROM forget_plans") == []
    with pytest.raises(ForgetError):
        await forget.plan("", [])
    for i in range(450):
        say(store, 200.0 + i, "user", f"filler about the landlord number {i}")
    forget.ask = judge_of()
    with pytest.raises(ForgetError, match="too many places"):
        await forget.plan(SUBJECT, ["landlord"])


async def test_confirm_removes_everything_verifies_and_writes_the_tombstone(tmp_path):
    store, home, sched, forget = world(tmp_path, skills=Skills(lawsuit_helper="Steps for the lawsuit", tea="Brew tea"))
    forget.skills = forget.skills if forget.skills is not None else None
    fill(store, home, sched)
    home.write("MEMORY.md", home.read("MEMORY.md") + "- Likes tea\n", by="system")     # a second write: .history holds the old lines
    old_copy = list((home.root / ".history").rglob("*.bak"))
    assert any("lawsuit" in p.read_text() for p in old_copy)
    got = await forget.plan(SUBJECT, TERMS)
    out = await forget.confirm(got["plan_id"])
    assert out["verified"] is True and out["left"] == 0 and out["changed_since_plan"] == 0 and out["older_copies"] >= 1
    assert [r["id"] for r in store._all("SELECT id FROM messages")] == [1, 2, 3]   # the visible chat is never a cleanup target
    assert "lawsuit" not in read(home, "MEMORY.md") and "Likes tea" in read(home, "MEMORY.md")
    assert not (home.root / "memory/people/sister.md").exists() or "Lawsuit" not in read(home, "memory/people/sister.md")
    log = read(home, "memory/log/2026-11-16.md")
    assert "c1111111111" not in log and "c2222222222" not in log and "c3333333333" in log and "Park landlord" in log
    assert [r["claim_id"] for r in store._all("SELECT claim_id FROM memory_claims")] == ["c3333333333"]
    assert store._all("SELECT claim_id FROM memory_claims_fts f JOIN memory_claims c ON c.rowid = f.rowid WHERE memory_claims_fts MATCH 'lawsuit'") == []
    assert [s["name"] for s in sched.list()] == ["Tea"] and sched.archived() == []
    assert store._all("SELECT * FROM goals") == [] and store._all("SELECT * FROM tasks") == []
    assert store._all("SELECT summary FROM conversation_summary")[0]["summary"] == ""
    assert forget.skills.custom_ids() == {"tea"}
    assert not any("lawsuit" in p.read_text() for p in (home.root / ".history").rglob("*.bak"))
    [tomb] = forget.tombstones.public()
    assert tomb["label"] == SUBJECT and "chat" not in tomb["removed"] and tomb["removed"]["memory"] == 5 and "terms" not in tomb
    assert store._all("SELECT * FROM forget_plans") == []                      # the plan is gone with the subject in it


async def test_an_item_changed_since_the_plan_is_left_alone(tmp_path):
    store, home, sched, forget = world(tmp_path)
    fill(store, home, sched)
    got = await forget.plan(SUBJECT, TERMS)
    store._run("UPDATE tasks SET result = 'the lawsuit, and also something new about it'")
    out = await forget.confirm(got["plan_id"])
    assert out["changed_since_plan"] >= 1 and out["verified"] is False and out["left"] >= 1
    assert store._all("SELECT id FROM tasks")
    with pytest.raises(ForgetError):
        await forget.confirm(got["plan_id"])                                   # the plan is used up


async def test_a_plan_is_stale_after_an_hour_and_messages_after_it_are_not_in_it(tmp_path):
    now = [1000.0]
    store, home, sched, forget = world(tmp_path)
    forget.clock = lambda: now[0]
    fill(store, home, sched)
    say(store, 5000.0, "user", "yes, forget the lawsuit")                      # after the plan's time: not part of it
    got = await forget.plan(SUBJECT, TERMS)
    assert not any(i["k"] == "msg" for i in forget.get_plan(got["plan_id"])["items"])
    now[0] += 4000
    assert forget.get_plan(got["plan_id"]) is None
    with pytest.raises(ForgetError):
        await forget.confirm(got["plan_id"])


async def test_nothing_to_forget_is_said_plainly(tmp_path):
    store, home, sched, forget = world(tmp_path)
    tools = {t.name: t for t in forget_tools(forget)}
    out = await tools["forget_plan"].handler({"subject": "the garden", "terms": ["garden"]})
    assert "Nothing stored" in out and "forget_confirm" in out


async def test_the_tools_never_repeat_the_subject_and_the_confirm_needs_the_owners_card(tmp_path):
    store, home, sched, forget = world(tmp_path)
    fill(store, home, sched)
    tools = {t.name: t for t in forget_tools(forget)}
    assert tools["forget_confirm"].requires_approval and not tools["forget_plan"].requires_approval
    assert tools["forget_confirm"].auto_approve is None
    assert any("forget_confirm".startswith(x) for x in NEVER_REMEMBER)          # an approval of it is never remembered as a rule
    said = await tools["forget_plan"].handler({"subject": SUBJECT, "terms": TERMS})
    assert "lawsuit" not in said and "landlord" not in said
    pid = re.search(r"Plan (p[0-9a-f]+)", said).group(1)
    assert tools["forget_confirm"].validate({"plan_id": pid}) is None and tools["forget_confirm"].validate({"plan_id": "nope"})
    card = await tools["forget_confirm"].describe({"plan_id": pid})
    assert SUBJECT in card and "cannot be undone" in card and f"{CATEGORY_LABELS['memory']}: 5" in card and "Chat" not in card and len(card) <= 300
    result = await tools["forget_confirm"].handler({"plan_id": pid})
    assert "Forgotten" in result and "nothing left" in result and "lawsuit" not in result
    again = await tools["forget_confirm"].handler({"plan_id": pid})
    assert again.startswith("error")


def test_terms_are_trimmed_unique_and_bounded():
    assert clean_terms("the sister's lawsuit", ["Lawsuit", " lawsuit ", "x", "", "landlord"]) == ["Lawsuit", "landlord", "the sister's lawsuit"]
    assert len(clean_terms("s", [f"term{i}" for i in range(40)])) == 12
    assert sha("  A  b ") == sha("a B")


# ----------------------------------------------------------------------------------------------------------- never again
def forgotten(store, label=SUBJECT, terms=tuple(TERMS)):
    return Tombstones(store).add(label, list(terms), {"chat": 1})


async def test_the_tombstone_notice_block_and_screen():
    store = Store()
    tomb = Tombstones(store)
    assert tomb.block() == "" and tomb.notice() == ""
    async def never(_m):
        raise AssertionError("nothing is forgotten: no request")
    assert await tomb.screener(never)(["anything"]) == set()
    forgotten(store)
    assert SUBJECT in tomb.block() and "<forgotten_topics>" in tomb.block() and SUBJECT in tomb.notice() and "never" in tomb.notice().lower()
    seen = []

    async def call(messages):
        seen.append(messages)
        return json.dumps({"about": [1]})
    assert await tomb.screener(call)(["Likes tea", "Sister's case", "Likes rain"]) == {1}
    assert "[1] Sister's case" in seen[0][1]["content"] and "forget" in seen[0][0]["content"].lower()

    async def bad(_m):
        return "no json"
    with pytest.raises(ScreenFailed):
        await tomb.screener(bad)(["x"])
    assert tomb.remove(tomb.all()[0]["id"]) and tomb.all() == [] and not tomb.remove("fnone")


async def test_extract_drops_a_claim_about_a_forgotten_topic_and_a_failed_screen_stores_nothing(tmp_path):
    store, home = Store(), Home(tmp_path / "home")
    forgotten(store)
    window = make_window(messages=[{"id": 1, "role": "user", "text": "My sister's case goes on. Also I like Earl Grey tea."}])
    good = {"kind": "preference", "salience": "high", "text": "Likes Earl Grey tea", "quote": "I like Earl Grey tea", "speaker": "user", "handle": "m1", "secret": False}
    bad = {"kind": "fact", "salience": "high", "text": "Sister's case goes on", "quote": "My sister's case goes on", "speaker": "user", "handle": "m1", "secret": False}

    async def model(provider, req, **kw):
        sysmsg = req.messages[0]["content"]
        body = req.messages[-1]["content"]
        if "Extract" in sysmsg or "extract" in sysmsg.lower():
            assert "<forgotten_topics>" in body                              # the request names what not to take
            out = {"claims": [good, bad], "more": False}
        elif "forget" in sysmsg.lower():
            out = {"about": [i for i, ln in enumerate(re.findall(r"^\[\d+\] .*", body, re.M)) if "case" in ln]}
        else:
            ids = re.findall(r"### Claim ID: (c[0-9a-f]+)", body)
            out = {"verdicts": [{"claim_id": i, "ok": True, "lens": lens, "reason": ""} for i in ids for lens in ("refute", "evidence")]}
        from assistant.providers import Done, TextDelta
        yield TextDelta(json.dumps(out))
        yield Done("stop")

    admitted, stats = await run_claims(window, store, home, model, model=("p", "m"), tombstones=Tombstones(store))
    assert [c.text for c in admitted] == ["Likes Earl Grey tea"] and stats["dropped"] == 1
    assert [r["text"] for r in store._all("SELECT text FROM memory_claims")] == ["Likes Earl Grey tea"]

    store2, home2 = Store(), Home(tmp_path / "home2")
    forgotten(store2)

    async def broken(provider, req, **kw):
        from assistant.providers import Done, TextDelta
        sysmsg = req.messages[0]["content"]
        yield TextDelta(json.dumps({"claims": [good], "more": False}) if "xtract" in sysmsg else "not json at all")
        yield Done("stop")

    admitted, stats = await run_claims(window, store2, home2, broken, model=("p", "m"), tombstones=Tombstones(store2))
    assert admitted == [] and stats["error"] == "screen_failed" and store2._all("SELECT * FROM memory_claims") == []


async def test_without_a_tombstone_the_extract_request_has_no_block_and_no_screen_request():
    store = Store()
    seen = []
    window = make_window(messages=[{"id": 1, "role": "user", "text": "I like Earl Grey tea"}])
    fn = answering({"claims": [], "more": False})
    await run_claims(window, store, Home("/tmp/forget-none-home"), fn, model=("p", "m"), tombstones=Tombstones(store))
    assert len(fn.calls) == 1 and "forgotten_topics" not in fn.calls[0].messages[-1]["content"]
    assert seen == []


async def test_a_pass_takes_out_a_line_it_added_about_a_forgotten_topic(tmp_path):
    screens = []

    def other(req):
        body = req.messages[-1]["content"]
        screens.append(body)
        lines = re.findall(r"^\[(\d+)\] (.*)", body, re.M)
        return {"about": [int(i) for i, ln in lines if "case" in ln]}

    def plan(req):
        cid = claim_id_in(req)
        return {"edits": [{"handle": "edit:1", "op": "insert_after", "start": 4, "end": 4, "guard": "- Likes tea", "reason": "promote",
                           "replacement": "- Sister's case goes on", "evidence": [f"claim:{cid}"],
                           "origin": {"source_kind": "user", "source": "m1", "user_request": "tea"}}]}
    fn = brain(extract=lambda req: {"claims": [{"kind": "preference", "salience": "high", "text": "Takes tea without sugar",
                                                "quote": "I always take my tea without sugar", "speaker": "user", "handle": "m1", "secret": False}]},
               reconcile=plan, other=other)
    store, home, up, clock, inbox = setup(tmp_path, fn)
    up.tombstones = Tombstones(store)
    forgotten(store)
    say(store, clock.t - 3600, "user", "I always take my tea without sugar")
    out = await up.run(trigger="nightly")
    assert out["status"] == "done"
    mem = read(home, "MEMORY.md")
    assert "case" not in mem and "Likes tea" in mem                               # added by the plan, screened, taken out again
    assert any("case" in s for s in screens)
    assert [r["text"] for r in store._all("SELECT text FROM memory_claims")] == ["Takes tea without sugar"]
    assert any("forgotten_topics" in c.messages[-1]["content"] for k, c in fn.calls if k in ("extract", "reconcile"))


async def test_a_failed_screen_after_the_pass_puts_the_file_back(tmp_path):
    def plan(req):
        cid = claim_id_in(req)
        return {"edits": [{"handle": "edit:1", "op": "insert_after", "start": 4, "end": 4, "guard": "- Likes tea", "reason": "promote",
                           "replacement": "- Takes tea without sugar", "evidence": [f"claim:{cid}"],
                           "origin": {"source_kind": "user", "source": "m1", "user_request": "tea"}}]}
    n = []

    def other(req):
        n.append(1)
        return {"about": []} if len(n) == 1 else "garbage"                         # the claims screen passes, the lines screen fails
    fn = brain(extract=lambda req: {"claims": [{"kind": "preference", "salience": "high", "text": "Takes tea without sugar",
                                                "quote": "I always take my tea without sugar", "speaker": "user", "handle": "m1", "secret": False}]},
               reconcile=plan, other=other)
    store, home, up, clock, inbox = setup(tmp_path, fn)
    up.tombstones = Tombstones(store)
    forgotten(store)
    before = read(home, "MEMORY.md")
    say(store, clock.t - 3600, "user", "I always take my tea without sugar")
    await up.run(trigger="nightly")
    assert read(home, "MEMORY.md") == before


async def test_the_agents_prompt_carries_the_notice_only_when_something_is_forgotten(tmp_path):
    from assistant.prompt import PromptBuilder
    store = Store()
    pb = PromptBuilder("base {agent_name}", None, None)
    tomb = Tombstones(store)
    pb.forgotten = tomb.notice
    assert "Forgotten topics" not in pb.build(agent_name="Kelo")
    forgotten(store)
    assert "Forgotten topics" in pb.build(agent_name="Kelo") and SUBJECT in pb.build(agent_name="Kelo")


# ----------------------------------------------------------------------------------------------------------- API
async def test_the_api_lists_what_is_forgotten_lets_the_owner_allow_it_again_and_shows_a_plan(tmp_path):
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"))

    async def fn(*a, **k):
        if False:
            yield None
    app = create_app(s, stream_fn=fn, with_schedule_check=False)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/forget")).status_code == 401
        c.headers.update(AUTH)
        assert (await c.get("/api/forget")).json() == {"items": []}
        assert (await c.get("/api/forget/plans/pnone")).status_code == 404
        assert (await c.delete("/api/forget/fnone")).status_code == 404
    from assistant.store import Store as S
    store = S(str(tmp_path / "a.db"))
    tid = Tombstones(store).add("a topic", ["x1", "x2"], {"memory": 2})
    store.close() if hasattr(store, "close") else None
    async with Running(create_app(s, stream_fn=fn, with_schedule_check=False)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        items = (await c.get("/api/forget")).json()["items"]
        assert items == [{"id": tid, "label": "a topic", "created_at": items[0]["created_at"], "removed": {"memory": 2}}]   # no terms
        assert (await c.delete(f"/api/forget/{tid}")).json() == {"ok": True}
        assert (await c.get("/api/forget")).json() == {"items": []}


async def test_the_chat_stays_but_what_cites_it_goes(tmp_path):
    store, home, sched, forget = world(tmp_path)
    m1, m2, m3 = fill(store, home, sched)
    store._run("INSERT INTO message_attachments(message_id, idx, name, path, kind, mime, size, auto_text) VALUES (?,0,'a.txt','p','file','text/plain',1,'')", (m1,)) \
        if forget._has("message_attachments") else None
    got = await forget.plan(SUBJECT, TERMS)
    assert "chat" not in got["counts"]
    await forget.confirm(got["plan_id"])
    assert len(store._all("SELECT id FROM messages")) == 3 and "lawsuit" in store._all("SELECT content FROM messages WHERE id = 1")[0]["content"]
    assert not store._all("SELECT 1 FROM memory_claims WHERE handle = ?", (f"m{m1}",))      # claims citing the message are gone


async def test_a_mixed_row_is_rewritten_and_a_row_about_the_subject_is_deleted(tmp_path):
    store, home, sched, forget = world(tmp_path)
    store._run("INSERT INTO tasks(title, status, result, started_at) VALUES ('Day', 'done', 'Bought tea. The sister lawsuit hearing moved. Walked home.', 1.0)")
    store._run("INSERT INTO tasks(title, status, result, started_at) VALUES ('Lawsuit', 'done', 'Only the lawsuit today', 1.0)")
    rewrites = []

    async def ask(system, user):
        if "Rewrite" in system or "rewrite" in system.lower():
            rewrites.append(user)
            return {"text": "Bought tea. Walked home."}
        rows = [int(m.group(1)) for m in re.finditer(r"^\[(\d+)\] .*lawsuit", user, re.M | re.I)]
        mixed = [int(m.group(1)) for m in re.finditer(r"^\[(\d+)\] .*Bought tea", user, re.M)]
        return {"about": rows, "mixed": mixed}
    forget.ask = ask
    got = await forget.plan(SUBJECT, TERMS)
    out = await forget.confirm(got["plan_id"])
    rows = store._all("SELECT title, result FROM tasks")
    assert rows == [{"title": "Day", "result": "Bought tea. Walked home."}] and len(rewrites) == 1 and out["verified"]


async def test_a_failed_rewrite_leaves_the_mixed_row_alone(tmp_path):
    store, home, sched, forget = world(tmp_path)
    store._run("INSERT INTO tasks(title, status, result, started_at) VALUES ('Day', 'done', 'Bought tea. The lawsuit moved.', 1.0)")

    async def ask(system, user):
        if "ewrite" in system:
            return None
        return {"about": [0], "mixed": [0]}
    forget.ask = ask
    got = await forget.plan(SUBJECT, TERMS)
    out = await forget.confirm(got["plan_id"])
    assert store._all("SELECT result FROM tasks")[0]["result"] == "Bought tea. The lawsuit moved." and out["changed_since_plan"] == 1


async def test_the_summary_and_the_memory_step_of_a_compaction_never_get_to_mention_a_forgotten_topic():
    from test_compaction import Fake, seed, W
    from assistant.compaction import Compactor
    store, fake = Store(), Fake(summaries=["summary"])
    seed(store, 40)
    forgotten(store)
    flushed = []

    async def flush(provider, model, transcript):
        flushed.append(transcript)
    c = Compactor(store, fake, flush_fn=flush)
    c.forgotten = Tombstones(store).block
    await c.compact("p", "m", W, 500)
    sums = fake.of("summary")
    assert sums and all("<forgotten_topics>" in r.messages[0]["content"] and SUBJECT in r.messages[0]["content"]
                        and "Do not make any reference" in r.messages[0]["content"] for r in sums)
    assert flushed and "<forgotten_topics>" in flushed[0]
    plain, fake2 = Store(), Fake(summaries=["summary"])
    seed(plain, 40)
    await Compactor(plain, fake2).compact("p", "m", W, 500)
    assert all("forgotten" not in r.messages[0]["content"] for r in fake2.of("summary"))
