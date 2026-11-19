"""A refusal does not spend the line's standing (card p-browser-chromeos, part 2): after a site refuses the browser, the browser does
not load that site again for six hours (per registrable site, for the whole server), answers open, click and type to it at once with
the time it may try again, and never retries on its own. The owner can clear a pause (DELETE API)."""

from assistant.agent.fence import plain
from assistant.tools.browser import REFUSAL_PAUSE_SECONDS, BrowserSession, _site_of

from test_browser_blocked import ctx, session_on, DENIED   # noqa: F401 - the Playwright context fixture and the refusing page

SITE = "example.test"


def test_the_pause_is_six_hours():
    assert REFUSAL_PAUSE_SECONDS == 6 * 3600


async def test_a_refusal_records_a_pause_for_the_site(ctx):
    s, page = await session_on(ctx)
    await s.open("https://denied.example.test/a")
    assert s._refused_until.get(_site_of("https://denied.example.test/a")) is not None
    assert s.pause_note("https://denied.example.test/zzz") != ""
    assert s.pause_note("https://other-site.test/") == ""            # another site is not paused


async def test_a_second_open_in_the_pause_is_answered_without_a_request(ctx):
    s, page = await session_on(ctx)
    await s.open("https://denied.example.test/a")
    n = len(ctx.hits)
    out = plain(await s.open("https://denied.example.test/b"))
    assert "will not load" in out and "about" in out and "minutes" in out
    assert len(ctx.hits) == n                                          # nothing was sent to the site
    assert "Blocked again" not in out


async def test_a_click_on_the_site_in_the_pause_is_answered_without_clicking(ctx):
    s, page = await session_on(ctx)
    await s.open("https://denied.example.test/a")
    n = len(ctx.hits)
    out = plain(await s.click("#go"))
    assert "will not load" in out and len(ctx.hits) == n


async def test_a_type_on_the_site_in_the_pause_is_answered_without_typing(ctx):
    s, page = await session_on(ctx)
    await s.open("https://denied.example.test/a")
    out = plain(await s.type("#q", "hello"))
    assert "will not load" in out


async def test_after_the_pause_the_site_loads_again(ctx):
    s, page = await session_on(ctx)
    await s.open("https://denied.example.test/a")
    s._refused_until[SITE] = s.clock() - 1                                    # six hours later
    n = len(ctx.hits)
    await s.open("https://denied.example.test/d")
    assert len(ctx.hits) > n                                           # loaded again (and refused again: paused again)
    assert s.pause_note("https://denied.example.test/d") != ""


async def test_the_owner_can_clear_a_pause(ctx):
    s, page = await session_on(ctx)
    await s.open("https://denied.example.test/a")
    assert [p["site"] for p in s.refusal_pauses()] == [SITE]
    assert s.clear_pause(SITE) is True and s.clear_pause(SITE) is False
    n = len(ctx.hits)
    await s.open("https://denied.example.test/e")
    assert len(ctx.hits) > n


def test_the_page_script_is_never_blocked_to_get_past_a_refusal():
    # no code path aborts or strips a site's own scripts (a guard for the refusal pause to stay the only answer to a refusal)
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/assistant/tools/browser.py").read_text()
    assert "route.abort" not in src and "remove_script" not in src


def test_listing_refusal_pauses_leaves_an_open_hand_over_alone():
    s = BrowserSession(clock=lambda: 100.0)
    with s.pause_clock("owner"):                                       # a hand-over open on this tab: count 1
        assert s.refusal_pauses() == []
        assert s._paused.get("owner") == 1                             # the hand-over count is not pruned as an expired pause


# ---- B2: a refusal of a browser that is not signed in to the site opens one 15-minute sign-in pass (brief B2) ----------------------
from assistant.tools.challenge import signed_out_note
from assistant.tools.browser import SIGN_IN_PASS_SECONDS


def test_the_sign_in_pass_is_fifteen_minutes():
    assert SIGN_IN_PASS_SECONDS == 15 * 60


def test_the_signed_out_note_has_three_variants_in_plain_english():
    saved = signed_out_note("shop.test", "shop login", job=False, minutes=15)
    assert "not signed in" in saved and "browser_login(name='shop login')" in saved and "follow the site's own sign-in link" in saved
    job = signed_out_note("shop.test", "", job=True, minutes=15)
    assert "report that Kelo's browser needs to be signed in" in job and "do not use shop.test again in this job" in job
    chat = signed_out_note("shop.test", "", job=False, minutes=15)
    assert "request_login" in chat and "taking over" in chat and "For the next 15 minutes" in chat


async def test_a_signed_out_refusal_opens_a_sign_in_pass_and_says_to_sign_in(ctx):
    s, page = await session_on(ctx)
    s.signed_in_site = lambda site: False
    s.login_for = lambda site: "shop login"
    out = plain(await s.open("https://denied.example.test/a"))
    assert "is not signed in" in out and "browser_login(name='shop login')" in out and "is blocking Kelo" not in out
    assert "example.test" in s._sign_in_pass and s.pause_note("https://denied.example.test/zzz") == ""


async def test_during_the_pass_the_site_loads(ctx):
    s, page = await session_on(ctx)
    s.signed_in_site = lambda site: False
    await s.open("https://denied.example.test/a")
    n = len(ctx.hits)
    out = plain(await s.open("https://denied.example.test/b"))    # the pass lets the site load once: it is sent
    assert len(ctx.hits) == n + 1 and "will not load" not in out


async def test_a_successful_sign_in_ends_the_pause(ctx):
    s, page = await session_on(ctx)
    s.signed_in_site = lambda site: False
    await s.open("https://denied.example.test/a")
    assert s.clear_pause("example.test") is True
    assert s.pause_note("https://denied.example.test/zzz") == "" and "example.test" not in s._sign_in_pass


async def test_a_failed_sign_in_keeps_the_pause(ctx):
    s, page = await session_on(ctx)
    s.signed_in_site = lambda site: False
    await s.open("https://denied.example.test/a")
    s.end_sign_in_pass("example.test")
    assert "example.test" not in s._sign_in_pass and s.pause_note("https://denied.example.test/zzz") != ""


async def test_a_second_refusal_ends_the_pass(ctx):
    s, page = await session_on(ctx)
    s.signed_in_site = lambda site: False
    await s.open("https://denied.example.test/a")
    await s.open("https://denied.example.test/b")               # the site loaded in the pass and refused again
    assert "example.test" not in s._sign_in_pass and s.pause_note("https://denied.example.test/zzz") != ""


async def test_a_refusal_while_signed_in_keeps_todays_note(ctx):
    s, page = await session_on(ctx)
    s.signed_in_site = lambda site: True
    out = plain(await s.open("https://denied.example.test/a"))
    assert "is blocking Kelo's browser" in out and "is not signed in" not in out and "example.test" not in s._sign_in_pass


async def test_one_pass_per_pause(ctx):
    s, page = await session_on(ctx)
    s.signed_in_site = lambda site: False
    await s.open("https://denied.example.test/a")               # the pass opens with the pause
    s.end_sign_in_pass("example.test")                           # the sign-in failed: the pause stays
    n = len(ctx.hits)
    out = plain(await s.open("https://denied.example.test/b"))   # paused: nothing is sent, and no new pass
    assert "will not load" in out and len(ctx.hits) == n and "example.test" not in s._sign_in_pass
