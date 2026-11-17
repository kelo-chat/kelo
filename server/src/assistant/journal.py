"""The journal: once a day the conversation of each earlier day is written up as 1-3 short entries (title + summary) by the user's
main model, whichever provider that is. Days without messages are skipped; a day whose write-up failed is marked and can be
written again from the app (or by the next night's run, within LOOKBACK_DAYS). The agent reads it back with `journal_search`.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .i18n import t
from .providers import ChatRequest, TextDelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS journal_date ON journal(date);
CREATE TABLE IF NOT EXISTS journal_days (date TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
"""

LOOKBACK_DAYS = 7          # the daily job looks this far back for days not written yet
MAX_INPUT_CHARS = 12_000
PROMPT = ("You keep a short journal of the days a user spent with their personal assistant. Below is one day of their conversation. "
          "Write 1 to 3 entries about what mattered that day (what the user did, decided, felt or got done), in the user's language, "
          "warm and concrete, never inventing anything. Reply with JSON only: "
          '[{"title": "at most 20 characters", "summary": "one or two sentences, at most 200 characters"}]')


def parse_entries(text: str) -> list[dict[str, str]]:
    m = re.search(r"\[.*\]", text or "", re.S)
    items: list[dict[str, str]] = []
    if m:
        try:
            data = json.loads(m.group(0))
            for it in data if isinstance(data, list) else []:
                if isinstance(it, dict) and str(it.get("summary") or "").strip():
                    items.append({"title": str(it.get("title") or "").strip()[:40] or t("A day"), "summary": str(it["summary"]).strip()[:400]})
        except ValueError:
            items = []
    if not items and (body := (text or "").strip()):
        items = [{"title": t("A day"), "summary": body[:400]}]
    return items[:3]


class Journal:
    def __init__(self, store: Any, stream_fn: Callable[..., AsyncIterator[Any]], model: Callable[[], tuple[str, str] | None],
                 key_args: Callable[[str], dict[str, Any]] = lambda _p: {}, clock: Callable[[], float] = time.time):
        self.store, self.stream_fn, self.model, self.key_args, self.clock = store, stream_fn, model, key_args, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.store.profile().get("timezone") or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def _bounds(self, date: str) -> tuple[float, float]:
        d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=self.tz())
        return d.timestamp(), (d + timedelta(days=1)).timestamp()

    def messages_of(self, date: str) -> list[dict[str, Any]]:
        a, b = self._bounds(date)
        from .secret_guard import scrub_rows   # a message sent with a secret in it (after the warning) is read without it
        return scrub_rows(self.store, self.store._all(
            "SELECT id, role, content, created_at FROM messages WHERE created_at >= ? AND created_at < ? ORDER BY id", (a, b)))

    def _set_day(self, date: str, status: str, reason: str = "") -> None:
        self.store._run("INSERT INTO journal_days(date, status, reason, updated_at) VALUES(?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
                        "status = excluded.status, reason = excluded.reason, updated_at = excluded.updated_at", (date, status, reason, self.clock()))

    def day_status(self, date: str) -> str:
        rows = self.store._all("SELECT status FROM journal_days WHERE date = ?", (date,))
        return rows[0]["status"] if rows else ""

    async def write_day(self, date: str) -> dict[str, Any]:
        """Write (or rewrite) one day. Returns {"date", "status": done|empty|pending|failed, "reason", "items"}."""
        datetime.strptime(date, "%Y-%m-%d")
        msgs = self.messages_of(date)
        if not msgs:
            self._set_day(date, "empty")
            return {"date": date, "status": "empty", "reason": "", "items": []}
        chosen = self.model()
        if not chosen:
            self._set_day(date, "pending", "no model is set")
            return {"date": date, "status": "pending", "reason": "no model is set", "items": self.entries(date=date)}
        provider, model = chosen
        lines, size = [], 0
        for m in msgs:
            if m["role"] == "user" and str(m["content"]).startswith(("[Handoff from a background job", "[Handoff from a watcher")):
                who = "Background job report"           # not the user's words
            else:
                who = "User" if m["role"] == "user" else "Assistant"
            text = re.sub(r"^\s*\[[^\]]*\]\s*\[client_timezone=[^\]]*\]\s*", "", str(m["content"]))
            line = f"{who}: {text.strip()[:500]}"
            if size + len(line) > MAX_INPUT_CHARS:
                break
            lines.append(line)
            size += len(line)
        req = ChatRequest(model=model, messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": f"Date: {date}\n\n" + "\n".join(lines)}],
                          tools=[], extra={})
        parts: list[str] = []
        try:
            async for ev in self.stream_fn(provider, req, **self.key_args(provider)):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
        except Exception as e:  # noqa: BLE001
            reason = f"{type(e).__name__}: {str(e)[:160]}"
            self._set_day(date, "failed", reason)
            return {"date": date, "status": "failed", "reason": reason, "items": self.entries(date=date)}
        items = parse_entries("".join(parts))
        if not items:
            self._set_day(date, "failed", "the model gave no entries")
            return {"date": date, "status": "failed", "reason": "the model gave no entries", "items": self.entries(date=date)}
        self.store._run("DELETE FROM journal WHERE date = ?", (date,))
        now = self.clock()
        for it in items:
            self.store._run("INSERT INTO journal(date, title, summary, created_at) VALUES(?,?,?,?)", (date, it["title"], it["summary"], now))
        self._set_day(date, "done")
        return {"date": date, "status": "done", "reason": "", "items": self.entries(date=date)}

    async def daily(self) -> list[dict[str, Any]]:
        """The daily job: every earlier day of the last week that has messages and is not written yet."""
        today = datetime.fromtimestamp(self.clock(), self.tz()).date()
        out = []
        for back in range(LOOKBACK_DAYS, 0, -1):
            date = (today - timedelta(days=back)).isoformat()
            if self.day_status(date) in ("done", "empty"):
                continue
            out.append(await self.write_day(date))
        return out

    def entries(self, month: str | None = None, date: str | None = None) -> list[dict[str, Any]]:
        if date:
            rows = self.store._all("SELECT * FROM journal WHERE date = ? ORDER BY id", (date,))
        else:
            rows = self.store._all("SELECT * FROM journal WHERE date LIKE ? ORDER BY date DESC, id", (f"{month}-%",))
        return [{"id": r["id"], "date": r["date"], "title": r["title"], "summary": r["summary"], "created_at": r["created_at"]} for r in rows]

    def pending(self, month: str) -> list[dict[str, str]]:
        return [{"date": r["date"], "status": r["status"], "reason": r["reason"]}
                for r in self.store._all("SELECT * FROM journal_days WHERE date LIKE ? AND status IN ('pending', 'failed') ORDER BY date DESC",
                                         (f"{month}-%",))]

    def delete(self, entry_id: int) -> bool:
        return self.store._run("DELETE FROM journal WHERE id = ?", (entry_id,)).rowcount > 0

    def search(self, *, start: str = "", end: str = "", keyword: str = "", limit: int = 30) -> list[dict[str, Any]]:
        """Entries between `start` and `end` (YYYY-MM-DD, both included; either may be empty) whose title or summary contains
        `keyword` (case-insensitive; empty = any), oldest first, at most `limit`. Raises ValueError for a malformed date."""
        for d in (start, end):
            if d:
                datetime.strptime(d, "%Y-%m-%d")
        where, args = [], []
        if start:
            where.append("date >= ?")
            args.append(start)
        if end:
            where.append("date <= ?")
            args.append(end)
        if keyword.strip():
            where.append("(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')")
            pat = "%" + re.sub(r"([\\%_])", r"\\\1", keyword.strip()) + "%"
            args += [pat, pat]
        sql = "SELECT * FROM journal" + (" WHERE " + " AND ".join(where) if where else "")
        # newest `limit` matches, shown oldest first
        rows = self.store._all(sql + " ORDER BY date DESC, id DESC LIMIT ?", (*args, max(1, min(int(limit), 100))))[::-1]
        return [{"id": r["id"], "date": r["date"], "title": r["title"], "summary": r["summary"]} for r in rows]


def journal_tools(journal: Journal) -> list[Any]:
    """`journal_search`: the agent reads its own journal (read-only), so "what did I do last week?" can be answered."""
    from .agent import Tool

    async def search(args: dict[str, Any]) -> str:
        date = str(args.get("date") or "").strip()
        start, end = (date, date) if date else (str(args.get("from") or "").strip(), str(args.get("to") or "").strip())
        keyword = str(args.get("keyword") or "")
        try:
            items = journal.search(start=start, end=end, keyword=keyword, limit=int(args.get("limit") or 30))
        except ValueError:
            return "error: dates must look like 2026-11-06"
        today = datetime.fromtimestamp(journal.clock(), journal.tz()).date().isoformat()
        if not items:
            return (f"No journal entries{f' from {start}' if start else ''}{f' to {end}' if end else ''}"
                    f"{f' about {keyword!r}' if keyword.strip() else ''}. Each day is written up the night after it (today, {today}, is "
                    "not in the journal yet); use the conversation for today.")
        return "\n".join(f"- {i['date']} · {i['title']}: {i['summary']}" for i in items)

    return [Tool(
        name="journal_search",
        description="Search your journal of earlier days with the user (short entries written each night): by day, range or keyword.",
        parameters={"type": "object", "properties": {
            "date": {"type": "string", "description": "one day, YYYY-MM-DD"},
            "from": {"type": "string", "description": "first day, included"},
            "to": {"type": "string", "description": "last day, included"},
            "keyword": {"type": "string"},
            "limit": {"type": "integer", "description": "default 30"}}},
        handler=search, read_only=True, trail="memory")]
