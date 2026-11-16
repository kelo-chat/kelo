import pytest

from assistant.agent.said_do import promises_more


@pytest.mark.parametrize("text", [
    "이어서 세 번째 상품 리뷰를 진행하고 있어요.\n준비해 둔 내용으로 본문을 채워 바로 등록할게요.",
    "명이나물 등록을 마치는 대로 참깨라면까지 순서대로 이어서 등록해 드릴게요!",
    "Next, I'll post the second review.",
    "次のレビューを投稿します。",
])
def test_a_reply_that_announces_the_next_step_without_doing_it(text):
    assert promises_more(text)


@pytest.mark.parametrize("text", [
    "햇반 리뷰는 등록이 완료되었어요.",
    "이어서 바로 등록할까요? 말씀해 주시면 계속 올려드릴게요.",
    "Done! All four reviews are posted.",
    "리뷰 4개를 모두 등록했어요. 필요하면 더 도와드릴게요.",
    "Shall I post the next one?",
    "그대로 둘게요.",
    "알겠어요, 기억할게요.",
    "",
])
def test_a_finished_answer_a_question_or_an_offer_is_not_one(text):
    assert promises_more(text) == ""


from assistant.agent.said_do import asks_for_a_way


@pytest.mark.parametrize("text", ["내가 이때까지 했던 거 보고 그런식으로 해", "앞으로도 이렇게 써 줘", "Write it the way I did last time",
                                  "from now on use this order"])
def test_the_owner_setting_a_reusable_way_is_found(text):
    assert asks_for_a_way(text)


@pytest.mark.parametrize("text", ["쿠팡 리뷰 써줘", "계속", "Post the review", "", "이렇게 해", "이런 식으로 고쳐 줘", "저렇게 바꿔"])
def test_a_plain_request_sets_no_way(text):
    assert asks_for_a_way(text) == ""
