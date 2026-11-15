# Keyguard: the key of passwords, keys and cards on your phones

Server side of `docs/security/e2e-and-phone-key.md` section 2 (and 5.9, 5.14, 5.15). Code: `src/assistant/keyguard.py`
(`KeyGuard`, the only code that reads `master.key` or `ASSISTANT_SECRET_KEY`), `src/assistant/api/keyguard.py` (the API, contract in
its docstring), `Store` (takes its sealers from the KeyGuard at each use).

## Scope (owner's decision, 2026-11-10)

Only secrets: the vault (website logins, service keys, connector tokens), the AI providers' API keys and the auto-pay cards.
Chats, memory, files and what the phones share (calendar, contacts, notifications...) are not encrypted under Keyguard; personal
data gets its own key on the server (`<state>/data.key`) when Keyguard is turned on, so it stays readable while the server is
locked. The apps' enable screen says only "passwords, keys and cards" (never chats, memory or files).

## Flow

1. Enable (any signed-in phone, over KE2E): `POST /api/keyguard/enroll` gives a NEW key and a recovery code (shown once). The phone wraps
   the key with its hardware key (Android Keystore AES-GCM, blob `0x01` ‖ IV ‖ ct; iPhone Secure Enclave ECIES, blob `0x02` ‖ ct;
   AAD `"kelo-keyguard v1"`; unlock condition biometrics or device passcode, 5.9) and sends the blob:
   `POST /api/keyguard/enroll/finish`. Only then the server re-seals every secret with the new key in one transaction, stores the
   blob, the recovery wrap (Argon2id 64 MiB, 3 passes, AES-256-GCM) and the check value, and shreds `master.key`.
2. After a restart the server is locked: secrets answer `423 {"code": "locked"}`, `/api/agent/status` has `"locked": true`, and a
   `keyguard_locked` notification (urgent) goes to the phones. The app asks for biometrics, fetches its blob
   (`GET /api/keyguard/blob`), unwraps it and sends the key (`POST /api/keyguard/unlock`). The key stays in memory only.
3. More phones enroll while unlocked (same two calls; no new key, no recovery code). A phone's copy is dropped with
   `DELETE /api/keyguard/phones/{id}` (never the last one). `POST /api/keyguard/disable` writes `master.key` back.
4. All phones lost: `assistant keyguard recover` on the server machine (or the app's recovery screen over KE2E) with the code.

Refused while `ASSISTANT_SECRET_KEY` is set in `.env` (`409 {"code": "env_key"}`): `assistant keyguard enable` on the server machine
moves it out (the running server writes the same key into `master.key` via `POST /api/keyguard/prepare`, `.env` is backed up 0600
and the line removed, the server restarted; the key is never printed), then the phone turns Keyguard on. Delete the `.env` backup
afterwards. Backups made after enabling hold no key; older backups need the old key (or `master.key` in them).

Removing a device (`DELETE /api/devices/{id}`) drops its copy; the last copy while Keyguard is on is refused (409
`last_keyguard_phone`). While locked, a chat reply that needs a provider key ends with "Kelo is locked: open it on your phone to
unlock it, then send again." (20 languages), and a scheduled run waits (up to 12 hours) for the unlock and then says in its report
that it started late because Kelo was locked.

## Background work while locked (one rule)

- Internal jobs (`Scheduler.add_every` / `add_daily`) need a key unless added with `needs_secrets=False` (housekeeping: schedule
  archive, library trash, storage watch, server updates). While locked (`Scheduler.paused`) they are not run and not marked done,
  so each runs once at the first tick after the unlock if it is still due (briefing, proactive pass, follow-ups, watchers, checklist,
  memory upkeep, nightly journal and look-back, worker limits, model wait). One log line per locked stretch, nothing secret.
- A job that still meets the lock ends quietly (`keyguard.Locked` caught in `schedules._quiet`).
- Work cut off by the restart (background jobs, background turns) is marked at start and resumed after the unlock
  (`KeyGuard.after_unlock`); the scheduler is poked then. The morning's first app open does not start the briefing while locked.
- Scheduled runs the user set up wait for the unlock and say so in their report (see above).
- Kelo's own working keys in `api_keys` (names with a colon: `push:install_id`, `reports:install_id`, `phone:note-digest`, the
  operator's `operator:*` / `oauth-app:*` tokens) are not the owner's passwords, keys or cards: they follow the personal data's
  key, so pushes (including the unlock push) and reports keep working while locked.

## Who may use it

Every signed-in device (there are no owner devices). The exception is a review copy (`ASSISTANT_REVIEW_MODE`): its devices get 403 on every
`/api/keyguard/*` route, so a reviewer cannot lock the review server; the access key (server machine) still can. The wallet works as in the
product (cards can be added); auto-pay simply cannot be turned on there because it needs Keyguard (409 `keyguard_required`).

## Auto-pay needs Keyguard (owner's rule, 2026-11-10)

- Turning auto-pay on (a new card with `auto_pay: true`, `PATCH` with `auto_pay: true`, or sending the number again for auto-pay)
  answers `409 {"code": "keyguard_required"}` while Keyguard is not on (mode "phone").
- An auto-pay card while Keyguard is off keeps its sealed copy but does not pay on its own: `ask_reason` / `Wallet.decide` reason
  `keyguard_off` ("Auto-pay needs Keyguard; this payment asks on the phone", 20 languages; the model gets the same in EXPLAIN), and
  the card shows `"auto_pay_paused_reason": "keyguard_off"`. `GET /api/wallet` has `"auto_pay_paused": n`. Turning Keyguard on
  resumes them as they were.
- Turning Keyguard off is allowed; its answer (and `GET /api/keyguard`) says how many auto-pay cards now wait: `"auto_pay_paused"`.
