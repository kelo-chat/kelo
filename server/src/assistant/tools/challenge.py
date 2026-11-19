"""Telling a bot check ("are you a person?") apart from an ordinary page, from structure rather than words.

Evidence, strongest first:
- the main document's response: status and headers (`cf-mitigated: challenge`, `x-amzn-waf-action: captcha|challenge`, `x-datadome`,
  `x-px-*`...), collected by BrowserSession from the page's own responses;
- frames and iframes of the page: their addresses (challenges.cloudflare.com, google.com/recaptcha, hcaptcha.com,
  captcha-delivery.com, arkoselabs.com, geetest.com, awswaf.com...) and whether they are shown at a usable size;
- marks in the DOM that the check scripts put there (#challenge-running, .cf-turnstile, .g-recaptcha, .h-captcha, #px-captcha...);
- the page title of a full-page check ("Just a moment...").

`detect` is a pure function over what `PROBE_JS` read from the page plus the response, so it is tested without a browser. A widget
that is present but invisible (reCAPTCHA v3 / invisible badges, a managed Turnstile that never shows) is NOT a check: the page works.

What happens to a check (BrowserSession.snapshot): the agent may first try it itself in the page, with its normal browser steps and
its own reading of the page or a picture of it (browser_human_check), at most CHECK_TRIES times (`try_note`). No outside solving
service, no paid solver, nothing that changes the page's or the site's scripts. When the tries are used up, or the agent says it
cannot do this kind, the check is handed to the user as before (the app's `handover`): the step waits while the user solves it in the
live view or skips the site, and the agent is told how it ended (`handover_note`). Without an app turn the agent gets `note_for`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..i18n import N_, t

# Reads what `detect` needs. Nothing typed by the user is read; only addresses, sizes, a few element marks and the title. One-time-code boxes are counted by structure.
# (Kept free of words the test fakes route on: no attribute setters, no "pw:".)
PROBE_JS = r"""() => {
  const kelo_check = 1;
  const seen = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), shown: r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none' && c.opacity !== '0' }; };
  const frames = [...document.querySelectorAll('iframe')].slice(0, 40).map((f) => ({ src: (f.src || f.getAttribute('data-src') || '').slice(0, 300), title: (f.title || '').slice(0, 80), ...seen(f) }));
  const marks = {};
  const sel = { cf_running: '#challenge-running, #challenge-stage, #cf-challenge-running, #challenge-form, .cf-browser-verification, #cf-please-wait',
    turnstile: '.cf-turnstile, [data-sitekey][class*="turnstile"]', recaptcha: '.g-recaptcha, #recaptcha, [data-sitekey].g-recaptcha',
    hcaptcha: '.h-captcha, [data-hcaptcha-widget-id]', px: '#px-captcha, [id^="px-captcha"]', geetest: '.geetest_holder, .geetest_panel, .geetest_box, [class^="geetest_"]',
    arkose: '#arkose, [data-arkose], #FunCaptcha, #funcaptcha', awswaf: '#captcha-container awswaf-captcha, awswaf-captcha, #aws-waf-captcha',
    datadome: '#ddv1-captcha-container, .datadome-captcha' };
  for (const [k, s] of Object.entries(sel)) { const e = document.querySelector(s); if (e) marks[k] = seen(e); }
  const scripts = [...document.scripts].slice(0, 80).map((s) => (s.src || '').slice(0, 200)).filter(Boolean);
  const whole = document.body ? document.body.innerText : '';
  const text = whole.slice(0, 3000);
  const ins = [...document.querySelectorAll('input')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  // a code box by STRUCTURE only (no name, id or label is read): autocomplete one-time-code; a row of 4-8 one-character boxes; or the one
  // visible box shaped like a code (numeric input mode, or a short maxlength of 4-8) that carries no autocomplete token naming something else
  const kind = (e) => (e.getAttribute('type') || '').toLowerCase();
  const token = (e) => (e.getAttribute('autocomplete') || '').toLowerCase();
  const other = (e) => token(e).split(/\s+/).some((t) => t && t !== 'off' && t !== 'on' && t !== 'one-time-code');
  const tokened = ins.filter((e) => /(^|\s)one-time-code(\s|$)/.test(token(e))).length;
  const singles = ins.filter((e) => e.maxLength === 1 && !e.disabled && !e.readOnly && ['', 'text', 'tel', 'number', 'password'].includes(kind(e))).length;
  const shaped = ins.filter((e) => ['', 'text', 'tel', 'number'].includes(kind(e)) && !e.disabled && !e.readOnly && !other(e) && e.maxLength !== 1
    && ((/numeric|decimal/.test(e.inputMode || '') && !(e.maxLength > 12)) || (e.maxLength >= 4 && e.maxLength <= 8))).length;
  const secret_box = ins.some((e) => kind(e) === 'password' && getComputedStyle(e).visibility !== 'hidden');
  // a short box a person could type into: not a search box (type or role), not a box of a search form
  const lookup = (e) => kind(e) === 'search' || e.getAttribute('role') === 'searchbox' || !!e.closest('[role=search]');
  const short_inputs = ins.filter((e) => ['', 'text', 'tel', 'number'].includes(kind(e)) && !e.disabled && !e.readOnly
    && !lookup(e) && ((e.maxLength > 0 && e.maxLength <= 12) || /numeric|decimal/.test(e.inputMode || '') || kind(e) !== 'text' || ins.length <= 3)).length;
  return { kelo_check, title: (document.title || '').slice(0, 160), frames, marks, scripts, text, text_len: whole.length, url: location.href.slice(0, 300),
           secret_box, short_inputs,
           inputs: document.querySelectorAll('input:not([type=hidden])').length, otp: tokened + (singles >= 4 && singles <= 8 ? 1 : (tokened === 0 && shaped === 1 ? 1 : 0)) };
}"""


@dataclass(slots=True)
class Challenge:
    vendor: str            # cloudflare | recaptcha | hcaptcha | datadome | perimeterx | arkose | geetest | aws_waf
    kind: str              # interstitial (the whole page is the check) | widget (a check box or puzzle on the page)
    evidence: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return t(VENDOR_NAMES[self.vendor]) if self.vendor in VENDOR_NAMES else self.vendor

    def as_dict(self) -> dict[str, Any]:
        return {"vendor": self.vendor, "name": self.name, "kind": self.kind, "evidence": list(self.evidence)}


VENDOR_NAMES = {"cloudflare": N_("Cloudflare check"), "recaptcha": "reCAPTCHA", "hcaptcha": "hCaptcha", "datadome": N_("DataDome check"),
                "perimeterx": N_("HUMAN (PerimeterX) check"), "arkose": N_("Arkose puzzle"), "geetest": N_("GeeTest puzzle"),
                "aws_waf": N_("AWS WAF check")}

# frame address -> vendor, and whether a shown frame of it is the check itself (not just a badge)
_FRAMES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"challenges\.cloudflare\.com/"), "cloudflare"),
    (re.compile(r"(?:google\.com|recaptcha\.net|gstatic\.com)/recaptcha/"), "recaptcha"),
    (re.compile(r"(?:^|[/.])hcaptcha\.com/"), "hcaptcha"),
    (re.compile(r"captcha-delivery\.com/"), "datadome"),
    (re.compile(r"(?:arkoselabs\.com|funcaptcha\.com)/"), "arkose"),
    (re.compile(r"geetest\.com/"), "geetest"),
    (re.compile(r"(?:awswaf\.com|captcha\.awswaf)"), "aws_waf"),
    (re.compile(r"(?:px-cdn\.net|perimeterx\.net|px-cloud\.net|/px/captcha)"), "perimeterx"),
]
_MIN_SIDE = 30   # a frame or box smaller than this (both sides) is a badge or a hidden helper, not a check the user must do


def _visible(box: dict[str, Any] | None) -> bool:
    return bool(box) and bool(box.get("shown")) and (int(box.get("w") or 0) >= _MIN_SIDE and int(box.get("h") or 0) >= _MIN_SIDE)


def detect(probe: Any, *, status: int | None = None, headers: dict[str, str] | None = None, frame_urls: list[str] | None = None) -> Challenge | None:
    """A bot check on the page, or None. `probe`: what PROBE_JS returned (anything else = no page reading); `status`/`headers`: the main
    document's last response; `frame_urls`: addresses of the page's frames as the browser lists them (cross-origin ones included)."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    p = probe if isinstance(probe, dict) and probe.get("kelo_check") else {}
    title = str(p.get("title") or "").strip()
    marks: dict[str, Any] = p.get("marks") if isinstance(p.get("marks"), dict) else {}
    frames = [f for f in (p.get("frames") or []) if isinstance(f, dict)]
    scripts = [str(s) for s in (p.get("scripts") or [])]
    inputs = int(p.get("inputs") or 0) if str(p.get("inputs") or "0").isdigit() else 0

    # 1. the response says so
    if h.get("cf-mitigated", "").lower() == "challenge":
        return Challenge("cloudflare", "interstitial", ["header cf-mitigated: challenge"])
    waf = h.get("x-amzn-waf-action", "").lower()
    if waf in ("captcha", "challenge"):
        return Challenge("aws_waf", "interstitial", [f"header x-amzn-waf-action: {waf}"])
    blocked_status = status in (401, 403, 405, 429, 503)
    if blocked_status and ("x-datadome" in h or "x-dd-b" in h or "x-datadome-cid" in h):
        return Challenge("datadome", "interstitial", [f"HTTP {status} with a DataDome header"])
    if blocked_status and any(k.startswith("x-px") for k in h):
        return Challenge("perimeterx", "interstitial", [f"HTTP {status} with a PerimeterX header"])

    # 2. a full-page check: its running marks (never its title words), or a Cloudflare server answering 403/503 with its check page
    if "cf_running" in marks:
        return Challenge("cloudflare", "interstitial", ["Cloudflare check page elements"])
    if blocked_status and h.get("server", "").lower() == "cloudflare" and (any("challenges.cloudflare.com" in s for s in scripts) or "turnstile" in marks):
        return Challenge("cloudflare", "interstitial", [f"HTTP {status} from Cloudflare with its check script"])

    # 3. a check shown inside the page: a visible frame of a check service, or a visible box the check script drew
    seen_frames = list(frames) + [{"src": u, "shown": None} for u in (frame_urls or [])]
    for f in seen_frames:
        src = str(f.get("src") or "")
        for rx, vendor in _FRAMES:
            if not rx.search(src):
                continue
            if vendor == "recaptcha" and ("size=invisible" in src or "/recaptcha/api2/aframe" in src or "/recaptcha/enterprise/aframe" in src):
                continue    # the invisible kind: scores in the background, asks nothing (its puzzle frame 'bframe' is caught when shown)
            if f.get("shown") is None:
                continue    # known only from the browser's frame list: no size, so not proof of something to do
            if _visible(f):
                kind = "interstitial" if vendor in ("datadome", "perimeterx", "aws_waf") or (vendor == "cloudflare" and inputs <= 1) else "widget"
                return Challenge(vendor, kind, [f"visible frame from {re.sub(r'^https?://', '', src).split('/')[0]}"])
    for key, vendor in (("turnstile", "cloudflare"), ("recaptcha", "recaptcha"), ("hcaptcha", "hcaptcha"), ("px", "perimeterx"),
                        ("geetest", "geetest"), ("arkose", "arkose"), ("awswaf", "aws_waf"), ("datadome", "datadome")):
        if _visible(marks.get(key)):
            return Challenge(vendor, "interstitial" if vendor in ("perimeterx", "aws_waf", "datadome") else "widget", [f"visible {key} element"])
    return None


BLOCKED_MAX_TEXT = 1500   # a blocking page is short; a long page that answered 403 is an ordinary page


def blocked(status: int | None, headers: dict[str, str] | None, title: str, text: str) -> str | None:
    """An access-denied page, by structure: the site answered HTTP 403, 429 or 503 for the page with a short body (a refusal page, not
    an ordinary page that happens to answer 403) and it is not a human check (a check vendor's header, mark or frame is never blocked
    here: it keeps its hand-over path). It is not a puzzle a person can solve, so waiting and retrying do not help. Returns what the
    site said ('HTTP 403 "<first line>"') or None. 401 is a request to sign in, not a block. No word of any language is read.
    `text` is the page's visible text."""
    if status not in (403, 429, 503):
        return None
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if h.get("cf-mitigated") or h.get("x-amzn-waf-action") or any(k.startswith(("x-px", "x-datadome", "x-dd-")) for k in h):
        return None
    title, body = (title or "").strip(), (text or "").strip()
    if len(body) > BLOCKED_MAX_TEXT:
        return None
    words = " ".join((title or body.split("\n", 1)[0]).split())[:60]
    return f'HTTP {status} "{words}"' if words else f"HTTP {status}"


CHECK_TRIES = 2   # the agent's own tries at one human check before it goes to the user


def check_boxes(probe: Any) -> list[dict[str, int]]:
    """Where the visible check frames and boxes are on the page (CSS px: x, y, w, h), from what PROBE_JS read. A click the agent makes
    on a check that sits inside an ordinary page must land in one of these."""
    p = probe if isinstance(probe, dict) and probe.get("kelo_check") else {}
    boxes: list[dict[str, Any]] = [f for f in (p.get("frames") or []) if isinstance(f, dict)
                                   and any(rx.search(str(f.get("src") or "")) for rx, _ in _FRAMES)]
    marks = p.get("marks") if isinstance(p.get("marks"), dict) else {}
    boxes += [b for k, b in marks.items() if k != "cf_running" and isinstance(b, dict)]
    out = []
    for b in boxes:
        if _visible(b):
            try:
                out.append({"x": int(b.get("x") or 0), "y": int(b.get("y") or 0), "w": int(b["w"]), "h": int(b["h"])})
            except (TypeError, ValueError):
                continue
    return out


def _where(c: Challenge, host: str) -> str:
    what = "the whole page is a check" if c.kind == "interstitial" else "a check box or puzzle is on the page"
    return f"[Human check on the page ({c.name}){f' on {host}' if host else ''}: {what}."


def try_note(c: Challenge, host: str, left: int) -> str:
    """What the agent is told while it may still try the check itself (`left` of CHECK_TRIES tries remain)."""
    return (f"{_where(c, host)} You may try it yourself in this page ({left} of {CHECK_TRIES} tries left; each page reading that still "
            "shows it uses one): use browser_human_check to see a picture and click or type in the check, or your other browser steps. "
            "No outside solving services, and never change or work around the page's scripts. If it is a kind you cannot do, call "
            "browser_human_check with action hand_over. After the last try it goes to the user by itself.]")


def note_for(c: Challenge, host: str) -> str:
    """What the agent is told when its tries are used up (or it cannot do this kind) and nobody can be asked (no app turn)."""
    return (f"{_where(c, host)} It needs the user now: do not try it again or retry in a loop. Tell the user that the site wants "
            "to confirm a person is there and that they can do it themselves in the live browser (Browser, then Take control). When "
            "the user hands the browser back, read the page once with browser_read and go on.]")


def handover_note(outcome: str, host: str, minutes: int = 0) -> str:
    """What the agent is told after a human check was handed to the user (the browser step waited for it). The agent already had its
    tries: it does not try again, and does not open the same page again in a loop."""
    site = host or "the site"
    if outcome in ("solved", "done"):
        return (f"[The human check on {site} is gone (the user solved it, or it cleared by itself). The page below is how it looks now: go "
                "on from here; read it once more with browser_read only if it looks unfinished.]")
    away = (f"Do not open {site} again in this task and do not try the check again yourself. Go on without it (other sources or "
            "sites) and say in your answer, in one short line, that ")
    if outcome == "skipped":
        return f"[The user chose to skip {site}, which asked to confirm a person is there. {away}{site} was skipped.]"
    if outcome == "repeat":
        return f"[{site} asks again to confirm a person is there and was already skipped in this task. {away}{site} was skipped.]"
    if outcome == "expired":
        return (f"[Nobody solved the human check on {site} within {max(1, minutes)} minutes. {away}{site} could not be used because it "
                "asked to confirm a person is there.]")
    if outcome == "closed":
        return f"[The tab with the human check on {site} was closed before it was solved. {away}{site} could not be used.]"
    return f"[The human check on {site} was not solved (the task is stopping). {away}{site} could not be used.]"


def signed_out_note(site: str, login: str, *, job: bool, minutes: int) -> str:
    """What the agent is told when a site refuses a browser that is not signed in to it: the next step is the sign-in (brief B2)."""
    head = (f"[{site} refused Kelo's browser, and Kelo's browser is not signed in to {site}. Sites often refuse a browser that is not "
            "signed in, so the next step is to sign in: do not retry the page, switch the view or use another address. ")
    if login:
        how = (f"A saved login exists: open {site}'s home page, follow the site's own sign-in link and call browser_login(name={login!r}) there "
               "(or pass that link's ref as via and Kelo clicks it). ")
    elif job:
        how = (f"No login is saved for {site} and nobody can sign in during this job: report that Kelo's browser needs to be signed in "
               f"to {site} once, and do not use {site} again in this job. ")
    else:
        how = (f"No login is saved for {site}: ask the user to save one (request_login) or to sign in once themselves by taking over "
               "the browser. ")
    return head + how + (f"For the next {minutes} minutes Kelo's browser loads {site} only to sign in; after the sign-in succeeds, "
                         "start again from the site's home page.]")


def blocked_note(host: str, why: str, *, job: bool | None = None, count: int = 1, second: bool = False) -> str:
    """What the agent is told when a site answers with an access-denied page."""
    site = host or "the site"
    if job is None:
        try:
            from ..workers import current_worker
            is_job = bool(current_worker.get())
        except Exception:
            is_job = False
    else:
        is_job = bool(job)

    if is_job:
        return (f"[This site ({site}) is blocking Kelo's browser: it answered {why}. Stop on this site: do not retry, "
                f"open another address of it or switch to its mobile site. Call report(status='blocked', reason='site_refused', "
                f"summary=...) saying what you could not do there; the user can take over the browser to sign in or get past it.]")

    return (f"[This site ({site}) is blocking Kelo's browser: it answered {why}. Do not retry. "
            f"Tell the user plainly that it refused Kelo's browser and that they can take over the browser to sign in or get past it.]")


def blocked_again_note(host: str) -> str:
    """What the agent is told for a site that has blocked it twice in this task (nothing is loaded)."""
    return (f"[Blocked again. {host or 'The site'} has blocked Kelo's browser twice in this task, so it was not tried a third time. Do not "
            "open or click on it again: use another source, or tell the user the site blocks Kelo's browser and what you did instead.]")
