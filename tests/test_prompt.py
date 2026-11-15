import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from assistant.agent.toolbox import CORE_TOOLS
from assistant.home import Home, Skills
from assistant.prompt import PromptBuilder, now_line, valid_timezone
from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, read_events, scripted  # noqa: F401

CONTENT = Path(__file__).resolve().parents[1] / "content"
NOW = datetime(2026, 11, 6, 1, 42, 7, tzinfo=timezone.utc)
FOREIGN = re.compile(r"tool_search|subagent\.|cron\.|worker\.|(?-i:SOUL\.md|IDENTITY\.md|USER\.md)|"
                     r"Secure Vault|secure entry|request_api_access|scaffold_connector|INDEX\.md|client_timezone", re.I)


def test_now_line_uses_the_users_zone_and_survives_junk():
    assert now_line("Asia/Seoul", NOW) == "Now: 2026-11-06T10:42+09:00 (Asia/Seoul, Friday)"
    assert now_line("Not/AZone", NOW) == "Now: 2026-11-06T01:42+00:00 (UTC, Friday)" == now_line(None, NOW)
    assert valid_timezone("Europe/Paris") == "Europe/Paris" and valid_timezone("../../etc") is None


# ---- the shipped prompt itself (content/prompts/system.md, maintained by hand)
@pytest.fixture(scope="module")
def prompt_text():
    return (CONTENT / "prompts" / "system.md").read_text(encoding="utf-8")


def test_shipped_prompt_is_short_and_names_nothing_from_other_products(prompt_text):
    assert len(prompt_text.encode("utf-8")) <= 12_000
    assert not FOREIGN.search(prompt_text), FOREIGN.search(prompt_text)
    assert "{agent_name}" in prompt_text and "~/" not in prompt_text
    assert not (CONTENT / "prompts" / "system.original.md").exists() and not (CONTENT / "prompts" / "CHANGES.md").exists()


@pytest.mark.parametrize("fact", [
    "Kelo", "[UNTRUSTED CONTENT <id>]", "[END UNTRUSTED CONTENT <id>]", "request_login", "browser_login", "vault_status", "connect_service",
    "add_service_key", "confirm_purchase", "APPROVED", "wallet_cards", "browser_fill_card", "card numbers", "find_tools",
    "schedule_create", "watch_add", "CHECKLIST.md", "NOTIFY_PREFS.md",
    "journal_search", "extract_document", "view_image", "workroom_run", "assistant.md", "user.md", "MEMORY.md", "AGENTS.md",
    "memory/people/", "Now:", "Keep names, quotes and code", "<message_break/>", "notes/services/"])
def test_shipped_prompt_keeps_the_key_facts(prompt_text, fact):
    assert fact in prompt_text


def test_human_checks_may_be_tried_twice_and_limits_still_mean_stop(prompt_text):
    """The rule in every place the model reads it: try a human check yourself at most twice, no solving services, no tampering
    with the page's scripts, then hand it over; rate limits, locked accounts and refusals still mean stop and tell the user."""
    short = Settings(token=TOKEN).system_prompt
    job = (Path(__file__).resolve().parents[1] / "src" / "assistant" / "workers.py").read_text(encoding="utf-8")
    for text in (prompt_text, short, job):
        assert "at most twice" in text and "never solve" not in text.lower() and "Never solve" not in text
        assert not re.search(r"captcha|recaptcha|hcaptcha|turnstile|cloudflare", text, re.I)
    assert "outside solving services" in prompt_text and "page's scripts" in prompt_text and "handed to the user" in prompt_text
    assert "rate limits, locked accounts and refusals mean stop and tell the user" in prompt_text
    assert "no outside solving services" in short


def test_facts_moved_out_of_the_prompt_sit_where_the_model_sees_them():
    """Each rule is written once, where it matters: the weekday rule on the calendar line, how to hand off long work on the delegate
    tool (always shown), how to work on goals next to the goals themselves, the person-page rule on the roster."""
    from assistant.prompt import goals_block, turn_lines
    lines = "\n".join(turn_lines(tz_name="UTC", now=NOW))
    assert "take weekdays from here, never work one out" in lines
    assert "The user's language: English. Write every message in it, also between steps" in lines   # no English drift mid-turn
    assert "switch only if the user writes in or asks for another" in lines and "when pages, mails or tools use another" in lines
    from assistant.prompt import language_line
    assert language_line("ko").startswith("The user's language: Korean. Write every message in it")
    assert "delegate" in CORE_TOOLS
    block = goals_block([{"id": 1, "title": "g", "status": "tracking"}])
    assert "never silently" in block and "declined stays unasked" in block
    assert goals_block([]) == ""                                                  # no goals: no goal rules either
    prompt = (CONTENT / "prompts" / "system.md").read_text(encoding="utf-8")
    for moved in ("**Long work.**", "**Goals.**", "**Waiting for the user.**", "read their page", "Take weekdays from the calendar"):
        assert moved not in prompt, moved
    from assistant.server import SCHEDULE_NOTE                                   # what an automatic run must do: in its own note
    assert "NO_REPLY" in SCHEDULE_NOTE and "schedule_pause" in SCHEDULE_NOTE
    from assistant.schedules import schedule_tools
    from assistant.watchers import watch_tools
    sched = {t.name: t.description for t in schedule_tools(None, None)}
    watch = {t.name: t.description for t in watch_tools(None)}
    assert "task_allow" in sched["schedule_create"]                              # the follow-up question, where the job is made
    assert "watch_dry_run" in watch["watch_add"] and "show the user" in watch["watch_dry_run"] and "dry run" in watch["watch_enable"]


def test_the_short_rules_are_kelos_own_and_cover_the_same_ground():
    short = Settings(token=TOKEN).system_prompt
    assert not FOREIGN.search(short) and len(short) < 2_500
    for fact in ("Kelo", "UNTRUSTED CONTENT", "request_login", "connect_service", "add_service_key", "confirm_purchase", "APPROVED",
                 "schedule_create", "delegate", "extract_document"):
        assert fact in short, fact


def test_builder_fills_in_name_time_model_goals_files_skills_and_learned_rules(tmp_path):
    home = Home(tmp_path / "h")
    home.write("MEMORY.md", "# MEMORY.md\n- 민지 likes green tea\n")
    (tmp_path / "s" / "docs").mkdir(parents=True)
    (tmp_path / "s" / "docs" / "SKILL.md").write_text("---\nname: Documents\ndescription: Write docs.\n---\n# Documents")
    b = PromptBuilder("You are {agent_name}.\n## Style\n", home, Skills(tmp_path / "s"))
    b.learned = lambda: "## Learned Preferences\n- Conclusion first"
    out = b.build(agent_name="Sage", user_name="민지", provider="antigravity-oauth", model="gemini-3.8-flash-low",
                  goals=[{"title": "매일 운동", "note": "30분", "status": "tracking"}, {"title": "끝난 일", "status": "done"}],
                  tz_name="Asia/Seoul", now=NOW)
    assert out.startswith("You are Sage.") and "Fri 11-06 (today)" in out
    assert "Now: 2026-11-06T10:42+09:00 (Asia/Seoul, Friday)" in out
    assert "Model in use: antigravity-oauth / gemini-3.8-flash-low" in out and "The user's name: 민지" in out
    assert "- 매일 운동 · note: 30분" in out and "끝난 일" not in out
    assert "### MEMORY.md" in out and "green tea" in out and "- docs: Write docs." in out
    assert out.index("## Skills") < out.index("## Learned Preferences") < out.index("## Your Files") < out.index("## This Turn")


def test_the_real_content_loads_and_stays_small():
    b = PromptBuilder.load(CONTENT, None, Skills(CONTENT / "skills"))
    out = b.build(agent_name="Sage", now=NOW)
    assert "You are Sage, a personal agent" in out and "- shopping-and-bookings:" in out and "- work-room:" in out
    assert len(out) < 13_100, len(out)   # 12_900 before the line on saying first what a multi-step job will do (backlog 67, owner 2026-11-13: raise the cap a little); 12_800 before the recheck_later line (2026-11-13; the examples it had trimmed were put back); 12_700 before the longer privacy line and the keys line that loads add_service_key at once (2026-11-12; the privacy line was paid for by trimming elsewhere first); 12_500 before the lines on looking facts up and on advice (2026-11-12); 13_200 before the prompt diet, 2026-11-11 (built length 12_056 then); 13_000 before the reply-language line named the user's language (2026-11-11); 12_000 before the lines on how to talk to the user (prompt.tone_line), tap answers, the briefing
    # and the clause on answering from the closest data; 12_700 before remembering the browser on the owner's sign-ins (2026-11-10)
    assert not FOREIGN.search(out)


# ---- through the server
def turn_text(msgs):
    """The turn's own lines (time, calendar, schedules, goals): a note right before the user's message, after the cached prefix."""
    from assistant.server import TURN_NOTE
    return next(m["content"] for m in msgs if isinstance(m.get("content"), str) and m["content"].startswith(TURN_NOTE))


def talk_app(tmp_path, seen, **kw):
    async def fn(provider, request, **k):
        seen.append(request.messages)
        yield TextDelta("ok"); yield Done("stop")

    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home"),
                 content_dir=str(CONTENT), **kw)
    return create_app(s, stream_fn=fn)


async def test_talk_uses_the_written_prompt_with_the_clients_time_and_the_agents_files(tmp_path):
    seen = []
    async with Running(talk_app(tmp_path, seen)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"user_name": "민지", "agent_name": "Sage"})
        await c.put("/api/files/MEMORY.md", json={"content": "# MEMORY.md\n- 민지 likes green tea\n"})
        async with c.stream("POST", "/api/talk", json={"text": "hi", "timezone": "Asia/Seoul"}) as r:
            await read_events(r)
        assert (await c.get("/api/profile")).json()["timezone"] == "Asia/Seoul"      # remembered for next time
        async with c.stream("POST", "/api/talk", json={"text": "again"}) as r:       # client sent no zone: use the saved one
            await read_events(r)
    system = seen[0][0]["content"]
    assert system.startswith("# Kelo") and "You are Sage, a personal agent" in system
    assert "green tea" in system and "## Skills" in system and not FOREIGN.search(system)
    assert not re.search(r"Now: \d", system) and "## This Turn" not in system            # nothing that changes every turn
    assert re.search(r"Now: \d{4}-\d\d-\d\dT\d\d:\d\d\+09:00 \(Asia/Seoul, \w+day\)", turn_text(seen[0]))   # the clock: in the turn's note
    assert seen[0][-1]["content"] == "hi" and seen[1][-1]["content"] == "again"          # the user's words go as they are
    assert seen[0][-2]["content"] == turn_text(seen[0])                                  # the turn's note comes right before them
    assert "(Asia/Seoul," in turn_text(seen[1])                                          # saved zone used when the client sends none
    assert seen[0][0]["content"] == seen[1][0]["content"]                                # the whole system text is the same next turn


async def test_the_agents_tools_exist_only_when_files_are_enabled_and_can_write_memory(tmp_path):
    names = []

    async def fn(provider, request, **k):
        names.append([t["function"]["name"] for t in request.tools])
        yield TextDelta("ok"); yield Done("stop")

    on = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home"), content_dir=str(CONTENT))
    off = Settings(token=TOKEN, provider="p", model="m", cdp_host=None)
    every = []
    for s in (on, off):
        app = create_app(s, stream_fn=fn)
        every.append({t.name for t in app.state.tools})
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
                await read_events(r)
    connect = {"find_tools", "web_search", "web_fetch", "add_service_key", "save_found_secret", "vault_status", "request_login", "request_card", "request_payment_pin", "connector_get", "connector_download", "connector_send", "mcp_list_tools", "mcp_call_tool",
               "connect_service", "email_list", "email_read", "email_send", "email_update",
               "gcal_calendars", "gcal_events", "gcal_create_event", "gcal_update_event", "gcal_delete_event", "phone_calendar_events",
               "phone_calendar_add", "phone_calendar_update", "phone_calendar_delete", "phone_contacts", "phone_messages", "phone_send_text",
               "phone_call", "phone_call_log", "phone_notifications", "phone_health", "briefing_sources", "calendar_events",
               "schedule_create", "schedule_list", "schedule_update", "schedule_delete", "schedule_pause", "schedule_retry", "schedule_runs", "task_allow", "journal_search",
               "watch_add", "watch_list", "watch_update", "watch_remove", "watch_dry_run", "watch_enable",
               "ha_watch_add", "ha_watch_list", "ha_watch_remove",
               "delegate", "workers_list", "worker_status", "worker_message", "worker_stop", "resume",
               "await_reply", "followup_snooze", "followup_cancel", "action_result",   # (action_result: offered only while a result is unknown, done_once.py)
               "goal_get", "goal_set_facts", "goal_plan", "goal_step_done", "goal_log", "goal_metric_add", "goal_link_schedule",
               "goal_add_document", "goal_suggest", "quick_replies", "compare_options", "react", "set_tone", "big_event_notice", "briefing", "recheck_later", "recheck_watch", "recheck_stop", "recheck_result", "offer_post",
               "package_entries", "package_entry_add", "package_entry_delete",   # entries of installed Nest packages (nest.py)
               "weather", "my_location", "places_near", "messenger"}   # forecasts without a browser, the phone's last location, places near it
    files = {"read_file", "write_file", "list_files", "read_skill", "search_skills", "extract_document", "generate_image", "save_product_image", "list_product_images", "view_image", "remember",
             "propose_skill", "storage_cleanup_list", "storage_cleanup", "show_picture"}
    assert every[0] == files | connect
    assert every[1] == connect                                       # connections need no files area
    # tools on demand: the first request shows only the core set (those this server has); the rest load with find_tools
    shown = CORE_TOOLS - {"action_result"}                            # that one is hidden until some step's result is unknown: no prompt cost
    assert set(names[0]) == shown & every[0] and set(names[1]) == shown & every[1]
    assert {"read_file", "write_file", "web_search", "find_tools", "quick_replies", "delegate"} <= set(names[0])


async def test_files_api_edits_the_agents_files_and_refuses_everything_else(tmp_path):
    async with Running(talk_app(tmp_path, [])) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/files")).status_code == 401
        c.headers.update(AUTH)
        tree = {f["path"]: f for f in (await c.get("/api/files")).json()}
        assert tree["MEMORY.md"]["writable"] and {"assistant.md", "user.md", "AGENTS.md"} <= set(tree)
        assert not {"SOUL.md", "USER.md", "IDENTITY.md"} & set(tree)
        assert (await c.put("/api/files/memory/people/kim.md", json={"content": "# Kim"})).json()["ok"]
        assert (await c.get("/api/files/memory/people/kim.md")).json()["content"] == "# Kim"
        assert (await c.put("/api/files/evil.md", json={"content": "x"})).status_code == 422
        assert (await c.get("/api/files/..%2F..%2Fetc%2Fpasswd")).status_code == 404
        assert (await c.get("/api/files/.history/x")).status_code == 404
    async with Running(create_app(Settings(token=TOKEN, cdp_host=None), tools=[], stream_fn=scripted())) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/files")).status_code == 404   # feature off


async def test_connectors_list_the_imported_catalog_honestly(tmp_path):
    async with Running(talk_app(tmp_path, [])) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        items = (await c.get("/api/connectors")).json()
    by = {x["id"]: x for x in items}
    from assistant.presets import UNSUPPORTED
    assert "browser" not in by                             # the browser is part of the app, not a connector
    assert not set(UNSUPPORTED) & set(by)                                            # what cannot work here is not listed at all
    assert by["github"]["status"] == "available" and by["github"]["connect"] == "device" and by["github"]["description"]
    assert by["github"]["name"] == "GitHub" and by["github"]["category"]
    assert by["outlook_mail"]["connect"] == "device" and by["outlook_mail"]["via"] == "microsoft"
    assert by["notion"]["status"] == "available" and by["notion"]["connect"] == "mcp"
    assert by["figma"]["connect"] == "key"                                          # Figma's MCP takes only approved apps: its REST API with a token
    assert by["gmail"]["status"] == "available" and by["gmail"]["connect"] == "mail"
    assert by["slack"]["connect"] == "key" and by["google_sheets"]["via"] == "google"
    assert "expedia" not in by and "canva" not in by                                # browser sites need the browser, which this test has not
    assert all(x["connect"] for x in items) and "coming_soon" not in {x["status"] for x in items}


def test_calendar_and_file_listing_are_in_the_context_so_nothing_needs_computing_or_looking_up(tmp_path):
    from assistant.prompt import calendar
    cal = calendar("Asia/Seoul", NOW, days=3)
    assert cal == "Fri 11-06 (today), Sat 11-07, Sun 11-08"
    home = Home(tmp_path / "h")
    home.write("memory/people/kim.md", "---\nname: Kim Minsu\naliases: [Minsu, 민수]\nrelation: friend\ngroups: [climbing]\n---\n# Kim")
    out = PromptBuilder("x", home, None).build(tz_name="Asia/Seoul", now=NOW)
    assert "Calendar (the user's local dates" in out and "Fri 11-06 (today)" in out
    assert "All your files:" in out and "memory/people/kim.md" in out and "MEMORY.md" in out
    assert "- Kim Minsu (aka Minsu, 민수; friend; groups: climbing): memory/people/kim.md" in out


def test_shipped_skills_are_kelos_own_with_front_matter():
    s = Skills(CONTENT / "skills")
    ids = [e["id"] for e in s.index]
    assert set(ids) == {"shopping-and-bookings", "connect-a-service", "documents", "images", "schedules-and-watching",
                        "research", "work-room", "critique-a-plan", "break-down-work"}
    assert all(e["description"] for e in s.index) and not (CONTENT / "skills" / "index.json").exists()
    assert not [p for p in (CONTENT / "skills").rglob("*") if p.is_file() and p.name != "SKILL.md"]
    for e in s.index:
        body = s.read(e["id"])
        assert not body.startswith("---") and not FOREIGN.search(body), e["id"]
        assert not re.search(r"[\uac00-\ud7a3]", body), e["id"]          # English source
    assert "/opt/" not in "".join(p.read_text() for p in (CONTENT / "skills").rglob("SKILL.md"))


def test_what_changes_every_turn_comes_last_so_the_start_of_the_prompt_can_be_cached(tmp_path):
    from datetime import timedelta
    home = Home(tmp_path / "h")
    home.write("MEMORY.md", "# MEMORY.md\n- a fact\n")
    pb = PromptBuilder.load(CONTENT, home, Skills(CONTENT / "skills"))
    a_stable, a_turn = pb.build_parts(agent_name="Sage", goals=[{"title": "goal-alpha-xyz", "status": "tracking"}], tz_name="Asia/Seoul",
                                      now=NOW)
    b_stable, b_turn = pb.build_parts(agent_name="Sage", goals=[{"title": "goal-beta-xyz", "status": "tracking"}], tz_name="Asia/Seoul",
                                      now=NOW + timedelta(minutes=1))
    assert a_stable == b_stable and a_turn != b_turn                                     # a minute later: the same bytes up front
    assert "## Your Files" in a_stable and "a fact" in a_stable and not re.search(r"Now: \d", a_stable) and "goal-alpha" not in a_stable
    assert a_turn.startswith("## This Turn") and "Now:" in a_turn and "goal-alpha-xyz" in a_turn
    whole = pb.build(agent_name="Sage", tz_name="Asia/Seoul", now=NOW)
    assert whole.index("## Skills") < whole.index("## Your Files") < whole.index("## This Turn")   # one text: the turn's lines last
    home.write("memory/x.md", "new fact")                                                # a file change changes the stable part
    assert pb.build_parts(agent_name="Sage", tz_name="Asia/Seoul", now=NOW)[0] != a_stable


def test_skills_that_need_a_missing_service_are_named_only_in_a_closing_line():
    sk = Skills(CONTENT / "skills")
    plain = sk.index_text()
    assert "Not usable" not in plain and any(ln.startswith("- shopping-and-bookings:") for ln in plain.splitlines())   # no information: all full
    none = sk.index_text(set())
    assert not any(ln.startswith("- shopping-and-bookings:") for ln in none.splitlines())
    tail = next(ln for ln in none.splitlines() if ln.startswith("Not usable"))
    assert "shopping-and-bookings" in tail and "research" not in tail
    assert any(ln.startswith("- documents:") for ln in none.splitlines())                   # needs nothing: keeps its line
    assert any(ln.startswith("- research:") for ln in none.splitlines())                    # web_search / web_fetch need no browser
    assert any(ln.startswith("- shopping-and-bookings:") for ln in sk.index_text({"browser"}).splitlines())


def test_skills_can_be_searched_by_words_from_a_request():
    sk = Skills(CONTENT / "skills")
    ids = [e["id"] for e in sk.search("book a hotel")]
    assert "shopping-and-bookings" in ids[:2] and sk.search("zzzzqqq") == [] and sk.search("") == []


def test_the_next_schedules_are_in_every_turn_with_readable_times_and_how_to_change_them(tmp_path):
    from assistant.prompt import schedules_line, when_text
    base = NOW.timestamp()                                       # Fri 2026-11-06 10:42 in Seoul
    rows = [{"id": 7, "name": "Weekly report", "enabled": True, "next_run_ts": base + 4 * 86400, "timezone": "Asia/Seoul"},
            {"id": 3, "name": "Morning news", "enabled": True, "next_run_ts": base + 3600, "timezone": "Asia/Seoul"},
            {"id": 4, "name": "Paused one", "enabled": False, "next_run_ts": None, "timezone": "Asia/Seoul"},
            {"id": 5, "name": "Tomorrow run", "enabled": True, "next_run_ts": base + 86400, "timezone": "Asia/Seoul"}]
    rows += [{"id": 10 + i, "name": f"job{i}", "enabled": True, "next_run_ts": base + (10 + i) * 86400, "timezone": "Asia/Seoul"} for i in range(4)]
    line = schedules_line(rows, "Asia/Seoul", NOW)
    assert line.startswith("Next scheduled jobs (user's local time): #3 Morning news — today 11:42; #5 Tomorrow run — tomorrow 10:42; "
                           "#7 Weekly report — Tue 11-10 10:42;")
    assert "job1" in line and "job2" not in line and "Paused one" not in line and "(+3 more)" in line   # five at most
    assert "schedule_* tools" in line
    assert when_text(base + 400 * 86400, "Asia/Seoul", NOW).startswith("Sat 2027-12-11")
    assert schedules_line([], "UTC", NOW).startswith("Scheduled jobs: none saved") and schedules_line(None, "UTC", NOW) == ""
    assert "none coming up (1 paused or off)" in schedules_line(rows[2:3], "UTC", NOW)
    out = PromptBuilder("x", None, None).build(tz_name="Asia/Seoul", now=NOW, schedules=rows)
    assert out.index("## This Turn") < out.index("#3 Morning news")


async def test_a_schedule_made_in_the_app_is_in_the_agents_next_turn(tmp_path):
    seen = []
    async with Running(talk_app(tmp_path, seen)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "hi", "timezone": "Asia/Seoul"}) as r:
            await read_events(r)
        made = await c.post("/api/schedules", json={"name": "Plant watering", "prompt": "remind me to water the plants",
                                                    "cron": "0 9 * * *", "timezone": "Asia/Seoul"})
        assert made.status_code in (200, 201), made.text
        async with c.stream("POST", "/api/talk", json={"text": "again"}) as r:
            await read_events(r)
    assert "Scheduled jobs: none saved" in turn_text(seen[0])
    assert re.search(r"Next scheduled jobs \(user's local time\): #\d+ Plant watering — (today|tomorrow) 09:00", turn_text(seen[1]))


def test_missing_data_is_answered_from_data_on_the_same_thing_never_another():
    """One general clause in the known-vs-inferred line (no task-specific rule): without the exact data, the answer is built from the
    data on the same period, place or item, and data on another time or thing never stands in for it."""
    text = (CONTENT / "prompts" / "system.md").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if "Keep what you know apart from what you infer" in x)
    assert "lacking the exact data, answer from data on the same period, place or item, never another time's or thing's" in line
    assert "give the basis for a claim" in line
    assert "weather" not in text.lower()


def test_the_prompt_says_to_finish_the_job_and_to_explain_when_blocked():
    text = (CONTENT / "prompts" / "system.md").read_text(encoding="utf-8")
    for phrase in ("try 2-3 other ways yourself", "lacks a bulk or one-click feature", "Lead with your next step, not what you cannot do",
                   "what you tried, why each failed and your recommended option", "ask one question", "what is done, what is left and why",
                   "keep going until done or really stopped", "never ask whether to continue"):
        assert phrase in text, phrase
    assert "rate limits, locked accounts and refusals mean stop and tell the user" in text   # the two rules stay side by side


def test_rules_moved_by_the_prompt_diet_sit_where_they_matter(tmp_path):
    """Each rule is said once, where the step happens: the owner's "remember this browser" rule on browser_login (a core tool and the
    only way the owner's sign-in is made), the fill-in card sentence in the system prompt only, the rare browser and place tools
    loaded with find_tools but named where the model meets the need, the work room sentence not promising a tool that may be absent."""
    text = (CONTENT / "prompts" / "system.md").read_text(encoding="utf-8")
    assert "remember this browser" not in text and "tell the user in one short sentence to fill it in below" in text
    assert "when the work room is available" in text
    assert not {"browser_click_at", "browser_drag", "browser_view", "places_near"} & CORE_TOOLS
    assert {"browser_login", "browser_look", "browser_open", "weather"} <= CORE_TOOLS
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host="127.0.0.1", home_dir=str(tmp_path / "home"),
                              content_dir=str(CONTENT)))
    desc = {t.name: t.description for t in app.state.tools}
    assert "(never someone else's), accept an offer to keep this browser signed in or skip verification next time" in desc["browser_login"]
    assert "not saving the password, marketing or cookie consent, a recovery phone or email, or security settings" in desc["browser_login"]
    assert "find_tools" in desc["browser_look"] and "browser_click_at" in desc["browser_look"] and "browser_drag" in desc["browser_look"]
    view = next(t for t in app.state.tools if t.name == "browser_open").parameters["properties"]["view"]["description"]
    assert "load browser_view with find_tools" in view


async def test_slash_new_starts_a_fresh_conversation_for_the_model_and_keeps_the_messages(tmp_path):
    """2026-11-12: the owner typed /clear and /new; Kelo said "a new session" and then summarised everything before."""
    seen = []
    async with Running(talk_app(tmp_path, seen)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for text in ("카카오 체험단 신청해", "/new", "안녕"):
            async with c.stream("POST", "/api/talk", json={"text": text}) as r:
                await read_events(r)
        stored = [m["content"] for m in (await c.get("/api/messages")).json()["items"] if m["role"] == "user"] \
            if isinstance((await c.get("/api/messages")).json(), dict) else []
    before, at_new, after = seen
    assert any("카카오 체험단 신청해" == m.get("content") for m in before)
    flat = lambda msgs: " ".join(str(m.get("content")) for m in msgs)   # noqa: E731
    assert "new conversation" in flat(at_new) and "카카오 체험단 신청해" not in flat(at_new)
    assert "카카오 체험단 신청해" not in flat(after) and after[-1]["content"] == "안녕"
    assert not stored or "카카오 체험단 신청해" in stored   # the messages stay on screen


async def test_a_sent_briefing_step_is_a_yes_and_other_text_is_not(tmp_path):
    """2026-11-12: the owner tapped the card's step "…정리해 드릴까요?" and Kelo answered "원하시면 …" instead of doing it."""
    seen = []
    app = talk_app(tmp_path, seen)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        b = app.state.briefing
        card = {"day": "2026-11-12", "suggestions": [{"text": "심사 결과 메일 확인하고 다음 단계 정리해줘", "refs": []}]}
        b.store._run("INSERT INTO briefings(day, message_id, card, created_at) VALUES (?, ?, ?, ?)",
                     ("2026-11-12", None, json.dumps(card, ensure_ascii=False), b.clock()))
        for text in ("심사 결과 메일 확인하고  다음 단계 정리해줘", "안녕"):
            async with c.stream("POST", "/api/talk", json={"text": text}) as r:
                await read_events(r)
    assert "It is a yes: do it now" in seen[0][-1]["content"] and seen[0][-1]["content"].startswith("심사 결과 메일")
    assert seen[1][-1]["content"] == "안녕"


def test_briefing_steps_are_asked_for_as_the_owners_own_request():
    from assistant.briefing import PROMPT
    assert "the owner's own short request" in PROMPT and "Not a question" in PROMPT
