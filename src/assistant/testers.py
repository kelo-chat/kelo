"""Sign-ups for the Google Play closed test (backlog 86): kelo.chat/tester takes a Google account email (the one used on Google Play),
an optional phone model and a consent tick; the operator hears at once by email ("[Kelo Pre-Test] ...") sent through the Gmail mailbox
connected on the operator server (the beta emails' way, server.py beta_mail), to SUPPORT. Only the operator's own server takes them.

What is kept: email, model, language, created_at, mailed (bool) in tester_signups, one row per email (a repeat updates the model and is
not mailed again), deleted after KEEP_DAYS days or on request. No IP address is stored: it lives only in the in-memory rate counter.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from .i18n import t

log = logging.getLogger("uvicorn.error")

Mailer = Callable[[str, str, str, str], Awaitable[str]]   # (to, subject, body, reply_to) -> what the mailbox said
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,24}$")
SUPPORT = "support@kelo.chat"
SUBJECT_TAG = "[Kelo Pre-Test]"
KEEP_DAYS = 180
MODEL_MAX = 60
PER_IP_HOUR = 10
MAX_ROWS = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS tester_signups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, model TEXT NOT NULL DEFAULT '', lang TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, mailed INTEGER NOT NULL DEFAULT 0);
"""


class SignupError(ValueError):
    """`code`: why the sign-up was not taken (the page shows its own words for it)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TesterDesk:
    __test__ = False

    def __init__(self, store: Any, mailer: Mailer | None, wall: Callable[[], float] = time.time,
                 notify: Callable[[str, str], Any] | None = None) -> None:
        """`notify(title, body)`: an app notification to the operator for each new sign-up, next to the mail (Gmail hides a mail its
        own account sent to an address that forwards back to it)."""
        self.store, self.mailer, self.wall, self.notify = store, mailer, wall, notify
        self._hits: dict[str, deque[float]] = {}
        store._db.executescript(SCHEMA)

    def _limited(self, ip: str) -> bool:
        now = self.wall()
        q = self._hits.setdefault(ip, deque())
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= PER_IP_HOUR:
            return True
        q.append(now)
        if len(self._hits) > 10_000:                     # the counter never grows without bound
            self._hits.clear()
        return False

    def _prune(self) -> None:
        self.store._run("DELETE FROM tester_signups WHERE created_at < ?", (self.wall() - KEEP_DAYS * 86400,))

    def count(self) -> int:
        return int(self.store._all("SELECT COUNT(*) AS n FROM tester_signups", ())[0]["n"])

    async def sign_up(self, email: str, model: str, lang: str, consent: bool, ip: str, trap: str = "") -> bool:
        """True when it is a new sign-up (the owner was emailed or will be); False for a repeat of a known email.
        SignupError("invalid" | "consent" | "limited" | "full") otherwise. `trap`: a hidden field only bots fill (then it pretends
        success and keeps nothing)."""
        if trap:
            return True
        email = (email or "").strip()
        if not EMAIL_RE.match(email):
            raise SignupError("invalid")
        if not consent:
            raise SignupError("consent")
        if self._limited(ip or "?"):
            raise SignupError("limited")
        self._prune()
        model = " ".join((model or "").split())[:MODEL_MAX]
        known = self.store._all("SELECT id FROM tester_signups WHERE lower(email) = lower(?)", (email,))
        if known:
            self.store._run("UPDATE tester_signups SET model = ? WHERE id = ?", (model, known[0]["id"]))
            return False
        if self.count() >= MAX_ROWS:
            raise SignupError("full")
        rid = int(self.store._run("INSERT INTO tester_signups(email, model, lang, created_at) VALUES(?,?,?,?)",
                                  (email, model, (lang or "")[:8], self.wall())).lastrowid)
        if self.notify is not None:
            try:
                self.notify(t("New tester sign-up: {email}", email=email),
                            t("Phone: {model} · {count} signed up so far", model=model or "-", count=self.count()))
            except Exception:  # noqa: BLE001 - the sign-up and its mail never depend on the notification
                log.warning("tester sign-up %s: the notification failed", rid)
        await self._mail(rid, email, model, lang)
        return True

    async def _mail(self, rid: int, email: str, model: str, lang: str) -> None:
        if self.mailer is None:
            log.warning("tester sign-up %s: no mailer on this server", rid)
            return
        n = self.count()
        subject = f"{SUBJECT_TAG} 새 테스터 신청: {email}"
        body = "\n".join([
            "kelo.chat/tester 에서 새 테스터 신청이 왔어요.",
            "",
            f"이메일: {email}",
            f"휴대폰: {model or '(적지 않음)'}",
            f"언어: {lang or '-'}",
            f"지금까지 신청: {n}명",
            "",
            "Play Console → 테스트 및 출시 → 비공개 테스트 → 테스터 → 이메일 목록에 이 주소를 넣고, 참여 링크를 보내 주세요.",
        ])
        try:
            await self.mailer(SUPPORT, subject, body, email)
            self.store._run("UPDATE tester_signups SET mailed = 1 WHERE id = ?", (rid,))
        except Exception as e:  # noqa: BLE001 - the sign-up is kept even when the mail fails; it is logged without the address
            log.warning("tester sign-up %s: the email could not be sent (%s)", rid, type(e).__name__)
