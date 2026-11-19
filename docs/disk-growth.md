# Disk growth audit (2026-11-16)

Owner request: check every database table for a retention rule, check whether the browser profile cache grows without limit, and add limits
where one is missing. Rule for every limit: remove oldest first, by age or size, and never the owner's own files or memory.

Already in place before this audit (STATE.md 11-16): container logs (3 x 10 MB), update backups (newest 2), memory pass backups (newest 3),
older-layout backup folder (newest 1), rollback tags, old release images (removed by exact tag), storage warnings (80% / 95% of the cap,
host free under 1 GB), the cleanup screen, the 507 `disk_full` answer.

New in this change: `server/src/assistant/disk_limits.py` (class `DiskLimits`, run every 6 hours by the scheduler, job `disk_limits`), the
default browser cache cap, the browser profile cache sweep in `docker/entrypoint.sh`. Tests: `tests/test_disk_limits.py`.

## What is never limited (the owner's own things)

`messages` and the files sent with them (`message_attachments`, `chat_images` and the folder `chat_images/`), `memory_claims` (also the
superseded ones: they are the memory's history), notes, memory, people pages, `.history`, uploads, the library (`files/`, 30-day trash
by the owner's own delete), `journal`, `goals` and their rows, `wallet_payments` and `wallet_cards` (payment history, cards), `watchers` (what the owner asked to watch; their `watch_events` are capped), the vault (`credentials`, `entries`),
`profile`, `devices`, `schedules`, `schedule_grants`, `recipient_allows`, `learned_rules`, `shipping_profiles`, `payment_pins`,
`phone_*` data the phone sent. They grow only with what the owner did and the owner removes them (chat clear, cleanup screen, delete).
The storage warning and the cleanup screen cover their size.

## Tables

| Place | Grows by | Limit today | Fix in this change |
|---|---|---|---|
| `usage` | one row per day | none (a few bytes a day, the lifetime total is a feature) | none needed |
| `usage_events` | one row per model call | 400 days and newest 200 000 (`store.prune_usage_events`) | none |
| `tasks`, `approval_log`, `notifications` | per run / approval / notice | newest 5000 / 3000 / 1000 (`activity.py`) | none |
| `trail_labels` | per tool result | 7 days, and with the run | none |
| `turn_log`, `turn_events` | per reply | 24 hours | none |
| `actions` (done-once) | per action | 30 days | none |
| `injection_events`, `injection_notices` | per detected injection | 90 days / 3 days | none |
| `proactive_log` | per looked-at item | newest 4000; source `situation` rows older than 90 days (`SituationResearch.cleanup`) | yes (both rules) |
| `proactive_held` | per item kept for a later message | until its own deadline (`expire_held`), sent or dropped at once | none |
| `help_items` | per proposal of the hourly situation research (situation.py) | told, dropped and expired rows older than 90 days (`SituationResearch.cleanup`, daily 04:35); prepared and selected rows expire first (48 h) | yes |
| `help_events` | per status change of a help item (situation.py) | older than 90 days, and rows whose item is gone (`SituationResearch.cleanup`) | yes |
| `situation_passes` | one row per situation pass | older than 90 days, never a running row (`SituationResearch.cleanup`); the count rule `KEEP_PASSES` is gone | yes |
| `checklist_links` | one row per checklist item: how it is looked after (its own job, or the send times) | an item taken off the list loses its row (`Checklist.reconcile`); at most MAX_ITEMS (30) rows | yes |
| `meta` (`situation:source:<sid>` snapshots) | one hash per declared source (fingerprint, ids, times; no content) | removed when its `read_at` is older than 90 days (a connector removed long ago) | yes |
| `followups` | per follow-up | closed ones 30 days | none |
| `offers` | per card | closed ones 60 days | none |
| `briefings` | per day | 120 days | none |
| `schedule_runs` | per run | 100 per schedule | none |
| `watch_events` | per check | 50 per watcher | none |
| `goal_log` | per goal event | 300 per goal | none |
| `vault_log` | per vault use | newest `LOG_KEEP` | none |
| `workers`, `worker_messages`, `product_results` | per background job | purged with the job | none |
| `secret_requests` | per request | open 1 day, answered 7 days | none |
| `place_cache` | per looked-up place | newest `CACHE_MAX` | none |
| `kelo_claims`, `kelo_names` | per name claim | 1 day / idle 30 days | none |
| `tester_signups`, `beta_requests`, `operator_reports` | per request | 180 / 30-180 / 365 days | none |
| `operator_events` | per site event (sign-up, beta request, report, Kelo address pool notice) | 30 days (`operator_events.py`) | none |
| `phone_notes`, `phone_tasks` | per phone sync | 7 days (tasks), age and count (notes) | none |
| `follow_readings` | one per check run (every 10 minutes in a window) | NONE (the unbounded one) | older than 30 days, and readings of a check that is gone |
| `follow_checks` | per planned check | NONE | finished or stopped and window ended 30 days ago; checks of an item that is gone |
| `follow_items` | per trip / appointment | NONE | ended more than 60 days ago |
| `follow_usage` | one row per source and month | NONE | older than 24 months |
| `follow_removed` | one per item the owner removed from the list | NONE | start more than 60 days ago (`disk_limits.py`) |
| `memory_runs` | one row per memory pass | NONE | newest 500 (claims untouched) |
| `forget_plans` | one row per forget request | removed at confirm; open plans after `PLAN_TTL` (forget.py) | none needed |
| `forget_tombstones` | one row per forgotten topic (owner action) | never: the owner removes one with "allow again" | none needed (tiny, owner-made) |
| `order_preapprovals` | one row per pre-approved order (owner card) | NONE | none needed: tiny, a payment record (kept like the other payment tables) |
| `schedule_pay_allows` | one row per schedule payment allow | removed with its schedule or by the owner | none needed |
| `browser_sign_ins` | one row per site Kelo's browser is signed in to (sites and times only) | removed by the owner's sign-out or the profile's delete | none needed: one row per site |
| `browser_profiles` | one row per named browser profile (made only when the owner asks) | removed by the owner (chat card or Keychain) | none needed: a few rows |
| `browser_profile_units` | one sealed row per profile and site (cookies and storage of that site) | removed with its profile or the owner's sign-out | none needed: one row per profile and site |
| `rechecks` | per recheck (no new rows since 2026-11-19; the service is retired) | NONE | closed / stopped / sent and older than 180 days; open ones stay |
| `kept_corrections` | per owner correction | NONE | resolved and older than 365 days; open ones stay |
| `review_days` | one row per night | NONE | older than 400 days |
| `order_terms`, `pay_approvals`, `pay_attempts` | per order | NONE | never pruned: the owner's payment record (a few hundred bytes per order) |
| `buy_plans` | per site and product | only failed plans | not used for 180 days |
| `buy_ready` | per watcher | removed with the watcher by hand | rows whose watcher is gone |
| `payment_paths` | per site and way of paying | NONE | not seen for 365 days |
| `message_replies`, `message_comparisons` | per chat card | chat clear | rows whose message is gone |
| `messages_fts` | per chat message (index only, no second copy of the text) | follows `messages`: a deleted or edited message leaves the index in the same write (triggers); chat clear | none (grows with `messages`) |
| `resume_offers` | per resumable task | chat clear | rows whose task is gone |
| `notices_read`, `push_installs`, `push_tokens`, `access_codes`, `pairings` | per notice / install / phone | tiny and one row per device; `pairings` expire | none (a deleted `notices_read` row would show a notice as new again) |
| Owner settings and small config tables: `agent_avatar`, `api_keys`, `approval_rules`, `beta_installs`, `conversation_summary`, `ha_watches`, `meta`, `report_installs`, `schedule_rules`, `goal_docs`, `goal_facts`, `goal_steps`, `goal_metrics`, `goal_metric_values`, `goal_suggestions`, `journal_days`, `wallet_limits`, `wallet_sites`, `phone_calendar`, `phone_data` | one row per thing the owner set up | the owner's own records, one row per setting, goal or day | none |
| `memory_claims_fts` | with `memory_claims` | follows its table | none |
| the SQLite file (`assistant.db`) | never shrinks by itself: deleted rows leave empty pages | NONE | `reclaim()`: VACUUM when 64 MB and 30% of the file is empty, at most once a week (foreign keys are off in this database, so every parent/child delete is explicit) |

## Files and containers

| Place | Grows by | Limit today | Fix in this change |
|---|---|---|---|
| Browser profile, page cache (`browser-data:/data/profile`, Docker) | every page the browser opens | `BROWSER_CACHE_MB`, and 0 / unset meant "Chrome's own default", which grows with the free disk | 0 / unset now means 1024 MB (media cache a quarter of it); the same default for the native host (`hosting/browser_arguments.py`); the limits screen says "browser default (1 GB)" |
| Browser profile, other caches (`Code Cache`, `GPUCache`, `DawnCache`, shader caches, `component_crx_cache`; service-worker storage is left alone) | every site that runs scripts | NONE (the page cache flag does not cover them) | emptied at every browser start; cookies, logins and site storage stay |
| Browser profile, crash reports (`Crashpad`) and `/tmp` leftovers of the browser | per crash / start | NONE | removed after one day, checked every 30 minutes; reports also at start |
| Browser downloads (`/downloads`) | per download | 1 day (loop in entrypoint.sh) | none |
| `<state>/browser-history/` (step pictures of unattended runs) | per browser step of a task or schedule | per tab 300 frames / 60 MB, 14 days, purged only when a run wrote | purge also runs on its own now; and the whole folder is capped at 1 GB, whole tabs oldest first, never one written in the last hour |
| Work room scratch (`workroom-scratch:/work`) | per job | emptied before and after every job; one job 2 GB (`WORKROOM_DISK`) | none |
| Work room tools (`workroom-tools:/tools`) | packages the owner approved | the owner's choice; counted in the job cap | none (not removed on its own: the owner installed them) |
| Update backups `/state/.backups` | per update | newest 2 | none |
| Memory pass backups `.memory-passes` | per pass | newest 3 | none |
| `.backup` (older layouts) | once | newest 1 | none |
| Container logs | per line | 3 x 10 MB | none |
| Released images, rollback tags | per update | exact-tag removal, newest rollback point kept | none |
| Generated images (`.tryon/`) and chat pictures | per image the agent made | owner removes (cleanup screen, chat clear) | none: they are the owner's results |
| Uploads, library, trash | the owner's files | uploads: owner only (old ones offered on the cleanup screen); library trash 30 days | none |
| Recall index | rebuilt in memory (`:memory:`) | not on disk | none |

## Decisions

- Time-based rules only count dates, states and links between tables. No rule looks at the words of a record.
- Finished payment records are kept for two years because they are the owner's evidence; an unsettled one is never removed.
- The follow-through readings are only the evidence for a message the owner already got; 30 days is far longer than any use of them.
- A job that fails does not stop the others and never stops the server (`DiskLimits.run` reports each part).
- The limits screen and the apps need no change: the browser cache line already reads the setting, only its default text changed.

The work browser (`work-browser` container) keeps no profile between starts: its profile folder is emptied at every start
(`BROWSER_EPHEMERAL=1`), so its volume only holds what a running job wrote. Its downloads share the browser downloads folder, as before.

## Tests (`tests/test_disk_limits.py`)

Rules on an empty database and with a missing table; follow-through age and parent/child removal; memory pass log keeps the newest and the
claims are untouched; open rechecks stay; unknown and pressed payment attempts never go and referenced terms stay; rows hanging on removed
messages go while chat stays; no rule names an owner table or lacks a WHERE; the database file shrinks (forced) and is not shrunk twice;
browser history cap removes oldest whole tabs and spares a busy one; one failing part does not stop the run. Also
`test_disk_limits.py::test_dockers_browser_caps_its_cache_by_default`, `test_limits.py`, `test_profiles.py`.
