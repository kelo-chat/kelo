# Installing Kelo

## One command

```
# macOS and Linux
curl -fsSL https://kelo.chat/install.sh | bash

# Windows (PowerShell; one line, no terminal questions, no Docker needed; the setup page opens in the browser)
irm https://kelo.chat/install-windows.ps1 | iex

# Windows with Docker Desktop (WSL 2) instead
irm https://kelo.chat/install.ps1 | iex
```

> **Windows without Docker Desktop:** the Windows line above installs Kelo Server into Kelo's own WSL 2 distro (no Docker Desktop needed, free for everyone). It downloads the Windows installer (MSI) from kelo.chat, checks its size and SHA-256 against the release list, and installs it silently. For architecture, see [Direction since 2026-11-16 (Windows)](native-server.md#direction-since-2026-11-16-windows).

The installer downloads the newest release of [kelo-chat/kelo-server](https://github.com/kelo-chat/kelo-server) (the server part of
each release) into `~/kelo`. To install from another copy instead, tell it where the Kelo files are:

```
KELO_SOURCE=/path/to/kelo bash scripts/install.sh              # macOS / Linux
$env:KELO_SOURCE='C:\path\to\kelo'; .\scripts\install.ps1    # Windows
```

`KELO_SOURCE` can be a folder, a `.tar.gz` (or `.zip` on Windows) URL, or a git URL.

What the installer does:

1. Shows the server it found (system, CPU type) and the folder Kelo goes into (`~/kelo`, or `--dir` / `KELO_HOME`).
2. Checks Docker and Docker Compose, and that Docker is running. If Docker is missing it shows one screen with the link and
   stops. On Linux it offers Docker's own install script (y/N, uses sudo). On a Mac it can open Docker Desktop for you.
3. Installs `uv` for this user if it is missing (no sudo).
4. Copies the Kelo files. Your `.env`, `instances/`, `connect/` and the phone app folders are never overwritten.
5. Prepares Python with `uv sync`, and adds a `kelo` command (`~/.local/bin/kelo`, or `kelo.cmd` on Windows) that runs
   `assistant` in the Kelo folder: `kelo status`, `kelo phone add`, `kelo limits --choose`.
6. Opens the guided setup (`assistant setup --from-release`), described below. It pulls the signed official Kelo images
   (digest and signature checked) and starts them; nothing is built on your computer. If the pull fails, it says why and
   builds the images here instead. `--build-here` builds them here from the start (slow).

Options: `--yes` (no questions; defaults everywhere, never deletes data), `--add NAME` (add another Kelo server copy),
`--dir DIR`, `--source SRC`, `--version X.Y.Z`, `--allow-downgrade`, `--from-release` (the default), `--build-here`, `--no-start`, `--uninstall` (add `--delete-data`
to also delete chats, keys and files). On Windows: `-Yes`, `-Add NAME`, `-Dir`, `-Source`, `-Version`, `-AllowDowngrade`, `-FromRelease`, `-BuildHere`,
`-NoStart`, `-Uninstall`, `-DeleteData`. A custom `--source` is built here, since it is not the release, unless `--from-release`
is given too. `NO_COLOR=1` turns colours off. Questions are read from the terminal even through `curl | bash`.

### Choosing the version

By default the installer installs the newest release. To install an exact one, add `--version X.Y.Z` (PowerShell `-Version X.Y.Z`,
or the environment variable `KELO_VERSION`):

```
curl -fsSL https://kelo.chat/install.sh | bash -s -- --version 0.10.2
```

- The Kelo files come from the `release-X.Y.Z` tag of `kelo-chat/kelo-server`, and with `--from-release` (the default) the images
  are the signed ones of that version, checked like an update checks them (digest, signature, version label). A version whose
  own files do not yet know `--release-version` cannot pull its signed images by version: the installer says so and builds
  that version's images here from its files instead.
- A version that does not exist stops the installer with one line before anything is changed. All versions are on the releases
  page: https://github.com/kelo-chat/kelo-server/releases.
- An update to an **older** version than the installed one is refused (data written by a newer version may not go back), also
  with `--yes`, unless you add `--allow-downgrade` (PowerShell `-AllowDowngrade`, or `KELO_ALLOW_DOWNGRADE=1`). The same version
  again, or a newer one, needs nothing extra.
- With your own `--source`, `--version` does not pick the files: the files you point at must be that version (their
  `version.json` is checked before anything is copied), and `--version` picks the images.
- `KELO_RELEASES_BASE` points the installer at another copy of the public source repository (default
  `https://github.com/kelo-chat/kelo-server`); it exists for tests.

- **Update:** run the same command again (the installer asks whether to update or add another copy; with `--yes` / `-Yes` it
  updates directly). Your data, keys and signed-in phones stay; the containers are rebuilt and restarted.
- **Uninstall:** `bash install.sh --uninstall`. It stops every copy, removes the main containers and the `kelo` command, gives
  a Kelo address back, and keeps your data (Docker volumes, `.env` and `instances/`) unless you add `--delete-data` (then
  every copy is deleted with `kelo instance delete`).
- **Windows uninstall:** remove Kelo Server in Settings > Apps. It keeps your data in `C:\ProgramData\Kelo\wsl`. To delete the data too, open a terminal as administrator and run `msiexec /x "KeloServer-<version>-x64.msi" KELO_DELETE_DATA=1` with the MSI you installed. Backups you made by hand (`backup-*.tar` in `C:\ProgramData\Kelo`) are kept.

## Windows: SmartScreen and Smart App Control

The Windows installers (desktop app `.exe` and `.msi`, and the Kelo Server `.msi`) are **not code-signed** for now. The Windows line above downloads the Kelo Server MSI with PowerShell, which does not mark the file as downloaded from the internet, so SmartScreen does not show on that route; the note below is for the MSI download in a browser. What
you see depends on one Windows setting. Findings come from a test on a Windows 11 PC (2026-11-16: Smart App Control On, an
unsigned Kelo Server MSI with a downloaded-file mark).

| Windows setting | What happens | What to tell the user |
|---|---|---|
| Smart App Control Off, SmartScreen On (the usual case) | A blue "Windows protected your PC" window | Click **More info**, then **Run anyway**. |
| Smart App Control **On** (Windows 11, Settings → Privacy & security → Windows Security → App & browser control) | An unsigned `.exe` is blocked and the window has no "Run anyway" button | Use the Microsoft Store version of the app. Direct installers are not supported with Smart App Control On yet. Turning it Off is possible but cannot be switched back On without reinstalling Windows, so Kelo does not suggest it. |

What the test proved and did not prove, so the texts do not promise more:

- The unsigned Kelo Server **MSI** installed and uninstalled without any block under Smart App Control (it is run by Microsoft's
  signed `msiexec`; the scripts it runs went through Microsoft's signed `powershell.exe` and `wsl.exe`). The server came up and
  answered `/healthz`.
- Unsigned PowerShell scripts also ran (with `-ExecutionPolicy Bypass`).
- Not proven: the Burn setup bundle `.exe` (an unsigned executable launched on its own, the kind Smart App Control blocks) and the
  desktop app's own `Kelo.exe` inside its MSI. Both are treated as **not supported with Smart App Control On**. A signed
  certificate would be the real fix; the installers stay unsigned for now, to be revisited before launch.

Texts that say this (keep them in step): the Windows download card on the site's front page (`index.en.html`, `index.ko.html`),
README.md and README.ko.md ("Windows" under the install list), `apps/desktop/README.md`.

## The guided setup

`assistant setup` (or `kelo setup`) in a terminal opens a four-step screen in the system language (any language in
`server/src/assistant/locales/`; `KELO_LANG=ko` or `KELO_LANG=en` overrides it):

1. **Check the Kelo server** – Docker, Compose, and the CPUs, RAM and free disk Docker can use. Creates the access token and the
   encryption key (never shown).
2. **Size** – Recommended (RAM 4 GB · CPU 4 · disk 20 GB), Minimum (RAM 2 GB · CPU 2 · disk 10 GB) or No limit (everything
   this computer has, shown as numbers). The size is what all of Kelo's containers together may use, program files included;
   the server, browser, work room and Claude/agy containers each get a share of it. It suggests Minimum when the computer is
   smaller than Recommended and warns if a choice is above what it has. Change it later with `assistant limits --choose`
   (or `--profile recommended|minimum|unlimited`; single sizes such as `--memory` still work).
3. **Start Kelo** – with `--from-release` (the installer's default) it first pulls and checks the signed official images, then runs
   `docker compose up -d` (no build; the Claude and agy containers are still built here, as their CLIs are not published). If the
   pull fails it shows the reason and runs `docker compose up -d --build` instead. Without `--from-release` it is
   `docker compose up -d --build`. Either way a spinner and the last few log lines show, then it waits until Kelo answers.
4. **Phone** – a `<name>.kelo.chat` address (offered for now; availability and terms may change) (`assistant connect kelo`), Tailscale or your own domain (prints the
   commands to run), or skip for now (the phone app needs an https address, so it cannot sign in until one is set). Then it
   shows a compact QR code (about 20 lines) to scan with the phone, and the link.

The AI model is not chosen in the terminal. After the phone is connected, the app asks for the AI model and its key, and keeps
asking until one is set. The last screen of the setup says so. Model settings already in `.env` (`ASSISTANT_PROVIDER`,
`ASSISTANT_MODEL`, provider keys) are left as they are.

A re-run is an update: it keeps every answer as the suggestion (press Enter) and never rotates keys or tokens.

- `assistant setup --plain` asks the old plain-text questions.
- `assistant setup --yes` asks nothing.
- `--no-start` saves the answers without building or starting.
- `--from-release` pulls and checks the signed release images instead of building them here (the installer passes it by default); `--release-version X.Y.Z` with it pulls that release instead of the newest (the installer's `--version`). `assistant updater setup --pull-release --release-version X.Y.Z` does the same for a first install without the guided screen.
- `--browser`, `--cli` and `--profile` still skip their questions.

### Setup in a browser

`assistant setup --web` asks the same four steps in a page instead of the terminal: it prints a link, opens it in the
browser, and the page answers each question as the terminal would. The page runs only on this computer (it listens on
127.0.0.1 only), and the link carries a one-time token in its fragment, so the token never reaches a server log. When the
run ends, the page shows the summary (or the failure) and the server stops by itself. `--no-open` only prints the link and
leaves the browser alone. The terminal setup is unchanged; the Mac and Windows installers are
planned to use this page.

Screenshots: `docs/install-demo/`.

## Updates from the app

The installer sets up the **updater** container (`assistant updater setup`) and asks one question, "Update automatically at
night?" (default no). Installer options: `--auto-update` / `--no-auto-update` (`KELO_AUTO_UPDATE=1|0`; PowerShell `-AutoUpdate` /
`-NoAutoUpdate`) skip the question. The installer pulls and checks the signed release images by default (`--from-release`,
`KELO_FROM_RELEASE=1`, `-FromRelease`); `--build-here` (`KELO_FROM_RELEASE=0`, `-BuildHere`) builds them here instead (the Claude
and agy containers are built here either way: their CLIs are never published). `KELO_REGISTRY` points at another registry
(default `ghcr.io/kelo-chat`).

`assistant updater setup` writes into `.env`, printing no secret:

| Setting | What it is |
|---|---|
| `UPDATER_TOKEN` | the secret only the main copy's server and the updater share (made once) |
| `KELO_ROOT` | this folder; the updater sees it at the same path, so it uses exactly the same compose and env files (`KELO_ROOT_HOST`: the Windows path, on Windows) |
| `KELO_UID`, `KELO_GID` | the folder's owner; the updater runs as them, so the files it writes stay yours |
| `DOCKER_GID`, `KELO_DOCKER_SOCKET` | the Docker socket's group and path, as Docker sees them |
| `KELO_REGISTRY` | where release images come from (unset = `ghcr.io/kelo-chat`) |
| `KELO_AUTO_UPDATE` | `1` = nightly auto-update until the owner changes it in the app |

and adds `updater` to `COMPOSE_PROFILES`. Only the main copy runs an updater; it updates every copy on the computer (they share the
images), so the owner of an extra copy cannot update the others. `assistant updater off` removes it; `assistant update` keeps working
without it. A copy built from the source files (no release label on its images) is reported as such in the app, which then says to
update with `kelo update` on the server machine.

How a release is made (for the operator; nothing is published automatically): a version tag `v0.5.0` in the source repository runs
`.github/workflows/release-images.yml`, which builds `server` (the server, browser and egress roles), `workroom` and `updater` for
linux/amd64 and linux/arm64, pushes them to `ghcr.io/kelo-chat/<name>:<version>` and `:stable`, signs them with cosign keyless, and
attaches `release.json`. `assistant publish-server release.json` on the operator's server adds it to
`https://kelo.chat/download/latest.json` under `"server"`, where every updater reads it.

## More than one Kelo server on one computer

One computer can host several Kelo servers (for different people, or for testing). Running the installer again asks whether to update or create another copy (`--add <name>` / `-Add <name>` creates one directly; `--yes` / `-Yes` updates). You can also run `kelo instance create <name>` at any time.

- **What is separate:** Each copy runs on its own port, with its own access token, database, chats, files, API keys, and phone pairings. Each copy can also have its own `<name>.kelo.chat` address (`kelo connect kelo`). Settings live in `instances/<name>/env`.
- **What is shared:** Docker images and the updater container are shared across all copies on the computer.
- **Updating:** Only the main copy runs the updater; an update from the main copy's app (or `kelo update`) updates all copies together.
- **Memory:** Each additional copy needs its own RAM and disk space. Recommended size for another copy is 4 GB RAM (minimum 2 GB). Check and adjust caps anytime with `kelo limits --instance <name>`.

## Optional settings

Run these in the folder that holds `compose.yml`, after `assistant setup`. Add `--instance NAME` to work on an extra copy instead
of the main one. `assistant status` shows each copy, whether it runs, and which of these settings are on.

## Signing in with a subscription account (off by default)

Kelo normally uses a model through an API key. Some providers can also be used by signing in with a consumer subscription or
account instead: Claude (Pro/Max), Google Antigravity, ChatGPT (Codex), Qwen and MiniMax. The vendors have not confirmed that
using these subscriptions from other apps is allowed, so Kelo calls them **unofficial** and hides them unless you turn them on:

```
assistant enable oauth                     # the main copy
assistant enable oauth --instance alice    # an extra copy
assistant disable oauth                    # hide them again
```

What it does:

- It saves `ASSISTANT_UNOFFICIAL_PROVIDERS=1` (or `0`) in the copy's settings file (`.env`, or `instances/NAME/env`), so the
  setting survives restarts and updates. If the copy is running, only its server container is recreated to read it. Chats and
  data are kept. `--no-restart` only saves it.
- **On:** the app lists these providers under an "unofficial" divider (My → Model and the chat model sheet), and asks once before
  each one is used. Choosing one guides you through its sign-in.
- **Off (the default):** the app does not list them, and the server refuses to newly select one (`403 unofficial_disabled`).
- **Turning it off never breaks a working setup.** If a copy already uses one of these providers as its main or fallback model
  (or as the server's default `ASSISTANT_PROVIDER`), that one keeps working and stays listed until another model is chosen. The
  others are hidden. Saved sign-ins are not deleted.
- Claude and Antigravity run in their own containers. `enable oauth` also turns those on (`COMPOSE_PROFILES=claude,agy`) and
  starts them, because the server container cannot start containers itself; the first start builds their images. Their logins
  live in their own volumes, so this changes no sign-in. (`disable oauth` leaves them running.)

### Signing in from the phone

No terminal is needed. In the app: My → Model, choose the provider, then **Sign in** (after the one-time notice).

| Provider | What the app shows | What you do |
| --- | --- | --- |
| ChatGPT (Codex), Qwen, MiniMax | An address and a short code (device code) | Open the address, type or confirm the code, approve. The app notices by itself. |
| Claude (Pro/Max) | An address (`claude auth login` in the Claude container) | Sign in; the page shows a code. Copy it and paste it into the app. |
| Antigravity | An address (the `agy` sign-in in its container) | Sign in with Google; the page shows an authorization code. Paste it into the app. |

Pasting the whole address the page ended on works too. If the Claude or Antigravity container is off, the app says so and names
`assistant enable oauth`. A sign-in in the terminal still works (`docker compose exec -it claude-cli claude` → `/login`,
`docker compose exec -it agy-cli agy`, `python -m assistant.providers.oauth login <provider>`).
