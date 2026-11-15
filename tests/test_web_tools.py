"""web_search / web_fetch: looking things up from the server, without the browser (tools/web.py)."""

import httpx
import pytest

from assistant.agent.fence import inside, plain, render
from assistant.netguard import NetGuardError
from assistant.tools.web import MAX_TEXT, WebTools, _fetcher, parse_ddg, parse_rss, readable


def resolver(ip="93.184.216.34"):
    def resolve(host, port, *a, **k):
        return [(2, 1, 6, "", (ip, port))]
    return resolve


DDG = """<html><body>
<div class="result results_links result--ad"><a class="result__a" href="https://ads.example/x">Ad</a></div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.example.com%2Ftickets%3Fa%3D1&amp;rut=abc">Tickets &amp; prices</a></h2>
  <a class="result__snippet" href="#">Adult 1-day ticket from <b>¥475</b>.</a>
</div>
<div class="result results_links web-result">
  <h2 class="result__title"><a class="result__a" href="https://blog.example/post">A blog post</a></h2>
</div>
</body></html>"""

RSS = """<?xml version="1.0" encoding="utf-8" ?><rss version="2.0"><channel><title>Bing</title>
<item><title>Shanghai Disneyland Tickets</title><link>https://tickets.example/sh</link><description>From &amp;yen;475</description></item>
<item><title>bad</title><link>javascript:alert(1)</link><description>x</description></item>
</channel></rss>"""

PAGE = """<html><head><title> Ticket prices </title><script>var x=1</script></head><body>
<header><nav><a href="/">Home</a> <a href="/a">Menu item</a></nav></header>
<main><h1>Shanghai Disneyland tickets</h1><p>""" + ("Standard 1-day ticket costs ¥475 to ¥799 depending on the date. " * 12) + """</p></main>
<footer>Copyright and links</footer></body></html>"""


def tools_with(handler, ip="93.184.216.34"):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)
    w = WebTools(fetch=_fetcher(transport=httpx.MockTransport(wrapped), resolver=resolver(ip)), language=lambda: "ko-KR",
                 sleep=_no_sleep)
    return w, seen


async def _no_sleep(_s):
    return None


def test_parse_ddg_skips_ads_and_decodes_the_real_address():
    found = parse_ddg(DDG)
    assert [r["url"] for r in found] == ["https://www.example.com/tickets?a=1", "https://blog.example/post"]
    assert found[0]["title"] == "Tickets & prices" and "¥475" in found[0]["snippet"] and found[1]["snippet"] == ""


def test_parse_rss_keeps_only_web_addresses():
    assert parse_rss(RSS) == [{"title": "Shanghai Disneyland Tickets", "url": "https://tickets.example/sh", "snippet": "From ¥475"}]


def test_readable_keeps_the_main_text_and_drops_menus_and_footers():
    title, text = readable(PAGE)
    assert title == "Ticket prices" and "¥475 to ¥799" in text
    assert "Menu item" not in text and "Copyright" not in text and "var x" not in text


async def test_search_posts_to_duckduckgo_with_an_honest_agent_and_the_users_language():
    w, seen = tools_with(lambda r: httpx.Response(200, text=DDG, headers={"content-type": "text/html; charset=utf-8"}))
    out = await w.web_search({"query": "상해 디즈니랜드 입장권 최저가", "count": 5})
    assert "(duckduckgo)" in out and "1. Tickets & prices\n   https://www.example.com/tickets?a=1" in out and "web_fetch" in out
    req = seen[0]
    assert req.method == "POST" and req.headers["host"] == "html.duckduckgo.com" and "Kelo" in req.headers["user-agent"]
    assert req.headers["accept-language"].startswith("ko-KR") and b"q=" in req.content


async def test_search_falls_back_to_bing_when_duckduckgo_shows_its_robot_check():
    def handler(r):
        if r.headers["host"] == "html.duckduckgo.com":
            return httpx.Response(202, text="<div class='anomaly-modal'>robot?</div>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=RSS, headers={"content-type": "application/rss+xml"})
    w, seen = tools_with(handler)
    out = await w.web_search({"query": "shanghai disneyland"})
    assert "(bing)" in out and "https://tickets.example/sh" in out
    assert seen[1].url.params["format"] == "rss" and seen[1].url.params["q"] == "shanghai disneyland"


async def test_search_says_so_when_no_engine_answers():
    w, _ = tools_with(lambda r: httpx.Response(503, text="down", headers={"content-type": "text/html"}))
    with pytest.raises(NetGuardError, match="no search engine answered"):
        await w.web_search({"query": "x"})
    assert (await w.web_search({"query": "  "})).startswith("Error")


async def test_requests_to_one_engine_are_spaced():
    waits = []

    async def sleep(s):
        waits.append(s)
    w = WebTools(fetch=_fetcher(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=DDG, headers={"content-type": "text/html"})),
                                resolver=resolver()), clock=lambda: 100.0, sleep=sleep)
    await w.web_search({"query": "a"})
    await w.web_search({"query": "b"})
    assert waits == [1.0]


async def test_fetch_returns_readable_text_and_reads_on_with_start():
    long = "<html><title>T</title><body><main>" + "<p>" + ("word " * 3000) + "</p></main></body></html>"
    w, _ = tools_with(lambda r: httpx.Response(200, text=long, headers={"content-type": "text/html"}))
    first = await w.web_fetch({"url": "https://example.com/a"})
    assert plain(first).startswith("URL: https://example.com/a\nTitle: T\n") and f"start={MAX_TEXT}" in first
    assert inside(render(first, source="web")).startswith("Title: T\n") and "start=" not in inside(render(first, source="web"))   # the note is ours, outside
    more = await w.web_fetch({"url": "https://example.com/a", "start": MAX_TEXT})
    assert "text cut" not in more


async def test_fetch_says_when_the_site_refuses_or_needs_javascript():
    w, _ = tools_with(lambda r: httpx.Response(403, text="<html><body>Please enable JS</body></html>", headers={"content-type": "text/html"}))
    assert "HTTP 403" in await w.web_fetch({"url": "https://example.com/"})
    js = "<html><head><script src='/app.js'></script></head><body><div id='root'></div></body></html>"
    w, _ = tools_with(lambda r: httpx.Response(200, text=js, headers={"content-type": "text/html"}))
    assert "browser_open" in await w.web_fetch({"url": "https://example.com/"})


async def test_fetch_follows_redirects_checking_each_hop():
    def handler(r):
        if r.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.com/new"})
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
    w, seen = tools_with(handler)
    out = await w.web_fetch({"url": "http://example.com/old"})   # plain http on port 80 is fine for reading
    assert out.startswith("URL: https://example.com/new") and [r.url.scheme for r in seen] == ["http", "https"]


@pytest.mark.parametrize("url", ["http://example.com:8080/", "https://user:pw@example.com/", "https://10.0.0.1/", "https://localhost/",
                                 "ftp://example.com/x"])
async def test_fetch_refuses_what_is_not_a_plain_public_page(url):
    w, seen = tools_with(lambda r: httpx.Response(200, text="x"))
    if url.startswith("ftp"):
        assert (await w.web_fetch({"url": url})).startswith("Error")
    else:
        with pytest.raises(NetGuardError):
            await w.web_fetch({"url": url})
    assert seen == []


async def test_fetch_refuses_a_name_that_resolves_to_a_private_address():
    w, seen = tools_with(lambda r: httpx.Response(200, text="x"), ip="192.168.1.20")
    with pytest.raises(NetGuardError, match="non-public"):
        await w.web_fetch({"url": "https://intranet.example.com/"})
    assert seen == []


async def test_fetch_refuses_files_that_are_not_text():
    w, _ = tools_with(lambda r: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}))
    with pytest.raises(NetGuardError, match="not a text page"):
        await w.web_fetch({"url": "https://example.com/a.pdf"})


def test_the_tools_only_read_need_no_card_and_mark_what_they_bring_as_untrusted():
    tools = {t.name: t for t in WebTools().tools()}
    assert set(tools) == {"web_search", "web_fetch"}
    assert all(t.untrusted and t.read_only and not t.requires_approval for t in tools.values())
