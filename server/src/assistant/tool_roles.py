"""Who may pay, who may drive the phone: tool sets by role (docs/browser-workers.md, docs/phone-worker.md).

The agent that finds things (the chat, a schedule's run, a research / browser / general job) has no payment tool. A payment specialist
(a job of kind "checkout") has the payment tools and the browser's page tools, and nothing to search with. The split is by TOOL
NAME sets declared here, never by words on a page; every approval, card and journal row stays where it is (tools/purchase.py,
pay_terms.py): this module only decides which tools a run is shown.

- PAY_TOOLS: the tools that pay, ask for a card or a PIN, or settle an order's outcome.
- checkout_tools(): the specialist's set = PAY_TOOLS + the browser's page tools (every `browser_*`) + the order / shipping helpers
  + signing in + report. No web_search, web_fetch, delegate or file tools: it cannot go shopping, only complete the checkout it was
  briefed for.
- The chat does not click or type on websites (the browser split, `settings.browser_worker`): it keeps the tools that read or show
  a page and the downloads (BROWSER_CHAT_KEEPS); every other `browser_*` tool, and `find_login_code` (it types into a page), is for
  jobs only. Web work goes to a job of kind "browser" (`delegate`).
- The situation research (situation.py, source "situation"): RESEARCH_TOOLS, read-only tools only, picked by tool NAME and by the effect
  kind the connector tables declare (effects.TOOL_FAMILY: every call a read), plus its own tools (propose_help, record_owner_preference;
  the selection step: select_help, drop_help). Nothing that sends, posts, pays, deletes, acts on a page, drives the phone or makes a schedule.
- The phone job (kind "phone") alone drives the Android device: every tool named `android_*` (except the chat's own screenshot
  picture), the signing-in helpers and the job's report. Every other run is shown no phone tool.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

CHECKOUT_KIND = "checkout"
PHONE_KIND = "phone"

# Tools that move money or settle an order. Only the payment specialist is given them.
PAY_TOOLS: frozenset[str] = frozenset({
    "checkout_review", "checkout_submit", "confirm_purchase", "purchase_result",
    "browser_fill_card", "wallet_cards", "browser_type_payment_pin", "browser_pin_keypad_card", "payment_step_wait",
    "request_payment_pin", "request_card",
})
# What the specialist has besides PAY_TOOLS and `browser_*`: the order and shipping helpers, signing in, and the job's own report.
CHECKOUT_HELPERS: frozenset[str] = frozenset({
    "begin_order", "end_order", "shipping_profiles", "ask_shipping",
    "vault_status", "request_login", "find_login_code", "read_skill", "report", "job_progress", "action_result",
})
# What the specialist never has, even though it starts with `browser_` (steering other jobs is the main agent's alone: MAIN_ONLY).
CHECKOUT_DENIED: frozenset[str] = frozenset({"browser_forget_site", "browser_profiles", "browser_profile_delete"})


# The phone's tools are every `android_*` tool (by prefix, as the specialist takes every `browser_*`), except the ones that need the chat
# reply that is running: the picture of the screen in the reply, and handing the phone to the owner (android_show: its card and Done
# button belong to a reply; a job has no reply to put a card in, so a job reports needs_input and the agent that talks to the owner
# hands the phone over, then resumes the job).
PHONE_PREFIX = "android_"
PHONE_CHAT_TOOLS: frozenset[str] = frozenset({"android_send_screenshot", "android_show"})
# The tools the prefix takes today (the phone job has them loaded up front; a test checks the list against the prefix).
PHONE_TOOLS: frozenset[str] = frozenset({
    "android_apps", "android_key", "android_login", "android_look", "android_open", "android_swipe", "android_tap", "android_type",
})
# What the phone job has besides the phone tools: signing in, its skills, and the job's own report.
PHONE_HELPERS: frozenset[str] = frozenset({"vault_status", "request_login", "find_login_code", "read_skill", "report", "job_progress", "action_result"})


# The browser tools the chat keeps while the browser split is on: the ones that read or show a page, or move a file, and do not act on it.
#   browser_open: opens a public address and returns its text (the chat's reading of a page, as web_fetch does for a job-less lookup)
#   browser_read: the page text again (no click)
#   browser_downloads / browser_save_download / browser_save_pdf: list and keep what a tab downloaded or the page as a PDF
#   browser_site_state: lists and moves a saved sign-in state; browser_forget_site: clears a site's data, but only after the owner's card
# Everything else named `browser_*` (click, click_at, type, drag, upload, login, look, human_check, private, fill_shipping, ...) acts on a
# page: a job's. `find_login_code` types a one-time code into a page, so it is a job's too.
BROWSER_PREFIX = "browser_"
# Owner 2026-11-19 03:10: the chat has no web work at all (opening, reading, acting): every such step is a browser job with one window.
# What stays in the chat is what is not web work: the owner's sign-in and data controls (a card each).
BROWSER_CHAT_KEEPS: frozenset[str] = frozenset({"browser_site_state", "browser_forget_site", "browser_profiles", "browser_profile_delete"})
BROWSER_JOB_ONLY_EXTRA: frozenset[str] = frozenset({"find_login_code"})


def is_checkout(kind: str | None) -> bool:
    return kind == CHECKOUT_KIND


def is_phone(kind: str | None) -> bool:
    return kind == PHONE_KIND


def is_phone_tool(name: str) -> bool:
    return name.startswith(PHONE_PREFIX) and name not in PHONE_CHAT_TOOLS


def is_browser_acting_tool(name: str) -> bool:
    """A tool that acts on a web page (the chat is not given it while the browser split is on)."""
    return (name.startswith(BROWSER_PREFIX) and name not in BROWSER_CHAT_KEEPS) or name in BROWSER_JOB_ONLY_EXTRA


def finder_tools(tools: Iterable[Any], *, pay: bool = True, phone: bool = True, browser: bool = False) -> list[Any]:
    """The run that finds things: every tool except the paying ones (`pay`), the phone's (`phone`) and the ones that act on a web page
    (`browser`: only the chat passes it)."""
    return [t for t in tools if not (pay and t.name in PAY_TOOLS) and not (phone and is_phone_tool(t.name))
            and not (browser and is_browser_acting_tool(t.name))]


def phone_tools(tools: Iterable[Any]) -> list[Any]:
    """The phone job's run: the phone's tools and the helpers, nothing to search or browse with."""
    return [t for t in tools if is_phone_tool(t.name) or t.name in PHONE_HELPERS]


def checkout_tools(tools: Iterable[Any]) -> list[Any]:
    """The payment specialist's run: the paying tools, the browser's page tools and the helpers, nothing to search with."""
    return [t for t in tools if t.name not in CHECKOUT_DENIED and (t.name in PAY_TOOLS or t.name in CHECKOUT_HELPERS or t.name.startswith("browser_"))]


def for_kind(tools: Iterable[Any], kind: str | None, *, pay_split: bool = True, phone_split: bool = True,
             browser_split: bool = True) -> list[Any]:
    """The tools a run of this kind is shown (None = the chat and every run on the app's own list: schedule and follow-through runs
    count as the chat). A phone job always has its own role; the splits decide what the other runs lose; the browser split takes the
    acting browser tools from the chat only (a job of kind browser, general or research keeps them)."""
    if is_phone(kind):
        return phone_tools(tools)
    if pay_split and is_checkout(kind):
        return checkout_tools(tools)
    return finder_tools(tools, pay=pay_split, phone=phone_split, browser=browser_split and kind is None)


# ---- the situation research (situation.py): an unattended run that may only read and propose ---------------------------------
# Tools that read and nothing else, by name: the agent's own files and earlier messages, memory, the web, the weather, the lists of
# schedules, watchers and jobs, the calendar of the briefing, a goal.
RESEARCH_READ_NAMES: frozenset[str] = frozenset({
    "read_file", "list_files", "read_skill", "search_skills", "read_earlier_messages", "memory_explain", "web_search", "web_fetch", "weather",
    "schedule_list", "schedule_runs", "watch_list", "ha_watch_list", "event_reminders_list", "workers_list", "worker_status", "goal_get", "work_list", "calendar_events",
})
# Connector tools whose every call is a read, by the effect kinds in effects.TOOL_FAMILY (never by words in a name). The phone's own data
# is left out (no phone tool in this run), and a download saves a file.
RESEARCH_NOT: frozenset[str] = frozenset({"connector_download"})
RESEARCH_OWN: tuple[str, ...] = ("propose_help", "record_owner_preference")   # the research step's own tools (situation.py)
SELECTION_OWN: tuple[str, ...] = ("select_help", "drop_help")                 # the selection step's own tools
SELECTION_READ: frozenset[str] = frozenset({"read_file", "read_earlier_messages", "memory_explain"})   # what the selection step may read with
WRITER_READ: frozenset[str] = frozenset({"read_file", "read_earlier_messages", "memory_explain", "web_search", "web_fetch", "weather",
                                         "email_list", "email_read", "gcal_events", "calendar_events"})   # the main agent's check before it writes


def _effect_reads() -> frozenset[str]:
    from .effects import TOOL_FAMILY
    return frozenset(n for n, (fam, kinds) in TOOL_FAMILY.items() if fam != "phone" and kinds == ("read",)) - RESEARCH_NOT


RESEARCH_TOOLS: frozenset[str] = RESEARCH_READ_NAMES | _effect_reads()


def _acting(name: str) -> bool:
    return name in PAY_TOOLS or is_phone_tool(name) or name.startswith(("phone_", PHONE_PREFIX)) or is_browser_acting_tool(name)


def research_tools(tools: Iterable[Any], names: Iterable[str] | None = None) -> list[Any]:
    """The research run's tools out of the app's list: the read-only names (or `names` within them), never an acting one."""
    allowed = RESEARCH_TOOLS if names is None else frozenset(names) & RESEARCH_TOOLS
    return [t for t in tools if t.name in allowed and not _acting(t.name)]
