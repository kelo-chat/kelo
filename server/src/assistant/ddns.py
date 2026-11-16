"""Keep a DuckDNS name pointed at this computer, only when the owner asked for it (`assistant connect domain ... --duckdns`).

The DuckDNS token is kept in the copy's settings file sealed with its ASSISTANT_SECRET_KEY (DUCKDNS_TOKEN_ENC), never in plain
text and never printed. The server asks DuckDNS every few minutes to set the name to the address the request comes from.
Most people already update their DuckDNS name another way (their router, NPM's host); then nothing here runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

UPDATE = "https://www.duckdns.org/update"
EVERY = 300.0
TOKEN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
log = logging.getLogger("uvicorn.error")


def label_of(host: str) -> str | None:
    """`kelo.me.duckdns.org` and `me.duckdns.org` -> "me" (DuckDNS answers for every sub-name of yours); other hosts -> None."""
    parts = host.lower().rstrip(".").split(".")
    if len(parts) >= 3 and parts[-2:] == ["duckdns", "org"]:
        return parts[-3]
    return None


def seal(secret_key: str, token: str) -> str:
    token = token.strip().lower()
    if not TOKEN.match(token):
        raise ValueError("a DuckDNS token looks like 1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f (shown on duckdns.org after signing in)")
    return Fernet(secret_key.encode()).encrypt(token.encode()).decode()


def unseal(secret_key: str, sealed: str) -> str | None:
    try:
        return Fernet(secret_key.encode()).decrypt(sealed.encode()).decode()
    except (InvalidToken, ValueError):
        return None


async def update_once(domain: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    async with httpx.AsyncClient(transport=transport, timeout=20) as c:
        try:
            r = await c.get(UPDATE, params={"domains": domain, "token": token, "ip": ""})
        except httpx.HTTPError:
            return False
    return r.status_code == 200 and r.text.strip().startswith("OK")


def start(secret_key: str, *, env: Mapping[str, str] | None = None, transport: httpx.AsyncBaseTransport | None = None,
          every: float = EVERY) -> asyncio.Task[Any] | None:
    env = os.environ if env is None else env
    domain, sealed = env.get("DUCKDNS_DOMAIN", "").strip(), env.get("DUCKDNS_TOKEN_ENC", "").strip()
    if not domain or not sealed or not secret_key:
        return None
    token = unseal(secret_key, sealed)
    if not token:
        log.warning("duckdns: the saved token cannot be read with this copy's secret key; run `assistant connect domain` again")
        return None

    async def loop() -> None:
        last = None
        while True:
            ok = await update_once(domain, token, transport=transport)
            if ok != last:
                log.info("duckdns: %s.duckdns.org %s", domain, "points here" if ok else "could not be updated (check the token)")
                last = ok
            await asyncio.sleep(every)

    return asyncio.create_task(loop())
