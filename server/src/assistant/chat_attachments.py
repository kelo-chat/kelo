"""Files the user sent with a chat message (backlog 26): kept with the message, so the bubble shows the picture's thumbnail (or a
file chip) after a reload and on every device, like chat_images.py does for Kelo's own pictures.

The app uploads each file first (PUT /api/uploads/{name}), then sends the message with the list:

    POST /api/talk | /api/turns  {"text": "photo.jpg 내용을 읽고 요약해줘.", "attachments": [{"path": "uploads/photo.jpg"}], "auto_text": true}

- `attachments`: at most 10, each an upload of the agent's files ("uploads/<name>", as PUT /api/uploads answered); an unknown one is
  refused (422) before anything is stored.
- `auto_text`: the text is the app's own "read and summarise" sentence (nothing was typed). It still goes to the agent (the names
  are how it finds the files); apps hide it and show only the attachments.

CHAT ATTACHMENTS API:
GET /api/messages rows (user messages): "attachments": [{"name", "path", "kind": image|video|audio|document, "mime", "size",
    "url": "/api/uploads/<name>/file", "thumb": "/api/uploads/<name>/thumb" | null (pictures and videos only)}] and "auto_text": bool.
    Both are read with the signed-in API (the same auth as every /api/uploads call).
Turn event (first event of a reply to a message with attachments): {"type": "user_message", "message_id", "attachments": [...],
    "auto_text": bool}.
Clearing the chat removes the list (the uploaded files stay in the user's files, as before).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .media import kind_of, mime_of
from .owner_messages import OWNER

SCHEMA = """
CREATE TABLE IF NOT EXISTS message_attachments (
    message_id INTEGER NOT NULL, idx INTEGER NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'file',
    mime TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0, auto_text INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (message_id, idx));
"""
MAX_PER_MESSAGE = 10
THUMB_KINDS = ("image", "video")


class AttachmentError(ValueError):
    """An attachment that is not one of the user's uploads (the message is refused, 422)."""


class ChatAttachments:
    def __init__(self, store: Any, home: Any = None):
        self.store, self.home = store, home
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def check(self, paths: list[str]) -> list[dict[str, Any]]:
        """The uploads a message names, checked before it is stored: [{"name", "path", "kind", "mime", "size"}]."""
        if len(paths) > MAX_PER_MESSAGE:
            raise AttachmentError(f"at most {MAX_PER_MESSAGE} attachments per message")
        if paths and self.home is None:
            raise AttachmentError("this server keeps no files")
        out = []
        for raw in paths:
            name = str(raw or "").strip().removeprefix("uploads/")
            if not name or "/" in name or name.startswith("."):
                raise AttachmentError(f"not an upload: {raw!r}")
            try:
                path = self.home.upload_path(name)
            except Exception:   # noqa: BLE001 - HomeError: no such upload
                raise AttachmentError(f"no such upload: {name}") from None
            out.append({"name": path.name, "path": f"uploads/{path.name}", "kind": kind_of(path.name), "mime": mime_of(path.name),
                        "size": path.stat().st_size})
        return out

    def owner_attached(self, path: str) -> bool:
        """The upload `path` ("uploads/<name>") is a file the OWNER attached to their own chat message (not one a forward or Kelo's own
        words carried): what they gave the assistant, not outside text. False when unsure."""
        try:
            name = str(path or "").strip().removeprefix("uploads/")
            if not name or "/" in name:
                return False
            rows = self.store._all("SELECT 1 FROM message_attachments a JOIN messages m ON m.id = a.message_id "
                                   "WHERE a.path = ? AND m.role = 'user' AND m.author = ? LIMIT 1", (f"uploads/{name}", OWNER))
            return bool(rows)
        except Exception:  # noqa: BLE001 - unsure: the file counts as outside text
            return False

    def attach(self, message_id: int, items: list[dict[str, Any]], auto_text: bool = False) -> list[dict[str, Any]]:
        for i, it in enumerate(items):
            self.store._run("INSERT OR REPLACE INTO message_attachments(message_id, idx, name, path, kind, mime, size, auto_text) "
                            "VALUES(?,?,?,?,?,?,?,?)", (message_id, i, it["name"], it["path"], it["kind"], it["mime"], it["size"],
                                                        int(auto_text)))
        return [self.public(it) for it in items]

    @staticmethod
    def public(it: dict[str, Any]) -> dict[str, Any]:
        q = quote(it["name"], safe="")
        return {"name": it["name"], "path": it["path"], "kind": it["kind"], "mime": it["mime"], "size": it["size"],
                "url": f"/api/uploads/{q}/file", "thumb": f"/api/uploads/{q}/thumb" if it["kind"] in THUMB_KINDS else None}

    def for_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [r["id"] for r in rows if r.get("role") == "user"]
        if not ids:
            return rows
        got: dict[int, list[dict[str, Any]]] = {}
        auto: dict[int, bool] = {}
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            for a in self.store._all(f"SELECT * FROM message_attachments WHERE message_id IN ({','.join('?' * len(part))}) "
                                     "ORDER BY message_id, idx", tuple(part)):
                got.setdefault(a["message_id"], []).append(self.public(a))
                auto[a["message_id"]] = bool(a["auto_text"])
        return [{**r, "attachments": got.get(r["id"], []), "auto_text": auto.get(r["id"], False)} if r.get("role") == "user" else r
                for r in rows]
