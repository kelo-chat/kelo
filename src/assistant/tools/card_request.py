"""request_card: ask the user for a payment card right in the chat, e.g. when a purchase needs one and the wallet has none.

A secure card entry appears in the chat (the same card form as the wallet page: number, expiry, CVC, nickname and how it pays). The
card goes from the app straight to the wallet (POST /api/wallet/cards, sealed on the phone); the agent only hears that a card was
added, by its nickname and last four digits. Kept apart from purchase.py on purpose: it only asks, it never pays.
"""

from __future__ import annotations

from typing import Any

from ..agent import Tool
from ..netguard import NetGuardError
from ..secret_requests import ask
from .vault_tools import site_host


def card_request_tools() -> list[Tool]:
    async def request_card(args: dict[str, Any]) -> str:
        reason = str(args.get("reason") or "").strip()[:200]
        if not reason:
            return "error: reason is required: one short line on what the card is for (e.g. the order it will pay for)"
        site = str(args.get("site") or "").strip()
        hosts: list[str] = []
        if site:
            try:
                hosts = [site_host(site)]
            except NetGuardError as e:
                return f"error: {e}"
        req, state = await ask("card", label=reason, hosts=hosts, reason=reason)
        if state == "saved":
            card = ((req or {}).get("result") or {}).get("card") or {}
            name = card.get("label") or "the new card"
            ends = f" (ends {card['last4']})" if card.get("last4") else ""
            return (f"The user added the card '{name}'{ends} to the wallet; you "
                    "never see its number. Go on with the task now, without waiting for a message: check it with wallet_cards and pay "
                    "with it by that nickname.")
        if state == "cancelled":
            return ("The user closed the card entry without adding a card. Do not ask for a card again unless they bring it up; say in "
                    "one short line what this means for the task (e.g. it cannot be paid for now).")
        if state in ("replaced", "expired"):
            return "This card entry was replaced by a newer one or ran out; no card was added."
        return ("A secure card entry is now shown in the chat, right below your message. Tell the user in one short sentence to add the "
                "card in the entry below; never ask for card numbers in chat, and do not send them to another page. Once they save it "
                "you are told and go on by yourself.")

    return [Tool("request_card",
                 "Ask the user to add a payment card when a payment needs one and the wallet has none that can pay (see wallet_cards). "
                 "A secure card entry appears in the chat right below your message; the card goes straight to the user's wallet and "
                 "you never see its number. Never ask for "
                 "card numbers in chat.",
                 {"type": "object", "properties": {
                     "reason": {"type": "string", "description": "one short line: what the card is for (the order or payment)"},
                     "site": {"type": "string", "description": "optional: the https site where it will pay"}},
                  "required": ["reason"]},
                 request_card)]   # no approval card: adding the card in the entry is the user's own choice (they can cancel it)
