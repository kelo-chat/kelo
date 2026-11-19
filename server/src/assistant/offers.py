"""Offers: a few ready-to-run things Kelo can do for the owner now, each one sentence and one button.

An offer is one concrete task grounded in the owner's own context, for example "LG U+ 결합으로 인터넷·TV 가입 접수해 드릴게요. 지금 쓰는
통신사와 주소만 확인해 주세요." It has the sentence, what Kelo still needs from the owner (at most 3 short questions), a start action and a
dismiss action. Tapping the start action sends the offer's `request` as the owner's own chat message and gives the model, as a hidden note
that is never stored, the steps Kelo prepared. Offers are one-shot tasks, not goals (goals.py has its own suggestions, which this module
follows in shape but never mixes with). Kelo is self-hosted: nothing here counts across users.

What the card shows and what a tap runs (audit F1): the card shows the request and the steps, so the owner sees what will run. A tap
starts Kelo's own turn (source "offer", not the owner's chat): the request and the plan reach the model in one note that says they are
Kelo's own words, not the owner's. So they never count as the owner's words for the outside-text rule or for the card skips that the
owner's own chat gets. offer_post is refused once outside text has reached its turn.

Small classes, one job each; the pure ones are tested alone:
  * NumberRule          pure: does a text state a number (digits or a currency)? A nightly offer must not: that pass cannot browse.
  * OfferRanker         pure: the order of the cards, the demotions, the retirements and the pauses (see below).
  * OfferRepository     the table `offers`: one open offer per topic key (a new one updates the old), seen, state changes, dismissals.
  * FollowupOffers      offers of kind `resume`, made in code from open follow-ups that wait for the owner (no model).
  * OfferExtractor      the nightly look-back asks for offers in reflection.py's own request (no call of its own); each needs a quote
                        of the OWNER's words (reflection.find_quote) and states no number.
  * OfferTools          the agent's tool offer_post (loaded with find_tools; there is no line for it in the prompt).
  * OfferNote           the hidden note a started offer gives the model: Kelo's own plan, marked as not the owner's words.
  * OfferService        what the routes use: listing, start, dismiss, and the on/off setting.

Where offers come from (column `source`):
  followup  kind `resume`: every open follow-up that waits for a piece of information from the owner (a job paused for an answer, a
            question asked in the chat, a login, a fill-in card). The sentence is a template plus the task's title; the request is
            "<title> 이어서 해 줘" (in the owner's language); the steps point at that task. The offer closes with the follow-up.
  nightly   kind wish | routine | deadline, up to 3 a night, from the same request as the rules and goals.
  chat      kind `chat`: the agent posts one with offer_post when a concrete next task shows up in conversation that the owner did not ask
            for now. Only such an offer may carry numbers (a price, a fee, a condition), and only with `sources` that pass
            recheck.SourceRule (two sources from different sites, or one official one); it is then kept with the time it was checked and
            retired 7 days later.

Ranking (OfferRanker): resume, then deadline (a date within 7 days; a farther one counts as a wish until it is near), then chat (posted
today; an older one counts as a wish), then wish, then routine; within a kind the newer one first. Every view of an offer the owner did not
start counts once per local day (`seen`): after 5 views each further view moves it down one place, and at 12 views it is retired. A dismissal
(not_interested / done_already) moves every offer of the same family (kind + the first two words of the topic) down 2 places for 30 days;
three dismissals of one kind within 30 days pause that kind for 14 days. "not_now" hides the offer for 3 days and counts for nothing. An offer
past `expires_at` is retired (14 days by default; a deadline on its date; a resume offer when its follow-up closes). At most 6 are shown.

OFFERS API (also next to serialize() in server.py):
Offer: {"id", "kind": resume|deadline|chat|wish|routine, "sentence", "needs": [str], "request": str, "steps": [str], "created_at",
  "has_numbers": bool, "checked_at"?: unix (only when has_numbers)}. request and steps are what the card shows (what will run); the
  evidence stays on the server.
GET  /api/offers                 {"items": [Offer]} ranked, at most 6, [] while offers are off. Each item returned is counted as seen at
       most once per local day, not on every refresh.
POST /api/offers/{id}/start      {"turn_id"}: a reply starts on the offer's request as Kelo's own turn (source "offer"; not stored as the
       owner's message; follow it with GET /api/turns/{turn_id}); the offer leaves the list. 404 unknown or not shown, 409 while a reply is already running (and the other
       answers a chat message can get: 428 ai_consent_required, 409 secret_in_message, no model).
POST /api/offers/{id}/dismiss    {"reason"?: not_interested|done_already|not_now} -> {"ok"}; 404 unknown or not shown, 422 another reason.
Offers are switched on and off with PUT /api/proactive {"offers": bool} (on by default).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .agent import Tool
from .i18n import N_, t
from .recheck import SourceRule, TopicKey
from .reflection import find_quote, quoted

log = logging.getLogger(__name__)

KINDS = ("resume", "deadline", "chat", "wish", "routine")   # the order the cards come in
NIGHTLY_KINDS = ("wish", "routine", "deadline")
SOURCES = ("nightly", "chat", "followup")
STATES = ("shown", "started", "dismissed", "retired")
REASONS = ("not_interested", "done_already", "not_now")
NEGATIVE = ("not_interested", "done_already")               # a dismissal that counts against the family and the kind
DAY = 86400.0
SHOW_MAX = 6
SENTENCE_MAX, REQUEST_MAX, NEED_MAX, STEP_MAX, TOPIC_MAX = 220, 200, 100, 200, 60
NEEDS_MAX, STEPS_MAX = 3, 6
PER_NIGHT = 3
EXPIRES_DAYS = 14             # the default life of an offer
DEADLINE_DAYS = 7             # a deadline ranks as one from this close to its date
FACT_DAYS = 7                 # an offer with numbers lives this long after its facts were checked
NOT_NOW_DAYS = 3
VIEWS_FREE, VIEWS_RETIRE = 5, 12
FAMILY_DAYS, FAMILY_PLACES = 30, 2
STRIKES, STRIKE_DAYS, PAUSE_DAYS = 3, 30, 14
QUIET_DAYS = 14               # a topic that was started or retired is not offered again for this long (FAMILY_DAYS after a dismissal)
KEEP_CLOSED_DAYS = 60
RESUME_KINDS = ("worker", "reply", "login", "entry")   # follow-ups that wait for a piece of information from the owner

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, topic_key TEXT NOT NULL, sentence TEXT NOT NULL,
    needs_json TEXT NOT NULL DEFAULT '[]', request TEXT NOT NULL, steps_json TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'nightly', sources_json TEXT NOT NULL DEFAULT '[]', checked_at REAL,
    created REAL NOT NULL, expires_at REAL, state TEXT NOT NULL DEFAULT 'shown', seen INTEGER NOT NULL DEFAULT 0, last_seen REAL,
    dismiss_reason TEXT NOT NULL DEFAULT '', started_turn TEXT NOT NULL DEFAULT '',
    closed REAL, hide_until REAL);
CREATE INDEX IF NOT EXISTS offers_topic ON offers(topic_key);
CREATE UNIQUE INDEX IF NOT EXISTS offers_one_open ON offers(topic_key) WHERE state = 'shown';
"""

EXTRACT_PROMPT = f"""

Offers. In the same JSON, also give "offers": [{{"kind": "wish", "topic": "...", "sentence": "...", "needs": ["..."], "request": "...", "steps": ["..."], "by": null, "quote": "..."}}].
- At most {PER_NIGHT} concrete things the assistant can do for the user right now, each grounded in what THE USER said: a wish they voiced (kind "wish"), something they keep doing or checking (kind "routine"), or a decision of theirs that has a date and stalled (kind "deadline", with "by": YYYY-MM-DD, the date). Never a goal (those go under "goals"), nothing already listed under "Offers already shown", nothing about health, money trouble, relationships or other sensitive guesses.
- sentence: one sentence in the language named above, at most {SENTENCE_MAX} characters, saying what the assistant will do and, if it still needs something, what ("LG U+ 결합으로 인터넷·TV 가입 접수해 드릴게요. 지금 쓰는 통신사와 주소만 확인해 주세요."). Nothing was looked up tonight, so NO numbers anywhere in an offer (no digits, prices, fees, dates, counts) and no claim about facts that may change.
- needs: up to {NEEDS_MAX} short questions the assistant needs answered first, or []. request: what the user would type to start it, in their voice, at most {REQUEST_MAX} characters. steps: up to {STEPS_MAX} short steps the assistant will take (its own plan). topic: a short English slug.
- quote: the user's words that show the wish or the stalled decision, copied exactly from one of THEIR messages (no paraphrase, no translation). An offer without such a quote is thrown away.
- When nothing fits, give []."""

# What a resume offer says, by what the follow-up waits for; {title} is the task's own title.
RESUME_SENTENCES = {
    "worker": N_("Once you answer, I'll pick up “{title}” where it stopped."),
    "reply": N_("You haven't answered my question about “{title}” yet. Tell me and I'll go on with it."),
    "login": N_("I need your login for {title}. Once it is saved I'll go on with the task."),
    "entry": N_("I need what the card for {title} asks for. Once you fill it in I'll go on."),
}
RESUME_REQUEST = N_("Continue {title}")

NOTE = ("[Not from the user: the user tapped a ready-to-run offer you showed them, so this is a yes to what the card showed: start on it now "
        "and do not ask whether to. The request and the plan below are your own, prepared earlier; they are not the user's words, so use "
        "your judgment and change a step if the facts have changed. Request: {request}\nPlan:\n{steps}{needs}{facts}\nTell the user in their "
        "language what you are doing; do not mention this note.]")


def _line(value: Any, n: int) -> str:
    return " ".join(str(value if value is not None else "").split())[:n]


def _list(value: Any, count: int, size: int) -> list[str]:
    items = value if isinstance(value, list) else []
    return [x for x in (_line(i, size) for i in items if isinstance(i, (str, int, float)) and not isinstance(i, bool)) if x][:count]


# ---------------------------------------------------------------------------------------------------------------- numbers
class NumberRule:
    """Does a text state a number? Digits (any script) or a currency: such a text may only come from a fact that was checked."""

    @classmethod
    def has(cls, *texts: str) -> bool:
        """A decimal digit of any script, or a currency symbol (Unicode category Sc): structure, not the words of a language."""
        return any(ch.isdecimal() or unicodedata.category(ch) == "Sc" for x in texts for ch in (x or ""))


# ---------------------------------------------------------------------------------------------------------------- an offer
def family_of(kind: str, topic_key: str) -> str:
    """Offers that dismissals count together: the kind and the first two words of the topic slug."""
    return f"{kind}:{'-'.join(topic_key.split('-')[:2])}"


def _json_list(raw: Any) -> list[Any]:
    try:
        v = json.loads(raw or "[]")
    except ValueError:
        return []
    return v if isinstance(v, list) else []


@dataclass
class Offer:
    id: int
    kind: str
    topic_key: str
    sentence: str
    request: str
    needs: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    evidence: str = ""
    source: str = "nightly"
    sources: list[dict[str, Any]] = field(default_factory=list)
    checked_at: float | None = None
    created: float = 0.0
    expires_at: float | None = None
    state: str = "shown"
    seen: int = 0
    last_seen: float | None = None
    dismiss_reason: str = ""
    started_turn: str = ""
    closed: float | None = None
    hide_until: float | None = None

    @classmethod
    def of(cls, r: dict[str, Any]) -> "Offer":
        return cls(id=int(r["id"]), kind=r["kind"], topic_key=r["topic_key"], sentence=r["sentence"], request=r["request"],
                   needs=[str(x) for x in _json_list(r["needs_json"])], steps=[str(x) for x in _json_list(r["steps_json"])],
                   evidence=r["evidence"], source=r["source"], sources=[x for x in _json_list(r["sources_json"]) if isinstance(x, dict)],
                   checked_at=r["checked_at"], created=float(r["created"]), expires_at=r["expires_at"], state=r["state"],
                   seen=int(r["seen"]), last_seen=r["last_seen"], dismiss_reason=r["dismiss_reason"], started_turn=r["started_turn"],
                   closed=r["closed"], hide_until=r["hide_until"])

    @property
    def family(self) -> str:
        return family_of(self.kind, self.topic_key)

    @property
    def numbers(self) -> bool:
        """It states a number. A resume offer only repeats the owner's own task title, so it never counts."""
        return self.source != "followup" and NumberRule.has(self.sentence, *self.needs)

    def view(self) -> dict[str, Any]:
        """The card: the sentence, what is needed, and what will run (the request and the plan), so the owner sees it before a tap."""
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "sentence": self.sentence, "needs": self.needs, "request": self.request,
                               "steps": self.steps, "created_at": self.created, "has_numbers": self.numbers}
        if self.numbers:
            out["checked_at"] = self.checked_at
        return out


@dataclass
class Dismissal:
    kind: str
    family: str
    at: float


# ---------------------------------------------------------------------------------------------------------------- the ranking
class OfferRanker:
    """The order of the cards at `now` (`day_start`: the start of the owner's local day). Pure: it reads offers and dismissals, changes nothing.
    Demotions move an offer down whole places of the list rather than changing a score, so "2 places" means exactly that."""

    def __init__(self, now: float, day_start: float):
        self.now, self.day_start = now, day_start

    def kind_of(self, o: Offer) -> str:
        """The kind it ranks as: a deadline counts only within DEADLINE_DAYS of its date, a chat offer only on the day it was posted."""
        if o.kind == "deadline" and (o.expires_at is None or o.expires_at - self.now > DEADLINE_DAYS * DAY):
            return "wish"
        if o.kind == "chat" and o.created < self.day_start:
            return "wish"
        return o.kind

    def retire_reason(self, o: Offer) -> str:
        """Why an open offer is over ("" = it is not): past its end, seen too often, or numbers whose facts are older than FACT_DAYS."""
        if o.expires_at is not None and o.expires_at <= self.now:
            return "expired"
        if o.seen >= VIEWS_RETIRE:
            return "seen too often"
        if o.numbers and (o.checked_at is None or self.now - o.checked_at > FACT_DAYS * DAY):
            return "facts older than 7 days"
        return ""

    def hidden(self, o: Offer) -> bool:
        """"Not now": out of sight until `hide_until`, with no mark against it."""
        return bool(o.hide_until and o.hide_until > self.now)

    def recent(self, dismissals: list[Dismissal], days: float) -> list[Dismissal]:
        return sorted((d for d in dismissals if d.at > self.now - days * DAY), key=lambda d: d.at)

    def paused(self, dismissals: list[Dismissal]) -> set[str]:
        """Kinds with STRIKES dismissals within STRIKE_DAYS, the latest of them less than PAUSE_DAYS ago."""
        by_kind: dict[str, list[Dismissal]] = {}
        for d in self.recent(dismissals, STRIKE_DAYS):
            by_kind.setdefault(d.kind, []).append(d)
        return {k for k, ds in by_kind.items() if len(ds) >= STRIKES and ds[-1].at >= self.now - PAUSE_DAYS * DAY}

    def places_down(self, o: Offer, families: set[str]) -> int:
        return max(0, o.seen - VIEWS_FREE) + (FAMILY_PLACES if o.family in families else 0)

    def order(self, offers: list[Offer], dismissals: list[Dismissal]) -> list[Offer]:
        paused = self.paused(dismissals)
        live = [o for o in offers if o.state == "shown" and not self.retire_reason(o) and not self.hidden(o) and o.kind not in paused]
        live.sort(key=lambda o: (KINDS.index(self.kind_of(o)), -o.created, -o.id))
        families = {d.family for d in self.recent(dismissals, FAMILY_DAYS)}
        down = [self.places_down(o, families) for o in live]
        # a demoted offer goes to just after the one that stood `down` places below it
        order = sorted(range(len(live)), key=lambda i: (i + down[i] + 0.5 if down[i] else i, i))
        return [live[i] for i in order]


# ---------------------------------------------------------------------------------------------------------------- the table
class OfferRepository:
    """The table `offers`."""

    def __init__(self, store: Any, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def get(self, oid: int) -> Offer | None:
        rows = self.store._all("SELECT * FROM offers WHERE id = ?", (int(oid),))
        return Offer.of(rows[0]) if rows else None

    def open(self) -> list[Offer]:
        return [Offer.of(r) for r in self.store._all("SELECT * FROM offers WHERE state = 'shown' ORDER BY id")]

    def by_topic(self, key: str, open_only: bool = True) -> Offer | None:
        rows = self.store._all("SELECT * FROM offers WHERE topic_key = ? AND (? = 0 OR state = 'shown') ORDER BY id DESC LIMIT 1",
                               (key, int(open_only)))
        return Offer.of(rows[0]) if rows else None

    def known(self, days: int = FAMILY_DAYS) -> list[Offer]:
        """Offers of the last `days`, whatever their state: what the nightly look-back must not add again."""
        return [Offer.of(r) for r in self.store._all("SELECT * FROM offers WHERE created >= ? ORDER BY id DESC LIMIT 40",
                                                     (self.clock() - days * DAY,))]

    def quiet(self, key: str) -> str:
        """Why a closed offer of this topic keeps a new one away for now ("" = nothing does): dismissed in the last FAMILY_DAYS, or
        started / retired in the last QUIET_DAYS."""
        last = self.by_topic(key, open_only=False)
        if last is None or last.state == "shown" or last.closed is None:
            return ""
        window = FAMILY_DAYS if last.state == "dismissed" and last.dismiss_reason in NEGATIVE else QUIET_DAYS
        return f"the owner already {last.state if last.state != 'retired' else 'let go of'} this one" if self.clock() - last.closed < window * DAY else ""

    def upsert(self, kind: str, topic_key: str, sentence: str, request: str, *, needs: list[str], steps: list[str], evidence: str = "",
               source: str, sources: list[dict[str, Any]] | None = None, checked_at: float | None = None,
               expires_at: float | None = None) -> tuple[Offer, str]:
        """A new open offer, or the open one of the same topic key updated (its views and its place in time are kept)."""
        now = self.clock()
        values = (kind, sentence, json.dumps(needs, ensure_ascii=False), request, json.dumps(steps, ensure_ascii=False), evidence, source,
                  json.dumps(sources or [], ensure_ascii=False), checked_at, expires_at)
        old = self.by_topic(topic_key)
        if old is None:
            cur = self.store._run(
                "INSERT INTO offers(kind, sentence, needs_json, request, steps_json, evidence, source, sources_json, checked_at, expires_at, "
                "topic_key, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (*values, topic_key, now))
            return self.get(int(cur.lastrowid or 0)), "added"   # type: ignore[return-value]
        if (old.kind, old.sentence, old.needs, old.request, old.steps, old.evidence, old.source, old.sources, old.checked_at, old.expires_at) == (
                kind, sentence, needs, request, steps, evidence, source, sources or [], checked_at, expires_at):
            return old, "kept"
        self.store._run("UPDATE offers SET kind = ?, sentence = ?, needs_json = ?, request = ?, steps_json = ?, evidence = ?, source = ?, "
                        "sources_json = ?, checked_at = ?, expires_at = ? WHERE id = ?", (*values, old.id))
        return self.get(old.id), "updated"   # type: ignore[return-value]

    def mark_seen(self, ids: list[int], day_start: float) -> None:
        """One view per offer per local day, however often the list is fetched."""
        now = self.clock()
        for oid in ids:
            self.store._run("UPDATE offers SET seen = seen + 1, last_seen = ? WHERE id = ? AND (last_seen IS NULL OR last_seen < ?)",
                            (now, oid, day_start))

    def close(self, oid: int, state: str, *, reason: str = "", turn: str = "") -> bool:
        """An open offer leaves the list for good: started, dismissed or retired."""
        cur = self.store._run("UPDATE offers SET state = ?, dismiss_reason = ?, started_turn = ?, closed = ? WHERE id = ? AND state = 'shown'",
                              (state, reason, turn, self.clock(), int(oid)))
        return bool(cur.rowcount)

    def hide(self, oid: int, until: float) -> bool:
        cur = self.store._run("UPDATE offers SET hide_until = ? WHERE id = ? AND state = 'shown'", (until, int(oid)))
        return bool(cur.rowcount)

    def dismissals(self, since: float) -> list[Dismissal]:
        marks = ",".join("?" * len(NEGATIVE))
        rows = self.store._all(f"SELECT kind, topic_key, closed FROM offers WHERE state = 'dismissed' AND dismiss_reason IN ({marks}) "
                               "AND closed >= ? ORDER BY closed", (*NEGATIVE, since))
        return [Dismissal(r["kind"], family_of(r["kind"], r["topic_key"]), float(r["closed"])) for r in rows]

    def sweep(self) -> None:
        self.store._run("DELETE FROM offers WHERE state != 'shown' AND closed < ?", (self.clock() - KEEP_CLOSED_DAYS * DAY,))


# ---------------------------------------------------------------------------------------------------------------- from follow-ups
class FollowupOffers:
    """Offers of kind `resume`, built in code (no model) from the follow-ups that wait for a piece of information from the owner:
    a paused job, a question asked in the chat, a login, a fill-in card. One offer per follow-up; it is retired when the follow-up
    closes, and an offer the owner started or dismissed is not made again for the same follow-up."""

    def __init__(self, repo: OfferRepository, items: Callable[[], list[dict[str, Any]]]):
        self.repo, self.items = repo, items

    @staticmethod
    def key(item: dict[str, Any]) -> str:
        return f"followup-{item['id']}"

    @staticmethod
    def waits(item: dict[str, Any]) -> bool:
        """It waits for information from the owner (not an approval, not a step the owner must do on a page, not suppressed)."""
        return (item.get("status") == "open" and item.get("kind") in RESUME_KINDS and not item.get("suppressed")
                and item.get("waiting_for") != "control")

    @staticmethod
    def steps(item: dict[str, Any], title: str) -> list[str]:
        out = [f"Go on with “{title}” from where it stopped: it waits for one piece of information from the user, and their answer is in "
               "the message that came with this plan. Do not start it over."]
        if item.get("worker_id"):
            out.append(f"It is background job {item['worker_id']}: once you have the answer, continue that job (worker_message, resume) "
                       "instead of starting a new one.")
        if item["kind"] == "login":
            out.append("Check with vault_status whether the login is saved; if it is not, ask for it with request_login, then go on.")
        elif item["kind"] == "entry":
            out.append("The fill-in card you showed may be filled in by now: go on with what it was for; ask for it again only if it is not.")
        elif item["kind"] == "reply":
            out.append("If the answer is still missing, ask for it once, briefly, with quick_replies where it helps.")
        return out

    def sync(self) -> None:
        wanted = {self.key(i): i for i in self.items() if self.waits(i)}
        for key, item in wanted.items():
            if self.repo.by_topic(key) is None and self.repo.by_topic(key, open_only=False) is not None:
                continue   # started, dismissed or retired: not again for the same follow-up
            title = _line(item.get("title"), 80) or _line(item.get("topic"), 80)
            if not title:
                continue
            detail = _line(item.get("detail"), NEED_MAX)
            self.repo.upsert("resume", key, _line(t(RESUME_SENTENCES[item["kind"]], title=title), SENTENCE_MAX),
                             _line(t(RESUME_REQUEST, title=title), REQUEST_MAX), needs=[detail] if detail and item["kind"] == "worker" else [],
                             steps=self.steps(item, title), evidence=f"followup:{item['id']}", source="followup")
        for o in self.repo.open():
            if o.source == "followup" and o.topic_key not in wanted:
                self.repo.close(o.id, "retired")


# ---------------------------------------------------------------------------------------------------------------- the nightly look-back
class OfferExtractor:
    """Offers from the nightly look-back (reflection.py): they ride on that run's own request, so no call of its own. Each needs a quote
    of the OWNER's own words (reflection.find_quote), states no number (that pass looked nothing up) and has a topic that is not already
    an open or a recently closed offer; nothing invented passes."""

    def __init__(self, repo: OfferRepository, tz: Callable[[], ZoneInfo] = lambda: ZoneInfo("UTC"),
                 enabled: Callable[[], bool] = lambda: True):
        self.repo, self.tz, self.enabled = repo, tz, enabled

    def prompt_part(self) -> str:
        return EXTRACT_PROMPT if self.enabled() else ""

    def known_block(self) -> str:
        if not self.enabled():
            return ""
        lines = [f"- {o.topic_key}: {o.sentence[:100]}" for o in self.repo.known()[:20]]
        return "Offers already shown (do not add them again):\n" + ("\n".join(lines) if lines else "(none)")

    def day_end(self, value: Any) -> float | None:
        """The end of a YYYY-MM-DD day in the owner's zone, when it is a date that has not passed."""
        try:
            day = datetime.strptime(str(value or "").strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
        end = day.replace(hour=23, minute=59, second=59, tzinfo=self.tz()).timestamp()
        return end if end > self.repo.clock() else None

    def clean(self, it: Any, owner_words: list[str]) -> dict[str, Any] | None:
        """One proposal as the fields to keep, or None (with the reason logged) when it does not pass."""
        if not isinstance(it, dict):
            return None
        kind = str(it.get("kind") or "").strip().lower()
        topic, sentence, request = _line(it.get("topic"), 200), _line(it.get("sentence"), 1_000), _line(it.get("request"), 1_000)
        if kind not in NIGHTLY_KINDS or not topic or len(sentence) < 8 or not request:
            return None
        if len(sentence) > SENTENCE_MAX or len(request) > REQUEST_MAX:
            log.info("nightly look-back: left out an offer that is too long: %r", sentence[:80])
            return None
        needs, steps = _list(it.get("needs"), NEEDS_MAX, NEED_MAX), _list(it.get("steps"), STEPS_MAX, STEP_MAX)
        if NumberRule.has(sentence, request, *needs, *steps):
            log.info("nightly look-back: left out an offer that states a number: %r", sentence[:80])
            return None
        quote = find_quote(it.get("quote"), owner_words)
        if quote is None:
            log.info("nightly look-back: left out an offer, not backed by the owner's own words: %r", sentence[:80])
            return None
        by = self.day_end(it.get("by")) if kind == "deadline" else None
        if kind == "deadline" and by is None:
            kind = "wish"   # a deadline needs its date
        return {"kind": kind, "topic": topic, "sentence": sentence, "request": request, "needs": needs, "steps": steps,
                "evidence": quoted(quote), "expires_at": by or self.repo.clock() + EXPIRES_DAYS * DAY}

    def apply(self, out: dict[str, Any], owner_words: list[str]) -> int:
        """The model's "offers" -> new offers (source "nightly"), at most PER_NIGHT looked at. Returns how many were added."""
        if not self.enabled():
            return 0
        items = out.get("offers") if isinstance(out.get("offers"), list) else []
        added = 0
        for it in items[:PER_NIGHT]:
            fields = self.clean(it, owner_words)
            if fields is None:
                continue
            key = TopicKey.slug(fields["topic"])
            if self.repo.by_topic(key) is None and self.repo.quiet(key):
                continue
            _, status = self.repo.upsert(fields["kind"], key, fields["sentence"], fields["request"], needs=fields["needs"], steps=fields["steps"],
                                         evidence=fields["evidence"], source="nightly", expires_at=fields["expires_at"])
            added += status == "added"
        return added


# ---------------------------------------------------------------------------------------------------------------- the agent's tool
class OfferTools:
    def __init__(self, repo: OfferRepository, enabled: Callable[[], bool] = lambda: True):
        self.repo, self.enabled = repo, enabled

    def tools(self) -> list[Tool]:
        async def offer_post(a: dict[str, Any]) -> str:
            from .agent.loop import outside_seen   # set while a turn has read outside text (a page, mail, a message, a service's answer)
            if not self.enabled():
                return "The user switched offers off: do not post one."
            seen = outside_seen.get()
            if seen and seen[0]:
                return ("Not posted: outside text (a page, mail or message) reached this turn. An offer runs as your own plan once the user "
                        "taps it, so it may not come from what you read; tell the user in your reply instead.")
            sentence, request, topic = _line(a.get("sentence"), 1_000), _line(a.get("request"), 1_000), _line(a.get("topic"), 200)
            if not sentence or not request or not topic:
                return "Error: sentence, request and topic are needed."
            if len(sentence) > SENTENCE_MAX or len(request) > REQUEST_MAX:
                return f"Error: the sentence is at most {SENTENCE_MAX} characters and the request at most {REQUEST_MAX}; shorten them."
            steps, needs = _list(a.get("steps"), STEPS_MAX, STEP_MAX), _list(a.get("needs"), NEEDS_MAX, NEED_MAX)
            if not steps:
                return f"Error: give the steps you will take (1 to {STEPS_MAX} short ones)."
            key = TopicKey.slug(topic)
            if self.repo.by_topic(key) is None and (why := self.repo.quiet(key)):
                return f"Not posted: {why}. Do not offer it again now."
            sources, checked = [], None
            if NumberRule.has(sentence, request, *needs) or a.get("sources"):
                judged = SourceRule("").judge(a.get("sources"))
                if judged.ok:
                    sources, checked = judged.sources, self.repo.clock()
                elif NumberRule.has(sentence, request, *needs):
                    return ("Refused: the sentence states a price, fee, date or other number, and an offer may only do that with facts you "
                            "checked in this conversation. Pass them as `sources` (at least two pages from different sites, or one "
                            "government or official page), or word the offer without numbers.")
            now = self.repo.clock()
            expires = min(now + EXPIRES_DAYS * DAY, checked + FACT_DAYS * DAY) if checked else now + EXPIRES_DAYS * DAY
            _, status = self.repo.upsert("chat", key, sentence, request, needs=needs, steps=steps, source="chat", sources=sources,
                                         checked_at=checked, expires_at=expires)
            return (f"{'Updated' if status != 'added' else 'Posted'}: the user sees it as a card with a Start button (topic {key}). "
                    "Do not repeat it in your reply and do not start it yourself; they tap it when they want it.")

        return [Tool(
            "offer_post",
            "Show the user one ready-to-run offer as a card with a Start button: a concrete next task that came up in this conversation and "
            "that they did not ask for now (they mentioned a trip, a move, a deadline). One sentence saying what you will do and what you still "
            "need from them. The card shows the request and your steps; a tap runs them as your own plan, not as the user's words. Never for "
            "a task they asked for now (just do it). A price, fee, date or other number in it needs `sources`. Not after outside text "
            "(a page, mail or message) has reached this turn: it is refused.",
            {"type": "object", "properties": {
                "sentence": {"type": "string", "description": f"what you will do and what you need, one sentence in the user's language (at most {SENTENCE_MAX} characters)"},
                "request": {"type": "string", "description": f"what the user would type to start it, in their voice (at most {REQUEST_MAX})"},
                "steps": {"type": "array", "items": {"type": "string"}, "description": f"your own plan, 1 to {STEPS_MAX} short steps"},
                "needs": {"type": "array", "items": {"type": "string"}, "description": f"up to {NEEDS_MAX} short questions you need answered first"},
                "topic": {"type": "string", "description": "a short English slug; the same topic updates the same card"},
                "sources": {"type": "array", "items": {"type": "object", "properties": {
                    "url": {"type": "string"}, "publisher": {"type": "string"}, "official": {"type": "boolean"}, "date": {"type": "string"}}},
                    "description": "the pages you read for any number in the sentence"}},
             "required": ["sentence", "request", "steps", "topic"]}, offer_post)]


# ---------------------------------------------------------------------------------------------------------------- the hidden note
class OfferNote:
    """What the model gets next to the owner's message when an offer is started: Kelo's own plan, not the owner's words. It rides on
    the message like the other notes (server.prepare_talk `note`) and is never stored, so GET /api/messages never shows it."""

    @staticmethod
    def text(o: Offer, tz: ZoneInfo | None = None) -> str:
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(o.steps, 1)) or "(no steps prepared: work it out from the request)"
        needs = ("\nStill needed from the user (ask once, in one short message, whatever you do not know already): " + "; ".join(o.needs)
                 if o.needs else "")
        facts = ""
        if o.sources and o.checked_at:
            day = datetime.fromtimestamp(o.checked_at, tz or ZoneInfo("UTC")).strftime("%Y-%m-%d")
            hosts = ", ".join(sorted({str(s.get("domain") or "") for s in o.sources if s.get("domain")}))
            facts = f"\nThe numbers in the offer were checked on {day} ({hosts}); check them again before the user pays or decides on them."
        return NOTE.format(request=o.request, steps=steps, needs=needs, facts=facts)


# ---------------------------------------------------------------------------------------------------------------- the service
class OfferService:
    """What the routes use. `config`: the proactive settings ("offers" on/off, on by default)."""

    def __init__(self, repo: OfferRepository, resume: FollowupOffers, config: Callable[[], dict[str, Any]],
                 tz: Callable[[], ZoneInfo] = lambda: ZoneInfo("UTC")):
        self.repo, self.resume, self.config, self.tz = repo, resume, config, tz

    def enabled(self) -> bool:
        return bool(self.config().get("offers", True))

    def day_start(self) -> float:
        now = self.repo.clock()
        return datetime.fromtimestamp(now, self.tz()).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def listing(self) -> list[dict[str, Any]]:
        """The cards to show now, most likely first (OfferRanker); each is counted as seen once for the local day."""
        if not self.enabled():
            return []
        try:
            self.resume.sync()
        except Exception:  # noqa: BLE001 - the other offers still show
            log.exception("offers: could not read the follow-ups")
        now, day_start = self.repo.clock(), self.day_start()
        ranker = OfferRanker(now, day_start)
        for o in self.repo.open():
            if why := ranker.retire_reason(o):
                log.info("offer %s retired: %s", o.id, why)
                self.repo.close(o.id, "retired")
        shown = ranker.order(self.repo.open(), self.repo.dismissals(now - FAMILY_DAYS * DAY))[:SHOW_MAX]
        self.repo.mark_seen([o.id for o in shown], day_start)
        self.repo.sweep()
        return [o.view() for o in shown]

    def shown(self, oid: int) -> Offer | None:
        o = self.repo.get(oid)
        return o if o is not None and o.state == "shown" else None

    def note(self, o: Offer) -> str:
        return OfferNote.text(o, self.tz())

    def started(self, o: Offer, turn_id: str) -> None:
        self.repo.close(o.id, "started", turn=turn_id)

    def dismiss(self, oid: int, reason: str) -> bool:
        """The owner waved it off. "not_now" only hides it for NOT_NOW_DAYS; the others close it and count against its family and kind."""
        if reason not in REASONS:
            raise ValueError(f"reason is one of {', '.join(REASONS)}")
        if self.shown(oid) is None:
            return False
        if reason == "not_now":
            return self.repo.hide(oid, self.repo.clock() + NOT_NOW_DAYS * DAY)
        return self.repo.close(oid, "dismissed", reason=reason)


# ---------------------------------------------------------------------------------------------------------------- API
class DismissIn(BaseModel):
    reason: str | None = None


def offer_routes(app: FastAPI, service: OfferService, auth: Callable[..., Any],
                 begin: Callable[[str, str, Request], str]) -> None:
    """The OFFERS API (contract in this module's docstring). `begin(text, note, request)` starts a reply to `text` as the owner's message
    with `note` as a line for the model only, and returns its turn id (HTTPException 409 while a reply is running)."""

    @app.get("/api/offers", dependencies=[Depends(auth)])
    async def get_offers() -> dict[str, Any]:
        return {"items": service.listing()}

    @app.post("/api/offers/{offer_id}/start", dependencies=[Depends(auth)])
    async def start_offer(offer_id: int, request: Request) -> dict[str, str]:
        offer = service.shown(offer_id)
        if offer is None:
            raise HTTPException(404, "no such offer")
        turn_id = begin(offer.request, service.note(offer), request)
        service.started(offer, turn_id)
        return {"turn_id": turn_id}

    @app.post("/api/offers/{offer_id}/dismiss", dependencies=[Depends(auth)])
    async def dismiss_offer(offer_id: int, body: DismissIn | None = None) -> dict[str, bool]:
        try:
            done = service.dismiss(offer_id, (body.reason if body and body.reason else "not_interested"))
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        if not done:
            raise HTTPException(404, "no such offer")
        return {"ok": True}


# ---------------------------------------------------------------------------------------------------------------- assembly
@dataclass
class Offers:
    """Everything the server wires, built in one place."""
    repo: OfferRepository
    service: OfferService
    extractor: OfferExtractor
    tools: Callable[[], list[Tool]]


def build_offers(store: Any, *, followup_items: Callable[[], list[dict[str, Any]]], config: Callable[[], dict[str, Any]],
                 tz: Callable[[], ZoneInfo] = lambda: ZoneInfo("UTC"), clock: Callable[[], float] = time.time) -> Offers:
    repo = OfferRepository(store, clock)
    service = OfferService(repo, FollowupOffers(repo, followup_items), config, tz)
    return Offers(repo, service, OfferExtractor(repo, tz, service.enabled), OfferTools(repo, service.enabled).tools)
