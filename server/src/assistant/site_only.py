"""Site-only mode (ASSISTANT_SITE_ONLY=1, docs/site-only.md): a copy that serves kelo.chat, the downloads and install scripts, the
sign-in, push, report and beta brokers, and the operator's lists, and runs no assistant of its own.

`SiteRoutes` is an allowlist: a request is served only when its method and path are on the list below. Every other request
answers 404 {"code": "site_only"} and every websocket closes with 4404, so a route added later is refused on a site copy until it
is listed here on purpose. `SiteOnlyGate` is the ASGI layer that applies the list; create_app adds it outermost."""

from __future__ import annotations

import json
import re
from typing import Any

# (methods, path pattern). A pattern is a regular expression matched against the whole path. HEAD is answered as GET.
_ROUTES: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = tuple(
    (frozenset(methods), re.compile(pattern)) for methods, pattern in (
        # the site pages and install scripts (api/site.py)
        ({"GET"}, r"/|/install|/install\.sh|/install\.ps1|/install-windows\.ps1|/privacy|/support|/download|/tester"),
        ({"POST"}, r"/tester"),
        # app and server builds, and their feeds (downloads.py)
        ({"GET"}, r"/download/latest\.json|/download/notices\.json|/download/[^/]+|/download/[^/]+/[^/]+|/downloads/[^/]+"),
        ({"GET"}, r"/assets/.+"),
        ({"GET"}, r"/healthz"),
        # Kelo addresses (kelo_names.py), push relay (push.py), reports and beta requests (reports.py, beta.py)
        ({"GET", "POST"}, r"/broker/tunnel/(check|claim|release)"),
        ({"GET", "POST"}, r"/broker/push/(status|send|live)"),
        ({"POST"}, r"/broker/reports|/broker/beta|/broker/beta/status"),
        # the sign-in broker (google_web.py, github_web.py) and the shared return page (connectors / mail_calendar)
        ({"GET", "POST"}, r"/broker/google/(client|exchange|refresh)|/broker/github/(client|exchange)"),
        ({"GET"}, r"/oauth/relay|/oauth/callback"),
        # signed in: the access key or, for the operator's lists, the operator link key (api/auth.py require_operator)
        ({"GET"}, r"/api/about"),
        ({"GET", "POST", "PUT", "DELETE"}, r"/api/operator/.+"),
        # the access key only, from the server machine (curl): the operator's OAuth apps and the tester-mail Gmail
        ({"GET", "POST", "PUT", "DELETE"}, r"/api/oauth-apps(/.*)?|/api/mail/accounts.*|/api/mail/providers|/api/google/web/start"),
    )
)


class SiteRoutes:
    """Which method and path a site copy serves."""

    def allowed(self, method: str, path: str) -> bool:
        m = "GET" if method.upper() == "HEAD" else method.upper()
        return any(m in methods and pattern.fullmatch(path) for methods, pattern in _ROUTES)


REFUSED_CODE = "site_only"
REFUSED_MESSAGE = "This copy of Kelo only serves the kelo.chat site and its sign-in and notification services; this route is not served here."


class SiteOnlyGate:
    """Pure ASGI middleware: passes the allowed requests on, answers the others (see the module docstring)."""

    def __init__(self, app: Any, routes: SiteRoutes) -> None:
        self.app = app
        self.routes = routes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await receive()                                   # websocket.connect
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 4404, "reason": REFUSED_CODE})
            return
        if self.routes.allowed(scope.get("method", "GET"), scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": REFUSED_MESSAGE, "code": REFUSED_CODE}).encode()
        await send({"type": "http.response.start", "status": 404,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
