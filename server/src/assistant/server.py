"""HTTP API (single user). Run: uvicorn assistant.server:app

The routes live in api/ (one router per domain, map in api/__init__.py); create_app() below builds the services and the turn engine
and includes them. The list here stays the overview of the API.

POST /api/chat                 stream (SSE) one stateless agent turn
POST /api/talk                 stream (SSE) a turn of the stored conversation: {"text": "...", "answers"?: [{"question", "value"}]}
                               (answers: the values of the quick-reply fields the text answers, checked: see goals.py "Quick replies")
                               "attachments"?: [{"path": "uploads/<name>"}], "auto_text"?: bool (files sent with the message and
                               whether the text is the app's own sentence: chat_attachments.py)
POST /api/approvals/{id}       answer an approval_request seen on a stream
(no routes) an Android device Kelo drives over adb: the tools android_look/tap/type/swipe/key/open/apps, on with ANDROID_DEVICE,
                               ANDROID_BRIDGE_URL and ANDROID_BRIDGE_TOKEN (the bridge container; android/, docs/android-device.md)
GET/PUT /api/language          the owner's language for background jobs (i18n.py): {"language", "source": picked|app|guess, "available"};
                               PUT {"language"} from the app's language pick, the only thing that changes it
GET/PUT /api/profile           user_name, agent_name, provider, model, fallback_provider, fallback_model, background_provider,
                               background_model (background jobs only, empty = the main model; usage.py), tone ("friendly" | "polite",
                               default polite: how Kelo talks to the user, prompt.tone_line; the agent's set_tone changes it too); min_app_version (the oldest
                               app build per platform: ASSISTANT_MIN_APP_ANDROID / _IOS / _DESKTOP, see MIN APP VERSION; also in /api/about)
GET/DELETE /api/messages       stored conversation (DELETE clears the chat history and what was made from it: see chat_clear.py)
                               each row: id, role, content, created_at, reaction (an emoji on a user message, "" none; reactions.py)
GET/DELETE /api/conversation/summary   the rolling summary of the older conversation and the context numbers (see CONVERSATION
                               COMPACTION next to serialize())
GET/PUT/DELETE /api/files[/path]  the agent's own files (MEMORY.md, ...), edited in Settings; trash, reset, folders: api/files.py docstring
GET/POST /api/goals, PATCH/DELETE /api/goals/{id}, GET /api/goals/{id}, /api/goals/suggestions...   (see GOALS API in goals.py)
GET/PUT/DELETE /api/connectors/{id}/permissions   what Kelo may do with one connector: allow / ask / off per kind and per action (permissions.py has the contract)
GET  /api/connectors/{id}/data      what came from this connector and is still kept: {"files", "notes", "items"} (connector_data.py; only recorded origins, zeros when unknown)
POST /api/connectors/{id}/forget    delete that data (files go to the library's trash): the same counts, deleted. Nothing is deleted without this call
DELETE /api/credentials/{name}      disconnect a connector (a service, mail, MCP server): the response also carries "data": the summary above, taken after
                                    the disconnect, so the app can ask whether to forget it too (the data is kept until POST .../forget)
GET  /api/connectors, /api/providers, /api/models?provider=, /api/usage (+ "jobs", "background", "subscriptions", "accounts" with role)
GET  /api/usage/summary?days=30   token use by day, by kind of job and by model, with the cached share (see usage.py)
PUT/DELETE /api/providers/{name}/key   API key entered in the app (write-only)
DELETE /api/providers/{name}/login     sign out of a sign-in provider (409 while it runs the main model; a fallback on it is emptied)
POST /api/providers/{name}/login/start|finish|cancel, GET .../login/status   sign in from the phone (device code or pasted code)
POST /api/providers/{name}/cli/update {version?}, GET .../cli/update   any signed-in device: the claude / agy sidecar installs a newer CLI into its
    home volume; {"state": idle|running|done|failed, "step", "from", "to", "ok", "error", "unchanged"?, "active": {source, version},
    "product"}; the CLI status in /api/providers says "updatable" (a sign-in + a sidecar that can do it)
GET /api/devices, POST /api/devices/pair ({"origin"}; "owner" / "first_owner" are accepted and ignored), POST /api/pair (one-time code -> device token), DELETE /api/devices/{id},
PUT /api/devices/{id}/owner (master token only; kept for old tools, changes nothing: there are no owner devices, every device has full rights)
GET/POST /api/devices/codes, DELETE /api/devices/codes/{id} (master token only): multi-use access codes for app store reviewers
    (POST {"label", "days" (default 30), "uses" (default 20)} -> {"id", "code", "label", "expires_at", "max_uses"}; the code
    is shown this once. DELETE revokes it and signs out every device that joined with it -> {"ok", "signed_out"}. review.py)
GET/POST /api/credentials, PUT /api/credentials/{name}/secret, DELETE   connections to user-registered services (secret write-only)
GET /api/vpn, POST /api/vpn/apply         the VPN / Proxy connectors' route now, and "switch now" for a change waiting for the browser (vpn.py)
GET /api/secret-requests, POST /api/secret-requests/{id}/cancel|card   fill-in cards the agent asked for (see secret_request in turn events)
/api/wallet/...                cards, auto-pay limits, pause, payment history, notifications, one-time card delivery (see WALLET API below)
/api/wallet/shipping, /api/address/..., /api/shipping-forms/...   shipping addresses of any country and an order's address form (SHIPPING API)
/api/tasks, /api/agent/status, /api/approvals/..., /api/schedules, /api/browser/sessions, /api/journal, /api/reflection, /api/agent/avatar,
/api/notifications              the agent screen (see AGENT SCREEN API next to serialize())
GET /api/workers[/{id}], POST /api/workers/{id}/resume|stop|message   background jobs the agent handed off (see WORKERS API)
/api/browser/sessions/{id}/live (WebSocket), .../frame, .../control, .../input   live view of a tab and taking control (see LIVE VIEW API)
GET/PUT /api/operator/cloudflare, POST /api/operator/cloudflare/reclaim   the operator's address service (any signed-in device)
POST /broker/tunnel/claim|release, GET /broker/tunnel/check   <name>.kelo.chat addresses for other copies (see kelo_names.py)
POST/DELETE /api/push/register, GET /api/push/status   this phone's FCM token, for push notifications (see PUSH API next to serialize())
POST /api/live-activity, DELETE /api/live-activity/{turn_id}   an iPhone Live Activity of a running reply: its push token and the phone's key (see live_activity.py)
GET/PUT/DELETE /api/operator/firebase   the operator's Firebase service-account key (any signed-in device; never returned)
GET /api/notices?lang=ko       unread operator notices (kelo.chat feed, notices.py), newest first, filtered by min_app (X-Kelo-App)
POST /api/notices/{id}/read    mark a notice as read (any signed-in device; 204, idempotent)
GET /api/operator/status       {"owner": true, "operator": bool}: whether this is the operator's server (any signed-in device; "owner" is kept for older apps: true for every device and the access key; the apps
                               show their operator section only when "operator" is true)
GET /download/{android|windows|windows-msi|mac}[/{file}], /download/latest.json, /download/notices.json   the app builds and operator notices feed (no auth; see downloads.py, notices.py)
POST /broker/push/send, GET /broker/push/status   pushes for other copies' phones (see push.py)
POST /api/reports              report a bad AI reply to the Kelo developer (forwarded to the broker; see reports.py)
GET /api/operator/reports, POST|DELETE /api/operator/reports/{id}   the reports other copies sent (any signed-in device)
POST /broker/reports           reports from other copies (no user token; rate-limited; see reports.py)
GET/POST /api/google/beta      this copy's request to try Google sign-in while it is closed (forwarded to the broker; see beta.py)
GET /api/operator/beta, POST|DELETE /api/operator/beta/{id}   beta requests other copies sent; approve or decline (any signed-in device); the
                                              Play tester sign-ups (kelo.chat/tester) are in the same list, kind play_tester, negative id
POST /broker/beta, POST /broker/beta/status   beta requests from other copies and their answer (install secret; rate-limited; beta.py)
GET  /healthz                  liveness (no auth)
/api/keyguard/...              Keyguard: the key of passwords, keys and cards held by the owner's phones; 423 {"code": "locked"}
                               while locked (api/keyguard.py, docs/keyguard.md)
POST /e2e, GET /e2e/ws (WebSocket)   KE2E: any API call (or the live view's WebSocket) sealed end to end (api/e2e_gate.py,
                               docs/security/e2e-and-phone-key.md); GET /api/e2e/info {"v", "kid", "pk", "fingerprint"} (no auth);
                               POST /api/e2e/rotate (master token: a new key, every phone pairs again). A device that used KE2E
                               once is refused on plaintext /api/* (403 code e2e_required). Errors with a code carry it at the
                               top level too: {"detail": {...}, "code": "..."}
GET  /api/about                {"version", "build", "min_app", "min_server", "min_app_version", "db": {"ok", "schema", "tables",
                               "migrations"}} (used by `assistant update`; version.json, docs/versioning.md). Every answer has
                               X-Kelo-Server: <version>+<build>; an app older than min_app (X-Kelo-App) gets 426 app_too_old
GET /api/server-update, POST /api/server-update/check|update|rollback, PUT /api/server-update/settings   any signed-in device (not on a review copy): update this
                               server from the app through the updater container (see server_updates.py)
GET  /oauth/client-metadata.json   Kelo's OAuth Client ID Metadata Document (no auth; only on the server holding the shared relay page)
GET  /                         a short page that opens the Kelo phone app (no web app); the API needs the token

Everything except /healthz, /oauth/... and /broker/... needs `Authorization: Bearer $ASSISTANT_TOKEN`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import date, datetime
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import (
    AgentDone, AgentThinking, ApprovalRequest, AssistantText, REPEAT_NOTE, StepLimit, Tool, ToolResult, ToolStarted, approval_timeout,
    current_approval, current_call, current_box, emit_ui, handover, run_agent, ui_sink,
)
from .agent.loop import outside_seen
from .agent.fence import fence
from .big_events import AHEAD_DAYS, WEEK_DAYS
from .agent.toolbox import CORE_TOOLS, SCHEDULE_RUN_TOOLS, ToolBox, ToolScopes, find_tools_tool
from .agent.toolbox import tool_catalog as _tool_catalog
from .activity import Activity, host_of, rule_kind, schedule_rule_ok, title_from
from .avatar import Avatar
from .journal import Journal, journal_tools
from . import i18n
from .mailboxes import Mailboxes
from .microsoft_calendar import MicrosoftCalendar
from .microsoft_mail import GraphMail
from .nest_sources import NestCalendars, NestMail
from .i18n import N_
from .memory_inbox import MemoryInbox, inbox_tools
from .reactions import Reactions, emoji as reaction_emoji
from .memory_upkeep import MemoryUpkeep
from .recall import Recall, TurnRecall
from .nightly_review import NightlyReview
from .reflection import Reflection
from . import review
from .quiet_hours import NightlyRunner, QuietHours
from .day_types import country_for, day_type
from .server_updates import ServerUpdates, UpdaterClient
from .server_updates import routes as server_update_routes
from .server_updates import run_tick as run_server_update_tick
from .notices import NoticesFeed, REFRESH_SECONDS as NOTICES_REFRESH_SECONDS, netguard_notices_fetcher
from .compaction import (
    MEMORY_FLUSH_PROMPT, TIME_TAG, MIN_KEEP, NEXT_TURN_MARGIN, NO_REPLY, Compactor, ContextOverflow, est_tokens, message_tokens,
    request_tokens, summary_message,
)
from . import windows
from . import version as release_version
from .schedules import CATCH_UP_SECONDS, SCHEDULE_MARK, ScheduleError, Scheduler, Schedules, schedule_check_text, schedule_tools
from .schedules import zone as sched_zone
from .checklist import NOTE as CHECKLIST_NOTE, Checklist, checklist_routes
from .buy_ready import FAST_STEPS, FAST_TOOLS, LOGIN_PROMPT, LOGIN_STEPS, LOGIN_TOOLS, BuyReady
from .buy_replay import Plans, Recorder, ReplayStream
from .watchers import WATCH_MARK, Page as WatchPage, WatchError, Watchers, handoff_text as watch_handoff_text, watch_tools, watcher_routes
from .ha_events import HaEvents, handoff_text as ha_handoff_text
from .task_names import TaskNamer
from .tools.ha_tools import ha_watch_tools
from .proactive import Proactive, proactive_routes
from .offers import build_offers, offer_routes
from .recheck import RECHECK_MARK, RESEARCH_STEPS, RESEARCH_TOOLS, build_rechecks, recheck_routes
from .briefing import Briefing, briefing_routes
from .briefing_sources import Sources, sources_routes
from .follow import AeroApiFlights, FollowRunner, FollowStore, IncheonFlights, ItemFinder, PageSource, Planner, follow_routes, follow_tools
from .follow.upcoming import UpcomingExtractor
from .calendars import Calendars
from .weather import Weather
from .wallet import CardDeliveries, Wallet, site_keys as wallet_site_keys
from .payment_pins import PaymentLedger, PaymentPaths, PaymentPins, PinDeliveries
from .payment_steps import PaymentSteps
from .schedule_grants import ACTION_REASONS as GRANT_REASONS, ConditionalGrants, task_allow_tools
from .run_progress import RunProgress
from . import tool_labels
from .tools.purchase import purchase_tools
from .tools.order import STEP_LOGGED as ORDER_STEP_LOGGED, Orders, ShippingForms, order_tools
from .done_once import ActionLedger, DoneOnce, page_key
from .injection_watch import InjectionDetector, InjectionLog, InjectionWatch, LeakMarker
from .outside_rule import OutsideReview, OutsideTextRule, OwnerWords, PeopleBook
from .privacy_guard import PrivacyGuard
from .permissions import PHONE_NAME, Gate, Permissions, connected_by_family
from .shipping import ShippingBook
from .tools.card_request import card_request_tools
from .tools.payment_step import payment_step_tools, request_pin_tools
from .tools.web import web_tools
from .tools.places import places_tool
from .providers.login_flows import LoginFlows
from .oauth_connect import DeviceFlows, GetJson, PostForm
from .mail import Mail, MailError
from .mcp_oauth import (McpConnections, Fetcher as McpFetcher)
from .connect_ways import ways as connect_ways
from .github_web import GitHubWeb
from .kelo_names import TICK_SECONDS as KELO_TICK_SECONDS, KeloNames
from . import ddns
from .google_web import GoogleWeb
from .google_calendar import GoogleCalendar, calendar_tools
from .phone_calendar import PhoneCalendars, phone_calendar_tools
from .phone_data import PhoneData, phone_calendar_write_tools, phone_tools
from .keyconnect import KeyConnections
from .presets import NETWORK_IDS
from .vpn import VpnRoute
from .push import Apns, Fcm, PushBroker, PushClient
from .live_activity import LiveActivities
from .reports import ReportBox, ReportBroker, ReportClient
from .beta import BetaBroker, BetaClient, BetaDesk
from .testers import TesterDesk
from .packages import Registry
from .nest import Nest
from .custom_skills import SkillProposals, skill_tools
from . import ai_consent, secret_guard
from . import usage as usage_meter
from .providers import cli_manage, releases
from .providers.types import TURN_NOTE as _TURN_NOTE, TURN_NOTE_END
from .home import Home, Skills
from .prompt import TONE_WAYS, TONES, PromptBuilder, turn_lines, valid_timezone
from .downloads import Downloads
from . import cred_keyring
from .tools.connect_tools import connect_tools
from .tools.channel_tools import channel_tools
from .tools.vault_tools import vault_tools
from .vault import Transient
from .secret_requests import SecretRequests, entry_context
from .chat_images import ChatImages, browser_grab
from .quota import Quota
from .storage_cleanup import Cleanup, cleanup_tools
from .storage_cleanup import routes as storage_cleanup_routes
from .storage_watch import DISK_FULL_CODE, StorageWatch, disk_full, disk_full_text
from . import model_limits
from .model_limits import LIMIT_CODE, QuotaGuard, limit_of
from .tools.image_tools import ImageStore, image_tools
from .tools.mail_tools import mail_tools
from .tools.oauth_tools import oauth_tools
from .tools.extract import extract_tool, extractor_for
from .tools.home_tools import home_tools, memory_tools
from .deliverables import Deliverables
from .connector_data import ConnectorData
from .files_api import share_url
from .hosting.channels import workroom_agent_channel, workroom_proxy_channel
from .workroom import InstallProxy, SocketRunner, Workroom, install_site, workroom_tools
from .effort import listed as effort_listed, request_extra as effort_extra
from .effort import restore as effort_restore
from .resume import (CONTINUE_LABEL, MAX_RESTART_RESUMES, MODEL_DOWN_LINE, MODEL_NOTE, MODEL_WAIT_GIVE_UP, MODEL_WAIT_NOTIFY_AFTER,
                     RESTART_LINE, RESTART_NOTE, continue_buttons, is_outage, next_wait, with_language)
from .providers import ChatRequest, Done, TextDelta
from .providers import get_provider, list_providers, stream_chat as default_stream_chat
from .providers.client import stall_seconds
from .providers.registry import (
    REMOVED_PROVIDERS,
    USER_ENDPOINT_PREFIX,
    clear_stored_base_urls,
    set_stored_base_url,
    set_user_endpoints,
    stored_base_url,
)
from .providers import refused as refused_fields
from .store import Store
from .goals import GOAL_MARK, GoalBook, GoalError
from .comparison import Comparisons, comparison_tools
from .repeats import RepeatGuard, collapse_repeat
from .reply_text import ReplyText
from .channels import ChannelManager, Discord, Slack, Telegram
from .netguard import fetch as netguard_fetch
from .tools.browser import (
    BrowserPolicyError, BrowserSession, browser_owner, browser_tools, looks_final, one_run_tab, point_tools,
)
from .live_source import LiveSources
from .tools.live import LiveHub
from .android.card import DeviceCard
from .android.device import DeviceClient
from .android.live import DeviceLive
from .android.mirror import AndroidMirror
from .android.screen import ScreenReader
from .android.tools import AndroidSession, android_tools, phone_grab
from .tools.browser_more import BrowserExtras
from .browser_history import ActionArchive
from .workers import HANDOFF_MARK, PAUSED as WORKER_PAUSED, Limits as WorkerLimits, WorkerHost, Workers, current_turn, current_worker, worker_routes
from .nest_drafts import NEST_SHARE_MARK
from .tools.task_permission import COVERED as GRANT_COVERED, TaskGrants, task_permission_tools
from .followups import Followups, followup_routes
from .api.auth import AuthGuard
from .api import include_api, module_router
from .api.context import AppContext
from .chat_attachments import AttachmentError, ChatAttachments
from .location import location_ask
from .api.e2e_gate import E2EGateway
from .api.e2e_keys import load_key as load_e2e_key
from .api.errors import http_error, locked_error
from .keyguard import Locked as KeyGuardLocked
from .keyring_client import PLACEHOLDER, KeyringClient, ring_key_from, start_up as keyring_up
from .e2e import E2EServer
from .api.versioning import VersionMiddleware
from .app_releases import AppReleases, report as report_ignored_minimums
from .api.provider_policy import ProviderPolicy

MODEL_REQUIRED = N_("No AI model is chosen yet. Choose one in the Kelo app, then send again.")
log = logging.getLogger("uvicorn.error")   # shown with the server's own lines


class _NoSignInCodes(logging.Filter):
    """The access log shows /oauth/... without its query: a returning sign-in carries a one-time code there."""
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and args[2].startswith("/oauth/") and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0] + "?…", *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_NoSignInCodes())
_SECRET_QUERY = re.compile(r"([?&](?:ticket|token)=)[^&\s\"]*")


class _NoTickets(logging.Filter):
    """A live-view ticket (or any token=) in a query never reaches a log line: the access log, and uvicorn's own
    '"WebSocket /path?query" [accepted]' line (logged on uvicorn.error). WebSocket frames are never logged either (at debug level the
    websockets library would print their beginning, and a frame from the phone may be typed text, e.g. a password)."""
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno <= logging.DEBUG and record.msg in ("< %s", "> %s"):
            return False
        args = record.args
        if isinstance(args, tuple) and any(isinstance(a, str) and "?" in a for a in args):
            record.args = tuple(_SECRET_QUERY.sub(r"\1…", a) if isinstance(a, str) else a for a in args)
        if isinstance(record.msg, str) and "?" in record.msg:
            record.msg = _SECRET_QUERY.sub(r"\1…", record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_NoTickets())
APPROVAL_TIMEOUT_SECONDS = 300.0
UNATTENDED_APPROVAL_SECONDS = 1800.0   # a run nobody watches (a schedule, a background task) waits this long for an approval, then skips the step
                                       # (and a follow-up of kind approval_expired offers to try it again, followups.py)
HANDOVER_SECONDS = 600.0               # a human check handed to the user in a chat reply waits this long (they follow it, or come back from the notification)
HANDOVER_UNATTENDED_SECONDS = UNATTENDED_APPROVAL_SECONDS   # ...in a run nobody watches (a job, a schedule); a job's own deadline stays the upper bound
HANDOVER_JOB_MARGIN = 120.0            # a job's hand-over ends this long before the job's deadline, so the job still has time to report
HANDOVER_PER_SITE = 2                  # a site's human check is handed over at most this often in one task (then the agent goes on without it)
HANDOVER_RESOLVED_KEEP = 900.0         # how long GET /api/approvals/pending lists a hand-over that ended (the card folds to its outcome)
SECRET_PLACEHOLDER = i18n.N_("[hidden key]")   # shown through t(); what replaces a key or password in a message sent with "mask"
HELD_STATUS = i18n.N_("You are in control")   # shown through t(); the live line while the user controls the agent's browser tab
CLI_UPDATE_NOTE_DAYS = 3   # a newer claude / agy CLI out this long: the owner gets one notification about it (check_cli_updates)
WATCHED_SECONDS = 20.0   # a reply whose reader asked within this many seconds of its end was seen in the chat (no notification, no unread dot)
SCHEDULE_NOTE = ("[This is the automatic job '{name}' that the user set up. Nobody is watching right now. Do the job and finish with a "
                 "short report. Any approval you need goes to the user's phone and may not be answered; skip a step that is not approved "
                 "and say so. Ask no questions: if something is missing, report what. If there is nothing worth telling, reply exactly "
                 "NO_REPLY. If the job cannot work any more (a login is gone, the site changed, the account has a problem), call "
                 "schedule_pause with the reason. A temporary block (the site is under maintenance, down, or sold out right now): call "
                 "schedule_retry with the time it should work again (the site's own reopening time when it shows one).]")
BACKGROUND_NOTE = ("[The user handed you this task to do on your own, in a browser tab of its own, and may not be watching. Do it and "
                   "finish with a short report. Skip any step whose approval is not answered and say so.]")
JOURNAL_HOUR, JOURNAL_MINUTE = 3, 30
RECENT_WORDS_SECONDS = 3600   # the owner's earlier messages that still count as naming a place (outside_rule.OwnerWords)
REFLECTION_HOUR, REFLECTION_MINUTE = 3, 50      # the nightly look-back, after the journal
# heads this turn's lines (time, calendar, schedules, goals), sent as a message of their own right before the user's message, after
# everything that stays the same between turns (the cached prefix)
TURN_NOTE = _TURN_NOTE   # providers/types.py: the agy adapter recognises it too
# A chat reply that ends without words or at its step budget: plain lines in the user's language (never an exception's text).
NO_ANSWER_LINE = N_("I did the steps above but could not put the result into words. Press Continue and I will pick up from here.")
STEP_LIMIT_BACKGROUND = N_("This is taking many steps, so the rest goes on in the background. I will tell you when it is done; you can "
                           "follow or stop it on the Work page.")
STEP_LIMIT_CONTINUE = N_("I used the steps one reply can take. Press Continue and I will carry on from here.")
STEP_LIMIT_STOPPED = N_("I stopped because the last steps kept repeating without getting anywhere. Press Continue to try again, or tell "
                        "me what to change.")
STEP_LIMIT_PROGRESS = 2   # distinct successful tool calls in a reply that count as real progress (StepLimit.progress)
# where a tool reads the user's own data: the links in it may lead to the items of a request for many (tools/task_permission.py)
OWN_DATA_SOURCES = frozenset({"email", "file", "calendar", "connector", "mcp", "phone"})
FALLBACK_REASON = N_("the main model did not answer")
warm_clock = time.monotonic   # the throttle of warming on app open (tests move it)
FAILED_LINE = N_("Something went wrong on the server, so I stopped. Please try again in a moment.")
MODEL_FAILED_LINE = N_("The AI model could not answer this time. Please try again in a moment.")
# the provider sent nothing at all for the stall limit (providers/client.py STALL_SECONDS): the turn ends instead of hanging
STALLED_LINE = N_("The AI model sent nothing for {minutes} minutes, so I stopped waiting. Please try again, or choose a lower thinking level.")
CHANNEL_NOTE = ("[Not from the user: this message came from the user's {name}; your reply is read there on a phone: keep it short, no tables. "
                "Fill-in cards and payments can only be answered in the Kelo app.]")
STEP_NOTE = ("[Not from the user: this is a next step from the briefing card that the user tapped and sent. It is a yes: do it now, "
             "without asking again whether to.]")
NEW_SESSION_WORDS = ("/new",)   # the only chat command; the apps list it when "/" is typed
NEW_SESSION_NOTE = ("[Not from the user: the user started a new conversation (/new). The earlier messages are no longer in your context; "
                    "your memory files, schedules and goals still are. Say in one short line that a new conversation has started, and "
                    "nothing else: no summary of before, no question.]")
REQUEST_STEP_LOGGED = N_("Allowed by your request: {request}")   # a click the user's own words asked for (tools/order.py)
LOCKED_LINE = N_("Kelo is locked: open it on your phone to unlock it, then send again.")
KELO_BUSY_TITLE = N_("Kelo addresses paused: too many new claims")
KELO_FULL_TITLE = N_("Kelo addresses are all taken ({used}/{max})")
KELO_FULL_BODY = N_("New copies are refused now. Move the Cloudflare zone to a bigger plan or raise the address limit.")
KELO_NEAR_TITLE = N_("Kelo addresses are almost all taken ({used}/{max})")
KELO_USED_TITLE = N_("Kelo addresses: {percent}% used ({used}/{max})")
REPORT_TITLE = N_("New report: {reason}")
REPORT_BODY = N_("Someone reported a reply. Read it in Operator settings → Reports.")
REPORT_REASONS = {"harmful": N_("harmful or offensive"), "inaccurate": N_("not accurate"), "privacy": N_("private information"),
                  "other": N_("other")}
KELO_NEAR_BODY = N_("New copies will be refused at {max}. Move the Cloudflare zone to a bigger plan or raise the address limit soon.")
KELO_BUSY_BODY = N_("{count} new addresses are waiting for their first connection. New claims are refused until fewer wait, at the latest until {time}.")
LOCKED_WAIT_SECONDS = 12 * 3600.0   # a scheduled run waits this long for a phone to unlock Keyguard, then gives up
LONG_THINKING_SECONDS = 60.0   # still reasoning after this long: the status says it is a long think (the apps do the same)
LONG_THINKING = N_("Thinking for a long time")


def chat_error_text(e: BaseException) -> str:
    """What the chat says for a reply that failed: the user's words, never the exception's (that goes to the log)."""
    from .providers.types import ProviderError, ProviderStalled
    if isinstance(e, KeyGuardLocked):   # a provider key is under Keyguard and no phone unlocked it since the restart
        return i18n.t(LOCKED_LINE)
    if isinstance(e, ProviderStalled):
        return i18n.t(STALLED_LINE, minutes=max(1, round(e.seconds / 60)))
    return i18n.t(MODEL_FAILED_LINE if isinstance(e, (ProviderError, ContextOverflow, TimeoutError)) else FAILED_LINE)


EMPTY_REPLY_NUDGE = ("You gave no answer last time, probably because you tried a command or a file that is not available to you. There "
                     "is no shell. Use only the tools in your list (find_tools loads more); your files, the time, the calendar and the skills list are already in "
                     "this conversation. Answer the user now, in their language.")
HEARTBEAT_SECONDS = 4.0  # a reply stream that is quiet this long sends a comment line, so phones, routers and proxies keep the connection
TURN_IDLE_SECONDS = 180.0  # a reply that nobody has asked about for this long is stopped (the phone is gone)
TURN_KEEP_SECONDS = 600.0  # a finished reply stays available this long, for a reader that is catching up
STOP_GRACE_SECONDS = 1.0   # a closed card or a declined approval reaches the model this long after, so a stop that follows it wins (turn_engine)
ENTRY_WAIT_SECONDS = 600.0    # a followed reply waits this long for a fill-in card it asked for; a later answer starts a new reply
ENDED_TABS_KEPT = 20   # finished automatic-run / handed-over-task tabs still listed (with their last picture) after they close
POLL_WAIT_MAX = 25.0  # the longest a "what is new?" request waits (a proxy cuts a quiet request at about 100 s)


class LanguageMiddleware:
    """Sets the language of user-facing text (i18n.py) for each request from its Accept-Language header."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        header = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k == b"accept-language"), None)
        token = i18n.use(i18n.pick(header))
        try:
            await self.app(scope, receive, send)
        finally:
            i18n.reset(token)


def guess_language(store: Store) -> str | None:
    """The language of a server that was in use before languages were tracked: Korean when the user wrote Korean."""
    rows = store._all("SELECT content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 200")
    text = " ".join(str(r["content"]) for r in rows)
    if not text.strip():
        return None
    hangul = sum(1 for ch in text if "\uac00" <= ch <= "\ud7a3")
    return "ko" if hangul >= max(5, len(text) // 20) else None


# Settings, Settings.from_env and the minimum app builds (MIN APP VERSION) live in settings.py; re-exported here for callers
from .settings import (  # noqa: E402,F401
    MIN_APP_ENV, MIN_APP_UNIT, Settings, _count, _default_content_dir, _disk_limit, _min_app_versions, _share, desktop_version_code,
)


class AnswerIn(BaseModel):
    question: int = Field(ge=0, lt=20)            # the question's place in the open quick replies
    value: str = Field(max_length=300)            # its plain form (goals.py check_answer)


class AttachmentIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)


class TalkIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    timezone: str | None = Field(default=None, max_length=64)  # the client's IANA zone, for the time the agent sees
    answers: list[AnswerIn] | None = Field(default=None, max_length=8)   # the quick-reply fields this message answers
    # a key, password or card in the text is held back (409 secret_in_message, secret_guard.py) unless the owner chose after the
    # warning: "allow" sends it as written (the message is marked so lasting summaries leave the secret out), "mask" replaces it
    secrets: str | None = Field(default=None, pattern="^(allow|mask)$")
    # "check": hold the message back (428 ai_consent_required) while the AI company it would go to has not been agreed to
    # (ai_consent.py). The apps send it; API clients do not.
    ai_consent: str | None = Field(default=None, pattern="^check$")
    # files sent with the message (chat_attachments.py): uploads named "uploads/<name>", kept with the message; auto_text = the text
    # is the app's own "read and summarise" sentence (still sent to the agent; apps hide it)
    attachments: list[AttachmentIn] | None = Field(default=None, max_length=10)
    auto_text: bool = False


# A reply in progress, the approvals it waits on and MODEL_STARTED live in turn_engine.py; re-exported here
from . import turn_engine
from . import turn_resume
from . import turn_goals
from . import turn_workers
from .turn_engine import MODEL_STARTED, ApprovalBroker, Turn, TurnRegistry, run_turn  # noqa: E402,F401
from .turn_log import INTERRUPTED, TurnLog  # noqa: E402


# ---- turn events ------------------------------------------------------------------------------------------------------
# What a reply sends to the app, in order (SSE `data:` lines on /api/chat and /api/talk; `events` of GET /api/turns/{id}):
#   {"type": "reaction", "message_id", "emoji"}           the owner's message of this reply: 👀 first, its emoji right before "done" (⚠️ before "error") (reactions.py)
#   {"type": "thinking"}                                   the model is reasoning (no text)
#   {"type": "text", "text": "..."}                        a piece of the answer
#   {"type": "tool_started", "id", "name", "arguments", "label", "hidden"}
#   {"type": "approval_request", "approval_id", "id", "name", "arguments", "detail", "label", "site", "can_remember", "task_id", "expires_at"}
#        answer: POST /api/approvals/{approval_id}. `site`: the host it acts on ("" when none); `can_remember`: the card may offer
#        "allow on this site" (never for payments, schedules or the final pay click); `expires_at`: unix time it stops waiting.
#        `ask`: the card's title as a whole question in the user's language ("Sign in?" -> "로그인할까요?", tool_labels.ASKS); "" for a
#        tool without one (build it from `label`, as before).
#        Also "schedule_id" (the scheduled job this run belongs to, else null) and "can_remember_schedule": the card may offer
#        "keep allowing in this scheduled job" (remember "schedule": the same step on the same site in that job's runs; never for payments,
#        card filling, the wallet, schedule changes or the final pay click).
#        for confirm_purchase it also carries
#          "payment": {"site", "summary", "amount", "currency", "amount_text", "items": [{"name", "options", "quantity", "price_text"}],
#                      "shipping": {"source": profile|once|site, "label", "name", "phone" (E.164), "formatted" (the address in its
#                                   country's format, without the name; for "site" the page's words), "country"} | null,
#                      "coupon": "" | "<code>", "order": {"what"} | null}
#          inside an order (tools/order.py) this is the order's ONE card: show it all at once (items, address, card, total, site).
#        and, when a wallet card was named,
#          "card": {"id", "label", "brand", "last4", "auto_pay", "needs_card", "reason", "reason_text"}
#        `reason` says why it asks instead of paying automatically: not_auto | no_server_copy | paused | vault_locked | currency |
#        over_per_payment | over_monthly | first_site (`reason_text` is the Korean line). `needs_card: true` (an end-to-end card): on
#        Allow the phone asks for biometrics, decrypts the card and POSTs it to /api/wallet/deliveries/{approval_id}; the approval and
#        the delivery may arrive in either order.
#   {"type": "shipping_form", "id", "form_id", "site", "what", "country", "prefill": {<address field keys>, "phone", "note"}, "label",
#        "save": true, "ask_payment": bool, "expires_at"}   an order needs an address (none on the site account, no shipping profile):
#        one form in the chat, country-aware (GET /api/address/form/{CC}), prefilled with what Kelo remembers for the user to check,
#        "use for next orders" on by default. Answer: POST /api/shipping-forms/{form_id} (SHIPPING API); the agent waits for it.
#   {"type": "card_needed", "id", "approval_id", "card": {"id", "label", "brand", "last4"}, "site", "amount", "currency", "amount_text",
#        "expires_at"}   an allowed payment waits for the phone to send that end-to-end card (unix time `expires_at`, ~5 minutes);
#        not sent in time = not paid
#   {"type": "payment_auto", "id", "card": {"id", "label", "brand", "last4"}, "payment": <payment, see WALLET API>}
#        a payment went ahead without asking, under the user's auto-pay rules (also listed in /api/wallet/notifications).
#        A purchase a schedule made under the permission it got at setup also carries "schedule_grant": {"id", "schedule_id", "via"}
#        (via: the card's label or the payment way); "card" is then left out when no wallet card was used.
#   {"type": "tool_result", "id", "name", "output", "is_error", "approved", "label", "hidden"}
#   {"type": "browser", "id", "tab", "title", "url", "host", "status", "image", "width", "height", "bytes", "at"}
#        a card of the assistant's browser tab: `id` is the tool call it belongs to; `tab` the BrowserTab id it shows (the phone's own
#        tab, "owner", or a job's / run's "task:<id>" / "schedule:<id>": open that one live, never "this phone's tab"); `image` is a data:image/jpeg;base64 URL
#        (at most 120 KB of JPEG, at most 720 px wide) or null; `status` is a short line in the user's language such as "Looking at the site",
#        "Waiting for payment approval", "Purchase in progress"; at most one card per 2 s per tab (a card shown with an approval may come sooner).
#   {"type": "device", "id", "key": "android", "status", "image", "width", "height", "bytes", "at"}   the same card for the Android device
#        the reply is using (the apps' phone card): `status` a short line in the user's language ("Tapping on the phone"), `image` a
#        data:image/jpeg;base64 URL (<= 120 KB, <= 720 px wide, portrait) or null; at most one per 2 s. Open it live with key "android".
#   {"type": "browser_control", "id", "controller": "user"|"agent"}   the user took over the tab this reply works in (`id`, a BrowserTab
#        id) or handed it back. While "user", the reply's browser steps wait (up to 5 minutes, then the agent is told it could not act).
#   {"type": "browser_check", "id", "tab", "site", "vendor", "name", "kind"}   a human check ("A human check is needed") appeared in the tab `tab` (a
#        BrowserTab id). vendor: cloudflare|recaptcha|hcaptcha|datadome|perimeterx|arkose|geetest|aws_waf, `name` its Korean/brand name,
#        kind: interstitial (the whole page is the check) | widget (a box or puzzle on the page). Sent once the agent's own tries (at
#        most 2, in the page with its browser steps; no solving services) are used up or it gave up: the app offers the live view
#        (live-ticket + take control) so the user does it. At most once per 10 min per tab and check. In an unattended run also a
#        Notification of type browser_check. Only sent where no hand-over runs (below).
#   {"type": "human_check", "approval_id", "state": "waiting", "site", "tab", "check": {"vendor", "name", "kind"}, "label", "ask",
#        "detail", "task_id", "expires_at", ...}   a human check on the tab this reply works in, which the agent could not do itself in
#        its tries, was handed to the user: the browser step
#        waits (a chat reply 10 min, a job or a schedule 30 min, never past a job's deadline). The same item is in GET /api/approvals/
#        pending with "kind": "human_check" (the chat's live card, the Work page) and an approval_waiting Notification is left (one).
#        "Solve it": open the live view of `tab` and take control; the server re-reads the tab every 3 s and when it is handed back,
#        and ends the item by itself once the check is gone. "Skip this site": POST /api/approvals/{id} {"approve": false}
#        ({"approve": true}, an older app's Allow, means "done, go on"). Then {"type": "human_check", "approval_id", "state":
#        solved|progressed|skipped|expired|cancelled, "site", "tab"}; the agent is told to go on (solved; progressed: the user's step
#        of a sign-in is done but the sign-in goes on, so the agent takes the next step) or to go on without that site and say so.
#        "check": {"vendor", "name", "kind" (verification for a sign-in step), "step": approve | code (what the user is asked to do)}.
#   An approval_request for find_login_code carries "detail" WITH the code ("Enter this code? 123456 — from …") and
#        "login_code": {"code", "from", "subject", "received_at"}; both exist only in the live event / GET /api/approvals/pending (memory).
#        The approval log and the notification keep the masked line ("••••56"); the model only learns the code's length.
#   {"type": "secret_request", "id", "request": SecretRequest}   a fill-in card the agent asked for (add_service_key, request_login,
#        request_card), shown in the chat right here; the same event again with the new "state" once it is answered while the reply runs.
#        SecretRequest: {"id": "sr_…", "kind": key|login|card, "name" (the vault item; "" for a card), "label", "hosts" (where the value
#        may be sent / the card used; may be empty for a card), "reason", "check" (a key is tried before it is saved), "source":
#        agent|draft, "turn_id", "task_id", "after_id" (newest message id when asked), "state": open|saved|cancelled|expired|replaced,
#        "created_at", "answered_at", "expires_at", "result"? (saved: {"checked": ok|unchecked} | {"mode"} | {"card": {"id","label",
#        "brand","last4","default"}}), "message_id"? (GET /api/secret-requests only: the agent message it goes under, null while
#        that reply is being written)}. Save: key -> PUT /api/credentials/{name}/secret (422 when the service refused it on the test
#        request; nothing saved), login -> PUT /api/vault/{name}/login, card -> the wallet's card form (POST /api/wallet/cards), then
#        POST /api/secret-requests/{id}/card {"card_id"}. Each answers "turn_id": a reply that started to carry on (follow it like
#        your own), or null (the reply that asked was still waiting and goes on by itself). Cancel: POST /api/secret-requests/{id}/cancel.
#        Saving is the consent: there is no approval card first (add_service_key asks only when it would move a stored key to other hosts).
#   {"type": "followup", "id", "followup_id", "kind", "status": open|snoozed|dropped, "topic"?, "until"?}   the agent marked a question as
#        awaiting the user's reply (await_reply), or put off / dropped a follow-up (see FOLLOW-UPS API)
#   {"type": "replies", "questions", "goal_id"}: tap-to-answer buttons were attached; the app loads the messages again when the reply ends.
#        A choice question may have "cards": one per option, same order: {"image"?: {"image_id", "url": "/api/chat-images/{id}", "width",
#        "height"} (thumbnail, longest side 480), "detail"?: str (<= 80 chars, "price · arrival"), "recommended"?: true (at most one)}; {} =
#        an option with nothing to show. Same shape in GET /api/messages "replies". Apps that ignore "cards" show plain buttons (see goals.py).
#   {"type": "comparison"}: a comparison card was attached; the app loads the messages again when the reply ends
#   {"type": "worker", "id", "status", "title"}   a background job (see WORKERS API) was started by this reply (status "running") or one
#        it started / this run belongs to changed status: running | needs_input | blocked | done | failed | stopped | interrupted
#   {"type": "retry", "reason"} / {"type": "fallback", "provider", "model", "reason" (the user's words), "detail" (for logs)}
#   {"type": "step_limit", "steps", "progress", "next": background|continue|stopped, "job_id"?}   the reply used its step budget:
#        background = the work was moving, the rest goes on in a background job (job_id); continue = Continue button follows;
#        stopped = the last steps went in circles (Continue button follows). A plain text line saying so follows. A job's runs send
#        {"type": "step_limit", "steps", "progress"} and chain their chunks (workers.py).
#   {"type": "error", "message" (always the user's words, translated), "code"?, "detail"? (the exception, for logs; never shown)}
#   {"type": "done", "input_tokens", "output_tokens", "steps"}
#   {"type": "interrupted", "reason": "restart"}   the last event of a reply the server stopped under (written at start-up, turn_log.py);
#        the reply's status is then "interrupted", and the resume offer (resume.py) is what carries on.
#   Following a reply (turns.py): GET /api/turns/{id}?after=N&wait=20 (long-poll, the fallback) and the WebSocket GET /api/turns/{id}/ws?after=N
#        (the events numbered: frames {"seq", "event"} from `after` on, then {"done": true, "next"}; {"type": "ping"} every 25 s while nothing
#        happens; {"type": "gone"} and close 4404 for an unknown reply; close 4401 without a valid token; close 1000 at the end).
#        Both answer from the server's memory while the reply is known there, and from the reply log after a restart (done: true).
#   tool_started / tool_result also carry "group" (consecutive calls with the same group fold into one chip) and "done" (the folded
#   chip's words with {n}, e.g. "Read {n} messages")
# `name` is the raw tool name; `label` is the short Korean words to show instead (tool_labels.py), and `hidden` marks calls the
# chat need not show (the assistant reading its own notes and guides).
#
# ---- AGENT SCREEN API (all need the bearer token; times are unix seconds unless named *_iso / ISO strings) -----------------------------
# Task (one per reply: chat, an automatic run, a background task):
#   {"id": int, "turn_id", "title", "status": running|waiting_approval|done|failed|stopped|interrupted|waiting_model, "result": one line, "source": chat|api|
#    schedule:<id>|background|worker|handoff, "schedule_id": int|null, "started_at", "finished_at": ts|null, "seen": bool, "day":
#    "YYYY-MM-DD" (profile zone), "live_status": "Opening a site"… while running else null, "turn_available": bool (GET /api/turns/{turn_id}
#    still answers), "parent_id": the task that started this background job|null, "worker_id": that job|null (see WORKERS API),
#    "resumed_from" / "resumed_as": the task this one goes on from / the task that went on from it|null}
#   interrupted = a restart (or a model outage) cut it off; waiting_model = every AI model failed for a reason of the provider, it goes
#   on by itself once a light check passes (resume.py). A chat turn cut off ends with a message carrying a "Continue" quick reply
#   (replies with one question, one option): sending that option as the next message resumes the turn.
# GET  /api/tasks?limit=50&before=<id>&source=   {"items": [Task] newest first, "unseen": n, "next_before": id|null}
# GET  /api/tasks/{id}                           Task + {"answer", "error", "input_tokens", "output_tokens", "approvals": [Approval]}
# POST /api/tasks/seen {"up_to": id}             {"ok", "marked", "unseen"}          POST /api/tasks/{id}/stop  {"ok"} (404 = not running)
# GET  /api/agent/status   poll every few seconds: {"busy", "status": live line|null, "task_id", "turn_id", "title", "source",
#        "controller": "user"|"agent", "controlled_by": device|null, "running": [{"task_id", "turn_id", "title", "status", "source",
#        "waiting_approval", "tab", "controller"}], "pending_approvals": n, "open_followups": n (FOLLOW-UPS API), "unseen_tasks": n,
#        "unseen_notifications": n, "secret_requests": n (open secure entries), "secret_request_kinds": {"key"|"login"|"card"|
#        "payment_pin": n} (so the badge can say what waits), "avatar_version": int, "briefing_rev": str, "agent_name", "tone"} (the last two: the profile's, so
#        a change made in the chat (set_tone) or on another phone shows without reloading the profile; briefing_rev changes when a
#        briefing card already sent changes, e.g. its hours line was answered on another device: ask GET /api/messages again)
#        `controller` is about the running reply's tab (when idle: the asking phone's own tab); while "user", `status` is "You are in control".
# Approval (history row): {"id" (= approval_id), "task_id", "tool", "label", "site", "detail" (the card's line; never arguments),
#   "decision": pending|allowed_once|allowed_always|allowed_by_rule|denied|expired|cancelled, "asked_at", "answered_at", "rule_id",
#   "unattended": bool, "can_remember": bool}
# POST /api/approvals/{id} {"approve": bool, "remember"?: "site"|"schedule"}  {"ok", "decision", "remembered": bool, "rule": Rule|ScheduleRule|null}
#        remember is honoured only when the card said can_remember (site) / can_remember_schedule (schedule); otherwise the answer is
#        an ordinary allow (remembered false).
# GET  /api/approvals/pending        {"items": [approval_request event without "arguments", + "turn_id", "asked_at", "unattended",
#        "source" (the asking run's task source: chat|api|schedule:<id>|background|worker|...), "worker_id" (the background job it is a
#        step of)|null, "title" (that run's title)], "resolved": [{"approval_id", "kind": "human_check", "outcome":
#        solved|progressed|skipped|expired|cancelled, "site", "tab", "task_id", "at"}] (hand-overs that ended in the last 15 minutes, newest first:
#        a card the app still shows folds to its outcome)}   the app shows these in the chat as well as on the 작업 page.
#        An item with "kind": "human_check" (see the human_check turn event) also carries "tab" and "check"; its decision in the
#        history is solved | progressed | skipped | expired | cancelled (tool "browser_check").
#        An item with "kind": "location" is a LOCATION REQUEST card (below), listed so a reloaded chat shows it again.
# LOCATION REQUEST (location.py; backlog 24): my_location found no location in a followed chat reply, so the reply WAITS on a card:
#   turn event {"type": "location_request", "approval_id", "state": "waiting", "reason" (the agent's words, may be ""),
#   "settings": bool (the user refused before: offer "Open Settings" instead of the OS prompt), "task_id", "tab", "expires_at"}
#   then, when it ends, {"type": "location_request", "approval_id", "state": allowed|refused|expired|cancelled}.
#   Allow -> the app asks the OS (iOS "while using the app", Android location), reads the place and sends
#   POST /api/location-requests/{approval_id} {"allowed": true, "place": {"lat", "lon", "name"?, "country_code"?, "accuracy"?,
#   "sublocality"?}}; refused in the OS prompt or "Not now" -> {"allowed": false} (or POST /api/approvals/{id} approve=false).
#   The tool then returns the place (with the neighbourhood from the map), or tells the agent the user did not allow it; an
#   unanswered card runs out after the approval time (APPROVAL_TIMEOUT_SECONDS) and the reply goes on without it. Only in a followed
#   chat reply; background and scheduled runs never ask. One approval_waiting notification; history tool "my_location".
# GET  /api/approvals/history?limit=50&before=<asked_at>&task_id=   {"items": [Approval], "next_before": ts|null}
# Rule: {"id", "kind": browser_action|login, "kind_label", "site", "created_at", "uses", "last_used"}  (a site covers its subdomains)
# GET  /api/approvals/rules {"items": [Rule]}       DELETE /api/approvals/rules/{id} {"ok"}
# TaskPermission (tools/task_permission.py): one Allow (tool request_task_permission) for a repeated step of one task: {"id", "site"
#   (registrable domain), "kind": apply|send|delete, "action" (the user's words), "max_count", "used", "task_id", "approval_id",
#   "created_at", "expires_at", "jobs": [job ids that work under it]}. Ends with its task (the chat reply, or the job it was handed to).
#   Each step it covers is logged in the history (decision allowed_by_rule, rule_id null). Never payments or account changes.
# GET  /api/task-permissions {"items": [TaskPermission]} (running tasks only)    DELETE /api/task-permissions/{id} {"ok"} (404 = over)
# Schedule: {"id", "name", "prompt", "kind": cron|window|interval|once, "cron": str|null, "run_at": ISO|null (once),
#   "every_minutes": n|null + "start_at": ISO|null (interval), "window_minutes": n (window: the cron time ± n minutes, else 0), "starts_on":
#   YYYY-MM-DD|null (cron/window), "ends_on": YYYY-MM-DD|null (repeating), "timezone",
#   "enabled", "description": words in the user's language ("Every Mon at 9:00 AM", "Every day at 9:00 AM, give or take 15 minutes", "Every 2 hours"), "next_run": ISO with
#   offset|null, "next_run_ts", "created_at", "updated_at", "created_by": user|agent, "goal_id": int|null, "last_run": {"task_id",
#   "status": done|quiet|failed|stopped|paused|missed, "at"}|null, "failure_count", "disabled_reason": str|null, "paused": bool,
#   "paused_reason": str|null, "paused_at": ts|null, "archived_at": ts|null, "purge_at": ts|null (archived only), "running": bool,
#   "retry_at": unix ts|null, "retry_why": str|null}
#   retry_at: a run stopped by a temporary block (site maintenance, down, sold out) asked to be tried again then (schedule_retry): the same
#   schedule runs once more at that time with the same permissions; its regular times do not move. null when none is waiting. retry_why is
#   the reason in the owner's language. That run shows in the history as "skipped" with the line "Will try again at <time>: <why>" and
#   sends no notification; the retry's own report is the one the owner gets. Apps ignore fields they do not know.
#   quiet = the run had nothing worth telling (the agent replied NO_REPLY): no chat message, no notification, still in the history.
#   paused = the run (or the user) paused it for a lasting blocker: off until turned back on (PATCH enabled true or POST .../resume).
# GET    /api/schedules?goal_id=   {"items": [Schedule] (not archived; only that goal's when goal_id is given), "timezone": default zone}
# POST   /api/schedules            201 Schedule. {"name", "prompt", one of "cron" (+ "window_minutes"? 0..120) | "run_at" (ISO local or
#          unix) | "every_minutes" (≥ 5; "run_at" then is the first run, default one interval from now), "timezone"?, "enabled"?, "goal_id"?,
#          "starts_on"? (cron: first local day, YYYY-MM-DD), "ends_on"? (repeating: last local day; when it has passed the schedule turns
#          itself off, reason "finished")}  PATCH: "starts_on"/"ends_on" "" clears it.
#          A schedule the user saves here (POST, or a PATCH that changes its name, task, time or days; not the switch alone) is handed to
#          the main agent as a hidden message led by "[New schedule from the user, not a chat message]" (left out of GET /api/messages):
#          it checks ahead that the job can run unattended (logins, connected services, phone permissions, unclear details) and stays
#          silent (NO_REPLY) when all is ready, else asks once in the chat (with quick_replies). Schedules the agent makes are not.
#          422 {"detail"} for a bad cron, a past run_at, both/neither, runs closer than 5 minutes, an unknown zone, a window too wide for
#          how often it runs, an unknown goal
# GET/PATCH/DELETE /api/schedules/{id}   PATCH takes any of the POST fields ("goal_id": 0 = no goal); {"enabled": false|true} is the
#          on/off switch (true also clears a pause). DELETE archives: off and out of the list, restorable for 30 days, then removed;
#          DELETE ?forever=true deletes it for good at once (with its run history).
# POST   /api/schedules/{id}/run   {"ok", "task_id", "turn_id"} (409 = already running); follow it with GET /api/turns/{turn_id}
# POST   /api/schedules/{id}/pause {"reason"?}  Schedule (paused; no notification for the user's own pause)
# POST   /api/schedules/{id}/resume             Schedule (on again; 422 for a one-time job whose time has passed)
# GET    /api/schedules/{id}/runs?limit=20      {"items": [Run] newest first}  (the last 100 per schedule are kept; archived ones too)
#   Run: {"id", "schedule_id", "started_at", "finished_at": ts|null (running), "status": running|done|quiet|failed|stopped|skipped|paused,
#        "result": one line (the answer's first sentence, the pause reason, why it was skipped), "task_id": int|null, "error": str|null}
#   skipped = the time came while the last run was still going, or the server was down too long (CATCH_UP_SECONDS).
# GET    /api/schedules/archive                 {"items": [Schedule with archived_at, purge_at], "keep_days": 30}
# POST   /api/schedules/{id}/restore            Schedule (back as it was; a one-time job whose time passed comes back off) 404 / 422
# DELETE /api/schedules/archive/{id}            {"ok"}: an archived one deleted for good (it cannot be restored any more); 404
# GET    /api/schedules/{id}/rules              {"items": [ScheduleRule]}   DELETE /api/schedules/{id}/rules/{rule_id} {"ok"}
#   ScheduleRule: {"id", "schedule_id", "tool", "label", "site" ("" = a step on no site), "created_at", "uses", "last_used"}
# GET    /api/schedules/{id}/grants             {"items": [ScheduleGrant]}   DELETE /api/schedules/{id}/grants/{grant_id} {"ok"}
#   What the user allowed once at setup (task_allow, schedule_grants.py) so the task acts without a card. Removed with the task.
#   ScheduleGrant (a conditional task's grant): {"id", "scope": schedule|watcher|job, "scope_ref", "scope_key", "task_name",
#        "task_when", "schedule_id" (0 for a watcher / job), "schedule_name", "schedule_when", "kind": login|purchase|send|change,
#        "site" (registrable domain), "created_at", "uses", "last_used", "trigger", "action", "once", "urgent", "missed",
#        "max_count" (0 = no count)} + login: {"login", "login_label"} | purchase: {"what", "max_amount", "currency", "monthly_cap",
#        "runs_per_month", "max_amount_text", "monthly_cap_text", "spent_this_month", "card_id", "card_label", "card_last4", "used",
#        "shipping_*"} | send / change: {"what", "targets": [str], "template", "varies", "allow_delete", "used"}
#   The same grants for a watcher / a job: GET /api/watchers/{id}/grants, GET /api/workers/{id}/grants (DELETE .../grants/{gid});
#   every task's: GET /api/task-grants {"items"}, DELETE /api/task-grants/{gid}.
#   The setup card (approval_request of task_allow) carries "grant": {"scope", "task_name", "task_when", "trigger", "action", "once",
#        "urgent", "missed", "login": {"label", "site"}|null, "purchase": {"site", "what", "max_amount", "currency",
#        "max_amount_text", "max_count", "monthly_cap", "monthly_cap_text", "card": {"id", "label", "last4", "auto_pay",
#        "auto_limit", "auto_limit_text", "phone_only"}|null, "card_note", "shipping"}|null, "send": {"to", "site", "template",
#        "varies", "max_count"}|null, "change": {"what", "where", "allow_delete", "max_count"}|null}
#   A card asked in a run of a task marked urgent carries "urgent": true and "missed" (its notification too: Notification.urgent).
#   GET /api/vault items of kind login carry "schedule_grants": [ScheduleGrant] (the tasks that may use that login).
# GET    /api/goals/{id}/schedules              {"items": [Schedule]} the jobs that serve that goal (404 no such goal)
# The PHONE CALENDAR API (phone_calendar.py: PUT/DELETE/GET /api/phone-calendar) and GOOGLE CALENDAR (google_calendar.py, docs/google.md:
#   GET /api/google/calendar, POST /api/google/web/start {"origin", "product": gmail|calendar}) are described in their modules.
#   New Google sign-ins stay closed until the operator opens them (PUT /api/oauth-apps/google/open {"open": bool}, any signed-in device): start
#   answers 409 with the review note, and connector rows carry "connect_state": "pending_review"; connected accounts keep working.
# The PHONE DATA API (phone_data.py: /api/phone, /api/phone/{contacts|messages|call-log|health|dialer|notifications},
#   /api/phone/tasks/claim, /api/phone/tasks/{id}/result) is described in its module.
# The CHECKLIST API (checklist.py), WATCHERS API (watchers.py) and PROACTIVE API (proactive.py) are described in their modules:
#   GET/PUT /api/checklist, POST /api/checklist/run; /api/watchers[/{id}[/dry-run|/enable|/events|/buy-check]]; GET/PUT /api/proactive,
#   GET /api/proactive/log, POST /api/proactive/run. Tasks of the checklist have source "checklist", proactive messages "proactive".
# BrowserTab: {"id" (owner key: "owner" | device id | "schedule:<id>" | "task:<hex>"), "label", "kind": owner|device|schedule|task,
#   "title", "url", "host", "status": working|idle|failed, "status_text" ("You are in control" while the user holds it), "last_activity": ts|null,
#   "task_id": int|null, "controller": "user"|"agent", "controlled_by": device id ("owner" = the master token)|null, "viewers": n,
#   "thumbnail": data:image/jpeg;base64 (≤ 60 KB, 360 px wide)|null, "thumbnail_width", "thumbnail_height", "ended_at": ts|null}
#   status "ended": a schedule/task tab whose run is over and that was closed; listed with the picture, title, url, host and status
#   line it had at the end ("ended_at" = when it closed; null for open tabs). The last ENDED_TABS_KEPT (20) are kept, in memory.
# Tab lifecycle (a sweeper every 60 s): a chat tab (owner / device) closes after 1 hour unused, counted from the end of the last
#   turn that used it; a schedule:* / task:* tab closes as soon as its run is over (done/failed/stopped) and becomes an "ended"
#   entry. Never while someone watches it live, the user controls it (both also count as use), an approval for it is pending or a
#   turn works in it: checked again on the next sweep. Closing a tab keeps cookies and sign-ins (the browser profile stays).
# GET    /api/browser/sessions?thumbnails=true   {"available": bool, "items": [BrowserTab]} working first, ended last
#   Every BrowserTab has "source": "browser". When an Android device is set up (ANDROID_DEVICE) the list starts with one more item, the
#   device as a live source: {"id": "android", "key": "android", "kind": "device", "source": "device", "label"/"title": "Android", "width",
#   "height" (its screen, 0 when the bridge is away), "status": working|idle, "controller", "controlled_by", "viewers", ...}. The
#   routes below take the key "android" for it (404 without a device); frames are JPEG <= 720 px wide, at most 3 a second, fetched only
#   while someone watches; input events (frame pixels): {"type":"tap","x","y"}, {"type":"swipe","x1","y1","x2","y2","ms"?},
#   {"type":"text","text"}, {"type":"key","key":"back|home|enter|recents"}; css_w x css_h of a frame = the device's own pixels.
# GET    /api/browser/sessions/{id}/screenshot   image/jpeg ≤ 300 KB, ≤ 1080 px wide (404 = no tab / blank page); for an ended entry
#          the picture kept when it closed
# POST   /api/browser/sessions {"prompt", "url"?}   201 {"id", "task_id", "turn_id"}: a background task in a tab of its own
# DELETE /api/browser/sessions/{id}              {"ok"} (409 while a task works in it); on an ended entry: removes it from the list
# Step pictures of unattended runs (browser_history.py): every browser step of a "task:*" / "schedule:*" tab keeps a JPEG (≤ 800 px,
#   ≤ 150 KB) and a manifest line; per tab the last 300 steps and ≤ 60 MB, each kept 14 days. Chat tabs are not recorded.
# GET    /api/browser/sessions/{id}/history?before=<seq>&limit=50   {"id", "items": [HistoryStep] newest first, "total", "bytes",
#          "next_before": seq|null (pass as `before` for the next page)}; empty items for a tab without history; 404 = no history kept
#          on this server.  HistoryStep: {"seq", "at" (unix s), "url", "title", "action" (tool name), "detail" (where it acted: the
#          address opened, the element clicked; never typed text, codes or card data), "task_id"|null, "bytes", "width", "height",
#          "frame": "/api/browser/sessions/{id}/history/{seq}"|null (null = no picture, e.g. a blank page)}
# GET    /api/browser/sessions/{id}/history/{seq}   image/jpeg (404 = gone)
# Browser network and setup (docs/browser.md): the browser container has no route anywhere (internal network); pages reach the internet
#   only through the egress proxy, which refuses private, local, CGNAT, link-local and metadata addresses. Managed policy, the user's
#   language (BROWSER_LANG) and time zone (profile timezone, per tab), downloads into a shared read-only folder (BROWSER_DOWNLOADS_DIR).
#
# ---- LIVE VIEW API: watch a tab move, and take it over ------------------------------------------------------------------------------
# A "device" below is the paired phone (its id), or "owner" for the master token. One screencast per tab, shared by all viewers.
# POST /api/browser/sessions/{id}/live-ticket     {"ticket", "expires_in": 30}: one use, 30 s, for this tab and this device only
# WS   /api/browser/sessions/{id}/live?ticket=…[&max_width=720&quality=55&fps=10]
#        (or the header "Authorization: Bearer <token>" instead of a ticket). The ticket never reaches a log line.
#        Closes: 4401 bad/used/expired ticket, 4404 no such tab (after {"type":"ended","reason":"no_tab"}), 4408 no message for 60 s,
#        4429 too many failed attempts, 1000 the tab closed (after {"type":"ended","reason":"tab_closed"}).
#   server -> phone
#     text {"type":"frame","seq","w","h","ts","url","title","bytes","css_w","css_h","secure"?} and RIGHT AFTER it one binary message: the JPEG
#          (`bytes` long, w x h pixels; css_w x css_h = the CSS size of the viewport it shows). Only the newest frame is sent: a slow
#          phone skips frames, never more than one is in flight. At most `fps` a second; a still page sends nothing new.
#          `secure` (new and optional; an app that does not know it ignores it): true while a secret field shows in the picture (a
#          password, a card number or a one-time code in the viewport, or focused); the phone's own frames are always true. The
#          browser checks the page at most once a second and keeps the answer between frames; until the first answer it is true.
#     text {"type":"status","id","controller":"user"|"agent","controlled_by","you_control","label","agent":{"busy","status","task_id",
#          "title","waiting_approval"},"viewers","page_busy"?,"stale"?}   on connect and whenever it changes; `label` is the header line
#          ("You are in control"…). Browser tabs only, new and optional (an app that does not know them ignores them):
#          `stale` true = the page's main thread does not answer, so the picture is an old one (no new frame for 2 s and the page
#          did not answer a probe in 3 s); `page_busy` true = `stale`, or the user's last input was not taken by the page in 3 s.
#          Both go back to false with the next frame.
#          Browser tabs only, new and optional: `pointer` {"x","y","down"} = where the agent's pointer was last (a new watcher draws it at once).
#     text {"type":"pointer","x","y","down"}   the agent's pointer (mouse, or a finger in the phone view), new and optional (an app that does
#          not know it ignores the message). x, y are CSS px of the viewport, the space of the frames' css_w x css_h (picture pixel =
#          x * w / css_w, y * h / css_h; no offsetTop shift). Sent while the agent moves, at most 20 a second (the newest position wins);
#          `down` true when a button or finger is pressed: a press and a release are always sent at once, so a tap is down true, then false.
#     text {"type":"pong","ts"} / {"type":"quality","max_width","quality","fps","every_nth_frame"} /
#          {"type":"input_ok","input": tap|…,"ok","x"?,"y"?,"page"?} (only for input sent with "ack": true)
#     text {"type":"error","code","message","for"}   codes: not_controller, no_frame, bad_event, held, browser, bad_message
#     text {"type":"ended","reason": tab_closed|no_ping|no_tab}
#   phone -> server (JSON text)
#     {"type":"ping"} at least every 30 s (any message counts; 60 s of silence ends the view)
#     {"type":"quality","max_width":720|1080,"quality":40..80,"fps":1..15}   (defaults 720 / 55 / 10; shared by everyone watching the tab)
#     {"type":"control","take":true|false}
#     input, only from the device that took control (else error not_controller); add "ack":true to get an input_ok back:
#       {"type":"tap","x","y"}  {"type":"long_press","x","y","ms"?:700}  {"type":"scroll","dx","dy","x"?,"y"?}
#       {"type":"text","text"} (typed as is, never logged)  {"type":"key","key":Enter|Backspace|Tab|Escape|ArrowUp|ArrowDown|ArrowLeft|
#       ArrowRight|Delete|Home|End|PageUp|PageDown}  {"type":"back"}  {"type":"forward"}  {"type":"reload"}
#       x, y, dx, dy are pixels of the last frame received (optional "frame_w","frame_h" when they refer to a frame of another size).
#       Mapping: css_x = x * css_w / w; css_y = y * css_h / h - offsetTop (kept inside the viewport); dx, dy scale the same way.
# GET  /api/browser/sessions/{id}/frame?after=<seq>&wait=10[&max_width&quality&fps]   the same frames without a WebSocket:
#        200 image/jpeg + X-Frame-Seq, X-Frame-W, X-Frame-H, X-Frame-Css-W, X-Frame-Css-H, X-Frame-Ts, X-Frame-Url, X-Frame-Title
#        (both %-encoded), X-Controller, X-You-Control; 204 when nothing newer came within `wait` (≤ 25 s); 404 no such tab.
#        Each request keeps this device watching for 60 s; DELETE .../frame stops at once.
# POST /api/browser/sessions/{id}/control {"take": bool}  {"id","controller":"user"|"agent","controlled_by","you_control"}
#        409 another device holds the tab; 404 no such tab. Control goes back to the agent when the device lets go, or 30 s after
#        its last live connection closed / its last request (input, frame, control). Handing back closes the purchase window.
# POST /api/browser/sessions/{id}/input  one input event as above -> {"ok", "type", "x"?, "y"?, "page"?: [x, y]}; 409 not the
#        controller (or no frame yet: open the live view first), 422 a bad event. A page that does not take the event within 3 s (or
#        is known to be blocked) answers at once HTTP 200 {"ok": false, "busy": true, "type", "note": "the page is not responding"}:
#        the event is dropped, never queued to land later on another screen; the user taps again when the picture moves.
#        The user's touches use touch id 2 (the agent's use 1).
#   While the user controls a tab, the agent's browser steps on it wait for it to be handed back (up to 5 minutes, then the agent is
#   told "the user is controlling the browser"); the user's own taps need no approval card. A step that is already running when the
#   user takes control is stopped at once (the agent is told "The user took over the browser; this step was stopped"), and what it
#   left half done in the page (a navigation that never answers, a finger still down) is cleaned up before the first input.
# JournalEntry: {"id", "date": "YYYY-MM-DD", "title", "summary", "created_at"}
# GET    /api/journal?month=YYYY-MM   {"month", "items": [JournalEntry] newest day first, "pending": [{"date", "status": pending|failed, "reason"}]}
# POST   /api/journal/rebuild?date=YYYY-MM-DD   {"date", "status": done|empty|pending|failed, "reason", "items"}; written with the
#          user's main model, whichever provider it is (pending = no model is set)
#   The agent reads the journal with the read-only tool journal_search (date, from/to, keyword).
# DELETE /api/journal/{id}   {"ok"}
# Learned preferences (reflection.py): each night at the quietest learned time (quiet_hours.py; 03:50 until a week of use is known;
#   profile timezone, after the journal), once the app has gone unused for 10 minutes (waits up to 3 h), the main model reads the
#   day's conversation, the corrections spotted in code and the rules so far, and proposes short preference rules (written as the
#   user's preference in the app language). A rule needs the owner's own words stating a lasting preference: its quote must be found
#   in their messages (denied approvals or other behaviour alone never make one), and "evidence" is that quote in quotation marks.
#   Accepted rules, and proposed ones with confidence >= 0.8, go into every turn's system prompt.
#   Nights without a new user message are skipped; invalid output changes nothing.
# GET    /api/reflection   {"enabled": bool, "rules": [{"id": int, "rule": text, "evidence": text, "date": "YYYY-MM-DD",
#          "confidence": 0..1, "status": proposed|accepted|rejected}] (accepted first, then by confidence),
#          "last_run": {"at": unix seconds, "status": done|skipped|failed|pending, "code": ""|no_messages|switched_off|in_use|
#                       no_model|model_error|bad_output, "added", "updated", "dropped", "goals", "held": counts, "changed": bool,
#                       "reason": free text for logs only (English, model notes; apps build their own sentence from the fields)}|null,
#          "guide": the rules in use as "- rule" lines (for app versions that still show a written guide)}
#          (404 = the agent's files are not enabled)
# PATCH  /api/reflection/rules/{id} {"status": proposed|accepted|rejected}  -> the rule; 404 unknown id; 422 bad status
# PUT    /api/reflection/enabled {"enabled": bool}  switch the nightly run on/off -> the same as GET
# POST   /api/reflection/run?force=false  run now with the main model -> the new last_run; skipped when no
#          new user message since the last run (force=true: the last day anyway); 409 while one is running
# (PUT /api/reflection/guide is gone: 410. The written guide of older versions became accepted rules at start-up.)
# Memory pass (memory_upkeep.py): right after the look-back each night (same switch, idle wait and main model), and after a chat
#   with 6+ new user messages has been quiet for 30 minutes (at most every 4 h), the model rewrites user.md / MEMORY.md (About the
#   user / Ongoing / Decided & done, at most ASSISTANT_MEMORY_BUDGET characters, default 3000) / AGENTS.md / people pages from what
#   happened since the last pass. The code keeps each file's previous version under .memory-passes/ (last 10), caps sizes, scrubs
#   secrets and drops MEMORY.md lines that repeat a rule in use. GET /api/reflection then also carries
#   "memory": {"last_pass": {"at", "status": "done", "code": "", "added": n, "tidied": n, "files": [path], "trigger": nightly|idle|
#              manual, "undone": bool, "can_undo": bool} | null (the last pass that changed something),
#              "last_attempt": {"at", "status": done|skipped|failed|pending, "code": ""|no_news|switched_off|in_use|no_model|
#              model_error|bad_output|no_room, "trigger"} | null}
# GET    /api/reflection/memory   the same "last_pass"/"last_attempt" plus "changes": [{"file", "added": [line], "removed": [line]}]
# POST   /api/reflection/memory/run?force=false   a pass now -> {"status", "code", "added", "tidied", "files", ...}; 409 while one runs
# POST   /api/reflection/memory/undo   the last pass's files back as before it (a file changed since is kept) and the inbox lines it
#          filed back in the inbox -> {"restored": [path], "kept": [path], "inbox_back": n, "last_pass", "last_attempt"}; 404 nothing
#          to undo; 409 while a pass runs
# The nightly review (nightly_review.py): right after the memory pass each night (same switch, idle wait and main model) Kelo writes
#   a short first-person look back at its own work to reviews/<YYYY-MM-DD>.md in its files (front matter: day, user_turns,
#   correction_turns, correction_free_rate, open_corrections), keeps WORKING_TOGETHER.md (lines not to cross, recent frictions, open
#   corrections; the previous version as WORKING_TOGETHER.prev.md) and keeps each correction the owner made open until it is fixed
#   (resolved_by review|owner|age). A correction needs a quote found in the owner's own words. Open ones go into every turn's system
#   prompt ("Recently corrected"). Nights without an owner message are skipped; invalid output writes nothing.
# GET    /api/reviews?limit=30   {"days": [{"day": "YYYY-MM-DD", "user_turns", "correction_turns", "correction_free_rate": 0..1|null,
#          "open_corrections", "doc_path"}] newest first, "last_run": {"at", "status": done|skipped|failed|pending, "code": ""|no_messages|
#          switched_off|in_use|no_model|model_error|bad_output, "added", "resolved", "dropped", "open", "changed", "reason"}|null}
# GET    /api/corrections?status=open   {"items": [{"id", "opened_at", "owner_quote", "what_went_wrong", "how_to_act",
#          "status": open|resolved|dropped, "resolved_at", "resolved_by": ""|review|owner|age, "last_seen_at"}]}  (status also resolved|dropped|all)
# POST   /api/corrections/{id}/resolve   the owner says it is fixed -> the item; 404 unknown id
# POST   /api/reviews/run   a review now (looks at the last day at least) -> the new last_run; 409 while one is running
# (all four: 404 when the agent's files are not enabled)
# Custom skills (custom_skills.py): skills written on this server, used only once the owner approves them in My → Records.
# GET    /api/skills   {"items": [{"id", "name", "description", "source": builtin|custom|installed, "state": ready|held, "author"}]}
# GET    /api/skills/proposals   {"items": [{"id", "skill", "name", "kind": new|change|edited, "description", "text", "diff": [{"op":
#          add|del|same|gap|file, "text"}], "reason", "origin": {"type": chat|job|draft|disk, "task_id"?, "worker_id"?},
#          "outside_text": bool, "created_at", "content_hash"? (edited), "secrets_removed": n}], "drafting": [{"task_id"?, "worker_id"?}],
#          "failed": [...]}
# POST   /api/skills/proposals/{id}/approve {"expected_hash"?}  -> {"ok", "skill"}; 404 unknown; 409 refused or changed again
# POST   /api/skills/proposals/{id}/discard  -> {"ok"}; 404 unknown (an edited one gets its approved text back)
# DELETE /api/skills/custom/{id}  -> {"ok"}; 404 none
# POST   /api/tasks/{id}/skill-draft | /api/workers/{id}/skill-draft  "save as skill" from a finished run -> 202 {"status":
#          "drafting"}; 409 still running or already drafting; 404 unknown; 503 no model. The draft lands in the proposals; a draft
#          that could not be written is listed for an hour in the proposals' "failed": [{"task_id"?, "worker_id"?, "reason", "at"}].
#   The agent's `remember` tool (memory_inbox.py) appends dated lines to memory/inbox.md; each pass files them and removes only the
#   lines it read, after its writes. Each turn's note (after the cached prefix) carries the newest inbox lines and up to 3 matching
#   sections of the people/memory pages and the journal (recall.py, SQLite FTS5); a section sent recently is named by path only.
# PUT    /api/agent/avatar   raw JPEG/PNG/WebP body ≤ 2 MB -> {"avatar_version", "mime", "bytes", "width": 512, "height": 512}; 413 / 422
# GET    /api/agent/avatar   the picture (image/jpeg, or image/png when it has transparency), ETag; If-None-Match -> 304; 404 = none
# DELETE /api/agent/avatar   {"ok", "avatar_version": 0}.  GET/PUT /api/profile also carry "avatar_version" (0 = no picture)
# Notification: {"id", "type": task_done|task_failed|approval_waiting|payment_auto|schedule_disabled|schedule_paused|watch_fired|
#   watch_disabled|proactive|briefing|browser_check|followup|cli_update|storage, "title", "body", "task_id", "approval_id", "schedule_id", "payment_id", "followup_id",
#   "created_at", "seen", "provider" ("" unless the notification is about one provider), "urgent" (a card of a task marked urgent at
#   setup: time matters, the phone shows it as urgent even in quiet hours)}
#   browser_check: an unattended run met a human check (title "A human check is needed"); open the task's tab in the live view to do it.
#   schedule_paused: a run paused its job (body: the reason). watch_fired: a watcher's condition held (title "A watched condition came true:
#   <name>", body: what it found; the agent's own message follows in the chat). watch_disabled: a watcher kept failing and turned off.
#   proactive: a message the agent sent unprompted (title: the agent's name, body: the message; task_id: its task).
#   cli_update: a newer claude / agy CLI has been out for CLI_UPDATE_NOTE_DAYS (title "A newer <CLI> is out", body: installed and
#   new version and the update command); once per new version; "provider": that CLI's provider. Opens My → Model at that CLI's box.
#   storage: storage is filling up (storage_watch.py): 80% / 95% of the cap (sent within the owner's waking hours) or the server
#   machine's disk critically low (sent at once); the body says what takes the most room. Opens My → Kelo server resources; the
#   phone notification's "Clean up" action opens the cleanup screen.
#   followup: a reminder about something waiting for the user (title: the agent's name, body: the message, also in the chat;
#   followup_id: the item, see FOLLOW-UPS API; task_id / approval_id when it has one).
# GET    /api/notifications?after=<id>&limit=50   {"items": [Notification] newest first, "unseen": n, "last_id": int}
# POST   /api/notifications/seen {"up_to": id}    {"ok", "marked", "unseen"}  (auto payments are marked seen in the wallet too)
#   task_* only for replies nobody watched to the end (automatic runs, background tasks, a chat the phone left); approval_waiting only
#   for unattended runs, marked seen once answered or expired.
#
# ---- PUSH API (Firebase Cloud Messaging; the operator's project, Android package chat.kelo.app; see push.py and docs/push.md) ---------
# Phone side, on its own server (device token required; the master token gets 403 on register):
# POST   /api/push/register {"token": "<FCM registration token>", "platform": "android"}   {"ok": true, "push": bool}
#          iPhone: {"token": "<APNs device token, hex>", "platform": "ios-apns", "environment": "production"|"development"}
#          (environment = the app's aps-environment; TestFlight and the App Store are production, an Xcode run is development).
#          one token per device: a newer one replaces the device's older one (and a token moved from another pairing leaves it).
#          Call it on every app start and from onNewToken. 422 {"detail"} for a bad token/platform. "push": false = this server has
#          no way to send pushes (no broker, no Firebase key); the token is kept for when it gets one.
# DELETE /api/push/register        {"ok": true, "removed": bool, "push": bool}   this device stops getting pushes
# GET    /api/push/status          {"enabled": bool, "registered": bool, "mode": local|broker|off, "broker_reachable"?: bool,
#          "broker_push"?: bool}   (the broker_* pair only in mode broker; asked of the broker at most every 5 minutes)
# DELETE /api/devices/{id} also drops that device's push token.
# A push is an FCM *data* message (no title/body; android priority HIGH) whose data holds only strings:
#   {"type": task_done|task_failed|approval_waiting|payment_auto|schedule_disabled|schedule_paused|watch_fired|watch_disabled|
#    proactive|followup, "id": "<notification id>", "task_id"?, "approval_id"?, "schedule_id"?}
#   The phone shows its own notification after GET /api/notifications?after=<id - 1> (or opens the task / approval by id).
#   Sent for every Notification above that nobody saw arrive: task_* and approval_waiting as described there, payment_auto only
#   when the reply was not being followed (asked within WATCHED_SECONDS), schedule_disabled, schedule_paused, watch_*, proactive and followup always.
# Operator side (any signed-in device):
# PUT    /api/operator/firebase   body: the service-account key file as downloaded (raw JSON; or {"service_account": <file>})
#          -> {"enabled": true, "project_id", "client_email"}; checked (type, project_id, client_email, private_key) and proven by
#          minting one OAuth token before it is saved encrypted. 422 {"detail"} never contains the key. The key is never returned.
# GET    /api/operator/firebase   {"enabled", "project_id", "client_email"} ("" when off)     DELETE -> the same, enabled false
# Broker side (public, for other copies; see push.py for the limits):
# POST   /broker/push/send {"install_id": "<copy's secret, 32-128 [A-Za-z0-9_-]>", "tokens": [≤10], "apns"?: [≤10 {"token", "environment"}],
#          "data": {as above}}
#          -> {"results": [{"ok": bool, "invalid": bool, "error"?: FCM error code}] in token order, "apns_results"?: [the same for apns,
#          error = Apple's reason; "DISABLED" where this broker has no APNs key]}; 404 push off on this server,
#          403 bad install_id, 422 bad body, 429 rate limit (Retry-After), 502 Google refused the operator's key.
# GET    /broker/push/status       {"push": bool, "apns": bool}
#
# ---- WORKERS API: background jobs the agent hands off (workers.py) ----------------------------------------------------------------
# The agent calls delegate(task, kind: browser|research|general, url?, files?, deadline_min?) and gets an answer at once; the job runs
# as turns of its own (source "worker", unattended: approvals come as a card + notification, payments only through confirm_purchase)
# in the tab "task:<id>", with up to ASSISTANT_WORKER_STEPS (200) steps / ASSISTANT_WORKER_MINUTES (60) for kind browser (research
# half, general less), at most ASSISTANT_WORKERS_MAX (3) running at once. It ends with report(status, summary, details?, keep_tab?);
# the report goes to the conversation as a hidden message (left out of GET /api/messages) answered by the main agent in a turn with
# source "handoff" (its notification: "Finished" / "Needs your help" / "Got stuck" / "It did not work out: <title>"; none when it replies NO_REPLY).
# Each run of a job and its handoff is a Task with "parent_id" (the task that called delegate) and "worker_id" (Task also carries both,
# null for ordinary replies), so the Activity screen can show them under that task. A chat turn has ASSISTANT_MAX_STEPS (30) steps.
# Worker: {"id": "w1a2b3", "kind", "title", "status": running|needs_input|blocked|waiting|done|failed|stopped|interrupted, "parent_task_id",
#   "task_id" (its latest run), "turn_id" (follow it with GET /api/turns/{id})|null, "tab": "task:<id>", "started_at", "updated_at",
#   "finished_at"|null, "steps", "max_steps", "deadline_at"|null (while running), "wake_at": float|null (when scheduled to retry; status shows as waiting),
#   "waiting_for": login|control|user|retry|null (paused only), "login": vault entry name|null, "reason": why it stopped (the report's summary, or the restart line)|null,
#   "status_text" (the last step's Korean words), "last_url", "thumbnail": "/api/browser/sessions/task:<id>/screenshot" (open or ended tab)|null}
# GET  /api/workers?limit=50&status=&parent_task_id=   {"items": [Worker] newest first, "running": n, "limit": n}
# GET  /api/workers/{id}       Worker + {"task", "url", "files", "report": {"status", "summary", "details", "keep_tab", "at"}|null,
#        "lines": [{"at", "text": "Opening a site · example.com"}] (last 40 steps), "segments", "input_tokens", "output_tokens", "error",
#        "keep_tab"}   404 = no such job
# POST /api/workers/{id}/resume {"note"?}   {"ok", "worker"}: goes on from where it stopped (same transcript; same tab, or a new one told
#        to go on from the history), with a fresh budget. 409 running, 429 too many running, 404.
# POST /api/workers/{id}/stop               {"ok", "worker"}  (404)
# POST /api/workers/{id}/message {"text"}   {"ok", "delivered": delivered|resumed|queued, "worker"}: a running job reads it before its
#        next step; one that is not running goes on with it.
# A paused job goes on by itself when the login it asked for (request_login) is saved (PUT /api/vault/{name}/login), or when the user
# hands its tab back after taking it over (live view). Its tab stays open while it runs or waits (up to a day) and, with keep_tab, an
# hour after it is done. A restart marks running jobs "interrupted" and resumes them when the server is back (ASSISTANT_WORKER_RESUME=0
# leaves them for the user).
#
# ---- WATCHERS API: cheap condition checks that wake the agent (watchers.py; full text in its docstring) ------------------------------
# Watcher: {"id", "name", "kind": page|script, "url", "mode": text_appears|text_disappears|number_above|number_below|changed|script,
#   "match", "selector", "regex", "threshold", "use_browser", "signed_in", "method": GET|POST, "body"|null, "headers": {name: value}|null,
#   "level": light|tab|page|null (the raised level in force; how it looks otherwise follows from the fields), "level_until"|null,
#   "code"|null, "note", "every_minutes" (≥ 5; ≥ 2 when ready to buy), "enabled", "once", "state":
#   off|watching|error|done, "description", "last_value", "last_checked_at", "next_check_at", "last_fired_at", "fire_count",
#   "error_count", "last_error", "disabled_reason", "dry_run": {"at", "ok", "satisfied", "value", "detail", "error", "url", "title",
#   "level": light|tab|page|null (how the dry run looked)}|null,
#   "can_enable": bool, "buy": {"armed", "tab", "last_kept_at", "signed_in", "problems", "remembered" (true when a plan exists)}|null,
#   "created_at", "updated_at", "created_by"}
# GET/POST /api/watchers, GET/PATCH/DELETE /api/watchers/{id}, POST /api/watchers/{id}/dry-run -> {"result", "watcher"},
# POST /api/watchers/{id}/enable {"enabled"} (409 without a fresh successful dry run),
# POST /api/watchers/{id}/buy-check (the ready-to-buy keep-alive now; 404 when not armed), GET /api/watchers/{id}/events.
# Starts off; fires on a change into its condition (the dry run is the starting point): notification watch_fired + a hidden handoff to
# the agent (left out of GET /api/messages), whose answer joins the chat. Server fetches: public https only, ≤ 3 checked redirects.
# Three ways to look, cheapest first: light (the server, no cookies, the browser's own identity), tab (a request inside the agent's
# browser with the owner's sign-ins; same origin), page (a full page load). signed_in / POST start at tab, use_browser at page; a
# blocked look (401/403/429/503, a bot check) is repeated one level up and the level that worked is kept for 24 h. A human check the
# browser cannot pass pauses the checks (twice, four times as late; not an error) and sends one watch_disabled notification a day
# ("<site> asks for a human check, so watching '<name>' is paused there..."). A hit is confirmed by a second look 30 s later (skipped
# when ready to buy; the buying run looks at the page itself).
# New optional fields on POST/PATCH: method (GET|POST), body (≤ 4 KB), headers (≤ 8, no Cookie/Authorization), signed_in. A POST whose
# address or body names an action (order, pay, book, cancel...) is 422: a watcher only reads.
#
# ---- BRIEFING API: the morning briefing (briefing.py; weather.py for the forecasts) --------------------------------------------------
# GET/PUT /api/briefing (on, time auto|fixed, offset, early open, sections, quiet days, weather provider; the weather follows the
# phone's last location), POST /api/briefing/location, POST/DELETE /api/briefing/keys/{kma|weatherapi},
# GET /api/briefing/preview, POST /api/briefing/run. Its message carries "briefing" (the card) in GET /api/messages; its notification
# has type "briefing" (title "Morning briefing", body the one-line summary). The agent's `weather` tool answers weather questions.
# ---- CHECKLIST API: CHECKLIST.md, part of the proactive messages (checklist.py) ---------------------------------------------------
# GET /api/checklist {"enabled" (= the proactive switch), "every_minutes" (= the proactive pace), "text", "items": n, "list": [item],
#   "path", "last_run": {"at", "status": done|quiet|failed|skipped, "reason", "task_id"}|null, "next_run_at", "running"};
#   PUT {"items"? [item] (replaces the list lines), "text"? (whole file)} ("enabled"/"every_minutes" accepted and ignored);
# POST /api/checklist/run (409 while running). Runs only while proactive messages are on, in waking hours, while today's daily_total
# has room. No items = no run and no model call; quiet (NO_REPLY) = no message, no notification; otherwise the answer joins the chat
# as "[Recurring check]", a task_done "Take a look: Recurring check" notification goes out (task source "checklist") and it counts
# as one 오늘 message (proactive log source "checklist").
#
# ---- PROACTIVE API: unprompted messages, off by default (proactive.py; NOTIFY_PREFS.md holds the user's rules) ---------------------
# GET/PUT /api/proactive {"enabled" (false), "wake_start" ("07:00"), "wake_end" ("22:00"), "every_minutes" (120; 30..1440),
#   "daily_regular" (2 보통 = regular a day), "daily_total" (3 보통 + 오늘 = today a day), "daily_now" (3 지금 = now a day), "timezone", "prefs_file", "today":
#   {"보통", "오늘", "지금"} (stored urgency values, kept as they are), "unanswered_limit" (3; 1..10), "pause_hours" (18; 1..168),
#   "pause_max_days" (7; 1..30), "last_pass" (with "reason_code", "backoff_level", "next_allowed_at"), "awake", "guard"}; GET /api/proactive/log?limit&before {"items": [{"id", "key", "source", "title",
#   "urgency", "status": sent|dropped, "reason", "message", "decided_at", "expires_at", "task_id"}], "next_before"};
# POST /api/proactive/run (409 when off or running). A delivered item is an assistant message in the chat, a Task (source
# "proactive") and a notification (type "proactive", title the agent's name, body the message).
#
# ---- FOLLOW-UPS API: reminders about what waits for the user (followups.py, full text in its docstring) ------------------------
# Items: login (request_login with no value yet), worker (a job in needs_input), approval (an unattended card unanswered),
# approval_expired (such a card ran out after UNATTENDED_APPROVAL_SECONDS: offers to try again), reply (the agent called await_reply).
# First follow-up 30 min after the stall (approval_expired: right when the card runs out), second 3 h after that, then on_hold; one
# more ~1 h before a known deadline; waking hours only (PUT /api/proactive wake_start/wake_end). Followup: {"id", "kind", "title",
# "detail", "stage": first|second|on_hold, "sent", "next_at", "began_at", "deadline", "deadline_sent", "snoozed_until", "suppressed",
# "last_sent_at", "message_id", "task_id", "worker_id", "approval_id", "login", "topic", "waiting_for", "origin": worker|schedule|
# background|null, "schedule_id", "status", "closed_why", "actions": [{"kind": open_login|approve|deny|resume|retry|snooze|drop,
# "name"|"approval_id"|"worker_id"|"followup_id", "minutes"? (snooze), "label"}]}   retry: {"kind": "retry", "followup_id", "label"}
# GET /api/followups?closed=false {"items"}; POST /api/followups/{id}/snooze {"minutes": 5..10080} {"ok", "item"}; POST
# /api/followups/{id}/drop {"ok", "item"} (404 unknown or closed); POST /api/followups/{id}/retry {"ok", "item", "how":
# worker_resumed|worker_queued|worker_delivered|schedule_started|background_started, "worker_id"?, "schedule_id"?, "task_id"?,
# "turn_id"?} (404 unknown or closed, 409 {"detail"} cannot be tried again; the step asks for approval again, payments too); GET/PUT /api/followups/settings {"enabled" (true), "first_delay_min"
# (30; 5..1440), "second_delay_min" (180; 5..2880), "wake_start", "wake_end", "timezone", "awake"}. Delivery: an assistant message in the
# chat (its id = the item's message_id, where the app shows the actions) + a notification of type "followup" (pushed).
#
# ---- STORAGE API: what this copy may use, early warnings and cleanup (quota.py, storage_watch.py, storage_cleanup.py) ------------
# GET    /api/resources  {"memory": {"used", "limit"}, "cpus", "disk": {"used" (the database included), "limit" | null, "parts":
#          {database|uploads|images|notes|history|other: bytes}, "free_on_host"}, "host": {"memory", "cpus", "disk_free", "disk_total"},
#          "profile", "minimum", "recommended", "alert": {"level": ok|warn (>= 80% of the cap)|critical (>= 95%), "percent" | null
#          (null without a cap), "host_low": bool (the server machine's disk is critically low), "top": the biggest part | null},
#          "can_clean": bool (this device may use the cleanup API)}
# GET    /api/storage/cleanup  (any signed-in device) {"groups": [{"id": uploads|images|browser_history|results|backups, "name", "bytes", "count",
#          "items": [{"id", "name", "bytes", "date", "keeps_text"? (uploads), "trashed"? (results), "kind"? (backups: update|memory|
#          migration)}]}], "total", "older_than_days"}. Notes, memory and the database are never listed.
# POST   /api/storage/cleanup {"items": [id], "confirm": true}  (any signed-in device) -> {"removed", "skipped", "freed", "groups", "total"}; 400 without
#          confirm or items. Only ids on the current list are removed; nothing is ever deleted without this call.
# A write that fails because the server machine's disk is full answers 507 {"detail": "<localized>", "code": "disk_full"}; a chat
# reply that hits it ends with {"type": "error", "message": "<the same words>", "code": "disk_full"}. Chat is never refused at the cap.
#
# ---- CONVERSATION COMPACTION (compaction.py, windows.py) ---------------------------------------------------------------------------
# Every model request is estimated (UTF-8 bytes / 3): system prompt + tool schemas + head + summary + history + this turn. Under
# `threshold` (0.75 of the model's context window; ASSISTANT_COMPACT_AT) everything is sent. At or above it the conversation part is
# cut to at most 10 % of the window: inside a turn the oldest tool results become one-line digests first; then the conversation's middle
# is folded into the rolling summary by the model of the turn (the first 4 messages since the last clear, capped at ~2k tokens, and the
# newest messages filling 5 % of the window, at least 2, stay word for word). A silent memory step (MEMORY.md / user.md / memory/ only) runs once before each compaction. After a chat turn a background
# fold runs with the main model when the next request would likely cross the threshold. A fold that fails keeps the old summary.
# A context-length error lowers that model's window (source "learned", kept in the database) and the turn is retried once after
# compacting under the lower window. Scheduled and handed-over runs read the summary and are compacted inline when they must be, but
# never start the after-turn fold; their saved answers join the conversation and are folded by the normal rules later.
# Context window: override (ASSISTANT_MODEL_CONTEXT="model=tokens,provider/model=tokens") > learned > provider (its /models list, cached
# when the model list is fetched) > table (providers/model_windows.json) > default 150,000.
# GET    /api/conversation/summary   {"summary", "up_to_message_id", "updated_at", "messages_folded", "compactions", "memory_flushes",
#          "last_memory_flush_at", "provider", "model", "context_window", "max_output", "source": override|learned|provider|table|default,
#          "threshold", "last_request_tokens" (estimate of the last request sent, any model), "last_error"}  (numbers for the main model;
#          provider/model/context_window/source null when no model is set)
# DELETE /api/conversation/summary   {"ok"}: forgets the summary (the messages stay; it is rebuilt when needed). DELETE /api/messages
#          also forgets it.
# DELETE /api/messages   clears the chat history and everything made from it (chat_clear.py lists what goes and what stays):
#          {"ok": true, "stopped": {"jobs": n, "turns": n}, "cleared": {"messages": n, "tasks": n, "workers": n, "approvals": n,
#          "notifications": n, "followups": n, "journal": n, ..., "browser_pictures": n, "browser_tabs": n}}. Background jobs are
#          stopped first (as POST /api/workers/{id}/stop does), then every running reply (a chat turn too: cancelled, as
#          DELETE /api/turns/{id} does; its waiting approvals are denied); then the tables (the reply log too), the server's memory of
#          turns (GET /api/turns/{id} and its WebSocket answer 404 after it) and the pictures of browser steps on disk. One clear runs at a time.

ARG_LABELS = {"site": i18n.N_("Site"), "url": i18n.N_("Address"), "host": i18n.N_("Site"), "label": i18n.N_("Name"), "name": i18n.N_("Name"), "to": i18n.N_("To"),
              "subject": i18n.N_("Subject"), "summary": i18n.N_("Content"), "amount": i18n.N_("Amount"), "currency": i18n.N_("Currency"), "files": i18n.N_("Files"),
              "packages": i18n.N_("Programs"), "text": i18n.N_("Text to type"), "selector": None, "task": i18n.N_("Task"), "prompt": i18n.N_("Task"),
              "service": i18n.N_("Service"), "card": i18n.N_("Card")}   # shown through t() in arg_lines


def _short(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(_short(x) for x in v[:5]) + (" …" if len(v) > 5 else "")
    if isinstance(v, dict):
        return ""
    t = str(v).strip()
    if t.startswith(("http://", "https://")):
        from urllib.parse import urlsplit
        t = urlsplit(t).hostname or t
    return t[:120]


def arg_lines(args: Any) -> list[dict[str, str]]:
    """A tool call's arguments as a few readable label/value pairs for an approval card (never raw JSON; unknown keys left out)."""
    out: list[dict[str, str]] = []
    if not isinstance(args, dict):
        return out
    seen: set[str] = set()
    for k, v in args.items():
        raw = ARG_LABELS.get(k)
        label = i18n.t(raw) if raw else None
        val = _short(v)
        if not label or not val or (label, val) in {(o["label"], o["value"]) for o in out} or label in seen and raw == ARG_LABELS["name"]:
            continue
        seen.add(label)
        out.append({"label": label, "value": val})
        if len(out) >= 4:
            break
    return out


def readable_args(args: Any) -> str:
    return " · ".join(f"{o['label']}: {o['value']}" for o in arg_lines(args))

def serialize(ev: Any) -> dict[str, Any]:
    match ev:
        case AgentThinking():
            return {"type": "thinking"}  # status only; the reasoning text is never sent
        case AssistantText():
            return {"type": "text", "text": ev.text}
        case ToolStarted():
            return {"type": "tool_started", "id": ev.id, "name": ev.name, "arguments": ev.arguments,
                    "label": tool_labels.label(ev.name), "hidden": tool_labels.hidden(ev.name), "group": tool_labels.group(ev.name),
                    "done": tool_labels.done(ev.name)}
        case ToolResult():
            return {"type": "tool_result", "id": ev.id, "name": ev.name, "output": ev.output,
                    "is_error": ev.is_error, "approved": ev.approved, "label": tool_labels.label(ev.name), "hidden": tool_labels.hidden(ev.name),
                    "group": tool_labels.group(ev.name), "done": tool_labels.done(ev.name)}
        case StepLimit():
            return {"type": "step_limit", "steps": ev.steps, "progress": ev.progress}
        case AgentDone():
            return {"type": "done", "input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens, "steps": ev.steps}
    raise TypeError(type(ev))


def sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def tool_catalog(tools: list[Tool]) -> str:
    """One line per tool shown now, then the names find_tools loads (agent/toolbox.py): what a model that went looking for its tool
    list should be told."""
    return _tool_catalog(tools)


def create_app(
    settings: Settings,
    *,
    tools: list[Tool] | None = None,
    stream_fn: Callable[..., AsyncIterator[Any]] = default_stream_chat,
    models_client: httpx.AsyncClient | None = None,
    device_post: PostForm | None = None,
    device_get: GetJson | None = None,
    mcp_fetcher: McpFetcher | None = None,
    mail_imap: Callable[..., Any] | None = None,
    mail_smtp: Callable[..., Any] | None = None,
    balance_transport: httpx.AsyncBaseTransport | None = None,
    balance_resolver: Callable[..., list] | None = None,
    browser: BrowserSession | None = None,
    with_schedule_tools: bool | None = None,
    with_worker_tools: bool | None = None,
    with_followup_tools: bool | None = None,
    with_goal_tools: bool | None = None,
    with_react_tool: bool | None = None,   # `react`: the emoji on the owner's message (reactions.py)
    with_tone_tool: bool | None = None,    # `set_tone`: how Kelo talks to the user, changed from the chat (profile "tone")
    with_schedule_check: bool | None = None,   # hand schedules the user saves to the agent (default: on unless tools are given)
    cloudflare_transport: httpx.AsyncBaseTransport | None = None,
    ddns_transport: httpx.AsyncBaseTransport | None = None,
    push_fetcher: McpFetcher | None = None,
    report_fetcher: McpFetcher | None = None,
    login_flows: LoginFlows | None = None,   # phone sign-in to subscription providers (tests pass fakes)
    updater_transport: httpx.AsyncBaseTransport | None = None,   # the updater container (tests pass a fake)
    app_releases: AppReleases | None = None,   # the record of released apps (app_releases.py; tests pass their own)
    weather_fetcher: McpFetcher | None = None,   # the weather services (weather.py; tests pass a fake)
    caldav_fetcher: McpFetcher | None = None,   # CalDAV calendars (calendars.py; tests pass a fake server)
    vpn_probe: Callable[..., Any] | None = None,   # the VPN connector's "is it connected" check and exit-address lookup (vpn.py; tests pass fakes)
    vpn_exit: Callable[..., Any] | None = None,
    channel_fetch: Callable[..., Any] | None = None,   # the messengers' transport (channels/; tests pass a fake Telegram)
    channel_connect: Callable[..., Any] | None = None,   # Discord's gateway and Slack's Socket Mode (websockets); tests pass a fake
    ha_connect: Callable[..., Any] | None = None,   # Home Assistant's websocket; tests pass a fake
    keyring_transport: httpx.AsyncBaseTransport | None = None,   # the key container (keyring_client.py; tests pass a fake)
    android_http: Callable[..., Any] | None = None,   # the Android bridge's HTTP client class (android/device.py; tests pass one with a fake transport)
    notices_fetcher: Callable[..., Awaitable[bytes]] | None = None,   # operator notices feed fetcher (tests pass fakes)
) -> FastAPI:
    """`browser`: a browser session to list and close tabs of when `tools` are given (tests); `with_schedule_tools`: add the
    schedule_* tools to the agent (default: only when the app builds its own tool list); `with_worker_tools`: the same for delegate and
    the tools that manage background jobs."""
    session: BrowserSession | None = None

    async def tester_resend_loop() -> None:
        """Tester sign-up notices whose mail failed are sent again: 2 minutes after the start (the vault is open by then), then
        every 6 hours. Errors are logged by the desk; the loop never stops the server."""
        await asyncio.sleep(120)
        while True:
            try:
                n = await tester_desk.retry_unsent()
                if n:
                    log.info("tester sign-ups: %d notice(s) sent again", n)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("tester sign-ups: resend failed: %s", type(e).__name__)
            await asyncio.sleep(6 * 3600)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("Kelo server %s", release_version.current().label)   # version.json (docs/versioning.md)
        if store.keyguard.locked:   # Keyguard (keyguard.py): no secret opens until a phone sends the key
            log.warning("Keyguard: the server is locked until a phone unlocks it")
            activity.notify("keyguard_locked", i18n.t("Kelo is locked"), i18n.t("Open Kelo on your phone to unlock it."), urgent=True)
        keyring_start = asyncio.create_task(keyring_up(store, keyring)) if keyring.enabled else None   # never holds the start up
        if settings.scheduler:
            scheduler.start()
        updater = ddns.start(settings.secret_key, transport=ddns_transport)   # keeps a DuckDNS name pointed here, only when asked for
        sweeper = asyncio.create_task(tab_sweeper()) if session is not None else None   # closes idle and finished tabs
        cli_watch = None if releases.disabled() else asyncio.create_task(cli_update_watch())   # a newer claude / agy CLI is out
        main_loop[:] = [asyncio.get_running_loop()]
        store.keyguard.after_unlock(warm_chat_session)   # the main model on agy: its chat session starts now, not with the first reply
        prime_cli_statuses()   # the claude / agy status My → Model shows: checked now, in the background
        # jobs and turns a restart cut off: marked now, resumed when they may be; while Keyguard is locked, only once a phone unlocked
        def resume_cut_off() -> None:
            workers.startup()   # jobs: marked interrupted, and resumed when they may be
            if settings.task_resume_on_start:
                resume_after_restart()   # turns: background ones go on, a chat turn gets a Continue button
        vpn_start = asyncio.create_task(vpn.start(store.keyguard))   # the route files from the vault (after the unlock when Keyguard is locked)
        tester_resend = asyncio.create_task(tester_resend_loop())   # kelo.chat/tester notices whose mail failed go out again
        notices_start = asyncio.create_task(notices_feed.refresh()) if notices_feed.enabled else None
        if channels is not None:   # the messengers (Telegram) read their bot; behind Keyguard only once a phone unlocked the server
            channels.bind()
            if store.keyguard.locked:
                store.keyguard.after_unlock(channels.start_all)
            else:
                channels.start_all()
        if store.keyguard.locked:
            store.keyguard.after_unlock(ha_events.restart)
        else:
            ha_events.restart()
        if store.keyguard.locked:
            keep, workers.resume_on_start = workers.resume_on_start, False
            workers.startup()   # marked interrupted now; resumed after the unlock
            workers.resume_on_start = keep
            store.keyguard.after_unlock(resume_cut_off)
            store.keyguard.after_unlock(scheduler.poke)   # background jobs that waited run at once if still due
        else:
            resume_cut_off()
        yield
        stopping[0] = True   # turns cut off from here on stay "interrupted" (looked at again on the next start)
        activity.interrupt_tasks([t.task_id for t in running_turns()])
        for t in running_turns():   # the log says what a restart would: "interrupted" is the last event; what the cut-off replies write later is not kept
            if t.log is not None:
                t.log.add(t.id, len(t.events), INTERRUPTED)
                t.log.finished(t.id, "interrupted")
                t.log = None
        await workers.shutdown()   # running jobs are marked interrupted (resumed on the next start), before their turns are cut off
        vpn_start.cancel()
        tester_resend.cancel()
        if notices_start:
            notices_start.cancel()
        if keyring_start:
            keyring_start.cancel()
        await keyring.aclose()
        await vpn.close()
        if sweeper:
            sweeper.cancel()
        if cli_watch:
            cli_watch.cancel()
        await cli_statuses.close()
        await app.state.close_models_http()
        if channels is not None:
            await channels.stop_all()
        await push.close()
        await live_activities.close()
        if updater:
            updater.cancel()
        await scheduler.stop()
        await watchers.stop()
        await follow_runner.stop()
        await ha_events.stop()
        if hub:
            await hub.close()
        if session:
            await session.close()
        turn_log.flush()   # the events queued since the last write

    app = FastAPI(title="assistant", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(LanguageMiddleware)
    # A minimum above every released app of its platform (ASSISTANT_MIN_APP_*, or version.json min_app in a misbuilt image) would lock
    # every phone out: it is logged as an error and not enforced, and /api/about lists the platform in min_app_ignored. Never fails start-up.
    enforcement = (app_releases or AppReleases.load()).enforceable(release_version.current().min_app, settings.min_app_version)
    report_ignored_minimums(enforcement, "ASSISTANT_MIN_APP_* / version.json min_app")
    settings.min_app_version = dict(enforcement.min_builds)
    settings.min_app_ignored = list(enforcement.ignored)
    app.add_middleware(VersionMiddleware, enforcement=enforcement)   # X-Kelo-Server on every answer; 426 for a too old app
    app.add_exception_handler(StarletteHTTPException, http_error)   # errors carry "code" at the top level too (api/errors.py)
    app.add_exception_handler(KeyGuardLocked, locked_error)          # a secret needed while Keyguard is locked: 423 (keyguard.py)
    appctx = AppContext(app=app, settings=settings)   # the services the routers in api/ are built from (api/context.py)
    broker = ApprovalBroker()
    store = Store(settings.db_path, settings.secret_key or None)
    notices_feed = NoticesFeed(
        notices_fetcher if notices_fetcher is not None else netguard_notices_fetcher,
        settings.notices_url,
        store,
    )
    appctx.add(notices_feed=notices_feed)
    app.state.notices_feed = notices_feed
    keyring = KeyringClient(settings.keyring_url, settings.keyring_token, ring_key_from(store.keyguard), transport=keyring_transport)
    appctx.add(keyring=keyring)   # AI-provider keys live there when it is on (docs/keyring.md)
    # every model call is counted (usage.py): by the kind of the turn running, or by the job that asked (counted(kind))
    # a provider whose usage is used up (model_limits.py): not called for background work until it resets; chat says when it can
    async def limit_windows(name: str) -> list[dict[str, Any]]:
        try:
            pname = get_provider(name).name
        except KeyError:
            return []
        return await cli_manage.cli_usage(pname) if pname in (cli_manage.CLAUDE, cli_manage.AGY) else []
    limit_guard = QuotaGuard(store, limit_windows, tz=lambda: store.profile()["timezone"])
    app.state.limit_guard = limit_guard
    if settings.review_mode:   # review.py: a subscription sign-in is never used here, whatever the profile or the environment names
        stream_fn = review.official_only(stream_fn, lambda p: unofficial_name(p if isinstance(p, str) else getattr(p, "name", "")) is not None)
    counted = usage_meter.metered(stream_fn, lambda r: store.add_usage_event(**r), current_turn.get, guard=limit_guard)
    stream_fn = counted()

    def background_model() -> tuple[str, str] | None:
        """The model for background jobs (usage.BACKGROUND_KINDS); None = they use the main model."""
        prof = store.profile()
        return (prof["background_provider"], prof["background_model"]) if prof["background_provider"] and prof["background_model"] else None

    def job_stream(kind: str) -> Callable[..., AsyncIterator[Any]]:
        """The stream function of a background job: on the background model when one is set (else, or when it fails, the main model)."""
        return usage_meter.background_stream(counted, kind, background_model, lambda p: key_args(p),
                                             lambda k, why: usage_meter.fallback_record(store, k, why, *(background_model() or ("", ""))))
    # Endpoints the user typed for custom / LM Studio / other address-less providers: kept in the database (meta), loaded
    # into the registry so every caller of resolve_base_url sees them. One server per process: start from this store only.
    clear_stored_base_urls()
    try:
        set_user_endpoints(json.loads(store.meta("custom_endpoints") or "[]"))
    except Exception:
        set_user_endpoints([])
    for _p in list_providers():
        if _p.takes_url and (_u := store.meta(f"base_url:{_p.name}")):
            set_stored_base_url(_p.name, _u)
    # endpoints that turned the thinking level away (providers/refused.py): kept in the database, this store's only
    refused_fields.on_change = None
    refused_fields.forget()
    try:
        refused_fields.restore(json.loads(store.meta("effort_refused") or "{}"))
    except ValueError:
        pass
    refused_fields.on_change = lambda seen: store.set_meta("effort_refused", json.dumps(seen))
    i18n.set_fallback(lambda: store.meta("ui_lang") or None)   # background jobs speak the owner's language (i18n.py: the rule)
    if not store.meta("ui_lang") and (lang := guess_language(store)):
        store.set_meta("ui_lang", lang)
        store.set_meta("ui_lang_from", "guess")   # a guess: the owner's first app request replaces it
    transient = Transient()   # logins typed "use once": in memory only
    wallet = Wallet(store)
    activity = Activity(store)        # task log, approval history and rules, notifications

    def entry_consent(req: dict[str, Any], decision: str) -> None:
        """Filling in (or closing) a fill-in card is the answer to the agent's request: written to the approval log like a card would be."""
        from .secret_requests import TOOL_OF
        tool = TOOL_OF.get(req["kind"], req["kind"])
        activity.log_approval(uuid.uuid4().hex, task_id=req.get("task_id"), tool=tool, label=tool_labels.label(tool),
                              site=(req.get("hosts") or [""])[0], detail=f"fill-in card in the chat: {req.get('label') or req.get('name')}",
                              decision=decision)

    # fill-in cards the agent asked for in the chat (secret_requests.py); an answer that comes after its reply ended goes on in a new one
    entries = SecretRequests(store, log_consent=entry_consent, on_answer=lambda req: entry_answered(req))
    app.state.secret_requests = entries
    goal_book: GoalBook   # set once the user's files are known (below)
    avatar = Avatar(store)
    schedules = Schedules(store)
    scheduler = Scheduler(schedules)   # its runner is set below, once turns can be started
    schedule_grants = ConditionalGrants(store, schedules, wallet)   # what a "when X, do Y" task may do on its own (asked once at setup)
    schedules.on_deleted = schedule_grants.drop_schedule
    app.state.schedule_grants = schedule_grants
    deliveries = CardDeliveries()   # end-to-end cards the phone sends for one approved payment: in memory only
    # payment PINs (payment_pins.py): only the phone's ciphertext is kept; the phone releases a PIN for one approved payment (memory only)
    payment_pins = PaymentPins(store)
    pin_deliveries = PinDeliveries()
    payment_ledger = PaymentLedger()        # the approved payment of each tab (what a payment-step card shows)
    payment_paths = PaymentPaths(store)     # per site, which way of paying went through without a protected keypad
    payment_steps = PaymentSteps()          # "approve in another app" / PIN keypad cards open now (payment_steps.py)
    pin_pads: dict[str, Any] = {}           # step id -> the protected keypad shown inside that card (tools/payment_step.PinPad)
    schedule_grants.pins = payment_pins
    app.state.payment_steps = payment_steps
    app.state.payment_pins = payment_pins
    appctx.add(payment_ledger=payment_ledger)   # turn_engine: a paid order whose receipt purchase_result never checked is said at the end
    app.state.pin_deliveries = pin_deliveries

    def payment_step_opened(ev: dict[str, Any]) -> None:
        """A payment-step card is time-critical: an urgent notification (it may sound in quiet hours) for when the app is not on
        screen; it opens the chat at the card. Never the code itself."""
        site = str(ev.get("site") or "")
        title = (i18n.t("Enter your payment PIN: {site}", site=site) if ev.get("kind") == "pin_pad"
                 else i18n.t("Approve the payment: {site}", site=site))
        activity.notify("approval_waiting", title, str(ev.get("instruction") or "")[:300], task_id=ev.get("task_id"),
                        approval_id=ev["step_id"], urgent=True)

    payment_steps.on_open = payment_step_opened
    payment_steps.on_close = lambda ev: activity.resolve_approval_notes(ev["step_id"])

    def pin_release_asked(req: dict[str, Any]) -> None:
        """A PIN release nobody asked on an approval card (an auto-pay payment, a task allowed to use the PIN): the phone must confirm it."""
        activity.notify("approval_waiting", i18n.t("Confirm to release your payment PIN: {site}", site=str(req.get("site") or "")),
                        i18n.t("Open Kelo and confirm with your fingerprint or face."), task_id=(current_turn.get() or {}).get("task_id"),
                        approval_id=req["slot_id"], urgent=True)

    pin_deliveries.on_request = pin_release_asked
    device_flows = DeviceFlows(store, env=dict(os.environ), **({"post_form": device_post} if device_post else {}),
                               **({"get_json": device_get} if device_get else {}), keyring=keyring)   # connections made with a code typed on the service's own page
    # skills and connectors as folders: built-in skills from server/content/skills, installed packages from <state>/packages, picked up
    # on the next read without a restart (packages.py)
    content = Path(settings.content_dir) if settings.content_dir else None
    _state = Path(settings.db_path).parent if settings.db_path != ":memory:" else (Path(settings.home_dir).parent if settings.home_dir else None)
    drafts_dir = settings.package_drafts_dir or (_state / "package-drafts" if _state else None)
    packages = Registry(builtin_skills=content / "skills" if content and (content / "skills").is_dir() else None,
                        installed=settings.packages_dir or (_state / "packages" if _state else None),
                        custom=settings.custom_skills_dir or (_state / "custom_skills" if _state else None),
                        drafts=drafts_dir)
    app.state.packages = packages
    # Kelo Nest (nest.py): packages installed from GitHub links, their permissions, and the screens they add to the apps
    nest = Nest(packages, store, data_dir=(_state / "nest-data") if _state else None, drafts_dir=drafts_dir, keyring=keyring)
    app.state.nest = nest
    # the assistant's skill proposals and "save as skill" drafts, waiting for the owner's approval (custom_skills.py)
    skill_proposals = SkillProposals(packages) if packages.custom_dir is not None else None
    app.state.skill_proposals = skill_proposals
    mcp_conn = McpConnections(store, relay=settings.oauth_relay, services=packages.mcp_service,
                              **({"fetcher": mcp_fetcher} if mcp_fetcher else {}))   # hosted MCP servers, signed in on their own page
    google_web = GoogleWeb(store, relay=settings.oauth_relay, broker=settings.oauth_broker,
                           **({"fetcher": mcp_fetcher} if mcp_fetcher else {}))   # Gmail with a sign-in button (secret only on the operator's server)
    mail = Mail(store, **({"imap": mail_imap} if mail_imap else {}), **({"smtp": mail_smtp} if mail_smtp else {}),
                renew=google_web.refresh)   # mailboxes with an app password or a Google sign-in
    gcal = GoogleCalendar(store, google_web, **({"fetcher": mcp_fetcher} if mcp_fetcher else {}))   # Google Calendar with the same sign-in
    phone_cals = PhoneCalendars(store)   # the phones' own calendars, as snapshots the app sends (phone_calendar.py)
    outlook_cal = MicrosoftCalendar(store)
    # every connected mailbox behind one interface: IMAP (app passwords, Gmail by Google sign-in), Outlook by Graph, Nest connectors
    # that provide mail (mailboxes.py); the email tools, the briefing, the heads-ups and sign-in codes all read through it
    mailboxes = Mailboxes(mail, GraphMail(store), NestMail(nest, store))
    app.state.mailboxes = mailboxes
    calendars = Calendars(store, mail, phone_cals, gcal, outlook_cal, **({"fetcher": caldav_fetcher} if caldav_fetcher else {}),
                          nest=NestCalendars(nest))
    sources = Sources(store, calendars, mailboxes)   # which connected calendars and mailboxes the briefing and heads-ups read (briefing_sources.py)
    app.state.sources = sources
    weather = Weather(store, language=lambda: store.meta("ui_lang") or i18n.current(),
                      **({"fetcher": weather_fetcher} if weather_fetcher else {}))   # forecasts without a browser (weather.py)
    app.state.weather = weather
    phone_data = PhoneData(store)        # contacts, texts, calls, notifications, health the phones share; tasks they do (phone_data.py)
    # the VPN / Proxy connectors: Kelo's web browsing through the owner's VPN (vpn.py). A route change waits until the agent's browser is quiet.
    vpn = VpnRoute(store, settings.route_dir, settings.vpn_dir,
                   browsing=lambda: session is not None and session.busy(),
                   paying=lambda: (session is not None and session.purchase_open()) or orders.open_now(),
                   **({"probe": vpn_probe} if vpn_probe else {}), **({"exit_lookup": vpn_exit} if vpn_exit else {}))
    keys = KeyConnections(store, registry=packages, vpn=vpn, **({"fetcher": mcp_fetcher} if mcp_fetcher else {}), keyring=keyring)
    for _old in [c["name"] for c in store.credentials() if c["kind"] == "onepassword"]:   # a token kept by the removed 1Password connector
        store.delete_credential(_old)
        store.add_vault_log(_old, "deleted", "the 1Password connector was removed from Kelo")
    store.set_meta("onepassword_state", "")
    github_web = GitHubWeb(store, device_flows, relay=settings.oauth_relay, broker=settings.oauth_broker,
                           **({"fetcher": mcp_fetcher} if mcp_fetcher else {}))   # GitHub with a sign-in button (secret only on the operator's server)
    broker_hits: dict[str, deque[float]] = {}   # keys and tokens made on the service, checked before saving
    kelo_names = KeloNames(store, transport=cloudflare_transport)   # <name>.kelo.chat addresses (only on the operator's server)
    app.state.kelo_names = kelo_names
    # the breaker tripped (kelo_names.PENDING_MAX names wait for their first connector): the operator hears once, with a push
    kelo_names.on_busy = lambda n, until: activity.notify(
        "operator_busy", i18n.t(KELO_BUSY_TITLE),
        i18n.t(KELO_BUSY_BODY, count=n, time=datetime.fromtimestamp(until, sched_zone(store.profile()["timezone"])).strftime("%H:%M")))
    # the pool of addresses is 90% used, or full: the operator hears once per fill, with a push
    def pool_notice(kind: str, used: int, cap: int) -> None:
        """kelo_names.on_pool: 50 and 70 % ("near50", "near70"), 90 % ("near") and full, each once per fill."""
        if kind == "full":
            title = i18n.t(KELO_FULL_TITLE, used=used, max=cap)
        elif kind == "near":
            title = i18n.t(KELO_NEAR_TITLE, used=used, max=cap)
        else:
            title = i18n.t(KELO_USED_TITLE, percent=kind.removeprefix("near"), used=used, max=cap)
        activity.notify("operator_pool", title, i18n.t(KELO_FULL_BODY if kind == "full" else KELO_NEAR_BODY, max=cap))
    kelo_names.on_pool = pool_notice
    fcm = Fcm(store, **({"fetcher": push_fetcher} if push_fetcher else {}))   # sends pushes, only where the Firebase key is (the operator)
    apns = Apns(**({"fetcher": push_fetcher} if push_fetcher else {}))        # iPhones: only where the APNs key file is set (KELO_APNS_*)
    push_broker = PushBroker(store, fcm, apns)                                 # /broker/push/send for the other copies
    push = PushClient(store, fcm, apns, broker_url=settings.push_broker, **({"fetcher": push_fetcher} if push_fetcher else {}))
    live_activities = LiveActivities(push.live)   # iPhone Live Activities (live_activity.py; backlog 64b)
    channels: ChannelManager | None = None   # built after the chat helpers below; a notification before that goes to the phones only

    def on_notify(n: dict[str, Any]) -> None:
        push.notify(n)
        if channels is not None:
            channels.notify(n)   # also to the owner's messenger, when they asked for that (channels/manager.py)
    activity.on_notify = on_notify   # every notification nobody saw arrive wakes this copy's phones (ids only)
    phone_data.on_task = push.wake     # a text, call or calendar change waiting for a phone wakes it (the task's id only)
    app.state.push, app.state.fcm, app.state.apns, app.state.push_broker = push, fcm, apns, push_broker
    report_box = ReportBox(store)                                             # reports other copies sent (only filled on the operator's server)
    report_box.on_add = lambda r: activity.notify(   # a new report: the operator hears at once (notification and push)
        "operator_report", i18n.t(REPORT_TITLE, reason=i18n.t(REPORT_REASONS.get(r.get("reason") or "", REPORT_REASONS["other"]))),
        i18n.t(REPORT_BODY))
    report_broker = ReportBroker(report_box)                                  # /broker/reports
    reports = ReportClient(store, report_broker, broker_url=settings.report_broker, public_url=settings.public_url,
                           **({"fetcher": report_fetcher} if report_fetcher else {}))
    app.state.reports, app.state.report_box = reports, report_box

    def beta_mailbox() -> dict[str, Any] | None:
        """The Gmail mailbox connected here, which the beta emails go out from (None: no email is sent)."""
        return next((a for a in mail.accounts() if a["provider"] == "gmail"), None)

    async def beta_mail(to: str, subject: str, body: str, reply_to: str) -> str:
        box = beta_mailbox()
        if box is None:
            raise MailError("no Gmail mailbox is connected")
        return await mail.send(box["name"], [to], subject, body, reply_to=reply_to)

    beta_desk = BetaDesk(store, notify=lambda title, body: activity.notify("beta_request", title, body), mailer=beta_mail,
                         mail_account=lambda: (beta_mailbox() or {}).get("address", ""))   # requests other copies sent (operator only)
    beta_broker = BetaBroker(beta_desk, report_broker)                        # /broker/beta (same install secret as reports)
    beta = BetaClient(store, beta_desk, broker_url=settings.oauth_broker, install_id=reports.install_id, is_operator=google_web.is_broker,
                      fetcher=google_web.fetcher)
    google_web.beta_open, google_web.beta_install = beta.approved, beta.install_for_exchange
    app.state.beta, app.state.beta_desk = beta, beta_desk
    tester_desk = TesterDesk(store, mailer=beta_mail, notify=lambda title, body: activity.notify("beta_request", title, body))   # kelo.chat/tester sign-ups, emailed to the operator (testers.py; backlog 86)
    app.state.tester_desk = tester_desk
    home = Home(settings.home_dir) if settings.home_dir else None
    deliverables = Deliverables(home) if home else None   # the user's library of results (files/), its trash and share links
    appctx.add(connector_data=ConnectorData(store, home))   # what came from a connector, counted and forgotten on the owner's word (connector_data.py)
    goal_book = GoalBook(store, home)   # each goal's facts, plan, numbers, log, documents; suggestions; quick replies (goals.py)
    comparisons = Comparisons(store, hidden_marks=(HANDOFF_MARK, WATCH_MARK, GOAL_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK))
    images = ImageStore(Path(settings.home_dir) / ".tryon") if settings.home_dir else None
    state_root = Path(settings.db_path).parent if settings.db_path != ":memory:" else (Path(settings.home_dir).parent if settings.home_dir else None)
    # KE2E (api/e2e_gate.py): the server key at <state>/e2e/server.key; /e2e and /e2e/ws are opened in the outermost layer
    e2e_server = E2EServer(load_e2e_key(state_root))
    app.add_middleware(E2EGateway, server=e2e_server)   # added last of the middlewares: it runs first, before language and version
    appctx.add(e2e_server=e2e_server, state_root=state_root)
    downloads = Downloads(settings.downloads_dir or (state_root / "downloads" if state_root else None))   # read only (downloads.py)
    quota = Quota(state_root, settings.disk_limit) if state_root else None
    for part in (home, images):
        if part is not None:
            part.quota = quota
    # what can be removed to free storage (storage_cleanup.py): the app's cleanup screen and the agent's two tools (it always asks)
    cleanup = Cleanup(home=home, images=images, results=deliverables, state_root=state_root, quota=quota) if state_root else None
    app.state.cleanup = cleanup
    skills = Skills(registry=packages, home=home) if packages.builtin_skills or packages.installed_dir or packages.custom_dir else None
    prompts = PromptBuilder.load(content, home, skills, settings.prompt_file) if content else None
    extract_upload = extractor_for(home) if home else None
    tools_given = tools is not None
    given_tool_names = frozenset(t.name for t in tools or [])
    tool_scopes = ToolScopes(store.meta, store.set_meta)   # the tools each conversation, schedule and job has loaded
    schedule_check_on = with_schedule_check if with_schedule_check is not None else not tools_given
    browser_more: BrowserExtras | None = None

    def grant_alive(scope: str) -> bool:
        """A task that holds a permission is still on: its chat reply runs, or its background job runs or waits for the user."""
        if scope.startswith("turn:"):
            t = turns.get(scope[5:])
            return t is not None and not t.done
        if scope.startswith("job:"):
            r = workers._row(scope[4:])
            return r is not None and r["status"] in ("running", *WORKER_PAUSED)
        return False

    def grant_used(g: Any, tool: str, kind: str, host: str) -> None:
        cur = current_turn.get() or {}
        activity.log_approval(uuid.uuid4().hex, task_id=cur.get("task_id"), tool=tool, label=tool_labels.label(tool), site=host,
                              detail=i18n.t(GRANT_COVERED, action=g.action, used=g.used, count=g.max_count), decision="allowed_by_rule",
                              unattended=bool(cur.get("unattended")))

    def schedule_grant_used(g: dict[str, Any], tool: str, host: str, detail: str) -> None:
        cur = current_turn.get() or {}
        activity.log_approval(uuid.uuid4().hex, task_id=cur.get("task_id"), tool=tool, label=tool_labels.label(tool), site=host,
                              detail=detail, decision="allowed_by_rule", unattended=bool(cur.get("unattended")))

    schedule_grants.on_use = schedule_grant_used
    task_grants = TaskGrants(alive=grant_alive)   # "do this N times on this site" for one task (tools/task_permission.py)
    task_grants.on_use = grant_used
    shipping = ShippingBook(store)   # the user's shipping addresses, next to the wallet's cards (shipping.py)
    schedule_grants.shipping = shipping
    orders = Orders(alive=grant_alive)   # the order the user asked for in chat: one card at the end (tools/order.py)
    shipping_forms = ShippingForms(shipping)   # the address form an order shows in the chat, waiting for the user

    def order_step_used(o: Any, tool: str, kind: str, host: str) -> None:
        cur = current_turn.get() or {}
        activity.log_approval(uuid.uuid4().hex, task_id=cur.get("task_id"), tool=tool, label=tool_labels.label(tool), site=host,
                              detail=i18n.t(ORDER_STEP_LOGGED, what=o.what), decision="allowed_by_rule",
                              unattended=bool(cur.get("unattended")))

    orders.on_use = order_step_used

    def request_step_used(tool: str, kind: str, host: str, request: str) -> None:
        """A click the user's own request named (apply, enter, sign up, send on the task's site; tools/order.py): no card was shown,
        so the approval log says what allowed it."""
        cur = current_turn.get() or {}
        activity.log_approval(uuid.uuid4().hex, task_id=cur.get("task_id"), tool=tool, label=tool_labels.label(tool), site=host,
                              detail=i18n.t(REQUEST_STEP_LOGGED, request=" ".join(str(request or "").split())[:120]),
                              decision="allowed_by_rule", unattended=bool(cur.get("unattended")))

    orders.on_request_use = request_step_used

    def wrong_charge_notice(title: str, body: str, task_id: int | None) -> None:
        """purchase_result found the charge differs from what was approved and cancelling is free: the owner is told right away."""
        activity.notify("purchase_mismatch", title[:120], body[:400], task_id=task_id, urgent=True)

    def cancel_click_used(host: str, words: str, order_no: str) -> None:
        """A cancel click let through without a card because the charge was wrong and cancelling was free (the approval log says so)."""
        cur = current_turn.get() or {}
        activity.log_approval(uuid.uuid4().hex, task_id=cur.get("task_id"), tool="browser_click", label=tool_labels.label("browser_click"),
                              site=host, detail=i18n.t("Cancel of a wrong charge: {words} (order {order})", words=words[:80], order=order_no or "-"),
                              decision="allowed_by_rule", unattended=bool(cur.get("unattended")))
    app.state.orders, app.state.shipping, app.state.shipping_forms = orders, shipping, shipping_forms
    app.state.task_grants = task_grants
    # notes the agent jots down mid-chat (memory_inbox.py), filed later by the memory pass
    memory_inbox = MemoryInbox(home, tz=lambda: store.profile()["timezone"]) if home else None
    reactions = Reactions(store)   # 👀 then one emoji on the owner's messages (reactions.py)

    def last_user_message_id() -> int | None:
        rows = store._all("SELECT id FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1")
        return int(rows[0]["id"]) if rows else None
    # pictures Kelo puts in the chat (chat_images.py): a screenshot of its browser tab the owner asked to see
    chat_images = ChatImages(store, state_root / "chat_images" if state_root else None)
    chat_attachments = ChatAttachments(store, home)   # files the user sent with a message, kept with it (chat_attachments.py)
    android_session: AndroidSession | None = None   # the phone app's screen per owner (android/tools.py); None = no device set up
    android_live: DeviceLive | None = None   # the device as a live source for the apps' phone card (android/live.py); None = no device set up
    android_client = DeviceClient(settings.android_url, settings.android_token, **({"http": android_http} if android_http else {}))
    appctx.add(android_client=android_client)   # api/android_device.py reads its status (GET /api/android)
    if tools is None:
        tools = []
        if settings.cdp_host:
            session = BrowserSession(settings.cdp_host, settings.cdp_port, allow_private=settings.allow_private_browsing)
            session.order_dialog = orders.dialog_ok
            session.on_cancel_click = cancel_click_used
            tools = [*tools, *browser_tools(session, approve_actions=settings.approve_browser_actions, grants=task_grants, orders=orders),
                     *point_tools(session, approve_actions=settings.approve_browser_actions, grants=task_grants, orders=orders),
                     *purchase_tools(session, wallet, deliveries, grants=schedule_grants, orders=orders, pins=payment_pins,
                                     pin_deliveries=pin_deliveries, ledger=payment_ledger, paths=payment_paths,
                                     notify=wrong_charge_notice, timezone=lambda: store.profile().get("timezone") or ""),
                     *payment_step_tools(session, payment_steps, payment_ledger, pins=payment_pins, pin_deliveries=pin_deliveries,
                                         paths=payment_paths, orders=orders, vault_locked=store.vault_locked, pads=pin_pads),
                     *order_tools(orders, session, shipping, shipping_forms, grants=schedule_grants,
                                  timezone=lambda: store.profile().get("timezone") or "", wallet=wallet, paths=payment_paths),
                     *task_permission_tools(task_grants, session.current_host, lambda: (current_turn.get() or {}).get("task_id"),
                                            owner_request=lambda: orders.request() if orders.request else "")]
        if settings.android_device and android_client.enabled:
            android_session = AndroidSession(android_client, ScreenReader())
            android_live = DeviceLive(android_client)
            tools = [*tools, *android_tools(android_client, grants=task_grants, orders=orders, approve_actions=settings.approve_browser_actions,
                                    session=android_session, mirror=AndroidMirror(android_client, session) if session is not None else None,
                                    card=DeviceCard(android_client), hold=android_live.hold_for_user)]   # a phone app on the device the owner set up (loaded on demand)
            if chat_images.root is not None:   # the phone's screen as a picture in the chat (password fields painted over)
                tools = [*tools, chat_images.tool(phone_grab(android_client, ScreenReader()), current_turn.get, name="android_send_screenshot",
                                                  description="Show the user a screenshot of the phone's screen in the chat (when they ask "
                                                  "to see it, or a picture helps). Password fields are covered.")]
        tools = [*tools, *web_tools(language=lambda: settings.browser_lang or i18n.current(), proxy=vpn.proxy_url),   # search + page text, no browser
                 *card_request_tools(), *request_pin_tools(payment_pins), *connect_tools(store, home, **({'fetcher': mcp_fetcher} if mcp_fetcher else {}), keyring=keyring), *channel_tools(lambda: channels), *vault_tools(store, session, transient, on_request=lambda n, lb, h, d: followups.login_requested(
                     n, lb, h, d, task=current_turn.get(), worker=current_worker.get()), grants=schedule_grants, orders=orders,
                                                                                                     android=android_session),
                 *oauth_tools(lambda: connect_ways(store, mail, browser=session is not None, google_open=google_web.open_here(), registry=packages, relay=settings.oauth_relay, vpn=vpn), device_flows, mail, packages), *mail_tools(mailboxes),
                 *calendar_tools(gcal), *phone_calendar_tools(phone_cals, lambda: store.profile().get("timezone")),
                 *phone_calendar_write_tools(phone_data, phone_cals, lambda: store.profile().get("timezone")), sources.tool(), sources.calendar_tool(lambda: store.profile().get("timezone")),
                 *phone_tools(phone_data, lambda: store.profile().get("timezone")), weather.tool(), weather.location_tool(), places_tool(weather)]
        if session is not None:   # downloads, uploads, PDFs, private tabs, sign-in states, codes from mail, run pictures (browser_more.py)
            browser_more = BrowserExtras(
                session, home=home, files=deliverables, mail=mailboxes, locale=settings.browser_lang, download_dir=settings.browser_download_dir,
                archive=ActionArchive(state_root / "browser-history") if state_root else None, timezone=lambda: store.profile()["timezone"],
                notify=lambda title, body, task_id, push: activity.notify("browser_check", title, body, task_id=task_id, push=push),
                auto_codes=store.mail_codes_auto, vault_log=store.add_vault_log)
            tools = [*tools, *browser_more.tools(), chat_images.tool(browser_grab(session), current_turn.get)]   # a screenshot into the chat
        if deliverables is not None and chat_images.root is not None:
            # a picture from the library, a saved product or generated photo (images: same home_dir condition), or a web address
            tools = [*tools, chat_images.library_tool(deliverables, current_turn.get, images=images, fetcher=netguard_fetch)]
        if images:
            tools = [*tools, *image_tools(images, creds=store, keyring=keyring)]
        if cleanup is not None:
            tools = [*tools, *cleanup_tools(cleanup)]
        tools = [*tools, *nest.tools(), *nest.draft_tools(current_turn=current_turn.get, emit_fn=emit_ui)]   # the entries installed packages keep on this server (loaded on demand)
        if home:
            tools = [*tools, *home_tools(home, skills, extract_upload, catalog=lambda: tool_catalog(tools)), extract_tool(home)]
            if memory_inbox is not None:
                tools = [*tools, *inbox_tools(memory_inbox, last_user_message_id)]
            if skill_proposals is not None:   # loaded on demand: a proposal waits for the owner's approval (custom_skills.py)
                tools = [*tools, *skill_tools(skill_proposals, lambda: (
                    {"type": "job", "worker_id": w} if (w := current_worker.get()) else
                    {"type": "chat", "task_id": (current_turn.get() or {}).get("task_id")}))]
            if settings.workroom_dir:   # code, documents and media without internet (workroom.py); results go to the library
                room = Workroom(home, SocketRunner(workroom_agent_channel(settings.workroom_dir)), files=deliverables,
                                proxy=InstallProxy(workroom_proxy_channel(settings.workroom_dir)))
                tools = [*tools, *workroom_tools(room, share=lambda n: (lambda s: {**s, "url": share_url(settings.public_url or "", s["token"])})(
                    deliverables.share(n)))]
    # watchers (watchers.py): cheap page / script checks that wake the agent only when their condition holds
    try:
        watch_room = room   # the work room, when this server has one (script watchers run there)
    except NameError:
        watch_room = None

    async def watch_browser(url: str, key: str) -> WatchPage:
        """A page that needs JavaScript, as the agent's browser shows it, in a tab of its own that is closed right after. The status is the
        main document's real one, and the bot-check reading the browser already has comes with it (a check that clears by itself, like
        Cloudflare's "just a moment", gets a few seconds before it counts)."""
        assert session is not None
        token = browser_owner.set(key)
        try:
            try:
                await session.open(url)
            except BrowserPolicyError as e:
                raise WatchError(str(e)) from None
            status, headers, found = await session.page_state()
            for _ in range(3):
                if found is None or found.kind != "interstitial":
                    break
                await asyncio.sleep(3)
                status, headers, found = await session.page_state()
            page = session.raw_page(key)
            html = await page.content() if page is not None else ""
            final = str(getattr(page, "url", "") or url)
        finally:
            browser_owner.reset(token)
            await session.close_tab(key)
        return WatchPage(final, status or 200, "text/html", html, headers, found)

    async def watch_tab(url: str, method: str, headers: dict[str, str], body: str | None, key: str) -> WatchPage:
        """The same request as a plain read, made inside a background tab of the site in the agent's browser (its sign-ins, its route)."""
        assert session is not None
        try:
            got = await session.site_fetch(url, method=method, headers=headers, body=body, key=key)
        except BrowserPolicyError as e:
            raise WatchError(str(e)) from None
        return WatchPage(got["url"], got["status"], got["content_type"], got["text"], got["headers"])

    async def watch_browser_version() -> str:
        assert session is not None
        return await session.chrome_version()

    async def watch_script(code: str, files: list[dict[str, str]]) -> dict[str, Any]:
        assert watch_room is not None
        async with watch_room.lock:
            return await watch_room.runner.call("/run", {"language": "python", "code": code, "files": files, "timeout_s": 30}, 90)

    watchers = Watchers(store, browser_fetch=watch_browser if session is not None else None, tab_fetch=watch_tab if session is not None else None,
                        script_runner=watch_script if watch_room is not None else None,
                        browser_version=watch_browser_version if session is not None else None,
                        language=lambda: settings.browser_lang or i18n.current(), proxy=vpn.proxy_url)
    app.state.watchers = watchers
    ha_events = HaEvents(store, connect=ha_connect)   # Home Assistant live watches (backlog 62); tests pass a fake socket
    app.state.ha_events = ha_events
    if with_schedule_tools or (with_schedule_tools is None and not tools_given):
        tools = [*tools, *schedule_tools(schedules, scheduler), *task_allow_tools(schedule_grants, wallet), *watch_tools(watchers), *ha_watch_tools(ha_events)]
    # the journal (written each night with the main model) and the agent's read-only search of it
    journal = Journal(store, job_stream("journal"), lambda: journal_model(), lambda p: key_args(p))
    # the nightly run that learns the user's preferences (reflection.py), also with the main model
    reflection = (Reflection(store, home, activity, job_stream("reflection"), lambda: journal_model(), lambda p: key_args(p), last_use=lambda: last_use[0])
                  if home else None)
    if prompts and reflection:
        prompts.learned = reflection.prompt_section
    if reflection:
        reflection.goals = goal_book   # the nightly run may also suggest goals
    # the memory pass (memory_upkeep.py): keeps user.md / MEMORY.md / AGENTS.md / people pages current, with the look-back's switch,
    # its model and its rules (a rule in use is not repeated in MEMORY.md)
    memory_upkeep = (MemoryUpkeep(store, home, activity, job_stream("memory"), lambda: journal_model(), lambda p: key_args(p),
                                  enabled=reflection.enabled, rules=lambda: [r["rule"] for r in reflection.in_use()],
                                  budget=settings.memory_budget, last_use=lambda: last_use[0], inbox=memory_inbox)
                     if home and reflection else None)
    # the nightly review of Kelo's own work (nightly_review.py): corrections kept open until fixed, reviews/, WORKING_TOGETHER.md
    nightly_review = (NightlyReview(store, home, activity, job_stream("reflection"), lambda: journal_model(), lambda p: key_args(p),
                                    last_use=lambda: last_use[0], reflection=reflection) if home and reflection else None)
    if prompts and nightly_review:
        prompts.corrections = nightly_review.prompt_section
    # the few sections of the people pages, memory pages and journal that match each message, in the turn's note (recall.py)
    turn_recall = TurnRecall(Recall(home, store), store, memory_inbox) if home else None

    def memory_note(tail: list[dict[str, Any]], message_id: int | None) -> str:
        if turn_recall is None:
            return ""
        last = next((m for m in reversed(tail) if m.get("role") == "user"), None)
        content = last.get("content") if last else ""
        text = content if isinstance(content, str) else " ".join(str(p.get("text") or "") for p in content or [] if isinstance(p, dict))
        try:
            return turn_recall.block(TIME_TAG.sub("", text)[:2000], message_id)
        except Exception:  # noqa: BLE001 - a turn never fails over its notes
            log.exception("could not look up notes for the turn")
            return ""
    def jobs_note() -> str:
        """Background jobs' state now (workers.prompt_block): a stopped job is never reported as still going."""
        try:
            return workers.prompt_block()
        except Exception:  # noqa: BLE001 - a turn never fails over this line
            log.exception("could not list the background jobs for the turn")
            return ""
    goal_book.hidden_marks = (GOAL_MARK, HANDOFF_MARK, WATCH_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK)
    if not tools_given:
        tools = [*tools, *journal_tools(journal)]
    if session is None:
        session = browser if browser is not None else None
    hub = LiveHub(session) if session is not None else None   # live view of a tab, and the user taking it over
    live_sources = LiveSources(hub, android_live)   # watch and take over: the browser's tabs, and the device under the key "android"
    if session is not None:
        session.remote_dns = vpn.active               # with the VPN on, names are resolved by the VPN, not here
        session.vpn_note = vpn.down_note              # a page that cannot load because the VPN is down says so
        for _t in tools:   # every step that drives the browser waits while the user holds the tab (see BrowserSession.hold_for_user)
            if (_t.name.startswith("browser_") or _t.name == "confirm_purchase") and _t.hold is None:
                _t.hold = session.hold_for_user
        if browser_more is not None:
            browser_more.wrap(tools)   # a picture of each browser step of background jobs and automatic runs

    def connected_ids() -> set[str]:
        """Connectors this app can use right now: the browser, and every service the user connected with a key."""
        ids: set[str] = set()
        if any(t.name.startswith("browser_") for t in tools):
            ids.add("browser")
        browsing = "browser" in ids
        ids |= {cid for cid, w in connect_ways(store, mail, browser=browsing, google_open=google_web.open_here(), registry=packages, vpn=vpn).items()
                if (w.get("connected") and cid not in NETWORK_IDS) or (browsing and w.get("connect") == "browser")}   # the VPN is not a service for the model
        return ids

    # what the owner lets Kelo do with each connector (permissions.py); the gate runs before every tool call (agent/loop.py)
    ways_seen: list[Any] = [0.0, {}]

    def ways_table() -> dict[str, dict[str, Any]]:
        """The connect table (connect_ways.ways), kept for a few seconds: the gate asks for it on every step."""
        if time.monotonic() - ways_seen[0] > 5:
            ways_seen[:] = [time.monotonic(), connect_ways(store, mail, browser=any(t.name.startswith("browser_") for t in tools),
                                                          google_open=google_web.open_here(), registry=packages, vpn=vpn)]
        return ways_seen[1]

    def connector_name(cid: str) -> str:
        if cid == "phone":
            return i18n.t(PHONE_NAME)
        found = next((s for s in packages.services() if s.id == cid), None)
        return (i18n.t(found.name) if found is not None and found.regions else found.name) if found is not None else cid

    permissions = Permissions(
        store, tools=lambda: tools, names=connector_name, accounts=lambda: mailboxes.accounts(),
        aliases=lambda item: next((p.id for p in packages.installed("connector") if p.item == item), None),
        connected=lambda: connected_by_family(ways_table(), mailboxes.accounts()))
    leak_marker = LeakMarker(store)   # a private token in the model's private context that must never leave (injection_watch.py)
    injection_log = InjectionLog(store, redact=leak_marker.hide)
    injection_watch = InjectionWatch(
        InjectionDetector(), injection_log, lambda kind, title, body: activity.notify(kind, title, body),
        run_id=lambda: str((current_turn.get() or {}).get("task_id") or (current_turn.get() or {}).get("turn_id") or ""),
        origin_hint=lambda: session.current_host() if session is not None else "", tz=lambda: store.profile().get("timezone"))
    if prompts is not None:
        prompts.marker = leak_marker.line
    privacy = PrivacyGuard(home=home, store=store, wallet=wallet, shipping=shipping,
                           own_addresses=lambda: [a.get("address") for a in mailboxes.accounts()],   # the check before anything leaves (privacy_guard.py)
                           marker=leak_marker, on_leak=injection_watch.leak)
    nest.privacy_guard = privacy

    def outside_now() -> bool:
        """Outside text has reached this run (a fenced tool result), or the turn is a group's (what others wrote is outside text)."""
        seen = outside_seen.get()
        return bool(seen and seen[0]) or str((current_turn.get() or {}).get("source") or "").startswith("group:")

    def owner_recent_words() -> list[str]:
        """The owner's own chat messages of the last RECENT_WORDS_SECONDS (at most 6, before this turn's): places named there count
        as named for the outside-text rule ("쓰레드에 올려" … "너가")."""
        hidden = (HANDOFF_MARK, WATCH_MARK, GOAL_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK)
        since = time.time() - RECENT_WORDS_SECONDS
        rows = [m for m in store.messages(limit=14) if m.get("role") == "user" and float(m.get("created_at") or 0) >= since
                and isinstance(m.get("content"), str) and not m["content"].startswith(hidden)]
        return [m["content"] for m in rows][-6:]

    outside_review = OutsideReview(   # a send, change or delete after outside text asks unless the owner named the target (outside_rule.py)
        OutsideTextRule(is_own=privacy.is_own), OwnerWords(PeopleBook(home), granted=schedule_grants.covered_targets, recent=owner_recent_words),
        seen=outside_now, turn=lambda: current_turn.get() or {}, ordered_site=lambda: (o.site if (o := orders.current()) else ""))

    async def browser_step(tool: str, args: dict[str, Any]) -> tuple[str, str]:
        """(commit kind, site) of a browser click or typing step, for the privacy guard; ("", "") without a browser. A phone app's
        step (android_tap, android_type) is judged by the screen last read, the app's package standing for the site."""
        if tool.startswith("android_"):
            return await android_session.judge_ahead(tool, args) if android_session is not None else ("", "")
        if session is None:
            return "", ""
        return await session.judge_ahead(tool, args), session.current_host()

    def browser_page(tool: str, args: dict[str, Any]) -> tuple[str, str]:
        """(the page's path with the query keys that name its subject, the clicked element's words) of a browser step, for
        done_once.py's fingerprint (page_key); for a phone app's step: (package/activity, the item's words)."""
        if tool.startswith("android_"):
            return android_session.page(tool, args) if android_session is not None else ("", "")
        if session is None:
            return "", ""
        url, label = session.note_allowed(str(args.get("selector") or ""))
        return page_key(url), label

    action_ledger = ActionLedger(store)   # what Kelo sent, changed or deleted: a step is done once (done_once.py)
    done_once = DoneOnce(action_ledger, effect=permissions.effect, turn=current_turn.get, tz=lambda: store.profile().get("timezone"),
                         browser_step=browser_step, browser_page=browser_page,
                         on_unresolved=lambda info: followups.unknown_result(info))   # (followups: made below)
    permission_gate = Gate(permissions, privacy, browser_step=browser_step, apply_covers=schedule_grants.apply_covers,
                           outside=outside_review, on_stopped=injection_watch.step_stopped, once=done_once)
    appctx.add(permissions=permissions, permission_gate=permission_gate, ways_table=ways_table, vpn=vpn,
               injection_watch=injection_watch, injection_log=injection_log, done_once=done_once, action_ledger=action_ledger)
    app.state.permissions = permissions
    app.state.done_once = done_once

    def calendar_read_allowed(a: Any) -> bool:
        """The chat's calendar_events skips a calendar the owner turned reading off for (Connectors → <name> → What Kelo may do)."""
        cid = {"phone": "phone", "google": "google_calendar", "outlook": "outlook_calendar", "caldav": "caldav_calendar",
               "nest": a.item}.get(a.provider, "imap_email")
        action = {"phone": "phone_calendar_events", "google_calendar": "gcal_events"}.get(cid, "calendar_events")
        return permissions.mode_of(cid, "read", action) != "off"
    sources.owner_allows = calendar_read_allowed

    quiet = QuietHours(store, lambda: store.profile().get("timezone"),
                       kind_of=lambda d: day_type(date.fromisoformat(d), country_for(store)))   # weekday / weekend nights apart
    last_use = [0.0]   # when someone last used the app in the foreground (background polls for notifications/push don't count)

    def note_use(request: Request) -> None:
        # the owner's language (background jobs speak it; a chat turn uses its own request's) is the one chosen in the app (PUT
        # /api/language). Until one is chosen it follows the language of the owner's signed-in apps (their Accept-Language: the
        # phone's or computer's language), so changing the phone's language changes it; the master token (scripts, the owner's own
        # tools) only fills it while nothing was seen yet (empty, or the setup's guess)
        source = store.meta("ui_lang_from") or ("" if not store.meta("ui_lang") else "app")
        follows = ("", "guess", "app") if getattr(request.state, "device_id", None) is not None else ("", "guess")
        if source in follows and (lang := i18n.pick(request.headers.get("accept-language"))) and \
                (lang != store.meta("ui_lang") or source != "app"):
            store.set_meta("ui_lang", lang)
            store.set_meta("ui_lang_from", "app")
        path = request.url.path
        if not (path.startswith("/api/notifications") or path.startswith("/api/push") or path.startswith("/api/wallet/notifications")):
            prev_use, last_use[0] = last_use[0], time.time()
            warm_on_open()
            quiet.record(last_use[0])     # learns when the app is usually left alone at night (quiet_hours.py)
            if (b := getattr(app.state, "briefing", None)) is not None:
                if not store.keyguard.locked:   # locked (Keyguard): the briefing waits for the unlock like every background job
                    b.app_opened(last_use[0], prev_use)   # the morning's first open may bring the briefing early (briefing.py)

    auth = AuthGuard(settings.token, store, note_use, review_mode=settings.review_mode)   # api/auth.py: tokens, the brute-force guard, and where KE2E's check plugs in
    client_key, failures, device_of = auth.client_key, auth.failures, auth.device_of
    require_auth, require_owner, require_master = auth.require_auth, auth.require_owner, auth.require_master
    auth_failed, auth_blocked = auth.auth_failed, auth.auth_blocked

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    def resolve_model(body_provider: str | None, body_model: str | None) -> tuple[str, str]:
        prof = store.profile()
        provider = body_provider or prof["provider"] or settings.provider
        model = body_model or prof["model"] or settings.model
        if not provider or not model:
            # the app shows its own words for this code; other clients get the message in the user's language
            raise HTTPException(400, {"code": "model_required", "message": i18n.t(MODEL_REQUIRED)})
        return provider, model

    def fallback_model() -> tuple[str, str] | None:
        prof = store.profile()
        if prof["fallback_provider"] and prof["fallback_model"]:
            return prof["fallback_provider"], prof["fallback_model"]
        return None

    def provider_key(name: str) -> str:
        """A provider under any of its names, so two spellings of one provider compare equal."""
        try:
            return get_provider(name).name
        except KeyError:
            return name

    def provider_identity(name: str) -> str:
        """Which service a provider really is, for the rule that the main and the fallback model come from different ones: a custom
        endpoint ("custom", "custom-<slug>") is its host ("custom@<host>"), so two endpoints on different hosts are different
        services and two on one host are the same. Everything else is its provider_key. Only that rule uses it (window sizes, usage
        and sign-in checks keep provider_key)."""
        pk = provider_key(name)
        if pk == "custom" or pk.startswith(USER_ENDPOINT_PREFIX):
            url = stored_base_url(pk)
            return f"custom@{(urlsplit(url).hostname or '').lower() if url else ''}"
        return pk

    policy = ProviderPolicy(store, settings, provider_key)   # unofficial sign-ins and their consent (api/provider_policy.py)
    unofficial_name, unofficial_in_use, unofficial_shown = policy.unofficial_name, policy.unofficial_in_use, policy.unofficial_shown
    consent_of = policy.consent_of
    usage_cache: dict[str, tuple[float, dict[str, Any]]] = {}   # subscription usage per provider (api/models_usage.py)
    appctx.add(usage_cache=usage_cache)
    account_cache: dict[tuple[str, str, bool], tuple[float, dict[str, Any]]] = {}   # balances per provider, key and admin key (models_usage)
    appctx.add(account_cache=account_cache)

    # A profile saved before a provider was removed from the catalog: forget that choice (the server default applies) instead of failing every turn.
    _prof = store.profile()
    for _field, _model_field in (("provider", "model"), ("fallback_provider", "fallback_model"), ("background_provider", "background_model")):
        if _prof[_field].strip().lower() in REMOVED_PROVIDERS:
            store.set_profile(**{_field: "", _model_field: ""})
            log.warning("stored %s %r is no longer offered; cleared (the server default applies)", _field, _prof[_field])
    # The main model and the fallback never come from the same provider. A profile saved before that rule may hold both: empty the fallback.
    _prof = store.profile()
    _main = _prof["provider"] or settings.provider
    if _main and _prof["fallback_provider"] and provider_identity(_main) == provider_identity(_prof["fallback_provider"]):
        store.set_profile(fallback_provider="", fallback_model="")
        log.info("fallback model cleared: it was on the same provider as the main model (%s)", provider_key(_main))

    # replies in progress, and finished ones for a little while (turn_engine.TurnRegistry); the names below are its methods
    registry = TurnRegistry(lambda: session, lambda: hub)
    turn_log = TurnLog(store)   # every reply's events, written down as they happen (turn_log.py): readable after a restart
    turn_log.interrupt_running()   # a reply the last run of the server stopped under: its last event is "interrupted"
    turns, goal_task_ids = registry.turns, registry.goal_task_ids
    running_turns, held_by, turn_line = registry.running, registry.held_by, registry.turn_line

    thumbs: dict[str, tuple[float, str, Any]] = {}   # owner -> (taken at, url, (data url, w, h)); a list request reuses one for 10 s
    ended_tabs: dict[str, dict[str, Any]] = {}   # schedule/task tab -> its finished entry (BrowserTab + "shot": the large JPEG), oldest first
    appctx.add(thumbs=thumbs, ended_tabs=ended_tabs)   # the tab list's pictures (api/browser.py); a cleared chat empties both

    cancel_turn, sweep, looked_after = registry.cancel, registry.sweep, registry.looked_after
    appctx.add(registry=registry)

    def key_args(prov_name: str) -> dict[str, Any]:
        try:
            name = get_provider(prov_name).name
            if keyring.has(name):   # the key is in the key container: the call goes through it with a placeholder
                return {"api_key": PLACEHOLDER, "client": keyring.client_for(name, httpx.Timeout(120.0, connect=15.0, read=stall_seconds()))}
            if key := store.key(get_provider(prov_name).name):  # a key entered in the app wins over .env
                return {"api_key": key}
        except KeyError:
            pass  # unknown provider: the stream reports it
        return {}

    # ---- compaction: each request fitted to the window of the model that runs it (see the CONVERSATION COMPACTION notes above)
    def meta_json(key: str) -> dict[str, Any] | None:
        try:
            value = json.loads(store.meta(key) or "null")
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def window_for(provider: str, model: str, *, learned: bool = True) -> windows.Window:
        pk = provider_key(provider)
        return windows.lookup(pk, model, overrides=settings.context_overrides, stored=meta_json(f"model_windows:{pk}"),
                              learned=meta_json(f"learned_window:{pk}/{model}") if learned else None)

    def learn_window(provider: str, model: str, tokens: int) -> windows.Window:
        """A request of about `tokens` was too long for this model: remember a lower window (never raised by later successes)."""
        pk, cur = provider_key(provider), window_for(provider, model)
        base = window_for(provider, model, learned=False)
        new = max(4_096, int(min(cur.context, max(tokens, 1)) * 0.9))
        if new < cur.context:
            store.set_meta(f"learned_window:{pk}/{model}", json.dumps({"context": new, "base_context": base.context,
                                                                       "base_source": base.source, "at": time.time()}))
            log.warning("context window of %s/%s lowered to %d tokens after a context-length error", pk, model, new)
        return window_for(provider, model)

    async def memory_flush(provider: str, model: str, transcript: str) -> None:
        """The silent memory step before a compaction: only the memory file tools; whatever it says is dropped."""
        if home is None:
            return
        msgs = [{"role": "system", "content": MEMORY_FLUSH_PROMPT.format(token=NO_REPLY)},
                {"role": "user", "content": "Messages about to be condensed:\n\n" + transcript}]
        agen = run_agent(provider, model, msgs, memory_tools(home), max_steps=4, stream_fn=counted("compaction"), **key_args(provider))
        try:
            async for _ev in agen:
                pass
        finally:
            await agen.aclose()

    compactor = Compactor(store, job_stream("compaction"), key_args, threshold=settings.compact_at, flush_fn=memory_flush if home is not None else None)
    background_folds: set[asyncio.Task] = set()
    schema_tokens: list[tuple[tuple[str, ...], int]] = []

    def tools_tokens() -> int:
        """The tool definitions a chat turn sends: the core set and what the conversation has loaded (tools on demand)."""
        names = tuple(tool_scopes.load("chat"))
        if not schema_tokens or schema_tokens[0][0] != names:
            box = ToolBox(tool_core, names)
            shown = box.shown(tools) if tools else []
            schema_tokens[:] = [(names, est_tokens(json.dumps([t.spec() for t in shown], ensure_ascii=False)) if shown else 0)]
        return schema_tokens[0][1]

    def schedules_for_prompt() -> list[dict[str, Any]] | None:
        """The saved scheduled jobs for the per-turn line (prompt.schedules_line); None when they cannot be read."""
        try:
            return schedules.list()
        except Exception:  # noqa: BLE001 - a turn never fails over this line
            log.exception("could not list schedules for the prompt")
            return None

    def system_parts(provider: str, model: str, tz_name: str) -> tuple[str, str]:
        """(stable system text, this turn's lines): see PromptBuilder.build_parts. The stable part stays byte-identical between turns,
        so it can be a cached prefix; the turn's lines (the clock to the minute, schedules, goals) go after it."""
        if prompts:
            prof = store.profile()
            return prompts.build_parts(agent_name=prof["agent_name"], user_name=prof["user_name"], provider=provider, model=model,
                                       goals=goal_book.for_prompt(), tz_name=tz_name, connected=connected_ids(),
                                       schedules=schedules_for_prompt(), tone=prof["tone"], limits=permissions.limits_line())
        return settings.system_prompt, persona(tz_name).lstrip("\n")

    def system_text(provider: str, model: str, tz_name: str) -> str:
        """The whole system text in one piece (stable part, then the turn's lines), for runs that send no conversation of their own."""
        stable, turn = system_parts(provider, model, tz_name)
        return f"{stable}\n\n{turn}" if turn else stable

    async def warm_chat_session(tries: tuple[int, ...] = (0, 10, 30)) -> None:
        """When the main model runs on the agy CLI, start its session for the chat's system prompt now (no model request, no tokens),
        so the first reply after a restart does not wait for agy's ~5 s start-up. The sidecar may still be coming up: a few tries."""
        try:
            prof = store.profile()
            provider, model = prof["provider"] or settings.provider, prof["model"] or settings.model
            if not provider or not model or get_provider(provider).name != cli_manage.AGY:
                return
            from .providers import cli_agents, cli_remote
            stable, _turn = system_parts(provider, model, prof["timezone"])
            req = ChatRequest(model=model, messages=cli_agents.fit_agy_rules([{"role": "system", "content": stable}]),
                              extra=effort_extra(cli_manage.AGY, prof["effort"], model), purpose="chat")
            for wait in tries:
                await asyncio.sleep(wait)
                if await cli_remote.warm(cli_manage.AGY, req):
                    log.info("agy chat session warmed")
                    return
            log.info("agy chat session could not be warmed (the first reply starts it)")
        except Exception as e:  # noqa: BLE001 - only an optimisation
            log.info("agy chat session could not be warmed (%s)", type(e).__name__)

    warm_tasks: set[Any] = set()
    main_loop: list[Any] = []   # the server's event loop, noted at start-up
    opened_warm = [0.0]   # when the app being opened last started a warm (monotonic)

    def warm_on_open() -> None:
        """A foreground app request: start the chat's agy session in the background if it is not up (after a restart or the idle close).
        At most once a minute; the sidecar does nothing when a live worker for the prompt is there. Never blocks the request."""
        now = warm_clock()
        if store.keyguard.locked or now - opened_warm[0] < 60 and opened_warm[0]:
            return
        opened_warm[0] = now
        def start() -> None:
            task = asyncio.get_running_loop().create_task(warm_chat_session((0,)))
            warm_tasks.add(task)
            task.add_done_callback(warm_tasks.discard)
        if main_loop:   # note_use may run in a worker thread
            main_loop[0].call_soon_threadsafe(start)

    class TurnContext:
        """One turn's conversation, built for whichever model runs it: system prompt, head, summary, history, then `tail` (this turn's
        message). Built again for the fallback model, and again after a context-length error."""

        def __init__(self, tz_name: str, tail: list[dict[str, Any]], before_id: int | None):
            self.tz_name, self.tail, self.before_id = tz_name, tail, before_id
            self.provider = self.model = ""
            self.window: windows.Window | None = None
            self.fixed = 0
            self.assembled: Any = None
            self.on_status: Callable[[str], None] = lambda _s: None

        async def build(self, provider: str, model: str) -> list[dict[str, Any]]:
            """[stable system text, summary, history, this turn's lines, this turn's message(s)]: everything before the turn's lines
            is the same as in the previous turn, so providers that cache the start of a request reuse it."""
            self.provider, self.model, self.window = provider, model, window_for(provider, model)
            system, turn = system_parts(provider, model, self.tz_name)
            notes = memory_note(self.tail, self.before_id)   # relevant notes and the inbox: per turn, after the cached part
            turn = "\n\n".join(x for x in (turn, jobs_note(), notes) if x)
            note = [{"role": "user", "content": TURN_NOTE + turn + TURN_NOTE_END}] if turn else []
            self.fixed = est_tokens(system) + request_tokens(note + self.tail) + tools_tokens()
            a = compactor.assemble(self.window, self.fixed, self.before_id)
            if a.over:   # it would reach the threshold: fold before sending (waits for a background fold that is running)
                self.on_status(i18n.t("Tidying up the conversation"))
                await compactor.compact(provider, model, self.window, self.fixed, self.before_id)
                a = compactor.assemble(self.window, self.fixed, self.before_id)
            self.assembled = a
            return [{"role": "system", "content": system}, *a.messages, *note, *self.tail]

        async def compact_in_turn(self, history: list[dict[str, Any]], target: int) -> list[dict[str, Any]] | None:
            """Inside the turn, after the tool digests: fold the oldest history messages of this request into the summary and take
            them out (they go even if the fold fails: the request could not be sent otherwise)."""
            a = self.assembled
            if a is None or self.window is None:
                return None
            row_of = {id(m): r for m, r in zip(a.history, a.rows)}
            pos = [i for i, m in enumerate(history) if id(m) in row_of]
            if len(pos) <= MIN_KEEP:
                return None
            over, freed, chosen = request_tokens(history) - target, 0, []
            for i in pos[:-MIN_KEEP]:
                chosen.append(i)
                freed += message_tokens(history[i])
                if freed >= over:
                    break
            self.on_status(i18n.t("Tidying up the conversation"))
            await compactor.fold_through(self.provider, self.model, self.window, row_of[id(history[chosen[-1]])]["id"])
            summary = store.conversation_summary()["summary"]
            old = a.summary_msgs[0] if a.summary_msgs else None
            new_msg = summary_message(summary) if summary.strip() else None
            drop, out, placed = set(chosen), [], False
            for i, m in enumerate(history):
                if i in drop:
                    continue
                if old is not None and m is old:
                    if new_msg:
                        out.append(new_msg)
                    placed = True
                    continue
                if not placed and new_msg and id(m) in row_of:   # no summary before: it goes right before the history
                    out.append(new_msg)
                    placed = True
                out.append(m)
            a.summary_msgs = [new_msg] if new_msg else []
            return out

    def after_turn(ctx: TurnContext) -> None:
        """After a chat turn: fold in the background now if the next request would likely cross the threshold."""
        chosen = journal_model()
        if not chosen or ctx.window is None:
            return
        provider, model = chosen
        window, fixed = window_for(provider, model), ctx.fixed + NEXT_TURN_MARGIN
        try:
            if not compactor.needs_fold(window, fixed):
                return
        except Exception:  # noqa: BLE001
            log.exception("compaction check failed")
            return
        task = asyncio.ensure_future(compactor.compact(provider, model, window, fixed))
        background_folds.add(task)
        task.add_done_callback(background_folds.discard)

    pending_replies: dict[str, tuple[list[dict[str, Any]], int | None]] = {}   # turn id -> the quick replies its message gets
    pending_comparisons: dict[str, dict[str, Any]] = {}   # turn id -> the comparison card its message gets
    pending_approvals: dict[str, dict[str, Any]] = {}   # approval id -> its approval_request event (+ turn_id, task_id, asked_at, expires_at)
    # (turn id, tool call id) -> (grant, label, what) of a message or change a task's setup permission covered: the user is told
    # right after it ran (schedule_grants.py)
    covered_calls: dict[tuple[str, str], tuple[dict[str, Any], str, str]] = {}
    run_progress = RunProgress()   # a long run tells once how far it got (backlog 66)

    def turn_scopes(turn: Turn) -> list[str]:
        """The conditional tasks a turn works for (schedule_grants.current_scopes, from the turn instead of the running context)."""
        out = [turn.cond] if turn.cond else []
        if turn.source.startswith("schedule:") and turn.source[9:].isdigit():
            out.append(turn.source)
        if turn.worker_id:
            out.append(f"job:{turn.worker_id}")
        return out

    def grant_done_note(turn: Turn, hit: tuple[dict[str, Any], str, str]) -> None:
        g, label, what = hit
        followed = not turn.unattended and time.monotonic() - turn.touched < WATCHED_SECONDS
        name = g.get("task_name") or turn.title
        activity.notify("task_done", i18n.t("Done as you allowed: {label}", label=label)[:120],
                        f"{name} · {what}"[:400] if name else what[:400], task_id=turn.task_id, push=not followed)
    resolved_checks: dict[str, dict[str, Any]] = {}     # approval id -> how a human check hand-over ended, for HANDOVER_RESOLVED_KEEP
    handover_seen: dict[str, dict[str, int]] = {}       # task scope (job id or turn id) -> site -> hand-overs solved (-1 = skipped / ran out)
    tab_failed: dict[str, bool] = {}                     # browser owner -> its last browser step failed
    owner_tasks: dict[str, int] = {}                     # browser owner -> the task that used its tab last
    stopping = [False]   # set at shutdown: turns cut off from then on keep their "interrupted" task instead of ending as stopped
    pending_links: dict[str, tuple[int, int]] = {}       # source -> (the task it resumes, restarts so far): an automatic run started again

    def keep_resume(turn: Turn, spec: dict[str, Any] | None, restarts: int = 0, resumed_from: int | None = None) -> None:
        """Remember how to start `turn` again (resume.py); and when it resumes an earlier task, tie the two together."""
        turn.resume, turn.restarts = spec, restarts
        if spec is not None:
            activity.set_resume(turn.task_id, spec, restarts, resumed_from)
        if resumed_from:
            old = activity.resume_row(resumed_from) or {}
            went_on = old.get("status") == "waiting_model"
            activity.resolve_resume(resumed_from, "resumed", turn.task_id, status="interrupted", seen=True,
                                    result=i18n.t("Went on once the AI model was back") if went_on else None)

    def approval_site(name: str, args: dict[str, Any]) -> str:
        if name == "workroom_install":
            return install_site(args)   # one package: "<name>.pypi.install", so "remember" covers that package only
        for k in ("site", "url"):
            if isinstance(args.get(k), str) and args[k].strip():
                return host_of(args[k])
        if session is not None and name.startswith("browser_"):
            return host_of(session.current_url())
        if android_session is not None and name.startswith("android_"):
            return android_session.package()   # the app stands for the site
        return ""

    def task_closed(turn: Turn, status: str, answer: str, error: str) -> None:
        """The reply is over: its task record, and a notification when nobody was there to see the end."""
        if status == "waiting_model":   # the AI model is down: the task waits and goes on once it is back (model_wait_tick)
            activity.finish_task(turn.task_id, status, answer=answer, error=error, seen=True)
            if turn.limited:
                store._run("UPDATE tasks SET result = ? WHERE id = ?", (i18n.t(model_limits.USED_UP), turn.task_id))
            # out of usage: the first check is at the reset (nothing is tried before it); an outage: in a minute
            activity.wait_for_model(turn.task_id, turn.limit_reset if turn.limited and turn.limit_reset else time.time() + next_wait(0))
            return
        # a job's own steps, a background reply that chose to say nothing, or a scheduled run that asked to be tried again later
        quiet = turn.quiet_end or answer.strip() == NO_REPLY or turn.task_id in schedules.retried_tasks
        watched = quiet or (not turn.unattended and time.monotonic() - turn.touched < WATCHED_SECONDS)
        task = activity.finish_task(turn.task_id, status, answer=answer, error=error, seen=watched)
        if watched or task is None or status not in ("done", "failed"):
            return
        if status == "done":
            activity.notify("task_done", f"{turn.note_head or i18n.t('Finished')}: {turn.title}"[:120], task["result"], task_id=turn.task_id,
                            schedule_id=task["schedule_id"])
        else:
            activity.notify("task_failed", f"{i18n.t('It did not work out')}: {turn.title}"[:120], task["result"], task_id=turn.task_id,
                            schedule_id=task["schedule_id"])

    def scope_of(source: str) -> str:
        """The tool scope of a run by its source: the conversation's own turns share "chat"; a schedule or the checklist has its own."""
        return "chat" if source in ("chat", "api", "handoff", "background") else source

    def turn_toolbox(scope: str) -> ToolBox:
        """The run's ToolBox: the core tools, then what this scope loaded before. An automatic run of a schedule also gets, up front,
        the tools its runs need (SCHEDULE_RUN_TOOLS) and, before its first run, the tools of the conversation it was set up in, so an
        unattended run does not have to go looking."""
        up_front: list[str] = []
        if scope.startswith("schedule:"):
            if not store.meta(ToolScopes.PREFIX + scope):
                up_front += tool_scopes.load("chat")
            up_front += SCHEDULE_RUN_TOOLS
        box = tool_scopes.box(scope, tool_core, up_front)
        box.blocked = permission_gate.hides   # a tool the owner switched off for every connector it could act on is not offered
        return box

    def start_turn(*args: Any, **kw: Any) -> Turn:
        """Begin a reply in the background (turn_engine.run_turn: the engine, its dependencies from the app context)."""
        return run_turn(appctx, *args, **kw)

    def start_background(*args: Any, **kw: Any) -> Any:
        return turn_engine.start_background(appctx, *args, **kw)

    def entry_answered(req: dict[str, Any]) -> tuple[str, str | None]:
        """The user saved a fill-in card after the reply that asked for it had ended (it ran out of waiting, or was stopped): the work goes
        on by itself. A background job waiting for input is resumed; a chat request starts a short reply (on the tab the asking reply
        used) once no other chat reply is running. Returns (reply, turn id): ("started", id) when a reply started at once, ("queued", None)
        when the work goes on later, ("none", None) otherwise (secret_requests.REPLY_STATES). A cancel starts nothing: it is told with the
        user's next message (SecretRequests.take_notes)."""
        if req.get("state") != "saved" or req.get("source") != "agent":
            return "none", None
        what = {"key": f"the key for {req.get('name')!r} (it went straight to the keychain)",
                "login": f"the login for {', '.join(req.get('hosts') or [])} (name {req.get('name')!r})",
                "card": "a payment card (it went straight to the wallet; see wallet_cards)"}.get(req["kind"], "what you asked for")
        if req["kind"] == "key" and ((req.get("result") or {}).get("checked") == "ok"):
            what += "; the service accepted it on a test request"
        note = (f"[Not from the user: the user just saved {what} in the fill-in card you asked for. Go on with what you were doing; do "
                "not ask for it again.]")
        rows = store._all("SELECT worker_id, run_source, owner FROM secret_requests WHERE id = ?", (req["id"],))
        row = rows[0] if rows else {}
        if row.get("worker_id"):
            r = workers._row(row["worker_id"])
            if r is not None and r["status"] == "needs_input":
                workers.resume(row["worker_id"], why=note, auto=True)
                return "queued", None
            return "none", None
        if scope_of(row.get("run_source") or "chat") != "chat":
            return "none", None   # a scheduled run: its next run uses what was saved
        try:
            provider, model = resolve_model(None, None)
        except HTTPException:
            return "none", None

        # the reply that asked may have been cut off by a restart meanwhile (its "Continue" offer still open): this reply IS that
        # continuation, so it carries the user's original request and settles the offer (one continuation, not two), and it is
        # told the owner's language (an English-only note made an English, clipped answer: 2026-11-10)
        asked = int(req["task_id"]) if req.get("task_id") else None
        cut = activity.resume_row(asked) if asked else None
        spec = dict((cut or {}).get("spec") or {})
        offer = activity.offer_for_task(asked) if asked else None
        first = store._all("SELECT content FROM messages WHERE id = ? AND role = 'user'", (int(spec["message_id"]),)) \
            if spec.get("message_id") else []
        if first:
            note += (f"\n[Not from the user: what the user asked, which this reply continues: {str(first[0]['content'])[:1500]!r}. "
                     "Finish that request.]")
        note = with_language(note, i18n.owner())

        def begin() -> Turn:
            ctx = TurnContext(store.profile()["timezone"], [{"role": "user", "content": note}], None)
            said = ReplyText()   # a line that only led into a tool step is not kept as the answer of a stopped reply

            def observe(item: dict[str, Any] | None) -> None:
                if item is not None:
                    said.feed(item)
                if item is None or item["type"] == "done":
                    if said:
                        store.add_message("assistant", collapse_repeat(said.text()))
                    said.clear()

            title = i18n.t("Carrying on: {label}", label=req.get("label") or req.get("name") or "")
            t = start_turn(provider, model, ctx.tail, observe, source="chat", title=title, owner=row.get("owner") or None, context=ctx)
            t.handing += 1   # not stopped for want of a reader: the save may have come from the Keychain page or another phone
            if offer is not None and asked is not None:   # the cut-off reply's Continue button is used up by this reply
                use_offer(int(offer["message_id"]))
                keep_resume(t, spec or {"kind": "chat"}, int((cut or {}).get("restarts") or 0), asked)
            return t

        busy = [t for t in turns.values() if not t.done and t.source == "chat"]
        if not busy:
            return "started", begin().id

        async def later() -> None:   # the user is in another reply right now: carry on once it is over
            for t in busy:
                await t.ended.wait()
            try:
                begin()
            except Exception:  # noqa: BLE001
                log.exception("could not go on after a fill-in card was saved")
        asyncio.ensure_future(later())
        return "queued", None

    async def run_schedule(s: dict[str, Any]) -> dict[str, Any]:
        waited = ""
        if store.keyguard.locked:   # Keyguard: no secret opens until a phone unlocks; the run waits for it, then says so
            since = time.monotonic()
            if not await store.keyguard.wait_unlocked(LOCKED_WAIT_SECONDS):
                tid = activity.start_task(s["name"], f"schedule:{s['id']}")
                activity.finish_task(tid, "failed", error=i18n.t(LOCKED_LINE))
                activity.notify("task_failed", f"{i18n.t('It did not work out')}: {s['name']}"[:120], i18n.t(LOCKED_LINE), task_id=tid,
                                schedule_id=s["id"])
                return {"task_id": tid, "status": "failed", "error": "locked"}
            minutes = max(1, round((time.monotonic() - since) / 60))
            waited = (f"\n\n[Not from the user: this run started {minutes} minute(s) late because Kelo was locked (Keyguard) until the "
                      "the user unlocked it on their phone. Say so in one short line of the report.]")
        try:
            note = str(s.get("retry_note") or "")   # an expired approval tried again, or the try-again the last run asked for
            retry = (f"\n\n{note}" if note.startswith("[") else f"\n\n[Not from the user: {note}]") if note else ""
            retry += waited
            allowed = schedule_grants.summary(f"schedule:{s['id']}")   # what the user allowed it at setup (schedule_grants.py)
            turn = start_background(s["prompt"] + retry, title=s["name"], source=f"schedule:{s['id']}", owner=f"schedule:{s['id']}",
                                    note=SCHEDULE_NOTE.format(name=s["name"]), saved_as=i18n.t("[Automatic run · {name}]", name=s['name']),
                                    extra_note=allowed)
        except HTTPException as e:
            tid = activity.start_task(s["name"], f"schedule:{s['id']}")
            activity.finish_task(tid, "failed", error=str(e.detail))
            activity.notify("task_failed", f"{i18n.t('It did not work out')}: {s['name']}"[:120], str(e.detail), task_id=tid, schedule_id=s["id"])
            return {"task_id": tid, "status": "failed", "error": str(e.detail)}
        await turn.ended.wait()
        task = activity.task(turn.task_id, full=True) or {}
        quiet = (task.get("answer") or "").strip() == NO_REPLY   # nothing worth telling: no message, no notification (task_closed)
        status = "stopped" if turn.final_status == "waiting_model" else turn.final_status   # it goes on once the model is back
        return {"task_id": turn.task_id, "status": status, "error": turn.final_error, "quiet": quiet,
                "result": "" if quiet else str(task.get("result") or "")}

    def schedule_off(s: dict[str, Any]) -> None:
        activity.notify("schedule_disabled", i18n.t("Turned off the automatic run: {name}", name=s['name'])[:120], s.get("disabled_reason") or "", schedule_id=s["id"])

    def schedule_paused(s: dict[str, Any]) -> None:
        if s.get("paused_by") != "user":   # the user's own pause needs no notification
            activity.notify("schedule_paused", i18n.t("Paused the automatic run: {name}", name=s['name'])[:120], s.get("paused_reason") or "", schedule_id=s["id"])

    scheduler.run_fn, scheduler.on_disabled = run_schedule, schedule_off
    schedules.on_paused = schedule_paused

    # the checklist (checklist.py): CHECKLIST.md, part of the proactive messages (their switch, pace, waking hours and daily cap),
    # quiet unless something needs attention
    async def run_checklist(body: str) -> dict[str, Any]:
        turn = start_background(body, title=i18n.t("Recurring check"), source="checklist", owner="task:checklist", note=CHECKLIST_NOTE,
                                saved_as=i18n.t("[Recurring check]"))
        turn.note_head = i18n.t("Take a look")
        await turn.ended.wait()
        task = activity.task(turn.task_id, full=True) or {}
        return {"task_id": turn.task_id, "status": turn.final_status, "error": turn.final_error,
                "quiet": (task.get("answer") or "").strip() == NO_REPLY}

    checklist = Checklist(store, home, run_checklist, waking=lambda t: proactive.awake(t), switch=lambda: proactive.config()["enabled"],
                          pace=lambda: proactive.config()["every_minutes"] * 60.0, room=lambda: proactive.room_today(),
                          on_said=lambda task_id: proactive.checklist_said(task_id),
                          hold=lambda: proactive.checklist_hold()) if home else None

    def journal_model() -> tuple[str, str] | None:
        prof = store.profile()
        provider, model = prof["provider"] or settings.provider, prof["model"] or settings.model
        return (provider, model) if provider and model else None

    def rename_task(task_id: int, name: str) -> None:
        activity.rename_task(task_id, name)
        for live in turns.values():   # the running list (GET /api/agent/status) shows the new name too
            if live.task_id == task_id:
                live.title = name[:80]

    task_namer = TaskNamer(job_stream("task_name"), rename_task, journal_model, lambda p: key_args(p))   # a short task name (task_names.py)

    # The nightly jobs (journal, then the look-back) run once a night at the quietest time learned from the user's own use
    # (quiet_hours.py; 03:50 until a week of use is known), checked every 5 minutes so a restart during the night still runs them.
    nightly_jobs: list[Callable[[], Awaitable[Any]]] = [journal.daily]

    async def purge_schedules() -> int:
        return schedules.purge_archived()
    scheduler.paused = lambda: store.keyguard.locked   # jobs that need a key wait for Keyguard's unlock (schedules.Scheduler.held)
    scheduler.add_daily("schedule_archive", 4, 30, purge_schedules, needs_secrets=False)   # deleted schedules older than 30 days go for good
    scheduler.add_every("watchers", 30, watchers.check_due)
    if checklist:
        scheduler.add_every("checklist", checklist.period, checklist.tick)
    if reflection:
        nightly_jobs.append(reflection.nightly)
    if memory_upkeep:
        nightly_jobs.append(memory_upkeep.nightly)              # right after the look-back, so its rules are current
        scheduler.add_every("memory_idle", 300, memory_upkeep.idle_tick)   # and after a busy chat has gone quiet
    if nightly_review:
        nightly_jobs.append(nightly_review.nightly)             # after the memory pass: looks back at Kelo's own work
    nightly = NightlyRunner(store, quiet, nightly_jobs)
    scheduler.add_every("nightly", 300, nightly.tick)
    scheduler.add_every("kelo_names", KELO_TICK_SECONDS, kelo_names.tick)   # names never connected go after an hour; idle ones after 30 days
    if deliverables:
        async def purge_trash() -> int:
            return await asyncio.to_thread(deliverables.purge)
        scheduler.add_daily("library_trash", 4, 20, purge_trash, needs_secrets=False)   # deleted results older than 30 days, and expired share links
    if home:
        async def purge_home_trash() -> int:
            return await asyncio.to_thread(home.purge_trash)
        scheduler.add_daily("home_trash", 4, 25, purge_home_trash, needs_secrets=False)   # deleted agent files older than 30 days

    # ---- background jobs the agent hands off with delegate (workers.py; WORKERS API next to serialize()) ---------------------------
    def start_worker_turn(*args: Any, **kw: Any) -> Any:
        return turn_workers.start_worker_turn(appctx, *args, **kw)

    handoff_lock = asyncio.Lock()

    async def worker_handoff(*args: Any, **kw: Any) -> Any:
        return await turn_workers.worker_handoff(appctx, *args, **kw)

    async def worker_emit(*args: Any, **kw: Any) -> Any:
        return await turn_workers.worker_emit(appctx, *args, **kw)

    def worker_window(*args: Any, **kw: Any) -> Any:
        return turn_workers.worker_window(appctx, *args, **kw)

    def delegated(*args: Any, **kw: Any) -> Any:
        return turn_workers.delegated(appctx, *args, **kw)

    def worker_limit_until(*args: Any, **kw: Any) -> Any:
        return turn_workers.worker_limit_until(appctx, *args, **kw)

    def worker_limit_known(*args: Any, **kw: Any) -> Any:
        return turn_workers.worker_limit_known(appctx, *args, **kw)

    def worker_limit_text(*args: Any, **kw: Any) -> Any:
        return turn_workers.worker_limit_text(appctx, *args, **kw)

    def worker_limit_notice(*args: Any, **kw: Any) -> Any:
        return turn_workers.worker_limit_notice(appctx, *args, **kw)

    workers = Workers(store, WorkerHost(
        start_turn=start_worker_turn, cancel_turn=cancel_turn, handoff=worker_handoff, emit=worker_emit, tools=lambda: tools,
        browser=session is not None, tab_open=lambda k: session is not None and session.has_tab(k),
        has_picture=lambda k: session is not None and (session.has_tab(k) or k in ended_tabs),
        controlled=lambda k: held_by(k) is not None, retire_tab=lambda k: retire_tab(k),
        login_saved=lambda n: transient.has(n) or bool((c := store.credential(n)) and c.get("secret")), window=worker_window,
        on_delegate=delegated, on_end=lambda wid, status: (reactions.job_ended(wid, status), schedule_grants.drop_job(wid)),
        limit_text=worker_limit_text, limit_until=worker_limit_until, limit_notice=worker_limit_notice,
        progress=lambda wid: task_grants.progress(f"job:{wid}")),
        WorkerLimits(settings.workers_max, settings.worker_steps, settings.worker_minutes, settings.worker_tokens),
        resume_on_start=settings.worker_resume_on_start)
    if with_worker_tools or (with_worker_tools is None and not tools_given):
        tools = [*tools, *workers.main_tools()]
    if session is not None:
        session.control_listeners.append(workers.control_changed)   # the user handing a paused job's tab back lets it go on
    app.include_router(module_router(worker_routes, workers, require_auth))
    app.state.workers = workers

    async def worker_limit_tick(*args: Any, **kw: Any) -> Any:
        return await turn_workers.worker_limit_tick(appctx, *args, **kw)
    scheduler.add_every("worker_limits", 60, worker_limit_tick)   # jobs paused by the usage limit: one notice, go on after the reset

    # ready-to-buy watchers (buy_ready.py, docs/instant-buy.md): a parked tab, a keep-alive, and a short buying run at the hit
    async def run_small_turn(system: str, user: str, *, source: str, owner: str, title: str, names: tuple[str, ...], steps: int, cond: str,
                             wrap_tools: Callable[[list[Any]], list[Any]] | None = None,
                             stream_wrap: Callable[..., Any] | None = None) -> tuple[Any, str]:
        """A restricted unattended run (only the named tools, a step budget) on one tab; (the turn, the words it ended with)."""
        provider, model = resolve_model(None, None)   # the main model; run_turn falls back to the fallback model itself
        by_name = {t.name: t for t in tools if t.name in names}
        turn_tools = [by_name[n] for n in names if n in by_name]
        if wrap_tools is not None:
            turn_tools = wrap_tools(turn_tools)
        said, final = ReplyText(), [""]

        def observe(item: dict[str, Any] | None) -> None:
            if item is None:
                final[0] = said.text().strip()
            else:
                said.feed(item)

        turn = start_turn(provider, model, [{"role": "system", "content": system}, {"role": "user", "content": user}], observe, source=source,
                          title=title, unattended=True, owner=owner, tools_=turn_tools, max_steps=steps,
                          tool_scope=source, cond=cond, stream_wrap=stream_wrap)
        turn.quiet_end = True   # its own words go to the owner through the handoff report or the urgent notification, not a task notice
        await turn.ended.wait()
        return turn, final[0]

    async def buy_login(scope: str, site: str) -> bool:
        """A quiet sign-in on the parked tab under the task's login grant. browser_login is covered by a grant only inside a turn (the
        grant reads the running turn's `cond`), so this is a tiny unattended run with only browser_read, browser_click, browser_login."""
        site_names = set(wallet_site_keys(site))
        grants_here = [g for g in schedule_grants.list(scope=scope, kind="login") if g["site"] in site_names]
        wid = scope.partition(":")[2]
        if not grants_here or not wid.isdigit():
            return False
        try:
            turn, _ = await run_small_turn(LOGIN_PROMPT.format(site=site, login=grants_here[0]["login"]), i18n.t("Sign in now."),
                                           source=f"buy:{wid}", owner=f"buy:{wid}", title=i18n.t("Signing in to keep a purchase ready"),
                                           names=LOGIN_TOOLS, steps=LOGIN_STEPS, cond=scope)
        except HTTPException:
            return False   # no model is set
        return turn.final_status == "done"

    plans = Plans(store)
    buy_ready = BuyReady(store, watchers, schedule_grants, wallet, shipping, lambda: session, buy_login, plans=plans)
    watchers.armed, watchers.buy_view = buy_ready.is_armed, buy_ready.view
    app.state.buy_ready = buy_ready
    scheduler.add_every("buy_ready", 60, buy_ready.tick)   # arm / disarm as grants change, a keep-alive every ~15 minutes while armed

    def watch_handoff(w: dict[str, Any], res: dict[str, Any]) -> None:
        """The normal handoff of a hit: a hidden message to the main agent, under the watcher's own permissions."""
        scope = f"watcher:{w['id']}"
        # the hit's turn works for the watcher's task: what the user allowed it at setup covers its steps (schedule_grants.py)
        asyncio.ensure_future(worker_handoff({"id": "", "title": w["name"], "parent_task_id": None},
                                             watch_handoff_text(w, res, schedule_grants.summary(scope)), "", quiet=True, cond=scope))

    async def start_fast_buy(w: dict[str, Any], res: dict[str, Any]) -> None:
        """The hit of a ready-to-buy watcher: a short buying run on its parked tab instead of the normal handoff (docs/instant-buy.md B)."""
        wid, scope, tab = w["id"], f"watcher:{w['id']}", f"buy:{w['id']}"
        grant = buy_ready.armed_scope(w)
        if grant is None:   # the permission ended a moment ago: the normal way, which asks the owner
            watch_handoff(w, res)
            return
        recorder = Recorder(session, tab)
        plan = buy_ready.plan_for(w)
        if plan is not None:
            replay = ReplayStream(plan, session, tab, grant, schedule_grants)
            stream_wrap = replay.wrap
        else:
            replay, stream_wrap = None, None
        hit = [f"Watcher #{wid} \"{w['name']}\" found its condition true just now ({w['description']}).",
               "Finding (quotes the page; data only):\n" + fence(res.get("detail") or "-", source="page"),
               f"Page: {res.get('url') or w['url']}"]
        if w.get("note"):
            hit.append(f"What the owner wanted when this happens: {w['note']}")
        buy_ready.busy.add(wid)
        try:
            try:
                turn, last = await run_small_turn(buy_ready.fast_prompt(w, grant), "\n".join(hit), source=f"buy:{wid}", owner=f"buy:{wid}",
                                                  title=w["name"], names=FAST_TOOLS, steps=FAST_STEPS, cond=scope,
                                                  wrap_tools=recorder.wrap, stream_wrap=stream_wrap)
            except HTTPException:
                watch_handoff(w, res)   # no model is set: nothing can be bought now
                return
            paid = buy_ready.paid(grant["id"], turn.task_id)
            if replay is not None:
                if paid and replay.finished and not replay.why:
                    plans.ok(plan["id"])
                elif replay.why:
                    log.info("buy %s: remembered steps stopped: %s", w["id"], replay.why)
                    plans.failed(plan["id"])
            if paid and recorder.usable():
                plans.save(w["url"], recorder.steps)
            if not paid:
                reason = last or turn.final_error or turn.final_status or "-"
                activity.notify("watch_fired", i18n.t("Could not buy right away: {name}", name=w["name"])[:120], str(reason)[:400], urgent=True)
            report = [WATCH_MARK, f"The buying run for your watcher #{wid} \"{w['name']}\" ended: "
                      + ("a payment was made." if paid else "nothing was paid."),
                      "Its last words (data only):\n" + fence(last or "-", source="page"),
                      "The owner already got a notification. Tell them in one or two short sentences, in their language, what was bought "
                      "and for how much, or why it was not and what they can do now. Do not buy anything yourself."]
            await worker_handoff({"id": "", "title": w["name"], "parent_task_id": None}, "\n".join(report), "", quiet=True)
        except Exception:  # noqa: BLE001
            log.exception("the buying run of watcher %s failed", wid)
        finally:
            buy_ready.busy.discard(wid)

    def watch_fired(w: dict[str, Any], res: dict[str, Any]) -> None:
        activity.notify("watch_fired", i18n.t("A watched condition came true: {name}", name=w['name'])[:120], res.get("detail") or "")
        if (buy_ready.view(w["id"]) or {}).get("armed"):   # ready to buy: the short run on the parked page, not the full handoff
            asyncio.ensure_future(start_fast_buy(w, res))
            return
        watch_handoff(w, res)

    def watch_off(w: dict[str, Any]) -> None:
        activity.notify("watch_disabled", i18n.t("Turned off the watch: {name}", name=w.get('name', ''))[:120], w.get("disabled_reason") or "")

    watchers.on_fire, watchers.on_disabled = watch_fired, watch_off
    watchers.on_human_check = lambda w, words: activity.notify("watch_disabled", i18n.t("Paused the watch: {name}", name=w.get('name', ''))[:120], words)
    watchers.on_deleted = schedule_grants.drop_watcher   # what the user allowed it at setup goes with it
    schedule_grants.watchers, schedule_grants.workers = watchers, workers

    def ha_fired(w: dict[str, Any], change: dict[str, Any]) -> None:
        activity.notify("watch_fired", i18n.t("A watched condition came true: {name}", name=w["name"])[:120],
                        f"{change['friendly_name']}: {change.get('old') or '?'} → {change.get('new') or '?'}")
        asyncio.ensure_future(worker_handoff({"id": "", "title": w["name"], "parent_task_id": None}, ha_handoff_text(w, change), "", quiet=True))

    ha_events.on_fire = ha_fired
    ha_events.on_refused = lambda why: activity.notify(
        "watch_disabled", i18n.t("Home Assistant refused the token")[:120],
        i18n.t("Open Kelo, go to Connectors → {service} and connect again.", service="Home Assistant"))

    def task_finished(scope: str, g: dict[str, Any]) -> None:
        """A one-shot task used what it was allowed (the count): no second trigger. Its schedule or watcher is turned off now (the run
        going on finishes; its grants go when it ends) and the user is told."""
        kind, _, ref = scope.partition(":")
        name = g.get("task_name") or ""
        why = i18n.t("Done what it was set up for: {action}", action=g.get("action") or g.get("what") or name)
        if kind == "schedule" and ref.isdigit():
            schedules.finish(int(ref), why)
            scheduler.poke()
            activity.notify("schedule_disabled", i18n.t("Finished and turned off: {name}", name=name)[:120], why, schedule_id=int(ref))
        elif kind == "watcher" and ref.isdigit():
            watchers.finish(int(ref), why)
            activity.notify("watch_disabled", i18n.t("Finished and turned off: {name}", name=name)[:120], why)

    schedule_grants.on_finished = task_finished
    app.include_router(module_router(watcher_routes, watchers, require_auth, buy_check=buy_ready.keep))

    appctx.add(require_auth=require_auth, schedule_grants=schedule_grants, watchers=watchers, workers=workers, schedules=schedules, activity=activity)
    from .api import grants as api_grants
    app.include_router(api_grants.router(appctx))
    from .api import security as api_security
    app.include_router(api_security.router(appctx))
    from .api import actions as api_actions
    app.include_router(api_actions.router(appctx))
    from .api import android_device as api_android_device
    app.include_router(api_android_device.router(appctx))

    # proactive messages (proactive.py): off until the user turns them on
    async def deliver_proactive(item: dict[str, Any]) -> int:
        text = item["message"]
        store.add_message("assistant", text)
        tid = activity.start_task(item["title"][:80] or i18n.t("Heads-up"), "proactive")
        activity.finish_task(tid, "done", answer=text)
        # a mail says what it is ("New mail · sender · subject"); everything else comes from the agent by name
        activity.notify("proactive", (item.get("notice") or store.profile().get("agent_name") or "Kelo")[:60], text[:400], task_id=tid)
        return tid

    def events_ahead(days: int) -> Callable[[], Awaitable[list[dict[str, Any]]]]:
        """One read of the chosen calendars `days` ahead, all-day events included (big trips are often all-day): the proactive pass
        (big events two weeks ahead) and the briefing (this week's "coming up") each keep their one read at their own pace."""
        return lambda: sources.upcoming_events(days * 24, all_day=True, pages=2)

    proactive = Proactive(store, home, stream_fn=job_stream("proactive"), model=lambda: journal_model(), key_args=lambda p: key_args(p),
                          deliver=deliver_proactive, mail=sources.mail_view(), schedules=schedules, watchers=watchers,
                          calendar=events_ahead(AHEAD_DAYS),
                          busy=lambda: any(t.source in ("chat", "api") for t in running_turns()),
                          owner_marks=(HANDOFF_MARK, WATCH_MARK, GOAL_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK), tone=lambda: store.profile()["tone"])
    app.state.proactive, app.state.checklist = proactive, checklist
    scheduler.add_every("proactive", 60, proactive.tick)
    app.include_router(module_router(proactive_routes, proactive, require_auth))

    # rechecks (recheck.py): Kelo looks again at what it told the owner and corrects itself first. A restricted research turn (web_search,
    # web_fetch, weather, no conversation) per due row; a verified change goes to the main agent as a hidden handoff (RECHECK_MARK)
    async def run_recheck_turn(system: str, user: str) -> str:
        provider, model = resolve_model(None, None)
        by_name = {t.name: t for t in tools if t.name in RESEARCH_TOOLS}
        said, final = ReplyText(), [""]

        def observe(item: dict[str, Any] | None) -> None:
            if item is None:
                final[0] = said.text()
            else:
                said.feed(item)

        turn = start_turn(provider, model, [{"role": "system", "content": system}, {"role": "user", "content": user}], observe, source="recheck",
                          title=i18n.t("Rechecking an earlier answer"), unattended=True, tools_=[by_name[n] for n in RESEARCH_TOOLS if n in by_name],
                          max_steps=RESEARCH_STEPS, tool_scope="recheck")
        turn.quiet_end = True   # a research run tells nobody; only a verified change does, through the main agent
        await turn.ended.wait()
        if turn.final_status != "done":
            raise RuntimeError(str(turn.final_error or turn.final_status))
        return final[0]

    async def send_recheck(row: Any, text: str) -> None:
        await worker_handoff({"id": "", "title": (row.claim[:80] or i18n.t("An earlier answer")), "parent_task_id": None}, text,
                             i18n.t("An update on what I said"))

    def recheck_replied(text: str) -> bool:
        asked = store._all("SELECT id FROM messages WHERE role = 'user' AND content = ? ORDER BY id DESC LIMIT 1", (text,))
        return bool(asked) and bool(store._all("SELECT 1 FROM messages WHERE role = 'assistant' AND id > ? LIMIT 1", (asked[0]["id"],)))

    rechecks = build_rechecks(store, run_turn=run_recheck_turn, send=send_recheck, replied=recheck_replied, awake=proactive.awake,
                              next_awake=proactive.next_awake, prefs=proactive.prefs, config=proactive.config, tz=proactive.tz,
                              busy=lambda: any(t.source in ("chat", "api") for t in running_turns()),
                              ready=lambda: consents.required(store.profile(), settings.provider) is None,
                              turn_id=lambda: str((current_turn.get() or {}).get("id") or ""))
    proactive.sources.append(rechecks.source)
    if reflection:
        reflection.rechecks = rechecks.extractor   # the nightly look-back asks for rechecks in its own request
    app.state.rechecks = rechecks
    scheduler.add_every("rechecks", 300, rechecks.service.tick)
    app.include_router(module_router(recheck_routes, rechecks.service, require_auth))

    # updating this server from the app (server_updates.py; the updater container does the work, updater_service.py): any signed-in device (not on a review copy),
    # and only on the main copy (the one whose server has UPDATER_TOKEN and reaches the updater)
    def own_version() -> str:
        return release_version.current().version   # version.json
    server_updates = ServerUpdates(
        store, UpdaterClient(settings.updater_url, settings.updater_token, transport=updater_transport), version=own_version,
        notify=activity.notify, tz=lambda: store.profile().get("timezone"), quiet_slot=lambda now: quiet.quiet_slot(now),
        awake=lambda ts: proactive.awake(ts), last_use=lambda: last_use[0],
        busy=lambda: any(t.source in ("chat", "api") for t in running_turns()), default_auto=settings.auto_update)
    app.state.server_updates = server_updates
    if settings.updater_token:
        scheduler.add_every("server_update", 300, lambda: run_server_update_tick(server_updates), needs_secrets=False)
    app.include_router(module_router(server_update_routes, server_updates, require_owner))
    if notices_feed.enabled:
        scheduler.add_every("notices", NOTICES_REFRESH_SECONDS, notices_feed.refresh, needs_secrets=False)

    # storage warnings (storage_watch.py): 80% / 95% of the cap within the owner's waking hours, a critically low disk on the server
    # machine at once; a write that fails because the disk is full answers 507 with words the apps show
    storage_watch = StorageWatch(store, quota, activity.notify, awake=lambda ts: proactive.awake(ts) or proactive.owner_live(ts))
    app.state.storage_watch = storage_watch
    if quota is not None:
        quota.listeners.append(storage_watch.mark_dirty)
        scheduler.add_every("storage_watch", 60, storage_watch.tick, needs_secrets=False)

    def disk_full_answer() -> JSONResponse:
        storage_watch.mark_dirty()
        return JSONResponse({"detail": disk_full_text(), "code": DISK_FULL_CODE}, status_code=507)

    @app.exception_handler(sqlite3.OperationalError)
    async def sqlite_error(request: Request, exc: sqlite3.OperationalError) -> JSONResponse:
        if disk_full(exc) and request.scope.get("type") == "http":
            return disk_full_answer()
        raise exc

    @app.exception_handler(OSError)
    async def os_error(request: Request, exc: OSError) -> JSONResponse:
        if disk_full(exc) and request.scope.get("type") == "http":
            return disk_full_answer()
        raise exc
    app.include_router(module_router(storage_cleanup_routes, cleanup, require_owner))

    # follow-ups (followups.py): a reminder when a login, a job, an approval or the agent's question waits for the user
    async def deliver_followup(item: dict[str, Any], text: str) -> int:
        mid = store.add_message("assistant", text)
        activity.notify("followup", (store.profile().get("agent_name") or "Kelo")[:60], text[:400], task_id=item.get("task_id"),
                        approval_id=item.get("approval_id"), followup_id=item["id"])
        return mid

    def schedule_since(sid: int, since: float) -> str:
        if schedules.get(sid) is None:
            return "gone"
        return "ran" if store._all("SELECT 1 FROM schedule_runs WHERE schedule_id = ? AND started_at > ? LIMIT 1", (sid, since)) else ""

    async def retry_expired(item: dict[str, Any]) -> dict[str, Any]:
        """POST /api/followups/{id}/retry: start the run whose approval ran out again; its step asks for approval again.
        ValueError (-> 409) when it cannot be."""
        origin, note = item["origin"], item["note"]
        if origin["kind"] == "worker":
            wid = str(origin.get("worker_id") or "")
            w = workers.get(wid)
            if w is None:
                raise ValueError("that background job is gone")
            if w["status"] == "running":
                ok, how = workers.message(wid, note)
            else:
                ok, how = workers.resume(wid, why=note, auto=True)
            if not ok:
                raise ValueError(f"the background job could not go on ({how})")
            return {"how": f"worker_{how}", "worker_id": wid}
        if origin["kind"] == "schedule":
            sid = int(origin.get("schedule_id") or 0)
            s = schedules.get(sid)
            if s is None:
                raise ValueError("that automatic run is gone")
            if not scheduler.start_run(s, note=note):
                raise ValueError("this automatic run is running right now")
            for _ in range(20):   # its turn starts on the run's first step
                await asyncio.sleep(0)
                if (t := next((t for t in turns.values() if t.source == f"schedule:{sid}" and not t.done), None)) is not None:
                    return {"how": "schedule_started", "schedule_id": sid, "task_id": t.task_id, "turn_id": t.id}
            return {"how": "schedule_started", "schedule_id": sid, "task_id": None, "turn_id": None}
        owner = str(origin.get("owner") or "")
        if not owner or owner == "owner" or any(t.owner == owner for t in running_turns()):
            owner = f"task:{secrets.token_hex(4)}"
        try:
            turn = start_background(str(origin.get("prompt") or ""), title=str(origin.get("title") or ""),
                                    source=str(origin.get("source") or "background"), owner=owner,
                                    note=f"{origin.get('note') or BACKGROUND_NOTE}\n[Not from the user: {note}]",
                                    saved_as=str(origin.get("saved_as") or ""), cond=str(origin.get("cond") or ""))
        except HTTPException as e:
            raise ValueError(str(e.detail)) from None
        return {"how": "background_started", "task_id": turn.task_id, "turn_id": turn.id}

    # ---- going on after a restart or a model outage (resume.py) ---------------------------------------------------------------
    def last_user_id(*args: Any, **kw: Any) -> Any:
        return turn_resume.last_user_id(appctx, *args, **kw)

    def resume_context(*args: Any, **kw: Any) -> Any:
        return turn_resume.resume_context(appctx, *args, **kw)

    def use_offer(*args: Any, **kw: Any) -> Any:
        return turn_resume.use_offer(appctx, *args, **kw)

    def plain_note(*args: Any, **kw: Any) -> Any:
        return turn_resume.plain_note(appctx, *args, **kw)

    def offer_continue(*args: Any, **kw: Any) -> Any:
        return turn_resume.offer_continue(appctx, *args, **kw)

    def continue_chat(*args: Any, **kw: Any) -> Any:
        return turn_resume.continue_chat(appctx, *args, **kw)

    def resume_task(*args: Any, **kw: Any) -> Any:
        return turn_resume.resume_task(appctx, *args, **kw)

    def resume_after_restart(*args: Any, **kw: Any) -> Any:
        return turn_resume.resume_after_restart(appctx, *args, **kw)

    async def model_reachable(*args: Any, **kw: Any) -> Any:
        return await turn_resume.model_reachable(appctx, *args, **kw)

    def limit_wait(*args: Any, **kw: Any) -> Any:
        return turn_resume.limit_wait(appctx, *args, **kw)

    def limit_urgent(*args: Any, **kw: Any) -> Any:
        return turn_resume.limit_urgent(appctx, *args, **kw)

    def limit_notices(*args: Any, **kw: Any) -> Any:
        return turn_resume.limit_notices(appctx, *args, **kw)

    async def model_wait_tick(*args: Any, **kw: Any) -> Any:
        return await turn_resume.model_wait_tick(appctx, *args, **kw)

    app.state.resume_after_restart, app.state.model_wait_tick = resume_after_restart, model_wait_tick
    scheduler.add_every("model_wait", 30, model_wait_tick)

    # ---- goals (goals.py): the agent starts on a new goal, and picks one up again when asked ------------------------------------
    def start_goal_work(*args: Any, **kw: Any) -> Any:
        return turn_goals.start_goal_work(appctx, *args, **kw)

    def goals_of_turn(*args: Any, **kw: Any) -> Any:
        return turn_goals.goals_of_turn(appctx, *args, **kw)

    def busy_goals(*args: Any, **kw: Any) -> Any:
        return turn_goals.busy_goals(appctx, *args, **kw)

    goal_queued: dict[asyncio.Future[None], int] = {}   # goal work that was started and is not over yet (it may still wait its turn)
    goal_book.busy = busy_goals

    def start_schedule_check(*args: Any, **kw: Any) -> Any:
        return turn_goals.start_schedule_check(appctx, *args, **kw)

    def link_schedule(*args: Any, **kw: Any) -> Any:
        return turn_goals.link_schedule(appctx, *args, **kw)

    def react_tool() -> Tool:
        async def react(a: dict[str, Any]) -> str:
            try:
                chosen = reaction_emoji(a.get("emoji"))
            except ValueError as e:
                return f"Error: {e}."
            turn = turns.get(str((current_turn.get() or {}).get("turn_id") or ""))
            if turn is None or turn.done:
                return "Error: there is no message of the user's to react to here."
            turn.reaction_choice = chosen
            # 2026-11-12: a reply already written got a second closing line after the reaction ("필요한 일 말씀해 주세요." then
            # "필요하실 때 말씀해 주세요")
            return (f"{chosen} goes on the user's message once you are done with it. If your reply is already written, end here: "
                    "add no second closing line.")
        return Tool("react", "Put one emoji on the user's message (it shows once you have finished with it).",
                    {"type": "object", "properties": {"emoji": {"type": "string", "description": "one emoji"}},
                     "required": ["emoji"]}, react)

    def tone_tool() -> Tool:
        """set_tone: the owner asks in the chat to be talked to casually or politely. Saved like the app's own setting (no card: it only
        changes how Kelo talks); the next turn's prompt line follows it."""
        async def set_tone(a: dict[str, Any]) -> str:
            tone = str(a.get("tone") or "").strip().lower()
            if tone not in TONES:
                return f"Error: tone must be one of: {', '.join(TONES)}."
            store.set_profile(tone=tone)
            return f"Saved: you now talk to the user in the {tone} tone ({TONE_WAYS[tone]}), from this reply on."
        return Tool("set_tone", "Change how you talk to the user, when they ask: friendly (casual, like a close friend; informal forms such "
                    "as Korean 반말) or polite (courteous; polite forms such as Korean 해요체). Saved for later chats too.",
                    {"type": "object", "properties": {"tone": {"type": "string", "enum": list(TONES)}}, "required": ["tone"]}, set_tone)

    def goal_tools() -> list[Tool]:
        def replies(turn_id: str, questions: list[dict[str, Any]], gid: int | None) -> None:
            pending_replies[turn_id] = (questions, gid)

        def touched(turn_id: str, gid: int) -> None:
            if (t := turns.get(turn_id)) is not None:
                t.goal_ids.add(gid)
        async def card_images(turn_id: str, urls: list[str]) -> list[dict[str, Any] | None]:
            return await chat_images.card_pictures(turn_id, urls, netguard_fetch) if chat_images.root is not None else [None] * len(urls)
        return goal_book.tools(current_turn.get, replies, lambda gid: schedules.list(gid), link_schedule, on_goal=touched,
                               card_images=card_images)

    followups = Followups(store, home=home, stream_fn=counted("followup"), model=lambda: journal_model(), key_args=lambda p: key_args(p),
                          deliver=deliver_followup, wake=lambda: proactive.window_at()[:2], next_wake=proactive.next_awake,
                          live=lambda t: proactive.owner_live(t),
                          busy=lambda: any(t.source in ("chat", "api") for t in running_turns()), workers=workers,
                          pending_approvals=lambda: [a for a in pending_approvals.values() if a.get("kind") != "human_check"],
                          login_state=lambda n: "gone" if not (c := store.credential(n)) else "saved" if c.get("secret") or transient.has(n) else "waiting",
                          schedule_next=lambda sid: (schedules.get(sid) or {}).get("next_run_ts"), schedule_since=schedule_since,
                          retry=retry_expired, user_marks=(HANDOFF_MARK, WATCH_MARK, GOAL_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK),
                          tone=lambda: store.profile()["tone"], entries=entries, action_state=action_ledger.state_of)
    entries.on_open = followups.entry_requested   # a fill-in card nobody fills is followed up (a login has its own item)
    # offers (offers.py): ready-to-run task cards. resume ones come from the open follow-ups, the nightly look-back adds up to 3, the agent
    # posts its own (offer_post); a tap starts a reply whose message is the offer's request and whose hidden note is Kelo's own plan
    offers = build_offers(store, followup_items=followups.items, config=proactive.config, tz=proactive.tz)
    app.state.offers = offers
    if reflection:
        reflection.offers = offers.extractor   # the nightly look-back asks for offers in its own request

    def begin_offer_turn(text: str, note: str, request: Request) -> str:
        """The owner tapped an offer: its request goes in as their message, the plan as a line for the model only (409 while a reply runs)."""
        if any(t.source in ("chat", "api") for t in running_turns()):
            raise HTTPException(409, "Kelo is still answering another message; try again when it has finished")
        provider, model, ctx, observe, started = prepare_talk(TalkIn(text=text, ai_consent="check"), request, note=note)
        return started(start_turn(provider, model, ctx.tail, observe, context=ctx)).id
    app.include_router(module_router(offer_routes, offers.service, require_auth, begin_offer_turn))
    # the morning briefing (briefing.py): weather, today's events and what may have been missed, once a day
    async def deliver_briefing(card: dict[str, Any], text: str, push: bool) -> int:
        mid = store.add_message("assistant", text)
        activity.notify("briefing", i18n.t("Morning briefing"), card.get("summary") or "", push=push)
        return mid

    briefing = Briefing(store, weather, proactive, home=home, followups=followups, mail=sources.mail_view(), calendar=events_ahead(WEEK_DAYS + 1),
                        sources=sources,
                        stream_fn=job_stream("briefing"), model=lambda: journal_model(), key_args=lambda p: key_args(p),
                        deliver=deliver_briefing, busy=lambda: any(t.source in ("chat", "api") for t in running_turns()),
                        language=lambda: store.meta("ui_lang") or "en", tone=lambda: store.profile()["tone"],
                        consent=lambda: consents.required(store.profile(), settings.provider) is None)   # no consent yet: no model call
    app.state.briefing = briefing

    def follow_tz() -> ZoneInfo:
        try:
            return ZoneInfo(store.profile().get("timezone") or "UTC")
        except Exception:
            return ZoneInfo("UTC")

    follow_store = FollowStore(store)
    upcoming = UpcomingExtractor(follow_store, tz=follow_tz, clock=follow_store.clock)   # chat items from the nightly look-back
    if reflection:
        reflection.upcoming = upcoming
    briefing.looking_after = upcoming.recent_titles
    follow_sources = [
        IncheonFlights(store=store, fetcher=netguard_fetch),
        AeroApiFlights(store=store, follow_store=follow_store, fetcher=netguard_fetch, notify=activity.notify),
        PageSource(watchers=watchers),
    ]
    follow_finder = ItemFinder(calendar=events_ahead(AHEAD_DAYS), tz=follow_tz())
    follow_planner = Planner(
        stream_fn=job_stream("follow_plan"),
        model=journal_model,
        key_args=lambda p: key_args(p),
        sources=follow_sources,
    )
    follow_runner = FollowRunner(
        follow_store,
        follow_finder,
        follow_planner,
        follow_sources,
        activity=activity,
        home=home,
        proactive=proactive,
        workers=workers,
        tz=follow_tz(),
    )
    app.state.follow_runner = follow_runner
    app.state.follow_store = follow_store
    if with_followup_tools or (with_followup_tools is None and not tools_given):
        tools = [*tools, *followups.tools(current_turn.get), *done_once.tools()]   # action_result: shown only while a result is unknown
    if with_goal_tools or (with_goal_tools is None and not tools_given):
        tools = [*tools, *goal_tools(), *comparison_tools(pending_comparisons, current_turn.get, emit_ui)]
    if with_react_tool or (with_react_tool is None and not tools_given):
        tools = [*tools, react_tool()]
    if with_tone_tool or (with_tone_tool is None and not tools_given):
        tools = [*tools, tone_tool(), proactive.ahead_tool(), briefing.tool(), *rechecks.tools(), *offers.tools()]   # the owner's own settings: no card
    if not tools_given:
        tools = [*tools, *follow_tools(follow_runner)]
    # tools on demand (agent/toolbox.py): the core set plus any tools the caller gave are always shown; the rest load with find_tools
    tool_core = frozenset(CORE_TOOLS | given_tool_names)
    loadable = [t.name for t in tools if t.name not in tool_core]
    if loadable and "find_tools" not in {t.name for t in tools}:   # nothing to load (a caller's own short list): no find_tools either
        tools = [*tools, find_tools_tool(lambda: loadable)]
    app.state.tools = tools   # every tool of a chat turn, shown or loadable
    app.state.tool_scopes = tool_scopes
    app.state.followups = followups
    scheduler.add_every("followups", 60, followups.tick)
    scheduler.add_every("cred_keyring", 60, lambda: cred_keyring.sync(store, keyring))
    app.include_router(module_router(followup_routes, followups, require_auth))

    scheduler.add_every("briefing", 60, briefing.tick)
    app.include_router(module_router(briefing_routes, briefing, require_auth))
    app.include_router(module_router(sources_routes, sources, require_auth))
    scheduler.add_every("follow", 60, follow_runner.tick)
    app.include_router(module_router(follow_routes, follow_runner, require_auth))
    if checklist:
        app.include_router(module_router(checklist_routes, checklist, require_auth, lambda: (
            scheduler._every_last["checklist"] + checklist.period()) if "checklist" in scheduler._every_last else None))

    def stream_turn(turn: Turn) -> StreamingResponse:
        """Hand a reply to the page as a stream. A page that goes away stops it (a page that asks again and again, below, does not)."""

        async def events() -> AsyncIterator[str]:
            started, cursor = time.monotonic(), 0
            try:
                yield ": connected\n\n"   # the first bytes go out at once, so the page knows the line is up while the model thinks
                while True:
                    batch, done = await turn.wait(cursor, HEARTBEAT_SECONDS)
                    for item in batch:
                        yield sse(item)
                    cursor += len(batch)
                    if done:
                        break
                    if not batch:
                        yield ": keep-alive\n\n"   # a comment line: nothing for the app to read, but bytes keep moving
            finally:
                if not turn.done:   # the client went away: stop the work
                    log.info("reply stream closed early by the client after %.0fs (%d events sent)", time.monotonic() - started, cursor)
                    cancel_turn(turn)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    def turn_response(provider: str, model: str, messages: list[dict[str, Any]],
                      observe: Callable[[dict[str, Any] | None], None] | None = None,
                      effort: str | None = None, fallback: bool = True, source: str = "chat") -> StreamingResponse:
        return stream_turn(start_turn(provider, model, messages, observe, effort, fallback, source=source))

    def with_system(messages: list[dict[str, Any]], extra: str = "") -> list[dict[str, Any]]:
        if any(m.get("role") == "system" for m in messages):
            return messages
        return [{"role": "system", "content": settings.system_prompt + (extra or persona())}, *messages]

    def secret_check(text: str, choice: str | None) -> str:
        """The text a message may go on with: unchanged when it holds no secret (or the owner chose "allow" after the warning),
        with the secrets replaced for "mask", else 409 secret_in_message telling the app where they are (never what they are)."""
        hits = secret_guard.find(text, packages.key_patterns(), lambda cid: (lambda p: p.text("name") if p else "")(packages.get(cid)))
        if not hits or choice == "allow":
            if hits:
                log.info("message sent with a secret in it after the warning (%s)", ", ".join(secret_guard.kinds(hits)))
            return text
        placeholder = i18n.t(SECRET_PLACEHOLDER)
        if choice == "mask":
            return secret_guard.mask(text, hits, placeholder)
        log.info("message held back: it looks like it holds a secret (%s)", ", ".join(secret_guard.kinds(hits)))
        raise HTTPException(409, secret_guard.describe(text, hits, placeholder))

    app.state.secret_check = secret_check

    # the chat's AI consent book and the turn set-up shared by /api/talk, /api/turns and Nest screens (ai_consent.py)
    consents = ai_consent.ConsentBook(store.meta, store.set_meta)

    def persona(tz_name: str | None = None) -> str:
        """The per-turn lines (time, calendar, names, goals) for the short rules."""
        prof = store.profile()
        return "\n\n" + "\n".join(turn_lines(tz_name=tz_name or prof["timezone"], agent_name=prof["agent_name"],
                                              user_name=prof["user_name"], goals=goal_book.for_prompt(), schedules=schedules_for_prompt(),
                                              tone=prof["tone"]))

    def answers_note(answers: list[AnswerIn] | None) -> str:
        """The values of the quick-reply fields a message answers, checked against the open questions, as a line for the model
        ("" when there are none, or nothing is open any more: the words of the message still say it). 422 when a value does not fit."""
        if not answers:
            return ""
        questions = goal_book.open_questions()
        lines = []
        for a in answers:
            if a.question >= len(questions):
                continue
            q = questions[a.question]
            try:
                value = GoalBook.check_answer(q, a.value)
            except GoalError as e:
                raise HTTPException(422, f"answer {a.question + 1}: {e}") from None
            if q.get("kind"):   # a typed field (date, time, days...): its plain value next to the user's words
                lines.append(f"{q.get('question') or a.question + 1} = {value} ({q['kind']})")
        return f"[Not from the user: the exact values of the fields they filled in: {'; '.join(lines)}]" if lines else ""

    def prepare_talk(body: TalkIn, request: Request | None = None, *, owner: str | None = None, via: str = "", note: str = "") -> tuple[
            str, str, TurnContext, Callable[[dict[str, Any] | None], None], Callable[[Turn], Turn]]:
        """What a reply to the user's message needs: the model, the conversation (built per model when the turn runs), and what
        saves the answer (and then looks whether the conversation should be compacted before the next turn).
        `owner`: whose browser tab it drives (a request's device when not given); `via`: the messenger the message came through
        (stored with it and with the reply: "telegram"); `note`: a line for the model only (never stored), channels.CHANNEL_NOTE."""
        browser_owner.set(owner or str(request.state.device_id or "owner"))   # each phone drives its own browser tab, however many are connected
        provider, model = resolve_model(None, None)
        offer = activity.open_offer(last_user_id())   # a "Continue" button under the newest message (resume.py)
        tapped = offer if offer is not None and body.text.strip() == offer["label"].strip() else None
        if body.ai_consent == "check" and (need := consents.required(store.profile(), settings.provider)):
            raise HTTPException(428, need)   # the app asks the owner first; nothing was stored or sent
        form_note = answers_note(body.answers)   # before anything is saved: a value that does not fit is refused (422)
        body.text = secret_check(body.text, body.secrets)   # a pasted key or password stops here (409), before it is saved or sent
        try:   # the files sent with it must be the user's uploads (422 before anything is stored)
            attached = chat_attachments.check([a.path for a in body.attachments or []])
        except AttachmentError as e:
            raise HTTPException(422, str(e)) from None
        user_id = store.add_message("user", body.text, via=via)
        shown = chat_attachments.attach(user_id, attached, body.auto_text) if attached else []
        if body.secrets == "allow" and secret_guard.find(body.text, packages.key_patterns()):
            secret_guard.mark(store, user_id)   # sent as is after the warning: summaries and memory read it without the secret
        tz = valid_timezone(body.timezone)
        if tz:
            store.set_profile(timezone=tz)
        tz_name = tz or store.profile()["timezone"]
        text = body.text
        if body.text.strip().lower() in NEW_SESSION_WORDS:   # /new: the model starts a fresh conversation; the messages stay on screen
            store.start_session(user_id)
            text = NEW_SESSION_NOTE
        elif briefing.tapped_step(body.text):   # a next step from the briefing card, sent as is: the owner said yes
            text = f"{body.text}\n\n{STEP_NOTE}"
        if tapped is not None:   # the user tapped Continue: the turn that was cut off goes on
            use_offer(int(tapped["message_id"]))
            text = f"{body.text}\n\n{with_language(RESTART_NOTE if tapped['why'] == 'restart' else MODEL_NOTE, i18n.current())}"
        if note:   # the channel's line goes where the other notes go: the model reads it, the stored message does not
            text = f"{text}\n\n{note}"
        if form_note:
            text = f"{text}\n\n{form_note}"
        if entry_note := entries.take_notes():   # a fill-in card the user closed after the reply that asked had ended
            text = f"{text}\n\n{entry_note}"
        ctx = TurnContext(tz_name, [{"role": "user", "content": text}], user_id)
        said = ReplyText()   # a line that only led into a tool step is not kept as the answer of a stopped reply (reply_text.py)
        saved = False

        def save() -> None:
            nonlocal saved
            if not saved and said:
                store.add_message("assistant", collapse_repeat(said.text()), via=via)
            saved = True

        def observe(item: dict[str, Any] | None) -> None:
            if item is None:  # stream ended (finished or client left): keep what was said (reply_text.py)
                save()
                try:
                    after_turn(ctx)
                except Exception:  # noqa: BLE001 - never break the end of a reply
                    log.exception("after-turn compaction check failed")
                return
            said.feed(item)
            if item["type"] == "done":   # its tokens were counted per model call (usage.py)
                save()

        def started(turn: Turn) -> Turn:
            old = activity.resume_row(int(tapped["task_id"])) if tapped is not None else None
            restarts = int(old.get("restarts") or 0) + (1 if tapped and tapped["why"] == "restart" else 0) if old else 0
            keep_resume(turn, {"kind": "chat", "message_id": user_id}, restarts, old["id"] if old else None)
            turn.reaction_msg = user_id
            turn.read_pending = True   # 👀 once the model starts answering, not while the reply still waits for it (deliver below)
            if shown:   # the files the message carries, first in the reply's events (chat_attachments.py)
                turn.events.insert(0, {"type": "user_message", "message_id": user_id, "attachments": shown, "auto_text": body.auto_text})
            return turn

        return provider, model, ctx, observe, started

    def answer_approval(approval_id: str, approve: bool, remember: str | None = None) -> dict[str, Any] | None:
        """Answers a pending approval card (POST /api/approvals/{id}, and the Telegram buttons). None when it is not waiting any more."""
        fut = broker.pending.get(approval_id)
        if fut is None or fut.done():
            return None
        meta = pending_approvals.get(approval_id) or {}
        if meta.get("kind") == "location":   # the location card answered as an approval: Allow = the app already sent the place
            meta["answer"] = "allowed" if approve else "refused"
            broker.resolve(approval_id, approve)
            return {"ok": True, "decision": meta["answer"], "remembered": False, "rule": None}
        if meta.get("kind") == "human_check":   # Skip this site (approve=false), or an older app's Allow: "done, go on"
            meta["answer"] = "done" if approve else "skip"
            broker.resolve(approval_id, approve)
            return {"ok": True, "decision": "solved" if approve else "skipped", "remembered": False, "rule": None}
        decision, rule = "denied", None
        if approve:
            decision = "allowed_once"
            if remember == "site" and meta.get("can_remember"):
                rule = activity.add_rule(meta["name"], meta["site"], approval_id)
                decision = "allowed_always" if rule else decision
            elif remember == "schedule" and meta.get("can_remember_schedule"):
                rule = activity.add_schedule_rule(meta["schedule_id"], meta["name"], meta["site"], approval_id)
                decision = "allowed_always" if rule else decision
        activity.answer_approval(approval_id, decision)   # recorded before the waiting step goes on
        broker.resolve(approval_id, approve)
        return {"ok": True, "decision": decision, "remembered": rule is not None, "rule": rule}

    def with_running(s: dict[str, Any]) -> dict[str, Any]:
        return {**s, "running": scheduler.busy(s["id"])}

    if settings.review_mode:   # a review copy's profile never keeps a subscription sign-in (the stream refuses one anyway)
        _prof = store.profile()
        for _field, _model_field in (("provider", "model"), ("fallback_provider", "fallback_model"), ("background_provider", "background_model")):
            if _prof[_field] and unofficial_name(_prof[_field]):
                store.set_profile(**{_field: "", _model_field: ""})
                log.warning("review mode: stored %s %r is a subscription sign-in; cleared", _field, _prof[_field])
        # the operator set the model on the server (`assistant review model`): it is the reviewer's model from the first open, by name
        # (the apps then neither show the choose-a-model card nor an empty model chip)
        _prof = store.profile()
        if settings.provider and settings.model and not unofficial_name(settings.provider) and not _prof["provider"] and not _prof["model"]:
            store.set_profile(provider=settings.provider, model=settings.model)

    # every service and helper the routers below use, in one place; api/__init__.py includes them in their order
    appctx.add(consents=consents, prepare_talk=prepare_talk, answer_approval=answer_approval, require_auth=require_auth, resolve_model=resolve_model,
               secret_check=secret_check, settings=settings, start_turn=start_turn, store=store, stream_turn=stream_turn,
               turn_response=turn_response, with_system=with_system, activity=activity, deliverables=deliverables, nest=nest,
               phone_cals=phone_cals, require_owner=require_owner, cancel_turn=cancel_turn, turns=turns, turn_log=turn_log, avatar=avatar,
               consent_of=consent_of, provider_key=provider_key, provider_identity=provider_identity, unofficial_name=unofficial_name,
               unofficial_shown=unofficial_shown, briefing=briefing, broker=broker, browser_more=browser_more,
               chat_images=chat_images, chat_attachments=chat_attachments, compactor=compactor, deliveries=deliveries,
               ended_tabs=ended_tabs, goal_book=goal_book, handover_seen=handover_seen, journal_model=journal_model,
               owner_tasks=owner_tasks, pending_approvals=pending_approvals, pending_links=pending_links,
               pending_replies=pending_replies, pending_comparisons=pending_comparisons, comparisons=comparisons,
               resolved_checks=resolved_checks, running_turns=running_turns, session=session,
               state_root=state_root, tab_failed=tab_failed, thumbs=thumbs, tool_scopes=tool_scopes, window_for=window_for,
               workers=workers, goal_queued=goal_queued, goal_task_ids=goal_task_ids, scheduler=scheduler, schedules=schedules,
               start_goal_work=start_goal_work, with_running=with_running, extract_upload=extract_upload, home=home, beta=beta,
               gcal=gcal, github_web=github_web, google_web=google_web, keys=keys, mail=mail, mcp_conn=mcp_conn,
               packages=packages, tools=tools, beta_desk=beta_desk, tester_desk=tester_desk, broker_hits=broker_hits, calendars=calendars,
               device_of=device_of, phone_data=phone_data, login_flows=login_flows, meta_json=meta_json, usage_cache=usage_cache,
               device_flows=device_flows, entries=entries, mcp_fetcher=mcp_fetcher, schedule_grants=schedule_grants,
               transient=transient, apns=apns, beta_broker=beta_broker, client_key=client_key, fcm=fcm, kelo_names=kelo_names,
               push_broker=push_broker, report_box=report_box, report_broker=report_broker, reports=reports, wallet=wallet,
               auth=auth, push=push, require_master=require_master, cleanup=cleanup, images=images, quota=quota,
               storage_watch=storage_watch, background_model=background_model, balance_resolver=balance_resolver,
               balance_transport=balance_transport, limit_guard=limit_guard, models_client=models_client,
               payment_pins=payment_pins, payment_steps=payment_steps, pin_deliveries=pin_deliveries, pin_pads=pin_pads,
               shipping=shipping, shipping_forms=shipping_forms, task_grants=task_grants, weather=weather, followups=followups,
               held_by=held_by, turn_line=turn_line, start_schedule_check=start_schedule_check, auth_blocked=auth_blocked,
               auth_failed=auth_failed, hub=hub, start_background=start_background, sweep=sweep, live_sources=live_sources, journal=journal,
               job_stream=job_stream, key_args=key_args, skill_proposals=skill_proposals, memory_upkeep=memory_upkeep,
               reflection=reflection, downloads=downloads, nightly_review=nightly_review)
    channels = ChannelManager(
        store, activity, turns, broker, pending_approvals, prepare_talk, start_turn, cancel_turn, answer_approval, home,
        make={"telegram": lambda token: Telegram(token, fetch=channel_fetch or netguard_fetch),
              "discord": lambda token: Discord(token, fetch=channel_fetch or netguard_fetch, **({"connect": channel_connect} if channel_connect else {})),
              "slack": lambda token: Slack(token, fetch=channel_fetch or netguard_fetch, **({"connect": channel_connect} if channel_connect else {}))},
        note=CHANNEL_NOTE,
        talk_in=lambda text, files: TalkIn(text=text, attachments=[AttachmentIn(path=f["path"]) for f in files] or None, ai_consent="check"),
        # a group turn (channels/manager.py): the owner's model, the consent check, the three public tools, the privacy guard
        resolve_model=resolve_model, consent_needed=lambda: consents.required(store.profile(), settings.provider),
        group_tools=lambda: tools, privacy=privacy)
    appctx.add(channels=channels, live_activities=live_activities)
    app.state.channels, app.state.appctx = channels, appctx
    include_api(app, appctx)
    cli_statuses, cli_update_watch, prime_cli_statuses = appctx.cli_statuses, appctx.cli_update_watch, appctx.prime_cli_statuses
    device_provider = appctx.device_provider
    retire_tab, tab_sweeper = appctx.retire_tab, appctx.tab_sweeper
    appctx.add(TurnContext=TurnContext, activity=activity, approval_site=approval_site, broker=broker, chat_images=chat_images,
               compactor=compactor, covered_calls=covered_calls, run_progress=run_progress, deliveries=deliveries, entries=entries,
               fallback_model=fallback_model, followups=followups, goal_book=goal_book, goal_task_ids=goal_task_ids,
               goals_of_turn=goals_of_turn, grant_done_note=grant_done_note, handover_seen=handover_seen, key_args=key_args,
               learn_window=learn_window, meta_json=meta_json, orders=orders, owner_tasks=owner_tasks,
               pending_approvals=pending_approvals, pending_replies=pending_replies,
               pending_comparisons=pending_comparisons, comparisons=comparisons, persona=persona,
               pin_deliveries=pin_deliveries, provider_key=provider_key, provider_identity=provider_identity, reactions=reactions, resolved_checks=resolved_checks,
               retire_tab=retire_tab, schedule_grants=schedule_grants, scope_of=scope_of, session=session, settings=settings,
               stopping=stopping, storage_watch=storage_watch, store=store, stream_fn=stream_fn, sweep=sweep,
               tab_failed=tab_failed, task_closed=task_closed, task_grants=task_grants, task_namer=task_namer, tools=tools, turn_scopes=turn_scopes,
               turn_toolbox=turn_toolbox, turns=turns, turn_log=turn_log, window_for=window_for, workers=workers)   # what run_turn reads (turn_engine.py)
    appctx.add(TurnContext=TurnContext, keep_resume=keep_resume, pending_links=pending_links, resolve_model=resolve_model,
               start_turn=start_turn, store=store)   # what turn_engine.py reads
    appctx.add(TurnContext=TurnContext, activity=activity, background_folds=background_folds, continue_chat=continue_chat,
               counted=counted, fallback_model=fallback_model, goal_book=goal_book, journal_model=journal_model,
               keep_resume=keep_resume, key_args=key_args, last_user_id=last_user_id, limit_guard=limit_guard,
               limit_notices=limit_notices, limit_urgent=limit_urgent, limit_wait=limit_wait, model_reachable=model_reachable,
               offer_continue=offer_continue, pending_links=pending_links, plain_note=plain_note, proactive=proactive,
               resolve_model=resolve_model, resume_task=resume_task, running_turns=running_turns, schedule_grants=schedule_grants,
               scheduler=scheduler, schedules=schedules, start_background=start_background, start_turn=start_turn, store=store,
               use_offer=use_offer, worker_handoff=worker_handoff)   # what turn_resume.py reads
    appctx.add(background_folds=background_folds, goal_book=goal_book, goal_queued=goal_queued, journal_model=journal_model,
               schedule_check_on=schedule_check_on, schedules=schedules, turns=turns, worker_handoff=worker_handoff)   # what turn_goals.py reads
    # what turn_workers.py reads
    appctx.add(TurnContext=TurnContext, activity=activity, handoff_lock=handoff_lock, journal_model=journal_model,
               keep_resume=keep_resume, limit_guard=limit_guard, proactive=proactive, reactions=reactions,
               resolve_model=resolve_model, resume_context=resume_context, running_turns=running_turns,
               schedule_grants=schedule_grants, start_turn=start_turn, stopping=stopping, store=store, system_text=system_text,
               task_grants=task_grants, tool_scopes=tool_scopes, turn_scopes=turn_scopes, turns=turns, window_for=window_for,
               worker_limit_known=worker_limit_known, worker_limit_text=worker_limit_text, workers=workers)
    return app


def app_factory() -> FastAPI:
    return create_app(Settings.from_env())
