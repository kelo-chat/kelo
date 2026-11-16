"""The public website served at "/", "/install" and "/privacy" (website.py), and the pairing panel on "/"."""

import re

import httpx

from assistant import website
from assistant.landing import APP_OPEN
from assistant.server import create_app

from test_server import Running, scripted, settings


async def test_pages_answer_in_both_languages_and_keep_the_pairing_panel():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        for path in ("/", "/install", "/privacy"):
            ko = await c.get(path, headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
            en = await c.get(path, headers={"Accept-Language": "en-US,en;q=0.9"})
            assert ko.status_code == en.status_code == 200
            assert '<html lang="ko">' in ko.text and '<html lang="en">' in en.text
            assert ko.headers["content-language"] == "ko" and "Accept-Language" in ko.headers["vary"]
            assert "default-src 'none'" in ko.headers["content-security-policy"]
            assert "https://" not in re.sub(r'(href|content)="https://kelo\.chat[^"]*"', "", ko.text.split("<body", 1)[0])  # no outside assets
        home_ko = (await c.get("/", headers={"Accept-Language": "ko"})).text
        home_en = (await c.get("/", headers={"Accept-Language": "en"})).text
        # pairing links (https://<host>/#pair=CODE) open "/": the panel and the same intent link are always in the page
        for html in (home_ko, home_en):
            assert 'id="pair"' in html and 'id="pair-copy"' in html and "package=chat.kelo.app" in html
            assert APP_OPEN.replace(";", "&#x3b;") in html or APP_OPEN in html
            assert '/assets/site.js?v=' in html
        assert "Kelo 앱 열기" in home_ko and "Open the Kelo app" in home_en
        assert "서버 설치하기" in home_ko and "Install the server" in home_en
        # the app is not published yet: no download links
        assert ".apk" not in home_ko and "play.google.com" not in home_ko


async def test_language_choice_query_header_and_default():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert '<html lang="en">' in (await c.get("/")).text                                         # no header: English (primary)
        assert '<html lang="en">' in (await c.get("/", headers={"Accept-Language": "ja"})).text       # neither: English
        assert '<html lang="ko">' in (await c.get("/", headers={"Accept-Language": "ko-KR,ko;q=0.9"})).text  # Korean first: Korean
        assert '<html lang="ko">' in (await c.get("/", headers={"Accept-Language": "ja,ko;q=0.8"})).text
        assert '<html lang="en">' in (await c.get("/", headers={"Accept-Language": "en-US,en;q=0.9,ko;q=0.8"})).text
        assert '<html lang="en">' in (await c.get("/?lang=en", headers={"Accept-Language": "ko"})).text
        page = (await c.get("/privacy?lang=ko", headers={"Accept-Language": "en"})).text
        assert '<html lang="ko">' in page and 'href="/privacy?lang=en"' in page and "data-keep-hash" in page
        assert 'href="/install?lang=ko"' in page                                                      # links keep the language
        assert '<html lang="en">' in (await c.get("/?lang=xx")).text
        home = (await c.get("/", headers={"Accept-Language": "ko"})).text                             # English is the canonical page
        assert '<link rel="canonical" href="https://kelo.chat/?lang=ko">' in home
        assert '<link rel="alternate" hreflang="x-default" href="https://kelo.chat/">' in home
        assert '<link rel="alternate" hreflang="en" href="https://kelo.chat/">' in home


async def test_assets_cache_and_etag():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        v = website.version("site.css")
        css = await c.get(f"/assets/site.css?v={v}")
        assert css.status_code == 200 and css.headers["content-type"].startswith("text/css") and "immutable" in css.headers["cache-control"]
        assert ".m-idle .kelo-breathe" in css.text and "@keyframes idle-breathe" in css.text and "prefers-reduced-motion" in css.text
        assert "immutable" not in (await c.get("/assets/site.css?v=old")).headers["cache-control"]
        js = await c.get("/assets/site.js")
        assert js.status_code == 200 and "#pair=" in js.text
        assert (await c.get("/assets/kelo.svg")).headers["content-type"].startswith("image/svg+xml")
        assert (await c.get("/assets/nope.txt")).status_code == 404
        # app screenshots on the home page are served from /assets as webp, from this server only
        for lang in website.LANGS:
            shots = re.findall(r'src="(/assets/[a-z0-9-]+\.webp\?v=\w+)"', website.render("index", lang))
            assert len(shots) >= 5
            got = await c.get(shots[0])
            assert got.status_code == 200 and got.headers["content-type"] == "image/webp" and "immutable" in got.headers["cache-control"]
        assert (await c.get("/assets/..%2Fstyle.css")).status_code == 404 and (await c.get("/assets/nope.webp")).status_code == 404
        # fonts, photographs, logos and the home page's moments come from this server too; nothing outside site/ is reachable
        for name, kind in (("familjen-grotesk.woff2", "font/woff2"), ("photo-train-1280.webp", "image/webp"), ("logo-apple.svg", "image/svg+xml"),
                           ("moments/moments.js", "text/javascript"), ("moments/iris-opens/moment.css", "text/css"), ("enter.js", "text/javascript")):
            got = await c.get(f"/assets/{name}")
            assert got.status_code == 200 and got.headers["content-type"].startswith(kind), name
        for bad in ("moments/../style.css", "moments/..%2F..%2Fwebsite.py", "fonts/familjen-grotesk.woff2", "pages/index.en.html", "style.css"):
            assert (await c.get(f"/assets/{bad}")).status_code == 404, bad
        assert "font-src 'self'" in website.CSP
        first = await c.get("/install", headers={"Accept-Language": "en"})
        again = await c.get("/install", headers={"Accept-Language": "en", "If-None-Match": first.headers["etag"]})
        assert again.status_code == 304 and again.content == b""


async def test_other_routes_are_untouched():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/api/goals")).status_code == 401
        assert (await c.get("/oauth/relay")).status_code == 400


def test_mascots_are_scoped_and_every_page_renders():
    for page in website.PAGES:
        for lang in website.LANGS:
            html = website.render(page, lang)
            assert "{{" not in html and "<style" not in html   # the SVGs' styles moved to site.css (CSP: style-src 'self')
            assert 'class="mascot m-' in html
            # owner's wording rules: the server's machine is not "your computer", no address-name scheme, no subscription sign-ins
            for banned in ("내 컴퓨터", "your computer", "your own computer", "이름.kelo.chat", "name.kelo.chat", "구독", "subscription",
                           "AES-256", "ChatGPT", "Antigravity", "Dropbox"):
                assert banned not in html, (page, lang, banned)
    install = website.render("install", "ko") + website.render("install", "en")
    # server sizes: one line each, minimum then recommended
    for line in ("RAM 2GB · CPU 2코어 · 남은 디스크 10GB", "RAM 4GB · CPU 4코어 · 남은 디스크 20GB",
                 "2 GB RAM · 2 CPU cores · 10 GB free disk", "4 GB RAM · 4 CPU cores · 20 GB free disk"):
        assert line in install, line
    from html import escape
    for page in ("index", "install"):   # the install commands (the public repository, then setup), with their copy button
        for lang in website.LANGS:
            html = website.render(page, lang)
            assert escape(website.INSTALL_SH) in html and "data-copy" in html and 'data-tabs' in html
            assert "github.com/kelo-chat/kelo" in html and 'class="soon"' not in html and "곧 열려요" not in html
    names = re.findall(r"@keyframes\s+([\w-]+)", website.stylesheet())
    assert len(names) == len(set(names))


def test_the_run_it_for_free_tip_is_on_the_home_page_in_every_language():
    marks = {"ko": ("무료로 운영하기", "Kelo 주소(현재 한정 제공)", "Pay As You Go", "README.ko.md#팁-kelo를-무료로-운영하기"),
             "en": ("Run it for free", "Kelo address (offered for now)", "Pay As You Go", "#tip-run-kelo-for-free")}
    assert set(marks) == set(website.LANGS)   # a new site language needs its own tip
    for lang in website.LANGS:
        html = website.render("index", lang)
        tip = html.split('<aside class="tip" id="free"', 1)[1].split("</aside>", 1)[0]
        for text in (*marks[lang], "Oracle", "FreeLLMAPI", "Custom endpoint", "2 OCPU", "12 GB", "20%"):
            assert text in tip, (lang, text)
        assert f'href="{website.GITHUB}' in tip and "{{" not in tip
        assert "<img" not in tip and "<svg" not in tip   # no third-party logos on the site
        for banned in ("kelo.chat", "OAuth", "e2-micro", "AWS", "Lambda"):   # no address details, no sign-in talk, no other clouds
            assert banned not in tip, (lang, banned)


# ---------------------------------------------------------------------------------------------------------------- the v2 designs
def test_both_v2_designs_render_every_page_with_their_own_stylesheet():
    assert set(website.DESIGNS) == {"a", "b"}
    for design in website.DESIGNS:
        for page in website.PAGES:
            for lang in website.LANGS:
                html = website.render(page, lang, design=design)
                assert "{{" not in html and "<style" not in html, (design, page, lang)
                assert f'class="page-{page} design-{design}"' in html and f"/assets/v2/{design}.css?v=" in html and "/assets/v2/base.css?v=" in html
                assert "/assets/enter.js?v=" in html and "/assets/site.js?v=" in html and 'class="nav__moods"' in html
                assert 'class="closing" data-tone="8"' in html and f'<html lang="{lang}">' in html
                for banned in ("내 컴퓨터", "your computer", "your own computer", "이름.kelo.chat", "name.kelo.chat", "구독", "subscription",
                               "ChatGPT", "Antigravity"):
                    assert banned not in html, (design, page, lang, banned)
                head = html.split("<body", 1)[0]
                assert "https://" not in re.sub(r'(href|content)="https://kelo\.chat[^"]*"', "", head)   # no outside assets
            home = website.render("index", lang, design=design)
            # the home page: pairing panel, ground tones in page order, every in-page link has its target
            assert 'id="pair"' in home and 'id="pair-copy"' in home
            tones = [int(t) for t in re.findall(r'data-tone="(\d)"', home)]
            assert tones == sorted(tones) and tones[0] == 0 and tones[-1] == 8, (design, tones)
            ids = set(re.findall(r'\sid="([\w-]+)"', home))
            for anchor in set(re.findall(r'href="#([\w-]+)"', home)) - {"pair"}:
                assert anchor in ids, (design, lang, anchor)
            assert "Kelo 주소 (현재 한정 제공)" in home if lang == "ko" else "Kelo address (offered for now)" in home


async def test_v2_stylesheets_are_served_with_the_mascot_animations_on_base():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        for name in ("v2/base.css", "v2/a.css", "v2/b.css"):
            got = await c.get(f"/assets/{name}?v={website.version(name)}")
            assert got.status_code == 200 and got.headers["content-type"].startswith("text/css") and "immutable" in got.headers["cache-control"], name
            assert "prefers-color-scheme: dark" in got.text or name == "v2/base.css", name
        base = (await c.get("/assets/v2/base.css")).text
        assert "@keyframes idle-breathe" in base and "prefers-reduced-motion: reduce" in base
        for bad in ("v2/c.css", "v2/../style.css", "v2/base.js"):
            assert (await c.get(f"/assets/{bad}")).status_code == 404, bad
    for design in website.DESIGNS:   # each design sets every ground tone the pages use, for light and dark
        css = website.asset(f"v2/{design}.css")[0]
        for n in range(9):
            assert css.count(f"--tone-{n}:") >= 2, (design, n)


def test_the_privacy_policy_opens_with_a_summary_that_links_to_its_sections():
    for design in (None, *website.DESIGNS):
        for lang in website.LANGS:
            html = website.render("privacy", lang, design=design)
            summary = html.split('<section class="summary"', 1)[1].split("</section>", 1)[0]
            ids = set(re.findall(r'\sid="([\w-]+)"', html))
            links = re.findall(r'href="#([\w-]+)"', summary)
            assert {"model", "operator", "third", "app", "keep"} <= set(links) <= ids, (design, lang, links)
            for text in ("Cloudflare", "ML Kit", "Google", "GitHub"):
                assert text in summary, (lang, text)
            assert ("Kelo 주소" if lang == "ko" else "Kelo address") in summary
            assert html.index('class="summary"') < html.index('id="scope"')   # before section 1


def test_android_versions_are_named_plainly_everywhere():
    for design in (None, *website.DESIGNS):
        for page in website.PAGES:
            for lang in website.LANGS:
                html = website.render(page, lang, design=design)
                for banned in ("전체 기능 빌드", "full build", "APK 버전", "APK version", "sideload", "flavor", "adb", "파일로 설치",
                               "installed from a file"):
                    assert banned not in html, (design, page, lang, banned)
        for lang, label in (("ko", "kelo.chat에서 받은 앱에서만"), ("en", "only in the app downloaded from kelo.chat")):
            privacy = website.render("privacy", lang, design=design)
            assert 'id="android-versions"' in privacy and label in privacy and "Health Connect" in privacy, (design, lang)


def test_design_b_draws_the_servers_and_credits_only_the_photos_it_shows():
    for design in website.DESIGNS:
        for lang in website.LANGS:
            home = website.render("index", lang, design=design)
            for gone in ("photo-rack", "photo-minipc", "photo-cabinet"):
                assert gone not in home, (design, gone)
            assert 'class="art art--home-server"' in home and 'class="art art--free-cloud"' in home and "{{" not in home
            assert ('class="art art--night"' in home) == (design == "b")
            credits = website.render("privacy", lang, design=design).split('id="credits"', 1)[1]
            for gone in ("Einsteinathome_Server", "Minisforum", "CITIB-servers"):
                assert gone not in credits, (design, lang, gone)
            for shown in re.findall(r"/assets/(photo-[a-z]+)-\d+\.webp", home):
                stem = website.PHOTO_FILES.index(shown)
                assert website.CREDITS[stem][5] in credits, (design, lang, shown)   # every photo shown is credited
    live = website.render("privacy", "en").split('id="credits"', 1)[1]   # the live site is design B now: no server photos, no credits
    assert website.LIVE_DESIGN == "b" and "Einsteinathome_Server" not in live and "Minisforum" not in live


async def test_the_support_page_answers_in_both_languages_and_every_footer_links_to_it():
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        ko = (await c.get("/support", headers={"Accept-Language": "ko"})).text
        en = (await c.get("/support", headers={"Accept-Language": "en"})).text
        assert '<html lang="ko">' in ko and '<html lang="en">' in en
        assert "support@kelo.chat" in ko and "support@kelo.chat" in en
        assert "앱과 내 서버 사이의 대화는 종단간 암호화돼요. 오가는 길에서는 내 폰과 내 서버 말고는 아무도 읽을 수 없어요. 답을 만들 때는 내가 고른 AI 회사로 대화가 가요." in ko
        assert "내 컴퓨터" not in ko and "my computer" not in en.lower() and "subscription" not in en.lower() and "구독" not in ko
        assert 'href="/privacy?lang=ko"' in ko and 'href="/privacy?lang=en"' in en and "assistant phone add" in en
        for lang in ("ko", "en"):
            for page in ("/", "/install", "/privacy", "/support"):
                html = (await c.get(f"{page}?lang={lang}")).text
                assert f'href="/support?lang={lang}"' in html, (page, lang)
            for design in website.DESIGNS:
                assert f'href="/support?lang={lang}"' in website.render("index", lang, design=design), (design, lang)


async def test_the_home_page_links_the_stores_with_badges_the_site_serves_itself():
    stores = {"https://apps.microsoft.com/detail/9P54HX2TX361": "badge-microsoft", "https://apps.apple.com/": "badge-appstore"}
    alts = {"en": ("Get it from Microsoft", "Download on the App Store", "Android beta testing – sign up"),
            "ko": ("Microsoft Store에서 받기", "App Store에서 다운로드", "Android 베타 테스트 – 신청하기")}
    async with Running(create_app(settings(), tools=[], stream_fn=scripted())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        for lang in website.LANGS:
            for design in (None, "a", "b"):
                html = website.render("index", lang, design=design or website.LIVE_DESIGN)
                for url in stores:
                    assert f'href="{url}' in html, (lang, url)
                assert "id6832422201" in html and f'href="/tester?lang={lang}"' in html
                for name in ("badge-microsoft", "badge-appstore", "badge-android-beta"):
                    srcs = set(re.findall(rf'src="(/assets/{name}-{lang}\.svg\?v=\w+)"', html))
                    assert len(srcs) == 1, (lang, name)
                    got = await c.get(srcs.pop())
                    assert got.status_code == 200 and got.headers["content-type"].startswith("image/svg+xml")
                    assert (await c.get(f"/assets/{name}-{lang}.svg")).status_code == 200   # the README links it without ?v=
                for alt in alts[lang]:
                    assert f'alt="{alt}"' in html, (lang, alt)
                ms_hl = "ko-kr&amp;gl=KR" if lang == "ko" else "en-us&amp;gl=US"
                assert f"9P54HX2TX361?hl={ms_hl}" in html
                assert not re.findall(r'<img[^>]+src="(?!/assets/)', html), (lang, design)
    # no page (the old home pages included) still says the iPhone app is in review or being built
    for page in sorted(website.SITE.joinpath("pages").glob("*.html")):
        html = page.read_text(encoding="utf-8")
        for stale in ("App Store 심사", "App Store review", "being built", "만들고 있어요", "준비 중이고"):
            assert stale not in html, (page.name, stale)
