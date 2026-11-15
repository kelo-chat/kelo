#!/usr/bin/env python3
"""The iPhone app's KE2E client end to end against a real Kelo server process, with a recording proxy in the middle.

Starts the server (create_app with a scripted model, a temporary state folder) on a free port and a TCP proxy that keeps every
byte, makes a pairing link whose address is the proxy (the owner's call goes straight to the server, not through the proxy), then
runs the Swift suite KE2ELiveServerTests (ios/KeloCore) with KELO_E2E_LIVE pointing at a settings file. Afterwards it checks the
proxy's record: the only plaintext request lines are POST /e2e, GET /e2e/ws and GET /api/e2e/info, and no token, pairing code,
message, reply or file content crossed in the clear.

Run from the repository root (needs the server's environment):
    uv run python ios/tools/e2e_local.py [--scratch DIR]
Exit code 0 when the Swift suite passed and the record is clean.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

import httpx  # noqa: E402

from assistant.providers import Done, TextDelta  # noqa: E402
from assistant.server import Settings, create_app  # noqa: E402
from test_server import TOKEN, Running  # noqa: E402

TALK = "which green apples are best for a crumble tonight"
REPLY = "bramley apples, with cinnamon and oats"
FILE_MARKER = "file-secret-91c4e2::"
ALLOWED = {(b"POST", b"/e2e"), (b"GET", b"/e2e/ws"), (b"GET", b"/api/e2e/info")}
# request lines anywhere in the client's bytes (keep-alive connections carry many requests back to back)
REQUEST_LINE = re.compile(rb"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (/\S*) HTTP/1\.[01]\r\n")


class Recorder:
    """A TCP proxy that keeps every byte it passes, both ways (client->server bytes separately, for the request lines)."""

    def __init__(self, upstream_port: int):
        self.upstream_port = upstream_port
        self.up = bytearray()
        self.down = bytearray()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._conn, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _conn(self, reader, writer):
        up_r, up_w = await asyncio.open_connection("127.0.0.1", self.upstream_port)

        async def pipe(src, dst, record):
            try:
                while data := await src.read(65536):
                    record += data
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, up_w, self.up), pipe(up_r, writer, self.down))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", help="folder for the server state and the Swift build (default: a temporary folder, deleted)")
    args = ap.parse_args()
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="kelo-e2e-ios-"))
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        return await run(scratch)
    finally:
        if not args.scratch:
            shutil.rmtree(scratch, ignore_errors=True)


async def run(scratch: Path) -> int:
    async def reply(provider, request, **kw):
        yield TextDelta(REPLY)
        yield Done("stop")

    home = scratch / "home"
    home.mkdir(exist_ok=True)
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(scratch / "state" / "s.db"),
                              home_dir=str(home)), tools=[], stream_fn=reply)
    async with Running(app) as srv, Recorder(srv.port) as proxy:
        async with httpx.AsyncClient(base_url=srv.url, timeout=30) as owner:
            info = (await owner.get("/api/e2e/info")).json()
            # the owner's tool asks for a pairing link (straight to the server: only the phone's traffic goes through the proxy)
            r = await owner.post("/api/devices/pair", json={"origin": proxy.url}, headers={"Authorization": f"Bearer {TOKEN}"})
            r.raise_for_status()
            pair = r.json()
        out = scratch / "out.json"
        settings = scratch / "live.json"
        settings.write_text(json.dumps({"link": pair["link"], "fingerprint": info["fingerprint"], "talk": TALK, "reply": REPLY,
                                        "file_marker": FILE_MARKER, "out": str(out)}))
        env = dict(os.environ, KELO_E2E_LIVE=str(settings))
        proc = await asyncio.create_subprocess_exec(
            str(ROOT / "ios/KeloCore/test.sh"), "--scratch-path", str(scratch / "spm"), "--filter", "KE2ELiveServerTests",
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output, _ = await proc.communicate()
        text = output.decode(errors="replace")
        summary = [ln for ln in text.splitlines() if "Test run with" in ln or "✘" in ln or "error:" in ln]
        print("\n".join(summary[-20:]) or text[-3000:])
        ok = proc.returncode == 0 and "KE2ELiveServerTests" in text and "passed" in (summary[-1] if summary else "")

    # the record: what anyone on the path saw
    lines = [(m.group(1), m.group(2).split(b"?")[0]) for m in REQUEST_LINE.finditer(bytes(proxy.up))]
    bad_lines = sorted({f"{m.decode()} {p.decode()}" for m, p in lines if (m, p) not in ALLOWED})
    token = json.loads(out.read_text())["token"] if out.exists() else None
    secrets = {"device token": token, "owner token": TOKEN, "pairing code": pair["code"], "message": TALK, "reply": REPLY,
               "file content": FILE_MARKER}
    seen = bytes(proxy.up) + bytes(proxy.down)
    leaked = [name for name, value in secrets.items() if value and value.encode() in seen]
    print(f"proxy saw {len(lines)} requests: " + ", ".join(sorted({f'{m.decode()} {p.decode()}' for m, p in lines})))
    if not token:
        print("FAIL: the Swift suite did not report its device token (did it run?)")
        ok = False
    if bad_lines:
        print("FAIL: plaintext requests crossed the proxy: " + ", ".join(bad_lines))
        ok = False
    if leaked:
        print("FAIL: in the clear on the path: " + ", ".join(leaked))
        ok = False
    if not any(p == b"/e2e/ws" for _, p in lines):
        print("FAIL: the live view did not go through /e2e/ws")
        ok = False
    print("OK: only ciphertext crossed the path" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
