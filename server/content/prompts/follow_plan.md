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
  - preparations a person would do in prep entries with {"what", "when", "needs"} (e.g. online check-in opening, documents, leave-by time from the place).
- Only use check kinds that are available.
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
