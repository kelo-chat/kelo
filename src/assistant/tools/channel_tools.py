"""The `messenger` tool: the owner's own messenger chats with Kelo (Telegram, Discord, Slack), changed from the chat.

It does not message other people. Pairing, the running channel and the notifications are the ChannelManager's (channels/manager.py);
this is only the model's way to ask for the status, a fresh pairing code, notifications on or off, the groups it answers in, or to
unpair.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..agent import Tool
from ..i18n import t

CHANNELS = ["telegram", "discord", "slack"]
ACTIONS = ["status", "new_code", "notify_on", "notify_off", "unpair", "groups", "remove_group"]


def channel_tools(manager: Callable[[], Any]) -> list[Tool]:
    """`manager` is read at call time: the ChannelManager is built after the tools (server.py)."""

    def get(args: dict[str, Any]) -> tuple[Any, str] | str:
        m = manager()
        cid = str(args.get("channel") or "telegram")
        if m is None or cid not in m.items:
            return "error: this messenger is not available"
        return m, cid

    async def describe(args: dict[str, Any]) -> str:
        got = get(args)
        name = ((got[0].state(got[1])["paired"] or {}).get("name") or "") if not isinstance(got, str) else ""
        if args.get("action") == "remove_group":
            return t("Stop answering in the group \"{group}\"?", group=str(args.get("group") or ""))
        return t("Disconnect {platform} from {name}?", platform=got[0].names[got[1]] if not isinstance(got, str) else "", name=name)

    async def messenger(args: dict[str, Any]) -> str:
        got = get(args)
        if isinstance(got, str):
            return got
        m, cid = got
        action = str(args.get("action") or "status")
        st = m.state(cid)
        if action == "status":
            return json.dumps(m.status(cid))
        if action not in ACTIONS:
            return f"error: action must be one of {', '.join(ACTIONS)}"
        if action == "new_code":
            if not (st["bot"] or {}).get("username") or not m.status(cid)["connected"]:
                return f"error: {m.names[cid]} is not connected yet. The user connects it in Connectors -> {m.names[cid]} first."
            m.begin_pairing(cid)   # posts the link and the code in the chat itself: the code in a reply of the model is masked as a secret
            return ("A new link and code were posted in the chat just now as a message of yours (the owner sees them there, valid 10 "
                    "minutes). Tell the owner in one short sentence to open that link on their phone; do not write the link or the code again.")
        if action in ("notify_on", "notify_off"):
            m.set_notify(cid, action == "notify_on")
            return "ok"
        if action == "groups":
            return json.dumps(m.groups(cid))
        if action == "remove_group":
            group = str(args.get("group") or "").strip()
            return "ok" if group and m.remove_group(cid, group) else "error: no such group (see the groups action)"
        if not st["paired"]:
            return f"error: no {m.names[cid]} account is connected"
        m.unpair(cid)
        return "ok"

    return [Tool(
        "messenger",
        "Kelo's own messenger chats with the owner (Telegram, Discord, Slack): status, pairing code, notifications on/off, unpair, and the groups "
        "it answers in (groups lists them, remove_group stops one; group = its chat_id or exact title). Not for messaging other people.",
        {"type": "object", "properties": {"channel": {"type": "string", "enum": CHANNELS},
                                          "action": {"type": "string", "enum": ACTIONS},
                                          "group": {"type": "string", "description": "for remove_group: the group's chat_id or exact title"}},
         "required": ["action"]},
        messenger, requires_approval=True, auto_approve=lambda a: a.get("action") not in ("unpair", "remove_group"), describe=describe,
        declined="The user kept it as it was; nothing was changed. Do not ask again unless they bring it up.")]
