"""What can safely be removed to free storage, for the app's cleanup screen and for the agent (which must ask first).

Nothing here deletes anything by itself. The app lists the groups with sizes and dates, the owner ticks items and confirms; the agent
can list the same groups (`storage_cleanup_list`) and delete only through `storage_cleanup`, which always shows an approval card.

Groups (each item: {"id": "<group>:<key>", "name", "bytes", "date"} plus what is noted below):
  uploads          upload originals not touched for OLD_DAYS days. Only the original goes: the text read from a document
                   (notes/extracted/<name>.md) stays, so the assistant still knows what was in it ("keeps_text": true).
  images           pictures the assistant generated (.tryon/). An old chat card that showed one shows nothing after.
  browser_history  the step pictures of unattended browser runs (browser-history/<tab>/), except a tab written in the last hour
                   (it may still be running). The browser's own downloads are not offered: the server only reads that folder, and
                   the browser container clears it after a day by itself.
  results          files in the library (files/) not touched for OLD_DAYS days, and everything in its trash ("trashed": true).
                   Removed for good (not moved to the trash).
  backups          ("kind": update | memory | migration; "name": the version an update backup was taken from, "" for the others)
                   the update backups beyond the newest KEEP_UPDATE_BACKUPS (/state/.backups,
                   updates.py; none while an update or restore is writing one), the memory pass backups beyond the newest
                   KEEP_MEMORY_PASSES (.memory-passes, memory_upkeep.py; only the last pass can be undone anyway) and the
                   folders older layouts were moved out of beyond the newest one (.backup, home.py).
Never offered and never deleted, whatever id is sent: the database (assistant.db*), notes and memory (notes/, memory/,
reflection/, the core files, their .history), skills, the vault. A delete re-lists the groups first and removes only ids that are on
that list; anything else is skipped.

GET  /api/storage/cleanup                         -> {"groups": [{"id", "bytes", "count", "items": [...]}], "total", "older_than_days"}
POST /api/storage/cleanup {"items": [id], "confirm": true}
     -> {"removed": [id], "skipped": [id], "freed": bytes, "groups": [...] (the list after), "total"}; 400 without confirm
Both: any signed-in device (not on a review copy).
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .i18n import N_, t
from .quota import fmt_size

OLD_DAYS = 14
KEEP_UPDATE_BACKUPS = 2
KEEP_MEMORY_PASSES = 3
KEEP_MIGRATION_BACKUPS = 1
BUSY_SECONDS = 3600            # a browser history folder written this recently may still be in use
MAX_ITEMS = 300                # per group in one answer (biggest first)
GROUPS = ("uploads", "images", "browser_history", "results", "backups")
GROUP_NAMES = {"uploads": N_("Old upload originals"), "images": N_("Generated images"), "browser_history": N_("Browser step pictures"),
               "results": N_("Old results and trash"), "backups": N_("Older backups")}
PROTECTED_TOP = ("notes", "memory", "reflection", "skills", ".history", "people")


def _size(p: Path) -> int:
    if p.is_symlink():
        return 0
    if p.is_file():
        return p.stat().st_size
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def _newest(p: Path) -> float:
    try:
        best = p.stat().st_mtime
    except OSError:
        return 0.0
    if p.is_dir():
        for f in p.rglob("*"):
            try:
                best = max(best, f.stat().st_mtime)
            except OSError:
                continue
    return best


class Cleanup:
    def __init__(self, *, home: Any = None, images: Any = None, results: Any = None, state_root: str | Path | None = None,
                 quota: Any = None, clock: Callable[[], float] = time.time, old_days: int = OLD_DAYS):
        self.home, self.images, self.results, self.quota, self.clock, self.old_days = home, images, results, quota, clock, old_days
        self.state = Path(state_root) if state_root else None
        # where each kind of item lives (paths are checked against these on delete)
        self._items: dict[str, tuple[str, Path, dict[str, Any]]] = {}

    # ------------------------------------------------------------------ listing
    def _add(self, out: dict[str, list[dict[str, Any]]], group: str, key: str, name: str, path: Path, nbytes: int, date: float,
             **extra: Any) -> None:
        iid = f"{group}:{key}"
        self._items[iid] = (group, path, extra)
        out[group].append({"id": iid, "name": name, "bytes": int(nbytes), "date": float(date), **extra})

    def _scan(self) -> dict[str, list[dict[str, Any]]]:
        now, old = self.clock(), self.clock() - self.old_days * 86400
        out: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUPS}
        self._items = {}
        home = self.home
        if home is not None:
            for u in home.uploads():
                if u["modified"] >= old:
                    continue
                p = home.root / u["path"]
                text = home.root / "notes" / "extracted" / f"{p.stem}.md"
                self._add(out, "uploads", p.name, p.name, p, u["bytes"], u["modified"], keeps_text=text.is_file() and not text.is_symlink())
        if self.images is not None:
            for name in self.images.names():
                p = self.images.path(name)
                if p is not None:
                    st = p.stat()
                    self._add(out, "images", name, name, p, st.st_size, st.st_mtime)
        if self.state is not None:
            root = self.state / "browser-history"
            if root.is_dir():
                for d in sorted(root.iterdir()):
                    if d.is_symlink() or not d.is_dir():
                        continue
                    when = _newest(d)
                    if now - when < BUSY_SECONDS:
                        continue
                    self._add(out, "browser_history", d.name, self._tab_title(d), d, _size(d), when)
        if self.results is not None:
            for f in self.results.list():
                if f["created"] < old:
                    self._add(out, "results", f["name"], f["name"], self.results.dir / f["name"], f["bytes"], f["created"], trashed=False)
            for r in self.results.trashed():
                p = self.results.trash_dir / f"{r['id']}-{r['name']}"
                self._add(out, "results", f"trash/{r['id']}", r["name"], p, r.get("bytes") or _size(p), r["deleted_at"], trashed=True)
        self._backups(out)
        for g in out:
            out[g].sort(key=lambda i: (-i["bytes"], i["date"]))
        return out

    @staticmethod
    def _tab_title(d: Path) -> str:
        """The last page a run was on ("example.com — Title"), else the folder name."""
        try:
            lines = (d / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return d.name
        for line in reversed(lines):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            title = str(r.get("title") or "").strip() if isinstance(r, dict) else ""
            url = str(r.get("url") or "").strip() if isinstance(r, dict) else ""
            if title or url:
                return (title or url)[:80]
        return d.name

    def _backups(self, out: dict[str, list[dict[str, Any]]]) -> None:
        if self.state is not None:
            b = self.state / ".backups"
            if b.is_dir():
                names = sorted(p.name for p in b.iterdir())
                writing = any(n.startswith(".") for n in names)       # .<id>.part / .restore-* / .replaced-*: an update is at work
                rows = []
                for n in names:
                    d = b / n
                    if n.startswith(".") or d.is_symlink() or not d.is_dir():
                        continue
                    try:
                        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    rows.append((float(meta.get("created") or d.stat().st_mtime), n, d, meta))
                rows.sort()
                if not writing:
                    for created, n, d, meta in rows[:-KEEP_UPDATE_BACKUPS] if len(rows) > KEEP_UPDATE_BACKUPS else []:
                        label = str(meta.get("version") or meta.get("label") or n)[:60]
                        self._add(out, "backups", f"update/{n}", label, d, int(meta.get("bytes") or _size(d)), created, kind="update")
        if self.home is not None:
            for folder, kind, keep in ((".memory-passes", "memory", KEEP_MEMORY_PASSES), (".backup", "migration", KEEP_MIGRATION_BACKUPS)):
                root = self.home.root / folder
                if not root.is_dir():
                    continue
                dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
                for d in dirs[:-keep] if len(dirs) > keep else []:
                    # no name: the folder's name is an internal id; the apps show the kind and the date
                    self._add(out, "backups", f"{kind}/{d.name}", "", d, _size(d), _newest(d), kind=kind)

    def groups(self) -> dict[str, Any]:
        found = self._scan()
        groups = [{"id": g, "name": t(GROUP_NAMES[g]), "bytes": sum(i["bytes"] for i in items), "count": len(items), "items": items[:MAX_ITEMS]}
                  for g, items in found.items()]
        return {"groups": groups, "total": sum(g["bytes"] for g in groups), "older_than_days": self.old_days}

    # ------------------------------------------------------------------ deleting (only after the owner said yes)
    def _protected(self, p: Path) -> bool:
        """The database, notes and memory: never removed, even if a path somehow pointed there."""
        try:
            rp = p.resolve()
        except OSError:
            return True
        if self.state is not None:
            st = self.state.resolve()
            if rp.parent == st and rp.name.startswith("assistant.db"):
                return True
        if self.home is not None:
            h = self.home.root.resolve()
            if rp == h:
                return True
            try:
                rel = rp.relative_to(h).parts
            except ValueError:
                rel = ()
            if rel and (rel[0] in PROTECTED_TOP or (len(rel) == 1 and rp.suffix.lower() == ".md")):
                return True
        return False

    def _inside(self, p: Path, group: str, extra: dict[str, Any]) -> bool:
        """The path is where its group's items live (one level down), nothing else."""
        roots: list[Path] = []
        if group == "uploads" and self.home is not None:
            roots = [self.home.root / "uploads"]
        elif group == "images" and self.images is not None:
            roots = [self.images.root]
        elif group == "browser_history" and self.state is not None:
            roots = [self.state / "browser-history"]
        elif group == "results" and self.results is not None:
            roots = [self.results.trash_dir if extra.get("trashed") else self.results.dir]
        elif group == "backups":
            kind = extra.get("kind")
            if kind == "update" and self.state is not None:
                roots = [self.state / ".backups"]
            elif kind == "memory" and self.home is not None:
                roots = [self.home.root / ".memory-passes"]
            elif kind == "migration" and self.home is not None:
                roots = [self.home.root / ".backup"]
        try:
            return not p.is_symlink() and any(p.resolve().parent == r.resolve() for r in roots)
        except OSError:
            return False

    def remove(self, ids: list[str]) -> dict[str, Any]:
        self._scan()                                  # only what is on the list right now
        removed, skipped, freed = [], [], 0
        trash_gone: set[str] = set()
        for iid in dict.fromkeys(str(i) for i in ids):
            item = self._items.get(iid)
            if item is None:
                skipped.append(iid)
                continue
            group, p, extra = item
            if self._protected(p) or not self._inside(p, group, extra) or not p.exists():
                skipped.append(iid)
                continue
            size = _size(p)
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except OSError:
                skipped.append(iid)
                continue
            if group == "results" and extra.get("trashed"):
                trash_gone.add(iid.split("/", 1)[1])
            removed.append(iid)
            freed += size
        if trash_gone and self.results is not None:
            self.results.forget_trashed(trash_gone)
        if any(i.startswith("uploads:") for i in removed) and self.home is not None:
            self.home.media.clear_stale([self.home.root / u["path"] for u in self.home.uploads()])
        if removed and self.quota is not None:
            self.quota.wrote()
        return {"removed": removed, "skipped": skipped, "freed": freed, **self.groups()}

    # ------------------------------------------------------------------ for the agent
    def describe(self, ids: list[str]) -> str:
        """The approval card's line: how many items, how much, a few names."""
        self._scan()
        picked = [(iid, self._items[iid]) for iid in dict.fromkeys(ids) if iid in self._items]
        total = sum(_size(p) for _, (_, p, _) in picked)
        names = ", ".join(p.name for _, (_, p, _) in picked[:4]) + (" …" if len(picked) > 4 else "")
        return t("Delete {count} items ({size}) for good: {names}", count=len(picked), size=fmt_size(total), names=names)

    def summary(self) -> str:
        data = self.groups()
        lines = [f"Can be removed: {fmt_size(data['total'])} in all (uploads and results older than {self.old_days} days, generated "
                 "images, browser step pictures, older backups). Notes, memory and the database are never offered."]
        for g in data["groups"]:
            if not g["count"]:
                continue
            lines.append(f"\n{g['id']} ({g['name']}): {g['count']} items, {fmt_size(g['bytes'])}")
            for i in g["items"][:15]:
                when = time.strftime("%Y-%m-%d", time.localtime(i["date"]))
                flags = " [text kept]" if i.get("keeps_text") else " [trash]" if i.get("trashed") else ""
                # a run's page title came from a website: the agent gets the id and date only, never outside text
                name = "" if g["id"] == "browser_history" else f"  {i['name']}"
                lines.append(f"- {i['id']}{name}  {fmt_size(i['bytes'])}  {when}{flags}")
            if g["count"] > 15:
                lines.append(f"- … {g['count'] - 15} more")
        return "\n".join(lines)


class CleanupIn(BaseModel):
    items: list[str] = Field(default_factory=list, max_length=2000)
    confirm: bool = False


def routes(app: FastAPI, cleanup: Cleanup | None, owner: Callable[..., Any]) -> None:
    def need() -> Cleanup:
        if cleanup is None:
            raise HTTPException(404, "this server keeps no files")
        return cleanup

    @app.get("/api/storage/cleanup", dependencies=[Depends(owner)])
    async def storage_cleanup_list() -> dict[str, Any]:
        return need().groups()

    @app.post("/api/storage/cleanup", dependencies=[Depends(owner)])
    async def storage_cleanup_remove(body: CleanupIn) -> dict[str, Any]:
        c = need()
        if not body.confirm:
            raise HTTPException(400, t("Nothing was deleted: confirm the deletion first."))
        if not body.items:
            raise HTTPException(400, t("Choose what to delete first."))
        return c.remove(body.items)


def cleanup_tools(cleanup: Cleanup) -> list[Any]:
    """`storage_cleanup_list` (only looks) and `storage_cleanup` (always asks the user with an approval card first)."""
    from .agent.loop import Tool

    async def list_handler(_args: dict[str, Any]) -> str:
        return cleanup.summary()

    def ids_of(args: dict[str, Any]) -> list[str]:
        items = args.get("items")
        return [str(i) for i in items] if isinstance(items, list) else []

    def validate(args: dict[str, Any]) -> str | None:
        ids = ids_of(args)
        if not ids:
            return "name the items to delete (ids from storage_cleanup_list)"
        cleanup._scan()
        unknown = [i for i in ids if i not in cleanup._items]
        return (f"not on the cleanup list (ask storage_cleanup_list again; notes, memory and the database are never deleted): "
                f"{', '.join(unknown[:5])}") if unknown else None

    async def describe(args: dict[str, Any]) -> str:
        return cleanup.describe(ids_of(args))

    async def remove_handler(args: dict[str, Any]) -> str:
        res = cleanup.remove(ids_of(args))
        out = f"Deleted {len(res['removed'])} items, freed {fmt_size(res['freed'])}."
        if res["skipped"]:
            out += f" Skipped (no longer on the list): {', '.join(res['skipped'][:10])}."
        return out

    return [
        Tool("storage_cleanup_list",
             "List what can safely be removed to free the assistant's storage or the server machine's disk: old upload originals "
             "(the text read from them stays), generated images, browser step pictures, old results and the trash, older backups. "
             "Shows ids, sizes and dates. Nothing is deleted. Notes, memory and the database are never listed.",
             {"type": "object", "properties": {}}, list_handler, read_only=True),
        Tool("storage_cleanup",
             "Delete chosen items from storage_cleanup_list for good. Suggest it only when storage is short or the user asks; say "
             "what would go and how much it frees first. The user always confirms on an approval card before anything is deleted.",
             {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"},
                                                         "description": "ids from storage_cleanup_list"}},
              "required": ["items"]},
             remove_handler, requires_approval=True, validate=validate, describe=describe,
             declined="The user chose not to delete these. Do not retry; ask what they would like instead."),
    ]


__all__ = ["Cleanup", "GROUPS", "KEEP_MEMORY_PASSES", "KEEP_UPDATE_BACKUPS", "OLD_DAYS", "cleanup_tools", "routes"]
