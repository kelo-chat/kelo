"""The stored conversation: the messages (with the pictures Kelo showed, chat_images.py), clearing the chat and what was made from it (chat_clear.py), and the rolling summary of older messages (CONVERSATION COMPACTION in server.py)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from fastapi import Depends
from ..chat_images import chat_image_routes
from ..calls import with_call
from ..chat_search import DEFAULT_AROUND, DEFAULT_LIMIT, NOT_THE_USERS_WORDS
from .. import chat_search
from ..tools.browser import (
    one_run_tab,
)
from ..browser_history import ActionArchive
from ..chat_clear import clear_tables
from fastapi import APIRouter, HTTPException
from .context import AppContext

log = logging.getLogger("uvicorn.error")


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    briefing = ctx.briefing
    broker = ctx.broker
    browser_more = ctx.browser_more
    cancel_turn = ctx.cancel_turn
    chat_images = ctx.chat_images
    chat_attachments = ctx.chat_attachments
    comparisons = ctx.comparisons
    compactor = ctx.compactor
    deliveries = ctx.deliveries
    ended_tabs = ctx.ended_tabs
    chat_views = ctx.chat_views   # the browser tabs and the phone the chat shows (chat_views.py)
    goal_book = ctx.goal_book
    handover_seen = ctx.handover_seen
    journal_model = ctx.journal_model
    owner_tasks = ctx.owner_tasks
    pending_approvals = ctx.pending_approvals
    pending_links = ctx.pending_links
    pending_replies = ctx.pending_replies
    pending_comparisons = ctx.pending_comparisons
    require_auth = ctx.require_auth
    resolved_checks = ctx.resolved_checks
    running_turns = ctx.running_turns
    session = ctx.session
    settings = ctx.settings
    state_root = ctx.state_root
    store = ctx.store
    tab_failed = ctx.tab_failed
    thumbs = ctx.thumbs
    tool_scopes = ctx.tool_scopes
    turns = ctx.turns
    window_for = ctx.window_for
    workers = ctx.workers

    chat_image_routes(router, chat_images, require_auth)

    def decorate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The extra fields GET /api/messages puts on each message (replies, comparison, images, attachments, briefing card)."""
        rows = [with_call(r) for r in rows]
        rows = goal_book.replies_for(rows)   # tap-to-answer buttons on the newest agent messages
        rows = ctx.comparisons.for_messages(rows)   # comparison cards (comparison.py)
        rows = chat_images.for_messages(rows)  # screenshots Kelo showed with a reply (chat_images.py)
        rows = chat_attachments.for_messages(rows)   # files the user sent with a message (chat_attachments.py)
        return briefing.attach(rows)         # the morning briefing's card on its message (briefing.py)

    @router.get("/api/messages", dependencies=[Depends(require_auth)])
    async def get_messages(limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.messages(limit=max(1, min(limit, 500)), before_id=before_id)
        rows = [m for m in rows if not (m["role"] == "user" and m["content"].startswith(NOT_THE_USERS_WORDS))]   # not the user's words
        return decorate(rows)

    @router.get("/api/messages/search", dependencies=[Depends(require_auth)])
    async def search_messages(q: str = "", limit: int = DEFAULT_LIMIT, before: int | None = None) -> list[dict[str, Any]]:
        """Matches in the whole conversation, newest first (chat_search.py). `before` = only ids below it (page with the last id)."""
        return chat_search.search(store, q, limit=limit, before=before)

    @router.get("/api/messages/around", dependencies=[Depends(require_auth)])
    async def messages_around(id: int, n: int = DEFAULT_AROUND) -> list[dict[str, Any]]:
        """The messages around a hit (n before, the message, n after), oldest first, shaped like GET /api/messages. 404 if unknown or hidden."""
        rows = chat_search.around(store, id, n=n)
        if rows is None:
            raise HTTPException(status_code=404, detail={"detail": "message not found", "code": "message_not_found"})
        return decorate(rows)

    clearing = asyncio.Lock()   # one clear of the chat history at a time

    async def clear_history() -> dict[str, Any]:
        """Clear the chat history and what came of it: stop the work first, then the tables, then memory and the pictures on disk."""
        jobs = await workers.stop_all()           # the clean stop path of each job (its turn cancelled, its tab retired)
        cancelled: set[str] = set()
        for _ in range(3):                         # a reply that started meanwhile (another phone, a schedule) is stopped too
            live = [t for t in turns.values() if not t.done]
            if not live:
                break
            for t in live:
                cancel_turn(t)
                cancelled.add(t.id)
            await asyncio.wait([asyncio.ensure_future(t.ended.wait()) for t in live], timeout=5)   # what was said is saved, then deleted
            await asyncio.sleep(0)
        for approval_id in [*pending_approvals, *broker.pending]:   # anything still waiting for a person is denied, so nothing hangs
            broker.discard(approval_id)
            deliveries.discard(approval_id)
        pending_approvals.clear()
        resolved_checks.clear()
        handover_seen.clear()
        cleared: dict[str, Any] = clear_tables(store)
        tool_scopes.clear("chat")   # a new conversation starts with the core tools again
        for tid in [k for k, t in turns.items() if t.done]:   # a phone asking for a finished reply again gets 404, not its words
            turns.pop(tid, None)
        pending_replies.clear()
        pending_comparisons.clear()
        pending_links.clear()
        chat_images.clear()
        owner_tasks.clear()
        workers.forget()
        tabs = 0
        if session is not None:   # tabs of finished runs, and the last pictures kept of them
            for key in session.open_keys():
                if one_run_tab(key) and not any(t.owner == key for t in running_turns()):
                    try:
                        tabs += int(bool(await session.close_tab(key)))
                    except Exception:  # noqa: BLE001 - the sweeper closes it later
                        log.exception("clear history: closing tab %s failed", key)
                    thumbs.pop(key, None)
                    tab_failed.pop(key, None)
        ended_tabs.clear()
        for view in chat_views.items():   # the chat shows nothing afterwards: its views close with the history
            chat_views.close(view.key, "cleared")
        archive = browser_more.archive if browser_more is not None else None
        if archive is None and state_root is not None:   # pictures kept while this server had a browser are deleted all the same
            archive = ActionArchive(state_root / "browser-history")
        cleared["browser_pictures"] = archive.clear() if archive is not None else 0
        cleared["browser_tabs"] = tabs
        log.info("chat history cleared: %d jobs and %d replies stopped", jobs, len(cancelled))
        return {"ok": True, "stopped": {"jobs": jobs, "turns": len(cancelled)}, "cleared": cleared}

    ctx.add(clear_history=clear_history, clearing=clearing)   # api/data.py: "start over" clears the chat the same way

    @router.delete("/api/messages", dependencies=[Depends(require_auth)])
    async def delete_messages() -> dict[str, Any]:
        """Clear the chat history and everything made from it (chat_clear.py); older apps call this for "Delete chat history" too."""
        async with clearing:
            return await clear_history()

    @router.get("/api/conversation/summary", dependencies=[Depends(require_auth)])
    async def get_conversation_summary() -> dict[str, Any]:
        cur = store.conversation_summary()
        chosen = journal_model()
        w = window_for(*chosen) if chosen else None
        return {**cur, "provider": chosen[0] if chosen else None, "model": chosen[1] if chosen else None,
                "context_window": w.context if w else None, "max_output": w.max_output if w else None, "source": w.source if w else None,
                "threshold": settings.compact_at, "last_request_tokens": compactor.last_request_tokens, "last_error": compactor.last_error}

    @router.delete("/api/conversation/summary", dependencies=[Depends(require_auth)])
    async def delete_conversation_summary() -> dict[str, bool]:
        store.clear_conversation_summary()
        return {"ok": True}
    return router
