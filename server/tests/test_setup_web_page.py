"""The setup page itself: one self-contained file (no external URL), the token only from the URL fragment, no HTML injection."""

import re
from pathlib import Path

from assistant import setup_web

HTML = Path(setup_web.PAGE).read_text(encoding="utf-8")


def test_the_page_file_is_next_to_the_module_and_is_one_file():
    assert setup_web.PAGE.name == "setup_web.html" and setup_web.PAGE.is_file()
    assert HTML.startswith("<!doctype html>") and HTML.rstrip().endswith("</html>")


def test_no_external_url_at_all():
    # no scripts, styles, fonts or images from elsewhere; the only links are the ones the wizard sends (built at run time)
    assert not re.search(r"https?://", HTML)
    assert not re.search(r"<(script|link|img|iframe)\b[^>]*\bsrc=", HTML)
    assert not re.search(r"<link\b", HTML)
    assert not re.search(r"@import|url\(\s*['\"]?(?:https?:)?//", HTML)
    assert not re.search(r"\bsrc\s*=\s*['\"]\s*(?:https?:)?//", HTML)


def test_the_token_comes_from_the_fragment_and_is_sent_only_as_a_header():
    assert "location.hash" in HTML and "#t=" not in HTML.split("<script>", 1)[1].split("const TOKEN", 1)[0]
    assert re.search(r"const TOKEN = \(location\.hash\.match\(/[^/]+/\)", HTML)
    assert 'history.replaceState(null, "", location.pathname)' in HTML       # the token leaves the address bar at once
    assert HTML.count('"X-Kelo-Setup": TOKEN') + HTML.count('"X-Kelo-Setup":TOKEN') >= 2
    for storage in ("localStorage", "sessionStorage", "document.cookie", "?t=", "window.name"):
        assert storage not in HTML, storage


def test_the_page_never_builds_html_from_text():
    # server text (questions, labels, commands, errors) is put in with textContent only
    assert "innerHTML" not in HTML and "outerHTML" not in HTML and "insertAdjacentHTML" not in HTML
    assert "document.write" not in HTML and "eval(" not in HTML


def test_a_link_becomes_clickable_only_when_it_is_https_or_plain_loopback():
    """Finding 19: the QR card's href is set only through webLink(); the same patterns, run here, decide what may be a link."""
    assert HTML.count("link.href = ") == 1 and "if (webLink(ev.link)) link.href = ev.link;" in HTML
    m = re.search(r"function webLink\(u\) \{\s*return /(.+?)/i\.test\(u\) \|\| /(.+?)/i\.test\(u\);", HTML)
    assert m, "webLink() must be the two literal patterns"
    patterns = [re.compile(p, re.I) for p in m.groups()]

    def ok(u):
        return any(p.search(u) for p in patterns)

    assert ok("https://pat.kelo.chat/#pair=one-time") and ok("http://127.0.0.1:8080/#pair=x") and ok("http://localhost:9/")
    for bad in ("javascript:alert(1)", "data:text/html,<script>x</script>", "http://example.com/", "https://", "https://a b.example/",
                "HTTP://evil.example/", "kelo://pair?x=1"):
        assert not ok(bad), bad


def test_the_page_polls_the_events_and_answers_with_post_only():
    assert '"/events?after="' in HTML and 'method: "POST"' in HTML and '"/answer"' in HTML
    assert "application/json" in HTML


def test_layout_rules_for_light_dark_and_narrow_screens():
    assert "prefers-color-scheme: dark" in HTML
    assert "overflow-wrap: anywhere" in HTML
    assert "max-width: 360px" in HTML and "max-width: 640px" in HTML
    assert "min-height: 44px" in HTML                                       # touch-sized buttons and choice rows
    assert 'name="viewport"' in HTML and "width=device-width" in HTML


def test_the_page_has_every_card_the_wizard_can_show():
    for kind in ("choose", "ask", "confirm", "qr", "summary", "fail", "live", "log", "live_end", "resolved", "hello", "banner",
                 "step", "ok", "warn", "info"):
        assert f'"{kind}"' in HTML, kind
    assert "ev.matrix" in HTML and "liveLines.slice(-12)" in HTML            # the QR from the matrix; the last 12 lines
    assert '"radiogroup"' in HTML and "ev.options" in HTML                   # radio rows with the hint under the label


def test_the_page_is_served_as_it_is(tmp_path):
    from http.client import HTTPConnection

    url, srv = setup_web.serve(lambda ui: 0, lang="en", grace=0.1)
    try:
        conn = HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{srv.server_port}"})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        assert resp.status == 200 and body == setup_web.PAGE.read_bytes()
        assert resp.getheader("Content-Type").startswith("text/html")
    finally:
        srv.stop()
        srv.closed.wait(5)
