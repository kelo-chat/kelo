"""Keeps agy (Antigravity CLI) sessions alive between model calls, so a reply does not pay agy's start-up again and again.

Why (measured 2026-11-09 from the main sidecar's own agy 1.5.3 log): the CLI itself is up in ~40 ms, but a new agy process then spends
~5 s before the first byte goes to the model: "silent auth" ~2.1 s (keyring token, loadCodeAssist, fetchAvailableModels) and the model
/ experiment refresh after it ~2.8 s (two more loadCodeAssist calls). The vendored plugin already has a persistent worker
(`--input-format stream-json`: one NDJSON message per turn, only the new messages when a request extends the previous one), but here it
was thrown away far more often than needed:

1. One plugin client served every kind of call, and each call writes the workspace GEMINI.md from its own system prompt. A follow-up,
   memory or summary call (another prompt) rewrote it and the plugin then killed the chat's worker, also in the middle of a reply when
   the two ran at once. Here each system prompt gets a client of its own (own workspace and GEMINI.md); a few are kept.
2. The next turn is not a strict extension of the previous request: this turn's lines (TURN_NOTE) are rebuilt every turn and the
   previous turn's tool rounds are stored only as its final text. `continuation` recognises exactly that shape, so the session goes
   on and only the new turn is sent.
3. A worker that ends while the conversation goes on (agy tried a tool of its own, a cancel, an error, a request that does not
   continue the session) is replaced at once, so the next call finds a process that has its start-up behind it or under way.

Tools loaded on demand during a session are told to the live session too (the plugin puts tools only in a session's first message).
Set ASSISTANT_AGY_SESSIONS (default 3) for how many sessions are kept and ASSISTANT_AGY_WARM=0 to turn the replacement off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

from .types import TURN_NOTE

log = logging.getLogger(__name__)

IDLE_CLOSE_SECONDS = 20 * 60   # a session unused this long is closed (its agy process ends); the most recently used one stays
# Kinds of call whose next call continues the same conversation (usage.kind_of_source names). Every other kind (memory upkeep, briefing,
# reflection, follow-ups ... and an unknown one) is "one-off": it never takes a conversation session's place and its own session is
# closed soon, since the next call rarely continues it.
CONVERSATION_PURPOSES = frozenset({"chat", "worker", "schedule", "task", "checklist"})
ONE_OFF_IDLE_SECONDS = 120
# A live session keeps every tool result in full, while a fresh start shortens old ones (the plugin's whole prompt is at most 150,000
# characters, old tool results 300 each). Past this much the session starts over from the server's own (compacted) conversation.
SESSION_MAX_CHARS = 300_000
TOOLS_NOTE = ("[Not from the user: the app changed the tools you can call. From now on these are your tools (OpenAI function schema); "
              "call them with <tool_call> blocks as before.]\n")
NO_TOOLS_NOTE = "[Not from the user: no tools are available for this reply. Answer in plain text.]"


# measured 2026-11-09 (agy 1.5.3): one live agy session holds ~250 MB RSS; the sidecar's own runner ~60 MB
SESSION_MEMORY = 250 * 1024 * 1024
RUNNER_MEMORY = 60 * 1024 * 1024
DEFAULT_SESSIONS = 3


def _memory_cap() -> int | None:
    """The container's memory cap (None = no cap or unknown)."""
    try:
        from ..quota import cgroup_limits
        return cgroup_limits()["memory_limit"]
    except Exception:  # noqa: BLE001 - outside a container, or an unreadable cgroup: no cap known
        return None


def max_sessions(memory_cap: int | None | object = ...) -> int:
    """How many agy sessions are kept: ASSISTANT_AGY_SESSIONS when set, else 3, fewer when the container's memory cap cannot hold
    that many (a small size gives the agy container ~200 MB: one session; a session past the cap would get the container killed)."""
    raw = os.environ.get("ASSISTANT_AGY_SESSIONS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    cap = _memory_cap() if memory_cap is ... else memory_cap
    if isinstance(cap, int) and cap > 0:
        return max(1, min(DEFAULT_SESSIONS, (cap - RUNNER_MEMORY) // SESSION_MEMORY))
    return DEFAULT_SESSIONS


def warm_enabled() -> bool:
    return os.environ.get("ASSISTANT_AGY_WARM", "1").strip().lower() not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------- which session a request continues (pure functions)

def _same(a: Any, b: Any) -> bool:
    """The plugin's own test for "the same message" (prompt._messages_match_prefix)."""
    return (isinstance(a, dict) and isinstance(b, dict) and a.get("role") == b.get("role") and a.get("content") == b.get("content")
            and a.get("tool_calls") == b.get("tool_calls"))


def _is_turn_note(m: Any) -> bool:
    return isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].startswith(TURN_NOTE)


def _last_turn_note(msgs: list[dict[str, Any]]) -> int | None:
    return next((i for i in range(len(msgs) - 1, -1, -1) if _is_turn_note(msgs[i])), None)


def _flat(text: Any) -> str:
    return " ".join(str(text or "").split())


def _next_turn(prev: list[dict[str, Any]], incoming: list[dict[str, Any]], said: str) -> int | None:
    """Where the new turn starts in `incoming` when it is the turn after `prev` in the same conversation, else None.

    prev     = [... base ..., TURN_NOTE(n), user(n), (tool rounds of turn n)]
    incoming = [... base ..., user(n), assistant(n) ..., TURN_NOTE(n+1), user(n+1)]
    The base must be unchanged (no summary rewritten, no history dropped), the user messages in between must be the ones turn n was
    sent, and every assistant message in between must be text this session wrote itself (`said`): anything else, such as a message
    another job added to the conversation, means the session has not seen it and has to start over."""
    k = _last_turn_note(prev)
    if k is None or len(incoming) <= k or not all(_same(a, b) for a, b in zip(prev[:k], incoming[:k])):
        return None
    j = _last_turn_note(incoming)
    if j is None or j <= k:
        return None
    asked = [m.get("content") for m in prev[k + 1:] if isinstance(m, dict) and m.get("role") == "user"]
    gap = incoming[k:j]
    if not asked or not gap or not isinstance(gap[0], dict) or gap[0].get("role") != "user" or gap[0].get("content") != asked[0]:
        return None
    wrote, rest, answered = _flat(said), iter(asked), False
    for m in gap:
        role = m.get("role") if isinstance(m, dict) else None
        if role == "assistant" and not m.get("tool_calls"):
            head = _flat(m.get("content"))[:200]
            if head and head not in wrote:
                return None
            answered = True
        elif role != "user" or not any(m.get("content") == c for c in rest):   # in order: `rest` is consumed as it is searched
            return None
    return j if answered else None


def continuation(prev_req: list[dict[str, Any]] | None, prev_sent: list[dict[str, Any]] | None,
                 incoming: list[dict[str, Any]], said: str = "") -> tuple[list[dict[str, Any]], bool] | None:
    """(messages to hand the plugin, new turn?) so that its live session, which has seen `prev_sent` (built from the request
    `prev_req`), gets only what is new in `incoming`; None when `incoming` does not go on from that session.

    The plugin keeps a session going only when a request starts with exactly the messages of the previous one. Handing it
    `prev_sent` + the new messages meets that test, and the new messages are what it sends."""
    if not prev_req or not prev_sent:
        return None
    if len(incoming) > len(prev_req) and all(_same(a, b) for a, b in zip(prev_req, incoming)):
        return [*prev_sent, *incoming[len(prev_req):]], False   # the next tool round of the same turn
    j = _next_turn(prev_req, incoming, said)
    return None if j is None else ([*prev_sent, *incoming[j:]], True)


def session_chars(messages: list[dict[str, Any]]) -> int:
    from .vendor.antigravity.prompt import _render_message_content
    return sum(len(_render_message_content(m.get("content"))) + (len(json.dumps(m["tool_calls"])) if m.get("tool_calls") else 0)
               for m in messages if isinstance(m, dict))


def rules_key(messages: list[dict[str, Any]]) -> str:
    """The system text the plugin turns into GEMINI.md: requests with the same one can share a session."""
    from .vendor.antigravity.prompt import _render_message_content
    parts = [_render_message_content(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    return hashlib.sha256("\n\n".join(p for p in parts if p).encode()).hexdigest()


def tools_key(tools: list[dict[str, Any]] | None) -> str:
    return hashlib.sha256(json.dumps(tools or [], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def tools_note(tools: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not tools:
        return {"role": "user", "content": NO_TOOLS_NOTE}
    return {"role": "user", "content": TOOLS_NOTE + json.dumps(tools, ensure_ascii=False)}


# ---------------------------------------------------------------- the plugin client, with a warm replacement worker

# ---------------------------------------------------------------- the sign-in token, kept safe from two agy starts at once

# 2026-11-11 22:30: two agy processes started 0.3 s apart (the warm at start-up and one more) and the sign-in token file was left
# EMPTY (0 bytes): every session links the one real token file into its own home, and a start refreshes it in place, so two
# starts writing it together can truncate it. Then nothing works until the owner signs in again. So: starts are one at a time,
# with a short gap for the start's own token refresh, and a good token is kept as a private copy that puts it back if it is ever
# found empty.
SPAWN_GAP_SECONDS = 3.0
TOKEN_MIN_BYTES = 11          # agy's own "is there a token" test: more than 10 bytes
BACKUP_SUFFIX = ".kelo-keep"
_spawn_lock = threading.Lock()


def _token_path() -> Any:
    from .vendor.antigravity.process import _TOKEN_FILENAMES, resolve_real_token_path
    real = resolve_real_token_path()
    if real is not None:
        return real
    from pathlib import Path   # an emptied token is not "existing" for the vendor's scan: look for the file itself
    base = Path(os.getenv("ANTIGRAVITY_CONFIG_DIR", "").strip() or (Path.home() / ".gemini" / "antigravity-cli"))
    return next((base / n for n in _TOKEN_FILENAMES if (base / n).is_file()), None)


def guard_token() -> str:
    """Keep a private copy of a good token; put it back when the token is found empty or missing its contents.
    Returns "kept" / "restored" / "" (nothing to do). Never reads the token into logs."""
    import shutil
    try:
        path = _token_path()
        if path is None:
            return ""
        keep = path.with_name(path.name + BACKUP_SUFFIX)
        size = path.stat().st_size if path.is_file() else 0
        if size >= TOKEN_MIN_BYTES:
            if not keep.is_file() or keep.read_bytes() != path.read_bytes():
                tmp = keep.with_name(keep.name + ".tmp")
                shutil.copyfile(path, tmp)
                os.chmod(tmp, 0o600)
                os.replace(tmp, keep)
                return "kept"
            return ""
        if keep.is_file() and keep.stat().st_size >= TOKEN_MIN_BYTES:
            tmp = path.with_name(path.name + ".kelo-restore")
            shutil.copyfile(keep, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            log.warning("agy: the sign-in token was found empty; put back the last good copy")
            return "restored"
    except OSError as e:
        log.warning("agy: could not check the sign-in token (%s)", type(e).__name__)
    return ""


def client_class() -> type:
    """The vendored AntigravityClient (left unmodified) with two additions: it counts its process starts, and when its worker ends
    while the client is still in use it starts the replacement at once (agy needs ~5 s before it can take a message)."""
    from .vendor.antigravity.client import AntigravityClient

    def kelo_alive_now(c: Any, model: str, effort: str | None) -> bool:
        try:
            return bool(c.kelo_alive(model, effort))
        except Exception:  # noqa: BLE001
            return False

    class KeloAgyClient(AntigravityClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.spawns = 0
            self.kelo_key = ""
            self.kelo_busy = False
            self.kelo_used = 0.0
            self.kelo_req: list[dict[str, Any]] | None = None    # the last request this session answered, as the server sent it
            self.kelo_sent: list[dict[str, Any]] | None = None   # the same, as handed to the plugin (what the session has seen)
            self.kelo_tools: str | None = None                  # tools_key of the tools the session was told about
            self.kelo_purpose = ""                              # what the call that made or last used it was for (CONVERSATION_PURPOSES)
            self.kelo_said = ""                                 # the text the session wrote in its current turn
            self._kelo_spawning = False

        def _get_or_spawn_worker(self, model: str, effort: str | None) -> Any:
            with self._lock:
                before = self._worker_proc
                self._kelo_spawning = True
                try:
                    if kelo_alive_now(self, model, effort):
                        proc = super()._get_or_spawn_worker(model, effort)   # the live worker: no new start
                    else:
                        with _spawn_lock:   # one agy start at a time (see guard_token)
                            guard_token()
                            proc = super()._get_or_spawn_worker(model, effort)
                            if proc is not before:
                                time.sleep(SPAWN_GAP_SECONDS)   # its own token refresh happens now; the next start waits
                            guard_token()
                finally:
                    self._kelo_spawning = False
                if proc is not before:
                    self.spawns += 1
                return proc

        def _terminate_worker_locked(self) -> None:
            model, effort, had = self._worker_model, self._worker_effort, self._worker_proc is not None
            super()._terminate_worker_locked()
            if had and not self.is_closed and not self._kelo_spawning and warm_enabled():
                try:
                    self._get_or_spawn_worker(model or "", effort)
                except Exception as e:  # noqa: BLE001 - the next call spawns one itself
                    log.warning("agy: could not start the replacement worker (%s)", type(e).__name__)

        def kelo_alive(self, model: str | None, effort: str | None) -> bool:
            """A live worker that the next call with this model would use as it is."""
            m, e = self._resolve_model_and_effort(model, effort)
            p = self._worker_proc
            return p is not None and p.poll() is None and self._worker_model == m and self._worker_effort == e

    return KeloAgyClient


class Lease:
    """One model call's hold on a session: `messages` is what to hand the client; `release` records how it went."""

    def __init__(self, pool: AgyPool, client: Any, request: list[dict[str, Any]], messages: list[dict[str, Any]], tools: str,
                 mode: str, reserved: bool, transient: bool = False, reason: str = "", purpose: str = ""):
        self.pool, self.client, self.request, self.messages, self.tools = pool, client, request, messages, tools
        self.mode = mode            # "round" (same turn goes on), "turn" (next turn goes on), "new" (session starts over)
        self.reserved = reserved    # False: every session was busy; the plugin answers with a one-off process
        self.transient = transient  # a client made for this one call (the pool was full): closed when it ends
        self.reason = reason        # why a "new" session was needed (for the log)
        self.purpose = purpose
        self._done = False

    def release(self, ok: bool, text: str = "") -> None:
        if self._done:
            return
        self._done = True
        self.pool._release(self, ok, text)


class AgyPool:
    """The agy sessions of one runner: one plugin client per system prompt (more when the same prompt runs twice at once), at most
    `max_clients`, the least recently used one closed first."""

    def __init__(self, factory: Any = None, *, max_clients: int | None = None, idle_close: float = IDLE_CLOSE_SECONDS,
                 clock: Any = time.monotonic):
        self._factory = factory or client_class()
        self._max = max_clients or max_sessions()
        self._idle_close = idle_close
        self._clock = clock
        self._clients: list[Any] = []
        self._lock = threading.Lock()
        self.closed = False

    @property
    def clients(self) -> list[Any]:
        return list(self._clients)

    def lease(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, model: str | None = None,
              effort: str | None = None, purpose: str = "") -> Lease:
        if self.closed:
            raise RuntimeError("agy sessions are closed")
        key, tkey = rules_key(messages), tools_key(tools)
        conversation = purpose in CONVERSATION_PURPOSES
        drop: list[Any] = []
        with self._lock:
            now = self._clock()
            newest = max((c for c in self._clients if _is_conversation(c)), key=lambda c: c.kelo_used, default=None)
            for c in list(self._clients):   # sessions nobody used for a while: their agy process ends
                if c.is_closed:
                    stale = True
                elif c.kelo_busy:
                    stale = False
                elif not _is_conversation(c):
                    stale = now - c.kelo_used > ONE_OFF_IDLE_SECONDS
                else:
                    stale = c is not newest and now - c.kelo_used > self._idle_close
                if stale:
                    self._clients.remove(c)
                    drop.append(c)
            mine = [c for c in self._clients if c.kelo_key == key]
            free = sorted((c for c in mine if not c.kelo_busy), key=lambda c: -c.kelo_used)
            pick, plan, reason = None, None, "no session for this prompt"
            for c in free:
                if not c.kelo_alive(model, effort):
                    why = "worker not alive"
                elif (got := continuation(c.kelo_req, c.kelo_sent, messages, c.kelo_said)) is None:
                    why = "conversation changed"
                elif session_chars(got[0]) > SESSION_MAX_CHARS:
                    why = "session too long"   # grown too long: this one starts over below
                else:
                    pick, plan = c, got
                    break
                if reason == "no session for this prompt":
                    reason = why
            if pick is None and free:
                pick = free[0]   # its workspace already holds this prompt's GEMINI.md
            if pick is None:
                if len(self._clients) >= self._max:
                    idle = [c for c in self._clients if not c.kelo_busy]
                    one_off = [c for c in idle if not _is_conversation(c)]
                    pool_ = one_off or (idle if conversation else [])   # a one-off call never takes a conversation's place
                    if pool_:
                        victim = min(pool_, key=lambda c: c.kelo_used)
                        self._clients.remove(victim)
                        drop.append(victim)
                if len(self._clients) < self._max:
                    pick = self._factory()
                    pick.kelo_key = key
                    pick.kelo_purpose = purpose
                    self._clients.append(pick)
            elif conversation:
                pick.kelo_purpose = purpose
            if pick is None and mine:   # every session is answering something: the plugin's one-off process in one of this prompt
                lease = Lease(self, mine[0], messages, messages, tkey, "new", reserved=False,
                              reason="all sessions busy (one-off process)", purpose=purpose)
            elif pick is None:   # never in another prompt's client: its GEMINI.md would be rewritten under a running reply
                lease = Lease(self, self._factory(), messages, messages, tkey, "new", reserved=False, transient=True, purpose=purpose,
                              reason=("pool full, one-off kept off the chat session (transient)" if not conversation
                                      else "all sessions busy (one-off process)"))
            else:
                pick.kelo_busy, pick.kelo_used = True, now
                if plan is None:
                    lease = Lease(self, pick, messages, messages, tkey, "new", reserved=True, reason=reason, purpose=purpose)
                else:
                    sent, new_turn = plan
                    if pick.kelo_tools != tkey:   # the tool list changed since the session was told: tell it before the new messages
                        sent = [*sent[:len(pick.kelo_sent)], tools_note(tools), *sent[len(pick.kelo_sent):]]
                    lease = Lease(self, pick, messages, sent, tkey, "turn" if new_turn else "round", reserved=True, purpose=purpose)
        log.info("agy session %s: %s", purpose or "unknown", lease.mode if lease.mode != "new" else f"new ({lease.reason})")
        for c in drop:
            _close_soon(c)
        return lease

    def warm(self, messages: list[dict[str, Any]], model: str | None = None, effort: str | None = None) -> bool:
        """Start the chat's agy process for this system prompt without sending the model anything (no tokens): the workspace rules
        are written the way a call writes them and the worker is spawned, so the first real chat call finds it up and only pays for
        its own prompt. True when a live worker for this prompt is there afterwards. Blocking (the spawn takes a moment)."""
        lease = self.lease(messages, None, model, effort, purpose="chat")
        try:
            if not lease.reserved or lease.transient:
                return False   # every session is busy: warming is never worth a one-off process
            c = lease.client
            c._sync_workspace_rules(messages)
            c._get_or_spawn_worker(*c._resolve_model_and_effort(model, effort))
            return bool(c.kelo_alive(model, effort))
        finally:
            lease.release(False)   # nothing was said: the session carries no conversation state, the next call starts it as "new"

    def _release(self, lease: Lease, ok: bool, text: str) -> None:
        c = lease.client
        if lease.transient:
            _close_soon(c)
            return
        with self._lock:
            if not lease.reserved:
                return
            c.kelo_busy, c.kelo_used = False, self._clock()
            with c._lock:
                hist = list(c._worker_history)
            recorded = ok and len(hist) == len(lease.messages) and all(a is b for a, b in zip(hist, lease.messages))
            if recorded:   # the plugin kept this exchange in its live session: the next call can go on from it
                c.kelo_said = (c.kelo_said + "\n" + text) if lease.mode == "round" else text
                c.kelo_req, c.kelo_sent, c.kelo_tools = lease.request, lease.messages, lease.tools
            else:
                c.kelo_req = c.kelo_sent = c.kelo_tools = None
                c.kelo_said = ""

    def close(self) -> None:
        with self._lock:
            self.closed = True
            clients, self._clients = self._clients, []
        for c in clients:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


def _is_conversation(client: Any) -> bool:
    return getattr(client, "kelo_purpose", "") in CONVERSATION_PURPOSES


def _close_soon(client: Any) -> None:
    """Close a session off the caller's thread (it waits for agy to exit)."""
    threading.Thread(target=lambda: _quiet_close(client), daemon=True, name="agy-close").start()


def _quiet_close(client: Any) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass

