"""Reference KE2E client (docs/security/e2e-and-phone-key.md, section 1.4): seals a request, opens the response stream.

Used by server/tests/test_e2e_core.py now and by the end-to-end tests once /e2e is wired. The apps' clients do the same steps;
docs/security/e2e-vectors.json holds fixed inputs and outputs for them.

    client = E2EClient(pk_bytes)                       # pinned from the QR's k= or GET /api/e2e/info
    sealed = client.seal("POST", "/api/talk", {"authorization": "Bearer …"}, b'{"text": "hi"}')
    # POST sealed.envelope to /e2e with Content-Type: application/x-kelo-e2e, then feed the answer:
    for piece in sealed.opener.feed(chunk): ...
    sealed.opener.close()                              # raises Truncated if the last frame never came
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from assistant import e2e


@dataclass
class SealedRequest:
    envelope: bytes
    inner: e2e.InnerRequest
    ctx: e2e.HpkeContext = field(repr=False)
    opener: e2e.FrameOpener = field(repr=False)

    def response_key(self) -> bytes:
        return self.ctx.export(e2e.LABEL_RESPONSE, 32)

    def ws_channel(self) -> e2e.WsChannel:
        return e2e.WsChannel.client(self.ctx)


class E2EClient:
    def __init__(self, pk: bytes):
        if len(pk) != e2e.N_PK:
            raise ValueError("server key must be 32 bytes")
        self.pk = pk
        self.kid = e2e.kid_of(pk)
        self.info = e2e.info_for(self.kid)
        self.fingerprint = e2e.fingerprint_of(pk)

    def seal(self, method: str, path: str, headers: dict[str, str] | None = None, body: bytes = b"", *,
             ts: int | None = None, nonce: bytes | None = None, ikm_e: bytes | None = None,
             kid: bytes | None = None) -> SealedRequest:
        """`ts`, `nonce`, `ikm_e` fix the otherwise random parts (vectors); `kid` overrides the envelope's kid (negative tests)."""
        inner = e2e.InnerRequest(method=method.upper(), path=path, headers=e2e.filter_inner_headers(headers or {}),
                                 ts=int(time.time()) if ts is None else ts, nonce=nonce or e2e.new_nonce(), body=body)
        enc, ctx = e2e.setup_base_s(self.pk, self.info, ikm_e)
        ct = ctx.seal(e2e.AAD_REQUEST, inner.encode())
        envelope = e2e.encode_envelope(kid if kid is not None else self.kid, enc, ct)
        return SealedRequest(envelope, inner, ctx, e2e.FrameOpener(ctx.export(e2e.LABEL_RESPONSE, 32)))

    def seal_stream(self, method: str, path: str, headers: dict[str, str] | None = None, body: bytes = b"", *,
                    ts: int | None = None, nonce: bytes | None = None, ikm_e: bytes | None = None,
                    chunk: int = e2e.MAX_FRAME_PLAINTEXT) -> tuple[SealedRequest, list[bytes]]:
        """A version 0x02 request: the envelope head (H sealed with an empty body) and the body as frames of at most `chunk`
        bytes (the last one flagged). `sealed.envelope` is the head alone; send head + frames."""
        inner = e2e.InnerRequest(method=method.upper(), path=path, headers=e2e.filter_inner_headers(headers or {}),
                                 ts=int(time.time()) if ts is None else ts, nonce=nonce or e2e.new_nonce(), body=b"")
        enc, ctx = e2e.setup_base_s(self.pk, self.info, ikm_e)
        head = e2e.encode_stream_head(self.kid, enc, ctx.seal(e2e.AAD_REQUEST, inner.encode()))
        sealer = e2e.FrameSealer(ctx.export(e2e.LABEL_REQ_BODY, 32), aad=e2e.AAD_REQUEST)
        pieces = [body[i:i + chunk] for i in range(0, len(body), chunk)] or [b""]
        frames = [sealer.seal(p, last=n == len(pieces) - 1) for n, p in enumerate(pieces)]
        inner.body = body
        return SealedRequest(head, inner, ctx, e2e.FrameOpener(ctx.export(e2e.LABEL_RESPONSE, 32))), frames

    def request(self, http, method: str, path: str, headers: dict[str, str] | None = None, body: bytes = b"",
                **kw) -> tuple[dict, bytes]:
        """Send through an httpx.Client (any transport) to /e2e and return (head, body). Raises on any KE2E error."""
        sealed = self.seal(method, path, headers, body, **kw)
        resp = http.post("/e2e", content=sealed.envelope, headers={"content-type": e2e.CONTENT_TYPE})
        if resp.status_code == 409:
            raise e2e.UnknownKid(resp.text)
        if resp.status_code != 200 or resp.headers.get("content-type", "").split(";")[0] != e2e.CONTENT_TYPE:
            raise e2e.BadEnvelope(f"outer status {resp.status_code}")
        frames = sealed.opener.feed(resp.content)
        sealed.opener.close()
        return sealed.opener.head or {}, b"".join(frames[1:])
