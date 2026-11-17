"""Proactive mail (proactive.py): every mail since the mailbox's cursor is judged (batched, no count cap), the cursor moves only when the pass
got through, every decision keeps a reason, a mail's notification title, the once-a-day spam skim, the booking-vs-calendar cross-check and
change alerts that go out at once outside the daily limits and the unanswered guard. Fake mailbox, fake model, fake clock."""

import json

from assistant.proactive import BATCH, mail_notice

from test_proactive import Judge, all_as, setup, ts


class Mailbox:
    """A fake mailbox with the Mailboxes.arrivals contract: rows per kind, a cursor that counts looks."""

    def __init__(self, inbox=(), junk=None, promo=None):
        self.rows = {"inbox": list(inbox), "junk": junk, "promo": promo}
        self.asked: list[tuple] = []
        self.looks = 0
        self.fail = False

    def accounts(self):
        return [{"name": "mail-gmail", "provider": "gmail", "label": "Gmail · me@example.org", "address": "me@example.org"}]

    async def arrivals(self, account, cursor, kind="inbox"):
        self.asked.append((kind, cursor))
        if self.fail and kind == "inbox":
            raise OSError("server down")
        if self.rows[kind] is None:
            return None
        self.looks += 1
        rows, self.rows[kind] = self.rows[kind], []
        return {"rows": rows, "cursor": {"uid": self.looks}}


def mail(uid, subject, sender="Kim <kim@example.org>", snippet=""):
    return {"uid": str(uid), "from": sender, "subject": subject, "date": "", "unread": True, "bulk": False, "snippet": snippet}


def inbox_asks(box):
    return [c for k, c in box.asked if k == "inbox"]


async def test_the_cursor_moves_only_after_the_pass_got_through_and_a_failing_mailbox_keeps_its_old_one(tmp_path):
    box = Mailbox([mail(1, "hello")])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), box)
    box.fail = True
    r = await p.run()
    assert r["status"] == "nothing_new" and "OSError" in r["reason"] and p.mail_state("mail-gmail") == {}
    box.fail = False
    clock.t += 7200
    r = await p.run()
    assert r["sent"] == 1 and inbox_asks(box) == [None, None]                       # the failed look is repeated from the same place
    assert p.mail_state("mail-gmail")["inbox"] == {"uid": 1}
    box.rows["inbox"] = [mail(2, "again")]
    clock.t += 7200
    await p.run()
    assert inbox_asks(box)[-1] == {"uid": 1}                                          # the next look starts where this one ended


async def test_a_model_that_fails_leaves_the_cursor_so_the_mail_is_looked_at_again(tmp_path):
    async def garbage(provider, request, **kw):
        from assistant.providers import Done, TextDelta
        yield TextDelta("not json")
        yield Done("stop")
    box = Mailbox([mail(1, "hello")])
    store, home, clock, p, sent = setup(tmp_path, garbage, box)
    r = await p.run()
    assert r["reason_code"] == "bad_answer" and p.mail_state("mail-gmail") == {}
    p.stream_fn = all_as("보통")
    box.rows["inbox"] = [mail(1, "hello")]                                            # the pretend server hands it out again
    clock.t += 7200
    assert (await p.run())["sent"] == 1 and p.mail_state("mail-gmail")["inbox"]


async def test_all_mail_of_a_pass_is_judged_in_batches_with_no_count_cap(tmp_path):
    n = BATCH * 2 + 15
    box = Mailbox([mail(i, f"m{i}") for i in range(n)])
    judge = all_as("drop")
    store, home, clock, p, sent = setup(tmp_path, judge, box)
    r = await p.run()
    assert len(judge.requests) == 3 and r["candidates"] == n and r["dropped"] == n
    asked = [len([ln for ln in rq.messages[-1]["content"].split("Items:\n", 1)[1].splitlines() if ln[:1].isdigit()]) for rq in judge.requests]
    assert asked == [BATCH, BATCH, 15]
    assert len(p.log(200)) == n and p.log(1)[0]["status"] == "dropped"


async def test_every_decision_keeps_a_reason_the_models_or_a_short_code_one(tmp_path):
    box = Mailbox([mail(1, "세일 광고"), mail(2, "계약서 회신 요청"), mail(3, "택배")])
    judge = Judge(lambda items: [{"n": 1, "urgency": "drop", "reason": "광고 메일이에요", "message": ""},
                                 {"n": 2, "urgency": "보통", "reason": "", "message": "계약서 회신이 필요해요"},
                                 {"n": 3, "urgency": "drop", "message": ""}])
    store, home, clock, p, sent = setup(tmp_path, judge, box)
    await p.run()
    by = {x["title"]: x for x in p.log()}
    assert by["세일 광고"]["reason"] == "광고 메일이에요"
    assert by["계약서 회신 요청"]["reason"] == "No reason given; judged worth telling you" and by["계약서 회신 요청"]["status"] == "sent"
    assert by["택배"]["reason"] == "No reason given; judged not worth telling you" and by["택배"]["status"] == "dropped"
    assert all(x["reason"] for x in p.log())
    assert "REQUIRED for every item, drop too" in judge.requests[0].messages[0]["content"]


def test_the_mail_notification_title_says_what_it_is_and_fits():
    assert mail_notice("Kim Min <kim@example.org>", "계약서 회신 요청") == "New mail · Kim Min · 계약서 회신 요청"
    assert mail_notice("kim@example.org", "Hi") == "New mail · kim · Hi"
    long = mail_notice('"A very long sender name that goes on and on" <a@b.c>', "An equally long subject line about the quarterly planning meeting")
    assert len(long) <= 60 and long.startswith("New mail · A very long sender name") and long.endswith("…")
    assert mail_notice("a@b.c", "").endswith("(no subject)")


async def test_a_mail_is_delivered_with_its_notice_and_other_items_without_one(tmp_path):
    box = Mailbox([mail(1, "계약서 회신 요청", "민지 <minji@example.org>")])
    store, home, clock, p, sent = setup(tmp_path, all_as("오늘"), box)
    await p.run()
    assert sent[0]["notice"] == "New mail · 민지 · 계약서 회신 요청" and sent[0]["kind"] == "" and sent[0]["title"] == "계약서 회신 요청"


# ------------------------------------------------------------------------------------------------------------------ the spam skim
async def test_spam_and_promotions_are_skimmed_once_a_day_and_marked_when_one_is_told(tmp_path):
    box = Mailbox(inbox=[], junk=[], promo=[])
    judge = Judge(lambda items: [{"n": n, "urgency": "보통" if "청구서" in t else "drop", "reason": "r", "message": "연체된 청구서가 와 있어요"}
                                 for n, t in items])
    store, home, clock, p, sent = setup(tmp_path, judge, box)
    await p.run()
    assert [k for k, _ in box.asked] == ["junk", "promo", "inbox"]                    # the first look only sets each cursor
    box.rows.update(junk=[mail(1, "청구서 연체 안내"), mail(2, "당첨되셨습니다")], promo=[mail(3, "세일")])
    clock.t += 7200
    await p.run()
    assert [k for k, _ in box.asked][3:] == ["inbox"]                                  # the same day: no second skim
    clock.t = ts(2026, 11, 8, 9, 0)
    box.rows["junk"] = [mail(1, "청구서 연체 안내"), mail(2, "당첨되셨습니다")]
    box.rows["promo"] = [mail(3, "세일")]
    r = await p.run()
    assert [k for k, _ in box.asked][4:] == ["junk", "promo", "inbox"] and r["sent"] == 1
    assert sent[0]["message"] == "Found in the spam folder: 연체된 청구서가 와 있어요" and sent[0]["title"] == "청구서 연체 안내"
    first = judge.requests[-1].messages[-1]["content"]
    assert "[mail, found in spam]" in first and "[mail, found in promotions]" in first
    assert "normally junk" in judge.requests[-1].messages[0]["content"]
    assert [x["status"] for x in p.log() if x["title"] in ("당첨되셨습니다", "세일")] == ["dropped", "dropped"]


async def test_the_skim_is_retried_when_it_failed_and_can_be_switched_off(tmp_path):
    box = Mailbox(inbox=[], junk=[], promo=None)
    store, home, clock, p, sent = setup(tmp_path, all_as("drop"), box)
    p.update(spam_skim=False)
    await p.run()
    assert [k for k, _ in box.asked] == ["inbox"]
    p.update(spam_skim=True)
    clock.t += 7200
    await p.run()
    assert [k for k, _ in box.asked][1:] == ["junk", "promo", "inbox"] and p.mail_state("mail-gmail")["skim_day"] == "2026-11-07"
    assert "promo" not in p.mail_state("mail-gmail")                                    # this provider has none: no cursor for it


# ------------------------------------------------------------------------------------------------------------------ calendar cross-check
async def test_a_booking_that_disagrees_with_the_calendar_is_at_least_today_and_the_model_sees_the_calendar(tmp_path):
    async def cal():
        return [{"id": "e1", "title": "서울 출장 KE123", "start": ts(2026, 11, 12, 9, 0), "where": "김포공항"}]
    box = Mailbox([mail(1, "항공권 예약 확인: 11월 13일 09:00")])
    judge = Judge(lambda items: [{"n": 1, "urgency": "보통", "reason": "예약 확인 메일", "message": "항공권이 캘린더보다 하루 늦어요.", "mismatch": True}])
    store, home, clock, p, sent = setup(tmp_path, judge, box, calendar=cal)
    await p.run()
    assert sent[0]["urgency"] == "오늘" and "하루 늦어요" in sent[0]["message"]
    asked = judge.requests[0].messages[-1]["content"]
    assert "Calendar (to compare booking mails with):\n- 2026-11-12 09:00 서울 출장 KE123 @ 김포공항" in asked
    assert "set \"mismatch\": true" in judge.requests[0].messages[0]["content"]
    # switched off: no calendar lines and a mismatch flag changes nothing
    p.update(calendar_check=False)
    box.rows["inbox"] = [mail(2, "호텔 예약 확인")]
    clock.t += 7200
    await p.run()
    assert "Calendar (to compare" not in judge.requests[-1].messages[-1]["content"] and sent[-1]["urgency"] == "보통"


# ------------------------------------------------------------------------------------------------------------------ change alerts
def change_judge(extra_for=("항공편 변경",)):
    return Judge(lambda items: [{"n": n, "urgency": "보통", "reason": "r", "message": f"알림 {n}", **({"change": True} if any(k in t for k in extra_for) else {})}
                                for n, t in items])


async def test_a_change_alert_goes_out_at_once_outside_every_daily_limit(tmp_path):
    box = Mailbox([mail(i, f"일반 {i}") for i in range(1, 6)] + [mail(9, "항공편 변경 안내"), mail(10, "예약 취소 안내")])
    judge = change_judge(("항공편 변경", "예약 취소"))
    store, home, clock, p, sent = setup(tmp_path, judge, box)
    r = await p.run()
    logged = [x for x in p.log() if x["status"] == "sent"]
    changes = [x for x in logged if x["kind"] == "change"]
    assert r["sent"] == 4 and len(changes) == 2 and all(x["urgency"] == "지금" for x in changes)
    assert [x["urgency"] for x in logged if x["kind"] == ""] == ["보통", "보통"]       # the regular limit (2) is unchanged
    assert len(sent) == 1 and sent[0]["kind"] == "change" and sent[0]["urgency"] == "지금"   # all four are one message (change + regular)
    assert p.today_counts() == {"보통": 2, "오늘": 0, "지금": 0} and p.today_changes() == 2   # daily_now is not used up either
    assert [x["kind"] for x in p.log() if x["status"] == "sent"].count("change") == 2
    assert sent[0]["message"].startswith("• 알림 6")                                      # changes go first (the group keeps the outbox order)
    assert p.public()["today_changes"] == 2 and "change" in judge.requests[0].messages[0]["content"]


async def test_change_alerts_are_not_held_back_by_the_unanswered_guard_nor_counted_by_it(tmp_path):
    box = Mailbox([mail(1, "일반")])
    store, home, clock, p, sent = setup(tmp_path, change_judge(), box)
    p.update(unanswered_limit=1)
    assert (await p.tick())["sent"] == 1                                                # one regular message, nobody answers
    assert p.hold_reason(clock.t) == "waiting_for_reply"
    box.rows["inbox"] = [mail(2, "일반 둘"), mail(3, "항공편 변경 안내")]
    clock.t += 7200
    r = await p.tick()                                                                  # held: only the change goes out
    assert r["sent"] == 1 and [s["kind"] for s in sent] == ["", "change"] and sent[-1]["title"] == "항공편 변경 안내"
    held = next(x for x in p.held() if x["title"] == "일반 둘")                         # kept until its deadline, not dropped
    assert held["why"] == "waiting_for_reply" and not any(x["title"] == "일반 둘" for x in p.log())
    assert p.guard()["unanswered"] == 1                                                 # the change was not counted
    box.rows["inbox"] = [mail(4, "일반 셋")]
    clock.t += 7200
    r = await p.tick()                                                                  # nothing urgent: the pass is shown as held back
    assert r["status"] == "skipped" and r["reason_code"] == "waiting_for_reply" and r["sent"] == 0


async def test_change_alerts_have_their_own_generous_limit_and_can_be_switched_off(tmp_path):
    box = Mailbox([mail(i, f"항공편 변경 {i}") for i in range(1, 5)])
    store, home, clock, p, sent = setup(tmp_path, change_judge(), box)
    p.update(daily_change=2)
    await p.run()
    assert [x["kind"] for x in p.log() if x["status"] == "sent"].count("change") == 2   # over daily_change they are ordinary 보통 items
    over = [x for x in p.log() if "over the limit for changes" in x["reason"]]
    assert len(over) == 2 and {x["urgency"] for x in over} <= {"오늘", None}
    assert p.config()["daily_change"] == 2 and p.config()["change_alerts"] is True
    box.rows["inbox"] = [mail(20, "항공편 변경 20")]
    p.update(change_alerts=False)
    clock.t += 7200
    await p.run()
    assert not any(s["kind"] == "change" and s["title"] == "항공편 변경 20" for s in sent)       # off: no longer a change alert
    assert json.loads(store.meta("proactive:config"))["change_alerts"] is False


# ---------------------------------------------------------------------------------------------------------- mail_check: one switch
async def test_mail_check_off_reads_no_mail_but_the_calendar_still_comes_and_on_again_does_not_replay_the_backlog(tmp_path):
    box = Mailbox([mail(1, "old unread")], junk=[mail(5, "spam")], promo=[])
    t0 = ts(2026, 11, 7, 9, 0)

    async def calendar():
        return [{"id": "e1", "title": "치과", "start": t0 + 3600, "end": t0 + 7200, "all_day": False, "where": "역삼"}]

    judge = all_as("오늘")
    store, home, clock, p, sent = setup(tmp_path, judge, box, calendar=calendar)
    assert p.public()["mail_check"] is True
    assert p.update(mail_check=False)["mail_check"] is False
    r = await p.run()
    assert r["status"] == "done" and r["sent"] == 1 and sent[0]["source"] == "calendar"
    assert box.asked == [] and box.looks == 0                                        # no mailbox was opened: no inbox, no skim
    assert p.mail_state("mail-gmail") == {}                                          # and no cursor moved
    assert "Calendar (to compare" not in judge.requests[-1].messages[-1]["content"]  # no mail line, so no cross-check block
    # mail back on while a mail arrived during the off time
    box.rows["inbox"] = [mail(2, "arrived while off")]
    p.update(mail_check=True)
    assert store.meta("proactive:mail_resume")
    clock.t += 7200
    r = await p.run()
    assert r["sent"] == 0 and all(s["source"] != "mail" for s in sent)               # the old and the off-time mail is not alerted
    assert not store.meta("proactive:mail_resume")                                   # the catch-up is through
    assert [c for k, c in box.asked if k == "inbox"] == [None]                       # every cursor started from "now"
    assert p.mail_state("mail-gmail").get("inbox") is not None
    box.rows["inbox"] = [mail(3, "really new")]
    clock.t += 7200
    r = await p.run()
    assert r["sent"] == 1 and sent[-1]["source"] == "mail"                           # from then on mail works as before


async def test_mail_check_off_leaves_the_morning_briefing_mail_alone_and_the_api_takes_the_boolean(tmp_path):
    from httpx import ASGITransport, AsyncClient
    from fastapi import FastAPI
    from assistant.proactive import proactive_routes

    box = Mailbox([mail(1, "x")])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), box)
    app = FastAPI()
    proactive_routes(app, p, lambda: None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/proactive")).json()["mail_check"] is True
        got = (await c.put("/api/proactive", json={"mail_check": False})).json()
        assert got["mail_check"] is False and got["enabled"] is True
        assert (await c.put("/api/proactive", json={"spam_skim": False})).json()["mail_check"] is False   # other changes leave it alone
        assert (await c.put("/api/proactive", json={"mail_check": True})).json()["mail_check"] is True
