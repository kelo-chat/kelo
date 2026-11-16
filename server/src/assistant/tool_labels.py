"""Short words the phone shows for a tool call instead of its raw name ("browser_open" -> "Opening a site").

`hidden` marks the calls the chat need not show at all (the assistant looking through its own notes and guides); the event still
carries them, with the raw name, so nothing is lost for a details view.
"""

from __future__ import annotations

from typing import Any

from .i18n import N_, t

LABELS: dict[str, tuple[str, bool]] = {
    "web_search": (N_("Searching the web"), False),
    "messenger": (N_("Telegram settings"), False),
    "package_entries": (N_("Reading a package's entries"), False),
    "package_entry_add": (N_("Saving an entry"), False),
    "package_entry_delete": (N_("Removing an entry"), False),
    "nest_draft_create": (N_("Creating a package draft"), False),
    "nest_draft_write": (N_("Updating a package draft"), False),
    "nest_draft_check": (N_("Checking a package draft"), False),
    "nest_draft_delete": (N_("Deleting a package draft"), False),
    "nest_draft_from_skill": (N_("Making a draft from a skill"), False),
    "nest_share_github": (N_("Sharing a package to GitHub"), False),
    "web_fetch": (N_("Reading a web page"), False),
    "browser_open": (N_("Opening a site"), False),
    "browser_read": (N_("Checking the page"), False),
    "browser_view": (N_("Looking at the site"), False),
    "browser_click": (N_("Clicking on the page"), False),
    "browser_click_at": (N_("Clicking on the page"), False),
    "browser_drag": (N_("Dragging on the page"), False),
    "browser_type": (N_("Typing"), False),
    "browser_login": (N_("Signing in"), False),
    "android_look": (N_("Looking at the app screen"), False),
    "android_tap": (N_("Tapping in the app"), False),
    "android_type": (N_("Typing in the app"), False),
    "android_swipe": (N_("Scrolling the app"), False),
    "android_key": (N_("Pressing an app button"), False),
    "android_open": (N_("Opening an app"), False),
    "android_apps": (N_("Checking the app list"), False),
    "android_login": (N_("Signing in"), False),
    "android_show": (N_("Showing the phone screen"), False),
    "android_send_screenshot": (N_("Sending a screenshot"), False),
    "confirm_purchase": (N_("Asking to confirm a payment"), False),
    "purchase_result": (N_("Checking the receipt"), False),
    "request_task_permission": (N_("Asking to allow repeated steps"), False),
    "wallet_cards": (N_("Checking payment cards"), False),
    "browser_fill_card": (N_("Entering card details"), False),
    "payment_step_wait": (N_("Waiting for your payment approval"), False),
    "browser_type_payment_pin": (N_("Entering the payment PIN"), False),
    "browser_pin_keypad_card": (N_("Waiting for you to enter your PIN"), False),
    "begin_order": (N_("Starting your order"), False),
    "shipping_profiles": (N_("Checking shipping addresses"), False),
    "browser_fill_shipping": (N_("Entering the shipping address"), False),
    "ask_shipping": (N_("Asking where to deliver"), False),
    "end_order": (N_("Closing the order"), False),
    "browser_downloads": (N_("Checking downloaded files"), False),
    "browser_save_download": (N_("Saving a downloaded file"), False),
    "browser_upload": (N_("Uploading a file"), False),
    "browser_save_pdf": (N_("Saving the page as PDF"), False),
    "browser_private": (N_("Switching to a private tab"), False),
    "browser_site_state": (N_("Moving the sign-in"), False),
    "browser_forget_site": (N_("Clearing site data"), False),
    "find_login_code": (N_("Finding a sign-in code in mail"), False),
    "browser_human_check": (N_("Trying the human check"), False),
    "browser_look": (N_("Looking at the page"), False),
    "view_image": (N_("Looking at a photo"), False),
    "vault_status": (N_("Checking the vault"), True),
    "request_login": (N_("Asking for a login"), False),
    "add_service_key": (N_("Asking for a service key"), False),
    "save_found_secret": (N_("Saving the key to the Keychain"), False),
    "request_card": (N_("Asking for a payment card"), False),
    "request_payment_pin": (N_("Asking for a payment PIN"), False),
    "connect_service": (N_("Connecting a service"), False),
    "connector_get": (N_("Getting from a service"), False),
    "connector_send": (N_("Sending to a service"), False),
    "connector_download": (N_("Saving a file from a service"), False),
    "mcp_list_tools": (N_("Checking what a service can do"), True),
    "mcp_call_tool": (N_("Using a service"), False),
    "email_list": (N_("Checking mail"), False),
    "email_read": (N_("Reading mail"), False),
    "email_send": (N_("Sending mail"), False),
    "email_update": (N_("Organizing mail"), False),
    "gcal_calendars": (N_("Checking calendars"), False),
    "gcal_events": (N_("Checking the calendar"), False),
    "gcal_create_event": (N_("Adding an event"), False),
    "gcal_update_event": (N_("Changing an event"), False),
    "gcal_delete_event": (N_("Deleting an event"), False),
    "phone_calendar_events": (N_("Checking the phone calendar"), False),
    "weather": (N_("Checking the weather"), False),
    "my_location": (N_("Checking your location"), False),
    "places_near": (N_("Looking for places nearby"), False),
    "briefing_sources": (N_("Choosing what the briefing reads"), False),
    "briefing": (N_("Checking the morning briefing"), False),
    "follow_list": (N_("Checking what is coming up"), False),
    "follow_stop": (N_("Stopping what is watched"), False),
    "big_event_notice": (N_("Setting when to mention big events"), False),
    "recheck_later": (N_("Noting to look at this again"), True),
    "recheck_watch": (N_("Starting to keep an eye on this"), False),
    "recheck_stop": (N_("Stopping a recheck"), False),
    "recheck_result": (N_("Noting what the recheck found"), True),
    "offer_post": (N_("Offering something I can do next"), True),
    "calendar_events": (N_("Checking the calendars"), False),
    "phone_calendar_add": (N_("Adding to the phone calendar"), False),
    "phone_calendar_update": (N_("Changing the phone calendar"), False),
    "phone_calendar_delete": (N_("Deleting from the phone calendar"), False),
    "phone_contacts": (N_("Checking contacts"), False),
    "phone_messages": (N_("Checking text messages"), False),
    "phone_send_text": (N_("Sending a text"), False),
    "phone_call": (N_("Opening the dialer"), False),
    "phone_call_log": (N_("Checking recent calls"), False),
    "phone_notifications": (N_("Checking phone notifications"), False),
    "phone_health": (N_("Checking health data"), False),
    "extract_document": (N_("Reading a document"), False),
    "generate_image": (N_("Making an image"), False),
    "send_screenshot": (N_("Sending a screenshot"), False),
    "show_picture": (N_("Showing a picture"), False),
    "list_product_images": (N_("Checking product photos"), False),
    "save_product_image": (N_("Saving a product photo"), False),
    "read_file": (N_("Checking files"), True),
    "list_files": (N_("Checking files"), True),
    "write_file": (N_("Saving a note"), False),
    "remember": (N_("Noting this down"), False),
    "react": (N_("Reacting to your message"), True),
    "read_skill": (N_("Checking a guide"), True),
    "search_skills": (N_("Looking for a guide"), True),
    "propose_skill": (N_("Suggesting a skill"), False),
    "find_tools": (N_("Working"), True),
    "schedule_create": (N_("Creating an automation"), False),
    "schedule_list": (N_("Checking automations"), False),
    "schedule_update": (N_("Changing an automation"), False),
    "schedule_delete": (N_("Deleting an automation"), False),
    "schedule_pause": (N_("Pausing an automation"), False),
    "schedule_retry": (N_("Planning to try again later"), False),
    "schedule_runs": (N_("Checking run history"), True),
    "task_allow": (N_("Asking once what this task may do on its own"), False),
    "watch_add": (N_("Creating a watch"), False),
    "watch_list": (N_("Checking watches"), True),
    "watch_update": (N_("Changing a watch"), False),
    "watch_remove": (N_("Deleting a watch"), False),
    "watch_dry_run": (N_("Testing a watch"), False),
    "watch_enable": (N_("Turning on a watch"), False),
    "ha_watch_add": (N_("Creating a Home Assistant watch"), False),
    "ha_watch_list": (N_("Checking Home Assistant watches"), True),
    "ha_watch_remove": (N_("Deleting a Home Assistant watch"), False),
    "journal_search": (N_("Searching the journal"), False),
    "delegate": (N_("Handing off a task"), False),
    "report": (N_("Reporting on a task"), False),
    "job_progress": (N_("Updating progress"), True),
    "workers_list": (N_("Checking handed-off tasks"), True),
    "worker_status": (N_("Checking handed-off tasks"), True),
    "worker_message": (N_("Messaging a handed-off task"), False),
    "worker_stop": (N_("Stopping a handed-off task"), False),
    "resume": (N_("Resuming a handed-off task"), False),
    "await_reply": (N_("Waiting for your answer"), True),
    "followup_snooze": (N_("Putting off a reminder"), False),
    "followup_cancel": (N_("Stopping a reminder"), False),
    "action_result": (N_("Noting whether a step went through"), False),
    "workroom_run": (N_("Running in the workroom"), False),
    "workroom_install": (N_("Installing a program in the workroom"), False),
    "workroom_uninstall": (N_("Removing a program from the workroom"), False),
    "workroom_files": (N_("Checking results"), True),
    "share_file": (N_("Making a public link"), False),
    "goal_get": (N_("Checking a goal"), True),
    "goal_set_facts": (N_("Saving what you told me"), False),
    "goal_plan": (N_("Planning the goal"), False),
    "goal_step_done": (N_("Updating the plan"), False),
    "goal_log": (N_("Noting progress"), True),
    "goal_metric_add": (N_("Recording a number"), False),
    "goal_link_schedule": (N_("Linking an automation to a goal"), False),
    "goal_add_document": (N_("Attaching a document to a goal"), False),
    "goal_suggest": (N_("Suggesting a goal"), False),
    "set_tone": (N_("Working"), True),   # how Kelo talks to the user: the reply itself shows the change
    "quick_replies": (N_("Adding answer buttons"), True),
    "compare_options": (N_("Putting the options side by side"), True),
    "storage_cleanup_list": (N_("Checking what can be cleaned up"), False),
    "storage_cleanup": (N_("Cleaning up storage"), False),
}
DEFAULT = (N_("Working"), False)

# Consecutive calls of one group fold into one chip in the chat ("✓ Read 6 messages"); the chip's words once more than one is done,
# with {n} for how many. Every browser step is one group: the chat's browser card shows them.
DONE: dict[str, str] = {
    "web_search": N_("Searched the web {n} times"),
    "web_fetch": N_("Read {n} web pages"),
    "browser": N_("Did {n} steps on web pages"),
    "email_list": N_("Checked mail {n} times"),
    "email_read": N_("Read {n} messages"),
    "email_send": N_("Sent {n} emails"),
    "email_update": N_("Organized mail {n} times"),
    "connector_get": N_("Got from a service {n} times"),
    "connector_send": N_("Sent to a service {n} times"),
    "mcp_call_tool": N_("Used a service {n} times"),
    "extract_document": N_("Read {n} documents"),
    "view_image": N_("Looked at {n} photos"),
    "gcal_events": N_("Checked the calendar {n} times"),
    "gcal_create_event": N_("Added {n} events"),
    "gcal_update_event": N_("Changed {n} events"),
    "gcal_delete_event": N_("Deleted {n} events"),
    "write_file": N_("Saved {n} notes"),
    "generate_image": N_("Made {n} images"),
}
DONE_ANY = N_("{label} · {n} times")

# The title of an approval card, as a whole question in the user's language ("Sign in?" -> ko "로그인할까요?"), for every tool that asks.
# Sent as "ask" with the approval (turn event and GET /api/approvals/pending); "" for a tool without one (the app then builds the
# question from the label, as older servers expect).
ASKS: dict[str, str] = {
    "add_service_key": N_("Send the saved key to new hosts?"),   # only when it would move a stored key (a new key needs no card)
    "android_tap": N_("Tap this in the app?"),
    "android_login": N_("Sign in?"),
    "android_type": N_("Type into this field in the app?"),
    "browser_click": N_("Click this on the page?"),
    "browser_click_at": N_("Click this point on the page?"),
    "browser_drag": N_("Drag on the page?"),
    "browser_login": N_("Sign in?"),
    "browser_type": N_("Type into this field?"),
    "browser_upload": N_("Upload these files?"),
    "confirm_purchase": N_("Pay for this?"),
    "purchase_result": N_("Cancel this order?"),
    "connector_send": N_("Send this to the service?"),
    "email_send": N_("Send this email?"),
    "email_update": N_("Move these messages to the trash?"),   # only moving to the trash asks
    "find_login_code": N_("Enter this sign-in code?"),
    "gcal_create_event": N_("Add this event?"),
    "gcal_delete_event": N_("Delete this event?"),
    "gcal_update_event": N_("Change this event?"),
    "generate_image": N_("Make this image?"),
    "nest_share_github": N_("Share this package to your GitHub?"),
    "mcp_call_tool": N_("Use the service for this?"),
    "phone_calendar_add": N_("Add this to the phone calendar?"),
    "phone_calendar_delete": N_("Delete this from the phone calendar?"),
    "phone_calendar_update": N_("Change this in the phone calendar?"),
    "phone_call": N_("Open the dialer with this number?"),
    "phone_send_text": N_("Send this text?"),
    "request_task_permission": N_("During this task, {action} on {site}, up to {count} times. Go ahead?"),
    "schedule_create": N_("Create this automation?"),
    "schedule_delete": N_("Delete this automation?"),
    "schedule_update": N_("Change this automation?"),
    "save_found_secret": N_("Send the found key to these hosts?"),   # only when a host is not on the page's own site
    "share_file": N_("Make a public link to this file?"),
    "task_allow": N_("When this happens, do this without asking?"),
    "storage_cleanup": N_("Delete these files to free up space?"),
    "watch_enable": N_("Turn on this watch?"),
    "workroom_install": N_("Install this program in the workroom?"),
}


def label(name: str) -> str:
    return t(LABELS.get(name, DEFAULT)[0])


def hidden(name: str) -> bool:
    return LABELS.get(name, DEFAULT)[1]


def group(name: str) -> str:
    """Calls of one group in a row fold into one chip: the tool itself, or "browser" for every browser step."""
    return "browser" if name.startswith("browser_") else name


def done(name: str) -> str:
    """The folded chip's words in the user's language, with {n} left for the count."""
    g = group(name)
    if g in DONE:
        return t(DONE[g])
    return t(DONE_ANY, label=label(name), n="{n}")


# request_task_permission for many items (several sites, or a list of what was found)
ASK_MANY = N_("{count} {what}: {action}. Go ahead?")
ASK_ALL = N_("All {count} {what}: {action}. Go ahead?")
ASK_MANY_PLAIN = N_("{action} for {count} items during this task. Go ahead?")
ASK_ALL_PLAIN = N_("{action} for all {count} found. Go ahead?")


def _ask_many(args: dict[str, Any]) -> str:
    from .tools.task_permission import many, plan_of
    p = plan_of(args)
    if not (many(p) or p["targets"]) or not p["action"] or p["count"] < 1:
        return ""
    if p["what"]:
        return t(ASK_ALL if p["all"] else ASK_MANY, what=p["what"], action=p["action"], count=p["count"])
    return t(ASK_ALL_PLAIN if p["all"] else ASK_MANY_PLAIN, action=p["action"], count=p["count"])


def _ask_fields(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """The words a question with {fields} is filled with (from the call's own arguments)."""
    if name == "request_task_permission":
        from .tools.task_permission import _site
        site, action = _site(str(args.get("site") or "")), " ".join(str(args.get("action") or "").split())[:80]
        count = str(args.get("max_count") or "").strip()[:6]
        return {"site": site, "action": action, "count": count} if site and action and count else {}
    return {}


def ask(name: str, args: dict[str, Any] | None = None) -> str:
    """The approval card's question for this tool in the user's language, or "" (see ASKS)."""
    if name not in ASKS:
        return ""
    if name == "request_task_permission" and (whole := _ask_many(args or {})):
        return whole
    fields = _ask_fields(name, args or {})
    if "{" in ASKS[name] and not fields:
        return ""
    return t(ASKS[name], **fields)
