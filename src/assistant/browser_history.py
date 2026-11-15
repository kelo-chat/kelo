"""A picture of every browser step of the runs nobody watches (background jobs "task:*" and automatic runs "schedule:*"), for looking back
at what the agent did there ("audit / replay").

Per tab, in `<state>/browser-history/<tab>/`: `NNNNNN.jpg` (≤ 800 px wide, ≤ FRAME_BYTES) and `manifest.jsonl`, one line per step:
{"seq", "at", "url", "title", "action" (the tool), "detail" (where: the address opened, the element clicked; never typed text or codes),
 "task_id", "bytes", "width", "height"}. Kept: the last MAX_FRAMES steps and at most MAX_BYTES per tab; a step older than KEEP_DAYS is
deleted (with its run's task, which the task log forgets on its own schedule), checked at most once an hour.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response

MAX_FRAMES = 300
MAX_BYTES = 60 * 1024**2
KEEP_DAYS = 14
FRAME_WIDTH = 800
FRAME_BYTES = 150_000
PURGE_EVERY = 3600.0


def safe_dir(key: str) -> str:
    """A tab key as a folder name ("task:w1a2" -> "task_w1a2"); only the tab kinds that are archived."""
    if not re.fullmatch(r"(task|schedule):[A-Za-z0-9_\-]{1,64}", key or ""):
        raise ValueError("not an archived tab")
    return key.replace(":", "_")


class ActionArchive:
    def __init__(self, root: str | Path, *, max_frames: int = MAX_FRAMES, max_bytes: int = MAX_BYTES, keep_days: float = KEEP_DAYS,
                 clock: Any = time.time):
        self.root = Path(root)
        self.max_frames, self.max_bytes, self.keep = max_frames, max_bytes, keep_days * 86400
        self.clock = clock
        self._last_purge = 0.0

    def _dir(self, key: str) -> Path:
        return self.root / safe_dir(key)

    def _rows(self, d: Path) -> list[dict[str, Any]]:
        try:
            lines = (d / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("seq"), int):
                out.append(r)
        return out

    def _write(self, d: Path, rows: list[dict[str, Any]]) -> None:
        tmp = d / "manifest.jsonl.tmp"
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(d / "manifest.jsonl")

    def record(self, key: str, jpeg: bytes | None, *, url: str, title: str, action: str, detail: str = "", task_id: int | None = None,
               width: int = 0, height: int = 0) -> dict[str, Any] | None:
        """Keep one step (with or without a picture). Returns its row, or None for a tab that is not archived."""
        try:
            d = self._dir(key)
        except ValueError:
            return None
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        rows = self._rows(d)
        seq = (rows[-1]["seq"] + 1) if rows else 1
        row = {"seq": seq, "at": self.clock(), "url": url[:500], "title": title[:200], "action": action[:60], "detail": detail[:200],
               "task_id": task_id, "bytes": len(jpeg) if jpeg else 0, "width": width if jpeg else 0, "height": height if jpeg else 0}
        if jpeg:
            (d / f"{seq:06d}.jpg").write_bytes(jpeg)
        rows.append(row)
        rows = self._trim(d, rows)
        self._write(d, rows)
        if self.clock() - self._last_purge > PURGE_EVERY:
            self.purge()
        return row

    def _trim(self, d: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = self.clock()
        keep = [r for r in rows if now - float(r.get("at") or 0) < self.keep]
        while len(keep) > self.max_frames or sum(int(r.get("bytes") or 0) for r in keep) > self.max_bytes:
            keep.pop(0)
        gone = {r["seq"] for r in rows} - {r["seq"] for r in keep}
        for seq in gone:
            (d / f"{seq:06d}.jpg").unlink(missing_ok=True)
        return keep

    def purge(self) -> int:
        """Drop steps older than KEEP_DAYS everywhere; a tab with none left goes entirely. Returns the folders removed."""
        self._last_purge = self.clock()
        removed = 0
        if not self.root.is_dir():
            return 0
        for d in self.root.iterdir():
            if not d.is_dir() or d.is_symlink():
                continue
            rows = self._rows(d)
            keep = self._trim(d, rows)
            if not keep:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
            elif len(keep) != len(rows):
                self._write(d, keep)
        return removed

    def history(self, key: str, *, before: int | None = None, limit: int = 50) -> dict[str, Any]:
        """Newest first, `limit` (1..100) steps with seq < `before`."""
        try:
            d = self._dir(key)
        except ValueError:
            return {"id": key, "items": [], "total": 0, "bytes": 0, "next_before": None}
        now = self.clock()
        rows = [r for r in self._rows(d) if now - float(r.get("at") or 0) < self.keep]
        limit = max(1, min(int(limit or 50), 100))
        page = [r for r in reversed(rows) if before is None or r["seq"] < before][:limit]
        items = [{**r, "frame": f"/api/browser/sessions/{key}/history/{r['seq']}" if r.get("bytes") else None} for r in page]
        more = bool(page) and any(r["seq"] < page[-1]["seq"] for r in rows)
        return {"id": key, "items": items, "total": len(rows), "bytes": sum(int(r.get("bytes") or 0) for r in rows),
                "next_before": page[-1]["seq"] if more else None}

    def frame(self, key: str, seq: int) -> bytes | None:
        try:
            p = self._dir(key) / f"{int(seq):06d}.jpg"
        except (ValueError, TypeError):
            return None
        if p.is_symlink() or not p.is_file():
            return None
        return p.read_bytes()

    def delete(self, key: str) -> bool:
        try:
            d = self._dir(key)
        except ValueError:
            return False
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            return True
        return False

    def clear(self) -> int:
        """Every tab's pictures and steps (clearing the chat history). Returns the pictures deleted."""
        pictures = 0
        if not self.root.is_dir():
            return 0
        for d in self.root.iterdir():
            if d.is_symlink() or not d.is_dir():
                if d.is_symlink() or d.is_file():
                    d.unlink(missing_ok=True)
                continue
            pictures += sum(1 for p in d.glob("*.jpg") if p.is_file())
            shutil.rmtree(d, ignore_errors=True)
        return pictures


def history_routes(app: FastAPI, archive: ActionArchive | None, require_auth: Any) -> None:
    """GET /api/browser/sessions/{id}/history?before=&limit=   GET /api/browser/sessions/{id}/history/{seq}   (contract in server.py)"""

    def need() -> ActionArchive:
        if archive is None:
            raise HTTPException(404, "this server keeps no browser history")
        return archive

    @app.get("/api/browser/sessions/{key}/history", dependencies=[Depends(require_auth)])
    async def get_history(key: str, before: int | None = None, limit: int = 50) -> dict[str, Any]:
        return need().history(key, before=before, limit=limit)

    @app.get("/api/browser/sessions/{key}/history/{seq}", dependencies=[Depends(require_auth)])
    async def get_history_frame(key: str, seq: int) -> Response:
        data = need().frame(key, seq)
        if data is None:
            raise HTTPException(404, "no such picture")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})
