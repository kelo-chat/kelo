"""Tools for services the user connects themselves (REST APIs and MCP servers).

The agent describes the connection; the user types the key into the app; the server sends the requests and puts the key in.
The model never sees the key, and no code written by the model runs here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from ..agent import Ours, Tool
from ..agent.fence import data
from ..credentials import (
    SECRET_MARK,
    apply_secret,
    clean_caller_headers,
    clean_endpoint,
    clean_hosts,
    clean_name,
    placement_from_auth,
    redact,
)
from ..home import Home
from ..i18n import t
from ..mcp_oauth import open_token
from ..netguard import NetGuardError, Reply, fetch
from ..secret_requests import ask
from ..store import Store
from ..vault import VaultError, record_http, use_credential

OUT_CAP = 12_000
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
Fetcher = Callable[..., Awaitable[Reply]]


class McpError(Exception):
    pass


def _text(body: bytes, secret: str | None, cap: int = OUT_CAP) -> str:
    text = body.decode("utf-8", "replace")
    if secret:
        text = redact(text, secret)
    return text if len(text) <= cap else text[:cap] + f"\n[cut at {cap} characters of {len(text)}]"


def _jsonrpc(reply: Reply, request_id: int) -> dict[str, Any]:
    ctype = reply.headers.get("content-type", "").split(";")[0].strip().lower()
    candidates: list[bytes] = []
    if ctype == "application/json":
        candidates = [reply.body]
    elif ctype == "text/event-stream":
        data: list[bytes] = []
        for line in reply.body.split(b"\n") + [b""]:
            line = line.rstrip(b"\r")
            if line.startswith(b"data:"):
                data.append(line[5:].lstrip(b" "))
            elif not line and data:
                candidates.append(b"\n".join(data))
                data = []
    else:
        raise McpError("the MCP server returned an unsupported content type")
    for raw in candidates:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or msg.get("id") != request_id:
            continue
        if "error" in msg:
            raise McpError("the MCP server returned an error; the operation was not repeated")
        if not isinstance(msg.get("result"), dict):
            raise McpError("the MCP server returned no result")
        return msg["result"]
    raise McpError("the MCP server did not return the matching result")


async def mcp_session_call(fetcher: Fetcher, cred: dict[str, Any], method: str, params: dict[str, Any]) -> dict[str, Any]:
    """initialize -> notifications/initialized -> one request, each over its own POST, as the MCP streamable-HTTP transport does."""
    endpoint, placement, secret = cred["endpoint"], cred["placement"], cred["secret"]
    session = ""
    protocol = PROTOCOLS[0]
    counter = 0

    async def post(name: str, payload: dict[str, Any], *, notification: bool = False) -> dict[str, Any] | None:
        nonlocal session, protocol, counter
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": name, "params": payload}
        if not notification:
            counter += 1
            message["id"] = counter
        url, auth = apply_secret(endpoint, placement, secret)
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": protocol, **auth}
        if session:
            headers["Mcp-Session-Id"] = session
        reply = await fetcher("POST", url, allowed_hosts=cred["hosts"], headers=headers, content=json.dumps(message).encode())
        if sid := reply.headers.get("mcp-session-id"):
            if not all(33 <= ord(c) <= 126 for c in sid):
                raise McpError("the MCP server sent an invalid session id")
            session = sid
        if notification:
            if reply.status not in (200, 202, 204):
                raise McpError(f"the MCP server refused the initialization notice (HTTP {reply.status})")
            return None
        if reply.status >= 400:
            raise McpError(f"the MCP server answered HTTP {reply.status}; the operation was not repeated")
        return _jsonrpc(reply, counter)

    init = await post("initialize", {"protocolVersion": protocol, "capabilities": {}, "clientInfo": {"name": "assistant", "version": "1"}})
    agreed = (init or {}).get("protocolVersion")
    if agreed not in PROTOCOLS or not isinstance((init or {}).get("capabilities", {}).get("tools"), dict):
        raise McpError("the MCP server does not offer tools over a supported protocol version")
    protocol = agreed
    await post("notifications/initialized", {}, notification=True)
    result = await post(method, params)
    return result or {}


def connect_tools(store: Store, home: Home | None = None, fetcher: Fetcher = fetch) -> list[Tool]:
    async def ready(name: str, tool: str, detail: str = "") -> dict[str, Any]:
        """The service key for one use (the vault refuses when it is locked or empty, and logs the use). A connection made with OAuth
        gives its access token, renewed first when it has run out."""
        return await open_token(store, use_credential(store, clean_name(name), tool=tool, kind="api", detail=detail), fetcher)

    def registration(args: dict[str, Any]) -> tuple[str, list[str], dict[str, Any], str, str]:
        """(name, hosts, placement, MCP endpoint, check URL) from the call; raises NetGuardError when something is not allowed."""
        name = clean_name(str(args.get("name", "")))
        hosts = clean_hosts(args.get("hosts"))
        placement = placement_from_auth(args.get("auth"))
        endpoint = clean_endpoint(args.get("mcp_endpoint"), hosts)
        check = str(args.get("check_url") or "").strip()
        if check:
            parts = urlsplit(check)
            if parts.scheme != "https" or (parts.hostname or "").lower() not in hosts or parts.username or parts.password:
                raise NetGuardError("check_url must be an https:// GET on one of the hosts")
            apply_secret(check, placement, "x" * 16)   # {secret} where the placement needs it, and only there
        return name, hosts, placement, endpoint, check

    def moves_stored_key(args: dict[str, Any]) -> bool:
        """The call would send a key that is already stored somewhere new (other hosts or another place in the request): that drops
        the stored key, and the user is asked first (the only case add_service_key shows an Allow/Deny card)."""
        try:
            name, hosts, placement, _, _ = registration(args)
        except NetGuardError:
            return False
        old = store.credential(name)
        return bool(old and old["kind"] == "api" and old["secret"] and (sorted(old["hosts"]) != sorted(hosts) or old["placement"] != placement))

    async def describe_move(args: dict[str, Any]) -> str:
        name, hosts, _, _, _ = registration(args)
        old = store.credential(name) or {}
        return t("A key for {label} is already saved for {old}. Sending it to {new} instead deletes the saved key; you then enter the "
                 "key again in the chat.", label=old.get("label") or name, old=", ".join(old.get("hosts") or []), new=", ".join(hosts))

    async def add_service_key(args: dict[str, Any]) -> str:
        try:
            name, hosts, placement, endpoint, check = registration(args)
        except NetGuardError as e:
            return f"error: {e}"
        label = str(args.get("label") or name).strip()[:80] or name
        reason = str(args.get("reason") or "").strip()[:300]
        old = store.credential(name)
        try:
            store.save_credential(name, label, hosts, placement, endpoint)
        except VaultError as e:
            return f"error: {e}"
        store.add_vault_log(name, "requested", f"service key for {', '.join(hosts)}")
        if old and old["secret"] and store.credential(name)["secret"]:
            return f"{name} is already connected with these hosts and placement. Use it directly."
        use = (f"connector_get / connector_send{' or mcp_list_tools / mcp_call_tool' if endpoint else ''} with name={name!r}. Keep what you "
               f"learn about this API (endpoints that worked, limits) in notes/services/{name}.md with write_file.")
        req, state = await ask("key", name=name, label=label, hosts=hosts, reason=reason, check_url=check)
        if state == "saved":
            checked = ((req or {}).get("result") or {}).get("checked")
            how = (" The service accepted it on a test request." if checked == "ok" else
                   " It was not tested yet: if the first call is refused, tell the user." if check else "")
            return (f"The user saved the key for {name!r} in the fill-in card (it went straight to the keychain; you never see it).{how} "
                    f"Go on with what you were doing now, without waiting for a message: use {use}")
        if state == "cancelled":
            return ("The user closed the fill-in card without saving a key. Do not ask for it again unless they bring it up; say in one "
                    "short line what this means for the task, if anything.")
        if state in ("replaced", "expired"):
            return "This entry was replaced by a newer one or ran out; nothing was saved."
        return (f"Registered {name!r} for {', '.join(hosts)}. A fill-in card for the key is now shown in the chat, right below your "
                "message. Tell the user in one short sentence to put the key in the entry below; never in chat, and do not send them "
                "to another page. Once they save it you are told and go on by yourself (do not wait for a message from them). "
                f"Then use {use}")

    async def send(method: str, args: dict[str, Any]) -> str:
        try:
            where = urlsplit(str(args.get("url", "")))        # host and path only: a query string may carry something private
            cred = await ready(str(args.get("name", "")), "connector_get" if method == "GET" else "connector_send", f"{method} {where.hostname or ''}{where.path}")
            url, auth = apply_secret(str(args.get("url", "")), cred["placement"], cred["secret"])
            headers = {**clean_caller_headers(args.get("headers"), cred["placement"]), **cred["placement"].get("fixed_headers", {}), **auth}
            body = args.get("json")
            content = None
            if method != "GET" and body is not None:
                content = json.dumps(body, ensure_ascii=False).encode()
                headers.setdefault("Content-Type", "application/json")
            reply = await fetcher(method, url, allowed_hosts=cred["hosts"], headers=headers, content=content)
        except NetGuardError as e:
            return Ours(f"error: {e}")
        ctype = reply.headers.get("content-type", "")
        hint = record_http(store, cred["name"], reply.status, "connector_get" if method == "GET" else "connector_send",
                           write=method not in ("GET", "HEAD"))
        # the service's answer is fenced as external data; the status line, the cut note and the hint are ours
        return (f"HTTP {reply.status}{f' · {ctype}' if ctype else ''}\n" + data(_text(reply.body, cred['secret']))
                + ("\n[response was cut at the size limit]" if reply.truncated else "") + hint)

    async def connector_get(args: dict[str, Any]) -> str:
        return await send("GET", args)

    async def connector_send(args: dict[str, Any]) -> str:
        method = str(args.get("method", "POST")).upper()
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            return Ours("error: method must be POST, PUT, PATCH or DELETE (use connector_get to read)")
        return await send(method, args)

    async def mcp(args: dict[str, Any], method: str, params: dict[str, Any]) -> str:
        try:
            cred = await ready(str(args.get("name", "")), "mcp_call_tool" if method == "tools/call" else "mcp_list_tools",
                         method if method == "tools/list" else f"{method} {params.get('name', '')}")
            if not cred["endpoint"]:
                raise NetGuardError(f"{cred['name']!r} has no MCP endpoint; it is a REST connection (use connector_get / connector_send)")
            result = await mcp_session_call(fetcher, cred, method, params)
        except (NetGuardError, McpError) as e:
            code = re.search(r"HTTP (\d{3})", str(e))
            hint = record_http(store, cred["name"], int(code.group(1)), "mcp") if code else ""
            return "error: " + data(e) + hint   # the server's error text may quote its own words: fenced
        store.set_credential_result(cred["name"], "ok", "MCP answered")
        out = _text(json.dumps(result, ensure_ascii=False).encode(), cred["secret"])
        return ("(the tool reported an error)\n" if result.get("isError") else "") + data(out)

    async def mcp_list_tools(args: dict[str, Any]) -> str:
        cursor = args.get("cursor")
        return await mcp(args, "tools/list", {"cursor": str(cursor)} if cursor else {})

    async def mcp_call_tool(args: dict[str, Any]) -> str:
        tool, arguments = str(args.get("tool", "")), args.get("arguments") or {}
        if not tool or not isinstance(arguments, dict):
            return Ours("error: tool (name) and arguments (object) are required; inspect the schema with mcp_list_tools first")
        return await mcp(args, "tools/call", {"name": tool, "arguments": arguments})

    name_prop = {"type": "string", "description": "the connection name"}
    url_prop = {"type": "string", "description": f"on a registered host; write {SECRET_MARK} where a credential goes in the path"}
    return [
        Tool("add_service_key",
             "Connect a service that works with an API key or token. You describe where the key goes; a fill-in card for the key "
             "then appears in the chat right below your message, and the user pastes the key there (never in chat; you never see it). "
             "Before the call, say in one short sentence that the entry is coming below. Saving it is their consent; once saved you "
             "go on by yourself.",
             {"type": "object", "properties": {
                 "name": {"type": "string", "description": "short lower-case slug"}, "label": {"type": "string",
                                                                                       "description": "what the key is for, as the user calls it"},
                 "reason": {"type": "string", "description": "one short line: why you need it now"},
                 "hosts": {"type": "array", "items": {"type": "string"}, "description": "exact API host names the key may be sent to"},
                 "auth": {"type": "object", "description": "OpenAPI security scheme: http + bearer, or apiKey + in + name",
                          "properties": {"type": {"type": "string", "enum": ["http", "apiKey"]},
                                         "scheme": {"type": "string", "enum": ["bearer"]},
                                         "in": {"type": "string", "enum": ["header", "query", "path"]},
                                         "name": {"type": "string", "description": "header or parameter name"},
                                         "prefix": {"type": "string", "description": "text before the key"}},
                          "required": ["type"]},
                 "check_url": {"type": "string", "description": "optional: a read-only https GET on one of the hosts that answers 2xx only "
                               f"with a valid key (a 'who am I' / 'me' endpoint from the API docs; {SECRET_MARK} in the path when the key "
                               "goes there). The key is tried with it before it is saved."},
                 "mcp_endpoint": {"type": "string", "description": "https URL of a hosted MCP server, when that is what you connect"}},
              "required": ["name", "hosts", "auth"]},
             # no Allow/Deny card: typing the key into the entry (which shows the hosts) is the consent. Only a call that would move
             # a key that is already stored to other hosts asks first.
             add_service_key, requires_approval=True, auto_approve=lambda a: not moves_stored_key(a), describe=describe_move,
             declined="The user kept the saved key where it is; nothing was changed. Do not ask again unless they bring it up."),
        Tool("connector_get", "Read from a connected service (GET; the server adds the key).",
             {"type": "object", "properties": {"name": name_prop, "url": url_prop,
                                                           "headers": {"type": "object"}}, "required": ["name", "url"]},
             connector_get, untrusted=True, source="connector", read_only=True),
        Tool("connector_send", "Change something in a connected service (the server adds the key). The user approves each call.",
             {"type": "object", "properties": {"name": name_prop, "method": {"type": "string", "enum": ["POST", "PUT", "PATCH", "DELETE"]},
                                               "url": url_prop, "json": {"description": "request body"}, "headers": {"type": "object"}},
              "required": ["name", "url"]}, connector_send, requires_approval=True, untrusted=True, source="connector"),
        Tool("mcp_list_tools", "List the tools of a connected MCP server with their input schemas.",
             {"type": "object", "properties": {"name": name_prop, "cursor": {"type": "string"}}, "required": ["name"]},
             mcp_list_tools, untrusted=True, source="mcp", read_only=True),
        Tool("mcp_call_tool", "Call one tool of a connected MCP server. The user approves each call.",
             {"type": "object", "properties": {"name": name_prop, "tool": {"type": "string"}, "arguments": {"type": "object"}},
              "required": ["name", "tool"]}, mcp_call_tool, requires_approval=True, untrusted=True, source="mcp"),
    ]
