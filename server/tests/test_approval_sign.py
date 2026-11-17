"""Signed payment approvals (docs/approval-signing.md, approval_sign.py): the message bytes, the shared vectors every app tests
against, which approvals count as payments, the phone's approval key, and the answer route's checks."""

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from assistant import approval_sign
from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.tool_roles import PAY_TOOLS

from split_pay import SHOP, Split, direct, job, settle

SHOP_PAGE = SHOP
from test_server import AUTH, TOKEN, Running, follow

VECTORS = Path(__file__).resolve().parents[2] / "docs" / "security" / "approval-sign-vectors.json"


def new_key():
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, base64.b64encode(spki).decode()


def sign(key, message: bytes) -> str:
    return base64.b64encode(key.sign(message, ec.ECDSA(hashes.SHA256()))).decode()


def app_settings(tmp_path, **kw):
    return Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), **kw)


# ---- the pure rules and the shared vectors --------------------------------------------------------------------------------------

def test_the_vectors_file_verifies_and_its_bad_cases_do_not():
    doc = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert doc["alg"] == "ES256" and len(doc["approvals"]) == 3 and len(doc["bad"]) == 2
    for a in doc["approvals"]:
        msg = approval_sign.approval_message(a["approval_id"], a["decision"] == "approve", a["issued_at"])
        assert msg.hex() == a["message_hex"]
        assert approval_sign.verify(doc["public_key_spki_b64"], msg, a["sig_b64"]) is True
    for b in doc["bad"]:
        assert approval_sign.verify(doc["public_key_spki_b64"], bytes.fromhex(b["message_hex"]), b["sig_b64"]) is False, b["name"]


def test_the_signed_message_is_exactly_the_documented_bytes():
    assert approval_sign.approval_message("abc", True, 1700000000) == b"kelo-approval v1\nabc\napprove\n1700000000"
    assert approval_sign.approval_message("abc", False, 7) == b"kelo-approval v1\nabc\ndeny\n7"


def test_payments_are_the_purchase_card_and_the_paying_click_only():
    assert approval_sign.is_payment({"name": "confirm_purchase"})
    assert approval_sign.is_payment({"name": "x", "payment": {"site": "shop.example"}})
    assert approval_sign.is_payment({"name": "browser_click", "commit": "pay"})
    assert not approval_sign.is_payment({"name": "send"})
    assert not approval_sign.is_payment({"name": "purchase_result", "ask": "Charged 2 instead of 1"})   # a wrong charge: no money moves
    assert not approval_sign.is_payment({"name": "browser_click", "commit": "security"})
    assert not approval_sign.is_payment({})


def test_only_p256_keys_with_es256_are_accepted_and_garbage_never_verifies():
    p384 = ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    for bad_key in (base64.b64encode(p384).decode(), "not base64 !!", "", base64.b64encode(b"junk").decode()):
        try:
            approval_sign.check_public_key("ES256", bad_key)
        except approval_sign.BadKey:
            pass
        else:
            raise AssertionError(f"accepted {bad_key[:20]!r}")
    _, pub = new_key()
    try:
        approval_sign.check_public_key("RS256", pub)
    except approval_sign.BadKey:
        pass
    else:
        raise AssertionError("accepted another alg")
    assert approval_sign.verify(pub, b"m", "AAAA") is False
    assert approval_sign.verify("junk", b"m", "AAAA") is False


# ---- through the server ---------------------------------------------------------------------------------------------------------

async def pair(c, name="폰"):
    code = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
    r = await c.post("/api/pair", json={"code": code, "name": name})
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["device_id"]


async def pending_one(c, phone):
    for _ in range(200):
        items = (await c.get("/api/approvals/pending", headers=phone)).json()["items"]
        if items:
            return items[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no approval is waiting")


def payment_tool():
    async def handler(args):
        return "paid"

    async def describe(args):
        return "Pay the shop"

    return Tool("confirm_purchase", "confirm_purchase", {"type": "object"}, handler, requires_approval=True, describe=describe,
                approval_extra=lambda approval_id, args: {"payment": {"site": "shop.example", "amount_text": "12,000"}})


def plain_tool():
    async def handler(args):
        return "sent"

    async def describe(args):
        return "Send a note"

    return Tool("send", "send", {"type": "object"}, handler, requires_approval=True, describe=describe)


def replies(*calls):
    """A fake model: for each turn, the tool call named, then the closing text (one turn per name)."""
    seq = []
    for name in calls:
        seq += [[ToolCall(f"c-{name}-{len(seq)}", name, "{}"), Done("tool_calls")], [TextDelta("ok"), Done("stop")]]
    it = iter(seq)

    async def fn(provider, request, **kw):
        for ev in next(it):
            yield ev
    return fn


def shop_browser(split):
    """A checkout job needs a browser (a copy without one says so): a page of the shop the jobs pay at."""
    if not split:
        return None
    from assistant.tools.browser import BrowserSession
    from test_agent_profile import ShotPage

    async def connect():
        return ShotPage(SHOP_PAGE)
    return BrowserSession(connect=connect)


def pay_model(split, *names):
    """The same turns two ways: with the switch off the chat makes each call itself (the old path); with the split on (the default) a
    payment is made by a checkout job the chat hands over, and any other call stays with the chat."""
    if split:
        return Split(*[job((n, {})) if n in PAY_TOOLS else direct(n) for n in names])
    return replies(*names)


async def start_turn(c, text="결제해줘"):
    return (await c.post("/api/turns", json={"text": text}, headers=AUTH)).json()["id"]


async def test_a_phone_stores_its_approval_key_and_replacing_it_needs_the_old_one(tmp_path):
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=replies())) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        phone, device_id = await pair(c)
        old_key, old_pub = new_key()
        listing = {d["id"]: d for d in (await c.get("/api/devices", headers=phone)).json()["devices"]}
        assert listing[device_id]["approval_key"] is False and "approval_key" in listing[device_id]
        assert "public_key" not in json.dumps(listing)
        r = await c.post("/api/devices/approval-key", json={"alg": "ES256", "public_key": old_pub}, headers=phone)
        assert r.status_code == 200 and r.json() == {"ok": True, "approval_key": True}
        assert {d["id"]: d for d in (await c.get("/api/devices", headers=phone)).json()["devices"]}[device_id]["approval_key"] is True
        assert (await c.post("/api/devices/approval-key", json={"alg": "ES256", "public_key": old_pub}, headers=AUTH)).status_code == 403
        assert (await c.post("/api/devices/approval-key", json={"alg": "RS256", "public_key": old_pub}, headers=phone)).status_code == 422
        p384 = base64.b64encode(ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        assert (await c.post("/api/devices/approval-key", json={"alg": "ES256", "public_key": p384}, headers=phone)).status_code == 422
        # replacing: the old key must sign the new one
        _, new_pub = new_key()
        assert (await c.post("/api/devices/approval-key", json={"alg": "ES256", "public_key": new_pub}, headers=phone)).json()["code"] == \
            "signature_required"
        stranger, _ = new_key()
        msg = approval_sign.key_message("ES256", new_pub)
        bad = await c.post("/api/devices/approval-key", json={"alg": "ES256", "public_key": new_pub, "sig": sign(stranger, msg)}, headers=phone)
        assert bad.status_code == 403 and bad.json()["code"] == "bad_signature"
        good = await c.post("/api/devices/approval-key", json={"alg": "ES256", "public_key": new_pub, "sig": sign(old_key, msg)}, headers=phone)
        assert good.status_code == 200


@pytest.mark.parametrize("split", [False, True], ids=["chat-switch-off", "checkout-job"])
async def test_a_payment_needs_the_phones_signature_and_a_refusal_keeps_the_card_open(tmp_path, split):
    fn = pay_model(split, "confirm_purchase")
    async with Running(create_app(app_settings(tmp_path, payment_specialist=split), tools=[payment_tool()], stream_fn=fn, with_worker_tools=split, browser=shop_browser(split))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        phone, _ = await pair(c)
        key, pub = new_key()
        assert (await c.post("/api/devices/approval-key", json={"alg": "ES256", "public_key": pub}, headers=phone)).status_code == 200
        tid = await start_turn(c)
        card = await pending_one(c, phone)
        aid, issued = card["approval_id"], card["issued_at"]
        assert card["name"] == "confirm_purchase" and isinstance(issued, int)
        url = f"/api/approvals/{aid}"
        r = await c.post(url, json={"approve": True}, headers=phone)
        assert r.status_code == 403 and r.json()["code"] == "signature_required"
        assert (await pending_one(c, phone))["approval_id"] == aid                               # still open
        other, _ = new_key()
        assert (await c.post(url, json={"approve": True, "sig": sign(other, approval_sign.approval_message(aid, True, issued))},
                             headers=phone)).json()["code"] == "bad_signature"
        assert (await c.post(url, json={"approve": True, "sig": sign(key, approval_sign.approval_message(aid, False, issued))},
                             headers=phone)).json()["code"] == "bad_signature"                    # a signature for "deny" is no approval
        assert (await c.post(url, json={"approve": True, "sig": sign(key, approval_sign.approval_message(aid, True, issued + 1))},
                             headers=phone)).json()["code"] == "bad_signature"
        assert (await pending_one(c, phone))["approval_id"] == aid
        ok = await c.post(url, json={"approve": True, "sig": sign(key, approval_sign.approval_message(aid, True, issued))}, headers=phone)
        assert ok.status_code == 200 and ok.json()["decision"] == "allowed_once"
        assert (await c.post(url, json={"approve": True, "sig": sign(key, approval_sign.approval_message(aid, True, issued))},
                             headers=phone)).status_code == 404                                    # answered once
        await follow(c, tid)


@pytest.mark.parametrize("split", [False, True], ids=["chat-switch-off", "checkout-job"])
async def test_denials_and_other_approvals_are_not_signed(tmp_path, split):
    fn = pay_model(split, "confirm_purchase", "send")
    async with Running(create_app(app_settings(tmp_path, payment_specialist=split), tools=[payment_tool(), plain_tool()], stream_fn=fn,
                                             with_worker_tools=split, browser=shop_browser(split))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        phone, _ = await pair(c)
        key, pub = new_key()
        await c.post("/api/devices/approval-key", json={"alg": "ES256", "public_key": pub}, headers=phone)
        tid = await start_turn(c, "결제해줘")
        card = await pending_one(c, phone)
        r = await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False}, headers=phone)
        assert r.status_code == 200 and r.json()["decision"] == "denied"
        await follow(c, tid)
        await settle(c)                                  # one checkout job per shop: the next payment waits for this job to end
        tid = await start_turn(c, "보내줘")
        card = await pending_one(c, phone)
        assert card["name"] == "send"
        r = await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": True}, headers=phone)
        assert r.status_code == 200 and r.json()["decision"] == "allowed_once"
        await follow(c, tid)


@pytest.mark.parametrize("split", [False, True], ids=["chat-switch-off", "checkout-job"])
async def test_a_phone_without_a_key_still_pays_until_the_owner_requires_signatures(tmp_path, split):
    fn = pay_model(split, "confirm_purchase", "confirm_purchase", "confirm_purchase")
    async with Running(create_app(app_settings(tmp_path, payment_specialist=split), tools=[payment_tool()], stream_fn=fn, with_worker_tools=split, browser=shop_browser(split))) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        phone, _ = await pair(c)
        assert (await c.get("/api/profile", headers=AUTH)).json()["require_signed_payments"] is False
        tid = await start_turn(c)
        card = await pending_one(c, phone)
        assert (await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": True}, headers=phone)).status_code == 200   # as before
        await follow(c, tid)
        await settle(c)

        put = await c.put("/api/profile", json={"require_signed_payments": True}, headers=AUTH)
        assert put.status_code == 200 and put.json()["require_signed_payments"] is True
        assert (await c.get("/api/profile", headers=AUTH)).json()["require_signed_payments"] is True
        tid = await start_turn(c)
        card = await pending_one(c, phone)
        r = await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": True}, headers=phone)
        assert r.status_code == 403 and r.json()["code"] == "signature_required"
        assert r.json()["detail"]["message"] == \
            "Approve payments in the Kelo phone app (you may need to update the app)."
        assert (await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False}, headers=phone)).status_code == 200
        await follow(c, tid)
        await settle(c)
        assert (await c.put("/api/profile", json={"require_signed_payments": False}, headers=AUTH)).json()["require_signed_payments"] is False
