"""Every outside service Kelo knows: id, display name and a one-line description (English; translated later).
How each one is connected lives in connect_ways.py (presets.py, mcp_oauth.py, oauth_connect.py, mail.py).
To add a connector without changing this code, make a package folder: server/examples/packages/README.md (quick start), docs/packages.md.

`regions`: countries (ISO 3166 alpha-2) where the service is widely used. The Connectors screen features those services for a
device in that region (connectors.py), after the worldwide featured list. A regional service's name and description are
translated (i18n.py), since they are shown to people who read that language."""

from __future__ import annotations

from typing import NamedTuple

from .i18n import N_
from .presets import SELF_HOSTED_IDS  # noqa: F401  (loads presets first: selfhosted needs it)
from .selfhosted import DESCRIPTIONS as _SH_DESC, NAMES as _SH_NAMES


class Service(NamedTuple):
    id: str
    name: str
    description: str
    regions: tuple[str, ...] = ()


CATALOG: tuple[Service, ...] = (
    Service("adobe_workfront", "Adobe Workfront", "Work management: projects, tasks and approvals for teams"),
    Service("agoda", "Agoda", "Search hotels and compare stay prices"),
    Service("ahrefs", "Ahrefs", "SEO data: backlinks, keywords and site rankings"),
    Service("airtable", "Airtable", "Look up and update records in your Airtable bases"),
    Service("amplitude", "Amplitude", "Product analytics: user behavior, funnels and retention"),
    Service("apify", "Apify", "Run web scrapers and automation actors, then collect the results"),
    Service("apollo", "Apollo.io", "Find sales leads and company contacts"),
    Service("apple_healthkit", "Apple Health", "Steps, sleep and other health data stored on an iPhone"),
    Service("asana", "Asana", "Tasks and projects for team planning"),
    Service("atlassian", "Atlassian", "Jira issues and Confluence pages"),
    Service("attio", "Attio", "A customizable CRM for contacts, companies and deals"),
    Service("basecamp", "Basecamp", "Team to-dos, message boards and project files"),
    Service("beehiiv", "beehiiv", "Newsletter publishing and subscriber management"),
    Service("betterstack", "Better Stack", "Uptime checks, logs and on-call alerts"),
    Service("bitly", "Bitly", "Create short links and see how many clicks they get"),
    Service("box", "Box", "Cloud storage for files and shared folders"),
    Service("brex", "Brex", "Company cards, spending and expense tracking"),
    Service("buildkite", "Buildkite", "Check and trigger CI pipelines and builds"),
    Service("caldav_calendar", "CalDAV Calendar", "Read a calendar from any CalDAV provider: Fastmail, Zoho, Nextcloud and more"),
    Service("calendly", "Calendly", "Meeting booking pages and scheduled events"),
    Service("canva", "Canva", "Design graphics, slides and social posts"),
    Service("clickhouse", "ClickHouse", "Run fast analytical SQL queries on a ClickHouse database"),
    Service("clickup", "ClickUp", "Tasks, docs and goals in one workspace"),
    Service("close", "Close", "A sales CRM with calls, email and pipelines"),
    Service("cloudflare_observability", "Cloudflare Observability", "Logs and traffic analytics for your Cloudflare services"),
    Service("cloudflare_workers", "Cloudflare Workers", "Deploy and manage serverless code on Cloudflare's network"),
    Service("cloudinary", "Cloudinary", "Store, transform and deliver images and video"),
    Service("coinmarketcap", "CoinMarketCap", "Crypto prices, rankings and market caps"),
    Service("contentful", "Contentful", "A headless CMS for structured website and app content"),
    Service("customer_io", "Customer.io", "Automated email and messages triggered by customer activity"),
    Service("datadog", "Datadog", "Monitoring: metrics, logs and alerts for your systems"),
    Service("deepl", "DeepL", "Machine translation between many languages"),
    Service("descript", "Descript", "Edit audio and video by editing the transcript"),
    Service("digitalocean", "DigitalOcean", "Cloud servers, databases and apps"),
    Service("discord", "Discord", "Talk to Kelo from Discord, alone or in your own servers"),
    Service("dropbox", "Dropbox", "Cloud file storage and sharing"),
    Service("egnyte", "Egnyte", "Business file storage with sharing controls"),
    Service("elevenlabs", "ElevenLabs", "AI voices for speech, dubbing and voice agents"),
    Service("evernote", "Evernote", "Notes, clippings and notebooks"),
    Service("exa", "Exa", "A search engine built for AI, returning pages by meaning"),
    Service("expedia", "Expedia", "Search flights, hotels and rental cars"),
    Service("expo", "Expo", "Build and ship React Native mobile apps"),
    Service("facebook_cli", "Facebook", "Read your feed and pages and post through the website"),
    Service("fathom", "Fathom", "Recordings, summaries and action items from video calls"),
    Service("figma", "Figma", "Design files, components and comments"),
    Service("firecrawl", "Firecrawl", "Turn web pages into clean text for reading and research"),
    Service("fireflies", "Fireflies.ai", "Meeting transcripts and searchable call notes"),
    Service("framer", "Framer", "Design and publish websites"),
    Service("freshbooks", "FreshBooks", "Invoices, expenses and time tracking for small business"),
    Service("github", "GitHub", "Issues, pull requests and code in your repositories"),
    Service("gitlab", "GitLab", "Repositories, merge requests and CI pipelines"),
    Service("gmail", "Gmail", "Read, search, organize and send email"),
    Service("google_calendar", "Google Calendar", "See your schedule and create or move events"),
    Service("google_contacts", "Google Contacts", "Look up people, phone numbers and addresses"),
    Service("google_docs", "Google Docs", "Read and write documents"),
    Service("google_drive", "Google Drive", "Find, read and organize files in your Drive"),
    Service("google_forms", "Google Forms", "Create forms and read the responses"),
    Service("google_health_connect", "Health Connect", "Fitness and health data stored on an Android phone"),
    Service("google_sheets", "Google Sheets", "Read and update spreadsheet cells and tabs"),
    Service("google_slides", "Google Slides", "Create and edit presentations"),
    Service("google_tasks", "Google Tasks", "Your to-do lists in Google"),
    Service("grab_mcp", "Grab", "Rides and food delivery across Southeast Asia"),
    Service("grafana_cloud", "Grafana Cloud", "Dashboards, metrics and logs for your infrastructure"),
    Service("greenhouse", "Greenhouse", "Hiring: job openings, candidates and interview stages"),
    Service("hugging_face", "Hugging Face", "Browse AI models, datasets and demo apps"),
    Service("ibkr", "Interactive Brokers", "Brokerage account, positions and market data"),
    Service("imap_email", "IMAP Email", "Any mailbox that supports IMAP: read, organize and send"),
    Service("incident_io", "incident.io", "Declare, track and review production incidents"),
    Service("instacart", "Instacart", "Grocery shopping and delivery from local stores"),
    Service("instagram", "Instagram", "Browse your feed, profiles and messages through the website"),
    Service("jotform", "Jotform", "Online forms and submissions"),
    Service("kit", "Kit", "Email marketing for creators, with subscribers and sequences"),
    Service("krisp", "Krisp", "Meeting notes and noise-free call recordings"),
    Service("launchdarkly", "LaunchDarkly", "Feature flags and gradual rollouts"),
    Service("linear", "Linear", "Issue tracking and sprint planning for software teams"),
    Service("lucid", "Lucid", "Diagrams and flowcharts"),
    Service("mailerlite", "MailerLite", "Email newsletters, lists and campaigns"),
    Service("make", "Make", "Visual automation scenarios that link apps together"),
    Service("mapbox", "Mapbox", "Maps, geocoding and routing"),
    Service("mentimeter", "Mentimeter", "Live polls, quizzes and word clouds for presentations"),
    Service("mercury", "Mercury", "Online business banking with accounts and transactions"),
    Service("messenger_read", "Messenger", "Read your Facebook chats through the website"),
    Service("meta_catalog_search", "Facebook Marketplace", "Search local listings on facebook.com/marketplace"),
    Service("metricool", "Metricool", "Plan social posts and track how they perform"),
    Service("mintlify", "Mintlify", "Documentation sites for products and APIs"),
    Service("miro", "Miro", "Online whiteboards for brainstorming and planning"),
    Service("mixpanel", "Mixpanel", "Event analytics for product usage"),
    Service("monday", "monday.com", "Boards for tracking work, projects and workflows"),
    Service("mongodb_atlas", "MongoDB Atlas", "Cloud MongoDB databases and clusters"),
    Service("motherduck", "MotherDuck", "Cloud DuckDB for querying data with SQL"),
    Service("neon", "Neon", "Serverless Postgres databases with branching"),
    Service("netlify", "Netlify", "Deploy and host websites"),
    Service("notion", "Notion", "Pages, databases and wikis"),
    Service("opentable", "OpenTable", "Find restaurants and book tables"),
    Service("otter", "Otter.ai", "Live transcription and summaries of conversations"),
    Service("outlook_calendar", "Outlook Calendar", "Microsoft calendar events and invitations"),
    Service("outlook_contacts", "Outlook Contacts", "Your Microsoft address book"),
    Service("outlook_mail", "Outlook Mail", "Microsoft email: read, search and send"),
    Service("pandadoc", "PandaDoc", "Create, send and e-sign proposals and contracts"),
    Service("paypal", "PayPal", "Payments, invoices and transaction history"),
    Service("philips_hue", "Philips Hue", "Smart lights you control from home"),
    Service("places", "Places Search", "Find businesses and points of interest on a map"),
    Service("planetscale", "PlanetScale", "Managed MySQL and Postgres databases"),
    Service("posthog", "PostHog", "Product analytics, session replay and feature flags"),
    Service("postman", "Postman", "API collections, requests and tests"),
    Service("prisma", "Prisma", "Database tooling and hosted Postgres for apps"),
    Service("pulumi", "Pulumi", "Cloud infrastructure defined in code"),
    Service("quickbooks", "QuickBooks", "Small-business accounting, invoices and books"),
    Service("railway", "Railway", "Deploy apps and databases to the cloud"),
    Service("ramp", "Ramp", "Corporate cards, expenses and bill pay"),
    Service("read_ai", "Read AI", "Meeting summaries, highlights and engagement reports"),
    Service("resend", "Resend", "Send transactional email from your own domain"),
    Service("restream", "Restream", "Broadcast a live stream to several platforms at once"),
    Service("sanity", "Sanity", "A content platform with editable structured data"),
    Service("sentry", "Sentry", "Errors and performance problems in your apps"),
    Service("shop-pay", "Shop Pay", "An accelerated checkout wallet used by Shopify stores"),
    Service("shopback_mcp", "ShopBack", "Cashback and deals at online shops"),
    Service("shopify", "Shopify", "Products, orders and customers in your online store"),
    Service("shopify-ucp", "Shopify UCP", "A shared protocol for AI agents to shop at Shopify stores"),
    Service("slack", "Slack", "Read channels, post messages, and talk to Kelo from Slack"),
    Service("spotify", "Spotify", "Music, podcasts and playlists"),
    Service("stripe", "Stripe", "Customers, payments and subscriptions"),
    Service("stripe-link", "Link by Stripe", "A saved-payment wallet for one-click checkout"),
    Service("supabase", "Supabase", "Postgres databases, auth and storage for apps"),
    Service("superhuman_docs", "Superhuman Docs", "Documents written and edited with AI help"),
    Service("superhuman_mail", "Superhuman Mail", "A fast email client with split inboxes and reminders"),
    Service("tavily", "Tavily", "Web search and page extraction made for AI"),
    Service("telegram", "Telegram", "Talk to Kelo from Telegram, and get its messages there"),
    Service("threads", "Threads", "Read and post short text updates through the website"),
    Service("ticketmaster", "Ticketmaster", "Find concerts, sports and shows and their tickets"),
    Service("ticktick", "TickTick", "To-do lists, reminders and habit tracking"),
    Service("tiktok_ads", "TikTok for Business", "Ad campaigns and results on TikTok"),
    Service("tldv", "tl;dv", "Recorded and transcribed meetings with highlights"),
    Service("todoist", "Todoist", "Tasks, projects and due dates"),
    Service("trello", "Trello", "Kanban boards, lists and cards"),
    Service("typefully", "Typefully", "Write, schedule and publish posts for X and other networks"),
    Service("v0", "v0", "Generate web interfaces from a text prompt"),
    Service("vercel", "Vercel", "Deployments, domains and logs for web projects"),
    Service("vpn_proxy", "Proxy", "Send Kelo's web browsing through a SOCKS5 or HTTP proxy"),
    Service("vpn_wireguard", "VPN (WireGuard)", "Send Kelo's web browsing through your own VPN"),
    Service("webflow", "Webflow", "Visual website builder with a content manager"),
    Service("webull", "Webull", "Stock trading, quotes and portfolio"),
    Service("wise", "Wise", "Send money abroad and hold many currencies"),
    Service("wix", "Wix", "Build and manage websites and online stores"),
    Service("wordpress_com", "WordPress.com", "Publish and manage posts on a hosted WordPress site"),
    Service("workable", "Workable", "Recruiting: job posts, applicants and hiring pipelines"),
    Service("workos", "WorkOS", "Enterprise sign-in and user directories for apps"),
    Service("zapier", "Zapier", "Automations that connect thousands of apps"),
    Service("zoho_books", "Zoho Books", "Accounting: invoices, bills and bank reconciliation"),
    Service("zoho_crm", "Zoho CRM", "Leads, deals and customer records"),
    Service("zoho_desk", "Zoho Desk", "Customer support tickets and help center"),
    Service("zoho_projects", "Zoho Projects", "Project plans, tasks and timesheets"),
    Service("zoho_show", "Zoho Show", "Create and share slide presentations"),
    Service("zoom", "Zoom", "Video meetings, schedules and recordings"),

    # used mostly in Korea; opened in Kelo's browser with a login from the keychain
    Service("naver", N_("Naver"), N_("Search, mail, calendar, shopping and maps on Korea's largest portal"), ("KR",)),
    Service("kakao", N_("Kakao"), N_("Kakao Map for places, routes and opening hours"), ("KR",)),
    Service("coupang", N_("Coupang"), N_("Shop Korea's big online store with fast delivery"), ("KR",)),
    Service("baemin", N_("Baemin"), N_("Order food delivery from nearby restaurants"), ("KR",)),
    Service("toss", N_("Toss"), N_("Check accounts, spending and transfers in Toss"), ("KR",)),
    Service("daangn", N_("Daangn"), N_("Buy and sell second-hand items with neighbours"), ("KR",)),
    # used mostly in Korea; a hosted MCP server signed in with a Kakao account
    Service("kakao_playmcp", N_("Kakao PlayMCP"), N_("Kakao's hub of AI tools, reached with one Kakao sign-in"), ("KR",)),
    # apps the owner runs on their own network (selfhosted.py); shown under their own group
    *(Service(cid, _SH_NAMES[cid], desc) for cid, desc in _SH_DESC.items()),
)
BY_ID: dict[str, Service] = {s.id: s for s in CATALOG}

# Kept in the catalog but not shown on the Connectors screen (unless already connected), with the reason.
HIDDEN: dict[str, str] = {
    # 2026-11: its sign-in server rejects clients it has not approved (CIMD document never fetched, registration IP-restricted)
    "kakao_playmcp": "Kakao only lets apps it has approved connect",
    # 2026-11 probe (POST /api/mcp-oauth/<id>/start for every MCP connector): registration refused or not offered
    "dropbox": "Dropbox only lets apps it has approved connect",
    "grab_mcp": "Grab's sign-in has no registration for outside apps (HTTP 404)",
    "shopback_mcp": "ShopBack offers no registration for outside apps",
    # (tl;dv was here: its sign-in turns the client metadata document down at once; since 2026-11-09 Kelo registers there instead.)
    # 2026-11-09 audit: its sign-in page sends the browser to http://api.read.ai/oauth/login, which does not answer (the https one works):
    # the page never opens. On Read AI's side; check again later.
    "read_ai": "Read AI's sign-in page does not open (it sends the browser to an address that does not answer)",
    # 2026-11-09 (owner's sign-in): accepts only client domains it trusts; refused after the login without fetching the client document,
    # and offers no registration. Atlas API access needs a service account, which this app has no way for yet.
    # Its docs now say so: "This model works only with AI clients that MongoDB registers in advance"; for your own agent, "use
    # Programmatic Access" (an Atlas service account) https://www.mongodb.com/docs/mcp-server/remote-mcp/access-models/
    "mongodb_atlas": "MongoDB Atlas only lets apps it has approved connect",
    # 2026-11-09 allowlist audit (vendor docs; the refusal comes only after the login, so the probe alone could not show it):
    # "Custom clients and gateways must have their exact redirect URI allowlisted before Ramp authorization can complete"
    # https://agents.ramp.com/docs/mcp/setup (request form: https://docs.ramp.com/developer-api/v1/mcp-redirect-whitelist-request).
    # Its API keys are client-credential apps, which this app cannot exchange yet.
    "ramp": "Ramp only lets apps whose return address it approved connect",
    # "To add one, contact Greenhouse to have its redirect URI added to the allowlist. This is a manual step today."
    # https://support.greenhouse.io/hc/en-us/articles/53944054829083 (Harvest keys use Basic auth, which key presets cannot send yet).
    "greenhouse": "Greenhouse only lets apps it has approved connect",
}

# Hosted MCP sign-ins that may turn this app down only AFTER the user has logged in: the service keeps a list of approved apps
# ("approved"), or each organisation's admin keeps one ("admin"). Neither shows before the login (no metadata field says so, and the
# sign-in page opens the same for any client), so the Connectors screen says it before the sign-in opens (connect_ways: "note"), and
# offers `instead` (a presets.KEYS id) as the other way when there is one. A refusal that comes back to this app is remembered
# (mcp_oauth.CIMD_REFUSED) and the row then shows the other way, or a reason.
class Caution(NamedTuple):
    kind: str          # "approved" | "admin"
    instead: str = ""  # a key preset offered as the other way ("" = none)


SIGN_IN_CAUTION: dict[str, Caution] = {
    # 2026-11-09 audit; quotes from the vendors' own pages.
    # Stripe: "Stripe maintains an allowlist of vetted MCP client redirect URIs ... email us at mcp@stripe.com" (older docs.stripe.com/mcp
    # text); today's page: "If your client doesn't support OAuth, use an agent API key" (a Bearer key on mcp.stripe.com).
    "stripe": Caution("approved", "stripe_key"),
    # ClickUp: "ClickUp maintains an allowlist of vetted MCP clients" https://clickup.com/blog/how-to-use-clickup-mcp-server/ (its developer
    # FAQ is less strict); the MCP takes OAuth only, so the other way is a personal token for the REST API.
    "clickup": Caution("approved", "clickup_key"),
    # Mentimeter: names only ChatGPT and Claude; Client ID Metadata Documents only, and like Wise it never fetched this app's document
    # before the login (a made-up client_id gets an error page at once, this app's gets the login). No public API to fall back on.
    "mentimeter": Caution("approved"),
    # Atlassian and Trello: "The domains and patterns you configure here define which client apps are allowed to use the Atlassian MCP
    # server" (default: Atlassian-supported domains) https://support.atlassian.com/security-and-access-policies/docs/available-atlassian-rovo-mcp-server-domains/
    # Trello's error "Access to the domain is restricted" https://support.atlassian.com/trello/docs/troubleshoot-trello-mcp-connection-errors/
    # (the owner's own Atlassian site did connect on 2026-11-07).
    "atlassian": Caution("admin"),
    "trello": Caution("admin"),
    # Egnyte: "Unregistered Web MCP Clients option is available but disabled by default"; admins add allowed apps
    # https://helpdesk.egnyte.com/hc/en-us/articles/44361750683661
    "egnyte": Caution("admin"),
    # Mintlify: "To let other clients connect, add their redirect domains in the Redirect URIs section of MCP → Configure."
    # https://www.mintlify.com/docs/ai/mintlify-mcp
    "mintlify": Caution("admin"),
    # Adobe Workfront: "Only URLs on this list can complete login for your MCP agents" (admin: System Preferences → MCP preferences)
    # https://experienceleague.adobe.com/en/docs/workfront/using/basics/workfront-mcp-server/configure-workfront-mcp-server
    "adobe_workfront": Caution("admin"),
}
# How a connector is used that the Connectors screen leaves out (the agent can still open any site in its browser)
HIDDEN_WAYS: frozenset[str] = frozenset({"browser"})
