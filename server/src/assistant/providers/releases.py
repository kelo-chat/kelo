"""The latest published version of a few programs Kelo talks to or runs, read from their public release metadata.

Used for:
  codex   the ChatGPT-subscription client version Kelo presents (openai-codex): ChatGPT offers a model only to Codex clients at least
          as new as that model, so an old or "0.0.0" version never sees new models. Source: the npm registry's dist-tag `latest` of
          `@openai/codex`, the same package the official `npm i -g @openai/codex` installs (its GitHub release tag `rust-v<ver>` is
          cut together with it). Falls back to CODEX_PINNED when the registry cannot be reached.
  claude  the Claude Code CLI (npm `@anthropic-ai/claude-code`, dist-tag `latest`: what docker/cli.Dockerfile's `npm install -g` takes)
  agy     the Antigravity CLI: the per-platform manifest Google's own installer (antigravity.google/cli/install.sh) reads

Every answer is cached a day in memory (a failure for 10 minutes, so a short outage is retried soon). Nothing here sends a credential
or anything about the user: plain GETs of public JSON with a short timeout.
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

# The newest stable Codex release when this was written (npm `@openai/codex` latest, GitHub `rust-v0.167.0`, 2026-11-06).
CODEX_PINNED = "0.167.0"

DAY = 86_400.0
FAIL_RETRY = 600.0
TIMEOUT = httpx.Timeout(5.0, connect=3.0)

NPM_TAGS = "https://registry.npmjs.org/-/package/{package}/dist-tags"
AGY_MANIFEST = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/{platform}.json"   # install.sh's host

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_VERSION_IN = re.compile(r"(\d+\.\d+\.\d+)")

Fetcher = Callable[[str], Awaitable[str | None]]

_cache: dict[str, tuple[float, str | None]] = {}   # what -> (when fetched, version or None after a failure)
_pending: dict[str, asyncio.Task[Any]] = {}


def _agy_platform() -> str:
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), "amd64")
    osname = "darwin" if platform.system() == "Darwin" else "linux"
    return f"{osname}_{arch}"


async def _get_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
        r = await c.get(url, headers={"accept": "application/json"})
    if r.status_code != 200 or len(r.content) > 256_000:
        return None
    try:
        return r.json()
    except ValueError:
        return None


async def fetch(what: str) -> str | None:
    """One network look-up, no cache. None when the source cannot be reached or says something unexpected."""
    try:
        if what in ("codex", "claude"):
            package = {"codex": "@openai/codex", "claude": "@anthropic-ai/claude-code"}[what]
            data = await _get_json(NPM_TAGS.format(package=package))
            ver = data.get("latest") if isinstance(data, dict) else None
        elif what == "agy":
            data = await _get_json(AGY_MANIFEST.format(platform=_agy_platform()))
            ver = data.get("version") if isinstance(data, dict) else None
        else:
            return None
    except (httpx.HTTPError, OSError):
        return None
    return ver if isinstance(ver, str) and _SEMVER.match(ver) else None


_fetcher: list[Fetcher] = [fetch]


def set_fetcher(fn: Fetcher | None) -> None:
    """Tests: replace the network look-up (None puts the real one back) and forget what was cached."""
    _fetcher[0] = fn or fetch
    _cache.clear()


def disabled() -> bool:
    return os.environ.get("ASSISTANT_NO_RELEASE_CHECK", "").strip().lower() in ("1", "true", "yes")


def _fresh(what: str) -> tuple[bool, str | None]:
    hit = _cache.get(what)
    if not hit:
        return False, None
    age = time.monotonic() - hit[0]
    return age < (DAY if hit[1] else FAIL_RETRY), hit[1]


async def latest(what: str) -> str | None:
    """The latest version (cached a day); None when it is not known."""
    if disabled():
        return None
    ok, ver = _fresh(what)
    if ok:
        return ver
    found = await _fetcher[0](what)
    if found is None and ver:   # keep the last good answer through an outage; look again in 10 minutes
        _cache[what] = (time.monotonic() - DAY + FAIL_RETRY, ver)
        return ver
    _cache[what] = (time.monotonic(), found)
    return found


def latest_cached(what: str) -> str | None:
    """What is known now, without waiting: starts a look-up in the background when the cache is old or empty."""
    if disabled():
        return None
    ok, ver = _fresh(what)
    if not ok and what not in _pending:
        try:
            task = asyncio.get_running_loop().create_task(latest(what))
        except RuntimeError:
            return ver
        _pending[what] = task
        task.add_done_callback(lambda _t: _pending.pop(what, None))
    return ver


async def codex_version() -> str:
    """The Codex client version Kelo presents to chatgpt.com, for the model list and for every turn alike."""
    return await latest("codex") or CODEX_PINNED


def version_of(text: str | None) -> str | None:
    """'2.1.311 (Claude Code)' -> '2.1.311'; None when there is no x.y.z in it."""
    m = _VERSION_IN.search(text or "")
    return m.group(1) if m else None


def newer(latest_version: str | None, installed: str | None) -> bool:
    """True when `latest_version` is a higher x.y.z than the one in `installed` (False when either is unknown)."""
    a, b = version_of(latest_version), version_of(installed)
    if not a or not b:
        return False
    return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))
