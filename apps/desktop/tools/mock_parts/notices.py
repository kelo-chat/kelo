"""Sample operator notices for 마이 → 공지 (GET /api/notices?lang=, POST /api/notices/{id}/read; shapes as the server's notices.py).

Off unless KELO_MOCK_NOTICES is set (an older server: every path answers 404), so the other screenshots keep their My page.
One info and one important notice, in Korean and English; a read notice goes away from the list.
"""

from __future__ import annotations

import os
import threading

LOCK = threading.Lock()
READ: set = set()

NOTICES = {
    "ko": [
        {"id": "n-important-1", "date": "2026-11-15", "level": "important",
         "title": "보안 업데이트가 나왔어요. 서버를 0.10.0으로 올려 주세요",
         "body": "비밀번호 보관 기능의 보안을 높이는 수정이 들어 있어요.\n마이 → 서버 업데이트에서 바로 할 수 있고, 대화는 그대로 남아요.",
         "link": "https://kelo.chat/download"},
        {"id": "n-info-1", "date": "2026-11-10", "level": "info",
         "title": "11월 20일 새벽 서버 점검 안내",
         "body": "새벽 2시부터 30분 동안 서버 점검이 있어요. 그 사이에는 대화가 잠시 멈출 수 있고,\n끝나면 따로 알려 드릴게요.",
         "link": None},
    ],
    "en": [
        {"id": "n-important-1", "date": "2026-11-15", "level": "important",
         "title": "A security update is out. Please update your server to 0.10.0",
         "body": "It hardens how stored passwords are kept.\nYou can update from My → Server update. Your chats stay as they are.",
         "link": "https://kelo.chat/download"},
        {"id": "n-info-1", "date": "2026-11-10", "level": "info",
         "title": "Server maintenance on November 20 at night",
         "body": "There is maintenance from 2 a.m. for 30 minutes. Chats may pause for a moment,\nand we will tell you when it is over.",
         "link": None},
    ],
}


def route(h, method: str, path: str, q: dict, s) -> bool:
    if not path.startswith("/api/notices"):
        return False
    if not os.environ.get("KELO_MOCK_NOTICES", "").strip():
        h.send_json({"detail": "Not Found"}, 404)
        return True
    if method == "POST" and path.startswith("/api/notices/") and path.endswith("/read"):
        with LOCK:
            READ.add(path.split("/")[3])
        h.send_response(204)
        h.end_headers()
        return True
    if method == "GET" and path == "/api/notices":
        lang = q.get("lang", ["en"])[0]
        with LOCK:
            rows = [n for n in NOTICES.get(lang, NOTICES["en"]) if n["id"] not in READ]
        out = [{k: v for k, v in n.items() if v is not None} for n in rows]
        h.send_json({"notices": out})
        return True
    return False
