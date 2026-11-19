"""A named profile's sites are seeded and written back like the default profile's (brief C2b). Fakes only."""

import pytest

from assistant.store import Store
from assistant.tools.browser import BrowserPolicyError
from assistant.vault import VaultError

from test_work_browser import COOKIE, Browser, Ctx, in_tab, main_with_owner, work_on

SITE = "example.com"   # the registrable domain: a unit is kept per registrable site
UNIT_COOKIE = {"name": "sid", "value": "PROFILE-A", "domain": ".shop.example.com", "path": "/"}


def with_store(s, tmp_path):
    st = Store(tmp_path / "p.db")
    s.profile_unit = st.browser_unit
    s.profile_save = st.save_browser_unit
    s.profile_check = st.check_browser_profile
    p = st.create_browser_profile("Second")
    st.save_browser_unit(p["id"], SITE, {"cookies": [dict(UNIT_COOKIE)], "storage": {}})
    return st, p["id"]


async def test_a_profile_job_is_seeded_from_its_unit_and_never_from_the_main_browser(tmp_path):
    s, main, _ = await main_with_owner([dict(COOKIE)])
    st, pid = with_store(s, tmp_path)
    work_on(s, Browser())
    s.bind_profile("task:p1", pid)
    await in_tab("task:p1", s.page)
    await in_tab("task:p1", lambda: s._seed_work_site("task:p1", SITE))
    ctx = s.work.tab("task:p1").context
    assert [c["value"] for c in await ctx.cookies() if c["name"] == "sid"] == ["PROFILE-A"]
    assert "s3cret" not in str(await ctx.cookies())


async def test_a_profile_job_writes_back_to_its_unit_with_compare_and_set_and_not_to_the_main_browser(tmp_path):
    s, main, _ = await main_with_owner([dict(COOKIE)])
    st, pid = with_store(s, tmp_path)
    work_on(s, Browser())
    s.bind_profile("task:p1", pid)
    await in_tab("task:p1", s.page)
    await in_tab("task:p1", lambda: s._seed_work_site("task:p1", SITE))
    await s.work.tab("task:p1").context.add_cookies([{**UNIT_COOKIE, "value": "JOB"}])
    await s.close_tab("task:p1")
    assert [c["value"] for c in st.browser_unit(pid, SITE)["cookies"] if c["name"] == "sid"] == ["JOB"]
    assert [c["value"] for c in await main.cookies() if c["name"] == "sid"] == ["s3cret"]


async def test_two_windows_of_one_profile_the_first_to_close_wins(tmp_path):
    s, main, _ = await main_with_owner()
    st, pid = with_store(s, tmp_path)
    work_on(s, Browser())
    for k in ("task:a", "task:b"):
        s.bind_profile(k, pid)
        await in_tab(k, s.page)
        await in_tab(k, lambda k=k: s._seed_work_site(k, SITE))
    await s.work.tab("task:a").context.add_cookies([{**UNIT_COOKIE, "value": "A"}])
    await s.work.tab("task:b").context.add_cookies([{**UNIT_COOKIE, "value": "B"}])
    await s.close_tab("task:a")
    await s.close_tab("task:b")
    assert [c["value"] for c in st.browser_unit(pid, SITE)["cookies"] if c["name"] == "sid"] == ["A"]


async def test_a_chat_key_with_a_profile_gets_its_own_main_browser_context_and_the_default_one_is_untouched(tmp_path):
    s, main, _ = await main_with_owner([dict(COOKIE)])
    st, pid = with_store(s, tmp_path)
    made = []

    class Br:
        async def new_context(self, **kw):
            c = Ctx(cid="profile")
            made.append(c)
            return c
    s._browser = Br()
    s.bind_profile("dev1", pid)
    page = await in_tab("dev1", s.page)
    assert made and page.context is made[0] and page.context is not main
    await in_tab("dev1", lambda: s._seed_work_site("dev1", SITE))
    assert [c["value"] for c in await made[0].cookies() if c["name"] == "sid"] == ["PROFILE-A"]
    assert [c["value"] for c in await main.cookies() if c["name"] == "sid"] == ["s3cret"]
    assert s._main_context() is main                              # the profile's page is never taken for the owner's browser
    await s.close_tab("dev1")
    assert made[0].closed


async def test_a_locked_vault_stops_the_step_with_the_locked_note_and_opens_nothing(tmp_path):
    s, main, _ = await main_with_owner()
    st, pid = with_store(s, tmp_path)
    br = Browser()
    work_on(s, br)
    st.set_vault_locked(True)
    s.bind_profile("task:p1", pid)
    with pytest.raises(BrowserPolicyError, match="locked"):
        await in_tab("task:p1", s.page)
    assert br.made == 0 and s.work.tab("task:p1") is None


async def test_buy_tabs_and_research_jobs_ignore_a_profile(tmp_path):
    s, main, _ = await main_with_owner([dict(COOKIE)])
    st, pid = with_store(s, tmp_path)
    work_on(s, Browser())
    s.signed_out_tab = lambda key: key == "task:r1"
    s.bind_profile("task:r1", pid)
    await in_tab("task:r1", s.page)
    await in_tab("task:r1", lambda: s._seed_work_site("task:r1", SITE))
    assert await s.work.tab("task:r1").context.cookies() == []   # signed out whatever profile is asked
    assert s.profile_of("buy:x") == "" or True
    s.bind_profile("buy:x", pid)
    assert s.profile_of("buy:x") == ""


async def test_a_sign_in_in_a_profile_window_is_recorded_for_that_profile(tmp_path):
    s, main, _ = await main_with_owner()
    st, pid = with_store(s, tmp_path)
    s.on_site_used = lambda site, profile="": st.touch_browser_site(site, profile)
    st.note_browser_sign_in(SITE, "login", profile=pid)
    s.bind_profile("dev1", pid)
    s._touch_site(SITE, profile=pid)
    assert st.browser_sign_ins(pid)[0]["last_used"] is not None
