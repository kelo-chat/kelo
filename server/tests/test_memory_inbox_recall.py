"""Capture first, file later (memory_inbox.py + the memory pass) and reading only what is relevant (recall.py), with fake models:
the remember tool's dated inbox lines, filing that removes only what a successful pass read, nothing lost on a failed or cut-off
pass, the periodic full rewrite, dated lines and oldest-first ageing, the section index (FTS5/BM25) and the turn's note (relevant
sections once, then by path; never in the cached system prompt)."""

import asyncio
import json

import httpx

from assistant.home import Home
from assistant.memory_inbox import INBOX, INBOX_MAX, INBOX_SHOWN, MemoryInbox, inbox_tools
from assistant.memory_upkeep import REWRITE_EVERY, fit_budget, line_date, tag_new_lines
import assistant.memory_upkeep as mu
from assistant.providers import Done, TextDelta
from assistant.recall import SHOWN_WINDOW, Recall, TurnRecall, query_terms, sections
from assistant.server import TURN_NOTE, Settings, create_app
from assistant.store import Store

from test_memory_upkeep import Clock, answer, answering, read, say, setup, ts
from test_server import AUTH, TOKEN, Running, read_events

MEM = "# MEMORY.md\n\n## About the user\n- {x}\n\n## Ongoing\n\n## Decided & done\n"


def with_inbox(tmp_path, fn, **kw):
    store, home, up, clock = setup(tmp_path, fn, **kw)
    inbox = MemoryInbox(home, tz=lambda: "Asia/Seoul", clock=clock)
    up.inbox = inbox
    return store, home, up, clock, inbox


# ---------------------------------------------------------------------------------------------------------- the inbox
async def test_remember_writes_a_dated_line_with_its_source_newest_first(tmp_path):
    home = Home(tmp_path / "home")
    clock = Clock(ts(2026, 11, 9, 14, 3))
    inbox = MemoryInbox(home, tz=lambda: "Asia/Seoul", clock=clock)
    [tool] = inbox_tools(inbox, lambda: 1234)
    assert tool.name == "remember"
    out = await tool.handler({"text": "Prefers  window seats", "kind": "preference"})
    assert out == "noted: 2026-11-09 14:03 · m1234 · preference · Prefers window seats"
    clock.t += 60
    await tool.handler({"text": "Works from home on Fridays"})
    assert [ln.split(" · ")[-1] for ln in inbox.lines()] == ["Works from home on Fridays", "Prefers window seats"]
    await tool.handler({"text": "prefers window seats"})                       # already waiting: not twice
    assert len(inbox.lines()) == 2
    assert (await tool.handler({"text": "password: hunter22"})).startswith("error:")   # a secret is never kept
    assert "hunter22" not in (home.root / INBOX).read_text()
    for i in range(INBOX_MAX + 5):
        inbox.add(f"fact number {i}")
    assert len(inbox.lines()) == INBOX_MAX and "fact number 204" in inbox.lines()[0]
    block = inbox.prompt_block()
    assert block.count("\n- ") == INBOX_SHOWN and "fact number 204" in block.splitlines()[1] and f"older in {INBOX}" in block


async def test_a_pass_files_the_inbox_and_removes_only_what_it_read(tmp_path):
    store, home, up, clock, inbox = with_inbox(tmp_path, None)
    inbox.add("Allergic to peanuts", "fact", 7)

    async def fn(provider, request, **kw):
        assert "<inbox>" in request.messages[1]["content"] and "Allergic to peanuts" in request.messages[1]["content"]
        inbox.add("Moved to a new team", "fact", 8)               # noted while the pass runs: stays for the next one
        yield TextDelta(json.dumps(answer(memory_md=MEM.format(x="Allergic to peanuts [2026-11-09, inbox m7]"))))
        yield Done("stop")
    up.stream_fn = fn
    out = await up.run()                                          # no new conversation: the inbox alone is reason enough
    assert out["status"] == "done" and out["filed"] == 1
    assert "Allergic to peanuts [2026-11-09, inbox m7]" in read(home, "MEMORY.md")
    assert [ln.split(" · ")[-1] for ln in inbox.lines()] == ["Moved to a new team"]
    undone = up.undo()                                            # undoing the pass puts its filed note back in the inbox
    assert undone["inbox_back"] == 1 and "Allergic to peanuts" not in read(home, "MEMORY.md")
    assert [ln.split(" · ")[-1] for ln in inbox.lines()] == ["Moved to a new team", "Allergic to peanuts"]
    assert inbox.restore(inbox.lines()) == 0                      # never twice


async def test_a_failed_or_cut_off_pass_loses_nothing(tmp_path, monkeypatch):
    store, home, up, clock, inbox = with_inbox(tmp_path, answering("not json"))
    inbox.add("Allergic to peanuts")
    assert (await up.run())["code"] == "bad_output" and len(inbox.lines()) == 1
    up.stream_fn = answering(RuntimeError("down"))
    assert (await up.run())["code"] == "model_error" and len(inbox.lines()) == 1

    async def slow(provider, request, **kw):
        await asyncio.sleep(5)
        yield TextDelta(json.dumps(answer(memory_md=MEM.format(x="x"))))
    monkeypatch.setattr(mu, "MODEL_TIMEOUT", 0.05)
    up.stream_fn = slow
    out = await up.run()
    assert out["status"] == "failed" and "no answer within" in out["reason"] and len(inbox.lines()) == 1
    assert "x" not in read(home, "MEMORY.md").split("## About the user")[1].split("##")[0]


async def test_a_file_changed_meanwhile_keeps_the_inbox_for_the_next_pass(tmp_path):
    store, home, up, clock, inbox = with_inbox(tmp_path, None)
    inbox.add("Allergic to peanuts")

    async def fn(provider, request, **kw):
        home.write("MEMORY.md", MEM.format(x="the user's own edit"), by="user")
        yield TextDelta(json.dumps(answer(memory_md=MEM.format(x="Allergic to peanuts"))))
        yield Done("stop")
    up.stream_fn = fn
    await up.run()
    assert "the user's own edit" in read(home, "MEMORY.md") and len(inbox.lines()) == 1


async def test_every_few_passes_the_files_are_rewritten_whole(tmp_path):
    fn = answering(*[answer(memory_md=MEM.format(x=f"fact {i}")) for i in range(REWRITE_EVERY + 2)])
    store, home, up, clock = setup(tmp_path, fn)
    for i in range(REWRITE_EVERY + 1):
        say(store, clock.t - 10, "user", f"message {i}")
        out = await up.run()
        clock.t += 3600
        assert out["status"] == "done"
    flags = ["rewrite user_md, memory_md and agents_md whole" in c.messages[0]["content"] for c in fn.calls]
    assert flags == [False] * (REWRITE_EVERY - 1) + [True, False]


def test_new_lines_get_a_date_and_the_oldest_go_first():
    before = "## About the user\n- Old fact [2026-01-02, said m3]\n"
    after = tag_new_lines(before, "## About the user\n- Old fact [2026-01-02, said m3]\n- New fact\n- Group:\n", "2026-11-09")
    assert after.splitlines()[1:] == ["- Old fact [2026-01-02, said m3]", "- New fact [2026-11-09]", "- Group:"]
    assert line_date("- New fact [2026-11-09]") == "2026-11-09" and line_date("- no tag") == ""
    done = "\n".join(f"- job {d} " + "x" * 120 + f" [2026-{d:02d}-01, task]" for d in (5, 1, 9, 3))
    out = fit_budget("# MEMORY.md\n\n## About the user\n- keep me\n\n## Ongoing\n\n## Decided & done\n" + done + "\n", 400)
    assert "keep me" in out and "job 9" in out and "job 1 " not in out          # the oldest by date went, not the first by position
    assert all(ln.endswith("task]") for ln in out.splitlines() if ln.startswith("- job"))   # shortened lines keep their tag


# ---------------------------------------------------------------------------------------------------------- the section index
def people(home, slug, name, aliases, body):
    home.write(f"memory/people/{slug}.md", f"---\nname: {name}\naliases: [{', '.join(aliases)}]\nrelation: friend\ngroups: []\n---\n\n{body}")


def test_sections_and_terms():
    assert sections("intro\n# A\none\n## B\ntwo\n") == [("", "intro"), ("A", "one"), ("B", "two")]
    assert query_terms("지우랑 저녁 먹기로 했어") [:3] == ["지우랑", "지우", "저녁"]
    assert "the" not in query_terms("what is the plan")


def test_the_index_finds_the_right_section_and_keeps_up_with_changes(tmp_path):
    home = Home(tmp_path / "home")
    store = Store()
    people(home, "jiwoo", "지우", ["Jiwoo"], "# 지우\n\n## Work\nSame team, meets on Mondays.\n\n## Food\nVegetarian; no mushrooms.\n")
    people(home, "minho", "민호", [], "# 민호\n\n## Family\nOlder brother, lives in Busan.\n")
    home.write("memory/trips.md", "# Trips\n\n## Spring\nPassport renewal before the spring trip.\n")
    store._run("CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, title TEXT NOT NULL, "
               "summary TEXT NOT NULL, created_at REAL NOT NULL)")
    store._run("INSERT INTO journal(date, title, summary, created_at) VALUES('2026-11-07', 'Dentist', 'Booked a cleaning for Thursday', 1)")
    r = Recall(home, store)
    hits = r.search("지우랑 저녁 먹을 건데 뭐 먹지? food ideas")
    assert hits[0]["path"] == "memory/people/jiwoo.md" and "Vegetarian" in hits[0]["text"] and len(hits) <= 3
    assert r.search("when is the dentist")[0]["path"] == "journal/2026-11-07"
    assert r.search("passport")[0]["key"] == "memory/trips.md#Spring"
    assert r.search("hello there") == []
    home.write("memory/trips.md", "# Trips\n\n## Autumn\nVisa for the autumn trip.\n")
    assert r.search("passport") == [] and r.search("visa")[0]["key"] == "memory/trips.md#Autumn"
    (home.root / "memory/trips.md").unlink()
    assert r.search("visa") == []
    home.write("memory/inbox.md", "# inbox.md\n- 2026-11-09 10:00 · m1 · fact · visa\n")
    assert r.search("visa") == []                                   # the inbox is shown on its own, not indexed


def test_a_section_is_sent_once_then_named_by_path(tmp_path):
    home = Home(tmp_path / "home")
    store = Store()
    people(home, "jiwoo", "지우", [], "# 지우\n\n## Food\nVegetarian; no mushrooms.\n")
    inbox = MemoryInbox(home)
    inbox.add("Started a new job")
    tr = TurnRecall(Recall(home, store), store, inbox)
    ids = [store.add_message("user", f"msg {i}") for i in range(SHOWN_WINDOW + 2)]
    first = tr.block("지우 food", ids[0])
    assert "### Just noted" in first and "Started a new job" in first and "Vegetarian" in first
    again = tr.block("지우 food", ids[0])                              # the same turn built again (fallback model): text again
    assert "Vegetarian" in again
    later = tr.block("지우 food", ids[1])
    assert "Vegetarian" not in later and "Shown earlier" in later and "memory/people/jiwoo.md" in later
    much_later = tr.block("지우 food", ids[SHOWN_WINDOW + 1])
    assert "Vegetarian" in much_later
    home.write("memory/people/jiwoo.md", (home.root / "memory/people/jiwoo.md").read_text().replace("Vegetarian", "Vegan"))
    assert "Vegan" in tr.block("지우 food", ids[SHOWN_WINDOW + 1])     # changed text is sent again


async def test_the_turn_note_carries_the_notes_and_the_system_prompt_stays_the_same(tmp_path):
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request.messages)
        yield TextDelta("ok")
        yield Done("stop")

    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False)
    app = create_app(s, tools=[], stream_fn=fn)
    home = Home(tmp_path / "home")
    people(home, "jiwoo", "지우", [], "# 지우\n\n## Food\nVegetarian; no mushrooms.\n")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for text in ("지우랑 저녁 뭐 먹지?", "지우가 좋아할 만한 곳 알려줘"):
            async with c.stream("POST", "/api/talk", json={"text": text}) as r:
                await read_events(r)
    assert len(seen) == 2 and seen[0][0]["content"] == seen[1][0]["content"]
    assert "Vegetarian" not in seen[0][0]["content"]                       # never in the cached system prompt
    note1, note2 = seen[0][-2]["content"], seen[1][-2]["content"]
    assert note1.startswith(TURN_NOTE) and "Vegetarian" in note1
    assert "Vegetarian" not in note2 and "memory/people/jiwoo.md" in note2  # the second turn names it by path


async def test_the_remember_tool_is_always_at_hand(tmp_path):
    from assistant.agent.toolbox import CORE_TOOLS
    assert "remember" in CORE_TOOLS
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False)
    app = create_app(s, stream_fn=answering("ok"))
    assert "remember" in {t.name for t in app.state.tools}


async def test_a_note_taken_mid_chat_leaves_the_cached_system_prompt_as_it_was(tmp_path):
    """remember writes memory/inbox.md; the next turn's system text is byte for byte the same (the inbox shows in the turn note)."""
    from assistant.providers import ToolCall
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request.messages)
        if len(seen) == 1:
            yield ToolCall(id="c1", name="remember", arguments=json.dumps({"text": "Allergic to peanuts", "kind": "fact"}))
            yield Done("tool_calls")
            return
        yield TextDelta("ok")
        yield Done("stop")

    from pathlib import Path
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False, content_dir=str(Path(__file__).resolve().parents[2] / "server" / "content"))   # the real rules: the file list is in them
    app = create_app(s, stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for text in ("I'm allergic to peanuts, by the way.", "What should I cook tonight?"):
            async with c.stream("POST", "/api/talk", json={"text": text}) as r:
                await read_events(r)
    home = Home(tmp_path / "home")
    assert "Allergic to peanuts" in (home.root / INBOX).read_text()
    assert "## Your Files" in seen[2][0]["content"]
    assert len(seen) == 3 and seen[0][0]["content"] == seen[2][0]["content"]   # turn 2's system text is turn 1's
    assert INBOX not in seen[2][0]["content"] and "Allergic to peanuts" not in seen[2][0]["content"]
    note = next(m["content"] for m in seen[2] if isinstance(m.get("content"), str) and m["content"].startswith(TURN_NOTE))
    assert "Allergic to peanuts" in note                                         # the fresh note works before it is filed
