import asyncio

import httpx

from assistant.providers import Done, TextDelta
from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running, read_events


def app_for(tmp_path, **kw):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), **kw), tools=[],
                      stream_fn=kw.pop("stream_fn", None) or _quiet)


async def _quiet(provider, request, **k):
    yield TextDelta("ok")
    yield Done("stop")


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


async def add_phone(c, name="폰"):
    code = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
    r = await c.post("/api/pair", json={"code": code, "name": name})
    return code, r


async def test_a_phone_is_added_with_a_one_time_code_and_then_has_its_own_token(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.post("/api/devices/pair")).status_code == 401                  # only a signed-in device can add another
        code, r = await add_phone(c, "  Alice's iPhone  ")
        tok = r.json()["token"]
        assert r.status_code == 200 and len(tok) >= 40 and tok != TOKEN
        assert (await c.get("/api/profile", headers=bearer(tok))).status_code == 200
        assert (await c.post("/api/pair", json={"code": code, "name": "again"})).status_code == 400      # a code works once
        listing = (await c.get("/api/devices", headers=bearer(tok))).json()
        assert [d["name"] for d in listing["devices"]] == ["Alice's iPhone"] and listing["current"] == r.json()["device_id"]
        assert "token" not in str(listing) and (await c.get("/api/devices", headers=AUTH)).json()["current"] is None


async def test_any_number_of_phones_can_share_one_container_and_each_can_be_removed_alone(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        phones = []
        for i in range(40):
            _, r = await add_phone(c, f"phone {i}")
            phones.append((r.json()["device_id"], r.json()["token"]))
        assert len({t for _, t in phones}) == 40
        results = await asyncio.gather(*[c.get("/api/profile", headers=bearer(t)) for _, t in phones])
        assert all(r.status_code == 200 for r in results)
        lost_id, lost_tok = phones[7]
        assert (await c.delete(f"/api/devices/{lost_id}", headers=bearer(phones[0][1]))).status_code == 200      # removed from another phone
        assert (await c.get("/api/profile", headers=bearer(lost_tok))).status_code == 401
        others = [t for i, (_, t) in enumerate(phones) if i != 7]
        assert all(r.status_code == 200 for r in await asyncio.gather(*[c.get("/api/profile", headers=bearer(t)) for t in others]))
        assert (await c.delete(f"/api/devices/{lost_id}", headers=AUTH)).status_code == 404
        assert len((await c.get("/api/devices", headers=AUTH)).json()["devices"]) == 39


async def test_codes_expire_and_wrong_codes_are_rate_limited(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        code = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
        for i in range(10):
            assert (await c.post("/api/pair", json={"code": "x" * 30 + str(i), "name": "n"})).status_code == 400
        assert (await c.post("/api/pair", json={"code": code, "name": "n"})).status_code == 429       # guessing is cut off, even for a real code


def test_expired_codes_are_refused_and_the_store_never_keeps_secrets(tmp_path):
    from assistant.store import Store
    st = Store(tmp_path / "a.db")
    st.PAIRING_TTL = -1
    late = st.new_pairing()
    assert st.redeem_pairing(late, "late") is None and st.devices() == []
    st.PAIRING_TTL = 600
    code2 = st.new_pairing()
    dev, tok = st.redeem_pairing(code2, "ok")
    raw = (tmp_path / "a.db").read_bytes()
    assert code2.encode() not in raw and tok.encode() not in raw and st.device_for_token(tok)["id"] == dev
    assert st.device_for_token("nope") is None and st.device_for_token("") is None


async def test_two_phones_can_talk_at_the_same_time_to_one_container(tmp_path):
    started = asyncio.Event()
    gate = asyncio.Event()
    calls = []

    async def slow(provider, request, **k):
        calls.append(1)
        if len(calls) == 2:
            started.set()
        await gate.wait()                       # both answers are in progress together
        yield TextDelta(f"answer {len(calls)}")
        yield Done("stop")

    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")), tools=[], stream_fn=slow)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, timeout=20) as c:
        (_, a), (_, b) = await add_phone(c, "A"), await add_phone(c, "B")
        ta, tb = a.json()["token"], b.json()["token"]

        async def ask(tok, text):
            async with c.stream("POST", "/api/talk", json={"text": text}, headers=bearer(tok)) as r:
                return r.status_code, await read_events(r)

        t1 = asyncio.create_task(ask(ta, "from A"))
        t2 = asyncio.create_task(ask(tb, "from B"))
        await asyncio.wait_for(started.wait(), 10)
        gate.set()
        (s1, e1), (s2, e2) = await t1, await t2
        assert (s1, s2) == (200, 200) and any(e["type"] == "done" for e in e1) and any(e["type"] == "done" for e in e2)
        texts = [m["content"] for m in (await c.get("/api/messages", headers=AUTH)).json()]
        assert "from A" in texts and "from B" in texts and sum(t.startswith("answer") for t in texts) == 2     # one shared conversation


async def test_the_pairing_link_and_qr_use_the_address_the_owner_is_on_and_refuse_odd_origins(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        ok = (await c.post("/api/devices/pair", json={"origin": "https://me.tail1234.ts.net:8100/"}, headers=AUTH)).json()
        info = (await c.get("/api/e2e/info")).json()   # the server's KE2E key rides in the fragment, after the code
        assert ok["link"] == f"https://me.tail1234.ts.net:8100/#pair={ok['code']}&k={info['pk']}"
        assert ok["fingerprint"] == info["fingerprint"]
        assert ok["qr_svg"].startswith("<svg") and 'xmlns="http://www.w3.org/2000/svg"' in ok["qr_svg"]        # shows as an image, not as text
        assert "link" not in (await c.post("/api/devices/pair", headers=AUTH)).json()                        # no origin: just the code
        for bad in ("javascript:alert(1)", "https://x.example.com/some/path", "https://user:pw@x.example.com", "ftp://x.example.com", "https://x.example.com?a=1", "not a url"):
            assert (await c.post("/api/devices/pair", json={"origin": bad}, headers=AUTH)).status_code == 422, bad


async def test_a_fixed_public_address_is_put_in_the_pairing_link_instead_of_a_temporary_one(tmp_path):
    import httpx
    from assistant.server import Settings, create_app
    from test_server import AUTH, TOKEN, Running, scripted
    app = create_app(Settings(token=TOKEN, cdp_host=None, db_path=str(tmp_path / "s.db"), public_url="https://kelo.example.org"), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        out = (await c.post("/api/devices/pair", json={"origin": "https://random-words.trycloudflare.com"})).json()
        assert out["link"].startswith("https://kelo.example.org/#pair=")


async def test_an_old_app_that_sends_the_key_part_of_the_link_still_pairs(tmp_path):
    """KE2E links are https://host/#pair=CODE&k=<key>; an app that reads all of it as the code sends "CODE&k=..."."""
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        code = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
        r = await c.post("/api/pair", json={"code": f"{code}&k=OFJ52Ces7PFBXbc107iFdZtEkPYt3bDrO1FM3Msw01k", "name": "old app"})
        assert r.status_code == 200 and r.json()["token"]
        assert (await c.post("/api/pair", json={"code": f"{code}&k=x", "name": "again"})).status_code == 400   # still once only
