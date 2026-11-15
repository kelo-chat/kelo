"""What the user did while they held a tab (live view → take control), told to the agent when they hand it back.

While a device controls a tab, a small script runs in an isolated world of the page (`Page.addScriptToEvaluateOnNewDocument` with a
world name, so the page's own scripts can neither see nor call it) and reports through a CDP binding that exists only in that world:
- clicks: the label of what was clicked (aria-label, text, title, alt) and the link it leads to;
- fields: which field changed (its label) and, for plain fields only, a short value (≤ 40 characters). Password, card, security code,
  one-time code and similar fields are recorded as "edited" with NO value, and the value is never read at all;
- form submits.
Navigations of the main frame come from `Page.frameNavigated`. Nothing is written to disk: the journal lives in memory until the tab is
handed back, then becomes a few lines the agent reads with its next look at the page (BrowserSession.note). Labels and addresses come from
the page itself, so the agent gets them inside the page text (untrusted).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..agent.fence import data

WORLD = "kelo-journal"
BINDING = "__keloJournalNote"
MAX_ENTRIES = 60          # kept per takeover (the oldest go first)
MAX_LINES = 14            # lines in the summary

SCRIPT = r"""(() => {
  if (window.__keloJournal) return; window.__keloJournal = 1;
  const send = (o) => { try { %(binding)s(JSON.stringify(o)); } catch (e) {} };
  const clip = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const label = (el) => clip(el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('alt')) || el.innerText
    || (el.tagName === 'INPUT' ? el.value : '') || el.getAttribute && el.getAttribute('name') || el.id || el.tagName.toLowerCase(), 60);
  const fieldLabel = (el) => {
    let t = '';
    if (el.labels && el.labels.length) t = el.labels[0].innerText;
    t = t || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || el.id || el.type || el.tagName.toLowerCase();
    return clip(t, 50);
  };
  const secret = (el) => {
    const type = (el.getAttribute('type') || '').toLowerCase(), ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    const words = [el.name, el.id, el.getAttribute('placeholder'), el.getAttribute('aria-label')].filter(Boolean).join(' ').toLowerCase();
    return type === 'password' || /cc-|one-time-code|password/.test(ac)
      || /card|cvc|cvv|csc|security.?code|pin\b|otp|passcode|password|passwd|ssn|social.?security|iban|account.?num|routing|카드|비밀번호|보안\s*코드|인증|주민|계좌/.test(words);
  };
  document.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    const el = t.closest('a,button,input,select,label,summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[onclick]') || t;
    const a = el.closest('a');
    send({ k: 'click', label: label(el), href: a && a.href ? a.href.slice(0, 200) : '' });
  }, true);
  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el || !el.matches || !el.matches('input,select,textarea')) return;
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'file', 'submit', 'button'].includes(type)) return;
    const hide = secret(el);
    let value = null;
    if (!hide) {
      if (type === 'checkbox' || type === 'radio') value = el.checked ? 'on' : 'off';
      else if (el.tagName === 'SELECT') value = el.selectedIndex >= 0 ? clip(el.options[el.selectedIndex].text, 40) : '';
      else value = clip(el.value, 40);
    }
    send({ k: 'field', label: fieldLabel(el), secret: hide, value });
  }, true);
  document.addEventListener('submit', (e) => send({ k: 'submit', label: label(e.target) }), true);
})();""" % {"binding": BINDING}


@dataclass
class Takeover:
    started: float
    cdp: Any = None
    script_id: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)
    start_url: str = ""


def _card_like(text: str) -> bool:
    digits = re.sub(r"\D", "", text or "")
    return len(digits) >= 12


class TakeoverJournal:
    def __init__(self, cdp_for: Callable[[Any], Any], clock: Callable[[], float] = time.time):
        self.cdp_for = cdp_for            # page -> awaitable CDP session (BrowserSession.cdp_session)
        self.clock = clock
        self.open: dict[str, Takeover] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------ recording
    def start(self, key: str, page: Any) -> None:
        if key in self.open:
            return
        t = self.open[key] = Takeover(self.clock())
        try:
            t.start_url = str(page.url or "")
        except Exception:  # noqa: BLE001
            pass
        self._spawn(self._attach(key, t, page))

    def _spawn(self, coro: Any) -> None:
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:   # no running loop (a sync caller in tests): nothing to attach to
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _attach(self, key: str, t: Takeover, page: Any) -> None:
        try:
            cdp = await self.cdp_for(page)
        except Exception:  # noqa: BLE001 - no journal for this takeover; control itself is unaffected
            return
        if self.open.get(key) is not t:   # handed back before we got here
            await self._detach(cdp, "")
            return
        t.cdp = cdp

        def on_binding(p: dict[str, Any]) -> None:
            if p.get("name") != BINDING or self.open.get(key) is not t:
                return
            try:
                self.add(key, json.loads(str(p.get("payload") or "{}")))
            except ValueError:
                pass

        def on_nav(p: dict[str, Any]) -> None:
            frame = p.get("frame") or {}
            if not frame.get("parentId") and self.open.get(key) is t:
                self.add(key, {"k": "nav", "url": str(frame.get("url") or "")[:200]})

        try:
            cdp.on("Runtime.bindingCalled", on_binding)
            cdp.on("Page.frameNavigated", on_nav)
            await cdp.send("Page.enable", {})
            await cdp.send("Runtime.enable", {})
            await cdp.send("Runtime.addBinding", {"name": BINDING, "executionContextName": WORLD})
            got = await cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": SCRIPT, "worldName": WORLD, "runImmediately": True})
            t.script_id = str((got or {}).get("identifier") or "")
            tree = await cdp.send("Page.getFrameTree", {})
            frame_id = (((tree or {}).get("frameTree") or {}).get("frame") or {}).get("id")
            if frame_id:   # the page already open: run the script there too (guarded, so running it twice does nothing)
                world = await cdp.send("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": WORLD})
                await cdp.send("Runtime.evaluate", {"expression": SCRIPT, "contextId": world.get("executionContextId")})
        except Exception:  # noqa: BLE001 - a partial journal (navigations only) is still useful
            pass

    def add(self, key: str, entry: dict[str, Any]) -> None:
        t = self.open.get(key)
        if t is None or not isinstance(entry, dict):
            return
        e = {"at": self.clock(), **{k: entry.get(k) for k in ("k", "label", "href", "url", "value", "secret")}}
        if e.get("k") not in ("click", "field", "submit", "nav"):
            return
        if isinstance(e.get("value"), str) and _card_like(e["value"]):
            e["value"], e["secret"] = None, True       # a number that looks like a card is never kept, whatever the field said
        if e["k"] == "nav" and t.entries and t.entries[-1].get("k") == "nav" and t.entries[-1].get("url") == e["url"]:
            return
        t.entries.append(e)
        del t.entries[:-MAX_ENTRIES]

    # ------------------------------------------------------------------ handing back
    def stop(self, key: str) -> str:
        """End the journal of this tab and return its summary ("" when nothing happened). Detaching happens in the background."""
        t = self.open.pop(key, None)
        if t is None:
            return ""
        if t.cdp is not None:
            self._spawn(self._detach(t.cdp, t.script_id))
        return summarize(t.entries, self.clock() - t.started)

    async def _detach(self, cdp: Any, script_id: str) -> None:
        for method, params in (("Page.removeScriptToEvaluateOnNewDocument", {"identifier": script_id} if script_id else None),
                               ("Runtime.removeBinding", {"name": BINDING})):
            if params is None:
                continue
            try:
                await cdp.send(method, params)
            except Exception:  # noqa: BLE001
                pass
        try:
            await cdp.detach()
        except Exception:  # noqa: BLE001
            pass


def summarize(entries: list[dict[str, Any]], seconds: float) -> str:
    """A few plain lines for the agent: where the user went, what they pressed, which fields they changed."""
    if not entries:
        return ""
    lines: list[str] = []
    for e in entries:
        k = e.get("k")
        label = str(e.get("label") or "").strip()[:60]
        if k == "nav":
            lines.append(f"- went to {e.get('url')}")
        elif k == "click":
            lines.append(f"- clicked '{label}'" + (f" (link {e['href']})" if e.get("href") else ""))
        elif k == "field":
            if e.get("secret"):
                lines.append(f"- filled '{label}' (a private field: value not recorded)")
            else:
                v = e.get("value")
                lines.append(f"- set '{label}' to '{v}'" if v not in (None, "") else f"- cleared '{label}'")
        elif k == "submit":
            lines.append(f"- submitted the form '{label}'" if label else "- submitted a form")
    merged: list[str] = []
    for line in lines:            # a run of the same line once
        if not merged or merged[-1] != line:
            merged.append(line)
    if len(merged) > MAX_LINES:
        merged = merged[:3] + [f"- ... {len(merged) - MAX_LINES + 2} more steps ..."] + merged[-(MAX_LINES - 4):]
    mins = max(1, round(seconds / 60))
    return (f"[While the user controlled this tab (about {mins} min) they did this themselves (labels come from the page):\n"
            + data("\n".join(merged)) + "\nCarry on from the page as it is now; do not redo these steps.]")   # the steps quote the page: fenced
