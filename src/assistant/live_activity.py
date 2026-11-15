"""iPhone Live Activities for a running reply (backlog 64b): the lock screen and the Dynamic Island show the step Kelo is on, the site
and a Stop button while the app is away. iOS stops an app soon after it leaves the screen, so this server updates the activity through
Apple's push service (APNs push type "liveactivity"; the operator's broker sends it for copies without the APNs key, push.py).
Nothing readable travels through Apple or the broker: the phone makes a fresh 32-byte key per activity and gives it to its own server
with the activity's push token (POST /api/live-activity, over the app's usual connection), and every update carries only
{"v": 1, "n": <steps so far>, "sealed": base64(nonce ‖ ChaCha20-Poly1305(key, nonce, aad = "kelo-live v1", JSON {"step", "site",
"state"}))}; the phone's widget extension opens it with the same key. state: working | waiting (an approval waits) | done | failed |
stopped. The key and the token live only in this process's memory and are dropped when the turn ends (a restart drops them too: the
phone's activity then goes stale and the app ends it the next time it opens).

LIVE ACTIVITY API (under the app's bearer token):
POST   /api/live-activity            {"turn_id", "token" (hex APNs push token of the activity), "environment": "production" |
                                       "development", "key" (base64 of 32 bytes)} -> {"ok": true}; 404 unknown or finished turn;
                                       422 a bad token or key; 404 also when this server cannot send iPhone pushes at all
DELETE /api/live-activity/{turn_id}  -> {"ok": true}   the phone ended the activity itself
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

log = logging.getLogger("uvicorn.error")

AAD = b"kelo-live v1"
EVERY = 5.0                    # at most one update every 5 s (Apple budgets Live Activity pushes)
STEP_MAX, SITE_MAX = 80, 60
TOKEN_RE = re.compile(r"^[0-9a-fA-F]{64,200}$")
TOOL_STARTED = "tool_started"  # the turn event that starts a tool call (server.py events contract)

Sender = Callable[[str, str, dict[str, Any], str, int], Awaitable[dict[str, Any]]]   # (token, environment, content, event, timestamp)


def seal(key: bytes, plain: dict[str, Any]) -> str:
    nonce = os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(nonce, json.dumps(plain, ensure_ascii=False, separators=(",", ":")).encode(), AAD)
    return base64.b64encode(nonce + ct).decode()


def state_of(turn: Any) -> dict[str, str]:
    """What the lock screen shows now: the turn's live line, the site of its newest browser picture, and how it stands."""
    step = (turn.status_text or "")[:STEP_MAX]
    site = next((str(e.get("host") or "") for e in reversed(turn.events) if e.get("type") == "browser" and e.get("host")), "")[:SITE_MAX]
    if turn.ended.is_set():
        state = {"done": "done", "failed": "failed"}.get(turn.final_status, "stopped")
    elif turn.waiting:
        state = "waiting"
    else:
        state = "working"
    return {"step": step, "site": site, "state": state}


def steps_of(turn: Any) -> int:
    return sum(1 for e in turn.events if e.get("type") == TOOL_STARTED and not e.get("hidden"))


class LiveActivities:
    def __init__(self, send: Sender, *, clock: Callable[[], float] = time.time, every: float = EVERY) -> None:
        self.send, self.clock, self.every = send, clock, every
        self.items: dict[str, dict[str, Any]] = {}       # turn id -> {"token", "env", "key", "task"}

    def register(self, turn: Any, token: str, env: str, key_b64: str) -> None:
        """ValueError for a bad token or key."""
        token = (token or "").strip()
        if not TOKEN_RE.match(token):
            raise ValueError("token: the activity's APNs push token in hex")
        try:
            key = base64.b64decode(key_b64 or "", validate=True)
        except (ValueError, TypeError):
            raise ValueError("key: base64 of 32 bytes") from None
        if len(key) != 32:
            raise ValueError("key: base64 of 32 bytes")
        self.forget(turn.id)
        item: dict[str, Any] = {"token": token.lower(), "env": "development" if env == "development" else "production", "key": key}
        item["task"] = asyncio.create_task(self._follow(turn, item))
        self.items[turn.id] = item

    def forget(self, turn_id: str) -> None:
        old = self.items.pop(turn_id, None)
        if old and old.get("task") and not old["task"].done():
            old["task"].cancel()

    async def _follow(self, turn: Any, item: dict[str, Any]) -> None:
        last: tuple[dict[str, str], int] | None = None
        try:
            while not turn.ended.is_set():
                # the lock screen follows this reply: it counts as read, so the reply is not stopped for want of a reader while the
                # iPhone app sleeps in the background (TurnRegistry.sweep, TURN_IDLE_SECONDS)
                turn.touched = time.monotonic()
                now = (state_of(turn), steps_of(turn))
                if now != last:
                    last = now
                    if not await self._push(item, "update", *now):
                        return
                try:
                    await asyncio.wait_for(turn.ended.wait(), timeout=self.every)
                except asyncio.TimeoutError:
                    pass
            await self._push(item, "end", state_of(turn), steps_of(turn))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("live activity of turn %s", turn.id)
        finally:
            if self.items.get(turn.id) is item:
                self.items.pop(turn.id, None)

    async def _push(self, item: dict[str, Any], event: str, plain: dict[str, str], n: int) -> bool:
        """False when the token is gone (the activity was dismissed or the app removed): stop following."""
        content = {"v": 1, "n": n, "sealed": seal(item["key"], plain)}
        try:
            r = await self.send(item["token"], item["env"], content, event, int(self.clock()))
        except Exception as e:  # noqa: BLE001
            log.warning("live activity push failed: %s", str(e)[:80])
            return True
        return not r.get("invalid")

    async def close(self) -> None:
        for tid in list(self.items):
            self.forget(tid)
