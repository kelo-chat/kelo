"""Browser tools: drive the Chromium container over the Chrome DevTools Protocol.

Safety:
- Only http(s) URLs; hosts must resolve to public addresses (no loopback, LAN, link-local, Docker
  network, cloud metadata). This is the guard in code; the network layer is separate: the browser container has no route anywhere and
  reaches the internet only through the egress proxy, which refuses private addresses (egress.py, docs/browser.md).
- Page text is returned as *untrusted* content (the agent loop wraps it).
- Looking things up never asks: ordinary clicks (links, tabs, filters, date pickers, "more" buttons) and typing into search fields go
  through without a card. A click or typing step that commits something in the user's name asks (an Allow/Deny card): paying,
  ordering, booking, subscribing, sending or posting, deleting or cancelling, signing up or changing account settings, sending a
  form with personal details, typing personal details or a message. The element is read from the page just before the step to
  judge this (commits.py); the owner can still have every click and typing step ask (ASSISTANT_APPROVE_BROWSER_ACTIONS=1).
- After the user allowed a purchase (checkout_review), the few committing steps that finish it on that same site are covered by
  that approval for a short while (`PURCHASE_WINDOW_SECONDS`, `PURCHASE_WINDOW_ACTIONS`).

Card fields: `fill_card` types a wallet card (number, expiry, CVC, holder, postal code) straight from the server into the checkout form of
the current page and its frames (a payment provider's iframe included when the browser can reach it). The values never come back:
the answer names which fields were filled and the page text with the number removed.

Browser card: after each action the tab is shown in the chat (a `browser` turn event: title, URL, status line and a small
JPEG of the page, at most one per `CARD_MIN_INTERVAL` seconds per tab and never more than `CARD_MAX_BYTES`).

Taking control (live.py drives the input): while a phone holds a tab (`take_control`), every agent step on that tab waits for the
tab to be handed back, at most `CONTROL_WAIT_SECONDS`, and then says "the user is controlling the browser" instead of acting
(`hold_for_user`, wired as the tools' `hold`; `page()` waits the same way for tools that reach the page another way). What the user
does by hand is their own action: no approval cards, and a pop-up they cause is accepted. Handing the tab back closes any purchase
window, so the agent can never ride on clicks the user made, and leaves a page the agent may not open for about:blank.
"""

from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import math
import json
import logging
import re
import socket
import time
from contextvars import ContextVar
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from ..agent import Tool, emit_ui, handover
from ..browser_devices import accept_language
from ..browser_identity import identity_for as _chrome_override
from .child_targets import FRAME_TYPE, ChildTargets
from ..agent.loop import ToolStopped, ToolTimeout
from ..agent.fence import data
from ..data_trail import USES_PARAM
from ..i18n import N_, t
from ..signin_state import CLEAN_READINGS, still_signing_in
from .challenge import (CHECK_TRIES, PROBE_JS, Challenge, blocked, blocked_again_note, blocked_note, check_boxes, detect, handover_note,
                        note_for, signed_out_note, try_note)
from . import humanize as human
from .checkout import REFUSAL, guarded
from .holds import HANDS_WAIT_SECONDS
from .commits import TARGET_JS, click_route, judge_type, kind_of_verdict, label_of, pressable
from .downloads import DownloadArea
from .pacing import SitePacer
from .sites import registrable, same_site
from .work_browser import DOWN_NOTE, SEED_FAILED_NOTE, WorkBrowser, WorkBrowserDown, WorkTab, COOKIE_KEYS, collect_changes, seed_tab, work_tab, seed_signed_out_note
from .task_window import TaskWindow
from .verification import SIGN_IN_SECONDS, Verification, options_note, unconfirmed, verification_note, verification_step, waiting_note
from .page_notes import (LAYER_CHOICES_JS, LAYERS_JS, OVERLAY_JS, OVERLAY_ROUNDS, closed_note, masked_note, masked_values, not_found_note,
                         open_note, overlays, signature)
from .browser_locale import tab_locale
from .page_paths import NO_ELEMENT, PATH_OF_JS, with_path
from .page_tree import controls_only, for_reading, frame_hosts, frame_of, interactive_refs, ref_boxes, ref_name, ref_of, ref_selector, ref_words
from .page_dialogs import DIALOG_ONLY_NOTE, TabDialogs, answer_for, dialog_note, live_view

log = logging.getLogger("assistant.browser")

BLOCKED_HOSTNAMES = {"localhost", "host.docker.internal", "gateway.docker.internal", "metadata.google.internal"}
MAX_TEXT_CHARS = 12_000
MAX_TREE_CHARS = 20_000           # a page reading as its accessibility tree with refs (page_tree.py): longer than plain text
SNAPSHOT_MS = 4_000               # how long reading that tree may take before the page's plain text is shown instead
LOOK_WIDTH = 1024                 # browser_look: a picture of the visible page with a box and its ref on each control
LOOK_BYTES = 400_000
LOOK_MAX_LABELS = 80
STALE_REF = "ref {ref} is not on the page any more: read the page again (browser_read) and use a ref from that reading"
NAV_TIMEOUT_MS = 30_000
CLICK_LOAD_MS = 20_000            # after a click: how long its navigation may take to get a page before the step goes on with what is there
STEP_SECONDS = 45.0               # one browser step (click, type, read, ...) is stopped after this long, not counting a human-check hand-over
OPEN_SECONDS = 60.0               # browser_open: navigation (30) + settling (6) + reading
RESCUE_SECONDS = 3.0              # every DevTools call of the clean-up after a stopped step, and of the user's input, is bounded by this
HUMAN_PAUSE_SECONDS = 8.0         # the pause after a step must end by itself in this time, or the page does not answer
CDP_SECONDS = 5.0                 # a DevTools call or page script of a step that has no reason to take longer
SLOW_STEP_SECONDS = 20.0          # a step running longer than this writes where it is waiting to the log
CHAT_TAB_IDLE_SECONDS = 3600.0   # a phone's (or the owner's) tab is closed after an hour unused, counted from the end of its last turn
TAB_IDLE_SECONDS = CHAT_TAB_IDLE_SECONDS   # older name
SITE_TAB_PREFIX = "task:watch-site:"   # a watcher's background tab for one site (site_fetch): <prefix><registrable domain>
SITE_TAB_IDLE_SECONDS = 600.0    # a site tab unused this long is closed
SITE_TABS_MAX = 3                # at most this many site tabs at a time (the least recently used one makes room)
SITE_FETCH_TIMEOUT_MS = 20_000
TAB_SWEEP_SECONDS = 60.0         # how often the server's sweeper looks for tabs to close (the count of tabs is not limited)
PURCHASE_WINDOW_SECONDS = 180.0   # after an allowed purchase: how long the clicks that finish it need no second card...
PURCHASE_WINDOW_ACTIONS = 4       # ...and how many of them
CANCEL_WINDOW_ACTIONS = 4         # a free cancel of a wrong charge (purchase_result): how many cancel clicks it covers, for PURCHASE_WINDOW_SECONDS
CARD_MIN_INTERVAL = 2.0           # seconds between two browser cards of one tab
CARD_MAX_BYTES = 120_000          # the JPEG in a browser card is never bigger than this (else the card goes without a picture)
CARD_MAX_WIDTH = 720              # pixels; screenshots are scaled down to this width
CONTROL_WAIT_SECONDS = HANDS_WAIT_SECONDS   # an agent step waits this long for the user to hand a tab back, then reports that it could not act (tools/holds.py)
UPLOAD_MAX_BYTES = 50 * 1024**2   # files set into one file input, all together (what the driver can carry into the page)
PDF_MAX_BYTES = 50 * 1024**2      # a page printed to PDF
SITE_STATE_SECONDS = 12 * 3600.0  # a saved site sign-in state (cookies + storage) is kept this long, in memory only
CHECK_NOTICE_SECONDS = 600.0      # the same bot check on the same tab is announced (notification, chat event) at most this often
CHECK_POLL_SECONDS = 3.0          # while a human check waits for the user, the tab is looked at again this often (a cheap structure read)
CHECK_SHOT_WIDTH = 1024           # the picture of a page with a human check the agent may look at (browser_human_check)
CHECK_SHOT_BYTES = 400_000
SETTLE_MS = 6_000                 # after the HTML arrived, how long a page may take to finish loading (pages drawn by scripts) before it is read
OVERLAY_CLICK_MS = 3_000          # how long the click on a covering window's own close button may take
OVERLAY_PAUSE = 0.4               # seconds a closed window gets to go away before the page is looked at again
# browser_open on a tab that already shows that address: nothing is loaded again (the model opened coupang 4 times in 6 minutes and then
# told the owner the page was closed); it gets the page as it is.
SAME_PAGE_NOTE = "This tab is open and already shows {url} ({title}). It was not loaded again; this is the page as it is now."
# A window the page opened (window.open, a target=_blank link) becomes the tab's page: the agent goes on in it.
POPUP_WAIT_SECONDS = 0.4          # after a click: how long a new window may take to show up before the click is taken as staying put
POPUP_NOTE = "[The click opened a new window ({url}); you are in it now (same sign-ins). When it closes you are back on the page that opened it.]"
TIMEOUT_NOTE = ("[The owner's control of this tab timed out (their screen went away), so it came back to you; they may not have finished "
                "what they were doing.]")
TAB_CLOSED_NOTE = "[The tab you were in closed; you are now in tab {id}: {title}. browser_tabs lists this task's tabs.]"
# a browser that restarted (or crashed) under a job: its tab is a new blank page; the first acting step waits until the page is read (job_cuts.py)
BROWSER_LOST_NOTE = ("[The browser restarted and this tab's page was lost; this is a new blank tab. Your last page was {url}. Sign-ins are "
                     "kept. Open that site's home page (browser_open) and get back to the page through the site's own links, then read it "
                     "before you act; a form you were filling may be empty again.]")
LOST_ACT_NOTE = ("[Not done: the browser restarted and this tab is a new blank page. Open the site's home page again (browser_open), get back "
                 "to the page you need through the site's own links, and read it before you click or type.]")
NO_CHANGE_NOTE = ("[The click went through, but nothing changed: same address, same page content and form values, no new window, no "
                  "pop-up. Do not click the same element again. Look at the page below for what holds it (a message, an unticked box, "
                  "a choice still to make, a frame or window it belongs to), try another way (another element), or tell "
                  "the user what you see.]")
# A click that brought up a window on the same page (page_notes.LAYERS_JS): it may confirm what was done, or ask for a choice or another
# step first. Our own lines: done_once.py reads their first words, never the page's.
OPENED_NOTE = ("[The click opened a pop-up on this page. Read it: it may confirm what was done, or ask for a choice or another step first. "
               "A step it asks for that your task forbids or does not mention is the user's choice: do not take it, and do not cancel it "
               "to look for a way around; report needs_input naming that step and what it does.]")
# A click inside the window an earlier click opened, that took none of the committing steps the window offered and closed it
DECLINED_NOTE = ("[The click closed the pop-up without taking the step it offered; nothing was done through that pop-up. Read the page "
                 "before your next step.]")
# After a click or typing on the same page, only what changed is returned (the whole page again is mostly repeated words): the changed
# lines with CHANGE_CONTEXT lines around them, and what left the page. A change bigger than CHANGE_MAX_SHARE of the page: all of it.
CHANGE_CONTEXT = 2
CHANGE_MAX_SHARE = 0.6
CHANGES_HEAD = "[Only what changed on the page since your last reading of it is shown below (browser_read shows the whole page).]"
NO_TEXT_CHANGE = "[The page's text is the same as in your last reading of it (browser_read shows the whole page).]"
FRAME_TEXT_CHARS = 6_000          # text of frames inside the page (an embedded form or checkout), all of them together
_FRAME_MIN_AREA = 4_000           # CSS px²: smaller frames (trackers, ads' pixels) are not read
# What tells "something happened" after a click: the address, the page's markup and every form value (a choice that only sets an
# input changes no text), in the main page and in each frame.
FINGERPRINT_JS = """() => { const b = document.body; if (!b) return location.href; const t = b.innerHTML; let h = 0;
  for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) | 0;
  let s = location.href + '#' + t.length + ':' + h;
  for (const e of document.querySelectorAll('input,select,textarea')) s += '|' + (e.checked ? 1 : 0) + String(e.value || '').slice(0, 40);
  return s; }"""
# the element's centre (the element is brought into view first, by a person's scroll: _bring_into_view) is under something that is not the element, a part of it or what holds it
COVERED_JS = """(e) => { const r = e.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  const h = e.ownerDocument.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return !!h && h !== e && !e.contains(h) && !h.contains(e); }"""
FRAME_TEXT_JS = """() => document.body ? [innerWidth * innerHeight, document.body.innerText || ''] : [0, '']"""


# browser_look's labels: ONE host element with a closed shadow root over the whole viewport (never takes a click), a 2px outline and
# the ref for each box. Removed right after the picture (the handle it returns is removed; no attribute marks the page, addendum 9).
LOOK_OVERLAY_JS = r"""(boxes) => {
  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;margin:0;padding:0;border:0;background:none';
  const root = host.attachShadow({mode: 'closed'});
  const colors = ['#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4', '#008080', '#9a6324', '#800000'];
  boxes.forEach((b, i) => {
    const c = colors[i % colors.length];
    const box = document.createElement('div');
    box.style.cssText = `position:fixed;left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px;outline:2px solid ${c};` +
      'outline-offset:0;box-sizing:border-box;pointer-events:none';
    const tag = document.createElement('div');
    tag.textContent = b.ref;
    tag.style.cssText = `position:fixed;left:${Math.max(0, b.x)}px;top:${Math.max(0, b.y - 14)}px;background:${c};color:#fff;` +
      'font:bold 11px/14px monospace;padding:0 3px;border-radius:2px;pointer-events:none;white-space:nowrap';
    root.append(box, tag);
  });
  (document.body || document.documentElement).appendChild(host);
  return host;
}"""
# where a frame's own viewport starts inside the frame that holds its iframe element (border and padding included)
FRAME_ORIGIN_JS = """(e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
  return [r.left + e.clientLeft + (parseFloat(s.paddingLeft) || 0), r.top + e.clientTop + (parseFloat(s.paddingTop) || 0)]; }"""


def _devtools_version(ip: str, port: int) -> str:
    """The DevTools version document of the browser (its WebSocket address names 127.0.0.1 inside the container)."""
    import urllib.request
    with urllib.request.urlopen(f"http://{ip}:{port}/json/version", timeout=3) as r:
        return r.read().decode()


def _site_of(url: str) -> str:
    from .sites import registrable
    host = (urlsplit(url or "").hostname or "").lower()
    return registrable(host) if host else ""


TARGET_READ_MS = 1_500            # how long reading a click or typing target may take before its selector words are judged instead
LISTENER_READ_MS = 1_200          # how long reading the target's click listeners (DevTools) may take before only its inline code is judged
# listeners that run on a click (read to tell a click that only opens a window or a page from one that sends something)
_CLICK_EVENTS = frozenset({"click", "mousedown", "mouseup", "pointerdown", "pointerup", "touchstart", "touchend", "auxclick"})
# the listeners of the element at a path (%s) of a click-like type (%s), as the source of each handler; run by DevTools (a Runtime.evaluate
# with the command-line API, getEventListeners), so no page global is set
_LISTENERS_JS = """(() => {
  const hit = document.querySelectorAll(%s);
  if (hit.length !== 1) return [];
  const types = %s, out = [];
  let all = {};
  try { all = getEventListeners(hit[0]); } catch (e) { return []; }
  for (const type of Object.keys(all)) {
    if (!types.includes(type)) continue;
    for (const l of (all[type] || []).slice(0, 20)) {
      try { out.push(Function.prototype.toString.call(l.listener).slice(0, 4000)); } catch (e) {}
    }
  }
  return out.slice(0, 20);
})()"""

# Which phone is asking. Set per request, so each device keeps its own tab and two phones never click in each other's page.
browser_owner: ContextVar[str] = ContextVar("browser_owner", default="owner")


POINT_TOOLS = ("browser_click_at", "browser_drag")   # steps at a point of the last browser_look picture (no element named)
LOOK_FIRST = "look at the page first (browser_look)"
DRAG_POINTS = (2, 20)


def _point_arg(p: Any) -> tuple[float, float]:
    if not isinstance(p, dict):
        raise ValueError("a point is {x, y}")
    x, y = float(p.get("x")), float(p.get("y"))
    if not (0 <= x <= 10_000 and 0 <= y <= 10_000):
        raise ValueError("x and y are pixels of the picture")
    return x, y


def step_points(tool: str, args: dict[str, Any]) -> list[tuple[float, float]]:
    """The picture points of a browser_click_at (one) or browser_drag (its path) step; ValueError when they are not usable."""
    if tool == "browser_click_at":
        return [_point_arg(args)]
    path = args.get("path")
    if not isinstance(path, list) or not DRAG_POINTS[0] <= len(path) <= DRAG_POINTS[1]:
        raise ValueError(f"path must list {DRAG_POINTS[0]} to {DRAG_POINTS[1]} points")
    return [_point_arg(p) for p in path]


def step_key(tool: str, args: dict[str, Any]) -> str:
    """What a judged step is remembered under (judged, judged_info): the selector, or "point:x,y" (picture pixels) for a point
    step (a drag: its first point)."""
    if tool not in POINT_TOOLS:
        return str(args.get("selector", ""))
    try:
        x, y = step_points(tool, args)[0]
    except (TypeError, ValueError):
        return "point:?"
    return f"point:{round(x)},{round(y)}"


def one_run_tab(key: str) -> bool:
    """A tab that belongs to one run (an automatic run or a task handed over): closed as soon as that run is over."""
    return key.startswith(("schedule:", "task:")) and not is_site_tab(key)


def is_site_tab(key: str) -> bool:
    """A watcher's site tab (site_fetch): it stays open between checks and closes after SITE_TAB_IDLE_SECONDS unused."""
    return key.startswith(SITE_TAB_PREFIX)


def site_tab_key(url: str) -> str:
    return SITE_TAB_PREFIX + (_site_of(url) or "site")


def same_address(a: str, b: str) -> bool:
    """Whether two addresses are the same page: same scheme, host (a leading "www." ignored), port, path (a trailing slash ignored) and
    query; the #fragment is ignored. False for anything that is not http(s)."""
    def parts(url: str) -> tuple[str, str, int | None, str, str] | None:
        p = urlsplit((url or "").strip())
        if p.scheme.lower() not in ("http", "https") or not p.hostname:
            return None
        try:
            port = p.port
        except ValueError:
            return None
        return (p.scheme.lower(), p.hostname.lower().removeprefix("www."), None if port in (None, 80, 443) else port,
                p.path.rstrip("/") or "", p.query)
    one, two = parts(a), parts(b)
    return one is not None and one == two


def _origin_of(url: str) -> str:
    p = urlsplit(url or "")
    return f"{p.scheme.lower()}://{(p.hostname or '').lower()}" + (f":{p.port}" if p.port and p.port not in (80, 443) else "")


# A request made inside the page, as the page itself would make it: the tab's cookies and sign-ins ride along, nothing is read out of them.
SITE_FETCH_JS = r"""async (a) => {
  const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), a.timeout);
  try {
    const r = await fetch(a.url, { method: a.method, headers: a.headers, body: a.method === 'GET' ? undefined : a.body,
                                   credentials: 'include', redirect: 'follow', signal: ctl.signal });
    const text = await r.text(); const h = {};
    for (const [k, v] of r.headers.entries()) if (/^(server|cf-mitigated|x-amzn-waf-action|x-datadome|x-dd-|x-px)/i.test(k)) h[k.toLowerCase()] = String(v).slice(0, 200);
    return { status: r.status, contentType: r.headers.get('content-type') || '', text: text.slice(0, a.cap), headers: h, url: r.url };
  } finally { clearTimeout(timer); }
}"""


class BrowserPolicyError(Exception):
    pass


class NoSignInForm(BrowserPolicyError):
    """fill_login found no sign-in field on the open page; browser_login then reaches the form by a click (card p-signin-gaps)."""


class LoginOptions(BrowserPolicyError):
    """The sign-in form has unticked boxes or switches and the agent has not chosen yet: nothing was typed (card p-signin-gaps)."""

    def __init__(self, options: list[tuple[str, str]]):
        super().__init__("the sign-in form has unticked options; nothing was typed")
        self.options = options


class BrowserControlled(Exception):
    """The user holds this tab (they took control on their phone); the agent may not act on it now."""


# a chat reply's step on a tab the owner holds is told at once (the reply says so; the step goes on only after the owner hands the tab back)
CHAT_CONTROL_WAIT_SECONDS = 5.0
hold_patience: ContextVar[float | None] = ContextVar("hold_patience", default=None)   # set by turn_engine for a chat reply
CONTROLLED_CHAT_NOTE = ("[Not done: the user has taken over this browser tab and not handed it back. Do not use browser tools until they do. "
                        "In your reply, say you are waiting for them to hand the browser back and will carry on then (or ask what they did). "
                        "Stop here.]")
CONTROLLED_NOTE = ("[Not done: the user is controlling the browser right now (they took over this tab on their phone) and has not handed "
                   "it back for {minutes} minutes. Do not use browser tools until they do: tell the user you will carry on when they "
                   "hand the browser back (or ask what they did), and stop here.]")


TOOK_OVER_NOTE = ("[The owner took over this tab, so this step was stopped before it finished. Your browser steps on this tab wait until "
                  "the owner presses hand back; that can take long. Then read the page again before you act: they may have changed it.]")
MOVED_NOTE = ("[Not done: the owner changed this tab by hand before handing it back. Read it again (browser_read or browser_look) and "
              "decide the next step from what it shows now.]")
STEP_TIMEOUT_NOTE = ("[The browser step did not finish in {seconds} s: the page at {host} did not answer (a navigation that never "
                     "completed, or a page that stopped responding). I stopped it and left the page as it is. If this step pressed a button "
                     "that pays, orders, books or sends, it may have gone through: never press it again; browser_read and check the "
                     "account's orders first. Otherwise try browser_read, or tell the user the site does not respond.]")
PACE_NOTE = ("[Not submitted: this site blocks quick repeated searches and the last one on {host} was only seconds ago. Wait about "
             "{seconds} s (use the time for something else), then submit again.]")
PACE_MARGIN_SECONDS = 10.0        # a paced wait must leave this much of the step's time to do the step itself
STILL_LOADING_NOTE = "[The page is still loading after {seconds} s; stopped. What is shown is the page as it is now.]"
COVERED_NOTE = ("[The element is covered by {layer}; I could not close it. Ask the user to close it with \"take over the browser\", "
                "or try another element.]")


async def cdp_send(cdp: Any, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
    """One DevTools call that gives up after `timeout` seconds (the page's main thread answers input and scripts, and a page that is
    stuck never does): TimeoutError, which callers treat like any other failed call."""
    return await asyncio.wait_for(cdp.send(method, params or {}), CDP_SECONDS if timeout is None else timeout)


async def evaluate(page: Any, js: str, arg: Any = None, timeout: float | None = None) -> Any:
    """page.evaluate that gives up after `timeout` seconds (a page with a navigation that never answers hangs every script)."""
    limit = CDP_SECONDS if timeout is None else timeout
    if arg is None:
        return await asyncio.wait_for(page.evaluate(js), limit)
    return await asyncio.wait_for(page.evaluate(js, arg), limit)


def await_chain(task: Any) -> str:
    """Where a task is waiting, as 'a > b > c' (the coroutine names down its chain of awaits): for the log of a slow step."""
    names: list[str] = []
    coro = task.get_coro() if hasattr(task, "get_coro") else None
    while coro is not None and len(names) < 12:
        names.append(getattr(coro, "__qualname__", None) or getattr(coro, "__name__", None) or type(coro).__name__)
        coro = getattr(coro, "cr_await", None) or getattr(coro, "gi_yieldfrom", None)
        if coro is not None and not hasattr(coro, "cr_await") and not hasattr(coro, "gi_yieldfrom"):
            names.append(type(coro).__name__)
            break
    return " > ".join(names)


@dataclass(slots=True)
class CardFill:
    """What came of typing a card: which fields were filled (and where), what was not found, frames that could not be reached, whether
    the page now asks for extra authentication, and the page text afterwards with the card number removed."""
    filled: list[str]
    missing: list[str]
    unreachable: list[str]
    auth: str
    text: str


@dataclass(slots=True)
class SignIn:
    """What came of typing a login: the page afterwards (values removed) and a reading of whether the site accepted it."""
    text: str
    outcome: str      # success | failed | code_needed | blocked | unknown
    detail: str
    step: str = ""    # code_needed: the verification step the page asks for, "approve" | "code" (verification.py)


def classify_sign_in(*, before_url: str, after_url: str, password_visible: bool, text: str = "", challenge: str = "", otp_boxes: int = 0,
                     invalid_marked: bool = False) -> tuple[str, str]:
    """Read a page after a sign-in was submitted, by structure only (docs/no-word-lists.md phase C): a bot check found by structure, a
    one-time-code input (autocomplete=one-time-code), the sign-in form still shown and the page marking a field invalid or raising an
    alert (aria-invalid, role=alert), the password field gone (the caller then checks the page is past every sign-in step). A reading, not a proof: when nothing is clear it says
    unknown, and what is stored is never thrown away because of a reading (a 'failed' only stops the assistant from trying again until
    the user says so). `text` is not read. A sign-in to approve on a phone has no input to see: verification.verification_step reads it
    after this, and `signin_state` demands positive evidence before a hand-over counts as signed in."""
    if challenge:
        return "blocked", f"the site shows a human check ({challenge})"
    if otp_boxes > 0 and not password_visible:
        return "code_needed", "the site accepted the password and asks for a verification code"
    if password_visible and invalid_marked:
        return "failed", "the page still shows the sign-in form and marks a field as wrong"
    if not password_visible:
        return "success", ("the sign-in page was left" if after_url.split("#")[0] != before_url.split("#")[0]
                           else "the password field is gone")   # the caller still demands positive evidence (signin_state.still_signing_in)
    return "unknown", "could not tell whether the site accepted the login"


_PAGE_STATE = """() => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e); return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none'; };
  return { pw: [...document.querySelectorAll('input[type=password]')].some(shown), text: (document.body ? document.body.innerText : '').slice(0, 20000),
           otp: [...document.querySelectorAll('input[autocomplete~="one-time-code"]')].filter(shown).length,
           invalid: [...document.querySelectorAll('[aria-invalid="true"], [role="alert"]')].some(shown) };
}"""

# Marks the visible username and password inputs of the page (nothing else is read), so they can be filled by the server.
_TAG_LOGIN_FIELDS = with_path("""() => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none' && !e.disabled && !e.readOnly; };
  const kind = (e) => (e.getAttribute('type') || '').toLowerCase();
  const inputs = [...document.querySelectorAll('input')].filter(shown);
  const textish = (e) => ['', 'text', 'email', 'tel', 'search'].includes(kind(e));
  const p = inputs.findIndex((e) => kind(e) === 'password');
  let u = -1;
  if (p >= 0) { for (let k = p - 1; k >= 0; k--) { if (textish(inputs[k])) { u = k; break; } } }
  else {
    // no password field: only a field that says it takes the account name (a username-first sign-in step), never a search box or
    // any other text field of the page (2026-11-19: a saved login's name was typed into a shop's search field and sent)
    const ac = (e) => (e.getAttribute('autocomplete') || '').toLowerCase().split(/\\s+/);
    const searchy = (e) => kind(e) === 'search' || e.getAttribute('role') === 'searchbox' || !!e.closest('[role=search], search');
    u = inputs.findIndex((e) => textish(e) && !searchy(e) && (kind(e) === 'email' || ac(e).includes('username') || ac(e).includes('email')));
  }
  return { u: u >= 0, p: p >= 0, p_count: inputs.filter((e) => kind(e) === 'password').length,
           u_path: u >= 0 ? keloPath(inputs[u]) : '', p_path: p >= 0 ? keloPath(inputs[p]) : '' };
}""")
# What the tagged password input is right now (the checks at the moment of typing): how many are tagged, whether it is an input of
# type password that is still the type it had when it was tagged, whether it is rendered masked, and the LENGTH of its value (never the value).
_LOGIN_STATE = with_path("""(path) => {
  const els = [...document.querySelectorAll(path)];
  const e = els[0];
  if (!e) return { n: 0 };
  const c = getComputedStyle(e);
  const sec = c.webkitTextSecurity || c.textSecurity || '';
  return { n: els.length, input: e.tagName === 'INPUT', type: (e.type || '').toLowerCase(),
           masked: sec !== 'none', length: String(e.value || '').length };
}""")

# Marks the card fields of a checkout form in one frame (nothing is read from them but their kind, size and, for an expiry <select>, its
# options). Recognised by their autocomplete tokens (cc-number, cc-exp, cc-exp-month, cc-exp-year, cc-csc, cc-name, postal-code; WHATWG) and, for
# a field that says nothing, by `given`: {ref: kind} that the page judge (page_judge.py) named after reading the field's label (never a word
# list here). `open` lists the fields left open: {ref, tag, type, inputmode, maxlength, placeholder, label, options}. A card number split over
# several boxes is marked number-0, number-1, ...
_TAG_CARD_FIELDS = with_path(r"""(given) => {
  given = given || {};
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none' && !e.disabled && !e.readOnly; };
  const skip = ['hidden', 'checkbox', 'radio', 'submit', 'button', 'image', 'file', 'reset', 'range', 'color', 'date', 'email', 'search', 'url'];
  const els = [...document.querySelectorAll('input, select')].filter((e) => shown(e) && !skip.includes((e.getAttribute('type') || '').toLowerCase()));
  const label = (e) => {
    const parts = [e.getAttribute('placeholder'), e.getAttribute('aria-label'), e.getAttribute('title'), e.name, e.id];
    if (e.labels) for (const l of e.labels) parts.push(l.innerText);
    const by = e.getAttribute('aria-labelledby');
    if (by) by.split(/\s+/).forEach((id) => { const x = document.getElementById(id); if (x) parts.push(x.innerText); });
    if (!(e.labels && e.labels.length) && !e.getAttribute('placeholder') && !e.getAttribute('aria-label') && e.parentElement) {
      const near = (e.parentElement.innerText || '').trim();   // a caption written next to the box, without a <label>
      if (near.length <= 40) parts.push(near);
    }
    return parts.filter(Boolean).join(' ').slice(0, 80);
  };
  const ac = (e) => (e.getAttribute('autocomplete') || '').toLowerCase();
  const kindOf = (e, i) => {
    const a = ac(e);
    if (/cc-number/.test(a)) return 'number';
    if (/cc-csc/.test(a)) return 'cvc';
    if (/cc-exp-month/.test(a)) return 'exp_month';
    if (/cc-exp-year/.test(a)) return 'exp_year';
    if (/cc-exp/.test(a)) return 'exp';
    if (/cc-name/.test(a)) return 'name';
    if (/postal-code/.test(a)) return 'postal';
    const g = String(given['f' + i] || '').replace(/-\d$/, '');
    return ['number', 'cvc', 'exp', 'exp_month', 'exp_year', 'name', 'postal'].includes(g) ? g : '';
  };
  const found = els.map((e, i) => [e, kindOf(e, i)]);
  const card = found.some(([e, k]) => ['number', 'cvc', 'exp', 'exp_month', 'exp_year'].includes(k) && (k === 'number' || k === 'cvc' || /cc-/.test(ac(e))));
  const max = (e) => (e.maxLength && e.maxLength > 0 ? e.maxLength : 0);
  // a number split over boxes: several 'number' fields, or one short one followed by unlabeled short boxes
  let numbers = found.filter(([e, k]) => k === 'number' && e.tagName !== 'SELECT').map(([e]) => e);
  if (numbers.length === 1 && max(numbers[0]) && max(numbers[0]) <= 6) {
    const at = els.indexOf(numbers[0]);
    for (let i = at + 1; i < els.length && numbers.length < 4; i++) {
      if (found[i][1] || !max(els[i]) || max(els[i]) > 6 || els[i].tagName === 'SELECT') break;
      numbers.push(els[i]);
    }
  }
  if (numbers.length > 1 && !numbers.every((e) => max(e) && max(e) <= 7)) numbers = numbers.slice(0, 1);   // two whole-number fields: use the first
  if (numbers.length === 1 && max(numbers[0]) && max(numbers[0]) >= 8 && max(numbers[0]) < 13) numbers = [];   // no card number is this short
  const out = { any: false, card, number: [], exp: null, exp_month: null, exp_year: null, cvc: false, name: false, postal: false, open: [], paths: {} };
  const marked = new Set();
  const mark = (e, tag) => { marked.add(e); out.paths[tag] = keloPath(e); };
  numbers.forEach((e, i) => { mark(e, 'number-' + i); out.number.push(max(e)); });
  const first = (k) => { const hit = found.find(([e, kk]) => kk === k && !marked.has(e)); return hit ? hit[0] : null; };
  const opts = (e) => (e.tagName === 'SELECT' ? [...e.options].map((o) => [o.value, (o.text || '').trim()]).slice(0, 60) : null);
  for (const k of ['exp', 'exp_month', 'exp_year']) {
    const e = first(k);
    if (!e) continue;
    mark(e, k);
    out[k] = { max: max(e), placeholder: (e.getAttribute('placeholder') || '').toLowerCase(), options: opts(e) };
  }
  const cvc = first('cvc');
  if (cvc && cvc.tagName !== 'SELECT') { mark(cvc, 'cvc'); out.cvc = true; }
  const extra = (e) => e && !e.value && (card || /cc-|billing/.test(ac(e)));
  const name = first('name'), postal = first('postal');
  if (extra(name)) { mark(name, 'name'); out.name = true; }
  if (extra(postal)) { mark(postal, 'postal'); out.postal = true; }
  out.any = out.number.length > 0 || !!out.exp || !!out.exp_month || !!out.exp_year || out.cvc;
  if (!out.any) { out.name = out.postal = false; out.paths = {}; marked.clear(); }
  // the fields still open (no autocomplete token, not named by `given`): what the page judge reads, in a frame that has a field the
  // shape of a card number or a code and no card fields recognised yet
  if (els.length >= 3 && !(out.number.length && (out.exp || out.exp_month) && out.cvc)) {
    els.forEach((e, i) => { if (!marked.has(e) && !found[i][1] && !/\S/.test(ac(e).replace(/off/, ''))) {
      out.open.push({ ref: 'f' + i, tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(), inputmode: e.getAttribute('inputmode') || '', maxlength: max(e),
                      placeholder: (e.getAttribute('placeholder') || '').slice(0, 40), label: label(e),
                      options: opts(e) ? opts(e).slice(0, 4).map((o) => o[1]).concat([String(opts(e).length) + ' options']) : [] });
    } });
    out.open = out.open.slice(0, 24);
  }
  return out;
}""")

# The shipping fields of a checkout (browser_fill_shipping): by their autocomplete tokens (WHATWG) and by their type (a telephone box), else by
# `given`: {ref: kind} that the page judge (page_judge.py) named after reading the field's label, name and placeholder. No word list of any
# language is read here. Billing-only, login, e-mail, card, coupon and search fields are never taken: the autocomplete tokens and input types
# say so, and the judge names only the fields it is sure about. `open` lists the fields left open (ref, tag, type, inputmode, maxlength,
# placeholder, label, options) for the judge.
_TAG_ADDRESS_FIELDS = with_path(r"""(given) => {
  given = given || {};
  const vis = (e) => { const r = e.getBoundingClientRect(); const st = getComputedStyle(e);
                       return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const label = (e) => [e.labels && e.labels.length ? Array.from(e.labels).map((l) => l.innerText).join(' ') : '', e.getAttribute('aria-label'),
                        e.getAttribute('placeholder'), e.getAttribute('name'), e.id, e.getAttribute('title')].filter(Boolean).join(' ').slice(0, 80);
  const AC = {'name': 'name', 'given-name': 'given', 'family-name': 'family', 'tel': 'tel', 'tel-national': 'tel', 'tel-local': 'tel',
              'tel-country-code': 'tel_country', 'postal-code': 'postal', 'address-line1': 'line1', 'address-line2': 'line2',
              'address-line3': 'line2', 'street-address': 'street', 'address-level1': 'level1', 'address-level2': 'level2',
              'address-level3': 'level3', 'country': 'country', 'country-name': 'country'};
  const KINDS = ['name', 'given', 'family', 'tel', 'tel_country', 'postal', 'line1', 'line2', 'street', 'level1', 'level2', 'level3', 'country', 'note'];
  const OTHER_AC = /(^|\s)(username|email|new-password|current-password|one-time-code|cc-[a-z-]+|bday[a-z-]*|organization[a-z-]*|url|photo|impp)(\s|$)/;
  const open = [];
  const found = {};
  const paths = {};
  let n = 0;
  let i = -1;
  for (const e of document.querySelectorAll('input, select, textarea')) {
    i++;
    const type = (e.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'password', 'email', 'search', 'checkbox', 'radio', 'submit', 'button', 'image', 'file', 'reset'].includes(type)) continue;
    if (e.disabled || !vis(e)) continue;
    const acs = (e.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/).filter((x) => x && !x.startsWith('section-'));
    if (acs.includes('billing') || acs.some((x) => x.startsWith('cc-')) || OTHER_AC.test(acs.join(' '))) continue;
    let kind = AC[acs.filter((x) => x !== 'shipping').pop() || ''];
    if (!kind && type === 'tel') kind = 'tel';
    if (!kind) {
      const g = String(given['f' + i] || '').replace(/-\d$/, '');
      kind = KINDS.includes(g) ? g : '';
      if (!kind && !String(given['f' + i] || '')) open.push({ ref: 'f' + i, tag: e.tagName.toLowerCase(), type, inputmode: e.getAttribute('inputmode') || '', maxlength: e.maxLength > 0 ? e.maxLength : 0,
        placeholder: (e.getAttribute('placeholder') || '').slice(0, 40), label: label(e),
        options: e.tagName === 'SELECT' ? Array.from(e.options).slice(0, 4).map((o) => (o.text || '').trim()).concat([String(e.options.length) + ' options']) : [] });
    }
    if (!kind) continue;
    const list = found[kind] = found[kind] || [];
    const tag = `${kind}-${list.length}`;
    paths[tag] = keloPath(e);
    list.push({tag, path: paths[tag], el: e.tagName.toLowerCase(), max: e.maxLength > 0 ? e.maxLength : 0, ph: e.getAttribute('placeholder') || '',
               ro: !!(e.readOnly), opts: e.tagName === 'SELECT' ? Array.from(e.options).map((o) => [o.value, (o.text || '').trim()]) : []});
    n++;
  }
  return {n, found, open: open.slice(0, 30), paths};
}""")
_SET_READONLY = r"""([path, value]) => { const e = document.querySelector(path); if (!e) return false;
  const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, value);
  e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); return true; }"""


def split_by(value: str, sizes: list[int]) -> list[str]:
    """A value spread over boxes of these lengths (the last box takes the rest): "01012345678" over 3/4/4 boxes."""
    out, rest = [], value
    for i, size in enumerate(sizes):
        take = len(rest) if i == len(sizes) - 1 or not size else size
        out.append(rest[:take])
        rest = rest[take:]
    return out


def phone_for(field: dict[str, Any], national: str, e164: str) -> str:
    """The phone as one field wants it: E.164 when its placeholder starts with +, the placeholder's grouping when it shows one
    ("010-0000-0000"), else the national digits."""
    ph = str(field.get("ph") or "")
    if ph.strip().startswith("+"):
        return e164
    groups = [len(g) for g in re.findall(r"[0-9Xx#*_]+", ph)] if re.search(r"\d|[Xx#]{2}", ph) else []
    if len(groups) >= 2 and sum(groups) == len(national):
        return "-".join(split_by(national, groups))
    return national


def pick_select(options: list[list[str]], wants: list[str]) -> str | None:
    """The option value of a select (a region or a country list) that matches one of `wants` by value or by its text."""
    low = [w.casefold() for w in wants if w]
    for value, text in options:
        if value.casefold() in low or text.casefold() in low:
            return value
    for value, text in options:
        if any(w and (w == text.casefold().split(" (")[0] or (len(w) > 3 and w in text.casefold())) for w in low):
            return value
    return None


# Whether a payment page asks the payer to prove it is them (the card's own app, 3-D Secure, a bank's code), by STRUCTURE (docs/no-word-lists.md,
# hc-pagefields): a visible one-time-code input in the page or one of its frames, or the tab left the shop's site while a purchase was allowed
# (the card network's or the bank's own page). No scheme name or word of a language is read. Then the user must act on their phone: the
# assistant says so and waits (payment_step_wait reads the page with the page judge), instead of clicking around.
_EXTRA_AUTH_JS = r"""() => [...document.querySelectorAll('input')].some((e) => {
  const r = e.getBoundingClientRect(), c = getComputedStyle(e);
  if (!(r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none') || e.disabled) return false;
  const ac = (e.getAttribute('autocomplete') || '').toLowerCase();
  if (ac.split(/\s+/).includes('one-time-code')) return true;
  return ['', 'text', 'tel', 'number'].includes((e.getAttribute('type') || '').toLowerCase()) && /numeric|decimal/.test(e.getAttribute('inputmode') || '')
    && e.maxLength >= 5 && e.maxLength <= 8 && !/(^|\s)(cc-[a-z-]+|tel[a-z-]*|postal-code)(\s|$)/.test(ac);
})"""


EXTRA_AUTH_NOTE = ("[The page asks for extra authentication of the payment ({words}). If this payment was approved with checkout_review, "
                   "call payment_step_wait now: it shows the user one card with the code, QR code and time left and WAITs for their answer. "
                   "Otherwise tell the user to complete it on their phone (the card company's app, bank app or code) and WAIT for them to "
                   "say it is done. Do not click, retry or fill anything meanwhile; afterwards read the page once with browser_read and report.]")


# Marks the box(es) of a one-time code on the page: one box (autocomplete=one-time-code, or a code / OTP / verification-number field), or a row of
# one-character boxes (4-8 of them). `sel` (optional): a selector the agent named instead. Nothing is read from the boxes.
_TAG_CODE_FIELDS = with_path(r"""(sel) => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none' && !e.disabled && !e.readOnly; };
  const okType = (e) => ['', 'text', 'tel', 'number', 'password'].includes((e.getAttribute('type') || '').toLowerCase());
  const inputs = [...document.querySelectorAll('input')].filter((e) => shown(e) && okType(e));
  if (sel) {
    let picked = [];
    try { picked = [...document.querySelectorAll(sel)].filter((e) => e.tagName === 'INPUT' && shown(e)); } catch (err) { picked = []; }
    const got = picked.slice(0, 8);
    return { boxes: got.length, paths: got.map((e) => keloPath(e)) };
  }
  // a code box by STRUCTURE only: autocomplete one-time-code, else the one visible box shaped like a code (numeric input mode or a short
  // maxlength of 4-8, no autocomplete token that names something else). Two or more such boxes that are not a one-character row: not guessed.
  const named = /(^|\s)(username|email|tel[a-z-]*|cc-[a-z-]+|postal-code|street-address|address-[a-z0-9-]+|name|given-name|family-name|current-password|new-password|bday[a-z-]*|country[a-z-]*|organization[a-z-]*)(\s|$)/;
  const shaped = inputs.filter((e) => (e.getAttribute('type') || '').toLowerCase() !== 'password' && !named.test((e.getAttribute('autocomplete') || '').toLowerCase())
    && ((/numeric|decimal/.test(e.getAttribute('inputmode') || '') && e.maxLength !== 1) || (e.maxLength >= 4 && e.maxLength <= 8)));
  const one = inputs.find((e) => (e.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code') || (shaped.length === 1 ? shaped[0] : null);
  const singles = inputs.filter((e) => e.maxLength === 1);
  if (singles.length >= 4 && singles.length <= 8 && (!one || one.maxLength === 1)) {
    return { boxes: singles.length, paths: singles.map((e) => keloPath(e)) };
  }
  if (one) return { boxes: 1, paths: [keloPath(one)] };
  return { boxes: 0, paths: [] };
}""")


_CHECK_HEADERS = {"server", "cf-mitigated", "cf-ray", "x-amzn-waf-action", "x-datadome", "x-dd-b", "x-datadome-cid", "x-amz-cf-id", "x-cache"}
BLOCK_LIMIT = 2                   # access-denied answers from one host in one task, then it is not tried again...
BLOCK_SECONDS = 600.0             # ...for this long (a new message of the user starts a new task, and with it a clean slate)
SIGN_IN_PASS_SECONDS = 15 * 60.0   # after a refusal of a browser not signed in to the site: a sign-in pass (brief B2)
KEEP_CHOICE_SECONDS = 120.0        # browser_login's second call (the stay-signed-in choice) is part of the approved sign-in this long
TOUCH_SECONDS = 10 * 60.0   # a site's sign-in row is marked used at most this often (brief B1)
REFUSAL_PAUSE_SECONDS = 6 * 3600.0   # a site that refused the browser is not loaded again for this long (per registrable site, the whole server)
REFUSAL_PAUSE_NOTE = ("[{site} refused Kelo's browser a short while ago. Kelo's browser will not load {site} again for about {minutes} minutes, "
                      "and it does not retry on its own. Use another source only if the task allows it; otherwise say plainly that {site} refused Kelo's browser and that the user can take over the browser to sign in or get past it.]")
OTP_NOTE = ("[The page asks for a one-time code. The user's mailbox is connected: find_login_code(site=...) looks for a code that site "
            "mailed. During a sign-in you started on that site it may type it by itself (the user's setting); otherwise it asks the user "
            "before typing it. If there is none, ask the user.]")


_READ_STORAGE = "() => Object.keys(localStorage).slice(0, 500).map((k) => [k, String(localStorage.getItem(k)).slice(0, 100000)])"


def site_of(site: str) -> str:
    """A bare host name from a URL or a host ("https://www.example.com/x" -> "www.example.com")."""
    text = (site or "").strip().lower()
    host = (urlsplit(text).hostname or "") if "://" in text else text.split("/")[0].split(":")[0]
    host = host.strip(".")
    if not host or "." not in host or _is_ip(host) or not re.fullmatch(r"[a-z0-9.\-]+", host):
        raise BrowserPolicyError(f"{site!r} is not a site name (example: example.com)")
    return host


def cookie_for(domain: str, host: str) -> bool:
    """Whether a cookie of `domain` belongs to the site `host`: the same host, a parent domain of it, or one of its subdomains
    (a leading "www." of the site does not narrow it)."""
    d, h = domain.lower().lstrip("."), host.lower().removeprefix("www.")
    return bool(d) and (d == h or d.endswith("." + h) or h.endswith("." + d) and d.count(".") >= 1)


def code_mask(code: str) -> str:
    """How a one-time code is shown where it may be kept (logs, notifications): all but the last two characters hidden."""
    c = str(code or "")
    return "•" * max(0, len(c) - 2) + c[-2:] if len(c) > 2 else "•" * len(c)


async def check_url(
    url: str,
    *,
    allow_private: bool = False,
    resolver: Callable[..., list] = socket.getaddrinfo,
    internal_names: frozenset[str] = frozenset(),
    lookup: bool = True,
) -> None:
    """`lookup=False`: a name is not looked up here (the VPN connector is on: the VPN resolves it, and a lookup here would tell the local
    resolver every site visited). The checks that need no lookup stay: scheme, internal names, IP literals."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise BrowserPolicyError(f"only http and https URLs are allowed (got {parts.scheme or 'none'!r})")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise BrowserPolicyError("URL has no host")
    if allow_private:
        return
    if host in BLOCKED_HOSTNAMES or host in internal_names or "." not in host and not _is_ip(host):
        raise BrowserPolicyError(f"host {host!r} is internal and not allowed")
    if not lookup and not _is_ip(host):
        return
    try:
        infos = await asyncio.to_thread(resolver, host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError:
        raise BrowserPolicyError(f"could not resolve {host!r}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise BrowserPolicyError(f"{host!r} resolves to a non-public address ({ip}); blocked")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def _no_auto_code(_host: str) -> str | None:
    return None


async def _viewport_width(page: Any) -> float:
    """The page's viewport width in CSS px (what a screenshot shows), or 0 when it cannot be read."""
    try:
        vs = page.viewport_size
        if vs and vs.get("width"):
            return float(vs["width"])
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(await page.evaluate("() => window.innerWidth") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def shrink_jpeg(raw: bytes, max_width: int = CARD_MAX_WIDTH, cap: int = CARD_MAX_BYTES) -> tuple[bytes | None, int, int]:
    """Scale a screenshot down to `max_width` and re-encode it until it fits in `cap` bytes. (None, 0, 0) when it never does."""
    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.width > max_width:
        img = img.resize((max_width, max(1, round(img.height * max_width / img.width))))
    for quality in (60, 45, 30):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() <= cap:
            return buf.getvalue(), img.width, img.height
    if img.width > 360:   # a very busy page: one more try at half the size
        img = img.resize((img.width // 2, max(1, img.height // 2)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=30, optimize=True)
        if buf.tell() <= cap:
            return buf.getvalue(), img.width, img.height
    return None, 0, 0


def expiry_text(m: int, y: int, size: int, placeholder: str) -> str:
    """The expiry as one field wants it: MMYY, MM/YY, MM / YY or MM/YYYY, from the field's length and placeholder."""
    p = placeholder.lower()
    if size == 4 or (p and "/" not in p and "yy" in p and "yyyy" not in p):
        return f"{m:02d}{y % 100:02d}"
    if size >= 7 or "yyyy" in p:
        return f"{m:02d}/{y}"
    if " / " in p:
        return f"{m:02d} / {y % 100:02d}"
    return f"{m:02d}/{y % 100:02d}"


def pick_option(options: list[list[str]], want: int, *, year: bool) -> str | None:
    """The value of the <select> option that means `want` (a month 1-12, or a year written with 2 or 4 digits)."""
    targets = {want, want % 100} if year else {want}
    for value, text in options:
        for s in (value, text):
            m = re.fullmatch(r"\s*0*(\d{1,4})\s*(?:월|년)?\s*", str(s or ""))
            if m and int(m.group(1)) in targets:
                return value
    return None


def scrub_card(text: str, number: str) -> str:
    """Page text with the card number taken out, however it is spaced, and anything else that looks like a whole card number."""
    if number:
        spaced = r"[\s\-]?".join(re.escape(ch) for ch in number)
        text = re.sub(spaced, "[card number hidden]", text)
        if len(number) > 4:
            text = text.replace(number[:-4], "[hidden]")
    return re.sub(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)", lambda m: "[card number hidden]" if _luhn(re.sub(r"\D", "", m.group(0))) else m.group(0), text)


def _luhn(n: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(n)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return len(n) >= 12 and total % 10 == 0


def _country_names(cc: str) -> list[dict[str, str]]:
    from .. import address   # the country's English name, for a country list on the page
    return [c for c in address.countries() if c["code"] == (cc or "").upper()]


def changed_lines(old: str, new: str, *, context: int = CHANGE_CONTEXT, max_share: float = CHANGE_MAX_SHARE) -> str | None:
    """What of `new` (a page's text now) differs from `old` (its text at the agent's last reading): the changed and added lines with
    `context` lines around each, "…" between the pieces, and the lines that left the page. "" = nothing changed; None = so much changed
    (more than `max_share` of the page) that the whole page should be shown."""
    import difflib
    a = [ln.rstrip() for ln in old.splitlines() if ln.strip()]
    b = [ln.rstrip() for ln in new.splitlines() if ln.strip()]
    if a == b:
        return ""
    keep: set[int] = set()
    gone: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            gone += a[i1:i2]
        if tag in ("replace", "insert"):
            keep.update(range(max(0, j1 - context), min(len(b), j2 + context)))
        else:   # only removed: the lines around where they were
            keep.update(range(max(0, j1 - context), min(len(b), j1 + context)))
    changed = sum(len(b[j]) for j in keep) + sum(len(g) for g in gone)
    if changed > max_share * max(1, sum(len(x) for x in b)):
        return None
    out: list[str] = []
    last = -2
    for j in sorted(keep):
        if j != last + 1 and out:
            out.append("…")
        out.append(b[j])
        last = j
    if gone:
        shown = "\n".join(gone)
        out.append("[No longer on the page:]\n" + (shown[:1500] + " …" if len(shown) > 1500 else shown))
    return "\n".join(out)


class _Pause:
    """Context manager of BrowserSession.pause_clock (counted, so nested hand-overs of one tab stay paused until the last ends)."""
    def __init__(self, session: "BrowserSession", key: str):
        self.session, self.key = session, key

    def __enter__(self) -> "_Pause":
        self.session._paused[self.key] = self.session._paused.get(self.key, 0) + 1
        return self

    def __exit__(self, *_exc: Any) -> None:
        n = self.session._paused.get(self.key, 0) - 1
        if n > 0:
            self.session._paused[self.key] = n
        else:
            self.session._paused.pop(self.key, None)


class BrowserSession:
    """Lazy connection to the browser container; one page (tab) per device that uses it."""

    def __init__(
        self,
        cdp_host: str = "browser",
        cdp_port: int = 9223,
        *,
        allow_private: bool = False,
        connect: Callable[[], Any] | None = None,
        resolver: Callable[..., list] = socket.getaddrinfo,
        cdp: Callable[[Any], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        new_context: Callable[[], Any] | None = None,
    ):
        self.cdp_host, self.cdp_port, self.allow_private = cdp_host, cdp_port, allow_private
        self._connect = connect
        self._new_context = new_context        # -> awaitable fresh private context (tests); default: the browser's new_context()
        self._cdp = cdp                        # page -> awaitable CDP session (tests); default: Playwright's new_cdp_session
        self._resolver = resolver
        self._pw: Any = None
        self._ctx: Any = None
        self._browser: Any = None
        self._pages: dict[str, Any] = {}       # device -> its tab
        self._used: dict[str, float] = {}      # device -> when it last used the tab (self.clock), or its last turn ended
        self._refused_until: dict[str, float] = {}    # registrable site -> self.clock() time until which it is not loaded (a refusal)
        self.clock = clock
        self.remote_dns: Callable[[], bool] = lambda: False   # the VPN connector is on: names are resolved by the VPN, not here (vpn.py)
        self.vpn_note: Callable[[], Awaitable[str]] | None = None   # -> the text saying the VPN is down, or "" (server.py wires vpn.down_note)
        self._lock = asyncio.Lock()
        self._purchase: dict[str, tuple[str, float, int]] = {}   # device -> (host, until, clicks left) after an allowed purchase
        self._cancel: dict[str, tuple[str, float, int, str]] = {}   # device -> (host, until, clicks left, order no) to cancel a wrong charge
        self.on_cancel_click: Callable[[str, str, str], None] | None = None   # (host, the click's words, order no): logged by the server
        self._last_card: dict[str, float] = {}                   # device -> when its last browser card went out
        self._dialogs: dict[str, TabDialogs] = {}                # tab -> its page's own pop-ups (page_dialogs.py)
        self._status: dict[str, tuple[str, float]] = {}          # device -> (last status line of its card, wall time)
        self._seen_at: dict[str, float] = {}                     # device -> wall time its tab was last used
        self._control: dict[str, str] = {}                       # tab -> the device that took control of it (absent = the agent's)
        self._handed_back: dict[str, asyncio.Event] = {}         # tab -> set whenever the agent may act on it
        self.control_listeners: list[Callable[[str, str | None], Any]] = []   # (tab, device or None) on every change
        self.handback_listeners: list[Callable[[str, str, str, str], Any]] = []   # (tab, what the owner did, reason, "browser") on every hand-back
        self.close_listeners: list[Callable[[str], Awaitable[Any]]] = []   # (tab) after close_tab closed it (the live view's X screen lets go)
        self.control_wait = CONTROL_WAIT_SECONDS
        self.work: WorkBrowser | None = None                     # the work browser (work_browser.py), set by server.py when it is configured
        self._work_gen: dict[str, int] = {}                      # work tab -> the WorkBrowser.generation its page was made in
        self._seed_cdp: dict[str, Any] = {}                      # work tab -> its DevTools session that pauses document requests (A3)
        self.signed_out_tab: Callable[[str], bool] = lambda key: False   # research jobs browse signed out (A6, set by server.py)
        self.on_owner_sign_in: Callable[[str], Any] = lambda site: None   # a sign-in the owner typed by hand (store: B1, set by server.py)
        self.on_site_used: Callable[[str], Any] = lambda site: None       # a site the browser went to (store: last used, B1)
        self.on_site_forgotten: Callable[[str], Any] = lambda site: None  # the owner signed the browser out of a site (B1)
        self._touched: dict[str, float] = {}
        # named browser profiles (brief C2, set by server.py): the units are sealed in the store
        self.profile_unit: Callable[[str, str], dict[str, Any]] = lambda profile, site: {"cookies": [], "storage": {}}
        self.profile_save: Callable[[str, str, dict[str, Any]], Any] = lambda profile, site, unit: None
        self.profile_check: Callable[[str], Any] = lambda profile: None      # raises VaultError when the keychain is locked or the id is unknown
        self._listened: set[int] = set()                       # contexts that already have the owner-page listener (C1d)
        self._profile_tabs: dict[str, WorkTab] = {}            # chat tab key -> the seed/write-back record of its own profile context (C2b)
        self.signed_in_site: Callable[[str], bool] = lambda site: True   # a sign-in row exists for the site (server.py, B1)
        self.login_for: Callable[[str], str] = lambda site: ""           # the name of a saved login for the site ("" = none) (server.py)
        self._sign_in_pass: dict[str, float] = {}      # registrable site -> clock() time until which the site may load for a sign-in
        self._sign_in_pass_used: set[str] = set()      # the sites that had their pass in this pause (one pass per pause)
        self._pass_opened: set[str] = set()            # a pass opened by the refusal being answered now (snapshot reads it once)
        self._work_dl: Any = None                                # the work browser's browser-wide DevTools session (downloads)
        # ---- S6: what the pages are, and what the browser is set up with (see the notes at each method)
        self._doc: dict[str, tuple[int, dict[str, str]]] = {}   # tab -> (status, headers) of its main document's last response
        self._notes: dict[str, list[str]] = {}                   # tab -> lines for its next page reading (e.g. what the user did by hand)
        self._site_locks: dict[str, asyncio.Lock] = {}           # site tab -> one request at a time (site_fetch)
        self._opened: dict[str, str] = {}   # tab -> the address last opened in it (kept apart from its target, see target_id)
        self._checks: dict[str, tuple[str, float]] = {}          # tab -> (bot check vendor, when it was announced)
        self.check_listeners: list[Callable[[str, Challenge, str], Any]] = []   # (tab, check, host) when a check is first seen
        self._private: dict[str, Any] = {}                       # tab -> its own private context (nothing kept after it closes)
        self._type_cdp: dict[str, tuple[Any, Any]] = {}          # tab -> (page, the DevTools session) that types Korean (_ime_sender)
        self._tab_cdp: dict[str, Any] = {}                       # tab -> the CDP session that keeps its time zone / locale override
        self._targets: dict[str, tuple[str, str]] = {}           # tab -> (target id = main frame id, browser context id)
        self._children: ChildTargets | None = None              # the frames and workers of the tab's language (child_targets.py), one connection
        self._work_children: ChildTargets | None = None         # the same for the work browser (its own connection, see work_connected)
        self._child_locales: dict[str, str] = {}                # target id of a frame -> the locale of its tab ("" = the browser's)
        self._tab_locales: dict[str, str] = {}                  # tab key -> the locale it was given (_prepare; "" = the browser's)
        self._site_states: dict[str, tuple[dict[str, Any], float]] = {}   # host -> (cookies + storage, saved at): memory only
        self._dl_cdp: Any = None                                 # the browser-wide CDP session that hears downloads
        self._dl_contexts: set[str] = set()                      # browser contexts whose downloads go to the download area
        self.downloads: DownloadArea | None = None               # set by the app when the download folder is shared with the server
        self.timezone: Callable[[], str] = lambda: ""            # the user's time zone (profile), applied to every tab
        self.locale = ""                                         # e.g. "ko-KR": the language the browser was started with
        self.user_language: Callable[[], str] = lambda: ""         # the user's own app language code (ko, ja, en...); "" = not set
        self.prepare_pages = connect is None                     # set up time zone / downloads on new tabs (off for the plain test fakes)
        self.journal: Any = None                                 # takeover.TakeoverJournal: what the user does while holding a tab
        self.has_mailbox: Callable[[], bool] = lambda: False     # a mailbox is connected (a code prompt then points to find_login_code)
        self._login_url: dict[str, str] = {}                     # tab -> the address the agent's last fill_login started from
        self._login_at: dict[str, float] = {}                    # tab -> when fill_login last typed a saved login there (a sign-in the agent started)
        self.sign_ins: dict[str, dict[str, Any]] = {}            # tab -> the sign-in the agent started there: {"site", "at", "task"}
        self.auto_code: Callable[[str], Awaitable[str | None]] = _no_auto_code   # types a code from the site's own mail (browser_more)
        self._judged: dict[str, tuple[str, str, bool]] = {}      # device -> (selector, commit kind, password field) of its last judged step
        self._carried: dict[str, tuple[str, str, str, float]] = {}   # device -> a step judge_ahead judged for the next judge() of it
        self._judged_info: dict[str, dict[str, Any] | None] = {}  # device -> what was read of that step's element (commits.TARGET_JS)
        self._judged_words: dict[str, tuple[str, str]] = {}  # device -> (selector, the words it was judged by) of its last judged step
        self._verdict: dict[str, Any] = {}   # device -> the step judge's last verdict (its flags; use_cancel_action reads them)
        # device -> {(page, label): commit kind}: the first judgement of an element stays its judgement on that page (a retry with
        # another selector for the same button gets the same card words)
        self._kinds_seen: dict[str, dict[tuple[str, str], str]] = {}
        # device -> (page, label, monotonic deadline, tries left) of a step the user allowed whose click then failed: the same element
        # tried again with another selector does not ask again
        self._allowed_failed: dict[str, tuple[str, str, float, int]] = {}
        self.page_judge: Any = None   # page_judge.PageJudge: what a page asks (a verification step, a payment approval) and which field is which when structure is silent (None = no model wired: the safer side)
        self.step_judge: Any = None   # step_judge.StepJudge: what a submit control does when the page's structure cannot tell (None = no model wired)
        self.order_dialog: Callable[[str, str, str], bool] | None = None   # (tab, host, message): a pop-up an open order may accept
        self._wake: dict[str, asyncio.Event] = {}                # tab -> set when it is handed back (a waiting human check looks again)
        self._options: dict[str, set[str]] = {}                  # tab -> option selectors (CSS paths) of its last reading
        self._keep_choice: dict[str, float] = {}                 # login name -> when browser_login listed its form's options (S6)
        self._hand_end: dict[str, tuple[str, str, float]] = {}   # tab -> (site, step kind, when) of its last finished verification hand-over
        self._pending_hand: dict[str, tuple[Any, ...]] = {}      # tab -> a verification hand-over raised while the agent chooses options
        self._approving: dict[str, tuple[bool, float]] = {}      # site -> (an approve-step hand-over is open, when it opened / ended)
        self._handed: dict[str, str] = {}                        # tab -> how its last human check hand-over ended (for fill_login)
        self._tries: dict[str, dict[str, Any]] = {}              # tab -> the agent's own tries at the human check on it (see snapshot)
        self._shot_scale: dict[str, float] = {}                  # tab -> page CSS px per pixel of the last check picture the agent saw
        self._look_url: dict[str, str] = {}                      # tab -> the page address of its last browser_look picture
        self._check_clicked: dict[str, bool] = {}                # tab -> the agent clicked into the check (keys may follow)
        self.check_tries = CHECK_TRIES                           # readings of one check the agent may try before it goes to the user
        self.check_poll = CHECK_POLL_SECONDS
        self._own_layers: dict[str, tuple[str, set[str]]] = {}   # tab -> (page address, windows the agent's own steps opened there)
        self._opened_layer: dict[str, tuple[str, str]] = {}      # tab -> (page address, label) of the window its last click opened (OPENED_NOTE)
        # tab -> the pages under the one it shows now, each with its tab DevTools session: a window a page opened
        # (window.open, target=_blank) becomes the tab's page; when it closes, the tab goes back to the page that opened it
        self._windows: dict[str, TaskWindow] = {}              # tab key -> its window: its own tabs, the active one is _pages[key] (brief C1)
        self._tab_profile: dict[str, str] = {}                 # tab key -> the named profile its window is opened in (before the window exists)
        self._profile_ctx: dict[str, Any] = {}                 # chat tab key -> its own context for a named profile (C2)
        self._creating = 0                                     # pages Kelo is creating now: the context listener skips them (a count)
        self.active_listeners: list[Callable[[str], Any]] = []  # told when a key's active tab changes (live view, VNC)
        self._popup_setup: dict[str, asyncio.Future] = {}      # tab -> the setup of the window it just moved into (watch, language)
        self._unchanged: dict[str, bool] = {}                    # tab -> its last click changed nothing (NO_CHANGE_NOTE)
        self._last_text: dict[str, tuple[str, str]] = {}         # tab -> (page address, its text) of the last reading the agent got
        self._ref_words: dict[str, dict[str, str]] = {}          # tab -> {ref: 'role "name"'} of its last reading with refs (page_tree)
        self.changes_only = True                                 # after a step on the same page: only what changed (CHANGES_HEAD)
        self._probes: dict[str, Any] = {}                        # tab -> the last structure reading of its page (challenge.PROBE_JS)
        # tab -> (page address, how its sign-in verification went: "mail" = the mailbox was pointed to first, or a hand-over outcome)
        self._verified: dict[str, tuple[str, str]] = {}
        self.overlay_pause = OVERLAY_PAUSE
        # (task, host) -> (access-denied answers, when the last one came): the task is the user's message the reply works on
        self._blocked: dict[tuple[str, str], tuple[int, float]] = {}
        self._steps: dict[str, asyncio.Future] = {}              # tab -> the agent step running on it now (run_step)
        self._step_clock: dict[str, dict[str, float]] = {}       # tab -> {"spent", "limit"}: the running step's clock (run_step)
        self.nav_block: Callable[[], str | None] | None = None   # set by the checkout tools: why this tab may not be reloaded or navigated now
        self.pacer = SitePacer(clock)                            # per-site gaps between submits and navigations (pacing.py)
        self.sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep   # waits of the pacer (tests: a fake clock's)
        self._typed: dict[str, bool] = {}                        # tab -> text was typed since its last submit or navigation
        self._stopped: set[Any] = set()                          # steps cancelled because the user took the tab over
        self._paused: dict[str, int] = {}                        # tab -> hand-overs open now (the step clock stands still)
        self._rescues: dict[str, asyncio.Future] = {}            # tab -> the clean-up after the user took the tab over
        self._background: set[asyncio.Future] = set()            # clean-ups no step waits for (a route that does not come off)
        self._owner_moved: set[str] = set()                      # tabs the owner changed by hand before handing them back (MOVED_NOTE)
        self.book: Any = None                                    # tab_book.TabBook: the page of each tab key, kept across a server restart (server.py)
        self._restart_tabs: dict[str, str] = {}                  # what adopt_after_restart found: key -> "kept" | "gone"
        self._lost: dict[str, str] = {}                          # tab -> the last url of a page the browser lost (restart or crash)
        self._lost_act: set[str] = set()                         # tabs whose next acting step waits until the page is read (LOST_ACT_NOTE)
        self.on_lost: Callable[[str], Any] | None = None         # told of a tab the browser lost (workers: a job's card line)

    # ------------------------------------------------------------------ the user taking a tab over
    def controlled_by(self, key: str) -> str | None:
        """The device holding this tab, or None when the agent may use it."""
        return self._control.get(key)

    def controls(self) -> dict[str, str]:
        """Every tab a device holds now: {tab: device}."""
        return dict(self._control)

    def take_control(self, key: str, device: str) -> bool:
        """The device takes the tab. True when a running agent step on it was stopped by this (see interrupt)."""
        if self._control.get(key) == device:
            return False
        self._control[key] = device
        self._handed_back.setdefault(key, asyncio.Event()).clear()
        cancelled = self.interrupt(key)
        if self.journal is not None and (page := self.raw_page(key)) is not None:
            try:
                self.journal.start(key, page)      # what the user does by hand is written down for the agent (takeover.py)
            except Exception:  # noqa: BLE001 - the journal never stops the user from taking over
                pass
        self._tell(key, device)
        return cancelled

    def move_control(self, key: str, device: str) -> None:
        """The tab moves from one of the owner's devices to another (the first one has no live connection): the hold goes on, no step is
        stopped and nothing is handed back."""
        if self._control.get(key) is None or self._control.get(key) == device:
            return
        self._control[key] = device
        self._tell(key, device)

    def restore_control(self, key: str, device: str) -> None:
        """A button hold saved before a restart: the tab is the owner's again (no step, no journal, not handed back)."""
        self._control[key] = device
        self._handed_back.setdefault(key, asyncio.Event()).clear()
        self._tell(key, device)

    def tab_cdp(self, key: str) -> Any:
        """The DevTools session the tab was prepared with (None when it has none)."""
        return self._tab_cdp.get(key)

    def _note_opened(self, key: str, url: str) -> None:
        """The address a tab was last opened at (kept apart from its DevTools target, see _targets)."""
        self._opened[key] = url

    def target_id(self, key: str) -> str:
        """The DevTools target id of the tab's page ("" when it was not read)."""
        return self._targets.get(key, ("", ""))[0]

    async def release_control(self, key: str, reason: str = "owner", *, moved: bool = False) -> bool:
        """Hand the tab back to the agent. The purchase window of that tab is closed (the user's own clicks never count towards it),
        and a page the agent would not be allowed to open is left for about:blank. `reason` "timeout": the owner's connection went away
        and the tab came back on its own, which the agent is told (they may not have finished). False when nobody held it."""
        if self._control.pop(key, None) is None:
            return False
        self._purchase.pop(key, None)
        page = self._pages.get(key)
        try:
            url = str(page.url or "") if page is not None and not page.is_closed() else ""
            if url.startswith(("http://", "https://")):
                try:
                    await self._guard(url)
                except BrowserPolicyError:
                    await page.goto("about:blank")
        except Exception:  # noqa: BLE001 - a tab that went away meanwhile
            pass
        summary = ""
        if self.journal is not None:
            try:
                summary = self.journal.stop(key)
            except Exception:  # noqa: BLE001
                summary = ""
        if reason == "timeout":
            summary = (TIMEOUT_NOTE + "\n" + summary) if summary else TIMEOUT_NOTE
        if summary or moved:
            self._owner_moved.add(key)         # the agent's next acting step on this tab reads the page first (hold_to_act)
        if summary:
            self.note(key, summary)            # the agent reads it with its next look at the page
        self._tell(key, None)   # first, so whoever listens hears it before a waiting agent step moves on
        for fn in list(self.handback_listeners):   # the paused jobs of the tab go on; a chat turn that ended hears of it (workers.handed_back)
            try:
                fn(key, summary, reason, "browser")
            except Exception:  # noqa: BLE001 - a listener never breaks handing back
                pass
        self._handed_back.setdefault(key, asyncio.Event()).set()
        self._wake.setdefault(key, asyncio.Event()).set()   # a human check waiting on this tab looks again right away
        return True

    # ------------------------------------------------------------------ one deadline per step; the user's hands win
    def pause_clock(self, key: str) -> "_Pause":
        """`with session.pause_clock(tab):` the step clock of that tab's running step stands still (a human-check hand-over: the user
        has up to 10 minutes to solve it, which is not the page being slow)."""
        return _Pause(self, key)

    async def run_step(self, make: Callable[[], Awaitable[Any]], seconds: float) -> Any:
        """The tools' `limiter`: run one agent step with a deadline of `seconds` of step time. The clock stops while a hand-over of
        this tab waits (pause_clock), so only the page's own time counts. At the deadline the step is cancelled (ToolTimeout, the loop
        then calls `rescue` and tells the model). The step is registered per tab so that `interrupt` (the user takes the tab over) can
        cancel it: then ToolStopped carries TOOK_OVER_NOTE. A step that runs long logs where it is waiting (SLOW_STEP_SECONDS)."""
        key = browser_owner.get()
        task = asyncio.ensure_future(make())
        self._steps[key] = task
        loop = asyncio.get_running_loop()
        spent, last, logged = 0.0, loop.time(), False
        clock = self._step_clock[key] = {"spent": 0.0, "limit": float(seconds)}
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.1)
                now = loop.time()
                if not self._paused.get(key):
                    spent += now - last
                last = now
                clock["spent"] = spent
                if done:
                    break
                if spent >= SLOW_STEP_SECONDS and not logged:
                    logged = True
                    log.warning("a browser step on %s runs longer than %.0f s, waiting in: %s", key, SLOW_STEP_SECONDS, await_chain(task))
                if spent >= seconds:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise ToolTimeout()
            if task.cancelled():
                if task in self._stopped:
                    raise ToolStopped(TOOK_OVER_NOTE)
                raise asyncio.CancelledError()
            return task.result()
        finally:
            if not task.done():
                task.cancel()   # this call itself was cancelled (the reply was stopped): the step goes with it
            if self._steps.get(key) is task:
                del self._steps[key]
                self._step_clock.pop(key, None)
            self._stopped.discard(task)

    def timeout_text(self, args: dict[str, Any] | None = None) -> str:
        """What the model is told when a step hit its deadline (names the site the page belongs to)."""
        host = ""
        url = str((args or {}).get("url") or "")
        if url.startswith(("http://", "https://")):
            host = urlsplit(url).hostname or ""
        host = host or self.current_host() or "this site"
        return STEP_TIMEOUT_NOTE.format(seconds=round(STEP_SECONDS), host=host)

    def with_deadline(self, tools: list[Tool]) -> list[Tool]:
        """Give each browser step tool the step deadline (run_step), the clean-up (rescue) and the words for the model."""
        for tool in tools:
            tool.deadline = OPEN_SECONDS if tool.name == "browser_open" else STEP_SECONDS
            tool.limiter = self.run_step
            tool.on_timeout = lambda _args: self.rescue()
            tool.timeout_text = self.timeout_text
        return tools

    def step_running(self, key: str) -> bool:
        return key in self._steps and not self._steps[key].done()

    def interrupt(self, key: str) -> bool:
        """The user took this tab over: the agent's step on it is cancelled (ToolStopped, TOOK_OVER_NOTE), and what it left half
        done in the page (a loading navigation, a finger still down) is cleaned up before the user's first input (`settled`).
        True when a running step was cancelled; the clean-up runs only then."""
        task = self._steps.get(key)
        if task is None or task.done():
            return False
        self._stopped.add(task)
        task.cancel()
        try:
            self._rescues[key] = asyncio.ensure_future(self.rescue(key))
        except RuntimeError:   # no running loop (a plain test): nothing to clean up
            pass
        return True

    async def settled(self, key: str, timeout: float = RESCUE_SECONDS) -> None:
        """Wait (bounded) until the clean-up after taking the tab over is done: the user's first tap must not meet a half-finished touch."""
        task = self._rescues.get(key)
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except Exception:  # noqa: BLE001 - bounded; the input goes on
                pass

    async def rescue(self, key: str | None = None) -> None:
        """Leave a tab's page usable after a step was stopped: stop the loading of a navigation that never answers (it holds every
        script of the page), lift a touch or mouse button the step left down. Every call is bounded (RESCUE_SECONDS) and none of them
        needs an answer from the page's main thread to be given up on."""
        key = key or browser_owner.get()
        cdp = self._tab_cdp.get(key)
        if cdp is None:
            page = self.raw_page(key)
            if page is None:
                return
            try:
                cdp = await asyncio.wait_for(self.cdp_session(page), RESCUE_SECONDS)
            except Exception:  # noqa: BLE001
                return
        calls = (("Page.stopLoading", {}),
                 ("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []}),
                 ("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 0, "y": 0, "button": "left", "buttons": 0, "clickCount": 0}))
        await asyncio.gather(*(self._quietly(lambda m=m, p=p: cdp_send(cdp, m, p, RESCUE_SECONDS)) for m, p in calls))

    def _later(self, coro: Awaitable[Any]) -> None:
        """Run a clean-up nobody waits for (kept so it is not dropped, forgotten when done)."""
        fut = asyncio.ensure_future(coro)
        self._background.add(fut)
        fut.add_done_callback(self._background.discard)

    def _tell(self, key: str, device: str | None) -> None:
        for fn in list(self.control_listeners):
            try:
                fn(key, device)
            except Exception:  # noqa: BLE001 - a listener never breaks taking or giving back control
                pass

    async def wait_for_agent_turn(self, key: str, timeout: float | None = None) -> bool:
        """Wait until nobody holds the tab (at most `timeout`, default `control_wait`). True = the agent may act."""
        if key not in self._control:
            return True
        ev = self._handed_back.setdefault(key, asyncio.Event())
        try:
            await asyncio.wait_for(ev.wait(), self.control_wait if timeout is None else timeout)
        except asyncio.TimeoutError:
            pass
        return key not in self._control

    async def hold_for_navigation(self, args: dict[str, Any] | None = None) -> str | None:
        """hold_for_user, then the order journal's block: while an order of this task may have gone through (pressed or unknown) the tab
        is not reloaded or navigated (browser_open): the confirmation may be on it."""
        held = await self.hold_for_user(args)
        if held:
            return held
        return self.nav_block() if self.nav_block is not None else None

    async def hold_to_act(self, args: dict[str, Any] | None = None) -> str | None:
        """The `hold` of the acting steps: hold_for_user, then MOVED_NOTE when the owner changed this tab by hand before handing it back
        and the page has not been read since (snapshot and look clear it)."""
        held = await self.hold_for_user(args)
        if held:
            return held
        if browser_owner.get() in self._lost_act:   # the browser was lost: the page is read first (snapshot and look clear it)
            return LOST_ACT_NOTE
        return MOVED_NOTE if browser_owner.get() in self._owner_moved else None

    async def hold_for_user(self, _args: dict[str, Any] | None = None) -> str | None:
        """A tool's `hold`: before an agent step on this device's tab, wait while the user controls it. None = go on; otherwise the
        words the agent gets instead of a result."""
        patience = hold_patience.get()
        if await self.wait_for_agent_turn(browser_owner.get(), patience):
            return None
        if patience is not None:   # a chat reply: told at once, the reply says it waits for the owner
            return CONTROLLED_CHAT_NOTE
        return CONTROLLED_NOTE.format(minutes=max(1, round(self.control_wait / 60)))

    def raw_page(self, key: str) -> Any:
        """That tab's page without waiting or opening anything (the live view), or None."""
        page = self._pages.get(key)
        try:
            return page if page is not None and not page.is_closed() else None
        except Exception:  # noqa: BLE001
            return None

    async def cdp_session(self, page: Any) -> Any:
        """A DevTools session on `page` (send(method, params), on(event, handler), detach())."""
        if self._cdp is not None:
            return await self._cdp(page)
        return await page.context.new_cdp_session(page)

    # ------------------------------------------------------------------ which steps commit something (commits.py)
    async def read_target(self, selector: str) -> dict[str, Any] | None:
        """What a click or typing target is (its words, role, form and page; commits.TARGET_JS), or None when it cannot be read in
        TARGET_READ_MS (not there yet, a selector Playwright cannot use here, no browser): then its selector words are judged."""
        page = self._pages.get(browser_owner.get())
        if page is None or not selector:
            return None
        try:
            if page.is_closed():
                return None
            owner = await asyncio.wait_for(self.frame_for(page, selector), TARGET_READ_MS / 1000)
            got = await asyncio.wait_for(owner.locator(ref_selector(selector)).first.evaluate(TARGET_JS, timeout=TARGET_READ_MS),
                                         TARGET_READ_MS / 1000 + 0.5)
        except Exception:  # noqa: BLE001 - when the element cannot be read, the selector's own words decide
            return None
        if not isinstance(got, dict):
            return None
        return await self._with_listeners(got, page, owner)

    async def _listener_code(self, page: Any, path: str) -> str | None:
        """The source of the click-like listeners added with addEventListener on the element at `path` (the CSS path TARGET_JS gave;
        DevTools' getEventListeners reads them, in an expression that leaves nothing in the page). None when they cannot be read.
        `page` may be a frame inside the page (one in another process has a DevTools session of its own)."""
        if not path:
            return None
        if hasattr(page, "main_frame"):
            cdp = await self.cdp_session(page)
        elif self._cdp is not None:
            return None
        else:
            cdp = await page.page.context.new_cdp_session(page)
        try:
            expression = _LISTENERS_JS % (json.dumps(path), json.dumps(sorted(_CLICK_EVENTS)))
            got = await cdp.send("Runtime.evaluate", {"expression": expression, "includeCommandLineAPI": True, "returnByValue": True})
            value = ((got or {}).get("result") or {}).get("value")
            if not isinstance(value, list):
                return None
            return "\n;\n".join(str(src) for src in value if src)
        finally:
            try:
                await cdp.detach()
            except Exception:  # noqa: BLE001
                pass

    async def judge_ahead(self, tool: str, args: dict[str, Any]) -> str:
        """judge() for a check that runs before the step's own (the privacy guard in the tool gate): the answer is kept for that one
        next judge() of the same step, so the page is read once."""
        kind = await self.judge(tool, args)
        self._carried[browser_owner.get()] = (tool, repr(sorted(args.items(), key=str)), kind, time.monotonic())
        return kind

    async def judge(self, tool: str, args: dict[str, Any]) -> str:
        """The commit kind of a browser_click / browser_type step ("" = only looking), remembered for the card and the server's rules."""
        carried = self._carried.pop(browser_owner.get(), None)   # judged a moment ago by judge_ahead for the same step: not read twice
        if carried is not None and carried[0] == tool and carried[1] == repr(sorted(args.items(), key=str)) and time.monotonic() - carried[3] < 5:
            return carried[2]
        if tool in POINT_TOOLS:
            return await self._judge_point(tool, args)
        selector = str(args.get("selector", ""))
        info = await self.read_target(selector)
        # a ref that could not be read is judged by the words the page's reading gave it (never by "e12"); none known: "unknown",
        # which always asks (no_card)
        words = self.selector_words(selector) if info is None else selector
        if info is None and ref_of(selector) and words == selector:
            # the last reading did not carry it (the page changed after a click, 2026-11-12: the same apply button clicked again
            # asked "press a button?" though the owner had asked for it): ask the element itself for its role and name
            words = await self._ref_name_now(selector) or selector
        if info is None and ref_of(selector) and words == selector:
            kind = "unknown"
        elif tool == "browser_type":
            kind = await self.type_kind(info, words, str(args.get("text", "")), bool(args.get("submit", False)))
        else:
            kind = await self.click_kind(info, words)
        # a typed value is secret when its field is a password field, or when the field cannot be read at all (fail closed: the
        # value is masked; no word of the label decides, hc-final-a)
        secret = (bool(info) and (str(info.get("type") or "") == "password" or "password" in str(info.get("autocomplete") or ""))) or (
            info is None and tool == "browser_type")
        target = self._target_key(info, selector)
        if kind and target[1]:
            seen = self._kinds_seen.setdefault(browser_owner.get(), {})
            kind = seen.setdefault(target, kind)
            if len(seen) > 200:
                seen.pop(next(iter(seen)))
        self._judged[browser_owner.get()] = (selector, kind, tool == "browser_type" and secret)
        self._judged_info[browser_owner.get()] = info
        self._judged_words[browser_owner.get()] = (selector, words)
        return kind

    @staticmethod
    def is_submit_step(tool: str, info: dict[str, Any], args: dict[str, Any]) -> bool:
        """A step that submits a form by the page's structure: a submit control inside a form, or Enter typed into a field of one."""
        if tool == "browser_type":
            return bool(args.get("submit")) and bool(info.get("in_form"))
        return bool(info.get("submits"))

    async def judged_kind(self, info: dict[str, Any]) -> str:
        """The commit kind the step judge gives a control ("" = nothing to ask for; "unknown" = the judge could not answer, or there is
        none wired: the owner is asked, never a pass)."""
        if self.step_judge is None:
            return "unknown"
        verdict = await self.step_judge.web(info)
        self._verdict[browser_owner.get()] = verdict
        return kind_of_verdict(verdict, info)

    async def click_kind(self, info: dict[str, Any] | None, words: str) -> str:
        """The commit kind of a click (docs/click-judge.md): structure first (a link, a window opener, plain text, a page-state
        control only looks), the step judge for a step that changes something or cannot be read."""
        if click_route(info) == "":
            return ""
        if info is None:
            info = {"text": words, "url": self.current_url()}   # unreadable: the judge sees its name only
        return await self.judged_kind(info)

    async def type_kind(self, info: dict[str, Any] | None, words: str, text: str, submit: bool) -> str:
        """The commit kind of typing: a card number or a password field asks (judge_type); Enter into a form that is no search form is
        a submit, judged like a click, and the judge's kind wins when it says one (no flag = no card)."""
        own = judge_type(info, words, text, submit)
        if info is None or not submit or not info.get("in_form") or info.get("search_form"):
            return own
        return await self.judged_kind(info) or own

    async def submit_refusal(self, tool: str, args: dict[str, Any]) -> str | None:
        """The safety net's refusal: a submit control on a page WITHOUT checkout structure that the step judge says spends money. The
        way to pay is checkout_review / checkout_submit (tools/checkout.py REFUSAL). None for everything else (the judge being
        unavailable asks the owner in judge())."""
        if tool in POINT_TOOLS:
            return await self._point_refusal(tool, args)
        selector = str(args.get("selector") or "")
        if self.step_judge is None or not selector or (tool == "browser_type" and not args.get("submit")):
            return None
        info = await self.read_target(selector)
        if info is None or not self.is_submit_step(tool, info, args):
            return None
        verdict = await self.step_judge.web(info)
        return REFUSAL if verdict is not None and verdict.spends_money else None

    @staticmethod
    def is_point_submit(info: dict[str, Any] | None) -> bool:
        """What is at a point is a submit control, or a control inside a form (pressing it may submit the form)."""
        return bool(info) and bool(info.get("submits") or info.get("in_form"))

    async def _point_infos(self, tool: str, args: dict[str, Any]) -> list[dict[str, Any] | None]:
        """What is at the places of a point step (a drag: where it starts and where it lets go), read like a click target."""
        try:
            pts = step_points(tool, args)
            ends = [pts[0]] if len(pts) == 1 else [pts[0], pts[-1]]
            return [await asyncio.wait_for(self.read_point(*self.page_point(*p)), TARGET_READ_MS / 1000 + LISTENER_READ_MS / 1000 + 1)
                    for p in ends]
        except Exception:  # noqa: BLE001 - nothing readable: judge() asks the owner for an unreadable point
            return [None]

    async def _point_refusal(self, tool: str, args: dict[str, Any]) -> str | None:
        """The safety net for a point press (click at coordinates, drag) on a page without checkout structure: when the control at the
        point is a submit control, or inside a form, the step judge says what it does (cached per page path + control); spends_money
        is refused with the checkout tools message. A judge that cannot answer refuses nothing here (judge() turns it into a card)."""
        if self.step_judge is None:
            return None
        for info in await self._point_infos(tool, args):
            if self.is_point_submit(info):
                verdict = await self.step_judge.web(info)
                if verdict is not None and verdict.spends_money:
                    return REFUSAL
        return None

    async def _judge_point(self, tool: str, args: dict[str, Any]) -> str:
        """A point step is judged by the element at its point, as a click on it; a drag by the elements where it starts AND where it
        lets go (a "slide to pay" handle may say nothing, its end may): the weightier of the two counts. "unknown" when nothing
        ordinary is at one of them (a frame, nothing, no look picture, a page that moved on): then the user always gets a card."""
        key = step_key(tool, args)
        try:
            pts = step_points(tool, args)
            ends = [pts[0]] if len(pts) == 1 else [pts[0], pts[-1]]
            infos = [await asyncio.wait_for(self.read_point(*self.page_point(*p)), TARGET_READ_MS / 1000 + LISTENER_READ_MS / 1000 + 1)
                     for p in ends]
        except Exception:  # noqa: BLE001
            infos = [None]
        judged = [("unknown" if i is None else await self.click_kind(i, ""), i) for i in infos]
        # the weightiest: "unknown" (always asks), then any commit kind, then "" (only looking)
        for n, (k, i) in enumerate(judged):   # a submit control (or one inside a form) the older rules call ordinary: the step judge decides
            if not k and self.is_point_submit(i):
                judged[n] = (await self.judged_kind(i), i)
        kind, info = max(judged, key=lambda ki: 2 if ki[0] == "unknown" else 1 if ki[0] else 0)
        target = self._target_key(info, "")
        if kind not in ("", "unknown") and target[1]:
            seen = self._kinds_seen.setdefault(browser_owner.get(), {})
            kind = seen.setdefault(target, kind)
        self._judged[browser_owner.get()] = (key, kind, False)
        self._judged_info[browser_owner.get()] = info
        return kind

    def page_point(self, x: float, y: float) -> tuple[float, float]:
        """A point of this tab's last browser_look picture in page CSS px. BrowserPolicyError when there was no look, or the page's
        address changed since (the picture no longer shows it)."""
        key = browser_owner.get()
        scale, url = self._shot_scale.get(key), self._look_url.get(key)
        if scale is None or url is None or url != self.current_url():
            raise BrowserPolicyError(LOOK_FIRST)
        return float(x) * scale, float(y) * scale

    def point_ready(self) -> bool:
        """There is a browser_look picture of the page this tab shows now."""
        try:
            self.page_point(0, 0)
        except BrowserPolicyError:
            return False
        return True

    async def read_point(self, x: float, y: float) -> dict[str, Any] | None:
        """What is at (x, y) of the page (CSS px), read like a click target (commits.TARGET_JS, its listeners too); None when it is a
        frame (what is inside it is not read), nothing, or cannot be read."""
        page = self._pages.get(browser_owner.get())
        if page is None:
            return None
        try:
            if page.is_closed():
                return None
            handle = await asyncio.wait_for(page.evaluate_handle(
                "([x, y]) => { const e = document.elementFromPoint(x, y); return e && !['IFRAME', 'FRAME'].includes(e.tagName) ? e : null; }",
                [x, y]), TARGET_READ_MS / 1000)
            el = handle.as_element()
            if el is None:
                return None
            got = await asyncio.wait_for(el.evaluate(TARGET_JS), TARGET_READ_MS / 1000 + 0.5)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(got, dict):
            return None
        return await self._with_listeners(got, page, page)

    async def _with_listeners(self, got: dict[str, Any], page: Any, owner: Any) -> dict[str, Any]:
        path = str(got.pop("judge_path", "") or "")   # the element's CSS path (commits.TARGET_JS); not kept in the reading
        if got.get("structure_read"):
            try:
                # a frame of another process has its own DevTools target; one in the page's process cannot be read this way (None)
                more = await asyncio.wait_for(self._listener_code(page if owner is page else owner, path), LISTENER_READ_MS / 1000)
            except Exception:  # noqa: BLE001 - the inline handlers alone are judged
                more = None
            if more is None:
                got["listeners_read"] = False
            else:
                got["listeners_read"] = True
                if more:
                    got["handler_src"] = (str(got.get("handler_src") or "") + "\n;\n" + more)[:24000]
        return got

    async def _ref_name_now(self, selector: str) -> str:
        """`role "name"` of a ref's element on the page now (its own accessibility line), or "" when it cannot be read in time."""
        page = self._pages.get(browser_owner.get())
        if page is None:
            return ""
        try:
            line = await asyncio.wait_for(page.locator(ref_selector(selector)).first.aria_snapshot(timeout=TARGET_READ_MS),
                                          TARGET_READ_MS / 1000 + 0.5)
        except Exception:  # noqa: BLE001 - unknown, and so it asks
            return ""
        first = next((x.strip() for x in str(line or "").splitlines() if x.strip()), "")
        return first.lstrip("- ").split(" [ref=")[0].rstrip(":").strip()

    async def gone_ref(self, selector: str) -> str | None:
        """STALE_REF when `selector` is a ref this tab's page no longer has, checked before any card (owner report 2026-11-19: the
        owner was asked about an element that was gone, allowed it, and the click failed); None for a selector that is not a ref, a ref
        that is there, no page yet, or a page that cannot be asked in time (then the step's own checks decide)."""
        ref = ref_of(selector)
        page = self._pages.get(browser_owner.get())
        if ref is None or page is None:
            return None
        try:
            if page.is_closed():
                return None
            n = await asyncio.wait_for(page.locator(ref_selector(selector)).count(), TARGET_READ_MS / 1000)
        except Exception:  # noqa: BLE001 - not answered in time, or a page that cannot be asked this way
            return None
        return STALE_REF.format(ref=ref) if n == 0 else None

    def selector_words(self, selector: str) -> str:
        """What a selector names for judging and cards: for a ref of the last reading its words there (`button "Pay now"`), else the
        selector itself (also for a ref that reading did not have)."""
        ref = ref_of(selector)
        if ref is None:
            return selector
        return self._ref_words.get(browser_owner.get(), {}).get(ref, selector)

    def ref_words_of(self, key: str) -> dict[str, str]:
        """The refs of that tab's latest reading and their words (`button "Pay now"`); empty before any reading."""
        return dict(self._ref_words.get(key, {}))

    def ref_label(self, selector: str) -> str:
        """The page's own name for a ref: from the last reading, else as the element itself gave it when its step was judged ("" for
        a selector, or a ref without one): what a card shows."""
        if not ref_of(selector):
            return ""
        got = self._judged_words.get(browser_owner.get())
        return ref_name(self.selector_words(selector)) or (ref_name(got[1]) if got and got[0] == selector else "")

    def _target_key(self, info: dict[str, Any] | None, selector: str) -> tuple[str, str]:
        """Which element this is, for "the same button again": the page (without its #fragment) and the element's own words."""
        page = str((info or {}).get("url") or self.current_url()).split("#", 1)[0]
        return page, " ".join(target_label(info, selector).lower().split())

    def note_allowed(self, selector: str) -> tuple[str, str]:
        """A step is about to run (allowed by a card or by a rule): what it is, in case it fails and is tried again."""
        return self._target_key(self.judged_info(selector), selector)

    def target_state(self, selector: str) -> tuple[str, str, str]:
        """(role, state, scene) of the element last judged for this selector (commits.TARGET_JS): what it is, how it stands now and how
        its part of the page stands; ("", "", "") when it was not read. done_once.py puts them in the step's fingerprint."""
        info = self.judged_info(selector) or {}
        return str(info.get("role") or info.get("tag") or ""), str(info.get("state") or ""), str(info.get("scene") or "")

    def allowed_failed(self, target: tuple[str, str]) -> None:
        if target[1]:
            self._allowed_failed[browser_owner.get()] = (target[0], target[1], time.monotonic() + RETRY_SAME_SECONDS, RETRY_SAME_TRIES)

    def allowed_done(self) -> None:
        self._allowed_failed.pop(browser_owner.get(), None)

    def retry_of_allowed(self, selector: str) -> bool:
        """The step judged last is the element the user just allowed, whose click failed: another selector for the same element."""
        got = self._allowed_failed.get(browser_owner.get())
        if not got or time.monotonic() > got[2] or got[3] <= 0:
            return False
        if self._target_key(self.judged_info(selector), selector) != (got[0], got[1]):
            return False
        self._allowed_failed[browser_owner.get()] = (got[0], got[1], got[2], got[3] - 1)
        return True

    def judged(self, selector: str) -> str:
        """The commit kind last judged for this selector on this device's tab ("" when it was judged harmless or not judged)."""
        got = self._judged.get(browser_owner.get())
        return got[1] if got and got[0] == selector else ""

    def judged_info(self, selector: str) -> dict[str, Any] | None:
        """What was read of the element last judged for this selector on this device's tab (None when it could not be read)."""
        got = self._judged.get(browser_owner.get())
        return self._judged_info.get(browser_owner.get()) if got and got[0] == selector else None

    def judged_secret(self, selector: str) -> bool:
        """The typing step last judged for this selector goes into a password field (never covered by a task's permission)."""
        got = self._judged.get(browser_owner.get())
        return bool(got and got[0] == selector and got[2])

    # ------------------------------------------------------------------ the purchase the user allowed
    def current_url(self) -> str:
        page = self._pages.get(browser_owner.get())
        try:
            return str(page.url) if page is not None and not page.is_closed() else ""
        except Exception:  # noqa: BLE001
            return ""

    def current_host(self) -> str:
        return (urlsplit(self.current_url()).hostname or "").lower()

    def allow_purchase(self, host: str, seconds: float = PURCHASE_WINDOW_SECONDS, actions: int = PURCHASE_WINDOW_ACTIONS) -> None:
        self._purchase[browser_owner.get()] = (host.lower(), time.monotonic() + seconds, actions)

    def purchase_active(self, host: str | None = None) -> bool:
        got = self._purchase.get(browser_owner.get())
        if not got:
            return False
        h, until, left = got
        host = (self.current_host() if host is None else host).lower()
        return left > 0 and time.monotonic() < until and bool(host) and (host == h or same_site(host, h))

    def use_purchase_action(self) -> bool:
        """One click/typing step that finishes the allowed purchase, on the same site. False = ask the user as usual."""
        if not self.purchase_active():
            return False
        h, until, left = self._purchase[browser_owner.get()]
        self._purchase[browser_owner.get()] = (h, until, left - 1)
        return True

    def end_purchase(self) -> None:
        self._purchase.pop(browser_owner.get(), None)

    # The cancel window (purchase_result): after a payment turned out to differ from what the owner approved while cancelling is free,
    # the cancel clicks of THAT order on THAT site go through without a card, for a few minutes. Nothing else is covered.
    def allow_cancel(self, host: str, order_no: str = "", seconds: float = PURCHASE_WINDOW_SECONDS,
                     actions: int = CANCEL_WINDOW_ACTIONS) -> None:
        self._cancel[browser_owner.get()] = (host.lower(), time.monotonic() + seconds, actions, order_no)

    def cancel_active(self, host: str | None = None) -> bool:
        got = self._cancel.get(browser_owner.get())
        if not got:
            return False
        h, until, left, _no = got
        host = (self.current_host() if host is None else host).lower()
        return left > 0 and time.monotonic() < until and bool(host) and (host == h or same_site(host, h))

    def use_cancel_action(self, selector: str) -> bool:
        """One click the step judge says deletes something, inside the open cancel window on its site (the window counts the clicks).
        False = ask the user as usual."""
        if not self.cancel_active() or self.judged(selector) != "delete":
            return False
        v = self._verdict.get(browser_owner.get())   # a deletion only: never closing the account or a membership, security, money or sending
        if v is None or any(getattr(v, f, False) for f in ("opts_out", "account_security", "spends_money", "sends", "signs_up", "applies")):
            return False
        h, until, left, no = self._cancel[browser_owner.get()]
        self._cancel[browser_owner.get()] = (h, until, left - 1, no)
        words = self.selector_words(selector)
        if self.on_cancel_click is not None:
            try:
                self.on_cancel_click(self.current_host(), ref_name(words) if ref_of(selector) else words, no)
            except Exception:  # noqa: BLE001 - the log never stops the cancel
                pass
        return True

    def end_cancel(self) -> None:
        self._cancel.pop(browser_owner.get(), None)

    async def page(self) -> Any:
        key = browser_owner.get()
        if key in self._control and self.step_running(key):
            raise BrowserControlled("the user took over the browser on their phone; this step is stopped")   # never wait inside a step
        if key in self._control and not await self.wait_for_agent_turn(key):
            raise BrowserControlled("the user is controlling the browser on their phone; wait until they hand it back")
        async with self._lock:
            self._used[key] = self.clock()   # idle tabs are closed by the server's sweeper (TAB_SWEEP_SECONDS), never here
            self._seen_at[key] = time.time()
            if self.work is not None and work_tab(key) and key not in self._private:
                if not self.work.connected():
                    await self.work.ensure()   # a work browser that went away: the new connection forgets its old pages below
                if key in self._work_gen and self._work_gen[key] != self.work.generation:
                    self._forget_tab(key)
                    self._work_dl = None
            page = self._pages.get(key)
            if page is not None and not page.is_closed():
                return page
            if (alive := self._window_page(key)) is not None:
                return alive
            if profile := self.profile_of(key):   # a named profile: the keychain must be open and the profile must exist, or nothing opens
                try:
                    self.profile_check(profile)
                except Exception as e:  # noqa: BLE001 - a VaultError carries a note that is safe to show
                    raise BrowserPolicyError(str(e)) from None
            if self.work is not None and work_tab(key) and key not in self._private:
                page = await self._work_page(key)
            elif profile and key not in self._private:
                page = await self._profile_page(key)
            elif self._connect:
                page = await self._private[key].new_page() if key in self._private else await self._connect()
            else:
                await self._ensure_browser()
                if key in self._private:   # this tab browses privately: a fresh page in its own context
                    page = await self._private[key].new_page()
                else:
                    self._creating += 1
                    try:
                        page = await self._new_window(self._ctx)   # a task's window of its own: never the start page or another task's
                    finally:
                        self._creating -= 1
            self._pages[key] = page
            win = self._windows.setdefault(key, TaskWindow(key, profile=self._tab_profile.get(key, "")))
            win.activate(win.add(page).id)
            if key in self._lost:
                lost_url = self._lost.pop(key)
                self._notes.setdefault(key, []).insert(0, BROWSER_LOST_NOTE.format(url=lost_url[:300] or "the page"))
                self._lost_act.add(key)
                self._told_lost(key)
            self._watch(page, key)
            if self.prepare_pages:
                await self._prepare(page, key)
            return page

    async def _work_page(self, key: str) -> Any:
        """A page of a scheduled or background tab: in its own context of the work browser, filled with a copy of the owner's cookies.
        The main browser is never used for it; a work browser that does not answer fails the step with DOWN_NOTE."""
        try:
            ctx = await self.work.open_context(key, await self._main_cookies(), signed_out=self.signed_out_tab(key))
        except WorkBrowserDown:
            raise BrowserPolicyError(DOWN_NOTE) from None
        self._work_gen[key] = self.work.generation
        await self._guard_work_context(key, ctx)
        self._listen_owner_pages(ctx)
        self._creating += 1
        try:
            page = await ctx.new_page()
        finally:
            self._creating -= 1
        await self._arm_seed(key, page)
        if self.work.on_new_page is not None:
            await self._quietly(lambda: self.work.on_new_page(key))
        return page

    async def _profile_page(self, key: str) -> Any:
        """A chat tab in a named profile: a context of its own in the main browser (the owner's default context is not touched), seeded
        per site from the profile's units like a work tab and written back to them at close."""
        if self._connect is None:
            await self._ensure_browser()
        browser = getattr(self, "_browser", None)
        if browser is None:
            raise BrowserPolicyError("the browser is not running, so a profile window cannot be opened")
        ctx = self._profile_ctx.get(key)
        if ctx is None:
            ctx = await browser.new_context(no_viewport=True, accept_downloads=True)
            self._profile_ctx[key] = ctx
            self._profile_tabs[key] = WorkTab(context=ctx)
            await self._guard_work_context(key, ctx)
        self._listen_owner_pages(ctx)
        self._creating += 1
        try:
            page = await ctx.new_page()
        finally:
            self._creating -= 1
        await self._arm_seed(key, page)
        return page

    def _listen_owner_pages(self, ctx: Any) -> None:
        """A page the owner opens by hand in a work or profile context joins the window they hold (C1d), as in the main browser; once per context."""
        if id(ctx) in self._listened or not hasattr(ctx, "on"):
            return
        self._listened.add(id(ctx))
        try:
            ctx.on("page", self._on_context_page)
        except Exception:  # noqa: BLE001 - a context without events: the main browser's listener is the one that matters
            pass

    def _main_context(self) -> Any:
        """The owner's browser context (the persistent profile); the context of an open main page in the test path."""
        if self._connect is None:
            return self._ctx
        return next((p.context for k, p in list(self._pages.items())
                     if not work_tab(k) and k not in self._profile_ctx and k not in self._private
                     and not p.is_closed() and getattr(p, "context", None) is not None), None)

    async def _main_cookies(self) -> list[dict[str, Any]]:
        """Every cookie of the owner's browser (the sign-in copy). [] when there is none; never opens a page in the main browser."""
        try:
            if self._connect is None:
                await self._ensure_browser()
            ctx = self._main_context()
            return list(await ctx.cookies()) if ctx is not None else []
        except Exception:  # noqa: BLE001 - the job then signs in with the vault
            return []

    async def _work_storage_now(self, ctx: Any, site: str) -> dict[str, dict[str, str]]:
        """The local storage of `site` in a work tab's context as it is now (through a page of that context that is still open), by origin.
        {} when no page is open or on any error."""
        try:
            page = next((p for p in list(ctx.pages) if not p.is_closed()), None)
            if page is None:
                return {}
            state = await self.export_site_state(site, page=page)
        except Exception:  # noqa: BLE001
            return {}
        return {str(o["origin"]): {str(k): str(v) for k, v in o.get("localStorage") or []}
                for o in state.get("origins") or [] if o.get("localStorage")}

    async def _carry_back(self, key: str, cookies: list[dict[str, Any]], storage: list[dict[str, Any]], sites: set[str]) -> None:
        """What a work tab changed on its sites goes back to the owner's browser, value by value (card A4): a changed or new cookie or
        storage key is written only where the owner's browser still holds the value the job started from (new: nothing there). Deletions
        are never written. Counts only in the log."""
        written = left = swritten = sleft = 0
        try:
            ctx = self._main_context()
            main = {(c["name"], c["domain"], c.get("path", "")): str(c.get("value", "")) for c in await self._main_cookies()}
            keep = []
            for ch in cookies:
                c = ch["cookie"]
                if main.get((c["name"], c["domain"], c.get("path", ""))) == ch["seeded"]:
                    keep.append(c)
                else:
                    left += 1
            if ctx is not None and keep:
                await ctx.add_cookies(keep)
                written = len(keep)
            if storage:
                page = next((p for p in list(ctx.pages if ctx is not None else []) if not p.is_closed()), None)
                now: dict[str, dict[str, dict[str, str]]] = {}
                for site in {ch["site"] for ch in storage}:
                    now[site] = await self._main_storage(site)
                cdp = await self.cdp_session(page) if page is not None else None
                for ch in storage:
                    if now.get(ch["site"], {}).get(ch["origin"], {}).get(ch["key"]) != ch["seeded"] or cdp is None:
                        sleft += 1
                        continue
                    await cdp.send("DOMStorage.setDOMStorageItem", {"storageId": {"securityOrigin": ch["origin"], "isLocalStorage": True},
                                                                  "key": ch["key"], "value": ch["value"]})
                    swritten += 1
        except Exception:  # noqa: BLE001 - carrying back never stops a tab from closing
            pass
        log.info("work browser: tab %s closed, %d cookies and %d storage keys written back, %d left (the owner changed them)",
                 key, written, swritten, left + sleft)

    def _write_back_unit(self, key: str, profile: str, cookies: list[dict[str, Any]], storage: list[dict[str, Any]], sites: set[str]) -> None:
        """What a profile window changed goes back into the profile's units, value by value: a changed or new cookie or storage key is
        written only where the unit still holds the value the window started from (new: nothing there). Deletions are never written.
        Counts only in the log."""
        written = left = 0
        for site in sorted(sites):
            mine = [c for c in cookies if registrable(str(c["cookie"].get("domain") or "").lstrip(".").lower()) == site]
            smine = [c for c in storage if c["site"] == site]
            if not mine and not smine:
                continue
            try:
                unit = self.profile_unit(profile, site)
                have = {(c["name"], c["domain"], c.get("path", "")): c for c in unit.get("cookies") or []}
                changed = False
                for ch in mine:
                    c = ch["cookie"]
                    now = have.get((c["name"], c["domain"], c.get("path", "")))
                    if (str(now.get("value", "")) if now is not None else None) == ch["seeded"]:
                        have[(c["name"], c["domain"], c.get("path", ""))] = c
                        written += 1
                        changed = True
                    else:
                        left += 1
                st = {o: dict(items) for o, items in (unit.get("storage") or {}).items()}
                for ch in smine:
                    if st.get(ch["origin"], {}).get(ch["key"]) == ch["seeded"]:
                        st.setdefault(ch["origin"], {})[ch["key"]] = ch["value"]
                        written += 1
                        changed = True
                    else:
                        left += 1
                if changed:
                    self.profile_save(profile, site, {"cookies": list(have.values()), "storage": st})
            except Exception:  # noqa: BLE001 - a locked keychain: nothing is written, and the window still closes
                log.info("browser profile: a site's changes could not be written back")
        log.info("browser profile: tab %s closed, %d values written back to its profile, %d left (changed meanwhile)", key, written, left)

    def owner_handed_back(self, key: str, summary: str, reason: str, kind: str) -> None:
        """A hand-back listener (server.py): the owner did something on a work tab, so that site counts as signed in by the job and its
        cookies go back to the owner's browser when the tab closes. A sign-in the owner typed by hand is recorded for the Keychain
        (brief B1), on any tab."""
        page = self._pages.get(key)
        host = (urlsplit(str(getattr(page, "url", "") or "")).hostname or "").lower() if page is not None else ""
        if self.journal is not None and key in self.journal.signed_in:
            self.journal.signed_in.discard(key)
            if registrable(host):
                self._hook(self.on_owner_sign_in, registrable(host), self.profile_of(key))
                self.clear_pause(registrable(host))   # the owner signed in: a pause for that site ends (brief B2)
        if self.work is None or not work_tab(key) or not summary:
            return
        if host:
            self.work.note_signed(key, host)

    def _hook(self, fn: Callable[..., Any], site: str, profile: str = "") -> None:
        """Calls one of the store hooks (server.py); a hook that fails never breaks the browser. A named profile's id is passed on."""
        try:
            fn(site, profile) if profile else fn(site)
        except Exception:  # noqa: BLE001
            log.info("browser hook failed for one site")

    def bind_profile(self, key: str, profile: str) -> None:
        """The task window of `key` is opened in this named profile (before its first page). A buy tab never takes one."""
        if profile and not key.startswith("buy:"):
            self._tab_profile[key] = profile
        else:
            self._tab_profile.pop(key, None)

    def try_bind_profile(self, key: str, profile: str) -> str:
        """browser_open(profile=...): only for a window that is not open yet. "" when bound; else the error to tell the model."""
        if key.startswith("buy:") or key in self._private:
            return "error: this tab does not use profiles"
        win = self._windows.get(key)
        if win is not None and getattr(win, "profile", "") != profile:
            return "error: this task's window uses another profile; close it first (browser_close) or keep using it"
        if win is not None:
            return ""
        try:
            self.profile_check(profile)
        except Exception as e:  # noqa: BLE001 - a VaultError says why (locked, unknown id)
            return f"error: {e}"
        self._tab_profile[key] = profile
        return ""

    async def close_profile_windows(self, profile: str) -> int:
        """Close every window bound to a named profile (its profile is deleted): what each changed is written back first, to a profile
        that is deleted right after (nothing is kept)."""
        keys = [k for k, p in list(self._tab_profile.items()) if p == profile]
        for k in keys:
            await self.close_tab(k)
        return len(keys)

    async def forget_profile_site(self, profile: str, site: str) -> None:
        """A named profile is signed out of a site: its open contexts lose that site's cookies (the stored unit is the caller's)."""
        reg = registrable(site.strip().lower())
        ctxs = [wt.context for k, wt in self._profile_tabs.items() if self._tab_profile.get(k) == profile]
        if self.work is not None:
            ctxs += [wt.context for k, p in self._tab_profile.items() if p == profile and (wt := self.work.tab(k)) is not None]
        for ctx in ctxs:
            try:
                kept = [c for c in await ctx.cookies() if registrable(str(c.get("domain") or "").lstrip(".").lower()) != reg]
                await ctx.clear_cookies()
                await ctx.add_cookies([{k: c[k] for k in COOKIE_KEYS if k in c} for c in kept if c.get("name") and c.get("domain")])
            except Exception:  # noqa: BLE001 - a context that went away
                pass

    def profile_of(self, key: str) -> str:
        """The named profile this tab's window uses ("" = the default profile, the owner's browser). Research jobs and private tabs are
        signed out / private whatever was asked, so they have none."""
        if key.startswith("buy:") or key in self._private or self.signed_out_tab(key):
            return ""
        return self._tab_profile.get(key, "")

    def _touch_site(self, site: str, now: float | None = None, profile: str = "") -> None:
        """The site was used by Kelo's browser: its sign-in row is marked used, at most once per TOUCH_SECONDS per site."""
        if not site:
            return
        now = time.monotonic() if now is None else now
        tk = f"{profile}:{site}" if profile else site
        if now - self._touched.get(tk, -TOUCH_SECONDS) < TOUCH_SECONDS:
            return
        self._touched[tk] = now
        self._hook(self.on_site_used, site, profile)

    async def _main_storage(self, site: str) -> dict[str, dict[str, str]]:
        """The local storage of `site` in the owner's browser, by origin, read through a page of the main context that already exists (a
        blank one does; no page is opened). {} on any error."""
        try:
            ctx = self._main_context()
            page = next((p for p in list(ctx.pages if ctx is not None else []) if not p.is_closed()), None)
            if page is None:
                return {}
            state = await self.export_site_state(site, page=page)
        except Exception:  # noqa: BLE001 - the cookies alone still carry most sign-ins
            return {}
        return {str(o["origin"]): {str(k): str(v) for k, v in o.get("localStorage") or []}
                for o in state.get("origins") or [] if o.get("localStorage")}

    def _seed_tab(self, key: str) -> WorkTab | None:
        """The record a site is seeded into for this tab: a work tab's, or a named profile's own context's (None: nothing to seed)."""
        if self.work is not None and self.work.tab(key) is not None:
            return self.work.tab(key)
        return self._profile_tabs.get(key)

    async def _seed_work_site(self, key: str, site: str) -> None:
        """Before a work tab (or a named profile's tab) goes to `site`: the sign-in for that site (cookies and local storage) is copied
        into the tab's context, once per site: the owner's browser for the default profile, the profile's sealed unit for a named one
        (never the owner's browser). Also notes the site as touched (what the job changed there is written back at close). A failure
        is said on the tab's next reading (SEED_FAILED_NOTE). Counts only in the log."""
        site = registrable(str(site or "").lower())
        wt = self._seed_tab(key)
        if wt is None or not site:
            return
        profile = self.profile_of(key)
        try:
            if profile:
                unit = self.profile_unit(profile, site)
                cookies = [c for c in unit.get("cookies") or []
                           if registrable(str(c.get("domain") or "").lstrip(".").lower()) == site]
                storage = dict(unit.get("storage") or {})
            else:
                cookies = [c for c in await self._main_cookies()
                           if registrable(str(c.get("domain") or "").lstrip(".").lower()) == site]
                storage = await self._main_storage(site)
            if self.work is not None and self.work.tab(key) is not None:
                n = await self.work.seed_site(key, site, cookies, storage)
                self.work.note_touched(key, site)
            else:
                n = await seed_tab(wt, site, cookies, storage)
                wt.touched.add(site)
            self._touch_site(site, profile=profile)
            if not profile and not wt.signed_out:
                wt.owner_had.setdefault(site, len(cookies))   # what the owner's browser held for the site at the first copy (S3)
            state = ("named profile" if profile else "signed-out tab" if wt.signed_out
                     else "sign-in on record" if wt.owner_had.get(site) and self.signed_in_site(site) else "no sign-in")
            log.info("work browser: tab %s, site %s: %d cookies copied of %d in the source, %s", key, site, n, len(cookies), state)
        except Exception:  # noqa: BLE001 - the tab still loads, and the agent is told it may not be signed in
            self._notes.setdefault(key, []).append(SEED_FAILED_NOTE)
            log.info("work browser: tab %s could not be seeded", key)

    def _seed_note(self, key: str, url: str) -> str:
        """Once per site and job tab, on a reading of a page of a site whose sign-in copy found no sign-in in the owner's browser (nothing
        there, or no record of a sign-in): the job knows before a refusal tells it (card p-signin-gaps). "" otherwise."""
        wt = self.work.tab(key) if self.work is not None else None
        site = _site_of(url)
        if wt is None or wt.signed_out or not site or site in wt.told or site not in wt.owner_had:
            return ""
        wt.told.add(site)
        found = wt.owner_had[site]
        if found and self.signed_in_site(site):
            return ""
        return seed_signed_out_note(site, found, self.login_for(site)) + "\n"

    async def _guard_work_context(self, key: str, ctx: Any) -> None:
        """A document request in a work tab's context is seeded first (the route hook of card A3): the route always goes on
        (fallback), also when seeding failed."""
        if not hasattr(ctx, "route"):
            return

        async def guard(route: Any, request: Any) -> None:
            try:
                if request.resource_type == "document":
                    await self._seed_work_site(key, registrable(str(urlsplit(request.url).hostname or "").lower()))
            except Exception:  # noqa: BLE001
                pass
            try:
                await route.fallback()
            except Exception:  # noqa: BLE001 - the page went away
                pass

        try:
            await ctx.route("**/*", guard)
        except Exception:  # noqa: BLE001 - a context without routes works, the copy at open still runs
            log.info("work browser: no route hook for tab %s", key)

    async def _arm_seed(self, key: str, page: Any) -> None:
        """Document requests of the tab's page are also paused on its own DevTools session (card A3, path R+F): a redirect to another host
        is not seen by the context route, so the document is seeded here before it is sent. Every paused request is continued."""
        try:
            cdp = await self.cdp_session(page)
            await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]})
        except Exception:  # noqa: BLE001 - the route hook still seeds the first document of each site
            return

        async def paused(ev: dict[str, Any]) -> None:
            try:
                await self._seed_work_site(key, registrable(str(urlsplit(str(ev["request"]["url"])).hostname or "").lower()))
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    await cdp.send("Fetch.continueRequest", {"requestId": ev["requestId"]})
                except Exception:  # noqa: BLE001 - the page went away
                    pass

        try:
            cdp.on("Fetch.requestPaused", lambda ev: asyncio.ensure_future(paused(ev)))
        except Exception:  # noqa: BLE001
            return
        self._seed_cdp[key] = cdp

    def _forget_tab(self, key: str) -> None:
        """Drop everything kept for a tab's page without closing anything (the page belongs to a connection that is gone)."""
        for d in (self._pages, self._private, self._tab_cdp, self._type_cdp, self._targets, self._opened, self._doc, self._ref_words, self._look_url,
                  self._shot_scale, self._windows):
            d.pop(key, None)

    def _forget_main_tabs(self) -> None:
        """The main browser reconnected: its tabs are forgotten; the work tabs are the work browser's and stay."""
        keys = {k for d in (self._pages, self._private, self._tab_cdp, self._type_cdp, self._targets, self._opened, self._doc, self._ref_words,
                            self._look_url, self._shot_scale) for k in d if not work_tab(k)}
        for k in keys:
            self._forget_tab(k)

    def _window_page(self, key: str) -> Any:
        """The active tab of the window of `key` when its page is alive (None otherwise). A closed tab is dropped from the window; when it
        was the active one, the window goes on in the tab that is active now, and the agent is told which."""
        win = self._windows.get(key)
        if win is None:
            return None
        for tab in list(win.tabs):
            if tab.page.is_closed():
                self._drop_tab(key, tab.page)
        active = win.active_tab()
        if active is None:
            return None
        self._pages[key] = active.page
        return active.page

    def _drop_tab(self, key: str, page: Any) -> None:
        """A tab's page closed: it leaves its window; when that was the active tab, the next one is active and the agent is told."""
        win = self._windows.get(key)
        if win is None:
            return
        tab = win.find(page)
        if tab is None:
            return
        was_active = win.active == tab.id
        win.remove(page)
        self._journal_tab(key, "tab_closed", tab)
        if was_active:
            nxt = win.active_tab()
            if nxt is not None:
                self._pages[key] = nxt.page
                self._notes.setdefault(key, []).append(TAB_CLOSED_NOTE.format(id=nxt.id, title=str(nxt.page.url or "")[:200]))
            else:
                self._pages.pop(key, None)

    def _on_context_page(self, page: Any) -> None:
        """A page of the owner's browser that Kelo did not make (Ctrl+T, Ctrl+N, a link that opens a window of its own). A window opened by a
        tab of a task is that tab's pop-up (_on_popup); any other page is a tab of the window the owner holds, or closed."""
        if self._creating:
            return
        asyncio.ensure_future(self._adopt_owner_page(page))

    async def _adopt_owner_page(self, page: Any) -> None:
        try:
            opener = await page.opener()
        except Exception:  # noqa: BLE001 - no opener known: treated as a page of its own
            opener = None
        if opener is not None and any(win.find(opener) is not None for win in self._windows.values()):
            return   # a pop-up of a task's tab: _on_popup made it a tab already
        held = [k for k in self._windows if self._control.get(k) and any(t.page.context is page.context for t in self._windows[k].tabs)]
        if len(held) != 1:
            await self._quietly(page.close)   # nobody holds this window: a page nobody asked for is closed
            return
        key = held[0]
        win = self._windows[key]
        tab = win.add(page)                    # not activated: the owner switches with the tab chips or the page does
        self._journal_tab(key, "tab_opened", tab)
        await self._adopt(key, page)

    def _journal_tab(self, key: str, kind: str, tab: Any) -> None:
        """While the owner holds the window, what they did with its tabs goes to the takeover journal (C1d; host only)."""
        if self.journal is not None and key in getattr(self.journal, "open", {}):
            try:
                self.journal.tab_event(key, kind, tab.id, str(getattr(tab.page, "url", "") or ""))
            except Exception:  # noqa: BLE001 - the journal never breaks a tab
                pass

    async def _activate(self, key: str, tab_id: str) -> Any:
        """Makes one tab of the window the active one: its DevTools sessions and target move with it, and the live listeners hear of it."""
        win = self._windows[key]
        cur = win.active_tab()
        if cur is not None:
            cur.tab_cdp = self._tab_cdp.pop(key, None)
            cur.target = self._targets.pop(key, None)
        tab = win.activate(tab_id)
        self._pages[key] = tab.page
        self._journal_tab(key, "tab_switched", tab)
        if tab.tab_cdp is not None:
            self._tab_cdp[key] = tab.tab_cdp
        if tab.target is not None:
            self._targets[key] = tab.target
        for fn in list(self.active_listeners):
            try:
                await fn(key)
            except Exception:  # noqa: BLE001 - a listener never breaks switching tabs
                log.info("a tab-switch listener failed")
        return tab.page

    async def window_tabs(self, key: str) -> list[dict[str, Any]]:
        """The tabs of this task's window with their addresses and titles (browser_tabs list)."""
        win = self._windows.get(key)
        if win is None:
            return []
        out = []
        for row in win.listing():
            tab = win.get(row["id"])
            try:
                title = str(await tab.page.title())[:200] if tab is not None and not tab.page.is_closed() else ""
            except Exception:  # noqa: BLE001 - a tab that went away meanwhile
                title = ""
            out.append({**row, "title": title})
        return out

    async def switch_tab(self, key: str, tab_id: str) -> Any:
        """Makes another tab of this task's window the one the task reads and acts in (browser_tabs switch)."""
        win = self._windows.get(key)
        if win is None or win.get(tab_id) is None:
            raise BrowserPolicyError("no such tab in this window")
        page = await self._activate(key, tab_id)
        self._used[key] = self.clock()
        return page

    async def new_tab(self, key: str) -> Any:
        """A new empty tab in this task's window, made the active one (browser_tabs new). A work, private or named-profile window is its
        context, so the tab is a page of that context; a main-browser window is not offered (see the note below)."""
        win = self._windows.get(key)
        if win is None or win.active_tab() is None:
            raise BrowserPolicyError("this task has no window yet: open a page first")
        cur = win.active_tab().page
        if self.work is not None and work_tab(key) or key in self._private or key in self._profile_ctx:
            self._creating += 1
            try:
                page = await cur.context.new_page()
            finally:
                self._creating -= 1
        else:
            # live check (test_work_seed_live.py, C1c): Chrome opens such a tab in a new window, not in this task's: not offered here
            raise BrowserPolicyError("a new tab in this window is not offered in the owner's browser; the task's own windows (its jobs) have it")
        new = win.add(page)
        self._watch(page, key)
        await self._activate(key, new.id)
        return page

    async def close_window_tab(self, key: str, tab_id: str) -> None:
        """Closes one tab of this task's window (browser_tabs close). The last tab of a window goes with the task, not here."""
        win = self._windows.get(key)
        tab = win.get(tab_id) if win is not None else None
        if tab is None:
            raise BrowserPolicyError("no such tab in this window")
        if len(win.tabs) <= 1:
            raise BrowserPolicyError("the last tab closes with the task")
        was_active = win.active == tab.id
        if was_active:
            nxt = next(t for t in win.tabs if t is not tab)
            await self._activate(key, nxt.id)
        page = tab.page
        self._drop_tab(key, page)                       # out of the window first: the close event then finds nothing to do
        win.remove(page)
        await self._quietly(page.close)

    async def _new_window(self, ctx: Any) -> Any:
        """A page in a Chrome window of its own (a task never shares a window with another task). Falls back to a tab of the context
        when the browser cannot be told to open a window."""
        try:
            bcdp = await self._browser_cdp()
            async with ctx.expect_page() as got:
                await bcdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True})
            return await got.value
        except Exception:  # noqa: BLE001 - a browser that does not do windows: a tab of the context (no window of its own)
            return await ctx.new_page()

    async def _browser_cdp(self) -> Any:
        """The browser-wide DevTools session of the main browser (kept)."""
        if getattr(self, "_bcdp", None) is None:
            self._bcdp = await self._browser.new_browser_cdp_session()
        return self._bcdp

    def _on_popup(self, key: str, opener: Any, popup: Any) -> None:
        """A page of this tab opened a window: it becomes a tab of the task's window, and the active one (the user's live view follows).
        A window opened by a page the tab already left is not the tab's."""
        if self._pages.get(key) is not opener or key not in self._windows:
            return
        win = self._windows[key]
        parent = win.find(opener)
        cur = win.active_tab()
        if cur is not None:
            cur.tab_cdp = self._tab_cdp.pop(key, None)
            cur.target = self._targets.pop(key, None)
        new = win.add(popup, opener=parent.id if parent is not None else "")
        win.activate(new.id)
        self._pages[key] = popup
        self._journal_tab(key, "tab_opened", new)
        self._popup_setup[key] = asyncio.ensure_future(self._adopt(key, popup))

    async def _adopt(self, key: str, popup: Any) -> None:
        """Set up a window the tab moved into like a tab of its own: pop-ups answered, time zone and downloads."""
        self._watch(popup, key)
        if self.prepare_pages:
            try:
                await self._prepare(popup, key)
            except Exception:  # noqa: BLE001 - the window works without them
                pass

    async def _ensure_browser(self) -> None:
        """Connect to the browser container (first use, or after it restarted). Everything tied to the old connection is forgotten."""
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()
        if self._ctx is None or not self._browser.is_connected():
            # a connection that dropped (the browser restarted or crashed): its tabs' pages are gone; each tab's next step says so
            if self._browser is not None:
                self._lost.update({key: str(getattr(p, "url", "") or "") for key, p in self._pages.items()
                                   if key not in self._private and not p.is_closed()})
            # Chrome's DevTools HTTP endpoint rejects Host headers that are not an IP or localhost.
            ip = (await asyncio.to_thread(socket.gethostbyname, self.cdp_host))
            self._browser = await self._pw.chromium.connect_over_cdp(f"http://{ip}:{self.cdp_port}")
            self._ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
            self._ctx.on("page", self._on_context_page)   # a page the owner opens by hand (C1d)
            self._forget_main_tabs()
            self._dl_cdp, self._dl_contexts = None, set()
            self._child_locales.clear()
            await self._start_children(ip)

    async def _start_children(self, ip: str) -> None:
        """The frames and workers of the main browser's tabs (child_targets.py): one DevTools connection, started with the
        connection. Without it their language is the browser's own; that is logged, not raised."""
        old, self._children = self._children, None
        self._children = await self._open_children(ip, self.cdp_port, old, "main")

    async def work_connected(self, host: str, port: int) -> None:
        """The work browser (re)connected: its frames and workers get the same treatment, over its own connection."""
        ip = await asyncio.to_thread(socket.gethostbyname, host)
        old, self._work_children = self._work_children, None
        self._work_children = await self._open_children(ip, port, old, "work")

    async def _open_children(self, ip: str, port: int, old: ChildTargets | None, which: str) -> ChildTargets | None:
        if old is not None:
            await old.close()
        try:
            import websockets
            info = json.loads(await asyncio.to_thread(_devtools_version, ip, port))
            url = re.sub(r"^wss?://[^/]+", f"ws://{ip}:{port}", str(info["webSocketDebuggerUrl"]))
            children = ChildTargets(await websockets.connect(url, max_size=None, ping_interval=None), self._child_locale)
            await children.start()
            return children
        except Exception as e:  # noqa: BLE001 - the tab's main frame is still covered
            log.warning("the frames and workers of the %s browser are not covered: %s", which, type(e).__name__)
            return None

    def _child_locale(self, info: dict[str, Any], parent: str) -> str:
        """The locale of a frame or a worker of a desktop tab: its tab's (the user's own language; "" = the browser's). A shared or
        service worker takes the locale of a desktop tab that shows its site (its page is not known to the browser)."""
        kind, tid = str(info.get("type") or ""), str(info.get("targetId") or "")
        if kind in (FRAME_TYPE, "worker"):
            loc = self._child_locales.get(parent, "") or self._page_locale(parent)
            if kind == FRAME_TYPE and tid:
                self._child_locales[tid] = loc
            return loc
        if kind in ("shared_worker", "service_worker"):
            site = _site_of(str(info.get("url") or ""))
            for key, page in list(self._pages.items()):
                if site and _site_of(str(getattr(page, "url", "") or "")) == site:
                    return self._tab_locales.get(key, "")
        return ""

    def _page_locale(self, target_id: str) -> str:
        for key, (tid, _) in self._targets.items():
            if tid and tid == target_id:
                return self._tab_locales.get(key, "")
        return ""


    @staticmethod
    async def _desktop_identity(page: Any, accept: str) -> dict[str, Any] | None:
        """The browser's own User-Agent with the user's language list, and the client hints Google Chrome of this version sends on this
        machine (browser_identity.identity_for: the stated OS, Linux by default, this CPU's architecture, no model, desktop). The override must carry the metadata:
        without it Chrome sends no Sec-CH-UA headers any more (measured on Chromium 2026-11-18). None when the version is not known."""
        ua = str(await page.evaluate("() => navigator.userAgent") or "")
        m = re.search(r"Chrome/(\d+\.\d+\.\d+\.\d+)", ua)
        if not m:
            return None
        return {"userAgent": ua, "acceptLanguage": accept, "userAgentMetadata": _chrome_override(m.group(1))["userAgentMetadata"]}

    async def _prepare(self, page: Any, key: str) -> None:
        """A new tab gets the user's time zone and language (Emulation overrides, kept for the tab's life by its own CDP session), is
        kept active and focused as if in front (Page.setWebLifecycleState, Emulation.setFocusEmulationEnabled), and its downloads go
        to the download area. Nothing here fakes the browser: the time zone and language are the user's own."""
        try:
            cdp = await self.cdp_session(page)
        except Exception:  # noqa: BLE001 - a tab without DevTools access works, just without these
            return
        self._tab_cdp[key] = cdp
        tz = (self.timezone() or "").strip()
        locale = tab_locale(self.user_language(), self.locale)   # the user's own language; the browser's default when none is set
        for method, params in (("Emulation.setTimezoneOverride", {"timezoneId": tz} if tz else None),
                               ("Emulation.setLocaleOverride", {"locale": locale} if locale else None)):
            if params:
                try:
                    await cdp.send(method, params)
                except Exception:  # noqa: BLE001 - an unknown zone name: the browser's own stays
                    pass
        self._tab_locales[key] = locale   # the frames and workers of this tab keep its language (_child_locale)
        accept = accept_language(locale)
        if accept:   # the user's Accept-Language (and navigator.languages) with the browser's own User-Agent and client hints
            try:
                ident = await self._desktop_identity(page, accept)
                if ident is not None:
                    await cdp.send("Emulation.setUserAgentOverride", ident)
            except Exception:  # noqa: BLE001 - the browser's own list stays
                pass
        # a tab the agent works in is often not the one in front (the user's live view, another phone's tab): pages that pause in a
        # hidden or unfocused tab (timers, animations, "you left the page" checks) keep working as in a tab in front
        for method, params in (("Page.setWebLifecycleState", {"state": "active"}),
                               ("Emulation.setFocusEmulationEnabled", {"enabled": True})):
            try:
                await cdp.send(method, params)
            except Exception:  # noqa: BLE001 - an older browser: the tab works as it is
                pass
        try:
            info = (await cdp.send("Target.getTargetInfo", {})).get("targetInfo") or {}
            self._targets[key] = (str(info.get("targetId") or ""), str(info.get("browserContextId") or ""))
        except Exception:  # noqa: BLE001
            pass
        if self.book is not None and key not in self._private and self._targets.get(key, ("", ""))[0]:
            self.book.save(key, self._targets[key][0], str(getattr(page, "url", "") or ""))
        await self._route_downloads(key)

    # ------------------------------------------------------------------ downloads (downloads.py)
    async def _route_downloads(self, key: str) -> None:
        if self.downloads is None:
            return
        _, context = self._targets.get(key, ("", ""))
        if self.work is not None and work_tab(key):
            await self._route_work_downloads(key, context)
            return
        if context and context in self._dl_contexts:
            return
        try:
            if self._dl_cdp is None:
                if self._browser is None or not hasattr(self._browser, "new_browser_cdp_session"):
                    return
                bs = await self._browser.new_browser_cdp_session()
                bs.on("Browser.downloadWillBegin", self._download_began)
                bs.on("Browser.downloadProgress", self._download_moved)
                self._dl_cdp = bs
            params: dict[str, Any] = {"behavior": "allowAndName", "downloadPath": self.downloads.browser_dir, "eventsEnabled": True}
            if key in self._private and context:
                params["browserContextId"] = context
            await self._dl_cdp.send("Browser.setDownloadBehavior", params)
            self._dl_contexts.add(context)
        except Exception:  # noqa: BLE001 - downloads then stay with the browser's default handling
            pass

    async def _route_work_downloads(self, key: str, context: str) -> None:
        """Downloads of a work tab: the work browser's own session, always for the tab's own context (never the default one)."""
        try:
            if self._work_dl is None:
                bs = await self.work.browser_cdp()
                bs.on("Browser.downloadWillBegin", self._download_began)
                bs.on("Browser.downloadProgress", self._download_moved)
                self._work_dl = bs
            params: dict[str, Any] = {"behavior": "allowAndName", "downloadPath": self.downloads.browser_dir, "eventsEnabled": True}
            if context:
                params["browserContextId"] = context
            await self._work_dl.send("Browser.setDownloadBehavior", params)
        except Exception:  # noqa: BLE001 - downloads then stay with the work browser's default handling
            pass

    def _tab_of_frame(self, frame_id: str) -> str:
        """The tab a download came from: its main frame is the tab's target; a download from an inner frame goes to the tab used last."""
        hit = next((k for k, (t, _) in self._targets.items() if t and t == frame_id), "")
        if hit:
            return hit
        open_ = [k for k in self._pages if self.has_tab(k)]
        return max(open_, key=lambda k: self._used.get(k, 0.0), default="owner")

    def _download_began(self, params: dict[str, Any]) -> None:
        if self.downloads is None:
            return
        key = self._tab_of_frame(str(params.get("frameId") or ""))
        d = self.downloads.begin(key, params, context=self._targets.get(key, ("", ""))[1])
        if d.state == "blocked":
            asyncio.ensure_future(self._cancel_download(d))

    def _download_moved(self, params: dict[str, Any]) -> None:
        if self.downloads is not None and (d := self.downloads.progress(params)) is not None:
            asyncio.ensure_future(self._cancel_download(d))

    async def _cancel_download(self, d: Any) -> None:
        work = self.work is not None and work_tab(d.key)
        params: dict[str, Any] = {"guid": d.guid}
        if d.context and (work or d.key in self._private):
            params["browserContextId"] = d.context
        try:
            await (self._work_dl if work else self._dl_cdp).send("Browser.cancelDownload", params)
        except Exception:  # noqa: BLE001 - it finished or went away meanwhile
            pass

    def _watch(self, page: Any, key: str) -> None:
        """A page's own pop-up (alert, confirm, prompt, leaving the page) is answered here (page_dialogs.answer_for: OK only for the
        user or what they allowed) and kept for the next page reading (dialog_note) and the live view (page_dialog)."""
        if not hasattr(page, "on"):
            return

        def on_crash(*_args: Any) -> None:   # the page's renderer crashed: the tab's next step reads a new blank page (BROWSER_LOST_NOTE)
            if self._pages.get(key) is page:
                self._pages.pop(key, None)
                self._lost[key] = str(getattr(page, "url", "") or "")
                self._told_lost(key)

        try:
            page.on("crash", on_crash)
        except Exception:  # noqa: BLE001 - a page object without the event works as before
            pass

        def on_closed(*_args: Any) -> None:   # a tab's page closed (the site closed its window, or the owner did): it leaves the window
            if key in self._windows and self._windows[key].find(page) is not None:
                self._drop_tab(key, page)

        try:
            page.on("close", on_closed)
        except Exception:  # noqa: BLE001 - a page object without the event works as before
            pass

        def on_navigated(frame: Any) -> None:   # card A3: the main frame of a work tab went to a site (noted for the write-back)
            if work_tab(key) and self.work is not None and getattr(frame, "parent_frame", True) is None:
                host = (urlsplit(str(getattr(frame, "url", "") or "")).hostname or "").lower()
                if host:
                    self.work.note_touched(key, registrable(host))

        if work_tab(key):
            try:
                page.on("framenavigated", on_navigated)
            except Exception:  # noqa: BLE001
                pass

        async def on_dialog(dialog: Any) -> None:
            url = str(getattr(page, "url", "") or "")
            host = (urlsplit(url).hostname or "").lower()
            kind = str(getattr(dialog, "type", "") or "")
            message = str(getattr(dialog, "message", "") or "")
            token = browser_owner.set(key)
            try:
                by_user = key in self._control   # the user is at the controls: a pop-up their tap caused is theirs to accept
                in_purchase = self.purchase_active(host) or self.cancel_active(host)
                in_order = not by_user and not in_purchase and self.order_dialog is not None and self.order_dialog(key, host, message)
            finally:
                browser_owner.reset(token)
            answer, _why = answer_for(kind, by_user=by_user, in_purchase=in_purchase, in_order=bool(in_order))
            self._tab_dialogs(key).add(kind, message[:300], answer, _why, url, time.time())
            try:
                await (dialog.accept() if answer == "ok" else dialog.dismiss())
            except Exception:  # noqa: BLE001 - the pop-up may already be gone
                pass

        def on_response(resp: Any) -> None:   # the main document's status and headers: evidence for bot-check detection
            try:
                if resp.request.resource_type != "document" or resp.frame != page.main_frame:
                    return
                keep = {k.lower(): str(v)[:200] for k, v in dict(resp.headers or {}).items()
                        if k.lower() in _CHECK_HEADERS or k.lower().startswith(("x-px", "x-datadome", "x-dd-", "x-amzn-waf", "x-akamai"))}
                self._doc[key] = (int(resp.status), keep)
            except Exception:  # noqa: BLE001
                pass

        try:
            page.on("dialog", on_dialog)
            page.on("response", on_response)
            page.on("popup", lambda popup: self._on_popup(key, page, popup))
        except Exception:  # noqa: BLE001
            pass

    async def _guard(self, url: str) -> None:
        await check_url(url, allow_private=self.allow_private, resolver=self._resolver,
                        internal_names=frozenset({self.cdp_host, "server"}), lookup=not self.remote_dns())

    async def _vpn_down(self, error: Exception) -> str:
        """The page failed to load through a proxy tunnel: when the VPN connector is on and not connected, that is the reason (fail closed)."""
        if self.vpn_note is None or not re.search(r"ERR_(TUNNEL_CONNECTION_FAILED|PROXY_CONNECTION_FAILED|EMPTY_RESPONSE|CONNECTION_(CLOSED|RESET|REFUSED)|"
                                                  r"TIMED_OUT|NAME_NOT_RESOLVED)|Timeout", str(error)):
            return ""
        try:
            return await self.vpn_note()
        except Exception:  # noqa: BLE001 - the original error is shown instead
            return ""

    def busy(self, within: float = 120.0) -> bool:
        """A tab was used by a tool (or watched, or held) in the last `within` seconds: a turn or job is browsing. The VPN connector
        waits for it to be quiet before it changes the route (vpn.py)."""
        now = self.clock()
        return any(now - at < within for key, at in list(self._used.items()) if self.has_tab(key))

    def purchase_open(self) -> bool:
        """A purchase window is open on any tab (an allowed purchase that is being finished): a route change waits for it."""
        now = time.monotonic()
        return any(left > 0 and now < until for _h, until, left in list(self._purchase.values()))

    async def _tree(self, page: Any) -> str | None:
        """The page as its accessibility tree with refs (page_tree.py), frames inside it included; None when it cannot be read
        (an old driver, a page that is changing, SNAPSHOT_MS): then the page's plain text is shown."""
        try:
            tree = await page.aria_snapshot(mode="ai", timeout=SNAPSHOT_MS)
        except Exception:  # noqa: BLE001
            return None
        return tree if isinstance(tree, str) and tree.strip() else None

    async def _frame_hosts_note(self, page: Any, tree: str) -> str:
        """The iframe lines of a reading get the host the frame comes from (the words in it are that host's, not the page's)."""
        lines = tree.splitlines()
        n = 0
        for i, line in enumerate(lines):
            m = re.match(r"^(\s*)- iframe\b.*\[ref=((?:f\d+)?e\d+)\]", line)
            if not m or n >= 12:
                continue
            n += 1
            try:
                handle = await asyncio.wait_for(page.locator(f"aria-ref={m.group(2)}").element_handle(timeout=1_000), 1.5)
                frame = await handle.content_frame() if handle is not None else None
                if frame is None:
                    continue
                host = urlsplit(str(frame.url or "")).hostname or "this page"
            except Exception:  # noqa: BLE001
                continue
            lines[i] = line + f"\n{m.group(1)}  [A frame inside the page, from {host}:]"
        return "\n".join(lines)

    async def snapshot(self, *, handing: bool = True, clear: bool = False, changes: bool = False, only_controls: bool = False) -> str:
        """The page as text for the agent (see _snapshot); with a task window of more than one tab, its first line says which tab this is."""
        text = await self._snapshot(handing=handing, clear=clear, changes=changes, only_controls=only_controls)
        win = self._windows.get(browser_owner.get())
        if win is None or len(win.tabs) < 2 or win.active_tab() is None:
            return text
        active = win.active_tab()
        title = ""
        try:
            title = str(await active.page.title())[:120]
        except Exception:  # noqa: BLE001 - the header still names the tab
            pass
        return f"[tab {active.id} of {len(win.tabs)}: {title}]\n" + text

    async def _snapshot(self, *, handing: bool = True, clear: bool = False, changes: bool = False, only_controls: bool = False) -> str:
        """The page as text for the agent. A human check on it: the agent may first try it itself (`check_tries` readings that still
        show it, `try_note`); then it is handed to the user (when the app set `handover` for this turn and `handing`): the step waits
        until it is solved, skipped or runs out, and the note says which.
        A window covering the page is closed with its own close button first, or reported when it has none (page_notes.py), on a
        page (address) not read before or with `clear` (opened, or read again). Otherwise a step on the same page opened it (a date
        picker or menu the agent's click opened): it is the agent's own and left alone from then on. Values that look masked or
        not loaded get a note. `changes` (a click or typing): on the same page as the last reading, only what changed is shown.
        The page is shown as its accessibility tree with refs (`button "Go" [ref=e5]`; `only_controls`: only what can be clicked or
        typed into, plus headings); when that cannot be read, as its plain text with the frames' text after it."""
        page = await self.page()
        layers = await self._layers(page, browser_owner.get(), close=clear)
        try:
            title = await asyncio.wait_for(page.title(), CDP_SECONDS)
            text = await evaluate(page, "() => document.body ? document.body.innerText : ''")
        except asyncio.TimeoutError:   # the page does not answer (a navigation that never completes): the step is stopped, not stuck
            raise ToolTimeout() from None
        text = (text or "").strip()
        cut = ""
        if len(text) > MAX_TEXT_CHARS:
            cut = f"\n[page text truncated, {len(text) - MAX_TEXT_CHARS} more chars]"
            text = text[:MAX_TEXT_CHARS]
        key = browser_owner.get()
        popups = self._dialogs[key].take() if key in self._dialogs else []
        # the page's own words (title, text, pop-ups) are marked as external data: the loop fences them, and keeps these lines of
        # ours (address, notes, hand-over) outside (agent/fence.py)
        said = "".join(dialog_note(d, site=_site_of(d.url), login=self.login_for(_site_of(d.url)) if d.answer == "cancel" else "",
                                   job=self._is_job()) + "\n" for d in popups)
        said += "".join(f"{n}\n" for n in self._notes.pop(key, []))
        self._owner_moved.discard(key)
        self._lost_act.discard(key)
        said += "".join(f"{n}\n" for n in layers)
        if spots := masked_values(text):
            said += masked_note(spots, data) + "\n"
        said += await self._lists_note(page)
        said += self._seed_note(key, str(getattr(page, "url", "") or ""))
        check, otp = await self._look(page, key)
        if check is None and (why := self._denied(key, page, title, text)):
            host = urlsplit(str(page.url or "")).hostname or ""
            site = _site_of(str(page.url or ""))
            if site in self._pass_opened:   # the site is not signed in: the next step is the sign-in (brief B2)
                self._pass_opened.discard(site)
                said += signed_out_note(host, self.login_for(site), job=self._is_job(), minutes=SIGN_IN_PASS_SECONDS // 60) + "\n"
            else:
                said += blocked_note(host, why, count=self._blocks(host)) + "\n" + (blocked_again_note(host) + "\n" if self._blocks(host) >= self._block_limit() else "")
        if check is not None:
            host = urlsplit(str(page.url or "")).hostname or ""
            left = self._try_left(key, host) if handing else 0
            if left > 0:
                said += try_note(check, host, left) + "\n"     # the agent's own try first; nobody is told yet
            else:
                self._announce(key, check, host)
                hand = handover.get() if handing else None
                if hand is None:
                    said += note_for(check, host) + "\n"
                else:
                    with self.pause_clock(key):
                        outcome, minutes = await self._hand_over(hand, key, check, host)
                    if outcome in ("solved", "done"):
                        again = await self.snapshot(handing=False)   # the page as it is now (no second hand-over from this step)
                        return handover_note(outcome, host) + "\n" + again
                    said += handover_note(outcome, host, round(minutes)) + "\n"
        else:
            self._check_clicked.pop(key, None)
            if self._tries.pop(key, None) is not None and handing:
                said += "[The human check is gone: go on from the page below.]\n"
            if handing and key in self._pending_hand and verification_step(self._probes.get(key), signing_in=True) is None:
                # the hand-over raised while the agent chose the page's options, and the page has gone on meanwhile: its end
                with self.pause_clock(key):
                    note, solved = await self._finish_hand(key, *self._pending_hand.pop(key))
                if solved:
                    return note + "\n" + await self.snapshot(handing=self._handed.get(key) == "progressed")
                said += note + "\n"
            ver = None if self.in_checkout() else verification_step(self._probes.get(key), signing_in=self._signing_in(key))
            if ver is not None:   # a sign-in verification: the user is told what to do, and the step waits for the page to move on
                with self.pause_clock(key):
                    note, solved = await self._verify(page, key, ver, handing)
                if solved:   # after a step that only moved on, the next page may need the user again (a new hand-over)
                    return note + "\n" + await self.snapshot(handing=self._handed.get(key) == "progressed")
                said += note + "\n"
            elif otp and self.has_mailbox():
                said += OTP_NOTE + "\n"
        tree = await self._tree(page)
        if tree is not None:   # the frames are inside the tree
            self._ref_words[key] = ref_words(tree)
            tree = await self._frame_hosts_note(page, for_reading(controls_only(tree) if only_controls else tree))
            cut = ""
            if len(tree) > MAX_TREE_CHARS:
                cut = f"\n[page text truncated, {len(tree) - MAX_TREE_CHARS} more chars]"
                tree = tree[:MAX_TREE_CHARS]
            body, frames, text = tree, [], tree
        else:
            frames = await self._frames_text(page)
            body = text + "".join(f"\n\n[A frame inside the page, from {h}:]\n{ft}{more}" for h, ft, more in frames)
        url = str(page.url or "").split("#", 1)[0]
        before = self._last_text.get(key)
        self._last_text[key] = (url, body)
        if changes and self.changes_only and before is not None and before[0] == url:
            shown = changed_lines(before[1], body)
            if shown is not None:
                if not shown:
                    return f"URL: {page.url}\nTitle: {data(title)}\n{said}{NO_TEXT_CHANGE}"
                return f"URL: {page.url}\nTitle: {data(title)}\n{said}{CHANGES_HEAD}\n{data(shown)}"
        return (f"URL: {page.url}\nTitle: {data(title)}\n{said}\n{data(text)}{cut}"
                + "".join(f"\n\n[A frame inside the page, from {h}:]\n{data(ft)}{more}" for h, ft, more in frames))

    @staticmethod
    async def _frames_text(page: Any) -> list[tuple[str, str, str]]:
        """The visible text of the frames inside the page (an embedded form, game or checkout, also from another host), after the
        page's own text: what the agent can click there (browser_click finds selectors inside frames). Tiny frames are skipped."""
        try:
            frames = [f for f in page.frames if f is not page.main_frame]
        except Exception:  # noqa: BLE001
            return []
        out: list[tuple[str, str, str]] = []   # (host, text, cut note)
        left = FRAME_TEXT_CHARS
        for f in frames[:12]:
            if left <= 0:
                break
            try:
                area, text = await asyncio.wait_for(f.evaluate(FRAME_TEXT_JS), 2.0)
            except Exception:  # noqa: BLE001 - a frame that is loading, gone or closed to scripts
                continue
            text = str(text or "").strip()
            if float(area or 0) < _FRAME_MIN_AREA or not text:
                continue
            host = urlsplit(str(f.url or "")).hostname or "this page"
            more = f" [frame text truncated, {len(text) - left} more chars]" if len(text) > left else ""
            out.append((host, text[:left], more))
            left -= len(text)
        return out

    async def _layers(self, page: Any, key: str, *, close: bool) -> list[str]:
        """Windows covering the page (page_notes.OVERLAY_JS): closed with their own close button when `close`, one at a time and
        looked at again after each, else remembered as the agent's own. Returns the notes for the page reading."""
        if key in self._tries or key in self._checks or not hasattr(page, "evaluate"):
            return []   # a human check is on this tab: never touch it
        url = str(getattr(page, "url", "") or "").split("#")[0]
        if self.in_checkout():
            return []   # a payment or booking window is never closed for the user
        if self._own_layers.get(key, ("", set()))[0] != url:
            self._own_layers[key] = (url, set())
            close = True   # a page not read before: what covers it is the site's, not the agent's
        own = self._own_layers[key][1]
        shut: list[dict[str, Any]] = []
        notes: list[str] = []
        for round_ in range(OVERLAY_ROUNDS + 1):
            try:
                found = overlays(await evaluate(page, OVERLAY_JS))
            except Exception:  # noqa: BLE001 - a page that is changing: read it as it is
                found = []
            if not close:
                # a step of the agent on the same page: what is open now is its own (a date picker, a menu) and left alone; only an
                # "install the app" layer is never the agent's own, so one that appears now (after typing, a click) is closed too
                fresh = [f for f in found if f.get("app") and signature(f) not in own]
                if not fresh:
                    own.update(signature(f) for f in found)
                    return []
                own.update(signature(f) for f in found if not f.get("app"))
                found, close = fresh, True
            found = [f for f in found if signature(f) not in own]
            still = {signature(f) for f in found}
            notes = [open_note(f, data, tried=True) if signature(f) in still else closed_note(f, data) for f in shut]
            tried = {signature(f) for f in shut}
            pick = next((f for f in found if f["close"] is not None and "close_at" in f and signature(f) not in tried), None)
            if pick is None and round_ < OVERLAY_ROUNDS:
                app = next((f for f in found if f.get("app") and signature(f) not in tried), None)
                if app is not None and await self._dismiss(page, app):   # no control to press: Escape, then the dim area around the sheet
                    shut.append(app)
                    if self.overlay_pause:
                        await asyncio.sleep(self.overlay_pause)
                    continue
            if pick is None or round_ == OVERLAY_ROUNDS:
                notes += [open_note(f, data) for f in found if signature(f) not in tried]
                break
            try:   # a person's click at the control's centre: a trusted event, no mark, no JavaScript click
                await asyncio.wait_for(self._press_at(page, key, pick["close_at"]), OVERLAY_CLICK_MS / 1000)
            except Exception:  # noqa: BLE001 - covered or moving: the window is read again on the next round
                pass
            shut.append(pick)
            if self.overlay_pause:
                await asyncio.sleep(self.overlay_pause)
        return notes

    @staticmethod
    def _task() -> str:
        from .task_permission import current_scope
        return current_scope()

    def _denied(self, key: str, page: Any, title: str, text: str) -> str:
        """The page is an access-denied answer (challenge.blocked): what the site said ("" when it is not), and it is counted for its host."""
        status, headers = self._doc.get(key, (None, {}))
        why = blocked(status, headers, title, text)
        if not why:
            return ""
        host = urlsplit(str(page.url or "")).hostname or ""
        count, _ = self._blocked.get((self._task(), host), (0, 0.0))
        self._blocked[(self._task(), host)] = (count + 1, self.clock())
        site = _site_of(str(page.url or ""))
        self._pause_site(site)   # a refusal spends nothing more: the site is not loaded for a while
        if site in self._sign_in_pass:   # a refusal in the pass ends it
            self._sign_in_pass.pop(site, None)
        elif site and not self.signed_in_site(site) and site not in self._sign_in_pass_used:
            self._sign_in_pass[site] = self.clock() + SIGN_IN_PASS_SECONDS
            self._sign_in_pass_used.add(site)
            self._pass_opened.add(site)
        return why

    def end_sign_in_pass(self, site: str) -> None:
        """A sign-in attempt on the site ended without success: the pass closes and the pause stays."""
        self._sign_in_pass.pop(_site_of(site) or site, None)

    def _pause_site(self, site: str) -> None:
        if site:
            self._refused_until[site] = max(self._refused_until.get(site, 0.0), self.clock() + REFUSAL_PAUSE_SECONDS)

    def pause_note(self, url: str) -> str:
        """The words for the site of `url` while it is paused after a refusal ("" = it may be loaded)."""
        site = _site_of(url or "")
        if not site or site not in self._refused_until:
            return ""
        now = self.clock()
        if self._sign_in_pass.get(site, 0.0) > now:   # a sign-in pass for this site: it may load
            return ""
        if self._refused_until[site] <= now:
            self._refused_until.pop(site, None)
            return ""
        return REFUSAL_PAUSE_NOTE.format(site=site, minutes=max(1, math.ceil((self._refused_until[site] - now) / 60)))

    def refusal_pauses(self) -> list[dict[str, Any]]:
        """The sites paused after a refusal, with the minutes left (the owner's list; expired pauses are dropped)."""
        now = self.clock()
        for site in [s for s, until in self._refused_until.items() if until <= now]:
            self._refused_until.pop(site, None)
        return [{"site": s, "minutes_left": max(1, math.ceil((until - now) / 60))} for s, until in self._refused_until.items()]

    def clear_pause(self, site: str) -> bool:
        """The owner clears a site's pause: the browser may load it again at once. False when it was not paused."""
        key = _site_of(site) or site
        self._sign_in_pass.pop(key, None)
        self._sign_in_pass_used.discard(key)
        return self._refused_until.pop(key, None) is not None

    def _blocks(self, host: str) -> int:
        """Access-denied answers of `host` in this task within the last BLOCK_SECONDS."""
        count, at = self._blocked.get((self._task(), host), (0, 0.0))
        return count if count and self.clock() - at < BLOCK_SECONDS else 0

    @staticmethod
    def _is_job() -> bool:
        try:
            from ..workers import current_worker
            return bool(current_worker.get())
        except Exception:
            return False

    def _block_limit(self) -> int:
        return 3 if self._is_job() else BLOCK_LIMIT

    def blocked_site(self, url: str) -> str:
        """The words for the agent when `url`'s host has already blocked it BLOCK_LIMIT times in this task ("" = go ahead)."""
        if (paused := self.pause_note(url)):
            return paused
        host = urlsplit(url or "").hostname or ""
        return blocked_again_note(host) if host and self._blocks(host) >= self._block_limit() else ""

    async def _dismiss(self, page: Any, layer: dict[str, Any]) -> bool:
        """An "install the app" layer with no control that closes it: the Escape key, then one tap on the dim area around its sheet.
        True when the layer is gone after that. Nothing is removed from the page and no control of the layer is used."""
        async def gone() -> bool:
            try:
                return all(signature(f) != signature(layer) for f in overlays(await evaluate(page, OVERLAY_JS)))
            except Exception:  # noqa: BLE001
                return False
        try:
            await asyncio.wait_for(page.keyboard.press("Escape"), CDP_SECONDS)
            await asyncio.sleep(self.overlay_pause)
            if await gone():
                return True
            if layer.get("backdrop"):
                x, y = layer["backdrop"]
                await asyncio.wait_for(page.mouse.click(x, y), CDP_SECONDS)
                await asyncio.sleep(self.overlay_pause)
                return await gone()
        except Exception:  # noqa: BLE001 - a page that does not take keys or clicks: the layer is reported instead
            pass
        return False

    def _look_pause(self, *, glance: bool) -> Callable[[Any], Awaitable[Any]]:
        """The pause after a step: a person's look at the page (mouse drift)."""
        return human.glance if glance else human.dwell

    async def _press_at(self, page: Any, key: str, at: list[float]) -> None:
        """One press at a point of the page's viewport (CSS px), with the mouse, like a person's."""
        await human.click_at(page, float(at[0]), float(at[1]))

    async def _bring_into_view(self, page: Any, owner: Any, selector: str, key: str) -> None:
        """Scroll the element into view as a person does, before the covered check and the step (addendum 8): the mouse wheel
        (humanize.bring_into_view). A failure leaves the page as it is: the step then tries as before."""
        try:
            await human.bring_into_view(owner, owner.locator(selector).first, 10_000)
        except Exception:  # noqa: BLE001 - the step tries as before
            log.debug("could not bring the element into view", exc_info=True)

    async def _covered(self, page: Any, owner: Any, selector: str, key: str) -> str:
        """Before a click or typing step: "" when the element can be hit, else the words of the layer that covers it once the
        layer could not be closed (a window over the page: the step would only wait for a timeout). A covering layer is closed first
        (page_notes.OVERLAY_JS, once) and the element looked at again."""
        await self._bring_into_view(page, owner, selector, key)
        async def hit() -> bool:   # True = something else is on top of the element's centre
            try:
                return bool(await asyncio.wait_for(owner.locator(selector).first.evaluate(COVERED_JS), CDP_SECONDS))
            except Exception:  # noqa: BLE001 - not on the page yet, in a frame we cannot ask, or no answer: the step tries as before
                return False
        if not await hit() or key in self._tries or key in self._checks:
            return ""
        try:
            found = overlays(await evaluate(page, OVERLAY_JS))
        except Exception:  # noqa: BLE001
            return ""
        own = self._own_layers.get(key, ("", set()))[1]
        if not found or all(signature(f) in own and not f.get("app") for f in found):
            return ""   # not a window, or one the agent's own step opened (a date picker): the step tries as before
        notes = await self._layers(page, key, close=True)
        if notes:
            self._notes.setdefault(key, []).extend(notes)
        if not await hit():
            return ""
        try:
            left = overlays(await evaluate(page, OVERLAY_JS))
        except Exception:  # noqa: BLE001
            left = found
        return left[0]["label"][:80] if left else found[0]["label"][:80]

    async def _hand_over(self, hand: Callable[..., Any], key: str, check: Challenge, host: str) -> tuple[str, float]:
        """Hand a human check on this tab to the user and wait (the app's `handover`: a card in the chat and on the Work page, a
        notification). Returns how it ended (solved | done | skipped | repeat | expired | closed | cancelled) and the minutes waited."""
        await self.card(t("Waiting for you to confirm you are a person"), force=True)
        started = time.monotonic()
        outcome = str(await hand({"tab": key, "site": host, "vendor": check.vendor, "name": check.name, "kind": check.kind},
                                 lambda: self.wait_check_gone(key)))
        self._handed[key] = outcome
        if outcome in ("solved", "done"):
            self._checks.pop(key, None)
            self._tries.pop(key, None)
        return outcome, (time.monotonic() - started) / 60

    def _try_left(self, key: str, host: str) -> int:
        """Count one page reading that shows a human check: how many of the agent's own tries are left before it goes to the user
        (the first reading is the sighting, each later one ends a try). A new site starts again; a check the agent gave up on, or one
        already handed over, has none."""
        st = self._tries.get(key)
        if st is None or st["site"] != host:
            st = self._tries[key] = {"site": host, "seen": 0, "out": False}
        if st["out"] or self.check_tries <= 0:
            st["out"] = True
            return 0
        left = self.check_tries - st["seen"]
        st["seen"] += 1
        if left <= 0:
            st["out"] = True
        return max(0, left)

    def give_up_check(self) -> None:
        """The agent cannot do the human check on its tab: the next reading hands it to the user."""
        self._tries[browser_owner.get()] = {"site": self.current_host(), "seen": self.check_tries, "out": True}

    # ---- the agent's own try at a human check: a picture of the page, a click or keys on the check itself, nothing else
    async def check_picture(self) -> tuple[Challenge | None, bytes | None, int, int, list[dict[str, int]]]:
        """A picture of this device's page while a human check is on it: (check, JPEG, width, height, the check's boxes in picture
        pixels). (None, None, 0, 0, []) when there is no check now."""
        page = await self.page()
        key = browser_owner.get()
        found, probe = await self._read_check(page, key)
        if found is None:
            return None, None, 0, 0, []
        jpeg, w, h = await self.screenshot(key, CHECK_SHOT_WIDTH, CHECK_SHOT_BYTES)
        if not jpeg or not w:
            return found, None, 0, 0, []
        vw = await _viewport_width(page)
        scale = vw / w if vw else 1.0
        self._shot_scale[key] = scale
        boxes = [{k: round(v / scale) for k, v in b.items()} for b in check_boxes(probe)]
        return found, jpeg, w, h, boxes

    async def _check_target(self, page: Any, key: str) -> tuple[Challenge, Any]:
        await self._guard(page.url)
        found, probe = await self._read_check(page, key)
        if found is None:
            raise BrowserPolicyError("there is no human check on this page now")
        return found, probe

    async def click_in_check(self, x: float, y: float) -> str:
        """Click a point of the last check picture (its pixels). Only on the check itself: anywhere on a page that is all check, else
        inside one of the check's boxes. Returns the page reading afterwards (which counts as one try)."""
        page = await self.page()
        key = browser_owner.get()
        found, probe = await self._check_target(page, key)
        scale = self._shot_scale.get(key)
        if scale is None:
            raise BrowserPolicyError("look at the picture first (action look)")
        cx, cy = float(x) * scale, float(y) * scale
        if found.kind != "interstitial" and not any(b["x"] - 6 <= cx <= b["x"] + b["w"] + 6 and b["y"] - 6 <= cy <= b["y"] + b["h"] + 6
                                                     for b in check_boxes(probe)):
            raise BrowserPolicyError("that point is outside the human check; only the check itself can be clicked this way")
        try:
            await human.click_at(page, cx, cy)
        except Exception as e:  # noqa: BLE001
            raise BrowserPolicyError(f"could not click there ({type(e).__name__})") from None
        self._check_clicked[key] = True
        await asyncio.sleep(self.settle_seconds)
        await self._guard(page.url)
        await self._settle(page)
        return await self.snapshot()

    async def type_in_check(self, text: str) -> str:
        """Type a short answer into the part of the check that was just clicked. Returns the page reading afterwards (one try)."""
        page = await self.page()
        key = browser_owner.get()
        await self._check_target(page, key)
        if not self._check_clicked.get(key):
            raise BrowserPolicyError("click the check's answer box first (action click)")
        if not text or len(text) > 40 or human.has_control_chars(text):
            raise BrowserPolicyError("the text must be 1 to 40 plain characters")
        try:
            await human.type_keys(page, text)
        except Exception as e:  # noqa: BLE001
            raise BrowserPolicyError(f"could not type ({type(e).__name__})") from None
        await asyncio.sleep(self.settle_seconds)
        await self._guard(page.url)
        return await self.snapshot()

    # ---- browser_look: a picture of the visible page with a box and a ref on each control (the refs browser_click takes)
    async def look(self) -> tuple[bytes | None, int, int, int]:
        """(JPEG, width, height, how many controls carry a label). The boxes come from the accessibility tree with boxes (viewport
        CSS px; an element in a frame is moved by where that frame sits). The labels are drawn into the page only for the picture and
        always taken out again. Remembers the picture's scale and the page address for browser_click_at / browser_drag."""
        self._owner_moved.discard(browser_owner.get())   # the page is read now: what the owner changed by hand is seen (hold_to_act)
        self._lost_act.discard(browser_owner.get())
        page = await self.page()
        key = browser_owner.get()
        await self._guard(page.url)
        try:
            tree = await page.aria_snapshot(mode="ai", boxes=True, timeout=SNAPSHOT_MS)
        except Exception:  # noqa: BLE001 - a picture without labels
            tree = None
        boxes: list[dict[str, Any]] = []
        if isinstance(tree, str) and tree.strip():
            self._ref_words[key] = ref_words(tree)
            try:
                vw, vh = await page.evaluate("() => [innerWidth, innerHeight]")
            except Exception:  # noqa: BLE001
                vw, vh = 1e9, 1e9
            found, hosts = ref_boxes(tree), frame_hosts(tree)
            origins: dict[str, tuple[float, float] | None] = {"": (0.0, 0.0)}
            for ref in interactive_refs(tree):
                b = found.get(ref)
                if b is None or b[2] < 1 or b[3] < 1:
                    continue
                off = await self._frame_origin(page, frame_of(ref), hosts, origins)
                if off is None:
                    continue
                x, y = b[0] + off[0], b[1] + off[1]
                if x + b[2] <= 0 or y + b[3] <= 0 or x >= vw or y >= vh:
                    continue   # not in the visible part of the page
                boxes.append({"ref": ref, "x": round(x, 1), "y": round(y, 1), "w": round(b[2], 1), "h": round(b[3], 1)})
                if len(boxes) >= LOOK_MAX_LABELS:
                    break
        host = None
        try:
            if boxes:
                try:
                    host = await page.evaluate_handle(LOOK_OVERLAY_JS, boxes)
                except Exception:  # noqa: BLE001 - a page that refuses it: the picture without labels
                    boxes = []
            jpeg, w, h = await self.screenshot(key, LOOK_WIDTH, LOOK_BYTES)
        finally:
            if host is not None:
                await self._quietly(lambda: host.evaluate("(e) => e.remove()"))
                await self._quietly(host.dispose)
        if not jpeg or not w:
            return None, 0, 0, 0
        vw_css = await _viewport_width(page)
        self._shot_scale[key] = vw_css / w if vw_css else 1.0
        self._look_url[key] = str(page.url or "")
        return jpeg, w, h, len(boxes)

    async def _frame_origin(self, page: Any, frame: str, hosts: dict[str, str],
                            origins: dict[str, tuple[float, float] | None]) -> tuple[float, float] | None:
        """Where the viewport of frame `frame` ("f1") starts in the page's viewport (frames inside frames added up); None when it
        cannot be told."""
        if frame in origins:
            return origins[frame]
        origins[frame] = None   # (a loop in a broken reading ends here)
        iframe = hosts.get(frame)
        parent = await self._frame_origin(page, frame_of(iframe), hosts, origins) if iframe else None
        if parent is not None:
            try:
                dx, dy = await asyncio.wait_for(page.locator(f"aria-ref={iframe}").evaluate(FRAME_ORIGIN_JS, timeout=1_000), 1.5)
                origins[frame] = (parent[0] + float(dx), parent[1] + float(dy))
            except Exception:  # noqa: BLE001
                pass
        return origins[frame]

    async def wait_check_gone(self, key: str) -> bool:
        """Return True once the human check has left this tab's page (the user solved it, or it cleared by itself) and nobody holds the
        tab; False when the tab closed. Looks every `check_poll` seconds and right away when the tab is handed back; a reading that fails
        (the page is changing) never counts as gone, and "gone" must be seen twice in a row."""
        wake = self._wake.setdefault(key, asyncio.Event())
        clean = 0
        while True:
            page = self.raw_page(key)
            if page is None:
                return False
            found, ok = await self._probe(page, key)
            clean = clean + 1 if ok and found is None else 0
            if clean >= 2 and key not in self._control:
                return True
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), self.check_poll if clean == 0 else min(1.0, self.check_poll))
            except asyncio.TimeoutError:
                pass

    def _signing_in(self, key: str) -> bool:
        """The agent started a sign-in on this tab a short while ago (browser_login)."""
        got = self.sign_ins.get(key) or {}
        try:
            return time.time() - max(float(got.get("at") or 0), self._login_at.get(key, 0.0)) < SIGN_IN_SECONDS
        except (TypeError, ValueError):
            return False

    async def _verify(self, page: Any, key: str, v: Verification, handing: bool) -> tuple[str, bool]:
        """A sign-in verification on this tab's page: (the agent's note, whether it was completed while the step waited).
        A code the site may have mailed is looked for in the user's mailbox first (once per page). Then the user is asked AT ONCE
        through the app's hand-over (card and notification, kind "verification"), with no model turn before it (2026-11-10, 0.9.0+6:
        the card went out a minute late). When the page shows unticked options ("don't ask again on this browser"), the agent gets
        one step to choose them while the user opens their phone (options_note); the wait goes on at its next browser step
        (_pending_hand). Otherwise the step waits right away until the page is past signing in, the user's step is gone (progressed),
        the user gives up on it, or the hand-over's time runs out. Right after a hand-over of this site ended, a page of the same
        step kind is not handed over again before it settles (REHAND_SECONDS). Without an app turn, or when the same page was
        already handed over and not completed, the agent is told to tell the user plainly and stop."""
        if (pending := self._pending_hand.pop(key, None)) is not None:
            return await self._finish_hand(key, *pending)
        host = urlsplit(str(page.url or "")).hostname or ""
        url = str(page.url or "").split("#", 1)[0]
        site = registrable(host)
        seen = self._verified.get(key)
        if v.step == "code" and self.has_mailbox() and (seen is None or seen[0] != url):
            self._verified[key] = (url, "mail")
            return OTP_NOTE + " If there is none, the user enters it: read the page again and they will be asked.", False
        hand = handover.get() if handing else None
        if hand is None or (seen is not None and seen[0] == url and seen[1] not in ("mail", "solved", "done", "progressed")):
            return waiting_note(v, host), False
        last = self._hand_end.get(key)
        if last is not None and last[0] == site and last[1] == v.step and time.monotonic() - last[2] < REHAND_SECONDS:
            # the page may still be on its way after the user's approval: wait for it instead of asking again at once
            if await self._settles_away(page, key, v, last[2] + REHAND_SECONDS):
                return "", True
        await self.card(v.say, force=True)
        if v.step == "approve":
            self._approving[site] = (True, time.monotonic())
        started = time.monotonic()
        task = asyncio.ensure_future(hand({"tab": key, "site": host, "vendor": "verification", "name": v.say, "kind": "verification",
                                           "step": v.step}, lambda: self.wait_verification_gone(key, url, v)))
        await asyncio.sleep(0)   # the card and the notification go out now
        options = await self._unticked_options(page)
        if options and not task.done():
            self._pending_hand[key] = (task, v, url, host, site, started)
            self._verified[key] = (url, "asked")
            return options_note(v, host, "\n".join(f"- `{sel}`: {data(label)}" for sel, label in options)), False
        return await self._finish_hand(key, task, v, url, host, site, started)

    async def _finish_hand(self, key: str, task: Any, v: Verification, url: str, host: str, site: str,
                           started: float) -> tuple[str, bool]:
        """Wait for the verification hand-over `task` raised by _verify and tell the agent how it ended."""
        try:
            outcome = str(await task)
        except asyncio.CancelledError:
            outcome = "cancelled"
        finally:
            if v.step == "approve":
                self._approving[site] = (False, time.monotonic())
        page = self.raw_page(key)
        if outcome == "done" and page is not None:   # the user said it is done (an older app's button): believed only past signing in
            _found, probe = await self._read_check(page, key)
            if still_signing_in(probe, self._login_url.get(key, "")):
                outcome = "pending"
        if outcome in ("solved", "done", "progressed"):
            self._hand_end[key] = (site, v.step, time.monotonic())
        self._verified[key] = (url, outcome)
        self._handed[key] = outcome
        return (verification_note(outcome, v, host, round((time.monotonic() - started) / 60)),
                outcome in ("solved", "done", "progressed"))

    def approve_pending(self, site: str) -> str | None:
        """While the user is asked to approve a sign-in of `site` on their phone (or just did): why find_login_code has nothing to do."""
        got = self._approving.get(registrable(site) or site)
        if got is None or not (got[0] or time.monotonic() - got[1] < APPROVE_QUIET_SECONDS):
            return None
        return (f"{site} signs in by an approval on the user's phone or in its app: there is NO code, so do not search mail or SMS "
                "for one. " + ("The user is being asked to approve now; the step waits for them." if got[0] else
                               "Read the page (browser_read) to see where the sign-in stands."))

    async def _settles_away(self, page: Any, key: str, v: Verification, until: float) -> bool:
        """Right after a hand-over: True when, before `until` (monotonic), the page stops showing a verification step of kind v.step
        (it went on by itself); False when it still shows one then (a new request: it is handed over)."""
        while time.monotonic() < until:
            await asyncio.sleep(min(0.5, max(0.05, self.check_poll)))
            page = self.raw_page(key) or page
            try:
                probe = await page.evaluate(PROBE_JS)
            except Exception:  # noqa: BLE001 - the page is changing
                continue
            await self.judge_probe(probe, True)
            now = verification_step(probe, signing_in=True)
            if isinstance(probe, dict) and probe.get("kelo_check") and (now is None or now.step != v.step):
                return True
        return False

    async def _unticked_options(self, page: Any) -> list[tuple[str, str]]:
        """The visible, unticked checkboxes and switches of the page, by structure: (a selector for browser_click, the words next to it
        as the page shows them). At most 8; [] when the page cannot be read. The agent judges them by their words."""
        try:
            got = await page.evaluate(OPTIONS_JS)
        except Exception:  # noqa: BLE001 - a page that is changing: no options this time
            return []
        found = [(str(o["path"]), " ".join(str(o.get("label") or "").split())[:120]) for o in (got or [])[:8]
                 if isinstance(o, dict) and o.get("path")]
        self._options[browser_owner.get()] = {sel for sel, _ in found}
        return found

    async def sign_in_links(self, hosts: list[str]) -> list[str]:
        """The words of up to 3 visible links of the open page that go to one of `hosts` (a saved login's own hosts) on another host than
        the page's (SIGN_IN_LINKS_JS). [] on any error."""
        try:
            got = await (await self.page()).evaluate(SIGN_IN_LINKS_JS, [h.lower() for h in hosts if not h.startswith("app:")])
        except Exception:  # noqa: BLE001
            return []
        return [str(w) for w in (got or []) if isinstance(w, str) and w][:3]

    async def open_sign_in(self, via: str) -> str:
        """browser_login's own click on the site's sign-in link or button `via` (a ref or selector of the open page), made like any click
        of the agent (pacing, a window it opens becomes the tab's page); the click's reading. Only an element that just opens something
        (judged "" like a link): anything that may change something is refused, so the approval rules of browser_click stay whole.
        BrowserPolicyError when `via` is not on the page or is such a step: nothing is clicked."""
        page = await self.page()
        try:
            owner = await self.frame_for(page, via)
            n = await owner.locator(ref_selector(via)).count()
        except Exception:  # noqa: BLE001
            n = 0
        if n == 0:
            raise BrowserPolicyError(f"{via} is not on this page: read the page again and pass the ref of the site's sign-in link or "
                                     "button from that reading; nothing was clicked or typed")
        if kind := await self.judge("browser_click", {"selector": via}):
            raise BrowserPolicyError(f"{via} may change something ({kind}), so browser_login does not click it: click it with browser_click "
                                     "(it asks as usual), then call browser_login on the page with the form; nothing was typed")
        return await self.click(via)

    def wait_keep_choice(self, name: str) -> None:
        """browser_login listed the form's options for login `name`: its next call with `keep` is part of the same sign-in (S6)."""
        self._keep_choice[name] = time.monotonic()

    def keep_choice_open(self, name: str) -> bool:
        at = self._keep_choice.get(name)
        return at is not None and time.monotonic() - at < KEEP_CHOICE_SECONDS

    def end_keep_choice(self, name: str) -> None:
        self._keep_choice.pop(name, None)

    async def wait_verification_gone(self, key: str, url: str, step: Verification) -> bool | str:
        """How the sign-in verification `step`, handed to the user, ended on this tab (nobody holding the tab):
        True = signed in (signin_state.still_signing_in clear on CLEAN_READINGS readings in a row);
        "progressed" = no verification step of that kind is shown any more, and the page settled (no change for SETTLE_QUIET seconds,
        at most SETTLE_MAX after it left) on a sign-in page that still needs something ("stay signed in?", "continue"): control goes
        back to the agent, which reads it. A redirect or interstitial after an approval is waited out, so it ends signed in. Another
        page of the same step kind (a second "approve on your phone") is the same request: the wait goes on, no second card.
        False = the tab closed. A failed reading tells nothing."""
        wake = self._wake.setdefault(key, asyncio.Event())
        clean = away = 0
        left_at: float | None = None
        sig: Any = None
        quiet_since = 0.0
        while True:
            page = self.raw_page(key)
            if page is None:
                return False
            try:
                probe = await page.evaluate(PROBE_JS)
            except Exception:  # noqa: BLE001 - the page is changing: no reading this time
                probe = None
            now_t = time.monotonic()
            if not (isinstance(probe, dict) and probe.get("kelo_check")):
                clean = 0
            else:
                await self.judge_probe(probe, True)
                now = verification_step(probe, signing_in=True)
                same = now is not None and now.step == step.step
                signed = not same and not still_signing_in(probe, self._login_url.get(key, ""))
                clean = clean + 1 if signed else 0
                away = 0 if same else away + 1
                if same:
                    left_at, sig = None, None
                else:
                    left_at = left_at or now_t
                    mark = (str(probe.get("url") or ""), str(probe.get("title") or ""), int(probe.get("text_len") or 0))
                    if mark != sig:
                        sig, quiet_since = mark, now_t
                if key not in self._control:
                    if clean >= CLEAN_READINGS:
                        return True
                    settled = left_at is not None and (now_t - quiet_since >= SETTLE_QUIET or now_t - left_at >= SETTLE_MAX)
                    if away >= 2 and not signed and settled:
                        return "progressed"
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), self.check_poll if away == 0 else min(0.25, self.check_poll))
            except asyncio.TimeoutError:
                pass

    def note(self, key: str, text: str) -> None:
        """A line the agent gets with its next reading of that tab's page."""
        notes = self._notes.setdefault(key, [])
        notes.append(text)
        del notes[:-3]

    async def check(self) -> Challenge | None:
        """The bot check on this device's tab now, if any (nobody is told here: that happens when it is handed over)."""
        page = await self.page()
        return (await self._look(page, browser_owner.get()))[0]

    async def _probe(self, page: Any, key: str) -> tuple[Challenge | None, bool]:
        """Read the page's structure once and look for a bot check: (the check or None, whether the page could be read at all)."""
        found, probe = await self._read_check(page, key)
        return found, isinstance(probe, dict) and bool(probe.get("kelo_check"))

    async def _read_check(self, page: Any, key: str) -> tuple[Challenge | None, Any]:
        try:
            probe = await page.evaluate(PROBE_JS)
        except Exception:  # noqa: BLE001 - a page that is changing: no reading this time
            probe = None
        status, headers = self._doc.get(key, (None, {}))
        try:
            frame_urls = [str(f.url or "") for f in list(page.frames)[1:]] if hasattr(page, "frames") else []
        except Exception:  # noqa: BLE001
            frame_urls = []
        return detect(probe, status=status, headers=headers, frame_urls=frame_urls), probe

    def password_box_shown(self, key: str) -> bool | None:
        """Whether the page of tab `key`, as last read, shows a password box (the structure of a sign-in form); None when it was not read."""
        probe = self._probes.get(key)
        if not isinstance(probe, dict) or not probe.get("kelo_check"):
            return None
        return bool(probe.get("secret_box"))

    async def judge_probe(self, probe: Any, signing_in: bool) -> None:
        """A short page right after a sign-in with no code box: the page judge says whether it is a verification step (code | approve) or
        none (probe["judged_step"]). No judge or no answer leaves it unset: verification_step then takes the page for one (the safer side)."""
        if self.page_judge is None or not unconfirmed(probe, signing_in=signing_in):
            return
        got = await self.page_judge.verification(probe)
        if got is not None:
            probe["judged_step"] = got

    async def _look(self, page: Any, key: str) -> tuple[Challenge | None, int]:
        """Read the page's structure once: a bot check (challenge.detect) and how many one-time-code boxes it shows."""
        found, probe = await self._read_check(page, key)
        await self.judge_probe(probe, self._signing_in(key))
        self._probes[key] = probe
        if found is None:
            self._checks.pop(key, None)
        otp = probe.get("otp") if isinstance(probe, dict) else 0
        return found, int(otp) if isinstance(otp, int) else 0

    def _announce(self, key: str, found: Challenge, host: str) -> None:
        last = self._checks.get(key)
        now = time.monotonic()
        if last and last[0] == found.vendor and now - last[1] < CHECK_NOTICE_SECONDS:
            return
        self._checks[key] = (found.vendor, now)
        for fn in list(self.check_listeners):
            try:
                out = fn(key, found, host)
                if asyncio.iscoroutine(out):
                    asyncio.ensure_future(out)
            except Exception:  # noqa: BLE001 - telling the user never breaks reading the page
                pass

    # ------------------------------------------------------------------ a watcher's reads (watchers.py)
    async def chrome_version(self) -> str:
        """The running browser's version ("130.0.6723.58"), or "" when it cannot be read (no browser, a test fake)."""
        try:
            if not self._connect:
                await self._ensure_browser()
            v = str(getattr(self._browser, "version", "") or "")
        except Exception:  # noqa: BLE001
            return ""
        return v if v[:1].isdigit() else ""

    async def page_state(self) -> tuple[int | None, dict[str, str], Challenge | None]:
        """The current tab's main document status, the response headers that matter for bot checks, and the bot check on it, if any
        (the reading the browser already does; nobody is told here)."""
        key = browser_owner.get()
        page = self._pages.get(key)
        if page is None:
            return None, {}, None
        found, _probe = await self._read_check(page, key)
        status, headers = self._doc.get(key, (None, {}))
        return status, dict(headers), found

    async def close_idle_site_tabs(self, keep: str = "") -> list[str]:
        """Closes the watchers' site tabs unused for SITE_TAB_IDLE_SECONDS (and, with `keep`, the least recently used ones so that
        `keep` fits within SITE_TABS_MAX)."""
        closed = [k for k in self.open_keys() if is_site_tab(k) and k != keep and self.idle_for(k) >= SITE_TAB_IDLE_SECONDS]
        live = [k for k in self.open_keys() if is_site_tab(k) and k not in closed and k != keep]
        room = SITE_TABS_MAX - (1 if keep else 0)
        if len(live) > room:
            closed += sorted(live, key=self.idle_for, reverse=True)[:len(live) - room]
        for k in closed:
            await self.close_tab(k)
        return closed

    async def site_fetch(self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: str | None = None,
                         key: str = "", cap: int = 2_000_000) -> dict[str, Any]:
        """A request made INSIDE the agent's browser, from a background tab of the site (one per registrable domain, opened once at the
        site's root, or at `url` for a GET): `fetch(url, {credentials: "include"})` there, so the owner's sign-ins ride along and the
        request leaves through the browser's own route (egress / VPN). Cookies are never read out. Same origin only: the tab sits on the
        url's origin, and an answer that came from another address is refused. Returns {"status", "content_type", "text", "headers", "url"}."""
        await self._guard(url)
        key = key or site_tab_key(url)
        origin = _origin_of(url)
        async with self._site_locks.setdefault(key, asyncio.Lock()):
            await self.close_idle_site_tabs(keep=key)
            token = browser_owner.set(key)
            try:
                page = await self.page()
                if _origin_of(str(getattr(page, "url", "") or "")) != origin:
                    try:
                        await page.goto(url if method == "GET" else origin + "/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    except Exception as e:
                        if (note := await self._vpn_down(e)):
                            raise BrowserPolicyError(note) from None
                        raise
                    try:
                        await self._guard(str(page.url))
                    except BrowserPolicyError:
                        await page.goto("about:blank")
                        raise
                    if _origin_of(str(page.url)) != origin:
                        raise BrowserPolicyError(f"the site sent the tab to another address ({urlsplit(str(page.url)).hostname}); "
                                                 "a signed-in check stays on one site")
                try:
                    got = await page.evaluate(SITE_FETCH_JS, {"url": url, "method": method, "headers": dict(headers or {}), "body": body,
                                                              "cap": cap, "timeout": SITE_FETCH_TIMEOUT_MS})
                except Exception as e:
                    if (note := await self._vpn_down(e)):
                        raise BrowserPolicyError(note) from None
                    raise BrowserPolicyError(f"the request inside the browser failed ({type(e).__name__})") from None
                self._used[key] = self.clock()
            finally:
                browser_owner.reset(token)
        if not isinstance(got, dict) or not isinstance(got.get("status"), int):
            raise BrowserPolicyError("the browser gave no answer to the request")
        final = str(got.get("url") or url)
        if _origin_of(final) != origin:
            raise BrowserPolicyError(f"the answer came from another address ({urlsplit(final).hostname}); a signed-in check stays on one site")
        return {"status": int(got["status"]), "content_type": str(got.get("contentType") or ""), "text": str(got.get("text") or "")[:cap],
                "headers": {str(k).lower(): str(v) for k, v in dict(got.get("headers") or {}).items()}, "url": final}

    async def open(self, url: str) -> str:
        await self._guard(url)
        if said := self.blocked_site(url):
            return said
        page = await self.page()
        if same_address(str(getattr(page, "url", "") or ""), url):
            title = ""
            try:
                title = str(await page.title())[:120]
            except Exception:  # noqa: BLE001 - the page has no title to give
                pass
            return SAME_PAGE_NOTE.format(url=str(page.url), title=title) + "\n" + await self.snapshot(clear=True)
        host = urlsplit(url).hostname or ""
        if note := self._pace_note(host, "nav", browser_owner.get()):
            return note
        await self._pace_wait(host, "nav", browser_owner.get())
        # opening an address starts a new flow: the clicks that finish an allowed purchase are those that follow from its page
        self.end_purchase()
        site = _site_of(url)
        await self._seed_work_site(browser_owner.get(), site)
        if self.work is None or not work_tab(browser_owner.get()):
            self._touch_site(registrable(site))
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            if (note := await self._vpn_down(e)):
                raise BrowserPolicyError(note) from None
            raise
        status = getattr(resp, "status", None)
        self._note_opened(browser_owner.get(), url)
        try:  # a redirect may have landed somewhere we would not have allowed directly
            await self._guard(page.url)
        except BrowserPolicyError:
            await page.goto("about:blank")
            raise
        await self._settle(page)
        await self._human_pause(page, self._look_pause(glance=False))
        self._own_layers.pop(browser_owner.get(), None)   # opened (again): its windows are the site's, not the agent's
        text = await self.snapshot(clear=True)
        return f"{not_found_note(status)}\n{text}" if status in (404, 410) else text

    async def shoot(self, page: Any, *, type: str = "jpeg", quality: int | None = None,
                    clip: dict[str, float] | None = None, css_scale: bool = False, timeout: int = 5_000) -> bytes:
        """A picture of the tab's viewport (or `clip` of it)."""
        kw: dict[str, Any] = {"type": type, "timeout": timeout}
        if quality is not None and type == "jpeg":
            kw["quality"] = quality
        if clip is not None:
            kw["clip"] = clip
        if css_scale:
            kw["scale"] = "css"
        return await page.screenshot(**kw)

    @staticmethod
    async def _settle(page: Any) -> None:
        """Give a page drawn by its scripts up to SETTLE_MS to finish loading, so it is not read while still empty."""
        try:
            await page.wait_for_load_state("load", timeout=SETTLE_MS)
        except Exception:  # noqa: BLE001 - a page that never finishes (ads, long polls) is read as it is
            pass

    async def frame_for(self, page: Any, selector: str) -> Any:
        """Where `selector` is: the page itself, else the first frame inside it that has it (an embedded form or checkout, also
        from another host), else the page (whose error then says what is wrong)."""
        if (ref := ref_of(selector)) is not None:
            # a ref resolves from the page, frames included (f1e3): no search; one the page no longer has is said so
            try:
                n = await page.locator(ref_selector(selector)).count()
            except Exception:  # noqa: BLE001 - "Invalid frame": the frame of that ref is gone
                n = 0
            if not n:
                raise BrowserPolicyError(STALE_REF.format(ref=ref))
            return page
        if not selector or not hasattr(page, "frames"):
            return page
        try:
            if await page.locator(selector).count():
                return page
        except Exception:  # noqa: BLE001 - a selector Playwright cannot use: the click says so
            return page
        try:
            frames = [f for f in page.frames if f is not page.main_frame]
        except Exception:  # noqa: BLE001
            return page
        for f in frames:
            try:
                if await f.locator(selector).count():
                    return f
            except Exception:  # noqa: BLE001 - a frame that went away
                continue
        return page

    @staticmethod
    async def _layer_labels(page: Any) -> list[str]:
        """The windows open over the page now (page_notes.LAYERS_JS); [] when they cannot be read."""
        try:
            got = await asyncio.wait_for(page.evaluate(LAYERS_JS), 2.0)
        except Exception:  # noqa: BLE001 - a page that is changing: no window is claimed
            return []
        return [str(x) for x in got] if isinstance(got, list) else []

    async def _offered_elsewhere(self, owner: Any, selector: str, key: str) -> tuple[str, bool] | None:
        """Before a click: when the element sits in the window this tab's earlier click opened, (that window's label, whether another
        control of it commits something by the step judge). None otherwise, or when it cannot be read. A judge that cannot answer
        counts as no commit (the earlier click then stays done)."""
        opened, page = self._opened_layer.get(key), self._pages.get(key)
        if opened is None or page is None or str(getattr(page, "url", "") or "").split("#")[0] != opened[0]:
            return None
        try:
            got = await asyncio.wait_for(owner.locator(selector).first.evaluate(LAYER_CHOICES_JS), 2.0)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(got, dict) or str(got.get("label") or "") != opened[1]:
            return None
        for path in [str(p) for p in (got.get("paths") or []) if p][:4]:
            info = await self.read_target(path)
            if info is None or click_route(info) == "" or self.step_judge is None:
                continue
            try:
                verdict = await self.step_judge.web(info)
            except Exception:  # noqa: BLE001
                verdict = None
            if kind_of_verdict(verdict, info) not in ("", "unknown"):
                return opened[1], True
        return opened[1], False

    @staticmethod
    async def _fingerprint(page: Any) -> str:
        """The address, markup and form values of the page and its frames (FINGERPRINT_JS): equal before and after a click = the
        click changed nothing."""
        parts: list[str] = []
        try:
            frames = list(page.frames) if hasattr(page, "frames") else []
        except Exception:  # noqa: BLE001
            frames = []
        for f in frames or [page]:
            try:
                parts.append(str(await asyncio.wait_for(f.evaluate(FINGERPRINT_JS), 2.0)))
            except Exception:  # noqa: BLE001 - a frame that is changing or gone: that is a change
                parts.append(f"?{id(f)}")
        return "\n".join(parts)

    def last_click_changed_nothing(self, key: str | None = None) -> bool:
        return self._unchanged.get(key or browser_owner.get(), False)

    def _tab_dialogs(self, key: str) -> TabDialogs:
        return self._dialogs.setdefault(key, TabDialogs())

    def page_dialog(self, key: str) -> dict[str, Any] | None:
        """The live status's `dialog` for a tab (server.py LIVE VIEW API): its newest page pop-up while the tab still shows the address
        it came from (page_dialogs.SHOW_SECONDS at most), else None. A native pop-up is never in the picture or on the X screen, so
        without this the owner watches a page that seems to do nothing (owner report 2026-11-19)."""
        tab, page = self._dialogs.get(key), self._pages.get(key)
        if tab is None or page is None:
            return None
        d = tab.shown(str(getattr(page, "url", "") or ""), time.time())
        return live_view(d) if d is not None else None

    async def click(self, selector: str) -> str:
        page = await self.page()
        key = browser_owner.get()
        before = str(getattr(page, "url", "") or "")
        if said := self.blocked_site(before):   # a click on the page of a site that already blocked us twice: nothing is sent to it
            return said
        self._unchanged.pop(key, None)
        pressed = self.judged(selector)   # the kind the gate judged for THIS click ("" = commits nothing)
        declines = self._judged.get(key, ("",))[0] == selector and pressed == ""
        info = self.judged_info(selector) or {}
        owner = await self.frame_for(page, selector)
        selector = ref_selector(selector)
        if layer := await self._covered(page, owner, selector, key):
            return COVERED_NOTE.format(layer=data(layer))
        host = urlsplit(before).hostname or ""
        paced = ""   # what this click is to the site: a form submit / search, a link to another address, or neither
        href = str(info.get("href") or "").strip()
        if info.get("submits") and (info.get("search_form") or self._typed.get(key)):
            paced = "submit"
        elif info.get("tag") == "a" and href and not href.startswith(("#", "javascript:")) and info.get("link_target") != "_blank":
            paced = "nav"
        if paced:
            if note := self._pace_note(host, paced, key):
                return note
            await self._pace_wait(host, paced, key)
        mark = await self._fingerprint(page)
        layers = await self._layer_labels(page)
        offered = await self._offered_elsewhere(owner, selector, key) if declines else None
        pops = self._tab_dialogs(key)
        dialogs = pops.mark()
        await human.click(owner, selector, timeout_ms=10_000, fallback=lambda ms: owner.click(selector, timeout=ms))
        await self._loaded(page, key)
        moved = await self._moved_to_window(key, page)
        if moved is not None:   # the click opened a window: the agent goes on in it
            page = moved
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - read it as it is
                pass
        await self._guard(page.url)
        if not paced and str(getattr(page, "url", "") or "") != before:   # went to another address: the site saw a request
            self.pacer.record(host, "nav", human.pace_factor())
        await self._settle(page)
        await self._human_pause(page, self._look_pause(glance=str(getattr(page, "url", "") or "") == before))
        after = await self._layer_labels(page)
        same = str(getattr(page, "url", "") or "") == before
        if moved is not None:
            self._notes.setdefault(key, []).insert(0, POPUP_NOTE.format(url=str(page.url or "")[:200]))
        elif (cancelled := pops.only_cancelled(dialogs)) is not False and await self._fingerprint(page) == mark:
            # nothing changed, or the only pop-ups were questions answered Cancel: the same click again does the same (2026-11-19)
            self._unchanged[key] = True
            self._notes.setdefault(key, []).insert(0, DIALOG_ONLY_NOTE if cancelled else NO_CHANGE_NOTE)
        elif same and (new := [x for x in after if x not in layers]):
            self._notes.setdefault(key, []).insert(0, OPENED_NOTE)   # a window of the page itself opened: maybe a result, maybe a step
            self._opened_layer[key] = (before.split("#")[0], new[0])
        elif same and offered is not None and offered[1] and offered[0] not in after:
            self._notes.setdefault(key, []).insert(0, DECLINED_NOTE)   # the job closed it without the step it offered
        if (o := self._opened_layer.get(key)) is not None and (not same or o[1] not in after):
            self._opened_layer.pop(key, None)   # that window is gone (closed any way): nothing more to decline
        return await self.snapshot(changes=True)   # a window the click opened on the same page is the agent's own (snapshot)

    # ------------------------------------------------------------------ pacing per site (pacing.py)
    def _pace_note(self, host: str, kind: str, key: str) -> str | None:
        """None when a `kind` request to `host` may go now or after a wait the step has time for; else the words that tell the model
        to wait (nothing is sent)."""
        need = self.pacer.need(host, kind)
        if need <= 0:
            return None
        clock = self._step_clock.get(key)
        if clock is not None and need + PACE_MARGIN_SECONDS > clock["limit"] - clock["spent"]:
            return PACE_NOTE.format(host=host or "this site", seconds=max(1, round(need + 0.5)))
        return None

    async def _pace_wait(self, host: str, kind: str, key: str) -> None:
        """Wait until a `kind` request to `host` is allowed, then record it as going out now."""
        need = self.pacer.need(host, kind)
        if need > 0:
            await self.sleep(need)
        self.pacer.record(host, kind, human.pace_factor())
        self._typed.pop(key, None)

    async def _human_pause(self, page: Any, pause: Callable[[Any], Awaitable[Any]]) -> None:
        """The short human-like pause after a step (it reads the page's size with a script). A page that does not answer scripts (a
        navigation of the step that never completes) ends it after HUMAN_PAUSE_SECONDS: the navigation is stopped (rescue) so the page
        can be read, instead of the step hanging on a pause."""
        try:
            await asyncio.wait_for(pause(page), HUMAN_PAUSE_SECONDS)
        except asyncio.TimeoutError:
            await self.rescue()
            self._notes.setdefault(browser_owner.get(), []).insert(0, STILL_LOADING_NOTE.format(seconds=round(HUMAN_PAUSE_SECONDS)))

    async def _loaded(self, page: Any, key: str) -> None:
        """After a click: wait for the page it navigates to (CLICK_LOAD_MS). A navigation that does not answer is not a failed click:
        it is stopped (rescue) and the page is read as it is, with a note."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=CLICK_LOAD_MS)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ != "TimeoutError":
                raise
            await self.rescue(key)
            self._notes.setdefault(key, []).insert(0, STILL_LOADING_NOTE.format(seconds=round(CLICK_LOAD_MS / 1000)))

    @staticmethod
    async def _lists_note(page: Any) -> str:
        """The page's lists with what each shows now and its options (browser_type with the option's words chooses one)."""
        try:
            got = await page.evaluate(LISTS_JS)
        except Exception:  # noqa: BLE001
            return ""
        lines = [f"- {g['sel']}{' (' + data(g['label']) + ')' if g.get('label') else ''}: now {data(g.get('now') or '')}; options: "
                 f"{data(' | '.join(g.get('options') or []))}" for g in (got or []) if isinstance(g, dict) and g.get("sel")]
        return ("[Lists on the page (browser_type with an option's words chooses it):\n" + "\n".join(lines) + "]\n") if lines else ""

    async def _choose(self, page: Any, owner: Any, selector: str, want: str) -> str | None:
        """browser_type on a list (2026-11-10: a quantity list was never set; clicks open a native list headless clicks cannot pick
        from): a <select> gets the option whose words or value match `want` (select_option); a custom list (a button or div that opens
        options) is opened with a click and the matching option clicked. Returns the note, or None when the target is a text field
        (typed as usual). Raises BrowserPolicyError when no option matches (naming the options there)."""
        try:
            loc = owner.locator(selector).first
            kind = await loc.evaluate(KIND_JS, timeout=5_000)
        except Exception:  # noqa: BLE001 - not readable: typed as usual
            return None
        if not isinstance(kind, dict) or kind.get("text_field"):
            return None
        w = " ".join(want.split()).lower()
        if kind.get("tag") == "select":
            opts = [(str(o.get("value") or ""), " ".join(str(o.get("label") or "").split())) for o in kind.get("options") or []]
            pick = (next((o for o in opts if o[1].lower() == w), None) or next((o for o in opts if o[0].lower() == w), None)
                    or next((o for o in opts if w and w in o[1].lower()), None))
            if pick is None:
                raise BrowserPolicyError(f"no option {want!r} in that list; its options: {', '.join(o[1] for o in opts[:30])}")
            await loc.select_option(value=pick[0], timeout=5_000)
            shown = await loc.evaluate("(e) => (e.selectedOptions[0] || {}).text || e.value", timeout=5_000)
            return f"[Chose “{pick[1]}” in the list; it now shows “{' '.join(str(shown).split())}”.]"
        # a custom list: open it, then the option with those words
        await human.click(owner, selector, timeout_ms=10_000, fallback=lambda ms: loc.click(timeout=ms))
        await asyncio.sleep(0.3)
        for cand in (owner.get_by_role("option", name=want, exact=True), owner.get_by_role("menuitem", name=want, exact=True),
                     owner.get_by_text(want, exact=True)):
            try:
                n = await cand.count()
            except Exception:  # noqa: BLE001
                continue
            for i in range(n - 1, -1, -1):
                item = cand.nth(i)
                try:
                    if await item.is_visible() and not await item.evaluate("(e, t) => e === t || e.contains(t)", await loc.element_handle()):
                        await item.click(timeout=5_000)
                        await asyncio.sleep(0.2)
                        now = " ".join(str(await loc.inner_text(timeout=3_000)).split())[:80]
                        return f"[Opened the list and chose “{want}”; it now shows “{now}”.]"
                except Exception:  # noqa: BLE001 - try the next one
                    continue
        raise BrowserPolicyError(f"the list opened, but no option {want!r} was found in it; read the page to see its options")

    async def _moved_to_window(self, key: str, page: Any) -> Any:
        """The window a click opened, once the tab moved into it and it is set up (POPUP_WAIT_SECONDS at most for it to show up);
        None when the click stayed on the page."""
        loop = asyncio.get_running_loop()
        until = loop.time() + POPUP_WAIT_SECONDS
        while self._pages.get(key) is page and loop.time() < until:
            await asyncio.sleep(0.05)
        now = self._pages.get(key)
        if now is page or now is None:
            return None
        setup = self._popup_setup.pop(key, None)
        if setup is not None:
            try:
                await asyncio.wait_for(asyncio.shield(setup), NAV_TIMEOUT_MS / 1000)
            except Exception:  # noqa: BLE001 - it is read as it is
                pass
        return self._pages.get(key)

    async def click_at(self, x: float, y: float) -> str:
        """browser_click_at: click a point of the last browser_look picture (its pixels). Returns the page after, like click()."""
        return await self._at_points([(x, y)])

    async def drag(self, path: list[tuple[float, float]]) -> str:
        """browser_drag: press at the first point of the last browser_look picture, move through the others, let go there."""
        return await self._at_points(path)

    async def _at_points(self, pts: list[tuple[float, float]]) -> str:
        page = await self.page()
        key = browser_owner.get()
        await self._guard(page.url)
        where = [self.page_point(x, y) for x, y in pts]
        before = str(getattr(page, "url", "") or "")
        self._unchanged.pop(key, None)
        mark = await self._fingerprint(page)
        layers = await self._layer_labels(page)
        pops = self._tab_dialogs(key)
        dialogs = pops.mark()
        try:
            if len(where) == 1:
                await human.click_at(page, *where[0])
            else:
                await human.drag(page, where)
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001
            raise BrowserPolicyError(f"could not {'click' if len(where) == 1 else 'drag'} there ({type(e).__name__})") from None
        moved = await self._moved_to_window(key, page)
        if moved is not None:
            page = moved
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception:  # noqa: BLE001
                pass
        await self._guard(page.url)
        await self._settle(page)
        await self._human_pause(page, self._look_pause(glance=str(getattr(page, "url", "") or "") == before))
        after = await self._layer_labels(page)
        same = str(getattr(page, "url", "") or "") == before
        if moved is not None:
            self._notes.setdefault(key, []).insert(0, POPUP_NOTE.format(url=str(page.url or "")[:200]))
        elif (cancelled := pops.only_cancelled(dialogs)) is not False and await self._fingerprint(page) == mark:
            # nothing changed, or the only pop-ups were questions answered Cancel: the same click again does the same (2026-11-19)
            self._unchanged[key] = True
            self._notes.setdefault(key, []).insert(0, DIALOG_ONLY_NOTE if cancelled else NO_CHANGE_NOTE)
        elif same and (new := [x for x in after if x not in layers]):
            self._notes.setdefault(key, []).insert(0, OPENED_NOTE)   # a point click never declines a pop-up: fail closed
            self._opened_layer[key] = (before.split("#")[0], new[0])
        if (o := self._opened_layer.get(key)) is not None and (not same or o[1] not in after):
            self._opened_layer.pop(key, None)
        return await self.snapshot(changes=True)

    async def _ime_sender(self, page: Any) -> Callable[[str, dict[str, Any]], Awaitable[Any]] | None:
        """send(method, params) on the tab's DevTools session, each call bounded (cdp_send), for typing Korean through the input-method
        path (humanize.type_text). The tab's own session when it has one; else a session kept per tab. None when there is none
        to be had (the text is then typed as before)."""
        key = browser_owner.get()
        cdp = self._tab_cdp.get(key)
        if cdp is None:
            held = self._type_cdp.get(key)
            if held is not None and held[0] is page:
                cdp = held[1]
            else:
                try:
                    cdp = await asyncio.wait_for(self.cdp_session(page), CDP_SECONDS)
                except Exception:  # noqa: BLE001
                    return None
                self._type_cdp[key] = (page, cdp)
        return lambda method, params: cdp_send(cdp, method, params)

    async def type(self, selector: str, text: str, submit: bool = False) -> str:
        page = await self.page()
        if said := self.pause_note(str(getattr(page, "url", "") or "")):   # a site paused after a refusal: nothing is typed into it
            return said
        owner = await self.frame_for(page, selector)
        selector = ref_selector(selector)
        if layer := await self._covered(page, owner, selector, browser_owner.get()):
            return COVERED_NOTE.format(layer=data(layer))
        chose = await self._choose(page, owner, selector, text)
        if chose is not None:   # a list: the option was chosen (no typing)
            self._notes.setdefault(browser_owner.get(), []).insert(0, chose)
            return await self.snapshot(changes=True)
        key = browser_owner.get()
        host = urlsplit(str(getattr(page, "url", "") or "")).hostname or ""
        if submit and (note := self._pace_note(host, "submit", key)):   # nothing is typed that could not be sent
            return note
        await human.type_text(owner, text, selector=selector, timeout_ms=10_000, fallback=lambda ms: owner.fill(selector, text, timeout=ms),
                              cdp=await self._ime_sender(page))
        self._typed[key] = True
        if submit:
            before = str(getattr(page, "url", "") or "")
            await human.pause_before_submit()
            await self._pace_wait(host, "submit", key)
            await owner.press(selector, "Enter")
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await self._guard(page.url)
            if str(getattr(page, "url", "") or "") != before:
                await self._human_pause(page, self._look_pause(glance=False))
        return await self.snapshot(changes=True)

    settle_seconds = 0.8     # how long a page gets to show its answer after Enter (a sign-in error often appears without a page load)
    sign_in_wait = 12.0      # how long a sign-in is watched for a clear answer (a site that redirects through a few pages takes seconds)

    async def _password_field_ok(self, page: Any, origin_ok: Callable[[str], bool] | None, path: str, *, after: int | None = None) -> None:
        """The checks at the moment of typing a password (docs/data-trail.md, browser login checks): the page is https and on the credential's
        site, the tagged field is ONE input of type password, still the type it had when tagged, rendered masked; `after`: the length the
        field must hold now (the secret's own length; only lengths are compared, the value is never read). Raises BrowserPolicyError."""
        parts = urlsplit(str(getattr(page, "url", "") or ""))
        if parts.scheme != "https" or (origin_ok is not None and not origin_ok(page.url)):
            raise BrowserPolicyError("the page is not the credential's own site over https; nothing was typed")
        st = await page.evaluate(_LOGIN_STATE, path)
        if not isinstance(st, dict) or st.get("n") != 1:
            raise BrowserPolicyError("ambiguous target: the password field to type into could not be told from the others; nothing was typed")
        if not st.get("input") or st.get("type") != "password":
            raise BrowserPolicyError("the target is not a password field or is not masked (its type is not password, or changed after it was "
                                     "found); nothing was typed")
        if not st.get("masked"):
            raise BrowserPolicyError("the password field is not masked (it would show what is typed); nothing was typed")
        if after is not None and int(st.get("length") or 0) != after:
            raise BrowserPolicyError("the password was not applied: the field's value length does not match; the field was cleared and the "
                                     "sign-in was not sent. Do not retry; tell the user")

    async def fill_login(self, username: str, password: str, *, step: str = "both", submit: bool = True,
                         origin_ok: Callable[[str], bool] | None = None, ask_options: bool = False,
                         keep: str | None = None) -> SignIn:
        """Type a saved login into the visible sign-in form of the current page, and (optionally) press Enter.
        The values go straight from here to the page: the answer is the page afterwards with both values removed from it,
        and a reading of whether the site accepted the login. The password goes only into one masked input of type password on a
        https page of the credential's site (`origin_ok(url)`), and its length in the field must equal the password's, else
        nothing is sent, the field is cleared and a precise error says why (no retry).
        `ask_options` (browser_login's submit call without a choice yet): a form with unticked boxes or switches raises LoginOptions
        before anything is typed. `keep`: the listed option to tick before typing ("none": tick nothing)."""
        page = await self.page()
        await self._guard(page.url)
        before = page.url
        self._login_url[browser_owner.get()] = before
        self._login_at[browser_owner.get()] = time.time()   # a sign-in the agent started on this tab: a short page after it may be a verification step
        found = await page.evaluate(_TAG_LOGIN_FIELDS)
        want_user, want_pass = step in ("both", "username"), step in ("both", "password")
        if not ((want_user and found["u"]) or (want_pass and found["p"])):
            raise NoSignInForm("no sign-in form with the expected field was found on this page")
        if ask_options and keep is None and (options := await self._unticked_options(page)):
            raise LoginOptions(options)   # nothing typed: the agent chooses the stay-signed-in option by its words first (S6)
        if keep and keep != "none":
            if keep not in self._options.get(browser_owner.get(), set()):
                raise BrowserPolicyError("keep must be one of the option selectors browser_login listed for this page; nothing was typed")
            try:
                await human.click(page, keep, timeout_ms=10_000, fallback=lambda ms: page.locator(keep).click(timeout=ms))
            except Exception as e:  # noqa: BLE001 - a driver message never reaches the model as it is
                raise BrowserPolicyError(f"could not tick the chosen option ({type(e).__name__}); nothing was typed") from None
        try:
            if want_pass and found["p"]:
                if int(found.get("p_count", 1)) > 1:
                    raise BrowserPolicyError("ambiguous target: the page has more than one password field; nothing was typed")
                await self._password_field_ok(page, origin_ok, found["p_path"])   # before anything is typed
            if want_user and found["u"]:
                user_box = page.locator(found["u_path"])
                await human.type_text(page, username, locator=user_box, secret=True, timeout_ms=10_000,
                                      fallback=lambda ms: user_box.fill(username, timeout=ms))
            if want_pass and found["p"]:
                pass_box = page.locator(found["p_path"])
                await pass_box.focus(timeout=10_000)   # a page that unmasks a field when it is focused shows here, before the typing
                await self._password_field_ok(page, origin_ok, found["p_path"])
                await human.type_text(page, password, locator=pass_box, secret=True, timeout_ms=10_000,
                                      fallback=lambda ms: pass_box.fill(password, timeout=ms))
                try:
                    await self._password_field_ok(page, origin_ok, found["p_path"], after=len(password.encode("utf-16-le")) // 2)
                except BrowserPolicyError:
                    try:
                        await pass_box.fill("", timeout=5_000)
                    except Exception:  # noqa: BLE001 - the page may be gone; the error below is what matters
                        pass
                    raise
            if submit:
                target = found["p_path"] if (want_pass and found["p"]) else found["u_path"]
                await human.pause_before_submit()
                await page.locator(target).press("Enter", timeout=10_000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
                except Exception:  # noqa: BLE001 - a page that signs in without navigating is fine
                    pass
        except BrowserPolicyError:
            raise
        except Exception as e:  # noqa: BLE001 - a driver message could quote what was typed: say only what kind of failure it was
            raise BrowserPolicyError(f"could not fill in the form ({type(e).__name__})") from None
        try:
            await self._guard(page.url)
        except BrowserPolicyError:
            await page.goto("about:blank")
            raise
        outcome, detail, step = "unknown", "no sign-in was submitted", ""
        if submit and want_pass and found["p"]:
            await asyncio.sleep(self.settle_seconds)
            waited, state = self.settle_seconds, {"pw": True, "text": ""}
            while True:   # looked at again until the page answers clearly (signed in, rejected, a code) or the wait is over
                try:
                    state = await page.evaluate(_PAGE_STATE)
                except Exception:  # noqa: BLE001 - the page was still changing (a redirect): look again
                    state = {"pw": True, "text": ""}
                outcome, _ = classify_sign_in(before_url=before, after_url=page.url, password_visible=bool(state["pw"]),
                                              otp_boxes=int(state.get("otp") or 0), invalid_marked=bool(state.get("invalid")))
                if outcome != "unknown" or waited >= self.sign_in_wait:
                    break
                await asyncio.sleep(0.5)
                waited += 0.5
            found, _ = await self._look(page, browser_owner.get())
            outcome, detail = classify_sign_in(before_url=before, after_url=page.url, password_visible=bool(state["pw"]),
                                               otp_boxes=int(state.get("otp") or 0), invalid_marked=bool(state.get("invalid")),
                                               challenge=found.name if found is not None else "")
            ver = verification_step(self._probes.get(browser_owner.get()), signing_in=True) if found is None else None
            step = ver.step if ver is not None else ("code" if outcome == "code_needed" else "")
            if ver is not None:   # by structure and words (verification.py): a code to type, or a sign-in to approve elsewhere
                outcome, detail = "code_needed", ("the site accepted the password and waits for the user to approve the sign-in on their phone"
                                                  if ver.step == "approve" else "the site accepted the password and asks for a verification code")
        self._handed.pop(browser_owner.get(), None)
        text = await self.snapshot()
        if outcome == "blocked" and self._handed.get(browser_owner.get()) in ("solved", "done"):
            outcome, detail = "unknown", "the user solved the human check; the page below shows where the sign-in stands"
        # signed in only on evidence (signin_state.py): the page as read last (after any hand-over) is past every sign-in step
        probe = self._probes.get(browser_owner.get())
        handed = self._handed.get(browser_owner.get())
        if outcome == "code_needed" and handed in ("solved", "done", "progressed"):
            outcome, detail = (("success", "the user completed the sign-in verification and the page is past signing in")
                               if not still_signing_in(probe, self._login_url.get(browser_owner.get(), "")) else
                               ("unknown", "the user did the verification step, but the sign-in is NOT finished: the page below shows the "
                                           "next step") if handed == "progressed" else
                               ("code_needed", "the sign-in verification is still pending: the page still shows a sign-in or verification step"))
        elif outcome == "success" and isinstance(probe, dict) and probe.get("kelo_check") and still_signing_in(probe, self._login_url.get(browser_owner.get(), "")):
            outcome, detail = "unknown", "the sign-in form was left, but the page still shows a sign-in or verification step"
        for secret in (password, username):
            if len(secret) >= 3:
                text = text.replace(secret, "[hidden]")
        return SignIn(text, outcome, detail, step if outcome == "code_needed" else "")

    async def extra_auth_signs(self) -> str:
        """What shows, by structure, that the page asks the payer to prove it is them ("" = nothing): a one-time-code input in the page or a
        frame, or the tab left the site a purchase was allowed on."""
        key = browser_owner.get()
        got = self._purchase.get(key)
        page = self._pages.get(key)
        if page is None:
            return ""
        signs: list[str] = []
        host = (urlsplit(str(getattr(page, "url", "") or "")).hostname or "").lower()
        if got and host and host != got[0] and not same_site(host, got[0]):
            signs.append("the page left the shop's site")
        try:
            frames = list(page.frames) if hasattr(page, "frames") else [page]
        except Exception:  # noqa: BLE001
            frames = [page]
        for frame in frames or [page]:
            try:
                if await frame.evaluate(_EXTRA_AUTH_JS) is True:
                    signs.append("a one-time-code box")
                    break
            except Exception:  # noqa: BLE001 - a frame that is gone or closed to scripts
                continue
        return ", ".join(signs)

    def in_checkout(self) -> bool:
        """A purchase was allowed on this tab (the window may have run out): pages are read with payment authentication in mind."""
        return browser_owner.get() in self._purchase

    async def fill_card(self, number: str, cvc: str, exp_month: int, exp_year: int, holder: str = "", postal: str = "") -> CardFill:
        """Type a card into the checkout form of the current page and of every frame the browser can reach. The values go straight from
        here into the fields; the answer names the fields, never their contents."""
        page = await self.page()
        await self._guard(page.url)
        try:
            frames = list(page.frames) if hasattr(page, "frames") else [page]
        except Exception:  # noqa: BLE001
            frames = [page]
        frames = frames or [page]
        filled: list[str] = []
        unreachable: list[str] = []
        seen: dict[str, bool] = {}
        for frame in frames:
            where = urlsplit(str(getattr(frame, "url", "") or "")).hostname or "page"
            try:
                found = await frame.evaluate(_TAG_CARD_FIELDS, None)
                if isinstance(found, dict) and found.get("open") and self.page_judge is not None:
                    # fields with no autocomplete token: the page judge reads their labels (nothing is typed by a word list)
                    named = await self.page_judge.fields("card", [r for r in found["open"] if isinstance(r, dict)])
                    if named:
                        found = await frame.evaluate(_TAG_CARD_FIELDS, named)
            except Exception:  # noqa: BLE001 - a frame that is gone or closed to scripts
                unreachable.append(where)
                continue
            if not isinstance(found, dict) or not found.get("any"):
                continue
            try:
                done = await self._fill_frame(frame, found, number, cvc, exp_month, exp_year, holder, postal)
            except Exception as e:  # noqa: BLE001 - a driver message could quote what was typed: name only the kind of failure
                raise BrowserPolicyError(f"could not fill the card form ({type(e).__name__})") from None
            for k in done:
                seen[k] = True
                filled.append(k if where == "page" or frame is frames[0] else f"{k} ({where})")
        missing = [k for k in ("number", "expiry", "cvc") if k not in seen]
        try:
            await self._guard(page.url)
        except BrowserPolicyError:
            await page.goto("about:blank")
            raise
        text = scrub_card(await self.snapshot(), number)
        return CardFill(filled, missing, unreachable, await self.extra_auth_signs(), text)

    async def _fill_frame(self, frame: Any, found: dict[str, Any], number: str, cvc: str, m: int, y: int, holder: str, postal: str) -> list[str]:
        done: list[str] = []
        paths = found.get("paths") or {}
        loc = lambda kind: frame.locator(paths.get(kind) or NO_ELEMENT)  # noqa: E731

        async def put(kind: str, value: str) -> None:   # typed key by key like a person (never a typo), or filled as before
            box = loc(kind)
            await human.type_text(frame, value, locator=box, secret=True, timeout_ms=5_000, fallback=lambda ms: box.fill(value, timeout=ms))

        sizes = list(found.get("number") or [])
        if sizes:
            if len(sizes) == 1:
                await put("number-0", number)
            else:
                rest, i = number, 0
                for i, size in enumerate(sizes):
                    take = len(rest) if i == len(sizes) - 1 else (size or 4)
                    await put(f"number-{i}", rest[:take])
                    rest = rest[take:]
            done.append("number")
        exp = found.get("exp")
        if exp:
            await put("exp", expiry_text(m, y, exp.get("max") or 0, exp.get("placeholder") or ""))
            done.append("expiry")
        for kind, want in (("exp_month", m), ("exp_year", y)):
            info = found.get(kind)
            if not info:
                continue
            if info.get("options"):
                value = pick_option(info["options"], want, year=kind == "exp_year")
                if value is None:
                    continue
                await human.hover(frame, loc(kind))
                await loc(kind).select_option(value=value, timeout=5_000)
            else:
                short = kind == "exp_year" and ((info.get("max") or 0) == 2 or ("yy" in (info.get("placeholder") or "") and "yyyy" not in (info.get("placeholder") or "")))
                await put(kind, f"{want % 100:02d}" if short or kind == "exp_month" else str(want))
            if "expiry" not in done and (kind == "exp_year" or not found.get("exp_year")):
                done.append("expiry")
        if found.get("cvc"):
            await put("cvc", cvc)
            done.append("cvc")
        if found.get("name") and holder:
            await put("name", holder)
            done.append("holder")
        if found.get("postal") and postal:
            await put("postal", postal)
            done.append("postal code")
        return done

    async def fill_address(self, v: dict[str, Any]) -> CardFill:
        """Type a shipping address into the checkout form of the current page (tools/order.py). `v`: the profile's fields plus
        phone_national, calling_code and admin_area_name. Fields are found by their autocomplete tokens or words; a read-only field (one a
        postcode search fills) gets its value set directly. The answer names the fields that were filled."""
        page = await self.page()
        await self._guard(page.url)
        got = await page.evaluate(_TAG_ADDRESS_FIELDS, None)
        if isinstance(got, dict) and got.get("open") and self.page_judge is not None:
            # fields with no autocomplete token or telephone type: the page judge reads their labels (nothing is matched by a word list)
            named = await self.page_judge.fields("shipping", [r for r in got["open"] if isinstance(r, dict)])
            if named:
                got = await page.evaluate(_TAG_ADDRESS_FIELDS, named)
        found: dict[str, list[dict[str, Any]]] = (got or {}).get("found") or {}
        filled: list[str] = []
        try:
            async def put(field: dict[str, Any], value: str) -> bool:
                if not value:
                    return False
                loc = page.locator(field["path"])
                if field["el"] == "select":
                    return False
                if field.get("ro"):
                    return bool(await page.evaluate(_SET_READONLY, [field["path"], value]))
                await human.type_text(page, value, locator=loc, timeout_ms=5_000, fallback=lambda ms: loc.fill(value, timeout=ms))
                return True

            async def choose(field: dict[str, Any], wants: list[str]) -> bool:
                value = pick_select(field.get("opts") or [], wants)
                if value is None:
                    return False
                loc = page.locator(field["path"])
                await human.hover(page, loc)
                await loc.select_option(value=value, timeout=5_000)
                return True

            async def one(kind: str, value: str, label: str, wants: list[str] | None = None) -> None:
                for field in found.get(kind, [])[:1]:
                    ok = await choose(field, wants or [value]) if field["el"] == "select" else await put(field, value)
                    if ok:
                        filled.append(label)

            name = str(v.get("name") or "")
            if found.get("given") and found.get("family"):
                parts = name.split()
                cjk = bool(re.match(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", name))
                if cjk:
                    family, given = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (name[:1], name[1:])
                else:
                    family, given = (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else ("", name)
                await one("family", family, "family name")
                await one("given", given, "given name")
            else:
                await one("name", name, "name")
            cc = str(v.get("country") or "")
            await one("country", cc, "country", [cc, *[c["name"] for c in _country_names(cc)]])
            line1, line2 = str(v.get("address_line1") or ""), str(v.get("address_line2") or "")
            if found.get("street"):
                await one("street", "\n".join(x for x in (line1, line2) if x), "street address")
            else:
                await one("line1", line1 if found.get("line2") or not line2 else f"{line1} {line2}", "address line 1")
                await one("line2", line2, "address line 2")
            await one("level3", str(v.get("dependent_locality") or ""), "district")
            await one("level2", str(v.get("locality") or ""), "city")
            region = str(v.get("admin_area") or "")
            await one("level1", region, "region", [region, str(v.get("admin_area_name") or "")])
            postal = str(v.get("postal_code") or "")
            boxes = found.get("postal", [])
            if len(boxes) > 1 and all(b["max"] for b in boxes[:-1]):
                digits = re.sub(r"[\s-]", "", postal)
                for b, part in zip(boxes, split_by(digits, [b["max"] for b in boxes])):
                    await put(b, part)
                filled.append("postal code")
            else:
                await one("postal", postal, "postal code")
            e164, national = str(v.get("phone") or ""), str(v.get("phone_national") or "")
            tels = found.get("tel", [])
            if found.get("tel_country") and tels:
                code = str(v.get("calling_code") or "")
                await one("tel_country", code, "phone country code", [code, code.lstrip("+"), cc])
                rest = re.sub(r"\D", "", e164)[len(code.lstrip("+")):]
                if await put(tels[0], rest):
                    filled.append("phone")
            elif len(tels) > 1:
                sizes = [b["max"] for b in tels]
                if not all(sizes[:-1]):   # boxes without a length: the usual 3/4/4 (or 3/3/4) split of a national number
                    sizes = [3, len(national) - 7, 4] if len(tels) == 3 else [len(national) // 2, 0]
                first = tels[0]
                if first["el"] == "select":   # a list of area / mobile prefixes, then the boxes for the rest
                    pre = next((o[0] for o in first["opts"] if o[0] and national.startswith(o[0])), None)
                    if pre is not None:
                        loc = page.locator(first["path"])
                        await loc.select_option(value=pre, timeout=5_000)
                        for b, part in zip(tels[1:], split_by(national[len(pre):], [b["max"] for b in tels[1:]] or [0])):
                            await put(b, part)
                        filled.append("phone")
                else:
                    for b, part in zip(tels, split_by(national, sizes)):
                        await put(b, part)
                    filled.append("phone")
            elif tels:
                if await put(tels[0], phone_for(tels[0], national, e164)):
                    filled.append("phone")
            await one("note", str(v.get("note") or ""), "delivery note")
        except Exception as e:  # noqa: BLE001 - a driver message could quote what was typed: name only the kind of failure
            raise BrowserPolicyError(f"could not fill the shipping form ({type(e).__name__})") from None
        want = {"name": bool(v.get("name")), "address line 1": bool(v.get("address_line1")) and not found.get("street"),
                "postal code": bool(v.get("postal_code")), "phone": bool(v.get("phone")), "city": bool(v.get("locality")),
                "region": bool(v.get("admin_area"))}
        have = set(filled) | ({"name"} if {"family name", "given name"} <= set(filled) else set()) | (
            {"address line 1"} if "street address" in filled else set())
        missing = [k for k, w in want.items() if w and k not in have]
        await self._guard(page.url)
        return CardFill(filled, missing, [], "", await self.snapshot())

    # ------------------------------------------------------------------ browser card for the chat
    async def card(self, status: str, *, force: bool = False) -> dict[str, Any] | None:
        """Show the tab in the chat (title, URL, status line, small JPEG). At most one per CARD_MIN_INTERVAL seconds per tab
        unless `force`. Returns what was sent, or None (nobody listening, too soon, or no page)."""
        key = browser_owner.get()
        now = time.monotonic()
        self._status[key] = (status, time.time())
        if not force and now - self._last_card.get(key, -1e9) < CARD_MIN_INTERVAL:
            return None
        page = self._pages.get(key)
        if page is None or page.is_closed():
            return None
        url = str(page.url or "")
        if not url.startswith(("http://", "https://")):
            return None
        try:
            title = str(await page.title())[:200]
        except Exception:  # noqa: BLE001
            title = ""
        image, size, w, h = None, 0, 0, 0
        try:
            raw = await self.shoot(page, quality=70)
            jpeg, w, h = await asyncio.to_thread(shrink_jpeg, raw)
            if jpeg is not None:
                image, size = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(), len(jpeg)
        except Exception:  # noqa: BLE001 - a card without a picture is still a card
            image = None
        if self.purchase_active(urlsplit(url).hostname or ""):
            status = t("Purchase in progress")
        item = {"type": "browser", "tab": key, "title": title, "url": url, "host": urlsplit(url).hostname or "", "status": status,
                "image": image, "width": w, "height": h, "bytes": size, "at": int(time.time())}
        if not await emit_ui(item):
            return None
        self._last_card[key] = now
        return item

    # ------------------------------------------------------------------ the app's list of open tabs
    async def tabs(self) -> list[dict[str, Any]]:
        """Every open tab: {"key" (its owner: "owner", a device id, "schedule:<id>", "task:<id>"), "title", "url", "host", "status",
        "last_activity", "controlled_by" (the device holding it, or None)}. Nothing is opened or navigated."""
        out = []
        for key, page in list(self._pages.items()):
            try:
                if page.is_closed():
                    continue
                url = str(page.url or "")
                title = str(await page.title())[:200] if url.startswith(("http://", "https://")) else ""
            except Exception:  # noqa: BLE001 - a tab that went away meanwhile
                continue
            status, at = self._status.get(key, ("", 0.0))
            win = self._windows.get(key)
            out.append({"key": key, "title": title, "url": url, "host": urlsplit(url).hostname or "", "status": status,
                        "tabs": win.listing() if win is not None else [],
                        "last_activity": max(at, self._seen_at.get(key, 0.0)) or None, "controlled_by": self._control.get(key),
                        "browser": "work" if work_tab(key) and self.work is not None else "main"})
        return out

    # ------------------------------------------------------------------ closing idle tabs (driven by the server's sweeper)
    def open_keys(self) -> list[str]:
        out = []
        for key, page in list(self._pages.items()):
            try:
                if not page.is_closed():
                    out.append(key)
            except Exception:  # noqa: BLE001
                continue
        return out

    def touch(self, key: str) -> None:
        """Count the tab as used now (the end of a turn that used it, the user watching or holding it)."""
        if key in self._pages:
            self._used[key] = self.clock()

    def idle_for(self, key: str) -> float:
        """Seconds since the tab was last used; a tab never seen before starts counting now."""
        now = self.clock()
        return now - self._used.setdefault(key, now)

    async def tab_info(self, key: str) -> dict[str, Any] | None:
        return next((t for t in await self.tabs() if t["key"] == key), None)

    def has_tab(self, key: str) -> bool:
        page = self._pages.get(key)
        return page is not None and not page.is_closed()

    def tab_state(self, key: str) -> str:
        """What a restart or a lost browser left of a job's tab (job_cuts.py): "held" (the owner holds it), "kept" (the same page is open
        again), "new" (the tab was booked and its page is gone), else "none"."""
        if key in self._control:
            return "held"
        if self._restart_tabs.get(key) == "kept" and self.has_tab(key):
            return "kept"
        if self._restart_tabs.get(key) == "gone" or key in self._lost or (self.book is not None and key in self.book.all()):
            return "new"
        return "none"

    def _told_lost(self, key: str) -> None:
        if self.on_lost is None:
            return
        try:
            self.on_lost(key)
        except Exception:  # noqa: BLE001 - the job's card line is a courtesy; the tab goes on
            log.exception("could not tell that the browser lost tab %s", key)

    async def _target_of(self, page: Any) -> str:
        try:
            cdp = await self.cdp_session(page)
            info = (await cdp.send("Target.getTargetInfo", {})).get("targetInfo") or {}
            return str(info.get("targetId") or "")
        except Exception:  # noqa: BLE001 - a page without DevTools access is no page of a booked tab
            return ""

    async def adopt_after_restart(self) -> dict[str, str]:
        """After a server restart (the browser may have kept running): a booked tab whose page is still open gets that page back ("kept",
        read again before any step); a booked tab with no page is forgotten ("gone"); http(s) pages no tab has are leftovers and are closed.
        Never raises: a failure leaves every booked tab "gone"."""
        out: dict[str, str] = {}
        if self.book is None or self._connect:
            self._restart_tabs = out
            return out
        booked = self.book.all()
        if not booked:   # nothing to find again: no connection is made just to look
            self._restart_tabs = out
            return out
        try:
            await self._ensure_browser()
            if self._ctx is None:
                raise RuntimeError("no browser context")
            by_target: dict[str, Any] = {}
            for page in list(self._ctx.pages):
                if not page.is_closed() and (target := await self._target_of(page)):
                    by_target[target] = page
            for key, entry in booked.items():
                page = by_target.get(str(entry.get("target") or ""))
                if page is None:
                    self.book.forget(key)
                    out[key] = "gone"
                    continue
                self._pages[key] = page
                self._watch(page, key)
                await self._prepare(page, key)
                self._used[key] = self.clock()
                out[key] = "kept"
            wanted = {str(e.get("target") or "") for e in booked.values()}
            for target, page in by_target.items():
                if target not in wanted and str(page.url or "").startswith(("http://", "https://")):
                    await self._quietly(page.close)   # a leftover of no tab: closed, never taken by a job
        except Exception:  # noqa: BLE001 - the tabs start new; the job is told so by its pick-up note
            log.exception("the browser tabs could not be found again after the restart")
            out = {key: "gone" for key in booked}
            for key in booked:
                self.book.forget(key)
        self._restart_tabs = out
        return out

    async def screenshot(self, key: str, max_width: int = CARD_MAX_WIDTH, cap: int = CARD_MAX_BYTES) -> tuple[bytes | None, int, int]:
        """A JPEG of that tab, scaled to `max_width` and at most `cap` bytes; (None, 0, 0) when there is no tab or no picture fits."""
        page = self._pages.get(key)
        if page is None or page.is_closed() or not str(page.url or "").startswith(("http://", "https://")):
            return None, 0, 0
        try:
            raw = await self.shoot(page, quality=75)
            return await asyncio.to_thread(shrink_jpeg, raw, max_width, cap)
        except Exception:  # noqa: BLE001
            return None, 0, 0

    async def close_tab(self, key: str) -> bool:
        """Close one tab. The browser context (cookies, sign-ins) stays: only the page goes. A private tab's own context goes with it,
        and with that everything it kept (cookies, storage, cache). A work tab's sign-ins go back to the owner's browser (work_browser)."""
        signed_here = (self.sign_ins.get(key) or {}).get("site", "")   # read before the sign-in is forgotten below
        async with self._lock:
            page = self._pages.pop(key, None)
            for d in (self._used, self._seen_at, self._status, self._last_card, self._purchase, self._dialogs, self._doc, self._notes,
                      self._checks, self._targets, self._opened, self._tries, self._shot_scale, self._check_clicked, self.sign_ins, self._login_at, self._login_url, self._probes,
                      self._cancel, self._verified, self._last_text, self._ref_words, self._look_url):
                d.pop(key, None)
            held = self._control.pop(key, None)
            if self.book is not None:
                self.book.forget(key)
            self._lost.pop(key, None)
            self._lost_act.discard(key)
            private = self._private.pop(key, None)
            cdp = self._tab_cdp.pop(key, None)
            win = self._windows.pop(key, None)
            under = [t for t in (win.tabs if win is not None else []) if t.page is not page]   # the window's other tabs go with it
            self._popup_setup.pop(key, None)
            self._unchanged.pop(key, None)
        profile = self._tab_profile.pop(key, "") if not self.profile_of(key) else self.profile_of(key)
        self._tab_profile.pop(key, None)
        ptab = self._profile_tabs.pop(key, None)
        pctx = self._profile_ctx.pop(key, None)
        if self.work is not None and self.work.tab(key) is not None:   # read before the context closes: what the job changed goes back
            if signed_here:
                self.work.note_signed(key, signed_here)
            cookies, storage, sites = await self.work.close(key, read_storage=self._work_storage_now)
            if profile:
                self._write_back_unit(key, profile, cookies, storage, sites)
            else:
                await self._carry_back(key, cookies, storage, sites)
        elif ptab is not None:   # a chat tab's own profile context
            if signed_here:
                ptab.signed.add(registrable(signed_here.lower()))
            cookies, storage, sites = await collect_changes(ptab, self._work_storage_now)
            await self._quietly(pctx.close if pctx is not None else None)
            self._write_back_unit(key, profile, cookies, storage, sites)
        for tab in under:   # the other tabs of the task's window go with it
            await self._quietly(tab.tab_cdp.detach if tab.tab_cdp is not None and hasattr(tab.tab_cdp, "detach") else None)
            await self._quietly(lambda p=tab.page: p.close())
        if held is not None:
            if self.journal is not None:
                try:
                    self.journal.stop(key)
                except Exception:  # noqa: BLE001
                    pass
            self._handed_back.setdefault(key, asyncio.Event()).set()
            self._tell(key, None)
        await self._quietly(cdp.detach if cdp is not None and hasattr(cdp, "detach") else None)
        if page is not None:
            await self._quietly(lambda: page.close())
        if private is not None:
            await self._quietly(lambda: private.close())
        for fn in list(self.close_listeners):
            await self._quietly(lambda fn=fn: fn(key))
        return page is not None

    @staticmethod
    async def _quietly(make: Callable[[], Any] | None) -> None:
        """Await make() and ignore any failure (closing or cleaning up something that may already be gone)."""
        if make is None:
            return
        try:
            await make()
        except Exception:  # noqa: BLE001 - already closed or gone
            pass

    # ------------------------------------------------------------------ private browsing (nothing kept)
    def is_private(self, key: str | None = None) -> bool:
        return (browser_owner.get() if key is None else key) in self._private

    async def set_private(self, on: bool, carry_from: str = "") -> dict[str, Any]:
        """Switch this device's tab to private browsing (a fresh context of its own: no cookies, storage or cache from the profile and
        nothing kept after it closes) or back to the normal profile. `carry_from`: a site whose sign-in (cookies + storage) is copied from
        the normal profile into the private tab. Returns {"private", "carried": {"cookies", "storage"} | None}."""
        key = browser_owner.get()
        state = None
        if self.is_private(key):
            await self.close_tab(key)
        if on and carry_from:
            state = await self.export_site_state(carry_from, page=await self.page())
        await self.close_tab(key)
        carried = None
        if on:
            if self._new_context is not None:
                ctx = await self._new_context()
            elif self.work is not None and work_tab(key):
                try:
                    ctx = await self.work.new_private_context()
                except WorkBrowserDown:
                    raise BrowserPolicyError(DOWN_NOTE) from None
            else:
                await self._ensure_browser()
                ctx = await self._browser.new_context(no_viewport=True, accept_downloads=True)
            self._private[key] = ctx
            if state is not None:
                carried = await self.import_site_state(state, ctx=ctx)
            await self.page()
        return {"private": on, "carried": carried}

    # ------------------------------------------------------------------ one site's sign-in state (cookies + local storage)
    async def export_site_state(self, site: str, *, page: Any = None) -> dict[str, Any]:
        """The cookies of a site (and its subdomains) and its local storage, from the context of `page` (default: this device's tab).
        Local storage is read from the page when it is on that site, else asked of the browser for https://<site> and https://www.<site>."""
        host = site_of(site)
        page = page or await self.page()
        ctx = page.context
        cookies = [c for c in await ctx.cookies() if cookie_for(str(c.get("domain") or ""), host)]
        origins: list[dict[str, Any]] = []
        try:
            cur = urlsplit(str(page.url or ""))
            if cur.scheme == "https" and cur.hostname and cookie_for(cur.hostname, host):
                items = await page.evaluate(_READ_STORAGE)
                if isinstance(items, list) and items:
                    origins.append({"origin": f"https://{cur.netloc}", "localStorage": items})
        except Exception:  # noqa: BLE001
            pass
        if not origins:
            cdp = None
            try:
                cdp = await self.cdp_session(page)
                await cdp.send("DOMStorage.enable", {})
                for origin in (f"https://{host}", f"https://www.{host}"):
                    got = await cdp.send("DOMStorage.getDOMStorageItems", {"storageId": {"securityOrigin": origin, "isLocalStorage": True}})
                    items = [[str(k), str(v)] for k, v in (got.get("entries") or [])][:500]
                    if items:
                        origins.append({"origin": origin, "localStorage": items})
            except Exception:  # noqa: BLE001 - cookies alone still carry most sign-ins
                pass
            finally:
                if cdp is not None and cdp is not self._tab_cdp.get(browser_owner.get()):
                    await self._quietly(cdp.detach if hasattr(cdp, "detach") else None)
        return {"host": host, "cookies": cookies, "origins": origins}

    async def import_site_state(self, state: dict[str, Any], *, ctx: Any = None) -> dict[str, int]:
        """Put a site's cookies into a context (default: this device's tab's) and its local storage into every page of that site opened
        there later (only keys the page does not have yet). Returns {"cookies", "storage"} counts."""
        ctx = ctx or (await self.page()).context
        keys = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
        cookies = [{k: c[k] for k in keys if k in c} for c in state.get("cookies") or [] if c.get("name") and c.get("domain")]
        if cookies:
            await ctx.add_cookies(cookies)
        stored = 0
        for o in state.get("origins") or []:
            items = [[str(k), str(v)] for k, v in o.get("localStorage") or []]
            if not items:
                continue
            stored += len(items)
            await ctx.add_init_script(script="(() => { if (location.origin !== %s) return; for (const [k, v] of %s) { try { if (localStorage.getItem(k) === null) "
                                             "localStorage.setItem(k, v); } catch (e) {} } })();" % (json.dumps(o["origin"]), json.dumps(items)))
        return {"cookies": len(cookies), "storage": stored}

    async def save_site_state(self, site: str) -> dict[str, int]:
        """Keep a copy of a site's sign-in state from this device's tab (memory only, SITE_STATE_SECONDS) for another tab to load."""
        state = await self.export_site_state(site)
        self._site_states[state["host"]] = (state, time.monotonic())
        return {"cookies": len(state["cookies"]), "storage": sum(len(o["localStorage"]) for o in state["origins"])}

    async def load_site_state(self, site: str) -> dict[str, int]:
        host = site_of(site)
        got = self._site_states.get(host)
        if got is None or time.monotonic() - got[1] > SITE_STATE_SECONDS:
            self._site_states.pop(host, None)
            raise BrowserPolicyError(f"no saved sign-in state for {host}; save it first from a tab that is signed in")
        return await self.import_site_state(got[0])

    def forget_site_state(self, site: str) -> bool:
        return self._site_states.pop(site_of(site), None) is not None

    async def forget_site(self, site: str) -> str:
        """Clear cookies of `site`'s registrable domain from the default context, clear site data for known https origins, and drop saved sign-in state.
        The owner's yes is the approval card of the tool (browser_more), never words read from a message."""
        host = site_of(site)
        reg_domain = registrable(host)
        if not reg_domain:
            raise BrowserPolicyError(f"invalid site {site!r}")

        # Default context
        if getattr(self, "_browser", None) is not None and hasattr(self._browser, "contexts") and self._browser.contexts:
            ctx = self._browser.contexts[0]
        else:
            page = await self.page()
            ctx = page.context

        def cookie_matches(c: dict[str, Any]) -> bool:
            d = str(c.get("domain") or "").lstrip(".")
            return bool(d) and (d == reg_domain or registrable(d) == reg_domain)

        all_cookies = await ctx.cookies() if hasattr(ctx, "cookies") else []
        matching = [c for c in all_cookies if cookie_matches(c)]
        count = len(matching)

        if hasattr(ctx, "clear_cookies"):
            try:
                rx = re.compile(rf"(^|\.){re.escape(reg_domain)}$", re.I)
                await ctx.clear_cookies(domain=rx)
            except Exception:
                pass
        if hasattr(ctx, "_cookies") and isinstance(ctx._cookies, list):
            ctx._cookies = [c for c in ctx._cookies if not cookie_matches(c)]

        # Collect https origins from cookies and open tabs
        origins: set[str] = {f"https://{host}"}
        if reg_domain != host:
            origins.add(f"https://{reg_domain}")
        for c in matching:
            d = str(c.get("domain") or "").lstrip(".")
            if d:
                origins.add(f"https://{d}")

        open_pages = []
        if hasattr(self, "_pages"):
            open_pages.extend(self._pages.values())
        if hasattr(ctx, "pages"):
            open_pages.extend(ctx.pages)
        for p in open_pages:
            try:
                if not getattr(p, "is_closed", lambda: False)():
                    p_url = getattr(p, "url", "")
                    if p_url:
                        u = urlsplit(p_url)
                        if u.scheme == "https" and u.hostname:
                            if registrable(u.hostname) == reg_domain:
                                origins.add(f"https://{u.netloc}")
            except Exception:
                pass

        # Clear site data via CDP Storage.clearDataForOrigin (bounded 5s each)
        cdp = None
        try:
            page = await self.page()
            cdp = await self.cdp_session(page)
            for origin in sorted(origins):
                try:
                    await asyncio.wait_for(
                        cdp.send("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"}),
                        timeout=5.0
                    )
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if cdp is not None and cdp is not self._tab_cdp.get(browser_owner.get()):
                await self._quietly(cdp.detach if hasattr(cdp, "detach") else None)

        # Drop saved sign-in state (SITE_STATE)
        to_drop = [h for h in list(self._site_states) if h == reg_domain or registrable(h) == reg_domain]
        for h in to_drop:
            self._site_states.pop(h, None)

        if self.work is not None:
            await self.work.forget(site)   # the open work tabs' contexts too
        self._hook(self.on_site_forgotten, reg_domain)
        return (f"Removed {count} cookies for {reg_domain} and cleared its site data. "
                "The site needs a new sign-in (the vault's saved login, if any, still works: browser_login).")

    def saved_sites(self) -> list[str]:
        now = time.monotonic()
        return sorted(h for h, (_, at) in self._site_states.items() if now - at <= SITE_STATE_SECONDS)

    # ------------------------------------------------------------------ files in and out of pages
    async def upload(self, selector: str, files: list[tuple[str, str, bytes]]) -> str:
        """Set files into a file input of this device's page (the bytes go from the server into the page; nothing is read from the user's
        computer). `files`: (name, MIME type, bytes)."""
        page = await self.page()
        await self._guard(page.url)
        total = sum(len(b) for _, _, b in files)
        if not files or total > UPLOAD_MAX_BYTES:
            raise BrowserPolicyError(f"between 1 file and {UPLOAD_MAX_BYTES // 1024**2} MB in all can be uploaded at once")
        if ref_of(selector):
            await self.frame_for(page, selector)   # a ref of an older reading: said so
        payload = [{"name": n, "mimeType": m, "buffer": b} for n, m, b in files]
        loc = page.locator(ref_selector(selector))
        loc = getattr(loc, "first", loc)
        # 1. the target is the file input (or its label)
        try:
            await loc.set_input_files(payload, timeout=5_000)
            return await self.snapshot()
        except Exception:  # noqa: BLE001 - not a file input: the ways below
            pass
        # 2. a button that opens the file picker (Threads, X, most social sites hide the input): click it and hand the files to the
        #    picker, which never opens on the screen (owner 2026-11-14)
        try:
            async with page.expect_file_chooser(timeout=8_000) as chooser_info:
                await loc.click(timeout=5_000)
            chooser = await chooser_info.value
            await chooser.set_files(payload)
            return await self.snapshot()
        except Exception:  # noqa: BLE001
            pass
        # 3. a hidden file input on the page: the one that takes this kind of file (or the only one)
        inputs = page.locator("input[type=file]")
        try:
            count = await inputs.count()
        except Exception:  # noqa: BLE001
            count = 0
        kinds = {m.split("/", 1)[0] for _, m, _ in files}
        chosen = None
        for i in range(count):
            accept = str(await inputs.nth(i).get_attribute("accept") or "")
            if count == 1 or not accept or any(k + "/" in accept for k in kinds):
                chosen = inputs.nth(i)
                break
        if chosen is not None:
            try:
                await chosen.set_input_files(payload, timeout=10_000)
                return await self.snapshot()
            except Exception:  # noqa: BLE001
                pass
        raise BrowserPolicyError(f"could not put the files into {selector!r}: it is not a file input, clicking it opened no file "
                                 "picker, and the page has no file input that takes them. Read the page again and pass the "
                                 "button that adds a photo or video.")

    async def save_pdf(self) -> tuple[bytes, str, str]:
        """This device's page printed to PDF (backgrounds included): (bytes, title, url)."""
        page = await self.page()
        url = str(page.url or "")
        await self._guard(url)
        cdp = await self.cdp_session(page)
        try:
            got = await cdp.send("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True})
        finally:
            await self._quietly(cdp.detach if hasattr(cdp, "detach") else None)
        data = base64.b64decode(got.get("data") or "")
        if not data.startswith(b"%PDF"):
            raise BrowserPolicyError("the browser did not make a PDF of this page")
        if len(data) > PDF_MAX_BYTES:
            raise BrowserPolicyError(f"the PDF is larger than {PDF_MAX_BYTES // 1024**2} MB")
        try:
            title = str(await page.title())[:120]
        except Exception:  # noqa: BLE001
            title = ""
        return data, title, url

    async def fill_code(self, code: str, *, selector: str = "", submit: bool = True) -> str:
        """Type a one-time code into the code box(es) of this device's page and (optionally) press Enter. The code goes straight to the page;
        the answer is the page afterwards with the code taken out."""
        page = await self.page()
        await self._guard(page.url)
        if ref_of(selector):   # a ref of the last reading: its CSS path is what the code script reads
            await self.frame_for(page, selector)
            selector = await page.locator(ref_selector(selector)).first.evaluate(PATH_OF_JS)
        found = await page.evaluate(_TAG_CODE_FIELDS, selector or None)
        boxes = int(found.get("boxes") or 0) if isinstance(found, dict) else 0
        paths = list(found.get("paths") or []) if isinstance(found, dict) else []
        if not boxes:
            raise BrowserPolicyError("no box for a one-time code was found on this page" + ("" if selector else "; name it with selector"))
        if boxes > 1 and boxes != len(code):
            raise BrowserPolicyError(f"the page has {boxes} code boxes but the code has {len(code)} characters")
        try:
            if boxes == 1:
                one = page.locator(paths[0])
                await human.type_text(page, code, locator=one, secret=True, timeout_ms=10_000, fallback=lambda ms: one.fill(code, timeout=ms))
            else:
                for i, ch in enumerate(code):
                    box = page.locator(paths[i])
                    await human.type_text(page, ch, locator=box, secret=True, timeout_ms=5_000,
                                          fallback=lambda ms, box=box, ch=ch: box.fill(ch, timeout=ms))
            if submit:
                await human.pause_before_submit()
                await page.locator(paths[boxes - 1]).press("Enter", timeout=10_000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001 - a driver message could quote the code: name only the kind of failure
            raise BrowserPolicyError(f"could not type the code ({type(e).__name__})") from None
        try:
            await self._guard(page.url)
        except BrowserPolicyError:
            await page.goto("about:blank")
            raise
        await asyncio.sleep(self.settle_seconds)
        return (await self.snapshot()).replace(code, "[code hidden]")

    async def close(self) -> None:
        if self.work is not None:
            await self.work.shutdown()
        if self._pw is not None:
            await self._pw.stop()
            self._pw = self._ctx = self._browser = None
            self._pages.clear()
            self._used.clear()


RETRY_SAME_SECONDS = 180.0   # an allowed click that failed: the same element tried again within this long does not ask again
RETRY_SAME_TRIES = 3


REHAND_SECONDS = 15.0        # after a verification hand-over of a site ended: a page of the same step kind waits this long to settle
SETTLE_QUIET = 2.5           # a page that left the verification step counts as settled after this long without change (a "signing
                             # you in..." page that moves on by itself within a second or two is waited out)...
SETTLE_MAX = 8.0             # ...or this long after it left, whichever comes first
APPROVE_QUIET_SECONDS = 120.0   # find_login_code refuses for a site whose approve-step hand-over is open or ended this recently

# What a typing target is: a text field (typed), a <select> (with its options) or something else (a custom list that opens on a click)
KIND_JS = r"""(e) => { const tag = e.tagName.toLowerCase(), type = (e.getAttribute('type') || '').toLowerCase();
  const role = (e.getAttribute('role') || '').toLowerCase(), pops = e.hasAttribute('aria-haspopup');
  const text_field = !(e.readOnly && (role === 'combobox' || pops)) && ((tag === 'input' &&
    !['checkbox', 'radio', 'button', 'submit', 'image', 'file', 'reset'].includes(type)) || tag === 'textarea' || e.isContentEditable);
  return {tag, text_field, options: tag === 'select' ? [...e.options].map((o) => ({value: o.value, label: o.text})) : []}; }"""
# The page's visible <select> lists: a selector for each, what it shows now and its options (the page text alone does not say which
# option is chosen)
LISTS_JS = r"""() => { const out = [];
  const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const all = [...document.querySelectorAll('select')];
  all.forEach((e, i) => { if (out.length >= 8 || !shown(e)) return;
    const sel = e.id ? `select#${CSS.escape(e.id)}` : e.name ? `select[name="${e.name.replace(/"/g, '\\"')}"]` : `select >> nth=${i}`;
    const lab = (e.labels && e.labels[0] && e.labels[0].innerText) || e.getAttribute('aria-label') || e.getAttribute('title') || '';
    out.push({sel, label: lab.trim().slice(0, 40), now: ((e.selectedOptions[0] || {}).text || '').trim().slice(0, 40),
              options: [...e.options].slice(0, 25).map((o) => o.text.trim().slice(0, 30))}); });
  return out; }"""

# Links of the open page that go to one of a saved login's own hosts, on another host than the page's: where the site's own sign-in most
# likely is (structure only: the link's target, never its words). Their words, at most 3.
SIGN_IN_LINKS_JS = r"""(hosts) => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none'; };
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    if (out.length >= 3) break;
    let u; try { u = new URL(a.getAttribute('href'), location.href); } catch (e) { continue; }
    if (u.protocol !== 'https:' || !hosts.includes(u.hostname.toLowerCase()) || u.hostname === location.hostname || !shown(a)) continue;
    const words = (a.innerText || a.getAttribute('aria-label') || a.title || '').replace(/\s+/g, ' ').trim().slice(0, 60);
    if (words && !out.includes(words)) out.push(words);
  }
  return out;
}"""

# The visible, unticked checkboxes and switches of a page, each given as a CSS path and named by the words next to it
OPTIONS_JS = with_path(r"""() => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none'; };
  const words = (e) => (e && (e.innerText || e.textContent) || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const e of document.querySelectorAll('input[type=checkbox], [role=checkbox], [role=switch]')) {
    if (out.length >= 8) break;
    const checked = e.matches('input') ? e.checked : e.getAttribute('aria-checked') === 'true';
    const lab = (e.labels && e.labels[0]) || e.closest('label');
    const box = lab || e;
    if (checked || e.disabled || !(shown(e) || (lab && shown(lab)))) continue;
    let label = e.getAttribute('aria-label') || words(lab);
    if (!label && e.getAttribute('aria-labelledby')) label = words(document.getElementById(e.getAttribute('aria-labelledby')));
    if (!label) label = words(e.parentElement).slice(0, 120);
    // a styled box hides its input: the label is what takes the click
    out.push({path: keloPath(shown(e) ? e : box), label: label.slice(0, 120)});
  }
  return out;
}""")


def target_label(info: dict[str, Any] | None, selector: str) -> str:
    """The element's words as the user sees them on the page (its text, label, aria-label), never a selector. "" when there are none
    readable (then the card says "a button" / "a field"). An element that could not be read gives "" too: the words in a selector are
    the model's, not the page's (2026-11-10: a card showed the model's own sentence "click the button below to finish" as the label)."""
    words = label_of(info, selector) if info is not None and pressable(info) else ""   # plain text is no button's name
    words = " ".join(str(words).split())
    return words[:60] + "…" if len(words) > 60 else words


def link_place(info: dict[str, Any] | None) -> str:
    """Where a link goes, as a card shows it: host and path, without the query ("" for no link, a #fragment or a script link)."""
    href = str((info or {}).get("href") or "").strip()
    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
        return ""
    try:
        parts = urlsplit(urljoin(str((info or {}).get("url") or ""), href))
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    place = parts.hostname + unquote(parts.path).rstrip("/")
    return place[:60] + "…" if len(place) > 60 else place


def page_words(info: dict[str, Any] | None) -> str:
    """The plain text of what a step clicks when it is no control (a selector picked words on the page): the page's own words."""
    words = " ".join(str((info or {}).get("text") or "").split())
    return words[:60] + "…" if len(words) > 60 else words


def ref_check(session: "BrowserSession", inner: Callable[[dict[str, Any]], str | None] | None = None) -> Callable[[dict[str, Any]], Awaitable[str | None]]:
    """A tool's `validate`: `inner`'s reason first, then a ref (args["selector"]) the page no longer has (STALE_REF), before any card."""
    async def check(args: dict[str, Any]) -> str | None:
        if inner is not None and (problem := inner(args)):
            return problem
        return await session.gone_ref(str(args.get("selector") or ""))
    return check


SELECTOR_ARG = "ref (e12) from your latest reading of the page (a ref ends when the page changes), or a Playwright selector"

# What the approval card adds, by commit kind (commits.py).
_COMMIT_WARNINGS = {
    "send": N_("This may send or post something in your name."),
    "delete": N_("This may delete or cancel something."),
    "apply": N_("This may send an application or an entry in your name."),
    "account": N_("This may sign you up or change your account."),
    "security": N_("This may change how your account is protected (an app password, 2-step verification, a device) or issue an official document in your name."),
    "form": N_("This may send a form with your details."),
    "personal": N_("This types personal details or a message."),
    "optout": N_("This may opt you out of something, such as messages or notifications. It can usually be turned back on."),
}


def step_decider(session: BrowserSession, tool: str, *, approve_actions: bool = False, grants: Any = None,
                 orders: Any = None) -> Callable[[dict[str, Any]], Any]:
    """The `auto_approve` of a click, typing or point step (browser_click, browser_type, browser_click_at, browser_drag): True =
    no card. A point step whose element was read counts as a click on that element; one Kelo could not read ("unknown") always asks."""
    as_tool = "browser_click" if tool in POINT_TOOLS else tool
    async def decide(args: dict[str, Any]) -> bool:
        """No card for a step that only looks (unless the owner set every step to ask), nor for one that finishes a purchase
        the user just allowed on this site."""
        kind = await session.judge(tool, args)
        if not kind and not approve_actions:
            return True
        if kind == "unknown":
            return False   # Kelo could not read what this step acts on: the user always sees it (no rule, grant or order covers it)
        sel = step_key(tool, args)
        if kind == "security":
            # the account's protection or an official document: only the very element the user just allowed (a retry of its
            # failed click) goes without a card; no rule, grant, order, purchase window or sign-in shortcut covers it
            return as_tool == "browser_click" and session.retry_of_allowed(sel)
        if kind and as_tool == "browser_click" and session.retry_of_allowed(sel):
            return True   # the same element the user just allowed, whose click failed: another selector for it
        if kind and grants is not None and grants.use(kind, session.current_host(), tool=as_tool, secret=session.judged_secret(sel)):
            return True   # the user allowed this kind of step on this site for the task running now
        if as_tool == "browser_click" and kind == "delete" and session.use_cancel_action(sel):
            return True   # cancels the order whose charge was wrong, while cancelling is free (purchase_result's cancel window)
        if session.use_purchase_action():
            # finishes the purchase the user just allowed on this site (its window: a few clicks, a few minutes, on the pages that
            # follow from where it was allowed; browser_open ends it). Checked before the "paid" rule below: the final button
            # of an approved order is itself a "buy" that the paid order would otherwise ask about again
            return True
        if kind and orders is not None and not approve_actions and kind == "apply" \
                and (o := orders.start_implicit(session.current_host())) is not None:
            # a form step without begin_order (a weaker model often skips it): the order the user asked for starts here, so its
            # preparing steps don't each ask; the final payment still goes through checkout_review
            from .order import IMPLICIT_NOTE
            session.note(browser_owner.get(), IMPLICIT_NOTE.format(site=o.site, what=o.what))
        return bool(kind and orders is not None and not approve_actions and orders.covers(
            kind, session.current_host(), tool=as_tool, info=session.judged_info(sel), selector=sel, url=session.current_url(),
            text=str(args.get("text", "")), secret=session.judged_secret(sel)))   # only prepares the order going on here
    return decide


def browser_tools(session: BrowserSession, *, approve_actions: bool = False, grants: Any = None, orders: Any = None) -> list[Tool]:
    """approve_actions=True: every click and typing step asks (the owner's ASSISTANT_APPROVE_BROWSER_ACTIONS=1). Default: only the
    steps that commit something in the user's name ask (commits.py). `grants`: the permissions users gave for one task
    (task_permission.TaskGrants), which cover that task's repeated steps of one kind on one site. `orders`: the order the user asked
    for (tools/order.Orders), which covers the steps that only prepare it on its site."""
    async def shown(text: str, status: str) -> str:
        await session.card(status)
        return text

    async def open_(args: dict[str, Any]) -> str:
        if profile := str(args.get("profile") or "").strip():   # only for a task window that is not open yet
            if bad := session.try_bind_profile(browser_owner.get() or "", profile):
                return bad
        return await shown(await session.open(str(args["url"])), t("Looking at the site"))

    async def read(args: dict[str, Any]) -> str:
        return await shown(await session.snapshot(clear=True, only_controls=args.get("only") == "controls"), t("Checking the page"))

    async def click(args: dict[str, Any]) -> str:
        target = session.note_allowed(str(args["selector"]))
        try:
            text = await session.click(str(args["selector"]))
        except Exception:
            session.allowed_failed(target)   # tried again with another selector for the same element: no second card
            raise
        if session.last_click_changed_nothing() and session.judged(str(args["selector"])):
            # allowed, ran, changed nothing: the same element tried again (another selector) does not ask again
            session.allowed_failed(target)
        else:
            session.allowed_done()
        if session.in_checkout() and (words := await session.extra_auth_signs()):
            text += "\n\n" + EXTRA_AUTH_NOTE.format(words=words)
            return await shown(text, t("Waiting for payment verification"))
        return await shown(text, t("Working on the page"))

    async def type_(args: dict[str, Any]) -> str:
        return await shown(await session.type(str(args["selector"]), str(args["text"]), bool(args.get("submit", False))), t("Typing"))

    def no_card(tool: str) -> Callable[[dict[str, Any]], Any]:
        return step_decider(session, tool, approve_actions=approve_actions, grants=grants, orders=orders)

    def declined_click(args: dict[str, Any]) -> str:
        sel = str(args.get("selector", ""))
        label = target_label(session.judged_info(sel), sel) or session.ref_label(sel) or label_of(session.judged_info(sel), sel) or sel
        return (f"The user declined clicking '{label}'. When this was one answer of a choice the page offers (the other button of a "
                "yes/no, another option), the user most likely wants a different answer: ask with quick_replies whose FIRST options "
                f"are the page's other choices in the page's own words, and do not offer '{label}' again as an option. Click nothing "
                "for this question until the user answers.")

    def warning(args: dict[str, Any]) -> str:
        kind = session.judged(str(args.get("selector", "")))
        return " " + t(_COMMIT_WARNINGS[kind]) if kind in _COMMIT_WARNINGS else ""

    def unnamed(info: dict[str, Any] | None) -> str:
        """Said after "a button" / "a field" when nothing names it: whether it has no words or could not be read at all."""
        return " " + (t("It has no words on the page.") if info else t("Kelo could not read what is there."))

    async def describe_click(args: dict[str, Any]) -> str:
        host = session.current_host() or t("blank page")
        sel = str(args.get("selector", ""))
        info = session.judged_info(sel)
        # what the user sees on the page, never the selector (for a ref the page's own name for it, never "aria-ref=…"); never only
        # "a button" (owner report 2026-11-19)
        label = target_label(info, sel) or session.ref_label(sel)
        if label:
            line = t("Click “{label}” on {host}.", host=host, label=label)
        elif place := link_place(info):
            line = t("Click the link to {place} on {host}.", host=host, place=place)
        elif text := page_words(info):
            line = t("Click the text “{label}” on {host}.", host=host, label=text)
        else:
            line = t("Click a button on {host}.", host=host) + unnamed(info)
        return line + warning(args)

    async def describe_type(args: dict[str, Any]) -> str:
        host = session.current_host() or t("blank page")
        sel = str(args.get("selector", ""))
        info = session.judged_info(sel)
        label = target_label({**info, "text": "", "words": info.get("words") or info.get("attrs") or ""} if info else None, sel) \
            or session.ref_label(sel)
        line = t("Type into “{label}” on {host}.", host=host, label=label) if label else t("Type into a field on {host}.", host=host) + unnamed(info)
        return line + warning(args)

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    return session.with_deadline([
        Tool("browser_open", "Open a public http(s) address in your browser and return the page text. Use it only to get onto a site: its "
             "home page, an exact address the user gave, your job's start address, or a search result you were given (a person arrives "
             "there by clicking it). Within a site, move the way a person does: click its links, menus and buttons and use its own search "
             "box; never type or build an address of that site (a search, product, sign-in or account page), and never one you made up. "
             "Its reading marks elements with [ref=e12]: click and type by the refs of your last reading.",
             obj({"url": {"type": "string"},
                  "profile": {"type": "string", "description": "a browser_profiles id"}}, ["url"]),
             open_, untrusted=True, source="page", hold=session.hold_for_navigation, trail="web"),
        Tool("browser_read", "Return the current page's title, URL and what it shows, one element per line. Readings mark elements "
             "with [ref=e12]: pass e12 to browser_click and browser_type. When the user questions a value you read, read the page again (or where it shows in full) "
             "before you answer.",
             obj({"only": {"type": "string", "enum": ["controls"],
                           "description": "controls: only what can be clicked or typed into, plus headings (shorter; use it when "
                                          "you only need to act)"}}, []),
             read, untrusted=True, source="page", hold=session.hold_for_user, trail="web"),
        Tool("browser_click", "Click an element and return the page after (also inside frames of the page; a window the click "
             "opens becomes the page you are on). A click that commits something in "
             "the user's name shows them a card first.",
             obj({"selector": {"type": "string", "description": SELECTOR_ARG}}, ["selector"]), click, requires_approval=True, untrusted=True, source="page",
             validate=ref_check(session), auto_approve=no_card("browser_click"), describe=describe_click, declined=declined_click, hold=guarded(session, session.hold_to_act)),
        Tool("browser_type", "Type text into an input. On a list (a select or a dropdown) it chooses the option with that text instead: "
             "the one way to set a list (quantity, size, date). Personal details or "
             "a message show the user a card first.",
             obj({"selector": {"type": "string", "description": SELECTOR_ARG}, "text": {"type": "string"},
                  "submit": {"type": "boolean", "description": "press Enter after"},
                  "uses": USES_PARAM},
                 ["selector", "text"]),
             type_, requires_approval=True, untrusted=True, source="page", validate=ref_check(session), auto_approve=no_card("browser_type"), describe=describe_type,
             hold=guarded(session, session.hold_to_act, only_submit=True)),
    ])


POINT_RULE = ("Only for things your page reading gives no ref for, or whose ref click failed twice: canvas pictures and editors, maps, "
              "sliders, drawing, drag handles. Look first (browser_look), act, then look or read again to check. After two failed tries "
              "at the same spot, try another way. Go back to refs as soon as you can.")


def point_tools(session: BrowserSession, *, approve_actions: bool = False, grants: Any = None, orders: Any = None) -> list[Tool]:
    """browser_click_at and browser_drag: a point (or a path) of the last browser_look picture, for what has no ref. Approval like
    browser_click, judged by the element at the (first) point; a point Kelo cannot read (a frame, nothing there) always asks."""
    async def shown(text: str, status: str) -> str:
        await session.card(status)
        return text

    def ok(tool: str) -> Callable[[dict[str, Any]], str | None]:
        def check(args: dict[str, Any]) -> str | None:
            try:
                step_points(tool, args)
            except (TypeError, ValueError) as e:
                return str(e)
            return None if session.point_ready() else LOOK_FIRST
        return check

    async def click_at(args: dict[str, Any]) -> str:
        x, y = step_points("browser_click_at", args)[0]
        return await shown(await session.click_at(x, y), t("Working on the page"))

    async def drag(args: dict[str, Any]) -> str:
        return await shown(await session.drag(step_points("browser_drag", args)), t("Working on the page"))

    def describe(tool: str) -> Callable[[dict[str, Any]], Awaitable[str]]:
        async def words(args: dict[str, Any]) -> str:
            host = session.current_host() or t("blank page")
            label = target_label(session.judged_info(step_key(tool, args)), "")
            if tool == "browser_click_at":
                line = t("Click a point on {host}.", host=host)
            else:
                line = t("Drag on {host}.", host=host)
            if label:
                line += " " + t("It is on “{label}”.", label=label)
            kind = session.judged(step_key(tool, args))
            if kind == "unknown":
                line += " " + t("Kelo could not read what is there.")
            elif kind in _COMMIT_WARNINGS:
                line += " " + t(_COMMIT_WARNINGS[kind])
            return line
        return words

    point = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"]}
    return session.with_deadline([
        Tool("browser_click_at", "Click a point of your last browser_look picture (x, y in its pixels). " + POINT_RULE,
             {"type": "object", "properties": {"x": {"type": "number", "description": "pixels from the picture's left"},
                                               "y": {"type": "number", "description": "pixels from the picture's top"}},
              "required": ["x", "y"]},
             click_at, requires_approval=True, untrusted=True, source="page", validate=ok("browser_click_at"),
             auto_approve=step_decider(session, "browser_click_at", approve_actions=approve_actions, grants=grants, orders=orders),
             describe=describe("browser_click_at"), hold=guarded(session, session.hold_to_act, tool="browser_click_at")),
        Tool("browser_drag", "Drag along a path of points (x, y) of your last browser_look picture: press at the first, move through "
             "the others, let go at the last. Same rules as browser_click_at: canvas, map, slider or drag handle with no ref; look first, act, look again.",
             {"type": "object", "properties": {"path": {"type": "array", "items": point, "minItems": DRAG_POINTS[0],
                                                        "maxItems": DRAG_POINTS[1]}},
              "required": ["path"]},
             drag, requires_approval=True, untrusted=True, source="page", validate=ok("browser_drag"),
             auto_approve=step_decider(session, "browser_drag", approve_actions=approve_actions, grants=grants, orders=orders),
             describe=describe("browser_drag"), hold=guarded(session, session.hold_to_act, tool="browser_drag")),
    ])
