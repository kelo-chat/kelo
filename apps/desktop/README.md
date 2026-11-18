# Kelo for desktop (Windows, also macOS and Linux)

The Kelo chat app for a computer: a Compose Multiplatform for Desktop (Kotlin) app that connects to a Kelo server, like the
Android app in `apps/android/kelo`. It runs in a window and waits in the system tray, so heads-ups reach you while the window is
closed. Packaged as **MSI / EXE** (direct download) and **MSIX** (Microsoft Store) for Windows, and a DMG for macOS.

![Chat](screens/mock-13-chat-replies.png)

## What it does

- **Connect**: paste the pairing link from a signed-in device (My → My devices → Connect a new device) or from
  `assistant phone add` on the computer the Kelo server runs on; or the server address and access key. The device token is
  kept sealed: on Windows with DPAPI (bound to the Windows user), on macOS/Linux in an owner-only key file
  (`%APPDATA%\Kelo`, `~/Library/Application Support/Kelo`, `~/.config/kelo`). Plain `http://` is accepted only for a server on
  this same device (`localhost`, `127.0.0.1`); everything else needs `https://`.
- **Chat**: replies stream in as they are written (short requests to `GET /api/turns/{id}`, so it works through any tunnel),
  tool chips, approval cards (allow / deny, "keep allowing in this schedule"), answer buttons, the secure login/key form,
  connect cards, browser pictures (one card per tab), tool steps of one kind folded into one chip, a task's setup card, a
  permission for many items with its list, the morning briefing card, payment steps (the code, QR code and time left; the approval
  itself happens on the phone; a page's PIN keypad is clicked with the mouse), Markdown (lists, tables, code, links). Right-click a message to copy, edit or retry.
  **Enter** sends, **Shift+Enter** adds a line.
- **Attachments**: drop files anywhere on the chat (or "+"): each uploads at once, streamed from disk, **100 MB at most per
  file** (checked before anything is sent), and is named in the next message.
- **Model chooser** in the composer (the main model; the fallback is under My, and is never on the main model's provider).
- **Kelo's character** in the floating header, drawn from the same art and motion as the phone; its mood follows what the agent
  does (thinking, browser, reading, working, asking, done, offline, asleep at night).
- **다짐 / Goals** (suggestions, plan steps, numbers), **작업 / Work** (questions waiting, running now, coming up, schedules
  with on/off/add/archive, watchers, earlier work with filters), **커넥터 / Connectors** (search, sign in in the browser, device
  codes, keys, mailboxes), **마이 / My** (models, usage, documents, "Kelo가 먼저 알려주기", this device, connect another device,
  log out).
- **Desktop**: system tray icon with the character (open, Work, quit), desktop notifications (finished replies while hidden,
  approvals, the agent's own news, scheduled runs; `GET /api/notifications` every 30 s), start at sign-in (hidden in the tray),
  one instance per user, Ctrl+1…5 (⌘ on a Mac) to switch pages, Ctrl+F (⌘F) to search the chat, light/dark/system, reduce motion.
- **Updates** (the downloaded Windows/macOS app; the Microsoft Store copy updates itself): at start and once a day it reads the
  public list `https://kelo.chat/download/latest.json` (a plain GET with a `Kelo-Desktop/<version>` User-Agent, nothing else). A
  newer build shows a small banner once and a card in My; "받기" downloads the installer, checks its sha256 and only then opens it
  (the user installs it). `-Dkelo.update.base=` / `KELO_UPDATE_BASE` point the check at a local mock for tests.
- **20 languages**, right-to-left for Arabic; every label wraps (checked in narrow windows in German, Arabic and Thai).

## How it shares code with the Android app

The Android app is the reference and is **not changed** by this project:

- The `apps/shared/` module (package `chat.kelo.app`: API client, models, pairing, the chat reducer and turn follower, Markdown parser,
  usage/work/goal rules, notification rules, and the Compose character and icons) is a composite build both apps include
  (`settings.gradle.kts`: `includeBuild("../shared")`, `implementation("chat.kelo:shared")`). Its unit tests run once, in
  `apps/shared/` (`./gradlew desktopTest` there); `./gradlew test` here runs them first. A few Android tests of code that has a desktop
  copy (COPIES.md) stay in `apps/android/kelo` and are also run here (`androidTestsRunHere` in `build.gradle.kts`).
- A few small pieces whose Android file also holds Android-only code are copied by hand under
  `src/main/kotlin/chat/kelo/app/` (theme, motion, the rules of files that also hold UI), each saying where it
  comes from.
- **Text**: all apps' text is `apps/shared/strings/values*/strings*.xml` (Android format; the desktop-only lines are the
  `strings_desktop*.xml` files). `apps/shared/gradle/strings-to-compose.gradle.kts` converts it at build time into Compose resources
  (`chat.kelo.app.res.Res`), so there is nothing to run after changing a string. Screens write `string(Res.string.x, args)`,
  `plural(Res.plurals.x, n, n)`, `stringArray(Res.array.x)` (`chat.kelo.app.ui.text`; Android's `%d`/`%1$s` formatting); outside
  composition (tray, notifications, coroutines) `loadString(Res.string.x, args)`. The app root sets the default locale and
  `LocalTextLocale` to the language shown (`i18n/DesktopLocale.kt`). Check the strings with `python3 apps/android/tools/strings_check.py`.

## Build and run (any OS, JDK 17)

```sh
cd desktop
export JAVA_HOME=/path/to/jdk-17          # macOS with Homebrew: /opt/homebrew/opt/openjdk@17
./gradlew test                            # 587 tests: the shared Android tests + the desktop's own
./gradlew run                             # the app
./gradlew packageDmg                      # macOS disk image: build/compose/binaries/main/dmg/Kelo-<package number>.dmg
./gradlew renderIcons                     # redraw packaging/icons (PNG, ICO, ICNS, MSIX tiles) from the character
```

The app's own log is `kelo.log` in its data folder (no tokens or message text). `KELO_HOME=/some/dir` gives a separate data
folder (tests, screenshots).

### Screenshots

`screens/` holds the main screens, drawn off screen by the app itself from the same composables and live server calls
(`--snapshot`, see `src/main/kotlin/chat/kelo/desktop/Snapshots.kt`), so they can be made where the screen cannot be captured:

```sh
KELO_HOME=/tmp/kelo-shots ./gradlew run --args="--snapshot screens --pair 'https://host/#pair=CODE' --lang ko"
```

- `--size 1920x1080` draws only the main pages (`02-chat`, `03-goals`, `04-work`, `05-connectors`, `06-my`) at that exact pixel size, and
  `--open schedules,model,task:ID,tab:ID` adds screens a click away (`20-open-*`: My -> Model, a task's page, a browser tab live;
  `schedules` just scrolls Work to "Coming up"). These make the Microsoft Store's screenshots (`store/screenshots/{en,ko}`, 1920x1080,
  against a real server with real chat content; the app keeps a local copy of the chat, so delete `cache/*.messages.cache` in `KELO_HOME`
  after clearing a test server's chat). `./gradlew renderStoreLogos` draws the Store's box art, app tile and poster art into `store/logos`.
- `test1-*`: against a real Kelo server (a fresh test instance: no model chosen yet, so the chat shows "choose a model").
- `mock-*`: against `tools/mock_server.py`, a pretend server with sample data and a scripted reply (streaming text, tool chips,
  an approval, answer buttons) — no model is ever called. `python3 tools/mock_server.py --e2e` and pair with the link it prints
  (the app talks only KE2E: `--e2e` puts the server's own gateway, apps/ios/tools/mock_e2e_gateway.py, in front; it needs the
  server's environment, `uv sync`; the UI tests do the same through MockHarness whenever that environment is there);
  `--send TEXT` makes the snapshot run type a message. Never use `--send` against a real server.
- `tools/mock_server.py --fresh [--signin]` answers like a brand-new server (no model, no service ready, the same `/api/providers` rows as a fresh server; `--signin` adds the sign-in services). `ModelSetupFlowTest` drives the first start against it (sheet → key → model, an address, a sign-in) and saves each step to `build/setup-flow/` (or `$KELO_SHOTS`).
- `parity-*`: the pages added to match the Android app (chat, Work, Goals, Connectors, My and its pages, model settings), in Korean, light and dark, drawn by the UI tests (`MyPagesTest`, `OpsPagesTest`, `ChatParityTest`, `WorkPagesTest`, `GoalsConnectorsTest`, `ModelPageTest`) against the mock; `KELO_SHOTS=screens` puts them here. The mock's extra sample endpoints are one module per part of the app in `tools/mock_parts/`.
- `wallet-auto-*` and `grants-*`: whether each card pays on its own or why it asks, and what a schedule may do without asking (wallet, a schedule's page, a login's page), in Korean and English, light and dark, wide and narrow (`GrantsPagesTest`; `KELO_MOCK_WALLET=ask` makes the mock's cards all ask each time: `wallet-auto-list-ask-*`).
- `shipping-*`: shipping addresses next to the cards (list, a Korean address in Korean, a US one in English, the local checks, the
  country list), the chat's address form (Korea's form in Korean, the US form in English, a cancelled and a timed-out one) and the one
  final order card (items, where it goes, the card, the coupon, the total), light and dark, wide and narrow (`ShippingPagesTest`; the
  mock serves the contract's sample JSON from `tools/mock_parts/shipping_data/`, and a message with "머그"/"mug" gets a scripted order).
- `skills-*`: custom skills (`SkillsShotsTest`): `skills-review-*` the skills waiting for approval on My → Records (a new skill's text, a change's added and removed lines; `skills-review-narrow-de-*` 360 dp wide), `skills-save-*` "Save as skill" on a finished task's page (and `skills-save-drafting-*` while the draft is written), in Korean and English, light and dark.
- `server-update-*`: My → 서버 업데이트 / Server update (owner only): the card at the top of My and the page with a newer release, an
  update running and one the server undid by itself, in Korean and English, light and dark, and narrow in German and Thai
  (`ServerUpdateShotsTest`). The mock answers `/api/server-update` only with `KELO_MOCK_SERVER_UPDATE=<scenario>` (available, running,
  waiting, done, failed, rolled_back, rollback_failed, local, forbidden, unavailable; `POST /mock/server-update` switches it while it
  runs; see `tools/mock_parts/server_update.py`); without it every path is 404, like an older server.
- `catchup-*`: what the desktop gained to catch up with the phone (`CATCHUP.md`; `CatchupShotsTest` against the mock with
  `KELO_MOCK_CATCHUP=1`, see `tools/mock_parts/catchup_desktop.py`): the morning briefing card, a task's setup card (urgent), a
  permission for many items with its list, the payment step (the code, QR code and time left; `KELO_MOCK_CATCHUP=pad`: the page's
  PIN keypad clicked with the mouse), folded tool chips, My → Notifications with weekday/weekend hours and the briefing's settings,
  and a watcher's and a job's grants, in Korean and English, light and dark, wide and narrow.
- `*-narrow-*`: a 520 dp wide window in German, Arabic and Thai (wrapping and right-to-left).

## Windows: MSI, EXE and MSIX

jpackage builds Windows installers only on Windows. Two ways:

**GitHub Actions** (`.github/workflows/desktop-windows.yml`, `windows-latest`): on a push touching `apps/desktop/` (or the shared
Android code/strings) or by hand (Actions → desktop-windows → Run workflow; the package number comes from `version.properties` unless one is given). It checks the text is in step, runs
the tests, builds `Kelo-<v>.msi`, `Kelo-<v>.exe` and `Kelo-<v>.0.msix`, and attaches them to the run as an artifact.

**On a Windows PC** (JDK 17 and the Windows 10/11 SDK):

```powershell
cd desktop
.\gradlew.bat test packageMsi packageExe createDistributable
pwsh packaging\windows\make_msix.ps1 -AppDir build\compose\binaries\main\app\Kelo -Version 1.0.8.0 `
  -IdentityName 12345YourName.Kelo -Publisher "CN=..." -PublisherDisplayName "Your Name" -Out build\msix\Kelo-1.0.8.0.msix
```

- MSI: per-user install (no admin prompt), Start menu and desktop shortcuts, a fixed upgrade code so a newer MSI updates the
  installed one. Version numbers (docs/versioning.md): the app shows the release from the root `version.json` (`0.9.0`); the installers carry
  their own rising package number from `apps/desktop/version.properties` (three parts for MSI/EXE, `1.0.8`; four ending in `.0` for
  MSIX, `1.0.8.0`), raised for every desktop release; `-PkeloVersion=X.Y.Z` overrides it for one build.
- The MSI/EXE are not code-signed here (owner decision: they stay unsigned for now). SmartScreen shows a blue "Windows protected
  your PC" window: More info, then Run anyway. Smart App Control (Windows 11, when On) blocks unsigned executables outright, with
  no "Run anyway": there the Microsoft Store version is the supported route. See `docs/install.md`, "Windows: SmartScreen and
  Smart App Control".
- The MSIX (`packaging/windows/AppxManifest.xml`) is a full-trust desktop package with a start-at-sign-in task (off until the
  user turns it on in Settings → Apps → Startup). In the Store version the app's own switch points there instead.

## Microsoft Store

1. Make a Partner Center developer account (individual or company): https://partner.microsoft.com/dashboard → Apps and games.
2. **New product → MSIX or PWA app**, reserve the name **Kelo** (or what you choose).
3. **Product management → Product identity**: copy `Package/Identity/Name`, `Package/Identity/Publisher` and
   `Package/Properties/PublisherDisplayName`. Put them in the GitHub repository as variables `KELO_MSIX_IDENTITY_NAME`,
   `KELO_MSIX_PUBLISHER`, `KELO_MSIX_PUBLISHER_DISPLAY_NAME` (Settings → Secrets and variables → Actions → Variables), or
   pass them to `make_msix.ps1`.
4. Run the workflow with the version; download the `.msix` from the run's artifact. Leave it **unsigned** — the Store signs it.
   (To try it on your own PC first, sign it with a test certificate whose subject equals the Publisher, trust that
   certificate, and double-click the `.msix`; or run the Windows App Certification Kit on it.)
5. **Start a submission**: Pricing and availability, Properties (category: Productivity), Age ratings questionnaire, Store
   listings (descriptions and screenshots per language; `screens/` has light/dark/narrow shots to start from), and
   **Packages** → upload the `.msix`.
6. The `runFullTrust` capability is a restricted capability: in the submission's notes for certification say that Kelo is a
   desktop app (Java runtime packaged with jpackage) that needs full trust to run, connect to the user's own Kelo server and
   show tray notifications. Also give certification a way to try it (a test server address and pairing link, or a note that it
   needs the user's own self-hosted Kelo server, with a short video).
7. Submit. Later versions: raise the version (`1.0.1` → MSIX `1.0.1.0`), run the workflow, upload the new package.

Automatic uploads (a draft submission, or `--commit` to send it to certification) from the maintainer's Mac: `store/msstore.py`, set up as
described in [`store/MSSTORE.md`](store/MSSTORE.md) (Korean). The first submission must still be made by hand in Partner Center.

## Files

- `build.gradle.kts` — build, shared-source sync, packaging (`compose.desktop.application.nativeDistributions`)
- `src/main/kotlin/chat/kelo/desktop/` — `Main.kt` (window, tray, notifications, shortcuts), `Snapshots.kt`,
  `core/` (data folder, sealed store, settings, start at sign-in, single instance, notification polling, log),
  `chat/` (chat model, chat page, cards, Markdown, model chooser), `pages/` (Goals, Work, Connectors, My),
  `ui/` (shell with rail and floating header, connect screen, components), `i18n/` (language -> locale),
  `tools/RenderIcons.kt`
- `src/main/kotlin/chat/kelo/app/` — desktop copies of a few Android pieces (see above)
- `tools/mock_server.py` — a pretend server (the desktop-only text is `../shared/strings/*/strings_desktop*.xml`)
- `packaging/icons/` — icons drawn from the character; `packaging/windows/` — `AppxManifest.xml`, `make_msix.ps1`
- `../.github/workflows/desktop-windows.yml` — Windows build
