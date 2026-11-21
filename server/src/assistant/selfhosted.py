"""Apps the owner runs on their own network (Home Assistant, Immich, Paperless, ...): how each is connected.

Unlike the services in presets.py these have no fixed host: the owner types the address in the app's form (http://192.168.0.10:8123,
a name on the network, or a Tailscale name). The address is stored with the connection (placement["origin"]), only the owner's form
(keyconnect.KeyConnections.connect) can write it, and requests to it go through netguard.fetch_local, which refuses loopback and
link-local addresses. Each preset's `verify` is a GET path that must answer 200 before anything is saved.

Paths and headers are from each project's own API documentation (named in `api`).
"""

from __future__ import annotations

from .i18n import N_
from .presets import Field, KeyPreset

ADDRESS = "address"


def _addr(example: str) -> Field:
    return Field(ADDRESS, N_("Server address"), secret=False, placeholder=example)


def _token() -> Field:
    return Field("token", N_("API key / token"))


_ADDR_NOTE = N_("The address is the one you open the app with on your network, for example http://192.168.0.10:8123 (an address from Tailscale works too).")


def _get(url: str, mode: str = "state", **more: Any) -> dict[str, Any]:
    """A declared read of a self-hosted app: one GET through the connection's own call (situation_reads.py)."""
    return {"tool": "connector_get", "arguments": {"name": "{{connection}}", "url": url}, "mode": mode, **more}


# The situation read of each self-hosted app (data, see situation.md). Paths come from each app's documented API. Apps with no state
# that changes the owner's situation, and those whose path could not be confirmed, have no entry (the note says why).
SITUATION_READS: dict[str, dict[str, Any]] = {
    "home_assistant": _get("/api/logbook/{{since}}", "changes", select="", id=["entity_id", "when"],
                           fields={"when": "when", "entity": "entity_id", "name": "name", "state": "state", "message": "message"}, max_rows=50),
    "frigate": _get("/api/events?after={{since_unix}}&limit=20", "changes", id=["id"],
                    fields={"label": "label", "camera": "camera", "zones": "zones", "start_time": "start_time"}),
    "paperless": _get("/api/documents/?ordering=-added&page_size=10", "changes", select="results", id=["id"],
                      fields={"title": "title", "added": "added", "correspondent": "correspondent"}),
    # mealie, tandoor, grocy: the date-filtered meal-plan and stock paths could not be confirmed against their docs; no read (see the report)
    # firefly_iii: the transactions path and its start parameter are from the app's API; check them with a live instance before relying on them
    "firefly_iii": _get("/api/v1/transactions?limit=10&start={{since_date}}", "changes", select="data", id=["id"],
                        fields={"date": "attributes.transactions.0.date", "amount": "attributes.transactions.0.amount",
                                "currency": "attributes.transactions.0.currency_code", "what": "attributes.transactions.0.description"},
                        content=["finance"]),
    # miniflux, freshrss: unread news is context, not a reason to wake the look
    "miniflux": _get("/v1/entries?status=unread&limit=20&order=published_at&direction=desc", "changes", select="entries", id=["id"],
                     fields={"title": "title", "feed": "feed.title", "published_at": "published_at"}, wakes=False),
    "freshrss": _get("/api/greader.php/reader/api/0/stream/contents/reading-list?n=20&output=json&xt=user/-/state/com.google/read",
                     "changes", select="items", id=["id"], fields={"title": "title", "feed": "origin.title", "published": "published"},
                     wakes=False),
    "n8n": _get("/api/v1/executions?status=error&limit=10", "changes", select="data", id=["id"],
                fields={"workflowId": "workflowId", "startedAt": "startedAt", "status": "status"}),
    "uptime_kuma": _get("/metrics", "state", lines="^monitor_status\\{", max_rows=50),
    "beszel": _get("/api/collections/systems/records?fields=name,status", "state", select="items", fields={"name": "name", "status": "status"}),
    "adguard_home": _get("/control/status", "state", fields={"protection": "protection_enabled", "running": "running"}),
    "pihole": _get("/api/dns/blocking", "state", fields={"blocking": "blocking"}),
    "nextcloud": _get("/ocs/v2.php/apps/notifications/api/v2/notifications?format=json", "changes", select="ocs.data",
                      id=["notification_id"], fields={"app": "app", "subject": "subject", "datetime": "datetime"},
                      arguments={"name": "{{connection}}", "url": "/ocs/v2.php/apps/notifications/api/v2/notifications?format=json",
                                 "headers": {"OCS-APIRequest": "true"}}, content=["notifications"]),
    "gitea": _get("/api/v1/notifications?status-types=unread&limit=20", "changes", id=["id"],
                  fields={"title": "subject.title", "type": "subject.type", "repo": "repository.full_name", "updated_at": "updated_at"},
                  content=["notifications"]),
    "forgejo": _get("/api/v1/notifications?status-types=unread&limit=20", "changes", id=["id"],
                    fields={"title": "subject.title", "type": "subject.type", "repo": "repository.full_name", "updated_at": "updated_at"},
                    content=["notifications"]),
    # immich, linkwarden, karakeep, wallabag, actual_budget: saved items or a budget, nothing that changes the owner's situation by the hour
}


def _p(pid: str, name: str, example: str, help_: tuple[str, ...], blurb: str, api: str, placement: dict, verify: str, **kw) -> KeyPreset:
    fields = kw.pop("fields", (_addr(example), _token()))
    kw.setdefault("situation", SITUATION_READS.get(pid))
    return KeyPreset(pid, name, f"{pid.replace('_', '-')}-api", (), placement, (*help_, _ADDR_NOTE), blurb, api, fields=fields, verify=verify, local=True, **kw)


SELF_HOSTED: tuple[KeyPreset, ...] = (
    _p("home_assistant", "Home Assistant", "http://192.168.0.10:8123",
       (N_("In Home Assistant, open your profile (bottom left) → Security → Long-lived access tokens."),
        N_("Press Create token, give it a name and paste it here.")),
       N_("Sees your devices and rooms and switches lights, locks, heating and scenes."),
       "Home Assistant REST API (docs: developers.home-assistant.io, 'REST API'). Use connector_get / connector_send with a path on the "
       "address, e.g. GET /api/states, GET /api/states/<entity_id>, GET /api/services, POST /api/services/<domain>/<service> with a JSON body "
       "like {\"entity_id\": \"light.kitchen\"}, POST /api/template with {\"template\": \"...\"}. If the Model Context Protocol Server "
       "integration is added in Home Assistant, mcp_list_tools / mcp_call_tool also work (endpoint /api/mcp)."
       " For 'tell me when X happens' use ha_watch_add (live, no polling) instead of a watcher or a schedule.",
       {"type": "bearer_header"}, "/api/", endpoint="/api/mcp", device=True),
    _p("frigate", "Frigate", "http://192.168.0.10:5000",
       (N_("Frigate's port 5000 needs no key on your home network. Enter the address with that port."),),
       N_("Sees cameras, detected events and snapshots."),
       "Frigate HTTP API (docs: docs.frigate.video, 'HTTP API'). GET /api/version, GET /api/config, GET /api/events?limit=10, "
       "GET /api/<camera>/latest.jpg and GET /api/events/<id>/snapshot.jpg (pictures: save them with connector_download). Port 5000 is "
       "the unauthenticated port; the signed-in port 8971 uses its own https certificate and is not supported.",
       {"type": "none"}, "/api/version", fields=(_addr("http://192.168.0.10:5000"),)),
    _p("immich", "Immich", "http://192.168.0.10:2283",
       (N_("In Immich, open your profile picture → Account Settings → API Keys."), N_("Press New API Key, pick the permissions and paste it here.")),
       N_("Searches your photos and albums."),
       "Immich API (docs: api.immich.app). POST /api/search/metadata with a JSON body (e.g. {\"takenAfter\": \"2026-01-01T00:00:00.000Z\"}), "
       "POST /api/search/smart {\"query\": \"beach\"} (only when Immich's machine-learning container runs), GET /api/albums, GET /api/assets/<id>/thumbnail and GET /api/assets/<id>/original "
       "(pictures: save them with connector_download).",
       {"type": "custom_header", "header": "x-api-key", "prefix": ""}, "/api/users/me"),
    _p("paperless", "Paperless-ngx", "http://192.168.0.10:8000",
       (N_("In Paperless-ngx, open your user menu (top right) → My Profile."), N_("Press the circular arrow next to API Auth Token to make one and paste it here.")),
       N_("Searches your scanned documents and reads their text."),
       "Paperless-ngx REST API (docs: docs.paperless-ngx.com/api). GET /api/documents/?query=<text>, GET /api/documents/<id>/ (the 'content' "
       "field is the text), GET /api/documents/<id>/download/ (save it with connector_download), GET /api/correspondents/, /api/tags/.",
       {"type": "custom_header", "header": "Authorization", "prefix": "Token "}, "/api/documents/?page_size=1"),
    _p("mealie", "Mealie", "http://192.168.0.10:9925",
       (N_("In Mealie, open your profile → API Tokens."), N_("Make a token and paste it here.")),
       N_("Finds recipes and meal plans, and adds to your shopping lists."),
       "Mealie REST API (docs: docs.mealie.io/documentation/getting-started/api-usage; the server's own /docs page lists every endpoint). "
       "GET /api/recipes?search=<text>, GET /api/households/mealplans, GET /api/households/shopping/lists.",
       {"type": "bearer_header"}, "/api/users/self"),
    _p("tandoor", "Tandoor Recipes", "http://192.168.0.10:8080",
       (N_("In Tandoor, open Settings → API."), N_("Make an API token and paste it here.")),
       N_("Finds recipes and meal plans, and edits shopping lists."),
       "Tandoor REST API (docs: docs.tandoor.dev/features/api; the server's own /docs/api/ page lists every endpoint). "
       "GET /api/recipe/?query=<text>, GET /api/meal-plan/, GET /api/shopping-list-entry/.",
       {"type": "bearer_header"}, "/api/recipe/?page_size=1"),
    _p("grocy", "Grocy", "http://192.168.0.10:9283",
       (N_("In Grocy, open the user menu (top right) → Manage API keys."), N_("Add a new API key and paste it here.")),
       N_("Sees stock, shopping lists and chores."),
       "Grocy REST API (the server's own /api page, Swagger, lists every endpoint). GET /api/stock, GET /api/objects/shopping_list, "
       "GET /api/chores, POST /api/stock/products/<id>/add.",
       {"type": "custom_header", "header": "GROCY-API-KEY", "prefix": ""}, "/api/system/info"),
    _p("actual_budget", "Actual Budget", "http://192.168.0.10:5007",
       (N_("Actual Budget has no API of its own. Run the community project actual-http-api next to it (github.com/jhonderson/actual-http-api)."),
        N_("Use the API_KEY you set for it and paste it here, with the address of that program (port 5007 by default).")),
       N_("Reads accounts, balances and transactions of your budget."),
       "The community actual-http-api (github.com/jhonderson/actual-http-api): requests go to its address, the key goes in x-api-key. "
       "Paths are under /v1: GET /v1/budgets lists the budgets (groupId is the sync id), then for example GET /v1/budgets/<budgetSyncId>/accounts; "
       "its own page /api-docs/ lists every endpoint. Actual's sync id is also in Actual under Settings → Advanced settings. This connection "
       "is saved without a test request (the key is only checked on the budget paths).",
       {"type": "custom_header", "header": "x-api-key", "prefix": ""}, ""),
    _p("firefly_iii", "Firefly III", "http://192.168.0.10:8080",
       (N_("In Firefly III, open Options → Profile → OAuth."), N_("Under Personal Access Tokens press Create new token, and paste it here.")),
       N_("Sees accounts, transactions and budgets, and adds transactions."),
       "Firefly III API v1 (docs: docs.firefly-iii.org/references/firefly-iii/api). GET /api/v1/accounts, GET /api/v1/transactions?limit=20, "
       "GET /api/v1/budgets, POST /api/v1/transactions.",
       {"type": "bearer_header", "fixed_headers": {"Accept": "application/vnd.api+json"}}, "/api/v1/about/user"),
    _p("linkwarden", "Linkwarden", "http://192.168.0.10:3000",
       (N_("In Linkwarden, open Settings → Access Tokens."), N_("Make a token and paste it here.")),
       N_("Searches and saves your bookmarks."),
       "Linkwarden API (docs: docs.linkwarden.app/api). GET /api/v1/links?searchQueryString=<text>, GET /api/v1/collections, POST /api/v1/links.",
       {"type": "bearer_header"}, "/api/v1/collections"),
    _p("karakeep", "Karakeep", "http://192.168.0.10:3000",
       (N_("In Karakeep, open Settings → API Keys."), N_("Press New API Key, name it and paste it here.")),
       N_("Searches and saves your bookmarks and notes."),
       "Karakeep API (docs: docs.karakeep.app/API). GET /api/v1/bookmarks, GET /api/v1/bookmarks/search?q=<text>, GET /api/v1/lists, "
       "POST /api/v1/bookmarks with {\"type\": \"link\", \"url\": \"...\"}.",
       {"type": "bearer_header"}, "/api/v1/users/me"),
    _p("wallabag", "Wallabag", "http://192.168.0.10:8080",
       (N_("In Wallabag, open the menu → API clients management and create a new client."),
        N_("Paste its Client ID and Client secret here, with your Wallabag user name and password.")),
       N_("Searches and saves the articles you read later."),
       "Wallabag API (docs: doc.wallabag.org, 'API'). GET /api/entries.json?search=<text>, POST /api/entries.json with {\"url\": \"...\"}, "
       "GET /api/entries/<id>.json. The server signs in for you and renews the session.",
       {"type": "bearer_header"}, "/api/entries.json?perPage=1", login="wallabag",
       fields=(_addr("http://192.168.0.10:8080"), Field("client_id", N_("Client ID"), secret=False), Field("client_secret", N_("Client secret")),
               Field("username", N_("User name"), secret=False), Field("password", N_("Password")))),
    _p("miniflux", "Miniflux", "http://192.168.0.10:8080",
       (N_("In Miniflux, open Settings → API Keys."), N_("Press Create a new API key and paste it here.")),
       N_("Reads your news feeds and marks entries read."),
       "Miniflux API (docs: miniflux.app/docs/api.html). GET /v1/entries?status=unread&limit=20, GET /v1/feeds, "
       "PUT /v1/entries with {\"entry_ids\": [..], \"status\": \"read\"}.",
       {"type": "custom_header", "header": "X-Auth-Token", "prefix": ""}, "/v1/me"),
    _p("freshrss", "FreshRSS", "http://192.168.0.10:8080",
       (N_("In FreshRSS, open Settings → Authentication and turn on 'Allow API access'."),
        N_("Under Profile set an API password (it is not your normal password), then paste your user name and that API password here.")),
       N_("Reads your news feeds."),
       "FreshRSS Google Reader compatible API (docs: freshrss.github.io/FreshRSS/en/developers/06_GoogleReader_API.html), paths under "
       "/api/greader.php/reader/api/0: GET /subscription/list?output=json, GET /unread-count?output=json, "
       "GET /stream/contents/reading-list?n=20&output=json. The server signs in for you.",
       {"type": "custom_header", "header": "Authorization", "prefix": "GoogleLogin auth="}, "/api/greader.php/reader/api/0/user-info?output=json",
       login="freshrss",
       fields=(_addr("http://192.168.0.10:8080"), Field("username", N_("User name"), secret=False), Field("password", N_("API password")))),
    _p("n8n", "n8n", "http://192.168.0.10:5678",
       (N_("In n8n, open Settings → n8n API."), N_("Press Create an API key and paste it here.")),
       N_("Lists your workflows and their runs, and turns them on or off."),
       "n8n public API (docs: docs.n8n.io/api). GET /api/v1/workflows, GET /api/v1/executions?limit=10, "
       "POST /api/v1/workflows/<id>/activate. To run a workflow on request, connect 'n8n MCP' instead.",
       {"type": "custom_header", "header": "X-N8N-API-KEY", "prefix": ""}, "/api/v1/workflows?limit=1"),
    KeyPreset("n8n_mcp", "n8n MCP", "n8n-mcp-api", (), {"type": "bearer_header"},
              (N_("In n8n, add an 'MCP Server Trigger' node to a workflow, set Authentication to Bearer Auth with a token, and publish the workflow."),
               N_("Copy the node's Production URL and paste it here with the token. Pick the streamable HTTP transport if n8n offers a choice.")),
              N_("Runs the n8n workflows you chose to share."),
              "An n8n MCP Server Trigger: use mcp_list_tools to see the workflows it offers and mcp_call_tool to run one.",
              fields=(Field("mcp_url", N_("MCP Server Trigger URL"), secret=False, placeholder="http://192.168.0.10:5678/mcp/your-path"),
                      Field("token", N_("Token"))),
              local=True, endpoint_field="mcp_url"),
    _p("uptime_kuma", "Uptime Kuma", "http://192.168.0.10:3001",
       (N_("In Uptime Kuma, open Settings → API Keys."), N_("Press Add API Key, and paste the key here.")),
       N_("Sees which of your sites and services are up."),
       "Uptime Kuma has no REST API; its API key opens the Prometheus text page GET /metrics (docs: github.com/louislam/uptime-kuma/wiki, "
       "'Prometheus Integration'). Lines monitor_status{monitor_name=...} 1 mean up, 0 down, 2 pending, 3 maintenance.",
       {"type": "basic_auth", "user": ""}, "/metrics"),
    _p("beszel", "Beszel", "http://192.168.0.10:8090",
       (N_("Use the e-mail address and password you sign in to Beszel with."),),
       N_("Sees how your servers are doing: load, memory, disks and temperatures."),
       "Beszel runs on PocketBase (docs: beszel.dev; PocketBase API: pocketbase.io/docs). GET /api/collections/systems/records, "
       "GET /api/collections/system_stats/records?sort=-created&perPage=5&filter=(system='<id>'). The server signs in for you.",
       {"type": "custom_header", "header": "Authorization", "prefix": ""}, "/api/collections/systems/records?perPage=1", login="beszel",
       fields=(_addr("http://192.168.0.10:8090"), Field("username", N_("E-mail address"), secret=False), Field("password", N_("Password")))),
    _p("adguard_home", "AdGuard Home", "http://192.168.0.10:3000",
       (N_("Use the user name and password you sign in to AdGuard Home with."),),
       N_("Sees blocking statistics and turns filtering on or off."),
       "AdGuard Home API (docs: github.com/AdguardTeam/AdGuardHome/tree/master/openapi). GET /control/status, GET /control/stats, "
       "GET /control/querylog?limit=20, POST /control/protection with {\"enabled\": true|false}.",
       {"type": "basic_auth", "user": ""}, "/control/status",
       fields=(_addr("http://192.168.0.10:3000"), Field("username", N_("User name"), secret=False), Field("password", N_("Password")))),
    _p("pihole", "Pi-hole", "http://192.168.0.10",
       (N_("Use the web password of Pi-hole version 6 (or an app password from Settings → Web interface / API)."),),
       N_("Sees blocking statistics and turns blocking on or off."),
       "Pi-hole v6 API (docs: ftl.pi-hole.net, or your Pi-hole's own /api/docs/ page). GET /api/stats/summary, GET /api/dns/blocking, "
       "POST /api/dns/blocking with {\"blocking\": true|false, \"timer\": null}. Version 5 is not supported. The server signs in for you.",
       {"type": "custom_header", "header": "X-FTL-SID", "prefix": ""}, "/api/dns/blocking", login="pihole",
       fields=(_addr("http://192.168.0.10"), Field("password", N_("Password")))),
    _p("nextcloud", "Nextcloud", "http://192.168.0.10:8080",
       (N_("In Nextcloud, open your profile picture → Personal settings → Security → Devices & sessions."),
        N_("Under 'Create new app password' name it and press Create. Paste your user name and that app password here.")),
       N_("Reads and saves files by their path, and sees your shares."),
       "Nextcloud: a file is read with GET /remote.php/dav/files/<user>/<path> and written with PUT to the same path (listing a folder "
       "needs PROPFIND, which is not available here). OCS API under /ocs/v2.php, for example "
       "GET /ocs/v2.php/apps/files_sharing/api/v1/shares?format=json (docs: docs.nextcloud.com/server/latest/developer_manual/client_apis). "
       "The Notes app, when installed: GET /index.php/apps/notes/api/v1/notes.",
       {"type": "basic_auth", "user": "", "fixed_headers": {"OCS-APIRequest": "true"}}, "/ocs/v2.php/cloud/user?format=json",
       fields=(_addr("http://192.168.0.10:8080"), Field("username", N_("User name"), secret=False), Field("password", N_("App password")))),
    _p("gitea", "Gitea", "http://192.168.0.10:3000",
       (N_("In Gitea, open your profile picture → Settings → Applications."), N_("Under Generate New Token pick the access it needs and paste the token here.")),
       N_("Sees repositories, issues and pull requests, and writes issues."),
       "Gitea API (docs: docs.gitea.com/api; your server's own /api/swagger page lists every endpoint). GET /api/v1/user/repos, "
       "GET /api/v1/repos/<owner>/<repo>/issues, POST /api/v1/repos/<owner>/<repo>/issues.",
       {"type": "custom_header", "header": "Authorization", "prefix": "token "}, "/api/v1/user"),
    _p("forgejo", "Forgejo", "http://192.168.0.10:3000",
       (N_("In Forgejo, open your profile picture → Settings → Applications."), N_("Under Generate new token pick the access it needs and paste the token here.")),
       N_("Sees repositories, issues and pull requests, and writes issues."),
       "Forgejo API (your server's own /api/swagger page lists every endpoint; same shape as Gitea). GET /api/v1/user/repos, "
       "GET /api/v1/repos/<owner>/<repo>/issues, POST /api/v1/repos/<owner>/<repo>/issues.",
       {"type": "custom_header", "header": "Authorization", "prefix": "token "}, "/api/v1/user"),
)

# one line for each app's row in the connector list (catalog.py)
DESCRIPTIONS: dict[str, str] = {
    "home_assistant": "Home automation: devices, rooms, scenes and sensors",
    "frigate": "Camera recordings, detections and snapshots",
    "immich": "Your own photo library",
    "paperless": "Scanned documents you can search",
    "mealie": "Recipes, meal plans and shopping lists",
    "tandoor": "Recipes, meal plans and shopping lists",
    "grocy": "Household stock, shopping lists and chores",
    "actual_budget": "Your own budgeting app",
    "firefly_iii": "Your own personal finance manager",
    "linkwarden": "Saved links and bookmarks",
    "karakeep": "Bookmarks and notes you saved",
    "wallabag": "Articles saved to read later",
    "miniflux": "A minimal feed reader",
    "freshrss": "A feed reader",
    "n8n": "Workflows you built: list them and see their runs",
    "n8n_mcp": "Run the n8n workflows you chose to share",
    "uptime_kuma": "Up or down status of your sites and services",
    "beszel": "Server load, memory, disks and temperatures",
    "adguard_home": "Network-wide ad blocking and DNS statistics",
    "pihole": "Network-wide ad blocking and DNS statistics",
    "nextcloud": "Your own cloud storage for files",
    "gitea": "Your own code hosting",
    "forgejo": "Your own code hosting",
}
NAMES: dict[str, str] = {p.id: p.name for p in SELF_HOSTED}
