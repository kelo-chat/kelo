# Follow-through: Kelo looks after what is coming up

Status (2026-11-16):
- Phase 1: done (server: items, plan, runner with flight_status and page checks; notifications).
- Phase 2: partly. Done (server): the `weather` source (Open-Meteo, no key) and the AeroAPI monthly allowance (a setting, used/allowance in
  `GET /api/follow`, stop at 90 %, fallback to the other flight source or a "paused" check). Done (server): parcel tracking (`parcel` source: the carrier's tracking
  page read by the main model, no carrier or word list; shipping mails give the item). Open: re-planning on item changes, the operator page's usage line (the numbers are in `GET /api/follow` as `usage`).
- Phase 3: done (server: preparations with existing task rules, "done ahead" and problem notices, missing-login advance notice).
- Phase 4: open (apps: watching line and stop button in Work).

## Why

Today Kelo notices things (the hourly look reads new mail every hour and the calendar at its send times and says "this is coming up"), but it does
not look after them. Between the notice and the event nothing is watched: a gate that moves, a train that is late, a parcel that is
stuck, rain on the day of a picnic, a booking the restaurant changed. And when Kelo sees something it could prepare (check in, confirm a
booking, gather the documents a trip needs) it only suggests it.

Goal: for each thing coming up in the owner's life, Kelo decides once what could change and what can be done ahead, then watches the
changing parts cheaply until it is over, tells the owner the moment something changes, and does the preparation itself within the
permissions the owner already gave. A flight's gate change is one example of the general rule, not a feature of its own.

## Principles

- One model call per item to plan it; no model call to watch it. Repeated checks are code (an API or a page read), and a change
  message built from structured fields is a template. A model writes only when a change needs explaining (free text from a page).
- Free sources first. A paid source is used only with the owner's own key, with a usage counter that stops before its free allowance.
- Done, not suggested: preparation runs as a background task under the existing rules (`permissions.py`, `schedule_grants.py`,
  `done_once.py`); only what always asks (payments outside a grant, deletions) asks.
- Generic: nothing in the code names a site, an airline or a shop. A source is a class behind one interface.
- Quiet: only a change, a problem or a finished preparation is told. A check that finds nothing says nothing.

## The pieces (server/src/assistant/follow/)

```
follow/
  items.py      UpcomingItem, ItemFinder        what is coming up (calendar, booking mails, orders, goals with a date)
  plan.py       FollowPlan, Planner             one model call per item -> checks + preparations (JSON, validated)
  checks.py     Check, CheckKind, Reading       one thing to watch, its window and interval; a reading is a dict of fields
  sources/      Source (ABC) + one file each    flight_icn.py, flight_aeroapi.py, weather.py, parcel.py, page.py (wraps watchers)
  runner.py     FollowRunner                    the loop: due checks -> source.read -> compare -> notify / re-plan
  prep.py       PrepRunner                      preparations as background tasks with the item's grant
  store.py      FollowStore                     tables follow_items, follow_checks, follow_readings, follow_usage
  api.py        /api/follow routes
```

### UpcomingItem (items.py)

```python
@dataclass(frozen=True, slots=True)
class UpcomingItem:
    key: str                 # stable: "cal:<event id>", "mail:<message id>", "order:<order id>", "goal:<id>"
    kind: str                # flight | train | stay | dining | appointment | parcel | trip | deadline | other
    title: str
    starts_at: float         # epoch seconds (UTC)
    ends_at: float | None
    place: str               # free text as found (airport, address, shop)
    refs: dict[str, str]     # what identifies it outside: {"flight": "KE123", "date": "2026-11-20"}, {"tracking": "..."}, {"booking": "..."}
    source: str              # calendar | mail | order | goal
```

`ItemFinder.find(now) -> list[UpcomingItem]` reuses what exists: `big_events.kind()` for calendar events (flight, stay, trip, days),
the booking-confirmation reading the proactive pass already does for mail (its model output gets a `refs` object added), the order
ledger for parcels, goals with a due date. Pure regexes pull refs where they are plain (flight numbers `[A-Z0-9]{2}\d{1,4}`); the
mail read (`event_kinds.MailBookings`) also says when a mail is a shipping notice and copies its tracking number, carrier and tracking
link (each must appear in the mail text): that makes a `parcel` item, kept for `PARCEL_DAYS = 14` after the mail. Items within `LOOK_AHEAD_DAYS = 14` are kept; one already planned (same key and same start) is not
planned again.

### FollowPlan (plan.py)

One model call per new item (or when the item itself changed: new time, new place). The prompt (English, in `prompts/follow_plan.md`)
gives the item, the owner's NOTIFY_PREFS and the list of available check kinds and sources, and asks for JSON:

```json
{"checks": [{"kind": "flight_status", "refs": {"flight": "KE123", "date": "2026-11-20"},
             "fields": ["gate", "departure_time", "status"], "from": "-3h", "until": "+30m", "every_minutes": 10}],
 "prep":   [{"what": "online check-in", "when": "-24h", "needs": ["browser", "login:airline"]}],
 "tell":   {"lead": ["-1d"], "on_change": true}}
```

`Planner.validate()` drops checks whose kind has no source on this server, clamps intervals (`MIN_EVERY = 5` minutes, a window of at
most 3 days), and caps checks per item at 6 (a safety bound against a model loop, not a limit on the owner). An item with nothing to
watch and nothing to prepare is stored as "nothing to do" so it is not planned again.

### Check kinds and sources (checks.py, sources/)

```python
class Source(ABC):
    kind: ClassVar[str]                      # "flight_status", "weather", "parcel", "page"
    def available(self) -> bool: ...         # a key is set, or the source is free and reachable
    def covers(self, refs: dict[str, str]) -> bool: ...
    async def read(self, refs: dict[str, str]) -> Reading: ...   # Reading = {"fields": {...}, "at": ts, "ok": bool, "detail": str}
    def paused(self, refs) -> str: ...       # why it would cover refs but is stopped for the month (an allowance), else ""
    def significant(self, field, old, new) -> bool: ...   # is a changed field worth telling (a forecast ignores small moves)
    def usage(self) -> dict | None: ...      # this month's calls, for GET /api/follow
```

| kind | first sources | fields compared |
|---|---|---|
| flight_status | `IncheonFlights` (data.go.kr, free key, departures and arrivals at ICN); `AeroApiFlights` (owner's FlightAware key, personal tier, stops at its free allowance) | gate, terminal, scheduled/estimated time, status (delayed, cancelled, boarding) |
| weather | `WeatherSource`: Open-Meteo (no key, no card; the briefing's forecast and place lookup, weather.py) | rain_chance, temperature, condition, warning at the place and hour |
| parcel | `ParcelSource`: the carrier's tracking page (fetched like `page`), its text read by the main model (no word list, any carrier or language) | parcel: picked_up, in_transit, out_for_delivery, delivered (delivered ends the item) |
| page | `watchers.py` as it is (light / tab / page levels, signed-in reads) | the field its selector or regex picks |

Several sources of one kind that `covers()` the check are tried in list order at every reading; the first that answers is used, a source
that fails hands over to the next one. A source that is `paused()` is skipped. A source that needs a key reads it from the keyring like a
connector key (`keyring.py`); none of it is shown to the model.

`follow_usage` counts calls per source and month (`flight_aeroapi`, `weather_open_meteo`).

**Weather check.** Refs are `{"place": text, "at": epoch seconds or ISO 8601 with offset}`; the planner fills both from the item (its place
and start) unless the plan names another place or hour, and drops the check when the item has no place. The place is looked up by the
provider (the whole text, then its comma-separated parts from the last); the forecast is the hour containing `at` (Open-Meteo reaches 16
days). Fields: `rain_chance` (%), `precip_mm`, `temperature` (whole °C), `condition` (rain, snow, sleet, thunder, fog, cloudy, partly,
clear; shown as words in the owner's language), `warning` (comma-joined codes wind, heat, cold, thunder; Open-Meteo has no official
feed, so these come from the same numbers the briefing uses). A forecast moves with every update, so only a real change is told
(`significant`): rain chance by 20 points or more, temperature by 3 °C or more, a new condition or warning. A smaller move is not stored,
so slow drift adds up against the last told value and ends in one message. The change goes through the same compare and notify path as
every other kind (`follow_change`, template lines "Chance of rain 10% -> 70%"). Calls: one place lookup per place (cached) and one
forecast per place per 20 minutes.

**FlightAware allowance.** `Settings.aeroapi_allowance` (env `ASSISTANT_AEROAPI_ALLOWANCE`, a whole number, default 500, 0 = never call).
The default is the free amount of FlightAware's personal tier as published when this was written (500 result sets a month); an owner on
another plan sets the number from their plan, which applies at once. Every call is counted in `follow_usage` (`flight_aeroapi`, per UTC
month). At `ALLOWANCE_STOP = 0.9` of the allowance (450 of 500) the source is `paused`: no call goes out, the owner is told once for the
month (`follow_problem`), and the runner uses the next flight source that covers the flight (the Incheon one). When none is left the
check is not an error: it keeps its place, shows `paused: true` with a `note` ("Flight lookups are paused until next month ..."), counts
no errors, and reads again in the next month. The remaining 10 % absorbs calls that were under way at the limit; nothing is billed by
surprise. A key alone makes the source `available()` (plans made while paused stay valid); `paused()` is the allowance.

### FollowRunner (runner.py)

Runs in the server's scheduler next to the watcher loop. Every minute: checks whose window is open and whose `next_at` passed are read
(at most `PARALLEL = 4` at once). A reading is compared field by field with the last good reading:

- a field changed -> one notification of type `follow_change` with a template line per field ("Gate A11 -> A6", "Departure 14:05 ->
  14:50", "Status: cancelled"), title from the item; a cancelled or much-changed item is re-planned;
- an error -> retried at the next interval; 5 in a row stop that check and say so once (like watchers' MAX_ERRORS);
- the window closed -> the check ends quietly.

The next check is `every_minutes` x a random 0.8-1.2, like watchers. Times are stored in UTC and shown in the owner's time zone.

### PrepRunner (prep.py)

At each prep's `when`, a background task starts (the same path schedules and goals use: `workers.py`) with a short brief made from the
plan ("Do the online check-in for KE123 on 2026-11-20 with the saved airline login"). Its steps follow every rule a task follows today:
a login comes from the keychain or is asked for; a payment outside a grant asks; done_once stops repeats. When it finishes, the owner
gets one line ("Checked in for KE123; the boarding pass is in Files"). A prep that needs something missing (no login saved) tells the
owner once ahead of time instead of failing on the day.

### Notifications and the apps

- New notification types `follow_change`, `follow_done`, `follow_problem`. They MUST be added to `push.NOTE_TYPES` (otherwise the
  push is refused) and to the apps' known types (the apps already show unknown types with the server's title since 0.10.1).
- Items show in the Work screen's "coming up" list (phase 4: a "watching" line under the item with the fields being watched and a stop
  button). Until then the owner stops one by telling Kelo ("stop watching the flight"), which calls `follow_stop`.

### API (api.py) and agent tools

```
GET    /api/follow                  {"usage": [{"source", "month", "used", "allowance" | null, "stop_at" | null, "paused"}],   (calls this month)
                                     "items": [{"key", "kind", "title", "starts_at", "checks": [{"kind", "fields", "from", "until", "state", "last",
                                      "paused", "note"}],
                                      "prep": [{"what", "when", "state"}], "state": planned|watching|done|stopped}]}
POST   /api/follow/{key}/stop       stops its checks and preps
POST   /api/follow/{key}/replan     plans it again
```
Agent tools: `follow_list`, `follow_stop(key)`; planning itself is not a tool (the runner does it).

### Owner control

On by default for the proactive pass's calendars and mailboxes. The owner turns kinds off in words ("don't watch my parcels"), kept in
NOTIFY_PREFS like the proactive pass's other preferences. Quiet hours follow the proactive contact hours, except changes within
`NOW_WINDOW` of the item's start (a gate change at 6 am before a 7 am flight is told).

## Phases

1. Items + plan + runner with `flight_status` (IncheonFlights, then AeroApiFlights with the allowance counter) and `page` checks;
   notifications. Tests with fake sources and a fake clock. [DONE]
2. `weather` and `parcel`; re-planning on item changes; usage screen line in the operator page (calls per source this month). [PARTLY: weather source,
   parcel source and the AeroAPI allowance (setting, count, 90 % stop, fallback, paused check, `usage` in `GET /api/follow`) are done; re-planning on item changes and the operator page's line are open]
3. Preparations (inside runner.py) with the existing task rules; "done ahead" messages; missing-login advance notice. [DONE]
4. Apps: the watching line and stop button in Work (Android first, then desktop and iOS). [OPEN]

## Tests (server/tests/test_follow_*.py)

- ItemFinder: calendar flight with a flight number -> refs; a booking mail -> item; nothing found -> none.
- Planner.validate: unknown kinds dropped, intervals clamped, at most 6 checks, "nothing to do" kept.
- Runner: a gate change fires one follow_change with the template line; the same reading again is quiet; errors stop after 5; the
  window end stops the check; quiet hours hold a change except near the start.
- Usage: a source past 90 % of its allowance refuses and tells once (`test_follow_allowance.py`: the setting, the count per month, the
  stop point, fallback to the other source in both directions, the paused check and `usage` in the API).
- Weather (`test_follow_weather.py`, fake Open-Meteo HTTP): a reading is parsed; a rain change fires one `follow_change`; small moves are
  quiet and slow drift adds up; place lookup falls back to the address parts; the planner fills place and hour from the item.
- Parcel (`test_follow_parcel.py`, fake page and fake model): the status comes from the model's enum and nothing in code matches words; an
  unknown answer or no model is a failed reading; delivered ends the item and its checks; a shipping mail becomes a parcel item for 14
  days; the plan sets refs and window itself.
- Push: every follow_* type is in push.NOTE_TYPES.
