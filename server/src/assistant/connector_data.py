"""What came from one connector, for the question Kelo asks when the owner disconnects it: "also delete the N files and M items that came
from it?". Only data whose origin is RECORDED is counted or deleted; nothing is guessed from text.

Traceable today (connector id = the connection's name, e.g. "mail-gmail", as DELETE /api/credentials/{name} uses it):
- files: library files saved by connector_download (deliverables.py keeps `origin` per file in .origins.json). Deleting one moves it to
  the library's trash (kept TRASH_DAYS days, restorable), never destroys it.
- items: proactive_log rows whose key is "mail:<connector id>:..." (the alerts and mail decisions made from that mailbox). Their text
  is cleared with the row.

Not traceable today (counted as 0 and left alone): memory notes (memory/inbox.md lines carry only a chat message id; filed memory
pages have no origin), follow items (source is only "mail" / "calendar" / "order", with no connector), calendar entries and
chat images. "notes" stays in the summary so the apps have a stable shape; it is 0 until a note origin is recorded.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .deliverables import Deliverables
from .home import Home


class ConnectorData:
    def __init__(self, store: Any, home: Home | None):
        self.store = store
        self.home = home
        self.library = Deliverables(home) if home is not None else None

    # ------------------------------------------------------------------ counting
    def _file_names(self, connector_id: str) -> list[str]:
        if self.library is None:
            return []
        return sorted(name for name, cid in self.library.origins().items()
                      if cid == connector_id and (self.library.dir / name).is_file() and not (self.library.dir / name).is_symlink())

    @staticmethod
    def _prefix(connector_id: str) -> str:
        return f"mail:{connector_id}:"

    def _item_rows(self, connector_id: str) -> int:
        try:
            return int(self.store._all("SELECT COUNT(*) AS n FROM proactive_log WHERE substr(key, 1, ?) = ?",
                                       (len(self._prefix(connector_id)), self._prefix(connector_id)))[0]["n"])
        except sqlite3.OperationalError:   # no alert log in this installation yet
            return 0

    def summary(self, connector_id: str) -> dict[str, int]:
        """{"files": n, "notes": n, "items": n}: how much of what came from this connector is still kept. Unknown connector: zeros."""
        cid = str(connector_id or "")
        if not cid:
            return {"files": 0, "notes": 0, "items": 0}
        return {"files": len(self._file_names(cid)), "notes": 0, "items": self._item_rows(cid)}

    # ------------------------------------------------------------------ deleting
    def forget(self, connector_id: str) -> dict[str, int]:
        """Delete what came from this connector and nothing else. Files go to the library's trash. Returns the counts deleted."""
        cid = str(connector_id or "")
        if not cid:
            return {"files": 0, "notes": 0, "items": 0}
        files = 0
        for name in self._file_names(cid):
            self.library.trash(name)   # moved to the trash (the origin moves with it), restorable for TRASH_DAYS days
            files += 1
        # origins whose file is already gone are dropped too, so the index does not keep a connector's name for nothing
        if self.library is not None:
            index = self.library.origins()
            stale = [n for n, c in index.items() if c == cid]
            if stale:
                for n in stale:
                    index.pop(n, None)
                self.library._save_origins(index)
        items = 0
        try:
            items = self.store._run("DELETE FROM proactive_log WHERE substr(key, 1, ?) = ?",
                                    (len(self._prefix(cid)), self._prefix(cid))).rowcount
        except sqlite3.OperationalError:
            items = 0
        return {"files": files, "notes": 0, "items": max(items, 0)}
