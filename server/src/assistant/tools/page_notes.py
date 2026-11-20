"""Two things about a page the agent reads that its text alone hides (used by browser.py's snapshot; never site-specific, and no word of any
language decides anything: docs/no-word-lists.md, hc-pagefields):

Windows covering the page. A notice window or any fixed layer over the middle of the page (role=dialog, aria-modal, <dialog open>,
a high fixed element under the viewport's center) is found by OVERLAY_JS, which also marks its own close control
(its centre is returned as close_at, nothing is marked) when it can tell by STRUCTURE: an icon-only control (no words) that is declared a close control (command=close,
data-dismiss, data-bs-dismiss) or drawn in the top corner of the layer or of its sheet. A control that has words
is never pressed (nothing can tell "Close" from "Accept" or "Don't show again" in a language the code does not read), nor anything that
could commit. Windows with form fields (a sign-in or search form) or an embedded frame (payment, verification, a human check), any
window on a checkout or payment page and windows the agent's own step opened are left alone. With no safe close control the window is
reported, and the agent reads it and decides.

Values that are masked or not loaded. "0*", "1,2**", "●●●●", "$--": the text shows a value hidden or still loading, and the agent must
not state it as a fact. masked_values() finds such spots and masked_note() is the line added to the page reading.
"""

from __future__ import annotations

import re
from typing import Any

from .page_paths import with_path

OVERLAY_ROUNDS = 3        # windows closed one after another on one reading (a notice over another)
LABEL_CHARS = 160         # how much of a window's text the note quotes

# Returns {"kelo_overlay": 1, "found": [{"kind": dialog|overlay, "label", "close": index|null, "close_label", "skipped": [..]}]}
OVERLAY_JS = r"""() => {
  const kelo_overlay = 1;
  const W = window.innerWidth, H = window.innerHeight, body = document.body;
  if (!body || !W || !H) return { kelo_overlay, found: [] };
  // The layer's controls are found by STRUCTURE only (docs/no-word-lists.md): no word of any language is read. A control closes the layer
  // when the HTML or the framework says so (a submit button of <form method=dialog>, command=close, data-dismiss / data-bs-dismiss),
  // or when it is an icon-only control (no text, no label) drawn in the top corner of the layer or of its sheet. A control with words
  // ("Close", "Accept", "Don't show again", a cookie choice) is NEVER pressed: the layer is reported and the agent reads it.
  // Names of classes and ids decide nothing (hc-final-a): a dialog and a close control are told by geometry, roles and attributes.
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && c.visibility !== 'hidden' && c.display !== 'none' && Number(c.opacity) > 0.05
      && r.bottom > 0 && r.right > 0 && r.top < H && r.left < W; };
  const zOf = (e) => { let z = 0; for (let x = e; x && x !== body; x = x.parentElement) { const v = parseInt(getComputedStyle(x).zIndex, 10); if (v > z) z = v; } return z; };
  const fixedRoot = (e) => { let top = null; for (let x = e; x && x !== body && x !== document.documentElement; x = x.parentElement) { if (getComputedStyle(x).position === 'fixed') top = x; } return top; };
  const roots = [];
  document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open]').forEach((e) => { if (shown(e)) roots.push(e); });
  const bodyText = (body.innerText || '').length;
  for (const e of document.elementsFromPoint(W / 2, H / 2)) {
    const f = fixedRoot(e);
    if (!f) continue;
    const r = f.getBoundingClientRect();
    if (shown(f) && r.width * r.height >= 0.12 * W * H && zOf(f) >= 10 && (f.innerText || '').length < 0.7 * Math.max(bodyText, 1)) roots.push(f);
    break;
  }
  // A bar along an edge (a consent bar, a site's own header or tab bar) is NOT a layer: by geometry a consent bar and a sticky header
  // with a cart icon in its corner look alike, and pressing that icon would be a wrong click. The agent reads such a bar in the page.
  const outer = [...new Set(roots)].filter((e, _i, all) => !all.some((o) => o !== e && o.contains(e)));
  const found = [];
  let n = 0;
  for (const root of outer.slice(0, 4)) {
    const text = (root.innerText || '').replace(/\s+/g, ' ').trim();
    const fields = [...root.querySelectorAll('input,select,textarea')].filter((i) => shown(i) && !/^(?:checkbox|radio|hidden|button|submit|image|reset)$/i.test(i.type || ''));
    if (fields.length) continue;   // a sign-in, search or picker form the agent is using
    if ([...root.querySelectorAll('iframe')].some(shown)) continue;   // a payment, verification or other embedded window
    const head = root.querySelector('h1,h2,h3,h4');
    const label = (root.getAttribute('aria-label') || (head && head.innerText) || text).replace(/\s+/g, ' ').trim().slice(0, 160);
    let best = null, rank = 0;
    const skipped = [];
    const rr = root.getBoundingClientRect();
    const share = (rr.width * rr.height) / (W * H);
    // a sheet: the biggest child, smaller than the layer (the layer around it is a backdrop)
    let sheet = null;
    for (const k of root.children) { const kr = k.getBoundingClientRect(); if (!sheet || kr.width * kr.height > sheet.width * sheet.height) sheet = kr; }
    if (sheet && sheet.width * sheet.height >= 0.9 * rr.width * rr.height) sheet = null;
    // an "open or install the app" layer by structure: a link whose scheme is not a web address (intent:, market:, itms-apps: ...)
    const app = share >= 0.6 && [...root.querySelectorAll('a[href]')].some((c) => shown(c) && !/^(?:https?:|#|javascript:|mailto:|tel:)/i.test(c.getAttribute('href') || ''));
    const declared = (c) => c.matches('[command=close],[data-dismiss],[data-bs-dismiss]');
    for (const c of root.querySelectorAll('button,[role=button],a,input[type=button],input[type=submit],[onclick],[command],[data-dismiss],[data-bs-dismiss]')) {
      if (!shown(c)) continue;
      const words = ((c.innerText || c.value || '') + '').replace(/\s+/g, ' ').trim();
      if (words && !/^[\u00d7\u2715\u2716\u2573\u2a2f\u2717]$/.test(words)) continue;   // a control with words is never pressed (no word decides what it does)
      const href = c.tagName === 'A' ? (c.getAttribute('href') || '') : '';
      if (href && !/^(?:#|javascript:)/i.test(href)) continue;   // a link to somewhere is not a close control
      if (declared(c) && 3 > rank) { best = c; rank = 3; }
    }
    if (rank < 2) {
      // an icon-only close control (a cross drawn by an svg, an image or CSS, no words, no label): small, in the top corner of the
      // layer (or of its sheet), nearest to it. Never a link away (an "open in the app" or store link), never a control that talks
      // about the app. A control in a LEFT corner only counts on an app layer (elsewhere that is often a back arrow or a menu).
      const refs = [rr].concat(sheet ? [sheet] : []);
      const seen = new Set();
      let bestD = 1e9;
      for (const e of root.querySelectorAll('button,[role=button],a,[onclick],[tabindex],svg,img,i,span,div')) {
        let c = e;
        if (/^(?:svg|img|i|span)$/i.test(e.tagName) || (e.tagName === 'DIV' && !e.getAttribute('onclick') && !e.getAttribute('tabindex'))) {
          c = e.closest('button,[role=button],a,[onclick],[tabindex]') || (e.tagName === 'DIV' ? null : e.parentElement);
        }
        if (!c || c === root || seen.has(c) || !root.contains(c) || !shown(c)) continue;
        seen.add(c);
        if (((c.innerText || c.value || '') + '').trim()) continue;
        const hint = [c.getAttribute('aria-label'), c.getAttribute('title')].filter(Boolean).join(' ');
        if (hint && !declared(c)) continue;   // a labelled icon control (a menu, a back arrow, a share) is only taken when it is declared a close control
        const link = c.closest('a[href]');
        if (link && !/^(?:#|javascript:)/i.test(link.getAttribute('href') || '')) continue;   // intent://, market://, a store or app address
        const cr = c.getBoundingClientRect();
        if (cr.width > 72 || cr.height > 72 || cr.width < 8 || cr.height < 8) continue;
        const cs = getComputedStyle(c);
        const glyph = c.matches('svg,img') || c.querySelector('svg,img,i,b,span,path,use') || (getComputedStyle(c, '::before').content || 'none') !== 'none'
          || (getComputedStyle(c, '::after').content || 'none') !== 'none' || (cs.backgroundImage || 'none') !== 'none';
        if (!glyph) continue;
        const cx = cr.left + cr.width / 2, cy = cr.top + cr.height / 2;
        for (const ref of refs) {
          if (cy - ref.top > 0.25 * ref.height || cy < ref.top - 4) continue;
          const dl = cx - ref.left, dr = ref.right - cx;
          if (dr > 0.25 * ref.width && !(app && dl <= 0.25 * ref.width)) continue;
          const d = Math.min(dl, dr) + (cy - ref.top);
          if (d < bestD) { bestD = d; best = c; rank = 2.5; }
        }
      }
    }
    const item = { kind: root.matches('[role=dialog],[role=alertdialog],[aria-modal=true],dialog') ? 'dialog' : 'overlay', label, close: null, close_label: '', skipped, app,
      backdrop: (app && sheet) ? [Math.max(rr.left + 4, 4), Math.max(rr.top + 4, 4)] : null };
    if (item.backdrop && sheet && item.backdrop[0] >= sheet.left && item.backdrop[0] <= sheet.right && item.backdrop[1] >= sheet.top && item.backdrop[1] <= sheet.bottom) item.backdrop = null;
    if (best) {
      // no mark in the page (addendum 9): the close control is pressed by a person's click or tap at its centre (browser.py _close)
      const cb = best.getBoundingClientRect();
      item.close = ++n;
      item.close_at = [Math.round((cb.left + cb.width / 2) * 10) / 10, Math.round((cb.top + cb.height / 2) * 10) / 10];
      item.close_label = ((best.innerText || best.value || '').trim() || best.getAttribute('aria-label') || best.getAttribute('title') || 'X').slice(0, 40);
    }
    found.push(item);
    if (best) break;   // one at a time: the page is looked at again after each close
  }
  return { kelo_overlay, found };
}"""

# The labels of the windows open over the page now: a dialog by role or by the HTML element, or a fixed layer over the middle of the screen
# (the same structure OVERLAY_JS uses; no word decides). Read before and after a click: a new one means the click opened a window on the
# page; one that is gone means the click closed it.
LAYERS_JS = r"""() => {
  const W = window.innerWidth, H = window.innerHeight, body = document.body;
  if (!body || !W || !H) return [];
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && c.visibility !== 'hidden' && c.display !== 'none' && Number(c.opacity) > 0.05; };
  const zOf = (e) => { let z = 0; for (let x = e; x && x !== body; x = x.parentElement) { const v = parseInt(getComputedStyle(x).zIndex, 10); if (v > z) z = v; } return z; };
  const roots = [...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open]')].filter(shown);
  const bodyText = (body.innerText || '').length;
  for (const e of document.elementsFromPoint(W / 2, H / 2)) {
    let f = null;
    for (let x = e; x && x !== body && x !== document.documentElement; x = x.parentElement) { if (getComputedStyle(x).position === 'fixed') f = x; }
    if (f && shown(f)) {
      const r = f.getBoundingClientRect();
      if (r.width * r.height >= 0.12 * W * H && zOf(f) >= 10 && (f.innerText || '').length < 0.7 * Math.max(bodyText, 1)) roots.push(f);
    }
    break;
  }
  const outer = [...new Set(roots)].filter((e, _i, all) => !all.some((o) => o !== e && o.contains(e)));
  return outer.slice(0, 4).map((root) => { const head = root.querySelector('h1,h2,h3,h4');
    return ((root.getAttribute('aria-label') || (head && head.innerText) || root.innerText || '') + '').replace(/\s+/g, ' ').trim().slice(0, 120); });
}"""

# For the element about to be clicked: the window it sits in (its label, as LAYERS_JS gives it) and CSS paths of up to 4 other pressable
# controls of that window (read with TARGET_JS and judged before the click). null when the element is in no window.
LAYER_CHOICES_JS = with_path(r"""(el) => {
  const body = document.body, SEL = '[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open]';
  let root = el.closest(SEL);
  while (root && root.parentElement && root.parentElement.closest(SEL)) root = root.parentElement.closest(SEL);
  if (!root) { for (let x = el; x && x !== body && x !== document.documentElement; x = x.parentElement) { if (getComputedStyle(x).position === 'fixed') root = x; } }
  if (!root) return null;
  const head = root.querySelector('h1,h2,h3,h4');
  const label = ((root.getAttribute('aria-label') || (head && head.innerText) || root.innerText || '') + '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const act = el.closest('button, a[href], [role=button], [role=link], input, [onclick]') || el;
  const paths = [];
  for (const c of root.querySelectorAll('button, a[href], [role=button], [role=link], input[type=button], input[type=submit], [onclick]')) {
    if (c === act || c.contains(act) || act.contains(c)) continue;
    const r = c.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) continue;
    const p = keloPath(c);
    if (p && document.querySelectorAll(p).length === 1) paths.push(p);
    if (paths.length >= 4) break;
  }
  return { label, paths };
}""")


def overlays(probe: Any) -> list[dict[str, Any]]:
    """The windows of an OVERLAY_JS reading ([] for anything else, e.g. a page that could not be read)."""
    if not isinstance(probe, dict) or not probe.get("kelo_overlay"):
        return []
    out = []
    for f in probe.get("found") or []:
        if isinstance(f, dict) and isinstance(f.get("label"), str):
            close = f.get("close")
            back = f.get("backdrop")
            at = f.get("close_at")
            item = {"kind": str(f.get("kind") or "overlay"), "label": f["label"][:LABEL_CHARS], "app": bool(f.get("app")),
                    "backdrop": [float(back[0]), float(back[1])] if isinstance(back, list) and len(back) == 2 else None,
                    "close": close if isinstance(close, int) and not isinstance(close, bool) else None,
                    "close_label": str(f.get("close_label") or "")[:40],
                    "skipped": [str(s)[:40] for s in (f.get("skipped") or []) if isinstance(s, str)][:2]}
            if item["close"] is not None and isinstance(at, list) and len(at) == 2:   # the close control's centre (CSS px)
                item["close_at"] = [float(at[0]), float(at[1])]
            out.append(item)
    return out


def signature(f: dict[str, Any]) -> str:
    return f"{f['kind']}:{f['label'][:60]}"


KIND_WORDS = {"dialog": "A pop-up window", "overlay": "A layer"}


def closed_note(f: dict[str, Any], quote: Any) -> str:
    """`quote` marks page words as data (fence.data)."""
    skipped = f" (not its {quote(' / '.join(f['skipped']))} option)" if f["skipped"] else ""
    return (f"[{KIND_WORDS.get(f['kind'], 'A layer')} covered the page: {quote(f['label'])}. Closed it with its own "
            f"{quote(f['close_label'])} button{skipped}; the text below is the page behind it.]")


def open_note(f: dict[str, Any], quote: Any, *, tried: bool = False) -> str:
    why = "its close button did not close it" if tried else "it has no plain close button"
    return (f"[{KIND_WORDS.get(f['kind'], 'A layer')} still covers the page: {quote(f['label'])}; {why}, so it was left open. Read "
            "its text below; close it yourself only with a control that just closes it, never a \"don't show again\" one unless the "
            "user asked.]")


# ---------------------------------------------------------------------------------------------------------- masked values
MASK = "*＊●•∙◦○"
_MASKED = re.compile(
    rf"(?<![\w{MASK}])[$€£¥₩]?\s?\d[\d,.]*[{MASK}]+[\d,.{MASK}]*(?:\s?[^\W\d_]{{1,4}}|\s?[$€£¥₩%])?"   # 0*, 1,2**, 40,0** (a unit letter or symbol after it is allowed)
    rf"|[{MASK}]{{3,}}(?:[\s,.-]?[\d{MASK}]+)*"                                                       # ●●●●, ***, ****-1234
    r"|(?<![\w-])[$€£¥₩]\s?-{1,3}(?![\w-])|(?<![\w-])-{1,3}\s?[$€£¥₩](?!\w)"         # $--, --$
)


def masked_values(text: str, limit: int = 3) -> list[str]:
    """Spots in page text that show a value masked or not loaded, each with a few words before it ("예치금 0* 원")."""
    out: list[str] = []
    for m in _MASKED.finditer(text or ""):
        line_start = text.rfind("\n", 0, m.start()) + 1
        before = text[max(line_start, m.start() - 24):m.start()]
        spot = re.sub(r"\s+", " ", (before + m.group(0))).strip()
        if spot and spot not in out:
            out.append(spot[-48:])
        if len(out) >= limit:
            break
    return out


def masked_note(spots: list[str], quote: Any) -> str:
    return ("[Some values on this page look masked, partial or not loaded yet: " + ", ".join(quote(s) for s in spots)
            + ". Never state them as facts or as checked. Find where the value shows in full (another panel, menu, tab or the page "
            "that lists it) or read the page again once it has loaded; if you cannot, tell the user it was hidden.]")


def not_found_note(status: int) -> str:
    return (f"[This address answered {status} (not found): the page does not exist, so nothing was checked here. Do not guess "
            "addresses; open the site's home page and follow its own links, menu or search.]")
