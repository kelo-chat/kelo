"""Call mode's words in the chat (the in-app call with Kelo, see docs/calls.md): the hidden message that starts a call, and the
rules the model follows for a spoken reply.

Pure text: no I/O. The apps send a spoken message with `call` (TalkIn) and the server stores it with via "call"; the model reads
CALL_NOTE for that turn only (it is never stored). CALL_MARK leads the hidden message, so chat history and search leave it out
(chat_search.NOT_THE_USERS_WORDS).
Each message of a call is stored with the call's id (store.call_id; GET /api/messages "call"), and POST /api/turns "amend" replaces
the call's unanswered words (store.drop_unanswered_call_words).
"""

from __future__ import annotations

CALL_ID_PATTERN = r"^[A-Za-z0-9-]{8,64}$"
CALL_MARK = "[A call answered in the Kelo app, not from the user]"   # leads the hidden message; GET /api/messages leaves it out
CALL_NOTE = ("[Not from the user: this message is the user's speech in a voice call in the Kelo app. Their words reach you through "
             "speech recognition and your reply is heard, not read, so respond to what they meant to say, never to how it was "
             "written down; ask only when the meaning itself is unclear. Speak in short natural sentences, the most important point "
             "first, a short first sentence. Write numbers, dates, times, amounts and units "
             "the way a person says them in the reply's language. No lists, tables, links, code, emoji or Markdown. A card the "
             "user must answer (an approval, a form) still shows on the screen: say in a few words that it is on the screen.]")


def with_call(row: dict) -> dict:
    """A stored message as GET /api/messages gives it: "call" = the call it belongs to (the apps fold a call into one card), only on
    call messages; the store's call_id column itself is not sent."""
    call = row.pop("call_id", "") or ""
    if call:
        row["call"] = call
    return row


def answered_text(topic: str) -> str:
    """The hidden message that starts the call: the mark, that the user answered, and to greet in a few spoken words in the user's
    language; with a topic, to start with it ("You called about: <topic>")."""
    parts = [CALL_MARK, "The user answered the call in the Kelo app. Greet them in a few spoken words in their language."]
    topic = (topic or "").strip()
    if topic:
        parts.append(f"You called about: {topic}. Start with that.")
    return " ".join(parts)
