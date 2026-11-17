"""A step judge for tests: the verdict comes from a function of the control (never from words in the code under test)."""

from __future__ import annotations

from typing import Any, Callable

from assistant.step_judge import FLAGS, StepVerdict


def verdict(*flags: str) -> StepVerdict:
    return StepVerdict(**{f: f in flags for f in FLAGS})


class FakeJudge:
    """`says(info)` -> flag names (tuple), or None for "the judge is down". Counts its calls."""

    def __init__(self, says: Callable[[dict[str, Any]], Any] | tuple[str, ...] | None = ()) -> None:
        self.says = says if callable(says) else (lambda _info, v=says: v)
        self.calls: list[dict[str, Any]] = []

    async def web(self, info: dict[str, Any]) -> StepVerdict | None:
        self.calls.append(info)
        got = self.says(info)
        return None if got is None else verdict(*got)

    async def phone(self, package: str, activity: str, item: dict[str, Any], screen: list[dict[str, Any]]) -> StepVerdict | None:
        self.calls.append(item)
        got = self.says(item)
        return None if got is None else verdict(*got)


# words of the test pages (Korean, English, Japanese) -> flags: a stand-in for the model, living in tests only
_TABLE = (("deletes", ("삭제", "탈퇴", "Delete", "削除", "Cancel", "취소")),
          ("opts_out", ("수신 거부", "Unsubscribe", "配信停止", "탈퇴", "Delete account")), ("applies", ("신청", "Apply now", "応募")),
          ("account_security", ("동의", "정보 수정", "비밀번호 변경", "앱 비밀번호", "2단계", "App password")), ("sends", ("등록하기", "리뷰 등록", "보내기", "Send", "送信")),
          ("signs_up", ("회원가입", "가입하기", "Sign up", "会員登録")))


def by_page_words(info: dict[str, Any]) -> tuple[str, ...]:
    text = f"{info.get('text') or ''} {info.get('words') or ''} {info.get('name') or ''} {info.get('desc') or ''}"
    return tuple(f for f, ws in _TABLE if any(w in text for w in ws))
