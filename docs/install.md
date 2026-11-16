# Installing Kelo

## One command

```
# macOS and Linux
curl -fsSL https://kelo.chat/install.sh | bash

# Windows (PowerShell; needs Docker Desktop with WSL 2)
irm https://kelo.chat/install.ps1 | iex
```

> **Mac (coming): Kelo Server.pkg:** Work is underway on a signed macOS installer package (`Kelo Server.pkg`) for macOS 26+ on Apple silicon. It installs Apple `container` and Kelo's container runtime to run Kelo without Docker or terminal commands. It configures the background LaunchDaemon (`chat.kelo.server`) and opens the guided setup in your browser. Not released yet; see [Direction since 2026-11-16](native-server.md#direction-since-2026-11-16-apple-container-on-macos-26-apple-silicon).
>
> **Windows without Docker Desktop:** Work is underway on a dedicated Windows installer that runs Docker Engine inside Kelo's own WSL 2 distro (no Docker Desktop needed, free for everyone). For architecture and status, see [Direction since 2026-11-16 (Windows)](native-server.md#direction-since-2026-11-16-windows).

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
`--dir DIR`, `--source SRC`, `--from-release` (the default), `--build-here`, `--no-start`, `--uninstall` (add `--delete-data`
to also delete chats, keys and files). On Windows: `-Yes`, `-Add NAME`, `-Dir`, `-Source`, `-FromRelease`, `-BuildHere`,
`-NoStart`, `-Uninstall`, `-DeleteData`. A custom `--source` is built here, since it is not the release, unless `--from-release`
is given too. `NO_COLOR=1` turns colours off. Questions are read from the terminal even through `curl | bash`.

- **Update:** run the same command again (the installer asks whether to update or add another copy; with `--yes` / `-Yes` it
  updates directly). Your data, keys and signed-in phones stay; the containers are rebuilt and restarted.
- **Uninstall:** `bash install.sh --uninstall`. It stops every copy, removes the main containers and the `kelo` command, gives
  a Kelo address back, and keeps your data (Docker volumes, `.env` and `instances/`) unless you add `--delete-data` (then
  every copy is deleted with `kelo instance delete`).

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
- `--from-release` pulls and checks the signed release images instead of building them here (the installer passes it by default).
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
