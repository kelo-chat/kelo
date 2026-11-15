# Browsing through your VPN or a proxy

Connectors → **Network** has two connectors that send Kelo's **web browsing** through a route you choose:

- **VPN (WireGuard)**: paste the WireGuard configuration your VPN provider gives you (Mullvad, Proton VPN, IVPN, a server of your own …).
- **Proxy**: a SOCKS5 or HTTP proxy address (`socks5://host:1080`, `http://host:3128`), with a user name and password if it needs them.

At most one is on. Connecting one turns the other off; disconnecting returns to a direct connection.

## What goes through, and what does not

| Goes through the VPN / proxy | Stays direct |
|---|---|
| Kelo's browser (every page, script, image and download it loads) | AI model calls (Claude, GPT, Gemini, local models, the subscription CLIs) |
| `web_fetch` and `web_search` (the pages Kelo reads without the browser) | Your other connectors (mail, calendars, Google, GitHub, apps on your network …) |
| | Push notifications, Telegram and Discord |
| | Your phone's connection to Kelo (Kelo address, Tailscale, Cloudflare …) |

The VPN's exit is outside your home, so your home network is **not** reachable through it. The home-network block stays on top: Kelo's
browser still refuses private and local addresses, and the same lists as before.

## If the VPN is down

Kelo **fails closed**: while a VPN or proxy is connected, browsing that cannot go through it is refused ("the VPN is not connected right
now"). It is never sent directly instead. Disconnecting the connector is the only way back to direct. The same happens after a restart of
a Kelo that asks for your phone to unlock it: until you unlock it, browsing stays refused (it does not fall back to direct).

## Getting a WireGuard file

- **Mullvad**: mullvad.net → Account → WireGuard configuration → generate a key → pick a country → *Download file*.
- **Proton VPN**: account.protonvpn.com → Downloads → WireGuard configuration → choose *Router* or *Android/iOS*, pick a server, *Create*.
- **IVPN**: ivpn.net → Account → WireGuard → *Generate key*, then *Download configuration* for a server.
- **Your own server** (PiVPN, wg-easy, a VPS …): the client file the server tool gives you. The server must be outside your home network:
  an address on your own LAN is refused.

Open the file in a text editor, copy **everything**, and paste it into the connector's field. Line breaks may be lost on the way (some
phone keyboards turn them into spaces); Kelo reads the file by field name, so that is fine. Kelo uses only the fields it needs
(`PrivateKey`, `Address`, `DNS`, `PublicKey`, `PresharedKey`, `Endpoint`, `AllowedIPs`); commands such as `PostUp` are ignored. A file
without a `DNS` line gets `1.1.1.1`, asked **through** the tunnel.

If `AllowedIPs` is narrower than `0.0.0.0/0, ::/0` (a split tunnel), only those addresses go through it and other sites will not load.

## Things you may notice

- **More checks.** Many sites show human checks more often, or block, addresses that belong to a VPN. A different exit server or a
  residential proxy may help.
- **Signing in again.** After the route changes, your cookies and sign-ins in Kelo's browser stay, but a site that ties a session to the
  address it came from may ask you to sign in again or show a check.
- **Time zone and language stay yours.** Kelo never disguises them, so a site can notice that the VPN's country and the browser's time
  zone (or language) differ.
- **Switching waits for the browser.** If Kelo is browsing (a tab used by a task in the last 2 minutes), the change is made when it
  finishes ("applies when the current browsing finishes", at most 30 minutes). Ask again with *switch now* (`force`) to make it at once.
  A purchase that is open is never interrupted, not even then; the change waits for it.
- When the route changes, the browser's open connections are closed at once and reopened through the new route.

## How it works (for the curious)

```
 browser ──▶ egress ──┬─ direct ─────────────────────────▶ internet
                      └─ SOCKS5 ──▶ vpn container ──┬─ wireproxy (WireGuard in user space) ──▶ VPN provider
                                                    └─ forwarder (your proxy + its user name/password) ──▶ your proxy
 server (web_fetch) ──▶ socks5h://vpn:1080 (when the route is on)
```

- The `vpn` container (same image, `ROLE=vpn`) has **no extra privileges**: no `NET_ADMIN`, no `/dev/net/tun`. WireGuard runs in user space
  with [wireproxy](https://github.com/pufferffish/wireproxy), a pinned release whose SHA-256 is checked when the image is built
  (`docker/wireproxy.lock`).
- It sits on `vpn-net` (internal) together with `egress` and `server` only. The browser is not on it.
- The server writes two small volumes: `kelo-route` (`route.json`: direct or SOCKS5 to `vpn:1080`, plus a counter; **no secrets**, read-only
  for `egress`) and `kelo-vpn` (the WireGuard file or the proxy's address and password, mode 0600, read-only for the `vpn` container).
  `egress` never sees your keys.
- Names are looked up by the VPN (SOCKS5 with the host name), not on your computer, so your local DNS does not see the sites Kelo visits.
- The WireGuard file or proxy password is kept in the vault like any other key and is never shown again.

Needs the newer `compose.yml` (compose level 2: the `vpn` service, `vpn-net`, `kelo-route`, `kelo-vpn`). A native install without Docker
has no VPN connectors.
