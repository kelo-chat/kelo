"""A model that is only busy is not "used up" (busy-429, 2026-11-17). A rate limit (HTTP 429 or its code) with no reset time, or one that
resets within BUSY_MAX_WAIT, is busy: a call that gave nothing yet is tried again (the provider's Retry-After, else 2 s, 5 s, 15 s; at
most BUSY_RETRIES times, for chat too); still busy, background calls wait BUSY_HOLD (120 s) and the reply says "busy", not "used up";
the 4th busy hold in a row takes the used-up ladder. Balances, far resets (a Retry-After of hours), and partial answers keep the used-up
behaviour. Fake models only; the pauses are recorded instead of waited for."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from assistant import i18n, model_limits as ml
from assistant.providers import ChatRequest, Done, TextDelta
from assistant.providers.types import ProviderError, QuotaExhausted
from assistant.store import Store
from assistant.usage import metered

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=SEOUL).timestamp()   # a Monday, 10:00 in Seoul


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def pauses(monkeypatch):
    """Records every busy wait instead of sleeping."""
    waited = []

    async def record(self, seconds):
        waited.append(seconds)
    monkeypatch.setattr(ml.QuotaGuard, "pause", record, raising=False)
    return waited


def request():
    return ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}], tools=[], extra={})


def busy_429(**kw):
    return ProviderError("HTTP 429 from provider: upstream is crowded", status=429, **kw)


async def run(counted, kind="chat"):
    return [ev async for ev in counted(kind)("p", request())]


# ------------------------------------------------------------------------------------------------ retries before any answer
async def test_a_busy_429_with_no_reset_then_an_answer_is_one_answer_with_no_hold_and_no_error(pauses):
    """Byo's case (TokenRouter's crowded 429): the second try answers; the user sees the answer, nothing is held, no error line."""
    calls = []

    async def model(provider, req, **kw):
        calls.append(provider)
        if len(calls) == 1:
            raise busy_429()
        yield TextDelta("ok")
        yield Done("stop")
    g = ml.QuotaGuard(Store(), clock=Clock(NOW))
    got = await run(metered(model, lambda r: None, lambda: None, guard=g))
    assert any(isinstance(ev, TextDelta) for ev in got) and len(calls) == 2
    assert g.until("p") is None and g.state("p") is None
    assert pauses == [ml.BUSY_WAITS[0]]


async def test_a_429_with_a_retry_after_of_3_seconds_waits_3_seconds_then_answers(pauses):
    calls = []

    async def model(provider, req, **kw):
        calls.append(provider)
        if len(calls) == 1:
            raise busy_429(retry_after=3.0)
        yield TextDelta("ok")
        yield Done("stop")
    g = ml.QuotaGuard(Store(), clock=Clock(NOW))
    got = await run(metered(model, lambda r: None, lambda: None, guard=g))
    assert any(isinstance(ev, TextDelta) for ev in got) and len(calls) == 2
    assert pauses == [3.0]


async def test_busy_four_times_is_busy_not_used_up_holds_background_for_120_seconds_and_says_busy(pauses):
    calls = []

    async def model(provider, req, **kw):
        calls.append(provider)
        raise busy_429()
        yield   # pragma: no cover - makes this a generator
    clock = Clock(NOW)
    g = ml.QuotaGuard(Store(), clock=clock)
    with pytest.raises(QuotaExhausted) as ei:
        await run(metered(model, lambda r: None, lambda: None, guard=g))
    assert len(calls) == 1 + ml.BUSY_RETRIES and pauses == list(ml.BUSY_WAITS)
    assert getattr(ei.value, "busy", False) and ei.value.reset_at is None
    assert g.until("p") == NOW + ml.BUSY_HOLD and g.busy_now("p")
    with pytest.raises(QuotaExhausted):                                    # a background call is held
        await run(metered(model, lambda r: None, lambda: None, guard=g), kind="journal")
    assert len(calls) == 1 + ml.BUSY_RETRIES                              # held: the provider is not called for it
    i18n_tok = i18n.use("ko")
    try:
        assert ml.chat_line(ei.value, "Asia/Seoul", NOW) == "AI 모델이 지금 바빠서 답하지 못했어요. 1분 뒤에 다시 보내 주세요."
    finally:
        i18n.reset(i18n_tok)
    assert ml.chat_line(ei.value, "Asia/Seoul", NOW) == ("The AI model is busy right now, so it could not answer. Send it again in a "
                                                          "minute.")
    clock.t = NOW + ml.BUSY_HOLD + 1
    assert g.until("p") is None and not g.busy_now("p")                    # the hold is over: background calls go out again


async def test_a_429_that_resets_in_2_hours_is_used_up_as_before_no_retries(pauses):
    calls = []

    async def model(provider, req, **kw):
        calls.append(provider)
        raise ProviderError("rate limited", status=429, retry_after=7200.0)
        yield   # pragma: no cover
    g = ml.QuotaGuard(Store(), clock=Clock(NOW))
    with pytest.raises(QuotaExhausted) as ei:
        await run(metered(model, lambda r: None, lambda: None, guard=g))
    assert len(calls) == 1 and pauses == []
    assert not getattr(ei.value, "busy", False)
    assert ei.value.reset_at == NOW + 7200 + ml.RESET_MARGIN
    assert ml.chat_line(ei.value, "Asia/Seoul", NOW).startswith("The AI model's usage limit is used up. You can use it again")


async def test_a_balance_that_ran_out_is_not_busy_and_is_not_retried(pauses):
    calls = []

    async def model(provider, req, **kw):
        calls.append(provider)
        raise ProviderError("insufficient_quota", status=402)
        yield   # pragma: no cover
    g = ml.QuotaGuard(Store(), clock=Clock(NOW))
    with pytest.raises(QuotaExhausted) as ei:
        await run(metered(model, lambda r: None, lambda: None, guard=g))
    assert len(calls) == 1 and pauses == []
    assert ei.value.balance and not getattr(ei.value, "busy", False)


async def test_an_error_after_the_stream_gave_text_is_never_retried(pauses):
    calls = []

    async def model(provider, req, **kw):
        calls.append(provider)
        yield TextDelta("partial answer")
        raise busy_429()
    g = ml.QuotaGuard(Store(), clock=Clock(NOW))
    got = []
    with pytest.raises(QuotaExhausted):
        async for ev in metered(model, lambda r: None, lambda: None, guard=g)("chat")("p", request()):
            got.append(ev)
    assert len(calls) == 1 and pauses == []                                # nothing is retried: the partial output stays as it is
    assert any(isinstance(ev, TextDelta) and ev.text == "partial answer" for ev in got)


async def test_the_fourth_busy_hold_in_a_row_takes_the_used_up_ladder():
    clock = Clock(NOW)
    g = ml.QuotaGuard(Store(), clock=clock)
    for _ in range(ml.BUSY_LADDER_AFTER):
        hit = await g.classify("p", "m", busy_429())
        assert getattr(hit, "busy", False) and hit.until - clock.t == ml.BUSY_HOLD
        clock.t = hit.until + 1                                             # the hold passes; the next 429 comes again
    hit = await g.classify("p", "m", busy_429())
    assert not getattr(hit, "busy", False)
    assert hit.until - clock.t == ml.BACKOFF[0]                             # 600 s: the used-up ladder starts again
    assert g.state("p")["busy_run"] == ml.BUSY_LADDER_AFTER + 1


async def test_a_busy_wait_is_only_offered_while_retries_are_left(pauses):
    g = ml.QuotaGuard(Store(), clock=Clock(NOW))
    assert await g.busy_wait("p", "m", busy_429(), 0) == ml.BUSY_WAITS[0]
    assert await g.busy_wait("p", "m", busy_429(), 2) == ml.BUSY_WAITS[2]
    assert await g.busy_wait("p", "m", busy_429(), ml.BUSY_RETRIES) is None
    assert await g.busy_wait("p", "m", busy_429(retry_after=7200.0), 0) is None   # used up: no wait
    assert await g.busy_wait("p", "m", ProviderError("bad request", status=400), 0) is None
