# Location: coordinates, places and the permission card

What the server does with the phone's location (backlog 24). Code: `src/assistant/location.py`, `weather.py` (`my_location`,
`set_here`), the card in `server.py` (`ask_location`, LOCATION REQUEST block), `api/approvals.py` (`POST /api/location-requests/{id}`).

## The phone's place

`POST /api/briefing/location {"lat", "lon", "name"?, "country_code"?, "accuracy"?, "sublocality"?}`: sent when the app opens (only the
newest is kept). `sublocality` is the neighbourhood (Korean "dong") from the phone's own geocoder; newer apps send it when they know it.

## Place names and nearby places (no key)

Provider: **Photon** by komoot (`photon.komoot.io`), OpenStreetMap data (ODbL), free, without a key, under fair use. Kelo uses it
lightly: coordinates are rounded to 3 decimals (about 110 m) before they leave the server, every answer is cached for 30 days
(at most 2000 entries, table `place_cache`), and one server sends at most one request a second. Every answer the agent gets carries
"© OpenStreetMap contributors". Names are kept at neighbourhood level (no street or house number).

`my_location` adds "On the map that is around <neighbourhood, district, city>" when the phone sent no `sublocality`.

## For the runtime agent: the near-coordinates place search tool (tools/)

The tool lives in `src/assistant/tools/` (runtime agent's area). What it can use from the server, in process:

- The user's place: `weather.here()` / the raw record behind `my_location` (`Weather._here_raw()`: `lat`, `lon`, `accuracy_m`,
  `at`, `name`, `sublocality`, `country_code`). Suggest a public `Weather.here_raw()` if you need it; ask the server agent.
- `weather.places` (a `location.PlaceLookup`):
  - `await places.place_name(lat, lon)` -> `{"name", "parts", "attribution"}` or `None`
  - `await places.search_near(query, lat, lon, limit=8)` -> `[{"name", "kind", "address", "lat", "lon", "distance_m"}]`, nearest
    first; `query` is a kind ("parking", "pharmacy") or a name, in any language.
- When no location is known, call the `location_ask` context variable (`location.location_ask.get()`, `await ask(reason)` ->
  "allowed" | "refused" | "expired" | "cancelled", `None` outside a followed chat reply) exactly as `my_location` does, then read
  the place again.

The server builds the tool list in `create_app` (`weather.tool()`, `weather.location_tool()`); a new tool is added there next to them.
