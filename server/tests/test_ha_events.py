"""Tests for Home Assistant live events (ha_events.py and tools/ha_tools.py)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from assistant.ha_events import HaEvents, HaWatchError, handoff_text, matches, socket_url
from assistant.store import Store
from assistant.tools.ha_tools import ha_watch_tools


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


class FakeSocket:
    def __init__(self, incoming: list[dict[str, Any]] | None = None) -> None:
        self.incoming: list[dict[str, Any]] = list(incoming or [])
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True

    async def recv(self) -> str:
        if not self.incoming:
            raise ConnectionClosed(None, None)
        item = self.incoming.pop(0)
        return json.dumps(item)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> str:
        if not self.incoming:
            raise StopAsyncIteration
        item = self.incoming.pop(0)
        return json.dumps(item)


def add_ha_connector(store: Store, origin: str = "http://192.168.0.10:8123", token: str = "test-token-xyz") -> None:
    store.save_credential("home-assistant-api", "Home Assistant", ["192.168.0.10"], {"origin": origin}, "")
    store.set_credential_secret("home-assistant-api", token)


def test_socket_url():
    assert socket_url("http://192.168.0.10:8123") == "ws://192.168.0.10:8123/api/websocket"
    assert socket_url("http://192.168.0.10:8123/") == "ws://192.168.0.10:8123/api/websocket"
    assert socket_url("https://ha.example.com") == "wss://ha.example.com/api/websocket"
    assert socket_url("https://ha.example.com/") == "wss://ha.example.com/api/websocket"
    with pytest.raises(ValueError, match="not an http"):
        socket_url("ftp://x")
    with pytest.raises(ValueError, match="not an http"):
        socket_url("")


def test_matches():
    w = {"enabled": 1, "entity_id": "binary_sensor.front_door", "to_state": ""}
    # the same entity with a real change and no to_state -> True
    assert matches(w, "binary_sensor.front_door", "off", "on") is True
    assert matches(w, "binary_sensor.front_door", None, "on") is True

    # to_state "on" with new "off" -> False
    w_on = {"enabled": 1, "entity_id": "binary_sensor.front_door", "to_state": "on"}
    assert matches(w_on, "binary_sensor.front_door", "off", "on") is True
    assert matches(w_on, "binary_sensor.front_door", "on", "off") is False

    # old == new (an attribute update) -> False
    assert matches(w, "binary_sensor.front_door", "on", "on") is False
    assert matches(w_on, "binary_sensor.front_door", "on", "on") is False

    # a disabled watch -> False
    w_disabled = {"enabled": 0, "entity_id": "binary_sensor.front_door", "to_state": ""}
    assert matches(w_disabled, "binary_sensor.front_door", "off", "on") is False

    # another entity -> False
    assert matches(w, "binary_sensor.back_door", "off", "on") is False

    # new is None -> False
    assert matches(w, "binary_sensor.front_door", "on", None) is False


def test_handle():
    store = Store()
    clock = Clock(1_000.0)
    add_ha_connector(store)
    ha = HaEvents(store, clock=clock)

    fired_events: list[tuple[dict[str, Any], dict[str, Any]]] = []
    ha.on_fire = lambda w, c: fired_events.append((w, c))

    w = ha.add("binary_sensor.front_door", "Front door", to_state="on", cooldown=60)
    wid = w["id"]

    ev_data = {
        "entity_id": "binary_sensor.front_door",
        "old_state": {"state": "off"},
        "new_state": {"state": "on", "attributes": {"friendly_name": "Front Door"}},
    }

    # a watch fires once
    fired = ha.handle(ev_data)
    assert fired == [wid]
    assert len(fired_events) == 1
    assert fired_events[0][0]["id"] == wid
    assert fired_events[0][1]["new"] == "on"
    assert fired_events[0][1]["friendly_name"] == "Front Door"

    # a second matching event inside the cooldown does not fire
    clock.advance(30.0)
    fired2 = ha.handle(ev_data)
    assert fired2 == []
    assert len(fired_events) == 1

    # after the cooldown (advance the fake clock) it fires again
    clock.advance(31.0)  # now 61s after first fire
    fired3 = ha.handle(ev_data)
    assert fired3 == [wid]
    assert len(fired_events) == 2

    # a `once` watch fires once and then is disabled
    fired_events.clear()
    w_once = ha.add("binary_sensor.washer", "Washer", to_state="off", once=True, cooldown=60)
    wid_once = w_once["id"]

    washer_ev = {
        "entity_id": "binary_sensor.washer",
        "old_state": {"state": "on"},
        "new_state": {"state": "off", "attributes": {"friendly_name": "Washer"}},
    }
    fired_once = ha.handle(washer_ev)
    assert fired_once == [wid_once]
    assert len(fired_events) == 1

    # check watch is disabled in db
    row = ha.get(wid_once)
    assert row is not None
    assert row["enabled"] == 0
    assert row["fire_count"] == 1

    # does not fire again even after cooldown
    clock.advance(100.0)
    assert ha.handle(washer_ev) == []
    assert len(fired_events) == 1


@pytest.mark.asyncio
async def test_session_auth_and_events(caplog):
    store = Store()
    token = "secret-token-xyz-12345"
    add_ha_connector(store, token=token)
    clock = Clock(1_000.0)

    frames = [
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {"id": 1, "type": "result", "success": True},
        {
            "id": 1,
            "type": "event",
            "event": {
                "event_type": "state_changed",
                "data": {
                    "entity_id": "binary_sensor.front_door",
                    "old_state": {"state": "off"},
                    "new_state": {"state": "on", "attributes": {"friendly_name": "Front Door"}},
                },
            },
        },
    ]

    fake_sock = FakeSocket(frames)

    def fake_connect(url: str, **kw: Any):
        assert url == "ws://192.168.0.10:8123/api/websocket"
        return fake_sock

    ha = HaEvents(store, connect=fake_connect, clock=clock)
    w = ha.add("binary_sensor.front_door", "Front door", to_state="on")

    received: list[tuple[dict[str, Any], dict[str, Any]]] = []
    ha.on_fire = lambda w_arg, c_arg: received.append((w_arg, c_arg))

    with caplog.at_level(logging.DEBUG):
        await ha._session()

    # on_fire callback got the watch and change.new == "on"
    assert len(received) == 1
    watch_got, change_got = received[0]
    assert watch_got["id"] == w["id"]
    assert change_got["new"] == "on"
    assert change_got["old"] == "off"
    assert change_got["entity_id"] == "binary_sensor.front_door"

    # token appears in the sent auth frame but in no log record
    assert len(fake_sock.sent) >= 2
    auth_frame = fake_sock.sent[0]
    assert auth_frame == {"type": "auth", "access_token": token}
    sub_frame = fake_sock.sent[1]
    assert sub_frame == {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}

    assert token not in caplog.text


@pytest.mark.asyncio
async def test_auth_invalid():
    store = Store()
    add_ha_connector(store, token="bad-token")
    clock = Clock(1_000.0)

    frames = [
        {"type": "auth_required"},
        {"type": "auth_invalid", "message": "Invalid access token"},
    ]
    fake_sock = FakeSocket(frames)

    def fake_connect(url: str, **kw: Any):
        return fake_sock

    ha = HaEvents(store, connect=fake_connect, clock=clock)
    ha.add("binary_sensor.front_door", "Front door")

    refused_reasons: list[str] = []
    ha.on_refused = lambda why: refused_reasons.append(why)

    # _run catches PermissionError and stops without reconnect loop
    await ha._run()

    assert refused_reasons == ["Invalid access token"]


def test_wanted():
    store = Store()
    ha = HaEvents(store)

    # False with no watches and no connector
    assert ha.wanted() is False

    # False with no watches even when connector exists
    add_ha_connector(store)
    assert ha.wanted() is False

    # True with a watch and the connector
    w = ha.add("binary_sensor.front_door", "Front door")
    assert ha.wanted() is True

    # False when connector is missing
    store.delete_credential("home-assistant-api")
    assert ha.wanted() is False


@pytest.mark.asyncio
async def test_ha_watch_add_validation_and_tools():
    store = Store()
    ha = HaEvents(store)
    tools = {t.name: t for t in ha_watch_tools(ha)}

    # Without connector, adding fails
    with pytest.raises(HaWatchError, match="Home Assistant is not connected"):
        ha.add("binary_sensor.front_door", "Front door")
    res = await tools["ha_watch_add"].handler({"entity_id": "binary_sensor.front_door", "name": "Front door"})
    assert "error: Home Assistant is not connected" in res

    # Bad entity_id refuses ("front door")
    add_ha_connector(store)
    with pytest.raises(HaWatchError, match="domain.object_id"):
        ha.add("front door", "Front door")
    res_bad = await tools["ha_watch_add"].handler({"entity_id": "front door", "name": "Front door"})
    assert "error: entity_id must look like domain.object_id" in res_bad

    # Works for "binary_sensor.front_door" when connector exists
    w = ha.add("binary_sensor.front_door", "Front door", to_state="on")
    assert w["entity_id"] == "binary_sensor.front_door"
    assert w["to_state"] == "on"

    res_tool = await tools["ha_watch_add"].handler({"entity_id": "binary_sensor.motion", "name": "Motion"})
    assert "Watching now (live, no polling):" in res_tool
    assert "binary_sensor.motion" in res_tool

    # List tool
    res_list = await tools["ha_watch_list"].handler({})
    assert "binary_sensor.front_door" in res_list
    assert "binary_sensor.motion" in res_list

    # Remove tool
    res_rm = await tools["ha_watch_remove"].handler({"id": f"#{w['id']}"})
    assert res_rm == "Removed."
    assert ha.get(w["id"]) is None


def test_handoff_text():
    w = {"id": 1, "name": "Front door", "note": "Lock the door", "once": 1}
    change = {"friendly_name": "Front Door", "entity_id": "binary_sensor.front_door", "old": "off", "new": "on"}
    text = handoff_text(w, change)
    assert '[Home Assistant watch #1 "Front door"] Front Door (binary_sensor.front_door) changed from off to on just now.' in text
    assert "What the user wanted when this happens: Lock the door" in text
    assert "This watch is now off (once)." in text


@pytest.mark.asyncio
async def test_server_create_app_integration(tmp_path):
    from assistant.server import Settings, create_app

    store = Store(tmp_path / "s.db")
    add_ha_connector(store)

    fake_sock = FakeSocket([{"type": "auth_required"}, {"type": "auth_ok"}])

    app = create_app(Settings(token="test-token", cdp_host=None, db_path=str(tmp_path / "s.db")), ha_connect=lambda url, **kw: fake_sock)
    assert hasattr(app.state, "ha_events")
    assert isinstance(app.state.ha_events, HaEvents)
