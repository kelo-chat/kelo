# End-to-end encryption and the phone-held master key (design v1)

Status: design, 2026-11-10. One spec for the server, Android, iPhone and desktop apps, so the platforms do not drift apart.
Every platform must pass the same test vectors (`docs/security/e2e-vectors.json`, made by the server work first).

Two features:

1. **KE2E**: the apps talk to their own Kelo server end to end. Cloudflare (the `<name>.kelo.chat` tunnel ends TLS there), the
   operator server and anything else on the path see only ciphertext.
2. **Keyguard**: the server's master key can be held by the user's phones instead of a file next to the database, so a stolen,
   powered-off server disk does not open the keychain, cards or tokens.

Both use only standard primitives through well-known libraries. Nothing here is a new cipher.

---

## 1. KE2E

### 1.1 What it protects, and what stays visible

| Who | Before | With KE2E |
|---|---|---|
| Cloudflare (default address) | request paths, bodies, tokens, answers | that a device talks to `/e2e`, sizes, timing |
| Operator server (kelo.chat) | nothing of the chat (it is not on the path) | same |
| Network attacker without the server's private key | nothing (TLS) | nothing |

Still visible: the host name, that it is a Kelo server, message sizes and timing. Not covered by v1: push payloads (they already carry
only kind and ids), the Google/GitHub sign-in relay (the operator still sees a token in transit), web pages the server serves to a
browser (install, privacy, downloads, OAuth return pages).

### 1.2 Keys

- The server (each instance on a computer) has a static **X25519** key pair, made on first start, kept at `<state>/e2e/server.key`
  (0600, never logged, never in an API answer). Public key `pkS` (32 bytes).
- `kid` = first 8 bytes of SHA-256(`pkS`), lowercase hex (16 characters).
- **Fingerprint** for people: SHA-256(`pkS`), first 15 bytes, base32 (RFC 4648, no padding), in groups of 4 letters
  (e.g. `ABCD-EFGH-…`). Shown by `assistant phone fingerprint`, in the app under My → Devices, and on the server's QR page.
- Rotation: `assistant e2e rotate` makes a new pair; every device must pair again (scan a new QR). v1 has no automatic rotation.

### 1.3 Pairing carries the key

- The pairing link becomes `https://host/#pair=<code>&k=<base64url(pkS), no padding>`. The fragment is never sent to any server, so
  the key reaches the phone only through the QR or link shown on the owner's screen. Old apps must keep parsing the link (check each
  app's parser; extra fragment parameters must be ignored).
- An app that got `k` **pins** it: from then on it sends every API call through KE2E and accepts no plaintext answer from that server
  (no fallback; a failure says "the server must be updated" or "pair again").
- The pairing call itself (`POST /api/pair` with the code) already goes through KE2E when `k` is known, so the code is not visible
  on the path either.
- Typed codes (key sign-in, reviewer access codes) have no `k`: the app fetches `GET /api/e2e/info` (plaintext) and uses that key
  **trust on first use**. The device is marked *key not verified*; My → Devices shows the fingerprint to compare with
  `assistant phone fingerprint`, and scanning the QR once marks it verified.
- Devices paired before KE2E: on update the app fetches `/api/e2e/info`, pins it as *not verified* (TOFU) and switches to KE2E.
- After a device's first KE2E call the server marks that device **e2e-only**: its token is refused on plaintext `/api/*` from then
  on. A stolen token cannot be used around the encryption.

### 1.4 The envelope (HPKE, RFC 9180)

Suite: **DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 / ChaCha20-Poly1305**, base mode (KEM 0x0020, KDF 0x0001, AEAD 0x0003).

`info` = ASCII `"kelo-e2e v1 "` + `kid`.

**Request** (one per HTTP call):

1. `enc, ctx = SetupBaseS(pkS, info)`.
2. Inner request = `u32 BE length of H` ‖ `H` ‖ `body`. `H` is UTF-8 JSON:
   `{"m": "POST", "p": "/api/talk?x=1", "h": {"authorization": "Bearer …", "content-type": "application/json"}, "ts": 1731200000, "n": "<16 random bytes, base64url>"}`.
   Only these headers go inside: authorization, content-type, accept, accept-language, if-none-match, range, and `x-kelo-*`.
3. `ct = ctx.Seal(aad = "kelo-e2e v1 req", pt = inner)`.
4. Outer HTTP: `POST /e2e`, `Content-Type: application/x-kelo-e2e`, body = `0x01` ‖ `kid` (8 bytes) ‖ `enc` (32) ‖ `ct`.
   The outer request carries no Authorization header and no query.

Server: `ctx = SetupBaseR(enc, skS, info)`, open, check `ts` within ±300 s of its clock and `n` not seen in the last 10 minutes
(replay), then run the inner request through the normal app (same routes, same auth, same rate limits) and seal the answer.
Unknown `kid` → plaintext `409 {"code": "e2e_key_changed"}` (the app says: pair again). Anything that does not open → `400` with no
detail.

**Response** (also for streams):

- `key = ctx.Export("kelo-e2e v1 res", 32)`.
- Body = a sequence of frames: `u32 BE length of c` ‖ `c`, with
  `c = ChaCha20-Poly1305(key, nonce = 4 zero bytes ‖ u64 BE counter, aad = "kelo-e2e v1 res" ‖ flag byte, pt)`. Counter starts at 0.
  Flag `0x00` = more frames follow, `0x01` = last frame.
- Frame 0 plaintext = response head JSON `{"s": 200, "h": {"content-type": "…", "etag": "…", "retry-after": "…"}}`. Following frames
  = the body in pieces (a server-sent-events stream sends one frame per event as it happens).
- The outer response is `200`, `Content-Type: application/x-kelo-e2e`, `Cache-Control: no-store`, streamed. The real status is in
  frame 0. A stream that ends without a last frame is an error (truncation); a counter gap or a frame that does not open is an error.
- Large uploads and downloads: the same framing; inner body frames of at most 64 KiB.

**WebSocket** (live view of the agent's browser):

- `GET /e2e/ws` upgrade. The client's first message = the request envelope above whose inner `p` is the target WebSocket path.
- Then both directions use `c2s = ctx.Export("kelo-e2e v1 ws c2s", 32)` and `s2c = ctx.Export("kelo-e2e v1 ws s2c", 32)`, each with
  its own counter, same nonce layout, `aad = "kelo-e2e v1 ws"`; one WebSocket message = one sealed frame (no length prefix).

### 1.5 What stays plaintext on purpose

`/healthz`, `GET /api/e2e/info` (`{"v": 1, "kid", "pk", "fingerprint"}`), the website pages, `/download/*`, OAuth return pages a
browser opens, `/broker/*` (operator services between servers), and the pre-pairing screens of old apps.

### 1.6 Libraries

- Python: `cryptography` primitives (X25519, HKDF, ChaCha20Poly1305) composed exactly as RFC 9180 says, or a maintained HPKE
  package if one is in the lock file; either way it must pass the RFC 9180 test vectors for this suite and our own vectors.
- Android and desktop (JVM): Google **Tink** HPKE (`HpkeParameters`, X25519 / HKDF-SHA256 / ChaCha20-Poly1305), raw HPKE contexts
  where Tink exposes them; if Tink cannot export secrets, use BouncyCastle's HPKE. Same vectors.
- iPhone: **CryptoKit HPKE** (`HPKE.Sender` / `HPKE.Recipient`, `.Curve25519_SHA256_ChachaPoly`, `exportSecret`), available on
  iOS 17, which is the app's minimum.

### 1.7 Every platform must have

- A KE2E client that wraps the existing API client at one place (no per-call changes in screens).
- Streaming (turn events), uploads/downloads, the live-view WebSocket.
- Pinning, *key not verified* state, the fingerprint in My → Devices, "pair again" when the key changed.
- No fallback to plaintext once pinned.
- Unit tests against `docs/security/e2e-vectors.json` (fixed keys, `enc`, nonces, outputs) and the RFC 9180 vectors.

---

## 2. Keyguard: the master key held by the user's phones

### 2.1 Today

`store.py` loads one master key (Fernet key: `$ASSISTANT_SECRET_KEY` or `<state>/master.key`). From it come the Fernet key for provider
API keys and the AES-256-GCM sealers (vault, auto-pay cards, personal data). Whoever has the disk has the key.

### 2.2 The mode

Optional, chosen by the owner (My → Security). Off by default in v1.

- **Enable** (any signed-in phone, over KE2E, biometric confirmation):
  1. The server makes a new master key `K'` and re-seals everything from `K` to `K'` in one transaction (key rotation; old backups
     keep needing the old key).
  2. It sends `K'` to the phone inside the KE2E answer. The phone wraps it with its hardware key (2.4) and sends back the wrapped blob;
     the server stores `{device_id, alg, blob, created}`.
  3. A **recovery code** (24 characters, shown once on the phone, never stored in plain): the server stores
     `Argon2id(code, salt) → AES-256-GCM wrap of K'`. It is the only way back if every phone is lost.
  4. The server deletes `master.key`. If `ASSISTANT_SECRET_KEY` is set in `.env`, enabling is refused until
     `assistant keyguard enable` on the server machine removes it (the server cannot edit the host's `.env`).
  5. A check value `HMAC-SHA256(K', "kelo keyguard check")` is stored so a wrong key is recognised.
- **After a restart** the server is **locked**: it holds no master key. Nothing sealed can be opened (provider keys, vault, cards,
  connector tokens), so the agent cannot run. The server sends an "unlock" push to every phone; the app shows "Kelo is locked: open
  it". The owner taps, passes biometrics, the app fetches its blob (`GET /api/keyguard/blob`, KE2E), unwraps it on the phone and
  sends the key (`POST /api/keyguard/unlock`, KE2E). The server checks the check value and keeps the key **in memory only**.
- **More phones**: while unlocked, another phone can enroll (step 2 for that phone).
- **Removing a phone** deletes its blob. The last blob cannot be removed without turning the mode off.
- **Turning it off**: while unlocked, the owner chooses to store the key on the server again (`master.key` written back).
- **Lost all phones**: `assistant keyguard recover` on the server machine (or the app on a new phone) with the recovery code.
- Locked state in the API: `GET /api/keyguard` → `{"mode": "phone", "locked": true, "phones": [...]}`. Endpoints that need a secret
  answer `423 {"code": "locked"}`; the apps show the unlock prompt for it.

### 2.3 Server side

A `KeyGuard` class owns the master key's life (load, lock, unlock, rotate, enroll, recover) and hands sealers to `Store`; nothing else
reads `master.key` or the env var. The key never goes to logs, crash reports or API answers except inside KE2E to an enrolling phone.

### 2.4 Phone side (the wrap is the phone's own business, but both phones do the same thing)

- **Android**: an AES-256-GCM key in AndroidKeyStore, StrongBox when the device has it, `setUserAuthenticationRequired(true)` with
  per-use biometric or device credential (BiometricPrompt + CryptoObject). Blob = `0x01` ‖ 12-byte IV ‖ ciphertext+tag.
- **iPhone**: a CryptoKit `SecureEnclave.P256.KeyAgreement` key with access control `[.privateKeyUsage, .userPresence]` (5.9).
  Wrap with CryptoKit HPKE DHKEM(P-256, HKDF-SHA256) / HKDF-SHA256 / AES-256-GCM, empty info, AAD `"kelo-keyguard v1"` (5.14).
  (`SecKeyCreateEncryptedData` ECIES cannot take an AAD.) Blob = `0x02` ‖ `enc` (65 bytes) ‖ ciphertext ‖ tag.
- **Desktop**: v1 does not hold the key. It shows the locked state and asks the owner to open Kelo on a phone.
- The blob format byte lets the server tell which kind it stores; the server never opens a blob.

---

## 3. Order of work

1. Server: KE2E core module + `docs/security/e2e-vectors.json` + reference Python client (standalone, before any big refactor).
2. Apps (in parallel, from the vectors): KE2E client core and tests; then pairing, pinning, streaming, uploads, WebSocket.
3. Server: the object-oriented split of `server.py` (routers and service classes, behaviour unchanged, all tests green).
4. Server: KE2E wired in front of the routes; device e2e-only flag; `/api/e2e/info`; fingerprint CLI; QR with `k`.
5. Server + apps: Keyguard.
6. End-to-end tests: each app against a local server, through a proxy that records traffic and proves no plaintext body crosses it.

---

## 4. Words for the site, the privacy page and the stores (only once KE2E has shipped)

Owner's choice (2026-11-10): do not name Cloudflare. Say that only your phone and your server can read it. Keep the AI-company clause:
the server does send the conversation to the AI company the user chose, and leaving it out would be untrue (and App Store 5.1.2
needs it disclosed).

- ko: "앱과 내 서버 사이의 대화는 종단간 암호화돼요. 오가는 길에서는 내 폰과 내 서버 말고는 아무도 읽을 수 없어요. 답을 만들 때는 내가 고른 AI 회사로 대화가 가요."
- en: "Chats between the app and your server are end-to-end encrypted. On the way, nobody but your phone and your server can read them. To write a reply, your server sends the conversation to the AI company you chose."
- The privacy page also lists the exceptions: sizes and timing stay visible, push carries only kind and ids, the sign-in relay handles a
  token in passing, and a phone paired before KE2E switches to KE2E on its own after the update but shows *key not verified* until
  its QR is scanned once.

---

## 5. Changes after the first platform reports (2026-11-10, v1 still)

1. **Old apps and the `&k=` link**: today's Android/desktop parsers read everything after `#pair=` as the code. The server's
   `POST /api/pair` therefore cuts the code at the first `&` before checking it, so an old app that sends `CODE&k=…` still pairs
   (over plaintext, as before). New apps split the fragment and pin `k`.
2. **Streamed request bodies** (large uploads): envelope version `0x02` = `0x02` ‖ `kid` ‖ `enc` ‖ `u32 BE len(ct)` ‖ `ct` (sealing
   only the head `H`, with an empty body) ‖ body frames. Body frames use `Export("kelo-e2e v1 req body", 32)`, the response frame
   layout (`u32` length, counter nonce, flag byte) with `aad = "kelo-e2e v1 req" ‖ flag`, at most 64 KiB plaintext each, last frame
   flagged `0x01`. Version `0x01` (body inside `ct`) stays for normal calls.
3. **Response head headers**: `h` carries every response header except hop-by-hop headers (connection, keep-alive,
   transfer-encoding, upgrade, proxy-*, te, trailer) and `set-cookie`, names lowercased. (The desktop live view needs `x-frame-*`,
   `x-you-control`, `x-controller`; downloads need `content-length` and `content-disposition`.)
4. **Last-frame flag**: the flag is only in the AAD, so a reader tries flag `0x00` first and then `0x01`; a frame that opens with
   neither is an error.
5. **No silent plaintext after support was seen**: once an app has seen KE2E on a server (pinned or `/api/e2e/info` answered), a later
   `404` or plaintext answer from that server is an error, never a reason to stay plain. Only a server that never offered KE2E may be
   used in plaintext, and the app shows that state.
6. **Libraries**: JVM (desktop) and Android use **BouncyCastle** (`org.bouncycastle:bcprov-jdk18on`, lightweight API: HPKE with
   export, fixed ephemeral keys for tests). Tink's public API cannot export secrets. The pure-JVM core lives once in Android
   (`chat.kelo.app.core.e2e`) and reaches the desktop through the `shared/` module; no second copy.
7. Desktop facts: chat answers are followed with `GET /api/turns/{id}?wait=20`, the live view polls `…/frame` (no WebSocket on desktop).
8. **WebSocket message type**: the first plaintext byte of every sealed WebSocket message is its type, `0x01` text or `0x02` binary
   (the live view mixes a text header and a JPEG). If the first envelope fails, the server closes with code `4400` (does not open),
   `4401` (not signed in / ticket refused) or `4409` (`e2e_key_changed`).
9. **Keyguard unlock condition** (both phones the same): biometrics **or** the device passcode, and the key survives re-enrolling a
   face or finger. iPhone: `[.privateKeyUsage, .userPresence]` (the wallet already uses `.userPresence`). Android:
   `setUserAuthenticationRequired(true)` with `setUserAuthenticationParameters(0, AUTH_BIOMETRIC_STRONG | AUTH_DEVICE_CREDENTIAL)`.
   (`.biometryCurrentSet` would lose the key when Face ID is set up again.)
10. **Errors with a top-level `code`**: answers such as `423 {"code": "locked"}`, `409 {"code": "e2e_key_changed"}` carry `code` at the
    top level next to `detail`; every app's error type reads both.
11. **Vectors**: example pairing codes in `e2e-vectors.json` must meet the apps' code rules (16 to 128 characters).
12. **Export compliance (iPhone)**: KE2E uses CryptoKit (in iOS) with standard algorithms; re-check the App Store Connect encryption
    answers and `ITSAppUsesNonExemptEncryption` before the first build with KE2E ships. Decided by the owner, not by an agent.
13. **Clock skew and replays**: the envelope has already opened, so the server answers SEALED (normal response frames) with status
    400 and `{"code": "e2e_clock", "now": <server unix time>}` or `{"code": "e2e_replay"}`. The app corrects its clock offset once from
    `now` and retries once. Only envelopes that do not open get the plaintext `400` with no detail.
17. **WebSocket head**: after the first envelope opens, the server's first s2c message is a text message (type `0x01`) with the head
    JSON `{"s": 101, "h": {...}}` (or an error status and then a close with 4400/4401/4409), so the client knows the socket is ready.
14. **Keyguard blob AAD** (both phones): the fixed ASCII string `"kelo-keyguard v1"`.
15. **Android 26-29**: a Keystore key cannot combine a CryptoObject with the device credential there, so the Keyguard key uses a
    30-second authentication window on those versions; the enable screen warns that removing the screen lock deletes the key
    (the recovery code is then needed).
16. **Answered secure-entry lines** (vault/login requests): an answered request never sits above the composer; finished lines
    (saved, cancelled, expired, replaced) clear at every reply end and on reload; open requests stay. Same rule on all three apps.
18. **Servers older than 0.9.0** (no `X-Kelo-Server` header, no `/api/e2e/info`): apps from 0.9.0 on do not talk to them; they
    show "update your server" (same screen as a too-old server). There is no plaintext mode for a server that never offered KE2E,
    so nobody on the path can make a new app fall back by hiding the header or the info endpoint. (Replaces the "never offered"
    exception in item 5.)
