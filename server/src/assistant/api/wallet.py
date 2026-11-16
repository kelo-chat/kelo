"""Wallet, auto-pay cards, payment PINs and steps, shipping addresses and order address forms (WALLET API, PAYMENT PINS, SHIPPING API)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from .. import i18n
from ..wallet import WalletError
from ..payment_pins import PinError
from ..payment_steps import StepError
from ..shipping import ShippingError
from .. import address as address_data
from ..tools.browser import BrowserPolicyError
from fastapi import APIRouter
from .context import AppContext

log = logging.getLogger("uvicorn.error")


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    deliveries = ctx.deliveries
    entries = ctx.entries
    payment_pins = ctx.payment_pins
    payment_steps = ctx.payment_steps
    pin_deliveries = ctx.pin_deliveries
    pin_pads = ctx.pin_pads
    require_auth = ctx.require_auth
    schedule_grants = ctx.schedule_grants
    shipping = ctx.shipping
    shipping_forms = ctx.shipping_forms
    store = ctx.store
    wallet = ctx.wallet

    # ---- WALLET API ---------------------------------------------------------------------------------------------------------------
    # Every endpoint needs the bearer token. Bodies are read by hand (not by pydantic), so a refused request never echoes a card number.
    # Errors: {"detail": "<safe English sentence>"} with 400 (not JSON), 404 (no such card / slot), 409 (already sent), 413, 422 (invalid).
    #
    # Card (as every answer shows it; never a number or CVC):
    #   {"id": "card_…", "label" (the owner's nickname for it; the card's title everywhere), "brand": visa|mastercard|amex|jcb|discover|unionpay|diners|other, "last4", "exp_month", "exp_year",
    #    "holder", "postal_code", "created_at", "auto_pay": bool, "server_copy": bool (the server can read it: only with auto_pay),
    #    "e2e": bool (the phone's ciphertext is stored), "e2e_alg", "e2e_key_id", "usable": bool, "expired": bool, "last_used": ts|null,
    #    "pays_automatically": bool (auto-pay on, a server copy, Keyguard on, not paused, vault not locked), "ask_reason": "" |
    #    not_auto | no_server_copy | keyguard_off | paused | vault_locked (why it asks each time), "ask_reason_text": that reason in
    #    the owner's language, "auto_pay_paused_reason": "" | "keyguard_off" (an auto-pay card waits for Keyguard: its sealed copy is
    #    kept and it pays automatically again once Keyguard is on),
    #    "default": bool (the card the agent pays with unless the owner or the task names another; the first card saved is it)}
    #   Even a card that pays automatically asks for the first payment on a new site (first_site) and over a limit.
    # GET    /api/wallet                     {"paused", "cards": [Card], "timezone", "month": "YYYY-MM", "auto_cards": n (cards that pay
    #                                         automatically now), "auto_pay_paused": n (auto-pay cards that wait for Keyguard),
    #                                         "schedule_grants": [ScheduleGrant of kind purchase] (see SCHEDULES)}
    # GET    /api/wallet/cards               {"paused", "cards": [Card]}
    # POST   /api/wallet/cards               201 Card. Body, common: {"label"?, "exp_month", "exp_year", "holder"?, "postal_code"?, "auto_pay": bool,
    #                                         "default"?: true}
    #          auto_pay true:  + {"number", "cvc"} (Luhn and CVC checked; brand and last4 derived), optional "e2e" kept for later.
    #          AUTO-PAY NEEDS KEYGUARD (keyguard.py, owner's rule): auto_pay true here, or turning it on / sending the number again with
    #          PATCH, answers 409 {"code": "keyguard_required"} while Keyguard is not on (mode "phone"). Turning Keyguard off later is
    #          allowed: the cards stay, ask on the phone (ask_reason keyguard_off) and resume when it is on again.
    #          auto_pay false: + {"last4", "brand"?, "e2e": {"blob": base64 ≤ 8192, "alg"?: ≤ 40, "key_id"?: ≤ 120}}; "number"/"cvc" refused
    # GET    /api/wallet/cards/{id}          Card
    # PATCH  /api/wallet/cards/{id}          Card. Any of {"label", "holder", "postal_code", "exp_month", "exp_year", "e2e", "auto_pay", "number", "cvc",
    #                                         "default": true (makes it the default; false is refused: make another card the default)}
    #          Turning auto-pay on from the phone: the phone opens its own end-to-end copy after the owner's device check and sends
    #          {"auto_pay": true, "number", "cvc"} here once; the values are never logged.
    #          auto_pay true needs {"number", "cvc"} unless a server copy exists; the last 4 must match. auto_pay false wipes the server copy at
    #          once; the card keeps paying end-to-end with "e2e" sent in the same request or the blob kept from before; with neither it keeps
    #          only its details ("usable": false) until the phone sends a blob.
    # DELETE /api/wallet/cards/{id}?new_default=card_…   {"ok": true}   (overwritten, then deleted; the database runs with secure_delete)
    #          deleting the default card: with one card left it becomes the default; with more, 409 unless new_default names one
    # GET    /api/wallet/cards/{id}/blob     {"blob", "alg", "key_id"}   the phone's own ciphertext (404 when none)
    # GET    /api/wallet/limits              {"month", "timezone", "limits": [{"currency", "per_payment", "monthly", "default_per_payment",
    #                                         "default_monthly", "custom", "spent_this_month"}]}   (null defaults for a currency the user added)
    # PUT    /api/wallet/limits/{CUR}        {"per_payment", "monthly"} -> that row. 0 = never automatic in that currency.
    # DELETE /api/wallet/limits/{CUR}        {"ok": bool}   back to the default (a currency the user added is removed: never automatic)
    # PUT    /api/wallet/pause               {"paused": bool} -> {"paused": bool}   the global "pause auto-pay" switch
    # GET    /api/wallet/payments?limit=100&before_id=   {"items": [Payment]}   newest first
    # GET    /api/wallet/cards/{id}/payments?limit=30&before_id=   {"items": [Payment made with that card], "next_before_id": id|null}
    #          newest first; pass next_before_id as before_id for the older page (null: no more)
    # GET    /api/wallet/notifications?after=0           {"items": [Payment of kind auto], "unseen": n}
    # POST   /api/wallet/notifications/seen              {"up_to": id} -> {"ok": true, "marked": n}
    # GET    /api/wallet/deliveries          {"items": [{"approval_id", "card": {"id","label","brand","last4"}, "site", "summary", "amount",
    #                                         "currency", "amount_text", "expires_in"}]}   approvals waiting for an end-to-end card
    # POST   /api/wallet/deliveries/{approval_id}   {"card_id"?, "number", "cvc"} -> {"ok": true}   once per approval, memory only,
    #          about 5 minutes; Luhn, CVC and the last 4 digits are checked. 404 = nothing waits (used, denied, timed out); 409 = already sent.
    # Payment: {"id", "at", "kind": auto|approved, "site" (registrable domain), "host", "summary", "amount", "currency", "amount_text",
    #           "card_id", "card_label", "month", "seen", "grant_id", "task_id" (the activity task it was made in, or null), "source"
    #           ("chat" | "api" | "schedule:<id>" | "task:<hex>" ...: the kind of turn)}
    # ---- SHIPPING API -------------------------------------------------------------------------------------------------------------
    # Addresses of any country, built from open address metadata (address.py; no country has code of its own). Errors as the wallet's.
    # GET    /api/address/countries          {"countries": [{"code", "name" (English; apps show their own language's name)}], "default": "KR"}
    #          default: the country of the user's last address, else of their time zone, else of their language's region
    # GET    /api/address/form/{CC}          the form of one country in the request's language: {"country", "country_name", "lang",
    #          "fields": [{"key", "label", "required", "autocomplete", "options"?: [{"key", "name"}], "pattern"?, "example"?, "numeric"?}]
    #          (display order), "phone": {"label", "calling_code", "required"}, "note": {"label", "required"}, "labels": {"country",
    #          "nickname", "optional"}, "format": the postal format}. Keys: name, address_line1, address_line2, dependent_locality,
    #          locality, admin_area (an option's key when there are options), postal_code, sorting_code. 404 for an unknown code.
    # Profile: {"id": "ship_…", "label" (nickname), "country", <the keys above>, "organization", "phone" (E.164), "note", "default",
    #           "created_at", "last_used", "formatted" (the country's postal format, with the name), "one_line" (without the name)}
    # GET    /api/wallet/shipping            {"profiles": [Profile]}   (the default first; also "shipping" in GET /api/wallet)
    # POST   /api/wallet/shipping            201 Profile. {"country", <keys>, "phone", "note"?, "label"?, "default"?: true}; required fields,
    #                                        the postal-code pattern and the regions follow the COUNTRY; the first one is the default
    # GET    /api/wallet/shipping/{id}       Profile
    # PATCH  /api/wallet/shipping/{id}       Profile. Any of the above; {"default": true} makes it the default
    # DELETE /api/wallet/shipping/{id}       {"ok": true}   (deleting the default: the oldest one left becomes it)
    # GET    /api/shipping-forms             {"items": [shipping_form turn event]}   the address forms waiting now
    # POST   /api/shipping-forms/{form_id}   {"address": {"country", <keys>, "phone", "note"}, "save": bool, "label", "payment": card|site|null}
    #          -> {"ok": true, "profile": Profile|null (saved), "formatted"}; {"cancel": true} -> {"ok": true}. 404: no longer waiting.
    MAX_WALLET_BODY = 32_768

    async def wallet_body(request: Request) -> dict[str, Any]:
        if int(request.headers.get("content-length") or 0) > MAX_WALLET_BODY:
            raise HTTPException(413, "request too large")
        raw = await request.body()
        if len(raw) > MAX_WALLET_BODY:
            raise HTTPException(413, "request too large")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "the body must be a JSON object") from None
        if not isinstance(data, dict):
            raise HTTPException(400, "the body must be a JSON object")
        return data

    def wallet_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except WalletError as e:
            raise HTTPException(e.status, {"code": e.code, "message": str(e)} if e.code else str(e)) from None

    def need_card(card_id: str) -> dict[str, Any]:
        card = wallet.card(card_id)
        if card is None:
            raise HTTPException(404, "no such card")
        return card

    @router.get("/api/wallet", dependencies=[Depends(require_auth)])
    async def get_wallet() -> dict[str, Any]:
        cards = wallet.cards()
        return {"paused": wallet.paused(), "cards": cards, "timezone": wallet.timezone(), "month": wallet.month_key(),
                "auto_cards": sum(1 for c in cards if c["pays_automatically"] and c["usable"]),
                "auto_pay_paused": wallet.auto_pay_paused(),   # auto-pay cards waiting for Keyguard (0 while it is on)
                "schedule_grants": schedule_grants.list(kind="purchase"), "shipping": shipping.profiles()}

    @router.get("/api/wallet/cards", dependencies=[Depends(require_auth)])
    async def get_wallet_cards() -> dict[str, Any]:
        return {"paused": wallet.paused(), "cards": wallet.cards()}

    @router.post("/api/wallet/cards", dependencies=[Depends(require_auth)], status_code=201)
    async def add_wallet_card(request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        return wallet_call(lambda: wallet.add_card(body))

    @router.get("/api/wallet/cards/{card_id}", dependencies=[Depends(require_auth)])
    async def get_wallet_card(card_id: str) -> dict[str, Any]:
        return need_card(card_id)

    @router.patch("/api/wallet/cards/{card_id}", dependencies=[Depends(require_auth)])
    async def patch_wallet_card(card_id: str, request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        return wallet_call(lambda: wallet.update_card(card_id, body))

    @router.delete("/api/wallet/cards/{card_id}", dependencies=[Depends(require_auth)])
    async def delete_wallet_card(card_id: str, new_default: str | None = None) -> dict[str, bool]:
        if not wallet_call(lambda: wallet.delete_card(card_id, new_default)):
            raise HTTPException(404, "no such card")
        return {"ok": True}

    @router.get("/api/wallet/cards/{card_id}/payments", dependencies=[Depends(require_auth)])
    async def get_wallet_card_payments(card_id: str, limit: int = 30, before_id: int | None = None) -> dict[str, Any]:
        need_card(card_id)
        return wallet.card_payments(card_id, limit, before_id)

    @router.get("/api/wallet/cards/{card_id}/blob", dependencies=[Depends(require_auth)])
    async def get_wallet_blob(card_id: str) -> dict[str, str]:
        need_card(card_id)
        blob = wallet.blob(card_id)
        if blob is None:
            raise HTTPException(404, "this card has no end-to-end copy")
        return blob

    @router.get("/api/wallet/limits", dependencies=[Depends(require_auth)])
    async def get_wallet_limits() -> dict[str, Any]:
        return {"month": wallet.month_key(), "timezone": wallet.timezone(), "limits": wallet.limits()}

    @router.put("/api/wallet/limits/{currency}", dependencies=[Depends(require_auth)])
    async def put_wallet_limit(currency: str, request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        return wallet_call(lambda: wallet.set_limit(currency, body.get("per_payment"), body.get("monthly")))

    @router.delete("/api/wallet/limits/{currency}", dependencies=[Depends(require_auth)])
    async def reset_wallet_limit(currency: str) -> dict[str, bool]:
        return {"ok": wallet.reset_limit(currency)}

    @router.put("/api/wallet/pause", dependencies=[Depends(require_auth)])
    async def put_wallet_pause(request: Request) -> dict[str, bool]:
        body = await wallet_body(request)
        if not isinstance(body.get("paused"), bool):
            raise HTTPException(422, "paused must be true or false")
        wallet.set_paused(body["paused"])
        return {"paused": wallet.paused()}

    @router.get("/api/wallet/payments", dependencies=[Depends(require_auth)])
    async def get_wallet_payments(limit: int = 100, before_id: int | None = None) -> dict[str, Any]:
        return {"items": wallet.payments(limit, before_id)}

    @router.get("/api/wallet/notifications", dependencies=[Depends(require_auth)])
    async def get_wallet_notifications(after: int = 0, limit: int = 50) -> dict[str, Any]:
        return wallet.notifications(after, limit)

    @router.post("/api/wallet/notifications/seen", dependencies=[Depends(require_auth)])
    async def seen_wallet_notifications(request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        up_to = body.get("up_to")
        if not isinstance(up_to, int) or isinstance(up_to, bool):
            raise HTTPException(422, "up_to must be a payment id")
        activity.mark_payment_notes_seen(up_to)   # the same payments in the app's notification feed
        return {"ok": True, "marked": wallet.mark_seen(up_to)}

    # ---- PAYMENT PINS and PAYMENT STEPS (payment_pins.py, payment_steps.py, tools/payment_step.py) ------------------------------
    # PaymentPin: {"id", "site" (registrable domain), "label", "digits", "e2e_alg", "e2e_key_id", "created_at", "updated_at",
    #              "last_used", "uses"}   never the PIN: only the phone can open its ciphertext
    # GET    /api/payment-pins                 {"items": [PaymentPin]}
    # POST   /api/payment-pins                 {"site", "label"?, "digits", "e2e": {"blob", "alg", "key_id"}, "request_id"?} -> {"pin", "replaced",
    #                                          "turn_id"}   one per site (a new one replaces it); answers the chat's fill-in card
    # PATCH  /api/payment-pins/{id}            {"label"?, "e2e"? + "digits"} -> PaymentPin       DELETE /api/payment-pins/{id} {"ok"}
    # GET    /api/payment-pins/{id}/blob       {"blob", "alg", "key_id"}
    # GET    /api/payment-pins/deliveries      {"items": [{"slot_id", "approval_id", "pin": {"id","site","label","digits"}, "site",
    #                                          "amount_text", "expires_in"}]}   payments waiting for the phone to release a PIN
    # POST   /api/payment-pins/deliveries/{slot_id}  {"pin_id"?, "pin": "<digits>"} -> {"ok": true}  once, memory only, a few minutes
    # GET    /api/payment-steps                {"items": [payment_step event]}   open cards and ones that ended in the last 15 minutes
    # POST   /api/payment-steps/{id}           {"answer": approved|cancel|new_code|code|done, "code"?} -> {"ok", "step_id", "answer"}
    # GET    /api/payment-steps/{id}/pad       image/jpeg: the protected keypad region of the page, now (kind pin_pad only)
    # POST   /api/payment-steps/{id}/pad/tap   {"x", "y"} (0..1 of that picture) -> {"taps"}   a click on the same spot of that region
    # POST   /api/payment-steps/{id}/pad/clear -> {"taps": 0}   the page's own clear key
    def pin_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except PinError as e:
            raise HTTPException(e.status, str(e)) from None

    @router.get("/api/payment-pins", dependencies=[Depends(require_auth)])
    async def get_payment_pins() -> dict[str, Any]:
        return {"items": payment_pins.list()}

    @router.post("/api/payment-pins", dependencies=[Depends(require_auth)])
    async def post_payment_pin(request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        pin, replaced = pin_call(lambda: payment_pins.save(body))
        result = {"pin": {"site": pin["site"], "label": pin["label"]}}
        rid = str(body.get("request_id") or "")
        req = entries.get(rid) if rid else None
        if req is not None and req.get("kind") == "payment_pin" and req.get("state") == "open":
            got = entries.answer_full(rid, "saved", result)
        else:
            got = entries.saved_full("payment_pin", pin["site"], result)
        # reply / request: whether a reply is still coming for the agent's request (secret_requests.REPLY_STATES)
        return {"pin": pin, "replaced": replaced, "turn_id": got["turn_id"], "reply": got["reply"], "request": got["request"]}

    @router.patch("/api/payment-pins/{pin_id}", dependencies=[Depends(require_auth)])
    async def patch_payment_pin(pin_id: str, request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        return pin_call(lambda: payment_pins.update(pin_id, body))

    @router.delete("/api/payment-pins/{pin_id}", dependencies=[Depends(require_auth)])
    async def delete_payment_pin(pin_id: str) -> dict[str, bool]:
        if not payment_pins.delete(pin_id):
            raise HTTPException(404, "no such payment PIN")
        return {"ok": True}

    @router.get("/api/payment-pins/deliveries", dependencies=[Depends(require_auth)])
    async def get_pin_deliveries() -> dict[str, Any]:
        return {"items": pin_deliveries.pending()}

    @router.get("/api/payment-pins/{pin_id}/blob", dependencies=[Depends(require_auth)])
    async def get_payment_pin_blob(pin_id: str) -> dict[str, str]:
        blob = payment_pins.blob(pin_id)
        if blob is None:
            raise HTTPException(404, "no such payment PIN")
        return blob

    @router.post("/api/payment-pins/deliveries/{slot_id}", dependencies=[Depends(require_auth)])
    async def deliver_payment_pin(slot_id: str, request: Request) -> dict[str, bool]:
        body = await wallet_body(request)
        if store.vault_locked():
            raise HTTPException(423, "the keychain is locked")
        pin = pin_call(lambda: pin_deliveries.deliver(slot_id, body))
        body.pop("pin", None)
        payment_pins.log(pin, "pin-released", "released by the phone for one approved payment")
        return {"ok": True}

    def step_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except StepError as e:
            raise HTTPException(e.status, str(e)) from None

    @router.get("/api/payment-steps", dependencies=[Depends(require_auth)])
    async def get_payment_steps() -> dict[str, Any]:
        return {"items": payment_steps.pending()}

    @router.post("/api/payment-steps/{step_id}", dependencies=[Depends(require_auth)])
    async def answer_payment_step(step_id: str, request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        return step_call(lambda: payment_steps.answer(step_id, body))

    def pad_of(step_id: str) -> Any:
        pad = pin_pads.get(step_id)
        if pad is None or not payment_steps.is_open(step_id):
            raise HTTPException(404, "no keypad is shown for this payment step")
        return pad

    @router.get("/api/payment-steps/{step_id}/pad", dependencies=[Depends(require_auth)])
    async def get_payment_pad(step_id: str) -> Response:
        pad = pad_of(step_id)
        try:
            jpeg, w, h = await pad.shot()
        except BrowserPolicyError as e:
            raise HTTPException(409, str(e)) from None
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Pad-Width": str(w), "X-Pad-Height": str(h),
                                                               "X-Pad-Taps": str(pad.taps), "X-Pad-Clear": "1" if pad.has_clear else "0"})

    @router.post("/api/payment-steps/{step_id}/pad/tap", dependencies=[Depends(require_auth)])
    async def tap_payment_pad(step_id: str, request: Request) -> dict[str, int]:
        pad = pad_of(step_id)
        body = await wallet_body(request)
        x, y = body.get("x"), body.get("y")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
            raise HTTPException(422, "x and y must be numbers between 0 and 1")
        try:   # where the user tapped is never logged: on a keypad it would tell the digit
            taps = await pad.tap(float(x), float(y), str(request.state.device_id or "owner"))
        except BrowserPolicyError as e:
            raise HTTPException(409, str(e)) from None
        payment_steps.update(step_id, taps=taps)
        return {"taps": taps}

    @router.post("/api/payment-steps/{step_id}/pad/clear", dependencies=[Depends(require_auth)])
    async def clear_payment_pad(step_id: str, request: Request) -> dict[str, int]:
        pad = pad_of(step_id)
        try:
            taps = await pad.clear(str(request.state.device_id or "owner"))
        except BrowserPolicyError as e:
            raise HTTPException(409, str(e)) from None
        payment_steps.update(step_id, taps=taps)
        return {"taps": taps}

    @router.get("/api/wallet/deliveries", dependencies=[Depends(require_auth)])
    async def get_wallet_deliveries() -> dict[str, Any]:
        return {"items": deliveries.pending()}

    @router.post("/api/wallet/deliveries/{approval_id}", dependencies=[Depends(require_auth)])
    async def deliver_wallet_card(approval_id: str, request: Request) -> dict[str, bool]:
        body = await wallet_body(request)
        wallet_call(lambda: deliveries.deliver(approval_id, body))
        return {"ok": True}

    # ---- shipping addresses and the address form of an order (SHIPPING API) ---------------------------------------------------
    def shipping_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ShippingError as e:
            raise HTTPException(e.status, str(e)) from None

    @router.get("/api/address/countries", dependencies=[Depends(require_auth)])
    async def address_countries() -> dict[str, Any]:
        return {"countries": address_data.countries(),
                "default": address_data.default_country(timezone=store.profile().get("timezone") or "", lang=i18n.current(),
                                                        previous=shipping.last_country())}

    @router.get("/api/address/form/{country}", dependencies=[Depends(require_auth)])
    async def address_form(country: str) -> dict[str, Any]:
        if not address_data.known(country):
            raise HTTPException(404, "unknown country code")
        return address_data.form(country, i18n.current())

    @router.get("/api/wallet/shipping", dependencies=[Depends(require_auth)])
    async def get_shipping() -> dict[str, Any]:
        return {"profiles": shipping.profiles()}

    @router.post("/api/wallet/shipping", dependencies=[Depends(require_auth)], status_code=201)
    async def add_shipping(request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        return shipping_call(lambda: shipping.add(body))

    @router.get("/api/wallet/shipping/{pid}", dependencies=[Depends(require_auth)])
    async def get_shipping_profile(pid: str) -> dict[str, Any]:
        p = shipping.get(pid)
        if p is None:
            raise HTTPException(404, "no such shipping profile")
        return p

    @router.patch("/api/wallet/shipping/{pid}", dependencies=[Depends(require_auth)])
    async def patch_shipping(pid: str, request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        return shipping_call(lambda: shipping.update(pid, body))

    @router.delete("/api/wallet/shipping/{pid}", dependencies=[Depends(require_auth)])
    async def delete_shipping(pid: str) -> dict[str, bool]:
        if not shipping.delete(pid):
            raise HTTPException(404, "no such shipping profile")
        return {"ok": True}

    @router.get("/api/shipping-forms", dependencies=[Depends(require_auth)])
    async def pending_shipping_forms() -> dict[str, Any]:
        return {"items": shipping_forms.pending()}

    @router.post("/api/shipping-forms/{form_id}", dependencies=[Depends(require_auth)])
    async def answer_shipping_form(form_id: str, request: Request) -> dict[str, Any]:
        body = await wallet_body(request)
        try:
            return shipping_call(lambda: shipping_forms.answer(form_id, body))
        except KeyError:
            raise HTTPException(404, "this address form is no longer waiting (it ran out or the reply ended)") from None

    return router
