"""Click judgement without word lists (docs/click-judge.md): page structure first, the step judge (a fake here) for a step that changes
something or cannot be read, a task's grant before any card. The same fake verdicts give the same result on Korean, Japanese and
English pages: the words of the page decide nothing. Real Chromium on local pages (skipped without it)."""

from __future__ import annotations

import pytest

from assistant.step_judge import FLAGS, StepJudge, parse
from assistant.tools.task_permission import TaskGrants

from fake_judge import FakeJudge
from test_grant_only_commits import PAGES, TITLES, asks, ctx, setup, turn  # noqa: F401  (ctx, turn: fixtures)

POST = "onclick=\"fetch('/x',{method:'POST'})\""
SITE = "https://shop.example.com"
LOTTO = "https://lotto.example.com/game"
PAGES.update({
    f"{SITE}/links": "<a id=buylink href='/product/1'>Buy now</a><a id=gone href='/account/leave'>회원 탈퇴</a>",
    f"{SITE}/ko": f"<form action=/x method=post><button id=go type=submit>신청하기</button></form><button id=del {POST}>삭제하기</button>",
    f"{SITE}/ja": f"<form action=/x method=post><button id=go type=submit>応募する</button></form><button id=del {POST}>削除する</button>",
    f"{SITE}/en": f"<form action=/x method=post><button id=go type=submit>Apply now</button></form><button id=del {POST}>Delete it</button>",
    f"{SITE}/search": "<form role=search action=/s><input id=q name=q><button id=find type=submit>Go</button></form>",
    f"{SITE}/contact": "<form action=/x method=post><input id=phone autocomplete=tel value='010-1111-2222'><button id=send type=submit>Next</button></form>",
    f"{SITE}/plain": "<form action=/x method=post><input id=note name=note value='hi'><button id=send type=submit>Next</button></form>",
    f"{SITE}/page": "<p id=text>Buy now</p><input id=tick type=checkbox><select id=pick><option>a</option></select>",
    LOTTO: f"<h1>Weekly 6/45</h1><button id=auto {POST}>자동번호발급 - issue numbers automatically</button>",
})
TITLES.setdefault(LOTTO, "Lotto")


def use(s, says):
    s.step_judge = FakeJudge(says)
    return s.step_judge


async def test_a_real_link_with_a_buy_label_asks_nothing_and_calls_no_judge(ctx, turn):
    s, _p, click = await setup(ctx)
    judge = use(s, ("spends_money",))
    await s.open(f"{SITE}/links")
    assert not await asks(click, "#buylink") and not await asks(click, "#gone")   # a link only goes to another page
    assert judge.calls == []


async def test_plain_text_and_page_state_controls_call_no_judge(ctx, turn):
    s, _p, click = await setup(ctx)
    judge = use(s, ("deletes",))
    await s.open(f"{SITE}/page")
    for sel in ("#text", "#tick", "#pick"):
        assert not await asks(click, sel), sel
    assert judge.calls == []


async def test_a_submit_judged_applies_asks_without_a_grant_and_goes_with_the_sites_apply_grant(ctx, turn):
    grants = TaskGrants(alive=lambda s: s in {"turn:t1"})
    s, _p, click = await setup(ctx, grants)
    use(s, ("applies",))
    await s.open(f"{SITE}/en")
    assert await asks(click, "#go")                                         # no grant: the card
    grants.add(site="shop.example.com", kind="apply", action="apply", max_count=3, task_id=7)
    assert not await asks(click, "#go")                                     # inside what the owner allowed
    assert grants.active()[0]["used"] == 1


async def test_the_weekly_lotto_button_that_issues_numbers_asks_nothing(ctx, turn):
    """The weekly purchase's grant covers buying on the site; an "issue numbers automatically" button the judge says neither
    spends money nor applies is only a step of it: no card (it asked every time before, by the word for issuing)."""
    s, _p, click = await setup(ctx)
    judge = use(s, ())
    await s.open(LOTTO)
    assert not await asks(click, "#auto")
    assert len(judge.calls) == 1


@pytest.mark.parametrize("path", ["ko", "ja", "en"])
async def test_a_submit_judged_deleting_asks_in_every_language(ctx, turn, path):
    s, _p, click = await setup(ctx)
    use(s, lambda info: ("deletes",) if info.get("tag") == "button" and info.get("type") != "submit" else ())
    await s.open(f"{SITE}/{path}")
    assert await asks(click, "#del") and s.judged("#del") == "delete"
    assert not await asks(click, "#go")


@pytest.mark.parametrize("says,kind", [(("applies",), "apply"), (("sends",), "send"), ((), ""), (("opts_out",), "optout"),
                                       (("account_security",), "security"), (("signs_up",), "account"), (("deletes",), "delete")])
@pytest.mark.parametrize("path", ["ko", "ja", "en"])
async def test_the_same_verdict_gives_the_same_kind_on_korean_japanese_and_english_pages(ctx, turn, path, says, kind):
    s, _p, _click = await setup(ctx)
    use(s, says)
    await s.open(f"{SITE}/{path}")
    assert await s.judge("browser_click", {"selector": "#go"}) == kind


async def test_the_judge_being_down_asks_and_so_does_having_none(ctx, turn):
    s, _p, click = await setup(ctx)
    use(s, None)
    await s.open(f"{SITE}/en")
    assert await asks(click, "#go") and s.judged("#go") == "unknown"
    s.step_judge = None
    assert await asks(click, "#go")


async def test_typing_enter_in_a_search_form_asks_nothing_and_calls_no_judge(ctx, turn):
    s, _p, _click = await setup(ctx)
    judge = use(s, ("sends",))
    await s.open(f"{SITE}/search")
    assert await s.judge("browser_type", {"selector": "#q", "text": "shoes", "submit": True}) == ""
    assert judge.calls == []


async def test_a_form_carrying_the_owners_phone_by_autocomplete_asks_only_when_the_judge_flags_it(ctx, turn):
    s, _p, _click = await setup(ctx)
    use(s, ())
    await s.open(f"{SITE}/contact")
    assert await s.judge("browser_click", {"selector": "#send"}) == ""          # details in the form ask nothing by themselves
    use(s, ("sends",))
    s._kinds_seen.clear()
    assert await s.judge("browser_click", {"selector": "#send"}) == "send"      # the judge's stronger kind wins


async def test_a_form_without_personal_values_is_not_a_form_step(ctx, turn):
    s, _p, _click = await setup(ctx)
    use(s, ())
    await s.open(f"{SITE}/plain")
    assert await s.judge("browser_click", {"selector": "#send"}) == ""


# ---- the step judge itself: seven flags, strict, cached, fail closed
def test_the_step_judge_has_seven_flags_and_all_are_required():
    assert len(FLAGS) == 7 and {"applies", "opts_out"} <= set(FLAGS)
    full = {f: False for f in FLAGS}
    assert parse(full) is not None and parse({**full, "applies": True}).kind == "apply" and parse({**full, "opts_out": True}).kind == "optout"
    assert parse({k: v for k, v in full.items() if k != "opts_out"}) is None
    assert parse({**full, "sends": "yes"}) is None


async def test_the_step_judge_asks_once_per_control_and_fails_closed():
    asked = []

    async def ask(system, user):
        asked.append(user)
        return {f: False for f in FLAGS}
    judge = StepJudge(ask)
    info = {"url": "https://a.example/x/1?q=1", "tag": "button", "text": "Next"}
    assert (await judge.web(info)).kind == "" and (await judge.web({**info, "url": "https://a.example/x/1?q=2"})).kind == ""
    assert len(asked) == 1

    async def down(system, user):
        raise RuntimeError("no model")
    assert await StepJudge(down).web(info) is None
    async def bad(system, user):
        return {"spends_money": True}
    assert await StepJudge(bad).web(info) is None

