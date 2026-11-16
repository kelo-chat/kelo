

def test_a_short_answer_to_the_assistant_takes_its_title_from_the_request_before_it():
    """"제가 할까요?" → "너가": the task is named after what the owner asked before, not "너가" (owner 2026-11-14)."""
    from assistant.activity import is_short_reply, task_title
    assert is_short_reply("너가") and is_short_reply("응!") and is_short_reply("yes") and not is_short_reply("로또 당첨 번호 확인해줘")
    assert task_title("너가", ["로또 당첨 번호 확인해줘", "안녕"]) == "로또 당첨 번호 확인해줘"
    assert task_title("너가", ["응", "ㅇㅇ"]) == "너가"            # nothing better before it
    assert task_title("홍보글 써줘", ["로또"]) == "홍보글 써줘"     # a real request keeps its own words
