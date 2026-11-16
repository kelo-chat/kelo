"""agy calls record how they used the session pool (usage_events.note) and count the whole prompt (agy reports input without the
cached part), 2026-11-13."""
from types import SimpleNamespace

from assistant.providers import cli_remote
from assistant.providers.cli_agents import session_note
from assistant.providers.types import Usage


def test_session_note_says_how_the_pool_was_used():
    assert session_note(None) == ""
    assert session_note(SimpleNamespace(mode="turn", reason="")) == "turn"
    assert session_note(SimpleNamespace(mode="round", reason="")) == "round"
    assert session_note(SimpleNamespace(mode="new", reason="conversation changed")) == "new: conversation changed"


def test_the_sidecar_wire_carries_the_session_note():
    ev = Usage(100, 5, 80, 0, session="new: worker not alive")
    back = cli_remote.decode_event(cli_remote.encode_event(ev))
    assert back.session == "new: worker not alive" and back.cache_read_tokens == 80
    old = cli_remote.decode_event({"type": "usage", "input_tokens": 1, "output_tokens": 2})   # an older sidecar
    assert old.session == ""


def test_the_timing_note_reads_like_the_log_line():
    import time as _t
    from assistant.usage import _timing_note
    assert _timing_note(None) == ""
    note = _timing_note({"began": _t.monotonic() - 3.0, "first": 1.25})
    assert note.startswith("first 1.2s, done ") and note.endswith("s")
    assert _timing_note({"began": _t.monotonic(), "first": None}).startswith("done ")
