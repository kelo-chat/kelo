"""A GET to an address (web_fetch, browser_open) is a way to send data (audit F12): after outside text, a host the owner did not name asks
for a card (outside_rule.py), and the privacy guard reads the host labels as well as the path and query (privacy_guard.check_url)."""

from assistant.agent import Tool
from assistant.effects import Effect
from assistant.outside_rule import URL_TOOLS

from test_outside_rule import RULE, gate_for, review
from test_privacy_guard import guard

CHAT = {"source": "chat", "unattended": False, "request": "read the article"}


def fetch(url, name="web_fetch"):
    async def handler(args):
        return "done"
    return Tool(name, name, {"type": "object"}, handler)


def test_the_url_tools_are_the_two_that_fetch_an_address():
    assert URL_TOOLS == {"web_fetch", "browser_open"}


def test_a_get_after_outside_text_to_an_unnamed_host_asks_and_names_the_host():
    rev = review(CHAT, seen=True)
    line = rev.line(None, "web_fetch", {"url": "https://collector.example/track?x=1"})
    assert line is not None and "collector.example" in line
    assert rev.line(None, "browser_open", {"url": "collector.example/t"}) is not None


def test_a_get_to_a_host_the_owner_named_or_with_no_outside_text_needs_no_card():
    named = review({**CHAT, "request": "fetch https://docs.example.org/page for me"}, seen=True)
    assert named.line(None, "web_fetch", {"url": "https://docs.example.org/other"}) is None
    assert review(CHAT, seen=False).line(None, "web_fetch", {"url": "https://collector.example/x"}) is None
    assert review(CHAT, seen=True).line(None, "web_fetch", {"url": ""}) is None


async def test_the_gate_asks_for_a_get_after_outside_text_even_where_the_owner_allows_everything():
    gate = gate_for(review(CHAT, seen=True))
    verdict = await gate.check(fetch("https://collector.example/track"), {"url": "https://collector.example/track"})
    assert verdict.action == "ask" and "collector.example" in verdict.card
    quiet = gate_for(review(CHAT, seen=False))
    assert (await quiet.check(fetch("https://collector.example/track"), {"url": "https://collector.example/track"})).action == "pass"


def test_the_outside_rule_is_pure_for_a_url_effect():
    effect = Effect("send", None, ["site:collector.example"], "", "web_fetch")
    assert RULE.needs_card(effect, True, set(), "") is not None
    assert RULE.needs_card(effect, True, {"site:collector.example"}, "") is None


def test_a_value_in_a_host_label_is_found_like_one_in_the_path():
    g = guard()
    hit = g.check_url("web_fetch", {"url": "https://01055550101.collector.example/ping"})
    assert hit is not None and "phone" in hit.categories
    assert g.check_url("web_fetch", {"url": "https://collector.example/ping?x=01055550101"}) is not None
    assert g.check_url("web_fetch", {"url": "https://docs.example.org/page?id=7"}) is None
    assert g.check_url("web_fetch", {"url": "https://my.example.com/ping"}) is None


def test_the_scheme_and_the_port_are_not_scanned_as_values():
    g = guard()
    assert g.check_url("web_fetch", {"url": "https://docs.example.org:8443/page"}) is None
