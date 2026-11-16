"""reply_text.py: what of a finished reply's text is stored (lead-ins before tool steps are left out), and the old rule for a reply that
did not end "done"."""

from assistant import tool_labels
from assistant.reply_text import BOOKKEEPING, ReplyText


def run(*events, done=True):
    r = ReplyText()
    for e in events:
        if isinstance(e, str):
            e = {"type": "text", "text": e}
        elif isinstance(e, tuple):
            e = {"type": "tool_started", "name": e[0]}
        r.feed(e)
    if done:
        r.feed({"type": "done"})
    return r.text()


def test_bookkeeping_tools_are_hidden_ones():
    assert "react" in BOOKKEEPING and "quick_replies" in BOOKKEEPING and "find_tools" in BOOKKEEPING and "offer_post" in BOOKKEEPING
    for name in BOOKKEEPING:
        assert tool_labels.hidden(name), name


ANSWER = ("Sapporo, 3 nights from 12 Dec. The best time to leave is the morning flight.\n\n"
          "- Flight: 120,000 KRW round trip\n- Hotel near the station: 90,000 KRW a night\n\nWant me to book it? " + "x" * 200)


def test_the_sapporo_case_keeps_the_opener_and_the_answer():
    events = ["Let me look into Sapporo for you."]
    for line in ("I'll check the official timetable.", "Now the flight prices.", "Looking at hotels near the station.",
                 "Checking the weather for those dates.", "One more look at the train.", "Last, the snow report.\n"):
        events += [("browser_open",), ("browser_read",), line]
    events += [("web_search",), ANSWER]
    assert run(*events) == "Let me look into Sapporo for you.\n\n" + ANSWER


def test_an_answer_written_before_a_final_react_is_kept_whole():
    assert run("Short answer.", ("react",)) == "Short answer."
    assert run("I'll look.", ("web_search",), "Here is the result.", ("react",), ("quick_replies",)) == "I'll look.Here is the result."   # kept as written
    assert run("Pick one:", ("quick_replies",)) == "Pick one:"


def test_long_partial_results_between_tools_are_kept():
    part = "Flights found: " + "a, " * 80
    assert len(part) > 160
    out = run("Checking.", ("web_search",), part, ("browser_open",), "Done.")
    assert part in out and out.startswith("Checking.") and out.endswith("Done.")
    two_paragraphs = "Found two.\n\nThe cheaper is on Monday."
    assert two_paragraphs in run("Hi.", ("web_search",), two_paragraphs, ("web_search",), "Final.")


def test_a_reply_without_tools_is_unchanged_and_nothing_dropped_is_not_rejoined():
    assert run("Hello ", "there.\n\nSecond paragraph.") == "Hello there.\n\nSecond paragraph."
    assert run("Hi.", ("react",), " Bye.") == "Hi. Bye."
    assert run("A", ("web_search",), "B " + "x" * 200) == "A" + "B " + "x" * 200      # first and final kept, as written


def test_message_breaks_belong_to_their_segment():
    out = run("Opening.<message_break/>", ("web_search",), "Checking this.<message_break/>", ("web_search",), "Part one.<message_break/>Part two.")
    assert out == "Opening.<message_break/>Part one.<message_break/>Part two."
    assert "Checking" not in out and out.count("<message_break/>") == 2


def test_a_reply_that_ended_other_than_done_keeps_the_text_after_the_last_tool_step():
    assert run("I'll check.", ("web_search",), "Half an answ", done=False) == "Half an answ"
    assert run("I'll check.", ("web_search",), done=False) == ""
    assert run("Answer.", ("react",), done=False) == ""


def test_no_closing_text_keeps_the_last_thing_written():
    assert run("Let me look.", ("web_search",), "The result is 5.", ("react",)) == "Let me look.The result is 5."
    assert run("Let me look.", ("web_search",), "The result is 5.", ("web_search",)).endswith("The result is 5.")
