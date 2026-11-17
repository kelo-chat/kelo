"""hc-challenge-probe (docs/no-word-lists.md): challenge.PROBE_JS counts one-time-code boxes and short inputs by structure, never by the
words of a name, id, placeholder or label. Real Chromium on local fixture pages (skipped without Playwright's browser); no real site."""

from __future__ import annotations

import pytest

from assistant.tools.challenge import PROBE_JS

pw = pytest.importorskip("playwright.async_api")


@pytest.fixture
async def page():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:   # no browser installed
            pytest.skip(f"no chromium: {e}")
        pg = await b.new_page()
        yield pg
        await b.close()


async def read(page, body: str) -> dict:
    await page.set_content(f"<!doctype html><body>{body}</body>")
    return await page.evaluate(PROBE_JS)


async def test_the_autocomplete_token_counts_whatever_the_box_is_called(page):
    assert (await read(page, "<input name=x autocomplete=one-time-code>"))["otp"] == 1
    assert (await read(page, "<input name=x autocomplete='one-time-code'><input autocomplete=username>"))["otp"] == 1


async def test_names_ids_placeholders_and_labels_never_count(page):
    body = "<input name=otp><input id=verification_code><input placeholder='2fa code'><input aria-label=totp><label>One-time code<input></label>"
    assert (await read(page, body))["otp"] == 0


async def test_a_numeric_box_with_a_short_maxlength_counts_once(page):
    assert (await read(page, "<input inputmode=numeric maxlength=6>"))["otp"] == 1
    assert (await read(page, "<input maxlength=6>"))["otp"] == 1
    assert (await read(page, "<input inputmode=numeric maxlength=6 autocomplete=postal-code>"))["otp"] == 0       # a token naming something else
    assert (await read(page, "<input inputmode=numeric maxlength=30>"))["otp"] == 0                                 # not short
    assert (await read(page, "<input inputmode=numeric maxlength=1>"))["otp"] == 0                                  # a lone one-character box is no row


async def test_a_row_of_one_character_boxes_counts(page):
    assert (await read(page, "<input maxlength=1>" * 6))["otp"] == 1
    assert (await read(page, "<input maxlength=1>" * 3))["otp"] == 0
    assert (await read(page, "<input maxlength=1>" * 9))["otp"] == 0


async def test_two_unmarked_code_shaped_boxes_are_not_guessed(page):
    assert (await read(page, "<input maxlength=6><input maxlength=6>"))["otp"] == 0


async def test_a_search_box_is_never_a_short_input_by_type_or_role_but_not_by_its_name(page):
    r = await read(page, "<input type=search maxlength=5><div role=search><input maxlength=5></div>")
    assert r["short_inputs"] == 0
    r = await read(page, "<input name=query maxlength=5><input name=keyword maxlength=5>")
    assert r["short_inputs"] == 2                                      # a name is no evidence: a short box is a short box (the safer side)


async def test_the_other_fields_of_the_reading_are_unchanged(page):
    r = await read(page, "<input type=password><input type=hidden><input maxlength=1>")
    assert r["secret_box"] is True and r["inputs"] == 2 and r["kelo_check"] == 1
