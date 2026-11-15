"""Builds docs/security/e2e-vectors.json: fixed KE2E inputs and outputs every app tests against (explained in e2e-vectors.md).

Everything is deterministic (keys derived from fixed seeds with RFC 9180 DeriveKeyPair, fixed ts and nonces), so running this
again gives the same bytes; tests/test_e2e_core.py checks that the file in the repository equals build().

    uv run python tests/e2e_vectors_gen.py          # rewrite the file (only when the format changes on purpose)
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305  # noqa: E402

from assistant import e2e  # noqa: E402
from e2e_client import E2EClient  # noqa: E402

VECTORS = ROOT / "docs" / "security" / "e2e-vectors.json"

TS = 1731200000
SERVER_NOW = 1731200010
PAIR_CODE = "Kq7ZpX2mVb9TfLw4"   # 16 characters: the apps accept 16 to 128


def _seed(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def _hex(b: bytes) -> str:
    return b.hex()


def _priv(sk) -> bytes:
    return e2e._priv_bytes(sk)


def _frames(key: bytes, pieces: list[tuple[bytes, bool]]) -> tuple[list[dict], bytes]:
    sealer = e2e.FrameSealer(key)
    out, stream = [], b""
    for pt, last in pieces:
        counter = sealer.counter
        frame = sealer.seal(pt, last=last)
        flag = e2e.FLAG_LAST if last else e2e.FLAG_MORE
        out.append({
            "counter": counter,
            "flag": flag,
            "pt_text": pt.decode("utf-8"),
            "pt": _hex(pt),
            "nonce": _hex(e2e._frame_nonce(counter)),
            "aad": _hex(e2e.LABEL_RESPONSE + bytes([flag])),
            "frame": _hex(frame),
        })
        stream += frame
    return out, stream


def _request_vector(name: str, client: E2EClient, method: str, path: str, headers: dict, body: bytes, ikm_label: str,
                    nonce: bytes, ts: int = TS) -> tuple[dict, object]:
    ikm_e = _seed(ikm_label)
    sk_e = e2e.derive_key_pair(ikm_e)
    sealed = client.seal(method, path, headers, body, ts=ts, nonce=nonce, ikm_e=ikm_e)
    kid, enc, ct = e2e.split_envelope(sealed.envelope)
    head = sealed.inner.head_json()
    v = {
        "name": name,
        "ikm_e": _hex(ikm_e),
        "sk_e": _hex(_priv(sk_e)),
        "enc": _hex(enc),
        "shared_secret": _hex(sealed.ctx.shared_secret),
        "key": _hex(sealed.ctx.schedule.key),
        "base_nonce": _hex(sealed.ctx.schedule.base_nonce),
        "exporter_secret": _hex(sealed.ctx.schedule.exporter_secret),
        "head_json": head.decode("utf-8"),
        "body_text": body.decode("utf-8"),
        "inner": _hex(sealed.inner.encode()),
        "aad": _hex(e2e.AAD_REQUEST),
        "ct": _hex(ct),
        "envelope": _hex(sealed.envelope),
        "server_now": SERVER_NOW,
        "response_key": _hex(sealed.response_key()),
        "ws_c2s_key": _hex(sealed.ctx.export(e2e.LABEL_WS_C2S, 32)),
        "ws_s2c_key": _hex(sealed.ctx.export(e2e.LABEL_WS_S2C, 32)),
    }
    return v, sealed


def build() -> dict:
    server_ikm = _seed("kelo-e2e vectors v1 server")
    server = e2e.ServerKey(e2e.derive_key_pair(server_ikm))
    client = E2EClient(server.public_bytes)

    talk, talk_sealed = _request_vector(
        "talk", client, "POST", "/api/talk?x=1",
        {"Authorization": "Bearer test-token-0001", "Content-Type": "application/json"},
        '{"text":"안녕, Kelo"}'.encode("utf-8"), "kelo-e2e vectors v1 client talk", bytes(range(16)))
    get, get_sealed = _request_vector(
        "get_events", client, "GET", "/api/turns/42/events?after=0",
        {"authorization": "Bearer test-token-0001", "accept": "text/event-stream", "x-kelo-device": "phone-1"},
        b"", "kelo-e2e vectors v1 client events", bytes(range(16, 32)))

    rkey = bytes.fromhex(talk["response_key"])
    head_only, head_only_stream = _frames(rkey, [(e2e.response_head(204, {}), True)])
    simple, simple_stream = _frames(rkey, [
        (e2e.response_head(200, {"Content-Type": "application/json", "ETag": '"v1"'}), False),
        (b'{"ok":true,"reply":"\xeb\x84\xa4"}', True),
    ])
    skey = bytes.fromhex(get["response_key"])
    stream3, stream3_bytes = _frames(skey, [
        (e2e.response_head(200, {"content-type": "text/event-stream"}), False),
        (b'event: text\ndata: {"t":"Hel"}\n\n', False),
        (b'event: done\ndata: {}\n\n', True),
    ])

    ws_client = get_sealed.ws_channel()
    ws_server = e2e.WsChannel(send_key=bytes.fromhex(get["ws_s2c_key"]), recv_key=bytes.fromhex(get["ws_c2s_key"]))
    c2s_text = '{"type":"click","x":10,"y":20}'
    s2c_text = '{"type":"frame","seq":0,"w":2,"h":2}'
    s2c_bin = bytes.fromhex("ffd8ffe000104a464946")   # the start of a JPEG
    ws = {
        "aad": _hex(e2e.AAD_WS),
        "types": {"text": e2e.WS_TEXT, "binary": e2e.WS_BINARY},
        "close_codes": {"bad_envelope": e2e.WS_CLOSE_BAD_ENVELOPE, "unauthorized": e2e.WS_CLOSE_UNAUTHORIZED,
                        "key_changed": e2e.WS_CLOSE_KEY_CHANGED},
        "c2s_first": {"counter": 0, "nonce": _hex(e2e._frame_nonce(0)), "type": e2e.WS_TEXT, "payload_text": c2s_text,
                      "pt": _hex(bytes([e2e.WS_TEXT]) + c2s_text.encode()), "message": _hex(ws_client.seal(c2s_text))},
        "s2c_first": {"counter": 0, "nonce": _hex(e2e._frame_nonce(0)), "type": e2e.WS_TEXT, "payload_text": s2c_text,
                      "pt": _hex(bytes([e2e.WS_TEXT]) + s2c_text.encode()), "message": _hex(ws_server.seal(s2c_text))},
        "s2c_second": {"counter": 1, "nonce": _hex(e2e._frame_nonce(1)), "type": e2e.WS_BINARY, "payload": _hex(s2c_bin),
                       "pt": _hex(bytes([e2e.WS_BINARY]) + s2c_bin), "message": _hex(ws_server.seal(s2c_bin))},
    }

    # A streamed upload (envelope version 0x02): the head seals H with an empty body; the body follows in frames.
    up_body = "사진 한 장: 0123456789abcdef0123456789".encode("utf-8")
    up_ikm = _seed("kelo-e2e vectors v1 client upload")
    up_sealed, up_frames = client.seal_stream("PUT", "/api/uploads/photo.jpg",
                                              {"authorization": "Bearer test-token-0001", "content-type": "image/jpeg"},
                                              up_body, ts=TS, nonce=bytes(range(64, 80)), ikm_e=up_ikm, chunk=16)
    up_kid, up_enc, up_ct, _ = e2e.split_stream_head(up_sealed.envelope)
    body_key = up_sealed.ctx.export(e2e.LABEL_REQ_BODY, 32)
    upload = {
        "name": "upload_stream", "version": e2e.ENVELOPE_VERSION_STREAM, "ikm_e": _hex(up_ikm),
        "sk_e": _hex(_priv(e2e.derive_key_pair(up_ikm))), "enc": _hex(up_enc),
        "head_json": up_sealed.inner.head_json().decode(), "inner": _hex(e2e.InnerRequest(
            up_sealed.inner.method, up_sealed.inner.path, up_sealed.inner.headers, up_sealed.inner.ts, up_sealed.inner.nonce).encode()),
        "ct": _hex(up_ct), "head": _hex(up_sealed.envelope), "server_now": SERVER_NOW,
        "body_key": _hex(body_key), "body_aad_prefix": _hex(e2e.AAD_REQUEST), "body_text": up_body.decode("utf-8"),
        "body_frames": [{"counter": n, "flag": 1 if n == len(up_frames) - 1 else 0, "frame": _hex(f),
                         "pt": _hex(up_body[n * 16:(n + 1) * 16])} for n, f in enumerate(up_frames)],
        "envelope": _hex(up_sealed.envelope + b"".join(up_frames)),
        "response_key": _hex(up_sealed.response_key()),
        "note": "body frames cut at 16 bytes here to show several; real clients use up to 64 KiB",
    }

    # Negative cases.
    frames3 = [bytes.fromhex(f["frame"]) for f in stream3]
    tampered = bytearray(frames3[1])
    tampered[-1] ^= 0x01
    wrong_kid = client.seal("GET", "/api/me", {"authorization": "Bearer test-token-0001"}, ts=TS, nonce=bytes(range(32, 48)),
                            ikm_e=_seed("kelo-e2e vectors v1 client wrong kid"), kid=bytes(8))
    old = client.seal("GET", "/api/me", {"authorization": "Bearer test-token-0001"}, ts=SERVER_NOW - e2e.TS_WINDOW - 1,
                      nonce=bytes(range(48, 64)), ikm_e=_seed("kelo-e2e vectors v1 client old ts"))
    bad_env = bytearray.fromhex(talk["envelope"])
    bad_env[-1] ^= 0x01
    bad_ver = bytearray.fromhex(talk["envelope"])
    bad_ver[0] = 0x02
    # A frame sealed as "more" where the stream then ends; and a last frame followed by one more frame.
    after_last = simple_stream + bytes.fromhex(stream3[1]["frame"])
    negative = [
        {"name": "tampered_frame", "kind": "response", "key": _hex(skey),
         "stream": _hex(frames3[0] + bytes(tampered) + frames3[2]), "expect": e2e.BadFrame.code,
         "note": "last byte of frame 1's tag flipped: frame 0 opens, frame 1 must fail"},
        {"name": "truncated_stream", "kind": "response", "key": _hex(skey), "stream": _hex(frames3[0] + frames3[1]),
         "expect": e2e.Truncated.code, "note": "the stream of 3 without its last frame: every frame opens, close() must fail"},
        {"name": "partial_frame", "kind": "response", "key": _hex(skey), "stream": _hex(stream3_bytes[:-5]),
         "expect": e2e.Truncated.code, "note": "the stream ends inside the last frame"},
        {"name": "counter_gap", "kind": "response", "key": _hex(skey), "stream": _hex(frames3[0] + frames3[2]),
         "expect": e2e.BadFrame.code, "note": "frame 1 missing: frame 2 does not open with counter 1"},
        {"name": "data_after_last", "kind": "response", "key": _hex(rkey), "stream": _hex(after_last),
         "expect": e2e.BadFrame.code, "note": "bytes after the last frame"},
        {"name": "wrong_kid", "kind": "request", "envelope": _hex(wrong_kid.envelope), "server_now": SERVER_NOW,
         "expect": e2e.UnknownKid.code, "http_status": 409, "http_body": {"code": "e2e_key_changed"},
         "note": "kid bytes are zero: the server answers plaintext 409 before trying to open"},
        {"name": "replayed_nonce", "kind": "request", "envelope": talk["envelope"], "server_now": SERVER_NOW, "repeat": 2,
         "expect": e2e.Replayed.code, "note": "the talk envelope sent twice: the first opens, the second is refused"},
        {"name": "old_timestamp", "kind": "request", "envelope": _hex(old.envelope), "server_now": SERVER_NOW,
         "ts": SERVER_NOW - e2e.TS_WINDOW - 1, "expect": e2e.StaleTimestamp.code,
         "note": "ts 301 s before server_now (window is ±300 s)"},
        {"name": "tampered_envelope", "kind": "request", "envelope": _hex(bytes(bad_env)), "server_now": SERVER_NOW,
         "expect": e2e.BadEnvelope.code, "http_status": 400, "note": "last byte of the talk envelope flipped"},
        {"name": "bad_version", "kind": "request", "envelope": _hex(bytes(bad_ver)), "server_now": SERVER_NOW,
         "expect": e2e.BadEnvelope.code, "http_status": 400, "note": "first byte 0x02"},
        {"name": "ws_tampered", "kind": "ws_c2s", "key": get["ws_c2s_key"],
         "message": _hex(bytes.fromhex(ws["c2s_first"]["message"])[:-1] + bytes([bytes.fromhex(ws["c2s_first"]["message"])[-1] ^ 1])),
         "expect": e2e.BadFrame.code, "note": "the first c2s message with its last byte flipped"},
        {"name": "ws_unknown_type", "kind": "ws_c2s", "key": get["ws_c2s_key"],
         "message": _hex(ChaCha20Poly1305(bytes.fromhex(get["ws_c2s_key"])).encrypt(e2e._frame_nonce(0), b"\x03hi", e2e.AAD_WS)),
         "expect": e2e.BadFrame.code, "note": "opens, but the type byte is 0x03"},
        {"name": "stream_body_truncated", "kind": "request", "server_now": SERVER_NOW,
         "envelope": _hex(up_sealed.envelope + b"".join(up_frames[:-1])), "expect": e2e.BadEnvelope.code, "http_status": 400,
         "note": "the upload without its last body frame"},
        {"name": "stream_body_tampered", "kind": "request", "server_now": SERVER_NOW,
         "envelope": _hex(up_sealed.envelope + up_frames[0][:-1] + bytes([up_frames[0][-1] ^ 1]) + b"".join(up_frames[1:])),
         "expect": e2e.BadEnvelope.code, "http_status": 400, "note": "the upload with body frame 0's last byte flipped"},
    ]

    return {
        "version": 1,
        "revision": 2,
        "spec": "docs/security/e2e-and-phone-key.md section 1",
        "suite": {"mode": e2e.MODE_BASE, "kem_id": e2e.KEM_ID, "kdf_id": e2e.KDF_ID, "aead_id": e2e.AEAD_ID,
                  "name": "DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 / ChaCha20-Poly1305"},
        "labels": {
            "info_prefix": e2e.INFO_PREFIX.decode(), "aad_request": e2e.AAD_REQUEST.decode(),
            "response": e2e.LABEL_RESPONSE.decode(), "ws_c2s": e2e.LABEL_WS_C2S.decode(), "ws_s2c": e2e.LABEL_WS_S2C.decode(),
            "aad_ws": e2e.AAD_WS.decode(), "envelope_version": e2e.ENVELOPE_VERSION,
            "envelope_version_stream": e2e.ENVELOPE_VERSION_STREAM, "req_body": e2e.LABEL_REQ_BODY.decode(), "flag_more": e2e.FLAG_MORE,
            "flag_last": e2e.FLAG_LAST, "ts_window": e2e.TS_WINDOW, "replay_window": e2e.REPLAY_WINDOW,
            "max_frame_plaintext": e2e.MAX_FRAME_PLAINTEXT,
        },
        "server": {
            "ikm": _hex(server_ikm),
            "sk": _hex(_priv(server._sk)),
            "pk": _hex(server.public_bytes),
            "pk_b64url": server.public_b64,
            "kid": server.kid_hex,
            "info": _hex(server.info),
            "info_text": server.info.decode(),
            "fingerprint": server.fingerprint,
            "e2e_info": server.public_info(),
            "pair_code": PAIR_CODE,
            "pair_link": f"https://example.kelo.chat/#pair={PAIR_CODE}&k={server.public_b64}",
        },
        "requests": [talk, get],
        "stream_requests": [upload],
        "responses": [
            {"name": "head_only", "request": "talk", "key": talk["response_key"], "frames": head_only,
             "stream": _hex(head_only_stream), "note": "frame 0 is also the last frame (no body)"},
            {"name": "simple", "request": "talk", "key": talk["response_key"], "frames": simple, "stream": _hex(simple_stream)},
            {"name": "stream3", "request": "get_events", "key": get["response_key"], "frames": stream3,
             "stream": _hex(stream3_bytes), "note": "server-sent events: one frame per event"},
        ],
        "websocket": {"request": "get_events", **ws},
        "negative": negative,
    }


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    VECTORS.write_text(dumps(build()), encoding="utf-8")
    print(f"wrote {VECTORS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
