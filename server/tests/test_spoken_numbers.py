"""The safety net for digits in Kelo's spoken replies (spoken_numbers.py): the shapes a speech model misreads, nothing else."""

import pytest

from assistant import spoken_numbers as sn


@pytest.fixture
def numbers():
    return sn.SpokenNumbers()


def test_decimal_point_is_read_as_point(numbers):
    assert numbers.apply("It takes 2.5 hours", "en") == "It takes two point five hours"


def test_clock_time_is_not_one_number(numbers):
    # owner report (2026-11-19): "14:55" was heard as "1455"; the clock is read as hour and minutes
    assert numbers.apply("at 14:55", "en") == "at fourteen fifty-five"
    assert numbers.apply("14:55에 출발해요", "ko") == "십사 오십오에 출발해요"


def test_a_clock_time_on_the_hour_is_just_the_hour(numbers):
    assert numbers.apply("at 9:00", "en") == "at nine"


def test_german_decimal_comma(numbers):
    assert numbers.apply("2,5 Stunden", "de") == "zwei Komma fünf Stunden"


def test_korean_grouped_amount_keeps_the_particle(numbers):
    assert numbers.apply("15,000원", "ko") == "만 오천원"
    assert numbers.apply("사과 3개에 15,000원이에요.", "ko") == "사과 3개에 만 오천원이에요."


def test_grouped_amount_in_english(numbers):
    assert numbers.apply("pay 1,000,000 won", "en") == "pay one million won"


@pytest.mark.parametrize("text, lang", [
    ("3시", "ko"), ("2026", "en"), ("010-1234-5678", "ko"), ("29", "en"), ("Dates 29, 2026 ok", "en"),
    ("1.2.3 and v2", "en"),
])
def test_left_as_they_are(numbers, text, lang):
    assert numbers.apply(text, lang) == text


def test_a_language_without_a_converter_is_unchanged(numbers):
    assert numbers.apply("नमस्ते 3", "hi") == "नमस्ते 3"


def test_a_text_with_no_digits_is_identical(numbers):
    assert numbers.apply("no numbers here.", "en") == "no numbers here."


def test_a_library_error_leaves_the_text_unchanged(numbers, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("library broke")

    monkeypatch.setattr(sn, "num2words", boom)
    with caplog.at_level("DEBUG"):
        assert numbers.apply("It takes 2.5 hours", "en") == "It takes 2.5 hours"
    assert "2.5" not in caplog.text


def test_a_language_list_limits_the_rewrite():
    only_ko = sn.SpokenNumbers(frozenset({"ko"}))
    assert only_ko.apply("It takes 2.5 hours", "en") == "It takes 2.5 hours"
    assert only_ko.apply("15,000원", "ko") == "만 오천원"


def test_regional_tags_use_their_own_converter(numbers):
    assert numbers.apply("2,5 horas", "pt-BR") == "dois vírgula cinco horas"
