# Push notifications

Kelo wakes the phone app with Firebase Cloud Messaging (FCM). The Firebase project is the operator's own (its id is in the app's `apps/android/kelo/app/google-services.json`, which is public client configuration and part of the source; to build with your own Firebase project, replace that file, see [verify-builds.md](verify-builds.md)) and the Android package is
`chat.kelo.app`. Only the operator's server (the broker, the same one that brokers sign-ins and Kelo addresses) holds the
Firebase service-account key. Every other copy sends its pushes through that broker.

A push carries **ids only**. It never carries a title, body or any words the assistant wrote:

```json
{"type": "task_done", "id": "42", "task_id": "17"}
```

`type` is one of `task_done`, `task_failed`, `approval_waiting`, `payment_auto`, `schedule_disabled`, `schedule_paused`,
`watch_fired`, `watch_disabled` or `proactive`. The other keys are
`approval_id` and `schedule_id`, sent only when they apply. Every value is a string. When the app wakes up, it asks its own
server `GET /api/notifications` for the details and shows them. The broker learns only that something happened on some copy,
never what it was.

## When a push is sent

A copy sends a push for each notification nobody saw arrive:

- `task_done` / `task_failed`: a reply nobody followed to the end. This covers automatic runs, tasks handed over from the agent
  screen, and a chat the phone left.
- `approval_waiting`: an unattended run asks for approval.
- `payment_auto`: an automatic payment, unless the reply was being followed at that moment.
- `schedule_disabled`: an automatic run was switched off after it kept failing.
- `schedule_paused`: an automatic run paused its own job because something blocks it for good (the reason is in the body).
- `watch_fired`: a watcher's condition held; the agent's short message follows in the chat.
- `watch_disabled`: a watcher kept failing and switched itself off.
- `proactive`: a message the agent sent without being asked (only when the user turned proactive messages on).

Each push goes to every phone paired with that copy that registered a token. A device removed from the app gets no more pushes.

## Rechecks

Kelo looks again, by itself, at what it told you that may change and that you may act on (rules, prices, availability, the result of a
review, opening times). When a check finds a change that two independent sources (or one official source) support, and Kelo has
confirmed it a second time itself, the correction arrives as an ordinary Kelo message in the chat and a normal `task_done` push
(ids only, as above), in your waking hours only. A check that finds nothing, or finds too little to be sure, sends nothing.
`GET /api/rechecks?days=` lists what is being checked; `PUT /api/proactive` takes `rechecks` (on/off) and `recheck_per_day`
(research runs a day, default 4). The design is in `server/src/assistant/recheck.py`.

## Offers

Kelo keeps up to 6 ready-to-run cards: one sentence, what it still needs from you, a start button and a dismiss button. A task that
paused for your answer (a question in the chat, a login, a fill-in card) is a `resume` card, made without a model from the open
follow-ups. The nightly look-back adds at most 3 `wish`, `routine` or `deadline` cards from your own words (it quotes them and never
states a price or number: it cannot look anything up), and during a chat Kelo may post a `chat` card with `offer_post`; only that card
may carry numbers, and then only with sources that pass the recheck rule, and it is dropped after 7 days. Order: resume, deadline (a
date within 7 days), chat (today), wish, routine; newer first. Cards you keep ignoring move down (after 5 views) and are retired at 12
views; a dismissed kind and topic go down 2 places for 30 days, and three dismissals of one kind pause it for 14 days; "not now" hides
a card for 3 days with no mark against it.

`GET /api/offers` lists the cards (counted as seen once per local day), `POST /api/offers/{id}/start` sends the card's request as your
message (409 while Kelo is answering) and gives the model its prepared steps as a hidden note that is never stored or shown, and
`POST /api/offers/{id}/dismiss {"reason"?: "not_interested"|"done_already"|"not_now"}` removes it. `PUT /api/proactive` takes `offers`
(on/off, on by default). The design is in `server/src/assistant/offers.py`.

## For the operator

1. **Download the key.** Open Firebase console → your project → Project settings → Service accounts. Click
   **Generate new private key** to download a JSON file.
2. **Upload it with any signed-in device or the access key.** Use the app's operator settings, or run curl on the operator's computer:

   ```
   curl -X PUT https://<operator address>/api/operator/firebase \
        -H "Authorization: Bearer $ASSISTANT_TOKEN" -H 'content-type: application/json' \
        --data-binary @key.json
   ```

   **Never paste the key into a chat**, the assistant's chat included. The server checks the fields (`type` must be
   `service_account`, plus `project_id`, `client_email` and `private_key`). It then mints one OAuth token to prove the key works
   and only then stores the whole file encrypted. No message is sent during this check. The answer is
   `{"enabled": true, "project_id", "client_email"}`. The key is never returned.
3. **Check or turn off.** `GET /api/operator/firebase` returns `{"enabled", "project_id", "client_email"}`.
   `DELETE /api/operator/firebase` removes the key, and push is then off for every copy.
4. **Delete the downloaded file** from the computer you downloaded it to, once the upload worked.

The operator's own copy sends straight to FCM, with no HTTP hop to itself.

## iPhone (direct APNs, no SDK in the app)

The iPhone app registers its own APNs device token (hex) with `platform: "ios-apns"` and `"environment": "production"` (TestFlight,
App Store) or `"development"` (an Xcode run). The operator's server signs with its APNs key and posts HTTP/2 to `api.push.apple.com`
(or `api.sandbox.push.apple.com`). The payload is a fixed, localized alert (`loc-key` `kelo_push_update`), `content-available: 1` and the
same ids as above, nothing else. The operator sets `KELO_APNS_KEY_FILE` (path of the .p8, never stored in the database),
`KELO_APNS_KEY_ID`, `KELO_APNS_TEAM_ID` and optionally `KELO_APNS_TOPIC` (default `chat.kelo.app`); without them iPhone pushes are skipped
quietly. The broker takes `"apns": [{"token", "environment"}]` next to `"tokens"` and answers `"apns_results"` (same shape; `error` is Apple's
reason, `DISABLED` where the broker has no APNs key). A 410 or `BadDeviceToken` marks the token invalid and the copy drops it. See apps/ios/README.md.

## Which broker a copy uses

A copy uses the first of these that is set: `ASSISTANT_PUSH_BROKER`, `ASSISTANT_KELO_BROKER`, `ASSISTANT_OAUTH_BROKER`; if none is
set, the operator `https://kelo.chat` (the same default the sign-in broker has, settings.py `_broker`). Set it to `off` for no operator
push; then, without a Firebase key of its own, push is off and the endpoints answer `"push": false`.

## Phone side (each copy, device token)

| Call | Body | Answer |
|---|---|---|
| `POST /api/push/register` | `{"token": "<FCM token>", "platform": "android"}` or `{"token": "<APNs token, hex>", "platform": "ios-apns", "environment": "production"\|"development"}` | `{"ok": true, "push": bool}`<br>`403` with the master token (not a phone)<br>`422` bad token or platform |
| `DELETE /api/push/register` | | `{"ok": true, "removed": bool, "push": bool}` |
| `GET /api/push/status` | | `{"enabled", "registered", "mode": local / broker / off}`, plus `"broker_reachable"` and `"broker_push"` in mode `broker` |

- Each device has one token. Registering a new one replaces the old one.
- Register on every app start and from `onNewToken`. A token that FCM reports as no longer valid is dropped on the next push, and
  registering again brings it back.
- `DELETE /api/devices/{id}` also drops that device's token.

## Broker side (operator's server, public)

| Call | Body | Answer |
|---|---|---|
| `POST /broker/push/send` | `{"install_id", "tokens": [≤ 10], "apns"?: [≤ 10 {"token", "environment"}], "data": {…ids as above}}` | `200 {"results": [{"ok", "invalid", "error"?}], "apns_results"?: […]}`, in token order<br>`404` push off<br>`403` bad install_id<br>`422` bad body (other keys in data, more than 10 tokens…)<br>`429` rate limit, with `Retry-After`<br>`502` Google refused the key |
| `GET /broker/push/status` | | `{"push": bool, "apns": bool}` |

- **install_id:** each copy makes its own random secret (32–128 characters of `[A-Za-z0-9_-]`) and keeps it encrypted in its
  database. The broker stores only its SHA-256 and registers it on first use.
- **Limits:** 120 requests an hour per copy, 600 an hour per address, 20 new copies a day per address, and 10 tokens per request.
- **Retries:** the copy sends tokens in batches of 10. It retries a busy or unreachable broker after 2 s, 10 s and 30 s, and
  honours `Retry-After`. It drops the tokens reported `invalid`.

**FCM call** (HTTP v1, checked against the Firebase reference in 2026-11). The OAuth scope is
`https://www.googleapis.com/auth/firebase.messaging`, and the token is cached for about 55 minutes.

```
POST https://fcm.googleapis.com/v1/projects/{project_id}/messages:send
{"message": {"token": "<FCM token>", "data": {...strings...}, "android": {"priority": "HIGH"}}}
```

How the broker handles FCM's answers:

- `UNREGISTERED` (404) and `INVALID_ARGUMENT` (400) are reported as `invalid: true`.
- `UNAVAILABLE` (503) and `INTERNAL` (500) are retried twice, with backoff.
- `QUOTA_EXCEEDED` (429) is not retried here.
- A `401` mints a new OAuth token once and tries again.

Tokens and payloads never reach a log line. Only the notification type and counts are logged.
