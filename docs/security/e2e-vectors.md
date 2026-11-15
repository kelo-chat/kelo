# KE2E test vectors (`e2e-vectors.json`)

Fixed inputs and outputs for the KE2E envelope in `e2e-and-phone-key.md` section 1. Every client (Android, iPhone, desktop) runs them
in its unit tests, next to the RFC 9180 vectors for the same suite (Appendix A.2.1, base mode). All byte strings are lowercase hex
unless the field name ends in `_text` (UTF-8 text) or `_b64url` (base64url, no padding).

The file is made by `tests/e2e_vectors_gen.py` (deterministic: keys come from fixed seeds through RFC 9180 `DeriveKeyPair`, `ts` and
nonces are fixed). `tests/test_e2e_core.py` fails if the file and the generator disagree. The server's own code is
`src/assistant/e2e.py`; the reference client is `tests/e2e_client.py`.

## Top level

| Field | Meaning |
|---|---|
| `version` | Format of this file (1). A change that breaks old app tests raises it. |
| `revision` | Bumped when vectors are added or a case changes within the same format (2: spec section 5: streamed request bodies, the WebSocket type byte, a 16-character pairing code). |
| `suite` | HPKE mode and ids: base mode 0, KEM 0x0020, KDF 0x0001, AEAD 0x0003. |
| `labels` | Every constant string and number of the protocol, as used below (info prefix, AADs, exporter labels, flags, windows, 64 KiB frame limit). |
| `server` | The server's key pair and what is derived from it. |
| `requests` | Two sealed requests (`talk`: POST with a JSON body; `get_events`: GET of an event stream, no body). |
| `stream_requests` | A streamed upload (envelope version `0x02`). |
| `responses` | Response streams sealed with a request's `response_key`. |
| `websocket` | First message in each direction, with the `get_events` request's WebSocket keys. |
| `negative` | Inputs that must fail, with the expected error. |

## `server`

- `ikm`: the seed; `sk` = `DeriveKeyPair(ikm)` (X25519 private key, 32 bytes); `pk` its public key; `pk_b64url` the same as the
  pairing link's `k=` parameter.
- `kid`: first 8 bytes of SHA-256(`pk`), hex (16 characters). In the envelope it travels as the 8 raw bytes.
- `info` / `info_text`: the HPKE info, `"kelo-e2e v1 " + kid`.
- `fingerprint`: SHA-256(`pk`), first 15 bytes, base32 (RFC 4648, no padding), groups of 4 joined with `-`.
- `e2e_info`: the exact body of `GET /api/e2e/info`.
- `pair_code`, `pair_link`: an example pairing code (16 characters, inside the apps' 16 to 128 rule) and the link carrying it and
  the key (`#pair=<code>&k=<pk_b64url>`); parsers must ignore parameters they do not know.

## `requests[]`

| Field | Meaning |
|---|---|
| `ikm_e`, `sk_e`, `enc` | The client's ephemeral key: seed, private key (`DeriveKeyPair(ikm_e)`), public key = HPKE `enc`. |
| `shared_secret`, `key`, `base_nonce`, `exporter_secret` | HPKE intermediate values of this context (for debugging an implementation). |
| `head_json`, `body_text` | The inner request's JSON head and body as text. |
| `inner` | The exact inner plaintext: `u32 BE len(head)` ‖ head ‖ body. |
| `aad`, `ct` | `ct = Seal(aad = "kelo-e2e v1 req", inner)`, the first seal of the context (sequence 0). |
| `envelope` | The outer HTTP body: `0x01` ‖ kid (8 bytes) ‖ `enc` ‖ `ct`. |
| `server_now` | The server clock to use when opening (the `ts` in the head is 10 s before it). |
| `response_key` | `Export("kelo-e2e v1 res", 32)`. |
| `ws_c2s_key`, `ws_s2c_key` | `Export("kelo-e2e v1 ws c2s", 32)`, `Export("kelo-e2e v1 ws s2c", 32)`. |

How to use them:

- **Every platform** (the receiving side of HPKE is deterministic): act as the recipient with `server.sk`, `enc` and `server.info`,
  open `ct` with `aad`, compare with `inner`, and compare the three exported keys. CryptoKit: `HPKE.Recipient(privateKey:ciphersuite:
  info:encapsulatedKey:)`, then `open(_:authenticating:)` and `exportSecret(context:outputByteCount:)`.
- **Libraries that accept a fixed ephemeral key** (BouncyCastle, Tink's internal test API): seal `inner` with `sk_e` and compare
  `ct` and `envelope`. CryptoKit's `HPKE.Sender` always picks a random ephemeral key, so on iPhone test the sending side by a round
  trip (seal with `HPKE.Sender`, open with `HPKE.Recipient` and `server.sk`).
- Your own JSON encoder may write the head with other spacing or key order: that is fine (the server parses JSON). Byte-exact
  comparisons use `inner` as given.

## `stream_requests[]` (envelope version 0x02)

For bodies too large to hold in memory. `head` = `0x02` ‖ kid ‖ `enc` ‖ `u32 BE len(ct)` ‖ `ct`, where `ct` seals `inner` = the
head JSON with an **empty** body (a server refuses a 0x02 head whose `ct` carries body bytes). Then the body as frames, laid out like
response frames: `u32 BE len(c)` ‖ `c`, `c = ChaCha20-Poly1305(body_key, 4 zero bytes ‖ u64 BE counter, "kelo-e2e v1 req" ‖ flag, pt)`
with `body_key = Export("kelo-e2e v1 req body", 32)`, at most 64 KiB plaintext each, the last flagged `0x01`. An empty body is one
empty last frame. `envelope` is everything the HTTP body carries (head then frames); `body_frames[]` lists each frame (cut at 16
bytes in the vector only to show several). The answer uses `response_key` exactly as for version 0x01.

## `responses[]`

Each has `key` (the request's `response_key`), `frames[]` and `stream` (all frames concatenated, as the HTTP body arrives).

Per frame: `counter`, `flag` (0 = more follow, 1 = last), `pt` / `pt_text`, `nonce` (4 zero bytes ‖ u64 BE counter), `aad`
(`"kelo-e2e v1 res"` ‖ flag byte), and `frame` = `u32 BE len(c)` ‖ `c` with `c = ChaCha20-Poly1305(key, nonce, aad, pt)`.

- `head_only`: one frame that is both frame 0 (the head `{"s":204,"h":{}}`) and the last frame.
- `simple`: head (status 200, content-type, etag) then one body frame marked last.
- `stream3`: an event stream: head, one event (flag 0), the final event (flag 1).

Frame 0's plaintext is always the head JSON `{"s": status, "h": {lower-case header: value}}`: every response header except
hop-by-hop ones (connection, keep-alive, transfer-encoding, upgrade, proxy-*, te, trailer) and set-cookie; a repeated header is
joined with ", ". A reader does not know the flag in
advance: it tries flag 0, then flag 1 (or checks both AADs); exactly one opens.

## `websocket`

`c2s_first` is the first message the app sends after the opening envelope (sealed with `ws_c2s_key`, counter 0), `s2c_first` and
`s2c_second` the first two from the server (sealed with `ws_s2c_key`, counters 0 and 1). AAD is `"kelo-e2e v1 ws"`, no flag, no
length prefix: one WebSocket binary message = ciphertext ‖ tag. Each direction counts on its own.

The plaintext's first byte is the message type (`types`): `0x01` text (UTF-8 follows, `payload_text`), `0x02` binary (`payload`,
e.g. a JPEG of the live view). `pt` is the whole plaintext with that byte. Any other type is an error. `close_codes`: the server
closes with 4400 when the first envelope does not open, 4401 when the inner request is not signed in, 4409 for `e2e_key_changed`.

## `negative[]`

`expect` is the error name used by the server code and the reference client:

| expect | Where | What the app or server does |
|---|---|---|
| `bad_frame` | response / WebSocket | Stop reading, discard everything, show a connection error. |
| `truncated` | response | The stream ended without a last frame (or inside a frame): an error, not a short answer. |
| `unknown_kid` | request (server) | Plaintext `409 {"code": "e2e_key_changed"}`; the app says "pair again". |
| `bad_envelope` | request (server) | Plaintext `400`, no body detail. |
| `replayed` | request (server) | Refused; `repeat: 2` means open the same envelope twice with one server, the second fails. |
| `stale_timestamp` | request (server) | `ts` outside ±300 s of `server_now`. |

Cases: `tampered_frame`, `truncated_stream`, `partial_frame`, `counter_gap`, `data_after_last` (responses); `wrong_kid`,
`replayed_nonce`, `old_timestamp`, `tampered_envelope`, `bad_version` (requests, server side; an app can use them to test its own
envelope parser if it has one); `stream_body_truncated`, `stream_body_tampered` (a 0x02 upload missing its last body frame or with
a flipped byte: the server answers 400); `ws_tampered`, `ws_unknown_type` (open with `key` as the receiving c2s key).
