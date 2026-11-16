# Golden threads (replayed real conversations, redacted)

These are the owner's real replies, written by `assistant eval export` and checked by hand before they are committed. `test_golden.py`
replays every file here: the tool calls the reply made are played again through a scripted model, so a change that renames a tool, removes
it, or changes its arguments shows up as a failing replay.

How to add threads
1. `uv run assistant eval export --days 7 --out /tmp/golden-review` (the database is only read; replies are kept 24 hours).
2. Open every file and read it. Look for anything the redaction missed: names, addresses, codes, numbers, other people's messages. Edit
   the file by hand to remove it (replace with `[redacted:<kind>]`); a file with doubts is not committed.
3. Copy only the reviewed files into this folder and run `uv run pytest -q server/tests -k "evals or golden"`.
4. Commit only those files (never the whole export folder).

Format (version 1): `turn_id`, `status`, `day` (date only), `user` (the owner's message), `events` (tool calls with their arguments, tool
results cut to 2 KB, approval cards), `reply` (the text of the answer). No profile, keys or file contents other than a tool's own result.

The folder is empty in the repository until threads are reviewed; the replay test skips when there are none.
