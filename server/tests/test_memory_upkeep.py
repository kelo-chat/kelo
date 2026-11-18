"""The helpers and triggers of the memory pass (memory_upkeep.py): the secret scrub, MEMORY.md layout and budget fitting, changed-line
counts, the switch, the idle and nightly triggers and the API shape the apps read. The pass itself (claims, reconcile, undo, usage
limit) is covered end to end in test_memory_pass.py, step by step in test_memory_claims.py and test_memory_reconcile.py. No request
ever leaves the process."""

import httpx

from assistant.memory_upkeep import (CAPS, IDLE_AFTER, IDLE_GAP, IDLE_MIN_MESSAGES, MemoryUpkeep, budget_from_env, fit_budget, in_layout,
                                     line_changes, migrate_layout, scrub)
from assistant.home import Home
from assistant.server import Settings, create_app
from assistant.store import Store

from test_memory_pass import Clock, brain, claim_of, read, say, setup, ts  # noqa: F401
from test_server import AUTH, TOKEN, Running


def test_finished_logs_go_first_and_durable_facts_stay_when_the_budget_bites():
    text = ("# MEMORY.md\n\n## About the user\n- Shoe size 255 mm\n- Deliveries to the office front desk\n\n## Ongoing\n- Renew the passport\n\n"
            "## Decided & done\n" + "\n".join(f"- Finished job {i}: " + "processed 400 items, 30 retried. " * 8 for i in range(20)) + "\n")
    out = fit_budget(text, 1_000)
    assert len(out) <= 1_000 and "Shoe size 255 mm" in out and "office front desk" in out and "Renew the passport" in out
    assert out.count("Finished job") < 20 and "Finished job 19" in out                  # the oldest finished items went first
    assert all(len(line) <= 170 for line in out.splitlines() if "Finished job" in line)
    assert CAPS == {"user.md": 1_500, "AGENTS.md": 1_500} and in_layout(out)
    assert budget_from_env({"ASSISTANT_MEMORY_BUDGET": "5000"}) == 5000 and budget_from_env({}) == 3000
    assert budget_from_env({"ASSISTANT_MEMORY_BUDGET": "12"}) == 800 and budget_from_env({"ASSISTANT_MEMORY_BUDGET": "a lot"}) == 3000


def test_other_section_names_move_into_the_current_layout_keeping_every_item():
    text = "# MEMORY.md\n\n## Facts\n- a\n\n## Ongoing\n- c\n\n## 완료한 일\n- d\n\n## Decided & done\n- f\n\n## Odd name\n- e\nstill e\n"
    out = migrate_layout(text)   # only the three names of the layout are known; any other heading's items go to About the user
    assert out == "# MEMORY.md\n\n## About the user\n- a\n- d\n- e still e\n\n## Ongoing\n- c\n\n## Decided & done\n- f\n"
    assert migrate_layout(out) == out and in_layout(out)


def test_the_secret_scrub_takes_out_values_by_shape_only():
    # (hc-privacy: the old test also expected "password"/"PIN"/"OTP" words in several languages to drop a line; no word decides now,
    # the bare-code and spoken-password cases are the extracting model's `secret` flag: test_memory_claims.py)
    text = ("- Bank site: Hunter2024!\n- card 4111 1111 1111 1111 paid\n- Likes tea\n"
            "- API key sk-abcdefghijklmnopqrstu123\n- IBAN GB82 WEST 1234 5698 7654 32 (salary)\n- ID 900101-1234567\n")
    out, n = scrub(text)
    for secret in ("Hunter2024", "4111 1111", "sk-abcdef", "WEST 1234", "1234567"):
        assert secret not in out
    assert "Likes tea" in out and n == 5
    for kept in ("hotpot: 2 servings", "Timezone: Asia/Seoul", "laptop is 13 inch", "call 010-1234-5678", "order: 20261112", "GB82 WEST 1234 5698 7654 33"):
        assert scrub(kept) == (kept, 0)
    assert scrub("label: Tr0ub4dor&3") == ("", 1)                  # a "label: value" line whose value is password-shaped, in any language
    assert scrub("비밀번호: sunflower7") == ("", 1)


async def test_the_server_uses_the_look_backs_switch(tmp_path):
    store = Store()
    up = MemoryUpkeep(store, Home(tmp_path / "home"), None, brain(), lambda: ("p", "m"))
    assert up.enabled()
    store.set_meta("reflection:enabled", "0")
    assert not up.enabled()


def test_changes_count_rewrites_as_tidied():
    ch = line_changes("- Shoe size is 255 mm and clothing size is M; said while shopping\n- Old log line about job 1\n",
                      "- Shoe size 255 mm, clothing M\n- Lives near the river\n")
    assert ch["tidied"] == 2 and ch["new"] == 1 and ch["added"] == ["Shoe size 255 mm, clothing M", "Lives near the river"]


async def test_the_idle_trigger_waits_for_a_quiet_chat_with_enough_new_messages(tmp_path):
    fn = brain()
    store, home, up, clock, inbox = setup(tmp_path, fn)
    for i in range(IDLE_MIN_MESSAGES - 1):
        say(store, clock.t - 600 + i, "user", f"msg {i}")
    assert await up.idle_tick() is None                          # too few, and too recent
    say(store, clock.t - 500, "user", "one more")
    assert await up.idle_tick() is None                          # enough, but the chat is not quiet yet
    clock.t += IDLE_AFTER
    out = await up.idle_tick()
    assert out is not None and out["status"] == "done" and out["trigger"] == "idle" and len(fn.calls) == 1
    for i in range(IDLE_MIN_MESSAGES):
        say(store, clock.t + i, "user", f"later {i}")
    clock.t += IDLE_AFTER + 60
    assert await up.idle_tick() is None and len(fn.calls) == 1   # at most once per IDLE_GAP
    clock.t += IDLE_GAP
    assert (await up.idle_tick())["status"] == "done" and len(fn.calls) == 2


async def test_the_nightly_pass_waits_until_the_app_is_left_alone(tmp_path):
    fn = brain()
    store, home, up, clock, inbox = setup(tmp_path, fn)
    used = [clock.t - 60]
    slept = []

    async def sleep(s):
        slept.append(s)
        clock.t += s
        if len(slept) == 3:
            used[0] = clock.t - 700
    up.last_use, up.sleep = (lambda: used[0]), sleep
    say(store, clock.t - 3600, "user", "hello")
    out = await up.nightly()
    assert len(slept) == 3 and out["status"] == "done" and out["trigger"] == "nightly"


async def test_memory_pass_api_shows_the_last_pass_its_changes_and_undoes_it(tmp_path):
    from test_memory_pass import plan_for
    fn = brain(extract={"claims": [claim_of(1, "Takes tea without sugar", "I always take my tea without sugar")]}, reconcile=plan_for)
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False)
    async with Running(create_app(s, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        got = (await c.get("/api/reflection")).json()
        assert got["memory"] == {"last_pass": None, "last_attempt": None}
        assert (await c.post("/api/reflection/memory/undo")).status_code == 404
        store = Store(str(tmp_path / "a.db"))
        store.set_profile(timezone="Asia/Seoul")
        (tmp_path / "home" / "MEMORY.md").write_text("# MEMORY.md\n\n## About the user\n- Likes tea\n\n## Ongoing\n- Dentist visit is on "
                                                     "2026-11-10. Parking is behind the clinic.\n\n## Decided & done\n")
        store.add_message("user", "I always take my tea without sugar", author="owner")
        out = (await c.post("/api/reflection/memory/run")).json()
        assert out["status"] == "done" and "MEMORY.md" in out["files"]
        last = (await c.get("/api/reflection")).json()["memory"]["last_pass"]
        assert {"at", "status", "code", "added", "tidied", "files", "trigger", "undone", "can_undo"} <= set(last)   # fields are only added
        assert last["can_undo"] and last["trigger"] == "manual" and last["added"] >= 1 and last["stats"]["claims_admitted"] == 1
        changes = (await c.get("/api/reflection/memory")).json()["changes"]
        mem = next(ch for ch in changes if ch["file"] == "MEMORY.md")
        assert "Takes tea without sugar" in mem["added"] and any("Dentist" in x for x in mem["removed"])
        undone = (await c.post("/api/reflection/memory/undo")).json()
        assert "MEMORY.md" in undone["restored"] and undone["last_pass"]["undone"] is True
        assert "Takes tea" not in (tmp_path / "home" / "MEMORY.md").read_text()
        assert (await c.post("/api/reflection/memory/undo")).status_code == 404
        await c.put("/api/reflection/enabled", json={"enabled": False})
        n = len(fn.calls)
        assert (await c.post("/api/reflection/memory/run?force=true")).json()["code"] == "switched_off" and len(fn.calls) == n
