"""Every choice question is one question at a time, with at most three concrete answers, and no catch-all answers (owner 2026-11-20,
D11): the server enforces the counts and the shape of a question about connecting services (connect: two or three service ids, plus a
last answer for not now); the texts ask for it; no word list decides what a catch-all is. Fake models only."""

import re
from pathlib import Path

import pytest

from assistant.goals import GoalBook, GoalError
from assistant.prompt import GOAL_RULES

ROOT = Path(__file__).resolve().parents[2]


def clean(*qs):
    return GoalBook.clean_questions(list(qs))


def test_two_questions_in_one_call_are_refused():
    with pytest.raises(GoalError, match="one question at a time"):
        clean({"question": "Which?", "options": ["a", "b"]}, {"question": "When?", "options": ["x", "y"]})


def test_four_answers_are_refused_and_three_pass():
    with pytest.raises(GoalError, match="at most 3 answers"):
        clean({"question": "Which?", "options": ["a", "b", "c", "d"]})
    got = clean({"question": "Which?", "options": ["a", "b", "c"]})
    assert got[0]["options"] == ["a", "b", "c"]


def test_a_connect_question_takes_two_or_three_services_and_a_last_not_now():
    ok = clean({"question": "Which mail?", "options": ["Gmail", "Dropbox", "Not now"], "connect": ["gmail", "dropbox"]})
    assert ok[0]["connect"] == ["gmail", "dropbox"] and ok[0]["options"][-1] == "Not now"
    with pytest.raises(GoalError, match="two or three services"):
        clean({"question": "Which?", "options": ["a", "b", "c", "d", "Not now"], "connect": ["gmail", "outlook", "naver", "daum"]})
    with pytest.raises(GoalError, match="connect lists the ids"):
        clean({"question": "Which?", "options": ["Nope", "Gmail", "Not now"], "connect": ["no-such-service", "gmail"]})
    with pytest.raises(GoalError, match="two or three services"):
        clean({"question": "Which?", "options": ["Gmail", "Dropbox", "Not now"], "connect": ["gmail"]})


def test_typed_answers_are_no_longer_marked():
    got = clean({"question": "Which?", "options": ["Plan A", "직접 입력"], "typed": ["직접 입력"]})
    assert "typed" not in got[0] and got[0]["options"] == ["Plan A", "직접 입력"]


def test_product_cards_take_at_most_three():
    with pytest.raises(GoalError, match="at most 3 answers"):
        clean({"question": "Which?", "options": ["a", "b", "c", "d"], "cards": [{}, {}, {}, {}]})


def test_the_texts_ask_one_question_and_have_no_rather_not_say_rule():
    src = (ROOT / "server" / "src" / "assistant" / "goals.py").read_text(encoding="utf-8")
    qr = src[src.index('Tool("quick_replies"'):src.index('wrap(quick_replies), closes_reply=True')]
    assert "one question at a time" in qr and "two or three" in qr
    assert "For anything personal include" not in qr and "rather not say" not in qr.lower().replace("(other, not sure, rather not say)", "")
    system = (ROOT / "server" / "content" / "prompts" / "system.md").read_text(encoding="utf-8")
    assert "one question, at most three" in system


def test_no_word_list_decides_a_catch_all():
    got = clean({"question": "Which?", "options": ["Other", "A"]})
    assert got[0]["options"] == ["Other", "A"]
