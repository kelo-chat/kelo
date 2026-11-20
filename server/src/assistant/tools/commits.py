"""Which browser steps commit something on the user's behalf, and so get an Allow/Deny card (docs/click-judge.md).

No word, phrase, site or country list decides anything here. A click is told by what the step DOES:

  1. Structure (`structure`, `click_route`): a real link to another page, or an element that only opens a window or a tab, only looks
     (what is done there is judged there); a plain text label is not pressable. A submit, code that sends a request or changes state,
     a sign-in or security form (password, new-password, one-time-code, WebAuthn) or an element that cannot be read goes on to 2.
  2. The step judge (step_judge.StepJudge, strict JSON, cached): spends_money, sends, deletes, account_security, signs_up, applies,
     opts_out -> the commit kinds below. No flag set = no card. The judge unavailable or a bad answer = "unknown": the owner is asked.
  3. Typing asks only where it touches money or a sign-in: a card number by VALUE (13-19 digits passing Luhn) or a password field
     (type password, autocomplete current-password / new-password / one-time-code) = "personal". An e-mail address, a phone number, a
     name or an address typed into a field, and an ordinary form submit, ask nothing by themselves (as in the reference: ask on what
     the step DOES, never on what is typed); the privacy guard still looks at typed text.

Commit kinds (the names tools/browser.py, permissions, grants, orders and the cards use):
  pay      spends money (a final checkout step goes through checkout_review / checkout_submit, tools/checkout.py)
  apply    an application, a registration for a program, event, benefit or official record, a draw entry
  send     sends something to people or services
  delete   deletes or cancels something
  optout   unsubscribes, withdraws consent, closes a membership
  account  creates an account or registers
  security changes how the account is protected or reached: ALWAYS asks, no rule, grant or order covers it
  personal typing a card number or into a password field
"""

from __future__ import annotations

import re
from typing import Any

from .page_paths import with_path

# Read from the page for one element (Playwright `locator.evaluate`): what the user would see and what the element would do.
TARGET_JS = with_path(r"""(el) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  const act = el.closest('button, a[href], [role=button], [role=link], [role=tab], [role=menuitem], [role=option], input, select, textarea, summary, label') || el;
  const tag = act.tagName.toLowerCase();
  const type = (act.getAttribute('type') || '').toLowerCase();
  const form = act.form || act.closest('form');
  const lab = act.labels && act.labels.length ? act.labels[0].innerText : '';
  const words = [act.getAttribute('aria-label'), act.getAttribute('title'),
                 ['button', 'submit', 'reset', 'image'].includes(type) ? act.value || act.getAttribute('alt') : ''].filter(Boolean).join(' ');
  const attrs = [act.getAttribute('name'), act.id, act.getAttribute('placeholder'), act.getAttribute('aria-label'), act.getAttribute('autocomplete'),
                 lab, act.getAttribute('data-testid')].filter(Boolean).join(' ');
  const submits = !!form && ((tag === 'button' && (type === 'submit' || type === '')) || (tag === 'input' && (type === 'submit' || type === 'image')));
  // personal fields are told by structure: input types and the standard autocomplete tokens (WHATWG), never by names or words
  const TOKENS = /(^|\s)(name|given-name|family-name|additional-name|honorific-prefix|email|tel|tel-[a-z-]+|impp|street-address|address-line\d|address-level\d|postal-code|country|country-name|bday|bday-[a-z]+|username|new-password|current-password|one-time-code|cc-[a-z-]+|sex|organization|organization-title|url)(\s|$)/;
  let personal = 0, message = 0, search = false, secure = false, signup = false, texts = 0;
  if (form) {
    search = form.getAttribute('role') === 'search' || !!form.querySelector('input[type=search], [role=searchbox]');
    secure = !!form.querySelector('input[type=password], input[autocomplete~="new-password"], input[autocomplete~="current-password"], input[autocomplete~="one-time-code"], input[autocomplete*="webauthn"]');
    signup = !!form.querySelector('input[autocomplete~="new-password"]');
    for (const f of form.querySelectorAll('input, textarea, [contenteditable=true]')) {
      const ft = (f.getAttribute('type') || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'image', 'reset', 'checkbox', 'radio', 'search'].includes(ft)) continue;
      const v = f.isContentEditable ? f.innerText : f.value;
      if (!v && ft !== 'file') continue;
      if (f.tagName === 'TEXTAREA' || f.isContentEditable) { message++; continue; }
      if (['email', 'tel', 'password', 'file'].includes(ft) || TOKENS.test((f.getAttribute('autocomplete') || '').toLowerCase())) personal++;
      else texts++;
    }
  }
  // the page asks for card details: payment inputs by their autocomplete token (structure, not words)
  const payPage = !!document.querySelector('input[autocomplete^="cc-"]');
  // what the click would run: inline handlers (the element and two parents), a javascript: address, and the page's own functions they
  // call (two levels). The listeners added with addEventListener are read separately (BrowserSession.read_target, DevTools).
  const code = [];
  for (let n = act, i = 0; n && n.nodeType === 1 && i < 3; n = n.parentElement, i++) {
    for (const ev of ['onclick', 'onmousedown', 'onmouseup', 'onpointerup', 'ontouchend']) { const v = n.getAttribute(ev); if (v) code.push(v); }
  }
  const rawHref = tag === 'a' ? (act.getAttribute('href') || '') : '';
  if (/^\s*javascript:/i.test(rawHref)) code.push(rawHref.replace(/^\s*javascript:/i, ''));
  const seenFns = new Set();
  const resolveFns = (src, depth) => {
    if (depth > 2) return;
    for (const m of src.matchAll(/([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(/g)) {
      const name = m[1];
      if (seenFns.has(name) || seenFns.size > 16 || /^(if|for|while|switch|return|function|typeof|new)$/.test(name)) continue;
      seenFns.add(name);
      let f = window;
      try { for (const part of name.split('.')) f = f == null ? undefined : f[part]; } catch (e) { f = undefined; }
      if (typeof f !== 'function') continue;
      let body = '';
      try { body = Function.prototype.toString.call(f); } catch (e) {}
      if (!body || /\[native code\]/.test(body)) continue;
      code.push(body.slice(0, 3000));
      resolveFns(body.slice(0, 3000), depth + 1);
    }
  };
  for (const c of code.slice()) resolveFns(c, 1);
  // the element is read again by DevTools through this CSS path (never a page global); empty when the path does not find it alone
  const judgePath = keloPath(act);
  const judgeHits = judgePath ? document.querySelectorAll(judgePath) : [];
  const formTarget = form ? (form.getAttribute('target') || '') : '';
  // how the control stands now, and how the part of the page around it stands (done_once.py: the same click on a changed page is a
  // new step). By structure only: ARIA states, checked/disabled/selected values, the other controls' own labels with digits masked
  // (a counter or a timer is not a change), the open dialogs' labels. Typed text is not read here (done_once FormWords has it).
  const stateOf = (e) => {
    const out = [];
    for (const a of ['aria-pressed', 'aria-checked', 'aria-selected', 'aria-expanded', 'aria-disabled', 'aria-current']) {
      const v = e.getAttribute(a); if (v !== null && v !== '') out.push(a.slice(5) + '=' + v);
    }
    if (e.matches('input[type=checkbox], input[type=radio]')) out.push('checked=' + e.checked);
    if (e.matches('select')) out.push('value=' + clean(e.selectedOptions && e.selectedOptions[0] ? e.selectedOptions[0].text : e.value).slice(0, 40));
    if (e.disabled) out.push('disabled');
    return out.join(' ');
  };
  const CONTROLS = 'button, a[href], [role=button], [role=link], [role=tab], [role=checkbox], [role=switch], [role=radio], [role=option], '
    + '[role=menuitem], input, select, textarea, [aria-pressed], [aria-checked], [aria-expanded]';
  const labelOf = (c) => c.getAttribute('aria-label') || (c.matches('input[type=button], input[type=submit]') ? c.value
    : c.matches('input, textarea, select') ? (c.getAttribute('placeholder') || c.getAttribute('name') || '') : (c.innerText || ''));
  let scope = form || act.parentElement || act;
  for (let n = scope, i = 0; n && n.parentElement && n !== document.body && i < 12; n = n.parentElement, i++) {
    if (n.parentElement.querySelectorAll(CONTROLS).length > 30) break;
    scope = n.parentElement;
  }
  const sceneParts = [];
  for (const c of [...scope.querySelectorAll(CONTROLS)].slice(0, 30)) {
    if (c === act || act.contains(c) || c.contains(act)) continue;
    const r = c.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) continue;
    sceneParts.push((c.getAttribute('role') || c.tagName.toLowerCase()) + ':' + clean(labelOf(c)).replace(/\d/g, '#').slice(0, 40) + ':' + stateOf(c));
  }
  for (const d of document.querySelectorAll('[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open]')) {
    const r = d.getBoundingClientRect();
    if (!(r.width > 1 && r.height > 1)) continue;
    const head = d.querySelector('h1,h2,h3,h4');
    sceneParts.push('w:' + clean(d.getAttribute('aria-label') || (head && head.innerText) || d.innerText || '').replace(/\d/g, '#').slice(0, 60));
  }
  return {
    structure_read: true, handler_src: code.join('\n;\n').slice(0, 12000),
    link_target: (tag === 'a' || tag === 'area') ? (act.getAttribute('target') || '') : '', form_target: formTarget,
    pay_page: payPage,
    in_form: !!form, tag, type, role: (act.getAttribute('role') || '').toLowerCase(), cursor: getComputedStyle(act).cursor || '',
    text: clean(act.innerText || act.textContent || ''), words: clean(words), attrs: clean(attrs),
    href: tag === 'a' ? (act.getAttribute('href') || '') : '',
    editable: !!act.isContentEditable, autocomplete: (act.getAttribute('autocomplete') || '').toLowerCase(),
    name: (act.getAttribute('name') || '').toLowerCase(),
    submits, search_form: search, form_personal: personal, form_message: message, form_text: texts, secure_form: secure, signup_form: signup,
    url: location.href, title: clean(document.title),
    judge_path: judgeHits.length === 1 && judgeHits[0] === act ? judgePath : '',
    state: stateOf(act), scene: sceneParts.join('\n').slice(0, 4000),
  };
}""")

LONG_LINK = 25   # a link with more words than this is a result, an article or a product card: following it only looks

# A count, a price or a rating, maybe with a word or two after it: a label of a card, not a button (digits and symbols only)
_COUNT = re.compile(r"^[(\[]?\s*(?:[\u20a9$\u20ac\xa3\xa5])?\s*\d[\d,.]*\s*(?:\+)?\s*(?:\S+\s*){0,3}$")
_NAV_ROLES = ("link", "menuitem", "menuitemradio", "menuitemcheckbox", "tab", "treeitem")
# What a browser itself builds from page state, whose press sends nothing unless code says so (checked after the code is read)
_CONTROL_TAGS = frozenset({"select", "option", "textarea", "summary", "label"})
_CONTROL_ROLES = frozenset({"checkbox", "switch", "radio", "option", "combobox", "listbox", "tab", "menuitemradio", "menuitemcheckbox"})
_FIELD_TYPES = frozenset({"checkbox", "radio", "text", "search", "email", "tel", "number", "date", "datetime-local", "time", "month",
                          "week", "range", "color", "url", "password", "file"})

# The standard autocomplete tokens (WHATWG) of a sign-in secret: typing one by hand stays guarded (sign-ins go through browser_login)
_SECRET_AUTOCOMPLETE = re.compile(r"(?:^|\s)(?:current-password|new-password|one-time-code)(?:\s|$)")

_SELECTOR_TEXT = re.compile(r"""(?:text|has-text|:text|text-is|aria-label|title|value|alt|placeholder)\s*[=(]\s*(["']?)(.+?)\1\s*[)\]]?\s*(?:$|>>)""",
                            re.I)


def _luhn(n: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(n)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def selector_words(selector: str) -> str:
    """The text a selector names (its text=/has-text/aria-label values), else its ids and classes with - and _ read as spaces. Only
    handed to the step judge as the control's name when the element cannot be read; it decides nothing itself."""
    s = selector or ""
    found = [m.group(2) for m in _SELECTOR_TEXT.finditer(s)]
    if found:
        return " ".join(found)
    return re.sub(r"[#.\-_\[\]=\"'():>]+", " ", s)


def _page(info: dict[str, Any] | None, url: str) -> str:
    """The page's path and title (not its host)."""
    raw = str((info or {}).get("url") or url or "")
    path = re.sub(r"^[a-z]+://[^/]+", "", raw, flags=re.I)
    return f"{path} {(info or {}).get('title') or ''}"


def is_count(label: str) -> bool:
    """A number with at most a few words after it: a count, a price or a rating on a card (digits decide, no word does)."""
    w = " ".join((label or "").split())
    return bool(w) and bool(_COUNT.match(w)) and "!" not in w


# ---------------------------------------------------------------- what a click would do, from the element and its code
# Code that sends something or changes state: a form submit, a request (fetch, XHR, jQuery/axios posts, a beacon), a POST.
_STATE_CODE = re.compile(r"\.\s*(?:request)?submit\s*\(|\bfetch\s*\(|XMLHttpRequest|\$\s*\.\s*(?:post|ajax|get)\s*\(|\.ajax\s*\(|"
                         r"\baxios\b|sendBeacon|method\s*[:=]\s*['\"]?post|\.send\s*\(|WebSocket", re.I)
# Code that opens a window or goes to another page.
_OPEN_CODE = re.compile(r"\bwindow\s*\.\s*open\s*\(|(?<![\w.])open\s*\(|showModalDialog|"
                        r"\blocation(?:\s*\.\s*href)?\s*=(?!=)|location\s*\.\s*(?:assign|replace)\s*\(|history\s*\.\s*pushState", re.I)


def structure(info: dict[str, Any] | None) -> str:
    """What clicking the element does, from what it is and the code it runs (not its words):
    "changes" (a form submit, a request, a POST, a sign-in or security form: it may commit), "opens" (it only opens a window or a new
    tab), "goes" (an ordinary link to another page, nothing else), or "" (not known: the element was not read this way, or its code
    says neither)."""
    if not info or not info.get("structure_read"):
        return ""
    src = str(info.get("handler_src") or "")
    if info.get("submits") or _STATE_CODE.search(src) or info.get("secure_form"):
        return "changes"
    href = str(info.get("href") or "")
    real_link = info.get("tag") == "a" and not re.match(r"^\s*(#|javascript:|$)", href, re.I)
    target = str(info.get("link_target") or "").strip().lower()
    new_window = real_link and target not in ("", "_self", "_top", "_parent")
    if new_window or _OPEN_CODE.search(src):
        return "opens"
    if real_link:
        return "goes"
    return ""


def click_route(info: dict[str, Any] | None) -> str:
    """"" = only looking (nothing to ask, no judge call); "judge" = the step judge says what the click does (docs/click-judge.md 1-2).
    None (the element could not be read) is judged."""
    if info is None:
        return "judge"
    if not pressable(info):
        return ""
    how = structure(info)
    if how in ("goes", "opens"):
        return ""
    if how == "changes":
        return "judge"
    tag, ftype, role = str(info.get("tag") or ""), str(info.get("type") or ""), str(info.get("role") or "")
    if not str(info.get("handler_src") or "").strip() and info.get("structure_read"):
        # nothing runs on a press: a field, a tick box, a list choice, a tab or a link by role changes only the page's own state
        if tag in _CONTROL_TAGS or (tag == "input" and ftype in _FIELD_TYPES) or role in _CONTROL_ROLES or role in _NAV_ROLES:
            return ""
    return "judge"


def kind_of_verdict(verdict: Any, info: dict[str, Any] | None) -> str:
    """The commit kind of a judged click or Enter: the judge's strongest flag, "" when it found nothing that commits (an ordinary form
    submit runs without a card, whatever the form holds); "unknown" (always asks) when there was no verdict."""
    if verdict is None:
        return "unknown"
    return verdict.kind or ""


_PRESSABLE_TAGS = frozenset({"a", "button", "input", "select", "textarea", "summary", "label", "option"})
_PRESSABLE_ROLES = frozenset({"button", "link", "tab", "menuitem", "menuitemradio", "menuitemcheckbox", "option", "checkbox", "switch",
                              "radio", "combobox", "treeitem"})


def pressable(info: dict[str, Any] | None) -> bool:
    """The element is something one presses (a button, a link, a field, a control by role, one with click code, or a pointer
    cursor), not plain text on the page (2026-11-10: a selector text=<the page's instruction sentence> picked the sentence itself)."""
    if not info:
        return False
    if not info.get("structure_read"):
        return True   # read the older way (tests' pages): judged as before
    return (str(info.get("tag") or "") in _PRESSABLE_TAGS or str(info.get("role") or "") in _PRESSABLE_ROLES
            or bool(str(info.get("handler_src") or "").strip()) or str(info.get("cursor") or "") == "pointer")


def label_of(info: dict[str, Any] | None, selector: str) -> str:
    if info is None:
        return selector_words(selector)
    text, words = str(info.get("text") or ""), str(info.get("words") or "")
    return f"{text} {words}".strip() if len(text) <= 60 else words


def coupon_box(info: dict[str, Any] | None) -> bool:
    """A control that is not a submit, inside a form that holds a typed text field: the box that applies a code to the order (by
    structure; whether it changes anything is the step judge's call)."""
    return bool(info) and bool(info.get("in_form")) and not info.get("submits") and int(info.get("form_text") or 0) > 0 \
        and str(info.get("tag") or "") in ("button", "input", "a")


def judge_type(info: dict[str, Any] | None, selector: str, text: str, submit: bool = False) -> str:
    """The commit kind of typing `text` into this field: "personal" only where typing touches money or a sign-in, else "" (no card).
    Typing an e-mail address, a phone number, a name or an address asks nothing (what is typed never asks; the privacy guard still
    looks at the text for the owner's own details, and the send or submit step is judged by what it does). Asks for: a card number by
    VALUE (13-19 digits passing Luhn: payments go through checkout / the payment specialist) and a password field (input type
    password, or autocomplete current-password / new-password / one-time-code: sign-ins go through browser_login and the vault).
    `submit` (Enter) is judged like a click by the caller, not here."""
    digits = re.sub(r"[\s-]", "", text or "")
    if digits.isdigit() and 13 <= len(digits) <= 19 and _luhn(digits):
        return "personal"
    if info is None:
        return ""
    if str(info.get("type") or "") == "password" or _SECRET_AUTOCOMPLETE.search(str(info.get("autocomplete") or "")):
        return "personal"
    return ""
