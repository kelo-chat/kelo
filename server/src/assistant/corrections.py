"""Corrections of what Kelo told the owner: ONE owner of "look again at Kelo's own recent answers", used by the morning briefing
(briefing.py, while the briefing is on) and the hourly look (situation.py, while proactive messages are on). Neither has its own copy.

check(now):
  1. Kelo's own answers of the last WINDOW_HOURS (assistant messages, newest first, each cut at MESSAGE_CHARS, CONVERSATION_CHARS in
     all); an answer with a correction in the proactive ledger (key "correction:<message id>", told or set aside, until the row
     expires) is left out. No answer left: [] and no model call.
  2. One unattended research turn (source "recheck": it never asks or announces, turn_engine.SELF_STARTED; it reads pages a search
     found without a card, outside_rule.py) with the read-only tools (tool_roles.research_tools) and its own `propose_correction`.
     Prompt: content/prompts/correction_check.md. Budget: the hourly look's RESEARCH_STEPS and RESEARCH_SECONDS.
  3. Code checks every proposal: the message is one of the answers shown, the quote is Kelo's own words in it (reflection.find_quote),
     the sources pass recheck.SourceRule (two registrable domains, or one government domain).
  4. Returns the corrections; nothing is recorded here. The caller tells the owner and then calls record(key, "told") (or "dropped"
     when it chose not to tell): one proactive_log row, status "dropped" with the reason "already told" and SENT_KEEP (never counted
     against the daily limits), so the other path never tells it again.
Silent when unchanged: what still holds, or could not be settled, gives nothing and records nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import i18n
from .agent import Tool
from .agent.fence import fence
from .i18n import t
from .proactive import DROPPED_KEEP, SENT_KEEP, Candidate
from .prompt import language_line
from .recheck import SourceRule
from .reflection import find_quote
from .situation import CONVERSATION_CHARS, MESSAGE_CHARS, RESEARCH_SECONDS, RESEARCH_STEPS, WINDOW_HOURS, prompts_dir
from .tool_roles import research_tools

KEY = "correction:"
SOURCE = "recheck"          # the turn's source and tool scope (see the docstring)
NOW_MAX, WHY_MAX, QUOTE_MAX = 600, 300, 400

RunTurn = Callable[[str, str, list[Any], int, float, str, str], Awaitable[str]]   # situation.RunTurn: system, user, tools, steps, seconds, title, scope


@dataclass
class Correction:
    message_id: int
    said_at: float
    quote: str
    now: str
    why: str = ""
    since: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{KEY}{self.message_id}"

    @property
    def url(self) -> str | None:
        return str(self.sources[0]["url"]) if self.sources else None

    def line(self) -> str:
        """One line for the briefing card: what is right now, and who says so."""
        who = next((str(s.get("publisher") or s.get("domain")) for s in self.sources if s.get("publisher") or s.get("domain")), "")
        return self.now + (f" ({who})" if who else "")

    def prepared(self, said: str) -> str:
        """What the hourly look's selection and writer read (`said`: when Kelo said it, local)."""
        return (f"Kelo told the owner on {said} (message {self.message_id}): \"{self.quote}\". What is right now: {self.now}"
                + (f" (since {self.since})" if self.since else "") + (f". What the wrong answer could cost them: {self.why}" if self.why else "")
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
                f"Now: {self._local(now)} ({self.p.tz().key})", language_line(i18n.owner()), "",
                "Kelo's answers (newest first):",
                fence("\n\n".join(f"message {a['id']} · {self._local(a['at'])}\n{a['text']}" for a in answers)), "",
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
            found.append(Correction(mid, ans["at"], quote[:QUOTE_MAX], now_text, " ".join(str(a.get("why") or "").split())[:WHY_MAX],
                                    str(a.get("since") or "").strip()[:20] or None, judged.sources))
            return f"Kept: a correction for message {mid}."

        return Tool("propose_correction",
                    "Keep one correction of an answer Kelo gave: only when an official source, or two independent ones, show it no longer "
                    "holds or was wrong. Nothing is sent by this.",
                    {"type": "object", "properties": {
                        "message": {"type": "integer", "description": "the answer's message number"},
                        "quote": {"type": "string", "description": "Kelo's words that are wrong, copied exactly"},
                        "now": {"type": "string", "description": "what is right now, one or two sentences in the owner's language"},
                        "since": {"type": "string", "description": "YYYY-MM-DD, when a page says when it changed"},
                        "why": {"type": "string", "description": "what the owner could do or lose with the wrong answer"},
                        "sources": {"type": "array", "items": {"type": "object", "properties": {
                            "url": {"type": "string"}, "publisher": {"type": "string"}, "official": {"type": "boolean"},
                            "date": {"type": "string"}}, "required": ["url"]}}},
                     "required": ["message", "quote", "now", "sources"]},
                    propose_correction)

    def record(self, key: str, outcome: str, title: str = "") -> None:
        """The caller told the owner ("told") or chose not to ("dropped"): one ledger row, so neither path tells it again."""
        cand = Candidate(key=str(key), source="correction", title=(title or str(key))[:200])
        if outcome == "told":
            self.p._record(cand, "dropped", t("The recheck already told you"), keep=SENT_KEEP)
        else:
            self.p._record(cand, "dropped", t("Not worth telling you"), keep=DROPPED_KEEP)
