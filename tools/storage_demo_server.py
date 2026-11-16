#!/usr/bin/env python3
"""A real Kelo server (server/src/assistant/server.py) with a filled storage, for screenshots of the storage warning and the cleanup screen.

    uv run python tools/storage_demo_server.py --state /tmp/kelo-storage-demo --lang ko --level warn --port 18931

Everything here is the real server code; only its data and two outside facts are set up for the picture:
  - the data folder is filled with sample uploads (one with text read from it), generated images, browser step pictures of an
    unattended run, old results and the trash, update backups and memory pass backups, aged so they count as "old";
  - the storage cap is set so the share in use is 83% (--level warn) or 96% (--level critical); --level host keeps the cap low and
    says the server's own disk has 0.8 GB left (--level real: the real free space of this computer is used);
  - the owner counts as awake, so the warning notification is made at once (in --lang, the owner's last app language).
--note-after N holds the notification back N seconds (the server's minute check sends it after that).
Connect an app with the address it prints and the access key (the master token: the owner, who may clean up). Nothing outside
--state is touched; delete that folder afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server" / "src"))

TOKEN = "kelo-storage-demo-token-0001"
DAY = 86400
GB = 1024**3
Usage = namedtuple("Usage", "total used free")

MB = 1024**2
SAMPLES = {
    "ko": {"uploads": [("2025-자동차보험-증권.pdf", 24 * MB, 40, True), ("영수증-9월.jpg", 6 * MB, 52, False),
                       ("이사-견적서.pdf", 9 * MB, 33, True), ("가족여행-영상.mp4", 1210 * MB, 61, False)],
           "new_upload": ("이번주-회의록.pdf", 3 * MB),
           "big_new": "강아지-산책-영상.mp4",
           "results": [("월간-지출-정리.xlsx", 12 * MB, 45), ("여행-일정표.pdf", 6 * MB, 30)],
           "trash": ("메모-초안.docx", 4 * MB),
           "tab_title": "주문 내역 — 예시 쇼핑몰",
           "notes": "# 메모\n\n- 다음 주 화요일 치과 예약\n"},
    "en": {"uploads": [("2025-car-insurance-policy.pdf", 24 * MB, 40, True), ("receipt-september.jpg", 6 * MB, 52, False),
                       ("moving-quote.pdf", 9 * MB, 33, True), ("family-trip-video.mp4", 1210 * MB, 61, False)],
           "new_upload": ("this-week-meeting-notes.pdf", 3 * MB),
           "big_new": "dog-walk-video.mp4",
           "results": [("monthly-spending.xlsx", 12 * MB, 45), ("trip-itinerary.pdf", 6 * MB, 30)],
           "trash": ("draft-note.docx", 4 * MB),
           "tab_title": "Order history — example shop",
           "notes": "# Notes\n\n- Dentist next Tuesday\n"},
}


def _age(p: Path, days: float) -> None:
    t = time.time() - days * DAY
    for q in [p, *p.rglob("*")] if p.is_dir() else [p]:
        os.utime(q, (t, t))


def _blob(path: Path, n: int, head: bytes = b"") -> Path:
    """A file of n bytes that takes almost no disk: the header, then a hole (the storage count reads the file's size)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(head)
        f.truncate(max(n, len(head)))
    return path


def seed(state: Path, lang: str) -> None:
    s = SAMPLES[lang]
    home = state / "home"
    for name, n, days, text in s["uploads"]:
        head = b"%PDF-1.4\n" if name.endswith(".pdf") else b"\xff\xd8\xff" if name.endswith(".jpg") else b"\x00\x00\x00\x18ftypmp42"
        _age(_blob(home / "uploads" / name, n, head), days)
        if text:
            t = home / "notes" / "extracted" / f"{Path(name).stem}.md"
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(f"# {Path(name).stem}\n\n(text read from the document)\n", encoding="utf-8")
    name, n = s["new_upload"]
    _blob(home / "uploads" / name, n, b"%PDF-1.4\n")
    (home / "notes" / "todo.md").write_text(s["notes"], encoding="utf-8")
    for i, n in enumerate((180 * MB, 140 * MB)):
        _age(_blob(home / ".tryon" / f"product-look-{i + 1}.png", n, b"\x89PNG\r\n\x1a\n"), 20 + i * 9)
    tab = state / "browser-history" / "task_w7c2"
    for i in range(1, 9):
        _blob(tab / f"{i:06d}.jpg", 7 * MB, b"\xff\xd8\xff")
    (tab / "manifest.jsonl").write_text("".join(json.dumps({"seq": i, "at": time.time() - 3 * DAY, "url": "https://shop.example.com/orders",
                                                             "title": s["tab_title"], "action": "browser_open"}, ensure_ascii=False) + "\n"
                                                 for i in range(1, 9)), encoding="utf-8")
    _age(tab, 3)
    for name, n, days in s["results"]:
        _age(_blob(home / "files" / name, n), days)
    tname, tn = s["trash"]
    trash = home / ".trash"
    _blob(trash / f"a1b2c3d4e5f6-{tname}", tn)
    (trash / "index.json").write_text(json.dumps([{"id": "a1b2c3d4e5f6", "name": tname, "bytes": tn, "deleted_at": time.time() - 9 * DAY}],
                                                  ensure_ascii=False), encoding="utf-8")
    for i, (version, days) in enumerate((("0.1.398210", 36), ("0.1.400114", 21), ("0.1.401802", 9), ("0.1.402380", 2))):
        d = state / ".backups" / f"2026{10 + i // 3:02d}{1 + i * 7:02d}-0300"
        _blob(d / "state.tar.gz", (1600 + i * 90) * MB)
        (d / "meta.json").write_text(json.dumps({"id": d.name, "label": "before update", "version": version,
                                                 "created": time.time() - days * DAY, "bytes": (1600 + i * 90) * MB}))
        _age(d, days)
    for i in range(5):
        p = home / ".memory-passes" / f"2026110{i}T0410-pass"
        _blob(p / "files" / "MEMORY.md", 40_000)
        _age(p, 8 - i)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--lang", choices=sorted(SAMPLES), default="ko")
    ap.add_argument("--level", choices=("warn", "critical", "host", "real"), default="warn")
    ap.add_argument("--port", type=int, default=18931)
    ap.add_argument("--note-after", type=float, default=0, help="seconds before the warning notification may go out (0: at once); "
                    "the server's own minute check sends it then, so a phone that already looked at its notifications gets it as new")
    a = ap.parse_args()
    state = Path(a.state)
    if state.exists():
        shutil.rmtree(state)
    state.mkdir(parents=True)
    seed(state, a.lang)

    import uvicorn
    from assistant.quota import Quota
    from assistant.server import Settings, create_app

    from assistant import profiles
    cap = int(profiles.caps("minimum")["disk"])           # the minimum size's share for the assistant (about 5.25 GB)
    share = {"warn": 0.83, "critical": 0.962, "host": 0.62, "real": 0.62}[a.level]
    used = Quota(state).used() + 450_000                  # + the database the server is about to make
    _age(_blob(state / "home" / "uploads" / SAMPLES[a.lang]["big_new"], max(1, int(cap * share) - used), b"\x00\x00\x00\x18ftypmp42"), 1)
    env = {"ASSISTANT_TOKEN": TOKEN, "ASSISTANT_HOME": str(state), "ASSISTANT_DISK_LIMIT": str(cap),
           "BROWSER_CDP_HOST": "", "ASSISTANT_REPORT_BROKER": "", "ASSISTANT_KELO_BROKER": ""}
    app = create_app(Settings.from_env(env))
    watch = app.state.storage_watch
    if a.level in ("warn", "critical"):
        watch._usage = lambda p: Usage(500 * GB, 380 * GB, 120 * GB)
    elif a.level == "host":
        watch._usage = lambda p: Usage(460 * GB, int(459.2 * GB), int(0.8 * GB))
    watch.awake = lambda ts: True
    watch.store.set_meta("ui_lang", a.lang)
    # a server already set up (names and a main model), so the app opens at the chat; no message is ever sent to the model here
    watch.store.set_profile(user_name="민지" if a.lang == "ko" else "Alex", agent_name="Kelo", provider="agy", model="gemini-3.8-flash",
                            timezone="Asia/Seoul" if a.lang == "ko" else "Europe/London")
    if a.note_after > 0:
        due, send = time.time() + a.note_after, watch.notify

        def later(*args, **kw):
            if time.time() < due:
                raise RuntimeError("not yet")       # the watcher keeps it for its next minute check
            return send(*args, **kw)
        watch.notify = later
    print("sent:", watch.check(fresh=True), "| public:", watch.public(watch.numbers(fresh=True)), flush=True)
    print(f"address http://127.0.0.1:{a.port}  (Android emulator: http://10.0.2.2:{a.port})  access key {TOKEN}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
