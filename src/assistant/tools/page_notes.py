"""Two things about a page the agent reads that its text alone hides (used by browser.py's snapshot; never site-specific):

Windows covering the page. A notice window ("오늘 그만 보기 / 닫기"), a cookie banner or any fixed layer over the middle of the
page (role=dialog, aria-modal, <dialog open>, a high fixed element under the viewport's center) is found by OVERLAY_JS, which
also marks its own close control (data-kelo-close). Only plain close controls count ("닫기", "Close", "×", an icon button labelled
close; for a cookie banner also "reject" / "necessary only"); a "don't show again" / "오늘 그만 보기" control is never used, nor
anything that could commit (OK, confirm, accept, subscribe). Windows with form fields (a sign-in or search form) or an embedded
frame (payment, verification), human checks, any window on a checkout or payment page and windows the agent's own step opened are
left alone. With no safe close control the window is reported, not guessed at.

Values that are masked or not loaded. "예치금 0* 원", "1,2**원", "●●●●", "$--": the text shows a value hidden or still loading, and
the agent must not state it as a fact. masked_values() finds such spots and masked_note() is the line added to the page reading.
"""

from __future__ import annotations

import re
from typing import Any

OVERLAY_ROUNDS = 3        # windows closed one after another on one reading (a notice over a cookie banner)
LABEL_CHARS = 160         # how much of a window's text the note quotes

# Returns {"kelo_overlay": 1, "found": [{"kind": dialog|overlay|cookie, "label", "close": index|null, "close_label", "skipped": [..]}]}
OVERLAY_JS = r"""() => {
  const kelo_overlay = 1;
  const W = window.innerWidth, H = window.innerHeight, body = document.body;
  if (!body || !W || !H) return { kelo_overlay, found: [] };
  const CLOSE = /^(?:닫기|창\s*닫기|팝업\s*닫기|close|close\s+(?:this\s+)?(?:dialog|window|popup|pop-up|modal|banner|notice|ad)|dismiss|no,?\s*thanks|not\s+now|maybe\s+later|나중에|괜찮아요|×|✕|✖|╳|x|閉じる|关闭|關閉|schließen|fermer|cerrar|chiudi|fechar|sluiten|zamknij|закрыть|kapat|tutup|đóng|ปิด|إغلاق|बंद\s*करें)$/i;
  const CLOSE_HINT = /(?:^|[\s_-])(?:close|닫기|dismiss)(?:$|[\s_-])|^(?:close|닫기|dismiss)/i;
  const CLOSE_CLASS = /(?:^|[\s_-])(?:btn[-_]?close|close[-_]?btn|closebtn|close[-_]?button|modal[-_]?close|popup[-_]?close|layer[-_]?close|close)(?:$|[\s_-])/i;
  const COOKIE_CLOSE = /^(?:reject(?:\s+all)?|decline(?:\s+all)?|refuse(?:\s+all)?|necessary\s+(?:cookies\s+)?only|only\s+(?:necessary|essential)(?:\s+cookies)?|essential\s+(?:cookies\s+)?only|use\s+necessary\s+cookies\s+only|필수\s*(?:쿠키만|항목만|만)\s*(?:허용|사용)?|거부|모두\s*거부)$/i;
  const NEVER = /오늘|하루|그만\s*보|다시\s*보지|보지\s*않|열지\s*않|일주일|\d+\s*일\s*(?:간|동안)|don'?t\s+show|do\s+not\s+show|never\s+show|not\s+show|show\s+again|hide\s+for|for\s+(?:today|\d+\s+days?)|今日|今天|不再|nicht\s+mehr|ne\s+plus/i;
  const COOKIE = /cookie|쿠키|consent|gdpr|クッキー/i;
  const CHECK = /captcha|recaptcha|hcaptcha|turnstile|challenge|verify\s+you\s+are\s+human|i'?m\s+not\s+a\s+robot|자동\s*입력\s*방지|보안\s*문자|사람인지/i;
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
  document.querySelectorAll('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],[aria-label*=cookie i]').forEach((e) => {
    const f = fixedRoot(e) || (getComputedStyle(e).position === 'sticky' ? e : null);
    if (f && shown(f) && COOKIE.test(f.innerText || '')) roots.push(f);
  });
  const outer = [...new Set(roots)].filter((e, _i, all) => !all.some((o) => o !== e && o.contains(e)));
  const found = [];
  let n = 0;
  for (const root of outer.slice(0, 4)) {
    const text = (root.innerText || '').replace(/\s+/g, ' ').trim();
    if (CHECK.test(text) || [...root.querySelectorAll('iframe')].some((f) => CHECK.test(f.src || '') || CHECK.test(f.title || ''))) continue;
    const fields = [...root.querySelectorAll('input,select,textarea')].filter((i) => shown(i) && !/^(?:checkbox|radio|hidden|button|submit|image|reset)$/i.test(i.type || ''));
    if (fields.length) continue;   // a sign-in, search or picker form the agent is using
    if ([...root.querySelectorAll('iframe')].some(shown)) continue;   // a payment, verification or other embedded window
    const cookie = COOKIE.test(text) && !root.matches('[aria-modal=true],dialog');
    const head = root.querySelector('h1,h2,h3,h4,[class*=title i]');
    const label = (root.getAttribute('aria-label') || (head && head.innerText) || text).replace(/\s+/g, ' ').trim().slice(0, 160);
    let best = null, rank = 0;
    const skipped = [];
    for (const c of root.querySelectorAll('button,[role=button],a,input[type=button],input[type=submit],[aria-label],[title],[onclick],[class*=close i],[id*=close i]')) {
      if (!shown(c)) continue;
      const words = ((c.innerText || c.value || '') + '').replace(/\s+/g, ' ').trim();
      const hint = [c.getAttribute('aria-label'), c.getAttribute('title'), ...[...c.querySelectorAll('img[alt]')].map((i) => i.alt)].filter(Boolean).join(' ');
      if (NEVER.test(words) || NEVER.test(hint)) { if (skipped.length < 2) skipped.push((words || hint).slice(0, 40)); continue; }
      const href = c.tagName === 'A' ? (c.getAttribute('href') || '') : '';
      if (href && !/^(?:#|javascript:)/i.test(href)) continue;   // a link to somewhere is not a close control
      let r = 0;
      if (CLOSE.test(words) || (cookie && COOKIE_CLOSE.test(words))) r = 3;
      else if (!words && CLOSE_HINT.test(hint)) r = 2;
      else if (!words && !hint && CLOSE_CLASS.test(((c.className && c.className.baseVal !== undefined) ? c.className.baseVal : c.className || '') + ' ' + (c.id || ''))) r = 1;
      if (r > rank) { best = c; rank = r; }
    }
    const item = { kind: cookie ? 'cookie' : (root.matches('[role=dialog],[role=alertdialog],[aria-modal=true],dialog') ? 'dialog' : 'overlay'), label, close: null, close_label: '', skipped };
    if (best) {
      document.querySelectorAll('[data-kelo-close]').forEach((x) => x.removeAttribute('data-kelo-close'));
      best.setAttribute('data-kelo-close', String(++n));
      item.close = n;
      item.close_label = ((best.innerText || best.value || '').trim() || best.getAttribute('aria-label') || best.getAttribute('title') || 'X').slice(0, 40);
    }
    found.push(item);
    if (best) break;   // one at a time: the page is looked at again after each close
  }
  return { kelo_overlay, found };
}"""


def click_js(index: int) -> str:
    """A plain JavaScript click on the marked close control, for when the browser's own click cannot reach it."""
    return f"() => {{ const e = document.querySelector('[data-kelo-close=\"{int(index)}\"]'); if (e) {{ e.click(); return 1; }} return 0; }}"


def overlays(probe: Any) -> list[dict[str, Any]]:
    """The windows of an OVERLAY_JS reading ([] for anything else, e.g. a page that could not be read)."""
    if not isinstance(probe, dict) or not probe.get("kelo_overlay"):
        return []
    out = []
    for f in probe.get("found") or []:
        if isinstance(f, dict) and isinstance(f.get("label"), str):
            close = f.get("close")
            out.append({"kind": str(f.get("kind") or "overlay"), "label": f["label"][:LABEL_CHARS],
                        "close": close if isinstance(close, int) and not isinstance(close, bool) else None,
                        "close_label": str(f.get("close_label") or "")[:40],
                        "skipped": [str(s)[:40] for s in (f.get("skipped") or []) if isinstance(s, str)][:2]})
    return out


def signature(f: dict[str, Any]) -> str:
    return f"{f['kind']}:{f['label'][:60]}"


KIND_WORDS = {"cookie": "A cookie banner", "dialog": "A pop-up window", "overlay": "A layer"}


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
    rf"(?<![\w{MASK}])[$€£¥₩]?\s?\d[\d,.]*[{MASK}]+[\d,.{MASK}]*(?:\s?[^\W\d_]{{1,4}}|\s?[$€£¥₩%])?"   # 0*, 1,2**원, 40,0** 원
    rf"|[{MASK}]{{3,}}(?:[\s,.-]?[\d{MASK}]+)*"                                                       # ●●●●, ***, ****-1234
    r"|(?<![\w-])[$€£¥₩]\s?-{1,3}(?![\w-])|(?<![\w-])-{1,3}\s?(?:원|₩|USD|KRW|EUR|JPY|円|元)(?!\w)"         # $--, - 원
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
