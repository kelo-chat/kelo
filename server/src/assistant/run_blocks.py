"""A scheduled run's typed blockers: what a tool call itself reported as the reason a step could not be done (a connected service that is
missing or needs connecting again, a site that needs the owner's own sign-in or check, a device that was offline), with a stable id of
what it waits for. The closing phase may end a run as blocked only on one of these (scheduled_runs.Closer). The job keeps the block
until a run succeeds, the job is edited, or (connector blocks only) the connector is connected again (schedules.release_blocks)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

BLOCK_REASONS = ("connect_missing", "connect_expired", "owner_step", "site_check", "device_away")
RECONNECT_REASONS = ("connect_missing", "connect_expired")   # the only reasons a reconnect releases
ON_MAX = 160                                                 # a stable 1-160 character identifier
ON_RE = re.compile(r"^(site:[a-z0-9.-]+|device:[A-Za-z0-9_.:-]+|[a-z0-9][a-z0-9_.-]*)$")


@dataclass(frozen=True)
class Block:
    reason: str      # one of BLOCK_REASONS
    on: str          # a connector id ("gmail"), "site:<domain>" or "device:<owner id>"

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "on": self.on}

    @staticmethod
    def parse(raw: Any) -> "Block | None":
        """Strict: a dict with a known reason and an id of 1-160 characters in the closed id shapes; anything else is None."""
        if not isinstance(raw, dict):
            return None
        reason, on = raw.get("reason"), raw.get("on")
        if reason not in BLOCK_REASONS or not isinstance(on, str) or not (1 <= len(on) <= ON_MAX) or not ON_RE.match(on):
            return None
        return Block(reason, on)


def site_on(host: str) -> str:
    """"site:<registrable domain>" for a host; "" when there is no host."""
    from .tools import sites
    h = str(host or "").strip().lower()
    if not h:
        return ""
    dom = sites.registrable(h).lower()
    return f"site:{dom}" if dom else ""


def device_on(owner: str) -> str:
    return f"device:{owner}" if owner else ""


# set by server.py: the connector ids a vault item is (its connect rows), so a tool that only knows an item name can note the connector
resolve_connectors: Any = None


def note_item(reason: str, item: str) -> None:
    """A vault item was needed and is missing or empty: every connector it is (resolve_connectors) is noted with the reason."""
    if resolve_connectors is None or not item:
        return
    for cid in sorted(resolve_connectors(item)):
        note(reason, cid)


def note(reason: str, on: str) -> None:
    """Called by the code that hit the typed condition: appends the block to the running turn's evidence (the list under the key
    "blocks" that turn_engine gives workers.current_turn). Does nothing outside a turn, for an invalid block, or for a duplicate."""
    from .workers import current_turn
    cur = current_turn.get() or {}
    blocks = cur.get("blocks")
    got = Block.parse({"reason": reason, "on": on})
    if blocks is None or got is None or got in blocks:
        return
    blocks.append(got)
