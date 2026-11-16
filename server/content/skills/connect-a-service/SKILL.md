---
name: Connect a service
description: Connect the user's accounts and other APIs, and use them safely.
---

# Connect a service

## Pick the way in
1. If the service is in the app's Connectors list, call `connect_service(service=<id>)`. A card appears; the user signs in on the service's own page, types a code there, or enters an app password. You never see it.
2. An API that is not in the list but works with a key or token: call `add_service_key` (its fields say what to give: the only hosts the key may reach and how it is sent). A fill-in card for the key then appears in the chat below your message: tell the user to paste the key there (never in chat). Once they save it you go on by yourself; if the API has a 'who am I' endpoint, pass it as `check_url` so the key is tried before it is saved.
3. A website without an API: use the browser with a login from the keychain (`request_login`, then `browser_login`).

## Using it
- REST: `connector_get` to read, `connector_send` to change (the user approves each change). The server adds the key.
- MCP: `mcp_list_tools` first, then `mcp_call_tool` with arguments that match the schema.
- Mail: `email_list`, `email_read`, `email_send` (sending is approved by the user), `email_update` (read/unread, flag, archive, move; the trash is approved by the user).
- Read `notes/services/<name>.md` before the next use.

## Problems
- 401 or 403 after a correct request: the key is wrong, expired or lacks permission. Tell the user to check it in the app; do not keep trying.
- Rate limits and refusals mean stop and report, not another route.
- After a change that failed or timed out, read the current state before trying again: the first request may have worked.
