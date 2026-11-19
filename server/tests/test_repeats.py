"""repeats.collapse_repeat on a stored reply: a reply whose paragraphs are written again after a tool keeps one copy (paragraph by paragraph,
a restated paragraph may be reworded or have a sentence added); a reply with a new second part is kept whole. Synthetic sentences only."""

from assistant.repeats import collapse_repeat

P1 = "받은편지함의 안 읽은 메일 12통을 모두 읽음으로 바꿨어요. 이제 받은편지함에는 새 메일만 남아 있어요."
P2 = "다시 찾고 싶은 메일이 있으면 보내는 사람이나 제목을 말씀해 주세요. 바로 찾아 드릴게요."
P1_AGAIN = "받은편지함에 있던 안 읽은 메일 12통을 전부 읽음 표시로 바꿨어요. 지금은 새 메일만 남아 있어요."
P2_AGAIN = ("다시 찾고 싶은 메일이 있으면 보내는 사람이나 제목을 말씀해 주세요. 바로 찾아 드릴게요. "
            "보관한 메일도 같이 찾아볼 수 있고, 날짜 범위를 정해 주시면 더 정확하게 골라 드릴 수 있어요.")


def test_a_reply_whose_paragraphs_are_written_again_after_a_tool_keeps_one_copy():
    said = P1 + "\n\n" + P2 + "\n\n" + P1_AGAIN + "\n\n" + P2_AGAIN
    assert collapse_repeat(said) == P1 + "\n\n" + P2
    said_with_breaks = P1 + "<message_break/>" + P2 + "<message_break/>" + P1_AGAIN + "<message_break/>" + P2_AGAIN
    assert collapse_repeat(said_with_breaks) == P1 + "<message_break/>" + P2


def test_a_reply_with_a_new_second_part_is_kept_whole():
    new_part = "다시 찾고 싶은 메일이 있으면 말씀해 주세요. 지금 바로 찾아볼게요."
    said = P1 + "\n\n" + new_part
    assert collapse_repeat(said) == said
    assert collapse_repeat(P1 + "\n\n" + P2) == P1 + "\n\n" + P2
