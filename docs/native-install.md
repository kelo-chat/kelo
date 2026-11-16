# Kelo without Docker: native install on macOS and Windows (design)

Status: design only, nothing built. Written 2026-11-09 against the code in this repository. Owner's choice: **option C**, meaning no
container engine. The server, a bundled Chromium and the other parts run as ordinary processes, installed from a DMG (macOS) or an
MSI/EXE/MSIX (Windows). Linux servers, NAS boxes and the cloud keep Docker.

Labels used below: **[guess]** is an estimate or a judgement nobody has measured yet. **[verify]** is a fact this document relies on
but did not check on a real machine. Everything else was read from this repository or from the cited source.

---

## 1. Summary and recommendations

**It can be done.** Most of what the containers give us has a native replacement, but some of those replacements are weaker.
Docker gives us four things: (1) a separate Linux VM on Mac and Windows, so the owner's files are simply not there; (2) one
unprivileged user per component; (3) network topology (`browser-net` is `internal`, and `workroom` has `network_mode: none`);
(4) restart, limits and images as the unit of update. Natively we rebuild (2) from OS accounts or OS sandboxes and (3) from a local
proxy plus OS sandbox rules. We rebuild (4) with launchd, Task Scheduler and versioned folders. Nothing replaces (1) except the
sandboxes themselves.

Recommendations:

1. **One app, two roles.** The existing desktop app (`apps/desktop/`) gets a switch, "Use this computer as the Kelo server". The server
   parts are a separately versioned, signed **runtime pack** that is downloaded when the switch is turned on. The pack is not
   inside the client bundle (section 7).
2. **Browser.** Bundle a Chromium with its own profile folder. Keep Chromium's own sandbox on. Run it `--headless=new`. Force all
   traffic through the egress proxy with flags *and* policy (`--proxy-server`, `--proxy-bypass-list=<-loopback>`, QUIC off, WebRTC
   `disable_non_proxied_udp`, no extensions, `file://` blocked). Replace the open DevTools port with a **DevTools gate**: Chromium
   uses `--remote-debugging-pipe` and the server connects to a websocket on loopback whose path holds a secret. Do **not** wrap
   Chromium in `sandbox-exec`, because it most likely breaks Chromium's own sandbox (section 4.1).
3. **Egress.** Run the existing `assistant/egress.py` unchanged in substance as a local process, bound to `127.0.0.1` (today it
   binds `0.0.0.0`) on a random port. The page-level guarantee then comes from Chromium's network-service design. The OS-level
   guarantee comes only in "hardened" mode: a hidden `_kelobrowser` account plus a packet-filter rule on macOS, or a firewall/WFP
   rule on Windows (section 4.2).
4. **Workroom on macOS.** Use Seatbelt through `sandbox-exec` with a profile generated per job: writes only to that job's folder,
   no network at all for runs, and loopback to one port only during an approved install. Copy the *approach* of Anthropic's
   `sandbox-runtime` (Apache-2.0) into Python. Do not take it as a runtime dependency (section 4.3).
5. **Workroom on Windows.** Ship native Windows **without code execution at first**. Then add an **AppContainer (LPAC) + Job
   Object** runner. A WSL-based "full Linux work room" can follow as an opt-in for owners who already have WSL (section 4.3).
6. **CLI sidecars.** Keep the HTTP boundary (`cli_runner.py` ↔ `providers/cli_remote.py`) and run each CLI runner as its own
   process. Give each one its own config folder (`CLAUDE_CONFIG_DIR`, `ANTIGRAVITY_CONFIG_DIR`) and, on macOS, its own Seatbelt
   profile that cannot read Kelo's state or the owner's home (section 4.4).
7. **Two protection levels on macOS.** *Standard* needs no admin: everything runs as the owner, with sandboxes. *Hardened* asks
   for admin once: hidden accounts `_kelobrowser` and `_kelowork`, LaunchDaemons and a `pf` anchor. Offer Hardened at set-up and
   show the active level in the app. Windows starts with the equivalent of Standard.
8. **Drop multiple copies (instances) for native.** Keep one server per OS user account. Several people per computer stays a
   Docker/Linux feature (section 4.8).
9. **Updates.** Extend the desktop app's existing updater (`apps/desktop/.../core/Updates.kt`, which already reads `latest.json` and
   checks sha256) with **minisign signatures**, in the minisign format (Ed25519). Use **versioned
   side-by-side runtime folders**. Apply the database rule from `updates.py` unchanged (back up before, restore on rollback when
   the schema fingerprint changed). Give Chromium its own update channel, because it needs security updates more often than Kelo
   ships (section 6).
10. **Phases.** 0: spikes (2–3 weeks). 1: macOS native (8–12 weeks). 1b: macOS hardened accounts (3–5 weeks). 2: Windows without
    code execution (6–9 weeks). 3: Windows code execution (5–8 weeks). All of these are **[guess]**, for one engineer (section 11).

What we honestly lose compared with Docker on a Mac: in Standard mode, a full browser compromise means a renderer bug plus a
sandbox escape. That runs as the owner, so it can read the owner's files and reach the LAN. With Docker it would land in a Linux VM
that has neither. Hardened mode closes most of that gap. Standard mode is roughly the risk of the owner's own Chrome, with an
autonomous user driving it.

---

## 2. What each container does today

From `compose.yml`, the Dockerfiles and `docker/entrypoint.sh`. Every service has `cap_drop: [ALL]` and `no-new-privileges`, and
most have a read-only root.

| Container | Protects against | What depends on it |
|---|---|---|
| **Docker Desktop VM** (Mac/Win) | Nothing from the owner's disk is visible unless mounted, and Kelo mounts nothing from the host except the updater's Kelo folder. Any escape lands in a Linux VM. | Everything. |
| `server` (uid 1001, read-only root, `env_file`) | Holds the secrets: `ASSISTANT_TOKEN`, API keys, `ASSISTANT_SECRET_KEY` and the DB. It is the only container with them. Port is bound to `127.0.0.1` by default. | The apps; it drives the browser over DevTools (`browser:9223`), the workroom over `/run/workroom/agent.sock`, the CLIs over `cli-net`, and the updater over `updater-net`. Its own outward reads go through `netguard.py` / `watchers.public_get`. |
| `browser` (uid 1000, **no env_file**, only `browser-net` which is `internal`) | A malicious page: no route anywhere except the egress proxy; holds no secrets; the profile lives in a volume; runs `--no-sandbox` because "the container is the sandbox" (`entrypoint.sh`). Downloads go to a volume the server mounts read-only and are deleted after one day. Policy in `docker/chromium-policy.json`. | `tools/browser.py` (Playwright `connect_over_cdp`), `tools/live.py` (screencast and taking control), `tools/downloads.py`, `browser_identity.py` (runs next to it), noVNC viewer on 6080. |
| `egress` (uid 1004, no keys) | LAN, host, CGNAT, link-local and metadata access from pages: resolves the name itself, refuses if *any* address is non-public, connects to the address it checked (no DNS rebinding), and blocks Google background hosts. Also relays the noVNC port. | The browser's only way out. |
| `workroom` (uid 1003, `network_mode: none`, read-only root, 2 GB RAM, `pids_limit: 512`) | Prompt-injected code: no network, no secrets, scratch `/work` emptied before and after every job, `sweep()` kills every leftover process, `RLIMIT_FSIZE`, not dumpable (`prctl`). Installs go only through the server's `InstallProxy` (proxy.sock, registries only, one-time password). | `workroom.py` tools (`run_code`, install), document and media generation (LibreOffice, ffmpeg, ImageMagick, poppler in `docker/workroom.Dockerfile`). |
| `claude-cli`, `agy-cli` (uid 1002, `cli-net` only, no env_file, own home volume) | Credential separation: the subscription login is never visible to the server or browser, and model keys are never visible to the CLI. A page cannot reach a logged-in CLI (they are not on `agent`/`browser-net`). | `providers/cli_remote.py`, `cli_agents.py`, `agy_pool.py` (warm sessions), `cli_login.py` (phone sign-in), `cli_update.py` (in-app CLI update into the home volume). |
| `updater` (only container with the Docker socket, `updater-net` internal, fixed commands, `UPDATER_TOKEN`) | Keeps Docker control away from the AI-facing server. Verifies digest, cosign signature and version label (`release.py`). | `server_updates.py`, `updates.py` (backup, rollback, schema fingerprint). |
| `cloudflared` / `kelo-tunnel` (`agent` network only) | Tunnel credentials stay out of the server. | `connect.py` modes `cloudflare` and `kelo`. |
| `caddy` (`NET_BIND_SERVICE` only) | TLS for the `direct` mode on 80/443. | `connect.py` `domain --direct`. |

Also enforced by Docker today: the RAM/CPU limits in `limits.py` (`SERVER_MEMORY`, `WORKROOM_MEMORY`, …), `quota.cgroup_limits()`,
and `agy_pool.max_sessions()`, which reads the cgroup cap.

---

## 3. Target architecture

```
 Kelo.app / Kelo.exe (desktop client, tray / menu bar)  ── controls ──┐
                                                                      ▼
 launchd (macOS) / Task Scheduler or service (Windows) → kelo-supervisor (Python, part of the runtime pack)
   ├─ server        uvicorn assistant.server:app_factory  127.0.0.1:<port>   (owner account; holds secrets)
   ├─ egress        python -m assistant.egress            127.0.0.1:<rand>   (no secrets)
   ├─ browser-host  python -m assistant.native.browser_host  → Chromium --headless=new --remote-debugging-pipe
   │                 (DevTools gate ws://127.0.0.1:<rand>/<secret>; downloads cleanup; identity)
   ├─ workroom      python workroom_agent.py (macOS: each job under sandbox-exec; Windows: AppContainer + Job Object)
   ├─ cli-claude / cli-agy   uvicorn assistant.cli_runner (127.0.0.1:<rand> + CLI_RUNNER_TOKEN; own config dir; Seatbelt on macOS)
   └─ tunnel        cloudflared (kelo / cloudflare modes), when connected
```

| Process | Standard (macOS, no admin) | Hardened (macOS, admin once) | Windows phase 2/3 |
|---|---|---|---|
| server, egress, supervisor | owner, LaunchAgent | owner, LaunchAgent | owner, logon task |
| browser-host + Chromium | owner; Chromium sandbox; proxy by flags and policy | `_kelobrowser` (LaunchDaemon, `UserName`); `pf` allows only loopback to the egress port | owner; Chromium sandbox; optional per-exe firewall rule (admin) |
| workroom | owner + Seatbelt per job | `_kelowork` + Seatbelt per job | off (phase 2); AppContainer + Job Object (phase 3) |
| CLI runners | owner + Seatbelt (no read of Kelo state or ~) | same | owner, environment scrubbed (no sandbox) |

### 3.1 Supervisor

There is no Docker restart policy natively, so a small `assistant/native/supervisor.py` takes its place. It starts the children,
restarts them with backoff, and checks the same health endpoints the compose healthchecks use (`/healthz`, `agent.sock` `health`,
`/healthz` on the runners, a TCP probe on egress). It writes logs with rotation (3 × 10 MB, as compose does) and exits cleanly on
stop. On Windows, every child goes into one Job Object with `KILL_ON_JOB_CLOSE`, so nothing outlives the supervisor. On macOS in
Hardened mode, the per-account parts are separate launchd jobs with `KeepAlive`, and the supervisor only watches them.

---

## 4. Components

### 4.1 Browser

**Binary.** Bundle a Chromium build. Do not use the owner's Google Chrome: Chrome policy is machine-wide (`HKLM\...\Google\Chrome`,
or managed preferences for `com.google.Chrome`), so locking the proxy would also lock the owner's personal browsing. Sources:

| Source | Licence / redistribution | Codecs (H.264/AAC) | Notes |
|---|---|---|---|
| Chrome for Testing (Google) | **[verify]** terms for redistribution | Google has a commit "Enable proprietary codecs for Google Chrome for Testing"; whether shipped CfT binaries carry them is **[verify]** | Playwright's Chromium is CfT since about v1.49 **[verify]**. No auto-updater. |
| Our own CI build of open-source Chromium | BSD + third-party (redistributable) | Off unless `proprietary_codecs=true` and `ffmpeg_branding="Chrome"`, which needs patent licences (Via LA) | Hours per build on a big machine **[guess]**. Full control. |
| Third-party builds (e.g. ungoogled-chromium) | Varies | Usually off | Supply-chain trust. |
| "Use my installed Chrome" (advanced) | No redistribution | Yes | Flags only, no policy; weaker. |

Without proprietary codecs, some video sites will not play. Kelo's agent rarely needs video, so this is acceptable for v1, but it is
a product decision (section 13).

**Run mode.** `--headless=new`, which is the same browser code as headful since Chrome 112. On macOS a hidden account has no
WindowServer session, so Hardened mode *needs* headless. In Standard mode, headless keeps a Kelo window off the owner's desktop.
The phone's live view is not affected: `tools/live.py` already uses `Page.startScreencast`. The noVNC viewer (`EGRESS_RELAY`, Xvfb,
x11vnc) is dropped natively. Today's Docker browser also has no GPU (Xvfb), so the fingerprint posture is similar. Still,
bot-check behaviour under headless is the biggest product risk. **Spike 0.1** checks it against the vendors listed in
`tools/challenge.py`.

**Identity.** `browser_identity.py` hard-codes `platform "Linux"` and the Linux UA. Natively it must report the real OS (macOS or
Windows UA, `Sec-CH-UA-Platform`) and keep the "Google Chrome" brand list. It moves from a shell loop in `entrypoint.sh` into
browser-host.

**Sandbox.** Keep Chromium's own sandbox on (natively there is no `--no-sandbox`). Renderers on macOS (Seatbelt) and Windows
(restricted token / AppContainer) cannot open sockets or read the user's files. Do not run Chromium under `sandbox-exec`: Firefox
under an outer `sandbox-exec` crashes with `forbidden-sandbox-reinit` because its children call `sandbox_init()` again
([Mozilla bug 1415159](https://bugzilla.mozilla.org/show_bug.cgi?id=1415159)). Chromium sandboxes its helpers the same way
([design doc](https://chromium.googlesource.com/chromium/src/+/HEAD/sandbox/mac/seatbelt_sandbox_design.md)), so the same failure
is expected **[verify, spike 0.2]**. The OS-level fence for the browser therefore has to be an account plus a packet filter, not
Seatbelt.

**Profile and downloads.** Profile: `<data>/browser/profile` (mode 0700, owned by `_kelobrowser` in Hardened mode). Downloads:
`<data>/browser/downloads`. In Hardened mode that folder is group-readable by the owner's group, which replaces the read-only
volume mount. Set it with policy `DownloadDirectory`, the existing CDP `Browser.setDownloadBehavior` (`tools/downloads.py`) and
`PromptForDownloadLocation: false`. The one-day cleanup loop moves from `entrypoint.sh` into browser-host. Executables are
already cancelled over CDP. On macOS, downloads carry the quarantine attribute, and on Windows the Mark-of-the-Web; Kelo never
opens them.

**DevTools.** Today `9223` is reachable only on `browser-net`. Natively, `--remote-debugging-port` on loopback could be reached by
*any* local process or user, and DevTools access means full control of every signed-in site. Design: browser-host launches
Chromium with `--remote-debugging-pipe`. It serves exactly one websocket on `127.0.0.1:<random>` at `/devtools/browser/<256-bit
secret>`, with no `/json/*` listing, and relays it to the pipe. The secret is handed to the server through the supervisor and never
written to the env file. `tools/browser.py._ensure_browser()` changes from `http://{ip}:{port}` to a new `BROWSER_CDP_URL`
(Playwright `connect_over_cdp` accepts a `ws://` endpoint). In Standard mode, the server could instead launch Chromium itself
over the pipe. Keeping browser-host as a separate process means the same code serves both modes. (Chrome 136+ also refuses
`--remote-debugging-port` on the default profile **[verify]**; we never use the default profile.)

**Policy and flags.**

- Flags, which work without admin: `--proxy-server=http://127.0.0.1:<egress>`, `--proxy-bypass-list=<-loopback>` (removes
  Chromium's implicit bypass for localhost, so loopback also goes to egress, which refuses it), `--disable-quic`,
  `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`, `--disable-extensions`, plus today's language and cache flags.
- Policy: the existing `docker/chromium-policy.json`, plus `ProxySettings` (`fixed_servers`, same server and bypass list),
  `ExtensionInstallBlocklist: ["*"]`, `URLBlocklist` + `file://*`, and `DownloadDirectory`.
- Where policy goes. Windows: `HKLM\SOFTWARE\Policies\Chromium` (admin, written by the per-machine installer) or
  `HKCU\SOFTWARE\Policies\Chromium` (whether a standard user can write it is **[verify]**). This applies to every
  Chromium-branded browser on that PC **[verify]**. macOS: Chromium reads policy as *mandatory* only from managed preferences
  (`/Library/Managed Preferences/<bundle id>.plist`, normally written by MDM or a configuration profile). Values in
  `/Library/Preferences` are treated as *recommended* **[verify]**. Plan: flags are the primary mechanism in Standard mode.
  Policy is written as mandatory where the installer has admin, and `chrome://policy` is checked in spike 0.2. Give the bundled
  Chromium its own bundle ID (`chat.kelo.browser`) so its policy domain is ours alone.

### 4.2 Egress without containers

**Reuse.** `assistant/egress.py` is already stand-alone (standard library plus `netguard.is_public`). Changes:

- Add `EGRESS_BIND`, default `127.0.0.1` natively; today it binds `0.0.0.0` (`main()` and `relay()`). On a LAN-facing machine,
  `0.0.0.0` would make it an open proxy to the public internet.
- Port `0` gives a random port, reported to the supervisor. Drop `EGRESS_RELAY` natively.
- Optional: run more than one proxy (browser, CLI allowlist), each with its own rules.

**Why a page can hardly get around it.** In Chromium, renderers never open sockets. Every page request (fetch, XHR, WebSocket,
navigation, subresources, service workers, beacons, prefetch, downloads) is an IPC call to the **network service**, a separate
process. The network service applies the browser-wide proxy configuration that the browser process set from policy or flags. The
renderer can name a URL but has no say in how it is routed. With QUIC off there is no UDP path for HTTP/3. With WebRTC
`disable_non_proxied_udp`, media and data channels go only through the proxy (TURN over TCP/TLS) or not at all. Without the
implicit loopback bypass, `127.0.0.1` and `localhost` are proxied and refused. Bluetooth, USB, serial, HID, the media router and
sensors are denied by policy, so the remaining LAN discovery paths (mDNS/SSDP for cast) are off. A page that wants the LAN
therefore needs a **second bug**: a renderer exploit *and* a sandbox escape, or a network-service compromise. Whether the network
service itself is sandboxed is believed true on macOS, while the Windows rollout state is **[verify]**.

**What it does not cover.**

- Code execution in the browser's OS account (after a sandbox escape). The proxy is then only a convention. In Standard mode that
  code can reach the LAN directly and read the owner's files. Only Hardened mode stops it: on macOS the `_kelobrowser` account plus
  a `pf` rule, on Windows a per-SID WFP filter or per-exe firewall rule.
- Exfiltration to the public internet. That is allowed by design, as with Docker.
- DNS lookups made by the browser process itself (proxy host is an IP literal; DoH off). Minor.
- Anything a local process does with the proxy. It only restricts, so this gains nothing.
- The server's own outbound requests. These use `netguard.py` / `watchers.public_get` in code, same as today. The server can reach
  the LAN in Docker too.

**OS fence in Hardened mode.**

- macOS: `pf` rules matching `user _kelobrowser` (pass out on `lo0` to the egress port, block everything else). They are loaded
  into a sub-anchor of the stock `anchor "com.apple/*"`, so `/etc/pf.conf` is not edited, and enabled with a `pfctl -E` reference
  token by a root LaunchDaemon at boot. Whether `user` matching works for outbound sockets on current macOS is **[verify, spike
  0.3]**. The fallback is a Network Extension content filter, which needs a system extension, an Apple entitlement and user
  approval: heavier.
- Windows: an outbound block rule for Kelo's `chrome.exe` path covering all remote addresses *except* `127.0.0.1` (block rules
  beat allow rules in Windows Firewall, so exclude by address range), created by the per-machine MSI. Alternatively, follow
  sandbox-runtime's Windows approach and filter WFP `ALE_AUTH_CONNECT` by account SID (section 4.3).

### 4.3 Workroom (code execution)

**What has to change in `workroom_agent.py` regardless of OS.**

- `sweep()` kills every PID in `/proc`, which is only valid in a container. Natively it must kill only this job's processes.
- `job_env()` `PATH` must point at the bundled runtimes instead of `/usr/bin`.
- `prctl` is Linux-only (already guarded).
- `/opt/workroom/build` becomes the runtime pack version.
- The socket folder `/run/workroom` moves to the data folder.
- On Windows, `agent.sock` and `proxy.sock` (`SocketRunner`, `InstallProxy`) become loopback TCP with a bearer token or a named
  pipe. Python's `AF_UNIX` on Windows is not something to rely on **[verify]**.

**Tools pack.** The Docker workroom carries Python with numpy, pandas, scipy, matplotlib, seaborn, plotly, openpyxl, xlsxwriter,
python-docx, python-pptx, pypdf and pillow, plus Node, LibreOffice, ffmpeg, ImageMagick, poppler and Noto fonts. Natively this is
an optional **workroom pack**, downloaded on first use (sizes in section 5.2).

#### macOS: Seatbelt

Anthropic's **sandbox-runtime** (`srt`; npm `@anthropic-ai/sandbox-runtime`; **Apache-2.0**; "Beta Research Preview"; repository
<https://github.com/anthropic-experimental/sandbox-runtime>, whose README names `github.com/anthropics/sandbox-runtime` as the
canonical location) does this:

- **macOS**: `sandbox-exec` with a Seatbelt profile generated per run. Writes are denied unless allowed. Reads are *allowed unless
  denied* (`denyRead`/`allowRead`). There is a mandatory deny list (`.bashrc`, `.zshrc`, `.git/hooks`, `.git/config`, …). Network
  is only to the localhost ports of its own HTTP and SOCKS5 proxies. Violations are read from the macOS sandbox log.
- **Linux**: bubblewrap with the network namespace removed; proxies are bridged in over Unix sockets; seccomp blocks Unix sockets.
- **Windows (alpha)**: a dedicated `srt-sandbox` local user, a WFP `ALE_AUTH_CONNECT` filter keyed on that SID (only loopback to
  the proxy port range), a write-restricted token, and additive ACEs on writable paths. Known gaps it documents: DNS through the
  system resolver is not fenced, and Schannel-based tools cannot complete HTTPS (Python and Node, with their own TLS, work).
- **Proxy rules**: domain allow/deny lists, plus a resolved-address check that refuses loopback, link-local, metadata and the
  host's own addresses. That check is the same idea as `netguard.resolve_public`. It warns that domain filtering does not inspect
  traffic and that domain fronting may get around it.

**Use it or copy it?** Copy the approach and write it in Python as `assistant/native/seatbelt.py`. Reasons:

- Kelo already has the proxy part (`InstallProxy`: registries only, one-time password, CONNECT only, public addresses only).
- Our runs need *no* network at all, which is simpler than srt's default.
- We want reads denied for the owner's home *and* Kelo's state. That has to be added to srt's allow-reads default anyway.
- Calling `srt` would put Node and a beta API on the critical path of every job.

Apache-2.0 is compatible with AGPL-3.0. If any profile text is copied, credit it in `NOTICE`. Keep `srt` as a test oracle: run the
same job under both and compare.

Profile per job (illustrative; the real one comes out of spike 0.4):

```scheme
(version 1)
(deny default)
(allow process-fork)
(allow process-exec (subpath "<runtime>/workroom") (subpath "/bin") (subpath "/usr/bin"))
(allow file-read* (subpath "/System") (subpath "/usr/lib") (subpath "/Library/Fonts") (subpath "<runtime>/workroom")
                  (subpath "<tools>") (subpath "<job>"))
(allow file-write* (subpath "<job>"))                 ; + (subpath "<tools>") only for an install
(allow sysctl-read) (allow mach-lookup (global-name "com.apple.system.logger")) ; the minimum the runtimes need [verify]
(deny network*)                                        ; install: (allow network-outbound (remote ip "localhost:<fwd port>"))
```

- **A folder per job** (`<data>/workroom/jobs/<random>`), deleted afterwards. A process left behind by job N can only touch job
  N's folder, which no longer exists, so it cannot read job N+1's input files.
- **Leftover processes.** Seatbelt does not stop `setsid` or double-fork. In Standard mode, after each job kill the process group
  *and* every process whose executable or working folder is under the runtime or job path (`proc_pidinfo`). In Hardened mode the
  agent runs as `_kelowork`, and `kill(-1, SIGKILL)` kills that account's processes, which is the exact native twin of `sweep()`.
- **Limits.** `RLIMIT_FSIZE` and the `tree_bytes` disk watch work as they are. macOS does not enforce `RLIMIT_AS`, so add an RSS
  watch (kill above `WORKROOM_MEMORY`) and `taskpolicy -b` (background QoS) for CPU.
- **LibreOffice** under `(deny default)` is the compatibility risk. It needs fonts, `mach-lookup` services and possibly
  `ipc-posix-shm`. Spike 0.4.
- **Risk:** `sandbox-exec(1)` has been marked deprecated for years, yet it still ships and is used by Chrome-less tools (Bazel, Nix,
  srt). If Apple removes it, the fallback is a signed helper with the App Sandbox entitlement and no `network.client`. Children
  inherit that sandbox. Installs would then move to the server, which would fetch wheels through netguard and install with
  `--no-index`.

#### Windows: options

| Option | Files of the owner | Network | Admin / install | Cost and risks |
|---|---|---|---|---|
| **AppContainer (LPAC) + Job Object** | Denied: user-profile ACLs do not grant the AppContainer SID; grant only the job and tools folders | None without capabilities (loopback is blocked too). For installs, `internetClient` without `privateNetworkClientServer` reaches the public internet but not the LAN or loopback **[verify]** | None (profile via `CreateAppContainerProfile`) | Python, Node, LibreOffice and ffmpeg inside AppContainer need testing; installs reach all public hosts, not just registries (keep `--ignore-scripts`, prefer binaries) |
| Restricted token / Low integrity only | **Readable** (reads are not restricted) | Not restricted | None | Not enough on its own |
| Dedicated local user + WFP by SID (srt's way) | Denied by profile ACLs | Only loopback to the proxy port, kernel-enforced | Admin once; a password to store, or S4U | srt's Windows backend is alpha; account management on Home editions |
| WSL 2 work room | Denied if `automount` and `interop` are off in `/etc/wsl.conf` | bwrap without network namespace inside | WSL install (admin, virtualization, maybe reboot) | Reuses `docker/workroom.Dockerfile` as a rootfs (`wsl --import`); heavy prerequisite |
| Off at first | n/a | n/a | n/a | Agent loses `run_code`; document making falls back to server-side Python libraries |

**Recommendation.** Phase 2 ships with the work room off on native Windows, and the tool list says so (`workroom.NOT_RUNNING`
gets a native text). Phase 3 adds an **AppContainer (LPAC) runner with a Job Object** for memory, CPU, process count and
kill-on-close. It is a kernel boundary, it is what Chromium uses for its own renderers, it needs no admin, and it works on Home
editions. A WSL work room is an opt-in for power users later. `bash` does not exist natively, so the Windows work room offers
`python` and `node` only, and the tool description says so.

### 4.4 CLI sidecars (claude-code, agy)

- **Running.** `cli_runner.py` is already a stand-alone uvicorn app, and `providers/cli_remote.py` already talks to it by URL plus
  `CLI_RUNNER_TOKEN`. Natively, the supervisor starts one runner per enabled CLI on `127.0.0.1:<random>` and sets
  `ASSISTANT_CLI_CLAUDE_URL` / `ASSISTANT_CLI_AGY_URL`. `cli_manage.in_container()` (`/.dockerenv`) becomes a runtime-mode check.
- **Not redistributable.** The CLI programs may not be shipped (`release.NEVER_PUBLISHED`). Natively they are installed on the
  owner's machine at first sign-in, the way `cli_update.py` already does: npm with a bundled Node for claude (or Claude Code's
  native installer **[verify]**), and Google's installer for agy. They go into `<data>/cli/<kind>/<version>/`. `cli_manage` says
  agy is installable on darwin and linux only, so **agy on native Windows is [verify]**.
- **Credential separation.**
  - Set `CLAUDE_CONFIG_DIR=<data>/cli/claude/home` and `ANTIGRAVITY_CONFIG_DIR=<data>/cli/agy/home`. `cli_manage.cli_logout`
    already treats agy with `ANTIGRAVITY_CONFIG_DIR` as file-based instead of using the macOS keychain item `gemini/antigravity`.
    Whether claude-code on macOS still uses the login Keychain when `CLAUDE_CONFIG_DIR` is set is **[verify, spike 0.5]**. If it
    does, Kelo's login and the owner's personal Claude Code login could collide.
  - The runner environment is already scrubbed by `claude_env()` (allow-list) and `wrap_agy_child_env`.
  - On macOS, each runner gets a Seatbelt profile. It may read the system and its own install and home. It may write its own home
    and tmp. It may not read `~` or Kelo's state, so no API keys, DB, vault or other CLI's login. Network stays open, as `cli-net`
    is today. This also contains the "agy explored files" behaviour seen before: natively, an unconfined agy would see the owner's
    home.
  - On Windows (phase 2) there is no sandbox, only the environment scrub and its own config folder. This is weaker than Docker and
    is shown in section 9.
- **Keep-alive pool.** `agy_pool.max_sessions()` uses the cgroup cap, which is `None` natively and so falls back to 3 sessions,
  about 750 MB. Change it to use `quota.host_capacity()` (made OS-aware) so a small Mac keeps fewer. Sleep and wake kill or
  strand agy processes. The existing "replace a worker that ended" logic covers this, but add a wake hook (supervisor →
  `/warm` or a restart of the runner) **[guess]**.

### 4.5 Running in the background and staying awake

- **macOS.**
  - Standard: the server is a LaunchAgent registered with `SMAppService.agent`. Its plist lives in the app bundle at
    `Contents/Library/LaunchAgents` and points to a stable launcher in the bundle, which execs the current runtime version.
  - Hardened: `SMAppService.daemon` jobs with `UserName` for the browser and workroom accounts. The owner approves them once in
    System Settings → Login Items. A root one-shot job creates the hidden accounts (`sysadminctl`/`dscl`, `IsHidden`, no login
    shell) and loads the `pf` anchor.
  - The JVM client needs a small signed Swift helper for `SMAppService` calls **[guess]**. Today `Autostart.kt` writes
    `~/Library/LaunchAgents` itself, which still works but shows the "background item added" notice either way.
  - The menu bar: the desktop tray (AWT `SystemTray`) already exists. Add server status, start/stop and "keep awake".
- **Windows.**
  - Phase 2: a per-user Task Scheduler task "at log on", restart on failure. The MSI is already per-user; the MSIX uses
    `windows.startupTask`.
  - Phase 3 option: a Windows service under a virtual account (`NT SERVICE\KeloServer`), so it runs without sign-in. That needs
    the per-machine MSI. The tray is already in the app.
- **Preventing sleep.**
  - macOS: `IOPMAssertionCreateWithName(kIOPMAssertPreventUserIdleSystemSleep)` while serving (the same as `caffeinate -i`),
    optionally only on AC power. A MacBook with the lid closed still sleeps unless it is in clamshell mode with a display.
    FileVault: after a reboot nothing runs until someone signs in. Hardened daemons too, since the data is in a user-unlocked
    volume **[verify]**.
  - Windows: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`. Modern Standby laptops may still drop network.
  - Show both limits in the app instead of promising 24/7.

### 4.6 Where data lives

| | macOS | Windows |
|---|---|---|
| Client (exists) | `~/Library/Application Support/Kelo` | `%APPDATA%\Kelo` |
| Server state (`ASSISTANT_HOME`: DB, home, uploads, packages) | `~/Library/Application Support/Kelo/server/main/state` | `%LOCALAPPDATA%\Kelo\server\main\state` |
| Runtime pack versions | `~/Library/Application Support/Kelo/runtime/<ver>/` (Hardened: `/Library/Application Support/Kelo/runtime`, root-owned) | `%LOCALAPPDATA%\Kelo\runtime\<ver>\` |
| Browser profile + downloads | `…/Kelo/browser/` (Hardened: owned by `_kelobrowser`) | `%LOCALAPPDATA%\Kelo\browser\` |
| Workroom jobs and tools | `…/Kelo/workroom/{jobs,tools}` | `%LOCALAPPDATA%\Kelo\workroom\` (ACL'd for the AppContainer SID) |
| CLI homes | `…/Kelo/cli/{claude,agy}/` | `%LOCALAPPDATA%\Kelo\cli\` |
| Logs | `~/Library/Logs/Kelo/` | `%LOCALAPPDATA%\Kelo\logs\` |
| Secrets (`ASSISTANT_TOKEN`, `ASSISTANT_SECRET_KEY`, vault master key) | Keychain item (Standard); 0600 file under the server account (Hardened) | DPAPI-sealed file, as the desktop client already does for its token |

Settings stay in the same env-file format (`<state>/../kelo.env`), so `Settings.from_env` does not change. Only the secrets move
out of the file.

### 4.7 Reaching the server from the phone (`connect.py`)

- **kelo** (`<name>.kelo.chat`): bundle `cloudflared` (Apache-2.0, about 40 MB **[guess]**) and run it under the supervisor with
  `TUNNEL_TOKEN` from `connect/kelo/tunnel.env`. This replaces the `kelo-tunnel` compose profile; the token still never reaches the
  server.
- **cloudflare** (own domain): the same binary with `--config` from the copy's folder.
- **tailscale / funnel**: unchanged. Use the owner's Tailscale. On macOS the CLI may live inside `Tailscale.app`, so `which` needs
  a fallback path **[verify]**.
- **direct** (Caddy on 80/443): leave it out of native v1. It needs router port forwarding and inbound firewall rules, and on
  macOS it binds privileged ports.
- In `connect.py`, the calls `m.compose(..., "--profile", ...)` become calls to a process-backend interface.

### 4.8 Several copies on one computer

Today `instances.py` runs any number of copies as compose projects, each with its own port, token and data. Natively, each copy
would need its own supervisor, browser, egress and workroom, and in Hardened mode its own accounts. That is a lot of machinery for
what is mostly a server/NAS use case. **Recommendation: drop it for native.** There is one server per OS user account; a second
macOS or Windows user on the same computer can run their own (ports are random, data is per user). The data layout keeps the name
`main`, so it can come back later. `assistant instance …` prints "use the Docker install for several people on one computer".
This is a change from the deployment model in the owner's notes, so it is listed in section 13.

---

## 5. Packaging

### 5.1 Python

| Option | Fit |
|---|---|
| **python-build-standalone** (astral-sh; what `uv python install` uses) + `uv venv --relocatable` + `uv sync --frozen` | **Recommended.** A normal `site-packages`, so pure-Python updates are file copies. Every `.so`/`.pyd` can be signed one by one. The same lockfile (`uv.lock`) as Docker. |
| PyInstaller | One-file mode extracts to temp, which is awkward for notarization and a frequent antivirus false positive on Windows **[guess, common report]**. Hard to patch partially. |
| Briefcase (BeeWare) | Good, but brings its own app packaging, and we already have the Compose app for that. |

Dependencies that matter natively:

- `playwright` ships a Node driver of about 50–100 MB **[guess]**. We use it only for `connect_over_cdp`, not its browsers.
- `av` (PyAV) carries ffmpeg libraries.
- `pillow-heif`, `cryptography`.
- `pdftotext` (poppler) is optional in `documents.py`.

### 5.2 Size estimates (all **[guess]**, to be measured in spike 0.6)

| Part | Unpacked | Download (compressed) |
|---|---|---|
| Desktop client (exists: JVM via jlink + Compose/Skia) | 150–200 MB | 80–110 MB |
| Server runtime pack (Python + deps + Kelo + egress + cloudflared) | 250–350 MB | 90–130 MB |
| Chromium | 350–500 MB | 130–180 MB |
| Workroom pack (Python science stack, Node, LibreOffice, ffmpeg, ImageMagick, poppler, Noto CJK) | 1.3–1.8 GB | 500–750 MB |
| **First install with server switched on, no workroom** | ~0.8–1.0 GB | ~300–420 MB |
| **Everything** | ~2.1–2.8 GB | ~0.8–1.2 GB |

The Docker images today are of the same order. The difference is that native downloads are split, so a client-only user never
pays for the rest. Mac architectures: arm64 and x86_64 as separate packs, not universal, to halve downloads.

### 5.3 Signing and notarization

- **macOS** (none of this exists yet: `tools/build/README.md` notes no Developer ID certificate yet).
  - Apple Developer Program, a "Developer ID Application" certificate, hardened runtime and a secure timestamp on *every* Mach-O:
    Python, each `.so`, Node, cloudflared, and every Chromium helper (re-signed with our identity).
  - Entitlements **[verify]**: Chromium needs `cs.allow-jit` (V8). Python may need `cs.allow-unsigned-executable-memory` for
    cffi/ctypes. Avoid `cs.disable-library-validation` by signing everything ourselves.
  - Submit with `xcrun notarytool` and staple the DMG. `tools/build/desktop.sh` already does that for the client.
  - Runtime packs are zipped, signed, notarized and verified by our updater with minisign. They are downloaded without the
    quarantine flag, so Gatekeeper does not re-check them, but code signatures still apply on Apple silicon.
  - A Developer ID pkg installer ("Developer ID Installer" certificate) is only needed if we ever ship a `.pkg`. Hardened mode
    uses SMAppService from the app instead.
- **Windows.**
  - Authenticode-sign the MSI/EXE and every `.exe`/`.dll`/`.pyd` we ship: an OV/EV certificate on a hardware token or cloud HSM,
    or Azure Trusted/Artifact Signing (eligibility **[verify]**). Until reputation builds, SmartScreen still warns; EV no longer
    skips this **[verify]**.
  - The Store MSIX is signed by the Store. Downloading a runtime pack into a Store app may conflict with Store policy on
    downloading code **[verify]**, so the Store build stays client-only.

---

## 6. Updates without Docker images

**Manifest.** `latest.json` already carries `"server"` (image digests, `release.py`). Add `"server_native"`:

```json
{"version": "0.6.0", "compose_level": 1, "min_app": {...},
 "packs": {"mac-arm64": {"url": "...", "sha256": "...", "size": 0},
           "win-x64":   {"url": "...", "sha256": "...", "size": 0}},
 "chromium": {"mac-arm64": {"version": "157.0.x", "url": "...", "sha256": "..."}, "win-x64": {...}},
 "workroom": {"mac-arm64": {"version": "2026.11.01", "url": "...", "sha256": "..."}}}
```

**Signing.** cosign keyless (used for images) needs an online CA and log. For native packs, sign the manifest with **minisign
(Ed25519)**, in the minisign format. Pin one public key in the client, and verify it in Kotlin
with the JDK's built-in Ed25519 (JDK 15+; the desktop builds on 17). Keep `release.py`'s trust rules:

- the manifest only names files (host allow-list, sha256, size);
- nothing in it is executed except the verified pack;
- the version must be newer unless it is an explicit rollback;
- `min_app` and `compose_level` gates still apply.

**Flow.** The native twin of `updates.Updater` gets a process backend instead of compose.

1. Preflight (`/api/about`, free disk).
2. Download and verify into `runtime/<new>/`.
3. Back up with the same HELPER code (`fp`, `schema`, `backup`), run directly with `KELO_STATE` set, instead of
   `docker compose exec`.
4. Stop children, flip `runtime/current` to `<new>`, start, and health-check (`/healthz`, `/api/about` migrations, `GET
   /api/profile`).
5. On failure: flip back. Restore the backup only when the schema fingerprint changed, which is **the migration rule in
   `updates.py`, unchanged**.

Keep 2 runtime versions on disk, matching `KEEP_BACKUPS` = 3 for data. `server_updates.py` keeps its API, and `UpdaterClient`
talks to the supervisor's local control endpoint instead of `http://updater:9400`. The auto-update night window logic stays as
it is.

**Client app.**

- macOS: either (a) extend `Updates.kt` from "download DMG, check sha256, open it" to "verify minisign, replace the bundle, relaunch"
  (the bundle is user-owned after drag-install), or (b) adopt **Sparkle 2** (MIT; EdDSA appcasts, deltas). Sparkle is an
  Objective-C framework, so a JVM app needs a native shim, or Sparkle's `sparkle-cli` **[verify]**. Recommendation: (a) now, since
  it covers both OSes with one code path; (b) if delta updates become important.
- Windows:
  - MSI/EXE: the self-updater downloads the MSI, checks the signature and sha256, and runs `msiexec` (the fixed `upgradeUuid`
    is already set).
  - MSIX outside the Store: an `.appinstaller` file gives Windows-driven updates, but MSIX cannot create accounts or firewall
    rules and runs per user. Fine for the client and for server phase 2 without hardening.
  - winget: a distribution channel (manifest PR to `winget-pkgs`). Updates are user-driven (`winget upgrade`); add after GA.

**Chromium** gets its own channel. Chrome stable ships security fixes about weekly, so a bundled browser older than a few weeks is
a liability. The updater swaps `browser/<ver>/` the same way, and restarts only browser-host. The Docker path has the same issue
today, since a rebuild is needed to get Debian's Chromium fixes.

**Data migrations** are unchanged: in-place, additive at start-up (`store.py`), with no down-migration. Moving an existing Docker
install to native needs `assistant export` (volume → tar of `/state`) and a native import. That is new code, and section 13 asks
whether it is wanted.

---

## 7. Relation to the existing desktop app

| | One app ("use this computer as the Kelo server") | Separate "Kelo Server" app |
|---|---|---|
| Install | One download; the server pack is fetched on demand | Two installs, two icons |
| Pairing | Automatic: the client reads the local token, no QR | QR or link as today |
| Tray, start at sign-in, single instance, updates, 20 languages | Already in `apps/desktop/` | Rebuilt or duplicated |
| Signing and notarization pipelines | One app + packs | Two apps |
| Coupling | The client must not be needed for the server to run: the server is its own launchd/Task Scheduler job | Natural |
| Store builds | Store build hides the server switch | Server app is never in a Store |

**Recommendation: one app.** The switch lives under My → this device. The client is only the controller; quitting it does not stop
the server. The server pack, Chromium and the workroom pack are separate signed downloads, so client-only users stay small.
Desktop work: a new `apps/desktop/src/main/kotlin/chat/kelo/desktop/server/` package (pack download and verify, supervisor control,
status, protection level, keep-awake, Hardened set-up) and strings in all 20 languages. Android stays the UI reference; the server
switch is desktop-only by nature.

---

## 8. What stays on Docker, and how both paths share code

Docker stays for Linux servers, NAS boxes, the cloud, multi-person computers, and anyone who prefers it. `compose.yml`,
`instances.py`, `updater_service.py` and `install.sh`/`install.ps1` stay as they are. `install.ps1` keeps "Docker Desktop + WSL 2"
as the Windows server route until native phase 3 is done.

Sharing:

- **One Python package, one lockfile, one version.** Add `KELO_RUNTIME=docker|native` (detected) and `assistant/native/` for the
  native-only parts. Native packs and images are built from the same tag in the same release workflow.
- **One settings format** (env keys read by `Settings.from_env`). Native-only keys:
  - `BROWSER_CDP_URL` (ws endpoint, replaces `BROWSER_CDP_HOST`/`PORT`);
  - `EGRESS_BIND`;
  - `WORKROOM_SANDBOX=seatbelt|appcontainer|off`;
  - `KELO_PROTECTION=standard|hardened`.
- **Move logic out of shell into Python.** `browser_host.py` does what the `browser)` branch of `entrypoint.sh` does today:
  flags, language list, cache cap, stale lock files, download cleanup, identity loop. The Docker entrypoint then calls the same
  module, and the two paths cannot drift.
- **A backend interface** (`docker` | `native`) behind `instances.Instances` / `updates.Updater` / `connect.py`, so `server_updates.py` and
  the app see one API.
- **Tests.** Run the existing pytest suite on macOS and Windows CI, plus new sandbox tests: a job tries to read `~`, write outside
  its folder, reach `192.168.0.1`, `127.0.0.1:<server>` and `1.1.1.1`, and survive after the job. Each must fail.

---

## 9. Security comparison

| Threat | Docker today | Native macOS Standard | Native macOS Hardened | Native Windows (phase 2 / 3) |
|---|---|---|---|---|
| **Malicious page** (exploit, drive-by) | Renderer runs with `--no-sandbox` inside a no-caps container in a VM; escape lands in the browser container (no secrets, no route) | Chromium sandbox on (stronger per-process than today); after renderer + sandbox escape: owner account, owner files and LAN | Chromium sandbox; escape lands in `_kelobrowser`: no owner files, `pf` allows only the proxy | Chromium sandbox; escape lands in the owner account. Optional per-exe firewall rule (admin) blocks direct network, not files |
| **Page or agent reaching the LAN (SSRF)** | Internal network + egress; `check_url` in code | Egress via flags + policy, `<-loopback>`, no QUIC, WebRTC proxied, `check_url`. Bypass needs a browser-process compromise | Same + `pf` per account (kernel) | Same as Standard; + firewall/WFP rule if admin |
| **Prompt-injected agent running code** | No network, no secrets, scratch emptied, all processes killed, 2 GB / 512 PIDs cap | Seatbelt per job: no network, writes only to the job folder, reads of `~` and Kelo state denied; leftover processes found by path; RSS watch | Same + `_kelowork` account (`kill -1`, file ACLs as a second wall) | Phase 2: no code execution. Phase 3: AppContainer (no network, no profile access) + Job Object limits |
| **Reading the owner's files** | Not mounted; separate VM | Browser: Chromium sandbox only. Workroom and CLIs: Seatbelt deny. Server: owner account (any owner-level malware can read Kelo data, as with any app) | Browser and workroom: other accounts. Server: owner | Browser and CLIs: owner account. Workroom (3): AppContainer |
| **Credential theft between components** | Server-only keys; CLI login only in its sidecar; browser none; updater alone has the Docker socket | Keys in Keychain or server state; CLI Seatbelt cannot read them; browser holds none but runs as owner (a browser escape could read keys) | Browser and workroom accounts cannot read server or CLI files | Same account for all except the workroom; separation by environment scrub and DPAPI only. **Weakest cell** |
| **DevTools hijack** (control of signed-in sites) | Port only on `browser-net` | Pipe + secret-path websocket on loopback; workroom cannot reach loopback | Same | Same; workroom (3) cannot reach loopback |
| **Update tampering** | Digest + cosign + version label | minisign manifest + sha256 + Developer ID/notarization | Same | minisign + Authenticode |

Net: **macOS Hardened ≈ Docker** for everything except "the whole thing sits in a VM". **macOS Standard** is good against pages
and code, and weaker against a full browser escape. **Windows phase 2** is clearly weaker for browser escapes and credential
separation, and should be described that way in the app (section 13).

---

## 10. What changes, file by file

| File / module | Change |
|---|---|
| **new** `server/src/assistant/native/` | `paths.py` (data folders per OS), `supervisor.py`, `browser_host.py` (launch, pipe, DevTools gate, downloads cleanup, identity), `seatbelt.py`, `appcontainer.py` (phase 3, ctypes), `power.py` (sleep assertions), `secrets_store.py` (Keychain/DPAPI), `accounts.py` (Hardened set-up), `pf.py`, `updater.py` (pack verify, flip, rollback) |
| `docker/entrypoint.sh` | `browser)` branch calls `python -m assistant.native.browser_host --docker` (shared logic) |
| `egress.py` | `EGRESS_BIND`, port 0 reporting, relay optional |
| `tools/browser.py` | `BROWSER_CDP_URL` ws endpoint in `_ensure_browser`; `check_url` also refuses `file:` |
| `browser_identity.py` | OS-correct platform and UA; callable as a library from browser_host |
| `docker/chromium-policy.json` | + `ProxySettings`, `ExtensionInstallBlocklist`, `file://*`, `DownloadDirectory` (also good for Docker) |
| `workroom_agent.py` | `sweep()` per backend; per-job folders; runtime `PATH`; sandbox wrapper hook; TCP/named-pipe transport on Windows; RSS watch |
| `workroom.py` | `SocketRunner`/`InstallProxy` transport per OS; native `NOT_RUNNING` text; Windows: python/node only |
| `server.py` (`Settings.from_env`) | new keys; `workroom_dir` / `browser_download_dir` from native paths |
| `providers/cli_manage.py`, `cli_update.py`, `cli_login.py` | runtime-mode check instead of `/.dockerenv`; per-CLI config dirs; install locations |
| `providers/agy_pool.py` | `max_sessions` from host RAM natively; wake hook |
| `quota.py` | `host_capacity()` via `sysctl hw.memsize` / `GlobalMemoryStatusEx`; `cgroup_limits()` returns empty natively |
| `limits.py` | native: storage cap only (as today); RAM/CPU for workroom via Job Object (Windows) or RSS watch (macOS); UI shows capacity numbers |
| `instances.py`, `manage.py` | backend interface; native = `main` only |
| `updates.py`, `server_updates.py`, `release.py`, `downloads.py` | native backend; `server_native` manifest section; minisign verify |
| `connect.py` | process backend for `kelo`/`cloudflare`; Tailscale path discovery; `direct` disabled natively |
| `cli.py` / `wizard.py` | native set-up runs from the app; `assistant` CLI still works against the native data folder |
| `apps/desktop/` | server switch, pack manager, Hardened set-up, keep-awake, status; Swift helper for SMAppService; `Updates.kt` signature check |
| `tools/build/` | `server-pack.sh` (mac), Windows workflow for packs; signing and notarization of packs |
| `docs/` | install, browser, connect pages for native |

---

## 11. Phased plan (effort is **[guess]**, one engineer)

| Phase | Content | Effort |
|---|---|---|
| **0. Spikes** | 0.1 headless Chromium vs the bot checks in `challenge.py`. 0.2 Chromium source/licence/codecs; mandatory policy on macOS; outer `sandbox-exec` confirmed broken. 0.3 `pf` `user` rules on current macOS. 0.4 Seatbelt profile running python/node/LibreOffice/ffmpeg. 0.5 claude-code and agy credential storage with config dirs on macOS. 0.6 measure pack sizes; notarize a Python + Chromium bundle | 2–3 weeks |
| **1. macOS native (Standard)** | `native/` package, supervisor, browser-host + DevTools gate, local egress, Seatbelt workroom, CLI runners with Seatbelt, data/secrets, cloudflared, desktop server switch, packs + signing + notarization, updater + rollback, docs, CI on macOS | 8–12 weeks |
| **1b. macOS Hardened** | hidden accounts via SMAppService daemons, `pf` anchor, file ownership/groups for downloads, set-up UI, uninstall | 3–5 weeks |
| **2. Windows, no code execution** | Chromium + policy (HKLM via per-machine MSI, or flags), egress, DevTools gate, claude runner (agy if it works), DPAPI secrets, logon task, keep-awake, MSI packs + Authenticode, updater, optional firewall rule | 6–9 weeks |
| **3. Windows code execution** | AppContainer (LPAC) runner + Job Object, ACL set-up, workroom pack for Windows, sandbox tests; later optional WSL work room (+3 weeks) | 5–8 weeks |

Total: about 6–9 months to all phases **[guess]**. Phase 1 alone gives Mac owners Kelo without Docker.

---

## 12. Risks

1. **Bot checks on headless Chromium** may succeed less often than today's headful Xvfb browser. That directly affects
   shopping and booking flows. Fallback: headful Chromium as the owner (Standard only), with its window hidden from view.
2. **Chromium supply**: redistribution terms of CfT, codec patents, and a weekly security cadence we must keep up with.
3. **`sandbox-exec` deprecation** on a future macOS. The fallback is the App Sandbox helper (4.3).
4. **`pf` on macOS** is lightly documented. A macOS update may change anchor behaviour, and other VPN or firewall apps share
   `pf`.
5. **Windows** is weak for credential separation and browser escape until hardening exists. Antivirus false positives on a
   bundled Python, and SmartScreen reputation for a new certificate.
6. **Size and disk.** Up to about 2.8 GB unpacked with everything, and laptops sleeping. Both need clear UI.
7. **Two deployment paths to test.** Mitigated by moving shell logic into shared Python and running CI on all three OSes.
8. **CLI vendor changes** (claude-code, agy) on native platforms, which are not under our control.

## 13. Open questions for the owner

1. Is **Standard** (no admin) acceptable as the default on macOS, with **Hardened** offered at set-up? Or should Hardened be
   required for the server switch?
2. Is it acceptable to **drop multiple copies** on native, keeping several people per computer as a Docker-only feature?
3. **Chromium source**: Chrome for Testing (pending its terms), our own CI build, or another build? Is missing H.264/AAC playback
   acceptable?
4. Should native Windows ship **without code execution** first (phase 2), labelled in the app?
5. One app with a server switch (recommended), or a separate server app?
6. Does the Microsoft Store build stay **client-only**?
7. Is a **Docker → native migration** (export/import of chats, keys, files) needed at launch?
8. Budget for an **Apple Developer ID** (not yet in the keychain, per `tools/build/README.md`) and a **Windows code-signing**
   certificate or Trusted Signing.
9. Should the app say plainly that a native Windows server is less isolated than Docker (section 9)?

## 14. What was and was not verified

- **Read in this repository:** every claim about `compose.yml`, the Dockerfiles, `entrypoint.sh`, `chromium-policy.json`,
  `egress.py` (including the `0.0.0.0` bind), `netguard.py`, `tools/browser.py` (`connect_over_cdp` over HTTP), `tools/live.py`
  (screencast), `workroom*.py` (`sweep()` via `/proc`, install flags), `cli_*`, `agy_pool.py`, `instances.py`, `updates.py`,
  `release.py`, `updater_service.py`, `server_updates.py`, `connect.py`, the install scripts and `apps/desktop/`.
- **From the web:**
  - sandbox-runtime's design, licence (Apache-2.0), platforms, Windows alpha approach and documented limitations, from its README
    at <https://github.com/anthropic-experimental/sandbox-runtime>. Its canonical owner is stated there as `anthropics`. I did not
    read its source code.
  - The Firefox `forbidden-sandbox-reinit` report ([bug 1415159](https://bugzilla.mozilla.org/show_bug.cgi?id=1415159)).
  - Chromium's Mac sandbox design
    ([seatbelt_sandbox_design.md](https://chromium.googlesource.com/chromium/src/+/HEAD/sandbox/mac/seatbelt_sandbox_design.md)).
  - The CfT proprietary-codec commit
    ([a242368](https://chromium.googlesource.com/chromium/src/+/a242368ef321d4a1974999434084347ffcc085fd)), and the general
    Chromium codec build flags.
- **Not verified, marked [verify] above:**
  - Chromium nested under `sandbox-exec` (expected to fail, not tested);
  - mandatory versus recommended policy on macOS;
  - Windows network-service sandbox status;
  - `pf` `user` matching on current macOS;
  - AppContainer `internetClient` versus private-network behaviour for our use;
  - claude-code Keychain behaviour with `CLAUDE_CONFIG_DIR`;
  - agy on Windows;
  - CfT redistribution terms;
  - Sparkle `sparkle-cli`;
  - SmartScreen/EV and Trusted Signing eligibility;
  - Python `AF_UNIX` on Windows;
  - Chrome 136 debugging-port rule;
  - all sizes and efforts.
