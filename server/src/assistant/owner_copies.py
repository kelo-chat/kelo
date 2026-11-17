"""The owner's saved copies of the files the server trusts (audit F2 and F8).

The agent can write its own files (home.write, by="agent") and so can the server's own jobs (by="system"). Some of those files hold the
owner's private values (user.md, MEMORY.md, the pages in memory/people) and the owner's notification rules (NOTIFY_PREFS.md). The privacy
guard, the outside-text rule and the notice jobs used to read them straight from disk, so an agent edit could remove a protected value
or add a contact that counts as the owner's own.

So the server keeps, in the store, the text of each such file as the OWNER last saved it (home.write(by="user"): the app's editor, or a
reset; a page the owner trashes loses its copy). Agent and system writes never change a copy. How the readers use them:
  * notice rules: the copy only (Home.policy). The file stays as notes.
  * privacy guard: the copy AND the file as it is now, so an agent edit can add a value to the check but never take one out.
  * outside-text rule: the people pages' copies only. A page the agent wrote is no owner-named contact until the owner saves it.
The first start after this change copies the files as they are then (SEEDED): there is no older owner copy to trust, so a file the
agent wrote before that start is trusted once. Nothing else is trusted from the agent's files.
"""

from __future__ import annotations

import json
from typing import Any

KEY = "owner_copy:"            # store meta: owner_copy:<path> = {"text": ...}
SEEDED = "owner_copy:seeded"   # set once, after the first start that copied the files as they were
POLICY = "NOTIFY_PREFS.md"     # the owner's notification rules (its "Do not tell me" / "hours" sections)
PROTECTED = ("user.md", "MEMORY.md", POLICY)
PEOPLE_DIR = "memory/people"


def kept(rel: str) -> bool:
    """Is `rel` one of the files whose owner copy is kept: the core files that hold private values, the rules, and each people page."""
    if rel in PROTECTED:
        return True
    return rel.startswith(PEOPLE_DIR + "/") and rel.endswith(".md") and "/" not in rel[len(PEOPLE_DIR) + 1:]


class OwnerCopies:
    """The copies, in the store (meta rows: nothing the agent's tools can reach)."""

    def __init__(self, store: Any):
        self.store = store

    def record(self, rel: str, text: str) -> None:
        """The owner saved `rel` with this text (the app's editor, or a reset)."""
        if kept(rel):
            self.store.set_meta(KEY + rel, json.dumps({"text": text}, ensure_ascii=False))

    def forget(self, rel: str) -> None:
        """The owner moved `rel` (a page, or a folder of pages) to the trash: its copies go with it."""
        self.store.set_meta(KEY + rel, "")
        for sub in self.store.meta_prefix(KEY + rel + "/"):
            self.store.set_meta(KEY + rel + "/" + sub, "")

    def text(self, rel: str) -> str:
        """The owner's copy of `rel`, "" when there is none (never saved, or moved to the trash)."""
        raw = self.store.meta(KEY + rel)
        if not raw:
            return ""
        try:
            return str(json.loads(raw).get("text") or "")
        except ValueError:
            return ""

    def people(self) -> dict[str, str]:
        """{path: text} of every people page the owner saved (memory/people/<name>.md)."""
        out: dict[str, str] = {}
        prefix = KEY + PEOPLE_DIR + "/"
        for sub in self.store.meta_prefix(prefix):
            text = self.text(PEOPLE_DIR + "/" + sub)
            if text:
                out[PEOPLE_DIR + "/" + sub] = text
        return out

    def seed(self, home: Any) -> None:
        """Once: a file that has no copy yet gets its copy from the file as it is now (see the module doc)."""
        if self.store.meta(SEEDED):
            return
        for rel in (*PROTECTED, *(p["path"] for p in home.people())):
            if self.store.meta(KEY + rel):
                continue
            try:
                self.record(rel, home.read(rel))
            except Exception:  # noqa: BLE001 - a file that is not there has nothing to copy yet
                continue
        self.store.set_meta(SEEDED, "1")
