"""start_voice_call: the reply asks the user's open Kelo app to ring for a spoken call with Kelo (an in-app call screen, not a
phone call to anyone; phone_data.phone_call is the phone call tool)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from ..agent import Tool, emit_ui


def call_tools(current_turn: Callable[[], dict[str, Any] | None]) -> list[Tool]:
    async def start_voice_call(a: dict[str, Any]) -> str:
        turn = current_turn() or {}
        if not turn.get("turn_id") or turn.get("unattended"):
            return "Error: a call can only ring the Kelo app the user is writing from, during their message."
        if not turn.get("can_call"):
            return ("Error: the app the user is writing from cannot take a call (an older app, no microphone, or no speech model "
                    "for their language). Say so in one short line.")
        call_id = uuid.uuid4().hex
        await emit_ui({"type": "call_offer", "call_id": call_id, "topic": str(a.get("topic") or "").strip()[:80]})
        return ("The call screen is ringing in the user's Kelo app now. End this reply with one short line. When the user answers you "
                "will get a new message and the call starts; if they decline, nothing comes.")

    return [Tool("start_voice_call",
                 "Ring the user's open Kelo app for a spoken call with you (not a phone call). Use when the user asks to talk "
                 "by voice. A phone call to a person: phone_call.",
                 {"type": "object", "properties": {"topic": {"type": "string", "description": "optional topic"}}},
                 start_voice_call)]
