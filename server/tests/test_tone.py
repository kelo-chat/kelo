"""How Kelo talks to the user (profile "tone"): friendly or polite, polite by default. The profile field and its validation, the one
prompt line per value, the texts the model writes to the user on its own (briefing line, follow-ups, proactive messages) told the
same, and the set_tone tool that switches it from the chat."""

from __future__ import annotations

import httpx

from assistant.prompt import DEFAULT_TONE, TONES, PromptBuilder, tone_line, turn_lines
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running, read_events
import test_briefing
import test_followups
import test_proactive
from test_prompt import CONTENT, NOW, turn_text


def test_the_store_reads_polite_until_another_tone_is_saved():
    s = Store()
    assert s.profile()["tone"] == "polite" == DEFAULT_TONE
    assert s.set_profile(tone="friendly")["tone"] == "friendly"
    assert s.set_profile(tone="")["tone"] == "polite"           # emptied: back to the default


def test_one_line_per_tone_only_for_the_user_and_honest_in_both():
    friendly, polite = tone_line("friendly"), tone_line("polite")
    assert "close friend" in friendly and "informal" in friendly and "반말" in friendly and "해요체" not in friendly
    assert "courteously" in polite and "polite form" in polite and "해요체" in polite and "반말" not in polite
    for line in (friendly, polite):
        assert "\n" not in line and len(line) < 520
        assert "what you write to others for them" in line and "no flattery" in line and "set_tone" in line
    assert tone_line("") == tone_line(None) == tone_line("shouty") == polite      # unknown or unset: the default
    assert "set_tone" not in tone_line("friendly", switch=False)                  # one-off texts are not told how to switch
    assert set(TONES) == {"friendly", "polite"}


def test_the_turn_lines_carry_the_tone_next_to_the_names():
    lines = turn_lines(tz_name="UTC", now=NOW, agent_name="Sage", user_name="민지", tone="friendly")
    i = lines.index("The user's name: 민지")
    assert lines[i + 1] == tone_line("friendly")
    assert tone_line("polite") in turn_lines(tz_name="UTC", now=NOW)               # nothing saved: polite
    stable, turn = PromptBuilder("You are {agent_name}.", None, None).build_parts(agent_name="Sage", tone="friendly", now=NOW)
    assert "How you talk to the user" not in stable and tone_line("friendly") in turn   # per user, in the turn's part


def talk_app(tmp_path, seen, steps=None):
    """A real app whose model records each request; `steps` scripts its replies (default: "ok")."""
    replies = iter(steps or [])

    async def fn(provider, request, **k):
        seen.append(request.messages)
        for ev in next(replies, [TextDelta("ok"), Done("stop")]):
            yield ev

    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home"), content_dir=str(CONTENT))
    return create_app(s, stream_fn=fn)


async def test_the_profile_api_defaults_to_polite_and_takes_only_the_two_tones(tmp_path):
    seen = []
    async with Running(talk_app(tmp_path, seen)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/profile")).json()["tone"] == "polite"
        for bad in ("casual", "", "Friendly", "polite "):
            assert (await c.put("/api/profile", json={"tone": bad})).status_code == 422, bad
        assert (await c.get("/api/profile")).json()["tone"] == "polite"
        r = await c.put("/api/profile", json={"tone": "friendly"})
        assert r.status_code == 200 and r.json()["tone"] == "friendly"
        assert (await c.put("/api/profile", json={"agent_name": "Sage"})).json()["tone"] == "friendly"   # other fields leave it
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            await read_events(r)
        assert tone_line("friendly") in turn_text(seen[0]) and tone_line("polite") not in turn_text(seen[0])
        await c.put("/api/profile", json={"tone": "polite"})
        async with c.stream("POST", "/api/talk", json={"text": "again"}) as r:
            await read_events(r)
        assert tone_line("polite") in turn_text(seen[1])
        assert seen[0][0]["content"] == seen[1][0]["content"]                     # the cached system part does not move


async def test_set_tone_switches_it_from_the_chat_without_a_card(tmp_path):
    seen = []
    steps = [[ToolCall("c1", "find_tools", '{"query": "set_tone"}'), Done("tool_calls")],
             [ToolCall("c2", "set_tone", '{"tone": "friendly"}'), Done("tool_calls")],
             [TextDelta("알겠어, 이제 편하게 말할게."), Done("stop")],
             [ToolCall("c3", "set_tone", '{"tone": "loud"}'), Done("tool_calls")],
             [TextDelta("ok"), Done("stop")]]
    app = talk_app(tmp_path, seen, steps)
    tool = next(t for t in app.state.tools if t.name == "set_tone")
    assert not tool.requires_approval and "friendly" in tool.description and "polite" in tool.description
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "반말로 해 줘"}) as r:
            events = await read_events(r)
        assert not any(e["type"] == "approval_request" for e in events)
        out = next(e for e in events if e["type"] == "tool_result" and e.get("name") == "set_tone")["output"]
        assert out.startswith("Saved") and "friendly" in out
        assert (await c.get("/api/profile")).json()["tone"] == "friendly"
        assert (await c.get("/api/agent/status")).json()["tone"] == "friendly"   # the apps' poll picks the change up
        async with c.stream("POST", "/api/talk", json={"text": "다음"}) as r:
            events = await read_events(r)
        bad = next(e for e in events if e["type"] == "tool_result" and e.get("name") == "set_tone")["output"]
        assert bad.startswith("Error") and (await c.get("/api/profile")).json()["tone"] == "friendly"
        assert tone_line("friendly") in turn_text(seen[-1])
    from assistant.workers import MAIN_ONLY
    assert "set_tone" in MAIN_ONLY                                                # a background job never changes it


# ---- the texts the model writes to the user on its own
async def test_a_follow_up_is_written_in_the_saved_tone():
    f, store, clock, sent, writer = test_followups.make()
    store.set_profile(tone="friendly")
    test_followups.login_slot(store)
    f.login_requested("login-tickets", "Tickets", "tickets.example.com", task={"task_id": 3, "source": "chat"})
    clock.t += 30 * 60
    assert await f.tick() == 1
    facts = writer.requests[0].messages[-1]["content"]
    assert tone_line("friendly", switch=False) in facts


async def test_proactive_messages_are_written_in_the_saved_tone(tmp_path):
    for tone in ("polite", "friendly"):
        judge = test_proactive.all_as("보통")
        store, home, clock, p, sent = test_proactive.setup(tmp_path / tone, judge, test_proactive.FakeMail(test_proactive.mails("a")))
        if tone == "friendly":
            store.set_profile(tone="friendly")
        await p.run()
        assert judge.requests and tone_line(tone, switch=False) in judge.requests[-1].messages[-1]["content"]


async def test_the_briefing_line_is_written_in_the_saved_tone(tmp_path):
    seen = []

    class Model(test_briefing.Model):
        async def __call__(self, provider, req, **kw):
            seen.append(req)
            async for ev in super().__call__(provider, req, **kw):
                yield ev

    store, clock, p, b, sent, m, fake = test_briefing.setup(tmp_path, test_briefing.ts(2026, 11, 9, 7, 30), model=Model(),
                                                            unread=[{"uid": "1", "subject": "Hi", "from": "Kim"}])
    store.set_profile(tone="friendly")
    await b.build(clock.t, use_model=True)
    assert seen and tone_line("friendly", switch=False) in seen[0].messages[-1]["content"]
