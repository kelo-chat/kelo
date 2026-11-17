Plan how to watch and prepare for an upcoming item in the user's life.

Given an upcoming item, available check kinds and sources, and user preferences, decide what to watch and what to prepare ahead.
Output ONLY a JSON object with this structure:
```json
{
  "checks": [
    {
      "kind": "flight_status",
      "refs": {"flight": "KE123", "date": "2026-11-20"},
      "fields": ["gate", "departure_time", "status"],
      "from": "-3h",
      "until": "+30m",
      "every_minutes": 10
    }
  ],
  "prep": [
    {
      "what": "online check-in",
      "when": "-24h",
      "kind": "check_in",
      "needs": ["browser", "login:airline"]
    }
  ],
  "tell": {
    "lead": ["-1d", "-3h"],
    "on_change": true
  }
}
```

Rules:
- For every item, plan at least time-based help that needs no source:
  - lead reminders in tell.lead (e.g. ["-1d", "-3h"]).
  - preparations a person would do in prep entries with {"what", "when", "kind", "needs"} (e.g. online check-in opening, documents, leave-by time from the place). "kind" is "check_in" for an online check-in that opens before a trip and "other" for every other preparation.
- Only use check kinds that are available.
- A "weather" check reads the forecast for the item's place at the item's start hour (refs default to the item's place and start time;
  add "refs": {"place": ..., "at": ...} only when the weather that matters is elsewhere or at another hour, "at" as epoch seconds or an
  ISO 8601 time with offset). Fields: "rain_chance", "temperature", "condition", "warning". Use it only when the weather would change
  what the owner does (something outdoors, a journey, an outdoor reservation), with a window of up to 3 days before the start and an
  interval of 60 minutes or more, since forecasts change slowly. Watch the fields that matter for the item, not all four by default.
- A "parcel" check (only for an item of kind "parcel") reads the carrier's tracking page for the item's own tracking number; give no refs, window
  or interval (they are fixed: two weeks, hourly). It ends when the parcel is delivered.
- "from" and "until" are offsets relative to the item start time (e.g. "-3h", "+30m").
- "every_minutes" must be at least 5.
- Only return nothing ({"checks": [], "prep": [], "tell": {}}) for trivial items (a recurring daily event).
- Generic behaviour: never hardcode site, airline, or shop names.
- Return valid JSON only, without markdown explanation.

Upcoming item:
{item}

Available check kinds and sources:
{sources}

User preferences (NOTIFY_PREFS):
{notify_prefs}
