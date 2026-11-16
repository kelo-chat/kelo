"""Calls Kelo never makes on a connected service, or always asks about, whoever asks.

One table of rules per connector (preset id, the id the connector has in the catalog). A rule names the HTTP methods, a pattern for the
request's normalised path, optionally a pattern for the JSON body, and what the call does (`why`):

  code      runs code (on the owner's machine, in their automation, on their live store)
  takeover  changes who can get in, or mints a key that could (tokens, API keys, app passwords, users, mail passwords)
  system    manages the system the service runs on (restart, config, protection off, backup restore)
  data      deletes data that cannot come back

and a `mode`:

  never  refused before any request is made, whoever asks. Only for calls that break or hijack Kelo itself (Telegram webhook and
         polling), run arbitrary code on the owner's machine with no other purpose (Home Assistant shell, scripts) and calls that MINT A
         NEW SECRET that would come back to Kelo (access tokens, API keys, app passwords, deploy and SSH keys).
  ask    the step always shows an Allow/Deny card, even where the owner's connector setting is "allow", a standing rule, a task's
         permission or the "asked directly" rule for the owner's device app would have let it through (permissions.Gate).

The model is never told this list; it learns it from the refusal. `tools/connector_effects.py` prints the table into
docs/connector-permissions.md (a test keeps it in step).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from .agent import Ours
from .i18n import N_, t

W = frozenset({"POST", "PUT", "PATCH", "DELETE"})    # every method that changes something
MINT = frozenset({"POST", "PUT", "PATCH"})            # creating or changing a key (deleting one mints nothing)
ANY = frozenset({"*"})
DELETE = frozenset({"DELETE"})

WHY = ("code", "takeover", "system", "data")
MODES = ("never", "ask")
PHRASE = {"code": "runs code", "takeover": "could take over an account", "system": "manages the system the service runs on",
          "data": "deletes data that cannot come back"}
CARD = {"code": N_("This can run code ({service})."), "takeover": N_("This can change who can get into {service}."),
        "system": N_("This can manage the system {service} runs on."), "data": N_("This can delete data in {service} that cannot come back.")}


@dataclass(frozen=True)
class Rule:
    methods: frozenset[str]        # {"POST", "PUT", "PATCH", "DELETE"} (or with "GET" too); "*" = every method
    path: re.Pattern[str]          # .search() on the normalised path; anchored with ^ where meant
    why: str                       # code | takeover | system | data
    mode: str = "ask"              # never | ask
    body: re.Pattern[str] | None = None   # also .search() on the JSON body's text; a call without a body never matches
    doc: str = ""                  # the service's own API documentation this was taken from
    sure: bool = True              # False: the doc page could not be read in full; the path is a conservative prefix


@dataclass(frozen=True)
class Hit:
    preset: str
    rule: Rule

    @property
    def service(self) -> str:
        return service_name(self.preset)

    @property
    def refusal(self) -> str:
        return Ours(f"error: not run. Kelo never makes this call: it {PHRASE[self.rule.why]} ({self.service}). Tell the owner they can do "
                    f"it in {self.service} itself if they really want it.")

    @property
    def card(self) -> str:
        return t(CARD[self.rule.why], service=self.service)


def _r(methods: frozenset[str], path: str, why: str, mode: str = "ask", *, doc: str, body: str | None = None, sure: bool = True) -> Rule:
    assert why in WHY and mode in MODES
    return Rule(methods, re.compile(path), why, mode, re.compile(body, re.I | re.S) if body else None, doc, sure)


def _mint(path: str, *, doc: str, sure: bool = True, why: str = "takeover", methods: frozenset[str] = MINT) -> tuple[Rule, ...]:
    """A key or token that comes back to the caller: creating it is never done; revoking it asks (it could cut Kelo's own key)."""
    return (_r(methods, path, why, "never", doc=doc, sure=sure),
            *((_r(DELETE, path, why, "ask", doc=doc, sure=sure),) if "DELETE" not in methods and "*" not in methods else ()))


E = r"(?![a-z0-9_-])"                          # the end of a path segment's name (".json" and "/" and "$" pass)
NOT_FILE = r"^(?!.*/(?:contents|raw|src)/).*?"  # git hosts: a file called "hooks" or "keys" inside a repository is no hook or key


# ---------------------------------------------------------------------------------------------------------------------------------
# Telegram: the bot is Kelo's own channel to the owner. Method names are case-insensitive and both GET and POST work (the paths here are
# lower-cased by normalised_path; the token sits in the path as /bot<token>/).
TELEGRAM = "https://core.telegram.org/bots/api"
_TELEGRAM = (_r(ANY, r"^/bot[^/]*(?:/test)?/(?:setwebhook|deletewebhook|getupdates|logout|close)$", "system", "never", doc=TELEGRAM),)

# GitHub (api.github.com, or a GitHub Enterprise Server's /api/v3): the paths and methods are from docs.github.com/en/rest
# (workflow dispatch/enable/disable confirmed on the page; the rest follow the REST reference's resource names).
GITHUB = "https://docs.github.com/en/rest"
_GITHUB_DISPATCH = "https://docs.github.com/en/rest/actions/workflows"
_GITHUB = (
    # keys and tokens that would come back to Kelo
    *_mint(NOT_FILE + r"/(?:user|repos/[^/]+/[^/]+)/(?:ssh_signing_)?keys" + E, doc=GITHUB + "/deploy-keys/deploy-keys"),
    *_mint(NOT_FILE + r"/authorizations" + E, doc=GITHUB + "/oauth-authorizations"),
    *_mint(NOT_FILE + r"/applications/[^/]+/token" + E, doc=GITHUB + "/apps/oauth-applications"),
    *_mint(NOT_FILE + r"/app/installations/[^/]+/access_tokens" + E, doc=GITHUB + "/apps/apps"),
    *_mint(NOT_FILE + r"/app-manifests/[^/]+/conversions" + E, doc=GITHUB + "/apps/apps"),
    _r(W, NOT_FILE + r"/admin(?:/|$)", "system", doc=GITHUB + "/enterprise-admin"),
    _r(W, NOT_FILE + r"/hooks" + E, "system", doc=GITHUB + "/webhooks/repo-config"),
    _r(W, NOT_FILE + r"/(?:actions|dependabot|codespaces)/(?:secrets|variables|runners)" + E, "code", doc=GITHUB + "/actions/secrets"),
    _r(W, NOT_FILE + r"/environments/[^/]+/(?:secrets|variables)" + E, "code", doc=GITHUB + "/actions/secrets"),
    _r(W, NOT_FILE + r"/(?:collaborators|invitations)" + E, "takeover", doc=GITHUB + "/collaborators/collaborators"),
    _r(W, NOT_FILE + r"/orgs/[^/]+/(?:members|memberships|outside_collaborators|invitations)" + E, "takeover", doc=GITHUB + "/orgs/members"),
    _r(W, NOT_FILE + r"/teams/[^/]+/(?:members|memberships)" + E, "takeover", doc=GITHUB + "/teams/members"),
    _r(frozenset({"POST"}), NOT_FILE + r"/transfer$", "system", doc=GITHUB + "/repos/repos"),
    _r(DELETE, NOT_FILE + r"/repos/[^/]+/[^/]+$", "data", doc=GITHUB + "/repos/repos"),
    _r(W, NOT_FILE + r"/actions/workflows/[^/]+/(?:dispatches|enable|disable)$", "code", doc=_GITHUB_DISPATCH),
    _r(W, NOT_FILE + r"/actions/runs/[^/]+/(?:rerun|rerun-failed-jobs)$", "code", doc=GITHUB + "/actions/workflow-runs"),
    _r(frozenset({"POST"}), NOT_FILE + r"/repos/[^/]+/[^/]+/dispatches$", "code", doc=GITHUB + "/repos/repos"),
    _r(W, r"/contents/\.github/workflows(?:/|$)", "code", doc=GITHUB + "/repos/contents"),   # a workflow file is code that runs on push
)

# Gitea and Forgejo (/api/v1): the /admin/ paths, hooks and runners were read in Gitea's swagger.v1.json (gitea.com/swagger.v1.json,
# first 100 kB); keys, tokens, collaborators, transfer and the secrets/variables follow the same swagger's resource names.
GITEA = "https://docs.gitea.com/api/1.22/"
FORGEJO = "https://forgejo.org/docs/latest/user/api-usage/"
_GITEA = (
    *_mint(NOT_FILE + r"/(?:user|repos/[^/]+/[^/]+|admin/users/[^/]+)/keys" + E, doc=GITEA, sure=False),
    *_mint(NOT_FILE + r"/users/[^/]+/tokens" + E, doc=GITEA, sure=False),
    *_mint(NOT_FILE + r"/user/applications/oauth2" + E, doc=GITEA, sure=False),
    _r(W, NOT_FILE + r"/admin(?:/|$)", "system", doc=GITEA),
    _r(W, NOT_FILE + r"/hooks" + E, "system", doc=GITEA),
    _r(W, NOT_FILE + r"/actions/(?:secrets|variables|runners)" + E, "code", doc=GITEA),
    _r(W, NOT_FILE + r"/(?:collaborators|invitations)" + E, "takeover", doc=GITEA, sure=False),
    _r(W, NOT_FILE + r"/orgs/[^/]+/members" + E, "takeover", doc=GITEA, sure=False),
    _r(W, NOT_FILE + r"/teams/[^/]+/members" + E, "takeover", doc=GITEA, sure=False),
    _r(W, NOT_FILE + r"/user/gpg_keys" + E, "takeover", doc=GITEA, sure=False),
    _r(frozenset({"POST"}), NOT_FILE + r"/transfer$", "system", doc=GITEA, sure=False),
    _r(DELETE, NOT_FILE + r"/repos/[^/]+/[^/]+$", "data", doc=GITEA, sure=False),
    _r(W, NOT_FILE + r"/actions/workflows/[^/]+/dispatches$", "code", doc=GITEA, sure=False),
    _r(W, r"/contents/\.(?:github|gitea|forgejo)/workflows(?:/|$)", "code", doc=GITEA, sure=False),
)

# n8n's public API (docs.n8n.io/api/api-reference/): the docs page that could be read lists the users endpoints (confirmed); workflows,
# credentials, source-control, variables and projects are the other resources of the same /api/v1. Running an existing workflow through
# its own webhook (/webhook/...) is not touched: the owner built it.
N8N = "https://docs.n8n.io/api/api-reference/"
_N8N = (
    _r(W, r"(?:^|/)api/v1/(?:workflows|source-control|variables)" + E, "code", doc=N8N, sure=False),
    _r(W, r"(?:^|/)api/v1/credentials" + E, "takeover", doc=N8N, sure=False),
    _r(W, r"(?:^|/)api/v1/users" + E, "takeover", doc=N8N),
    _r(W, r"(?:^|/)api/v1/projects" + E, "takeover", doc=N8N, sure=False),
    _r(MINT, r"(?:^|/)rest/api-keys" + E, "takeover", "never", doc=N8N, sure=False),               # the editor's own (cookie) API
    _r(W, r"(?:^|/)rest/(?:workflows|credentials|users|variables|source-control)" + E, "code", doc=N8N, sure=False),
)

# Nextcloud's provisioning API (docs.nextcloud.com ... instruction_set_for_users: the users endpoints under /ocs/v1.php/cloud/ confirmed;
# groups, apps and the app-password login endpoints are from the Nextcloud OCS reference and were not on the page that could be read).
NEXTCLOUD = "https://docs.nextcloud.com/server/latest/admin_manual/configuration_user/instruction_set_for_users.html"
_NC = r"/ocs/v[12]\.php/(?:cloud|apps/provisioning_api/api/v1)"
_NEXTCLOUD = (
    _r(ANY, r"/ocs/v[12]\.php/core/(?:get)?apppassword" + E, "takeover", "never", doc=NEXTCLOUD, sure=False),
    _r(ANY, r"/(?:index\.php/)?login/(?:v2|flow)" + E, "takeover", "never", doc=NEXTCLOUD, sure=False),
    _r(W, _NC + r"/(?:users|groups)" + E, "takeover", doc=NEXTCLOUD),
    _r(W, _NC + r"/apps" + E, "code", doc=NEXTCLOUD, sure=False),
    _r(W, r"(?:^|/)settings/(?:apps|users)" + E, "takeover", doc=NEXTCLOUD, sure=False),
)

# Shopify: REST Admin paths are /admin/api/<version>/<resource>.json (docs: shopify.dev/docs/api/admin-rest, a legacy API); GraphQL is
# POST /admin/api/<version>/graphql.json, so its mutations are looked for in the JSON body (shopify.dev/docs/api/admin-graphql).
SHOPIFY = "https://shopify.dev/docs/api/admin-rest"
SHOPIFY_GQL = "https://shopify.dev/docs/api/admin-graphql"
_SA = r"/admin/api/[^/]+"
_GQL = _SA + r"/graphql(?:\.json)?$"
_SHOPIFY = (
    *_mint(_SA + r"/storefront_access_tokens" + E, doc=SHOPIFY),
    _r(frozenset({"POST"}), _GQL, "takeover", "never", doc=SHOPIFY_GQL,
       body=r"mutation\b.*\b(?:storefrontAccessTokenCreate|delegateAccessTokenCreate)\b"),
    _r(W, _SA + r"/themes/[^/]+/assets" + E, "code", doc=SHOPIFY),
    _r(W, _SA + r"/themes(?:\.json|/[^/]+\.json)$", "code", doc=SHOPIFY),
    _r(W, _SA + r"/script_tags" + E, "code", doc=SHOPIFY),
    _r(W, _SA + r"/webhooks" + E, "system", doc=SHOPIFY),
    _r(W, _SA + r"/(?:users|staff_members?)" + E, "takeover", doc=SHOPIFY),
    _r(W, r"/admin/(?:api_permissions|oauth)(?:/|$)", "takeover", doc=SHOPIFY, sure=False),
    _r(frozenset({"POST"}), _GQL, "code", doc=SHOPIFY_GQL, body=(
        r"mutation\b.*\b(?:theme(?:Create|Update|Delete|Publish|FilesUpsert|FilesCopy|FilesDelete)|scriptTag(?:Create|Update|Delete)"
        r"|(?:webhookSubscription|eventBridgeWebhookSubscription|pubSubWebhookSubscription)(?:Create|Update|Delete)"
        r"|staffMember\w*|storefrontAccessTokenDelete|appRevokeAccessScopes|appUninstall)\b")),
)

# Pi-hole v6 (the API is documented on the Pi-hole's own /api/docs page; docs.pi-hole.net/api points there and could not show the
# paths): /api/action/* is in FTL's action.yaml (restartdns, gravity, flush/logs, flush/network, confirmed); teleporter, config,
# dns/blocking and auth/app are from the same API's reference and are conservative.
PIHOLE = "https://docs.pi-hole.net/api/"
_PIHOLE = (
    _r(ANY, r"(?:^|/)api/auth/app" + E, "takeover", "never", doc=PIHOLE, sure=False),
    _r(W, r"(?:^|/)api/action(?:/|$)", "system", doc="https://github.com/pi-hole/FTL/blob/master/src/api/docs/content/specs/action.yaml"),
    _r(W, r"(?:^|/)api/teleporter" + E, "system", doc=PIHOLE, sure=False),
    _r(frozenset({"POST"}), r"(?:^|/)api/dns/blocking$", "system", doc=PIHOLE, body=r'"blocking"\s*:\s*(?:false|"false"|0)'),
    _r(W, r"(?:^|/)api/config(?:/|$)", "system", doc=PIHOLE, sure=False),
)

# AdGuard Home: openapi.yaml of the project (raw.githubusercontent.com/AdguardTeam/AdGuardHome/master/openapi/openapi.yaml), all read.
ADGUARD = "https://github.com/AdguardTeam/AdGuardHome/blob/master/openapi/openapi.yaml"
_OFF = r'"enabled"\s*:\s*(?:false|"false"|0)'
_ADGUARD = (
    _r(frozenset({"POST"}), r"(?:^|/)control/protection$", "system", doc=ADGUARD, body=_OFF),
    _r(W, r"(?:^|/)control/(?:dns_config|update|tls/configure|install/[a-z_]+)$", "system", doc=ADGUARD),
    _r(W, r"(?:^|/)control/filtering/config$", "system", doc=ADGUARD, body=_OFF),
    _r(W, r"(?:^|/)control/(?:safebrowsing|parental|safesearch)/disable$", "system", doc=ADGUARD),
    _r(W, r"(?:^|/)control/safesearch/settings$", "system", doc=ADGUARD, body=_OFF),
)

# Frigate: docs.frigate.video "HTTP API" (the page could only be read as a list of sections; the App group holds restart and config).
FRIGATE = "https://docs.frigate.video/integrations/api/frigate-http-api/"
_FRIGATE = (
    _r(W, r"(?:^|/)api/(?:restart|config/save|config/set)" + E, "system", doc=FRIGATE, sure=False),
    _r(W, r"(?:^|/)api/go2rtc/streams" + E, "code", doc=FRIGATE, sure=False),      # a go2rtc stream source can be "exec:<command>"
)

# Paperless-ngx: /api/token/ is in docs/api.md (docs.paperless-ngx.com/api, confirmed); the other resources are the ones its API lists
# (the docs site answered 403, so those paths are conservative prefixes).
PAPERLESS = "https://docs.paperless-ngx.com/api/"
_PAPERLESS = (
    _r(ANY, r"(?:^|/)api/token" + E, "takeover", "never", doc=PAPERLESS),
    _r(ANY, r"(?:^|/)api/profile/generate_auth_token" + E, "takeover", "never", doc=PAPERLESS, sure=False),
    _r(W, r"(?:^|/)api/(?:mail_accounts|users|groups)" + E, "takeover", doc=PAPERLESS, sure=False),
    _r(W, r"(?:^|/)api/(?:workflows|config)" + E, "code", doc=PAPERLESS, sure=False),
)

# Firefly III: api-docs.firefly-iii.org (403 to the fetch tool; paths are the API's: /api/v1/data/destroy, /data/purge, /users, ...).
FIREFLY = "https://api-docs.firefly-iii.org/"
_FIREFLY = (
    *_mint(r"(?:^|/)oauth/(?:personal-access-tokens|clients)" + E, doc=FIREFLY, sure=False),
    _r(W, r"(?:^|/)api/v1/data/(?:destroy|purge)" + E, "data", doc=FIREFLY, sure=False),
    _r(W, r"(?:^|/)api/v1/(?:users|configuration)" + E, "takeover", doc=FIREFLY, sure=False),
    _r(W, r"(?:^|/)api/v1/webhooks" + E, "system", doc=FIREFLY, sure=False),
)

# Mealie: the app's own /docs (OpenAPI); mealie.io's API page names no admin paths, so /api/admin/ is the conservative prefix.
MEALIE = "https://docs.mealie.io/documentation/getting-started/api-usage/"
_MEALIE = (
    *_mint(r"(?:^|/)api/users/api-tokens" + E, doc=MEALIE, sure=False),
    _r(W, r"(?:^|/)api/admin(?:/|$)", "system", doc=MEALIE, sure=False),
)

# Immich: immich-openapi-specs.json (raw.githubusercontent.com/immich-app/immich/main/open-api/): /admin/* and /api-keys* read there;
# /system-config and /jobs are the long-standing resources of the same API (not in the part of the file that could be read).
IMMICH = "https://api.immich.app/endpoints"
_IMMICH = (
    *_mint(r"(?:^|/)api/api-keys" + E, doc=IMMICH),
    _r(W, r"(?:^|/)api/admin(?:/|$)", "system", doc=IMMICH),
    _r(W, r"(?:^|/)api/(?:system-config|jobs)" + E, "system", doc=IMMICH, sure=False),
)

# Datadog: docs.datadoghq.com/api/latest/key-management/ (paths confirmed there); users and roles: /api/v2/users, /api/v2/roles.
DATADOG = "https://docs.datadoghq.com/api/latest/key-management/"
_DATADOG = (
    *_mint(r"(?:^|/)api/v[12]/(?:api_keys?|application_keys?|current_user/application_keys)" + E, doc=DATADOG),
    _r(W, r"(?:^|/)api/v[12]/(?:users?|roles|user_invitations)" + E, "takeover", doc="https://docs.datadoghq.com/api/latest/users/"),
)

# Slack: method names read on docs.slack.dev/reference/methods (admin.*, apps.uninstall, auth.revoke, oauth.v2.access,
# tooling.tokens.rotate). Slack takes GET and POST for every method.
SLACK = "https://docs.slack.dev/reference/methods"
_SLACK = (
    _r(ANY, r"(?:^|/)api/(?:oauth(?:\.v2)?\.|tooling\.tokens\.rotate)", "takeover", "never", doc=SLACK),
    _r(ANY, r"(?:^|/)api/admin\.", "takeover", doc=SLACK),
    _r(ANY, r"(?:^|/)api/apps\.(?:uninstall|permissions|manifest)", "system", doc=SLACK),
    _r(ANY, r"(?:^|/)api/auth\.revoke$", "takeover", doc=SLACK),
)

# Miniflux: miniflux.app/docs/api.html (users, api-keys, flush-history confirmed).
MINIFLUX = "https://miniflux.app/docs/api.html"
_MINIFLUX = (
    *_mint(r"(?:^|/)v1/api-keys" + E, doc=MINIFLUX),
    _r(W, r"(?:^|/)v1/users" + E, "takeover", doc=MINIFLUX),
    _r(W, r"(?:^|/)v1/flush-history$", "data", doc=MINIFLUX),
)

# Linkwarden (docs.linkwarden.app/api: sections Tokens, Users; the page shows no paths), Karakeep (docs.karakeep.com/api: Admin section),
# Wallabag (doc.wallabag.org: OAuth at /oauth/v2/token, clients in the developer area), FreshRSS (Google Reader API ClientLogin).
LINKWARDEN = "https://docs.linkwarden.app/api/api-introduction"
KARAKEEP = "https://docs.karakeep.com/api/karakeep-api"
WALLABAG = "https://doc.wallabag.org/developer/api/methods/"
FRESHRSS = "https://freshrss.github.io/FreshRSS/en/developers/06_Fever_API.html"
_LINKWARDEN = (
    *_mint(r"(?:^|/)api/v1/tokens" + E, doc=LINKWARDEN, sure=False),
    _r(W, r"(?:^|/)api/v1/users" + E, "takeover", doc=LINKWARDEN, sure=False),
)
_KARAKEEP = (
    *_mint(r"(?:^|/)api/v1/api-?keys" + E, doc=KARAKEEP, sure=False),
    _r(W, r"(?:^|/)api/v1/admin(?:/|$)", "takeover", doc=KARAKEEP),
)
_WALLABAG = (
    _r(ANY, r"(?:^|/)oauth/v2/token" + E, "takeover", "never", doc=WALLABAG, sure=False),
    _r(ANY, r"(?:^|/)(?:api|developer)/clients?" + E, "takeover", "never", doc=WALLABAG, sure=False),
    _r(W, r"(?:^|/)api/user" + E, "takeover", doc=WALLABAG, sure=False),
)
_FRESHRSS = (
    _r(ANY, r"(?:^|/)api/greader\.php/accounts/clientlogin" + E, "takeover", "never", doc=FRESHRSS, sure=False),
)

# Home Assistant: developers.home-assistant.io/docs/api/rest (POST /api/services/<domain>/<service>). Services that run code on the
# owner's machine are never called; the Supervisor (hassio: add-ons, backups, host reboot) and restarting or stopping Home Assistant
# itself always ask.
HOME_ASSISTANT = "https://developers.home-assistant.io/docs/api/rest/"
_HOME_ASSISTANT = (
    _r(ANY, r"(?:^|/)api/services/(?:shell_command|command_line|python_script|pyscript|rest_command)" + E, "code", "never", doc=HOME_ASSISTANT),
    _r(ANY, r"(?:^|/)api/(?:hassio|hassio_ingress)" + E, "system", doc=HOME_ASSISTANT),
    _r(ANY, r"(?:^|/)api/services/hassio" + E, "system", doc=HOME_ASSISTANT),
    _r(ANY, r"(?:^|/)api/services/homeassistant/(?:restart|stop)" + E, "system", doc=HOME_ASSISTANT),
)

RULES: dict[str, tuple[Rule, ...]] = {
    "telegram": _TELEGRAM, "github": _GITHUB, "gitea": _GITEA, "n8n": _N8N, "nextcloud": _NEXTCLOUD,
    "shopify": _SHOPIFY, "pihole": _PIHOLE, "adguard_home": _ADGUARD, "frigate": _FRIGATE, "paperless": _PAPERLESS,
    "firefly_iii": _FIREFLY, "mealie": _MEALIE, "immich": _IMMICH, "datadog": _DATADOG, "slack": _SLACK, "miniflux": _MINIFLUX,
    "linkwarden": _LINKWARDEN, "karakeep": _KARAKEEP, "wallabag": _WALLABAG, "freshrss": _FRESHRSS, "home_assistant": _HOME_ASSISTANT,
}
# Gitea's docs differ from Forgejo's only in where they are
RULES["forgejo"] = tuple(Rule(r.methods, r.path, r.why, r.mode, r.body, FORGEJO if r.doc == GITEA else r.doc, r.sure) for r in _GITEA)


# ---------------------------------------------------------------------------------------------------------------------------------
def normalised_path(url: str) -> str:
    """The path of a full URL, else the text before "?" and "#": percent-decoded (again where it was escaped twice), lower-cased, "\\" as
    "/", "." and ".." segments and ;parameters resolved, runs of "/" as one and no trailing "/". "" for the root."""
    raw = re.sub(r"[\t\r\n]", "", str(url or "").strip())
    raw = urlsplit(raw).path if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I) else raw.split("?", 1)[0].split("#", 1)[0]
    for _ in range(3):
        again = unquote(raw)
        if again == raw:
            break
        raw = again
    out: list[str] = []
    for seg in raw.lower().replace("\\", "/").split("/"):
        seg = seg.split(";", 1)[0]
        if seg in ("", "."):
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    return "/" + "/".join(out) if out else ""


def _body_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(body)


def match(preset_id: str, method: str, url: str, body: Any = None) -> Rule | None:
    """The rule a call falls under ("never" before "ask"), None when the connector has none or the call is fine."""
    rules = RULES.get(preset_id)
    if not rules:
        return None
    method, path, text = str(method or "GET").upper(), normalised_path(url), _body_text(body)
    found = [r for r in rules if ("*" in r.methods or method in r.methods) and r.path.search(path)
             and (r.body is None or (text and r.body.search(text)))]
    return next((r for r in found if r.mode == "never"), found[0] if found else None)


def refusal(preset_id: str, method: str, url: str, body: Any = None) -> str:
    """"" or the error for a call Kelo never makes (the "never" rules; an "ask" rule is the gate's business)."""
    rule = match(preset_id, method, url, body)
    return Hit(preset_id, rule).refusal if rule is not None and rule.mode == "never" else ""


def preset_of(item: str) -> str | None:
    """The preset (catalog connector id) of a vault item built into the server; None for anything else (an installed package, a
    connection the model registered itself): those have no rules."""
    from .effects import _item_ids
    return _item_ids().get((item or "").strip().casefold())


def classify(tool: Any, args: dict[str, Any] | None) -> Hit | None:
    """The rule a connector_get / connector_download / connector_send call falls under, else None."""
    name = str(getattr(tool, "name", tool) or "")
    if name not in ("connector_get", "connector_download", "connector_send") or not isinstance(args, dict):
        return None
    preset = preset_of(str(args.get("name") or ""))
    if preset is None or preset not in RULES:
        return None
    method = str(args.get("method") or "POST").upper() if name == "connector_send" else "GET"
    rule = match(preset, method, str(args.get("url") or ""), args.get("json") if name == "connector_send" else None)
    return Hit(preset, rule) if rule is not None else None


def service_name(preset_id: str) -> str:
    from .presets import KEYS
    if preset_id in KEYS:
        return KEYS[preset_id].name
    from .oauth_connect import PROVIDERS
    for p in PROVIDERS.values():
        if preset_id in p.serves:
            return p.name
    return preset_id


def table() -> list[tuple[str, Rule]]:
    """Every rule with its preset id, in a stable order (the doc is generated from it)."""
    return [(pid, r) for pid in sorted(RULES) for r in RULES[pid]]
