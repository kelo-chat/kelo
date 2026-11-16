"""effects.py: what a tool call does (read / change / send / delete, on which connector, at what targets), for every family of connectors;
schedule_grants.action_of as a thin view of it; MCP tools by annotation and by the verbs in their names; and docs/connector-permissions.md
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
    ("mcp_call_tool", {"name": "notion-mcp", "tool": "search_pages"}, ("read", "notion", "search_pages")),
    ("mcp_call_tool", {"name": "notion-mcp", "tool": "create_page"}, ("change", "notion", "create_page")),
    ("mcp_call_tool", {"name": "notion-mcp", "tool": "delete_page"}, ("delete", "notion", "delete_page")),
    ("mcp_call_tool", {"name": "notion-mcp", "tool": "send_comment"}, ("send", "notion", "send_comment")),
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
@pytest.mark.parametrize("name,kind", [
    ("send_message", "send"), ("postComment", "send"), ("reply_to_thread", "send"), ("share_file", "send"), ("invite_user", "send"),
    ("publish_page", "send"), ("message_user", "send"),
    ("delete_issue", "delete"), ("remove-member", "delete"), ("trashFile", "delete"), ("destroy_session", "delete"), ("purge_cache", "delete"),
    ("get_page", "read"), ("list_issues", "read"), ("search_docs", "read"), ("read_file", "read"), ("find_user", "read"),
    ("query_database", "read"), ("fetch_url", "read"),
    ("create_page", "change"), ("update_task", "change"), ("set_status", "change"), ("", "change"),
])
def test_an_mcp_tool_without_annotations_is_classed_by_the_first_verb_in_its_name(name, kind):
    assert mcp_kind(name) == kind


def test_mcp_annotations_beat_the_name_and_a_server_that_gave_none_falls_back_to_the_name():
    assert mcp_kind("delete_everything", {"readOnlyHint": True}) == "read"
    assert mcp_kind("get_secret", {"destructiveHint": True}) == "delete"
    assert mcp_kind("get_secret", {"readOnlyHint": False, "destructiveHint": False}) == "read"     # nothing usable: the name
    assert effects.mcp_by_name_only("get_secret", {"readOnlyHint": False}) is True
    assert effects.mcp_by_name_only("get_secret", {"readOnlyHint": True}) is False
    effects.forget_mcp_tools()
    effects.remember_mcp_tools("Notion-MCP", [{"name": "archive_page", "annotations": {"destructiveHint": True}},
                                              {"name": "get_page", "annotations": {"readOnlyHint": True}}, {"name": "bad"}, "x"])
    assert fx("mcp_call_tool", {"name": "notion-mcp", "tool": "archive_page"}) == ("delete", "notion", "archive_page")   # by annotation: no verb says so
    assert fx("mcp_call_tool", {"name": "notion-mcp", "tool": "archive_other"}) == ("change", "notion", "archive_other")
    effects.forget_mcp_tools()


def test_payments_and_account_steps_keep_their_own_checks():
    for tool in ("confirm_purchase", "browser_fill_card", "request_payment_pin", "browser_login", "begin_order"):
        assert keeps_own_checks(tool, {})
    assert keeps_own_checks("mcp_call_tool", {"name": "stripe", "tool": "create_payment_link"})
    assert keeps_own_checks("mcp_call_tool", {"name": "x", "tool": "checkout_cart"})
    assert keeps_own_checks("mcp_call_tool", {"name": "x", "tool": "sign_up_user"})
    assert not keeps_own_checks("mcp_call_tool", {"name": "notion", "tool": "create_page"})
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
    assert action_of("mcp_call_tool", {"name": "notion", "tool": "cancel_booking"})[2] is True   # the standing-permission delete words
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
