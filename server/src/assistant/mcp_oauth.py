"""Connect a hosted MCP server with OAuth, the way the MCP authorization spec describes it: discover the server's authorization server,
identify this app to it without anyone creating an app in advance, send the user to the service's own sign-in page with PKCE, and take the
code back at /oauth/callback.

How the app identifies itself, in the spec's order of preference:
 1. Client ID Metadata Document (draft-ietf-oauth-client-id-metadata-document): when the authorization server says
    `client_id_metadata_document_supported`, the client_id is the https address of a small public JSON file describing Kelo
    (client_metadata(); served by the operator's server at /oauth/client-metadata.json). The service fetches it and checks that the
    return address is listed there, so it only works with the shared relay page, whose address the document names. No registration
    call, no secret: a public client protected by PKCE.
 2. Dynamic client registration (RFC 7591), when the server publishes a registration endpoint.
The method used is kept in the token bundle ("registration": "cimd" | "dcr").

The access and refresh tokens are sealed into the vault as one service key; no endpoint returns them and the assistant never sees them.
Every network call goes through netguard (https, public addresses only, no redirects followed).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from .i18n import N_, t
from .netguard import NetGuardError, Reply, fetch
from .scope_pick import ScopePick
from .store import Store
from .vault import VaultError

Fetcher = Callable[..., Awaitable[Reply]]
PENDING_SECONDS = 900.0
CLIENT_NAME = "Kelo"
MARK = "oauth-bundle-v1"
CLIENT_METADATA_PATH = "/oauth/client-metadata.json"
CIMD_REFUSED = "mcp_cimd_refused."      # store meta key + service id: "1" = the service turned the metadata document down, so register next
                                        # time; "refused <time>" = it turned this app down and there is nothing else to try (refused())
REFUSAL_ERRORS = ("invalid_client", "unauthorized_client", "invalid_request")
CLIENT_REFUSALS = ("invalid_client", "unauthorized_client")   # errors that name this app's client itself (not a cancel, not a bad request)
REFUSED = "refused"
REFUSAL_DAYS = 30   # a remembered refusal is tried again after this long (the service may have approved the app since)


@dataclass(frozen=True)
class McpService:
    id: str          # the connector's id in the catalog
    item: str        # the vault item that holds its tokens
    name: str
    url: str         # the MCP endpoint (an OAuth API package: its base address)
    blurb: str
    # an OAuth 2 API from an installed package (connect type "oauth"): fixed endpoints, scopes, API hosts and a client id instead of
    # discovery and registration; the tokens are sent only to its hosts. None for an MCP server.
    oauth: Any = None
    situation: Any = None    # the one read-only call that shows the service's state (situation_reads.py); None = none (see situation.md)


def _svc(cid: str, name: str, url: str, blurb: str) -> McpService:
    return McpService(cid, cid.removesuffix("_mcp").replace("_", "-") + "-mcp", name, url, blurb)


# Checked 2026-11-07: each of these answers an unauthenticated MCP request with 401 (a few also answer without a sign-in) and publishes
# OAuth metadata, found the way discover() looks for it, with an https registration endpoint and PKCE S256 (or no PKCE list).
# Candidates: the Claude connectors directory (796 entries), the official MCP Registry (17,860 vendor-published remote addresses), curated
# remote-server lists and vendor pages, all probed read-only (no client was registered and no sign-in started for the ones added that day).
# Not here, though they publish a registration endpoint: Figma refuses registrations from clients it has not approved (HTTP 403), and Canva
# accepts the registration but refuses the return address on its sign-in page (it takes only addresses it approved). Some of the services
# added on 2026-11-07 may do the same; that only shows when someone signs in.
# Also left out:
#  - Client ID Metadata Documents only (47 checked 2026-11-07 with this app's document; ElevenLabs, Mentimeter, MongoDB Atlas and Wise were
#    added). Refused this app's document (most keep a list of trusted client domains): Shopify, Squarespace, Affinity, TomTom, MYOB, Funnel,
#    Ironclad, Sendcloud, Atlar, Diffit, Isometric, FMP, Public, Superlawyer, Aptus, Morningstar, Steadybit, PGA, Black Diamond, Loopio,
#    Macrobond, Wealth.com, and MyCase, InfoTrack and LawY (HTTP 400). BILL, Autodesk and Shop answered 403; Zendesk needs the customer's
#    own subdomain; Unsplash stops automated browsers; ZipRecruiter no longer publishes its sign-in details. Reached their login page but
#    niche or business-only: Adaption, Belvo, Hubstaff (these three fetched the document before the login), Amazon Selling Partner,
#    Crunchbase, Bitrise, Chariot, Files.com, Loops, Softr, Synthflow, Wealthbox.
#  - OAuth with a client the service must approve first (~90: Slack, Box, HubSpot, Asana v2, Zoom, Webex, Xero, GitHub, Google Cloud,
#    Discord, DocuSign, Smartsheet, Wrike, PagerDuty, Render, Expedia, Uber, Instacart, Spotify, Toss, ...).
#  - MCP address refuses outside callers with HTTP 403 (Adobe Creative Cloud, Mailchimp, QuickBooks, TurboTax, Yelp, Ticketmaster, Shippo, ...).
#  - Only an API key / token (G2, Stack Overflow, Weights & Biases, Pipedrive's old address, Freshdesk, Rippling, RocketReach, ...), or no
#    sign-in at all (~90 open servers such as DeepWiki, Microsoft Learn, AWS Knowledge).
#  - Only the old SSE transport (Triple Whale, Third Bridge); token endpoints that take only client_secret_basic (Cloze, Dext, PubMatic);
#    demo servers (example-server.modelcontextprotocol.io, the Stytch demo).
#  - Asana and Calendly pass these checks but stay key presets here.
#  - About 435 more that pass, but are niche, enterprise-only or tied to one region (left for the owner to choose), and some 7,700 small
#    servers that pass in the MCP Registry. Removed on the owner's request the same day: Granola, Klaviyo, Lovable, Facebook ads, Peloton,
#    Plaid, Rappi, Readwise, Replit, Runway, vidIQ, Withings.
SERVICES: dict[str, McpService] = {s.id: s for s in [
    _svc("dropbox", "Dropbox", "https://mcp.dropbox.com/mcp", N_("Finds, reads and organizes files.")),
    _svc("grab_mcp", "Grab", "https://mcp-ai-apps.grab.com/mcp", N_("Uses Grab services.")),
    _svc("linear", "Linear", "https://mcp.linear.app/mcp", N_("Finds and makes issues and projects.")),
    _svc("notion", "Notion", "https://mcp.notion.com/mcp", N_("Finds and edits pages and databases.")),
    _svc("shopback_mcp", "ShopBack", "https://connect.shopback.com/shopping-agent/mcp", N_("Finds cashback shopping deals.")),
    _svc("stripe", "Stripe", "https://mcp.stripe.com", N_("Sees and manages payments, customers and invoices.")),
    _svc("todoist", "Todoist", "https://ai.todoist.net/mcp", N_("Sees and makes tasks and projects.")),
    _svc("vercel", "Vercel", "https://mcp.vercel.com", N_("Sees projects and deployment status.")),
    _svc("evernote", "Evernote", "https://mcp.evernote.com/mcp", N_("Finds and edits notes and notebooks.")),
    # Added 2026-11-07 after probing the Claude connectors directory, the MCP Registry, curated lists and vendor pages (same checks as above).
    _svc("adobe_workfront", "Adobe Workfront", "https://mcp.workfront.adobe.com/mcp/v1/workfront", N_("Manages projects, tasks and approval requests.")),
    _svc("ahrefs", "Ahrefs", "https://api.ahrefs.com/mcp/mcp", N_("Analyzes backlinks, keywords and search rankings.")),
    _svc("airtable", "Airtable", "https://mcp.airtable.com/mcp", N_("Finds and edits records in bases and tables.")),
    _svc("amplitude", "Amplitude", "https://mcp.amplitude.com/mcp", N_("Sees and analyzes product usage numbers and charts.")),
    _svc("apify", "Apify", "https://mcp.apify.com", N_("Runs web scraping actors and gets the results.")),
    _svc("apollo", "Apollo.io", "https://mcp.apollo.io/mcp", N_("Finds prospects and companies and manages sales contacts.")),
    _svc("atlassian", "Atlassian", "https://mcp.atlassian.com/v1/mcp", N_("Finds and makes Jira issues and Confluence pages.")),
    _svc("attio", "Attio", "https://mcp.attio.com/mcp", N_("Finds and edits CRM contacts, companies and deals.")),
    _svc("basecamp", "Basecamp", "https://mcp.basecamp.com/mcp", N_("Sees and manages projects, tasks and messages.")),
    _svc("beehiiv", "beehiiv", "https://mcp.beehiiv.com/mcp", N_("Writes newsletters and analyzes how they do.")),
    _svc("betterstack", "Better Stack", "https://mcp.betterstack.com/", N_("Sees logs, metrics and uptime.")),
    _svc("bitly", "Bitly", "https://api-ssl.bitly.com/v4/mcp", N_("Makes short links and QR codes and sees clicks.")),
    _svc("brex", "Brex", "https://api.brex.com/mcp", N_("Sees and manages company card spending and expenses.")),
    _svc("buildkite", "Buildkite", "https://mcp.buildkite.com/mcp", N_("Sees CI pipelines and build results.")),
    _svc("clickhouse", "ClickHouse", "https://mcp.clickhouse.cloud/mcp", N_("Looks up ClickHouse Cloud data.")),
    _svc("clickup", "ClickUp", "https://mcp.clickup.com/mcp", N_("Finds and makes tasks, docs and projects.")),
    _svc("close", "Close", "https://mcp.close.com/mcp", N_("Manages CRM leads, contacts and deals.")),
    _svc("cloudflare_observability", "Cloudflare Observability", "https://observability.mcp.cloudflare.com/mcp", N_("Sees Workers logs and analytics.")),
    _svc("cloudflare_workers", "Cloudflare Workers", "https://bindings.mcp.cloudflare.com/mcp", N_("Manages Workers, KV, R2 and D1 resources.")),
    _svc("cloudinary", "Cloudinary", "https://asset-management.mcp.cloudinary.com/mcp", N_("Uploads and manages images and videos.")),
    _svc("coinmarketcap", "CoinMarketCap", "https://mcp.coinmarketcap.com/mcp", N_("Sees crypto prices and on-chain data.")),
    _svc("contentful", "Contentful", "https://mcp.contentful.com/mcp", N_("Manages content entries and content models.")),
    _svc("customer_io", "Customer.io", "https://mcp.customer.io/mcp", N_("Manages customer message campaigns and segments.")),
    _svc("deepl", "DeepL", "https://mcp.deepl.com/v1/mcp", N_("Translates text and documents.")),
    _svc("descript", "Descript", "https://api.descript.com/v2/mcp", N_("Loads and edits videos.")),
    _svc("digitalocean", "DigitalOcean", "https://mcp.digitalocean.com/mcp", N_("Deploys and manages apps and servers.")),
    _svc("egnyte", "Egnyte", "https://mcp-server.egnyte.com/mcp", N_("Finds and reads files and folders.")),
    _svc("exa", "Exa", "https://mcp.exa.ai/mcp", N_("Searches the web and code docs.")),
    _svc("expo", "Expo", "https://mcp.expo.dev/mcp", N_("Manages app builds, releases and status.")),
    _svc("fathom", "Fathom", "https://api.fathom.ai/mcp", N_("Finds and reads summaries and transcripts of meeting recordings.")),
    _svc("firecrawl", "Firecrawl", "https://mcp.firecrawl.dev/v2/mcp-search", N_("Searches the web and pulls out page content.")),
    _svc("fireflies", "Fireflies.ai", "https://api.fireflies.ai/mcp", N_("Finds transcripts and summaries of meeting recordings.")),
    _svc("framer", "Framer", "https://mcp.framer.com/mcp", N_("Designs, edits and publishes websites.")),
    _svc("freshbooks", "FreshBooks", "https://mcp.freshbooks.com/v1", N_("Sees invoices, expenses and finances.")),
    _svc("gitlab", "GitLab", "https://gitlab.com/api/v4/mcp", N_("Finds and works with repositories, issues and merge requests.")),
    _svc("grafana_cloud", "Grafana Cloud", "https://mcp.grafana.com/mcp", N_("Looks up metrics, logs and traces, and manages dashboards.")),
    _svc("greenhouse", "Greenhouse", "https://mcp.greenhouse.io/mcp", N_("Finds and edits job candidates and applications.")),
    _svc("hugging_face", "Hugging Face", "https://huggingface.co/mcp", N_("Finds models, datasets and Spaces.")),
    # IBKR's own instructions for "any MCP-compatible AI application" use /mcp-public (same sign-in server); /mcp is its partners' address.
    _svc("ibkr", "Interactive Brokers", "https://api.ibkr.com/v1/api/mcp-public", N_("Sees your brokerage account and markets, and trades when you allow it.")),
    _svc("jotform", "Jotform", "https://mcp.jotform.com/", N_("Makes forms and sees responses.")),
    _svc("kit", "Kit", "https://app.kit.com/mcp", N_("Manages email marketing subscribers and emails.")),
    _svc("krisp", "Krisp", "https://mcp.krisp.ai/mcp", N_("Finds meeting transcripts and notes.")),
    _svc("launchdarkly", "LaunchDarkly", "https://mcp.launchdarkly.com/mcp", N_("Sees and changes feature flags.")),
    _svc("lucid", "Lucid", "https://mcp.lucid.app/mcp", N_("Makes diagrams and finds documents.")),
    _svc("mailerlite", "MailerLite", "https://mcp.mailerlite.com/mcp", N_("Manages email marketing campaigns and subscribers.")),
    _svc("make", "Make", "https://mcp.make.com", N_("Runs and manages Make scenarios.")),
    _svc("mapbox", "Mapbox", "https://mcp.mapbox.com/mcp", N_("Searches places, finds routes and makes maps.")),
    _svc("mercury", "Mercury", "https://mcp.mercury.com/mcp", N_("Sees bank balances and transactions.")),
    _svc("metricool", "Metricool", "https://ai.metricool.com/mcp", N_("Schedules social media posts and analyzes how they do.")),
    _svc("mintlify", "Mintlify", "https://mcp.mintlify.com/", N_("Finds, reads and edits docs.")),
    _svc("miro", "Miro", "https://mcp.miro.com/", N_("Sees boards and makes drawings and notes.")),
    _svc("mixpanel", "Mixpanel", "https://mcp.mixpanel.com/mcp", N_("Looks up and manages product analytics data.")),
    _svc("motherduck", "MotherDuck", "https://api.motherduck.com/mcp", N_("Looks up DuckDB databases.")),
    _svc("neon", "Neon", "https://mcp.neon.tech/mcp", N_("Manages Postgres databases and projects.")),
    _svc("netlify", "Netlify", "https://netlify-mcp.netlify.app/mcp", N_("Manages sites and deployments.")),
    _svc("otter", "Otter.ai", "https://mcp.otter.ai/mcp", N_("Finds and reads meeting transcripts and summaries.")),
    _svc("pandadoc", "PandaDoc", "https://mcp.pandadoc.com/v1/mcp", N_("Makes and sends documents and sees signing status.")),
    _svc("paypal", "PayPal", "https://mcp.paypal.com/mcp", N_("Sees and manages transactions, invoices and payments.")),
    _svc("planetscale", "PlanetScale", "https://mcp.pscale.dev/mcp/planetscale", N_("Manages databases and branches.")),
    _svc("posthog", "PostHog", "https://mcp.posthog.com/mcp", N_("Sees product analytics, feature flags and errors.")),
    _svc("postman", "Postman", "https://mcp.postman.com/minimal", N_("Finds and works with API collections and specs.")),
    _svc("prisma", "Prisma", "https://mcp.prisma.io/mcp", N_("Manages Prisma Postgres databases.")),
    _svc("pulumi", "Pulumi", "https://mcp.ai.pulumi.com/mcp", N_("Sees and manages cloud infrastructure stacks.")),
    _svc("railway", "Railway", "https://mcp.railway.com/", N_("Deploys and manages apps and infrastructure.")),
    _svc("ramp", "Ramp", "https://mcp.ramp.com/mcp", N_("Sees and manages company card spending, bills and expenses.")),
    _svc("read_ai", "Read AI", "https://api.read.ai/mcp", N_("Finds and reads meeting notes and summaries.")),
    _svc("resend", "Resend", "https://mcp.resend.com/", N_("Sends emails and sees what was sent.")),
    _svc("restream", "Restream", "https://mcp.restream.io/mcp", N_("Makes and manages live streams on many platforms.")),
    _svc("sanity", "Sanity", "https://mcp.sanity.io/", N_("Makes and looks up structured content.")),
    _svc("sentry", "Sentry", "https://mcp.sentry.dev/mcp", N_("Finds errors and issues and looks into their causes.")),
    _svc("supabase", "Supabase", "https://mcp.supabase.com/mcp", N_("Manages databases, tables and projects.")),
    _svc("superhuman_docs", "Superhuman Docs", "https://docs.superhuman.com/apis/mcp", N_("Makes, finds and edits documents and tables.")),
    _svc("superhuman_mail", "Superhuman Mail", "https://mcp.mail.superhuman.com/mcp", N_("Finds mail and events and writes replies.")),
    _svc("tavily", "Tavily", "https://mcp.tavily.com/mcp", N_("Searches the web and fetches content.")),
    _svc("ticktick", "TickTick", "https://mcp.ticktick.com/", N_("Finds and makes tasks and habits.")),
    _svc("tiktok_ads", "TikTok for Business", "https://business-api.tiktok.com/open_mcp/tt-ads-mcp-layer", N_("Makes and analyzes TikTok ad campaigns.")),
    _svc("tldv", "tl;dv", "https://mcp.tldv.io/mcp", N_("Finds meeting transcripts, notes and tasks.")),
    _svc("trello", "Trello", "https://mcp.trello.com/v1", N_("Finds and makes boards and cards.")),
    _svc("typefully", "Typefully", "https://mcp.typefully.com/mcp", N_("Writes and schedules posts for X, LinkedIn and Threads.")),
    _svc("v0", "v0", "https://v0.app/api/mcp", N_("Makes web apps.")),
    _svc("webflow", "Webflow", "https://mcp.webflow.com/mcp", N_("Manages sites, CMS content and pages.")),
    _svc("webull", "Webull", "https://api.webull.com/mcp", N_("Sees your brokerage account and prices.")),
    _svc("wix", "Wix", "https://mcp.wix.com/mcp", N_("Manages Wix sites and business data.")),
    _svc("wordpress_com", "WordPress.com", "https://public-api.wordpress.com/wpcom/v2/mcp/v1", N_("Manages sites, posts and pages.")),
    _svc("workable", "Workable", "https://mcp.workable.com/mcp", N_("Manages hiring and HR work.")),
    _svc("workos", "WorkOS", "https://mcp.workos.com/mcp", N_("Manages your WorkOS workspace.")),
    _svc("zoho_books", "Zoho Books", "https://zohobooks.zohomcp.com/mcp/message", N_("Manages invoices, expenses and books.")),
    _svc("zoho_crm", "Zoho CRM", "https://zohocrm.zohomcp.com/mcp/message", N_("Manages CRM leads, deals and contacts.")),
    _svc("zoho_desk", "Zoho Desk", "https://zohodesk.zohomcp.com/mcp/message", N_("Handles customer support tickets.")),
    _svc("zoho_projects", "Zoho Projects", "https://zohoprojects.zohomcp.com/mcp/message", N_("Manages projects and tasks.")),
    _svc("zoho_show", "Zoho Show", "https://show.zoho.com/mcp", N_("Makes and edits presentations.")),
    # Added 2026-11-07, Client ID Metadata Documents only (no registration endpoint). Checked by opening each sign-in page with the
    # document's address as client_id and the relay as return address: each went on to its own login page without a client error. None of
    # them fetched the document before the login, so they check the client only after it; a refusal would show only then.
    # 2026-11-09: ElevenLabs' docs: "No client registration is needed for clients that support hosted client metadata (CIMD)".
    # 2026-11-09: Wise (moved to a key preset) and MongoDB Atlas (catalog.HIDDEN) refused this app after the owner signed in, without ever
    # fetching the document (none of its fetches in the server log): they accept only client domains they trust. ElevenLabs used to point
    # at its Claude-marketplace address, whose sign-in ended on claude.ai; it now uses the generic one (same for Descript, TikTok and Zoho
    # below/above, which had claude/anthropic addresses).
    # 2026-11-09 allowlist audit (every connector's metadata and sign-in page with a made-up client and with this app's document, read
    # while watching the operator's log for document fetches, plus each vendor's docs): moved to key presets because their docs say the
    # sign-in takes only listed apps (or an admin-approved one) while the same MCP address takes a personal key: monday.com ("pre-approved
    # third-party agents"), Zapier ("Any MCP client on the supported list"), incident.io ("Built-in clients like Claude, ChatGPT, and
    # Cursor are trusted automatically"), Datadog (an org admin allow-lists each return address). Ramp and Greenhouse: catalog.HIDDEN.
    # Services that may refuse only after the login, or whose organisation admin decides: catalog.SIGN_IN_CAUTION.
    _svc("elevenlabs", "ElevenLabs", "https://api.elevenlabs.io/v1/mcp", N_("Sets up voice agents and turns text into speech.")),
    _svc("mentimeter", "Mentimeter", "https://ai.mentimeter.com/api/mcp", N_("Makes interactive presentations with polls and quizzes.")),
    _svc("mongodb_atlas", "MongoDB Atlas", "https://mcp.mongodb.com/", N_("Manages Atlas clusters and reads and writes data.")),
    # Added 2026-11-07: reads Client ID Metadata Documents (and refuses this app's registrations). Checked by opening its sign-in page with
    # the document's address as client_id and the relay as return address: it sends the browser on to the Kakao login; it checks the client
    # only after the login, so a refusal would show only then.
    _svc("kakao_playmcp", "Kakao PlayMCP", "https://playmcp.kakao.com/mcp", N_("Uses the tools you picked in PlayMCP.")),
]}


class McpOAuthError(Exception):
    """Something the user can act on; the text is shown in the app."""


def refused(store: Store, service: str) -> bool:
    """Whether this service turned this app down (after a login, or at registration) with no other way left to try, within
    REFUSAL_DAYS. The Connectors screen then offers the other way (catalog.SIGN_IN_CAUTION) or says why it can't be connected."""
    kind, _, when = store.meta(CIMD_REFUSED + service).partition(" ")
    if kind != REFUSED:
        return False
    try:
        return time.time() - float(when) < REFUSAL_DAYS * 86400
    except ValueError:
        return True


def remember_refusal(store: Store, service: str) -> None:
    store.set_meta(CIMD_REFUSED + service, f"{REFUSED} {time.time():.0f}")


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def clean_origin(origin: str) -> str:
    """The address the user's phone reaches this app at (https, or http only on this computer). The service sends the user back there."""
    p = urlsplit((origin or "").strip().rstrip("/"))
    local = (p.hostname or "") in ("localhost", "127.0.0.1", "::1")
    if p.scheme not in ("https", "http") or (p.scheme == "http" and not local) or not p.hostname or p.username or p.path or p.query or p.fragment:
        raise McpOAuthError(t("This app's address is not valid. Open the app at its https address."))
    return f"{p.scheme}://{p.netloc}"


def client_metadata_url(relay: str) -> str:
    """The client_id this app uses with services that read a Client ID Metadata Document: the document on the server that holds the
    shared relay page (the operator's), since the document has to list that page as the return address. "" without an https relay."""
    p = urlsplit((relay or "").strip())
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return ""
    return f"https://{p.netloc.lower()}{CLIENT_METADATA_PATH}"


def client_metadata(relay: str) -> dict[str, Any]:
    """The public JSON document named by client_metadata_url(relay). Fields from RFC 7591 as the draft and the MCP authorization spec ask:
    client_id equal to the document's own address, a name, and the exact return addresses; no secret, public client (PKCE)."""
    url = client_metadata_url(relay)
    if not url:
        return {}
    return {"client_id": url, "client_name": CLIENT_NAME, "client_uri": _origin(url), "redirect_uris": [relay.strip()],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}


def relay_state(nonce: str, origin: str) -> str:
    """The `state` sent to a service when the sign-in comes back through the shared relay page: the random part, then the address of
    the copy of the app that started it (the relay page sends the user on to it)."""
    return f"{nonce}.{_b64url(origin.encode())}"


def relay_target(state: str) -> str:
    """Where the relay page sends a returning sign-in, read from its `state`; "" when it names no acceptable address."""
    _, _, tail = (state or "").rpartition(".")
    try:
        origin = base64.urlsafe_b64decode(tail + "=" * (-len(tail) % 4)).decode()
        return clean_origin(origin)
    except (ValueError, McpOAuthError):
        return ""


class McpConnections:
    def __init__(self, store: Store, *, fetcher: Fetcher = fetch, relay: str = "",
                 services: Callable[[str], McpService | None] | None = None, ask: Callable[..., Any] | None = None):
        """`relay`: a fixed https page every copy of the app shares (registered once with services that want a fixed return address);
        it sends each returning sign-in on to the copy that started it. Empty: the service returns straight to this copy.
        With a relay, services that read Client ID Metadata Documents get the relay host's document as client_id."""
        self.store, self.fetcher, self.relay = store, fetcher, relay.strip()
        self.picker = ScopePick(ask) if ask is not None else None   # which listed scopes are this app's own (scope_pick.py); None: none are asked
        self.services = services or SERVICES.get   # `services`: finds a connector's MCP server (packages.Registry.mcp_service)
        self.metadata_url = client_metadata_url(self.relay)
        self.pending: dict[str, dict[str, Any]] = {}

    # ---- talking to the service
    async def _get_json(self, url: str) -> dict[str, Any] | None:
        try:
            r = await self.fetcher("GET", url, allowed_hosts=[urlsplit(url).hostname or ""], headers={"Accept": "application/json"})
        except NetGuardError:
            return None
        if r.status != 200:
            return None
        try:
            body = json.loads(r.body)
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    async def _post(self, url: str, *, form: dict[str, str] | None = None, body: dict[str, Any] | None = None,
                    headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        if urlsplit(url).scheme != "https":
            raise McpOAuthError(t("The service gave an address that is not https."))
        h = {"Accept": "application/json", **(headers or {})}
        if form is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
            content = urlencode(form).encode()
        else:
            h["Content-Type"] = "application/json"
            content = json.dumps(body or {}).encode()
        try:
            r = await self.fetcher("POST", url, allowed_hosts=[urlsplit(url).hostname or ""], headers=h, content=content)
        except NetGuardError as e:
            raise McpOAuthError(t("Couldn't connect to the service: {e}", e=e)) from None
        try:
            out = json.loads(r.body)
        except ValueError:
            out = {}
        return r.status, out if isinstance(out, dict) else {}

    async def discover(self, svc: McpService) -> dict[str, Any]:
        """Where to sign in, where to get tokens and how to identify this app (metadata document or registration), read from the
        service's published metadata."""
        scope = ""
        prm_urls: list[str] = []
        try:
            r = await self.fetcher("POST", svc.url, allowed_hosts=[urlsplit(svc.url).hostname or ""],
                                   headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                                   content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                                       "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "assistant", "version": "1"}}}).encode())
            www = r.headers.get("www-authenticate", "")
            for part in www.split(","):
                k, _, v = part.strip().removeprefix("Bearer ").partition("=")
                if k.strip() == "resource_metadata" and v.strip().strip('"').startswith("https://"):
                    prm_urls.append(v.strip().strip('"'))
                if k.strip() == "scope":
                    scope = v.strip().strip('"')
        except NetGuardError:
            pass
        p = urlsplit(svc.url)
        path = p.path.rstrip("/")
        prm_urls += [f"https://{p.netloc}/.well-known/oauth-protected-resource{path}", f"https://{p.netloc}/.well-known/oauth-protected-resource"]
        prm: dict[str, Any] = {}
        for u in dict.fromkeys(prm_urls):
            got = await self._get_json(u)
            if got and got.get("authorization_servers"):
                prm = got
                break
        issuer = str((prm.get("authorization_servers") or [_origin(svc.url)])[0]).rstrip("/")
        i = urlsplit(issuer)
        ipath = i.path.rstrip("/")
        meta: dict[str, Any] = {}
        for u in dict.fromkeys([f"https://{i.netloc}/.well-known/oauth-authorization-server{ipath}", f"https://{i.netloc}/.well-known/oauth-authorization-server",
                                f"https://{i.netloc}/.well-known/openid-configuration{ipath}", f"{issuer}/.well-known/openid-configuration"]):
            got = await self._get_json(u)
            if got and got.get("authorization_endpoint") and got.get("token_endpoint"):
                meta = got
                break
        if not meta:
            raise McpOAuthError(t("Couldn't find {name}'s sign-in details. Try again in a moment.", name=svc.name))
        cimd = meta.get("client_id_metadata_document_supported") is True
        if not meta.get("registration_endpoint"):
            if cimd and not self.metadata_url:
                raise McpOAuthError(t("{name} can only connect through a server with a fixed sign-in address (relay), and this server has none.",
                                      name=svc.name))
            if not cimd:
                raise McpOAuthError(t("{name} doesn't support automatic sign-up, so it can't be connected.", name=svc.name))
        methods = meta.get("code_challenge_methods_supported")
        if methods and "S256" not in methods:
            raise McpOAuthError(t("{name} doesn't support the safe sign-in method (PKCE).", name=svc.name))
        for k in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            if meta.get(k) and not str(meta[k]).startswith("https://"):
                raise McpOAuthError(t("{name} gave an address that is not https.", name=svc.name))
        if not scope:   # no scope named by the server's own challenge: only the model's pick of the listed ones (some, e.g. Kakao PlayMCP: ["default"], list them only in the metadata)
            listed = [str(s) for s in (prm.get("scopes_supported") or meta.get("scopes_supported") or [])]
            scope = " ".join(await self.picker.pick(svc.id, listed)) if self.picker is not None else ""
        return {"authorization_endpoint": meta["authorization_endpoint"], "token_endpoint": meta["token_endpoint"],
                "registration_endpoint": str(meta.get("registration_endpoint") or ""), "resource": str(prm.get("resource") or svc.url),
                "scope": scope, "auth_methods": meta.get("token_endpoint_auth_methods_supported") or [], "cimd": cimd}

    # ---- connecting
    def sweep(self) -> None:
        now = time.time()
        for k, v in list(self.pending.items()):
            if now - v["created"] > PENDING_SECONDS:
                self.pending.pop(k, None)

    async def start(self, service: str, origin: str) -> str:
        """Identify this app to the service (metadata document, else registration) and return the address of its sign-in page."""
        svc = self.services(service)
        if svc is None:
            raise McpOAuthError(t("This service can't be connected this way."))
        here = clean_origin(origin)
        if refused(self.store, svc.id):   # it said no last time and nothing else is left to try: no point sending the user to log in again
            raise McpOAuthError(t("{name} only accepts apps it approved in advance, so this app can't connect to it.", name=svc.name))
        redirect = self.relay or here + "/oauth/callback"
        if svc.oauth is not None:
            return self._start_static(svc, here, redirect)
        info = await self.discover(svc)
        verifier = _b64url(secrets.token_bytes(32))
        state = relay_state(_b64url(secrets.token_bytes(24)), here) if self.relay else _b64url(secrets.token_bytes(24))
        sep = "&" if urlsplit(info["authorization_endpoint"]).query else "?"

        def sign_in_url(client_id: str) -> str:
            query = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": state,
                     "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
                     "resource": info["resource"], **({"scope": info["scope"]} if info["scope"] else {})}
            return info["authorization_endpoint"] + sep + urlencode(query)

        can_register = bool(info["registration_endpoint"])
        client: dict[str, str] | None = None
        if info["cimd"] and self.metadata_url and not (can_register and self.store.meta(CIMD_REFUSED + svc.id)):
            client = {"client_id": self.metadata_url, "client_secret": "", "auth_method": "none", "registration": "cimd"}
            # Some servers that say they read metadata documents turn this one down at once (an error page instead of their login):
            # register instead when they can, rather than send the user to that error page.
            if await self._refused(sign_in_url(self.metadata_url)):
                if not can_register:
                    remember_refusal(self.store, svc.id)
                    raise McpOAuthError(t("{name} only accepts apps it approved in advance, so this app can't connect to it.", name=svc.name))
                client = None
        if client is None:
            client = await self._register(svc, info, redirect)
        self.sweep()
        self.pending[state] = {"service": svc.id, "verifier": verifier, "redirect": redirect, "created": time.time(), **client,
                               "token_endpoint": info["token_endpoint"], "resource": info["resource"], "origin": here,
                               "can_register": can_register}
        self.store.add_vault_log(svc.item, "requested", f"OAuth connection started ({client['registration']})")
        return sign_in_url(client["client_id"])

    def _start_static(self, svc: McpService, here: str, redirect: str) -> str:
        """An OAuth API whose package names its endpoints and client id (public, or the owner's own): authorization code with PKCE."""
        o = svc.oauth
        if not o.get("client_id"):
            raise McpOAuthError(t("{name} needs your own app's client id first. Add it in the package's settings, then connect again.", name=svc.name))
        verifier = _b64url(secrets.token_bytes(32))
        state = relay_state(_b64url(secrets.token_bytes(24)), here) if self.relay else _b64url(secrets.token_bytes(24))
        query = {**dict(o.get("params") or {}), "response_type": "code", "client_id": o["client_id"], "redirect_uri": redirect, "state": state,
                 "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
                 **({"scope": " ".join(o["scopes"])} if o.get("scopes") else {})}
        self.sweep()
        self.pending[state] = {"service": svc.id, "verifier": verifier, "redirect": redirect, "created": time.time(), "client_id": o["client_id"],
                               "client_secret": "", "auth_method": "none", "registration": "static", "token_endpoint": o["token_url"],
                               "resource": "", "origin": here, "can_register": False}
        self.store.add_vault_log(svc.item, "requested", "OAuth connection started (package)")
        sep = "&" if urlsplit(o["authorize_url"]).query else "?"
        return o["authorize_url"] + sep + urlencode(query)

    async def _refused(self, url: str) -> bool:
        """Whether the sign-in page answers this request with a client error straight away (HTTP 400/401, as RFC 6749 asks for an unknown
        client: an error shown, not a redirect back). A redirect (to the login) or a page is taken as accepted so far; so is any failure."""
        try:
            r = await self.fetcher("GET", url, allowed_hosts=[urlsplit(url).hostname or ""], headers={"Accept": "text/html,application/json"})
        except NetGuardError:
            return False
        return r.status in (400, 401)

    async def _register(self, svc: McpService, info: dict[str, Any], redirect: str) -> dict[str, str]:
        """Dynamic client registration (RFC 7591): a new client for this copy, made on the spot."""
        method = "none" if not info["auth_methods"] or "none" in info["auth_methods"] else "client_secret_post"
        status, reg = await self._post(info["registration_endpoint"], body={
            "client_name": CLIENT_NAME, "redirect_uris": [redirect], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": method, **({"scope": info["scope"]} if info["scope"] else {})})
        if status in (401, 403):
            remember_refusal(self.store, svc.id)
            raise McpOAuthError(t("{name} only accepts apps it approved in advance, so this app can't connect to it.", name=svc.name))
        if status not in (200, 201) or not reg.get("client_id"):
            detail = str(reg.get("error_description") or reg.get("error") or f"HTTP {status}")[:120]
            raise McpOAuthError(t("Couldn't sign this app up with {name} ({detail}).", name=svc.name, detail=detail))
        return {"client_id": str(reg["client_id"]), "client_secret": str(reg.get("client_secret") or ""),
                "auth_method": str(reg.get("token_endpoint_auth_method") or method), "registration": "dcr"}

    def _client_auth(self, p: dict[str, Any], form: dict[str, str]) -> dict[str, str]:
        if p["client_secret"] and p["auth_method"] == "client_secret_basic":
            return {"Authorization": "Basic " + base64.b64encode(f"{p['client_id']}:{p['client_secret']}".encode()).decode()}
        if p["client_secret"]:
            form["client_secret"] = p["client_secret"]
        return {}

    async def finish(self, state: str, code: str = "", error: str = "") -> tuple[McpService | None, str, str]:
        """The service sent the user back. Returns (service, the app's address to go back to, a problem to show or "")."""
        self.sweep()
        p = self.pending.pop(state or "", None)
        if p is None:
            return None, "", t("This connection request has expired or was already handled. Connect again from the app.")
        svc = self.services(p["service"])
        if svc is None:   # an installed package removed while its sign-in page was open
            return None, p["origin"], t("This service can't be connected this way.")
        if error or not code:
            self.store.add_vault_log(svc.item, "failed", f"OAuth: {(error or 'no code')[:60]}", ok=False)
            if self._note_refusal(svc, p, error):
                return svc, p["origin"], t("{name} didn't accept this app's sign-in this way. Connect again from the app and it will try another way.",
                                           name=svc.name)
            if refused(self.store, svc.id):
                return svc, p["origin"], t("{name} only accepts apps it approved in advance, so this app can't connect to it.", name=svc.name)
            return svc, p["origin"], "access_denied" if error == "access_denied" else (error or "no code")
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": p["redirect"], "client_id": p["client_id"],
                "code_verifier": p["verifier"], **({"resource": p["resource"]} if p["resource"] else {})}
        headers = self._client_auth(p, form)
        try:
            status, tok = await self._post(p["token_endpoint"], form=form, headers=headers)
        except McpOAuthError as e:
            return svc, p["origin"], str(e)
        if status != 200 or not tok.get("access_token"):
            self.store.add_vault_log(svc.item, "failed", f"OAuth token: HTTP {status}", ok=False)
            if self._note_refusal(svc, p, str(tok.get("error") or "")):
                return svc, p["origin"], t("{name} didn't accept this app's sign-in this way. Connect again from the app and it will try another way.",
                                           name=svc.name)
            if refused(self.store, svc.id):
                return svc, p["origin"], t("{name} only accepts apps it approved in advance, so this app can't connect to it.", name=svc.name)
            return svc, p["origin"], t("Couldn't get a token ({error}).", error=str(tok.get('error') or status)[:60])
        self._save(svc, p, tok)
        return svc, p["origin"], ""

    def _note_refusal(self, svc: McpService, p: dict[str, Any], error: str) -> bool:
        """A service that took the metadata document at first but refused the client later (after the login): remember it, so the next
        try registers instead (when the service offers registration). True when that next try will be different. A refusal of the
        client with nothing else left to try (no registration, or a registered client refused) is remembered too (refused())."""
        if error not in REFUSAL_ERRORS:
            return False
        if p.get("registration") == "cimd" and p.get("can_register"):
            self.store.set_meta(CIMD_REFUSED + svc.id, "1")
            return True
        if error in CLIENT_REFUSALS:
            remember_refusal(self.store, svc.id)
        return False

    def _save(self, svc: McpService, p: dict[str, Any], tok: dict[str, Any]) -> None:
        hosts = list(svc.oauth["hosts"]) if svc.oauth is not None else [urlsplit(svc.url).hostname or ""]
        endpoint = "" if svc.oauth is not None else svc.url   # an OAuth API is used with connector_get / connector_send, not MCP
        try:
            self.store.save_credential(svc.item, svc.name, hosts, {"type": "bearer_header", "via": "oauth"}, endpoint, kind="api")
        except VaultError:
            self.store.delete_credential(svc.item)
            self.store.save_credential(svc.item, svc.name, hosts, {"type": "bearer_header", "via": "oauth"}, endpoint, kind="api")
        self.store.set_credential_secret(svc.item, json.dumps(bundle(p, tok)))
        self.store.add_vault_log(svc.item, "saved", "OAuth connection")


def bundle(p: dict[str, Any], tok: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    expires = tok.get("expires_in")
    return {"mark": MARK, "access_token": str(tok["access_token"]),
            "refresh_token": str(tok.get("refresh_token") or (old or {}).get("refresh_token") or ""),
            "expires_at": time.time() + float(expires) if isinstance(expires, (int, float)) and expires > 0 else 0,
            "token_endpoint": p["token_endpoint"], "client_id": p["client_id"], "client_secret": p.get("client_secret", ""),
            "auth_method": p.get("auth_method", "none"), "resource": p.get("resource", ""), "registration": p.get("registration", "dcr")}


def is_bundle(secret: str | None) -> bool:
    return bool(secret) and secret.startswith("{") and f'"mark": "{MARK}"' in secret


async def open_token(store: Store, cred: dict[str, Any], fetcher: Fetcher = fetch) -> dict[str, Any]:
    """The credential ready for one request: an OAuth bundle becomes its access token, renewed first when it has run out; a Zoom app or a
    Google service account is exchanged for a short token."""
    from .keyconnect import is_minted, open_minted
    if is_minted(cred.get("secret")):
        return await open_minted(store, cred, fetcher)
    if not is_bundle(cred.get("secret")):
        return cred
    b = json.loads(cred["secret"])
    if b["expires_at"] and time.time() > b["expires_at"] - 60:
        if not b["refresh_token"]:
            store.set_credential_result(cred["name"], "rejected", "the sign-in ran out")
            raise VaultError(f"the connection to {cred['label']} has run out. Ask the user to connect it again in the Connectors tab.")
        conn = McpConnections(store, fetcher=fetcher)
        form = {"grant_type": "refresh_token", "refresh_token": b["refresh_token"], "client_id": b["client_id"]}
        if b["resource"]:
            form["resource"] = b["resource"]
        headers = conn._client_auth(b, form)
        try:
            status, tok = await conn._post(b["token_endpoint"], form=form, headers=headers)
        except McpOAuthError:
            status, tok = 0, {}
        if status != 200 or not tok.get("access_token"):
            store.set_credential_result(cred["name"], "rejected", "the service did not renew the sign-in")
            store.add_vault_log(cred["name"], "failed", "OAuth renewal refused", ok=False)
            raise VaultError(f"{cred['label']} did not renew the connection. Ask the user to connect it again in the Connectors tab.")
        b = bundle(b, tok, old=b)
        store.set_credential_secret(cred["name"], json.dumps(b))
        store.add_vault_log(cred["name"], "replaced", "OAuth sign-in renewed")
    return {**cred, "secret": b["access_token"]}
