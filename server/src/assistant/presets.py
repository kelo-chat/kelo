"""How each catalog service is connected when it has no OAuth that works here: a key or token the user creates on the service, a Google
service account, the agent's own browser with a login kept in the vault, or not at all (with the reason). Everything here is fixed in code:
the hosts a key may be sent to, where it goes in the request, and the request that checks it before it is saved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .i18n import N_, t


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    secret: bool = True
    placeholder: str = ""


@dataclass(frozen=True)
class KeyPreset:
    id: str
    name: str
    item: str
    hosts: tuple[str, ...]
    placement: dict[str, Any]
    help: tuple[str, ...]
    blurb: str
    api: str                                  # for the assistant: where the API lives and where its documentation is
    fields: tuple[Field, ...] = (Field("token", N_("API key / token")),)
    verify: str = ""                          # a GET that must answer 200 before the key is saved ("" = saved without a check)
    verify_json_ok: bool = False              # Slack answers 200 even for a bad token: check {"ok": true}
    mint: str = ""                            # "zoom" / "google": the secret is exchanged for short tokens at each use
    store_host: bool = False                  # Shopify: the host is the user's own store
    endpoint: str = ""                         # an MCP address on one of `hosts` the key also opens (installed key packages)
    # ---- apps on the owner's own network (selfhosted.py): the address is typed in the app's form and kept with the connection
    local: bool = False                        # fields has an "address" (or `endpoint_field`); `verify` and `endpoint` are paths on it
    device: bool = False                       # controls devices: a request in the user's own chat needs no approval card (connect_tools)
    login: str = ""                            # pihole / beszel / freshrss / wallabag: user name + password are traded for a session token
    endpoint_field: str = ""                   # n8n: the field that holds the full MCP URL (the address is taken from it)
    sends: bool = False                        # posts to people (a messenger): a change through it is a "send" for the owner's limits (effects.py)
    route: str = ""                            # "wireguard" / "proxy": the VPN connectors (vpn.py): they set the route of Kelo's web browsing


TOKEN = (Field("token", N_("API key / token")),)

KEYS: dict[str, KeyPreset] = {p.id: p for p in [
    KeyPreset("asana", "Asana", "asana-api", ("app.asana.com",), {"type": "bearer_header"},
              (N_("In Asana, open your profile photo → Settings → Apps → Manage developer apps."), N_("Make a personal access token with 'Create new token' and paste it here.")),
              N_("Sees and makes projects and tasks."), "REST API at https://app.asana.com/api/1.0 (docs: developers.asana.com).",
              verify="https://app.asana.com/api/1.0/users/me"),
    KeyPreset("figma", "Figma", "figma-api", ("api.figma.com",), {"type": "custom_header", "header": "X-Figma-Token", "prefix": ""},
              (N_("In Figma, open the top-left menu → Settings → Security → Personal access tokens."),
               N_("Make a token, pick the access it needs (read file content, comments and so on) and paste it here.")),
              N_("Reads design files, components and comments."), "REST API at https://api.figma.com/v1 (docs: figma.com/developers/api).",
              verify="https://api.figma.com/v1/me"),
    KeyPreset("calendly", "Calendly", "calendly-api", ("api.calendly.com",), {"type": "bearer_header"},
              (N_("Open Calendly → Integrations & apps → API and webhooks."), N_("Make a Personal access token and paste it here.")),
              N_("Sees booking links, event types and booked meetings."), "REST API at https://api.calendly.com (docs: developer.calendly.com).",
              verify="https://api.calendly.com/users/me"),
    KeyPreset("slack", "Slack", "slack-api", ("slack.com",), {"type": "bearer_header"},
              (N_("Make an app at api.slack.com/apps → Create New App → From scratch."),
               N_("Under Socket Mode, turn it on and make an app-level token with the scope connections:write (it starts with xapp-)."),
               N_("Under OAuth & Permissions, add search:read, channels:history, channels:read, groups:history, im:history, users:read, chat:write to User Token Scopes, "
                  "and chat:write, im:history, channels:history, groups:history, channels:read, groups:read, reactions:write, files:read to Bot Token Scopes."),
               N_("Under Event Subscriptions, turn it on and subscribe to the bot events message.im, message.channels, message.groups and member_joined_channel. "
                  "Turn on Interactivity & Shortcuts, and under App Home allow messages in the Messages tab."),
               N_("Press Install to Workspace and paste the three tokens here: the User OAuth Token (xoxp-), the Bot User OAuth Token (xoxb-) and the app-level token (xapp-).")),
              N_("Finds and reads messages, sends them when you allow it, and lets you talk to Kelo from Slack."),
              "Web API at https://slack.com/api/<method> (docs: api.slack.com/methods); calls through this connector act as the owner (their user token). "
              "Kelo's own Slack chat with the owner (its bot) is run by the server itself (never open Socket Mode or post as the bot yourself). After the "
              "tokens are saved the server posts a code (10 minutes); the owner sends it to the bot in a direct message, and from then on only that Slack "
              "account talks to Kelo there. In channels the owner invited the bot to, it answers only when mentioned or replied to in its thread, with no "
              "access to the owner's private things. Check the chat with the messenger tool (status), new_code for a fresh code.",
              fields=(Field("token", N_("User OAuth Token (xoxp-…)")), Field("bot", N_("Bot User OAuth Token (xoxb-…)")),
                      Field("app", N_("App-level token (xapp-…)"))),
              verify="https://slack.com/api/auth.test", verify_json_ok=True, mint="slack", sends=True),
    KeyPreset("ticketmaster", "Ticketmaster", "ticketmaster-api", ("app.ticketmaster.com",), {"type": "query_param", "param": "apikey"},
              (N_("Make an app at developer.ticketmaster.com → My Apps and paste its Consumer Key."),),
              N_("Finds concert and game dates and tickets."), "Discovery API at https://app.ticketmaster.com/discovery/v2 (docs: developer.ticketmaster.com).",
              verify="https://app.ticketmaster.com/discovery/v2/events.json?size=1"),
    KeyPreset("places", "Places Search", "places-api", ("places.googleapis.com",), {"type": "custom_header", "header": "X-Goog-Api-Key", "prefix": ""},
              (N_("Turn on Places API (New) at console.cloud.google.com."), N_("Make an API key under Credentials and paste it here. Google may charge for what you use.")),
              N_("Finds addresses, opening hours and ratings of places and shops."),
              "Places API (New) at https://places.googleapis.com/v1/places:searchText (POST, needs an X-Goog-FieldMask header)."),
    KeyPreset("shopify", "Shopify", "shopify-api", (), {"type": "custom_header", "header": "X-Shopify-Access-Token", "prefix": ""},
              (N_("Make an app in Shopify admin → Settings → Apps and sales channels → Develop apps."), N_("Pick the Admin API access, install the app, then paste the Admin API access token (shpat_).")),
              N_("Sees and manages products, stock and orders."), "Admin REST/GraphQL API at https://<store>.myshopify.com/admin/api/2025-07 (docs: shopify.dev).",
              fields=(Field("store", N_("Store address"), secret=False, placeholder="mystore.myshopify.com"), Field("token", N_("Admin API access token"))),
              verify="https://{store}/admin/api/2025-07/shop.json", store_host=True),
    KeyPreset("zoom", "Zoom", "zoom-api", ("api.zoom.us",), {"type": "bearer_header", "via": "zoom"},
              (N_("Make an app at marketplace.zoom.us → Develop → Build App → Server-to-Server OAuth."),
               N_("Add meeting:read, meeting:write, user:read, cloud_recording:read to Scopes and press Activate."),
               N_("Paste the Account ID, Client ID and Client secret from App credentials.")),
              N_("Sets up and changes meetings, and sees recordings."), "REST API at https://api.zoom.us/v2 (docs: developers.zoom.us).",
              fields=(Field("account_id", "Account ID", secret=False), Field("client_id", "Client ID", secret=False), Field("client_secret", "Client secret")),
              verify="https://api.zoom.us/v2/users/me", mint="zoom"),
    KeyPreset("telegram", "Telegram", "telegram-bot", ("api.telegram.org",), {"type": "url_path_segment"},
              (N_("In Telegram, open @BotFather and send /newbot; pick a name and a user name ending in 'bot'."),
               N_("Copy the token it gives you (it looks like 123456:ABC-...) and paste it here.")),
              N_("Talk to Kelo from your Telegram, with answer buttons for its questions."),
              "Bot API at https://api.telegram.org/bot{secret}/<method> (docs: core.telegram.org/bots/api). "
              "This is Kelo's own Telegram chat with the owner, run by the server itself (never call getUpdates). After the token is "
              "saved the server posts a link and a code (10 minutes); the owner opens the link or sends the code to the bot, and from "
              "then on only that account talks to Kelo, in a private chat with the bot; in groups the owner added it to, it answers "
              "only when mentioned or replied to, with no access to the owner's private things (it reads the rest of the group's talk as "
              "context only when the owner made the bot a group admin or turned Group Privacy off in BotFather); messages arrive "
              "within seconds. The token alone does not finish it: check with the messenger tool (status), new_code for a fresh code.",
              fields=(Field("token", N_("Bot token")),), verify="https://api.telegram.org/bot{secret}/getMe", sends=True),
    KeyPreset("discord", "Discord", "discord-bot", ("discord.com",), {"type": "custom_header", "header": "Authorization", "prefix": "Bot "},
              (N_("Open discord.com/developers/applications → New Application → Bot → Reset Token, and copy the token."),
               N_("On the same Bot page, turn ON \"Message Content Intent\"."),
               N_("Open OAuth2 → URL Generator: tick the scope bot and the permissions Send Messages, Read Message History and Add Reactions, "
                  "open the URL it makes and add the bot to a server you own (a private one just for you is fine). Then paste the token here.")),
              N_("Talk to Kelo from Discord, with answer buttons for its questions."),
              "Bot API at https://discord.com/api/v10 (docs: discord.com/developers/docs). This is Kelo's own Discord chat with the owner, "
              "run by the server itself (never open the gateway). After the token is saved the server posts a code (10 minutes); the owner "
              "sends the code to the bot in a direct message, and from then on only that account talks to Kelo there. In servers the owner "
              "owns and added the bot to, it answers only when mentioned or replied to, with no access to the owner's private things. The "
              "token alone does not finish it: check with the messenger tool (status), new_code for a fresh code.",
              fields=(Field("token", N_("Bot token")),), verify="https://discord.com/api/v10/users/@me", sends=True),
    # Wise's MCP sign-in accepts only client domains it trusts (it refused this app after the login), so it is a personal token here.
    KeyPreset("wise", "Wise", "wise-api", ("api.wise.com",), {"type": "bearer_header"},
              (N_("In Wise, open Settings → Integrations and tools → API tokens."),
               N_("Add a token (Read only is enough to see balances and rates) and paste it here.")),
              N_("Sees balances, exchange rates and transfers."), "REST API at https://api.wise.com (docs: docs.wise.com/api-docs). "
              "Start with GET /v2/profiles for the profile id, then /v4/profiles/{profileId}/balances?types=STANDARD and GET /v1/transfers?profile={profileId}.",
              verify="https://api.wise.com/v2/profiles"),
    # 2026-11-09 allowlist audit: these MCP sign-ins take only apps on the vendor's list (or one the organisation's admin approved), but
    # the same MCP address takes a personal key as a Bearer token (each vendor's docs, quoted in mcp_oauth.py and catalog.py).
    KeyPreset("monday", "monday.com", "monday-api", ("mcp.monday.com", "api.monday.com"), {"type": "bearer_header"},
              (N_("In monday.com, open your profile picture → Developers → My access tokens."), N_("Copy your personal API token and paste it here.")),
              N_("Finds and edits boards and items."), "monday.com's hosted MCP server at https://mcp.monday.com/mcp (use mcp_list_tools / "
              "mcp_call_tool; docs: developer.monday.com/api-reference/docs/mcp-api-token). GraphQL API at https://api.monday.com/v2.",
              endpoint="https://mcp.monday.com/mcp"),
    KeyPreset("zapier", "Zapier", "zapier-api", ("mcp.zapier.com",), {"type": "bearer_header"},
              (N_("Open mcp.zapier.com, make a new MCP server and pick 'Other' as the client."),
               N_("Add the actions you want, then open Connect, make a token and paste it here.")),
              N_("Runs actions in the apps you connected to Zapier."), "Zapier MCP at https://mcp.zapier.com/api/v1/connect (use mcp_list_tools / "
              "mcp_call_tool; docs: docs.zapier.com/mcp).", endpoint="https://mcp.zapier.com/api/v1/connect"),
    KeyPreset("incident_io", "incident.io", "incident-io-api", ("mcp.incident.io", "api.incident.io"), {"type": "bearer_header"},
              (N_("In incident.io, open Settings → API keys and press Add new."), N_("Pick what it may see and do, make the key and paste it here.")),
              N_("Sees incident response and on-call info."), "incident.io's MCP server at https://mcp.incident.io/mcp (use mcp_list_tools / "
              "mcp_call_tool). REST API at https://api.incident.io (docs: api-docs.incident.io).",
              verify="https://api.incident.io/v1/identity", endpoint="https://mcp.incident.io/mcp"),
    KeyPreset("datadog", "Datadog", "datadog-api", ("mcp.datadoghq.com",), {"type": "bearer_header"},
              (N_("In Datadog, open your profile → Personal Settings → Personal Access Tokens (or a service account's token)."),
               N_("Make a token with the access the agent needs and paste it here.")),
              N_("Looks into outages with metrics, logs and traces."), "Datadog's MCP server at https://mcp.datadoghq.com/v1/mcp (US1 site; use "
              "mcp_list_tools / mcp_call_tool; docs: docs.datadoghq.com/bits_ai/mcp_server/setup).",
              endpoint="https://mcp.datadoghq.com/v1/mcp?toolsets=all,visualizations"),
    # The other ways offered next to an MCP sign-in that may refuse this app (catalog.SIGN_IN_CAUTION): not catalog ids of their own.
    KeyPreset("stripe_key", "Stripe", "stripe-api", ("mcp.stripe.com", "api.stripe.com"), {"type": "bearer_header"},
              (N_("In the Stripe Dashboard, open Developers → API keys and make a restricted key for an agent."),
               N_("Give it only the access the agent needs and paste it here.")),
              N_("Sees and manages payments, customers and invoices."), "Stripe's MCP server at https://mcp.stripe.com (use mcp_list_tools / "
              "mcp_call_tool). REST API at https://api.stripe.com/v1 (docs: docs.stripe.com/mcp).", endpoint="https://mcp.stripe.com"),
    KeyPreset("clickup_key", "ClickUp", "clickup-api", ("api.clickup.com",), {"type": "custom_header", "header": "Authorization", "prefix": ""},
              (N_("In ClickUp, open your avatar → Settings → Apps."), N_("Press Generate under API Token and paste the token (it starts with pk_) here.")),
              N_("Finds and makes tasks, docs and projects."), "REST API at https://api.clickup.com/api/v2 (docs: developer.clickup.com).",
              verify="https://api.clickup.com/api/v2/user"),
    # ---- Kelo's web browsing through the owner's VPN or proxy (vpn.py). Not an API: the connection sets the route of the agent's browser and of
    # web_fetch / web_search. At most one is on; connecting one turns the other off. The vault items are the ones vpn.py names (WG_ITEM, PROXY_ITEM).
    KeyPreset("vpn_wireguard", "VPN (WireGuard)", "vpn-wireguard", (), {"type": "none"},
              (N_("Get a WireGuard configuration file from your VPN provider (Mullvad, Proton VPN, IVPN and others offer one; or use your own server)."),
               N_("Open the file in a text editor, copy everything in it and paste it here."),
               N_("Only Kelo's web browsing goes through the VPN. AI model calls, your other connections, mail and your phone connection stay direct."),
               N_("After a switch, sites that tie a session to the address may ask you to sign in again or show a check. Cookies and sign-ins are kept.")),
              N_("Kelo's browser and its web reads go out through your VPN. If the VPN is down, browsing stops; it never goes around it."),
              "Not an API. This connection only sets the route of Kelo's own web browsing (its browser and web_fetch / web_search); there is nothing to call "
              "and nothing to use it for. If browsing fails with 'The VPN is not connected right now', tell the user.",
              fields=(Field("config", N_("WireGuard configuration (paste the whole file)")),), route="wireguard"),
    KeyPreset("vpn_proxy", "Proxy", "vpn-proxy", (), {"type": "none"},
              (N_("Enter the address of a SOCKS5 or HTTP proxy outside your home network, like socks5://host:1080."),
               N_("If the proxy needs a user name and password, enter them too."),
               N_("Only Kelo's web browsing goes through the proxy. AI model calls, your other connections, mail and your phone connection stay direct."),
               N_("After a switch, sites that tie a session to the address may ask you to sign in again or show a check. Cookies and sign-ins are kept.")),
              N_("Kelo's browser and its web reads go out through your proxy. If the proxy is down, browsing stops; it never goes around it."),
              "Not an API. This connection only sets the route of Kelo's own web browsing (its browser and web_fetch / web_search); there is nothing to call "
              "and nothing to use it for. If browsing fails with 'The VPN is not connected right now', tell the user.",
              fields=(Field("address", N_("Proxy address"), secret=False, placeholder="socks5://host:1080"),
                      Field("user", N_("User name (if it needs one)"), secret=False), Field("password", N_("Password (if it needs one)"))), route="proxy"),
]}

from .selfhosted import SELF_HOSTED  # noqa: E402  (needs Field and KeyPreset above)

KEYS.update({p.id: p for p in SELF_HOSTED})
SELF_HOSTED_IDS = frozenset(p.id for p in SELF_HOSTED)
NETWORK_IDS = frozenset(p.id for p in KEYS.values() if p.route)   # shown under the category "Network"

GOOGLE_HOSTS = ("www.googleapis.com", "sheets.googleapis.com", "docs.googleapis.com", "slides.googleapis.com", "forms.googleapis.com")
GOOGLE_SCOPES = ("https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/documents",
                 "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/presentations", "https://www.googleapis.com/auth/forms.body",
                 "https://www.googleapis.com/auth/forms.responses.readonly")
GOOGLE = KeyPreset(
    "google", "Google", "google-sa", GOOGLE_HOSTS, {"type": "bearer_header", "via": "google"},
    (N_("Make a project at console.cloud.google.com."),
     N_("Under APIs & Services → Library, turn on the Google Calendar, Drive, Docs, Sheets, Slides and Forms APIs."),
     N_("Go to IAM & Admin → Service accounts → Create service account → Keys → Add key → JSON and download it."),
     N_("Paste the content of the JSON file below."),
     N_("Once connected, share the calendars, files and folders you want to use with the service account address it shows (…iam.gserviceaccount.com). Only what you share can be seen.")),
    N_("Works with your Google Calendar, Drive, Docs, Sheets, Slides and Forms. Only the calendars and files you share can be seen."),
    "Google REST APIs (calendar/v3 and drive/v3 on www.googleapis.com; sheets, docs, slides, forms on their own *.googleapis.com hosts). "
    "Only calendars and files shared with the service account are visible.",
    fields=(Field("key", N_("Service account key (JSON)")),), mint="google")
GOOGLE_CONNECTORS = ("google_calendar", "google_docs", "google_drive", "google_forms", "google_sheets", "google_slides")

# The agent's own browser does the work; a login, when the site needs one, is kept in the vault and typed by the server.
BROWSER: dict[str, str] = {
    "agoda": "https://www.agoda.com", "canva": "https://www.canva.com", "expedia": "https://www.expedia.com", "opentable": "https://www.opentable.com",
    "instacart": "https://www.instacart.com", "facebook_cli": "https://www.facebook.com", "instagram": "https://www.instagram.com", "threads": "https://www.threads.com",
    "messenger_read": "https://www.messenger.com", "box": "https://app.box.com", "quickbooks": "https://qbo.intuit.com", "spotify": "https://open.spotify.com",
    "google_contacts": "https://contacts.google.com", "google_tasks": "https://calendar.google.com/calendar/u/0/r/tasks", "meta_catalog_search": "https://www.facebook.com/marketplace",
    # used mostly in Korea (featured there: catalog.Service.regions)
    "naver": "https://www.naver.com", "kakao": "https://map.kakao.com", "coupang": "https://www.coupang.com", "baemin": "https://www.baemin.com",
    "toss": "https://toss.im", "daangn": "https://www.daangn.com",
}
BLOCKS_BOTS = {"facebook_cli", "instagram", "threads", "messenger_read", "google_contacts", "google_tasks", "meta_catalog_search", "coupang"}

UNSUPPORTED: dict[str, str] = {
    "apple_healthkit": N_("iPhone health data can only be read by an app installed on the phone. A web app can't."),
    "google_health_connect": N_("Health Connect data can only be read by an app installed on Android. A web app can't."),
    "shop-pay": N_("This app doesn't pay for you with a saved card."),
    "stripe-link": N_("This app doesn't pay for you with a saved card."),
    "shopify-ucp": N_("This payment link is made only for another product, so this app can't use it."),
    "philips_hue": N_("For safety, the agent is blocked from devices on your home network."),
}


def browser_hint(cid: str) -> str:
    return t("If the site blocks automated visits, signing in may not work.") if cid in BLOCKS_BOTS else ""


def secret_fields(p: KeyPreset) -> list[str]:
    return [f.id for f in p.fields if f.secret]


def public(p: KeyPreset) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "item": p.item, "help": [t(h) for h in p.help], "checked": bool(p.verify) or bool(p.mint) or bool(p.endpoint_field), "local": p.local,
            "fields": [{"id": f.id, "label": t(f.label), "secret": f.secret, "placeholder": f.placeholder} for f in p.fields]}


__all__ = ["SELF_HOSTED_IDS", "NETWORK_IDS", "BROWSER", "GOOGLE", "GOOGLE_CONNECTORS", "KEYS", "UNSUPPORTED", "Field", "KeyPreset", "browser_hint", "field", "public", "secret_fields"]
