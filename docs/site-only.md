# Site-only copy and the operator link

A **site-only copy** is an extra copy of Kelo that does one job: it serves the kelo.chat site and the services the other copies lean
on (sign-in brokers, push relay, Kelo addresses, reports, Google sign-in beta requests, the tester sign-ups, app downloads). It runs
no assistant of its own: no model, browser, work room, schedules, phone bridge or connector. The **main copy** (your own Kelo, the one
your apps hold) reads and answers the site's operator lists through a link, and hears about the site's events.

## What a site-only copy runs

Set with `ASSISTANT_SITE_ONLY=1` in the copy's settings (`instances/<name>/env`), or with `assistant instance site-only <name> on`.
Nothing is deleted by it: volumes, keys and data stay, and `assistant instance site-only <name> off` gives the whole copy back at its
next start.

- Only the server and the copy's tunnel are started (`docker compose up --no-deps server <tunnel>`). The browser, egress, VPN, keyring,
  work room, CLI, Android and updater containers are removed (containers only, never their volumes).
- The server answers only the routes in `server/src/assistant/site_only.py`. Every other route answers `404 {"code": "site_only"}`,
  and every websocket closes with code 4404. A route added later is refused on a site copy until it is listed there on purpose.
- Its settings are forced off whatever the env says: scheduler, browser, keyring, Android bridge, updater, auto-update, notices feed,
  VPN and work-room folders, and the worker count.
- The operator is the site copy by default (`ASSISTANT_OPERATOR` is taken as `1` unless it says `0`).

The routes it keeps, by group: the site pages and install scripts; app and server downloads and their feeds; `/healthz`; the Kelo
address, push, report and beta brokers (`/broker/*`); the Google and GitHub sign-in brokers and the shared return page
(`/oauth/*`); `/api/about` and `/api/operator/*` (access key, or the operator link key for the operator lists only); and, with the
access key only (from the server's own machine), `/api/oauth-apps`, `/api/mail/accounts`, `/api/mail/providers` and
`/api/google/web/start`.

## The operator link

The link lets the main copy read and change the site's operator lists (`/api/operator/*`, except Firebase) and get its events.

- `assistant operator link <site copy> [--url https://kelo.chat]` makes a random key of 256 bits and never prints it. The site copy
  keeps only the SHA-256 of the key (`KELO_OPERATOR_LINK_SHA256`, in its settings). The main copy keeps the key itself in its `.env`
  (`KELO_OPERATOR_SITE_KEY`, next to `ASSISTANT_TOKEN`) with the site's address (`KELO_OPERATOR_SITE_URL`). Restart both copies after it.
- The site accepts the key only on `/api/operator/*` (`api/auth.py`, `require_operator`). It is not an access key for anything else.
- `assistant operator status` asks the site whether the key is accepted; the key is not shown.
- `assistant operator unlink <site copy>` removes the three lines from both sides. The lists are then the main copy's own again.

On the main copy, with the link set:

- `/api/operator/status`, `beta`, `reports`, `cloudflare` are answered by the site copy with its own status and JSON; the apps need no
  change. Firebase stays local: the main copy keeps its own key for its phones' pushes.
- An unreachable site, or a refused key, answers `502` with `{"code": "operator_site_down" | "operator_site_key", "message": ...}`.
- Only fixed paths with integer ids are ever forwarded, and the query values are checked on the main copy first (no open proxy).

## The events feed

On every server the operator's events are kept for 30 days in `operator_events` (`operator_events.py`): a kelo.chat/tester sign-up, a
Google sign-in beta request, a report, a Kelo address pool that fills up, or a busy pool. Each keeps only what its notice needs (no
address of a visitor). `GET /api/operator/events?after=<id>&limit=<1..500>` returns those with a bigger id, oldest first.

The main copy polls the feed once a minute, while it is linked (`operator_site.py`, `OperatorSiteWatch`). Each event becomes one
notification on the main copy, in its owner's language (`operator_notices.py` renders the words from the event's data), and the phones
are woken as for any notification. The cursor is saved after each one, so a restart never repeats a notice, and a main copy that was down
catches up. The first run only sets the cursor, so the site's older sign-ups are not announced.

## Setting up

1. Pick the extra copy that serves the site (its `ASSISTANT_PUBLIC_URL` is `https://kelo.chat`) and make it site-only:
   `assistant instance site-only operator on`.
2. Give it the sign-in app and push keys it has always had (the Google app, Firebase and APNs on that copy).
3. `assistant operator link operator --url https://kelo.chat`.
4. Restart: `assistant instance start operator`, then `docker compose up -d --no-deps --no-build server` on the main copy.
5. Check: `assistant operator status` and `curl -s -o /dev/null -w '%{http_code}' https://kelo.chat/api/chat -X POST` (404).

To undo: `assistant operator unlink operator`, `assistant instance site-only operator off`, `assistant instance start operator`, and
restart the main server. The tables and settings the feature adds are additive and harmless to older code.
