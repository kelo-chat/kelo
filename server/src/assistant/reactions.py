"""Reactions on the owner's chat messages, as messaging apps show them.

When the model starts answering a message (the reply's first thinking, text or tool event, not while it still waits for the
model) the message gets READ (👀). When the work for it is over, 👀 gives way to one emoji:
the one the model chose with `react` (any single emoji: it fits the topic or the outcome), else DONE (✅). A turn that failed or was
stopped ends on FAILED (⚠️), whatever was chosen. Work that is not over when the turn ends keeps 👀 (`hold`):

- the turn handed work to background jobs (`delegate`): 👀 until the last of them ends; then the emoji the job's report turn
  chose, else the one the first turn chose, else ✅ (⚠️ when a job failed or was stopped). A job that waits for the user
  (needs_input, blocked) has not ended.
- the turn ended with a question to the user (quick_replies, await_reply): 👀 until a later turn on the answer finishes, which then
  sets the same emoji on both (or hands both on to the jobs it started).

Stored in `messages.reaction` (with `reaction_hold` and `reaction_next`), so it survives a restart and goes with the history when
the chat is cleared. GET /api/messages carries `reaction` on user messages; a chat turn emits `{"type": "reaction", "message_id",
"emoji"}` when it starts and when it ends.
"""

from __future__ import annotations

from typing import Any

READ = "\U0001F440"        # 👀
DONE = "✅"            # ✅
FAILED = "⚠️"    # ⚠️
MAX_LEN = 40               # code points of the longest sequence we accept (family and flag-tag sequences fit easily)

ZWJ, VS16, KEYCAP, CANCEL_TAG = 0x200D, 0xFE0F, 0x20E3, 0xE007F

# Code points that can be an emoji on their own (Extended_Pictographic, slightly broadened to whole blocks): a valid reaction is built
# from these, joined by ZWJ, each with an optional VS16, skin tone or tag sequence.
_PICTO = (
    (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122), (0x2139, 0x2139),
    (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B), (0x2328, 0x2328), (0x23CF, 0x23CF), (0x23E9, 0x23F3),
    (0x23F8, 0x23FA), (0x24C2, 0x24C2), (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE),
    (0x2600, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55),
    (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297), (0x3299, 0x3299),
    (0x1F000, 0x1F0FF), (0x1F10D, 0x1F10F), (0x1F12F, 0x1F12F), (0x1F16C, 0x1F171), (0x1F17E, 0x1F17F), (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A), (0x1F1AD, 0x1F1E5), (0x1F201, 0x1F20F), (0x1F21A, 0x1F21A), (0x1F22F, 0x1F22F), (0x1F232, 0x1F23A),
    (0x1F23C, 0x1F23F), (0x1F249, 0x1F3FA), (0x1F400, 0x1F53D), (0x1F546, 0x1F64F), (0x1F680, 0x1F6FF), (0x1F774, 0x1F77F),
    (0x1F7D5, 0x1F7FF), (0x1F80C, 0x1F80F), (0x1F848, 0x1F84F), (0x1F85A, 0x1F85F), (0x1F888, 0x1F88F), (0x1F8AE, 0x1F8FF),
    (0x1F90C, 0x1F93A), (0x1F93C, 0x1F945), (0x1F947, 0x1FAFF), (0x1FC00, 0x1FFFD),
)
_SKIN = range(0x1F3FB, 0x1F400)
_REGIONAL = range(0x1F1E6, 0x1F200)
_TAGS = range(0xE0020, 0xE007F)
_KEYCAP_BASES = set(map(ord, "0123456789#*"))


def _picto(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _PICTO) and cp not in _SKIN


def _element(cps: list[int], i: int) -> int:
    """The end of one emoji element starting at `i` (a pictograph with its optional VS16, skin tone or tag sequence), or -1."""
    if i >= len(cps) or not _picto(cps[i]):
        return -1
    i += 1
    if i < len(cps) and cps[i] == VS16:
        i += 1
    if i < len(cps) and cps[i] in _SKIN:
        i += 1
    if i < len(cps) and cps[i] in _TAGS:   # a subdivision flag: black flag + tag letters + cancel tag
        while i < len(cps) and cps[i] in _TAGS:
            i += 1
        if i >= len(cps) or cps[i] != CANCEL_TAG:
            return -1
        i += 1
    return i


def emoji(text: Any) -> str:
    """`text` as one emoji (a single grapheme: ZWJ sequences, skin tones, flags and keycaps allowed; surrounding blanks dropped).
    ValueError for anything else: words, two emoji, a lone skin tone or regional letter, punctuation."""
    s = str(text or "").strip()
    cps = [ord(c) for c in s]
    if not cps:
        raise ValueError("give one emoji")
    if len(cps) > MAX_LEN:
        raise ValueError("one emoji only")
    if len(cps) == 2 and all(c in _REGIONAL for c in cps):   # a flag
        return s
    if cps[0] in _KEYCAP_BASES:   # 1️⃣ #️⃣
        if cps[1:] in ([VS16, KEYCAP], [KEYCAP]):
            return s
        raise ValueError("one emoji only, no words or numbers")
    i = _element(cps, 0)
    while 0 < i < len(cps) and cps[i] == ZWJ:
        i = _element(cps, i + 1)
    if i != len(cps):
        raise ValueError("one emoji only, no words or other marks")
    return s


class Reactions:
    """The reactions in the store's `messages` table (the columns are added by Store at start-up)."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def _set(self, mid: int, emoji_: str, hold: str = "", nxt: str = "") -> dict[str, Any]:
        self.store._run("UPDATE messages SET reaction = ?, reaction_hold = ?, reaction_next = ? WHERE id = ? AND role = 'user'",
                        (emoji_, hold, nxt, mid))
        return {"type": "reaction", "message_id": mid, "emoji": emoji_}

    def get(self, mid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT reaction, reaction_hold, reaction_next FROM messages WHERE id = ?", (mid,))
        return rows[0] if rows else None

    def read(self, mid: int) -> dict[str, Any]:
        """Kelo started on message `mid`."""
        return self._set(mid, READ)

    def _held(self, kind: str) -> list[dict[str, Any]]:
        return self.store._all("SELECT id, reaction_hold, reaction_next FROM messages WHERE role = 'user' AND reaction_hold LIKE ?",
                               (kind + "%",))

    def finish(self, mid: int, status: str, chosen: str = "", jobs: list[str] | tuple[str, ...] = (), asked: bool = False) -> list[dict[str, Any]]:
        """The turn on message `mid` is over (`status` done | failed | stopped). Returns the reaction events to send."""
        if self.get(mid) is None:
            return []
        if status != "done":
            return [self._set(mid, FAILED)]
        waiting = [r["id"] for r in self._held("reply") if r["id"] != mid]   # earlier messages whose question this turn answered
        if jobs:   # handed on: 👀 until the jobs end (the earlier questions' messages wait for them too)
            hold = "job:" + ",".join(dict.fromkeys(jobs))
            for other in waiting:
                self.store._run("UPDATE messages SET reaction_hold = ? WHERE id = ?", (hold, other))
            self._set(mid, READ, hold, chosen)
            return []
        if asked:   # a question back to the user: 👀 until a turn on the answer finishes
            self._set(mid, READ, "reply", chosen)
            return []
        final = chosen or DONE
        return [self._set(other, final) for other in waiting] + [self._set(mid, final)]

    def job_ended(self, wid: str, status: str, chosen: str = "") -> list[dict[str, Any]]:
        """Background job `wid` ended (done | failed | stopped); a job waiting for the user (needs_input, blocked) has not."""
        if status not in ("done", "failed", "stopped"):
            return []
        out = []
        for r in self._held("job:"):
            ids = [w for w in r["reaction_hold"][4:].split(",") if w]
            if wid not in ids:
                continue
            left = [w for w in ids if w != wid]
            failed = status != "done" or r["reaction_next"] == FAILED
            if left:   # other jobs still run: remember a failure for the end
                self.store._run("UPDATE messages SET reaction_hold = ?, reaction_next = ? WHERE id = ?",
                                ("job:" + ",".join(left), FAILED if failed else r["reaction_next"], r["id"]))
                continue
            out.append(self._set(r["id"], FAILED if failed else (chosen or r["reaction_next"] or DONE)))
        return out
