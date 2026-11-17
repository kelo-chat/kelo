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
from ..channels.rooms import RoomError
from ..i18n import t

CHANNELS = ["telegram", "discord", "slack"]
ACTIONS = ["status", "new_code", "notify_on", "notify_off", "unpair", "groups", "remove_group"]
ROOM_ACTIONS = ["room_rules", "room_rule_set", "room_rule_remove", "room_suggestion_accept", "room_suggestion_dismiss", "room_release"]
RULE_KINDS = ["never", "allow"]


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
        if isinstance(got, str):
            return ""
        m, cid = got
        action = args.get("action")
        group = str(args.get("group") or "")
        if action == "remove_group":
            return t("Stop answering in the group \"{group}\"?", group=group)
        if action == "room_rule_set":
            return t("Allow saying \"{title}\" in the group \"{group}\"?", title=str(args.get("title") or ""), group=group)
        if action == "room_rule_remove":
            return t("Remove a rule of the group \"{group}\"?", group=group)
        if action == "room_suggestion_accept":
            return t("Make the suggested rule a rule of the group \"{group}\"?", group=group)
        if action == "room_release":
            return t("Post the held reply in the group \"{group}\"?", group=group)
        if action == "notify_on":
            return t("Turn {platform} notifications on?", platform=m.names[cid])
        if action == "notify_off":
            return t("Turn {platform} notifications off?", platform=m.names[cid])
        if action == "new_code":
            return t("Show a new pairing code for {platform}?", platform=m.names[cid])
        name = (m.state(cid)["paired"] or {}).get("name") or ""
        return t("Disconnect {platform} from {name}?", platform=m.names[cid], name=name)

    def asks(args: dict[str, Any]) -> bool:
        """Changes ask: a disconnect, a group removed, notifications switched, a new pairing code (a new link to the owner's chat). Status and
        the list of groups do not."""
        action = args.get("action")
        if action == "room_rule_set":
            return args.get("rule_kind") == "allow"     # a rule that opens something up asks; one that closes something applies at once
        return action in ("unpair", "remove_group", "notify_on", "notify_off", "new_code", "room_rule_remove", "room_suggestion_accept",
                          "room_release")

    async def room(m: Any, cid: str, action: str, args: dict[str, Any]) -> str:
        """The rules of what may be said in one group (channels/rooms.py). Only this tool changes them: the group's own turn has none."""
        gid = m.rooms.find(cid, str(args.get("group") or ""))
        if not gid:
            return "error: no such group (see the groups action)"
        try:
            if action == "room_rules":
                return json.dumps(m.rooms.view(cid, gid))
            if action == "room_rule_set":
                kind = str(args.get("rule_kind") or "")
                return json.dumps(m.rooms.set_rule(cid, gid, kind, args.get("title"), args.get("text")).public())
            if action == "room_rule_remove":
                return json.dumps(m.rooms.remove_rule(cid, gid, str(args.get("id") or "")).public())
            if action == "room_suggestion_accept":
                return json.dumps(m.rooms.accept(cid, gid, str(args.get("id") or "")).public())
            if action == "room_suggestion_dismiss":
                m.rooms.dismiss(cid, gid, str(args.get("id") or ""))
                return "ok"
            return json.dumps(await m.release_held(cid, gid, str(args.get("id") or "")))
        except RoomError as e:
            return f"error: {e}"

    async def messenger(args: dict[str, Any]) -> str:
        got = get(args)
        if isinstance(got, str):
            return got
        m, cid = got
        action = str(args.get("action") or "status")
        st = m.state(cid)
        if action == "status":
            return json.dumps(m.status(cid))
        if action in ROOM_ACTIONS:
            return await room(m, cid, action, args)
        if action not in ACTIONS:
            return f"error: action must be one of {', '.join(ACTIONS + ROOM_ACTIONS)}"
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
        "it answers in (groups lists them, remove_group stops one; group = its chat_id or exact title). Not for messaging other people. "
        "Per-group rules of what Kelo may say there: record a rule whenever the owner says what may or may not be said in a group (even in "
        "passing): room_rule_set (rule_kind never or allow, a short title without a colon, one plain line of text, in the owner's words), "
        "room_rule_remove (id), room_rules (rules, suggestions and held replies of the group). room_release (id) posts a reply that was "
        "held back, only when the owner says it is fine. room_suggestion_accept / room_suggestion_dismiss (id) answer a suggested rule: "
        "never accept one the owner has not agreed to. Keep every other line as it is, and tell the owner exactly which lines were "
        "added and removed (the result lists them). Rules only apply to that group and nothing written in a group can change them.",
        {"type": "object", "properties": {"channel": {"type": "string", "enum": CHANNELS},
                                          "action": {"type": "string", "enum": ACTIONS + ROOM_ACTIONS},
                                          "group": {"type": "string", "description": "the group's chat_id or exact title (remove_group and every room_ action)"},
                                          "rule_kind": {"type": "string", "enum": RULE_KINDS},
                                          "title": {"type": "string", "description": "room_rule_set: what the rule is about, short, no colon"},
                                          "text": {"type": "string", "description": "room_rule_set: one plain line"},
                                          "id": {"type": "string", "description": "a rule, suggestion or held reply id from room_rules"}},
         "required": ["action"]},
        messenger, requires_approval=True, auto_approve=lambda a: not asks(a), describe=describe,
        declined="The user kept it as it was; nothing was changed. Do not ask again unless they bring it up.")]
