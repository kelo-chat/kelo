"""Learning the user's preferences: once a night (after the journal) the user's main model reads the day's conversation, the times
the user seemed to correct the assistant (spotted here in code), the rules learned so far and the start of user.md / MEMORY.md, and
proposes short preference rules (and, in the same answer, up to GOALS_PER_NIGHT goals the user could take on, grounded in what they
said; goals.py keeps them as suggestions) and up to 5 "rechecks": things the assistant told the user that may change and that they may act
on, each backed by a quote of the assistant's own words (recheck.py keeps them). They ride on this one request: no call of its own.

A rule is one short line describing what the user prefers ("Don't add events to my calendar on its own", "캘린더에 일정을 직접 넣지
않기"), written in the owner's app language, stored in the database (table learned_rules) with its evidence (the owner's own words,
quoted, which the app shows under it), the date, a confidence between 0 and 1 and a status:
  proposed   suggested by the nightly run; used in replies once its confidence reaches PROMPT_CONFIDENCE
  accepted   the user kept it (always used; the nightly run never changes it)
  rejected   the user turned it down (never used, and the same rule is not proposed again)
The rules in use go into every turn's system prompt as a short "Learned preferences" list (prompt.py).

A rule comes only from the owner's own words stating a lasting preference ("앞으로 캘린더에 넣지 마", "from now on, keep it short"),
never from behaviour alone: approvals denied while trying things out, failed tasks or one-off requests are not preferences, so the
model is not shown them. Each new rule carries a "quote" the code looks up in the owner's own messages of the run (not the
assistant's, not automatic runs); a rule whose quote is not found there is dropped, and the quote becomes its evidence. Suggestions an
older version inferred from behaviour (confidence <= INFERRED_CONFIDENCE_MAX) are removed once at start-up (drop_inferred).

Each run is kept as last_run with structured fields the apps turn into a sentence in their own language (status, code, counts); the
free-text "reason" is for logs only.

The run skips nights without a new message from the user, and output that is not valid JSON or has the wrong shape changes nothing.
The whole request is held under INPUT_TOKENS (estimated like compaction.py does). An older server kept a written guide
(reflection/GUIDE.md); at start-up its lines become accepted rules and the old folder is kept under .backup/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import i18n
from .compaction import TIME_TAG, clip_tokens, est_tokens
from .home import Home, HomeError
from .providers import ChatRequest, TextDelta

INPUT_TOKENS = 12_000                       # the whole nightly request, estimated
LOOKBACK_SECONDS = 24 * 3600                # the first run looks this far back
MAX_LOOKBACK_SECONDS = 3 * 24 * 3600        # and no run looks further back than this, however long ago the last one was
USER_CHARS, ASSISTANT_CHARS = 700, 350      # each message as the model sees it
RULE_MAX, EVIDENCE_MAX = 200, 300           # characters
NEW_PER_NIGHT = 5
ACTIVE_MAX = 40                             # proposed + accepted rules kept; the least sure proposed ones go first
PROMPT_CONFIDENCE = 0.8                     # a proposed rule is used in replies from this confidence on
PROMPT_RULES, PROMPT_CHARS = 12, 1_500
GOALS_PER_NIGHT = 3                         # goal suggestions per night (goals.py keeps them)
INFERRED_CONFIDENCE_MAX = 0.7               # older versions kept rules inferred from behaviour at or below this (drop_inferred)
QUOTE_MIN = 4                               # characters (letters and digits) a quote needs to count
# What the apps get in last_run["code"]: why a run was skipped, pending or failed ("" for done)
CODES = ("no_messages", "switched_off", "in_use", "no_model", "model_error", "bad_output", "busy")
LANG_NAMES = {"en": "English", "ko": "Korean", "ja": "Japanese", "zh-Hans": "Simplified Chinese", "zh-Hant": "Traditional Chinese",
              "de": "German", "es": "Spanish", "fr": "French", "it": "Italian", "pt": "Portuguese", "pt-BR": "Portuguese (Brazil)",
              "ru": "Russian", "uk": "Ukrainian", "pl": "Polish", "nl": "Dutch", "tr": "Turkish", "ar": "Arabic", "hi": "Hindi",
              "id": "Indonesian", "th": "Thai", "vi": "Vietnamese"}
log = logging.getLogger(__name__)
STATUSES = ("proposed", "accepted", "rejected")
# conversation entries that are not the user's own words: scheduled or handed-over runs and job reports. The first three are saved
# in the owner's language at the time (i18n), so every language's form counts.
_SAVED_AS = ("[Automatic run · {name}]", "[Task handed over · {title}]", "[Recurring check]")


def _auto_prefixes() -> tuple[str, ...]:
    out = {"[Handoff from a background job", "[Handoff from a watcher", "[Handoff from a recheck", "[Goal work"}
    for lang in i18n.available():
        for template in _SAVED_AS:
            head = i18n.t(template, lang=lang).split("{")[0].rstrip(" ·]").strip()
            if len(head) > 1:
                out.add(head)
    return tuple(sorted(out))


AUTO_PREFIXES = _auto_prefixes()

# The user pushing back on an answer, in a few languages. Deliberately simple: the model weighs each hit itself.
CORRECTION = re.compile(
    r"^\s*(아니(?![면고])|아뇨|노노|no\b|nope\b|nein\b|non\b|não\b|нет\b|いいえ|違う|ちがう|不是|不对)"
    r"|그게\s*아니|그거\s*아니|그건\s*아니|아니라고|틀렸|틀린\s*거|잘못\s*(했|알|찾|봤|이해)|다시\s*(해|하|찾|봐|알아|확인|써|만들)"
    r"|말했잖|했잖아|그만\s*해|왜\s*자꾸|내\s*말은"
    r"|that'?s\s+(wrong|not\s+(it|right|what))|that\s+is\s+wrong|not\s+what\s+i\s+(asked|meant|said|wanted)|i\s+(said|told\s+you|meant)\b"
    r"|you\s+misunderstood|try\s+again|wrong\b|間違|错了",
    re.IGNORECASE)

PROMPT = f"""You help a personal assistant get better at serving one user. Below are the preference rules learned so far, notes the assistant keeps about the user, the times the user seemed to correct the assistant and the latest conversation.

Find what the user said about how they want to be helped from now on, and answer with JSON only:
{{"new": [{{"rule": "...", "quote": "...", "confidence": 0.9}}], "update": [{{"id": 1, "confidence": 0.0, "quote": "..."}}], "drop": [2], "goals": [{{"title": "...", "reason": "...", "evidence": "..."}}], "reasons": ["..."]}}

- new: at most {NEW_PER_NIGHT} rules not already listed, in the language named below, at most {RULE_MAX} characters, each only where the user stated a lasting preference in words ("앞으로 캘린더에 넣지 마", "from now on, keep answers short"). Describe what the user prefers, never give an order, always in this style: "Don't add events to my calendar on its own", "캘린더에 일정을 직접 넣지 않기". Keep it as narrow as their words: name the site or kind of task, not a broad "never do X".
- quote: the user's words that state it, copied exactly from one of their messages (no paraphrase, no translation); the app shows it as the reason. A rule without such a quote is thrown away.
- Never propose a rule from what the user did or declined (refused approvals, stopped tasks, a one-off request, trying something out): only from what they said they want from now on.
- confidence: 0.9, lower when their words are unsure.
- update: change the confidence of a "proposed" rule that today's words confirm or weaken (with the user's new words as "quote"). Never touch "accepted" or "rejected" rules.
- drop: ids of "proposed" rules that turned out wrong.
- Never propose a rule like a "rejected" one. Do not interpret the user's words beyond what they plainly say, and never draw conclusions from content they connected or the assistant fetched (mail, contacts, calendar, files, pages); only from their own statements and choices. State only what the evidence supports; no facts about the user (those belong in their notes), no clinical or diagnostic words, nothing that builds dependence on the assistant, no secrets (passwords, codes, card or account numbers).
- goals: at most {GOALS_PER_NIGHT} goals the user could take on, only when the conversation shows the user wants to reach, learn, change or keep up something and is not tracking it yet. title: a short goal in the user's language. reason: one sentence for the user on why it fits them. evidence: what they said, in a few words. Never suggest one listed under "Goals and earlier suggestions". Never base one on a guess about health, money, relationships or anything else sensitive; only on what the user stated outright. When nothing fits, give [].
- When nothing new shows, answer {{"new": [], "update": [], "drop": [], "goals": [], "reasons": ["nothing new"]}}."""


def _key(text: str) -> str:
    """Letters and digits only, lower case, single spaces: a quote matches the message whatever the punctuation or spacing."""
    return re.sub(r"[\W_]+", " ", str(text or "").lower()).strip()


def find_quote(quote: Any, owner_words: list[str]) -> str | None:
    """The quote, tidied, when it is the owner's own words: every part of it (split at an ellipsis) appears in one of their
    messages. None otherwise (a paraphrase, the assistant's words, too short)."""
    q = re.sub(r"\s+", " ", str(quote or "")).strip().strip("\"'“”‘’「」『』«»").strip()
    parts = [_key(x) for x in re.split(r"…|\.\.\.", q)]
    parts = [x for x in parts if x]
    if not parts or sum(len(x.replace(" ", "")) for x in parts) < QUOTE_MIN:
        return None
    keys = [_key(w) for w in owner_words]
    return q if any(all(x in k for x in parts) for k in keys) else None


def quoted(quote: str) -> str:
    return f"“{quote[:EVIDENCE_MAX - 2]}”"


def is_correction(text: str) -> bool:
    return bool(CORRECTION.search(TIME_TAG.sub("", text or "")[:400]))


def _clean(text: Any) -> str:
    return TIME_TAG.sub("", str(text or "")).strip()


def _norm(rule: str) -> str:
    return re.sub(r"[\W_]+", " ", rule.lower()).strip()


# A Korean rule written as an order ("…하지 마라", "…해라") becomes the plain style the app shows ("…하지 않기", "…하기").
_KO_DONT = re.compile(r"지\s*(?:마라|마세요|마십시오|말아라|말라|말\s*것)[.!]?$")
_KO_DO = re.compile(r"(?:하라|해라|하세요|하십시오|할\s*것)[.!]?$")
_EVIDENCE_LABEL = re.compile(r"^\s*(?:근거|이유|evidence|why|reason)\s*[:：]\s*", re.IGNORECASE)


def tidy_rule(rule: Any) -> str:
    text = re.sub(r"\s+", " ", str(rule or "")).strip().rstrip(".")
    text = _KO_DONT.sub("지 않기", text)
    return _KO_DO.sub("하기", text)


def tidy_evidence(evidence: Any) -> str:
    return _EVIDENCE_LABEL.sub("", re.sub(r"\s+", " ", str(evidence or "")).strip())[:EVIDENCE_MAX]


_OLD_SUMMARY = re.compile(r"^(\d+) new, (\d+) updated, (\d+) dropped(?:, (\d+) goal)?")


def _old_code(status: str, reason: str) -> str:
    """The code of a run kept by an older server, from its English reason."""
    for words, code in (("no new messages", "no_messages"), ("switched off", "switched_off"), ("in use", "in_use"),
                        ("no model", "no_model"), ("expected form", "bad_output"), ("already going", "busy")):
        if words in reason:
            return code
    return "model_error" if status == "failed" else ""


def run_fields(run: dict[str, Any]) -> dict[str, Any]:
    """A last_run with every structured field, also for one an older server kept (only status and reason)."""
    out = dict(run)
    reason = str(out.get("reason") or "")
    out.setdefault("code", _old_code(str(out.get("status") or ""), reason))
    m = _OLD_SUMMARY.match(reason)
    for key, group in (("added", 1), ("updated", 2), ("dropped", 3), ("goals", 4)):
        out.setdefault(key, int(m.group(group) or 0) if m else 0)
    out.setdefault("held", 0)
    return out


def parse_output(text: str) -> dict[str, Any] | None:
    """The model's JSON reply, read forgivingly (prose or a code fence around it). None when there is no JSON object."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _confidence(v: Any, default: float = 0.5) -> float:
    try:
        return round(min(1.0, max(0.0, float(v))), 2)
    except (TypeError, ValueError):
        return default


IDLE_BEFORE_RUN = 600.0     # the app must have been left alone this long (seconds) before the nightly run starts
IDLE_POLL = 60.0
IDLE_WAIT_MAX = 3 * 3600.0   # give up for the night after waiting this long


class Reflection:
    def __init__(self, store: Any, home: Home, activity: Any, stream_fn: Callable[..., AsyncIterator[Any]],
                 model: Callable[[], tuple[str, str] | None], key_args: Callable[[str], dict[str, Any]] = lambda _p: {},
                 clock: Callable[[], float] = time.time, last_use: Callable[[], float] = lambda: 0.0,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep):
        self.store, self.home, self.activity, self.stream_fn, self.model, self.key_args, self.clock = (
            store, home, activity, stream_fn, model, key_args, clock)
        self.last_use, self.sleep = last_use, sleep      # when the app was last used in the foreground (unix seconds)
        self.lock = asyncio.Lock()
        self.goals: Any = None   # the goal book (goals.py), set by the server: the run may also suggest goals
        self.rechecks: Any = None   # recheck.RecheckExtractor, set by the server: the same request also asks for rechecks (no call of its own)
        self.offers: Any = None   # offers.OfferExtractor, set by the server: and for offers, grounded in the owner's own words
        self.migrated = self.migrate_guide()
        self.tidy_proposed()
        self.drop_inferred()

    # ------------------------------------------------------------------ on/off
    def enabled(self) -> bool:
        return self.store.meta("reflection:enabled") != "0"

    def set_enabled(self, on: bool) -> dict[str, Any]:
        self.store.set_meta("reflection:enabled", "1" if on else "0")
        return self.public()

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.store.profile().get("timezone") or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), self.tz()).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ the rules
    def rules(self, statuses: tuple[str, ...] = STATUSES) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(statuses))
        return self.store._all(f"SELECT id, rule, evidence, date, confidence, status FROM learned_rules WHERE status IN ({marks}) "
                               "ORDER BY CASE status WHEN 'accepted' THEN 0 WHEN 'proposed' THEN 1 ELSE 2 END, confidence DESC, id",
                               statuses)

    def in_use(self) -> list[dict[str, Any]]:
        return [r for r in self.rules(("accepted", "proposed")) if r["status"] == "accepted" or r["confidence"] >= PROMPT_CONFIDENCE]

    def add_rule(self, rule: str, evidence: str = "", confidence: float = 0.5, status: str = "proposed") -> int | None:
        rule = tidy_rule(rule)[:RULE_MAX]
        if len(rule) < 4 or status not in STATUSES:
            return None
        if _norm(rule) in {_norm(r["rule"]) for r in self.rules()}:
            return None
        cur = self.store._run("INSERT INTO learned_rules(rule, evidence, date, confidence, status, updated_at) VALUES(?,?,?,?,?,?)",
                              (rule, tidy_evidence(evidence), self._today(), _confidence(confidence), status, self.clock()))
        return int(cur.lastrowid)

    def tidy_proposed(self) -> int:
        """Rules still waiting for the user that an older version wrote as orders ("…하지 마라") read as preferences now."""
        n = 0
        for r in self.rules(("proposed",)):
            tidy = tidy_rule(r["rule"])[:RULE_MAX]
            if tidy != r["rule"] and len(tidy) >= 4:
                self.store._run("UPDATE learned_rules SET rule = ? WHERE id = ?", (tidy, r["id"]))
                n += 1
        return n

    def drop_inferred(self) -> int:
        """Once: suggestions an older version inferred from behaviour (denied approvals, repeats) rather than the owner's words were
        kept at or below INFERRED_CONFIDENCE_MAX; they go, with the rules it held back. The owner's own choices stay."""
        if self.store.meta("reflection:words_only") == "1":
            return 0
        gone = [r["id"] for r in self.rules(("proposed",)) if r["confidence"] <= INFERRED_CONFIDENCE_MAX]
        for rid in gone:
            self.store._run("DELETE FROM learned_rules WHERE id = ?", (rid,))
        self.store.set_meta("reflection:held", "[]")
        self.store.set_meta("reflection:words_only", "1")
        return len(gone)

    def set_status(self, rule_id: int, status: str) -> dict[str, Any]:
        if status not in STATUSES:
            raise HomeError(f"status must be one of {', '.join(STATUSES)}")
        if not self.store._all("SELECT id FROM learned_rules WHERE id = ?", (rule_id,)):
            raise KeyError(rule_id)
        self.store._run("UPDATE learned_rules SET status = ?, updated_at = ? WHERE id = ?", (status, self.clock(), rule_id))
        return next(r for r in self.rules() if r["id"] == rule_id)

    def prompt_section(self) -> str:
        """The rules in use as one system-prompt section ("" when there are none)."""
        lines, used = [], 0
        for r in self.in_use()[:PROMPT_RULES]:
            line = f"- {r['rule']}"
            if used + len(line) > PROMPT_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
        return ("## Learned Preferences\nLearned from earlier conversations with this user. What they say now comes first.\n"
                + "\n".join(lines)) if lines else ""

    # ------------------------------------------------------------------ an older server's written guide
    def migrate_guide(self) -> int:
        """reflection/GUIDE.md (and the nightly notes next to it) from an older server: each bullet line of the guide becomes an
        accepted rule (it was in use already), then the folder is moved to .backup/<time>/reflection. Returns the rules added."""
        d = self.home.root / "reflection"
        if not d.is_dir() or d.is_symlink():
            return 0
        guide = d / "GUIDE.md"
        added, section = 0, ""
        if guide.is_file() and not guide.is_symlink():
            for line in guide.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("#"):
                    section = line.lstrip("# ").strip()
                elif m := re.match(r"^\s*[-*]\s+(.+)$", line):
                    if self.add_rule(m.group(1), f"from the earlier working guide ({section})" if section else "from the earlier working guide",
                                     0.7, "accepted"):
                        added += 1
        import shutil
        dst = self.home.root / ".backup" / time.strftime("%Y%m%dT%H%M%S") / "reflection"
        dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.move(str(d), str(dst))
        return added

    # ------------------------------------------------------------------ reading
    def last_run(self) -> dict[str, Any] | None:
        try:
            v = json.loads(self.store.meta("reflection:last_run") or "null")
        except ValueError:
            return None
        return run_fields(v) if isinstance(v, dict) else None

    def public(self) -> dict[str, Any]:
        """GET /api/reflection. `guide` is the rules in use as text, for app versions that still show a written guide."""
        return {"enabled": self.enabled(), "rules": self.rules(), "last_run": self.last_run(),
                "guide": "\n".join(f"- {r['rule']}" for r in self.in_use())}

    # ------------------------------------------------------------------ inputs
    def _since(self) -> float:
        now = self.clock()
        try:
            seen = float(self.store.meta("reflection:seen_until") or 0)
        except ValueError:
            seen = 0.0
        return max(seen or now - LOOKBACK_SECONDS, now - MAX_LOOKBACK_SECONDS)

    def _messages(self, since: float) -> list[dict[str, Any]]:
        from .secret_guard import scrub_rows   # a message sent with a secret in it (after the warning) is read without it
        return scrub_rows(self.store, self.store._all("SELECT id, role, content, created_at FROM messages WHERE created_at > ? ORDER BY id", (since,)))

    @staticmethod
    def _from_user(m: dict[str, Any]) -> bool:
        return m["role"] == "user" and not _clean(m["content"]).startswith(AUTO_PREFIXES)

    def owner_words(self, msgs: list[dict[str, Any]]) -> list[str]:
        """The owner's own messages of a run (not the assistant's, not automatic runs): where a rule's quote must be found."""
        return [_clean(m["content"]).replace("<message_break/>", " ") for m in msgs if self._from_user(m)]

    @staticmethod
    def assistant_words(msgs: list[dict[str, Any]]) -> list[str]:
        """The assistant's own messages of a run: where the quote of a recheck must be found."""
        return [_clean(m["content"]).replace("<message_break/>", " ") for m in msgs if m["role"] == "assistant"]

    def signals(self, msgs: list[dict[str, Any]], since: float) -> dict[str, list[dict[str, Any]]]:
        """The times the user seemed to correct the assistant. Denied approvals and failed tasks are left out on purpose: behaviour
        alone never makes a rule."""
        corrections, last_answer = [], ""
        for m in msgs:
            text = _clean(m["content"])
            if m["role"] != "user":
                last_answer = text
            elif self._from_user(m) and is_correction(text):
                corrections.append({"user": text[:200], "after": last_answer[:160]})
        return {"corrections": corrections[-8:]}

    @staticmethod
    def signals_text(sig: dict[str, list[dict[str, Any]]]) -> str:
        out = [f"Times the user seemed to correct the assistant (a word match; judge each yourself): {len(sig['corrections'])}"]
        out += [f'- user: "{c["user"]}"' + (f' (after: "{c["after"]}")' if c["after"] else "") for c in sig["corrections"]]
        return "\n".join(out)

    def _conversation(self, msgs: list[dict[str, Any]], budget: int) -> str:
        tz, lines, used = self.tz(), [], 0
        for m in reversed(msgs):   # the newest that fit
            who, cap = ("User", USER_CHARS) if m["role"] == "user" else ("Assistant", ASSISTANT_CHARS)
            text = re.sub(r"\s+", " ", _clean(m["content"]).replace("<message_break/>", " "))
            text = text if len(text) <= cap else text[:cap].rstrip() + " …"
            line = f"{datetime.fromtimestamp(m['created_at'], tz).strftime('%m-%d %H:%M')} {who}: {text}"
            t = est_tokens(line) + 1
            if used + t > budget:
                break
            lines.append(line)
            used += t
        left = len(msgs) - len(lines)
        return ((f"[{left} earlier messages left out]\n" if left else "") + "\n".join(reversed(lines))).strip()

    def build_request(self, msgs: list[dict[str, Any]], since: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        sig = self.signals(msgs, since)

        def head(name: str, tokens: int) -> str:
            p = self.home.root / name
            return clip_tokens(p.read_text(encoding="utf-8", errors="replace").strip(), tokens) if p.is_file() else ""

        current = "\n".join(f"{r['id']}. [{r['status']}, {r['confidence']}] {r['rule']}" for r in self.rules()) or "(none yet)"
        lang = i18n.current()
        parts = [f"Language for rules, evidence and goals: {LANG_NAMES.get(lang, lang)}",
                 f"Rules so far:\n{clip_tokens(current, 1_200)}",
                 f"user.md (start):\n{head('user.md', 500) or '(empty)'}",
                 f"MEMORY.md (start):\n{head('MEMORY.md', 800) or '(empty)'}",
                 clip_tokens(self.signals_text(sig), 900)]
        if self.goals is not None:
            taken = "\n".join(f"- {t}" for t in self.goals.taken_titles()) or "(none)"
            parts.append(f"Goals and earlier suggestions (tracked, accepted or declined):\n{clip_tokens(taken, 500)}")
        system = PROMPT + (self.rechecks.prompt_part() if self.rechecks is not None else "") + (self.offers.prompt_part() if self.offers is not None else "")
        if self.rechecks is not None:
            parts.append(clip_tokens(self.rechecks.known_block(), 500))
        if self.offers is not None and (block := self.offers.known_block()):
            parts.append(clip_tokens(block, 400))
        fixed = est_tokens(system) + sum(est_tokens(p) for p in parts) + 200
        convo = self._conversation(msgs, max(1_000, INPUT_TOKENS - fixed))
        user = "\n\n".join([*parts, f"Conversation:\n{convo}"])
        return ([{"role": "system", "content": system}, {"role": "user", "content": user}],
                {"signals": sig, "tokens": est_tokens(system) + est_tokens(user)})

    # ------------------------------------------------------------------ the run
    def _record(self, status: str, code: str = "", reason: str = "", changed: bool = False, **counts: int) -> dict[str, Any]:
        """Keeps the run as last_run: status, code (CODES, "" when done) and counts for the apps; `reason` is for logs only."""
        run = {"at": self.clock(), "status": status, "code": code, "changed": changed,
               **{k: int(counts.get(k, 0)) for k in ("added", "updated", "dropped", "goals", "held")}, "reason": reason}
        self.store.set_meta("reflection:last_run", json.dumps(run, ensure_ascii=False))
        log.info("nightly look-back: %s%s · %s", status, f" ({code})" if code else "", reason)
        return run

    def apply_goals(self, out: dict[str, Any]) -> int:
        """Goal suggestions from the model's answer, at most GOALS_PER_NIGHT; repeats of goals or earlier suggestions are left out."""
        if self.goals is None or not isinstance(out.get("goals"), list):
            return 0
        added = 0
        for item in out["goals"][:GOALS_PER_NIGHT]:
            if isinstance(item, dict) and self.goals.suggest(str(item.get("title") or ""), str(item.get("reason") or ""),
                                                             str(item.get("evidence") or ""), "nightly"):
                added += 1
        return added

    def apply(self, out: dict[str, Any], owner_words: list[str] | None = None) -> tuple[int, int, int]:
        """The model's answer -> (added, updated, dropped). A new rule needs a quote found in `owner_words` (the owner's own messages
        the model read); without one it is left out, whatever the model says. Only proposed rules are ever changed or dropped."""
        words = owner_words or []
        proposed = {r["id"]: r for r in self.rules(("proposed",))}
        added = updated = dropped = 0
        known = {_norm(r["rule"]) for r in self.rules()}
        for item in (out.get("new") if isinstance(out.get("new"), list) else [])[:NEW_PER_NIGHT]:
            if not isinstance(item, dict):
                continue
            rule, quote = tidy_rule(item.get("rule"))[:RULE_MAX], find_quote(item.get("quote"), words)
            if len(rule) < 4 or _norm(rule) in known:
                continue
            if quote is None:
                log.info("nightly look-back: left out %r, not backed by the owner's own words", rule[:80])
                continue
            if self.add_rule(rule, quoted(quote), _confidence(item.get("confidence"), 0.9)):
                added += 1
                known.add(_norm(rule))
        for item in out.get("update") if isinstance(out.get("update"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), int) and item["id"] in proposed:
                r = proposed[item["id"]]
                quote = find_quote(item.get("quote"), words)
                self.store._run("UPDATE learned_rules SET confidence = ?, evidence = ?, updated_at = ? WHERE id = ?",
                                (_confidence(item.get("confidence"), r["confidence"]), quoted(quote) if quote else r["evidence"],
                                 self.clock(), r["id"]))
                updated += 1
        for rid in out.get("drop") if isinstance(out.get("drop"), list) else []:
            if isinstance(rid, int) and rid in proposed:
                self.store._run("DELETE FROM learned_rules WHERE id = ?", (rid,))
                dropped += 1
        active = self.rules(("accepted", "proposed"))
        extra = len(active) - ACTIVE_MAX
        if extra > 0:   # keep the list short: the least sure proposed rules go
            for r in sorted((r for r in active if r["status"] == "proposed"), key=lambda r: r["confidence"])[:extra]:
                self.store._run("DELETE FROM learned_rules WHERE id = ?", (r["id"],))
                dropped += 1
        return added, updated, dropped

    async def run(self, *, force: bool = False) -> dict[str, Any]:
        """One run. Returns {"at", "status": done|skipped|failed|pending|busy, "reason", "changed"} (also kept as last_run).
        `force` (the app's "now" button) looks at least at the last day even when it was read already."""
        if self.lock.locked():
            return run_fields({"at": self.clock(), "status": "busy", "code": "busy", "reason": "a run is already going", "changed": False})
        async with self.lock:
            return await self._run(force)

    async def _run(self, force: bool) -> dict[str, Any]:
        since = self._since()
        if force:
            since = min(since, self.clock() - LOOKBACK_SECONDS)
        msgs = self._messages(since)
        if not any(self._from_user(m) for m in msgs):
            return self._record("skipped", "no_messages", "no new messages from the user since the last run")
        chosen = self.model()
        if not chosen:
            return self._record("pending", "no_model", "no model is set")
        provider, model = chosen
        messages, _info = self.build_request(msgs, since)
        req = ChatRequest(model=model, messages=messages, tools=[], extra={})
        parts: list[str] = []
        try:
            async for ev in self.stream_fn(provider, req, **self.key_args(provider)):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
        except Exception as e:  # noqa: BLE001
            return self._record("failed", "model_error", f"{type(e).__name__}: {str(e)[:160]}")
        out = parse_output("".join(parts))
        if out is None or not any(isinstance(out.get(k), list) for k in ("new", "update", "drop", "goals", "rechecks", "offers")):
            return self._record("failed", "bad_output", "the model did not answer in the expected form; nothing was changed")
        added, updated, dropped = self.apply(out, self.owner_words(msgs))
        suggested = self.apply_goals(out)
        rechecked = self.rechecks.apply(out, self.assistant_words(msgs)) if self.rechecks is not None else 0
        offered = self.offers.apply(out, self.owner_words(msgs)) if self.offers is not None else 0
        self.store.set_meta("reflection:seen_until", repr(msgs[-1]["created_at"]))
        raw = out.get("reasons")
        reasons = [str(r).strip()[:200] for r in (raw if isinstance(raw, list) else [raw]) if r is not None and str(r).strip()]
        summary = (f"{added} new, {updated} updated, {dropped} dropped" + (f", {suggested} goal(s) suggested" if suggested else "")
                   + (f", {rechecked} recheck(s) kept" if rechecked else "") + (f", {offered} offer(s) made" if offered else ""))
        return self._record("done", "", "; ".join([summary, *reasons])[:600], bool(added or updated or dropped or suggested or rechecked or offered),
                            added=added, updated=updated, dropped=dropped, goals=suggested)

    async def nightly(self) -> dict[str, Any]:
        """The nightly job: only when switched on, and only once the app has not been used for IDLE_BEFORE_RUN seconds (waits up to
        IDLE_WAIT_MAX for that; a night the user keeps using the app is skipped)."""
        if not self.enabled():
            return self._record("skipped", "switched_off", "switched off")
        waited = 0.0
        while self.clock() - self.last_use() < IDLE_BEFORE_RUN:
            if waited >= IDLE_WAIT_MAX:
                return self._record("skipped", "in_use", "the app was in use the whole time")
            await self.sleep(IDLE_POLL)
            waited += IDLE_POLL
        return await self.run()
