"""Search across the whole chat history (chat_search.py): GET /api/messages/search and GET /api/messages/around.

English and Korean substrings (FTS5 trigram index kept in step by triggers), short queries (LIKE scan), deleted and edited messages,
rows the server keeps for itself left out, paging with `before`, the index backfilled for a database made before it existed, and the
messages around a hit. Fake model only; no network."""

import sqlite3

import httpx

from assistant.chat_search import NOT_THE_USERS_WORDS, around, search, snippet
from assistant.goals import GOAL_MARK
from assistant.server import Settings, create_app
from assistant.store import SEARCH_INDEX, Store
from assistant.workers import HANDOFF_MARK

from test_server import AUTH, TOKEN, Running


def fresh(tmp_path) -> Store:
    return Store(tmp_path / "chat.db")


def ids(rows) -> list[int]:
    return [r["id"] for r in rows]


def test_english_substring_matches_case_insensitively_and_the_snippet_marks_the_match(tmp_path):
    store = fresh(tmp_path)
    first = store.add_message("user", "Remind me to renew the passport next Tuesday")
    second = store.add_message("assistant", "Sure. The PASSPORT office opens at nine.")
    store.add_message("user", "buy milk")
    hits = search(store, "passport")
    assert ids(hits) == [second, first]                                  # newest first
    assert hits[0]["role"] == "assistant" and hits[0]["created_at"] > 0
    assert "«PASSPORT»" in hits[0]["snippet"]
    assert "«passport»" in hits[1]["snippet"]
    assert search(store, "milk") and search(store, "nothing here at all") == []


def test_korean_substrings_match_inside_words(tmp_path):
    store = fresh(tmp_path)
    store.add_message("user", "지난주 결제를 취소해줘")
    store.add_message("assistant", "네, 결제 취소 요청을 보냈어요.")
    store.add_message("user", "날씨 어때")
    hits = search(store, "결제 취소")                                       # the phrase with its space: only one message has it
    assert [h["role"] for h in hits] == ["assistant"] and "«결제 취소»" in hits[0]["snippet"]
    found = search(store, "결제")                                          # a syllable run inside a word: both messages
    assert len(found) == 2 and "«결제»를" in found[1]["snippet"]
    assert search(store, "날씨")[0]["role"] == "user"
    assert search(store, "취소를") == []                                    # a substring that is not in the text


def test_short_queries_fall_back_to_a_scan_and_still_match(tmp_path):
    store = fresh(tmp_path)
    store.add_message("user", "I need a PC for work")
    store.add_message("assistant", "ok, noted")
    store.add_message("user", "결제")
    assert ids(search(store, "pc")) and "«PC»" in search(store, "pc")[0]["snippet"]
    assert ids(search(store, "ok"))
    assert ids(search(store, "결제")) == [3]                              # two Korean syllables: scanned, not trigram-matched
    assert search(store, "%") == [] and search(store, "_") == []          # LIKE wildcards are plain text
    assert search(store, "   ") == []


def test_quotes_and_operators_in_the_query_are_plain_text(tmp_path):
    store = fresh(tmp_path)
    store.add_message("user", 'say "hello" OR goodbye, a-b*c')
    assert ids(search(store, '"hello"')) and ids(search(store, "OR goodbye"))
    assert ids(search(store, "a-b*c"))
    assert search(store, "NOT zzzz") == []


def test_deleted_and_edited_messages_leave_the_index(tmp_path):
    store = fresh(tmp_path)
    gone = store.add_message("user", "secret plan alpha")
    kept = store.add_message("assistant", "plan beta")
    assert ids(search(store, "plan alpha")) == [gone]
    store.clear_messages()                                                 # DELETE FROM messages: the triggers delete the index rows
    assert search(store, "plan") == []
    again = store.add_message("assistant", "plan gamma")
    store._run("UPDATE messages SET content = ? WHERE id = ?", ("plan delta", again))   # an edit replaces the words in the index
    assert search(store, "gamma") == []
    assert ids(search(store, "delta")) == [again]
    assert kept != again


def test_deleted_words_are_gone_from_the_database_file(tmp_path):
    store = fresh(tmp_path)
    mark = "ZQXJ-deleted-words-seven"
    store.add_message("user", f"private {mark}")
    store.clear_messages()
    store._db.close()
    assert mark.encode() not in (tmp_path / "chat.db").read_bytes()


def test_hidden_rows_the_server_keeps_for_itself_are_not_searched(tmp_path):
    store = fresh(tmp_path)
    shown = store.add_message("user", "where is the handoff note")
    store.add_message("user", f"{HANDOFF_MARK}\nthe handoff note is internal")
    store.add_message("user", f"{GOAL_MARK}\nhandoff goal kickoff")
    agent = store.add_message("assistant", "the handoff note is here")
    assert ids(search(store, "handoff")) == [agent, shown]
    assert search(store, "internal") == [] and search(store, "kickoff") == []
    assert HANDOFF_MARK in NOT_THE_USERS_WORDS and GOAL_MARK in NOT_THE_USERS_WORDS


def test_paging_with_before_and_a_limit_clamped_to_fifty(tmp_path):
    store = fresh(tmp_path)
    made = [store.add_message("user", f"note number {i}") for i in range(60)]
    page = search(store, "note", limit=500)
    assert len(page) == 50 and ids(page) == made[::-1][:50]
    older = search(store, "note", limit=50, before=page[-1]["id"])
    assert ids(older) == made[::-1][50:] and search(store, "note", limit=0)[0]["id"] == made[-1]


def test_the_index_is_backfilled_once_for_a_database_made_before_it(tmp_path):
    path = tmp_path / "old.db"
    store = Store(path)
    old = [store.add_message("user", f"legacy message {i} ひらがな") for i in range(3)]
    store._db.close()
    db = sqlite3.connect(path)                                             # a database from before the search existed
    db.executescript("DROP TRIGGER messages_fts_ai; DROP TRIGGER messages_fts_ad; DROP TRIGGER messages_fts_au; DROP TABLE messages_fts;")
    db.commit()
    db.close()
    reopened = Store(path)
    assert ids(search(reopened, "legacy")) == old[::-1]
    assert ids(search(reopened, "ひらがな")) == old[::-1]
    reopened._db.close()
    again = Store(path)                                                    # a second start does not index twice
    assert ids(search(again, "legacy message 1")) == [old[1]]
    assert "messages_fts" in SEARCH_INDEX


def test_snippet_is_a_short_window_with_ellipses_and_the_match_marked():
    text = "word " * 40 + "needle " + "word " * 40
    out = snippet(text, "needle")
    assert "«needle»" in out and out.startswith("…") and out.endswith("…")
    assert len(out) < 120
    assert snippet("no match here", "zzz") == "no match here"


def test_around_gives_the_messages_either_side_oldest_first_without_hidden_rows(tmp_path):
    store = fresh(tmp_path)
    m = [store.add_message("user" if i % 2 == 0 else "assistant", f"line {i}") for i in range(6)]
    store.add_message("user", f"{HANDOFF_MARK}\nhidden between")
    m += [store.add_message("user" if i % 2 == 0 else "assistant", f"line {i}") for i in range(6, 12)]
    rows = around(store, m[5], n=3)
    assert ids(rows) == m[2:9]                                             # three before, the anchor, three after
    assert [r["content"] for r in rows][3] == "line 5"
    assert around(store, m[0], n=2)[0]["id"] == m[0]
    assert around(store, 999_999) is None
    hidden_id = store._all("SELECT id FROM messages WHERE content LIKE ?", (HANDOFF_MARK + "%",))[0]["id"]
    assert around(store, hidden_id) is None


async def test_http_search_and_around_need_the_token_and_return_the_shape(tmp_path):
    seeded = fresh(tmp_path)
    a = seeded.add_message("user", "the parking code is 4417")
    b = seeded.add_message("assistant", "Parking code noted.")
    seeded.add_message("user", "unrelated")
    seeded._db.close()
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "chat.db"),
                              home_dir=str(tmp_path)), tools=[])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, timeout=30) as anon:
        assert (await anon.get("/api/messages/search", params={"q": "parking"})).status_code == 401
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        found = (await c.get("/api/messages/search", params={"q": "parking", "limit": 1})).json()
        assert [f["id"] for f in found] == [b] and set(found[0]) == {"id", "role", "snippet", "created_at"}
        older = (await c.get("/api/messages/search", params={"q": "parking", "before": b})).json()
        assert [f["id"] for f in older] == [a] and "«4417»" not in older[0]["snippet"]
        assert (await c.get("/api/messages/search", params={"q": ""})).json() == []
        around_rows = (await c.get("/api/messages/around", params={"id": a, "n": 1})).json()
        assert [r["id"] for r in around_rows] == [a, b]
        assert (await c.get("/api/messages/around", params={"id": 987654})).status_code == 404
