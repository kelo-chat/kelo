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

CONTENT = Path(__file__).resolve().parents[2] / "server" / "content"
NOW = datetime(2026, 11, 6, 1, 42, 7, tzinfo=timezone.utc)
FOREIGN = re.compile(r"tool_search|subagent\.|cron\.|worker\.|(?-i:SOUL\.md|IDENTITY\.md|USER\.md)|"
                     r"Secure Vault|secure entry|request_api_access|scaffold_connector|INDEX\.md|client_timezone", re.I)


def test_now_line_uses_the_users_zone_and_survives_junk():
    assert now_line("Asia/Seoul", NOW) == "Now: 2026-11-06T10:42+09:00 (Asia/Seoul, Friday)"
    assert now_line("Not/AZone", NOW) == "Now: 2026-11-06T01:42+00:00 (UTC, Friday)" == now_line(None, NOW)
    assert valid_timezone("Europe/Paris") == "Europe/Paris" and valid_timezone("../../etc") is None


# ---- the shipped prompt itself (server/content/prompts/system.md, maintained by hand)
@pytest.fixture(scope="module")
def prompt_text():
    return (CONTENT / "prompts" / "system.md").read_text(encoding="utf-8")


def test_shipped_prompt_is_short_and_names_nothing_from_other_products(prompt_text):
    assert len(prompt_text.encode("utf-8")) <= 15_350   # 15_000 before the goal paragraph of system.md (2026-11-20, card goals-coaching: the owner's text for goals and work items, 580 characters); 14_900 before the line on going on from words written before a tool step (2026-11-20, p-reply-split-bubbles); 14_400 before the lines on a way of responding that holds and setting up a promised later action (2026-11-20, p-call-standing-mode); 14_200 before the line on saying what a search covered and that an empty search is not proof (2026-11-19, p-calendar-query-photo); 14_050 before the line on the checklist and checklist_link (2026-11-19, p-checklist-own-pace); 14_000 before the line on saying plainly when an earlier answer was wrong (2026-11-19, p-recheck-like-reference); 13_350 before round 2 shared shopping rule; 12_250 before round 1 (2026-11-19, card p-shopping-like-reference); 12_000 before the line on delegating phone work (2026-11-16, docs/phone-worker.md)
    assert not FOREIGN.search(prompt_text), FOREIGN.search(prompt_text)
    assert "{agent_name}" in prompt_text and "~/" not in prompt_text
    assert not (CONTENT / "prompts" / "system.original.md").exists() and not (CONTENT / "prompts" / "CHANGES.md").exists()


def test_a_way_of_responding_holds_and_a_promised_later_action_is_set_up(prompt_text):
    # owner 2026-11-20: what the user sets about how Kelo responds holds for everything that follows; a later action Kelo tells about
    # is set up in the same reply, never offered
    assert ("When the user sets how you should respond from now on, that is how you respond to everything that follows until they "
            "change or end it") in prompt_text
    assert "without asking whether to" in prompt_text and "offer to set it up" not in prompt_text
    assert "How you will respond in this conversation from now on is not such a later action." in prompt_text
    for word in ("interpret", "translat"):
        assert word not in prompt_text.lower()


@pytest.mark.parametrize("fact", [
    "Kelo", "[UNTRUSTED CONTENT <id>]", "[END UNTRUSTED CONTENT <id>]", "request_login", "browser_login", "vault_status", "connect_service",
    "add_service_key", "checkout_review", "approved", "wallet_cards", "browser_fill_card", "card numbers", "find_tools",
    "schedule_create", "watch_add", "CHECKLIST.md", "NOTIFY_PREFS.md",
    "journal_search", "extract_document", "view_image", "workroom_run", "assistant.md", "user.md", "MEMORY.md", "AGENTS.md",
    "memory/people/", "Now:", "Keep names, quotes and code", "<message_break/>", "notes/services/"])
def test_shipped_prompt_keeps_the_key_facts(prompt_text, fact):
    assert fact in prompt_text


def test_human_checks_may_be_tried_twice_and_limits_still_mean_stop(prompt_text):
    """The rule in every place the model reads it: try a human check yourself at most twice, no solving services, no tampering
    with the page's scripts, then hand it over; rate limits, locked accounts and refusals still mean stop and tell the user."""
    short = Settings(token=TOKEN).system_prompt
    job = (Path(__file__).resolve().parents[2] / "server" / "src" / "assistant" / "workers.py").read_text(encoding="utf-8")
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
    assert "never a second goal or a work item" in block and "Ask one question at a time" in block
    assert goals_block([]) == ""                                                  # no goals: no goal rules either
    prompt = (CONTENT / "prompts" / "system.md").read_text(encoding="utf-8")
    for moved in ("**Long work.**", "**Waiting for the user.**", "read their page", "Take weekdays from the calendar"):
        assert moved not in prompt, moved
    from assistant.server import SCHEDULE_NOTE                                   # what an automatic run must do: in its own note
    assert "NO_REPLY" in SCHEDULE_NOTE and "schedule_pause" not in SCHEDULE_NOTE and "the user decides" in SCHEDULE_NOTE
    from assistant.schedules import schedule_tools
    from assistant.watchers import watch_tools
    sched = {t.name: t.description for t in schedule_tools(None, None)}
    watch = {t.name: t.description for t in watch_tools(None)}
    assert "task_allow" in sched["schedule_create"]                              # the follow-up question, where the job is made
    assert "start=true" in watch["watch_add"] and "watch_enable" in watch["watch_add"] and "checks once" in watch["watch_enable"]


def test_the_short_rules_are_kelos_own_and_cover_the_same_ground():
    short = Settings(token=TOKEN).system_prompt
    assert not FOREIGN.search(short) and len(short) < 2_500
    for fact in ("Kelo", "UNTRUSTED CONTENT", "request_login", "connect_service", "add_service_key", "checkout_review", "approved",
                 "schedule_create", "delegate", "extract_document"):
        assert fact in short, fact


def test_builder_fills_in_name_time_model_goals_files_skills_and_learned_rules(tmp_path):
    home = Home(tmp_path / "h")
    home.write("MEMORY.md", "# MEMORY.md\n- 민지 likes green tea\n", by="user")
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


def test_react_is_the_last_call_and_nothing_is_written_after_it(prompt_text):
    """The reply is written before the emoji: a model that wrote it again after `react` stored the same text twice (chat-dup, 2026-11-18)."""
    assert "`react` ends the reply: write nothing after it." in prompt_text


def test_the_real_content_loads_and_stays_small():
    b = PromptBuilder.load(CONTENT, None, Skills(CONTENT / "skills"))
    out = b.build(agent_name="Sage", now=NOW)
    assert "You are Sage, a personal agent" in out and "- shopping-and-bookings:" in out and "- work-room:" in out
    assert len(out) < 18_250, len(out)   # 17_750 before the goal setup texts and the work-item rules (2026-11-20, card goals-coaching); 17_500 before the line on going on from words written before a tool step (2026-11-20, p-reply-split-bubbles); 17_100 before the lines on a way of responding that holds and setting up a promised later action (2026-11-20, p-call-standing-mode); 16_900 before the line on saying what a search covered and that an empty search is not proof (2026-11-19, p-calendar-query-photo); 16_750 before the line on the checklist and checklist_link (2026-11-19, p-checklist-own-pace); 16_700 before the line on saying plainly when an earlier answer was wrong (2026-11-19, p-recheck-like-reference); 16_100 before round 2 shared shopping rule; 14_950 before round 1 (2026-11-19, card p-shopping-like-reference); 14_700 before the line on delegating phone work (2026-11-16, docs/phone-worker.md); 14_500 before the line telling the agent to read its help pages (kelo_help) for questions about Kelo itself (2026-11-16, server/content/help); 14_050 before the rule on never promising what nothing will do (2026-11-16, docs/scheduled-runs.md); 13_800 before the lines on showing pictures unasked when looks matter and never giving a path the user cannot open (owner 2026-11-15); 13_100 before the five everyday-life skills joined the skill list (2026-11-15); 12_900 before the line on saying first what a multi-step job will do (backlog 67, owner 2026-11-13: raise the cap a little); 12_800 before the recheck_later line (2026-11-13; the examples it had trimmed were put back); 12_700 before the longer privacy line and the keys line that loads add_service_key at once (2026-11-12; the privacy line was paid for by trimming elsewhere first); 12_500 before the lines on looking facts up and on advice (2026-11-12); 13_200 before the prompt diet, 2026-11-11 (built length 12_056 then); 13_000 before the reply-language line named the user's language (2026-11-11); 12_000 before the lines on how to talk to the user (prompt.tone_line), tap answers, the briefing
    # and the clause on answering from the closest data; 12_700 before remembering the browser on the owner's sign-ins (2026-11-10)
    assert not FOREIGN.search(out)


def test_a_wrong_earlier_answer_is_said_and_its_advice_revisited(prompt_text):
    assert "When you find an earlier answer of yours was wrong, or a new fact changes it, tell the user plainly and revisit the advice that rested on it." in prompt_text
    assert "a re-check" not in prompt_text


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
    connect = {"find_tools", "kelo_help", "read_earlier_messages", "web_search", "web_fetch", "add_service_key", "save_found_secret", "vault_status", "request_login", "request_card", "request_payment_pin", "connector_get", "connector_download", "connector_send", "mcp_list_tools", "mcp_call_tool",
               "connect_service", "email_list", "email_read", "email_send", "email_update",
               "gcal_calendars", "gcal_events", "gcal_create_event", "gcal_update_event", "gcal_delete_event", "phone_calendar_events",
               "phone_calendar_add", "phone_calendar_update", "phone_calendar_delete", "phone_contacts", "phone_messages", "phone_send_text",
               "phone_call", "phone_call_log", "phone_notifications", "phone_health", "briefing_sources", "calendar_events",
               "schedule_create", "schedule_list", "schedule_update", "schedule_delete", "schedule_pause", "schedule_keep_going", "schedule_runs", "task_allow", "journal_search",
               "watch_add", "watch_list", "watch_update", "watch_remove", "watch_dry_run", "watch_enable",
               "ha_watch_add", "ha_watch_list", "ha_watch_remove",
               "delegate", "workers_list", "worker_status", "worker_message", "worker_stop", "resume",
               "await_reply", "followup_snooze", "followup_cancel", "action_result",   # (action_result: offered only while a result is unknown, done_once.py)
               "goal_get", "goal_create", "goal_update", "goal_set_status", "work_create", "work_list", "work_close", "work_log",
               "goal_set_facts", "goal_plan", "goal_step_done", "goal_log", "goal_metric_add", "goal_link_schedule",
               "goal_add_document", "goal_suggest", "quick_replies", "compare_options", "react", "set_tone", "set_owner_settings", "finish_first_chat", "big_event_notice", "briefing", "offer_post",
               "follow_list", "follow_stop", "follow_remove",
               "package_entries", "package_entry_add", "package_entry_delete",   # entries of installed Nest packages (nest.py)
               "nest_draft_create", "nest_draft_write", "nest_draft_check", "nest_draft_delete", "nest_draft_from_skill", "nest_share_github",
               "weather", "my_location", "places_near", "messenger",   # forecasts without a browser, the phone's last location, places near it
               "start_voice_call"}                                       # a call with Kelo in the owner's app (call_tools.py): no files needed
    files = {"read_file", "write_file", "list_files", "read_skill", "search_skills", "extract_document", "generate_image", "save_product_image", "list_product_images", "view_image", "remember", "memory_explain", "forget_plan", "forget_confirm",
             "propose_skill", "storage_cleanup_list", "storage_cleanup", "show_picture", "checklist_link", "event_reminders_set", "event_reminders_list", "event_reminders_remove"}
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
    home.write("memory/people/kim.md", "---\nname: Kim Minsu\naliases: [Minsu, 민수]\nrelation: friend\ngroups: [climbing]\n---\n# Kim", by="user")
    out = PromptBuilder("x", home, None).build(tz_name="Asia/Seoul", now=NOW)
    assert "Calendar (the user's local dates" in out and "Fri 11-06 (today)" in out
    assert "All your files:" in out and "memory/people/kim.md" in out and "MEMORY.md" in out
    assert "- Kim Minsu (aka Minsu, 민수; friend; groups: climbing): memory/people/kim.md" in out


def test_shipped_skills_are_kelos_own_with_front_matter():
    s = Skills(CONTENT / "skills")
    ids = [e["id"] for e in s.index]
    assert set(ids) == {"shopping-and-bookings", "connect-a-service", "documents", "images", "schedules-and-watching",
                        "research", "work-room", "critique-a-plan", "break-down-work", "trips", "meals-and-groceries",
                        "mail-triage", "receipts-and-spending", "subscriptions-and-bills", "goal-coaching"}
    assert all(e["description"] for e in s.index) and not (CONTENT / "skills" / "index.json").exists()
    # a skill folder holds SKILL.md and, optionally, extra .md files it names (read with read_skill's file argument)
    assert not [p for p in (CONTENT / "skills").rglob("*") if p.is_file() and p.name != "SKILL.md" and p.suffix != ".md"]
    for e in s.index:
        body = s.read(e["id"])
        assert not body.startswith("---") and not FOREIGN.search(body), e["id"]
        assert not re.search(r"[\uac00-\ud7a3]", body), e["id"]          # English source
    assert "/opt/" not in "".join(p.read_text() for p in (CONTENT / "skills").rglob("SKILL.md"))


def test_what_changes_every_turn_comes_last_so_the_start_of_the_prompt_can_be_cached(tmp_path):
    from datetime import timedelta
    home = Home(tmp_path / "h")
    home.write("MEMORY.md", "# MEMORY.md\n- a fact\n", by="user")
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
    home.write("memory/x.md", "new fact", by="user")                                  # a file change changes the stable part
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
    line = next(x for x in text.splitlines() if "keep it apart from what you infer" in x)   # 2026-11-19: the known-vs-inferred clause was rewritten
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
    assert not {"browser_click_at", "browser_drag", "places_near"} & CORE_TOOLS
    assert {"browser_login", "browser_look", "browser_open", "weather"} <= CORE_TOOLS
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host="127.0.0.1", home_dir=str(tmp_path / "home"),
                              content_dir=str(CONTENT)))
    desc = {t.name: t.description for t in app.state.tools}
    assert "(never someone else's), accept an offer to keep this browser signed in or skip verification next time" in desc["browser_login"]
    assert "not saving the password, marketing or cookie consent, a recovery phone or email, or security settings" in desc["browser_login"]
    assert "find_tools" in desc["browser_look"] and "browser_click_at" in desc["browser_look"] and "browser_drag" in desc["browser_look"]
    assert "view" not in next(t for t in app.state.tools if t.name == "browser_open").parameters["properties"]


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


def test_the_prompt_states_each_principle_once(prompt_text):
    # 2026-11-18: the request is the go-ahead, the card is the question, look up what is missing, check before adding, say it once
    for phrase in ("The user's request is the go-ahead for what it names",
                   "do not also ask it in chat",
                   "never borrow a value from an unrelated earlier answer",
                   "Asked what you or the app did, set up, saved or shows: read that record first",
                   "change a match instead of adding a twin",
                   "A step whose result is unknown is checked, never repeated",
                   "Write the answer once",
                   "already shown as its own message"):
        assert prompt_text.count(phrase) == 1, phrase
    for gone in ("When the user answers a question, act on that answer", "**Apps on the Android device.**"):
        assert gone not in prompt_text, gone


def _tool_texts(app):
    """Every description a model reads for a tool: its own text and every parameter description, nested ones included."""
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "description" and isinstance(v, str):
                    yield v
                else:
                    yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
    for t in app.state.tools:
        yield t.name, t.description or "", list(walk(t.parameters))


def _test_app(tmp_path):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host="127.0.0.1", home_dir=str(tmp_path / "home"),
                               content_dir=str(CONTENT)))


def test_tool_texts_never_ask_to_confirm_what_the_request_covers(tmp_path):
    # 2026-11-18 (watch double confirm, chat-dup): tool texts said "show the dry run the user saw" and "ask them to confirm",
    # while the prompt says the request is the go-ahead and the card is the question
    bad = re.compile(r"show (this|it|the result) to the user before|a dry run the user saw|\(they approve\)|confirmed [^.]* in chat|"
                     r"(?<!never )ask (the user|them) (to confirm|whether to (turn|switch|start|do))", re.I)   # "never ask them to confirm" is the rule
    for name, desc, params in _tool_texts(_test_app(tmp_path)):
        for text in [desc, *params]:
            assert not bad.search(text), (name, bad.search(text).group(0))


def test_tool_texts_are_english_and_name_no_product_in_a_rule(tmp_path):
    # the model reads these in English; a product name or a Korean trigger word in a rule leaks into unrelated turns
    for name, desc, params in _tool_texts(_test_app(tmp_path)):
        if name == "set_tone":
            continue
        for text in [desc, *params]:
            assert not re.search(r"[가-힣]", text), (name, text[:80])
        if name in ("connector_send", "mcp_call_tool"):
            assert "Home Assistant" not in desc, name


def test_the_core_tool_texts_stay_small(tmp_path):
    # 2026-11-18: core tool texts (own text + parameter texts) 13_067 bytes before watch-one-step and follow-dates-promise,
    # 14_234 on main after them, 14_324 with the tool-text pass, 14_533 with the profile argument of delegate and browser_open
    # (named browser profiles); 15_043 with shopping rules in quick_replies, recheck_later, web_search (2026-11-19, card p-shopping-like-reference)
    # 15_242 after the three browser cards of 2026-11-19 together (click-first browser_open text, page pop-up notes, stale-ref card
    # words; each fitted alone, together they passed 15_200)
    app = _test_app(tmp_path)
    total = 0
    for name, desc, params in _tool_texts(app):
        if name in CORE_TOOLS:
            total += len(desc.encode("utf-8")) + sum(len(p.encode("utf-8")) for p in params)
    # owner 2026-11-20 (D11): the quick_replies text is the owner's exact wording (one question, at most three answers, connect, no catch-alls)
    assert total < 15_800, total


def test_the_watchers_line_names_the_ones_on_and_never_their_values_or_addresses():
    # 2026-11-18 (watch double confirm): a second watcher was added for the one already set up, because the turn did not show it
    from assistant.prompt import turn_lines, watchers_line
    rows = [{"id": 4, "name": "Price", "enabled": 1, "every_minutes": 30, "last_value": "69.99 https://shop.example.test/x", "url": "https://shop.example.test/x"},
            {"id": 3, "name": "Old", "enabled": 0, "every_minutes": 60, "last_value": "secret page text", "url": "https://old.example.test"}]
    line = watchers_line(rows)
    assert "Watchers: #4 Price (on, every 30 min)" in line and "(+1 off)" in line and "watch_list" in line
    assert "69.99" not in line and "shop.example" not in line and "secret" not in line and "Old" not in line
    assert watchers_line(None) == "" and watchers_line([]) == ""
    lines = turn_lines(tz_name="UTC", now=NOW, watchers=rows)
    assert any(ln.startswith("Watchers: #4") for ln in lines)
    assert not any(ln.startswith("Watchers") for ln in turn_lines(tz_name="UTC", now=NOW, watchers=None))


def test_the_jobs_line_names_only_the_jobs_waiting_for_the_owner():
    from assistant.prompt import jobs_line, turn_lines
    assert jobs_line([]) == "" and jobs_line(None) == "" and jobs_line([{"id": "w1", "status": "done", "title": "x"}]) == ""
    rows = [{"id": "w1", "title": "shop", "status": "needs_input", "question_id": "q_abc"},
            {"id": "w2", "title": "booking", "status": "blocked"}, {"id": "w3", "title": "x", "status": "done"}]
    line = jobs_line(rows)
    assert line.startswith("Jobs waiting for the owner: w1 \"shop\" (needs_input, question q_abc); w2 \"booking\" (blocked)")
    assert "w3" not in line and "worker_stop" in line and "answer_to" in line
    assert "Jobs waiting for the owner:" in "\n".join(turn_lines(tz_name=None, jobs=rows))
    assert "Jobs waiting" not in "\n".join(turn_lines(tz_name=None))
    many = [{"id": f"w{i}", "title": "t", "status": "blocked"} for i in range(8)]
    assert "(+3 more)" in jobs_line(many)
