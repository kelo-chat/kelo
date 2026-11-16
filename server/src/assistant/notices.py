"""Kelo operator notices: a signed feed from kelo.chat, shown per copy until a user reads them.

The operator (kelo.chat) publishes a file ``notices.json`` next to the app downloads manifest, served at
``GET /download/notices.json`` (no auth, short cache, ETag; 404 when absent → treated as empty by copies).

Shape of notices.json (operator side):
  {"notices": [{"id": "2026-11-16-beta",
                "date": "2026-11-16T09:00:00Z",
                "until": "2026-12-31T00:00:00Z",   (optional)
                "level": "info" | "important",
                "title": {"ko": "...", "en": "..."},
                "body":  {"ko": "...", "en": "..."},
                "link":  "https://kelo.chat/...",   (optional; must be https + kelo.chat)
                "min_app": "0.10.1"},               (optional)
               ...],
   "sig": "<Ed25519 over notices_statement(notices)>",
   "key": "<key id>"}

Every Kelo server (copy) fetches the URL at start and every 6 h, verifies the signature, drops expired entries,
and keeps the last good list in memory (bad sig / network error → keep last good, never clear).

Read state lives in the copy's SQLite: notices_read(id TEXT PK, read_at REAL).

API:
  GET  /api/notices?lang=ko  → {"notices": [{id, date, level, title, body, link?}]} unread, newest first
  POST /api/notices/{id}/read → 204

Operator CLI (cli.py):
  assistant notice add   --id ID --title-ko .. --title-en .. --body-ko .. --body-en ..
                         [--level important] [--until YYYY-MM-DD] [--link URL] [--min-app X.Y.Z]
  assistant notice remove ID
  assistant notice list

Validation:
  - id: 1-80 chars, [A-Za-z0-9._-]
  - title: ≤ 60 chars per language
  - body: ≤ 600 chars per language
  - at most 20 notices
  - link: https + kelo.chat (optional)
  - min_app: MAJOR.MINOR.PATCH (optional)
  - until: ISO date YYYY-MM-DD (optional); treated as the next day 00:00:00Z (shows through that day)
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FEED_URL = "https://kelo.chat/download/notices.json"
REFRESH_SECONDS = 6 * 3600
MAX_BODY_BYTES = 64 * 1024
FETCH_TIMEOUT = 10.0
MAX_NOTICES = 20
MAX_TITLE = 60
MAX_BODY = 600
LEVELS = ("info", "important")
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_VERSION_RE = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,5})$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_LINK_HOST_RE = re.compile(r"^(?:www\.)?kelo\.chat$", re.IGNORECASE)


# ---------------------------------------------------------------------- validation

def _valid_link(url: str | None) -> str | None:
    """None when url is None; raises ValueError when it is present but not a valid kelo.chat https link."""
    if url is None:
        return None
    from urllib.parse import urlsplit
    try:
        p = urlsplit(url.strip())
    except ValueError:
        raise ValueError(f"link is not a valid URL: {url!r}")
    if p.scheme.lower() != "https":
        raise ValueError(f"link must be https: {url!r}")
    if not _LINK_HOST_RE.fullmatch((p.hostname or "").lower()):
        raise ValueError(f"link must be on kelo.chat: {url!r}")
    return url.strip()


def _until_epoch(date_str: str | None) -> float | None:
    """'YYYY-MM-DD' -> epoch of the next day 00:00:00 UTC (shows through that day). None when absent."""
    if date_str is None:
        return None
    m = _DATE_RE.fullmatch(date_str.strip())
    if not m:
        raise ValueError(f"--until must be YYYY-MM-DD: {date_str!r}")
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=UTC)
    except ValueError as e:
        raise ValueError(f"--until: {e}") from None
    return (d + timedelta(days=1)).timestamp()


def validate_notice(n: dict[str, Any]) -> None:
    """Check a single notice dict (from CLI or the feed). Raises ValueError with a clear message."""
    nid = n.get("id")
    if not isinstance(nid, str) or not _ID_RE.fullmatch(nid):
        raise ValueError(f"notice id must be 1-80 characters [A-Za-z0-9._-]: {nid!r}")
    date = n.get("date")
    if not isinstance(date, str):
        raise ValueError("notice must have a 'date' string")
    try:
        datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"notice date must be ISO 8601: {date!r}") from None
    level = n.get("level", "info")
    if level not in LEVELS:
        raise ValueError(f"level must be 'info' or 'important': {level!r}")
    for field, limit in (("title", MAX_TITLE), ("body", MAX_BODY)):
        val = n.get(field)
        if not isinstance(val, dict):
            raise ValueError(f"notice must have a '{field}' object")
        for lang, text in val.items():
            if not isinstance(text, str):
                raise ValueError(f"notice {field}[{lang!r}] must be a string")
            if len(text) > limit:
                raise ValueError(f"notice {field}[{lang!r}] is too long ({len(text)} > {limit})")
        if "en" not in val and "ko" not in val:
            raise ValueError(f"notice {field} must have at least 'en' or 'ko'")
    link = n.get("link")
    if link is not None:
        _valid_link(link)   # raises ValueError when bad
    min_app = n.get("min_app")
    if min_app is not None:
        if not isinstance(min_app, str) or not _VERSION_RE.fullmatch(min_app.strip()):
            raise ValueError(f"min_app must be MAJOR.MINOR.PATCH: {min_app!r}")
    until = n.get("until")
    if until is not None:
        if not isinstance(until, str):
            raise ValueError("'until' must be a string (ISO datetime)")
        try:
            datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"'until' must be ISO 8601: {until!r}") from None


# ---------------------------------------------------------------------- feed parsing and verification

def _parse_feed(raw: bytes) -> list[dict[str, Any]] | None:
    """Parse and verify a fetched notices.json. Returns the notices list (may be empty) or None on any error."""
    if not raw:
        return []
    from . import release_sign
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        log.warning("notices: feed is not valid UTF-8 JSON")
        return None
    if not isinstance(obj, dict):
        log.warning("notices: feed root is not an object")
        return None
    notices = obj.get("notices")
    sig = obj.get("sig")
    kid = obj.get("key")
    if not isinstance(notices, list):
        log.warning("notices: 'notices' is not a list")
        return None
    if not isinstance(sig, str) or not isinstance(kid, str):
        log.warning("notices: unsigned or missing sig/key")
        return None
    if not release_sign.verify_notices(notices, sig, kid):
        log.warning("notices: signature verification failed")
        return None
    if len(notices) > MAX_NOTICES:
        log.warning("notices: too many notices (%d > %d); ignoring feed", len(notices), MAX_NOTICES)
        return None
    # Validate each notice
    good: list[dict[str, Any]] = []
    for n in notices:
        if not isinstance(n, dict):
            log.warning("notices: notice is not a dict")
            return None
        try:
            validate_notice(n)
        except ValueError as e:
            log.warning("notices: invalid notice %r: %s", n.get("id"), e)
            return None
        good.append(n)
    return good


def _active(notices: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """Drop notices whose 'until' has passed."""
    result = []
    for n in notices:
        until = n.get("until")
        if until is None:
            result.append(n)
        else:
            try:
                exp = datetime.fromisoformat(until.replace("Z", "+00:00")).timestamp()
                if now < exp:
                    result.append(n)
            except ValueError:
                result.append(n)   # malformed 'until' -> keep it (validate_notice would have caught it)
    return result


def _pick_text(loc: dict[str, str], lang: str) -> str:
    """Pick the best available language: requested lang, then 'en', then 'ko', then first available."""
    return loc.get(lang) or loc.get("en") or loc.get("ko") or next(iter(loc.values()), "")


def _parse_min_app(min_app_str: str) -> tuple[int, int, int]:
    m = _VERSION_RE.fullmatch(min_app_str.strip())
    if not m:
        raise ValueError(min_app_str)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


# ---------------------------------------------------------------------- the feed holder (one per server process)

class NoticesFeed:
    """Fetches, caches, and verifies the operator's notices feed. One instance per server process.

    `fetcher`: async callable(url, max_bytes) -> bytes; raises on network error or oversized response.
    `url`: the feed URL; empty string = disabled.
    """

    def __init__(self, fetcher: Callable[..., Awaitable[bytes]] | None, url: str, store: Any):
        self._fetcher = fetcher
        self._url = url
        self._store = store          # a Store instance with notices_read and notices_feed helpers
        self._notices: list[dict[str, Any]] = []   # last good verified list (empty when never fetched)
        self._fetched_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self._url and self._fetcher is not None)

    async def refresh(self) -> None:
        """Fetch and verify the feed. On error keeps the last good list."""
        if not self.enabled:
            return
        self._fetched_at = time.monotonic()
        try:
            raw = await self._fetcher(self._url, MAX_BODY_BYTES)  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001
            log.warning("notices: fetch failed (%s: %s)", type(e).__name__, e)
            return
        if raw is not None and len(raw) > MAX_BODY_BYTES:
            log.warning("notices: response body exceeds %d bytes cap", MAX_BODY_BYTES)
            return
        parsed = _parse_feed(raw)
        if parsed is None:
            return   # keep last good
        self._notices = parsed
        log.info("notices: loaded %d notice(s)", len(parsed))

    async def refresh_if_stale(self) -> None:
        """Refresh when the last fetch is older than REFRESH_SECONDS."""
        if not self.enabled:
            return
        if time.monotonic() - self._fetched_at >= REFRESH_SECONDS:
            await self.refresh()

    def unread(self, lang: str, app_version: tuple[str, str, int | None] | None) -> list[dict[str, Any]]:
        """Notices the user has not read yet, newest first, filtered by min_app and expiry.

        `app_version`: the result of version.parse_app_header (platform, version, build) or None.
        """
        from . import version as rel_ver
        now = time.time()
        read_ids = self._store.notices_read_ids()
        result = []
        for n in reversed(self._notices):   # most-recently-added first
            nid = n.get("id", "")
            if nid in read_ids:
                continue
            if n.get("until"):
                try:
                    exp = datetime.fromisoformat(n["until"].replace("Z", "+00:00")).timestamp()
                    if now >= exp:
                        continue
                except ValueError:
                    pass
            if app_version is not None and n.get("min_app"):
                try:
                    req = rel_ver.parse(n["min_app"])
                    app_ver = rel_ver.parse(app_version[1])
                    if app_ver < req:
                        continue
                except ValueError:
                    pass   # unparsable min_app -> show the notice
            lang_key = lang[:2].lower() if lang else "en"
            title_loc = n.get("title", {})
            body_loc = n.get("body", {})
            row: dict[str, Any] = {
                "id": nid,
                "date": n["date"],
                "level": n.get("level", "info"),
                "title": _pick_text(title_loc, lang_key),
                "body": _pick_text(body_loc, lang_key),
            }
            if "link" in n and n["link"]:
                row["link"] = n["link"]
            result.append(row)
        result.sort(key=lambda r: r["date"], reverse=True)
        return result

    def mark_read(self, nid: str) -> None:
        """Mark a notice as read; silently ignores unknown ids."""
        known = {n.get("id") for n in self._notices}
        if nid in known:
            self._store.set_notice_read(nid)

    def all_notices(self) -> list[dict[str, Any]]:
        """The raw verified list (for the public /download/notices.json route on the operator's server)."""
        return list(self._notices)


# ---------------------------------------------------------------------- operator CLI helpers

def _load_notices_file(path: Path) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Read notices.json from `path`. Returns (notices, sig, key). All three are None when file absent."""
    if not path.is_file():
        return [], None, None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"cannot read {path}: {e}") from None
    notices = obj.get("notices", [])
    sig = obj.get("sig")
    kid = obj.get("key")
    return notices, sig, kid


def _write_notices_file(path: Path, notices: list[dict[str, Any]], key_path: Path, root: Path | None = None) -> None:
    """Write a signed notices.json atomically to `path`."""
    from . import release_sign
    sig, kid = release_sign.sign_notices(notices, key_path, root)
    obj = {"notices": notices, "sig": sig, "key": kid}
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)   # atomic on POSIX


def cli_add(notices_path: Path, notice: dict[str, Any], key_path: Path, root: Path | None = None,
            out: Callable[[str], None] = print) -> int:
    """Add or replace a notice in notices.json. Returns 0 on success, 1 on error."""
    try:
        validate_notice(notice)
    except ValueError as e:
        out(f"error: {e}")
        return 1
    try:
        existing, _, _ = _load_notices_file(notices_path)
    except RuntimeError as e:
        out(f"error: {e}")
        return 1
    # Replace if same id, otherwise append
    ids = [n.get("id") for n in existing]
    if notice["id"] in ids:
        idx = ids.index(notice["id"])
        existing[idx] = notice
        action = "updated"
    else:
        if len(existing) >= MAX_NOTICES:
            out(f"error: at most {MAX_NOTICES} notices allowed; remove one first")
            return 1
        existing.append(notice)
        action = "added"
    try:
        _write_notices_file(notices_path, existing, key_path, root)
    except Exception as e:  # noqa: BLE001
        out(f"error: {e}")
        return 1
    out(f"Notice {notice['id']!r} {action}. {len(existing)} notice(s) in {notices_path.name}.")
    return 0


def cli_remove(notices_path: Path, nid: str, key_path: Path, root: Path | None = None,
               out: Callable[[str], None] = print) -> int:
    """Remove a notice by id. Returns 0 on success, 1 on error."""
    try:
        existing, _, _ = _load_notices_file(notices_path)
    except RuntimeError as e:
        out(f"error: {e}")
        return 1
    before = len(existing)
    existing = [n for n in existing if n.get("id") != nid]
    if len(existing) == before:
        out(f"error: no notice with id {nid!r}")
        return 1
    try:
        _write_notices_file(notices_path, existing, key_path, root)
    except Exception as e:  # noqa: BLE001
        out(f"error: {e}")
        return 1
    out(f"Notice {nid!r} removed. {len(existing)} notice(s) remaining.")
    return 0


def cli_list(notices_path: Path, out: Callable[[str], None] = print) -> int:
    """List all notices in notices.json."""
    try:
        notices, sig, kid = _load_notices_file(notices_path)
    except RuntimeError as e:
        out(f"error: {e}")
        return 1
    if not notices and sig is None:
        out("No notices file yet.")
        return 0
    out(f"{len(notices)} notice(s) in {notices_path.name} (key {kid or '?'}):")
    for n in notices:
        until = f", until {n['until']}" if n.get("until") else ""
        level = n.get("level", "info")
        title_en = (n.get("title") or {}).get("en", "")
        out(f"  {n.get('id')}  [{level}]{until}  {title_en!r}")
    return 0


def run_notice(args: Any, root: Path | None = None, out: Callable[[str], None] = print) -> int:
    """`assistant notice add|remove|list` implementation."""
    from . import release_sign
    root = (root or Path.cwd()).resolve()
    notices_path = root / "state" / "downloads" / "notices.json"
    file_override = getattr(args, "file", None)
    if file_override:
        notices_path = Path(file_override)
    else:
        import os
        env_path = os.environ.get("KELO_NOTICES_FILE", "").strip()
        if env_path:
            notices_path = Path(env_path)
        elif "ASSISTANT_DOWNLOADS_DIR" in os.environ:
            notices_path = Path(os.environ["ASSISTANT_DOWNLOADS_DIR"]) / "notices.json"
        elif Path("/state/downloads").is_dir():
            notices_path = Path("/state/downloads/notices.json")

    if args.ncmd == "list":
        return cli_list(notices_path, out)

    # For add/remove we need the signing key
    key_path_str = getattr(args, "sign_key", None)
    key_path = Path(key_path_str).expanduser() if key_path_str else release_sign.DEFAULT_KEY_PATH.expanduser()
    if not key_path.is_file():
        out(f"error: signing key not found at {key_path}  (make one with `assistant release-key new`)")
        return 1

    if args.ncmd == "remove":
        return cli_remove(notices_path, args.id, key_path, root, out)

    # add
    if args.ncmd == "add":
        # Build the notice dict from args
        from datetime import UTC
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        date_arg = getattr(args, "date", None)
        notice: dict[str, Any] = {
            "id": args.id,
            "date": date_arg if date_arg else now_iso,
            "level": getattr(args, "level", "info") or "info",
            "title": {"ko": args.title_ko, "en": args.title_en},
            "body": {"ko": args.body_ko, "en": args.body_en},
        }
        until_str = getattr(args, "until", None)
        if until_str:
            # Convert YYYY-MM-DD to ISO datetime (next day 00:00:00Z)
            try:
                epoch = _until_epoch(until_str)
            except ValueError as e:
                out(f"error: {e}")
                return 1
            until_dt = datetime.fromtimestamp(epoch, UTC)  # type: ignore[arg-type]
            notice["until"] = until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        link = getattr(args, "link", None)
        if link:
            try:
                notice["link"] = _valid_link(link)
            except ValueError as e:
                out(f"error: {e}")
                return 1
        min_app = getattr(args, "min_app", None)
        if min_app:
            notice["min_app"] = min_app.strip()
        return cli_add(notices_path, notice, key_path, root, out)

    out(f"error: unknown notice subcommand {args.ncmd!r}")
    return 1


# ---------------------------------------------------------------------- netguard fetcher for the copy

async def netguard_notices_fetcher(url: str, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    """Fetch the notices feed with the netguard fetcher (https, public addresses, no redirects)."""
    from . import netguard
    from urllib.parse import urlsplit
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme.lower() != "https":
        raise ValueError(f"notices URL must be https: {url!r}")
    netguard.valid_host(host)
    r = await netguard.fetch(
        "GET", url, allowed_hosts=[host],
        headers={"Accept": "application/json", "User-Agent": "Kelo"},
        timeout=FETCH_TIMEOUT, max_body=max_bytes,
    )
    if r.status == 404:
        return b""
    if r.status != 200:
        raise RuntimeError(f"fetch returned HTTP {r.status}")
    if r.truncated:
        raise ValueError(f"feed exceeds {max_bytes} bytes cap")
    return r.body


__all__ = [
    "NoticesFeed", "FEED_URL", "REFRESH_SECONDS", "MAX_BODY_BYTES",
    "validate_notice", "cli_add", "cli_remove", "cli_list", "run_notice",
    "netguard_notices_fetcher", "_parse_feed", "_write_notices_file", "_load_notices_file",
]
