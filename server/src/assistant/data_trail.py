"""The data trail: a code-first guard for data that leaves the owner's world (docs/data-trail.md, phase T1).

Every tool result that carries data from somewhere gets a label (`TrailLabel`): what kind of source (the TOOL's own declaration, never the
text), the item's own id, who it came from, its title, and a fingerprint of its text. Before a call that sends, posts, uploads, shares,
submits a form or opens an address runs, the call is read the same way and its fingerprint is compared with the labels of the run. A label
that is "in" the call is judged by facts only (no word lists, no site names, no language rules): see `DataTrail.review`.

Fingerprint: the text is normalised (NFKC, case-folded, whitespace collapsed) and cut into overlapping runs of RUN characters, each run
hashed; that works in any script. Exact "value tokens" (addresses, numbers of 6+ digits, URLs) go with it. Before an outgoing call is
read, common encodings are undone (percent, base64 of 16+, hex of 32+, punycode).
Fail closed: a private result without a label, a matcher or a label store that fails -> the outgoing call asks (a card).
"""

from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
import logging
import re
import struct
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit

from . import trail_files
from .connector_rules import SEP
from .i18n import N_, t
from .outside_rule import Derived, named_targets

log = logging.getLogger(__name__)

RUN = 12                  # characters in one run
MIN_RUNS = 3              # runs of a label that must appear in a call for the label to be "in" it
MAX_CHARS = 60_000        # of one text that is fingerprinted
MIN_DISTINCT = 4          # a run of fewer different characters ("-----------") says nothing
MIN_TITLE = 3             # a title shorter than this (normalised) is not a proof of anything
KEEP_DAYS = 7
MAX_OUTSIDE_CHARS = 200_000   # of one outside text that is read for the targets it names
MAX_FILE_LABELS = 8           # labels one file carries
SELF_KINDS = ("mail", "file", "memory", "contacts", "calendar", "photos", "messages", "notes", "vault", "profile", "web", "channel",
              "device", "connector")   # the kinds a Tool.trail may start with ("connector:<id>" for a connected service)
PRIVATE_KINDS = frozenset(SELF_KINDS) - {"web"}
OWNER_VALUE_KINDS = ("memory", "profile")   # where the owner's own values (address, phone) are saved: a form field made for them may take them

USES_PARAM: dict[str, Any] = {
    "type": "array", "items": {"type": "string"},
    "description": "Ids of the items you read (a mail's id, a file's path, a contact's id) whose content this step sends or shares. Only "
                   "when the user asked for exactly that item in their own words.",
}

CARD_LINE = N_("{kind} from {title} would go to {where}")
OUTSIDE_WORD = N_("outside")
LINK_WORD = N_("a public link")
SECRET_REFUSAL = N_("this would send the saved key or password \"{name}\" to a place it does not belong to; stopped")
SOURCE_WORDS = {   # the text the card gives a kind (translated; the kind itself is the tool's declaration)
    "mail": N_("mail"), "file": N_("file"), "memory": N_("memory"), "contacts": N_("contacts"), "calendar": N_("calendar"),
    "photos": N_("photos"), "messages": N_("messages"), "notes": N_("notes"), "vault": N_("saved logins"), "profile": N_("profile"),
    "web": N_("web page"), "channel": N_("chat"), "device": N_("device data"), "connector": N_("connected service"),
}


# ---------------------------------------------------------------------------------------------------------------- fingerprints
def normalise(text: str) -> str:
    """NFKC, case-folded, whitespace collapsed to one space."""
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def _h(piece: str) -> int:
    return int.from_bytes(hashlib.blake2b(piece.encode("utf-8", "replace"), digest_size=8).digest(), "big")


def runs_of(norm: str) -> frozenset[int]:
    """The hashed runs of RUN characters of an already normalised text (the first MAX_CHARS characters)."""
    norm = norm[:MAX_CHARS]
    out: set[int] = set()
    for i in range(0, len(norm) - RUN + 1):
        piece = norm[i:i + RUN]
        if len(set(piece)) >= MIN_DISTINCT:
            out.add(_h(piece))
    return frozenset(out)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NUMBER = re.compile(r"\d(?:[\s().-]?\d){5,}")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def tokens_of(norm: str) -> frozenset[str]:
    """Exact values: addresses, numbers of 6+ digits (digits only, so "010-1234-5678" and "01012345678" are one), URLs."""
    out = {m.group().strip(".,;:") for m in _EMAIL.finditer(norm)}
    out |= {re.sub(r"\D", "", m.group()) for m in _NUMBER.finditer(norm)}
    out |= {m.group().rstrip(".,;:") for m in _URL.finditer(norm)}
    return frozenset(x for x in out if x)


@dataclass(frozen=True)
class Fingerprint:
    runs: frozenset[int] = frozenset()
    tokens: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.runs or self.tokens)


def fingerprint(text: str) -> Fingerprint:
    norm = normalise(text)
    return Fingerprint(runs_of(norm), tokens_of(norm))


# ---- the decoder: common encodings undone before an outgoing call is read
_B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2}){16,}")
_PUNY = re.compile(r"xn--[a-z0-9-]+", re.I)
MAX_VARIANTS = 48


def _readable(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(text) < 8 or sum(ch.isprintable() or ch.isspace() for ch in text) < 0.9 * len(text):
        return None
    return text


def _decoded(text: str) -> list[str]:
    out: list[str] = []
    if "%" in text:
        once = unquote(text)
        twice = unquote(once)
        out += [x for x in (once, twice) if x != text]
    for m in _B64.finditer(text):
        piece = m.group().rstrip("=")
        for alphabet in (lambda s: s.replace("-", "+").replace("_", "/"), lambda s: s):
            try:
                s = alphabet(piece)
                got = _readable(base64.b64decode(s + "=" * (-len(s) % 4), validate=True))
            except (binascii.Error, ValueError):
                continue
            if got:
                out.append(got)
                break
    for m in _HEX.finditer(text):
        try:
            got = _readable(bytes.fromhex(m.group()))
        except ValueError:
            continue
        if got:
            out.append(got)
    for m in _PUNY.finditer(text):
        try:
            out.append(codecs.decode(m.group()[4:].lower(), "punycode"))
        except (UnicodeError, ValueError):
            continue
    return out


def variants(text: str, depth: int = 3) -> list[str]:
    """`text` and what its encoded parts decode to (a decoded part may be encoded again: up to `depth` levels)."""
    seen = [text]
    frontier = [text]
    for _ in range(depth):
        nxt: list[str] = []
        for piece in frontier:
            for got in _decoded(piece):
                if got not in seen and len(seen) < MAX_VARIANTS:
                    seen.append(got)
                    nxt.append(got)
        frontier = nxt
        if not frontier:
            break
    return seen


def outgoing_fingerprint(text: str) -> Fingerprint:
    runs: set[int] = set()
    toks: set[str] = set()
    for v in variants(text):
        fp = fingerprint(v)
        runs |= fp.runs
        toks |= fp.tokens
    return Fingerprint(frozenset(runs), frozenset(toks))


def is_in(label: Fingerprint, out: Fingerprint, ignore: Callable[[str], bool] | None = None) -> bool:
    """A label is "in" a call when MIN_RUNS of its runs (all of them, for a very short text) or any value token appear in it."""
    if label.runs:
        if len(label.runs & out.runs) >= min(MIN_RUNS, len(label.runs)):
            return True
    return any(x in out.tokens and not (ignore is not None and ignore(x)) for x in label.tokens)


# ---------------------------------------------------------------------------------------------------------------- labels
@dataclass(frozen=True)
class TrailLabel:
    id: str
    kind: str          # the tool's declaration: mail, file, memory, contacts, calendar, photos, messages, notes, vault, profile, web, channel, device, connector:<id>
    source_id: str     # the item's own id (message id, path, event id, contact id, URL) or the call's id
    party: str         # the address or account the item came from ("" when the source has none)
    title: str         # the item's own name (subject, file name, event title)
    private: bool      # an owner source
    outside: bool      # fenced as outside text
    at: float

    @property
    def base_kind(self) -> str:
        return self.kind.split(":", 1)[0]


@dataclass(frozen=True)
class TrailItem:
    """One item a tool's result carries (the small structured side field of a result): its own id, who it came from, its title and
    (when the tool has it) its own text. An item without text takes the whole result's text."""
    source_id: str
    party: str = ""
    title: str = ""
    text: str = ""
    kind: str = ""     # this item's own kind, when the tool returns more than one (read_file: a memory file or a document); else the tool's


class Collector:
    """The items a running tool call reports; the loop makes the call's labels from them."""
    def __init__(self) -> None:
        self.items: list[TrailItem] = []


from contextvars import ContextVar  # noqa: E402
collector: ContextVar[Collector | None] = ContextVar("trail_collector", default=None)


def note_item(source_id: str, *, party: str = "", title: str = "", text: str = "", kind: str = "") -> None:
    """Called by a tool that returns data: this result carries the item `source_id` (no effect outside a run)."""
    c = collector.get()
    if c is not None and source_id:
        c.items.append(TrailItem(str(source_id)[:300], str(party or "")[:300], " ".join(str(title or "").split())[:200], str(text or ""), kind))


active: ContextVar[Any] = ContextVar("trail_active", default=None)   # the DataTrail of the running turn (set by the loop), for the file tools


def mark_written(path: Any, text: str) -> None:
    """A tool wrote `text` to the file at `path`: it takes the labels of the run that the text carries (no effect outside a run)."""
    trail = active.get()
    if trail is not None:
        trail.mark_file(path, text)


def carried(path: Any) -> list[dict[str, Any]]:
    """The labels the file at `path` carries ([] outside a run or for a file with none): reading it carries them on."""
    trail = active.get()
    return trail.file_labels(path) if trail is not None else []


# ---------------------------------------------------------------------------------------------------------------- the store
# the tables (trail_labels, recipient_allows) are in store.py's SCHEMA: the task log's pruning and the chat clearing delete from them


class TrailStore:
    """Labels of a run (deleted with the run's records, and after KEEP_DAYS whatever happens) and the owner's recipient allows."""

    def __init__(self, store: Any, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        self._cache: OrderedDict[str, list[tuple[TrailLabel, Fingerprint]]] = OrderedDict()
        self.prune()
        self.drop_retired()

    # The saved-login list (vault_status) carried a "vault" label until 2026-11-16; a label made then stays with its run and made every
    # open of that site ask ("sends: saved logins") after the fix too. Labels of a source that no longer labels are dropped at start.
    RETIRED = (("vault", "vault_status"),)

    def drop_retired(self) -> int:
        n = 0
        for kind, title in self.RETIRED:
            try:
                n += self.store._run("DELETE FROM trail_labels WHERE kind = ? AND title = ?", (kind, title)).rowcount
            except Exception:  # noqa: BLE001 - a missing table on a fresh store: nothing to drop
                log.debug("no trail labels to drop")
        return n

    def add(self, run_id: str, label: TrailLabel, fp: Fingerprint) -> None:
        runs = sorted(fp.runs)
        self.store._run(
            "INSERT OR REPLACE INTO trail_labels(id, run_id, kind, source_id, party, title, private, outside, at, runs, tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (label.id, run_id, label.kind, label.source_id, label.party, label.title, int(label.private), int(label.outside), label.at,
             struct.pack(f">{len(runs)}Q", *runs), "\n".join(sorted(fp.tokens))))
        mine = self._cache.setdefault(run_id, [])
        mine[:] = [x for x in mine if x[0].id != label.id]   # (INSERT OR REPLACE above: the same id is one label)
        mine.append((label, fp))
        self._cache.move_to_end(run_id)
        while len(self._cache) > 8:
            self._cache.popitem(last=False)

    def labels(self, run_id: str) -> list[tuple[TrailLabel, Fingerprint]]:
        if run_id in self._cache:
            self._cache.move_to_end(run_id)
            return list(self._cache[run_id])
        rows = self.store._all("SELECT * FROM trail_labels WHERE run_id = ? ORDER BY at, id", (run_id,))
        out = []
        for r in rows:
            blob = r["runs"] or b""
            runs = frozenset(struct.unpack(f">{len(blob) // 8}Q", blob)) if blob else frozenset()
            out.append((TrailLabel(r["id"], r["kind"], r["source_id"], r["party"], r["title"], bool(r["private"]), bool(r["outside"]), r["at"]),
                        Fingerprint(runs, frozenset(x for x in str(r["tokens"] or "").split("\n") if x))))
        self._cache[run_id] = out
        return list(out)

    def delete_run(self, run_id: str) -> None:
        self._cache.pop(run_id, None)
        self.store._run("DELETE FROM trail_labels WHERE run_id = ?", (run_id,))

    def delete_all(self) -> None:
        self._cache.clear()
        self.store._run("DELETE FROM trail_labels")

    def prune(self) -> None:
        self.store._run("DELETE FROM trail_labels WHERE at < ?", (self.clock() - KEEP_DAYS * 86400,))

    # ---- the owner's allows: "always for this recipient" (written by the card's button, listed and removed by the owner's API only)
    def add_allow(self, recipient: str, kind: str, approval_id: str = "") -> dict[str, Any] | None:
        recipient, kind = (recipient or "").strip(), (kind or "").strip()
        if not recipient or not kind:
            return None
        self.store._run("INSERT OR IGNORE INTO recipient_allows(recipient, kind, created_at, approval_id) VALUES(?,?,?,?)",
                        (recipient, kind, self.clock(), approval_id))
        rows = self.store._all("SELECT * FROM recipient_allows WHERE recipient = ? AND kind = ?", (recipient, kind))
        return rows[0] if rows else None

    def allows(self) -> list[dict[str, Any]]:
        return self.store._all("SELECT id, recipient, kind, created_at, approval_id FROM recipient_allows ORDER BY id")

    def allowed(self, recipient: str, kind: str) -> bool:
        return bool(self.store._all("SELECT 1 FROM recipient_allows WHERE recipient = ? AND kind = ?", (recipient, kind)))

    def delete_allow(self, allow_id: int) -> bool:
        return self.store._run("DELETE FROM recipient_allows WHERE id = ?", (int(allow_id),)).rowcount > 0


# ---------------------------------------------------------------------------------------------------------------- the outgoing call
@dataclass
class Outbound:
    """What a call sends: where (comparable targets: an address, a number's digits, "site:<domain>", "connector:<name>"), the text
    that goes (without who it goes to), the ids of the files/items it names, and the ids the agent declared in `uses`."""
    where: list[str]
    text: str
    ids: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)   # the sites it reaches (for a saved key's own hosts)
    form_ok: bool = False   # typed into a form field made for the owner's own value, on the task's site (form_values.py): their saved copies may go
    policy: str = ""   # the connector's declared policy of this method (connector_rules.json): "task" and "fresh" take away the owner's lasting allow, "fresh" the task permission too


@dataclass
class Review:
    action: str = "pass"            # pass | ask | refuse
    card: str = ""
    message: str = ""
    offers: list[dict[str, Any]] = field(default_factory=list)   # [{"recipient", "kind", "titles": [...]}] for "always for this recipient"


@dataclass(frozen=True)
class Secret:
    name: str
    value: str
    hosts: tuple[str, ...]   # registrable sites the value belongs to


ID_ARGS = ("files", "attachments", "paths", "path", "file", "name")   # the names of arguments that carry a file's path (a tool's schema, not words)


def _ids_of(args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ID_ARGS:
        v = args.get(key)
        for x in ([v] if isinstance(v, str) else v if isinstance(v, list) else []):
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
    return out


def _uses_of(args: dict[str, Any]) -> list[str]:
    v = args.get("uses")
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def host_of(url: str) -> str:
    try:
        parts = urlsplit(url if "://" in url else "https://" + url)
        return (parts.hostname or "").lower()
    except ValueError:
        return ""


class DataTrail:
    """Labels for the results of a run, and the review of its outgoing calls (decisions 1-7 of docs/data-trail.md)."""

    def __init__(self, store: TrailStore, *, run_id: Callable[[], str], turn: Callable[[], dict[str, Any] | None],
                 is_own: Callable[[str], bool] = lambda _t: False, secrets: Callable[[], list[Secret]] | None = None,
                 site_of: Callable[[str], str] = lambda h: h, granted: Callable[[], Iterable[str]] | None = None,
                 owner_words: Callable[[], Iterable[str]] | None = None, current_site: Callable[[], str] = lambda: "",
                 norm_target: Callable[[str], str] = lambda x: x.strip().casefold(), clock: Callable[[], float] = time.time):
        """`is_own(target)`: the owner's own destination (a mailbox of theirs, their phone, their chat). `secrets()`: the saved keys and
        passwords with the sites they belong to (raises when the vault is locked: the call then asks). `granted()`: the places the
        running task's permissions name. `owner_words()`: the owner's own earlier messages of this conversation."""
        self.store, self.run_id, self.turn = store, run_id, turn
        self.is_own, self.secrets, self.site_of = is_own, secrets, site_of
        self.granted, self.owner_words, self.current_site = granted, owner_words, current_site
        self.norm_target, self.clock = norm_target, clock
        self.broken: set[str] = set()
        self.outside_broken: set[str] = set()   # runs whose outside labels could not be made: every unnamed target counts as derived

    # ------------------------------------------------------------------ labels
    def label_result(self, tool: Any, args: dict[str, Any], call_id: str, text: str, items: list[TrailItem], *, outside: bool = False) -> None:
        """Make the labels of one tool result. Never raises: a failure marks the run, and its outgoing calls then ask."""
        kind = str(getattr(tool, "trail", "") or "")
        if not kind:
            return
        run = self.run_id()
        try:
            whole = None
            made = 0
            for item in items or [TrailItem(call_id, "", _call_title(tool, args))]:
                private = (item.kind or kind).split(":", 1)[0] in PRIVATE_KINDS
                body = item.text
                if not body:
                    if whole is None:
                        whole = fingerprint(text)
                    fp = whole
                else:
                    fp = fingerprint(body)
                if not fp and not item.source_id:
                    continue
                label = TrailLabel(uuid.uuid4().hex, item.kind or kind, item.source_id or call_id, item.party, item.title or _call_title(tool, args),
                                   private, outside, self.clock())
                self.store.add(run, label, fp)
                made += 1
            if items and made == 0:
                raise ValueError("no label made")
        except Exception:  # noqa: BLE001 - fail closed: this run's outgoing calls ask
            log.exception("could not label the result of %s", getattr(tool, "name", "?"))
            self.broken.add(run)

    def label_outside(self, call_id: str, text: str, source: str = "") -> None:
        """The label of one outside text (a fenced tool result): its runs and tokens, and every target it names. The id is a hash of the
        text, so the same text twice in a run is one label. `source` says what kind of text it was (fence.SOURCE_LABELS: page, email,
        calendar ...; "" = web). Never raises: a failure marks the run (`derive` then asks for every target)."""
        run = self.run_id()
        try:
            norm = normalise(text)[:MAX_OUTSIDE_CHARS]
            if not norm:
                return
            lid = hashlib.blake2b((run + "\0" + norm).encode("utf-8", "replace"), digest_size=16).hexdigest()
            fp = Fingerprint(runs_of(norm), tokens_of(norm) | frozenset(named_targets(norm)))
            self.store.add(run, TrailLabel(lid, source or "web", call_id, "", "", False, True, self.clock()), fp)
        except Exception:  # noqa: BLE001 - fail closed
            log.exception("could not label outside text")
            self.outside_broken.add(run)

    def label_given(self, call_id: str, text: str) -> None:
        """The label of text the OWNER gave the run (a file they attached to their own message): not outside text, but its words are
        not the owner's own either, so a target written only there is derived from it (see derive). Never raises: a failure marks the run."""
        run = self.run_id()
        try:
            norm = normalise(text)[:MAX_OUTSIDE_CHARS]
            if not norm:
                return
            lid = hashlib.blake2b(("given\0" + run + "\0" + norm).encode("utf-8", "replace"), digest_size=16).hexdigest()
            fp = Fingerprint(runs_of(norm), tokens_of(norm) | frozenset(named_targets(norm)))
            self.store.add(run, TrailLabel(lid, "given", call_id, "", "", False, False, self.clock()), fp)
        except Exception:  # noqa: BLE001 - fail closed
            log.exception("could not label given text")
            self.outside_broken.add(run)

    def outside_sources(self) -> set[str]:
        """The kinds (outside_rule.SOURCE_WORDS keys) of the outside text this run has read; empty when unsure."""
        try:
            return {_source_key(lb.kind) for lb, _ in self.store.labels(self.run_id()) if lb.outside} - {""}
        except Exception:  # noqa: BLE001 - unsure: the card says "text from outside"
            return set()

    def given_link(self, url: str) -> bool:
        """The whole address, as written, is a link in outside text this run read (a search result, a page): reading it sends nothing the
        run made up. False when unsure."""
        try:
            toks = tokens_of(normalise(url))
            if not toks:
                return False
            return any(toks <= fp.tokens for lb, fp in self.store.labels(self.run_id()) if lb.outside)
        except Exception:  # noqa: BLE001 - unsure: not given
            return False

    def derive(self, targets: list[str], text: str) -> Derived | None:
        """What of a step comes from the run's outside text (docs/data-trail.md, "T3 as built"). None: no outside text in this run.
        A target written in a file the owner attached counts as derived too (not written by the owner); the content is checked against
        the outside text only (copying an attached file's text into a step is what the owner gave it for)."""
        run = self.run_id()
        every = Derived(frozenset(x for x in targets if x), True)
        try:
            if run in self.outside_broken:
                return every
            labels = self.store.labels(run)
            outs = [(lb, fp) for lb, fp in labels if lb.outside]
            given = [(lb, fp) for lb, fp in labels if lb.kind == "given"]
            if not outs and not given:
                return None
            words = self.own_words()
            owner_runs, owner_toks = runs_of(words), tokens_of(words) | frozenset(named_targets(words))
            found: set[str] = set()
            kinds: set[str] = set()
            for x in targets:
                if not x:
                    continue
                if not _comparable(x):   # no text form: derived whenever outside text came before
                    if outs:
                        found.add(x)
                        kinds |= {_source_key(lb.kind) for lb, _ in outs}
                    continue
                hit = [lb for lb, fp in outs + given if x in fp.tokens]
                if hit:
                    found.add(x)
                    kinds |= {_source_key(lb.kind) if lb.outside else "file" for lb in hit}   # an attached file is "a file"
            out = outgoing_fingerprint(text) if text.strip() else Fingerprint()
            matched = [lb for lb, fp in outs if out and is_in(Fingerprint(fp.runs - owner_runs, fp.tokens - owner_toks), out)]
            content = bool(matched)
            kinds |= {_source_key(lb.kind) for lb in matched}
            return Derived(frozenset(found), content, frozenset(k for k in kinds if k))
        except Exception:  # noqa: BLE001 - fail closed
            log.exception("the data trail could not tell where a step comes from; the owner is asked")
            return every

    # ------------------------------------------------------------------ files
    def mark_file(self, path: Any, text: str) -> None:
        """The labels of the run that `text` (just written to `path`) carries go into the file's attribute; none: the attribute goes."""
        run = self.run_id()
        try:
            out = outgoing_fingerprint(text) if text.strip() else Fingerprint()
            rows = [lb for lb, fp in self.store.labels(run) if out and is_in(fp, out)] if out else []
            rows = rows[:MAX_FILE_LABELS]
            data = {"v": 1, "labels": [{"id": lb.id, "kind": lb.kind, "source_id": lb.source_id, "party": lb.party, "title": lb.title,
                                        "outside": lb.outside} for lb in rows]} if rows else None
            if not trail_files.write(path, data):
                raise OSError("the file cannot hold labels")
        except Exception:  # noqa: BLE001 - fail closed: the run's outgoing calls ask
            log.exception("could not label a written file")
            self.broken.add(run)

    def file_labels(self, path: Any) -> list[dict[str, Any]]:
        got = trail_files.read(path)
        if not got:
            return []
        labels = [x for x in got.get("labels") or [] if isinstance(x, dict)]
        if got.get("unreadable"):
            labels.append({"kind": "web", "source_id": str(path), "outside": True})
        return labels

    # ------------------------------------------------------------------ reading a call
    def unreadable_line(self) -> str:
        return t(CARD_LINE).format(kind=t(SOURCE_WORDS["device"]), title="?", where="?")

    def outbound(self, tool: Any, name: str, args: dict[str, Any], e: Any, commit: str = "", site: str = "") -> Outbound | None:
        """What this call sends, or None when it does not send, post, upload, share, submit a form or open an address (effects.py decides
        the kind of a call; a browser step is judged by commits.py: `commit` and `site`)."""
        from . import effects
        from .outside_rule import SUBMIT_COMMITS
        from .privacy_guard import URL_TOOLS, outgoing_text
        args = args if isinstance(args, dict) else {}
        uses, ids = _uses_of(args), _ids_of(args)
        if name in URL_TOOLS:
            url = str(args.get("url") or "").strip()
            if not url:
                return None
            try:
                parts = urlsplit(url if "://" in url else "https://" + url)
            except ValueError:
                return Outbound(["site:?"], url, ids, uses)
            host = (parts.hostname or "").lower()
            # the address's own host is WHERE the call goes, not what it carries: only the path, query and fragment are content
            # (2026-11-16: the saved-login list names a shop's host, so every open of that shop asked as if the login went there)
            text = " ".join(x for x in (parts.path, parts.query, parts.fragment) if x and x != "/")
            return Outbound(["site:" + effects.site_target(host)] if host else ["site:?"], text, [], uses, [host] if host else [])
        if name == "browser_upload":
            where = self.current_site()
            return Outbound(["site:" + effects.site_target(where)] if where else ["site:?"], "", ids, uses, [where] if where else [])
        if name == "share_file":
            return Outbound(["link"], "", ids, uses)
        if e is None and name in ("browser_type", "android_type") and commit in SUBMIT_COMMITS and site:
            e = effects.effect_of(tool, args, site=site, commit=commit)
        if e is None or e.kind != "send":
            return None
        hosts = [x[5:] for x in e.targets if x.startswith("site:")]
        return Outbound([x for x in e.targets if x], outgoing_text(args, e.text), ids, uses, hosts, policy=getattr(e, "policy", ""))

    # ------------------------------------------------------------------ the review
    def own_words(self) -> str:
        """The owner's own words of this turn: its message(s), or a background job's saved request. Outside text never counts."""
        turn = dict(self.turn() or {})
        if str(turn.get("source") or "").startswith("group:"):
            return ""
        parts = [str(turn.get("request") or "")]
        if self.owner_words is not None and not turn.get("unattended"):
            try:
                parts += [str(x) for x in self.owner_words()]
            except Exception:  # noqa: BLE001 - this turn's words only
                pass
        return normalise("\n".join(parts))

    def _titled(self, label: TrailLabel, words: str) -> bool:
        title = normalise(label.title)
        return len(title) >= MIN_TITLE and title in words

    def review(self, outbound: Outbound | None) -> Review:
        """The decision for one outgoing call. Everything here is a comparison of facts."""
        if outbound is None:
            return Review()
        run = self.run_id()
        try:
            return self._review(outbound, run)
        except Exception:  # noqa: BLE001 - fail closed
            log.exception("the data trail could not review a call; the owner is asked")
            where = ", ".join(self._shown(x) for x in outbound.where[:3]) or "?"
            return Review("ask", t(CARD_LINE).format(kind=t(SOURCE_WORDS["device"]), title="?", where=where))

    def _shown(self, target: str) -> str:
        if target == "link":
            return t(LINK_WORD)
        return target.removeprefix("site:").removeprefix("connector:")[:60]

    def _review(self, ob: Outbound, run: str) -> Review:
        out = outgoing_fingerprint(ob.text)
        # 1. secret values: only to the credential's own sites, else refused (not a card)
        if self.secrets is not None and ob.text.strip():
            variants_ = variants(ob.text)
            low = [normalise(v) for v in variants_]
            for s in self.secrets():
                if len(s.value) >= 8 and (any(s.value in v for v in variants_) or normalise(s.value) in "\n".join(low)):
                    sites = {self.site_of(h) for h in ob.hosts if h}
                    if not sites or not sites <= set(s.hosts):
                        return Review("refuse", message=t(SECRET_REFUSAL).format(name=s.name))
        labels = [(lb, fp) for lb, fp in self.store.labels(run) if lb.private]
        broken = run in self.broken
        going: list[TrailLabel] = []
        if labels or broken:
            dest_hosts = {h.lower() for h in ob.hosts if h}
            # the owner's own address in a mail says nothing, and neither does the destination's own host (or a host of its site)
            ignore = (lambda x: self.is_own(x) or any(x.lower() == h or x.lower().endswith("." + h) or h.endswith("." + x.lower())
                                                     for h in dest_hosts))
            ids = set(ob.ids)
            names = {i.rsplit("/", 1)[-1] for i in ids}
            for lb, fp in labels:
                if lb.source_id in ids or (names and lb.source_id.rsplit("/", 1)[-1] in names) or is_in(fp, out, ignore):
                    going.append(lb)
        if not going and not broken:
            return Review()
        # decisions 2-6 per label and destination
        words = self.own_words()
        picked = {str(x) for x in (dict(self.turn() or {}).get("picked") or [])}
        uses = set(ob.uses)
        granted = {self.norm_target(x) for x in (self.granted() if self.granted is not None else [])}
        asks: list[tuple[TrailLabel | None, str]] = []
        dests = [d for d in ob.where if d] or [""]
        for dest in dests:
            if dest and (self.is_own(dest) or self.is_own(dest.removeprefix("connector:"))):
                continue                                                           # 2. the owner's own destination
            if broken and not going:
                asks.append((None, dest))                                          # fail closed: labels are missing
                continue
            for lb in going:
                if ob.form_ok and lb.base_kind in OWNER_VALUE_KINDS:
                    continue                                                       # private form value into its own field (T2)
                if lb.party and dest and self.norm_target(lb.party) == dest:
                    continue                                                       # 3. back to its origin
                if lb.source_id in picked or (lb.source_id in uses and self._titled(lb, words)):
                    continue                                                       # 4. picked by the owner
                if dest and ob.policy in ("", "standing") and self.store.allowed(dest, lb.kind):
                    continue                                                       # 5. the owner's allow for this recipient
                if dest and ob.policy != "fresh" and (dest in granted or granted_whole(dest, granted)):
                    continue                                                       # 6. the task's permission names this recipient
                asks.append((lb, dest))                                            # 7. a card
        if not asks:
            return Review()
        lines: list[str] = []
        offers: dict[tuple[str, str], list[str]] = {}
        for lb, dest in asks:
            where = self._shown(dest) or t(OUTSIDE_WORD)
            if lb is None:
                lines.append(t(CARD_LINE).format(kind=t(SOURCE_WORDS["device"]), title="?", where=where))
                continue
            lines.append(t(CARD_LINE).format(kind=t(SOURCE_WORDS.get(lb.base_kind, lb.base_kind)), title=lb.title[:60] or "?", where=where))
            if dest and ob.policy in ("", "standing"):   # a method that never takes a lasting allow offers none
                offers.setdefault((dest, lb.kind), []).append(lb.title[:60])
        lines = list(dict.fromkeys(lines))[:6]
        return Review("ask", "\n".join(lines), offers=[{"recipient": r, "kind": k, "titles": list(dict.fromkeys(v))[:6]}
                                                       for (r, k), v in offers.items()])


def granted_whole(dest: str, granted: set[str]) -> bool:
    """A recipient bound to a connector ("connector:<name>#<recipient>") is covered by a permission that names the connector whole."""
    return dest.startswith("connector:") and SEP in dest and dest.split(SEP, 1)[0] in granted


def _comparable(target: str) -> bool:
    """A target with a text form an outside text can be searched for: an address, a handle, a number's digits, "site:<domain>". Others
    ("connector:<name>", "calendar", "calendar:<id>", "event:<id>", an app, a contact's name) cannot be told from outside text, so they
    count as coming from it. A calendar id can contain "@", so the calendar and event targets are checked first."""
    if target.startswith(("calendar:", "event:")):
        return False
    return "@" in target or target.isdigit() or target.startswith("site:")


# the kinds of outside text, as the card's words know them (outside_rule.SOURCE_WORDS); a tool's own data kind maps to its source
SOURCE_KEYS = {"mail": "email", "email": "email", "page": "page", "web": "web", "file": "file", "memory": "file", "connector": "connector",
               "mcp": "mcp", "package": "package", "phone": "phone", "contacts": "phone", "messages": "phone", "calendar": "calendar"}


def _source_key(kind: str) -> str:
    return SOURCE_KEYS.get(str(kind or "").split(":", 1)[0], "")


TITLE_ARGS = ("url", "path", "query", "q", "name", "id", "to", "subject")   # arguments that say what a call was about (a tool's schema, not words)


def _call_title(tool: Any, args: dict[str, Any]) -> str:
    for k in TITLE_ARGS:
        v = (args or {}).get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return f"{getattr(tool, 'name', 'tool')} {str(v).strip()[:80]}"
    return str(getattr(tool, "name", "tool"))
