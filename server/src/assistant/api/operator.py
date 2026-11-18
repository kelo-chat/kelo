"""The operator's services and settings: <name>.kelo.chat addresses (kelo_names.py), push for every copy's phones (push.py), reports of bad AI replies (reports.py), Google sign-in beta requests (beta.py). /api/operator/* needs a signed-in device (not on a review copy); /broker/* is public and rate-limited."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from collections import deque
from typing import Any
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from .. import i18n
from ..kelo_names import KeloNameError
from ..push import PushError
from ..reports import MAX_BODY as REPORT_MAX_BODY
from ..reports import ReportError
from ..beta import BetaError
from ..downloads import names_itself
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ..operator_site import OperatorSiteDown
from .context import AppContext

log = logging.getLogger("uvicorn.error")


class BetaAnswerIn(BaseModel):
    action: str = Field(pattern="^(approve|decline)$")
    email: bool | None = None


class OperatorCloudflareIn(BaseModel):
    api_token: str | None = Field(default=None, max_length=200)     # write-only; "" removes it
    account_id: str | None = Field(default=None, max_length=32)
    zone_id: str | None = Field(default=None, max_length=32)
    zone_name: str | None = Field(default=None, max_length=253)
    max_names: int | None = None


class ReportMarkIn(BaseModel):
    handled: bool = True


class TunnelClaimIn(BaseModel):
    name: str = Field(max_length=40)
    install_id: str = Field(max_length=128)


class TunnelReleaseIn(BaseModel):
    install_id: str = Field(max_length=128)


# Early Google sign-in by request (beta.py) is closed (owner, 2026-11-15): the apps show no "request beta" button; copies already
# approved keep their sign-in.
BETA_REQUESTS_OPEN = False

def claim_ip(request: Request) -> str:
    """The client address a claim counts against. Cloudflare's CF-Connecting-IP is believed only when the request reached this server
    from the same machine or the local network (the tunnel's connector, a reverse proxy there); a request straight from the internet
    counts by its own address, so the header cannot be made up to get past the cap. An IPv6 address counts by its /64."""
    peer = request.client.host if request.client else ""
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        addr = None
    header = (request.headers.get("cf-connecting-ip") or "").strip()
    if header and (addr is None or addr.is_loopback or addr.is_private):
        try:
            addr = ipaddress.ip_address(header)
        except ValueError:
            pass
    if addr is None:
        return peer or "?"
    return str(ipaddress.ip_network(f"{addr}/64", strict=False)) if addr.version == 6 else str(addr)


def name_refusal(e: KeloNameError) -> HTTPException:
    """A broker refusal; one with a code (daily_cap, busy) answers {"code", "message"} so the CLI can say what to do."""
    if e.code:
        return HTTPException(e.status, {"code": e.code, "message": str(e), "retry_after": e.retry_after},
                             headers={"Retry-After": str(max(1, e.retry_after or 60))})
    return HTTPException(e.status, str(e))


def tester_view(r: dict[str, Any]) -> dict[str, Any]:
    """A kelo.chat/tester sign-up as a row of the operator's beta list (negative id)."""
    return {"id": -int(r["id"]), "kind": "play_tester", "created_at": r["created_at"], "email": r["email"], "products": ["play_test"],
            "language": r["lang"], "app_version": r["model"], "status": "approved" if r["added"] else "requested", "handled_at": None,
            "notice": "sent" if r["mailed"] else "failed"}


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    apns = ctx.apns
    beta = ctx.beta
    beta_broker = ctx.beta_broker
    beta_desk = ctx.beta_desk
    tester_desk = ctx.tester_desk
    client_key = ctx.client_key
    fcm = ctx.fcm
    google_web = ctx.google_web
    kelo_names = ctx.kelo_names
    push_broker = ctx.push_broker
    report_box = ctx.report_box
    report_broker = ctx.report_broker
    reports = ctx.reports
    auth = ctx.auth
    require_auth = ctx.require_auth
    require_owner = ctx.require_owner
    require_operator = ctx.auth.require_operator   # the operator's lists: also the operator link key of a main copy (api/auth.py)
    operator_site = ctx.operator_site
    operator_events = ctx.operator_events
    settings = ctx.settings
    store = ctx.store

    async def from_site(method: str, path: str, *, query: dict[str, str] | None = None, body: Any = None) -> JSONResponse:
        """A linked main copy answers the operator's lists from the site copy: its status and JSON as they are. A refusal of the key or
        an unreachable site is a 502 with a code the apps show as their message."""
        try:
            status, data = await operator_site.call(method, path, query=query, body=body)
        except OperatorSiteDown as e:
            if e.code == "operator_site_key":
                return JSONResponse({"code": "operator_site_key", "message": i18n.t(
                    "The site server refused this server's operator key. Run `assistant operator link` again.")}, status_code=502)
            return JSONResponse({"code": "operator_site_down", "message": i18n.t(
                "Could not reach the site server. Try again in a moment.")}, status_code=502)
        return JSONResponse(status_code=status, content=data)

    # ---- Kelo addresses. The operator's Cloudflare token goes in (any signed-in device), never out.
    @router.get("/api/operator/cloudflare", dependencies=[Depends(require_operator)])
    async def get_operator_cloudflare() -> Any:
        if operator_site.linked():
            return await from_site("GET", "/api/operator/cloudflare")
        return kelo_names.config()

    @router.put("/api/operator/cloudflare", dependencies=[Depends(require_operator)])
    async def put_operator_cloudflare(body: OperatorCloudflareIn) -> Any:
        if operator_site.linked():
            return await from_site("PUT", "/api/operator/cloudflare", body=body.model_dump(exclude_none=True))
        try:
            return await kelo_names.configure(api_token=body.api_token, account_id=body.account_id, zone_id=body.zone_id,
                                              zone_name=body.zone_name, max_names=body.max_names)
        except KeloNameError as e:
            raise HTTPException(422, str(e)) from None

    @router.post("/api/operator/cloudflare/reclaim", dependencies=[Depends(require_operator)])
    async def reclaim_kelo_names() -> Any:
        if operator_site.linked():
            return await from_site("POST", "/api/operator/cloudflare/reclaim")
        try:
            return {"released": await kelo_names.reclaim_idle()}
        except KeloNameError as e:
            raise HTTPException(e.status, str(e)) from None

    tunnel_hits: dict[str, deque[float]] = {}

    def tunnel_gate(request: Request, kind: str, limit: int, window: float) -> None:
        who = f"{kind}:{client_key(request)}"
        now = time.monotonic()
        hits = tunnel_hits.setdefault(who, deque())
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(429, "too many requests from one place; try again later", headers={"Retry-After": str(int(window))})
        hits.append(now)
        if len(tunnel_hits) > 5000:
            for k in [k for k, v in tunnel_hits.items() if not v or now - v[-1] > 3600]:
                tunnel_hits.pop(k, None)

    reclaim_task: set[asyncio.Task[Any]] = set()

    def maybe_reclaim() -> None:
        """Idle names are released in the background, at most every 6 hours, when someone asks for a name."""
        if time.time() - kelo_names.last_reclaim < 6 * 3600 or reclaim_task:
            return
        kelo_names.last_reclaim = time.time()

        async def run() -> None:
            try:
                await kelo_names.reclaim_idle()
            except Exception:  # noqa: BLE001 - a failed sweep is retried next time
                log.warning("kelo: idle reclaim failed", exc_info=True)

        t = asyncio.create_task(run())
        reclaim_task.add(t)
        t.add_done_callback(reclaim_task.discard)

    @router.get("/broker/tunnel/check", include_in_schema=False)
    async def broker_tunnel_check(request: Request, name: str = "") -> dict[str, Any]:
        tunnel_gate(request, "check", 60, 60)
        try:
            return kelo_names.check(name[:40])
        except KeloNameError as e:
            raise HTTPException(e.status, str(e)) from None

    @router.post("/broker/tunnel/claim", include_in_schema=False)
    async def broker_tunnel_claim(body: TunnelClaimIn, request: Request) -> dict[str, Any]:
        if not kelo_names.enabled():
            raise HTTPException(404, "this server does not hand out Kelo addresses")
        tunnel_gate(request, "claim", 10, 600)
        try:
            out = await kelo_names.claim(body.name, body.install_id, claim_ip(request))
        except KeloNameError as e:
            raise name_refusal(e) from None
        maybe_reclaim()
        return out

    @router.post("/broker/tunnel/release", include_in_schema=False)
    async def broker_tunnel_release(body: TunnelReleaseIn, request: Request) -> dict[str, Any]:
        if not kelo_names.enabled():
            raise HTTPException(404, "this server does not hand out Kelo addresses")
        tunnel_gate(request, "claim", 10, 600)
        try:
            return {"released": await kelo_names.release(body.install_id)}
        except KeloNameError as e:
            raise HTTPException(e.status, str(e)) from None

    # ---- push notifications for every copy's phones. The operator's Firebase key goes in (any signed-in device), never out.
    @router.get("/api/operator/firebase", dependencies=[Depends(require_owner)])
    async def get_operator_firebase() -> dict[str, Any]:
        return fcm.config()

    @router.put("/api/operator/firebase", dependencies=[Depends(require_owner)])
    async def put_operator_firebase(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > 20_000:
            raise HTTPException(413, "a service-account key file is a few KB; this is not one")
        try:
            out = await fcm.configure(raw)
        except PushError as e:
            raise HTTPException(e.status, str(e)) from None
        log.info("push: Firebase service account set for project %s", out["project_id"])
        return out

    @router.delete("/api/operator/firebase", dependencies=[Depends(require_owner)])
    async def delete_operator_firebase() -> dict[str, Any]:
        return fcm.disable()

    def is_operator() -> bool:
        """The operator's server: the one other copies lean on (sign-in relay, pushes, addresses, reports, beta requests).
        ASSISTANT_OPERATOR decides when set; otherwise it is the server whose own address is the broker in its settings, or one
        already running the address or push service. Every self-hosted copy (its phones included) is not."""
        if settings.review_mode:   # a review copy never shows the operator's settings
            return False
        if operator_site.linked():   # a main copy that reads a site copy's lists is that copy's operator
            return True
        if settings.operator is not None:
            return settings.operator
        if kelo_names.enabled() or fcm.enabled() or apns.enabled():
            return True
        return names_itself(settings.public_url, settings.oauth_broker, settings.push_broker, settings.report_broker)

    @router.get("/api/operator/status", dependencies=[Depends(require_operator)])
    async def operator_status(request: Request) -> dict[str, bool]:
        # "owner" is kept for older apps: true for the access key and every device (a review copy's devices never get here: 403)
        return {"owner": request.state.device_id is None or auth.owner_flag(settings.review_mode), "operator": is_operator()}

    def push_refusal(e: PushError) -> HTTPException:
        return HTTPException(e.status, str(e), headers={"Retry-After": str(e.retry_after)} if e.retry_after else None)

    @router.get("/broker/push/status", include_in_schema=False)
    async def broker_push_status(request: Request) -> dict[str, bool]:
        tunnel_gate(request, "push-status", 60, 60)
        return {"push": push_broker.enabled(), "apns": apns.enabled()}

    @router.post("/broker/push/send", include_in_schema=False)
    async def broker_push_send(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > 64_000:
            raise HTTPException(413, "too large")
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        try:
            return await push_broker.handle(body, client_key(request))
        except PushError as e:
            raise push_refusal(e) from None

    @router.post("/broker/push/live", include_in_schema=False)
    async def broker_push_live(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > 64_000:
            raise HTTPException(413, "too large")
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        try:
            return await push_broker.handle_live(body, client_key(request))
        except PushError as e:
            raise push_refusal(e) from None

    # ---- reports of a bad AI reply (Google Play generative-AI policy); see reports.py for who keeps what
    def report_refusal(e: ReportError) -> HTTPException:
        return HTTPException(e.status, str(e), headers={"Retry-After": str(e.retry_after)} if e.retry_after else None)

    async def json_body(request: Request, limit: int) -> Any:
        raw = await request.body()
        if len(raw) > limit:
            raise HTTPException(413, i18n.t("The report is too long. Shorten the comment and try again."))
        try:
            return json.loads(raw)
        except ValueError:
            return None

    @router.post("/api/reports", dependencies=[Depends(require_auth)])
    async def post_report(request: Request) -> dict[str, Any]:
        """{reason, reply, comment?, asked?, app_version?} from the app; the model this server is set to use is added here."""
        body = await json_body(request, REPORT_MAX_BODY)
        if not isinstance(body, dict):
            raise HTTPException(422, "send {reason, reply, comment?, asked?, app_version?}")
        prof = store.profile()
        body = {**body, "provider": prof["provider"] or settings.provider, "model": prof["model"] or settings.model}
        try:
            return await reports.send(body)
        except ReportError as e:
            raise report_refusal(e) from None

    @router.post("/broker/reports", include_in_schema=False)
    async def broker_reports(request: Request) -> dict[str, Any]:
        body = await json_body(request, REPORT_MAX_BODY + 1000)
        try:
            return report_broker.handle(body, client_key(request))
        except ReportError as e:
            raise report_refusal(e) from None

    @router.get("/api/operator/reports", dependencies=[Depends(require_operator)])
    async def get_operator_reports(handled: bool | None = None, limit: int = 100) -> Any:
        if operator_site.linked():
            if not 1 <= limit <= 500:
                raise HTTPException(422, "limit must be between 1 and 500")
            query = {"limit": str(limit), **({} if handled is None else {"handled": "true" if handled else "false"})}
            return await from_site("GET", "/api/operator/reports", query=query)
        return {**report_box.list(limit=limit, handled=handled), "receiving": reports.is_operator()}

    @router.post("/api/operator/reports/{report_id}", dependencies=[Depends(require_operator)])
    async def mark_operator_report(report_id: int, body: ReportMarkIn) -> Any:
        if operator_site.linked():
            return await from_site("POST", f"/api/operator/reports/{report_id}", body={"handled": body.handled})
        got = report_box.mark(report_id, body.handled)
        if got is None:
            raise HTTPException(404, "no such report")
        return got

    @router.delete("/api/operator/reports/{report_id}", dependencies=[Depends(require_operator)])
    async def delete_operator_report(report_id: int) -> Any:
        if operator_site.linked():
            return await from_site("DELETE", f"/api/operator/reports/{report_id}")
        if not report_box.delete(report_id):
            raise HTTPException(404, "no such report")
        return {"deleted": True}

    # ---- asking to try Google sign-in while it is closed (Google's Testing mode); see beta.py for who keeps what and when mail goes out
    def beta_refusal(e: BetaError) -> HTTPException:
        return HTTPException(e.status, str(e), headers={"Retry-After": str(e.retry_after)} if e.retry_after else None)

    async def beta_body(request: Request) -> Any:
        raw = await request.body()
        if len(raw) > 4000:
            raise HTTPException(413, "too long")
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def beta_view() -> dict[str, Any]:
        s = beta.state()
        return {"status": s["status"], "email": s["email"], "products": s["products"], "at": s["at"],
                "open": google_web.open_here(), "can_request": BETA_REQUESTS_OPEN and bool(settings.oauth_broker or google_web.is_broker())}

    @router.get("/api/google/beta", dependencies=[Depends(require_auth)])
    async def get_google_beta() -> dict[str, Any]:
        """This copy's request: "none" | "requested" | "approved" | "declined" (asks the broker at most every few minutes)."""
        await google_web.client_id()
        await beta.refresh()
        return beta_view()

    @router.post("/api/google/beta", dependencies=[Depends(require_auth)])
    async def post_google_beta(request: Request) -> dict[str, Any]:
        """{email, products: ["gmail"|"calendar"], app_version?} from the app; the language is the app's (Accept-Language)."""
        if not BETA_REQUESTS_OPEN:
            raise HTTPException(410, "beta requests are closed")
        body = await beta_body(request)
        try:
            await beta.send(body, language=i18n.current())
        except BetaError as e:
            raise beta_refusal(e) from None
        return beta_view()

    @router.post("/broker/beta", include_in_schema=False)
    async def broker_beta(request: Request) -> dict[str, Any]:
        if not google_web.is_broker():
            raise HTTPException(404, "this server is not a Google sign-in broker")
        if not BETA_REQUESTS_OPEN:
            raise HTTPException(410, "beta requests are closed")
        try:
            return beta_broker.handle(await beta_body(request), client_key(request))
        except BetaError as e:
            raise beta_refusal(e) from None

    @router.post("/broker/beta/status", include_in_schema=False)
    async def broker_beta_status(request: Request) -> dict[str, Any]:
        if not google_web.is_broker():
            raise HTTPException(404, "this server is not a Google sign-in broker")
        try:
            return beta_broker.status(await beta_body(request), client_key(request), google_web.open_to_all())
        except BetaError as e:
            raise beta_refusal(e) from None

    @router.get("/api/operator/beta", dependencies=[Depends(require_operator)])
    async def get_operator_beta() -> Any:
        if operator_site.linked():
            return await from_site("GET", "/api/operator/beta")
        got = beta_desk.list()
        reqs = [{**r, "kind": "google"} for r in got["requests"]] + [tester_view(r) for r in tester_desk.rows()]
        reqs.sort(key=lambda r: (r["status"] != "requested", -r["created_at"]))
        open_n = int(got["open"]) + sum(1 for r in reqs if r["kind"] == "play_tester" and r["status"] == "requested")
        return {**got, "requests": reqs, "open": open_n, "receiving": google_web.is_broker(), "open_to_all": google_web.open_to_all()}

    @router.post("/api/operator/beta/{request_id}", dependencies=[Depends(require_operator)])
    async def answer_operator_beta(request_id: int, body: BetaAnswerIn) -> Any:
        """Approved (the operator added the address as a test user in Google Cloud Console) or Decline. Mails the requester once."""
        if operator_site.linked():
            return await from_site("POST", f"/api/operator/beta/{request_id}", body=body.model_dump(exclude_none=True))
        if request_id < 0:
            r = tester_desk.row(-request_id)
            if r is None:
                raise HTTPException(404, "no such request")
            if body.action == "approve":
                r = tester_desk.mark_added(-request_id) or r
                return {"request": tester_view(r), "mailed": False, "mail_error": ""}
            tester_desk.delete(-request_id)
            return {"request": {**tester_view(r), "status": "declined"}, "mailed": False, "mail_error": ""}
        try:
            if body.action == "approve":
                return await beta_desk.approve(request_id, send_mail=body.email is not False)
            return await beta_desk.decline(request_id, send_mail=body.email is True)
        except BetaError as e:
            raise beta_refusal(e) from None

    @router.delete("/api/operator/beta/{request_id}", dependencies=[Depends(require_operator)])
    async def delete_operator_beta(request_id: int) -> Any:
        if operator_site.linked():
            return await from_site("DELETE", f"/api/operator/beta/{request_id}")
        if not (tester_desk.delete(-request_id) if request_id < 0 else beta_desk.delete(request_id)):
            raise HTTPException(404, "no such request")
        return {"deleted": True}

    @router.get("/api/operator/events", dependencies=[Depends(require_operator)])
    async def get_operator_events(after: int = 0, limit: int = 100) -> dict[str, Any]:
        """The events a site copy keeps for its operator's main copy (operator_events.py): those with an id above `after`."""
        if after < 0 or not 1 <= limit <= 500:
            raise HTTPException(422, "after must be 0 or more and limit between 1 and 500")
        return operator_events.since(after, limit=limit)

    return router
