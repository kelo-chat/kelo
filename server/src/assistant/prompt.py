"""Builds the system prompt: Kelo's written rules (server/content/prompts/system.md, maintained by hand) plus what changes every turn."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import i18n
from .home import Home, Skills

# The app's languages, named in English for the model (the code is what the app sends in Accept-Language, see i18n.pick).
LANGUAGE_NAMES = {
    "en": "English", "ko": "Korean", "ja": "Japanese", "zh-Hans": "Simplified Chinese", "zh-Hant": "Traditional Chinese",
    "es": "Spanish", "fr": "French", "de": "German", "pt-BR": "Brazilian Portuguese", "it": "Italian", "ru": "Russian",
    "id": "Indonesian", "vi": "Vietnamese", "th": "Thai", "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "pl": "Polish",
    "nl": "Dutch", "uk": "Ukrainian",
}


def language_line(lang: str | None = None) -> str:
    """The language the user's app is set to: what the model writes to the user in, unless the user writes in another one. It also
    covers the short messages between tool steps: tool results, pages and Kelo's own notes are mostly English, and a model mirrors
    them mid-turn (seen 2026-11-10: English updates to a Korean user before any note was involved)."""
    code = lang or i18n.current()
    name = LANGUAGE_NAMES.get(code, code)
    return (f"The user's language: {name}. Write every message in it, also between steps and when pages, mails or tools use "
            "another; switch only if the user writes in or asks for another.")


# How Kelo talks to the user (profile "tone"): the owner picks it in the app, or asks in the chat (the set_tone tool).
TONES = ("friendly", "polite")
DEFAULT_TONE = "polite"
TONE_WAYS = {
    "friendly": "casually, like a close friend (the informal form where a language has one, e.g. Korean 반말)",
    "polite": "courteously and warmly (the polite form where a language has one, e.g. Korean 해요체)",
}


def tone_of(value: str | None) -> str:
    """A stored tone, or the default for an empty or unknown one."""
    return value if value in TONES else DEFAULT_TONE


def tone_line(tone: str | None, *, switch: bool = True) -> str:
    """One line on how to address the user. Only the user: what is written to others for them follows the context and their words.
    `switch` adds how the user changes it (the chat's prompt; not for one-off texts such as a reminder)."""
    return (f"Talk to the user {TONE_WAYS[tone_of(tone)]}. Not for what you write to others for them: that follows the context and "
            "their instructions. Honest in any tone: no flattery; say plainly when a plan has a flaw."
            + (" They switch it in the app or by asking you (set_tone)." if switch else ""))


def valid_timezone(name: str | None) -> str | None:
    if not name:
        return None
    try:
        ZoneInfo(name)
        return name
    except Exception:  # noqa: BLE001 - unknown/odd names must never break a chat turn
        return None


def now_line(tz_name: str | None, now: datetime | None = None) -> str:
    """`Now: 2026-11-07T15:20+09:00 (Asia/Seoul, Saturday)`: the user's local time, the one clock the model trusts."""
    tz_name = valid_timezone(tz_name) or "UTC"
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(tz_name))
    offset = local.strftime("%z")
    return f"Now: {local.strftime('%Y-%m-%dT%H:%M')}{offset[:3]}:{offset[3:]} ({tz_name}, {local.strftime('%A')})"


def calendar(tz_name: str | None, now: datetime | None = None, days: int = 21) -> str:
    """Weekdays for the next few weeks, so the model never has to work one out."""
    tz = ZoneInfo(valid_timezone(tz_name) or "UTC")
    start = (now or datetime.now(UTC)).astimezone(tz)
    return ", ".join(f"{(start + timedelta(days=i)).strftime('%a %m-%d')}" + (" (today)" if i == 0 else "") for i in range(days))


SCHEDULE_LINES = 5


def when_text(ts: float, tz_name: str | None, now: datetime | None = None) -> str:
    """A run time as the user reads it, in their zone: "today 07:30", "tomorrow 09:00", "Mon 11-10 18:00" (year added past 300 days)."""
    tz = ZoneInfo(valid_timezone(tz_name) or "UTC")
    local = datetime.fromtimestamp(ts, tz)
    today = (now or datetime.now(UTC)).astimezone(tz).date()
    days = (local.date() - today).days
    hm = local.strftime("%H:%M")
    if days == 0:
        return f"today {hm}"
    if days == 1:
        return f"tomorrow {hm}"
    return local.strftime("%a %Y-%m-%d " if days > 300 else "%a %m-%d ") + hm


def schedules_line(rows: list[dict[str, Any]] | None, tz_name: str | None, now: datetime | None = None) -> str:
    """The next few scheduled jobs (Schedules.list() rows) in one line, soonest first, and how to see or change them. The app's own
    edits show up here on the next turn, so the agent never works from an old list."""
    if rows is None:
        return ""
    upcoming = sorted((r for r in rows if r.get("enabled") and r.get("next_run_ts")), key=lambda r: r["next_run_ts"])
    tools = "the schedule_* tools list, change and add them"
    if not upcoming:
        off = len(rows)
        return (f"Scheduled jobs: none coming up ({off} paused or off)" if off else "Scheduled jobs: none saved") + f"; {tools}."
    shown = "; ".join(f"#{r['id']} {str(r.get('name') or '')[:60]} — {when_text(r['next_run_ts'], r.get('timezone') or tz_name, now)}"
                      for r in upcoming[:SCHEDULE_LINES])
    more = len(rows) - min(len(upcoming), SCHEDULE_LINES)
    return (f"Next scheduled jobs (user's local time): {shown}" + (f" (+{more} more)" if more else "") + f". {tools}.")


def watchers_line(rows: list[dict[str, Any]] | None) -> str:
    """The watchers (Watchers.list() rows) in one line: the ones on, and how many are off. Their page-derived values and addresses
    never go here (the page can say anything); watch_list shows them with their state."""
    if not rows:
        return ""
    on = [r for r in rows if r.get("enabled")]
    off = len(rows) - len(on)
    tools = "watch_list shows them all, watch_update changes one."
    if not on:
        return f"Watchers: none on ({off} off); {tools}"
    shown = "; ".join(f"#{r['id']} {str(r.get('name') or '')[:60]} (on, every {r.get('every_minutes')} min)" for r in on[:5])
    more = len(on) - min(len(on), 5)
    return f"Watchers: {shown}" + (f" (+{more} more on)" if more else "") + (f" (+{off} off)" if off else "") + f"; {tools}"


JOB_LINES = 5


def jobs_line(rows: list[dict[str, Any]] | None) -> str:
    """The background jobs waiting for the owner (Workers.public rows, status needs_input or blocked), so a turn about one of them settles
    it; "" when there are none."""
    rows = [r for r in rows or [] if r.get("status") in ("needs_input", "blocked")]
    if not rows:
        return ""
    shown = "; ".join(f"{r['id']} \"{str(r.get('title') or '')[:60]}\" ({r['status']}"
                      + (f", question {r['question_id']}" if r.get("question_id") else "") + ")" for r in rows[:JOB_LINES])
    more = len(rows) - min(len(rows), JOB_LINES)
    return ("Jobs waiting for the owner: " + shown + (f" (+{more} more)" if more else "") + ". When the owner's message is about one of "
            "them, settle it before you reply: worker_message with their words (answer_to = its question id) to let it go on or change "
            "it, or worker_stop when they let it go. A reply or a reaction alone leaves it waiting.")


def turn_lines(*, tz_name: str | None, now: datetime | None = None, agent_name: str = "", user_name: str = "",
               goals: list[dict[str, Any]] | None = None, provider: str = "", model: str = "",
               schedules: list[dict[str, Any]] | None = None, tone: str = "", limits: str = "", first_chat: str = "",
               follow: list[dict[str, Any]] | None = None, watchers: list[dict[str, Any]] | None = None,
               jobs: list[dict[str, Any]] | None = None) -> list[str]:
    """What changes every turn, as lines: the time, the calendar, the model, names, how to talk to the user, the next scheduled jobs
    and the goals being tracked. `first_chat`: the first-conversation block (first_chat.py), only while that conversation is open."""
    out = [now_line(tz_name, now),
           f"Calendar (the user's local dates for the next 3 weeks; take weekdays from here, never work one out): {calendar(tz_name, now)}",
           language_line()]
    if provider or model:
        out.append(f"Model in use: {provider or '?'} / {model or '?'}")
    if agent_name:
        out.append(f"Your name: {agent_name}")
    if user_name:
        out.append(f"The user's name: {user_name}")
    out.append(tone_line(tone))
    if limits:   # what the owner limited per connector in the app (permissions.py): only when something is off or set to ask
        out.append(limits)
    if line := schedules_line(schedules, tz_name, now):
        out.append(line)
    if line := watchers_line(watchers):
        out.append(line)
    if line := jobs_line(jobs):
        out.append(line)
    if line := follow_line(follow, tz_name, now):
        out.append(line)
    if block := goals_block(goals):
        out.append(block)
    if first_chat:
        out.append(first_chat)
    return out


GOAL_LINES, GOAL_CHARS = 8, 2_200
FOLLOW_LINES = 5


def follow_line(rows: list[dict[str, Any]] | None, tz_name: str | None, now: datetime | None = None) -> str:
    """The next few things Kelo keeps for the owner (follow items, soonest first) in one line, with the local date, the title and the
    source; "" when there are none. The list itself is the follow_list tool's: the line only tells the model that it is there."""
    if not rows:
        return ""
    tz = ZoneInfo(valid_timezone(tz_name) or "UTC")
    shown = []
    for r in rows[:FOLLOW_LINES]:
        local = datetime.fromtimestamp(float(r.get("starts_at") or 0), tz)
        day = local.strftime("%Y-%m-%d") if r.get("all_day") else local.strftime("%Y-%m-%d %H:%M")
        shown.append(f"{day} {str(r.get('title') or '')[:80]} ({r.get('source') or 'unknown'})")
    more = len(rows) - min(len(rows), FOLLOW_LINES)
    return ("Kept for the owner (next items, user's local time): " + "; ".join(shown) + (f" (+{more} more)" if more else "")
            + ". follow_list shows each one in full, with what Kelo does for it.")


def goal_line(g: dict[str, Any]) -> str:
    """One tracked goal for the prompt: number, title, plan progress, the next step and whose it is, the key facts."""
    bits = [f"#{g['id']} {g['title']}" if g.get("id") is not None else str(g.get("title") or "")]
    if g.get("note"):
        bits.append(f"note: {str(g['note'])[:160]}")
    plan = g.get("plan") or {}
    if plan.get("total"):
        bits.append(f"plan {plan.get('done', 0)}/{plan['total']} done")
        if g.get("next_step"):
            bits.append(f"next ({'you' if g.get('next_owner') == 'agent' else 'user'}): {str(g['next_step'])[:120]}")
    elif "plan" in g:
        bits.append("no plan yet")
    facts = g.get("facts") or []
    if facts:
        bits.append("facts: " + "; ".join(f"{f['key']}=" + ("(declined)" if f.get("skipped") else str(f.get("value", ""))[:60]) for f in facts))
    if g.get("asking"):
        bits.append("waiting for the user's answers to your questions")
    return "- " + " · ".join(bits)


def goals_block(goals: list[dict[str, Any]] | None) -> str:
    tracking = [g for g in (goals or []) if g.get("status") == "tracking"][:GOAL_LINES]
    lines, used = [], 0
    for g in tracking:
        line = goal_line(g)
        if used + len(line) > GOAL_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    from .goals import feature_name
    return (f'Goals the user is working on (the app calls them "{feature_name()}"; use that word with the user; read one in full with '
            "goal_get):\n" + "\n".join(lines) + "\n" + GOAL_RULES)


# How to work on goals: here, next to the goals themselves, so it is read only while there are some (the goal_* tools say what each
# one keeps).
GOAL_RULES = ("Treat each goal as a project you share: plan it with the user, each step owned by the user or by you, and keep its facts, "
              "plan, progress, numbers and files with the goal tools. When a goal waits for the user's answers, their next message most "
              "likely answers you: save it to that goal and go on. Do your own steps with your usual tools and say what you did; ask only "
              "what blocks you. A question the user declined stays unasked. Offer check-ins as a schedule with the goal's id, never "
              "silently.")


BUYING_HEAD = "**Buying, ordering, booking.**"


def _fragment(folder: Path, name: str, fallback: Path | None) -> str:
    """A prompt fragment next to the system prompt; a custom prompt file that has none beside it uses the shipped one."""
    try:
        return (folder / name).read_text(encoding="utf-8").strip()
    except OSError:
        if fallback is None or fallback == folder:
            raise
        return (fallback / name).read_text(encoding="utf-8").strip()


def buying_filled(text: str, folder: Path, pay_split: bool, browser_split: bool = False, fallback: Path | None = None) -> str:
    """The buying paragraph follows the switches: system.md holds the one for a chat that pays and clicks itself (both off). Payment
    switch on: buying-specialist.md (the chat prepares and hands the payment over). Browser switch on: the chat does not use websites
    itself, so `websites.md` (the delegate rule) comes before the buying paragraph, which is buying-job-specialist.md (payment switch
    on) or buying-job.md (off)."""
    start = text.find(BUYING_HEAD)
    if start < 0 or not (pay_split or browser_split):
        return text
    end = text.find("\n\n", start)
    if browser_split:
        websites = _fragment(folder, "websites.md", fallback)
        body = _fragment(folder, "buying-job-specialist.md" if pay_split else "buying-job.md", fallback)
        paragraph = f"{websites}\n\n{BUYING_HEAD} {body}"
    else:
        paragraph = f"{BUYING_HEAD} " + _fragment(folder, "buying-specialist.md", fallback)
    return text[:start] + paragraph + (text[end:] if end >= 0 else "")


class PromptBuilder:
    def __init__(self, base: str, home: Home | None, skills: Skills | None):
        self.base, self.home, self.skills = base, home, skills
        self.learned: Callable[[], str] = lambda: ""   # the learned preferences section (reflection.py), set by the server
        self.corrections: Callable[[], str] = lambda: ""   # the open corrections section (nightly_review.py), set by the server
        self.forgotten: Callable[[], str] = lambda: ""   # the forgotten-topics notice (forget.py Tombstones.notice), set by the server
        self.marker: Callable[[], str] = lambda: ""    # the private marker line (injection_watch.LeakMarker.line), set by the server

    @classmethod
    def load(cls, content_dir: str | Path, home: Home | None, skills: Skills | None, prompt_file: str | None = None,
             pay_split: bool = False, browser_split: bool = False) -> PromptBuilder | None:
        path = Path(prompt_file) if prompt_file else Path(content_dir) / "prompts" / "system.md"
        try:
            return cls(buying_filled(path.read_text(encoding="utf-8"), path.parent, pay_split, browser_split, Path(content_dir) / "prompts"), home, skills)
        except OSError:
            return None

    def build(self, *, agent_name: str = "", user_name: str = "", provider: str = "", model: str = "",
              goals: list[dict[str, Any]] | None = None, tz_name: str | None = None, now: datetime | None = None,
              connected: set[str] | None = None, schedules: list[dict[str, Any]] | None = None, tone: str = "",
              limits: str = "", first_chat: str = "", follow: list[dict[str, Any]] | None = None,
              watchers: list[dict[str, Any]] | None = None, jobs: list[dict[str, Any]] | None = None) -> str:
        """The whole prompt in one text: the stable part, then this turn's lines (see build_parts)."""
        stable, turn = self.build_parts(agent_name=agent_name, user_name=user_name, provider=provider, model=model, goals=goals,
                                        tz_name=tz_name, now=now, connected=connected, schedules=schedules, tone=tone,
                                        limits=limits, first_chat=first_chat, follow=follow, watchers=watchers, jobs=jobs)
        return f"{stable}\n\n{turn}"

    def build_parts(self, *, agent_name: str = "", user_name: str = "", provider: str = "", model: str = "",
                    goals: list[dict[str, Any]] | None = None, tz_name: str | None = None, now: datetime | None = None,
                    connected: set[str] | None = None, schedules: list[dict[str, Any]] | None = None,
                    tone: str = "", limits: str = "", first_chat: str = "",
                    follow: list[dict[str, Any]] | None = None, watchers: list[dict[str, Any]] | None = None,
                    jobs: list[dict[str, Any]] | None = None) -> tuple[str, str]:
        """(stable, turn). Stable, from what changes least to most: rules, skills, learned preferences (at most nightly), the user's
        files (when written). It is byte-for-byte the same from turn to turn until one of those changes, so a provider that caches
        the start of the request reuses it. Turn: "## This Turn" (the clock to the minute, calendar, model, names, how to talk to
        the user, schedules, goals), which changes every turn and so goes after everything stable (the server sends it next to the user's message)."""
        parts = [self.base.replace("{agent_name}", agent_name or "Kelo").rstrip()]
        if self.skills and (idx := self.skills.index_text(connected)):
            parts.append(idx)
        if learned := self.learned():
            parts.append(learned)
        if corrections := self.corrections():
            parts.append(corrections)
        if forgotten := self.forgotten():
            parts.append(forgotten)
        if self.home:
            files = self.home.context_block()
            if marker := self.marker():   # in the private part with the owner's files: the model is told never to write it anywhere
                files = f"{files}\n\n{marker}"
            parts.append(files)
        turn = "## This Turn\n" + "\n".join(turn_lines(tz_name=tz_name, now=now, agent_name=agent_name, user_name=user_name,
                                                         goals=goals, provider=provider, model=model, schedules=schedules,
                                                         tone=tone, limits=limits, first_chat=first_chat, follow=follow,
                                                         watchers=watchers, jobs=jobs))
        return "\n\n".join(parts), turn
