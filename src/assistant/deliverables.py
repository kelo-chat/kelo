"""The user's library of results ("Results" in the app): files the agent made for them, kept in `files/` of the agent's home.

- Files arrive from the work room (workroom_run copies what the code wrote to $OUT_DIR) and are never overwritten: a second
  `report.xlsx` becomes `report-1.xlsx`.
- Deleting moves a file to `.trash/` (hidden from the agent), where it is kept TRASH_DAYS days and can be restored; a daily job
  purges what is older.
- A share link makes one file downloadable by anyone with the link for SHARE_HOURS hours. Each link is made on purpose (the user in
  the app, or an approval card for the agent each time; never remembered), can be revoked, and dies with the file.
Everything counts toward the assistant's storage cap (quota.py).
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import time
from pathlib import Path
from typing import Any

from .home import TRASH, TRASH_DAYS, Home, HomeError

DIR = "files"
SHARES = ".shares.json"
SHARE_HOURS = 48
MAX_FILE = 100 * 1024**2
NAME = re.compile(r"[^A-Za-z0-9._\-가-힣 ()]+")
KINDS = {"document": {".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".html", ".pptx", ".odp", ".epub"},
         "spreadsheet": {".xlsx", ".xls", ".ods", ".csv", ".tsv"},
         "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".tif", ".tiff", ".heic"},
         "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus"},
         "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif"},
         "data": {".json", ".jsonl", ".xml", ".yaml", ".yml", ".parquet", ".sqlite", ".db"},
         "archive": {".zip", ".tar", ".gz", ".tgz", ".7z"}}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".html", ".svg", ".log", ".py", ".js", ".sh"}


class LibraryFileError(HomeError):
    """A missing or refused library file. The message is safe to show."""


def clean_name(name: str) -> str:
    base = NAME.sub("_", Path(str(name).replace("\\", "/")).name).strip(" .")
    return base[:120] or "file"


def kind_of(name: str) -> str:
    suf = Path(name).suffix.lower()
    return next((k for k, s in KINDS.items() if suf in s), "other")


def type_of(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class Deliverables:
    def __init__(self, home: Home, clock: Any = time.time):
        self.home, self.clock = home, clock
        self.dir = home.root / DIR
        self.trash_dir = home.root / TRASH

    # ------------------------------------------------------------------ files
    def _file(self, name: str) -> Path:
        base = Path(str(name).replace("\\", "/")).name
        p = self.dir / base
        if not base or base.startswith(".") or not p.is_file() or p.is_symlink():
            raise LibraryFileError(f"no file named {base!r} in your library")
        return p

    def path(self, name: str) -> Path:
        return self._file(name)

    def save(self, name: str, data: bytes) -> str:
        """Keep one result. Returns the name it was kept under (never replaces an earlier file)."""
        base = clean_name(name)
        if not data:
            raise LibraryFileError("empty file")
        if len(data) > MAX_FILE:
            raise LibraryFileError(f"file larger than {MAX_FILE // 1024**2} MB")
        if self.home.quota:
            self.home._check_room(len(data))
        self.dir.mkdir(exist_ok=True, mode=0o700)
        target, n = self.dir / base, 1
        while target.exists():
            target = self.dir / f"{Path(base).stem}-{n}{Path(base).suffix}"
            n += 1
        tmp = target.with_name("." + target.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        if self.home.quota:
            self.home.quota.wrote()
        return target.name

    def list(self) -> list[dict[str, Any]]:
        """Newest first: {"name", "path", "bytes", "type", "kind", "created", "shared_until"}."""
        if not self.dir.is_dir():
            return []
        shares = self._shares()
        now = self.clock()
        out = []
        for p in self.dir.iterdir():
            if not p.is_file() or p.is_symlink() or p.name.startswith("."):
                continue
            st = p.stat()
            until = max((s["expires_at"] for s in shares.values() if s["name"] == p.name and s["expires_at"] > now), default=None)
            out.append({"name": p.name, "path": f"{DIR}/{p.name}", "bytes": st.st_size, "type": type_of(p.name), "kind": kind_of(p.name),
                        "created": st.st_mtime, "shared_until": until})
        return sorted(out, key=lambda r: (-r["created"], r["name"]))

    def read_text(self, name: str, limit: int = 12_000) -> str:
        p = self._file(name)
        if p.suffix.lower() not in TEXT_SUFFIXES:
            raise LibraryFileError(f"{p.name} is not a text file; describe it from what the code printed, or open it in the work room")
        data = p.read_bytes()
        text = data[:limit].decode("utf-8", "replace")
        return text + (f"\n[showing the first {limit} bytes of {len(data)}]" if len(data) > limit else "")

    # ------------------------------------------------------------------ trash
    def _trash_index(self) -> list[dict[str, Any]]:
        try:
            rows = json.loads((self.trash_dir / "index.json").read_text(encoding="utf-8"))
            return [r for r in rows if isinstance(r, dict) and r.get("id") and r.get("name")]
        except (OSError, ValueError):
            return []

    def _save_trash_index(self, rows: list[dict[str, Any]]) -> None:
        self.trash_dir.mkdir(exist_ok=True, mode=0o700)
        tmp = self.trash_dir / "index.json.tmp"
        tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.trash_dir / "index.json")

    def trash(self, name: str) -> dict[str, Any]:
        """Move a file to the trash (its share links stop working). Returns its trash entry."""
        p = self._file(name)
        rows = self._trash_index()
        entry = {"id": secrets.token_hex(6), "name": p.name, "bytes": p.stat().st_size, "deleted_at": self.clock()}
        self.trash_dir.mkdir(exist_ok=True, mode=0o700)
        p.replace(self.trash_dir / f"{entry['id']}-{p.name}")
        self._save_trash_index([*rows, entry])
        self.unshare(p.name)
        return {**entry, "purge_at": entry["deleted_at"] + TRASH_DAYS * 86400}

    def trashed(self) -> list[dict[str, Any]]:
        return sorted(({**r, "purge_at": r["deleted_at"] + TRASH_DAYS * 86400} for r in self._trash_index()
                       if (self.trash_dir / f"{r['id']}-{r['name']}").is_file()), key=lambda r: -r["deleted_at"])

    def restore(self, name: str) -> str:
        """Bring back the most recently deleted file of that name (or trash id). Returns the name it is kept under now."""
        rows = self._trash_index()
        pick = next((r for r in sorted(rows, key=lambda r: -r["deleted_at"]) if r["id"] == name or r["name"] == name), None)
        src = self.trash_dir / f"{pick['id']}-{pick['name']}" if pick else None
        if not pick or not src.is_file():
            raise LibraryFileError(f"nothing named {name!r} in the trash")
        self.dir.mkdir(exist_ok=True, mode=0o700)
        target, n = self.dir / pick["name"], 1
        while target.exists():
            target = self.dir / f"{Path(pick['name']).stem}-{n}{Path(pick['name']).suffix}"
            n += 1
        src.replace(target)
        self._save_trash_index([r for r in rows if r["id"] != pick["id"]])
        return target.name

    def forget_trashed(self, ids: set[str]) -> None:
        """Drop these trash entries from the index (their files were removed for good by storage_cleanup.py)."""
        rows = self._trash_index()
        keep = [r for r in rows if r["id"] not in ids]
        if len(keep) != len(rows):
            self._save_trash_index(keep)

    def purge(self) -> int:
        """Delete trash older than TRASH_DAYS (the daily job). Returns how many files went."""
        now, rows, keep, n = self.clock(), self._trash_index(), [], 0
        for r in rows:
            blob = self.trash_dir / f"{r['id']}-{r['name']}"
            if now - float(r.get("deleted_at") or 0) >= TRASH_DAYS * 86400:
                blob.unlink(missing_ok=True)
                n += 1
            elif blob.is_file():
                keep.append(r)
        if self.trash_dir.is_dir():
            known = {f"{r['id']}-{r['name']}" for r in keep} | {"index.json"}
            for p in self.trash_dir.iterdir():   # a file left behind by a crash between the move and the index write
                if p.is_file() and p.name not in known and now - p.stat().st_mtime >= TRASH_DAYS * 86400:
                    p.unlink(missing_ok=True)
        if n or len(keep) != len(rows):
            self._save_trash_index(keep)
        self._save_shares({t: s for t, s in self._shares().items() if s["expires_at"] > now})   # expired links are forgotten too
        if n and self.home.quota:
            self.home.quota.wrote()
        return n

    # ------------------------------------------------------------------ share links
    def _shares(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads((self.home.root / SHARES).read_text(encoding="utf-8"))
            return {t: s for t, s in raw.items() if isinstance(s, dict) and "name" in s and "expires_at" in s}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_shares(self, shares: dict[str, dict[str, Any]]) -> None:
        tmp = self.home.root / (SHARES + ".tmp")
        tmp.write_text(json.dumps(shares), encoding="utf-8")
        tmp.replace(self.home.root / SHARES)

    def share(self, name: str, hours: float = SHARE_HOURS) -> dict[str, Any]:
        """A new link for one file: {"token", "name", "created_at", "expires_at"}. Valid `hours` (at most SHARE_HOURS)."""
        p = self._file(name)
        now = self.clock()
        shares = {t: s for t, s in self._shares().items() if s["expires_at"] > now}
        token = secrets.token_urlsafe(32)
        shares[token] = {"name": p.name, "created_at": now, "expires_at": now + min(float(hours), SHARE_HOURS) * 3600}
        self._save_shares(shares)
        return {"token": token, **shares[token]}

    def unshare(self, name: str) -> int:
        """Revoke every link to a file. Returns how many there were."""
        shares = self._shares()
        left = {t: s for t, s in shares.items() if s["name"] != Path(str(name)).name}
        if len(left) != len(shares):
            self._save_shares(left)
        return len(shares) - len(left)

    def shared(self, token: str) -> Path | None:
        """The file a link opens, or None (unknown, revoked, expired, or the file is gone)."""
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
            return None
        for t, s in self._shares().items():
            if secrets.compare_digest(t, token):
                if s["expires_at"] <= self.clock():
                    return None
                try:
                    return self._file(s["name"])
                except LibraryFileError:
                    return None
        return None
