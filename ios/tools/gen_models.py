"""Writes KeloCore's API models (Sources/KeloCore/Models/ApiModels.swift) from the compact table below.

Each model becomes a Swift struct that is Codable, Equatable, Hashable and Sendable, with
  - a memberwise init whose parameters all have the defaults below (except required ones),
  - a lenient init(from:) built on `Lenient`: a missing, null or mistyped field takes its default, numbers sent as strings and ids
    sent as numbers still read (the Android app's kotlinx settings: ignoreUnknownKeys, coerceInputValues, explicitNulls = false);
    a required field ("!") that is missing makes the whole entry unreadable, and lists leave unreadable entries out,
  - encode(to:) with the server's field names, so a model can be kept on the phone and read back.
The field names are the server's (src/assistant/server.py API comment blocks, src/assistant/api/*.py routers and their WALLET /
SHIPPING API blocks in api/wallet.py, followups.py, workers.py, watchers.py) as the
Android app reads them (android/kelo/app/src/main/java/chat/kelo/app/**/*Models.kt).

Run: python3 ios/tools/gen_models.py ios/KeloCore/Sources/KeloCore/Models/ApiModels.swift

Field spec: "swiftName:type[=default][@json_key]". Types: S I L D B (String Int Int64 Double Bool), a trailing ? for optional, a
trailing ! for required; C a country code (String, read in upper case); [S] list of strings, [Model] list of models, {S:S} / {S:L} / {S:Model} maps, [S]? a list that may be absent (nil), Model / Model? nested. The json key
defaults to the snake_case of the Swift name.
"""
import re
import sys

MODELS = {
    # ---- core ------------------------------------------------------------------------------------------------------------------
    "Profile": ("GET/PUT /api/profile. tone: friendly | polite (an older server sends none: polite).", [
        "userName:S", "agentName:S", "provider:S", "model:S", "fallbackProvider:S", "fallbackModel:S", "backgroundProvider:S",
        "backgroundModel:S", "effort:S", "timezone:S", "avatarVersion:L", "modelReady:B?", "minAppVersion:{S:L}",
        "tone:S=\"polite\""]),
    "Goal": ("GET /api/goals items. status is tracking | done.", [
        "id:L!", "title:S", "category:S", "note:S", "status:S=\"tracking\"", "schedulesOn:I"]),
    "Device": ("GET /api/devices items.", ["id:L!", "name:S", "createdAt:D", "lastSeen:D?", "owner:B"]),
    "DeviceList": ("GET /api/devices.", ["devices:[Device]", "current:L?"]),
    "PairStart": ("POST /api/devices/pair. link is \"https://host/#pair=CODE\" (missing only when the server knows no address).", [
        "code:S", "expiresIn:I", "link:S?"]),
    "About": ("GET /api/about.", ["version:S", "build:S"]),
    "OkAnswer": ("Any {\"ok\": bool} answer.", ["ok:B"]),
    "SeenAnswer": ("POST /api/tasks/seen, /api/notifications/seen.", ["ok:B", "marked:I", "unseen:I"]),
    # ---- chat ------------------------------------------------------------------------------------------------------------------
    "StoredMessage": ("GET /api/messages items. images: the screenshots Kelo showed with an agent reply (server chat_images.py).", [
        "id:L!", "role:S", "content:S", "createdAt:D", "images:[ChatImage]"]),
    "ChatImage": ("A screenshot Kelo showed in the chat: the turn event {\"type\": \"image\", \"image_id\", ...} and GET /api/messages \"images\". url is the server's own (/api/chat-images/{id}), read with the signed-in API; width and height in pixels (0 when unknown).", [
        "id:S", "url:S", "width:I", "height:I", "caption:S"]),
    "MessageFile": ("A file the user sent with a message (server chat_attachments.py): the turn event user_message \"attachments\" and GET /api/messages \"attachments\". kind image | video | audio | document; url and thumb are the server's own (/api/uploads/{name}/file, .../thumb; thumb null until the server has one), read with the signed-in API.", [
        "name:S", "path:S", "kind:S=\"document\"", "mime:S", "size:L", "url:S", "thumb:S?"]),
    "TurnStarted": ("POST /api/turns.", ["id:S"]),
    "ProviderConsentRecord": ("A provider's consent as stored.", ["acceptedAt:D?", "version:I"]),
    "ProviderInfo": ("GET /api/providers items, as the model picker reads them.", [
        "name:S!", "displayName:S", "configured:B?", "effortLevels:[S]", "effortNote:S?", "unofficial:B", "vendor:S",
        "consent:ProviderConsentRecord?", "consentVersion:I", "company:S?"]),
    "ModelList": ("GET /api/models?provider=. info: what the list says about each model (only the thinking level is read here).", [
        "provider:S", "models:[S]", "labels:{S:S}", "error:S?", "stale:B", "fetchedAt:L?", "info:{S:ModelEffort}"]),
    "ModelEffort": ("GET /api/models info entry, its thinking level: effortLevels nil = the list does not say (the provider's levels "
                    "apply), empty = none; effortNote with empty levels: why (a custom server that turned the level away).", [
        "effortLevels:[S]?", "effortNote:S?"]),
    "ConversationSummary": ("GET /api/conversation/summary (CONVERSATION COMPACTION in server.py).", [
        "summary:S", "upToMessageId:L?", "updatedAt:D?", "messagesFolded:I", "compactions:I", "memoryFlushes:I",
        "lastMemoryFlushAt:D?", "provider:S?", "model:S?", "contextWindow:I?", "maxOutput:I?", "source:S?", "threshold:D?",
        "lastRequestTokens:I?", "lastError:S?"]),
    # ---- agent status ------------------------------------------------------------------------------------------------------------
    "RunningRun": ("GET /api/agent/status running[] entries.", [
        "taskId:L?", "turnId:S?", "title:S", "status:S", "source:S", "waitingApproval:B", "tab:S?", "controller:S?"]),
    "AgentStatus": ("GET /api/agent/status, polled every few seconds in the foreground. tone: the profile's (nil = older server).", [
        "busy:B", "status:S?", "taskId:L?", "turnId:S?", "title:S?", "source:S?", "controller:S?", "controlledBy:S?",
        "running:[RunningRun]", "pendingApprovals:I", "openFollowups:I", "agentName:S?", "unseenTasks:I", "unseenNotifications:I",
        "avatarVersion:L=-1", "secretRequests:I", "secretKinds:{S:L}@secret_request_kinds", "tone:S?", "briefingRev:S?", "locked:B"]),
    "TaskBrief": ("GET /api/tasks?limit=1 items, only what tells a finished run from a failed one nobody has looked at.", [
        "id:L", "status:S", "seen:B=true"]),
    "TasksBrief": ("GET /api/tasks?limit=1.", ["items:[TaskBrief]"]),
    "WorkerBrief": ("GET /api/workers?status=needs_input items.", ["id:S", "status:S", "updatedAt:D?"]),
    "WorkersBrief": ("GET /api/workers?status=needs_input.", ["items:[WorkerBrief]"]),
    "BrowserTabBrief": ("GET /api/browser/sessions?thumbnails=false items, only what tells whether the agent works in a tab.", [
        "id:S", "status:S", "lastActivity:D?", "taskId:L?"]),
    "BrowserTabsBrief": ("GET /api/browser/sessions?thumbnails=false.", ["items:[BrowserTabBrief]"]),
    # ---- tasks and approvals -------------------------------------------------------------------------------------------------------
    "AgentTask": ("One reply (typed in chat, an automatic run, a background task). status: running | waiting_approval | done | failed | stopped; source: chat | api | schedule:<id> | background | worker | handoff | checklist | proactive.", [
        "id:L!", "turnId:S", "title:S", "status:S", "result:S", "source:S", "scheduleId:L?", "startedAt:D", "finishedAt:D?",
        "seen:B=true", "day:S", "liveStatus:S?", "turnAvailable:B", "parentId:L?", "workerId:S?"]),
    "TaskPage": ("GET /api/tasks?limit=50&before=<id>&source=.", ["items:[AgentTask]", "unseen:I", "nextBefore:L?"]),
    "ApprovalRecord": ("An approval as the history keeps it (never its arguments). decision: pending | allowed_once | allowed_always | allowed_by_rule | denied | expired | cancelled.", [
        "id:S!", "taskId:L?", "tool:S", "label:S", "site:S", "detail:S", "decision:S", "askedAt:D", "answeredAt:D?", "ruleId:L?",
        "unattended:B", "canRemember:B"]),
    "TaskDetail": ("GET /api/tasks/{id}: the task with its answer and the approvals it asked for.", [
        "id:L!", "turnId:S", "title:S", "status:S", "result:S", "source:S", "startedAt:D", "finishedAt:D?", "liveStatus:S?",
        "answer:S", "error:S", "inputTokens:L", "outputTokens:L", "approvals:[ApprovalRecord]", "parentId:L?", "workerId:S?"]),
    "ApprovalHistory": ("GET /api/approvals/history.", ["items:[ApprovalRecord]", "nextBefore:D?"]),
    "ApprovalRule": ("A standing \"allow on this site\" rule. kind: browser_action | login.", [
        "id:L!", "kind:S", "kindLabel:S", "site:S", "createdAt:D", "uses:I", "lastUsed:D?"]),
    "RuleList": ("GET /api/approvals/rules.", ["items:[ApprovalRule]"]),
    "ApprovalAnswer": ("POST /api/approvals/{id} {approve, remember?: site | schedule}.", ["ok:B", "decision:S", "remembered:B"]),
    # ---- schedules -------------------------------------------------------------------------------------------------------------
    "LastRun": ("A schedule's last run. status: done | quiet | failed | stopped | paused | missed.", ["taskId:L?", "status:S", "at:D?"]),
    "Schedule": ("A saved automatic run. kind: cron | window (cron +- window_minutes) | interval (every_minutes) | once (run_at).", [
        "id:L!", "name:S", "prompt:S", "kind:S=\"cron\"", "cron:S?", "runAt:S?", "everyMinutes:I?", "startAt:S?", "windowMinutes:I",
        "timezone:S", "enabled:B=true", "description:S", "nextRun:S?", "nextRunTs:D?", "createdAt:D?", "updatedAt:D?",
        "createdBy:S=\"user\"", "goalId:L?", "lastRun:LastRun?", "failureCount:I", "disabledReason:S?", "running:B", "paused:B",
        "pausedReason:S?", "pausedAt:D?", "archivedAt:D?", "purgeAt:D?"]),
    "ScheduleList": ("GET /api/schedules?goal_id=.", ["items:[Schedule]", "timezone:S"]),
    "ScheduleRun": ("One run of a schedule. status: running | done | quiet | failed | stopped | skipped | paused.", [
        "id:L", "scheduleId:L?", "startedAt:D", "finishedAt:D?", "status:S", "result:S", "taskId:L?", "error:S?"]),
    "ScheduleRuns": ("GET /api/schedules/{id}/runs.", ["items:[ScheduleRun]"]),
    "ScheduleArchive": ("GET /api/schedules/archive: deleted jobs, restorable for keep_days.", ["items:[Schedule]", "keepDays:I=30"]),
    "ScheduleRule": ("\"Keep allowing this in that scheduled job\" (site \"\" = a step on no site).", [
        "id:L!", "scheduleId:L?", "tool:S", "label:S", "site:S", "createdAt:D", "uses:I", "lastUsed:D?"]),
    "ScheduleRules": ("GET /api/schedules/{id}/rules.", ["items:[ScheduleRule]"]),
    "ScheduleGrant": ("What a schedule may do without asking, allowed once at setup (schedule_grants.py). kind: login (login, login_label) | purchase (what, caps).", [
        "id:L!", "scheduleId:L", "scheduleName:S", "scheduleWhen:S", "kind:S", "site:S", "createdAt:D", "uses:I", "lastUsed:D?",
        "login:S", "loginLabel:S", "what:S", "maxAmount:D", "currency:S", "monthlyCap:D", "runsPerMonth:I", "maxAmountText:S",
        "monthlyCapText:S", "spentThisMonth:D", "scope:S=\"schedule\"", "scopeRef:S", "taskName:S", "trigger:S", "action:S", "once:B",
        "urgent:B", "maxCount:I", "used:I", "cardLabel:S?", "cardLast4:S?", "targets:[S]", "template:S", "allowDelete:B"]),
    # ---- the morning briefing (briefing.py): the card on its chat message (GET /api/messages "briefing") ----------------------------
    "BriefingPlace": ("Where the weather is for: the town the owner's phone was last in.", ["name:S"]),
    "BriefingNote": ("A weather note. kind: rain_start | rain_now | snow_start | snow_now | sleet_start | sleet_now | temp_drop | wind | heat | cold | air.", [
        "kind:S", "text:S", "at:D?"]),
    "BriefingWeather": ("unit: C | F; headline: \"구름 조금 · 최고 11° · 최저 4°\"; attribution: the data source's credit line, linked to attributionUrl.", [
        "high:I?", "low:I?", "unit:S=\"C\"", "sky:S", "headline:S", "notes:[BriefingNote]", "provider:S", "attribution:S",
        "attributionUrl:S", "fallback:B", "id:S"]),
    "BriefingOpen": ("Where an item opens. kind: followup (id, message_id) | job (worker_id, task_id) | goal (id) | event (start) | mail (account, uid, subject).", [
        "kind:S", "id:L?", "messageId:L?", "workerId:S?", "taskId:L?", "start:D?", "account:S?", "uid:S?", "subject:S?"]),
    "BriefingEvent": ("One of today's events; source: which calendar, only when more than one is read.", [
        "title:S", "start:D", "end:D?", "place:S@where", "open:BriefingOpen?", "source:S", "id:S"]),
    "BriefingAhead": ("A big event in the coming week for the card's \"coming up\" line. kind: flight | stay | trip | days; first / last: its local days (YYYY-MM-DD).", [
        "title:S", "start:D", "kind:S", "first:S", "last:S", "open:BriefingOpen?", "id:S"]),
    "BriefingItem": ("A missed item. kind: followup | job | approval | login | mail | goal | promise; source: the mailbox, only when several are read.", [
        "kind:S", "title:S", "detail:S", "open:BriefingOpen?", "source:S", "id:S"]),
    "BriefingHours": ("A learned contact-hours suggestion still open, offered at the bottom of the card (accepted or kept like on 기록).", [
        "id:S", "dayType:S=\"weekday\"", "wakeStart:S", "wakeEnd:S", "currentStart:S", "currentEnd:S"]),
    "BriefingLink": ("A note the model made across the card's sources (connections) or a next step phrased as a question (suggestions); refs: ids of items on the card (weather \"w\", events \"e1\", ahead \"a1\", missed \"m1\"); a connection has at least one, a suggestion may have none.", [
        "text:S", "refs:[S]"]),
    "BriefingCard": ("The morning briefing's card: the weather, today's events and what may have been missed; words in the app's language, times unix seconds.", [
        "day:S", "summary:S", "line:S?", "quiet:B", "place:BriefingPlace?", "weather:BriefingWeather?", "events:[BriefingEvent]",
        "ahead:[BriefingAhead]", "missed:[BriefingItem]", "hours:BriefingHours?", "headline:S?", "connections:[BriefingLink]",
        "suggestions:[BriefingLink]"]),
    # ---- the morning briefing's settings (GET/PUT /api/briefing, briefing.py; weather.py) ----------------------------------------
    "BriefingPlaceInfo": ("Where a phone of the owner's last was (weather.py \"here\"): what the weather is for.", [
        "name:S", "lat:D", "lon:D", "countryCode:S", "at:D?"]),
    "WeatherProviderInfo": ("A weather service the server can use.", ["id:S", "name:S", "needsKey:B", "attribution:S", "attributionUrl:S"]),
    "BriefingWeatherSettings": ("provider: auto | open_meteo | kma | weatherapi; keys: which own keys are saved.", [
        "provider:S=\"auto\"", "here:BriefingPlaceInfo?", "keys:{S:B}",
        "providers:[WeatherProviderInfo]"]),
    "BriefingSettings": ("GET /api/briefing. time_mode: auto (start of the day's hours, or the morning's first open) | fixed; quiet_days: short | skip.", [
        "enabled:B=true", "timeMode:S=\"auto\"", "fixedTime:S=\"07:30\"", "offsetMin:I", "earlyOpen:B=true", "sections:{S:B}",
        "quietDays:S=\"short\"", "nextAt:D?", "weather:BriefingWeatherSettings"]),
    "SourceAccount": ("One account the briefing and the heads-ups can read (GET /api/briefing/sources). kind: calendar | mail; problem: the quiet line when it could not be read.", [
        "id:S", "kind:S=\"calendar\"", "provider:S", "name:S", "address:S", "label:S", "on:B=true", "problem:S?"]),
    "SourceGroup": ("One kind of account: every account on unless the owner switched it off; a new one starts on.", ["accounts:[SourceAccount]"]),
    "BriefingSources": ("GET/PUT /api/briefing/sources.", ["calendar:SourceGroup", "mail:SourceGroup"]),
    "CalDavAccount": ("GET /api/caldav accounts: a CalDAV calendar added with its own address (iCloud, Yahoo and Naver come with their mail).", [
        "name:S", "address:S", "url:S"]),
    "CalDavAccounts": ("GET /api/caldav.", ["accounts:[CalDavAccount]"]),
    "PaymentPin": ("GET /api/payment-pins items: a saved payment PIN as the server knows it (never the PIN; payment_pins.py).", [
        "id:S!", "site:S", "label:S", "digits:I", "e2eAlg:S", "e2eKeyId:S", "createdAt:D", "updatedAt:D", "lastUsed:D?", "uses:I"]),
    "PaymentPinList": ("GET /api/payment-pins.", ["items:[PaymentPin]"]),
    "PinSaved": ("POST /api/payment-pins: the saved PIN, whether it replaced one, and the reply it started (a fill-in card's answer).", [
        "pin:PaymentPin?", "replaced:B", "turnId:S?"]),
    "TaskGrantLogin": ("A task setup card's login part.", ["label:S", "site:S"]),
    "TaskGrantCard": ("A task setup card's payment card: its own auto-pay limit next to the amount asked for.", [
        "label:S", "last4:S", "autoPay:B", "autoLimit:D?", "autoLimitText:S", "phoneOnly:B"]),
    "TaskGrantPurchase": ("A task setup card's purchase part (max_count 0 = no count limit).", [
        "site:S", "what:S", "maxAmount:D", "currency:S", "maxAmountText:S", "maxCount:I", "monthlyCapText:S", "card:TaskGrantCard?",
        "shipping:S"]),
    "TaskGrantSend": ("A task setup card's message part: recipients, the wording ({braces} vary).", [
        "to:[S]", "site:S", "template:S", "varies:S", "maxCount:I"]),
    "TaskGrantChange": ("A task setup card's change part.", ["what:S", "places:[S]@where", "allowDelete:B", "maxCount:I"]),
    "TaskGrantAsk": ("approval_request.grant: the setup card of a \"when X happens, do Y\" task (task_allow, schedule_grants.py).", [
        "scope:S", "taskName:S", "taskWhen:S", "trigger:S", "action:S", "once:B", "urgent:B", "missed:S", "login:TaskGrantLogin?",
        "purchase:TaskGrantPurchase?", "send:TaskGrantSend?", "change:TaskGrantChange?"]),
    "ScheduleGrantList": ("GET /api/schedules/{id}/grants (DELETE /api/schedules/{id}/grants/{grant_id} takes one back).", [
        "items:[ScheduleGrant]"]),
    "RunStarted": ("POST /api/schedules/{id}/run (409 = already running); follow with GET /api/turns/{turn_id}.", [
        "ok:B", "taskId:L?", "turnId:S?"]),
    # ---- watchers --------------------------------------------------------------------------------------------------------------
    "DryRun": ("A watcher's check without the agent (POST /api/watchers/{id}/dry-run).", [
        "at:D?", "ok:B", "satisfied:B", "value:S?", "detail:S?", "error:S?", "url:S?", "title:S?"]),
    "Watcher": ("A cheap condition check that wakes the agent only when it holds. kind: page | script; mode: text_appears | text_disappears | number_above | number_below | changed | script; state: off | watching | error | done.", [
        "id:L!", "name:S", "kind:S=\"page\"", "url:S?", "mode:S", "match:S?", "selector:S?", "regex:S?", "threshold:D?",
        "useBrowser:B", "code:S?", "note:S?", "everyMinutes:I", "enabled:B", "once:B", "state:S=\"off\"", "description:S",
        "lastValue:S?", "lastCheckedAt:D?", "nextCheckAt:D?", "lastFiredAt:D?", "fireCount:I", "errorCount:I", "lastError:S?",
        "disabledReason:S?", "dryRun:DryRun?", "canEnable:B", "createdAt:D?", "updatedAt:D?", "createdBy:S=\"user\""]),
    "WatcherList": ("GET /api/watchers.", ["items:[Watcher]", "limit:I"]),
    "WatcherEvent": ("GET /api/watchers/{id}/events items.", ["id:L", "at:D", "kind:S", "detail:S?", "value:S?"]),
    "WatcherEvents": ("GET /api/watchers/{id}/events.", ["items:[WatcherEvent]"]),
    "DryRunAnswer": ("POST /api/watchers/{id}/dry-run.", ["result:DryRun?", "watcher:Watcher?"]),
    # ---- browser tabs ----------------------------------------------------------------------------------------------------------
    "BrowserTab": ("One browser tab the agent keeps. kind: owner | device | schedule | task; status: working | idle | failed | ended.", [
        "id:S!", "label:S", "kind:S", "title:S", "url:S", "host:S", "status:S=\"idle\"", "statusText:S", "lastActivity:D?",
        "taskId:L?", "controller:S=\"agent\"", "controlledBy:S?", "viewers:I", "thumbnail:S?", "thumbnailWidth:I",
        "thumbnailHeight:I", "endedAt:D?"]),
    "BrowserTabs": ("GET /api/browser/sessions?thumbnails=true.", ["available:B", "items:[BrowserTab]"]),
    "BrowserJobStarted": ("POST /api/browser/sessions {prompt, url?}.", ["id:S", "taskId:L?", "turnId:S?"]),
    "LiveTicket": ("POST /api/browser/sessions/{id}/live-ticket: one use, 30 s, this tab and this device only.", [
        "ticket:S", "expiresIn:I=30"]),
    "ControlAnswer": ("POST /api/browser/sessions/{id}/control {take}.", ["id:S", "controller:S", "controlledBy:S?", "youControl:B"]),
    # ---- workers ---------------------------------------------------------------------------------------------------------------
    "Worker": ("A background job the agent handed off (GET /api/workers). kind: browser | research | general; status: running | needs_input | blocked | done | failed | stopped | interrupted; waiting_for: login | control | user.", [
        "id:S!", "kind:S", "title:S", "status:S", "parentTaskId:L?", "taskId:L?", "turnId:S?", "tab:S?", "startedAt:D",
        "updatedAt:D", "finishedAt:D?", "steps:I", "maxSteps:I", "deadlineAt:D?", "waitingFor:S?", "login:S?", "reason:S?",
        "statusText:S?", "lastUrl:S?", "thumbnail:S?"]),
    "WorkerList": ("GET /api/workers?limit=50&status=&parent_task_id=.", ["items:[Worker]", "running:I", "limit:I"]),
    "WorkerReport": ("A job's report.", ["status:S", "summary:S", "details:S", "keepTab:B", "at:D?"]),
    "WorkerLine": ("One of a job's last steps.", ["at:D", "text:S"]),
    "WorkerDetail": ("GET /api/workers/{id}: the job with what it was asked, its last steps and its report.", [
        "id:S!", "kind:S", "title:S", "status:S", "parentTaskId:L?", "taskId:L?", "turnId:S?", "tab:S?", "startedAt:D",
        "updatedAt:D", "finishedAt:D?", "steps:I", "maxSteps:I", "deadlineAt:D?", "waitingFor:S?", "login:S?", "reason:S?",
        "statusText:S?", "lastUrl:S?", "thumbnail:S?", "task:S", "url:S?", "files:[S]", "report:WorkerReport?",
        "lines:[WorkerLine]", "segments:I", "inputTokens:L", "outputTokens:L", "error:S?", "keepTab:B"]),
    "WorkerAnswer": ("POST /api/workers/{id}/resume|stop|message. delivered: delivered | resumed | queued.", [
        "ok:B", "delivered:S?", "worker:Worker?"]),
    # ---- journal, reflection, avatar, notifications ------------------------------------------------------------------------------
    "JournalEntry": ("One day of the journal.", ["id:L!", "date:S", "title:S", "summary:S", "createdAt:D"]),
    "JournalGap": ("A day not written yet (pending) or that could not be (failed).", ["date:S", "status:S", "reason:S"]),
    "JournalMonth": ("GET /api/journal?month=YYYY-MM.", ["month:S", "items:[JournalEntry]", "pending:[JournalGap]"]),
    "LookbackRun": ("The last nightly look-back: done | skipped | failed | pending. The last nightly look-back: status, why (`code`: no_messages, switched_off, in_use, no_model, model_error, bad_output; \"\" when done) and counts. `reason` is the server's log text (English, model notes): never shown.", ["at:D?", "status:S", "code:S", "added:I", "updated:I", "dropped:I", "goals:I", "held:I", "reason:S", "changed:B"]),
    "LearnedRule": ("One preference the nightly look-back learned. status: proposed | accepted | rejected; confidence 0..1.", [
        "id:L!", "rule:S", "evidence:S", "date:S", "confidence:D", "status:S=\"proposed\""]),
    "Lookback": ("GET /api/reflection (404 = the agent's files are off: the app hides the page).", [
        "enabled:B=true", "rules:[LearnedRule]", "lastRun:LookbackRun?", "guide:S"]),
    "AvatarSaved": ("PUT/DELETE /api/agent/avatar.", ["avatarVersion:L", "mime:S", "bytes:I", "width:I", "height:I"]),
    "ServerNotification": ("GET /api/notifications items. type: task_done | task_failed | approval_waiting | payment_auto | schedule_disabled | schedule_paused | watch_fired | watch_disabled | proactive | browser_check | followup.", [
        "id:L!", "type:S", "title:S", "body:S", "taskId:L?", "approvalId:S?", "scheduleId:L?", "paymentId:L?", "followupId:L?",
        "messageId:L?", "createdAt:D", "seen:B", "provider:S", "urgent:B"]),
    "NotificationPage": ("GET /api/notifications?after=<id>&limit=50.", ["items:[ServerNotification]", "unseen:I", "lastId:L"]),
    # ---- follow-ups ------------------------------------------------------------------------------------------------------------
    "FollowupSettings": ("GET/PUT /api/followups/settings (the waking hours are the proactive settings).", [
        "enabled:B=true", "firstMinutes:I=30@first_delay_min", "secondMinutes:I=180@second_delay_min", "wakeStart:S", "wakeEnd:S",
        "timezone:S", "awake:B?"]),
    # ---- connectors ------------------------------------------------------------------------------------------------------------
    "Connector": ("GET /api/connectors items. status: connected | disabled | coming_soon | available | browser | autofill | unsupported; connect: device | mcp | mail | key | browser | autofill | nil.", [
        "id:S!", "name:S", "description:S", "category:S", "status:S", "connect:S?", "item:S", "via:S", "site:S", "steps:[S]",
        "featured:B", "rank:I?", "featuredRegions:[S]"]),
    "KeyField": ("One field of a key form.", ["id:S!", "label:S", "secret:B", "placeholder:S"]),
    "KeyForm": ("GET /api/keys/{via}: a key or token made on the service.", [
        "id:S!", "name:S", "item:S", "help:[S]", "checked:B", "connected:B", "label:S", "fields:[KeyField]"]),
    "MailProvider": ("A mail provider.", ["id:S!", "name:S", "help:[S]"]),
    "MailAccount": ("A connected mail account.", ["name:S!", "provider:S", "label:S", "address:S"]),
    "MailProviders": ("GET /api/mail/providers.", ["providers:[MailProvider]", "accounts:[MailAccount]", "googleWeb:B"]),
    "OAuthScope": ("A scope choice of a device-flow provider.", ["id:S!", "label:S", "detail:S"]),
    "OAuthProvider": ("GET /api/oauth/providers items: a service connected by a code typed on its own page.", [
        "id:S!", "item:S", "name:S", "hasClientId:B", "connected:B", "account:S", "settingsUrl:S", "web:B", "help:[S]",
        "scopes:[OAuthScope]", "grantedScope:S"]),
    # ---- addresses and shipping (address.py, shipping.py) -------------------------------------------------------------------
    "AddressCountry": ("GET /api/address/countries items. `name` is English: the app shows the country in its own language for `code`.", [
        "code:C!", "name:S"]),
    "AddressCountries": ("GET /api/address/countries. `defaultCountry`: the country a new address starts with (the last address's, else the server's).", [
        "countries:[AddressCountry]", "defaultCountry:C@default"]),
    "AddressOption": ("One choice of a field that is picked from a list (US states, JP prefectures): `key` is stored, `name` is shown.", [
        "key:S!", "name:S"]),
    "AddressField": ("One row of an address form, in display order. `options` non-empty: picked from a list; `pattern`: the postal code's check (case-insensitive); `example`: the placeholder; `numeric`: a number keyboard.", [
        "key:S!", "label:S", "required:B", "autocomplete:S", "options:[AddressOption]", "pattern:S?", "example:S?", "numeric:B"]),
    "AddressPhone": ("An address form's phone row: one field with the calling code (\"+1\") as a fixed prefix.", [
        "label:S", "callingCode:S", "required:B=true"]),
    "AddressNote": ("An address form's delivery note row.", [
        "label:S", "required:B"]),
    "AddressLabels": ("The form's own words in the request's language: the country picker's label, the nickname's, and \"optional\".", [
        "country:S", "nickname:S", "optional:S"]),
    "AddressForm": ("GET /api/address/form/{CC}: the country's address form, labels in the request's language. `fields` are in display order.", [
        "country:S", "countryName:S", "lang:S", "fields:[AddressField]", "phone:AddressPhone", "note:AddressNote", "labels:AddressLabels", "format:S"]),
    "ShippingProfile": ("A saved shipping address (\"배송 정보\"): any country's address as structured fields, the phone as E.164, a nickname (\"집\", \"Office\"); one of them is the default. `formatted`: the country's own postal format with the name; `oneLine`: without the name.", [
        "id:S!", "label:S", "country:C", "name:S", "organization:S", "addressLine1:S@address_line1", "addressLine2:S@address_line2", "dependentLocality:S", "locality:S", "adminArea:S", "postalCode:S", "sortingCode:S", "phone:S", "note:S", "isDefault:B@default", "createdAt:D", "lastUsed:D?", "formatted:S", "oneLine:S"]),
    "ShippingProfiles": ("GET /api/wallet/shipping (the default one first).", [
        "profiles:[ShippingProfile]"]),
    "ShippingFormAnswer": ("POST /api/shipping-forms/{form_id}: `profile` is the saved one (nil when not saved); `formatted` the address in its country's format.", [
        "ok:B", "profile:ShippingProfile?", "formatted:S"]),
    "OAuthFlow": ("A device-flow attempt. status: pending | connected | denied | expired | error | cancelled.", [
        "id:S!", "provider:S", "status:S", "message:S", "account:S", "userCode:S", "verificationUri:S", "expiresIn:I"]),
    "OAuthApp": ("GET /api/oauth-apps/{provider}: the operator's OAuth app (the secret is never sent back).", [
        "clientId:S", "hasSecret:B", "isBroker:B", "callback:S"]),
    "UrlAnswer": ("An answer that is one address to open.", ["url:S"]),
    "NameAnswer": ("An answer that is one keychain name.", ["name:S"]),
    # ---- keychain (vault) ------------------------------------------------------------------------------------------------------
    "Placement": ("Where a key goes in a request.", ["type:S?", "header:S?", "param:S?", "via:S?"]),
    "VaultEntry": ("GET /api/vault items (never a secret). kind: login | email | api.", [
        "name:S!", "label:S", "kind:S", "hosts:[S]", "endpoint:S", "hasSecret:B", "autoAllow:B", "lastUsed:D?", "usernameHint:S?",
        "placement:Placement", "lastResult:S?", "lastResultAt:D?", "lastResultDetail:S", "scheduleGrants:[ScheduleGrant]"]),
    "VaultState": ("GET /api/vault.", ["locked:B", "mailCodesAuto:B=true", "items:[VaultEntry]", "pending:[VaultEntry]", "enteredOnce:[S]", "verifying:[S]"]),
    "VaultLogRow": ("GET /api/vault/log items.", ["ts:D", "item:S", "action:S", "detail:S", "ok:B=true"]),
    "NewLogin": ("POST /api/vault/logins.", ["name:S"]),
    # ---- wallet ----------------------------------------------------------------------------------------------------------------
    "WalletCard": ("A card as every wallet answer shows it: never a number or CVC. ask_reason: \"\" | not_auto | no_server_copy | paused | vault_locked | keyguard_off.", [
        "id:S!", "label:S", "brand:S=\"other\"", "last4:S@last4", "expMonth:I", "expYear:I", "holder:S", "postalCode:S", "createdAt:D",
        "autoPay:B", "serverCopy:B", "e2e:B@e2e", "e2eAlg:S@e2e_alg", "e2eKeyId:S@e2e_key_id", "usable:B", "expired:B", "lastUsed:D?",
        "paysAutomatically:B?", "askReason:S", "askReasonText:S", "isDefault:B@default"]),
    "WalletInfo": ("GET /api/wallet. auto_cards: cards that pay on their own now (nil from an older server).", [
        "paused:B", "cards:[WalletCard]", "timezone:S", "month:S", "autoCards:I?", "scheduleGrants:[ScheduleGrant]", "shipping:[ShippingProfile]"]),
    "WalletLimit": ("One row of GET /api/wallet/limits (defaults are nil for a currency the user added).", [
        "currency:S!", "perPayment:D", "monthly:D", "defaultPerPayment:D?", "defaultMonthly:D?", "custom:B", "spentThisMonth:D"]),
    "WalletLimits": ("GET /api/wallet/limits.", ["month:S", "timezone:S", "limits:[WalletLimit]"]),
    "WalletPayment": ("A payment that went ahead. kind: auto | approved.", [
        "id:L", "at:D", "kind:S", "site:S", "host:S", "summary:S", "amount:D", "currency:S=\"KRW\"", "amountText:S", "cardId:S",
        "cardLabel:S", "month:S", "seen:B=true", "taskId:L?", "source:S"]),
    "WalletPayments": ("GET /api/wallet/payments.", ["items:[WalletPayment]", "nextBeforeId:L?"]),
    "WalletNotifications": ("GET /api/wallet/notifications.", ["items:[WalletPayment]", "unseen:I"]),
    "E2eBlob": ("GET /api/wallet/cards/{id}/blob: the phone's own ciphertext.", ["blob:S!", "alg:S", "keyId:S"]),
    "PauseState": ("PUT /api/wallet/pause.", ["paused:B"]),
    # ---- my page ---------------------------------------------------------------------------------------------------------------
    "Tokens": ("Token counts.", ["inputTokens:L", "outputTokens:L"]),
    "UsageWindow": ("One limit window, e.g. \"Current session\" 42% used.", [
        "group:S?", "label:S", "usedPercent:I", "resetsAt:S?", "resetsText:S?"]),
    "Subscription": ("A provider's own limits.", ["provider:S", "windows:[UsageWindow]", "error:S?"]),
    "Usage": ("GET /api/usage (subscription is nil for providers that report no limits).", [
        "today:Tokens", "total:Tokens", "subscription:Subscription?"]),
    "Upload": ("GET /api/uploads items: a document the user uploaded (path \"uploads/<name>\").", ["path:S!", "bytes:L", "modified:D"]),
    "UploadText": ("GET /api/uploads/{name}/text.", ["name:S", "text:S", "truncated:B"]),
    "MemoryUse": ("Memory in bytes.", ["used:L?", "limit:L?"]),
    "DiskUse": ("Disk in bytes.", ["used:L", "limit:L?", "parts:{S:L}", "freeOnHost:L?"]),
    "HostCapacity": ("The computer's own capacity (shown as numbers when there is no cap).", [
        "memory:L?", "cpus:D?", "diskFree:L?", "diskTotal:L?"]),
    "Sizes": ("A size profile.", ["memory:L", "cpus:D", "disk:L"]),
    "StorageAlert": ("GET /api/resources \"alert\": level ok | warn (80% of the cap) | critical (95%); hostLow: the server's own disk is "
                     "almost full; top: the biggest part.", ["level:S=\"ok\"", "percent:I?", "hostLow:B", "top:S?"]),
    "Resources": ("GET /api/resources. Sizes are bytes.", [
        "memory:MemoryUse", "cpus:D?", "disk:DiskUse", "host:HostCapacity", "profile:S", "minimum:Sizes", "recommended:Sizes",
        "alert:StorageAlert", "canClean:B"]),
    "CleanupItem": ("GET /api/storage/cleanup item: date is epoch seconds; keepsText: an upload whose read text stays; trashed: in the "
                    "results' trash; kind: a backup's kind (update | memory | migration).", [
        "id:S!", "name:S", "bytes:L", "date:D", "keepsText:B", "trashed:B", "kind:S"]),
    "CleanupGroup": ("GET /api/storage/cleanup group: uploads | images | browser_history | results | backups.", [
        "id:S!", "name:S", "bytes:L", "count:I", "items:[CleanupItem]"]),
    "CleanupList": ("GET /api/storage/cleanup: what can safely be removed.", ["groups:[CleanupGroup]", "total:L", "olderThanDays:I=14"]),
    "CleanupResult": ("POST /api/storage/cleanup {items, confirm: true}.", [
        "removed:[S]", "skipped:[S]", "freed:L", "groups:[CleanupGroup]", "total:L"]),
    "CliStatus": ("A provider's command-line tool.", [
        "installed:B", "installable:B", "loggedIn:B?", "hint:S", "remote:B", "version:S?", "updateHint:S?", "updateAvailable:B", "latestVersion:S?", "updatable:B"]),
    "ProviderDetail": ("GET /api/providers items, everything the model page and the keychain's AI key page use. key_source: app | env | nil.", [
        "name:S!", "displayName:S", "oauth:B", "loggedIn:B?", "configured:B?", "cli:CliStatus?", "effortLevels:[S]", "effortNote:S?",
        "takesKey:B", "keySource:S?", "keyHint:S?", "takesUrl:B", "baseUrl:S?", "unofficial:B", "vendor:S",
        "consent:ProviderConsentRecord?", "consentVersion:I"]),
    # ---- push ------------------------------------------------------------------------------------------------------------------
    "PushStatus": ("GET /api/push/status. mode: local | broker | off.", [
        "enabled:B", "registered:B", "mode:S", "brokerReachable:B?", "brokerPush:B?"]),
    "PushRegistered": ("POST/DELETE /api/push/register.", ["ok:B", "push:B", "removed:B?"]),
}

SWIFT = {"S": ("String", '""'), "I": ("Int", "0"), "L": ("Int64", "0"), "D": ("Double", "0"), "B": ("Bool", "false")}
GETTER = {"S": "str", "I": "int", "L": "int64", "D": "double", "B": "bool"}
OPT = {"S": "optStr", "I": "optInt", "L": "optInt64", "D": "optDouble", "B": "optBool"}
KEYWORDS = {"default", "extension", "protocol", "operator"}


def snake(name):
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def parse_field(spec):
    m = re.fullmatch(r"(\w+):([^=@]+)(?:=([^@]+))?(?:@(\w+))?", spec)
    if not m:
        raise SystemExit("bad field " + spec)
    name, typ, default, key = m.groups()
    return name, typ, default, key or snake(name)


def field_code(name, typ, default, key):
    """(swift type, default expression or None for required, decode expression, encode statement)"""
    q = f'"{key}"'
    if typ == "[S]?":   # a list that may be absent: nil, not empty (the server says nothing vs. says none)
        return "[String]?", "nil", f"j.has({q}) ? j.strings({q}) : nil", f"try c.encodeIfPresent({name}, forKey: AnyKey({q}))"
    if typ.startswith("[") and typ.endswith("]"):
        inner = typ[1:-1]
        if inner == "S":
            return "[String]", default or "[]", f"j.strings({q})", f"try c.encode({name}, forKey: AnyKey({q}))"
        return f"[{inner}]", default or "[]", f"j.list({q}, of: {inner}.self)", f"try c.encode({name}, forKey: AnyKey({q}))"
    if typ.startswith("{"):
        inner = typ[3:-1]
        v = SWIFT[inner][0] if inner in SWIFT else inner   # {S:S} / {S:L}, or {S:Model} (a map of nested models)
        t = f"[String: {v}]"
        return t, default or "[:]", f"j.decode({q}, as: {t}.self) ?? [:]", f"try c.encode({name}, forKey: AnyKey({q}))"
    required = typ.endswith("!")
    optional = typ.endswith("?")
    base = typ.rstrip("!?")
    if base == "C":   # a country code, always upper case ("kr" from an older server reads as "KR")
        if required:
            return "String", None, f"try j.require({q}, j.optStr({q})).uppercased()", f"try c.encode({name}, forKey: AnyKey({q}))"
        return "String", default or '""', f"j.str({q}, {default or chr(34) * 2}).uppercased()", f"try c.encode({name}, forKey: AnyKey({q}))"
    if base in SWIFT:
        st, d = SWIFT[base]
        if optional:
            return st + "?", "nil", f"j.{OPT[base]}({q})", f"try c.encodeIfPresent({name}, forKey: AnyKey({q}))"
        if required:
            return st, None, f"try j.require({q}, j.{OPT[base]}({q}))", f"try c.encode({name}, forKey: AnyKey({q}))"
        return st, default or d, f"j.{GETTER[base]}({q}, {default or d})", f"try c.encode({name}, forKey: AnyKey({q}))"
    # a nested model
    if optional:
        return base + "?", "nil", f"j.decode({q}, as: {base}.self)", f"try c.encodeIfPresent({name}, forKey: AnyKey({q}))"
    return base, default or f"{base}()", f"j.decode({q}, as: {base}.self) ?? {default or base + '()'}", f"try c.encode({name}, forKey: AnyKey({q}))"


def model(name, doc, fields):
    parsed = [field_code(*parse_field(f)) + (parse_field(f)[0],) for f in fields]
    out = [f"/// {doc}", f"public struct {name}: Codable, Equatable, Hashable, Sendable {{"]
    for st, d, dec, enc, fname in parsed:
        out.append(f"    public var {fname}: {st}")
    out.append("")
    params = ", ".join(f"{fname}: {st}" + ("" if d is None else f" = {d}") for st, d, dec, enc, fname in parsed)
    out.append(f"    public init({params}) {{")
    for st, d, dec, enc, fname in parsed:
        out.append(f"        self.{fname} = {fname}")
    out.append("    }")
    out.append("")
    out.append("    public init(from decoder: Decoder) throws {")
    out.append("        let j = try Lenient(decoder)")
    for st, d, dec, enc, fname in parsed:
        out.append(f"        {fname} = {dec}")
    out.append("    }")
    out.append("")
    out.append("    public func encode(to encoder: Encoder) throws {")
    out.append("        var c = encoder.container(keyedBy: AnyKey.self)")
    for st, d, dec, enc, fname in parsed:
        out.append(f"        {enc}")
    out.append("    }")
    out.append("}")
    return "\n".join(out)


def main():
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        raise SystemExit(__doc__)
    parts = [
        "// GENERATED by ios/tools/gen_models.py from its model table: do not edit by hand.",
        "// Regenerate: python3 ios/tools/gen_models.py ios/KeloCore/Sources/KeloCore/Models/ApiModels.swift",
        "// Field names are the server's (src/assistant/server.py API blocks, followups.py, workers.py, watchers.py).",
        "// swiftlint:disable all",
        "import Foundation",
        "",
    ]
    for name, (doc, fields) in MODELS.items():
        parts.append(model(name, doc, fields))
        parts.append("")
    with open(sys.argv[1], "w") as f:
        f.write("\n".join(parts))
    print(f"wrote {len(MODELS)} models to {sys.argv[1]}")


main()
