"""effects.py: what a tool call does (read / change / send / delete, on which connector, at what targets), for every family of connectors;
schedule_grants.action_of as a thin view of it; MCP tools by annotation, manifest and the tool judge (never by the words in their names); and docs/connector-permissions.md
staying in step with the code (tools/connector_effects.py)."""

import importlib.util
from pathlib import Path

import pytest

from assistant import effects
from assistant.effects import Effect, effect_of, keeps_own_checks, mcp_kind
from assistant.schedule_grants import action_of

ROOT = Path(effects.__file__).resolve().parents[3]


def fx(tool, args=None, **kw):
    e = effect_of(tool, args or {}, **kw)
    return None if e is None else (e.kind, e.connector, e.action)


@pytest.fixture(autouse=True)
def mcp_world():
    """What the notion-mcp server listed (its own hints) and what the tool judge said; nothing from the words of a tool's name."""
    effects.forget_mcp_tools()
    effects.remember_mcp_tools("notion-mcp", [{"name": "search_pages", "annotations": {"readOnlyHint": True}},
                                              {"name": "delete_page", "annotations": {"destructiveHint": True}}])
    effects.remember_judged("notion-mcp", "create_page", "change", False)
    effects.remember_judged("notion-mcp", "send_comment", "send", False)
    yield
    effects.forget_mcp_tools()


class FakeAsk:
    """The model behind the tool judge: answers by the tool named in the request (a table), counts its requests."""

    def __init__(self, table):
        self.table, self.calls = table, []

    async def __call__(self, system, user):
        import json
        ctx = json.loads(user)
        self.calls.append(ctx)
        got = self.table.get(ctx.get("tool") or ctx.get("path"))
        return None if got is None else {"kind": got[0], "own_check": got[1]}


# tool, args, (kind, connector, action)
TABLE = [
    # mail: the connector is the mailbox's catalog id, from `account`
    ("email_list", {"account": "gmail"}, ("read", "gmail", "email_list")),
    ("email_read", {"account": "mail-naver"}, ("read", "imap_email", "email_read")),
    ("email_send", {"account": "mail-gmail", "to": ["a@b.org"], "body": "hi"}, ("send", "gmail", "email_send")),
    ("email_send", {"account": "outlook", "to": ["a@b.org"]}, ("send", "outlook_mail", "email_send")),
    ("email_update", {"account": "gmail", "action": "read"}, ("change", "gmail", "email_update")),
    ("email_update", {"account": "gmail", "action": "trash"}, ("delete", "gmail", "email_trash")),
    ("email_read", {"account": "nest-fastmail"}, ("read", "fastmail", "email_read")),
    # Google Calendar, the phone's calendar and the rest of the phone
    ("gcal_events", {}, ("read", "google_calendar", "gcal_events")),
    ("gcal_create_event", {}, ("change", "google_calendar", "gcal_create_event")),
    ("gcal_update_event", {}, ("change", "google_calendar", "gcal_update_event")),
    ("gcal_delete_event", {}, ("delete", "google_calendar", "gcal_delete_event")),
    ("phone_calendar_events", {}, ("read", "phone", "phone_calendar_events")),
    ("phone_calendar_add", {}, ("change", "phone", "phone_calendar_add")),
    ("phone_calendar_delete", {}, ("delete", "phone", "phone_calendar_delete")),
    ("phone_contacts", {}, ("read", "phone", "phone_contacts")),
    ("phone_messages", {}, ("read", "phone", "phone_messages")),
    ("phone_send_text", {"to": "010-1234-5678", "text": "hi"}, ("send", "phone", "phone_send_text")),
    ("phone_call", {"to": "010-1234-5678"}, ("send", "phone", "phone_call")),
    # a service connected with a key: the HTTP method decides; a messenger posts to people
    ("connector_get", {"name": "asana-api", "url": "/x"}, ("read", "asana", "connector_get")),
    ("connector_download", {"name": "asana-api", "url": "/x"}, ("read", "asana", "connector_download")),
    ("connector_send", {"name": "asana-api", "method": "POST", "url": "/x"}, ("change", "asana", "connector_send")),
    ("connector_send", {"name": "asana-api", "method": "PATCH", "url": "/x"}, ("change", "asana", "connector_send")),
    ("connector_send", {"name": "asana-api", "method": "DELETE", "url": "/x"}, ("delete", "asana", "connector_delete")),
    ("connector_send", {"name": "slack-api", "method": "POST", "url": "/chat.postMessage"}, ("send", "slack", "connector_send")),
    ("connector_send", {"name": "slack-api", "method": "DELETE", "url": "/x"}, ("delete", "slack", "connector_delete")),
    # an app on the owner's own network
    ("connector_get", {"name": "home-assistant-api", "url": "/api/states"}, ("read", "home_assistant", "connector_get")),
    ("connector_send", {"name": "home-assistant-api", "method": "POST", "url": "/api/services/light/turn_on"}, ("change", "home_assistant", "connector_send")),
    # MCP: the server's own tool name is the action
    ("mcp_list_tools", {"name": "notion-mcp"}, ("read", "notion", "mcp_list_tools")),
    ("mcp_call_tool", {"name": "notion-mcp", "tool": "search_pages"}, ("read", "notion", "search_pages")),      # the server's readOnlyHint
    ("mcp_call_tool", {"name": "notion-mcp", "tool": "create_page"}, ("change", "notion", "create_page")),     # the judge said change
    ("mcp_call_tool", {"name": "notion-mcp", "tool": "delete_page"}, ("delete", "notion", "delete_page")),     # the server's destructiveHint
    ("mcp_call_tool", {"name": "notion-mcp", "tool": "send_comment"}, ("send", "notion", "send_comment")),     # the judge said send
    # Nest packages that keep entries
    ("package_entries", {"package": "tracker", "collection": "c"}, ("read", "tracker", "package_entries")),
    ("package_entry_add", {"package": "tracker", "collection": "c"}, ("change", "tracker", "package_entry_add")),
    ("package_entry_delete", {"package": "tracker", "collection": "c"}, ("delete", "tracker", "package_entry_delete")),
]


@pytest.mark.parametrize("tool,args,want", TABLE)
def test_the_classification_table_per_connector_family(tool, args, want):
    assert fx(tool, args) == want


@pytest.mark.parametrize("tool", ["read_file", "write_file", "web_fetch", "workroom_run", "confirm_purchase", "schedule_create", "remember", "weather"])
def test_tools_that_reach_no_connector_have_no_effect(tool):
    assert effect_of(tool, {"path": "x"}) is None


def test_an_empty_account_is_the_only_connected_mailbox_when_the_accounts_are_given():
    boxes = lambda: [{"name": "mail-gmail", "provider": "gmail", "address": "me@gmail.com"}]   # noqa: E731
    assert fx("email_send", {"to": ["a@b.org"]}, accounts=boxes) == ("send", "gmail", "email_send")
    assert fx("email_send", {"to": ["a@b.org"]}) == ("send", None, "email_send")
    assert fx("email_read", {"account": "me@gmail.com"}, accounts=boxes) == ("read", "gmail", "email_read")
    two = lambda: [*boxes(), {"name": "outlook", "provider": "outlook", "address": "o@x.org"}]   # noqa: E731
    assert fx("email_read", {}, accounts=two) == ("read", None, "email_read")


def test_the_browser_steps_are_sends_or_deletes_on_a_site_and_belong_to_no_connector():
    e = effect_of("browser_type", {"text": "Kim"}, site="shop.example.com", commit="personal")
    assert (e.kind, e.connector, e.text, e.action) == ("send", None, "Kim", "browser_type") and e.targets[0].startswith("site:")
    assert effect_of("browser_click", {}, site="shop.example.com", commit="delete").kind == "delete"
    assert effect_of("browser_click", {}, site="shop.example.com", commit="pay") is None
    assert effect_of("browser_click", {}, site="", commit="send") is None


def test_a_vault_name_the_catalog_does_not_know_stays_as_it_is_and_installed_packages_can_alias_it():
    assert fx("connector_get", {"name": "my-thing", "url": "/"}) == ("read", "my-thing", "connector_get")
    assert fx("connector_get", {"name": "my-thing-key", "url": "/"}, aliases={"my-thing-key": "my_thing"}.get) == ("read", "my_thing", "connector_get")


# ---------------------------------------------------------------------------------------------------------- MCP tools
def test_an_mcp_tool_no_hint_or_judge_covers_is_a_change_that_asks_whatever_its_name_says():
    for name in ("send_message", "delete_issue", "get_page", "list_issues", "fetch_url", "purge_cache", ""):
        assert mcp_kind(name) == "change", name                      # the words of a name decide nothing
        assert keeps_own_checks("mcp_call_tool", {"name": "x", "tool": name}) is True   # unknown: its own card, whatever "allow" says


def test_the_servers_hints_and_the_judge_decide_and_a_hint_that_is_not_read_only_is_never_a_read():
    assert mcp_kind("anything", {"destructiveHint": True}) == "delete"
    assert mcp_kind("anything", {"readOnlyHint": True}) == "read"
    assert mcp_kind("anything", {"readOnlyHint": False}) == "change"
    assert mcp_kind("anything", {"destructiveHint": False}) == "change"        # no usable hint: unknown, a change
    effects.remember_judged("c", "x", "read", False)
    assert mcp_kind("x", {}, "c") == "read"
    assert mcp_kind("x", {"readOnlyHint": False}, "c") == "change"             # the server says it may change things: not a read
    effects.remember_judged("c", "y", "send", False)
    assert mcp_kind("y", {"readOnlyHint": False}, "c") == "send"               # never less strict than the judge
    effects.forget_mcp_tools()
    effects.remember_mcp_tools("Notion-MCP", [{"name": "archive_page", "annotations": {"destructiveHint": True}},
                                              {"name": "get_page", "annotations": {"readOnlyHint": True}}, {"name": "bad"}, "x"])
    assert fx("mcp_call_tool", {"name": "notion-mcp", "tool": "archive_page"}) == ("delete", "notion", "archive_page")
    assert fx("mcp_call_tool", {"name": "notion-mcp", "tool": "archive_other"}) == ("change", "notion", "archive_other")


async def test_the_judge_is_asked_once_per_tool_and_only_when_nothing_else_says(monkeypatch):
    from assistant.tool_judge import ToolJudge
    ask = FakeAsk({"book_table": ("change", True), "list_things": ("read", False), "weird": None})
    monkeypatch.setattr(effects, "JUDGE", ToolJudge(ask))
    effects.remember_mcp_tools("shop-mcp", [{"name": "book_table", "description": "Reserve and pay for a table"},
                                            {"name": "list_things", "description": "Lists things"},
                                            {"name": "hinted", "annotations": {"readOnlyHint": True}}])
    for tool in ("book_table", "list_things", "hinted", "book_table", "weird"):
        await effects.ensure_judged("mcp_call_tool", {"name": "shop-mcp", "tool": tool})
    assert [c["tool"] for c in ask.calls] == ["book_table", "list_things", "weird"]   # hinted needs none; the repeat is cached
    assert ask.calls[0]["description"] == "Reserve and pay for a table"
    assert fx("mcp_call_tool", {"name": "shop-mcp", "tool": "list_things"}) == ("read", "shop-mcp", "list_things")
    assert keeps_own_checks("mcp_call_tool", {"name": "shop-mcp", "tool": "book_table"}) is True
    assert keeps_own_checks("mcp_call_tool", {"name": "shop-mcp", "tool": "list_things"}) is False
    assert keeps_own_checks("mcp_call_tool", {"name": "shop-mcp", "tool": "hinted"}) is False
    assert keeps_own_checks("mcp_call_tool", {"name": "shop-mcp", "tool": "weird"}) is True      # the judge could not tell: asks
    assert mcp_kind("weird", {}, "shop-mcp") == "change"
    # a changed description is judged again
    effects.remember_mcp_tools("shop-mcp", [{"name": "list_things", "description": "Lists things and also orders them"}])
    assert effects.judged("shop-mcp", "list_things") is None


async def test_no_judge_means_no_request_and_an_unknown_tool_asks(monkeypatch):
    monkeypatch.setattr(effects, "JUDGE", None)
    await effects.ensure_judged("mcp_call_tool", {"name": "shop-mcp", "tool": "list_things"})
    assert effects.judged("shop-mcp", "list_things") is None
    assert keeps_own_checks("mcp_call_tool", {"name": "shop-mcp", "tool": "list_things"}) is True


async def test_a_rest_call_of_a_connected_service_is_judged_by_method_and_path(monkeypatch):
    from assistant.tool_judge import ToolJudge
    ask = FakeAsk({"/v1/charges": ("change", True), "/v1/notes": ("change", False)})
    monkeypatch.setattr(effects, "JUDGE", ToolJudge(ask))
    pay = {"name": "my-api", "method": "POST", "url": "https://api.example.com/v1/charges", "json": {"amount": 5}}
    note = {"name": "my-api", "method": "POST", "url": "https://api.example.com/v1/notes", "json": {"text": "x"}}
    assert keeps_own_checks("connector_send", pay) is True and keeps_own_checks("connector_send", note) is True   # not judged yet: asks
    for a in (pay, note, note):
        await effects.ensure_judged("connector_send", a)
    assert len(ask.calls) == 2
    assert keeps_own_checks("connector_send", pay) is True and keeps_own_checks("connector_send", note) is False


def test_payments_and_account_steps_keep_their_own_checks():
    for tool in ("confirm_purchase", "browser_fill_card", "request_payment_pin", "browser_login", "begin_order"):
        assert keeps_own_checks(tool, {})
    effects.remember_judged("stripe", "create_payment_link", "change", True)    # the judge's reading of the tool's description
    effects.remember_judged("x", "sign_up_user", "change", True)
    assert keeps_own_checks("mcp_call_tool", {"name": "stripe", "tool": "create_payment_link"})
    assert keeps_own_checks("mcp_call_tool", {"name": "x", "tool": "sign_up_user"})
    assert not keeps_own_checks("mcp_call_tool", {"name": "notion-mcp", "tool": "create_page"})
    assert not keeps_own_checks("email_send", {})
    assert keeps_own_checks("browser_click", {}, "security") and not keeps_own_checks("browser_click", {}, "send")


# ---------------------------------------------------------------------------------------------------------- the standing permissions' view
def test_action_of_is_a_thin_view_with_the_same_answers_as_before():
    assert action_of("email_send", {"to": ["Kim@X.org"], "body": "hi"}) == ("send", ["kim@x.org"], False, "hi")
    assert action_of("email_update", {"action": "read"}) is None
    assert action_of("email_update", {"action": "trash", "account": "mail-naver"}) == ("change", ["connector:naver"], True, "")
    assert action_of("phone_send_text", {"to": "010-1234-5678", "text": "t"}) == ("send", ["1012345678"], False, "t")
    assert action_of("gcal_delete_event", {}) == ("change", ["calendar"], True, "")
    assert action_of("phone_calendar_add", {}) == ("change", ["calendar"], False, "")
    assert action_of("connector_send", {"name": "Notion", "method": "DELETE"}) == ("change", ["connector:notion"], True, "")
    assert action_of("mcp_call_tool", {"name": "Notion", "tool": "get_page"}) == ("change", ["connector:notion/get_page"], False, "")
    assert action_of("mcp_call_tool", {"name": "notion-mcp", "tool": "delete_page"})[2] is True   # a delete by the effect's kind (hint), not a word
    assert action_of("mcp_call_tool", {"name": "notion", "tool": "cancel_booking"})[2] is False  # a name says nothing
    got = action_of("browser_type", {"text": "Kim"}, "shop.example.com", "personal")
    assert got[0] == "send" and got[1][0].startswith("site:") and got[2:] == (False, "Kim")
    for tool in ("email_read", "gcal_events", "phone_contacts", "phone_call", "connector_get", "package_entry_add", "workroom_run"):
        assert action_of(tool, {"name": "x", "package": "p"}) is None


# ---------------------------------------------------------------------------------------------------------- the doc
def test_the_connector_permissions_doc_is_in_step_with_the_code():
    spec = importlib.util.spec_from_file_location("connector_effects", ROOT / "tools" / "connector_effects.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (ROOT / "docs" / "connector-permissions.md").read_text(encoding="utf-8") == mod.render(), \
        "run: uv run python tools/connector_effects.py --write"
    counts = mod.counts()
    assert counts["connectors"] > 100 and counts["actions"] > counts["connectors"]
