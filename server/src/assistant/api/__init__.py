"""The HTTP API, split by domain.

server.create_app() builds the services (store, push, wallet, the turn engine's helpers, ...) and puts them on one `AppContext`
(context.py). Each module below has `router(ctx) -> APIRouter`: it takes what it uses from the context at its top (its dependencies
in one place) and holds the routes of one domain. create_app includes them in the order the routes were always registered: order
matters (e.g. /api/payment-pins/deliveries before /api/payment-pins/{pin_id}, /api/schedules/archive before /api/schedules/{sid}).
server/tests/test_route_table.py pins every route, its order, methods, status code, auth and parameters (server/tests/fixtures/route_table.json).

Included in this order:

    grants          what conditional tasks / jobs may do on their own: list, revoke
    security        what the injection watch saw (GET /api/security/injections)
    actions         what Kelo sent, changed or deleted, and whether it is known to have gone through (GET /api/actions)
    chat            AI consent, /api/chat, /api/talk, /api/turns (start)
    calls           a call with Kelo answered in the app (POST /api/calls/{id}/answered; calls.py)
    nest            Kelo Nest screens on this server
    turns           follow or stop a reply
    profile         the owner's profile and language
    conversation    messages, clearing the chat, the rolling summary
    goals           goals and their workspace
    files           the agent's files, uploads, the library of results
    about           version and database health
    connectors      the connector list, icons, keys made on a service, MCP sign-ins, OAuth return pages
    mail_calendar   mailboxes, calendars, what a phone shares, the Google sign-in app and its broker
    providers       AI providers: list, consent, keys, endpoints, phone sign-in, CLI sidecar updates
    connections     registered services (credentials), OAuth device-flow providers, GitHub sign-in and its broker
    operator        addresses, push for every copy, reports, Google beta (/api/operator/*, /broker/*)
    vault           device-flow OAuth, vault logins / lock / log, fill-in cards
    devices         pairing, access codes, owner rights, removing a phone
    push            this phone's push registration
    resources       capacity and try-on pictures
    models_usage    model lists, agy install, token use and subscription windows
    wallet          cards, auto-pay limits, payment PINs and steps, shipping (WALLET API, SHIPPING API)
    approvals       approval cards, history, rules, per-task permissions
    agent_screen    live status and the task log
    schedules       schedules
    browser         browser tabs, the tab sweeper, run pictures, the live view (LIVE VIEW API)
    journal, skills, reflection, reviews, notifications, notices, site, forget (what Kelo was asked to forget)
    data            your data: export, import from another assistant, start over (data_controls.py)

Routes the modules register themselves (`x_routes(router, ...)`), included through module_router: workers, watchers, proactive, server updates, storage cleanup,
followups, briefing, briefing sources, checklist, chat images (inside conversation), goal workspace (inside goals).

Not routes: auth.py (`AuthGuard`: tokens, brute-force guard, the KE2E hook), provider_policy.py (`ProviderPolicy`), schemas.py
(request bodies shared by routers). The API comment blocks the apps' model tables follow (WALLET API, SHIPPING API, LIVE VIEW API,
...) stay next to the routes they describe; the contracts next to serialize() in server.py stay there.
"""

from collections.abc import Callable  # noqa: E402
from typing import Any  # noqa: E402

from fastapi import APIRouter  # noqa: E402


def module_router(register: Callable[..., None], *args: Any, **kw: Any) -> APIRouter:
    """A router for a module that adds its own routes with `register(router, ...)` (worker_routes, briefing_routes, ...)."""
    router = APIRouter()
    register(router, *args, **kw)
    return router


# The routers, in the order their routes are registered (order matters for overlapping paths; server/tests/test_route_table.py pins it).
ROUTERS = ("chat", "calls", "nest", "turns", "profile", "conversation", "goals", "files", "about", "connectors", "mail_calendar", "providers",
           "connections", "operator", "vault", "devices", "e2e_keys", "keyguard", "push", "resources", "models_usage", "wallet",
           "approvals", "agent_screen", "schedules", "browser", "journal", "skills", "reflection", "reviews", "notifications", "notices", "site", "forget", "data")


def include_api(app: Any, ctx: Any) -> None:
    """Build every router of ROUTERS from the app context and include it, in order. A router may put helpers it owns on the context
    (connections: device_provider, read by vault), so the order is also the order they are built in."""
    import importlib
    for name in ROUTERS:
        app.include_router(importlib.import_module(f"{__name__}.{name}").router(ctx))
