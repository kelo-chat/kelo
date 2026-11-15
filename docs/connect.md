# Reaching Kelo from your phone

Each copy of Kelo (the main one and every `assistant instance`) chooses its own way. One way at a time per copy: picking a
new one turns the old one off first (gives a Kelo name back, stops Funnel / Serve, stops the tunnel or Caddy).

| Way | Command | Good for | Keep in mind |
|--- |--- |--- |--- |
| **Kelo address** `name.kelo.chat` (offered for now) | `assistant connect kelo --name NAME` | Easiest: no router changes. | Uses https through a Cloudflare Tunnel. |
| **Tailscale (private)** | `assistant connect tailscale` | Most private: nothing is public. | Every phone needs the Tailscale app on the same account. |
| **Tailscale Funnel (public)** | `assistant connect funnel` (= `tailscale --public`) | Public https with no domain, encrypted end to end to this computer. | Ports 443/8443/10000 only. Beta, with bandwidth limits Tailscale does not publish. |
| **Own domain: Cloudflare** | `assistant connect cloudflare --domain example.com` | Your domain on your own Cloudflare account. | Goes through Cloudflare. Needs `cloudflared` on this computer. |
| **Own domain: direct** | `assistant connect domain --host me.duckdns.org --direct` | Nothing in between: Caddy on this computer gets the certificate by itself. | Needs ports 80 and 443 free here, forwarded on the router, and not blocked by your ISP. |
| **Own domain: your proxy** | `assistant connect domain --host kelo.me.duckdns.org --proxy` | Fits a proxy you already run (Nginx Proxy Manager, Traefik, Caddy). | You add the host in your proxy. Kelo prints the exact settings. |

Add `--instance NAME` to any command to work on an extra copy instead of the main one. Then:

```
assistant connect status            # every copy: method, the exact address, and a health check (GET /healthz through it)
assistant phone add                 # QR code for a phone; the link uses the address set up above
assistant connect off               # close it again (a Kelo name is given back)
```

## Kelo address (`name.kelo.chat`)

The Kelo project offers these addresses for now. Availability and terms may change. Tailscale and your own domain are always available.

- Names are 3 to 30 characters long, made of `a-z`, `0-9` and `-`. Some names are reserved (www, api, admin, mail, kelo, broker,
  relay, and others). An extra copy uses its own name by default. The main copy needs `--name`.
- Each copy gets one name. Asking for a different name gives the old one back first. Each copy keeps a random secret
  (`KELO_INSTALL_ID` in its settings file), and only a hash of it is sent to the broker. Only that copy can get the name's token
  again or give the name back.
- The tunnel token is saved in the copy's own `kelo/tunnel.env` (mode 0600). Only the `kelo-tunnel` container reads it, so the
  server container never sees it.
- If a name has had no connected tunnel for 30 days, it is given back. This counts from the latest `connect kelo` at the
  earliest.
- **Privacy:** traffic to `name.kelo.chat` uses https through a Cloudflare Tunnel. The Tailscale and own-domain ways exist for those
  who prefer to keep Cloudflare out of the path.

## Tailscale Funnel

`assistant connect funnel` uses the first free one of ports 443, 8443 and 10000, or the port you give with `--https-port`. The
address is `https://<computer>.<tailnet>.ts.net[:port]`. If all three ports already serve something else, the command stops and
suggests private Tailscale or your own domain instead.

The first time, Tailscale may ask you to turn on Funnel (HTTPS certificates and the `funnel` attribute) through a link it prints.
Funnel's relays cannot read the traffic, because TLS ends on this computer.

## Your own address (DuckDNS or a domain)

`--host` takes the address exactly as you want it:

- a bare name: `me.duckdns.org`
- a sub-name: `kelo.me.duckdns.org`
- a name with a port: `https://me.duckdns.org:8443`

`connect status` shows the address exactly as you typed it.

- **If your bare address already serves another app** (on 80/443 behind your proxy), give Kelo its own sub-name or port and
  route it with your proxy (`--proxy`). DuckDNS answers for every sub-name of yours, so `kelo.me.duckdns.org` works without
  any setup at DuckDNS.
- **`--direct`** only works when ports 80 and 443 on this computer are free. Kelo never takes them over from another service,
  and only one copy per computer can use it. Before starting Caddy, it checks that the name points to this computer's public
  address. Then:
  1. Forward TCP 80 and 443 on your router to this computer.
  2. Caddy gets a Let's Encrypt certificate by itself.

  Add `--skip-dns-check` only if your network is unusual.
- **`--proxy`** makes Kelo listen on your home network (`SERVER_BIND`, default 0.0.0.0, or `--bind <lan-ip>`) and sets the
  public address. It then prints what to enter in your proxy:
  - target `http://<lan-ip>:<port>`
  - websockets on (the phone's live view of the agent's browser and "take control" use a WebSocket at
    `/api/browser/sessions/<id>/live`). In Nginx Proxy Manager, turn on **Websockets Support** on the host's Details tab.
    Without it the app falls back to fetching one picture at a time (`/api/browser/sessions/<id>/frame`), which is slower.
  - buffering off
  - timeouts of at least 120 s
  - no upload size limit (Kelo checks sizes and the storage cap itself). Through Cloudflare (the `kelo.chat` address or an own Cloudflare domain) one request is limited to 100 MB by Cloudflare, so the apps send files over 99 MB in parts of 90 MB (each part is tried again after a dropped connection, and the apps ask the server what it already holds first); on Tailscale or a direct domain a file goes up in one request, whatever its size

  For Nginx Proxy Manager (Advanced tab):

  ```
  proxy_buffering off; proxy_request_buffering off; proxy_cache off;
  proxy_read_timeout 300s; proxy_send_timeout 300s; client_max_body_size 0;
  ```

  The **Websockets Support** switch adds the `Upgrade` / `Connection` headers. In plain Nginx, add them yourself in the
  `location`: `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";`.
  To check it: `uv run python scripts/ws_check.py https://kelo.me.duckdns.org` (it needs the token in `$ASSISTANT_TOKEN`)
  should print `websocket upgrade OK`.

  Example: an NPM host `kelo.me.duckdns.org` forwarding to `http://192.168.1.20:8080` is
  `assistant connect domain --host kelo.me.duckdns.org --proxy`. `connect off` puts back the `SERVER_BIND` you had before.

  **Step by step in Nginx Proxy Manager:** see [the guide in the README](../README.md#through-nginx-proxy-manager).
- **`--duckdns`** is optional. It keeps a `.duckdns.org` name pointed at this computer. The token is read from `$DUCKDNS_TOKEN`
  or typed without being shown. It is saved sealed with the copy's `ASSISTANT_SECRET_KEY` and never printed. Leave it out if
  your router or proxy already updates the name.

## For the operator of kelo.chat

1. **Add the zone.** Add `kelo.chat` to a Cloudflare account. Use a separate account if you want the address service kept
   apart from everything else.
2. **Create the API token** (My Profile or Account → API Tokens → Create custom token). It needs only these permissions:
   - **Account → Cloudflare Tunnel → Edit**
   - **Zone → DNS → Edit**, limited to the zone `kelo.chat`
3. **Run the broker.** On the operator's Kelo (the server reached at `https://kelo.chat`, the same one that brokers sign-ins).
   The easy way is the app:
   **My → Operator settings → Cloudflare**. Operator settings show on every phone signed in to the operator's server (every
   signed-in device has the same full rights; there are no owner devices). The same screen also has **Beta requests** (Google
   sign-in) and the push key. My → Devices marks the operator's server as **Operator**.

   Or, with the access key:

   ```
   curl -X PUT https://kelo.chat/api/operator/cloudflare -H "Authorization: Bearer $ASSISTANT_TOKEN" \
        -H 'content-type: application/json' \
        -d '{"api_token":"…","account_id":"<32 hex>","zone_id":"<32 hex>","zone_name":"kelo.chat","max_names":190}'
   ```

   The token is checked against Cloudflare before it is saved, then stored encrypted. It is never returned.
   `GET /api/operator/cloudflare` shows the settings and how many names are in use. `POST /api/operator/cloudflare/reclaim`
   runs the idle sweep now. It also runs by itself at most every 6 hours, when someone claims a name.
4. **Point the copies at the broker.** Copies use `ASSISTANT_KELO_BROKER`. If that is not set, they use
   `ASSISTANT_OAUTH_BROKER`, and then `https://kelo.chat`.

**Limits and costs (checked 2026-11):**

- **Tunnels:** a Cloudflare account can have 1,000 tunnels. More need Tunnels for Platforms, at about $4.50 per tunnel per
  month. That is why `max_names` defaults to 900.
- **DNS records:**
  - Free zones created on or after 2024-09-01 allow 200 records. Records you add yourself count too.
  - On a free zone, set `max_names` to about 190.
  - For more, move `kelo.chat` to a paid plan (Pro or higher: 3,500 records).

When the cap or a Cloudflare limit is reached, a claim gets HTTP 503 "no Kelo addresses are available right now". The message
suggests Tailscale or your own domain instead.

**Broker API** (public, rate limited per address; the copy calls it with `assistant connect kelo`):

| Call | Body / query | Answer |
|--- |--- |--- |
| `POST /broker/tunnel/claim` | `{"name", "install_id"}` (install_id: 32-128 chars `[A-Za-z0-9_-]`) | `200 {"name", "hostname", "tunnel_token"}`<br>`409` taken, or this copy already has another name<br>`422` bad or reserved name<br>`503` full<br>`429` more than 10 claims/releases per 10 min<br>`404` service off<br>`502` Cloudflare error |
| `POST /broker/tunnel/release` | `{"install_id"}` | `200 {"released": name or null}` |
| `GET /broker/tunnel/check?name=` | | `200 {"name", "available", "hostname" or "reason": taken / full / rule}` (60 per minute) |

For each name, the broker creates the following in Cloudflare:

- a tunnel `kelo-<name>` (`config_src: cloudflare`), whose ingress sends `<name>.kelo.chat` to `http://server:8080` and
  everything else to `http_status:404`
- a proxied CNAME `<name>.kelo.chat` pointing to `<tunnel id>.cfargotunnel.com`

Giving a name back removes the DNS record, cuts the tunnel's connectors, and deletes the tunnel. If a tunnel left over from an
earlier holder exists, the broker deletes it first, so that holder's old token stops working.
