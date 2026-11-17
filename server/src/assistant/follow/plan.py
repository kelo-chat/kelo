"""Planner: one model call per upcoming item -> checks and preparations."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..providers.types import ChatRequest, TextDelta
from .checks import Source
from .items import UpcomingItem

log = logging.getLogger(__name__)

MIN_EVERY = 5          # minutes
PREP_KINDS = ("check_in", "other")   # what a preparation is, as the apps show it (the plan prompt names them)
MAX_CHECKS = 6         # safety cap against model loops
MAX_WINDOW_DAYS = 3    # at most 3 days of checks
MAX_WINDOW_SECONDS = MAX_WINDOW_DAYS * 86400
PARCEL_EVERY = 60       # minutes: a carrier page is not read more often
PARCEL_WINDOW_DAYS = 14
TIMEOUT = 30.0
MAX_TOKENS = 600

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[3] / "content" / "prompts" / "follow_plan.md"


def parse_offset(val: Any) -> float:
    """Convert an offset string like '-3h', '+30m', '-1d' to seconds."""
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val or "").strip()
    if not s:
        return 0.0
    sign = -1.0 if s.startswith("-") else 1.0
    s = s.lstrip("+-")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?$", s)
    if not m:
        return 0.0
    num = float(m.group(1))
    unit = (m.group(2) or "m").lower()
    mult = 60.0
    if unit in ("s", "sec", "second", "seconds"):
        mult = 1.0
    elif unit in ("m", "min", "minute", "minutes"):
        mult = 60.0
    elif unit in ("h", "hr", "hour", "hours"):
        mult = 3600.0
    elif unit in ("d", "day", "days"):
        mult = 86400.0
    return sign * num * mult


@dataclass(frozen=True, slots=True)
class FollowPlan:
    checks: list[dict[str, Any]] = field(default_factory=list)
    prep: list[dict[str, Any]] = field(default_factory=list)
    tell: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        if isinstance(self.tell, dict):
            lead = self.tell.get("lead")
            if isinstance(lead, (list, tuple)) and any(lead):
                return False
            if lead and not isinstance(lead, (list, tuple)):
                return False
        return not self.checks and not self.prep

    def to_dict(self) -> dict[str, Any]:
        return {"checks": self.checks, "prep": self.prep, "tell": self.tell}


class Planner:
    def __init__(self, stream_fn: Callable[..., AsyncIterator[Any]] | None = None,
                 model: Callable[[], tuple[str, str] | None] = lambda: None,
                 key_args: Callable[[str], dict[str, Any]] = lambda _p: {},
                 sources: list[Source] | None = None,
                 prompt_path: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.stream_fn = stream_fn
        self.model = model
        self.key_args = key_args
        self.sources = list(sources or [])
        self.prompt_path = prompt_path or DEFAULT_PROMPT_PATH
        self.clock = clock

    def validate(self, raw_plan: dict[str, Any], item: UpcomingItem) -> FollowPlan:
        """Validate and clamp checks and preps according to the design specification."""
        if not isinstance(raw_plan, dict):
            return FollowPlan()

        available_kinds = {s.kind for s in self.sources if s.available()}

        raw_checks = raw_plan.get("checks")
        if not isinstance(raw_checks, list):
            raw_checks = []

        valid_checks: list[dict[str, Any]] = []
        for ch in raw_checks:
            if not isinstance(ch, dict):
                continue
            kind = str(ch.get("kind") or "").strip()
            # Drop check if kind has no available source
            if kind not in available_kinds:
                continue

            # Verify that at least one available source covers these refs
            refs = ch.get("refs") if isinstance(ch.get("refs"), dict) else dict(item.refs)
            if kind == "weather":
                # a forecast is about where and when the item is, unless the plan names another place or hour
                refs = {"place": item.place, "at": str(int(item.starts_at)), **{str(k): str(v) for k, v in refs.items() if v}}
            if kind == "parcel":
                # a parcel is the item's own tracking number and page; the plan cannot point the check elsewhere. Watched for the
                # parcel's two weeks, once an hour at most
                tracking, url = item.refs.get("tracking"), item.refs.get("url")
                if not (tracking and url):
                    continue
                refs = {"tracking": str(tracking), "carrier": str(item.refs.get("carrier") or ""), "url": str(url)}
            if not any(s.covers(refs) for s in self.sources if s.kind == kind and s.available()):
                continue

            # Clamp intervals: MIN_EVERY = 5 minutes
            try:
                every = int(ch.get("every_minutes", 10))
            except (ValueError, TypeError):
                every = 10
            every = max(PARCEL_EVERY if kind == "parcel" else MIN_EVERY, every)

            # Clamp window to at most 3 days
            from_str = str(ch.get("from") or "-3h")
            until_str = str(ch.get("until") or "+30m")
            from_sec = parse_offset(from_str)
            until_sec = parse_offset(until_str)
            w_start = item.starts_at + from_sec
            w_end = item.starts_at + until_sec

            if kind == "parcel":
                from_str, until_str = "+0m", f"+{PARCEL_WINDOW_DAYS}d"
                w_start, w_end = item.starts_at, item.starts_at + PARCEL_WINDOW_DAYS * 86400

            if w_end < w_start:
                w_start, w_end = w_end, w_start

            if (w_end - w_start) > (PARCEL_WINDOW_DAYS if kind == "parcel" else MAX_WINDOW_DAYS) * 86400:
                w_start = w_end - (PARCEL_WINDOW_DAYS if kind == "parcel" else MAX_WINDOW_DAYS) * 86400

            fields = ch.get("fields")
            if not isinstance(fields, list):
                fields = ["gate", "departure_time", "status"] if kind == "flight_status" else list(
                    next((s.default_fields for s in self.sources if s.kind == kind), ("value",)))

            valid_checks.append({
                "kind": kind,
                "refs": refs,
                "fields": [str(f) for f in fields],
                "from": from_str,
                "until": until_str,
                "window_start": w_start,
                "window_end": w_end,
                "every_minutes": every,
            })

        # Cap checks per item at 6
        valid_checks = valid_checks[:MAX_CHECKS]

        raw_preps = raw_plan.get("prep")
        if not isinstance(raw_preps, list):
            raw_preps = []

        valid_preps: list[dict[str, Any]] = []
        for pr in raw_preps:
            if not isinstance(pr, dict):
                continue
            what = str(pr.get("what") or "").strip()
            when = str(pr.get("when") or "").strip()
            if what:
                valid_preps.append({
                    "what": what,
                    "when": when,
                    "state": "later",
                    **{k: v for k, v in pr.items() if k not in ("what", "when", "state", "kind")},
                    "kind": str(pr.get("kind") or "") if pr.get("kind") in PREP_KINDS else "other",   # the apps read this, never the words of `what`
                })

        tell = raw_plan.get("tell") if isinstance(raw_plan.get("tell"), dict) else {"on_change": True}
        return FollowPlan(checks=valid_checks, prep=valid_preps, tell=tell)

    async def plan(self, item: UpcomingItem, notify_prefs: str = "") -> FollowPlan:
        """Produce a FollowPlan for item, calling the background model if available, else falling back to default."""
        chosen = self.model()
        if not chosen or self.stream_fn is None:
            return self._scripted_plan(item)

        provider, model = chosen
        prompt_text = self._build_prompt(item, notify_prefs)
        req = ChatRequest(
            model=model,
            messages=[{"role": "user", "content": prompt_text}],
            tools=[],
            max_tokens=MAX_TOKENS,
            extra={},
        )

        parts: list[str] = []
        try:
            async for ev in self.stream_fn(provider, req, **self.key_args(provider)):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
            raw = "".join(parts).strip()
            parsed = self._extract_json(raw)
            return self.validate(parsed, item)
        except Exception as e:
            log.warning("follow plan for %s failed (%s), falling back to scripted plan", item.key, e)
            return self._scripted_plan(item)

    def _extract_json(self, text: str) -> dict[str, Any]:
        s = text.strip()
        # Remove markdown code block if present
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
        if m:
            s = m.group(1).strip()
        elif s.startswith("{") and s.endswith("}"):
            pass
        else:
            m = re.search(r"(\{.*\})", s, re.DOTALL)
            if m:
                s = m.group(1).strip()
        try:
            res = json.loads(s)
            return res if isinstance(res, dict) else {}
        except Exception:
            return {}

    def _build_prompt(self, item: UpcomingItem, notify_prefs: str) -> str:
        avail = [f"- {s.kind} (source: {type(s).__name__})" for s in self.sources if s.available()]
        avail_str = "\n".join(avail) if avail else "None"
        template = ""
        if self.prompt_path.is_file():
            template = self.prompt_path.read_text(encoding="utf-8")
        if not template:
            template = "Plan checks for {item}. Available: {sources}. Prefs: {notify_prefs}."

        item_str = json.dumps({
            "key": item.key,
            "kind": item.kind,
            "title": item.title,
            "starts_at": item.starts_at,
            "place": item.place,
            "refs": item.refs,
            "source": item.source,
        })
        return template.replace("{item}", item_str).replace("{sources}", avail_str).replace("{notify_prefs}", notify_prefs or "(none)")

    def _scripted_plan(self, item: UpcomingItem) -> FollowPlan:
        """Scripted fallback when no model is available or model call fails."""
        if item.kind == "parcel":
            return self.validate({"checks": [{"kind": "parcel"}], "tell": {"on_change": True}}, item)
        if item.kind == "flight" or "flight" in item.refs:
            raw = {
                "checks": [{
                    "kind": "flight_status",
                    "refs": dict(item.refs),
                    "fields": ["gate", "departure_time", "status"],
                    "from": "-3h",
                    "until": "+30m",
                    "every_minutes": 10,
                }] if item.refs.get("flight") else [],
                "prep": [
                    {"what": "online check-in", "when": "-24h", "needs": ["browser"], "kind": "check_in"},
                ],
                "tell": {"lead": ["-1d", "-3h"], "on_change": True},
            }
            return self.validate(raw, item)
        if item.kind in ("booking", "hotel", "train", "bus", "trip", "stay", "dining") or "booking" in item.refs or item.source in ("mail", "chat"):
            raw = {
                "checks": [],
                "prep": [
                    {"what": "check booking details and documents", "when": "-1d", "needs": []},
                ],
                "tell": {"lead": ["-1d", "-3h"], "on_change": True},
            }
            return self.validate(raw, item)
        return self.validate({}, item)
