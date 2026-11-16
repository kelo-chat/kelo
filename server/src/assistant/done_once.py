"""A step that sends, changes or deletes is done once: checked first, recorded, and an unknown result is said and verified.

A mail that timed out may still have gone out; a model that simply tries again sends it twice (money, mail and messages going out
twice). So before such a step runs, the app looks in its own record of what it did; after it, it writes down what happened. There are
no idempotency keys: the record is Kelo's own, and the check before a retry is a look at the outside (the sent folder, a site's order
page), not a guess.

Small classes, one job each; the pure ones are tested alone:
  * ActionFingerprint  pure: `of(effect, tool, args, page=None)` is the sha256 of what a call would do outside (tool, kind, connector,
                       sorted targets, normalised text), so the same step worded differently (key order, spaces) is the same one.
  * ResultReader       pure, table-driven per tool: `of(tool, result_or_exception)` -> Outcome(state, outside_id). A success is `done`
                       (with the id the result shows), a clear refusal is `failed`, a timeout, a dropped connection, a 5xx or a page
                       that did not settle is `unknown`.
  * RepeatWords        pure: did the owner's own words of THIS turn ask for the step more than once ("twice", "두 번 보내", a count,
                       "again") next to the action? Used for a repeat inside one run only.
  * OwnerAsk           pure: do the owner's words of this chat or API turn ask for this kind of action (a send word, a change word, a
                       delete word, one table)? A new request from the owner is not a repeat the owner did not ask for.
  * DuplicateRule      pure: which record, if any, stops a step. An `unknown` always. A `done` one in the SAME run unless the owner asked
                       for it more than once. A `done` one in an EARLIER run: the owner's own turn that asks for this kind of action
                       runs it (with a note for the model), an owner turn that does not (the model redoing it on its own) and a
                       background run are stopped, except a schedule's next run repeating its own recurring step. A `failed` one never stops.
  * ActionLedger       the repository over the table `actions` (30 days).
  * DoneOnce           the policy loop.py calls: `begin(tool, args)` before the handler (a block, or a record of the attempt),
                       `Attempt.settle(result_or_exception, text)` after it (done / failed / unknown). Reads are never recorded; the
                       purchase tools and the "pay" click have their own ledger (receipt.py) and are not touched.
Wording for the model sits in the tool texts below, not in the always-on prompt (the prompt budget is tight).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

from .agent import Tool, found_secrets
from .agent.fence import split
from .effects import Effect, effect_of
from .i18n import N_
from .outside_rule import OWNER_SOURCES

log = logging.getLogger(__name__)

DAY = 86400.0
KEEP_DAYS = 30
DETAIL_MAX = 200
STATES = ("done", "unknown", "failed")
TOOL_NAME = "action_result"
CHECKED_KINDS = ("send", "change", "delete")
ANDROID_STEPS = frozenset({"android_tap", "android_type"})   # the phone app's steps: the app's package is the site (android/tools.py)
BROWSER_STEPS = frozenset({"browser_click", "browser_click_at", "browser_drag", "browser_type"}) | ANDROID_STEPS
TYPING_STEPS = frozenset({"browser_type", "android_type"})   # typing sends nothing by itself; its submit does

UNKNOWN_BLOCK = ("Not run: the same {action} on {target} at {time} has an unknown result (id {id}). Check first whether it went out (for mail: "
                 "`email_list` in the sent folder; on a site: its history or order page), then call `action_result({id}, done|not_done)`. "
                 "Do not repeat it before that.")
DONE_BLOCK = ("Not run: the same {action} on {target} already went out at {time} (id {id}). Do not do it twice: tell the owner it is already "
              "done. Only if the owner says they want it again (\"again\", \"once more\") in their own words may it be repeated.")
APPROVED_NOTE = " The owner's approval of the card does not override this."
AGAIN_NOTE = ("Note: the same step was done at {time} (id {id}); it ran again because the owner asked for it in this message.")
UNKNOWN_LINE = ("The result is unknown (id {id}): tell the owner what you did and that you are checking, check whether it went out (for mail: "
                "`email_list` in the sent folder; on a site: its history or order page), then call `action_result({id}, done|not_done)`. "
                "Do not repeat it before you know.")

# what the owner is told an unattended run left unconfirmed ("Kelo did <phrase> on <target> at <time> ...", followups.py)
TOOL_PHRASES = {
    "email_send": N_("a mail send"), "email_update": N_("a mailbox change"), "phone_send_text": N_("a text message"),
    "phone_call": N_("a phone call"), "connector_send": N_("a service call"), "mcp_call_tool": N_("a service call"),
    "package_entry_add": N_("a change in an app"), "package_entry_delete": N_("a change in an app"),
}
KIND_PHRASES = {"send": N_("a send"), "change": N_("a change"), "delete": N_("a deletion")}


def phrase_of(tool: str, kind: str) -> str:
    """The English msgid of what a step was (translated by whoever shows it)."""
    if tool in TOOL_PHRASES:
        return TOOL_PHRASES[tool]
    if tool.startswith(("gcal_", "phone_calendar")):
        return N_("a calendar change")
    if tool in ANDROID_STEPS:
        return N_("a step in a phone app")
    if tool in BROWSER_STEPS:
        return N_("a step on a site")
    return KIND_PHRASES.get(kind, KIND_PHRASES["change"])


def squash(value: Any) -> str:
    """Whitespace collapsed (case kept)."""
    return " ".join(str(value if value is not None else "").split())


def shown_target(targets: list[str] | tuple[str, ...], limit: int = 3) -> str:
    names = [str(x).removeprefix("site:").removeprefix("connector:")[:60] for x in targets if x]
    return (", ".join(names[:limit]) + (f" +{len(names) - limit}" if len(names) > limit else "")) or "?"


# ---------------------------------------------------------------------------------------------------------- the fingerprint
@dataclass(frozen=True, slots=True)
class PageStep:
    """What a browser step stands on: its commit class (commits.py), the page's path (no query), the clicked element's words and
    what was typed on the page before it (FormWords.digest): two reviews sent with the same "등록" button are two different steps."""
    commit: str
    path: str = ""
    label: str = ""
    typed: str = ""


# Query keys that only track, time or sign a visit: dropped, so the same page reached twice keeps one address
_VOLATILE_QUERY = re.compile(r"^(?:utm_.*|fbclid|gclid|dclid|msclkid|ref|referrer|src|source|ts|t|_|timestamp|time|nonce|token|"
                             r"session.*|sid|cache|cb|v|ver|rnd|random)$", re.I)


def page_key(url: str) -> str:
    """A page's path plus the query keys that say WHICH thing it is about (a product, an order, a post), sorted; the keys that only
    track or time a visit are dropped. Two reviews on one review page differ here by their product's id."""
    parts = urlsplit(url or "")
    keep = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _VOLATILE_QUERY.match(k))
    path = parts.path.rstrip("/")
    return f"{path}?{urlencode(keep)}" if keep else path


class FormWords:
    """What the agent typed in a run since its last committing browser step, so a commit stands for the form it sends. Typing after
    a commit starts a new form; a commit retried without new typing keeps the same words (and so the same fingerprint)."""

    MAX_FIELDS = 40

    def __init__(self) -> None:
        self._runs: dict[str, tuple[list[str], bool]] = {}   # run id -> (typed texts, a commit has used them)

    def typed(self, run: str, text: Any) -> None:
        words, used = self._runs.get(run, ([], False))
        words = [] if used else words
        self._runs[run] = ((words + [squash(text)])[-self.MAX_FIELDS:], False)

    def digest(self, run: str) -> str:
        words, _ = self._runs.get(run, ([], False))
        return hashlib.sha256("\n".join(words).encode()).hexdigest()[:16] if words else ""

    def committed(self, run: str) -> None:
        if run in self._runs:
            self._runs[run] = (self._runs[run][0], True)


class ActionFingerprint:
    """Pure: the same thing done outside gives the same fingerprint, however the model words its arguments."""

    VOLATILE = frozenset({"request_id", "requestid", "nonce", "timestamp", "ts", "sent_at", "idempotency_key", "idempotencykey",
                          "trace_id", "headers", "call_id"})
    SET_KEYS = frozenset({"to", "cc", "bcc", "uids", "attendees", "recipients"})   # order means nothing here

    @classmethod
    def of(cls, effect: Effect, tool_name: str, args: dict[str, Any] | None, page: PageStep | None = None) -> str:
        obj = {"tool": tool_name, "kind": effect.kind, "connector": effect.connector or "",
               "targets": sorted({squash(x) for x in effect.targets if squash(x)}), "body": cls._body(effect, tool_name, args or {}, page)}
        return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()

    @classmethod
    def _body(cls, effect: Effect, tool: str, args: dict[str, Any], page: PageStep | None) -> Any:
        if tool == "email_send":   # the subject, the body and the attachments by name
            return {"subject": squash(args.get("subject")), "text": squash(effect.text or args.get("body")),
                    "files": sorted(cls._names(args.get("attachments")))}
        if page is not None or tool in BROWSER_STEPS:   # the site is a target; the class, the page's path and the typed or clicked words
            page = page or PageStep("")
            body = {"commit": page.commit, "path": page.path.rstrip("/"),
                    "text": squash(args.get("text") if tool in TYPING_STEPS else page.label)}
            return {**body, "typed": page.typed} if page.typed else body
        if tool == "mcp_call_tool":
            return {"tool": squash(args.get("tool")), "arguments": cls.normalise(args.get("arguments") or {})}
        if tool == "phone_send_text":
            return {"text": squash(effect.text or args.get("text"))}
        return cls.normalise(args)

    @staticmethod
    def _names(value: Any) -> list[str]:
        out = []
        for item in value if isinstance(value, list) else [value] if value else []:
            name = item.get("name") or item.get("filename") or item.get("path") if isinstance(item, dict) else item
            if name:
                out.append(squash(str(name).replace("\\", "/").rsplit("/", 1)[-1]))
        return out

    @classmethod
    def normalise(cls, value: Any, key: str = "") -> Any:
        """Strings with their whitespace collapsed, mappings by sorted key without the volatile fields and the empty ones, a web
        address by host, path and sorted query; the sets of people and ids in sorted order."""
        if isinstance(value, str):
            return cls._address(value) if key.casefold() == "url" or key.casefold().endswith("_url") else squash(value)
        if isinstance(value, dict):
            out = {str(k): cls.normalise(v, str(k)) for k, v in value.items() if str(k).casefold() not in cls.VOLATILE}
            return {k: out[k] for k in sorted(out) if out[k] not in (None, "", [], {})}
        if isinstance(value, (list, tuple)):
            items = [cls.normalise(v) for v in value]
            items = [v for v in items if v not in (None, "", [], {})]
            return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, default=str)) if key.casefold() in cls.SET_KEYS else items
        return value

    @staticmethod
    def _address(url: str) -> str:
        try:
            parts = urlsplit(squash(url))
        except ValueError:
            return squash(url)
        if not parts.netloc:
            return squash(url)
        query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
        return f"{parts.netloc.lower()}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


# ---------------------------------------------------------------------------------------------------------- what a result means
class Outcome(NamedTuple):
    state: str          # done | unknown | failed
    outside_id: str = ""


_TIMEOUT = re.compile(
    r"time[ds]?[ -]?out|connection (?:was )?(?:reset|closed|aborted|dropped|lost|error)|(?:server|peer) (?:closed|disconnected)|disconnect|"
    r"broken pipe|reset by peer|unexpected eof|eof occurred|temporar(?:y|ily)|try again later|service unavailable|bad gateway|"
    r"gateway|overloaded|did not (?:finish|settle|load|respond|complete)|no response|network|unreachable|\b(?:read|write) error|"
    r"remote(?:ly)? (?:closed|protocol)|navigation|could not be reached|Connection\w*Error|\bOSError\b|SMTP\w*Disconnected", re.I)
_REFUSED = re.compile(
    r"must be|must have|\brequired\b|\binvalid\b|not a valid|malformed|not allowed|turned off|\brefused\b|not connected|not found|no such|"
    r"unknown (?:tool|account|connection|folder|service|calendar)|denied|unauthori[sz]ed|forbidden|permission|too many|at most|"
    r"\bnot run\b|was not run|never makes|bad request|unsupported|not supported|no (?:mcp )?endpoint|authentication|"
    r"nothing was (?:sent|changed|saved)|not (?:a )?registered|not accepted|did not accept|could not (?:resolve|find)|has no (?:address|host)|"
    r"cannot be used|does not match|only https|not a bare", re.I)
_ERROR = re.compile(r"^\s*(?:error\b|not run\b|\(the tool reported an error\))", re.I)
_HTTP_FIRST = re.compile(r"^\s*HTTP (\d{3})\b")
_HTTP_ANY = re.compile(r"\bHTTP (\d{3})\b")
_SMTP = re.compile(r"(?<![\d.])([45]\d\d)[ -]\d\.\d\.\d")
_SMTP_REFUSED = re.compile(r"SMTP(?:RecipientsRefused|SenderRefused|NotSupportedError|HeloError)")
_NOTHING_CHANGED = re.compile(r"the click went through, but nothing changed", re.I)
_PAGE_ERROR = re.compile(r"\bplease (?:enter|fill|select|correct|provide|choose)\b|\bis required\b|something went wrong|try again|"
                         r"올바르지|오류가 발생|다시 시도|필수 항목", re.I)
# the number a confirmation page shows (an order, a booking, a request); at least one digit
_CONFIRMATION = re.compile(
    r"(?:confirmation|order|booking|reservation|reference|receipt|ticket|tracking|request|application)\s*(?:number|no\.?|num|code|id|#)"
    r"\s*[:：#]?\s*([A-Z0-9][A-Z0-9-]{4,29})|(?:주문|예약|예매|접수|신청|확인|승인|결제)\s*번호\s*[:：]?\s*([A-Za-z0-9-]{4,30})|"
    r"\bconfirmation\s*[:#]\s*([A-Z0-9-]{5,30})", re.I)
_ID_KEYS = ("message_id", "messageId", "order_id", "orderId", "event_id", "eventId", "confirmation", "id")
_VALUE = r"""\\?["']?\s*[:=]\s*\\?["']?([\w.@<>:+/=-]{3,80})"""


class ResultReader:
    """Pure: what a tool's answer (or the exception it raised) says about whether the step happened outside."""

    NOT_DONE = {"null", "none", "true", "false"}

    def __init__(self) -> None:
        # per tool; any tool not listed reads as plain text
        self.table: dict[str, Callable[[str, str], Outcome]] = {
            "connector_send": self._http, "mcp_call_tool": self._mcp,
            **{name: self._page for name in BROWSER_STEPS}}

    def of(self, tool_name: str, result: Any) -> Outcome:
        if isinstance(result, BaseException):
            return self._exception(result)
        own, data = self._parts(result)
        return self.table.get(tool_name, self._plain)(own, data)

    def detail(self, result: Any) -> str:
        """A short line for the record: the exception, or the first line of the app's own words. Never a message body."""
        if isinstance(result, BaseException):
            line = f"{type(result).__name__}: {result}"
        else:
            own, _ = self._parts(result)
            line = next((ln for ln in own.splitlines() if ln.strip()), "")
        return " ".join(found_secrets.scrub(line).split())[:DETAIL_MAX]

    # ---- the kinds of reading
    def _plain(self, own: str, data: str) -> Outcome:
        return self._error(own, data) or Outcome("done", self._id(own + "\n" + data))

    def _http(self, own: str, data: str) -> Outcome:
        m = _HTTP_FIRST.search(own)
        if m is None:
            return self._plain(own, data)
        state = self._status(int(m.group(1)))
        return Outcome(state, self._id(data) if state == "done" else "")

    def _mcp(self, own: str, data: str) -> Outcome:
        if "(the tool reported an error)" in own:   # the server answered, and said it did not do it
            return Outcome("failed")
        return self._plain(own, data)

    def _page(self, own: str, data: str) -> Outcome:
        if (err := self._error(own, data)) is not None:
            return err
        text = own + "\n" + data
        if m := _CONFIRMATION.search(text):
            number = next((g for g in m.groups() if g and any(c.isdigit() for c in g)), "")
            if number:
                return Outcome("done", number)
        if _NOTHING_CHANGED.search(text) or _PAGE_ERROR.search(text):
            return Outcome("failed")
        return Outcome("done")

    # ---- the pieces
    @staticmethod
    def _parts(result: Any) -> tuple[str, str]:
        """(the app's own words, the external data) of a result; the data is what a service or page said."""
        text = getattr(result, "text", None)
        raw = str(text if isinstance(text, str) else result)
        head = getattr(result, "head", "") or ""
        parts = split(raw)
        own = "\n".join(p for is_data, p in parts if not is_data)
        data = "\n".join(p for is_data, p in parts if is_data)
        return (head + "\n" + own).strip(), data

    @staticmethod
    def _status(code: int) -> str:
        if code < 400:
            return "done"
        if code in (408, 429) or code >= 500:
            return "unknown"
        return "failed"

    def _error(self, own: str, data: str = "") -> Outcome | None:
        """None when the text is no error; else failed (a clear refusal) or unknown (it may have gone out). An error line quotes what the
        service said (fenced as data), so its words and status count too."""
        if not _ERROR.search(own):
            return None
        own = f"{own}\n{data}"
        if m := _HTTP_ANY.search(own):
            state = self._status(int(m.group(1)))
            return Outcome("unknown" if state == "done" else state)   # an error line that quotes a success code says nothing certain
        if _SMTP.search(own) or _SMTP_REFUSED.search(own):
            return Outcome("failed")
        if _TIMEOUT.search(own):
            return Outcome("unknown")
        return Outcome("failed" if _REFUSED.search(own) else "unknown")

    def _exception(self, exc: BaseException) -> Outcome:
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return Outcome("unknown")
        code = getattr(exc, "status_code", None) or getattr(exc, "status", None) or getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(code, int) and code:
            state = self._status(code)
            return Outcome("unknown" if state == "done" else state)
        name = type(exc).__name__
        if re.search(r"Timeout|Connection|Network|Transport|Protocol|Disconnect|ReadError|WriteError|Closed", name):
            return Outcome("unknown")
        if isinstance(exc, (ValueError, TypeError, KeyError, LookupError, PermissionError, NotImplementedError)) or "Policy" in name:
            return Outcome("failed")
        if isinstance(exc, OSError):
            return Outcome("unknown")
        return Outcome("failed" if _REFUSED.search(str(exc)) and not _TIMEOUT.search(str(exc)) else "unknown")

    def _id(self, text: str) -> str:
        for key in _ID_KEYS:
            for m in re.finditer(rf"""(?<![\w]){key}{_VALUE}""", text):
                value = m.group(1).strip(".,;")
                if value.casefold() not in self.NOT_DONE:
                    return value
        return ""


# ---------------------------------------------------------------------------------------------------------- the owner's own words
def owner_turn(turn: dict[str, Any] | None) -> bool:
    """A chat or API turn the owner is in: its request is the owner's own words (a background run, a group or a job has none)."""
    turn = turn or {}
    return str(turn.get("source") or "") in OWNER_SOURCES and not turn.get("unattended")


class RepeatWords:
    """Pure: the owner's own message of this turn asks for the step more than once, next to the action ("send it twice", "두 번 보내",
    "3 times", "send it again"). Only a repeat inside one run needs this."""

    REPEAT = re.compile(r"\b(?:again|once more|one more time|another time|twice|thrice|re-?send|re-?do|re-?try|repeat|(?:\d+|two|three|four|five|six)\s*times)\b|"
                        r"다시|한\s*번\s*더|두\s*번|세\s*번|네\s*번|\d+\s*(?:번|회)|두\s*차례|재전송|재발송|재송부|재시도", re.I)
    ALONE = re.compile(r"re-?send|re-?do|re-?try|재전송|재발송|재송부|재시도", re.I)   # the word itself names the action
    ACTIONS = {
        "send": re.compile(r"\b(?:send|sent|mail|e-?mail|text|message|sms|call|dm|post|reply|forward)\b|보내|보낼|전송|발송|메일|이메일|문자|"
                           r"메시지|전화|답장|올려", re.I),
        "change": re.compile(r"\b(?:update|change|edit|add|create|book|schedule|save|submit|apply|register|set|turn|do)\b|수정|변경|추가|등록|만들|생성|"
                             r"저장|신청|제출|예약|예매|해줘|해 줘|해봐", re.I),
        "delete": re.compile(r"\b(?:delete|remove|trash|cancel|erase|clear)\b|삭제|지워|지우|제거|취소|버려", re.I),
    }
    NEAR = 40   # characters around the repeat word in which the action must stand

    def asked(self, turn: dict[str, Any] | None, effect: Effect) -> bool:
        if not owner_turn(turn):
            return False   # a background run, a group or a job has no owner words of its own
        text = str((turn or {}).get("request") or "")
        pattern = self.ACTIONS.get(effect.kind)
        for m in self.REPEAT.finditer(text):
            if self.ALONE.fullmatch(m.group()):
                return True
            window = text[max(0, m.start() - self.NEAR): m.end() + self.NEAR]
            if pattern is not None and pattern.search(window):
                return True
        return False


class OwnerAsk:
    """Pure: the owner's own message of this chat or API turn asks for this kind of action. One table: the kind, its English action
    words (whole words, with an ending: sends, sent-as-send, sending) and its Korean ones (anywhere in the text)."""

    TABLE = {
        "send": (("send", "sent", "reply", "post", "mail", "text"), ("보내", "전송", "답장")),
        "change": (("turn", "set", "open", "lock", "add", "move", "update"), ("켜", "꺼", "바꿔", "올려", "내려", "열어", "잠가")),
        "delete": (("delete", "remove"), ("지워", "삭제")),
    }

    def __init__(self) -> None:
        self.patterns = {kind: re.compile(r"\b(?:" + "|".join(en) + r")(?:s|es|d|ed|ing)?\b|" + "|".join(ko), re.I) for kind, (en, ko) in self.TABLE.items()}

    # a question about the result is not a request: "불 켜졌어?", "메일 전송됐어?", "did you send it?", "is the light on?"
    KO_RESULT = re.compile(r"^(?:졌|졋|됐|되었|돼|됬)")
    EN_QUESTION = re.compile(r"^\s*(?:did|was|were|is|are|has|have|had)\b", re.I)

    def asked(self, turn: dict[str, Any] | None, effect: Effect) -> bool:
        pattern = self.patterns.get(effect.kind)
        text = str((turn or {}).get("request") or "")
        if not (pattern and owner_turn(turn)):
            return False
        question = text.rstrip().endswith("?")
        for m in pattern.finditer(text):
            if self.KO_RESULT.match(text[m.end():]):   # the stem followed by a passive/past ending: a question about what happened
                continue
            if question and self.EN_QUESTION.match(text):
                continue
            return True
        return False


# ---------------------------------------------------------------------------------------------------------- the ledger
SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, run_id TEXT NOT NULL DEFAULT '', task_id INTEGER, turn_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '', tool TEXT NOT NULL, kind TEXT NOT NULL, connector TEXT NOT NULL DEFAULT '',
    targets_json TEXT NOT NULL DEFAULT '[]', fingerprint TEXT NOT NULL, state TEXT NOT NULL, outside_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '', checked_at REAL);
CREATE INDEX IF NOT EXISTS actions_fingerprint ON actions(fingerprint, ts);
CREATE INDEX IF NOT EXISTS actions_ts ON actions(ts);
"""


@dataclass(frozen=True, slots=True)
class Record:
    id: int
    ts: float
    run_id: str
    task_id: int | None
    turn_id: str
    source: str
    tool: str
    kind: str
    connector: str
    targets: tuple[str, ...]
    fingerprint: str
    state: str
    outside_id: str
    detail: str
    checked_at: float | None

    @classmethod
    def of(cls, row: dict[str, Any]) -> Record:
        try:
            targets = tuple(str(x) for x in json.loads(row["targets_json"] or "[]"))
        except ValueError:
            targets = ()
        return cls(int(row["id"]), float(row["ts"]), row["run_id"], row["task_id"], row["turn_id"], row["source"], row["tool"], row["kind"],
                   row["connector"], targets, row["fingerprint"], row["state"], row["outside_id"], row["detail"], row["checked_at"])

    def public(self) -> dict[str, Any]:
        """What GET /api/actions shows: where and what, never a message body."""
        return {"id": self.id, "ts": self.ts, "run_id": self.run_id, "task_id": self.task_id, "turn_id": self.turn_id, "source": self.source,
                "tool": self.tool, "kind": self.kind, "connector": self.connector or None, "targets": list(self.targets),
                "state": self.state, "outside_id": self.outside_id, "detail": self.detail, "checked_at": self.checked_at}


class ActionLedger:
    """What Kelo did outside, one row per step that sends, changes or deletes (kept 30 days)."""

    def __init__(self, store: Any, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()
        self._pruned = 0.0
        self.prune()

    def record(self, *, run_id: str, task_id: int | None, turn_id: str, source: str, tool: str, kind: str, connector: str | None,
               targets: list[str], fingerprint: str, state: str = "unknown", outside_id: str = "", detail: str = "") -> int:
        now = self.clock()
        if now - self._pruned > 3600:
            self.prune()
        cur = self.store._run(
            "INSERT INTO actions(ts, run_id, task_id, turn_id, source, tool, kind, connector, targets_json, fingerprint, state, outside_id, detail) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, run_id, task_id, turn_id, source[:60], tool, kind, connector or "", json.dumps(targets, ensure_ascii=False), fingerprint, state,
             outside_id[:80], detail[:DETAIL_MAX]))
        return int(cur.lastrowid or 0)

    def mark(self, record_id: int, state: str, *, outside_id: str = "", detail: str = "", checked: bool = False) -> None:
        """Set the state (and the outside id, when given). `checked`: the owner's side was looked at (action_result): checked_at is set."""
        self.store._run("UPDATE actions SET state = ?, outside_id = CASE WHEN ? != '' THEN ? ELSE outside_id END, detail = ?, "
                        "checked_at = CASE WHEN ? THEN ? ELSE checked_at END WHERE id = ?",
                        (state, outside_id[:80], outside_id[:80], detail[:DETAIL_MAX], int(checked), self.clock(), int(record_id)))

    def get(self, record_id: int) -> Record | None:
        rows = self.store._all("SELECT * FROM actions WHERE id = ?", (int(record_id),))
        return Record.of(rows[0]) if rows else None

    def recent(self, fingerprint: str, within_s: float) -> list[Record]:
        """The records of this fingerprint in the last `within_s` seconds, newest first."""
        rows = self.store._all("SELECT * FROM actions WHERE fingerprint = ? AND ts >= ? ORDER BY id DESC", (fingerprint, self.clock() - within_s))
        return [Record.of(r) for r in rows]

    def open_unknown(self, run_id: str | None = None, within_s: float | None = None) -> list[Record]:
        since = self.clock() - within_s if within_s else 0.0
        rows = self.store._all("SELECT * FROM actions WHERE state = 'unknown' AND ts >= ? AND (? IS NULL OR run_id = ?) ORDER BY id",
                               (since, run_id, run_id))
        return [Record.of(r) for r in rows]

    def list(self, days: int = 7) -> list[Record]:
        since = self.clock() - max(1, min(int(days), KEEP_DAYS)) * 86400
        return [Record.of(r) for r in self.store._all("SELECT * FROM actions WHERE ts >= ? ORDER BY id DESC LIMIT 500", (since,))]

    def state_of(self, record_id: int) -> str:
        rec = self.get(record_id)
        return rec.state if rec is not None else "gone"

    def prune(self) -> None:
        self._pruned = self.clock()
        self.store._run("DELETE FROM actions WHERE ts < ?", (self._pruned - KEEP_DAYS * 86400,))


# ---------------------------------------------------------------------------------------------------------- the decision
@dataclass(frozen=True, slots=True)
class Block:
    text: str            # what the model is told: instead of the step running, or (reason "again") next to its result
    record: Record
    reason: str          # "unknown" | "done" | "again"

    @property
    def stops(self) -> bool:
        return self.reason != "again"


class DuplicateRule:
    """Pure: does a record of the same fingerprint stop this step? ("unknown" | "done" | "again", record) or None. "again" does not stop
    it: the owner asked for this kind of action in a new message, so it runs again, with a note for the model."""

    def __init__(self, repeats: RepeatWords | None = None, asks: OwnerAsk | None = None):
        self.repeats, self.asks = repeats or RepeatWords(), asks or OwnerAsk()

    def verdict(self, effect: Effect, records: list[Record], turn: dict[str, Any] | None) -> tuple[str, Record] | None:
        for rec in records:   # newest first; what is unknown is unknown for everyone, whoever asks
            if rec.state == "unknown":
                return "unknown", rec
        turn = turn or {}
        again = None
        for rec in records:
            if rec.state != "done":
                continue   # a failed step never stops anything
            how = self._how(rec, effect, turn)
            if how == "stop":
                return "done", rec
            if how == "again" and again is None:
                again = rec
        return ("again", again) if again is not None else None

    def _how(self, rec: Record, effect: Effect, turn: dict[str, Any]) -> str:
        """"stop", "again" (runs, with a note) or "ok" (runs) for a step whose fingerprint was done in `rec`."""
        if rec.run_id == DoneOnce.run_id(turn):   # a retry inside one run, unless the owner asked for it more than once
            return "ok" if self.repeats.asked(turn, effect) else "stop"
        if owner_turn(turn):   # a new request from the owner is no repeat they did not ask for; the model redoing it on its own is
            return "again" if self.asks.asked(turn, effect) else "stop"
        source = str(turn.get("source") or "")
        # a recurring schedule's next run is expected to do its step again; another kind of run is not
        return "ok" if source.startswith("schedule:") and rec.source == source else "stop"


class Attempt:
    """One tracked step of this run: a block (the step is not run), or the record that `settle` closes."""

    def __init__(self, once: DoneOnce, tool: str, record_id: int = 0, block: str = "", note: str = ""):
        self.once, self.tool, self.record_id, self.block, self.note = once, tool, record_id, block, note

    def settle(self, result: Any, text: str) -> str:
        """After the handler: `result` is its answer or the exception it raised, `text` what the model is shown. Returns `text`, with
        the note that it ran again at the owner's request, and the line about an unknown result, added."""
        text = f"{text}\n\n{self.note}" if self.note else text
        try:
            outcome = self.once.reader.of(self.tool, result)
            detail = "" if outcome.state == "done" else self.once.reader.detail(result)
            note = self.once.after(self.record_id, outcome, detail)
        except Exception:  # noqa: BLE001 - the record is a help; it never changes what the step answered
            log.exception("the action record of %s could not be closed", self.tool)
            return text
        return f"{text}\n\n{note}" if note else text


class DoneOnce:
    """What loop.py calls around a tool's handler: a step that sends, changes or deletes is checked against the ledger first, then recorded."""

    def __init__(self, ledger: ActionLedger, *, effect: Callable[[Any, dict[str, Any]], Effect | None],
                 turn: Callable[[], dict[str, Any] | None] = lambda: None, clock: Callable[[], float] = time.time,
                 tz: Callable[[], str | None] = lambda: None,
                 browser_step: Callable[[str, dict[str, Any]], Awaitable[tuple[str, str]]] | None = None,
                 browser_page: Callable[[str, dict[str, Any]], tuple[str, str]] | None = None,
                 on_unresolved: Callable[[dict[str, Any]], Any] | None = None,
                 reader: ResultReader | None = None, rule: DuplicateRule | None = None):
        """`effect(tool, args)`: effects.effect_of with the app's mailboxes (permissions.Permissions.effect). `turn()`: the running turn
        ({"task_id", "turn_id", "source", "unattended", "request"}). `browser_step(tool, args)` (async): (commit class, site) of a
        browser step; `browser_page(tool, args)`: (page path, clicked element's words). `on_unresolved(info)`: told, at the end of an
        unattended run, of each step whose result is still unknown (followups.unknown_result)."""
        self.ledger, self.effect, self.turn, self.clock, self.tz = ledger, effect, turn, clock, tz
        self.browser_step, self.browser_page, self.on_unresolved = browser_step, browser_page, on_unresolved
        self.reader, self.rule = reader or ResultReader(), rule or DuplicateRule()
        self.form = FormWords()

    # ---- the tool list: action_result is shown only while some step's result is unknown
    @property
    def version(self) -> int:
        """1 while some step's result is unknown, else 0: when it flips, a running turn works out its tool list again."""
        return int(bool(self.ledger.open_unknown(within_s=DAY)))

    def hides(self, tool_name: str) -> bool:
        return tool_name == TOOL_NAME and not self.version

    # ---- before and after
    @staticmethod
    def run_id(turn: dict[str, Any] | None) -> str:
        turn = turn or {}
        return str(turn.get("task_id") or turn.get("turn_id") or "")

    async def begin(self, tool: Any, args: dict[str, Any], *, approved: bool | None = None) -> Attempt | None:
        """Before the handler runs: None for a step that only reads (or is none of this module's business), an Attempt with a `block`
        when the same step is unknown or done already, else an Attempt whose record `settle` closes. Never raises."""
        try:
            name = str(getattr(tool, "name", tool))
            run = self.run_id(self.turn())
            if name in TYPING_STEPS and not args.get("submit"):
                self.form.typed(run, args.get("text"))
            effect, page = await self._effect_of(tool, name, args)
            if effect is None or effect.kind not in CHECKED_KINDS:
                return None
            if page is not None:
                page = PageStep(page.commit, page.path, page.label, self.form.digest(run))
                self.form.committed(run)
            fp = ActionFingerprint.of(effect, name, args, page)
            turn = dict(self.turn() or {})
            found = self.before(effect, fp, turn, name, approved=approved)
            if found is not None and found.stops:
                return Attempt(self, name, block=found.text)
            rid = self.ledger.record(run_id=self.run_id(turn), task_id=turn.get("task_id"), turn_id=str(turn.get("turn_id") or ""),
                                     source=str(turn.get("source") or ""), tool=name, kind=effect.kind, connector=effect.connector,
                                     targets=list(effect.targets), fingerprint=fp, state="unknown", detail="started, no answer yet")
            return Attempt(self, name, record_id=rid, note=found.text if found is not None else "")
        except Exception:  # noqa: BLE001 - a broken record never stops the owner's step
            log.exception("the action ledger failed before %s", getattr(tool, "name", tool))
            return None

    def before(self, effect: Effect, fp: str, turn: dict[str, Any] | None, tool: str = "", *, approved: bool | None = None) -> Block | None:
        """What the ledger says of a step that would repeat one (within 24 hours): a Block that stops it, a Block (reason "again") with the
        note for the model when it runs again because the owner asked, or None."""
        found = self.rule.verdict(effect, self.ledger.recent(fp, DAY), turn)
        if found is None:
            return None
        reason, rec = found
        words = {"action": (tool or rec.tool).replace("_", " "), "target": shown_target(rec.targets or effect.targets),
                 "time": self._when(rec.ts), "id": rec.id}
        text = {"unknown": UNKNOWN_BLOCK, "done": DONE_BLOCK, "again": AGAIN_NOTE}[reason].format(**words)
        if reason == "done" and approved:
            text += APPROVED_NOTE
        return Block(text, rec, reason)

    def after(self, record_id: int, outcome: Outcome, detail: str = "") -> str:
        """Write down what happened. Returns the line to add for the model when the result is unknown, else ""."""
        self.ledger.mark(record_id, outcome.state, outside_id=outcome.outside_id, detail=detail)
        return UNKNOWN_LINE.format(id=record_id) if outcome.state == "unknown" else ""

    async def _effect_of(self, tool: Any, name: str, args: dict[str, Any]) -> tuple[Effect | None, PageStep | None]:
        if name not in BROWSER_STEPS:
            return self.effect(tool, args), None
        if self.browser_step is None or (name in TYPING_STEPS and not args.get("submit")):
            return None, None   # typing into a field sends nothing by itself; without a browser there is nothing to judge
        commit, site = await self.browser_step(name, args)
        effect = effect_of(name, args, site=site, commit=commit) if commit and site else None
        if effect is None:
            return None, None
        path, label = self.browser_page(name, args) if self.browser_page is not None else ("", "")
        return effect, PageStep(commit, path, label)

    # ---- the end of an unattended run
    def run_ended(self, turn: dict[str, Any]) -> int:
        """An unattended run is over: each step of it whose result is still unknown goes to the owner as one follow-up. Returns how many."""
        if not turn.get("unattended") or self.on_unresolved is None:
            return 0
        told = 0
        for rec in self.ledger.open_unknown(self.run_id(turn)):
            try:
                self.on_unresolved({"id": rec.id, "ts": rec.ts, "task_id": rec.task_id, "source": rec.source, "tool": rec.tool,
                                    "action": phrase_of(rec.tool, rec.kind), "target": shown_target(rec.targets)})
                told += 1
            except Exception:  # noqa: BLE001 - one notice failing never stops the end of a run
                log.exception("could not tell about the unknown result %s", rec.id)
        return told

    def _when(self, ts: float) -> str:
        try:
            zone = ZoneInfo(self.tz() or "")
        except Exception:  # noqa: BLE001 - no usable zone: the server's own
            zone = None
        d = datetime.fromtimestamp(ts, zone)
        return d.strftime("%H:%M") if d.date() == datetime.fromtimestamp(self.clock(), zone).date() else d.strftime("%m-%d %H:%M")

    # ---- the agent's tool
    def tools(self) -> list[Tool]:
        async def action_result(args: dict[str, Any]) -> str:
            try:
                rid = int(args.get("id"))
            except (TypeError, ValueError):
                return "Error: id is the number from the unknown-result line."
            outcome = str(args.get("outcome") or "").strip().lower()
            if outcome not in ("done", "not_done"):
                return "Error: outcome is done (it went out) or not_done (it did not)."
            rec = self.ledger.get(rid)
            if rec is None:
                open_ids = ", ".join(str(r.id) for r in self.ledger.open_unknown(within_s=DAY)) or "none"
                return f"Error: no record {rid}. Open unknown results: {open_ids}."
            if rec.state != "unknown":
                return f"Error: record {rid} is already {rec.state}; only an unknown result is marked."
            if outcome == "done":
                self.ledger.mark(rid, "done", outside_id=str(args.get("outside_id") or "").strip(), detail="checked: it went through", checked=True)
                return f"Noted: record {rid} is done. Do not do it again."
            self.ledger.mark(rid, "failed", detail="checked: not sent" if rec.kind == "send" else "checked: not done", checked=True)
            return f"Noted: record {rid} did not happen. You may do it again now if it is still wanted."

        return [Tool(TOOL_NAME, "After a step's result was unknown (a timeout, a dropped connection): say what you found when you checked "
                     "whether it went out. done = it did, not_done = it did not (then it may be repeated).",
                     {"type": "object", "properties": {
                         "id": {"type": "integer", "description": "the id from the unknown-result line"},
                         "outcome": {"type": "string", "enum": ["done", "not_done"]},
                         "outside_id": {"type": "string", "description": "the message, order or entry id you found, if any"}},
                      "required": ["id", "outcome"]}, action_result)]
