"""The public website every Kelo server shows in a browser: the home page ("/"), how to install a server ("/install") and the
privacy policy ("/privacy"), in Korean and English.

The pages are plain HTML kept in `site/pages/<page>.<lang>.html` (the body only); this module wraps them in one shared frame
(head, top bar, footer), puts the mascot in as inline SVG and serves everything else from /assets/: one stylesheet, two small
scripts (enter.js in <head>, site.js deferred), real app screenshots (`site/shots/*.webp`), photographs (`site/photos/*.webp`,
Wikimedia Commons, credited in the footer), service and platform logos (`site/logos/`), two self-hosted fonts (`site/fonts/`,
SIL OFL) and the home page's two scroll moments (`site/moments/`, from the tasteskill kit, unchanged). No external fonts,
scripts or trackers, and the Content-Security-Policy below allows nothing but this server.

Placeholders in a page: {{svg:<state>}} (the mascot), {{shot:<name>}} (a screenshot's URL), {{href:<page>[#anchor]}},
{{asset:<file>}} (any served file's versioned URL), {{app_open}}, {{install_sh}} and {{install_ps1}} (the one-line install
commands), {{github}} (the project on GitHub, for links
into its README), and {{dl:<platform>}} (an app download button
with its version and size from the published builds, downloads.py; filled at every request, so a new build shows at once).

Pairing links ("https://<host>/#pair=CODE", made by POST /api/devices/pair) also land on "/": the fragment never reaches the server,
so the home page always carries the "open the Kelo app" panel (hidden) and site.js shows it on top of the page when the address has
`#pair=`. The panel's button is the same Android intent link the server always used (landing.APP_OPEN).

Language: English is the primary language (canonical address, hreflang x-default, and what a reader gets by default). `?lang=ko|en`
wins; otherwise a browser whose Accept-Language puts Korean ahead of English gets Korean, and everything else (no header, or only
other languages) gets English, like a store listing whose default language is the fallback. Every link inside the site carries
`?lang=` so a reader who switched stays in that language.
"""

from __future__ import annotations

import hashlib
import re
from functools import cache
from html import escape
from pathlib import Path

from .downloads import human_size
from .landing import APP_OPEN

SITE = Path(__file__).with_name("site")
LANGS = ("ko", "en")
PRIMARY = "en"
CANONICAL = "https://kelo.chat"
PAGES = ("index", "install", "privacy", "support", "tester")
# the two v2 designs (site/v2/<design>.css, site/pages/index-<design>.<lang>.html). Both are previews until one is chosen:
# set LIVE_DESIGN to "a" or "b" and "/", "/install" and "/privacy" all use that design. None keeps the current pages.
DESIGNS = ("a", "b")
LIVE_DESIGN: str | None = "b"
PATHS = {"index": "/", "install": "/install", "privacy": "/privacy", "support": "/support", "tester": "/tester"}
CHARACTERS = ("idle", "working", "browser", "approval", "done", "sleeping", "reading", "thinking", "listening")
GITHUB = "https://github.com/kelo-chat/kelo"   # the project on GitHub (header and footer)
# the one-line installers (scripts/install.sh, install.ps1, served by api/site.py): they check Docker, get uv, fetch the newest release
# of the public source into ~/kelo and open the guided setup
INSTALL_SH = "curl -fsSL https://kelo.chat/install.sh | bash"
INSTALL_PS1 = "irm https://kelo.chat/install.ps1 | iex"
_SHOT = re.compile(r"[a-z0-9-]+\.webp")

CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
CSP_FORM = CSP.replace("form-action 'none'", "form-action 'self'")


def _weights(accept_language: str) -> dict[str, float]:
    """The best q of each site language in an Accept-Language header ("ko-KR,ko;q=0.9,en;q=0.8" -> {"ko": 1.0, "en": 0.8})."""
    best: dict[str, float] = {}
    for i, part in enumerate(accept_language.split(",")[:32]):
        tag, _, params = part.strip().partition(";")
        base = tag.strip().lower().split("-", 1)[0]
        if base not in LANGS:
            continue
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k.strip().lower() == "q":
                try:
                    q = max(0.0, min(1.0, float(v)))
                except ValueError:
                    q = 0.0
        # equal q: the one listed first wins
        best[base] = max(best.get(base, -1.0), q - i * 1e-6)
    return best


def pick_lang(query: str | None, accept_language: str | None) -> str:
    """?lang= first; Korean when the browser puts Korean ahead of English; English (the primary language) otherwise."""
    if query in LANGS:
        return query  # type: ignore[return-value]
    w = _weights(accept_language or "")
    return "ko" if w.get("ko", 0.0) > 0 and w.get("ko", 0.0) > w.get("en", 0.0) else PRIMARY


# ---------------------------------------------------------------------------------------------------------------- the mascot
_STYLE = re.compile(r"<style>(.*?)</style>", re.S)


@cache
def _character(name: str) -> tuple[str, str]:
    """(inline <svg> markup without its <style>, that style scoped to `.m-<name>` with renamed keyframes)."""
    raw = (SITE / "character" / f"kelo_{name}.svg").read_text(encoding="utf-8")
    css = "\n".join(_STYLE.findall(raw))
    frames = re.findall(r"@keyframes\s+([\w-]+)", css)
    for f in frames:   # unique names per character: several characters define "toggle-front" and friends differently
        css = re.sub(rf"(@keyframes\s+){re.escape(f)}(?![\w-])", rf"\g<1>{name}-{f}", css)
        css = re.sub(rf"(animation:\s*){re.escape(f)}(?![\w-])", rf"\g<1>{name}-{f}", css)
    # scope every class rule to this character's own <svg>
    css = re.sub(r"(^|\n)\s*(\.[\w-]+)\s*\{", rf"\1.m-{name} \2 {{", css)
    svg = _STYLE.sub("", raw)
    svg = re.sub(r'\s(width|height)="100%"', "", svg, count=2)
    svg = svg.replace("<svg ", f'<svg class="mascot m-{name}" aria-hidden="true" focusable="false" ', 1)
    svg = re.sub(r">\s+<", "><", svg).strip()
    return svg, css.strip()


def character(name: str) -> str:
    return _character(name)[0]


@cache
def art(name: str) -> str:
    """An illustration from site/art/<name>.svg (Kelo's style: the mascot's outline, sky blue and cream), inline, comments and
    whitespace dropped. {{mascot:<mood>:<x>:<y>:<size>}} places the character (with its animations) inside the drawing."""
    raw = (SITE / "art" / f"{name}.svg").read_text(encoding="utf-8")
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    raw = re.sub(r"\{\{mascot:(\w+):(\d+):(\d+):(\d+)\}\}", lambda m: character(m.group(1)).replace(
        "<svg ", f'<svg x="{m.group(2)}" y="{m.group(3)}" width="{m.group(4)}" height="{m.group(4)}" ', 1), raw)
    return re.sub(r">\s+<", "><", raw).strip()


# ---------------------------------------------------------------------------------------------------------------- assets
@cache
def stylesheet() -> str:
    base = (SITE / "style.css").read_text(encoding="utf-8")
    mascots = "\n".join(_character(n)[1] for n in CHARACTERS)
    return f"{base}\n/* mascot animations (from apps/android/design/character) */\n{mascots}\n"


@cache
def script() -> str:
    return (SITE / "site.js").read_text(encoding="utf-8")


@cache
def icon() -> str:
    """The tab icon: the idle character, still (an <svg> file may not run the page's stylesheet anyway)."""
    return _STYLE.sub("", (SITE / "character" / "kelo_idle.svg").read_text(encoding="utf-8"))


# flat files under /assets/<name>: the folder each kind lives in, and its media type
_FLAT = {".webp": (("shots", "photos", "logos"), "image/webp"), ".svg": (("logos",), "image/svg+xml"),
         ".woff2": (("fonts",), "font/woff2")}
_FLAT_NAME = re.compile(r"[a-z0-9-]+\.(webp|svg|woff2)")
# the moments (nested: their loader imports ./lib.js and <id>/moment.js by relative URL)
_MOMENT = re.compile(r"moments/(?:[a-z-]+/)?[a-z-]+\.(js|css)")
_TYPES = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}
_V2 = re.compile(r"v2/(base|a|b)\.css")


@cache
def _file(name: str) -> tuple[bytes, str] | None:
    if _MOMENT.fullmatch(name):
        path = SITE / name
        return (path.read_bytes(), _TYPES[name.rsplit(".", 1)[1]]) if path.is_file() else None
    if not _FLAT_NAME.fullmatch(name):
        return None
    folders, media = _FLAT["." + name.rsplit(".", 1)[1]]
    for folder in folders:
        path = SITE / folder / name
        if path.is_file():
            return path.read_bytes(), media
    return None


@cache
def _shot(name: str) -> bytes | None:
    got = _file(name) if _SHOT.fullmatch(name) else None
    return got[0] if got else None


@cache
def v2_file(name: str) -> tuple[str, str] | None:
    """The v2 designs' files (site/v2/base.css, a.css, b.css): base.css carries the mascot animations, like site.css."""
    if not _V2.fullmatch(name):
        return None
    path = SITE / name
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if name == "v2/base.css":
        text += "\n/* mascot animations (from apps/android/design/character) */\n" + "\n".join(_character(n)[1] for n in CHARACTERS) + "\n"
    return text, _TYPES[name.rsplit(".", 1)[1]]


def asset(name: str) -> tuple[str | bytes, str] | None:
    """(body, media type) of /assets/<name>, or None."""
    if name.startswith("v2/"):
        return v2_file(name)
    if name == "site.css":
        return stylesheet(), "text/css; charset=utf-8"
    if name == "site.js":
        return script(), "text/javascript; charset=utf-8"
    if name == "enter.js":
        return (SITE / "enter.js").read_text(encoding="utf-8"), "text/javascript; charset=utf-8"
    if name == "kelo.svg":
        return icon(), "image/svg+xml"
    return _file(name)


def version(name: str) -> str:
    got = asset(name)
    if not got:
        return "0"
    data = got[0] if isinstance(got[0], bytes) else got[0].encode()
    return hashlib.sha256(data).hexdigest()[:10]


# ---------------------------------------------------------------------------------------------------------------- the frame
TEXT = {
    "ko": {
        "skip": "본문으로 건너뛰기", "nav_label": "사이트 메뉴", "features": "기능", "install": "설치", "privacy": "개인정보",
        "support": "고객 지원", "menu": "메뉴", "primary": "서버 설치하기", "get_app": "앱 받기", "contact_mail": "메일 보내기",
        "close_h": "내 서버에서 일하는 AI 비서를 시작하세요", "close_line": "무료 오픈 소스(AGPL-3.0)예요. Docker가 돌아가는 컴퓨터면 설치할 수 있어요.",
        "col_kelo": "Kelo", "col_project": "프로젝트", "licence": "AGPL-3.0 오픈 소스",
        "credits": "사진: Wikimedia Commons", "credits_by": "{by}, {licence}", "adapted": "잘라서 크기 조정",
        "trademarks": "제품 이름과 로고는 각 소유자의 상표예요.",
        "other": "English", "other_lang": "en", "other_label": "Read this page in English",
        "footer_line": "직접 설치한 Kelo 서버에서 돌아가는 나만의 AI 비서.",
        "contact": "문의", "contact_value": "support@kelo.chat",
        "dl": {"android": "Android용 내려받기 (.apk)", "windows": "Windows용 내려받기 (.exe)", "windows-msi": "MSI 설치 파일",
               "mac": "macOS용 내려받기 (.dmg)"},
        "dl_meta": "버전 {version} · {size}", "dl_off": "아직 올라오지 않았어요. 곧 이곳에서 받을 수 있어요.",
        "copyright": "Kelo",
        "titles": {"index": "Kelo · 직접 설치한 서버에서 돌아가는 AI 비서", "install": "서버 설치하기 · Kelo", "privacy": "개인정보 처리방침 · Kelo", "support": "고객 지원 · Kelo",
                   "tester": "테스터 모집 · Kelo"},
        "desc": {
            "index": "Kelo는 직접 설치한 Kelo 서버에서 돌아가는 AI 비서예요. 휴대폰 앱으로 대화하고, 맡긴 일은 비서가 브라우저와 연결한 서비스로 처리해요. 데이터는 Kelo 서버에 남아요.",
            "install": "Kelo 서버를 설치하고 휴대폰을 연결하는 방법.",
            "privacy": "Kelo 앱과 서버가 어떤 정보를 어디에 두는지 설명하는 개인정보 처리방침.",
            "support": "Kelo 문의처, 시작하는 방법, 자주 묻는 질문.",
            "tester": "Google Play 비공개 테스트에 참여할 테스터를 모집해요.",
        },
        "tester_notice": {
            "sent": "신청을 받았어요. 참여 링크를 메일로 보내 드릴게요.",
            "again": "이미 신청하셨어요. 곧 메일로 연락드릴게요.",
            "invalid": "이메일 주소를 다시 확인해 주세요.",
            "consent": "동의 체크가 필요해요.",
            "limited": "잠시 뒤에 다시 시도해 주세요.",
            "full": "지금은 신청을 받지 않아요.",
            "off": "이 서버에서는 신청을 받지 않아요.",
        },
    },
    "en": {
        "skip": "Skip to content", "nav_label": "Site menu", "features": "Features", "install": "Install", "privacy": "Privacy",
        "support": "Support", "menu": "Menu", "primary": "Install the server", "get_app": "Get the app", "contact_mail": "Email us",
        "close_h": "Run your own AI agent", "close_line": "Free and open source under AGPL-3.0. Any computer that runs Docker will do.",
        "col_kelo": "Kelo", "col_project": "Project", "licence": "Open source under AGPL-3.0",
        "credits": "Photographs via Wikimedia Commons", "credits_by": "{by}, {licence}", "adapted": "cropped and resized",
        "trademarks": "Product names and logos are trademarks of their owners.",
        "other": "한국어", "other_lang": "ko", "other_label": "이 페이지를 한국어로 보기",
        "footer_line": "Your own AI assistant, running on a Kelo server you set up.",
        "contact": "Contact", "contact_value": "support@kelo.chat",
        "dl": {"android": "Download for Android (.apk)", "windows": "Download for Windows (.exe)", "windows-msi": "MSI installer",
               "mac": "Download for macOS (.dmg)"},
        "dl_meta": "Version {version} · {size}", "dl_off": "Not published yet. It will be here soon.",
        "copyright": "Kelo",
        "titles": {"index": "Kelo · an AI assistant on a server you run", "install": "Install the server · Kelo",
                   "privacy": "Privacy policy · Kelo", "support": "Support · Kelo",
                   "tester": "Become a tester · Kelo"},
        "desc": {
            "index": "Kelo is an AI assistant that runs on a Kelo server you set up. Talk to it from the phone app; it gets things done in its own browser and the services you connect. Your data stays with you.",
            "install": "How to install the Kelo server and connect your phone.",
            "privacy": "Privacy policy: what the Kelo app and server keep, and where.",
            "support": "Kelo support: how to reach us, getting started and common questions.",
            "tester": "Join the Google Play closed test of the Kelo app.",
        },
        "tester_notice": {
            "sent": "Thanks! We'll email you the link to join.",
            "again": "You're already on the list. We'll email you soon.",
            "invalid": "Please check the email address.",
            "consent": "Please tick the consent box.",
            "limited": "Please try again in a little while.",
            "full": "Sign-ups are closed for now.",
            "off": "This server does not take sign-ups.",
        },
    },
}


def href(page: str, lang: str, anchor: str = "") -> str:
    return f"{PATHS[page]}?lang={lang}{anchor}"


def canonical(page: str, lang: str) -> str:
    return CANONICAL + PATHS[page] + ("" if lang == PRIMARY else f"?lang={lang}")


def _asset_url(name: str) -> str:
    if asset(name) is None:
        raise FileNotFoundError(f"site asset {name!r} is missing")
    return f"/assets/{name}?v={version(name)}"


@cache
def body(page: str, lang: str, design: str | None = None) -> str:
    name = f"index-{design}" if design and page == "index" else page
    html = (SITE / "pages" / f"{name}.{lang}.html").read_text(encoding="utf-8")
    html = re.sub(r"\{\{art:([a-z-]+)\}\}", lambda m: art(m.group(1)), html)
    html = re.sub(r"\{\{svg:(\w+)\}\}", lambda m: character(m.group(1)), html)
    html = html.replace("{{app_open}}", escape(APP_OPEN))
    html = html.replace("{{install_sh}}", escape(INSTALL_SH)).replace("{{install_ps1}}", escape(INSTALL_PS1))
    html = html.replace("{{github}}", GITHUB)
    if "{{credits}}" in html:
        html = html.replace("{{credits}}", credits_list(lang, design))
    html = re.sub(r"\{\{shot:([a-z0-9-]+)\}\}", lambda m: f"/assets/{m.group(1)}.webp?v={version(m.group(1) + '.webp')}", html)
    html = re.sub(r"\{\{asset:([a-z0-9/.-]+)\}\}", lambda m: _asset_url(m.group(1)), html)
    return re.sub(r"\{\{href:(\w+)(#[\w-]+)?\}\}", lambda m: href(m.group(1), lang, m.group(2) or ""), html)


_DL = re.compile(r"\{\{dl:([a-z-]+)\}\}")


def download_button(platform: str, lang: str, row: dict | None) -> str:
    """A download button and its "Version · size" line; a quiet line instead when that build is not published. The MSI is the
    second choice for Windows: no button at all until it is published."""
    t = TEXT[lang]
    label = escape(t["dl"].get(platform, platform))
    if not row:
        if platform == "windows-msi":
            return ""
        return f'<p class="dl-off">{escape(t["dl_off"])}</p>'
    meta = escape(t["dl_meta"].format(version=row["version"], size=human_size(int(row["size"]))))
    return (f'<a class="btn btn--secondary dl-btn" href="/download/{escape(platform)}" data-platform="{escape(platform)}">{label}</a>'
            f'<p class="dl-meta">{meta}</p>')


def render(page: str, lang: str, apps: dict | None = None, design: str | None = None, notice: str | None = None) -> str:
    """One whole page: the frame is built once per page and language; the download buttons are filled from `apps` (the
    published builds, downloads.Downloads.latest()) every time. `design` ("a" or "b") renders that v2 design instead of the
    current one; without it, LIVE_DESIGN decides."""
    design = design or LIVE_DESIGN
    frame = _frame_v2(page, lang, design) if design else _frame(page, lang)
    html = _DL.sub(lambda m: download_button(m.group(1), lang, (apps or {}).get(m.group(1))), frame)
    if "{{tester_notice}}" in html:
        kind = "sent" if notice == "1" else (notice or "")
        msg = TEXT.get(lang, {}).get("tester_notice", {}).get(kind)
        tag = f'<p class="notice notice-{escape(kind)}">{escape(msg)}</p>' if msg else ""
        html = html.replace("{{tester_notice}}", tag)
    return html


# the photographs on the home page (site/photos), as the footer's credits list names them: (subject en, subject ko, by, licence,
# licence URL, source page, cropped beyond resizing)
CREDITS = (
    ("Night train at a platform", "밤 기차와 승강장", "Alasdair McLellan", "Public domain", "https://creativecommons.org/publicdomain/mark/1.0/",
     "https://commons.wikimedia.org/wiki/File:NSB_WLAB2_night_train_in_Trondheim.jpg", False),
    ("Server rack", "서버 랙", "Ehrobod01", "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/",
     "https://commons.wikimedia.org/wiki/File:Einsteinathome_Server_01.jpg", True),
    ("Alarm clock on a bookshelf", "책장 위 자명종", "Nenad Stojkovic", "CC BY 2.0", "https://creativecommons.org/licenses/by/2.0/",
     "https://commons.wikimedia.org/wiki/File:Old_alarm_clock_on_the_bookshelf_-_50233824038.jpg", False),
    ("Library shelves", "도서관 서가", "Eugene Ormandy", "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/",
     "https://commons.wikimedia.org/wiki/File:Bookshelf_of_Sanko_Library_2.jpg", False),
    ("Riverside path", "강변 산책로", "Chamber88w", "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/",
     "https://commons.wikimedia.org/wiki/File:Riverside_Park_path.jpg", False),
    ("Notebook with pen", "공책과 펜", "Phil Slattery", "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/",
     "https://commons.wikimedia.org/wiki/File:Notebook_with_pen,_August,_2019.jpg", False),
    ("Server cabinet", "서버 캐비닛", "Jan Prucha", "CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/",
     "https://commons.wikimedia.org/wiki/File:CITIB-servers.jpg", True),
    ("Mini PC", "미니 PC", "Goovaeh8fot6yugh", "CC0", "https://creativecommons.org/publicdomain/zero/1.0/",
     "https://commons.wikimedia.org/wiki/File:Minisforum_N40_Mini_PC_ports.jpg", False),
    ("Phone and coffee", "휴대폰과 커피", "Pixel.la", "CC0", "https://creativecommons.org/publicdomain/zero/1.0/",
     "https://commons.wikimedia.org/wiki/File:Hands-coffee-smartphone-technology_(23698591814).jpg", False),
)

# the file name stem of each photograph in CREDITS (site/photos/<stem>-<width>.webp), in the same order
PHOTO_FILES = ("photo-train", "photo-rack", "photo-clock", "photo-library", "photo-path", "photo-notebook", "photo-cabinet", "photo-minipc",
               "photo-phone")
assert len(PHOTO_FILES) == len(CREDITS)

ARROW = ('<svg class="m-arrow" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2 8h11M9 4l4 4-4 4" fill="none" '
         'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def credits_list(lang: str, design: str | None = None) -> str:
    """The photographs' credits (the privacy page's last section; the home page's footer links to it): only the photographs that
    home page (of `design`, or the current one) actually shows."""
    t = TEXT[lang]
    home = body("index", lang, design)
    rows = "".join(
        f'<li>{escape(en if lang == "en" else ko)}: <a href="{escape(src)}" rel="noopener">{escape(t["credits_by"].format(by=by, licence=lic))}</a>'
        f' (<a href="{escape(url)}" rel="noopener">{escape(lic)}</a>{", " + escape(t["adapted"]) if cut else ""})</li>'
        for (en, ko, by, lic, url, src, cut), stem in zip(CREDITS, PHOTO_FILES) if f"/assets/{stem}-" in home)
    return f'<ul class="credit-list">{rows}</ul>'


@cache
def _frame(page: str, lang: str) -> str:
    """One whole page with its {{dl:...}} placeholders. Built once per page and language (the pages do not change while the server runs).
    The <html> tag stays bare (`<html lang="..">`): enter.js puts the page's data attributes on it."""
    t = TEXT[lang]
    other = t["other_lang"]
    current = ' aria-current="page"'
    nav = "".join(
        f'<a href="{href(p, lang, a)}"{current if p == page and not a else ""}>{escape(t[k])}</a>'
        for p, a, k in (("index", "#features", "features"), ("install", "", "install"), ("privacy", "", "privacy")))
    alternates = "".join(f'<link rel="alternate" hreflang="{x}" href="{canonical(page, x)}">' for x in LANGS)
    css, js = version("site.css"), version("site.js")
    install = href("install", lang, "#steps" if page == "install" else "")
    moments = page == "index"
    head_moments = (f'<link rel="modulepreload" href="/assets/moments/moments.js?v={version("moments/moments.js")}">' if moments else "")
    tail_moments = (f'<script type="module" src="/assets/moments/moments.js?v={version("moments/moments.js")}"></script>' if moments else "")
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        f'<title>{escape(t["titles"][page])}</title><meta name="description" content="{escape(t["desc"][page])}">'
        '<meta name="page-type" content="software"><meta name="site-kind" content="software">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="theme-color" content="#fbfbfa" media="(prefers-color-scheme: light)">'
        '<meta name="theme-color" content="#121315" media="(prefers-color-scheme: dark)">'
        f'<meta property="og:title" content="{escape(t["titles"][page])}"><meta property="og:description" content="{escape(t["desc"][page])}">'
        f'<meta property="og:type" content="website"><meta property="og:url" content="{canonical(page, lang)}">'
        f'<meta property="og:image" content="{CANONICAL}/assets/share-{lang}.webp"><meta name="twitter:card" content="summary_large_image">'
        f'<link rel="canonical" href="{canonical(page, lang)}">{alternates}'
        f'<link rel="alternate" hreflang="x-default" href="{canonical(page, PRIMARY)}">'
        f'<link rel="icon" href="/assets/kelo.svg?v={version("kelo.svg")}" type="image/svg+xml">'
        f'<link rel="preload" href="/assets/familjen-grotesk.woff2" as="font" type="font/woff2" crossorigin>'
        f'<link rel="stylesheet" href="/assets/site.css?v={css}">'
        f'<script src="/assets/enter.js?v={version("enter.js")}"></script>{head_moments}'
        f'<script src="/assets/site.js?v={js}" defer></script>'
        f'</head><body class="page-{page}">'
        f'<header class="nav nav--centre"><div class="wrap nav__bar">'
        f'<a class="skip-link" href="#top">{escape(t["skip"])}</a>'
        f'<a class="nav__logo" href="{href("index", lang)}">{character("idle")}<span>Kelo</span></a>'
        f'<nav class="nav__links" id="site-menu" aria-label="{escape(t["nav_label"])}">{nav}'
        f'<a href="{GITHUB}" rel="noopener">GitHub</a></nav>'
        f'<div class="nav__actions">'
        f'<a class="nav__lang" href="{PATHS[page]}?lang={other}" hreflang="{other}" lang="{other}" '
        f'aria-label="{escape(t["other_label"])}" data-keep-hash>{escape(t["other"])}</a>'
        f'<a class="btn btn--primary" href="{install}">{escape(t["primary"])}</a>'
        f'<button class="nav__toggle" type="button" aria-expanded="false" aria-controls="site-menu">{escape(t["menu"])}</button>'
        f'</div></div></header>'
        f'<main id="top">{body(page, lang)}</main>'
        f'<footer class="closing"><div class="wrap">'
        f'<div class="close-word">'
        f'<div class="close-word__head"><div class="section-head"><h2>{escape(t["close_h"])}</h2><p>{escape(t["close_line"])}</p></div>'
        f'<a class="btn btn--primary" href="{install}">{escape(t["primary"])}{ARROW}</a></div>'
        f'<p class="close-word__name" aria-hidden="true"><span>Kelo</span>{character("sleeping")}</p>'
        f'</div>'
        f'<div class="footer__dir"><div class="footer__grid grid">'
        f'<p class="footer__lede span-6">{escape(t["footer_line"])}</p>'
        f'<div class="footer__col span-3"><h3>{escape(t["col_kelo"])}</h3><ul>'
        f'<li><a href="{href("index", lang, "#features")}">{escape(t["features"])}</a></li>'
        f'<li><a href="{href("install", lang)}">{escape(t["install"])}</a></li>'
        f'<li><a href="{href("index", lang, "#get-app")}">{escape(t["get_app"])}</a></li></ul></div>'
        f'<div class="footer__col span-3"><h3>{escape(t["col_project"])}</h3><ul>'
        f'<li><a href="{GITHUB}" rel="noopener">GitHub</a></li>'
        f'<li><a href="{href("privacy", lang)}">{escape(t["privacy"])}</a></li>'
        f'<li><a href="{href("support", lang)}">{escape(t["support"])}</a></li>'
        f'<li><a href="mailto:{escape(t["contact_value"])}">{escape(t["contact_mail"])}</a></li></ul></div>'
        f'</div>'
        f'<div class="footer__legal"><span>© 2026 {escape(t["copyright"])} · {escape(t["licence"])}</span>'
        f'<a class="credits" href="{href("privacy", lang, "#credits")}">{escape(t["credits"])}</a><span>{escape(t["trademarks"])}</span></div>'
        f'</div></div></footer>{tail_moments}</body></html>'
    )


def etag(page: str, lang: str, apps: dict | None = None) -> str:
    return '"' + hashlib.sha256(render(page, lang, apps).encode()).hexdigest()[:16] + '"'


# ---------------------------------------------------------------------------------------------------------------- the v2 frame
MOODS = ("idle", "listening", "thinking", "reading", "browser", "working", "approval", "done", "sleeping")
V2 = {
    "ko": {
        "nav": (("index", "#features", "기능"), ("index", "#privacy", "개인정보"), ("install", "", "설치 안내"), ("index", "#get-app", "앱 받기"),
                ("index", "#faq", "질문")),
        "moods": {"idle": "기다리는 중", "listening": "듣는 중", "thinking": "생각하는 중", "reading": "읽는 중", "browser": "사이트 보는 중",
                  "working": "일하는 중", "approval": "허락 기다림", "done": "끝났어요", "sleeping": "쉬는 중"},
        "cols": (("제품", (("index", "#features", "기능"), ("index", "#workroom", "작업실"), ("index", "#away", "자리를 비워도 챙겨요"),
                          ("index", "#people", "여러 사람이 쓰는 방법"), ("index", "#get-app", "앱 받기"))),
                 ("시작하기", (("install", "", "설치 안내"), ("install", "#requirements", "서버 사양"), ("index", "#run-free", "무료로 운영하기"),
                             ("index", "#reach", "밖에서 연결하는 방법"), ("latest", "", "빌드 정보 (latest.json)"))),
                 ("프로젝트", (("github", "", "GitHub"), ("privacy", "", "개인정보 처리방침"), ("support", "", "고객 지원"), ("privacy", "#credits", "사진 출처"),
                             ("mail", "", "메일 보내기")))),
        "close_h": "내 서버에서 일하는 AI 비서를 시작하세요",
        "close_line": "무료 오픈 소스(AGPL-3.0)예요. Docker가 돌아가는 컴퓨터면 설치할 수 있어요.",
        "lang_label": "언어",
    },
    "en": {
        "nav": (("index", "#features", "Features"), ("index", "#privacy", "Privacy"), ("install", "", "Install"), ("index", "#get-app", "Get the app"),
                ("index", "#faq", "FAQ")),
        "moods": {"idle": "Ready", "listening": "Listening", "thinking": "Thinking", "reading": "Reading", "browser": "Browsing",
                  "working": "Working", "approval": "Waiting for you", "done": "Done", "sleeping": "Resting"},
        "cols": (("Product", (("index", "#features", "Features"), ("index", "#workroom", "Work room"), ("index", "#away", "Follows up while you're away"),
                             ("index", "#people", "Many phones, many people"), ("index", "#get-app", "Get the app"))),
                 ("Get started", (("install", "", "Install guide"), ("install", "#requirements", "Server size"), ("index", "#run-free", "Run it for free"),
                                 ("index", "#reach", "Reaching your server"), ("latest", "", "Build info (latest.json)"))),
                 ("Project", (("github", "", "GitHub"), ("privacy", "", "Privacy policy"), ("support", "", "Support"), ("privacy", "#credits", "Photo credits"),
                             ("mail", "", "Email us")))),
        "close_h": "Start your own AI assistant",
        "close_line": "Free and open source under AGPL-3.0. Any machine that runs Docker will do.",
        "lang_label": "Language",
    },
}


def _v2_link(kind: str, anchor: str, lang: str, page: str) -> str:
    if kind == "github":
        return GITHUB
    if kind == "latest":
        return "/download/latest.json"
    if kind == "mail":
        return f"mailto:{TEXT[lang]['contact_value']}"
    if kind == "index" and page == "index" and anchor:
        return anchor
    return href(kind, lang, anchor)


@cache
def _frame_v2(page: str, lang: str, design: str) -> str:
    """One whole v2 page (design "a" or "b"): sticky top bar with the mascot showing what the section is about, the page, a closing
    band and a footer with link columns. Same head as _frame (no outside assets, CSP)."""
    t, v = TEXT[lang], V2[lang]
    other = t["other_lang"]
    nav = "".join(f'<a href="{_v2_link(k, a, lang, page)}"{" aria-current=\"page\"" if k == page and not a and k != "index" else ""}>{escape(label)}</a>'
                  for k, a, label in v["nav"])
    moods = "".join(f'<span class="mood" data-mood="{m}">{character(m)}</span>' for m in MOODS)
    labels = "|".join(f"{m}:{v['moods'][m]}" for m in MOODS)
    cols = "".join(
        f'<div class="foot__col"><h3>{escape(h)}</h3><ul>'
        + "".join(f'<li><a href="{_v2_link(k, a, lang, page)}"{" rel=\"noopener\"" if k == "github" else ""}>{escape(label)}</a></li>' for k, a, label in links)
        + "</ul></div>" for h, links in v["cols"])
    alternates = "".join(f'<link rel="alternate" hreflang="{x}" href="{canonical(page, x)}">' for x in LANGS)
    install = href("install", lang, "#steps" if page == "install" else "")
    get_app = "#get-app" if page == "index" else href("index", lang, "#get-app")
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        f'<title>{escape(t["titles"][page])}</title><meta name="description" content="{escape(t["desc"][page])}">'
        '<meta name="color-scheme" content="light dark">'
        f'<meta name="theme-color" content="{"#f7f7f5" if design == "a" else "#f1ece2"}" media="(prefers-color-scheme: light)">'
        f'<meta name="theme-color" content="{"#101113" if design == "a" else "#0d0f0e"}" media="(prefers-color-scheme: dark)">'
        f'<meta property="og:title" content="{escape(t["titles"][page])}"><meta property="og:description" content="{escape(t["desc"][page])}">'
        f'<meta property="og:type" content="website"><meta property="og:url" content="{canonical(page, lang)}">'
        f'<meta property="og:image" content="{CANONICAL}/assets/share-{lang}.webp"><meta name="twitter:card" content="summary_large_image">'
        f'<link rel="canonical" href="{canonical(page, lang)}">{alternates}'
        f'<link rel="alternate" hreflang="x-default" href="{canonical(page, PRIMARY)}">'
        f'<link rel="icon" href="/assets/kelo.svg?v={version("kelo.svg")}" type="image/svg+xml">'
        f'<link rel="preload" href="/assets/familjen-grotesk.woff2" as="font" type="font/woff2" crossorigin>'
        f'<link rel="stylesheet" href="/assets/v2/base.css?v={version("v2/base.css")}">'
        f'<link rel="stylesheet" href="/assets/v2/{design}.css?v={version(f"v2/{design}.css")}">'
        f'<script src="/assets/enter.js?v={version("enter.js")}"></script>'
        f'<script src="/assets/site.js?v={version("site.js")}" defer></script>'
        f'</head><body class="page-{page} design-{design}">'
        f'<header class="nav"><div class="wrap nav__bar">'
        f'<a class="skip-link" href="#top">{escape(t["skip"])}</a>'
        f'<a class="nav__logo" href="{href("index", lang)}"><span class="nav__moods" aria-hidden="true" data-mood="idle">{moods}</span>'
        f'<span class="nav__name">Kelo</span><span class="nav__status" aria-hidden="true" data-labels="{escape(labels)}">{escape(v["moods"]["idle"])}</span></a>'
        f'<nav class="nav__links" id="site-menu" aria-label="{escape(t["nav_label"])}">{nav}'
        f'<a class="nav__gh" href="{GITHUB}" rel="noopener">GitHub</a></nav>'
        f'<div class="nav__actions">'
        f'<a class="nav__lang" href="{PATHS[page]}?lang={other}" hreflang="{other}" lang="{other}" '
        f'aria-label="{escape(t["other_label"])}" data-keep-hash>{escape(t["other"])}</a>'
        f'<a class="btn btn--primary btn--sm" href="{install}">{escape(t["primary"])}</a>'
        f'<button class="nav__toggle" type="button" aria-expanded="false" aria-controls="site-menu"><span class="nav__burger" aria-hidden="true"></span>'
        f'<span class="sr-only">{escape(t["menu"])}</span></button>'
        f'</div></div></header>'
        f'<main id="top">{body(page, lang, design)}</main>'
        f'<section class="closing" data-tone="8" data-mood="sleeping" aria-labelledby="close-h"><div class="wrap closing__in">'
        f'<div class="closing__copy"><h2 id="close-h">{escape(v["close_h"])}</h2><p>{escape(v["close_line"])}</p>'
        f'<div class="btn-row"><a class="btn btn--primary" href="{install}">{escape(t["primary"])}{ARROW.replace("m-arrow", "i-arrow")}</a>'
        f'<a class="btn btn--secondary" href="{get_app}">{escape(t["get_app"])}</a></div></div>'
        f'<div class="closing__art" aria-hidden="true">{character("sleeping")}</div></div></section>'
        f'<footer class="foot"><div class="wrap">'
        f'<div class="foot__grid"><div class="foot__brand"><a class="foot__logo" href="{href("index", lang)}">{character("idle")}<span>Kelo</span></a>'
        f'<p>{escape(t["footer_line"])}</p>'
        f'<p class="foot__lang"><span>{escape(v["lang_label"])}</span>'
        f'<a href="{PATHS[page]}?lang=ko" hreflang="ko" lang="ko"{" aria-current=\"true\"" if lang == "ko" else ""}>한국어</a>'
        f'<a href="{PATHS[page]}?lang=en" hreflang="en" lang="en"{" aria-current=\"true\"" if lang == "en" else ""}>English</a></p></div>'
        f'{cols}</div>'
        f'<div class="foot__legal"><span>© 2026 {escape(t["copyright"])} · {escape(t["licence"])}</span><span>{escape(t["trademarks"])}</span></div>'
        f'</div></footer></body></html>'
    )
