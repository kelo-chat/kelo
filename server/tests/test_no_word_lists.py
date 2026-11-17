"""Hygiene (docs/no-word-lists.md, 2026-11-16): the modules whose decisions used to come from lists of human words contain none.

Checked on the code, not the prose: string literals outside docstrings must not carry letters of any script other than ASCII (no Hangul,
kana, Cyrillic ...), regex patterns must not alternate English words, and a literal set or tuple of four or more plain words is refused.
"""

from __future__ import annotations

import ast
import re
import unicodedata
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "assistant"
GUARDED = ["connector_rules.py", "channels/rooms.py", "tools/order.py", "outside_rule.py", "tools/commits.py", "tools/task_permission.py", "android/judge.py"]
# Cleaned in c-hardcode-mid (docs/no-word-lists.md, table "MEDIUM and LOW items"): no word of a human language decides anything there.
# Left out on purpose: offers.py (its model prompts
# carry Korean examples, which prompts may), model_limits.py (it reads a reset TIME out of a CLI's own phrase; the decision is by status).
GUARDED += ["big_events.py", "tools/login_code.py", "tools/sites.py", "agent/found_secrets.py", "egress.py", "netguard.py", "vpn.py", "keyring_client.py",
            "ai_consent.py", "resume.py", "compaction.py", "mail.py", "mailboxes.py", "presets.py", "buy_ready.py", "goals.py", "android/signin.py",
            "follow/items.py", "follow/runner.py", "follow/sources/parcel.py", "tools/challenge.py", "nightly_review.py", "agent/loop.py", "event_kinds.py",
            "receipt.py", "buy_replay.py", "done_once.py"]
# hc-actions (2026-11-17): H1, H2, H3, H7
GUARDED += ["effects.py", "schedule_grants.py", "watchers.py", "tool_judge.py"]
# hc-injection (2026-11-17): H13
GUARDED += ["injection_watch.py"]
# hc-privacy (2026-11-17): H10, H11, H12
GUARDED += ["privacy_guard.py", "secret_guard.py", "memory_upkeep.py"]
# hc-pagefields (2026-11-17): H14, H17, H18, H19, H20, M1, M2, M5 (page_judge.py reads the text for them; the browser.py field words are
# covered by test_the_browser_page_field_word_lists_are_gone)
GUARDED += ["tools/payment_step.py", "tools/verification.py", "tools/keypad.py", "tools/vault_tools.py", "tools/takeover.py",
            "tools/page_notes.py", "signin_state.py"]
# hc-rest (2026-11-17): M8, L5, M6, L2, H21 (the per-service rules are data in content/forbidden_rules.json)
# (agent/toolbox.py, reflection.py, followups.py, recheck.py and proactive.py hold state and field names or the stored Korean urgency values: not guarded)
GUARDED += ["mcp_oauth.py", "scope_pick.py", "recall.py", "notify_block.py", "forbidden.py"]
# hc-final-a: the cookie advice by typed need (workers), the secure-field and countdown and window probes by structure (chat_images, payment_step, page_notes)
GUARDED += ["workers.py", "chat_images.py"]
REGEX_CALLS = {"compile", "search", "match", "fullmatch", "sub", "findall", "finditer", "split"}
WORD = re.compile(r"[A-Za-z]{3,}")
# Not human language: the names of a standard or of code. HTML tags, input types and ARIA roles, the WHATWG autocomplete tokens, JavaScript
# and Playwright syntax, and the commit kinds of tools/commits.py.
STANDARD = frozenset("""
a button input select option textarea summary label link menuitem menuitemradio menuitemcheckbox tab treeitem checkbox switch radio
combobox listbox searchbox search text email tel number date time month week range color url password file submit image
datetime-local name given-name family-name additional-name honorific-prefix impp street-address postal-code country country-name
bday username new-password current-password one-time-code sex organization organization-title post ajax get assign replace has-text
text-is aria-label title value alt placeholder request
apply send delete optout account security form personal pay unknown
# hc-final-a: the job status values of workers.py (stored codes, not words of a language)
running blocked stopped interrupted failed
rnd ref referrer src source time token sid cache random ver fbclid gclid dclid msclkid requestid nonce timestamp idempotencykey bcc uids attendees recipients null none true false
flight stay trip appointment booking asia aiza npm xai read unread flag unflag fail softfail permerror policy mon tue wed thu github glpat xox akia added resolved dropped open site path selector
ghp gho ghs ghu method tool package provider done skipped failed pending status code tidied files trigger stats
""".split()) | frozenset("""
area base col embed img meta source track wbr param script style noscript template svg head div section article table header footer main nav aside
tag attrs children parent kind mode match regex threshold code method body headers note once
override exfil money marker leak
""".split())   # HTML element names, and the field names of the server's own watcher schema (watchers.py): names of code, not human words


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _literal_strings(tree: ast.AST):
    docs = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node


def _is_regex_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not node.args:
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in REGEX_CALLS


def _alternatives_of_words(pattern: str) -> list[str]:
    """Every alternative of every group of the pattern that is a plain English-looking word or a run of such words."""
    found: list[str] = []
    for group in re.findall(r"\(\?:([^()]*)\)", pattern):
        for alt in group.split("|"):
            if "|" in group and WORD.fullmatch(re.sub(r"\\b|[\s-]", "", alt)) is not None and alt not in STANDARD:
                found.append(alt)
    return found


@pytest.mark.parametrize("rel", GUARDED)
def test_no_literal_carries_a_letter_of_a_non_latin_script(rel):
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    bad = []
    for node in _literal_strings(tree):
        for ch in node.value:
            if ord(ch) > 127 and unicodedata.category(ch).startswith("L"):
                bad.append((node.lineno, node.value[:60]))
                break
    assert bad == [], f"{rel} still has words of another language in code: {bad[:5]}"


@pytest.mark.parametrize("rel", GUARDED)
def test_no_regex_alternates_english_words(rel):
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if _is_regex_call(node) and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            alts = _alternatives_of_words(node.args[0].value)
            if alts:
                bad.append((node.lineno, alts[:3]))
    assert bad == [], f"{rel} has regex word alternations: {bad[:5]}"


@pytest.mark.parametrize("rel", GUARDED)
def test_no_literal_list_of_four_or_more_plain_words(rel):
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        elts = None
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            elts = node.elts
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("frozenset", "set") and node.args:
            elts = node.args[0].elts if isinstance(node.args[0], (ast.Tuple, ast.List, ast.Set)) else None
        if elts is None:
            continue
        words = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str) and re.fullmatch(r"[a-z]{3,}", e.value)
                 and e.value not in STANDARD]
        if len(words) >= 4:
            bad.append((node.lineno, words[:4]))
    assert bad == [], f"{rel} holds a word list: {bad[:5]}"


def test_the_guarded_modules_exist():
    for rel in GUARDED:
        assert (SRC / rel).is_file(), rel


def test_the_click_word_lists_are_gone():
    from assistant.tools import commits
    for name in ("judge_words", "judge_click", "continue_only", "keep_signed_in", "asks_to_keep_signed_in", "security_step", "cancel_words",
                 "coupon_words", "is_entry", "_KO_NAV", "_EN_NAV", "_SECURITY_PAGE", "_SETTINGS_PAGE", "_DELEGATION", "_DOC_WORD",
                 "_DOC_ISSUE", "_NOT_A_DOCUMENT", "_GOV_PAGE", "_MESSAGE_FIELD", "_PERSONAL_FIELD", "_CANCEL_ORDER", "_NOT_AN_ORDER",
                 "_NEW_WINDOW_WORDS", "_COUPON", "_SEARCH_FIELD", "_SEARCH_NAMES", "_PLAIN_YES", "_SIGN_UP"):
        assert not hasattr(commits, name), name


# ------------------------------------------------------------------------------------------------------------------ JavaScript strings
# hc-challenge-probe (2026-11-17): a page script held in a Python string is code too. Its literals (quoted strings and regex literals; not
# identifiers, not the DOM API) must carry no word of a human language, apart from STANDARD and the names below: CSS and DOM values,
# the markup that bot-check scripts and WHATWG forms define. Those are names of a standard or of a vendor's element, not language.
JS_STANDARD = frozenset("""
iframe frame frames style visibility hidden display opacity input numeric decimal data sitekey class id div span canvas object
embed checkbox radio disabled readonly aria invalid alert role timer dialog autocomplete inputmode maxlength href type reset testid
click onclick change dismiss close btn closebtn
if for while switch return function typeof new javascript https mailto native target not use off
onmousedown onmouseup onpointerup ontouchend tabindex contenteditable alertdialog command webauthn address-line address-level
pointer center fixed sticky before after overlay aria-modal cc-csc cc-exp cc-number webkit-text-security
protected field
challenge running stage form browser verification cloudflare please wait turnstile recaptcha hcaptcha widget captcha holder panel box
geetest arkose funcaptcha awswaf aws waf ddv container datadome ddv1 kelo true false address
# (the JS keywords, DOM events and CSS values above, the kind values of page_notes (dialog, overlay), and the names of the server's own
# result fields are names of code, not language. hc-final-a: class and id words (cookie, consent, modal, layer, countdown) are gone from the
# scripts, so they are no longer allowed here)
""".split())


def _js_literals(js: str):
    """The quoted strings and regex literals of a JavaScript text (a small scanner, enough for the page scripts of this server)."""
    i, n, prev = 0, len(js), ""
    while i < n:
        c = js[i]
        if js.startswith("//", i):
            i = js.find("\n", i) if "\n" in js[i:] else n
            continue
        if js.startswith("/*", i):
            j = js.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and js[j] != c:
                j += 2 if js[j] == "\\" else 1
            yield js[i + 1:j]
            i, prev = j + 1, c
            continue
        if c == "/" and prev in ("", "(", ",", "=", ">", ":", "!", "&", "|", "?", "{", ";", "}", "[", "+") :
            j = i + 1
            while j < n and js[j] != "/" and js[j] != "\n":
                if js[j] == "[":
                    while j < n and js[j] != "]":
                        j += 2 if js[j] == "\\" else 1
                j += 2 if js[j] == "\\" else 1
            yield js[i + 1:j]
            i, prev = j + 1, "/"
            continue
        if not c.isspace():
            prev = c
        i += 1


def _js_words(js: str) -> list[str]:
    words = []
    for lit in _js_literals(js):
        lit = re.sub(r"kelo-[a-z-]+", " ", lit)                           # the server's own marker attributes (data-kelo-*): names of code
        for chunk in re.findall(r"[A-Za-z][A-Za-z-]*[A-Za-z]|[A-Za-z]{3,}", re.sub(r"\\[A-Za-z]", " ", lit)):
            if chunk.lower() in STANDARD or chunk.lower() in JS_STANDARD:    # a whole standard name such as one-time-code, given-name
                continue
            for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", chunk):   # words of a hyphenated or camelCase name
                if len(part) >= 3 and part.lower() not in STANDARD and part.lower() not in JS_STANDARD:
                    words.append(part.lower())
    return words


def _js_strings(tree: ast.AST):
    """The string constants of a module that are page scripts: an arrow function or `function` text, outside docstrings."""
    for node in _literal_strings(tree):
        if "=>" in node.value or "function" in node.value:
            yield node


def test_the_js_scanner_finds_words_in_literals_and_not_in_identifiers():
    assert _js_words("() => document.querySelectorAll('input').filter((e) => /otp|verif\\w*.?code|2fa/.test(e.name))") == ["otp", "verif"]
    assert _js_words("() => { const searching = 1; return e.getBoundingClientRect(); }") == []
    assert _js_words("() => /search|query|keyword/.test(x)") == ["query", "keyword"]    # `search` is a STANDARD input type


@pytest.mark.parametrize("rel", GUARDED)
def test_no_page_script_literal_carries_a_word(rel):
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    bad = [(n.lineno, _js_words(n.value)[:6]) for n in _js_strings(tree) if _js_words(n.value)]
    assert bad == [], f"{rel} has words in a page script (decide by structure, docs/no-word-lists.md): {bad[:5]}"
