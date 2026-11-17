"""What one proactive pass tells goes out as ONE message per mailbox (proactive.group_outbox / group_text): a single item is told as
before, several are bullet lines, identical mails fold with a count, a mailbox is named only when there are several, the limits still
count per item, a failed delivery leaves its group unlogged and the cursors where they were, and every grouped item is logged."""

import json

import pytest

from assistant.proactive import Entry, Outgoing, Candidate, group_outbox, group_text

from test_proactive import Judge, all_as, setup, ts
from test_proactive_mail import Mailbox, mail


class TwoBoxes:
    """Two mailboxes (Gmail and Naver) with the Mailboxes.arrivals contract; the rows stay until the ledger has them."""

    def __init__(self, gmail=(), naver=()):
        self.rows = {"mail-gmail": list(gmail), "mail-naver": list(naver)}

    def accounts(self):
        return [{"name": "mail-gmail", "provider": "gmail", "label": "Gmail · a@example.org", "address": "a@example.org"},
                {"name": "mail-naver", "provider": "naver", "label": "Naver · b@naver.com", "address": "b@naver.com"}]

    async def arrivals(self, account, cursor, kind="inbox"):
        if kind != "inbox":
            return None
        return {"rows": [dict(m) for m in self.rows[account]], "cursor": {"t": 1}}


def said(n):
    return Judge(lambda items: [{"n": i, "urgency": "보통", "reason": "r", "message": n(t)} for i, t in items])


def out(source="mail", title="Hi", message="m", account="mail-gmail", sender="Kim <kim@example.org>", urgency="보통", change=False):
    extra = {"from": sender, "account": account} if source == "mail" else {}
    return Outgoing(Candidate(f"{source}:{title}:{message}", source, title, extra=extra), urgency, "r", message, change)


# ------------------------------------------------------------------------------------------------------------------ pure helpers
def test_grouping_is_per_mailbox_in_first_appearance_order_and_folds_duplicates():
    box = [out(account="a", message="one", title="x"), out(source="goal", message="g"), out(account="b", message="two", title="y"),
           out(account="a", message="  ONE ", title="z"), out(account="a", message="other", title="x", sender=" KIM  <kim@example.org> ")]
    # the last one has the same sender+subject as the first (normalised) although the text differs: folded too
    groups = group_outbox(box)
    assert [g.key for g in groups] == [("mail", "a"), ("other", ""), ("mail", "b")]
    assert [(e.item.message, e.count) for e in groups[0].entries] == [("one", 3)]
    assert len(groups[0].items) == 3


def test_group_text_single_several_counts_and_header():
    a, b = out(message="Alpha"), out(message="Beta", title="B")
    assert group_text([Entry(a, [a])]) == "Alpha"
    assert group_text([Entry(a, [a, a])]) == "Alpha (2 mails)"
    g = out(source="goal", message="Goal")
    assert group_text([Entry(g, [g, g, g])]) == "Goal (3 times)"
    assert group_text([Entry(a, [a, a]), Entry(b, [b])]) == "• Alpha (2 mails)\n• Beta"
    assert group_text([Entry(a, [a]), Entry(b, [b])], "Gmail:") == "Gmail:\n• Alpha\n• Beta"


# ------------------------------------------------------------------------------------------------------------------ one pass
async def test_one_item_is_sent_as_today(tmp_path):
    box = Mailbox([mail(1, "계약서 회신 요청", "민지 <minji@example.org>")])
    store, home, clock, p, sent = setup(tmp_path, all_as("오늘"), box)
    await p.run()
    assert len(sent) == 1 and sent[0]["message"].startswith("알림 1") and "•" not in sent[0]["message"]
    assert sent[0]["title"] == "계약서 회신 요청" and sent[0]["notice"] == "New mail · 민지 · 계약서 회신 요청" and sent[0]["urgency"] == "오늘"


async def test_two_items_of_one_pass_are_one_message_with_bullets(tmp_path):
    box = Mailbox([mail(1, "첫째", "A <a@x.org>"), mail(2, "둘째", "B <b@x.org>")])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), box)
    r = await p.run()
    assert r["sent"] == 2 and len(sent) == 1
    lines = sent[0]["message"].split("\n")
    assert len(lines) == 2 and all(x.startswith("• 알림") for x in lines)
    assert sent[0]["title"] == "2 things to tell you" and sent[0]["notice"] == "New mail · 2" and sent[0]["source"] == "mail"
    assert sent[0]["key"] == "mail:mail-gmail:1"


async def test_identical_mails_fold_with_count(tmp_path):
    box = Mailbox([mail(1, "New trusted device", "Service <no-reply@svc.com>"), mail(2, "New trusted device", "Service <no-reply@svc.com>")])
    store, home, clock, p, sent = setup(tmp_path, said(lambda t: "새 신뢰 기기가 추가됐어요" if "trusted" in t else "?"), box)
    r = await p.run()
    assert r["sent"] == 2 and len(sent) == 1
    assert sent[0]["message"] == "새 신뢰 기기가 추가됐어요 (2 mails)" and sent[0]["title"] == "New trusted device"
    assert sent[0]["notice"] == "New mail · Service · New trusted device"


async def test_two_mailboxes_two_messages_with_headers(tmp_path):
    box = TwoBoxes([mail(1, "g1"), mail(2, "g2", "B <b@x.org>")], [mail(7, "n1")])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), box)
    p.update(daily_regular=5, daily_total=5)
    await p.run()
    assert len(sent) == 2
    assert sent[0]["message"].startswith("Gmail:\n• 알림") and sent[0]["notice"] == "New mail · Gmail · 2"
    assert sent[1]["message"].startswith("Naver:\n알림") and "•" not in sent[1]["message"] and sent[1]["title"] == "n1"


async def test_single_mailbox_has_no_header(tmp_path):
    box = Mailbox([mail(1, "a", "A <a@x.org>"), mail(2, "b", "B <b@x.org>")])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), box)
    await p.run()
    assert len(sent) == 1 and ":\n" not in sent[0]["message"].split("•")[0] and sent[0]["message"].startswith("• ")


async def test_change_and_regular_items_share_the_message(tmp_path):
    box = Mailbox([mail(1, "일반", "A <a@x.org>"), mail(2, "항공편 변경", "B <b@x.org>")])
    judge = Judge(lambda items: [{"n": n, "urgency": "보통", "reason": "r", "message": f"M{n}", **({"change": True} if "변경" in t else {})}
                                 for n, t in items])
    store, home, clock, p, sent = setup(tmp_path, judge, box)
    await p.run()
    assert len(sent) == 1 and sent[0]["kind"] == "change" and sent[0]["urgency"] == "지금"
    assert sent[0]["message"] == "• M2\n• M1"                                                # the change comes first


async def test_limits_still_count_per_item(tmp_path):
    box = Mailbox([mail(i, "same", "A <a@x.org>") for i in range(1, 6)])                    # five identical mails fold into one line ...
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), box)
    r = await p.run()
    assert r["sent"] == 2 and r["held"] == 3 and len(sent) == 1                             # ... but only two fit the daily limit (the rest is held)
    assert sent[0]["message"].endswith("(2 mails)") and p.today_counts() == {"보통": 2, "오늘": 0, "지금": 0}
    assert p.guard()["unanswered"] == 2


async def test_failed_delivery_keeps_the_cursor_for_that_group(tmp_path):
    box = TwoBoxes([mail(1, "g1")], [mail(7, "n1")])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), box)
    p.update(daily_regular=5, daily_total=5)
    good = p.deliver

    async def flaky(item):
        if item["message"].startswith("Naver"):
            raise OSError("push down")
        return await good(item)

    p.deliver = flaky
    r = await p.run()
    assert r["status"] == "failed" and len(sent) == 1 and sent[0]["message"].startswith("Gmail")   # the other mailbox's message still went out
    assert [x["key"] for x in p.log() if x["status"] == "sent"] == ["mail:mail-gmail:1"]            # only the delivered group is logged
    assert p.mail_state("mail-gmail") == {} and p.mail_state("mail-naver") == {}                    # no cursor moved
    assert p.guard()["unanswered"] == 1
    p.deliver = good
    clock.t += 7200
    r = await p.run()
    assert r["status"] == "done" and r["sent"] == 1 and sent[-1]["message"].startswith("Naver")    # the failed group is told next pass, once
    assert p.mail_state("mail-naver")["inbox"] == {"t": 1}


async def test_every_grouped_item_is_logged_sent_with_the_task_id(tmp_path):
    box = Mailbox([mail(1, "x", "A <a@x.org>"), mail(2, "x", "A <a@x.org>"), mail(3, "y", "B <b@x.org>")])
    judge = Judge(lambda items: [{"n": n, "urgency": "오늘" if n == 3 else "보통", "reason": f"why{n}", "message": f"M{n}"} for n, _ in items])
    store, home, clock, p, sent = setup(tmp_path, judge, box)
    await p.run()
    rows = sorted((x for x in p.log() if x["status"] == "sent"), key=lambda x: x["key"])
    assert len(sent) == 1 and sent[0]["urgency"] == "오늘"                                       # the most urgent of the group
    assert [(x["key"], x["task_id"], x["urgency"], x["message"], x["reason"]) for x in rows] == [
        ("mail:mail-gmail:1", 1, "보통", "M1", "why1"), ("mail:mail-gmail:2", 1, "보통", "M2", "why2"),
        ("mail:mail-gmail:3", 1, "오늘", "M3", "why3")]
