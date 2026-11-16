"""Tools for services the user connects themselves (REST APIs and MCP servers).

The agent describes the connection; the user types the key into the app; the server sends the requests and puts the key in.
The model never sees the key, and no code written by the model runs here.
"""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from ..agent import Ours, Tool
from ..agent.loop import outside_seen
from ..agent.fence import data
from ..agent import found_secrets
from ..cred_keyring import spawn_sync, via
from ..credentials import (
    SECRET_MARK,
    apply_secret,
    clean_caller_headers,
    clean_endpoint,
    clean_hosts,
    clean_name,
    clean_secret,
    local_origin,
    local_url,
    placement_from_auth,
    redact,
)
from .. import forbidden, local_login
from ..deliverables import Deliverables, LibraryFileError
from ..home import Home, HomeError
from ..i18n import t
from ..keyguard import Locked
from ..mcp_oauth import open_token
from ..netguard import NetGuardError, Reply, fetch, fetch_local
from ..secret_requests import ask
from ..store import Store
from ..workers import current_turn
from ..vault import VaultError, record_http, use_credential
from .downloads import blocked_name, looks_executable

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


async def mcp_session_call(fetcher: Fetcher, cred: dict[str, Any], method: str, params: dict[str, Any],
                           keyring: Any = None) -> dict[str, Any]:
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
        reply = await via(cred, fetcher, keyring)("POST", url, allowed_hosts=cred["hosts"], headers=headers, content=json.dumps(message).encode())
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


MAX_DOWNLOAD = 50 * 1024**2


def connect_tools(store: Store, home: Home | None = None, fetcher: Fetcher = fetch, local_fetcher: Fetcher = fetch_local,
                  keyring: Any = None) -> list[Tool]:
    library = Deliverables(home) if home is not None else None

    async def ready(name: str, tool: str, detail: str = "") -> dict[str, Any]:
        """The service key for one use (the vault refuses when it is locked or empty, and logs the use). A connection made with OAuth
        gives its access token, renewed first when it has run out; an app on the owner's network that takes a password gives a session
        token (`login_secret` is kept so a 401 can drop it)."""
        cred = use_credential(store, clean_name(name), tool=tool, kind="api", detail=detail)
        if local_login.is_login(cred.get("secret")):
            place = cred["placement"]
            try:
                token = await local_login.token_for(cred["secret"], place["origin"], place.get("base", ""), local_fetcher)
            except local_login.LoginError as e:
                store.set_credential_result(cred["name"], "rejected", "the app did not accept the sign-in")
                raise NetGuardError(f"{cred['label']} did not accept the stored sign-in ({e}). Ask the user to connect it again in the Connectors tab.") from None
            return {**cred, "secret": token, "login_secret": cred["secret"]}
        return await open_token(store, cred, fetcher)

    def carrier(cred: dict[str, Any], **extra: Any) -> Fetcher:
        """How this connection's requests go out: through fetch_local to the stored address for an app on the owner's network (only the
        owner's own form can store one), through the normal https-only fetch otherwise."""
        origin = local_origin(cred)

        def go(method: str, url: str, **kw: Any) -> Awaitable[Reply]:
            kw.pop("allowed_hosts", None)
            if not origin:
                return fetcher(method, url, allowed_hosts=cred["hosts"], **kw, **extra)
            return local_fetcher(method, url, origin=origin, **kw, **extra)
        return go

    def own_device(args: dict[str, Any]) -> bool:
        """The connection is the owner's own device app (Home Assistant): what it says is not outside text for the approval rule."""
        c = store.credential(str(args.get("name", "")).strip().lower())
        return bool(c and local_origin(c) and c["placement"].get("device"))

    def asked_directly(args: dict[str, Any]) -> bool:
        """No approval card for a change to the owner's own device app when the owner asked for it in their own chat message and
        nothing from outside (a page, mail, message, another app's data) has reached this run. A step Kelo takes by itself (a
        background job, a schedule, a watcher) or after outside text still asks; a task the owner set up runs under its own permission."""
        if not own_device(args):
            return False
        turn = current_turn.get() or {}
        seen = outside_seen.get()
        return turn.get("source") == "chat" and not turn.get("unattended") and not (seen and seen[0])

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

    def found_args(args: dict[str, Any]) -> dict[str, Any]:
        """The call with its hosts filled in: none named means the page the key came from."""
        found = found_secrets.current.get()
        item = found.get(str(args.get("ref", ""))) if found else None
        if args.get("hosts") or item is None or not found_secrets.host_of(item.source):
            return args
        return {**args, "hosts": [found_secrets.host_of(item.source)]}

    def outside_source(args: dict[str, Any]) -> list[str]:
        """The hosts of the call that are not under the registrable domain of the page the key came from."""
        found = found_secrets.current.get()
        item = found.get(str(args.get("ref", ""))) if found else None
        if item is None:
            return []
        try:
            hosts = registration(found_args(args))[1]
        except NetGuardError:
            return []
        home = found_secrets.registrable_domain(found_secrets.host_of(item.source))
        return [h for h in hosts if not home or found_secrets.registrable_domain(h) != home]

    def found_moves(args: dict[str, Any]) -> bool:
        a = found_args(args)
        return bool(outside_source(a)) or moves_stored_key(a)

    async def describe_found(args: dict[str, Any]) -> str:
        a = found_args(args)
        try:
            name, hosts, _, _, _ = registration(a)
        except NetGuardError:
            return ""
        far = outside_source(a)
        if far:
            return t("A key found on a page would be sent to {new}, which is not on the same site as the page. It is saved in the keychain; "
                     "you never see it.", new=", ".join(far))
        old = store.credential(name) or {}
        return t("A key for {label} is already saved for {old}. Sending it to {new} instead deletes the saved key; you then enter the "
                 "key again in the chat.", label=old.get("label") or name, old=", ".join(old.get("hosts") or []), new=", ".join(hosts))

    async def save_found_secret(args: dict[str, Any]) -> str:
        found = found_secrets.current.get()
        ref = str(args.get("ref", "")).strip()
        item = found.get(ref) if found else None
        if item is None:
            return Ours(f"error: no hidden secret {ref or '(no ref)'} in this conversation turn (it may have expired, one hour at most); "
                        "nothing was saved. Read the page again, or ask the user to add the key with add_service_key.")
        if item.kind not in found_secrets.SAVEABLE:
            return Ours(f"error: {ref} is a {item.kind}; it cannot be saved as a service key. Nothing was saved.")
        args = found_args(args)
        try:
            name, hosts, placement, endpoint, _ = registration(args)
            secret = clean_secret(item.value)
        except (NetGuardError, ValueError) as e:
            return Ours(f"error: {e}")
        if store.vault_locked():
            return Ours("error: the user has locked the keychain; nothing was saved. Tell them they can unlock it in the app.")
        label = str(args.get("label") or name).strip()[:80] or name
        old = store.credential(name)
        try:
            store.save_credential(name, label, hosts, placement, endpoint)
            if not store.set_credential_secret(name, secret):
                return Ours("error: the key could not be saved.")
        except (VaultError, Locked) as e:
            return Ours(f"error: {e}" if isinstance(e, VaultError) else "error: the keychain is locked until a phone unlocks it; nothing was saved.")
        store.add_vault_log(name, "replaced" if old and old["secret"] else "saved", f"service key found on a page, for {', '.join(hosts)}")
        spawn_sync(store, keyring)
        found.forget(ref)
        # the apps turn this line into a "Saved in the Keychain" card (KeySavedRules in shared chat/KeySaved.kt and iOS): keep its start
        return Ours(f"Saved the key for {name!r} in the keychain (hosts: {', '.join(hosts)}). You never see it. Use connector_get / connector_send "
                    f"with name={name!r}; keep what you learn about this API in notes/services/{name}.md with write_file.")

    async def request(cred: dict[str, Any], method: str, url: str, caller_headers: Any, content: bytes | None, *, name: str, tool: str,
                      max_body: int | None = None) -> Reply:
        """One request with the key put in. A 401 from an app we signed in to drops the session token and tries once more (a refused
        request changed nothing, so repeating it is safe)."""
        for attempt in (0, 1):
            full = local_url(cred, url) if local_origin(cred) else url
            full, auth = apply_secret(full, cred["placement"], cred["secret"])
            headers = {**clean_caller_headers(caller_headers, cred["placement"]), **cred["placement"].get("fixed_headers", {}), **auth}
            if content is not None:
                headers.setdefault("Content-Type", "application/json")
            extra = {"max_body": max_body} if max_body else {}
            reply = await via(cred, carrier(cred, **extra), keyring)(method, full, headers=headers, content=content)
            if reply.status == 401 and cred.get("login_secret") and attempt == 0:
                local_login.forget(cred["login_secret"])
                cred = await ready(name, tool, "sign in again")
                continue
            return reply
        return reply

    def never(method: str, args: dict[str, Any]) -> str:
        """The refusal for a call Kelo never makes on this connector (forbidden.py), "" when the call may go on."""
        preset = forbidden.preset_of(str(args.get("name", "")))
        return forbidden.refusal(preset, method, str(args.get("url", "")), args.get("json")) if preset else ""

    async def send(method: str, args: dict[str, Any]) -> str:
        tool = "connector_get" if method == "GET" else "connector_send"
        if refused := never(method, args):
            return refused
        try:
            where = urlsplit(str(args.get("url", "")))        # host and path only: a query string may carry something private
            cred = await ready(str(args.get("name", "")), "connector_get" if method == "GET" else "connector_send", f"{method} {where.hostname or ''}{where.path}")
            body = args.get("json")
            content = None
            if method != "GET" and body is not None:
                content = json.dumps(body, ensure_ascii=False).encode()
            reply = await request(cred, method, str(args.get("url", "")), args.get("headers"), content, name=str(args.get("name", "")), tool=tool)
        except NetGuardError as e:
            return Ours(f"error: {e}")
        ctype = reply.headers.get("content-type", "")
        hint = record_http(store, cred["name"], reply.status, "connector_get" if method == "GET" else "connector_send",
                           write=method not in ("GET", "HEAD"))
        # the service's answer is fenced as external data; the status line, the cut note and the hint are ours
        return (f"HTTP {reply.status}{f' · {ctype}' if ctype else ''}\n" + data(_text(reply.body, cred['secret']))
                + ("\n[response was cut at the size limit]" if reply.truncated else "") + hint)

    async def connector_download(args: dict[str, Any]) -> str:
        if refused := never("GET", args):
            return refused
        if library is None:
            return Ours("error: this server has no library to save into")
        try:
            where = urlsplit(str(args.get("url", "")))
            cred = await ready(str(args.get("name", "")), "connector_download", f"GET {where.hostname or ''}{where.path}")
            reply = await request(cred, "GET", str(args.get("url", "")), args.get("headers"), None, name=str(args.get("name", "")),
                                  tool="connector_download", max_body=MAX_DOWNLOAD)
            hint = record_http(store, cred["name"], reply.status, "connector_download")
            if reply.status != 200:
                return Ours(f"error: the service answered HTTP {reply.status}; nothing was saved.{hint}")
            if reply.truncated:
                return Ours(f"error: the file is larger than {MAX_DOWNLOAD // 1024**2} MB; nothing was saved.")
            ctype = reply.headers.get("content-type", "").split(";")[0].strip().lower()
            name = str(args.get("save_as") or "").strip() or Path(urlsplit(str(args.get("url", ""))).path).name or "download"
            if "." not in name and (ext := mimetypes.guess_extension(ctype or "")):
                name += ext
            if blocked_name(name) or looks_executable(reply.body[:16]):
                return Ours("error: programs and scripts are not kept in the library; nothing was saved.")
            saved = library.save(name, reply.body, origin=cred["name"])   # the connector it came from (connector_data.py)
        except (NetGuardError, VaultError, LibraryFileError, HomeError) as e:
            return Ours(f"error: {e}")
        return Ours(f"Saved to the user's library as files/{saved} ({len(reply.body)} bytes{f', {ctype}' if ctype else ''}). Show or share it "
                    "with the library's file tools; do not paste its content.")

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
            result = await mcp_session_call(carrier(cred), cred, method, params, keyring=keyring)
        except (NetGuardError, McpError) as e:
            code = re.search(r"HTTP (\d{3})", str(e))
            hint = record_http(store, cred["name"], int(code.group(1)), "mcp") if code else ""
            return "error: " + data(e) + hint   # the server's error text may quote its own words: fenced
        store.set_credential_result(cred["name"], "ok", "MCP answered")
        if method == "tools/list":   # the server's own words on each tool (read-only, destructive) decide what a call of it counts as
            from .. import effects
            effects.remember_mcp_tools(cred["name"], result.get("tools"))
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
             "Saving it is their consent; once saved you go on by yourself.",
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
        Tool("save_found_secret",
             "Save a key you came across (shown to you as [secret #N: ..., hidden]) in the keychain: you never see its value. Give the "
             "placement like add_service_key. By default the key may go only to the site it was found on and API hosts under the same "
             "registrable domain; other hosts ask the user first. Never ask the user to copy a hidden key.",
             {"type": "object", "properties": {
                 "ref": {"type": "string", "description": 'the number in the placeholder, e.g. "#1"'},
                 "name": {"type": "string", "description": "short lower-case slug"},
                 "label": {"type": "string", "description": "what the key is for, as the user calls it"},
                 "hosts": {"type": "array", "items": {"type": "string"},
                           "description": "exact API host names the key may be sent to; default: the host of the page it was found on"},
                 "auth": {"type": "object", "description": "OpenAPI security scheme: http + bearer, or apiKey + in + name",
                          "properties": {"type": {"type": "string", "enum": ["http", "apiKey"]},
                                         "scheme": {"type": "string", "enum": ["bearer"]},
                                         "in": {"type": "string", "enum": ["header", "query", "path"]},
                                         "name": {"type": "string", "description": "header or parameter name"},
                                         "prefix": {"type": "string", "description": "text before the key"}},
                          "required": ["type"]}},
              "required": ["ref", "name", "auth"]},
             save_found_secret, requires_approval=True, auto_approve=lambda a: not found_moves(a), describe=describe_found,
             declined="The user did not allow sending the key to those hosts; nothing was saved. Do not ask again unless they bring it up."),
        Tool("connector_get", "Read from a connected service (GET; the server adds the key). For an app on the user's own network "
             "give url as a path such as /api/states.",
             {"type": "object", "properties": {"name": name_prop, "url": url_prop,
                                                           "headers": {"type": "object"}}, "required": ["name", "url"]},
             connector_get, untrusted=True, source="connector", read_only=True, own_data=own_device),
        Tool("connector_download", "Save a picture or file from a connected service (GET) into the user's library, for example a camera "
             "snapshot, a photo or a PDF. Then show or share it with the library's file tools.",
             {"type": "object", "properties": {"name": name_prop, "url": url_prop, "save_as": {"type": "string", "description": "file name"},
                                               "headers": {"type": "object"}}, "required": ["name", "url"]},
             connector_download, source="connector"),
        Tool("connector_send", "Change something in a connected service (the server adds the key). The user approves each call, "
             "except for a change to their own smart-home app (Home Assistant) that they asked for in their own message: that goes ahead "
             "at once (switching, locking, heating). A change nobody asked for, or one prompted by text from a page, mail or message, is "
             "asked first.",
             {"type": "object", "properties": {"name": name_prop, "method": {"type": "string", "enum": ["POST", "PUT", "PATCH", "DELETE"]},
                                               "url": url_prop, "json": {"description": "request body"}, "headers": {"type": "object"}},
              "required": ["name", "url"]}, connector_send, requires_approval=True, untrusted=True, source="connector",
             auto_approve=asked_directly),
        Tool("mcp_list_tools", "List the tools of a connected MCP server with their input schemas.",
             {"type": "object", "properties": {"name": name_prop, "cursor": {"type": "string"}}, "required": ["name"]},
             mcp_list_tools, untrusted=True, source="mcp", read_only=True, own_data=own_device),
        Tool("mcp_call_tool", "Call one tool of a connected MCP server. The user approves each call (not for the user's own smart-home "
             "app when they asked for it in their own message).",
             {"type": "object", "properties": {"name": name_prop, "tool": {"type": "string"}, "arguments": {"type": "object"}},
              "required": ["name", "tool"]}, mcp_call_tool, requires_approval=True, untrusted=True, source="mcp", auto_approve=asked_directly),
    ]
