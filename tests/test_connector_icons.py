"""Services' app icons (connector_icons.py): every row with an icon points at a real square PNG, the icon route is public, cached
with an ETag and allows nothing but the picture, and rows without an icon keep their old shape."""

import json
import re

import httpx
from PIL import Image

from assistant import connector_icons
from assistant.catalog import BY_ID
from assistant.connect_ways import ways
from assistant.connectors import connector_rows, list_connectors
from assistant.mail import PROVIDERS as MAIL_PROVIDERS, Mail
from assistant.server import create_app
from assistant.store import Store

from test_server import AUTH, scripted, settings

URL = re.compile(r"^/api/connectors/icons/([a-z0-9_]+(?:-128)?)\.png\?v=([0-9a-f]{16})$")


def test_manifest_points_at_real_square_pngs_with_a_source_and_date():
    m = connector_icons.manifest()
    assert len(m["icons"]) >= 80
    assert set(m["connectors"]) <= set(BY_ID) and set(m["mail"]) <= set(MAIL_PROVIDERS)
    assert set(m["connectors"].values()) | set(m["mail"].values()) <= set(m["icons"])
    for key, info in m["icons"].items():
        assert re.fullmatch(r"[a-z0-9_]+", key)
        # a service's icon comes from its own site; "kelo" marks Kelo's own plain glyph for a connector that is no one company's
        assert (info["source"].startswith("https://") and info["page"].startswith("https://")) or info["source"] == "kelo", key
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", info["fetched"])
        assert re.fullmatch(r"#[0-9A-F]{6}", info["color"])
        for name, px in ((key, 256), (f"{key}-128", 128)):
            with Image.open(connector_icons.DIR / f"{name}.png") as im:
                assert im.format == "PNG" and im.size == (px, px), name
    # nothing else sits in the folder (no SVG, no script, no stray file)
    names = {p.name for p in connector_icons.DIR.iterdir()}
    assert names == {"manifest.json"} | {f"{k}{s}.png" for k in m["icons"] for s in ("", "-128")}


def test_rows_carry_icon_fields_only_when_there_is_an_icon():
    rows = {r["id"]: r for r in list_connectors()}
    m = connector_icons.manifest()
    for cid, r in rows.items():
        if cid in m["connectors"]:
            big, small = URL.match(r["icon"]), URL.match(r["icon_small"])
            assert big and small and big[1] == m["connectors"][cid] and small[1] == f"{big[1]}-128"
            assert r["icon_color"] == m["icons"][big[1]]["color"]
        else:
            assert not {"icon", "icon_small", "icon_color"} & set(r)
    s = Store()
    full = {r["id"]: r for r in connector_rows(ways(s, Mail(s), browser=True))}
    assert full["notion"]["icon"] == rows["notion"]["icon"]
    # generic connectors get Kelo's neutral glyph, never a company's logo
    for cid in ("imap_email", "caldav_calendar", "places"):
        assert m["icons"][m["connectors"][cid]]["source"] == "kelo" and full[cid]["icon"] == rows[cid]["icon"], cid
    # one brand, one picture
    assert len({rows[c]["icon"] for c in ("outlook_mail", "outlook_calendar", "outlook_contacts")}) == 1


def test_unknown_or_odd_names_have_no_icon():
    assert connector_icons.read("../manifest") is None and connector_icons.read("manifest") is None
    assert connector_icons.read("NOTION") is None and connector_icons.read("nope_nothing") is None
    assert connector_icons.for_connector("nope") == {} and connector_icons.for_mail("nope") == {}


async def test_icon_route_is_public_cached_and_strict():
    app = create_app(settings(), tools=[], stream_fn=scripted())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test") as c:
        rows = {r["id"]: r for r in (await c.get("/api/connectors", headers=AUTH)).json()}
        url = rows["notion"]["icon"]
        r = await c.get(url)                     # no Authorization: the same picture for everyone
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(b"\x89PNG")
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert r.headers["x-content-type-options"] == "nosniff" and "default-src 'none'" in r.headers["content-security-policy"]
        tag = r.headers["etag"]
        again = await c.get(url, headers={"If-None-Match": tag})
        assert again.status_code == 304 and not again.content
        plain = await c.get(url.split("?")[0])   # without the current version: checked again later
        assert plain.status_code == 200 and plain.headers["cache-control"] == "public, max-age=86400"
        assert (await c.get(rows["notion"]["icon_small"])).status_code == 200
        for bad in ("/api/connectors/icons/manifest.png", "/api/connectors/icons/NOTION.png", "/api/connectors/icons/nope.png",
                    "/api/connectors/icons/..%2Fmanifest.png", "/api/connectors/icons/notion.svg"):
            assert (await c.get(bad)).status_code == 404, bad
        mail = (await c.get("/api/mail/providers", headers=AUTH)).json()["providers"]
        by_id = {p["id"]: p for p in mail}
        assert URL.match(by_id["gmail"]["icon"]) and by_id["gmail"]["help"] is not None
        assert json.dumps(mail)   # still plain JSON with the old fields
