"""Remembered checkout steps (docs/instant-buy.md C): a buying run that paid is kept as a plan of steps by element role and name; the
next hit of that product replays it with no model call, and any doubt hands the same run over to the model.

Recording (Recorder): the buying run's tools are wrapped. Each successful step is kept, in order:
  {"do": "open", "url"}                       scheme://host/path, no query
  {"do": "click", "role", "name", "path"}     role and name from the reading the ref came from; path = the page's url path
  {"do": "type", "role", "name", "text"}      only a field that is not personal, with a short value (no '@', no run of 5+ digits)
  {"do": "fill_shipping", "profile"}          the profile nickname the run passed ("" = the order's address)
  {"do": "fill_card", "card"}                 the card argument the run passed (a label, never a number)
  {"do": "confirm", "path"}                   only when the purchase window opened after it
A step that cannot be replayed (a personal field, a sign-in, a click without a named element) makes the recording unusable: no plan.
Personal values never enter a plan.

Replay (ReplayStream): wraps the turn's stream function. While the plan runs it answers each request with the next tool call; refs are
found in the tab's latest reading: the one element whose role and name equal the step's (whitespace and case folded), else the one
of that role whose name contains the step's name; none or several, after one fresh reading, is a stop. The confirm step is checked
here in code: the permission's `what` must show in the page text, the total is read with purchase_total() from the page's order-total elements, and purchase_check() must
cover it; then confirm_purchase gets those values (the tool checks everything again). A stop (no element, a click that changed
nothing, a tool error, an unclear total, a refused check, a payment not confirmed) hands the SAME run to the model with a note of what
was done and why it stopped. After the last step the replay calls end_order and ends the run with one line, with no model call.
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .i18n import N_, t
from .providers.types import Done, TextDelta, ToolCall
from .receipt import currency_ok, parse_money
from .schedule_grants import words_of
from .tools.checkout import read_page
from .tools.page_tree import ref_of, role_name
from .wallet import money_text, site_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS buy_plans (
    id INTEGER PRIMARY KEY, site TEXT NOT NULL, product_path TEXT NOT NULL, steps TEXT NOT NULL, created_at REAL NOT NULL,
    used_count INTEGER NOT NULL DEFAULT 0, last_ok_at REAL, fails INTEGER NOT NULL DEFAULT 0, UNIQUE(site, product_path));
"""
MAX_FAILS = 2   # a plan that stopped twice in a row is dropped

DONE_LINE = N_("Bought {what} with the remembered checkout steps ({amount}).")
STOP_NOTE = ("[Kelo] The remembered checkout steps for this site ran first: {done}. They stopped here: {why}. The tab shows that page "
             "now. Go on from here with the steps of your instructions. A payment confirmed above is not confirmed again.")

# --------------------------------------------------------------------------- the total on a checkout page
def purchase_total(totals: list[str], currency: str, declared: str = "") -> float | None:
    """The order total by the page's STRUCTURE: the texts of its order-total elements (checkout.Probe.totals: schema.org totals,
    data-total style attributes, <output>). Exactly one distinct amount, in the granted currency; none, several, an amount that cannot
    be read, a currency named by the element or declared by the page that differs, or no total element at all give None (the caller
    stops and the model takes over). No label word is read."""
    if declared and currency and declared.upper() != currency.upper():
        return None
    amounts: set[float] = set()
    for text in totals:
        amount, named = parse_money(text)
        if amount is None or not currency_ok(currency, named):
            return None
        amounts.add(round(amount, 2))
    return next(iter(amounts)) if len(amounts) == 1 else None


# --------------------------------------------------------------------------- plans
def _norm(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def path_pattern(path: str) -> str:
    """/goods/12345/detail and /goods/678/detail are the same product page pattern."""
    return re.sub(r"\d+", "#", (path or "/").rstrip("/") or "/")


def _site_path(url: str) -> tuple[str, str]:
    u = urlsplit(url or "")
    return site_key(u.hostname or ""), (u.path or "/")


class Plans:
    def __init__(self, store: Any, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def _one(self, row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "steps": json.loads(row["steps"] or "[]")}

    def find(self, url: str) -> dict[str, Any] | None:
        """The plan of this product page: the same path, else the same site and path pattern (the most recently good one)."""
        site, path = _site_path(url)
        if not site:
            return None
        rows = self.store._all("SELECT * FROM buy_plans WHERE site = ? ORDER BY COALESCE(last_ok_at, created_at) DESC", (site,))
        for r in rows:
            if r["product_path"] == path:
                return self._one(r)
        for r in rows:
            if path_pattern(r["product_path"]) == path_pattern(path):
                return self._one(r)
        return None

    def save(self, url: str, steps: list[dict[str, Any]]) -> None:
        site, path = _site_path(url)
        self.store._run("INSERT INTO buy_plans(site, product_path, steps, created_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(site, product_path) DO UPDATE SET steps = excluded.steps, created_at = excluded.created_at, fails = 0",
                        (site, path, json.dumps(steps, ensure_ascii=False), self.clock()))

    def ok(self, plan_id: int) -> None:
        self.store._run("UPDATE buy_plans SET used_count = used_count + 1, last_ok_at = ?, fails = 0 WHERE id = ?", (self.clock(), plan_id))

    def failed(self, plan_id: int) -> bool:
        """One more stop; True when the plan was dropped (MAX_FAILS in a row)."""
        self.store._run("UPDATE buy_plans SET fails = fails + 1 WHERE id = ?", (plan_id,))
        rows = self.store._all("SELECT fails FROM buy_plans WHERE id = ?", (plan_id,))
        if rows and rows[0]["fails"] >= MAX_FAILS:
            self.store._run("DELETE FROM buy_plans WHERE id = ?", (plan_id,))
            return True
        return False


# --------------------------------------------------------------------------- recording
# Typed text is remembered only for the roles that hold no personal data by their nature: a number stepper (a quantity) or a search box.
# Every other field (name, address, mail, phone, card ...) makes the plan unusable: the shipping and card tools fill those from the owner's
# saved data on replay. The role is the page's own (ARIA); no label word is read.
IMPERSONAL_ROLES = ("spinbutton", "searchbox")
LONG_DIGITS = re.compile(r"\d{5,}")
RECORDED = ("browser_open", "browser_click", "browser_type", "browser_fill_shipping", "browser_fill_card", "confirm_purchase",
            "browser_login", "browser_click_at", "browser_drag")


class Recorder:
    def __init__(self, session: Any, tab: str):
        self.session, self.tab = session, tab
        self.steps: list[dict[str, Any]] = []
        self.unusable = ""   # why this recording cannot become a plan ("" = it can)

    def usable(self) -> bool:
        kinds = [s["do"] for s in self.steps]
        return not self.unusable and "confirm" in kinds and kinds.index("confirm") < len(kinds) - 1

    def wrap(self, tools: list[Any]) -> list[Any]:
        return [dataclasses.replace(x, handler=self._wrapped(x.name, x.handler)) if x.name in RECORDED else x for x in tools]

    def _path(self) -> str:
        return urlsplit(self.session.current_url() or "").path or "/"

    def _target(self, args: dict[str, Any]) -> tuple[str, str]:
        ref = ref_of(str(args.get("selector") or ""))
        return role_name(self.session.ref_words_of(self.tab).get(ref or "", "")) if ref else ("", "")

    def _wrapped(self, name: str, handler: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
        async def run(args: dict[str, Any]) -> Any:
            role, label = self._target(args) if name in ("browser_click", "browser_type") else ("", "")
            path = self._path()
            out = await handler(args)   # a raise is not a step done: nothing is kept
            text = out if isinstance(out, str) else str(getattr(out, "text", "") or "")
            if text.startswith("Error"):
                return out
            self._keep(name, args, role, label, path)
            return out
        return run

    def _keep(self, name: str, args: dict[str, Any], role: str, label: str, path: str) -> None:
        if name == "browser_open":
            u = urlsplit(str(args.get("url") or ""))
            self.steps.append({"do": "open", "url": urlunsplit((u.scheme, u.netloc, u.path, "", ""))})
        elif name == "browser_click":
            if self.session.last_click_changed_nothing(self.tab):
                return
            if not role or not label:
                self.unusable = self.unusable or "a click on an element without a name"
                return
            self.steps.append({"do": "click", "role": role, "name": label, "path": path})
        elif name == "browser_type":
            value = str(args.get("text") or "")
            if (not role or not label or role not in IMPERSONAL_ROLES or "@" in value or LONG_DIGITS.search(value)
                    or not value.strip() or len(value) > 40):
                self.unusable = self.unusable or "a personal or unnamed field was typed"
                return
            self.steps.append({"do": "type", "role": role, "name": label, "text": value})
        elif name == "browser_fill_shipping":
            self.steps.append({"do": "fill_shipping", "profile": str(args.get("profile") or "")})
        elif name == "browser_fill_card":
            self.steps.append({"do": "fill_card", "card": str(args.get("card") or "")})
        elif name == "confirm_purchase":
            if self.session.purchase_active():
                self.steps.append({"do": "confirm", "path": self._path()})
        else:   # browser_login, a click at a point, a drag
            self.unusable = self.unusable or f"{name} cannot be replayed"


# --------------------------------------------------------------------------- replay
def _content_text(msg: dict[str, Any]) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(p.get("text") or "") for p in c if isinstance(p, dict))
    return ""


def find_ref(words: dict[str, str], role: str, name: str) -> str | None:
    """The one ref of this role whose name equals `name` (folded), else the one whose name contains it; None for none or several."""
    want_role, want = role.casefold(), _norm(name)
    same = [(ref, _norm(role_name(w)[1])) for ref, w in words.items() if role_name(w)[0].casefold() == want_role]
    exact = [ref for ref, n in same if n == want]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    near = [ref for ref, n in same if want and want in n]
    return near[0] if len(near) == 1 else None


class ReplayStream:
    def __init__(self, plan: dict[str, Any], session: Any, tab: str, grant: dict[str, Any], grants: Any):
        self.plan, self.session, self.tab, self.grant, self.grants = plan, session, tab, grant, grants
        self.steps: list[dict[str, Any]] = list(plan.get("steps") or [])
        self.currency = str(grant.get("currency") or "KRW").upper()
        self.pos = 0
        self.done: list[str] = []      # each step done, in short words, for the note
        self.why = ""                  # the stop reason; "" while replaying and after a full replay
        self.finished = False          # every step done and end_order called
        self.amount: float | None = None
        self._out: dict[str, Any] | None = None   # the step whose tool call is out: {"kind", "step"}
        self._reread = False           # one fresh reading was taken for the current step
        self._note_at: int | None = None
        self._n = 0

    # what run_turn(stream_wrap=...) takes
    def wrap(self, real: Callable[..., AsyncIterator[Any]]) -> Callable[..., AsyncIterator[Any]]:
        async def stream(provider: Any, request: Any, **kw: Any) -> AsyncIterator[Any]:
            if not self.why and not self.finished:
                ev = await self._next(list(request.messages))
                if isinstance(ev, ToolCall):
                    yield ev
                    yield Done("tool_calls")
                    return
                if isinstance(ev, str):
                    yield TextDelta(ev)
                    yield Done("stop")
                    return
            if self.finished and not self.why:
                yield TextDelta(t(DONE_LINE, what=self.grant.get("what") or "", amount=money_text(self.amount or 0, self.currency)))
                yield Done("stop")
                return
            async for e in real(provider, self._with_note(request), **kw):
                yield e
        return stream

    def _with_note(self, request: Any) -> Any:
        msgs = list(request.messages)
        at = len(msgs) if self._note_at is None else min(self._note_at, len(msgs))
        note = STOP_NOTE.format(done="; ".join(self.done) or "nothing", why=self.why)
        msgs.insert(at, {"role": "user", "content": note})
        return dataclasses.replace(request, messages=msgs)

    def _stop(self, why: str, msgs: list[dict[str, Any]]) -> None:
        self.why = why
        self._note_at = len(msgs)

    def _call(self, name: str, args: dict[str, Any], kind: str, step: dict[str, Any] | None = None) -> ToolCall:
        self._n += 1
        self._out = {"kind": kind, "step": step}
        return ToolCall(id=f"replay-{self._n}", name=name, arguments=json.dumps(args, ensure_ascii=False))

    async def _next(self, msgs: list[dict[str, Any]]) -> ToolCall | str | None:
        last = _content_text(msgs[-1]) if msgs and msgs[-1].get("role") == "tool" else ""
        out, self._out = self._out, None
        if out is not None:
            kind, step = out["kind"], out["step"]
            if last.startswith("Error"):
                self._stop(f"the step {self._words(step) or kind} failed: {last[:200]}", msgs)
                return None
            if kind == "end":
                self.finished = True
                return None
            if kind == "read_for_confirm":
                return await self._confirm(step, last, msgs)
            if kind == "read":
                self._reread = True
            elif kind == "step":
                if step["do"] == "click" and self.session.last_click_changed_nothing(self.tab):
                    self._stop(f"clicking {self._words(step)} changed nothing", msgs)
                    return None
                if step["do"] == "confirm" and not self.session.purchase_active():
                    self._stop(f"the payment was not confirmed: {last[:200]}", msgs)
                    return None
                self.done.append(self._words(step))
                self.pos += 1
                self._reread = False
        if self.pos >= len(self.steps):
            return self._call("end_order", {}, "end")
        return self._act(self.steps[self.pos], msgs)

    def _words(self, step: dict[str, Any] | None) -> str:
        if not step:
            return ""
        if step["do"] in ("click", "type"):
            return f'{step["do"]} {step.get("role")} "{step.get("name")}"'
        return step["do"]

    def _act(self, step: dict[str, Any], msgs: list[dict[str, Any]]) -> ToolCall | None:
        do = step["do"]
        if do == "open":
            return self._call("browser_open", {"url": step["url"]}, "step", step)
        if do in ("click", "type"):
            ref = find_ref(self.session.ref_words_of(self.tab), step["role"], step["name"])
            if ref is None:
                if not self._reread:
                    return self._call("browser_read", {}, "read", step)
                self._stop(f'no single {step["role"]} named "{step["name"]}" on the page', msgs)
                return None
            if do == "click":
                return self._call("browser_click", {"selector": ref}, "step", step)
            return self._call("browser_type", {"selector": ref, "text": step["text"]}, "step", step)
        if do == "fill_shipping":
            return self._call("browser_fill_shipping", {"profile": step["profile"]} if step.get("profile") else {}, "step", step)
        if do == "fill_card":
            return self._call("browser_fill_card", {"card": step["card"]} if step.get("card") else {}, "step", step)
        if do == "confirm":
            return self._call("browser_read", {}, "read_for_confirm", step)
        self._stop(f"an unknown step {do!r}", msgs)
        return None

    async def _confirm(self, step: dict[str, Any], page: str, msgs: list[dict[str, Any]]) -> ToolCall | None:
        what = str(self.grant.get("what") or "")
        want, have = words_of(what), words_of(page)
        if want and len(want & have) < max(1, (len(want) + 1) // 2):
            self._stop(f"the page does not name {what}", msgs)
            return None
        probe = await read_page(self.session)
        amount = purchase_total(probe.totals, self.currency, probe.currency) if probe is not None else None
        if amount is None:
            self._stop("the total on the page is not clear", msgs)
            return None
        host = self.session.current_host()
        chk = self.grants.purchase_check(host, amount, self.currency, what)
        if chk.grant is None:
            self._stop(chk.note or "the purchase permission does not cover this total", msgs)
            return None
        self.amount = amount
        return self._call("confirm_purchase", {"site": host, "summary": what, "amount": amount, "currency": self.currency}, "step", step)
