"""Corrections of what Kelo told the owner: ONE owner of "look again at Kelo's own recent answers", used by the morning briefing
(briefing.py, while the briefing is on) and the hourly look (situation.py, while proactive messages are on). Neither has its own copy.

check(now):
  1. Kelo's own answers of the last WINDOW_HOURS (assistant messages, newest first, each cut at MESSAGE_CHARS, CONVERSATION_CHARS in
     all); an answer with a correction in the proactive ledger (key "correction:<message id>", told or set aside, until the row
     expires) is left out. The model reads them oldest first together with the owner's messages from the oldest answer on (context
     only, `thread`). No answer left: [] and no model call.
  2. One unattended research turn (source "recheck": it never asks or announces, turn_engine.SELF_STARTED; it reads pages a search
     found without a card, outside_rule.py) with the read-only tools (tool_roles.research_tools) and its own `propose_correction`.
     Prompt: content/prompts/correction_check.md. Budget: the hourly look's RESEARCH_STEPS and RESEARCH_SECONDS.
  3. Code checks every proposal: the message is one of the answers shown, the quote is Kelo's own words in it (reflection.find_quote),
     the sources pass recheck.SourceRule (two registrable domains, or one government domain), and the proposal says what the words
     meant in their own frame (`meant`) and what the owner may still rely on (`why`); without both it is refused.
  4. Returns the corrections; nothing is recorded here. The caller tells the owner and then calls record(key, "told") (or "dropped"
     when it chose not to tell): one proactive_log row, status "dropped" with the reason "already told" and SENT_KEEP (never counted
     against the daily limits), so the other path never tells it again.
  5. The morning card shows each as one self-explaining line (`Correction.line`, CARD_LINE): what Kelo said and when, then what is right.
Silent when unchanged: what still holds, or could not be settled, gives nothing and records nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import i18n
from .agent import Tool
from .agent.fence import fence
from .i18n import N_, t
from .model_limits import clock_text
from .proactive import DROPPED_KEEP, SENT_KEEP, Candidate
from .prompt import language_line
from .recheck import SourceRule
from .reflection import find_quote
from .situation import CONVERSATION_CHARS, MESSAGE_CHARS, RESEARCH_SECONDS, RESEARCH_STEPS, WINDOW_HOURS, prompts_dir
from .tool_roles import research_tools

KEY = "correction:"
SOURCE = "recheck"          # the turn's source and tool scope (see the docstring)
NOW_MAX, WHY_MAX, QUOTE_MAX, MEANT_MAX = 600, 300, 400, 300
CARD_LINE = N_("I told you “{quote}” {when}. Correction: {now}")

RunTurn = Callable[[str, str, list[Any], int, float, str, str], Awaitable[str]]   # situation.RunTurn: system, user, tools, steps, seconds, title, scope


def short_quote(quote: str) -> str:
    """Kelo's earlier words as the card shows them: the quote as kept, on one line (no cut: the owner decides any limit)."""
    return " ".join(str(quote or "").split())


def said_when(at: float, now: float, tz: ZoneInfo) -> str:
    """When Kelo said it, as the owner reads it, in the current language: 'today at 9:10 AM' / 'yesterday at 8:01 PM' /
    'on 11/18 at 8:01 PM' (the owner's time zone)."""
    d, today = datetime.fromtimestamp(at, tz), datetime.fromtimestamp(now, tz).date()
    clock = clock_text(d)
    if d.date() == today:
        return t("today at {time}", time=clock)
    if d.date() == today - timedelta(days=1):
        return t("yesterday at {time}", time=clock)
    return t("on {month}/{day} at {time}", month=d.month, day=d.day, time=clock)


@dataclass
class Correction:
    message_id: int
    said_at: float
    quote: str
    now: str
    why: str = ""
    since: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    meant: str = ""

    @property
    def key(self) -> str:
        return f"{KEY}{self.message_id}"

    @property
    def url(self) -> str | None:
        return str(self.sources[0]["url"]) if self.sources else None

    def line(self, now: float, tz: ZoneInfo) -> str:
        """One line for the briefing card that explains itself, in the current language: what Kelo said and when (a short quote), then
        what is right now, and who says so."""
        who = next((str(s.get("publisher") or s.get("domain")) for s in self.sources if s.get("publisher") or s.get("domain")), "")
        return (t(CARD_LINE, quote=short_quote(self.quote), when=said_when(self.said_at, now, tz), now=self.now)
                + (f" ({who})" if who else ""))

    def prepared(self, said: str) -> str:
        """What the hourly look's selection and writer read (`said`: when Kelo said it, local)."""
        return (f"Kelo told the owner on {said} (message {self.message_id}): \"{self.quote}\""
                + (f" (meaning: {self.meant})" if self.meant else "") + f". What is right now: {self.now}"
                + (f" (since {self.since})" if self.since else "") + (f". What the owner may still rely on: {self.why}" if self.why else "")
                + ". Checked on: " + ", ".join(str(s["url"]) for s in self.sources) + ".")


class CorrectionCheck:
    def __init__(self, proactive: Any, *, run_turn: RunTurn, tools: Callable[[], list[Any]] = lambda: [],
                 prompt: Callable[[], str] | None = None):
        self.p, self.store = proactive, proactive.store
        self.run_turn, self.tools = run_turn, tools
        self.prompt = prompt or (lambda: (prompts_dir() / "correction_check.md").read_text(encoding="utf-8"))

    def owns(self, key: str) -> bool:
        return str(key).startswith(KEY)

    def _local(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.p.tz()).strftime("%Y-%m-%d %H:%M")

    def answers(self, now: float) -> list[dict[str, Any]]:
        """Kelo's answers of the window not corrected yet, newest first, within the size budget."""
        rows = self.store._all("SELECT id, content, created_at FROM messages WHERE role = 'assistant' AND created_at > ? ORDER BY id DESC",
                               (now - WINDOW_HOURS * 3600,))
        out: list[dict[str, Any]] = []
        used = 0
        for r in rows:
            if self.p._ledgered(f"{KEY}{r['id']}", now):
                continue
            text = " ".join(str(r["content"] or "").replace("<message_break/>", " ").split())[:MESSAGE_CHARS]
            if not text:
                continue
            if used + len(text) > CONVERSATION_CHARS:
                break
            used += len(text)
            out.append({"id": int(r["id"]), "at": float(r["created_at"]), "text": text})
        return out

    def thread(self, answers: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """What the check reads, oldest first: Kelo's answers (proposable, with their message number) and the owner's messages from the
        oldest answer shown on (context only: they show what came after an answer). The owner's messages share the size budget; the
        newest are kept."""
        if not answers:
            return []
        used = sum(len(a["text"]) for a in answers)
        rows = self.store._all("SELECT content, created_at FROM messages WHERE role = 'user' AND created_at >= ? ORDER BY id DESC",
                               (min(a["at"] for a in answers),))
        owner: list[dict[str, Any]] = []
        for r in rows:
            text = " ".join(str(r["content"] or "").replace("<message_break/>", " ").split())[:MESSAGE_CHARS]
            if not text:
                continue
            if used + len(text) > CONVERSATION_CHARS:
                break
            used += len(text)
            owner.append({"role": "user", "at": float(r["created_at"]), "text": text})
        return sorted([{**a, "role": "assistant"} for a in answers] + owner, key=lambda x: x["at"])

    def told_lines(self, now: float) -> list[str]:
        rows = self.store._all("SELECT title, decided_at FROM proactive_log WHERE key LIKE ? AND expires_at > ? ORDER BY id DESC LIMIT 40",
                               (KEY + "%", now))
        return [f"{self._local(r['decided_at'])} · {r['title']}" for r in rows]

    async def check(self, now: float) -> list[Correction]:
        answers = self.answers(now)
        if not answers:
            return []
        found: list[Correction] = []
        with i18n.owner_language():
            body = "\n".join([
                f"Now: {self._local(now)} in the owner's time zone ({self.p.tz().key})", language_line(i18n.owner()), "",
                "The conversation of the last day, oldest first (only Kelo's answers carry a message number; the owner's messages show "
                "what came after):",
                fence("\n\n".join((f"message {x['id']} · {self._local(x['at'])}" if x["role"] == "assistant"
                                   else f"owner · {self._local(x['at'])}") + f"\n{x['text']}" for x in self.thread(answers, now))), "",
                "Corrections already told:", "\n".join(self.told_lines(now)) or "(none)"])
            await self.run_turn(self.prompt(), body, research_tools(self.tools()) + [self.tool(answers, found)], RESEARCH_STEPS,
                                RESEARCH_SECONDS, t("Rechecking an earlier answer"), SOURCE)
        return found

    def tool(self, answers: list[dict[str, Any]], found: list[Correction]) -> Tool:
        by_id = {a["id"]: a for a in answers}

        async def propose_correction(a: dict[str, Any]) -> str:
            try:
                mid = int(a.get("message"))
            except (TypeError, ValueError):
                mid = -1
            ans = by_id.get(mid)
            if ans is None:
                return "Error: message is the number of one of the answers shown."
            if any(c.message_id == mid for c in found):
                return f"Already kept: a correction for message {mid}."
            quote = find_quote(a.get("quote"), [ans["text"]])
            if quote is None:
                return "Refused: quote must be Kelo's own words, copied exactly from that answer."
            now_text = " ".join(str(a.get("now") or "").split())[:NOW_MAX]
            if not now_text:
                return "Error: now (what is right now) is required."
            judged = SourceRule("").judge(a.get("sources"))
            if not judged.ok:
                return f"Refused: {judged.reason}. A correction needs one official source or two independent ones."
            meant = " ".join(str(a.get("meant") or "").split())[:MEANT_MAX]
            if not meant:
                return "Error: meant (what Kelo's words meant, read in the frame they were said in) is required."
            why = " ".join(str(a.get("why") or "").split())[:WHY_MAX]
            if not why:
                return ("Refused: why must say what the owner may still do or rely on now because of those words; when nothing, there is "
                        "nothing to correct.")
            found.append(Correction(mid, ans["at"], quote[:QUOTE_MAX], now_text, why, str(a.get("since") or "").strip()[:20] or None,
                                    judged.sources, meant))
            return f"Kept: a correction for message {mid}."

        return Tool("propose_correction",
                    "Keep one correction of an answer Kelo gave: only when an official source, or two independent ones, show it no longer "
                    "holds or was wrong when read in the frame it was said in, and the owner may still rely on it. Nothing is sent by this.",
                    {"type": "object", "properties": {
                        "message": {"type": "integer", "description": "the answer's message number (the owner's messages have none)"},
                        "quote": {"type": "string",
                                  "description": "the shortest stretch of Kelo's words that carries the wrong claim, copied exactly; the owner is shown it"},
                        "meant": {"type": "string",
                                  "description": "what those words meant, read in the frame they were said in: the reference points the answer "
                                                 "used, or the owner's own where it named none"},
                        "now": {"type": "string",
                                "description": "what is right now: one or two plain sentences in the owner's language that stand on their own"},
                        "since": {"type": "string", "description": "YYYY-MM-DD, when a page says when it changed"},
                        "why": {"type": "string", "description": "what the owner may still do or rely on now because of those words"},
                        "sources": {"type": "array", "items": {"type": "object", "properties": {
                            "url": {"type": "string"}, "publisher": {"type": "string"}, "official": {"type": "boolean"},
                            "date": {"type": "string"}}, "required": ["url"]}}},
                     "required": ["message", "quote", "meant", "now", "why", "sources"]},
                    propose_correction)

    def record(self, key: str, outcome: str, title: str = "") -> None:
        """The caller told the owner ("told") or chose not to ("dropped"): one ledger row, so neither path tells it again."""
        cand = Candidate(key=str(key), source="correction", title=(title or str(key))[:200])
        if outcome == "told":
            self.p._record(cand, "dropped", t("The recheck already told you"), keep=SENT_KEEP)
        else:
            self.p._record(cand, "dropped", t("Not worth telling you"), keep=DROPPED_KEEP)
