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
- After the user allowed a purchase (confirm_purchase), the few committing steps that finish it on that same site are covered by
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
import json
import logging
import random
import re
import socket
import time
import weakref
from contextvars import ContextVar
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from ..agent import Tool, emit_ui, handover
from ..agent.loop import ToolStopped, ToolTimeout
from ..agent.fence import data
from ..i18n import N_, t
from ..signin_state import CLEAN_READINGS, still_signing_in
from .challenge import (CHECK_TRIES, PROBE_JS, Challenge, blocked, blocked_again_note, blocked_note, check_boxes, detect, handover_note,
                        note_for, try_note)
from . import humanize as human
from .commits import (TARGET_JS, asks_to_keep_signed_in, cancel_words, continue_only, judge_click, judge_type, keep_signed_in, label_of,
                      money_page, pressable)
from .downloads import DownloadArea
from .pacing import SitePacer
from .sites import registrable, same_site
from .verification import SIGN_IN_SECONDS, Verification, options_note, verification_note, verification_step, waiting_note
from .page_notes import (OVERLAY_JS, OVERLAY_ROUNDS, click_js, closed_note, masked_note, masked_values, not_found_note, open_note,
                         overlays, signature)
from .page_tree import controls_only, for_reading, frame_hosts, frame_of, interactive_refs, ref_boxes, ref_name, ref_of, ref_selector, ref_words

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
CONTROL_WAIT_SECONDS = 300.0      # an agent step waits this long for the user to hand a tab back, then reports that it could not act
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
# The phone view (BrowserSession.set_view): a current Android phone's screen in CSS px, its pixel ratio, mobile and touch
VIEWS = ("desktop", "mobile")
PHONE = {"width": 412, "height": 915, "deviceScaleFactor": 2.625, "mobile": True, "screenWidth": 412, "screenHeight": 915}
SITE_VIEWS_KEPT = 300             # sites whose working view is remembered (memory only)
VIEW_NOTE = "[Now showing this tab in the {view} view (same sign-ins and cookies); the page was loaded again in it.]"
VIEW_SAME_NOTE = "[This tab already shows the {view} view; the page was loaded again.]"
VIEW_REMEMBERED_NOTE = "[Opened in the {view} view: it is the one that worked on this site before.]"
# A window the page opened (window.open, a target=_blank link) becomes the tab's page: the agent goes on in it, with the tab's view.
POPUP_WAIT_SECONDS = 0.4          # after a click: how long a new window may take to show up before the click is taken as staying put
POPUP_NOTE = "[The click opened a new window ({url}); you are in it now (same sign-ins, same view). When it closes you are back on the page that opened it.]"
POPUP_BACK_NOTE = "[The window you were in closed; you are back on the page that opened it.]"
NO_CHANGE_NOTE = ("[The click went through, but nothing changed: same address, same page content and form values, no new window, no "
                  "pop-up. Do not click the same element again. Look at the page below for what holds it (a message, an unticked box, "
                  "a choice still to make, a frame or window it belongs to), try another way (another element; the other desktop / phone view: load browser_view with find_tools), or tell "
                  "the user what you see.]")
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
# the element's centre (scrolled into view) is under something that is not the element, a part of it or what holds it
COVERED_JS = """(e) => { e.scrollIntoView({block: 'center', inline: 'center'}); const r = e.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  const h = e.ownerDocument.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return !!h && h !== e && !e.contains(h) && !h.contains(e); }"""
FRAME_TEXT_JS = """() => document.body ? [innerWidth * innerHeight, document.body.innerText || ''] : [0, '']"""


# browser_look's labels: ONE host element with a closed shadow root over the whole viewport (never takes a click), a 2px outline and
# the ref for each box. Removed right after the picture (LOOK_CLEAR_JS).
LOOK_OVERLAY_JS = r"""(boxes) => {
  document.querySelectorAll('[data-kelo-look]').forEach((e) => e.remove());
  const host = document.createElement('div');
  host.setAttribute('data-kelo-look', '');
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
  return boxes.length;
}"""
LOOK_CLEAR_JS = "() => document.querySelectorAll('[data-kelo-look]').forEach((e) => e.remove())"
# where a frame's own viewport starts inside the frame that holds its iframe element (border and padding included)
FRAME_ORIGIN_JS = """(e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
  return [r.left + e.clientLeft + (parseFloat(s.paddingLeft) || 0), r.top + e.clientTop + (parseFloat(s.paddingTop) || 0)]; }"""


def mobile_identity(full_version: str) -> dict[str, Any]:
    """Emulation.setUserAgentOverride parameters for Chrome of this version on an Android phone: the reduced User-Agent Chrome sends
    there and its client hints (the same brands and versions as the desktop identity, platform Android, mobile, form factor Mobile)."""
    from ..browser_identity import brand_lists
    major = int(full_version.split(".")[0])
    brands, full_list = brand_lists(full_version)
    return {
        "userAgent": f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Mobile Safari/537.36",
        "userAgentMetadata": {
            "brands": brands, "fullVersionList": full_list, "fullVersion": full_version,
            "platform": "Android", "platformVersion": "", "architecture": "", "model": "", "mobile": True,
            "bitness": "", "wow64": False, "formFactors": ["Mobile"],
        },
    }


def _site_of(url: str) -> str:
    from .sites import registrable
    host = (urlsplit(url or "").hostname or "").lower()
    return registrable(host) if host else ""


TARGET_READ_MS = 1_500            # how long reading a click or typing target may take before its selector words are judged instead
LISTENER_READ_MS = 1_200          # how long reading the target's click listeners (DevTools) may take before only its inline code is judged
# listeners that run on a click (read to tell a click that only opens a window or a page from one that sends something)
_CLICK_EVENTS = frozenset({"click", "mousedown", "mouseup", "pointerdown", "pointerup", "touchstart", "touchend", "auxclick"})

# Words of a button that spends money or commits the user, read from a selector alone (the server keeps "allow on this site" rules away
# from such clicks). Only a hint for when to insist on a card: never a permission. The finer judgement on the element is commits.py.
_FINAL_WORDS = ("pay", "buy", "purchase", "checkout", "check out", "place order", "place-order", "order now", "confirm", "submit order",
                "book now", "reserve", "subscribe", "donate", "결제", "구매", "주문", "예약", "신청", "확인", "충전", "송금", "이체", "후원")

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


class BrowserControlled(Exception):
    """The user holds this tab (they took control on their phone); the agent may not act on it now."""


CONTROLLED_NOTE = ("[Not done: the user is controlling the browser right now (they took over this tab on their phone) and has not handed "
                   "it back for {minutes} minutes. Do not use browser tools until they do: tell the user you will carry on when they "
                   "hand the browser back (or ask what they did), and stop here.]")


TOOK_OVER_NOTE = "[The user took over the browser; this step was stopped. Wait for them to hand it back.]"
STEP_TIMEOUT_NOTE = ("[The browser step did not finish in {seconds} s: the page at {host} did not answer (a navigation that never "
                     "completed, or a page that stopped responding). I stopped it and left the page as it is. If this step pressed a button "
                     "that pays, orders, books or sends, it may have gone through: never press it again; browser_read and check the "
                     "account's orders first. Otherwise try browser_read, then the other view, or tell the user the site does not respond.]")
PACE_NOTE = ("[Not submitted: this site blocks quick repeated searches and the last one on {host} was only seconds ago. Wait about "
             "{seconds} s (use the time for something else), then submit again.]")
PACE_MARGIN_SECONDS = 10.0        # a paced wait must leave this much of the step's time to do the step itself
STILL_LOADING_NOTE = "[The page is still loading after {seconds} s; stopped. What is shown is the page as it is now.]"
COVERED_NOTE = ("[The element is covered by {layer}; I could not close it. Ask the user to close it with \"take over the browser\", "
                "or try the other view.]")


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


_FAILED = ("incorrect", "invalid", "wrong password", "wrong username", "not recognized", "not recognised", "doesn't match", "does not match",
           "do not match", "didn't match", "try again", "unable to log in", "unable to sign in", "bad credentials", "authentication failed",
           "login failed", "log in failed", "sign in failed", "sign-in failed", "no account", "access denied", "is not correct", "isn't correct",
           "잘못", "일치하지", "올바르지", "로그인 실패", "다시 시도", "맞지 않", "틀렸", "존재하지 않", "확인해 주세요", "확인해주세요")
_CODE = ("verification code", "verify it's you", "verify your identity", "two-factor", "two factor", "2-step", "two-step", "2fa", "one-time code",
         "one-time password", "authenticator app", "security code", "enter the code", "sent a code", "sent you a code",
         "인증 코드", "인증코드", "인증번호", "인증 번호", "2단계", "보안 코드", "일회용")
_SIGNED_IN = ("log out", "logout", "sign out", "signout", "log off", "로그아웃")


def classify_sign_in(*, before_url: str, after_url: str, password_visible: bool, text: str, challenge: str = "") -> tuple[str, str]:
    """Read a page after a sign-in was submitted. A reading, not a proof: when nothing is clear it says unknown, and what is stored is never
    thrown away because of a reading (a 'failed' only stops the assistant from trying again until the user says so).
    `challenge`: the name of a bot check found on the page by structure (challenge.detect), or ""; words on the page alone never count."""
    t = (text or "").lower()
    if challenge:
        return "blocked", f"the site shows a human check ({challenge})"
    if any(w in t for w in _CODE) and not password_visible:
        return "code_needed", "the site accepted the password and asks for a verification code"
    if password_visible and any(w in t for w in _FAILED):
        return "failed", "the page still shows the sign-in form and says the user name or password is wrong"
    if not password_visible and any(w in t for w in _SIGNED_IN):
        return "success", "signed in"
    if not password_visible and after_url.split("#")[0] != before_url.split("#")[0] and not any(w in t for w in _FAILED):
        return "success", "the sign-in page was left"
    return "unknown", "could not tell whether the site accepted the login"


_PAGE_STATE = """() => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e); return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none'; };
  return { pw: [...document.querySelectorAll('input[type=password]')].some(shown), text: (document.body ? document.body.innerText : '').slice(0, 20000) };
}"""

# Marks the visible username and password inputs of the page (nothing else is read), so they can be filled by the server.
_TAG_LOGIN_FIELDS = """() => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none' && !e.disabled && !e.readOnly; };
  const kind = (e) => (e.getAttribute('type') || '').toLowerCase();
  const inputs = [...document.querySelectorAll('input')].filter(shown);
  document.querySelectorAll('[data-vault-u],[data-vault-p]').forEach((e) => { e.removeAttribute('data-vault-u'); e.removeAttribute('data-vault-p'); });
  const textish = (e) => ['', 'text', 'email', 'tel', 'search'].includes(kind(e));
  const p = inputs.findIndex((e) => kind(e) === 'password');
  let u = -1;
  if (p >= 0) { for (let k = p - 1; k >= 0; k--) { if (textish(inputs[k])) { u = k; break; } } }
  else { u = inputs.findIndex(textish); }
  if (u >= 0) inputs[u].setAttribute('data-vault-u', '1');
  if (p >= 0) inputs[p].setAttribute('data-vault-p', '1');
  return { u: u >= 0, p: p >= 0 };
}"""
# Marks the card fields of a checkout form in one frame (nothing is read from them but their kind, size and, for an expiry <select>, its
# options). Recognised by autocomplete (cc-number, cc-exp, cc-exp-month, cc-exp-year, cc-csc, cc-name, postal-code), else by the words of
# the field (name, id, placeholder, aria-label, its <label>). A card number split over several boxes is marked number-0, number-1, ...
_TAG_CARD_FIELDS = r"""() => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none' && !e.disabled && !e.readOnly; };
  document.querySelectorAll('[data-kelo-cc]').forEach((e) => e.removeAttribute('data-kelo-cc'));
  const skip = ['hidden', 'checkbox', 'radio', 'submit', 'button', 'image', 'file', 'reset', 'range', 'color', 'date', 'email', 'search', 'url'];
  const els = [...document.querySelectorAll('input, select')].filter((e) => shown(e) && !skip.includes((e.getAttribute('type') || '').toLowerCase()));
  const words = (e) => {
    const parts = [e.name, e.id, e.getAttribute('placeholder'), e.getAttribute('aria-label'), e.getAttribute('title'), e.getAttribute('data-testid'),
                   e.getAttribute('data-elements-stable-field-name')];
    if (e.labels) for (const l of e.labels) parts.push(l.innerText);
    const by = e.getAttribute('aria-labelledby');
    if (by) by.split(/\s+/).forEach((id) => { const x = document.getElementById(id); if (x) parts.push(x.innerText); });
    if (!(e.labels && e.labels.length) && !e.getAttribute('placeholder') && !e.getAttribute('aria-label') && e.parentElement) {
      const near = (e.parentElement.innerText || '').trim();   // a caption written next to the box, without a <label>
      if (near.length <= 40) parts.push(near);
    }
    return parts.filter(Boolean).join(' ').toLowerCase();
  };
  const ac = (e) => (e.getAttribute('autocomplete') || '').toLowerCase();
  const kindOf = (e) => {
    const a = ac(e), w = words(e);
    if (/cc-number/.test(a) || /card.?num|cardnum|cc.?num|credit.?card|card[\s_\-]*no(\d|\b)|\bpan\b|카드\s*번호/.test(w)) return 'number';
    if (/cc-csc/.test(a) || /\bcvc|\bcvv|\bcsc\b|security.?code|card.?code|cvc2|보안\s*코드|cvc|cvv/.test(w)) return 'cvc';
    if (/cc-exp-month/.test(a) || /exp\w*[\s_\-]*(month|mm)\b|\b(month|mm)[\s_\-]*exp|(유효|만료).*월/.test(w)) return 'exp_month';
    if (/cc-exp-year/.test(a) || /exp\w*[\s_\-]*(year|yy)|\b(year|yy)[\s_\-]*exp|(유효|만료).*(년|연도)/.test(w)) return 'exp_year';
    if (/cc-exp/.test(a) || /expir|exp.?date|\bexp\b|mm\s*\/\s*yy|유효\s*기간|만료/.test(w)) return 'exp';
    if (/cc-name/.test(a) || /name.?on.?card|card.?holder|holder.?name|cardholder|카드\s*소유자|명의/.test(w)) return 'name';
    if (/postal-code/.test(a) || /postal|\bzip|post.?code|우편\s*번호/.test(w)) return 'postal';
    return '';
  };
  const found = els.map((e) => [e, kindOf(e)]);
  const card = found.some(([e, k]) => ['number', 'cvc', 'exp', 'exp_month', 'exp_year'].includes(k) && (k === 'number' || k === 'cvc' || /cc-/.test(ac(e))));
  const max = (e) => (e.maxLength && e.maxLength > 0 ? e.maxLength : 0);
  // a number split over boxes: several 'number' fields, or one short one followed by unlabeled short boxes
  let numbers = found.filter(([, k]) => k === 'number').map(([e]) => e);
  if (numbers.length === 1 && max(numbers[0]) && max(numbers[0]) <= 6) {
    const at = els.indexOf(numbers[0]);
    for (let i = at + 1; i < els.length && numbers.length < 4; i++) {
      if (found[i][1] || !max(els[i]) || max(els[i]) > 6 || els[i].tagName === 'SELECT') break;
      numbers.push(els[i]);
    }
  }
  if (numbers.length > 1 && !numbers.every((e) => max(e) && max(e) <= 7)) numbers = numbers.slice(0, 1);   // two whole-number fields: use the first
  const out = { any: false, card, number: [], exp: null, exp_month: null, exp_year: null, cvc: false, name: false, postal: false };
  numbers.forEach((e, i) => { e.setAttribute('data-kelo-cc', 'number-' + i); out.number.push(max(e)); });
  const first = (k) => { const hit = found.find(([e, kk]) => kk === k && !e.hasAttribute('data-kelo-cc')); return hit ? hit[0] : null; };
  if (card) {   // unlabeled expiry boxes next to a card form: month / year by their own words
    for (const [e, k] of found) {
      if (k || e.hasAttribute('data-kelo-cc')) continue;
      const w = words(e);
      if (!first('exp_month') && /\bmm\b|month|월/.test(w)) { found[found.findIndex(([x]) => x === e)][1] = 'exp_month'; continue; }
      if (!first('exp_year') && /\byy(yy)?\b|year|년/.test(w)) { found[found.findIndex(([x]) => x === e)][1] = 'exp_year'; }
    }
  }
  const opts = (e) => (e.tagName === 'SELECT' ? [...e.options].map((o) => [o.value, (o.text || '').trim()]).slice(0, 60) : null);
  for (const k of ['exp', 'exp_month', 'exp_year']) {
    const e = first(k);
    if (!e) continue;
    e.setAttribute('data-kelo-cc', k);
    out[k] = { max: max(e), placeholder: (e.getAttribute('placeholder') || '').toLowerCase(), options: opts(e) };
  }
  const cvc = first('cvc');
  if (cvc) { cvc.setAttribute('data-kelo-cc', 'cvc'); out.cvc = true; }
  const extra = (e) => e && !e.value && (card || /cc-|billing/.test(ac(e)) || /billing|card/.test(words(e)));
  const name = first('name'), postal = first('postal');
  if (extra(name)) { name.setAttribute('data-kelo-cc', 'name'); out.name = true; }
  if (extra(postal)) { postal.setAttribute('data-kelo-cc', 'postal'); out.postal = true; }
  out.any = out.number.length > 0 || !!out.exp || !!out.exp_month || !!out.exp_year || out.cvc;
  if (!out.any) { out.name = out.postal = false; document.querySelectorAll('[data-kelo-cc]').forEach((e) => e.removeAttribute('data-kelo-cc')); }
  return out;
}"""
_UNTAG_CARD_FIELDS = "() => document.querySelectorAll('[data-kelo-cc]').forEach((e) => e.removeAttribute('data-kelo-cc'))"

# The shipping fields of a checkout (browser_fill_shipping): by their autocomplete tokens first, else by their words (label, name, id,
# placeholder) in common languages. Billing-only, login, e-mail, card, coupon and search fields are never taken.
_TAG_ADDRESS_FIELDS = r"""() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const st = getComputedStyle(e);
                       return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const words = (e) => [e.labels && e.labels.length ? Array.from(e.labels).map((l) => l.innerText).join(' ') : '', e.getAttribute('aria-label'),
                        e.getAttribute('placeholder'), e.getAttribute('name'), e.id, e.getAttribute('title')].filter(Boolean).join(' ');
  const AC = {'name': 'name', 'given-name': 'given', 'family-name': 'family', 'tel': 'tel', 'tel-national': 'tel', 'tel-local': 'tel',
              'tel-country-code': 'tel_country', 'postal-code': 'postal', 'address-line1': 'line1', 'address-line2': 'line2',
              'address-line3': 'line2', 'street-address': 'street', 'address-level1': 'level1', 'address-level2': 'level2',
              'address-level3': 'level3', 'country': 'country', 'country-name': 'country'};
  const NOT = /coupon|promo|search|query|\bqty\b|quantity|수량|쿠폰|검색|password|e-?mail|이메일|메일|card|카드|cvc|cvv|user.?name|login|아이디|birth|생년/i;
  const R = [
    ['note', /memo|\bnote|request|instruction|배송\s*메모|요청\s*사항|요청사항|備考|配達.*(指定|メモ)|备注|lieferhinweis|hinweis/i],
    ['tel_country', /country.?code|calling.?code|국가\s*번호|dial.?code/i],
    ['tel', /phone|mobile|\btel|cell|휴대폰|핸드폰|전화|연락처|電話|携帯|电话|手机|telefon|téléphone|teléfono/i],
    ['postal', /zip|postal|post.?code|우편|郵便|〒|邮编|郵遞|\bplz\b|code postal|código postal|\bcap\b/i],
    ['line2', /address.?2|addr.?2|line.?2|상세|detail|\bapt|suite|\bunit\b|building|建物|地址.?2|zusatz|complément|complemento/i],
    ['level1', /\bstate\b|province|region|prefecture|county|시\s*\/?\s*도|都道府県|bundesland|provincia|région/i],
    ['level2', /\bcity\b|\btown\b|locality|시\s*\/?\s*군\s*\/?\s*구|市区町村|城市|\bort\b|\bstadt\b|\bville\b|ciudad|città|cidade|город/i],
    ['country', /\bcountry\b|국가|^国$|país|\bpays\b|\bland\b/i],
    ['family', /last.?name|family.?name|surname|nachname|nom de famille|(^|\s)(姓|セイ)(\s|$)/i],
    ['given', /first.?name|given.?name|vorname|prénom|(^|\s)(名|メイ)(\s|$)/i],
    ['name', /full.?name|\bname\b|recipient|receiver|받는\s*(분|사람)|수령인|수취인|성명|이름|氏名|お名前|姓名|收件人|收貨人|empfänger|destinatario/i],
    ['line1', /address.?1|addr.?1|line.?1|street|address|주소|도로명|住所|番地|地址|straße|strasse|\brue\b|calle|indirizzo|endereço|адрес/i],
  ];
  const found = {};
  let n = 0;
  for (const e of document.querySelectorAll('input, select, textarea')) {
    const type = (e.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'password', 'email', 'search', 'checkbox', 'radio', 'submit', 'button', 'image', 'file', 'reset'].includes(type)) continue;
    if (e.disabled || !vis(e)) continue;
    const ac = (e.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/).filter((x) => x && !x.startsWith('section-'));
    if (ac.includes('billing') || ac.some((x) => x.startsWith('cc-'))) continue;
    let kind = AC[ac.filter((x) => x !== 'shipping').pop() || ''];
    const w = words(e);
    if (!kind) {
      if (NOT.test(w)) continue;
      const hit = R.find(([, re]) => re.test(w));
      kind = hit ? hit[0] : '';
      if (!kind && e.tagName === 'TEXTAREA' && /address|주소|住所|地址/i.test(w)) kind = 'street';
    }
    if (!kind) continue;
    const list = found[kind] = found[kind] || [];
    const tag = `${kind}-${list.length}`;
    e.setAttribute('data-kelo-addr', tag);
    list.push({tag, el: e.tagName.toLowerCase(), max: e.maxLength > 0 ? e.maxLength : 0, ph: e.getAttribute('placeholder') || '',
               ro: !!(e.readOnly), opts: e.tagName === 'SELECT' ? Array.from(e.options).map((o) => [o.value, (o.text || '').trim()]) : []});
    n++;
  }
  return {n, found};
}"""
_SET_READONLY = r"""([tag, value]) => { const e = document.querySelector(`[data-kelo-addr="${tag}"]`); if (!e) return false;
  const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, value);
  e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); return true; }"""
_UNTAG_ADDRESS_FIELDS = "() => document.querySelectorAll('[data-kelo-addr]').forEach((e) => e.removeAttribute('data-kelo-addr'))"


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


# Words of a page that asks the payer to prove it is them (the card's own app, 3-D Secure, ISP, a one-time code). Then the user must act
# on their phone: the assistant says so and waits, instead of clicking around.
_EXTRA_AUTH = ("3-d secure", "3d secure", "verified by visa", "visa secure", "securecode", "identity check", "safekey", "protectbuy",
               "one-time password", "one time password", "authenticate this payment", "authenticate your payment", "approve the payment in",
               "confirm in your bank", "bank app", "verify your card", "안심클릭", "앱카드", "카드사 앱", "카드사앱", "간편인증", "본인인증",
               "공동인증", "인증서", "결제 비밀번호", "결제비밀번호", "인증번호", "ars 인증", "isp 인증", "isp결제", "페이북", "휴대폰 인증")


def extra_auth(text: str) -> str:
    """The words that show the page asks for extra authentication of the payment, or ""."""
    t = (text or "").lower()
    hit = next((w for w in _EXTRA_AUTH if w in t), "")
    if not hit and (m := re.search(r"\b(isp|otp|3ds)\b", t)):
        hit = m.group(1).upper()
    return hit


EXTRA_AUTH_NOTE = ("[The page asks for extra authentication of the payment ({words}). If this payment was approved with confirm_purchase, "
                   "call payment_step_wait now: it shows the user one card with the code, QR code and time left and WAITs for their answer. "
                   "Otherwise tell the user to complete it on their phone (the card company's app, bank app or code) and WAIT for them to "
                   "say it is done. Do not click, retry or fill anything meanwhile; afterwards read the page once with browser_read and report.]")

_UNTAG_LOGIN_FIELDS = "() => document.querySelectorAll('[data-vault-u],[data-vault-p]').forEach((e) => { e.removeAttribute('data-vault-u'); e.removeAttribute('data-vault-p'); })"

# Marks the box(es) of a one-time code on the page: one box (autocomplete=one-time-code, or a code / OTP / verification-number field), or a row of
# one-character boxes (4-8 of them). `sel` (optional): a selector the agent named instead. Nothing is read from the boxes.
_TAG_CODE_FIELDS = r"""(sel) => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none' && !e.disabled && !e.readOnly; };
  document.querySelectorAll('[data-kelo-otp]').forEach((e) => e.removeAttribute('data-kelo-otp'));
  const okType = (e) => ['', 'text', 'tel', 'number', 'password'].includes((e.getAttribute('type') || '').toLowerCase());
  const inputs = [...document.querySelectorAll('input')].filter((e) => shown(e) && okType(e));
  if (sel) {
    let picked = [];
    try { picked = [...document.querySelectorAll(sel)].filter((e) => e.tagName === 'INPUT' && shown(e)); } catch (err) { picked = []; }
    picked.slice(0, 8).forEach((e, i) => e.setAttribute('data-kelo-otp', String(i)));
    return { boxes: picked.length > 8 ? 8 : picked.length };
  }
  const words = (e) => [e.name, e.id, e.getAttribute('placeholder'), e.getAttribute('aria-label'), e.getAttribute('autocomplete')]
    .concat(e.labels ? [...e.labels].map((l) => l.innerText) : []).filter(Boolean).join(' ').toLowerCase();
  const one = inputs.find((e) => (e.getAttribute('autocomplete') || '').toLowerCase() === 'one-time-code')
    || inputs.find((e) => (e.getAttribute('type') || '').toLowerCase() !== 'password' && /otp|one.?time|verif|2fa|mfa|totp|auth.?code|security.?code|\bcode\b|pin\b|token|인증|코드|확인\s*번호|보안\s*번호/.test(words(e)) && !/coupon|promo|zip|postal|우편|쿠폰|card|카드/.test(words(e)));
  const singles = inputs.filter((e) => e.maxLength === 1);
  if (singles.length >= 4 && singles.length <= 8 && (!one || one.maxLength === 1)) {
    singles.forEach((e, i) => e.setAttribute('data-kelo-otp', String(i)));
    return { boxes: singles.length };
  }
  if (one) { one.setAttribute('data-kelo-otp', '0'); return { boxes: 1 }; }
  return { boxes: 0 };
}"""
_UNTAG_CODE_FIELDS = "() => document.querySelectorAll('[data-kelo-otp]').forEach((e) => e.removeAttribute('data-kelo-otp'))"


_CHECK_HEADERS = {"server", "cf-mitigated", "cf-ray", "x-amzn-waf-action", "x-datadome", "x-dd-b", "x-datadome-cid", "x-amz-cf-id", "x-cache"}
BLOCK_LIMIT = 2                   # access-denied answers from one host in one task, then it is not tried again...
BLOCK_SECONDS = 600.0             # ...for this long (a new message of the user starts a new task, and with it a clean slate)
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


def looks_final(selector: str) -> bool:
    """Whether a click target reads like the button that pays, orders, books or otherwise commits (a hint, never a permission)."""
    t = re.sub(r"\s+", " ", (selector or "").lower())
    return any(w in t for w in _FINAL_WORDS)


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
        self.clock = clock
        self.remote_dns: Callable[[], bool] = lambda: False   # the VPN connector is on: names are resolved by the VPN, not here (vpn.py)
        self.vpn_note: Callable[[], Awaitable[str]] | None = None   # -> the text saying the VPN is down, or "" (server.py wires vpn.down_note)
        self._lock = asyncio.Lock()
        self._purchase: dict[str, tuple[str, float, int]] = {}   # device -> (host, until, clicks left) after an allowed purchase
        self._cancel: dict[str, tuple[str, float, int, str]] = {}   # device -> (host, until, clicks left, order no) to cancel a wrong charge
        self.on_cancel_click: Callable[[str, str, str], None] | None = None   # (host, the click's words, order no): logged by the server
        self._last_card: dict[str, float] = {}                   # device -> when its last browser card went out
        self._dialogs: dict[str, list[str]] = {}                 # device -> pop-up messages since the last page reading
        self._status: dict[str, tuple[str, float]] = {}          # device -> (last status line of its card, wall time)
        self._seen_at: dict[str, float] = {}                     # device -> wall time its tab was last used
        self._control: dict[str, str] = {}                       # tab -> the device that took control of it (absent = the agent's)
        self._handed_back: dict[str, asyncio.Event] = {}         # tab -> set whenever the agent may act on it
        self.control_listeners: list[Callable[[str, str | None], Any]] = []   # (tab, device or None) on every change
        self.control_wait = CONTROL_WAIT_SECONDS
        # ---- S6: what the pages are, and what the browser is set up with (see the notes at each method)
        self._doc: dict[str, tuple[int, dict[str, str]]] = {}   # tab -> (status, headers) of its main document's last response
        self._notes: dict[str, list[str]] = {}                   # tab -> lines for its next page reading (e.g. what the user did by hand)
        self._view_cdp: dict[str, Any] = {}                      # tab -> the DevTools session holding its phone view (absent = desktop)
        self._site_locks: dict[str, asyncio.Lock] = {}           # site tab -> one request at a time (site_fetch)
        self._site_views: dict[str, str] = {}                    # site -> the view that last worked there ("desktop" | "mobile")
        self._checks: dict[str, tuple[str, float]] = {}          # tab -> (bot check vendor, when it was announced)
        self.check_listeners: list[Callable[[str, Challenge, str], Any]] = []   # (tab, check, host) when a check is first seen
        self._private: dict[str, Any] = {}                       # tab -> its own private context (nothing kept after it closes)
        self._type_cdp: dict[str, tuple[Any, Any]] = {}          # tab -> (page, the DevTools session) that types Korean (_ime_sender)
        self._tab_cdp: dict[str, Any] = {}                       # tab -> the CDP session that keeps its time zone / locale override
        self._targets: dict[str, tuple[str, str]] = {}           # tab -> (target id = main frame id, browser context id)
        self._site_states: dict[str, tuple[dict[str, Any], float]] = {}   # host -> (cookies + storage, saved at): memory only
        self._dl_cdp: Any = None                                 # the browser-wide CDP session that hears downloads
        self._dl_contexts: set[str] = set()                      # browser contexts whose downloads go to the download area
        self.downloads: DownloadArea | None = None               # set by the app when the download folder is shared with the server
        self.timezone: Callable[[], str] = lambda: ""            # the user's time zone (profile), applied to every tab
        self.locale = ""                                         # e.g. "ko-KR": the language the browser was started with
        self.prepare_pages = connect is None                     # set up time zone / downloads on new tabs (off for the plain test fakes)
        self.journal: Any = None                                 # takeover.TakeoverJournal: what the user does while holding a tab
        self.has_mailbox: Callable[[], bool] = lambda: False     # a mailbox is connected (a code prompt then points to find_login_code)
        self.sign_ins: dict[str, dict[str, Any]] = {}            # tab -> the sign-in the agent started there: {"site", "at", "task"}
        self.auto_code: Callable[[str], Awaitable[str | None]] = _no_auto_code   # types a code from the site's own mail (browser_more)
        self._judged: dict[str, tuple[str, str, bool]] = {}      # device -> (selector, commit kind, password field) of its last judged step
        self._carried: dict[str, tuple[str, str, str, float]] = {}   # device -> a step judge_ahead judged for the next judge() of it
        self._judged_info: dict[str, dict[str, Any] | None] = {}  # device -> what was read of that step's element (commits.TARGET_JS)
        # device -> {(page, label): commit kind}: the first judgement of an element stays its judgement on that page (a retry with
        # another selector for the same button gets the same card words)
        self._kinds_seen: dict[str, dict[tuple[str, str], str]] = {}
        # device -> (page, label, monotonic deadline, tries left) of a step the user allowed whose click then failed: the same element
        # tried again with another selector does not ask again
        self._allowed_failed: dict[str, tuple[str, str, float, int]] = {}
        self.order_dialog: Callable[[str, str, str], bool] | None = None   # (tab, host, message): a pop-up an open order may accept
        self._wake: dict[str, asyncio.Event] = {}                # tab -> set when it is handed back (a waiting human check looks again)
        self._options: dict[str, set[str]] = {}                  # tab -> option selectors marked on its page (data-kelo-opt)
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
        # tab -> the pages under the one it shows now, each with its phone-view and tab DevTools sessions: a window a page opened
        # (window.open, target=_blank) becomes the tab's page; when it closes, the tab goes back to the page that opened it
        self._openers: dict[str, list[tuple[Any, Any, Any]]] = {}
        self._first_phone = False                                # a new window's first request just went out as a phone
        self._mobile_id: dict[str, dict[str, Any]] = {}          # tab -> the phone identity its view sends (for its new windows)
        self._phone_ready: "weakref.WeakSet[Any]" = weakref.WeakSet()   # windows whose phone overrides are on
        self._phone_asked: "weakref.WeakSet[Any]" = weakref.WeakSet()   # windows whose first requests went out as a phone
        self._popup_setup: dict[str, asyncio.Future] = {}      # tab -> the setup of the window it just moved into (watch, view)
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
        self.pacer = SitePacer(clock)                            # per-site gaps between submits and navigations (pacing.py)
        self.sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep   # waits of the pacer (tests: a fake clock's)
        self._typed: dict[str, bool] = {}                        # tab -> text was typed since its last submit or navigation
        self._stopped: set[Any] = set()                          # steps cancelled because the user took the tab over
        self._paused: dict[str, int] = {}                        # tab -> hand-overs open now (the step clock stands still)
        self._rescues: dict[str, asyncio.Future] = {}            # tab -> the clean-up after the user took the tab over
        self._background: set[asyncio.Future] = set()            # clean-ups no step waits for (a route that does not come off)

    # ------------------------------------------------------------------ the user taking a tab over
    def controlled_by(self, key: str) -> str | None:
        """The device holding this tab, or None when the agent may use it."""
        return self._control.get(key)

    def controls(self) -> dict[str, str]:
        """Every tab a device holds now: {tab: device}."""
        return dict(self._control)

    def take_control(self, key: str, device: str) -> None:
        if self._control.get(key) == device:
            return
        self._control[key] = device
        self._handed_back.setdefault(key, asyncio.Event()).clear()
        self.interrupt(key)
        if self.journal is not None and (page := self.raw_page(key)) is not None:
            try:
                self.journal.start(key, page)      # what the user does by hand is written down for the agent (takeover.py)
            except Exception:  # noqa: BLE001 - the journal never stops the user from taking over
                pass
        self._tell(key, device)

    async def release_control(self, key: str) -> bool:
        """Hand the tab back to the agent. The purchase window of that tab is closed (the user's own clicks never count towards it),
        and a page the agent would not be allowed to open is left for about:blank. False when nobody held it."""
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
        if self.journal is not None:
            try:
                summary = self.journal.stop(key)
            except Exception:  # noqa: BLE001
                summary = ""
            if summary:
                self.note(key, summary)            # the agent reads it with its next look at the page
        self._tell(key, None)   # first, so whoever listens hears it before a waiting agent step moves on
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
            tool.deadline = OPEN_SECONDS if tool.name in ("browser_open", "browser_view") else STEP_SECONDS
            tool.limiter = self.run_step
            tool.on_timeout = lambda _args: self.rescue()
            tool.timeout_text = self.timeout_text
        return tools

    def step_running(self, key: str) -> bool:
        return key in self._steps and not self._steps[key].done()

    def interrupt(self, key: str) -> None:
        """The user took this tab over: the agent's step on it is cancelled (ToolStopped, TOOK_OVER_NOTE), and what it left half
        done in the page (a loading navigation, a finger still down) is cleaned up before the user's first input (`settled`)."""
        task = self._steps.get(key)
        if task is not None and not task.done():
            self._stopped.add(task)
            task.cancel()
        try:
            self._rescues[key] = asyncio.ensure_future(self.rescue(key))
        except RuntimeError:   # no running loop (a plain test): nothing to clean up
            pass

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
        cdp = self._view_cdp.get(key) or self._tab_cdp.get(key)
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

    async def hold_for_user(self, _args: dict[str, Any] | None = None) -> str | None:
        """A tool's `hold`: before an agent step on this device's tab, wait while the user controls it. None = go on; otherwise the
        words the agent gets instead of a result."""
        if await self.wait_for_agent_turn(browser_owner.get()):
            return None
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

    async def _listener_code(self, page: Any) -> str | None:
        """The source of the click-like listeners added with addEventListener on the element TARGET_JS just read (it leaves it in
        window.__keloJudgeTarget), read through DevTools (DOMDebugger.getEventListeners). None when they cannot be read.
        `page` may be a frame inside the page (one in another process has a DevTools session of its own)."""
        if hasattr(page, "main_frame"):
            cdp = await self.cdp_session(page)
        elif self._cdp is not None:
            return None
        else:
            cdp = await page.page.context.new_cdp_session(page)
        try:
            got = await cdp.send("Runtime.evaluate", {"expression": "window.__keloJudgeTarget", "objectGroup": "kelo-judge"})
            obj = (got or {}).get("result", {}).get("objectId")
            if not obj:
                return None
            ls = await cdp.send("DOMDebugger.getEventListeners", {"objectId": obj})
            parts: list[str] = []
            for li in (ls or {}).get("listeners", [])[:20]:
                if li.get("type") not in _CLICK_EVENTS:
                    continue
                fn = (li.get("handler") or {}).get("objectId")
                if not fn:
                    continue
                src = await cdp.send("Runtime.callFunctionOn", {
                    "objectId": fn, "returnByValue": True,
                    "functionDeclaration": "function(){try{return Function.prototype.toString.call(this).slice(0,4000)}catch(e){return ''}}"})
                parts.append(str((src or {}).get("result", {}).get("value") or ""))
            return "\n;\n".join(p for p in parts if p)
        finally:
            try:
                await cdp.send("Runtime.evaluate", {"expression": "delete window.__keloJudgeTarget"})
                await cdp.send("Runtime.releaseObjectGroup", {"objectGroup": "kelo-judge"})
            except Exception:  # noqa: BLE001
                pass
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
            kind = judge_type(info, words, str(args.get("text", "")), bool(args.get("submit", False)))
        else:
            kind = judge_click(info, words, url=self.current_url())
        secret = (bool(info) and (str(info.get("type") or "") == "password" or "password" in str(info.get("autocomplete") or ""))) or (
            info is None and bool(re.search(r"passw|\bpw\b|비밀번호", words, re.I)))
        target = self._target_key(info, selector)
        if kind and target[1]:
            seen = self._kinds_seen.setdefault(browser_owner.get(), {})
            kind = seen.setdefault(target, kind)
            if len(seen) > 200:
                seen.pop(next(iter(seen)))
        self._judged[browser_owner.get()] = (selector, kind, tool == "browser_type" and secret)
        self._judged_info[browser_owner.get()] = info
        return kind

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
        judged = [("unknown" if i is None else judge_click(i, "", url=self.current_url()), i) for i in infos]
        # the weightiest: "unknown" (always asks), then any commit kind, then "" (only looking)
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
        if got.get("structure_read"):
            try:
                # a frame of another process has its own DevTools target; one in the page's process cannot be read this way (None)
                more = await asyncio.wait_for(self._listener_code(page if owner is page else owner), LISTENER_READ_MS / 1000)
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
        """The page's own name for a ref of the last reading ("" for a selector, or a ref without one): what a card shows."""
        return ref_name(self.selector_words(selector)) if ref_of(selector) else ""

    def _target_key(self, info: dict[str, Any] | None, selector: str) -> tuple[str, str]:
        """Which element this is, for "the same button again": the page (without its #fragment) and the element's own words."""
        page = str((info or {}).get("url") or self.current_url()).split("#", 1)[0]
        return page, " ".join(target_label(info, selector).lower().split())

    def note_allowed(self, selector: str) -> tuple[str, str]:
        """A step is about to run (allowed by a card or by a rule): what it is, in case it fails and is tried again."""
        return self._target_key(self.judged_info(selector), selector)

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
        """One click that cancels an order (words like "cancel order" / "주문 취소", never "delete account") inside the open cancel window
        on its site. False = ask the user as usual."""
        if not self.cancel_active() or self.judged(selector) != "delete":
            return False
        words = self.selector_words(selector)
        if not cancel_words(words):
            return False
        h, until, left, no = self._cancel[browser_owner.get()]
        self._cancel[browser_owner.get()] = (h, until, left - 1, no)
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
            page = self._pages.get(key)
            if page is not None and not page.is_closed():
                return page
            if (back := self._back_to_opener(key)) is not None:
                return back
            if self._connect:
                page = await self._private[key].new_page() if key in self._private else await self._connect()
            else:
                await self._ensure_browser()
                if key in self._private:   # this tab browses privately: a fresh page in its own context
                    page = await self._private[key].new_page()
                else:
                    taken = {id(p) for p in self._pages.values()} | {id(o[0]) for st in self._openers.values() for o in st}
                    free = next((p for p in self._ctx.pages if id(p) not in taken and not p.is_closed()), None)
                    page = free or await self._ctx.new_page()
            self._pages[key] = page
            self._view_cdp.pop(key, None)   # a new page starts in the desktop view (open() applies a site's remembered one)
            self._watch(page, key)
            if self.prepare_pages:
                await self._prepare(page, key)
            return page

    def _back_to_opener(self, key: str) -> Any:
        """The window this tab moved into closed: the tab shows the page that opened it again (with its own view), or None."""
        stack = self._openers.get(key) or []
        while stack:
            opener, view_cdp, tab_cdp = stack.pop()
            try:
                alive = not opener.is_closed()
            except Exception:  # noqa: BLE001
                alive = False
            if not alive:
                continue
            self._pages[key] = opener
            self._view_cdp.pop(key, None)
            if view_cdp is not None:
                self._view_cdp[key] = view_cdp
            if tab_cdp is not None:
                self._tab_cdp[key] = tab_cdp
            self._notes.setdefault(key, []).append(POPUP_BACK_NOTE)
            return opener
        self._openers.pop(key, None)
        return None

    def _on_popup(self, key: str, opener: Any, popup: Any) -> None:
        """A page of this tab opened a window: the tab goes on in it (the user's live view follows), in the tab's view."""
        if self._pages.get(key) is not opener:
            return   # a window opened by a page the tab already left
        mobile = key in self._view_cdp
        if mobile and self._first_phone:   # its first request went out as a phone (_phone_windows)
            self._phone_asked.add(popup)
        self._first_phone = False
        self._openers.setdefault(key, []).append((opener, self._view_cdp.pop(key, None), self._tab_cdp.pop(key, None)))
        del self._openers[key][:-5]
        self._pages[key] = popup
        self._popup_setup[key] = asyncio.ensure_future(self._adopt(key, popup, mobile))

    async def _adopt(self, key: str, popup: Any, mobile: bool) -> None:
        """Set up a window the tab moved into like a tab of its own: pop-ups answered, time zone and downloads, and the phone view
        when the tab was in it (the window's first load was made before it could be set: it is loaded again in that view)."""
        self._watch(popup, key)
        if self.prepare_pages:
            try:
                await self._prepare(popup, key)
            except Exception:  # noqa: BLE001 - the window works without them
                pass
        if not mobile:
            return
        try:
            cdp = await self.cdp_session(popup)
            self._mobile_id[key] = await self._phone(cdp, popup)
            self._view_cdp[key] = cdp
            self._phone_ready.add(popup)
            try:
                await popup.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception:  # noqa: BLE001
                pass
            # its first request already asked as a phone (_phone_windows): no second load (a form's window is never sent twice);
            # otherwise it is loaded again in the phone view
            if popup not in self._phone_asked and str(popup.url or "").startswith(("http://", "https://")):
                await popup.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - it stays in the desktop view
            pass

    async def _ensure_browser(self) -> None:
        """Connect to the browser container (first use, or after it restarted). Everything tied to the old connection is forgotten."""
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()
        if self._ctx is None or not self._browser.is_connected():
            # Chrome's DevTools HTTP endpoint rejects Host headers that are not an IP or localhost.
            ip = (await asyncio.to_thread(socket.gethostbyname, self.cdp_host))
            self._browser = await self._pw.chromium.connect_over_cdp(f"http://{ip}:{self.cdp_port}")
            self._ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
            self._pages.clear()
            for d in (self._private, self._tab_cdp, self._type_cdp, self._targets, self._doc, self._ref_words, self._look_url, self._shot_scale):
                d.clear()
            self._dl_cdp, self._dl_contexts = None, set()

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
        for method, params in (("Emulation.setTimezoneOverride", {"timezoneId": tz} if tz else None),
                               ("Emulation.setLocaleOverride", {"locale": self.locale} if self.locale else None)):
            if params:
                try:
                    await cdp.send(method, params)
                except Exception:  # noqa: BLE001 - an unknown zone name: the browser's own stays
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
        await self._route_downloads(key)

    # ------------------------------------------------------------------ downloads (downloads.py)
    async def _route_downloads(self, key: str) -> None:
        if self.downloads is None:
            return
        _, context = self._targets.get(key, ("", ""))
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
        params: dict[str, Any] = {"guid": d.guid}
        if d.context and d.key in self._private:
            params["browserContextId"] = d.context
        try:
            await self._dl_cdp.send("Browser.cancelDownload", params)
        except Exception:  # noqa: BLE001 - it finished or went away meanwhile
            pass

    def _watch(self, page: Any, key: str) -> None:
        """A JavaScript pop-up (alert / confirm) is answered here: accepted only inside a purchase the user allowed on that site,
        dismissed otherwise (what the browser would do with nobody there). Its words go into the next page reading."""
        if not hasattr(page, "on"):
            return

        async def on_dialog(dialog: Any) -> None:
            host = (urlsplit(str(getattr(page, "url", "") or "")).hostname or "").lower()
            token = browser_owner.set(key)
            try:
                by_user = key in self._control   # the user is at the controls: a pop-up their tap caused is theirs to accept
                accept = by_user or self.purchase_active(host) or self.cancel_active(host)
                in_order = not accept and self.order_dialog is not None and self.order_dialog(key, host, str(getattr(dialog, "message", "") or ""))
                accept = accept or in_order
            finally:
                browser_owner.reset(token)
            msgs = self._dialogs.setdefault(key, [])
            how = ("accepted (the user was controlling the browser)" if by_user else "accepted (a step of the order the user asked for)"
                   if in_order else "accepted (inside the purchase the user allowed)" if accept else "dismissed")
            msgs.append(f"{str(getattr(dialog, 'message', '') or '')[:300]!r} -> {how}")
            del msgs[:-5]
            try:
                await (dialog.accept() if accept else dialog.dismiss())
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
        await self.keep_view(browser_owner.get())
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
        popups = self._dialogs.pop(key, [])
        # the page's own words (title, text, pop-ups) are marked as external data: the loop fences them, and keeps these lines of
        # ours (address, notes, hand-over) outside (agent/fence.py)
        said = "".join(f"[the page showed a pop-up: {data(m)}]\n" for m in popups)
        said += "".join(f"{n}\n" for n in self._notes.pop(key, []))
        said += "".join(f"{n}\n" for n in layers)
        if spots := masked_values(text):
            said += masked_note(spots, data) + "\n"
        said += await self._lists_note(page)
        check, otp = await self._look(page, key)
        if check is None and (why := self._denied(key, page, title, text)):
            host = urlsplit(str(page.url or "")).hostname or ""
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
        if self.in_checkout() or money_page(None, url):
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
            pick = next((f for f in found if f["close"] is not None and signature(f) not in tried), None)
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
            sel = f'[data-kelo-close="{pick["close"]}"]'
            try:
                await page.click(sel, timeout=OVERLAY_CLICK_MS)
            except Exception:  # noqa: BLE001 - covered or moving: a plain JavaScript click on the same control
                try:
                    await page.evaluate(click_js(pick["close"]))
                except Exception:  # noqa: BLE001
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
        return why

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

    async def _covered(self, page: Any, owner: Any, selector: str, key: str) -> str:
        """Before a click or typing step: "" when the element can be hit, else the words of the layer that covers it once the
        layer could not be closed (a window over the page: the step would only wait for a timeout). A covering layer is closed first
        (page_notes.OVERLAY_JS, once) and the element looked at again."""
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
        try:
            if boxes:
                try:
                    await page.evaluate(LOOK_OVERLAY_JS, boxes)
                except Exception:  # noqa: BLE001 - a page that refuses it: the picture without labels
                    boxes = []
            jpeg, w, h = await self.screenshot(key, LOOK_WIDTH, LOOK_BYTES)
        finally:
            await self._quietly(lambda: page.evaluate(LOOK_CLEAR_JS))
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

    def sign_in_continue(self, selector: str) -> bool:
        """The click only goes on with the owner's own sign-in on this tab: browser_login ran here a short while ago, the page is
        still a sign-in step (signin_state.still_signing_in) on the same site as that sign-in (its OAuth pages too: the same
        registrable domain), and the element's words, as read on the page, only go on ("Continue", "Next", "OK", "Sign in"). Consent
        ("Allow", "Agree"), sign-up and settings words are not this and keep their card (2026-11-10)."""
        key = browser_owner.get()
        info = self.judged_info(selector)
        probe = self._probes.get(key)
        if info is None or not self._signing_in(key) or not (isinstance(probe, dict) and probe.get("kelo_check")):
            return False
        site = str((self.sign_ins.get(key) or {}).get("site") or "")
        if not site or registrable(self.current_host()) != site or not still_signing_in(probe):
            return False
        if str(info.get("type") or "").lower() == "checkbox" or str(info.get("role") or "") in ("checkbox", "switch") \
                or selector in self._options.get(key, ()):
            return True   # ticking a box of the sign-in page sends nothing ("stay signed in", "don't ask again on this browser")
        return continue_only(label_of(info, selector))

    def sign_in_remember(self, selector: str) -> bool:
        """The click keeps the owner signed in on Kelo's own browser: browser_login ran on this tab a short while ago, the page is on the
        same site as that sign-in, its reading is from Kelo's check and asks whether to remember this device or stay signed in (a
        device-registration page may sit on a non-login path, so it need not still look like a sign-in), and the element's words
        choose to keep it (commits.keep_signed_in). The negative choice is not this: it keeps today's rules (2026-11-15)."""
        key = browser_owner.get()
        info = self.judged_info(selector)
        probe = self._probes.get(key)
        if info is None or not self._signing_in(key) or not (isinstance(probe, dict) and probe.get("kelo_check")):
            return False
        site = str((self.sign_ins.get(key) or {}).get("site") or "")
        if not site or registrable(self.current_host()) != site:
            return False
        page = f"{probe.get('title') or ''} {probe.get('text') or ''}"
        return asks_to_keep_signed_in(page) and keep_signed_in(label_of(info, selector), page)

    def _signing_in(self, key: str) -> bool:
        """The agent started a sign-in on this tab a short while ago (browser_login)."""
        got = self.sign_ins.get(key) or {}
        try:
            return time.time() - float(got.get("at") or 0) < SIGN_IN_SECONDS
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
            return options_note(v, host, "\n".join(f"- {sel}: {data(label)}" for sel, label in options)), False
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
            if still_signing_in(probe):
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
        found = [(f'[data-kelo-opt="{o["id"]}"]', " ".join(str(o.get("label") or "").split())[:120]) for o in (got or [])[:8]
                 if isinstance(o, dict) and o.get("id")]
        self._options[browser_owner.get()] = {sel for sel, _ in found}
        return found

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
                now = verification_step(probe, signing_in=True)
                same = now is not None and now.step == step.step
                signed = not same and not still_signing_in(probe)
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

    async def _look(self, page: Any, key: str) -> tuple[Challenge | None, int]:
        """Read the page's structure once: a bot check (challenge.detect) and how many one-time-code boxes it shows."""
        found, probe = await self._read_check(page, key)
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

    async def open(self, url: str, view: str = "") -> str:
        await self._guard(url)
        if said := self.blocked_site(url):
            return said
        page = await self.page()
        host = urlsplit(url).hostname or ""
        if note := self._pace_note(host, "nav", browser_owner.get()):
            return note
        await self._pace_wait(host, "nav", browser_owner.get())
        # opening an address starts a new flow: the clicks that finish an allowed purchase are those that follow from its page
        self.end_purchase()
        site = _site_of(url)
        want = view if view in VIEWS else self._site_views.get(site, "")
        switched = ""
        if want and want != self.view():
            await self.set_view(want)
            switched = want
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            if (note := await self._vpn_down(e)):
                raise BrowserPolicyError(note) from None
            raise
        status = getattr(resp, "status", None)
        if view in VIEWS and (status is None or status < 400):
            self.remember_view(site, view)
        try:  # a redirect may have landed somewhere we would not have allowed directly
            await self._guard(page.url)
        except BrowserPolicyError:
            await page.goto("about:blank")
            raise
        await self._settle(page)
        await self._human_pause(page, human.dwell)
        self._own_layers.pop(browser_owner.get(), None)   # opened (again): its windows are the site's, not the agent's
        text = await self.snapshot(clear=True)
        if switched and not view:
            text = VIEW_REMEMBERED_NOTE.format(view=switched) + "\n" + text
        return f"{not_found_note(status)}\n{text}" if status in (404, 410) else text

    # ------------------------------------------------------------------ desktop or phone view of a tab
    def view(self, key: str | None = None) -> str:
        """The view this tab shows sites: "mobile" (a phone's browser) or "desktop"."""
        return "mobile" if (key or browser_owner.get()) in self._view_cdp else "desktop"

    def remember_view(self, site: str, view: str) -> None:
        """The view that worked on this site, used again when the agent opens it later (this task and the next ones)."""
        if not site or view not in VIEWS:
            return
        self._site_views.pop(site, None)
        self._site_views[site] = view
        while len(self._site_views) > SITE_VIEWS_KEPT:
            self._site_views.pop(next(iter(self._site_views)))

    async def set_view(self, view: str) -> bool:
        """Show sites in this tab as a phone's browser ("mobile") or a desktop one ("desktop"), keeping its sign-ins, cookies and
        storage (the same browser context). The phone view is one DevTools session holding Emulation overrides for the tab: the
        User-Agent with its client hints (Android, mobile, the same Chrome version and brands), a phone's viewport and pixel ratio,
        touch. Going back to desktop detaches that session, which drops exactly those overrides (the browser's own identity stays).
        True when the view changed; the page is not reloaded here."""
        if view not in VIEWS:
            raise ValueError(f"view must be one of {', '.join(VIEWS)}")
        key = browser_owner.get()
        if view == self.view(key):
            return False
        page = await self.page()
        if view == "desktop":
            cdp = self._view_cdp.pop(key, None)
            try:
                await cdp.send("Emulation.clearDeviceMetricsOverride", {})
                await cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": False})
            except Exception:  # noqa: BLE001 - detaching drops them anyway
                pass
            try:
                await cdp.detach()
            except Exception:  # noqa: BLE001
                pass
            return True
        cdp = await self.cdp_session(page)
        self._mobile_id[key] = await self._phone(cdp, page)
        self._view_cdp[key] = cdp
        return True

    @staticmethod
    async def _phone(cdp: Any, page: Any) -> dict[str, Any]:
        """The phone view's Emulation overrides on one page's DevTools session (set_view; a window the tab moved into)."""
        full = ""
        try:
            full = str((await cdp.send("Browser.getVersion", {})).get("product") or "").split("/")[-1]
        except Exception:  # noqa: BLE001 - read the version from the page instead
            pass
        if not full[:1].isdigit():
            ua = str(await page.evaluate("() => navigator.userAgent"))
            m = re.search(r"Chrome/([\d.]+)", ua)
            full = m.group(1) if m else "130.0.0.0"
        ident = mobile_identity(full)
        await cdp.send("Emulation.setUserAgentOverride", ident)
        await cdp.send("Emulation.setDeviceMetricsOverride", dict(PHONE))
        await cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        return ident

    async def keep_view(self, key: str) -> None:
        """The phone view's screen again (a Playwright screenshot of a window-sized page resets the device metrics it finds)."""
        cdp = self._view_cdp.get(key)
        if cdp is not None:
            try:
                await cdp_send(cdp, "Emulation.setDeviceMetricsOverride", dict(PHONE))
            except Exception:  # noqa: BLE001 - the page went away: page() starts the next one in the desktop view
                pass

    async def shoot(self, page: Any, key: str | None = None, *, type: str = "jpeg", quality: int | None = None,
                    clip: dict[str, float] | None = None, css_scale: bool = False, timeout: int = 5_000) -> bytes:
        """A picture of the tab's viewport (or `clip` of it). In the phone view it is taken through the view's own DevTools session,
        so the phone screen stays as it is (Playwright's page.screenshot resets the device metrics of a window-sized page)."""
        key = browser_owner.get() if key is None else key
        cdp = self._view_cdp.get(key)
        if cdp is None:
            kw: dict[str, Any] = {"type": type, "timeout": timeout}
            if quality is not None and type == "jpeg":
                kw["quality"] = quality
            if clip is not None:
                kw["clip"] = clip
            if css_scale:
                kw["scale"] = "css"
            return await page.screenshot(**kw)
        params: dict[str, Any] = {"format": type}
        if quality is not None and type == "jpeg":
            params["quality"] = quality
        if clip is not None:
            params["clip"] = {**{k: float(clip[k]) for k in ("x", "y", "width", "height")},
                              "scale": 1.0 / PHONE["deviceScaleFactor"] if css_scale else 1.0}
        got = await asyncio.wait_for(cdp.send("Page.captureScreenshot", params), timeout / 1000)
        return base64.b64decode(got["data"])

    async def switch_view(self, view: str) -> str:
        """browser_view: switch this tab's view and load the page again in it (same address, same sign-ins)."""
        changed = await self.set_view(view)
        page = await self.page()
        url = str(getattr(page, "url", "") or "")
        status = None
        if url.startswith(("http://", "https://")):
            await self._guard(url)
            resp = await page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            try:
                await self._guard(page.url)
            except BrowserPolicyError:
                await page.goto("about:blank")
                raise
            await self._settle(page)
            status = getattr(resp, "status", None)
            if status is None or status < 400:
                self.remember_view(_site_of(page.url), view)
        text = await self.snapshot(clear=True)
        head = VIEW_NOTE.format(view=view) if changed else VIEW_SAME_NOTE.format(view=view)
        return f"{head}\n{not_found_note(status)}\n{text}" if status in (404, 410) else f"{head}\n{text}"

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

    async def click(self, selector: str) -> str:
        page = await self.page()
        key = browser_owner.get()
        before = str(getattr(page, "url", "") or "")
        if said := self.blocked_site(before):   # a click on the page of a site that already blocked us twice: nothing is sent to it
            return said
        self._unchanged.pop(key, None)
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
        dialogs = len(self._dialogs.get(key, []))
        release = await self._phone_windows(key, page)   # a window this click opens asks for the phone page from its first request
        try:
            # in the phone view a click is a tap (a touch), as on a phone: a phone site may listen to touch only (2026-11-10)
            tapped = key in self._view_cdp and await self._tap(owner, selector, key)
            if not tapped:
                await human.click(owner, selector, timeout_ms=10_000, fallback=lambda ms: owner.click(selector, timeout=ms))
            await self._loaded(page, key)
            moved = await self._moved_to_window(key, page)
            if (tapped and moved is None and len(self._dialogs.get(key, [])) == dialogs
                    and await self._fingerprint(page) == mark):   # the tap changed nothing: one mouse click, for a mouse-only page
                await human.click(owner, selector, timeout_ms=10_000, fallback=lambda ms: owner.click(selector, timeout=ms))
                await self._loaded(page, key)
                moved = await self._moved_to_window(key, page)
        finally:
            if release is not None:
                release()   # never awaited: removing the route waits for a navigation that may never answer
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
        await self._human_pause(page, human.dwell if str(getattr(page, "url", "") or "") != before else human.glance)
        if moved is not None:
            self._notes.setdefault(key, []).insert(0, POPUP_NOTE.format(url=str(page.url or "")[:200]))
        elif len(self._dialogs.get(key, [])) == dialogs and await self._fingerprint(page) == mark:
            self._unchanged[key] = True
            self._notes.setdefault(key, []).insert(0, NO_CHANGE_NOTE)
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

    async def _tap(self, owner: Any, selector: str, key: str) -> bool:
        """A touch at the middle of the element (Input.dispatchTouchEvent on the phone view's session; Chrome makes the click of it,
        as on a phone). False when it could not be done (then the mouse clicks)."""
        cdp = self._view_cdp.get(key)
        down = False
        shown = human._mouse_page(owner)   # None: no page mouse to show
        try:
            loc = owner.locator(selector).first
            await loc.scroll_into_view_if_needed(timeout=10_000)
            box = await loc.bounding_box(timeout=5_000)
            if cdp is None or not box or box["width"] <= 0 or box["height"] <= 0:
                return False
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            point = {"x": x, "y": y, "radiusX": 4, "radiusY": 4, "force": 1, "id": 1}
            await cdp_send(cdp, "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
            down = True
            human.set_pressed(shown, True, (x, y))   # the live view draws the finger (live.py PointerThrottle)
            await asyncio.sleep(0.06 + 0.06 * random.random())   # a finger rests on the screen a moment
            await cdp_send(cdp, "Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            return True
        except Exception:  # noqa: BLE001 - not reachable this way (or the page does not answer): the mouse clicks
            if down and cdp is not None:   # lift the finger so it cannot swallow the next touch
                await self._quietly(lambda: cdp_send(cdp, "Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []}, RESCUE_SECONDS))
            return False
        finally:
            if down:
                human.set_pressed(shown, False)

    async def _phone_windows(self, key: str, page: Any) -> Callable[[], None] | None:
        """While a click on a phone-view tab may open a window (a pop-up, a target=_blank link): the requests of a new window opened by
        `page` go out with the phone identity (User-Agent, Sec-CH-UA-Mobile ?1, Sec-CH-UA-Platform Android) until _adopt has put the
        phone view's overrides on it, so its FIRST request already asks for the phone page (2026-11-10: a "바로구매" window came in the
        PC version; the overrides attach only after the window exists). A context route, only for the length of the click (a route
        turns the HTTP cache off). Returns the function that lets it go, or None (desktop view, or no context to route in). Letting go never
        waits: the route stops doing anything at once (it passes every request on) and is removed in the background, because removing
        it does not return while a navigation of this click has no answer (the step would hang with it)."""
        live = {"on": True}
        ident = self._mobile_id.get(key)
        ctx = getattr(page, "context", None)
        if key not in self._view_cdp or ident is None or ctx is None or not hasattr(ctx, "route"):
            return None
        ua = str(ident.get("userAgent") or "")

        async def handler(route: Any, request: Any) -> None:
            if not live["on"]:
                try:
                    await route.fallback()
                except Exception:  # noqa: BLE001
                    pass
                return
            phone = {**request.headers, "user-agent": ua, "sec-ch-ua-mobile": "?1", "sec-ch-ua-platform": '"Android"'}
            headers = None
            try:
                pg = request.frame.page
            except Exception:  # noqa: BLE001 - no frame yet: the first request of a window being opened (by this click)
                pg = None
                try:
                    headers = phone if request.is_navigation_request() else None
                    self._first_phone = True
                except Exception:  # noqa: BLE001
                    headers = None
            try:
                if pg is not None and pg is not page and pg not in self._phone_ready and await pg.opener() is page:
                    headers = phone
                    self._phone_asked.add(pg)
            except Exception:  # noqa: BLE001 - a request without a page (a worker): as it is
                headers = None
            try:
                await (route.fallback(headers=headers) if headers else route.fallback())
            except Exception:  # noqa: BLE001 - the request was already handled or the page closed
                pass
        try:
            await asyncio.wait_for(ctx.route("**/*", handler), RESCUE_SECONDS)
        except Exception:  # noqa: BLE001 - no route: the window is loaded again in the phone view (_adopt)
            return None

        async def unroute() -> None:
            try:
                await asyncio.wait_for(ctx.unroute("**/*", handler), 120.0)
            except Exception:  # noqa: BLE001
                pass

        def release() -> None:
            live["on"] = False
            self._later(unroute())
        return release

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
        while self._pages.get(key) is page and (loop.time() < until or (self._first_phone and loop.time() < until + NAV_TIMEOUT_MS / 1000)):
            await asyncio.sleep(0.05)   # (a window whose first request went out (_phone_windows) is waited for until it shows up)
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
        dialogs = len(self._dialogs.get(key, []))
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
        await self._human_pause(page, human.dwell if str(getattr(page, "url", "") or "") != before else human.glance)
        if moved is not None:
            self._notes.setdefault(key, []).insert(0, POPUP_NOTE.format(url=str(page.url or "")[:200]))
        elif len(self._dialogs.get(key, [])) == dialogs and await self._fingerprint(page) == mark:
            self._unchanged[key] = True
            self._notes.setdefault(key, []).insert(0, NO_CHANGE_NOTE)
        return await self.snapshot(changes=True)

    async def _ime_sender(self, page: Any) -> Callable[[str, dict[str, Any]], Awaitable[Any]] | None:
        """send(method, params) on the tab's DevTools session, each call bounded (cdp_send), for typing Korean through the input-method
        path (humanize.type_text). The phone view's own session when there is one; else a session kept per tab. None when there is none
        to be had (the text is then typed as before)."""
        key = browser_owner.get()
        cdp = self._view_cdp.get(key) or self._tab_cdp.get(key)
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
                await self._human_pause(page, human.dwell)
        return await self.snapshot(changes=True)

    settle_seconds = 0.8     # how long a page gets to show its answer after Enter (a sign-in error often appears without a page load)
    sign_in_wait = 12.0      # how long a sign-in is watched for a clear answer (a site that redirects through a few pages takes seconds)

    async def fill_login(self, username: str, password: str, *, step: str = "both", submit: bool = True) -> SignIn:
        """Type a saved login into the visible sign-in form of the current page, and (optionally) press Enter.
        The values go straight from here to the page: the answer is the page afterwards with both values removed from it,
        and a reading of whether the site accepted the login."""
        page = await self.page()
        await self._guard(page.url)
        before = page.url
        found = await page.evaluate(_TAG_LOGIN_FIELDS)
        want_user, want_pass = step in ("both", "username"), step in ("both", "password")
        if not ((want_user and found["u"]) or (want_pass and found["p"])):
            await page.evaluate(_UNTAG_LOGIN_FIELDS)
            raise BrowserPolicyError("no sign-in form with the expected field was found on this page")
        try:
            if want_user and found["u"]:
                user_box = page.locator("[data-vault-u]")
                await human.type_text(page, username, locator=user_box, secret=True, timeout_ms=10_000,
                                      fallback=lambda ms: user_box.fill(username, timeout=ms))
            if want_pass and found["p"]:
                pass_box = page.locator("[data-vault-p]")
                await human.type_text(page, password, locator=pass_box, secret=True, timeout_ms=10_000,
                                      fallback=lambda ms: pass_box.fill(password, timeout=ms))
            if submit:
                target = "[data-vault-p]" if (want_pass and found["p"]) else "[data-vault-u]"
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
        finally:
            try:
                await page.evaluate(_UNTAG_LOGIN_FIELDS)
            except Exception:  # noqa: BLE001 - the page may have navigated away
                pass
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
                outcome, _ = classify_sign_in(before_url=before, after_url=page.url, password_visible=bool(state["pw"]), text=str(state["text"]))
                if outcome != "unknown" or waited >= self.sign_in_wait:
                    break
                await asyncio.sleep(0.5)
                waited += 0.5
            found, _ = await self._look(page, browser_owner.get())
            outcome, detail = classify_sign_in(before_url=before, after_url=page.url, password_visible=bool(state["pw"]), text=str(state["text"]),
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
                               if not still_signing_in(probe) else
                               ("unknown", "the user did the verification step, but the sign-in is NOT finished: the page below shows the "
                                           "next step") if handed == "progressed" else
                               ("code_needed", "the sign-in verification is still pending: the page still shows a sign-in or verification step"))
        elif outcome == "success" and isinstance(probe, dict) and probe.get("kelo_check") and still_signing_in(probe):
            outcome, detail = "unknown", "the sign-in form was left, but the page still shows a sign-in or verification step"
        for secret in (password, username):
            if len(secret) >= 3:
                text = text.replace(secret, "[hidden]")
        return SignIn(text, outcome, detail, step if outcome == "code_needed" else "")

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
                found = await frame.evaluate(_TAG_CARD_FIELDS)
            except Exception:  # noqa: BLE001 - a frame that is gone or closed to scripts
                unreachable.append(where)
                continue
            if not isinstance(found, dict) or not found.get("any"):
                continue
            try:
                done = await self._fill_frame(frame, found, number, cvc, exp_month, exp_year, holder, postal)
            except Exception as e:  # noqa: BLE001 - a driver message could quote what was typed: name only the kind of failure
                raise BrowserPolicyError(f"could not fill the card form ({type(e).__name__})") from None
            finally:
                try:
                    await frame.evaluate(_UNTAG_CARD_FIELDS)
                except Exception:  # noqa: BLE001
                    pass
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
        return CardFill(filled, missing, unreachable, extra_auth(text), text)

    async def _fill_frame(self, frame: Any, found: dict[str, Any], number: str, cvc: str, m: int, y: int, holder: str, postal: str) -> list[str]:
        done: list[str] = []
        loc = lambda kind: frame.locator(f'[data-kelo-cc="{kind}"]')  # noqa: E731

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
        got = await page.evaluate(_TAG_ADDRESS_FIELDS)
        found: dict[str, list[dict[str, Any]]] = (got or {}).get("found") or {}
        filled: list[str] = []
        try:
            async def put(field: dict[str, Any], value: str) -> bool:
                if not value:
                    return False
                loc = page.locator(f'[data-kelo-addr="{field["tag"]}"]')
                if field["el"] == "select":
                    return False
                if field.get("ro"):
                    return bool(await page.evaluate(_SET_READONLY, [field["tag"], value]))
                await human.type_text(page, value, locator=loc, timeout_ms=5_000, fallback=lambda ms: loc.fill(value, timeout=ms))
                return True

            async def choose(field: dict[str, Any], wants: list[str]) -> bool:
                value = pick_select(field.get("opts") or [], wants)
                if value is None:
                    return False
                loc = page.locator(f'[data-kelo-addr="{field["tag"]}"]')
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
                        loc = page.locator(f'[data-kelo-addr="{first["tag"]}"]')
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
        finally:
            try:
                await page.evaluate(_UNTAG_ADDRESS_FIELDS)
            except Exception:  # noqa: BLE001
                pass
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
            raw = await self.shoot(page, key, quality=70)
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
            out.append({"key": key, "title": title, "url": url, "host": urlsplit(url).hostname or "", "status": status,
                        "last_activity": max(at, self._seen_at.get(key, 0.0)) or None, "controlled_by": self._control.get(key)})
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

    async def screenshot(self, key: str, max_width: int = CARD_MAX_WIDTH, cap: int = CARD_MAX_BYTES) -> tuple[bytes | None, int, int]:
        """A JPEG of that tab, scaled to `max_width` and at most `cap` bytes; (None, 0, 0) when there is no tab or no picture fits."""
        page = self._pages.get(key)
        if page is None or page.is_closed() or not str(page.url or "").startswith(("http://", "https://")):
            return None, 0, 0
        try:
            raw = await self.shoot(page, key, quality=75)
            return await asyncio.to_thread(shrink_jpeg, raw, max_width, cap)
        except Exception:  # noqa: BLE001
            return None, 0, 0

    async def close_tab(self, key: str) -> bool:
        """Close one tab. The browser context (cookies, sign-ins) stays: only the page goes. A private tab's own context goes with it,
        and with that everything it kept (cookies, storage, cache)."""
        async with self._lock:
            page = self._pages.pop(key, None)
            for d in (self._used, self._seen_at, self._status, self._last_card, self._purchase, self._dialogs, self._doc, self._notes,
                      self._checks, self._targets, self._tries, self._shot_scale, self._check_clicked, self.sign_ins, self._probes,
                      self._cancel, self._verified, self._last_text, self._ref_words, self._look_url):
                d.pop(key, None)
            held = self._control.pop(key, None)
            private = self._private.pop(key, None)
            cdp = self._tab_cdp.pop(key, None)
            self._view_cdp.pop(key, None)   # its overrides end with the page
            under = self._openers.pop(key, [])
            self._popup_setup.pop(key, None)
            self._unchanged.pop(key, None)
        for opener, _view, tab_cdp in under:   # the pages that opened the window the tab was in go with it
            await self._quietly(tab_cdp.detach if tab_cdp is not None and hasattr(tab_cdp, "detach") else None)
            await self._quietly(lambda p=opener: p.close())
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

    async def forget_site(self, site: str, user_said: str = "") -> str:
        """Clear cookies of `site`'s registrable domain from the default context, clear site data for known https origins, and drop saved sign-in state."""
        words = (user_said or "").strip()
        if not words:
            return "Error: browser_forget_site refused: user agreement is required before clearing site data."
        if re.search(r"\b(no|stop|cancel|don'?t|never)\b|그만|취소|중단|아니|안\s*해", words, re.I):
            return "Error: browser_forget_site refused: user declined or chose to stop."
        if not re.search(r"\b(yes|clear|retry|ok|sure|agree|proceed|yep|y)\b|지워|삭제|정리|다시|재시도|초기화|그래|응|좋아|쿠키", words, re.I):
            return "Error: browser_forget_site refused: user did not agree to clear site data."

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
        if ref_of(selector):   # a ref of the last reading: that element is marked for the code script (it reads only CSS selectors)
            await self.frame_for(page, selector)
            await page.locator(ref_selector(selector)).first.evaluate("(e) => e.setAttribute('data-kelo-refpick', '')")
            selector = "[data-kelo-refpick]"
        try:
            found = await page.evaluate(_TAG_CODE_FIELDS, selector or None)
        finally:
            if selector == "[data-kelo-refpick]":
                await self._quietly(lambda: page.evaluate(
                    "() => document.querySelectorAll('[data-kelo-refpick]').forEach((e) => e.removeAttribute('data-kelo-refpick'))"))
        boxes = int(found.get("boxes") or 0) if isinstance(found, dict) else 0
        if not boxes:
            await self._quietly(lambda: page.evaluate(_UNTAG_CODE_FIELDS))
            raise BrowserPolicyError("no box for a one-time code was found on this page" + ("" if selector else "; name it with selector"))
        if boxes > 1 and boxes != len(code):
            await self._quietly(lambda: page.evaluate(_UNTAG_CODE_FIELDS))
            raise BrowserPolicyError(f"the page has {boxes} code boxes but the code has {len(code)} characters")
        try:
            if boxes == 1:
                one = page.locator('[data-kelo-otp="0"]')
                await human.type_text(page, code, locator=one, secret=True, timeout_ms=10_000, fallback=lambda ms: one.fill(code, timeout=ms))
            else:
                for i, ch in enumerate(code):
                    box = page.locator(f'[data-kelo-otp="{i}"]')
                    await human.type_text(page, ch, locator=box, secret=True, timeout_ms=5_000,
                                          fallback=lambda ms, box=box, ch=ch: box.fill(ch, timeout=ms))
            if submit:
                await human.pause_before_submit()
                await page.locator(f'[data-kelo-otp="{boxes - 1}"]').press("Enter", timeout=10_000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001 - a driver message could quote the code: name only the kind of failure
            raise BrowserPolicyError(f"could not type the code ({type(e).__name__})") from None
        finally:
            await self._quietly(lambda: page.evaluate(_UNTAG_CODE_FIELDS))
        try:
            await self._guard(page.url)
        except BrowserPolicyError:
            await page.goto("about:blank")
            raise
        await asyncio.sleep(self.settle_seconds)
        return (await self.snapshot()).replace(code, "[code hidden]")

    async def close(self) -> None:
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

# The visible, unticked checkboxes and switches of a page, each marked with data-kelo-opt and named by the words next to it
OPTIONS_JS = r"""() => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none'; };
  const words = (e) => (e && (e.innerText || e.textContent) || '').replace(/\s+/g, ' ').trim();
  const out = []; let n = 0;
  for (const e of document.querySelectorAll('input[type=checkbox], [role=checkbox], [role=switch]')) {
    if (out.length >= 8) break;
    const checked = e.matches('input') ? e.checked : e.getAttribute('aria-checked') === 'true';
    const lab = (e.labels && e.labels[0]) || e.closest('label');
    const box = lab || e;
    if (checked || e.disabled || !(shown(e) || (lab && shown(lab)))) continue;
    let label = e.getAttribute('aria-label') || words(lab);
    if (!label && e.getAttribute('aria-labelledby')) label = words(document.getElementById(e.getAttribute('aria-labelledby')));
    if (!label) label = words(e.parentElement).slice(0, 120);
    const id = String(++n);
    (shown(e) ? e : box).setAttribute('data-kelo-opt', id);   // a styled box hides its input: the label is what takes the click
    out.push({id, label: label.slice(0, 120)});
  }
  return out;
}"""


def target_label(info: dict[str, Any] | None, selector: str) -> str:
    """The element's words as the user sees them on the page (its text, label, aria-label), never a selector. "" when there are none
    readable (then the card says "a button" / "a field"). An element that could not be read gives "" too: the words in a selector are
    the model's, not the page's (2026-11-10: a card showed the model's own sentence "click the button below to finish" as the label)."""
    words = label_of(info, selector) if info is not None and pressable(info) else ""   # plain text is no button's name
    words = " ".join(str(words).split())
    return words[:60] + "…" if len(words) > 60 else words


SELECTOR_ARG = "ref (e12) or Playwright selector"

# What the approval card adds, by commit kind (commits.py).
_COMMIT_WARNINGS = {
    "pay": N_("This may be the button that confirms a payment, order or booking."),
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
        if as_tool == "browser_click" and not approve_actions and kind != "pay" and session.sign_in_remember(sel):
            return True   # the owner's own sign-in on Kelo's own browser: keeping it signed in is what the owner wants (2026-11-15)
        if kind == "security":
            # the account's protection or an official document: only the very element the user just allowed (a retry of its
            # failed click) goes without a card; no rule, grant, order, purchase window or sign-in shortcut covers it
            return as_tool == "browser_click" and session.retry_of_allowed(sel)
        if kind and as_tool == "browser_click" and kind != "pay" and session.retry_of_allowed(sel):
            return True   # the same element the user just allowed, whose click failed: another selector for it
        if kind and as_tool == "browser_click" and not approve_actions and kind not in ("pay", "delete", "optout") \
                and session.sign_in_continue(sel):
            return True   # a plain "continue" of the owner's own sign-in (an OAuth step of the same site included)
        if kind and grants is not None and grants.use(kind, session.current_host(), tool=as_tool, secret=session.judged_secret(sel)):
            return True   # the user allowed this kind of step on this site for the task running now
        if as_tool == "browser_click" and kind == "delete" and session.use_cancel_action(sel):
            return True   # cancels the order whose charge was wrong, while cancelling is free (purchase_result's cancel window)
        if session.use_purchase_action():
            # finishes the purchase the user just allowed on this site (its window: a few clicks, a few minutes, on the pages that
            # follow from where it was allowed; browser_open ends it). Checked before the "paid" rule below: the final button
            # of an approved order is itself a "buy" that the paid order would otherwise ask about again
            return True
        if (kind == "pay" and orders is not None and orders.paid_here(session.current_host())
                and not money_page(session.judged_info(sel), session.current_url())):
            return False  # the order is paid and its purchase window is over: a "buy" on a product page or cart starts a new purchase
        if kind and orders is not None and not approve_actions and orders.current() is None and orders.covers_request(
                kind, session.current_host(), tool=as_tool, info=session.judged_info(sel), selector=sel, url=session.current_url()):
            return True   # the user's request names this commit (apply / enter / sign up / send) on the site the task works on
        if kind and orders is not None and not approve_actions and kind in ("pay", "apply", "form") \
                and (o := orders.start_implicit(session.current_host())) is not None:
            # buying without begin_order (a weaker model often skips it): the order the user asked for starts here, so its
            # preparing steps don't each ask; the final payment still goes through confirm_purchase
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
        view = str(args.get("view") or "")
        return await shown(await session.open(str(args["url"]), view if view in VIEWS else ""), t("Looking at the site"))

    async def view_(args: dict[str, Any]) -> str:
        return await shown(await session.switch_view(str(args["view"])), t("Looking at the site"))

    def view_ok(args: dict[str, Any]) -> str | None:
        return None if args.get("view") in VIEWS else "view must be desktop or mobile"

    async def read(args: dict[str, Any]) -> str:
        return await shown(await session.snapshot(clear=True, only_controls=args.get("only") == "controls"), t("Checking the page"))

    async def click(args: dict[str, Any]) -> str:
        target = session.note_allowed(str(args["selector"]))
        try:
            text = await session.click(str(args["selector"]))
        except Exception:
            session.allowed_failed(target)   # tried again with another selector for the same element: no second card
            raise
        if session.last_click_changed_nothing() and session.judged(str(args["selector"])) not in ("", "pay"):
            # allowed, ran, changed nothing: the same element tried again (another selector) does not ask again; a payment button
            # always does (the agent is told not to repeat the click)
            session.allowed_failed(target)
        else:
            session.allowed_done()
        if session.in_checkout() and (words := extra_auth(text)):
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

    async def describe_click(args: dict[str, Any]) -> str:
        host = session.current_host() or t("blank page")
        sel = str(args.get("selector", ""))
        # what the user sees on the page, never the selector (for a ref the page's own name for it, never "aria-ref=…")
        label = target_label(session.judged_info(sel), sel) or session.ref_label(sel)
        line = t("Click “{label}” on {host}.", host=host, label=label) if label else t("Click a button on {host}.", host=host)
        return line + warning(args)

    async def describe_type(args: dict[str, Any]) -> str:
        host = session.current_host() or t("blank page")
        sel = str(args.get("selector", ""))
        info = session.judged_info(sel)
        label = target_label({**info, "text": "", "words": info.get("words") or info.get("attrs") or ""} if info else None, sel) \
            or session.ref_label(sel)
        line = t("Type into “{label}” on {host}.", host=host, label=label) if label else t("Type into a field on {host}.", host=host)
        return line + warning(args)

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    return session.with_deadline([
        Tool("browser_open", "Open a public http(s) address in your browser and return the page text. Only an address you were given "
             "(a search result, a link on a page, the user, a tool); never one you made up: for a page inside a site, open the site "
             "and follow its links, menu or search. Its reading marks elements with [ref=e12]: click and type by the refs of your "
             "last reading.",
             obj({"url": {"type": "string"},
                  "view": {"type": "string", "enum": list(VIEWS),
                           "description": "optional: open it as a desktop or a phone browser (default: the view that last worked on "
                                          "that site, else desktop); to switch later, load browser_view with find_tools"}}, ["url"]),
             open_, untrusted=True, source="page", hold=session.hold_for_user),
        Tool("browser_view", "Switch this tab between the desktop and the mobile (phone) view of sites and load the page again in "
             "it; sign-ins and cookies stay. If a page or a step fails in one view (blocked, broken layout, a button that can't be "
             "clicked, a dead end), try the other view before giving up.",
             obj({"view": {"type": "string", "enum": list(VIEWS)}}, ["view"]), view_, untrusted=True, source="page",
             validate=view_ok, hold=session.hold_for_user),
        Tool("browser_read", "Return the current page's title, URL and what it shows, one element per line. Readings mark elements "
             "with [ref=e12]: pass e12 to browser_click and browser_type. When the user questions a value you read, read the page again (or where it shows in full) "
             "before you answer.",
             obj({"only": {"type": "string", "enum": ["controls"],
                           "description": "controls: only what can be clicked or typed into, plus headings (shorter; use it when "
                                          "you only need to act)"}}, []),
             read, untrusted=True, source="page", hold=session.hold_for_user),
        Tool("browser_click", "Click an element and return the page after (also inside frames of the page; a window the click "
             "opens becomes the page you are on). A click that commits something in "
             "the user's name shows them a card first.",
             obj({"selector": {"type": "string", "description": SELECTOR_ARG}}, ["selector"]), click, requires_approval=True, untrusted=True, source="page",
             auto_approve=no_card("browser_click"), describe=describe_click, declined=declined_click, hold=session.hold_for_user),
        Tool("browser_type", "Type text into an input. On a list (a select or a dropdown) it chooses the option with that text instead: "
             "the one way to set a list (quantity, size, date). Personal details or "
             "a message show the user a card first.",
             obj({"selector": {"type": "string", "description": SELECTOR_ARG}, "text": {"type": "string"},
                  "submit": {"type": "boolean", "description": "press Enter after"}},
                 ["selector", "text"]),
             type_, requires_approval=True, untrusted=True, source="page", auto_approve=no_card("browser_type"), describe=describe_type,
             hold=session.hold_for_user),
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
             describe=describe("browser_click_at"), hold=session.hold_for_user),
        Tool("browser_drag", "Drag along a path of points (x, y) of your last browser_look picture: press at the first, move through "
             "the others, let go at the last. Same rules as browser_click_at: canvas, map, slider or drag handle with no ref; look first, act, look again.",
             {"type": "object", "properties": {"path": {"type": "array", "items": point, "minItems": DRAG_POINTS[0],
                                                        "maxItems": DRAG_POINTS[1]}},
              "required": ["path"]},
             drag, requires_approval=True, untrusted=True, source="page", validate=ok("browser_drag"),
             auto_approve=step_decider(session, "browser_drag", approve_actions=approve_actions, grants=grants, orders=orders),
             describe=describe("browser_drag"), hold=session.hold_for_user),
    ])
