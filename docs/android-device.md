# A phone Kelo drives: an Android device over adb (design, in progress)

Status: design 2026-11-14 (owner: driving it from Kelo is what matters). Phases 1, 1b and 2 are built; the phone card in the apps is described below.

## What it is

The owner runs an Android device Kelo may use on their behalf: a redroid container on a Linux machine (docs below), an emulator, or
a spare phone with wireless debugging. Kelo reads its screen (the UI tree: texts, buttons and their places, plus a screenshot when
the model can see images), taps, types, swipes, presses keys and opens apps, the way it already drives its browser. It is for apps
with no usable website (delivery, booking, app-only coupons and lookups). The owner's own phone is never driven (that stays the
phone actions the app itself offers).

Tested 2026-11-14 on the owner's Linux host: redroid 15 (`redroid/redroid:15.0.0-latest`, 720x1280, software GPU) boots in ~110 s and
uses ~1.2 GB; over adb, `uiautomator dump` gives the screen's texts and bounds and `input tap` on a bound's centre opens it. Android 16
images crash SurfaceFlinger without a GPU; Android 13 works but is old.

## Where the device runs

Kelo does not decide where the owner keeps the device; three places are supported, chosen by one setting:

| place | who | how the bridge reaches it | setting |
|---|---|---|---|
| **inside Kelo** (same machine, same compose project) | a Linux server whose kernel has binder (most distribution kernels: Debian, Ubuntu with linux-modules-extra) | the `redroid` service on the internal `android-dev` network; adb is never published on any host port | `ANDROID_DEVICE=redroid:5555` + compose profile `android-device` |
| **next to Kelo** (another container or VM on the same machine or LAN) | the owner already runs redroid, an emulator or a VM | the LAN address through `android-out` | `ANDROID_DEVICE=<lan-address>:5555` |
| **a spare phone** | any server | wireless debugging on the phone (pair once: `assistant android pair <host:port> <code>`) | `ANDROID_DEVICE=<phone-address>:<port>` |

Docker Desktop on macOS and Windows has no binder in its Linux VM, so "inside Kelo" is Linux only; those servers use "next to Kelo"
or a spare phone.

### Inside Kelo (README steps only)

Owner 2026-11-15: no setup command and no `redroid` service in compose.yml. "Inside Kelo" means the owner runs redroid on the same
Linux machine by the README steps (kernel modules once, `docker run` with `--network <project>_android-out`, then
`ANDROID_DEVICE=redroid:5555` and the `android` profile in `.env`). The design below is kept for reference only; it is not planned.

#### (not planned) the `redroid` service and `assistant android setup`


- compose.yml: service `redroid` under `profiles: [android-device]`, image pinned by digest (`redroid/redroid:15.0.0-latest@sha256:...`),
  `privileged: true` (redroid needs it), `mem_limit: ${ANDROID_MEMORY:-2560m}`, volume `android-data:/data`, the boot arguments of
  the setup section below, network `android-dev` only (internal: true; the bridge is the only other member). Its own internet goes
  through `egress` like the browser's (`android-dev` is internal; Android's proxy setting `http://egress:3128` is set by the bridge
  after boot with `settings put global http_proxy`), so the device follows the owner's VPN route too.
- `assistant android setup` (cli.py, Linux only, needs root once): checks `binder_linux`, `loop`, `erofs` (`modinfo`), writes
  `/etc/modprobe.d/kelo-android.conf`, `/etc/modules-load.d/kelo-android.conf` and the udev rule (0666 binder devices), loads them,
  then sets `ANDROID_DEVICE=redroid:5555` and the profile in `.env` and starts the services. Each step says what it does and asks
  first; `--undo` removes exactly these files. A kernel without binder gets a clear message ("this kernel has no binder; use a device
  next to Kelo or a spare phone").
- The installer does not ask about it (owner, 2026-11-15): the device is an extra the owner turns on later (README, this page).
- Sizes: "Recommended" server size grows by 2.5 GB when it is on; the setup refuses when the machine has less than 2 GB free.

## Pieces

```
compose.yml           android (profile "android"): adb + a small HTTP bridge; networks android-net (server) and android-out (LAN)
docker/android/       Dockerfile (python:3.12-slim + android-tools adb), bridge.py
server/src/assistant/android/
  device.py           DeviceClient: the bridge's HTTP API, typed; one client per server
  screen.py           ScreenReader: UI tree XML -> Screen(items: [Item(ref, text, desc, cls, bounds, clickable, editable, checked)])
  tools.py            the agent tools below
  judge.py            commit kind of a tap from the item's words (tools/commits.judge_words) and of typing (commits.judge_type)
```

### The bridge (docker/android/bridge.py)

The server cannot reach the LAN (its network is internal; everything goes through `egress`), so adb lives in its own sidecar, like
the browser. The bridge is the only thing on `android-out` and serves the server only (on `android-net`, a token from `.env`
`ANDROID_BRIDGE_TOKEN`, at least 32 characters, like KEYRING_TOKEN). It keeps one adb connection to `ANDROID_DEVICE`
(`host:port`, e.g. `192.168.1.50:5555`; empty = feature off) and reconnects on failure.

```
GET  /status                         {"connected", "device", "android": "15", "size": [w, h], "app": "<package/activity in front>"}
GET  /screen?image=0|1               {"xml": "<uiautomator dump>", "png": "<base64>"|null, "app"}
GET  /screen?image=only              {"png": "<base64>"}: the picture alone, no dump (the browser view's frames)
POST /tap        {"x", "y"}
POST /swipe      {"x1", "y1", "x2", "y2", "ms"}
POST /text       {"text"}            ASCII with `input text` (spaces as %s); anything else (Korean, emoji) through ADB Keyboard:
                                     switch to it, broadcast ADB_INPUT_B64, switch back to the owner's keyboard. Without it: an error
                                     that says to install it (redroid has no shell clipboard command, and `input text` is ASCII only)
POST /key        {"key": "back|home|enter|recent|delete|tab"}
POST /open       {"package"}         the launcher activity (cmd package resolve-activity), started with `am start -n` (monkey exits with an error on redroid)
GET  /apps                           {"items": [{"package", "label"}]} launchable apps
POST /install    {"apk_url"}         refused unless the owner turned installs on (ANDROID_ALLOW_INSTALL=1)
```

Every call has a timeout (dump 20 s, others 10 s) and answers `{"error"}` instead of hanging.

### ScreenReader (screen.py)

Pure. Parses the dump into items with a short ref (`a1`, `a2`, ... in reading order), drops invisible and zero-size nodes, merges a
clickable parent with its only text child, and renders the model's view like the browser's controls reading:

```
App: com.coupang.mobile (ReviewWriteActivity)   720x1280
a1 button "Back"
a7 edit "Tell others what you thought of this product" (empty)
a9 button "Submit"
```

Refs live until the next `android_look`; a tap on a stale ref is refused with "look again".

### Tools (tools.py; loaded on demand, `find_tools` "android", "phone app")

| tool | args | does |
|---|---|---|
| `android_look` | `image?` | the screen as above (+ the screenshot for a model that sees images) |
| `android_tap` | `ref` or `x, y` | taps the item's centre |
| `android_type` | `ref?`, `text`, `submit?` | taps the field first when `ref` is given, then types |
| `android_swipe` | `direction` up/down/left/right or coordinates | scrolls |
| `android_key` | `key` | back, home, enter, recent |
| `android_open` | `app` (package or label) | opens an app |
| `android_apps` | | lists launchable apps |

Each acting tool returns the new screen (one dump after the step settles, 0.8 s), so the model rarely needs a separate look.

### Rules (judge.py)

The same rules as the browser, with the app's package as the "site":
- a tap on an item whose words are a payment, send, delete, apply, account or security word (`commits.judge_words`) is that commit
  kind -> the usual approval card, rules ("always allow on this app"), task grants and `done_once` (fingerprint: package, activity,
  the item's words, and what was typed in the run since the last commit, like `FormWords`);
- typing personal details (`commits.judge_type` on the text and the field's hint) asks; typing a message does not (its send button
  does);
- payments always go through the purchase rules (no tap that pays without the owner's approval, or a grant that covers it);
- what the screen shows is outside text (fenced like a web page), so the outside-text rule applies to steps after reading it.

### Settings

`.env`: `ANDROID_DEVICE`, `ANDROID_BRIDGE_TOKEN`, `ANDROID_ALLOW_INSTALL`. The server shows the device in My -> Settings later (phase
3); until then the owner sets it in `.env` and runs `assistant update`.

## Signing in without seeing the screen: `android_login`

Most sign-ins on the device (Google Play, an app's own account) need no eyes: the owner enters the login in Kelo's secure fill-in
card (`request_login`, the vault: values never reach the model) and Kelo types it on the device.

- `android_login(name, step="both"|"username"|"password", submit=true)` in `tools/vault_tools.py`, next to `browser_login`, sharing
  its checks (vault locked, no such login, nothing entered yet, the last sign-in was rejected, Transient "save once it works").
- Where it may type: the login item's `hosts` must name the app or its service: `app:<package>` (new host form, accepted by
  `request_login` and `login_site_ok`), or the service's web host for its own sign-in screens (`accounts.google.com` for a Google sign-in
  shown by `com.google.android.gms` / `com.android.vending`; a small table `APP_SIGNIN_HOSTS` maps those packages to hosts). Anything
  else is refused with the same wording as a wrong site.
- Fields: from the last screen (refresh it first): the password field is the visible edit node with `password="true"`; the user name
  field is the nearest visible edit node above it, or, on a page with no password field (Google asks for the e-mail first), the
  focused or first edit node. `step` works like the browser's two-page forms.
- Typing: the bridge `/text` (ADB Keyboard for anything but plain ASCII); the values are passed server -> bridge only, never logged on
  either side, never in the tool result (the result is the new screen with the edit nodes' text replaced by "(filled)").
- Submit: `/key enter`, or a tap on the button named Next / Sign in (and their translations, e.g. the Korean words for Next and Sign in) (commits.judge_words "account" or a fixed
  word list) when enter does not move the screen within 2 s.
- Result reading (`SignInReader`): wait up to 12 s like the browser's sign_in_wait, polling the screen every 0.5 s: success when the
  password field is gone and the package/activity left the sign-in screen; failed when the screen says the password is wrong (the
  browser's `_FAILED` words + Google's "Wrong password"); code_needed when it asks for a code or shows "Check your phone"
  -> the same notes as the browser (find_login_code, ask the owner). The vault's settle() is reused.
- Approval card: "Type your <label> login into <app>" (the card the browser's login shows, with the app's name).

## Seeing and using the screen through Kelo's browser view (no app update)

When the owner must look (a human check, an app's first-run screens, a step Kelo cannot read), Kelo shows the device inside its own
browser and the owner uses the apps' existing browser "take control" view. The browser never talks to the bridge: the server is the
middle.

- `AndroidMirror` (server/src/assistant/android/mirror.py) opens a tab of its own (`<owner>:phone`, so the agent's own page stays) in Kelo's browser on `about:blank` and writes a small page
  into it over DevTools (`Page.setDocumentContent`): an `<img>` that fills the width, a text box with Send, and Back / Home / Enter
  buttons. No network: the server pushes each new screenshot into the page (`Runtime.evaluate` setting the img's `src` to a
  data URL), at most 2 frames per second and only when the picture changed (hash of the PNG).
- Input: the page's script keeps a queue `window.__keloQueue` of {type: tap, x, y (image pixels), } / {type: text, value} / {type: key,
  key}; the server drains it every 300 ms with `Runtime.evaluate`, maps image pixels to device pixels (the image is the device's
  size, scaled by the page width) and calls the bridge. Typed text goes straight to `/text` and is never logged.
- `android_show(reason)` tool: opens the mirror tab, moves the agent's live view to it, sends the owner the usual "take control"
  notice with the reason ("Sign in to Play: a human check"), and waits like `request_human_check` does (the owner presses Done in the
  page, or 10 minutes pass). The mirror stops when the tab closes or after 10 minutes without input.
- Approval rules: the owner's own taps need none (it is the owner's hand), as with browser take-control.

## The phone card in the apps (phase 2, built)

While Kelo drives the device the chat shows a **phone card** (the browser card for the device: phone icon, "Phone", the step line,
a portrait picture) and its "View" opens a live view of the device inside the app, with take control / give back, taps, swipes,
typing and Back / Home / Recents. No browser tab is involved (`android_show` is the fallback for apps that do not know the card; its
description says the apps' card is preferred).

- One live interface, two sources: `server/src/assistant/live_source.py` has the protocol `LiveSource` (ticket, viewer join/leave, newest
  frame, take / give back, input, control state) and the registry `LiveSources`. The browser's `LiveHub` and the device's
  `DeviceLive` (`android/live.py`) implement it; the routes `/api/browser/sessions/{key}/live-ticket|live|frame|control|input` ask the
  registry for the source of `{key}`: the key `android` is the device (404 when `ANDROID_DEVICE` is not set up), every other key a
  browser tab. No route branches on the kind.
- `GET /api/browser/sessions` also lists the device (`{"id": "android", "key": "android", "kind": "device", "source": "device", "title",
  "width", "height", ...}`) when it is set up; every browser item carries `"source": "browser"` (their `kind` is unchanged).
- Frames: the bridge's `GET /screen?image=only` PNG -> JPEG (Pillow, at most 720 px wide, quality 55), **at most 3 a second and only
  while at least one viewer is joined** (nobody watching = the bridge is never asked for a picture; a viewer silent for 60 s is
  dropped). A picture identical to the last (same PNG hash) is not encoded again and sends no new frame.
- Input (frame pixels -> device pixels by the frame/device ratio): `{"type":"tap","x","y"}`, `{"type":"swipe","x1","y1","x2","y2","ms"}`,
  `{"type":"text","text"}` and `{"type":"key","key":"back|home|enter|recents"}`. **Typed text is never logged, kept or echoed**
  (an error from the bridge has it replaced). What the owner does by hand needs no approval card.
- Control: the device taking the phone holds it while a live connection is open or for 30 s after its last request; meanwhile the
  `android_*` steps wait up to 30 s and then answer "the owner is using the phone; try again in a moment".
- Chat card: after each `android_*` step the server sends a turn event `{"type":"device","key":"android","status","image"}` (JPEG
  <= 120 KB, <= 720 px wide, at most one per 2 s; `android/card.py`); the apps fold the steps of the reply into one phone card.

## Phases

1. Bridge container + DeviceClient + ScreenReader + the tools + judge rules; tests with a fake bridge and saved dumps (a settings
   screen, a review form, a payment button). On by setting ANDROID_DEVICE.
1b. `android_login` (vault fill-in card -> typed on the device) and `android_show` (the device inside Kelo's browser view).
2. Live view (built): the apps show a phone card in the chat while Kelo drives the device and a live view of it (see "The phone card
   in the apps" below), for sign-ins and checks the owner does by hand. `android_show` stays as the fallback for old apps.
3. Inside Kelo: README and this page only (owner, 2026-11-15: no `assistant android setup` / `pair` commands, no compose service
   added by Kelo). The owner follows the README steps (kernel modules, `docker run` of redroid on the project's `android-out` network,
   `ANDROID_DEVICE=redroid:5555`).
4. Settings screen (device address, test connection), install from Play when the image has Google services.

## Setting up redroid on a Linux machine

```sh
# kernel modules (Debian 13's stock kernel has them; binderfs is not needed)
echo "options binder_linux devices=binder,hwbinder,vndbinder" > /etc/modprobe.d/redroid.conf
printf "binder_linux\nloop\nerofs\n" > /etc/modules-load.d/redroid.conf
modprobe binder_linux devices=binder,hwbinder,vndbinder && modprobe loop && modprobe erofs
# Android's services open binder as their own users
printf 'KERNEL=="binder", MODE="0666"\nKERNEL=="hwbinder", MODE="0666"\nKERNEL=="vndbinder", MODE="0666"\n' > /etc/udev/rules.d/60-redroid-binder.rules
chmod 666 /dev/binder /dev/hwbinder /dev/vndbinder
docker run -d --privileged --name redroid --restart unless-stopped --memory 2560m -v redroid-data:/data \
  -p <lan-address>:5555:5555 redroid/redroid:15.0.0-latest \
  androidboot.redroid_gpu_mode=guest androidboot.redroid_width=720 androidboot.redroid_height=1280 androidboot.redroid_dpi=320
```

Install ADB Keyboard (github.com/senzhk/ADBKeyBoard, one 19 KB APK) so Kelo can type Korean and emoji:
`adb install keyboardservice-debug.apk && adb shell ime enable com.android.adbkeyboard/.AdbIME` (tested with Korean and an emoji).

Bind adb to the LAN address only (never 0.0.0.0 on a machine reachable from the internet). Google Play needs a GApps image
(MindTheGapps for Android 15) and the device registered at google.com/android/uncertified.
