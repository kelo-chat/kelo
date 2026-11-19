"""One search rule for every calendar read tool: a list, a JSON-array string or a plain string of words; an event matching ANY word."""

from assistant.event_search import matches, no_match_line, query_terms


def test_a_list_keeps_a_phrase_whole_and_drops_duplicates_case_and_dicts():
    assert query_terms(["Testville Flight", "TESTVILLE", "flight", {"x": 1}, ""]) == ["testville flight", "testville", "flight"]


def test_a_plain_string_splits_at_spaces_commas_and_semicolons():
    assert query_terms("nowhere, xx; zilch  yy") == ["nowhere", "xx", "zilch", "yy"]


def test_a_json_array_string_is_read_as_the_list():
    assert query_terms('["nowhere","TESTVILLE"]') == ["nowhere", "testville"]
    assert query_terms('[not json') == ["[not", "json"]


def test_full_width_letters_equal_their_ascii_form():
    assert query_terms(["ＡＢＣ"]) == ["abc"]


def test_at_most_twenty_terms_are_kept():
    assert len(query_terms([f"w{i}" for i in range(40)])) == 20


def test_no_terms_match_everything_and_any_one_term_is_enough():
    assert matches([], "anything") is True
    assert matches(["zilch", "testville"], "Flight to Testville", "", "") is True
    assert matches(["zilch"], "Flight to Testville", "", "") is False


def test_an_empty_search_says_it_was_filtered_and_how_big_the_range_is():
    assert "holds 3 events" in no_match_line(["a"], "between x and y", 3)
    assert no_match_line(["a"], "between x and y", 0) == "No events between x and y, with or without the search words."
