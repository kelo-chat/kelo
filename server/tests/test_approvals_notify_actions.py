"""What a phone notification may offer for a pending approval (api/approvals.notify_actions): Allow and Deny for plain approvals, only Deny for a
payment (an Allow needs the phone's biometric signature in the app), nothing for a card that needs input in the app."""

import httpx

from assistant.api.approvals import notify_actions
from assistant.agent import Tool
from test_approval_sign import app_settings, pending_one, plain_tool, replies, start_turn, pair
from test_server import AUTH, Running, follow
from assistant.server import create_app


def test_a_plain_approval_may_be_allowed_or_denied():
    for item in ({"name": "send", "commit": ""}, {"name": "browser_click", "commit": "submit"}, {"name": "phone_send_text", "lines": []}):
        assert notify_actions(item) == ["allow", "deny"], item


def test_a_payment_never_gets_allow():
    for item in ({"name": "confirm_purchase", "payment": {"amount": 1}}, {"name": "checkout_review"}, {"name": "browser_click", "commit": "pay"},
                 {"name": "x", "payment": {"amount_text": "1"}, "card": {"needs_card": True}}, {"name": "confirm_purchase", "pin": {"id": 1}}):
        assert notify_actions(item) == ["deny"], item


def test_a_card_that_needs_input_in_the_app_offers_nothing():
    needs_input = [
        {"name": "x", "pin": {"id": 1, "needs_pin": True}},                      # a PIN
        {"type": "payment_step", "kind": "pin_pad"},                             # a payment step
        {"name": "x", "card": {"id": 1, "needs_card": True}},                    # a wallet or card choice
        {"name": "x", "needs_card": True},
        {"name": "request_login"}, {"name": "add_service_key"}, {"name": "browser_login"},   # a secret or a login
        {"name": "find_login_code", "login_code": {"code": "123456"}},            # a code from the mail
        {"kind": "human_check", "name": "browser_check"},                        # a human check
        {"kind": "location", "name": "my_location"},                             # a location request
        {"name": "x", "commit": "security"},
    ]
    for item in needs_input:
        assert notify_actions(item) == [], item


def paying_tool():
    """A call that asks for approval and carries a payment part (the chat itself no longer holds the checkout tools)."""
    async def handler(args):
        return "paid"

    async def describe(args):
        return "Pay the shop"

    return Tool("pay_shop", "pay_shop", {"type": "object"}, handler, requires_approval=True, describe=describe,
                approval_extra=lambda approval_id, args: {"payment": {"site": "shop.example", "amount_text": "12,000"}})


async def test_the_pending_list_carries_notify_actions_per_item(tmp_path):
    fn = replies("pay_shop", "send")
    async with Running(create_app(app_settings(tmp_path), tools=[paying_tool(), plain_tool()], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        phone, _ = await pair(c)
        tid = await start_turn(c, "결제해줘")
        card = await pending_one(c, phone)
        assert card["name"] == "pay_shop" and card["notify_actions"] == ["deny"]
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False}, headers=phone)
        await follow(c, tid)
        tid = await start_turn(c, "보내줘")
        card = await pending_one(c, phone)
        assert card["name"] == "send" and card["notify_actions"] == ["allow", "deny"]
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False}, headers=phone)
        await follow(c, tid)
