"""Kelo's own help pages (help_docs.py, server/content/help/*.md): the search finds the right page for questions in English and, with a
product word in them, in Korean; a question with no match gets the page list, not a guess; the tool is read-only and asks for no card;
the system prompt tells the agent to read it first; the pages stay English and small."""

import re
from pathlib import Path

import pytest

from assistant.agent.toolbox import CORE_TOOLS
from assistant.help_docs import PAGE_CHARS, HelpDocs
from assistant.home import Skills
from assistant.prompt import PromptBuilder
from assistant.server import Settings, create_app
from assistant.tool_labels import hidden, label

CONTENT = Path(__file__).resolve().parents[1] / "content"
HELP = CONTENT / "help"
AREAS = {"chat-and-approvals", "browser", "schedules-and-watches", "memory-and-forget", "payments", "connectors", "keychain",
         "phone-and-devices", "server-and-updates"}


@pytest.fixture(scope="module")
def docs():
    return HelpDocs(HELP)


def shown_pages(docs, query):
    """The pages of the passages the tool shows for a question (the agent reads all of them)."""
    hits = docs.search(query)
    assert hits, query
    return [h["page"] for h in hits]


def test_there_is_one_page_per_area_with_a_title_and_a_summary(docs):
    docs.refresh()
    assert set(docs.pages) == AREAS
    for p in docs.pages.values():
        assert p.title and len(p.summary) > 40, p.name
        assert len(p.text) < PAGE_CHARS, p.name        # a page is read whole in one answer


@pytest.mark.parametrize("question,page", [
    ("can you tidy my email inbox", "connectors"),
    ("how do I make a reminder that repeats every day", "schedules-and-watches"),
    ("watch a page and tell me when the price drops", "schedules-and-watches"),
    ("forget something you remembered about me", "memory-and-forget"),
    ("do you ask before paying for something", "payments"),
    ("what happens when the order outcome is unknown after pressing pay", "payments"),
    ("where are my passwords stored", "keychain"),
    ("recovery code Keyguard phone", "keychain"),
    ("can Kelo read my text messages and contacts", "phone-and-devices"),
    ("how do I update the server", "server-and-updates"),
    ("connect my phone with Tailscale", "server-and-updates"),
    ("browse through a VPN", "browser"),
    ("allow ask off permission card", "chat-and-approvals"),
])
def test_english_questions_find_their_page(docs, question, page):
    assert page in shown_pages(docs, question)


@pytest.mark.parametrize("question,page", [
    ("Gmail 메일 정리도 돼?", "connectors"),
    ("Tailscale 로 폰 연결하는 방법", "server-and-updates"),
    ("VPN 으로 브라우저 쓸 수 있어?", "browser"),
    ("Keyguard 가 뭐야", "keychain"),
])
def test_korean_questions_with_a_product_word_find_their_page(docs, question, page):
    assert page in shown_pages(docs, question)


async def test_a_question_with_no_match_gets_the_page_list_and_an_instruction_not_a_guess(docs):
    out = await docs.tool().handler({"query": "메일 정리도 돼?"})        # Korean words only: the pages are English
    assert "No passage matches" in out and "English" in out
    for name in AREAS:
        assert f"- {name}:" in out
    assert "[help page:" not in out


async def test_the_tool_returns_passages_with_their_page_names_and_can_read_a_whole_page(docs):
    out = await docs.tool().handler({"query": "forget plan one confirmation"})
    assert out.startswith("[help page: memory-and-forget") and "Pages: memory-and-forget" in out
    whole = await docs.tool().handler({"page": "Payments"})
    assert whole.startswith("[help page: payments]") and "# Payments" in whole
    missing = await docs.tool().handler({"page": "nope"})
    assert "No help page named 'nope'" in missing and "- payments:" in missing


async def test_no_pages_means_the_tool_says_so(tmp_path):
    out = await HelpDocs(tmp_path / "none").tool().handler({"query": "schedules"})
    assert "No help pages are installed" in out


async def test_a_changed_page_is_searched_without_a_restart(tmp_path):
    (tmp_path / "a.md").write_text("# Alpha\n\nThe first page about apples and nothing else at all here.\n\n## Fruit\nApples are red.\n")
    d = HelpDocs(tmp_path)
    assert [h["page"] for h in d.search("apples")] == ["a", "a"]
    (tmp_path / "a.md").write_text("# Alpha\n\nThe first page about pears and nothing else at all here.\n\n## Fruit\nPears are green and long.\n")
    (tmp_path / "b.md").write_text("# Beta\n\nSecond page about quinces and nothing more than that.\n\n## Fruit\nQuinces are yellow.\n")
    assert d.search("apples") == [] and shown_pages(d, "quinces")[0] == "b" and shown_pages(d, "pears")[0] == "a"


def test_the_tool_is_read_only_and_never_asks_for_a_card(docs):
    tool = docs.tool()
    assert tool.name == "kelo_help" and tool.read_only is True
    assert tool.requires_approval is False and tool.auto_approve is None and tool.hold is None
    assert not tool.untrusted                                   # the pages are the product's own text, not outside text
    assert "kelo_help" in CORE_TOOLS and hidden("kelo_help") and label("kelo_help") == "Checking Kelo's help pages"


def test_the_app_always_offers_the_tool_the_prompt_names():
    app = create_app(Settings(token="t", provider="p", model="m", cdp_host=None, content_dir=str(CONTENT)))
    assert "kelo_help" in {t.name for t in app.state.tools}


def test_the_prompt_rule_says_to_read_the_help_pages_first():
    text = PromptBuilder.load(CONTENT, None, Skills(CONTENT / "skills")).build(agent_name="Sage")
    rule = next(p for p in text.split("\n\n") if "`kelo_help`" in p)
    assert "call `kelo_help` first" in rule and "never from memory" in rule and "say so" in rule


def test_the_pages_are_english_and_name_no_other_product():
    for f in HELP.glob("*.md"):
        assert not re.search(r"[가-힣]", f.read_text(encoding="utf-8")), f.name
