"""Which browser steps commit something on the user's behalf, and so get an Allow/Deny card.

Looking things up never asks: following links, tabs, filters, date pickers, "more" buttons, cookie banners and typing into a search box
go through without a card. A step asks when it would do something in the user's name that is hard to take back:

  pay      pay, buy, order, book, reserve, subscribe, donate, top up, transfer (also a plain "Confirm" / "Next" on a checkout page)
  apply    apply, enter a draw or a campaign, RSVP (sends an application in the user's name, spends no money)
  send     send, post, publish, comment, reply, submit a review
  delete   delete, cancel an order / booking / subscription, close an account
  optout   opt out, stop receiving something (messages, notifications, a list), turn alerts off: low risk, can usually be turned
           back on (also a plain "Confirm" / "Save" on a page whose purpose is opting out)
  account  sign up, register, change account settings (a plain "Save" on a settings page)
  security change how the account is protected or reached (create an app password or an API key, turn 2-step verification
           on or off, add or remove a device, a passkey or a recovery address) or issue an official document or certificate
           in the user's name (a family register, a tax statement, 증명서 발급): ALWAYS asks — no rule, grant, order or request
           covers it (2026-11-12: another assistant made itself an app password to keep reading the user's mail)
  form     a button that sends a form holding personal details or a message
  personal typing personal details (e-mail, phone, name, address, password, card, ...) or a message (a text area)

What the element does comes first, from the element and the code its click runs (`structure`: inline handlers, a javascript:
address, the page functions they call and the listeners added with addEventListener, read through DevTools): an element that only
opens a window or a tab (target=_blank, window.open) or a plain link whose code sends nothing prepares and never commits, whatever its
words ("바로구매 새창"). A commit is a form submit, a request that may change something (fetch, XHR, a POST), or, when nothing tells,
the words below. A link straight to a payment address (/pay, /billing, place-order) and deletions or opt-outs keep asking.

Going somewhere is never a commit, whatever its words: a link to another page (an `<a href>`, or an element whose role is link, menu
item or tab), a result / product / campaign card, a count or price label ("7,855명 신청", "1,234 reviews", "₩82,524") and a sign-in or
sign-up entry ("로그인 / 회원가입") only open a page. The commit is the button that sends the form there (the final sign-up submit still
asks). Only payment words (pay, buy, checkout, 결제, 구매, ...), deletions and opt-outs keep asking on a link, and a link that sits in a form on a
checkout page is judged like a button.

The judgement is made on the element itself, read from the page just before the step (`TARGET_JS`): its visible words, label, role, the
form it belongs to (a search form or one holding personal details) and the page (a checkout or settings page). When the element cannot
be read, the selector's own words are judged instead. The word lists are generic (no site has its own rule) and lean towards asking:
a missed commit costs more than an extra card. This is a hint for when to insist on a card, never a permission: payments still go
through confirm_purchase, and the owner can still have every click and every typing step ask (ASSISTANT_APPROVE_BROWSER_ACTIONS=1).
"""

from __future__ import annotations

import re
from typing import Any

# Read from the page for one element (Playwright `locator.evaluate`): what the user would see and what the element would do.
TARGET_JS = r"""(el) => {
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
  const PERSONAL = /e-?mail|phone|mobile|tel|password|passwd|name|address|street|postal|zip|birth|bday|passport|card|cvc|cvv|ssn|national|이메일|메일|전화|휴대폰|핸드폰|연락처|비밀번호|이름|성명|주소|우편|생년월일|여권|카드|주민/i;
  let personal = 0, message = 0, search = false;
  if (form) {
    search = form.getAttribute('role') === 'search' || !!form.querySelector('input[type=search], [role=searchbox]') ||
             /search|query|find|검색/i.test(form.getAttribute('action') || '') ||
             !!form.querySelector('input[name=q], input[name=query], input[name=keyword], input[name=search]');
    for (const f of form.querySelectorAll('input, textarea, [contenteditable=true]')) {
      const ft = (f.getAttribute('type') || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'image', 'reset', 'checkbox', 'radio', 'search'].includes(ft)) continue;
      const v = f.isContentEditable ? f.innerText : f.value;
      if (!v && ft !== 'file') continue;
      if (f.tagName === 'TEXTAREA' || f.isContentEditable) { message++; continue; }
      const fa = [f.getAttribute('name'), f.id, f.getAttribute('placeholder'), f.getAttribute('autocomplete'), f.getAttribute('aria-label')].join(' ');
      if (['email', 'tel', 'password', 'file'].includes(ft) || PERSONAL.test(fa)) personal++;
    }
  }
  // the page: does it ask how to pay (card fields, a choice of payment way, a heading such as "Payment method")? Is it a cart?
  const PAYWORDS = /결제\s*수단|결제\s*방법|결제\s*정보|카드\s*번호|신용\s*카드|무통장|간편\s*결제|payment\s*(method|details|information|options?)|pay\s*with|card\s*number|credit\s*card|debit\s*card|billing\s*(address|info)|支払い方法|お支払い方法|カード番号|支付方式|付款方式|zahlungsart|zahlungsmethode|moyen de paiement|m[ée]todo de pago|forma de pagamento|metodo di pagamento|способ оплаты/i;
  const heads = Array.from(document.querySelectorAll('h1, h2, h3, h4, legend, label, th, dt, summary, [role=heading]')).slice(0, 400)
                     .map(e => (e.innerText || e.textContent || '').slice(0, 80)).join(' | ');
  const payPage = !!document.querySelector('input[autocomplete^="cc-"], input[name*="cardnum" i], input[name*="card_number" i], input[name*="cardno" i], ' +
                                           'input[type=radio][name*="pay" i], input[type=radio][name*="settle" i], select[name*="pay" i]') || PAYWORDS.test(heads);
  const h1 = Array.from(document.querySelectorAll('h1')).slice(0, 3).map(e => e.innerText || '').join(' ');
  const cartPage = /\/(cart|basket|bag)(\/|$|\?|\.)/i.test(location.pathname) || /장바구니|shopping\s*(cart|bag)|\bcart\b|\bbasket\b|カート|购物车|購物車|warenkorb|panier|carrito|carrinho|carrello|корзина/i.test(document.title + ' ' + h1);
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
  try { window.__keloJudgeTarget = act; } catch (e) {}
  const formTarget = form ? (form.getAttribute('target') || '') : '';
  return {
    structure_read: true, handler_src: code.join('\n;\n').slice(0, 12000),
    link_target: (tag === 'a' || tag === 'area') ? (act.getAttribute('target') || '') : '', form_target: formTarget,
    pay_page: payPage, cart_page: cartPage,
    in_form: !!form, tag, type, role: (act.getAttribute('role') || '').toLowerCase(), cursor: getComputedStyle(act).cursor || '',
    text: clean(act.innerText || act.textContent || ''), words: clean(words), attrs: clean(attrs),
    href: tag === 'a' ? (act.getAttribute('href') || '') : '',
    editable: !!act.isContentEditable, autocomplete: (act.getAttribute('autocomplete') || '').toLowerCase(),
    name: (act.getAttribute('name') || '').toLowerCase(),
    submits, search_form: search, form_personal: personal, form_message: message,
    url: location.href, title: clean(document.title),
  };
}"""

LONG_LINK = 25   # a link with more words than this is a result, an article or a product card: following it only looks

_EN_NAV = re.compile(r"\b(view|see|show|details?|more|learn|how|info|information|availability|available|search|find|filter|sort|compare|"
                     r"history|list|lists|options?|faq|guide|policy|terms|calendar|dates?|status|track|tracking)\b")
# (reviews are not a looking word: "리뷰 등록" / "Submit review" sends one; "리뷰 보기" / "See reviews" still only looks by its other word)
_KO_NAV = re.compile(r"보기|조회|검색|내역|가능|안내|정보|방법|목록|상세|비교|정책|약관|일정|달력|날짜|옵션|선택|현황|더\s*보기|알아보기")

_EN_DELETE = re.compile(r"\b(delete|remove (my |your |the )?account|deactivate|close (my |your |the )?account|erase|terminate|"
                        r"cancel (my |your |the |this )?(order|booking|reservation|subscription|membership|plan|account|trip|ticket|payment))\b")
_KO_DELETE = re.compile(r"삭제|탈퇴|해지|(주문|예약|예매|구독|결제|신청|멤버십)\s*취소")
# Opting out: stop receiving something (a list, messages, notifications). Reversible and spends nothing; judged after deletions, so
# "delete my account" stays a deletion and a paid plan's "cancel subscription" / "구독 해지" stays one too.
_EN_OPTOUT = re.compile(r"\b(unsubscribe|unsub|opt[ -]?out|stop (receiving|getting|sending me)|turn off (all )?(notifications|alerts|emails|messages|updates)|"
                        r"remove me( from)?|leave (this |the )?(mailing )?list|no longer (receive|want)|don'?t send me)\b")
_XX_OPTOUT = re.compile(r"수신\s*(거부|해제|거절|철회|중단|안\s*함)|알림\s*(끄기|해제|중단)|그만\s*받기|받지\s*않(기|음)|"
                        r"abbestellen|newsletter abmelden|d[ée]sabonner|d[ée]sinscri|darse de baja|dar de baja|disiscriv|uitschrijven|wypisz|"
                        r"отписаться|відписатися|abonelikten [çc]ık|hủy đăng ký|berhenti berlangganan|إلغاء الاشتراك|"
                        r"退订|退訂|取消订阅|取消訂閱|配信停止|配信解除|購読解除|ยกเลิกการรับ|सदस्यता (रद्द|समाप्त)|desinscrever|descadastrar")
_EN_PAY = re.compile(r"\b(pay|buy|purchase|checkout|check out|place (my |your |the )?order|order now|top up|donate|subscribe|reserve|bid|"
                     r"send money|transfer (money|funds|now)|(submit|complete|confirm) (my |your |the )?(order|purchase|payment|booking|reservation)|"
                     r"book( now| it| this| tickets?| a room| online)?)\b")
_KO_PAY = re.compile(r"결제|구매|구입|주문|예약|예매|충전|송금|이체|후원|기부|입금|구독")
# Payment words that keep asking even on a link (a link straight to "pay" or "buy now" may start the payment itself).
_EN_MONEY = re.compile(r"\b(pay|buy|purchase|checkout|check out|place (my |your |the )?order|order now|top up|donate|send money|"
                       r"transfer (money|funds|now)|(submit|complete|confirm) (my |your |the )?(order|purchase|payment))\b")
_KO_MONEY = re.compile(r"결제|구매|구입|주문하기|바로\s*주문|송금|이체|입금|충전|후원|기부")
_EN_APPLY = re.compile(r"\b(apply( now)?|submit (my |your |the )?application|enroll|enter (the |this )?(draw|giveaway|contest|raffle)|rsvp)\b")
_KO_APPLY = re.compile(r"신청|응모|지원하기|참가하기|참여하기")
_EN_ACCOUNT = re.compile(r"\b(sign ?up|register|create (an |my |your )?account|join( now| free)?|"
                         r"change (my |your |the )?password|save changes|update (my |your |the )?(profile|account|settings|email|password|address))\b")
# A sign-in or sign-up entry: both words in one label ("로그인 / 회원가입", "Sign in or register") is a way in, not a form being sent.
_SIGN_IN = re.compile(r"로그인|\b(sign ?in|log ?in)\b", re.I)
_SIGN_UP = re.compile(r"가입|\b(sign ?up|register|join|create (an )?account)\b", re.I)
# A count, a price or a rating, maybe with a word or two after it: a label of a card, not a button ("7,855명 신청", "1,234 reviews").
_COUNT = re.compile(r"^[(\[]?\s*(?:[₩$€£¥]|krw|usd)?\s*\d[\d,.]*\s*(?:[kKmM만천억]|\+)?\s*(?:\S+\s*){0,3}$", re.I)
_IMPERATIVE = re.compile(r"하기|합니다|할게요|해요|\bnow\b|!", re.I)
_NAV_ROLES = ("link", "menuitem", "menuitemradio", "menuitemcheckbox", "tab", "treeitem")
_KO_ACCOUNT = re.compile(r"가입|비밀번호\s*변경|정보\s*(수정|변경)|설정\s*(저장|변경)")
_EN_SEND = re.compile(r"\b(send|post|publish|tweet|comment|reply|submit( review)?)\b")
_KO_SEND = re.compile(r"보내기|전송|게시|등록|발행|댓글|답글|제출|올리기|작성\s*완료")
# Words that commit only where they stand: a checkout page, a settings page, or a form holding personal details.
_EN_GENERIC = re.compile(r"^(?:(?:confirm|ok|okay|yes|continue|next|proceed|done|finish|complete|save|apply|agree|accept|submit|"
                         r"and|i|to|the|step)\W*){1,4}$")
_KO_GENERIC = re.compile(r"^(?:(?:확인|다음|계속|진행|완료|저장|적용|동의|네|예|하기|단계|합니다)\W*){1,4}$")

# A page whose purpose is opting out (by its path and title): its plain "Confirm" / "Save" / "Submit" opts out.
_OPTOUT_PAGE = re.compile(r"unsubscribe|opt-?out|optout|e-?mail[-_ ]?preferences|subscription[-_ ]?(center|centre|preferences|settings)|"
                          r"manage[-_ ]?(subscriptions|preferences)|notification[-_ ]?(settings|preferences)|수신\s*거부|구독\s*설정|알림\s*설정", re.I)
_MONEY_PAGE = re.compile(r"checkout|payment|/pay\b|billing|/order|/booking|/book\b|reservation|/cart|purchase|결제|주문|예약", re.I)
_SETTINGS_PAGE = re.compile(r"setting|/account|profile|preference|privacy|security|mypage|my-page|설정|계정|회원\s*정보|내\s*정보", re.I)

# security: what an account's protection or access is made of, and the verbs that change it (security_step)
_SEC_OBJECT = re.compile(r"app(?:lication)?[- ]?passwords?|passkeys?|security[- ]?keys?|2[- ]?step|two[- ]?(?:step|factor)|\b2fa\b|\bmfa\b|"
                         r"multi[- ]?factor|authenticator|\btokens?\b|credentials?|recovery (?:e-?mail|phone|codes?|address)|backup codes?|(?:trusted|signed[- ]in|"
                         r"login|registered) devices?|api[- ]?keys?|(?:personal )?access tokens?|connected apps?|third[- ]party (?:apps?|access)|"
                         r"애플리케이션\s*비밀번호|앱\s*비밀번호|2\s*단계|이중\s*인증|2\s*차\s*인증|\botp\b|패스\s*키|보안\s*키|복구\s*(?:이메일|메일|전화|"
                         r"번호|코드)|백업\s*코드|(?:신뢰|등록|로그인|사용)\S*\s*기기|연결된\s*(?:앱|서비스)|api\s*키|접근\s*토큰|인증서|"
                         r"forwarding|auto-?forward|forward (?:all|your|incoming) (?:mail|e-?mail|messages)|delegat\w*|grant access to your account|"
                         r"account access|mailbox permissions?|전달\s*주소|자동\s*전달|메일\s*전달|위임|대리인|액세스\s*권한\s*부여|계정\s*액세스", re.I)
_DELEGATION = re.compile(r"\bdelegat\w*|(?:^|\s)(?:위임|대리인)(?:\s|$)", re.I)   # naming delegation is enough: there is no harmless press
_SEC_VERB = re.compile(r"\b(?:create|generate|add|new|make|turn (?:on|off)|enable|disable|remove|revoke|delete|reset|change|set ?up|"
                       r"register|allow|grant|issue|export|copy|sign out|save(?: changes)?)\b|생성|발급|추가|등록|만들|해제|끄기|켜기|사용\s*안\s*함|삭제|변경|"
                       r"재발급|허용|설정|내보내기|복사|로그아웃|저장", re.I)
# a page about the account's protection (path and title, _page)
_SECURITY_PAGE = re.compile(r"security|apppasswords|app[-_]?passwords|signinoptions|two[-_]?step|2fa|mfa|passkey|/tokens|api[-_]?keys|credentials|보안|2단계|이중\s*인증|"
                           r"fwdandpop|forwarding|delegat|/accounts?(?:and|&)import|options/mail/forwarding|mailboxpermissions|메일\s*전달|위임", re.I)
# issuing an official document or certificate in the user's name; "발급" alone issues something wherever it stands
_DOC_ISSUE = re.compile(r"(?:재)?발급|(?:증명서|등본|초본|확인서|증명원|인증서)\S*\s*(?:신청|출력|인쇄|저장|내려받기|다운로드)|"
                        r"\b(?:issue|print|request|download) (?:an? |the |my )?(?:official |tax |family )?(?:certificate|transcript|"
                        r"statement|record|register)\b", re.I)
# ... but a coupon, points or a gift card "issued" in a shop is not a document (those words go on as before)
_NOT_A_DOCUMENT = re.compile(r"coupon|voucher|gift ?card|쿠폰|할인|적립|포인트|상품권|이용권|교환권|번호표|대기", re.I)

_SEARCH_FIELD = re.compile(r"search|query|keyword|\bfind\b|destination|where|location|city|\bdate|check.?in|check.?out|\bfrom\b|\bto\b|"
                           r"검색|찾기|목적지|도시|여행지|날짜|출발|도착|지역", re.I)
_SEARCH_NAMES = {"q", "query", "k", "kw", "keyword", "keywords", "s", "search", "term", "searchterm", "text", "wd", "p"}
_PERSONAL_FIELD = re.compile(r"e-?mail|phone|mobile|\btel\b|password|passwd|\bpw\b|first.?name|last.?name|full.?name|given.?name|"
                             r"family.?name|surname|user.?name|your.?name|(?:^|\s)name(?:\s|$)|recipient|receiver|login|address|street|postal|\bzip|birth|bday|passport|"
                             r"card|cvc|cvv|ssn|social.?security|national.?id|이메일|메일|전화|휴대폰|핸드폰|연락처|비밀번호|이름|성명|받는\s*분|수령인|수취인|"
                             r"주소|우편|생년월일|생일|여권|카드|주민", re.I)
_PERSONAL_AUTOCOMPLETE = re.compile(r"\b(name|given-name|family-name|additional-name|email|tel|tel-\w+|street-address|address-line\d|"
                                    r"address-level\d|postal-code|country|bday|bday-\w+|username|new-password|current-password|"
                                    r"one-time-code|cc-\w+|sex|organization)\b")
_MESSAGE_FIELD = re.compile(r"message|comment|reply|review|\bpost\b|tweet|\bbody\b|content|메시지|댓글|답글|내용|후기|리뷰|문의", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+\d[\d\s-]{7,}\d|(?<![\d-])0\d{1,2}[\s-]?\d{3,4}[\s-]?\d{4}(?![\d-])")

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
    """The words a selector names: its text=/has-text/aria-label values, else its ids and classes with - and _ read as spaces."""
    s = selector or ""
    found = [m.group(2) for m in _SELECTOR_TEXT.finditer(s)]
    if found:
        return " ".join(found)
    return re.sub(r"[#.\-_\[\]=\"'():>]+", " ", s)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _page(info: dict[str, Any] | None, url: str) -> str:
    """The page's path and title (not its host: a site called booking.example is not a checkout page)."""
    raw = str((info or {}).get("url") or url or "")
    path = re.sub(r"^[a-z]+://[^/]+", "", raw, flags=re.I)
    return f"{path} {(info or {}).get('title') or ''}"


def money_words(label: str) -> bool:
    """Words that pay or buy (they keep asking even on a link)."""
    w = _norm(label)
    return bool(_EN_MONEY.search(w) or _KO_MONEY.search(w))


def is_entry(label: str) -> bool:
    """A sign-in and sign-up entry in one label ("로그인 / 회원가입"): it opens the sign-in page; the form there is what commits."""
    return bool(_SIGN_IN.search(label or "") and _SIGN_UP.search(label or ""))


def is_count(label: str) -> bool:
    """A number with at most a few words after it and no command in it: a count, a price or a rating on a card ("7,855명 신청")."""
    w = _norm(label)
    return bool(w) and bool(_COUNT.match(w)) and not _IMPERATIVE.search(w) and not money_words(w)


# Words that only go on with a step (no consent, no sign-up, no settings), in several languages: a sign-in's "Continue" (commits.py)
_CONTINUE_ONLY = re.compile(
    r"^(?:(?:continue|next|proceed|ok|okay|yes|done|confirm|go on|sign ?in|log ?in|계속|다음|확인|진행|로그인|하기|네|예|はい|是|ja|oui|sí|sim|да|evet|続ける|続行|次へ|ログイン|"
    r"继续|繼續|下一步|确定|確定|登录|登入|weiter|fortfahren|anmelden|continuer|suivant|se connecter|continuar|siguiente|"
    r"iniciar sesi[óo]n|entrar|continua|avanti|accedi|doorgaan|volgende|inloggen|dalej|zaloguj|продолжить|далее|войти|devam|"
    r"ileri|giriş|tiếp tục|đăng nhập|lanjut|masuk|ดำเนินการต่อ|ถัดไป|เข้าสู่ระบบ|जारी रखें|आगे|متابعة|التالي)\W*){1,3}$", re.I)


def continue_only(label: str) -> bool:
    """The element's words only go on with a step ("계속하기", "Continue", "Next", "Sign in"): no consent, sign-up or settings."""
    w = " ".join((label or "").lower().split())
    return bool(w) and bool(_CONTINUE_ONLY.match(w))


def security_step(label: str, page: str = "") -> bool:
    """The words change how the account is protected or reached (an app password, 2-step verification, a device, a key) or issue an
    official document: an object of that kind with a verb that changes it, a changing verb alone on a security page, or "발급"-like
    words. Generic: no site has its own rule."""
    w = _norm(label)
    if not w:
        return False
    if _DOC_ISSUE.search(w) and not _NOT_A_DOCUMENT.search(w):
        return True
    if _DELEGATION.search(w):
        return True
    verb = bool(_SEC_VERB.search(w))
    if verb and _SEC_OBJECT.search(w):
        return True
    return verb and bool(page) and bool(_SECURITY_PAGE.search(page)) and len(w) <= 40


_CANCEL_ORDER = re.compile(r"\bcancel(?:l?ation)?\b|\brefund|취소|환불")
_NOT_AN_ORDER = re.compile(r"\b(account|membership|subscription|plan)\b|계정|탈퇴|해지|구독|멤버십")


def cancel_words(label: str) -> bool:
    """The words of a button or link that cancels an order, booking or payment ("Cancel order", "주문 취소"); never an account,
    a membership or a subscription (purchase_result's cancel window covers only the wrongly charged order)."""
    w = _norm(label)
    return bool(w) and bool(_CANCEL_ORDER.search(w)) and not _NOT_AN_ORDER.search(w)


def judge_words(label: str) -> str:
    """The commit kind the words of a button or link name, or "" for words that only look (see the module docstring)."""
    w = _norm(label)
    if not w:
        return ""
    if security_step(w):
        return "security"
    if _EN_DELETE.search(w) or _KO_DELETE.search(w):
        return "delete"
    if _EN_OPTOUT.search(w) or _XX_OPTOUT.search(w):
        return "optout"
    if is_entry(w) or is_count(w):
        return ""
    en_nav, ko_nav = bool(_EN_NAV.search(w)), bool(_KO_NAV.search(w))
    if _EN_ACCOUNT.search(w) or _KO_ACCOUNT.search(w):
        return "account"
    if (_EN_PAY.search(w) and not en_nav) or (_KO_PAY.search(w) and not ko_nav):
        return "pay"
    if (_EN_APPLY.search(w) and not en_nav) or (_KO_APPLY.search(w) and not ko_nav):
        return "apply"
    if (_EN_SEND.search(w) and not en_nav) or (_KO_SEND.search(w) and not ko_nav):
        return "send"
    return ""


# ---------------------------------------------------------------- what a click would do, from the element and its code
# Code that sends something or changes state: a form submit, a request (fetch, XHR, jQuery/axios posts, a beacon), a POST.
_STATE_CODE = re.compile(r"\.\s*(?:request)?submit\s*\(|\bfetch\s*\(|XMLHttpRequest|\$\s*\.\s*(?:post|ajax|get)\s*\(|\.ajax\s*\(|"
                         r"\baxios\b|sendBeacon|method\s*[:=]\s*['\"]?post|\.send\s*\(|WebSocket", re.I)
# Code that opens a window or goes to another page.
_OPEN_CODE = re.compile(r"\bwindow\s*\.\s*open\s*\(|(?<![\w.])open\s*\(|showModalDialog|"
                        r"\blocation(?:\s*\.\s*href)?\s*=(?!=)|location\s*\.\s*(?:assign|replace)\s*\(|history\s*\.\s*pushState", re.I)
# The element itself says it opens a new window (a hint used only together with code or a link that opens nothing else)
_NEW_WINDOW_WORDS = re.compile(r"새\s*창|새\s*탭|팝업|new\s+(?:window|tab)|opens?\s+in\s+a\s+new|popup|新しいウィンドウ|新窗口|新視窗", re.I)
_PAY_HREF = re.compile(r"/(?:pay|payment|payments|billing)(?:/|$|\?|\.)|(?:place|complete|confirm)[-_]?order", re.I)


def structure(info: dict[str, Any] | None) -> str:
    """What clicking the element does, from what it is and the code it runs (not its words):
    "changes" (a form submit, a request, a POST: it may commit), "opens" (it only opens a window or a new tab), "goes" (an ordinary link
    to another page, nothing else), or "" (not known: the element was not read this way, or its code says neither)."""
    if not info or not info.get("structure_read"):
        return ""
    src = str(info.get("handler_src") or "")
    if info.get("submits") or _STATE_CODE.search(src):
        return "changes"
    href = str(info.get("href") or "")
    real_link = info.get("tag") == "a" and not re.match(r"^\s*(#|javascript:|$)", href, re.I)
    target = str(info.get("link_target") or "").strip().lower()
    new_window = real_link and target not in ("", "_self", "_top", "_parent")
    if new_window or _OPEN_CODE.search(src):
        return "opens"
    if real_link:
        return "goes"
    label = f"{info.get('text') or ''} {info.get('words') or ''}"
    if _NEW_WINDOW_WORDS.search(label) and not info.get("in_form") and info.get("tag") in ("a", "button") and src:
        return "opens"   # says so, and runs code that neither sends nor posts anything
    return ""


def judge_click(info: dict[str, Any] | None, selector: str, *, url: str = "") -> str:
    """The commit kind of clicking this element ("" = only looking). `info` is what TARGET_JS read, or None when the element could not
    be read (then the selector's own words are judged)."""
    kind = _judge_click(info, selector, url)
    if _OPTOUT_PAGE.search(_page(info, url)) and kind in ("send", "account", "form", "optout", "generic"):
        return "optout"   # on a page made for opting out, its submit / save / confirm is the opt-out itself
    return "" if kind == "generic" else kind


def _judge_click(info: dict[str, Any] | None, selector: str, url: str) -> str:
    """judge_click without the opt-out page rule; "generic" = plain words ("OK", "Submit") that commit nothing on an ordinary page."""
    page = _page(info, url)
    money, settings = bool(_MONEY_PAGE.search(page)), bool(_SETTINGS_PAGE.search(page))
    if info is None:
        label = selector_words(selector)
        if security_step(label, page):
            return "security"
        kind = judge_words(label)
        if kind:
            return kind
        if _EN_GENERIC.match(_norm(label)) or _KO_GENERIC.match(_norm(label).replace(" ", "")):
            return "pay" if money else "security" if _SECURITY_PAGE.search(page) else "account" if settings else "generic"
        return ""
    if not pressable(info):
        return ""   # plain text on the page (not a button, link, field or anything with click code): pressing it sends nothing
    text, words = str(info.get("text") or ""), str(info.get("words") or "")
    label = f"{text} {words}".strip() if len(text) <= 60 else words
    real_link = info.get("tag") == "a" and not re.match(r"^\s*(#|javascript:|$)", str(info.get("href") or ""), re.I)
    submits = bool(info.get("submits"))
    if real_link and len(text) > LONG_LINK and not submits and not security_step(label, page):
        return ""   # a search result, an article or a product card
    if len(label) > 80 and not submits and not security_step(label, page):
        return ""
    search = bool(info.get("search_form"))
    holds_details = not search and (int(info.get("form_personal") or 0) > 0 or int(info.get("form_message") or 0) > 0)
    kind = "security" if security_step(label, page) else judge_words(label)
    how = structure(info)
    if kind and how == "opens":
        return ""   # it only opens a window or a tab (a "바로구매 새창" opener): what is done in that window asks there
    if kind and how == "goes" and not (info.get("in_form") and money) and kind not in ("delete", "optout", "security") \
            and not _PAY_HREF.search(str(info.get("href") or "")):
        return ""   # a plain link with no code that sends anything only opens the page; the pay button there asks
    goes = real_link or str(info.get("role") or "") in _NAV_ROLES
    if kind and goes and not submits and not (info.get("in_form") and money):
        # a link to another page only opens it ("예약하기" to the booking page, "회원가입" to the sign-up form): what is done there
        # asks. Payment words, deletions and security steps keep asking.
        return kind if kind in ("delete", "optout", "security") or (kind == "pay" and money_words(label)) else ""
    if kind == "send" and submits and search:
        return ""   # "Submit" on a search form only searches
    if kind:
        return kind
    plain = _norm(label)
    generic = not plain or bool(_EN_GENERIC.match(plain) or _KO_GENERIC.match(plain.replace(" ", "")))
    if generic and (submits or plain):
        if money:
            return "pay"
        if _SECURITY_PAGE.search(page):
            return "security"
        if settings:
            return "account"
    if submits and holds_details:
        return "form"
    return "generic" if generic and (submits or plain) else ""


# ---------------------------------------------------------------- inside an order the user asked for (tools/order.py)
# Pay words that may pay at once wherever they stand: the final button of a checkout, a money transfer, a booking, a subscription.
_EN_FINAL = re.compile(r"\b(place (my |your |the )?order|pay( now| securely)?|complete (my |your |the )?(order|purchase|payment|checkout)|"
                       r"(confirm|submit) (and pay|my order|your order|the order|order|purchase|payment)|buy with 1-click|1-click|one-click|"
                       r"donate|top up|subscribe|send money|transfer|bid|reserve|book( now| it| this| tickets?| a room| online)?)\b")
_KO_FINAL = re.compile(r"결제|구매\s*확정|주문\s*확정|주문\s*완료|송금|이체|충전|후원|기부|구독|입찰|예약|예매|입금")
# Words of a step that only takes the order further: to the cart, to the checkout, to the next step of the checkout.
_EN_STEP = re.compile(r"\b(check ?out|proceed( to (checkout|shipping|delivery|payment))?|continue( to (checkout|shipping|delivery|payment))?|"
                      r"go to (cart|checkout|basket)|view (cart|basket|bag)|buy (it )?now|order now|add to (cart|bag|basket)|purchase|buy|order|"
                      r"next|continue|confirm|ok|apply)\b")
_KO_STEP = re.compile(r"구매하기|바로\s*구매|구매|구입|주문하기|바로\s*주문|주문서|주문|장바구니|다음|계속|진행|확인|적용")
_COUPON = re.compile(r"coupon|promo|discount|voucher|gift ?card|code|쿠폰|할인|프로모션|적립금|포인트|상품권|クーポン|优惠|優惠", re.I)
_PAY_PATH = re.compile(r"/(pay|payment|payments|billing)(/|$|\?|\.)", re.I)


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


def order_step(info: dict[str, Any] | None, selector: str, *, url: str = "") -> bool:
    """Inside an order the user asked for: whether a click judged "pay" only takes the order further (to the cart or the checkout,
    the next step of the checkout) and so needs no card of its own. False = it may pay: the final payment, which goes through
    confirm_purchase. Leans towards False: a button with final words ("Place order", "결제하기" outside a cart), any pay button on a page
    that asks how to pay (card fields, a choice of payment way) or on a payment address."""
    w = _norm(label_of(info, selector))
    cart = bool(info and info.get("cart_page")) or bool(re.search(r"/(cart|basket|bag)(/|$|\?|\.)", str((info or {}).get("url") or url), re.I))
    if (info and info.get("pay_page")) or _PAY_PATH.search(_page(info, url).split(" ", 1)[0]):
        return False
    final = bool(_EN_FINAL.search(w) or _KO_FINAL.search(w))
    if final:
        # "결제하기" / "Pay" on a cart only opens the checkout (nothing to pay with is chosen there); anything stronger never
        bare = re.fullmatch(r"(?:\W|\d|선택|전체|상품|하기|pay|checkout|items?|selected|all|결제|구매|주문|원|krw|usd|\$|₩)*", w.replace(" ", ""))
        return cart and bool(bare)
    return bool(_EN_STEP.search(w) or _KO_STEP.search(w.replace(" ", "")) or not w)


def money_page(info: dict[str, Any] | None, url: str = "") -> bool:
    """A checkout, payment, order or booking page (by its path and title)."""
    return bool(_MONEY_PAGE.search(_page(info, url)))


def coupon_words(info: dict[str, Any] | None, selector: str) -> bool:
    """An "Apply" that applies a coupon, a promo code or points to the order (the code itself is not personal)."""
    return bool(_COUPON.search(f"{label_of(info, selector)} {str((info or {}).get('attrs') or '')} {selector}"))


def judge_type(info: dict[str, Any] | None, selector: str, text: str, submit: bool = False) -> str:
    """The commit kind of typing `text` into this field ("" = an ordinary search or filter field)."""
    digits = re.sub(r"[\s-]", "", text or "")
    if _EMAIL.search(text or "") or _PHONE.search(text or "") or (digits.isdigit() and 13 <= len(digits) <= 19 and _luhn(digits)):
        return "personal"
    if info is None:
        s = selector or ""
        if re.search(r"textarea|contenteditable", s, re.I):
            return "personal"
        attrs = selector_words(s)
        if re.search(r"search|query|keyword|검색", attrs, re.I) or re.fullmatch(r"\s*(input)?\s*(name\s+)?q\s*", attrs, re.I):
            return ""
        if _PERSONAL_FIELD.search(attrs) or _MESSAGE_FIELD.search(attrs):
            return "personal"
        return ""
    attrs = str(info.get("attrs") or "")
    ftype, role = str(info.get("type") or ""), str(info.get("role") or "")
    if ftype in ("email", "tel", "password") or _PERSONAL_AUTOCOMPLETE.search(str(info.get("autocomplete") or "")):
        return "personal"
    if (ftype == "search" or role in ("searchbox", "combobox") or bool(info.get("search_form"))
            or str(info.get("name") or "") in _SEARCH_NAMES):
        return ""   # a search box or a field of a search form (destination, dates, guests)
    if _PERSONAL_FIELD.search(attrs):
        return "personal"
    if _SEARCH_FIELD.search(attrs):
        return ""
    if info.get("tag") == "textarea" or info.get("editable") or _MESSAGE_FIELD.search(attrs):
        return "personal"
    if submit and (int(info.get("form_personal") or 0) > 0 or int(info.get("form_message") or 0) > 0):
        return "form"
    return ""
