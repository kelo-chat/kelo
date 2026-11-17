"""read_earlier_messages: the exact words of messages that compaction folded into the summary (read-only, the chat's own messages)."""

import re
from datetime import datetime, timezone
from pathlib import Path

from assistant.agent.toolbox import CORE_TOOLS
from assistant.earlier_messages import MAX_LIMIT, TEXT_CHARS, earlier_messages_tool
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tool_labels import hidden, label

CONTENT = Path(__file__).resolve().parents[1] / "content"


def make(n: int = 30, folded: int = 25) -> tuple[Store, list[int]]:
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    ids = [store.add_message("user" if i % 2 == 0 else "assistant", f"message {i:02d} about topic {i % 3}") for i in range(n)]
    t = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc).timestamp()          # 15:00 in Seoul
    for k, i in enumerate(ids):
        store._run("UPDATE messages SET created_at = ? WHERE id = ?", (t + 60 * k, i))
    if folded:
        assert store.save_conversation_summary("summary", ids[folded - 1], folded, expect_up_to=0)
    return store, ids


async def call(store: Store, **args) -> str:
    return await earlier_messages_tool(store).handler(args)


def listed(out: str) -> list[int]:
    return [int(m) for m in re.findall(r"^#(\d+) ", out, re.M)]


async def test_default_page_is_the_newest_condensed_messages_oldest_first_with_time_and_role():
    store, ids = make()
    out = await call(store)
    assert listed(out) == ids[15:25]                                   # 10 of the 25 folded ones; none of the 5 still in the context
    assert f"#{ids[15]} [2026-11-17 15:15] assistant: message 15 about topic 0" in out
    assert f"#{ids[16]} [2026-11-17 15:16] user: message 16" in out
    assert out.endswith(f"older messages: yes (call again with before={ids[15]})")


async def test_paging_with_before_walks_back_to_the_start_and_says_when_there_is_no_more():
    store, ids = make()
    seen: list[int] = []
    before = None
    for _ in range(5):
        out = await call(store, **({"before": before} if before else {}))
        seen = listed(out) + seen
        if out.endswith("older messages: no"):
            break
        before = listed(out)[0]
    assert seen == ids[:25]                                            # every folded message once, in order
    assert out.endswith("older messages: no")


async def test_before_cannot_reach_messages_that_are_still_in_the_live_context():
    store, ids = make()
    assert listed(await call(store, before=ids[-1] + 100)) == ids[15:25]
    assert listed(await call(store, before=ids[3], limit=20)) == ids[:3]


async def test_query_is_a_case_insensitive_substring_match_of_the_users_own_words():
    store, ids = make()
    out = await call(store, query="TOPIC 1")
    assert listed(out) == [i for k, i in enumerate(ids[:25]) if k % 3 == 1][-10:]
    assert (await call(store, query="nothing like this")).startswith("No earlier messages match")
    last = store.add_message("user", "100% sure, a_b")                 # LIKE wildcards in the query are plain text
    assert store.save_conversation_summary("s2", last, 1, expect_up_to=ids[24])
    assert len(listed(await call(store, query="100%"))) == 1
    assert listed(await call(store, query="%")) == [last]
    assert listed(await call(store, query="x_z")) == [] and listed(await call(store, query="a_b")) == [last]


async def test_limit_is_clamped_between_one_and_twenty_and_a_bad_value_means_ten():
    store, ids = make(60, 55)
    assert len(listed(await call(store, limit=500))) == MAX_LIMIT
    assert listed(await call(store, limit=0)) == ids[54:55]            # 1
    assert listed(await call(store, limit=-4)) == ids[54:55]
    assert len(listed(await call(store, limit="many"))) == 10


async def test_long_text_is_cut_with_an_ellipsis():
    store = Store()
    a = store.add_message("user", "x" * (TEXT_CHARS + 500))
    b = store.add_message("assistant", "short")
    store.save_conversation_summary("s", b, 2, expect_up_to=0)
    out = await call(store)
    line = next(ln for ln in out.splitlines() if ln.startswith(f"#{a} "))
    assert line.endswith("x" * TEXT_CHARS + "…") and "x" * (TEXT_CHARS + 1) not in line


async def test_nothing_is_returned_before_a_fold_and_never_what_came_before_a_new_conversation():
    store, ids = make(30, 0)
    assert (await call(store)).startswith("No messages have been condensed yet")
    store, ids = make(30, 20)
    store.set_meta("session_start_id", str(ids[9]))                    # /new after message 10: the model starts again there
    assert listed(await call(store, limit=20)) == ids[10:20]


async def test_only_the_chats_user_and_assistant_messages_are_read_never_tool_results():
    store, ids = make(10, 8)
    store._run("INSERT INTO messages(role, content, created_at) VALUES('tool', 'TOOL RESULT secret-ish', 1)")
    store._run("UPDATE conversation_summary SET up_to_message_id = (SELECT MAX(id) FROM messages) WHERE id = 1")
    out = await call(store, limit=20)
    assert "TOOL RESULT" not in out and "tool:" not in out
    assert listed(out) == ids


def test_it_is_read_only_asks_for_no_card_and_is_offered_to_the_chat_agent():
    tool = earlier_messages_tool(Store())
    assert tool.name == "read_earlier_messages" and tool.read_only is True
    assert tool.requires_approval is False and tool.auto_approve is None and tool.hold is None and not tool.untrusted
    assert tool.description.startswith("Read the exact earlier messages of this conversation that were condensed into the summary")
    assert set(tool.parameters["properties"]) == {"query", "before", "limit"} and not tool.parameters.get("required")
    assert "read_earlier_messages" in CORE_TOOLS and hidden("read_earlier_messages") and label("read_earlier_messages") == "Reading earlier messages"
    app = create_app(Settings(token="t", provider="p", model="m", cdp_host=None, content_dir=str(CONTENT)))
    assert "read_earlier_messages" in {t.name for t in app.state.tools}
