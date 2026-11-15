# The key container ("keyring"): AI-provider keys live outside the Kelo server (backlog 61)

Status: design v1 (2026-11-14). Phase A = the container and the AI providers' chat keys. Phase B = optional admin / billing keys for
balances. Phase C = connector API keys (vault entries with header / query placement).

## Why

Today the Kelo server keeps each AI provider's key it was given in the app, sealed in its database (`api_keys`, Fernet under the
master key; under Keyguard the phone holds that key). The server process opens the key to make a request. If the server process
were ever taken over (a bug in a page parser, a malicious connector answer that reaches code execution), the keys could be read out
and used anywhere.

With the keyring, the server never holds a provider key after the moment it is saved:
- a **separate container** stores the keys;
- the server sends model requests to the keyring **without** the key;
- the keyring adds the key only for that key's own provider host(s), and for admin keys only on read-only billing paths.
A server that is taken over can still make model calls through the keyring (as it can today). It cannot read a key out or send it
to another host.

## Shape

- Container `keyring`:
  - ROLE=keyring, `python -m assistant.keyring_service`, port 9500;
  - networks: `keyring-net` (internal, only `server` and `keyring`) and `keyring-out` (its way to the internet and the LAN for a
    custom endpoint);
  - volume `keyring-state` (`/state`), which only the keyring mounts;
  - no env_file. The only setting is `KEYRING_TOKEN`, passed as `environment: KEYRING_TOKEN: ${KEYRING_TOKEN}`. The server gets the
    same value. `assistant` (the CLI) creates it in .env on install / update, as it does for other generated secrets.
- Every call from the server carries `x-keyring-token: <KEYRING_TOKEN>`. The keyring compares it in constant time. Without it the
  answer is 401.
- Storage: `/state/keyring.db` (SQLite).
  - `entries(name TEXT PRIMARY KEY, kind TEXT, sealed BLOB, hosts TEXT /* JSON list */, local INTEGER, paths TEXT /* JSON list, admin only */, hint TEXT, created_at REAL)`;
  - `meta(key TEXT PRIMARY KEY, value TEXT)`.
  - Each secret is AES-256-GCM sealed under the ring key: `nonce12 ‖ ct`, aad = the entry name.
- The ring key is `HKDF-SHA256(the server's personal-data key, salt = b"", info = b"kelo-keyring v1", 32 bytes)`.
  - The personal-data key is the key `Store.keyguard.fernet()` is built from: the master key, or the data key once Keyguard is on.
  - The keyring never stores it: it starts LOCKED and answers 423 `{"code": "locked"}` until the server sends it.
  - The server sends it at start-up when its own key is available, and right after a Keyguard unlock. When the keyring answers 423
    (it restarted), the server sends it again and retries the call once.
  - `meta.check` = HMAC-SHA256(ring key, "kelo-keyring check") hex. A wrong key → 409 `{"code": "key_mismatch"}`.
  - So: the keyring's disk alone opens nothing; under Keyguard nothing opens until a phone unlocks the server; the server's disk
    alone does not hold the keys any more.
- Keyguard rotation (a new personal-data key): the server calls `POST /rekey {"old", "new"}` and the keyring re-seals every entry in
  one transaction.

## Keyring API (all JSON, all need x-keyring-token)

```
POST   /unlock          {"key": b64(32)}                 -> {"ok": true}; 409 key_mismatch
POST   /rekey           {"old": b64(32), "new": b64(32)} -> {"ok": true, "entries": n}; 409 key_mismatch
GET    /entries                                          -> {"entries": [{"name", "kind", "hosts", "local", "hint", "slot"?}]}   never a secret
PUT    /entries/{name}  {"secret", "kind": "chat"|"admin"|"connector", "hosts": ["api.openai.com"], "local": false, "paths": [], "hint": "…abcd", "slot"?: {"header": "x-api-key"}|{"param": "api_key"}}
                                                          -> {"ok": true}; 422 bad name / hosts / kind / slot; 423 locked
DELETE /entries/{name}                                   -> {"ok": true, "removed": bool}
GET    /healthz                                          -> {"ok": true, "locked": bool}   (no token needed; says nothing else)
ANY    /fwd/{name}/{path...}?{query}                     the proxy, below
```
`name`: `[a-z0-9][a-z0-9._:-]{0,63}`. Chat keys are named after the provider (`openai`, `custom-home-ollama`, …), admin keys
`admin:<provider>`, connector keys `cred:<name>`.

### Connector scope & slot
- Moved to keyring: keys of kind "api" for internet services that go in a header (`bearer_header`, `custom_header`) or a query parameter (`query_param`).
- Stays in the server's vault as today: Basic auth, minted tokens (Google service account, Zoom), sign-in bundles (JSON secrets: mail, calendars, MCP OAuth), local-login bundles, anything on the owner's own network (`local_origin`), keys in the URL path (Telegram), the messenger channels, the weather keys and the VPN.
- Kind `"connector"` requires `"slot"`: exactly one of `{"header": "<lowercase header name>"}` (matches `^[a-z][a-z0-9-]{0,63}$`) or `{"param": "<query name>"}` (matches `^[A-Za-z_][A-Za-z0-9_.\-\[\]]{0,63}$`). Slot is allowed only for connector entries.

### The proxy

The server sends the provider or connector request to `http://keyring:9500/fwd/<name>/<path>` with the header
`x-keyring-upstream: https://api.openai.com/v1` (the service's real base URL). The keyring:
- checks the token, that it is unlocked (423), and that the entry exists (404 `{"code": "no_key"}`);
- parses the upstream: the scheme must be `https`, or `http` only when the entry is `local`. The host (lowercase, no userinfo) must be
  in the entry's `hosts`. Otherwise 403 `{"code": "host_not_allowed"}`;
- for `kind == "admin"`: only GET, and the path after the upstream's own path must start with one of the entry's `paths`. Otherwise
  403 `{"code": "not_allowed"}`;
- resolves the host and refuses private, loopback, link-local, CGNAT and metadata addresses, unless the entry is `local` (a custom
  endpoint on the owner's network). It connects to the checked address. Copy the server's netguard rules; the module is in the same
  image;
- builds the URL `upstream + "/" + path + ("?" + query if query)`;
- copies the request headers except hop-by-hop headers, `host`, `content-length` and `x-keyring-*`;
- replaces the literal `KELO_KEYRING_SECRET`:
  - for chat/admin entries: wherever it appears in a recognized auth header (`KEY_HEADERS`) or in query as `?key=KELO_KEYRING_SECRET`.
  - for connector entries: ONLY in its declared slot: the header named `slot.header` (any other header, including `KEY_HEADERS` → 400 `bad_placement`), or the one query part `<slot.param>=KELO_KEYRING_SECRET` (any other query part with placeholder → 400 `bad_placement`).
- sends the body as it came, with no redirects;
- for chat/admin entries: streams the answer back: status, headers minus hop-by-hop ones, and the body as it arrives (SSE works).
- for connector entries: the answer is NOT streamed: reads it whole (at most 8 MiB; more → 502 `{"code": "too_big"}`), and replaces every form of the secret (`s`, `quote(s, safe="")`, `quote_plus(s)`) with `[redacted]` in the body and in header values, then answers with fixed `content-length`. A service that echoes a header back never shows the key to the server.
Timeouts: connect 15 s, read 300 s (the server's own stall rule still applies on its side).
Never logged: secrets, query strings, request or answer bodies, the upstream path. Logged: the entry name, the host, the status, the
duration.

## Server side

- `KEYRING_URL` (default `http://keyring:9500` in compose) and `KEYRING_TOKEN`. Without both, nothing changes: keys stay in the
  database as today. This covers tests, a native install, and an old compose file.
- When enabled:
  - saving a key in the app (PUT /api/providers/{name}/key) checks it as today, then stores it in the keyring with hosts = the host of
    the provider's base URL (a custom endpoint: the saved address's host; `local` when that host is private) and hint = its last 4
    characters. The server forgets it;
  - deleting deletes it there;
  - a model call for a provider whose key is in the keyring goes to `KEYRING_URL/fwd/<name>` with `x-keyring-upstream` = the real
    base URL and the key replaced by `KELO_KEYRING_SECRET`;
  - the model list and the balance checks go the same way.
- Moving the old keys: once the keyring is unlocked, every provider key in `api_keys` (not the system keys, whose names have a colon)
  is put into the keyring and then deleted from `api_keys`. It runs once a start, and does nothing when there is nothing left. A key
  that fails to go stays where it was and is tried again at the next start.
- Keys from the environment (.env `OPENAI_API_KEY` …) stay as they are in phase A. The owner's install guide says to enter keys in
  the app.

## Words for the privacy page / README (phase A ships them)

"AI provider keys you enter in the app are kept by a separate small program on your server, not by Kelo itself. Kelo asks it to
make the model call; it adds the key only for that provider's own address. Under Keyguard, it stays locked until your phone unlocks
Kelo."

## Implementation notes (server side, 61a-2)

- The keys in `/unlock` and `/rekey` are standard base64 (`base64.b64encode`) of the 32-byte ring key.
- `Settings.keyring_url` / `keyring_token` (from `KEYRING_URL` / `KEYRING_TOKEN`) carry the setting; tests pass a fake through `create_app(keyring_transport=...)`.
- The ring key is HKDF over `KeyGuard.key()` (the master key; the data key never feeds it). It changes only when Keyguard is turned on: `POST /api/keyguard/enroll/finish` first checks the step (`KeyGuard.pending_rotation`), calls `/rekey`, then re-seals the database; a refused step re-keys the keyring back. A failed `/rekey` answers 503 and Keyguard stays off.
- The start-up task tries the unlock at 0, 3, 10, 30 and 60 s while the keyring is still starting; while Keyguard is locked it stops, and the Keyguard unlock / recovery routes run it again.
- A balance check passes the keyring transport to `netguard.fetch`; the transport takes the host from the `sni_hostname` extension (fetch connects to the checked address) for `x-keyring-upstream`.
