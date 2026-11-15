# App store review servers

Store reviewers need a working Kelo server to try the apps. Each store gets its own copy on the operator's machine, so no
reviewer ever sees another store's chats or files:

| Store | Copy | Address |
|---|---|---|
| App Store (iPhone) | `review-apple` | `https://review-apple.kelo.chat` |
| Google Play (Android) | `review-google` | `https://review-google.kelo.chat` |
| Microsoft Store (Windows) | `review-ms` | `https://review-ms.kelo.chat` |
| TestFlight beta review (iPhone) | `review-testflight` | `https://review-testflight.kelo.chat` |

## What review mode changes

A review copy runs with `ASSISTANT_REVIEW_MODE=1` in its settings file (`instances/review-<store>/env`). It looks and works exactly
like the normal product: no banner, no demo shop, every feature on (chat, connectors, schedules, the browser, the wallet and
payments). Only this differs:

- **Subscription sign-ins are off for good.** The "unofficial" providers (Claude / Antigravity / ChatGPT / Qwen / MiniMax sign-ins)
  are not listed, cannot be chosen or signed in to, and any model call to one is refused, even if the settings file names one.
  `assistant enable oauth --instance review-<store>` refuses.
- **No operator settings.** Reviewers' phones join with an access code; on a review copy they never reach operator settings, Keyguard (so nobody can lock the review server); the wallet works as in the product (auto-pay needs Keyguard, so it stays off), and the copy never reports
  itself as the operator's server.
- **The model is set on the server.** The model from `assistant review model` is written into the profile at start-up, so a
  reviewer's phone shows it by name and never shows the "choose a model" card.

## Steps

Run these in the folder that holds `compose.yml`.

1. **Create the copy** (recommended size, review mode on, started):

   ```
   assistant review create apple        # or google, ms
   ```

   Add `--address` to also claim its kelo.chat address in the same step.

2. **Set its model** with an API key that has a spending cap. The key is asked for hidden (or read from `KELO_REVIEW_API_KEY`)
   and saved only in the copy's settings file; it is never printed:

   ```
   assistant review model apple --provider anthropic --model <model id>
   ```

   Only providers that take an API key are accepted. Running it again replaces the model and the key. In the app the model also
   shows under My → Model; a reviewer could switch it there, and `assistant review reset` puts the server's model back.

3. **Give it its address** (asks the kelo.chat address service; the same as for any copy):

   ```
   assistant connect kelo --instance review-apple
   ```

4. **Make a sign-in code and the review notes:**

   ```
   assistant review code apple            # --days 30 --uses 20 by default
   ```

   It prints only the path of a file (`instances/review-apple/review-notes.txt`, readable by you only) that holds the address, the
   code, a ready-made link and the notes text for the store, filled in from `docs/review/notes-<store>.txt`. Paste its text into
   the store's review notes field (App Store Connect: App Review Information → Notes; Play Console: App access → instructions;
   Partner Center: Submission options → Notes for certification).

   The code works for the given days and number of devices. Each device that uses it becomes an ordinary signed-in device
   labelled with the copy's name (My → Devices on the copy lists them). Only a hash of the code is kept on
   the server. A code that runs out stops new sign-ins; devices already signed in stay.

5. **Check it:** `assistant review status` (mode, address, model, codes; never a code or a key).

## Between review rounds

- `assistant review revoke apple`: every code of that copy stops working and every device that joined with one is signed out.
  The notes file with the old code is removed. Make a new code with `assistant review code apple`.
- `assistant review reset apple`: wipes chats, memory, files, goals, schedules, connectors and logins, the wallet and the
  browser's cookies and downloads. Kept: the settings file (model, key, address), the sign-in codes and the devices signed in
  with them, and keys saved for models. It stops the copy, empties its data in a one-off container, and starts it again.
- `assistant review delete apple`: removes the copy completely, including its kelo.chat address.

## How reviewers connect

The notes tell them; per app:

- **Android:** "Connect with access key" → server address + the code (the access key field also takes a sign-in code), or
  "Paste link" → the full link.
- **iPhone / Windows:** "Paste link" (iPhone) or the link field (Windows) → the full link `https://<address>/#pair=<code>`.
  Their "Connect with access key" screen does not take a sign-in code yet; once it does (as on Android), the Apple and Microsoft
  notes can offer the address + code way too.

The code is not case-sensitive and the dashes may be left out.
