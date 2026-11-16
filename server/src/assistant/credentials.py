"""Rules for connection credentials: what may be registered, and how a stored secret is placed into one request.

The agent registers a *request* (name, hosts, where the secret goes). Only the user can supply the secret, in the app.
The secret is applied here, inside the server, to a request the server itself sends to a registered host; it is never
returned to the model, and it is removed from anything an upstream service echoes back.
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote, urlsplit

from .netguard import NetGuardError, valid_host

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")
HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
PARAM = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-\[\]]{0,63}$")
KINDS = ("bearer_header", "custom_header", "query_param", "url_path_segment")
FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "cookie", "set-cookie", "proxy-authorization", "authorization"}
CALLER_HEADERS = {"accept", "accept-language", "content-type", "if-match", "if-none-match", "idempotency-key", "user-agent"}
SECRET_MARK = "{secret}"
MIN_SECRET = 8


def clean_name(name: str) -> str:
    n = (name or "").strip().lower()
    if not NAME.match(n):
        raise NetGuardError("name must be 3-64 characters: lower-case letters, digits, '.', '-' or '_' (example: acme)")
    return n


def placement_from_auth(auth: Any) -> dict[str, str]:
    """An OpenAPI-style security scheme -> where the secret goes in each request (the stored placement):
    {"type": "http", "scheme": "bearer"} -> Authorization: Bearer <key>
    {"type": "apiKey", "in": "header", "name": "X-Api-Key", "prefix"?: "Token "} -> that header
    {"type": "apiKey", "in": "query", "name": "api_key"} -> a query parameter
    {"type": "apiKey", "in": "path"} -> the {secret} mark in the URL path"""
    if not isinstance(auth, dict):
        raise NetGuardError('auth must be an object: {"type": "http", "scheme": "bearer"} or {"type": "apiKey", "in": "header"|"query"|"path", "name": ...}')
    kind = str(auth.get("type", "")).strip()
    if kind == "http":
        if str(auth.get("scheme", "bearer")).lower() != "bearer":
            raise NetGuardError("only the bearer scheme of type http is supported")
        return clean_placement({"type": "bearer_header"})
    if kind == "apiKey":
        where, name = str(auth.get("in", "")).strip(), str(auth.get("name", "")).strip()
        if where == "header":
            if name.lower() == "authorization":
                prefix = str(auth.get("prefix", "")).strip().lower()
                if prefix in ("bearer", ""):
                    return clean_placement({"type": "bearer_header"})
                raise NetGuardError("an Authorization header other than Bearer is not supported")
            return clean_placement({"type": "custom_header", "header": name, "prefix": str(auth.get("prefix", ""))})
        if where == "query":
            return clean_placement({"type": "query_param", "param": name})
        if where == "path":
            return clean_placement({"type": "url_path_segment"})
        raise NetGuardError("auth.in must be header, query or path")
    raise NetGuardError("auth.type must be http or apiKey")


def clean_placement(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        raw = {"type": raw}
    if not isinstance(raw, dict) or raw.get("type") not in KINDS:
        raise NetGuardError(f"placement.type must be one of {', '.join(KINDS)}")
    kind = raw["type"]
    out = {"type": kind}
    if kind == "custom_header":
        h = str(raw.get("header", ""))
        if not HEADER.match(h) or h.lower() in FORBIDDEN_HEADERS:
            raise NetGuardError("placement.header must be a plain header name (example: X-API-Key)")
        out["header"] = h
        prefix = str(raw.get("prefix", ""))
        if len(prefix) > 32 or not prefix.isprintable():
            raise NetGuardError("placement.prefix is too long or not printable")
        out["prefix"] = prefix
    elif kind == "query_param":
        p = str(raw.get("param", ""))
        if not PARAM.match(p):
            raise NetGuardError("placement.param must be a plain parameter name (example: api_key)")
        out["param"] = p
    return out


def clean_hosts(hosts: Any) -> list[str]:
    if not isinstance(hosts, list) or not hosts or len(hosts) > 5:
        raise NetGuardError("hosts must be a list of 1-5 API host names")
    return sorted({valid_host(str(h)) for h in hosts})


def clean_endpoint(endpoint: Any, hosts: list[str]) -> str:
    if not endpoint:
        return ""
    parts = urlsplit(str(endpoint))
    if (parts.scheme != "https" or (parts.hostname or "") not in hosts or parts.port not in (None, 443)
            or parts.username or parts.query or parts.fragment):
        raise NetGuardError("the MCP endpoint must be an https:// URL on one of the registered hosts, without a query string")
    return str(endpoint)


def clean_secret(secret: str) -> str:
    s = (secret or "").strip()
    if len(s) < MIN_SECRET or not s.isprintable() or any(c.isspace() for c in s):
        raise ValueError(f"the key must be a single line of at least {MIN_SECRET} characters without spaces")
    return s


def clean_caller_headers(headers: Any, placement: dict[str, str]) -> dict[str, str]:
    if not headers:
        return {}
    if not isinstance(headers, dict):
        raise NetGuardError("headers must be an object")
    out = {}
    own = placement.get("header", "").lower()
    for k, v in headers.items():
        key = str(k).lower()
        if key not in CALLER_HEADERS or key == own:
            raise NetGuardError(f"header {k!r} cannot be set; allowed: {', '.join(sorted(CALLER_HEADERS))}")
        val = str(v)
        if not val.isprintable() or len(val) > 512:
            raise NetGuardError(f"header {k!r} has an invalid value")
        out[str(k)] = val
    return out


def apply_secret(url: str, placement: dict[str, str], secret: str) -> tuple[str, dict[str, str]]:
    """The URL and headers to send. `url_path_segment` credentials need `{secret}` in the URL path; the others must not."""
    kind = placement["type"]
    headers: dict[str, str] = {}
    if kind == "url_path_segment":
        parts = urlsplit(url)
        if SECRET_MARK not in parts.path or SECRET_MARK in parts.query:
            raise NetGuardError("this credential is part of the URL path: write {secret} where it belongs, e.g. https://host/bot{secret}/getMe")
        path = parts.path.replace(SECRET_MARK, quote(secret, safe=""))
        return parts._replace(path=path).geturl(), headers
    if SECRET_MARK in url:
        raise NetGuardError("{secret} is only for credentials that go in the URL path")
    if kind == "bearer_header":
        headers["Authorization"] = f"Bearer {secret}"
    elif kind == "custom_header":
        headers[placement["header"]] = f"{placement.get('prefix', '')}{secret}"
    elif kind == "basic_auth":   # self-hosted presets only (presets.py): the user name is not secret and sits in the placement
        headers["Authorization"] = "Basic " + base64.b64encode(f"{placement.get('user', '')}:{secret}".encode()).decode()
    elif kind == "none":         # an app on the network that needs no key (Frigate's open port)
        pass
    elif kind == "query_param":
        sep = "&" if urlsplit(url).query else "?"
        url = f"{url}{sep}{quote(placement['param'], safe='')}={quote(secret, safe='')}"
    return url, headers


def redact(text: str, secret: str) -> str:
    """Remove the secret (plain and URL-encoded forms) from text an upstream service sent back."""
    for form in {secret, quote(secret, safe=""), quote(secret)}:
        text = text.replace(form, "[redacted]")
    return text


def local_origin(cred: dict[str, Any]) -> str:
    """The owner-typed address of a connection to an app on their own network ("" for an ordinary internet service). It lives in the
    placement, which only the owner's form (keyconnect.py) can write: the agent's own registration builds a fresh placement."""
    return str((cred.get("placement") or {}).get("origin") or "")


def local_url(cred: dict[str, Any], url: str) -> str:
    """A path like /api/states becomes origin + base path + path; a full URL is left for fetch_local to check against the origin."""
    place = cred.get("placement") or {}
    if url.startswith("/") and not url.startswith("//"):
        return f"{place['origin']}{place.get('base', '')}{url}"
    return url
