"""A local Kelo server for the Android app's KE2E end-to-end test (apps/android/tools/ke2e_local_test.sh).

Starts the real server app (src/assistant, a fake model that answers SECRET_REPLY) under uvicorn, behind a TCP proxy that records
every byte both ways (standing in for Cloudflare or anyone on the path, as tests/test_e2e_wire.py does), writes {"url", "owner_token",
"secret_reply"} to INFO_FILE, and runs until STOP_FILE appears. Then it checks the record: no token, no secret text of the app's test,
no request path other than the plaintext-on-purpose /api/e2e/info, and writes "ok" or the reasons to VERDICT_FILE.

    uv run python apps/android/tools/ke2e_local_server.py INFO_FILE STOP_FILE VERDICT_FILE
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "server" / "tests"), str(REPO / "server" / "src")]

from assistant.providers import Done, TextDelta  # noqa: E402
from assistant.server import Settings, create_app  # noqa: E402
from test_e2e_wire import Recorder  # noqa: E402
from test_server import TOKEN, Running  # noqa: E402

SECRET_REPLY = "bake the green apples into a crumble kelo-e2e-reply-5d1"
# what the Kotlin test sends (Ke2eLocalServerTest): none of it may cross the proxy in the clear
SECRETS = [TOKEN, "Bearer ", "kelo-e2e-goal-7f3a", "kelo-e2e-talk-91c2", SECRET_REPLY, "kelo-e2e-file-c4e2", '"code"', "Pixel KE2E"]


async def main(info_file: str, stop_file: str, verdict_file: str) -> None:
    async def reply(provider, request, **kw):
        yield TextDelta(SECRET_REPLY)
        yield Done("stop")

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(Path(tmp) / "state" / "s.db"),
                                  home_dir=str(home)), tools=[], stream_fn=reply)
        async with Running(app) as srv, Recorder(srv.port) as proxy:
            Path(info_file).write_text(json.dumps({"url": proxy.url, "owner_token": TOKEN, "secret_reply": SECRET_REPLY}))
            while not os.path.exists(stop_file):
                await asyncio.sleep(0.2)
            seen = bytes(proxy.seen)
    problems = [f"{s!r} crossed the path in the clear" for s in SECRETS if s.encode() in seen]
    plain_api = seen.count(b"/api/") - seen.count(b"/api/e2e/info")
    if plain_api:
        problems.append(f"{plain_api} plaintext /api/ path(s) other than /api/e2e/info")
    if b"POST /e2e " not in seen:
        problems.append("no POST /e2e was seen at all")
    Path(verdict_file).write_text("ok\n" if not problems else "\n".join(problems) + "\n")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
