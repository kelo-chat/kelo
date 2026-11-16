"""A reply that says the next step is being done now ("바로 등록할게요", "Next I'll post it") but calls no tool ends the run with
nothing done, and the owner waits for work that never starts. Pure: `promises_more(text)` tells such an ending; loop.py then tells
the model once or twice to make the call (or to ask plainly when it needs the owner first).
"""

from __future__ import annotations

import re

SAID_DO_NOTE = ("[Not from the user: your reply says you are doing the next step now (\"{said}\"), but you called no tool, so nothing "
                "happened and the run would end here while the user waits. Make that tool call now and keep going until the work is "
                "done. Only if you truly need the user's answer first, ask them one plain question instead.]")
MAX_SAID_DO = 2   # per run: a model that keeps only talking gets its answer through after this

# The last lines of a reply that announce work starting now (first person, present or near future), per language
_PROMISE = re.compile(
    r"(?:할게요|하겠습니다|드릴게요|드리겠습니다|볼게요|보겠습니다|진행하고\s*있어요|진행\s*중이에요|진행할게요|이어서\s+\S+.*(?:게요|겠습니다))"
    r"|\b(?:I'll|I will|I'm going to|I am going to|let me|next,? I'll|now I'll|I'm (?:now )?(?:doing|posting|filling|submitting|moving on))\b"
    r"|(?:します(?:ね)?[。！!]?$|いたします|進めます)"
    r"|(?:我(?:将|会|这就|马上)|接下来我)", re.I | re.M)
# A reply that hands the turn to the owner: a question, a choice or a wait for them
_ASKS = re.compile(r"[?？]\s*$|할까요|하실래요|알려\s*주세요|말씀해\s*주|기다릴게요|shall I|should I|would you like|do you want|let me know|"
                   r"tell me|ますか|でしょうか|吗|呢", re.I | re.M)


# An offer for later or a promise that needs no step, not work starting now ("필요하면 더 도와드릴게요", "그대로 둘게요", "기억할게요")
_OFFER = re.compile(r"도와\s*드릴|알려\s*드릴|(?:그대로|그냥)\s*둘게요|두겠습니다|기억할게요|기억해\s*둘게요|참고할게요|명심할게요|I\'ll (?:keep|leave|remember) |필요하(?:면|시면)|언제든|원하시면|if you (?:need|want|like)|any ?time|feel free|happy to help", re.I)


def tail(text: str, lines: int = 3) -> str:
    rows = [r.strip() for r in (text or "").strip().splitlines() if r.strip()]
    return "\n".join(rows[-lines:])


def promises_more(text: str) -> str:
    """The promising sentence when the reply ends by announcing a step it did not take; "" otherwise (also when it asks the owner)."""
    end = tail(text)
    if not end or _ASKS.search(end):
        return ""
    for line in reversed(end.splitlines()):
        if _PROMISE.search(line) and not _OFFER.search(line):
            return line[:160]
    return ""


# The owner tells how a kind of work is to be done, in a way that will come back ("내가 했던 거 보고 그런 식으로 해", "from now on
# like this"): once that work went through, the way is worth keeping as a skill, and the owner is asked with propose_skill's card.
SKILL_NOTE = ("[Not from the user: the user told you how this kind of work should be done (\"{said}\") and the work went through. If "
              "that way is worth reusing next time (the steps, the style, what to look at first), call propose_skill now with it in "
              "words (no secrets, no one-off details), so the user can keep it with one tap; if it is not, just answer.]")
MIN_SKILL_STEPS = 5   # tool results in the run before a way counts as worked through
_A_WAY = re.compile(
    r"했던\s*(?:거|것|대로|식|방식)|하던\s*(?:대로|식)|앞으로도|다음에도|다음부터|매번|"
    r"\b(?:like I did|the way I|same way|as I did|from now on|every time|next time)\b|"
    r"いつも(?:の|みたいに)|同じように|以前のように|像我(?:之前|以前)|以后都", re.I)


def asks_for_a_way(text: str) -> str:
    """The owner's words that set a reusable way of working, or ""."""
    m = _A_WAY.search(text or "")
    if not m:
        return ""
    a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
    return " ".join(text[a:b].split())[:120]
