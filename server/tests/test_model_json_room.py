"""2026-11-17: every forget_plan failed ("the AI model could not judge the matches") because ModelJson capped answers at 500 tokens and
the judge's JSON was cut off. Default room is now 1500; the forget judge asks for 2000."""

import asyncio

from assistant import forget as forget_mod
from assistant.model_json import MAX_TOKENS, ModelJson
from assistant.providers import TextDelta


def test_the_default_room_fits_a_long_answer_and_a_caller_can_ask_for_more():
    seen = []

    async def stream(provider, req, **_k):
        seen.append(req.max_tokens)
        yield TextDelta('{"about": [1, 2]}')

    mj = ModelJson(stream, lambda: ("p", "m"))
    assert asyncio.run(mj.ask("s", "u")) == {"about": [1, 2]}
    assert asyncio.run(mj.ask("s", "u", max_tokens=2000)) == {"about": [1, 2]}
    assert MAX_TOKENS >= 1500 and seen == [MAX_TOKENS, 2000]


def test_the_forget_judge_asks_for_room_for_a_whole_batch():
    calls = []

    async def ask(system, user, *, max_tokens=None):
        calls.append(max_tokens)
        return {"about": []}

    f = forget_mod.Forget.__new__(forget_mod.Forget)
    f.ask = ask
    assert asyncio.run(f._ask_long("s", "u")) == {"about": []}
    assert calls == [forget_mod.JUDGE_TOKENS] and forget_mod.JUDGE_TOKENS >= 2000
