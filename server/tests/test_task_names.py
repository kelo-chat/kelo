"""A task's name in Work: a short title made from the request by the background model (task_names.py), not the message copied."""

import asyncio

import httpx

from assistant.providers import Done, TextDelta
from assistant.server import create_app
from assistant.task_names import NAME_PROMPT, TaskNamer, clean_name

from test_agent_profile import answering, app_settings
from test_server import AUTH, Running, follow

LONG = "가상폰으로 플레이스토어 로그인 화면 까지만 가서 스크린샷찍어서 보내줘"


def test_clean_name_strips_quotes_labels_markdown_and_punctuation():
    assert clean_name('"Play Store sign-in screenshot"') == "Play Store sign-in screenshot"
    assert clean_name("제목: Play 스토어 로그인 화면 스크린샷.") == "Play 스토어 로그인 화면 스크린샷"
    assert clean_name("**`쿠팡 리뷰 4개 등록`**\nsecond line") == "쿠팡 리뷰 4개 등록"
    assert clean_name("# Name: “Weather check”!!") == "Weather check"
    assert clean_name("   ") == "" and clean_name("") == ""


def test_clean_name_drops_an_answer_and_cuts_a_long_name_at_a_word():
    assert clean_name("Sure! " + "I can do that for you right now and then some more words " * 3) == ""
    name = clean_name("Review all the open pull requests of the server repository today")
    assert name == "Review all the open pull requests of…" and len(name) <= 41
    assert clean_name("가" * 60).endswith("…") and len(clean_name("가" * 60)) == 41


def namer(reply="Play Store sign-in screenshot", *, error=None, hang=False, **kw):
    calls, renames = [], []

    async def stream(provider, request, **kwargs):
        calls.append((provider, request))
        if hang:
            await asyncio.sleep(60)
        if error:
            raise error
        yield TextDelta(reply)
        yield Done("stop")
    n = TaskNamer(stream, lambda tid, name: renames.append((tid, name)), lambda: ("p", "m"), **kw)
    return n, calls, renames


async def settle(n):
    while n._running:
        await asyncio.gather(*list(n._running))


async def test_a_long_request_is_named_once_with_the_cleaned_answer():
    n, calls, renames = namer('"Play Store sign-in screenshot."')
    assert n.name_later(7, LONG)
    assert not n.name_later(7, LONG)          # at most one call per task
    await settle(n)
    assert renames == [(7, "Play Store sign-in screenshot")] and len(calls) == 1
    req = calls[0][1]
    assert req.max_tokens == 30 and req.tools == [] and req.model == "m"
    assert req.messages[0]["content"] == NAME_PROMPT.format(request=LONG)


async def test_a_short_reply_is_named_from_the_earlier_request():
    n, calls, renames = namer("쿠팡 리뷰 4개 등록")
    assert n.name_later(3, "너가", "쿠팡에 리뷰 4개 등록해줘")
    await settle(n)
    assert renames == [(3, "쿠팡 리뷰 4개 등록")]
    assert "Earlier request: 쿠팡에 리뷰 4개 등록해줘" in calls[0][1].messages[0]["content"]


async def test_short_requests_hidden_marks_and_other_sources_are_not_sent():
    from assistant.schedules import SCHEDULE_MARK
    from assistant.workers import HANDOFF_MARK
    n, calls, renames = namer()
    assert not n.name_later(1, "로또 번호 확인해줘")                       # short: its own title is fine
    assert not n.name_later(2, HANDOFF_MARK + " " + LONG)
    assert not n.name_later(3, SCHEDULE_MARK + " " + LONG)
    assert not n.name_later(4, LONG, source="schedule:5")
    assert not n.name_later(5, LONG, source="worker")
    assert n.name_later(6, LONG, source="api")
    await settle(n)
    assert [r[0] for r in renames] == [6] and len(calls) == 1


async def test_an_error_or_a_timeout_keeps_the_title_and_raises_nothing(monkeypatch):
    n, _calls, renames = namer(error=RuntimeError("model down"))
    assert n.name_later(1, LONG)
    await settle(n)
    assert renames == []
    monkeypatch.setattr("assistant.task_names.TIMEOUT", 0.05)
    n, _calls, renames = namer(hang=True)
    assert n.name_later(1, LONG)
    await settle(n)
    assert renames == []
    n, _calls, renames = namer("x" * 200)                                  # an answer instead of a name
    assert n.name_later(1, LONG)
    await settle(n)
    assert renames == []


class FakeNamer:
    def __init__(self):
        self.calls = []

    def name_later(self, task_id, request, earlier="", source="chat"):
        self.calls.append((task_id, request, earlier, source))
        return True


async def test_a_chat_turn_asks_the_namer_with_the_request_and_the_earlier_one(tmp_path):
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=answering("알겠어요."))
    fake = app.state.appctx.task_namer = FakeNamer()
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": LONG})).json()["id"])
        await follow(c, (await c.post("/api/turns", json={"text": "너가"})).json()["id"])
    first, second = fake.calls
    assert first[1] == LONG and first[2] == "" and first[3] == "chat"
    assert second[1] == "너가" and second[2] == LONG and second[0] != first[0]


async def test_rename_task_changes_the_title_in_the_tasks_list(tmp_path):
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=answering("알겠어요."))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": LONG})).json()["id"])
        item = (await c.get("/api/tasks")).json()["items"][0]
        assert item["title"] != "Play Store sign-in screenshot"
        app.state.appctx.activity.rename_task(item["id"], "Play Store sign-in screenshot")
        assert (await c.get("/api/tasks")).json()["items"][0]["title"] == "Play Store sign-in screenshot"
