"""A fake main model for notify_block.NotifyBlock: an item is blocked when it holds one of the `blocked` terms (the real judge reads the
owner's rules; the fake only stands in for its answer). `calls` keeps every request; `down` makes it unavailable."""

from __future__ import annotations

import json

from assistant.notify_block import NotifyBlock


class FakeRules:
    def __init__(self, *blocked: str, down: bool = False) -> None:
        self.blocked, self.down, self.calls = blocked, down, []
        self.block = NotifyBlock(self.ask)

    async def ask(self, system, user):
        self.calls.append(json.loads(user))
        if self.down:
            return None
        items = self.calls[-1]["items"]
        return {"blocked": [any(b in x["text"].lower() for b in self.blocked) for x in items]}
