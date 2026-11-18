# Kelo's browser: network, setup and what it can do

The agent browses in its own browser (the `browser` container): Google Chrome, or Chromium presenting as Chrome (see "Identity"
below), driven by the server over the Chrome DevTools Protocol. This page
explains how that browser is fenced off from your home network, how it is set up, and the tools built on it.

> Who drives the browser (2026-11-17): the chat does not click or type on websites. The tools below that act on a page (click, type, drag,
> upload, login, look, human check, ...) belong to a background job of kind browser (`delegate`); the chat keeps `browser_open`, `browser_read`,
> `browser_view`, the download tools, `browser_site_state` and `browser_forget_site`. Switch `ASSISTANT_BROWSER_WORKER=0` gives the chat every
> tool again; see [browser-workers.md](browser-workers.md).

> Browsing can also go out through your own VPN or proxy (Connectors → Network): see [vpn.md](vpn.md). The LAN block below stays in force.

## The LAN block (network level)

```
            browser-net (internal: no gateway)          egress-out (ordinary)
 ┌─────────┐  DevTools :9223   ┌────────┐  :3128  ┌────────┐ ─────────────▶ internet (public addresses only)
 │ server  │ ────────────────▶ │browser │ ──────▶ │ egress │
 └─────────┘                   └────────┘         └────────┘ ✗ 192.168.x / 10.x / 172.16-31.x / 127.x / 169.254.x / 100.64/10 / fd00::/8 …
```

- **The browser has no route anywhere.** It sits only on `browser-net`, a Docker network marked `internal: true`. An internal network has
  no gateway: nothing the browser does (a page, a script, a redirect, WebRTC, DNS rebinding, a raw IP address) can reach the internet, your
  LAN, the host computer or a cloud metadata service directly. Checked on Docker Desktop for Mac: from such a network, `192.168.1.20`,
  `1.1.1.1` and `host.docker.internal` are all unreachable, and external names do not even resolve.
- **Its only way out is the egress proxy** (`assistant/egress.py`, the `egress` service, same image, `ROLE=egress`). The browser is started
  with `--proxy-server=http://egress:3128`. For every request the proxy resolves the host name itself, refuses it when *any* address is
  not public (loopback, RFC 1918, link-local incl. 169.254.169.254, CGNAT 100.64.0.0/10, multicast, reserved, IPv6 ULA/link-local, the
  IPv4-mapped forms), and connects to exactly the address it checked (no second lookup to race). HTTPS passes as a CONNECT tunnel (TLS stays
  end to end). A refused request gets `403` with `X-Kelo-Egress: blocked`; the browser shows a connection error.
- **The server still reaches DevTools**: it joins `browser-net` too and talks to `browser:9223` directly, not through the proxy. The
  server keeps its own internet access on its other networks.
- **The code guard stays** (`check_url` in `tools/browser.py`): the agent's own navigations are checked before they happen, and a redirect
  into a private address resets the tab. The network layer is what catches everything the page does by itself.
- Policy adds `WebRtcIPHandling: disable_non_proxied_udp` and `QuicAllowed: false`, so nothing tries to go around the proxy.
- **The live view is the browser's own X screen (2026-11-17).** The browser container runs TigerVNC's `Xvnc` (a real X display that also
  speaks RFB) on loopback `:5900`; `socat` republishes it on `:5901` for the server on `browser-net`, like DevTools on `:9223`, never
  published. The old noVNC page (`6080`, `VIEWER_PORT`, `EGRESS_RELAY=6080=browser:6080`) is gone: it was an unauthenticated control page
  of the signed-in browser. The only way to the screen is the server's ticketed `/api/browser/sessions/{key}/vnc` socket
  (`api/browser.py`, `tools/vnc.py`), which drops pointer, key and clipboard messages of anyone who does not hold the tab. One tab owns
  the screen at a time (`VncScreen`); a second tab gets `busy` and the apps show the CDP picture stream (`tools/live.py`) instead.
  While a viewer's frames are off (`{"type":"frames","on":false}` on the live socket) its picture headers, which carry the page's address and
  title, are not sent, so the live socket sends `{"type":"meta","url","title"}` instead: once at once, then whenever either changes.
  When the screen's size changes (rotation) the app closes `/vnc` and opens it again with the new width, height and density.

Why this design: it needs no `iptables`, no `NET_ADMIN` capability and no host networking, so it works the same on Docker Desktop (Mac,
Windows) and on Linux, and all containers keep `cap_drop: [ALL]`.

## Browser setup

- **Managed policy** (`docker/chromium-policy.json`, installed root-owned to `/etc/chromium/policies/managed/` and Chrome's directory):
  component updates, metrics, feedback, sign-in and sync off; password manager and leak detection off (the vault handles logins);
  address/card autofill off; spelling service, translate, search suggestions, media router (LAN cast discovery) off; notifications
  set to **ask** (`DefaultNotificationsSetting: 3`: a site's permission reads "default", like a normal Chrome; nobody answers the
  prompt, so no site is ever granted); location, sensors, USB/serial/Bluetooth/HID and local fonts denied by default; PDFs open in the browser (`AlwaysOpenPdfExternally:
  false`) and nothing asks for a download folder; Google update/variations/autofill/optimization hosts blocked (also refused by the proxy).
  Check it at `chrome://policy` in a tab.
- **Language and time zone**: `BROWSER_LANG` (default `ko-KR`) sets `--lang` and `--accept-lang` (`ko-KR,ko,en-US,en`): what sites see in
  `Accept-Language` and `navigator.languages`; the server also gives every tab the same locale for dates and numbers. The time zone of
  every tab is the user's profile time zone (`Emulation.setTimezoneOverride`), so it follows a change in the app without a restart; the
  container's own `TZ` (`BROWSER_TZ`, default Asia/Seoul) covers anything opened otherwise.
- **Identity: Google Chrome.** The browser is used for ordinary personal browsing on the user's behalf (shopping, bookings, mail,
  forms), so sites should treat it like the normal desktop Chrome most people use, not like an unusual or automated client. Which build
  it is comes from `BROWSER` in `.env` (`assistant setup` asks; docker/server.Dockerfile records the result in `/etc/kelo-browser`):
  - `BROWSER=chrome` (this server): **real Google Chrome stable**, installed from Google's signed apt repository
    (`dl.google.com/linux/chrome/deb`). Google publishes it for **linux/amd64 and, since mid-2026, linux/arm64** (Apple-silicon Docker
    Desktop runs arm64), checked 2026-11-08: both architectures carry `google-chrome-stable` 157.0.8103.38. Nothing about its identity is
    changed; it simply is Chrome. The managed policy is installed to `/etc/opt/chrome/policies/managed/` (verified in `chrome://policy`
    and by `ERR_BLOCKED_BY_ADMINISTRATOR` on a blocklisted host). Built on the user's own machine; never publish an image built this way.
    On an architecture Google does not build for, the image falls back to Chromium (next point) instead of failing.
  - `BROWSER=chromium` (the redistributable default, or the fallback): Debian's Chromium, same engine, presented as the **matching
    Google Chrome release**. The entrypoint passes `--user-agent` (Chrome's reduced Linux string, `Mozilla/5.0 (X11; Linux x86_64)
    AppleWebKit/537.36 (KHTML, like Gecko) Chrome/<major>.0.0.0 Safari/537.36`, the same on x86_64 and arm64, no "Chromium"), and
    `assistant/browser_identity.py` runs next to the browser: one DevTools connection that auto-attaches (flattened, paused until set) to
    every page, popup, new tab, cross-site iframe, dedicated/shared/service worker and worklet, and applies
    `Emulation.setUserAgentOverride` (`Network.` for workers) with `userAgentMetadata`: brands `Google Chrome` / `Chromium` / Chrome's
    GREASE brand in Chrome's own seeded order and version (e.g. M156 `"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"`,
    M157 `"Not-A.Brand";v="99", "Google Chrome";v="157", "Chromium";v="157"`), the full version list, platform `Linux`, empty platform
    version and model, the machine's architecture/bitness (`arm`/`x86`, `64`), not mobile, `Desktop`. So `navigator.userAgent`,
    `navigator.userAgentData` (incl. `getHighEntropyValues`) and the `User-Agent` / `Sec-CH-UA*` request headers all say the same Chrome.
    Known gap: a DevTools client that creates a tab *directly at a URL* (`Target.createTarget` with a URL) sends that first request with
    Chromium's own brand list (the UA string is still right); Kelo never opens tabs that way (Playwright opens `about:blank` first).
  - Both: headful on a 1440×900 screen (Xvnc, kiosk window), no "Headless" anywhere, `navigator.webdriver` `false` (Chrome only sets it with
    `--enable-automation`/headless, which are not used), language and time zone are the user's own (below).
- **WebGL and notifications (2026-11-15)**: the browser has a WebGL context like a desktop Chrome without a GPU: the entrypoint passes
  `--use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist` (software GL, the renderer string is SwiftShader's).
  Notifications are "ask" by policy, so a site reads `default`/`prompt` and never `denied`. These are generic, for every site; no site
  is named anywhere. Measured on the image (Chromium 156, arm64): WebGL 1 and WebGL 2 both give a context; the unmasked renderer is
  `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device …), SwiftShader driver)`. A blank `about:blank` tab still reads "denied" (an opaque
  origin); a real site reads "default". Before the change: no WebGL context at all, "denied" everywhere.
- **What is deliberately not done**: no canvas, audio, font, screen or timing noise, no WebGL value faked, no hiding of DevTools, no other fingerprint
  forging. The only goal is that the browser's identity is consistently "Chrome" everywhere a site can look; consistency matters more than
  tricks, and the browser stays honest about everything else (it is a Linux desktop Chrome, which is what it really is).
- **How it was checked** (2026-11-08, linux/arm64, local test page and a tiny local HTTP server inside the container, no external sites):
  the existing tab, a new tab, a `window.open` popup, a cross-site iframe, a dedicated worker and a service worker all reported the same
  `navigator.userAgent`, brands and high-entropy values, and every request carried the matching `User-Agent`, `Sec-CH-UA`,
  `Sec-CH-UA-Full-Version-List`, `-Arch`, `-Bitness`, `-Platform`, `-Platform-Version`, `-Model` and `Accept-Language`, for both real
  Chrome 157 and Chromium 156 with the identity; the Chromium result was compared against what real Chrome reports.
- **Restart fix**: a restarted container kept the X server's `/tmp/.X99-lock` and could never start the display again; the entrypoint now removes it.

## What asks for approval

Looking things up never asks. `browser_open`, `browser_read`, ordinary clicks (links, tabs, filters, sorting, date pickers, "more"
buttons, cookie banners), typing into any field (a search box, the destination, dates, an e-mail address, a phone number, a name, an
address, a message) and ordinary form submits run without a card. Ask on what the step DOES, never on what is typed (the reference agent's
rule: "an ordinary browser form submit is currently allowed by default"). A step that commits something in the user's name shows an Allow/Deny card, by the step judge's flags (`tools/commits.py`, `step_judge.py`):

- **pay**: spends money (buy, order, book, subscribe, donate, top up, transfer);
- **apply**: submits an application, a registration for a program, event or benefit, an official record or a draw entry;
- **send**: sends or posts something to people or services (a contact form that sends a message asks here);
- **delete**: deletes or cancels something (an order, a booking, a subscription, an account);
- **optout**: unsubscribes, withdraws consent, closes a membership;
- **account**: signs up or registers;
- **security**: changes how the account is protected or reached (an app password, 2-step verification, a device); always asks;
- **typing** asks in exactly two cases, both tied to money or sign-in, not to words: a card number (by value: 13-19 digits that pass the
  Luhn check; payments go through the checkout tools) and a field of input type `password` or autocomplete `current-password`,
  `new-password` or `one-time-code` (sign-ins go through `browser_login` and the vault).

The judge being unavailable (or answering badly) asks too: a step Kelo could not judge is never passed. Ordinary form submits run without
a card unless the step judge finds they spend, send, delete, sign up, apply, opt out or change account security, as in the reference. The
privacy guard is separate: secrets and the owner's own details in typed text are still checked where they were.

The judgement reads the element just before the step (`TARGET_JS`, at most 1.5 s): its visible words, `aria-label`, role, whether it is
a link to another page (a long link text is a result or a product card, so following it only looks), the form it belongs to and the
page it is on. When the element cannot be read, the selector's own words are judged by the step judge. The judgement is generic (no
site has a rule of its own). "Allow on this site" rules never
cover a payment-like click. Payments still go through `confirm_purchase`; after it is approved the few committing steps that finish the
purchase on the same site need no second card. Sign-ins (`browser_login`), uploads and one-time codes ask exactly as before.

### After paying: the receipt is checked

After a payment Kelo reads the confirmation page and calls `purchase_result` (`tools/purchase.py`, the checks in `receipt.py`) with the
order number, the total charged, the items and the cancel terms the page shows. The server compares them with what the owner approved
(a higher total, another item, option or quantity is a mismatch; a lower total is fine and said). The owner's three rules:

1. A wrong charge and cancelling is free right now (the page says so, or its deadline is still ahead): Kelo cancels the order at once,
   money back first, then reports. The cancel click of that order on that site goes through without a card for a few minutes
   (`BrowserSession.allow_cancel`, only words that cancel an order, never an account or a subscription); it is logged as a cancel of a
   wrong charge and the owner gets an urgent notice.
2. A wrong charge and cancelling costs a fee, or the terms are unclear: Kelo never cancels on its own, because that would lock in the
   fee. It asks the owner at once with the amount charged against the approved one, the difference, the fee and until when cancelling
   is free or cheaper. Allow cancels the order, Deny keeps it. An unanswered card gets the usual follow-ups, with the free-until time
   as the deadline when known.
3. The report says the approved condition and the real charge plainly, without excuses, and gives the next option.

If a task ends with a paid order whose receipt was never checked, the report ends with one line saying so and asking the owner to check
the order.

To have every click and typing step ask, as before, set `ASSISTANT_APPROVE_BROWSER_ACTIONS=1` in `.env` (default `0`).

## The work browser

Scheduled and background tabs run in a second browser, the work browser (the `work-browser` container, same image as the browser). The
owner's chat tabs and the purchase sign-in stay in the main browser.

- Which tabs: every tab whose key starts with `schedule:` or `task:` (schedules, watchers and their site tabs, the checklist, Work-page
  tasks and jobs handed off from a chat). Chat tabs (`owner`, a device id) and `buy:` tabs stay in the main browser (`work_browser.work_tab`).
- Each work tab has a context of its own in the work browser. Opening it copies every cookie of the main browser into that context (the
  sign-in copy, one way, in memory). A site's local storage is copied when the tab first opens that site, from a main-browser page that is
  already open; with none open, only the cookies are there.
- When the work tab closes, the cookies of the sites the job signed in to are copied back to the main browser (the sign-in carry-back):
  a site signed in by the vault login, or by the owner's own hands on that tab. Not copied back while a main-browser tab shows that site.
  Cookies the job changed on other sites never reach the main browser.
- Nothing is kept: the work browser starts with an empty profile folder (`BROWSER_EPHEMERAL=1`), and its work contexts are closed with their
  tabs. A restart of the work browser or of the server ends its contexts; a resumed job opens a new work tab with a fresh copy.
- No fallback: when the work browser does not answer, a work tab's step fails with a note saying so, and the job reports itself blocked.
  The main browser is never used for a work tab.
- Live view: the work browser has its own X screen (`WORK_VNC_PORT`, 5903). Watching or holding a work tab uses that screen, so it never
  moves the owner's screen, and the owner's tab never makes a work tab "busy". A new window of a job's tab brings the held work tab forward.
- Downloads from a work tab use the work browser's own DevTools session and its context, into the same download folder.
- Limits: no hard cap by default (the owner's decision). A size (Minimum, Recommended) gives the work browser a share of RAM and CPU,
  weight 1.5 against the browser's 2.0 (owner decision 2026-11-18). `assistant limits` can set `WORK_BROWSER_MEMORY` and `WORK_BROWSER_CPUS`,
  shown next to the browser's own numbers. Under memory pressure the work browser is ended first (`oom_score_adj: 500`), and when both browsers are busy
  it gets half the CPU weight (`cpu_shares: 512`).
- Setting: `WORK_BROWSER_CDP_HOST` (default `work-browser`; empty = off, every tab in the main browser as before) and
  `WORK_BROWSER_CDP_PORT` (default 9225).
- Known limit: a site that rotates its sign-in token on every use may sign the main browser out after a job used the copied cookie. The
  next chat on that site signs in again (the vault fills it in).

## Looking things up without the browser

`web_search(query, count?)` and `web_fetch(url, start?)` (`tools/web.py`) read the web from the server itself, for every model and
without a key or a card. Search uses DuckDuckGo's HTML endpoint (a form POST; no key, no JavaScript) and falls back to Bing's RSS
results when DuckDuckGo answers with its robot check or nothing; requests to one engine are spaced a second apart. `web_fetch` returns
a page as readable text (title, main content without menus and footers, 12,000 characters per call; `start` reads on). Both follow the
server's rules for outward reads (`watchers.public_get`, `netguard.py`): public http(s) only, the standard port, no credentials in the
URL, names that resolve to public addresses only, the connection pinned to the checked address, every redirect (at most 3) checked
again, 20 s and 2 MB at most, an honest `User-Agent` (`Kelo/1.0`). Results are untrusted content, like a page in the browser. A page that
refuses plain requests (HTTP 403 and the like) or shows nothing without JavaScript says so, and the agent opens it in the browser
instead. The agent is told to answer lookups and price comparisons this way in the chat, not as a background job.

## Tools

| Tool | What it does | Approval |
|---|---|---|
| `browser_downloads` | lists what pages of this tab downloaded (name, size, state, site) | no |
| `browser_save_download(name, save_as?)` | copies a finished download into the user's Results (files/, ≤ 100 MB; programs refused by name and by first bytes) | no |
| `browser_upload(selector, files)` | sets `uploads/...` or `files/...` into a file input (≤ 10 files, ≤ 50 MB) | every time, card names files and site; never remembered |
| `browser_save_pdf(name?)` | the page as a PDF in Results (files/) (`Page.printToPDF`) | no |
| `browser_private(on, use_login_of?)` | a private context for a sensitive one-off visit; nothing kept; optionally copies one site's sign-in in | no |
| `browser_site_state(action, site)` | save / load / forget / list a site's cookies + local storage (memory only, 12 h) | no |
| `find_login_code(site?, selector?, submit?)` | looks in connected mailboxes (last 15 min) for a code from that site; the card shows it; typed only after Allow | every time; never remembered |

Downloads: the browser writes them to the `browser-downloads` volume (`/downloads`, files named by download id); the server mounts it
read-only (`BROWSER_DOWNLOADS_DIR=/browser-downloads`) and copies a file only when asked. Executables are cancelled in the browser before
they finish; anything over 200 MB is cancelled; files older than a day are deleted by the browser container.

Pace of input (`tools/humanize.py`): the agent handles pages the way a person at that browser would. The pointer keeps its place per tab
and moves to a target along a curved, eased path with a little tremor (longer and slower for far or small targets, now and then a slight
overshoot and correction); a click lands on a random point inside the element, near its middle and away from its edges, with a short
press. An element out of view is first reached with uneven mouse-wheel steps. Text is typed key by key with uneven gaps (longer after
spaces and punctuation, sometimes a short pause); passwords, card details and one-time codes are typed exactly, never with a typo, and
never logged. After a page loads the agent looks at it for about 1–2 seconds before reading or acting. Anything that cannot be done this
way (an element without a box, a field that is not an input) falls back to the plain instant click or fill. All timings are in
`humanize.Timing`; `ASSISTANT_HUMAN_INPUT=0` turns it off, `ASSISTANT_HUMAN_INPUT_SEED=<n>` makes it repeatable.

Human checks: Cloudflare (interstitial and Turnstile), reCAPTCHA, hCaptcha, DataDome, PerimeterX/HUMAN, Arkose, GeeTest and AWS WAF are
recognised from response headers, frames, DOM marks and titles (`tools/challenge.py`), not from words on the page. Invisible widgets do
not count. The agent may first try a check itself in the page, at most twice (each page reading that still shows it ends a try), with
its normal browser steps and `browser_human_check` (a picture of the page, a click or keys on the check itself only, or hand it over
now). No outside solving services or paid solvers, and nothing that changes the page's or the site's scripts. After the tries, or
when the agent says it cannot do this kind, the check is handed to the user exactly as before: the step waits on a pending
`human_check` item (live view, follow-up), or, where no hand-over runs, the chat gets a `browser_check` event and, in an unattended
run, a notification. Rate limits, locked accounts and refusals still mean stop and tell the user.

Takeover journal: while the user controls a tab, an isolated-world script (invisible to the page) records clicks (labels), field changes
(short values of plain fields only; never password, card, security-code or one-time-code values) and navigations. When control returns
the agent gets a short summary with its next look at the page.

Step pictures: background jobs and automatic runs keep a small JPEG and a manifest line per browser step (last 300 / 60 MB per tab,
14 days): `GET /api/browser/sessions/{id}/history`.

One-time codes: the code lives only in memory between being found and being typed (or declined); the approval log and notifications
keep a masked form, and the model is told only that a code was found and how many digits it has.
