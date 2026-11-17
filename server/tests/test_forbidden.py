"""Calls Kelo never makes on a connected service, or always asks about (forbidden.py): every rule, the calls next to it that must keep
working, the path normalisation, the body checks, the gate (a forced card whatever the owner allowed) and the doc table. No network."""

import json

import pytest

from assistant import effects, forbidden
from assistant.forbidden import RULES, Rule, match, normalised_path, refusal
from assistant.netguard import Reply
from assistant.permissions import Gate, Info, Permissions
from assistant.presets import KEYS
from assistant.store import Store
from assistant.tools.connect_tools import connect_tools

from test_permissions import counting, fake_tools, run_calls

TG = "/bot{secret}/"
V = "/admin/api/2026-10"
SHOP = "shopify"


def gql(text):
    return {"query": text}


# (preset, method, url, json body) -> "never" | "ask" | None (= goes through). One realistic call each, plus the neighbours that must work.
CASES = [
    # ---- Telegram: Kelo's own bot (also as GET)
    *[("telegram", m, TG + n, None, "never") for m in ("GET", "POST") for n in ("setWebhook", "deleteWebhook", "getUpdates", "logOut", "close")],
    ("telegram", "POST", TG + "sendMessage", {"chat_id": 1, "text": "hi"}, None), ("telegram", "GET", TG + "getMe", None, None),
    ("telegram", "GET", TG + "getChat?chat_id=1", None, None),
    # ---- GitHub
    ("github", "POST", "/repos/o/r/keys", {"key": "ssh-ed25519 AAA"}, "never"), ("github", "POST", "/user/keys", {"key": "x"}, "never"),
    ("github", "POST", "/authorizations", {}, "never"), ("github", "POST", "/app/installations/5/access_tokens", {}, "never"),
    ("github", "DELETE", "/user/keys/1", None, "ask"), ("github", "POST", "/repos/o/r/hooks", {}, "ask"),
    ("github", "PUT", "/repos/o/r/actions/secrets/TOKEN", {}, "ask"), ("github", "POST", "/repos/o/r/actions/variables", {}, "ask"),
    ("github", "POST", "/repos/o/r/actions/runners/registration-token", None, "ask"),
    ("github", "PUT", "/repos/o/r/environments/prod/secrets/X", {}, "ask"), ("github", "PUT", "/repos/o/r/collaborators/bob", {}, "ask"),
    ("github", "POST", "/repos/o/r/transfer", {"new_owner": "x"}, "ask"), ("github", "DELETE", "/repos/o/r", None, "ask"),
    ("github", "PUT", "/orgs/o/memberships/bob", {}, "ask"), ("github", "POST", "/api/v3/admin/users", {}, "ask"),
    ("github", "POST", "/repos/o/r/actions/workflows/ci.yml/dispatches", {"ref": "main"}, "ask"),
    ("github", "POST", "/repos/o/r/dispatches", {"event_type": "x"}, "ask"),
    ("github", "PUT", "/repos/o/r/contents/.github/workflows/ci.yml", {"content": "x"}, "ask"),
    ("github", "POST", "/repos/o/r/issues", {"title": "t"}, None), ("github", "POST", "/repos/o/r/issues/1/comments", {"body": "b"}, None),
    ("github", "POST", "/repos/o/r/pulls", {}, None), ("github", "GET", "/repos/o/r/contents/README.md", None, None),
    ("github", "GET", "/repos/o/r/hooks", None, None), ("github", "DELETE", "/repos/o/r/issues/1/labels/bug", None, None),
    ("github", "PUT", "/repos/o/r/contents/hooks/pre-commit.sh", {"content": "x"}, None),        # a file called hooks/keys is just a file
    ("github", "PUT", "/repos/o/r/contents/keys/notes.txt", {"content": "x"}, None),
    # ---- Gitea and Forgejo (the same set under /api/v1)
    *[(p, m, u, b, mode) for p in ("gitea", "forgejo") for m, u, b, mode in [
        ("POST", "/api/v1/user/keys", {}, "never"), ("POST", "/api/v1/repos/o/r/keys", {}, "never"), ("POST", "/api/v1/users/bob/tokens", {}, "never"),
        ("POST", "/api/v1/user/applications/oauth2", {}, "never"), ("DELETE", "/api/v1/user/keys/3", None, "ask"),
        ("POST", "/api/v1/admin/users", {}, "ask"), ("POST", "/api/v1/repos/o/r/hooks", {}, "ask"),
        ("PUT", "/api/v1/repos/o/r/actions/secrets/X", {}, "ask"), ("PUT", "/api/v1/repos/o/r/actions/variables/X", {}, "ask"),
        ("PUT", "/api/v1/repos/o/r/collaborators/bob", {}, "ask"), ("POST", "/api/v1/repos/o/r/transfer", {}, "ask"),
        ("DELETE", "/api/v1/repos/o/r", None, "ask"), ("POST", "/api/v1/admin/actions/runners/registration-token", None, "ask"),
        ("POST", "/api/v1/repos/o/r/actions/workflows/ci.yml/dispatches", {}, "ask"),
        ("POST", "/api/v1/repos/o/r/issues", {"title": "t"}, None), ("POST", "/api/v1/repos/o/r/issues/1/comments", {}, None),
        ("POST", "/api/v1/repos/o/r/pulls", {}, None), ("GET", "/api/v1/repos/o/r/contents/README.md", None, None),
        ("POST", "/api/v1/repos/o/r/contents/keys/a.txt", {}, None)]],
    # ---- n8n: building and changing workflows asks; reading and the owner's own webhooks do not
    ("n8n", "POST", "/api/v1/workflows", {}, "ask"), ("n8n", "PUT", "/api/v1/workflows/1", {}, "ask"),
    ("n8n", "POST", "/api/v1/workflows/1/activate", None, "ask"), ("n8n", "POST", "/api/v1/workflows/1/deactivate", None, "ask"),
    ("n8n", "DELETE", "/api/v1/workflows/1", None, "ask"), ("n8n", "PUT", "/api/v1/workflows/1/transfer", {}, "ask"),
    ("n8n", "POST", "/api/v1/credentials", {}, "ask"), ("n8n", "POST", "/api/v1/users", [], "ask"), ("n8n", "POST", "/api/v1/source-control/pull", {}, "ask"),
    ("n8n", "POST", "/api/v1/variables", {}, "ask"), ("n8n", "POST", "/rest/api-keys", {}, "never"),
    ("n8n", "GET", "/api/v1/workflows", None, None), ("n8n", "GET", "/api/v1/executions", None, None),
    ("n8n", "POST", "/webhook/my-flow", {"a": 1}, None),
    # ---- Nextcloud
    ("nextcloud", "GET", "/ocs/v2.php/core/getapppassword", None, "never"), ("nextcloud", "POST", "/index.php/login/v2", None, "never"),
    ("nextcloud", "POST", "/ocs/v1.php/cloud/users", {}, "ask"), ("nextcloud", "PUT", "/ocs/v2.php/cloud/users/bob", {}, "ask"),
    ("nextcloud", "POST", "/ocs/v2.php/cloud/groups", {}, "ask"), ("nextcloud", "POST", "/ocs/v2.php/cloud/apps/files_external", None, "ask"),
    ("nextcloud", "DELETE", "/ocs/v2.php/cloud/apps/files_external", None, "ask"),
    ("nextcloud", "PUT", "/remote.php/dav/files/bob/a.txt", "x", None), ("nextcloud", "GET", "/ocs/v2.php/cloud/capabilities", None, None),
    ("nextcloud", "POST", "/index.php/apps/notes/api/v1/notes", {}, None), ("nextcloud", "GET", "/ocs/v2.php/cloud/users", None, None),
    # ---- Shopify: REST paths, then GraphQL by the body
    ("shopify", "POST", V + "/storefront_access_tokens.json", {}, "never"), ("shopify", "DELETE", V + "/storefront_access_tokens/1.json", None, "ask"),
    ("shopify", "PUT", V + "/themes/9/assets.json", {"asset": {"key": "a.liquid"}}, "ask"), ("shopify", "DELETE", V + "/themes/9/assets.json", None, "ask"),
    ("shopify", "POST", V + "/script_tags.json", {}, "ask"), ("shopify", "POST", V + "/webhooks.json", {}, "ask"),
    ("shopify", "POST", V + "/users.json", {}, "ask"), ("shopify", "POST", V + "/graphql.json", gql("mutation { storefrontAccessTokenCreate(input:{}) { userErrors { field } } }"), "never"),
    ("shopify", "POST", V + "/graphql.json", gql("mutation { delegateAccessTokenCreate(input:{}) { userErrors { field } } }"), "never"),
    ("shopify", "POST", V + "/graphql.json", gql("mutation themeFilesUpsert($f: [OnlineStoreThemeFilesUpsertFileInput!]!) { themeFilesUpsert(files: $f) { job { id } } }"), "ask"),
    ("shopify", "POST", V + "/graphql.json", gql("mutation { scriptTagCreate(input: {src: \"https://x\"}) { scriptTag { id } } }"), "ask"),
    ("shopify", "POST", V + "/graphql.json", gql("mutation { webhookSubscriptionCreate(topic: ORDERS_CREATE) { userErrors { field } } }"), "ask"),
    ("shopify", "POST", V + "/graphql.json", gql("mutation { staffMemberDelete(id: \"1\") { deletedStaffMemberId } }"), "ask"),
    ("shopify", "POST", V + "/graphql.json", gql("{ products(first: 5) { nodes { title } } }"), None),
    ("shopify", "POST", V + "/graphql.json", gql("{ staffMembers(first: 5) { nodes { name } } }"), None),
    ("shopify", "POST", V + "/graphql.json", gql("mutation { productUpdate(input: {id: \"1\", title: \"x\"}) { product { id } } }"), None),
    ("shopify", "POST", V + "/graphql.json", None, None),
    ("shopify", "GET", V + "/products.json", None, None), ("shopify", "GET", V + "/orders.json?limit=5", None, None),
    ("shopify", "POST", V + "/inventory_levels/set.json", {}, None), ("shopify", "GET", V + "/themes/9/assets.json", None, None),
    # ---- Pi-hole
    ("pihole", "GET", "/api/auth/app", None, "never"),
    ("pihole", "POST", "/api/action/restartdns", None, "ask"), ("pihole", "POST", "/api/action/gravity", None, "ask"),
    ("pihole", "POST", "/api/action/flush/network", None, "ask"), ("pihole", "POST", "/api/teleporter", None, "ask"),
    ("pihole", "POST", "/api/dns/blocking", {"blocking": False, "timer": 300}, "ask"), ("pihole", "PATCH", "/api/config", {"config": {}}, "ask"),
    ("pihole", "PUT", "/api/config/dns/hosts/1.2.3.4%20x.lan", None, "ask"),
    ("pihole", "POST", "/api/dns/blocking", {"blocking": True}, None), ("pihole", "GET", "/api/stats/summary", None, None),
    ("pihole", "POST", "/api/domains/deny/exact", {"domain": "ads.example"}, None), ("pihole", "GET", "/api/dns/blocking", None, None),
    # ---- AdGuard Home
    ("adguard_home", "POST", "/control/protection", {"enabled": False, "duration": 60000}, "ask"),
    ("adguard_home", "POST", "/control/dns_config", {}, "ask"), ("adguard_home", "POST", "/control/update", None, "ask"),
    ("adguard_home", "POST", "/control/tls/configure", {}, "ask"), ("adguard_home", "POST", "/control/install/configure", {}, "ask"),
    ("adguard_home", "POST", "/control/filtering/config", {"enabled": False, "interval": 0}, "ask"),
    ("adguard_home", "POST", "/control/safebrowsing/disable", None, "ask"), ("adguard_home", "POST", "/control/parental/disable", None, "ask"),
    ("adguard_home", "POST", "/control/safesearch/disable", None, "ask"),
    ("adguard_home", "POST", "/control/protection", {"enabled": True}, None), ("adguard_home", "POST", "/control/filtering/config", {"enabled": True, "interval": 24}, None),
    ("adguard_home", "POST", "/control/filtering/set_rules", {"rules": ["||ads.example^"]}, None), ("adguard_home", "GET", "/control/querylog", None, None),
    ("adguard_home", "POST", "/control/clients/add", {"name": "tv"}, None), ("adguard_home", "GET", "/control/stats", None, None),
    # ---- Frigate
    ("frigate", "POST", "/api/restart", None, "ask"), ("frigate", "POST", "/api/config/save", "x", "ask"), ("frigate", "PUT", "/api/config/set", {}, "ask"),
    ("frigate", "PUT", "/api/go2rtc/streams/door?src=exec:id", None, "ask"),
    ("frigate", "GET", "/api/events", None, None), ("frigate", "GET", "/api/config", None, None), ("frigate", "GET", "/api/front/latest.jpg", None, None),
    # ---- Paperless-ngx
    ("paperless", "POST", "/api/token/", {"username": "a", "password": "b"}, "never"), ("paperless", "POST", "/api/profile/generate_auth_token/", None, "never"),
    ("paperless", "POST", "/api/mail_accounts/", {}, "ask"), ("paperless", "PATCH", "/api/users/2/", {}, "ask"), ("paperless", "POST", "/api/groups/", {}, "ask"),
    ("paperless", "POST", "/api/workflows/", {}, "ask"), ("paperless", "PATCH", "/api/config/1/", {}, "ask"),
    ("paperless", "GET", "/api/documents/?query=tax", None, None), ("paperless", "POST", "/api/tags/", {"name": "t"}, None),
    ("paperless", "GET", "/api/mail_accounts/", None, None),
    # ---- Firefly III
    ("firefly_iii", "DELETE", "/api/v1/data/destroy?objects=accounts", None, "ask"), ("firefly_iii", "DELETE", "/api/v1/data/purge", None, "ask"),
    ("firefly_iii", "POST", "/api/v1/users", {}, "ask"), ("firefly_iii", "PUT", "/api/v1/configuration/x", {}, "ask"),
    ("firefly_iii", "POST", "/api/v1/webhooks", {}, "ask"), ("firefly_iii", "POST", "/oauth/personal-access-tokens", {}, "never"),
    ("firefly_iii", "POST", "/api/v1/transactions", {}, None), ("firefly_iii", "GET", "/api/v1/accounts", None, None),
    ("firefly_iii", "DELETE", "/api/v1/transactions/5", None, None),
    # ---- Mealie
    ("mealie", "POST", "/api/users/api-tokens", {"name": "k"}, "never"), ("mealie", "POST", "/api/admin/backups/x/restore", None, "ask"),
    ("mealie", "PUT", "/api/admin/users/1", {}, "ask"),
    ("mealie", "GET", "/api/recipes", None, None), ("mealie", "POST", "/api/households/shopping/items", {}, None),
    # ---- Immich
    ("immich", "POST", "/api/api-keys", {"name": "k"}, "never"), ("immich", "POST", "/api/api-keys/1/rotate", None, "never"),
    ("immich", "DELETE", "/api/api-keys/1", None, "ask"), ("immich", "PUT", "/api/admin/users/1", {}, "ask"),
    ("immich", "POST", "/api/admin/database-backups/start-restore", None, "ask"), ("immich", "PUT", "/api/system-config", {}, "ask"),
    ("immich", "PUT", "/api/jobs/thumbnailGeneration", {"command": "start"}, "ask"),
    ("immich", "POST", "/api/search/metadata", {}, None), ("immich", "GET", "/api/albums", None, None), ("immich", "POST", "/api/albums", {}, None),
    ("immich", "DELETE", "/api/assets", {"ids": ["1"]}, None), ("immich", "GET", "/api/api-keys", None, None),
    # ---- Datadog
    ("datadog", "POST", "/api/v2/api_keys", {}, "never"), ("datadog", "POST", "/api/v1/application_key", {}, "never"),
    ("datadog", "POST", "/api/v2/current_user/application_keys", {}, "never"), ("datadog", "DELETE", "/api/v2/api_keys/1", None, "ask"),
    ("datadog", "POST", "/api/v2/users", {}, "ask"), ("datadog", "POST", "/api/v2/roles", {}, "ask"),
    ("datadog", "GET", "/api/v1/monitor", None, None), ("datadog", "POST", "/api/v1/events", {}, None), ("datadog", "GET", "/api/v1/query?q=x", None, None),
    ("datadog", "GET", "/api/v2/api_keys", None, None),
    # ---- Slack
    ("slack", "POST", "/api/oauth.v2.access", {}, "never"), ("slack", "POST", "/api/tooling.tokens.rotate", {}, "never"),
    ("slack", "POST", "/api/admin.users.invite", {}, "ask"), ("slack", "GET", "/api/admin.teams.list", None, "ask"),
    ("slack", "POST", "/api/apps.uninstall", {}, "ask"), ("slack", "POST", "/api/auth.revoke", {}, "ask"),
    ("slack", "POST", "/api/chat.postMessage", {"channel": "C1", "text": "hi"}, None), ("slack", "GET", "/api/conversations.history?channel=C1", None, None),
    ("slack", "GET", "/api/search.messages?query=x", None, None), ("slack", "GET", "/api/auth.test", None, None),
    # ---- Miniflux
    ("miniflux", "POST", "/v1/api-keys", {"description": "k"}, "never"), ("miniflux", "DELETE", "/v1/api-keys/2", None, "ask"),
    ("miniflux", "POST", "/v1/users", {}, "ask"), ("miniflux", "PUT", "/v1/users/2", {}, "ask"), ("miniflux", "PUT", "/v1/flush-history", None, "ask"),
    ("miniflux", "GET", "/v1/entries", None, None), ("miniflux", "GET", "/v1/feeds", None, None), ("miniflux", "PUT", "/v1/entries", {"entry_ids": [1]}, None),
    # ---- Linkwarden, Karakeep, Wallabag, FreshRSS
    ("linkwarden", "POST", "/api/v1/tokens", {}, "never"), ("linkwarden", "POST", "/api/v1/users", {}, "ask"), ("linkwarden", "DELETE", "/api/v1/tokens/1", None, "ask"),
    ("linkwarden", "GET", "/api/v1/links", None, None), ("linkwarden", "POST", "/api/v1/links", {}, None),
    ("karakeep", "POST", "/api/v1/api-keys", {}, "never"), ("karakeep", "PUT", "/api/v1/admin/users/1", {}, "ask"),
    ("karakeep", "GET", "/api/v1/bookmarks", None, None), ("karakeep", "POST", "/api/v1/bookmarks", {}, None),
    ("wallabag", "POST", "/oauth/v2/token", {}, "never"), ("wallabag", "POST", "/api/clients", {}, "never"), ("wallabag", "POST", "/api/user", {}, "ask"),
    ("wallabag", "GET", "/api/entries.json", None, None), ("wallabag", "POST", "/api/entries.json", {"url": "https://x"}, None),
    ("wallabag", "DELETE", "/api/entries/1.json", None, None), ("wallabag", "GET", "/api/user", None, None),
    ("freshrss", "POST", "/api/greader.php/accounts/ClientLogin", None, "never"),
    ("freshrss", "GET", "/api/greader.php/reader/api/0/stream/contents/reading-list", None, None),
    ("freshrss", "POST", "/api/greader.php/reader/api/0/edit-tag", None, None),
    # ---- Home Assistant (as before: code never, the system asks, switching works)
    *[("home_assistant", "POST", f"/api/services/{d}/x", {}, "never") for d in ("shell_command", "command_line", "python_script", "pyscript", "rest_command")],
    ("home_assistant", "POST", "/api/services/hassio/host_reboot", {}, "ask"), ("home_assistant", "POST", "/api/hassio/addons/x/start", {}, "ask"),
    ("home_assistant", "POST", "/api/hassio_ingress/x", {}, "ask"), ("home_assistant", "POST", "/api/services/homeassistant/restart", {}, "ask"),
    ("home_assistant", "POST", "/api/services/homeassistant/stop", {}, "ask"),
    ("home_assistant", "POST", "/api/services/light/turn_on", {}, None), ("home_assistant", "POST", "/api/services/homeassistant/turn_off", {}, None),
    ("home_assistant", "POST", "/api/services/script/turn_on", {}, None), ("home_assistant", "GET", "/api/states", None, None),
]
IDS = [f"{c[0]}:{c[1]} {c[2]}" + (f" {json.dumps(c[3])[:40]}" if isinstance(c[3], dict) and c[0] in ("shopify", "pihole", "adguard_home") else "") for c in CASES]


def item_of(pid):
    return KEYS[pid].item if pid in KEYS else None


class Net:
    """A fetcher that records what would have been sent."""

    def __init__(self):
        self.calls = []

    async def __call__(self, method, url, *, allowed_hosts, headers=None, content=None, **kw):
        self.calls.append((method, url))
        return Reply(200, {"content-type": "application/json"}, b'{"ok": true}', False)


def world(pid):
    store, net = Store(), Net()
    item = item_of(pid)
    if pid == "github":
        item = "github-api"
    place = KEYS[pid].placement if pid in KEYS and KEYS[pid].placement.get("type") == "url_path_segment" else {"type": "bearer_header"}
    store.save_credential(item, pid, ["api.example.test"], place, "")
    store.set_credential_secret(item, "sekret-value-12345")
    tools = {x.name: x for x in connect_tools(store, fetcher=net)}
    return store, net, tools, item


def full(url):
    return "https://api.example.test" + (url if url.startswith("/") else "/" + url)


def url_for(pid, url):
    return full(url)


@pytest.mark.parametrize("pid,method,url,body,mode", CASES, ids=IDS)
def test_every_rule_names_its_mode_and_the_neighbours_stay_free(pid, method, url, body, mode):
    rule = match(pid, method, url, body)
    assert (rule.mode if rule else None) == mode


@pytest.mark.parametrize("pid,method,url,body,mode", [c for c in CASES if c[4] == "never"], ids=lambda v: str(v)[:50])
async def test_a_never_call_is_refused_before_any_request(pid, method, url, body, mode):
    store, net, tools, item = world(pid)
    args = {"name": item, "url": url_for(pid, url)}
    if method == "GET":
        out = await tools["connector_get"].handler(args)
        down = await tools["connector_download"].handler(args)
        assert "not run" in str(down)
    else:
        out = await tools["connector_send"].handler({**args, "method": method, "json": body})
    assert str(out).startswith("error: not run. Kelo never makes this call:") and net.calls == []


@pytest.mark.parametrize("pid,method,url,body,mode", [c for c in CASES if c[4] is None], ids=lambda v: str(v)[:50])
async def test_the_calls_next_to_a_rule_still_go_through(pid, method, url, body, mode):
    store, net, tools, item = world(pid)
    args = {"name": item, "url": url_for(pid, url)}
    out = await (tools["connector_get"].handler(args) if method == "GET" else tools["connector_send"].handler({**args, "method": method, "json": body}))
    assert "HTTP 200" in str(out) and len(net.calls) == 1


@pytest.mark.parametrize("pid,method,url,body,mode", [c for c in CASES if c[4] == "ask"], ids=lambda v: str(v)[:50])
async def test_an_ask_call_is_a_forced_card_with_the_connector_set_to_allow_and_no_request_before_it(pid, method, url, body, mode):
    store, net, tools, item = world(pid)
    tool = tools["connector_get" if method == "GET" else "connector_send"]
    args = {"name": item, "url": url_for(pid, url)} | ({} if method == "GET" else {"method": method, "json": body})
    p = Permissions(store, tools=lambda: fake_tools(), names=lambda cid: cid)
    verdict = await Gate(p).check(tool, args)
    assert verdict.action == "ask" and verdict.card and net.calls == []
    # through the real loop: the owner's "allow" for this connector and the tool's own auto-approve do not skip the card
    for cfg in ({}, {"read": "allow", "change": "allow", "delete": "allow"}):
        if cfg:
            fam_info = Info(effects.credential_connector(item), pid, "key")
            p.put(fam_info, cfg)
        ran = []
        res, asked, _ = await run_calls([counting(tool.name, ran, requires_approval=tool.name == "connector_send", auto_approve=lambda a: True)], Gate(p),
                                        (tool.name, args))
        assert len(asked) == 1 and asked[0].force is True and asked[0].detail and ran, (cfg, asked)


def test_a_task_permission_that_covers_the_call_does_not_skip_the_card(tmp_path):
    from test_conditional_grants import in_scope, world as grants_world
    store, wallet, schedules, grants, poll = grants_world(tmp_path)
    scope = f"schedule:{poll['id']}"
    grants.add_action(scope, "change", "keep n8n tidy", ["connector:n8n-api"], max_count=5)
    args = {"name": "n8n-api", "method": "POST", "url": "/api/v1/workflows/1/activate"}
    with in_scope(scope):
        assert grants.action_check("connector_send", args).grant is not None            # the permission covers this call ...
        assert grants.action_check("connector_send", {**args, "url": "/api/v1/tags"}).grant is not None
    hit = forbidden.classify("connector_send", args)
    assert hit is not None and hit.rule.mode == "ask"                                   # ... but the gate forces a card, which approve() never lets a grant skip
    effects.remember_judged("n8n-api", "POST /api/v1/tags", "change", False)             # the tool judge's reading of the other call
    assert effects.keeps_own_checks("connector_send", args) and not effects.keeps_own_checks("connector_send", {**args, "url": "/api/v1/tags"})
    effects.forget_mcp_tools()


async def test_a_never_call_is_off_at_the_gate_and_the_owners_allow_does_not_lift_it():
    store = Store()
    p = Permissions(store, tools=lambda: fake_tools(), names=lambda cid: cid)
    tool = counting("connector_send", [])
    v = await Gate(p).check(tool, {"name": "telegram-bot", "method": "POST", "url": "https://api.telegram.org/bot{secret}/setWebhook"})
    assert v.action == "off" and v.message.startswith("error: not run. Kelo never makes this call: it manages the system the service runs on (Telegram)")
    ran = []
    res, asked, _ = await run_calls([counting("connector_send", ran, requires_approval=True)], Gate(p),
                                    ("connector_send", {"name": "telegram-bot", "method": "POST", "url": "https://api.telegram.org/bot{secret}/close"}))
    assert ran == [] and asked == [] and res[0].is_error


async def test_what_the_owner_turned_off_stays_off_before_the_card():
    store = Store()
    tools = fake_tools()
    p = Permissions(store, tools=lambda: tools, names=lambda cid: cid)
    p.put(Info("n8n", "n8n", "key"), {"change": "off"})
    v = await Gate(p).check(counting("connector_send", []), {"name": "n8n-api", "method": "POST", "url": "/api/v1/workflows", "json": {}})
    assert v.action == "off" and "turned this off" in v.message


async def test_the_card_line_says_what_the_call_does_and_joins_the_privacy_guards_line():
    p = Permissions(Store(), tools=lambda: fake_tools(), names=lambda cid: cid)
    tool = counting("connector_send", [])
    for url, words in (("/api/v1/workflows", "run code"), ("/api/v1/users", "who can get into"), ("/api/action/gravity", "manage the system"),
                       ("/api/v1/data/destroy", "delete data")):
        name = {"/api/v1/workflows": "n8n-api", "/api/v1/users": "n8n-api", "/api/action/gravity": "pihole-api", "/api/v1/data/destroy": "firefly-iii-api"}[url]
        method = "DELETE" if "destroy" in url else "POST"
        v = await Gate(p).check(tool, {"name": name, "method": method, "url": url, "json": {}})
        assert v.action == "ask" and words in v.card, (url, v.card)


# ---- a preset without rules, an installed package, an unknown name
@pytest.mark.parametrize("pid", ["asana", "calendly", "clickup_key", "figma", "incident_io", "monday", "places", "stripe_key", "ticketmaster", "wise",
                                 "zapier", "zoom", "grocy", "tandoor", "actual_budget", "uptime_kuma", "beszel", "n8n_mcp"])
async def test_a_preset_without_rules_is_never_refused_or_asked(pid):
    assert pid not in RULES and pid in KEYS
    store, net, tools, item = world(pid)
    for url in ("/admin/users", "/api/v1/workflows", "/bot{secret}/setWebhook", "/api/services/shell_command/x", "/api/admin.users.invite"):
        assert refusal(pid, "POST", url, {}) == "" and match(pid, "POST", url, {}) is None
        assert forbidden.classify("connector_send", {"name": item, "method": "POST", "url": url}) is None
    out = await tools["connector_send"].handler({"name": item, "method": "POST", "url": full("/api/v1/workflows"), "json": {}})
    assert "HTTP 200" in str(out)


def test_an_unknown_name_or_an_installed_package_has_no_rules():
    assert forbidden.preset_of("some-package-api") is None and forbidden.preset_of("") is None
    assert forbidden.classify("connector_send", {"name": "some-package-api", "method": "POST", "url": "/api/v1/workflows"}) is None
    assert forbidden.classify("email_send", {"name": "n8n-api", "url": "/api/v1/workflows"}) is None
    assert forbidden.preset_of("  N8N-API ") == "n8n"


# ---- the path is normalised before it is matched
@pytest.mark.parametrize("url,path", [
    ("/API/Services/Shell_Command/x", "/api/services/shell_command/x"),
    ("/api/services/shell%5Fcommand/x", "/api/services/shell_command/x"),
    ("/api/services/shell%255Fcommand/x", "/api/services/shell_command/x"),
    ("//api//services///shell_command//x/", "/api/services/shell_command/x"),
    ("http://192.168.0.10:8123/api/services/shell_command/x", "/api/services/shell_command/x"),
    ("https://api.example.test/api/services/shell_command/x/?a=1&b=2#frag", "/api/services/shell_command/x"),
    ("/api/services/shell_command?x=1", "/api/services/shell_command"),
    ("/api/services/shell_command#top", "/api/services/shell_command"),
    ("/api/services/light/../shell_command/x", "/api/services/shell_command/x"),
    ("/api/./services/shell_command", "/api/services/shell_command"),
    ("\\api\\services\\shell_command", "/api/services/shell_command"),
    ("/api/services/shell_command;x=1/y", "/api/services/shell_command/y"),
    ("/nextcloud/ocs/v2.php/cloud/users", "/nextcloud/ocs/v2.php/cloud/users"),
    ("", ""), ("/", ""), ("https://api.example.test", ""),
])
def test_normalised_path(url, path):
    assert normalised_path(url) == path


@pytest.mark.parametrize("url", ["/API/Services/Shell_Command/x", "/api/services/shell%5Fcommand/x", "//api//services//shell_command//x/",
                                 "http://192.168.0.10:8123/api/services/shell_command/x", "/api/services/shell_command?x=1",
                                 "/api/services/light/../shell_command/x", "/home/api/services/shell_command"])
def test_each_spelling_of_a_forbidden_path_is_refused(url):
    assert refusal("home_assistant", "POST", url).startswith("error: not run")


def test_a_path_prefix_in_front_of_the_api_does_not_hide_a_call():
    assert match("nextcloud", "POST", "https://nas.example.ts.net/nextcloud/ocs/v2.php/cloud/users") is not None
    assert match("home_assistant", "POST", "/ha/api/services/hassio/host_reboot") is not None
    assert match("n8n", "POST", "/n8n/api/v1/workflows") is not None


# ---- the text
def test_the_refusal_names_the_class_and_the_service():
    cases = {("home_assistant", "/api/services/shell_command/x"): ("runs code", "Home Assistant"),
             ("datadog", "/api/v2/api_keys"): ("could take over an account", "Datadog"),
             ("telegram", "/bot{secret}/getUpdates"): ("manages the system the service runs on", "Telegram")}
    for (pid, url), (words, service) in cases.items():
        out = refusal(pid, "POST", url)
        assert out == (f"error: not run. Kelo never makes this call: it {words} ({service}). Tell the owner they can do it in {service} "
                       "itself if they really want it.")
    assert isinstance(refusal("datadog", "POST", "/api/v2/api_keys"), forbidden.Ours)
    assert refusal("datadog", "GET", "/api/v1/monitor") == "" and refusal("datadog", "DELETE", "/api/v2/api_keys/1") == ""    # an ask, not a refusal
    assert "data that cannot come back" in forbidden.PHRASE["data"]


def test_the_table_is_sound():
    seen = 0
    for pid, rules in RULES.items():
        assert pid in KEYS or pid == "github", pid
        for r in rules:
            seen += 1
            assert isinstance(r, Rule) and r.why in forbidden.WHY and r.mode in forbidden.MODES and r.doc.startswith("https://")
            assert r.methods <= {"*", "GET", "POST", "PUT", "PATCH", "DELETE"} and r.methods
            assert r.path.pattern == r.path.pattern.lower()                                  # matched against a lower-cased path
    assert seen == len(forbidden.table())
    assert set(RULES) == {"telegram", "github", "gitea", "forgejo", "n8n", "nextcloud", "shopify", "pihole", "adguard_home", "frigate", "paperless",
                          "firefly_iii", "mealie", "immich", "datadog", "slack", "miniflux", "linkwarden", "karakeep", "wallabag", "freshrss",
                          "home_assistant"}
    # "never" only for what breaks Kelo, runs code on the owner's machine with no other purpose, or mints a key
    never = {(pid, r.path.pattern) for pid, r in forbidden.table() if r.mode == "never"}
    assert all(r.methods <= {"POST", "PUT", "PATCH", "*"} for pid, r in forbidden.table() if r.mode == "never")
    assert len(never) >= 20


def test_the_per_service_rules_are_data_in_a_file_not_code():
    """Phase E (docs/no-word-lists.md): the tables live in content/forbidden_rules.json; the module only loads them."""
    import json
    from pathlib import Path
    raw = json.loads((Path(forbidden.__file__).resolve().parents[2] / "content" / "forbidden_rules.json").read_text(encoding="utf-8"))
    assert {k: len(v) for k, v in raw.items()} == {k: len(v) for k, v in RULES.items()} and len(raw) == 22
    assert {"telegram", "github", "forgejo", "gitea", "home_assistant", "slack", "datadog"} <= set(raw)
    src = Path(forbidden.__file__).read_text(encoding="utf-8")
    assert "setwebhook" not in src and "api/services/" not in src and "conversions" not in src       # no service's path in code


def test_a_missing_rules_file_stops_the_server_from_starting(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("ASSISTANT_CONTENT_DIR", str(tmp_path))
    with pytest.raises(OSError):
        importlib.reload(forbidden)
    monkeypatch.delenv("ASSISTANT_CONTENT_DIR")
    importlib.reload(forbidden)
    assert forbidden.RULES and forbidden.refusal("telegram", "POST", "/bot123:abc/setWebhook")
