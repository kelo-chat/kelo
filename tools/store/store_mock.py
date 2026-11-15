"""A pretend Kelo server for the store screenshots (App Store and Google Play), built on desktop/tools/mock_server.py and its
mock_parts: no model, no network, nothing saved. It plays one ordinary morning of one user, in English or Korean:

- the chat: today's morning briefing (weather, two events, one mail to check). POST /mock/chat {"chat": briefing|handoff|empty}
  sets it for the next scene (restart the app after): handoff is a hotel search handed off (delegate) and the report it came back
  with, empty is an empty chat for a message sent from the phone;
- a message sent from the phone gets a scripted reply, picked by its words (any other text gets the train one; the store sets use only the order one):
    train  "...KTX..." / "...train..."   two tool steps, then the train times (no approval)
    order  "...예매..." / "...book..."   a train seat picked, then the one final order card (confirm_purchase), waiting for 허용/거절
- the Work page: one running browser job (a hotel comparison) whose live view shows tools/store/data/site-{lang}.jpg;
- goals, schedules, connectors (tools/store/data/connectors-{lang}.json, made by the server's own code: gen_connectors.py), profile.

Times: --tz names the time zone the phone shows (the screenshots set the phone to the same one; --print-tz picks one where it is
morning now), so the briefing at --brief is this morning in that zone whatever the Mac's clock says. The words name dates of the
week of Tuesday 10 November 2026 ("next Tuesday, the 17th"); change them with the date when the sets are made again.

    uv run python tools/store/store_mock.py --lang ko --port 8790 --tz Asia/Seoul
Pair with the access key mock-desktop-token (or the #pair link it prints).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import re
import struct
import sys
import time
import uuid
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data"

spec = importlib.util.spec_from_file_location("kelo_mock", ROOT / "desktop/tools/mock_server.py")
dm = importlib.util.module_from_spec(spec)
sys.modules["kelo_mock"] = dm
spec.loader.exec_module(dm)

ap = argparse.ArgumentParser()
ap.add_argument("--lang", choices=("en", "ko"), default="ko")
ap.add_argument("--port", type=int, default=8790)
ap.add_argument("--tz", default="Asia/Seoul")
ap.add_argument("--brief", default="07:00", help="when this morning's briefing came (HH:MM in --tz)")
ap.add_argument("--print-tz", action="store_true",
                help="print a time zone where it is now between 07:15 and 11:00 on Seoul's date (a morning for the screenshots) and exit")
A = ap.parse_args()
if A.print_tz:
    from datetime import timezone
    now = datetime.now(timezone.utc)
    zones = ["Pacific/Kiritimati", "Pacific/Tongatapu", "Pacific/Fiji", "Asia/Kamchatka", "Pacific/Noumea", "Australia/Brisbane",
             "Asia/Seoul", "Asia/Shanghai", "Asia/Bangkok", "Asia/Dhaka", "Asia/Karachi", "Asia/Dubai", "Europe/Moscow", "Africa/Cairo",
             "Africa/Lagos", "Atlantic/Reykjavik", "Atlantic/Cape_Verde", "America/Noronha", "America/Sao_Paulo", "America/Halifax",
             "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu",
             "Pacific/Pago_Pago"]
    def minutes(z: str) -> int:
        t = now.astimezone(ZoneInfo(z))
        return t.hour * 60 + t.minute
    seoul = now.astimezone(ZoneInfo("Asia/Seoul")).date()   # the scenes' dates ("next Tuesday, the 17th") count from Seoul's today
    ok = [z for z in zones if 7 * 60 + 15 <= minutes(z) < 11 * 60 and now.astimezone(ZoneInfo(z)).date() == seoul]
    print(min(ok, key=lambda z: abs(minutes(z) - 8 * 60 - 30)) if ok else "Asia/Seoul")
    sys.exit(0)
LANG, KO, TZ = A.lang, A.lang == "ko", ZoneInfo(A.tz)
KO_TEXT = json.loads((ROOT / "src/assistant/locales/ko.json").read_text())


def tr(en: str) -> str:
    """The server's own words for a label (src/assistant/locales/ko.json), as a real server sends them."""
    return KO_TEXT.get(en, en) if KO else en


def w(ko: str, en: str) -> str:
    return ko if KO else en


TODAY = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def at(hh: int, mm: int, days: int = 0) -> float:
    return (TODAY + timedelta(days=days, hours=hh, minutes=mm)).timestamp()


NOW = time.time()
BRIEF_AT = tuple(int(x) for x in A.brief.split(":"))

# ---------------------------------------------------------------- the user's data
dm.TEXT[LANG].update({
    "user": w("지민", "Alex"), "agent": "Kelo", "history": [],
    "goals": [(w("한 달에 책 2권 읽기", "Read 2 books a month"), w("독서", "Reading"), 2, 5, w("첫 번째 책 끝까지 읽기", "Finish the first book")),
              (w("토익 850점", "Spanish B1 by March"), w("공부", "Learning"), 2, 5, w("실전 모의고사 한 번 풀기", "Take a full practice test")),
              (w("이사 준비", "Get ready to move"), w("생활", "Home"), 3, 7, w("이삿짐 업체 견적 받기", "Get quotes from movers"))],
    "goal_done": w("가계부 정리", "Budget spreadsheet"),
})

BRIEFING = {
    "day": TODAY.strftime("%Y-%m-%d"),
    "summary": " · ".join([tr("Rain this evening"), tr("{n} events").replace("{n}", "2"), tr("{n} to check").replace("{n}", "1")]),
    "line": w("저녁 7시부터 비가 와요. 퇴근길에 우산 챙기세요.", "Rain from 7 PM, so take an umbrella for the way home."),
    "quiet": False,
    "place": {"name": w("서울", "New York")},
    "weather": {"high": 14 if KO else 57, "low": 6 if KO else 43, "unit": "C" if KO else "F", "sky": "partly",
                "headline": w("구름 조금 · 최고 14° · 최저 6°", "Partly cloudy · High 57° · Low 43°"),
                "notes": [{"kind": "rain_start", "text": w("19:00부터 비", "Rain from 19:00"), "at": at(19, 0)},
                          {"kind": "temp_drop", "text": w("어제보다 4°C 낮아요", "7°F colder than yesterday")}],
                "provider": "open_meteo", "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
                "attribution_url": "https://open-meteo.com/", "fallback": False},
    "events": [{"title": w("팀 주간 회의", "Team weekly"), "start": at(10, 0), "end": at(11, 0), "where": w("3층 회의실", "Room 3B"),
                "open": {"kind": "event", "start": at(10, 0)}},
               {"title": w("치과 정기 검진", "Dentist check-up"), "start": at(15, 0), "end": at(15, 30), "where": "",
                "open": {"kind": "event", "start": at(15, 0)}}],
    "ahead": [],
    "missed": [{"kind": "mail", "title": w("견적서 회신 부탁드립니다", "Quote for the kitchen renovation"),
                "detail": "kim@example.com" if KO else "dana@example.com",
                "open": {"kind": "mail", "account": "mail-gmail", "uid": "4211",
                         "subject": w("견적서 회신 부탁드립니다", "Quote for the kitchen renovation")}}],
    "hours": None,
}


def briefing_text() -> str:
    lines = [tr("Morning briefing") + " — " + BRIEFING["line"],
             w("날씨", "Weather") + ": " + BRIEFING["place"]["name"] + " · " + BRIEFING["weather"]["headline"]]
    for e in BRIEFING["events"]:
        lines.append(f"- {datetime.fromtimestamp(e['start'], TZ):%H:%M} {e['title']}" + (f" ({e['where']})" if e["where"] else ""))
    return "\n".join(lines)


HISTORY = [("assistant", briefing_text(), at(*BRIEF_AT), BRIEFING)]   # the chat starts with this morning's briefing


def handoff(now: float) -> list[tuple]:
    """A task handed off (delegate) earlier this morning and the report it came back with: what the chat shows when you open it."""
    return [
        ("user", w("다음 주 부산 출장 숙소 좀 찾아 줘. 해운대 쪽으로 17일부터 2박, 1박 15만 원 이하면 좋겠어",
                   "Find me a hotel in Boston for next week's trip. Back Bay, two nights from the 17th, under $220 a night"), now - 1080, None),
        ("assistant", w("숙소 비교는 따로 맡겨 둘게요. 몇 곳 보고 끝나면 알려 드릴게요.",
                        "I've handed off the hotel comparison. I'll let you know when it's done."), now - 1068, None),
        ("assistant", w("숙소 비교가 끝났어요. 해운대에서 1박 15만 원 이하인 곳은 세 곳이에요.\n\n"
                        "1. **센텀 비즈니스 호텔** · 96,000원 · 센텀시티역 도보 2분\n"
                        "2. **오션뷰 호텔 해운대** · 128,000원 · 바다 전망, 해운대역 도보 4분\n"
                        "3. **마린 스테이 부산** · 142,000원 · 해수욕장 앞, 조식 포함\n\n"
                        "바다 전망을 원하시면 오션뷰 호텔이 좋아요. 예약도 해 드릴까요?",
                        "The hotel comparison is done. Three places in Back Bay are under $220 a night.\n\n"
                        "1. **Seaport Business Inn** · $159 · 2 min to South Station\n"
                        "2. **Harbor View Hotel** · $189 · Water view, 4 min to the station\n"
                        "3. **Back Bay Stay** · $214 · Near Copley Square, breakfast included\n\n"
                        "Harbor View has the view if you want one. Shall I book it?"), now - 150, None),
    ]

# ---------------------------------------------------------------- goal pages (GET /api/goals/{id}), as gc.py shapes them
GOALS = {
    1: {"facts": [(w("목표", "Target"), w("한 달에 2권", "2 books a month")), (w("읽는 시간", "Reading time"), w("자기 전 30분", "30 minutes before bed"))],
        "steps": [(w("이번 달 읽을 책 고르기", "Pick this month's books"), "done", "user", None),
                  (w("하루에 읽을 쪽수 나누기", "Split them into daily pages"), "done", "agent", None),
                  (w("일요일마다 읽은 쪽수 묻기", "Ask for pages read each Sunday"), "doing", "agent", None),
                  (w("첫 번째 책 끝까지 읽기", "Finish the first book"), "todo", "user", 15),
                  (w("다 읽은 책 한 줄로 정리하기", "Write one line on each book"), "todo", "agent", None)],
        "metric": (w("이번 달 읽은 쪽수", "Pages read this month"), w("쪽", "pages"), 0, 620, [0, 95, 180, 310]),
        "doc": w("이번 달 읽을 책", "This month's books"), "sched": (w("읽은 쪽수 묻기", "Ask for pages read"), w("매주 일요일 오후 9:00", "Sundays at 9:00 PM")),
        "log": [w("읽은 쪽수 310쪽을 적었어요.", "Logged 310 pages read."), w("두 권을 하루 21쪽씩 나눠 읽기로 했어요.", "Two books, about 21 pages a day.")]},
    2: {"facts": [(w("지금 점수", "Level now"), w("720점 (9월 시험)", "A2 (placement test)")), (w("시험 날짜", "Exam date"), w("12월 13일 (일)", "Sat, Mar 13")),
                  (w("공부 시간", "Study time"), w("평일 출근길 40분", "Weekday commute, 40 min"))],
        "steps": [(w("12월 시험 접수", "Book the exam"), "done", "user", None), (w("약한 파트 찾기", "Find weak spots"), "done", "agent", None),
                  (w("매일 아침 단어 30개 보내기", "Send 20 words each morning"), "doing", "agent", None),
                  (w("실전 모의고사 한 번 풀기", "Take a full practice test"), "todo", "user", 22),
                  (w("점수 보고 계획 고치기", "Adjust the plan after each test"), "todo", "agent", None)],
        "metric": (w("모의고사 점수", "Practice test score"), w("점", "%"), 720 if KO else 48, 850 if KO else 70,
                   [720, 745, 765, 790] if KO else [48, 55, 61, 66]),
        "doc": w("오답 노트", "Mistakes to review"), "sched": (w("토익 단어 30개", "20 Spanish words"), w("매일 오전 7:40", "Every day at 7:40 AM")),
        "log": [w("모의고사 790점을 적었어요.", "Logged a practice score of 66%."), w("약한 파트는 Part 5와 Part 7이에요.", "Weak spots: listening and writing.")]},
}


def goal_page(s, gid: int) -> dict:
    g = next((x for x in s.goals if x["id"] == gid), s.goals[0])
    d = GOALS.get(gid, GOALS[1])
    name, unit, start, target, vals = d["metric"]
    return {
        "goal": g,
        "facts": [{"key": k, "value": v, "source": "user"} for k, v in d["facts"]],
        "steps": [{"id": i + 1, "title": t, "status": st, "owner": o, "detail": "",
                   "due": (TODAY + timedelta(days=due)).strftime("%Y-%m-%d") if due else None} for i, (t, st, o, due) in enumerate(d["steps"])],
        "metrics": [{"id": 1, "name": name, "unit": unit, "start": start, "target": target,
                     "values": [{"value": v, "at": NOW - 86400 * 7 * (len(vals) - 1 - i)} for i, v in enumerate(vals)]}],
        "documents": [{"id": 1, "path": "uploads/goal-notes.md", "title": d["doc"], "exists": True}],
        "schedules": [{"id": 4, "name": d["sched"][0], "description": d["sched"][1], "enabled": True}],
        "log": [{"id": 2, "at": NOW - 86400, "text": d["log"][0], "by": "user"}, {"id": 1, "at": NOW - 86400 * 9, "text": d["log"][1]}],
    }


# ---------------------------------------------------------------- scripted replies
SCENES = {
    "train": {
        "tools": [("browser_open", tr("Opening a site")), ("browser_read", tr("Checking the page"))],
        "text": w("**가는 편 · 11월 17일 (화)**\n- 07:00 서울 → 09:39 부산 · 59,800원\n- 07:30 서울 → 10:05 부산 · 59,800원\n\n"
                  "**오는 편 · 11월 19일 (목)**\n- 18:30 부산 → 21:05 서울 · 59,800원\n\n"
                  "왕복 119,600원이에요. 07:00 차가 가장 빨리 도착해요.",
                  "**Out · Tue, Nov 17**\n- 6:05 AM New York → 9:50 AM Boston · $89\n- 7:00 AM New York → 10:45 AM Boston · $96\n\n"
                  "**Back · Thu, Nov 19**\n- 6:00 PM Boston → 9:36 PM New York · $96\n\n"
                  "$185 round trip. The 6:05 AM gets you there first."),
    },
    "order": {
        "tools": [("browser_open", tr("Opening a site")), ("browser_click", tr("Clicking on the page"))],
        "text": "",   # straight to the order card
        "order": True,
    },
}


def order_event(turn: dict) -> dict:
    """The one final approval of a purchase (confirm_purchase), shaped as desktop/tools/mock_parts/shipping_data's sample: a train
    ticket, so no shipping part, paid with what the site keeps (no card from the phone)."""
    site = "rail.example.com"
    item = w("서울 → 부산 KTX", "New York → Boston")
    opts = w("11월 17일 (화) 07:00 · 일반실 1명", "Tue, Nov 17, 6:05 AM · 1 adult, coach")
    amount_text = w("59,800원", "$89.00")
    return {
        "type": "approval_request", "approval_id": turn["approval"], "id": "s-order", "name": "confirm_purchase",
        "arguments": {"summary": item, "amount": 59800 if KO else 89.0, "currency": "KRW" if KO else "USD", "site": site},
        "detail": "", "lines": [], "label": tr("Asking to confirm a payment"), "ask": w("결제할까요?", "Pay for this?"), "site": site,
        "can_remember": False, "can_remember_schedule": False, "schedule_id": None, "task_id": 52, "expires_at": int(turn["started"] + 900),
        "payment": {"site": site, "summary": item, "amount": 59800 if KO else 89.0, "currency": "KRW" if KO else "USD",
                    "amount_text": amount_text,
                    "items": [{"name": item, "options": opts, "quantity": 1, "price_text": amount_text}],
                    "order": {"what": item}},
        "card": None,   # paid with what the site keeps (the order card says "사이트에서 결제" / "On the site")
    }


def pick_scene(text: str) -> str:
    low = text.lower()
    return "order" if ("예매" in text or "book" in low) else "train"


# ---------------------------------------------------------------- the live view's page
FRAME = (DATA / f"site-{LANG}.jpg").read_bytes()
JOB = {"id": "w1", "kind": "browser", "title": w("부산 숙소 가격 비교", "Compare hotels in Boston"), "status": "running",
       "status_text": w("숙소 세 곳 가격 보는 중", "Checking prices at three hotels"), "tab": "task:41", "task_id": 41}


class Msgs(list):
    """Message ids keep growing, also after POST /mock/chat (the apps cache messages by id)."""
    next_id = 1000

    def append(self, m):
        m["id"] = Msgs.next_id
        Msgs.next_id += 1
        super().append(m)


class State(dm.State):
    def __init__(self) -> None:
        super().__init__(LANG)
        self.profile.update({"provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "medium",
                             "fallback_provider": "openrouter", "fallback_model": "google/gemini-3.5-pro", "tone": "polite"})
        for g in self.goals:
            g["asking"] = False
            g["schedules_on"] = 0
        self.messages = Msgs()
        for role, text, when, card in HISTORY:
            m = {"role": role, "content": text, "created_at": when}
            if card:
                m["briefing"] = card
            self.messages.append(m)

    def reply_events(self, turn):
        sc = SCENES[turn["scene"]]
        age = time.time() - turn["started"]
        ev = [{"type": "thinking"}]
        tl = 0.8
        for i, (name, label) in enumerate(sc["tools"]):
            if age > tl:
                ev.append({"type": "tool_started", "id": f"s{i}", "name": name, "arguments": {}, "label": label})
            if age > tl + 0.9:
                ev.append({"type": "tool_result", "id": f"s{i}", "name": name, "output": "ok", "is_error": False, "approved": None,
                           "label": label})
            tl += 1.1
        text = sc["text"]
        if age > tl:
            shown = min(len(text), int((age - tl) * 45))
            end = len(text) if shown >= len(text) else shown - shown % 12
            for i in range(0, end, 12):
                ev.append({"type": "text", "text": text[i:min(i + 12, end)]})
        tl += len(text) / 45 + 0.4
        if age <= tl:
            return ev
        if sc.get("order"):
            o = order_event(turn)
            ev.append({"type": "tool_started", "id": o["id"], "name": o["name"], "arguments": {}, "label": o["label"]})
            ev.append(o)
            answer = self.approved.get(turn["approval"])
            if answer is None:
                return ev
            ev.append({"type": "tool_result", "id": o["id"], "name": o["name"], "output": "ok", "is_error": False, "approved": answer,
                       "label": o["label"]})
            ev.append({"type": "text", "text": "\n\n" + (w("주문했어요.", "Ordered.") if answer else w("주문하지 않았어요.", "I didn't order it."))})
        ev.append({"type": "done", "input_tokens": 1400, "output_tokens": 220, "steps": 3})
        return ev


CONNECTORS = json.loads((DATA / f"connectors-{LANG}.json").read_text())


class H(dm.Handler):
    def route(self, method):
        path = urlsplit(self.path).path
        q = parse_qs(urlsplit(self.path).query)
        if path.endswith("/live") and self.headers.get("Upgrade", "").lower() == "websocket":
            return self.live_socket()
        if path.startswith("/api/") and path != "/api/pair" and not path.startswith("/api/connectors/icons/") \
                and self.headers.get("Authorization") != f"Bearer {dm.TOKEN}":
            return self.send_json({"detail": "unauthorized"}, 401)
        s = self.state
        if path == "/mock/chat" and method == "POST":   # the chat for the next scene: briefing | handoff | empty (restart the app after)
            kind = self.body().get("chat", "empty")
            rows = HISTORY if kind == "briefing" else handoff(time.time()) if kind == "handoff" else []
            with s.lock:
                s.messages.clear()
                s.turns.clear()
                for role, text, when, card in rows:
                    s.messages.append({"role": role, "content": text, "created_at": when, **({"briefing": card} if card else {})})
            return self.send_json({"ok": True, "messages": len(rows)})
        if path == "/api/turns" and method == "POST":
            text = str(self.body().get("text", ""))
            tid = uuid.uuid4().hex[:12]
            with s.lock:
                s.messages.append({"role": "user", "content": text, "created_at": time.time()})
                s.turns[tid] = {"started": time.time(), "approval": "ap-" + tid, "saved": False, "scene": pick_scene(text),
                                "msg": len(s.messages)}
            return self.send_json({"id": tid})
        m = re.fullmatch(r"/api/turns/([^/]+)", path)
        if m and method == "GET":
            turn = s.turns.get(m.group(1))
            if not turn:
                return self.send_json({"detail": "that reply is gone"}, 404)
            after = int(q.get("after", ["0"])[0])
            deadline = time.time() + 1.0
            while True:
                ev = s.reply_events(turn)
                done = any(e["type"] == "done" for e in ev)
                if len(ev) > after or done or time.time() > deadline:
                    break
                time.sleep(0.1)
            if done and not turn["saved"]:
                with s.lock:
                    turn["saved"] = True
                    s.messages.append({"role": "assistant", "created_at": time.time(),
                                       "content": "".join(e["text"] for e in ev if e["type"] == "text")})
            return self.send_json({"events": ev[after:], "next": len(ev), "done": done})
        if path in ("/api/approvals/pending",):
            return self.send_json({"items": [], "resolved": []})
        if path == "/api/followups" and method == "GET":
            return self.send_json({"items": []})
        if path == "/api/notifications" and method == "GET":
            return self.send_json({"items": [], "unseen": 0, "last_id": 0})
        if path == "/api/agent/status":
            busy = any(not any(e["type"] == "done" for e in s.reply_events(t)) for t in s.turns.values())
            pend = sum(1 for t in s.turns.values() if t["approval"] not in s.approved
                       and any(e["type"] == "approval_request" for e in s.reply_events(t)))
            return self.send_json({"busy": busy, "status": None, "pending_approvals": pend, "agent_name": "Kelo",
                                   "tone": s.profile.get("tone", "polite"), "open_followups": 0, "secret_requests": 0,
                                   "unseen_tasks": 0, "unseen_notifications": 0, "avatar_version": 0})
        if path == "/api/task-permissions" and method == "GET":   # nothing allowed ahead of time in these scenes
            return self.send_json({"items": []})
        if path == "/api/workers" and method == "GET":
            return self.send_json({"items": [{**JOB, "started_at": NOW - 900, "updated_at": time.time() - 20, "steps": 6, "max_steps": 20,
                                              "parent_task_id": 40, "last_url": "https://stay.example.com/search"}],
                                   "running": 1, "limit": 4})
        if path == "/api/tasks" and method == "GET":
            rows = [(40, w("부산 출장 준비", "Plan the Boston trip"), "running", "", "chat", NOW - 900),
                    (39, w("월세 알림", "Rent reminder"), "done", w("월세 날짜를 알려 드렸어요.", "Sent the rent reminder."), "schedule:3",
                     at(9, 0, -16)),
                    (38, w("분기 보고서 초안", "Quarterly report draft"), "done",
                     w("초안을 문서로 만들어 뒀어요.", "The draft is saved as a document."), "chat", at(16, 10, -1))]
            return self.send_json({"items": [{"id": i, "title": t, "status": st, "result": r, "source": src, "started_at": t0,
                                              "finished_at": None if st == "running" else t0 + 240,
                                              "live_status": JOB["status_text"] if st == "running" else None, "seen": True}
                                             for i, t, st, r, src, t0 in rows], "unseen": 0})
        if path == "/api/browser/sessions" and method == "GET":
            return self.send_json({"available": True, "items": [{"id": JOB["tab"], "label": JOB["title"], "kind": "task", "title": "stay.example",
                                   "url": "https://stay.example.com/search", "host": "stay.example.com", "status": "working",
                                   "status_text": JOB["status_text"], "last_activity": time.time() - 5, "task_id": 41, "viewers": 1}]})
        if path.endswith("/live-ticket"):
            return self.send_json({"ticket": "mock-ticket"})
        if path.startswith("/api/browser/sessions/") and (path.endswith("/frame") or path.endswith("/screenshot")):
            if method == "DELETE":
                return self.send_json({"ok": True})
            if int(q.get("after", ["0"])[0]) >= 1:
                time.sleep(2)
                self.send_response(204)
                self.end_headers()
                return
            self.send_response(200)
            for k, v in {"Content-Type": "image/jpeg", "Content-Length": str(len(FRAME)), "X-Frame-Seq": "1", "X-Frame-W": "1440",
                         "X-Frame-H": "900", "X-Frame-Css-W": "1440", "X-Frame-Css-H": "900",
                         "X-Frame-Url": "https%3A%2F%2Fstay.example.com%2Fsearch", "X-Frame-Title": "stay.example", "X-Controller": "agent",
                         "X-You-Control": "0"}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(FRAME)
            return
        if path == "/api/schedules" and method == "GET":
            rows = [(w("토익 단어 30개", "20 Spanish words"), w("매일 오전 7:40", "Every day at 7:40 AM"), "40 7 * * *", at(7, 40, 1)),
                    (w("주간 회고", "Weekly review"), w("매주 금요일 오후 6:00", "Fridays at 6:00 PM"), "0 18 * * 5", at(18, 0, 3)),
                    (w("월세 알림", "Rent reminder"), w("매달 25일 오전 9:00", "Monthly on the 25th at 9:00 AM"), "0 9 25 * *", at(9, 0, 15))]
            return self.send_json({"items": [{"id": i + 1, "name": n, "description": d, "cron": c, "enabled": True, "next_run_ts": nx,
                                              "timezone": A.tz} for i, (n, d, c, nx) in enumerate(rows)], "timezone": A.tz})
        if path == "/api/watchers" and method == "GET":
            return self.send_json({"items": []})
        m = re.fullmatch(r"/api/goals/(\d+)", path)
        if m and method == "GET":
            return self.send_json(goal_page(s, int(m.group(1))))
        if path == "/api/goals/suggestions":
            return self.send_json({"items": []})
        if path == "/api/connectors" and method == "GET":
            return self.send_json(CONNECTORS)
        if path == "/api/devices" and method == "GET":
            return self.send_json({"devices": [{"id": 7, "name": w("내 휴대폰", "My phone"), "created_at": NOW - 86400 * 30}], "current": 7})
        if path == "/api/providers":
            return self.send_json([
                {"name": "anthropic", "display_name": "Anthropic", "configured": True, "vendor": "Anthropic", "effort_levels": ["low", "medium", "high"]},
                {"name": "openrouter", "display_name": "OpenRouter", "configured": True, "vendor": "OpenRouter"}])
        if path == "/api/models":
            pv = q.get("provider", [""])[0]
            ms = {"anthropic": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5"], "openrouter": ["google/gemini-3.5-pro"]}.get(pv, [])
            return self.send_json({"provider": pv, "models": ms, "info": {m: {"images": True} for m in ms}})
        if path == "/api/usage":
            return self.send_json({"today": {"input_tokens": 182000, "output_tokens": 9400}, "total": {"input_tokens": 9120000, "output_tokens": 410000},
                                   "subscription": None})
        return super().route(method)

    def live_socket(self):
        """Just enough WebSocket for the live view: the status, one frame, then quiet until the app closes it."""
        key = self.headers["Sec-WebSocket-Key"]
        acc = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {acc}\r\n\r\n").encode())
        self.wfile.flush()

        def send(op, data):
            n = len(data)
            hdr = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536
                                        else bytes([127]) + struct.pack(">Q", n))
            self.wfile.write(hdr + data)
            self.wfile.flush()
        send(1, json.dumps({"type": "status", "controller": "agent", "you_control": False, "label": JOB["status_text"], "viewers": 1,
                            "agent": {"busy": True, "status": JOB["status_text"], "task_id": 41, "title": JOB["title"]}}).encode())
        send(1, json.dumps({"type": "frame", "seq": 1, "w": 1440, "h": 900, "ts": time.time(), "url": "https://stay.example.com/search",
                            "title": "stay.example", "bytes": len(FRAME), "css_w": 1440, "css_h": 900}).encode())
        send(2, FRAME)
        try:
            while True:
                b = self.rfile.read(2)
                if len(b) < 2:
                    break
                op, n = b[0] & 15, b[1] & 127
                if n == 126:
                    n = struct.unpack(">H", self.rfile.read(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", self.rfile.read(8))[0]
                mask = self.rfile.read(4) if b[1] & 128 else b""
                payload = bytes(x ^ mask[i % 4] for i, x in enumerate(self.rfile.read(n))) if mask else self.rfile.read(n)
                if op == 8:
                    send(8, b"")
                    break
                if op == 1 and b'"ping"' in payload:
                    send(1, b'{"type":"pong"}')
        except OSError:
            pass
        self.close_connection = True


H.state = State()
srv = dm.Server(("127.0.0.1", A.port), H)
print(f"store mock ({LANG}, {A.tz}) on http://127.0.0.1:{A.port} - pair: http://127.0.0.1:{A.port}/#pair={dm.PAIR_CODE}", flush=True)
srv.serve_forever()
