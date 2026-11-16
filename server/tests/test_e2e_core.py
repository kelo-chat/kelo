"""KE2E core (server/src/assistant/e2e.py): RFC 9180 vectors, our own vectors (docs/security/e2e-vectors.json), keys, envelope,
frames, WebSocket, replay and clock checks. No network, no web framework."""

import json
import os
import stat

import httpx
import pytest
from cryptography.hazmat.primitives import hpke as crypto_hpke

from assistant import e2e
from e2e_client import E2EClient
import e2e_vectors_gen

# --------------------------------------------------------------------------------------------------------------------------------
# RFC 9180 Appendix A.2.1: DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, ChaCha20Poly1305, base mode
# --------------------------------------------------------------------------------------------------------------------------------
RFC = {
    "info": "4f6465206f6e2061204772656369616e2055726e",
    "ikmE": "909a9b35d3dc4713a5e72a4da274b55d3d3821a37e5d099e74a647db583a904b",
    "pkEm": "1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a",
    "skEm": "f4ec9b33b792c372c1d2c2063507b684ef925b8c75a42dbcbf57d63ccd381600",
    "ikmR": "1ac01f181fdf9f352797655161c58b75c656a6cc2716dcb66372da835542e1df",
    "pkRm": "4310ee97d88cc1f088a5576c77ab0cf5c3ac797f3d95139c6c84b5429c59662a",
    "skRm": "8057991eef8f1f1af18f4a9491d16a1ce333f695d4db8e38da75975c4478e0fb",
    "enc": "1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a",
    "shared_secret": "0bbe78490412b4bbea4812666f7916932b828bba79942424abb65244930d69a7",
    "key_schedule_context": "00431df6cd95e11ff49d7013563baf7f11588c75a6611ee2a4404a49306ae4cfc5b69c5718a60cc5876c358d3f7fc31ddb598503f67be58ea1e798c0bb19eb9796",
    "secret": "5b9cd775e64b437a2335cf499361b2e0d5e444d5cb41a8a53336d8fe402282c6",
    "key": "ad2744de8e17f4ebba575b3f5f5a8fa1f69c2a07f6e7500bc60ca6e3e3ec1c91",
    "base_nonce": "5c4d98150661b848853b547f",
    "exporter_secret": "a3b010d4994890e2c6968a36f64470d3c824c8f5029942feb11e7a74b2921922",
    "pt": "4265617574792069732074727574682c20747275746820626561757479",
    "encryptions": [
        (0, "436f756e742d30", "5c4d98150661b848853b547f",
         "1c5250d8034ec2b784ba2cfd69dbdb8af406cfe3ff938e131f0def8c8b60b4db21993c62ce81883d2dd1b51a28"),
        (1, "436f756e742d31", "5c4d98150661b848853b547e",
         "6b53c051e4199c518de79594e1c4ab18b96f081549d45ce015be002090bb119e85285337cc95ba5f59992dc98c"),
        (2, "436f756e742d32", "5c4d98150661b848853b547d",
         "71146bd6795ccc9c49ce25dda112a48f202ad220559502cef1f34271e0cb4b02b4f10ecac6f48c32f878fae86b"),
        (4, "436f756e742d34", "5c4d98150661b848853b547b",
         "63357a2aa291f5a4e5f27db6baa2af8cf77427c7c1a909e0b37214dd47db122bb153495ff0b02e9e54a50dbe16"),
        (255, "436f756e742d323535", "5c4d98150661b848853b5480",
         "18ab939d63ddec9f6ac2b60d61d36a7375d2070c9b683861110757062c52b8880a5f6b3936da9cd6c23ef2a95c"),
        (256, "436f756e742d323536", "5c4d98150661b848853b557f",
         "7a4a13e9ef23978e2c520fd4d2e757514ae160cd0cd05e556ef692370ca53076214c0c40d4c728d6ed9e727a5b"),
    ],
    "exports": [
        ("", "4bbd6243b8bb54cec311fac9df81841b6fd61f56538a775e7c80a9f40160606e"),
        ("00", "8c1df14732580e5501b00f82b10a1647b40713191b7c1240ac80e2b68808ba69"),
        ("54657374436f6e74657874", "5acb09211139c43b3090489a9da433e8a30ee7188ba8b0a9a1ccf0c229283e53"),
    ],
}


def h(x: str) -> bytes:
    return bytes.fromhex(x)


def test_rfc9180_derive_key_pair():
    sk_e = e2e.derive_key_pair(h(RFC["ikmE"]))
    sk_r = e2e.derive_key_pair(h(RFC["ikmR"]))
    assert e2e._priv_bytes(sk_e).hex() == RFC["skEm"]
    assert e2e._pub_bytes(sk_e.public_key()).hex() == RFC["pkEm"]
    assert e2e._priv_bytes(sk_r).hex() == RFC["skRm"]
    assert e2e._pub_bytes(sk_r.public_key()).hex() == RFC["pkRm"]


def test_rfc9180_sender_and_recipient_contexts():
    enc, s = e2e.setup_base_s(h(RFC["pkRm"]), h(RFC["info"]), ikm_e=h(RFC["ikmE"]))
    assert enc.hex() == RFC["enc"]
    assert s.shared_secret.hex() == RFC["shared_secret"]
    assert s.schedule.key_schedule_context.hex() == RFC["key_schedule_context"]
    assert s.schedule.secret.hex() == RFC["secret"]
    assert s.schedule.key.hex() == RFC["key"]
    assert s.schedule.base_nonce.hex() == RFC["base_nonce"]
    assert s.schedule.exporter_secret.hex() == RFC["exporter_secret"]

    r = e2e.setup_base_r(enc, e2e.derive_key_pair(h(RFC["ikmR"])), h(RFC["info"]))
    assert r.shared_secret == s.shared_secret
    for seq, aad, nonce, ct in RFC["encryptions"]:
        s.seq = r.seq = seq
        assert s._nonce().hex() == nonce
        assert s.seal(h(aad), h(RFC["pt"])).hex() == ct
        assert r.open(h(aad), h(ct)) == h(RFC["pt"])
    for ctx_hex, value in RFC["exports"]:
        assert s.export(h(ctx_hex), 32).hex() == value
        assert r.export(h(ctx_hex), 32).hex() == value


def test_rfc9180_open_fails_on_wrong_aad_or_order():
    enc, s = e2e.setup_base_s(h(RFC["pkRm"]), h(RFC["info"]), ikm_e=h(RFC["ikmE"]))
    r = e2e.setup_base_r(enc, e2e.derive_key_pair(h(RFC["ikmR"])), h(RFC["info"]))
    ct0 = s.seal(b"a", b"x")
    ct1 = s.seal(b"a", b"y")
    with pytest.raises(e2e.HpkeError):
        r.open(b"a", ct1)                # out of order
    assert r.seq == 0
    with pytest.raises(e2e.HpkeError):
        r.open(b"b", ct0)                # wrong aad
    assert r.open(b"a", ct0) == b"x"


def test_cross_check_with_cryptography_single_shot_hpke():
    """cryptography's own (Rust/OpenSSL) HPKE: single-shot = SetupBase + one Seal with empty AAD; output enc || ct."""
    suite = crypto_hpke.Suite(crypto_hpke.KEM.X25519, crypto_hpke.KDF.HKDF_SHA256, crypto_hpke.AEAD.CHACHA20_POLY1305)
    sk_r = e2e.derive_key_pair(h(RFC["ikmR"]))
    enc, s = e2e.setup_base_s(sk_r.public_key(), b"kelo info")
    assert suite.decrypt(enc + s.seal(b"", b"hello"), sk_r, info=b"kelo info") == b"hello"
    blob = suite.encrypt(b"hello back", sk_r.public_key(), info=b"kelo info")
    r = e2e.setup_base_r(blob[:32], sk_r, b"kelo info")
    assert r.open(b"", blob[32:]) == b"hello back"


def test_low_order_point_is_refused():
    sk_r = e2e.derive_key_pair(h(RFC["ikmR"]))
    with pytest.raises(e2e.HpkeError):
        e2e.setup_base_r(bytes(32), sk_r, b"")


# --------------------------------------------------------------------------------------------------------------------------------
# docs/security/e2e-vectors.json
# --------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vectors():
    return json.loads(e2e_vectors_gen.VECTORS.read_text(encoding="utf-8"))


def _server(vectors) -> e2e.E2EServer:
    return e2e.E2EServer(e2e.ServerKey.from_private_bytes(h(vectors["server"]["sk"])))


def test_vector_file_is_current(vectors):
    """The file is exactly what the generator makes: a format change must regenerate it (and tell the app agents)."""
    assert e2e_vectors_gen.dumps(e2e_vectors_gen.build()) == e2e_vectors_gen.VECTORS.read_text(encoding="utf-8")


def test_vector_file_matches_its_pinned_sha256():
    """The apps check this same pin before they use the file; the pin must be the file's hash (regenerate with --write)."""
    text = e2e_vectors_gen.VECTORS.read_text(encoding="utf-8")
    assert e2e_vectors_gen.SHA_FILE.read_text(encoding="utf-8") == e2e_vectors_gen.sha_line(text)


def test_vector_check_command_passes():
    """`e2e_vectors_gen.py --check` (the CI step) exits 0 on the repository's files."""
    assert e2e_vectors_gen.main(["--check"]) == 0


def test_vector_server_key(vectors):
    v = vectors["server"]
    key = e2e.ServerKey(e2e.derive_key_pair(h(v["ikm"])))
    assert key.public_bytes.hex() == v["pk"]
    assert key.kid_hex == v["kid"] and len(v["kid"]) == 16
    assert key.info.decode() == v["info_text"] == "kelo-e2e v1 " + v["kid"]
    assert key.fingerprint == v["fingerprint"]
    assert len(v["fingerprint"].replace("-", "")) == 24 and all(len(g) == 4 for g in v["fingerprint"].split("-"))
    assert e2e.b64url_decode(v["pk_b64url"]) == key.public_bytes
    assert key.public_info() == v["e2e_info"]
    assert 16 <= len(v["pair_code"]) <= 128 and f"#pair={v['pair_code']}&k={v['pk_b64url']}" in v["pair_link"]
    assert "sk" not in repr(key) and v["sk"] not in repr(key)


def test_vector_requests_open_on_the_server(vectors):
    server = _server(vectors)
    for v in vectors["requests"]:
        opened = server.open_request(h(v["envelope"]), now=v["server_now"])
        assert opened.inner.encode().hex() == v["inner"]
        assert opened.inner.head_json().decode() == v["head_json"]
        assert opened.inner.body.decode() == v["body_text"]
        assert opened.ctx.shared_secret.hex() == v["shared_secret"]
        assert opened.response_key().hex() == v["response_key"]
        assert opened.ctx.export(e2e.LABEL_WS_C2S, 32).hex() == v["ws_c2s_key"]
        assert opened.ctx.export(e2e.LABEL_WS_S2C, 32).hex() == v["ws_s2c_key"]


def test_vector_requests_seal_from_inner_bytes(vectors):
    """What an app with a fixed-ephemeral API checks: seal `inner` with sk_e and get `ct` / `envelope`."""
    pk = h(vectors["server"]["pk"])
    for v in vectors["requests"]:
        enc, ctx = e2e.setup_base_s(pk, h(vectors["server"]["info"]), ikm_e=h(v["ikm_e"]))
        assert enc.hex() == v["enc"] == e2e._pub_bytes(e2e.derive_key_pair(h(v["ikm_e"])).public_key()).hex()
        assert e2e._priv_bytes(e2e.derive_key_pair(h(v["ikm_e"]))).hex() == v["sk_e"]
        assert ctx.schedule.key.hex() == v["key"] and ctx.schedule.base_nonce.hex() == v["base_nonce"]
        assert ctx.schedule.exporter_secret.hex() == v["exporter_secret"]
        ct = ctx.seal(h(v["aad"]), h(v["inner"]))
        assert ct.hex() == v["ct"]
        assert e2e.encode_envelope(h(vectors["server"]["kid"]), enc, ct).hex() == v["envelope"]


def test_vector_responses(vectors):
    for r in vectors["responses"]:
        key = h(r["key"])
        sealer = e2e.FrameSealer(key)
        for f in r["frames"]:
            assert sealer.seal(h(f["pt"]), last=f["flag"] == 1).hex() == f["frame"]
        opener = e2e.FrameOpener(key)
        got = opener.feed(h(r["stream"]))
        opener.close()
        assert [g.hex() for g in got] == [f["pt"] for f in r["frames"]]
        assert opener.head == json.loads(r["frames"][0]["pt_text"])
        # byte by byte gives the same
        opener = e2e.FrameOpener(key)
        got = [p for b in h(r["stream"]) for p in opener.feed(bytes([b]))]
        opener.close()
        assert [g.hex() for g in got] == [f["pt"] for f in r["frames"]]


def test_vector_websocket(vectors):
    req = next(v for v in vectors["requests"] if v["name"] == vectors["websocket"]["request"])
    client_ch = e2e.WsChannel(send_key=h(req["ws_c2s_key"]), recv_key=h(req["ws_s2c_key"]))
    server_ch = e2e.WsChannel(send_key=h(req["ws_s2c_key"]), recv_key=h(req["ws_c2s_key"]))
    ws = vectors["websocket"]
    assert client_ch.seal(ws["c2s_first"]["payload_text"]).hex() == ws["c2s_first"]["message"]
    assert server_ch.open(h(ws["c2s_first"]["message"])) == ws["c2s_first"]["payload_text"]
    assert server_ch.seal(ws["s2c_first"]["payload_text"]).hex() == ws["s2c_first"]["message"]
    assert server_ch.seal(h(ws["s2c_second"]["payload"])).hex() == ws["s2c_second"]["message"]
    assert client_ch.open(h(ws["s2c_first"]["message"])) == ws["s2c_first"]["payload_text"]
    assert client_ch.open(h(ws["s2c_second"]["message"])) == h(ws["s2c_second"]["payload"])
    for m in ("c2s_first", "s2c_first", "s2c_second"):
        assert h(ws[m]["pt"])[0] == ws[m]["type"]


def test_vector_stream_request(vectors):
    server = _server(vectors)
    for v in vectors["stream_requests"]:
        opened = server.open_request(h(v["envelope"]), now=v["server_now"])
        assert opened.inner.body.decode() == v["body_text"]
        assert opened.inner.head_json().decode() == v["head_json"]
        assert opened.response_key().hex() == v["response_key"]
        assert opened.ctx.export(e2e.LABEL_REQ_BODY, 32).hex() == v["body_key"]
        # sealing side, byte for byte
        enc, ctx = e2e.setup_base_s(h(vectors["server"]["pk"]), h(vectors["server"]["info"]), ikm_e=h(v["ikm_e"]))
        ct = ctx.seal(e2e.AAD_REQUEST, h(v["inner"]))
        assert ct.hex() == v["ct"] and e2e.encode_stream_head(h(vectors["server"]["kid"]), enc, ct).hex() == v["head"]
        sealer = e2e.FrameSealer(h(v["body_key"]), aad=e2e.AAD_REQUEST)
        assert [sealer.seal(h(f["pt"]), last=f["flag"] == 1).hex() for f in v["body_frames"]] == [f["frame"] for f in v["body_frames"]]
        # the streaming server path: head first, then frames fed piece by piece
        s2 = _server(vectors)
        head = h(v["head"])
        assert e2e.stream_head_length(head[:e2e.STREAM_HEAD_FIXED]) == len(head)
        o2 = s2.open_stream_head(head, now=v["server_now"])
        reader = o2.body_opener()
        got = [p for b in h(v["envelope"])[len(head):] for p in reader.feed(bytes([b]))]
        reader.close()
        assert b"".join(got).decode() == v["body_text"]


ERRORS = {c.code: c for c in (e2e.BadFrame, e2e.Truncated, e2e.UnknownKid, e2e.Replayed, e2e.StaleTimestamp, e2e.BadEnvelope)}


def test_vector_negative_cases(vectors):
    assert {n["name"] for n in vectors["negative"]} >= {"tampered_frame", "truncated_stream", "wrong_kid", "replayed_nonce",
                                                         "old_timestamp"}
    for n in vectors["negative"]:
        exc = ERRORS[n["expect"]]
        if n["kind"] == "response":
            opener = e2e.FrameOpener(h(n["key"]))
            with pytest.raises(exc):
                opener.feed(h(n["stream"]))
                opener.close()
        elif n["kind"] == "request":
            server = _server(vectors)
            for _ in range(n.get("repeat", 1) - 1):
                server.open_request(h(n["envelope"]), now=n["server_now"])
            with pytest.raises(exc):
                server.open_request(h(n["envelope"]), now=n["server_now"])
        elif n["kind"] == "ws_c2s":
            assert n["name"] in ("ws_tampered", "ws_unknown_type")
            ch = e2e.WsChannel(send_key=bytes(32), recv_key=h(n["key"]))
            with pytest.raises(exc):
                ch.open(h(n["message"]))
        else:
            raise AssertionError(n["kind"])


# --------------------------------------------------------------------------------------------------------------------------------
# Server key file, envelope parsing, replay, frames
# --------------------------------------------------------------------------------------------------------------------------------

def test_server_key_file(tmp_path):
    k1 = e2e.ServerKey.load_or_create(tmp_path)
    path = tmp_path / "e2e" / "server.key"
    assert path.exists() and len(path.read_bytes()) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    k2 = e2e.ServerKey.load_or_create(tmp_path)
    assert k2.public_bytes == k1.public_bytes and k2.kid == k1.kid
    os.chmod(path, 0o644)
    e2e.ServerKey.load_or_create(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    k3 = e2e.ServerKey.rotate(tmp_path)
    assert k3.kid != k1.kid and e2e.ServerKey.load_or_create(tmp_path).kid == k3.kid
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_bytes(b"short")
    with pytest.raises(RuntimeError):
        e2e.ServerKey.load_or_create(tmp_path)


def test_round_trip_and_header_filter(tmp_path):
    key = e2e.ServerKey.load_or_create(tmp_path)
    server = e2e.E2EServer(key)
    client = E2EClient(key.public_bytes)
    sealed = client.seal("post", "/api/x?a=1", {"Authorization": "Bearer t", "Cookie": "c=1", "X-Kelo-Device": "d",
                                                "Host": "evil"}, b"\x00body")
    # the server drops headers outside the list even when a client put them in
    inner = sealed.inner
    inner.headers["cookie"] = "c=1"
    enc, ctx = e2e.setup_base_s(key.public_bytes, key.info)
    opened = server.open_request(e2e.encode_envelope(key.kid, enc, ctx.seal(e2e.AAD_REQUEST, inner.encode())))
    assert opened.inner.method == "POST" and opened.inner.path == "/api/x?a=1" and opened.inner.body == b"\x00body"
    assert opened.inner.headers == {"authorization": "Bearer t", "x-kelo-device": "d"}
    # the answer goes back to the client that sealed it
    sealer = opened.response_sealer()
    stream = sealer.seal(e2e.response_head(201, [("Content-Type", "application/json"), ("Set-Cookie", "no"), ("Connection", "close"),
                                                 ("Transfer-Encoding", "chunked"), ("Proxy-Authenticate", "x"),
                                                 ("X-Frame-Seq", "3"), ("Vary", "a"), ("Vary", "b")]))
    stream += b"".join(sealer.seal_chunks(b"x" * (e2e.MAX_FRAME_PLAINTEXT * 2 + 5), last=True))
    head, body = e2e.open_response(ctx.export(e2e.LABEL_RESPONSE, 32), stream)
    assert head == {"s": 201, "h": {"content-type": "application/json", "x-frame-seq": "3", "vary": "a, b"}}
    assert body == b"x" * (e2e.MAX_FRAME_PLAINTEXT * 2 + 5)
    assert sealer.counter == 4 and sealer.finished
    with pytest.raises(e2e.E2EError):
        sealer.seal(b"more")


@pytest.mark.parametrize("head", [
    b"not json",
    json.dumps([1]).encode(),
    json.dumps({"m": "GET", "p": "api", "h": {}, "ts": 1, "n": e2e.b64url(bytes(16))}).encode(),
    json.dumps({"m": "GET", "p": "//evil.example/x", "h": {}, "ts": 1, "n": e2e.b64url(bytes(16))}).encode(),
    json.dumps({"m": "GET", "p": "/a b", "h": {}, "ts": 1, "n": e2e.b64url(bytes(16))}).encode(),
    json.dumps({"m": "G T", "p": "/a", "h": {}, "ts": 1, "n": e2e.b64url(bytes(16))}).encode(),
    json.dumps({"m": "GET", "p": "/a", "h": {"a": 1}, "ts": 1, "n": e2e.b64url(bytes(16))}).encode(),
    json.dumps({"m": "GET", "p": "/a", "h": {}, "ts": "1", "n": e2e.b64url(bytes(16))}).encode(),
    json.dumps({"m": "GET", "p": "/a", "h": {}, "ts": True, "n": e2e.b64url(bytes(16))}).encode(),
    json.dumps({"m": "GET", "p": "/a", "h": {}, "ts": 1, "n": e2e.b64url(bytes(15))}).encode(),
    json.dumps({"m": "GET", "p": "/a", "h": {}, "ts": 1, "n": e2e.b64url(bytes(16)) + "=="}).encode(),
])
def test_bad_inner_heads(head):
    with pytest.raises(e2e.BadEnvelope):
        e2e.decode_inner(len(head).to_bytes(4, "big") + head)


def test_bad_inner_lengths():
    for data in (b"", b"\x00\x00\x00", b"\x00\x00\x00\x09{}", (e2e.MAX_HEAD_LEN + 1).to_bytes(4, "big") + b"{}"):
        with pytest.raises(e2e.BadEnvelope):
            e2e.decode_inner(data)
    with pytest.raises(e2e.BadEnvelope):
        e2e.split_envelope(b"\x01" + bytes(20))


def test_clock_window_and_replay(tmp_path):
    key = e2e.ServerKey.load_or_create(tmp_path)
    server = e2e.E2EServer(key)
    client = E2EClient(key.public_bytes)
    now = 2_000_000_000
    for ts, ok in ((now - 300, True), (now + 300, True), (now - 301, False), (now + 301, False)):
        sealed = client.seal("GET", "/api/me", ts=ts)
        if ok:
            server.open_request(sealed.envelope, now=now)
        else:
            with pytest.raises(e2e.StaleTimestamp) as ei:
                server.open_request(sealed.envelope, now=now)
            # the context exists: the server can answer with a sealed error the client opens
            sealer = ei.value.opened.response_sealer()
            head, _ = e2e.open_response(sealed.response_key(), sealer.seal(e2e.response_head(400), last=True))
            assert head["s"] == 400
    # the same nonce in a fresh envelope (new enc) is still a replay
    nonce = bytes(range(16))
    server.open_request(client.seal("GET", "/api/me", ts=now, nonce=nonce).envelope, now=now)
    with pytest.raises(e2e.Replayed):
        server.open_request(client.seal("GET", "/api/me", ts=now + 290, nonce=nonce).envelope, now=now + 299)


def test_replay_cache_expiry_and_bound():
    cache = e2e.ReplayCache(window=600, max_entries=3)
    assert cache.check_and_add(b"a", now=0)
    assert not cache.check_and_add(b"a", now=599)
    assert cache.check_and_add(b"a", now=600)      # expired, remembered again
    for n in (b"b", b"c", b"d"):
        assert cache.check_and_add(n, now=601)
    assert len(cache) == 3                          # the oldest ("a") was pushed out
    assert cache.check_and_add(b"a", now=602)


def test_frame_opener_errors():
    key = bytes(range(32))
    sealer = e2e.FrameSealer(key)
    head = sealer.seal(e2e.response_head(200))
    # frame 0 that is not a head
    s2 = e2e.FrameSealer(key)
    with pytest.raises(e2e.BadFrame):
        e2e.FrameOpener(key).feed(s2.seal(b"plain body", last=True))
    # a length outside the limits
    with pytest.raises(e2e.BadFrame):
        e2e.FrameOpener(key).feed((e2e.MAX_FRAME_CIPHERTEXT + 1).to_bytes(4, "big"))
    with pytest.raises(e2e.BadFrame):
        e2e.FrameOpener(key).feed((3).to_bytes(4, "big") + b"abc")
    # wrong key
    with pytest.raises(e2e.BadFrame):
        e2e.FrameOpener(bytes(32)).feed(head)
    # once failed, always failed
    o = e2e.FrameOpener(bytes(32))
    with pytest.raises(e2e.BadFrame):
        o.feed(head)
    with pytest.raises(e2e.BadFrame):
        o.feed(b"")
    with pytest.raises(e2e.E2EError):
        sealer.seal(b"x" * (e2e.MAX_FRAME_PLAINTEXT + 1))
    # an empty stream is truncated
    with pytest.raises(e2e.Truncated):
        e2e.FrameOpener(key).close()


def test_ws_counters_are_per_direction():
    _, ctx = e2e.setup_base_s(e2e.derive_key_pair(b"k" * 32).public_key(), b"info")
    client = e2e.WsChannel.client(ctx)
    server = e2e.WsChannel(send_key=ctx.export(e2e.LABEL_WS_S2C, 32), recv_key=ctx.export(e2e.LABEL_WS_C2S, 32))
    msgs = [client.seal(b"m%d" % i) for i in range(3)]
    assert server.seal(b"s0") and server.send_counter == 1
    assert [server.open(m) for m in msgs] == [b"m0", b"m1", b"m2"]
    assert server.open(client.seal("text é")) == "text é"
    with pytest.raises(e2e.BadFrame):
        server.open(msgs[0])               # replayed message (old counter)
    # a c2s message never opens as s2c
    with pytest.raises(e2e.BadFrame):
        e2e.WsChannel(send_key=bytes(32), recv_key=ctx.export(e2e.LABEL_WS_S2C, 32)).open(client.seal(b"x"))


def test_reference_client_over_httpx(tmp_path):
    """server/tests/e2e_client.py against a stand-in /e2e handler built only from e2e.py (the real one comes with the wiring)."""
    key = e2e.ServerKey.load_or_create(tmp_path)
    server = e2e.E2EServer(key)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/e2e" and "authorization" not in request.headers
        try:
            opened = server.open_request(request.content)
        except e2e.UnknownKid:
            return httpx.Response(409, json={"code": "e2e_key_changed"})
        except e2e.E2EError:
            return httpx.Response(400)
        sealer = opened.response_sealer()
        body = json.dumps({"you_sent": opened.inner.body.decode(), "auth": opened.inner.headers.get("authorization")}).encode()
        out = sealer.seal(e2e.response_head(200, {"content-type": "application/json"})) + b"".join(sealer.seal_chunks(body, True))
        return httpx.Response(200, content=out, headers={"content-type": e2e.CONTENT_TYPE, "cache-control": "no-store"})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://kelo.test") as http:
        client = E2EClient(key.public_bytes)
        head, body = client.request(http, "POST", "/api/talk", {"authorization": "Bearer t"}, b"hi")
        assert head["s"] == 200 and json.loads(body) == {"you_sent": "hi", "auth": "Bearer t"}
        with pytest.raises(e2e.UnknownKid):
            client.request(http, "GET", "/api/me", kid=bytes(8))


def test_stream_request_errors(tmp_path):
    key = e2e.ServerKey.load_or_create(tmp_path)
    server = e2e.E2EServer(key)
    client = E2EClient(key.public_bytes)
    sealed, frames = client.seal_stream("PUT", "/api/uploads/x", body=b"y" * (e2e.MAX_FRAME_PLAINTEXT + 10))
    assert len(frames) == 2
    assert server.open_request(sealed.envelope + b"".join(frames)).inner.body == b"y" * (e2e.MAX_FRAME_PLAINTEXT + 10)
    # an empty body still sends one empty last frame
    sealed, frames = client.seal_stream("POST", "/api/x")
    assert len(frames) == 1 and server.open_request(sealed.envelope + frames[0]).inner.body == b""
    # the head passed with body bytes, a head with a body inside ct, a head too short
    with pytest.raises(e2e.BadEnvelope):
        server.open_stream_head(sealed.envelope + frames[0])
    with pytest.raises(e2e.BadEnvelope):
        e2e.stream_head_length(b"\x01" + bytes(e2e.STREAM_HEAD_FIXED))
    assert e2e.stream_head_length(sealed.envelope[:10]) is None
    inner = e2e.InnerRequest("POST", "/api/x", {}, int(__import__("time").time()), e2e.new_nonce(), b"body")
    enc, ctx = e2e.setup_base_s(key.public_bytes, key.info)
    with pytest.raises(e2e.BadEnvelope):
        server.open_stream_head(e2e.encode_stream_head(key.kid, enc, ctx.seal(e2e.AAD_REQUEST, inner.encode())))
