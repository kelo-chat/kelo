"""Product results a browser job saw on a seller's own page (card p-shopping-like-reference). The chat offers only these, by result id; never items or prices from search results or articles."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .agent.fence import inside, plain
from .tools.sites import registrable

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_results (
    id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL, seller TEXT NOT NULL, price TEXT NOT NULL,
    sale_price TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '', seen_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS product_results_worker ON product_results(worker_id);
"""
NAME_MAX, PRICE_MAX, URL_MAX = 200, 60, 2000
ID_RE = re.compile(r"^p[0-9a-f]{10}$")


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    url: str
    seller: str
    price: str
    sale_price: str = ""
    image_url: str = ""


def page_key(url: str) -> tuple[str, str]:
    """host without a leading 'www.', lower case; path without a trailing '/'. Query and fragment are ignored."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parts.path or "").rstrip("/")
    return (host, path)


def amounts(text: str) -> set[str]:
    r"""Every number in text with its separators removed: re.findall(r"\d(?:[\d.,   ]*\d)?", text) -> keep digits only.
    '12,900원' -> {'12900'}; '$49.00' -> {'4900'}."""
    matches = re.findall(r"\d(?:[\d.,   ]*\d)?", text)
    out: set[str] = set()
    for m in matches:
        d = "".join(c for c in m if c.isdigit())
        if d:
            out.add(d)
    return out


def pages_read(transcript: list[dict]) -> dict[tuple[str, str], str]:
    """From a job's stored messages: for every tool message whose content (agent.fence.plain applied) has a line 'URL: <address>' among
    its first three lines, page_key(address) -> all such texts joined (a page read twice with changes-only output still counts)."""
    pages: dict[tuple[str, str], str] = {}
    for msg in transcript:
        if msg.get("role") != "tool":
            continue
        raw_text = str(msg.get("content") or "")
        content = plain(inside(raw_text))
        lines = [line.strip() for line in content.splitlines()[:3]]
        for line in lines:
            if line.startswith("URL: "):
                address = line[5:].strip()
                key = page_key(address)
                if key[0]:
                    pages[key] = (pages[key] + "\n" + content) if key in pages else content
                break
    return pages


def verify(raw: Any, pages: dict[tuple[str, str], str], wid: str) -> tuple[list[Product], list[str]]:
    """raw: the report's products list. Kept: a dict with name, url (http/https), price, availability == 'in_stock', whose page_key is in
    pages and whose amounts(price) is non-empty and a subset of amounts(that page's text); sale_price, when given, must pass the same
    check or it is dropped (the product stays). image_url kept only when it starts with https://. seller = tools.sites.registrable(host).
    id = 'p' + sha1(f'{wid}|{host}{path}').hexdigest()[:10]. Duplicates (same id) keep the first.
    Left out: one line each, '<name>: not on a page you opened in this job' / '<name>: that price is not on its page' /
    '<name>: not shown as buyable now' / '<name>: name, address or price missing'."""
    kept: list[Product] = []
    left: list[str] = []
    seen_ids: set[str] = set()

    if not isinstance(raw, list):
        return kept, left

    for item in raw:
        if not isinstance(item, dict):
            left.append("product: name, address or price missing")
            continue

        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        price = str(item.get("price") or "").strip()
        n = name or "product"

        if not name or not url or not price or not re.match(r"^https?://", url, re.I):
            left.append(f"{n}: name, address or price missing")
            continue

        availability = str(item.get("availability") or "").strip()
        if availability != "in_stock":
            left.append(f"{n}: not shown as buyable now")
            continue

        key = page_key(url)
        if key not in pages:
            left.append(f"{n}: not on a page you opened in this job")
            continue

        page_text = pages[key]
        price_am = amounts(price)
        page_am = amounts(page_text)
        if not price_am or not price_am.issubset(page_am):
            left.append(f"{n}: that price is not on its page")
            continue

        sale_price = str(item.get("sale_price") or "").strip()
        if sale_price:
            sale_am = amounts(sale_price)
            if not sale_am or not sale_am.issubset(page_am):
                sale_price = ""

        image_url = str(item.get("image_url") or "").strip()
        if not image_url.lower().startswith("https://"):
            image_url = ""

        host, path = key
        seller = registrable(host)
        pid = "p" + hashlib.sha1(f"{wid}|{host}{path}".encode("utf-8")).hexdigest()[:10]

        if pid in seen_ids:
            continue
        seen_ids.add(pid)

        kept.append(Product(
            id=pid,
            name=name[:NAME_MAX],
            url=url[:URL_MAX],
            seller=seller,
            price=price[:PRICE_MAX],
            sale_price=sale_price[:PRICE_MAX],
            image_url=image_url[:URL_MAX],
        ))

    return kept, left


class ProductResults:
    def __init__(self, store: Any):
        self.store = store
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def save(self, wid: str, products: list[Product], now: float) -> None:
        for p in products:
            self.store._run(
                "INSERT OR REPLACE INTO product_results(id, worker_id, name, url, seller, price, sale_price, image_url, seen_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p.id, wid, p.name, p.url, p.seller, p.price, p.sale_price, p.image_url, now),
            )

    def get(self, pid: str) -> Product | None:
        rows = self.store._all("SELECT * FROM product_results WHERE id = ?", (pid,))
        if not rows:
            return None
        r = rows[0]
        return Product(
            id=r["id"],
            name=r["name"],
            url=r["url"],
            seller=r["seller"],
            price=r["price"],
            sale_price=r.get("sale_price") or "",
            image_url=r.get("image_url") or "",
        )

    def of_worker(self, wid: str) -> list[Product]:
        rows = self.store._all("SELECT * FROM product_results WHERE worker_id = ? ORDER BY rowid", (wid,))
        return [
            Product(
                id=r["id"],
                name=r["name"],
                url=r["url"],
                seller=r["seller"],
                price=r["price"],
                sale_price=r.get("sale_price") or "",
                image_url=r.get("image_url") or "",
            )
            for r in rows
        ]

    def forget_workers(self, keep_ids: set[str]) -> None:
        if not keep_ids:
            self.store._run("DELETE FROM product_results")
            return
        marks = ",".join("?" * len(keep_ids))
        self.store._run(f"DELETE FROM product_results WHERE worker_id NOT IN ({marks})", tuple(keep_ids))
