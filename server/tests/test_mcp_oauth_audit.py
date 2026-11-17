"""What the 2026-11-09 audit of every hosted MCP connector changed (mcp_oauth.py, connect_ways.py, catalog.HIDDEN, presets.KEYS):
the exact sign-in address, the fallback from a refused metadata document to registration, scopes made for another assistant, addresses
made for another assistant's marketplace, and services that refuse this app. Against pretend services only."""

import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from assistant.catalog import HIDDEN, SIGN_IN_CAUTION
from assistant.connect_ways import ways
from assistant.connectors import connector_rows
from assistant.mail import Mail
from assistant.mcp_oauth import (CIMD_REFUSED, REFUSAL_DAYS, SERVICES, McpConnections, McpOAuthError, refused, relay_target,
                                 remember_refusal)
from assistant.tools.oauth_tools import oauth_tools
from assistant.netguard import NetGuardError, Reply
from assistant.presets import KEYS
from assistant.store import Store

from test_mcp_oauth import DOC, MCP, RELAY, FakeCimdService

AUTHORIZE = "https://auth.notion.example/authorize"
ORIGIN = "https://friend.trycloudflare.com"


class Refusing(FakeCimdService):
    """A server that says it reads metadata documents. `at_once`: its sign-in page answers this app's document with HTTP 400 (as
    Keycloak's "Client not found" does); otherwise it redirects to its login, like a server that checks the client only after it."""

    def __init__(self, *, at_once=True, prm_scopes=None, www_scope=True, **kw):
        super().__init__(**kw)
        self.at_once, self.prm_scopes, self.www_scope = at_once, prm_scopes, www_scope
        self.preflights: list[str] = []

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None):
        if method == "GET" and url.startswith(AUTHORIZE + "?"):
            assert urlsplit(url).hostname in allowed_hosts
            self.preflights.append(url)
            client = parse_qs(urlsplit(url).query)["client_id"][0]
            if client == DOC and self.at_once:
                return Reply(400, {"content-type": "text/html"}, b"<p>We are sorry... Client not found.</p>", False)
            raise NetGuardError("the server redirected (HTTP 302); redirects are not followed. Check the endpoint.")
        r = await super().fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content)
        if url == MCP and not self.www_scope and "www-authenticate" in r.headers:
            return Reply(r.status, {**r.headers, "www-authenticate": r.headers["www-authenticate"].split(", scope=")[0]}, r.body, False)
        if url.endswith("/.well-known/oauth-protected-resource/mcp") and self.prm_scopes is not None:
            return Reply(200, r.headers, json.dumps({**json.loads(r.body), "scopes_supported": self.prm_scopes}).encode(), False)
        return r


def q(url: str) -> dict[str, str]:
    parts = parse_qs(urlsplit(url).query)
    assert all(len(v) == 1 for v in parts.values())
    return {k: v[0] for k, v in parts.items()}


async def test_the_sign_in_address_has_exactly_the_fields_the_spec_asks_for():
    fake = Refusing(at_once=False, cimd=True, dcr=False)
    conn = McpConnections(Store(":memory:"), fetcher=fake.fetch, relay=RELAY)
    url = await conn.start("notion", ORIGIN)
    assert url.startswith(AUTHORIZE + "?")
    got = q(url)
    assert set(got) == {"response_type", "client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "resource", "scope"}
    p = conn.pending[got["state"]]
    assert got["response_type"] == "code" and got["client_id"] == DOC and got["redirect_uri"] == RELAY == p["redirect"]
    assert got["resource"] == MCP == p["resource"]                     # RFC 8707: the protected resource's own name
    assert got["code_challenge_method"] == "S256"
    assert got["code_challenge"] == base64.urlsafe_b64encode(hashlib.sha256(p["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert relay_target(got["state"]) == ORIGIN                        # the relay page knows where to send the user back
    assert got["scope"] == "read write" and p["registration"] == "cimd" and fake.registered == []
    assert len(fake.preflights) == 1 and q(fake.preflights[0]) == got  # the address checked first is the one the user gets


async def test_a_document_refused_at_once_falls_back_to_registration():
    fake = Refusing(at_once=True, cimd=True, dcr=True)
    conn = McpConnections(Store(":memory:"), fetcher=fake.fetch, relay=RELAY)
    got = q(await conn.start("notion", ORIGIN))
    assert got["client_id"] == "client-123" and got["redirect_uri"] == RELAY
    assert len(fake.registered) == 1 and fake.registered[0]["redirect_uris"] == [RELAY] and fake.registered[0]["client_name"] == "Kelo"
    assert conn.pending[got["state"]]["registration"] == "dcr"


async def test_a_document_refused_at_once_without_registration_is_explained_instead_of_an_error_page():
    conn = McpConnections(Store(":memory:"), fetcher=Refusing(at_once=True, cimd=True, dcr=False).fetch, relay=RELAY)
    with pytest.raises(McpOAuthError, match="approved in advance"):
        await conn.start("notion", ORIGIN)
    assert conn.pending == {}


async def test_a_refusal_after_the_login_is_remembered_and_the_next_try_registers():
    st = Store(":memory:")
    fake = Refusing(at_once=False, cimd=True, dcr=True)
    conn = McpConnections(st, fetcher=fake.fetch, relay=RELAY)
    first = q(await conn.start("notion", ORIGIN))
    assert first["client_id"] == DOC and fake.registered == []
    svc, origin, problem = await conn.finish(first["state"], error="invalid_client")
    assert svc.id == "notion" and origin == ORIGIN and "another way" in problem and st.meta(CIMD_REFUSED + "notion") == "1"
    again = q(await conn.start("notion", ORIGIN))
    assert again["client_id"] == "client-123" and len(fake.registered) == 1
    # a cancel is not a refusal of the client
    st2 = Store(":memory:")
    conn2 = McpConnections(st2, fetcher=Refusing(at_once=False, cimd=True, dcr=True).fetch, relay=RELAY)
    s2 = q(await conn2.start("notion", ORIGIN))["state"]
    assert (await conn2.finish(s2, error="access_denied"))[2] == "access_denied" and st2.meta(CIMD_REFUSED + "notion") == ""


async def test_without_registration_a_later_refusal_is_remembered_and_the_next_try_is_told_at_once():
    st = Store(":memory:")
    fake = Refusing(at_once=False, cimd=True, dcr=False)
    conn = McpConnections(st, fetcher=fake.fetch, relay=RELAY)
    s = q(await conn.start("notion", ORIGIN))["state"]
    _, _, problem = await conn.finish(s, error="invalid_client")
    assert "approved in advance" in problem and refused(st, "notion") and st.meta(CIMD_REFUSED + "notion").startswith("refused ")
    calls = len(fake.preflights)
    with pytest.raises(McpOAuthError, match="approved in advance"):   # no second trip to a login that ends in the same error
        await conn.start("notion", ORIGIN)
    assert len(fake.preflights) == calls and conn.pending == {}
    # a cancel or a bad request is not a refusal of this app
    for err in ("access_denied", "invalid_request"):
        st2 = Store(":memory:")
        conn2 = McpConnections(st2, fetcher=Refusing(at_once=False, cimd=True, dcr=False).fetch, relay=RELAY)
        s2 = q(await conn2.start("notion", ORIGIN))["state"]
        await conn2.finish(s2, error=err)
        assert not refused(st2, "notion") and st2.meta(CIMD_REFUSED + "notion") == ""
    # tried again after REFUSAL_DAYS: the service may have approved the app since
    st.set_meta(CIMD_REFUSED + "notion", f"refused {time.time() - REFUSAL_DAYS * 86400 - 60:.0f}")
    assert not refused(st, "notion") and q(await conn.start("notion", ORIGIN))["client_id"] == DOC


async def test_a_registered_client_refused_after_the_login_or_at_registration_is_remembered():
    st = Store(":memory:")
    conn = McpConnections(st, fetcher=Refusing(at_once=False, cimd=False, dcr=True).fetch, relay=RELAY)
    s = q(await conn.start("notion", ORIGIN))["state"]
    _, _, problem = await conn.finish(s, error="unauthorized_client")
    assert "approved in advance" in problem and refused(st, "notion")

    class NoRegistration(Refusing):
        async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None):
            if method == "POST" and url.endswith("/register"):
                return Reply(403, {"content-type": "application/json"}, b'{"error": "access_denied"}', False)
            return await super().fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content)

    st2 = Store(":memory:")
    with pytest.raises(McpOAuthError, match="approved in advance"):
        await McpConnections(st2, fetcher=NoRegistration(at_once=False, cimd=False, dcr=True).fetch, relay=RELAY).start("notion", ORIGIN)
    assert refused(st2, "notion")


def _connect_key(st, item):
    st.save_credential(item, item, ["api.example.com"], {"type": "bearer_header"}, "", kind="api")
    st.set_credential_secret(item, "x")


def test_a_sign_in_that_may_refuse_this_app_says_so_first_and_offers_the_other_way():
    st = Store(":memory:")
    table = ways(st, Mail(st), browser=True, relay=RELAY)
    for cid, c in SIGN_IN_CAUTION.items():
        assert cid in SERVICES and table[cid]["connect"] == "mcp" and table[cid]["note"], cid
        assert c.kind in ("approved", "admin") and (not c.instead or c.instead in KEYS), cid
        assert c.instead not in table   # an other way only: no row (nor connector card) of its own
        if c.kind == "admin":
            assert RELAY in table[cid]["note"]          # the exact address the organisation's admin has to allow
        if c.instead:
            fb = table[cid]["fallback"]
            assert fb["connect"] == "key" and fb["via"] == c.instead and fb["item"] == KEYS[c.instead].item and fb["title"] and not fb["connected"]
        else:
            assert "fallback" not in table[cid]
    assert "admin" in ways(st, Mail(st), browser=True)["egnyte"]["note"]   # without a relay: no address to quote
    # the Connectors screen carries both, and the card the assistant puts in the chat tells it too
    rows = {r["id"]: r for r in connector_rows(table)}
    assert rows["stripe"]["note"] == table["stripe"]["note"] and rows["stripe"]["fallback"]["via"] == "stripe_key"
    assert "stripe_key" not in rows and "clickup_key" not in rows
    out = asyncio.run(oauth_tools(lambda: table)[0].handler({"service": "stripe"}))
    assert table["stripe"]["note"] in out and "stripe-api" in out


def test_after_a_refusal_the_row_is_the_other_way_or_a_reason():
    st = Store(":memory:")
    remember_refusal(st, "stripe")
    remember_refusal(st, "mentimeter")
    table = ways(st, Mail(st), browser=True)
    assert table["stripe"]["connect"] == "key" and table["stripe"]["via"] == "stripe_key" and table["stripe"]["item"] == "stripe-api"
    assert "turned this app's sign-in down" in table["stripe"]["note"]
    assert table["mentimeter"]["connect"] is None and "approved in advance" in table["mentimeter"]["reason"]
    assert "mentimeter" not in {r["id"] for r in connector_rows(table)}
    # the other way connected: the service's row is that connection, refused or not
    st2 = Store(":memory:")
    _connect_key(st2, "clickup-api")
    row = ways(st2, Mail(st2), browser=True)["clickup"]
    assert row["connect"] == "key" and row["connected"] and row["item"] == "clickup-api" and "note" not in row
    # one already connected by its sign-in stays as it is
    st3 = Store(":memory:")
    remember_refusal(st3, "stripe")
    _connect_key(st3, "stripe-mcp")
    assert ways(st3, Mail(st3), browser=True)["stripe"]["connect"] == "mcp"


def test_vendors_that_take_only_listed_apps_are_keys_on_the_same_mcp_address():
    st = Store(":memory:")
    table = ways(st, Mail(st), browser=True)
    for cid, endpoint in {"monday": "https://mcp.monday.com/mcp", "zapier": "https://mcp.zapier.com/api/v1/connect",
                          "incident_io": "https://mcp.incident.io/mcp", "datadog": "https://mcp.datadoghq.com/v1/mcp?toolsets=all,visualizations"}.items():
        assert cid not in SERVICES and table[cid]["connect"] == "key" and KEYS[cid].endpoint == endpoint, cid
        assert urlsplit(endpoint).hostname in KEYS[cid].hosts and KEYS[cid].placement == {"type": "bearer_header"}
    for cid in ("ramp", "greenhouse"):
        assert table[cid]["connect"] is None and cid in HIDDEN
    assert SERVICES["ibkr"].url == "https://api.ibkr.com/v1/api/mcp-public"


async def test_listed_scopes_go_through_the_model_pick_and_no_brand_list():
    from assistant.scope_pick import ScopePick
    listed = ["offline_access", "read", "write", "claudeai", "chatgpt.tools"]
    asked = []

    async def ask(system, user):
        asked.append(json.loads(user))
        return {"scopes": ["offline_access", "read", "write", "made_up"]}   # a name the server never listed is dropped
    fake = Refusing(at_once=False, cimd=False, dcr=True, www_scope=False, prm_scopes=listed)
    conn = McpConnections(Store(":memory:"), fetcher=fake.fetch, relay=RELAY)
    conn.picker = ScopePick(ask)
    got = q(await conn.start("notion", ORIGIN))
    assert got["scope"] == "offline_access read write" and fake.registered[0]["scope"] == "offline_access read write"
    assert asked == [{"service": "notion", "scopes": listed}]
    assert fake.preflights == []   # a registered client is this app's own: nothing to check first


async def test_no_model_or_a_bad_answer_asks_for_no_scope():
    from assistant.scope_pick import ScopePick
    listed = ["offline_access", "read", "write", "claudeai"]

    async def start(picker):
        fake = Refusing(at_once=False, cimd=False, dcr=True, www_scope=False, prm_scopes=listed)
        conn = McpConnections(Store(":memory:"), fetcher=fake.fetch, relay=RELAY)
        conn.picker = picker
        return q(await conn.start("notion", ORIGIN))
    assert "scope" not in await start(None) or (await start(None))["scope"] in ("", [""])
    for answer in (None, {"scopes": "read"}, {"scopes": [1]}, {"other": []}):
        async def ask(system, user, answer=answer):
            return answer
        assert (await start(ScopePick(ask))).get("scope", "") in ("", [""])


def test_no_connector_uses_an_address_made_for_another_assistant():
    import re
    bad = re.compile(r"claude|anthropic|marketplace|chatgpt|openai|cursor", re.I)
    assert not [s.id for s in SERVICES.values() if bad.search(s.url)]
    assert SERVICES["elevenlabs"].url == "https://api.elevenlabs.io/v1/mcp"
    assert SERVICES["descript"].url == "https://api.descript.com/v2/mcp"
    assert SERVICES["zoho_crm"].url == "https://zohocrm.zohomcp.com/mcp/message"


def test_services_that_refuse_this_app_get_another_way_or_a_reason():
    st = Store(":memory:")
    table = ways(st, Mail(st), browser=True)
    # Wise: its sign-in trusts only some client domains, so a personal token instead
    assert "wise" not in SERVICES and KEYS["wise"].hosts == ("api.wise.com",)
    assert table["wise"]["connect"] == "key" and table["wise"]["item"] == "wise-api"
    # MongoDB Atlas and the others in catalog.HIDDEN: no sign-in card, the assistant is told why
    assert "mongodb_atlas" in HIDDEN
    for cid, reason in HIDDEN.items():
        if cid in table:
            assert table[cid] == {"connect": None, "reason": reason}, cid
    # one already connected stays usable
    st.save_credential("mongodb-atlas-mcp", "MongoDB Atlas", ["mcp.mongodb.com"], {"type": "bearer_header", "via": "oauth"},
                       "https://mcp.mongodb.com/", kind="api")
    st.set_credential_secret("mongodb-atlas-mcp", "x")
    assert ways(st, Mail(st), browser=True)["mongodb_atlas"]["connect"] == "mcp"
