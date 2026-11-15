"""Home Assistant live events (backlog 62): one websocket to the owner's Home Assistant, filtered to the entity watches the agent added
for the owner ("tell me when the front door opens / the washer finishes"); a match fires like a watcher hit (watchers.py): a notification
and a hidden handoff to the main agent. The socket is open only while the Home Assistant connector exists and a watch is on.

The connector is the self-hosted preset "home_assistant" (selfhosted.py): vault item `ITEM`, placement {"origin": "http://host:8123", ...},
secret = the long-lived access token. The token is read at each connect and never logged.

HA_WATCHES (table ha_watches): {"id", "entity_id", "to_state" ("" = any change of state), "name", "note", "once", "cooldown" (seconds),
"enabled", "last_fired", "fire_count", "created_at"}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any, Callable

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

log = logging.getLogger("assistant.ha_events")

ITEM = "home-assistant-api"
ENTITY_OK = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
MAX_WATCHES = 50
DEFAULT_COOLDOWN = 60
MAX_FRAME = 4 * 1024 * 1024
MAX_BACKOFF = 300.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS ha_watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL, to_state TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', once INTEGER NOT NULL DEFAULT 0, cooldown INTEGER NOT NULL DEFAULT 60,
    enabled INTEGER NOT NULL DEFAULT 1, last_fired REAL, fire_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
"""


class HaWatchError(ValueError):
    """Shown to the agent as it is."""


def socket_url(origin: str) -> str:
    """ws(s)://host:port/api/websocket for the connector's http(s) origin; ValueError for anything else."""
    o = (origin or "").strip().rstrip("/")
    if o.startswith("https://"):
        return "wss://" + o[len("https://"):] + "/api/websocket"
    if o.startswith("http://"):
        return "ws://" + o[len("http://"):] + "/api/websocket"
    raise ValueError("not an http(s) origin")


def matches(w: dict[str, Any], entity_id: str, old: str | None, new: str | None) -> bool:
    """A change of [entity_id] from [old] to [new] state fires watch [w]: same entity, the state really changed, and (when the watch names
    one) the new state is the one it waits for. Attribute-only updates (old == new) never fire."""
    if not w.get("enabled") or w.get("entity_id") != entity_id or new is None or old == new:
        return False
    want = str(w.get("to_state") or "")
    return not want or new == want


class HaEvents:
    def __init__(self, store: Any, *, connect: Callable[..., Any] | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self.store = store
        self._connect = connect or websockets.connect
        self._clock, self._sleep = clock, sleep
        self.on_fire: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None   # (watch, change): notification + handoff
        self.on_refused: Callable[[str], Any] | None = None                          # the token was refused: tell the owner once
        self._task: asyncio.Task[None] | None = None
        self._told_refused = False
        store._db.executescript(SCHEMA)
        store.credential_watchers.append(self._credential_changed)

    # ---- the watch list
    def _rows(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ha_watches" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY id"
        return [dict(r) for r in self.store._all(sql, ())]

    def list(self) -> list[dict[str, Any]]:
        return self._rows()

    def add(self, entity_id: str, name: str, *, to_state: str = "", note: str = "", once: bool = False, cooldown: int = DEFAULT_COOLDOWN) -> dict[str, Any]:
        entity_id = (entity_id or "").strip().lower()
        if not ENTITY_OK.match(entity_id):
            raise HaWatchError("entity_id must look like domain.object_id, e.g. binary_sensor.front_door (see GET /api/states)")
        if len(self._rows()) >= MAX_WATCHES:
            raise HaWatchError(f"at most {MAX_WATCHES} Home Assistant watches; remove one first")
        if self.store.credential(ITEM) is None:
            raise HaWatchError("Home Assistant is not connected; ask the user to connect it in Connectors first")
        wid = int(self.store._run(
            "INSERT INTO ha_watches(entity_id, to_state, name, note, once, cooldown, created_at) VALUES(?,?,?,?,?,?,?)",
            (entity_id, (to_state or "").strip()[:60], (name or entity_id).strip()[:80], (note or "").strip()[:400], int(bool(once)),
             max(0, min(int(cooldown), 86_400)), self._clock())).lastrowid)
        self.restart()
        return self.get(wid) or {}

    def get(self, wid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM ha_watches WHERE id = ?", (wid,))
        return dict(rows[0]) if rows else None

    def remove(self, wid: int) -> bool:
        n = self.store._run("DELETE FROM ha_watches WHERE id = ?", (wid,)).rowcount
        self.restart()
        return bool(n)

    # ---- the connection
    def wanted(self) -> bool:
        """The socket is open only while the connector exists and a watch is on."""
        return bool(self._rows(enabled_only=True)) and self.store.credential(ITEM) is not None

    def restart(self) -> None:
        """(Re)starts or stops the connection to match [wanted] (the loop must be running; tests call it from async code)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = loop.create_task(self._run()) if self.wanted() else None

    def _credential_changed(self, name: str, removed: bool) -> None:
        if name == ITEM:
            self._told_refused = False
            self.restart()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        backoff = 5.0
        while self.wanted():
            try:
                await self._session()
                backoff = 5.0                              # a clean close (HA restarted): try again soon
            except asyncio.CancelledError:
                raise
            except PermissionError:
                return                                     # the token was refused: wait for the owner to connect again
            except (OSError, asyncio.TimeoutError, WebSocketException, ConnectionClosed, ValueError) as e:
                log.info("Home Assistant events: %s; retrying in %.0fs", type(e).__name__, backoff)
            await self._sleep(backoff * (1 + random.random() * 0.2))
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def _session(self) -> None:
        cred = self.store.credential(ITEM) or {}
        url = socket_url(str((cred.get("placement") or {}).get("origin") or ""))
        token = str(cred.get("secret") or "")
        async with self._connect(url, max_size=MAX_FRAME, open_timeout=20) as ws:
            hello = json.loads(await ws.recv())
            if hello.get("type") != "auth_required":
                raise ValueError("not a Home Assistant socket")
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            answer = json.loads(await ws.recv())
            if answer.get("type") != "auth_ok":
                if not self._told_refused and self.on_refused:
                    self._told_refused = True
                    self.on_refused(str(answer.get("message") or "auth_invalid"))
                raise PermissionError("Home Assistant refused the token")
            await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "event":
                    self.handle(((msg.get("event") or {}).get("data") or {}))

    def handle(self, data: dict[str, Any]) -> list[int]:
        """One state_changed event's data: fires every watch it matches (cooldown and once kept). Returns the fired watch ids (tests)."""
        entity = str(data.get("entity_id") or "")
        old = (data.get("old_state") or {}).get("state") if data.get("old_state") else None
        new_state = data.get("new_state") or None
        new = new_state.get("state") if new_state else None
        fired: list[int] = []
        now = self._clock()
        for w in self._rows(enabled_only=True):
            if not matches(w, entity, old, new):
                continue
            if w.get("last_fired") and now - float(w["last_fired"]) < int(w.get("cooldown") or 0):
                continue
            self.store._run("UPDATE ha_watches SET last_fired = ?, fire_count = fire_count + 1, enabled = ? WHERE id = ?",
                            (now, 0 if w.get("once") else 1, w["id"]))
            fired.append(int(w["id"]))
            change = {"entity_id": entity, "old": old, "new": new,
                      "friendly_name": str(((new_state or {}).get("attributes") or {}).get("friendly_name") or entity)}
            if self.on_fire:
                try:
                    self.on_fire(self.get(int(w["id"])) or w, change)
                except Exception:  # noqa: BLE001 - one bad handler never stops the socket
                    log.exception("Home Assistant watch handler failed")
        if fired and not self._rows(enabled_only=True):
            self.restart()                                 # the last once-watch fired: nothing left to listen for
        return fired


def handoff_text(w: dict[str, Any], change: dict[str, Any]) -> str:
    """The hidden message the main agent gets when a Home Assistant watch fires."""
    lines = [f"[Home Assistant watch #{w['id']} \"{w['name']}\"] {change['friendly_name']} ({change['entity_id']}) changed from "
             f"{change.get('old') or '?'} to {change.get('new') or '?'} just now."]
    if w.get("note"):
        lines.append(f"What the user wanted when this happens: {w['note']}")
    lines.append("The user already got a short notification. Tell them in one short sentence, in their language, what happened; do only "
                 "what the note asks." + (" This watch is now off (once)." if w.get("once") else ""))
    return "\n".join(lines)
